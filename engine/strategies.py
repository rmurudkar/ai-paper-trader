"""Technical trading strategy signals for autonomous paper trading system.

This module implements individual technical analysis strategies that generate
BUY/SELL/HOLD signals with confidence scores. Each strategy operates independently
and produces standardized signal outputs for combination by engine/combiner.py.

CRITICAL ARCHITECTURE NOTE:
===========================
Strategies are TICKER-AGNOSTIC - they process whatever tickers are provided by
discovery.py. Never hardcode ticker lists in this module. All strategies receive
market data and return standardized signal dictionaries.

Strategy Philosophy:
===================
Each strategy implements a specific technical analysis approach:
- **Momentum**: Trend-following using moving average relationships
- **Mean Reversion**: Counter-trend using RSI oversold/overbought levels
- **MA Crossover**: Breakout signals from moving average crossovers
- **Volume Surge**: Breakout confirmation using unusual volume patterns

All strategies include confidence scoring to weight signals appropriately
in the combiner. Higher confidence = stronger signal conviction.

Signal Standardization:
======================
Every strategy returns the same signal format:
```python
{
    "signal": "BUY" | "SELL" | "HOLD",
    "confidence": float,  # 0.0 to 1.0
    "strategy": str,      # Strategy name for tracking
    "reason": str         # Human-readable explanation
}
```

Market Data Dependencies:
========================
All strategies require market data from fetchers/market.py:
- price: Current market price
- volume: Current trading volume
- ma_20, ma_50, ma_200: Moving averages
- rsi_14: RSI indicator
- avg_volume_20: 20-day average volume
- price_change_pct: Daily price change percentage

Strategy Weights:
================
Each strategy's output is weighted by learned weights from the feedback loop:
- Initial weights: 0.5 (neutral)
- Range: 0.1 to 1.0 (never fully silenced)
- Updated via exponential moving average based on outcomes
- Tracked per strategy in Turso weights table

Performance Considerations:
==========================
- All calculations use vectorized operations where possible
- Market data is pre-fetched and passed to all strategies
- No external API calls within strategy functions
- Efficient memory usage for high-frequency execution

Risk Integration:
================
Strategies focus purely on signal generation - risk management happens
in risk/manager.py. Strategies should not consider:
- Position sizing
- Portfolio allocation
- Sector concentration
- Cash reserves

These are handled downstream in the trading pipeline.
"""

import logging
from typing import Dict, List, Any
import math

# Configure logging
logger = logging.getLogger(__name__)


