---
name: status-report
description: >
  Generate a comprehensive session status report for the paper trader project.
  Run this at the start of any session to understand what was last built, which
  components are working, which tests are failing, and what to work on next.
  Trigger when the user says "status", "what did we do last time", "where did we
  leave off", "what should I work on", "catch me up", or runs /status-report.
---

# Status Report

Generate a full status report for the paper trader project. This skill runs the
integration test suite, reads recent git history, checks which modules from the
CLAUDE.md implementation priority list exist, cross-references failing tests against
those modules, and produces a clear picture of where the project stands and what
to work on next.

---

## Step 1 — Run the Integration Tests

Run the full test suite, excluding expensive Claude API tests:

```
pytest tests/ -m "not expensive" -v --tb=short 2>&1
```

If the `tests/` directory does not exist yet, skip this step and note in the report
that no tests have been written yet.

Capture the full output. Parse it for:
- Total tests collected
- Number passed
- Number failed
- Number errored (import errors, setup failures)
- Names of every failing or erroring test function
- The short traceback for each failure (--tb=short gives the key line)

If pytest is not installed, note that in the report and do not fail — continue with
the remaining steps.

---

## Step 2 — Read Recent Git History

Run:
```
git log --oneline --since="14 days ago"
```

List every commit. Group them loosely by what they touch (fetchers, engine, risk, etc.)
based on the commit message. This is the "last session" summary.

Also run:
```
git status
```

Note any uncommitted changes — these are in-progress work that hasn't been committed yet.

---

## Step 3 — Check Module Existence Against Implementation Priority

The CLAUDE.md file defines this implementation priority order:

```
1.  db/client.py
2.  db/schema.sql
3.  scheduler/loop.py
4.  fetchers/discovery.py
5.  engine/strategies.py
6.  engine/regime.py
7.  engine/combiner.py
8.  risk/manager.py
9.  feedback/logger.py
10. feedback/outcomes.py
11. feedback/weights.py
12. dashboard/app.py
```

Additionally check for the fetcher modules that are required before the engine runs:
```
fetchers/marketaux.py
fetchers/newsapi.py
fetchers/scraper.py
fetchers/market.py
fetchers/aggregator.py
executor/alpaca.py
```

For each file, check whether it exists. Mark each as:
- EXISTS — file is present
- MISSING — file has not been created yet
- STUB — file exists but appears to be a placeholder (< 20 lines of actual logic)

A STUB is when the file exists but the key functions are not implemented (e.g., the
function exists but has only `pass` or `raise NotImplementedError`). Read the file
briefly to make this determination.

---

## Step 4 — Cross-Reference Tests with Modules

For each module that EXISTS or is a STUB, check whether a corresponding test file
exists in the `tests/` directory.

For each test file that exists, look up how many tests it contains and how many
passed vs failed in Step 1.

Produce a mapping like:

| Module              | Status  | Test File           | Tests | Pass | Fail |
|---------------------|---------|---------------------|-------|------|------|
| db/client.py        | EXISTS  | tests/db/test_client.py | 3  |  3   |  0   |
| engine/combiner.py  | EXISTS  | tests/engine/test_combiner.py | 5 | 3 | 2 |
| engine/regime.py    | STUB    | MISSING             |  —    |  —   |  —   |
| risk/manager.py     | MISSING | MISSING             |  —    |  —   |  —   |

---

## Step 5 — Diagnose Failing Tests

For each failing test, read the short traceback from pytest output and determine:

- Is this a real bug in the module logic?
- Is this a missing module (ImportError because the module doesn't exist yet)?
- Is this a real API connectivity issue (network, missing key)?
- Is this a DB state issue (stale test data from a previous run)?

Summarize each failure in one sentence. Do not go deep into debugging here — the goal
is to surface what needs attention, not fix everything at once.

---

## Step 6 — Determine Next Step

Using everything gathered above, determine ONE recommended next step. Priority order:

1. If there are failing tests for an existing module → fix the module (the test found
   a real bug).
2. If a module is a STUB and it's the next item in implementation priority order →
   implement it.
3. If a module is MISSING and it's next in implementation priority order → build it.
4. If all modules exist and all tests pass → write tests for any modules that are
   missing test coverage.
5. If tests are missing entirely → run the test-writer skill on the first untested module.

Be specific. Don't say "work on the engine". Say "Implement engine/combiner.py —
regime modifier math and threshold logic are not yet built (engine/combiner.py is a
stub with no combiner logic)."

---

## Step 7 — Format and Output the Report

Output the report using this exact format:

```
═══════════════════════════════════════════════════════════
PAPER TRADER — SESSION STATUS REPORT
Generated: <current date and time>
═══════════════════════════════════════════════════════════

## LAST SESSION (git history, past 14 days)

<bullet list of commits, grouped loosely by area>

Uncommitted changes: <list files, or "none">

───────────────────────────────────────────────────────────

## TEST HEALTH

Total tests run: X  |  Passed: X  |  Failed: X  |  Errors: X
(Excludes @pytest.mark.expensive tests — Claude API calls)

| Module                  | Status  | Tests | Pass | Fail |
|-------------------------|---------|-------|------|------|
| db/client.py            | EXISTS  |   3   |   3  |   0  |
| engine/combiner.py      | EXISTS  |   5   |   3  |   2  |
| engine/regime.py        | STUB    |   —   |   —  |   —  |
...

───────────────────────────────────────────────────────────

## FAILING TESTS

<For each failure:>
• test_<name> (tests/<path>): <one-sentence diagnosis>

If none: "All tests passing."

───────────────────────────────────────────────────────────

## IMPLEMENTATION GAPS

Modules not yet built or stubbed (from CLAUDE.md priority list):
• <list each MISSING or STUB module>

Modules with no test coverage:
• <list each module that exists but has no test file>

───────────────────────────────────────────────────────────

## RECOMMENDED NEXT STEP

<One clear, specific action. Name the file, describe what needs to be done,
and explain why it's the highest priority right now.>

═══════════════════════════════════════════════════════════
```

---

## Step 8 — Save the Report

After outputting the report to the user, save a copy to:
```
.claude/last-status.md
```

Overwrite any previous version. This file allows the next session's status-report
run to compare current state against last session's state — if the same test is
failing two sessions in a row, that is worth flagging explicitly.

If a previous `.claude/last-status.md` exists, read it before generating the new
report. Add a note under "LAST SESSION" if any previously failing tests are still
failing — that indicates a known issue that hasn't been addressed.
