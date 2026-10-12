# Preregistration: betting-value evaluation of the MLB pitcher PURE shadow (dry-run diagnostic)

Committed on 2026-10-12. No `data/shadow_pure/mlb_pitcher` store existed on `main` on that date, and no
prospective row had matured. The machine-readable copy is
[`PITCHER_SHADOW_VALUE_PREREG.json`](PITCHER_SHADOW_VALUE_PREREG.json). The code loads that file and records its
sha256 in every `value_scorecard` record. If this document and the JSON disagree, the JSON is what ran.

Changing any setting requires a new preregistration id and a new evaluation window.

This is track C, market value. It is kept separate from accuracy promotion, which uses the `accuracy_scorecard`
records. It runs only as a **dry-run diagnostic** inside `scripts/research/mlb_pitcher_shadow.py evaluate`. Nothing
reads its output to place, size or route an order, and nothing here touches staking or a production surface.

## Hypothesis and arms

* **H1.** In the regular season, at the pregame capture (about T−60m, after lineups), buying the side preferred by
  the frozen challenger `mlb_pitcher_workload_joint_v1` makes money. Entry is at the executable Kalshi price, and
  the result is net of taker fees and one tick of slippage.
* **Primary arm (the only arm eligible for an independent-value verdict):** `challenger`, pregame window,
  regular season. The challenger is validated for the regular season only, so the primary evaluation window opens
  with the 2027 regular season.
* **Descriptive only, never verdict-bearing:**
  * the challenger at `morning`
  * `champion` and `baseline` at `pregame`
  * the challenger at `pregame` in the postseason, labelled `BLOCKED_NOT_VALIDATED_POSTSEASON`
* **Market-informed arms:** the collector has none. A probability source flagged market-informed would be forced to
  `NOT_INDEPENDENT_EVIDENCE`.

## Inputs (as_of information only)

* **Probabilities.** The captured ladder, exactly as written: `p_yes = P(actual ≥ at_least | the probable starter
  starts)`. `participation_probability` is null.
* **Quotes.** These come from the separate `market/` listing snapshots and are mapped by
  `lib/research/mlb_pitcher_shadow_scorecards.market_family`:
  * The player is the probable starter matched by name and team. Unmatched rows are dropped and counted.
  * **Strike:** `comparisonOperator = AT_LEAST` with `threshold` K (Kalshi "K+") maps to `at_least = K` on the
    integer ladder. `GREATER_THAN f` maps to floor(f)+1. Any other operator is dropped and counted.
  * **Prices** are in dollars.
  * **observed_at** is the quote's `quoted_at`. A quote observed after the snapshot's as_of is dropped and counted.
  * **Entry** is the latest quote at or before as_of.
* **Outcomes.** StatsAPI box-score outcomes. `played` means the projected starter started.

## Decision rule, costs, settlement

| setting | value |
|---|---|
| decision unit | one per (game, pitcher, statistic), at the earliest as_of within the window |
| rule | `max_edge_after_fees`, `min_edge = 0.02` |
| fees | Kalshi taker: coefficient 0.07, multiplier 1, 1 contract per order, cent round-up (`docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md`) |
| slippage | $0.01 per contract |
| stale quote | a quote older than 21,600 s at as_of is discarded and counted |
| calibration window | none: all arms are frozen before collection |
| DNP policy | **`void`** |
| DNP sensitivity | `settle_by_actual`, reported as `dnp_sensitivity` and never verdict-bearing |

**DNP policy, checked against the series rules.** The repository doesn't document how a pitcher prop settles when
the listed pitcher doesn't start. `docs/PLAYER_PROP_SETTLEMENT.md` §6 records that no Kalshi rules text or
void-condition field is captured for these series, and it forbids guessing an automatic VOID/NO path. This
preregistration therefore voids (excludes) a decision whose pitcher didn't start, instead of inventing a
settlement. It reports `settle_by_actual` as a sensitivity. Under that policy a non-starter's recorded relief line,
or 0, settles the contract.

If the series rule is captured later, it can be adopted only under a new preregistration id.

## Minimum sample and inference

* **Minimum sample.** `min_decisions = 200` and `min_games = 40`, the SCORECARDS.md defaults. Below either one the
  verdict is `INSUFFICIENT_SAMPLE`.
* **Confidence intervals.** ROI after fees is reported with percentile bootstrap CIs that resample **whole games**
  (2,000 replicates, seed 20261010). A second CI resamples whole pitchers.
* **Verdicts.** `VALIDATED_VALUE` needs ROI > 0 **and** every clustered 95% CI lower bound > 0. ROI > 0 that doesn't
  meet the CI condition is `POSITIVE_BUT_INCONCLUSIVE`. ROI ≤ 0 is `NO_EVIDENCE_OF_VALUE`.
* **CLV** is diagnostic only. It never enters the verdict.

## What a verdict means

A verdict describes a recorded, preregistered evaluation window. It isn't wagering authority. This document
authorizes no change to staking, bet logging or routing.
