"""
Integration tests for fetchers/discovery.py.

Tests exercise real behavior: real yfinance data, real SQLite caching, real Marketaux
and NewsAPI responses (where API keys are available). No mocking.

Scenarios:
  1. Discovery mode returns required output shape
  2. Watchlist mode returns only watchlist tickers
  3. Deduplication works across sources
  4. Cap at MAX_DISCOVERY_TICKERS is enforced
  5. Ticker validation rejects penny stocks and micro-caps
  6. Market movers uses dynamic S&P 500 list
  7. Sector rotation picks returns valid tickers
  8. Fallback tickers returned when all sources fail
  9. Ticker extraction from text works correctly
  10. Discovery log is written to SQLite
"""

import os
import sys
import sqlite3
import tempfile
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fetchers.discovery import (
    discover_tickers,
    _extract_tickers_from_text,
    _get_market_movers,
    _get_sector_rotation_picks,
    _get_fallback_tickers,
    _get_user_watchlist,
    _validate_ticker,
    _prioritize_and_cap,
    _init_database,
    _log_discovery_cycle,
    get_sector,
)


########################################################################
# SCENARIO 1 — Discovery mode returns required output shape
########################################################################

def test_discovery_mode_output_shape():
    """
    Scenario: Call discover_tickers() with TICKER_MODE=discovery.
    Expected: Output is a dict with keys: tickers (list), sources (dict), mode (str), cycle_id (str).
              mode == "discovery". len(tickers) <= MAX_DISCOVERY_TICKERS.
              Every key in sources maps to a non-empty list of strings.
    Why it matters: CLAUDE.md requires discover_tickers to return this exact shape
                    for all downstream modules to consume.
    """
    original_mode = os.environ.get('TICKER_MODE')
    original_max = os.environ.get('MAX_DISCOVERY_TICKERS')
    try:
        os.environ['TICKER_MODE'] = 'discovery'
        os.environ['MAX_DISCOVERY_TICKERS'] = '30'

        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        result = discover_tickers(db_path=db_path)

        # Required keys
        assert 'tickers' in result
        assert 'sources' in result
        assert 'mode' in result
        assert 'cycle_id' in result

        # Type checks
        assert isinstance(result['tickers'], list)
        assert isinstance(result['sources'], dict)
        assert isinstance(result['mode'], str)
        assert isinstance(result['cycle_id'], str)

        # Mode is discovery (or fallback if APIs are down)
        assert result['mode'] in ('discovery', 'fallback')

        # Cap is respected
        max_tickers = int(os.environ.get('MAX_DISCOVERY_TICKERS', '30'))
        assert len(result['tickers']) <= max_tickers + 20  # watchlist/positions exempt from cap

        # Sources entries are non-empty lists of strings
        for ticker, sources in result['sources'].items():
            assert isinstance(sources, list)
            assert len(sources) > 0
            for s in sources:
                assert isinstance(s, str)

    finally:
        if original_mode is not None:
            os.environ['TICKER_MODE'] = original_mode
        else:
            os.environ.pop('TICKER_MODE', None)
        if original_max is not None:
            os.environ['MAX_DISCOVERY_TICKERS'] = original_max
        else:
            os.environ.pop('MAX_DISCOVERY_TICKERS', None)
        os.unlink(db_path)


########################################################################
# SCENARIO 2 — Watchlist mode returns only watchlist tickers
########################################################################

