# MLB hitter engine audit (SIFT Wave 2, Phase 4)

**Status: research only.** Nothing in this audit changes production hitter outputs, the app export,
publication, settlement, staking or authority. The incumbent engine is read and rerun, never edited. Its code
is the hitter path at `origin/main` f7371b2.

**Reproduce.**

1. `python3 scripts/research/hitter_w2/incumbent_rerun.py --start 2026-08-12 --end 2026-09-27 --n-sims 500 --out <rerun.jsonl.gz>`
2. `python3 scripts/research/hitter_w2/decompose_incumbent.py --rerun <rerun.jsonl.gz> --records-cache <cache> --out data/research/hitter_w2/audit/incumbent_decomposition.json`
3. `python3 scripts/research/hitter_w2/rerun_fidelity.py <rerun.jsonl.gz> data/research/hitter_w2/audit/incumbent_rerun_fidelity.json`

The committed rerun output is `data/research/hitter_w2/audit/incumbent_rerun.jsonl.gz`. Every number below
comes from those JSON files.

## 1. What exists, and the research already done (not redone here)

| Item | Where | Result already on record |
|---|---|---|
| Hitter engine (pitch → PA → lineup Monte Carlo) | `docs/HITTER_SIMULATION_ENGINE.md`; `lib/research/{hitter_feature_context, hitter_pa_outcome_model, pitch_environment_model, pitch_sequence_model, hitter_contact_model, bullpen_exposure_model, lineup_game_simulator, hitter_market_distributions, hitter_board_builder}.py`; `scripts/build_hitter_projection_board.py` | Validated only on synthetic ground truth. Section 6 of that doc says no leak-free real backtest was possible. |
| Prospective snapshots | `lib/research/hitter_prospective_snapshot.py`, `.github/workflows/hitter-snapshot-scheduler.yml` → `data/edgelab/hitter_projection_snapshots/` | 18 dates, one row per Kalshi contract, probability at the listed threshold only. `distributionMean` is present only for 10-07 onwards. |
| MLB-RSCH-0028 | `docs/EDGELAB_MLB_RSCH_0028_HITTER_PROP_AUDIT.md` | PARITY with Kalshi (Brier 0.158 vs 0.155), over-confident (slope 0.86) |
| Calibration re-audit (2026-10) | `docs/EDGELAB_MLB_PROP_CALIBRATION_2026_10_HITTERS.md` | 5,913 one-per-ticker rows, 418 player-games. Hits level bias **+0.045** (model 0.316 vs realised 0.270); RBI **−0.059** (0.126 vs 0.185). Pooled and per-family Platt did **not** help out of sample, so it is not retried here. |
| Player props publication | `docs/EDGELAB_MLB_PLAYER_PROPS_2026_10.md` | The hitter path is published only for confirmed-lineup starters, as RESEARCH_PROJECTION |
| `research-multiseason-batting-backtest.yml` (MLB-RSCH-0012) | `data/research_cache/batting_backtest/` | **Team**-level batting box scores only. No player lines. Not usable for hitters. |

**No player-level batting outcomes existed in the repository** apart from:

* the Statcast pitch archive, 643 games from 2026-08-11. It has no RBI or runs.
* Kalshi settlement records. These cover the market-listed population only.

This lane therefore backfilled sports-only official data. The backfill used
`.github/workflows/research-hitter-w2-backfill.yml` and
`scripts/research/hitter_w2/fetch_pbp_backfill.py`, reading the MLB Stats API game feed for every regular-season
gamePk already committed in the schedule cache. Output is in `data/research/hitter_w2/mlb_feed/<season>/`:

| Season | Games scheduled | Extracted (Final) | Not final | Fetch failures | Batting rows | PA rows | fetched_at |
|---|---:|---:|---:|---:|---:|---:|---|
| 2024 | 2,430 | 2,425 | 5 | 0 | 50,669 | 182,657 | 2026-10-11T02:24–02:27Z |
| 2025 | 2,430 | 2,426 | 4 | 0 | 50,808 | 183,154 | 2026-10-11T02:23–02:26Z |
| 2026 | 2,430 | 2,427 | 3 | 0 | 51,667 | 184,196 | 2026-10-11T02:18–02:19Z |

