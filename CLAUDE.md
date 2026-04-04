# Paper Trading App

Python app that fetches financial and macroeconomic news via licensed APIs,
analyzes sentiment with Claude API, generates buy/sell signals, and executes
paper trades via Alpaca. No real trades — simulation only.

## Stack
- Python 3.11+
- Anthropic SDK (sentiment engine)
- Alpaca SDK (paper trading + Benzinga news feed)
- Marketaux API (stock-tagged news + pre-built sentiment scores)
- NewsAPI.ai (macro, geopolitical, economic headlines)
- Polygon.io (licensed full article text — Reuters, AP, Benzinga)
- yfinance (price, volume, moving averages)
- trafilatura (scraper fallback only — step 3 in waterfall)
- newspaper3k (scraper fallback only — after trafilatura fails)
- Streamlit (dashboard)
- plotly (performance charts)
- python-dotenv

## Project Structure
paper-trader/
├── fetchers/
│   ├── marketaux.py      # Marketaux API — stock news + pre-built sentiment
│   ├── newsapi.py        # NewsAPI.ai — macro/geopolitical headlines + snippets
│   ├── polygon.py        # Polygon.io — full licensed article text (step 1)
│   ├── alpaca_news.py    # Alpaca News — Benzinga feed (step 2)
│   ├── scraper.py        # Fallback scraper — only when steps 1+2 fail (step 3)
│   ├── market.py         # yfinance — price, volume, moving averages
│   └── aggregator.py     # Waterfall enrichment + dedup + merge
├── engine/
│   ├── sentiment.py      # Claude sentiment analysis
│   └── signals.py        # Buy/sell/hold signal logic
├── executor/
│   └── alpaca.py         # Alpaca paper trade executor
├── dashboard/
│   └── app.py            # Streamlit UI
├── .env.example
├── requirements.txt
└── CLAUDE.md

## Environment Variables (.env)
ANTHROPIC_API_KEY=
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets
MARKETAUX_API_KEY=
NEWSAPI_AI_KEY=
POLYGON_API_KEY=

## Data Flow (strict order)

### 1. fetchers/marketaux.py
- Fetch ticker-tagged financial news from Marketaux API
- Extract pre-built sentiment_score per ticker (-1.0 to 1.0)
- DO NOT re-analyze with Claude — scores are trusted and used directly
- Returns: list of {title, ticker, sentiment_score, snippet, url, published_at, source:"marketaux"}

### 2. fetchers/newsapi.py
- Fetch macro/geopolitical/economic headlines from NewsAPI.ai
- Returns headlines + snippets only — no full text from this API
- Every item flagged needs_full_text: true for waterfall enrichment
- Returns: list of {title, snippet, topics, url, published_at, source:"newsapi", needs_full_text:true}

### 3. fetchers/polygon.py
- Fetch full licensed article text from Polygon.io news endpoint
- Called by aggregator waterfall as step 1 full-text enrichment
- Match articles by URL or headline to NewsAPI.ai items
- Also fetch Polygon's own ticker-filtered news feed independently
- Truncate body to 1200 words max
- API: GET https://api.polygon.io/v2/reference/news
- Returns: {full_text, publisher, published_at, tickers, url, partial:false}

### 4. fetchers/alpaca_news.py
- Fetch Benzinga news via Alpaca News API (free with Alpaca account)
- Called by aggregator waterfall as step 2 full-text enrichment
- Filter by watchlist tickers
- Returns: list of {title, full_text, ticker, url, published_at, source:"alpaca", partial:false}

### 5. fetchers/scraper.py
- FALLBACK ONLY — called by aggregator when steps 1 and 2 find no full text
- Never called directly from anywhere except aggregator.py
- Skip list (return snippet only): wsj.com, ft.com, bloomberg.com, nytimes.com
- Primary: trafilatura.fetch_url() + trafilatura.extract()
- Fallback: newspaper3k Article().parse()
- If both fail: return snippet, set partial:true
- Truncate to 1200 words max
- Returns: {full_text, partial:bool}

