# F5_THREE_WAY_PRICING.md

**Milestone:** F5 Three-Way Pricing Correction
**Scope:** Correct the known production pricing error for F5 winner
markets by preserving and pricing the real tie outcome instead of
discarding it through two-way renormalization. Full-game moneyline
pricing, staking, tier thresholds, bankroll rules, market-selection
philosophy, and settlement logic were NOT changed.

---

## 1. Root cause

Kalshi's F5 market has a real, separately tradable **TIE** contract.
Confirmed directly from a real, live market snapshot
(`data/kalshi_registry_snapshots/kalshi_search_2026-07-29_0803.json`):

```
F5 event KXMLBF5-26JUL291310ATLNYMG1 -> 3 contracts:
  KXMLBF5-26JUL291310ATLNYMG1-TIE  (title: "...first 5 innings tie?", american_odds: 614)
  KXMLBF5-26JUL291310ATLNYMG1-NYM  (american_odds: 147)
  KXMLBF5-26JUL291310ATLNYMG1-ATL  (american_odds: 208)

GAME event KXMLBGAME-26JUL292210SEALAD -> 2 contracts only:
  KXMLBGAME-26JUL292210SEALAD-SEA  (american_odds: 135)
  KXMLBGAME-26JUL292210SEALAD-LAD  (american_odds: -141)
```

A full-game MLB moneyline is genuinely two-way: a tied regulation game
always continues into extra innings until a winner is decided, so there
is no real "tie" outcome to price. F5 is different: the market settles
on the score after exactly 5 innings, and a tie at that point is a real,
final, separately-priced outcome.

Production's prior F5 code (`scripts/build_market_ledger.py`) computed
the correct three-way Poisson probability (away win / tie / home win)
via the same joint-distribution math full-game ML uses, then discarded
the tie by renormalizing:

```python
p_away_win, p_push_f5 = p_team_wins(f5_away, f5_home)
p_home_win = 1 - p_away_win - p_push_f5
p_away_net = p_away_win / (1 - p_push_f5)   # <-- renormalization
p_home_net = p_home_win / (1 - p_push_f5)   # <-- renormalization
```

The tie contract's own price (`f5ml.get('tie_american')`) was fetched
into a local variable and never read again anywhere else in the file —
confirmed by grep showing exactly one occurrence. The market-implied
side had the identical bug: `vig_free_2way(f5_away_am, f5_home_am)`
computed a vig-free split assuming away+home are the only two outcomes,
silently discarding the tie contract's price there too.

Net effect: both team-side fair probabilities were systematically
overstated on both the model side and the market-comparison side.

## 2. Before/after numerical example

Using real F5 projections (away=2.3, home=1.9 runs) and a real
Kalshi F5 three-way price (away=-130, tie=+260, home=+150):

| | Away | Tie | Home |
|---|---|---|---|
| **Model (correct, three-way)** | 47.59% | 19.83% | 32.58% |
| **Model (legacy, renormalized)** | 59.36% | *(discarded)* | 40.64% |
| **Market (correct, 3-way vig-free)** | 45.47% | 22.35% | 32.18% |
| **Market (legacy, 2-way vig-free)** | 58.56% | *(discarded)* | 41.44% |

| Edge on F5_ML_Away | Legacy (buggy) | Corrected |
|---|---|---|
| model − market | 59.36% − 58.56% = **0.80pp** | 47.59% − 45.47% = **2.12pp** |

Note the correction does not uniformly shrink edge — both sides of the
comparison moved, and by different amounts, because the market's own
vig structure also changes once the tie contract's real price is
included. This example's edge actually *increased* after correction.
The historical impact study (§8) confirms this both-directions finding
across real evaluated games, not just this one illustrative case.

## 3. Corrected formula

