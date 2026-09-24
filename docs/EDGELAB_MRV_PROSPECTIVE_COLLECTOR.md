# MRV prospective collector v1

**RESEARCH ONLY. READ-ONLY. Parallel to, and isolated from, the MLB-ALPHA-0002
collector. Changes nothing in production (eligibility, recommendations,
staking, wager routing, settlement, execution) and nothing in the ALPHA-0002
corpus or its frozen shadows C01-F5REV / C03-BOOKIMB.**

Identity: `COLLECTOR_ID = MRV_PROSPECTIVE_COLLECTOR`, `COLLECTOR_VERSION = v1.1.1`,
`SCHEMA_VERSION = mrv_prospective_v1` (`lib/edgelab/research/mrv_collector/__init__.py`).
Every stored row and every manifest carries all three plus its `runId`.

## Why a separate collector

The MRV program's DATA_BLOCKED hypotheses (information-event repricing,
sportsbook lead/lag at minutes, totals vs sportsbook lines, order-book
imbalance) need three things the ALPHA-0002 corpus cannot supply without
changing that collector: a delivered cadence of ten minutes or better, no
first-N book cap (the 400-book cap captured 30 game-total books in 21 days),
and per-fetch timestamps. Changing the ALPHA-0002 collector would change the
data universe its frozen candidates are evaluated on. So MRV v1 has its own
storage root, research branch, state file, series policy, run identity and
health report, and by test never imports the ALPHA-0002 path.

| | ALPHA-0002 collector | MRV v1 |
|---|---|---|
| storage | `research_artifacts/mlb_alpha_0002/prospective/` on `research/mlb-alpha-0002-prospective` | `research_artifacts/mrv_prospective/v1/` on `research/mrv-prospective-v1` |
| run id | `ALPHA0002_<ts>` | `MRV1_<ts>_<6 hex>` |
| books | first 400 FULL tickers per run | every per-game market of every pregame game |
| timestamps | one `capturedAt` per run | `requestedAt`/`respondedAt`/HTTP `Date` per fetch |
| cadence | one cron event per capture | bounded 340-minute loop, persist after every cycle |
| policy | `series_universe_policy.json` | `mrv_prospective/mrv_series_policy.json` |

## What one cycle does (`lib/edgelab/research/mrv_collector/cycle.py`)

1. MLB schedule for today and tomorrow (ET) → eligible **pregame** games
   (abstract state Preview or detailed state in Scheduled / Pre-Game / Warmup /
   Delayed Start / Postponed).
2. Kalshi `GET /series` → every MLB-looking series classified by policy:
   `PER_GAME_BOOK` (13 per-game series: quotes + full books), `PROP_QUOTE`
   (props: quotes only), `EXCLUDED` (futures/awards/non-MLB, listed in the
   manifest), `UNCLASSIFIED_MLB` (new series: reported, never dropped).
3. Every included series paged with `status=open&limit=1000` to exhaustion.
   Each page attempt is recorded (cursor before/after, status, items, error,
   timestamps). A series is `complete` only when the last page carried no
   cursor. A safety cap of 50 pages is explicit truncation evidence
   (`SAFETY_PAGE_CAP`), never silent.
4. **Every** per-game market whose event resolves to exactly one eligible
   pregame game gets `GET /markets/{ticker}/orderbook`. No cap. Full ladder
   stored in cents with the source key (`orderbook_fp` dollars vs legacy
   `orderbook` cents), best YES/NO bid and ask, top-of-book quantities,
   level counts, two-sided flag.
5. Trade tape `GET /markets/trades?min_ts=<previous cycle start − 60 s>` paged
   to exhaustion, MLB tickers only, deduplicated by `trade_id` against the
   last 20,000 ids.
