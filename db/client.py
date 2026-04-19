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

_db_client: Optional[libsql_client.Client] = None
_db_init_error: Optional[Exception] = None


def get_db() -> libsql_client.Client:
    """Get the shared Turso database client (singleton).

    On first call, creates the client. On failure, caches the error and
    re-raises it on all subsequent calls — prevents leaking event loops
    from repeated failed create_client_sync() calls.
    """
    global _db_client, _db_init_error

    if _db_client is not None:
        return _db_client

    if _db_init_error is not None:
        raise _db_init_error

    url = os.getenv("TURSO_CONNECTION_URL")
    auth_token = os.getenv("TURSO_AUTH_TOKEN")

    if not url or not auth_token:
        _db_init_error = ValueError(
            "TURSO_CONNECTION_URL and TURSO_AUTH_TOKEN must be set in .env"
        )
        logger.error(f"[DB] {_db_init_error}")
        raise _db_init_error

    logger.debug(f"[DB] Creating connection to {url}")
    try:
        _db_client = libsql_client.create_client_sync(url=url, auth_token=auth_token)
        logger.debug(f"[DB] Connection created successfully, type={type(_db_client)}")
        return _db_client
    except Exception as e:
        logger.error(f"[DB] Failed to create client: {type(e).__name__}: {e}")
        _db_init_error = e
        raise


def reset_db() -> None:
    """Reset the singleton so get_db() will retry on next call.

    Use this if you want to recover after a transient network error.
    """
    global _db_client, _db_init_error
    if _db_client is not None:
        try:
            _db_client.close()
        except Exception:
            pass
    _db_client = None
    _db_init_error = None


def close_db() -> None:
    """Close the shared database client. Call on app shutdown."""
    global _db_client
    if _db_client is not None:
        try:
            _db_client.close()
        except Exception:
            pass
        _db_client = None


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
    logger.debug(f"[DB] save_sentiment_score called: ticker={ticker}, score={sentiment_score}, articles={article_count}")
    try:
        db = get_db()
        logger.debug(f"[DB] Got database connection for {ticker}")
        now = datetime.utcnow().isoformat() + "Z"
        logger.debug(f"[DB] Executing INSERT for {ticker}: timestamp={now}")
        result = db.execute(
            "INSERT INTO sentiment_history (ticker, sentiment_score, article_count, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            [ticker, sentiment_score, article_count, now],
        )
        logger.debug(f"[DB] INSERT executed for {ticker}, result type={type(result)}")
        logger.debug(f"Saved sentiment for {ticker}: {sentiment_score} ({article_count} articles)")
    except Exception as e:
        logger.error(f"[DB] save_sentiment_score exception for {ticker}: {type(e).__name__}: {e}")
        raise


def get_previous_sentiment(ticker: str) -> Optional[Dict[str, Any]]:
    """Get the most recent *prior* sentiment score for a ticker.

    Skips the newest row (current cycle) and returns the one before it,
    so the strategy can compute the delta between cycles.

    Returns:
        Dict with sentiment_score, article_count, recorded_at — or None.
    """
    logger.debug(f"[DB] get_previous_sentiment called for {ticker}")
    try:
        db = get_db()
        logger.debug(f"[DB] Got database connection for {ticker}")
        logger.debug(f"[DB] Executing SELECT for {ticker}")
        result = db.execute(
            "SELECT sentiment_score, article_count, recorded_at "
            "FROM sentiment_history "
            "WHERE ticker = ? "
            "ORDER BY recorded_at DESC "
            "LIMIT 1 OFFSET 1",
            [ticker],
        )
        logger.debug(f"[DB] SELECT executed for {ticker}, result type={type(result)}, has .rows={hasattr(result, 'rows')}")
        if result.rows:
            logger.debug(f"[DB] Found {len(result.rows)} rows for {ticker}")
            row = result.rows[0]
            logger.debug(f"[DB] Row data for {ticker}: {row}")
            return {
                "sentiment_score": row[0],
                "article_count": row[1],
                "recorded_at": row[2],
            }
        logger.debug(f"[DB] No rows found for {ticker}")
        return None
    except Exception as e:
        logger.error(f"[DB] get_previous_sentiment exception for {ticker}: {type(e).__name__}: {e}")
        raise


