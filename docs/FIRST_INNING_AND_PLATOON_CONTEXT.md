# First-inning and confirmed-lineup platoon context (Baseball Input Data / Platoon Context mission)

Data/projection-input improvement only — this does **not** add any new
betting gate or restriction, does not change settlement, canonical bet
history, research-query logic, staking, correlation logic, or
recommendation eligibility. The projection-board philosophy is
unchanged: the model supplies data/projections; manual analysis remains
the final betting decision-maker.

## Prior gaps

**First-inning (NRFI/YRFI).** `pitcherSavant.firstInningSplit`
(dedicated Baseball Savant first-inning xERA, `hfInn=1`) was fully
implemented in `api/savant.js` but only ever fetched for pitchers
flagged as openers (Rule 24's opener-qualification gate,
`avgIPperStart < 3.0`) — every normal starter's `firstInningSplit` was
left `null`. Even when present, `scripts/build_market_ledger.py` only
read it to enforce Rule 40's PAPER-cap gate; the NRFI/YRFI lambda itself
was always `awayProjRuns / 9` / `homeProjRuns / 9` — a full-game-derived
proxy, never first-inning-specific. Confirmed by direct inspection: no
`data/slates/*/authoritative.json` snapshot on disk has a non-null
`firstInningSplit` on a non-opener start.

**Platoon/handedness.** `pitcherSavant.vsLHH`/`vsRHH` (starter platoon
splits) were fetched by `api/slate.js` for every starter, but Baseball
Savant's CSV export appears to reject/return-empty for automated
requests in production (`api/savant.js`'s own fallback message: "Baseball
Savant may be blocking automated requests") — every committed snapshot
has these null regardless. Independently, `scripts/fetch_lineups.py`
resolved the MLB Stats API boxscore's confirmed batting order into a
single aggregate team-level wOBA delta and discarded the individual
batters — no handedness, no per-batter identity, no batting-order
position ever reached `data/slate.json`. Starter throwing hand
(`pitchHand`) was never captured at all.

## What was added

- `api/pitchers.js` / `api/slate.js`: capture `pitcher.pitchHand`
  ('L'/'R') from the same MLB Stats API `probablePitcher` object already
  hydrated — no extra request.
- `api/slate.js`: `firstInningSplit` is now fetched for every confirmed
  starter (not just openers). Rule 24's own opener-only fields
  (`openerRole`/`avgIPperStart`/`openerQualified`) are unchanged.
- `scripts/fetch_lineups.py`: captures each confirmed batter's
  `batSide`, order, and season wOBA into a new `confirmedLineup` list
  (plus a `lineupHandedness` composition summary), instead of discarding
  them after the aggregate wOBA delta is computed. Existing
  `lineupWOBADelta`/`lineupAdj`/`lineupConfirmed*` fields are untouched.
- `api/enrich.js` (`type=batterplatoon`) + `scripts/fetch_batter_platoon_splits.py`
  (new pipeline step, runs after `fetch_lineups.py`): per-batter
  wOBA/K%/BB%/ISO vs LHP and vs RHP via MLB Stats API `sitCodes=vl`/`vr`
  hitting splits (same host/vendor as everything else in this file) for
  every batter in a confirmed lineup, merged into that batter's
  `platoonSplits` field.
- `api/savant.js`'s starter platoon-split fetch also now requests
  hard-hit%/barrel% per split (quality-of-contact/power indicator).
- `lib/research/platoon_context.py` (new): reusable, pure
  confirmed-lineup-vs-opposing-starter engine. Computes a bounded
  (±0.15 R/G) `aggregatePlatoonAdvantageRPG` from two components —
  the lineup's top-3-weighted wOBA vs the opposing starter's hand
  (shrunk to season wOBA below a 40-PA floor) and the starter's own
  xERA split weighted by the lineup's actual handedness mix (min 20 PA,
  matching the existing Savant fetch's own floor) — plus a full
  handedness-composition/availability debug block. Returns
  `LINEUP_UNCONFIRMED`/`MISSING_DATA` honestly, never guesses
  player-level context for an unconfirmed lineup.
- `lib/research/first_inning_context.py` (new): blends dedicated
  first-inning pitcher evidence into the NRFI/YRFI lambda (weight scaled
  by appearance count: 0.30 at 5-7 appearances, 0.55 at 8+, 0 below 5),
  bounded to ±35% of the naive proxy, plus a small (±15%-of-lambda,
  separately capped) nudge from the shared platoon context above. Falls
  back to the exact pre-existing naive proxy, byte-for-byte, whenever
  dedicated evidence is unavailable.

## Wiring

`scripts/build_market_ledger.py`'s `compute_projections(g)` now folds
`build_offense_platoon_context(g, side)`'s bounded RPG adjustment
directly into `awayProjRuns`/`homeProjRuns` (and, scaled to the 5/9
share, `f5AwayProj`/`f5HomeProj`) — so ML, F5, game totals, and team
totals all pick up the platoon signal automatically, without any
per-market code change. `compute_game_projection_context(g)` exposes
`awayPlatoonContext`/`homePlatoonContext` (debug, spread onto every
market row) and `firstInningContext` (attached only to the NRFI/YRFI
rows) so manual analysis can see exactly which inputs were available,
which were missing, which generic fallbacks were used, and whether
dedicated first-inning evidence was applied.

First-inning-only evidence (`firstInningSplit`) never touches
`awayProjRuns`/`homeProjRuns`/F5 — the only channel by which it could
reach those markets is the platoon context, which both layers
explicitly share. See `tests/test_platoon_first_inning_ledger_integration.py`
for the regression proof.

## Sample-size floors

| Signal | Floor | Fallback |
|---|---|---|
| Hitter platoon split (wOBA/K%/BB%/ISO vs one hand) | 40 PA | shrink to hitter's season wOBA |
| Starter platoon split (xERA vs one hand) | 20 PA (matches the existing Savant fetch's own `min_pas=20`) | skip that hand's contribution |
| Confirmed lineup completeness | 6/9 resolved batters (matches `fetch_lineups.py`'s own `MIN_BATTERS_FOR_CONFIRMED`) | skip hitter-side platoon component |
| First-inning pitcher evidence | 5 appearances (matches the existing `openerQualified` floor); 8+ for the full blend weight | naive `proj/9` proxy |

## Regression guarantees

- A game with none of the new fields projects **identically** to before
  this mission (see `TestRegressionNoNewDataIsUnchanged`).
- An unconfirmed lineup never computes player-level platoon context
  (`LINEUP_UNCONFIRMED`, zero adjustment).
- `scripts/validate_platoon_first_inning_wiring.py` (one-off, not part
  of any workflow) demonstrates all of the above against three real
  games from a committed `data/slates/2026-08-10/authoritative.json`
  snapshot.
