# Evaluation scorecards: projection accuracy and betting value

Two separate, read-only evaluation modules that ship beside `pure_gate.py`. They answer different
questions, take different inputs and are promoted through different gates.

| File | Question | Inputs |
|---|---|---|
| `accuracy_scorecard.py` | Is this forecast distribution more accurate and better calibrated than the alternatives? | projection rows (`pure_forecast.v1`, or `projection_only` for non-PURE comparators), sports outcomes, optionally the market family (only to define the listed cohort) |
| `value_scorecard.py` | Would buying at the executable Kalshi price at as_of, after fees and slippage, have made money? | calibrated probabilities per rung at as_of, executable quotes at or before as_of, sports outcomes |

Both are standard library only, Python 3.10+, the same dependency policy as `pure_gate.py`. There is no numpy
path, so results don't depend on what's installed. Neither module places orders, reads a live venue, or
touches wagering, staking, authority or routing code. Every output is descriptive. Neither promotes
anything.

## Two promotions, kept apart

* **Accuracy promotion** (track A, independent sports-only projections, and track B, best-accuracy
  projections) is decided on `accuracy_scorecard` head-to-heads against the production champion and the
  simple baseline, on a preregistered, untouched chronological holdout. A better CRPS or MAE says nothing
  about money.
* **Betting-value promotion** (track C) is decided on the `value_scorecard` verdict against preregistered
  minimums. Good calibration is necessary for value, but it isn't evidence of it. Value has to show up as
  ROI after fees at prices that could actually be executed.
* A projection that uses market information (track B with market inputs, or a market-informed benchmark)
  can be scored for accuracy. Its disagreement with the market it learned from is never independent
  evidence of value. `value_scorecard` returns `NOT_INDEPENDENT_EVIDENCE` for such a source whatever the
  numbers say.
* The market-informed benchmark in `accuracy_scorecard` is a **price-of-independence diagnostic**. A PURE
  challenger scoring worse than it is expected and isn't a defect. A PURE challenger scoring better isn't a
  betting signal.

---

## `accuracy_scorecard.py`

```bash
cd pure-contract
python3 -I accuracy_scorecard.py score \
    --challenger chal.jsonl --outcomes outcomes.jsonl \
    [--champion champ.jsonl] [--baseline base.jsonl] [--independent indep.jsonl] \
    [--market-benchmark v5.jsonl] [--comparator NAME[:ROLE]=PATH ...] [--projection-only ARM ...] \
    [--market listings.jsonl] [--eligible eligible.jsonl] \
    [--breakdown position=x_capture.position --breakdown "workload=lineage.exp_snap_share|bins=0.4,0.7" \
     --breakdown capture_window] \
    [--cluster game --cluster player] [--bootstrap 1000 --breakdown-bootstrap 200 --seed 20261010] \
    [--pit-bins 10] [--no-validate] [--config cfg.json] [--output report.json]
```

Python API: `accuracy_scorecard.score(challenger_rows, outcome_rows, comparators=..., market_rows=...,
eligible_rows=..., breakdowns={name: key_fn}, cluster_by=("game",), ...)`. Exit code 2 means a contract
error. The scorecard fails closed on bad input.

### Inputs

* **Forecast rows.** One row per `(sport, game_id, player_id, statistic, as_of)`. Rows under the
  `pure_forecast` contract (the default) go through `pure_gate.validate`, so v1 and kit v0 rows are accepted
  and market vocabulary is refused. Rows under the `projection_only` contract (the default for
  `--market-benchmark`, or any arm named in `--projection-only`) need only the identity, `as_of`, an optional
  `kickoff`, `conditional_on_playing` and a `projection` object with the same shape rules. Duplicates are
  refused. Several capture times for one identity are distinct rows. `identities_with_multiple_capture_times`
  counts them, and the `capture_window` breakdown separates them.
