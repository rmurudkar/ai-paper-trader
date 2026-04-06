"""Tests for engine.strategies module."""

import unittest
from unittest.mock import patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.strategies import (
    run_all_strategies,
    sentiment_price_divergence,
    multi_source_consensus,
    sentiment_momentum,
    news_catalyst_drift,
    volume_confirmation,
    vwap_position,
    relative_strength,
    momentum_signal,
    mean_reversion_signal,
    _apply_enrichment_boost,
)


class TestSentimentPriceDivergence(unittest.TestCase):
    """Test the sentiment-price divergence strategy."""

    def _market(self, **overrides):
        base = {"price": 175.0, "price_change_pct": 0.0, "ma_20": 170.0, "ma_50": 165.0,
                "rsi": 50.0, "volume": 50_000_000, "avg_volume_20": 45_000_000}
        base.update(overrides)
        return base

    def _sentiment(self, score=0.7, articles=2, sources=None):
        if sources is None:
            sources = {"marketaux": 1, "newsapi": 1}
        return {
            "ticker": "AAPL",
            "sentiment_score": score,
            "article_count": articles,
            "source_breakdown": sources,
            "confidence": 0.5,
        }

    def test_buy_bullish_sentiment_flat_price(self):
        """Bullish sentiment + flat price = BUY."""
        result = sentiment_price_divergence("AAPL", self._market(price_change_pct=0.1), self._sentiment(score=0.7))
        self.assertEqual(result["signal"], "BUY")
        self.assertGreater(result["confidence"], 0.5)
        self.assertEqual(result["strategy"], "sentiment_divergence")

    def test_buy_bullish_sentiment_negative_price(self):
        """Bullish sentiment + negative price = BUY (even stronger divergence)."""
        result = sentiment_price_divergence("AAPL", self._market(price_change_pct=-1.0), self._sentiment(score=0.8))
        self.assertEqual(result["signal"], "BUY")

    def test_sell_bearish_sentiment_flat_price(self):
        """Bearish sentiment + flat price = SELL."""
        result = sentiment_price_divergence("AAPL", self._market(price_change_pct=-0.2), self._sentiment(score=-0.7))
        self.assertEqual(result["signal"], "SELL")
        self.assertGreater(result["confidence"], 0.5)

    def test_sell_bearish_sentiment_positive_price(self):
        """Bearish sentiment + positive price = SELL (divergence)."""
        result = sentiment_price_divergence("AAPL", self._market(price_change_pct=1.0), self._sentiment(score=-0.8))
        self.assertEqual(result["signal"], "SELL")

    def test_hold_weak_sentiment(self):
        """Weak sentiment (abs < 0.5) = HOLD."""
        result = sentiment_price_divergence("AAPL", self._market(), self._sentiment(score=0.3))
        self.assertEqual(result["signal"], "HOLD")

    def test_hold_no_sentiment_data(self):
        """No sentiment data = HOLD."""
        result = sentiment_price_divergence("AAPL", self._market(), None)
        self.assertEqual(result["signal"], "HOLD")

    def test_hold_zero_articles(self):
        """Zero articles = HOLD."""
        result = sentiment_price_divergence("AAPL", self._market(), self._sentiment(articles=0))
        self.assertEqual(result["signal"], "HOLD")

    def test_no_divergence_bullish_price_already_up(self):
        """Bullish sentiment + price already up = no divergence, HOLD."""
        result = sentiment_price_divergence("AAPL", self._market(price_change_pct=2.0), self._sentiment(score=0.7))
        self.assertEqual(result["signal"], "HOLD")

    def test_no_divergence_bearish_price_already_down(self):
        """Bearish sentiment + price already down = no divergence, HOLD."""
        result = sentiment_price_divergence("AAPL", self._market(price_change_pct=-2.0), self._sentiment(score=-0.7))
        self.assertEqual(result["signal"], "HOLD")

    def test_confidence_scales_with_sentiment_strength(self):
        """Stronger sentiment = higher confidence."""
        weak = sentiment_price_divergence("AAPL", self._market(), self._sentiment(score=0.55))
        strong = sentiment_price_divergence("AAPL", self._market(), self._sentiment(score=0.95))
        self.assertGreater(strong["confidence"], weak["confidence"])

    def test_confidence_scales_with_price_flatness(self):
        """Flatter price = higher confidence (more divergence)."""
        flat = sentiment_price_divergence("AAPL", self._market(price_change_pct=0.0), self._sentiment(score=0.7))
        moved = sentiment_price_divergence("AAPL", self._market(price_change_pct=0.4), self._sentiment(score=0.7))
        self.assertGreater(flat["confidence"], moved["confidence"])

    def test_confidence_boost_multiple_articles(self):
        """3+ articles should boost confidence."""
        few = sentiment_price_divergence("AAPL", self._market(), self._sentiment(score=0.7, articles=1, sources={"marketaux": 1}))
        many = sentiment_price_divergence("AAPL", self._market(), self._sentiment(score=0.7, articles=4, sources={"marketaux": 2, "newsapi": 2}))
        self.assertGreater(many["confidence"], few["confidence"])

    def test_confidence_clamped(self):
        """Confidence should never exceed 0.95."""
        result = sentiment_price_divergence("AAPL", self._market(), self._sentiment(score=0.99, articles=10, sources={"a": 3, "b": 3, "c": 4}))
        self.assertLessEqual(result["confidence"], 0.95)


