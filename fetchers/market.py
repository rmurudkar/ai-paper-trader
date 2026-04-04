"""yfinance client for fetching market data."""

import yfinance as yf


def get_current_price(ticker: str) -> float:
    """Fetch the latest market price for a ticker.

    Args:
        ticker: Stock ticker symbol (e.g. 'AAPL').

    Returns:
        Current price as a float.
    """
    pass


def get_historical_prices(ticker: str, period: str = "1mo", interval: str = "1d"):
    """Fetch historical OHLCV data for a ticker.

    Args:
        ticker: Stock ticker symbol.
        period: Lookback period (e.g. '1d', '5d', '1mo', '3mo', '1y').
        interval: Data interval (e.g. '1m', '5m', '1h', '1d').

    Returns:
        pandas DataFrame with columns: Open, High, Low, Close, Volume.
    """
    pass


def get_ticker_info(ticker: str) -> dict:
    """Fetch metadata for a ticker (sector, market cap, etc.).

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Dict of ticker metadata from yfinance.
    """
    pass
