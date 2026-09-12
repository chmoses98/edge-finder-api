# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `STRONGER_CONFIRMATION`
- **Checkpoint:** `CHECKPOINT_4` (STRONGER_CONFIRMATION) — 7238 rows / 310 games / 14 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 60736
- joined rows: 7238 (excluded: 53153 without a pregame evaluation, 345 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29, 2026-08-30, 2026-08-31, 2026-09-01, 2026-09-02, 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07, 2026-09-08, 2026-09-09, 2026-09-10, 2026-09-11

## MLB-RSCH-0022

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - mixed evidence: brierDelta=-0.018897 logLossDelta=-0.166765
  - datesFavourable=3/14
- **production − market:** Brier Δ -0.018897, log-loss Δ -0.166765, CI {'low': -0.0359, 'high': -0.0027, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - mixed evidence: brierDelta=-0.000484 logLossDelta=-0.037207
  - datesFavourable=7/14
- **M2 (frozen α) − M0:** Brier Δ -0.000484, log-loss Δ -0.037207, CI {'low': -0.0007, 'high': -0.0003, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `FORWARD_SUPPORTS_FROZEN_FINDING`
  - beats benchmark on Brier (-0.000573) and log loss (-0.04139)
  - direction holds on 8/14 forward dates
  - improvement spread across 9/9 scored families
- **frozen β shrink − market:** Brier Δ -0.000573, log-loss Δ -0.04139, CI {'low': -0.0008, 'high': -0.0004, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
