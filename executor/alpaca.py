"""Alpaca paper trading executor."""

import os
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce


def get_client() -> TradingClient:
    """Instantiate and return an authenticated Alpaca TradingClient.

    Returns:
        Configured TradingClient pointed at the paper trading endpoint.
    """
    pass


def place_order(ticker: str, qty: float, side: str) -> dict:
    """Submit a market order to Alpaca paper trading.

    Args:
        ticker: Stock ticker symbol.
        qty: Number of shares (fractional allowed).
        side: 'buy' or 'sell'.

    Returns:
        Dict representing the submitted order.
    """
    pass


def get_portfolio() -> dict:
    """Fetch current paper trading portfolio state.

    Returns:
        Dict with keys: cash, positions (list), equity, buying_power.
    """
    pass


def get_positions() -> list[dict]:
    """Fetch all open positions.

    Returns:
        List of position dicts with ticker, qty, market_value, unrealized_pl.
    """
    pass


def close_position(ticker: str) -> dict:
    """Close an entire position for a given ticker.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict representing the closing order.
    """
    pass
