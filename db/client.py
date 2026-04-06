"""
Turso database client and utilities.

Provides connection management, caching helpers, and common query patterns
used throughout the trading app.
"""

import os
import json
import logging
from datetime import datetime
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

import libsql_client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


def get_db() -> libsql_client.Client:
    """Get or create Turso database connection."""
    url = os.getenv("TURSO_CONNECTION_URL")
    auth_token = os.getenv("TURSO_AUTH_TOKEN")

    if not url or not auth_token:
        raise ValueError(
            "TURSO_CONNECTION_URL and TURSO_AUTH_TOKEN must be set in .env"
        )

    return libsql_client.create_client_sync(url=url, auth_token=auth_token)


@contextmanager
def db_transaction():
    """Context manager for database transactions."""
    db = get_db()
    try:
        db.execute("BEGIN")
        yield db
        db.execute("COMMIT")
    except Exception as e:
        db.execute("ROLLBACK")
        logger.error(f"Transaction failed: {e}")
        raise


# ============================================================================
# SECTOR CACHE UTILITIES
# ============================================================================


def get_sector_from_cache(ticker: str) -> Optional[str]:
    """
    Fetch sector from cache for a given ticker.

    Returns None if not cached. Fetched sectors are assumed to be fresh
    for the current day.
    """
    db = get_db()
    result = db.execute(
        "SELECT sector FROM sector_cache WHERE ticker = ?",
        [ticker],
    )
    if result.rows:
        return result.rows[0][0]
    return None


