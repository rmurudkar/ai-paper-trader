"""Strategy and source weight adjustment with circuit breaker for autonomous trading.

This module implements the core learning mechanism for the autonomous trading system.
It adjusts strategy and news source weights based on trade outcomes and monitors
system performance to trigger circuit breaker protection when needed.

CRITICAL ARCHITECTURE NOTE:
===========================
This module is the BRAIN of the learning system. It processes every trade outcome
and continuously adjusts the weights that determine how much influence each
strategy and news source has on future trading decisions.

Learning Philosophy:
===================
The system uses exponential moving average to gradually adjust weights:
- **Gradual Adaptation**: Prevents overreaction to recent outcomes
- **Bounded Learning**: Weights stay between 0.1 and 1.0
- **Strategy Attribution**: Each outcome traces back to contributing strategies
- **Source Attribution**: News sources are weighted by sentiment accuracy

Weight Update Formula:
=====================
```python
# WIN: nudge weight up toward 1.0
new_weight = old_weight * 0.95 + 1.0 * 0.05

# LOSS: nudge weight down toward 0.0
new_weight = old_weight * 0.95 + 0.0 * 0.05

# Clamp between bounds
new_weight = max(0.1, min(1.0, new_weight))
```

This creates 5% adjustment per outcome with 95% memory of previous performance.

Weight Categories:
=================
**Strategy Weights:**
- momentum: Trend-following strategy performance
- mean_reversion: Counter-trend strategy performance
- ma_crossover: Moving average crossover performance
- volume_surge: Volume confirmation strategy performance

**Source Weights:**
- marketaux: Pre-tagged sentiment reliability
- newsapi: Scraped sentiment accuracy
- sentiment: Combined sentiment scoring effectiveness

Circuit Breaker System:
=======================
Monitors rolling 7-day performance and triggers automatic trading halt when:
- Win rate falls below 40%
- Daily loss exceeds 5% of portfolio
- Weekly loss exceeds 10% of portfolio
- System error rate exceeds 10%

Circuit breaker activation:
1. Halts ALL new trades immediately
2. Sends alert notifications (email/Slack)
3. Sets database flag to prevent trade execution
4. Requires manual reset to resume trading

Database Integration:
====================
Updates `weights` table with new strategy/source weights
Monitors `circuit_breaker` table for system halt status
Reads `outcomes` table for performance measurement
Queries `trades` table for attribution analysis

The learning system enables continuous adaptation without manual intervention
while protecting against catastrophic performance degradation.
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta
import statistics

# Configure logging
logger = logging.getLogger(__name__)

# Weight update parameters
WEIGHT_LEARNING_RATE = 0.05        # 5% adjustment per outcome
MIN_WEIGHT = 0.1                   # Never fully silence strategies
MAX_WEIGHT = 1.0                   # Maximum strategy influence
INITIAL_WEIGHT = 0.5               # Starting weight for new strategies

# Circuit breaker thresholds
CIRCUIT_BREAKER_WIN_RATE = 0.40    # 40% minimum win rate over 7 days
CIRCUIT_BREAKER_DAILY_LOSS = 0.05  # 5% daily loss limit
CIRCUIT_BREAKER_WEEKLY_LOSS = 0.10 # 10% weekly loss limit
ROLLING_WINDOW_DAYS = 7            # Performance measurement window


def update_weights(outcome: Dict[str, Any], db_path: str) -> None:
    """Update strategy and source weights based on trade outcome.

    This is the core learning function that processes each trade outcome and
    adjusts the weights of contributing strategies and news sources. The
    adjustment uses exponential moving average for gradual, stable learning.

    Weight Update Process:
    ---------------------
    1. **Parse Attribution**: Extract strategies and sources from outcome
    2. **Load Current Weights**: Get existing weights from database
    3. **Calculate Adjustments**: Apply learning formula based on outcome
    4. **Apply Bounds**: Clamp weights between minimum and maximum
    5. **Update Database**: Store new weights with timestamp
    6. **Log Changes**: Record weight adjustments for monitoring

    Attribution Processing:
    ----------------------
    Each trade outcome includes attribution data:
    - strategies_fired: List of strategies that contributed to signal
    - sentiment_source: Primary news source for sentiment
    - discovery_sources: How ticker was discovered (affects source weights)

    All contributing strategies and sources receive weight updates based
    on the trade outcome (WIN/LOSS/NEUTRAL).

    Learning Dynamics:
    ------------------
    **WIN Outcome:**
    - Contributing strategies: weights increase toward 1.0
    - News sources: sentiment source weight increases
    - Discovery sources: slight boost for successful discovery

    **LOSS Outcome:**
    - Contributing strategies: weights decrease toward 0.0
    - News sources: sentiment source weight decreases
    - Discovery sources: slight penalty for poor discovery

    **NEUTRAL Outcome:**
    - No weight adjustments (insufficient signal for learning)

    Weight Evolution Example:
    ------------------------
    Strategy starts at 0.5 (neutral):
    - After WIN: 0.5 * 0.95 + 1.0 * 0.05 = 0.525
    - After LOSS: 0.525 * 0.95 + 0.0 * 0.05 = 0.499
    - After WIN: 0.499 * 0.95 + 1.0 * 0.05 = 0.524

    Args:
        outcome: Trade outcome with attribution data:
                {
                    "trade_id": str,
                    "outcome": str,              # "WIN", "LOSS", "NEUTRAL"
                    "return_pct": float,
                    "strategies_fired": List[str], # Contributing strategies
                    "sentiment_source": str,     # News source for sentiment
                    "discovery_sources": List[str] # How ticker was found
                }
        db_path: Path to Turso database for weight storage

    Returns:
        None (weights updated in database)

    Example Usage:
    -------------
    >>> outcome = {
    ...     "trade_id": "abc123",
    ...     "outcome": "WIN",
    ...     "return_pct": 0.023,
    ...     "strategies_fired": ["momentum", "volume_surge"],
    ...     "sentiment_source": "newsapi",
    ...     "discovery_sources": ["news", "gainer"]
    ... }
    >>> update_weights(outcome, "trader.db")
    # momentum weight: 0.650 → 0.668
    # volume_surge weight: 0.450 → 0.478
    # newsapi weight: 0.720 → 0.734

    Error Handling:
    --------------
    - Database connection failures logged but don't crash system
    - Missing attribution data handled with sensible defaults
    - Invalid weight values corrected and logged
    - Malformed outcome data filtered and reported

    Performance:
    -----------
    - Batched database updates for efficiency
    - Weight calculations optimized for frequent updates
    - Minimal locking to avoid blocking other operations
    - Asynchronous processing where possible
    """
    raise NotImplementedError("Not yet implemented")


def check_circuit_breaker(db_path: str) -> bool:
    """Check if circuit breaker should be tripped based on performance metrics.

    Analyzes recent trading performance across multiple timeframes and metrics
    to determine if the system should halt trading to prevent further losses.
    This is a critical safety mechanism for capital preservation.

    Performance Metrics:
    -------------------
    **Rolling Win Rate (7 days):**
    - Percentage of profitable trades over rolling 7-day window
    - Threshold: 40% minimum win rate
    - Includes all closed positions with WIN/LOSS outcomes

    **Daily Loss Limits:**
    - Unrealized + realized losses for current trading day
    - Threshold: 5% of portfolio value
    - Prevents catastrophic single-day losses

    **Weekly Loss Limits:**
    - Cumulative losses over rolling 7-day period
    - Threshold: 10% of portfolio value
    - Catches sustained poor performance

    Circuit Breaker Logic:
    ---------------------
    Triggers halt if ANY condition is met:
    1. Win rate < 40% over 7 days (with minimum 10 trades)
    2. Daily unrealized loss > 5% of portfolio
    3. Weekly cumulative loss > 10% of portfolio
    4. System error rate > 10% over 24 hours

    Safety Philosophy:
    -----------------
    - **Fail-Safe**: Errs on side of caution
    - **Capital Preservation**: Protects against large drawdowns
    - **Systematic**: Rules-based, not discretionary
    - **Recoverable**: Manual reset allows resumption after review

    Args:
        db_path: Database path for performance data analysis

    Returns:
        True if circuit breaker should be tripped (halt trading)
        False if performance is acceptable (continue trading)

    Example Usage:
    -------------
    >>> if check_circuit_breaker("trader.db"):
    ...     logger.warning("Circuit breaker triggered - halting trading")
    ...     trip_circuit_breaker("Win rate below 40%", 0.35, "trader.db")
    ... else:
    ...     logger.info("Circuit breaker check passed - continuing trading")

    Performance Data:
    ----------------
    Analyzes recent trades from outcomes table:
    ```sql
    SELECT outcome, return_pct, measured_at
    FROM outcomes
    WHERE measured_at > datetime('now', '-7 days')
    ORDER BY measured_at DESC
    ```

    Integration:
    -----------
    - Called by scheduler/loop.py before each trading cycle
    - Results checked by risk/manager.py for trade approval
    - Status displayed in dashboard/app.py for monitoring
    - Alerts sent via email/Slack when triggered
    """
    raise NotImplementedError("Not yet implemented")


def _get_rolling_win_rate(db_path: str, days: int = 7) -> float:
    """Calculate rolling win rate over specified number of days.

    Computes the percentage of profitable trades (WIN outcomes) over a
    rolling time window. Used by circuit breaker to monitor system
    performance and trigger halts when performance degrades.

    Calculation Method:
    ------------------
    1. **Query Recent Trades**: Get all outcomes within time window
    2. **Filter WIN/LOSS**: Exclude NEUTRAL outcomes (no clear signal)
    3. **Calculate Percentage**: WIN count / (WIN + LOSS) count
    4. **Minimum Threshold**: Require at least 10 trades for valid rate

    Statistical Considerations:
    --------------------------
    - Excludes NEUTRAL outcomes to focus on clear wins/losses
    - Requires minimum trade count to avoid small-sample bias
    - Uses calendar days, not trading days, for consistent measurement
    - Handles weekend gaps and market holidays gracefully

    Args:
        db_path: Database path for outcome data
        days: Number of days for rolling window (default 7)

    Returns:
        Win rate as decimal (0.45 = 45% win rate)
        Returns 1.0 if insufficient trade history (optimistic default)

    Example Calculations:
    --------------------
    >>> # Recent outcomes: [WIN, WIN, LOSS, WIN, LOSS, LOSS, WIN]
    >>> win_rate = _get_rolling_win_rate("trader.db", 7)
    >>> print(f"Win rate: {win_rate:.1%}")
    Win rate: 57.1%  # 4 wins out of 7 trades

    >>> # Only 3 trades in window (below minimum threshold)
    >>> win_rate = _get_rolling_win_rate("trader.db", 1)
    >>> print(f"Win rate: {win_rate:.1%}")
    Win rate: 100.0%  # Default to optimistic until sufficient data

    Database Query:
    --------------
    ```sql
    SELECT outcome, COUNT(*) as count
    FROM outcomes
    WHERE measured_at > datetime('now', '-{days} days')
    AND outcome IN ('WIN', 'LOSS')
    GROUP BY outcome
    ```

    Integration:
    -----------
    - Primary input for circuit breaker decision
    - Displayed in dashboard performance metrics
    - Used for strategy performance evaluation
    - Feeds into system health monitoring
    """
    raise NotImplementedError("Not yet implemented")


def trip_circuit_breaker(reason: str, win_rate: float, db_path: str) -> None:
    """Activate circuit breaker to halt all trading operations.

    This function implements the emergency halt mechanism when system
    performance falls below acceptable thresholds. Once activated,
    all trading operations cease until manual reset.

    Circuit Breaker Activation:
    --------------------------
    1. **Database Flag**: Set tripped=True in circuit_breaker table
    2. **Timestamp**: Record exact time of activation
    3. **Attribution**: Store reason and performance metrics
    4. **Notifications**: Send alerts via configured channels
    5. **Logging**: Comprehensive log entry for audit trail

    System Impact:
    -------------
    **Immediate Effects:**
    - All new trade signals ignored
    - Existing positions remain open (not force-closed)
    - Risk manager rejects all new trades
    - Scheduler skips trading cycles

    **Continued Operations:**
    - Data collection continues normally
    - Outcome measurement proceeds
    - Dashboard remains functional
    - Manual analysis tools available

    Database Update:
    ---------------
    Updates circuit_breaker table:
    ```sql
    UPDATE circuit_breaker
    SET tripped = 1,
        tripped_at = current_timestamp,
        reason = ?,
        win_rate_at_trip = ?
    WHERE id = 1
    ```

    Notification Channels:
    ---------------------
    - **Email**: If ALERT_EMAIL configured
    - **Slack**: If SLACK_WEBHOOK_URL configured
    - **Dashboard**: Real-time status update
    - **Logs**: Detailed entry with full context

    Args:
        reason: Human-readable explanation for circuit breaker activation
               (e.g., "Win rate below 40%", "Daily loss exceeded 5%")
        win_rate: Current win rate that triggered the halt
                 Used for context and later analysis
        db_path: Database path for circuit breaker status update

    Example Usage:
    -------------
    >>> # Win rate dropped below threshold
    >>> trip_circuit_breaker(
    ...     reason="Rolling 7-day win rate fell to 35%",
    ...     win_rate=0.35,
    ...     db_path="trader.db"
    ... )
    >>> # System immediately halts all new trading

    Recovery Process:
    ----------------
    Circuit breaker requires manual reset:
    1. **Investigate**: Analyze what caused poor performance
    2. **Adjust**: Modify strategies or parameters if needed
    3. **Reset**: Manually set tripped=False in database
    4. **Monitor**: Watch initial trades closely after resumption

    The halt mechanism ensures human oversight during periods of
    poor performance and prevents automated systems from
    compounding losses during adverse market conditions.
    """
    raise NotImplementedError("Not yet implemented")