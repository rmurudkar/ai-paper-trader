# Paper Trading App
Python app that fetches financial news, analyzes sentiment with Claude API, generates buy/sell signals, and executes paper trades via Alpaca.

## Stack
- Python 3.11+
- Anthropic SDK (sentiment engine)
- Alpaca SDK (paper trading)
- NewsAPI (headlines)
- yfinance (market data)
- Streamlit (dashboard)

## Structure
- fetchers/news.py — NewsAPI client
- fetchers/market.py — yfinance client
- engine/sentiment.py — Claude sentiment analysis
- engine/signals.py — buy/sell signal logic
- executor/alpaca.py — Alpaca paper trade executor
- dashboard/app.py — Streamlit UI
- .env.example — API key template
- requirements.txt

## News Fetching Architecture (updated)
Split fetchers/news.py into three files:

- fetchers/marketaux.py — Marketaux API client
  - fetch ticker-tagged stock/financial news
  - extract pre-built sentiment scores per ticker (do NOT re-analyze these with Claude)
  - returns: list of {title, ticker, sentiment_score, url, published_at}

- fetchers/newsapi.py — NewsAPI.ai client  
  - fetch macro, geopolitical, economic headlines
  - no pre-built sentiment — these go to Claude for analysis
  - returns: list of {title, summary, topics, url, published_at}

- fetchers/aggregator.py — merge + dedup
  - combines output of both fetchers
  - deduplicates by URL and title similarity
  - tags each item with source: "marketaux" | "newsapi"
  - returns unified list for engine/sentiment.py

Remove: fetchers/news.py (replaced by above 3 files)
Update: any imports in engine/sentiment.py to use aggregator output
