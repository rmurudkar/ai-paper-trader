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

Generate a full status report for the paper trader project. This skill:
1. Runs a fresh audit of codebase completeness (via the audit skill logic)
2. Runs the integration test suite
3. Reads recent git history
4. Combines all three sources into one report
5. Produces one specific recommended next step

The audit takes priority — a partially implemented component that blocks the pipeline
is more urgent than a missing component or a failing test.

---

## Step 1 — Run the Audit

Execute the full audit logic from the `audit` skill against the current codebase.
Do not read a cached AUDIT.md — run the audit fresh every time status-report is invoked.

From the audit, extract:
- List of PARTIAL files (incomplete implementations) with their unchecked items
- List of STUB files (no logic at all)
- List of MISSING files
- List of architectural violations
- Any deprecated files that still exist (engine/signals.py)

This audit output feeds directly into the report. Do not display it separately —
fold it into the ARCHITECTURE HEALTH section of the report below.

---

## Step 2 — Run the Integration Tests

Run the full test suite, excluding expensive Claude API tests:

```
pytest tests/ -m "not expensive" -v --tb=short 2>&1
```

If the `tests/` directory does not exist, note "No tests written yet" and continue.
If pytest is not installed, note it and continue.

Capture:
- Total tests collected, passed, failed, errored
- Names of every failing or erroring test function
- The short traceback for each failure

---

## Step 3 — Read Recent Git History

Run:
```
git log --oneline --since="14 days ago"
git status
```

Group commits loosely by area (fetchers, engine, risk, etc.) from the commit message.
Note any uncommitted changes.

If `.claude/last-status.md` exists, read it. Note any tests that are still failing
from the previous session — these are recurring issues that need to be explicitly called out.

---

## Step 4 — Cross-Reference Tests with Modules

For each module that exists (PARTIAL, STUB, or complete), check whether a corresponding
test file exists in `tests/`.

Build a table combining audit status and test results:

| Module                  | Audit Status | Test File | Tests | Pass | Fail |
|-------------------------|--------------|-----------|-------|------|------|
| db/client.py            | MISSING      | —         |  —    |  —   |  —   |
| fetchers/marketaux.py   | PARTIAL      | MISSING   |  —    |  —   |  —   |
| engine/combiner.py      | STUB         | EXISTS    |   5   |   2  |   3  |

Audit Status values: COMPLETE / PARTIAL / STUB / MISSING

---

## Step 5 — Determine Recommended Next Step

Use audit results and test failures together to determine ONE specific next step.

Priority order (highest first):

1. **Architectural violation** — fix before anything else. A violation means the
   system will behave incorrectly even after implementation. Example: hardcoded
   tickers in discovery.py means the discovery module will never work dynamically.

2. **Failing tests on a PARTIAL or COMPLETE module** — a test found a real bug in
   existing logic. Fix the module, then re-run tests.

3. **PARTIAL module that is next in implementation phase order** — incomplete
   implementations are worse than stubs because they create false confidence.
   Complete it before building the next module.

4. **STUB module that is next in phase order** — build the next unimplemented module.

5. **MISSING module that is next in phase order** — create the missing file.

6. **Module with no test coverage** — write tests using the test-writer skill.

Be specific. Name the file and what exactly needs to be done. Example:
"Complete `fetchers/discovery.py` — the dynamic news-driven discovery and market
mover scan are not implemented (hardcoded SP500_TICKERS list violates architecture).
This blocks the entire pipeline since all downstream modules receive their ticker
list from discovery."

---

## Step 6 — Format and Output the Report

```
═══════════════════════════════════════════════════════════════════
PAPER TRADER — SESSION STATUS REPORT
Generated: <current date and time>
═══════════════════════════════════════════════════════════════════

## LAST SESSION (git history, past 14 days)

<bullet list of commits grouped by area>

Uncommitted changes: <list files, or "none">

<If previous .claude/last-status.md exists and has recurring failures:>
⚠ Still failing from last session: <test names>

───────────────────────────────────────────────────────────────────

## ARCHITECTURE HEALTH

Incomplete implementations (PARTIAL):  X
Full stubs (no logic):                 X
Missing files:                         X
Architectural violations:              X
Deprecated (should be deleted):        X

### Incomplete — Needs Completion First
<For each PARTIAL file, list only the unchecked items from the audit:>
• fetchers/discovery.py (PARTIAL)
  - [ ] Dynamic news-driven discovery not implemented (SP500_TICKERS hardcoded)
  - [ ] Market movers scan not implemented
  - [ ] Sector rotation scan not implemented

### Stubs — Awaiting Implementation
<Group by phase:>
Phase 1 (Foundation): db/client.py, db/schema.sql
Phase 2 (Data Pipeline): fetchers/market.py, fetchers/aggregator.py
...

### Architectural Violations
<If any:>
• fetchers/discovery.py — hardcoded SP500_TICKERS list violates "never hardcode
  tickers outside discovery.py" rule. Dynamic yfinance fetching required.
<If none:>
  No violations detected.

### Deprecated Files Still Present
<If any:>
• engine/signals.py — superseded by engine/combiner.py and engine/strategies.py.
  Confirm no imports, then delete.

───────────────────────────────────────────────────────────────────

## TEST HEALTH

Total: X  |  Passed: X  |  Failed: X  |  Errors: X
(Skipped: @pytest.mark.expensive — Claude API tests)

| Module                  | Audit Status | Tests | Pass | Fail |
|-------------------------|--------------|-------|------|------|
| fetchers/marketaux.py   | PARTIAL      |   4   |   3  |   1  |
| engine/regime.py        | STUB         |   —   |   —  |   —  |
...

### Failing Tests
<For each failure:>
• test_<name> (tests/<path>): <one-sentence diagnosis>

<If none:> All tests passing.

───────────────────────────────────────────────────────────────────

## RECOMMENDED NEXT STEP

<One specific action. Name the file, what needs to be done, and why it's
the highest priority. Reference the audit violation or stub status.>

═══════════════════════════════════════════════════════════════════
```

---

## Step 7 — Write AUDIT.md and Save Report

After displaying the report:

1. Write the full audit output (from Step 1) to `AUDIT.md` in the project root,
   using the format defined in the audit skill. Overwrite any previous version.

2. Save the status report to `.claude/last-status.md`. Overwrite any previous version.
   This enables the next session to detect recurring failures.
