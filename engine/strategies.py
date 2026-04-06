"""Raw signal and modifier generation for autonomous paper trading system.

This module generates raw, unweighted signals and modifiers. All orchestration
(regime weighting, modifier application, threshold gating) belongs in
engine/combiner.py.

Category 1 — Sentiment-Reactive (highest edge):
  - sentiment_price_divergence: Detects gaps between sentiment and price action.
  - multi_source_consensus: Fires when 3+ articles from 2+ sources all agree.
  - sentiment_momentum: Fires when sentiment shifts > 0.4 between cycles.

Category 3 — News-Catalyst Momentum (post-news drift):
  - news_catalyst_drift: BUY/SELL when a gap is sustained near the day's extreme.

Category 2 — Technical Confirmation (modifiers, not standalone signals):
  - volume_confirmation: Scales confidence by volume relative to average.
  - vwap_position: Directional modifier based on price vs VWAP.
  - relative_strength: Modifier based on ticker performance vs SPY.

Standalone Technical Strategies:
  - momentum: Trend-following via MA relationships.
  - mean_reversion: Counter-trend via RSI levels.

Category 1/3/standalone return: {"signal", "confidence", "strategy", "reason"}
Category 2 modifiers return: {"multiplier"|"directional_modifier", "modifier_name", "reason"}
"""

import logging
from typing import Dict, List, Any, Optional

from db.client import get_previous_sentiment

logger = logging.getLogger(__name__)


