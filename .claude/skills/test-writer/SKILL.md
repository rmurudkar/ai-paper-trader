---
name: test-writer
description: >
  Write or update integration tests for any paper trader component that was just created
  or modified. Use this skill after building or changing any file in engine/, fetchers/,
  risk/, feedback/, executor/, scheduler/, db/, or dashboard/. Also trigger when the user
  says "write tests", "add tests", "update tests", or "test this component".
---

# Integration Test Writer

This skill writes and maintains **integration tests** for the autonomous paper trader.
Tests exercise real behavior against real infrastructure — real Turso DB, real Alpaca paper
account, real market data from yfinance, real Marketaux/NewsAPI responses. The only
exceptions are Claude API calls (marked `@pytest.mark.expensive` and skipped by default)
and a narrow set of market-condition edge cases that are genuinely impossible to observe
on demand (e.g. RSI < 30, VIX > 30), which use real historical data via yfinance rather
than fake data.

**No mocking of infrastructure.** If something breaks in a test, the failure should reveal
a real problem with the component, not a misconfigured mock.

---

## Step 1 — Identify the Module

Determine which module was just created or changed. The argument passed to this skill
is the module path (e.g. `engine/combiner.py`). If no argument is given, infer from
the most recently edited file in the session.

Read the module file in full before writing any tests.

---

## Step 2 — Locate or Create the Test File

Test files mirror the module directory structure under `tests/`:

| Module                        | Test file                              |
|-------------------------------|----------------------------------------|
| `fetchers/discovery.py`       | `tests/fetchers/test_discovery.py`     |
| `fetchers/marketaux.py`       | `tests/fetchers/test_marketaux.py`     |
| `fetchers/newsapi.py`         | `tests/fetchers/test_newsapi.py`       |
| `fetchers/scraper.py`         | `tests/fetchers/test_scraper.py`       |
| `fetchers/market.py`          | `tests/fetchers/test_market.py`        |
| `fetchers/aggregator.py`      | `tests/fetchers/test_aggregator.py`    |
| `engine/sentiment.py`         | `tests/engine/test_sentiment.py`       |
| `engine/strategies.py`        | `tests/engine/test_strategies.py`      |
| `engine/regime.py`            | `tests/engine/test_regime.py`          |
| `engine/combiner.py`          | `tests/engine/test_combiner.py`        |
| `risk/manager.py`             | `tests/risk/test_manager.py`           |
| `executor/alpaca.py`          | `tests/executor/test_alpaca.py`        |
| `feedback/logger.py`          | `tests/feedback/test_logger.py`        |
| `feedback/outcomes.py`        | `tests/feedback/test_outcomes.py`      |
| `feedback/weights.py`         | `tests/feedback/test_weights.py`       |
| `scheduler/loop.py`           | `tests/scheduler/test_loop.py`         |
| `db/client.py`                | `tests/db/test_client.py`              |

If the test file already exists, read it first, then update only the test functions
that correspond to changed logic. Do not delete or overwrite unaffected tests.

If the test file does not exist, create it from scratch using the template below.

Also ensure `tests/conftest.py` exists. If it does not, create it (see Step 3).

---

## Step 3 — Shared conftest.py

`tests/conftest.py` provides environment setup shared by all test files. If it does
not exist, create it with this content:

```python
import os
import pytest
from dotenv import load_dotenv

# Load real credentials from .env at the project root
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

def pytest_configure(config):
    """Register custom marks so pytest does not warn about unknown marks."""
    config.addinivalue_line(
        "markers",
        "expensive: marks tests that call the Claude API (skipped by default, run with -m expensive)"
    )
```

---

## Step 4 — Test File Structure

Every test file must follow this structure exactly:

```python
"""
Integration tests for <module name>.

Tests exercise real behavior: real API calls, real Turso DB writes, real yfinance data.
Claude API tests are marked @pytest.mark.expensive and are skipped on regular runs.
Run expensive tests explicitly: pytest -m expensive

Scenarios:
  1. <one-line description of scenario 1>
  2. <one-line description of scenario 2>
  ...
"""

import os
import pytest
# ... imports specific to this module

########################################################################
# SCENARIO 1 — <short name>
########################################################################

def test_<descriptive_name>():
    """
    Scenario: <what is being tested>
    Expected: <what the correct output or behavior is>
    Why it matters: <which CLAUDE.md rule or contract this validates>
    """
    # test body

########################################################################
# SCENARIO 2 — <short name>
########################################################################

def test_<descriptive_name_2>():
    """
    Scenario: <what is being tested>
    Expected: <what the correct output or behavior is>
    Why it matters: <which CLAUDE.md rule or contract this validates>
    """
    # test body
```

