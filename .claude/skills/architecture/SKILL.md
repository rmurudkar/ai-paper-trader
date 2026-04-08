---
name: architecture
description: >
  Full data flow narrative for the autonomous paper trader. Load this when building,
  debugging, or modifying any module. Covers the complete pipeline from scheduler
  through feedback loop, including the 4-step waterfall enrichment, 4-stage signal
  combiner, sentiment enrichment model, and strategy category system.
  Trigger when the user says "how does X work", "implement X", "fix X", or asks
  about any module's behavior, inputs, or outputs.
---

# Architecture — Full Data Flow

## 1. Scheduler (scheduler/loop.py) — STUB
- Primary job: every 15 minutes during market hours (9:30 AM – 4:00 PM ET)
- Pre-market job: 9:00 AM ET — overnight news, sector rotation scan
- Post-market job: 4:30 PM ET — daily P&L, feedback loop
- Weekend/holiday: skip trading, run outcome measurements only
- Before every cycle: check `is_market_open()` (Alpaca calendar API) + circuit breaker

## 2. Discovery (fetchers/discovery.py) — runs FIRST every cycle

**Watchlist mode**: return WATCHLIST env var tickers only.

**Discovery mode** — 5 sources in priority order:
1. News mentions: scan Marketaux + Massive + NewsAPI for ticker symbols via regex
   ($AAPL, NASDAQ:AAPL) + optional Groq company name recognition. 2+ mentions = add ticker.
2. Market movers: fetch full S&P 500 list (Wikipedia → Turso sp500_cache → static fallback),
   `yf.download()` 2-day history, compute daily change %, top 5 gainers + top 5 losers.
3. Sector rotation (pre-market only): 20 ETFs (11 SPDR sectors + VTV, VUG, RSP, IWM,
   EEM, VEA, QQQ, IVV, VTI). Fetch holdings dynamically via yfinance (fallback: hardcoded map).
   Top 3 holdings from top 2 + bottom 2 ETFs by 1-month performance.
4. Existing positions: always include held tickers.
5. WATCHLIST pins: always included, exempt from cap.

**Validation** (before adding any ticker): price ≥ $5, market cap ≥ $1B,
avg volume ≥ 500K. Cache in `validation_cache` SQLite (7-day TTL).

**Prioritization**: score = number of sources mentioning ticker. Positions + watchlist
exempt from MAX_DISCOVERY_TICKERS cap. Others sorted by score, top N taken.

**Returns:**
```python
{
    "tickers": ["AAPL", "NVDA", ...],
    "sources": {"AAPL": ["news", "position"], "NVDA": ["news", "gainer"]},
    "mode": "discovery",
    "cycle_id": "20260405_143022"
}
```

## 3. News Fetching

**fetchers/marketaux.py**
- Discovery mode: broad fetch, no ticker filter
- Watchlist mode: filter to watchlist tickers
- Returns pre-scored sentiment — DO NOT re-analyze with Claude
- Output shape: `{title, ticker, sentiment_score, snippet, description, url, published_at, source:"marketaux"}`

**fetchers/massive.py**
- Same pattern as Marketaux, optional (requires MASSIVE_API_KEY)
- Sentiment from `insights` array: positive→0.7, negative→-0.7, neutral→0.0
- DO NOT re-analyze with Claude
- Output: `{title, description, tickers, sentiment_score, url, published_at, source:"massive"}`

**fetchers/newsapi.py**
- Topics: macro (inflation), geopolitical (tariff), economic (recession), market (earnings), energy (oil)
- Discovery mode: extract tickers via regex + optional Groq from all articles
- Watchlist mode: filter to watchlist-relevant articles (broad topics always pass through)
- All articles flagged `needs_full_text: True` for waterfall enrichment
- Output: `{title, snippet, topics, url, published_at, source:"newsapi", tickers, extraction_confidence, needs_full_text:true}`

**fetchers/alpaca_news.py**
- Benzinga feed via Alpaca News API
- Strips HTML, truncates to 1200 words
- Used as standalone source AND as Step 2 of waterfall
- Output: `{title, full_text, ticker, tickers, url, published_at, source:"alpaca", author, partial}`

