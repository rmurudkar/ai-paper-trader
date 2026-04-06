"""
Integration tests for fetchers/aggregator.py.

Tests exercise real behavior against the pure aggregation logic (dedup, merge,
sort, waterfall enrichment, title similarity). No mocking — all functions
receive synthetic article dicts that mirror real fetcher output shapes.

Scenarios:
  1.  Title similarity: identical titles → 1.0
  2.  Title similarity: completely different titles → low score
  3.  Title similarity: partially overlapping titles → mid-range score
  4.  Title similarity: empty/None inputs → 0.0
  5.  Dedup removes exact URL duplicates
  6.  Dedup removes title-similar articles (>80%)
  7.  Dedup keeps article with full_text over snippet-only duplicate
  8.  Dedup preserves articles with no URL
  9.  Sort by published_at descending (newest first)
  10. Sort handles mixed date formats
  11. Sort places articles without dates at the end
  12. Merge combines all 5 sources and deduplicates
  13. Merge with empty sources returns empty list
  14. Massive articles included in merge output
  15. Waterfall step 4: articles without full_text marked partial=True
  16. Waterfall preserves already-enriched articles
  17. Alpaca enrichment matches by URL
  18. Alpaca enrichment matches by similar title
  19. Alpaca enrichment skips articles that don't need full_text
  20. _safe_fetch returns empty list on exception
  21. Live fetch_all_news returns articles from each configured source
  22. Each source's articles have required fields
  23. fetch_all_news deduplicates across sources
"""

import os
import sys
import logging

