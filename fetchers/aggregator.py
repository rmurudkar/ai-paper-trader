"""News aggregator that merges and deduplicates from multiple sources."""

from typing import List, Dict, Literal
from . import marketaux, newsapi


def fetch_all_news(max_marketaux: int = 20, max_newsapi: int = 15) -> List[Dict]:
    """Combine output of both fetchers with deduplication.

    Combines output of marketaux and newsapi fetchers, deduplicates by URL
    and title similarity, and tags each item with source.

    Args:
        max_marketaux: Maximum articles from Marketaux API.
        max_newsapi: Maximum articles from NewsAPI.ai.

    Returns:
        Unified list of dicts with keys varying by source:
        - Marketaux items: title, ticker, sentiment_score, url, published_at, source
        - NewsAPI items: title, summary, topics, url, published_at, source

        Source field is either "marketaux" or "newsapi".
    """
    pass


def deduplicate_articles(articles: List[Dict]) -> List[Dict]:
    """Remove duplicate articles by URL and title similarity.

    Args:
        articles: List of article dicts from various sources.

    Returns:
        Deduplicated list of article dicts.
    """
    pass


def calculate_title_similarity(title1: str, title2: str) -> float:
    """Calculate similarity score between two article titles.

    Args:
        title1: First article title.
        title2: Second article title.

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    pass