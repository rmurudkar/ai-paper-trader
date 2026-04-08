"""News aggregator with 4-step waterfall enrichment for NewsAPI.ai articles.

WATERFALL ENRICHMENT for each NewsAPI.ai article:
Step 1: look up full text in Polygon.io  → if found, enrich + mark partial:false
Step 2: look up full text in Alpaca News → if found, enrich + mark partial:false
Step 3: call scraper.py                  → if found, enrich + mark partial:false
Step 4: use snippet only                 → mark partial:true, flag for limited analysis

Then merge all sources: Marketaux + NewsAPI (enriched) + Polygon feed + Alpaca News
Deduplicate: exact URL match first, then title similarity > 80%
Sort by published_at descending


After fetch_all_news() returns, you get a single list with ~70-80 articles, structured like this:
 [
      {
          "title": "Semiconductor Tariff Could Impact Tech Giants",
          "full_text": "Full article text (1200 words max)...",  # if available
          "snippet": "Short excerpt...",  # if no full_text
          "url": "https://example.com/article",
          "published_at": "2026-04-07T13:00:00Z",
          "source": "newsapi",  # or: marketaux, alpaca, massive, polygon
          "tickers": ["AAPL", "NVDA"],
          "sentiment_score": 0.65,  # marketaux/massive only
          "topics": ["geopolitical"],  # newsapi only
          "extraction_confidence": 0.85,  # newsapi only
          "partial": False,  # True if snippet-only
          "author": "John Doe",  # alpaca/massive only
      },
      # ... 70+ more articles, sorted by published_at DESC
  ]
"""

import logging
from datetime import datetime
from typing import List, Dict

from . import marketaux, massive, newsapi, polygon, alpaca_news, scraper

logger = logging.getLogger(__name__)

TITLE_SIMILARITY_THRESHOLD = 0.80


def fetch_all_news(
    max_marketaux: int = 20,
    max_massive: int = 50,
    max_newsapi: int = 15,
    max_polygon: int = 20,
    max_alpaca: int = 15,
    watchlist: List[str] = None,
    discovery_context: Dict = None
) -> List[Dict]:
    """Fetch and merge all news sources with waterfall enrichment.

    Flow:
    1. Call marketaux.fetch_news(watchlist) for ticker-tagged news with sentiment scores
    2. Call massive.fetch_news(tickers) for ticker-tagged news with sentiment
    3. Call newsapi.fetch_headlines() for macro/geopolitical headlines
    4. Call alpaca_news.fetch_news(watchlist) for Benzinga feed
    5. Apply 4-step waterfall enrichment to NewsAPI articles
    6. Merge all sources and deduplicate

    Args:
        max_marketaux: Maximum articles from marketaux.fetch_news().
        max_massive: Maximum articles from massive.fetch_news().
        max_newsapi: Maximum articles from newsapi.fetch_headlines().
        max_polygon: Not used directly (called via fetch_full_text).
        max_alpaca: Maximum articles from alpaca_news.fetch_news().
        watchlist: List of ticker symbols for filtering (legacy parameter).
        discovery_context: Discovery context from discovery.py containing mode and tickers.

    Returns:
        Unified list sorted by published_at desc with partial flag.
    """
    # Determine effective tickers from discovery context or watchlist
    effective_tickers = watchlist or []
    if discovery_context:
        effective_tickers = discovery_context.get('tickers', effective_tickers)

    is_discovery = bool(discovery_context and discovery_context.get('mode') == 'discovery')

    # 1. Fetch from all sources
    logger.info("Fetching Marketaux news...")
    marketaux_articles = _safe_fetch(
        lambda: marketaux.fetch_news(
            tickers=effective_tickers,
            max_results=max_marketaux,
            broad=is_discovery
        ),
        "Marketaux"
    )

    logger.info("Fetching Massive news...")
    massive_articles = _safe_fetch(
        lambda: massive.fetch_news(
            tickers=effective_tickers or None,
            max_results=max_massive
        ),
        "Massive"
    )

    logger.info("Fetching NewsAPI headlines...")
    newsapi_articles = _safe_fetch(
        lambda: newsapi.fetch_headlines(
            max_results=max_newsapi,
            discovery_context=discovery_context,
            watchlist=effective_tickers,
            broad=is_discovery
        ),
        "NewsAPI"
    )

    logger.info("Fetching Alpaca news...")
    alpaca_articles = _safe_fetch(
        lambda: alpaca_news.fetch_news(
            watchlist=effective_tickers or ['SPY'],
            max_results=max_alpaca
        ),
        "Alpaca News"
    )

    # 2. Waterfall enrichment for NewsAPI articles
    logger.info(f"Enriching {len(newsapi_articles)} NewsAPI articles via waterfall...")
    enriched_newsapi = waterfall_enrich_newsapi(newsapi_articles, alpaca_articles)

    # 3. Merge and deduplicate (Massive included as a ticker-tagged source alongside Marketaux)
    merged = merge_sources(
        marketaux_articles, enriched_newsapi, [], alpaca_articles,
        massive_articles=massive_articles
    )

    logger.info(
        f"Aggregator complete: {len(marketaux_articles)} marketaux, "
        f"{len(massive_articles)} massive, {len(newsapi_articles)} newsapi, "
        f"{len(alpaca_articles)} alpaca → {len(merged)} after dedup"
    )

    return merged


