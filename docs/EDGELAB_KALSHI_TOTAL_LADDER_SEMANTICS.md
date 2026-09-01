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

4. **Full-game ladder verification.** The same external check was run for
   `KXMLBTOTAL` across 310 games on the research dates. The archived
   engine matches `> N` on 3518/3518 rungs and diverges from `>= N` on
   exactly the **271 boundary rungs** where `final total == N` — the
   divergence is entirely and only at the boundary, as the defect
   predicts. Artifact:
   `data/edgelab/research_artifacts/mlb_alpha_0001/game_total_semantics_verification.json`.

## Honest limit of this evidence, and a capture gap worth fixing

Kalshi's **own** settlement result is not archived anywhere in this
repository: `api/kalshisearch.js` and the registry snapshots only ever
fetch open markets, and every one of the 686,220 archived raw market
records carries `status="active"`. There is therefore no settlement
receipt to point at, and the conclusion above rests on three convergent
lines of evidence rather than on Kalshi's own recorded outcome:

- **market pricing at the boundary** — a contract whose realized total
  equals `N` was quoted at 97-99¢ late in its game in 13 discovery cases
  (and 36 of the 39 boundary rungs with a decisive late quote priced YES;
  the 3 that priced NO are provably mid-game captures taken *before* the
  total reached `N`, at 0.65h / 1.76h / 2.24h after first pitch). Under
  a strict `> N` rule those contracts are already worthless and could not
  trade near 99¢;
- **arithmetic** — the external verification above localises every
  archived divergence to exactly the boundary;
- **internal consistency** — the repo's own half-point ladders encode
  `N - 0.5`, which *is* `>= N`, for the same underlying contract shape.

**Recommendation (separate change):** capture settled markets, so
Kalshi's authoritative result is archived and a settlement-semantics
defect can be detected directly rather than reconstructed. The absence of
that field is the reason this defect survived as long as it did.

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
| `lib/kalshi_probability_adapters.py` | `adapt_total` (the per-rung Kalshi ladder pricer, used by `adapt_contract` for `game_total` and `inning_total` F3/F5/F7) uses `p_over_total(proj, line - 1)` |

`adapt_team_total` is deliberately **left alone**: its line arrives already
as the half-point `N - 0.5`, so `p_over_total(proj, N - 0.5)` is *already*
`P(runs >= N)`. Applying the correction there would double-count it. A test
pins both behaviours.

### `api/slate.js` is deliberately NOT changed

An earlier revision of this fix also applied the `- 1` correction to
`api/slate.js`'s `evalGameTotal`. **That was wrong and has been reverted.**
`evalGameTotal` does not price a Kalshi contract at all: it reads
`bookOdds.pinnacle.total.point` and compares the model against Pinnacle's
vig-free over/under. A sportsbook total is a different instrument — it has a
genuine three-way outcome (over / under / **push** on an exact integer
line), which is why that code subtracts `poissonPMF(round(line))` and
computes a `pPush`. Kalshi's rung has no push. `api/slate.js` reads no
Kalshi total data anywhere (verified by search), so the correction had no
business there.

**Separate pre-existing defect, reported not fixed here:** that same
expression mis-handles *half-point* sportsbook lines. For `vegasLine = 8.5`,
`pOver = P(runs >= 9)` is right, but `pUnder` then subtracts
`poissonPMF(round(8.5)) = PMF(9)` a second time, understating the under side
and reporting a phantom `pPush` on a line that can never push. It is correct
only for integer lines. This is Pinnacle-comparison math, unrelated to
Kalshi ladder semantics, and the TOTAL market is Rule-71 paper-only, so it
is left for a separately scoped change rather than bundled into a Kalshi
settlement-semantics PR.

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
- `tests/test_kalshi_probability_adapters.py::TestTotalsYesNo::test_matches_production_p_over_total_with_the_at_least_n_correction`
  (previously asserted the adapter passed the rung through unadjusted)
- `tests/test_kalshi_total_ladder_probability_semantics.py` — a 200-cell
  adversarial grid over projections x integer rungs proving, in every
  Kalshi-facing engine, `YES = P(X >= N)`, `NO = P(X < N)`,
  `YES + NO = 1` (no push rung), monotonicity in the rung, tail behaviour
  at extreme means, and that `PMF(N)` lands on the YES side. It also pins
  the shared primitive's `max_r=30` tail truncation as the *only* deviation
  from the closed form (~4e-8 at a realistic 9.7-run projection, i.e. far
  below a one-cent tick).
