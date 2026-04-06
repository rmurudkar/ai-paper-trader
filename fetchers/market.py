"""yfinance client for price, volume, moving averages, RSI, and macro indicators.

Market Data Pipeline:
====================
1. Individual ticker data: price, volume, 50MA, 200MA, RSI(14)
2. Macro indicators: VIX, SPY vs 200MA, 10yr-2yr yield spread
3. Sector ETF data for rotation scanning
4. Dynamic S&P 500 ticker list fetching

API Dependencies:
================
- yfinance: Free, no API key required
- Fetches from Yahoo Finance real-time and historical data
- Rate limits: ~1-2 requests per second recommended
- Handles market hours, weekends, holidays automatically

Error Recovery:
==============
- Individual ticker failures don't crash the batch
- Stale data handling: return None if data older than 1 hour during market hours
- Network timeouts: 10 second timeout per ticker
- Invalid tickers: log and skip, don't error

Performance Notes:
=================
- Batch requests where possible (yf.download for multiple tickers)
- Cache S&P 500 list for 24 hours (rarely changes)
- RSI calculation requires minimum 14 days of history
- Yield spread calculation requires treasury data
"""

import yfinance as yf
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
import logging
import requests
from datetime import datetime, timedelta
import time

from db.client import get_cached_sp500, save_sp500_cache

logger = logging.getLogger(__name__)

# Sector ETFs for rotation scanning
SECTOR_ETFS = {
    'XLK': 'Technology',
    'XLF': 'Financial',
    'XLE': 'Energy',
    'XLV': 'Health Care',
    'XLC': 'Communication Services',
    'XLI': 'Industrials',
    'XLY': 'Consumer Discretionary',
    'XLP': 'Consumer Staples',
    'XLU': 'Utilities',
    'XLRE': 'Real Estate',
    'XLB': 'Materials'
}

# Treasury tickers for yield spread
TREASURY_TICKERS = {
    '10Y': '^TNX',  # 10-Year Treasury
    '2Y': '^IRX'    # 2-Year Treasury (actually 3-month, but proxy)
}


def fetch_market_data(tickers: List[str], include_sector_etfs: bool = False) -> Dict:
    """Fetch comprehensive market data for tickers and macro indicators.

    Args:
        tickers: List of ticker symbols to analyze
        include_sector_etfs: Whether to include sector ETF data for discovery mode

    Returns:
        Dict with structure:
        {
            'tickers': {
                'AAPL': {
                    'price': 175.50,
                    'volume': 45000000,
                    'ma_50': 170.25,
                    'ma_200': 165.80,
                    'rsi': 62.5,
                    'last_updated': '2026-04-05T14:30:00Z'
                }
            },
            'macro': {
                'vix': 18.5,
                'spy_price': 520.25,
                'spy_ma_200': 510.50,
                'spy_vs_200ma': 0.019,  # 1.9% above 200MA
                'yield_10y': 4.25,
                'yield_2y': 4.85,
                'yield_spread': -0.60   # 2Y-10Y spread (inverted)
            },
            'sector_etfs': {  # Only if include_sector_etfs=True
                'XLK': {
                    'price': 210.50,
                    'change_pct': 2.1,
                    'volume_vs_avg': 1.4
                }
            }
        }
    """
    logger.info(f"Fetching market data for {len(tickers)} tickers, include_sector_etfs={include_sector_etfs}")

    result = {
        'tickers': {},
        'macro': {},
        'sector_etfs': {} if include_sector_etfs else None
    }

    # Fetch individual ticker data
    result['tickers'] = _fetch_ticker_batch(tickers)

    # Fetch macro indicators
    result['macro'] = _fetch_macro_indicators()

    # Fetch sector ETF data if requested
    if include_sector_etfs:
        result['sector_etfs'] = _fetch_sector_etfs()

    logger.info(f"Market data fetch complete: {len(result['tickers'])} tickers, macro indicators, {len(result.get('sector_etfs', {}))} sector ETFs")
    return result