def run_all_strategies(
    ticker: str,
    market_data: Dict[str, Any],
    sentiment_data: Optional[Dict[str, Any]] = None,
    macro_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate raw signals and modifiers for a single ticker.

    Returns raw, unweighted output. All orchestration (regime weighting,
    modifier application, signal combination, threshold gating) belongs
    in engine/combiner.py.

    Args:
        ticker: Stock ticker symbol
        market_data: From fetchers/market.py per-ticker data
        sentiment_data: From engine/sentiment.py get_ticker_sentiment_scores()
        macro_data: From fetchers/market.py macro indicators (spy_change_pct, etc.)

    Returns:
        Dict with:
            signals: List of raw signal dicts (non-HOLD only), each with
                     signal, confidence, strategy, reason.
            modifiers: List of modifier dicts, each with
                       multiplier or directional_modifier, modifier_name, reason.
    """
    signals = []

    # Category 1 + Cat 3 + standalone: Signal generators
    signal_fns = [
        lambda: sentiment_price_divergence(ticker, market_data, sentiment_data),
        lambda: multi_source_consensus(ticker, sentiment_data),
        lambda: sentiment_momentum(ticker, sentiment_data),
        lambda: news_catalyst_drift(ticker, market_data, sentiment_data),
        lambda: momentum_signal(ticker, market_data),
        lambda: mean_reversion_signal(ticker, market_data),
    ]

    for fn in signal_fns:
        try:
            result = fn()
            if result and result.get("signal") != "HOLD":
                signals.append(result)
        except Exception as e:
            logger.error(f"Strategy failed for {ticker}: {e}")

    # Category 2: Confirmation modifiers (raw, no regime scaling)
    modifiers = []
    modifier_fns = [
        ("volume_confirmation", lambda: volume_confirmation(market_data)),
        ("vwap_position", lambda: vwap_position(market_data)),
        ("relative_strength", lambda: relative_strength(market_data, macro_data)),
    ]

    for mod_name, fn in modifier_fns:
        try:
            mod = fn()
            if mod:
                mod["modifier_name"] = mod_name
                modifiers.append(mod)
        except Exception as e:
            logger.error(f"Modifier failed for {ticker}: {e}")

    return {"signals": signals, "modifiers": modifiers}


# ---------------------------------------------------------------------------
# Enrichment boost: urgency, materiality, time_horizon
# ---------------------------------------------------------------------------

# Urgency: breaking news demands faster action and carries more edge
_URGENCY_CONFIDENCE_BOOST = {
    "breaking": 1.25,
    "developing": 1.1,
    "standard": 1.0,
}

# Materiality: high-materiality events move prices more and for longer
_MATERIALITY_CONFIDENCE_BOOST = {
    "high": 1.2,
    "medium": 1.1,
    "low": 1.0,
    "unknown": 1.0,
}

# Time horizon: intraday catalysts are most actionable on a 15-min loop
_TIME_HORIZON_CONFIDENCE_BOOST = {
    "intraday": 1.15,
    "short_term": 1.05,
    "medium_term": 1.0,
    "long_term": 0.9,
}


def _apply_enrichment_boost(
    confidence: float,
    sentiment_data: Dict[str, Any],
) -> tuple:
    """Apply urgency, materiality, and time horizon boosts to confidence.

    Returns (boosted_confidence, list_of_notes) so strategies can log
    which enrichment factors contributed.
    """
    notes = []

    urgency = sentiment_data.get("urgency", "standard")
    urgency_mult = _URGENCY_CONFIDENCE_BOOST.get(urgency, 1.0)
    if urgency_mult != 1.0:
        confidence *= urgency_mult
        notes.append(f"urgency={urgency} ({urgency_mult}x)")

    materiality = sentiment_data.get("materiality", "unknown")
    mat_mult = _MATERIALITY_CONFIDENCE_BOOST.get(materiality, 1.0)
    if mat_mult != 1.0:
        confidence *= mat_mult
        notes.append(f"materiality={materiality} ({mat_mult}x)")

    time_horizon = sentiment_data.get("time_horizon", "medium_term")
    th_mult = _TIME_HORIZON_CONFIDENCE_BOOST.get(time_horizon, 1.0)
    if th_mult != 1.0:
        confidence *= th_mult
        notes.append(f"time_horizon={time_horizon} ({th_mult}x)")

    return confidence, notes


# ---------------------------------------------------------------------------
# Category 1: Sentiment-Reactive Strategies
# ---------------------------------------------------------------------------

def sentiment_price_divergence(
    ticker: str,
    market_data: Dict[str, Any],
    sentiment_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Detect divergence between news sentiment and price action.

    The alpha: if Claude scores sentiment strongly but price hasn't moved,
    the market hasn't priced in the news yet. Institutional desks process
    news in waves — the first algo wave catches obvious headlines, the second
    wave (30min–4hr later) catches nuanced full-text sentiment. Our Claude
    pipeline operates in that gap.

    BUY:  sentiment > +0.5  AND  price_change < +0.5%
    SELL: sentiment < -0.5  AND  price_change > -0.5%
    confidence = abs(sentiment) * (1 - abs(price_change_pct) / 5)
    """
    hold = {"signal": "HOLD", "confidence": 0.0, "strategy": "sentiment_divergence", "reason": ""}

    if not sentiment_data or sentiment_data.get("article_count", 0) == 0:
        return hold

    sentiment_score = sentiment_data.get("sentiment_score", 0.0)
    price_change_pct = market_data.get("price_change_pct", 0.0)

    # Need meaningful sentiment to act on
    if abs(sentiment_score) < 0.5:
        return hold

    # Confidence: stronger sentiment + less price movement = higher conviction
    confidence = abs(sentiment_score) * (1 - min(abs(price_change_pct) / 5.0, 1.0))
    confidence = max(0.1, min(0.95, confidence))

    # Boost confidence when multiple articles/sources agree
    article_count = sentiment_data.get("article_count", 1)
    source_count = len(sentiment_data.get("source_breakdown", {}))
    if article_count >= 3:
        confidence = min(0.95, confidence * 1.1)
    if source_count >= 2:
        confidence = min(0.95, confidence * 1.05)

    # Enrichment boost: urgency, materiality, time horizon
    confidence, enrich_notes = _apply_enrichment_boost(confidence, sentiment_data)

    confidence = round(max(0.1, min(0.95, confidence)), 3)

    enrich_tag = f" [{', '.join(enrich_notes)}]" if enrich_notes else ""

    # BUY: bullish sentiment, price hasn't moved up yet
    if sentiment_score > 0.5 and price_change_pct < 0.5:
        return {
            "signal": "BUY",
            "confidence": confidence,
            "strategy": "sentiment_divergence",
            "reason": (
                f"Bullish sentiment ({sentiment_score:+.2f}) but price flat/down "
                f"({price_change_pct:+.1f}%) — market hasn't priced in the news "
                f"({article_count} articles, {source_count} sources){enrich_tag}"
            ),
        }

    # SELL: bearish sentiment, price hasn't dropped yet
    if sentiment_score < -0.5 and price_change_pct > -0.5:
        return {
            "signal": "SELL",
            "confidence": confidence,
            "strategy": "sentiment_divergence",
            "reason": (
                f"Bearish sentiment ({sentiment_score:+.2f}) but price flat/up "
                f"({price_change_pct:+.1f}%) — market hasn't priced in the news "
                f"({article_count} articles, {source_count} sources){enrich_tag}"
            ),
        }

    return hold


def multi_source_consensus(
    ticker: str,
    sentiment_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fire when multiple sources agree on sentiment direction.

    Single-source sentiment is noise. Multi-source consensus is signal.
    Requires 3+ articles from 2+ different sources with all individual
    scores agreeing in direction (all > +0.3 or all < -0.3).

    BUY:  3+ articles, 2+ sources, ALL individual scores > +0.3
    SELL: 3+ articles, 2+ sources, ALL individual scores < -0.3
    confidence = min(article_count / 5, 1.0) * avg_sentiment_magnitude
    """
    hold = {"signal": "HOLD", "confidence": 0.0, "strategy": "multi_source_consensus", "reason": ""}

    if not sentiment_data:
        return hold

    article_count = sentiment_data.get("article_count", 0)
    source_breakdown = sentiment_data.get("source_breakdown", {})
    individual_scores = sentiment_data.get("individual_scores", [])

    if article_count < 3 or len(source_breakdown) < 2:
        reason = f"Insufficient coverage ({article_count} articles, {len(source_breakdown)} sources)"
        return {**hold, "reason": reason}

    all_bullish = all(s > 0.3 for s in individual_scores)
    all_bearish = all(s < -0.3 for s in individual_scores)

    if not all_bullish and not all_bearish:
        return {**hold, "reason": "Mixed signals across sources — no consensus"}

    avg_magnitude = sum(abs(s) for s in individual_scores) / len(individual_scores)
    confidence = min(article_count / 5.0, 1.0) * avg_magnitude

    # Enrichment boost: urgency, materiality, time horizon
    confidence, enrich_notes = _apply_enrichment_boost(confidence, sentiment_data)

    confidence = round(max(0.1, min(0.95, confidence)), 3)

    source_list = ", ".join(sorted(source_breakdown.keys()))
    enrich_tag = f" [{', '.join(enrich_notes)}]" if enrich_notes else ""

    if all_bullish:
        return {
            "signal": "BUY",
            "confidence": confidence,
            "strategy": "multi_source_consensus",
            "reason": (
                f"Consensus bullish: {article_count} articles from {len(source_breakdown)} sources "
                f"({source_list}) all score > +0.3{enrich_tag}"
            ),
        }

    return {
        "signal": "SELL",
        "confidence": confidence,
        "strategy": "multi_source_consensus",
        "reason": (
            f"Consensus bearish: {article_count} articles from {len(source_breakdown)} sources "
            f"({source_list}) all score < -0.3{enrich_tag}"
        ),
    }


def sentiment_momentum(
    ticker: str,
    sentiment_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Detect rapid sentiment shifts between cycles.

    Catches the inflection point when a narrative flips — e.g. a company
    getting negative press starts receiving positive coverage. On a 15-minute
    loop, a shift of >0.4 means the story is changing fast.

    Compares current cycle sentiment against the previous cycle's score
    stored in the sentiment_history table.

    BUY:  sentiment improved by > +0.4 since last cycle
    SELL: sentiment dropped by > -0.4 since last cycle
    confidence = min(abs(delta) / 1.0, 0.95)
    """
    hold = {"signal": "HOLD", "confidence": 0.0, "strategy": "sentiment_momentum", "reason": ""}

    if not sentiment_data or sentiment_data.get("article_count", 0) == 0:
        return hold

    current_score = sentiment_data.get("sentiment_score", 0.0)

    try:
        previous = get_previous_sentiment(ticker)
    except Exception as e:
        logger.error(f"Failed to fetch previous sentiment for {ticker}: {e}")
        return hold

    if previous is None:
        return {**hold, "reason": "No prior cycle sentiment — first observation"}

    previous_score = previous["sentiment_score"]
    delta = current_score - previous_score

    if abs(delta) < 0.4:
        return {**hold, "reason": f"Sentiment stable (delta {delta:+.2f})"}

    confidence = min(abs(delta) / 1.0, 0.95)

    # Enrichment boost: urgency, materiality, time horizon
    confidence, enrich_notes = _apply_enrichment_boost(confidence, sentiment_data)

    confidence = round(max(0.1, min(0.95, confidence)), 3)

    enrich_tag = f" [{', '.join(enrich_notes)}]" if enrich_notes else ""

    if delta > 0.4:
        return {
            "signal": "BUY",
            "confidence": confidence,
            "strategy": "sentiment_momentum",
            "reason": (
                f"Sentiment shifting bullish: {previous_score:+.2f} → {current_score:+.2f} "
                f"(delta {delta:+.2f}){enrich_tag}"
            ),
        }

    return {
        "signal": "SELL",
        "confidence": confidence,
        "strategy": "sentiment_momentum",
        "reason": (
            f"Sentiment shifting bearish: {previous_score:+.2f} → {current_score:+.2f} "
            f"(delta {delta:+.2f}){enrich_tag}"
        ),
    }


# ---------------------------------------------------------------------------
# Category 3: News-Catalyst Momentum (Post-News Drift)
# ---------------------------------------------------------------------------

def news_catalyst_drift(
    ticker: str,
    market_data: Dict[str, Any],
    sentiment_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Trade the post-news drift: stocks continue moving in the direction
    of their initial reaction to news for 1-5 days.

    If a stock gapped up >2% today and is still near the day's high, the
    drift hasn't exhausted and momentum is sustained. If the gap has reversed
    significantly, mean reversion is taking over — stay out.

    BUY:  gapped up > 2% AND price within 1% of day high
    SELL: gapped down > 2% AND price within 1% of day low
    HOLD: gap reversed or insufficient data

    Confidence is higher when:
    - The gap is larger (stronger catalyst)
    - Price is closer to the extreme (drift intact)
    - Urgency/materiality are high (enrichment boost)
    """
    hold = {"signal": "HOLD", "confidence": 0.0, "strategy": "news_catalyst_drift", "reason": ""}

    price = market_data.get("price")
    prev_close = market_data.get("prev_close")
    day_high = market_data.get("day_high")
    day_low = market_data.get("day_low")

    if not all([price, prev_close, day_high, day_low]) or prev_close == 0:
        return {**hold, "reason": "Insufficient price data for gap analysis"}

    gap_pct = (price - prev_close) / prev_close * 100

    if abs(gap_pct) < 2.0:
        return {**hold, "reason": f"No significant gap ({gap_pct:+.1f}%)"}

    if gap_pct > 2.0:
        # Bullish gap — check if price is still near the high
        if day_high == 0:
            return hold
        distance_from_high_pct = (day_high - price) / day_high * 100

        if distance_from_high_pct > 1.0:
            return {**hold, "reason": f"Gap up {gap_pct:+.1f}% but faded {distance_from_high_pct:.1f}% from high"}

        # Confidence: larger gap + closer to high = stronger
        gap_factor = min(gap_pct / 5.0, 1.0)  # 5%+ gap = max gap factor
        proximity_factor = 1.0 - distance_from_high_pct  # closer to high = higher
        confidence = max(0.3, min(0.85, gap_factor * 0.6 + proximity_factor * 0.3))

        enrich_tag = ""
        if sentiment_data:
            confidence, enrich_notes = _apply_enrichment_boost(confidence, sentiment_data)
            enrich_tag = f" [{', '.join(enrich_notes)}]" if enrich_notes else ""

        confidence = round(max(0.3, min(0.95, confidence)), 3)

        return {
            "signal": "BUY",
            "confidence": confidence,
            "strategy": "news_catalyst_drift",
            "reason": (
                f"Post-news drift: gapped up {gap_pct:+.1f}%, "
                f"holding {distance_from_high_pct:.1f}% from day high{enrich_tag}"
            ),
        }

    # gap_pct < -2.0: bearish gap
    if day_low == 0:
        return hold
    distance_from_low_pct = (price - day_low) / day_low * 100

    if distance_from_low_pct > 1.0:
        return {**hold, "reason": f"Gap down {gap_pct:+.1f}% but bounced {distance_from_low_pct:.1f}% from low"}

    gap_factor = min(abs(gap_pct) / 5.0, 1.0)
    proximity_factor = 1.0 - distance_from_low_pct
    confidence = max(0.3, min(0.85, gap_factor * 0.6 + proximity_factor * 0.3))

    enrich_tag = ""
    if sentiment_data:
        confidence, enrich_notes = _apply_enrichment_boost(confidence, sentiment_data)
        enrich_tag = f" [{', '.join(enrich_notes)}]" if enrich_notes else ""

    confidence = round(max(0.3, min(0.95, confidence)), 3)

    return {
        "signal": "SELL",
        "confidence": confidence,
        "strategy": "news_catalyst_drift",
        "reason": (
            f"Post-news drift: gapped down {gap_pct:+.1f}%, "
            f"holding {distance_from_low_pct:.1f}% from day low{enrich_tag}"
        ),
    }


# ---------------------------------------------------------------------------
# Category 2: Technical Confirmation Modifiers
# ---------------------------------------------------------------------------
# These return multipliers/directional modifiers, NOT standalone signals.
# Applied to all Category 1 signals to adjust confidence.


def volume_confirmation(market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Scale confidence based on volume relative to average.

    High volume confirms conviction — a sentiment signal backed by 2x avg
    volume is institutional money moving. Low volume means the move lacks
    participation and is less trustworthy.

    Returns multiplier:
      volume > 2x avg:   1.4  (strong confirmation)
      volume > 1.5x avg: 1.2  (moderate confirmation)
      volume < 0.7x avg: 0.6  (low conviction — dampens signal)
      normal volume:      1.0  (no adjustment)
    """
    volume = market_data.get("volume")
    avg_volume = market_data.get("avg_volume_20")

    if not volume or not avg_volume or avg_volume == 0:
        return None

    ratio = volume / avg_volume

    if ratio > 2.0:
        return {"multiplier": 1.4, "reason": f"Volume {ratio:.1f}x avg — strong confirmation"}
    if ratio > 1.5:
        return {"multiplier": 1.2, "reason": f"Volume {ratio:.1f}x avg — moderate confirmation"}
    if ratio < 0.7:
        return {"multiplier": 0.6, "reason": f"Volume {ratio:.1f}x avg — low conviction"}

    return {"multiplier": 1.0, "reason": "Normal volume"}


def vwap_position(market_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Directional modifier based on price position relative to VWAP.

    VWAP is the volume-weighted average price for the session. Institutional
    traders benchmark against VWAP — price above VWAP means buyers are in
    control, below means sellers.

    Returns directional_modifier:
      price > VWAP by 1%+:  +0.15  (bullish confirmation)
      price < VWAP by 1%+:  -0.15  (bearish confirmation)
      price near VWAP:       0.0   (neutral, no adjustment)

    Positive modifier boosts BUY confidence and dampens SELL confidence.
    Negative modifier does the opposite.
    """
    price = market_data.get("price")
    vwap = market_data.get("vwap")

    if not price or not vwap or vwap == 0:
        return None

    deviation_pct = (price - vwap) / vwap * 100

    if deviation_pct > 1.0:
        modifier = min(0.2, deviation_pct * 0.05)
        return {"directional_modifier": round(modifier, 3), "reason": f"Price {deviation_pct:+.1f}% above VWAP — bullish positioning"}
    if deviation_pct < -1.0:
        modifier = max(-0.2, deviation_pct * 0.05)
        return {"directional_modifier": round(modifier, 3), "reason": f"Price {deviation_pct:+.1f}% below VWAP — bearish positioning"}

    return {"directional_modifier": 0.0, "reason": "Price near VWAP — neutral"}


def relative_strength(
    market_data: Dict[str, Any],
    macro_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Directional modifier based on ticker performance vs SPY.

    A stock with bullish news that's also outperforming the market is a
    much stronger buy than one with bullish news that's lagging.

    Returns directional_modifier:
      ticker - SPY > +1%:  positive (outperforming, boost buys)
      ticker - SPY < -1%:  negative (underperforming, boost sells)
      within 1%:           0.0 (in line with market)

    Modifier magnitude scales with outperformance: capped at ±0.2.
    """
    if not macro_data:
        return None

    ticker_change = market_data.get("price_change_pct")
    spy_change = macro_data.get("spy_change_pct")

    if ticker_change is None or spy_change is None:
        return None

    spread = ticker_change - spy_change

    if abs(spread) < 1.0:
        return {"directional_modifier": 0.0, "reason": f"In line with market (spread {spread:+.1f}%)"}

    # Scale: 1% spread → 0.05, 4%+ spread → 0.2 (capped)
    modifier = max(-0.2, min(0.2, spread * 0.05))

    if spread > 1.0:
        return {"directional_modifier": round(modifier, 3), "reason": f"Outperforming SPY by {spread:+.1f}%"}
    return {"directional_modifier": round(modifier, 3), "reason": f"Underperforming SPY by {spread:+.1f}%"}


# ---------------------------------------------------------------------------
# Retained standalone technical strategies
# ---------------------------------------------------------------------------

def momentum_signal(ticker: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Trend-following signal using moving average relationships.

    BUY:  price > 20MA > 50MA (confirmed uptrend)
    SELL: price < 20MA < 50MA (confirmed downtrend)
    Confidence scales with distance from MA as % of price.
    """
    price = market_data.get("price")
    ma_20 = market_data.get("ma_20")
    ma_50 = market_data.get("ma_50")

    if not all([price, ma_20, ma_50]):
        return {"signal": "HOLD", "confidence": 0.0, "strategy": "momentum", "reason": "Insufficient MA data"}

    trend_strength = abs(price - ma_20) / price
    confidence = round(max(0.3, min(0.9, 0.3 + trend_strength * 12)), 3)

    if price > ma_20 > ma_50:
        return {
            "signal": "BUY",
            "confidence": confidence,
            "strategy": "momentum",
            "reason": f"Price above 20MA above 50MA, {trend_strength*100:.1f}% trend strength",
        }

    if price < ma_20 < ma_50:
        return {
            "signal": "SELL",
            "confidence": confidence,
            "strategy": "momentum",
            "reason": f"Price below 20MA below 50MA, {trend_strength*100:.1f}% trend strength",
        }

    return {"signal": "HOLD", "confidence": 0.0, "strategy": "momentum", "reason": "MAs not aligned"}


def mean_reversion_signal(ticker: str, market_data: Dict[str, Any]) -> Dict[str, Any]:
    """Counter-trend signal using RSI oversold/overbought levels.

    BUY:  RSI < 30 AND price up today (oversold bounce)
    SELL: RSI > 70 AND price down today (overbought pullback)
    Confidence scales with RSI extremity.
    """
    rsi = market_data.get("rsi")
    price_change_pct = market_data.get("price_change_pct", 0.0)

    if rsi is None:
        return {"signal": "HOLD", "confidence": 0.0, "strategy": "mean_reversion", "reason": "No RSI data"}

    if rsi < 30 and price_change_pct > 0:
        confidence = round(max(0.3, min(0.7, 0.3 + (30 - rsi) / 25)), 3)
        return {
            "signal": "BUY",
            "confidence": confidence,
            "strategy": "mean_reversion",
            "reason": f"RSI oversold at {rsi:.0f} with positive momentum ({price_change_pct:+.1f}%)",
        }

    if rsi > 70 and price_change_pct < 0:
        confidence = round(max(0.3, min(0.7, 0.3 + (rsi - 70) / 25)), 3)
        return {
            "signal": "SELL",
            "confidence": confidence,
            "strategy": "mean_reversion",
            "reason": f"RSI overbought at {rsi:.0f} with negative momentum ({price_change_pct:+.1f}%)",
        }

    return {"signal": "HOLD", "confidence": 0.0, "strategy": "mean_reversion", "reason": f"RSI neutral at {rsi:.0f}"}
