"""Claude-powered sentiment analysis engine.
 ---
  Input

  batch_analyze_articles(articles) — the full aggregator list (70+ articles).
   [
      {
          "title": "Semiconductor Tariff Could Impact Tech Giants",
          "full_text": "Full article text (1200 words max)...",  # if available
          "snippet": "Short excerpt...",  # if no full_text
          "url": "https://example.com/article",
          "published_at": "2026-04-07T13:00:00Z",
          "source": "newsapi",  # or: marketaux, alpaca, massive, polygon
          "tickers": ["AAPL", "NVDA"],
          "sentiment_score": 0.65,  # marketaux/massive only
          "topics": ["geopolitical"],  # newsapi only
          "extraction_confidence": 0.85,  # newsapi only
          "partial": False,  # True if snippet-only
          "author": "John Doe",  # alpaca/massive only
      },
      # ... 70+ more articles, sorted by published_at DESC
  ]

  ---
  Step 1 — Route each article by source (analyze_article_sentiment)

  Each article takes one of three paths based on source:

  Path A: marketaux or massive

  These have a pre-computed sentiment_score already. Now (after the refactor):
  - Extract text: full_text → description → snippet
  - Call Claude with that text to get a second-opinion score + proper urgency, materiality, time_horizon
  - Blend: final_score = (precomputed + claude_score) / 2
  - If no text available: use pre-computed score as-is, defaults for enrichment fields (urgency=standard, materiality=unknown,
  time_horizon=medium_term)
  - Returns one result per ticker (marketaux: single ticker field; massive: iterates tickers list)

  Path B: newsapi, alpaca, or polygon — WITH tickers
  - Must have full_text (skips article entirely if missing)
  - Calls analyze_newsapi_with_claude(full_text, ticker) for each ticker in tickers
  - Claude returns sentiment_score, urgency, materiality, time_horizon, reasoning
  - Text truncated to 1200 words before sending
  - If Claude returns non-JSON: _fallback_parse keyword-matches ("positive", "breaking", etc.)
  - Returns one result per ticker

  Path C: newsapi, alpaca, or polygon — NO tickers
  - Calls analyze_sector_macro_with_claude(full_text, title) instead
  - Claude gets a sector proxy map and thinks like a hedge fund manager — identifies direct sector impacts AND contrarian second-order
  trades (e.g., oil crash → buy airlines)
  - Returns multiple results tagged source="sector_macro" with lower credibility weight (0.7)

  After all articles are processed, batch_analyze_articles returns a flat list — one entry per ticker per article:

  [
      {
          "ticker": "NVDA",
          "sentiment_score": -0.685,       # blended if marketaux/massive, pure Claude otherwise
          "source": "newsapi",
          "urgency": "breaking",
          "materiality": "high", # Materiality = how much does this news actually affect the company's fundamentals? It's asking: "will this move the stock for a real business reason, or is it just noise?"
          "time_horizon": "short_term",
          "reasoning": "Semiconductor tariff directly cuts NVDA margins...",
          "published_at": "2026-04-07T13:00:00Z"
      },
      {
          "ticker": "AAPL",
          "sentiment_score": -0.6,
          "source": "newsapi",
          ...
      },
      {
          "ticker": "NVDA",                # same ticker, different article
          "sentiment_score": 0.3,
          "source": "marketaux",
          ...
      },
      # 200+ entries total across 70+ articles
  ]

  ---
  Step 2 — Aggregate per ticker (get_ticker_sentiment_scores)

  Called once per ticker. Filters the flat list for that ticker, then computes a weighted average:

  weight = source_credibility x materiality_weight x urgency_weight x recency_weight

  ┌────────────────────┬──────────────────────────────────────────────────────────────────────────┐
  │       Factor       │                                  Values                                  │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────┤
  │ Source credibility │ newsapi/alpaca/polygon=1.0, marketaux=0.8, sector_macro=0.7, massive=0.6 │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────┤
  │ Materiality        │ high=2.0x, medium=1.0x, low/unknown=0.5x                                 │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────┤
  │ Urgency            │ breaking=2.0x, developing=1.3x, standard=1.0x                            │
  ├────────────────────┼──────────────────────────────────────────────────────────────────────────┤
  │ Recency            │ <1hr=3.0x, 1-3hr=2.0x, 3-6hr=1.0x, 6+hr=0.5x                             │
  └────────────────────┴──────────────────────────────────────────────────────────────────────────┘

  weighted_sentiment = Σ(score x weight) / Σ(weights)

  So a breaking, high-materiality Reuters article from 10 minutes ago gets weight 1.0 x 2.0 x 2.0 x 3.0 = 12.0, while an old Massive
  snippet gets 0.6 x 0.5 x 1.0 x 0.5 = 0.15. The important article dominates.

  Also computes:
  - dominant_urgency: breaking wins if any article is breaking, else developing, else standard
  - max_materiality: takes the highest across all articles
  - shortest_horizon: takes the most actionable (intraday > short_term > medium_term > long_term)
  - confidence: min(1.0, article_count x 0.15 + source_count x 0.1 + avg_weight x 0.1)

  ---
  Step 3 — Record to DB + compute delta (record_ticker_sentiment)

  This is what the scheduler calls (via batch_record_sentiments). Wraps Step 2:

  1. Gets the Step 2 result for the ticker
  2. Queries sentiment_history DB table for the previous cycle's score
  3. delta = current_score - previous_score
  4. delta > +0.1 → bullish_shift, < -0.1 → bearish_shift, else stable
  5. Saves current score to DB for the next cycle to compare against

  ---
  Final Output (per ticker, what flows into strategies.py)

  {
      "ticker": "NVDA",
      "sentiment_score": -0.65,           # weighted average
      "article_count": 4,
      "source_breakdown": {"newsapi": 2, "marketaux": 1, "massive": 1},
      "confidence": 0.72,
      "individual_scores": [-0.7, -0.6, -0.685, -0.3],
      "individual_weights": [12.0, 8.4, 2.4, 0.15],
      "urgency": "breaking",              # dominant
      "materiality": "high",             # highest
      "time_horizon": "short_term",      # shortest/most actionable
      "sentiment_delta": -0.30,          # vs previous cycle
      "previous_score": -0.35,
      "delta_direction": "bearish_shift"
  }

  strategies.py uses sentiment_score to decide BUY/SELL direction, then multiplies confidence by the enrichment boosts (urgency,
  materiality, time_horizon).





"""


