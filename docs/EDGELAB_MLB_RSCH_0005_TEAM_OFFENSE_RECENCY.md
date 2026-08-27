# EdgeLab Research Lab — MLB-RSCH-0005: Multi-Season Team Offense Recency/Form Backtest

**Status: RESEARCH ONLY. No production model probability, feature, recommendation
logic, threshold, confidence tier, Bet Up To logic, Kalshi fee calculation,
bankroll/staking, market eligibility, lineup gate, slate output, risk gate,
settlement, or production cron behavior was changed.**

## 1. Question

Over multiple MLB seasons and tens of thousands of team-games: does a
team's recent offensive performance, relative to its own point-in-time
season-to-date baseline, predict scoring in the NEXT game — after
accounting for PIT-safe context (season baseline, opponent's own runs-
allowed baseline, home/away) — or is recent "hot/cold" form mostly noise
that regresses toward the team's true baseline?

This is a **baseball-level historical study**, not a Kalshi profitability
study — no market data is used or required.

## 2. Execution status

**Complete, no new fetch required.** Every schedule payload already
committed under `data/research_cache/bullpen_backtest/<season>/schedules/`
(by MLB-RSCH-0003) carries both teams' final scores per game.
`lib.edgelab.backtest.bullpen_backtest_reconstruction.
extract_team_games_from_schedule` was additively extended with
`runsScored`/`runsAllowed`/`opponentTeamId` (backward-compatible — that
module's existing 30-test suite still passes unchanged), and the entire
experiment ran **locally, against the already-committed cache**, with no
GitHub Actions dispatch and no network access needed at all.

## 3. Preregistered specification

### 3a. Feature families

All PIT-safe, reconstructed via `lib.edgelab.backtest.
team_offense_recency_reconstruction.reconstruct_offense_features`, which
filters a team's own game list via `is_strictly_before()` (imported
unchanged from the MLB-RSCH-0003 module) — excludes the target game
itself, every future game, and any same-date later game unless
`gameNumber` ordering is actually known.

**Season baseline**: `seasonToDateRunsPerGame` — mean runs scored across
all strictly-prior completed games this season. **Opponent baseline**:
`opponentSeasonToDateRunsAllowedPerGame` — the opponent's own mean runs
allowed across its own strictly-prior games this season. **Recent-form
windows** (fixed, preregistered, never optimized post hoc): `recentFormRate_5/10/20`
— mean runs scored over the exact most recent 5/10/20 prior games.
**Key variable**: `recentFormDeviation_{window}` = recent rate minus
season baseline. A team's first 20 completed games of a season are
excluded outright (`MIN_PRIOR_GAMES_FOR_BASELINE`), not approximated —
this also guarantees every window is fillable whenever a row is eligible
at all.

**Not implemented** (component measures): hits, walks, strikeouts,
extra-base hits — the reused schedule cache carries only final scores,
not team batting lines; adding those would require a new team-batting
boxscore fetch, out of scope per the mission's explicit "do not let this
become a giant new feature-building project" instruction.

### 3b. Outcome

Primary: **team runs scored in the next game** (`runsScored`). Also
recorded: `scored3Plus`/`scored4Plus`/`scored5Plus`, `shutout`. No
market outcomes used.

### 3c. Hypotheses — preregistered, not changed after results

H1/H2/H3 (5/10/20-game recent-form deviation has positive persistence
into next-game scoring), H4 (recency adds predictive information beyond
season + opponent baseline — a frozen control-vs-candidate regression
comparison), H5 (extreme hot/cold deviations show stronger persistence
than ordinary variation).

### 3d. Chronological split

Development = 2022–2024, validation = 2025, holdout = 2026 (locked). The
same fixed `run_hypothesis_tests` function is applied unchanged to all
three groups (`TestHoldoutIsolation`). H4's regression coefficients and
H5's extreme-group percentile cutoffs are fit **once** on development
rows and reused, object-identity-unchanged, on validation and holdout —
proven by `TestFrozenCandidateUnchanged` and by `ols_fit`/`percentile`
each appearing exactly the required number of times in `main()`.

### 3e. Evidence level

`E2_PIT_HISTORICAL` — the same evidence level MLB-RSCH-0003/0004 used for
their own reconstructed-from-dated-raw feature pathways.

## 4. Coverage (real data)

