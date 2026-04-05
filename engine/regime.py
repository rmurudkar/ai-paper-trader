"""Macro regime classification for autonomous paper trading system.

This module classifies the current market environment as risk-on, risk-off, or neutral
based on technical indicators and macro sentiment. The regime filter modifies signal
confidence and trade execution to align with prevailing market conditions.

CRITICAL ARCHITECTURE NOTE:
===========================
The regime filter is applied AFTER strategy signals are generated but BEFORE
final signal combination. It modifies confidence scores and trade direction
bias based on the macro environment assessment.

Regime Classification:
=====================

**RISK-ON (Bullish Environment)**
- VIX < 20 (low volatility/complacency)
- SPY > SPY 200MA (equity uptrend intact)
- 10yr-2yr yield spread > 0.5% (normal/positive yield curve)
- Positive macro news sentiment
→ Green light for long trades, reduce short confidence

**RISK-OFF (Bearish Environment)**
- VIX > 25 (high volatility/fear)
- SPY < SPY 200MA (equity downtrend)
- 10yr-2yr yield spread < -0.5% (inverted yield curve)
- Negative macro news sentiment
→ Green light for short trades, reduce long confidence

**NEUTRAL (Mixed Environment)**
- Indicators between risk-on/risk-off thresholds
- Mixed or unclear macro sentiment
- Transitional market conditions
→ No bias modification, trade normally

Signal Modification:
===================
The regime filter adjusts signal confidence:
- Risk-on: Keep BUY confidence as-is, reduce SELL by 20%
- Risk-off: Keep SELL confidence as-is, reduce BUY by 30%
- Neutral: No modifications applied

This creates systematic bias toward the prevailing regime while still
allowing counter-trend trades with reduced confidence.

Macro Sentiment Integration:
===========================
Combines technical regime indicators with sentiment from macro/economic news:
- Fed policy announcements and minutes
- Economic data releases (CPI, employment, GDP)
- Geopolitical events and trade policy
- Central bank communications

Strong macro sentiment can override neutral technical regime classification,
pushing the system toward risk-on or risk-off bias.

Data Dependencies:
=================
Technical indicators from fetchers/market.py:
- VIX (^VIX): Market volatility index
- SPY price vs 200MA: Equity market trend
- 10yr-2yr yield spread: Yield curve shape

Sentiment data from engine/sentiment.py:
- Aggregated sentiment from macro/economic news
- Weight-averaged across NewsAPI articles
- Classified as positive/negative/neutral

Performance Impact:
==================
Regime classification adds systematic market awareness to strategy signals
without overriding individual trade logic. It provides:
- Better risk-adjusted returns during regime shifts
- Reduced drawdowns in adverse market conditions
- Systematic adaptation to changing macro environment
"""

import logging
from typing import Dict, Any, Tuple
import math

# Configure logging
logger = logging.getLogger(__name__)

# Regime classification thresholds
VIX_RISK_ON_THRESHOLD = 20.0      # Below this = low volatility/risk-on
VIX_RISK_OFF_THRESHOLD = 25.0     # Above this = high volatility/risk-off
YIELD_SPREAD_RISK_ON = 0.5        # Above this = normal/positive curve
YIELD_SPREAD_RISK_OFF = -0.5      # Below this = inverted curve
MACRO_SENTIMENT_THRESHOLD = 0.3   # Abs value for sentiment override


