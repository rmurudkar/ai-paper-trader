"""Prompt templates and response parsing for engine/analysis.py.

This module owns the **full Claude call** used for high-materiality articles.
engine/materiality_classifier.py tags each article; articles tagged "high"
are routed here, where we ask Claude to produce a structured investment
thesis that downstream modules (engine/thesis_lifecycle.py, engine/strategies.py,
engine/combiner.py) can act on.

What the prompt extracts
------------------------
1. Thesis statement — a crisp, actionable sentence a PM could trade on.
2. Theme            — short category ("earnings_beat", "merger", …).
3. Mechanism        — how the thesis translates into price movement.
4. Implied tickers  — primary, secondary, peers, suppliers/customers,
                      beneficiaries/victims — not just those named in the
                      headline.
5. Direct sentiment — per-ticker score in [-1, +1] with reasoning.
6. Sectors          — GICS sectors the thesis touches.
7. Time horizon     — when the thesis should play out in price.
8. Lifecycle hint   — Claude's single-article guess at whether this reads
                      like a first report (emerging) or an already-priced-in
                      consensus piece. The thesis_lifecycle module may
                      override this using accumulated cross-article evidence.

Design notes
------------
- This module is **pure prompt plumbing**. It does not call the Anthropic
  SDK. analysis.py owns the client, retries, logging, and DB writes.
- Output is strict JSON. We refuse markdown fences and reject responses
  that can't be decoded.
- Article bodies are hard-truncated to WORD_LIMIT words before prompting
  (CLAUDE.md rule: never exceed 1200 words in Claude input).
- When analysis.py has a shortlist of already-active theses that could be
  relevant, it passes summaries via existing_theses=… so Claude can weigh
  novelty correctly. Claude does NOT re-output these — thesis_lifecycle.py
  handles matching and merging.

Public API
----------
    MODEL, MAX_TOKENS, TEMPERATURE, WORD_LIMIT
    build_analysis_prompt(article, existing_theses=None) -> (system, user)
    parse_analysis_response(raw_text) -> Dict
    AnalysisParseError
"""

from __future__ import annotations

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model + sampling constants
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500
TEMPERATURE = 0.2
WORD_LIMIT = 1200  # CLAUDE.md hard rule: never send more than 1200 words


# ---------------------------------------------------------------------------
# Valid enum values (used by parse_analysis_response for normalization)
# ---------------------------------------------------------------------------

VALID_DIRECTIONS = {"bullish", "bearish", "neutral"}
VALID_TIME_HORIZONS = {"intraday", "short_term", "medium_term", "long_term"}
VALID_LIFECYCLE_HINTS = {"emerging", "developing", "confirmed", "consensus"}
VALID_URGENCY = {"breaking", "developing", "standard"}
VALID_MATERIALITY = {"high", "medium", "low"}
VALID_TICKER_ROLES = {
    "primary", "secondary", "peer", "supplier", "customer",
    "competitor", "beneficiary", "victim",
}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior equity-research analyst embedded in an autonomous paper-trading
system. You receive one financial news article at a time. Your job is to decide
whether the article carries an actionable investment thesis and, if so, to
extract it in a strict structured form the trading system can act on.

Think in three steps.

1. UNDERSTAND
   Read the article end-to-end. Identify the concrete event or catalyst.
   Separate signal from noise: opinion pieces, generic recaps, sponsored
   content, and filler rarely carry a thesis. Be willing to skip.

2. EXTRACT
   If a thesis exists, write it in 1-2 sentences, present tense. A good
   thesis names:
     - WHAT is happening       (the catalyst)
     - WHO is affected          (primary tickers, sectors)
     - WHY it moves the price   (mechanism)
     - WHICH DIRECTION          (bullish / bearish / neutral)
   Then identify secondary tickers — suppliers, customers, peers,
   competitors, beneficiaries, or victims the article does not name
   explicitly but that a careful analyst would flag.

3. SCORE
   Assign sentiment per ticker in [-1.0, +1.0]. Estimate when the thesis
   should show up in price (time_horizon). Assess how novel the article
   feels (lifecycle_hint).

