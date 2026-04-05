"""NewsAPI.ai (EventRegistry) client for macro/geopolitical/economic headlines."""

import os
import re
import logging
import requests
from datetime import datetime
from typing import List, Dict, Union, Set, Tuple
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


def _get_api_key() -> str:
    """Get NewsAPI key with backward compatibility.

    Returns:
        API key from environment variables
    """
    # Try new standard first, then fall back to legacy
    api_key = os.getenv('NEWSAPI_KEY') or os.getenv('NEWSAPI_AI_KEY')
    if not api_key:
        logger.error("Neither NEWSAPI_KEY nor NEWSAPI_AI_KEY found in environment variables")
    return api_key


def _parse_discovery_context(discovery_context: Dict) -> Tuple[str, List[str], str]:
    """Parse discovery context into mode, tickers, and cycle_id.

    Args:
        discovery_context: Discovery result from discovery.py

    Returns:
        Tuple of (mode, tickers, cycle_id)
    """
    if not discovery_context:
        return 'legacy', [], ''

    return (
        discovery_context.get('mode', 'legacy'),
        discovery_context.get('tickers', []),
        discovery_context.get('cycle_id', '')
    )


def extract_tickers_from_text(text: str) -> Set[str]:
    """Extract ticker symbols from article text using multiple patterns.

    Detects ticker symbols in various formats:
    - $AAPL style mentions
    - (NASDAQ:AAPL) style mentions
    - Standalone 1-5 letter uppercase words near financial keywords

    Args:
        text: Article text to scan for ticker symbols

    Returns:
        Set of ticker symbols found in text
    """
    if not text:
        return set()

    tickers = set()

    # Pattern 1: $SYMBOL format (most reliable)
    dollar_symbols = re.findall(r'\$([A-Z]{1,5})\b', text)
    tickers.update(dollar_symbols)

    # Pattern 2: (EXCHANGE:SYMBOL) format
    exchange_symbols = re.findall(r'\([A-Z]+:([A-Z]{1,5})\)', text)
    tickers.update(exchange_symbols)

    # Pattern 3: Standalone symbols near financial keywords
    financial_keywords = [
        'stock', 'shares', 'equity', 'trading', 'earnings', 'revenue',
        'profit', 'loss', 'IPO', 'acquisition', 'merger', 'ticker',
        'symbol', 'NYSE', 'NASDAQ', 'quote', 'price'
    ]

    # Create pattern for financial keywords
    keywords_pattern = '|'.join(financial_keywords)
    financial_context_pattern = rf'\b(?:{keywords_pattern})\b'

    # Find financial keywords in text
    for match in re.finditer(financial_context_pattern, text, re.IGNORECASE):
        # Look for symbols within 100 characters of financial keywords
        start = max(0, match.start() - 100)
        end = min(len(text), match.end() + 100)
        context = text[start:end]

        # Find potential symbols in this context
        symbols = re.findall(r'\b[A-Z]{2,5}\b', context)
        tickers.update(symbols)

    # Filter out common false positives
    false_positives = {
        'CEO', 'CFO', 'IPO', 'SEC', 'NYSE', 'NASDAQ', 'USD', 'USA',
        'API', 'AI', 'IT', 'TV', 'UK', 'EU', 'US', 'NY', 'CA', 'TX',
        'LLC', 'INC', 'CORP', 'LTD', 'THE', 'AND', 'FOR', 'WITH',
        'FROM', 'THIS', 'THAT', 'YEAR', 'TIME', 'NEWS', 'DATA'
    }

    return tickers - false_positives


