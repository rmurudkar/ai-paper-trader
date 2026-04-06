"""Tests for the feedback loop: logger, outcomes, and weights."""

import unittest
from unittest.mock import patch, MagicMock, call
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from feedback.logger import log_trade
from feedback.outcomes import (
    measure_outcomes,
    _classify_outcome,
    _evaluate_trade,
    _get_pending_trades,
    WIN_THRESHOLD,
    LOSS_THRESHOLD,
    DEFAULT_HOLDING_PERIOD,
)
from feedback.weights import (
    update_weights,
    check_circuit_breaker,
    trip_circuit_breaker,
    _ema_update,
    _get_rolling_win_rate,
    WEIGHT_LEARNING_RATE,
    MIN_WEIGHT,
    MAX_WEIGHT,
    INITIAL_WEIGHT,
    CIRCUIT_BREAKER_WIN_RATE,
    MIN_TRADES_FOR_CIRCUIT_BREAKER,
)


# ============================================================================
# feedback/logger.py
# ============================================================================

class TestLogTrade(unittest.TestCase):

    @patch("feedback.logger.log_trade.__module__", "feedback.logger")
    @patch("db.client.log_trade")
    def test_returns_uuid(self, mock_db_log):
        trade_data = {
            "ticker": "AAPL",
            "signal": "BUY",
            "confidence": 0.72,
            "entry_price": 175.50,
            "shares": 45,
            "strategies_fired": ["momentum"],
            "order_id": "abc123",
        }
        trade_id = log_trade(trade_data)
        self.assertIsInstance(trade_id, str)
        self.assertGreater(len(trade_id), 0)
        mock_db_log.assert_called_once()

    @patch("db.client.log_trade")
    def test_passes_all_fields_to_db(self, mock_db_log):
        trade_data = {
            "ticker": "TSLA",
            "signal": "SELL",
            "confidence": 0.85,
            "sentiment_score": -0.6,
            "sentiment_source": "newsapi",
            "strategies_fired": ["mean_reversion", "sentiment_divergence"],
            "discovery_sources": ["news", "loser"],
            "regime_mode": "risk_off",
            "article_urls": ["https://example.com/1"],
            "entry_price": 250.0,
            "shares": 30,
            "stop_loss_price": 257.50,
            "take_profit_price": 242.50,
            "order_id": "xyz789",
        }
        trade_id = log_trade(trade_data)
        self.assertTrue(len(trade_id) > 0)

        kwargs = mock_db_log.call_args
        self.assertEqual(kwargs[1]["ticker"], "TSLA")
        self.assertEqual(kwargs[1]["signal"], "SELL")
        self.assertEqual(kwargs[1]["confidence"], 0.85)
        self.assertEqual(kwargs[1]["sentiment_source"], "newsapi")
        self.assertEqual(kwargs[1]["strategies_fired"], ["mean_reversion", "sentiment_divergence"])

    @patch("db.client.log_trade", side_effect=Exception("DB down"))
    def test_returns_empty_on_failure(self, mock_db_log):
        result = log_trade({"ticker": "FAIL", "signal": "BUY"})
        self.assertEqual(result, "")

    @patch("db.client.log_trade")
    def test_handles_missing_optional_fields(self, mock_db_log):
        trade_id = log_trade({"ticker": "AAPL", "signal": "BUY"})
        self.assertGreater(len(trade_id), 0)
        mock_db_log.assert_called_once()


# ============================================================================
# feedback/outcomes.py — _classify_outcome
# ============================================================================

class TestClassifyOutcome(unittest.TestCase):

    def test_win(self):
        self.assertEqual(_classify_outcome(0.025), "WIN")

    def test_loss(self):
        self.assertEqual(_classify_outcome(-0.018), "LOSS")

    def test_neutral_positive(self):
        self.assertEqual(_classify_outcome(0.005), "NEUTRAL")

    def test_neutral_negative(self):
        self.assertEqual(_classify_outcome(-0.003), "NEUTRAL")

    def test_exactly_win_threshold(self):
        # > threshold, not >=
        self.assertEqual(_classify_outcome(WIN_THRESHOLD), "NEUTRAL")

    def test_above_win_threshold(self):
        self.assertEqual(_classify_outcome(WIN_THRESHOLD + 0.001), "WIN")

    def test_exactly_loss_threshold(self):
        self.assertEqual(_classify_outcome(LOSS_THRESHOLD), "NEUTRAL")

    def test_below_loss_threshold(self):
        self.assertEqual(_classify_outcome(LOSS_THRESHOLD - 0.001), "LOSS")

    def test_zero_return(self):
        self.assertEqual(_classify_outcome(0.0), "NEUTRAL")


