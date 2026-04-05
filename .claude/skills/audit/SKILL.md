---
name: audit
description: >
  Regenerate AUDIT.md from scratch by reading every module in the paper trader codebase
  and comparing actual implementation against the CLAUDE.md specification. Prioritizes
  completeness — a file that exists but is a stub is flagged before a file that simply
  doesn't exist yet. Also used internally by the status-report skill to populate the
  audit summary section. Trigger when the user says "audit", "run audit", "check
  completeness", "what's implemented", or runs /audit.
---

# Architecture Audit

Perform a full audit of the paper trader codebase. Read every module, assess its
completeness against the CLAUDE.md specification, identify architectural violations,
and write a fresh `AUDIT.md`. Do not carry over any prior audit state — start from
zero every time.

**Priority order for flagging issues:**
1. Implemented but incomplete (file exists, some functions work, others are stubs)
2. Fully stubbed (file exists, no logic implemented at all)
3. Missing (file does not exist)
4. Architectural violations (file exists but breaks a CLAUDE.md hard rule)

---

## Step 1 — Read Every Module

Read each file listed below in full. For each file, determine:

**Implementation status:**
- `COMPLETE` — all key functions have real logic, not just `pass` or `raise NotImplementedError`
- `PARTIAL` — some functions implemented, others are stubs or TODO
- `STUB` — file exists but all functions contain only `pass`, `raise NotImplementedError`, or TODO comments
- `MISSING` — file does not exist

**Files to audit:**

Foundation:
- `db/client.py`
- `db/schema.sql`

Scheduler:
- `scheduler/loop.py`

Fetchers:
- `fetchers/discovery.py`
- `fetchers/marketaux.py`
- `fetchers/newsapi.py`
- `fetchers/scraper.py`
- `fetchers/market.py`
- `fetchers/aggregator.py`

Engine:
- `engine/sentiment.py`
- `engine/strategies.py`
- `engine/regime.py`
- `engine/combiner.py`
- `engine/signals.py` ← deprecated, should not exist

Execution:
- `executor/alpaca.py`

Risk:
- `risk/manager.py`

Feedback:
- `feedback/logger.py`
- `feedback/outcomes.py`
- `feedback/weights.py`

Dashboard:
- `dashboard/app.py`

---

## Step 2 — Completeness Checklist Per Module

For each file that exists (PARTIAL or STUB), check the specific items below against
the CLAUDE.md specification. Mark each item `[x]` (done) or `[ ]` (missing/stub).

The items below are the minimum required per CLAUDE.md — not nice-to-haves.

---

### db/client.py
- [ ] `get_db()` function exists and returns a libsql_client sync client
- [ ] Reads `TURSO_CONNECTION_URL` and `TURSO_AUTH_TOKEN` from environment
- [ ] No hardcoded credentials

### db/schema.sql
- [ ] `trades` table with all columns from CLAUDE.md section 19
- [ ] `outcomes` table with all columns
- [ ] `weights` table with all columns
- [ ] `circuit_breaker` table with all columns
- [ ] `sector_cache` table with all columns
- [ ] `discovery_log` table with all columns

### scheduler/loop.py
- [ ] Primary job scheduled every 15 minutes during market hours (9:30 AM–4:00 PM ET)
- [ ] Pre-market job scheduled at 9:00 AM ET
- [ ] Post-market job scheduled at 4:30 PM ET
- [ ] Weekend/holiday detection skips trading jobs
- [ ] `is_market_open()` checks Alpaca calendar API before order submission
- [ ] Circuit breaker check runs before each trading job
- [ ] Discovery runs as the first step in each trading cycle
- [ ] Logs each cycle: timestamp, tickers scanned, signals generated, orders placed

### fetchers/discovery.py
- [ ] Returns dict with keys: `tickers`, `sources`, `mode`
- [ ] Supports `TICKER_MODE=watchlist` mode (only WATCHLIST tickers)
- [ ] Supports `TICKER_MODE=discovery` mode (dynamic discovery)
- [ ] News-driven discovery: tickers with 2+ mentions in last 4 hours are added
- [ ] Market movers: top 5 gainers + top 5 losers from S&P 500 added
- [ ] Sector rotation scan: top/bottom 2 sector ETF top 3 holdings added
- [ ] Existing positions always included
- [ ] Pinned WATCHLIST tickers always included in discovery mode
- [ ] Dedup + cap at `MAX_DISCOVERY_TICKERS`
- [ ] `sources` dict maps each ticker to list of source reasons
- [ ] **VIOLATION CHECK**: No hardcoded ticker lists (SP500_TICKERS, SECTOR_ETF_HOLDINGS, etc.)
  - Dynamic fetching from yfinance/APIs only

