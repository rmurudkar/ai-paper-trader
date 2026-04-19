# Autonomous Paper Trading App

Python app that runs continuously, fetches financial news, analyzes sentiment with
Claude API, combines with technical strategies, enforces risk rules, executes paper
trades via Alpaca, and learns from its own performance. Paper trading only — no real money.

## Stack
- Python 3.11+, Anthropic SDK, APScheduler, Streamlit + Plotly
- Alpaca (`alpaca-trade-api` + `alpaca-py`) — paper trading + Benzinga news feed
- News: Marketaux API, Massive API, NewsAPI.ai (EventRegistry), Alpaca News, Polygon.io
- NLP: Groq Llama 3.1 8B (optional, company name → ticker extraction)
- Market data: yfinance (prices, MAs, RSI, VIX, yield spread, S&P 500 list)
- Scraping: trafilatura → newspaper3k → BeautifulSoup4 (waterfall fallback)
- DB: Turso (distributed SQLite) via libsql-client

## Project Structure
```
paper-trader/
├── scheduler/loop.py            # APScheduler event loop (STUB)
├── fetchers/
│   ├── discovery.py             # Ticker discovery engine — runs FIRST every cycle
│   ├── groq_client.py           # Groq company name → ticker extraction (optional)
│   ├── marketaux.py             # Pre-scored sentiment news
│   ├── massive.py               # Pre-scored sentiment news
│   ├── newsapi.py               # Macro/geopolitical headlines
│   ├── alpaca_news.py           # Benzinga feed via Alpaca
│   ├── polygon.py               # Licensed full-text enrichment (waterfall step 1)
│   ├── scraper.py               # Web scraper (waterfall step 3)
│   ├── market.py                # yfinance price/volume/MA/RSI/macro + S&P 500
│   └── aggregator.py            # 4-step waterfall + dedup + merge all 5 sources
├── engine/
│   ├── sentiment.py             # Claude sentiment with urgency/materiality/time_horizon
│   ├── strategies.py            # Cat 1/2/3 strategies + standalone technical
│   ├── regime.py                # Macro regime: risk_on / neutral / risk_off
│   └── combiner.py              # 4-stage weighted signal combiner
├── risk/manager.py              # Position sizing + 7 hard rules
├── executor/alpaca.py           # Alpaca paper trade executor
├── feedback/
│   ├── logger.py                # Trade logging to Turso
│   ├── outcomes.py              # Outcome measurement (every 4hrs + market close)
│   └── weights.py               # EMA weight updates + circuit breaker
├── dashboard/app.py             # Streamlit UI (reads from Turso)
├── db/
│   ├── client.py                # Turso connection + all query helpers
│   └── schema.sql               # 8 tables — see .claude/skills/schema/SKILL.md
├── tests/
│   ├── conftest.py
│   ├── fetchers/
│   ├── test_combiner.py
│   ├── test_feedback.py
│   ├── test_risk_manager.py
│   ├── test_sentiment.py
│   └── test_strategies.py
└── .claude/skills/
    ├── architecture/SKILL.md    # Full data flow, module contracts, return shapes
    ├── schema/SKILL.md          # Full SQL schema with column docs
    └── api-contracts/SKILL.md   # Exact return shapes for every module
```

## Environment Variables
```
ANTHROPIC_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
MARKETAUX_API_KEY=
NEWSAPI_KEY=
MASSIVE_API_KEY=               # optional
POLYGON_API_TOKEN=             # optional
GROQ_API_KEY=                  # optional
TURSO_CONNECTION_URL=libsql://your-database-name.turso.io
TURSO_AUTH_TOKEN=
TICKER_MODE=discovery          # "watchlist" | "discovery"
WATCHLIST=AAPL,MSFT,NVDA       # always-include list in both modes
MAX_DISCOVERY_TICKERS=30
ALERT_EMAIL=                   # optional
SLACK_WEBHOOK_URL=             # optional
```

## Implementation Status
- **Complete**: all fetchers, risk/manager.py, executor/alpaca.py,
  all feedback modules, dashboard/app.py, db/client.py, db/schema.sql
- **Stub**: scheduler/loop.py
- **Deleted (thesis redesign)**: engine/signals.py, engine/sentiment.py, engine/strategies.py, engine/combiner.py
- **To implement**: engine/analysis.py, engine/thesis_extractor.py, engine/thesis_lifecycle.py, materiality_classifier.py, new strategies.py, new combiner.py