THESIS LIFECYCLE AWARENESS
--------------------------
Every thesis moves through a lifecycle:

  emerging    First report. Language cues: "exclusive", "sources tell us",
              "just announced", unexpected timing, unanticipated data. The
              market has likely NOT priced this in yet.

  developing  Builds on earlier reporting. Analyst debate is still active.
              Language cues: "reports have suggested", "analysts are split",
              "questions remain", "may signal".

  confirmed   Treated as accepted fact; the article focuses on second-order
              effects or cross-sector implications. Multiple analysts quoted
              in agreement. Language cues: "as many have noted", "it is
              now clear", "the implication is".

  consensus   Retrospective / think-piece tone on a story that is already
              widely priced in. Language cues: "long expected", "baked in",
              "priced in", "as everyone knows". Alpha opportunity is gone
              or inverted (positioning may unwind).

You are reading ONE article. You cannot see the broader flow of news the
trading system has accumulated. So your lifecycle_hint is an assessment of
THIS article's tone only. The system may override you after matching this
article against its active-thesis store.

If the user message includes an "EXISTING ACTIVE THESES" block, use it as
context for novelty. If the article clearly builds on one of those theses
say so in novelty_reasoning — but do NOT copy those theses into your output.
The trading system handles matching and merging.

STRICT RULES
------------
- Output MUST be a single JSON object matching the schema in the user
  message. No prose before or after. No markdown. No code fences. JSON only.
- If the article has NO actionable thesis, set "skip": true with a short
  "skip_reason". Leave thesis fields as empty strings, tickers=[], sectors=[],
  urgency="standard", materiality="low".
- NEVER fabricate tickers. Include a ticker only if it is named in the
  article OR is an unambiguous public-equity consequence of the thesis
  (e.g. a direct competitor, a named supplier). If you are not confident
  the ticker exists and trades on a US exchange, omit it.
- Calibrate sentiment_score. Reserve |0.8|+ for clear, quantified, high-
  materiality catalysts. Vague commentary belongs near zero.
- "confidence" is your confidence in the thesis EXTRACTION, not in the
  direction of the bet. Low when the article is ambiguous, high when the
  catalyst is explicit and well-sourced.
- "time_horizon" is when the thesis shows up in PRICE, not how long the
  underlying business change takes. A multi-year strategy pivot announced
  today usually prices in within days.
- Be concise. Every field earns its place. No repetition between fields.
"""


# ---------------------------------------------------------------------------
# Output schema (rendered inline in the user prompt)
# ---------------------------------------------------------------------------

OUTPUT_SCHEMA = """\
OUTPUT SCHEMA
-------------
Respond with exactly ONE JSON object matching this shape:

{
  "skip": false,
  "skip_reason": null,

  "thesis": {
    "statement": "1-2 sentence thesis, present tense",
    "theme": "short category token, e.g. earnings_beat | earnings_miss | guidance_cut | guidance_raise | merger | acquisition | regulatory_approval | regulatory_rejection | litigation | leadership_change | product_launch | product_recall | macro_rates | macro_inflation | geopolitical | supply_shock | secular_ai | secular_ev | sector_rotation",
    "direction": "bullish | bearish | neutral",
    "mechanism": "1 sentence: the causal path from catalyst to price movement",
    "confidence": 0.0,
    "time_horizon": "intraday | short_term | medium_term | long_term",
    "lifecycle_hint": "emerging | developing | confirmed | consensus",
    "novelty_reasoning": "1 sentence justifying the lifecycle_hint from article language"
  },

  "tickers": [
    {
      "symbol": "UPPERCASE US-exchange ticker",
      "role": "primary | secondary | peer | supplier | customer | competitor | beneficiary | victim",
      "sentiment_score": 0.0,
      "reasoning": "1 sentence: why this ticker gets this score"
    }
  ],

  "sectors": ["GICS sector names, e.g. Information Technology, Health Care, Energy"],

  "urgency": "breaking | developing | standard",
  "materiality": "high | medium | low",

  "reasoning": "2-4 sentences summarising the full analysis"
}

FIELD NOTES
-----------
- time_horizon mapping:
    intraday     resolves by end of trading day
    short_term   days to a week (earnings reactions, guidance moves)
    medium_term  weeks to a few months (regulatory decisions, product cycles)
    long_term    quarters to years (secular themes, strategy pivots)
- urgency:
    breaking     unexpected, just-crossed-the-wire news
    developing   ongoing story with new information
    standard     follow-up, commentary, or scheduled event
