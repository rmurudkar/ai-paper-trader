"""Marketaux API client for ticker-tagged financial news."""

import os
import requests
from typing import List, Dict


def fetch_news(tickers: List[str] = None, max_results: int = 20) -> List[Dict]:
    """Fetch ticker-tagged financial news from Marketaux API.

    Extracts pre-built sentiment_score per ticker (-1.0 to 1.0).
    DO NOT re-analyze Marketaux sentiment with Claude — use it directly.

    Args:
        tickers: List of stock ticker symbols to filter for (e.g. ['AAPL', 'MSFT']).
                If None, fetches general financial news.
        max_results: Maximum number of articles to return.

    Returns:
        List of dicts with keys: title, ticker, sentiment_score, snippet, url, published_at, source.
        Source field is always "marketaux".
    """
    pass