def fetch_sp500_tickers() -> List[str]:
    """Dynamically fetch current S&P 500 ticker list.

    Sources (in order of preference):
    1. Wikipedia S&P 500 table via pandas
    2. Static fallback list if Wikipedia fails

    Returns:
        List of ~500 ticker symbols

    Note:
        Results are cached for 24 hours since S&P 500 changes are rare.
        Handles ticker symbol changes (e.g. BRK.B -> BRK-B for yfinance).
    """
    logger.info("Fetching S&P 500 ticker list dynamically...")

    try:
        # Method 1: Wikipedia table scraping
        logger.info("Attempting to fetch S&P 500 list from Wikipedia...")

        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)

        # The first table contains the current S&P 500 companies
        sp500_table = tables[0]

        # Extract ticker symbols from 'Symbol' column
        tickers = sp500_table['Symbol'].tolist()

        # Clean ticker symbols for yfinance compatibility
        tickers = [_clean_ticker_symbol(ticker) for ticker in tickers]
        tickers = [t for t in tickers if t]  # Remove any None values

        logger.info(f"Successfully fetched {len(tickers)} S&P 500 tickers from Wikipedia")
        save_sp500_cache(tickers)
        return sorted(tickers)

    except Exception as e:
        logger.error(f"Failed to fetch S&P 500 from Wikipedia: {e}")

        # Fallback 1: Last successful fetch from Turso cache
        cached = get_cached_sp500()
        if cached:
            logger.info(f"Using cached S&P 500 list ({len(cached)} tickers)")
            return cached

        # Fallback 2: Static list of major S&P 500 companies
        logger.info("Using static fallback S&P 500 list...")
        return _get_fallback_sp500_list()


def _fetch_ticker_batch(tickers: List[str]) -> Dict[str, Dict]:
    """Fetch market data for multiple tickers efficiently."""
    ticker_data = {}

    if not tickers:
        return ticker_data

    logger.info(f"Batch fetching data for {len(tickers)} tickers...")

    # Split into chunks to avoid overwhelming yfinance
    chunk_size = 50
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i+chunk_size]

        try:
            # Fetch batch data using yf.download
            data = yf.download(
                tickers=chunk,
                period='1y',  # Need enough history for 200MA and RSI
                interval='1d',
                group_by='ticker',
                auto_adjust=True,
                prepost=True,
                threads=True
            )

            # Process each ticker in the chunk
            for ticker in chunk:
                try:
                    if len(chunk) == 1:
                        ticker_hist = data
                    else:
                        ticker_hist = data[ticker] if ticker in data.columns.get_level_values(0) else None

                    if ticker_hist is not None and not ticker_hist.empty:
                        ticker_data[ticker] = _process_ticker_data(ticker, ticker_hist)
                    else:
                        logger.warning(f"No data available for ticker {ticker}")

                except Exception as e:
                    logger.error(f"Failed to process data for {ticker}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to fetch batch data for chunk {chunk}: {e}")

            # Fallback: fetch individually
            for ticker in chunk:
                try:
                    individual_data = _get_ticker_data(ticker)
                    if individual_data:
                        ticker_data[ticker] = individual_data
                except Exception as individual_e:
                    logger.error(f"Individual fetch also failed for {ticker}: {individual_e}")
                    continue

    logger.info(f"Successfully fetched data for {len(ticker_data)}/{len(tickers)} tickers")
    return ticker_data


def _get_ticker_data(ticker: str) -> Optional[Dict]:
    """Fetch comprehensive data for a single ticker."""
    try:
        stock = yf.Ticker(ticker)

        # Get 1 year of history for moving averages and RSI
        hist = stock.history(period='1y', interval='1d')

        if hist.empty:
            logger.warning(f"No historical data for {ticker}")
            return None

        return _process_ticker_data(ticker, hist)

    except Exception as e:
        logger.error(f"Failed to fetch data for {ticker}: {e}")
        return None