# ============================================================================
# SEEN ARTICLES DEDUPLICATION
# ============================================================================


def filter_unseen_articles(articles: List[Dict], ttl_hours: int = 8) -> List[Dict]:
    """Return only articles whose URLs haven't been seen within ttl_hours.

    Articles without a URL are always passed through. Seen URLs are looked up
    in bulk so this is one DB round-trip regardless of article count.

    Args:
        articles: Article dicts from the aggregator.
        ttl_hours: How long a URL is considered "seen". Default 8h covers a
                   full trading session so articles aren't re-analyzed intra-day,
                   but fresh articles appear the next morning.

    Returns:
        Subset of articles not yet seen.
    """
    if not articles:
        return []

    try:
        db = get_db()
        result = db.execute(
            "SELECT url FROM seen_articles "
            "WHERE seen_at >= datetime('now', ? || ' hours')",
            [f"-{ttl_hours}"],
        )
        seen_urls = {row[0] for row in result.rows}
    except Exception as e:
        logger.warning(f"Could not read seen_articles, processing all articles: {e}")
        return articles

    unseen = [a for a in articles if not a.get("url") or a["url"] not in seen_urls]
    logger.debug(f"seen_articles filter: {len(articles)} total, {len(articles) - len(unseen)} skipped, {len(unseen)} new")
    return unseen


def mark_articles_seen(articles: List[Dict]) -> None:
    """Record article URLs as seen so future cycles skip them.

    Uses INSERT OR IGNORE so re-marking an already-seen URL is a no-op
    (the original seen_at timestamp is preserved).

    Args:
        articles: Article dicts to mark. Articles without a URL are skipped.
    """
    if not articles:
        return

    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"
    marked = 0
    for article in articles:
        url = article.get("url")
        if url:
            try:
                db.execute(
                    "INSERT OR IGNORE INTO seen_articles (url, seen_at) VALUES (?, ?)",
                    [url, now],
                )
                marked += 1
            except Exception as e:
                logger.debug(f"Could not mark article seen ({url}): {e}")

    logger.debug(f"Marked {marked} articles as seen")


def cleanup_seen_articles(max_age_hours: int = 24) -> int:
    """Delete seen_articles rows older than max_age_hours.

    Called once per cycle to keep the table from growing unbounded.

    Returns:
        Number of rows deleted.
    """
    try:
        db = get_db()
        db.execute(
            "DELETE FROM seen_articles WHERE seen_at < datetime('now', ? || ' hours')",
            [f"-{max_age_hours}"],
        )
        # libsql_client doesn't expose rowcount; log without it
        logger.debug(f"Cleaned up seen_articles older than {max_age_hours}h")
        return 0
    except Exception as e:
        logger.warning(f"Could not clean up seen_articles: {e}")
        return 0


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


# ============================================================================
# THESIS MANAGEMENT UTILITIES
# ============================================================================


