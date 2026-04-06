"""4-stage signal combiner for autonomous paper trading system.

Orchestrates the full signal pipeline:

  Stage 1 — Primary Direction: pick dominant direction from Category 1
            (sentiment-reactive) and standalone technical signals, weighted
            by learned DB weights. Conflict between BUY and SELL signals
            applies a confidence penalty.

  Stage 2 — Modifier Application: apply Category 2 technical confirmation
            modifiers (volume, VWAP, relative strength) to adjust confidence.
            No regime scaling — modifiers are independent of regime.

  Stage 3 — Catalyst Drift Integration: if Category 3 news-catalyst drift
            signal exists, blend it in. Agreement boosts confidence,
            disagreement dampens it.

  Stage 4 — Regime Filter: apply macro regime adjustments. Risk-on dampens
            SELL. Risk-off dampens BUY and kills weak BUY signals.

  Gate   — Only signals with confidence > 0.55 pass to the risk manager.

This separation fixes the multiplicative stacking bug where regime weights
(e.g. 2x) were multiplied by modifier effects (e.g. 1.4x) = 2.8x total.
Now regime and modifiers are applied to the base confidence independently.
"""

import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

# Signal decision thresholds
BUY_THRESHOLD = 0.55
SELL_THRESHOLD = 0.55  # symmetric: any signal below this becomes HOLD

# Default learned weight for strategies not yet in the DB
DEFAULT_WEIGHT = 0.5

# Strategy categorization
_CAT1_STRATEGIES = {"sentiment_divergence", "multi_source_consensus", "sentiment_momentum"}
_CAT3_STRATEGIES = {"news_catalyst_drift"}
_STANDALONE_STRATEGIES = {"momentum", "mean_reversion"}

# Regime filter constants
_RISK_ON_SELL_DAMPEN = 0.8    # SELL confidence *= 0.8 in risk-on
_RISK_OFF_BUY_DAMPEN = 0.7    # BUY confidence *= 0.7 in risk-off
_RISK_OFF_BUY_GATE = 0.8      # kill BUY if confidence < 0.8 after dampening

# Cat 3 integration weights
_CAT3_CONFIRM_WEIGHT = 0.2    # boost when drift confirms direction
_CAT3_CONTRADICT_WEIGHT = 0.15  # penalty when drift contradicts

# Conflict penalty scaling
_CONFLICT_PENALTY_SCALE = 0.3


