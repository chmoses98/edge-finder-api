# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `INSUFFICIENT_FORWARD_DATA`
- **Checkpoint:** `CHECKPOINT_0` (HEALTH_ONLY) — 265 rows / 15 games / 2 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 7646
- joined rows: 265 (excluded: 7317 without a pregame evaluation, 64 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29, 2026-08-30

## MLB-RSCH-0022

- **status:** `INSUFFICIENT_FORWARD_DATA`
  - below CHECKPOINT_1 -- health only, no interpretation
- **production − market:** Brier Δ -0.003733, log-loss Δ 0.020648, CI {'low': -0.0341, 'high': 0.0325, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `INSUFFICIENT_FORWARD_DATA`
  - below CHECKPOINT_1 -- health only, no interpretation
- **M2 (frozen α) − M0:** Brier Δ -1e-05, log-loss Δ -2.3e-05, CI {'low': -0.0, 'high': 0.0, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `INSUFFICIENT_FORWARD_DATA`
  - below CHECKPOINT_1 -- health only, no interpretation
- **frozen β shrink − market:** Brier Δ 0.000308, log-loss Δ 0.000772, CI {'low': 0.0002, 'high': 0.0005, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
