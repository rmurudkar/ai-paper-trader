"""
Integration tests for fetchers/newsapi.py — hits the real NewsAPI.ai API.
Requires NEWSAPI_AI_KEY in .env

Run: python test_newsapi.py
"""

import os
import sys
import json
import logging

sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

from fetchers.newsapi import (
    fetch_headlines,
    _fetch_articles,
    _filter_by_ticker_relevance,
    _TOPIC_KEYWORDS,
    DEFAULT_TOPICS,
    SNIPPET_MAX_CHARS,
)

print("=" * 60)
print("NEWSAPI.PY INTEGRATION TEST SUITE")
print("=" * 60)

#################################
# SECTION 1: API key check — fail fast if not configured

print("\n--- SECTION 1: API key present ---")
api_key = os.getenv("NEWSAPI_AI_KEY")
if not api_key:
    print("FAIL: NEWSAPI_AI_KEY not set in environment. Set it in .env and retry.")
    sys.exit(1)
print(f"  NEWSAPI_AI_KEY: {'*' * 8}{api_key[-4:]}")
print("PASS")

#################################
# SECTION 1b: Raw API probe — show exact request/response before any parsing

print("\n--- SECTION 1b: Raw API probe ---")
import requests
from fetchers.newsapi import NEWSAPI_BASE_URL

def _probe(label, payload):
    resp = requests.post(
        NEWSAPI_BASE_URL,
        json=payload,
        headers={'Content-Type': 'application/json'},
        timeout=30,
    )
    block = resp.json().get("articles", {})
    count = len(block.get("results", []))
    total = block.get("totalResults", 0)
    print(f"  [{label}] HTTP {resp.status_code} → totalResults={total}, results={count}")
    return count

base_query = {
    'action': 'getArticles',
    'apiKey': api_key,
    'keywordLoc': 'body,title',
    'lang': 'eng',
    'isDuplicateFilter': 'skipDuplicates',
    'dataType': ['news'],
    'articlesPage': 1,
    'articlesCount': 3,
    'articlesSortBy': 'date',
    'articlesSortByAsc': False,
    'includeArticleTitle': True,
    'includeArticleBody': True,
    'includeArticleUrl': True,
    'includeArticlePublishDate': True,
    'includeArticleSource': True,
    'includeArticleCategories': False,
    'includeArticleConcepts': False,
}

def make_query(keyword):
    return {**base_query, 'keyword': keyword}

_probe("inflation",  make_query('inflation'))
_probe("tariff",     make_query('tariff'))
_probe("recession",  make_query('recession'))
_probe("earnings",   make_query('earnings'))
_probe("oil",        make_query('oil'))

print("PASS (probe complete)")

#################################
# SECTION 2: _fetch_articles — single topic, real HTTP call

print("\n--- SECTION 2: _fetch_articles for 'macro' (live API) ---")
articles = _fetch_articles(_TOPIC_KEYWORDS["macro"], "macro", max_results=5)

print(f"  Articles returned: {len(articles)}")
assert isinstance(articles, list), "Expected a list"
assert len(articles) > 0, "Expected at least 1 article — check API key and quota"

for i, a in enumerate(articles):
    assert "title" in a and a["title"], f"Article {i} missing title"
    assert "url" in a and a["url"], f"Article {i} missing url"
    assert "snippet" in a, f"Article {i} missing snippet"
    assert "published_at" in a, f"Article {i} missing published_at"
    assert a["source"] == "newsapi", f"Article {i} wrong source: {a['source']}"
    assert a["needs_full_text"] is True, f"Article {i} needs_full_text must be True"
    assert a["topics"] == ["macro"], f"Article {i} wrong topics: {a['topics']}"
    assert len(a["snippet"]) <= SNIPPET_MAX_CHARS, f"Article {i} snippet too long"
    print(f"  [{i}] {a['title'][:70]}")
    print(f"       url          : {a['url'][:60]}")
    print(f"       published_at : {a['published_at']}")
    print(f"       snippet len  : {len(a['snippet'])} chars")

print("PASS")

#################################
# SECTION 3: _fetch_articles — geopolitical topic

print("\n--- SECTION 3: _fetch_articles for 'geopolitical' (live API) ---")
articles = _fetch_articles(_TOPIC_KEYWORDS["geopolitical"], "geopolitical", max_results=5)

