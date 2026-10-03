# Edge Finder app export (MLB)

`scripts/app_export.py` publishes `app/latest/` on `main` from the data this repository already
commits. It is a pure adapter onto the shared contract vendored at `contract/edge_finder_contract/`
(schema `edge_finder.app.v1`; `tests/test_app_contract_v1.py` proves the vendored copy is
byte-identical to its manifest). It changes no model, threshold, stake, bankroll rule or bet
authority, reads no secret, and never fabricates a number: a field the repository does not hold
is `null`.

Where the app reads it: `https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/app/latest/`
(registry entry `MLB` in `contract/edge_finder_contract/registry.json`).

## CLI

    python scripts/app_export.py --out app/latest [--data-root data] [--now <aware ISO>]
                                 [--commit-sha X] [--workflow-run-id Y] [--date YYYY-MM-DD]

Deterministic: the same inputs and the same `--now` produce byte-identical files. On any failure
only `health.json` is rewritten (`components.export.status = DEGRADED`, `errors[]` filled,
`payload_run_id` = the run id of the payload still on disk) and the process exits 1; the previous
payload is never touched.

## Which date is exported

Today is the America/New_York production date of `--now`. The export date is the newest date at
or before today that has a non-empty `data/edgelab/markets/<date>.jsonl[.gz]`, a
`data/pipeline/<date>/recommendations.json`, or a non-empty `data/edgelab/games/<date>.jsonl`. When
that is not today (an off day, or a failed registry snapshot such as the empty 2026-10-02
partition) the run warns `no slate for today` and health reports the real age of the data, which
is exactly what the app should see.

## What is exported, from where

