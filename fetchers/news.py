"""NewsAPI client for fetching financial headlines."""

import os
import requests


def fetch_headlines(ticker: str, max_results: int = 10) -> list[dict]:
    """Fetch recent news headlines for a given ticker symbol.

    Args:
        ticker: Stock ticker symbol (e.g. 'AAPL').
        max_results: Maximum number of articles to return.

    Returns:
        List of article dicts with keys: title, description, url, publishedAt.
    """
    pass


def fetch_top_financial_news(max_results: int = 20) -> list[dict]:
    """Fetch top financial news headlines regardless of ticker.

    Args:
        max_results: Maximum number of articles to return.

    Returns:
        List of article dicts with keys: title, description, url, publishedAt.
    """
    pass