def deduplicate_articles(articles: List[Dict]) -> List[Dict]:
    """Remove duplicate articles by URL exact match, then by title similarity.

    Deduplication rules:
    1. URL exact match = duplicate (remove)
    2. Title similarity >80% = duplicate (remove)

    When duplicates are found, keep the article with more content (full_text over snippet).

    Args:
        articles: List of article dicts from various sources.

    Returns:
        Deduplicated list of article dicts.
    """
    if not articles:
        return []

    unique = []
    seen_urls = set()

    for article in articles:
        url = article.get('url', '')

        # Step 1: exact URL match
        if url and url in seen_urls:
            continue

        # Step 2: title similarity check against already-kept articles
        title = article.get('title', '')
        is_dup = False
        if title:
            for kept in unique:
                kept_title = kept.get('title', '')
                if kept_title and calculate_title_similarity(title, kept_title) > TITLE_SIMILARITY_THRESHOLD:
                    # Keep whichever has more content
                    if article.get('full_text') and not kept.get('full_text'):
                        unique.remove(kept)
                        if kept.get('url'):
                            seen_urls.discard(kept['url'])
                        break  # will add current article below
                    else:
                        is_dup = True
                        break

        if is_dup:
            continue

        if url:
            seen_urls.add(url)
        unique.append(article)

    return unique