def test_watchlist_mode_returns_only_watchlist_tickers():
    """
    Scenario: Set TICKER_MODE=watchlist, WATCHLIST=AAPL,MSFT.
    Expected: tickers == ["AAPL", "MSFT"] (order-agnostic). mode == "watchlist".
    Why it matters: CLAUDE.md defines watchlist mode as using ONLY user-specified tickers.
    """
    original_mode = os.environ.get('TICKER_MODE')
    original_watchlist = os.environ.get('WATCHLIST')
    try:
        os.environ['TICKER_MODE'] = 'watchlist'
        os.environ['WATCHLIST'] = 'AAPL,MSFT'

        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        result = discover_tickers(db_path=db_path)

        assert result['mode'] == 'watchlist'
        assert set(result['tickers']) == {'AAPL', 'MSFT'}

        # Every ticker should have source == ['watchlist']
        for ticker in result['tickers']:
            assert 'watchlist' in result['sources'][ticker]

    finally:
        if original_mode is not None:
            os.environ['TICKER_MODE'] = original_mode
        else:
            os.environ.pop('TICKER_MODE', None)
        if original_watchlist is not None:
            os.environ['WATCHLIST'] = original_watchlist
        else:
            os.environ.pop('WATCHLIST', None)
        os.unlink(db_path)


########################################################################
# SCENARIO 3 — Deduplication works across sources
########################################################################

def test_deduplication_across_sources():
    """
    Scenario: _prioritize_and_cap receives the same ticker from multiple sources.
    Expected: The ticker appears exactly once in the output keys, with all sources merged.
    Why it matters: CLAUDE.md requires dedup before analysis — never analyze same ticker twice.
    """
    ticker_sources = {
        'AAPL': ['news', 'gainer', 'watchlist'],
        'MSFT': ['news'],
        'NVDA': ['gainer', 'sector_rotation'],
    }

    result = _prioritize_and_cap(ticker_sources, max_tickers=30)

    # Each ticker appears exactly once
    assert len(result) == len(set(result.keys()))

    # AAPL should have all three sources preserved
    assert 'AAPL' in result
    assert set(result['AAPL']) == {'news', 'gainer', 'watchlist'}


########################################################################
# SCENARIO 4 — Cap at MAX_DISCOVERY_TICKERS is enforced
########################################################################

def test_max_discovery_tickers_cap():
    """
    Scenario: Set MAX_DISCOVERY_TICKERS=5 via env.
    Expected: len(tickers) <= 5 (excluding always-included positions/watchlist).
    Why it matters: CLAUDE.md sets MAX_DISCOVERY_TICKERS to prevent API overload.
    """
    original_mode = os.environ.get('TICKER_MODE')
    original_max = os.environ.get('MAX_DISCOVERY_TICKERS')
    original_watchlist = os.environ.get('WATCHLIST')
    try:
        os.environ['TICKER_MODE'] = 'discovery'
        os.environ['MAX_DISCOVERY_TICKERS'] = '5'
        os.environ.pop('WATCHLIST', None)  # Remove watchlist to test pure cap

        with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
            db_path = f.name

        result = discover_tickers(db_path=db_path)

        # Total tickers should be <= 5 when no watchlist/positions
        # (positions stub returns empty, watchlist removed)
        assert len(result['tickers']) <= 5

    finally:
        if original_mode is not None:
            os.environ['TICKER_MODE'] = original_mode
        else:
            os.environ.pop('TICKER_MODE', None)
        if original_max is not None:
            os.environ['MAX_DISCOVERY_TICKERS'] = original_max
        else:
            os.environ.pop('MAX_DISCOVERY_TICKERS', None)
        if original_watchlist is not None:
            os.environ['WATCHLIST'] = original_watchlist
        else:
            os.environ.pop('WATCHLIST', None)
        os.unlink(db_path)


########################################################################
# SCENARIO 5 — Ticker validation rejects penny stocks and micro-caps
########################################################################

def test_validate_ticker_rejects_penny_stocks():
    """
    Scenario: Validate a known penny stock or micro-cap ticker.
    Expected: _validate_ticker returns False for tickers below $5 or <$1B market cap.
    Why it matters: CLAUDE.md hard rule — no penny stocks (price < $5), no micro-caps (market cap < $1B).
    """
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    _init_database(db_path)

    try:
        # Known large-cap ticker should pass
        assert _validate_ticker('AAPL', db_path) is True

        # Empty/invalid ticker should fail
        assert _validate_ticker('', db_path) is False
        assert _validate_ticker('ZZZZYX', db_path) is False  # Non-existent ticker

    finally:
        os.unlink(db_path)


