# Projection Board — Stage 1 (game-derived) + Stage 2 (pitcher props)

Date: 2026-08-10 (Stage 1); updated 2026-08-10 (Stage 2)

## What this is

A PRE-GATE full-market projection board for every archived, eligible
MLB Kalshi market/rung. It exists so manual analysis (and tools like
ChatGPT) can inspect the entire board instead of only the small subset
of markets that happen to survive the automated recommendation/risk-gate
pipeline in `scripts/build_market_ledger.py` / `scripts/risk_gate.py`.

- **Stage 1**: game-derived markets (moneyline, F3/F5/F7, run lines,
  totals, team totals, NRFI/YRFI).
- **Stage 2**: `pitcher_strikeouts` / `pitcher_outs` join the same board,
  reusing PR #58/#59's existing production pitcher-workload/K/outs
  projection path end to end — no new pitcher modeling math.

Hitter props remain untouched/out of scope for both stages.

## Root cause this closes

`scripts/build_market_ledger.py`'s `REQUIRED_MARKETS` list (11 fixed
strings) is production's real-money gate — it evaluates exactly one line
per market family (`best_line()`), and only 6 of the 8 known market
families at all. Everything else — every alternate rung, F3/F7 winner
markets, every total/team-total threshold beyond best_line, the F5 Tie
leg — is either never modeled or modeled-but-never-exposed. See
`docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md` for the full per-family
audit that this stage acts on.

Separately, a "universal Kalshi MLB market engine" (contract parser,
classifier, taxonomy, and per-line probability adapters —
`lib/kalshi_mlb_contract_parser.py`, `lib/kalshi_mlb_market_classifier.py`,
`lib/research/market_taxonomy.py`, `lib/kalshi_probability_adapters.py`,
`scripts/discover_kalshi_mlb_markets.py`) already existed as production-
quality, individually-tested library code — but was never wired into the
pipeline as a board artifact, and never filtered to a specific stage's
scope. **Stage 1 reuses that engine wholesale** (no new statistical
model) and adds the board/display/advisory layer on top.

## Architecture

```
scripts/discover_kalshi_mlb_markets.discover()
  -> lib.kalshi_mlb_contract_parser.parse_contract()      (ticker -> canonical contract)
  -> lib.kalshi_mlb_market_classifier.classify_contract()  (family/period/side/line)
  -> lib.kalshi_probability_adapters.adapt_contract()       (fair probability, EVERY line)
       -> scripts.build_market_ledger.{poisson_pmf,p_team_wins,p_over_total}
       -> lib.research.three_way_projection.three_way_result_probs()  (F3/F5/F7 Away+Tie+Home=1)
       -> lib.kalshi_period_projections (F3/F7 period-scaled run projections)
       |
       v
lib.kalshi_projection_board.build_projection_board()
  - filters to BOARD_FAMILIES = STAGE1_FAMILIES | PITCHER_FAMILIES
  - synthesizes the complementary NO/Under side for every single-ticker
    two-sided family (game_total, inning_total, team_total,
    first_inning_run, and -- Stage 2 -- pitcher_strikeouts/pitcher_outs)
    so both sides of every two-sided ticker are visible
  - adds display fields (natural label, American odds both directions,
    raw-vs-mid and executable-vs-ask edge via scripts/executable_price.py;
    Stage 2 adds pitcher name/team resolution, a plain identity lookup
    against the matched game's already-resolved probable starter)
  - attaches the game's own marketLedger row (if any) as ADVISORY-ONLY
    metadata — never used to filter or hide a row
  - runs non-fatal internal coherence checks, surfaced in the summary
       |
       v
scripts/build_projection_board.py (I/O only)
  -> data/pipeline/<date>/projection_board.json  (lib/pipeline_artifacts.py)
```

Stage 2's pitcher_strikeouts/pitcher_outs pricing itself comes from a
sibling path already wired by PR #58/#59, joining the SAME
`adapt_contract()` dispatcher Stage 1 uses:

```
lib.kalshi_mlb_market_classifier.classify_contract()
  -> _resolve_pitcher_prop_subject()   (exact-name match vs. game's probable starter)
       |
       v
scripts.discover_kalshi_mlb_markets.resolve_projection_context()
  -> threads pitcherAvgIPperStart/pitcherKPct/... into projection_context
       |
       v
lib.kalshi_probability_adapters.adapt_pitcher_strikeouts / adapt_pitcher_outs
  -> lib.research.pitcher_workload_projection.project_pitcher_workload()
       (ONE shared per-out survival curve -> both K and outs probabilities,
        so a worsening workload assumption moves them together, never independently)
```

One statistical engine feeds every sibling contract for a game — all
alternate game totals come from the same full-game run distribution, all
team-total rungs come from the same team-run distributions, F3/F5/F7
Away/Tie/Home always sum to 1 by construction (never renormalized), and
every K/outs rung for one pitcher/game comes from the same survival curve.

## Families in scope