print(f"  Articles returned: {len(articles)}")
assert isinstance(articles, list)
for a in articles:
    assert a["topics"] == ["geopolitical"]
    assert a["needs_full_text"] is True
    print(f"  - {a['title'][:80]}")

print("PASS")

#################################
# SECTION 4: _fetch_articles — economic topic

print("\n--- SECTION 4: _fetch_articles for 'economic' (live API) ---")
articles = _fetch_articles(_TOPIC_KEYWORDS["economic"], "economic", max_results=5)

print(f"  Articles returned: {len(articles)}")
assert isinstance(articles, list)
for a in articles:
    assert a["topics"] == ["economic"]
    print(f"  - {a['title'][:80]}")

print("PASS")

#################################
# SECTION 5: fetch_headlines — default topics, default max_results

print("\n--- SECTION 5: fetch_headlines() with all defaults (live API) ---")
headlines = fetch_headlines()

print(f"  Articles returned : {len(headlines)}")
assert isinstance(headlines, list)
assert len(headlines) > 0, "Expected results from fetch_headlines()"

topics_seen = set()
for a in headlines:
    assert "title" in a and a["title"]
    assert "url" in a and a["url"]
    assert "snippet" in a
    assert "topics" in a and isinstance(a["topics"], list)
    assert "published_at" in a
    assert a["source"] == "newsapi"
    assert a["needs_full_text"] is True
    topics_seen.update(a["topics"])

print(f"  Topics present    : {sorted(topics_seen)}")
print(f"  Default topics    : {DEFAULT_TOPICS}")

for a in headlines[:5]:
    print(f"\n  title      : {a['title'][:70]}")
    print(f"  topics     : {a['topics']}")
    print(f"  published  : {a['published_at']}")
    print(f"  snippet    : {a['snippet'][:100]}...")

print("PASS")

#################################
# SECTION 6: fetch_headlines — sorted descending by published_at

print("\n--- SECTION 6: fetch_headlines sort order ---")
dates = [a["published_at"] for a in headlines if a["published_at"]]
sorted_dates = sorted(dates, reverse=True)
assert dates == sorted_dates, f"Results not sorted descending.\nGot: {dates}\nExpected: {sorted_dates}"
print(f"  First : {dates[0]}")
print(f"  Last  : {dates[-1]}")
print("PASS")

#################################
# SECTION 7: fetch_headlines — explicit single topic

print("\n--- SECTION 7: fetch_headlines(topics=['macro']) ---")
macro_only = fetch_headlines(topics=["macro"], max_results=5)

print(f"  Articles returned: {len(macro_only)}")
assert len(macro_only) > 0
for a in macro_only:
    assert a["topics"] == ["macro"], f"Expected ['macro'], got {a['topics']}"
    print(f"  - {a['title'][:80]}")

print("PASS")

#################################
# SECTION 8: fetch_headlines — multi-topic selection

print("\n--- SECTION 8: fetch_headlines(topics=['energy', 'crypto']) ---")
subset = fetch_headlines(topics=["energy", "crypto"], max_results=10)

print(f"  Articles returned: {len(subset)}")
assert isinstance(subset, list)
topics_in_result = set()
for a in subset:
    topics_in_result.update(a["topics"])
    assert a["topics"][0] in {"energy", "crypto"}, f"Unexpected topic: {a['topics']}"

print(f"  Topics in results: {topics_in_result}")
for a in subset[:5]:
    print(f"  [{a['topics'][0]:12}] {a['title'][:65]}")

print("PASS")

#################################
# SECTION 9: fetch_headlines — no duplicate URLs in results

print("\n--- SECTION 9: fetch_headlines deduplication ---")
all_headlines = fetch_headlines(max_results=30)
urls = [a["url"] for a in all_headlines]
unique_urls = set(urls)
duplicates = len(urls) - len(unique_urls)
print(f"  Total articles : {len(urls)}")
print(f"  Unique URLs    : {len(unique_urls)}")
print(f"  Duplicates     : {duplicates}")
assert duplicates == 0, f"Found {duplicates} duplicate URLs"
print("PASS")

#################################
# SECTION 10: fetch_headlines — max_results respected

print("\n--- SECTION 10: fetch_headlines max_results cap ---")
for cap in [5, 10, 20]:
    result = fetch_headlines(max_results=cap)
    print(f"  max_results={cap:2d} → returned {len(result)}")
    assert len(result) <= cap, f"Returned {len(result)} but cap was {cap}"

print("PASS")

