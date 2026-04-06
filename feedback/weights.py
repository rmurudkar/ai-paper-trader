"""Strategy and source weight adjustment with circuit breaker.

After each trade outcome, nudge the weights of contributing strategies
and news sources using exponential moving average.  Monitor rolling
7-day win rate and trip the circuit breaker when performance drops
below 40%.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# EMA weight update
WEIGHT_LEARNING_RATE = 0.05   # 5% adjustment per outcome
MIN_WEIGHT = 0.1
MAX_WEIGHT = 1.0
INITIAL_WEIGHT = 0.5

# Circuit breaker
CIRCUIT_BREAKER_WIN_RATE = 0.40
ROLLING_WINDOW_DAYS = 7
MIN_TRADES_FOR_CIRCUIT_BREAKER = 10


def update_weights(outcome: Dict[str, Any]) -> None:
    """Update strategy and source weights based on a trade outcome.

    WIN  → nudge contributing weights toward 1.0
    LOSS → nudge contributing weights toward 0.0
    NEUTRAL → no change

    Args:
        outcome: {
            "trade_id": str,
            "outcome": "WIN" | "LOSS" | "NEUTRAL",
            "return_pct": float,
            "strategies_fired": list[str],
            "sentiment_source": str | None,
            "discovery_sources": list[str],
        }
    """
    classification = outcome.get("outcome", "NEUTRAL")
    if classification == "NEUTRAL":
        logger.debug(f"Neutral outcome for {outcome.get('trade_id')} — no weight update")
        return

    target = 1.0 if classification == "WIN" else 0.0
    strategies = outcome.get("strategies_fired", [])
    sentiment_source = outcome.get("sentiment_source")

    try:
        from db.client import get_weight, set_weight

        # Update strategy weights
        for strategy in strategies:
            old_w = get_weight("strategy", strategy, INITIAL_WEIGHT)
            new_w = _ema_update(old_w, target)
            set_weight("strategy", strategy, new_w)
            logger.info(
                f"Weight update [{classification}] strategy/{strategy}: "
                f"{old_w:.3f} → {new_w:.3f}"
            )

        # Update source weight
        if sentiment_source:
            old_w = get_weight("source", sentiment_source, INITIAL_WEIGHT)
            new_w = _ema_update(old_w, target)
            set_weight("source", sentiment_source, new_w)
            logger.info(
                f"Weight update [{classification}] source/{sentiment_source}: "
                f"{old_w:.3f} → {new_w:.3f}"
            )

    except Exception as e:
        logger.error(f"Failed to update weights for {outcome.get('trade_id')}: {e}")

    # Check circuit breaker after every outcome
    try:
        if check_circuit_breaker():
            win_rate = _get_rolling_win_rate()
            trip_circuit_breaker(
                reason=f"Rolling {ROLLING_WINDOW_DAYS}-day win rate fell to {win_rate:.1%}",
                win_rate=win_rate,
            )
    except Exception as e:
        logger.error(f"Circuit breaker check failed: {e}")


def _ema_update(old_weight: float, target: float) -> float:
    """Exponential moving average weight update, clamped to [MIN, MAX]."""
    new = old_weight * (1 - WEIGHT_LEARNING_RATE) + target * WEIGHT_LEARNING_RATE
    return max(MIN_WEIGHT, min(MAX_WEIGHT, new))


def check_circuit_breaker() -> bool:
    """Return True if the circuit breaker should be tripped.

    Trips when:
      - At least MIN_TRADES_FOR_CIRCUIT_BREAKER outcomes in the window
      - Rolling win rate < CIRCUIT_BREAKER_WIN_RATE (40%)
    """
    try:
        from db.client import is_circuit_breaker_tripped
        if is_circuit_breaker_tripped():
            return False  # already tripped, don't re-trip

        win_rate = _get_rolling_win_rate()
        trade_count = _get_rolling_trade_count()

        if trade_count < MIN_TRADES_FOR_CIRCUIT_BREAKER:
            return False  # not enough data

        if win_rate < CIRCUIT_BREAKER_WIN_RATE:
            logger.warning(
                f"Circuit breaker trigger: win rate {win_rate:.1%} "
                f"< {CIRCUIT_BREAKER_WIN_RATE:.0%} over {trade_count} trades"
            )
            return True

        return False

    except Exception as e:
        logger.error(f"Circuit breaker check error: {e}")
        return False  # fail-open to avoid false trips


def _get_rolling_win_rate(days: int = ROLLING_WINDOW_DAYS) -> float:
    """Win rate over the last N days (WIN count / (WIN + LOSS), excludes NEUTRAL).

    Returns 1.0 if no qualifying trades (optimistic default).
    """
    try:
        from db.client import get_db
        db = get_db()
        result = db.execute(
            """
            SELECT
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome IN ('WIN', 'LOSS') THEN 1 ELSE 0 END) as total
            FROM outcomes
            WHERE measured_at >= datetime('now', '-' || ? || ' days')
            """,
            [days],
        )

        if result.rows:
            wins = result.rows[0][0] or 0
            total = result.rows[0][1] or 0
            if total > 0:
                return wins / total
        return 1.0

    except Exception as e:
        logger.error(f"Failed to calculate win rate: {e}")
        return 1.0


def _get_rolling_trade_count(days: int = ROLLING_WINDOW_DAYS) -> int:
    """Count of WIN+LOSS outcomes in the window (excludes NEUTRAL)."""
    try:
        from db.client import get_db
        db = get_db()
        result = db.execute(
            """
            SELECT COUNT(*) FROM outcomes
            WHERE outcome IN ('WIN', 'LOSS')
              AND measured_at >= datetime('now', '-' || ? || ' days')
            """,
            [days],
        )
        if result.rows:
            return result.rows[0][0] or 0
        return 0
    except Exception as e:
        logger.error(f"Failed to count rolling trades: {e}")
        return 0


def trip_circuit_breaker(reason: str, win_rate: float) -> None:
    """Trip the circuit breaker — halt all new trades.

    Sets DB flag, logs warning, and sends alerts if configured.
    Existing positions remain open.
    """
    try:
        from db.client import trip_circuit_breaker as db_trip
        db_trip(reason, win_rate)
        logger.warning(f"CIRCUIT BREAKER TRIPPED: {reason} (win rate: {win_rate:.1%})")

        _send_alert(reason, win_rate)

    except Exception as e:
        logger.error(f"Failed to trip circuit breaker: {e}")


def _send_alert(reason: str, win_rate: float) -> None:
    """Send circuit breaker alert via Slack and/or email if configured."""
    import os

    slack_url = os.getenv("SLACK_WEBHOOK_URL")
    if slack_url:
        try:
            import requests
            payload = {
                "text": (
                    f":rotating_light: *Circuit Breaker Tripped*\n"
                    f"Reason: {reason}\n"
                    f"Win rate: {win_rate:.1%}\n"
                    f"All trading halted. Manual reset required."
                ),
            }
            requests.post(slack_url, json=payload, timeout=10)
        except Exception as e:
            logger.warning(f"Slack alert failed: {e}")

    alert_email = os.getenv("ALERT_EMAIL")
    if alert_email:
        logger.info(f"Circuit breaker alert would be sent to {alert_email}")
