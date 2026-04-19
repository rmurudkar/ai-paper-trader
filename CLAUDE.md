# Autonomous Thesis-Driven Paper Trading App

Python app that runs continuously, fetches financial news, extracts investment theses with
Claude API, tracks thesis lifecycles, combines with technical strategies, enforces risk rules,
executes paper trades via Alpaca, and learns from its own performance. Paper trading only — no real money.

**Core Innovation**: Forward-looking thesis-driven trading that identifies market narratives before
they become consensus. The bot connects dots the market hasn't connected yet.

## Stack
- Python 3.11+, Anthropic SDK, APScheduler, Streamlit + Plotly
- Alpaca (`alpaca-trade-api` + `alpaca-py`) — paper trading + Benzinga news feed
- News: Marketaux API, Massive API, NewsAPI.ai (EventRegistry), Alpaca News, Polygon.io
- NLP: Groq Llama 3.1 8B (optional, company name → ticker extraction)
- Market data: yfinance (prices, MAs, RSI, VIX, yield spread, S&P 500 list)
- Scraping: trafilatura → newspaper3k → BeautifulSoup4 (waterfall fallback)
- DB: Turso (distributed SQLite) via libsql-client

## Project Structure
```
paper-trader/
├── scheduler/loop.py            # APScheduler event loop (STUB)
├── fetchers/
│   ├── discovery.py             # Ticker discovery engine — runs FIRST every cycle
│   ├── groq_client.py           # Groq company name → ticker extraction (optional)
│   ├── marketaux.py             # Pre-scored sentiment news
│   ├── massive.py               # Pre-scored sentiment news
│   ├── newsapi.py               # Macro/geopolitical headlines
│   ├── alpaca_news.py           # Benzinga feed via Alpaca
│   ├── polygon.py               # Licensed full-text enrichment (waterfall step 1)
│   ├── scraper.py               # Web scraper (waterfall step 3)
│   ├── market.py                # yfinance price/volume/MA/RSI/macro + S&P 500
│   └── aggregator.py            # 4-step waterfall + dedup + merge all 5 sources
├── engine/
│   ├── materiality_classifier.py # 3-stage materiality filter (rules, source, Claude)
│   ├── analysis.py              # Comprehensive analysis engine (replaces sentiment.py)
│   ├── thesis_extractor.py      # Claude thesis extraction from high materiality news
│   ├── thesis_lifecycle.py      # Thesis matching, evolution, lifecycle management
│   ├── strategies.py            # Thesis-driven strategies (4 tiers)
│   ├── regime.py                # Macro regime: risk_on / neutral / risk_off
│   └── combiner.py              # Thesis-first combiner (4-stage pipeline)
├── risk/manager.py              # Position sizing + 7 hard rules
├── executor/alpaca.py           # Alpaca paper trade executor
├── feedback/
│   ├── logger.py                # Trade logging to Turso
│   ├── outcomes.py              # Outcome measurement (every 4hrs + market close)
│   └── weights.py               # EMA weight updates + circuit breaker
├── dashboard/app.py             # Streamlit UI (reads from Turso)
├── db/
│   ├── client.py                # Turso connection + all query helpers
│   └── schema.sql               # 8 tables — see .claude/skills/schema/SKILL.md
├── tests/
│   ├── conftest.py
│   ├── fetchers/
│   ├── test_combiner.py
│   ├── test_feedback.py
│   ├── test_risk_manager.py
│   ├── test_sentiment.py
│   └── test_strategies.py
└── .claude/skills/
    ├── architecture/SKILL.md    # Full data flow, module contracts, return shapes
    ├── schema/SKILL.md          # Full SQL schema with column docs
    └── api-contracts/SKILL.md   # Exact return shapes for every module
```

## Environment Variables
```
ANTHROPIC_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
MARKETAUX_API_KEY=
NEWSAPI_KEY=
MASSIVE_API_KEY=               # optional
POLYGON_API_TOKEN=             # optional
GROQ_API_KEY=                  # optional
TURSO_CONNECTION_URL=libsql://your-database-name.turso.io
TURSO_AUTH_TOKEN=
TICKER_MODE=discovery          # "watchlist" | "discovery"
WATCHLIST=AAPL,MSFT,NVDA       # always-include list in both modes
MAX_DISCOVERY_TICKERS=30
ALERT_EMAIL=                   # optional
SLACK_WEBHOOK_URL=             # optional
```

## Implementation Status
- **Complete**: all fetchers, risk/manager.py, executor/alpaca.py, db/client.py, db/schema.sql
- **Stub**: scheduler/loop.py
- **Deleted (thesis redesign)**: engine/signals.py, engine/sentiment.py, old engine/strategies.py, old engine/combiner.py
- **To implement**: 
  - engine/materiality_classifier.py — 3-stage materiality filtering
  - engine/analysis.py — comprehensive analysis with thesis extraction routing
  - engine/thesis_extractor.py — Claude thesis extraction for high materiality articles
  - engine/thesis_lifecycle.py — thesis matching, evolution, lifecycle management
  - new engine/strategies.py — thesis-driven strategy system (4 tiers)
  - new engine/combiner.py — thesis-first combiner
  - Enhanced feedback modules for thesis-aware learning
  - Enhanced dashboard/app.py with thesis tracking
  - Database schema updates for thesis tables

## Hard Rules — Never Violate These
- NEVER send raw headlines to Claude for sentiment — always use `full_text`
- NEVER re-analyze Marketaux or Massive sentiment — it is pre-computed and trusted
- NEVER scrape: wsj.com, ft.com, bloomberg.com, nytimes.com, reuters.com, barrons.com, marketwatch.com
- NEVER use the live Alpaca URL (`api.alpaca.markets`) — paper only
- NEVER hardcode ticker lists outside discovery.py — all modules receive tickers dynamically
- NEVER bypass risk/manager.py — every signal must be approved before execution
- NEVER put >10% of portfolio in a single ticker
- NEVER allow total invested >80% of portfolio (keep 20% cash reserve)
- NEVER trade penny stocks (price < $5) or micro-caps (market cap < $1B)
- NEVER exceed 30% portfolio allocation in a single sector
- NEVER import deleted modules (signals.py, sentiment.py, old strategies.py, old combiner.py)
- ALWAYS run discovery.py as the FIRST step in every trading cycle
- ALWAYS truncate article text to 1200 words before any Claude or Groq call
- ALWAYS check market hours + circuit breaker before submitting orders
- ALWAYS log full signal metadata on every trade
- ALWAYS cache sector lookups in Turso `sector_cache` after yfinance fetch
- Cat 2 strategies (volume, VWAP, relative strength) are MODIFIERS only — never standalone signals
- Groq extraction is OPTIONAL — always combine with regex, degrade gracefully without GROQ_API_KEY
- NEVER extract theses from low materiality articles — waste of Claude API calls
- ALWAYS route high materiality articles through full thesis extraction
- NEVER hold thesis positions past CONSENSUS lifecycle stage — exit signal
- ALWAYS enter thesis positions in EMERGING/DEVELOPING stages for alpha
- NEVER ignore thesis lifecycle when sizing positions
- ALWAYS log thesis_id and lifecycle_stage with every trade for feedback loops

