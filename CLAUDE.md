# Autonomous Paper Trading App

Python app that runs continuously, fetches financial and macroeconomic news, scrapes
full article content, analyzes sentiment with Claude API, combines sentiment with
technical trading strategies, enforces risk management rules, executes paper trades
via Alpaca, and learns from its own performance over time.

Uses paper trading only — no real money trades.

## Stack
- Python 3.11+
- Anthropic SDK (sentiment engine + macro regime classification)
- Alpaca SDK (paper trading + portfolio state)
- Marketaux API (stock-tagged financial news + pre-built sentiment)
- NewsAPI.ai (macro, geopolitical, economic news)
- yfinance (price, volume, moving averages, VIX, yield data)
- trafilatura (primary article scraper)
- newspaper3k (fallback article scraper)
- BeautifulSoup4 (fallback for unparseable sites)
- APScheduler (continuous scheduler / event loop)
- SQLite (trade log, outcome tracking, weight tables)
- Streamlit (dashboard)

## Project Structure
```
paper-trader/
├── scheduler/
│   └── loop.py              # APScheduler event loop, market hours awareness
├── fetchers/
│   ├── discovery.py         # Dynamic ticker discovery from news + gainers/losers
│   ├── marketaux.py         # Marketaux API client
│   ├── newsapi.py           # NewsAPI.ai client + scraper
│   ├── scraper.py           # Full article text extractor
│   ├── market.py            # yfinance price/volume/MA/RSI data
│   └── aggregator.py        # Dedup + merge all feed sources
├── engine/
│   ├── sentiment.py         # Claude sentiment analysis (news catalyst)
│   ├── strategies.py        # Technical strategies: momentum, mean reversion, MA crossover
│   ├── regime.py            # Macro regime filter: risk-on vs risk-off
│   └── combiner.py          # Weighted signal combiner: sentiment + technicals + regime
├── risk/
│   └── manager.py           # Position sizing, stop loss, drawdown limits, correlation checks
├── executor/
│   └── alpaca.py            # Alpaca paper trade executor
├── feedback/
│   ├── logger.py            # Trade logging with full signal metadata
│   ├── outcomes.py          # Outcome measurement after N hours
│   └── weights.py           # Weight adjustment + circuit breaker
├── dashboard/
│   └── app.py               # Streamlit UI
├── db/
│   └── schema.sql           # SQLite schema: trades, outcomes, weights
├── .env.example
├── requirements.txt
└── CLAUDE.md
```

## Environment Variables (.env)
```
ANTHROPIC_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
MARKETAUX_API_KEY=
NEWSAPI_AI_KEY=
TICKER_MODE=discovery      # "watchlist" = trade only user-defined tickers, "discovery" = find tickers from news + market scans
WATCHLIST=AAPL,MSFT,NVDA   # optional: only used when TICKER_MODE=watchlist, or as a "always include" list in discovery mode
MAX_DISCOVERY_TICKERS=30   # max tickers to track per cycle in discovery mode (prevents API overload)
ALERT_EMAIL=              # optional: email for circuit breaker alerts
SLACK_WEBHOOK_URL=        # optional: Slack webhook for trade/halt alerts
```

---

## Architecture Overview (data flow order)

### 1. scheduler/loop.py — Continuous Event Loop
- Runs the full pipeline on a schedule using APScheduler
- **Primary job**: runs every 15 minutes during market hours (9:30 AM – 4:00 PM ET)
- **Pre-market job**: runs once at 9:00 AM ET to fetch overnight news and pre-compute signals
- **Post-market job**: runs once at 4:30 PM ET to log daily P&L and run feedback loop
- **Weekend/holiday**: skip all trading jobs; only run feedback outcome measurements
- Must check `is_market_open()` via Alpaca calendar API before submitting any order
- If the circuit breaker is tripped (see feedback/weights.py), skip all trading jobs and send alert
- Log every cycle: timestamp, tickers scanned, signals generated, orders placed

### 2. fetchers/discovery.py — Dynamic Ticker Discovery
Determines which tickers the system should analyze this cycle. Supports two modes controlled by `TICKER_MODE` env var.

**Mode: "watchlist"**
- Use only the tickers defined in `WATCHLIST` env var
- Simple, predictable, lower API usage
- Good for users who want to focus on specific stocks

**Mode: "discovery" (default)**
The system finds its own tickers every cycle. Sources, in priority order:

