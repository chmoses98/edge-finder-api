# Kalshi MLB Market Coverage Audit

Date: 2026-07-31
Scope: exactly what the current production pipeline discovers, classifies,
models, exposes, and can recommend/settle for Kalshi MLB markets, versus what
it does not — read from the actual code, not assumed.

**CORRECTION (Kalshi price-checker correction mission, later the same day):**
sections 4-5 below claim pitcher strikeouts/outs and hitter props "never
observed" / "never been observed to exist on Kalshi MLB." That claim is now
retracted -- a live series-catalogue dispatch confirmed KXMLBKS, KXMLBOUTS,
KXMLBHIT, KXMLBTB, KXMLBHRR, KXMLBRBI, and KXMLBSB as real series (see
`data/kalshi/discovery/2026-07-30_series_catalogue.json` and
`docs/KALSHI_PRICE_CHECKER_STRICT_REGISTRY.md`), the same way this repo's
F3/F7 claim was corrected earlier that day. What remains true and unchanged:
no probability distribution is modeled for any of these families yet (still
`UNSUPPORTED`, never a fabricated probability), and pitcher hits-allowed /
earned-runs-allowed genuinely remain unobserved.

This audit is the required Phase 1 deliverable that precedes the Phase 2
universal market engine. Every claim below is sourced to a specific file;
where evidence is a real, previously-fetched Kalshi snapshot rather than a
live query (this sandboxed environment has no outbound network access to
Kalshi), that is stated explicitly.

## 1. How a market reaches the slate today (pipeline trace)

```
scripts/fetch_kalshi_markets.py  ─┐  hardcoded SERIES_TICKER='KXMLBGAME' only
api/kalshisearch.js (Vercel)     ─┼─→ data/kalshi_search.json (broad, unfiltered
                                  │    /markets?status=open&limit=1000 pass —
                                  │    the ONLY prefix-agnostic fetch in the repo)
scripts/build_kalshi_registry.py ─┘  queries exactly 8 hardcoded series tickers
        ↓ (SERIES_CATALOGUE, 8 known series; backfills gaps from kalshi_search.json)
data/kalshi_market_registry.json  (per-game: moneyline, spread, total,
                                    team_total_{away,home}, f5_moneyline,
                                    f5_spread, f5_total, rfi — each with a
                                    full `lines` ladder + a `best_line`)
        ↓
scripts/merge_odds.py              (writes game.odds.kalshi.{ml,rl,total,
                                     team_totals,f5ml,f5_spread,f5_total,
                                     nrfi_yrfi} — EACH ladder-type block
                                     retains the FULL `all_lines` array,
                                     not just best_line)
        ↓
scripts/enrich_data.py             (team/pitcher stat enrichment feeding
                                     projections; does NOT compute modelProb)
        ↓
api/slate.js (Vercel, Poisson engine #1) → modelProb, mlEdge, runLineEval,
                                     totalEval, teamTotals, nrfi, f5, allEdges
        ↓
scripts/build_market_ledger.py (Poisson engine #2, the one that actually
                                 gates real money) → game.marketLedger[]
                                 ONE row per REQUIRED_MARKETS entry, ALWAYS
                                 using best_line only, never all_lines
        ↓
scripts/risk_gate.py / write_pending_bets.py → bets.json (no market-type
                                 whitelist here — everything upstream of
                                 REQUIRED_MARKETS is the real gate)
        ↓
scripts/capture_pregame_closing_lines.py / clv_from_snapshot.py → CLV
        ↓
scripts/log_manual_bet.py → manual entries into the same bets.json ledger
```

