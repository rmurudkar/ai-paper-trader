"""Tests for risk/manager.py — position sizing and portfolio safety checks."""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.manager import (
    check_trade,
    calculate_position_size,
    _reject,
    _find_position,
    _position_value,
    MAX_RISK_PER_TRADE,
    MAX_SHARES_PER_POSITION,
    MAX_PORTFOLIO_ALLOCATION,
    MAX_SINGLE_TICKER_ALLOCATION,
    MAX_SECTOR_ALLOCATION,
    MAX_OPEN_POSITIONS,
    MIN_STOCK_PRICE,
    MIN_MARKET_CAP,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    DUPLICATE_SIGNAL_HOURS,
    RISK_OFF_SIZE_REDUCTION,
    SECTOR_OVERWEIGHT_REDUCTION,
    SECTOR_OVERWEIGHT_THRESHOLD,
)


def _signal(ticker="AAPL", direction="BUY", confidence=0.75, regime="neutral"):
    return {
        "ticker": ticker,
        "signal": direction,
        "confidence": confidence,
        "regime": regime,
    }


def _portfolio(cash=50000, equity=100000, positions=None):
    return {
        "cash": cash,
        "equity": equity,
        "positions": positions or [],
    }


def _market(price=175.0, market_cap=2.8e12):
    return {"price": price, "market_cap": market_cap}


def _position(ticker="AAPL", qty=50, market_value=8750.0):
    return {"ticker": ticker, "qty": qty, "market_value": market_value}


