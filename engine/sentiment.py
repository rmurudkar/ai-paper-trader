"""Claude-powered sentiment analysis engine."""

import os
import json
import anthropic
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

# Defaults for pre-computed sources that don't provide enriched metadata
_DEFAULT_ENRICHMENT = {
    'urgency': 'standard',
    'materiality': 'unknown',
    'time_horizon': 'medium_term',
}


def analyze_article_sentiment(article: Dict) -> List[Dict]:
    """Process a single article for sentiment analysis.

    Marketaux items: pass sentiment_score directly, skip Claude call.
    Massive items: pass sentiment_score directly, skip Claude call.
    NewsAPI items: send full_text (not headline) to Claude for analysis.

    NEVER send raw headlines to Claude for sentiment — always use full_text.
    NEVER re-analyze Marketaux sentiment scores — they are pre-computed and trusted.

    Args:
        article: Article dict from aggregator with source field.

    Returns:
        List of dicts, one per ticker mentioned in the article, each with keys:
            ticker (str),
            sentiment_score (float, -1.0 to 1.0),
            source (str: 'marketaux' | 'massive' | 'newsapi'),
            reasoning (str, only for newsapi items).
    """
    results = []
    source = article.get('source', '')

    if source == 'marketaux':
        # Marketaux provides ticker and sentiment_score directly
        ticker = article.get('ticker')
        sentiment_score = article.get('sentiment_score')

        if ticker and sentiment_score is not None:
            results.append({
                'ticker': ticker,
                'sentiment_score': float(sentiment_score),
                'source': 'marketaux',
                'reasoning': f"Pre-computed sentiment from Marketaux: {sentiment_score}",
                **_DEFAULT_ENRICHMENT,
            })

    elif source == 'massive':
        # Massive provides tickers list and sentiment_score
        tickers = article.get('tickers', [])
        sentiment_score = article.get('sentiment_score')

        if tickers and sentiment_score is not None:
            for ticker in tickers:
                results.append({
                    'ticker': ticker,
                    'sentiment_score': float(sentiment_score),
                    'source': 'massive',
                    'reasoning': f"Pre-computed sentiment from Massive: {sentiment_score}",
                    **_DEFAULT_ENRICHMENT,
                })

    elif source in ['newsapi', 'alpaca', 'polygon']:
        # NewsAPI and similar sources need Claude analysis
        full_text = article.get('full_text', '')
        tickers = article.get('tickers', [])
        partial = article.get('partial', False)

        if not full_text:
            logger.warning(f"No full_text available for {source} article: {article.get('title', 'Unknown')}")
            return results

        if partial:
            logger.warning(f"Article is partial (snippet-only), analysis may be limited: {article.get('title', 'Unknown')}")

        if not tickers:
            logger.warning(f"No tickers found for {source} article: {article.get('title', 'Unknown')}")
            return results

        # Analyze sentiment for each ticker mentioned in the article
        for ticker in tickers:
            try:
                claude_result = analyze_newsapi_with_claude(full_text, ticker)
                if claude_result:
                    results.append({
                        'ticker': ticker,
                        'sentiment_score': claude_result.get('sentiment_score', 0.0),
                        'source': source,
                        'reasoning': claude_result.get('reasoning', 'Claude analysis'),
                        'urgency': claude_result.get('urgency', 'standard'),
                        'materiality': claude_result.get('materiality', 'unknown'),
                        'time_horizon': claude_result.get('time_horizon', 'medium_term'),
                    })
            except Exception as e:
                logger.error(f"Claude sentiment analysis failed for {ticker} in {source} article: {e}")

    else:
        logger.warning(f"Unknown source '{source}' for sentiment analysis")

    return results


