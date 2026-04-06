"""Trade logging for the feedback loop.

Every executed trade is logged with full signal metadata so the feedback
loop can attribute outcomes to specific strategies and sources.
"""

import logging
import uuid
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def log_trade(trade_data: Dict[str, Any]) -> str:
    """Log an executed trade to the DB with full attribution metadata.

    Args:
        trade_data: {
            "ticker": str,
            "signal": "BUY" | "SELL",
            "confidence": float,
            "sentiment_score": float | None,
            "sentiment_source": str | None,
            "strategies_fired": list[str],
            "discovery_sources": list[str],
            "regime_mode": str,
            "article_urls": list[str],
            "entry_price": float,
            "shares": int,
            "stop_loss_price": float,
            "take_profit_price": float,
            "order_id": str,
        }

    Returns:
        Generated trade_id (UUID string), or "" on failure.
    """
    trade_id = str(uuid.uuid4())

    try:
        from db.client import log_trade as db_log_trade

        db_log_trade(
            trade_id=trade_id,
            ticker=trade_data.get("ticker", ""),
            signal=trade_data.get("signal", ""),
            confidence=trade_data.get("confidence", 0.0),
            sentiment_score=trade_data.get("sentiment_score"),
            sentiment_source=trade_data.get("sentiment_source"),
            strategies_fired=trade_data.get("strategies_fired", []),
            discovery_sources=trade_data.get("discovery_sources", []),
            regime_mode=trade_data.get("regime_mode"),
            article_urls=trade_data.get("article_urls", []),
            entry_price=trade_data.get("entry_price", 0.0),
            shares=trade_data.get("shares", 0),
            stop_loss_price=trade_data.get("stop_loss_price"),
            take_profit_price=trade_data.get("take_profit_price"),
            order_id=trade_data.get("order_id"),
        )

        logger.info(
            f"Logged trade {trade_id}: {trade_data.get('signal')} "
            f"{trade_data.get('shares')} {trade_data.get('ticker')} "
            f"@ ${trade_data.get('entry_price', 0):.2f}"
        )
        return trade_id

    except Exception as e:
        logger.error(f"Failed to log trade for {trade_data.get('ticker', '?')}: {e}")
        return ""