class TestMultiSourceConsensus(unittest.TestCase):
    """Test the multi-source consensus strategy."""

    def _sentiment(self, scores, sources):
        """Build sentiment_data from per-article scores and source breakdown."""
        return {
            "ticker": "AAPL",
            "sentiment_score": sum(scores) / len(scores) if scores else 0.0,
            "article_count": len(scores),
            "source_breakdown": sources,
            "confidence": 0.5,
            "individual_scores": scores,
        }

    def test_buy_consensus_bullish(self):
        """3+ articles, 2+ sources, all > +0.3 = BUY."""
        sentiment = self._sentiment(
            [0.5, 0.6, 0.7],
            {"marketaux": 1, "newsapi": 2},
        )
        result = multi_source_consensus("AAPL", sentiment)
        self.assertEqual(result["signal"], "BUY")
        self.assertGreater(result["confidence"], 0.0)
        self.assertIn("Consensus bullish", result["reason"])

    def test_sell_consensus_bearish(self):
        """3+ articles, 2+ sources, all < -0.3 = SELL."""
        sentiment = self._sentiment(
            [-0.5, -0.6, -0.4],
            {"marketaux": 1, "massive": 2},
        )
        result = multi_source_consensus("AAPL", sentiment)
        self.assertEqual(result["signal"], "SELL")
        self.assertIn("Consensus bearish", result["reason"])

    def test_hold_mixed_signals(self):
        """Some positive, some negative = HOLD."""
        sentiment = self._sentiment(
            [0.5, -0.4, 0.6],
            {"marketaux": 1, "newsapi": 2},
        )
        result = multi_source_consensus("AAPL", sentiment)
        self.assertEqual(result["signal"], "HOLD")
        self.assertIn("Mixed signals", result["reason"])

    def test_hold_too_few_articles(self):
        """Only 2 articles = HOLD (need 3+)."""
        sentiment = self._sentiment(
            [0.5, 0.7],
            {"marketaux": 1, "newsapi": 1},
        )
        result = multi_source_consensus("AAPL", sentiment)
        self.assertEqual(result["signal"], "HOLD")
        self.assertIn("Insufficient", result["reason"])

    def test_hold_single_source(self):
        """3 articles but all from one source = HOLD (need 2+ sources)."""
        sentiment = self._sentiment(
            [0.5, 0.6, 0.7],
            {"marketaux": 3},
        )
        result = multi_source_consensus("AAPL", sentiment)
        self.assertEqual(result["signal"], "HOLD")
        self.assertIn("Insufficient", result["reason"])

    def test_hold_scores_near_zero(self):
        """Scores between -0.3 and +0.3 = no consensus (too weak)."""
        sentiment = self._sentiment(
            [0.1, 0.2, 0.15],
            {"marketaux": 1, "newsapi": 2},
        )
        result = multi_source_consensus("AAPL", sentiment)
        self.assertEqual(result["signal"], "HOLD")

    def test_hold_no_sentiment_data(self):
        result = multi_source_consensus("AAPL", None)
        self.assertEqual(result["signal"], "HOLD")

    def test_confidence_scales_with_article_count(self):
        """More articles = higher confidence (up to cap)."""
        few = multi_source_consensus("AAPL", self._sentiment(
            [0.5, 0.6, 0.7], {"a": 1, "b": 2},
        ))
        many = multi_source_consensus("AAPL", self._sentiment(
            [0.5, 0.6, 0.7, 0.55, 0.65], {"a": 2, "b": 3},
        ))
        self.assertGreater(many["confidence"], few["confidence"])

    def test_confidence_scales_with_magnitude(self):
        """Stronger sentiment = higher confidence."""
        weak = multi_source_consensus("AAPL", self._sentiment(
            [0.35, 0.4, 0.38], {"a": 1, "b": 2},
        ))
        strong = multi_source_consensus("AAPL", self._sentiment(
            [0.8, 0.9, 0.85], {"a": 1, "b": 2},
        ))
        self.assertGreater(strong["confidence"], weak["confidence"])

    def test_confidence_clamped(self):
        """Confidence should not exceed 0.95."""
        sentiment = self._sentiment(
            [0.95, 0.98, 0.99, 0.97, 0.96, 0.94, 0.93],
            {"a": 3, "b": 2, "c": 2},
        )
        result = multi_source_consensus("AAPL", sentiment)
        self.assertLessEqual(result["confidence"], 0.95)

    def test_one_outlier_blocks_consensus(self):
        """A single score on the wrong side of 0.3 blocks the signal."""
        sentiment = self._sentiment(
            [0.5, 0.6, 0.2],  # 0.2 < 0.3, breaks consensus
            {"a": 1, "b": 2},
        )
        result = multi_source_consensus("AAPL", sentiment)
        self.assertEqual(result["signal"], "HOLD")


