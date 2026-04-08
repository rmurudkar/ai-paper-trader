"""Polygon.io API client for full licensed article text."""

import os
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

POLYGON_BASE_URL = "https://api.polygon.io/v2/reference/news"
DEFAULT_WATCHLIST = ['AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'TSLA', 'JPM', 'SPY', 'QQQ']
WORD_LIMIT = 1200
HEADLINE_SIMILARITY_THRESHOLD = 0.6
# Polygon free tier: 5 requests/minute → enforce ≥13s between all requests
_MIN_REQUEST_INTERVAL = 13.0
_last_request_time: float = 0.0


# Module-level cache for Polygon articles within a single trading cycle
_polygon_cache: List[Dict] = []
_polygon_cache_timestamp: float = 0.0
_POLYGON_CACHE_TTL = 300.0  # 5 minutes — re-fetch if older


def fetch_full_text(url: str) -> Optional[Dict]:
    """Fetch full licensed article text from Polygon.io by URL lookup.

    Step 1 of waterfall enrichment for NewsAPI.ai articles.
    Uses Polygon API endpoint: GET https://api.polygon.io/v2/reference/news
    Truncates article body to 1200 words max.

    OPTIMIZATION: Caches Polygon results per trading cycle to avoid N API calls
    for N articles. Cache invalidates after 5 minutes.

    Args:
        url: Article URL to match against Polygon articles.

    Returns:
        Dict with keys: full_text, publisher, published_at, tickers, url, partial:false
        None if no match found on Polygon.
    """
    if not url:
        return None

    api_key = os.getenv('POLYGON_API_TOKEN')
    if not api_key:
        logger.debug("POLYGON_API_TOKEN not set — skipping Polygon enrichment")
        return None

    # Fetch recent articles across watchlist tickers and search by URL (with caching)
    candidates = _get_cached_polygon_articles()

    for article in candidates:
        if article.get('url') == url:
            logger.debug(f"[POLYGON] Cache hit for: {url}")
            return article

    logger.debug(f"[POLYGON] Cache miss for: {url}")
    return None


def _get_cached_polygon_articles(max_results: int = 50) -> List[Dict]:
    """Get cached Polygon articles, fetching only if cache is stale.

    Args:
        max_results: Max articles to fetch if cache is invalid.

    Returns:
        List of article dicts from Polygon cache.
    """
    global _polygon_cache, _polygon_cache_timestamp

    current_time = time.time()
    cache_age = current_time - _polygon_cache_timestamp

    # If cache is fresh, return it
    if _polygon_cache and cache_age < _POLYGON_CACHE_TTL:
        logger.debug(f"[POLYGON] Using cached articles ({len(_polygon_cache)} articles, age={cache_age:.1f}s)")
        return _polygon_cache

    # Cache is stale or empty — fetch fresh
    logger.info(f"[POLYGON] Cache miss or stale (age={cache_age:.1f}s) — fetching fresh articles")
    _polygon_cache = _fetch_general_news(DEFAULT_WATCHLIST, max_results=max_results)
    _polygon_cache_timestamp = time.time()

    logger.info(f"[POLYGON] Fetched {len(_polygon_cache)} articles, cache now valid for 5 minutes")
    return _polygon_cache


def _fetch_ticker_news(ticker: str, max_results: int = 10) -> List[Dict]:
    """Private: Fetch Polygon's own news feed filtered by watchlist ticker."""
    api_key = os.getenv('POLYGON_API_TOKEN')
    if not api_key:
        logger.error("POLYGON_API_TOKEN not found in environment variables")
        return []

    params = {
        'apiKey': api_key,
        'ticker': ticker,
        'order': 'desc',
        'limit': min(max_results, 50),
        'sort': 'published_utc',
        'published_utc.gte': _get_published_after(),
    }

    return _request_news(params)


def _fetch_general_news(watchlist: List[str], max_results: int = 20) -> List[Dict]:
    """Private: Fetch general financial news from Polygon filtered by watchlist."""
    api_key = os.getenv('POLYGON_API_TOKEN')
    if not api_key:
        logger.error("POLYGON_API_TOKEN not found in environment variables")
        return []

    # Polygon supports multiple tickers via comma-separated list in the ticker param
    # but only allows one ticker per request on the free plan — batch per ticker
    all_articles: List[Dict] = []
    seen_urls: set = set()
    per_ticker = max(5, max_results // max(len(watchlist), 1))

    for ticker in watchlist:
        articles = _fetch_ticker_news(ticker, max_results=per_ticker)
        for article in articles:
            url = article.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_articles.append(article)

        if len(all_articles) >= max_results:
            break

    all_articles.sort(key=lambda x: x.get('published_at', ''), reverse=True)
    return all_articles[:max_results]


def _truncate_article_text(text: str, max_words: int = WORD_LIMIT) -> str:
    """Private: Truncate article body to maximum word count."""
    if not text:
        return ''
    words = text.split()
    if len(words) <= max_words:
        return text
    return ' '.join(words[:max_words])


def _match_article_by_headline(headline: str, candidate_articles: List[Dict]) -> Optional[Dict]:
    """Private: Match NewsAPI headline against Polygon articles by similarity."""
    if not headline or not candidate_articles:
        return None

    headline_words = set(headline.lower().split())
    best_match = None
    best_score = 0.0

    for article in candidate_articles:
        candidate_title = article.get('title', '')
        if not candidate_title:
            continue

        candidate_words = set(candidate_title.lower().split())
        if not candidate_words:
            continue

        intersection = headline_words & candidate_words
        union = headline_words | candidate_words
        score = len(intersection) / len(union) if union else 0.0

        if score > best_score:
            best_score = score
            best_match = article

    if best_score >= HEADLINE_SIMILARITY_THRESHOLD:
        return best_match
    return None


def _enrich_newsapi_items(newsapi_items: List[Dict]) -> List[Dict]:
    """Private: Batch enrich NewsAPI.ai items with full text from Polygon."""
    enriched = []
    for item in newsapi_items:
        url = item.get('url', '')
        result = fetch_full_text(url)

        if result:
            enriched_item = item.copy()
            enriched_item['full_text'] = result['full_text']
            enriched_item['publisher'] = result.get('publisher', '')
            enriched_item['tickers'] = result.get('tickers', [])
            enriched_item['partial'] = False
            enriched_item.pop('needs_full_text', None)
            enriched.append(enriched_item)
        else:
            enriched.append(item)

    return enriched


def _request_news(params: Dict) -> List[Dict]:
    """Private: Execute GET request to Polygon news endpoint and parse results."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        time.sleep(_MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()

    try:
        response = requests.get(POLYGON_BASE_URL, params=params, timeout=30)
        response.raise_for_status()
        return _parse_response(response.json())
    except requests.exceptions.RequestException as e:
        logger.error(f"Polygon API request failed: {e}")
        return []
    except Exception as e:
        logger.error(f"Polygon API unexpected error: {e}")
        return []


def _parse_response(response_json: Dict) -> List[Dict]:
    """Private: Transform raw Polygon API response into normalized article dicts."""
    articles = []

    for item in response_json.get('results', []):
        article_url = item.get('article_url', '')
        if not article_url:
            continue

        raw_text = item.get('content', '') or item.get('description', '')
        full_text = _truncate_article_text(raw_text)

        publisher_info = item.get('publisher', {})
        publisher = publisher_info.get('name', '') if isinstance(publisher_info, dict) else ''

        articles.append({
            'title': item.get('title', ''),
            'full_text': full_text,
            'publisher': publisher,
            'published_at': item.get('published_utc', ''),
            'tickers': item.get('tickers', []),
            'url': article_url,
            'source': 'polygon',
            'partial': False,
        })

    return articles


def _get_published_after() -> str:
    """Private: Return ISO timestamp for articles published in the last 24 hours."""
    cutoff = datetime.utcnow() - timedelta(days=1)
    return cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')
