# EdgeLab Research Lab — MLB-RSCH-0003: Multi-Season Bullpen Workload Backtest

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed.**

## 1. Question

Over multiple MLB seasons and thousands of team-games: does recent bullpen
workload predict subsequent bullpen run prevention, and is the CURRENT
production workload adjustment (`lib.edgelab.bullpen_availability.
compute_bullpen_workload_adjustment`, unchanged, imported not reimplemented)
directionally and magnitude-wise reasonable?

This is a **baseball-level historical study**, not a Kalshi profitability
study (spec section 11) — no market data is used or required.

## 2. Execution status (read this first)

**The deterministic reconstruction/statistics pipeline is built and fully
tested. The actual multi-season data pull has not yet run in this session.**

This repository's own CI/local research environment has **no outbound network
access to `statsapi.mlb.com`** (confirmed: a direct request returns HTTP 403
from the environment's proxy). Per this milestone's own instruction, rather
than silently reducing scope to whatever tiny local corpus might exist, this
PR instead:

- implements the full deterministic feature/outcome reconstruction and
  statistics code (network-free, fully unit- and integration-tested against
  synthetic fixtures — 62 tests, all passing),
- adds `.github/workflows/research-multiseason-bullpen-backtest.yml`, a
  **manual-dispatch-only** GitHub Actions workflow that runs the network
  fetch (real outbound access, GitHub-hosted runner) followed by the
  backtest, and commits the resulting cache + results to this same research
  branch,
- registers experiment `MLB-RSCH-0003` with a preregistered specification
  (hypotheses, features, outcomes, dev/validation/holdout split) that does
  **not** change once real data is pulled,
- runs the backtest against the current (empty) cache and honestly reports
  the coverage shortfall rather than fabricating a result (see §4).

**Dispatching this workflow and retrieving its results is a follow-up step**
— see the PR description for whether it was dispatched this session and its
status.

## 3. Preregistered specification

### 3a. Feature families (spec section 3)

All PIT-safe, reconstructed via `lib.edgelab.backtest.
bullpen_backtest_reconstruction.reconstruct_workload_features`, which
filters a team's own game list via `is_strictly_before()` — proven (by that
module's own test suite) to exclude the target game itself, every future
game, and any same-date later game unless `gameNumber` ordering is actually
known (doubleheader-safe).

**Primary specification (reused production formula, not reimplemented):**
`productionFormulaInput` — a team's prior games windowed exactly as
`lib.edgelab.bullpen_usage.summarize_team_bullpen_usage` already does for
production, fed unchanged into `compute_bullpen_workload_adjustment`.

**Exploratory calendar-day features** (spec section 3's explicit list, all
implemented): bullpen pitches previous 1/2/3 calendar days, relievers used
previous 1/2 days, back-to-back reliever count, 3-consecutive-day reliever
count (`None` when the team didn't play all 3 prior days), heavy-usage
reliever count (reuses production's own `HEAVY_USE_PITCH_THRESHOLD=35`),
high-leverage-reliever usage previous day / back-to-back, days since last
game, doubleheader context (`doubleHeader`/`gameNumber`, sourced from the
schedule, known pregame).

### 3b. Outcome (spec section 4)

Primary: **relief runs allowed after the starter exits, per team-game**
(`reliefRunsAllowed`, from `relief_outcome_for_game`). Also recorded: relief
earned runs allowed, bullpen innings pitched, number of relievers used,
full-game team runs allowed. A complete-game shutout (0 relievers) is a real,
well-defined zero; any relief appearance with missing runs/earnedRuns/outs
data invalidates the whole game's outcome (never a partial sum).

**Not implemented:** late-inning (6th-9th) opponent runs — would require the
heavier live-feed endpoint per game, doubling network cost for a
secondary/exploratory outcome; the primary outcome above is used instead.

### 3c. Hypotheses (spec section 5) — preregistered, not changed after results

H1 (workload → worse prevention), H2 (back-to-back → worse prevention), H3
(high-leverage workload > generic workload effect), H4 (nonlinear/extreme
workload), H5 (current multiplier directionally aligned with observed risk).

### 3d. Chronological split (spec section 7)

Development = 2022–2024, validation = 2025, holdout = 2026 (locked). The
same fixed `run_hypothesis_tests` function is applied unchanged to all three
groups — proven structurally (its own source references no season-group
constant; see `tests/edgelab/test_run_multiseason_bullpen_backtest_experiment_script.py::TestHoldoutIsolation`).

### 3e. Evidence level

`E2_PIT_HISTORICAL` — the feature reconstruction pathway is proven
point-in-time-safe by tests, matching `lib/edgelab/pit_provenance.py`'s
existing `team_recent_game_log_reconstruction` manifest entry
(`RECONSTRUCTABLE_FROM_DATED_RAW`).

## 4. Coverage (current state — before real data pull)

| Season | Team-games | Games |
|---|---|---|
| 2022 | 0 | 0 |
| 2023 | 0 | 0 |
| 2024 | 0 | 0 |
| 2025 | 0 | 0 |
| 2026 | 0 | 0 |

Total: **0 / 3,000 minimum expected team-games.** Reason: no network access
in this session (see §2) — `data/research_cache/bullpen_backtest/` is
currently empty. This is reported explicitly per spec section 10's
instruction, rather than silently proceeding as if this were a validated
large-sample result.

## 5. Results

**Not available yet** — see §2/§4. Once the manual workflow is dispatched and
completes, `data/edgelab/experiment_reports/MLB-RSCH-0003/*.json` and
`data/edgelab/analytics/latest_mlb_rsch_0003_multiseason_bullpen_backtest.json`
will carry: development-set H1–H5 results (correlation/mean-difference with
95% game-clustered-bootstrap CI, decile tables), the same fixed
specification applied to validation and holdout, the current-multiplier
distribution/bucket table, and the four conclusion fields (A–D) below.

## 6. Conclusions (framework — filled in once real data is pulled)

- **A. Does bullpen fatigue exist as a repeatable baseball signal?** —
  `UNPROVEN` (no data yet).
- **B. Are the specific components used by the current model supported?** —
  `UNPROVEN` (no data yet).
- **C. Is the current 1.00–1.12 adjustment directionally reasonable?** —
  `UNPROVEN` (no data yet).
- **D. Is its magnitude TOO WEAK / PLAUSIBLE / TOO STRONG / UNPROVEN?** —
  `UNPROVEN` (no data yet; `classify_magnitude()` never proposes a new
  coefficient — per spec, this is not permission to tune the multiplier).

## 7. Limitations

- Real multi-season data has not been pulled in this session (see §2) —
  every number in this document, if any, is a placeholder pending the
  dispatched workflow's real run, not a validated large-sample result.
- Late-inning opponent runs not implemented (see §3b).
- H3's high-leverage-vs-generic comparison reports both effect sizes side by
  side; it is not a formal statistical interaction test.
- H4's nonlinearity check is descriptive (decile table), not a formal
  nonlinearity hypothesis test.
- `data/research_cache/bullpen_backtest/` stores a **compact extraction**
  (per-pitcher lines only, via `extract_pitcher_lines`), not raw MLB API
  payloads — keeps the committed cache small, per spec section 13's "do not
  create enormous noisy commits" instruction, at the cost of needing a
  versioned, tested extraction schema (documented in
  `lib/edgelab/backtest/bullpen_backtest_reconstruction.py`) rather than the
  raw wire format.