**Key finding: two independent discovery/classification bottlenecks, not one.**
1. `scripts/build_kalshi_registry.py`'s `SERIES_CATALOGUE` dict is a hardcoded
   allowlist of exactly 8 series tickers. A series outside this list is
   *retained* (via `lib/kalshi_discovery.py`'s `discover_unknown_series()`,
   fed only by `api/kalshisearch.js`'s broad pass) into
   `discoveredUnknownSeries`/`discoveredUnknownSeriesCount` in the registry
   JSON — but **nothing downstream ever reads those two fields**. Discovery
   of an unknown series is retention-only; it activates nothing.
2. `scripts/build_market_ledger.py`'s `REQUIRED_MARKETS` list (11 fixed
   strings) is the actual real-money gate — it is architecturally impossible
   for a market outside this list to ever get a `marketLedger` row, and the
   function force-fills any of the 11 that's missing data with a synthetic
   `Evaluation Failed` row, so nothing on the list is ever silently dropped
   either — but the list itself cannot grow without a code change.

## 2. Per-market-family status table

Series/ticker examples below are drawn from a real archived snapshot,
`data/kalshi_registry_snapshots/kalshi_search_2026-07-29_0803.json` (720
markets), cross-referenced against `data/research/kalshi_mlb_market_inventory.json`
(a prior research audit built from the same and other archived snapshots) and
`docs/research/KALSHI_MARKET_TAXONOMY.md`.

