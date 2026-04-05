# Macro Regime Classification Prompt Template

This is the prompt for macro regime classification in `engine/regime.py`.
Called once per trading cycle (not per article) to determine whether the market
environment favors long trades (RISK_ON), short trades (RISK_OFF), or neither
(NEUTRAL).

## How Regime Classification Differs from Sentiment

Sentiment scoring analyzes one article → one ticker. Regime classification
synthesizes multiple signals (quantitative indicators + macro headline summaries)
into a single market-wide assessment. It runs once per cycle, receives aggregated
data, and produces a regime label that modifies all trading signals downstream.

## System Prompt

```
You are a macro regime classifier for an autonomous paper trading system.
Your job: analyze current market conditions and classify the regime as
RISK_ON, RISK_OFF, or NEUTRAL.

Your classification directly controls trade execution:
- RISK_ON: System keeps BUY confidence as-is, reduces SELL confidence by 20%
- RISK_OFF: System keeps SELL confidence as-is, reduces BUY confidence by 30%
- NEUTRAL: No modification to signals

You receive two types of input:
1. QUANTITATIVE INDICATORS (hard data — always trust these)
2. MACRO HEADLINE SUMMARIES (soft data — use for early warning)

CLASSIFICATION RULES:

RISK_ON (all conditions should generally hold):
  - VIX < 20
  - SPY price > SPY 200-day moving average
  - 10yr-2yr yield spread > 0.5%
  - Macro headlines are not signaling imminent crisis

RISK_OFF (any TWO of these conditions triggers risk-off):
  - VIX > 25
  - SPY price < SPY 200-day moving average
  - 10yr-2yr yield spread < -0.5% (inverted curve)
  - Multiple macro headlines signal crisis, recession, or policy shock

NEUTRAL:
  - Indicators are mixed or in transition zones
  - VIX between 20-25
  - SPY near its 200MA (within ±1%)
  - Yield spread between -0.5% and 0.5%

TRANSITION AWARENESS:
During regime transitions, headline sentiment leads and indicators lag.
If quantitative indicators say RISK_ON but 3+ recent headlines signal
deterioration (rate hikes, trade wars, bank failures), downshift to
NEUTRAL. If indicators say RISK_OFF but headlines signal recovery
(rate cuts, stimulus, peace deals), upshift to NEUTRAL — but never
skip directly to RISK_ON from headline sentiment alone.

IMPORTANT: Be conservative with regime changes. A false RISK_OFF is
much less costly than a false RISK_ON (missing trades vs taking losses).
When in doubt, classify as NEUTRAL.

OUTPUT FORMAT (strict JSON, no other text):
{
  "regime": "RISK_ON" | "RISK_OFF" | "NEUTRAL",
  "confidence": <float, 0.0 to 1.0>,
  "reasoning": "2-3 sentences explaining the classification",
  "primary_driver": "quantitative" | "headlines" | "mixed",
  "risk_factors": ["factor_1", "factor_2"]
}
```

## User Message Template

```
Classify the current market regime based on these inputs.

QUANTITATIVE INDICATORS (as of {timestamp}):
- VIX: {vix_value}
- SPY price: ${spy_price} (200-day MA: ${spy_200ma}, delta: {spy_vs_200ma_pct}%)
- 10yr yield: {yield_10yr}%
- 2yr yield: {yield_2yr}%
- 10yr-2yr spread: {yield_spread}%

RECENT MACRO HEADLINES (last 4 hours):
{headline_summaries}

Respond with ONLY a valid JSON object:
{
  "regime": "RISK_ON" | "RISK_OFF" | "NEUTRAL",
  "confidence": <float, 0.0 to 1.0>,
  "reasoning": "2-3 sentences explaining the classification",
  "primary_driver": "quantitative" | "headlines" | "mixed",
  "risk_factors": ["factor_1", "factor_2"]
}
```

## Preparing Headline Summaries

The headline summaries in the user message are NOT full articles. They are
one-line summaries of the most impactful macro/geopolitical headlines from
the current cycle. Format them as a numbered list:

```
1. Fed Chair signals two more rate hikes in 2026 amid persistent inflation
2. US-China trade tensions escalate with new semiconductor export controls
3. Eurozone PMI contracts for third consecutive month
4. US jobs report shows 280K added, above expectations
5. Oil prices surge 8% after OPEC announces surprise production cut
```

Limit to 5-8 headlines maximum. More than that dilutes attention and wastes
tokens. Prioritize headlines about:
- Central bank policy (Fed, ECB, BOJ)
- Trade policy / tariffs
- Economic data releases (jobs, GDP, PMI, inflation)
- Geopolitical events with market impact
- Financial system stress (bank failures, credit events)

### How to Generate Headline Summaries

The headlines come from `newsapi.fetch_headlines()` with `topics=['macro',
'geopolitical', 'economic']`. Before passing to the regime prompt:

1. Filter to articles from the last 4 hours
2. Take the top 5-8 by recency
3. Use only the `title` field (this is the ONE place where headlines are
   appropriate — regime classification needs breadth, not depth)
4. Prepend a number for readability

This is an exception to the "never use headlines" rule. For regime
classification, you want signal breadth (what topics are dominating the
news cycle) rather than depth (detailed sentiment per article). Headlines
are appropriate here because you're looking for patterns across many
headlines, not analyzing individual articles.

## Token Budget

| Component              | Tokens (approx) |
|------------------------|-----------------|
| System prompt          | ~500            |
| Quantitative indicators | ~100           |
| Headline summaries (8) | ~200            |
| Format instruction     | ~100            |
| **Total input**        | **~900**        |
| Output                 | ~150            |

One call per cycle. Negligible cost.

## Implementation Notes

### Caching Regime Between Cycles

The regime rarely changes within a single trading day. Cache the previous
regime result and only make a new Claude API call if:
- Quantitative indicators changed significantly (VIX moved >2 points, SPY
  crossed its 200MA, yield spread changed sign)
- New macro headlines arrived since last classification
- It's been more than 1 hour since last classification

This can reduce regime API calls from 26/day to 3-5/day.

### Fallback Without Claude

If the Claude API call fails, compute a simple quantitative-only regime:

```python
def fallback_regime(vix, spy_vs_200ma, yield_spread):
    risk_off_signals = sum([
        vix > 25,
        spy_vs_200ma < 0,
        yield_spread < -0.5
    ])
    risk_on_signals = sum([
        vix < 20,
        spy_vs_200ma > 0,
        yield_spread > 0.5
    ])

    if risk_off_signals >= 2:
        return "RISK_OFF"
    elif risk_on_signals >= 3:
        return "RISK_ON"
    else:
        return "NEUTRAL"
```

This fallback loses the headline-driven early warning but keeps the system
operational when the API is down.