def _calculate_confidence(text: str, tickers: Set[str]) -> float:
    """Calculate confidence score for ticker extraction.

    Args:
        text: Original text that was scanned
        tickers: Set of tickers that were extracted

    Returns:
        Confidence score between 0.0 and 1.0
    """
    if not tickers:
        return 0.0

    # Count different types of mentions
    dollar_mentions = len(re.findall(r'\$[A-Z]{1,5}\b', text))
    exchange_mentions = len(re.findall(r'\([A-Z]+:[A-Z]{1,5}\)', text))
    financial_keywords = len(re.findall(
        r'\b(?:stock|shares|equity|trading|earnings|revenue)\b',
        text, re.IGNORECASE
    ))

    # Calculate weighted confidence
    confidence = min(1.0, (
        dollar_mentions * 0.4 +
        exchange_mentions * 0.4 +
        financial_keywords * 0.2
    ) / len(tickers))

    return round(confidence, 2)


def _enhance_articles_with_tickers(articles: List[Dict]) -> List[Dict]:
    """Add ticker extraction to articles for discovery feedback.

    Args:
        articles: List of article dicts from NewsAPI

    Returns:
        Articles enhanced with ticker extraction data
    """
    enhanced = []

    for article in articles:
        # Extract tickers from title and snippet
        text = f"{article.get('title', '')} {article.get('snippet', '')}"
        extracted_tickers = extract_tickers_from_text(text)

        # Create enhanced article with ticker information
        enhanced_article = {**article}
        enhanced_article['tickers'] = list(extracted_tickers)
        enhanced_article['extraction_confidence'] = _calculate_confidence(text, extracted_tickers)

        enhanced.append(enhanced_article)

    return enhanced


def fetch_headlines(
    topics: List[str] = None,
    max_results: int = 15,
    discovery_context: Dict = None,
    watchlist: List[str] = None,
    broad: bool = False
) -> List[Dict]:
    """Fetch macro/geopolitical/economic headlines from NewsAPI.ai with discovery integration.

    Returns headlines + snippets only (no full text from this source).
    Every item flagged for full-text enrichment via waterfall.

    Args:
        topics: List of topic categories to fetch (e.g. ['macro', 'geopolitical', 'economic']).
                If None, fetches all categories.
        max_results: Maximum total articles across all topics.
        discovery_context: Optional discovery context from discovery.py containing mode and tickers.
        watchlist: Optional list of ticker symbols for filtering.
        broad: When broad=True, fetch without ticker filtering for use by discovery.py.
               When False, filter to provided tickers. Works with discovery_context.

    Returns:
        List of dicts with keys: title, snippet, topics, url, published_at, source, needs_full_text.
        In discovery mode, also includes: tickers, extraction_confidence.
        Source field is always "newsapi".
        needs_full_text field is always True.
    """
    # Parse discovery context
    mode, context_tickers, cycle_id = _parse_discovery_context(discovery_context)

    # Log discovery context for debugging
    if cycle_id:
        logger.info(f"NewsAPI operating in {mode} mode for cycle {cycle_id}")

    api_key = _get_api_key()
    if not api_key:
        return []

    # Determine effective watchlist from context or direct parameter
    effective_watchlist = watchlist or context_tickers

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
    all_articles = all_articles[:max_results]

    # TODO: implement broad mode filtering
    # Apply mode-based processing
    if mode == 'discovery':
        # Discovery mode: Extract tickers from all articles for discovery feedback
        all_articles = _enhance_articles_with_tickers(all_articles)
        logger.info(f"Discovery mode: Enhanced {len(all_articles)} articles with ticker extraction")

    elif mode == 'watchlist' and effective_watchlist:
        # Watchlist mode: Filter articles by ticker relevance
        all_articles = _filter_by_ticker_relevance(all_articles, effective_watchlist)
        logger.info(f"Watchlist mode: Filtered to {len(all_articles)} relevant articles for {len(effective_watchlist)} tickers")

    # Legacy mode: No special processing (backward compatibility)

    logger.info(f"NewsAPI.ai returned {len(all_articles)} articles across topics: {effective_topics}")
    return all_articles


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
    api_key = _get_api_key()

    payload = {
        'action': 'getArticles',
        'apiKey': api_key,
        'keyword': keyword,
        'keywordLoc': 'body,title',
        'lang': 'eng',
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
            'tickers': [],
            'extraction_confidence': 0.0
        })

    return articles