| Market family | Kalshi series | Example ticker | Period | Discovered | Classified | Fair prob. | In slate | Recommendable | CLV-tracked | Settleable | **Status** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Moneyline (ML) | `KXMLBGAME` | `KXMLBGAME-26JUL302140BOSATH-BOS` | Full game | Yes | Yes | Yes (Poisson joint, `p_team_wins`) | Yes | Yes | Yes | Yes | **FULLY_SUPPORTED** |
| F5 winner (incl. Tie) | `KXMLBF5` | `KXMLBF5-26JUL302140BOSATH-BOS` / `-TIE` | First 5 innings | Yes | Yes | Team legs: Yes (F5 Three-Way Pricing Correction milestone: now the genuine, non-renormalized `away+tie+home=1` three-way probability, `lib/research/three_way_projection.py`). Tie leg: Yes, same computation | Team legs: Yes; Tie leg: priced informationally (`row.f5TieContract`, modelFairProbability/marketImpliedProbability/estimatedEdge/expectedValuePerDollar) | Team legs: Yes; Tie: **priced, not recommendable** — deliberately not added to `REQUIRED_MARKETS` (would be a market-selection-philosophy change, out of scope for a pricing correction) | Yes (team legs) | Team legs: Yes | Team legs **FULLY_SUPPORTED** (was **INCORRECTLY_PRICED** pre-fix — see docs/F5_THREE_WAY_PRICING.md — silently renormalized away the tie, overstating both team-side fair probabilities); Tie leg **PRICED_INFORMATIONAL_NOT_RECOMMENDABLE** (was `MODELED_NOT_EXPOSED`) |
| F3 winner | *unconfirmed prefix* | — none ever observed — | First 3 innings | **No** — no fetcher ever queries an F3 series ticker | No | No | No | No | No | No | **UNSUPPORTED** — existence confirmed by direct user report of real Kalshi wagers; this repo's own snapshots/archives have never contained one because every fetcher (`api/kalshisearch.js` `ALL_SERIES`, `build_kalshi_registry.py` `SERIES_CATALOGUE`, `fetch_kalshi_markets.py`'s single `SERIES_TICKER`) is a fixed allowlist that omits it. Ticker prefix and outcome structure (2-way vs. 3-way) are both **UNVERIFIED**. |
| F5 spread + alternates | `KXMLBF5SPREAD` | `KXMLBF5SPREAD-...-BOS2` (win by >1.5) | F5 | Yes (all lines) | Yes | Yes (per-line, but only best-line ever computed) | Ladder present (`odds.kalshi.f5_spread.all_lines`), best-line only actually evaluated | **No** — not in `REQUIRED_MARKETS` at all | No | Would be, if evaluated | **DISCOVERED_NOT_MODELED** — the alternate-line ladder already exists in `merge_odds.py` output and is completely unused by `build_market_ledger.py` |
| Full-game spread (run line) + alternates | `KXMLBSPREAD` | `KXMLBSPREAD-...-BOS2` | Full game | Yes (all lines) | Yes | Yes, best-line only (`RL_Away`/`RL_Home`) | Ladder present, best-line only evaluated | **No** — Rule 81 unconditionally rejects RL every run (documented WR 36%, CLV −4.09%, paper-only) | Yes (paper) | Yes | Best line: **MODELED_NOT_EXPOSED** (evaluated then force-rejected by Rule 81); alternates: **DISCOVERED_NOT_MODELED** |
| F5 total + alternates | `KXMLBF5TOTAL` | `KXMLBF5TOTAL-...-6` | F5 | Yes (all lines) | Yes | **No** — not in `REQUIRED_MARKETS` | Ladder present, none evaluated | No | No | Would be, if evaluated | **DISCOVERED_NOT_MODELED** |
| Full-game total + alternates | `KXMLBTOTAL` | `KXMLBTOTAL-...-10` | Full game | Yes (all lines) | Yes | Yes, best-line only (`Game_Total`) | Ladder present, best-line only evaluated | Rule 71 caps it paper-only (WR 41%, CLV −1.43%) | Yes (paper) | Yes | Best line: **MODELED_NOT_EXPOSED**; alternates: **DISCOVERED_NOT_MODELED** |
| Team total + alternates | `KXMLBTEAMTOTAL` | `KXMLBTEAMTOTAL-...-BOS4` | Full game | Yes (all lines) | Yes | Yes, best-line only, Over side only (`TT_Away_Over`/`TT_Home_Over`) | Ladder present, best-line-over only evaluated | Yes | Yes | Yes | Best line/Over: **FULLY_SUPPORTED**; Under side + alternates: **DISCOVERED_NOT_MODELED** |
| NRFI/YRFI | `KXMLBRFI` | `KXMLBRFI-26JUL302140BOSATH` | 1st inning | Yes | Yes | Yes (4-factor composite; naive 1st-inning Poisson) | Yes | Yes | Yes | Yes | **FULLY_SUPPORTED** |
| Exact/range-based team scoring | — | — none observed — | — | No | No | No | No | No | No | No | **UNSUPPORTED** — never observed in any archived snapshot; not confirmed to exist as a distinct Kalshi series (team totals already cover "over/under N" scoring) |
| Margin-of-victory | *is* `KXMLBSPREAD`/`KXMLBF5SPREAD` | (see spread rows above) | Full game / F5 | Yes | Yes | Only best line | See spread rows | See spread rows | See spread rows | See spread rows | Same status as the spread rows above — "margin-of-victory" is not a separate Kalshi series, it **is** the winning-margin/spread ladder |
| Race-to-N-runs | — | — none observed — | — | No | No | No | No | No | No | No | **UNSUPPORTED** — never observed in any of 355 archived snapshots or the 720-market inventory; no evidence Kalshi currently offers this for MLB |
| Inning-result/inning-scoring beyond F5/NRFI | — | — none observed — | — | No | No | No | No | No | No | No | **UNSUPPORTED** — F5 (inning_result/F5) and NRFI (first_inning_run/F1) are the only inning-scoped families ever observed; no standalone per-inning scoring market beyond these two exists in any snapshot examined |
| Pitcher strikeouts (+ alternates) | — | — none observed — | Player | No | No | No | No | No | No | No | **UNSUPPORTED** — zero `STRIKEOUT` ticker substrings across all 355 archived snapshot files; `pitcherSavant.kPct` is used only as a scalar input to run-scoring Poisson lambdas, never as a strikeout-count distribution; `lib/research/market_handler_registry.py`'s `pitcher_strikeouts` entry is an explicit `_unimplemented_handler` placeholder |
| Pitcher outs (+ alternates) | — | — none observed — | Player | No | No | No | No | No | No | No | **UNSUPPORTED** — same as strikeouts; no outs/workload probability distribution exists anywhere in the codebase, only scalar `avgIPperStart`/`seasonIP` inputs to the run model |
| Any other Kalshi MLB contract (hitter hits/TB/HR, pitcher hits/ER allowed) | — | — none observed — | Player | No | No | No | No | No | No | No | **UNSUPPORTED** — same evidentiary basis; `lib/research/market_taxonomy.py`'s `FAMILY_HITTER_*`/`FAMILY_PITCHER_HITS_ALLOWED`/`FAMILY_PITCHER_EARNED_RUNS` constants exist as taxonomy scaffolding only, never observed live, never modeled |

