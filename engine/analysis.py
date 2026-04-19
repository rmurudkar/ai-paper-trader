"""Comprehensive analysis engine for thesis-driven trading.

Replaces the old sentiment.py with dual-track processing:

High Materiality Track:
    Routes to thesis_extractor.py for full Claude thesis extraction.
    Extracts: thesis statement, theme, mechanism, implied tickers,
             direct sentiment, time horizon.
    Rich forward-looking analysis for thesis system.

Medium/Low Materiality Track:
    Basic sentiment analysis only.
    Uses pre-scored sentiment when available (Marketaux/Massive).
    Lightweight Claude processing for direct sentiment strategies.

Input:  Articles with materiality classification from materiality_classifier.py
Output: Articles enriched with either thesis data or sentiment data
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

from engine.thesis_extractor import extract_thesis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Basic Sentiment Analysis (Medium/Low Materiality Track)
# ---------------------------------------------------------------------------

SENTIMENT_MODEL = "claude-3-haiku-20240307"
SENTIMENT_MAX_TOKENS = 300
SENTIMENT_TEMPERATURE = 0.1
WORD_LIMIT = 1200

SENTIMENT_PROMPT = """You are a financial news sentiment analyzer for equity trading.

Analyze the sentiment of this article for trading the mentioned tickers.

Consider:
- Direct impact on company fundamentals
- Market reaction likelihood
- Time sensitivity of the information
- Strength of the signal

Output format (JSON):
{
  "sentiment_score": <float from -1.0 to 1.0>,
  "urgency": "<breaking|developing|standard>",
  "reasoning": "<2-3 sentence explanation>",
  "confidence": <float from 0.0 to 1.0>
}

Guidelines:
- breaking: immediate market-moving news requiring instant action
- developing: story gaining momentum, likely to impact prices within hours
- standard: normal news flow, may influence over days