import os
import json
import anthropic
import logging
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger(__name__)

# Sector → (ETF, top direct holdings). Used to map ticker-less macro/sector
# articles to tradeable proxies. Kept intentionally broad — the Claude prompt
# handles the direction (including contrarian second-order trades).
_SECTOR_PROXY_MAP = {
    "energy":                 ("XLE",  ["XOM", "CVX", "COP", "SLB", "EOG"]),
    "oil":                    ("XLE",  ["XOM", "CVX", "COP", "SLB", "MPC"]),
    "natural_gas":            ("XLE",  ["EQT", "COP", "SLB"]),
    "technology":             ("XLK",  ["AAPL", "MSFT", "NVDA", "AVGO", "AMD"]),
    "semiconductors":         ("SOXX", ["NVDA", "AMD", "AVGO", "TSM", "QCOM"]),
    "financials":             ("XLF",  ["JPM", "BAC", "GS", "MS", "WFC"]),
    "banks":                  ("KBE",  ["JPM", "BAC", "WFC", "C", "GS"]),
    "healthcare":             ("XLV",  ["UNH", "JNJ", "LLY", "ABBV", "PFE"]),
    "pharma":                 ("XPH",  ["LLY", "PFE", "ABBV", "MRK", "BMY"]),
    "consumer_staples":       ("XLP",  ["PG", "KO", "WMT", "COST", "PEP"]),
    "consumer_discretionary": ("XLY",  ["AMZN", "TSLA", "HD", "MCD", "NKE"]),
    "airlines":               ("JETS", ["DAL", "UAL", "AAL", "LUV", "JBLU"]),
    "industrials":            ("XLI",  ["CAT", "DE", "HON", "UPS", "GE"]),
    "defense":                ("ITA",  ["RTX", "LMT", "NOC", "GD", "HII"]),
    "utilities":              ("XLU",  ["NEE", "DUK", "SO", "AEP", "EXC"]),
    "real_estate":            ("XLRE", ["PLD", "AMT", "EQIX", "PSA", "SPG"]),
    "materials":              ("XLB",  ["LIN", "APD", "SHW", "ECL", "NEM"]),
    "gold":                   ("GLD",  ["NEM", "GOLD", "AEM", "WPM"]),
    "communication":          ("XLC",  ["META", "GOOGL", "NFLX", "DIS", "CMCSA"]),
    "crypto":                 ("IBIT", ["COIN", "MSTR", "MARA", "RIOT"]),
    "retail":                 ("XRT",  ["AMZN", "WMT", "TGT", "COST", "TJX"]),
    "autos":                  ("CARZ", ["TSLA", "GM", "F", "STLA"]),
    "macro":                  ("SPY",  ["QQQ", "IWM", "DIA"]),
}