6. Sportsbook: one `GET /sports/baseball_mlb/odds` (Pinnacle, DraftKings,
   FanDuel, BetMGM; h2h, spreads, totals; decimal). One stored row per
   (event, book, market, outcome) with the provider `last_update` and the
   fetch timestamps; credits from the response headers go into the manifest.
   Deterministic join to eligible games by team-name table + commence time
   within ±45 min: exactly one candidate → `MATCHED`; several → `AMBIGUOUS`
   (recorded, never guessed); none → `UNMATCHED` with reason.
7. Live feed per eligible game → state {status, probable pitchers, lineup ids
   and posted flags, weather, scheduled start, postponed}. A row is written
   only when the state fingerprint changes, carrying `observedAt` (our
   fetch), `prevObservedAt` (bounds the event), the feed's own
   `metaData.timeStamp` as `sourceTimestamp` labelled
   `feed_last_update_not_event_time`, and the list of field transitions.
8. Cross-section digest (every ticker → quote fp, book fp, fetch time, game),
   reconciliation, write-once manifest.

## Storage (raw = evidence, append-only)

Root: `data/edgelab/research_artifacts/mrv_prospective/v1/` on branch
`research/mrv-prospective-v1` (never main).

| path | one row per | notes |
|---|---|---|
| `attempts/<date>.jsonl` | cycle attempt, written **before** the cycle | an attempt without a manifest is a non-delivered capture |
| `runs/<date>/<runId>.json` | completed cycle | write-once; `captureClass`, reconciliation, series evidence, starvation, joins, failures, HTTP stats |
| `kalshi_quotes/<date>.jsonl.gz` | changed quote | change-suppressed by fingerprint |
| `kalshi_books/<date>.jsonl.gz` | changed book | full ladder; empty payloads stored with `book: null` and response keys |
| `kalshi_crosssection/<date>.jsonl.gz` | cycle | every ticker seen → fps + fetch times (+ `qRef`/`bRef` anchor dates) |
| `kalshi_trades/<date>.jsonl.gz` | print | dedup by `trade_id` |
| `sportsbook_odds/<date>.jsonl.gz` | (event, book, market, outcome) | provider `last_update` + fetch stamps |
| `sportsbook_joins/<date>.jsonl.gz` | event per cycle | MATCHED / AMBIGUOUS / UNMATCHED |
| `mlb_state/<date>.jsonl.gz` | game state change | transitions + first-seen / prev-seen bounds |
| `state/collector_state.json` | — | fingerprint cache; rebuildable |
| `health/latest.json`, `health/<date>.json` | — | derived; regenerated |

Semantics:
- **Append-only**: gzip members are appended; manifests refuse a second write.
- **Retry immutability**: a failed or partial cycle keeps its attempt row and
  manifest; a retry is a new `runId` with `attempt` incremented.
- **Deduplication**: a quote/book is referenced instead of stored only when
  an identical full row (same ticker, same fp) is present in a persisted
  partition of the last 3 days; otherwise the full row is written again.
  Dangling references are impossible by construction.
- **Partial-run semantics**: whatever was fetched is written; the manifest
  says `PARTIAL` with the failing stage(s).
- **Retention**: raw partitions are never pruned by code. The research branch
  may be archived to a Release once a month by the owner; the manifests are
  the evidence of what existed.
- **Derived tables** (health, any future normalized panel) are regenerated
  from raw and never edited by hand.

## Reconciliation and completeness class (per cycle)

`marketsReceived = archived + referenced + excluded`; `unaccountedRows` must
be 0. `COMPLETE` = every included series exhausted, no fetch failure, no
failed book, unaccounted 0, series list fetched. `PARTIAL` = anything
truncated/failed/unaccounted. `FAILED` = no market received.
`researchComplete` additionally requires zero **family starvation**: a listed
pregame game with markets in at least one core family but none in another
(core = GAME, TOTAL, SPREAD, TEAMTOTAL, F5, F5TOTAL). Games Kalshi has not
listed at all are reported as `unlistedEligibleGamePks`, not as starvation.

## Cadence

