# MLB Projection Foundation: live (prospective) operation

Research only. Every projection carries `authority: research_only`; nothing here changes a production model, pricer, ledger or
betting file. Entry point: `python -m mlb.live.run` (run from `research/projection_foundation`). Workflow:
`.github/workflows/projection-foundation.yml`. Data lives on the orphan branch `projection-data`, written only by that workflow.

## What runs, and when

| Stage | What it does | Writes |
|---|---|---|
| `capture` | MLB Stats API schedule (`hydrate=probablePitcher,lineups,team`) for yesterday/today/tomorrow (ET) | `availability/mlb/<date>/<HHMMSS>Z_<hash>.jsonl.gz` |
| `project` | frozen models for games starting in the next 20 h, from archived as-of availability only | `forecasts/mlb/<date>/...` (challenger **and** incumbent) |
| `publish` | latest view for the app | `projections/mlb/latest.json` (+ timestamped copy if content changed), `summary/mlb/latest.json` |
| `results` | FINAL box scores for archived forecasts; box-score history for model inputs | `outcomes/mlb/<date>/...`, `boxscores/mlb/<date>/...` |
| `settle` | settle-once; changed finals become correction records | `settlements/`, `corrections/` |
| `score` | 7-day (daily), 28-day (weekly) and season scorecards + segment checks | `scorecards/mlb/{daily,weekly,season}.{json,md}`, `$GITHUB_STEP_SUMMARY` |
| `guard` | `pf_core.archive_guard`: immutable directories may only gain files | - |

Schedule (UTC, March-November): 15:27 (matinees), 19:07 (~15:00 ET), 22:07 (~18:00 ET), 00:37 (~90 min before 10:10 pm ET
starts) run the whole chain; 08:37 runs `capture,results,settle,score,guard` (nightly settle). A stdlib `gate` job first asks
the schedule whether any playable game started in the last 30 h or starts within the window; if not (offseason, off-day) the
heavy job is skipped and the run ends green. `workflow_dispatch` has `dry_run` and `stage` inputs. On `pull_request` the full
chain runs with `--dry-run` (read-only token, temporary copy of the archive, nothing pushed).

Dormant safety: no games in the window is a successful no-op with the line
`MLB-PF SUMMARY: no MLB games in the window (offseason/off-day): no-op success`, and `projections/mlb/latest.json` says
`NO_GAMES_IN_WINDOW`. A failed fetch or stage fails the job; nothing is committed after a failure and the next run redoes the
idempotent stages.

## Fix 1: probable pitchers are captured PREGAME, never read from the post-game field

The feed's "probable pitcher" field is overwritten after the game and equals the actual starter 99.90% of the time
(RESULTS_MLB.md section 2), so a scratch cannot be measured from it. The runner now fetches the schedule with the
`probablePitcher` hydration before the game and archives it as availability observations (`kind: starter`):

| Situation | status | certainty |
|---|---|---|
| probable listed, club's lineup card not yet posted | `PROBABLE` | `PROJECTED` |
| that club's lineup card is posted (the card names the starter) | `STARTING` | `CONFIRMED` |
| a later capture names a different pitcher | `NOT_STARTING` for the replaced pitcher | same certainty as the new listing |

Evidence for treating "probable with a posted lineup" as confirmed: on the 527 archived pregame lineup captures
(`data/research/hitter_v2/pregame_lineups_2026.csv.gz`) the probable listed with the posted lineup equals the actual starter in
99.7% (1011 of 1014 games with a known starter). `projections` use `pf_core.availability.asof(...)` over these archived
observations; a capture at or after first pitch is ignored by construction. Projections for a game with no listed probable are
not made for that side.

## Fix 2: the incumbent's pregame projections are archived immutably

Only 40% of the rows in `data/pipeline/<date>/projections.json` were written before first pitch, because the file is
overwritten during the day (`meta.createdAt` is the last write). The production pipeline is **not** changed. Instead each
projection-foundation run reads the incumbent's current output and archives it (`model_role: incumbent`) through
`prospective.archive_forecasts`, which rejects any record generated or archived at or after the start:

* team runs and game total: `data/pipeline/<date>/projections.json`, accepted per game only if `createdAt < start` and
  `createdAt <= now < start` (a file rewritten after that game's first pitch is not a pregame forecast for it);
* pitcher outs / batters faced / strikeouts: the frozen production pitcher engine's rows in `data/shadow_pure/mlb_pitcher`
  (own `as_of`, mean + ladder);
* hitters (hits, total bases, RBIs, H+R+RBI): `data/edgelab/hitter_projection_snapshots/<date>.jsonl`
  (`distributionMean`, `projectionGeneratedAt`).

Incumbents carry a mean (and the pitcher ladder in `thresholds`) but no pmf, so scoring against them is MAE-based. A version
overwritten between two runs is lost to the archive, so the run cadence bounds how many intermediate versions are kept; the
scored forecast is the last one archived before the start.

## Batting orders and the hitter readiness rule

The posted lineup (`lineups.<side>Players`, batting order) is archived as `kind: lineup`, `status: STARTING`,
`certainty: CONFIRMED`, `detail.slot` (1-9), `detail.position`. A player on an earlier card but absent from a later one gets
`NOT_STARTING` (late scratch). Hitters are projected for the **confirmed** card only (a card with fewer than 9 names is treated
as not posted). Rule (`mlb.live.forecast.hitter_readiness`): a hitter projection may move beyond SHADOW only when its slot came
from a CONFIRMED pregame lineup observation and participation resolved to `CONFIRMED_ACTIVE`; otherwise participation is
UNVERIFIED/LIKELY_ACTIVE and the gate marks REVIEW. Every row stays `role: "shadow"` today (promotion is the owner's decision)
and carries `slot_source` and `shadow_exit_eligible` so the evidence can be counted. Clubs without a posted card still get the
old "assumed last lineup" projection in `latest.json`, labelled REVIEW/UNVERIFIED, but it is **not** archived as a forecast.

## Results

Final box scores (`/api/v1/game/<pk>/boxscore`, status Final in the schedule) become outcome rows for every archived forecast
entity. A pitcher forecast is a starter forecast: `participated` is true only if he started; a listed probable who did not
appear (`did_not_appear`) or who only relieved (`relief_appearance`) is void. A hitter participates only if he started with at
least one PA. Postponed/cancelled games are voided. Actual starters we did not forecast are recorded too (`forecasted: false`)
so the scorecard counts starter swaps. Finals of the last 3 days are re-read; a changed stat is a new outcome row, which
`prospective.settle` turns into one correction record per forecast while the original settlement stays untouched.

## Keeping model inputs current

The committed research datasets stop at the last backfill. The results stage therefore archives the box score of every final
game of the last 21 days (`boxscores/mlb/`, write-once) and projections append those games to the base frames with the base
schemas (`mlb/live/history.py`), only for games that started before `now - 3 h`. Per-PA events are not archived, so the
hand-specific strikeout factor stops updating at the last committed per-PA game. In live mode the factor is evaluated for the
forecast game too (`project._team_vs_hand_k_live` adds zero-event rows; history is unchanged): the research projector fell
back to the overall team K factor for a game not yet in the per-PA table while the evaluated model used the hand-specific one.

## Priority families and scorecard segments

Archived pitcher families: batters faced, outs, strikeouts (READY historically). Hitter families (plate appearances, hits,
total bases, home runs, runs, RBIs, H+R+RBI), team runs and game total are archived as SHADOW. The scorecard adds segment
checks for the priority pitcher families: opener flag, short rest (<= 4 days), season debut, layoff return, early exit
(BF <= 12, outcome-defined), starter certainty at forecast time (CONFIRMED vs PROJECTED), and counts of starter
no-shows, relief appearances and unforecast actual starters.

## Unverified until Opening Day

See the final report of the live-integration task; the list is kept next to the measured results there.
