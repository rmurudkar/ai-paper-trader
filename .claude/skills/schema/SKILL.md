---
name: schema
description: >
  Full Turso database schema for the autonomous paper trader. Load this when writing
  DB queries, adding new tables, running migrations, or debugging data issues.
  Trigger when the user mentions any table name, asks about what's stored in the DB,
  wants to write a SELECT/INSERT/UPDATE, or asks about the schema.
---

# Database Schema

Turso (distributed SQLite). Connection via `libsql_client.create_client_sync()`.
All helpers in `db/client.py`. Timestamps stored as ISO 8601 strings with Z suffix.
JSON arrays stored as TEXT (use json.dumps/json.loads).

---

## trades
Every executed paper trade with full signal attribution.

```sql
CREATE TABLE trades (
    trade_id        TEXT PRIMARY KEY,       -- UUID string
    ticker          TEXT NOT NULL,
    signal          TEXT NOT NULL,          -- "BUY" | "SELL"
    confidence      REAL NOT NULL,          -- 0.0–1.0
    sentiment_score REAL,                   -- -1.0 to 1.0, NULL if no sentiment
    sentiment_source TEXT,                  -- "marketaux"|"massive"|"newsapi"|NULL
    strategies_fired TEXT,                  -- JSON array e.g. ["sentiment_divergence","momentum"]
    discovery_sources TEXT,                 -- JSON array e.g. ["news","gainer"]
    regime_mode     TEXT,                   -- "risk_on"|"risk_off"|"neutral"
    article_urls    TEXT,                   -- JSON array of URLs that influenced signal
    entry_price     REAL NOT NULL,
    shares          INTEGER NOT NULL,
    stop_loss_price REAL,
    take_profit_price REAL,
    order_id        TEXT,                   -- Alpaca order ID
    created_at      TEXT NOT NULL           -- ISO 8601 UTC
);
```

**Key queries:**
```python
# Log a trade
db.execute("INSERT INTO trades (...) VALUES (...)", [...])

# Get open trades (no outcome yet)
db.execute("""
    SELECT t.* FROM trades t
    LEFT JOIN outcomes o ON t.trade_id = o.trade_id
    WHERE o.trade_id IS NULL AND t.order_id IS NOT NULL
    ORDER BY t.created_at ASC
""")

# Recent trades
db.execute("SELECT * FROM trades ORDER BY created_at DESC LIMIT ?", [limit])

# Count open positions
db.execute("""
    SELECT COUNT(*) FROM trades
    WHERE trade_id NOT IN (SELECT trade_id FROM outcomes)
""")
```

---

## outcomes
One row per closed trade. Written by feedback/outcomes.py.

```sql
CREATE TABLE outcomes (
    trade_id             TEXT PRIMARY KEY REFERENCES trades(trade_id),
    exit_price           REAL NOT NULL,
    return_pct           REAL NOT NULL,     -- e.g. 0.023 = +2.3%
    outcome              TEXT NOT NULL,     -- "WIN" | "LOSS" | "NEUTRAL"
    holding_period_hours REAL,
    measured_at          TEXT NOT NULL      -- ISO 8601 UTC
);
```

**Key queries:**
```python
# Rolling 7-day win rate
db.execute("""
    SELECT
        SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
        SUM(CASE WHEN outcome IN ('WIN','LOSS') THEN 1 ELSE 0 END) as total
    FROM outcomes
    WHERE measured_at >= datetime('now', '-7 days')
""")

# Daily win rate series (for chart)
db.execute("""
    SELECT DATE(measured_at) as day,
           SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END) as wins,
           COUNT(*) as total
    FROM outcomes
    WHERE measured_at >= datetime('now', '-30 days')
    GROUP BY DATE(measured_at)
    ORDER BY day
""")
```

---

## weights
Learned weights for strategies and news sources. Updated by EMA after every outcome.

