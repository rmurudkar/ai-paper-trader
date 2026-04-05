# Few-Shot Examples for Sentiment Calibration

These examples serve two purposes:
1. **Prompt injection**: Append to the system prompt when score clustering is observed
2. **Regression testing**: Run these through the prompt after any change to verify calibration

## How to Use These Examples

### In the Prompt (when needed for calibration)

Append this block to the system prompt AFTER the calibration anchors section:

```
EXAMPLES — use these to calibrate your scoring:

Example 1 (strong positive, score ~0.7):
Ticker: NVDA
Article summary: NVIDIA reported Q4 revenue of $22.1B, beating estimates of $20.4B
by 8%. Data center revenue surged 409% year-over-year driven by AI chip demand. CEO
Jensen Huang announced new Blackwell GPU architecture shipping ahead of schedule.
Company raised Q1 guidance above consensus.
Expected output: {"reasoning": "Significant earnings beat with massive YoY data center
growth. New product shipping early and raised guidance signal continued momentum.",
"sentiment_score": 0.75, "confidence": 0.95, "catalysts": ["earnings beat", "data
center growth 409%", "Blackwell shipping early", "raised guidance"]}

Example 2 (mildly negative, score ~-0.3):
Ticker: AAPL
Article summary: Apple reported iPhone revenue declined 3% year-over-year in Q2,
slightly below analyst estimates. Services revenue grew 14% but couldn't offset
the hardware slowdown. Management maintained full-year guidance but acknowledged
"challenging consumer spending environment" in China.
Expected output: {"reasoning": "Minor revenue miss driven by iPhone weakness in
China. Services growth partially offsets but hardware is the core business. Maintained
guidance prevents deeper negative score.", "sentiment_score": -0.25, "confidence": 0.85,
"catalysts": ["iPhone revenue decline", "China weakness", "services growth partial offset"]}

Example 3 (neutral / irrelevant, score ~0.0):
Ticker: MSFT
Article summary: The semiconductor industry is experiencing a cyclical downturn with
memory chip prices falling 15% quarter-over-quarter. Samsung and SK Hynix are cutting
production. Intel announced layoffs in its foundry division. Microsoft was mentioned
as a major cloud customer that could benefit from lower chip costs in the long term.
Expected output: {"reasoning": "Article is about semiconductor industry downturn.
MSFT mentioned only as a tangential beneficiary of lower chip costs — speculative
and indirect. No material impact on MSFT specifically.", "sentiment_score": 0.05,
"confidence": 0.4, "catalysts": ["potential lower chip costs"]}
```

### When to Add Few-Shot Examples to the Prompt

Add them when you observe:
- **Score clustering**: >60% of scores fall between -0.2 and 0.2
- **Systematic miscalibration**: Strong-impact articles consistently get moderate scores
- **New article type**: You're processing a type of article the prompt hasn't seen
  (e.g., M&A announcements, FDA approvals, commodity price shocks)

Remove them when:
- Scores are well-distributed across the range
- Token budget is tight and you need to cut costs
- The calibration anchors in the system prompt are sufficient

---

## Full Example Set for Regression Testing

Run each of these through `analyze_newsapi_with_claude()` after any prompt change.
Check that scores fall within the expected range.

### Example A: Blowout Earnings (expect 0.6 to 0.9)

**Ticker**: META
**Article text** (abbreviated for this doc):
Meta Platforms reported fourth-quarter revenue of $40.1 billion, crushing Wall Street
expectations of $38.9 billion. The social media giant's Reality Labs division posted
its first-ever quarterly profit of $200M, a dramatic reversal from $4.6B in losses
the same quarter last year. CEO Mark Zuckerberg announced a $50B share buyback
program and a new $0.50 quarterly dividend. The company's AI-powered ad targeting
improvements drove a 24% increase in average revenue per user across all regions.
Guidance for Q1 was set at $41-43B, well above the consensus of $39.5B.

**Expected score range**: 0.7 to 0.9
**Expected confidence**: 0.9+
**Key catalysts**: earnings beat, Reality Labs profitable, buyback, dividend, raised guidance

---

### Example B: Severe Regulatory Action (expect -0.6 to -0.9)

**Ticker**: GOOG
**Article text** (abbreviated):
The Department of Justice announced today that a federal judge has ruled Google
maintains an illegal monopoly in the search advertising market and has ordered
structural remedies including the forced divestiture of Google's Chrome browser
and potential separation of its Android operating system. The ruling, the largest
antitrust action since the breakup of AT&T, could fundamentally reshape Google's
business model. Alphabet's stock fell 12% in after-hours trading. Analysts at
Goldman Sachs estimate the Chrome divestiture alone could reduce Google's search
revenue by 15-20%. The company announced it will appeal.

**Expected score range**: -0.7 to -0.9
**Expected confidence**: 0.9+
**Key catalysts**: antitrust ruling, forced divestiture, 12% stock drop, revenue impact

---

### Example C: Mixed Signals (expect -0.1 to 0.1)

**Ticker**: AMZN
**Article text** (abbreviated):
Amazon's Q3 results painted a mixed picture. AWS revenue grew 19%, beating estimates
of 17%, and operating margins expanded to 30%. However, the core retail business saw
operating margins compress by 150 basis points due to higher fulfillment costs and
increased competition from Temu and Shein. North American retail revenue grew just 4%,
below the 6% consensus. The company announced a $10B investment in AI infrastructure
but warned it would pressure free cash flow in the near term. Management declined to
provide specific Q4 guidance, citing "macroeconomic uncertainty."