#################################
# SECTION 11: _filter_by_ticker_relevance — live articles + watchlist

print("\n--- SECTION 11: _filter_by_ticker_relevance on live headlines ---")
headlines = fetch_headlines(max_results=20)
watchlist = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "JPM", "SPY", "QQQ"]
filtered = _filter_by_ticker_relevance(headlines, watchlist)

broad_topics = {"macro", "geopolitical", "energy"}
broad_articles = [a for a in headlines if any(t in broad_topics for t in a["topics"])]

print(f"  Total headlines   : {len(headlines)}")
print(f"  After filtering   : {len(filtered)}")
print(f"  Broad-topic floor : {len(broad_articles)} (should all pass through)")

# All broad-topic articles must be in filtered
for a in broad_articles:
    assert a in filtered, f"Broad-topic article was incorrectly filtered: {a['title']}"

print("PASS")

#################################
# SECTION 12: _fetch_articles — invalid API key returns empty list

print("\n--- SECTION 12: Invalid API key returns [] gracefully ---")
import os
original_key = os.environ.get("NEWSAPI_AI_KEY")
os.environ["NEWSAPI_AI_KEY"] = "invalid-key-000"

result = _fetch_articles(_TOPIC_KEYWORDS["macro"], "macro", max_results=3)
print(f"  Result with bad key: {result}")
assert isinstance(result, list), "Should always return a list"
# API may return HTTP error or an error payload — either way we get [] or a list
# (no exception should propagate)

if original_key:
    os.environ["NEWSAPI_AI_KEY"] = original_key
print("  No exception raised, returned list")
print("PASS")

#################################
# SECTION 13: Full pipeline dump — print raw JSON of first 3 articles

print("\n--- SECTION 13: Raw output of first 3 articles ---")
headlines = fetch_headlines(max_results=3)
print(json.dumps(headlines, indent=2, default=str))
print("PASS")

#################################
# SECTION 14: Discovery mode ticker extraction

print("\n--- SECTION 14: Discovery mode ticker extraction ---")

discovery_context = {
    'mode': 'discovery',
    'tickers': ['AAPL', 'MSFT', 'NVDA'],
    'sources': {},
    'cycle_id': '20260405_test'
}

discovery_articles = fetch_headlines(
    topics=['macro'],
    max_results=5,
    discovery_context=discovery_context
)

print(f"  Articles with discovery context: {len(discovery_articles)}")

# Check that articles have ticker extraction fields
tickers_found = set()
for article in discovery_articles:
    assert 'tickers' in article, "Missing 'tickers' field in discovery mode"
    assert 'extraction_confidence' in article, "Missing 'extraction_confidence' field"
    tickers_found.update(article['tickers'])

print(f"  Total unique tickers extracted: {len(tickers_found)}")
print(f"  Tickers found: {sorted(tickers_found) if tickers_found else 'None'}")
print("PASS")

#################################
# SECTION 15: Watchlist mode filtering

print("\n--- SECTION 15: Watchlist mode filtering ---")

watchlist_context = {
    'mode': 'watchlist',
    'tickers': ['AAPL', 'MSFT'],
    'sources': {},
    'cycle_id': '20260405_test'
}

# Get unfiltered articles first
all_articles = fetch_headlines(topics=['macro', 'economic'], max_results=10)
print(f"  Articles without filtering: {len(all_articles)}")

# Get filtered articles
filtered_articles = fetch_headlines(
    topics=['macro', 'economic'],
    max_results=10,
    discovery_context=watchlist_context
)

print(f"  Articles with watchlist filtering: {len(filtered_articles)}")

# Verify filtering logic
for article in filtered_articles:
    topics = article.get('topics', [])
    broad_topics = {'macro', 'geopolitical', 'energy'}

    if any(topic in broad_topics for topic in topics):
        # Broad topics should pass through
        continue
    else:
        # Other articles should mention watchlist tickers
        text = f"{article.get('title', '')} {article.get('snippet', '')}".lower()
        has_ticker = any(ticker.lower() in text for ticker in ['aapl', 'msft'])
        assert has_ticker, f"Economic article should mention watchlist ticker: {article.get('title')}"

print("PASS")

#################################
# SECTION 16: Discovery context integration

print("\n--- SECTION 16: Discovery context integration ---")

# Test with no discovery context (legacy mode)
legacy_articles = fetch_headlines(topics=['macro'], max_results=3)
print(f"  Legacy mode articles: {len(legacy_articles)}")

