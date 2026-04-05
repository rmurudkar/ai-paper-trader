"""Weighted signal combiner for autonomous paper trading system.

This module takes outputs from sentiment analysis, technical strategies, and regime
classification to produce final trading signals per ticker. It implements learned
weighting based on historical performance and applies regime-based modifications.

CRITICAL ARCHITECTURE NOTE:
===========================
The combiner is the FINAL DECISION POINT before risk management. It combines:
- Technical strategy signals (engine/strategies.py)
- Sentiment analysis scores (engine/sentiment.py)
- Regime classification (engine/regime.py)
- Learned weights (feedback/weights.py)

Signal Combination Process:
==========================

**Per-Ticker Calculation:**
1. **Collect Strategy Signals**: Gather all technical strategy outputs
2. **Apply Learned Weights**: Weight each strategy by historical performance
3. **Include Sentiment**: Add sentiment as separate weighted signal
4. **Apply Regime Filter**: Modify confidence based on market regime
5. **Aggregate Signals**: Calculate weighted average of all signals
6. **Generate Decision**: Round to BUY/SELL/HOLD based on thresholds

**Weight Learning Integration:**
- Reads current weights from Turso `weights` table
- Each strategy and news source has learned weight (0.1-1.0)
- Weights updated by feedback loop based on trade outcomes
- Higher weights = better historical performance

**Regime Modifications:**
- Risk-On: Keep BUY confidence as-is, reduce SELL by 20%
- Risk-Off: Keep SELL confidence as-is, reduce BUY by 30%
- Neutral: No confidence modifications

Signal Thresholds:
=================
Final signal determined by weighted confidence score:
- **BUY**: Combined confidence > 0.55
- **SELL**: Combined confidence < 0.45
- **HOLD**: Between 0.45 and 0.55 (neutral zone)

These thresholds provide buffer against noise and ensure high-conviction signals.

Output Format:
=============
Each ticker produces a final trading signal with full attribution:
```python
{
    "ticker": "AAPL",
    "signal": "BUY",
    "confidence": 0.72,
    "components": {
        "momentum": 0.80,           # Individual strategy confidence
        "mean_reversion": 0.45,
        "sentiment": 0.65,
        "regime_adjusted": 0.72     # After regime modifications
    },
    "regime": "risk_on",
    "rationale": "Momentum + positive sentiment, risk-on regime"
}
```

Weight Management:
=================
- Initial weights: 0.5 (neutral performance assumption)
- Weight bounds: 0.1 to 1.0 (never fully silence strategies)
- Update frequency: After each trade outcome measurement
- Persistence: Stored in Turso database for system memory

The weight system enables continuous adaptation to changing market conditions
and strategy effectiveness without manual intervention.
"""

import logging
from typing import Dict, List, Any, Optional
import json

# Configure logging
logger = logging.getLogger(__name__)

# Signal decision thresholds
BUY_THRESHOLD = 0.55      # Combined confidence above this = BUY
SELL_THRESHOLD = 0.45     # Combined confidence below this = SELL
# Between thresholds = HOLD

# Regime modification factors
REGIME_BUY_REDUCTION_RISK_OFF = 0.30    # Reduce BUY confidence by 30% in risk-off
REGIME_SELL_REDUCTION_RISK_ON = 0.20    # Reduce SELL confidence by 20% in risk-on

# Default weights for new strategies/sources
DEFAULT_STRATEGY_WEIGHT = 0.5
DEFAULT_SOURCE_WEIGHT = 0.5


