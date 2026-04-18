"""APScheduler event loop for autonomous paper trading system.

Orchestrates the full pipeline on a schedule:
  - Primary job: every 30 minutes during market hours (9:30 AM - 4:00 PM ET)
  - Pre-market job: once at 9:00 AM ET
  - Post-market job: once at 4:30 PM ET
  - Weekend: outcome measurement only
"""

import os
import sys
import time
import uuid
import signal
import logging
import traceback
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
# Suppress noisy "Unclosed client session" errors from libsql's aiohttp usage
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logger = logging.getLogger(__name__)

_scheduler: Optional[BlockingScheduler] = None


# ═══════════════════════════════════════════════════════════════════════════
# Market hours
# ═══════════════════════════════════════════════════════════════════════════

def is_market_open() -> bool:
    """Check if market is open via Alpaca clock API. Defaults to False on error."""
    try:
        from executor.alpaca import is_market_open as alpaca_is_open
        return alpaca_is_open()
    except Exception as e:
        logger.warning(f"Market hours check failed, assuming closed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Circuit breaker
# ═══════════════════════════════════════════════════════════════════════════

def is_circuit_breaker_tripped(db_path: str = "") -> bool:
    """Check circuit breaker status. Defaults to True (halt) on error."""
    try:
        from db.client import is_circuit_breaker_tripped as db_cb_check
        return db_cb_check()
    except Exception as e:
        logger.error(f"Circuit breaker check failed, defaulting to HALT: {e}")
        return True


# ═══════════════════════════════════════════════════════════════════════════
# Primary trading cycle
# ═══════════════════════════════════════════════════════════════════════════

def run_trading_cycle(is_premarket: bool = False) -> Dict[str, Any]:
    """Execute one complete trading cycle.

    Sequence:
      1. Circuit breaker check
      2. Market hours check (skip execution if closed, unless premarket)
      3. Discovery — find active tickers
      4. Fetch news via aggregator
      5. Sentiment analysis (batch)
      6. Fetch market data for discovered tickers
      7. Per-ticker: strategies -> combiner -> risk check -> execute
      8. Log all trades

    Args:
        is_premarket: True for the 9:00 AM pre-market run (sector rotation on).

    Returns:
        Cycle result dict with stats and errors.
    """
    cycle_id = str(uuid.uuid4())[:8]
    cycle_start = time.monotonic()
    phase_timings: Dict[str, int] = {}
    errors: List[str] = []

    result = {
        "success": False,
        "cycle_id": cycle_id,
        "tickers_discovered": 0,
        "signals_generated": 0,
        "trades_executed": 0,
        "execution_time_ms": 0,
        "phase_timings": phase_timings,
        "errors": errors,
        "circuit_breaker_tripped": False,
    }

    logger.info(f"{'='*60}")
    logger.info(f"Cycle {cycle_id} starting ({'pre-market' if is_premarket else 'regular'})")
    logger.info(f"{'='*60}")

    # ── 1. Circuit breaker ─────────────────────────────────────────────
    if is_circuit_breaker_tripped():
        logger.warning(f"Cycle {cycle_id}: Circuit breaker is TRIPPED — skipping trading")
        result["circuit_breaker_tripped"] = True
        # Still run outcome measurement
        _run_outcome_measurement(cycle_id, errors)
        result["execution_time_ms"] = int((time.monotonic() - cycle_start) * 1000)
        return result

    # ── 2. Market hours (allow premarket to proceed regardless) ────────
    market_open = is_market_open()
    if not market_open and not is_premarket:
        logger.info(f"Cycle {cycle_id}: Market is closed — skipping trading cycle")
        result["execution_time_ms"] = int((time.monotonic() - cycle_start) * 1000)
        result["success"] = True
        return result

    # ── 3. Discovery ───────────────────────────────────────────────────
    t0 = time.monotonic()
    try:
        from fetchers.discovery import discover_tickers
        discovery = discover_tickers(is_premarket=is_premarket)
        tickers = discovery.get("tickers", [])
        sources = discovery.get("sources", {})
        mode = discovery.get("mode", "unknown")
        result["tickers_discovered"] = len(tickers)
        logger.info(f"Cycle {cycle_id}: Discovered {len(tickers)} tickers (mode={mode})")
    except Exception as e:
        msg = f"Discovery failed: {e}"
        logger.error(f"Cycle {cycle_id}: {msg}\n{traceback.format_exc()}")
        errors.append(msg)
        result["execution_time_ms"] = int((time.monotonic() - cycle_start) * 1000)
        return result  # Can't continue without tickers
    phase_timings["discovery"] = int((time.monotonic() - t0) * 1000)

    if not tickers:
        logger.info(f"Cycle {cycle_id}: No tickers discovered — nothing to do")
        result["success"] = True
        result["execution_time_ms"] = int((time.monotonic() - cycle_start) * 1000)
        return result

    # ── 4. Fetch news ──────────────────────────────────────────────────
    t0 = time.monotonic()
    articles = []
    try:
        from fetchers.aggregator import fetch_all_news
        articles = fetch_all_news(
            discovery_context=discovery,
            watchlist=tickers,
        )
        logger.info(f"Cycle {cycle_id}: Fetched {len(articles)} articles")
    except Exception as e:
        msg = f"News fetch failed: {e}"
        logger.error(f"Cycle {cycle_id}: {msg}\n{traceback.format_exc()}")
        errors.append(msg)
        # Continue — we can still run technical strategies without news
    phase_timings["news_fetch"] = int((time.monotonic() - t0) * 1000)

    # ── 4b. Deduplicate articles seen in previous cycles ───────────────
    if articles:
        try:
            from db.client import filter_unseen_articles, mark_articles_seen, cleanup_seen_articles
            unseen = filter_unseen_articles(articles, ttl_hours=8)
            skipped = len(articles) - len(unseen)
            if skipped:
                logger.info(f"Cycle {cycle_id}: Skipping {skipped} already-seen articles, {len(unseen)} new")
            mark_articles_seen(unseen)
            cleanup_seen_articles(max_age_hours=24)
            articles = unseen
        except Exception as e:
            logger.warning(f"Cycle {cycle_id}: Article dedup failed, processing all articles: {e}")

    # ── 5. Sentiment analysis ──────────────────────────────────────────
    t0 = time.monotonic()
    sentiment_results = []
    ticker_sentiments: Dict[str, Dict] = {}
    try:
        # TODO: Replace with thesis-driven analysis.py
        # from engine.sentiment import batch_analyze_articles, batch_record_sentiments
        pass
        if articles:
            sentiment_results = batch_analyze_articles(articles)

            # Promote tickers inferred by sector/macro Claude analysis that
            # weren't found by discovery (e.g. oil crash → DAL, UAL).
            # Must happen before market data fetch so they get price data too.
            sector_additions = _collect_sector_macro_tickers(
                sentiment_results, tickers, cycle_id
            )
            if sector_additions:
                tickers = tickers + sector_additions
                sources.update({t: ["sector_macro"] for t in sector_additions})
                result["tickers_discovered"] = len(tickers)
                logger.info(
                    f"Cycle {cycle_id}: Promoted {len(sector_additions)} sector_macro tickers "
                    f"into active set: {sector_additions}"
                )

            ticker_sentiments = batch_record_sentiments(tickers, sentiment_results)
            logger.info(
                f"Cycle {cycle_id}: Sentiment analysis produced "
                f"{len(sentiment_results)} results for {len(ticker_sentiments)} tickers"
            )
    except Exception as e:
        msg = f"Sentiment analysis failed: {e}"
        logger.error(f"Cycle {cycle_id}: {msg}\n{traceback.format_exc()}")
        errors.append(msg)
    phase_timings["sentiment"] = int((time.monotonic() - t0) * 1000)

    # ── 6. Market data ─────────────────────────────────────────────────
    # tickers may have grown via sector_macro — fetch covers all of them.
    t0 = time.monotonic()
    market_data: Dict[str, Any] = {"tickers": {}, "macro": {}}
    try:
        from fetchers.market import fetch_market_data
        market_data = fetch_market_data(
            tickers,
            include_sector_etfs=is_premarket,
        )
        logger.info(
            f"Cycle {cycle_id}: Market data for {len(market_data.get('tickers', {}))} tickers"
        )
    except Exception as e:
        msg = f"Market data fetch failed: {e}"
        logger.error(f"Cycle {cycle_id}: {msg}\n{traceback.format_exc()}")
        errors.append(msg)
    phase_timings["market_data"] = int((time.monotonic() - t0) * 1000)

    # ── 7. Regime classification ───────────────────────────────────────
    t0 = time.monotonic()
    regime_data = {"regime": "neutral", "confidence": 0.0}
    try:
        from engine.regime import get_current_regime
        macro_news_score = _aggregate_macro_sentiment(ticker_sentiments)
        regime_data = get_current_regime(
            market_data.get("macro", {}),
            macro_news_score=macro_news_score,
        )
        logger.info(f"Cycle {cycle_id}: Regime = {regime_data.get('regime')} "
                     f"(confidence={regime_data.get('confidence', 0):.2f})")
    except Exception as e:
        msg = f"Regime classification failed: {e}"
        logger.error(f"Cycle {cycle_id}: {msg}")
        errors.append(msg)
    phase_timings["regime"] = int((time.monotonic() - t0) * 1000)

    # ── 8. Load learned weights ────────────────────────────────────────
    learned_weights: Dict[str, float] = {}
    try:
        # TODO: Replace with thesis-aware weight loader
        # from engine.combiner import load_learned_weights
        pass
        learned_weights = load_learned_weights()
    except Exception as e:
        logger.warning(f"Could not load learned weights: {e}")

    # ── 9. Per-ticker signal pipeline ──────────────────────────────────
    t0 = time.monotonic()
    signals_generated = 0
    trades_executed = 0
    portfolio = None

    # Pre-fetch portfolio once
    try:
        from executor.alpaca import get_portfolio
        portfolio = get_portfolio()
    except Exception as e:
        msg = f"Portfolio fetch failed: {e}"
        logger.error(f"Cycle {cycle_id}: {msg}")
        errors.append(msg)
        result["execution_time_ms"] = int((time.monotonic() - cycle_start) * 1000)
        return result  # Can't do risk checks without portfolio state

    for ticker in tickers:
        try:
            ticker_market = market_data.get("tickers", {}).get(ticker, {})
            ticker_sentiment = ticker_sentiments.get(ticker)

            # Skip tickers with no market data
            if not ticker_market or not ticker_market.get("price"):
                logger.debug(f"Cycle {cycle_id}: Skipping {ticker} — no market data")
                continue

            # Run strategies
            # TODO: Replace with thesis lifecycle strategies
            # from engine.strategies import run_all_strategies
            pass
            raw_output = run_all_strategies(
                ticker=ticker,
                market_data=ticker_market,
                sentiment_data=ticker_sentiment,
                macro_data=market_data.get("macro"),
            )

            raw_signals = raw_output.get("signals", [])
            if not raw_signals:
                continue

            # Combine signals
            # TODO: Replace with thesis-first combiner
            # from engine.combiner import combine_ticker_signals
            pass
            combined = combine_ticker_signals(
                ticker=ticker,
                raw_output=raw_output,
                regime_data=regime_data,
                learned_weights=learned_weights,
            )

            if combined.get("signal") == "HOLD":
                continue

            signals_generated += 1
            logger.info(
                f"Cycle {cycle_id}: Signal for {ticker}: "
                f"{combined['signal']} (confidence={combined.get('confidence', 0):.3f})"
            )

            # Don't execute trades if market is closed (premarket = analysis only)
            if not market_open:
                logger.info(f"Cycle {cycle_id}: Market closed — signal logged but not executed for {ticker}")
                continue

            # Risk check
            from risk.manager import check_trade
            risk_result = check_trade(
                signal=combined,
                portfolio=portfolio,
                market_data=ticker_market,
            )

            if not risk_result.get("approved"):
                logger.info(
                    f"Cycle {cycle_id}: Risk rejected {ticker}: {risk_result.get('reason')}"
                )
                continue

            # Execute trade
            from executor.alpaca import place_order
            order_result = place_order(
                ticker=ticker,
                qty=risk_result["shares"],
                side=combined["signal"].lower(),
            )

            if "error" in order_result:
                msg = f"Order failed for {ticker}: {order_result['error']}"
                logger.error(f"Cycle {cycle_id}: {msg}")
                errors.append(msg)
                continue

            trades_executed += 1
            logger.info(
                f"Cycle {cycle_id}: Executed {combined['signal']} "
                f"{risk_result['shares']} {ticker} — order_id={order_result.get('order_id')}"
            )

            # Log trade
            try:
                from feedback.logger import log_trade
                log_trade({
                    "ticker": ticker,
                    "signal": combined["signal"],
                    "confidence": combined.get("confidence", 0),
                    "sentiment_score": (ticker_sentiment or {}).get("sentiment_score"),
                    "sentiment_source": _primary_sentiment_source(ticker_sentiment),
                    "strategies_fired": list(combined.get("components", {}).keys()),
                    "discovery_sources": sources.get(ticker, []),
                    "regime_mode": regime_data.get("regime"),
                    "article_urls": [],
                    "entry_price": risk_result["entry_price"],
                    "shares": risk_result["shares"],
                    "stop_loss_price": risk_result["stop_loss"],
                    "take_profit_price": risk_result["take_profit"],
                    "order_id": order_result.get("order_id"),
                })
            except Exception as e:
                logger.error(f"Cycle {cycle_id}: Trade logging failed for {ticker}: {e}")

            # Refresh portfolio after each trade so risk checks stay accurate
            try:
                portfolio = get_portfolio()
            except Exception:
                pass

        except Exception as e:
            msg = f"Pipeline failed for {ticker}: {e}"
            logger.error(f"Cycle {cycle_id}: {msg}\n{traceback.format_exc()}")
            errors.append(msg)

    phase_timings["signal_pipeline"] = int((time.monotonic() - t0) * 1000)
    result["signals_generated"] = signals_generated
    result["trades_executed"] = trades_executed

    # ── 10. Done ───────────────────────────────────────────────────────
    elapsed_ms = int((time.monotonic() - cycle_start) * 1000)
    result["execution_time_ms"] = elapsed_ms
    result["success"] = True

    logger.info(
        f"Cycle {cycle_id} complete: "
        f"{len(tickers)} tickers, {signals_generated} signals, "
        f"{trades_executed} trades, {len(errors)} errors, {elapsed_ms}ms"
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Post-market job
# ═══════════════════════════════════════════════════════════════════════════

def run_post_market() -> None:
    """Post-market job: measure outcomes + feedback loop."""
    logger.info("Running post-market analysis")
    errors: List[str] = []

    _run_outcome_measurement("post_market", errors)

    if errors:
        logger.warning(f"Post-market completed with errors: {errors}")
    else:
        logger.info("Post-market analysis complete")


# ═══════════════════════════════════════════════════════════════════════════
# Outcome measurement (reusable)
# ═══════════════════════════════════════════════════════════════════════════

def _run_outcome_measurement(context: str, errors: List[str]) -> None:
    """Run outcome measurement and weight updates."""
    try:
        from feedback.outcomes import measure_outcomes
        outcomes = measure_outcomes()
        logger.info(f"[{context}] Measured {len(outcomes)} outcomes")
    except Exception as e:
        msg = f"Outcome measurement failed: {e}"
        logger.error(f"[{context}] {msg}\n{traceback.format_exc()}")
        errors.append(msg)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _collect_sector_macro_tickers(
    sentiment_results: List[Dict],
    existing_tickers: List[str],
    cycle_id: str,
) -> List[str]:
    """Return validated tickers inferred by sector/macro Claude analysis.

    These come from ticker-less articles where Claude identified second-order
    trades (e.g. oil crash → buy DAL, UAL). Validates price >= $5,
    market_cap >= $1B, avg_volume >= 500K before adding to the active set.
    """
    import yfinance as yf

    existing = {t.upper() for t in existing_tickers}
    candidates = {
        r["ticker"].upper()
        for r in sentiment_results
        if r.get("source") == "sector_macro" and r.get("ticker")
    } - existing

    if not candidates:
        return []

    logger.info(
        f"Cycle {cycle_id}: Validating {len(candidates)} sector_macro candidates: "
        f"{sorted(candidates)}"
    )

    validated = []
    for ticker in sorted(candidates):
        try:
            info = yf.Ticker(ticker).info
            price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
            mkt_cap = info.get("marketCap") or 0
            avg_vol = info.get("averageVolume") or 0

            if price >= 5 and mkt_cap >= 1_000_000_000 and avg_vol >= 500_000:
                validated.append(ticker)
                logger.info(
                    f"Cycle {cycle_id}: sector_macro {ticker} validated — "
                    f"price={price:.2f}, mkt_cap={mkt_cap/1e9:.1f}B, vol={avg_vol:,}"
                )
            else:
                logger.debug(
                    f"Cycle {cycle_id}: sector_macro {ticker} rejected — "
                    f"price={price}, mkt_cap={mkt_cap}, vol={avg_vol}"
                )
        except Exception as e:
            logger.debug(f"Cycle {cycle_id}: Could not validate sector_macro ticker {ticker}: {e}")

    return validated


def _aggregate_macro_sentiment(ticker_sentiments: Dict[str, Dict]) -> float:
    """Average all ticker sentiments as a rough macro sentiment proxy."""
    scores = [
        s.get("sentiment_score", 0)
        for s in ticker_sentiments.values()
        if s.get("article_count", 0) > 0
    ]
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _primary_sentiment_source(sentiment_data: Optional[Dict]) -> Optional[str]:
    """Pick the dominant sentiment source from source_breakdown."""
    if not sentiment_data:
        return None
    breakdown = sentiment_data.get("source_breakdown", {})
    if not breakdown:
        return None
    return max(breakdown, key=breakdown.get)


# ═══════════════════════════════════════════════════════════════════════════
# Scheduler setup
# ═══════════════════════════════════════════════════════════════════════════

def run_scheduler() -> None:
    """Start the APScheduler event loop.

    Jobs:
      - Primary: every 30 min during market hours (9:30-16:00 ET, Mon-Fri)
      - Pre-market: 9:00 AM ET, Mon-Fri
      - Post-market: 4:30 PM ET, Mon-Fri
      - Weekend outcome check: Saturday 10:00 AM ET
    """
    global _scheduler

    logger.info("="*60)
    logger.info("AI Paper Trader - Starting Scheduler")
    logger.info("="*60)

    # Validate required env vars
    required = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "TURSO_CONNECTION_URL", "TURSO_AUTH_TOKEN"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        logger.error(f"Missing required environment variables: {missing}")
        sys.exit(1)

    # Initialize DB weights if needed
    try:
        from db.client import initialize_default_weights
        initialize_default_weights()
    except Exception as e:
        logger.warning(f"Could not initialize default weights: {e}")

    _scheduler = BlockingScheduler(timezone="US/Eastern")

    # Primary trading cycle — every 30 minutes during market hours
    _scheduler.add_job(
        func=_safe_run_trading_cycle,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour="9-15",
            minute="0,30",
            timezone="US/Eastern",
        ),
        id="primary_cycle",
        name="Primary Trading Cycle (30min)",
        max_instances=1,
        misfire_grace_time=300,
    )

    # Pre-market — 9:00 AM ET weekdays
    _scheduler.add_job(
        func=_safe_run_premarket,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=9,
            minute=0,
            timezone="US/Eastern",
        ),
        id="premarket",
        name="Pre-Market Analysis",
        max_instances=1,
        misfire_grace_time=600,
    )

    # Post-market — 4:30 PM ET weekdays
    _scheduler.add_job(
        func=run_post_market,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=16,
            minute=30,
            timezone="US/Eastern",
        ),
        id="postmarket",
        name="Post-Market Analysis",
        max_instances=1,
        misfire_grace_time=600,
    )

    # Weekend outcome measurement — Saturday 10:00 AM ET
    _scheduler.add_job(
        func=_safe_run_weekend,
        trigger=CronTrigger(
            day_of_week="sat",
            hour=10,
            minute=0,
            timezone="US/Eastern",
        ),
        id="weekend_outcomes",
        name="Weekend Outcome Measurement",
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Graceful shutdown
    def _shutdown(signum, frame):
        logger.info(f"Received signal {signum} — shutting down scheduler")
        if _scheduler and _scheduler.running:
            _scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Log schedule (next_run_time is only available after scheduler starts,
    # so we just log job names here)
    logger.info("Scheduled jobs:")
    for job in _scheduler.get_jobs():
        logger.info(f"  - {job.name} (id={job.id})")

    logger.info("Scheduler running. Press Ctrl+C to stop.")

    try:
        _scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


# ── Safe wrappers (prevent scheduler crash on unhandled exceptions) ─────

def _safe_run_trading_cycle():
    """Wrapper for regular trading cycle with error isolation."""
    try:
        run_trading_cycle(is_premarket=False)
    except Exception as e:
        logger.error(f"Unhandled error in trading cycle: {e}\n{traceback.format_exc()}")


def _safe_run_premarket():
    """Wrapper for pre-market cycle."""
    try:
        run_trading_cycle(is_premarket=True)
    except Exception as e:
        logger.error(f"Unhandled error in pre-market cycle: {e}\n{traceback.format_exc()}")


def _safe_run_weekend():
    """Wrapper for weekend outcome measurement."""
    try:
        errors: List[str] = []
        _run_outcome_measurement("weekend", errors)
    except Exception as e:
        logger.error(f"Unhandled error in weekend job: {e}\n{traceback.format_exc()}")


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_scheduler()