**fetchers/polygon.py**
- Licensed full-text lookup — Step 1 of waterfall only
- Matches NewsAPI URLs against Polygon feed
- Rate limited: 5 req/min, enforces ≥13s between requests
- Returns: `{full_text, publisher, published_at, tickers, url, partial:false}` or None

**fetchers/scraper.py** — FALLBACK ONLY, called by aggregator
- Tier 1: trafilatura.fetch_url() + trafilatura.extract()
- Tier 2: newspaper3k Article().parse()
- Tier 3: BeautifulSoup raw text
- Paywalled domains → snippet only, partial=True (never attempt: wsj.com, ft.com,
  bloomberg.com, nytimes.com, reuters.com, barrons.com, marketwatch.com)
- Returns: `{full_text, partial, extraction_method, word_count, extraction_time_ms}`

## 4. Aggregator (fetchers/aggregator.py)

**4-step waterfall** for each NewsAPI article with `needs_full_text:True`:
1. Polygon full-text lookup by URL → enrich if found
2. Alpaca News match by URL or title similarity >80% Jaccard → enrich if found
3. scraper.scrape(url) → enrich if found and not partial
4. Snippet only → mark `partial:True`

**Merge** all 5 sources: Marketaux + Massive + NewsAPI (enriched) + Alpaca + Polygon feed.

**Dedup**: exact URL match first, then title Jaccard similarity >80%. When duplicates,
keep article with full_text over snippet-only.

Returns: unified list sorted by published_at descending.

## 5. Sentiment Engine (engine/sentiment.py)

**Routing:**
- source == "marketaux" OR "massive" → pass sentiment_score directly, skip Claude
- source == "newsapi" / "alpaca" / "polygon" → send full_text to Claude
- Never send headlines to Claude, never send text >1200 words

**Claude call** uses `claude-3-haiku-20240307`, temperature=0.1, max_tokens=400.

**Enriched output per article per ticker:**
```python
{
    "sentiment_score": float,   # -1.0 to 1.0, clamped
    "urgency": str,             # "breaking" | "developing" | "standard"
    "materiality": str,         # "high" | "medium" | "low" | "unknown"
    "time_horizon": str,        # "intraday" | "short_term" | "medium_term" | "long_term"
    "reasoning": str,
    "published_at": str,
    "source": str
}
```

**Weighted aggregation** per ticker (get_ticker_sentiment_scores):

| Factor | Values |
|--------|--------|
| Source credibility | newsapi/alpaca/polygon=1.0, marketaux=0.8, massive=0.6 |
| Materiality | high=2.0×, medium=1.0×, low=0.5×, unknown=0.5× |
| Urgency | breaking=2.0×, developing=1.3×, standard=1.0× |
| Recency | <1hr=3.0×, 1-3hr=2.0×, 3-6hr=1.0×, 6+hr=0.5× |

Weighted average = Σ(score × weight) / Σ(weight). Floor: weight ≥ 0.1.

**Sentiment history tracking** (record_ticker_sentiment):
1. Aggregate via get_ticker_sentiment_scores
2. Fetch previous cycle score from `sentiment_history` table
3. Compute delta = current - previous
4. delta > +0.1 = bullish_shift, < -0.1 = bearish_shift, else stable
5. Save current score to `sentiment_history`

**Enriched aggregated output** adds to base aggregation:
- dominant urgency (breaking > developing > standard)
- highest materiality (high > medium > low > unknown)
- shortest time_horizon (intraday > short_term > medium_term > long_term)
- sentiment_delta, previous_score, delta_direction

## 6. Strategies (engine/strategies.py)

Generates raw unweighted signals and modifiers. No regime logic here — that's combiner.py.

**Category 1 — Sentiment-Reactive (primary signals):**

`sentiment_price_divergence`
- BUY: sentiment > +0.5 AND price_change_pct < +0.5%
- SELL: sentiment < -0.5 AND price_change_pct > -0.5%
- confidence = abs(sentiment) × (1 - abs(price_change) / 5.0)
- Boosts: 3+ articles ×1.1, 2+ sources ×1.05

`multi_source_consensus`
- Requires: article_count ≥ 3, source count ≥ 2, ALL individual_scores same direction
  (all > +0.3 for BUY or all < -0.3 for SELL)
- confidence = min(article_count / 5.0, 1.0) × avg_abs_score

