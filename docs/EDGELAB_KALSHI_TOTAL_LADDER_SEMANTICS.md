# Kalshi MLB total-ladder settlement semantics: "N or more", not "over N"

**Status: production defect fix. Changes probability AND settlement
semantics for `KXMLBTOTAL` / `KXMLBF5TOTAL`. Does NOT rewrite any
historical ledger row or stored settlement.**

## The defect in one sentence

Kalshi's integer-rung total contract `…-N` pays YES iff the combined
total is **N or more** (`>= N`), but this repository priced and settled
it as **strictly over N** (`> N`) — systematically undervaluing the YES
side of every integer-rung total contract by exactly `P(total == N)`.

## Evidence (external ground truth, not inference)

Found during MLB-ALPHA-0001 discovery and then verified against the MLB
Stats API rather than assumed:

1. **Quote evidence.** 13 archived contracts whose final in-game quote
   was ≥ 97¢ (the market pricing them as near-certain YES) were settled
   `NO` by this repo. All 13 sat exactly at `total == N`.
2. **Cross-family evidence.** Team-total ladders store `N − 0.5` and pin
   each team's exact run count; summing them reproduced the game total on
   163/163 events, and the archived game-total boundary rung was `NO`
   every time the true total equalled the rung.
3. **External verification (decisive).** For all 270 games underlying
   research candidate C01, first-five-innings scoring was re-fetched from
   `statsapi.mlb.com` and summed directly from the raw innings array.
   356/356 contracts matched the `>= N` rule. The archived `> N` rule
   disagreed on 10 contracts — **every one of them exactly at
   `totalF5 == N`**, and the true Kalshi outcome was YES in all 10.
   Artifact:
   `data/edgelab/research_artifacts/mlb_alpha_0001/f5_settlement_verification.json`.

The repo already knew this convention for the half-point ladders:
`scripts/build_kalshi_registry.py` documents `over_n=4` as "scores over
3.5", and `scripts/build_market_ledger.py`'s team-total block already
applies a `− 1` correction for exactly this reason. The integer ladders
store the rung as `N` instead of `N − 0.5`, and that difference was
mistaken for a different *contract* rather than a different *encoding*.

## What changed

| File | Change |
|---|---|
| `lib/edgelab/settlement.py` | `game_total` / `inning_total` settle `(away + home) >= threshold` |
| `scripts/build_market_ledger.py` | `Game_Total` model probability uses `p_over_total(total_proj, tot_line - 1)` = `P(total >= N)` |
| `api/slate.js` | `evalGameTotal`'s `pOver` uses `totalProb(totalProj, vegasLine - 1)`, mirroring the Python fix |

## Blast radius

The `>` → `>=` change alters a result **only** when the threshold is an
integer exactly equal to the realized total. The half-point families
(`team_total`, `winning_margin`, which store `N − 0.5`) are provably
unaffected — no integer run count equals a half-point line — and a
regression test now pins that.

Probability impact is one-sided and material: the YES side gains exactly
`P(total == N)` of probability mass, which for a typical MLB game total
is several percentage points. `Game_Total` is currently Rule-71 suspended
(paper only) in `build_market_ledger.py`, so real-money exposure to the
probability half of this defect is limited today; the settlement half
affects every archived integer-rung total contract.

## Historical rows

**Not rewritten here.** This change corrects behaviour going forward.
Re-settling affected historical rows is a separate, explicitly authorized
operational action (it requires re-running `scripts/edgelab/settle_markets.py`
for the affected tickers). The research programme carries its own
read-only corrected mapping at
`data/edgelab/research_artifacts/mlb_alpha_0001/corrected_total_settlements.json`
(564 archived settlements flip under the corrected rule).

## Tests

- `tests/edgelab/test_settlement.py::test_inning_total_exact_threshold_is_yes_at_least_n_never_a_push`
  (previously asserted `NO`; corrected with the evidence cited)
- `tests/edgelab/test_settlement.py::test_game_total_exact_integer_threshold_is_yes_at_least_n`
- `tests/edgelab/test_settlement.py::test_half_point_families_are_unaffected_by_the_ge_correction`
- `tests/test_fire_fixes.py::TestPOverTotalSemantics::test_game_total_call_uses_tot_line_minus_one`
  (previously asserted the unadjusted call was correct)