---

## Step 5 — What to Test Per Module

Read the corresponding section of CLAUDE.md for the module's data contract and rules.
The scenarios below are the minimum required — add more if the module has additional
logic or edge cases.

### fetchers/discovery.py

```
SCENARIO 1 — discovery mode returns required output shape
  Call get_discovery_tickers() with TICKER_MODE=discovery.
  Assert output is a dict with keys: tickers (list), sources (dict), mode (str).
  Assert mode == "discovery".
  Assert len(tickers) <= MAX_DISCOVERY_TICKERS.
  Assert every key in sources maps to a non-empty list of strings.

SCENARIO 2 — watchlist mode returns only watchlist tickers
  Set TICKER_MODE=watchlist, WATCHLIST=AAPL,MSFT.
  Assert tickers == ["AAPL", "MSFT"] (order-agnostic).
  Assert mode == "watchlist".

SCENARIO 3 — deduplication works across sources
  Manually trigger a case where the same ticker appears in news and gainers.
  Assert it appears exactly once in the output tickers list.

SCENARIO 4 — cap at MAX_DISCOVERY_TICKERS is enforced
  Set MAX_DISCOVERY_TICKERS=5 via env.
  Assert len(tickers) <= 5.
```

### fetchers/marketaux.py

```
SCENARIO 1 — output shape and required fields
  Call the Marketaux fetcher.
  Assert each item has: title, ticker, sentiment_score, url, published_at, source.
  Assert source == "marketaux" on every item.
  Assert all sentiment_score values are between -1.0 and 1.0.

SCENARIO 2 — sentiment_score is present and not re-analyzed
  Confirm sentiment_score is populated directly from the API response, not computed.
  Assert no Claude API calls are made (check by confirming no ANTHROPIC_API_KEY usage
  in the code path — read the source if unsure).
```

### fetchers/newsapi.py

```
SCENARIO 1 — output shape and required fields
  Call the NewsAPI fetcher.
  Assert each item has: title, url, published_at, source.
  Assert source == "newsapi" on every item.

SCENARIO 2 — scraper is invoked for full text
  Assert full_text is populated on at least one article (not empty string).
  This confirms the fetcher is calling scraper.py downstream.
```

### fetchers/scraper.py

```
SCENARIO 1 — successful scrape of an accessible article
  Pass a known publicly accessible financial news URL (e.g. Reuters, CNBC).
  Assert result has full_text that is non-empty.
  Assert partial == False.
  Assert len(full_text.split()) <= 1200 (truncation enforced).

SCENARIO 2 — paywall detection returns snippet only
  Pass a known paywalled URL (wsj.com or ft.com or bloomberg.com).
  Assert partial == True.
  Assert full_text is either empty or contains only a short snippet.
  Why it matters: CLAUDE.md hard rule — never scrape paywalled domains.

SCENARIO 3 — graceful failure on unreachable URL
  Pass a URL that will fail (e.g. https://this-domain-does-not-exist-xyz.com/article).
  Assert result has partial == True.
  Assert no exception is raised (failure is soft, not a crash).
```

### fetchers/market.py

```
SCENARIO 1 — ticker market data has required fields
  Call the market data fetcher for ["AAPL", "MSFT"].
  Assert output is a dict keyed by ticker.
  Assert each ticker entry has: price, volume, ma_20, ma_50, ma_200, rsi_14.
  Assert price > 0 for all tickers.
  Assert 0 <= rsi_14 <= 100 for all tickers.

SCENARIO 2 — macro indicators are present
  Assert the return also contains macro indicators with keys:
  vix, spy_price, spy_ma_200, yield_spread.
  Assert vix > 0.

SCENARIO 3 — historical data for RSI extremes (edge case)
  Use yfinance .history(start="2020-03-16", end="2020-03-20") for a known crash period.
  Feed that historical data into the RSI calculation function directly.
  Assert RSI < 35 for at least one day in that window.
  Why it matters: validates the mean reversion strategy can observe oversold conditions.
```

