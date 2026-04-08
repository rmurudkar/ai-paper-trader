---
name: api-contracts
description: >
  Exact input/output shapes for every module in the paper trader pipeline.
  Load this when wiring modules together, debugging data passing between modules,
  checking what fields are available downstream, or verifying a function's return shape.
  Trigger when the user asks "what does X return", "what does Y expect", "how do I
  connect X to Y", or when implementing the scheduler loop.
---

# API Contracts — Module Input/Output Shapes

This file documents the exact data shapes passed between modules.
When in doubt about a field name or type, treat this as the source of truth.

---

## fetchers/discovery.py

### discover_tickers(db_path, is_premarket) → dict
```python
{
    "tickers": List[str],           # e.g. ["AAPL", "NVDA", "XOM"]
    "sources": Dict[str, List[str]],# ticker → why included
                                    # sources: "news"|"gainer"|"loser"|
                                    #          "sector_rotation"|"position"|
                                    #          "watchlist"|"fallback"
    "mode": str,                    # "discovery"|"watchlist"|"fallback"
    "cycle_id": str                 # e.g. "20260405_143022"
}
```

---

## fetchers/marketaux.py

### fetch_news(tickers, max_results, broad) → List[dict]
Each item:
```python
{
    "title": str,
    "ticker": str,                  # primary ticker for this item
    "sentiment_score": float,       # -1.0 to 1.0 — use directly, no Claude
    "snippet": str,
    "description": str,             # longer form content from Marketaux
    "url": str,
    "published_at": str,            # ISO 8601
    "source": "marketaux"           # always this literal string
}
```

---

## fetchers/massive.py

### fetch_news(tickers, max_results) → List[dict]
Each item:
```python
{
    "title": str,
    "description": str,
    "tickers": List[str],           # all tickers tagged on this article
    "sentiment_score": float,       # -1.0 to 1.0 — use directly, no Claude
    "url": str,
    "published_at": str,
    "author": str,
    "source": "massive"
}
```

---

## fetchers/newsapi.py

### fetch_headlines(topics, max_results, discovery_context, watchlist, broad) → List[dict]
Each item:
```python
{
    "title": str,
    "snippet": str,                 # body[:500], no full text yet
    "topics": List[str],            # e.g. ["macro"] or ["geopolitical"]
    "url": str,
    "published_at": str,
    "source": "newsapi",
    "needs_full_text": True,        # always True — for waterfall enrichment
    "tickers": List[str],           # extracted via regex + optional Groq
    "extraction_confidence": float  # 0.0–1.0
}
```

---

## fetchers/alpaca_news.py

### fetch_news(watchlist, max_results) → List[dict]
Each item:
```python
{
    "title": str,
    "full_text": str,               # HTML-stripped, ≤1200 words
    "snippet": str,                 # summary field
    "ticker": str,                  # first symbol
    "tickers": List[str],           # all symbols
    "url": str,
    "published_at": str,
    "source": "alpaca",
    "author": str,
    "partial": bool                 # True if full_text is empty (only snippet available)
}
```

---

## fetchers/polygon.py

### fetch_full_text(url) → dict | None
Returns None if no URL match found.
```python
{
    "title": str,
    "full_text": str,               # ≤1200 words
    "publisher": str,
    "published_at": str,
    "tickers": List[str],
    "url": str,
    "source": "polygon",
    "partial": False                # always False when returned (not None)
}
```

---

## fetchers/scraper.py

### scrape(url, snippet) → dict
```python
{
    "full_text": str,               # extracted text or snippet fallback
    "partial": bool,                # True if using snippet fallback
    "extraction_method": str,       # "trafilatura"|"newspaper"|"beautifulsoup"|"snippet"
    "word_count": int,
    "extraction_time_ms": int
}
```

---

## fetchers/aggregator.py

### fetch_all_news(max_marketaux, max_massive, max_newsapi, max_alpaca, watchlist, discovery_context) → List[dict]
Returns unified, deduplicated, waterfall-enriched list sorted by published_at desc.

Each item has at minimum:
```python
{
    "title": str,
    "url": str,
    "source": str,                  # "marketaux"|"massive"|"newsapi"|"alpaca"|"polygon"
    "published_at": str,
    "partial": bool,
    # Plus source-specific fields (full_text, snippet, tickers, sentiment_score, etc.)
}
```

