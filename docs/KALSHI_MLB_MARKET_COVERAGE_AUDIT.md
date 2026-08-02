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