### engine/strategies.py

```
SCENARIO 1 — momentum signal fires on real uptrending ticker
  Fetch real market data for a ticker currently in an uptrend (use discovery output).
  Call momentum strategy.
  Assert output has keys: signal, confidence, strategy, reason.
  Assert signal is one of: BUY, SELL, HOLD.
  Assert 0.0 <= confidence <= 1.0.

SCENARIO 2 — mean reversion fires on historically oversold data
  Use yfinance historical data for AAPL from 2020-03-16 to 2020-03-20.
  Feed into mean reversion strategy.
  Assert at least one day produces signal == BUY (RSI was < 30 during COVID crash).
  Why it matters: validates the oversold detection path actually fires.

SCENARIO 3 — MA crossover signal shape
  Call MA crossover strategy on any ticker with sufficient history.
  Assert output has all required fields with valid types.
  Assert strategy == "ma_crossover".

SCENARIO 4 — volume surge signal requires both volume AND price direction
  Fetch a ticker with recent high volume.
  Call volume surge strategy.
  Assert that if volume > 1.5x average AND price is up, signal is BUY.
  Assert that if volume > 1.5x average AND price is down, signal is SELL.
  If volume is normal, assert signal is HOLD.
```

### engine/regime.py

```
SCENARIO 1 — regime classification returns required shape
  Call regime classifier with real current macro data.
  Assert output has: regime, vix, spy_vs_200ma, yield_spread, macro_sentiment, confidence.
  Assert regime is one of: risk_on, risk_off, neutral.
  Assert 0.0 <= confidence <= 1.0.

SCENARIO 2 — risk_off conditions in historical crash data
  Use yfinance to fetch macro indicators from 2020-03-20 (VIX > 60, SPY below 200MA).
  Feed into regime classifier.
  Assert regime == "risk_off".
  Why it matters: validates the risk_off detection path — critical for not going long
  during a crash.

SCENARIO 3 — risk_on conditions in normal market data
  Use yfinance data from a known calm period (e.g. 2021-07-01 to 2021-07-15,
  VIX < 20, SPY above 200MA).
  Assert regime == "risk_on" or neutral.

SCENARIO 4 — inverted yield curve triggers risk_off
  Construct macro indicators with yield_spread = -0.8 (strongly inverted).
  Feed into regime classifier with otherwise neutral conditions.
  Assert regime is risk_off or neutral (never risk_on with inverted curve).
  Note: this is the one scenario where direct input construction is acceptable
  because yield curve inversion is not currently observable in real-time data.
```

### engine/combiner.py

```
SCENARIO 1 — combiner produces required output shape
  Pass real strategy signals + regime + sentiment for a ticker.
  Assert output has: ticker, signal, confidence, components, regime, rationale.
  Assert signal is one of: BUY, SELL, HOLD.
  Assert 0.0 <= confidence <= 1.0.

SCENARIO 2 — regime modifier reduces BUY confidence in risk_off
  Set regime = risk_off.
  Pass a BUY signal with confidence = 0.80.
  Assert output confidence < 0.80 (regime penalty applied).
  Assert output confidence approximately equals 0.80 * 0.70 = 0.56.

SCENARIO 3 — regime modifier reduces SELL confidence in risk_on
  Set regime = risk_on.
  Pass a SELL signal with confidence = 0.80.
  Assert output confidence < 0.80.
  Assert output confidence approximately equals 0.80 * 0.80 = 0.64.

SCENARIO 4 — threshold boundaries: BUY requires confidence > 0.55
  Pass combined signals that average to exactly 0.54.
  Assert final signal == HOLD.
  Pass combined signals that average to exactly 0.56.
  Assert final signal == BUY.

SCENARIO 5 — learned weights from Turso are used
  Insert a test weight into the Turso weights table (category=strategy, name=momentum,
  weight=0.2). Run combiner with a BUY momentum signal.
  Assert the momentum signal is downweighted compared to a weight of 1.0.
  Clean up: delete the test weight row after the test.
```

### risk/manager.py

