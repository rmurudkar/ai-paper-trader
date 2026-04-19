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

CREATE TABLE IF NOT EXISTS sp500_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    tickers TEXT NOT NULL,          -- JSON array of ticker symbols
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment_history (
    ticker TEXT NOT NULL,
    sentiment_score REAL NOT NULL,
    article_count INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sentiment_history_ticker_time
    ON sentiment_history (ticker, recorded_at DESC);

CREATE TABLE IF NOT EXISTS seen_articles (
    url TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_seen_articles_seen_at
    ON seen_articles (seen_at);

CREATE TABLE IF NOT EXISTS discovery_log (
    cycle_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    source TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY (cycle_id, ticker, source)
);

-- ============================================================================
-- THESIS-DRIVEN ARCHITECTURE TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS active_theses (
    id TEXT PRIMARY KEY,                    -- UUID string
    thesis_statement TEXT NOT NULL,         -- Human-readable investment thesis
    theme TEXT NOT NULL,                    -- Category: "earnings", "merger", "regulatory", etc.
    direction TEXT NOT NULL,                -- "bullish" | "bearish"
    mechanism TEXT,                         -- How the thesis should play out
    lifecycle_stage TEXT NOT NULL,          -- "emerging" | "developing" | "confirmed" | "consensus" | "expired"
    confidence_score REAL NOT NULL,         -- 0.0-1.0: thesis extraction confidence
    conviction_score REAL NOT NULL,         -- 0.0-1.0: accumulated evidence strength
    tickers TEXT NOT NULL,                  -- JSON array of implicated tickers
    sectors TEXT,                           -- JSON array of GICS sectors
    time_horizon TEXT NOT NULL,             -- "intraday" | "short_term" | "medium_term" | "long_term"
    created_at TEXT NOT NULL,               -- ISO 8601 UTC
    last_updated TEXT NOT NULL,             -- ISO 8601 UTC
    expires_at TEXT,                        -- ISO 8601 UTC, NULL for active theses
    thesis_history TEXT,                    -- JSON array of statement evolution
    evidence_count INTEGER NOT NULL DEFAULT 0,
    source_diversity INTEGER NOT NULL DEFAULT 0  -- Number of unique sources supporting
);

CREATE INDEX IF NOT EXISTS idx_active_theses_lifecycle_updated
    ON active_theses (lifecycle_stage, last_updated DESC);

CREATE INDEX IF NOT EXISTS idx_active_theses_tickers
    ON active_theses (tickers);

CREATE TABLE IF NOT EXISTS thesis_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id TEXT NOT NULL REFERENCES active_theses(id),
    article_url TEXT NOT NULL,              -- Source article URL
    source TEXT NOT NULL,                   -- "marketaux" | "massive" | "newsapi" | etc.
    published_at TEXT NOT NULL,             -- Article publication time, ISO 8601 UTC
    added_conviction REAL NOT NULL,         -- Conviction delta this evidence provided (-1.0 to +1.0)
    reasoning TEXT,                         -- Claude's reasoning for conviction change
    materiality TEXT NOT NULL,              -- "high" | "medium" | "low"
    added_at TEXT NOT NULL                  -- When evidence was added, ISO 8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_thesis_evidence_thesis_id
    ON thesis_evidence (thesis_id, added_at DESC);

CREATE INDEX IF NOT EXISTS idx_thesis_evidence_url
    ON thesis_evidence (article_url);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    article_url TEXT,                       -- NULL for aggregated scores
    sentiment_score REAL NOT NULL,          -- -1.0 to +1.0
    urgency TEXT,                           -- "breaking" | "developing" | "standard"
    materiality TEXT NOT NULL,              -- "high" | "medium" | "low"
    reasoning TEXT,                         -- Claude's sentiment reasoning
    source TEXT NOT NULL,                   -- Original article source
    recorded_at TEXT NOT NULL               -- ISO 8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_sentiment_scores_ticker_time
    ON sentiment_scores (ticker, recorded_at DESC);