Article:
Title: {title}
Source: {source}
Tickers: {tickers}
Content: {content}
"""


def _truncate_content(text: str, limit: int = WORD_LIMIT) -> str:
    """Truncate content to word limit per CLAUDE.md hard rule."""
    if not text:
        return ""
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit])


def _has_prescored_sentiment(article: Dict) -> bool:
    """Check if article already has trusted pre-scored sentiment."""
    source = (article.get("source") or "").lower()
    return source in {"marketaux", "massive"} and "sentiment_score" in article


def analyze_basic_sentiment(
    article: Dict,
    claude_client: Optional["anthropic.Anthropic"] = None  # type: ignore[name-defined]
) -> Dict:
    """Basic sentiment analysis for medium/low materiality articles.

    Uses pre-scored sentiment when available, otherwise lightweight Claude call.
    """
    enriched = dict(article)

    # Use pre-scored sentiment if available (Marketaux/Massive)
    if _has_prescored_sentiment(article):
        logger.debug(f"Using pre-scored sentiment for {article.get('source')} article")
        # Keep existing sentiment_score, add defaults for missing fields
        enriched.setdefault("urgency", "standard")
        enriched.setdefault("reasoning", f"Pre-scored sentiment from {article.get('source')}")
        enriched["sentiment_method"] = "prescored"
        return enriched

    # Claude sentiment analysis for other sources
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed; skipping sentiment analysis")
        enriched.update({
            "sentiment_score": 0.0,
            "urgency": "standard",
            "reasoning": "No sentiment analysis available",
            "sentiment_method": "unavailable"
        })
        return enriched

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY unset; skipping sentiment analysis")
        enriched.update({
            "sentiment_score": 0.0,
            "urgency": "standard",
            "reasoning": "API key unavailable",
            "sentiment_method": "unavailable"
        })
        return enriched

    content = _truncate_content(
        article.get("full_text") or article.get("snippet") or ""
    )
    if not content:
        logger.debug("No content for sentiment analysis")
        enriched.update({
            "sentiment_score": 0.0,
            "urgency": "standard",
            "reasoning": "No content available",
            "sentiment_method": "no_content"
        })
        return enriched

    prompt = SENTIMENT_PROMPT.format(
        title=article.get("title") or "",
        source=article.get("source") or "",
        tickers=", ".join(article.get("tickers") or []) or "none",
        content=content
    )

    try:
        client = claude_client or anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=SENTIMENT_MODEL,
            max_tokens=SENTIMENT_MAX_TOKENS,
            temperature=SENTIMENT_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}]
        )

        raw_response = response.content[0].text.strip()

        # Parse JSON response
        import json
        sentiment_data = json.loads(raw_response)

        # Clamp sentiment score to [-1.0, 1.0]
        sentiment_score = max(-1.0, min(1.0, sentiment_data.get("sentiment_score", 0.0)))

        enriched.update({
            "sentiment_score": sentiment_score,
            "urgency": sentiment_data.get("urgency", "standard"),
            "reasoning": sentiment_data.get("reasoning", ""),
            "sentiment_confidence": sentiment_data.get("confidence", 0.5),
            "sentiment_method": "claude"
        })

    except Exception as e:
        logger.warning(f"Claude sentiment analysis failed: {e}")
        enriched.update({
            "sentiment_score": 0.0,
            "urgency": "standard",
            "reasoning": f"Analysis failed: {str(e)[:100]}",
            "sentiment_method": "failed"
        })

    return enriched


# ---------------------------------------------------------------------------
# Thesis Extraction (High Materiality Track)
# ---------------------------------------------------------------------------

def extract_thesis_from_article(
    article: Dict,
    claude_client: Optional["anthropic.Anthropic"] = None,  # type: ignore[name-defined]
    existing_theses: Optional[List[Dict]] = None,
) -> Dict:
    """Route high materiality articles to full Claude thesis extraction.

    Delegates to engine.thesis_extractor.extract_thesis. On skip (no thesis
    found, API error, missing SDK/key, parse error) falls back to basic
    sentiment so the article still contributes a trading signal.

    Classifier materiality stays the source of truth for routing; Claude's
    re-check of materiality is preserved under `claude_materiality_check`.
    """
    thesis = extract_thesis(
        article,
        claude_client=claude_client,
        existing_theses=existing_theses,
    )

    if thesis.get("skip"):
        logger.info(
            "Thesis skipped (%s); falling back to sentiment: %s",
            thesis.get("skip_reason"),
            (article.get("title") or "")[:80],
        )
        fallback = analyze_basic_sentiment(article, claude_client)
        fallback["thesis_extracted"] = False
        fallback["thesis_skip_reason"] = thesis.get("skip_reason")
        return fallback

    enriched = dict(article)
    claude_materiality = thesis.get("materiality")
    enriched.update(thesis)
    if "materiality" in article:
        enriched["materiality"] = article["materiality"]
    enriched["claude_materiality_check"] = claude_materiality
    enriched["thesis_extracted"] = True

    logger.info(
        "Thesis extracted [theme=%s direction=%s confidence=%.2f]: %s",
        thesis.get("theme"),
        thesis.get("direction"),
        thesis.get("confidence", 0.0),
        (article.get("title") or "")[:80],
    )
    return enriched


# ---------------------------------------------------------------------------
# Main Analysis Router
# ---------------------------------------------------------------------------

def analyze_article(
    article: Dict,
    claude_client: Optional["anthropic.Anthropic"] = None,  # type: ignore[name-defined]
    existing_theses: Optional[List[Dict]] = None,
) -> Dict:
    """Main analysis router based on materiality classification.

    Routes articles to the appropriate analysis track based on materiality
    level. `existing_theses` is passed to the thesis extractor only; it is
    used as novelty context for Claude and ignored on the sentiment path.
    """
    materiality = article.get("materiality", "low")

    if materiality == "high":
        return extract_thesis_from_article(
            article, claude_client, existing_theses=existing_theses
        )
    return analyze_basic_sentiment(article, claude_client)


def analyze_articles(
    articles: List[Dict],
    claude_client: Optional["anthropic.Anthropic"] = None,  # type: ignore[name-defined]
    existing_theses: Optional[List[Dict]] = None,
) -> List[Dict]:
    """Analyze a batch of articles with materiality classifications.

    Args:
        articles: List of articles from materiality_classifier.classify_articles()
        claude_client: Optional shared Claude client for efficiency
        existing_theses: Optional list of active-thesis summary rows passed
            to the thesis extractor as novelty context. thesis_lifecycle.py
            still owns matching / merging.

    Returns:
        Articles enriched with either thesis data or sentiment data
    """
    if not articles:
        return []

    analyzed = []
    thesis_count = 0
    sentiment_count = 0

    for article in articles:
        try:
            enriched = analyze_article(
                article, claude_client, existing_theses=existing_theses
            )
            analyzed.append(enriched)

            # Track routing
            if enriched.get("thesis_extracted"):
                thesis_count += 1
            else:
                sentiment_count += 1

        except Exception as e:
            logger.error(f"Analysis failed for article {article.get('title', '')[:50]}: {e}")
            # Add failed article with defaults
            enriched = dict(article)
            enriched.update({
                "sentiment_score": 0.0,
                "urgency": "standard",
                "reasoning": f"Analysis error: {str(e)[:100]}",
                "sentiment_method": "error"
            })
            analyzed.append(enriched)

    logger.info(
        f"Analysis complete: {thesis_count} thesis extractions, "
        f"{sentiment_count} sentiment analyses ({len(articles)} total articles)"
    )

    return analyzed