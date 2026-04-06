"""Risk management and position sizing for autonomous paper trading system.

Every signal from engine/combiner.py must pass through check_trade() and
receive approval before any order is placed via executor/alpaca.py.

Risk checks (all must pass):
  1. Penny stock filter (price >= $5)
  2. Market cap filter (>= $1B)
  3. Portfolio cash reserve (keep 20% cash)
  4. Max open positions (15)
  5. Single ticker allocation (max 10%)
  6. Sector concentration (max 30%)
  7. Duplicate signal cooldown (2 hours)
  8. Position sizing + stop/TP placement
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# ── Risk constants ──────────────────────────────────────────────────────────
MAX_RISK_PER_TRADE = 0.02           # 2% of portfolio per trade
MAX_SHARES_PER_POSITION = 500       # cap per ticker
MAX_PORTFOLIO_ALLOCATION = 0.80     # keep 20% cash
MAX_SINGLE_TICKER_ALLOCATION = 0.10 # 10% max per ticker
MAX_SECTOR_ALLOCATION = 0.30        # 30% max per sector
MAX_OPEN_POSITIONS = 15
MIN_STOCK_PRICE = 5.0               # no penny stocks
MIN_MARKET_CAP = 1_000_000_000      # no micro-caps (1B)
STOP_LOSS_PCT = 0.03                # 3% stop distance
TAKE_PROFIT_PCT = 0.03              # 3% TP distance
DUPLICATE_SIGNAL_HOURS = 2
RISK_OFF_SIZE_REDUCTION = 0.75      # reduce 25% in risk-off
SECTOR_OVERWEIGHT_REDUCTION = 0.50  # halve size if sector already at 20%+
SECTOR_OVERWEIGHT_THRESHOLD = 0.20  # trigger reduction above 20%


# ── Rejection helper ────────────────────────────────────────────────────────

def _reject(reason: str) -> Dict[str, Any]:
    return {
        "approved": False,
        "reason": reason,
        "position_size": 0,
        "shares": 0,
        "entry_price": 0.0,
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "portfolio_allocation_pct": 0.0,
    }


# ── Main entry point ───────────────────────────────────────────────────────

def check_trade(
    signal: Dict[str, Any],
    portfolio: Dict[str, Any],
    market_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run all risk checks and return an approval/rejection with sizing.

    Args:
        signal: From combiner — must have ticker, signal (BUY/SELL), confidence.
        portfolio: Current portfolio state:
            {
                "cash": float,
                "equity": float,        # total portfolio value
                "positions": [          # list of open positions
                    {"ticker": "AAPL", "qty": 50, "market_value": 8750.0,
                     "avg_entry_price": 170.0, "unrealized_pl": 275.0},
                    ...
                ],
            }
        market_data: Per-ticker data:
            {"price": 175.5, "market_cap": 2.8e12, "volume": ..., ...}
            If None, price is required in signal dict.

    Returns:
        Approval dict with position sizing or rejection with reason.
    """
    ticker = signal.get("ticker", "")
    direction = signal.get("signal", "HOLD")
    confidence = signal.get("confidence", 0.0)

    if direction == "HOLD":
        return _reject("Signal is HOLD — nothing to trade")

    price = (market_data or {}).get("price") or signal.get("entry_price", 0.0)
    if price <= 0:
        return _reject(f"No valid price for {ticker}")

    equity = portfolio.get("equity", 0.0)
    cash = portfolio.get("cash", 0.0)
    positions = portfolio.get("positions", [])

    if equity <= 0:
        return _reject("Portfolio equity is zero or negative")

    # ── 1. Penny stock filter ───────────────────────────────────────────
    if price < MIN_STOCK_PRICE:
        return _reject(f"Penny stock: {ticker} price ${price:.2f} < ${MIN_STOCK_PRICE}")

    # ── 2. Market cap filter ────────────────────────────────────────────
    market_cap = _get_market_cap(ticker, market_data)
    if market_cap is not None and market_cap < MIN_MARKET_CAP:
        return _reject(
            f"Micro-cap: {ticker} market cap ${market_cap/1e9:.2f}B < ${MIN_MARKET_CAP/1e9:.0f}B minimum"
        )

    # ── 3. Max open positions ───────────────────────────────────────────
    existing_position = _find_position(ticker, positions)
    if existing_position is None and len(positions) >= MAX_OPEN_POSITIONS:
        return _reject(f"Max open positions reached ({MAX_OPEN_POSITIONS})")

    # ── 4. Cash reserve check ───────────────────────────────────────────
    min_cash = equity * (1 - MAX_PORTFOLIO_ALLOCATION)
    if direction == "BUY" and cash <= min_cash:
        return _reject(
            f"Insufficient cash: ${cash:.0f} available, "
            f"${min_cash:.0f} required as 20% reserve"
        )

    # ── 5. Single ticker allocation ─────────────────────────────────────
    current_ticker_value = _position_value(existing_position)
    max_ticker_value = equity * MAX_SINGLE_TICKER_ALLOCATION
    available_for_ticker = max_ticker_value - current_ticker_value
    if direction == "BUY" and available_for_ticker <= 0:
        return _reject(
            f"Ticker allocation exceeded: {ticker} already at "
            f"${current_ticker_value:.0f} ({current_ticker_value/equity*100:.1f}% of portfolio)"
        )

    # ── 6. Sector concentration ─────────────────────────────────────────
    sector = _lookup_sector(ticker)
    sector_value = _sector_exposure(sector, positions) if sector else 0.0
    sector_pct = sector_value / equity if equity > 0 else 0.0

    if direction == "BUY" and sector and sector_pct >= MAX_SECTOR_ALLOCATION:
        return _reject(
            f"Sector allocation would exceed {MAX_SECTOR_ALLOCATION*100:.0f}%: "
            f"{sector} currently at {sector_pct*100:.1f}%"
        )

    # ── 7. Duplicate signal cooldown ────────────────────────────────────
    if _has_recent_signal(ticker, direction):
        return _reject(
            f"Duplicate signal: {direction} on {ticker} within last {DUPLICATE_SIGNAL_HOURS} hours"
        )

    # ── 8. Position sizing ──────────────────────────────────────────────
    shares = calculate_position_size(
        signal, portfolio, market_data, sector_pct
    )
    if shares <= 0:
        return _reject(f"Calculated position size is 0 for {ticker}")

    order_value = shares * price

    # Re-check cash after sizing (BUY only)
    if direction == "BUY":
        remaining_cash = cash - order_value
        if remaining_cash < min_cash:
            # Reduce shares to stay within cash reserve
            max_order_value = cash - min_cash
            if max_order_value <= 0:
                return _reject("Insufficient cash after reserve")
            shares = int(max_order_value / price)
            if shares <= 0:
                return _reject("Position too small after cash reserve adjustment")
            order_value = shares * price

    # Re-check ticker allocation after sizing
    if direction == "BUY" and (current_ticker_value + order_value) > max_ticker_value:
        shares = int(available_for_ticker / price)
        if shares <= 0:
            return _reject("Position too small after ticker allocation cap")
        order_value = shares * price

    # Stop loss / take profit
    if direction == "BUY":
        stop_loss = round(price * (1 - STOP_LOSS_PCT), 2)
        take_profit = round(price * (1 + TAKE_PROFIT_PCT), 2)
    else:
        stop_loss = round(price * (1 + STOP_LOSS_PCT), 2)
        take_profit = round(price * (1 - TAKE_PROFIT_PCT), 2)

    allocation_pct = round((order_value / equity) * 100, 2)

    logger.info(
        f"Trade approved: {direction} {shares} shares of {ticker} @ ${price:.2f} "
        f"(${order_value:.0f}, {allocation_pct}% of portfolio)"
    )

    return {
        "approved": True,
        "reason": "",
        "position_size": shares,
        "shares": shares,
        "entry_price": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "portfolio_allocation_pct": allocation_pct,
    }