**Checks.**

* Plate appearances reconstructed from the play-by-play equal the box-score PA total exactly. For 2025 both
  are 182,695.
* After treating inning-ending plays as stranding runners, every reconstructed PA satisfies the base-out
  run-accounting identity: runners before + batter = runs + outs added + runners after (or left on base).
* Kalshi settlement `actualValue` agrees with the box score in 99.8% of cases: H 12,312/12,333, TB
  12,249/12,271, RBI 11,817/11,823, H+R+RBI 11,857/11,879.

## 2. How each input enters the incumbent

| Input | How the incumbent uses it |
|---|---|
| **Expected PA** | Not modelled directly. It emerges from `lineup_game_simulator.simulate_game`:<br>• always **9 full innings for the batting team**, with no home-team bottom-9th skip, no walk-off and no extra innings;<br>• **no pinch-hitting or substitution**;<br>• the other 8 lineup spots draw from their season rates, where the "season" is the archive window (see the next rows). |
| **Batting-order slot** | Taken from the confirmed lineup (`lineupContext.order`). It sets the target's position in the lineup cycle. |
| **Confirmed lineup** | Required. `lineupConfirmedOfficial` must be true or the hitter is not projected. Lineups come from slate captures (`lineupSource` e.g. `lineup_audit+rotowire`, flag from the MLB API). |
| **Opposing starter** | • Pitch-family mix from the Statcast archive as-of.<br>• `pitcherSavant` kPct/bbPct give a ±3 pp bounded resample.<br>• Hook: logistic on innings vs `avgIPperStart`. |
| **Bullpen** | **Generic**:<br>• bullpen pitch mix = `None`, a default mix;<br>• `bullpen_pitcher_quality` and `choose_bullpen_pitcher_hand` are never called;<br>• the starter's platoon and quality resample is applied to bullpen PAs too. |
| **Opponent-adjusted contact / K / BB** | • Per-pitch-family hitter PA outcomes are shrunk toward the archive-window "season", then toward a league prior. The archive began 2026-08-11, so the median is **65 archived PA** and the p90 is 134.<br>• The pitch-by-pitch sequence and contact model decide the outcome.<br>• **Platoon is a no-op.** `season_woba` is never passed: `_season_stats_from_baseline` drops wOBA, giving 0% of rows. `batSide` is null in every slate lineup, giving 100% of rows. |
| **Park** | Physical wall geometry in the contact model. There is no empirical park run factor. |
| **Weather** | Wind relative to park orientation only, down-weighted 0.3x. |
| **Defense / speed** | OAA and sprint speed snapshots enter the contact-outcome conversion |
| **H, TB, HR** | From the target hitter's simulated PA outcomes. TB and HR are coherent with H in every draw (checked: 100% of rows). |
| **RBI, R** | From the simplified baserunning, "advance exactly N bases":<br>• a single moves a runner on 2B only to 3B;<br>• there are no sacrifice flies, productive outs, errors, double plays or extra bases taken. |
| **Teammate correlation** | **None across hitters.** Each hitter is simulated in his own independent game, so teammates' RBI and R are not reconciled with a common team run total. |

## 3. Market dependency inventory