| document | source | notes |
|---|---|---|
| `events.json` | `data/slates/<date>/authoritative.json` games[] (fallback `data/slate.json` when its `date` matches), plus `data/edgelab/games/<date>.jsonl` rows whose `mlbGamePk` is not on the slate | `event_id` = contract id over (`MLB`, `mlb_game_pk`, gamePk). Teams are participants with source `mlb_team_abbr`. Status: Pre-Game/Scheduled -> SCHEDULED, In Progress -> LIVE, Final -> FINAL; a game whose settlement evidence says `gameStatus: Final` is FINAL even if the slate still says Pre-Game. Start time from statsapi (`SCHEDULED`); a games-partition row without `scheduledStartTime` gets midnight UTC with `start_time_confidence = PLACEHOLDER`. |
| `markets.json` | `data/edgelab/markets/<date>` (identity, title, family, horizon, team, player, operator, threshold) joined with the latest `capturedAt` observation per ticker from `data/edgelab/observations/<date>` (bid/ask/last/volume/OI, dollars 0-1) and `data/kalshi/discovery/<date>.json` (period, side, line, close time, model support, real-money eligibility status) | `market_id = mkt_kalshi_<TICKER>`. Status: a ticker named by a SETTLED row of `data/edgelab/settlements/<date>` -> SETTLED; else the observed status (active -> OPEN), except that an "active" market of a FINAL event is CLOSED (`extensions.observed_status` keeps the raw value). Every ticker a wager references but that is not on the board gets a `market_stub` (prices null, `source = bets_ledger`, status SETTLED/UNKNOWN by the wager's status). The ticker-derived fallback `gameId` (`2026-10-01_PHI_ATL_1400`) is mapped to the gamePk through the games partition and through the ticker's `26OCT011400PHIATL` date+teams key; unmappable rows keep `event_id = null`. |
| `model_prices.json` | `data/edgelab/model_evaluations/<date>` rows with a `modelFairProbability`; per ticker the prospective-snapshot (`artifactSource = prospective_snapshot`, production checkpoint) row wins, else the newest `createdAt`. `marketLedger` rows of the slate fill tickers no evaluation row covers and otherwise travel in `extensions.market_ledger`. | `fair_probability` is P(YES): the evaluation's selection (NRFI, ML_Away, a contract title, ...) is oriented with `lib/edgelab/decision_side.resolve_side` against the market row; a selection whose side cannot be proven is skipped and counted in the run warnings (never guessed). `support_status` = `qualityTier` (TRUSTED_PRODUCTION / RESEARCH_ONLY / MARKET_LEDGER); `data_quality_status` from `dataQuality` (full -> OK). `model_version` = `modelConfigVersion+modelCommitSha[:12]`. `inputs_as_of` = the row's provenance capture time. |
| `recommendations.json` | canonical `data/edgelab/recommendations/<date>` rows; when that partition is empty (pregame, before RECOMMENDATION_SYNC) the `data/pipeline/<date>/execution.json` candidates | Status map: `RECOMMENDED` with real-money eligibility (handicapping card market `realMoneyEligible` or execution candidate `realMoneyEligible`) -> RECOMMENDED / authority MANUAL / `research_only=false`; `RECOMMENDED` without it, PAPER, MODEL_ONLY -> RESEARCH_CANDIDATE (`research_only=true`); `BET_PLACED` -> RECOMMENDED/MANUAL when its `betId` is a REAL ledger row; `PASS_*` / Rejected -> PASS with `reason_not_playable`; WATCH -> WATCH; `Missing Data` -> NOT_PLAYABLE. Full-universe `INSUFFICIENT_MODEL_SUPPORT` / `NOT_EVALUATED` rows are not recommendations and are not exported (counted in warnings). Selection: the placed bet's side, else the slate ledger's `executablePriceSide` for the same ticker, else `decision_side.resolve_side`; unprovable rows are skipped. `native_id` = `recommendationId` (or `execution:<date>:<game>:<market>` for the pregame fallback). Probabilities/prices are percent -> [0,1]. `stake_dollars` and `bankroll_basis` are always null (no dollars from the card). |
| `theses.json` | one per slate game | `summary = null` (the repository produces no prose). `supporting_factors` = the model evaluations' `thesisTags`; `context_notes.lineups` = slate `lineupStatus`; `evidence` = pitchers, projected runs and the ledger rows' status/edge. |
| `wagers.json` | `data/edgelab/bets/bets.jsonl` rows with `trackingType` REAL or REAL_PROBE and `recordStatus` ACTIVE | `source`: KALSHI_ROUTER when `importBatchId == kalshi-router-v1`, LEGACY_IMPORT for `entryMethod == LEGACY_BACKFILL`, else MANUAL. `wager_id` from `sourceBetKey` when present, else (`MLB`, repo, `betId`); `native_id = betId`. `placed_at` = `entryTimestamp` (aware, any offset) or, for the 469 receipts whose `timestampStatus` is NOT_PROVIDED, `recordedAt` with `extensions.placed_at_basis = RECORDED_AT`. `average_price` = `averageFillPrice` else `entryPrice`; `fees` = `executionEconomics.totalFees` else the row's `totalFees`, null when neither exists; `contracts` from the row, else `stake / entryPrice` flagged `contracts_basis = DERIVED_STAKE_OVER_ENTRY_PRICE`. Model/recommendation links come only from `linkage.apply_links` (records that predate `placed_at`). PAPER / MODEL_ONLY rows are never exported. |
| `settlements.json` | one per settled REAL wager: the matching `betId` row of `data/edgelab/settlements/<gameDate>` plus the bet row's own `result` / `netProfitLoss` | `result` from the bet (WIN -> WON, LOSS -> LOST, PUSH, VOID); `winning_side` from the settlement row's YES/NO; `net_pnl = netProfitLoss`; `gross_payout = null` because the ledger's `returnAmount` equals `netProfitLoss` on every row that has it (it is a net figure; kept as `extensions.return_amount_native`). `verification_status`: MODEL_DERIVED when a SETTLED settlement row exists (settled from the MLB Stats API), EXCHANGE_CONFIRMED only if that row carries `kalshiOfficialResult`, UNVERIFIED when the ledger says settled but no settlement row exists (`settled_at` then falls back to the bet's `updatedAt`, flagged in `extensions.settled_at_basis`). |
| `runs.json` | `native_run_id` = the newest `model_evaluations` `runId` (fallback: pipeline provenance workflowRunId / commitSha) | The contract `run_id` of every document is derived from (`MLB`, repo, native run id, `--now`). |
| `board.json`, `event_detail/<event_id>.json`, `performance.json` | built by the contract from the documents above | `performance.clv` = the ledger's own `clv` (percentage points, `POSITIVE_IS_GOOD_V1`) / 100; bankroll history is absent (`numericBankrollAvailable=false`). |
| `health.json` | latest observation `capturedAt`, newest exported model price `generated_at`, newest SETTLED settlement `settledAt`, newest router receipt `recordedAt`, `data/meta.json` `fetchedAt`, plus two extra components: `production_gate` (`data/edgelab/operational_health/production_health_gate.json`, degraded unless `summary.overall == HEALTHY`) and `daily_health` (`data/edgelab/health/<date>.json`) | `bet_authority = MANUAL` (humans place from the handicapping card; the real-money gate is `lib/betting_eligibility.py`), `model_required = true`. `next_scheduled_run` = the next half hour. |