# ── Position sizing ────────────────────────────────────────────────────────

def calculate_position_size(
    signal: Dict[str, Any],
    portfolio: Dict[str, Any],
    market_data: Optional[Dict[str, Any]] = None,
    sector_pct: float = 0.0,
) -> int:
    """Calculate position size based on risk budget, confidence, and regime.

    Formula:
        risk_budget = equity * MAX_RISK_PER_TRADE
        stop_distance = price * STOP_LOSS_PCT
        base_shares = risk_budget / stop_distance
        adjusted = base_shares * confidence * regime_factor * sector_factor
        capped at MAX_SHARES_PER_POSITION and max ticker allocation

    Returns integer share count (0 if calculation fails).
    """
    price = (market_data or {}).get("price") or signal.get("entry_price", 0.0)
    if price <= 0:
        return 0

    equity = portfolio.get("equity", 0.0)
    if equity <= 0:
        return 0

    confidence = signal.get("confidence", 0.5)
    regime = signal.get("regime", "neutral")

    # Base: 2% risk budget / 3% stop distance
    risk_budget = equity * MAX_RISK_PER_TRADE
    stop_distance = price * STOP_LOSS_PCT
    if stop_distance <= 0:
        return 0

    base_shares = risk_budget / stop_distance

    # Scale by confidence (0.55–0.95 range maps to ~0.6–1.0 multiplier)
    confidence_factor = max(0.5, min(1.0, confidence))

    # Regime factor
    regime_factor = RISK_OFF_SIZE_REDUCTION if regime == "risk_off" else 1.0

    # Sector overweight factor
    sector_factor = SECTOR_OVERWEIGHT_REDUCTION if sector_pct >= SECTOR_OVERWEIGHT_THRESHOLD else 1.0

    shares = int(base_shares * confidence_factor * regime_factor * sector_factor)

    # Hard cap on share count
    shares = min(shares, MAX_SHARES_PER_POSITION)

    return max(0, shares)