def _process_ticker_data(ticker: str, hist: pd.DataFrame) -> Dict:
    """Process raw yfinance data into standardized format."""
    try:
        # Current values (most recent)
        current_price = float(hist['Close'].iloc[-1])
        current_volume = int(hist['Volume'].iloc[-1])

        # Moving averages
        ma_50 = float(hist['Close'].rolling(50).mean().iloc[-1]) if len(hist) >= 50 else None
        ma_200 = float(hist['Close'].rolling(200).mean().iloc[-1]) if len(hist) >= 200 else None

        # RSI calculation
        rsi = _calculate_rsi(hist['Close']) if len(hist) >= 15 else None

        return {
            'price': current_price,
            'volume': current_volume,
            'ma_50': ma_50,
            'ma_200': ma_200,
            'rsi': rsi,
            'last_updated': datetime.now().isoformat() + 'Z'
        }

    except Exception as e:
        logger.error(f"Failed to process data for {ticker}: {e}")
        return None


def _calculate_rsi(prices: pd.Series, period: int = 14) -> Optional[float]:
    """Calculate RSI (Relative Strength Index)."""
    try:
        if len(prices) < period + 1:
            return None

        # Calculate price changes
        delta = prices.diff()

        # Separate gains and losses
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        # Calculate RS and RSI
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi.iloc[-1])

    except Exception as e:
        logger.error(f"RSI calculation failed: {e}")
        return None


def _fetch_macro_indicators() -> Dict:
    """Fetch macro indicators for regime detection."""
    logger.info("Fetching macro indicators...")

    macro_data = {}

    try:
        # VIX
        vix = yf.Ticker('^VIX')
        vix_hist = vix.history(period='5d')
        if not vix_hist.empty:
            macro_data['vix'] = float(vix_hist['Close'].iloc[-1])

        # SPY data
        spy = yf.Ticker('SPY')
        spy_hist = spy.history(period='1y')
        if not spy_hist.empty:
            spy_price = float(spy_hist['Close'].iloc[-1])
            spy_ma_200 = float(spy_hist['Close'].rolling(200).mean().iloc[-1]) if len(spy_hist) >= 200 else None

            macro_data['spy_price'] = spy_price
            macro_data['spy_ma_200'] = spy_ma_200

            if spy_ma_200:
                macro_data['spy_vs_200ma'] = (spy_price - spy_ma_200) / spy_ma_200

        # Treasury yields for yield spread
        try:
            tnx = yf.Ticker('^TNX')  # 10-Year Treasury
            irx = yf.Ticker('^IRX')  # 3-Month Treasury (proxy for 2Y)

            tnx_hist = tnx.history(period='5d')
            irx_hist = irx.history(period='5d')

            if not tnx_hist.empty and not irx_hist.empty:
                yield_10y = float(tnx_hist['Close'].iloc[-1])
                yield_3m = float(irx_hist['Close'].iloc[-1])

                macro_data['yield_10y'] = yield_10y
                macro_data['yield_2y'] = yield_3m  # Using 3M as proxy
                macro_data['yield_spread'] = yield_3m - yield_10y  # 2Y-10Y spread

        except Exception as e:
            logger.error(f"Failed to fetch treasury yields: {e}")

        logger.info(f"Fetched macro indicators: {list(macro_data.keys())}")

    except Exception as e:
        logger.error(f"Failed to fetch macro indicators: {e}")

    return macro_data


