"""
Integration tests for fetchers/alpaca_news.py.

Pure helper functions (_strip_html, _truncate_to_words, _filter_by_watchlist,
_format_alpaca_article) are tested with synthetic data. fetch_news() is tested
against the real Alpaca API when credentials are available.

Scenarios:
  1.  _strip_html removes tags and preserves text
  2.  _strip_html handles empty/None input
  3.  _truncate_to_words respects word limit
  4.  _truncate_to_words returns full text when under limit
  5.  _filter_by_watchlist keeps only matching tickers
  6.  _filter_by_watchlist is case-insensitive
  7.  _filter_by_watchlist returns empty for no matches
  8.  _format_alpaca_article produces correct output shape
  9.  _format_alpaca_article sets partial=True when no content
  10. _format_alpaca_article returns None for empty headline
  11. fetch_news returns list (may be empty without API keys)
  12. fetch_news articles have required fields
  13. fetch_news respects max_results cap
"""

import os
import sys
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

logging.basicConfig(level=logging.DEBUG, format='%(message)s', force=True)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fetchers.alpaca_news import (
    fetch_news,
    _strip_html,
    _truncate_to_words,
    _filter_by_watchlist,
    _format_alpaca_article,
)


# ── Helpers ──────────────────────────────────────────────────────────

def _mock_alpaca_article(headline="Test headline", content="Article body text",
                         summary="Short summary", symbols=None, url="https://example.com/1",
                         author="John Doe", created_at=None):
    """Build a SimpleNamespace that mimics an Alpaca News model object."""
    return SimpleNamespace(
        headline=headline,
        content=content,
        summary=summary,
        symbols=symbols or ["AAPL"],
        url=url,
        author=author,
        created_at=created_at or datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc),
    )


########################################################################
# SCENARIO 1 — _strip_html removes tags and preserves text
########################################################################

def test_strip_html_removes_tags():
    """
    Scenario: HTML content with paragraph and link tags.
    Expected: Tags removed, text preserved.
    """
    html = "<p>Apple <b>beat</b> earnings. <a href='#'>Read more</a></p>"
    result = _strip_html(html)
    assert "<" not in result
    assert ">" not in result
    assert "Apple" in result
    assert "beat" in result
    assert "earnings" in result


########################################################################
# SCENARIO 2 — _strip_html handles empty/None input
########################################################################

def test_strip_html_empty():
    """
    Scenario: Empty string and None input.
    Expected: Returns empty string.
    """
    assert _strip_html("") == ""
    assert _strip_html(None) == ""


########################################################################
# SCENARIO 3 — _truncate_to_words respects word limit
########################################################################

def test_truncate_respects_limit():
    """
    Scenario: Text with 20 words, limit set to 5.
    Expected: Result has exactly 5 words.
    """
    text = " ".join(f"word{i}" for i in range(20))
    result = _truncate_to_words(text, max_words=5)
    assert len(result.split()) == 5


########################################################################
# SCENARIO 4 — _truncate_to_words returns full text when under limit
########################################################################

def test_truncate_under_limit():
    """
    Scenario: Text with 3 words, limit is 1200.
    Expected: Full text returned unchanged.
    """
    text = "just three words"
    result = _truncate_to_words(text)
    assert result == text


########################################################################
# SCENARIO 5 — _filter_by_watchlist keeps only matching tickers
########################################################################

def test_filter_by_watchlist_keeps_matches():
    """
    Scenario: Articles for AAPL, MSFT, NVDA; watchlist is [AAPL, NVDA].
    Expected: Only AAPL and NVDA articles kept.
    """
    articles = [
        {"title": "A", "tickers": ["AAPL"]},
        {"title": "B", "tickers": ["MSFT"]},
        {"title": "C", "tickers": ["NVDA", "AAPL"]},
    ]
    result = _filter_by_watchlist(articles, ["AAPL", "NVDA"])
    assert len(result) == 2
    titles = {a["title"] for a in result}
    assert titles == {"A", "C"}


