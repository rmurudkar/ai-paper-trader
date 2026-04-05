# Architecture Audit
Generated: 2026-04-05

> Fresh audit of current codebase state against CLAUDE.md specification.
> Completeness issues (partial implementations) are listed before missing files.

---

## Priority 1 — Incomplete Implementations
*Files that exist but are only partially built. These block the pipeline more
than missing files because they create false confidence.*

### fetchers/discovery.py — PARTIAL + VIOLATION
- [x] Returns dict with keys: `tickers`, `sources`, `mode`
- [x] Supports `TICKER_MODE=watchlist` mode
- [x] Supports `TICKER_MODE=discovery` mode
- [ ] News-driven discovery: tickers with 2+ mentions in last 4 hours — **uses hardcoded `SP500_TICKERS[:20]` as fallback sample instead of dynamic extraction**
- [ ] Market movers: top 5 gainers + top 5 losers fetched dynamically from yfinance — **references hardcoded `SP500_TICKERS` list instead**
- [ ] Sector rotation scan: top/bottom 2 sector ETFs with top 3 holdings fetched via yfinance — **uses hardcoded `SECTOR_ETF_HOLDINGS` dict with fixed holdings instead of live yfinance data**
- [x] Existing positions always included
- [x] Pinned WATCHLIST tickers always included in discovery mode
- [x] Dedup + cap at `MAX_DISCOVERY_TICKERS`
- [x] `sources` dict maps each ticker to list of source reasons
- [ ] **VIOLATION**: `SP500_TICKERS` hardcoded list at line 98 — dynamic yfinance scan required
- [ ] **VIOLATION**: `SECTOR_ETF_HOLDINGS` hardcoded dict at line 110 — holdings must be fetched from yfinance, not hardcoded

### fetchers/marketaux.py — PARTIAL
- [x] `fetch_news()` makes real API requests
- [x] Watchlist mode: fetches filtered to provided tickers
- [ ] `broad=True` parameter is accepted but **silently ignored** — function only reads `TICKER_MODE` env var, never uses the `broad` argument passed by caller
- [x] Extracts pre-built `sentiment_score` per ticker from API response
- [x] Returns all required fields: `title`, `ticker`, `sentiment_score`, `snippet`, `url`, `published_at`, `source`
- [x] `source` field set to `"marketaux"` on every item
- [x] Rate limiting logic implemented (80/100 daily request warning)

---

## Priority 2 — Full Stubs
*Files that exist but contain no real logic. All functions are `pass` or
`raise NotImplementedError`.*

### fetchers/market.py — STUB
- [ ] Fetch price, volume, 50MA, 200MA, RSI(14) per ticker via yfinance
- [ ] Fetch VIX (`^VIX`) for regime detection
- [ ] Fetch SPY price vs SPY 200MA
- [ ] Fetch 10yr-2yr yield spread
- [ ] Accept dynamic ticker list (no hardcoded tickers)
- [ ] Return dict keyed by ticker + separate macro indicators dict

### fetchers/aggregator.py — STUB
- [ ] Merge Marketaux + NewsAPI outputs
- [ ] Deduplicate by URL exact match
- [ ] Deduplicate by title similarity > 80%
- [ ] 4-step waterfall enrichment for NewsAPI articles (Polygon → Alpaca News → scraper → snippet)
- [ ] Return unified list sorted by `published_at` descending

### engine/sentiment.py — STUB
- [ ] Marketaux items: pass `sentiment_score` directly, skip Claude
- [ ] NewsAPI items: send `full_text` (not headline) to Claude
- [ ] Never send raw headlines to Claude
- [ ] Truncate text to 1200 words before Claude call
- [ ] Return sentiment score -1.0 to 1.0 per ticker per article
- [ ] Tag sentiment with source for feedback loop attribution

### engine/strategies.py — STUB
- [ ] `momentum_signal()`: BUY if price > 20MA > 50MA, SELL if price < 20MA < 50MA
- [ ] `mean_reversion_signal()`: BUY if RSI < 30 on green day, SELL if RSI > 70 on red day
- [ ] `ma_crossover_signal()`: BUY if 20MA crosses above 50MA, SELL if crosses below
- [ ] `volume_surge_signal()`: BUY if volume > 1.5x avg + price up, SELL if surge + price down
- [ ] Each strategy returns `{signal, confidence, strategy, reason}`
- [ ] Confidence scales with signal strength (MA crossover fixed at 0.8, others variable)