### 6. fetchers/market.py
- Fetch per ticker in watchlist via yfinance:
  current price, volume, 50-day MA, 200-day MA
- Returns: dict keyed by ticker symbol

### 7. fetchers/aggregator.py
- WATERFALL ENRICHMENT for each NewsAPI.ai article:
    Step 1: look up full text in Polygon.io  → if found, enrich + mark partial:false
    Step 2: look up full text in Alpaca News → if found, enrich + mark partial:false
    Step 3: call scraper.py                  → if found, enrich + mark partial:false
    Step 4: use snippet only                 → mark partial:true, flag for limited analysis
- Merge all sources: Marketaux + NewsAPI (enriched) + Polygon feed + Alpaca News
- Deduplicate: exact URL match first, then title similarity > 80%
- Sort by published_at descending
- Returns: single unified list, each item has full_text or snippet + partial flag

### 8. engine/sentiment.py
- Marketaux items: use sentiment_score directly, skip Claude entirely
- partial:true items: send snippet with lower confidence weighting
- All other items: send full_text to Claude (never raw headline)
- Always truncate to 1200 words before Claude call
- Claude prompt: analyze sentiment relative to specific tickers
- Returns: sentiment score -1.0 to 1.0 per ticker, confidence 0.0 to 1.0

### 9. engine/signals.py
- Combine sentiment scores + market data (price, MA crossovers)
- Penalize signals derived from partial:true articles
- Generate: BUY | SELL | HOLD per ticker
- Confidence score 0.0 to 1.0
- Only forward confidence > 0.7 to executor
- Returns: list of {ticker, signal, confidence, reasoning}

### 10. executor/alpaca.py
- Submit paper trade orders via Alpaca SDK
- Market orders only for now
- Always use ALPACA_BASE_URL from env — paper trading only
- Log every order: timestamp, ticker, signal, confidence, order_id

### 11. dashboard/app.py
- Streamlit UI:
  - Portfolio: open positions, current P&L
  - Signal Feed: latest signals with reasoning
  - News Feed: articles with sentiment, source, partial flag shown
  - Trade History: all executed paper trades
  - Performance: P&L chart over time (Plotly)

## Key Rules for Claude Code
- NEVER send raw headlines to Claude — always full_text, fall back to snippet only if partial:true
- NEVER re-analyze Marketaux sentiment scores with Claude
- NEVER call scraper.py directly — only via aggregator waterfall
- NEVER scrape: wsj.com, ft.com, bloomberg.com, nytimes.com
- NEVER use live trading URL — always paper-api.alpaca.markets
- ALWAYS truncate to 1200 words before any Claude call
- ALWAYS deduplicate before analysis
- ALWAYS load keys from .env, never hardcode
- ALWAYS penalize partial:true articles in signal confidence scoring

## Fetcher Public API (one function per fetcher — strictly enforced)
- fetchers/marketaux.py  → fetch_news(tickers: list[str] = None) -> list[dict]
- fetchers/newsapi.py    → fetch_headlines(topics: list[str] = None) -> list[dict]
- fetchers/polygon.py    → fetch_full_text(url: str) -> dict
- fetchers/alpaca_news.py → fetch_news(tickers: list[str] = None) -> list[dict]
- fetchers/scraper.py    → scrape(url: str) -> dict
- fetchers/market.py     → fetch_market_data(tickers: list[str]) -> dict
- fetchers/aggregator.py → aggregate(tickers: list[str]) -> list[dict]

Each fetcher exposes exactly ONE public function. No additional public functions.
Any helper logic must be in private functions prefixed with underscore e.g. _parse_response()
## Default Watchlist
AAPL, MSFT, NVDA, GOOGL, AMZN, TSLA, JPM, SPY, QQQ

## requirements.txt
anthropic
alpaca-trade-api
yfinance
requests
trafilatura
newspaper3k
streamlit
plotly
python-dotenv