## Hard Rules — Never Violate These
- NEVER send raw headlines to Claude for sentiment — always use `full_text`
- NEVER re-analyze Marketaux or Massive sentiment — it is pre-computed and trusted
- NEVER scrape: wsj.com, ft.com, bloomberg.com, nytimes.com, reuters.com, barrons.com, marketwatch.com
- NEVER use the live Alpaca URL (`api.alpaca.markets`) — paper only
- NEVER hardcode ticker lists outside discovery.py — all modules receive tickers dynamically
- NEVER bypass risk/manager.py — every signal must be approved before execution
- NEVER put >10% of portfolio in a single ticker
- NEVER allow total invested >80% of portfolio (keep 20% cash reserve)
- NEVER trade penny stocks (price < $5) or micro-caps (market cap < $1B)
- NEVER exceed 30% portfolio allocation in a single sector
- NEVER import deleted modules (signals.py, sentiment.py, old strategies.py, old combiner.py)
- ALWAYS run discovery.py as the FIRST step in every trading cycle
- ALWAYS truncate article text to 1200 words before any Claude or Groq call
- ALWAYS check market hours + circuit breaker before submitting orders
- ALWAYS log full signal metadata on every trade
- ALWAYS cache sector lookups in Turso `sector_cache` after yfinance fetch
- Cat 2 strategies (volume, VWAP, relative strength) are MODIFIERS only — never standalone signals
- Groq extraction is OPTIONAL — always combine with regex, degrade gracefully without GROQ_API_KEY

## Ticker Modes
**watchlist** — WATCHLIST env var tickers only. Lower API usage, predictable.

**discovery** (default) — Finds tickers from: news mentions, S&P 500 market movers
(dynamically fetched), sector rotation across 20 ETFs, existing positions, WATCHLIST pins.
Validates each ticker: price ≥ $5, market cap ≥ $1B, avg volume ≥ 500K.
Capped at MAX_DISCOVERY_TICKERS. Positions and watchlist tickers exempt from cap.

## When to Load Skills
- Building, debugging, or modifying any module → load `.claude/skills/architecture/SKILL.md`
- Writing DB queries, migrations, or new tables → load `.claude/skills/schema/SKILL.md`
- Wiring modules together or checking return shapes → load `.claude/skills/api-contracts/SKILL.md`
```

---

**.claude/skills/architecture/SKILL.md**

```markdown
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

- Thesis circuit breaker: trips if thesis win rate < 0.35 over 14 days
- Sentiment circuit breaker: trips if sentiment win rate < 0.40 over 7 days
- **Materiality circuit breaker**: if high-materiality classification accuracy < 0.60, 
  disable thesis extraction, fall back to sentiment-only mode

**Learning outputs**:
- Strategy weights → `weights` table
- Thesis theme weights → `thesis_weights` table  
- Lifecycle timing → `lifecycle_weights` table
- Materiality accuracy → `materiality_performance` table

## 14. Enhanced Thesis Dashboard (dashboard/app.py)
All data from Turso. Cache TTL: 30s for portfolio/positions, 60s for trades/weights, 120s for thesis data.

**New Tabs**: Positions, Trade History, **Thesis Tracker**, Performance, Signals & Regime, Discovery, Risk Controls, Settings.

**Enhanced panels:**

**Portfolio KPIs** (unchanged):
- equity, cash (%), buying power, position count vs 15 max, unrealized P&L

**Thesis Tracker** (NEW):
- **Active Theses Table**: thesis_statement, lifecycle_stage, conviction_score, implied_tickers, evidence_count, days_active
- **Lifecycle Distribution**: pie chart of theses by stage (EMERGING/DEVELOPING/CONFIRMED/CONSENSUS)
- **Thesis Performance**: win rate by theme, by lifecycle stage, by conviction level
- **Recent Evidence**: latest supporting articles per thesis with conviction contribution

**Enhanced Performance**:
- **Dual win rate charts**: thesis trades vs sentiment trades (separate 7d/14d windows)
- **Attribution pie**: trade P&L by source (thesis vs sentiment vs technical)
- **Materiality accuracy**: classification accuracy vs actual trade impact
- **Lifecycle timing**: performance by entry stage (bar chart)

**Enhanced Learned Weights**:
- **Strategy weights**: existing bar charts
- **Thesis theme weights**: AI, earnings, regulatory, M&A, etc. (bar chart)
- **Lifecycle weights**: EMERGING vs DEVELOPING vs CONFIRMED entry performance
- **Source credibility**: learned weights for news sources