### engine/regime.py — STUB
- [ ] Classify regime as `risk_on`, `risk_off`, or `neutral`
- [ ] Risk-on: VIX < 20, SPY > 200MA, yield spread > 0.5%
- [ ] Risk-off: VIX > 25, SPY < 200MA, yield spread < -0.5%
- [ ] Macro sentiment from NewsAPI articles can override neutral → risk_off
- [ ] Return `{regime, vix, spy_vs_200ma, yield_spread, macro_sentiment, confidence}`

### engine/combiner.py — STUB
- [ ] Collect all strategy signals per ticker
- [ ] Read learned weights from Turso `weights` table
- [ ] Apply regime modifier (risk_on: SELL -20%, risk_off: BUY -30%)
- [ ] Include sentiment as separate signal with learned weight
- [ ] BUY threshold > 0.55, SELL threshold < 0.45, HOLD between
- [ ] Return `{ticker, signal, confidence, components, regime, rationale}` per ticker

### risk/manager.py — STUB
- [ ] Position size = risk_amount / (entry_price - stop_loss_price), capped at 500 shares
- [ ] BUY stop = entry_price × 0.97, SELL stop = entry_price × 1.03
- [ ] BUY take profit = entry_price × 1.03, SELL take profit = entry_price × 0.97
- [ ] Hard rule: total portfolio allocation ≤ 80%
- [ ] Hard rule: single ticker max 10% of portfolio
- [ ] Hard rule: single sector max 30% of portfolio
- [ ] Hard rule: no penny stocks (price < $5)
- [ ] Hard rule: no micro-caps (market cap < $1B)
- [ ] Hard rule: max 15 open positions
- [ ] Hard rule: no duplicate signals on same ticker within 2 hours
- [ ] Sector lookup queries Turso `sector_cache`, fetches via yfinance if missing
- [ ] Return `{approved, reason, position_size, shares, entry_price, stop_loss, take_profit, portfolio_allocation_pct}`

### executor/alpaca.py — STUB
- [ ] Check market hours before any order submission
- [ ] Submit limit order at entry_price + 0.1% slippage buffer
- [ ] Bracket order (stop loss + take profit) or separate stop order after fill
- [ ] Return `{order_id, symbol, filled_price, shares}` on success
- [ ] On failure: return error, do NOT retry in same cycle
- [ ] Check circuit breaker status before placing any order

### feedback/logger.py — STUB
- [ ] `log_trade()` writes to Turso `trades` table
- [ ] Log all 15 required fields including `strategies_fired` and `article_urls` as JSON arrays

### feedback/outcomes.py — STUB
- [ ] Fetch current price from yfinance for each open trade
- [ ] Calculate return: (current_price - entry_price) / entry_price
- [ ] Close position if stop loss or take profit hit
- [ ] Classify: WIN (> +1%), LOSS (< -1%), NEUTRAL (between)
- [ ] Write to Turso `outcomes` table

### feedback/weights.py — STUB
- [ ] WIN: `new_weight = old_weight * 0.95 + 1.0 * 0.05`
- [ ] LOSS: `new_weight = old_weight * 0.95 + 0.0 * 0.05`
- [ ] Clamp weight between 0.1 and 1.0
- [ ] Update weights for strategy that fired and news source
- [ ] Circuit breaker: rolling 7-day win rate check
- [ ] Trip if win rate < 40%, send email/Slack alert, set `tripped=True` in Turso

### scheduler/loop.py — STUB
- [ ] Primary job: every 15 minutes during market hours (9:30 AM–4:00 PM ET)
- [ ] Pre-market job: 9:00 AM ET
- [ ] Post-market job: 4:30 PM ET
- [ ] Weekend/holiday skip
- [ ] `is_market_open()` checks Alpaca calendar API
- [ ] Circuit breaker check before each trading cycle
- [ ] Discovery runs as first step in each cycle
- [ ] Log each cycle: timestamp, tickers scanned, signals generated, orders placed

### dashboard/app.py — STUB
- [ ] Portfolio overview: cash, positions, total value, daily P&L
- [ ] Trade history table with signal metadata, outcome, return
- [ ] Active tickers panel: tickers this cycle + discovery source per ticker
- [ ] Signal feed with confidence and strategy breakdown
- [ ] Weight table from Turso
- [ ] Regime indicator with VIX, SPY vs 200MA, yield spread
- [ ] Sector exposure pie chart
- [ ] Circuit breaker status with manual override button
- [ ] Win rate chart with 40% threshold line
- [ ] Settings panel: TICKER_MODE, WATCHLIST, MAX_DISCOVERY_TICKERS