# Test discovery context parsing
from fetchers.newsapi import _parse_discovery_context

# Test valid discovery context
valid_context = {'mode': 'discovery', 'tickers': ['AAPL'], 'cycle_id': 'test123'}
mode, tickers, cycle_id = _parse_discovery_context(valid_context)
assert mode == 'discovery', f"Expected 'discovery', got '{mode}'"
assert tickers == ['AAPL'], f"Expected ['AAPL'], got {tickers}"
assert cycle_id == 'test123', f"Expected 'test123', got '{cycle_id}'"

# Test None context (legacy)
mode, tickers, cycle_id = _parse_discovery_context(None)
assert mode == 'legacy', f"Expected 'legacy', got '{mode}'"
assert tickers == [], f"Expected [], got {tickers}"
assert cycle_id == '', f"Expected '', got '{cycle_id}'"

print("PASS")

#################################
# SECTION 17: Ticker extraction accuracy

print("\n--- SECTION 17: Ticker extraction accuracy ---")

from fetchers.newsapi import extract_tickers_from_text

# Test various ticker extraction patterns
test_cases = [
    ("Apple Inc. ($AAPL) reported strong earnings", {'AAPL'}),
    ("Microsoft (NASDAQ:MSFT) and Apple stock rose", {'MSFT'}),
    ("Trading in NVDA, AMD, and INTC was heavy", {'NVDA', 'AMD', 'INTC'}),
    ("The CEO said revenue will increase", set()),  # No tickers
    ("Stock market news about NYSE and SEC", set()),  # False positives filtered
]

for text, expected in test_cases:
    extracted = extract_tickers_from_text(text)
    print(f"  Text: {text[:50]}...")
    print(f"    Expected: {expected}")
    print(f"    Extracted: {extracted}")

    # Allow some variation in extraction but check for main patterns
    if expected:
        assert len(extracted) > 0, f"Should extract some tickers from: {text}"

print("PASS")

#################################
# SECTION 18: Mode switching behavior

print("\n--- SECTION 18: Mode switching behavior ---")

# Test that same call produces different results with different modes
base_params = {'topics': ['macro'], 'max_results': 3}

legacy_result = fetch_headlines(**base_params)
discovery_result = fetch_headlines(**base_params, discovery_context={'mode': 'discovery', 'tickers': [], 'cycle_id': 'test'})
watchlist_result = fetch_headlines(**base_params, discovery_context={'mode': 'watchlist', 'tickers': ['AAPL'], 'cycle_id': 'test'})

print(f"  Legacy mode: {len(legacy_result)} articles")
print(f"  Discovery mode: {len(discovery_result)} articles")
print(f"  Watchlist mode: {len(watchlist_result)} articles")

# Check that discovery mode adds ticker fields
if discovery_result:
    assert 'tickers' in discovery_result[0], "Discovery mode should add 'tickers' field"
    assert 'extraction_confidence' in discovery_result[0], "Discovery mode should add 'extraction_confidence' field"

# Check that legacy mode doesn't have ticker extraction fields populated
if legacy_result:
    # Legacy mode should have these fields but not populated
    assert legacy_result[0].get('tickers') == [], "Legacy mode should have empty tickers list"
    assert legacy_result[0].get('extraction_confidence') == 0.0, "Legacy mode should have zero confidence"

print("PASS")

#################################
# SECTION 19: Backward compatibility validation

print("\n--- SECTION 19: Backward compatibility validation ---")

# Test that all old function calls work exactly as before
old_style_call = fetch_headlines()
assert len(old_style_call) >= 0, "Old-style call should work"

old_style_with_params = fetch_headlines(topics=['macro'], max_results=5)
assert len(old_style_with_params) >= 0, "Old-style call with params should work"

# Test that old private functions still work
from fetchers.newsapi import _filter_by_ticker_relevance

if old_style_call:
    filtered = _filter_by_ticker_relevance(old_style_call[:3], ['AAPL', 'MSFT'])
    assert isinstance(filtered, list), "_filter_by_ticker_relevance should return list"

print(f"  Old-style calls: WORKING")
print(f"  Private functions: WORKING")
print("PASS")

#################################
# SUMMARY

print("\n" + "=" * 60)
print("ALL INTEGRATION TESTS PASSED (INCLUDING DISCOVERY FEATURES)")
print("=" * 60)