## Ticker Modes
**watchlist** — WATCHLIST env var tickers only. Lower API usage, predictable.

**discovery** (default) — Finds tickers from: news mentions, S&P 500 market movers
(dynamically fetched), sector rotation across 20 ETFs, **active thesis implications**, 
existing positions, WATCHLIST pins. Validates each ticker: price ≥ $5, market cap ≥ $1B, 
avg volume ≥ 500K. Capped at MAX_DISCOVERY_TICKERS. Positions, watchlist, and thesis-implied 
tickers exempt from cap.

## Thesis Lifecycle System

**Core Concept**: Investment theses have a natural lifecycle that determines optimal trading timing:

1. **EMERGING** — 1-2 articles, 1 source, <1 cycle old. Market hasn't noticed. **First-mover advantage window.**
2. **DEVELOPING** — 3+ articles, 2+ sources, 1-3 cycles. Smart money positioning. **Primary entry window.**  
3. **CONFIRMED** — 4+ articles, 3+ sources, institutional flow. **Scale-up window.**
4. **CONSENSUS** — Everyone talking, ticker moved 5%+. **Exit signal.**

**Strategy**: Enter EMERGING/DEVELOPING, scale CONFIRMED, exit CONSENSUS. Alpha lives in the gaps before consensus.

## Thesis-Driven Strategy Tiers

**Tier 1 — Thesis Lifecycle Strategies** (Primary signals):
- `first_mover_thesis`: EMERGING thesis, 1-2 articles, high materiality. Small size, wide stops, high alpha potential.
- `conviction_builder`: DEVELOPING thesis, 3+ articles, 2+ sources. Bread-and-butter strategy, normal size.
- `thesis_momentum`: Accelerating conviction (more articles per cycle). Add to positions.
- `thesis_fade_exit`: CONSENSUS/expired thesis. Exit signal, take profits.
- `counter_thesis`: New thesis contradicts existing. Reduce/exit conflicted positions.

**Tier 2 — Sentiment Fallback** (Non-thesis tickers):
- Enhanced versions of sentiment_divergence, multi_source_consensus, sentiment_momentum
- Only fire when no thesis implicates the ticker

**Tier 3 — Technical Timing** (Modifiers):
- `volume_confirmation`, `vwap_position`, `relative_strength` 
- Now thesis-aware: "does chart confirm thesis timing?"

**Tier 4 — Regime-Thesis Interaction** (Filter):
- Risk-off + growth thesis → dampen/kill
- Risk-off + defensive thesis → boost  
- Risk-on + any thesis → normal processing

## When to Load Skills
- Building, debugging, or modifying any module → load `.claude/skills/architecture/SKILL.md`
- Writing DB queries, migrations, or new tables → load `.claude/skills/schema/SKILL.md`
- Wiring modules together or checking return shapes → load `.claude/skills/api-contracts/SKILL.md`

---

## Thesis-Driven Architecture Flow

### 1. Discovery Enhancement
- **New source**: Active thesis implications — tickers from `active_theses` table
- **Priority order**: News mentions → Market movers → Sector rotation → **Thesis implications** → Positions → Watchlist
- **Thesis-implied tickers exempt** from MAX_DISCOVERY_TICKERS cap

### 2. Materiality Filtering (NEW)
**3-Stage Pipeline** in `materiality_classifier.py`:

**Stage 1: Rules-Based Detection**
- High materiality keywords: earnings, guidance, CEO, merger, FDA, regulatory
- Multi-ticker articles (≥3 tickers) → medium materiality
- Title patterns: "announces", "reports", "files" → potential high

**Stage 2: Source-Based Boost**  
- Premium sources (WSJ, Bloomberg, Reuters) → upgrade to medium
- Pre-scored sources (Marketaux, Massive) → medium
- Institutional feeds (Alpaca/Benzinga, Polygon) → high

**Stage 3: Claude Refinement** (Cost-controlled)
- Only for rules_score == "unknown" OR borderline cases
- Quick Claude call: "medium vs low materiality?"
- Skip Claude for clear high/low cases

**Output**: Articles tagged with materiality level → route to appropriate analysis

### 3. Comprehensive Analysis (REPLACES sentiment.py)
**Dual-track processing** in `analysis.py`:

**High Materiality Track**:
- Full Claude thesis extraction via `thesis_extractor.py`
- Extract: thesis statement, theme, mechanism, implied tickers, sentiment, time horizon
- Rich, forward-looking analysis for thesis system

**Medium/Low Materiality Track**:  
- Basic sentiment analysis only
- Use pre-scored sentiment when available (Marketaux/Massive)
- Lightweight processing for direct sentiment strategies

### 4. Thesis Management
**Lifecycle tracking** in `thesis_lifecycle.py`:

**Matching Logic**:
1. Compare new thesis against existing `active_theses`
2. Match on: theme keywords, ticker overlap, mechanism similarity
3. If match found → add to `thesis_evidence`, update conviction, evolve statement
4. If no match → create new thesis (EMERGING state)
5. Lifecycle transitions: emerging→developing→confirmed→consensus
6. Expire theses with no evidence >5 days

**Database Tables**:
- `active_theses`: Core thesis data, lifecycle state, conviction scores
- `thesis_evidence`: Supporting articles, conviction contributions  
- `sentiment_scores`: Fallback sentiment for non-thesis articles

### 5. Thesis-First Strategies
**4-Stage Strategy System** in new `strategies.py`:

**Primary**: Thesis lifecycle strategies determine most trades
**Secondary**: Sentiment fallback for non-thesis tickers  
**Modifiers**: Technical timing factors  
**Filter**: Regime-thesis interaction

Position sizing reflects lifecycle stage:
- EMERGING: 0.5x size (high risk)
- DEVELOPING: 1.0x size  
- CONFIRMED: 1.2x size
- CONSENSUS: 0.3x size (exit signal)