## 3. Counts

These counts describe **code capability**, not a specific slate's live
market count (Phase 6 will report live counts from an actual discovery run,
since this sandbox cannot reach Kalshi directly). "Contract" below means one
distinct, tradable Kalshi market ticker.

| Metric | Count | Basis |
|---|---|---|
| MLB market **families** with a confirmed, currently-fetched Kalshi series | 8 (`KXMLBGAME`, `KXMLBF5`, `KXMLBSPREAD`, `KXMLBTOTAL`, `KXMLBTEAMTOTAL`, `KXMLBF5SPREAD`, `KXMLBF5TOTAL`, `KXMLBRFI`) | `scripts/build_kalshi_registry.py` `SERIES_CATALOGUE` |
| Families **classified** (ticker structure understood) | 8 of 8 known, plus F3/F7 classifiable-on-sight via title-fallback in `lib/research/market_taxonomy.py` (not yet wired to production) | `lib/research/market_taxonomy.py` |
| Families with **any** fair-probability calculation | 6 of 8 (ML, F5 ML, RL/spread best-line, Total best-line, Team Total best-line/Over, NRFI/YRFI) | `scripts/build_market_ledger.py` `REQUIRED_MARKETS` |
| Families **exposed in `games[].marketLedger`** | Same 6, always exactly one line each — **never** the alternate-line ladder | `scripts/build_market_ledger.py::evaluate_game()` |
| Families **eligible for recommendation** (not Rule-suspended) | 4 of 8 fully (ML, F5 ML, Team Total Over, NRFI/YRFI); 2 of 8 modeled-but-paper-only (RL/spread via Rule 81, Game Total via Rule 71) | `REQUIRED_MARKETS` + Rule 71/81 gates |
| Families **unsupported** | F3, F7, pitcher strikeouts (+alt), pitcher outs (+alt), pitcher hits/ER allowed, hitter hits/TB/HR, race-to-runs, standalone exact/range team scoring | Confirmed absent from 355 archived snapshots + `lib/research/` taxonomy scaffolding |
| **Alternate lines** discovered but never modeled | Full ladders for spread, F5 spread, total, F5 total, team total (both sides) — all present in `merge_odds.py`'s `all_lines` output today | `scripts/merge_odds.py` |
| Contracts with **parsing errors** | None observed for the 8 known series (`parse_suffix()` in `build_kalshi_registry.py` has a documented 2-letter-abbreviation fix); F3/F7 and any unknown series would currently fail classification only because their series prefix isn't in `SERIES_FAMILY_MAP` — this is a *coverage* gap, not a parsing *bug* | `scripts/build_kalshi_registry.py`, `lib/research/market_taxonomy.py` |

## 4. Direct answers to the explicitly-asked questions