- materiality:
    Re-confirm the upstream classifier's call. If you disagree, use your
    own read — you are the more careful reader. Stay within high/medium/low.
- "tickers" may be empty if the article is macro-only. In that case the
  thesis still has value via the "sectors" field.
- JSON only. No markdown fences. No commentary.
"""


# ---------------------------------------------------------------------------
# User prompt template
# ---------------------------------------------------------------------------

_USER_PROMPT_TEMPLATE = """\
{schema}

{existing_context}ARTICLE METADATA
----------------
Title:        {title}
Source:       {source}
Published:    {published_at}
URL:          {url}
Known tickers (from fetch-time ticker extraction, may be incomplete): {known_tickers}
Upstream materiality classification: {materiality}

ARTICLE BODY
------------
{body}
"""


_EXISTING_THESES_TEMPLATE = """\
EXISTING ACTIVE THESES (context only — do NOT re-output these)
--------------------------------------------------------------
The trading system currently tracks these active theses. Use them to
calibrate your lifecycle_hint: if this article clearly supports one of
them, say so in novelty_reasoning, and lean toward 'developing' or
'confirmed'. If the article appears independent of all of them, the
thesis is more likely 'emerging'.

{thesis_list}

"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_words(text: str, limit: int = WORD_LIMIT) -> str:
    """Hard-truncate to `limit` words. Matches materiality_classifier behavior."""
    if not text:
        return ""
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit])


def _format_existing_theses(theses: Optional[List[Dict]]) -> str:
    """Render a list of active-thesis summaries into the prompt block.

    Each thesis is expected to have at least: id, thesis_statement, theme,
    direction, lifecycle_stage, tickers (list or JSON string).
    Missing fields degrade gracefully.
    """
    if not theses:
        return ""

    lines = []
    for t in theses:
        tickers = t.get("tickers")
        if isinstance(tickers, str):
            try:
                tickers = json.loads(tickers)
            except (TypeError, ValueError):
                tickers = [tickers]
        tickers_str = ", ".join(tickers or []) or "(none)"

        lines.append(
            f"- [{t.get('lifecycle_stage', '?')}] "
            f"{t.get('theme', '?')} / {t.get('direction', '?')} "
            f"on {tickers_str}: {t.get('thesis_statement', '').strip()}"
        )

    return _EXISTING_THESES_TEMPLATE.format(thesis_list="\n".join(lines))


def build_analysis_prompt(
    article: Dict,
    existing_theses: Optional[List[Dict]] = None,
) -> Tuple[str, str]:
    """Build the (system, user) prompt pair for a full thesis extraction call.

    Args:
        article: one article dict from fetchers/aggregator, already tagged
            with materiality by engine/materiality_classifier. Must contain
            enough body text to analyse — the caller should skip articles
            without full_text or snippet.
        existing_theses: optional list of active-thesis rows from
            db.get_active_theses(). Passed to Claude purely as novelty
            context; the caller is responsible for matching/merging.

    Returns:
        (system_prompt, user_prompt) ready to pass to the Anthropic SDK.
    """
    body = _truncate_words(
        article.get("full_text") or article.get("snippet") or ""
    )

    known_tickers = article.get("tickers") or []
    known_tickers_str = ", ".join(known_tickers) if known_tickers else "(none extracted)"

    existing_context = _format_existing_theses(existing_theses)

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        schema=OUTPUT_SCHEMA,
        existing_context=existing_context,
        title=(article.get("title") or "").strip() or "(no title)",
        source=article.get("source") or "(unknown source)",
        published_at=article.get("published_at") or "(unknown)",
        url=article.get("url") or "(no url)",
        known_tickers=known_tickers_str,
        materiality=article.get("materiality") or "(not classified)",
        body=body or "(no body text available)",
    )

    return SYSTEM_PROMPT, user_prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class AnalysisParseError(ValueError):
    """Raised when Claude's response cannot be parsed into the expected shape."""


# Tolerate markdown fences even though the prompt forbids them.
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = _JSON_FENCE_RE.sub("", raw).strip()
    return raw


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _normalize_enum(value: Optional[str], valid: set, default: str) -> str:
    if not isinstance(value, str):
        return default
    lowered = value.strip().lower().replace("-", "_").replace(" ", "_")
    return lowered if lowered in valid else default


