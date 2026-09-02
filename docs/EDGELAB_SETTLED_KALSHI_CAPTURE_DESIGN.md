# Capturing Kalshi's own settlement result (design only)

**Status: DESIGN / RESEARCH ONLY. Nothing implemented in this session.**

## Why

This repository mis-graded **1,512** `KXMLBF5SPREAD` contracts (full-game
margin instead of F5) and every integer-rung total contract at its
boundary, and neither was detectable from inside the system — because
**Kalshi's own settlement result is never stored anywhere**. All 686,220
archived raw market records carry `status="active"`, since the capture
path only ever requests open markets. Our canonical settlement had nothing
to be checked against.

Objective: make exchange truth a first-class, immutable input so a grading
defect surfaces in days, not weeks.

## 1. Source

Kalshi's public REST market endpoints, the same host the repo already
reads (`api/kalshisearch.js`, `scripts/build_kalshi_registry.py`):

- `GET /trade-api/v2/markets?series_ticker=<SERIES>&status=settled` —
  bulk sweep per MLB series, the natural mirror of today's open-market
  sweep.
- `GET /trade-api/v2/markets/{ticker}` — single-market fallback for a
  ticker the bulk sweep misses.

Both are public/unauthenticated, like the current fetch. Settlement fields
of interest: `result` (`yes`/`no`, and any void/cancel marker),
`status` (`settled`/`finalized`/`closed`), `settlement_value` /
`settled_time`, `close_time`, plus `open_interest`/`volume` at close.

No new credential is required. If a future Kalshi version needs auth for
settled markets, that is a blocker to report, not to work around.

## 2. Timing

One sweep per **series per game-date**, run late enough that every game
has resolved: **08:00 UTC on D+1** (~04:00 ET), after even a West Coast
extra-innings game. A single retry at **14:00 UTC on D+1** picks up
suspended/delayed games. A weekly backfill sweep re-requests any date
still holding unresolved tickers, bounded to the last 14 days.

This mirrors the cadence `edgelab-postgame.yml` already uses, and is a
handful of requests per day.

## 3. Market identity key

The **exact Kalshi `ticker` string**, verbatim, as the primary key — it is
what both sides already use, so no fuzzy or derived matching is needed or
permitted. `event_ticker` is stored alongside for grouping. A ticker that
does not parse into our registry is retained raw and flagged
`identityUnresolved`, never dropped and never guessed.

## 4. Immutable raw capture path

```
data/kalshi_settled_snapshots/<YYYY-MM-DD>/settled_<SERIES>_<HHMM>Z.json
```

Write-once, append-only, never edited in place — the same convention as
`data/kalshi_registry_snapshots/`. Each file stores the unmodified
response body plus a provenance envelope (`capturedAt`, `endpoint`,
`httpStatus`, `commitSha`, `githubRunId`). A re-run writes a **new**
file; it never overwrites an earlier one, so a changed exchange answer is
itself evidence rather than a silent update.

A normalized projection then lands in
`data/edgelab/exchange_settlements/<date>.jsonl.gz` with one row per
ticker: `marketTicker`, `eventTicker`, `exchangeResult`,
`exchangeStatus`, `settledAt`, `provenance`.

## 5. Cross-check against canonical settlement

A read-only comparator (`scripts/edgelab/compare_exchange_settlement.py`)
joins `data/edgelab/settlements/<date>` to
`data/edgelab/exchange_settlements/<date>` on `marketTicker` and emits per
date and per family:

| Class | Meaning |
|---|---|
| `AGREE` | canonical result == exchange result |
| `MISMATCH` | both settled, results differ — **the alarm case** |
| `CANONICAL_MISSING` | exchange settled, we have no result |
| `EXCHANGE_MISSING` | we settled, exchange row absent |
| `VOID_DISAGREEMENT` | one side void/cancelled, the other graded |

Both defects this repo actually had would have been caught on day one:
the F5-spread defect as a large, family-wide `MISMATCH` block, and the
totals defect as `MISMATCH` concentrated on rungs where `total == N`.

## 6. Mismatch behaviour — alert, never auto-overwrite

**No automatic overwrite of canonical settlement or of any ledger row.**
The comparator is read-only by construction.

- Any `MISMATCH` fails the daily health check and is written to
  `data/edgelab/reports/exchange_settlement_mismatches_<date>.json`.
- A family-level mismatch rate above a small threshold (proposed: **>1%**
  of that family's settled contracts on a date, or **any 3 consecutive
  dates** with a mismatch in the same family) escalates to a
  **settlement-integrity incident**: that family is flagged for review and
  its research use quarantined until root-caused.
- Re-grading is a separate, explicitly authorized operational action with
  its own PR — exactly as PRs #175 and #176 were handled. The exchange is
  treated as strong evidence, not as an authority that may silently
  rewrite history.

## 7. What this deliberately does not do

- Does not place, cancel, or read orders — read-only market data only.
- Does not touch `bets.jsonl` or any staking, risk-gate or eligibility path.
- Does not retro-fill past dates on first run beyond the bounded 14-day
  backfill; older reconciliation is a separate authorized job.