NewsAPI items after waterfall enrichment add:
```python
{
    "full_text": str,               # populated if any waterfall step succeeded
    "partial": bool,                # True only if all 3 enrichment steps failed
    # needs_full_text key removed after waterfall
}
```

---

## engine/sentiment.py

### batch_analyze_articles(articles) → List[dict]
Input: list of article dicts from aggregator (any source).
Output — one item per ticker per article:
```python
{
    "ticker": str,
    "sentiment_score": float,       # -1.0 to 1.0
    "source": str,                  # inherited from article
    "reasoning": str,
    "urgency": str,                 # "breaking"|"developing"|"standard"
    "materiality": str,             # "high"|"medium"|"low"|"unknown"
    "time_horizon": str,            # "intraday"|"short_term"|"medium_term"|"long_term"
    "published_at": str
}
```

### get_ticker_sentiment_scores(ticker, sentiment_results) → dict
Pure aggregation, no DB writes.
```python
{
    "ticker": str,
    "sentiment_score": float,       # weighted average
    "article_count": int,
    "source_breakdown": Dict[str, int],  # source → count
    "confidence": float,
    "individual_scores": List[float],
    "individual_weights": List[float],
    "urgency": str,                 # dominant across articles
    "materiality": str,             # highest across articles
    "time_horizon": str             # shortest across articles
}
# Returns {"ticker", "sentiment_score":0.0, "article_count":0, ...} if no data
```

### record_ticker_sentiment(ticker, sentiment_results) → dict
Aggregates + reads/writes `sentiment_history` table. Returns get_ticker_sentiment_scores
output plus:
```python
{
    # ...all fields from get_ticker_sentiment_scores...,
    "sentiment_delta": float | None,    # current - previous, None if first cycle
    "previous_score": float | None,
    "previous_recorded_at": str | None,
    "delta_direction": str              # "bullish_shift"|"bearish_shift"|"stable"
}
```

### batch_record_sentiments(tickers, sentiment_results) → Dict[str, dict]
Calls record_ticker_sentiment for each ticker.
Returns: `{ticker_str: record_ticker_sentiment_output}`

---

## engine/strategies.py

### run_all_strategies(ticker, market_data, sentiment_data, macro_data) → dict

**market_data** (from fetchers/market.py per-ticker output):
```python
{
    "price": float,
    "prev_close": float,
    "day_high": float,
    "day_low": float,
    "volume": int,
    "ma_20": float | None,
    "ma_50": float | None,
    "ma_200": float | None,
    "rsi": float | None,            # 0–100
    "price_change_pct": float,
    "avg_volume_20": float,
    "vwap": float | None,
    "last_updated": str
}
```

**sentiment_data**: output of record_ticker_sentiment() or get_ticker_sentiment_scores()

**macro_data** (from fetchers/market.py macro output):
```python
{
    "vix": float,
    "spy_price": float,
    "spy_ma_200": float,
    "spy_vs_200ma": float,          # (spy_price - spy_ma_200) / spy_ma_200
    "spy_change_pct": float,        # used by relative_strength modifier
    "yield_10y": float,
    "yield_2y": float,
    "yield_spread": float           # yield_2y - yield_10y
}
```

**Returns:**
```python
{
    "signals": [
        {
            "signal": "BUY" | "SELL",   # never HOLD (filtered out)
            "confidence": float,         # 0.05–0.95
            "strategy": str,             # strategy name
            "reason": str
        }
    ],
    "modifiers": [
        {
            # One of:
            "multiplier": float,              # Cat 2 volume_confirmation
            "directional_modifier": float,    # Cat 2 vwap_position, relative_strength
            # Always:
            "modifier_name": str,
            "reason": str
        }
    ]
}
```

---

## engine/regime.py

### get_current_regime(macro_data, macro_news_score) → dict
```python
{
    "regime": "risk_on" | "risk_off" | "neutral",
    "vix": float | None,
    "spy_vs_200ma": float | None,
    "yield_spread": float | None,
    "macro_sentiment": "positive" | "negative" | "neutral",
    "confidence": float             # 0.1–1.0
}
```

