"""
Integration tests for fetchers/market.py.

Uses real yfinance data — no mocking. Tests verify the output shape and field
completeness that downstream strategy modules depend on.

Scenarios:
  1. _process_ticker_data() returns all required fields including ma_20
  2. ma_20, ma_50, ma_200 are None when history is too short
  3. price_change_pct is correct for a two-row history
  4. avg_volume_20 falls back to current volume when history < 20 days
  5. fetch_market_data() returns required top-level keys
  6. fetch_market_data() populates ticker data for a known liquid stock
  7. macro indicators dict contains vix, spy_price, spy_ma_200, spy_vs_200ma
  8. sector ETFs are returned when include_sector_etfs=True
  9. sector ETFs are absent when include_sector_etfs=False
  10. Invalid / delisted ticker is skipped, does not crash the batch
"""

import sys
import os
import pandas as pd
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fetchers.market import (
    _process_ticker_data,
    _calculate_rsi,
    fetch_market_data,
    SECTOR_ETFS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hist(n_days: int, base_price: float = 100.0, base_volume: int = 1_000_000) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame with exactly n_days rows."""
    prices = [base_price + i * 0.10 for i in range(n_days)]
    volumes = [base_volume + i * 1000 for i in range(n_days)]
    return pd.DataFrame({
        "Open": prices,
        "High": [p + 0.5 for p in prices],
        "Low": [p - 0.5 for p in prices],
        "Close": prices,
        "Volume": volumes,
    })


# ---------------------------------------------------------------------------
# Scenario 1 — all required fields present when history is long enough
# ---------------------------------------------------------------------------

def test_process_ticker_data_returns_all_fields():
    """
    Scenario: 250-day history (enough for every indicator).
    Expected: output dict contains price, volume, ma_20, ma_50, ma_200,
              rsi, price_change_pct, avg_volume_20, last_updated — all non-None.
    Why it matters: strategies.py reads every one of these keys; a missing key
                    causes a silent KeyError or None comparison bug.
    """
    hist = _make_hist(250)
    result = _process_ticker_data("TEST", hist)

    required_fields = [
        "price", "volume",
        "ma_20", "ma_50", "ma_200",
        "rsi",
        "price_change_pct", "avg_volume_20",
        "last_updated",
    ]
    for field in required_fields:
        assert field in result, f"Missing field: {field}"
        assert result[field] is not None, f"Field is None: {field}"


# ---------------------------------------------------------------------------
# Scenario 2 — MAs are None when history is too short
# ---------------------------------------------------------------------------

def test_process_ticker_data_mas_none_when_history_short():
    """
    Scenario: Only 10 days of history — not enough for any MA.
    Expected: ma_20, ma_50, ma_200 are all None; price and volume still populated.
    """
    hist = _make_hist(10)
    result = _process_ticker_data("TEST", hist)

    assert result["price"] is not None
    assert result["volume"] is not None
    assert result["ma_20"] is None
    assert result["ma_50"] is None
    assert result["ma_200"] is None


def test_process_ticker_data_ma_20_present_ma_50_none():
    """
    Scenario: 25 days of history — enough for ma_20 but not ma_50 or ma_200.
    Expected: ma_20 is a float, ma_50 and ma_200 are None.
    """
    hist = _make_hist(25)
    result = _process_ticker_data("TEST", hist)

    assert isinstance(result["ma_20"], float)
    assert result["ma_50"] is None
    assert result["ma_200"] is None


# ---------------------------------------------------------------------------
# Scenario 3 — price_change_pct is correct
# ---------------------------------------------------------------------------

def test_price_change_pct_calculation():
    """
    Scenario: Two-row history where price moves from 100 to 110.
    Expected: price_change_pct == 10.0.
    """
    hist = _make_hist(2, base_price=100.0)
    # Override so close is exactly 100 then 110
    hist["Close"] = [100.0, 110.0]
    hist["Volume"] = [1_000_000, 1_000_000]

    result = _process_ticker_data("TEST", hist)
    assert abs(result["price_change_pct"] - 10.0) < 0.01


def test_price_change_pct_single_row_is_zero():
    """
    Scenario: Only one row of history — no previous close to compare.
    Expected: price_change_pct == 0.0 (no division error).
    """
    hist = _make_hist(1)
    result = _process_ticker_data("TEST", hist)
    assert result["price_change_pct"] == 0.0


# ---------------------------------------------------------------------------
# Scenario 4 — avg_volume_20 fallback
# ---------------------------------------------------------------------------

def test_avg_volume_20_falls_back_to_current_volume():
    """
    Scenario: Only 5 days of history — not enough to compute a 20-day average.
    Expected: avg_volume_20 equals current day's volume (the fallback).
    """
    hist = _make_hist(5, base_volume=500_000)
    result = _process_ticker_data("TEST", hist)

    assert result["avg_volume_20"] == float(result["volume"])


def test_avg_volume_20_computed_when_history_sufficient():
    """
    Scenario: 30 days of history with constant volume 1,000,000.
    Expected: avg_volume_20 is approximately 1,000,000.
    """
    hist = _make_hist(30, base_volume=1_000_000)
    hist["Volume"] = 1_000_000  # constant
    result = _process_ticker_data("TEST", hist)

    assert abs(result["avg_volume_20"] - 1_000_000) < 1


# ---------------------------------------------------------------------------
# Scenario 5 — fetch_market_data() top-level structure
# ---------------------------------------------------------------------------

def test_fetch_market_data_returns_required_keys():
    """
    Scenario: Call fetch_market_data() with a single known ticker.
    Expected: result has 'tickers', 'macro', 'sector_etfs' keys.
    """
    result = fetch_market_data(["AAPL"], include_sector_etfs=False)

    assert "tickers" in result
    assert "macro" in result
    assert "sector_etfs" in result


# ---------------------------------------------------------------------------
# Scenario 6 — ticker data populated for a liquid stock
# ---------------------------------------------------------------------------

def test_fetch_market_data_populates_aapl():
    """
    Scenario: Fetch data for AAPL (always liquid, always has history).
    Expected: AAPL present in result['tickers'] with all required fields non-None
              (except ma_200, which needs 200 days — just check it's in the dict).
    """
    result = fetch_market_data(["AAPL"], include_sector_etfs=False)

    assert "AAPL" in result["tickers"], "AAPL missing from tickers dict"
    aapl = result["tickers"]["AAPL"]

    for field in ["price", "volume", "ma_20", "ma_50", "price_change_pct", "avg_volume_20", "last_updated"]:
        assert field in aapl, f"AAPL data missing field: {field}"
        assert aapl[field] is not None, f"AAPL field is None: {field}"

    assert "ma_200" in aapl  # presence required even if None for short histories


# ---------------------------------------------------------------------------
# Scenario 7 — macro indicators
# ---------------------------------------------------------------------------

def test_fetch_market_data_macro_indicators():
    """
    Scenario: Call fetch_market_data() and inspect the macro dict.
    Expected: vix, spy_price, spy_ma_200, spy_vs_200ma are all present and numeric.
    Yield spread may be absent if treasury data is unavailable — that's acceptable.
    """
    result = fetch_market_data(["SPY"], include_sector_etfs=False)
    macro = result["macro"]

    assert "vix" in macro, "vix missing from macro indicators"
    assert isinstance(macro["vix"], float)

    assert "spy_price" in macro
    assert isinstance(macro["spy_price"], float)

    assert "spy_ma_200" in macro
    assert "spy_vs_200ma" in macro


# ---------------------------------------------------------------------------
# Scenario 8 — sector ETFs returned when requested
# ---------------------------------------------------------------------------

def test_fetch_market_data_includes_sector_etfs_when_requested():
    """
    Scenario: include_sector_etfs=True.
    Expected: sector_etfs dict is non-empty and contains at least one known ETF
              with price, change_pct, volume_vs_avg fields.
    """
    result = fetch_market_data(["SPY"], include_sector_etfs=True)

    assert result["sector_etfs"] is not None
    assert len(result["sector_etfs"]) > 0

    # Spot-check one ETF
    for etf_symbol in SECTOR_ETFS:
        if etf_symbol in result["sector_etfs"]:
            etf = result["sector_etfs"][etf_symbol]
            assert "price" in etf
            assert "change_pct" in etf
            assert "volume_vs_avg" in etf
            break


# ---------------------------------------------------------------------------
# Scenario 9 — sector ETFs absent when not requested
# ---------------------------------------------------------------------------

def test_fetch_market_data_excludes_sector_etfs_by_default():
    """
    Scenario: include_sector_etfs=False (default).
    Expected: sector_etfs key is present but value is None.
    """
    result = fetch_market_data(["AAPL"], include_sector_etfs=False)
    assert result["sector_etfs"] is None


# ---------------------------------------------------------------------------
# Scenario 10 — invalid ticker is skipped, batch does not crash
# ---------------------------------------------------------------------------

def test_fetch_market_data_skips_invalid_ticker():
    """
    Scenario: Batch contains one valid ticker (AAPL) and one invalid ticker.
    Expected: AAPL is present in result, invalid ticker is absent, no exception raised.
    """
    result = fetch_market_data(["AAPL", "INVALID_TICKER_XYZ123"], include_sector_etfs=False)

    assert "AAPL" in result["tickers"]
    assert "INVALID_TICKER_XYZ123" not in result["tickers"]
