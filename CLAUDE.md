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
- Massive API (ticker-tagged financial news + built-in sentiment)
- NewsAPI.ai (macro, geopolitical, economic news)
- Groq Llama 3.1 8B (company name → ticker extraction via REST API)
- yfinance (price, volume, moving averages, VIX, yield data)
- trafilatura (primary article scraper)
- newspaper3k (fallback article scraper)
- BeautifulSoup4 (fallback for unparseable sites)
- APScheduler (continuous scheduler / event loop)
- Turso (distributed SQLite database)
- libsql-client (Turso Python client)
- Streamlit (dashboard)

## Project Structure
```
paper-trader/
├── scheduler/
│   └── loop.py              # APScheduler event loop, market hours awareness
├── fetchers/
│   ├── discovery.py         # Dynamic ticker discovery from news + gainers/losers
│   ├── groq_client.py       # Groq Llama 3.1 8B for company name → ticker extraction
│   ├── marketaux.py         # Marketaux API client
│   ├── massive.py           # Massive API client (ticker-tagged news + sentiment)
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
│   ├── client.py            # Turso database connection manager
│   └── schema.sql           # Turso schema: trades, outcomes, weights
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
GROQ_API_KEY=              # optional: Groq free tier for company name extraction (sign up at console.groq.com)
MASSIVE_API_KEY=           # optional: Massive API for ticker-tagged news (sign up at massive.com)
TURSO_CONNECTION_URL=libsql://your-database-name-random.turso.io
TURSO_AUTH_TOKEN=
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

1. **News-driven discovery**: Scan Marketaux and NewsAPI results for ticker mentions before filtering. Extract ticker symbols via:
   - **Regex patterns** (always): $AAPL, (NASDAQ:AAPL), standalone ticker symbols
   - **Groq Llama 3.1 8B** (if GROQ_API_KEY set): Company name recognition (e.g., "Apple" → AAPL, "Nvidia" → NVDA)
   - Any ticker with 2+ mentions across sources in the last 4 hours gets added to the active set. Groq extraction catches company names without explicit ticker symbols, improving discovery coverage.

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

### 2.5 fetchers/groq_client.py — Groq Llama 3.1 8B Company Name Extraction
**Optional** — requires `GROQ_API_KEY` (free tier: 500K tokens/day, 14,400 requests/day)

Provides reusable AI-powered company name → ticker resolution:
- Extracts company names from article text and resolves to US stock ticker symbols
- Used by `discovery.py` to enhance news-driven ticker discovery
- Used by `newsapi.py` to tag macro articles with relevant company mentions
- Supplements regex pattern matching (catches "Apple Inc" → AAPL, "Nvidia" → NVDA, etc.)
- Graceful fallback: if GROQ_API_KEY not set, system continues with regex-only extraction

**Functions**:
- `extract_tickers_from_text(text)` — Extract company names from single article
- `extract_tickers_batch(articles)` — Batch process multiple articles
- `get_ticker_symbols(articles)` — Convenience function returning ticker set
- `is_available()` — Check if GROQ_API_KEY configured

**Setup**:
1. Sign up at [console.groq.com](https://console.groq.com)
2. Create API key
3. Add `GROQ_API_KEY=gsk_your_key_here` to `.env`

**Cost**: Free tier covers 99% of typical usage (small personal trading bot at ~100 articles/day = ~10% of free allowance)

### 3. fetchers/marketaux.py — Marketaux News
- In discovery mode: fetch broad financial news (no ticker filter), return ALL articles with ticker tags
- In watchlist mode: fetch news filtered to watchlist tickers only
- Extract pre-built sentiment_score per ticker (-1.0 to 1.0)
- DO NOT re-analyze Marketaux sentiment with Claude — use it directly
- Returns: list of `{title, ticker, sentiment_score, snippet, url, published_at, source:"marketaux"}`

### 3.5. fetchers/massive.py — Massive News
- Ticker-tagged financial news with built-in sentiment analysis (similar to Marketaux)
- Optional — requires `MASSIVE_API_KEY` in `.env` (sign up at massive.com)
- Returns articles with pre-tagged ticker symbols and sentiment scores
- Sentiment derived from Massive `insights` array: positive → 0.7, negative → -0.7, neutral → 0.0
- DO NOT re-analyze Massive sentiment with Claude — use it directly (same rule as Marketaux)
- Graceful fallback: if `MASSIVE_API_KEY` not set, aggregator skips this source
- In discovery mode: fetch broad news (no ticker filter), return ALL articles with ticker tags
- In watchlist mode: fetch news filtered to provided tickers only
- Returns: list of `{title, description, tickers, sentiment_score, url, published_at, author, source:"massive"}`

### 4. fetchers/newsapi.py — NewsAPI Macro/Geopolitical News
- Fetch macro/geopolitical/economic headlines from NewsAPI.ai
- In discovery mode: fetch broadly, let discovery.py extract tickers from results
- In watchlist mode: filter by relevance to watchlist before scraping
- Per-article ticker tagging:
  - **Regex extraction** (always): extract symbols from title/snippet
  - **Groq enhancement** (if GROQ_API_KEY set): supplement with company name recognition
  - Tickers are tagged on each article for discovery feedback
- Pass relevant URLs to fetchers/scraper.py for full text
- Returns: list of `{title, full_text, topics, url, published_at, source:"newsapi", tickers: [...], extraction_confidence: 0.0-1.0}`

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
- Merge Marketaux + Massive + NewsAPI + Alpaca News outputs
- 4-step waterfall enrichment for NewsAPI articles (Polygon → Alpaca → scraper → snippet-only)
- Deduplicate by URL exact match, then by title similarity (>80% Jaccard match = duplicate)
- Tag each item: source = "marketaux" | "massive" | "newsapi" | "alpaca" | "polygon"
- Returns: unified list sorted by published_at desc

### 8. engine/sentiment.py — Claude Sentiment Engine
- Marketaux items: pass sentiment_score directly, skip Claude call
- Massive items: pass sentiment_score directly, skip Claude call (same as Marketaux)
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
- Buy if RSI(14) is below 30 (oversold) on a green day
- Sell if RSI(14) is above 70 (overbought) on a red day
- Confidence scales with RSI extremity (0.3 = 30 RSI, 0.7 = RSI closer to 20)

**Moving Average Crossover**
- Buy if 20MA crosses above 50MA
- Sell if 20MA crosses below 50MA
- Confidence 0.8 (high conviction, infrequent signal)

**Volume Surge**
- Buy if today's volume > 1.5x 20-day avg volume AND price is up (breakout volume)
- Sell if volume surge AND price is down (panic volume)
- Confidence 0.5 (requires confirmation from sentiment or regime)

Return per strategy:
```python
{
    "signal": "BUY",
    "confidence": 0.75,
    "strategy": "momentum",
    "reason": "price above 20MA which is above 50MA"
}
```

### 10. engine/regime.py — Macro Regime Filter
Classify the current market regime. Data comes from fetchers/market.py.

**Risk-On (green light for long trades)**
- VIX < 20
- SPY price > SPY 200MA (uptrend)
- 10yr-2yr yield spread > 0.5% (normal or positive curve)
- Regime override: "risk_on"

**Risk-Off (green light for shorts, filter longs)**
- VIX > 25
- SPY price < SPY 200MA (downtrend)
- 10yr-2yr yield spread < -0.5% (inverted curve)
- Regime override: "risk_off"

**Neutral (no special regime, trade normally)**
- Everything else

Determine net sentiment from NewsAPI/macro articles (economic data, Fed, geopolitical).
If strongly negative macro sentiment detected, downshift to risk-off even if technical indicators are neutral.

Return:
```python
{
    "regime": "risk_on",
    "vix": 18.5,
    "spy_vs_200ma": 0.02,
    "yield_spread": 0.65,
    "macro_sentiment": "positive",
    "confidence": 0.85
}
```

### 11. engine/combiner.py — Weighted Signal Combiner
Take outputs from strategies (9), regime (10), and sentiment (8). Produce final trading signal per ticker.

**Per-ticker calculation:**
1. Collect all strategy signals for the ticker
2. Weight each by its learned weight from Turso `weights` table
3. Multiply by regime modifier:
   - If regime = risk_on: keep BUY confidence as-is, reduce SELL confidence by 20%
   - If regime = risk_off: keep SELL confidence as-is, reduce BUY confidence by 30%
   - If regime = neutral: no modifier
4. If sentiment is available, include it as a separate signal with learned weight
5. Average all weighted signals
6. Round to BUY (>0.55) / SELL (<0.45) / HOLD (0.45–0.55)

**Output per ticker:**
```python
{
    "ticker": "AAPL",
    "signal": "BUY",
    "confidence": 0.72,
    "components": {
        "momentum": 0.80,           # strategy confidence
        "sentiment": 0.65,
        "regime_adjusted": 0.72     # after regime filter
    },
    "regime": "risk_on",
    "rationale": "Momentum + positive sentiment, risk-on regime"
}
```

### 12. risk/manager.py — Risk Management
Before any order is placed, run through risk checks. Reject orders that violate hard limits.

**For each signal:**
1. **Position sizing**: Allocate max_allocation % of portfolio
   - Risk per trade = max 2% of portfolio
   - Position size = risk_amount / (entry_price - stop_loss_price)
   - Cap at max 500 shares per ticker (prevent concentration)

2. **Stop loss placement**: 
   - For BUY: stop = entry_price * 0.97 (3% below entry)
   - For SELL: stop = entry_price * 1.03 (3% above entry)

3. **Take profit placement**:
   - For BUY: TP = entry_price * 1.03 (3% above entry) — will adjust once position is in profit
   - For SELL: TP = entry_price * 0.97 (3% below entry)

4. **Hard rules (reject if any are violated)**:
   - Total portfolio allocation <= 80% (keep 20% cash)
   - Single ticker max 10% of portfolio
   - Single sector max 30% of portfolio
   - No penny stocks (price < $5)
   - No micro-caps (market cap < $1B)
   - Maximum 15 open positions at once
   - No duplicate signals on same ticker within 2 hours (prevent churn)

5. **Correlation check** (if sector data available):
   - If proposing a BUY in sector X that already holds 20% of portfolio, reduce position size by 50%

6. **Sector lookup**:
   - Query Turso `sector_cache` for ticker sector
   - If not in cache, fetch via yfinance and insert

**Return:**
```python
{
    "approved": True,
    "reason": "",
    "position_size": 45,
    "shares": 45,
    "entry_price": 175.50,
    "stop_loss": 170.24,
    "take_profit": 180.67,
    "portfolio_allocation_pct": 2.1
}
```

or if rejected:
```python
{
    "approved": False,
    "reason": "Sector allocation would exceed 30% (current: 25%, new: 8%)"
}
```

### 13. executor/alpaca.py — Alpaca Order Executor
Receives approved signals from risk/manager.py. Place actual orders on Alpaca paper account.

- Check market hours before submitting any order
- Submit limit order at entry_price (or current price + 0.1% slippage buffer)
- Set stop loss and take profit via order bracket (if supported by Alpaca SDK)
- If bracket not supported, place stop as a separate order immediately after entry fill
- On successful fill: return `{order_id, symbol, filled_price, shares}`
- On failure: return error message; do NOT retry within same cycle
- Track all orders in a local dict (order_id -> signal_metadata) for the feedback loop

### 14. feedback/logger.py — Trade Logger
Every time an order fills, log the trade to Turso `trades` table.

Write metadata:
```python
{
    "trade_id": "uuid",
    "ticker": "AAPL",
    "signal": "BUY",
    "confidence": 0.72,
    "sentiment_score": 0.65,
    "sentiment_source": "newsapi",
    "strategies_fired": ["momentum", "sentiment"],    # JSON array
    "discovery_sources": ["news", "gainer"],          # JSON array
    "regime_mode": "risk_on",
    "article_urls": ["url1", "url2"],                 # JSON array
    "entry_price": 175.50,
    "shares": 45,
    "stop_loss_price": 170.24,
    "take_profit_price": 180.67,
    "order_id": "alpaca-order-id",
    "created_at": "2026-04-04T14:32:00Z"
}
```

### 15. feedback/outcomes.py — Outcome Measurement
Scheduled job: runs every 4 hours and at market close, checks all open trades for exits.

For each open trade:
1. Fetch current price from yfinance
2. If holding > N hours (default 8 hours):
   - Calculate return: (current_price - entry_price) / entry_price
3. If stop loss or take profit hit, close position immediately
   - Set exit_price to stop/TP price
4. Classify: WIN (return > +1%), LOSS (return < -1%), NEUTRAL (between)
5. Write to Turso `outcomes` table:
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

Store in Turso `weights` table. The combiner reads from this table on every cycle.

**Circuit Breaker:**
- Track rolling 7-day win rate across all trades
- If win rate falls below 40%:
  1. Halt ALL new trades automatically
  2. Send alert via email and/or Slack
  3. Set `tripped = True` in Turso `circuit_breaker` table
  4. Scheduler skips all trading jobs until manual reset
- Manual reset: human sets `tripped = False` via dashboard or CLI

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

### 18. db/client.py — Turso Database Manager
Initialize and manage Turso connection.

```python
import os
from libsql_client import create_client_sync