### fetchers/marketaux.py
- [ ] `fetch_news()` makes real API requests (not stub)
- [ ] Discovery mode: fetches without ticker filter, returns all articles with ticker tags
- [ ] Watchlist mode: fetches filtered to watchlist tickers only
- [ ] Extracts pre-built `sentiment_score` per ticker (-1.0 to 1.0) from API response
- [ ] Returns list with fields: `title`, `ticker`, `sentiment_score`, `snippet`, `url`, `published_at`, `source`
- [ ] `source` field set to `"marketaux"` on every item
- [ ] Rate limiting logic implemented

### fetchers/newsapi.py
- [ ] `fetch_headlines()` makes real API requests (not stub)
- [ ] Fetches macro/geopolitical/economic headlines
- [ ] Discovery mode: fetches broadly, lets discovery extract tickers
- [ ] Watchlist mode: filters by relevance
- [ ] Passes URLs to scraper for full text
- [ ] Returns list with fields: `title`, `full_text`, `topics`, `url`, `published_at`, `source`
- [ ] `source` field set to `"newsapi"` on every item

### fetchers/scraper.py
- [ ] Primary scraper: `trafilatura.fetch_url()` + `trafilatura.extract()`
- [ ] Fallback 1: newspaper3k `Article().parse()`
- [ ] Fallback 2: BeautifulSoup raw text extraction
- [ ] Returns `{full_text, partial: bool}` shape
- [ ] Paywalled domains return snippet only with `partial=True`: wsj.com, ft.com, bloomberg.com, nytimes.com
- [ ] Full text truncated to 1200 words max

### fetchers/market.py
- [ ] Fetches price, volume, 50MA, 200MA, RSI(14) per ticker
- [ ] Fetches VIX (`^VIX`) for regime detection
- [ ] Fetches SPY price vs SPY 200MA
- [ ] Fetches 10yr-2yr yield spread
- [ ] Accepts dynamic ticker list from discovery (no hardcoded tickers)
- [ ] Discovery mode: fetches sector ETF data for sector rotation scan
- [ ] Returns dict keyed by ticker symbol + separate macro indicators dict

### fetchers/aggregator.py
- [ ] Merges Marketaux + NewsAPI outputs
- [ ] Deduplicates by URL exact match
- [ ] Deduplicates by title similarity > 80%
- [ ] Tags each item with source field
- [ ] Returns unified list sorted by `published_at` descending

### engine/sentiment.py
- [ ] Marketaux items: passes `sentiment_score` directly, skips Claude
- [ ] NewsAPI items: sends `full_text` (not headline) to Claude
- [ ] Never sends raw headlines to Claude
- [ ] Truncates text to 1200 words before Claude call
- [ ] Returns sentiment score -1.0 to 1.0 per ticker per article
- [ ] Tags sentiment with source for feedback loop attribution

### engine/strategies.py
- [ ] `momentum_signal()` implemented: BUY if price > 20MA > 50MA, SELL if price < 20MA < 50MA
- [ ] `mean_reversion_signal()` implemented: BUY if RSI < 30 on green day, SELL if RSI > 70 on red day
- [ ] `ma_crossover_signal()` implemented: BUY if 20MA crosses above 50MA, SELL if below
- [ ] `volume_surge_signal()` implemented: BUY if volume > 1.5x avg + price up, SELL if volume surge + price down
- [ ] Each strategy returns: `{signal, confidence, strategy, reason}`
- [ ] Confidence scales with signal strength (not a fixed value except MA crossover at 0.8)
- [ ] All strategies accept dynamic ticker list (no hardcoded tickers)

### engine/regime.py
- [ ] Classifies regime as `risk_on`, `risk_off`, or `neutral`
- [ ] Risk-on criteria: VIX < 20, SPY > 200MA, yield spread > 0.5%
- [ ] Risk-off criteria: VIX > 25, SPY < 200MA, yield spread < -0.5%
- [ ] Macro sentiment from NewsAPI/macro articles can override neutral → risk_off
- [ ] Returns: `{regime, vix, spy_vs_200ma, yield_spread, macro_sentiment, confidence}`