def _normalize_ticker_entry(raw: Dict) -> Optional[Dict]:
    symbol = raw.get("symbol") or raw.get("ticker")
    if not isinstance(symbol, str):
        return None
    symbol = symbol.strip().upper()
    if not symbol or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
        return None

    try:
        score = float(raw.get("sentiment_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0

    return {
        "symbol": symbol,
        "role": _normalize_enum(raw.get("role"), VALID_TICKER_ROLES, "primary"),
        "sentiment_score": round(_clamp(score, -1.0, 1.0), 3),
        "reasoning": (raw.get("reasoning") or "").strip(),
    }


def parse_analysis_response(raw_text: str) -> Dict:
    """Parse Claude's analysis response into a validated, normalized dict.

    Clamps sentiment to [-1, 1] and confidence to [0, 1]. Normalizes all
    enum fields to their valid values (or a sensible default). Filters out
    malformed ticker entries. Preserves the skip pathway so analysis.py
    can record "analyzed but no thesis" outcomes.

    Raises:
        AnalysisParseError: if the response is not valid JSON or is missing
            the top-level structure entirely.
    """
    if not raw_text or not isinstance(raw_text, str):
        raise AnalysisParseError("empty or non-string response")

    cleaned = _strip_fences(raw_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise AnalysisParseError(f"invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise AnalysisParseError(f"expected JSON object, got {type(data).__name__}")

    skip = bool(data.get("skip", False))
    skip_reason = data.get("skip_reason")
    if skip_reason is not None and not isinstance(skip_reason, str):
        skip_reason = str(skip_reason)

    thesis_raw = data.get("thesis") or {}
    if not isinstance(thesis_raw, dict):
        thesis_raw = {}

    try:
        thesis_confidence = float(thesis_raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        thesis_confidence = 0.0

    thesis = {
        "statement": (thesis_raw.get("statement") or "").strip(),
        "theme": (thesis_raw.get("theme") or "").strip().lower().replace(" ", "_"),
        "direction": _normalize_enum(thesis_raw.get("direction"), VALID_DIRECTIONS, "neutral"),
        "mechanism": (thesis_raw.get("mechanism") or "").strip(),
        "confidence": round(_clamp(thesis_confidence, 0.0, 1.0), 3),
        "time_horizon": _normalize_enum(
            thesis_raw.get("time_horizon"), VALID_TIME_HORIZONS, "short_term"
        ),
        "lifecycle_hint": _normalize_enum(
            thesis_raw.get("lifecycle_hint"), VALID_LIFECYCLE_HINTS, "emerging"
        ),
        "novelty_reasoning": (thesis_raw.get("novelty_reasoning") or "").strip(),
    }

    tickers_raw = data.get("tickers") or []
    if not isinstance(tickers_raw, list):
        tickers_raw = []
    tickers: List[Dict] = []
    seen_symbols = set()
    for entry in tickers_raw:
        if not isinstance(entry, dict):
            continue
        normalized = _normalize_ticker_entry(entry)
        if normalized is None:
            continue
        if normalized["symbol"] in seen_symbols:
            continue
        seen_symbols.add(normalized["symbol"])
        tickers.append(normalized)

    sectors_raw = data.get("sectors") or []
    if not isinstance(sectors_raw, list):
        sectors_raw = []
    sectors = [s.strip() for s in sectors_raw if isinstance(s, str) and s.strip()]

    urgency = _normalize_enum(data.get("urgency"), VALID_URGENCY, "standard")
    materiality = _normalize_enum(data.get("materiality"), VALID_MATERIALITY, "medium")
    reasoning = (data.get("reasoning") or "").strip()

    if skip:
        thesis = {
            "statement": "", "theme": "", "direction": "neutral",
            "mechanism": "", "confidence": 0.0,
            "time_horizon": "short_term", "lifecycle_hint": "emerging",
            "novelty_reasoning": "",
        }
        tickers = []
        sectors = []
        urgency = "standard"
        materiality = "low"

    return {
        "skip": skip,
        "skip_reason": skip_reason if skip else None,
        "thesis": thesis,
        "tickers": tickers,
        "sectors": sectors,
        "urgency": urgency,
        "materiality": materiality,
        "reasoning": reasoning,
    }
