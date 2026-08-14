# Production Fee-Aware Net EV Integration milestone

Status: production decision-layer change. Moves live MLB/Kalshi
recommendation qualification from fee-blind calibrated edge to
fee-aware **net executable edge**. Does **not** change model
probabilities, projection engines, calibration factors, bankroll
sizing rules, or correlation rule tables — see "Safety" below.

## 0. The gap this milestone closes

PR #88 built and validated a fee-aware execution-economics engine
(`lib/edgelab/kalshi_fees.py`) for **research and historical
reconciliation only** — it deliberately never touched
`scripts/build_market_ledger.py`, `scripts/risk_gate.py`, or
`scripts/write_pending_bets.py`. Reconfirmed on current `main` before
this milestone: grepping all three files for
`friction|fee|netEdge|adjustedEdge|postFriction|kalshi_fees|execution_economics`
returned zero real hits. The word "friction" that does appear in
`build_market_ledger.py`'s comments is a misnomer for "ask-based
execution price vs. mid-derived VF price" — it has nothing to do with
Kalshi trading fees. **No Kalshi transaction fee dollar amount was ever
subtracted anywhere in the live recommendation path before this
milestone.**

## 1. Old production decision formula (audited before any change)

```
model_prob (per-family Poisson/three-way projection)
  -> kalshi_vf = mid-derived vig-free probability
  -> exec_prob = executable_prob_from_price(yes_ask_cents)   # the real cost to enter
  -> rawEdgeVsExecutable = (model_prob - exec_prob) * 100
  -> calibratedEdgeVsExecutable = rawEdgeVsExecutable * CAL_MEDIUM   # CAL_MEDIUM=0.255, hardcoded at every call site
  -> edge = calibratedEdgeVsExecutable   ("legacy alias")
  -> confidence_from_edge(edge): edge<1.0 -> None; edge>=3.0 -> HIGH; edge>=1.5 -> MEDIUM; else PAPER
  -> cap_tier_for_disagreement (HIGH capped to MEDIUM if |rawEdgeVsVF|>7pt)
  -> enforce_bet_up_to (price ceiling from bet_up_to_price_cents(), a closed-form inversion of the SAME calibrated-edge formula, no fee term)
  -> bet_size(conf, market)  (fixed base x market-multiplier unit scheme, unrelated to fees)
  -> write_pending_bets.py copies betSize/edge verbatim into the pending-bet record
```

`CAL_HIGH`/`CAL_PAPER` are defined but never referenced — every market
family hardcodes `CAL_MEDIUM` regardless of tier (a pre-existing quirk,
**not** touched by this milestone — see "Safety").
`config/rules.json`'s `calibration`/`edge_thresholds` blocks mirror
these constants for documentation purposes only; none of the three
production scripts actually load that file at runtime.

## 2. New economics

All new fields are computed **once**, inside
`scripts/build_market_ledger.py`'s `build_edge_fields()` — the single
function every market family (ML/TT/F5/NRFI/YRFI) already called for
its edge fields, so the fee integration required no per-family
duplication. `lib/edgelab/kalshi_fees.py` (PR #88's engine) is the
**only** fee formula in the codebase; `build_edge_fields()` imports and
calls it directly, never reimplements it.

- **`feeAdjustedBreakEvenProbability`** = `kf.fee_adjusted_break_even_probability(execPrice)`
  = `execPrice + multiplier*execPrice*(1-execPrice)` — the probability-space
  price shift a real Kalshi trading fee represents. Reused verbatim from
  PR #88, never a fixed-cent haircut.
- **`expectedFeeDrag`** = `(feeAdjustedBreakEvenProbability - executableMarketProb) * 100` —
  the fee expressed in the SAME raw edge-percentage-point units as
  `rawEdgeVsExecutable`. **Not** the ROI-space "fee-only drag" research
  reports use (`fee_only_drag_percentage_points`) — that's a different
  unit space (return-per-dollar) appropriate for ROI reporting;
  production's whole architecture (edge, calibration, thresholds,
  bet-up-to) is probability/edge-space, so the break-even-shift
  formulation is the mathematically correct, consistent choice here.