Stage 1 (`STAGE1_FAMILIES`):
- Full-game moneyline (`game_result` / `full_game`)
- F3 / F5 / F7 Away / Tie / Home (`inning_result`)
- Full-game winning margin / run line, every archived threshold (`winning_margin`)
- Game totals, every archived threshold, Over and Under (`game_total`)
- F3 / F5 / F7 inning totals, every archived threshold, Over and Under (`inning_total`)
- Away and home team totals, every archived threshold, Over and Under (`team_total`)
- NRFI / YRFI (`first_inning_run`)

Stage 2 (`PITCHER_FAMILIES`):
- Pitcher strikeouts, every archived "N+" threshold, Yes and No (`pitcher_strikeouts`)
- Pitcher outs recorded, every archived "N+" threshold, Yes and No (`pitcher_outs`)

## Not in scope

- Hitter props (`hitter_hits`, `hitter_total_bases`, `hitter_home_runs`,
  `hitter_rbis`, `hitter_stolen_bases`, `hitter_hits_runs_rbis`) — no
  probability distribution exists for these anywhere in the codebase;
  untouched, per every stage's mission scope.
- `pitcher_hits_allowed` / `pitcher_earned_runs` — no Kalshi series has
  ever been observed for either; still `UNSUPPORTED`.

## Board row schema (see `lib/kalshi_projection_board.py`)

Each row carries: `gameId`, `marketTicker`, `marketFamily`, `horizon`,
`side`, `subjectId` (team abbr for team-scoped markets, pitcher MLB
player id for pitcher props), `subjectName` (pitcher display name when
identity resolved — `null` for non-pitcher rows), `team` (best-known team
abbreviation for the row — resolved via a plain identity lookup for
pitcher rows, never re-derived), `threshold` (natural, e.g. `8.5` for a
total or `6` for a pitcher "N+" prop) / `thresholdRaw` (internal
representation), `displayLabel`, `executableMarketPriceCents`,
`marketAmericanOdds`, `modelFairProbabilityPct`, `modelFairAmericanOdds`,
`rawEdgePct` (vs. mid), `executableEdgePct` (vs. ask — post-friction),
`projectionStatus` (`PROJECTED` / `NOT_MODELED` / `MISSING_DATA`),
`limitationReason`, and `automatedRecommendation` (advisory-only:
`automatedStatus`, `automatedConfidence`, `automatedGatesFired`,
`automatedRejectionReason`, and whether the automated ledger evaluated
this exact rung or a different one — pitcher props are not in
`scripts/build_market_ledger.py`'s `REQUIRED_MARKETS`, so their advisory
status is always `NOT_GOVERNED_BY_AUTOMATED_LEDGER` today, an honest
answer, not a gap).

**A downstream PASS/PAPER/Rejected automated status never removes a row
from this board.** A market that cannot be modeled honestly — including
a pitcher-prop ticker whose parsed display name doesn't exactly match
either team's probable starter — keeps `projectionStatus = NOT_MODELED`
or `MISSING_DATA` with an explicit `limitationReason` — never a
fabricated identity or probability.

## Coherence guarantees

Enforced by `tests/test_kalshi_projection_board.py` and self-checked at
runtime (`coherenceWarnings` in the board summary, expected empty):

- F3/F5/F7 Away + Tie + Home sum to 1 (100%).
- Game/F3/F5/F7 total Over probabilities decline monotonically as
  thresholds rise.
- Team-total Over probabilities decline monotonically as thresholds rise.
- Complementary YES/NO (Over/Under, YRFI/NRFI, and — Stage 2 — pitcher
  N+/fewer-than-N) probabilities of the same ticker sum to 1.
- Run-margin (`winning_margin`) probabilities are derived from the same
  independent-Poisson joint distribution as every other market
  (`lib.kalshi_probability_adapters.p_wins_by_over`).
- Stage 2: P(K ≥ N+1) ≤ P(K ≥ N) and P(Outs ≥ N+1) ≤ P(Outs ≥ N) for
  every pitcher/game.
- Stage 2: a worsening shared workload assumption (e.g. an opener flag)
  moves a pitcher's K and outs fair probabilities down together, proving
  the single shared survival curve, never two independently-drifting
  point estimates.
- Every archived alternate rung — game-derived and pitcher-prop alike —
  appears on the board, not only `best_line()`.
- Rows with a Rejected/Paper/Missing-Data automated status remain
  visible.
- No change to any existing production ML/F5/NRFI/pitcher-prop
  probability — this module imports, never reimplements,
  `scripts/build_market_ledger.py`'s Poisson primitives and
  `lib/kalshi_probability_adapters.py`'s adapters (cross-checked
  bit-for-bit in `tests/test_kalshi_probability_adapters.py` and this
  repo's own `TestNoRegressionVsProduction` /
  `test_stage1_game_derived_rows_unchanged_when_pitcher_props_also_on_slate`).

## Pipeline wiring

`scripts/build_projection_board.py` runs in `fetch-slate.yml`
immediately after `scripts/build_market_ledger.py` (needs marketLedger
for advisory linkage) and before the final regression/validation/publish
steps, with `continue-on-error: true` — this artifact can never block
slate publication or betting behavior. It writes only
`data/pipeline/<date>/projection_board.json`; it never touches
`data/slate.json`, `bets.json`, `config/rules.json`, or any
settlement/staking/risk-gate file.