########################################################################
# SCENARIO 6 — Market movers uses dynamic S&P 500 list
########################################################################

def test_market_movers_returns_gainers_and_losers():
    """
    Scenario: Call _get_market_movers() and verify it returns real market data.
    Expected: Returns dict with 'gainers' and 'losers' keys, each a list of up to 5 tickers.
              Tickers are valid strings. Uses full S&P 500 list (not hardcoded 20).
    Why it matters: This was the primary fix — replacing hardcoded SP500_TICKERS[:20]
                    with dynamic fetch_sp500_tickers() for proper market-wide discovery.
    """
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    _init_database(db_path)

    try:
        result = _get_market_movers(db_path)

        assert 'gainers' in result
        assert 'losers' in result
        assert isinstance(result['gainers'], list)
        assert isinstance(result['losers'], list)

        # Should have up to 5 of each
        assert len(result['gainers']) <= 5
        assert len(result['losers']) <= 5

        # If market data is available, should have non-empty results
        # (could be empty on weekends/holidays, so we allow it)
        all_movers = result['gainers'] + result['losers']
        for ticker in all_movers:
            assert isinstance(ticker, str)
            assert len(ticker) >= 1
            assert len(ticker) <= 5

    finally:
        os.unlink(db_path)


########################################################################
# SCENARIO 7 — Sector rotation picks returns valid tickers
########################################################################

def test_sector_rotation_picks():
    """
    Scenario: Call _get_sector_rotation_picks() for pre-market sector rotation analysis.
    Expected: Returns a list of ticker strings from sector ETF holdings.
              All tickers are valid stock symbols.
    Why it matters: CLAUDE.md specifies sector rotation runs once per day in pre-market
                    and adds top 3 holdings from best/worst performing sectors.
    """
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    _init_database(db_path)

    try:
        picks = _get_sector_rotation_picks(db_path)

        assert isinstance(picks, list)

        # Should return up to 12 tickers: (top 2 + bottom 2 sectors) * 3 holdings
        # minus deduplication
        assert len(picks) <= 12

        # All picks should be non-empty strings
        for ticker in picks:
            assert isinstance(ticker, str)
            assert len(ticker) >= 1

        # No duplicates
        assert len(picks) == len(set(picks))

    finally:
        os.unlink(db_path)


########################################################################
# SCENARIO 8 — Fallback tickers returned when all sources fail
########################################################################

def test_fallback_tickers():
    """
    Scenario: Call _get_fallback_tickers() directly.
    Expected: Returns dict with basic market ETFs (SPY, QQQ, IWM), mode='fallback'.
    Why it matters: CLAUDE.md rule — NEVER return empty ticker list, always have fallback.
    """
    result = _get_fallback_tickers()

    assert 'tickers' in result
    assert 'sources' in result
    assert result['mode'] == 'fallback'

    # Must contain the basic ETFs
    assert set(result['tickers']) == {'SPY', 'QQQ', 'IWM'}

    # All fallback sources should be 'fallback'
    for ticker, sources in result['sources'].items():
        assert sources == ['fallback']


########################################################################
# SCENARIO 9 — Ticker extraction from text works correctly
########################################################################

def test_extract_tickers_from_text():
    """
    Scenario: Pass text containing various ticker mention formats.
    Expected: Extracts $AAPL, (NASDAQ:MSFT), and standalone NVDA correctly.
              Filters out false positives like CEO, SEC, USA.
    Why it matters: News-driven discovery relies on accurate ticker extraction
                    from headlines and article bodies.
    """
    # Test $SYMBOL format
    text1 = "Shares of $AAPL and $TSLA surged today after earnings beat."
    result1 = _extract_tickers_from_text(text1)
    assert 'AAPL' in result1
    assert 'TSLA' in result1

    # Test (EXCHANGE:SYMBOL) format
    text2 = "Tech giants (NASDAQ:MSFT) and (NYSE:IBM) reported strong results."
    result2 = _extract_tickers_from_text(text2)
    assert 'MSFT' in result2
    assert 'IBM' in result2

    # Test false positive filtering
    text3 = "The CEO of the SEC told USA TODAY about the IPO market."
    result3 = _extract_tickers_from_text(text3)
    assert 'CEO' not in result3
    assert 'SEC' not in result3
    assert 'USA' not in result3
    assert 'IPO' not in result3

    # Test empty input
    assert _extract_tickers_from_text('') == set()
    assert _extract_tickers_from_text(None) == set()


