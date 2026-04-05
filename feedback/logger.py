"""Trade logging for autonomous paper trading system feedback loop.

This module logs every executed trade with comprehensive metadata to enable
the feedback loop for strategy weight updates and performance analysis.
All trade data flows to the Turso database for persistent storage.

CRITICAL ARCHITECTURE NOTE:
===========================
Every trade execution must be logged through this module immediately after
order fill confirmation. The logged data feeds directly into the feedback
loop for continuous system improvement and weight adjustment.

Logging Philosophy:
==================
The system learns from every trade by capturing:
- **Signal Attribution**: Which strategies and sources contributed
- **Market Context**: Regime, sentiment, and discovery source
- **Execution Details**: Price, timing, and order metadata
- **Strategy Components**: Individual confidence scores and weights

This comprehensive logging enables the feedback loop to:
- Attribute trade outcomes to specific strategies/sources
- Adjust weights based on performance
- Identify patterns in successful vs failed trades
- Monitor system performance over time

Database Integration:
====================
All trade data is stored in the Turso `trades` table with schema:
- trade_id: UUID for unique identification
- ticker, signal, confidence: Core trade details
- sentiment_score, sentiment_source: Sentiment attribution
- strategies_fired: JSON array of contributing strategies
- discovery_sources: JSON array of discovery sources
- regime_mode: Market regime at time of trade
- article_urls: JSON array of news article URLs
- entry_price, shares, stop_loss, take_profit: Execution details
- order_id: Alpaca order identifier for tracking
- created_at: ISO timestamp for analysis

Trade Attribution Model:
=======================
The logging system maintains full attribution chains:

**Strategy Attribution:**
- Which technical strategies fired (momentum, mean_reversion, etc.)
- Individual strategy confidence scores
- Strategy weights at time of execution

**News Attribution:**
- Which news sources contributed sentiment
- Specific article URLs and sentiment scores
- News source reliability weights

**Discovery Attribution:**
- How the ticker was discovered (news, gainer, sector_rotation)
- Discovery cycle ID for cross-referencing
- Source priority and selection reasoning

Performance Integration:
=======================
Logged data feeds into:
- feedback/outcomes.py: Trade outcome measurement
- feedback/weights.py: Strategy/source weight updates
- dashboard/app.py: Performance visualization
- Analysis tools: Historical performance analysis

The logging system is the foundation for all learning and adaptation
in the autonomous trading system.
"""

import logging
import uuid
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

# Configure logging
logger = logging.getLogger(__name__)


def log_trade(trade_data: Dict[str, Any], db_path: str) -> str:
    """Log executed trade with full metadata for feedback loop analysis.

    This function captures comprehensive trade information immediately after
    order execution and stores it in the Turso database for the feedback loop.
    Every piece of data that contributed to the trading decision is preserved.

    Trade Data Capture:
    ------------------
    **Core Trade Information:**
    - Ticker symbol and trade direction (BUY/SELL)
    - Final combined confidence score
    - Entry price and share quantity
    - Stop loss and take profit levels

    **Signal Attribution:**
    - Individual strategy signals and confidence scores
    - Strategy weights used in combination
    - News source sentiment scores and weights
    - Final signal combination rationale

    **Market Context:**
    - Market regime at time of execution
    - Macro sentiment environment
    - Discovery source that identified the ticker
    - Article URLs that contributed to sentiment

    **Execution Metadata:**
    - Alpaca order ID for tracking
    - Execution timestamp (ISO format)
    - Order type and execution details
    - Portfolio state at time of execution

    Database Schema Integration:
    ---------------------------
    Maps trade data to Turso `trades` table schema:
    ```sql
    INSERT INTO trades (
        trade_id, ticker, signal, confidence,
        sentiment_score, sentiment_source,
        strategies_fired, discovery_sources,
        regime_mode, article_urls,
        entry_price, shares, stop_loss_price, take_profit_price,
        order_id, created_at
    ) VALUES (...)
    ```

    UUID Generation:
    ---------------
    Generates unique trade_id for cross-referencing:
    - Used by outcomes.py for performance measurement
    - Used by weights.py for attribution and updates
    - Used by dashboard for trade history display

    Args:
        trade_data: Complete trade information dictionary containing:
                   {
                       "ticker": str,              # Stock symbol
                       "signal": str,              # "BUY" or "SELL"
                       "confidence": float,        # Final confidence (0.0-1.0)
                       "sentiment_score": float,   # Aggregated sentiment
                       "sentiment_source": str,    # Primary news source
                       "strategies_fired": List[str],  # Contributing strategies
                       "discovery_sources": List[str], # How ticker was found
                       "regime_mode": str,         # Market regime
                       "article_urls": List[str],  # News article URLs
                       "entry_price": float,       # Execution price
                       "shares": int,             # Share quantity
                       "stop_loss_price": float,  # Stop loss level
                       "take_profit_price": float, # Take profit level
                       "order_id": str            # Alpaca order identifier
                   }
        db_path: Path to Turso database connection string

    Returns:
        Generated trade_id (UUID string) for cross-referencing
        Empty string if logging fails (error logged separately)

    Example Usage:
    -------------
    >>> trade_data = {
    ...     "ticker": "AAPL",
    ...     "signal": "BUY",
    ...     "confidence": 0.72,
    ...     "sentiment_score": 0.65,
    ...     "sentiment_source": "newsapi",
    ...     "strategies_fired": ["momentum", "volume_surge"],
    ...     "discovery_sources": ["news", "gainer"],
    ...     "regime_mode": "risk_on",
    ...     "article_urls": ["https://example.com/article1"],
    ...     "entry_price": 175.50,
    ...     "shares": 45,
    ...     "stop_loss_price": 170.24,
    ...     "take_profit_price": 180.67,
    ...     "order_id": "alpaca-12345"
    ... }
    >>>
    >>> trade_id = log_trade(trade_data, "trader.db")
    >>> print(f"Logged trade with ID: {trade_id}")

    Error Handling:
    --------------
    - Database connection failures are logged but don't crash execution
    - Malformed trade data is validated and corrected where possible
    - Missing fields are filled with sensible defaults
    - JSON serialization errors are handled gracefully
    - All errors logged with full context for debugging

    Integration Points:
    ------------------
    Called by:
    - executor/alpaca.py: After successful order execution
    - dashboard/app.py: For manual trade logging (if supported)

    Feeds into:
    - feedback/outcomes.py: For performance measurement
    - feedback/weights.py: For strategy weight updates
    - dashboard/app.py: For trade history display

    Performance Considerations:
    --------------------------
    - Database writes are async where possible
    - JSON serialization is optimized for common data types
    - Large article URL lists are truncated if necessary
    - Database connection pooling reduces overhead
    - Failed writes are queued for retry
    """
    raise NotImplementedError("Not yet implemented")