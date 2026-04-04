"""Alpaca News API client for Benzinga news feed."""

import os
from alpaca.trading.client import TradingClient
from alpaca.data.historical import NewsClient
from typing import List, Dict


def fetch_ticker_news(ticker: str, max_results: int = 10) -> List[Dict]:
    """Fetch Benzinga news for a specific ticker via Alpaca News API.

    Free Benzinga news feed available with Alpaca account.

    Args:
        ticker: Stock ticker symbol to filter news for.
        max_results: Maximum number of articles to return.

    Returns:
        List of dicts with keys: title, full_text, ticker, url, published_at, source.
        Source field is always "alpaca".
    """
    pass


def fetch_watchlist_news(watchlist: List[str], max_results: int = 20) -> List[Dict]:
    """Fetch Benzinga news for all tickers in watchlist via Alpaca News API.

    Args:
        watchlist: List of ticker symbols to fetch news for.
        max_results: Maximum total articles across all tickers.

    Returns:
        List of dicts with keys: title, full_text, ticker, url, published_at, source.
        Source field is always "alpaca".
    """
    pass


def get_alpaca_news_client() -> NewsClient:
    """Initialize and return Alpaca News API client.

    Uses ALPACA_API_KEY and ALPACA_SECRET_KEY from environment.

    Returns:
        Configured Alpaca NewsClient instance.
    """
    pass


def filter_by_watchlist(articles: List[Dict], watchlist: List[str]) -> List[Dict]:
    """Filter Alpaca news articles by ticker watchlist.

    Args:
        articles: List of article dicts from Alpaca News API.
        watchlist: List of ticker symbols to filter for.

    Returns:
        Filtered list of articles matching watchlist tickers.
    """
    pass


def format_alpaca_article(raw_article: Dict) -> Dict:
    """Convert raw Alpaca News API response to standard format.

    Args:
        raw_article: Raw article dict from Alpaca News API.

    Returns:
        Formatted dict with keys: title, full_text, ticker, url, published_at, source.
        Source field is always "alpaca".
    """
    pass