def analyze_newsapi_with_claude(full_text: str, ticker: str) -> Dict:
    """Send full article text to Claude for enriched sentiment analysis.

    Extracts not just sentiment but also urgency, materiality, and time
    horizon — metadata that directly feeds into strategy confidence and
    holding period decisions.

    Args:
        full_text: Full article text (not headline).
        ticker: Ticker symbol to analyze sentiment for.

    Returns:
        Dict with keys:
            sentiment_score (float, -1.0 to 1.0),
            urgency (str: 'breaking' | 'developing' | 'standard'),
            materiality (str: 'high' | 'medium' | 'low'),
            time_horizon (str: 'intraday' | 'short_term' | 'medium_term' | 'long_term'),
            reasoning (str).
    """
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

    # Truncate to 1200 words as per project rules
    words = full_text.split()
    if len(words) > 1200:
        truncated_text = ' '.join(words[:1200])
        logger.info(f"Truncated article from {len(words)} to 1200 words for Claude analysis")
    else:
        truncated_text = full_text

    prompt = f"""Analyze this financial news article as it relates to stock ticker {ticker}.

Return a JSON object with exactly these fields:

1. "sentiment_score": float from -1.0 (very bearish) to 1.0 (very bullish), 0.0 = neutral

2. "urgency": how time-sensitive is this news?
   - "breaking": just happened, market is reacting now (earnings surprise, FDA decision, CEO resignation)
   - "developing": unfolding over hours, still evolving (regulatory investigation, deal negotiations)
   - "standard": background/thematic piece, no immediate catalyst (industry trends, analyst commentary)

3. "materiality": how much does this affect the company's fundamentals?
   - "high": directly impacts revenue, earnings, or valuation (earnings miss, major contract win/loss, guidance change, lawsuit with material damages)
   - "medium": affects operations or market position but not immediately quantifiable (management change, product launch, partnership)
   - "low": minimal fundamental impact (minor executive hire, conference attendance, generic industry commentary)

4. "time_horizon": how long will this news drive price action?
   - "intraday": one-day catalyst, price impact exhausts within the session
   - "short_term": 1-5 day catalyst (earnings reaction, product launch)
   - "medium_term": 1-4 week theme (regulatory process, sector rotation)
   - "long_term": multi-month structural change (new market entry, fundamental business pivot)

5. "reasoning": one sentence explaining your assessment

Article text:
{truncated_text}"""

    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=400,
            temperature=0.1,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )

        response_text = response.content[0].text.strip()

        # Strip markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines).strip()

        try:
            result = json.loads(response_text)
            return _parse_claude_result(result)
        except json.JSONDecodeError:
            logger.warning("Could not parse JSON from Claude response, falling back to text parsing")
            return _fallback_parse(response_text)

    except Exception as e:
        logger.error(f"Claude API call failed for ticker {ticker}: {e}")
        return {
            'sentiment_score': 0.0,
            'urgency': 'standard',
            'materiality': 'unknown',
            'time_horizon': 'medium_term',
            'reasoning': f"Analysis failed: {str(e)}"
        }


# Valid enum values for enriched fields
_VALID_URGENCY = {'breaking', 'developing', 'standard'}
_VALID_MATERIALITY = {'high', 'medium', 'low'}
_VALID_TIME_HORIZON = {'intraday', 'short_term', 'medium_term', 'long_term'}


def _parse_claude_result(result: Dict) -> Dict:
    """Parse and validate a successful JSON response from Claude."""
    sentiment_score = max(-1.0, min(1.0, float(result.get('sentiment_score', 0.0))))

    urgency = result.get('urgency', 'standard')
    if urgency not in _VALID_URGENCY:
        urgency = 'standard'

    materiality = result.get('materiality', 'unknown')
    if materiality not in _VALID_MATERIALITY:
        materiality = 'unknown'

    time_horizon = result.get('time_horizon', 'medium_term')
    if time_horizon not in _VALID_TIME_HORIZON:
        time_horizon = 'medium_term'

    return {
        'sentiment_score': sentiment_score,
        'urgency': urgency,
        'materiality': materiality,
        'time_horizon': time_horizon,
        'reasoning': result.get('reasoning', 'Claude sentiment analysis'),
    }