| Kind | Finding |
|---|---|
| Direct market features in the engine | **None.** The hitter feature context and simulator never read `odds`, `kalshi`, `teamTotals`, `modelProb`, `totalEval` or `pinnacleVF`, although those keys sit in the same slate game records. The keys they do read are listed by `grep -o '.get("…")'` on `hitter_feature_context.py`. |
| **Market-selected population** | **Yes, at two gates:**<br>• `_build_rows_for_game` returns early for a game with no Kalshi hitter markets;<br>• `build_hitter_projection_rows` returns `NO_ARCHIVED_CONTRACTS` unless the hitter has a matched Kalshi contract.<br>The production snapshots therefore cover only market-listed hitters at market thresholds. |
| Snapshot timing | The scheduler runs against the latest committed Kalshi snapshot (`hitter-snapshot-scheduler.yml`). |
| **Settlement-sourced observations** | Every prior real-data evaluation scored against Kalshi settlement records:<br>• RSCH-0028;<br>• the 10-07 re-audit;<br>• the `hitter_projection_audit` module.<br>The values themselves come from MLB StatsAPI and agree with the box score (above), but rows exist only where a market was listed and settled. |
| Evaluation thresholds | Earlier audits scored the listed ladder rungs, many per player-game |
| Price-fitted parameters | **None found.** League priors are hand-set constants, and the contact model is tuned to the FB-HR rate. The Platt recalibration was researched on prices and outcomes but not applied. |
| Vegas-derived totals | Not used by the hitter path. The slate's `modelProb` / `teamTotals` blocks are not read. |

## 4. Rerun on the full eligible population

* **Population.** Every confirmed-lineup starter in every game whose official lineup appeared in a pregame
  slate capture. Capture time is taken from the file name, strictly before first pitch. The window is
  2026-08-16 to 09-27, because the Statcast archive starts 08-11.
* **Coverage.** 1,015 team-games gave 9,135 hitter runs:
  * 8,903 PROJECTED;
  * **232 MODEL_ERROR**, a `TypeError` in `wall_distance_at_spray_angle`. Production records the same error
    class (MLB-RSCH-0028 excluded 211 `MODEL_ERROR` rows).
* **Matched to box-score starters:** 8,883 player-games in 519 games. 20 were not a starter in the box score,
  and 42 had a box-score slot different from the pregame lineup but were kept.

**Fidelity.** On the 2,306 market-listed player-game-thresholds that production actually priced, the rerun's
probability minus the archived snapshot's probability has these means:

| Family | Mean signed difference | Mean absolute difference |
|---|---:|---:|
| H | +0.0011 | 0.015 |
| TB | +0.0005 | 0.015 |
| RBI | −0.0007 | 0.013 |

The differences are Monte Carlo noise plus the missing wind (`weather.json` has no history). The rerun
reproduces production.

### 4.1 Level bias, full eligible population (8,883 player-games, 519 games; 95% game-clustered CI)

| Stat | Incumbent mean | Actual mean | Bias | Ratio |
|---|---:|---:|---|---:|
| PA | 4.437 | 4.016 | **+0.421** [+0.389, +0.453] | 1.105 |
| H | 1.024 | 0.889 | **+0.135** [+0.114, +0.159] | 1.152 |
| TB | 1.429 | 1.445 | −0.015 [−0.057, +0.025] | 0.989 |
| HR | 0.086 | 0.122 | **−0.037** [−0.044, −0.029] | 0.700 |
| RBI | 0.306 | 0.457 | **−0.151** [−0.171, −0.132] | 0.669 |
| R | 0.374 | 0.478 | **−0.104** [−0.124, −0.084] | 0.782 |
| BB | 0.461 | 0.349 | +0.112 [+0.098, +0.125] | 1.322 |
| K | 1.130 | 0.870 | +0.260 [+0.240, +0.280] | 1.298 |

The previously observed hits over-projection (+0.045 in probability on listed rungs) and RBI under-projection
(−0.059) are confirmed on the full population. At the mean level they are larger: **H +15%**, **RBI −33%**.
TB looks unbiased only because the excess hits are offset by too few HR.

### 4.2 Hits: PA count vs per-PA rate vs starter/bullpen

`Ĥ − H = (PÂ − PA)·r̂ + (PA·r̂ − H)`, where `r̂ = Ĥ/PÂ`. The rate part is split by the PAs actually taken vs the
starter and vs the bullpen, from the play-by-play.

| Component | Per player-game [95% CI] |
|---|---|
| Total H bias | +0.135 [+0.114, +0.159] |
| PA-count part | **+0.097** [+0.089, +0.104] (72%) |
| Per-PA rate part | +0.039 [+0.021, +0.056] (28%) |
| … rate vs starter | +0.010 [−0.004, +0.024] (CI includes 0) |
| … rate vs bullpen | **+0.029** [+0.018, +0.040] |