```
SCENARIO 1 — approved signal returns correct output shape
  Pass a valid BUY signal for a non-penny, non-micro-cap ticker within all limits.
  Assert output has: approved, position_size, shares, entry_price, stop_loss,
  take_profit, portfolio_allocation_pct.
  Assert approved == True.
  Assert stop_loss == entry_price * 0.97 (3% below).
  Assert take_profit == entry_price * 1.03 (3% above).

SCENARIO 2 — penny stock rejected
  Pass a BUY signal for a ticker priced below $5.
  Assert approved == False.
  Assert "penny" in reason.lower() or "price" in reason.lower().

SCENARIO 3 — single ticker cap enforced (10% max)
  Simulate portfolio state where AAPL already represents 9% of portfolio.
  Pass a new BUY for AAPL that would push it to 13%.
  Assert approved == False.
  Assert "10%" in reason or "ticker" in reason.lower().

SCENARIO 4 — total portfolio cap enforced (80% max)
  Simulate portfolio at 79% invested.
  Pass a new BUY signal that would require 3% allocation.
  Assert approved == False (would exceed 80% cap).

SCENARIO 5 — sector cap enforced (30% max)
  Simulate Technology sector at 28% of portfolio.
  Pass a BUY for an AAPL-sized position that would push Technology to 35%.
  Assert approved == False.
  Assert "sector" in reason.lower() or "30%" in reason.

SCENARIO 6 — duplicate signal within 2 hours rejected
  Submit a BUY for AAPL and record it.
  Immediately submit another BUY for AAPL.
  Assert the second signal is rejected with a reason mentioning duplicate or 2 hours.

SCENARIO 7 — stop loss calculation for SELL signal
  Pass a valid SELL signal at entry_price = 100.00.
  Assert stop_loss == 103.00 (3% above entry for short).
  Assert take_profit == 97.00 (3% below entry for short).
```

### executor/alpaca.py

```
SCENARIO 1 — paper order is placed and returns fill data
  Pass an approved BUY signal (risk manager approved output) for a liquid ticker.
  Assert the return has: order_id, symbol, filled_price, shares.
  Assert filled_price > 0.
  Assert symbol matches the ticker.
  Note: this places a real paper order on your Alpaca account.

SCENARIO 2 — market hours check prevents order outside hours
  If tests are run outside market hours (9:30 AM – 4:00 PM ET on a weekday):
  Assert the executor does not submit the order.
  Assert the return contains an error or skip message indicating market is closed.
  If tests are run during market hours, skip this scenario with pytest.skip().
```

### feedback/logger.py

```
SCENARIO 1 — trade is logged to Turso with all required fields
  Create a fake trade payload (full metadata dict as specified in CLAUDE.md section 14).
  Call the logger.
  Query Turso trades table for the inserted trade_id.
  Assert all fields are present and match the input.
  Cleanup: DELETE FROM trades WHERE trade_id = <test_trade_id>.

SCENARIO 2 — strategies_fired and article_urls are stored as JSON arrays
  Log a trade with strategies_fired=["momentum", "sentiment"].
  Query the row back.
  Assert the value parses correctly as a JSON array with 2 elements.
```

### feedback/outcomes.py

```
SCENARIO 1 — WIN outcome calculated correctly
  Insert a test trade into Turso with entry_price=100.00.
  Simulate current price = 102.50 (return = 2.5%).
  Call outcome measurement.
  Assert outcome == "WIN" (return > 1%).
  Assert return_pct approximately equals 2.5.
  Cleanup: delete test rows from trades and outcomes tables.

SCENARIO 2 — LOSS outcome calculated correctly
  Insert a test trade with entry_price=100.00.
  Simulate current price = 97.00 (return = -3.0%).
  Assert outcome == "LOSS".

SCENARIO 3 — NEUTRAL outcome for small moves
  Simulate return of 0.5% (between -1% and +1%).
  Assert outcome == "NEUTRAL".

SCENARIO 4 — stop loss exit triggers at correct price
  Insert a test trade with stop_loss_price=97.00, entry_price=100.00.
  Simulate current price = 96.50 (below stop loss).
  Assert position is closed at stop loss price (not current price).
  Assert outcome == "LOSS".
```

### feedback/weights.py

