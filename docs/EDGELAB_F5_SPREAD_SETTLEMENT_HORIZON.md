# KXMLBF5SPREAD was settled on the full-game margin

**Status: production defect fix. Corrects settlement horizon routing.
Does NOT rewrite any historical ledger row or stored settlement.**

## The defect

`lib/edgelab/settlement.py::settle_market` selected the score to grade
against by **family**, not by **horizon**. Only `inning_total` read
`game_outcome["periodScores"][horizon]`; every other score-based family
fell into an `else` branch that reads the **full-game** `awayRuns` /
`homeRuns`.

`KXMLBF5SPREAD` is `marketFamily=winning_margin`, `marketHorizon=F5`. It
therefore took the `else` branch and was graded on the **full-game
margin**, silently ignoring its own F5 horizon.

## Evidence

From the archived settlement store (MLB-ALPHA-0001 discovery):

- **1512 / 1512** archived `KXMLBF5SPREAD` settlements are identical to
  the same-rung **full-game** `KXMLBSPREAD` result. Not "usually" —
  every single one.
- **88** of them are logically impossible against this repo's own
  (correct) `KXMLBF5` winner settlements: the F5 spread says a team won
  the first five innings by 2+ runs while the F5 moneyline says that team
  did not win the first five innings at all.
- Full-game `KXMLBSPREAD` vs `KXMLBGAME` shows **0** such contradictions,
  isolating the fault to the F5-horizon path rather than to spread
  grading generally.

The research programme excluded the family outright rather than trusting
it (`build_entry_rows.py` drops all 1560 of its settlements).

## What changed

`lib/edgelab/settlement.py`:

1. New `_PERIOD_HORIZONS = {"F3", "F5", "F7"}`.
2. Any score-based market whose horizon is a period horizon is graded
   from `periodScores[horizon]`. If that period score is missing, the
   market is `SETTLEMENT_UNRESOLVED` — **the full-game score is never
   substituted for a missing period score**, which is the specific
   behaviour that produced 1512 wrong settlements.
3. Fixed a latent trap while here: `settle_market`'s legacy
   `horizon = market.get("marketHorizon") or "F5"` default meant a
   horizon-less `game_total` / `team_total` / `winning_margin` would now
   have been read as an F5 contract. Period routing for those families
   keys off the **explicit** `marketHorizon` only, so an absent horizon
   still means full game. `inning_total`'s documented F5 default is
   preserved unchanged.

## Prefer UNRESOLVED over a fabricated result

Where a trustworthy F5 score is not available in the settlement runtime,
the market now stays `SETTLEMENT_UNRESOLVED` / pending. That is the
intended outcome: an honest "not yet known" is strictly better than a
confident full-game answer to an F5 question.

The upstream feed already supports this — `scripts/edgelab/settle_markets.py`
builds `periodScores` for F3/F5/F7 from the MLB Stats API linescore via
`extract_period_score_from_linescore`, so F5 spreads settle correctly as
soon as the linescore is available; they simply were not consulting it.

## Historical rows

**Not rewritten here.** Re-settling the affected historical rows is a
separate, explicitly authorized operational action.

## Tests

- `test_f5_winning_margin_settles_on_f5_period_not_full_game` — a game
  won by 4 overall but lost through five innings settles NO on the F5
  spread and YES on the full-game spread.
- `test_f5_winning_margin_without_period_score_is_unresolved_never_full_game`
- `test_absent_horizon_still_means_full_game_for_score_based_families`