* **Outcomes.** These use the pure_gate outcome format, `{sport, game_id, player_id, statistic, actual, played[,
  source, observed_at]}`, from sports sources only and never market settlement. They're validated by
  `pure_gate.outcomes_index`.
* **Market family (optional).** Listing or quote rows with `game_id`, `player_id`, `statistic`, an optional
  `sport`, `open_time`, and an observation time (`observed_at`, or the aliases `row_observed_at`,
  `board_observed_at`, `quoted_at`). The scorecard uses them for one thing only, which is cohort membership.
* **Eligible population (optional).** Identity rows, optionally with `as_of`, that define the full population
  (for example, every rostered player-stat on the slate). Without the list, the population is the union of
  challenger forecasts and outcomes. In that case an identity that has neither a forecast nor an outcome
  can't be seen, and the report says so.

### Populations

* `full_eligible` is every eligible player-game-statistic.
* `pregame_market_listed` is the identities that some market-family row shows as **opened**
  (`open_time <= as_of`) and **observed by our own records** (`observed_at <= as_of`) at or before the
  forecast's own as_of. It uses pregame records only, never settlement, and never what traded. A row missing
  either time can't establish a listing, and it's counted under `rows_without_open_time_or_observed_at`. For
  an eligible identity with no forecast, the reference time is the eligible row's `as_of`, then any arm's
  earliest as_of, then the market row's `pure_as_of`/`as_of`. If none of those exist, the identity is
  counted as `eligible_cohort_membership_unknown`.

### Accounting (nothing is dropped silently)

The report gives these counts per population, per arm and per statistic: `eligible_identities`,
`forecast_rows`, `missing_forecast` (failed or missed captures, with up to 20 example keys),
`missing_forecast_with_outcome`, `forecast_rows_missing_outcome`, `conditional_dnp_excluded` (conditional rows
whose player didn't play), `forecast_rows_outside_eligible_population`, `scored_rows`,
`identities_with_forecast` and `identities_with_multiple_capture_times`. Each summary reports `n_rows`,
`n_games`, `n_players` and `n_capture_times`. Head-to-head pairing counts `paired`, `comparator_missing`,
`comparator_has_identity_at_other_as_of` and `conditionality_mismatch_excluded`.

### Metrics

Scale-dependent metrics (MAE, RMSE, bias, CRPS, RPS) are reported **per `sport:statistic` only**.
`all_statistics_scale_free` pools only coverage, PIT, quantile calibration and the mean rung Brier and log
scores.

| Metric | Definition |
|---|---|
| MAE, RMSE, bias | of the forecast `mean` against `actual`; bias = mean − actual. `mae_of_median` is also reported |
| Interval coverage | share of `p10 <= actual <= p90` (nominal 0.80) and, for every published level q < 0.5 with 1 − q also published, the central (1 − 2q) interval. Intervals are closed, so ties in discrete statistics count as covered |
| Quantile calibration | per published level q: share of `actual < v_q` and of `actual <= v_q`. For a calibrated forecast, q lies between the two (they coincide for continuous statistics) |
| PIT histogram | interval-censored PIT (below), `pit_bins` bins, χ² uniformity statistic with df = bins − 1 and its p-value, maximum absolute deviation from uniform, PIT mean and variance (uniform: 0.5 and 1/12) |
| CRPS (quantile approximation) | `2 × mean pinball loss` over the published quantile levels (p10, median, p90 plus any `quantiles`); the same approximation as `pure_gate compare` |
| CRPS (ladder threshold decomposition) | `Σ_i w_i (P(X ≥ t_i) − 1{y ≥ t_i})²`, midpoint-rule widths over the ladder strikes |
| Brier, log score per rung | for each `at_least`: `(p − o)²` and `−ln p(o)`, plus mean probability and observed frequency (reliability). Probabilities are clipped to [1e-6, 1 − 1e-6] for the log score and the clips are counted |
| RPS | ranked probability score over the ladder categories, which equals the **sum** of the rung Brier scores; `rps_normalized_mean` divides by the number of rungs |

**Approximations, stated plainly:**

* *CRPS from quantiles.* CRPS(F, y) = 2∫₀¹ ρ_q(y − F⁻¹(q)) dq, where ρ_q is the pinball loss. Averaging the
  pinball loss over a finite set of levels approximates that integral. With only p10, median and p90 it's
  coarse and usually **understates** the CRPS of the full distribution, because the tails are missing.
  Publish denser `quantiles` for a better approximation. Two exact identities hold, and both are tested. For
  a point forecast (all quantiles equal to x) and any level set with mean level 0.5, the approximation
  equals |y − x|, so CRPS of a point forecast equals MAE. In head-to-heads, both arms are scored on their
  **common** levels.
* *CRPS from the ladder.* This uses the threshold decomposition CRPS = ∫ BS(t) dt. For an integer-valued
  statistic with a unit-spaced half-integer ladder covering the support, it is the **exact** CRPS (tested
  against a Poisson). Otherwise it covers only the ladder's range, so it is a threshold-weighted CRPS and a
  lower bound on the full CRPS. In head-to-heads it uses the common strikes only.
* *PIT from knots.* The rows don't carry the full CDF, only knots. A ladder rung (t, p) says
  P(X < t) = 1 − p. A quantile (q, v) says P(X < v) ≤ q ≤ P(X ≤ v). The scorecard therefore computes, per
  row, the interval [L, U] that must contain the randomized PIT, as follows:
  * `y ≥ t` raises L to 1 − p, and `y < t` lowers U to 1 − p.
  * `y > v` raises L to q, and `y < v` lowers U to q.
  * `y = v` gives no bound, which handles discrete ties.

  Each row's unit mass is spread uniformly over [L, U] (the non-randomized PIT of Czado, Gneiting and Held,
  2009). For a calibrated forecast, the histogram is uniform in expectation whatever the knot density. This
  holds exactly for continuous statistics and for discrete statistics with a half-integer ladder (both
  tested). Sparse knots don't bias the histogram, they only smooth it and reduce power. Knots that
  contradict each other (L > U) collapse to the midpoint and are counted (`n_inconsistent_knots`).