# ============================================================================
# feedback/outcomes.py — _evaluate_trade
# ============================================================================

class TestEvaluateTrade(unittest.TestCase):

    def _trade(self, **overrides):
        from datetime import datetime, timezone, timedelta
        base = {
            "trade_id": "test-123",
            "ticker": "AAPL",
            "signal": "BUY",
            "entry_price": 100.0,
            "stop_loss_price": 97.0,
            "take_profit_price": 103.0,
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(),
            "strategies_fired": ["momentum"],
            "sentiment_source": "newsapi",
            "discovery_sources": ["news"],
        }
        base.update(overrides)
        return base

    @patch("feedback.outcomes._fetch_current_price", return_value=101.5)
    def test_buy_win_after_holding_period(self, mock_price):
        """Price up but below TP — exits on holding period."""
        result = _evaluate_trade(self._trade())
        self.assertIsNotNone(result)
        self.assertEqual(result["outcome"], "WIN")
        self.assertEqual(result["exit_reason"], "holding_period")
        self.assertAlmostEqual(result["return_pct"], 0.015, places=3)

    @patch("feedback.outcomes._fetch_current_price", return_value=95.0)
    def test_buy_loss_after_holding_period(self, mock_price):
        result = _evaluate_trade(self._trade())
        self.assertEqual(result["outcome"], "LOSS")
        self.assertAlmostEqual(result["return_pct"], -0.05, places=2)

    @patch("feedback.outcomes._fetch_current_price", return_value=95.0)
    def test_sell_win(self, mock_price):
        result = _evaluate_trade(self._trade(signal="SELL"))
        self.assertEqual(result["outcome"], "WIN")
        self.assertAlmostEqual(result["return_pct"], 0.05, places=2)

    @patch("feedback.outcomes._fetch_current_price", return_value=96.0)
    def test_stop_loss_hit_buy(self, mock_price):
        result = _evaluate_trade(self._trade(stop_loss_price=97.0))
        self.assertIsNotNone(result)
        self.assertEqual(result["exit_reason"], "stop_loss")

    @patch("feedback.outcomes._fetch_current_price", return_value=104.0)
    def test_take_profit_hit_buy(self, mock_price):
        result = _evaluate_trade(self._trade(take_profit_price=103.0))
        self.assertEqual(result["exit_reason"], "take_profit")

    @patch("feedback.outcomes._fetch_current_price", return_value=100.5)
    def test_not_ready_within_holding_period(self, mock_price):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        result = _evaluate_trade(self._trade(created_at=recent))
        self.assertIsNone(result)  # no exit criteria met

    @patch("feedback.outcomes._fetch_current_price", return_value=None)
    def test_no_price_returns_none(self, mock_price):
        result = _evaluate_trade(self._trade())
        self.assertIsNone(result)

    @patch("feedback.outcomes._fetch_current_price", return_value=105.0)
    def test_attribution_data_included(self, mock_price):
        result = _evaluate_trade(self._trade())
        self.assertEqual(result["strategies_fired"], ["momentum"])
        self.assertEqual(result["sentiment_source"], "newsapi")
        self.assertEqual(result["ticker"], "AAPL")


# ============================================================================
# feedback/outcomes.py — measure_outcomes integration
# ============================================================================