def get_current_regime(macro_data: Dict[str, Any], macro_news_score: float) -> Dict[str, Any]:
    """Classify current market regime based on technical indicators and macro sentiment.

    This is the main entry point for regime classification. It combines multiple
    technical indicators with macro news sentiment to determine the current
    market environment and appropriate trading bias.

    Regime Determination Process:
    ----------------------------
    1. **Technical Classification**: Analyze VIX, SPY trend, yield curve
    2. **Sentiment Override**: Check if strong macro news overrides technicals
    3. **Confidence Scoring**: Calculate conviction level in regime assessment
    4. **Signal Modification**: Determine how to adjust strategy confidence

    Technical Indicators:
    --------------------
    - **VIX Level**: Market volatility and fear gauge
    - **SPY vs 200MA**: Equity market trend direction
    - **Yield Spread**: 10yr-2yr spread for economic outlook
    - **Macro Sentiment**: Aggregated news sentiment score

    Regime Override Logic:
    ---------------------
    Strong macro sentiment (|score| > 0.3) can override neutral technical
    classification to push toward risk-on or risk-off bias. This captures
    major policy changes or economic events that markets haven't fully priced.

    Confidence Assessment:
    ---------------------
    - **High Confidence**: All indicators aligned in same direction
    - **Medium Confidence**: Most indicators aligned, some neutral
    - **Low Confidence**: Mixed signals or near threshold values

    Args:
        macro_data: Dict containing required technical indicators:
                   - vix: Current VIX level (volatility index)
                   - spy_price: Current SPY price
                   - spy_200ma: SPY 200-day moving average
                   - yield_spread: 10yr-2yr Treasury spread (percentage)
        macro_news_score: Aggregated sentiment from macro/economic news
                         Range: -1.0 (very negative) to +1.0 (very positive)
                         Calculated by engine/sentiment.py from NewsAPI articles

    Returns:
        Dict containing regime classification and supporting data:
        {
            "regime": str,              # "risk_on", "risk_off", "neutral"
            "vix": float,              # Current VIX level
            "spy_vs_200ma": float,     # SPY price relative to 200MA (percentage)
            "yield_spread": float,     # 10yr-2yr yield spread
            "macro_sentiment": str,    # "positive", "negative", "neutral"
            "confidence": float        # Conviction level (0.0-1.0)
        }

    Example Output:
    --------------
    Risk-On Environment:
    >>> get_current_regime({
    ...     "vix": 16.5,
    ...     "spy_price": 450.0,
    ...     "spy_200ma": 440.0,
    ...     "yield_spread": 1.2
    ... }, 0.4)
    {
        "regime": "risk_on",
        "vix": 16.5,
        "spy_vs_200ma": 0.023,
        "yield_spread": 1.2,
        "macro_sentiment": "positive",
        "confidence": 0.85
    }

    Risk-Off Environment:
    >>> get_current_regime({
    ...     "vix": 32.1,
    ...     "spy_price": 420.0,
    ...     "spy_200ma": 445.0,
    ...     "yield_spread": -0.8
    ... }, -0.6)
    {
        "regime": "risk_off",
        "vix": 32.1,
        "spy_vs_200ma": -0.056,
        "yield_spread": -0.8,
        "macro_sentiment": "negative",
        "confidence": 0.92
    }
    """
    raise NotImplementedError("Not yet implemented")


def _classify_regime(vix: float, spy_vs_200ma: float, yield_spread: float, news_score: float) -> Tuple[str, float]:
    """Internal function to classify regime based on individual indicators.

    This function implements the core regime classification logic using
    threshold-based analysis of technical indicators and sentiment.

    Classification Algorithm:
    -------------------------
    1. **Score each indicator** on risk-on vs risk-off scale
    2. **Weight indicators** by reliability and current market relevance
    3. **Aggregate scores** to determine overall regime bias
    4. **Apply sentiment override** for strong macro news
    5. **Calculate confidence** based on indicator alignment

    Indicator Scoring:
    -----------------
    - VIX: Linear scale between thresholds with saturation
    - SPY vs 200MA: Percentage above/below with sensitivity scaling
    - Yield Spread: Linear scale between inversion and steep curve
    - Macro Sentiment: Direct incorporation with threshold override

    Weighting Strategy:
    ------------------
    - VIX: 35% weight (most reliable volatility gauge)
    - SPY Trend: 30% weight (equity market direction)
    - Yield Spread: 25% weight (economic cycle indicator)
    - Macro Sentiment: 10% base + override capability

    Confidence Calculation:
    ----------------------
    Based on indicator alignment and distance from thresholds:
    - All indicators aligned: 0.8-1.0 confidence
    - Most indicators aligned: 0.5-0.8 confidence
    - Mixed signals: 0.2-0.5 confidence

    Args:
        vix: Current VIX volatility level
        spy_vs_200ma: SPY price relative to 200MA (as decimal: 0.02 = 2% above)
        yield_spread: 10yr-2yr Treasury yield spread (percentage)
        news_score: Macro news sentiment (-1.0 to +1.0)

    Returns:
        Tuple of (regime_classification, confidence_score):
        - regime_classification: "risk_on", "risk_off", or "neutral"
        - confidence_score: Float 0.0-1.0 indicating conviction level

    Implementation Notes:
    --------------------
    - Uses linear interpolation between thresholds for smooth transitions
    - Handles edge cases (missing data, extreme values) gracefully
    - Optimized for real-time execution without external dependencies
    - Deterministic output for consistent backtesting and debugging

    Example Classifications:
    -----------------------
    >>> _classify_regime(15.0, 0.05, 1.5, 0.2)
    ("risk_on", 0.88)

    >>> _classify_regime(30.0, -0.08, -1.2, -0.4)
    ("risk_off", 0.91)

    >>> _classify_regime(22.5, 0.01, 0.1, 0.05)
    ("neutral", 0.45)
    """
    raise NotImplementedError("Not yet implemented") 