| Season | Games | Team-games | Teams |
|---|---|---|---|
| 2022 | 2,137 | 4,262 | 30 |
| 2023 | 2,135 | 4,274 | 30 |
| 2024 | 2,139 | 4,264 | 30 |
| 2025 | 2,137 | 4,268 | 30 |
| 2026 (partial, through latest completed date) | 1,710 | 3,410 | 30 |
| **Total** | **10,258** | **20,478** | — |

**20,478 / 10,000 minimum expected team-games — well above target**
(tens of thousands, as targeted). Development (2022–2024) = 12,800
team-games / 6,411 games; validation (2025) = 4,268 / 2,137; locked
holdout (2026) = 3,410 / 1,710. Excluded: each team's first 20 completed
games of each season (~600 team-games per season, 30 teams × 20).

## 5. Results

All CIs are 95%, **team-clustered** bootstrap
(`cluster_key="team"` — the dominant repeated-measure structure here is
the same team appearing in ~140+ eligible games/season, sharing park/
roster/quality effects; same-game two-team dependence is not separately
clustered, a documented limitation, §8). A positive Spearman r means a
recently-hotter-than-baseline team scores *more* in its next game — the
direction H1/H2/H3 predict.

### 5a. Development (2022–2024, n=12,800 / 6,411 games) — PRIMARY

| Check | Spearman r | 95% CI |
|---|---|---|
| H1 — 5-game form deviation | +0.0025 | [-0.0177, 0.0220] |
| H2 — 10-game form deviation | +0.0113 | [-0.0142, 0.0336] |
| H3 — 20-game form deviation | +0.0130 | [-0.0118, 0.0376] |

**Every CI crosses zero.** None of the three windows show a
statistically confident relationship between recent-form deviation and
next-game scoring in development.

### 5b. Validation (2025, n=4,268 / 2,137 games)

| Check | Spearman r | 95% CI |
|---|---|---|
| H1 — 5-game | +0.0276 | [-0.0024, 0.0587] |
| H2 — 10-game | -0.0002 | [-0.0286, 0.0254] |
| H3 — 20-game | -0.0302 | [-0.0635, 0.0047] |