**Enhanced Circuit Breaker**:
- **Multi-breaker status**: thesis breaker, sentiment breaker, materiality breaker
- **Thesis health**: 14-day thesis win rate with 35% threshold
- **Sentiment health**: 7-day sentiment win rate with 40% threshold  
- **Materiality health**: classification accuracy with 60% threshold

**Enhanced Discovery**:
- **Source breakdown**: news vs movers vs **theses** vs positions vs watchlist
- **Thesis implications**: which active theses drove ticker discovery
- **Discovery accuracy**: how many discovered tickers became trades

**New Risk Controls**:
- **Thesis concentration**: % exposure by theme, lifecycle stage
- **Thesis lifecycle alerts**: theses approaching CONSENSUS (exit warnings)
- **Stale thesis monitor**: theses with no evidence >4 days (pre-expiry warning)
```



Updated Architecture:

┌─────────────────────────────────────────────────────────────────────┐
│                         SCHEDULER/LOOP.PY                          │
│                    (15min cycles, market hours)                     │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DISCOVERY.PY                                │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │   News Mentions │  │   Market Movers │  │  Active Theses      │  │
│  │   (regex/Groq)  │  │   (S&P gainers) │  │  (implied tickers)  │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │
│             │                   │                   ▲               │
│             └───────────────────┼───────────────────┘               │
│                                 ▼                                   │
│           ┌─────────────────────────────────────────┐               │
│           │        UNIFIED TICKER LIST              │               │
│           │     (validated, prioritized, capped)    │               │
│           └─────────────────────────────────────────┘               │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      NEWS PIPELINE                                  │
│                                                                     │
│  marketaux.py  massive.py  newsapi.py  alpaca_news.py  polygon.py   │
│      │             │           │            │            │          │
│      └─────────────┼───────────┼────────────┼────────────┘          │
│                    ▼           ▼            ▼                       │
│              ┌─────────────────────────────────────┐                │
│              │         AGGREGATOR.PY               │                │
│              │   (4-step waterfall + dedup)        │                │
│              └─────────────────┬───────────────────┘                │
└──────────────────────────────────┼──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  MATERIALITY FILTER (NEW)                           │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │              materiality_classifier.py                          ││
│  │                                                                 ││
│  │  Stage 1: RULES-BASED HIGH DETECTION                            ││
│  │  ┌─────────────────────────────────────────────────────────────┐││
│  │  │ Keywords: earnings, guidance, CEO, merger, FDA, regulatory  │││
│  │  │ Multi-ticker: >= 3 tickers → medium                        │ ││
│  │  │ Title patterns: "announces", "reports", "files"            │ ││
│  │  └─────────────────────────────────────────────────────────────┘││
│  │                              │                                  ││
│  │  Stage 2: SOURCE-BASED BOOST │                                  ││
│  │  ┌─────────────────────────────▼─────────────────────────────── ││
│  │  │ Premium sources: WSJ, Bloomberg, Reuters → upgrade to medium│ │ │
│  │  │ Pre-scored: Marketaux, Massive → medium                    │ │ │
│  │  │ Institutional: Alpaca/Benzinga, Polygon → high             │ │ │
│  │  └─────────────────────────────┬─────────────────────────────────┘ │ │
│  │                              │                                  │ │
│  │  Stage 3: CLAUDE REFINEMENT (edge cases only)                  │ │
│  │  ┌─────────────────────────────▼─────────────────────────────────┐ │ │
│  │  │ IF rules_score == "unknown" OR borderline cases            │ │ │
│  │  │ → quick Claude call: "medium vs low materiality?"          │ │ │
│  │  │ ELSE: skip Claude (cost control)                           │ │ │
│  │  └─────────────────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                   │                                  │
│  OUTPUT: articles tagged with materiality → route to analysis       │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 COMPREHENSIVE ANALYSIS                              │
│              (REPLACES sentiment.py)                                │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │              analysis.py  (router/orchestrator)                 │ │
│  │                                                                 │ │
│  │  ┌─────────────────────┐         ┌─────────────────────────────┐ │ │
│  │  │  HIGH MATERIALITY   │         │   MEDIUM/LOW MATERIALITY    │ │ │
│  │  │                     │         │                             │ │ │
│  │  │ FULL CLAUDE CALL:   │         │ BASIC ANALYSIS:             │ │ │
│  │  │ • Thesis extraction │         │ • Simple ticker sentiment   │ │ │
│  │  │ • Theme/mechanism   │         │ • Direction + confidence    │ │ │
│  │  │ • Implied tickers   │         │ • Use pre-scored if avail   │ │ │
│  │  │ • Direct sentiment  │         │   (Marketaux/Massive)       │ │ │
│  │  │ • Time horizon      │         │                             │ │ │
│  │  └─────────────────────┘         └─────────────────────────────┘ │ │
│  │             │                                │                   │ │
│  └─────────────┼────────────────────────────────┼───────────────────┘ │
│                │                                │                     │
└────────────────┼────────────────────────────────┼─────────────────────┘
                 │                                │
                 ▼                                │
┌─────────────────────────────────────────────────┼─────────────────────┐
│                THESIS MANAGEMENT                │                     │
│                                                 │                     │
│  ┌─────────────────────────────────────────────┐│                     │
│  │            thesis_lifecycle.py              ││                     │
│  │                                             ││                     │
│  │ 1. Match against existing active_theses:    ││                     │
│  │    • Theme similarity (keywords)            ││                     │
│  │    • Ticker overlap                         ││                     │
│  │    • Mechanism similarity                   ││                     │
│  │                                             ││                     │
│  │ 2. IF MATCH FOUND:                          ││                     │
│  │    • Add to thesis_evidence table           ││                     │
│  │    • Update conviction_score                ││                     │
│  │    • Evolve thesis_statement                ││                     │
│  │    • Check lifecycle transition:            ││                     │
│  │      emerging→developing→confirmed→consensus ││                     │
│  │                                             ││                     │
│  │ 3. IF NO MATCH:                             ││                     │
│  │    • Create new thesis (EMERGING state)     ││                     │
│  │                                             ││                     │
│  │ 4. Expire old theses (no evidence >5 days)  ││                     │
│  └─────────────────────────────────────────────┘│                     │
│                              │                  │                     │
└──────────────────────────────┼──────────────────┼─────────────────────┘
                               │                  │
                               ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 ENHANCED DATABASE                                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    active_theses                                │ │
│  │ • id, thesis_statement, theme, direction, mechanism             │ │
│  │ • lifecycle_stage, confidence_score, conviction_score           │ │
│  │ • tickers (JSON), sectors (JSON), time_horizon                  │ │
│  │ • created_at, last_updated, expires_at                          │ │
│  │ • thesis_history (JSON), evidence_count, source_diversity       │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                  thesis_evidence                                │ │
│  │ • thesis_id, article_url, source, published_at                 │ │
│  │ • added_conviction, reasoning, materiality                      │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ sentiment_scores (for non-thesis articles)                     │ │
│  │ • ticker, sentiment_score, urgency, materiality, reasoning     │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  THESIS-DRIVEN STRATEGIES                          │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                  strategies.py (REWRITTEN)                     │ │
│  │                                                                 │ │
│  │ TIER 1 - THESIS LIFECYCLE STRATEGIES:                          │ │
│  │ ┌─────────────┐┌─────────────┐┌─────────────┐┌─────────────────┐ │ │
│  │ │first_mover  ││conviction   ││thesis       ││thesis_fade      │ │ │
│  │ │_thesis      ││_builder     ││_momentum    ││_exit            │ │ │
│  │ │             ││             ││             ││                 │ │ │
│  │ │EMERGING     ││DEVELOPING   ││Accelerating ││CONSENSUS        │ │ │
│  │ │1-2 articles ││3+ articles  ││conviction   ││/EXPIRED         │ │ │
│  │ │High risk/   ││2+ sources   ││growing      ││Take profit      │ │ │
│  │ │reward       ││Normal size  ││Add position ││Exit signal      │ │ │
│  │ └─────────────┘└─────────────┘└─────────────┘└─────────────────┘ │ │
│  │                                                                 │ │
│  │ TIER 2 - SENTIMENT FALLBACK:                                   │ │
│  │ ┌─────────────┐┌─────────────┐┌─────────────┐                  │ │
│  │ │sentiment    ││multi_source ││sentiment    │                  │ │
│  │ │_divergence  ││_consensus   ││_momentum    │                  │ │
│  │ │(enhanced)   ││(fallback)   ││(fallback)   │                  │ │
│  │ │             ││             ││             │                  │ │
│  │ │For non-     ││Direct ticker││Historical   │                  │ │ │
│  │ │thesis tickers││sentiment    ││sentiment    │                  │ │
│  │ └─────────────┘└─────────────┘└─────────────┘                  │ │
│  │                                                                 │ │
│  │ TIER 3 - TECHNICAL TIMING:                                     │ │
│  │ ┌─────────────┐┌─────────────┐┌─────────────┐                  │ │
│  │ │volume       ││vwap         ││relative     │                  │ │
│  │ │_confirmation││_position    ││_strength    │                  │ │
│  │ │"act now?"   ││"act now?"   ││"act now?"   │                  │ │
│  │ └─────────────┘└─────────────┘└─────────────┘                  │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   THESIS-FIRST COMBINER                            │
│                                                                     │
│  Stage 1: THESIS SIGNALS VOTE                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ For each ticker:                                                │ │
│  │ • Find active theses implicating this ticker                   │ │
│  │ • Weight by lifecycle: emerging=0.6, developing=1.0,           │ │
│  │   confirmed=0.8, consensus=0.3                                 │ │
│  │ • Weight by conviction_score                                    │ │
│  │ • Resolve thesis conflicts (opposing directions)               │ │
│  └─────────────────────┬───────────────────────────────────────────┘ │
│                        │                                            │
│  Stage 2: SENTIMENT CONFIRMATION                                   │
│  ┌─────────────────────▼───────────────────────────────────────────┐ │
│  │ • Direct sentiment signals confirm/contradict thesis direction │ │
│  │ • Agreement: boost confidence                                   │ │
│  │ • Disagreement: dampen confidence                              │ │
│  │ • No thesis + strong sentiment: fallback signal                │ │
│  └─────────────────────┬───────────────────────────────────────────┘ │
│                        │                                            │
│  Stage 3: TECHNICAL TIMING                                         │
│  ┌─────────────────────▼───────────────────────────────────────────┐ │
│  │ • Volume surge on thesis ticker → accelerate                   │ │
│  │ • RSI extreme → delay entry                                     │ │
│  │ • VWAP position → fine-tune timing                              │ │
│  └─────────────────────┬───────────────────────────────────────────┘ │
│                        │                                            │
│  Stage 4: REGIME-THESIS INTERACTION                                │
│  ┌─────────────────────▼───────────────────────────────────────────┐ │
│  │ • Risk-off + growth thesis → heavy dampen/kill                 │ │
│  │ • Risk-off + defensive thesis → boost                          │ │
│  │ • Risk-on + any thesis → normal processing                     │ │
│  │ • Select which thesis categories are actionable                │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│              RISK/MANAGER.PY (enhanced)                             │
│                                                                     │
│ Position sizing now considers thesis lifecycle:                     │
│ • EMERGING thesis → 0.5x normal size (high risk)                   │
│ • DEVELOPING thesis → 1.0x normal size                             │
│ • CONFIRMED thesis → 1.2x normal size                              │ │
│ • CONSENSUS thesis → 0.3x normal size (exit signal)                │ │
│                                                                     │
│ Same 7 hard rules, but thesis-aware stops:                         │
│ • Thesis-driven trades → wider stops (thesis may take time)        │
│ • Sentiment-only trades → tighter stops (quicker moves)            │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                EXECUTOR/ALPACA.PY                                   │
│                   (unchanged)                                       │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                THESIS-AWARE FEEDBACK                                │
│                                                                     │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────────┐ │
│  │  LOGGER.PY      │  │   OUTCOMES.PY    │  │    WEIGHTS.PY       │ │
│  │  (enhanced)     │  │   (enhanced)     │  │    (enhanced)       │ │
│  │                 │  │                  │  │                     │ │
│  │ Log trades with:│  │ Dual measurement │  │ Update weights for: │ │
│  │ • thesis_id     │  │ windows:         │  │ • Strategy weights  │ │
│  │ • lifecycle_    │  │ • 8hr for        │  │ • THESIS weights    │ │
│  │   stage         │  │   sentiment      │  │   (by theme)        │ │
│  │ • signal_       │  │ • 3-5 days for   │  │ • Lifecycle stage   │ │
│  │   attribution   │  │   thesis trades  │  │   weights           │ │
│  │                 │  │                  │  │ • Materiality       │ │
│  │                 │  │ Attribution to   │  │   classification    │ │
│  │                 │  │ thesis vs        │  │   accuracy          │ │
│  │                 │  │ sentiment        │  │                     │ │
│  └─────────────────┘  └──────────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