### 6. Thesis-First Combiner
**4-Stage Pipeline** in new `combiner.py`:

**Stage 1**: Thesis signals vote (weighted by lifecycle + conviction)
**Stage 2**: Sentiment confirmation (boost agreement, dampen conflict)  
**Stage 3**: Technical timing (volume surge, RSI extremes, VWAP position)
**Stage 4**: Regime-thesis interaction (select actionable thesis categories)

Key difference: Resolves conflicts between **theses**, not strategies.

### 7. Enhanced Feedback Loop
**Thesis-aware measurement**:
- Dual timeframes: 8hr for sentiment, 3-5 days for thesis trades
- Track thesis_id, lifecycle_stage in trade logs
- Update strategy weights AND thesis theme weights
- Measure materiality classification accuracy

### 8. Enhanced Risk Management
**Thesis lifecycle position sizing**:
- Wider stops for thesis trades (theses take time to play out)
- Tighter stops for sentiment-only trades (quicker moves)
- Thesis-aware sector limits and concentration rules

**.claude/skills/architecture/SKILL.md**

```markdown
---
name: architecture
description: >
  Full data flow narrative for the thesis-driven autonomous paper trader. Load this when building,
  debugging, or modifying any module. Covers the complete pipeline from scheduler through thesis
  management, including materiality filtering, thesis extraction, lifecycle management, thesis-driven
  strategies, and thesis-aware feedback loops.
  Trigger when the user says "how does X work", "implement X", "fix X", or asks
  about any module's behavior, inputs, or outputs.
---

# Thesis-Driven Architecture — Full Data Flow

## 1. Scheduler (scheduler/loop.py) — STUB
- Primary job: every 15 minutes during market hours (9:30 AM – 4:00 PM ET)
- Pre-market job: 9:00 AM ET — overnight news, sector rotation scan
- Post-market job: 4:30 PM ET — daily P&L, feedback loop
- Weekend/holiday: skip trading, run outcome measurements only
- Before every cycle: check `is_market_open()` (Alpaca calendar API) + circuit breaker

## 2. Discovery (fetchers/discovery.py) — runs FIRST every cycle

**Watchlist mode**: return WATCHLIST env var tickers only.

**Discovery mode** — 6 sources in priority order:
1. News mentions: scan Marketaux + Massive + NewsAPI for ticker symbols via regex
   ($AAPL, NASDAQ:AAPL) + optional Groq company name recognition. 2+ mentions = add ticker.
2. Market movers: fetch full S&P 500 list (Wikipedia → Turso sp500_cache → static fallback),
   `yf.download()` 2-day history, compute daily change %, top 5 gainers + top 5 losers.
3. **Active thesis implications**: extract tickers from `active_theses` table where 
   lifecycle_stage ∈ [EMERGING, DEVELOPING, CONFIRMED]. Thesis-driven discovery.
4. Sector rotation (pre-market only): 20 ETFs (11 SPDR sectors + VTV, VUG, RSP, IWM,
   EEM, VEA, QQQ, IVV, VTI). Fetch holdings dynamically via yfinance (fallback: hardcoded map).
   Top 3 holdings from top 2 + bottom 2 ETFs by 1-month performance.
5. Existing positions: always include held tickers.
6. WATCHLIST pins: always included, exempt from cap.

**Validation** (before adding any ticker): price ≥ $5, market cap ≥ $1B,
avg volume ≥ 500K. Cache in `validation_cache` SQLite (7-day TTL).

**Prioritization**: score = number of sources mentioning ticker. Positions + watchlist
exempt from MAX_DISCOVERY_TICKERS cap. Others sorted by score, top N taken.

**Returns:**
```python
{
    "tickers": ["AAPL", "NVDA", ...],
    "sources": {"AAPL": ["news", "position"], "NVDA": ["news", "gainer"]},
    "mode": "discovery",
    "cycle_id": "20260405_143022"
}
```

## 3. News Fetching

**fetchers/marketaux.py**
- Discovery mode: broad fetch, no ticker filter
- Watchlist mode: filter to watchlist tickers
- Returns pre-scored sentiment — DO NOT re-analyze with Claude
- Output shape: `{title, ticker, sentiment_score, snippet, description, url, published_at, source:"marketaux"}`

**fetchers/massive.py**
- Same pattern as Marketaux, optional (requires MASSIVE_API_KEY)
- Sentiment from `insights` array: positive→0.7, negative→-0.7, neutral→0.0
- DO NOT re-analyze with Claude
- Output: `{title, description, tickers, sentiment_score, url, published_at, source:"massive"}`

**fetchers/newsapi.py**
- Topics: macro (inflation), geopolitical (tariff), economic (recession), market (earnings), energy (oil)
- Discovery mode: extract tickers via regex + optional Groq from all articles
- Watchlist mode: filter to watchlist-relevant articles (broad topics always pass through)
- All articles flagged `needs_full_text: True` for waterfall enrichment
- Output: `{title, snippet, topics, url, published_at, source:"newsapi", tickers, extraction_confidence, needs_full_text:true}`

**fetchers/alpaca_news.py**
- Benzinga feed via Alpaca News API
- Strips HTML, truncates to 1200 words
- Used as standalone source AND as Step 2 of waterfall
- Output: `{title, full_text, ticker, tickers, url, published_at, source:"alpaca", author, partial}`

**fetchers/polygon.py**
- Licensed full-text lookup — Step 1 of waterfall only
- Matches NewsAPI URLs against Polygon feed
- Rate limited: 5 req/min, enforces ≥13s between requests
- Returns: `{full_text, publisher, published_at, tickers, url, partial:false}` or None

**fetchers/scraper.py** — FALLBACK ONLY, called by aggregator
- Tier 1: trafilatura.fetch_url() + trafilatura.extract()
- Tier 2: newspaper3k Article().parse()
- Tier 3: BeautifulSoup raw text
- Paywalled domains → snippet only, partial=True (never attempt: wsj.com, ft.com,
  bloomberg.com, nytimes.com, reuters.com, barrons.com, marketwatch.com)
- Returns: `{full_text, partial, extraction_method, word_count, extraction_time_ms}`

## 4. Aggregator (fetchers/aggregator.py)

**4-step waterfall** for each NewsAPI article with `needs_full_text:True`:
1. Polygon full-text lookup by URL → enrich if found
2. Alpaca News match by URL or title similarity >80% Jaccard → enrich if found
3. scraper.scrape(url) → enrich if found and not partial
4. Snippet only → mark `partial:True`

**Merge** all 5 sources: Marketaux + Massive + NewsAPI (enriched) + Alpaca + Polygon feed.

**Dedup**: exact URL match first, then title Jaccard similarity >80%. When duplicates,
keep article with full_text over snippet-only.

Returns: unified list sorted by published_at descending.

## 5. Materiality Filtering (engine/materiality_classifier.py)

**3-stage pipeline** for cost-controlled thesis extraction:

**Stage 1: Rules-Based Detection**
- High materiality keywords: earnings, guidance, CEO, merger, FDA, regulatory, acquisition
- Multi-ticker threshold: ≥3 tickers mentioned → medium materiality
- Title patterns: "announces", "reports", "files", "launches" → boost score
- Returns: "high" | "medium" | "low" | "unknown"

**Stage 2: Source-Based Boost**
- Premium sources (wsj.com, bloomberg.com, reuters.com) → upgrade to medium
- Pre-scored sources (Marketaux, Massive) → medium (already have sentiment)  
- Institutional feeds (Alpaca/Benzinga, Polygon) → high (professional grade)
- Low-quality sources → downgrade by one level

**Stage 3: Claude Refinement** (Edge cases only)
- Trigger: rules_score == "unknown" OR borderline classification
- Quick Claude call: "Is this medium or low materiality for trading?"
- Cost control: Skip Claude for clear high/low cases
- Uses `claude-3-haiku-20240307`, temperature=0.1, max_tokens=100

**Output routing:**
- High materiality → Full thesis extraction via `thesis_extractor.py`
- Medium/Low materiality → Basic sentiment analysis via `analysis.py`

## 6. Comprehensive Analysis (engine/analysis.py) — REPLACES sentiment.py

**Dual-track processing** based on materiality:

**High Materiality Track:**
- Route to `thesis_extractor.py` for full Claude thesis extraction
- Extract: thesis statement, theme, mechanism, implied tickers, sentiment, time horizon
- Send to `thesis_lifecycle.py` for thesis matching/creation
- Rich forward-looking analysis

**Medium/Low Materiality Track:**
- Basic sentiment analysis only  
- Routing: source == "marketaux" OR "massive" → use pre-scored sentiment
- source == "newsapi"/"alpaca"/"polygon" → lightweight Claude sentiment call
- Never send headlines to Claude, never send text >1200 words
- Uses `claude-3-haiku-20240307`, temperature=0.1, max_tokens=200

**Sentiment output per article per ticker:**
```python
{
    "sentiment_score": float,   # -1.0 to 1.0, clamped
    "urgency": str,             # "breaking" | "developing" | "standard"  
    "reasoning": str,
    "published_at": str,
    "source": str,
    "materiality": str          # from materiality_classifier
}
```

**Thesis output per article** (high materiality only):
```python
{
    "thesis_statement": str,    # Investment thesis extracted
    "theme": str,               # Categorical theme (AI, earnings, regulatory, etc.)
    "direction": str,           # "bullish" | "bearish" | "neutral"
    "mechanism": str,           # How the thesis plays out
    "implied_tickers": list,    # Secondary tickers beyond directly mentioned
    "time_horizon": str,        # "intraday" | "short_term" | "medium_term" | "long_term"
    "confidence": float,        # Claude's confidence in thesis (0-1)
    "reasoning": str,
    "published_at": str,
    "source": str
}
```

## 7. Thesis Management (engine/thesis_lifecycle.py)

**Core workflow:**
1. Receive thesis from `analysis.py` high materiality track
2. Match against existing `active_theses` via similarity scoring
3. If match: update thesis, add evidence, evolve lifecycle
4. If no match: create new thesis in EMERGING state
5. Lifecycle transitions based on evidence accumulation
6. Expire stale theses (>5 days no evidence)

**Matching algorithm:**
- Theme keyword similarity (cosine similarity >0.7)
- Ticker overlap (Jaccard similarity >0.5)  
- Mechanism similarity (semantic comparison)
- Combined score > threshold → match found

**Lifecycle transitions:**
- EMERGING → DEVELOPING: 3+ supporting articles, 2+ sources
- DEVELOPING → CONFIRMED: 4+ articles, 3+ sources, institutional mentions
- CONFIRMED → CONSENSUS: 6+ articles, price movement >3%, broad coverage
- Any stage → EXPIRED: no supporting evidence >5 days

**Conviction scoring:**
- Each supporting article adds conviction based on source credibility + materiality
- Conviction decay over time without fresh evidence
- Conviction influences position sizing and strategy weights

**Database writes:**
- Update `active_theses` table with new conviction, lifecycle stage
- Add record to `thesis_evidence` table linking article to thesis
- Track thesis evolution in `thesis_history` JSON field

## 8. Thesis-Driven Strategies (engine/strategies.py) — REWRITTEN

**4-Tier Strategy System** focused on thesis lifecycle timing:

**Tier 1 — Thesis Lifecycle Strategies (Primary signals):**

`first_mover_thesis`
- Target: EMERGING theses (1-2 articles, <2 cycles old)
- BUY/SELL: Follow thesis direction if implied tickers haven't moved yet
- High risk/reward: small position, wider stops, potential for large alpha
- Confidence = thesis.confidence × materiality_boost × (1 - price_change_factor)

`conviction_builder`  
- Target: DEVELOPING theses (3+ articles, 2+ sources, 1-3 cycles)
- BUY/SELL: Follow thesis direction with normal position sizing
- Bread-and-butter strategy: most trades should come from here
- Confidence = thesis.confidence × conviction_score × source_diversity_factor

`thesis_momentum`
- Target: Accelerating conviction (conviction_score increasing cycle-over-cycle)
- BUY/SELL: Add to existing positions when thesis gains momentum
- Confidence = min(conviction_delta / max_conviction_delta, 0.95)

`thesis_fade_exit`
- Target: CONSENSUS theses (6+ articles, broad coverage, price moved >3%)
- Signal: SELL existing positions (profit taking)
- The market has caught up — edge is gone
- Confidence = 0.85 (high confidence exit signal)

`counter_thesis`
- Target: New thesis directly contradicts existing thesis on same tickers
- Signal: Reduce/exit conflicted positions  
- Don't try to pick winners — uncertainty itself is the signal
- Confidence = 0.70

**Tier 2 — Sentiment Fallback (Non-thesis tickers only):**

`sentiment_price_divergence` (enhanced)
- Only fires when ticker has NO active thesis implication
- BUY: sentiment > +0.5 AND price_change_pct < +0.5%
- SELL: sentiment < -0.5 AND price_change_pct > -0.5%
- Enhanced with materiality weighting

`multi_source_consensus` (fallback)
- Direct ticker sentiment from multiple sources
- Requires: no thesis + article_count ≥ 3 + source_count ≥ 2
- Fallback for high-conviction non-thesis moves

`sentiment_momentum` (fallback)
- Historical sentiment tracking for non-thesis tickers
- BUY: sentiment delta > +0.4 AND no contradicting thesis

**Tier 3 — Technical Timing (Thesis-aware modifiers):**

`volume_confirmation` → multiplier (thesis-aware)
- volume > 2× avg on thesis ticker → 1.4× (smart money positioning)
- volume < 0.7× avg on thesis ticker → 0.6× (thesis not resonating)

`vwap_position` → directional_modifier (timing)
- price > vwap on bullish thesis → +0.15 modifier (momentum building)
- price < vwap on bullish thesis → -0.10 modifier (early/wrong timing)

`relative_strength` → directional_modifier
- thesis ticker outperforming SPY → +0.15 modifier (thesis working)
- thesis ticker underperforming → -0.10 modifier (thesis not working)

**Tier 4 — Regime-Thesis Interaction (Filters, applied in combiner):**

No strategies here — logic embedded in combiner.py:
- Risk-off + growth thesis → heavy dampen (0.4×) or kill
- Risk-off + defensive thesis → boost (1.3×)  
- Risk-on + any thesis → normal processing
- Neutral regime → no adjustment

**Position sizing by lifecycle:**
- EMERGING: 0.5× normal size (high risk, early entry)
- DEVELOPING: 1.0× normal size (validated thesis)
- CONFIRMED: 1.2× normal size (institutional confirmation)
- CONSENSUS: 0.3× normal size (exit mode)

**Enhanced run_all_strategies returns:**
```python
{
    "thesis_signals": [  # Tier 1 only
        {
            "signal": "BUY"|"SELL", 
            "confidence": float, 
            "strategy": str, 
            "thesis_id": str,
            "lifecycle_stage": str,
            "reason": str
        }
    ],
    "sentiment_signals": [  # Tier 2 fallback
        {"signal": "BUY"|"SELL", "confidence": float, "strategy": str, "reason": str}
    ],
    "technical_modifiers": [  # Tier 3 timing
        {"multiplier"|"directional_modifier": float, "modifier_name": str, "reason": str}
    ]
}
```

## 9. Regime Classifier (engine/regime.py)

Weighted indicator scoring:
- VIX (35%): <20 → +0.35, >25 → -0.35, between → 0
- SPY vs 200MA (30%): >+2% → +0.30, <-2% → -0.30, between → 0
- Yield spread 10yr-2yr (25%): >0.5% → +0.25, <-0.5% → -0.25, between → 0
- Macro news sentiment (10% base): if abs(news_score) > 0.3, add ±0.10
  If abs(news_score) > 0.6, add additional ±0.15

Final score > 0.3 → risk_on. Score < -0.3 → risk_off. Else → neutral.
Confidence = min(1.0, abs(score) / 0.5), floor 0.1.

## 10. Thesis-First Combiner (engine/combiner.py) — 4-Stage Pipeline

**Key difference**: Resolves conflicts between **theses**, not strategies. Thesis signals take precedence.

**Stage 1 — Thesis Signals Vote:**
- For each ticker: find all active theses implicating this ticker
- Weight thesis signals by:
  - Lifecycle stage: emerging=0.6, developing=1.0, confirmed=0.8, consensus=0.3
  - Conviction score: multiply by thesis.conviction_score (0-1)
  - Learned thesis weights from `weights` table (by theme)
- Resolve thesis conflicts (opposing directions on same ticker):
  - Higher-conviction thesis wins
  - If convictions close: dampen both by conflict_penalty
- Thesis vote determines primary direction + confidence

**Stage 2 — Sentiment Confirmation:**
- Direct sentiment signals confirm/contradict thesis direction
- Agreement (sentiment + thesis same direction): boost confidence × 1.15
- Disagreement: dampen confidence × 0.85  
- No thesis + strong sentiment (abs > 0.6): create fallback signal
- Apply learned sentiment strategy weights

**Stage 3 — Technical Timing:**
- Apply Tier 3 modifiers based on thesis context:
  - Volume surge on thesis ticker → accelerate entry (1.2× confidence)
  - RSI extreme (>75 / <25) → delay entry (0.8× confidence)
  - VWAP position confirms direction → boost timing (directional modifier)
- Technical signals now answer: "Is this the right TIME to act on the thesis?"

**Stage 4 — Regime-Thesis Interaction:**
- Risk-off regime:
  - Growth theses (AI, tech, crypto) → heavy dampen (0.4×) or kill if confidence < 0.7
  - Defensive theses (utilities, healthcare, staples) → boost (1.3×)
- Risk-on regime:
  - All theses → normal processing (no adjustment)
- Neutral regime:
  - Slight preference for confirmed/developing over emerging (0.9× emerging)

**Final gates:**
- Confidence clamped to [0.05, 0.95]  
- If final confidence ≤ 0.60 → HOLD (higher threshold for thesis-driven system)
- Thesis signals get lower threshold (0.55) than sentiment-only signals (0.65)

**Enhanced output:**
```python
{
    "signal": "BUY"|"SELL"|"HOLD",
    "confidence": float,
    "primary_source": "thesis"|"sentiment"|"technical",
    "thesis_id": str,  # if thesis-driven
    "lifecycle_stage": str,  # if thesis-driven  
    "contributing_strategies": list,
    "regime_adjustment": float,
    "reasoning": str
}
```

## 11. Enhanced Risk Manager (risk/manager.py)

**Thesis-aware position sizing:**
```
risk_budget   = equity × 0.02
stop_distance = price × thesis_stop_factor   # thesis-aware stop distance
base_shares   = risk_budget / stop_distance
shares        = int(base_shares × confidence_factor × regime_factor × 
                   sector_factor × lifecycle_factor)