# ── Internal helpers ────────────────────────────────────────────────────────

def _find_position(ticker: str, positions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Find existing position for ticker, if any."""
    for pos in positions:
        if pos.get("ticker", "").upper() == ticker.upper():
            return pos
    return None


def _position_value(position: Optional[Dict[str, Any]]) -> float:
    """Get market value of a position (0 if no position)."""
    if position is None:
        return 0.0
    return position.get("market_value", 0.0)


def _get_market_cap(ticker: str, market_data: Optional[Dict[str, Any]]) -> Optional[float]:
    """Get market cap from market data or sector cache.

    Returns None if unavailable (which means the check is skipped — we
    don't reject trades just because market cap data is missing).
    """
    if market_data and "market_cap" in market_data:
        return market_data["market_cap"]
    try:
        from db.client import get_db
        db = get_db()
        result = db.execute(
            "SELECT market_cap FROM sector_cache WHERE ticker = ?", [ticker]
        )
        if result.rows and result.rows[0][0] is not None:
            return result.rows[0][0]
    except Exception:
        pass
    return None


def _lookup_sector(ticker: str) -> Optional[str]:
    """Look up ticker sector from DB cache, fetching via yfinance if missing."""
    try:
        from db.client import get_sector_from_cache, cache_sector
        sector = get_sector_from_cache(ticker)
        if sector:
            return sector

        # Cache miss — fetch from yfinance
        import yfinance as yf
        info = yf.Ticker(ticker).info
        sector = info.get("sector")
        market_cap = info.get("marketCap")
        if sector:
            cache_sector(ticker, sector, market_cap=market_cap)
            return sector
    except Exception as e:
        logger.warning(f"Could not look up sector for {ticker}: {e}")
    return None


def _sector_exposure(sector: str, positions: List[Dict[str, Any]]) -> float:
    """Calculate total market value of positions in the given sector."""
    if not sector:
        return 0.0
    total = 0.0
    for pos in positions:
        pos_sector = _lookup_sector(pos.get("ticker", ""))
        if pos_sector and pos_sector.lower() == sector.lower():
            total += pos.get("market_value", 0.0)
    return total


def _has_recent_signal(ticker: str, direction: str) -> bool:
    """Check if same signal was generated for this ticker within cooldown window."""
    try:
        from db.client import get_recent_trades
        cutoff = datetime.now(timezone.utc) - timedelta(hours=DUPLICATE_SIGNAL_HOURS)
        trades = get_recent_trades(limit=50)
        for trade in trades:
            if (
                trade.get("ticker", "").upper() == ticker.upper()
                and trade.get("signal", "") == direction
            ):
                created = trade.get("created_at", "")
                try:
                    trade_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    if trade_time >= cutoff:
                        return True
                except (ValueError, TypeError):
                    pass
        return False
    except Exception as e:
        logger.warning(f"Could not check recent trades for {ticker}: {e}")
        return False
