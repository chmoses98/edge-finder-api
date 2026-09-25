# EdgeLab Frozen Forward Scorecard

Deterministic confirmation engine. **RESEARCH ONLY — no refitting, no production impact.**
Regenerate with `python3 scripts/edgelab/run_frozen_forward_scorer.py` (idempotent).

- **Forward window:** settlement date strictly after 2026-08-28
- **Status:** `STRONGER_CONFIRMATION`
- **Checkpoint:** `CHECKPOINT_4` (STRONGER_CONFIRMATION) — 13508 rows / 565 games / 27 dates

## Frozen artifacts under test (parameters read-only, never re-estimated)

| Experiment | Frozen parameter | Training end |
|---|---|---|
| MLB-RSCH-0024 | alpha = 0.0004 | 2026-08-24 |
| MLB-RSCH-0026 | beta = 0.9833, base = 0.430536 | 2026-08-24 |

## Scoring mechanics

- version: `V2_SYMMETRIC_CLAMP_2026_09_22`
- symmetric clamp on candidate AND reference: [0.01, 0.99]
- observation quote units: unit-aware (cents before 2026-09-10, dollars after)
- ladder settlement: Kalshi >= N rule; archived > N rows on game dates <= 2026-08-31 corrected by exact rung shift
- Scorecards produced under mechanics V1 (before 2026-09-22) read dollars-era quotes as cents and clamped only the candidate; their FORWARD_SUPPORTS verdicts were artifacts and are superseded.

## Coverage

- settled forward tickers: 115144
- joined rows: 13508 (excluded: 100353 without a pregame evaluation, 1283 without a pregame fair price)
- families: first_inning_run, game_result, game_total, inning_result, inning_total, pitcher_outs, pitcher_strikeouts, team_total, winning_margin
- dates: 2026-08-29, 2026-08-30, 2026-08-31, 2026-09-01, 2026-09-02, 2026-09-03, 2026-09-04, 2026-09-05, 2026-09-06, 2026-09-07, 2026-09-08, 2026-09-09, 2026-09-10, 2026-09-11, 2026-09-12, 2026-09-13, 2026-09-14, 2026-09-15, 2026-09-16, 2026-09-17, 2026-09-18, 2026-09-19, 2026-09-20, 2026-09-21, 2026-09-22, 2026-09-23, 2026-09-24

## MLB-RSCH-0022

- **status:** `FORWARD_CONTRADICTS_FROZEN_FINDING`
  - worse on Brier (0.016494) and log loss (0.063298)
- **production − market:** Brier Δ 0.016494, log-loss Δ 0.063298, CI {'low': 0.0126, 'high': 0.0204, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0024

- **status:** `INTERMEDIATE_UNCONFIRMED`
  - mixed evidence: brierDelta=-0.0 logLossDelta=-1e-06
  - datesFavourable=13/27
- **M2 (frozen α) − M0:** Brier Δ -0.0, log-loss Δ -1e-06, CI {'low': -0.0, 'high': 0.0, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## MLB-RSCH-0026

- **status:** `FORWARD_CONTRADICTS_FROZEN_FINDING`
  - worse on Brier (2.5e-05) and log loss (9.1e-05)
- **frozen β shrink − market:** Brier Δ 2.5e-05, log-loss Δ 9.1e-05, CI {'low': -0.0, 'high': 0.0001, 'method': 'GAME_CLUSTERED_BOOTSTRAP'}

## Governance

- `refitPerformed`: False
- `frozenArtifactsMutated`: False
- `productionChanged`: False
- `newSegmentsInvented`: False
- `statusVocabularyExcludesProductionApproved`: True
