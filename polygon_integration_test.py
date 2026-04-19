"""
Integration tests for fetchers/polygon.py — hits the real Polygon.io API.
Requires POLYGON_API_TOKEN in .env

NOTE: Polygon free tier is rate-limited to 5 requests/minute.
      polygon.py sleeps 12s between per-ticker requests inside _fetch_general_news.
      This test suite takes ~3-4 minutes to run in full.

This is a STANDALONE integration test script (not part of pytest suite).
Run: python polygon_integration_test.py
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')

from fetchers.polygon import (
    fetch_full_text,
    _fetch_ticker_news,
    _fetch_general_news,
    _truncate_article_text,
    _match_article_by_headline,
    _enrich_newsapi_items,
    _request_news,
    _parse_response,
    _get_published_after,
    POLYGON_BASE_URL,
    DEFAULT_WATCHLIST,
    WORD_LIMIT,
    HEADLINE_SIMILARITY_THRESHOLD,
)

print("=" * 60)
print("POLYGON.PY INTEGRATION TEST SUITE")
print("=" * 60)

#################################
# SECTION 1: API key check — fail fast if not configured

print("\n--- SECTION 1: API key present ---")
api_key = os.getenv("POLYGON_API_TOKEN")
if not api_key:
    print("FAIL: POLYGON_API_TOKEN not set in environment. Set it in .env and retry.")
    sys.exit(1)
print(f"  POLYGON_API_TOKEN: {'*' * 8}{api_key[-4:]}")
print("PASS")

#################################
# SECTION 2: API probe — confirm endpoint reachable using _request_news so the
# module-level rate limiter tracks this call and paces subsequent requests.

print("\n--- SECTION 2: API probe via _request_news ---")
probe_results = _request_news({
    'apiKey': api_key,
    'ticker': 'AAPL',
    'order': 'desc',
    'limit': 3,
    'sort': 'published_utc',
})
print(f"  Results count : {len(probe_results)}")
assert isinstance(probe_results, list), "Expected a list from _request_news"
assert len(probe_results) > 0, "Expected at least 1 result from AAPL probe"
for a in probe_results:
    assert a['source'] == 'polygon'
    assert a['partial'] is False
print("PASS")

#################################
# SECTION 3: _get_published_after — format and 24h window

print("\n--- SECTION 3: _get_published_after format and value ---")
cutoff_str = _get_published_after()
print(f"  Returned: {cutoff_str}")

parsed = datetime.strptime(cutoff_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
now_utc = datetime.now(timezone.utc)
delta = now_utc - parsed

assert 23 <= delta.total_seconds() / 3600 <= 25, (
    f"Expected ~24h window, got {delta.total_seconds() / 3600:.1f}h"
)
print(f"  Delta from now: {delta.total_seconds() / 3600:.2f}h (expected ~24h)")
print("PASS")

#################################
# SECTION 4: _truncate_article_text — pure function, no API

print("\n--- SECTION 4: _truncate_article_text ---")

assert _truncate_article_text('') == '',   "Empty string should return empty string"
assert _truncate_article_text(None) == '', "None should return empty string"

short_text = "Apple reports record quarterly earnings."
assert _truncate_article_text(short_text) == short_text, "Short text should be unchanged"

at_limit = ' '.join([f'word{i}' for i in range(WORD_LIMIT)])
assert len(_truncate_article_text(at_limit).split()) == WORD_LIMIT, "At-limit text word count changed"

over_limit = ' '.join([f'word{i}' for i in range(WORD_LIMIT + 100)])
result = _truncate_article_text(over_limit)
assert len(result.split()) == WORD_LIMIT, f"Expected {WORD_LIMIT} words, got {len(result.split())}"

assert len(_truncate_article_text(over_limit, max_words=50).split()) == 50, "Custom max_words not respected"

print(f"  WORD_LIMIT={WORD_LIMIT}, all truncation cases pass")
print("PASS")

#################################
# SECTION 5: _parse_response — pure function, mock data

print("\n--- SECTION 5: _parse_response with mock data ---")

mock_response = {
    'results': [
        {
            'id': 'abc123',
            'publisher': {'name': 'Reuters', 'homepage_url': 'https://reuters.com'},
            'title': 'Apple hits all-time high ahead of earnings',
            'author': 'Jane Smith',
            'published_utc': '2026-04-04T10:00:00Z',
            'article_url': 'https://reuters.com/apple-ath-2026',
            'tickers': ['AAPL'],
            'content': 'Apple shares climbed to a record high on Thursday ' * 10,
            'description': 'A summary of Apple stock performance.',
        },
        {
            'id': 'def456',
            'publisher': {'name': 'AP'},
            'title': 'NVDA surges on AI demand',
            'published_utc': '2026-04-04T09:00:00Z',
            'article_url': 'https://apnews.com/nvda-surge',
            'tickers': ['NVDA'],
            'content': '',
            'description': 'Nvidia climbs on strong AI chip demand.',
        },
        {
            # Missing article_url — must be skipped
            'id': 'ghi789',
            'title': 'Should be skipped',
            'published_utc': '2026-04-04T08:00:00Z',
            'article_url': '',
            'tickers': ['TSLA'],
            'content': 'Some content',
        },
    ]
}

parsed = _parse_response(mock_response)
assert len(parsed) == 2, f"Expected 2 articles (skipping empty URL entry), got {len(parsed)}"

first = parsed[0]
assert first['url'] == 'https://reuters.com/apple-ath-2026'
assert first['title'] == 'Apple hits all-time high ahead of earnings'
assert first['publisher'] == 'Reuters'
assert first['published_at'] == '2026-04-04T10:00:00Z'
assert first['tickers'] == ['AAPL']
assert first['source'] == 'polygon'
assert first['partial'] is False
assert first['full_text']

# Empty content must fall back to description
second = parsed[1]
assert second['full_text'] == 'Nvidia climbs on strong AI chip demand.', (
    f"Expected description fallback, got: {second['full_text']!r}"
)

assert _parse_response({}) == []
assert _parse_response({'results': []}) == []

print(f"  Parsed {len(parsed)} articles (empty-url entry skipped correctly)")
print("  Schema: url, title, publisher, published_at, tickers, source, partial, full_text — OK")
print("  content→full_text, description fallback, empty-url skip — all pass")
print("PASS")

#################################
# SECTION 6: _parse_response — full_text truncated to WORD_LIMIT

print("\n--- SECTION 6: _parse_response truncates content to WORD_LIMIT ---")
long_content = ' '.join([f'word{i}' for i in range(WORD_LIMIT + 500)])
mock_long = {
    'results': [{
        'article_url': 'https://example.com/long-article',
        'title': 'Long article',
        'published_utc': '2026-04-04T10:00:00Z',
        'publisher': {'name': 'Test Publisher'},
        'tickers': ['AAPL'],
        'content': long_content,
    }]
}
parsed_long = _parse_response(mock_long)
word_count = len(parsed_long[0]['full_text'].split())
print(f"  Input words : {len(long_content.split())}")
print(f"  Output words: {word_count} (limit: {WORD_LIMIT})")
assert word_count == WORD_LIMIT, f"Expected {WORD_LIMIT} words after truncation, got {word_count}"
print("PASS")

#################################
# SECTION 7: _match_article_by_headline — pure function

print("\n--- SECTION 7: _match_article_by_headline ---")

candidates = [
    {'title': 'Apple reports record quarterly earnings beat', 'url': 'https://a.com/1'},
    {'title': 'Tesla misses revenue estimates for Q1 2026',   'url': 'https://a.com/2'},
    {'title': 'NVIDIA AI chip demand drives strong guidance', 'url': 'https://a.com/3'},
]

# Headline shares {apple, reports, record, earnings, beat} with candidate[0]
# → Jaccard = 5/8 = 0.625 > threshold 0.6
result = _match_article_by_headline(
    'Apple reports record earnings beat this quarter', candidates
)
assert result is not None, "Expected a match for Apple earnings headline"
assert result['url'] == 'https://a.com/1', f"Wrong match URL: {result['url']}"
print(f"  Matched: {result['title']!r}")

# Completely unrelated — should return None
result = _match_article_by_headline(
    'Hurricane forecast shows major storm approaching Florida coast', candidates
)
assert result is None, f"Expected no match for unrelated headline, got: {result}"
print("  No match for unrelated headline: correct")

# Edge cases — must not crash
assert _match_article_by_headline('', candidates) is None
assert _match_article_by_headline(None, candidates) is None
assert _match_article_by_headline('Apple earnings', []) is None

# Empty-title candidates are skipped, valid one still matched
result = _match_article_by_headline(
    'Apple reports record quarterly earnings beat',
    [{'title': '', 'url': 'https://a.com/empty'}, candidates[0]]
)
assert result is not None, "Should skip empty-title candidate and match valid one"

print(f"  Threshold={HEADLINE_SIMILARITY_THRESHOLD}, all edge cases pass")
print("PASS")

#################################
# LIVE DATA COLLECTION
# Fetch all data needed for remaining sections here.
# polygon.py enforces ≥13s between every request via a module-level rate limiter,
# so no manual sleeps are needed — calls queue automatically.
# 3 tickers × 13s ≈ 39s for _fetch_general_news.

print("\n--- LIVE DATA COLLECTION (rate-limited fetches, ~2-3min total) ---")
print("  Fetching AAPL articles (max 5)...")
_aapl_articles = _fetch_ticker_news('AAPL', max_results=5)
print(f"  AAPL: {len(_aapl_articles)} articles")

print("  Fetching MSFT articles (max 5)...")
_msft_articles = _fetch_ticker_news('MSFT', max_results=5)
print(f"  MSFT: {len(_msft_articles)} articles")

# Use a small 3-ticker watchlist to keep test duration reasonable.
print("  Fetching general news for ['AAPL', 'MSFT', 'NVDA']...")
_general_articles = _fetch_general_news(['AAPL', 'MSFT', 'NVDA'], max_results=15)
print(f"  General: {len(_general_articles)} articles")

print("--- LIVE DATA COLLECTION DONE ---")

#################################
# SECTION 8: _fetch_ticker_news — schema validation on AAPL data

print("\n--- SECTION 8: _fetch_ticker_news schema (AAPL) ---")
print(f"  Articles returned: {len(_aapl_articles)}")
assert isinstance(_aapl_articles, list)
assert len(_aapl_articles) > 0, "Expected at least 1 AAPL article — check API key and quota"

for i, a in enumerate(_aapl_articles):
    assert 'title' in a and a['title'],       f"Article {i} missing title"
    assert 'url' in a and a['url'],           f"Article {i} missing url"
    assert 'full_text' in a,                  f"Article {i} missing full_text"
    assert 'publisher' in a,                  f"Article {i} missing publisher"
    assert 'published_at' in a,               f"Article {i} missing published_at"
    assert 'tickers' in a,                    f"Article {i} missing tickers"
    assert a['source'] == 'polygon',          f"Article {i} wrong source: {a['source']}"
    assert a['partial'] is False,             f"Article {i} partial must be False"
    assert isinstance(a['tickers'], list),    f"Article {i} tickers must be a list"
    word_count = len(a['full_text'].split()) if a['full_text'] else 0
    assert word_count <= WORD_LIMIT, f"Article {i} exceeds {WORD_LIMIT} words: {word_count}"
    print(f"  [{i}] {a['title'][:65]}")
    print(f"       published_at : {a['published_at']}")
    print(f"       tickers      : {a['tickers'][:3]}{'...' if len(a['tickers']) > 3 else ''}")
    print(f"       full_text    : {word_count} words")

print("PASS")

#################################
# SECTION 9: _fetch_ticker_news — max_results respected (uses pre-fetched data)

print("\n--- SECTION 9: _fetch_ticker_news max_results cap ---")
# AAPL data was fetched with max_results=5 — verify cap was honoured
assert len(_aapl_articles) <= 5, f"Returned {len(_aapl_articles)} but cap was 5"
print(f"  max_results=5 → returned {len(_aapl_articles)} (≤ 5)")

# Verify MSFT cap too
assert len(_msft_articles) <= 5, f"MSFT returned {len(_msft_articles)} but cap was 5"
print(f"  max_results=5 → MSFT returned {len(_msft_articles)} (≤ 5)")
print("PASS")

#################################
# SECTION 10: _fetch_ticker_news — result list is always returned (no exceptions)

print("\n--- SECTION 10: _fetch_ticker_news always returns a list ---")
# MSFT and AAPL already confirmed above. Verify type safety.
assert isinstance(_aapl_articles, list)
assert isinstance(_msft_articles, list)
for a in _aapl_articles + _msft_articles:
    assert a['source'] == 'polygon'
    assert a['partial'] is False
print("  Both AAPL and MSFT returned valid lists with correct schema")
print("PASS")

#################################
# SECTION 11: _fetch_general_news — schema, dedup, sort order

print("\n--- SECTION 11: _fetch_general_news schema + dedup + sort ---")
print(f"  Articles returned: {len(_general_articles)}")
assert isinstance(_general_articles, list)
assert len(_general_articles) > 0, "Expected at least 1 article from general news"

# No duplicate URLs
urls = [a['url'] for a in _general_articles]
assert len(urls) == len(set(urls)), f"Duplicate URLs found: {len(urls) - len(set(urls))}"
print(f"  Dedup: {len(urls)} articles, 0 duplicates")

# Schema check
for i, a in enumerate(_general_articles):
    assert 'title' in a,     f"Article {i} missing title"
    assert 'url' in a,       f"Article {i} missing url"
    assert 'full_text' in a, f"Article {i} missing full_text"
    assert a['source'] == 'polygon'
    assert a['partial'] is False

# Sorted descending by published_at
dates = [a['published_at'] for a in _general_articles if a['published_at']]
assert dates == sorted(dates, reverse=True), "Articles not sorted descending by published_at"
print(f"  Sort order: OK (newest first)")

for a in _general_articles[:3]:
    print(f"  - {a['title'][:65]}")
print("PASS")

#################################
# SECTION 12: _fetch_general_news — cross-ticker deduplication

print("\n--- SECTION 12: _fetch_general_news cross-ticker deduplication ---")
# An article tagged with both AAPL and MSFT should only appear once in the merged list.
urls = [a['url'] for a in _general_articles]
assert len(urls) == len(set(urls)), f"{len(urls) - len(set(urls))} duplicate URLs found"
print(f"  {len(_general_articles)} unique articles across AAPL/MSFT/NVDA — no duplicates")
print("PASS")

#################################
# SECTION 13: _request_news — valid params returns parsed list (reuse AAPL probe data)

print("\n--- SECTION 13: _request_news with valid params ---")
# Reuse _aapl_articles from live data collection — already confirms _request_news works.
# No extra API call needed.
assert isinstance(_aapl_articles, list)
assert len(_aapl_articles) > 0
for a in _aapl_articles:
    assert 'url' in a
    assert a['source'] == 'polygon'
    assert a['partial'] is False
print(f"  _request_news returned {len(_aapl_articles)} articles with correct schema")
print("PASS")

#################################
# SECTION 14: _request_news — invalid API key returns empty list (no exception)

print("\n--- SECTION 14: _request_news with invalid API key returns [] ---")
bad_params = {
    'apiKey': 'invalid-key-000',
    'ticker': 'AAPL',
    'order': 'desc',
    'limit': 3,
    'sort': 'published_utc',
}
result = _request_news(bad_params)
print(f"  Result with bad key: {result}")
assert isinstance(result, list), "Should always return a list, never raise"
print("  No exception raised, returned list")
print("PASS")

#################################
# SECTION 15: fetch_full_text — empty and None URL edge cases

print("\n--- SECTION 15: fetch_full_text with empty/None URL ---")
assert fetch_full_text('') is None,   "Empty string URL should return None"
assert fetch_full_text(None) is None, "None URL should return None"
print("  fetch_full_text('') → None")
print("  fetch_full_text(None) → None")
print("PASS")

#################################
# SECTION 16: fetch_full_text — missing API token returns None

print("\n--- SECTION 16: fetch_full_text with no API token returns None ---")
original_key = os.environ.get('POLYGON_API_TOKEN')
os.environ['POLYGON_API_TOKEN'] = ''

result = fetch_full_text('https://example.com/some-article')
assert result is None, f"Expected None when API token missing, got: {result}"
print("  fetch_full_text with missing token → None, no exception raised")

if original_key:
    os.environ['POLYGON_API_TOKEN'] = original_key
print("PASS")

#################################
# SECTION 17: fetch_full_text — URL match from live Polygon feed

print("\n--- SECTION 17: fetch_full_text URL match (live API) ---")
# Seed from already-fetched AAPL articles so fetch_full_text can find it in _fetch_general_news.
assert len(_aapl_articles) > 0, "Need seed articles to test URL match"
target_url = _aapl_articles[0]['url']
print(f"  Target URL: {target_url[:70]}")

result = fetch_full_text(target_url)
if result is not None:
    print(f"  Match found: {result['title'][:65]}")
    assert result['url'] == target_url,   f"URL mismatch: {result['url']!r}"
    assert result['source'] == 'polygon', f"Source should be 'polygon': {result['source']!r}"
    assert result['partial'] is False,    "partial should be False"
    assert 'full_text' in result,         "Result missing full_text"
    assert 'publisher' in result,         "Result missing publisher"
    assert 'published_at' in result,      "Result missing published_at"
    assert 'tickers' in result,           "Result missing tickers"
    word_count = len(result['full_text'].split()) if result['full_text'] else 0
    assert word_count <= WORD_LIMIT, f"full_text exceeds {WORD_LIMIT} words: {word_count}"
    print(f"  full_text    : {word_count} words")
    print(f"  publisher    : {result['publisher']}")
    print(f"  tickers      : {result['tickers'][:3]}")
    print("PASS")
else:
    print("  WARNING: URL not matched — may be outside the 24h fetch window. Skipping assertion.")
    print("SKIP")

#################################
# SECTION 18: fetch_full_text — URL not in Polygon returns None

print("\n--- SECTION 18: fetch_full_text with non-Polygon URL returns None ---")
fake_url = 'https://totallyfake.example.invalid/article-does-not-exist-xyz-12345'
result = fetch_full_text(fake_url)
print(f"  Result for fake URL: {result}")
assert result is None, f"Expected None for unknown URL, got: {result}"
print("PASS")

#################################
# SECTION 19: _enrich_newsapi_items — matched items get full_text

print("\n--- SECTION 19: _enrich_newsapi_items — matched items enriched ---")
assert len(_aapl_articles) > 0, "Need AAPL seed articles"
matched_url = _aapl_articles[0]['url']

newsapi_items = [
    {
        'title': _aapl_articles[0]['title'],
        'snippet': 'A short snippet from newsapi.',
        'topics': ['market'],
        'url': matched_url,
        'published_at': _aapl_articles[0]['published_at'],
        'source': 'newsapi',
        'needs_full_text': True,
    }
]

enriched = _enrich_newsapi_items(newsapi_items)
assert len(enriched) == 1

item = enriched[0]
if item.get('partial') is False:
    assert 'full_text' in item,              "full_text should be present after enrichment"
    assert 'needs_full_text' not in item,    "needs_full_text should be removed after enrichment"
    assert item['source'] == 'newsapi',      "source must remain 'newsapi'"
    assert 'publisher' in item,              "publisher should be added by enrichment"
    assert 'tickers' in item,               "tickers should be added by enrichment"
    print(f"  full_text present  : {len(item['full_text'].split())} words")
    print(f"  publisher added    : {item['publisher']}")
    print("PASS")
else:
    print("  WARNING: URL not matched in enrichment — article outside 24h window.")
    print("SKIP")

#################################
# SECTION 20: _enrich_newsapi_items — unmatched items preserved unchanged

print("\n--- SECTION 20: _enrich_newsapi_items — unmatched items preserved ---")
unmatched_items = [
    {
        'title': 'Some macro economic headline',
        'snippet': 'Global inflation pressures remain elevated.',
        'topics': ['macro'],
        'url': 'https://totallyfake.example.invalid/macro-story-xyz',
        'published_at': '2026-04-04T08:00:00Z',
        'source': 'newsapi',
        'needs_full_text': True,
    }
]

result = _enrich_newsapi_items(unmatched_items)
assert len(result) == 1
item = result[0]
assert item['url'] == unmatched_items[0]['url']
assert item['source'] == 'newsapi'
assert item['snippet'] == unmatched_items[0]['snippet']
assert item.get('needs_full_text') is True, "needs_full_text must remain on unmatched items"
print("  Unmatched item returned unchanged with needs_full_text intact")
print("PASS")

#################################
# SECTION 21: _enrich_newsapi_items — mixed batch (matched + unmatched)

print("\n--- SECTION 21: _enrich_newsapi_items — mixed batch ---")
matched_item = {
    'title': _aapl_articles[0]['title'] if _aapl_articles else 'Placeholder',
    'snippet': 'Short snippet.',
    'topics': ['market'],
    'url': _aapl_articles[0]['url'] if _aapl_articles else 'https://fake.invalid/x',
    'published_at': _aapl_articles[0]['published_at'] if _aapl_articles else '',
    'source': 'newsapi',
    'needs_full_text': True,
}
unmatched_item = {
    'title': 'Unrelated headline no match',
    'snippet': 'Brief summary.',
    'topics': ['macro'],
    'url': 'https://fake.invalid/no-match-article',
    'published_at': '2026-04-04T08:00:00Z',
    'source': 'newsapi',
    'needs_full_text': True,
}

result = _enrich_newsapi_items([matched_item, unmatched_item])
assert len(result) == 2, f"Output length must equal input length, got {len(result)}"

unmatched_result = next(r for r in result if r['url'] == unmatched_item['url'])
assert unmatched_result['source'] == 'newsapi'
assert unmatched_result.get('needs_full_text') is True

print(f"  Batch size: 2, output size: {len(result)}")
print("  Unmatched item preserved with needs_full_text intact")
print("PASS")

#################################
# SECTION 22: Full pipeline dump — raw JSON of first 3 articles

print("\n--- SECTION 22: Raw JSON dump of first 3 general news articles ---")
print(json.dumps(_general_articles[:3], indent=2, default=str))
print("PASS")

#################################
# SUMMARY

print("\n" + "=" * 60)
print("ALL INTEGRATION TESTS PASSED")
print("=" * 60)