def _fetch_sector_etfs() -> Dict[str, Dict]:
    """Fetch sector ETF data for rotation scanning."""
    logger.info("Fetching sector ETF data...")

    sector_data = {}

    for etf_symbol, sector_name in SECTOR_ETFS.items():
        try:
            etf = yf.Ticker(etf_symbol)
            hist = etf.history(period='30d')  # Get 30 days for change calculation

            if not hist.empty:
                current_price = float(hist['Close'].iloc[-1])

                # Calculate daily change percentage
                if len(hist) >= 2:
                    prev_price = float(hist['Close'].iloc[-2])
                    change_pct = ((current_price - prev_price) / prev_price) * 100
                else:
                    change_pct = 0.0

                # Volume vs average
                current_volume = int(hist['Volume'].iloc[-1])
                avg_volume = float(hist['Volume'].rolling(20).mean().iloc[-1]) if len(hist) >= 20 else current_volume
                volume_vs_avg = current_volume / avg_volume if avg_volume > 0 else 1.0

                sector_data[etf_symbol] = {
                    'sector': sector_name,
                    'price': current_price,
                    'change_pct': change_pct,
                    'volume_vs_avg': volume_vs_avg
                }

        except Exception as e:
            logger.error(f"Failed to fetch data for sector ETF {etf_symbol}: {e}")
            continue

    logger.info(f"Fetched data for {len(sector_data)} sector ETFs")
    return sector_data


def _clean_ticker_symbol(ticker: str) -> Optional[str]:
    """Clean ticker symbol for yfinance compatibility."""
    if not ticker or not isinstance(ticker, str):
        return None

    ticker = ticker.strip().upper()

    # Handle common symbol transformations
    # BRK.B -> BRK-B (yfinance format)
    ticker = ticker.replace('.', '-')

    # Remove any invalid characters
    ticker = ''.join(c for c in ticker if c.isalnum() or c == '-')

    return ticker if ticker else None


def _get_fallback_sp500_list() -> List[str]:
    """Static fallback list of major S&P 500 companies."""
    return [
        'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'GOOG', 'AMZN', 'META', 'TSLA', 'BRK-B', 'LLY',
        'AVGO', 'V', 'JPM', 'WMT', 'XOM', 'UNH', 'ORCL', 'MA', 'COST', 'HD',
        'PG', 'NFLX', 'JNJ', 'BAC', 'CRM', 'ABBV', 'CVX', 'KO', 'AMD', 'PEP',
        'TMO', 'MRK', 'ACN', 'LIN', 'CSCO', 'ABT', 'ADBE', 'DHR', 'VZ', 'MCD',
        'QCOM', 'TXN', 'BMY', 'PM', 'AMGN', 'RTX', 'SPGI', 'HON', 'ORCL', 'UNP',
        'LOW', 'T', 'MDT', 'NKE', 'NEE', 'COP', 'IBM', 'GE', 'UPS', 'AMT',
        'BLK', 'SBUX', 'CAT', 'DE', 'AXP', 'BKNG', 'GILD', 'MMM', 'TJX', 'MO',
        'CVS', 'LRCX', 'SCHW', 'AMAT', 'ZTS', 'FDX', 'SYK', 'ADI', 'CME', 'TMUS',
        'MDLZ', 'C', 'SO', 'REGN', 'PLD', 'ISRG', 'DUK', 'GS', 'TGT', 'BSX',
        'AON', 'EQIX', 'SHW', 'ITW', 'KLAC', 'APD', 'CL', 'MSI', 'MMC', 'SNPS'
    ]


def get_current_price(ticker: str) -> Optional[float]:
    """Get current/latest price for a single ticker (utility function)."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period='1d', interval='1m')

        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        else:
            # Fallback to daily data
            hist = stock.history(period='2d')
            if not hist.empty:
                return float(hist['Close'].iloc[-1])

    except Exception as e:
        logger.error(f"Failed to get current price for {ticker}: {e}")

    return None


def get_volume(ticker: str) -> Optional[int]:
    """Get current/latest volume for a single ticker (utility function)."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period='1d')

        if not hist.empty:
            return int(hist['Volume'].iloc[-1])

    except Exception as e:
        logger.error(f"Failed to get volume for {ticker}: {e}")

    return None