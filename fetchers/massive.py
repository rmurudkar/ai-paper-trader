"""Massive.com API client for ticker-tagged financial news.

Fetches news articles from Massive with built-in ticker tagging and sentiment analysis.
Similar to Marketaux but with more reliable ticker identification and sentiment data.

Setup:
    Requires MASSIVE_API_KEY in .env
    Sign up at: https://massive.com/
"""

import os
import logging
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MASSIVE_BASE_URL = "https://api.massive.com/v2/reference/news"
MASSIVE_MAX_RESULTS = 100


def fetch_news(tickers: List[str] = None, max_results: int = 50) -> List[Dict]:
    """Fetch ticker-tagged financial news from Massive API (v3).

    Returns articles with pre-tagged ticker symbols and sentiment analysis.

    Args:
        tickers: List of stock ticker symbols to filter for (e.g. ['AAPL', 'MSFT']).
                If None, fetches broad news without ticker filtering.
        max_results: Maximum number of articles to return (max 100).

    Returns:
        List of dicts with keys: title, description, tickers, sentiment_score,
        url, published_at, source, author.
        Source field is always "massive".
    """
    try:
        api_key = os.getenv("MASSIVE_API_KEY")
        if not api_key:
            logger.error("massive | ❌ MASSIVE_API_KEY not found in .env file")
            return []

        logger.info("massive | ✓ MASSIVE_API_KEY found, making API request...")

        # Build request parameters for v2 API
        # Note: Massive v2 API returns latest news by default, doesn't support date filtering in query
        params = {
            "apiKey": api_key,
            "limit": min(max_results, MASSIVE_MAX_RESULTS),
        }

        logger.debug(f"massive | API URL: {MASSIVE_BASE_URL}")
        logger.debug(f"massive | Parameters: limit={params['limit']}")

        # Make API request
        response = requests.get(MASSIVE_BASE_URL, params=params, timeout=30)
        response.raise_for_status()

        # Parse response
        response_json = response.json()
        articles = _parse_articles(response_json, tickers)

        logger.info(f"massive | ✓ Massive API: {len(articles)} articles with ticker tags")
        return articles

    except requests.exceptions.RequestException as e:
        logger.error(f"massive | ❌ API request failed: {e}")
        return []
    except Exception as e:
        logger.error(f"massive | ❌ Unexpected error: {e}")
        return []


def _parse_articles(response_json: Dict, ticker_filter: List[str] = None) -> List[Dict]:
    """Extract and transform articles from Massive API v3 response.

    Massive v3 returns articles with ticker tags in the results array.

    Args:
        response_json: Raw JSON response from Massive API v3.
        ticker_filter: Optional list of tickers to filter by.

    Returns:
        List of transformed article dictionaries.
    """
    articles = []
    ticker_filter_set = set(ticker_filter) if ticker_filter else None

    # Massive v3 API returns "results" array
    results = response_json.get("results", [])
    logger.debug(f"massive | Processing {len(results)} articles from API response")

    for article in results:
        # Extract metadata from Massive v2 API response
        title = article.get("title", "")
        description = article.get("description", "")
        url = article.get("article_url", "")
        published_at = article.get("published_utc", "")
        author = article.get("author", "")

        # Massive v2 returns tickers as an array of strings
        tickers_raw = article.get("tickers", [])
        tickers = [t.upper() for t in tickers_raw if t] if tickers_raw else []

        # Skip if no ticker found
        if not tickers:
            logger.debug(f"massive | Skipping article (no ticker): {title[:50]}...")
            continue

        # Skip if ticker filter applied and no matching tickers
        if ticker_filter_set and not any(t in ticker_filter_set for t in tickers):
            logger.debug(f"massive | Skipping article (filtered): {title[:50]}...")
            continue

        # Extract sentiment from Massive insights (array of sentiment objects)
        sentiment_score = 0.0
        insights = article.get("insights", [])
        if insights and isinstance(insights, list) and len(insights) > 0:
            insight = insights[0]
            if isinstance(insight, dict):
                sentiment_str = insight.get("sentiment", "neutral").lower()
                if sentiment_str == "positive":
                    sentiment_score = 0.7
                elif sentiment_str == "negative":
                    sentiment_score = -0.7
                else:
                    sentiment_score = 0.0

        # Create article record
        articles.append(
            {
                "title": title,
                "description": description,
                "tickers": tickers,
                "sentiment_score": sentiment_score,
                "url": url,
                "published_at": published_at,
                "author": author,
                "source": "massive",
            }
        )
        logger.debug(f"massive | Extracted: {', '.join(tickers)} from '{title[:50]}...'")

    logger.info(f"massive | Parsed {len(articles)} articles with ticker tags")
    return articles