`sentiment_momentum`
- Reads `sentiment_history` table via get_previous_sentiment(ticker)
- BUY: delta > +0.4 since last cycle
- SELL: delta < -0.4 since last cycle
- confidence = min(abs(delta) / 1.0, 0.95)

**Category 2 — Technical Confirmation (modifiers only, never standalone signals):**

`volume_confirmation` → multiplier
- volume > 2× avg_volume_20: 1.4
- volume > 1.5× avg_volume_20: 1.2
- volume < 0.7× avg_volume_20: 0.6
- otherwise: 1.0

`vwap_position` → directional_modifier
- price > vwap by ≥1%: positive modifier (capped +0.2)
- price < vwap by ≥1%: negative modifier (capped -0.2)
- near vwap: 0.0
- Modifier formula: deviation_pct × 0.05

`relative_strength` → directional_modifier
- spread = ticker_change_pct - spy_change_pct
- abs(spread) < 1% → 0.0
- otherwise: spread × 0.05, capped ±0.2

**Category 3 — Post-News Drift:**

`news_catalyst_drift`
- Requires: gap = (price - prev_close) / prev_close × 100
- BUY: gap > +2% AND (day_high - price) / day_high ≤ 1% (near high, drift intact)
- SELL: gap < -2% AND (price - day_low) / day_low ≤ 1% (near low, drift intact)
- confidence = gap_factor × 0.6 + proximity_factor × 0.3 (capped 0.85)

**Standalone technical (no sentiment required):**

`momentum_signal`
- BUY: price > ma_20 > ma_50
- SELL: price < ma_20 < ma_50
- confidence = max(0.3, min(0.9, 0.3 + trend_strength × 12)) where trend_strength = abs(price - ma_20) / price

`mean_reversion_signal`
- BUY: rsi < 30 AND price_change_pct > 0
- SELL: rsi > 70 AND price_change_pct < 0
- confidence = max(0.3, min(0.7, 0.3 + rsi_extremity / 25))

**Enrichment boost** (applied in all Cat 1 + Cat 3 strategies):
```python
urgency:       breaking=1.25×, developing=1.1×, standard=1.0×
materiality:   high=1.2×,      medium=1.1×,     low/unknown=1.0×
time_horizon:  intraday=1.15×, short_term=1.05×, medium_term=1.0×, long_term=0.9×
```

run_all_strategies returns:
```python
{
    "signals": [  # non-HOLD only; Cat 3 included here
        {"signal": "BUY"|"SELL", "confidence": float, "strategy": str, "reason": str}
    ],
    "modifiers": [  # Cat 2 only
        {"multiplier"|"directional_modifier": float, "modifier_name": str, "reason": str}
    ]
}
```

## 7. Regime Classifier (engine/regime.py)

Weighted indicator scoring:
- VIX (35%): <20 → +0.35, >25 → -0.35, between → 0
- SPY vs 200MA (30%): >+2% → +0.30, <-2% → -0.30, between → 0
- Yield spread 10yr-2yr (25%): >0.5% → +0.25, <-0.5% → -0.25, between → 0
- Macro news sentiment (10% base): if abs(news_score) > 0.3, add ±0.10
  If abs(news_score) > 0.6, add additional ±0.15

Final score > 0.3 → risk_on. Score < -0.3 → risk_off. Else → neutral.
Confidence = min(1.0, abs(score) / 0.5), floor 0.1.

## 8. Signal Combiner (engine/combiner.py) — 4-Stage Pipeline

**Stage 1 — Primary Direction:**
- Separate Cat 1 + standalone signals from Cat 3 signals
- Apply learned weights from `weights` table (default 0.5 if not found)
- Group by direction: BUY signals vs SELL signals
- Weighted totals: buy_total = Σ(confidence × weight) for BUY signals
- Conflict penalty when both directions exist:
  `base_confidence *= (1.0 - conflict_ratio × 0.3)` where conflict_ratio = losing / (winning + losing)
- Pick direction with higher weighted total (BUY wins ties)
- If no signals → HOLD

**Stage 2 — Apply Technical Modifiers:**
- For each Cat 2 modifier:
  - multiplier: confidence *= multiplier
  - directional_modifier: if BUY → confidence *= (1 + dm); if SELL → confidence *= (1 - dm)
