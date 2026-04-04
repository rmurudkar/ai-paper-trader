"""Alpaca News API client for Benzinga news feed."""

import os
from alpaca.trading.client import TradingClient
from alpaca.data.historical import NewsClient
from typing import List, Dict


def fetch_news(watchlist: List[str], max_results: int = 20) -> List[Dict]:
    """Fetch Benzinga news for watchlist tickers via Alpaca News API.

    Step 2 of waterfall enrichment for NewsAPI.ai articles.
    Also fetch independent Alpaca News feed filtered by watchlist.
    Free Benzinga news feed available with Alpaca account.

    Args:
        watchlist: List of ticker symbols to fetch news for.
        max_results: Maximum total articles across all tickers.

    Returns:
        List of dicts with keys: title, full_text, ticker, url, published_at, source, partial:false.
        Source field is always "alpaca".
    """
    pass


def _fetch_ticker_news(ticker: str, max_results: int = 10) -> List[Dict]:
    """Private: Fetch Benzinga news for a specific ticker via Alpaca News API."""
    pass


def _get_alpaca_news_client() -> NewsClient:
    """Private: Initialize and return Alpaca News API client."""
    pass


def _filter_by_watchlist(articles: List[Dict], watchlist: List[str]) -> List[Dict]:
    """Private: Filter Alpaca news articles by ticker watchlist."""
    pass


def _format_alpaca_article(raw_article: Dict) -> Dict:
    """Private: Convert raw Alpaca News API response to standard format."""
    pass