def combine_signals(
    sentiment_signals: List[Dict[str, Any]],
    strategy_signals: List[Dict[str, Any]],
    regime: Dict[str, Any],
    weights: Dict[str, float]
) -> Dict[str, Any]:
    """Combine all signal types into final trading decision using learned weights.

    This is the core signal combination function that produces final trading
    decisions by weighing and combining all available signal sources.

    Signal Integration Process:
    --------------------------
    1. **Weight Strategy Signals**: Apply learned weights to each strategy
    2. **Include Sentiment**: Add sentiment as separate weighted signal component
    3. **Apply Regime Filter**: Modify confidence based on market environment
    4. **Calculate Average**: Compute weighted average of all signal components
    5. **Make Decision**: Apply thresholds to determine BUY/SELL/HOLD

    Weighting System:
    ----------------
    Each signal source has a learned weight (0.1-1.0) based on historical performance:
    - momentum, mean_reversion, ma_crossover, volume_surge: Strategy weights
    - marketaux, newsapi: News source weights
    - sentiment: Combined sentiment weight from news sources

    Regime Integration:
    ------------------
    Market regime modifies signal confidence before final averaging:
    - **Risk-On**: Reduces SELL confidence, keeps BUY confidence
    - **Risk-Off**: Reduces BUY confidence, keeps SELL confidence
    - **Neutral**: No modifications applied

    Decision Logic:
    --------------
    Final signal based on weighted confidence:
    - > 0.55: BUY signal (high conviction bullish)
    - < 0.45: SELL signal (high conviction bearish)
    - 0.45-0.55: HOLD signal (neutral/uncertain)

    Args:
        sentiment_signals: List of sentiment analysis results:
                          [{"ticker": "AAPL", "sentiment_score": 0.6, "source": "newsapi"}]
        strategy_signals: List of technical strategy outputs:
                         [{"signal": "BUY", "confidence": 0.8, "strategy": "momentum"}]
        regime: Market regime classification from engine/regime.py:
               {"regime": "risk_on", "confidence": 0.85}
        weights: Dict of learned weights by strategy/source name:
                {"momentum": 0.7, "newsapi": 0.6, "sentiment": 0.8}

    Returns:
        Combined signal dictionary:
        {
            "ticker": str,              # Stock ticker symbol
            "signal": str,              # "BUY", "SELL", or "HOLD"
            "confidence": float,        # Final confidence score (0.0-1.0)
            "components": Dict,         # Individual component contributions
            "regime": str,              # Market regime applied
            "rationale": str            # Human-readable explanation
        }

    Example Usage:
    -------------
    >>> sentiment = [{"ticker": "AAPL", "sentiment_score": 0.6, "source": "newsapi"}]
    >>> strategies = [
    ...     {"signal": "BUY", "confidence": 0.8, "strategy": "momentum"},
    ...     {"signal": "HOLD", "confidence": 0.4, "strategy": "mean_reversion"}
    ... ]
    >>> regime = {"regime": "risk_on", "confidence": 0.85}
    >>> weights = {"momentum": 0.7, "mean_reversion": 0.5, "sentiment": 0.6}
    >>>
    >>> result = combine_signals(sentiment, strategies, regime, weights)
    >>> print(f"Final signal: {result['signal']} with {result['confidence']:.2f} confidence")

    Error Handling:
    --------------
    - Missing weights default to 0.5 (neutral)
    - Invalid confidence scores clamped to 0.0-1.0 range
    - Empty signal lists handled gracefully (return HOLD)
    - Malformed inputs logged and filtered out

    Signal Attribution:
    ------------------
    The components dictionary provides full transparency:
    - Individual strategy contributions before and after regime adjustment
    - Sentiment score and source attribution
    - Regime modification effects
    - Final weighted average calculation
    """
    raise NotImplementedError("Not yet implemented")


def _load_weights(db_path: str) -> Dict[str, float]:
    """Load learned strategy and source weights from Turso database.

    This function retrieves the current learned weights for all strategies
    and news sources from the persistent weights table. These weights are
    continuously updated by the feedback loop based on trade outcomes.

    Database Schema:
    ---------------
    Queries the `weights` table:
    ```sql
    SELECT category, name, weight
    FROM weights
    WHERE category IN ('strategy', 'source')
    ```

    Weight Categories:
    -----------------
    - **strategy**: Technical strategy weights (momentum, mean_reversion, etc.)
    - **source**: News source weights (marketaux, newsapi, scraped)

    Default Weight Handling:
    -----------------------
    - New strategies/sources start with weight 0.5 (neutral)
    - Missing weights in database default to 0.5
    - Weight bounds enforced: 0.1 minimum, 1.0 maximum
    - Invalid weights logged and reset to defaults

    Performance Caching:
    -------------------
    - Weights cached for 60 seconds to reduce database load
    - Cache invalidated on weight updates
    - High-frequency combiner calls use cached values
    - Database connection pooled for efficiency

    Args:
        db_path: Path to Turso database or connection string
                Can be local SQLite path for testing/development

    Returns:
        Dict mapping strategy/source names to learned weights:
        {
            "momentum": 0.75,           # Strategy performed well historically
            "mean_reversion": 0.45,     # Strategy performed poorly
            "marketaux": 0.65,          # News source moderately reliable
            "newsapi": 0.80,           # News source very reliable
            "sentiment": 0.70          # Combined sentiment reliability
        }

    Example Usage:
    -------------
    >>> weights = _load_weights("trader.db")
    >>> print(f"Momentum strategy weight: {weights.get('momentum', 0.5)}")

    Database Integration:
    --------------------
    - Uses same connection pattern as other database modules
    - Handles Turso authentication automatically
    - Graceful degradation if database unavailable
    - All database errors logged with context

    Error Handling:
    --------------
    - Database connection failures return default weights
    - Malformed weight values logged and defaulted
    - Missing table/schema handled gracefully
    - Never crashes signal combination process

    Weight Evolution:
    ----------------
    Weights are updated by feedback/weights.py using exponential moving average:
    - WIN outcome: nudge weight up toward 1.0
    - LOSS outcome: nudge weight down toward 0.0
    - Bounded between 0.1 and 1.0 to prevent full silencing
    - Gradual adaptation prevents overfitting to recent outcomes
    """
    raise NotImplementedError("Not yet implemented")