### engine/combiner.py
- [ ] Collects all strategy signals per ticker
- [ ] Reads learned weights from Turso `weights` table
- [ ] Applies regime modifier: risk_on reduces SELL confidence by 20%, risk_off reduces BUY confidence by 30%
- [ ] Includes sentiment as separate signal with learned weight
- [ ] BUY threshold > 0.55, SELL threshold < 0.45, HOLD between
- [ ] Returns per ticker: `{ticker, signal, confidence, components, regime, rationale}`

### engine/signals.py
- [ ] **DEPRECATED** — this file should be deleted
- [ ] Check: is it still imported anywhere? (grep for `from engine.signals` or `import signals`)
- [ ] If imported anywhere, those imports must be removed before deletion

### risk/manager.py
- [ ] Position size = risk_amount / (entry_price - stop_loss_price), capped at 500 shares
- [ ] BUY stop = entry_price * 0.97, SELL stop = entry_price * 1.03
- [ ] BUY take profit = entry_price * 1.03, SELL take profit = entry_price * 0.97
- [ ] Hard rule: total portfolio allocation ≤ 80%
- [ ] Hard rule: single ticker max 10% of portfolio
- [ ] Hard rule: single sector max 30% of portfolio
- [ ] Hard rule: no penny stocks (price < $5)
- [ ] Hard rule: no micro-caps (market cap < $1B)
- [ ] Hard rule: max 15 open positions
- [ ] Hard rule: no duplicate signals on same ticker within 2 hours
- [ ] Sector lookup queries Turso `sector_cache`, fetches via yfinance if missing and caches
- [ ] Returns `{approved, reason, position_size, shares, entry_price, stop_loss, take_profit, portfolio_allocation_pct}`

### executor/alpaca.py
- [ ] Checks market hours before any order submission
- [ ] Submits limit order at entry_price + 0.1% slippage buffer
- [ ] Bracket order (stop loss + take profit) or separate stop order after fill
- [ ] Returns `{order_id, symbol, filled_price, shares}` on success
- [ ] On failure: returns error message, does NOT retry in same cycle
- [ ] All orders arrive pre-approved from risk/manager.py (executor does not re-check risk)
- [ ] Checks circuit breaker status before placing any order

### feedback/logger.py
- [ ] `log_trade()` writes to Turso `trades` table
- [ ] Logs all fields: trade_id (UUID), ticker, signal, confidence, sentiment_score,
  sentiment_source, strategies_fired (JSON array), discovery_sources (JSON array),
  regime_mode, article_urls (JSON array), entry_price, shares, stop_loss_price,
  take_profit_price, order_id, created_at

### feedback/outcomes.py
- [ ] Runs as scheduled job every 4 hours and at market close
- [ ] Fetches current price from yfinance for each open trade
- [ ] Calculates return: (current_price - entry_price) / entry_price
- [ ] Closes position if stop loss or take profit hit
- [ ] Classifies: WIN (return > +1%), LOSS (return < -1%), NEUTRAL (between)
- [ ] Writes to Turso `outcomes` table with all fields

### feedback/weights.py
- [ ] WIN update: `new_weight = old_weight * 0.95 + 1.0 * 0.05`
- [ ] LOSS update: `new_weight = old_weight * 0.95 + 0.0 * 0.05`
- [ ] Weight clamped between 0.1 and 1.0
- [ ] Updates weights for both the strategy that fired and the news source
- [ ] Writes to Turso `weights` table
- [ ] Circuit breaker: calculates rolling 7-day win rate
- [ ] Circuit breaker trips if win rate < 40%
- [ ] On trip: halts all new trades, sends email/Slack alert, sets `tripped=True` in Turso
- [ ] Manual reset: `tripped=False` via dashboard or CLI

### dashboard/app.py
- [ ] Portfolio overview: cash, positions, total value, daily P&L
- [ ] Trade history table with signal metadata, outcome, return
- [ ] Active tickers panel: which tickers this cycle + discovery source per ticker
- [ ] Signal feed: current signals with confidence and strategy breakdown
- [ ] Weight table: learned weights per strategy and source (from Turso)
- [ ] Regime indicator: current mode with VIX, SPY vs 200MA, yield spread inputs
- [ ] Sector exposure chart: pie chart of allocation by sector
- [ ] Performance chart: portfolio value over time
- [ ] Circuit breaker status: green/red with manual override button
- [ ] Win rate chart: rolling 7-day with 40% threshold line
- [ ] Settings panel: toggle TICKER_MODE, edit WATCHLIST, adjust MAX_DISCOVERY_TICKERS
- [ ] All data queries hit Turso (no local file reads)

