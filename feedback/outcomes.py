"""Trade outcome measurement for autonomous paper trading system.

This module measures the performance of executed trades and classifies outcomes
for the feedback loop. It runs as a scheduled job to check open trades and
determine when to close positions based on time and performance thresholds.

CRITICAL ARCHITECTURE NOTE:
===========================
This is a SCHEDULED COMPONENT that runs independently of the main trading cycle.
It monitors all open trades continuously and measures outcomes for the learning
system. Outcomes feed directly into weight adjustment via feedback/weights.py.

Outcome Measurement Philosophy:
==============================
The system learns by measuring trade outcomes across multiple timeframes:
- **Holding Period**: Default 8 hours, configurable per strategy
- **Time-based Exits**: Automatic closure after holding period
- **Target-based Exits**: Stop loss and take profit triggers
- **Performance Classification**: WIN/LOSS/NEUTRAL based on returns

Outcome measurement is conservative and focuses on:
- **Risk-adjusted returns**: Not just absolute gains
- **Consistent performance**: Avoiding large losses
- **Time efficiency**: Faster positive outcomes preferred

Classification Thresholds:
=========================
Trade outcomes are classified as:
- **WIN**: Return > +1% (meaningful positive outcome)
- **LOSS**: Return < -1% (meaningful negative outcome)
- **NEUTRAL**: Return between -1% and +1% (no clear signal)

These thresholds focus learning on trades with clear directional outcomes
and avoid noise from small fluctuations around break-even.

Measurement Schedule:
====================
The outcome measurement runs on multiple schedules:
- **Every 4 hours**: Check for time-based exits and early outcomes
- **Market close**: Daily measurement and exit logic
- **Weekend**: Comprehensive analysis of all closed positions

This ensures timely outcome measurement without excessive polling.

Database Integration:
====================
Reads from `trades` table to find pending trades
Writes to `outcomes` table with measured results:
```sql
INSERT INTO outcomes (
    trade_id, exit_price, return_pct, outcome,
    holding_period_hours, measured_at
) VALUES (...)
```

Position Management:
===================
The outcome measurement system also handles:
- **Automatic exits**: Time-based and target-based closures
- **Stop loss triggers**: Immediate exits on loss thresholds
- **Take profit triggers**: Automatic profit taking
- **Position tracking**: Integration with Alpaca for current prices

Integration with Learning:
=========================
Measured outcomes feed into:
- feedback/weights.py: Strategy and source weight updates
- dashboard/app.py: Performance visualization and reporting
- Analysis systems: Historical performance evaluation
- Circuit breaker: System halt evaluation based on outcomes
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone, timedelta

# Configure logging
logger = logging.getLogger(__name__)

# Outcome classification thresholds
WIN_THRESHOLD = 0.01      # +1% return = WIN
LOSS_THRESHOLD = -0.01    # -1% return = LOSS
# Between thresholds = NEUTRAL

# Default holding periods (hours)
DEFAULT_HOLDING_PERIOD = 8    # 8 hours default
MAX_HOLDING_PERIOD = 72       # 3 days maximum
MIN_HOLDING_PERIOD = 1        # 1 hour minimum


def measure_outcomes(db_path: str) -> List[Dict[str, Any]]:
    """Measure outcomes for all pending trades and classify performance.

    This is the main entry point for outcome measurement. It finds all open
    trades, checks their current performance, and determines if they should
    be closed based on time or performance criteria.

    Measurement Process:
    -------------------
    1. **Find Pending Trades**: Query database for open positions
    2. **Get Current Prices**: Fetch live prices from market data
    3. **Calculate Returns**: Compute performance since entry
    4. **Apply Exit Logic**: Check time and target-based exit criteria
    5. **Classify Outcomes**: WIN/LOSS/NEUTRAL based on returns
    6. **Update Database**: Record outcomes and close positions
    7. **Trigger Actions**: Alert weight update system

    Exit Criteria:
    -------------
    **Time-based Exits:**
    - Default: Close after 8 hours
    - Strategy-specific: Different holding periods per strategy
    - Weekend: Close all positions before market close Friday

    **Performance-based Exits:**
    - Stop Loss: Immediate exit if hit
    - Take Profit: Immediate exit if hit
    - Trailing Stop: Dynamic stop loss adjustment

    **Market-based Exits:**
    - Market Close: Close intraday positions
    - Gap Risk: Exit before earnings or major news
    - Volatility: Exit during extreme market conditions

    Outcome Calculation:
    -------------------
    Return = (Current Price - Entry Price) / Entry Price
    Holding Period = Current Time - Entry Time (in hours)

    Classification:
    - Return > +1%: WIN
    - Return < -1%: LOSS
    - Return -1% to +1%: NEUTRAL

    Args:
        db_path: Path to Turso database for trade and outcome data

    Returns:
        List of measured outcomes:
        [
            {
                "trade_id": str,           # UUID from original trade
                "ticker": str,             # Stock symbol
                "outcome": str,            # "WIN", "LOSS", or "NEUTRAL"
                "return_pct": float,       # Return percentage
                "holding_period_hours": float,  # Hours held
                "exit_price": float,       # Final exit price
                "exit_reason": str,        # Why trade was closed
                "measured_at": str         # ISO timestamp
            }
        ]

    Example Output:
    --------------
    >>> outcomes = measure_outcomes("trader.db")
    >>> for outcome in outcomes:
    ...     print(f"{outcome['ticker']}: {outcome['outcome']} "
    ...           f"({outcome['return_pct']:.1%}) after "
    ...           f"{outcome['holding_period_hours']:.1f}h")
    AAPL: WIN (+2.3%) after 6.5h
    MSFT: LOSS (-1.8%) after 4.2h
    GOOGL: NEUTRAL (+0.4%) after 8.0h

    Error Handling:
    --------------
    - Price fetch failures: Use last known price with warning
    - Database errors: Log and continue with available data
    - Position mismatches: Alert but don't crash measurement
    - Clock synchronization: Handle timezone differences gracefully

    Performance Impact:
    ------------------
    - Batched price fetching for efficiency
    - Database updates in transactions
    - Minimal market impact from measurement activity
    - Cached results to avoid redundant calculations

    Integration:
    -----------
    - Called by scheduler/loop.py on scheduled intervals
    - Results feed into feedback/weights.py for learning
    - Position changes sent to executor/alpaca.py for execution
    - Outcomes displayed in dashboard/app.py for monitoring
    """
    raise NotImplementedError("Not yet implemented")


def _get_pending_trades(db_path: str) -> List[Dict[str, Any]]:
    """Retrieve all trades that haven't been measured for outcomes yet.

    Queries the trades table for positions that:
    - Have been executed successfully (have order_id)
    - Don't have corresponding entry in outcomes table
    - Are within reasonable time window for measurement
    - Haven't been manually closed or cancelled

    Database Query Logic:
    --------------------
    ```sql
    SELECT t.* FROM trades t
    LEFT JOIN outcomes o ON t.trade_id = o.trade_id
    WHERE o.trade_id IS NULL
    AND t.order_id IS NOT NULL
    AND t.created_at > datetime('now', '-7 days')
    ORDER BY t.created_at ASC
    ```

    Data Enrichment:
    ---------------
    Adds calculated fields to trade data:
    - Age in hours since execution
    - Expected holding period based on strategy
    - Current status (open, approaching exit, overdue)

    Args:
        db_path: Database connection string for trade lookup

    Returns:
        List of pending trade records with metadata:
        [
            {
                "trade_id": str,
                "ticker": str,
                "signal": str,
                "entry_price": float,
                "shares": int,
                "stop_loss_price": float,
                "take_profit_price": float,
                "created_at": str,
                "age_hours": float,
                "target_holding_hours": float
            }
        ]
    """
    raise NotImplementedError("Not yet implemented")


def _classify_outcome(return_pct: float) -> str:
    """Classify trade return percentage into outcome category.

    Uses fixed thresholds to categorize trade performance:
    - Meaningful wins and losses are separated from noise
    - Conservative thresholds focus learning on clear signals
    - Neutral zone prevents overreaction to small movements

    Classification Logic:
    --------------------
    - WIN: Return > +1.0% (meaningful profit)
    - LOSS: Return < -1.0% (meaningful loss)
    - NEUTRAL: -1.0% ≤ Return ≤ +1.0% (noise/uncertain)

    The 1% threshold is chosen to:
    - Exceed typical bid-ask spreads and transaction costs
    - Focus on trades with clear directional outcomes
    - Avoid over-fitting to random market noise
    - Provide meaningful signal for strategy adjustment

    Learning Implications:
    ---------------------
    - WIN outcomes: Increase weights for contributing strategies/sources
    - LOSS outcomes: Decrease weights for contributing strategies/sources
    - NEUTRAL outcomes: No weight adjustments (insufficient signal)

    Args:
        return_pct: Trade return as decimal (0.02 = 2%)

    Returns:
        Outcome classification: "WIN", "LOSS", or "NEUTRAL"

    Example Classifications:
    -----------------------
    >>> _classify_outcome(0.025)   # +2.5% return
    "WIN"
    >>> _classify_outcome(-0.018)  # -1.8% return
    "LOSS"
    >>> _classify_outcome(0.005)   # +0.5% return
    "NEUTRAL"
    >>> _classify_outcome(-0.003)  # -0.3% return
    "NEUTRAL"
    """
    raise NotImplementedError("Not yet implemented")