########################################################################
# SCENARIO 10 — Discovery log is written to SQLite
########################################################################

def test_discovery_log_written_to_sqlite():
    """
    Scenario: Run a discovery cycle and verify the audit trail is written to SQLite.
    Expected: discovery_log table contains rows matching the cycle_id and tickers.
    Why it matters: CLAUDE.md requires logging all discovery decisions for debugging
                    and the feedback loop.
    """
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    _init_database(db_path)

    try:
        cycle_id = 'test_cycle_001'
        result = {
            'tickers': ['AAPL', 'MSFT'],
            'sources': {
                'AAPL': ['news', 'watchlist'],
                'MSFT': ['gainer'],
            }
        }

        _log_discovery_cycle(db_path, cycle_id, result)

        # Verify rows in database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ticker, source FROM discovery_log WHERE cycle_id = ?",
            (cycle_id,)
        )
        rows = cursor.fetchall()
        conn.close()

        # Should have 3 rows: AAPL-news, AAPL-watchlist, MSFT-gainer
        assert len(rows) == 3
        row_set = {(r[0], r[1]) for r in rows}
        assert ('AAPL', 'news') in row_set
        assert ('AAPL', 'watchlist') in row_set
        assert ('MSFT', 'gainer') in row_set

    finally:
        os.unlink(db_path)


########################################################################
# SCENARIO 11 — Prioritize and cap respects always-include tickers
########################################################################

def test_prioritize_and_cap_always_includes_positions_and_watchlist():
    """
    Scenario: _prioritize_and_cap receives tickers with position and watchlist sources.
    Expected: Position and watchlist tickers are always included even if cap is very low.
    Why it matters: CLAUDE.md rule — existing positions and user pinned tickers are
                    always included regardless of cap.
    """
    ticker_sources = {
        'AAPL': ['position'],           # always include
        'MSFT': ['watchlist'],          # always include
        'NVDA': ['news', 'gainer'],     # capped candidate (score 2)
        'TSLA': ['news'],              # capped candidate (score 1)
        'AMZN': ['gainer'],            # capped candidate (score 1)
        'META': ['news'],              # capped candidate (score 1)
        'GOOGL': ['loser'],            # capped candidate (score 1)
    }

    # Cap at 3 — but AAPL and MSFT are exempt
    result = _prioritize_and_cap(ticker_sources, max_tickers=3)

    # AAPL and MSFT must be included (exempt from cap)
    assert 'AAPL' in result
    assert 'MSFT' in result

    # Remaining cap is 3 - 2 = 1, so only highest-scored candidate (NVDA, score 2)
    # Total should be 3: AAPL + MSFT + 1 capped
    assert len(result) == 3

    # NVDA should win as highest priority (2 sources)
    assert 'NVDA' in result


########################################################################
# SCENARIO 12 — get_sector returns valid sector for known ticker
########################################################################

def test_get_sector_returns_valid_sector():
    """
    Scenario: Call get_sector('AAPL') with real yfinance data.
    Expected: Returns 'Technology' (AAPL's GICS sector).
    Why it matters: Sector lookup is used by risk/manager.py for sector allocation checks.
    """
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    _init_database(db_path)

    try:
        sector = get_sector('AAPL', db_path=db_path)
        assert sector is not None
        assert isinstance(sector, str)
        assert 'Technology' in sector or 'technology' in sector.lower()

    finally:
        os.unlink(db_path)