**Expected score range**: -0.1 to 0.15
**Expected confidence**: 0.7-0.85
**Key catalysts**: AWS beat, retail margin compression, competition, no guidance

---

### Example D: Passing Mention / Irrelevant (expect -0.05 to 0.05)

**Ticker**: TSLA
**Article text** (abbreviated):
The global lithium market is experiencing a price correction after a two-year surge.
Lithium carbonate prices have fallen 60% from their 2022 peak as new mining capacity
comes online in Australia and Chile. Major lithium producers including Albemarle and
SQM have cut production guidance. Battery manufacturers are renegotiating contracts
to reflect lower input costs. Tesla, BYD, and other EV makers could see lower battery
costs in coming quarters, though the pass-through timing remains uncertain. The
broader EV supply chain is adjusting to a new pricing equilibrium.

**Expected score range**: -0.05 to 0.1
**Expected confidence**: 0.3-0.5
**Reasoning**: TSLA is mentioned as one of several EV makers who "could" benefit.
Speculative, indirect, and the article is about lithium markets, not Tesla.

---

### Example E: Partial Article / Snippet Only (expect lower confidence)

**Ticker**: JPM
**Snippet** (this is all that's available — `partial=True`):
JPMorgan Chase reported record quarterly profit of $13.4 billion. CEO Jamie Dimon
warned of "storm clouds" in the economy including persistent inflation and geopolitical
risks. The bank raised its net interest income forecast for the full year.

**Expected score range**: 0.2 to 0.4
**Expected confidence**: 0.2-0.4 (snippet only — limited context)
**Key catalysts**: record profit, raised NII forecast, but "storm clouds" warning

---

### Example F: Contradictory Headline vs Content

**Headline** (NOT sent to Claude — just for context):
"Apple Stock CRASHES After Earnings Disaster"

**Ticker**: AAPL
**Article text** (abbreviated):
Apple reported Q2 earnings of $1.53 per share, missing analyst estimates of $1.55 by
two cents. Revenue of $90.8 billion was roughly in line with expectations. iPhone
revenue was flat year-over-year while Services grew 11%. The stock fell 2.3% in
after-hours trading. Apple announced a $90B share buyback and maintained its dividend.
Tim Cook stated the company sees "strong momentum in emerging markets" and expects
return to growth in the next quarter.

**Expected score range**: -0.1 to -0.2
**Expected confidence**: 0.8-0.9
**Reasoning**: This is why we analyze full text, not headlines. The "crash" headline
describes a 2.3% decline after a minor EPS miss. The actual article shows an in-line
quarter with maintained guidance and a massive buyback. The sentiment is mildly negative
at worst.

---

### Example G: Multi-Ticker Article — Scoring for Non-Primary Ticker

**Ticker being scored**: AMD
**Article text** (abbreviated):
NVIDIA announced its next-generation Blackwell Ultra GPU at GTC 2026, featuring 2x
the AI inference performance of the current Blackwell architecture. CEO Jensen Huang
demonstrated the chip running a 10-trillion parameter model in real time. NVIDIA also
announced partnerships with Microsoft Azure, Google Cloud, and Amazon Web Services for
day-one Blackwell Ultra availability. AMD was briefly mentioned as "continuing to
compete in the AI accelerator space with its MI350 chip, though analysts note NVIDIA
maintains a significant performance lead." Intel's Gaudi 3 was not mentioned.

**Expected score range**: -0.15 to -0.3
**Expected confidence**: 0.5-0.7
**Reasoning**: The article is about NVIDIA's competitive advance. For AMD specifically,
the mention characterizes it as trailing NVIDIA with a "significant performance lead"
gap. This is mildly negative for AMD — it reinforces the competitive disadvantage
narrative. Score should NOT be strongly negative because no direct AMD bad news occurred.

---

## Anti-Patterns to Watch For

### Bad: Sentiment matches headline tone, not article content
```json
{"sentiment_score": -0.8, "reasoning": "Article reports Apple crash after earnings"}
```
This scores the headline, not the content. The full text reveals a 2% dip on a minor miss.

### Bad: Score too moderate for clear-cut news
```json
{"sentiment_score": 0.2, "reasoning": "Meta had good earnings with some positives"}
```
A blowout beat with buyback + dividend + raised guidance should score 0.7+, not 0.2.

### Bad: High confidence on irrelevant mention
```json
{"sentiment_score": 0.3, "confidence": 0.9, "reasoning": "TSLA could benefit from lower lithium"}
```
A speculative "could benefit" in a lithium market article should get confidence 0.3-0.5, not 0.9.

### Bad: Sentiment bleed from primary ticker
```json
{"sentiment_score": 0.6, "reasoning": "NVIDIA's new GPU is very impressive, AMD competes in same space"}
```
When scoring AMD, NVIDIA's positive news is AMD's competitive threat, not AMD's win.

### Good: Appropriate score with calibrated confidence
```json
{
  "reasoning": "Article primarily about NVIDIA's Blackwell Ultra launch. AMD mentioned
  as trailing competitor with 'significant performance lead' gap cited by analysts.
  No direct AMD news but competitive narrative is mildly negative.",
  "sentiment_score": -0.2,
  "confidence": 0.55,
  "catalysts": ["NVIDIA competitive advance", "analyst commentary on AMD trailing"]
}
```
