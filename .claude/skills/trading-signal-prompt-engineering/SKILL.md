---
name: trading-signal-prompt-engineering
description: >
  Craft effective Claude API prompts for financial sentiment analysis and macro regime
  classification in the autonomous paper trading pipeline. Use this skill whenever working
  on engine/sentiment.py (especially analyze_newsapi_with_claude), engine/regime.py macro
  classification prompts, or any Claude API call that scores financial news sentiment,
  classifies market regime, or extracts trading-relevant signals from article text.
  Also trigger when the user mentions "sentiment prompt", "regime classification prompt",
  "Claude trading analysis", "per-ticker scoring", "few-shot financial examples",
  "token budgeting for sentiment", or wants to improve/debug/test sentiment analysis accuracy.
---

# Trading Signal Prompt Engineering

This skill defines how to write Claude API prompts for two core tasks in the paper trading
pipeline: **per-ticker sentiment scoring** and **macro regime classification**. These prompts
run inside `engine/sentiment.py` and `engine/regime.py` and are called on every 15-minute
trading cycle, so they must be reliable, token-efficient, and well-calibrated.

## When This Skill Applies

- Implementing or editing `analyze_newsapi_with_claude()` in `engine/sentiment.py`
- Implementing or editing macro classification in `engine/regime.py`
- Debugging sentiment scores that seem miscalibrated
- Optimizing token usage for batch sentiment analysis
- Adding new article types or edge cases to the prompt
- Testing prompt changes against historical articles

## Core Principles

### 1. Full Text, Never Headlines

The CLAUDE.md rule is absolute: **never send raw headlines to Claude for sentiment**.
Headlines are clickbait-optimized and produce noisy scores. Always send `full_text`
(truncated to 1200 words). If `full_text` is unavailable (`partial=True`), send the
snippet but include an explicit instruction telling Claude the context is limited and
to lower its confidence accordingly.

Why this matters: A headline like "Apple CRASHES After Earnings" might refer to a 2%
dip that recovered by close. The full article body contains the nuance that produces
accurate scores.

### 2. One Ticker Per Call

Even if an article mentions multiple tickers, score one ticker at a time. Multi-ticker
prompts cause "sentiment bleed" where positive news about Company A inflates scores
for Company B mentioned in the same article. The caller (`batch_analyze_articles`)
handles the loop — the prompt focuses on a single ticker.

Exception: If you need to extract *which tickers are mentioned* (discovery mode),
that's a separate extraction call, not a sentiment call.

### 3. Structured JSON Output

Always request JSON output. Free-text sentiment descriptions are harder to parse and
introduce extraction bugs. The prompt should specify the exact JSON schema and instruct
Claude to return *only* valid JSON with no markdown fencing.

### 4. Reasoning Before Score

Ask Claude to write its reasoning *before* the numeric score. This produces better
calibrated scores because the model "thinks through" the evidence before committing to
a number. The reasoning field also feeds into the trade log for debugging.

### 5. Bounded Scale with Anchor Points

The -1.0 to 1.0 scale needs anchor points or Claude will cluster everything between
-0.3 and 0.3. Provide explicit calibration anchors in the prompt so the model uses
the full range appropriately.

---

## Prompt Templates

Read `references/sentiment_prompt.md` for the full per-ticker sentiment prompt template
with inline comments explaining each section.

Read `references/regime_prompt.md` for the macro regime classification prompt template.

Read `references/few_shot_examples.md` for calibration examples (good vs bad articles,
edge cases, expected scores).

---

## Token Budgeting

Sentiment analysis runs on every trading cycle (every 15 min during market hours).
A typical cycle might process 10-30 articles × 1 call per ticker mention. Token costs
add up fast, so budget carefully.

### Per-Call Budget

| Component         | Tokens (approx) |
|-------------------|-----------------|
| System prompt     | ~400            |
| Article text (1200 words) | ~1600   |
| Few-shot examples | ~600            |
| Ticker context    | ~50             |
| **Total input**   | **~2650**       |
| Output (JSON)     | ~150-250        |

### Model Selection

Use `claude-sonnet-4-20250514` for sentiment calls — it's the best cost/quality tradeoff
for structured extraction. Opus is overkill for per-article sentiment. Haiku is too
imprecise for the -1.0 to 1.0 scale calibration.

### Batch Strategy

Process articles sequentially (one at a time per the CLAUDE.md rule). But minimize
redundant calls:

1. **Dedup before calling**: The aggregator already deduplicates articles. Never
   analyze the same article twice for the same ticker.
2. **Skip Marketaux articles**: They have pre-computed sentiment. Never re-analyze.
3. **Cache within cycle**: If the same article is relevant to multiple tickers,
   make separate calls but cache the article text so you don't re-fetch it.
4. **Partial articles get lower priority**: If `partial=True`, the article only has
   a snippet. Still analyze it, but tell Claude the context is limited.

### Daily Token Estimate

Assuming discovery mode with ~25 active tickers, ~20 NewsAPI articles per cycle,
~2 ticker mentions per article on average:

- Calls per cycle: ~40
- Input tokens per call: ~2650
- Output tokens per call: ~200
- **Per cycle**: ~114K tokens
- **Per trading day** (26 cycles × 6.5 hours): ~2.96M tokens
- At Sonnet pricing: roughly $2-4/day depending on actual article count