class TestMeasureOutcomes(unittest.TestCase):

    @patch("feedback.outcomes._get_pending_trades", return_value=[])
    def test_no_pending_trades(self, mock_pending):
        results = measure_outcomes()
        self.assertEqual(results, [])

    @patch("feedback.weights.update_weights")
    @patch("feedback.outcomes._record_outcome")
    @patch("feedback.outcomes._fetch_current_price", return_value=110.0)
    @patch("feedback.outcomes._get_pending_trades")
    def test_measures_and_records(self, mock_pending, mock_price, mock_record, mock_weights):
        from datetime import datetime, timezone, timedelta
        mock_pending.return_value = [{
            "trade_id": "t1",
            "ticker": "AAPL",
            "signal": "BUY",
            "entry_price": 100.0,
            "stop_loss_price": 97.0,
            "take_profit_price": 103.0,
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat(),
            "strategies_fired": ["momentum"],
            "sentiment_source": "newsapi",
            "discovery_sources": ["news"],
        }]
        results = measure_outcomes()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["outcome"], "WIN")
        mock_record.assert_called_once()
        mock_weights.assert_called_once()


# ============================================================================
# feedback/weights.py — _ema_update
# ============================================================================

class TestEmaUpdate(unittest.TestCase):

    def test_win_increases_weight(self):
        new = _ema_update(0.5, 1.0)
        self.assertAlmostEqual(new, 0.5 * 0.95 + 1.0 * 0.05)
        self.assertGreater(new, 0.5)

    def test_loss_decreases_weight(self):
        new = _ema_update(0.5, 0.0)
        self.assertAlmostEqual(new, 0.5 * 0.95 + 0.0 * 0.05)
        self.assertLess(new, 0.5)

    def test_clamped_at_min(self):
        new = _ema_update(MIN_WEIGHT, 0.0)
        self.assertGreaterEqual(new, MIN_WEIGHT)

    def test_clamped_at_max(self):
        new = _ema_update(MAX_WEIGHT, 1.0)
        self.assertLessEqual(new, MAX_WEIGHT)

    def test_consecutive_wins(self):
        w = INITIAL_WEIGHT
        for _ in range(10):
            w = _ema_update(w, 1.0)
        self.assertGreater(w, INITIAL_WEIGHT)
        self.assertLessEqual(w, MAX_WEIGHT)

    def test_consecutive_losses(self):
        w = INITIAL_WEIGHT
        for _ in range(10):
            w = _ema_update(w, 0.0)
        self.assertLess(w, INITIAL_WEIGHT)
        self.assertGreaterEqual(w, MIN_WEIGHT)


# ============================================================================
# feedback/weights.py — update_weights
# ============================================================================

class TestUpdateWeights(unittest.TestCase):

    @patch("db.client.set_weight")
    @patch("db.client.get_weight", return_value=0.5)
    @patch("feedback.weights.check_circuit_breaker", return_value=False)
    def test_win_updates_strategies(self, mock_cb, mock_get, mock_set):
        outcome = {
            "trade_id": "t1",
            "outcome": "WIN",
            "return_pct": 0.03,
            "strategies_fired": ["momentum", "mean_reversion"],
            "sentiment_source": "newsapi",
        }
        update_weights(outcome)
        # 2 strategy updates + 1 source update = 3 set_weight calls
        self.assertEqual(mock_set.call_count, 3)

    @patch("db.client.set_weight")
    @patch("db.client.get_weight", return_value=0.5)
    @patch("feedback.weights.check_circuit_breaker", return_value=False)
    def test_loss_updates_strategies(self, mock_cb, mock_get, mock_set):
        outcome = {
            "trade_id": "t2",
            "outcome": "LOSS",
            "return_pct": -0.02,
            "strategies_fired": ["momentum"],
            "sentiment_source": None,
        }
        update_weights(outcome)
        # 1 strategy update, no source update
        self.assertEqual(mock_set.call_count, 1)
        # Weight should decrease
        args = mock_set.call_args_list[0]
        new_weight = args[0][2]
        self.assertLess(new_weight, 0.5)

    @patch("db.client.set_weight")
    @patch("db.client.get_weight", return_value=0.5)
    @patch("feedback.weights.check_circuit_breaker", return_value=False)
    def test_neutral_no_update(self, mock_cb, mock_get, mock_set):
        update_weights({"trade_id": "t3", "outcome": "NEUTRAL", "strategies_fired": ["momentum"]})
        mock_set.assert_not_called()

    @patch("db.client.set_weight")
    @patch("db.client.get_weight", return_value=0.5)
    @patch("feedback.weights.check_circuit_breaker", return_value=False)
    def test_win_weight_value(self, mock_cb, mock_get, mock_set):
        update_weights({
            "trade_id": "t4",
            "outcome": "WIN",
            "strategies_fired": ["momentum"],
            "sentiment_source": None,
        })
        expected = 0.5 * 0.95 + 1.0 * 0.05  # 0.525
        actual = mock_set.call_args[0][2]
        self.assertAlmostEqual(actual, expected)

    @patch("db.client.set_weight")
    @patch("db.client.get_weight", return_value=0.5)
    @patch("feedback.weights.check_circuit_breaker", return_value=False)
    def test_loss_weight_value(self, mock_cb, mock_get, mock_set):
        update_weights({
            "trade_id": "t5",
            "outcome": "LOSS",
            "strategies_fired": ["momentum"],
            "sentiment_source": None,
        })
        expected = 0.5 * 0.95 + 0.0 * 0.05  # 0.475
        actual = mock_set.call_args[0][2]
        self.assertAlmostEqual(actual, expected)


