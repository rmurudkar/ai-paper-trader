"""NewsAPI.ai (EventRegistry) client for macro/geopolitical/economic headlines."""

import os
import logging
import requests
from datetime import datetime
from typing import List, Dict, Union
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

# NewsAPI.ai (EventRegistry) API configuration
NEWSAPI_BASE_URL = "https://eventregistry.org/api/v1/article/getArticles"
SNIPPET_MAX_CHARS = 500

# Primary search keyword per topic — used as the $query keyword in EventRegistry.
# Single representative term works best on the free tier; the OR chain syntax is
# not supported for more than ~3 quoted phrases.
_TOPIC_KEYWORDS = {
    'macro':        'inflation',
    'geopolitical': 'tariff',
    'economic':     'recession',
    'market':       'earnings',
    'energy':       'oil',
    'crypto':       'bitcoin',
}

DEFAULT_TOPICS = ['macro', 'geopolitical', 'economic', 'market', 'energy']


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
    api_key = os.getenv('NEWSAPI_AI_KEY')
    if not api_key:
        logger.error("NEWSAPI_AI_KEY not found in environment variables")
        return []

    effective_topics = topics if topics else DEFAULT_TOPICS
    results_per_topic = max(5, max_results // len(effective_topics))

    all_articles = []
    seen_urls = set()

    for topic in effective_topics:
        if topic in _TOPIC_KEYWORDS:
            articles = _fetch_articles(_TOPIC_KEYWORDS[topic], topic, results_per_topic)
        else:
            logger.warning(f"Unknown topic '{topic}', skipping")
            continue

        for article in articles:
            url = article.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_articles.append(article)

    # Sort by published_at descending
    all_articles.sort(key=lambda x: x.get('published_at', ''), reverse=True)

    logger.info(f"NewsAPI.ai returned {len(all_articles[:max_results])} articles across topics: {effective_topics}")
    return all_articles[:max_results]


def _fetch_macro_news(max_results: int = 10) -> List[Dict]:
    """Private: Fetch macro economic headlines from NewsAPI.ai."""
    return _fetch_articles('inflation', 'macro', max_results)


def _fetch_geopolitical_news(max_results: int = 10) -> List[Dict]:
    """Private: Fetch geopolitical headlines from NewsAPI.ai."""
    return _fetch_articles('tariff', 'geopolitical', max_results)


def _fetch_economic_news(max_results: int = 10) -> List[Dict]:
    """Private: Fetch economic headlines from NewsAPI.ai."""
    return _fetch_articles('recession', 'economic', max_results)


def _filter_by_ticker_relevance(articles: List[Dict], watchlist: List[str]) -> List[Dict]:
    """Private: Filter articles by relevance to ticker watchlist.

    Macro and geopolitical articles always pass through (broad market impact).
    Economic articles are included only if a watchlist ticker appears in title or snippet.
    """
    if not watchlist:
        return articles

    watchlist_lower = [t.lower() for t in watchlist]
    relevant = []

    # Topics with broad market impact — never filter out
    broad_topics = {'macro', 'geopolitical', 'energy'}

    for article in articles:
        topic_list = article.get('topics', [])
        if any(t in broad_topics for t in topic_list):
            relevant.append(article)
            continue

        text = f"{article.get('title', '')} {article.get('snippet', '')}".lower()
        if any(ticker in text for ticker in watchlist_lower):
            relevant.append(article)

    return relevant


def _fetch_articles(keyword: str, topic_name: str, max_results: int) -> List[Dict]:
    """Private: POST to NewsAPI.ai and return parsed articles for a keyword."""
    api_key = os.getenv('NEWSAPI_AI_KEY')

    payload = {
        'apiKey': api_key,
        '$query': {
            '$and': [
                {'keyword': keyword, 'keywordLoc': 'title,body'},
                {'lang': 'eng'},
            ]
        },
        'isDuplicateFilter': 'skipDuplicates',
        'dataType': ['news'],
        'articlesPage': 1,
        'articlesCount': max_results,
        'articlesSortBy': 'date',
        'articlesSortByAsc': False,
        'includeArticleTitle': True,
        'includeArticleBody': True,
        'includeArticleUrl': True,
        'includeArticlePublishDate': True,
        'includeArticleSource': True,
        'includeArticleCategories': False,
        'includeArticleConcepts': False,
    }

    try:
        logger.info(f"NewsAPI.ai request for topic '{topic_name}' (max {max_results})")
        response = requests.post(
            NEWSAPI_BASE_URL,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=30,
        )
        response.raise_for_status()

        return _parse_articles(response.json(), topic_name)
    except requests.exceptions.RequestException as e:
        logger.error(f"NewsAPI.ai request failed for topic '{topic_name}': {e}")
        return []
    except Exception as e:
        logger.error(f"NewsAPI.ai unexpected error for topic '{topic_name}': {e}")
        return []


def _parse_articles(response_json: Dict, topic_name: str) -> List[Dict]:
    """Private: Extract and transform articles from NewsAPI.ai response."""
    articles = []
    results = response_json.get('articles', {}).get('results', [])

    for article in results:
        title = article.get('title', '')
        url = article.get('url', '')

        if not title or not url:
            continue

        body = article.get('body', '')
        snippet = body[:SNIPPET_MAX_CHARS].strip() if body else ''
        published_at = article.get('publishedDate', article.get('date', ''))

        articles.append({
            'title': title,
            'snippet': snippet,
            'topics': [topic_name],
            'url': url,
            'published_at': published_at,
            'source': 'newsapi',
            'needs_full_text': True,
        })

    return articles