### Regime Classification Budget

Regime classification runs once per cycle (not per article), using aggregated macro
signals. Much cheaper:

- 1 call per cycle
- ~1500 input tokens (macro indicators + recent headline summaries)
- ~200 output tokens
- **Per day**: ~44K tokens total — negligible cost

---

## Implementation Pattern

Here's the recommended structure for `analyze_newsapi_with_claude()`:

```python
import json
import anthropic
from typing import Dict

# Load prompt templates at module level (not per-call)
# In production, read from references/ files or inline constants
SENTIMENT_SYSTEM_PROMPT = """..."""  # See references/sentiment_prompt.md
FEW_SHOT_EXAMPLES = """..."""       # See references/few_shot_examples.md

client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY from env

def analyze_newsapi_with_claude(full_text: str, ticker: str) -> Dict:
    """Send full article text to Claude for per-ticker sentiment analysis."""

    # Guard: never send empty text
    if not full_text or not full_text.strip():
        return {"sentiment_score": 0.0, "reasoning": "No article text provided", "confidence": 0.0}

    # Truncate to 1200 words (should already be done by scraper, but enforce)
    words = full_text.split()
    if len(words) > 1200:
        full_text = " ".join(words[:1200])

    user_message = f"""Analyze the sentiment of the following article as it relates to the
stock ticker {ticker}. Focus only on how this news affects {ticker} specifically.

<article>
{full_text}
</article>

<ticker>{ticker}</ticker>

Respond with ONLY a valid JSON object (no markdown, no explanation outside JSON):
{{
  "reasoning": "2-3 sentences explaining sentiment drivers for {ticker}",
  "sentiment_score": <float from -1.0 to 1.0>,
  "confidence": <float from 0.0 to 1.0>,
  "catalysts": ["list", "of", "key", "catalysts"]
}}"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        system=SENTIMENT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )

    # Parse response
    raw_text = response.content[0].text.strip()
    # Strip markdown fencing if present (defensive)
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(raw_text)
        # Clamp score to valid range
        result["sentiment_score"] = max(-1.0, min(1.0, float(result.get("sentiment_score", 0.0))))
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
        return result
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return {
            "sentiment_score": 0.0,
            "reasoning": f"Failed to parse Claude response: {e}",
            "confidence": 0.0,
            "catalysts": [],
            "parse_error": True
        }
```

### Handling Partial Articles

When `partial=True`, modify the user message to tell Claude:

```python
if partial:
    user_message = f"""Analyze the sentiment of the following PARTIAL article snippet as it
relates to {ticker}. NOTE: This is only a snippet, not the full article. Your confidence
should be lower than usual because you have limited context.

<snippet>
{snippet_text}
</snippet>
...
"""
```

This consistently produces lower confidence scores (0.3-0.5 range) for partial articles,
which downstream modules can use to weight these signals less heavily.

---

## Common Failure Modes and Fixes

### Score Clustering Around Zero

**Problem**: Most scores come back between -0.2 and 0.2.
**Cause**: No calibration anchors in the prompt.
**Fix**: Add the anchor points from `references/sentiment_prompt.md`. The system prompt
must include concrete examples of what -0.8, -0.3, 0.0, 0.3, 0.8 look like.

### Sentiment Bleed Across Tickers

**Problem**: Article about Apple's great earnings gives MSFT a positive score too.
**Cause**: Multi-ticker prompt or vague ticker instruction.
**Fix**: Emphasize in the prompt: "Score ONLY the impact on {ticker}. If the article
does not meaningfully affect {ticker}, return 0.0 regardless of overall article tone."

### Hallucinated Catalysts

**Problem**: Claude invents catalysts not mentioned in the article.
**Cause**: Claude filling in expected financial context.
**Fix**: Add to system prompt: "Only cite catalysts explicitly stated in the article.
Do not infer or assume information not present in the text."

### JSON Parse Failures

**Problem**: Claude wraps JSON in markdown fencing or adds preamble text.
**Cause**: Default assistant behavior.
**Fix**: The system prompt must say "Respond with ONLY valid JSON. No markdown code
fences, no preamble, no explanation outside the JSON object." The code should also
defensively strip fencing (see implementation pattern above).

### Regime Misclassification During Transitions

**Problem**: Regime stays RISK_ON during early stages of a sell-off.
**Cause**: Quantitative indicators lag; macro headlines arrive first.
**Fix**: The regime prompt should weight recent headline sentiment heavily during
transitions. See `references/regime_prompt.md` for the transition-aware prompt design.

---

## Testing Prompts

After changing any prompt, test against these scenarios before deploying:

1. **Strong positive**: Earnings beat + raised guidance article → expect score > 0.6
2. **Strong negative**: SEC investigation / fraud allegation → expect score < -0.6
3. **Mixed signals**: Revenue beat but guidance cut → expect score near 0.0 with high reasoning quality
4. **Irrelevant mention**: Article about industry trend that mentions ticker in passing → expect score near 0.0
5. **Partial article**: 100-word snippet only → expect lower confidence (< 0.5)
6. **Multi-ticker article**: Article about AAPL that mentions MSFT competitor → when analyzing MSFT, expect near 0.0
7. **Macro regime shift**: Fed rate hike article → regime should classify as RISK_OFF or at minimum NEUTRAL

Use the test articles in `references/few_shot_examples.md` as a regression suite.