# Patch external dependencies for all tests
@patch("risk.manager._has_recent_signal", return_value=False)
@patch("risk.manager._lookup_sector", return_value="Technology")
@patch("risk.manager._sector_exposure", return_value=0.0)
class TestCheckTradeApproval(unittest.TestCase):
    """Test that valid trades get approved with correct sizing."""

    def test_basic_buy_approved(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market())
        self.assertTrue(result["approved"])
        self.assertEqual(result["reason"], "")
        self.assertGreater(result["shares"], 0)
        self.assertGreater(result["entry_price"], 0)

    def test_stop_loss_for_buy(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market(price=100.0))
        self.assertTrue(result["approved"])
        self.assertAlmostEqual(result["stop_loss"], 97.0, places=2)
        self.assertAlmostEqual(result["take_profit"], 103.0, places=2)

    def test_stop_loss_for_sell(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(
            _signal(direction="SELL"), _portfolio(), _market(price=100.0)
        )
        self.assertTrue(result["approved"])
        self.assertAlmostEqual(result["stop_loss"], 103.0, places=2)
        self.assertAlmostEqual(result["take_profit"], 97.0, places=2)

    def test_allocation_pct_calculated(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market())
        self.assertTrue(result["approved"])
        self.assertGreater(result["portfolio_allocation_pct"], 0)
        self.assertLessEqual(result["portfolio_allocation_pct"], 10.0)

    def test_position_size_equals_shares(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market())
        self.assertEqual(result["position_size"], result["shares"])


@patch("risk.manager._has_recent_signal", return_value=False)
@patch("risk.manager._lookup_sector", return_value="Technology")
@patch("risk.manager._sector_exposure", return_value=0.0)
class TestPennyStockFilter(unittest.TestCase):

    def test_reject_penny_stock(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market(price=3.50))
        self.assertFalse(result["approved"])
        self.assertIn("Penny stock", result["reason"])

    def test_accept_at_minimum_price(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market(price=5.0))
        self.assertTrue(result["approved"])

    def test_accept_above_minimum(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market(price=50.0))
        self.assertTrue(result["approved"])


@patch("risk.manager._has_recent_signal", return_value=False)
@patch("risk.manager._lookup_sector", return_value="Technology")
@patch("risk.manager._sector_exposure", return_value=0.0)
class TestMarketCapFilter(unittest.TestCase):

    def test_reject_microcap(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market(market_cap=500_000_000))
        self.assertFalse(result["approved"])
        self.assertIn("Micro-cap", result["reason"])

    def test_accept_at_minimum_cap(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(), _portfolio(), _market(market_cap=1_000_000_000))
        self.assertTrue(result["approved"])

    def test_skip_check_if_no_cap_data(self, mock_sector_exp, mock_sector, mock_dup):
        """If market cap is unavailable, don't reject — skip the check."""
        result = check_trade(_signal(), _portfolio(), {"price": 50.0})
        self.assertTrue(result["approved"])


@patch("risk.manager._has_recent_signal", return_value=False)
@patch("risk.manager._lookup_sector", return_value="Technology")
@patch("risk.manager._sector_exposure", return_value=0.0)
class TestCashReserve(unittest.TestCase):

    def test_reject_insufficient_cash(self, mock_sector_exp, mock_sector, mock_dup):
        """Cash below 20% reserve threshold → reject."""
        portfolio = _portfolio(cash=10000, equity=100000)  # 10% cash, need 20%
        result = check_trade(_signal(), portfolio, _market())
        self.assertFalse(result["approved"])
        self.assertIn("cash", result["reason"].lower())

    def test_accept_sufficient_cash(self, mock_sector_exp, mock_sector, mock_dup):
        portfolio = _portfolio(cash=50000, equity=100000)
        result = check_trade(_signal(), portfolio, _market())
        self.assertTrue(result["approved"])

    def test_sell_ignores_cash_reserve(self, mock_sector_exp, mock_sector, mock_dup):
        """SELL signals don't require cash (shorting)."""
        portfolio = _portfolio(cash=5000, equity=100000)
        result = check_trade(_signal(direction="SELL"), portfolio, _market())
        self.assertTrue(result["approved"])


@patch("risk.manager._has_recent_signal", return_value=False)
@patch("risk.manager._lookup_sector", return_value="Technology")
@patch("risk.manager._sector_exposure", return_value=0.0)
class TestMaxPositions(unittest.TestCase):

    def test_reject_at_max_positions(self, mock_sector_exp, mock_sector, mock_dup):
        positions = [_position(ticker=f"T{i}") for i in range(MAX_OPEN_POSITIONS)]
        portfolio = _portfolio(positions=positions)
        result = check_trade(_signal(ticker="NEW"), portfolio, _market())
        self.assertFalse(result["approved"])
        self.assertIn("Max open positions", result["reason"])

    def test_allow_existing_ticker_at_max(self, mock_sector_exp, mock_sector, mock_dup):
        """If we already hold the ticker, adding to it is OK even at max positions."""
        positions = [_position(ticker=f"T{i}") for i in range(MAX_OPEN_POSITIONS)]
        positions[0] = _position(ticker="AAPL")
        portfolio = _portfolio(positions=positions)
        result = check_trade(_signal(ticker="AAPL"), portfolio, _market())
        self.assertTrue(result["approved"])


@patch("risk.manager._has_recent_signal", return_value=False)
@patch("risk.manager._lookup_sector", return_value="Technology")
@patch("risk.manager._sector_exposure", return_value=0.0)
class TestTickerAllocation(unittest.TestCase):

    def test_reject_ticker_overweight(self, mock_sector_exp, mock_sector, mock_dup):
        """Ticker already at 10% → reject new BUY."""
        positions = [_position(ticker="AAPL", market_value=10000)]  # 10% of 100k
        portfolio = _portfolio(positions=positions)
        result = check_trade(_signal(ticker="AAPL"), portfolio, _market())
        self.assertFalse(result["approved"])
        self.assertIn("allocation exceeded", result["reason"])

    def test_cap_shares_at_ticker_limit(self, mock_sector_exp, mock_sector, mock_dup):
        """If adding would exceed 10%, reduce shares to fit."""
        positions = [_position(ticker="AAPL", market_value=8000)]  # 8% of 100k
        portfolio = _portfolio(positions=positions)
        result = check_trade(_signal(ticker="AAPL"), portfolio, _market(price=100.0))
        self.assertTrue(result["approved"])
        # Max additional = $2000 at $100/share = 20 shares max
        self.assertLessEqual(result["shares"] * 100.0, 2000)


@patch("risk.manager._has_recent_signal", return_value=False)
@patch("risk.manager._lookup_sector", return_value="Technology")
class TestSectorConcentration(unittest.TestCase):

    def test_reject_sector_overweight(self, mock_sector, mock_dup):
        """Sector already at 30% → reject."""
        with patch("risk.manager._sector_exposure", return_value=30000):
            portfolio = _portfolio()
            result = check_trade(_signal(), portfolio, _market())
            self.assertFalse(result["approved"])
            self.assertIn("Sector allocation", result["reason"])

    def test_accept_sector_under_limit(self, mock_sector, mock_dup):
        with patch("risk.manager._sector_exposure", return_value=10000):
            result = check_trade(_signal(), _portfolio(), _market())
            self.assertTrue(result["approved"])


@patch("risk.manager._lookup_sector", return_value="Technology")
@patch("risk.manager._sector_exposure", return_value=0.0)
class TestDuplicateSignal(unittest.TestCase):

    def test_reject_duplicate_signal(self, mock_sector_exp, mock_sector):
        with patch("risk.manager._has_recent_signal", return_value=True):
            result = check_trade(_signal(), _portfolio(), _market())
            self.assertFalse(result["approved"])
            self.assertIn("Duplicate signal", result["reason"])

    def test_accept_no_duplicate(self, mock_sector_exp, mock_sector):
        with patch("risk.manager._has_recent_signal", return_value=False):
            result = check_trade(_signal(), _portfolio(), _market())
            self.assertTrue(result["approved"])


@patch("risk.manager._has_recent_signal", return_value=False)
@patch("risk.manager._lookup_sector", return_value="Technology")
@patch("risk.manager._sector_exposure", return_value=0.0)
class TestHoldSignal(unittest.TestCase):

    def test_hold_rejected(self, mock_sector_exp, mock_sector, mock_dup):
        result = check_trade(_signal(direction="HOLD"), _portfolio(), _market())
        self.assertFalse(result["approved"])
        self.assertIn("HOLD", result["reason"])


class TestCalculatePositionSize(unittest.TestCase):
    """Test position sizing formula."""

    def test_basic_sizing(self):
        """2% risk / 3% stop distance at $100, $100k portfolio.
        risk_budget=2000, stop_dist=3, base=666, *conf(1.0)=666.
        Capped at MAX_SHARES=500."""
        shares = calculate_position_size(
            _signal(confidence=1.0, regime="neutral"),
            _portfolio(equity=100000),
            _market(price=100.0),
        )
        self.assertEqual(shares, MAX_SHARES_PER_POSITION)

    def test_confidence_scales_down(self):
        shares_high = calculate_position_size(
            _signal(confidence=0.95),
            _portfolio(equity=100000),
            _market(price=100.0),
        )
        shares_low = calculate_position_size(
            _signal(confidence=0.55),
            _portfolio(equity=100000),
            _market(price=100.0),
        )
        self.assertGreater(shares_high, shares_low)

    def test_risk_off_reduces_size(self):
        shares_neutral = calculate_position_size(
            _signal(regime="neutral"),
            _portfolio(equity=100000),
            _market(price=100.0),
        )
        shares_risk_off = calculate_position_size(
            _signal(regime="risk_off"),
            _portfolio(equity=100000),
            _market(price=100.0),
        )
        self.assertLess(shares_risk_off, shares_neutral)

    def test_sector_overweight_reduces_size(self):
        shares_normal = calculate_position_size(
            _signal(), _portfolio(equity=100000), _market(price=100.0),
            sector_pct=0.0,
        )
        shares_overweight = calculate_position_size(
            _signal(), _portfolio(equity=100000), _market(price=100.0),
            sector_pct=0.25,
        )
        self.assertLess(shares_overweight, shares_normal)

    def test_capped_at_max_shares(self):
        shares = calculate_position_size(
            _signal(confidence=1.0),
            _portfolio(equity=1_000_000),
            _market(price=10.0),
        )
        self.assertLessEqual(shares, MAX_SHARES_PER_POSITION)

    def test_large_portfolio_still_capped_at_max_shares(self):
        """Even at 1M portfolio, max is MAX_SHARES_PER_POSITION."""
        shares = calculate_position_size(
            _signal(confidence=1.0),
            _portfolio(equity=1_000_000),
            _market(price=10.0),
        )
        self.assertEqual(shares, MAX_SHARES_PER_POSITION)

    def test_zero_price_returns_zero(self):
        shares = calculate_position_size(
            _signal(), _portfolio(), _market(price=0),
        )
        self.assertEqual(shares, 0)

    def test_zero_equity_returns_zero(self):
        shares = calculate_position_size(
            _signal(), _portfolio(equity=0), _market(price=100.0),
        )
        self.assertEqual(shares, 0)


class TestRejectHelper(unittest.TestCase):

    def test_reject_structure(self):
        result = _reject("test reason")
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "test reason")
        self.assertEqual(result["shares"], 0)
        self.assertEqual(result["position_size"], 0)
        self.assertEqual(result["entry_price"], 0.0)
        self.assertEqual(result["stop_loss"], 0.0)
        self.assertEqual(result["take_profit"], 0.0)
        self.assertEqual(result["portfolio_allocation_pct"], 0.0)


class TestFindPosition(unittest.TestCase):

    def test_find_existing(self):
        positions = [_position(ticker="AAPL"), _position(ticker="MSFT")]
        result = _find_position("AAPL", positions)
        self.assertIsNotNone(result)
        self.assertEqual(result["ticker"], "AAPL")

    def test_not_found(self):
        result = _find_position("TSLA", [_position(ticker="AAPL")])
        self.assertIsNone(result)

    def test_case_insensitive(self):
        result = _find_position("aapl", [_position(ticker="AAPL")])
        self.assertIsNotNone(result)


class TestPositionValue(unittest.TestCase):

    def test_with_position(self):
        self.assertEqual(_position_value(_position(market_value=5000)), 5000)

    def test_none_position(self):
        self.assertEqual(_position_value(None), 0.0)


if __name__ == "__main__":
    unittest.main()