```python
# lib/research/three_way_projection.py (already existed, pure, tested;
# now imported as a hard production dependency by build_market_ledger.py)
r = three_way_result_probs(f5_away_proj, f5_home_proj, max_runs=20)
p_away, p_tie, p_home = r["awayWinProb"], r["tieProb"], r["homeWinProb"]
# p_away + p_tie + p_home == 1, always -- never renormalized.

# scripts/build_market_ledger.py (new)
vf_away, vf_tie, vf_home = vig_free_3way(away_american, tie_american, home_american)
model_p = p_away if market == "F5_ML_Away" else p_home
kalshi_vf = vf_away if market == "F5_ML_Away" else vf_home
```

`vig_free_3way()` mirrors `vig_free_2way()` exactly, extended to three
American-odds inputs: implied probabilities are computed for all three
sides, then normalized to sum to 1 together (removing the vig across all
three contracts, not just two).

`three_way_result_probs()` reuses the identical independent-Poisson
joint-distribution math `p_team_wins()` already used (confirmed
numerically identical to 8+ decimal places at `max_runs=20` across the
full realistic F5 projection range, 1.2–4.1 runs) — this fix does not
introduce a new statistical model, it stops discarding a probability the
model already correctly computed.

## 4. Why full-game ML stays two-way

Full-game moneyline markets on Kalshi have exactly 2 contracts, no tie
leg (§1). A completed MLB game literally cannot end in a tie — it
continues into extra innings until a winner is decided. The "push"
probability `p_team_wins()` computes for a full 9-inning projection is a
Poisson-model artifact of comparing two run distributions at a fixed
inning count, not a real possible outcome — renormalizing it away for
ML_Away/ML_Home is *correct*, because there is no real market to price
it against. `scripts/build_market_ledger.py`'s ML_Away/ML_Home block
(`p_team_wins` + `vig_free_2way` + renormalization) is untouched by this
milestone — confirmed byte-for-byte unchanged via
`tests/test_f5_three_way_pricing.py::TestFullGameInvarianceUnderTheF5Fix`.

## 5. Treatment of F3/F5/F7

**Updated by the Systematic Best-Expression Comparison mission — the
status below superseded this section's original text, which was written
before F3/F7's contract structure was directly captured and is now
factually wrong; kept auditable here rather than silently rewritten.**

`lib/research/market_taxonomy.py`'s `HORIZON_MARKET_STATUS` now
classifies all three horizons as evidence-confirmed three-way:

- **F5**: `outcomeStructureStatus = CONFIRMED_THREE_WAY` — independently
  verified via a real market snapshot (§1).
