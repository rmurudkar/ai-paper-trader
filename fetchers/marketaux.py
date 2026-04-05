"""Marketaux API client for ticker-tagged financial news."""

import os
import logging
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# Rate limiting tracking (100 requests/day limit)
_request_count = 0
_request_date = None

# No default watchlist - tickers come from discovery.py or env var

# MarketAux API configuration
MARKETAUX_BASE_URL = "https://api.marketaux.com/v1/news/all"


def fetch_news(tickers: List[str] = None, max_results: int = 20, broad: bool = False) -> List[Dict]:
    """Fetch ticker-tagged financial news from Marketaux API.

    Extracts pre-built sentiment_score per ticker (-1.0 to 1.0).
    DO NOT re-analyze Marketaux sentiment with Claude — use it directly.

    Behavior depends on TICKER_MODE environment variable:
    - discovery mode: fetch broad financial news (no ticker filter), return ALL articles with ticker tags
    - watchlist mode: fetch news filtered to provided tickers only

    Args:
        tickers: List of stock ticker symbols to filter for (e.g. ['AAPL', 'MSFT']).
                In discovery mode, this parameter is ignored.
                In watchlist mode, this is required.
        max_results: Maximum number of articles to return.
        broad: When broad=True, fetch without ticker filtering for use by discovery.py.
               When False, filter to provided tickers. Overrides TICKER_MODE behavior.

    Returns:
        List of dicts with keys: title, ticker, sentiment_score, snippet, url, published_at, source.
        Source field is always "marketaux".
    """
    try:
        # Check rate limits
        _check_rate_limit()

        # Verify API key exists
        api_key = os.getenv('MARKETAUX_API_KEY')
        if not api_key:
            logger.error("MARKETAUX_API_KEY not found in environment variables")
            return []

        # Check ticker mode from environment
        ticker_mode = os.getenv('TICKER_MODE', 'discovery')

        # Generate yesterday's date for filtering recent articles
        published_after = _get_published_after()

        # TODO: implement broad mode filtering
        # Build API parameters based on mode
        if ticker_mode == 'discovery':
            # Discovery mode: fetch broad news, no ticker filter
            params = _build_discovery_params(published_after, max_results)
            logger.info(f"Making MarketAux API request in discovery mode (no ticker filter)")
        else:
            # Watchlist mode: filter by provided tickers
            if not tickers:
                logger.error("Watchlist mode requires tickers parameter")
                return []
            params = _build_watchlist_params(tickers, published_after, max_results)
            logger.info(f"Making MarketAux API request in watchlist mode for tickers: {tickers}")

        # Make API request
        response = requests.get(MARKETAUX_BASE_URL, params=params, timeout=30)
        response.raise_for_status()

        # Parse and transform response
        response_json = response.json()
        articles = _parse_articles(response_json)

        logger.info(f"MarketAux API returned {len(articles)} articles")
        return articles

    except requests.exceptions.RequestException as e:
        logger.error(f"MarketAux API request failed: {e}")
        return []
    except Exception as e:
        logger.error(f"MarketAux API unexpected error: {e}")
        return []


def _check_rate_limit():
    """Check and update rate limiting counter."""
    global _request_count, _request_date

    today = datetime.now().date()

    if _request_date != today:
        _request_count = 0
        _request_date = today

    _request_count += 1

    if _request_count >= 80:
        logger.warning(
            f"MarketAux API: {_request_count} requests made today, "
            "approaching daily limit of 100"
        )


def _build_discovery_params(published_after: str, max_results: int) -> Dict:
    """Build API request parameters for discovery mode (broad news, no ticker filter).

    Args:
        published_after: Date string in YYYY-MM-DD format.
        max_results: Maximum number of articles to return.

    Returns:
        Dictionary of API parameters for discovery mode.
    """
    params = {
        'api_token': os.getenv('MARKETAUX_API_KEY'),
        'filter_entities': 'true',
        'language': 'en',
        'limit': max_results,
        'published_after': published_after
        # No 'symbols' parameter - fetch all financial news
    }

    return params


def _build_watchlist_params(tickers: List[str], published_after: str, max_results: int) -> Dict:
    """Build API request parameters for watchlist mode (filtered by tickers).

    Args:
        tickers: List of stock ticker symbols.
        published_after: Date string in YYYY-MM-DD format.
        max_results: Maximum number of articles to return.

    Returns:
        Dictionary of API parameters for watchlist mode.
    """
    params = {
        'api_token': os.getenv('MARKETAUX_API_KEY'),
        'filter_entities': 'true',
        'language': 'en',
        'limit': max_results,
        'symbols': ','.join(tickers),
        'published_after': published_after
    }

    return params


def _parse_articles(response_json: Dict) -> List[Dict]:
    """Extract and transform articles from MarketAux API response.

    Args:
        response_json: Raw JSON response from MarketAux API.

    Returns:
        List of transformed article dictionaries.
    """
    articles = []

    for article in response_json.get('data', []):
        # Extract common fields
        base_article = {
            'title': article.get('title', ''),
            'snippet': article.get('snippet', ''),
            'url': article.get('url', ''),
            'published_at': article.get('published_at', ''),
            'source': 'marketaux'
        }

        # Create one record per entity/ticker mentioned
        entities = article.get('entities', [])
        if entities:
            for entity in entities:
                symbol = entity.get('symbol')
                if symbol:  # Ensure ticker exists
                    ticker_article = base_article.copy()
                    ticker_article.update({
                        'ticker': symbol,
                        'sentiment_score': entity.get('sentiment_score', 0.0)
                    })
                    articles.append(ticker_article)
        # Skip articles with no entities (no sentiment data available)

    return articles


def _get_published_after() -> str:
    """Get date string for filtering recent articles.

    Returns:
        Yesterday's date in YYYY-MM-DD format.
    """
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime('%Y-%m-%d')