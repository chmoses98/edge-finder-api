# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `STRONGER_CONFIRMATION`
- **Checkpoint:** `CHECKPOINT_4` (STRONGER_CONFIRMATION) — 6236 rows / 273 games / 12 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 54408
- joined rows: 6236 (excluded: 47845 without a pregame evaluation, 327 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29, 2026-08-30, 2026-08-31, 2026-09-01, 2026-09-02, 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07, 2026-09-08, 2026-09-09

## MLB-RSCH-0022

- **status:** `FORWARD_CONTRADICTS_FROZEN_FINDING`
  - worse on Brier (0.021127) and log loss (0.096063)
- **production − market:** Brier Δ 0.021127, log-loss Δ 0.096063, CI {'low': 0.0147, 'high': 0.0277, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `FORWARD_CONTRADICTS_FROZEN_FINDING`
  - worse on Brier (1e-06) and log loss (8e-06)
- **M2 (frozen α) − M0:** Brier Δ 1e-06, log-loss Δ 8e-06, CI {'low': -0.0, 'high': 0.0, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - mixed evidence: brierDelta=-3e-06 logLossDelta=-6e-06
  - datesFavourable=6/12
- **frozen β shrink − market:** Brier Δ -3e-06, log-loss Δ -6e-06, CI {'low': -0.0001, 'high': 0.0001, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
