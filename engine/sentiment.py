"""Claude-powered sentiment analysis engine."""

import os
import anthropic
from typing import List, Dict
from fetchers.aggregator import fetch_all_news


def analyze_article_sentiment(article: Dict) -> Dict:
    """Process a single article for sentiment analysis.

    Marketaux items: pass sentiment_score directly, skip Claude call.
    NewsAPI items: send full_text (not headline) to Claude for analysis.

    NEVER send raw headlines to Claude for sentiment — always use full_text.
    NEVER re-analyze Marketaux sentiment scores — they are pre-computed and trusted.

    Args:
        article: Article dict from aggregator with source field.

    Returns:
        Dict with keys:
            ticker (str),
            sentiment_score (float, -1.0 to 1.0),
            source (str: 'marketaux' | 'newsapi'),
            reasoning (str, only for newsapi items).
    """
    pass


def analyze_newsapi_with_claude(full_text: str, ticker: str) -> Dict:
    """Send full article text to Claude for sentiment analysis.

    Claude prompt: analyze sentiment as it relates to specific tickers.

    Args:
        full_text: Full article text (not headline).
        ticker: Ticker symbol to analyze sentiment for.

    Returns:
        Dict with keys:
            sentiment_score (float, -1.0 to 1.0),
            reasoning (str).
    """
    pass


def batch_analyze_articles(articles: List[Dict]) -> List[Dict]:
    """Run sentiment analysis for multiple articles.

    Processes both Marketaux (direct sentiment passthrough) and
    NewsAPI (Claude analysis of full_text) articles.

    Args:
        articles: List of article dicts from aggregator.

    Returns:
        List of sentiment analysis results per ticker per article.
    """
    pass


def get_ticker_sentiment_scores(ticker: str, articles: List[Dict]) -> Dict:
    """Get aggregated sentiment scores for a specific ticker.

    Args:
        ticker: Stock ticker symbol.
        articles: List of analyzed articles.

    Returns:
        Dict with aggregated sentiment data for the ticker.
    """
    pass