## Freshness thresholds

| component | fresh | stale | why |
|---|---|---|---|
| market_data | 20 min | 2 h | EdgeLab capture cadence |
| model | 30 min | 6 h | prospective snapshots / RECOMMENDATION_SYNC |
| others | contract defaults | | |

`overall_status` is the contract's rule: STALE when market or model data is past its stale
threshold, DEGRADED when the last export attempt failed (payload is last-known-good), UNAVAILABLE
when there is no payload, HEALTHY otherwise.

## Workflow

`.github/workflows/app-export.yml`: `workflow_run` (completed) on `Fetch Slate Data`,
`Build Handicapping Card`, `Prospective Model Snapshots (Scheduled)`, `EdgeLab Market Capture`,
`EdgeLab Postgame Settlement`, `EdgeLab Settlement Reconcile`; cron every 30 minutes; manual
dispatch. Python 3.11, `requirements-ci.txt`, `python scripts/app_export.py --out app/latest
--commit-sha $GITHUB_SHA --workflow-run-id $GITHUB_RUN_ID`, then `scripts/ci/git_data_commit.py`
on `app/latest/` (always, so a failed build's health.json is published), then a gate that fails
the job when the exporter exited non-zero. Concurrency group `app-export`, no cancel-in-progress.

## Tests

`python3 -m pytest tests/test_app_contract_v1.py -q`: vendored-contract check, synthetic end-to-end
(fixtures are trimmed copies of real committed records), determinism, failure safety, stale
health, naive-timestamp refusal, canonical timestamps everywhere, no secret-shaped strings,
wagers <-> settlements / ledger P&L reconciliation, the committed-corpus smoke test and the
workflow wiring test.

## Known gaps

- `placed_at` for router receipts is the ledger's `recordedAt` (the ingestion time, often after the
  game) because those rows carry no placement timestamp (`timestampStatus = NOT_PROVIDED`).
  Temporal linkage therefore links any model price produced before ingestion, not before the real
  fill. Fixing this needs the router to deliver the fill time.
- Events exist only for the exported slate date, so wagers from earlier dates have
  `event_id = null` and their markets are stubs without prices.
- Recommendation rows whose contract side cannot be proven by `decision_side.resolve_side` (for
  example `TT_Away_Over` against a ticker whose line is not the selection's line, `Game_Total`,
  `RL_Home`) are skipped and counted in the run warnings rather than oriented by guesswork.
  On 2026-10-01 this dropped a `RECOMMENDED` team-total row.
- The ledger's `returnAmount` is a net figure, so `gross_payout` is null everywhere; fees are only
  known for router receipts (`totalFees`), legacy rows have none.
- 22 settled REAL wagers have no settlement row (verification UNVERIFIED, `settled_at` = the bet's
  `updatedAt`). One ledger row says WIN while its settlement row is SETTLEMENT_UNRESOLVED; the
  export follows the ledger and marks it UNVERIFIED.
- `data/slate.json` is only used when its `date` is the export date; the per-date authoritative
  slate under `data/slates/<date>/` is preferred.
- No model concern was found that needed a change; nothing in this pass touched model logic.

## Contract feedback

- `build.wager` requires `contracts`; legacy rows have none, so the adapter derives
  `stake / entryPrice` and flags it. A nullable `contracts` would avoid the derivation.
- `build.settlement` has `gross_payout` and `net_pnl` but no place for a "net return" figure;
  the ledger's `returnAmount` lands in `extensions`.
- `health.build_health` accepts `extra_components`, which is where the repository's production
  gate and daily heartbeat live; a documented convention for extra component names would help the
  app render them.

## Research explorer (`app/latest/explorer/`, contract 1.1.0)

`scripts/research_export.py` publishes the research graph beside the v1 files, right after the v1
export in the same workflow step sequence, from the same committed data. `run_id` is the v1
manifest's `run_id`, `generated_at` the v1 manifest's `generated_at` (default `--now`), `as_of` the
newest data timestamp read. Publication is `research.publish_explorer` (validated, graph- and
capability-checked, atomic: a failure leaves the previous tree untouched and exits 1).

    python scripts/research_export.py --out app/latest [--data-root data] [--now ISO] [--commit-sha X]
        [--slate-start D --slate-end D] [--seasons 2025,2026] [--statcast-start D --statcast-end D]