shares        = min(shares, 500)   # hard cap
```

**New factors:**
- **lifecycle_factor**: 
  - EMERGING: 0.5 (high risk, small size)
  - DEVELOPING: 1.0 (normal size)
  - CONFIRMED: 1.2 (institutional validation)
  - CONSENSUS: 0.3 (exit mode)
- **thesis_stop_factor**:
  - Thesis trades: 0.05 (wider stops, theses take time)
  - Sentiment trades: 0.03 (tighter stops, quicker moves)

**Enhanced Stop/TP placement:**
- **Thesis-driven trades**: wider stops to account for thesis development time
  - BUY: stop_loss = price × 0.95, take_profit = price × 1.06
  - SELL: stop_loss = price × 1.05, take_profit = price × 0.94
- **Sentiment-only trades**: tighter stops for quicker moves
  - BUY: stop_loss = price × 0.97, take_profit = price × 1.03
  - SELL: stop_loss = price × 1.03, take_profit = price × 0.97

**Enhanced 7 hard rules:**
1. price ≥ $5
2. market_cap ≥ $1B (skip check if data unavailable)
3. len(open_positions) < 15 (exempt if ticker already held)
4. cash > equity × 0.20 (BUY only)
5. current_ticker_value < equity × 0.10
6. sector_exposure < equity × 0.30  
7. **Enhanced duplicate trade check**: no same-direction trade on same ticker within:
   - 2 hours for sentiment trades (quick moves)
   - 4 hours for thesis trades (may need multiple entry opportunities)

**Thesis-aware sector limits:**
- Track sector exposure by thesis theme
- Limit theme concentration: max 25% in any single thesis theme
- Emergency thesis exit: if thesis expires mid-trade, exit within 2 cycles

## 12. Executor (executor/alpaca.py)
- Uses `TradingClient` with `paper=True`
- Checks `client.get_clock().is_open` before every order
- Submits `MarketOrderRequest` with `TimeInForce.DAY`
- Returns dict with order_id, symbol, side, qty, status, filled_avg_price
- On failure: returns `{error, ticker}`, no retry within cycle

## 13. Thesis-Aware Feedback Loop

**Enhanced feedback/logger.py** — log_trade(trade_data):
- Writes to `trades` table with new fields:
  - `thesis_id` (if thesis-driven trade)
  - `lifecycle_stage` (EMERGING/DEVELOPING/CONFIRMED/CONSENSUS)
  - `signal_attribution` (thesis vs sentiment vs technical)
  - `materiality_level` (high/medium/low)
- Returns UUID for outcome tracking

**Enhanced feedback/outcomes.py** — measure_outcomes():
- **Dual measurement windows**:
  - Sentiment trades: 8 hours (quick moves)
  - Thesis trades: 3-5 days (theses take time to play out)
- **Attribution tracking**: separate outcomes by signal source
- Exit conditions (first hit):
  - stop_loss hit, take_profit hit
  - Age threshold (8hr sentiment / 120hr thesis)
  - **Thesis expiry**: if thesis expires, force exit
- WIN/LOSS/NEUTRAL same thresholds: ±1%
- Enhanced outcomes table with thesis attribution

**Enhanced feedback/weights.py:**

**Multi-dimensional weight updates**:
1. **Strategy weights** (existing): EMA update per strategy
2. **Thesis theme weights**: Track performance by thesis theme (AI, earnings, regulatory, etc.)
3. **Lifecycle stage weights**: Track performance by entry stage (EMERGING vs DEVELOPING vs CONFIRMED)
4. **Materiality accuracy**: Track materiality_classifier prediction vs actual trade impact

**Thesis-specific learning**:
```python
# Thesis theme weight update
theme_target = 1.0 if WIN else 0.0
new_theme_weight = old_theme_weight * 0.90 + theme_target * 0.10