**H/PA.**

| Context | Incumbent | Actual |
|---|---:|---:|
| All PAs | 0.2307 | 0.2212 |
| vs starter | 0.2315 | 0.2274 |
| vs bullpen | 0.2298 | 0.2129 |

The share of PA vs the starter is 0.557 forecast vs 0.575 actual.

**PA by slot.** The incumbent is too high in every slot, from +0.33 to +0.56. The excess is largest at the
bottom of the order: slot 9 is 4.01 vs 3.45 actual.

**PA by side.**

| Side | Forecast | Actual |
|---|---:|---:|
| Away | 4.436 | 4.096 |
| Home | 4.438 | 3.937 |

The incumbent gives the home team the same PA as the away team. That is the missing bottom-9th skip and
walk-off.

### 4.3 RBI: PA vs runners on base vs conversion

* Total RBI bias: −0.151, made of:
  * HR-RBI part: −0.044 (too few HR);
  * non-HR part: **−0.107** [−0.122, −0.094].
* Non-HR RBI = PA × (runners on base per PA) × (non-HR RBI per runner on base). The log-ratio of forecast to
  actual is −0.515, split as follows.

| Factor | Incumbent | Actual | Log-ratio | Share |
|---|---:|---:|---:|---:|
| PA | | | +0.100 | −19% |
| Runners on base per PA | 0.715 | 0.616 | +0.148 | −29% |
| Non-HR RBI per runner on base | **0.050** | **0.108** | **−0.763** | **148%** |

The RBI under-projection is a **baserunner-conversion mechanism** error. The simulator drives in half as many
runners as real baseball, and too many PAs with too many runners on base only partly offset it.

### 4.4 Runs

| Measure | Incumbent | Actual |
|---|---:|---:|
| Times on base (H + BB + HBP) per player-game | 1.484 | 1.286 |
| Runs per time on base | **0.252** | **0.371** |

The log-ratio for runs is −0.246, made of +0.143 from times on base and −0.389 from the scoring rate.

### 4.5 Observation choice

| Cohort | n | H bias | RBI bias | PA bias |
|---|---:|---|---|---|
| Market-listed (a PROJECTED snapshot row existed pregame) | 241 | +0.126 [+0.009, +0.248] | −0.193 [−0.323, −0.078] | +0.371 [+0.215, +0.519] |
| Not listed | 8,642 | +0.135 [+0.110, +0.158] | −0.150 [−0.171, −0.130] | +0.422 [+0.388, +0.453] |

**Observation choice is not the cause.** The bias has the same sign and similar size whether or not a market
existed. Settlement values agree with the box score (Section 1).

The earlier audits' "+0.045 / −0.059" are probability-level gaps at listed rungs. They understate the mean
error because most listed rungs sit in the tails.

## 5. What the decomposition isolates, and what it rules out

1. **PA count:** 72% of the hits bias. It comes from the 9-inning, no-substitution, no-walk-off simulator plus
   too many BB.
2. **Per-PA rate vs the bullpen:** the generic bullpen. The rate vs the starter is not significantly biased.
3. **RBI and R conversion:** the "advance exactly N bases" baserunning, plus too few HR.
4. **Thin per-hitter history:** 65 archived PA median, and platoon silently disabled.

**Not supported as the cause:** observation or market selection, and the settlement source. A blanket
recalibration was already rejected (10-07 audit).

The challenger is preregistered in `docs/research/HITTER_W2_PREREG.md` and changes only items 1 to 4.

## 6. Data gaps

| Gap | Status |
|---|---|
| Postseason | BLOCKED_DATA: not in the backfill |
| Weather history | BLOCKED_DATA for the incumbent rerun's wind input: `data/weather.json` has 3 commits and no history |
| Timestamped confirmed lineups for 2024–2025 | BLOCKED_DATA. The challenger uses the official starting lineup as the confirmed-lineup proxy. 2026 slate captures allow a confirmed vs unconfirmed check. |