def calculate_title_similarity(title1: str, title2: str) -> float:
    """Calculate Jaccard similarity between two article titles.

    Uses word-level Jaccard index: |intersection| / |union|.

    Args:
        title1: First article title.
        title2: Second article title.

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    if not title1 or not title2:
        return 0.0

    words1 = set(title1.lower().split())
    words2 = set(title2.lower().split())

    if not words1 or not words2:
        return 0.0

    intersection = words1 & words2
    union = words1 | words2

    return len(intersection) / len(union)


def merge_sources(
    marketaux_articles: List[Dict],
    newsapi_articles: List[Dict],
    polygon_articles: List[Dict],
    alpaca_articles: List[Dict],
    massive_articles: List[Dict] = None
) -> List[Dict]:
    """Merge articles from all sources and apply deduplication.

    Args:
        marketaux_articles: Articles from Marketaux API.
        newsapi_articles: Articles from NewsAPI.ai (already enriched).
        polygon_articles: Articles from Polygon.io feed.
        alpaca_articles: Articles from Alpaca News.
        massive_articles: Articles from Massive API.

    Returns:
        Merged and deduplicated list sorted by published_at desc.
    """
    all_articles = []
    all_articles.extend(marketaux_articles)
    if massive_articles:
        all_articles.extend(massive_articles)
    all_articles.extend(newsapi_articles)
    all_articles.extend(polygon_articles)
    all_articles.extend(alpaca_articles)

    deduped = deduplicate_articles(all_articles)
    return sort_by_published_date(deduped)


def waterfall_enrich_newsapi(
    newsapi_articles: List[Dict],
    alpaca_articles: List[Dict] = None
) -> List[Dict]:
    """4-step waterfall enrichment for NewsAPI.ai articles.

    For each NewsAPI.ai article with needs_full_text=True:
    Step 1: Call polygon.fetch_full_text(url) → if found, enrich + partial:false
    Step 2: Look up full text in alpaca_articles by title match → if found, enrich + partial:false
    Step 3: Call scraper.scrape(url) fallback → if found, enrich + partial:false
    Step 4: Use snippet only → partial:true (flag for limited analysis)

    Args:
        newsapi_articles: Articles from NewsAPI.ai with needs_full_text=True.
        alpaca_articles: Pre-fetched Alpaca articles for title matching.

    Returns:
        Enhanced NewsAPI articles with full_text or snippet + partial flag.
    """
    if not newsapi_articles:
        return []

    enriched = list(newsapi_articles)

    # Step 1: Polygon full text lookup
    logger.info(f"[WATERFALL] Step 1 START: Enriching with Polygon ({len(enriched)} articles need full_text)")
    enriched = enrich_newsapi_with_polygon(enriched)
    remaining = sum(1 for a in enriched if a.get('needs_full_text'))
    logger.info(f"[WATERFALL] Step 1 DONE: Polygon enriched {len(enriched) - remaining} articles, {remaining} still need full_text")

    # Step 2: Alpaca News title matching
    logger.info(f"[WATERFALL] Step 2 START: Enriching with Alpaca ({remaining} articles need full_text)")
    enriched = enrich_newsapi_with_alpaca(enriched, alpaca_articles or [])
    remaining = sum(1 for a in enriched if a.get('needs_full_text'))
    logger.info(f"[WATERFALL] Step 2 DONE: Alpaca enriched articles, {remaining} still need full_text")

    # Step 3: Scraper fallback
    logger.info(f"[WATERFALL] Step 3 START: Scraping URLs ({remaining} articles need full_text)")
    enriched = enrich_newsapi_with_scraper(enriched)
    remaining = sum(1 for a in enriched if a.get('needs_full_text'))
    logger.info(f"[WATERFALL] Step 3 DONE: Scraper enriched articles, {remaining} remain snippet-only")

    # Step 4: Mark any remaining as partial
    logger.info(f"[WATERFALL] Step 4 START: Marking {remaining} articles as partial")
    for article in enriched:
        if article.get('needs_full_text'):
            article['partial'] = True
            article.pop('needs_full_text', None)
    logger.info(f"[WATERFALL] Step 4 DONE: All articles processed")

    return enriched


def enrich_newsapi_with_polygon(newsapi_articles: List[Dict]) -> List[Dict]:
    """Step 1: Enrich NewsAPI items via polygon.fetch_full_text(url).

    Args:
        newsapi_articles: Articles from NewsAPI.ai.

    Returns:
        Articles with full_text added where Polygon lookup succeeded.
    """
    enriched = []
    total = len(newsapi_articles)

    for idx, article in enumerate(newsapi_articles):
        if not article.get('needs_full_text'):
            enriched.append(article)
            continue

        url = article.get('url', '')
        title = article.get('title', '')[:60]

        if not url:
            enriched.append(article)
            continue

        logger.info(f"[POLYGON] Processing article {idx+1}/{total}: {title}... (url={url})")

        try:
            logger.debug(f"[POLYGON] Calling fetch_full_text() for: {url}")
            result = polygon.fetch_full_text(url)

            if result and result.get('full_text'):
                updated = article.copy()
                updated['full_text'] = result['full_text']
                updated['partial'] = False
                updated.pop('needs_full_text', None)
                # Merge any extra tickers from Polygon
                existing_tickers = set(updated.get('tickers', []))
                existing_tickers.update(result.get('tickers', []))
                updated['tickers'] = list(existing_tickers)
                enriched.append(updated)
                logger.info(f"[POLYGON] ✓ Enriched article {idx+1}/{total}: {title}")
                continue
            else:
                logger.debug(f"[POLYGON] No result from fetch_full_text for: {url}")
        except Exception as e:
            logger.error(f"[POLYGON] Exception for article {idx+1}/{total} ({url}): {e}")

        enriched.append(article)

    return enriched


def enrich_newsapi_with_alpaca(
    newsapi_articles: List[Dict],
    alpaca_articles: List[Dict] = None
) -> List[Dict]:
    """Step 2: Enrich NewsAPI items with Alpaca News full_text via title matching.

    Args:
        newsapi_articles: Articles from NewsAPI.ai still needing full_text.
        alpaca_articles: Pre-fetched Alpaca articles to match against.

    Returns:
        Articles with full_text added where title matches found.
    """
    if not alpaca_articles:
        return newsapi_articles

    enriched = []
    for article in newsapi_articles:
        if not article.get('needs_full_text'):
            enriched.append(article)
            continue

        title = article.get('title', '')
        url = article.get('url', '')
        matched = False

        for alpaca_art in alpaca_articles:
            # Match by URL first
            if url and alpaca_art.get('url') == url and alpaca_art.get('full_text'):
                updated = article.copy()
                updated['full_text'] = alpaca_art['full_text']
                updated['partial'] = False
                updated.pop('needs_full_text', None)
                enriched.append(updated)
                matched = True
                logger.debug(f"Alpaca URL match: {title[:60]}")
                break

            # Match by title similarity
            alpaca_title = alpaca_art.get('title', '')
            if (title and alpaca_title
                    and calculate_title_similarity(title, alpaca_title) > TITLE_SIMILARITY_THRESHOLD
                    and alpaca_art.get('full_text')):
                updated = article.copy()
                updated['full_text'] = alpaca_art['full_text']
                updated['partial'] = False
                updated.pop('needs_full_text', None)
                enriched.append(updated)
                matched = True
                logger.debug(f"Alpaca title match: {title[:60]}")
                break

        if not matched:
            enriched.append(article)

    return enriched


def enrich_newsapi_with_scraper(newsapi_articles: List[Dict]) -> List[Dict]:
    """Step 3: Fallback scraper enrichment via scraper.scrape(url).

    Args:
        newsapi_articles: Articles from NewsAPI.ai still needing full_text.

    Returns:
        Articles with scraped full_text or unchanged if scraping fails.
    """
    enriched = []
    total = len([a for a in newsapi_articles if a.get('needs_full_text')])

    if total == 0:
        return newsapi_articles

    idx_count = 0
    for article in newsapi_articles:
        if not article.get('needs_full_text'):
            enriched.append(article)
            continue

        idx_count += 1
        url = article.get('url', '')
        title = article.get('title', '')[:60]
        snippet = article.get('snippet', '')

        if not url:
            logger.debug(f"[SCRAPER] Skipping article {idx_count}/{total} — no URL")
            enriched.append(article)
            continue

        logger.info(f"[SCRAPER] Processing article {idx_count}/{total}: {title}... (url={url})")

        try:
            logger.debug(f"[SCRAPER] Calling scrape() for: {url}")
            result = scraper.scrape(url, snippet=snippet)

            if result and result.get('full_text') and not result.get('partial', True):
                updated = article.copy()
                updated['full_text'] = result['full_text']
                updated['partial'] = False
                updated.pop('needs_full_text', None)
                enriched.append(updated)
                logger.info(f"[SCRAPER] ✓ Enriched article {idx_count}/{total}: {title}")
                continue
            else:
                logger.debug(f"[SCRAPER] Scrape returned partial or empty for {url}")
        except Exception as e:
            logger.error(f"[SCRAPER] Exception for article {idx_count}/{total} ({url}): {e}")

        enriched.append(article)

    return enriched


def sort_by_published_date(articles: List[Dict], descending: bool = True) -> List[Dict]:
    """Sort articles by published_at timestamp.

    Handles multiple date formats gracefully. Articles without a parseable
    date are placed at the end.

    Args:
        articles: List of article dicts with published_at field.
        descending: Sort newest first if True, oldest first if False.

    Returns:
        Sorted list of articles.
    """
    if not articles:
        return []

    def parse_date(article):
        date_str = article.get('published_at', '')
        if not date_str:
            return datetime.min

        for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ',
                    '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str, fmt)
            except (ValueError, TypeError):
                continue

        # If it's already a datetime object
        if isinstance(date_str, datetime):
            return date_str

        return datetime.min

    return sorted(articles, key=parse_date, reverse=descending)


def _safe_fetch(fetch_fn, source_name: str) -> List[Dict]:
    """Safely call a fetcher, returning empty list on failure."""
    try:
        result = fetch_fn()
        return result if result else []
    except Exception as e:
        logger.error(f"{source_name} fetch failed: {e}")
        return []