# Lifecycle timing weight update  
lifecycle_target = 1.0 if WIN else 0.0
lifecycle_weight = old_lifecycle_weight * 0.95 + lifecycle_target * 0.05
```

│  │   stage         │  │   sentiment      │  │   (by theme)        │ │
**Enhanced circuit breaker**:
- Track separate win rates: thesis trades vs sentiment trades
- Thesis circuit breaker: trips if thesis win rate < 0.35 over 14 days
- Sentiment circuit breaker: trips if sentiment win rate < 0.40 over 7 days
- **Materiality circuit breaker**: if high-materiality classification accuracy < 0.60, 
  disable thesis extraction, fall back to sentiment-only mode

**Learning outputs**:
- Strategy weights → `weights` table
- Thesis theme weights → `thesis_weights` table  
- Lifecycle timing → `lifecycle_weights` table
- Materiality accuracy → `materiality_performance` table

## 14. Enhanced Thesis Dashboard (dashboard/app.py)
All data from Turso. Cache TTL: 30s for portfolio/positions, 60s for trades/weights, 120s for thesis data.

**New Tabs**: Positions, Trade History, **Thesis Tracker**, Performance, Signals & Regime, Discovery, Risk Controls, Settings.

**Enhanced panels:**

**Portfolio KPIs** (unchanged):
- equity, cash (%), buying power, position count vs 15 max, unrealized P&L

**Thesis Tracker** (NEW):
- **Active Theses Table**: thesis_statement, lifecycle_stage, conviction_score, implied_tickers, evidence_count, days_active
- **Lifecycle Distribution**: pie chart of theses by stage (EMERGING/DEVELOPING/CONFIRMED/CONSENSUS)
- **Thesis Performance**: win rate by theme, by lifecycle stage, by conviction level
- **Recent Evidence**: latest supporting articles per thesis with conviction contribution

**Enhanced Performance**:
- **Dual win rate charts**: thesis trades vs sentiment trades (separate 7d/14d windows)
- **Attribution pie**: trade P&L by source (thesis vs sentiment vs technical)
- **Materiality accuracy**: classification accuracy vs actual trade impact
- **Lifecycle timing**: performance by entry stage (bar chart)

**Enhanced Learned Weights**:
- **Strategy weights**: existing bar charts
- **Thesis theme weights**: AI, earnings, regulatory, M&A, etc. (bar chart)
- **Lifecycle weights**: EMERGING vs DEVELOPING vs CONFIRMED entry performance
- **Source credibility**: learned weights for news sources

**Enhanced Circuit Breaker**:
- **Multi-breaker status**: thesis breaker, sentiment breaker, materiality breaker
- **Thesis health**: 14-day thesis win rate with 35% threshold
- **Sentiment health**: 7-day sentiment win rate with 40% threshold  
- **Materiality health**: classification accuracy with 60% threshold

**Enhanced Discovery**:
- **Source breakdown**: news vs movers vs **theses** vs positions vs watchlist
- **Thesis implications**: which active theses drove ticker discovery
- **Discovery accuracy**: how many discovered tickers became trades

**New Risk Controls**:
- **Thesis concentration**: % exposure by theme, lifecycle stage
- **Thesis lifecycle alerts**: theses approaching CONSENSUS (exit warnings)
- **Stale thesis monitor**: theses with no evidence >4 days (pre-expiry warning)
```



