"""Macro regime classification for autonomous paper trading system.

Classifies market environment as risk-on, risk-off, or neutral based on
VIX, SPY trend, yield curve, and macro sentiment. The regime drives
adaptive strategy weighting in engine/strategies.py rather than acting
as a simple binary toggle.

Thresholds:
  Risk-On:  VIX < 20, SPY > 200MA, yield spread > 0.5%, positive macro
  Risk-Off: VIX > 25, SPY < 200MA, yield spread < -0.5%, negative macro
  Neutral:  Everything in between
"""

import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Regime classification thresholds
VIX_RISK_ON_THRESHOLD = 20.0
VIX_RISK_OFF_THRESHOLD = 25.0
YIELD_SPREAD_RISK_ON = 0.5
YIELD_SPREAD_RISK_OFF = -0.5
MACRO_SENTIMENT_THRESHOLD = 0.3


def get_current_regime(macro_data: Dict[str, Any], macro_news_score: float = 0.0) -> Dict[str, Any]:
    """Classify current market regime.

    Args:
        macro_data: From fetchers/market.py macro indicators:
                   vix, spy_price, spy_ma_200, spy_vs_200ma, yield_spread
        macro_news_score: Aggregated macro news sentiment (-1.0 to 1.0)

    Returns:
        {"regime", "vix", "spy_vs_200ma", "yield_spread", "macro_sentiment", "confidence"}
    """
    vix = macro_data.get("vix")
    spy_vs_200ma = macro_data.get("spy_vs_200ma")
    yield_spread = macro_data.get("yield_spread")

    # Handle missing data gracefully
    if vix is None and spy_vs_200ma is None and yield_spread is None:
        return {
            "regime": "neutral",
            "vix": None,
            "spy_vs_200ma": None,
            "yield_spread": None,
            "macro_sentiment": "neutral",
            "confidence": 0.0,
        }

    regime, confidence = _classify_regime(
        vix or 22.0,  # default to neutral-ish if missing
        spy_vs_200ma or 0.0,
        yield_spread or 0.0,
        macro_news_score,
    )

    if macro_news_score > MACRO_SENTIMENT_THRESHOLD:
        macro_sentiment = "positive"
    elif macro_news_score < -MACRO_SENTIMENT_THRESHOLD:
        macro_sentiment = "negative"
    else:
        macro_sentiment = "neutral"

    return {
        "regime": regime,
        "vix": vix,
        "spy_vs_200ma": spy_vs_200ma,
        "yield_spread": yield_spread,
        "macro_sentiment": macro_sentiment,
        "confidence": round(confidence, 3),
    }


def _classify_regime(vix: float, spy_vs_200ma: float, yield_spread: float, news_score: float) -> Tuple[str, float]:
    """Score each indicator and aggregate into a regime classification.

    Each indicator votes risk-on (+1), risk-off (-1), or neutral (0).
    The weighted sum determines the regime. Strong macro sentiment can
    override a neutral technical reading.
    """
    score = 0.0
    indicators_counted = 0

    # VIX (35% weight)
    if vix < VIX_RISK_ON_THRESHOLD:
        score += 0.35
    elif vix > VIX_RISK_OFF_THRESHOLD:
        score -= 0.35
    indicators_counted += 1

    # SPY vs 200MA (30% weight)
    if spy_vs_200ma > 0.02:  # 2% above
        score += 0.30
    elif spy_vs_200ma < -0.02:  # 2% below
        score -= 0.30
    indicators_counted += 1

    # Yield spread (25% weight)
    if yield_spread > YIELD_SPREAD_RISK_ON:
        score += 0.25
    elif yield_spread < YIELD_SPREAD_RISK_OFF:
        score -= 0.25
    indicators_counted += 1

    # Macro sentiment (10% base weight + override)
    if abs(news_score) > MACRO_SENTIMENT_THRESHOLD:
        sentiment_contrib = 0.10 * (1 if news_score > 0 else -1)
        score += sentiment_contrib

        # Strong sentiment can push neutral toward a regime
        if abs(news_score) > 0.6:
            score += 0.15 * (1 if news_score > 0 else -1)
    indicators_counted += 1

    # Classify
    if score > 0.3:
        regime = "risk_on"
    elif score < -0.3:
        regime = "risk_off"
    else:
        regime = "neutral"

    # Confidence: how strongly indicators agree
    confidence = min(1.0, abs(score) / 0.5)
    confidence = max(0.1, confidence)

    return regime, confidence