# Defaults for pre-computed sources that don't provide enriched metadata
_DEFAULT_ENRICHMENT = {
    'urgency': 'standard',
    'materiality': 'unknown',
    'time_horizon': 'medium_term',
}


def analyze_article_sentiment(article: Dict) -> List[Dict]:
    """Process a single article for sentiment analysis.

    Marketaux/Massive: blends pre-computed score with Claude's second-opinion score
    (average of both) and uses Claude to fill in urgency, materiality, time_horizon.
    Falls back to pre-computed score + defaults if no article text is available.

    NewsAPI/Alpaca/Polygon: send full_text to Claude for full analysis.

    NEVER send raw headlines to Claude — always use full_text/description/snippet.

    Args:
        article: Article dict from aggregator with source field.

    Returns:
        List of dicts, one per ticker mentioned in the article, each with keys:
            ticker (str),
            sentiment_score (float, -1.0 to 1.0),
            source (str),
            urgency, materiality, time_horizon (str),
            reasoning (str).
    """
    results = []
    source = article.get('source', '')

    published_at = article.get('published_at')

    if source == 'marketaux':
        ticker = article.get('ticker')
        precomputed_score = article.get('sentiment_score')

        if ticker and precomputed_score is not None:
            text = (article.get('full_text') or article.get('description')
                    or article.get('snippet') or '')

            enrichment = _DEFAULT_ENRICHMENT.copy()
            blended_score = float(precomputed_score)
            reasoning = f"Marketaux pre-computed: {precomputed_score}"

            if text:
                try:
                    claude_result = analyze_newsapi_with_claude(text, ticker)
                    if claude_result:
                        claude_score = claude_result.get('sentiment_score', float(precomputed_score))
                        blended_score = round((float(precomputed_score) + claude_score) / 2, 3)
                        enrichment = {
                            'urgency': claude_result.get('urgency', 'standard'),
                            'materiality': claude_result.get('materiality', 'unknown'),
                            'time_horizon': claude_result.get('time_horizon', 'medium_term'),
                        }
                        reasoning = (
                            f"Blended (Marketaux={precomputed_score}, Claude={claude_score:.2f}): "
                            f"{claude_result.get('reasoning', '')}"
                        )
                except Exception as e:
                    logger.warning(f"Claude second-opinion failed for marketaux ticker {ticker}: {e}")
            else:
                logger.debug(f"No text for marketaux ticker {ticker}, using pre-computed score only")

            results.append({
                'ticker': ticker,
                'sentiment_score': blended_score,
                'source': 'marketaux',
                'reasoning': reasoning,
                'published_at': published_at,
                **enrichment,
            })

    elif source == 'massive':
        tickers = article.get('tickers', [])
        precomputed_score = article.get('sentiment_score')

        if tickers and precomputed_score is not None:
            text = (article.get('full_text') or article.get('description')
                    or article.get('snippet') or '')

            for ticker in tickers:
                enrichment = _DEFAULT_ENRICHMENT.copy()
                blended_score = float(precomputed_score)
                reasoning = f"Massive pre-computed: {precomputed_score}"

                if text:
                    try:
                        claude_result = analyze_newsapi_with_claude(text, ticker)
                        if claude_result:
                            claude_score = claude_result.get('sentiment_score', float(precomputed_score))
                            blended_score = round((float(precomputed_score) + claude_score) / 2, 3)
                            enrichment = {
                                'urgency': claude_result.get('urgency', 'standard'),
                                'materiality': claude_result.get('materiality', 'unknown'),
                                'time_horizon': claude_result.get('time_horizon', 'medium_term'),
                            }
                            reasoning = (
                                f"Blended (Massive={precomputed_score}, Claude={claude_score:.2f}): "
                                f"{claude_result.get('reasoning', '')}"
                            )
                    except Exception as e:
                        logger.warning(f"Claude second-opinion failed for massive ticker {ticker}: {e}")
                else:
                    logger.debug(f"No text for massive ticker {ticker}, using pre-computed score only")

                results.append({
                    'ticker': ticker,
                    'sentiment_score': blended_score,
                    'source': 'massive',
                    'reasoning': reasoning,
                    'published_at': published_at,
                    **enrichment,
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
            # No specific tickers — try sector/macro analysis for contrarian signals
            title = article.get('title', 'Unknown')
            logger.info(f"No tickers for {source} article — running sector/macro analysis: {title[:60]}")
            sector_results = analyze_sector_macro_with_claude(full_text, title)
            # Backfill published_at
            for r in sector_results:
                r.setdefault('published_at', published_at)
            return sector_results

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


def analyze_sector_macro_with_claude(full_text: str, title: str) -> List[Dict]:
    """Analyze a ticker-less article for sector/macro sentiment and contrarian trades.

    Rather than skipping articles with no specific tickers, this extracts:
      1. Which sectors/industries are affected and how (direct sentiment)
      2. Second-order / contrarian opportunities (e.g. oil crash → buy airlines)

    Returns a list of sentiment result dicts (one per affected ticker),
    using source='sector_macro'. These flow into the normal aggregation pipeline
    with slightly lower credibility weight.
    """
    client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))

    words = full_text.split()
    truncated = ' '.join(words[:1200]) if len(words) > 1200 else full_text

    # Build the proxy ticker reference so Claude knows what's available
    sector_list = '\n'.join(
        f'  {k}: ETF={v[0]}, stocks={", ".join(v[1][:3])}'
        for k, v in _SECTOR_PROXY_MAP.items()
    )

    prompt = f"""You are a macro-aware equity trader analyzing a financial news article that doesn't name specific stocks.

Your job: identify which sectors are affected and determine the smartest trades — including CONTRARIAN ones.

Think like a hedge fund manager:
- If oil prices crash → SELL energy companies (XOM, CVX) but BUY airlines (DAL, UAL) and consumer discretionary (lower fuel costs boost margins)
- If inflation spikes → SELL high-growth tech (high discount rates) but BUY banks (higher net interest margin) and commodities
- If geopolitical tensions rise → SELL travel/airlines but BUY defense (RTX, LMT, NOC)
- If dollar strengthens → SELL multinationals (AAPL, MSFT get hurt on FX) but BUY domestic retailers
- If recession fears rise → SELL cyclicals but BUY consumer staples, utilities (defensive rotation)

Available sector proxies:
{sector_list}

Return a JSON object with this exact structure:
{{
  "sector_themes": [
    {{
      "sector": "<sector key from the list above, e.g. 'energy'>",
      "direct_sentiment": <float -1.0 to 1.0 for the sector itself>,
      "direct_tickers": ["<ticker>", ...],  // 2-3 tickers most directly affected
      "contrarian_sector": "<sector key or null>",  // sector that BENEFITS from this news
      "contrarian_sentiment": <float -1.0 to 1.0 for the contrarian trade, or null>,
      "contrarian_tickers": ["<ticker>", ...],  // 2-3 contrarian tickers, or []
      "reasoning": "<one sentence>"
    }}
  ],
  "urgency": "breaking|developing|standard",
  "materiality": "high|medium|low",
  "time_horizon": "intraday|short_term|medium_term|long_term"
}}

Return an empty sector_themes list if the article has no market-relevant content.

Article title: {title}

Article text:
{truncated}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = response.content[0].text.strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            response_text = "\n".join(lines).strip()

        data = json.loads(response_text)
        urgency = data.get('urgency', 'standard')
        materiality = data.get('materiality', 'unknown')
        time_horizon = data.get('time_horizon', 'medium_term')

        results = []
        for theme in data.get('sector_themes', []):
            # Direct sector tickers
            for ticker in theme.get('direct_tickers', []):
                score = theme.get('direct_sentiment', 0.0)
                if score != 0.0:
                    results.append({
                        'ticker': ticker,
                        'sentiment_score': max(-1.0, min(1.0, float(score))),
                        'source': 'sector_macro',
                        'urgency': urgency,
                        'materiality': materiality,
                        'time_horizon': time_horizon,
                        'reasoning': f"[Sector: {theme.get('sector')}] {theme.get('reasoning', '')}",
                    })

            # Contrarian tickers
            contrarian_score = theme.get('contrarian_sentiment')
            if contrarian_score is not None:
                for ticker in theme.get('contrarian_tickers', []):
                    results.append({
                        'ticker': ticker,
                        'sentiment_score': max(-1.0, min(1.0, float(contrarian_score))),
                        'source': 'sector_macro',
                        'urgency': urgency,
                        'materiality': materiality,
                        'time_horizon': time_horizon,
                        'reasoning': f"[Contrarian: {theme.get('contrarian_sector')}] {theme.get('reasoning', '')}",
                    })

        if results:
            logger.info(f"Sector/macro analysis generated {len(results)} ticker signals from: {title[:60]}")
        return results

    except Exception as e:
        logger.error(f"Sector/macro Claude analysis failed: {e}")
        return []


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
            model="claude-haiku-4-5-20251001",
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
    'newsapi': 1.0,       # Claude-analyzed full article text
    'alpaca': 1.0,
    'polygon': 1.0,
    'marketaux': 0.8,     # Pre-computed, ticker-specific, generally reliable
    'massive': 0.6,       # Pre-computed, broader coverage but noisier
    'sector_macro': 0.7,  # Claude sector/contrarian inference — less direct than named ticker
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

    logger.debug(f"[SENTIMENT] Recording sentiment for {ticker}")

    aggregated = get_ticker_sentiment_scores(ticker, sentiment_results)
    current_score = aggregated['sentiment_score']
    article_count = aggregated['article_count']
    logger.debug(f"[SENTIMENT] Aggregated for {ticker}: score={current_score}, articles={article_count}")

    # Fetch previous cycle's sentiment
    logger.debug(f"[SENTIMENT] Fetching previous sentiment for {ticker}")
    try:
        previous = get_previous_sentiment(ticker)
        logger.debug(f"[SENTIMENT] Previous sentiment fetch returned: {type(previous)} = {previous}")
    except Exception as e:
        logger.error(f"[SENTIMENT] get_previous_sentiment() failed for {ticker}: {type(e).__name__}: {e}")
        previous = None

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
        logger.debug(f"[SENTIMENT] Delta computed for {ticker}: {direction} ({delta:+.3f})")
    else:
        aggregated['sentiment_delta'] = None
        aggregated['previous_score'] = None
        aggregated['previous_recorded_at'] = None
        aggregated['delta_direction'] = 'stable'
        logger.debug(f"[SENTIMENT] No previous sentiment for {ticker}, delta_direction=stable")

    # Record current cycle (even if article_count is 0 — absence of news is signal)
    logger.debug(f"[SENTIMENT] Saving sentiment for {ticker}: score={current_score}, articles={article_count}")
    try:
        save_sentiment_score(ticker, current_score, article_count)
        logger.debug(f"[SENTIMENT] Successfully saved sentiment for {ticker}")
    except Exception as e:
        logger.error(f"[SENTIMENT] save_sentiment_score() failed for {ticker}: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"[SENTIMENT] Traceback: {traceback.format_exc()}")

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
    logger.info(f"[SENTIMENT] batch_record_sentiments starting for {len(tickers)} tickers")

    for idx, ticker in enumerate(tickers, 1):
        logger.debug(f"[SENTIMENT] Processing {idx}/{len(tickers)}: {ticker}")
        try:
            ticker_sentiments[ticker] = record_ticker_sentiment(ticker, sentiment_results)
            logger.debug(f"[SENTIMENT] Successfully recorded {ticker}")
        except Exception as e:
            logger.error(f"[SENTIMENT] record_ticker_sentiment() failed for {ticker}: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[SENTIMENT] Full traceback for {ticker}: {traceback.format_exc()}")
            # Fall back to pure aggregation without DB
            logger.debug(f"[SENTIMENT] Falling back to pure aggregation (no DB) for {ticker}")
            ticker_sentiments[ticker] = get_ticker_sentiment_scores(ticker, sentiment_results)

    shifts = [t for t, s in ticker_sentiments.items() if s.get('delta_direction') != 'stable']
    if shifts:
        logger.info(f"[SENTIMENT] Sentiment shifts detected: {', '.join(shifts)}")

    logger.info(f"[SENTIMENT] batch_record_sentiments complete: {len(ticker_sentiments)} tickers processed")

    return ticker_sentiments