@patch("engine.strategies.get_previous_sentiment")
class TestSentimentMomentum(unittest.TestCase):
    """Test the sentiment momentum (narrative shift) strategy."""

    def _sentiment(self, score=0.6, articles=3):
        return {
            "ticker": "AAPL",
            "sentiment_score": score,
            "article_count": articles,
            "source_breakdown": {"marketaux": 1, "newsapi": 2},
            "confidence": 0.5,
            "individual_scores": [score] * articles,
        }

    def test_buy_sentiment_shift_bullish(self, mock_prev):
        """Sentiment jumped > +0.4 from last cycle = BUY."""
        mock_prev.return_value = {"sentiment_score": 0.0, "article_count": 2, "recorded_at": "2026-04-05T14:00:00Z"}
        result = sentiment_momentum("AAPL", self._sentiment(score=0.6))
        self.assertEqual(result["signal"], "BUY")
        self.assertIn("shifting bullish", result["reason"])

    def test_sell_sentiment_shift_bearish(self, mock_prev):
        """Sentiment dropped > -0.4 from last cycle = SELL."""
        mock_prev.return_value = {"sentiment_score": 0.3, "article_count": 2, "recorded_at": "2026-04-05T14:00:00Z"}
        result = sentiment_momentum("AAPL", self._sentiment(score=-0.3))
        self.assertEqual(result["signal"], "SELL")
        self.assertIn("shifting bearish", result["reason"])

    def test_hold_small_delta(self, mock_prev):
        """Delta < 0.4 = HOLD (sentiment stable)."""
        mock_prev.return_value = {"sentiment_score": 0.4, "article_count": 2, "recorded_at": "2026-04-05T14:00:00Z"}
        result = sentiment_momentum("AAPL", self._sentiment(score=0.6))
        self.assertEqual(result["signal"], "HOLD")
        self.assertIn("stable", result["reason"])

    def test_hold_no_previous(self, mock_prev):
        """No prior cycle data = HOLD (first observation)."""
        mock_prev.return_value = None
        result = sentiment_momentum("AAPL", self._sentiment(score=0.6))
        self.assertEqual(result["signal"], "HOLD")
        self.assertIn("first observation", result["reason"])

    def test_hold_no_sentiment_data(self, mock_prev):
        """No current sentiment data = HOLD."""
        result = sentiment_momentum("AAPL", None)
        self.assertEqual(result["signal"], "HOLD")
        mock_prev.assert_not_called()

    def test_hold_zero_articles(self, mock_prev):
        """Zero articles in current cycle = HOLD."""
        result = sentiment_momentum("AAPL", self._sentiment(articles=0))
        self.assertEqual(result["signal"], "HOLD")
        mock_prev.assert_not_called()

    def test_confidence_scales_with_delta(self, mock_prev):
        """Larger delta = higher confidence."""
        mock_prev.return_value = {"sentiment_score": -0.5, "article_count": 2, "recorded_at": "2026-04-05T14:00:00Z"}

        small_shift = sentiment_momentum("AAPL", self._sentiment(score=0.0))  # delta=0.5
        big_shift = sentiment_momentum("AAPL", self._sentiment(score=0.4))    # delta=0.9

        self.assertGreater(big_shift["confidence"], small_shift["confidence"])

    def test_confidence_capped(self, mock_prev):
        """Confidence should not exceed 0.95 even with extreme shift."""
        mock_prev.return_value = {"sentiment_score": -1.0, "article_count": 2, "recorded_at": "2026-04-05T14:00:00Z"}
        result = sentiment_momentum("AAPL", self._sentiment(score=1.0))  # delta=2.0
        self.assertLessEqual(result["confidence"], 0.95)

    def test_db_error_returns_hold(self, mock_prev):
        """DB failure should return HOLD, not crash."""
        mock_prev.side_effect = Exception("DB connection failed")
        result = sentiment_momentum("AAPL", self._sentiment(score=0.6))
        self.assertEqual(result["signal"], "HOLD")

    def test_exact_boundary_0_4(self, mock_prev):
        """Delta of exactly 0.4 should NOT fire (need > 0.4)."""
        mock_prev.return_value = {"sentiment_score": 0.2, "article_count": 2, "recorded_at": "2026-04-05T14:00:00Z"}
        result = sentiment_momentum("AAPL", self._sentiment(score=0.6))  # delta=0.4 exactly
        self.assertEqual(result["signal"], "HOLD")