Still no CI excludes zero (H1's low bound is closest, at -0.0024).

### 5c. Locked holdout (2026, n=3,410 / 1,710 games) — evaluated once, untouched during development

| Check | Spearman r | 95% CI |
|---|---|---|
| H1 — 5-game | +0.0067 | [-0.0207, 0.0333] |
| H2 — 10-game | +0.0305 | [-0.0002, 0.0622] |
| H3 — 20-game | +0.0070 | [-0.0289, 0.0401] |

No CI excludes zero (H2's low bound is closest, at -0.0002).
**No confident finding in any period, for any window.**

### 5d. H4 — Control vs. recency-candidate: frozen predictive comparison

CONTROL = season baseline + opponent baseline + home/away. CANDIDATE =
CONTROL + all three recent-form deviations. Both fit **once** on
development, frozen, applied unchanged to validation/holdout.

| Split | Model | MAE | RMSE | Mean Poisson deviance |
|---|---|---|---|---|
| Development (n=12,800) | Control | 2.4325 | 3.1070 | 2.2479 |
| Development | Candidate | 2.4317 | 3.1062 | 2.2471 |
| Validation (n=4,268) | Control | 2.5011 | 3.2190 | 2.3642 |
| Validation | Candidate | 2.5043 | 3.2220 | 2.3688 |
| Holdout (n=3,410) | Control | 2.5169 | 3.2252 | 2.3486 |
| Holdout | Candidate | 2.5155 | 3.2243 | 2.3472 |

**The candidate's improvement over control is negligible in development
(MAE improves by 0.0008 runs, ~0.03%) and inconsistent out-of-sample**
(candidate is *slightly worse* in validation, *slightly better* in
holdout, both differences under 0.002 MAE). Recency adds no meaningful
predictive information beyond the season+opponent baseline. Candidate
coefficients (development fit): `recentFormDeviation_5` = **-0.054**,
`recentFormDeviation_10` = **+0.073**, `recentFormDeviation_20` =
**+0.051** — small and inconsistently signed across windows, consistent
with noise rather than a real effect.

### 5e. H5 — Extreme hot/cold groups: persistence vs. regression

Cutoffs (10th/90th percentile of development's own `recentFormDeviation_10`
distribution: cold ≤ **-1.12**, hot ≥ **+1.20**) computed once on
development, frozen, applied unchanged to validation/holdout.
`persistenceFraction` = (actual next-game runs − season baseline) ÷
(recent hot/cold rate − season baseline): 0 = full regression to
baseline by the next game, 1 = the hot/cold rate persisted unchanged.

| Split | Group | n | Recent rate | Baseline | Next-game runs | Persistence |
|---|---|---|---|---|---|---|
| Development | Extreme hot | 1,282 | 6.27 | 4.54 | 4.60 | **0.029** |
| Development | Extreme cold | 1,280 | 2.98 | 4.51 | 4.33 | **0.117** |
| Validation | Extreme hot | 570 | 6.26 | 4.45 | 4.50 | **0.028** |
| Validation | Extreme cold | 441 | 3.02 | 4.57 | 4.54 | **0.019** |
| Holdout | Extreme hot | 346 | 6.30 | 4.55 | 4.54 | **-0.005** |
| Holdout | Extreme cold | 370 | 3.02 | 4.51 | 4.41 | **0.068** |

**Extreme hot/cold teams regress 88–100% of the way back to their own
season baseline by the very next game, in every period, both
directions.** A team on a recent tear averaging 6.3 runs/game (vs. a
~4.5 season baseline) scores at essentially its baseline rate (4.5–4.6)
the next time out — not anywhere near its hot streak's rate. The same
holds, slightly less completely, for cold teams. This is a strong,
consistent, out-of-sample-replicated descriptive finding.

## 6. Conclusions

- **A. Is recent offensive form predictive beyond season baseline?**
  **No.** All nine H1/H2/H3 correlation CIs (three windows × three
  periods) cross zero. H4's frozen control-vs-candidate comparison shows
  the candidate provides no meaningful, consistent predictive
  improvement over the season+opponent baseline alone. H5's extreme-group
  analysis directly shows why: whatever "hot" or "cold" streak a team is
  on regresses 88–100% of the way back to baseline by the very next game.
- **B. Which window, if any, replicates in 2025?** None. H1's validation
  CI comes closest to excluding zero (low = -0.0024) but does not.
- **C. Which survives untouched 2026?** None. H2's holdout CI comes
  closest (low = -0.0002) but does not.
- **D. Do extreme hot/cold offenses persist or regress?** **They regress
  — strongly, consistently, in both directions, in all three periods**
  (persistence fractions of -0.005 to 0.117, i.e. 0–12% of the hot/cold
  gap survives to the next game).
- **E. Is future production-model research on offense recency
  justified?** **No**, on this evidence. The honest, complete answer per
  the mission's explicitly acceptable null: **recent offensive form is
  mostly noise / mean reversion**, not a repeatable predictive signal,
  across a large, multi-season, out-of-sample-validated study.

**Final signal classification: NO_USEFUL_SIGNAL.** (Not
`MEAN_REVERSION_SIGNAL` in the strict classifier sense — that label
requires a *confidently negative* correlation, and these correlations are
statistically indistinguishable from zero rather than confidently
negative. The extreme-group persistence-fraction analysis in §5e is the
descriptive evidence that what mechanism does exist is overwhelmingly
regression to the mean, complementing rather than contradicting the
primary classification.)

## 7. Market relevance (secondary, after the baseball result)

Per the mission's "after baseball result" instruction: because no
repeatable recency signal was found, there is no basis for using recent
team offensive form (in the form tested here) as a feature for team
totals, game totals, moneyline/run line, or F3/F5 markets. No further
market-horizon analysis or Kalshi optimization was performed.

## 8. Limitations

- Same-game (two-team) dependence is not separately clustered — only
  repeated team observations are (`cluster_key="team"`). A more complete
  design might jointly cluster by both team and game; not built here.
- Component measures (hits, walks, strikeouts, extra-base hits) not
  implemented — would require a new team-batting boxscore fetch, out of
  scope per the mission's efficiency instruction. The primary analysis
  (runs scored) required no new fetch at all.
- Starting-pitcher identity/quality was not included as a secondary
  robustness control, per the same efficiency instruction — a candidate
  for a future, separately registered experiment if ever warranted (which
  this result does not support).
- H4's frozen regression is a simple closed-form OLS (no numpy — a
  pure-Python normal-equations solver), with no regularization,
  interaction terms, or nonlinear terms — a deliberately simple, non-tuned
  baseline-vs-candidate comparison, not a production-grade scoring model.
- First-five-innings / inning-level normalization not implemented — the
  reused schedule cache carries only final game scores.
- 2026 is a partial season (through the latest completed date at run
  time) — the locked holdout's own sample will grow if this experiment's
  analysis is ever rerun later in the season, but per this study's own
  rule the 2026 holdout must not be re-inspected or re-tuned against as
  it grows.
