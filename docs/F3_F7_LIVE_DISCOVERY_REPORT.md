# F3_F7_LIVE_DISCOVERY_REPORT.md

Spread/F3-F7-correction mission, Part 2 -- genuine, prefix-agnostic
live and historical investigation into Kalshi's real F3 (first 3
innings) and F7 (first 7 innings) MLB markets, following up on
`docs/research/INNING_RESULT_MIGRATION.md`'s prior finding that F3/F7
existence is user-confirmed but this repository has never
independently verified their ticker structure, outcome shape, or
settlement rules.

**"No F3 or F7 markets are available today" is never treated as proof
they do not exist.** Every conclusion below is scoped to the exact
evidence source it came from.

## 1. Status framework used throughout this report

| Status | Meaning |
|---|---|
| `NOT_LIVE_TODAY` | A genuine, broad, prefix-agnostic live query ran and found no F3/F7 contract for the date queried -- says nothing about other dates. |
| `HISTORICALLY_VERIFIED` | A real, independently-inspectable F3/F7 payload (ticker, title, price) was found in this repository's own archives or a live closed-market query. |
| `DISCOVERY_SUPPORTED` | This repository's discovery pipeline can find and retain the contract the moment it is live (regardless of whether one is live right now). |
| `PARSER_SUPPORTED` | `lib.kalshi_mlb_contract_parser`/`lib.kalshi_mlb_market_classifier` can correctly parse and classify the contract. |
| `PROBABILITY_SUPPORTED` | A fair probability can be computed for the contract. |
| `NOT_FOUND_AFTER_EXHAUSTIVE_SEARCH` | Every search path attempted (see section 2) returned nothing, for the specific window searched. |

## 2. Search paths attempted

1. **Repository historical evidence** -- `bets.json` (512 wagers) and
   `BET_LOG.md` searched for "F3", "first 3", "through 3", "first
   three", "F7", "first 7", "through 7", "first seven" (case
   insensitive). **Result: zero matches.** This means no F3/F7 wager
   was ever logged with enough textual detail in THIS repository's own
   records to independently corroborate the user's direct account --
   it does not contradict the user's account (a real wager placed
   directly in the Kalshi UI would never be recorded here unless
   manually logged with that detail).
2. **Git history** -- `git log --all` searched for commit messages
   and content mentioning F3/F7 tickers. The only hits are this
   repository's own prior investigation commits (`ef50188`, `3da02d5`)
   documenting the ABSENCE of independent verification, not a real
   ticker.
3. **Existing archived Kalshi snapshots** -- 355+ files under
   `data/kalshi_registry_snapshots/` and
   `data/research/inning_result_snapshots/` re-checked. All are fed
   exclusively by the 8-series allowlist (`api/kalshisearch.js`'s
   `ALL_SERIES`) -- structurally incapable of ever containing an F3/F7
   ticker, confirmed in `docs/research/INNING_RESULT_MIGRATION.md`.
4. **Real broad, open-status, prefix-agnostic discovery** --
   `api/kalshisearch.js`'s existing `discoveredUnknownSeriesMarkets`
   pass (added in Model Performance Phase 2A) queries
   `/markets?status=open&limit=1000` with NO series filter, then
   retains anything outside `ALL_SERIES`. Checked against every commit
   of `data/kalshi_search.json` in this repository's history that
   post-dates that pass's introduction:

   | Commit | Date | `discoveredUnknownSeriesCount` |
   |---|---|---|
   | `c70ef3b` | 2026-07-30 | 0 |
   | `78baf6f` | 2026-07-30 | 0 |

   **Result: `NOT_LIVE_TODAY` for 2026-07-30 (open markets only)** --
   every real, live, unfiltered query this repository has ever run
   found zero MLB-associated markets outside the known 8 series on
   that date.