The requested cadence is 10 minutes. It is **not** claimed as delivered.
Delivered cadence is computed from persisted manifests only
(`build_health.py`): gaps between consecutive cycle starts inside the MLB
window (15:00–04:59 UTC), median / p90 / max, plus a histogram.

Two paths write the same corpus, distinguished by `trigger`:

1. **Repo-native (deployable now)**: `.github/workflows/research-mrv-prospective-capture.yml`
   — hourly schedule, non-cancelling concurrency group, each job a bounded
   340-minute loop (`run_loop.py`) capturing every 10 minutes and persisting
   after every cycle. Queued jobs chain behind the running one, so the loop,
   not scheduler punctuality, sets the cadence; a dropped hourly event costs
   nothing until the running loop's budget ends. Free for a public repo.
   Inert until this workflow file is on the default branch (GitHub cannot
   dispatch or schedule a workflow from a feature branch).
   **Continuity (self-dispatch, owner-approved 2026-09-24).** Live, GitHub
   delivered only 1 of this workflow's first 5 hourly cron slots, so waiting for
   a scheduled event left multi-hour gaps. The last workflow step
   (`scripts/research/mrv_collector/chain_successor.py`) therefore requests at
   most ONE successor (`budget_minutes=340`, `cadence_minutes=10`) through the
   existing `workflow_dispatch` entry point. It runs only when every earlier step
   succeeded on the default branch:
   - the capture step succeeded, and `run_loop.py` now fails it when any cycle
     is FAILED or crashes;
   - rows were verified on the research branch;
   - the isolation checks passed.

   It requests nothing if another run of this workflow is already
   queued/pending/in progress, because that run is the successor. A failed or
   cancelled job requests nothing, so the chain stops visibly instead of looping.
   Permissions are `contents: write` plus `actions: write` (needed only to
   dispatch). The non-cancelling concurrency group admits one running and one
   pending job, so collectors never overlap. The hourly cron stays as the
   fallback that restarts a stopped chain.

   **Kill switch:** the repository variable `MRV_CONTINUOUS_CAPTURE_ENABLED`
   (Settings → Secrets and variables → Actions → Variables).
   - Unset (the default) or any other value means continuous capture is on.
   - Set it to `false` (also `0`/`no`/`off`/`disabled`) to stop. The running job
     finishes its bounded budget and requests no successor, and cron-triggered
     runs are skipped. A manual dispatch still runs one bounded job.
   - Disabling the workflow in the Actions tab stops everything.
2. **External always-on runner (primary once deployed)**:
   `scripts/research/mrv_collector/external_runner.sh` — same loop forever on
   any Linux host with python3 and git; needs `ODDS_API_KEY` and a
   fine-grained PAT with contents:write (`MRV_GIT_TOKEN`). Owner action.

Cycle cost, measured design: ~40 market pages + ~1,500–2,500 order books +
~20 live feeds + 1 odds call + trade pages ≈ 1,600–2,600 requests at the
throttled 7–8 req/s ≈ 4–6 minutes. If a cycle overruns the cadence the
loop starts the next one immediately and counts an overrun. Nothing in the
repo knows Kalshi's published limit; the throttle is empirical
(ALPHA-0002: 499-call burst drew 90 HTTP 429s; ~7.5 req/s sustained drew
none).

The Odds API call costs `markets × region-equivalents` credits; with three
markets and four books that is 3 credits per cycle, ~430/day at ten-minute
cadence over the window, against ~14,800 remaining on 2026-09-15 (per the
ALPHA-0002 manifests). The collector records `x-requests-remaining` in every
manifest; the health report surfaces it. Alternate lines are not requested
(per-event endpoint, multiples of the cost).

## Health and research-readiness gates (`health.py`, frozen `MRV_READINESS_GATES_V1_1_2026_09_23`)

Rolling 7-day window, computed only from the persisted corpus:

| gate | threshold |
|---|---|
| cadence median gap (in window) | ≤ 10 min |
| cadence p90 gap | ≤ 15 min |
| cadence max in-window gap | ≤ 30 min |
| COMPLETE share of delivered cycles | ≥ 90 % |
| unaccounted rows | 0 |
| FAILED share | ≤ 5 % |
| starved game-cycles | 0 |
| core families present (game-cycles) | ≥ 98 % |
| sportsbook matched share of eligible games | ≥ 90 % |
| ambiguous joins | 0 |
| books per matched event (median) | ≥ 3 |
| per-fetch timestamp coverage (books + odds rows) | 100 % |
| games with a state row | ≥ 95 % |
| lineup transitions observed | ≥ 20 |
| two-sided books | ≥ 95 % |
| books with depth | ≥ 95 % |
| sportsbook leg OK in every cycle (not budget-degraded) | 0 degraded cycles |
| sample: unique games / dates / contracts per core family — REGULAR_SEASON only | ≥ 60 / ≥ 10 / ≥ 80 |

`infrastructureHealthy` = every non-sample gate passes; `researchReady` =
all gates pass. **Until `researchReady` is true, no MRV inference run is
authorised on this corpus.** The thresholds are not to be lowered because
collecting the data is inconvenient.

## Readiness gates V1.2 (`readiness_v12.py`, `MRV_READINESS_GATES_V1_2_2026_09_24`)

**Why V1.2 exists.** The first live window (29 COMPLETE cycles, 0 unaccounted
rows, 0 failed books) left V1.1 `infrastructureHealthy = false` for three
reasons that are measurement-definition problems, not collector failures:
- tomorrow's games whose Kalshi families are only partly listed (Kalshi lists
  `KXMLBGAME` first);
- tomorrow's games whose sportsbooks have not posted lines;
- thin inning families inside the global two-sided-book share.

V1.2 fixes the readiness **population** before any prospective inference is
authorised. V1.1 is not edited. It is still computed and reported
(`gates`), and V1.2 is reported next to it (`readinessV1_2`).

**Window.** T-240 through T-5 minutes before scheduled first pitch, with both
ends included. This is the grid the MRV lead/lag design already used
(`market_structure/leadlag.py grid_rows(first_before=240, last_before=5,
step=5)`); it was predeclared and not fitted to live pass/fail results.
- A game-cycle's distance to first pitch uses the cycle start.
- A book's distance uses that book's own fetch time.
- The scheduled start is the latest persisted MLB state value by the end of
  that cycle.
- A game with no known start is `UNKNOWN_START`: excluded and counted.
- Capture outside the window is unchanged, and those game-cycles are reported as
  `OUTSIDE_READINESS_WINDOW_BEFORE_T240` / `_AFTER_T5`.

**Core families** (exactly the V1.1 sample-gate set): `KXMLBGAME`,
`KXMLBTOTAL`, `KXMLBSPREAD`, `KXMLBTEAMTOTAL`, `KXMLBF5`, `KXMLBF5TOTAL`.
Inning, RFI, F3/F7, extras and props remain captured and reported (non-core
book quality separately) but cannot make the core layer unhealthy.

**Populations changed (thresholds unchanged):**
- `familyCoverage.starvedGameCycles` (≤ 0) and `coreFamiliesPresentShare`
  (≥ 0.98) use in-window game-cycles and the six core families.
- `sportsbook.matchedGameShare` (≥ 0.90) uses eligible games observed inside
  the window. A game counts as matched only if it was MATCHED during an
  in-window cycle. Ambiguous joins still fail via the unchanged global gate.
- `orderBook.twoSidedShare` / `withDepthShare` (≥ 0.95) use archived
  core-family books fetched inside the window.
- Sample floors (≥ 60 games, ≥ 10 official dates, ≥ 80 contracts in each core
  family) count only REGULAR_SEASON games/contracts with at least one in-window
  observation. POSTSEASON is its own stratum, and OTHER_OR_UNKNOWN counts
  toward neither.