def run_all_strategies(ticker: str, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Execute all technical strategy functions for a single ticker.

    This is the main entry point for strategy signal generation. It runs all
    available strategies against the provided market data and returns a list
    of standardized signal dictionaries.

    Strategy Execution:
    ------------------
    1. **Momentum Signal**: Trend-following using MA relationships
    2. **Mean Reversion Signal**: Counter-trend using RSI levels
    3. **MA Crossover Signal**: Breakout using MA crossovers
    4. **Volume Surge Signal**: Confirmation using volume patterns

    Each strategy executes independently and errors in one strategy
    don't affect others. Failed strategies are logged and skipped.

    Signal Aggregation:
    ------------------
    - Collects all valid signals into a list
    - Preserves individual strategy confidence scores
    - Maintains strategy attribution for feedback loop
    - Filters out invalid/error signals automatically

    Error Handling:
    --------------
    - Individual strategy failures are caught and logged
    - Failed strategies don't crash the entire signal generation
    - Missing market data is handled gracefully
    - Invalid confidence scores are normalized to valid ranges

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL")
                Used for logging and error reporting
        market_data: Dict containing required market data fields:
                    - price: float (current market price)
                    - volume: int (current trading volume)
                    - ma_20: float (20-day moving average)
                    - ma_50: float (50-day moving average)
                    - ma_200: float (200-day moving average)
                    - rsi_14: float (14-day RSI indicator)
                    - avg_volume_20: int (20-day average volume)
                    - price_change_pct: float (daily change percentage)

    Returns:
        List of signal dictionaries, one per strategy that executed successfully:
        [
            {
                "signal": "BUY",
                "confidence": 0.75,
                "strategy": "momentum",
                "reason": "price above 20MA which is above 50MA"
            },
            {
                "signal": "SELL",
                "confidence": 0.60,
                "strategy": "mean_reversion",
                "reason": "RSI overbought at 78 on red day"
            }
        ]

    Example Usage:
    -------------
    >>> market_data = {
    >>>     "price": 175.50,
    >>>     "ma_20": 170.25,
    >>>     "ma_50": 165.80,
    >>>     "rsi_14": 45.2,
    >>>     # ... other required fields
    >>> }
    >>> signals = run_all_strategies("AAPL", market_data)
    >>> for signal in signals:
    >>>     print(f"{signal['strategy']}: {signal['signal']} ({signal['confidence']})")
    """
    raise NotImplementedError("Not yet implemented")


def momentum_signal(ticker: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Generate momentum signal using moving average trend analysis.

    Momentum Strategy Logic:
    -----------------------
    **BUY Signal**: Price above 20MA AND 20MA above 50MA (confirmed uptrend)
    - Indicates strong bullish momentum
    - Both price and trend are aligned upward
    - Higher confidence when price is further above MAs

    **SELL Signal**: Price below 20MA AND 20MA below 50MA (confirmed downtrend)
    - Indicates strong bearish momentum
    - Both price and trend are aligned downward
    - Higher confidence when price is further below MAs

    **HOLD Signal**: Mixed signals or weak trends
    - Price and MAs not aligned in same direction
    - Trend is unclear or transitioning
    - Low confidence in directional movement

    Confidence Scoring:
    ------------------
    Confidence scales with trend strength measured as distance from moving averages:
    - Distance = abs(price - ma_20) / price as percentage
    - 0% distance = 0.3 confidence (weak signal)
    - 5%+ distance = 0.9 confidence (strong signal)
    - Clamped between 0.3 and 0.9 for realistic ranges

    Technical Details:
    -----------------
    - Uses 20MA and 50MA for trend identification
    - Requires both price and MA relationship alignment
    - Filters out whipsaw signals in sideways markets
    - Works best in trending market conditions

    Args:
        ticker: Stock ticker symbol for logging and error reporting
        market_data: Dict containing required fields:
                    - price: Current market price
                    - ma_20: 20-day moving average
                    - ma_50: 50-day moving average

    Returns:
        Signal dictionary:
        {
            "signal": "BUY" | "SELL" | "HOLD",
            "confidence": float,  # 0.3 to 0.9
            "strategy": "momentum",
            "reason": str  # Explanation of signal logic
        }

    Example Output:
    --------------
    >>> momentum_signal("AAPL", {"price": 175, "ma_20": 170, "ma_50": 165})
    {
        "signal": "BUY",
        "confidence": 0.75,
        "strategy": "momentum",
        "reason": "price above 20MA which is above 50MA, 2.9% trend strength"
    }
    """
    raise NotImplementedError("Not yet implemented")


def mean_reversion_signal(ticker: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Generate mean reversion signal using RSI oversold/overbought analysis.

    Mean Reversion Strategy Logic:
    -----------------------------
    **BUY Signal**: RSI below 30 (oversold) AND price up today
    - Stock is oversold but showing signs of recovery
    - Combines oversold condition with positive momentum
    - Expects bounce from oversold levels

    **SELL Signal**: RSI above 70 (overbought) AND price down today
    - Stock is overbought and showing signs of weakness
    - Combines overbought condition with negative momentum
    - Expects pullback from overbought levels

    **HOLD Signal**: RSI in neutral range (30-70) or momentum misaligned
    - No clear oversold/overbought condition
    - RSI extreme but price momentum contradictory
    - Wait for better setup

    Confidence Scoring:
    ------------------
    Confidence scales with RSI extremity:
    - RSI 20 or 80: 0.7 confidence (strong extreme)
    - RSI 30 or 70: 0.3 confidence (mild extreme)
    - Linear interpolation between thresholds
    - Requires price momentum confirmation for any signal

    Technical Details:
    -----------------
    - Uses 14-day RSI for overbought/oversold identification
    - Requires price momentum confirmation to avoid catching falling knives
    - Works best in range-bound or choppy market conditions
    - Complementary to momentum strategy (contrarian approach)

    Args:
        ticker: Stock ticker symbol for logging and error reporting
        market_data: Dict containing required fields:
                    - rsi_14: 14-day RSI indicator (0-100)
                    - price_change_pct: Daily price change percentage

    Returns:
        Signal dictionary:
        {
            "signal": "BUY" | "SELL" | "HOLD",
            "confidence": float,  # 0.3 to 0.7
            "strategy": "mean_reversion",
            "reason": str  # Explanation of signal logic
        }

    Example Output:
    --------------
    >>> mean_reversion_signal("AAPL", {"rsi_14": 25, "price_change_pct": 1.2})
    {
        "signal": "BUY",
        "confidence": 0.65,
        "strategy": "mean_reversion",
        "reason": "RSI oversold at 25 with positive momentum (+1.2%)"
    }
    """
    raise NotImplementedError("Not yet implemented")


def ma_crossover_signal(ticker: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Generate signal based on moving average crossover events.

    MA Crossover Strategy Logic:
    ---------------------------
    **BUY Signal**: 20MA crosses above 50MA (golden cross)
    - Bullish breakout signal
    - Short-term trend overtaking long-term trend
    - Often indicates start of sustained uptrend

    **SELL Signal**: 20MA crosses below 50MA (death cross)
    - Bearish breakdown signal
    - Short-term trend falling below long-term trend
    - Often indicates start of sustained downtrend

    **HOLD Signal**: No recent crossover or unclear trend
    - MAs parallel or not clearly crossing
    - No significant breakout signal
    - Wait for clearer directional signal

    Crossover Detection:
    -------------------
    - Compares current 20MA vs 50MA relationship
    - Requires clear separation (>0.5%) to avoid false signals
    - Higher confidence for larger separation at crossover
    - Filters out minor fluctuations around MA levels

    Confidence Scoring:
    ------------------
    - Fixed high confidence (0.8) for clear crossovers
    - Crossovers are infrequent but high-conviction signals
    - Reduced confidence (0.5) if separation is minimal
    - No signal if MAs are too close together

    Technical Details:
    -----------------
    - Uses 20MA and 50MA for crossover detection
    - Requires minimum separation to confirm crossover
    - Works best for identifying trend changes early
    - Lower frequency but higher conviction signals

    Args:
        ticker: Stock ticker symbol for logging and error reporting
        market_data: Dict containing required fields:
                    - ma_20: 20-day moving average
                    - ma_50: 50-day moving average

    Returns:
        Signal dictionary:
        {
            "signal": "BUY" | "SELL" | "HOLD",
            "confidence": float,  # 0.5 to 0.8
            "strategy": "ma_crossover",
            "reason": str  # Explanation of crossover signal
        }

    Example Output:
    --------------
    >>> ma_crossover_signal("AAPL", {"ma_20": 172, "ma_50": 170})
    {
        "signal": "BUY",
        "confidence": 0.8,
        "strategy": "ma_crossover",
        "reason": "20MA crossed above 50MA (golden cross), 1.2% separation"
    }
    """
    raise NotImplementedError("Not yet implemented")


def volume_surge_signal(ticker: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Generate signal based on unusual volume patterns and price movement.

    Volume Surge Strategy Logic:
    ---------------------------
    **BUY Signal**: Volume > 1.5x average AND price up (breakout volume)
    - Unusual volume confirms bullish price movement
    - High volume on up days indicates institutional buying
    - Volume surge validates price breakout

    **SELL Signal**: Volume > 1.5x average AND price down (panic volume)
    - Unusual volume confirms bearish price movement
    - High volume on down days indicates institutional selling
    - Volume surge validates price breakdown

    **HOLD Signal**: Normal volume or volume/price contradiction
    - Volume not unusual enough to signal breakout
    - Volume surge but price movement contradictory
    - Wait for confirmation from other strategies

    Volume Analysis:
    ---------------
    - Compares current volume to 20-day average volume
    - Minimum 1.5x average required for volume surge
    - Higher volume multiples increase signal confidence
    - Filters out low-volume noise and false breakouts

    Confidence Scoring:
    ------------------
    - Moderate confidence (0.5) as volume needs confirmation
    - Scales with volume multiple: 2x avg = higher confidence
    - Requires price momentum alignment for any signal
    - Designed to confirm signals from other strategies

    Technical Details:
    -----------------
    - Uses 20-day average volume for baseline comparison
    - Requires both volume surge and directional price movement
    - Works best as confirmation with momentum or breakout strategies
    - Helps filter false signals in low-volume conditions

    Args:
        ticker: Stock ticker symbol for logging and error reporting
        market_data: Dict containing required fields:
                    - volume: Current trading volume
                    - avg_volume_20: 20-day average volume
                    - price_change_pct: Daily price change percentage

    Returns:
        Signal dictionary:
        {
            "signal": "BUY" | "SELL" | "HOLD",
            "confidence": float,  # 0.4 to 0.6
            "strategy": "volume_surge",
            "reason": str  # Explanation of volume pattern
        }

    Example Output:
    --------------
    >>> volume_surge_signal("AAPL", {
    ...     "volume": 75000000,
    ...     "avg_volume_20": 45000000,
    ...     "price_change_pct": 2.3
    ... })
    {
        "signal": "BUY",
        "confidence": 0.55,
        "strategy": "volume_surge",
        "reason": "Volume surge 1.67x average with +2.3% price move"
    }
    """
    raise NotImplementedError("Not yet implemented")