---

## Priority 3 — Missing Files
*Files that do not exist at all.*

- [ ] Create `db/client.py` — Turso connection manager (`get_db()` returning libsql_client sync client)
- [ ] Create `db/schema.sql` — all 6 tables: trades, outcomes, weights, circuit_breaker, sector_cache, discovery_log

---

## Priority 4 — Architectural Violations
*Files that violate CLAUDE.md hard rules. These must be fixed regardless of implementation phase.*

### fetchers/discovery.py
- [ ] **VIOLATION** (line 98): `SP500_TICKERS = [...]` — hardcoded list of 50 tickers violates "NEVER hardcode ticker lists in any module except discovery.py... all downstream modules receive tickers dynamically." The tickers must come from dynamic yfinance scans and news extraction, not a static list.
- [ ] **VIOLATION** (line 110): `SECTOR_ETF_HOLDINGS = {...}` — hardcoded sector holdings dict. Top 3 holdings per ETF must be fetched from yfinance at runtime, not hardcoded.
- [ ] **VIOLATION** (line 461): `sample_tickers = SP500_TICKERS[:20]` — discovery falls back to a static 20-ticker sample, undermining the entire dynamic discovery architecture.

### fetchers/polygon.py
- [ ] **VIOLATION** (line 16): `DEFAULT_WATCHLIST = ['AAPL', 'MSFT', 'NVDA', ...]` — hardcoded ticker list used as default in `_fetch_general_news()` at line 47. This module is not in the CLAUDE.md architecture — evaluate whether it's needed or should be removed.

### dashboard/app.py
- [ ] **VIOLATION** (line 5): `from engine import signals` — imports from the deprecated `engine/signals.py` module which is superseded by `engine/combiner.py` and `engine/strategies.py`. Will cause import errors once `engine/signals.py` is deleted.

---

## Deprecated
*Files that should be deleted.*

- [ ] Delete `engine/signals.py` — superseded by `engine/combiner.py` and `engine/strategies.py`
  - [x] Confirmed no other file imports from it (grep found no `from engine.signals` or `import engine.signals` in any module — only `dashboard/app.py` uses `from engine import signals` which is the violation above)

---

## Complete — No Action Needed

- `fetchers/newsapi.py` — COMPLETE. Full EventRegistry integration, multi-topic fetching, ticker extraction in discovery mode, watchlist filtering. Sets `needs_full_text=True` for aggregator enrichment.
- `fetchers/scraper.py` — COMPLETE. Full 3-tier fallback (trafilatura → newspaper3k → BeautifulSoup), paywall detection, 1200-word truncation, `{full_text, partial}` return shape.

---

## Implementation Phases

### Phase 1 — Foundation (nothing else works without these)
1. `db/client.py` — Turso connection manager
2. `db/schema.sql` — all 6 tables

### Phase 2 — Data Pipeline
3. `fetchers/market.py` — price, volume, RSI, MAs, VIX, yield spread
4. `fetchers/discovery.py` — fix hardcoded violations, implement dynamic yfinance discovery
5. `fetchers/aggregator.py` — merge + 4-step waterfall enrichment
6. Fix `fetchers/marketaux.py` — honor the `broad` parameter

### Phase 3 — Signal Engine
7. `engine/strategies.py` — all 4 strategies
8. `engine/regime.py` — macro regime classifier
9. `engine/combiner.py` — weighted signal combiner with Turso weight reads
10. `engine/sentiment.py` — Claude sentiment integration

### Phase 4 — Execution + Risk
11. `risk/manager.py` — all 7 hard rules + position sizing + sector cache
12. `executor/alpaca.py` — paper order placement with bracket orders

### Phase 5 — Feedback Loop
13. `feedback/logger.py` — trade logging to Turso
14. `feedback/outcomes.py` — outcome measurement
15. `feedback/weights.py` — EMA weight updates + circuit breaker

### Phase 6 — Orchestration + UI
16. `scheduler/loop.py` — full APScheduler event loop
17. `dashboard/app.py` — complete Streamlit UI, fix deprecated import

### Cleanup
18. Delete `engine/signals.py`
19. Fix or remove `fetchers/polygon.py` (not in CLAUDE.md architecture, has hardcoded watchlist)
