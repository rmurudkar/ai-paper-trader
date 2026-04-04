"""NewsAPI.ai client for macro, geopolitical, and economic headlines."""

import os
import requests
from typing import List, Dict


def fetch_macro_news(max_results: int = 10) -> List[Dict]:
    """Fetch macro economic headlines from NewsAPI.ai.

    No pre-built sentiment — these go to Claude for analysis.

    Args:
        max_results: Maximum number of articles to return.

    Returns:
        List of dicts with keys: title, summary, topics, url, published_at.
    """
    pass


def fetch_geopolitical_news(max_results: int = 10) -> List[Dict]:
    """Fetch geopolitical headlines from NewsAPI.ai.

    No pre-built sentiment — these go to Claude for analysis.

    Args:
        max_results: Maximum number of articles to return.

    Returns:
        List of dicts with keys: title, summary, topics, url, published_at.
    """
    pass


def fetch_economic_news(max_results: int = 10) -> List[Dict]:
    """Fetch economic headlines from NewsAPI.ai.

    No pre-built sentiment — these go to Claude for analysis.

    Args:
        max_results: Maximum number of articles to return.

    Returns:
        List of dicts with keys: title, summary, topics, url, published_at.
    """
    pass