---

## engine/combiner.py

### combine_ticker_signals(ticker, raw_output, regime_data, learned_weights) → dict

**raw_output**: output of run_all_strategies()
**regime_data**: output of get_current_regime()
**learned_weights**: output of get_all_weights("strategy") from db/client.py

**Returns:**
```python
{
    "ticker": str,
    "signal": "BUY" | "SELL" | "HOLD",
    "confidence": float,            # 0.05–0.95, 0.0 if HOLD
    "components": {
        strategy_name: {
            "signal": str,
            "raw_confidence": float,
            "weight": float
        }
    },
    "modifiers_applied": List[str], # reason strings from Stage 2 modifiers
    "cat3_effect": str | None,      # e.g. "+0.14 (drift confirms BUY)"
    "regime": str,
    "regime_adjustment": str,       # e.g. "Risk-off: BUY dampened 30%"
    "rationale": str                # human-readable summary
}
```

### load_learned_weights() → Dict[str, float]
Reads `weights` table for category="strategy". Returns `{}` on DB error.

---

## risk/manager.py

### check_trade(signal, portfolio, market_data) → dict

**signal** (output of combine_ticker_signals):
```python
{
    "ticker": str,
    "signal": "BUY" | "SELL",
    "confidence": float,
    "regime": str               # used for regime_factor in sizing
}
```

**portfolio** (from executor/alpaca.py get_portfolio):
```python
{
    "cash": float,
    "equity": float,
    "buying_power": float,
    "positions": [
        {
            "ticker": str,
            "qty": float,
            "market_value": float,
            "unrealized_pl": float,
            "current_price": float,
            "avg_entry_price": float,
            "side": str
        }
    ]
}
```

**market_data**: per-ticker dict from fetchers/market.py (needs at minimum `price` and optionally `market_cap`)

**Returns (approved):**
```python
{
    "approved": True,
    "reason": "",
    "position_size": int,           # same as shares
    "shares": int,
    "entry_price": float,
    "stop_loss": float,
    "take_profit": float,
    "portfolio_allocation_pct": float   # e.g. 2.1 means 2.1% of portfolio
}
```

**Returns (rejected):**
```python
{
    "approved": False,
    "reason": str,                  # human-readable rejection reason
    "position_size": 0,
    "shares": 0,
    "entry_price": 0.0,
    "stop_loss": 0.0,
    "take_profit": 0.0,
    "portfolio_allocation_pct": 0.0
}
```

---

## executor/alpaca.py

### place_order(ticker, qty, side) → dict
```python
# Success:
{
    "order_id": str,
    "symbol": str,
    "side": str,                    # "buy" | "sell"
    "qty": str,
    "status": str,
    "filled_avg_price": str | None,
    "submitted_at": str
}

# Failure or market closed:
{
    "error": str,
    "ticker": str
}
```

### get_portfolio() → dict
See risk/manager.py portfolio shape above.

### get_positions() → List[dict]
See positions list in portfolio shape above.

### is_market_open() → bool

---

## feedback/logger.py

### log_trade(trade_data) → str
trade_data keys (all optional except ticker/signal/entry_price/shares):
```python
{
    "ticker": str,
    "signal": "BUY" | "SELL",
    "confidence": float,
    "sentiment_score": float | None,
    "sentiment_source": str | None,
    "strategies_fired": List[str],
    "discovery_sources": List[str],
    "regime_mode": str | None,
    "article_urls": List[str],
    "entry_price": float,
    "shares": int,
    "stop_loss_price": float | None,
    "take_profit_price": float | None,
    "order_id": str | None
}
```
Returns: UUID string (trade_id). Returns "" on failure.

---

## feedback/outcomes.py

### measure_outcomes() → List[dict]
Returns list of outcome dicts for trades that were closed this run:
```python
{
    "trade_id": str,
    "ticker": str,
    "signal": str,
    "outcome": "WIN" | "LOSS" | "NEUTRAL",
    "return_pct": float,
    "exit_price": float,
    "holding_period_hours": float,
    "exit_reason": "stop_loss" | "take_profit" | "holding_period",
    "measured_at": str,
    # Attribution for weight updates:
    "strategies_fired": List[str],
    "sentiment_source": str | None,
    "discovery_sources": List[str]
}
```

