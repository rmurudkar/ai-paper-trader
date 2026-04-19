"""Thesis extraction from high-materiality articles.

Thin wrapper around the Anthropic API that takes one article + metadata and
returns a structured investment thesis dict. Self-contained:

- Prompt building and response parsing live in engine/prompt.py.
- Model / max_tokens / temperature constants are imported from prompt.py.
- This module owns nothing persistent — no DB writes, no file I/O.

Call path in the pipeline:

    materiality_classifier → analysis.py (router) → thesis_extractor.extract_thesis
                                                           │
                                                           ▼
                                                    thesis_lifecycle.py
                                                           │
                                                           ▼
                                            active_theses / thesis_evidence

Graceful degradation
--------------------
Every failure mode (missing SDK, missing API key, empty body, network error,
unparseable response) returns a dict with skip=True and a descriptive
skip_reason. Callers should always branch on `skip` before using the thesis
fields — a skip dict carries empty/default values for every thesis field.

The output shape is the one parse_analysis_response emits, plus three
article-metadata fields stamped on here:

    source         from article["source"]
    published_at   from article["published_at"]
    article_url    from article["url"]

These three fields are what thesis_lifecycle.py needs to write into the
thesis_evidence table when it links an article to an active thesis.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from engine.prompt import (
    MAX_TOKENS,
    MODEL,
    TEMPERATURE,
    AnalysisParseError,
    build_analysis_prompt,
    parse_analysis_response,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Skip-dict construction
# ---------------------------------------------------------------------------


def _skip_result(reason: str) -> Dict:
    """Build a parsed-response-shaped dict with skip=True and safe defaults.

    Mirrors the schema parse_analysis_response emits so downstream modules
    can assume a consistent key set regardless of success or failure.
    """
    return {
        "skip": True,
        "skip_reason": reason,

        # Spec thesis output (flat, top-level)
        "thesis_statement": "",
        "theme": "",
        "direction": "neutral",
        "mechanism": "",
        "implied_tickers": [],
        "time_horizon": "short_term",
        "confidence": 0.0,
        "reasoning": "",

        # Supporting fields for downstream modules
        "ticker_analysis": [],
        "sectors": [],
        "urgency": "standard",
        "materiality": "low",
        "lifecycle_hint": "emerging",
        "novelty_reasoning": "",
    }


def _attach_article_metadata(result: Dict, article: Dict) -> Dict:
    """Stamp source / published_at / article_url onto the thesis dict."""
    result["source"] = article.get("source") or ""
    result["published_at"] = article.get("published_at") or ""
    result["article_url"] = article.get("url") or ""
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_thesis(
    article: Dict,
    claude_client: Optional["anthropic.Anthropic"] = None,  # type: ignore[name-defined]
    existing_theses: Optional[List[Dict]] = None,
) -> Dict:
    """Produce a structured thesis from a single high-materiality article.

    Args:
        article: article dict from fetchers/aggregator, already tagged with
            materiality="high". Must contain enough body text (full_text or
            snippet) to analyse.
        claude_client: optional pre-built anthropic.Anthropic client. When
            None, one is constructed from ANTHROPIC_API_KEY on demand. Passing
            a shared client avoids re-authenticating across a batch.
        existing_theses: optional list of active-thesis summary rows to pass
            to Claude as novelty context. thesis_lifecycle.py still owns the
            final matching / merging decision.

    Returns:
        Thesis dict shaped like parse_analysis_response's output with three
        article-metadata fields (source, published_at, article_url) stamped
        on. On any failure the returned dict has skip=True and a populated
        skip_reason; thesis fields hold safe defaults.
    """
    body = article.get("full_text") or article.get("snippet") or ""
    if not body.strip():
        return _attach_article_metadata(_skip_result("empty_body"), article)

    try:
        import anthropic  # local import keeps module importable without SDK
    except ImportError:
        logger.warning("anthropic SDK not installed; skipping thesis extraction")
        return _attach_article_metadata(_skip_result("no_sdk"), article)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY unset; skipping thesis extraction")
        return _attach_article_metadata(_skip_result("no_api_key"), article)

    system_prompt, user_prompt = build_analysis_prompt(
        article, existing_theses=existing_theses
    )

    try:
        cli = claude_client or anthropic.Anthropic(api_key=api_key)
        response = cli.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:  # network / auth / rate-limit — degrade gracefully
        logger.warning("Claude thesis extraction failed: %s", e)
        return _attach_article_metadata(
            _skip_result(f"api_error: {str(e)[:120]}"), article
        )

    try:
        raw_text = response.content[0].text
    except (AttributeError, IndexError):
        logger.warning("Claude returned unexpected response shape: %r", response)
        return _attach_article_metadata(
            _skip_result("bad_response_shape"), article
        )

    try:
        parsed = parse_analysis_response(raw_text)
    except AnalysisParseError as e:
        logger.warning("Thesis parse failed: %s", e)
        return _attach_article_metadata(
            _skip_result(f"parse_error: {str(e)[:120]}"), article
        )

    return _attach_article_metadata(parsed, article)


def extract_theses(
    articles: List[Dict],
    claude_client: Optional["anthropic.Anthropic"] = None,  # type: ignore[name-defined]
    existing_theses: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Batch-extract theses from a list of articles.

    One Claude call per article — the caller is responsible for filtering
    down to high-materiality articles first. Passing a shared claude_client
    avoids re-authenticating across the batch.

    Input order is preserved. Articles that skip (for any reason) still
    appear in the output list; callers should filter on result["skip"].
    """
    if not articles:
        return []

    out: List[Dict] = []
    extracted = 0
    skipped = 0

    for article in articles:
        result = extract_thesis(
            article,
            claude_client=claude_client,
            existing_theses=existing_theses,
        )
        if result.get("skip"):
            skipped += 1
        else:
            extracted += 1
        out.append(result)

    logger.info(
        "Thesis extraction: %d articles → %d extracted, %d skipped",
        len(articles), extracted, skipped,
    )
    return out