1. **News-driven discovery**: Scan Marketaux and NewsAPI results for ticker mentions before filtering. Extract every ticker symbol mentioned in headlines and article bodies. Any ticker with 2+ mentions across sources in the last 4 hours gets added to the active set.

2. **Market movers**: Use yfinance to fetch today's top gainers and losers from the S&P 500. Add the top 5 gainers and top 5 losers — these are where momentum and mean reversion signals are most likely to fire.

3. **Sector rotation scan**: Once per day (pre-market job), fetch sector ETF performance (XLK, XLF, XLE, XLV, XLC, XLI, XLY, XLP, XLU, XLRE, XLB). For the top 2 performing sectors, add the top 3 holdings by weight. For the bottom 2 sectors, add the top 3 holdings (short/sell candidates).

4. **Existing positions**: Always include any ticker the system currently holds a position in — you can't manage risk on a position you're not tracking.

5. **User pinned tickers**: If `WATCHLIST` is set, always include those tickers in addition to discovered ones (they act as an "always include" list).

**Dedup and cap**: Merge all sources, deduplicate, cap at `MAX_DISCOVERY_TICKERS` (default 30). Prioritize tickers with the most signals (mentioned in news + is a market mover + in a rotating sector = highest priority).

**Returns:**
```python
{
    "tickers": ["AAPL", "NVDA", "SMCI", ...],
    "sources": {
        "AAPL": ["news", "position"],        # why each ticker was included
        "SMCI": ["news", "gainer"],
        "XOM":  ["sector_rotation", "news"]
    },
    "mode": "discovery"
}
```

**Important**: The discovery module runs FIRST in every cycle, before any fetcher or strategy. All downstream modules receive the active ticker list from discovery — they never hardcode tickers.

### 3. fetchers/marketaux.py — Marketaux News
- In discovery mode: fetch broad financial news (no ticker filter), return ALL articles with ticker tags
- In watchlist mode: fetch news filtered to watchlist tickers only
- Extract pre-built sentiment_score per ticker (-1.0 to 1.0)
- DO NOT re-analyze Marketaux sentiment with Claude — use it directly
- Returns: list of `{title, ticker, sentiment_score, snippet, url, published_at, source:"marketaux"}`

### 4. fetchers/newsapi.py — NewsAPI Macro/Geopolitical News
- Fetch macro/geopolitical/economic headlines from NewsAPI.ai
- In discovery mode: fetch broadly, let discovery.py extract tickers from results
- In watchlist mode: filter by relevance to watchlist before scraping
- Pass relevant URLs to fetchers/scraper.py for full text
- Returns: list of `{title, full_text, topics, url, published_at, source:"newsapi"}`

### 5. fetchers/scraper.py — Article Scraper
- Primary: `trafilatura.fetch_url()` + `trafilatura.extract()`
- Fallback 1: newspaper3k `Article().parse()`
- Fallback 2: BeautifulSoup raw text extraction
- If all fail: return snippet only, flag as `partial=True`
- Truncate full_text to 1200 words max before returning
- Skip paywalled sites (NYT, WSJ, FT, Bloomberg) — return snippet only
- Returns: `{full_text, partial: bool}`

### 6. fetchers/market.py — Market Data
- Fetch price, volume, 50MA, 200MA, RSI(14) for each ticker in the active ticker list (from discovery.py)
- Fetch VIX (^VIX), SPY price vs 200MA, 10yr-2yr yield spread for regime detection
- In discovery mode: also fetch sector ETF data for sector rotation scan
- Returns: dict keyed by ticker symbol + macro indicators dict

### 7. fetchers/aggregator.py — Dedup & Merge
- Merge Marketaux + NewsAPI outputs
- Deduplicate by URL exact match, then by title similarity (>80% match = duplicate)
- Tag each item: source = "marketaux" | "newsapi"
- Returns: unified list sorted by published_at desc

### 8. engine/sentiment.py — Claude Sentiment Engine
- Marketaux items: pass sentiment_score directly, skip Claude call
- NewsAPI items: send full_text (not headline) to Claude for analysis
- Claude prompt: analyze sentiment as it relates to specific tickers
- Returns: sentiment score -1.0 to 1.0 per ticker per article
- Also classify macro articles for regime detection (see engine/regime.py)

### 9. engine/strategies.py — Technical Strategy Signals
Each strategy independently produces a signal per ticker: BUY / SELL / HOLD with confidence 0.0–1.0.
Strategies run against whatever tickers discovery.py returned — they are ticker-agnostic.

