# MLB starting-pitcher PURE shadow collection (W2 Phase 2)

**Status: research shadow only.** Nothing here promotes, publishes, stakes or grants authority. The champion
(`lib/research/pitcher_prop_projection.py` + frozen params) is unchanged and runs as-is.

- **Collector:** `scripts/research/mlb_pitcher_shadow.py`
- **Store:** `lib/research/shadow_pure_store.py`
- **Workflow:** `.github/workflows/research-mlb-pitcher-shadow.yml`
- **Gate:** vendored `tools/pure_gate/`, from sift-sports-intelligence 558c592, sha256-pinned in
  `VENDORED_FROM`

## Arms and statistics

| Arm | Version | Validation label |
|---|---|---|
| champion | `pitcher_prop_projection@params_asof_2026-10-07`, on sports-only regular-season history | `RESEARCH_SHADOW_INCUMBENT` |
| challenger | `mlb_pitcher_workload_joint_v1` (#283) | `RESEARCH_SHADOW_ACCEPTED_CHALLENGER_REGULAR_SEASON` |
| baseline | `naive_recent5_v1` | `RESEARCH_SHADOW_BASELINE` |

**Statistics.** Each arm covers `pitcher_outs`, `pitcher_batters_faced` and `pitcher_strikeouts` from **one**
joint distribution per arm. For the challenger the chain is outs → BF | outs → K | BF. Each row is one
`pure_forecast.v1` row with its ladder inside it: outs 9–21, BF 15–30, K 2–10. Each row also carries quantiles
0.1, 0.25, 0.5, 0.75 and 0.9, and `model_frozen_hash`.

**Postseason games** (game types F, D, L, W) are collected, but every arm's rows are labelled
`BLOCKED_NOT_VALIDATED_POSTSEASON`:

- The challenger is validated for the regular season only.
- The champion's postseason path carries a frozen `postseason_shift_outs` that was fitted on settlement-evidence
  values. Its rows say so in `x_dependency_note`.

## Captures

A cheap gate runs every 10 minutes from 12:07 to 04:57 UTC (see *Workflow modes*), so the pregame window
(60 minutes, or 25 minutes when lineups are late) gets several chances even with the usual schedule delay.
Each game gets at most one capture per window:

| Window | When |
|---|---|
| `morning` | The game's official date, from 08:00 ET, and more than 120 minutes before first pitch. |
| `pregame` | From T−80 to T−20 minutes. The capture is taken as soon as both lineups are posted; otherwise it is taken at T−45 minutes or later, labelled lineups-not-posted. |

**Refusals and checks:**

- The collector refuses at or after first pitch, and for any game that is not in `Preview` state.
- It asserts `source_max_observed_at < as_of < kickoff` and every `feature_lineage[].observed_at < as_of`.
- It refuses a regular-season game whose sports history is more than 3 days stale
  (`history_stale_regular_season`). For 2027, the research cache has to be refreshed first, for example with
  `research-multiseason-starter-workload-backtest.yml`.

**Probable starter.** The source is the MLB StatsAPI schedule with `probablePitcher` and `lineups` hydrated.
The fetch time is stored per game in `runs/…json` and in each row's lineage (`probable_starter_id`).

## Record families

All of these live under `data/shadow_pure/mlb_pitcher/`. Every file is written once, named by `captured_at`,
and hash-chained in `MANIFEST.jsonl`.

| Family | Contents |
|---|---|
| `projections/<date>/<ts>_<window>__<arm>.jsonl` | `pure_forecast.v1` rows, one file per arm. They pass `pure_gate validate --require-v1` before they are written. |
| `market/<date>/<ts>_<window>.jsonl` | Contract listing (first-seen time) and the latest quote at or before `as_of`, **copied read-only** from `data/edgelab/{markets,observations}`. This file is never read by any arm. |
| `runs/<date>/<ts>_<window>.json` | Probable-starter source and timestamps, window decisions, and exclusions (the population accounting). |
| `outcomes/<date>/<ts>.jsonl` | One sports outcome row per player-game-stat, from StatsAPI `feed/live`, in the `pure_gate` outcome format. `played` means the pitcher started; the arms are conditional on starting. |
| `outcome_provenance/<date>/<ts>.jsonl` | URL, `fetched_at`, payload sha256, game end, actual starters, projected-but-not-starting, unprojected starters. |
| `reconciliation/<date>/<ts>.jsonl` | Projection ↔ outcome join. Also flags any mismatch between the exchange resolution value and the sports value. That flag is a check only. |
| `evaluations/<ts>.json` | `pure_gate compare` on the full eligible population and on the pregame market-listed cohort, per window and per season phase, clustered by game. |

**Pregame market-listed cohort.** These are player-game-stats whose contract was first seen in the edgelab
capture outputs at or before that projection's `as_of`. The cohort is defined pregame, never from resolution.

**Missed windows.** Once collection has started (the first non-dry-run capture), every window that closes
with no capture is written once to `missed/<date>/<detected_at>.jsonl` (`reason = NO_CAPTURE_IN_WINDOW`, with
the window's close time and the game status at detection). It is never captured late or back-filled. A
postponed, cancelled or suspended game is not a miss, and neither is the morning window of a game that already
has its pregame capture. A failed run shows up the same way once the window it should have taken has closed.

**Append-only CI.** `tests/research/test_shadow_pure_append_only.py` fails if any file under `data/shadow_pure/`
that exists on `origin/main` is modified or deleted. `MANIFEST.jsonl` may only grow by appending. The test also
fails if a manifest chain does not verify, or if the vendored gate's sha256 drifts.

## Workflow modes

**Scheduled gate.** `gate` checks out only `lib/research`, `scripts/research` and the store (sparse and
blobless, no install) and runs `scripts/research/mlb_pitcher_shadow_gate.py`. It uses the collector's own
window logic (`lib/research/mlb_pitcher_shadow_plan.py`, standard library only) on the live StatsAPI schedule
and says `work=true` only when a capture window is due, a missed window needs recording, a captured game's
outcome is pending (kickoff 3 h to 7 days ago), or the daily 15:xx UTC evaluation has not been written. If the
schedule fetch or the gate itself fails, the full job runs anyway (fail open). Off-window gate runs take about
20 s.

**Full job checkout.** Sparse and blobless: only the paths the collector, its tests and the commit helper
read (traced with an audit hook) — about 0.36 GB instead of the ~3 GB full tree.

**Scheduled (when the gate says there is work), or `workflow_dispatch` with `dry_run=false`:**

1. The job runs capture → outcomes → reconcile → evaluate (once a day, 15:xx UTC) → verify.
2. It commits `data/shadow_pure/mlb_pitcher/` only, through `scripts/ci/git_data_commit.py`.
3. It runs in its own concurrency group and never cancels a run in progress.

**`pull_request`, or `workflow_dispatch` with `dry_run=true`:** a dry run.

1. It fetches live, captures every not-yet-started game under a forced `pregame` label into
   `$RUNNER_TEMP`, and fetches outcomes for yesterday's final games.
2. It runs reconcile, evaluate and verify, plus `pure_gate validate` and the unit tests.
3. It uploads an artifact and **does not commit**.

## Rollback

1. Delete `.github/workflows/research-mlb-pitcher-shadow.yml`. Nothing else reads the shadow store.
2. Optionally remove `data/shadow_pure/mlb_pitcher/`.

No production flow depends on any of these files.