Updated Architecture:

┌─────────────────────────────────────────────────────────────────────┐
│                         SCHEDULER/LOOP.PY                          │
│                    (15min cycles, market hours)                     │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DISCOVERY.PY                                │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │   News Mentions │  │   Market Movers │  │  Active Theses      │  │
│  │   (regex/Groq)  │  │   (S&P gainers) │  │  (implied tickers)  │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │
│             │                   │                   ▲               │
│             └───────────────────┼───────────────────┘               │
│                                 ▼                                   │
│           ┌─────────────────────────────────────────┐               │
│           │        UNIFIED TICKER LIST              │               │
│           │     (validated, prioritized, capped)    │               │
│           └─────────────────────────────────────────┘               │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      NEWS PIPELINE                                  │
│                                                                     │
│  marketaux.py  massive.py  newsapi.py  alpaca_news.py  polygon.py   │
│      │             │           │            │            │          │
│      └─────────────┼───────────┼────────────┼────────────┘          │
│                    ▼           ▼            ▼                       │
│              ┌─────────────────────────────────────┐                │
│              │         AGGREGATOR.PY               │                │
│              │   (4-step waterfall + dedup)        │                │
│              └─────────────────┬───────────────────┘                │
└──────────────────────────────────┼──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  MATERIALITY FILTER (NEW)                           │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │              materiality_classifier.py                          ││
│  │                                                                 ││
│  │  Stage 1: RULES-BASED HIGH DETECTION                            ││
│  │  ┌─────────────────────────────────────────────────────────────┐││
│  │  │ Keywords: earnings, guidance, CEO, merger, FDA, regulatory  │││
│  │  │ Multi-ticker: >= 3 tickers → medium                        │ ││
│  │  │ Title patterns: "announces", "reports", "files"            │ ││
│  │  └─────────────────────────────────────────────────────────────┘││
│  │                              │                                  ││
│  │  Stage 2: SOURCE-BASED BOOST │                                  ││
│  │  ┌─────────────────────────────▼─────────────────────────────── ││
│  │  │ Premium sources: WSJ, Bloomberg, Reuters → upgrade to medium│ │ │
│  │  │ Pre-scored: Marketaux, Massive → medium                    │ │ │
│  │  │ Institutional: Alpaca/Benzinga, Polygon → high             │ │ │
│  │  └─────────────────────────────┬─────────────────────────────────┘ │ │
│  │                              │                                  │ │
│  │  Stage 3: CLAUDE REFINEMENT (edge cases only)                  │ │
│  │  ┌─────────────────────────────▼─────────────────────────────────┐ │ │
│  │  │ IF rules_score == "unknown" OR borderline cases            │ │ │
│  │  │ → quick Claude call: "medium vs low materiality?"          │ │ │
│  │  │ ELSE: skip Claude (cost control)                           │ │ │
│  │  └─────────────────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                   │                                  │
│  OUTPUT: articles tagged with materiality → route to analysis       │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 COMPREHENSIVE ANALYSIS                              │
│              (REPLACES sentiment.py)                                │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │              analysis.py  (router/orchestrator)                 │ │
│  │                                                                 │ │
│  │  ┌─────────────────────┐         ┌─────────────────────────────┐ │ │
│  │  │  HIGH MATERIALITY   │         │   MEDIUM/LOW MATERIALITY    │ │ │
│  │  │                     │         │                             │ │ │
│  │  │  delegates to →     │         │ BASIC ANALYSIS (in-module): │ │ │
│  │  │ thesis_extractor.py │         │ • Simple ticker sentiment   │ │ │
│  │  │ (FULL CLAUDE CALL): │         │ • Direction + confidence    │ │ │
│  │  │ • Thesis statement  │         │ • Use pre-scored if avail   │ │ │
│  │  │ • Theme/mechanism   │         │   (Marketaux/Massive)       │ │ │
│  │  │ • Implied tickers   │         │ • Lightweight Haiku call    │ │ │
│  │  │ • Direct sentiment  │         │   for newsapi/alpaca/polygon│ │ │
│  │  │ • Time horizon      │         │                             │ │ │
│  │  └─────────────────────┘         └─────────────────────────────┘ │ │
│  │             │                                │                   │ │
│  └─────────────┼────────────────────────────────┼───────────────────┘ │
│                │                                │                     │
│   thesis obj → thesis_lifecycle.py              │                     │
│                │                    sentiment → sentiment_scores tbl  │
└────────────────┼────────────────────────────────┼─────────────────────┘
                 │                                │
                 ▼                                │
