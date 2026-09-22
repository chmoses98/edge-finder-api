# MRV prospective collector v1

**RESEARCH ONLY. READ-ONLY. Parallel to, and isolated from, the MLB-ALPHA-0002
collector. Changes nothing in production (eligibility, recommendations,
staking, wager routing, settlement, execution) and nothing in the ALPHA-0002
corpus or its frozen shadows C01-F5REV / C03-BOOKIMB.**

Identity: `COLLECTOR_ID = MRV_PROSPECTIVE_COLLECTOR`, `COLLECTOR_VERSION = v1.0.0`,
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

## Health and research-readiness gates (`health.py`, frozen `MRV_READINESS_GATES_V1_2026_09_22`)

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
| sample: unique games / dates / contracts per core family | ≥ 60 / ≥ 10 / ≥ 80 |

`infrastructureHealthy` = every non-sample gate passes; `researchReady` =
all gates pass. **Until `researchReady` is true, no MRV inference run is
authorised on this corpus.** The thresholds are not to be lowered because
collecting the data is inconvenient.

## Owner actions

- Merge the PR so the workflow becomes schedulable (repo-native path).
- Optionally deploy `external_runner.sh` with `ODDS_API_KEY` and
  `MRV_GIT_TOKEN` for the always-on path.
- Decide the Odds API budget (3 credits/cycle) against other consumers.

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