class TestNewsCatalystDrift(unittest.TestCase):
    """Test the post-news drift strategy."""

    def _market(self, **overrides):
        base = {"price": 180.0, "prev_close": 175.0, "day_high": 181.0,
                "day_low": 178.0, "price_change_pct": 2.86,
                "volume": 50_000_000, "avg_volume_20": 45_000_000}
        base.update(overrides)
        return base

    def test_buy_gap_up_near_high(self):
        """Gapped up >2%, price near day high = BUY (drift intact)."""
        # price=180, prev_close=175 → gap +2.86%, day_high=181 → 0.55% from high
        result = news_catalyst_drift("AAPL", self._market())
        self.assertEqual(result["signal"], "BUY")
        self.assertIn("gapped up", result["reason"])

    def test_buy_large_gap_higher_confidence(self):
        """Larger gaps should produce higher confidence."""
        small_gap = news_catalyst_drift("AAPL", self._market(
            price=103.5, prev_close=100.0, day_high=104.0, day_low=102.0))  # 3.5% gap
        large_gap = news_catalyst_drift("AAPL", self._market(
            price=107.0, prev_close=100.0, day_high=108.0, day_low=105.0))  # 7% gap
        self.assertGreater(large_gap["confidence"], small_gap["confidence"])

    def test_sell_gap_down_near_low(self):
        """Gapped down >2%, price near day low = SELL (drift intact)."""
        result = news_catalyst_drift("AAPL", self._market(
            price=170.0, prev_close=175.0, day_high=174.0, day_low=169.5))
        self.assertEqual(result["signal"], "SELL")
        self.assertIn("gapped down", result["reason"])

    def test_hold_gap_up_but_faded(self):
        """Gapped up but price fell away from high = HOLD (reverting)."""
        result = news_catalyst_drift("AAPL", self._market(
            price=178.0, prev_close=175.0, day_high=182.0, day_low=177.0))
        # gap +1.7%, day_high 182 → 2.2% from high → faded
        self.assertEqual(result["signal"], "HOLD")

    def test_hold_gap_down_but_bounced(self):
        """Gapped down but price bounced off low = HOLD (reverting)."""
        result = news_catalyst_drift("AAPL", self._market(
            price=172.0, prev_close=175.0, day_high=174.0, day_low=169.0))
        # gap -1.7%, day_low 169 → 1.8% from low → bounced
        self.assertEqual(result["signal"], "HOLD")

    def test_hold_no_significant_gap(self):
        """Gap < 2% = HOLD (no catalyst)."""
        result = news_catalyst_drift("AAPL", self._market(
            price=176.0, prev_close=175.0, day_high=177.0, day_low=174.5))
        self.assertEqual(result["signal"], "HOLD")
        self.assertIn("No significant gap", result["reason"])

    def test_hold_missing_data(self):
        """Missing price data = HOLD."""
        result = news_catalyst_drift("AAPL", {"price": 175.0})
        self.assertEqual(result["signal"], "HOLD")

    def test_confidence_capped(self):
        """Confidence should not exceed 0.85."""
        result = news_catalyst_drift("AAPL", self._market(
            price=110.0, prev_close=100.0, day_high=110.0, day_low=108.0))
        self.assertLessEqual(result["confidence"], 0.85)

    def test_proximity_boosts_confidence(self):
        """Closer to high = higher confidence."""
        at_high = news_catalyst_drift("AAPL", self._market(
            price=104.0, prev_close=100.0, day_high=104.0, day_low=102.0))
        near_high = news_catalyst_drift("AAPL", self._market(
            price=103.0, prev_close=100.0, day_high=104.0, day_low=102.0))
        self.assertGreater(at_high["confidence"], near_high["confidence"])


