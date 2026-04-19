"""Thesis lifecycle management: matching, evolution, transitions.

Sits between `engine/thesis_extractor.py` (which produces a thesis dict from
one article) and the database (which stores the durable state of the world's
active theses). This module:

  1. Takes a newly-extracted thesis from a high-materiality article.
  2. Tries to match it against existing active_theses (direction + ticker
     overlap + theme similarity).
  3. On match: records evidence, bumps conviction, and transitions the
     lifecycle stage when evidence thresholds are crossed.
  4. On no match: creates a new thesis in the EMERGING stage.
  5. Expires stale theses (>5 days without evidence) at the start of a batch.

Matching heuristic
------------------
Candidate existing theses are filtered by direction + ≥1 primary-ticker
overlap via `db.client.find_similar_theses`. Each candidate is then scored:

    combined = 0.6 * ticker_jaccard + 0.4 * theme_similarity

Match threshold is 0.55 — enough that one shared ticker alone cannot pull
an article into an otherwise-unrelated thesis, but two shared tickers OR a
single shared ticker + same theme will merge.

theme_similarity returns 1.0 on exact match, otherwise a token-level Jaccard
over underscore-split theme tokens ("earnings_beat" vs "earnings_miss" → 1/3).

Lifecycle transitions
---------------------
Transition thresholds (evidence_count, source_diversity):

    emerging  → developing  : >=3 articles,  >=2 sources
    developing → confirmed  : >=4 articles,  >=3 sources
    confirmed → consensus   : >=6 articles,  >=4 sources

Only forward transitions are applied here; consensus → emerging never occurs.
Any stage → expired is handled separately by `db.client.expire_stale_theses`.

Conviction delta per evidence
-----------------------------
    base          = materiality_weight[materiality]   (0.15 / 0.08 / 0.04)
    source_boost  = 1.5 premium, 1.2 institutional, else 1.0
    confidence    = Claude's extraction confidence (0.0-1.0)
    delta         = base * source_boost * confidence

Only POSITIVE deltas are added here (supporting evidence). Contradicting
evidence is handled by the combiner's counter_thesis logic, not by writing
negative deltas to this thesis — that would muddy the per-thesis conviction.

Call path
---------

    analysis.py  (router)
         │
         ▼ thesis dict with thesis_extracted=True
    thesis_lifecycle.process_thesis
         │
         ├── find_similar_theses ─────► DB
         ├── (match) add_thesis_evidence + update_thesis_conviction
         └── (no match) create_thesis
                                        ▲
                             strategies.py / combiner.py read active_theses

Public API
----------
    process_thesis(article) -> Dict
    process_theses(articles) -> List[Dict]

Each returns a routing-result dict with `action` in:
    "matched" | "created" | "skipped" | "duplicate_evidence"
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from db.client import (
    add_thesis_evidence,
    create_thesis,
    expire_stale_theses,
    find_similar_theses,
    get_thesis_by_id,
    update_thesis_conviction,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

MATCH_THRESHOLD = 0.55
TICKER_WEIGHT = 0.6
THEME_WEIGHT = 0.4

MATERIALITY_BASE = {
    "high": 0.15,
    "medium": 0.08,
    "low": 0.04,
}

PREMIUM_SOURCES = {"wsj", "bloomberg", "reuters", "ft", "barrons"}
INSTITUTIONAL_SOURCES = {"alpaca", "polygon", "benzinga"}

STALE_THESIS_HOURS = 120  # 5 days — matches CLAUDE.md spec


# ---------------------------------------------------------------------------
# Helpers: ticker + theme extraction
# ---------------------------------------------------------------------------


def _primary_tickers(article: Dict) -> List[str]:
    """Return upper-case primary tickers from ticker_analysis."""
    analysis = article.get("ticker_analysis") or []
    return [
        (t.get("symbol") or "").upper()
        for t in analysis
        if isinstance(t, dict) and t.get("role") == "primary" and t.get("symbol")
    ]


def _all_tickers(article: Dict) -> List[str]:
    """Return all unique upper-case symbols from ticker_analysis."""
    analysis = article.get("ticker_analysis") or []
    seen = set()
    ordered: List[str] = []
    for t in analysis:
        if not isinstance(t, dict):
            continue
        sym = (t.get("symbol") or "").upper()
        if sym and sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    return ordered


def _jaccard(a: List[str], b: List[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def _theme_similarity(a: str, b: str) -> float:
    """Exact match → 1.0; otherwise Jaccard over underscore-tokenised themes."""
    if not a or not b:
        return 0.0
    a_norm = a.strip().lower()
    b_norm = b.strip().lower()
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    a_tokens = set(a_norm.split("_"))
    b_tokens = set(b_norm.split("_"))
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _score_candidate(incoming: Dict, incoming_tickers: List[str], candidate: Dict) -> float:
    """Combined match score in [0, 1]. Higher = better match."""
    ticker_score = _jaccard(incoming_tickers, candidate.get("tickers") or [])
    theme_score = _theme_similarity(
        incoming.get("theme", ""), candidate.get("theme", "")
    )
    return TICKER_WEIGHT * ticker_score + THEME_WEIGHT * theme_score


def _best_match(
    incoming: Dict,
    incoming_tickers: List[str],
    candidates: List[Dict],
) -> Optional[Tuple[Dict, float]]:
    """Return (best_thesis, score) if any candidate crosses MATCH_THRESHOLD."""
    best: Optional[Tuple[Dict, float]] = None
    for candidate in candidates:
        score = _score_candidate(incoming, incoming_tickers, candidate)
        if score < MATCH_THRESHOLD:
            continue
        if best is None or score > best[1]:
            best = (candidate, score)
    return best


# ---------------------------------------------------------------------------
# Conviction + lifecycle
# ---------------------------------------------------------------------------


def _source_boost(source: Optional[str]) -> float:
    if not source:
        return 1.0
    src = source.strip().lower()
    if src in PREMIUM_SOURCES:
        return 1.5
    if src in INSTITUTIONAL_SOURCES:
        return 1.2
    return 1.0


def _conviction_delta(article: Dict) -> float:
    """Positive conviction contribution from a single supporting article."""
    base = MATERIALITY_BASE.get(article.get("materiality", "low"), 0.04)
    boost = _source_boost(article.get("source"))
    confidence = float(article.get("confidence", 0.5) or 0.5)
    return round(base * boost * max(0.1, min(1.0, confidence)), 4)


def _next_lifecycle_stage(current: str, evidence_count: int, source_diversity: int) -> str:
    """Return the next stage if thresholds are crossed, else the current stage."""
    if current == "emerging" and evidence_count >= 3 and source_diversity >= 2:
        return "developing"
    if current == "developing" and evidence_count >= 4 and source_diversity >= 3:
        return "confirmed"
    if current == "confirmed" and evidence_count >= 6 and source_diversity >= 4:
        return "consensus"
    return current


# ---------------------------------------------------------------------------
# Result-dict shaping
# ---------------------------------------------------------------------------


def _result(
    action: str,
    thesis_id: Optional[str] = None,
    lifecycle_stage: Optional[str] = None,
    conviction_score: Optional[float] = None,
    evidence_added: bool = False,
    reason: str = "",
    match_score: Optional[float] = None,
) -> Dict:
    return {
        "action": action,
        "thesis_id": thesis_id,
        "lifecycle_stage": lifecycle_stage,
        "conviction_score": conviction_score,
        "evidence_added": evidence_added,
        "match_score": match_score,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def process_thesis(article: Dict) -> Dict:
    """Route a thesis-extracted article into active_theses / thesis_evidence.

    Args:
        article: dict output from engine.analysis.analyze_article with
            thesis_extracted=True. Expected keys:
              thesis_statement, theme, direction, mechanism,
              ticker_analysis, sectors, time_horizon, confidence, materiality,
              source, published_at, article_url.

    Returns:
        Routing result dict with:
            action               : "matched" | "created" | "skipped" | "duplicate_evidence"
            thesis_id            : UUID of target thesis (None if skipped)
            lifecycle_stage      : post-update stage (None if skipped)
            conviction_score     : post-update conviction (None if skipped)
            evidence_added       : True iff a new thesis_evidence row was written
            match_score          : combined match score if matched, else None
            reason               : human-readable short reason
    """
    # -- Gate: only theses we trust enough to accumulate --------------------
    if not article.get("thesis_extracted"):
        return _result("skipped", reason="not_thesis_extracted")

    if not (article.get("thesis_statement") or "").strip():
        return _result("skipped", reason="empty_thesis_statement")

    direction = article.get("direction")
    if direction not in {"bullish", "bearish"}:
        return _result("skipped", reason=f"non_tradable_direction:{direction}")

    primary = _primary_tickers(article)
    all_tk = _all_tickers(article)
    matching_tickers = primary or all_tk
    if not matching_tickers:
        return _result("skipped", reason="no_tickers_for_matching")

    # -- Matching ------------------------------------------------------------
    candidates = find_similar_theses(
        tickers=matching_tickers,
        direction=direction,
        min_ticker_overlap=1,
    )

    matched = _best_match(article, matching_tickers, candidates)

    if matched is not None:
        return _apply_match(article, matched[0], matched[1], all_tk)

    return _create_new(article, all_tk)


def _apply_match(
    article: Dict,
    matched_thesis: Dict,
    match_score: float,
    all_tickers: List[str],
) -> Dict:
    """Add evidence to matched thesis, bump conviction, maybe transition stage."""
    thesis_id = matched_thesis["id"]
    delta = _conviction_delta(article)

    added = add_thesis_evidence(
        thesis_id=thesis_id,
        article_url=article.get("article_url") or "",
        source=article.get("source") or "",
        published_at=article.get("published_at") or "",
        added_conviction=delta,
        materiality=article.get("materiality", "medium"),
        reasoning=article.get("reasoning") or "",
    )

    if not added:
        # Same article URL already linked to this thesis — no-op.
        return _result(
            "duplicate_evidence",
            thesis_id=thesis_id,
            lifecycle_stage=matched_thesis["lifecycle_stage"],
            conviction_score=matched_thesis["conviction_score"],
            evidence_added=False,
            match_score=match_score,
            reason="article_url_already_evidence",
        )

    # Re-fetch so evidence_count / source_diversity reflect the new row.
    refreshed = get_thesis_by_id(thesis_id) or matched_thesis
    next_stage = _next_lifecycle_stage(
        refreshed["lifecycle_stage"],
        refreshed.get("evidence_count", 0),
        refreshed.get("source_diversity", 0),
    )
    stage_transition = (
        next_stage if next_stage != refreshed["lifecycle_stage"] else None
    )

    update_thesis_conviction(
        thesis_id=thesis_id,
        conviction_delta=delta,
        new_lifecycle_stage=stage_transition,
    )

    # Re-fetch one more time to return accurate post-update state.
    final = get_thesis_by_id(thesis_id) or refreshed

    if stage_transition:
        logger.info(
            "Thesis %s transitioned %s → %s (evidence=%d sources=%d)",
            thesis_id,
            refreshed["lifecycle_stage"],
            stage_transition,
            final.get("evidence_count", 0),
            final.get("source_diversity", 0),
        )
    else:
        logger.debug(
            "Thesis %s matched (+%.3f conviction, stage=%s)",
            thesis_id,
            delta,
            final["lifecycle_stage"],
        )

    return _result(
        "matched",
        thesis_id=thesis_id,
        lifecycle_stage=final["lifecycle_stage"],
        conviction_score=final["conviction_score"],
        evidence_added=True,
        match_score=match_score,
        reason=(
            f"matched_with_transition:{stage_transition}"
            if stage_transition
            else "matched"
        ),
    )


def _create_new(article: Dict, all_tickers: List[str]) -> Dict:
    """Create a new thesis in the EMERGING stage and record its first evidence."""
    confidence = float(article.get("confidence", 0.5) or 0.5)
    initial_conviction = round(_conviction_delta(article), 4)

    thesis_id = create_thesis(
        thesis_statement=article["thesis_statement"].strip(),
        theme=article.get("theme") or "unspecified",
        direction=article["direction"],
        tickers=all_tickers,
        time_horizon=article.get("time_horizon", "short_term"),
        confidence_score=max(0.0, min(1.0, confidence)),
        conviction_score=initial_conviction,
        mechanism=article.get("mechanism") or None,
        sectors=article.get("sectors") or [],
    )

    add_thesis_evidence(
        thesis_id=thesis_id,
        article_url=article.get("article_url") or "",
        source=article.get("source") or "",
        published_at=article.get("published_at") or "",
        added_conviction=initial_conviction,
        materiality=article.get("materiality", "medium"),
        reasoning=article.get("reasoning") or "",
    )

    logger.info(
        "New thesis %s created [theme=%s direction=%s tickers=%s]",
        thesis_id,
        article.get("theme"),
        article["direction"],
        ",".join(all_tickers) or "(none)",
    )

    return _result(
        "created",
        thesis_id=thesis_id,
        lifecycle_stage="emerging",
        conviction_score=initial_conviction,
        evidence_added=True,
        reason="new_thesis_emerging",
    )


def process_theses(articles: List[Dict]) -> List[Dict]:
    """Batch-process thesis-extracted articles.

    Calls `expire_stale_theses` once up front so matching only sees live
    theses. Preserves input order. Each article produces one routing result;
    skipped articles are included so the caller can audit routing decisions.
    """
    if not articles:
        return []

    try:
        expire_stale_theses(max_age_hours=STALE_THESIS_HOURS)
    except Exception as e:
        # Expiry failure is non-fatal — log and continue.
        logger.warning("expire_stale_theses failed: %s", e)

    results: List[Dict] = []
    created = matched = duplicates = skipped = 0

    for article in articles:
        try:
            result = process_thesis(article)
        except Exception as e:
            logger.error(
                "process_thesis crashed on article %s: %s",
                (article.get("article_url") or article.get("title") or "?")[:80],
                e,
            )
            result = _result("skipped", reason=f"exception:{str(e)[:80]}")

        results.append(result)

        action = result["action"]
        if action == "created":
            created += 1
        elif action == "matched":
            matched += 1
        elif action == "duplicate_evidence":
            duplicates += 1
        else:
            skipped += 1

    logger.info(
        "Thesis lifecycle: %d articles → %d created, %d matched, "
        "%d duplicates, %d skipped",
        len(articles), created, matched, duplicates, skipped,
    )
    return results