- **`netExecutableEdge`** = `(rawEdgeVsExecutable - expectedFeeDrag) * CAL_MEDIUM` —
  the exact SAME calibration methodology already applied to
  `calibratedEdgeVsExecutable`, just measured against the fee-adjusted
  break-even instead of the raw executable price. **This is the new
  primary qualification metric** (`edgeUsedForQualification` is updated
  from `'calibratedEdgeVsExecutable'` to `'netExecutableEdge'`).
  `calibratedEdgeVsExecutable`/`edge`/`edgeUsedForDisplay` are
  **unchanged** — gross edge remains fully preserved and inspectable.
- **`netExpectedValuePerDollar`** = `kf.net_expected_value_per_dollar(modelProb, execPrice)`
  called directly, no local reimplementation — the same scale-invariant
  continuous-exposure formula PR #88's Tier B ("fee-only") research
  metric uses. Deliberately scale-invariant: the eventual stake isn't
  known yet at this point in the pipeline (`bet_size()` runs after tier
  assignment), so a stake-dependent formula would create a circular
  dependency.
- **`feeType`/`feeMultiplier`/`feeSource`/`feeScheduleVersion`** — fee
  provenance from `kf.fee_rule_for_series(seriesTicker)`. No MLB series
  in this repo has any evidence of a non-standard multiplier, so the
  math always uses the standard 0.07 taker rate today; an unregistered
  or missing series ticker returns the documented standard-taker
  fallback with an explicit `UNKNOWN_SERIES`/`NO_SERIES_TICKER_PROVIDED`
  confidence marker — **never a silent zero fee** (spec section 37,
  "fail-closed fee behavior").
- **Default execution assumption**: taker (`kf.FEE_TYPE_TAKER`) always —
  matches "crossing the market now" for an immediately actionable
  recommendation (spec section 6); no maker-rest assumption is ever
  used to improve EV.
