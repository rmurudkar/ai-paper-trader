"""Trade outcome measurement for the feedback loop.

Scheduled job that checks open trades, fetches current prices, classifies
outcomes (WIN/LOSS/NEUTRAL), and records them for weight adjustment.
Runs every 4 hours and at market close.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Outcome classification thresholds
WIN_THRESHOLD = 0.01      # +1% return
LOSS_THRESHOLD = -0.01    # -1% return

# Holding period
DEFAULT_HOLDING_PERIOD = 8    # hours
MAX_HOLDING_PERIOD = 72       # 3 days


def measure_outcomes() -> List[Dict[str, Any]]:
    """Measure outcomes for all pending (unmeasured) trades.

    Process:
      1. Query trades that have no row in outcomes yet.
      2. For each, fetch current price via yfinance.
      3. If holding period exceeded OR stop/TP hit → classify and record.
      4. Trigger weight update for each recorded outcome.

    Returns list of measured outcome dicts.
    """
    pending = _get_pending_trades()
    if not pending:
        logger.info("No pending trades to measure")
        return []

    results = []
    for trade in pending:
        try:
            outcome = _evaluate_trade(trade)
            if outcome is None:
                continue  # not yet ready to close

            _record_outcome(outcome)

            # Trigger weight update
            try:
                from feedback.weights import update_weights
                update_weights(outcome)
            except Exception as e:
                logger.warning(f"Weight update failed for {outcome['trade_id']}: {e}")

            results.append(outcome)
        except Exception as e:
            logger.error(f"Failed to measure trade {trade.get('trade_id', '?')}: {e}")

    logger.info(f"Measured {len(results)} outcomes out of {len(pending)} pending trades")
    return results


def _get_pending_trades() -> List[Dict[str, Any]]:
    """Get trades with no outcome recorded yet."""
    try:
        from db.client import get_db
        import json

        db = get_db()
        result = db.execute(
            """
            SELECT t.* FROM trades t
            LEFT JOIN outcomes o ON t.trade_id = o.trade_id
            WHERE o.trade_id IS NULL
              AND t.order_id IS NOT NULL
            ORDER BY t.created_at ASC
            """
        )

        columns = [
            "trade_id", "ticker", "signal", "confidence",
            "sentiment_score", "sentiment_source", "strategies_fired",
            "discovery_sources", "regime_mode", "article_urls",
            "entry_price", "shares", "stop_loss_price", "take_profit_price",
            "order_id", "created_at",
        ]

        trades = []
        for row in result.rows:
            trade = dict(zip(columns, row))
            trade["strategies_fired"] = json.loads(trade["strategies_fired"] or "[]")
            trade["discovery_sources"] = json.loads(trade["discovery_sources"] or "[]")
            trade["article_urls"] = json.loads(trade["article_urls"] or "[]")
            trades.append(trade)

        return trades
    except Exception as e:
        logger.error(f"Failed to fetch pending trades: {e}")
        return []


def _evaluate_trade(trade: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Evaluate a single trade and return an outcome dict if ready to close.

    Returns None if the trade hasn't hit any exit criteria yet.
    """
    ticker = trade["ticker"]
    entry_price = trade["entry_price"]
    signal = trade["signal"]
    created_at = trade["created_at"]

    # Parse trade time
    try:
        trade_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        trade_time = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_HOLDING_PERIOD + 1)

    now = datetime.now(timezone.utc)
    age_hours = (now - trade_time).total_seconds() / 3600

    # Fetch current price
    current_price = _fetch_current_price(ticker)
    if current_price is None or current_price <= 0:
        logger.warning(f"Could not fetch price for {ticker}, skipping")
        return None

    # Calculate return
    if signal == "BUY":
        return_pct = (current_price - entry_price) / entry_price
    else:  # SELL
        return_pct = (entry_price - current_price) / entry_price

    # Check exit criteria
    stop_loss = trade.get("stop_loss_price")
    take_profit = trade.get("take_profit_price")
    exit_reason = None

    if signal == "BUY":
        if stop_loss and current_price <= stop_loss:
            exit_reason = "stop_loss"
        elif take_profit and current_price >= take_profit:
            exit_reason = "take_profit"
    else:  # SELL
        if stop_loss and current_price >= stop_loss:
            exit_reason = "stop_loss"
        elif take_profit and current_price <= take_profit:
            exit_reason = "take_profit"

    # Time-based exit
    if exit_reason is None and age_hours >= DEFAULT_HOLDING_PERIOD:
        exit_reason = "holding_period"

    # Not ready to close yet
    if exit_reason is None:
        return None

    outcome = _classify_outcome(return_pct)

    return {
        "trade_id": trade["trade_id"],
        "ticker": ticker,
        "signal": signal,
        "outcome": outcome,
        "return_pct": round(return_pct, 6),
        "exit_price": current_price,
        "holding_period_hours": round(age_hours, 2),
        "exit_reason": exit_reason,
        "measured_at": now.isoformat(),
        # Attribution data for weight updates
        "strategies_fired": trade.get("strategies_fired", []),
        "sentiment_source": trade.get("sentiment_source"),
        "discovery_sources": trade.get("discovery_sources", []),
    }


def _classify_outcome(return_pct: float) -> str:
    """Classify trade return into WIN / LOSS / NEUTRAL."""
    if return_pct > WIN_THRESHOLD:
        return "WIN"
    elif return_pct < LOSS_THRESHOLD:
        return "LOSS"
    return "NEUTRAL"


def _record_outcome(outcome: Dict[str, Any]) -> None:
    """Write outcome to the DB."""
    try:
        from db.client import log_outcome
        log_outcome(
            trade_id=outcome["trade_id"],
            exit_price=outcome["exit_price"],
            return_pct=outcome["return_pct"],
            outcome=outcome["outcome"],
            holding_period_hours=outcome["holding_period_hours"],
        )
    except Exception as e:
        logger.error(f"Failed to record outcome for {outcome['trade_id']}: {e}")
        raise


def _fetch_current_price(ticker: str) -> Optional[float]:
    """Fetch current price for a ticker via yfinance."""
    try:
        import yfinance as yf
        data = yf.Ticker(ticker).history(period="1d")
        if not data.empty:
            return float(data["Close"].iloc[-1])
    except Exception as e:
        logger.warning(f"yfinance price fetch failed for {ticker}: {e}")
    return None
