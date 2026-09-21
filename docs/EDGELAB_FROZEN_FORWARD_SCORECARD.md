# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `STRONGER_CONFIRMATION`
- **Checkpoint:** `CHECKPOINT_4` (STRONGER_CONFIRMATION) — 11883 rows / 492 games / 23 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 100774
- joined rows: 11883 (excluded: 88307 without a pregame evaluation, 584 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29, 2026-08-30, 2026-08-31, 2026-09-01, 2026-09-02, 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07, 2026-09-08, 2026-09-09, 2026-09-10, 2026-09-11, 2026-09-12, 2026-09-13, 2026-09-14, 2026-09-15, 2026-09-16, 2026-09-17, 2026-09-18, 2026-09-19, 2026-09-20

## MLB-RSCH-0022

- **status:** `FORWARD_SUPPORTS_FROZEN_FINDING`
  - beats benchmark on Brier (-0.114106) and log loss (-0.814255)
  - direction holds on 12/23 forward dates
  - improvement spread across 9/9 scored families
- **production − market:** Brier Δ -0.114106, log-loss Δ -0.814255, CI {'low': -0.132, 'high': -0.0956, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `FORWARD_SUPPORTS_FROZEN_FINDING`
  - beats benchmark on Brier (-0.001762) and log loss (-0.136547)
  - direction holds on 16/23 forward dates
  - improvement spread across 9/9 scored families
- **M2 (frozen α) − M0:** Brier Δ -0.001762, log-loss Δ -0.136547, CI {'low': -0.002, 'high': -0.0015, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `FORWARD_SUPPORTS_FROZEN_FINDING`
  - beats benchmark on Brier (-0.002071) and log loss (-0.151476)
  - direction holds on 17/23 forward dates
  - improvement spread across 9/9 scored families
- **frozen β shrink − market:** Brier Δ -0.002071, log-loss Δ -0.151476, CI {'low': -0.0023, 'high': -0.0018, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