---

## feedback/weights.py

### update_weights(outcome) → None
Input: one item from measure_outcomes() output.
Side effects: updates `weights` table, may trip `circuit_breaker` table.

### check_circuit_breaker() → bool
Returns True if circuit breaker SHOULD trip (not if already tripped).
Returns False if already tripped (to avoid re-tripping).

---

## fetchers/market.py

### fetch_market_data(tickers, include_sector_etfs) → dict
```python
{
    "tickers": {
        "AAPL": {
            "price": float,
            "prev_close": float,
            "day_high": float | None,
            "day_low": float | None,
            "volume": int,
            "ma_20": float | None,    # None if <20 days history
            "ma_50": float | None,    # None if <50 days history
            "ma_200": float | None,   # None if <200 days history
            "rsi": float | None,      # None if <15 days history
            "price_change_pct": float,
            "avg_volume_20": float,   # falls back to current volume if <20 days
            "vwap": float | None,     # (high + low + close) / 3
            "last_updated": str
        }
        # ... one entry per requested ticker
    },
    "macro": {
        "vix": float,
        "spy_price": float,
        "spy_ma_200": float | None,
        "spy_vs_200ma": float | None,   # (spy_price - ma200) / ma200
        "spy_change_pct": float,
        "yield_10y": float,
        "yield_2y": float,              # actually 3-month treasury as proxy
        "yield_spread": float           # yield_2y - yield_10y
    },
    "sector_etfs": {                    # None if include_sector_etfs=False
        "XLK": {
            "sector": "Technology",
            "price": float,
            "change_pct": float,
            "volume_vs_avg": float
        }
        # ... one entry per ETF
    } | None
}
```

### fetch_sp500_tickers() → List[str]
Returns ~500 ticker strings. Sources in order: Wikipedia scrape → Turso sp500_cache → static fallback list of 100.

---

## Canonical Pipeline Wiring

This is how the scheduler loop should connect modules:

```python
# 1. Discovery
discovery = discover_tickers(db_path, is_premarket)
tickers = discovery["tickers"]

# 2. Fetch data
market_result = fetch_market_data(tickers, include_sector_etfs=is_premarket)
articles = fetch_all_news(watchlist=tickers, discovery_context=discovery)

# 3. Sentiment
sentiment_results = batch_analyze_articles(articles)
ticker_sentiments = batch_record_sentiments(tickers, sentiment_results)

# 4. Regime (once per cycle)
macro_news_score = ... # aggregate sentiment from macro-topic articles
regime = get_current_regime(market_result["macro"], macro_news_score)

# 5. Strategies + combine (per ticker)
learned_weights = load_learned_weights()
portfolio = get_portfolio()

for ticker in tickers:
    market_data = market_result["tickers"].get(ticker)
    if not market_data:
        continue

    sentiment_data = ticker_sentiments.get(ticker)
    raw = run_all_strategies(ticker, market_data, sentiment_data, market_result["macro"])
    signal = combine_ticker_signals(ticker, raw, regime, learned_weights)

    if signal["signal"] == "HOLD":
        continue

    # 6. Risk check
    approval = check_trade(signal, portfolio, market_data)
    if not approval["approved"]:
        continue

    # 7. Execute
    order = place_order(ticker, approval["shares"], signal["signal"].lower())
    if "error" in order:
        continue

    # 8. Log
    trade_id = log_trade({
        "ticker": ticker,
        "signal": signal["signal"],
        "confidence": signal["confidence"],
        "sentiment_score": sentiment_data.get("sentiment_score") if sentiment_data else None,
        "sentiment_source": ...,
        "strategies_fired": [s for s in signal["components"]],
        "discovery_sources": discovery["sources"].get(ticker, []),
        "regime_mode": regime["regime"],
        "article_urls": ...,
        "entry_price": approval["entry_price"],
        "shares": approval["shares"],
        "stop_loss_price": approval["stop_loss"],
        "take_profit_price": approval["take_profit"],
        "order_id": order["order_id"]
    })
```