class TestMomentumSignal(unittest.TestCase):

    def test_buy_uptrend(self):
        result = momentum_signal("AAPL", {"price": 180, "ma_20": 175, "ma_50": 170})
        self.assertEqual(result["signal"], "BUY")

    def test_sell_downtrend(self):
        result = momentum_signal("AAPL", {"price": 160, "ma_20": 165, "ma_50": 170})
        self.assertEqual(result["signal"], "SELL")

    def test_hold_mixed(self):
        result = momentum_signal("AAPL", {"price": 172, "ma_20": 170, "ma_50": 175})
        self.assertEqual(result["signal"], "HOLD")

    def test_hold_missing_data(self):
        result = momentum_signal("AAPL", {"price": 175})
        self.assertEqual(result["signal"], "HOLD")


class TestMeanReversionSignal(unittest.TestCase):

    def test_buy_oversold_bounce(self):
        result = mean_reversion_signal("AAPL", {"rsi": 25, "price_change_pct": 1.0})
        self.assertEqual(result["signal"], "BUY")

    def test_sell_overbought_pullback(self):
        result = mean_reversion_signal("AAPL", {"rsi": 78, "price_change_pct": -1.0})
        self.assertEqual(result["signal"], "SELL")

    def test_hold_neutral_rsi(self):
        result = mean_reversion_signal("AAPL", {"rsi": 50, "price_change_pct": 1.0})
        self.assertEqual(result["signal"], "HOLD")

    def test_hold_oversold_but_still_falling(self):
        """RSI < 30 but price still falling = no confirmation."""
        result = mean_reversion_signal("AAPL", {"rsi": 25, "price_change_pct": -1.0})
        self.assertEqual(result["signal"], "HOLD")


class TestVolumeConfirmation(unittest.TestCase):
    """Test the volume confirmation modifier."""

    def test_strong_volume_high_multiplier(self):
        result = volume_confirmation({"volume": 100_000_000, "avg_volume_20": 45_000_000})
        self.assertEqual(result["multiplier"], 1.4)

    def test_moderate_volume_moderate_multiplier(self):
        result = volume_confirmation({"volume": 70_000_000, "avg_volume_20": 45_000_000})
        self.assertEqual(result["multiplier"], 1.2)

    def test_low_volume_dampens(self):
        result = volume_confirmation({"volume": 25_000_000, "avg_volume_20": 45_000_000})
        self.assertEqual(result["multiplier"], 0.6)

    def test_normal_volume_no_change(self):
        result = volume_confirmation({"volume": 45_000_000, "avg_volume_20": 45_000_000})
        self.assertEqual(result["multiplier"], 1.0)

    def test_missing_data_returns_none(self):
        self.assertIsNone(volume_confirmation({"volume": 45_000_000}))
        self.assertIsNone(volume_confirmation({}))


class TestVWAPPosition(unittest.TestCase):
    """Test the VWAP position modifier."""

    def test_price_above_vwap_bullish(self):
        result = vwap_position({"price": 180, "vwap": 175})
        self.assertGreater(result["directional_modifier"], 0)
        self.assertIn("above VWAP", result["reason"])

    def test_price_below_vwap_bearish(self):
        result = vwap_position({"price": 170, "vwap": 175})
        self.assertLess(result["directional_modifier"], 0)
        self.assertIn("below VWAP", result["reason"])

    def test_price_near_vwap_neutral(self):
        result = vwap_position({"price": 175.5, "vwap": 175})
        self.assertEqual(result["directional_modifier"], 0.0)

    def test_modifier_capped_at_0_2(self):
        """Even extreme deviation should cap at ±0.2."""
        result = vwap_position({"price": 200, "vwap": 175})
        self.assertLessEqual(result["directional_modifier"], 0.2)

    def test_missing_vwap_returns_none(self):
        self.assertIsNone(vwap_position({"price": 175}))


class TestRelativeStrength(unittest.TestCase):
    """Test the relative strength vs SPY modifier."""

    def test_outperforming_spy_bullish(self):
        result = relative_strength(
            {"price_change_pct": 3.0},
            {"spy_change_pct": 0.5},
        )
        self.assertGreater(result["directional_modifier"], 0)
        self.assertIn("Outperforming", result["reason"])

    def test_underperforming_spy_bearish(self):
        result = relative_strength(
            {"price_change_pct": -1.5},
            {"spy_change_pct": 1.0},
        )
        self.assertLess(result["directional_modifier"], 0)
        self.assertIn("Underperforming", result["reason"])

    def test_in_line_with_market_neutral(self):
        result = relative_strength(
            {"price_change_pct": 1.0},
            {"spy_change_pct": 0.5},
        )
        self.assertEqual(result["directional_modifier"], 0.0)

    def test_no_macro_data_returns_none(self):
        self.assertIsNone(relative_strength({"price_change_pct": 1.0}, None))

    def test_missing_spy_change_returns_none(self):
        self.assertIsNone(relative_strength({"price_change_pct": 1.0}, {"vix": 18}))

    def test_modifier_capped(self):
        result = relative_strength(
            {"price_change_pct": 10.0},
            {"spy_change_pct": 0.0},
        )
        self.assertLessEqual(result["directional_modifier"], 0.2)