```sql
CREATE TABLE weights (
    category    TEXT NOT NULL,    -- "strategy" | "source"
    name        TEXT NOT NULL,    -- e.g. "sentiment_divergence" | "marketaux"
    weight      REAL NOT NULL DEFAULT 0.5,  -- clamped 0.1–1.0
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (category, name)
);
```

**Strategy names**: `sentiment_divergence`, `multi_source_consensus`, `sentiment_momentum`,
`news_catalyst_drift`, `momentum`, `mean_reversion`

**Source names**: `marketaux`, `massive`, `newsapi`, `alpaca`, `polygon`

**Key queries:**
```python
# Get one weight (default 0.5 if missing)
db.execute("SELECT weight FROM weights WHERE category=? AND name=?", [category, name])

# Get all weights for a category
db.execute("SELECT name, weight FROM weights WHERE category=?", [category])

# Upsert weight
db.execute("""
    INSERT INTO weights (category, name, weight, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(category, name) DO UPDATE SET
        weight = excluded.weight,
        updated_at = excluded.updated_at
""", [category, name, new_weight, now])
```

---

## circuit_breaker
Single-row table (id=1 always). Controls whether trading is halted.

```sql
CREATE TABLE circuit_breaker (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    tripped          BOOLEAN NOT NULL DEFAULT 0,
    tripped_at       TEXT,           -- ISO 8601 UTC, NULL if not tripped
    reason           TEXT,           -- e.g. "7-day win rate fell to 32%"
    win_rate_at_trip REAL            -- win rate that triggered the halt
);
```

**Key queries:**
```python
# Check status
db.execute("SELECT tripped FROM circuit_breaker WHERE id = 1")

# Trip
db.execute("""
    INSERT INTO circuit_breaker (id, tripped, tripped_at, reason, win_rate_at_trip)
    VALUES (1, 1, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
        tripped=1, tripped_at=excluded.tripped_at,
        reason=excluded.reason, win_rate_at_trip=excluded.win_rate_at_trip
""", [now, reason, win_rate])

# Reset
db.execute("""
    UPDATE circuit_breaker
    SET tripped=0, tripped_at=NULL, reason=NULL, win_rate_at_trip=NULL
    WHERE id=1
""")
```

---

## sector_cache
Caches ticker sector info fetched from yfinance. 7-day TTL.

```sql
CREATE TABLE sector_cache (
    ticker      TEXT PRIMARY KEY,
    sector      TEXT NOT NULL,      -- GICS sector e.g. "Technology", "Healthcare"
    market_cap  REAL,               -- in dollars
    avg_volume  REAL,
    fetched_at  TEXT NOT NULL       -- ISO 8601 UTC
);
```

**Key queries:**
```python
# Read (with 7-day freshness check)
db.execute("""
    SELECT sector FROM sector_cache
    WHERE ticker=? AND fetched_at > datetime('now', '-7 days')
""", [ticker])

# Write / update
db.execute("""
    INSERT INTO sector_cache (ticker, sector, market_cap, avg_volume, fetched_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(ticker) DO UPDATE SET
        sector=excluded.sector, market_cap=excluded.market_cap,
        avg_volume=excluded.avg_volume, fetched_at=excluded.fetched_at
""", [ticker, sector, market_cap, avg_volume, now])
```

---

## sp500_cache
Single-row cache of the S&P 500 ticker list. Populated by fetchers/market.py.

```sql
CREATE TABLE sp500_cache (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    tickers     TEXT NOT NULL,   -- JSON array of ticker strings
    fetched_at  TEXT NOT NULL
);
```

**Key queries:**
```python
# Read
result = db.execute("SELECT tickers FROM sp500_cache WHERE id=1")
tickers = json.loads(result.rows[0][0]) if result.rows else None

# Write
db.execute("""
    INSERT INTO sp500_cache (id, tickers, fetched_at) VALUES (1, ?, ?)
    ON CONFLICT(id) DO UPDATE SET tickers=excluded.tickers, fetched_at=excluded.fetched_at
""", [json.dumps(tickers), now])
```

