"""Claude-powered sentiment analysis engine."""

import os
import json
import anthropic
import logging
from datetime import datetime, timezone
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

    published_at = article.get('published_at')

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
                'published_at': published_at,
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
                    'published_at': published_at,
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
                        'published_at': published_at,
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

# --- Aggregation weights ---

# Source credibility: Claude-analyzed full text > pre-computed ticker-specific > pre-computed bulk
_SOURCE_CREDIBILITY = {
    'newsapi': 1.0,    # Claude-analyzed full article text
    'alpaca': 1.0,
    'polygon': 1.0,
    'marketaux': 0.8,  # Pre-computed, ticker-specific, generally reliable
    'massive': 0.6,    # Pre-computed, broader coverage but noisier
}

# Materiality impact on weight — high-materiality news should dominate
_MATERIALITY_WEIGHT = {
    'high': 2.0,       # Earnings, guidance, FDA, major contract
    'medium': 1.0,     # Product launch, management change, partnership
    'low': 0.5,        # Conference attendance, generic commentary
    'unknown': 0.5,    # Pre-computed sources without materiality classification
}

# Urgency impact — breaking news is more actionable
_URGENCY_WEIGHT = {
    'breaking': 2.0,   # Just happened, market reacting now
    'developing': 1.3, # Unfolding, still evolving
    'standard': 1.0,   # Background/thematic
}

# Recency decay brackets (hours_old -> multiplier)
# Articles from the last hour count 3x more than 6+ hour old articles
_RECENCY_BRACKETS = [
    (1.0, 3.0),    # 0-1 hours old: 3x weight
    (3.0, 2.0),    # 1-3 hours old: 2x weight
    (6.0, 1.0),    # 3-6 hours old: 1x weight (baseline)
    (float('inf'), 0.5),  # 6+ hours old: 0.5x weight
]


def _compute_article_weight(result: Dict, now: datetime = None) -> float:
    """Compute aggregation weight for a single sentiment result.

    Weight = source_credibility * materiality * urgency * recency.

    Args:
        result: Single sentiment result dict with source, materiality,
                urgency, and published_at fields.
        now: Current time (UTC). Defaults to datetime.now(timezone.utc).

    Returns:
        Float weight >= 0.1 (floor prevents any article from being fully ignored).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    source = result.get('source', 'unknown')
    source_w = _SOURCE_CREDIBILITY.get(source, 0.5)

    materiality = result.get('materiality', 'unknown')
    materiality_w = _MATERIALITY_WEIGHT.get(materiality, 0.5)

    urgency = result.get('urgency', 'standard')
    urgency_w = _URGENCY_WEIGHT.get(urgency, 1.0)

    # Recency decay
    recency_w = 1.0
    published_at = result.get('published_at')
    if published_at:
        try:
            if isinstance(published_at, str):
                # Handle ISO format with or without Z suffix
                pub_str = published_at.replace('Z', '+00:00')
                pub_time = datetime.fromisoformat(pub_str)
            elif isinstance(published_at, datetime):
                pub_time = published_at
            else:
                pub_time = None

            if pub_time is not None:
                # Ensure timezone-aware comparison
                if pub_time.tzinfo is None:
                    pub_time = pub_time.replace(tzinfo=timezone.utc)
                hours_old = max(0.0, (now - pub_time).total_seconds() / 3600.0)
                for bracket_hours, bracket_weight in _RECENCY_BRACKETS:
                    if hours_old <= bracket_hours:
                        recency_w = bracket_weight
                        break
        except (ValueError, TypeError):
            recency_w = 1.0  # Can't parse date, use baseline

    weight = source_w * materiality_w * urgency_w * recency_w
    return max(0.1, weight)


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
    """Get weighted-aggregated sentiment scores for a specific ticker.

    Each article's sentiment is weighted by:
      source credibility * materiality * urgency * recency decay

    A breaking earnings-miss from Reuters (high materiality, breaking urgency,
    published 10 minutes ago) will massively outweigh a generic industry-trend
    blog post (low materiality, standard urgency, 5 hours old).

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

    # Compute per-article weights and weighted sentiment
    now = datetime.now(timezone.utc)
    weights = [_compute_article_weight(r, now) for r in ticker_sentiments]
    scores = [r['sentiment_score'] for r in ticker_sentiments]

    total_weight = sum(weights)
    weighted_sentiment = sum(s * w for s, w in zip(scores, weights)) / total_weight

    # Count articles by source
    source_breakdown = {}
    for result in ticker_sentiments:
        source = result.get('source', 'unknown')
        source_breakdown[source] = source_breakdown.get(source, 0) + 1

    # Confidence: article count + source diversity + weight concentration
    # High total weight means high-quality, recent, material articles
    article_count = len(ticker_sentiments)
    source_count = len(source_breakdown)
    avg_weight = total_weight / article_count
    confidence = min(1.0, (article_count * 0.15) + (source_count * 0.1) + (avg_weight * 0.1))

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
        'sentiment_score': round(weighted_sentiment, 3),
        'article_count': article_count,
        'source_breakdown': source_breakdown,
        'confidence': round(confidence, 2),
        'individual_scores': scores,
        'individual_weights': [round(w, 3) for w in weights],
        'urgency': dominant_urgency,
        'materiality': max_materiality,
        'time_horizon': shortest_horizon,
    }