The bounds exist for tests only; production reads everything listed below. Authority for what is
exposed and at which status: the phase-2 audit (`audit_mlb.md` §4 matrix, §10 recommendations).

### What it publishes, from where

| file(s) | content | source |
|---|---|---|
| `teams/<prt_>.json` (30) | season results and rates (W%, R/G, RA/G, RD/G, OPS, OBP, K%, BB%, HR/G, pitching K%/BB%, starter outs/pitches) for every research-cache season with 30-team ranking context; home/away splits (latest season); current snapshots (bullpen xFIP/ERA, team xwOBA, opponent-quality adj, opposing-starter xERA); L5/L7/L10 form (analysis-only); home park factor; last 162 games as game refs with opponent/score/result; opponents; non-prop markets and projections for the team's v1 events | `research_cache/{bullpen_backtest schedules, batting_backtest, starter_workload}` joined on gamePk; `bullpen.json`, `savant_team.json`, `oppquality.json`, `team_offense_form.json`, slates |
| `players/<prt_>.json` | only players on the export slate (probable starters, confirmed-lineup batters, never in batting order) or named by a current v1 market whose (team, name) resolves to exactly one MLBAM id: Savant snapshot, slate starter block, pitcher appearance log (last 40), Statcast per-game aggregates (pitches, pitch mix, velocity, whiff%, xwOBA allowed; batter PA, xwOBA, EV, LA) over the labelled window 2026-08-11..2026-09-27 | `savant_team.json`, slates, `research_cache/starter_workload`, `statcast_raw` |
| `events/<evt_>.json` | one per v1 event: participants, players, matchup rows (projected runs, model win%, season/L7/L15 R/G, wRC+ proxy, offense baseline, opp-quality adj, bullpen xFIP/ERA, starter xFIP/xERA/K%/BB%), v1 model prices as projection refs (RESEARCH unless TRUSTED_PRODUCTION/MARKET_LEDGER), game-level market refs (prop markets counted, listed in player profiles / v1 markets.json), lineup status, venue/park factor, v1 wager ids, `extensions.model_inputs` (starter Savant block, bullpen incl. recentUsage, team form windows, opp quality, offense baseline, the 11-row marketLedger model vs Kalshi VF vs Pinnacle VF, Pinnacle/Kalshi odds) | `slates/<date>/authoritative.json` + the v1 publication |
| `market_history/<evt_>.json` | every ticker of the event: quote series from `edgelab/observations/<date>` tagged with the checkpoint, closing quotes from `edgelab/clv_quotes/<date>` (`+closing_quote`); game-level tickers first, player-prop tickers dropped whole if the document would pass 400 KB (stated in its quality) | EdgeLab |
| `series/<ser_>.json` | per team: runs scored / allowed per game (last 162, rolling 10), opponent-quality adj and offense baseline per slate date; per player: outs and pitches per appearance, Statcast velocity / batter xwOBA per game; per ticker with >= 2 evaluations: model P(YES) per run (x_axis RUN, RESEARCH points unless TRUSTED_PRODUCTION) | as above, `edgelab/model_evaluations/<date>` oriented with `decision_side.resolve_side` like v1 |
| `rankings/<rnk_>.json` | full 30-team universe per team metric and window (13 metrics x each research-cache season, 5 snapshots, form mean/median x L5/L7/L10; team-total overs% is published and ranked only when the form file stores lines) | arithmetic over the stored values |
| `metrics.json` | 52-59 registered metrics; `wager_win_rate`, `wager_clv` (REAL / REAL_PROBE wagers from the v1 `wagers.json`, by market family, with sample-size tiers, postmortem inventory) and `model_calibration_error` (bins of `data/research/calibration_bins.json`, REAL bankroll-counting wagers) carry their aggregates in `extensions` | |
| `capabilities.json`, `search_index.json`, `index.json` | the 36 capabilities, search over teams/players/events/metrics/rankings, the file table | |

An empty slate day (no v1 events, e.g. 2026-10-03: postseason markets, no slate) still publishes
the 30 team profiles, rankings, series, players named by current markets, capabilities, metrics and
search; the capabilities that need an event are then UNAVAILABLE with the reason "nothing to show
in this publication".

### Capability statuses (slate day; audit 2026-10-03)