- **Reference-allocation decomposition** (`referenceAllocationDollars`
  etc.): an illustrative-only worked example at a standardized $10
  allocation (`kf.DEFAULT_RESEARCH_ORDER_SIZE`, the same constant PR
  #88's research reports use), computed via `kf.simulate_order()`.
  **Never** the real bet's dollar stake — production's `betSize`/`stake`
  has always been a bankroll-**unit** multiplier (`bet_size(conf,
  market)`'s fixed base x market-multiplier scheme), not a dollar
  amount, and this milestone does not change that (no
  `stake / price = contracts` bug existed anywhere in
  `write_pending_bets.py` to begin with — verified directly).

## 3. Bet Up To — old vs. new, with worked examples

**Old formula** (`bet_up_to_price_cents`, unchanged, preserved as
`betUpToPriceGross`):
```
kalshi_vf_ceiling = fair_prob - threshold_pct / (cal_factor * 100)
```

**New formula** (`fee_aware_bet_up_to_price_cents`, drives
`betUpToPriceNet` — the ceiling actually enforced):
```
target_prob = fair_prob - threshold_pct / (cal_factor * 100)   # same target as the gross formula
price = kf.fee_adjusted_bet_up_to_price(target_prob)           # binary-search inversion of the SAME fee-adjusted break-even function above
```
Because `feeAdjustedBreakEvenProbability(P) = P + f(P) >= P` for any
valid price (the fee is never negative), `betUpToPriceNet <=
betUpToPriceGross` always holds for a nonzero fee — proven
algebraically and pinned down by
`test_item24_net_bet_up_to_never_exceeds_gross_bet_up_to` (property test
across 5 representative fair probabilities) and
`test_gross_ceiling_exceeds_net_ceiling_for_nonzero_fee`.

Worked example (`fair_prob=0.60`, `THRESHOLD_PAPER=1.0`,
`CAL_MEDIUM=0.255`): **gross ceiling = 56.08¢, net ceiling = 54.34¢** —
a 1.74¢ reduction. A price landing between those two ceilings (e.g.
55.21¢) now correctly fails to qualify, where the old gross-only check
would have accepted it.

`enforce_bet_up_to()` computes **both** ceilings (gross preserved for
display/backward-compat) but gates `exec_price_cents` against the NET
ceiling — keeping the price-ceiling check consistent with
`confidence_from_edge()`'s own net-edge decision metric at every call
site (spec section 33, "apply fee exactly once": there is exactly one
fee-aware decision surface, not two disagreeing ones).

## 4. Correlation ranking (risk_gate.py)

`scripts/risk_gate.py`'s `_entry_edge(entry)` — the single ranking
function used by `evaluate_correlation_gate`'s same-side-thesis
dedup/per-game cap/cluster-trim, and `build_risk_portfolio`'s TT-cap
sort — now prefers `entry.get('netExecutableEdge')` when present,
falling back to the legacy `edge`/`calibratedEdgeVsExecutable` chain
for any row that predates this milestone. This is an explicit,
narrow, single-function change (not a blanket reinterpretation of the
widely-consumed `edge` field, which 47 other files in this repo
reference) — see `docs/`'s own audit trail in
`scripts/risk_gate.py`'s `_entry_edge` docstring for why that choice
was made over repurposing `edge` itself. The TT-specific
`TT_MIN_EDGE_PCT` floor (a second, independent check on top of
`build_market_ledger.py`'s own gate) was updated the same way, so both
TT gates stay consistent with each other.

`CORRELATION_RULES`, `build_same_game_clusters`,
`GAME_MAX_REAL_MONEY_BETS`, `GAME_CLUSTER_MAX_STAKE_PCT` — all
**unchanged**. Only the ranking metric fed into the existing logic
changed.

## 5. Market family coverage

Every market family that reaches `build_edge_fields()` is covered
uniformly: **ML** (away/home), **TT** (away/home), **F5** (away/home,
tie-aware three-way pricing untouched — see below), **NRFI/YRFI**
(each independently priced and gated, never derived from the other).
**RL** (run lines) and **Game_Total** are unconditionally suspended in
`build_market_ledger.py` (Rule 81/Rule 71 market suspension) and never
reach `build_edge_fields()` at all — this milestone does not change
that suspension. Pitcher/hitter props are not evaluated in
`build_market_ledger.py`/`risk_gate.py` at all (a separate, pre-existing
research-only path) and are therefore out of scope, unchanged.

**Tie-aware F5**: `p_f5_away`/`p_f5_tie`/`p_f5_home` are computed
upstream (`three_way_result_probs`, never renormalized — tie is its own
separately-priced, never-collapsed outcome) and passed into
`build_edge_fields()` unchanged. The fee-adjusted break-even shift
operates on the same `f5_yes_ask_c` executable price already tied to
the exact F5_ML contract event being evaluated — model probability,
executable price, payout event, and fee-aware break-even all refer to
the identical contract event throughout, with zero risk of mixing the
tie outcome into a two-way calculation.

## 6. Behavior diff (descriptive / in-sample — spec sections 24-26)

Run via `scripts/edgelab/production_fee_gate_shadow_diff.py` against
the causal, no-look-ahead historical opportunity corpus (13-14 dates).
**Methodology**: `build_market_ledger.py`'s `evaluate_game()` needs a
full live game dict (pitcher stats, lineups, bullpen, park factors)
the causal research corpus does not preserve — reconstructing one would
mean fabricating inputs. Instead, this script calls the exact same
production functions (`build_edge_fields`, `confidence_from_edge`,
`bet_up_to_price_cents`, `fee_aware_bet_up_to_price_cents`) directly
against each causal opportunity's own `(modelFairProbability,
executable price, mid-derived VF)` triple — the identical code path,
fed from the research corpus instead of a live slate row. No threshold
retuning: `THRESHOLD_PAPER/MEDIUM/HIGH`/`CAL_MEDIUM` are the unchanged,
imported production constants.

**DESCRIPTIVE / IN-SAMPLE BEHAVIOR AUDIT — not proof the new gate is superior.**

| Metric | Old (fee-blind) | New (fee-aware) |
|---|---:|---:|
| Causal opportunities audited | 528 | 528 |
| Qualifiers | 256 | 223 |
| Retained | — | 192 |
| Rejected by fees | — | 33 |
| Tier downgraded (still qualifies, lower tier) | — | 31 |
| Unchanged (never qualified) | — | 272 |
| Average Bet Up To reduction | — | 1.60¢ |

Old vs. new qualifying-set descriptive outcomes:

| Metric | Old qualifying set | New qualifying set |
|---|---:|---:|
| n | 256 | 223 |
| Independent games | 68 | 68 |
| Gross ROI | +0.22% | −0.24% |
| Fee-only ROI | −3.31% | −3.79% |
| Realistic-execution ROI | −3.24% | −3.69% |
| YES / NO split | 112 / 144 | 79 / 144 |

By side: NO opportunities were essentially unaffected (144 old, 144
new, 0 rejected by fees) — YES opportunities lost 33 of 112 to fees
(29.5%). This mirrors PR #88's own 10%+ bucket finding (YES
underperforms NO after fees) but is **not** implemented as a
side-preference rule anywhere — `test_item16_no_side_bias_rule` greps
the live source for banned "prefer NO"/"fade YES" phrases and confirms
none exist; each side is independently priced and gated on its own
executable price (spec sections 15-16).

By market family: `team_total` lost only 1/145 qualifiers to fees
(cheap, near-50¢ prices where fee drag is proportionally small relative
to a healthy edge margin); `game_result` (ML) lost 15/18 (thin-edge ML
candidates were disproportionately fee-sensitive); `first_inning_run`
(NRFI/YRFI) lost 11/68; `inning_result` (F5) lost 6/25. Full breakdown
by family/side/price-bucket/edge-bucket/checkpoint is in
`data/edgelab/analytics/latest_production_fee_gate_validation.json`.

Chronological split maturity: **`FRAMEWORK_ONLY_INSUFFICIENT_DATES`**
(14 distinct dates, below `lib.edgelab.research_splits.MIN_DATES_FOR_MATURE_SPLIT=30`)
— the DEV/VALIDATION/HOLDOUT split is computed and labeled honestly,
not skipped, but this corpus is not yet mature enough for a real
out-of-sample validation. **This finding is explicitly not claimed as
proof the new gate is superior** — it is reported as what it is: an
in-sample description of how the decision boundary moved.

## 7. Core acceptance cases (spec sections 31-32)

- **Marginal edge (NO BET required)**: `model_prob=0.555`,
  `price=0.51¢` → gross calibrated edge clears `THRESHOLD_PAPER` (1.0)
  but net executable edge does not (`0.70 < 1.0`) → `confidence_from_edge`
  returns `None`. Proven in `test_item08_gross_positive_net_negative_candidate_rejected`.
- **Strong edge (BET retained)**: `model_prob=0.68`, `price=0.50¢` →
  both gross and net calibrated edges clear MEDIUM/HIGH, with gross
  edge, fee drag, and net edge all visible as separate fields. Proven
  in `test_item09_strong_candidate_retained_with_all_three_edges_visible`.

## 8. Safety

- **Model probabilities**: `p_team_wins`/`p_over_total`/`vig_free_2way`/`vig_free_3way`/
  `three_way_result_probs` — zero fee-engine dependency, verified by
  source-scan test (`test_model_probability_functions_unchanged_by_fee_integration`).
- **Bankroll sizing**: `bet_size(conf, market)` (fixed base x
  market-multiplier unit scheme) — zero fee-engine dependency, same
  base/multiplier table, verified by source-scan + exact-value test.
  Kelly multipliers, bankroll percentages, max game/daily exposure caps
  (`DAILY_RISK_CAP`, `GAME_MAX_REAL_MONEY_BETS`,
  `GAME_CLUSTER_MAX_STAKE_PCT`, TT caps) — all unchanged.
- **Correlation rules**: `CORRELATION_RULES` table, cluster-building,
  concentration caps — unchanged; only the ranking metric (`_entry_edge`)
  was updated, narrowly and explicitly.
- **Fee double-counting**: `kalshi_fees`/fee-adjusted-break-even/net-EV
  functions appear in exactly one production file
  (`scripts/build_market_ledger.py`) — `scripts/risk_gate.py` and
  `scripts/write_pending_bets.py` only ever read already-computed
  fields, never recompute a fee. Verified by source-scan test
  (`test_item28_no_fee_double_counting_across_pipeline`).
- **Calibration double-counting**: the same `cal_factor` is applied
  exactly once, to the fee-adjusted raw edge — verified numerically
  (`test_item29_no_calibration_double_counting`).
- **Bid/ask double-counting**: the executable (ask) price already used
  for `calibratedEdgeVsExecutable` is the SAME price fed into the
  fee-adjusted break-even shift — no separate spread/slippage term is
  ever subtracted inside `build_edge_fields()` (verified by source-scan
  of the function body).

## 9. Tests

`tests/test_production_fee_aware_net_ev.py` — 45 tests covering the
spec's full 40-item checklist (gross/calibrated preserved, fee applied
once, net<gross, zero-maker-fee, series multiplier, unknown-fee
fail-closed, both core acceptance cases, YES/NO, 10¢/50¢/90¢,
$5/$10/$25/$100 reference allocations, fractional/whole/unknown
granularity, fee-adjusted break-even, fee-aware Bet Up To + monotonic
`net <= gross` property test, tier downgrade/no-upgrade, risk-gate net
metric, no fee/calibration/bid-ask double-counting, tie-aware F5,
correlation ranking, stake semantics, fee provenance, post-trade
override, backward-compat schema, prospective-snapshot no-lookahead).

`tests/edgelab/test_production_fee_gate_shadow_diff.py` — 8 tests for
the shadow-diff script's classification/breakdown logic.

Plus targeted updates to 6 existing test files whose fixtures/legacy
differential snapshots needed to account for the new (purely additive)
fields or the intentional fee-aware behavior change: `test_f5_three_way_pricing.py`
(field-set), `test_build_market_ledger_projection_boundary.py` +
`test_end_to_end_pipeline_sandbox.py` (subprocess sandbox now needs
`lib/edgelab/kalshi_fees.py` copied in, same convention as
`bullpen_availability.py`), `test_phase1_f5_executable_price.py`
(4-tuple `enforce_bet_up_to` return, net-vs-gross ceiling behavior),
`test_write_pending_bets_differential.py` (strips the new additive keys
before the legacy byte-for-byte comparison), and
`tests/edgelab/test_research_fee_awareness.py` (PR #88's
"production scripts never import the fee engine" guard is now split:
`risk_gate.py`/`write_pending_bets.py` still guarded; a new test
confirms `build_market_ledger.py` DOES import it, intentionally).

One test-fixture recalibration: `test_end_to_end_pipeline_sandbox.py`'s
happy-path fixture had a thin enough edge margin (gross calibrated edge
1.784, just above `THRESHOLD_MEDIUM=1.5`) that fee drag (1.75pp)
legitimately dropped it to PAPER — the offense-gap fixture input was
widened (KC runsScored 480→500) to restore a comfortable net-edge margin,
preserving the test's original intent (prove the full chain reaches a
real accepted bet) under fee-aware gating too. Documented inline at the
fixture.

Full CI-equivalent suite (`tests/` with the 5 standard deselects):
**5137 passed, 7 skipped, 5 deselected, 0 failed.**

## 10. Activation decision

Validation is clean: all tests pass, no double-counting found, no
inconsistent family behavior, fail-closed fee behavior fully defined
(unknown series/no series ticker → documented standard fallback, never
silent zero), both core acceptance cases proven, shadow-diff behavior
diff produced and honestly labeled descriptive/in-sample. Per spec
section 41, **fee-aware net executable EV is enabled as the live
decision metric in this PR** — there is no separate feature flag;
`edgeUsedForQualification` was changed from `'calibratedEdgeVsExecutable'`
to `'netExecutableEdge'` directly at the single choke point
(`build_edge_fields()`), which every market family's `confidence_from_edge()`
call already reads from.

The production fee-blind→fee-aware gate change is now live; a
**separate**, not-yet-started milestone would be required to further
retune thresholds/calibration based on live results — explicitly out of
scope here (spec section 11: no threshold mining from this pass).