# ============================================================================
# SENTIMENT HISTORY TRACKING
# ============================================================================


def record_ticker_sentiment(ticker: str, sentiment_results: List[Dict]) -> Dict:
    """Aggregate sentiment for a ticker, record to DB, and compute cycle-over-cycle delta.

    This is the function the scheduler loop should call instead of
    get_ticker_sentiment_scores directly. It:
      1. Aggregates via get_ticker_sentiment_scores (pure, no DB)
      2. Fetches previous cycle's score from sentiment_history
      3. Computes delta (current - previous)
      4. Saves current score to sentiment_history
      5. Returns enriched dict with delta fields

    Args:
        ticker: Stock ticker symbol.
        sentiment_results: List of sentiment results from batch_analyze_articles.

    Returns:
        Dict from get_ticker_sentiment_scores, plus:
            sentiment_delta (float or None): change from previous cycle
            previous_score (float or None): previous cycle's score
            previous_recorded_at (str or None): when previous was recorded
            delta_direction (str): 'bullish_shift', 'bearish_shift', or 'stable'
    """
    from db.client import save_sentiment_score, get_previous_sentiment

    aggregated = get_ticker_sentiment_scores(ticker, sentiment_results)
    current_score = aggregated['sentiment_score']
    article_count = aggregated['article_count']

    # Fetch previous cycle's sentiment
    previous = get_previous_sentiment(ticker)

    if previous is not None:
        prev_score = previous['sentiment_score']
        delta = round(current_score - prev_score, 3)

        if delta > 0.1:
            direction = 'bullish_shift'
        elif delta < -0.1:
            direction = 'bearish_shift'
        else:
            direction = 'stable'

        aggregated['sentiment_delta'] = delta
        aggregated['previous_score'] = prev_score
        aggregated['previous_recorded_at'] = previous['recorded_at']
        aggregated['delta_direction'] = direction
    else:
        aggregated['sentiment_delta'] = None
        aggregated['previous_score'] = None
        aggregated['previous_recorded_at'] = None
        aggregated['delta_direction'] = 'stable'

    # Record current cycle (even if article_count is 0 — absence of news is signal)
    try:
        save_sentiment_score(ticker, current_score, article_count)
    except Exception as e:
        logger.error(f"Failed to save sentiment history for {ticker}: {e}")

    return aggregated


def batch_record_sentiments(tickers: List[str], sentiment_results: List[Dict]) -> Dict[str, Dict]:
    """Record sentiment history and compute deltas for all tickers in a cycle.

    Convenience wrapper for the scheduler loop. Calls record_ticker_sentiment
    for each ticker.

    Args:
        tickers: List of active ticker symbols for this cycle.
        sentiment_results: All sentiment results from batch_analyze_articles.

    Returns:
        Dict mapping ticker -> enriched sentiment dict (with delta fields).
    """
    ticker_sentiments = {}

    for ticker in tickers:
        try:
            ticker_sentiments[ticker] = record_ticker_sentiment(ticker, sentiment_results)
        except Exception as e:
            logger.error(f"Failed to record sentiment for {ticker}: {e}")
            # Fall back to pure aggregation without DB
            ticker_sentiments[ticker] = get_ticker_sentiment_scores(ticker, sentiment_results)

    shifts = [t for t, s in ticker_sentiments.items() if s.get('delta_direction') != 'stable']
    if shifts:
        logger.info(f"Sentiment shifts detected: {', '.join(shifts)}")

    return ticker_sentiments