def create_thesis(
    thesis_statement: str,
    theme: str,
    direction: str,
    tickers: List[str],
    time_horizon: str,
    confidence_score: float,
    conviction_score: float,
    mechanism: Optional[str] = None,
    sectors: Optional[List[str]] = None,
) -> str:
    """
    Create a new investment thesis.

    Args:
        thesis_statement: Human-readable investment thesis
        theme: Category like "earnings", "merger", "regulatory", etc.
        direction: "bullish" or "bearish"
        tickers: List of ticker symbols implicated in thesis
        time_horizon: "intraday", "short_term", "medium_term", "long_term"
        confidence_score: Claude's confidence in thesis extraction (0.0-1.0)
        conviction_score: Initial evidence strength (0.0-1.0)
        mechanism: Optional description of how thesis should play out
        sectors: Optional list of GICS sectors

    Returns:
        Thesis ID (UUID string)
    """
    import uuid

    db = get_db()
    thesis_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    db.execute(
        """
        INSERT INTO active_theses (
            id, thesis_statement, theme, direction, mechanism,
            lifecycle_stage, confidence_score, conviction_score,
            tickers, sectors, time_horizon, created_at, last_updated,
            thesis_history, evidence_count, source_diversity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            thesis_id, thesis_statement, theme, direction, mechanism,
            "emerging", confidence_score, conviction_score,
            json.dumps(tickers), json.dumps(sectors or []), time_horizon,
            now, now, json.dumps([thesis_statement]), 0, 0
        ]
    )

    logger.info(f"Created thesis {thesis_id}: {theme} - {direction}")
    return thesis_id


def get_active_theses(
    exclude_expired: bool = True,
    lifecycle_stages: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """
    Get active investment theses.

    Args:
        exclude_expired: If True, exclude expired theses
        lifecycle_stages: Optional filter by specific stages

    Returns:
        List of thesis dictionaries with tickers and sectors as parsed lists
    """
    db = get_db()

    where_conditions = []
    params = []

    if exclude_expired:
        where_conditions.append("lifecycle_stage != 'expired'")

    if lifecycle_stages:
        where_conditions.append(f"lifecycle_stage IN ({','.join(['?'] * len(lifecycle_stages))})")
        params.extend(lifecycle_stages)

    where_clause = f"WHERE {' AND '.join(where_conditions)}" if where_conditions else ""

    result = db.execute(
        f"""
        SELECT id, thesis_statement, theme, direction, mechanism,
               lifecycle_stage, confidence_score, conviction_score,
               tickers, sectors, time_horizon, created_at, last_updated,
               expires_at, thesis_history, evidence_count, source_diversity
        FROM active_theses
        {where_clause}
        ORDER BY last_updated DESC
        """,
        params
    )

    theses = []
    for row in result.rows:
        thesis = {
            "id": row[0],
            "thesis_statement": row[1],
            "theme": row[2],
            "direction": row[3],
            "mechanism": row[4],
            "lifecycle_stage": row[5],
            "confidence_score": row[6],
            "conviction_score": row[7],
            "tickers": json.loads(row[8]) if row[8] else [],
            "sectors": json.loads(row[9]) if row[9] else [],
            "time_horizon": row[10],
            "created_at": row[11],
            "last_updated": row[12],
            "expires_at": row[13],
            "thesis_history": json.loads(row[14]) if row[14] else [],
            "evidence_count": row[15],
            "source_diversity": row[16],
        }
        theses.append(thesis)

    return theses


def get_thesis_by_id(thesis_id: str) -> Optional[Dict[str, Any]]:
    """Get a specific thesis by ID."""
    theses = get_active_theses(exclude_expired=False)
    for thesis in theses:
        if thesis["id"] == thesis_id:
            return thesis
    return None


def find_similar_theses(
    tickers: List[str],
    theme: Optional[str] = None,
    direction: Optional[str] = None,
    min_ticker_overlap: int = 1
) -> List[Dict[str, Any]]:
    """
    Find active theses with overlapping tickers or matching themes.

    Args:
        tickers: Ticker list to match against
        theme: Optional theme filter
        direction: Optional direction filter
        min_ticker_overlap: Minimum number of overlapping tickers

    Returns:
        List of matching theses, sorted by overlap score descending
    """
    active_theses = get_active_theses(exclude_expired=True)
    ticker_set = set(t.upper() for t in tickers)

    matches = []
    for thesis in active_theses:
        thesis_tickers = set(t.upper() for t in thesis["tickers"])
        overlap = len(ticker_set & thesis_tickers)

        # Skip if insufficient ticker overlap
        if overlap < min_ticker_overlap:
            continue

        # Apply optional filters
        if theme and thesis["theme"] != theme:
            continue
        if direction and thesis["direction"] != direction:
            continue

        thesis["overlap_score"] = overlap
        thesis["overlap_tickers"] = list(ticker_set & thesis_tickers)
        matches.append(thesis)

    # Sort by overlap score (descending), then by conviction
    matches.sort(key=lambda x: (x["overlap_score"], x["conviction_score"]), reverse=True)
    return matches


def update_thesis_conviction(
    thesis_id: str,
    conviction_delta: float,
    new_lifecycle_stage: Optional[str] = None
) -> bool:
    """
    Update thesis conviction score and optionally lifecycle stage.

    Args:
        thesis_id: Thesis to update
        conviction_delta: Change in conviction (-1.0 to +1.0)
        new_lifecycle_stage: Optional new lifecycle stage

    Returns:
        True if thesis was found and updated
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"

    # Get current thesis
    current = get_thesis_by_id(thesis_id)
    if not current:
        return False

    new_conviction = max(0.0, min(1.0, current["conviction_score"] + conviction_delta))

    updates = ["conviction_score = ?", "last_updated = ?"]
    params = [new_conviction, now]

    if new_lifecycle_stage:
        updates.append("lifecycle_stage = ?")
        params.append(new_lifecycle_stage)

    params.append(thesis_id)

    db.execute(
        f"UPDATE active_theses SET {', '.join(updates)} WHERE id = ?",
        params
    )

    logger.debug(f"Updated thesis {thesis_id}: conviction {current['conviction_score']:.3f} -> {new_conviction:.3f}")
    return True


def expire_stale_theses(max_age_hours: int = 120) -> int:
    """
    Mark theses as expired if they haven't been updated recently.

    Args:
        max_age_hours: Theses older than this are expired

    Returns:
        Number of theses expired
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"

    result = db.execute(
        """
        UPDATE active_theses
        SET lifecycle_stage = 'expired', expires_at = ?
        WHERE lifecycle_stage != 'expired'
        AND last_updated < datetime('now', '-' || ? || ' hours')
        """,
        [now, max_age_hours]
    )

    expired_count = result.rows_affected
    if expired_count > 0:
        logger.info(f"Expired {expired_count} stale theses (older than {max_age_hours}h)")

    return expired_count


# ============================================================================
# THESIS EVIDENCE UTILITIES
# ============================================================================


def add_thesis_evidence(
    thesis_id: str,
    article_url: str,
    source: str,
    published_at: str,
    added_conviction: float,
    materiality: str,
    reasoning: Optional[str] = None
) -> bool:
    """
    Add supporting evidence to a thesis.

    Args:
        thesis_id: Target thesis ID
        article_url: Source article URL
        source: News source name
        published_at: Article publication timestamp (ISO 8601)
        added_conviction: Conviction change from this evidence (-1.0 to +1.0)
        materiality: "high", "medium", or "low"
        reasoning: Optional Claude reasoning for conviction change

    Returns:
        True if evidence was added successfully
    """
    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"

    # Check if evidence already exists for this article
    existing = db.execute(
        "SELECT id FROM thesis_evidence WHERE thesis_id = ? AND article_url = ?",
        [thesis_id, article_url]
    )
    if existing.rows:
        logger.debug(f"Evidence already exists for {article_url} on thesis {thesis_id}")
        return False

    # Add evidence
    db.execute(
        """
        INSERT INTO thesis_evidence (
            thesis_id, article_url, source, published_at,
            added_conviction, reasoning, materiality, added_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [thesis_id, article_url, source, published_at,
         added_conviction, reasoning, materiality, now]
    )

    # Update thesis evidence count and source diversity
    db.execute(
        """
        UPDATE active_theses SET
            evidence_count = (
                SELECT COUNT(*) FROM thesis_evidence WHERE thesis_id = ?
            ),
            source_diversity = (
                SELECT COUNT(DISTINCT source) FROM thesis_evidence WHERE thesis_id = ?
            )
        WHERE id = ?
        """,
        [thesis_id, thesis_id, thesis_id]
    )

    logger.debug(f"Added evidence to thesis {thesis_id} from {source} (conviction: {added_conviction:+.2f})")
    return True


def get_thesis_evidence(
    thesis_id: str,
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Get evidence supporting a thesis.

    Args:
        thesis_id: Target thesis ID
        limit: Optional limit on number of results

    Returns:
        List of evidence records, newest first
    """
    db = get_db()

    limit_clause = f"LIMIT {limit}" if limit else ""

    result = db.execute(
        f"""
        SELECT id, thesis_id, article_url, source, published_at,
               added_conviction, reasoning, materiality, added_at
        FROM thesis_evidence
        WHERE thesis_id = ?
        ORDER BY added_at DESC
        {limit_clause}
        """,
        [thesis_id]
    )

    evidence = []
    for row in result.rows:
        evidence.append({
            "id": row[0],
            "thesis_id": row[1],
            "article_url": row[2],
            "source": row[3],
            "published_at": row[4],
            "added_conviction": row[5],
            "reasoning": row[6],
            "materiality": row[7],
            "added_at": row[8],
        })

    return evidence


# ============================================================================
# SENTIMENT SCORES UTILITIES
# ============================================================================


def save_sentiment_scores(
    scores: List[Dict[str, Any]]
) -> int:
    """
    Save sentiment scores for non-thesis articles.

    Args:
        scores: List of sentiment score dicts with keys:
               ticker, sentiment_score, urgency, materiality, reasoning,
               source, article_url (optional)

    Returns:
        Number of scores saved
    """
    if not scores:
        return 0

    db = get_db()
    now = datetime.utcnow().isoformat() + "Z"

    saved_count = 0
    for score in scores:
        try:
            db.execute(
                """
                INSERT INTO sentiment_scores (
                    ticker, article_url, sentiment_score, urgency,
                    materiality, reasoning, source, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    score["ticker"],
                    score.get("article_url"),
                    score["sentiment_score"],
                    score.get("urgency"),
                    score["materiality"],
                    score.get("reasoning"),
                    score["source"],
                    now
                ]
            )
            saved_count += 1
        except Exception as e:
            logger.warning(f"Failed to save sentiment score for {score.get('ticker')}: {e}")

    logger.debug(f"Saved {saved_count}/{len(scores)} sentiment scores")
    return saved_count


def get_sentiment_for_ticker(
    ticker: str,
    hours_back: int = 24,
    include_article_urls: bool = False
) -> List[Dict[str, Any]]:
    """
    Get recent sentiment scores for a ticker.

    Args:
        ticker: Target ticker symbol
        hours_back: How far back to look for scores
        include_article_urls: Whether to include article URLs in results

    Returns:
        List of sentiment records, newest first
    """
    db = get_db()

    fields = "ticker, sentiment_score, urgency, materiality, reasoning, source, recorded_at"
    if include_article_urls:
        fields = "ticker, article_url, sentiment_score, urgency, materiality, reasoning, source, recorded_at"

    result = db.execute(
        f"""
        SELECT {fields}
        FROM sentiment_scores
        WHERE ticker = ? AND recorded_at >= datetime('now', '-' || ? || ' hours')
        ORDER BY recorded_at DESC
        """,
        [ticker.upper(), hours_back]
    )

    scores = []
    for row in result.rows:
        if include_article_urls:
            scores.append({
                "ticker": row[0],
                "article_url": row[1],
                "sentiment_score": row[2],
                "urgency": row[3],
                "materiality": row[4],
                "reasoning": row[5],
                "source": row[6],
                "recorded_at": row[7],
            })
        else:
            scores.append({
                "ticker": row[0],
                "sentiment_score": row[1],
                "urgency": row[2],
                "materiality": row[3],
                "reasoning": row[4],
                "source": row[5],
                "recorded_at": row[6],
            })

    return scores