* *The χ² p-value* assumes independent rows. Rows from one game are correlated, so treat the p-value as
  optimistic. Also, because the masses are fractional, the χ² statistic isn't exactly χ²-distributed. Use
  it to rank forecasts and spot shapes (U-shaped means overconfident, hump-shaped means underconfident,
  sloped means biased), not as a formal test.

### Head-to-head

Comparator roles are `production_champion`, `statistical_baseline`, `independent_sports_only`,
`market_informed_benchmark` and `other`. Pairs are identical `(identity, as_of)` rows with the same
conditionality, and both arms are scored only on the knots they both publish. For each metric the report
gives the challenger mean, the comparator mean, the delta (challenger − comparator) and a percentile
bootstrap 95% CI that resamples whole **games** (`sport|game_id`). With `--cluster player`, it also gives a CI
that resamples whole players. Loss metrics carry a reading: `CHALLENGER_LOWER_LOSS`, `CHALLENGER_HIGHER_LOSS`
or `NOT_DISTINGUISHABLE` (`NO_CI` below 3 clusters). With fewer than 20 games, `few_game_clusters_warning` is
set. Delta RMSE is reported, but its CI is on the additive delta MSE. Every block is
`DESCRIPTIVE_ONLY_NOT_AUTOMATIC_PROMOTION`.

### Breakdowns

A breakdown is a key function `ctx -> label`. `ctx` holds `forecast` (the row), `outcome` (the row),
`forecast_obj` and `listed`. Breakdowns are always nested inside `sport:statistic`. Comparator rows take the
challenger row's label for the same `(identity, as_of)`, so every arm is cut the same way. A key function that
returns None gives `UNKNOWN`, which stays visible. On the command line a breakdown is written
`NAME=PATH[|bins=a,b,...]`. `PATH` is a dotted path into the challenger row (then the outcome row), for example
`x_capture.position` or `x_capture.matchup_tier`. `lineage.NAME` reads a `feature_lineage` value. There are
also built-ins:

* `capture_window`: hours from as_of to kickoff, in the buckets `le_2h`, `2h_6h`, `6h_24h`, `24h_72h` and
  `gt_72h`.
* `player`, `game` and `market_listed`.

Workload tiers, game-script buckets and opponent-adjusted matchup tiers are producer attributes. Put them in
`x_*` metadata or the lineage and point a breakdown at them, binning numeric ones with `bins=`. A breakdown
must be a function of as_of information (a projected workload, not the realised one) if it's going to say
anything about prospective accuracy.

---

## `value_scorecard.py`

```bash
cd pure-contract
python3 -I value_scorecard.py fee --price 0.55 --contracts 10 [--role maker] [--multiplier 1]
python3 -I value_scorecard.py evaluate \
    --probabilities probs.jsonl | --forecasts pure_forecasts.jsonl \
    --quotes kalshi_quotes.jsonl --outcomes outcomes.jsonl \
    --prereg prereg.json \
    [--rule max_edge_after_fees --min-edge 0.02] [--role taker --contracts 1 --slippage 0.01 --tick 0.01] \
    [--min-decisions 200 --min-games 40 --min-roi-ci-lower 0] [--market-informed-source] \
    [--cluster game --cluster player] [--bootstrap 2000 --seed 20261010] \
    [--as-of-policy earliest] [--dnp-policy settle_by_actual] [--max-quote-age-seconds 3600] \
    [--calibration-fit-end 2026-09-30T00:00:00Z] [--output report.json] [--decisions-out decisions.jsonl]
```

Python API: `value_scorecard.evaluate(probability_rows, quote_rows, outcome_rows, rule=..., fees=FeeModel(...),
thresholds=..., prereg=..., market_informed_source=...)`.

### Inputs

* **Probabilities** are one row per rung at one as_of: `{sport, game_id, player_id, statistic, at_least,
  as_of[, kickoff], p_yes}`, or `side` (`YES`/`NO`) with `probability`. Here `p_yes = P(actual ≥ at_least)`,
  calibrated upstream. Rows may carry `market_informed: true`, and any such row forces
  `NOT_INDEPENDENT_EVIDENCE`. `--forecasts` turns `pure_forecast` ladders into probability rows. A forecast
  that is conditional on playing and has a `participation_probability` is converted to unconditional
  (pp × p for `at_least > 0`). Without one, the rows are flagged `conditional_on_playing`.
* **Quotes** are the separate market family: `{[sport,] game_id, player_id, statistic, at_least, observed_at,
  yes_bid, yes_ask[, open_time, close_time, ticker, market|series, kickoff]}`. Prices are in dollars, or in
  cents as `yes_bid_cents`/`yes_ask_cents` (NBA listings) or with `price_unit: "cents"`. The NFL capture rows
  carry dollars in `yes_bid`/`yes_ask`. The strike must be mapped explicitly to the same `at_least`
  representation the probability rows use. For an integer statistic, Kalshi's "50+" is P(X ≥ 50), the same
  event as a ladder rung at 49.5. Matching is exact, so a mismatch is a missing quote, not a silent
  approximation.
* **Outcomes** use the pure_gate outcome format (sports truth). YES wins iff `actual >= at_least`.

### Per-candidate economics (as_of information only)

| Quantity | Definition |
|---|---|
| Executable price | YES: the YES ask. NO: 1 − YES bid (in cents, 100 − bid). No ask (or no bid) means that side isn't executable, and it's counted |
| Spread | YES ask − YES bid |
| Slippage | a configurable dollar amount per contract, added to the quoted price |
| Fee | `round_up(coefficient × multiplier × C × P × (1 − P))` to $0.01 for one order of C contracts at the executed price P, divided by C. Coefficient 0.07 for takers, 0.0175 for makers (`--role maker`; the entry price is still the ask, so maker mode is an optimistic fill assumption). Exact decimal |
| Edge after fees (EV per contract) | p_side − (price + slippage + fee) |
| Expected ROI | EV / all-in cost |
| Bet-up-to | the highest quoted price on the tick grid at which EV ≥ 0 after slippage and fees, given p |
| Realised return | 1{side won} − all-in cost per contract; ROI = Σ return / Σ cost |