---

## Step 3 — Architectural Violation Scan

Scan every file for these violations. Flag any found:

| Violation | What to look for |
|-----------|-----------------|
| Hardcoded tickers outside discovery.py | List literals containing stock symbols: `["AAPL"`, `"MSFT"`, `DEFAULT_WATCHLIST`, `SP500_TICKERS` |
| Sentiment re-analysis of Marketaux data | Claude API call where `source == "marketaux"` |
| Raw headline sent to Claude | Claude call where input is `title` or `headline` field (not `full_text`) |
| Trade bypassing risk manager | Any `executor/alpaca.py` call not preceded by `risk/manager.py` check in the call chain |
| Deprecated module still imported | Any `from engine.signals import` or `import engine.signals` |
| Local file caching | `open(` / `json.dump` / `pickle` used for caching instead of Turso |
| Live trading URL | `api.alpaca.markets` (non-paper URL) anywhere in code |

---

## Step 4 — Write AUDIT.md

Write a fresh `AUDIT.md` to the project root. Use this format exactly:

```markdown
# Architecture Audit
Generated: <date and time>

> Fresh audit of current codebase state against CLAUDE.md specification.
> Completeness issues (partial implementations) are listed before missing files.

---

## Priority 1 — Incomplete Implementations
*Files that exist but are only partially built. These block the pipeline more
than missing files because they create false confidence.*

### <filename> — PARTIAL
- [x] <completed item>
- [ ] <incomplete item>
...

---

## Priority 2 — Full Stubs
*Files that exist but contain no real logic. All functions are `pass` or
`raise NotImplementedError`.*

### <filename> — STUB
- [ ] Implement <key function>
- [ ] Implement <key function>
...

---

## Priority 3 — Missing Files
*Files that do not exist at all.*

- [ ] Create `<filepath>` — <one-line description of what it needs to do>
...

---

## Priority 4 — Architectural Violations
*Files that violate CLAUDE.md hard rules. These must be fixed regardless of
implementation phase.*

### <filename>
- [ ] **VIOLATION**: <description of the violation and which CLAUDE.md rule it breaks>
...

If no violations found, write: "No architectural violations detected."

---

## Deprecated
*Files that should be deleted.*

- [ ] Delete `engine/signals.py` — superseded by `engine/combiner.py` and `engine/strategies.py`
  - [ ] First confirm no other file imports from it (grep result: <yes/no>)

---

## Implementation Phases

### Phase 1 — Foundation (nothing else works without these)
1. `db/client.py` — Turso connection manager
2. `db/schema.sql` — all 6 tables

### Phase 2 — Data Pipeline
3. `fetchers/market.py` — technical indicators for all strategies
4. `fetchers/discovery.py` — fix hardcoded ticker violation, implement dynamic discovery
5. `fetchers/aggregator.py` — merge + dedup all sources

### Phase 3 — Signal Engine
6. `engine/strategies.py` — all 4 strategies
7. `engine/regime.py` — macro regime classifier
8. `engine/combiner.py` — weighted signal combiner
9. `engine/sentiment.py` — Claude sentiment integration

### Phase 4 — Execution + Risk
10. `risk/manager.py` — all 7 hard rules + position sizing
11. `executor/alpaca.py` — paper order placement

### Phase 5 — Feedback Loop
12. `feedback/logger.py` — trade logging
13. `feedback/outcomes.py` — outcome measurement
14. `feedback/weights.py` — weight updates + circuit breaker

### Phase 6 — Orchestration + UI
15. `scheduler/loop.py` — full event loop
16. `dashboard/app.py` — complete UI
```

---

## Step 5 — Return Audit Summary

After writing AUDIT.md, return a brief summary to the user:

```
Audit complete → AUDIT.md written.

INCOMPLETE (partial implementations):  X file(s)
STUBS (no logic at all):               X file(s)
MISSING:                               X file(s)
VIOLATIONS:                            X issue(s)
DEPRECATED (needs deletion):           X file(s)

Highest priority: <one sentence — the most critical incomplete or missing item>
```