| Market | As well-supported as ML today? |
|---|---|
| F3 winner | **No.** Not discoverable (no fetcher queries it), not classified in production, no probability model wired, not exposed, not recommendable. Existence is real (user-confirmed) but ticker prefix and outcome structure are unverified. |
| F5 winner | **Team legs: yes.** Tie leg: no — fetched but never evaluated or exposed for recommendation. |
| F5 spreads + alternates | **No.** Discovered (full ladder fetched), but zero probability calculated for any F5 spread line — not in `REQUIRED_MARKETS` at all. |
| Full-game spreads + alternates | **No.** Best line only is modeled, and that best line is unconditionally rejected (Rule 81, paper-only). Alternates are fetched but never touched. |
| F5 totals + alternates | **No.** Discovered, never modeled at all (not in `REQUIRED_MARKETS`). |
| Full-game totals + alternates | **No.** Best line only is modeled and paper-capped (Rule 71). Alternates never touched. |
| Team totals + alternates | **Partially.** Best-line Over is fully supported per side. Under side and every alternate line are discovered but never modeled. |
| NRFI/YRFI | **Yes**, as well-supported as ML. |
| Exact/range team scoring | **No.** Never observed to exist as a distinct Kalshi market; nothing to model. |
| Margin-of-victory | **N/A as a distinct market** — this *is* the spread/winning-margin ladder above; same status as spreads. |
| Race-to-runs | **No.** Never observed on Kalshi MLB; no code path for it anywhere. |
| Inning-result/inning-scoring beyond F5/NRFI | **No additional markets observed** — F5 and NRFI already cover every inning-scoped family seen in real snapshot data. |
| Pitcher strikeouts + alternates | **No.** Never observed on Kalshi MLB in any archived data; no distribution exists in code. |
| Pitcher outs + alternates | **No.** Same as strikeouts. |
| Any other MLB contract | Hitter hits/total bases/home runs and pitcher hits/earned-runs-allowed are taxonomy-stubbed research constants only — never observed, never modeled. |

## 5. What this means for Phase 2

- The **highest-value, lowest-risk work** is wiring the alternate-line ladders
  that `merge_odds.py` *already writes* (`all_lines` for spread/total/team
  total/F5 spread/F5 total) into a new evaluator that reuses the exact same
  Poisson math already in `scripts/build_market_ledger.py`
  (`p_team_wins`, `p_over_total`, `compute_game_projection_context`) —
  this requires **no new statistical model**, only evaluating it once per
  line instead of once per market family.
- F3/F7 require a live, prefix-agnostic discovery pass before any modeling
  question is even answerable (ticker prefix and outcome structure are both
  unverified) — Phase 2's discovery step must attempt this, and Phase 6 must
  report the real result rather than assume.
- Pitcher strikeouts, pitcher outs, hitter props, race-to-runs, and standalone
  exact/range team-scoring markets have **no existing distribution to reuse**
  and have never been observed to exist on Kalshi MLB. Per the explicit
  "do not fabricate probabilities" rule, these must be discovered (if they
  exist), classified, and marked `UNSUPPORTED` with a precise reason —
  never assigned an invented probability.

## 6. Phase 2 status update (MLB slate coverage audit, 2026-08-19)

Everything in section 5 above has since been built: `scripts/discover_kalshi_mlb_markets.py`
is the universal engine (prefix-agnostic discovery over `data/kalshi_search.json`'s
broad, unfiltered pass), `lib/kalshi_probability_adapters.py` prices every
line of every alternate ladder plus F3/F5/F7 winner markets (F5/F3 verified
three-way) and pitcher strikeouts/outs (survival-curve joint model), and
`.github/workflows/discover-kalshi-mlb-markets.yml` runs it automatically
after every "Fetch Slate Data" run. Hitter hits/total-bases/RBIs/stolen-bases/
hits+runs+RBIs are CONFIRMED real Kalshi series with **no per-batter
distribution in this codebase** — correctly `UNSUPPORTED`, never faked.

