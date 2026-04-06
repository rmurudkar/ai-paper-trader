"""Alpaca paper trading executor."""

import os
import logging
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.common.exceptions import APIError

logger = logging.getLogger(__name__)

_client: TradingClient | None = None


def get_client() -> TradingClient:
    """Instantiate and return an authenticated Alpaca TradingClient.

    Returns:
        Configured TradingClient pointed at the paper trading endpoint.
    """
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")

    _client = TradingClient(
        api_key=api_key,
        secret_key=secret_key,
        paper=True,
    )
    return _client


def is_market_open() -> bool:
    """Check if the market is currently open via Alpaca clock API.

    Returns:
        True if the market is open for trading.
    """
    client = get_client()
    clock = client.get_clock()
    return clock.is_open


def place_order(ticker: str, qty: float, side: str) -> dict:
    """Submit a market order to Alpaca paper trading.

    Args:
        ticker: Stock ticker symbol.
        qty: Number of shares (fractional allowed).
        side: 'buy' or 'sell'.

    Returns:
        Dict representing the submitted order.
    """
    client = get_client()

    if not is_market_open():
        logger.warning("Market is closed — rejecting order for %s", ticker)
        return {"error": "market_closed", "ticker": ticker}

    order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

    request = MarketOrderRequest(
        symbol=ticker,
        qty=qty,
        side=order_side,
        time_in_force=TimeInForce.DAY,
    )

    try:
        order = client.submit_order(order_data=request)
        logger.info("Order submitted: %s %s x%s — order_id=%s", side.upper(), ticker, qty, order.id)
        return {
            "order_id": str(order.id),
            "symbol": order.symbol,
            "side": order.side.value,
            "qty": str(order.qty),
            "status": order.status.value,
            "filled_avg_price": str(order.filled_avg_price) if order.filled_avg_price else None,
            "submitted_at": str(order.submitted_at),
        }
    except APIError as e:
        logger.error("Alpaca order failed for %s: %s", ticker, e)
        return {"error": str(e), "ticker": ticker}


def get_portfolio() -> dict:
    """Fetch current paper trading portfolio state.

    Returns:
        Dict with keys: cash, positions (list), equity, buying_power.
    """
    client = get_client()
    account = client.get_account()
    positions = get_positions()

    return {
        "cash": float(account.cash),
        "equity": float(account.equity),
        "buying_power": float(account.buying_power),
        "positions": positions,
    }


def get_positions() -> list[dict]:
    """Fetch all open positions.

    Returns:
        List of position dicts with ticker, qty, market_value, unrealized_pl.
    """
    client = get_client()
    positions = client.get_all_positions()

    return [
        {
            "ticker": p.symbol,
            "qty": float(p.qty),
            "market_value": float(p.market_value),
            "unrealized_pl": float(p.unrealized_pl),
            "current_price": float(p.current_price),
            "avg_entry_price": float(p.avg_entry_price),
            "side": p.side.value if hasattr(p.side, "value") else str(p.side),
        }
        for p in positions
    ]


def close_position(ticker: str) -> dict:
    """Close an entire position for a given ticker.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict representing the closing order.
    """
    client = get_client()

    try:
        order = client.close_position(symbol_or_asset_id=ticker)
        logger.info("Closed position: %s — order_id=%s", ticker, order.id)
        return {
            "order_id": str(order.id),
            "symbol": order.symbol,
            "status": order.status.value,
        }
    except APIError as e:
        logger.error("Failed to close position %s: %s", ticker, e)
        return {"error": str(e), "ticker": ticker}
