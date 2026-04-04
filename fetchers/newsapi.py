"""NewsAPI.ai client for macro/geopolitical/economic headlines."""

import os
import requests
from typing import List, Dict


def fetch_headlines(topics: List[str] = None, max_results: int = 15) -> List[Dict]:
    """Fetch macro/geopolitical/economic headlines from NewsAPI.ai.

    Returns headlines + snippets only (no full text from this source).
    Every item flagged for full-text enrichment via waterfall.

    Args:
        topics: List of topic categories to fetch (e.g. ['macro', 'geopolitical', 'economic']).
                If None, fetches all categories.
        max_results: Maximum total articles across all topics.

    Returns:
        List of dicts with keys: title, snippet, topics, url, published_at, source, needs_full_text.
        Source field is always "newsapi".
        needs_full_text field is always True.
    """
    pass


def _fetch_macro_news(max_results: int = 10) -> List[Dict]:
    """Private: Fetch macro economic headlines from NewsAPI.ai."""
    pass


def _fetch_geopolitical_news(max_results: int = 10) -> List[Dict]:
    """Private: Fetch geopolitical headlines from NewsAPI.ai."""
    pass


def _fetch_economic_news(max_results: int = 10) -> List[Dict]:
    """Private: Fetch economic headlines from NewsAPI.ai."""
    pass


def _filter_by_ticker_relevance(articles: List[Dict], watchlist: List[str]) -> List[Dict]:
    """Private: Filter articles by relevance to ticker watchlist."""
    pass