@patch("engine.strategies.get_previous_sentiment", return_value=None)
class TestRunAllStrategies(unittest.TestCase):
    """Test run_all_strategies returns raw signals and modifiers dict."""

    def _market(self, **overrides):
        base = {"price": 175, "ma_20": 170, "ma_50": 165, "rsi": 50,
                "price_change_pct": 0.0, "volume": 50_000_000, "avg_volume_20": 45_000_000,
                "vwap": 174.0}
        base.update(overrides)
        return base

    def _sentiment(self, **overrides):
        base = {"sentiment_score": 0.7, "article_count": 2,
                "source_breakdown": {"marketaux": 1, "newsapi": 1}, "confidence": 0.5,
                "individual_scores": [0.7, 0.7]}
        base.update(overrides)
        return base

    def test_returns_dict_with_signals_and_modifiers(self, _):
        """run_all_strategies should return a dict with signals and modifiers keys."""
        result = run_all_strategies("AAPL", self._market(), self._sentiment())
        self.assertIsInstance(result, dict)
        self.assertIn("signals", result)
        self.assertIn("modifiers", result)
        self.assertIsInstance(result["signals"], list)
        self.assertIsInstance(result["modifiers"], list)

    def test_signals_are_raw_no_regime(self, _):
        """Signals should not contain regime tags or modifiers_applied."""
        result = run_all_strategies("AAPL", self._market(), self._sentiment())
        for s in result["signals"]:
            self.assertNotIn("regime", s)
            self.assertNotIn("modifiers_applied", s)
            self.assertIn("signal", s)
            self.assertIn("confidence", s)
            self.assertIn("strategy", s)
            self.assertIn("reason", s)

    def test_signals_filter_hold(self, _):
        """Signals list should not contain HOLD entries."""
        result = run_all_strategies("AAPL", self._market(), self._sentiment())
        for s in result["signals"]:
            self.assertNotEqual(s["signal"], "HOLD")

    def test_includes_sentiment_strategies(self, _):
        """Should include sentiment strategies when sentiment data present."""
        sentiment = self._sentiment(sentiment_score=0.8, article_count=3,
                                    source_breakdown={"marketaux": 1, "newsapi": 2},
                                    individual_scores=[0.7, 0.85, 0.75])
        result = run_all_strategies("AAPL", self._market(), sentiment)
        strategy_names = [s["strategy"] for s in result["signals"]]
        self.assertIn("sentiment_divergence", strategy_names)
        self.assertIn("multi_source_consensus", strategy_names)

    def test_works_without_sentiment(self, _):
        """Should work with no sentiment data (technical strategies only)."""
        market = self._market(price=180, ma_20=175, ma_50=170, rsi=25, price_change_pct=1.5)
        result = run_all_strategies("AAPL", market)
        self.assertIsInstance(result["signals"], list)
        strategy_names = [s["strategy"] for s in result["signals"]]
        self.assertNotIn("sentiment_divergence", strategy_names)

    def test_modifiers_have_modifier_name(self, _):
        """Each modifier should carry a modifier_name field."""
        result = run_all_strategies("AAPL", self._market(), self._sentiment())
        for m in result["modifiers"]:
            self.assertIn("modifier_name", m)
            self.assertIn(m["modifier_name"],
                          {"volume_confirmation", "vwap_position", "relative_strength"})

    def test_modifiers_include_volume(self, _):
        """Volume confirmation should appear in modifiers."""
        market = self._market(volume=100_000_000, avg_volume_20=45_000_000)
        result = run_all_strategies("AAPL", market, self._sentiment())
        vol_mods = [m for m in result["modifiers"] if m["modifier_name"] == "volume_confirmation"]
        self.assertEqual(len(vol_mods), 1)
        self.assertIn("multiplier", vol_mods[0])

    def test_modifiers_include_relative_strength_with_macro(self, _):
        """Relative strength should appear when macro data is provided."""
        macro = {"spy_change_pct": -2.0}
        result = run_all_strategies("AAPL", self._market(), self._sentiment(), macro)
        rs_mods = [m for m in result["modifiers"] if m["modifier_name"] == "relative_strength"]
        self.assertEqual(len(rs_mods), 1)
        self.assertIn("directional_modifier", rs_mods[0])

    def test_no_regime_parameter(self, _):
        """run_all_strategies should not accept regime_data parameter."""
        import inspect
        params = inspect.signature(run_all_strategies).parameters
        self.assertNotIn("regime_data", params)


