"""Alpaca News API client for Benzinga news feed.

Fetches ticker-tagged financial news with full article content via Alpaca's
free Benzinga news feed. Requires ALPACA_API_KEY and ALPACA_SECRET_KEY in .env.

Used both as an independent news source and as Step 2 of the waterfall
enrichment in aggregator.py (matching NewsAPI articles by URL/title).
"""

import os
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

from alpaca.data.historical import NewsClient
from alpaca.data.requests import NewsRequest
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

WORD_LIMIT = 1200
_HTML_TAG_RE = re.compile(r'<[^>]+>')


def fetch_news(watchlist: List[str] = None, max_results: int = 20) -> List[Dict]:
    """Fetch Benzinga news for watchlist tickers via Alpaca News API.

    Args:
        watchlist: List of ticker symbols to fetch news for.
                   If empty/None, fetches broad market news.
        max_results: Maximum total articles to return.

    Returns:
        List of dicts with keys: title, full_text, ticker, tickers, url,
        published_at, source, author, partial.
        Source field is always "alpaca".
    """
    client = _get_alpaca_news_client()
    if not client:
        return []

    try:
        # Build request — symbols param is comma-separated string
        symbols_str = ",".join(watchlist) if watchlist else None

        request = NewsRequest(
            symbols=symbols_str,
            start=datetime.now(timezone.utc) - timedelta(days=1),
            limit=min(max_results, 50),
            sort="desc",
            include_content=True,
            exclude_contentless=False,
        )

        response = client.get_news(request)

        # Response is a NewsSet with a .news list attribute
        raw_articles = response.news if hasattr(response, 'news') else []

        articles = []
        for raw in raw_articles:
            formatted = _format_alpaca_article(raw)
            if formatted:
                articles.append(formatted)

        # If watchlist provided, filter to relevant tickers
        if watchlist:
            articles = _filter_by_watchlist(articles, watchlist)

        logger.info(f"alpaca_news | Fetched {len(articles)} articles")
        return articles[:max_results]

    except Exception as e:
        logger.error(f"alpaca_news | Fetch failed: {e}")
        return []


def _get_alpaca_news_client() -> Optional[NewsClient]:
    """Initialize and return Alpaca News API client."""
    api_key = os.getenv('ALPACA_API_KEY')
    secret_key = os.getenv('ALPACA_SECRET_KEY')

    if not api_key or not secret_key:
        logger.error("alpaca_news | ALPACA_API_KEY or ALPACA_SECRET_KEY not found in .env")
        return None

    try:
        return NewsClient(api_key=api_key, secret_key=secret_key)
    except Exception as e:
        logger.error(f"alpaca_news | Failed to create NewsClient: {e}")
        return None


def _format_alpaca_article(raw_article) -> Optional[Dict]:
    """Convert raw Alpaca News API article to standard format.

    Args:
        raw_article: Alpaca News model object with headline, content, symbols, etc.

    Returns:
        Standardized article dict, or None if article is unusable.
    """
    try:
        headline = getattr(raw_article, 'headline', '') or ''
        if not headline:
            return None

        # Extract and clean content — may contain HTML
        raw_content = getattr(raw_article, 'content', '') or ''
        full_text = _strip_html(raw_content)
        full_text = _truncate_to_words(full_text)

        summary = getattr(raw_article, 'summary', '') or ''
        symbols = getattr(raw_article, 'symbols', []) or []
        url = getattr(raw_article, 'url', '') or ''
        author = getattr(raw_article, 'author', '') or ''

        # Parse created_at to ISO string
        created_at = getattr(raw_article, 'created_at', None)
        published_at = ''
        if created_at:
            if isinstance(created_at, datetime):
                published_at = created_at.strftime('%Y-%m-%dT%H:%M:%SZ')
            else:
                published_at = str(created_at)

        # Use first symbol as primary ticker
        primary_ticker = symbols[0] if symbols else ''

        return {
            'title': headline,
            'full_text': full_text if full_text else summary,
            'snippet': summary,
            'ticker': primary_ticker,
            'tickers': list(symbols),
            'url': url,
            'published_at': published_at,
            'source': 'alpaca',
            'author': author,
            'partial': not bool(full_text),
        }
    except Exception as e:
        logger.warning(f"alpaca_news | Failed to format article: {e}")
        return None


def _filter_by_watchlist(articles: List[Dict], watchlist: List[str]) -> List[Dict]:
    """Filter Alpaca news articles to those mentioning watchlist tickers."""
    watchlist_set = {t.upper() for t in watchlist}
    filtered = []
    for article in articles:
        article_tickers = {t.upper() for t in article.get('tickers', [])}
        if article_tickers & watchlist_set:
            filtered.append(article)
    return filtered


def _strip_html(text: str) -> str:
    """Remove HTML tags from article content."""
    if not text:
        return ''
    return _HTML_TAG_RE.sub('', text).strip()


def _truncate_to_words(text: str, max_words: int = WORD_LIMIT) -> str:
    """Truncate text to maximum word count."""
    if not text:
        return ''
    words = text.split()
    if len(words) <= max_words:
        return text
    return ' '.join(words[:max_words])