########################################################################
# SCENARIO 6 — _filter_by_watchlist is case-insensitive
########################################################################

def test_filter_by_watchlist_case_insensitive():
    """
    Scenario: Article has ticker "aapl" (lowercase), watchlist has "AAPL".
    Expected: Article is kept.
    """
    articles = [{"title": "A", "tickers": ["aapl"]}]
    result = _filter_by_watchlist(articles, ["AAPL"])
    assert len(result) == 1


########################################################################
# SCENARIO 7 — _filter_by_watchlist returns empty for no matches
########################################################################

def test_filter_by_watchlist_no_matches():
    """
    Scenario: No article tickers overlap with watchlist.
    Expected: Empty list.
    """
    articles = [{"title": "A", "tickers": ["TSLA"]}]
    result = _filter_by_watchlist(articles, ["AAPL", "MSFT"])
    assert result == []


########################################################################
# SCENARIO 8 — _format_alpaca_article produces correct output shape
########################################################################

def test_format_article_output_shape():
    """
    Scenario: Well-formed Alpaca News article object.
    Expected: Dict with all required keys and correct source tag.
    """
    raw = _mock_alpaca_article(
        headline="Apple beats Q2 earnings",
        content="<p>Apple reported strong Q2 results...</p>",
        symbols=["AAPL"],
    )
    result = _format_alpaca_article(raw)

    assert result is not None
    assert result["title"] == "Apple beats Q2 earnings"
    assert result["source"] == "alpaca"
    assert result["ticker"] == "AAPL"
    assert result["tickers"] == ["AAPL"]
    assert result["partial"] is False
    assert "<p>" not in result["full_text"]  # HTML stripped
    assert "published_at" in result
    assert "url" in result


########################################################################
# SCENARIO 9 — _format_alpaca_article sets partial=True when no content
########################################################################

def test_format_article_partial_when_no_content():
    """
    Scenario: Article has no content, only summary.
    Expected: partial=True, full_text falls back to summary.
    """
    raw = _mock_alpaca_article(
        headline="Breaking news",
        content="",
        summary="Short summary here",
    )
    result = _format_alpaca_article(raw)

    assert result is not None
    assert result["partial"] is True
    assert result["full_text"] == "Short summary here"


########################################################################
# SCENARIO 10 — _format_alpaca_article returns None for empty headline
########################################################################

def test_format_article_none_for_empty_headline():
    """
    Scenario: Article has no headline.
    Expected: Returns None (unusable article).
    """
    raw = _mock_alpaca_article(headline="")
    assert _format_alpaca_article(raw) is None

    raw2 = _mock_alpaca_article(headline=None)
    assert _format_alpaca_article(raw2) is None


########################################################################
# SCENARIO 11 — fetch_news returns list (graceful without API keys)
########################################################################

def test_fetch_news_returns_list():
    """
    Scenario: Call fetch_news (may or may not have API keys).
    Expected: Always returns a list (empty if no credentials).
    """
    result = fetch_news(watchlist=["AAPL"], max_results=5)
    assert isinstance(result, list)


########################################################################
# SCENARIO 12 — fetch_news articles have required fields
########################################################################

def test_fetch_news_article_fields():
    """
    Scenario: fetch_news returns articles (requires API keys).
    Expected: Each article has title, source, url, published_at, tickers.
    Skips assertion if no articles returned (missing credentials).
    """
    result = fetch_news(watchlist=["AAPL", "MSFT"], max_results=5)
    if not result:
        return  # No API keys — can't validate

    required_keys = {"title", "source", "url", "published_at", "tickers"}
    for article in result:
        for key in required_keys:
            assert key in article, f"Missing key: {key}"
        assert article["source"] == "alpaca"


########################################################################
# SCENARIO 13 — fetch_news respects max_results cap
########################################################################

def test_fetch_news_max_results():
    """
    Scenario: Request max_results=3.
    Expected: At most 3 articles returned.
    """
    result = fetch_news(watchlist=["AAPL"], max_results=3)
    assert len(result) <= 3