- **F3, F7**: `outcomeStructureStatus = CONFIRMED_THREE_WAY` — F7's raw
  market payload was captured directly
  (`data/kalshi/discovery/2026-07-30_f3_f7_search.json`'s
  `structureVerificationRawMarkets.KXMLBF7`: every event has exactly 3
  tickers, Away/Home/TIE). F3's own `-TIE` ticker, not directly observed
  in that specific 2026-07-30 run (rate-limited before that query — see
  `lib/research/market_taxonomy.py`'s own comment on this), **has since
  been directly captured** in later daily discovery snapshots (e.g.
  `data/kalshi/discovery/2026-08-18_f3_f7_search.json`'s
  `structureVerificationRawMarkets.KXMLBF3` — 75 markets across 25
  events, every event carrying its own `KXMLBF3-<event>-TIE` ticker,
  identical shape to F7). Both are `structureStatus = VERIFIED`.

This section previously stated F3/F7 were `UNVERIFIED` because "no
archived snapshot in this repository has ever captured their contract
structure" — that claim is now false; it described a state that existed
only in the window before `scripts/discover_kalshi_series_catalogue.py`'s
ongoing daily captures accumulated the direct F3 TIE-ticker evidence
above. The renormalization bug this document fixes (§1-§3) has still
only been corrected for F5 in `scripts/build_market_ledger.py` — F3/F7
remain `productionEnabled: False` (a deliberate market-selection-scope
decision, not a data-availability gap: no calibrated model exists for
either horizon — see `lib.research.three_way_projection`'s own
`scale_fn` docstring, which explicitly calls the default horizon-fraction
scaling a "RESEARCH-ONLY placeholder... NOT a claim that naive linear
scaling is the right model"). F3/F7 prices ARE now exposed as
research-only references (Market-Universe Parity mission's
`kalshi.f3ml`/`f7ml`, and the Systematic Best-Expression Comparison
mission's `expressionGroup` field on each F5 row) without a model
probability attached to either.

## 6. Contract mapping and where the Tie is exposed

`scripts/merge_odds.py` already populated `odds.kalshi.f5ml` with
`away`/`home`/`tie` American odds and `away_ticker`/`home_ticker`/
`tie_ticker` before this milestone — the data was always present, just
unused. `build_market_ledger.py` now reads all three.

**Deliberate scope decision:** the Tie contract is exposed as
**informational pricing data** attached to both `F5_ML_Away` and
`F5_ML_Home` rows (`row["f5TieContract"]` — modelFairProbability,
modelFairPrice, marketImpliedProbability, estimatedEdge,
expectedValuePerDollar, ticker), **not** added as a new bettable
`REQUIRED_MARKETS` entry. Adding a 12th recommendable market would be a
market-selection-philosophy change, explicitly out of scope for a
pricing correction. This is consistent with
`lib/research/inning_result_shadow_ledger.py`'s pre-existing, deliberate
design (F5's three-way structure is `PAPER_ONLY`/`realMoneyEligible:
False` pending calibration, not a design decision invented by this
milestone).

## 7. Probability and price conventions

- `marketImpliedProbability`/`kalshiVF` use the mid-price-derived
  vig-free probability — the same convention this file already used for
  every other market, now extended across all three F5 sides.
- `executablePriceUsed`/EV-per-dollar use the executable YES-ask price
  (`scripts/executable_price.py`'s existing convention), unchanged.
  `american_to_ask_cents()` prefers a real registry `yes_ask` and falls
  back to the American-mid-derived implied probability — the exact same
  fallback F5_ML_Away/F5_ML_Home already used before this milestone. A
  **separate, pre-existing, unrelated** gap was found while wiring this:
  `merge_odds.py` never actually passes an F5 `prices` sub-block through
  at all (`f5ml.get('prices')` is always `{}` in production today), so
  the fallback path is *always* taken for F5 in practice. Documented
  here, not fixed — orthogonal to the renormalization bug this milestone
  corrects.
- Rounding: probabilities are rounded to 2 decimal places (as a
  percentage) only at the row-construction boundary
  (`contract_pricing()`); `three_way_result_probs()`/`vig_free_3way()`
  themselves never round internally.

## 8. Historical impact (research-only; see `scripts/research/f5_historical_impact_study.py`)

Two confirmed data-availability limits bound what can be reproduced:

1. `bets.json` has 144 historical F5 bets (2026-05-26 through
   2026-08-01), but only the *final* (already-renormalized) probability
   was ever persisted — never the raw run projections. `data/pipeline/`
   pipeline-stage artifacts (the only place the raw projections are
   preserved) exist for only 3 dates. **137 of 144 historical F5 bets
   have no preserved projection inputs and cannot be recomputed.**
2. Even for the 7 bets that fall inside that 3-date window, the
   historical Kalshi TIE price was never captured (this is exactly the
   bug being fixed) — so only the **model-side** correction is
   reproducible historically; the market-side (3-way vig-free)
   correction can only be computed going forward.

Findings over the 62 reproducible F5 evaluations (3 dates,
2026-07-30/31, 2026-08-01):

- Average legacy team-side probability inflation: **9.53 percentage
  points**.
- Average tie probability: **19.06%** — not a rounding artifact, a
  material share of the outcome space.
- Approximate tier changes (using the historical 2-way `kalshiVF` as an
  approximation for the market side, since the true historical tie
  price is unavailable — see caveat in the report itself): 20 of 62 rows
  (32%) would have a different confidence tier; 14 would newly become
  ineligible (tier → none), 6 would newly become eligible (none → tier).
  **This is not a uniform "F5 recommendations decrease" finding** — both
  directions occur, consistent with §2's example.
- Settlement/CLV data inside the reproducible window: 0 settled bets —
  sample size is insufficient for any ROI/CLV comparison (this
  milestone's own calibration convention requires N≥50 per tier before
  drawing a conclusion; this finding is purely descriptive, not
  calibration-threshold-clearing).

All findings above are **descriptive only** — none clears this
project's existing N≥50 calibration threshold for a conclusion to be
actionable.

## 9. Historical data handling decision

**Decision: do not backfill any historical record.** Neither `bets.json`
nor existing `data/edgelab/model_evaluations/*.jsonl` records are
rewritten. Reasons:

- 137 of 144 historical F5 bets have no preserved projection inputs at
  all — backfilling them would mean fabricating inputs that were never
  recorded, explicitly prohibited by this milestone.
- The remaining 7 lack the historical tie price, so even a "best effort"
  backfill would only be a half-correction (model side only) that could
  be mistaken for the real, complete fix if written back as if it were
  one.

**How to distinguish going forward:** every `F5_ML_Away`/`F5_ML_Home`
`marketLedger` row now carries `f5PricingVersion` (currently always
`"f5_three_way_v1"` — no production code path produces the legacy value
any more). `lib/edgelab/model_evaluation.py` copies this into
`ModelEvaluation.modelVersion` for F5 rows specifically (every other
market family's `modelVersion` stays `None`, unchanged). A historical
`ModelEvaluation` with `modelVersion: None` for an F5 row therefore
unambiguously predates this fix — combined with its own `createdAt`/
`modelCommitSha`/`pipelineRunId` provenance fields (already existing
infrastructure, unmodified), there is no ambiguity for a future
calibration report.

## 10. Expected impact on recommendation counts

Going forward, F5_ML_Away/F5_ML_Home edge and tier eligibility will
shift per-game in either direction (§2, §8) as the systematic two-way
inflation is removed and the market-side comparison now reflects the
real three-way vig. No thresholds were changed to compensate — a lower
(or higher) F5 recommendation count is the intended, correct consequence
of pricing the market accurately, not something to offset.

## 11. Safety gates

`scripts/build_market_ledger.py`'s `F5PricingError` + `validate_f5_three_way()`
fail loudly (routing the row to `Missing Data`, never a silent two-way
fallback) when:
- away + tie + home does not sum to 1 within `1e-6` tolerance,
- any probability is outside `[0, 1]`,
- the tie's Kalshi price is missing while away/home prices are present,
- any two of the three contract tickers are identical.

The "accidentally routed through two-way normalization" failure mode is
additionally eliminated **structurally**: the F5 evaluation block no
longer calls `vig_free_2way()` or the `p_win / (1 - p_push)` pattern at
all (verified by
`tests/test_f5_three_way_pricing.py::TestNoProductionFallbackToLegacyF5Math`).

## 12. Python/JavaScript parity

`api/slate.js`'s only *live* F5-related code (`evalF5`) is a separate,
Pinnacle-priced heuristic model that never computed a tie probability to
begin with — not a Poisson twin of the Python engine, so there is no
existing "second implementation of the same bug" to fix there.
`projectF5Runs()`/`gameProbs()` (the file's genuine Poisson engine) is
full-game-only (extra-inning blend, 72% win cap) and dead code for F5
(never called). Rather than force a parity requirement onto code paths
that don't actually exist, this milestone adds pure, additive,
module-level `threeWayResultProbs()`/`vigFree3Way()` functions to
`api/slate.js` (before the untouched `handler` function) that mirror the
Python engine exactly. `tests/test_f5_python_js_parity.py` proves
bit-for-bit-comparable output (`1e-9` tolerance) via real Node.js
subprocess invocation across 6 three-way fixtures and 4 vig-free
fixtures — so a future phase that wires F5 pricing into the JS/API path
inherits already-verified-correct, drift-free math.

## 13. Documentation updated by this milestone

- `MODEL_CORE.md` Step 7 (F5 Probability): now states the three-way
  treatment explicitly instead of the ambiguous "F5 win probability."
- `docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md`'s F5 winner row: updated
  from "Tie leg: MODELED_NOT_EXPOSED" to "PRICED_INFORMATIONAL_NOT_RECOMMENDABLE."
- This document (new).