class TestEnrichmentBoost(unittest.TestCase):
    """Test _apply_enrichment_boost and its integration with strategies."""

    def test_breaking_high_materiality_boost(self):
        """Breaking + high materiality should boost ~1.5x."""
        conf, notes = _apply_enrichment_boost(0.5, {
            "urgency": "breaking",
            "materiality": "high",
            "time_horizon": "medium_term",
        })
        # 0.5 * 1.25 * 1.2 * 1.0 = 0.75
        self.assertAlmostEqual(conf, 0.75, places=2)
        self.assertEqual(len(notes), 2)  # urgency + materiality, not time_horizon

    def test_standard_unknown_no_boost(self):
        """Default enrichment values should not change confidence."""
        conf, notes = _apply_enrichment_boost(0.6, {
            "urgency": "standard",
            "materiality": "unknown",
            "time_horizon": "medium_term",
        })
        self.assertAlmostEqual(conf, 0.6, places=2)
        self.assertEqual(len(notes), 0)

    def test_developing_medium_boost(self):
        """Developing + medium materiality → moderate boost."""
        conf, notes = _apply_enrichment_boost(0.5, {
            "urgency": "developing",
            "materiality": "medium",
            "time_horizon": "medium_term",
        })
        # 0.5 * 1.1 * 1.1 * 1.0 = 0.605
        self.assertAlmostEqual(conf, 0.605, places=2)

    def test_long_term_dampens(self):
        """Long-term time horizon should slightly reduce confidence."""
        conf, notes = _apply_enrichment_boost(0.5, {
            "urgency": "standard",
            "materiality": "unknown",
            "time_horizon": "long_term",
        })
        # 0.5 * 1.0 * 1.0 * 0.9 = 0.45
        self.assertAlmostEqual(conf, 0.45, places=2)
        self.assertIn("time_horizon=long_term", notes[0])

    def test_intraday_boosts(self):
        """Intraday time horizon should boost confidence."""
        conf, notes = _apply_enrichment_boost(0.5, {
            "urgency": "standard",
            "materiality": "unknown",
            "time_horizon": "intraday",
        })
        # 0.5 * 1.0 * 1.0 * 1.15 = 0.575
        self.assertAlmostEqual(conf, 0.575, places=2)

    def test_max_boost_all_factors(self):
        """Breaking + high + intraday = maximum boost."""
        conf, notes = _apply_enrichment_boost(0.5, {
            "urgency": "breaking",
            "materiality": "high",
            "time_horizon": "intraday",
        })
        # 0.5 * 1.25 * 1.2 * 1.15 = 0.8625
        self.assertAlmostEqual(conf, 0.8625, places=3)
        self.assertEqual(len(notes), 3)

    def test_missing_fields_use_defaults(self):
        """Missing enrichment fields should default to 1.0 multiplier."""
        conf, notes = _apply_enrichment_boost(0.5, {})
        self.assertAlmostEqual(conf, 0.5, places=2)
        self.assertEqual(len(notes), 0)


