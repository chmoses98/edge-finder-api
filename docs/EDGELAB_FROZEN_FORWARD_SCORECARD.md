# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `STRONGER_CONFIRMATION`
- **Checkpoint:** `CHECKPOINT_4` (STRONGER_CONFIRMATION) — 5833 rows / 248 games / 11 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 49328
- joined rows: 5833 (excluded: 43183 without a pregame evaluation, 312 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29, 2026-08-30, 2026-08-31, 2026-09-01, 2026-09-02, 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07, 2026-09-08

## MLB-RSCH-0022

- **status:** `FORWARD_CONTRADICTS_FROZEN_FINDING`
  - worse on Brier (0.021552) and log loss (0.098748)
- **production − market:** Brier Δ 0.021552, log-loss Δ 0.098748, CI {'low': 0.0145, 'high': 0.0276, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `FORWARD_CONTRADICTS_FROZEN_FINDING`
  - worse on Brier (1e-06) and log loss (5e-06)
- **M2 (frozen α) − M0:** Brier Δ 1e-06, log-loss Δ 5e-06, CI {'low': -0.0, 'high': 0.0, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - mixed evidence: brierDelta=4e-06 logLossDelta=-3e-06
  - datesFavourable=5/11
- **frozen β shrink − market:** Brier Δ 4e-06, log-loss Δ -3e-06, CI {'low': -0.0001, 'high': 0.0001, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
