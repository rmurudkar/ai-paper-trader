"""yfinance client for price, volume, and moving average data."""

import yfinance as yf
from typing import Dict, List


def fetch_market_data(watchlist: List[str]) -> Dict[str, Dict]:
    """Fetch market data for all tickers in watchlist via yfinance.

    Per ticker: current price, volume, 50-day MA, 200-day MA

    Args:
        watchlist: List of ticker symbols.

    Returns:
        Dict keyed by ticker symbol, values are dicts with:
        price, volume, ma_50, ma_200.
    """
    pass


def _get_ticker_data(ticker: str) -> Dict:
    """Private: Fetch price, volume, 50MA, 200MA for a single ticker."""
    pass


def _calculate_moving_averages(ticker: str, periods: List[int] = [50, 200]) -> Dict[str, float]:
    """Private: Calculate moving averages for a ticker."""
    pass


def _get_current_price(ticker: str) -> float:
    """Private: Fetch the latest market price for a ticker."""
    pass


def _get_volume(ticker: str) -> int:
    """Private: Fetch the latest trading volume for a ticker."""
    pass