┌─────────────────────────────────────────────────┼─────────────────────┐
│                THESIS MANAGEMENT                │                     │
│                                                 │                     │
│  ┌─────────────────────────────────────────────┐│                     │
│  │            thesis_lifecycle.py              ││                     │
│  │                                             ││                     │
│  │ 1. Match against existing active_theses:    ││                     │
│  │    • Theme similarity (keywords)            ││                     │
│  │    • Ticker overlap                         ││                     │
│  │    • Mechanism similarity                   ││                     │
│  │                                             ││                     │
│  │ 2. IF MATCH FOUND:                          ││                     │
│  │    • Add to thesis_evidence table           ││                     │
│  │    • Update conviction_score                ││                     │
│  │    • Evolve thesis_statement                ││                     │
│  │    • Check lifecycle transition:            ││                     │
│  │      emerging→developing→confirmed→consensus ││                     │
│  │                                             ││                     │
│  │ 3. IF NO MATCH:                             ││                     │
│  │    • Create new thesis (EMERGING state)     ││                     │
│  │                                             ││                     │
│  │ 4. Expire old theses (no evidence >5 days)  ││                     │
│  └─────────────────────────────────────────────┘│                     │
│                              │                  │                     │
└──────────────────────────────┼──────────────────┼─────────────────────┘
                               │                  │
                               ▼                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 ENHANCED DATABASE                                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    active_theses                                │ │