def combine_ticker_signals(
    ticker: str,
    raw_output: Dict[str, Any],
    regime_data: Optional[Dict[str, Any]] = None,
    learned_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Run the 4-stage pipeline and produce a final trading signal.

    Args:
        ticker: Stock ticker symbol.
        raw_output: From strategies.run_all_strategies() —
                    {"signals": [...], "modifiers": [...]}.
        regime_data: From regime.get_current_regime() —
                     {"regime": "risk_on"|"risk_off"|"neutral", ...}.
        learned_weights: From DB weights table — maps strategy name to
                         learned weight (0.1–1.0). None = use defaults.

    Returns:
        Final signal dict with ticker, signal, confidence, components,
        modifiers_applied, cat3_effect, regime, regime_adjustment, rationale.
    """
    regime = (regime_data or {}).get("regime", "neutral")
    weights = learned_weights or {}
    signals = raw_output.get("signals", [])
    modifiers = raw_output.get("modifiers", [])

    # Categorize signals
    cat1_standalone = []
    cat3_signals = []
    for sig in signals:
        strategy = sig.get("strategy", "")
        if strategy in _CAT3_STRATEGIES:
            cat3_signals.append(sig)
        else:
            cat1_standalone.append(sig)

    # --- Stage 1: Primary Direction ---
    stage1 = _stage1_primary_direction(cat1_standalone, weights)
    if stage1["signal"] == "HOLD":
        return _hold_result(ticker, regime, "No directional signals fired")

    direction = stage1["signal"]
    confidence = stage1["confidence"]
    components = stage1["components"]

    # --- Stage 2: Apply Modifiers ---
    stage2_notes = []
    for mod in modifiers:
        if "multiplier" in mod:
            mult = mod["multiplier"]
            if mult != 1.0:
                confidence *= mult
                stage2_notes.append(mod.get("reason", ""))
        if "directional_modifier" in mod:
            dm = mod["directional_modifier"]
            if direction == "BUY":
                confidence *= (1.0 + dm)
            else:
                confidence *= (1.0 - dm)
            if dm != 0.0:
                stage2_notes.append(mod.get("reason", ""))

    # --- Stage 3: Integrate Category 3 ---
    cat3_effect = None
    for cat3 in cat3_signals:
        cat3_conf = cat3.get("confidence", 0.0)
        cat3_dir = cat3.get("signal", "HOLD")
        if cat3_dir == direction:
            boost = cat3_conf * _CAT3_CONFIRM_WEIGHT
            confidence += boost
            cat3_effect = f"+{boost:.3f} (drift confirms {direction})"
        elif cat3_dir != "HOLD":
            penalty = cat3_conf * _CAT3_CONTRADICT_WEIGHT
            confidence -= penalty
            cat3_effect = f"-{penalty:.3f} (drift contradicts {direction})"

    # --- Stage 4: Regime Filter ---
    regime_adj = _stage4_regime_filter(direction, confidence, regime)
    confidence = regime_adj["confidence"]
    regime_note = regime_adj["note"]

    # Risk-off BUY gate: kill weak BUYs
    if regime == "risk_off" and direction == "BUY" and confidence < _RISK_OFF_BUY_GATE:
        logger.info(f"Risk-off gate: killed BUY for {ticker} (confidence {confidence:.3f} < {_RISK_OFF_BUY_GATE})")
        return _hold_result(ticker, regime, f"Risk-off: BUY confidence {confidence:.2f} below {_RISK_OFF_BUY_GATE} gate")

    # Clamp
    confidence = round(max(0.05, min(0.95, confidence)), 3)

    # --- Threshold Gate ---
    if confidence <= BUY_THRESHOLD:
        return _hold_result(ticker, regime, f"Confidence {confidence:.2f} below {BUY_THRESHOLD} threshold")

    # Build rationale
    contributing = [s["strategy"] for s in cat1_standalone if s["signal"] == direction]
    rationale_parts = [" + ".join(contributing)]
    if stage2_notes:
        rationale_parts.append("confirmed by " + ", ".join(stage2_notes))
    if cat3_effect and "confirms" in (cat3_effect or ""):
        rationale_parts.append("drift sustained")
    if regime != "neutral":
        rationale_parts.append(f"{regime} regime")
    rationale = ", ".join(rationale_parts)

    return {
        "ticker": ticker,
        "signal": direction,
        "confidence": confidence,
        "components": components,
        "modifiers_applied": stage2_notes,
        "cat3_effect": cat3_effect,
        "regime": regime,
        "regime_adjustment": regime_note,
        "rationale": rationale,
    }


def _stage1_primary_direction(
    signals: List[Dict[str, Any]],
    weights: Dict[str, float],
) -> Dict[str, Any]:
    """Stage 1: determine primary direction from Cat 1 + standalone signals.

    Groups signals by direction, applies learned weights, picks the dominant
    direction, and computes base confidence with a conflict penalty if both
    BUY and SELL signals exist.

    Returns:
        {"signal": "BUY"|"SELL"|"HOLD", "confidence": float, "components": dict}
    """
    if not signals:
        return {"signal": "HOLD", "confidence": 0.0, "components": {}}

    buys = []
    sells = []
    components = {}

    for sig in signals:
        strategy = sig["strategy"]
        w = weights.get(strategy, DEFAULT_WEIGHT)
        weighted_conf = sig["confidence"] * w
        components[strategy] = {
            "signal": sig["signal"],
            "raw_confidence": sig["confidence"],
            "weight": w,
        }
        if sig["signal"] == "BUY":
            buys.append(weighted_conf)
        elif sig["signal"] == "SELL":
            sells.append(weighted_conf)

    buy_total = sum(buys)
    sell_total = sum(sells)

    if buy_total == 0 and sell_total == 0:
        return {"signal": "HOLD", "confidence": 0.0, "components": components}

    if buy_total >= sell_total:
        direction = "BUY"
        winning_total = buy_total
        winning_count = len(buys)
        losing_total = sell_total
    else:
        direction = "SELL"
        winning_total = sell_total
        winning_count = len(sells)
        losing_total = buy_total

    base_confidence = winning_total / winning_count

    # Conflict penalty: reduce confidence when signals disagree
    if losing_total > 0:
        conflict_ratio = losing_total / (winning_total + losing_total)
        base_confidence *= (1.0 - conflict_ratio * _CONFLICT_PENALTY_SCALE)

    return {
        "signal": direction,
        "confidence": base_confidence,
        "components": components,
    }


def _stage4_regime_filter(
    direction: str,
    confidence: float,
    regime: str,
) -> Dict[str, Any]:
    """Stage 4: apply regime-based confidence adjustments.

    Returns {"confidence": float, "note": str}.
    """
    if regime == "risk_on" and direction == "SELL":
        return {
            "confidence": confidence * _RISK_ON_SELL_DAMPEN,
            "note": f"Risk-on: SELL dampened {int((1 - _RISK_ON_SELL_DAMPEN) * 100)}%",
        }
    if regime == "risk_off" and direction == "BUY":
        return {
            "confidence": confidence * _RISK_OFF_BUY_DAMPEN,
            "note": f"Risk-off: BUY dampened {int((1 - _RISK_OFF_BUY_DAMPEN) * 100)}%",
        }
    return {"confidence": confidence, "note": "No regime adjustment"}


def _hold_result(ticker: str, regime: str, reason: str) -> Dict[str, Any]:
    """Build a HOLD result dict."""
    return {
        "ticker": ticker,
        "signal": "HOLD",
        "confidence": 0.0,
        "components": {},
        "modifiers_applied": [],
        "cat3_effect": None,
        "regime": regime,
        "regime_adjustment": "No regime adjustment",
        "rationale": reason,
    }


def load_learned_weights() -> Dict[str, float]:
    """Load learned strategy weights from the DB.

    Returns dict mapping strategy name to weight (0.1–1.0).
    Falls back to empty dict if DB is unavailable.
    """
    try:
        from db.client import get_all_weights
        return get_all_weights("strategy")
    except Exception as e:
        logger.warning(f"Could not load learned weights from DB: {e}")
        return {}