**What was still missing, and what this mission fixed:** the discovery
engine's output (`data/kalshi/discovery/<date>.json`) was a fully-priced,
fully-classified artifact that nothing ever accounted for end-to-end — a
contract whose parsed date didn't match the run's `date_str` was silently
dropped (`counts["discovered"]` overcounted `len(contracts)` with no
record of the gap), there was no closed set of terminal states a reader
could sum to prove nothing was missing, and — because
`scripts/discover_kalshi_mlb_markets.py` runs in a separate, explicitly
betting-logic-isolated workflow (see that workflow's own
`test_never_touches_betting_logic_scripts`) — a human or model following
`RUN_THE_SLATE.md` had no pointer to this artifact at all. `marketLedger`
(11 required markets, unchanged, still the only real-money gate) was
never touched and never will be by this mission.

Fix (additive, isolated to the discovery/audit path — never imports from
or is imported by `build_market_ledger.py`/`risk_gate.py`/
`write_pending_bets.py`/`validate_slate_final.py`):

- `scripts/discover_kalshi_mlb_markets.py`: the date-mismatch drop now
  records an explicit `classificationStatus="different_slate_date"`
  contract instead of a bare `continue`; every contract now also carries
  `gameMatched`/`gameStatus` so downstream accounting can distinguish an
  unmatched game from a started one without guessing from `gameId`'s shape.
- `lib/kalshi_market_coverage.py` (new): `classify_terminal_state()` maps
  every contract to exactly one of `FULLY_EVALUATED`,
  `MISSING_REQUIRED_CONTEXT`, `UNSUPPORTED_MODEL_FAMILY`,
  `PARSER_UNRESOLVED`, `GAME_MAPPING_UNRESOLVED`, `STARTED_GAME_EXCLUDED`,
  `NOT_APPLICABLE`, or (defensively) `NOT_EVALUATED_BUG`; `coverage_accounting()`
  sums every bucket and reports `unaccountedCount` (archived total minus
  the sum) as an explicit, testable value rather than an assumed zero.
- `scripts/build_full_market_coverage.py` (new): CLI wrapper, writes
  `data/pipeline/<date>/full_market_coverage.json` (the existing
  `lib/pipeline_artifacts.write_stage_artifact` envelope pattern) and
  `data/kalshi/discovery/<date>_coverage.json`, exits non-zero if
  `unaccountedCount > 0`. Wired into `discover-kalshi-mlb-markets.yml`
  immediately after the existing discovery step, same job, same
  archived-snapshot observation, still committed by the same
  read/classify/write-only workflow.

**Real 2026-08-19 result (v1, before the completion pass in section 7 below)**
(`data/kalshi_registry_snapshots/kalshi_search_2026-08-19_1931.json`,
2391 raw markets across 17 series + 1 stray non-MLB contract, against a
representative 14-game slate built from that same snapshot's real
(away, home, first-pitch) triples — see `scripts/research/audit_20260819_coverage.py`;
no live `data/slate.json` existed for 2026-08-19 at audit time, so pitcher
identity resolution for K/outs props could not run against real probable
starters and reports `MISSING_REQUIRED_CONTEXT` rather than `FULLY_EVALUATED`
for that family only — a synthetic-fixture limitation of this one-off audit
run, not a production gap):

| Terminal state | Count |
|---|---|
| FULLY_EVALUATED | 594 |
| STARTED_GAME_EXCLUDED | 971 |
| UNSUPPORTED_MODEL_FAMILY | 664 |
| MISSING_REQUIRED_CONTEXT | 161 |
| PARSER_UNRESOLVED | 1 |
| GAME_MAPPING_UNRESOLVED | 0 |
| NOT_APPLICABLE | 0 |
| NOT_EVALUATED_BUG | 0 |
| **Total / unaccounted** | **2391 / 0** |

Before this mission, none of the above was ever computed for a normal
slate run — `marketLedger` (the only thing `RUN_THE_SLATE.md` reads)
carries exactly `games × 11` rows regardless of how many of these 2391
contracts existed that day, and the other ~2380 had no reachable status
anywhere in the pipeline a human following the slate workflow would ever
see. `marketLedger`'s 11 rows, `REQUIRED_MARKETS`, Rule 71/81, and every
edge/sizing/risk-gate threshold are unchanged by this mission — this is a
visibility and accounting fix, not a betting-eligibility change.