│  │ • id, thesis_statement, theme, direction, mechanism             │ │
│  │ • lifecycle_stage, confidence_score, conviction_score           │ │
│  │ • tickers (JSON), sectors (JSON), time_horizon                  │ │
│  │ • created_at, last_updated, expires_at                          │ │
│  │ • thesis_history (JSON), evidence_count, source_diversity       │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                  thesis_evidence                                │ │
│  │ • thesis_id, article_url, source, published_at                 │ │
│  │ • added_conviction, reasoning, materiality                      │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ sentiment_scores (for non-thesis articles)                     │ │
│  │ • ticker, sentiment_score, urgency, materiality, reasoning     │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  THESIS-DRIVEN STRATEGIES                          │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                  strategies.py (REWRITTEN)                     │ │
│  │                                                                 │ │
│  │ TIER 1 - THESIS LIFECYCLE STRATEGIES:                          │ │
│  │ ┌─────────────┐┌─────────────┐┌─────────────┐┌─────────────────┐ │ │
│  │ │first_mover  ││conviction   ││thesis       ││thesis_fade      │ │ │
│  │ │_thesis      ││_builder     ││_momentum    ││_exit            │ │ │
│  │ │             ││             ││             ││                 │ │ │
│  │ │EMERGING     ││DEVELOPING   ││Accelerating ││CONSENSUS        │ │ │
│  │ │1-2 articles ││3+ articles  ││conviction   ││/EXPIRED         │ │ │
│  │ │High risk/   ││2+ sources   ││growing      ││Take profit      │ │ │
│  │ │reward       ││Normal size  ││Add position ││Exit signal      │ │ │
│  │ └─────────────┘└─────────────┘└─────────────┘└─────────────────┘ │ │
│  │                                                                 │ │
│  │ TIER 2 - SENTIMENT FALLBACK:                                   │ │
│  │ ┌─────────────┐┌─────────────┐┌─────────────┐                  │ │
│  │ │sentiment    ││multi_source ││sentiment    │                  │ │
│  │ │_divergence  ││_consensus   ││_momentum    │                  │ │
│  │ │(enhanced)   ││(fallback)   ││(fallback)   │                  │ │
│  │ │             ││             ││             │                  │ │
│  │ │For non-     ││Direct ticker││Historical   │                  │ │ │
│  │ │thesis tickers││sentiment    ││sentiment    │                  │ │
│  │ └─────────────┘└─────────────┘└─────────────┘                  │ │
│  │                                                                 │ │
│  │ TIER 3 - TECHNICAL TIMING:                                     │ │
│  │ ┌─────────────┐┌─────────────┐┌─────────────┐                  │ │
│  │ │volume       ││vwap         ││relative     │                  │ │
│  │ │_confirmation││_position    ││_strength    │                  │ │
│  │ │"act now?"   ││"act now?"   ││"act now?"   │                  │ │
│  │ └─────────────┘└─────────────┘└─────────────┘                  │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   THESIS-FIRST COMBINER                            │
│                                                                     │
│  Stage 1: THESIS SIGNALS VOTE                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ For each ticker:                                                │ │
│  │ • Find active theses implicating this ticker                   │ │
│  │ • Weight by lifecycle: emerging=0.6, developing=1.0,           │ │
│  │   confirmed=0.8, consensus=0.3                                 │ │
│  │ • Weight by conviction_score                                    │ │
│  │ • Resolve thesis conflicts (opposing directions)               │ │
│  └─────────────────────┬───────────────────────────────────────────┘ │
│                        │                                            │
│  Stage 2: SENTIMENT CONFIRMATION                                   │
│  ┌─────────────────────▼───────────────────────────────────────────┐ │
│  │ • Direct sentiment signals confirm/contradict thesis direction │ │
│  │ • Agreement: boost confidence                                   │ │
│  │ • Disagreement: dampen confidence                              │ │
│  │ • No thesis + strong sentiment: fallback signal                │ │
│  └─────────────────────┬───────────────────────────────────────────┘ │
│                        │                                            │
│  Stage 3: TECHNICAL TIMING                                         │
│  ┌─────────────────────▼───────────────────────────────────────────┐ │
│  │ • Volume surge on thesis ticker → accelerate                   │ │
│  │ • RSI extreme → delay entry                                     │ │
│  │ • VWAP position → fine-tune timing                              │ │
│  └─────────────────────┬───────────────────────────────────────────┘ │
│                        │                                            │
│  Stage 4: REGIME-THESIS INTERACTION                                │
│  ┌─────────────────────▼───────────────────────────────────────────┐ │
│  │ • Risk-off + growth thesis → heavy dampen/kill                 │ │
│  │ • Risk-off + defensive thesis → boost                          │ │
│  │ • Risk-on + any thesis → normal processing                     │ │
│  │ • Select which thesis categories are actionable                │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│              RISK/MANAGER.PY (enhanced)                             │
│                                                                     │
│ Position sizing now considers thesis lifecycle:                     │
│ • EMERGING thesis → 0.5x normal size (high risk)                   │
│ • DEVELOPING thesis → 1.0x normal size                             │
│ • CONFIRMED thesis → 1.2x normal size                              │ │
│ • CONSENSUS thesis → 0.3x normal size (exit signal)                │ │
│                                                                     │
│ Same 7 hard rules, but thesis-aware stops:                         │
│ • Thesis-driven trades → wider stops (thesis may take time)        │
│ • Sentiment-only trades → tighter stops (quicker moves)            │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                EXECUTOR/ALPACA.PY                                   │
│                   (unchanged)                                       │
└─────────────────────┬───────────────────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                THESIS-AWARE FEEDBACK                                │
│                                                                     │
│  ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────────┐ │
│  │  LOGGER.PY      │  │   OUTCOMES.PY    │  │    WEIGHTS.PY       │ │
│  │  (enhanced)     │  │   (enhanced)     │  │    (enhanced)       │ │
│  │                 │  │                  │  │                     │ │
│  │ Log trades with:│  │ Dual measurement │  │ Update weights for: │ │
│  │ • thesis_id     │  │ windows:         │  │ • Strategy weights  │ │
│  │ • lifecycle_    │  │ • 8hr for        │  │ • THESIS weights    │ │
│  │   stage         │  │   sentiment      │  │   (by theme)        │ │
│  │ • signal_       │  │ • 3-5 days for   │  │ • Lifecycle stage   │ │
│  │   attribution   │  │   thesis trades  │  │   weights           │ │
│  │                 │  │                  │  │ • Materiality       │ │
│  │                 │  │ Attribution to   │  │   classification    │ │
│  │                 │  │ thesis vs        │  │   accuracy          │ │
│  │                 │  │ sentiment        │  │                     │ │
│  └─────────────────┘  └──────────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