def cache_sector(ticker: str, sector: str, market_cap: Optional[float] = None,
                avg_volume: Optional[float] = None) -> None:
    """
    Cache sector information for a ticker.

    Args:
        ticker: Stock ticker symbol
        sector: Sector name (e.g., "Technology", "Healthcare")
        market_cap: Optional market cap in billions
        avg_volume: Optional average daily volume
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"
    db.execute(
        """
        INSERT INTO sector_cache (ticker, sector, market_cap, avg_volume, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            sector = excluded.sector,
            market_cap = excluded.market_cap,
            avg_volume = excluded.avg_volume,
            fetched_at = excluded.fetched_at
        """,
        [ticker, sector, market_cap, avg_volume, now],
    )
    logger.debug(f"Cached sector for {ticker}: {sector}")


# ============================================================================
# WEIGHT UTILITIES (for feedback loop)
# ============================================================================


def get_weight(category: str, name: str, default: float = 0.5) -> float:
    """
    Get learned weight for a strategy or source.

    Args:
        category: "strategy" or "source"
        name: e.g., "momentum" or "marketaux"
        default: Value to return if weight not found

    Returns:
        Weight value between 0.1 and 1.0
    """
    db = get_db()
    result = db.execute(
        "SELECT weight FROM weights WHERE category = ? AND name = ?",
        [category, name],
    )
    if result.rows:
        return result.rows[0][0]
    return default


def set_weight(category: str, name: str, weight: float) -> None:
    """
    Set or update a weight value.

    Args:
        category: "strategy" or "source"
        name: e.g., "momentum" or "marketaux"
        weight: Value between 0.0 and 1.0 (will be clamped)
    """
    db = get_db()
    weight = max(0.1, min(1.0, weight))  # Clamp between 0.1 and 1.0
    now = datetime.utcnow().isoformat() + "Z"

    db.execute(
        """
        INSERT INTO weights (category, name, weight, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(category, name) DO UPDATE SET
            weight = excluded.weight,
            updated_at = excluded.updated_at
        """,
        [category, name, weight, now],
    )
    logger.debug(f"Updated weight {category}/{name} to {weight}")


def get_all_weights(category: str) -> Dict[str, float]:
    """
    Get all weights for a category.

    Args:
        category: "strategy" or "source"

    Returns:
        Dict mapping name -> weight
    """
    db = get_db()
    result = db.execute(
        "SELECT name, weight FROM weights WHERE category = ?",
        [category],
    )
    return {row[0]: row[1] for row in result.rows}


def initialize_default_weights() -> None:
    """
    Initialize default weights if they don't exist.

    Called once at startup to ensure weight table is populated.
    """
    strategies = ["momentum", "mean_reversion", "ma_crossover", "volume_surge"]
    sources = ["marketaux", "newsapi", "sentiment"]

    for strategy in strategies:
        if get_weight("strategy", strategy) == 0.5:  # Default, not set
            set_weight("strategy", strategy, 0.5)

    for source in sources:
        if get_weight("source", source) == 0.5:
            set_weight("source", source, 0.5)

    logger.info("Initialized default weights")


# ============================================================================
# TRADE LOGGING UTILITIES
# ============================================================================


def log_trade(
    trade_id: str,
    ticker: str,
    signal: str,
    confidence: float,
    sentiment_score: Optional[float] = None,
    sentiment_source: Optional[str] = None,
    strategies_fired: Optional[List[str]] = None,
    discovery_sources: Optional[List[str]] = None,
    regime_mode: Optional[str] = None,
    article_urls: Optional[List[str]] = None,
    entry_price: float = 0.0,
    shares: int = 0,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    order_id: Optional[str] = None,
) -> None:
    """
    Log a trade to the trades table.

    Args:
        trade_id: Unique trade identifier (UUID)
        ticker: Stock ticker
        signal: "BUY" or "SELL"
        confidence: Confidence 0.0-1.0
        sentiment_score: Optional sentiment -1.0 to 1.0
        sentiment_source: "marketaux", "newsapi", or None
        strategies_fired: List of strategy names that contributed
        discovery_sources: List of discovery sources for this ticker
        regime_mode: "risk_on", "risk_off", or "neutral"
        article_urls: List of article URLs that influenced the signal
        entry_price: Entry price
        shares: Position size
        stop_loss_price: Stop loss level
        take_profit_price: Take profit level
        order_id: Alpaca order ID
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"

    db.execute(
        """
        INSERT INTO trades (
            trade_id, ticker, signal, confidence,
            sentiment_score, sentiment_source, strategies_fired, discovery_sources,
            regime_mode, article_urls, entry_price, shares,
            stop_loss_price, take_profit_price, order_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            trade_id,
            ticker,
            signal,
            confidence,
            sentiment_score,
            sentiment_source,
            json.dumps(strategies_fired or []),
            json.dumps(discovery_sources or []),
            regime_mode,
            json.dumps(article_urls or []),
            entry_price,
            shares,
            stop_loss_price,
            take_profit_price,
            order_id,
            now,
        ],
    )
    logger.info(f"Logged trade {trade_id}: {signal} {shares} @ {entry_price}")


def get_trade(trade_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve a trade by ID.

    Returns:
        Trade dict or None if not found
    """
    db = get_db()
    result = db.execute(
        "SELECT * FROM trades WHERE trade_id = ?",
        [trade_id],
    )
    if not result.rows:
        return None

    columns = [
        "trade_id", "ticker", "signal", "confidence",
        "sentiment_score", "sentiment_source", "strategies_fired", "discovery_sources",
        "regime_mode", "article_urls", "entry_price", "shares",
        "stop_loss_price", "take_profit_price", "order_id", "created_at"
    ]
    row = result.rows[0]
    trade = dict(zip(columns, row))
    trade["strategies_fired"] = json.loads(trade["strategies_fired"] or "[]")
    trade["discovery_sources"] = json.loads(trade["discovery_sources"] or "[]")
    trade["article_urls"] = json.loads(trade["article_urls"] or "[]")
    return trade


# ============================================================================
# OUTCOME LOGGING UTILITIES
# ============================================================================


def log_outcome(
    trade_id: str,
    exit_price: float,
    return_pct: float,
    outcome: str,
    holding_period_hours: Optional[float] = None,
) -> None:
    """
    Log outcome (exit) for a trade.

    Args:
        trade_id: Reference to trades.trade_id
        exit_price: Price at exit
        return_pct: Return percentage (-100 to +∞)
        outcome: "WIN", "LOSS", or "NEUTRAL"
        holding_period_hours: How long the position was held
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"

    db.execute(
        """
        INSERT INTO outcomes (
            trade_id, exit_price, return_pct, outcome, holding_period_hours, measured_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [trade_id, exit_price, return_pct, outcome, holding_period_hours, now],
    )
    logger.info(f"Logged outcome for {trade_id}: {outcome} ({return_pct:+.2f}%)")


def get_outcome(trade_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve outcome for a trade.

    Returns:
        Outcome dict or None if not found
    """
    db = get_db()
    result = db.execute(
        "SELECT * FROM outcomes WHERE trade_id = ?",
        [trade_id],
    )
    if not result.rows:
        return None

    columns = ["trade_id", "exit_price", "return_pct", "outcome", "holding_period_hours", "measured_at"]
    row = result.rows[0]
    return dict(zip(columns, row))


# ============================================================================
# CIRCUIT BREAKER UTILITIES
# ============================================================================


def is_circuit_breaker_tripped() -> bool:
    """Check if circuit breaker is active (trading halted)."""
    db = get_db()
    result = db.execute("SELECT tripped FROM circuit_breaker WHERE id = 1")
    if result.rows:
        return bool(result.rows[0][0])
    return False


def trip_circuit_breaker(reason: str, win_rate: float) -> None:
    """
    Trip the circuit breaker (halt trading).

    Args:
        reason: Why it tripped (e.g., "7-day win rate fell below 40%")
        win_rate: The win rate at the time of trip
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"

    db.execute(
        """
        INSERT INTO circuit_breaker (id, tripped, tripped_at, reason, win_rate_at_trip)
        VALUES (1, 1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            tripped = 1,
            tripped_at = excluded.tripped_at,
            reason = excluded.reason,
            win_rate_at_trip = excluded.win_rate_at_trip
        """,
        [now, reason, win_rate],
    )
    logger.warning(f"Circuit breaker tripped: {reason}")


def reset_circuit_breaker() -> None:
    """Reset the circuit breaker (resume trading)."""
    db = get_db()

    db.execute(
        """
        UPDATE circuit_breaker
        SET tripped = 0, tripped_at = NULL, reason = NULL, win_rate_at_trip = NULL
        WHERE id = 1
        """,
    )
    logger.info("Circuit breaker reset")


# ============================================================================
# S&P 500 CACHE UTILITIES
# ============================================================================


def get_cached_sp500() -> Optional[List[str]]:
    """Return the last successfully fetched S&P 500 ticker list, or None if never cached."""
    try:
        db = get_db()
        result = db.execute("SELECT tickers FROM sp500_cache WHERE id = 1")
        if result.rows:
            return json.loads(result.rows[0][0])
    except Exception as e:
        logger.warning(f"Could not read sp500_cache: {e}")
    return None


def save_sp500_cache(tickers: List[str]) -> None:
    """Persist the S&P 500 ticker list to Turso for use as a fallback."""
    try:
        db = get_db()
        now = datetime.utcnow().isoformat() + "Z"
        db.execute(
            """
            INSERT INTO sp500_cache (id, tickers, fetched_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                tickers = excluded.tickers,
                fetched_at = excluded.fetched_at
            """,
            [json.dumps(tickers), now],
        )
        logger.debug(f"Saved {len(tickers)} S&P 500 tickers to cache")
    except Exception as e:
        logger.warning(f"Could not save sp500_cache: {e}")


# ============================================================================
# SENTIMENT HISTORY UTILITIES
# ============================================================================


def save_sentiment_score(ticker: str, sentiment_score: float, article_count: int = 0) -> None:
    """Record a sentiment score for a ticker at the current cycle.

    Called once per ticker per cycle after sentiment analysis completes.
    The sentiment_momentum strategy compares the latest score against the
    previous cycle's score to detect narrative shifts.
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"
    db.execute(
        "INSERT INTO sentiment_history (ticker, sentiment_score, article_count, recorded_at) "
        "VALUES (?, ?, ?, ?)",
        [ticker, sentiment_score, article_count, now],
    )
    logger.debug(f"Saved sentiment for {ticker}: {sentiment_score} ({article_count} articles)")


def get_previous_sentiment(ticker: str) -> Optional[Dict[str, Any]]:
    """Get the most recent *prior* sentiment score for a ticker.

    Skips the newest row (current cycle) and returns the one before it,
    so the strategy can compute the delta between cycles.

    Returns:
        Dict with sentiment_score, article_count, recorded_at — or None.
    """
    db = get_db()
    result = db.execute(
        "SELECT sentiment_score, article_count, recorded_at "
        "FROM sentiment_history "
        "WHERE ticker = ? "
        "ORDER BY recorded_at DESC "
        "LIMIT 1 OFFSET 1",
        [ticker],
    )
    if result.rows:
        row = result.rows[0]
        return {
            "sentiment_score": row[0],
            "article_count": row[1],
            "recorded_at": row[2],
        }
    return None


# ============================================================================
# DISCOVERY LOG UTILITIES
# ============================================================================


def log_discovery(cycle_id: str, ticker: str, source: str) -> None:
    """
    Log a ticker discovery for audit trail.

    Args:
        cycle_id: Unique cycle identifier
        ticker: Stock ticker
        source: "news", "gainer", "loser", "sector_rotation", "position", "watchlist"
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"

    db.execute(
        """
        INSERT INTO discovery_log (cycle_id, ticker, source, discovered_at)
        VALUES (?, ?, ?, ?)
        """,
        [cycle_id, ticker, source, now],
    )


def get_discoveries_for_cycle(cycle_id: str) -> Dict[str, List[str]]:
    """
    Get all discoveries for a cycle, organized by ticker.

    Returns:
        Dict mapping ticker -> list of sources
    """
    db = get_db()
    result = db.execute(
        "SELECT ticker, source FROM discovery_log WHERE cycle_id = ? ORDER BY ticker",
        [cycle_id],
    )

    discoveries = {}
    for ticker, source in result.rows:
        if ticker not in discoveries:
            discoveries[ticker] = []
        discoveries[ticker].append(source)

    return discoveries


# ============================================================================
# QUERY UTILITIES
# ============================================================================


def get_recent_trades(limit: int = 20) -> List[Dict[str, Any]]:
    """Get most recent trades."""
    db = get_db()
    result = db.execute(
        """
        SELECT * FROM trades
        ORDER BY created_at DESC
        LIMIT ?
        """,
        [limit],
    )

    columns = [
        "trade_id", "ticker", "signal", "confidence",
        "sentiment_score", "sentiment_source", "strategies_fired", "discovery_sources",
        "regime_mode", "article_urls", "entry_price", "shares",
        "stop_loss_price", "take_profit_price", "order_id", "created_at"
    ]

    trades = []
    for row in result.rows:
        trade = dict(zip(columns, row))
        trade["strategies_fired"] = json.loads(trade["strategies_fired"] or "[]")
        trade["discovery_sources"] = json.loads(trade["discovery_sources"] or "[]")
        trade["article_urls"] = json.loads(trade["article_urls"] or "[]")
        trades.append(trade)

    return trades


def get_open_trades_count() -> int:
    """Count trades without outcomes (still open)."""
    db = get_db()
    result = db.execute(
        """
        SELECT COUNT(*) FROM trades
        WHERE trade_id NOT IN (SELECT trade_id FROM outcomes)
        """,
    )
    return result.rows[0][0] if result.rows else 0


def get_recent_win_rate(days: int = 7) -> float:
    """
    Calculate win rate over the last N days.

    Returns:
        Win rate between 0.0 and 1.0, or 0.0 if no outcomes
    """
    db = get_db()
    cutoff = datetime.utcnow().isoformat() + "Z"

    result = db.execute(
        """
        SELECT
            SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
            COUNT(*) as total
        FROM outcomes
        WHERE measured_at >= datetime('now', '-' || ? || ' days')
        """,
        [days],
    )

    if result.rows:
        wins, total = result.rows[0]
        if total and total > 0:
            return wins / total if wins else 0.0
    return 0.0