## 7. Completion pass (v2): stronger invariant, hitter research linkage, pregame view

Review of section 6's first cut found three real gaps, all fixed here,
none of them requiring any change to `marketLedger`/`risk_gate.py`/
`write_pending_bets.py`/bankroll/fee logic:

1. **The `unaccountedCount == 0` invariant was tautological.** It summed
   terminal states over `len(ledger_rows)` — discover()'s own output — so
   a bug that dropped a raw market BEFORE discover() returned it would
   never show up. Fixed by `lib.kalshi_market_coverage.raw_archive_accounting()`,
   which independently re-derives the unique raw ticker set directly from
   `search_doc` (mirroring, never calling, `extract_raw_markets`'s own
   dedup) and diffs it against the ledger's own tickers. Its
   `trueSilentRemainderCount` cannot be fooled by a shrunken denominator —
   proven by `tests/test_kalshi_market_coverage.py::TestRawArchiveInvariant::
   test_regression_discover_dropping_one_ticker_fails_the_audit`, which
   simulates exactly that bug and shows the weaker check reports a false
   `0` while the new one correctly reports `1`. Also now separately reports
   duplicate raw tickers and ticker-less entries, rather than folding
   either into the denominator.
2. **The coverage artifact wasn't reachable from the normal slate/manual
   workflow without hunting.** `RUN_THE_SLATE.md`'s new "FULL MARKET
   COVERAGE" section now states the exact path: S2 (fetch-slate) →
   `discover-kalshi-mlb-markets.yml` (workflow_run-triggered, same job now
   also runs `build_full_market_coverage.py` right after discovery) →
   `data/kalshi/discovery/<date>_coverage.json`. No new live Kalshi call;
   both steps read the exact same `data/kalshi_search.json`/`data/slate.json`
   observation.