| status | capabilities | why |
|---|---|---|
| VERIFIED | team_profiles, event_research, team_metrics, team_game_logs, historical_results, opponents, opponent_adjustment (simple: opposing-starter xERA, capped +/-0.2), recent_form_windows (analysis-only, labelled), raw_projections, market_prices, team_props, game_markets, venue_effects (single static park factor), calibration (descriptive), historical_accuracy, clv, wager_history, rankings, time_series, comparisons, search | research cache / production files with history and tests |
| PARTIAL | player_profiles, player_metrics (snapshot only), player_game_logs (no batter box lines; Statcast window), usage, lineups (status only), matchup_metrics (platoon context often MISSING_DATA), market_price_history (snapshot series, median 3 points), advanced_stats (48 Statcast game-days), situational_splits (home/away only), play_by_play (Statcast pitch log aggregated, nothing before 2026-08-11) | limitations quoted from the audit in each item |
| RESEARCH | projection_distributions, player_props | not published: NB shadows and hitter/pitcher prop probabilities are research; production says NO_MODEL_SUPPORT for props |
| UNAVAILABLE | schedule_strength, injuries, weather | not stored |

### Sizes (measured, `research.tree_bytes`, full production read)

| publication | teams | players | events | market_history | series | rankings | metrics.json | search | index.json | total |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-01 (1 game, 21 players) | 3.32 MB | 0.37 MB | 0.10 MB | 0.20 MB | 4.06 MB (149) | 0.64 MB (76) | 90 KB | 63 KB | 74 KB | 8.9 MB |
| 2026-10-03 (no events, 34 market players) | 3.34 MB | 0.76 MB | - | - | 4.29 MB (169) | 0.64 MB | 83 KB | 64 KB | 82 KB | 9.3 MB |
| 2026-09-25 (17 games, 305 players) | 3.82 MB | 6.25 MB | 1.76 MB | 3.41 MB | 7.53 MB (642) | 0.64 MB | 91 KB | 163 KB | 229 KB (compact, contract 1.1.1) | 23.9 MB |

Largest single documents on 2026-09-25: team 136 KB, event 119 KB, player 43 KB, market history
299 KB, series 46 KB (budgets 150 / 150 / 150 / 400 KB; team profiles drop projections, then
markets, if a doubleheader day would push them past 145 KB). Runtime: ~8 s (1 game) to ~15 s (full slate).

### Deliberately not published

Hitter/pitcher prop probabilities, NB distribution shadows, Pinnacle historical, replay scoring
(RESEARCH, listed in the manifest notes); batting orders (present in recent slates as
`confirmedLineup`, but the audit rates them UNAVAILABLE, so players are listed by team/role/name
only); injuries, weather values, umpires, PBP before 2026-08-11, player season-stat history, team
schedule strength, postseason 2026 (UNAVAILABLE); the research-branch microstructure (not on main);
single-evaluation "projection series" (the value is already the v1 model price); pitch-level rows.

### Tests and workflow

`tests/test_research_export.py` (real committed corpus, bounded to 4 slate dates, the 2026 season
and one Statcast week; ~25 s): verify_explorer clean, determinism, v1 identity coverage, capability
statuses equal the audit, GAME packet with `quality.missing == []`, no secret-shaped strings,
RESEARCH/PARTIAL statuses preserved into profiles and the packet, atomic failure, size budgets and
labels, the empty slate day, the refresh gate (skip within the interval leaves the tree
byte-identical; a changed v1 event set rebuilds), and the workflow wiring. In `.github/workflows/app-export.yml` the step
`research_export` runs right after `export` (only when it succeeded), `continue-on-error`, logs to the
step summary; the same commit step publishes `app/latest/` including `explorer/`, and the final gate
fails the job when `steps.research_export.outcome == failure` (the v1 payload is published regardless).

Refresh gate: every explorer file carries `generated_at`, so a rebuild rewrites the whole tree
(~24 MB on a full slate). The workflow passes `--min-interval-minutes 180`
(`research.refresh_due`, contract 1.1.1): the rebuild is skipped (exit 0, `explorer/` untouched,
reason printed to the step summary) unless no explorer exists, the v1 event set changed (a new
slate rebuilds at once) or the published tree is at least 3 hours old. Since 1.1.1 the v1
`publish.publish` no longer prunes `explorer/`, so a skipped run keeps the last tree; its `run_id`
then names the explorer's own publication, not the newer v1 manifest. Default `0` = always rebuild.

GAME handicap packets on 2026-09-25 (17 events, contract 1.1.1 compact text): 25.0-48.7 K chars
(median 35.8 K) against the 60 K budget, nothing truncated, `quality.missing` empty.