**Momentum**
- Buy if price is above 20MA AND 20MA is above 50MA (uptrend confirmed)
- Sell if price is below 20MA AND 20MA is below 50MA
- Confidence scales with trend strength (distance from MA as % of price)

**Mean Reversion**
- Buy if RSI(14) < 30 (oversold)
- Sell if RSI(14) > 70 (overbought)
- Confidence scales with RSI extremity (RSI 20 = higher confidence than RSI 29)

**MA Crossover**
- Buy on golden cross: 50MA crosses above 200MA
- Sell on death cross: 50MA crosses below 200MA
- Confidence = 0.8 (fixed — crossovers are binary events)
- Only fire on the day of the crossover, not while already crossed

**News Catalyst** (from sentiment.py)
- Buy if aggregated sentiment > +0.3
- Sell if aggregated sentiment < -0.3
- Confidence = abs(sentiment_score)

### 10. engine/regime.py — Macro Regime Filter
Determines the current market environment before any trade is approved.

**Inputs:**
- VIX level (from yfinance ^VIX)
- SPY price vs 200MA (above = bullish, below = bearish)
- 10yr-2yr yield spread (inverted = recession risk)
- Claude macro news classification (RISK_ON / RISK_OFF / NEUTRAL from recent macro articles)

**Output:**
```python
{
    "mode": "risk_on" | "risk_off" | "neutral",
    "position_size_multiplier": 0.0 - 1.0,   # scales all position sizes
    "confidence_threshold": 0.7 - 0.95,       # minimum confidence to act
    "max_open_positions": 1 - 5               # max concurrent positions
}
```

**Rules:**
- VIX > 30 → risk_off, position_size_multiplier = 0.3
- VIX > 40 → risk_off, position_size_multiplier = 0.0 (halt trading)
- SPY below 200MA → risk_off, raise confidence_threshold to 0.85
- Yield curve inverted → risk_off, reduce max_open_positions to 2
- Claude macro classification: weight recent 48hr news into final mode decision

### 11. engine/combiner.py — Weighted Signal Combiner
Merges all strategy signals + sentiment into a single actionable signal per ticker.

**Process:**
1. Collect signals from: momentum, mean_reversion, ma_crossover, news_catalyst
2. Apply learned weights from feedback/weights.py to each strategy's confidence
3. Compute weighted average confidence per ticker per direction (BUY vs SELL)
4. Apply regime filter: multiply confidence by `position_size_multiplier`, enforce `confidence_threshold`
5. Final output per ticker: `{signal: BUY|SELL|HOLD, confidence: float, strategies_agreeing: list}`

**Weight table** (initial defaults, adjusted by feedback loop):
```python
weights = {
    "momentum":       0.75,
    "mean_reversion": 0.65,
    "ma_crossover":   0.70,
    "news_catalyst":  0.60
}
```

Only act on signals where `confidence > regime.confidence_threshold` AND at least 2 strategies agree on direction.

### 12. risk/manager.py — Risk Management Gate
Every signal MUST pass through the risk manager before reaching the executor. The risk manager can veto or resize any trade. This layer is non-negotiable — never bypass it.

**Hard Rules (never overridden):**
- Max position size: never more than 10% of total portfolio value in one ticker
- Max open positions: governed by regime filter (default 5, reduced in risk-off)
- Stop loss: place a stop-loss order at -3% from entry on every BUY
- Take profit: place a limit sell at +8% from entry on every BUY
- Max daily loss: if portfolio is down 5% from day open, halt ALL trading for the day
- Max weekly loss: if portfolio is down 10% from week open, halt ALL trading until manual override
- Market hours: reject any order if market is closed (check Alpaca calendar)
- Sector concentration limit: no more than 30% of portfolio in a single sector. Since the system can discover ANY ticker, use a sector lookup (yfinance `.info["sector"]`) to classify tickers dynamically at runtime. Cache sector lookups in SQLite to avoid repeated API calls. If sector lookup fails, treat the ticker as "unknown" sector and apply a conservative 5% max position size instead of 10%.
- Cash reserve: always keep at least 20% of portfolio in cash — never go all-in
- Penny stock filter: reject any ticker with price < $5 or average daily volume < 500,000 — these are too illiquid and volatile for automated trading
- Market cap floor: reject tickers with market cap < $1B (micro-caps are too risky for autonomous trading)

**Position Sizing Formula:**
```python
base_size = portfolio_value * 0.10                    # 10% max
sized = base_size * signal_confidence                  # scale by confidence
regime_adjusted = sized * regime.position_size_multiplier  # scale by regime
final_shares = floor(regime_adjusted / current_price)  # convert to whole shares
```

