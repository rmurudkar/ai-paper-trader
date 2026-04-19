"""Materiality classifier for incoming news articles.

Tags each article coming out of fetchers/aggregator.py with a materiality label
(high / medium / low) that routes it through the downstream thesis-driven
analysis pipeline:

    high   → engine/analysis.py full Claude call (thesis extraction, sentiment,
             implied tickers, time horizon)
    medium → basic sentiment pass (or use pre-scored sentiment if available)
    low    → skip expensive analysis, rely on pre-scored sentiment / drop

3-stage pipeline:

    Stage 1  Rules-based detection
             Keywords (earnings, guidance, CEO, merger, FDA, regulatory ...),
             multi-ticker count, title patterns ("announces", "reports").

    Stage 2  Source-based boost
             Pre-scored sources (marketaux, massive) floor at medium.
             Institutional feeds (alpaca/benzinga, polygon) floor at high
             (professional grade).
             Paywalled/premium outlets (WSJ, Bloomberg, Reuters) floor at
             medium even when only a snippet is available.
             Low-quality sources are downgraded by one level.

    Stage 3  Claude refinement (edge cases only)
             Runs *only* when stages 1+2 leave the article ambiguous
             ("unknown") or on a narrow low/medium border. Uses the cheap
             haiku model with a short prompt to pick medium vs low.
             Skipped if full_text is missing or ANTHROPIC_API_KEY is unset.

Input  : list of article dicts produced by fetchers/aggregator.fetch_all_news()
Output : same list, each article augmented with:

    {
        ...,
        "materiality":            "high" | "medium" | "low",
        "materiality_confidence": 0.0 – 1.0,
        "materiality_method":     "rules" | "source" | "claude" | "default",
        "materiality_signals":    ["keyword:share buyback", "source_floor:alpaca", ...],
    }
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stage 1 — rules-based detection
# ---------------------------------------------------------------------------

# High-materiality keywords. Matched as whole words, case-insensitive.
# Events in this set almost always move a ticker.
HIGH_KEYWORDS: Tuple[str, ...] = (
    # Earnings / guidance
    "earnings", "revenue", "eps", "guidance", "profit warning",
    "beats estimates", "misses estimates", "beat estimates", "missed estimates",
    "pre-announcement", "preliminary results",
    # M&A
    "merger", "merges with", "acquisition", "acquires", "acquired by",
    "buyout", "takeover", "tender offer", "spin-off", "spinoff",
    # Leadership
    "ceo resigns", "ceo steps down", "ceo fired", "ceo appointed",
    "cfo resigns", "cfo steps down", "names new ceo",
    # Regulatory / legal
    "fda approval", "fda approves", "fda rejects", "fda rejection",
    "sec investigation", "doj investigation", "antitrust",
    "class action", "settlement reached", "recall",
    # Capital / structure
    "bankruptcy", "chapter 11", "delisting", "going private",
    "ipo priced", "secondary offering", "stock split",
    "dividend increase", "dividend cut", "dividend suspended",
    "share buyback", "stock buyback", "restructuring",
    # Major operational
    "plant closure", "layoffs", "major contract", "strategic partnership",
    "product launch", "product recall",
)

# Medium-materiality title / body patterns. Weaker than HIGH_KEYWORDS
# but still indicative of concrete corporate activity.
MEDIUM_TITLE_PATTERNS: Tuple[str, ...] = (
    "announces", "announced", "reports", "reported", "files",
    "unveils", "launches", "launched", "raises", "lowers",
    "upgrades", "downgrades", "initiates coverage", "price target",
    "analyst", "forecast", "outlook",
)

# Above this ticker count a single article is broad-market commentary and
# its impact on any one ticker is diluted — cap such articles at medium.
MULTI_TICKER_MEDIUM_THRESHOLD = 3

# Cached compiled regex for whole-word keyword matching (case-insensitive).
_HIGH_KEYWORD_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in HIGH_KEYWORDS) + r")\b",
    re.IGNORECASE,
)
_MEDIUM_PATTERN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in MEDIUM_TITLE_PATTERNS) + r")\b",
    re.IGNORECASE,
)


def stage1_rules_based(article: Dict) -> Tuple[str, float, List[str]]:
    """Stage 1: keyword / pattern / ticker-count rules.

    Returns (materiality, confidence, matched_signals).
    materiality may be "high", "medium", or "unknown" (never "low" at this stage
    — Stage 1 only *promotes*; downgrades happen later).
    """
    title = (article.get("title") or "").strip()
    body = article.get("full_text") or article.get("snippet") or ""
    tickers = article.get("tickers") or []

    # Combine title + body for matching. Title is weighted by appearing twice
    # so keywords in headlines have more pull.
    search_text = f"{title} {title} {body}"

    high_matches = _HIGH_KEYWORD_RE.findall(search_text)
    unique_high = sorted(set(m.lower() for m in high_matches))

    if unique_high:
        # Confidence scales with number of distinct keywords matched, capped.
        confidence = min(0.95, 0.65 + 0.1 * len(unique_high))
        return "high", confidence, [f"keyword:{kw}" for kw in unique_high]

    signals: List[str] = []

    medium_matches = _MEDIUM_PATTERN_RE.findall(title)
    if medium_matches:
        signals.extend(f"title:{m.lower()}" for m in set(medium_matches))

    if len(tickers) >= MULTI_TICKER_MEDIUM_THRESHOLD:
        signals.append(f"multi_ticker:{len(tickers)}")

    if signals:
        return "medium", 0.6, signals

    return "unknown", 0.0, []


# ---------------------------------------------------------------------------
# Stage 2 — source-based boost
# ---------------------------------------------------------------------------

# Institutional / licensed feeds: full-text, curated, professional grade.
# Per spec, every article from these feeds is floored at "high" materiality.
INSTITUTIONAL_SOURCES = {"alpaca", "polygon"}

# Pre-scored aggregators: trusted sentiment, but the bodies themselves tend
# to be short summaries — floor at medium, never auto-promote to high.
PRE_SCORED_SOURCES = {"marketaux", "massive"}

# Premium outlets that we *cannot* scrape (see paywalled-domains list in
# CLAUDE.md). If an article comes from one, the snippet still deserves
# medium-floor treatment because these publishers break real news.
PREMIUM_DOMAINS = (
    "wsj.com", "ft.com", "bloomberg.com", "nytimes.com",
    "reuters.com", "barrons.com", "marketwatch.com",
)

# Low-reputation feeds whose articles should be downgraded one level.
# Populate with identifiers that appear in article["source"] (lowercased).
# Intentionally empty by default — add entries as low-quality sources are
# identified in production traffic.
LOW_QUALITY_SOURCES: set = set()

# One-step downgrade mapping used when an article comes from a low-quality
# source. "unknown" maps to "low" because it represents an article that
# Stage 1 could not find any signal for — a low-quality variant of the same
# is clearly low materiality.
_DOWNGRADE_ONE_LEVEL = {
    "high": "medium",
    "medium": "low",
    "low": "low",
    "unknown": "low",
}

_MATERIALITY_RANK = {"low": 0, "unknown": 0, "medium": 1, "high": 2}


def _max_materiality(current: str, floor: str) -> str:
    """Return the higher of (current, floor) by rank."""
    if _MATERIALITY_RANK.get(floor, 0) > _MATERIALITY_RANK.get(current, 0):
        return floor
    return current


def _is_premium_domain(url: str) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return any(domain in lowered for domain in PREMIUM_DOMAINS)


def stage2_source_boost(
    article: Dict,
    stage1_materiality: str,
    stage1_confidence: float,
    stage1_signals: List[str],
) -> Tuple[str, float, List[str]]:
    """Stage 2: adjust Stage 1's verdict based on source credibility.

    - Institutional feeds (alpaca, polygon) → floor at high (professional grade)
    - Pre-scored aggregators (marketaux, massive) → floor at medium
    - Premium paywalled outlets (WSJ, Bloomberg, Reuters, ...) → floor at medium
    - Low-quality sources → downgrade by one level
    - Otherwise leave unchanged (Stage 1 result flows through)
    """
    source = (article.get("source") or "").lower()
    url = article.get("url") or ""
    signals = list(stage1_signals)
    materiality = stage1_materiality
    confidence = stage1_confidence

    if source in INSTITUTIONAL_SOURCES:
        # Spec: institutional feeds are professional grade → floor at high.
        promoted = _max_materiality(materiality, "high")
        if promoted != materiality:
            materiality = promoted
            # Tighter confidence when Stage 1 already said medium (pattern +
            # institutional source is a strong combo). Looser when Stage 1
            # was unknown (we're promoting on source alone).
            confidence = max(
                confidence,
                0.75 if stage1_materiality == "medium" else 0.70,
            )
            signals.append(f"source_promote:{source}")

    elif source in PRE_SCORED_SOURCES:
        materiality = _max_materiality(materiality, "medium")
        if materiality == "medium" and stage1_materiality != "medium":
            confidence = max(confidence, 0.55)
            signals.append(f"source_floor:{source}")

    elif _is_premium_domain(url):
        materiality = _max_materiality(materiality, "medium")
        if materiality == "medium" and stage1_materiality != "medium":
            confidence = max(confidence, 0.55)
            signals.append("source_floor:premium_domain")

    elif source in LOW_QUALITY_SOURCES:
        # Spec: low-quality sources → downgrade one level.
        downgraded = _DOWNGRADE_ONE_LEVEL.get(materiality, "low")
        if downgraded != materiality:
            materiality = downgraded
            # Cap confidence since we're overriding Stage 1 on source alone.
            confidence = min(confidence, 0.5) if confidence > 0 else 0.5
            signals.append(f"source_downgrade:{source}")

    return materiality, confidence, signals


# ---------------------------------------------------------------------------
# Stage 3 — Claude refinement (edge cases only)
# ---------------------------------------------------------------------------

CLAUDE_MODEL = "claude-3-haiku-20240307"
CLAUDE_MAX_TOKENS = 100
CLAUDE_TEMPERATURE = 0.1
WORD_LIMIT = 1200  # aligns with CLAUDE.md hard rule

_CLAUDE_PROMPT = (
    "You are a financial news triage classifier.\n"
    "Decide whether the following article is 'medium' or 'low' materiality\n"
    "for short-term equity trading.\n\n"
    "medium = article describes a concrete corporate action, analyst move,\n"
    "         macro event, or specific catalyst that could plausibly move\n"
    "         a stock within a few days.\n"
    "low    = commentary, opinion, broad market recap, duplicate coverage,\n"
    "         or content with no specific actionable catalyst.\n\n"
    "Respond with ONLY one token: either 'medium' or 'low'.\n\n"
    "Title: {title}\n"
    "Source: {source}\n"
    "Tickers: {tickers}\n"
    "Article:\n{body}\n"
)


def _truncate_words(text: str, limit: int = WORD_LIMIT) -> str:
    if not text:
        return ""
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit])


def _is_stage3_candidate(
    article: Dict, materiality: str, stage1_signals: List[str]
) -> bool:
    """Decide whether an article is worth spending a Claude call on.

    Only runs when:
      - we have enough body text to be meaningful, AND
      - Stage 1+2 left us unsure: either "unknown" or "medium" that only
        cleared the bar on weak signals (e.g. single title pattern).
    """
    body = article.get("full_text") or article.get("snippet") or ""
    if len(body.split()) < 40:
        return False

    if materiality == "unknown":
        return True

    if materiality == "medium":
        # Borderline: Stage 1 only barely tagged it, and no source floor
        # locked in the medium rating. One weak signal and no ticker focus.
        has_source_floor = any(s.startswith("source_floor:") for s in stage1_signals)
        only_title_pattern = (
            len(stage1_signals) == 1
            and stage1_signals[0].startswith("title:")
        )
        return only_title_pattern and not has_source_floor

    return False


def stage3_claude_refinement(
    article: Dict, client: Optional["anthropic.Anthropic"] = None  # type: ignore[name-defined]
) -> Tuple[Optional[str], float]:
    """Stage 3: ask Claude haiku to pick medium vs low on edge cases.

    Returns (materiality, confidence). materiality is None if the call was
    skipped or failed (caller should keep the Stage 1+2 verdict).
    """
    try:
        import anthropic  # local import keeps module importable without the SDK
    except ImportError:
        logger.debug("anthropic SDK not installed; skipping Stage 3")
        return None, 0.0

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("ANTHROPIC_API_KEY unset; skipping Stage 3")
        return None, 0.0

    body = _truncate_words(
        article.get("full_text") or article.get("snippet") or ""
    )
    if not body:
        return None, 0.0

    prompt = _CLAUDE_PROMPT.format(
        title=article.get("title") or "",
        source=article.get("source") or "",
        tickers=", ".join(article.get("tickers") or []) or "none",
        body=body,
    )

    try:
        cli = client or anthropic.Anthropic(api_key=api_key)
        response = cli.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            temperature=CLAUDE_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:  # network / API / auth errors — degrade gracefully
        logger.warning("Stage 3 Claude call failed: %s", e)
        return None, 0.0

    raw = ""
    try:
        raw = response.content[0].text.strip().lower()
    except (AttributeError, IndexError):
        logger.warning("Stage 3 unexpected response shape: %r", response)
        return None, 0.0

    if "medium" in raw:
        return "medium", 0.7
    if "low" in raw:
        return "low", 0.7

    logger.debug("Stage 3 ambiguous reply: %r", raw)
    return None, 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_article(
    article: Dict,
    use_claude: bool = True,
    claude_client: Optional["anthropic.Anthropic"] = None,  # type: ignore[name-defined]
) -> Dict:
    """Run all 3 stages on a single article, returning a new dict with
    materiality metadata attached. Does not mutate the input."""
    enriched = dict(article)

    s1_mat, s1_conf, s1_signals = stage1_rules_based(article)
    s2_mat, s2_conf, s2_signals = stage2_source_boost(
        article, s1_mat, s1_conf, s1_signals
    )

    materiality = s2_mat
    confidence = s2_conf
    signals = s2_signals
    method = "rules" if s1_mat != "unknown" else "default"
    if any(s.startswith("source_") for s in signals):
        method = "source"

    if use_claude and _is_stage3_candidate(article, materiality, s1_signals):
        s3_mat, s3_conf = stage3_claude_refinement(article, client=claude_client)
        if s3_mat is not None:
            materiality = s3_mat
            confidence = s3_conf
            method = "claude"
            signals = signals + [f"claude:{s3_mat}"]

    if materiality == "unknown":
        materiality = "low"
        confidence = max(confidence, 0.3)
        if method == "default":
            signals = signals + ["default_low"]

    enriched["materiality"] = materiality
    enriched["materiality_confidence"] = round(confidence, 3)
    enriched["materiality_method"] = method
    enriched["materiality_signals"] = signals
    return enriched


def classify_articles(
    articles: List[Dict],
    use_claude: bool = True,
    claude_client: Optional["anthropic.Anthropic"] = None,  # type: ignore[name-defined]
) -> List[Dict]:
    """Classify a batch of articles from fetchers/aggregator.fetch_all_news().

    Args:
        articles: list of article dicts as produced by the aggregator.
        use_claude: if False, Stage 3 is skipped entirely (useful for tests
            and for runs where cost control outranks precision).
        claude_client: optional pre-built anthropic.Anthropic client. When
            None, Stage 3 builds one on demand. Passing a shared client
            avoids re-authenticating per call.

    Returns:
        New list of article dicts, each augmented with materiality fields.
        Input order is preserved.
    """
    if not articles:
        return []

    out: List[Dict] = []
    counts = {"high": 0, "medium": 0, "low": 0}
    claude_calls = 0

    for article in articles:
        enriched = classify_article(
            article, use_claude=use_claude, claude_client=claude_client
        )
        counts[enriched["materiality"]] = counts.get(enriched["materiality"], 0) + 1
        if enriched["materiality_method"] == "claude":
            claude_calls += 1
        out.append(enriched)

    logger.info(
        "Materiality classification: %d high, %d medium, %d low "
        "(%d articles, %d Claude calls)",
        counts["high"], counts["medium"], counts["low"],
        len(articles), claude_calls,
    )
    return out