# ============================================================================
# feedback/weights.py — check_circuit_breaker
# ============================================================================

class TestCheckCircuitBreaker(unittest.TestCase):

    @patch("feedback.weights._get_rolling_trade_count", return_value=20)
    @patch("feedback.weights._get_rolling_win_rate", return_value=0.35)
    @patch("db.client.is_circuit_breaker_tripped", return_value=False)
    def test_trips_on_low_win_rate(self, mock_tripped, mock_rate, mock_count):
        self.assertTrue(check_circuit_breaker())

    @patch("feedback.weights._get_rolling_trade_count", return_value=20)
    @patch("feedback.weights._get_rolling_win_rate", return_value=0.60)
    @patch("db.client.is_circuit_breaker_tripped", return_value=False)
    def test_ok_with_good_win_rate(self, mock_tripped, mock_rate, mock_count):
        self.assertFalse(check_circuit_breaker())

    @patch("feedback.weights._get_rolling_trade_count", return_value=5)
    @patch("feedback.weights._get_rolling_win_rate", return_value=0.20)
    @patch("db.client.is_circuit_breaker_tripped", return_value=False)
    def test_not_enough_trades(self, mock_tripped, mock_rate, mock_count):
        """Don't trip if fewer than MIN_TRADES_FOR_CIRCUIT_BREAKER."""
        self.assertFalse(check_circuit_breaker())

    @patch("db.client.is_circuit_breaker_tripped", return_value=True)
    def test_already_tripped(self, mock_tripped):
        """Don't re-trip if already active."""
        self.assertFalse(check_circuit_breaker())

    @patch("feedback.weights._get_rolling_trade_count", return_value=15)
    @patch("feedback.weights._get_rolling_win_rate", return_value=0.40)
    @patch("db.client.is_circuit_breaker_tripped", return_value=False)
    def test_exactly_at_threshold(self, mock_tripped, mock_rate, mock_count):
        """40% exactly should NOT trip (need < 40%)."""
        self.assertFalse(check_circuit_breaker())


# ============================================================================
# feedback/weights.py — trip_circuit_breaker
# ============================================================================

class TestTripCircuitBreaker(unittest.TestCase):

    @patch("feedback.weights._send_alert")
    @patch("db.client.trip_circuit_breaker")
    def test_trips_db_and_alerts(self, mock_db_trip, mock_alert):
        trip_circuit_breaker("Win rate low", 0.35)
        mock_db_trip.assert_called_once_with("Win rate low", 0.35)
        mock_alert.assert_called_once_with("Win rate low", 0.35)

    @patch("feedback.weights._send_alert")
    @patch("db.client.trip_circuit_breaker", side_effect=Exception("DB error"))
    def test_db_failure_logged(self, mock_db_trip, mock_alert):
        # Should not raise
        trip_circuit_breaker("test", 0.3)


if __name__ == "__main__":
    unittest.main()