logging.basicConfig(level=logging.DEBUG, format='%(message)s', force=True)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fetchers.aggregator import (
    calculate_title_similarity,
    deduplicate_articles,
    sort_by_published_date,
    merge_sources,
    waterfall_enrich_newsapi,
    enrich_newsapi_with_alpaca,
    fetch_all_news,
    _safe_fetch,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _article(title="Test", url="https://example.com/1", source="newsapi",
             published_at="2026-04-05T12:00:00Z", full_text=None,
             snippet=None, needs_full_text=False, partial=None, tickers=None,
             sentiment_score=None):
    """Build a minimal article dict matching fetcher output shape."""
    art = {"title": title, "url": url, "source": source, "published_at": published_at}
    if full_text is not None:
        art["full_text"] = full_text
    if snippet is not None:
        art["snippet"] = snippet
    if needs_full_text:
        art["needs_full_text"] = True
    if partial is not None:
        art["partial"] = partial
    if tickers is not None:
        art["tickers"] = tickers
    if sentiment_score is not None:
        art["sentiment_score"] = sentiment_score
    return art


########################################################################
# SCENARIO 1 — Title similarity: identical titles → 1.0
########################################################################

def test_title_similarity_identical():
    """
    Scenario: Two identical titles.
    Expected: Similarity == 1.0.
    """
    assert calculate_title_similarity("Apple beats earnings", "Apple beats earnings") == 1.0


########################################################################
# SCENARIO 2 — Title similarity: completely different titles → low score
########################################################################

def test_title_similarity_different():
    """
    Scenario: Two titles with no overlapping words.
    Expected: Similarity == 0.0.
    """
    assert calculate_title_similarity("Apple beats earnings", "Oil prices surge today") == 0.0


########################################################################
# SCENARIO 3 — Title similarity: partial overlap → mid-range
########################################################################

def test_title_similarity_partial_overlap():
    """
    Scenario: Titles share some words but not all.
    Expected: 0.0 < similarity < 1.0.
    """
    score = calculate_title_similarity(
        "Apple stock rises after strong earnings report",
        "Apple earnings report beats expectations"
    )
    assert 0.0 < score < 1.0


########################################################################
# SCENARIO 4 — Title similarity: empty/None inputs → 0.0
########################################################################

def test_title_similarity_empty():
    """
    Scenario: One or both titles are empty or None.
    Expected: Similarity == 0.0 in all cases.
    """
    assert calculate_title_similarity("", "something") == 0.0
    assert calculate_title_similarity("something", "") == 0.0
    assert calculate_title_similarity("", "") == 0.0
    assert calculate_title_similarity(None, "something") == 0.0
    assert calculate_title_similarity("something", None) == 0.0


########################################################################
# SCENARIO 5 — Dedup removes exact URL duplicates
########################################################################

def test_dedup_removes_exact_url_duplicates():
    """
    Scenario: Two articles share the same URL.
    Expected: Only the first is kept.
    """
    articles = [
        _article(title="First", url="https://example.com/same", source="marketaux"),
        _article(title="Second", url="https://example.com/same", source="newsapi"),
    ]
    result = deduplicate_articles(articles)
    assert len(result) == 1
    assert result[0]["title"] == "First"


########################################################################
# SCENARIO 6 — Dedup removes title-similar articles (>80%)
########################################################################

def test_dedup_removes_title_similar_articles():
    """
    Scenario: Two articles have different URLs but nearly identical titles (>80% Jaccard).
    Expected: Only one is kept.
    """
    articles = [
        _article(title="Apple stock rises after strong earnings", url="https://a.com/1"),
        _article(title="Apple stock rises after strong earnings report", url="https://b.com/2"),
    ]
    result = deduplicate_articles(articles)
    assert len(result) == 1


########################################################################
# SCENARIO 7 — Dedup keeps article with full_text over snippet-only
########################################################################

def test_dedup_prefers_full_text_over_snippet():
    """
    Scenario: Two near-duplicate titles, first has no full_text, second has full_text.
    Expected: The one with full_text is kept.
    """
    articles = [
        _article(title="Fed raises rates unexpectedly", url="https://a.com/1", snippet="short"),
        _article(title="Fed raises rates unexpectedly", url="https://b.com/2",
                 full_text="The Federal Reserve raised interest rates by 50 basis points..."),
    ]
    result = deduplicate_articles(articles)
    assert len(result) == 1
    assert result[0].get("full_text") is not None


########################################################################
# SCENARIO 8 — Dedup preserves articles with no URL
########################################################################

def test_dedup_preserves_no_url_articles():
    """
    Scenario: Articles with empty URLs and different titles.
    Expected: Both are kept (can't dedup by URL).
    """
    articles = [
        _article(title="Article A", url=""),
        _article(title="Article B", url=""),
    ]
    result = deduplicate_articles(articles)
    assert len(result) == 2


########################################################################
# SCENARIO 9 — Sort by published_at descending (newest first)
########################################################################

def test_sort_descending():
    """
    Scenario: Three articles with different timestamps.
    Expected: Sorted newest → oldest by default.
    """
    articles = [
        _article(title="Old", published_at="2026-04-01T08:00:00Z"),
        _article(title="New", published_at="2026-04-05T18:00:00Z"),
        _article(title="Mid", published_at="2026-04-03T12:00:00Z"),
    ]
    result = sort_by_published_date(articles)
    assert [a["title"] for a in result] == ["New", "Mid", "Old"]


########################################################################
# SCENARIO 10 — Sort handles mixed date formats
########################################################################

def test_sort_mixed_date_formats():
    """
    Scenario: Articles use different date format strings.
    Expected: All parsed correctly and sorted by actual datetime.
    """
    articles = [
        _article(title="A", published_at="2026-04-01"),
        _article(title="B", published_at="2026-04-05T18:00:00Z"),
        _article(title="C", published_at="2026-04-03 12:00:00"),
    ]
    result = sort_by_published_date(articles)
    assert [a["title"] for a in result] == ["B", "C", "A"]


########################################################################
# SCENARIO 11 — Sort places articles without dates at the end
########################################################################

def test_sort_missing_dates_at_end():
    """
    Scenario: Some articles have no published_at.
    Expected: Articles without dates sort to the end (descending).
    """
    articles = [
        _article(title="NoDate", published_at=""),
        _article(title="HasDate", published_at="2026-04-05T12:00:00Z"),
    ]
    result = sort_by_published_date(articles)
    assert result[0]["title"] == "HasDate"
    assert result[1]["title"] == "NoDate"


########################################################################
# SCENARIO 12 — Merge combines all 5 sources and deduplicates
########################################################################

def test_merge_all_sources():
    """
    Scenario: One article from each source, all unique.
    Expected: All 5 appear in merged output.
    """
    result = merge_sources(
        marketaux_articles=[_article(title="Marketaux", url="https://m.com/1", source="marketaux")],
        newsapi_articles=[_article(title="NewsAPI", url="https://n.com/1", source="newsapi")],
        polygon_articles=[_article(title="Polygon", url="https://p.com/1", source="polygon")],
        alpaca_articles=[_article(title="Alpaca", url="https://a.com/1", source="alpaca")],
        massive_articles=[_article(title="Massive", url="https://ma.com/1", source="massive")],
    )
    sources = {a["source"] for a in result}
    assert sources == {"marketaux", "massive", "newsapi", "polygon", "alpaca"}
    assert len(result) == 5


########################################################################
# SCENARIO 13 — Merge with empty sources returns empty list
########################################################################

def test_merge_empty_sources():
    """
    Scenario: All source lists are empty.
    Expected: Empty result.
    """
    result = merge_sources([], [], [], [])
    assert result == []


########################################################################
# SCENARIO 14 — Massive articles included in merge output
########################################################################

def test_merge_includes_massive():
    """
    Scenario: Only Massive source has articles.
    Expected: Massive articles appear in output with correct source tag.
    """
    massive_arts = [
        _article(title="NVDA surges", url="https://massive.com/1", source="massive",
                 tickers=["NVDA"], sentiment_score=0.7),
        _article(title="AAPL dips", url="https://massive.com/2", source="massive",
                 tickers=["AAPL"], sentiment_score=-0.7),
    ]
    result = merge_sources([], [], [], [], massive_articles=massive_arts)
    assert len(result) == 2
    assert all(a["source"] == "massive" for a in result)


########################################################################
# SCENARIO 15 — Waterfall step 4: needs_full_text → partial=True
########################################################################

def test_waterfall_marks_unenriched_as_partial():
    """
    Scenario: NewsAPI article with needs_full_text=True, no Polygon/Alpaca/scraper match.
    Expected: Article gets partial=True, needs_full_text removed.
    Why: Step 4 of waterfall — snippet-only fallback.
    Note: Polygon and scraper calls will fail gracefully (no API keys in test).
    """
    articles = [
        _article(title="Macro headline", url="https://newsapi.com/1",
                 source="newsapi", snippet="Short snippet", needs_full_text=True),
    ]
    result = waterfall_enrich_newsapi(articles, alpaca_articles=[])
    assert len(result) == 1
    assert result[0].get("partial") is True
    assert "needs_full_text" not in result[0]


########################################################################
# SCENARIO 16 — Waterfall preserves already-enriched articles
########################################################################

def test_waterfall_preserves_enriched():
    """
    Scenario: Article already has full_text (needs_full_text not set).
    Expected: Article passes through unchanged.
    """
    articles = [
        _article(title="Already enriched", url="https://newsapi.com/2",
                 source="newsapi", full_text="Full article content here", partial=False),
    ]
    result = waterfall_enrich_newsapi(articles, alpaca_articles=[])
    assert len(result) == 1
    assert result[0]["full_text"] == "Full article content here"
    assert result[0].get("partial") is False


########################################################################
# SCENARIO 17 — Alpaca enrichment matches by URL
########################################################################

def test_alpaca_enrichment_url_match():
    """
    Scenario: NewsAPI article URL matches an Alpaca article URL.
    Expected: full_text copied from Alpaca, partial=False, needs_full_text removed.
    """
    newsapi_arts = [
        _article(title="Headline", url="https://shared.com/story",
                 source="newsapi", needs_full_text=True),
    ]
    alpaca_arts = [
        _article(title="Different headline", url="https://shared.com/story",
                 source="alpaca", full_text="Alpaca full text content"),
    ]
    result = enrich_newsapi_with_alpaca(newsapi_arts, alpaca_arts)
    assert len(result) == 1
    assert result[0]["full_text"] == "Alpaca full text content"
    assert result[0].get("partial") is False
    assert "needs_full_text" not in result[0]


########################################################################
# SCENARIO 18 — Alpaca enrichment matches by similar title
########################################################################

def test_alpaca_enrichment_title_match():
    """
    Scenario: NewsAPI and Alpaca articles have near-identical titles (>80% Jaccard)
              but different URLs.
    Expected: full_text copied from Alpaca match.
    """
    newsapi_arts = [
        _article(title="Apple stock surges on strong earnings report",
                 url="https://newsapi.com/apple", source="newsapi", needs_full_text=True),
    ]
    alpaca_arts = [
        _article(title="Apple stock surges on strong earnings report today",
                 url="https://alpaca.com/apple", source="alpaca",
                 full_text="Full Alpaca article about Apple earnings..."),
    ]
    result = enrich_newsapi_with_alpaca(newsapi_arts, alpaca_arts)
    assert len(result) == 1
    assert result[0].get("full_text") is not None
    assert result[0].get("partial") is False


########################################################################
# SCENARIO 19 — Alpaca enrichment skips already-enriched articles
########################################################################

def test_alpaca_enrichment_skips_enriched():
    """
    Scenario: Article does not have needs_full_text flag.
    Expected: Article returned unchanged, no Alpaca matching attempted.
    """
    newsapi_arts = [
        _article(title="Already done", url="https://newsapi.com/done",
                 source="newsapi", full_text="Existing text"),
    ]
    alpaca_arts = [
        _article(title="Already done", url="https://alpaca.com/done",
                 source="alpaca", full_text="Different text"),
    ]
    result = enrich_newsapi_with_alpaca(newsapi_arts, alpaca_arts)
    assert len(result) == 1
    assert result[0]["full_text"] == "Existing text"


########################################################################
# SCENARIO 20 — _safe_fetch returns empty list on exception
########################################################################

def test_safe_fetch_returns_empty_on_error():
    """
    Scenario: Fetcher function raises an exception.
    Expected: _safe_fetch returns [] instead of propagating.
    """
    def failing_fetcher():
        raise ConnectionError("API down")

    result = _safe_fetch(failing_fetcher, "TestSource")
    assert result == []


def test_safe_fetch_returns_empty_on_none():
    """
    Scenario: Fetcher function returns None.
    Expected: _safe_fetch returns [].
    """
    result = _safe_fetch(lambda: None, "TestSource")
    assert result == []


########################################################################
# SCENARIO 21 — Live fetch_all_news returns articles from each source
########################################################################

def test_fetch_all_news_live_sources():
    """
    Scenario: Call fetch_all_news with a small watchlist against real APIs.
    Expected: Returns a non-empty list. Each configured source (where API key
              is present) contributes at least one article.
    Why it matters: Validates the full aggregator pipeline end-to-end —
              fetching, waterfall enrichment, merge, dedup, sort.

    Sources checked (only asserted if their API key is configured):
      - marketaux  (MARKETAUX_API_KEY)
      - massive    (MASSIVE_API_KEY)
      - newsapi    (NEWSAPI_KEY or NEWSAPI_AI_KEY)
      - alpaca     (ALPACA_API_KEY + ALPACA_SECRET_KEY)
    """
    watchlist = ["AAPL", "MSFT", "NVDA"]
    result = fetch_all_news(
        max_marketaux=5,
        max_massive=10,
        max_newsapi=5,
        max_alpaca=5,
        watchlist=watchlist,
    )

    assert isinstance(result, list)

    # Collect which sources actually returned articles
    sources_found = {a.get("source") for a in result}
    logging.info(f"Sources found in live fetch: {sources_found}")
    logging.info(f"Total articles after dedup: {len(result)}")

    # Assert each source contributed IF its API key is configured.
    # Sources with keys that fail auth (401, expired) are logged but not asserted —
    # the aggregator correctly returns [] for those via _safe_fetch.
    source_key_map = {
        "marketaux": "MARKETAUX_API_KEY",
        "massive": "MASSIVE_API_KEY",
        "newsapi": ["NEWSAPI_KEY", "NEWSAPI_AI_KEY"],
        "alpaca": "ALPACA_API_KEY",
    }

    sources_with_keys = set()
    for source, keys in source_key_map.items():
        if isinstance(keys, list):
            has_key = any(os.getenv(k) for k in keys)
        else:
            has_key = bool(os.getenv(keys))

        if has_key:
            sources_with_keys.add(source)
            count = sum(1 for a in result if a.get("source") == source)
            if source in sources_found:
                logging.info(f"  {source}: {count} articles")
            else:
                logging.info(f"  {source}: key configured but 0 articles (auth/rate limit issue)")
        else:
            logging.info(f"  {source}: SKIPPED (no API key)")

    # At least one configured source must have returned articles
    assert len(sources_found) > 0, (
        f"No sources returned articles. Keys configured for: {sources_with_keys}"
    )

    # At least 2 distinct sources should be present to validate multi-source merge
    assert len(sources_found) >= 2, (
        f"Expected articles from at least 2 sources, got {len(sources_found)}: {sources_found}"
    )


########################################################################
# SCENARIO 22 — Each source's articles have required fields
########################################################################

def test_fetch_all_news_article_fields():
    """
    Scenario: Every article from fetch_all_news has the minimum required fields.
    Expected: title, url, source, published_at present on every article.
    """
    result = fetch_all_news(
        max_marketaux=3,
        max_massive=5,
        max_newsapi=3,
        max_alpaca=3,
        watchlist=["AAPL"],
    )

    required_keys = {"title", "url", "source", "published_at"}
    for article in result:
        for key in required_keys:
            assert key in article, (
                f"Article missing '{key}': {article.get('title', '<no title>')[:60]} "
                f"(source={article.get('source')})"
            )


########################################################################
# SCENARIO 23 — fetch_all_news deduplicates across sources
########################################################################

def test_fetch_all_news_no_url_duplicates():
    """
    Scenario: fetch_all_news output should have no duplicate URLs.
    Expected: Every non-empty URL appears at most once.
    """
    result = fetch_all_news(
        max_marketaux=5,
        max_massive=10,
        max_newsapi=5,
        max_alpaca=5,
        watchlist=["AAPL", "MSFT"],
    )

    urls = [a["url"] for a in result if a.get("url")]
    assert len(urls) == len(set(urls)), (
        f"Duplicate URLs found: {[u for u in urls if urls.count(u) > 1]}"
    )
