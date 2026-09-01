# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `INTERMEDIATE`
- **Checkpoint:** `CHECKPOINT_2` (INTERMEDIATE) — 913 rows / 47 games / 3 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Coverage

- settled forward tickers: 12870
- joined rows: 913 (excluded: 11873 without a pregame evaluation, 84 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29, 2026-08-30, 2026-08-31

## MLB-RSCH-0022

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - CHECKPOINT_2 reached; confirmation requires CHECKPOINT_3
  - directional read: brierDelta=0.02501 logLossDelta=0.116015
- **production − market:** Brier Δ 0.02501, log-loss Δ 0.116015, CI {'low': 0.0051, 'high': 0.0439, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - CHECKPOINT_2 reached; confirmation requires CHECKPOINT_3
  - directional read: brierDelta=4e-06 logLossDelta=1.1e-05
- **M2 (frozen α) − M0:** Brier Δ 4e-06, log-loss Δ 1.1e-05, CI {'low': -0.0, 'high': 0.0, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - CHECKPOINT_2 reached; confirmation requires CHECKPOINT_3
  - directional read: brierDelta=-0.000107 logLossDelta=-0.000536
- **frozen β shrink − market:** Brier Δ -0.000107, log-loss Δ -0.000536, CI {'low': -0.0003, 'high': 0.0001, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