Unchanged from V1.1: cadence, COMPLETE share, unaccounted rows, FAILED share,
ambiguous joins, books/event, budget degradation, timestamp coverage,
information-state coverage, and lineup transitions ≥ 20.

## Season phase (frozen rule `MRV_SEASON_PHASE_RULE_V1_2026_09_23`)

The 2026 regular season ends 2026-09-27, so the collector goes live in its
final days and a ten-date sample could otherwise be reached only by pooling
regular season with postseason. That is not allowed.

- **Raw provenance**: MLB Stats API `gameType` from the schedule
  (`gameTypeSchedule`) and the live feed (`gameData.game.type`), stored
  verbatim (`lib/edgelab/research/mrv_collector/season_phase.py`).
- **Coarse phase**: `REGULAR_SEASON` = `R`; `POSTSEASON` = `F`, `D`, `L`, `W`,
  `C`, `P` (MLB's own gameTypes meta); `OTHER_OR_UNKNOWN` = any other code, a
  missing code, or schedule and feed codes that disagree. Never guessed.
- **Carried through**: `mlb_state` rows (`gameType`, `seasonPhase`,
  `officialDate`, `seasonPhaseRule`), every cross-section entry (`gameType`,
  `phase`, `gameOfficialDate`), sportsbook join rows (`seasonPhase`), and
  manifests (`eligibleGames`, `eligibleGamesByPhase`, `seasonPhaseRule`).
- **Rule**: regular-season inferential MRV research requires its minimum
  game / date / contract sample **entirely** from `REGULAR_SEASON`
  observations. `POSTSEASON` is a separate regime: collected and preserved,
  never counted toward the regular-season gate. Regular season and
  postseason are not pooled in any inferential analysis unless a hypothesis
  or specification predeclares cross-regime pooling or stratification before
  the result is examined. `OTHER_OR_UNKNOWN` counts toward neither.
- **Health**: sample gates read regular-season games, regular-season MLB
  official dates and regular-season contracts only; `coverage.byPhase`
  reports every phase separately. Five regular-season dates plus five
  postseason dates fail the ten-date gate (tested).

## Odds API budget guard (`MRV_ODDS_BUDGET_GUARD_V1_2026_09_23`)

The quota is shared; the owner authorised the MRV leg at ~430 credits/day,
not unlimited use (`lib/edgelab/research/mrv_collector/odds_budget.py`).

- **Ledger**: `odds_budget/<ET date>.jsonl`, append-only, one row per cycle
  (request made or refused) with credits charged, provider
  `x-requests-last` / `x-requests-used` / `x-requests-remaining`, HTTP
  status, fetch timestamps, spend before the request, ceiling and reserve.
  The daily spend is summed from this ledger only, so a retry, new runId,
  new process or deleted state file cannot reset it.
- **Hard daily ceiling**: spend today + expected cost of the next request
  must be ≤ **450** credits, else the leg is refused
  (`DAILY_CEILING_REACHED`). Expected cost = max(last provider
  `x-requests-last`, design cost 3).
- **Reserve**: if the freshest provider-reported remaining quota (last
  response within 7 days) is **< 5,000**, no further MRV odds request is made
  (`REMAINING_BELOW_RESERVE`); a response that itself shows < 5,000 marks
  that cycle `REMAINING_BELOW_RESERVE_AFTER_REQUEST`.
- **Evidence**: a response without a remaining-quota header degrades the leg
  (`REMAINING_QUOTA_UNKNOWN`) until the owner intervenes; only the very first
  request with no ledger history runs without evidence, because it produces
  the evidence.
- **No retry spend**: the odds request is single-attempt (the fetcher's retry
  loop is disabled for it). A request that got no usable header is charged
  the design cost, never zero.
- **Degradation**: the leg status is `DEGRADED_BUDGET_GUARD` with the
  reason; the Kalshi and MLB legs of the cycle continue and the cycle's
  capture class is unaffected. No sportsbook rows are written, so missing
  quotes are never read as zero disagreement.
- **Readiness**: new gate `sportsbook.notBudgetDegraded` fails for any cycle
  in the window whose sportsbook leg is not `OK` (degraded, not configured
  or fetch-failed); `infrastructureHealthy` and `researchReady` are false
  while it fails.
- Other consumers' Odds API usage and credentials are untouched.

## First live run (2026-09-23) and v1.1.1

The first live dispatches (runs 35925944608 and 35928372851, cycles from
2026-09-23T22:02Z) confirmed that the live APIs match the assumptions the
fake world encoded. Kalshi books arrive as `orderbook_fp` with
`yes_dollars`/`no_dollars`. Quotes use `*_dollars` fields, and all 6,196
rows were unit `dollars`. Quantities are fractional and are stored as
floats. Per-game markets tick in whole cents; sub-penny trades appeared
only on the `KXMLB-26-*` season futures. MLB `gameType` is `R` in both the
schedule and the feed. The Odds API headers carry
`x-requests-last/used/remaining`, and team names join exactly (0 ambiguous).
The run also exposed defects the fake world could not model. All are fixed
with regression tests, and nothing in collection semantics or the gates
moved:

| Defect (live evidence) | Fix |
|---|---|
| Research branch missing on the remote, so every persist was refused (`couldn't find remote ref`) and rows stayed on the runner | The workflow creates the branch on the remote before capture. A new step fails the job if rows did not persist. |
| `health_out.txt` written into the checkout made the workflow's own scope check fail every run | The console copy goes to `$RUNNER_TEMP` |
| 854 inning / extras markets (`KXMLBINNINGWIN-<ev>-<inning>-<side>`, `KXMLBINNINGTOTAL-<ev>-<inning>-<n>`, `KXMLBEXTRAS-<ev>-EXTRAS`) were marked `NOT_ELIGIBLE:UNPARSED`, so no book was requested | Game-only identity from the event-ticker segment (same exact parser). An unresolvable segment is still refused. |
| The doubleheader suffix differs by series (`...TORBAL` in KXMLBGAME vs `...TORBALG2` elsewhere for one game), so one game split into two "starved" halves | Coverage and starvation are keyed by the resolved `gamePk`. The event suffixes are listed alongside. |
| The exchange-wide trade tape hit the 50-page cap on about 6 of 15 minutes | `TRADES_PAGE_CAP = 400`. Hitting it is still recorded. |

Rows from before the fix carry `collectorVersion v1.1.0`; rows from after
carry `v1.1.1`.

**Open gate-design question (not changed here).** Kalshi lists a game's
families in stages. At 22:30 UTC, 7 of tomorrow's games had only
`KXMLBGAME` listed. The frozen `familyCoverage.starvedGameCycles <= 0` gate
counts that as starvation. The cycle includes tomorrow's games, so as
written this gate fails on almost every evening cycle. Whether coverage
should be judged only inside a predeclared pre-first-pitch horizon is a
research-design decision for the owner. It is recorded here, not made.

## Owner actions

- Merge the PR so the workflow becomes schedulable (repo-native path).
- Optionally deploy `external_runner.sh` with `ODDS_API_KEY` and
  `MRV_GIT_TOKEN` for the always-on path.
- Odds API: authorised at the designed rate, capped by the guard above (450/day ceiling, 5,000 reserve).

## Tests

`tests/research/mrv_collector/`: fake-world end-to-end cycle (no network):
full-universe book requests with no cap, reconciliation arithmetic,
exclusion reasons, family starvation, collector version on every row,
per-fetch timestamps, change suppression with anchor verification and
self-healing, book change capture, trade dedup, sportsbook joins and
ambiguity refusal, state transitions with first-seen / prev-seen /
source-timestamp semantics, partial and failed cycle preservation, retry
identity, health metrics and gate evaluation, cadence window rule,
attempt-without-manifest accounting, production/ALPHA-0002 isolation.
