"""Marketaux API client for ticker-tagged stock/financial news."""

import os
import requests
from typing import List, Dict


def fetch_ticker_news(ticker: str, max_results: int = 10) -> List[Dict]:
    """Fetch ticker-tagged stock/financial news from Marketaux API.

    Extracts pre-built sentiment scores per ticker. Do NOT re-analyze these with Claude.

    Args:
        ticker: Stock ticker symbol (e.g. 'AAPL').
        max_results: Maximum number of articles to return.

    Returns:
        List of dicts with keys: title, ticker, sentiment_score, url, published_at.
    """
    pass


def fetch_financial_news(max_results: int = 20) -> List[Dict]:
    """Fetch general financial news with ticker tags from Marketaux API.

    Args:
        max_results: Maximum number of articles to return.

    Returns:
        List of dicts with keys: title, ticker, sentiment_score, url, published_at.
    """
    pass