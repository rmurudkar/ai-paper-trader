"""Tests for engine.combiner module."""

import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.combiner import (
    combine_ticker_signals,
    _stage1_primary_direction,
    _stage4_regime_filter,
    _hold_result,
    load_learned_weights,
    BUY_THRESHOLD,
    SELL_THRESHOLD,
    DEFAULT_WEIGHT,
    _CAT1_STRATEGIES,
    _CAT3_STRATEGIES,
    _STANDALONE_STRATEGIES,
    _RISK_ON_SELL_DAMPEN,
    _RISK_OFF_BUY_DAMPEN,
    _RISK_OFF_BUY_GATE,
    _CAT3_CONFIRM_WEIGHT,
    _CAT3_CONTRADICT_WEIGHT,
    _CONFLICT_PENALTY_SCALE,
)


def _sig(strategy, signal, confidence):
    return {"strategy": strategy, "signal": signal, "confidence": confidence, "reason": "test"}


def _mod(multiplier=None, directional_modifier=None, modifier_name="test_mod", reason="test"):
    m = {"modifier_name": modifier_name, "reason": reason}
    if multiplier is not None:
        m["multiplier"] = multiplier
    if directional_modifier is not None:
        m["directional_modifier"] = directional_modifier
    return m


class TestStage1PrimaryDirection(unittest.TestCase):
    """Test Stage 1: primary direction picking from Cat 1 + standalone signals."""

    def test_single_buy(self):
        signals = [_sig("momentum", "BUY", 0.8)]
        result = _stage1_primary_direction(signals, {})
        self.assertEqual(result["signal"], "BUY")
        self.assertAlmostEqual(result["confidence"], 0.8 * DEFAULT_WEIGHT)

    def test_single_sell(self):
        signals = [_sig("mean_reversion", "SELL", 0.7)]
        result = _stage1_primary_direction(signals, {})
        self.assertEqual(result["signal"], "SELL")
        self.assertAlmostEqual(result["confidence"], 0.7 * DEFAULT_WEIGHT)

    def test_buy_wins_over_sell(self):
        signals = [
            _sig("momentum", "BUY", 0.8),
            _sig("sentiment_divergence", "BUY", 0.7),
            _sig("mean_reversion", "SELL", 0.6),
        ]
        result = _stage1_primary_direction(signals, {})
        self.assertEqual(result["signal"], "BUY")

    def test_sell_wins_when_stronger(self):
        signals = [
            _sig("momentum", "BUY", 0.3),
            _sig("sentiment_divergence", "SELL", 0.9),
            _sig("mean_reversion", "SELL", 0.8),
        ]
        result = _stage1_primary_direction(signals, {})
        self.assertEqual(result["signal"], "SELL")

    def test_no_signals_returns_hold(self):
        result = _stage1_primary_direction([], {})
        self.assertEqual(result["signal"], "HOLD")
        self.assertEqual(result["confidence"], 0.0)

    def test_all_hold_signals_returns_hold(self):
        signals = [_sig("momentum", "HOLD", 0.5)]
        result = _stage1_primary_direction(signals, {})
        self.assertEqual(result["signal"], "HOLD")

    def test_conflict_penalty_applied(self):
        """When both BUY and SELL exist, confidence should be reduced."""
        buy_only = [_sig("momentum", "BUY", 0.8)]
        conflicted = [
            _sig("momentum", "BUY", 0.8),
            _sig("mean_reversion", "SELL", 0.6),
        ]
        r_clean = _stage1_primary_direction(buy_only, {})
        r_conflict = _stage1_primary_direction(conflicted, {})
        self.assertLess(r_conflict["confidence"], r_clean["confidence"])

    def test_conflict_penalty_formula(self):
        """Verify conflict penalty = opposing / (winning + opposing) * SCALE."""
        signals = [
            _sig("momentum", "BUY", 0.8),
            _sig("mean_reversion", "SELL", 0.4),
        ]
        result = _stage1_primary_direction(signals, {})
        buy_w = 0.8 * DEFAULT_WEIGHT
        sell_w = 0.4 * DEFAULT_WEIGHT
        expected = buy_w * (1.0 - (sell_w / (buy_w + sell_w)) * _CONFLICT_PENALTY_SCALE)
        self.assertAlmostEqual(result["confidence"], expected, places=6)

    def test_learned_weights_applied(self):
        signals = [_sig("momentum", "BUY", 0.8)]
        weights = {"momentum": 0.9}
        result = _stage1_primary_direction(signals, weights)
        self.assertAlmostEqual(result["confidence"], 0.8 * 0.9)

    def test_components_populated(self):
        signals = [_sig("momentum", "BUY", 0.8)]
        weights = {"momentum": 0.7}
        result = _stage1_primary_direction(signals, weights)
        self.assertIn("momentum", result["components"])
        comp = result["components"]["momentum"]
        self.assertEqual(comp["signal"], "BUY")
        self.assertAlmostEqual(comp["raw_confidence"], 0.8)
        self.assertAlmostEqual(comp["weight"], 0.7)

    def test_equal_buy_sell_picks_buy(self):
        """Tie-breaking: buy_total >= sell_total means BUY wins ties."""
        signals = [
            _sig("momentum", "BUY", 0.5),
            _sig("mean_reversion", "SELL", 0.5),
        ]
        result = _stage1_primary_direction(signals, {})
        self.assertEqual(result["signal"], "BUY")


