# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `INSUFFICIENT_FORWARD_DATA`
- **Checkpoint:** `CHECKPOINT_0` (HEALTH_ONLY) — 0 rows / 0 games / 0 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 0
- joined rows: 0 (excluded: 0 without a pregame evaluation, 0 without a pregame fair price)
- families: (none yet)
- dates: (none yet)

## Status reasons

- no settled rows after 2026-08-28 yet -- the FORWARD window has not begun accumulating
- health only: nothing is interpreted, no frozen parameter is touched

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