The fee constants are the same as SIFT's `src/opportunity/pricing.ts` (`KALSHI_TAKER_RATE = 0.07`) and
nfl-edge-finder's fee schedule (`taker_base_coefficient 0.07`, `maker_base_coefficient 0.0175`). The known
answers are tested: 1 @ $0.50 is $0.02, 100 @ $0.50 is $1.75, 10 @ $0.29 is $0.15, 100 @ $0.10 is exactly
$0.63 (binary floating point would give $0.64), and a maker 1 @ $0.50 is $0.01. For whole-cent prices,
Kalshi's newer fixed-point rounding (`nfl_edge/execution/fees.py`: trade fee to $0.000001, cent alignment, a
per-order rebate accumulator) is never more expensive than the per-order cent round-up, so the estimate here
is conservative there. Sub-penny prices can differ by up to a cent either way. Use `--fee-multiplier` for
series with a published multiplier.

### Anti-hindsight rules

1. **Entry uses quotes at or before as_of, never later.** The entry quote is the latest one with
   `observed_at <= as_of` on a market with `open_time <= as_of` (when given) and not closed by as_of. A
   better price one second after as_of is ignored, and `quotes_after_as_of_ignored_for_entry` counts it. An
   optional maximum quote age discards stale quotes, and those are counted too.
2. **One as_of per decision group, fixed by policy.** The policy is `earliest` (default), `latest` or
   `unique`. It is never chosen by price or outcome. Rungs at other as_of times are dropped and counted.
3. **One decision per `(sport, game_id, player_id, statistic)`.** Several thresholds on one player-stat-game
   are a single decision. The declared rule picks at most one `(threshold, side)`. Several statistics in the
   same game stay separate decisions, but they share the game cluster, so the bootstrap never treats them as
   independent.
4. **Rules see only `CandidateView`.** A view is an immutable object holding the forecast probability, the
   entry quote and the derived economics. It carries no outcome, settlement, later quote or closing line.
   Asking a view for anything else raises `HindsightError`. A rule must return one of the views it was
   offered (or None), and it must be deterministic. It's run twice on independently built copies and must
   agree. Outcomes are joined only after every group's decision is fixed. Declared rules:
   * `max_edge_after_fees(min_edge)`: largest EV (the default).
   * `max_expected_roi(min_edge)`.
   * `nearest_even_rung(min_edge)`: fix the rung whose model P(YES) is nearest 0.5, then take the better
     side there.

   All of them require EV > 0 and EV ≥ min_edge. Ties go to the lower strike, then YES.
5. **In-sample decisions are excluded.** With `--calibration-fit-end`, rungs whose as_of is at or before the
   end of the calibration fit window are excluded and counted (`rungs_in_sample_excluded`).
6. **CLV is diagnostic only.** The closing quote is the last quote on the same ticker after as_of and at or
   before min(kickoff, close_time). Without either time, there is no CLV. `clv_mid` is the closing side mid
   minus the executed price, and `clv_executable` is the closing side ask minus the quoted entry price. CLV
   never enters the verdict.
7. **Preregistration can't be overridden.** With `--prereg`, every setting it names (rule, min_edge, fees,
   thresholds, bootstrap, cluster, policies) comes from it. A command-line value that contradicts it is
   refused (exit 2). The report echoes the thresholds and the sha256 of the canonical preregistration JSON,
   so a result can be matched to the committed preregistration.

### Outputs

The report has these parts:

* **Accounting**: probability rungs, quote rows, rungs with no quote at or before as_of, non-executable
  sides, groups without a candidate, groups where the rule declined, in-sample exclusions, and decisions by
  settlement (`SETTLED`, `MISSING_OUTCOME`, `VOID_DNP`).
* **`decisions_summary`**: n decisions, games and players; ROI after fees; mean return per contract; hit
  rate; mean probability, price, fee and spread; mean predicted edge; and realised minus predicted.
  Game-clustered (and optionally player-clustered) percentile bootstrap CIs are given for ROI (ratio of
  resampled sums) and for mean return.
* **`edge_bands`**: reliability within predicted-edge bands (−0.10, −0.05, −0.02, 0, 0.02, 0.05, 0.10, 0.20).
  For each band it gives mean predicted probability against the observed win rate, mean predicted edge
  against mean realised return, and ROI. It is computed for the decisions and, descriptively, for every
  executable candidate (correlated, so no CIs).
* **Breakdowns**: `by_statistic`, `by_market` (series or ticker prefix) and `by_side`, each with sample sizes
  and CIs.
* **`closing_line_value`**: n with a pre-close quote, mean CLV, the share with positive CLV, and a CI.

**DNP policy.** The default, `settle_by_actual`, settles the decision on the recorded actual: a player who
didn't play has an actual of 0, so YES loses for `at_least > 0`. The alternative, `void`, refunds the
decision. Check the series' rules, and preregister the policy. DNP settlements are counted.

### Verdict

The verdict is decided in this order:

1. `NOT_INDEPENDENT_EVIDENCE`: `--market-informed-source`, or any probability row marked `market_informed`.
   This is forced, whatever the numbers say.
2. `INSUFFICIENT_SAMPLE`: `n_decisions < min_decisions` or `n_games < min_games`. The defaults are 200 and
   40. Preregister your own.
3. `NO_EVIDENCE_OF_VALUE`: ROI after fees ≤ 0.
4. `VALIDATED_VALUE`: ROI > 0 and the 95% CI lower bound for **every** requested clustering (always game,
   plus player if requested) is > `min_roi_ci_lower` (default 0).
5. `POSITIVE_BUT_INCONCLUSIVE`: otherwise.

`VALIDATED_VALUE` is a statement about a recorded, preregistered evaluation window. It isn't wagering
authority, and nothing in these modules changes any staking or routing behaviour.

---

## Vendoring into a sport repo

Copy these files next to the already-vendored gate, without editing them:

```
pure-contract/accuracy_scorecard.py   -> tools/pure_gate/accuracy_scorecard.py
pure-contract/value_scorecard.py      -> tools/pure_gate/value_scorecard.py
pure-contract/SCORECARDS.md           -> tools/pure_gate/SCORECARDS.md        (optional, documentation)
```

`accuracy_scorecard.py` loads `pure_gate.py` from its own directory, so the two must sit together.
`value_scorecard.py` doesn't import the gate. Add the new files and their sha256 to `VENDORED_FROM`. The
existing pin tests hash only `pure_gate.py` and the schema, so they're unaffected, and `pure_gate.py` is
unchanged by this addition (still `36b52d07…0885`). Extend the pin test to the new files if you want them
frozen too. Compute hashes with `sha256sum accuracy_scorecard.py value_scorecard.py`.

## Limitations

* The scorecards trust the timestamps the producers recorded (`as_of`, `observed_at`, `open_time`). Run
  `pure_gate rerun` and the sport lane's audits for independence. These modules don't prove it.
* Quote capture is change-suppressed in some lanes, so "latest quote at or before as_of" can be old. Use
  `--max-quote-age-seconds` when the capture cadence is known.
* Fill risk, queue position and depth aren't modelled beyond slippage and the top-of-book ask, and
  available size isn't enforced.
* Bootstrap CIs are percentile CIs from a finite number of resampled clusters. With few games they're
  unstable, and the sample-size minimums exist for that reason.