### 13. executor/alpaca.py — Paper Trade Executor
- Submit paper trade orders via Alpaca SDK
- Market orders for entries
- Simultaneously place bracket orders: stop-loss at -3%, take-profit at +8%
- Log every order with: timestamp, ticker, signal, confidence, order_id, fill_price, shares
- On fill confirmation: write to feedback/logger.py with full signal metadata
- NEVER use live trading endpoint — always `https://paper-api.alpaca.markets`

### 14. feedback/logger.py — Trade Signal Logger
On every executed trade, log the full provenance:
```python
{
    "trade_id": "uuid",
    "ticker": "AAPL",
    "signal": "BUY",
    "confidence": 0.82,
    "sentiment_score": 0.74,
    "sentiment_source": "marketaux",
    "strategies_fired": ["momentum", "news_catalyst"],
    "discovery_sources": ["news", "gainer"],   # how this ticker was discovered
    "regime_mode": "risk_on",
    "article_urls": ["https://..."],
    "entry_price": 182.50,
    "shares": 5,
    "stop_loss_price": 177.03,
    "take_profit_price": 197.10,
    "timestamp": "2026-04-04T14:32:00Z"
}
```
Store in SQLite `trades` table.

### 15. feedback/outcomes.py — Outcome Measurement
A scheduled job that runs hourly. For each open trade past its measurement window:

**Measurement windows** (configurable):
- Short-term: 4 hours (for day-trade style signals)
- Medium-term: 24 hours (default for swing signals)
- Long-term: 72 hours (for MA crossover signals)

**Process:**
1. Query `trades` table for trades past their measurement window
2. Fetch current price from Alpaca or yfinance
3. Compute return_pct = (current_price - entry_price) / entry_price
4. Classify: WIN (return > +1%), LOSS (return < -1%), NEUTRAL (between)
5. Write to SQLite `outcomes` table:
```python
{
    "trade_id": "uuid",
    "exit_price": 186.20,
    "return_pct": 2.03,
    "outcome": "WIN",
    "holding_period_hours": 6,
    "measured_at": "2026-04-04T20:32:00Z"
}
```

### 16. feedback/weights.py — Weight Adjustment + Circuit Breaker
After each outcome is recorded, adjust the weight table.

**Weight update formula (exponential moving average):**
```python
# WIN: nudge weight up
new_weight = old_weight * 0.95 + 1.0 * 0.05

# LOSS: nudge weight down
new_weight = old_weight * 0.95 + 0.0 * 0.05

# Clamp between 0.1 and 1.0 — never fully silence or over-trust
new_weight = max(0.1, min(1.0, new_weight))
```

Update weights for:
- The strategy that fired (momentum, mean_reversion, etc.)
- The news source that contributed (marketaux, newsapi, scraped)

Store in SQLite `weights` table. The combiner reads from this table on every cycle.

**Circuit Breaker:**
- Track rolling 7-day win rate across all trades
- If win rate falls below 40%:
  1. Halt ALL new trades automatically
  2. Send alert via email and/or Slack
  3. Set `circuit_breaker_tripped = True` in DB
  4. Scheduler skips all trading jobs until manual reset
- Manual reset: human sets `circuit_breaker_tripped = False` via dashboard or CLI

### 17. dashboard/app.py — Streamlit UI
- Portfolio overview: cash, positions, total value, daily P&L
- Trade history: every trade with signal metadata, outcome, return
- **Active tickers panel**: show which tickers are being tracked this cycle, why each was discovered (news, gainer, sector rotation, etc.), and which mode the system is in (watchlist vs discovery)
- Signal feed: current signals with confidence scores and strategy breakdown
- Weight table: current learned weights per strategy and source
- Regime indicator: current mode (risk-on / risk-off / neutral) with inputs
- **Sector exposure chart**: pie chart of portfolio allocation by sector (from sector_cache)
- Performance chart: portfolio value over time
- Circuit breaker status: green (active) / red (halted) with manual override button
- Win rate chart: rolling 7-day win rate with 40% threshold line
- **Settings panel**: toggle TICKER_MODE (watchlist / discovery), edit WATCHLIST, adjust MAX_DISCOVERY_TICKERS