```
SCENARIO 1 — WIN nudges weight upward via EMA
  Insert a test weight row: category=strategy, name=momentum, weight=0.50.
  Record a WIN outcome for a trade where momentum fired.
  Assert new weight > 0.50.
  Assert new weight approximately equals 0.50 * 0.95 + 1.0 * 0.05 = 0.525.
  Cleanup: delete test weight row.

SCENARIO 2 — LOSS nudges weight downward
  Insert weight=0.50 for test strategy.
  Record a LOSS outcome.
  Assert new weight approximately equals 0.50 * 0.95 + 0.0 * 0.05 = 0.475.

SCENARIO 3 — weight never drops below 0.1 (floor)
  Insert weight=0.11.
  Record 5 consecutive LOSS outcomes.
  Assert weight never drops below 0.10.

SCENARIO 4 — circuit breaker trips at < 40% win rate
  Insert 10 test outcome rows: 3 WIN, 7 LOSS (30% win rate over 7 days).
  Run the circuit breaker check.
  Query Turso circuit_breaker table.
  Assert tripped == True.
  Assert win_rate_at_trip < 0.40.
  Cleanup: SET tripped = False, delete test outcome rows.
```

### engine/sentiment.py (Claude API — expensive)

```
SCENARIO 1 — @pytest.mark.expensive — sentiment score is in valid range
  Fetch a real NewsAPI article with full_text populated.
  Call analyze_newsapi_with_claude() with the article text and a relevant ticker.
  Assert output has: sentiment_score, confidence, reasoning, catalysts.
  Assert -1.0 <= sentiment_score <= 1.0.
  Assert 0.0 <= confidence <= 1.0.
  Assert len(reasoning) > 0.

SCENARIO 2 — @pytest.mark.expensive — partial article reduces confidence
  Call with a short snippet (< 100 words) and partial=True.
  Assert confidence < 0.5.
  Why it matters: partial articles should carry less weight in signal combiner.

SCENARIO 3 — @pytest.mark.expensive — Marketaux articles are never re-analyzed
  This is a code-path test. Read engine/sentiment.py.
  Assert that when source=="marketaux" is passed, the function returns the
  pre-computed sentiment_score without making any Claude API call.
  Verify by checking that client.messages.create is never reached for marketaux items.
```

---

## Step 6 — DB Cleanup Pattern

Any test that writes to Turso must clean up after itself. Use a try/finally block:

```python
def test_trade_is_logged():
    """..."""
    trade_id = "test-" + str(uuid.uuid4())
    try:
        # ... run the test, insert data
        result = db.execute("SELECT * FROM trades WHERE trade_id = ?", [trade_id])
        assert len(result.rows) == 1
    finally:
        db.execute("DELETE FROM trades WHERE trade_id = ?", [trade_id])
        db.execute("DELETE FROM outcomes WHERE trade_id = ?", [trade_id])
```

Always clean up even if the test fails. Never leave test data in the production Turso DB.

---

## Step 7 — Historical Data Pattern for Edge Cases

When a real-world condition is needed but cannot be guaranteed in current market data,
use yfinance historical data. Fetch a date range where the condition is known to have
been true. Do not construct fake data — use real historical prices.

Known dates for each condition:

| Condition              | Date range to use                    | Why                           |
|------------------------|--------------------------------------|-------------------------------|
| VIX > 30               | 2020-03-15 to 2020-03-25            | COVID crash peak              |
| VIX > 30 (alternative) | 2024-08-05 to 2024-08-07            | Yen carry unwind              |
| RSI < 30               | 2020-03-16 to 2020-03-20            | COVID crash, broadly oversold |
| RSI > 70               | 2021-11-01 to 2021-11-15            | Late meme stock era           |
| SPY below 200MA        | 2022-06-01 to 2022-09-30            | 2022 bear market              |
| Inverted yield curve   | Construct directly (yield_spread=-0.8)| 2022-2023 data not easily isolated via yfinance |

---

## Step 8 — After Writing Tests

After writing or updating the test file:

1. Run the test file to confirm there are no syntax errors or import failures:
   ```
   pytest <test_file_path> -m "not expensive" --collect-only
   ```
   This dry-run collects tests without executing them. Fix any collection errors.

2. Run one non-expensive test to confirm real connectivity works:
   ```
   pytest <test_file_path>::test_<first_scenario> -v
   ```

3. Report to the user:
   - Which test file was created or updated
   - How many scenarios were written
   - Which scenarios are marked @pytest.mark.expensive
   - Whether the collection dry-run passed
