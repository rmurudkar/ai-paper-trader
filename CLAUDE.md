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
