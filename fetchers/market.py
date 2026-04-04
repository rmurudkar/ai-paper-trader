"""yfinance client for price, volume, and moving average data."""

import yfinance as yf
from typing import Dict, List


def get_ticker_data(ticker: str) -> Dict:
    """Fetch price, volume, 50MA, 200MA for a single ticker.

    Args:
        ticker: Stock ticker symbol (e.g. 'AAPL').

    Returns:
        Dict with keys: price, volume, ma_50, ma_200, ticker.
    """
    pass


def get_watchlist_data(watchlist: List[str]) -> Dict[str, Dict]:
    """Fetch market data for all tickers in watchlist.

    Args:
        watchlist: List of ticker symbols.

    Returns:
        Dict keyed by ticker symbol, values are dicts with:
        price, volume, ma_50, ma_200.
    """
    pass


def calculate_moving_averages(ticker: str, periods: List[int] = [50, 200]) -> Dict[str, float]:
    """Calculate moving averages for a ticker.

    Args:
        ticker: Stock ticker symbol.
        periods: List of MA periods to calculate (default [50, 200]).

    Returns:
        Dict with keys like 'ma_50', 'ma_200' mapped to float values.
    """
    pass


def get_current_price(ticker: str) -> float:
    """Fetch the latest market price for a ticker.

    Args:
        ticker: Stock ticker symbol (e.g. 'AAPL').

    Returns:
        Current price as a float.
    """
    pass


def get_volume(ticker: str) -> int:
    """Fetch the latest trading volume for a ticker.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Current volume as an integer.
    """
    pass