5. **Exchange series catalogue + recently-closed markets** (NEW this
   mission) -- `scripts/discover_kalshi_series_catalogue.py` queries
   `GET /series` (unfiltered, not limited to a pre-known prefix),
   retains every MLB-associated series found (ticker prefix, title, or
   F3/F7 horizon language), then queries BOTH `status=open` and
   `status=closed` for each one -- covering "recently closed markets"
   and "prior slate dates" that pass 4 above never queried. This
   sandbox has no network egress to `api.elections.kalshi.com`
   (independently re-confirmed by the prior Phase 2A mission), so this
   script's real output can only come from a GitHub Actions run. **See
   `data/kalshi/discovery/<dispatch-date>_series_catalogue.json` and
   `<dispatch-date>_f3_f7_search.json` for the live result of the
   post-merge workflow dispatch (Part 7 of this mission's final
   report).**

## 3. Conclusion as of this mission

| Question | Answer |
|---|---|
| Does F3 exist on Kalshi? | `EXISTS_ON_KALSHI_USER_CONFIRMED` (unchanged from Phase 2A -- this mission found no NEW independent live confirmation, but also found no evidence contradicting it) |
| Does F7 exist on Kalshi? | Same as F3 |
| Is F3/F7 live right now (2026-07-30/31)? | `NOT_LIVE_TODAY`, per the real broad-pass evidence in section 2.4 |
| Can this repository DISCOVER an F3/F7 contract the moment one goes live? | `DISCOVERY_SUPPORTED` -- yes. `scripts/discover_kalshi_mlb_markets.py` already merges `discoveredUnknownSeriesMarkets` (prefix-agnostic) into its input, and `scripts/discover_kalshi_series_catalogue.py` (new) adds a genuine series-catalogue + closed-market pass. |
| Can this repository CLASSIFY an F3/F7 contract once discovered? | `PARSER_SUPPORTED` for winner, spread, AND (as of this mission) total markets -- `lib.research.market_taxonomy.classify_market()`'s title-fallback now recognizes winner-shaped, spread-shaped, AND total-shaped F3/F7 text (previously only winner-shaped). |
| Can this repository PRICE an F3/F7 SPREAD or TOTAL contract? | `PROBABILITY_SUPPORTED` -- `lib.kalshi_period_projections.compute_period_projection()` (new) generalizes production's F5 projection formula to any inning boundary; `lib.kalshi_probability_adapters.adapt_contract()` prices any F3/F7 spread/total the same way it prices F5's. |
| Can this repository PRICE an F3/F7 WINNER contract? | **No -- deliberately not.** Outcome structure (two-way vs three-way, tie handling) is still `UNVERIFIED`. `lib.kalshi_probability_adapters._VERIFIED_THREE_WAY_PERIODS` gates this on `HORIZON_MARKET_STATUS`'s `outcomeStructureStatus`, which stays `UNVERIFIED` for F3/F7 until a real payload is inspected -- pricing a winner contract of unknown shape would fabricate a probability, which the mission forbids. |
| Can an F3/F7 winner contract be SETTLED? | `lib.research.inning_result_settlement.settle_inning_result()` is now parametric on the same verified-structure flag -- it will settle F3/F7 automatically the moment a future phase flips that flag, using the already-generalized `extract_period_score_from_linescore()` (reuses `lib/f5_settlement.py`'s exact inning-sum logic, parametrized). No code change will be required in that function when the day comes. |
| Will a newly-introduced unknown MLB series be silently ignored? | No -- `scripts/discover_kalshi_series_catalogue.py`'s catalogue pass retains every MLB-associated series it finds, known or not, with an explicit inclusion reason; `scripts/discover_kalshi_mlb_markets.py` already retains unclassified/unsupported contracts rather than dropping them. |

## 4. What would change the F3/F7 conclusion

The moment a live F3 or F7 payload is captured (via the new series
catalogue pass, a future slate date, or the user supplying a raw
example), the correct next step is: inspect its real ticker/event
ticker structure, its outcome legs (2 vs 3 tickers per event), and
whether a `-TIE`-equivalent suffix exists -- exactly the process
already documented for F5 in `docs/research/KALSHI_MARKET_TAXONOMY.md`
-- then flip `HORIZON_MARKET_STATUS[scope]["outcomeStructureStatus"]`
to `CONFIRMED_TWO_WAY` or `CONFIRMED_THREE_WAY` accordingly. Winner
pricing and settlement activate automatically from that one flag
change (see the gating in section 3 above) -- no other code change is
required.
