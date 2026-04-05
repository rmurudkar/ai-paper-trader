"""Database package for autonomous paper trading system.

Contains Turso database connection management and schema definitions
for persistent data storage.
"""

from db.client import (
    get_db,
    db_transaction,
    # Sector cache
    get_sector_from_cache,
    cache_sector,
    # Weights (feedback loop)
    get_weight,
    set_weight,
    get_all_weights,
    initialize_default_weights,
    # Trade logging
    log_trade,
    get_trade,
    # Outcomes
    log_outcome,
    get_outcome,
    # Circuit breaker
    is_circuit_breaker_tripped,
    trip_circuit_breaker,
    reset_circuit_breaker,
    # Discovery log
    log_discovery,
    get_discoveries_for_cycle,
    # Queries
    get_recent_trades,
    get_open_trades_count,
    get_recent_win_rate,
)

__all__ = [
    "get_db",
    "db_transaction",
    "get_sector_from_cache",
    "cache_sector",
    "get_weight",
    "set_weight",
    "get_all_weights",
    "initialize_default_weights",
    "log_trade",
    "get_trade",
    "log_outcome",
    "get_outcome",
    "is_circuit_breaker_tripped",
    "trip_circuit_breaker",
    "reset_circuit_breaker",
    "log_discovery",
    "get_discoveries_for_cycle",
    "get_recent_trades",
    "get_open_trades_count",
    "get_recent_win_rate",
]