def get_db():
    url = os.getenv("TURSO_CONNECTION_URL")
    auth_token = os.getenv("TURSO_AUTH_TOKEN")
    
    client = create_client_sync(
        url=url,
        auth_token=auth_token
    )
    
    return client

# Usage throughout app:
# db = get_db()
# result = db.execute("SELECT * FROM trades WHERE ticker = ?", [ticker])
# db.execute("INSERT INTO trades (...) VALUES (...)")
```

### 19. db/schema.sql — Turso Schema
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
- NEVER re-analyze Marketaux or Massive sentiment scores — they are pre-computed and trusted
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
- ALWAYS look up sector via yfinance for any newly discovered ticker and cache it in Turso
- One ticker at a time through the signal engine — no batch parallelism yet
- Groq company name extraction is OPTIONAL: gracefully degrade to regex-only if GROQ_API_KEY not set
- NEVER rely solely on Groq for ticker extraction — always combine with regex patterns
- ALWAYS truncate article text to 1500 chars before sending to Groq (cost optimization)

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
libsql-client
```

## Implementation Priority
Build in this order — each layer depends on the ones before it:

1. **db/client.py** — create Turso connection manager and initialize schema
2. **db/schema.sql** — create all Turso tables (trades, outcomes, weights, circuit_breaker, sector_cache, discovery_log)
3. **scheduler/loop.py** — basic event loop with market hours check
4. **fetchers/discovery.py** — ticker discovery engine (watchlist + dynamic modes)
5. **engine/strategies.py** — momentum, mean reversion, MA crossover
6. **engine/regime.py** — macro regime filter
7. **engine/combiner.py** — weighted signal combiner replacing old signals.py
8. **risk/manager.py** — position sizing + all hard rules + dynamic sector lookup (queries Turso sector_cache)
9. **feedback/logger.py** — trade logging to Turso on execution
10. **feedback/outcomes.py** — outcome measurement scheduled job (writes to Turso)
11. **feedback/weights.py** — weight adjustment + circuit breaker (reads/writes Turso weights and circuit_breaker tables)
12. **dashboard/app.py** — update to show new data (discovery panel, weights, regime, sector exposure, circuit breaker, pull from Turso)