---

## sentiment_history
Per-ticker sentiment score saved each cycle. Used by `sentiment_momentum` strategy
to compute cycle-over-cycle delta.

```sql
CREATE TABLE sentiment_history (
    ticker          TEXT NOT NULL,
    sentiment_score REAL NOT NULL,
    article_count   INTEGER NOT NULL DEFAULT 0,
    recorded_at     TEXT NOT NULL     -- ISO 8601 UTC
);

CREATE INDEX idx_sentiment_history_ticker_time
    ON sentiment_history (ticker, recorded_at DESC);
```

**Key queries:**
```python
# Save current cycle score
db.execute("""
    INSERT INTO sentiment_history (ticker, sentiment_score, article_count, recorded_at)
    VALUES (?, ?, ?, ?)
""", [ticker, score, article_count, now])

# Get previous cycle score (skip most recent, get the one before it)
db.execute("""
    SELECT sentiment_score, article_count, recorded_at
    FROM sentiment_history
    WHERE ticker=?
    ORDER BY recorded_at DESC
    LIMIT 1 OFFSET 1
""", [ticker])
```

**Note**: This table grows indefinitely. Consider adding a cleanup job to prune rows
older than 30 days.

---

## discovery_log
Audit trail of every ticker discovered in every cycle. Used by dashboard and
feedback loop analysis.

```sql
CREATE TABLE discovery_log (
    cycle_id        TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    source          TEXT NOT NULL,   -- "news"|"gainer"|"loser"|"sector_rotation"|"position"|"watchlist"|"fallback"
    discovered_at   TEXT NOT NULL,   -- ISO 8601 UTC
    PRIMARY KEY (cycle_id, ticker, source)
);
```

**Key queries:**
```python
# Log discovery
db.execute("""
    INSERT INTO discovery_log (cycle_id, ticker, source, discovered_at)
    VALUES (?, ?, ?, ?)
""", [cycle_id, ticker, source, now])

# Get latest cycle entries
db.execute("""
    SELECT cycle_id, ticker, source, discovered_at
    FROM discovery_log
    ORDER BY discovered_at DESC
    LIMIT 200
""")

# Get all tickers for a specific cycle
db.execute("""
    SELECT ticker, source FROM discovery_log
    WHERE cycle_id=? ORDER BY ticker
""", [cycle_id])
```

---

## db/client.py Helper Reference

```python
# Connection
get_db()                                    # returns libsql_client sync client

# Sector cache
get_sector_from_cache(ticker)               # → str | None
cache_sector(ticker, sector, market_cap, avg_volume)

# Weights
get_weight(category, name, default=0.5)     # → float
set_weight(category, name, weight)          # clamps to [0.1, 1.0]
get_all_weights(category)                   # → Dict[str, float]
initialize_default_weights()               # seeds 0.5 for all strategies/sources

# Trades
log_trade(trade_id, ticker, signal, ...)    # full signature in client.py
get_trade(trade_id)                         # → Dict | None
get_recent_trades(limit=20)                 # → List[Dict]
get_open_trades_count()                     # → int

# Outcomes
log_outcome(trade_id, exit_price, return_pct, outcome, holding_period_hours)
get_outcome(trade_id)                       # → Dict | None

# Circuit breaker
is_circuit_breaker_tripped()               # → bool
trip_circuit_breaker(reason, win_rate)
reset_circuit_breaker()

# S&P 500 cache
get_cached_sp500()                          # → List[str] | None
save_sp500_cache(tickers)

# Sentiment history
save_sentiment_score(ticker, score, article_count)
get_previous_sentiment(ticker)              # → Dict | None (skips most recent row)

# Discovery log
log_discovery(cycle_id, ticker, source)
get_discoveries_for_cycle(cycle_id)         # → Dict[str, List[str]]

# Queries
get_recent_win_rate(days=7)                 # → float (0.0–1.0)
```
```

