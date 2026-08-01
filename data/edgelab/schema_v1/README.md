# EdgeLab research schema — v1

Versioned, durable schema for MLB/Kalshi research: full market-universe
observation, decision tracking, bet ledger, CLV, and settlement — linked
by stable IDs so any bet, quote, or outcome can be traced back to raw
evidence.

This is Phase 1: **collection and linkage only.** No staking engine, no
Kelly sizing, no auto-betting. See `docs/EDGELAB_PHASE1.md` for the full
architecture writeup.

## Why a new schema instead of extending what's there

The repo already has several mature, narrower systems (`data/pipeline/<date>/*.json`
pipeline-stage artifacts, `bets.json` / `data/bets.json` bet ledgers,
`data/research/wagers.jsonl`). None of them is wrong to keep — EdgeLab
**ingests from them** rather than replacing them:

- Recommendations ingest `data/pipeline/<date>/recommendations.json` (the
  `marketLedger` rows) and `execution.json`, then extend coverage to
  markets the 11-market model config never evaluates at all (the other
  ~6 of the 17 strict-registry families), explicitly marked
  `NOT_EVALUATED` / `INSUFFICIENT_MODEL_SUPPORT` rather than silently
  dropped.
- Market observations ingest `data/kalshi_registry_snapshots/*.json` and
  `data/kalshi/discovery/<date>.json` — **no new Kalshi API calls**. Those
  snapshot files are already the raw, immutable evidence; EdgeLab adds a
  normalized, queryable layer plus a `provenance.sourceFile` pointer back
  to the exact raw file.
- Bets ingest `bets.json` and `data/bets.json` (reconciling their two
  different field-naming conventions into one canonical shape) and can
  also be logged directly against the new schema going forward.
- CLV reuses `scripts/clv_from_snapshot.py`'s formula and closing-quote
  selection ladder — not a competing implementation.
- Settlement links to `lib/f5_settlement.py` output for placed F5 bets and
  extends coverage to the full observed market universe (hypothetical
  returns for markets nobody bet).

## Stable identifiers

| ID | Built from | Notes |
|---|---|---|
| `gameId` | MLB Stats API `gamePk` when known, else `date_awayAbbr_homeAbbr` deterministic fallback | Never `"AWAY@HOME"` display strings — see `docs/CANONICAL_SCHEMAS.md`'s gap #2 |
| `marketTicker` | Kalshi's own ticker, verbatim | The one Kalshi identifier that is already always stable |
| `eventTicker` / `seriesTicker` | Kalshi's own, verbatim | |
| `marketObservationId` | sha1(`marketTicker` + `capturedAt`) | One row per (market, capture time) — reruns of the same snapshot file are naturally idempotent |
| `recommendationId` | sha1(`runId` + `marketTicker`) | One decision row per market per research run |
| `betId` | sha1(`gameId` + `marketTicker` + `entryTimestamp`) if derivable, else a ULID-style time-ordered token for pure manual entry | Deterministic where the inputs exist, so re-ingesting `bets.json` never duplicates a bet |
| `clvQuoteId` | sha1(`marketTicker` + `capturedAt`) | Shares the identity scheme with `marketObservationId` since a CLV quote *is* a market observation, tagged with `checkpoint`/`isClosingQuote` |
| `settlementId` | sha1(`gameId` + `marketTicker`) | One settlement row per market, ever |
| `runId` | `<runType>_<UTC timestamp>_<short random>` or the GitHub Actions `run_id` when running in CI | Identifies one research/ingestion run for audit trail |

See `lib/edgelab/ids.py` for the exact implementations.

## Every record carries

- `schemaVersion` (currently `"1"`)
- `createdAt` (ISO 8601 UTC)
- `updatedAt` where the record can be revised in place (recommendations,
  bets, settlements)
- `source` (which upstream system/file produced this record)
- `validationStatus` (`"valid"` / `"warning"` / `"invalid"`)
- `parserStatus` (`"parsed"` / `"partial"` / `"unparsed"`) where a record
  is derived from a parsed title/subtitle
- `provenance` (`{sourceSystem, sourceFile, capturedAt, ingestedAt}`) —
  every normalized record points back at the raw file it came from.
  Fields never available for a given record are `null`, never fabricated
  or defaulted to a placeholder value.

## Storage layout

```
data/edgelab/
  schema_v1/                 # JSON Schema definitions (this directory) + tags.json
  games/<date>.jsonl         # one row per game, appended once, updated in place is a new row w/ later updatedAt
  markets/<date>.jsonl       # one row per market (dimension), first-seen date
  observations/<date>.jsonl  # market_observation rows — time series, append-only
  bets/bets.jsonl            # single canonical placed-bet ledger, append-only
  recommendations/<date>.jsonl
  clv_quotes/<date>.jsonl    # time series, append-only
  settlements/<date>.jsonl   # one row per market, ever (idempotent overwrite-by-append + latest-wins read)
  research_runs/<date>.jsonl
  reports/<date>.md          # human-readable daily report
  reports/<date>.json        # machine-readable version of the same report
```

JSONL, partitioned by UTC calendar date, was chosen over one giant JSON
array per entity (see `docs/EDGELAB_PHASE1.md` §Storage for the full
tradeoff writeup): appends never require rewriting the whole file, dedup
only needs to scan one day's rows, and every format here (JSONL) loads
directly into SQLite/DuckDB/Parquet with a one-line `read_json_lines`
call in a later phase — no export step designed in now, but nothing here
blocks it either.

## Versioning policy

`schemaVersion` bumps only on a breaking change (field removed, type
changed, meaning changed). Additive fields do not require a bump — a
reader must already treat unknown fields as ignorable and missing
optional fields as `null`, not as an error. `lib/edgelab/schema.py`
documents the exact migration contract.
