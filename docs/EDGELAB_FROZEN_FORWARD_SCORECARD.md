# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `INSUFFICIENT_FORWARD_DATA`
- **Checkpoint:** `CHECKPOINT_0` (HEALTH_ONLY) — 189 rows / 11 games / 1 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 3278
- joined rows: 189 (excluded: 3061 without a pregame evaluation, 28 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29

## MLB-RSCH-0022

- **status:** `INSUFFICIENT_FORWARD_DATA`
  - below CHECKPOINT_1 -- health only, no interpretation
- **production − market:** Brier Δ -0.023118, log-loss Δ -0.017538, CI {'low': -0.0427, 'high': 0.0201, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `INSUFFICIENT_FORWARD_DATA`
  - below CHECKPOINT_1 -- health only, no interpretation
- **M2 (frozen α) − M0:** Brier Δ -1.8e-05, log-loss Δ -4e-05, CI {'low': -0.0, 'high': 0.0, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `INSUFFICIENT_FORWARD_DATA`
  - below CHECKPOINT_1 -- health only, no interpretation
- **frozen β shrink − market:** Brier Δ 0.000264, log-loss Δ 0.000463, CI {'low': 0.0001, 'high': 0.0004, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