3. **Hitter props were globally `UNSUPPORTED_MODEL_FAMILY`, even though a
   working hitter research engine already prices four of the five
   families.** `lib.research.hitter_board_builder`/`hitter_pricing.py`
   (PR #92/#93) already classifies, matches, and Monte-Carlo-prices every
   real archived `hitter_hits`/`hitter_total_bases`/`hitter_rbis`/
   `hitter_hits_runs_rbis` contract by ticker, writing
   `data/pipeline/<date>/hitter_projection_board.json` on its own
   ~15-minute schedule. `lib.kalshi_market_coverage.link_hitter_research()`
   joins the coverage ledger to that board BY TICKER (reusing its
   modelProbability/executableKalshiPrice/rawProbabilityEdge/
   expectedValuePerDollar/monteCarloStderr/researchRunId verbatim — no
   second model, no recomputation) and adds one new derived field,
   `hitterFeeAwareNetExpectedValuePerDollar`, via the existing canonical
   `lib.edgelab.kalshi_fees.net_expected_value_per_dollar()` (pure,
   staking/bankroll-untouched). A new terminal state,
   `RESEARCH_MODEL_ONLY`, is used ONLY when production has no adapter for
   the family AND the research board independently priced this exact
   ticker — `UNSUPPORTED_MODEL_FAMILY` now means what it says: no model
   anywhere in this repository, not merely "the generic adapter doesn't
   cover it." Every hitter-family row's `realMoneyEligibilityStatus` is
   forced to `"RESEARCH_ONLY"` regardless of linkage outcome — hitter
   props are never promoted to production real-money eligibility by this
   module (item 8's explicit constraint), and `hitter_stolen_bases`
   (the one hitter family with no research model either) correctly stays
   `UNSUPPORTED_MODEL_FAMILY`.

Also added: `pregame_view()` (Phase 6's item 6 — a `STARTED_GAME_EXCLUDED`/
`NOT_APPLICABLE`-excluded re-tally of the same ledger, the number that
actually matters before first pitch) and a new `AMBIGUOUS_TICKER_MATCH`
terminal state (reused from the hitter research board's own
`PLAYER_ID_UNRESOLVED`/`AMBIGUOUS_TICKER_MATCH` outcomes, never a guessed
match).

**Real 2026-08-19 result (v2, same archived snapshot + real hitter research
board, `data/pipeline/2026-08-19/hitter_projection_board.json`, 1639 rows,
generated 19:10 UTC from a standalone snapshot):**

| Terminal state | v1 count | v2 count | Change |
|---|---|---|---|
| FULLY_EVALUATED | 594 | 594 | unchanged |
| RESEARCH_MODEL_ONLY | *(state didn't exist)* | 377 | new — real hitter research evidence surfaced |
| MISSING_REQUIRED_CONTEXT | 161 | 408 | +247 hitter rows reclassified from UNSUPPORTED (lineup unconfirmed / not yet in lineup) |
| UNSUPPORTED_MODEL_FAMILY | 664 | 40 | -624 — now ONLY `hitter_stolen_bases` (genuinely no model anywhere) + nothing else |
| STARTED_GAME_EXCLUDED | 971 | 971 | unchanged |
| PARSER_UNRESOLVED | 1 | 1 | unchanged |
| GAME_MAPPING_UNRESOLVED | 0 | 0 | unchanged |
| NOT_APPLICABLE | 0 | 0 | unchanged |
| AMBIGUOUS_TICKER_MATCH | *(state didn't exist)* | 0 | new |
| NOT_EVALUATED_BUG | 0 | 0 | unchanged |
| **Total / trueSilentRemainderCount** | 2391 / *(invariant didn't exist)* | **2391 / 0** | raw-archive invariant now independently verified |

**Pregame-scoped view** (excludes `STARTED_GAME_EXCLUDED`/`NOT_APPLICABLE`):
`validPregameMarkets` = 1420, of which `pregameFullyEvaluatedProduction` = 594,
`pregameResearchSupportedHitterMarkets` = 377, `pregameMissingRequiredContext`
= 408, `pregameUnsupportedByAllModels` = 40 (all `hitter_stolen_bases`),
`pregameParserUnresolved` = 1, `pregameMappingUnresolved` = 0,
`pregameAmbiguousTickerMatch` = 0 — sums exactly to 1420 (asserted in
`tests/test_kalshi_market_coverage.py::TestPregameView::
test_pregame_states_sum_exactly_to_valid_pregame_markets`).

**Pitcher props, honestly reported:** 203 pitcher K/outs contracts archived
(179 strikeouts + 24 outs); 0 correctly mapped to a probable starter in
this specific v2 run, because no live `data/slate.json` with real
probable-starter identity (`game[side]['pitcher']`) exists for 2026-08-19
in this network-isolated sandbox, and per this mission's explicit
instruction none is fabricated. This is NOT a claim the wiring is broken —
`tests/test_kalshi_market_coverage.py::TestPitcherPropCoverage` proves,
with the same real-format tickers/titles (`"Sonny Gray: 6+ strikeouts?"`,
`KXMLBKS-...-ATHGRAY54-6`) and probable-starter fixture shape production
already uses, that a strikeouts/outs contract for the correct probable
starter resolves to `FULLY_EVALUATED` with independent per-threshold fair
probabilities (monotonically decreasing as the threshold rises), a
non-starter contract stays explicit `MISSING_REQUIRED_CONTEXT`, and no
contract is ever silently dropped either way. The remaining gap is a data
availability gap in this sandbox (no live MLB Stats API access), not a
code gap — it resolves automatically the moment a normal `fetch-slate.yml`
run populates `game[side]['pitcher']` from the live schedule/lineups.