class TestEnrichmentInStrategies(unittest.TestCase):
    """Test that enrichment boosts flow through actual strategies."""

    def _market(self, **overrides):
        base = {"price": 175.0, "price_change_pct": 0.0, "ma_20": 170.0, "ma_50": 165.0,
                "rsi": 50.0, "volume": 50_000_000, "avg_volume_20": 45_000_000}
        base.update(overrides)
        return base

    def _sentiment(self, **overrides):
        base = {"sentiment_score": 0.7, "article_count": 2,
                "source_breakdown": {"newsapi": 1, "marketaux": 1},
                "individual_scores": [0.7, 0.7]}
        base.update(overrides)
        return base

    def test_divergence_breaking_high_boosts_confidence(self):
        """Breaking + high materiality should boost divergence confidence."""
        plain = sentiment_price_divergence("AAPL", self._market(), self._sentiment())
        boosted = sentiment_price_divergence("AAPL", self._market(), self._sentiment(
            urgency="breaking", materiality="high", time_horizon="intraday",
        ))
        self.assertEqual(plain["signal"], "BUY")
        self.assertEqual(boosted["signal"], "BUY")
        self.assertGreater(boosted["confidence"], plain["confidence"])

    def test_divergence_standard_unknown_unchanged(self):
        """Standard/unknown enrichment should not change divergence confidence."""
        plain = sentiment_price_divergence("AAPL", self._market(), self._sentiment())
        with_defaults = sentiment_price_divergence("AAPL", self._market(), self._sentiment(
            urgency="standard", materiality="unknown", time_horizon="medium_term",
        ))
        self.assertAlmostEqual(plain["confidence"], with_defaults["confidence"], places=3)

    def test_consensus_breaking_boosts(self):
        """Multi-source consensus should be boosted by breaking urgency."""
        base = self._sentiment(
            article_count=4,
            source_breakdown={"newsapi": 2, "marketaux": 2},
            individual_scores=[0.5, 0.6, 0.5, 0.7],
        )
        plain = multi_source_consensus("AAPL", base)
        boosted = multi_source_consensus("AAPL", {**base, "urgency": "breaking", "materiality": "high"})
        self.assertEqual(plain["signal"], "BUY")
        self.assertEqual(boosted["signal"], "BUY")
        self.assertGreater(boosted["confidence"], plain["confidence"])

    @patch("engine.strategies.get_previous_sentiment")
    def test_momentum_breaking_boosts(self, mock_prev):
        """Sentiment momentum should be boosted by enrichment."""
        mock_prev.return_value = {"sentiment_score": -0.3, "article_count": 2, "recorded_at": "..."}
        plain = sentiment_momentum("AAPL", self._sentiment(sentiment_score=0.5))
        boosted = sentiment_momentum("AAPL", self._sentiment(
            sentiment_score=0.5, urgency="breaking", materiality="high",
        ))
        self.assertEqual(plain["signal"], "BUY")
        self.assertEqual(boosted["signal"], "BUY")
        self.assertGreater(boosted["confidence"], plain["confidence"])

    def test_drift_enrichment_boosts(self):
        """News catalyst drift should be boosted when sentiment enrichment is provided."""
        market = self._market(price=105.0, prev_close=100.0, day_high=105.5, day_low=100.0)
        plain = news_catalyst_drift("AAPL", market)
        boosted = news_catalyst_drift("AAPL", market, self._sentiment(
            urgency="breaking", materiality="high", time_horizon="intraday",
        ))
        self.assertEqual(plain["signal"], "BUY")
        self.assertEqual(boosted["signal"], "BUY")
        self.assertGreater(boosted["confidence"], plain["confidence"])

    def test_drift_no_sentiment_unchanged(self):
        """news_catalyst_drift without sentiment_data should behave as before."""
        market = self._market(price=105.0, prev_close=100.0, day_high=105.5, day_low=100.0)
        without = news_catalyst_drift("AAPL", market)
        with_none = news_catalyst_drift("AAPL", market, None)
        self.assertEqual(without["confidence"], with_none["confidence"])

    def test_enrichment_reason_tag(self):
        """Enrichment factors should appear in the reason string."""
        result = sentiment_price_divergence("AAPL", self._market(), self._sentiment(
            urgency="breaking", materiality="high", time_horizon="intraday",
        ))
        self.assertIn("urgency=breaking", result["reason"])
        self.assertIn("materiality=high", result["reason"])
        self.assertIn("time_horizon=intraday", result["reason"])

    def test_confidence_still_clamped_after_boost(self):
        """Even with max enrichment boost, confidence stays <= 0.95."""
        # Very strong sentiment + enrichment
        result = sentiment_price_divergence("AAPL", self._market(), self._sentiment(
            sentiment_score=0.95, article_count=5,
            source_breakdown={"newsapi": 3, "marketaux": 2},
            individual_scores=[0.95, 0.95, 0.95, 0.95, 0.95],
            urgency="breaking", materiality="high", time_horizon="intraday",
        ))
        self.assertLessEqual(result["confidence"], 0.95)

    @patch("engine.strategies.get_previous_sentiment")
    def test_run_all_with_enrichment(self, mock_prev):
        """run_all_strategies should produce higher confidence with enrichment."""
        mock_prev.return_value = None
        market = self._market()
        plain_sent = self._sentiment()
        enriched_sent = self._sentiment(urgency="breaking", materiality="high", time_horizon="intraday")

        plain_result = run_all_strategies("AAPL", market, plain_sent)
        enriched_result = run_all_strategies("AAPL", market, enriched_sent)

        # Both should return signals/modifiers dict
        self.assertIn("signals", plain_result)
        self.assertIn("signals", enriched_result)
        self.assertTrue(len(plain_result["signals"]) > 0)
        self.assertTrue(len(enriched_result["signals"]) > 0)

        # Find matching strategy to compare
        plain_div = next((s for s in plain_result["signals"] if s["strategy"] == "sentiment_divergence"), None)
        enriched_div = next((s for s in enriched_result["signals"] if s["strategy"] == "sentiment_divergence"), None)

        if plain_div and enriched_div:
            self.assertGreater(enriched_div["confidence"], plain_div["confidence"])


if __name__ == "__main__":
    unittest.main()
