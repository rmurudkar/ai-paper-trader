"""Streamlit dashboard for the AI paper trader.

Comprehensive UI covering:
  - Portfolio overview (equity, cash, buying power, daily P&L)
  - Open positions with live P&L
  - Trade history with full signal metadata and outcomes
  - Active tickers panel (discovery sources, mode)
  - Signal feed with confidence breakdown
  - Learned weight table (strategies + sources)
  - Macro regime indicator with inputs
  - Sector exposure pie chart
  - Portfolio value over time
  - Rolling win rate chart with 40% threshold
  - Circuit breaker status with manual override
  - Settings panel (ticker mode, watchlist, max tickers)

Run: streamlit run dashboard/app.py
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

# Ensure project root is in Python path for module imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv

from risk.manager import (
    MAX_RISK_PER_TRADE, MAX_SHARES_PER_POSITION, MAX_PORTFOLIO_ALLOCATION,
    MAX_SINGLE_TICKER_ALLOCATION, MAX_SECTOR_ALLOCATION, MAX_OPEN_POSITIONS,
    MIN_STOCK_PRICE, MIN_MARKET_CAP, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ── Page config ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AI Paper Trader",
    page_icon="$",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Data loading helpers (cached) ──────────────────────────────────────────

@st.cache_data(ttl=30)
def _load_portfolio() -> dict:
    """Fetch portfolio from Alpaca. Returns empty dict on failure."""
    try:
        from executor.alpaca import get_portfolio
        return get_portfolio()
    except Exception as e:
        logger.warning(f"Portfolio fetch failed: {e}")
        return {"cash": 0, "equity": 0, "buying_power": 0, "positions": []}


@st.cache_data(ttl=30)
def _load_positions() -> list[dict]:
    try:
        from executor.alpaca import get_positions
        return get_positions()
    except Exception:
        return []


@st.cache_data(ttl=60)
def _load_recent_trades(limit: int = 100) -> list[dict]:
    try:
        from db.client import get_recent_trades
        return get_recent_trades(limit=limit)
    except Exception:
        return []


@st.cache_data(ttl=60)
def _load_trades_with_outcomes(limit: int = 200) -> list[dict]:
    """Load trades joined with outcomes for history view."""
    try:
        from db.client import get_db
        db = get_db()
        result = db.execute(
            """
            SELECT
                t.trade_id, t.ticker, t.signal, t.confidence,
                t.sentiment_score, t.sentiment_source,
                t.strategies_fired, t.discovery_sources,
                t.regime_mode, t.entry_price, t.shares,
                t.stop_loss_price, t.take_profit_price,
                t.order_id, t.created_at,
                o.exit_price, o.return_pct, o.outcome,
                o.holding_period_hours, o.measured_at
            FROM trades t
            LEFT JOIN outcomes o ON t.trade_id = o.trade_id
            ORDER BY t.created_at DESC
            LIMIT ?
            """,
            [limit],
        )
        columns = [
            "trade_id", "ticker", "signal", "confidence",
            "sentiment_score", "sentiment_source",
            "strategies_fired", "discovery_sources",
            "regime_mode", "entry_price", "shares",
            "stop_loss_price", "take_profit_price",
            "order_id", "created_at",
            "exit_price", "return_pct", "outcome",
            "holding_period_hours", "measured_at",
        ]
        rows = []
        for row in result.rows:
            d = dict(zip(columns, row))
            d["strategies_fired"] = json.loads(d["strategies_fired"] or "[]")
            d["discovery_sources"] = json.loads(d["discovery_sources"] or "[]")
            rows.append(d)
        return rows
    except Exception:
        return []


@st.cache_data(ttl=60)
def _load_all_weights() -> dict:
    """Return {"strategy": {name: weight}, "source": {name: weight}}."""
    try:
        from db.client import get_all_weights
        return {
            "strategy": get_all_weights("strategy"),
            "source": get_all_weights("source"),
        }
    except Exception:
        return {"strategy": {}, "source": {}}


@st.cache_data(ttl=60)
def _load_circuit_breaker() -> dict:
    try:
        from db.client import get_db
        db = get_db()
        result = db.execute("SELECT tripped, tripped_at, reason, win_rate_at_trip FROM circuit_breaker WHERE id = 1")
        if result.rows:
            row = result.rows[0]
            return {
                "tripped": bool(row[0]),
                "tripped_at": row[1],
                "reason": row[2],
                "win_rate_at_trip": row[3],
            }
        return {"tripped": False, "tripped_at": None, "reason": None, "win_rate_at_trip": None}
    except Exception:
        return {"tripped": False, "tripped_at": None, "reason": None, "win_rate_at_trip": None}


@st.cache_data(ttl=60)
def _load_win_rate_series() -> list[dict]:
    """Daily win rate over the last 30 days for the chart."""
    try:
        from db.client import get_db
        db = get_db()
        result = db.execute(
            """
            SELECT
                DATE(measured_at) as day,
                SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN outcome IN ('WIN', 'LOSS') THEN 1 ELSE 0 END) as total
            FROM outcomes
            WHERE measured_at >= datetime('now', '-30 days')
            GROUP BY DATE(measured_at)
            ORDER BY day
            """
        )
        rows = []
        for row in result.rows:
            day, wins, total = row
            rate = (wins / total) if total > 0 else 0
            rows.append({"date": day, "win_rate": rate, "trades": total})
        return rows
    except Exception:
        return []


@st.cache_data(ttl=60)
def _load_equity_history() -> list[dict]:
    """Reconstruct portfolio value over time from trade outcomes.

    This is an approximation: sums realized P&L from closed trades by day.
    """
    try:
        from db.client import get_db
        db = get_db()
        result = db.execute(
            """
            SELECT
                DATE(o.measured_at) as day,
                SUM(o.return_pct * t.entry_price * t.shares / 100.0) as daily_pnl,
                COUNT(*) as trades
            FROM outcomes o
            JOIN trades t ON o.trade_id = t.trade_id
            WHERE o.measured_at >= datetime('now', '-30 days')
            GROUP BY DATE(o.measured_at)
            ORDER BY day
            """
        )
        return [
            {"date": row[0], "daily_pnl": row[1] or 0, "trades": row[2]}
            for row in result.rows
        ]
    except Exception:
        return []


@st.cache_data(ttl=60)
def _load_sector_exposure(positions: tuple) -> dict:
    """Sector allocation from open positions. Accepts tuple for caching."""
    sectors = {}
    for pos in positions:
        try:
            from db.client import get_sector_from_cache
            sector = get_sector_from_cache(pos["ticker"])
            if not sector:
                sector = "Unknown"
            val = pos.get("market_value", 0)
            sectors[sector] = sectors.get(sector, 0) + val
        except Exception:
            sectors["Unknown"] = sectors.get("Unknown", 0) + pos.get("market_value", 0)
    return sectors


@st.cache_data(ttl=60)
def _load_discovery_log() -> list[dict]:
    """Most recent discovery log entries."""
    try:
        from db.client import get_db
        db = get_db()
        result = db.execute(
            """
            SELECT cycle_id, ticker, source, discovered_at
            FROM discovery_log
            ORDER BY discovered_at DESC
            LIMIT 200
            """
        )
        return [
            {"cycle_id": r[0], "ticker": r[1], "source": r[2], "discovered_at": r[3]}
            for r in result.rows
        ]
    except Exception:
        return []


@st.cache_data(ttl=30)
def _load_regime() -> dict:
    """Fetch current macro regime data."""
    try:
        from fetchers.market import fetch_market_data
        from engine.regime import get_current_regime
        data = fetch_market_data([], include_sector_etfs=False)
        macro = data.get("macro", {})
        return get_current_regime(macro)
    except Exception:
        return {
            "regime": "unknown",
            "vix": None,
            "spy_vs_200ma": None,
            "yield_spread": None,
            "macro_sentiment": "unknown",
            "confidence": 0.0,
        }


@st.cache_data(ttl=30)
def _is_market_open() -> bool:
    try:
        from executor.alpaca import is_market_open
        return is_market_open()
    except Exception:
        return False


# ── Render functions ───────────────────────────────────────────────────────

def render_header():
    """Top banner with market status and circuit breaker."""
    col1, col2, col3 = st.columns([4, 2, 2])

    with col1:
        st.title("AI Paper Trader")
        st.caption("Autonomous paper trading with sentiment analysis & technical strategies")

    with col2:
        market_open = _is_market_open()
        if market_open:
            st.success("Market OPEN", icon=None)
        else:
            st.warning("Market CLOSED", icon=None)

    with col3:
        cb = _load_circuit_breaker()
        if cb["tripped"]:
            st.error("CIRCUIT BREAKER TRIPPED")
            st.caption(f"Reason: {cb['reason']}")
            if cb["win_rate_at_trip"] is not None:
                st.caption(f"Win rate at trip: {cb['win_rate_at_trip']:.1%}")
        else:
            st.success("Trading Active")


def render_portfolio_summary():
    """KPI cards: equity, cash, buying power, positions count, daily P&L."""
    portfolio = _load_portfolio()
    positions = portfolio.get("positions", [])

    equity = portfolio.get("equity", 0)
    cash = portfolio.get("cash", 0)
    buying_power = portfolio.get("buying_power", 0)
    invested = sum(p.get("market_value", 0) for p in positions)
    unrealized_pl = sum(p.get("unrealized_pl", 0) for p in positions)
    cash_pct = (cash / equity * 100) if equity > 0 else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Equity", f"${equity:,.2f}")
    c2.metric("Cash", f"${cash:,.2f}", delta=f"{cash_pct:.0f}% of portfolio")
    c3.metric("Buying Power", f"${buying_power:,.2f}")
    c4.metric("Positions", str(len(positions)), delta=f"of {15} max")
    c5.metric(
        "Unrealized P&L",
        f"${unrealized_pl:,.2f}",
        delta=f"{unrealized_pl/equity*100:.2f}%" if equity > 0 else "0%",
        delta_color="normal",
    )

    # Invested vs cash bar
    if equity > 0:
        inv_pct = invested / equity * 100
        st.progress(min(inv_pct / 100, 1.0), text=f"Invested: {inv_pct:.1f}% | Cash: {cash_pct:.1f}% (20% min reserve)")


def render_positions_table():
    """Open positions with live P&L."""
    positions = _load_positions()

    if not positions:
        st.info("No open positions")
        return

    df = pd.DataFrame(positions)
    df = df.rename(columns={
        "ticker": "Ticker",
        "qty": "Shares",
        "avg_entry_price": "Entry",
        "current_price": "Current",
        "market_value": "Value",
        "unrealized_pl": "P&L",
        "side": "Side",
    })

    df["Return %"] = ((df["Current"] - df["Entry"]) / df["Entry"] * 100).round(2)
    df["Entry"] = df["Entry"].apply(lambda x: f"${x:,.2f}")
    df["Current"] = df["Current"].apply(lambda x: f"${x:,.2f}")
    df["Value"] = df["Value"].apply(lambda x: f"${x:,.2f}")
    df["P&L"] = df["P&L"].apply(lambda x: f"${x:+,.2f}")

    display_cols = ["Ticker", "Side", "Shares", "Entry", "Current", "Value", "P&L", "Return %"]
    st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True,
    )


def render_trade_history():
    """Trade history with outcomes."""
    trades = _load_trades_with_outcomes()

    if not trades:
        st.info("No trades recorded yet")
        return

    rows = []
    for t in trades:
        outcome = t.get("outcome") or "OPEN"
        return_pct = t.get("return_pct")
        rows.append({
            "Date": t["created_at"][:16].replace("T", " ") if t["created_at"] else "",
            "Ticker": t["ticker"],
            "Signal": t["signal"],
            "Confidence": f"{t['confidence']:.2f}",
            "Entry": f"${t['entry_price']:,.2f}" if t["entry_price"] else "",
            "Exit": f"${t['exit_price']:,.2f}" if t.get("exit_price") else "-",
            "Return": f"{return_pct:+.2f}%" if return_pct is not None else "-",
            "Outcome": outcome,
            "Strategies": ", ".join(t.get("strategies_fired", [])),
            "Regime": t.get("regime_mode") or "-",
            "Sentiment": f"{t['sentiment_score']:.2f}" if t.get("sentiment_score") is not None else "-",
            "Hours Held": f"{t['holding_period_hours']:.1f}" if t.get("holding_period_hours") else "-",
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True, height=400)


def render_regime_indicator():
    """Macro regime display with inputs."""
    regime = _load_regime()
    regime_name = regime.get("regime", "unknown")

    if regime_name == "risk_on":
        st.success(f"Regime: RISK-ON (confidence: {regime.get('confidence', 0):.0%})")
    elif regime_name == "risk_off":
        st.error(f"Regime: RISK-OFF (confidence: {regime.get('confidence', 0):.0%})")
    else:
        st.warning(f"Regime: NEUTRAL (confidence: {regime.get('confidence', 0):.0%})")

    c1, c2, c3, c4 = st.columns(4)

    vix = regime.get("vix")
    spy = regime.get("spy_vs_200ma")
    ys = regime.get("yield_spread")
    macro = regime.get("macro_sentiment", "unknown")

    c1.metric("VIX", f"{vix:.1f}" if vix is not None else "N/A")
    c2.metric("SPY vs 200MA", f"{spy:+.2%}" if spy is not None else "N/A")
    c3.metric("Yield Spread", f"{ys:+.2f}%" if ys is not None else "N/A")
    c4.metric("Macro Sentiment", macro.capitalize())


def render_weight_table():
    """Learned weights for strategies and sources."""
    weights = _load_all_weights()

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Strategy Weights**")
        strat_w = weights.get("strategy", {})
        if strat_w:
            df = pd.DataFrame([
                {"Strategy": name, "Weight": f"{w:.3f}", "Bar": w}
                for name, w in sorted(strat_w.items())
            ])
            st.dataframe(df[["Strategy", "Weight"]], use_container_width=True, hide_index=True)

            fig = go.Figure(go.Bar(
                x=list(strat_w.values()),
                y=list(strat_w.keys()),
                orientation="h",
                marker_color=["#2ecc71" if v >= 0.5 else "#e74c3c" for v in strat_w.values()],
            ))
            fig.update_layout(
                xaxis_range=[0, 1],
                height=200,
                margin=dict(l=0, r=0, t=0, b=0),
                xaxis_title="Weight",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No strategy weights recorded yet")

    with col2:
        st.markdown("**Source Weights**")
        src_w = weights.get("source", {})
        if src_w:
            df = pd.DataFrame([
                {"Source": name, "Weight": f"{w:.3f}"}
                for name, w in sorted(src_w.items())
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            fig = go.Figure(go.Bar(
                x=list(src_w.values()),
                y=list(src_w.keys()),
                orientation="h",
                marker_color=["#3498db" if v >= 0.5 else "#e67e22" for v in src_w.values()],
            ))
            fig.update_layout(
                xaxis_range=[0, 1],
                height=150,
                margin=dict(l=0, r=0, t=0, b=0),
                xaxis_title="Weight",
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("No source weights recorded yet")


def render_sector_exposure():
    """Pie chart of portfolio allocation by sector."""
    positions = _load_positions()
    if not positions:
        st.info("No positions to display sector exposure")
        return

    # Convert to tuple of frozensets for caching
    pos_tuple = tuple(
        tuple(sorted(p.items())) for p in positions
    )
    sectors = _load_sector_exposure(pos_tuple)

    if not sectors:
        st.info("No sector data available")
        return

    fig = px.pie(
        names=list(sectors.keys()),
        values=list(sectors.values()),
        hole=0.4,
    )
    fig.update_layout(
        height=350,
        margin=dict(l=0, r=0, t=30, b=0),
    )
    fig.update_traces(textinfo="label+percent", textposition="inside")
    st.plotly_chart(fig, use_container_width=True)

    # Table view
    total = sum(sectors.values())
    rows = [
        {"Sector": s, "Value": f"${v:,.2f}", "Allocation": f"{v/total*100:.1f}%"}
        for s, v in sorted(sectors.items(), key=lambda x: -x[1])
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_win_rate_chart():
    """Rolling win rate with 40% circuit breaker threshold."""
    data = _load_win_rate_series()
    if not data:
        st.info("No outcome data for win rate chart")
        return

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["win_rate"],
        mode="lines+markers",
        name="Daily Win Rate",
        line=dict(color="#2ecc71", width=2),
    ))
    fig.add_hline(
        y=0.40,
        line_dash="dash",
        line_color="red",
        annotation_text="Circuit Breaker (40%)",
        annotation_position="top left",
    )
    fig.update_layout(
        yaxis_title="Win Rate",
        yaxis_tickformat=".0%",
        yaxis_range=[0, 1],
        xaxis_title="Date",
        height=300,
        margin=dict(l=0, r=0, t=30, b=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_equity_chart():
    """Cumulative P&L over time."""
    data = _load_equity_history()
    if not data:
        st.info("No trade data for equity chart yet")
        return

    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["date"])
    df["cumulative_pnl"] = df["daily_pnl"].cumsum()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["cumulative_pnl"],
        mode="lines",
        fill="tozeroy",
        name="Cumulative P&L",
        line=dict(color="#3498db", width=2),
    ))
    fig.add_trace(go.Bar(
        x=df["date"],
        y=df["daily_pnl"],
        name="Daily P&L",
        marker_color=["#2ecc71" if v >= 0 else "#e74c3c" for v in df["daily_pnl"]],
        opacity=0.5,
    ))
    fig.update_layout(
        yaxis_title="P&L ($)",
        xaxis_title="Date",
        height=350,
        margin=dict(l=0, r=0, t=30, b=0),
        barmode="overlay",
    )
    st.plotly_chart(fig, use_container_width=True)


def render_discovery_panel():
    """Active tickers panel showing discovery sources and mode."""
    mode = os.getenv("TICKER_MODE", "discovery")
    watchlist = os.getenv("WATCHLIST", "")

    st.markdown(f"**Mode:** `{mode}`")
    if watchlist:
        st.markdown(f"**Watchlist:** `{watchlist}`")

    log = _load_discovery_log()
    if not log:
        st.info("No discovery data yet")
        return

    # Group by most recent cycle
    if log:
        latest_cycle = log[0]["cycle_id"]
        cycle_entries = [e for e in log if e["cycle_id"] == latest_cycle]

        st.markdown(f"**Latest cycle:** `{latest_cycle}`")

        # Build ticker -> sources mapping
        ticker_sources = {}
        for entry in cycle_entries:
            tk = entry["ticker"]
            if tk not in ticker_sources:
                ticker_sources[tk] = []
            ticker_sources[tk].append(entry["source"])

        rows = [
            {"Ticker": tk, "Sources": ", ".join(srcs)}
            for tk, srcs in sorted(ticker_sources.items())
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=300)

        st.caption(f"{len(ticker_sources)} tickers discovered this cycle")


def render_circuit_breaker_controls():
    """Circuit breaker status with manual reset button."""
    cb = _load_circuit_breaker()

    if cb["tripped"]:
        st.error("TRADING HALTED - Circuit Breaker Tripped")
        st.markdown(f"**Reason:** {cb['reason']}")
        if cb["tripped_at"]:
            st.markdown(f"**Tripped at:** {cb['tripped_at']}")
        if cb["win_rate_at_trip"] is not None:
            st.markdown(f"**Win rate at trip:** {cb['win_rate_at_trip']:.1%}")

        if st.button("Reset Circuit Breaker", type="primary"):
            try:
                from db.client import reset_circuit_breaker
                reset_circuit_breaker()
                st.success("Circuit breaker reset. Trading will resume next cycle.")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Failed to reset: {e}")
    else:
        st.success("Circuit breaker is OFF - Trading is active")

        # Show current win rate
        try:
            from db.client import get_recent_win_rate
            wr = get_recent_win_rate(7)
            st.metric("7-Day Win Rate", f"{wr:.1%}")
        except Exception:
            pass


def render_settings_panel():
    """Settings panel for ticker mode, watchlist, and max tickers."""
    st.markdown("These settings are read from environment variables. "
                "Edit `.env` to change them permanently.")

    mode = os.getenv("TICKER_MODE", "discovery")
    watchlist = os.getenv("WATCHLIST", "")
    max_tickers = os.getenv("MAX_DISCOVERY_TICKERS", "30")

    st.text_input("TICKER_MODE", value=mode, disabled=True, help="'watchlist' or 'discovery'")
    st.text_input("WATCHLIST", value=watchlist, disabled=True, help="Comma-separated tickers")
    st.text_input("MAX_DISCOVERY_TICKERS", value=max_tickers, disabled=True)

    st.divider()

    st.markdown("**API Keys Status**")
    keys = {
        "ALPACA_API_KEY": bool(os.getenv("ALPACA_API_KEY")),
        "ANTHROPIC_API_KEY": bool(os.getenv("ANTHROPIC_API_KEY")),
        "MARKETAUX_API_KEY": bool(os.getenv("MARKETAUX_API_KEY")),
        "NEWSAPI_AI_KEY": bool(os.getenv("NEWSAPI_AI_KEY")),
        "GROQ_API_KEY": bool(os.getenv("GROQ_API_KEY")),
        "MASSIVE_API_KEY": bool(os.getenv("MASSIVE_API_KEY")),
        "TURSO_CONNECTION_URL": bool(os.getenv("TURSO_CONNECTION_URL")),
    }
    for key, configured in keys.items():
        if configured:
            st.markdown(f"- {key}: Configured")
        else:
            st.markdown(f"- {key}: **Not set**")


def render_outcome_breakdown():
    """Summary stats on trade outcomes."""
    trades = _load_trades_with_outcomes()
    if not trades:
        return

    closed = [t for t in trades if t.get("outcome")]
    if not closed:
        st.info("No closed trades yet")
        return

    wins = [t for t in closed if t["outcome"] == "WIN"]
    losses = [t for t in closed if t["outcome"] == "LOSS"]
    neutral = [t for t in closed if t["outcome"] == "NEUTRAL"]
    open_trades = [t for t in trades if not t.get("outcome")]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Wins", len(wins))
    c2.metric("Losses", len(losses))
    c3.metric("Neutral", len(neutral))
    c4.metric("Open", len(open_trades))

    if wins or losses:
        total_decisive = len(wins) + len(losses)
        win_rate = len(wins) / total_decisive if total_decisive > 0 else 0

        avg_win = sum(t["return_pct"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["return_pct"] for t in losses) / len(losses) if losses else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Win Rate", f"{win_rate:.1%}")
        c2.metric("Avg Win", f"{avg_win:+.2f}%")
        c3.metric("Avg Loss", f"{avg_loss:+.2f}%")

        # Outcome distribution
        fig = px.pie(
            names=["WIN", "LOSS", "NEUTRAL"],
            values=[len(wins), len(losses), len(neutral)],
            color=["WIN", "LOSS", "NEUTRAL"],
            color_discrete_map={"WIN": "#2ecc71", "LOSS": "#e74c3c", "NEUTRAL": "#95a5a6"},
            hole=0.4,
        )
        fig.update_layout(height=250, margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig, use_container_width=True)

    # Strategy performance breakdown
    st.markdown("**Performance by Strategy**")
    strat_stats = {}
    for t in closed:
        for s in t.get("strategies_fired", []):
            if s not in strat_stats:
                strat_stats[s] = {"wins": 0, "losses": 0, "returns": []}
            if t["outcome"] == "WIN":
                strat_stats[s]["wins"] += 1
            elif t["outcome"] == "LOSS":
                strat_stats[s]["losses"] += 1
            if t.get("return_pct") is not None:
                strat_stats[s]["returns"].append(t["return_pct"])

    if strat_stats:
        rows = []
        for name, stats in sorted(strat_stats.items()):
            total = stats["wins"] + stats["losses"]
            wr = stats["wins"] / total if total > 0 else 0
            avg_ret = sum(stats["returns"]) / len(stats["returns"]) if stats["returns"] else 0
            rows.append({
                "Strategy": name,
                "Trades": total,
                "Win Rate": f"{wr:.0%}",
                "Avg Return": f"{avg_ret:+.2f}%",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ── Main layout ────────────────────────────────────────────────────────────

def main():
    """Entry point - compose and run the Streamlit app."""
    render_header()
    st.divider()

    # ── Portfolio overview ──────────────────────────────────────────────
    render_portfolio_summary()

    # ── Tabbed main content ─────────────────────────────────────────────
    tabs = st.tabs([
        "Positions",
        "Trade History",
        "Performance",
        "Signals & Regime",
        "Discovery",
        "Risk Controls",
        "Settings",
    ])

    # Tab 1: Open Positions
    with tabs[0]:
        col1, col2 = st.columns([3, 2])
        with col1:
            st.subheader("Open Positions")
            render_positions_table()
        with col2:
            st.subheader("Sector Exposure")
            render_sector_exposure()

    # Tab 2: Trade History
    with tabs[1]:
        st.subheader("Trade History")
        render_trade_history()

    # Tab 3: Performance
    with tabs[2]:
        st.subheader("Performance Overview")
        render_outcome_breakdown()

        st.divider()
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Cumulative P&L")
            render_equity_chart()
        with col2:
            st.subheader("Win Rate Trend")
            render_win_rate_chart()

    # Tab 4: Signals & Regime
    with tabs[3]:
        col1, col2 = st.columns([3, 2])
        with col1:
            st.subheader("Learned Weights")
            render_weight_table()
        with col2:
            st.subheader("Macro Regime")
            render_regime_indicator()

    # Tab 5: Discovery
    with tabs[4]:
        st.subheader("Ticker Discovery")
        render_discovery_panel()

    # Tab 6: Risk Controls
    with tabs[5]:
        st.subheader("Circuit Breaker")
        render_circuit_breaker_controls()

        st.divider()
        st.subheader("Risk Parameters")
        
        params = pd.DataFrame([
            {"Parameter": "Max Risk Per Trade", "Value": f"{MAX_RISK_PER_TRADE:.0%}"},
            {"Parameter": "Max Shares Per Position", "Value": str(MAX_SHARES_PER_POSITION)},
            {"Parameter": "Max Portfolio Allocation", "Value": f"{MAX_PORTFOLIO_ALLOCATION:.0%}"},
            {"Parameter": "Max Single Ticker", "Value": f"{MAX_SINGLE_TICKER_ALLOCATION:.0%}"},
            {"Parameter": "Max Sector Allocation", "Value": f"{MAX_SECTOR_ALLOCATION:.0%}"},
            {"Parameter": "Max Open Positions", "Value": str(MAX_OPEN_POSITIONS)},
            {"Parameter": "Min Stock Price", "Value": f"${MIN_STOCK_PRICE:.2f}"},
            {"Parameter": "Min Market Cap", "Value": f"${MIN_MARKET_CAP/1e9:.0f}B"},
            {"Parameter": "Stop Loss", "Value": f"{STOP_LOSS_PCT:.0%}"},
            {"Parameter": "Take Profit", "Value": f"{TAKE_PROFIT_PCT:.0%}"},
        ])
        st.dataframe(params, use_container_width=True, hide_index=True)

    # Tab 7: Settings
    with tabs[6]:
        st.subheader("Configuration")
        render_settings_panel()

    # ── Footer ──────────────────────────────────────────────────────────
    st.divider()
    st.caption(f"Last refreshed: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} | "
               "Data auto-refreshes every 30-60 seconds")

    # Auto-refresh
    if st.sidebar.checkbox("Auto-refresh (30s)", value=False):
        st.cache_data.clear()
        import time
        time.sleep(30)
        st.rerun()

    # Sidebar manual refresh
    if st.sidebar.button("Refresh Now"):
        st.cache_data.clear()
        st.rerun()


if __name__ == "__main__":
    main()