def _fallback_parse(response_text: str) -> Dict:
    """Extract sentiment from a non-JSON Claude response."""
    text_lower = response_text.lower()
    if 'very positive' in text_lower or 'strongly positive' in text_lower:
        sentiment_score = 0.8
    elif 'positive' in text_lower:
        sentiment_score = 0.5
    elif 'very negative' in text_lower or 'strongly negative' in text_lower:
        sentiment_score = -0.8
    elif 'negative' in text_lower:
        sentiment_score = -0.5
    else:
        sentiment_score = 0.0

    # Try to infer urgency from keywords
    urgency = 'standard'
    if any(w in text_lower for w in ['breaking', 'just announced', 'just reported']):
        urgency = 'breaking'
    elif any(w in text_lower for w in ['developing', 'unfolding', 'emerging']):
        urgency = 'developing'

    return {
        'sentiment_score': sentiment_score,
        'urgency': urgency,
        'materiality': 'unknown',
        'time_horizon': 'medium_term',
        'reasoning': response_text[:200] + '...' if len(response_text) > 200 else response_text,
    }


def batch_analyze_articles(articles: List[Dict]) -> List[Dict]:
    """Run sentiment analysis for multiple articles.

    Processes both Marketaux/Massive (direct sentiment passthrough) and
    NewsAPI (Claude analysis of full_text) articles.

    Args:
        articles: List of article dicts from aggregator.

    Returns:
        List of sentiment analysis results per ticker per article.
    """
    all_results = []

    for article in articles:
        try:
            article_results = analyze_article_sentiment(article)
            all_results.extend(article_results)
        except Exception as e:
            logger.error(f"Failed to analyze sentiment for article {article.get('title', 'Unknown')}: {e}")

    logger.info(f"Batch sentiment analysis complete: {len(articles)} articles → {len(all_results)} ticker sentiments")
    return all_results


def get_ticker_sentiment_scores(ticker: str, sentiment_results: List[Dict]) -> Dict:
    """Get aggregated sentiment scores for a specific ticker.

    Args:
        ticker: Stock ticker symbol.
        sentiment_results: List of sentiment analysis results from batch_analyze_articles.

    Returns:
        Dict with aggregated sentiment data for the ticker.
    """
    ticker_sentiments = [
        result for result in sentiment_results
        if result.get('ticker', '').upper() == ticker.upper()
    ]

    if not ticker_sentiments:
        return {
            'ticker': ticker,
            'sentiment_score': 0.0,
            'article_count': 0,
            'source_breakdown': {},
            'confidence': 0.0,
        }

    # Calculate weighted average sentiment
    scores = [result['sentiment_score'] for result in ticker_sentiments]
    avg_sentiment = sum(scores) / len(scores)

    # Count articles by source
    source_breakdown = {}
    for result in ticker_sentiments:
        source = result.get('source', 'unknown')
        source_breakdown[source] = source_breakdown.get(source, 0) + 1

    # Confidence based on number of articles and source diversity
    article_count = len(ticker_sentiments)
    source_count = len(source_breakdown)
    confidence = min(1.0, (article_count * 0.2) + (source_count * 0.1))

    # Aggregate enriched metadata
    has_breaking = any(r.get('urgency') == 'breaking' for r in ticker_sentiments)
    has_developing = any(r.get('urgency') == 'developing' for r in ticker_sentiments)

    if has_breaking:
        dominant_urgency = 'breaking'
    elif has_developing:
        dominant_urgency = 'developing'
    else:
        dominant_urgency = 'standard'

    # Materiality: take the highest
    materiality_rank = {'high': 3, 'medium': 2, 'low': 1, 'unknown': 0}
    max_materiality = max(
        (r.get('materiality', 'unknown') for r in ticker_sentiments),
        key=lambda m: materiality_rank.get(m, 0),
    )

    # Time horizon: take the shortest (most actionable)
    horizon_rank = {'intraday': 1, 'short_term': 2, 'medium_term': 3, 'long_term': 4}
    shortest_horizon = min(
        (r.get('time_horizon', 'medium_term') for r in ticker_sentiments),
        key=lambda h: horizon_rank.get(h, 3),
    )

    return {
        'ticker': ticker,
        'sentiment_score': round(avg_sentiment, 3),
        'article_count': article_count,
        'source_breakdown': source_breakdown,
        'confidence': round(confidence, 2),
        'individual_scores': scores,
        'urgency': dominant_urgency,
        'materiality': max_materiality,
        'time_horizon': shortest_horizon,
    }