- No regime scaling at this stage

**Stage 3 — Catalyst Drift Integration:**
- For each Cat 3 signal (news_catalyst_drift):
  - Agrees with direction: confidence += cat3_confidence × 0.20
  - Contradicts direction: confidence -= cat3_confidence × 0.15
  - HOLD cat3: no effect

**Stage 4 — Regime Filter:**
- risk_on + SELL: confidence × 0.80
- risk_off + BUY: confidence × 0.70
  - Then gate: if confidence < 0.80 after dampening → HOLD (killed)
- neutral: no change

**Final gate:** confidence clamped to [0.05, 0.95]. If confidence ≤ 0.55 → HOLD.

## 9. Risk Manager (risk/manager.py)

**Position sizing:**
```
risk_budget   = equity × 0.02
stop_distance = price × 0.03
base_shares   = risk_budget / stop_distance
shares        = int(base_shares × confidence_factor × regime_factor × sector_factor)
shares        = min(shares, 500)   # hard cap
```
Where:
- confidence_factor = max(0.5, min(1.0, confidence))
- regime_factor = 0.75 if regime == "risk_off", else 1.0
- sector_factor = 0.50 if sector_pct ≥ 0.20 (sector overweight threshold), else 1.0

**Stop/TP placement:**
- BUY: stop_loss = price × 0.97, take_profit = price × 1.03
- SELL: stop_loss = price × 1.03, take_profit = price × 0.97

**7 hard rules checked in order:**
1. price ≥ $5
2. market_cap ≥ $1B (skip check if data unavailable — don't reject on missing data)
3. len(open_positions) < 15 (exempt if ticker already held)
4. cash > equity × 0.20 (BUY only)
5. current_ticker_value < equity × 0.10
6. sector_exposure < equity × 0.30
7. no same-direction trade on same ticker within 2 hours (checks `trades` table)

Sector lookup: `sector_cache` table → yfinance if miss → cache result.

## 10. Executor (executor/alpaca.py)
- Uses `TradingClient` with `paper=True`
- Checks `client.get_clock().is_open` before every order
- Submits `MarketOrderRequest` with `TimeInForce.DAY`
- Returns dict with order_id, symbol, side, qty, status, filled_avg_price
- On failure: returns `{error, ticker}`, no retry within cycle

## 11. Feedback Loop

**feedback/logger.py** — log_trade(trade_data) → writes to `trades` table, returns UUID.

**feedback/outcomes.py** — measure_outcomes():
- Queries trades with no matching row in `outcomes` table
- For each: fetch current price via yfinance
- Exit conditions (first hit): stop_loss hit, take_profit hit, age ≥ 8 hours
- WIN: return > +1%, LOSS: return < -1%, NEUTRAL: between
- Writes to `outcomes` table, then calls update_weights()

**feedback/weights.py:**

Weight update (EMA):
```python
target = 1.0 if WIN else 0.0
new_weight = old_weight * 0.95 + target * 0.05
new_weight = max(0.1, min(1.0, new_weight))
```
Updates: all strategies in strategies_fired + sentiment_source. NEUTRAL → no update.

Circuit breaker:
- Rolling 7-day win rate = WIN / (WIN + LOSS), excludes NEUTRAL
- Trips if: win_rate < 0.40 AND trade_count ≥ 10 in window
- On trip: writes to `circuit_breaker` table, sends Slack/email
- Already tripped → skip re-trip check
- Manual reset via dashboard

## 12. Dashboard (dashboard/app.py)
All data from Turso. Cache TTL: 30s for portfolio/positions, 60s for trades/weights.

Tabs: Positions, Trade History, Performance, Signals & Regime, Discovery, Risk Controls, Settings.

Key panels:
- Portfolio KPIs: equity, cash (with % of portfolio), buying power, position count vs 15 max, unrealized P&L
- Sector exposure: pie chart from sector_cache lookup on open positions
- Learned weights: bar charts for strategy weights and source weights
- Win rate chart: rolling daily win rate with 40% threshold line (red dashed)
- Circuit breaker: status + manual reset button + 7-day win rate metric
- Discovery panel: latest cycle_id, tickers discovered, source per ticker from discovery_log
- Settings: current env vars, API key status (configured/not set)
```

