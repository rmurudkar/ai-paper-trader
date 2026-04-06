"""Groq Llama 3.1 8B client for company name → ticker extraction.

Uses Groq's free tier (500K tokens/day, 14,400 requests/day) to extract
company names from article text and resolve them to ticker symbols.

This is a reusable component used by discovery.py and newsapi.py to improve
ticker discovery from articles that mention company names without explicit
ticker symbols (e.g., "Apple reported earnings" → AAPL).

Setup:
    1. Sign up at https://console.groq.com
    2. Create an API key
    3. Add GROQ_API_KEY=your_key to .env
"""

import os
import json
import logging
import requests
from typing import List, Dict, Set, Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

# Track daily usage to warn before hitting free tier limits
_daily_request_count = 0
_daily_request_date = None

_SYSTEM_PROMPT = """You are a financial entity extractor. Given article text, extract all company names and return their US stock ticker symbols.

Rules:
- Only return companies traded on US exchanges (NYSE, NASDAQ)
- Return JSON array of objects: [{"company": "Apple Inc", "ticker": "AAPL"}]
- If a ticker symbol is already mentioned (e.g., $AAPL, NASDAQ:AAPL), include it
- If only a company name is mentioned (e.g., "Apple", "Nvidia", "Berkshire Hathaway"), resolve it to the correct ticker
- Do NOT guess or hallucinate tickers — only return companies you are confident about
- If no companies are found, return an empty array: []
- Return ONLY the JSON array, no other text"""


def is_available() -> bool:
    """Check if Groq API key is configured."""
    return bool(os.getenv("GROQ_API_KEY"))


def extract_tickers_from_text(text: str, max_tokens: int = 200) -> List[Dict]:
    """Extract company names and ticker symbols from article text using Groq Llama.

    Args:
        text: Article text (title, snippet, or full body). Truncated to 1500 chars internally.
        max_tokens: Max output tokens for Groq response.

    Returns:
        List of dicts with 'company' and 'ticker' keys.
        Returns empty list if Groq is unavailable or extraction fails.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return []

    _track_usage()

    # Truncate input to keep costs minimal
    truncated = text[:1500]

    try:
        logger.debug(f"groq | Sending {len(truncated)} chars to Llama for extraction...")
        response = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": truncated},
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens,
            },
            timeout=10,
        )
        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"].strip()
        logger.debug(f"groq | Llama response: {content}")
        results = _parse_response(content)
        if results:
            logger.info(f"groq | ✓ Extracted {len(results)} companies: {results}")
        else:
            logger.debug(f"groq | No companies found in response")
        return results

    except requests.exceptions.Timeout:
        logger.warning("groq | Request timed out")
        return []
    except requests.exceptions.RequestException as e:
        logger.warning(f"groq | API request failed: {e}")
        return []
    except (KeyError, IndexError) as e:
        logger.warning(f"groq | Unexpected response format: {e}")
        return []


def extract_tickers_batch(articles: List[Dict], text_field: str = "title") -> Dict[str, Set[str]]:
    """Extract tickers from a batch of articles.

    Processes each article individually and returns a mapping of
    ticker symbols to the set of article indices that mentioned them.

    Args:
        articles: List of article dicts.
        text_field: Which field(s) to extract from. Uses 'title' and 'snippet' by default.

    Returns:
        Dict mapping ticker symbol → set of article indices where it was found.
    """
    if not is_available():
        logger.debug("groq | GROQ_API_KEY not set, skipping AI-powered ticker extraction")
        return {}

    import time
    ticker_articles: Dict[str, Set[str]] = {}

    for i, article in enumerate(articles):
        # Combine title and snippet for extraction
        text = f"{article.get('title', '')} {article.get('snippet', article.get('body', ''))}"
        if not text.strip():
            continue

        results = extract_tickers_from_text(text)

        for item in results:
            ticker = item.get("ticker", "").upper()
            if ticker:
                if ticker not in ticker_articles:
                    ticker_articles[ticker] = set()
                ticker_articles[ticker].add(i)

        # Delay to avoid rate limiting on free tier (14,400 requests/day limit)
        if i < len(articles) - 1:
            time.sleep(1.0)  # 1 second between requests

    found_count = len(ticker_articles)
    if found_count > 0:
        logger.info(f"groq | Extracted {found_count} tickers from {len(articles)} articles via Llama 3.1")
    else:
        logger.debug(f"groq | No tickers extracted from {len(articles)} articles")

    return ticker_articles


def get_ticker_symbols(articles: List[Dict]) -> Set[str]:
    """Convenience function: extract just the ticker symbols from articles.

    This is the main entry point for discovery.py and newsapi.py.

    Args:
        articles: List of article dicts with 'title' and/or 'snippet' fields.

    Returns:
        Set of ticker symbols found across all articles.
    """
    ticker_map = extract_tickers_batch(articles)
    return set(ticker_map.keys())


def _parse_response(content: str) -> List[Dict]:
    """Parse Groq response into list of company/ticker dicts.

    Handles various response formats:
    - Clean JSON array
    - JSON wrapped in markdown code blocks
    - Malformed responses (returns empty list)
    """
    # Strip markdown code blocks if present
    if content.startswith("```"):
        lines = content.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [l for l in lines if not l.strip().startswith("```")]
        content = "\n".join(lines)

    try:
        parsed = json.loads(content)
        if not isinstance(parsed, list):
            return []

        # Validate each item has required fields
        valid = []
        for item in parsed:
            if isinstance(item, dict) and "ticker" in item:
                valid.append({
                    "company": item.get("company", ""),
                    "ticker": item["ticker"].upper(),
                })
        return valid

    except json.JSONDecodeError:
        logger.debug(f"groq | Failed to parse response as JSON: {content[:200]}")
        return []


def _track_usage():
    """Track daily request count and warn near free tier limits."""
    global _daily_request_count, _daily_request_date
    from datetime import date

    today = date.today()
    if _daily_request_date != today:
        _daily_request_count = 0
        _daily_request_date = today

    _daily_request_count += 1

    if _daily_request_count == 13000:
        logger.warning("groq | Approaching daily free tier limit (14,400 requests)")
    elif _daily_request_count >= 14400:
        logger.error("groq | Daily free tier limit reached — extraction will fail until tomorrow")
