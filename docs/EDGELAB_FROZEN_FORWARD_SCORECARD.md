# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `STRONGER_CONFIRMATION`
- **Checkpoint:** `CHECKPOINT_4` (STRONGER_CONFIRMATION) — 11088 rows / 458 games / 21 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 90837
- joined rows: 11088 (excluded: 79253 without a pregame evaluation, 496 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29, 2026-08-30, 2026-08-31, 2026-09-01, 2026-09-02, 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07, 2026-09-08, 2026-09-09, 2026-09-10, 2026-09-11, 2026-09-12, 2026-09-13, 2026-09-14, 2026-09-15, 2026-09-16, 2026-09-17, 2026-09-18

## MLB-RSCH-0022

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - mixed evidence: brierDelta=-0.10379 logLossDelta=-0.744584
  - datesFavourable=10/21
- **production − market:** Brier Δ -0.10379, log-loss Δ -0.744584, CI {'low': -0.1234, 'high': -0.0849, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `FORWARD_SUPPORTS_FROZEN_FINDING`
  - beats benchmark on Brier (-0.001625) and log loss (-0.125733)
  - direction holds on 14/21 forward dates
  - improvement spread across 9/9 scored families
- **M2 (frozen α) − M0:** Brier Δ -0.001625, log-loss Δ -0.125733, CI {'low': -0.0019, 'high': -0.0014, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `FORWARD_SUPPORTS_FROZEN_FINDING`
  - beats benchmark on Brier (-0.00191) and log loss (-0.139505)
  - direction holds on 15/21 forward dates
  - improvement spread across 9/9 scored families
- **frozen β shrink − market:** Brier Δ -0.00191, log-loss Δ -0.139505, CI {'low': -0.0022, 'high': -0.0016, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