### 18. db/schema.sql — SQLite Schema
```sql
CREATE TABLE trades (
    trade_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    signal TEXT NOT NULL,           -- BUY or SELL
    confidence REAL NOT NULL,
    sentiment_score REAL,
    sentiment_source TEXT,
    strategies_fired TEXT,          -- JSON array
    discovery_sources TEXT,         -- JSON array: how ticker was found ("news", "gainer", "sector_rotation", etc.)
    regime_mode TEXT,
    article_urls TEXT,              -- JSON array
    entry_price REAL NOT NULL,
    shares INTEGER NOT NULL,
    stop_loss_price REAL,
    take_profit_price REAL,
    order_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE outcomes (
    trade_id TEXT PRIMARY KEY REFERENCES trades(trade_id),
    exit_price REAL NOT NULL,
    return_pct REAL NOT NULL,
    outcome TEXT NOT NULL,          -- WIN, LOSS, NEUTRAL
    holding_period_hours REAL,
    measured_at TEXT NOT NULL
);

CREATE TABLE weights (
    category TEXT NOT NULL,         -- "strategy" or "source"
    name TEXT NOT NULL,             -- e.g. "momentum" or "marketaux"
    weight REAL NOT NULL DEFAULT 0.5,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (category, name)
);

CREATE TABLE circuit_breaker (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tripped BOOLEAN NOT NULL DEFAULT 0,
    tripped_at TEXT,
    reason TEXT,
    win_rate_at_trip REAL
);

CREATE TABLE sector_cache (
    ticker TEXT PRIMARY KEY,
    sector TEXT NOT NULL,              -- e.g. "Technology", "Healthcare", "Energy"
    market_cap REAL,
    avg_volume REAL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE discovery_log (
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,              -- "news", "gainer", "loser", "sector_rotation", "position", "watchlist"
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (cycle_id, ticker)
);
```

---

## Key Rules for Claude Code
- NEVER send raw headlines to Claude for sentiment — always use full_text
- NEVER re-analyze Marketaux sentiment scores — they are pre-computed and trusted
- NEVER scrape paywalled domains: wsj.com, ft.com, bloomberg.com, nytimes.com
- ALWAYS truncate article text to 1200 words before sending to Claude
- ALWAYS deduplicate before analysis — never analyze the same article twice
- ALWAYS use paper trading endpoint, never live trading URL
- ALWAYS pass every signal through risk/manager.py before execution — no exceptions
- ALWAYS log full signal metadata on every trade for the feedback loop
- ALWAYS check market hours before submitting orders
- ALWAYS check circuit breaker status before running trading jobs
- NEVER put more than 10% of portfolio in a single ticker
- NEVER allow total invested to exceed 80% of portfolio (keep 20% cash reserve)
- NEVER trade penny stocks (price < $5) or micro-caps (market cap < $1B)
- NEVER exceed 30% portfolio allocation in a single sector
- NEVER hardcode ticker lists in any module except discovery.py — all downstream modules receive tickers dynamically
- ALWAYS run discovery.py as the FIRST step in every trading cycle
- ALWAYS look up sector via yfinance for any newly discovered ticker and cache it
- One ticker at a time through the signal engine — no batch parallelism yet

## Ticker Modes
**watchlist** — User provides a fixed list of tickers in `WATCHLIST` env var. System only analyzes and trades these tickers. Lower API usage, predictable behavior.

**discovery** (default) — System finds its own tickers every cycle from news mentions, market movers, and sector rotation. User can optionally set `WATCHLIST` as an "always include" list alongside discovered tickers. More autonomous, higher API usage, capped at `MAX_DISCOVERY_TICKERS`.

## Dependencies (requirements.txt)
```
anthropic
alpaca-trade-api
yfinance
trafilatura
newspaper3k
beautifulsoup4
requests
apscheduler
streamlit
plotly
python-dotenv
```

## Implementation Priority
Build in this order — each layer depends on the ones before it:

1. **db/schema.sql** — create the SQLite tables first (including sector_cache and discovery_log)
2. **scheduler/loop.py** — basic event loop with market hours check
3. **fetchers/discovery.py** — ticker discovery engine (watchlist + dynamic modes)
4. **engine/strategies.py** — momentum, mean reversion, MA crossover
5. **engine/regime.py** — macro regime filter
6. **engine/combiner.py** — weighted signal combiner replacing old signals.py
7. **risk/manager.py** — position sizing + all hard rules + dynamic sector lookup
8. **feedback/logger.py** — trade logging on execution
9. **feedback/outcomes.py** — outcome measurement scheduled job
10. **feedback/weights.py** — weight adjustment + circuit breaker
11. **dashboard/app.py** — update to show new data (discovery panel, weights, regime, sector exposure, circuit breaker)