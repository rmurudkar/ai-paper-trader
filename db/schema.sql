CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    signal TEXT NOT NULL,
    confidence REAL NOT NULL,
    sentiment_score REAL,
    sentiment_source TEXT,
    strategies_fired TEXT,
    discovery_sources TEXT,
    regime_mode TEXT,
    article_urls TEXT,
    entry_price REAL NOT NULL,
    shares INTEGER NOT NULL,
    stop_loss_price REAL,
    take_profit_price REAL,
    order_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outcomes (
    trade_id TEXT PRIMARY KEY REFERENCES trades(trade_id),
    exit_price REAL NOT NULL,
    return_pct REAL NOT NULL,
    outcome TEXT NOT NULL,
    holding_period_hours REAL,
    measured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS weights (
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.5,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (category, name)
);

CREATE TABLE IF NOT EXISTS circuit_breaker (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tripped BOOLEAN NOT NULL DEFAULT 0,
    tripped_at TEXT,
    reason TEXT,
    win_rate_at_trip REAL
);

CREATE TABLE IF NOT EXISTS sector_cache (
    ticker TEXT PRIMARY KEY,
    sector TEXT NOT NULL,
    market_cap REAL,
    avg_volume REAL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS discovery_log (
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (cycle_id, ticker, source)
);