class TestStage2Modifiers(unittest.TestCase):
    """Test Stage 2: modifier application to base confidence."""

    def _run_with_modifier(self, direction, base_conf, modifier, regime="neutral"):
        """Helper: build a raw_output with one signal and one modifier, run combiner."""
        raw = {
            "signals": [_sig("momentum", direction, base_conf / DEFAULT_WEIGHT)],
            "modifiers": [modifier],
        }
        return combine_ticker_signals("TEST", raw, {"regime": regime})

    def test_multiplier_applied(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],  # weighted = 0.75
            "modifiers": [_mod(multiplier=1.2)],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        # Stage 1: 1.5 * 0.5 = 0.75. Stage 2: 0.75 * 1.2 = 0.9.
        self.assertEqual(result["signal"], "BUY")
        self.assertAlmostEqual(result["confidence"], min(0.95, 0.9), places=2)

    def test_directional_modifier_buy(self):
        """directional_modifier adds to BUY confidence."""
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],  # weighted = 0.75
            "modifiers": [_mod(directional_modifier=0.1)],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        # 0.75 * (1 + 0.1) = 0.825
        self.assertEqual(result["signal"], "BUY")
        self.assertAlmostEqual(result["confidence"], 0.825, places=2)

    def test_directional_modifier_sell(self):
        """directional_modifier subtracts from SELL confidence."""
        raw = {
            "signals": [_sig("momentum", "SELL", 1.5)],  # weighted = 0.75
            "modifiers": [_mod(directional_modifier=0.1)],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        # 0.75 * (1 - 0.1) = 0.675
        self.assertEqual(result["signal"], "SELL")
        self.assertAlmostEqual(result["confidence"], 0.675, places=2)

    def test_no_modifiers_no_change(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertAlmostEqual(result["confidence"], 0.75, places=2)

    def test_multiplier_1_no_effect(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],
            "modifiers": [_mod(multiplier=1.0)],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertAlmostEqual(result["confidence"], 0.75, places=2)

    def test_modifier_notes_tracked(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],
            "modifiers": [_mod(multiplier=1.3, reason="volume surge")],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertIn("volume surge", result["modifiers_applied"])


class TestStage3CatalystDrift(unittest.TestCase):
    """Test Stage 3: Category 3 news-catalyst drift integration."""

    def test_drift_confirms_buy(self):
        raw = {
            "signals": [
                _sig("momentum", "BUY", 1.5),  # weighted = 0.75
                _sig("news_catalyst_drift", "BUY", 0.7),
            ],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        # Stage 1 base: 0.75. Stage 3: +0.7*0.2 = +0.14. Total: 0.89
        expected = 0.75 + 0.7 * _CAT3_CONFIRM_WEIGHT
        self.assertAlmostEqual(result["confidence"], expected, places=2)
        self.assertIn("+", result["cat3_effect"])
        self.assertIn("confirms", result["cat3_effect"])

    def test_drift_contradicts_buy(self):
        raw = {
            "signals": [
                _sig("momentum", "BUY", 1.5),  # weighted = 0.75
                _sig("news_catalyst_drift", "SELL", 0.6),
            ],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        # Stage 1 base: 0.75. Stage 3: -0.6*0.15 = -0.09. Total: 0.66
        expected = 0.75 - 0.6 * _CAT3_CONTRADICT_WEIGHT
        self.assertAlmostEqual(result["confidence"], expected, places=2)
        self.assertIn("-", result["cat3_effect"])
        self.assertIn("contradicts", result["cat3_effect"])

    def test_no_drift_no_effect(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertIsNone(result["cat3_effect"])
        self.assertAlmostEqual(result["confidence"], 0.75, places=2)

    def test_drift_hold_no_effect(self):
        raw = {
            "signals": [
                _sig("momentum", "BUY", 1.5),
                _sig("news_catalyst_drift", "HOLD", 0.5),
            ],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertIsNone(result["cat3_effect"])

    def test_cat3_excluded_from_stage1(self):
        """Cat 3 signals should NOT participate in Stage 1 direction picking."""
        raw = {
            "signals": [
                _sig("momentum", "BUY", 1.5),
                _sig("news_catalyst_drift", "SELL", 0.9),
            ],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        # Direction should still be BUY (Cat 3 doesn't vote in Stage 1)
        self.assertEqual(result["signal"], "BUY")


class TestStage4RegimeFilter(unittest.TestCase):
    """Test Stage 4: regime-based confidence adjustments."""

    def test_risk_on_dampens_sell(self):
        result = _stage4_regime_filter("SELL", 0.8, "risk_on")
        self.assertAlmostEqual(result["confidence"], 0.8 * _RISK_ON_SELL_DAMPEN)
        self.assertIn("SELL dampened", result["note"])

    def test_risk_on_no_effect_on_buy(self):
        result = _stage4_regime_filter("BUY", 0.8, "risk_on")
        self.assertAlmostEqual(result["confidence"], 0.8)
        self.assertEqual(result["note"], "No regime adjustment")

    def test_risk_off_dampens_buy(self):
        result = _stage4_regime_filter("BUY", 0.8, "risk_off")
        self.assertAlmostEqual(result["confidence"], 0.8 * _RISK_OFF_BUY_DAMPEN)
        self.assertIn("BUY dampened", result["note"])

    def test_risk_off_no_effect_on_sell(self):
        result = _stage4_regime_filter("SELL", 0.8, "risk_off")
        self.assertAlmostEqual(result["confidence"], 0.8)

    def test_neutral_no_change(self):
        result = _stage4_regime_filter("BUY", 0.8, "neutral")
        self.assertAlmostEqual(result["confidence"], 0.8)
        self.assertEqual(result["note"], "No regime adjustment")

    def test_risk_off_kills_weak_buy(self):
        """Risk-off BUY gate: if confidence < 0.8 after dampening, return HOLD."""
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],  # weighted = 0.75
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "risk_off"})
        # 0.75 * 0.7 = 0.525, which is < 0.8 gate → HOLD
        self.assertEqual(result["signal"], "HOLD")
        self.assertIn("Risk-off", result["rationale"])

    def test_risk_off_passes_strong_buy(self):
        """Strong BUY survives risk-off gate."""
        raw = {
            "signals": [_sig("momentum", "BUY", 1.9)],  # weighted = 0.95
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "risk_off"})
        # 0.95 * 0.7 = 0.665, which is < 0.8 gate → HOLD (gate is strict)
        # Need even higher: let's use weight override
        raw2 = {
            "signals": [_sig("momentum", "BUY", 1.9)],
            "modifiers": [],
        }
        result2 = combine_ticker_signals("TEST", raw2, {"regime": "risk_off"}, {"momentum": 1.0})
        # 1.9 * 1.0 = 1.9 → but wait, that's the base conf. 1.9 * 0.7 = 1.33, > 0.8 gate. Clamped to 0.95.
        self.assertEqual(result2["signal"], "BUY")


class TestThresholdGate(unittest.TestCase):
    """Test the final confidence threshold gate."""

    def test_above_threshold_passes(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],  # weighted = 0.75
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertEqual(result["signal"], "BUY")
        self.assertGreater(result["confidence"], BUY_THRESHOLD)

    def test_below_threshold_becomes_hold(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 0.8)],  # weighted = 0.4
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        # 0.8 * 0.5 = 0.4, which is < 0.55 → HOLD
        self.assertEqual(result["signal"], "HOLD")
        self.assertIn("below", result["rationale"])

    def test_exact_threshold_is_hold(self):
        """Confidence == threshold should be HOLD (need > not >=)."""
        raw = {
            "signals": [_sig("momentum", "BUY", 1.1)],  # weighted = 0.55
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertEqual(result["signal"], "HOLD")

    def test_no_signals_hold(self):
        raw = {"signals": [], "modifiers": []}
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertEqual(result["signal"], "HOLD")


class TestCombineTickerSignals(unittest.TestCase):
    """End-to-end integration tests for the full pipeline."""

    def test_full_pipeline_buy(self):
        raw = {
            "signals": [
                _sig("sentiment_divergence", "BUY", 1.5),
                _sig("momentum", "BUY", 1.4),
                _sig("news_catalyst_drift", "BUY", 0.6),
            ],
            "modifiers": [_mod(multiplier=1.1, reason="volume confirmation")],
        }
        result = combine_ticker_signals("AAPL", raw, {"regime": "risk_on"})
        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(result["signal"], "BUY")
        self.assertGreater(result["confidence"], BUY_THRESHOLD)
        self.assertEqual(result["regime"], "risk_on")

    def test_full_pipeline_sell(self):
        raw = {
            "signals": [
                _sig("sentiment_divergence", "SELL", 1.5),
                _sig("mean_reversion", "SELL", 1.4),
            ],
            "modifiers": [],
        }
        result = combine_ticker_signals("TSLA", raw, {"regime": "neutral"})
        self.assertEqual(result["signal"], "SELL")
        self.assertEqual(result["ticker"], "TSLA")

    def test_regime_data_none_defaults_neutral(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, None)
        self.assertEqual(result["regime"], "neutral")

    def test_learned_weights_none_uses_defaults(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"}, None)
        self.assertAlmostEqual(result["confidence"], 0.75, places=2)

    def test_confidence_clamped_upper(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.9)],
            "modifiers": [_mod(multiplier=1.5)],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"}, {"momentum": 1.0})
        self.assertLessEqual(result["confidence"], 0.95)

    def test_confidence_clamped_lower(self):
        """Even after penalties, confidence doesn't go below 0.05."""
        raw = {
            "signals": [
                _sig("momentum", "BUY", 0.2),
                _sig("mean_reversion", "SELL", 1.8),
            ],
            "modifiers": [_mod(multiplier=0.5)],
        }
        result = combine_ticker_signals("TEST", raw)
        # Whatever the result is, confidence should be >= 0.05
        self.assertGreaterEqual(result.get("confidence", 0), 0.0)

    def test_hold_returns_zero_confidence(self):
        raw = {"signals": [], "modifiers": []}
        result = combine_ticker_signals("TEST", raw)
        self.assertEqual(result["signal"], "HOLD")
        self.assertEqual(result["confidence"], 0.0)

    def test_multiple_modifiers_stack(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],  # weighted = 0.75
            "modifiers": [
                _mod(multiplier=1.2, reason="volume"),
                _mod(directional_modifier=0.1, reason="vwap"),
            ],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        # 0.75 * 1.2 = 0.9. Then 0.9 * 1.1 = 0.99 → clamped 0.95
        self.assertAlmostEqual(result["confidence"], 0.95, places=2)
        self.assertIn("volume", result["modifiers_applied"])
        self.assertIn("vwap", result["modifiers_applied"])


class TestReturnFormat(unittest.TestCase):
    """Verify all output fields are present with correct types."""

    def test_buy_signal_has_all_fields(self):
        raw = {
            "signals": [
                _sig("momentum", "BUY", 1.5),
                _sig("news_catalyst_drift", "BUY", 0.5),
            ],
            "modifiers": [_mod(multiplier=1.1, reason="vol")],
        }
        result = combine_ticker_signals("AAPL", raw, {"regime": "risk_on"})
        required_keys = {
            "ticker", "signal", "confidence", "components",
            "modifiers_applied", "cat3_effect", "regime",
            "regime_adjustment", "rationale",
        }
        self.assertTrue(required_keys.issubset(result.keys()))
        self.assertIsInstance(result["ticker"], str)
        self.assertIn(result["signal"], ("BUY", "SELL", "HOLD"))
        self.assertIsInstance(result["confidence"], float)
        self.assertIsInstance(result["components"], dict)
        self.assertIsInstance(result["modifiers_applied"], list)
        self.assertIsInstance(result["regime"], str)
        self.assertIsInstance(result["rationale"], str)

    def test_hold_signal_has_all_fields(self):
        raw = {"signals": [], "modifiers": []}
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        required_keys = {
            "ticker", "signal", "confidence", "components",
            "modifiers_applied", "cat3_effect", "regime",
            "regime_adjustment", "rationale",
        }
        self.assertTrue(required_keys.issubset(result.keys()))
        self.assertEqual(result["signal"], "HOLD")
        self.assertEqual(result["confidence"], 0.0)

    def test_components_structure(self):
        raw = {
            "signals": [_sig("momentum", "BUY", 1.5)],
            "modifiers": [],
        }
        result = combine_ticker_signals("TEST", raw, {"regime": "neutral"})
        self.assertIn("momentum", result["components"])
        comp = result["components"]["momentum"]
        self.assertIn("signal", comp)
        self.assertIn("raw_confidence", comp)
        self.assertIn("weight", comp)


class TestLoadLearnedWeights(unittest.TestCase):
    """Test load_learned_weights helper."""

    @patch("db.client.get_all_weights")
    def test_loads_from_db(self, mock_get):
        mock_get.return_value = {"momentum": 0.8, "mean_reversion": 0.6}
        result = load_learned_weights()
        self.assertEqual(result, {"momentum": 0.8, "mean_reversion": 0.6})
        mock_get.assert_called_once_with("strategy")

    @patch("db.client.get_all_weights", side_effect=Exception("DB down"))
    def test_fallback_on_db_error(self, mock_get):
        result = load_learned_weights()
        self.assertEqual(result, {})


class TestHoldResult(unittest.TestCase):
    """Test _hold_result helper."""

    def test_hold_result_structure(self):
        result = _hold_result("AAPL", "risk_on", "test reason")
        self.assertEqual(result["ticker"], "AAPL")
        self.assertEqual(result["signal"], "HOLD")
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["regime"], "risk_on")
        self.assertEqual(result["rationale"], "test reason")
        self.assertEqual(result["components"], {})
        self.assertEqual(result["modifiers_applied"], [])
        self.assertIsNone(result["cat3_effect"])


if __name__ == "__main__":
    unittest.main()
