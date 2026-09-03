# 10-Day MLB Production Calibration Review — 2026-09-03

Status: **COMPLETE. MEASUREMENT ONLY.** No refitting, no new candidate maps,
no production probability/threshold/staking changes. This is the scheduled
periodic calibration review requested for the current production window; it
extends (never rescoring) the MLB-RSCH-0022 / MLB-RSCH-0023 / frozen-forward
lineage with the newest settled data and reports whether that evidence
changes the standing conclusions.

Scope note: MLB-ALPHA-0002 scheduling, the accumulation clock, order-book
capture, queue observation, alpha candidates, and research-data branches
were **not touched**. This review found no direct production-data
dependency problem that would require touching them.

## A — Last calibration state

- **Last completed production probability calibration review:** MLB-RSCH-0022
  ("Production Probability Calibration & Market-Relative Skill"),
  registered/run 2026-08-28T19:50:00Z. Companion recalibration attempt
  MLB-RSCH-0023 (retired) and the unrelated run-mean-calibration retest
  MLB-RSCH-0025 (retired) sit in the same lineage; see `docs/EDGELAB_CALIBRATION.md`
  for the separate PlacedBet-ledger calibration engine (Milestone 2), last
  regenerated as part of this review (§C).
- **Data cutoff used by MLB-RSCH-0022:** DEV = settle ≤ 2026-08-17, VAL =
  2026-08-18..2026-08-28. FORWARD = settle > 2026-08-28, preregistered and
  explicitly **not computed** in that run.
- **Findings established (MLB-RSCH-0022):** Kalshi's own contemporaneous
  price beats production's archived probability on Brier/log-loss/ECE
  overall and in every one of 13 families; systematic overconfidence
  (probabilities too extreme in both tails); least-bad family
  first_inning_run (NRFI/YRFI, statistically tied with market); worst
  families pitcher_outs and pitcher_strikeouts; complete-input rows
  (`dataQuality=full` / `lineupConfirmationState=CONFIRMED`) show roughly
  half the model-market gap of incomplete-input rows.
- **Recalibration candidates rejected:** MLB-RSCH-0023's global (R1) and
  tiered (R2) logit-affine shrink maps both **failed VAL replication**
  (DEV Brier improved sharply, VAL Brier worsened) — retired at LEVEL 0,
  no validated correction. MLB-RSCH-0025's V2-retested mean-calibration
  candidate (C1, a different target — expected-run means, not win
  probabilities) passed DEV+VAL but **did not confirm on its own 2026
  holdout** — also retired, REJECT.
- **Forward holdout preregistered:** yes — settlement date strictly after
  2026-08-28 (`lib.edgelab.research.frozen_forward_scorer.FORWARD_START_DATE`),
  tracked by the dedicated, idempotent, non-refitting
  `scripts/edgelab/run_frozen_forward_scorer.py` / `docs/EDGELAB_FROZEN_FORWARD_SCORECARD.md`.
- **Has it already been scored?** Partially, by design. The frozen forward
  scorer uses a **checkpoint ladder** (CHECKPOINT_0..4), explicitly built to
  be rerun freely as the archive grows without ever refitting anything —
  reaching a checkpoint changes only what may be *claimed*, never a model.
  Before this review it stood at **CHECKPOINT_2 (INTERMEDIATE)** — 913 rows /
  47 games / 3 dates (2026-08-29..08-31) — status
  `INTERMEDIATE_UNCONFIRMED`. **Confirmation requires CHECKPOINT_3** (≥1,000
  rows, ≥60 games), which has not been reached. This review reran the frozen
  scorer (§H) and it produced **byte-identical output** — no new settled
  forward data exists beyond 2026-08-31 in the current archive, so the
  holdout's status is unchanged, not "rescored" in the forbidden sense: it
  is still genuinely open below its confirmation threshold, and nothing
  about it was refit or reinterpreted.

## B — Current data health

Read-only audit of `data/edgelab/{model_evaluations,settlements,recommendations,bets}/`,
2026-09-03:

| | |
|---|---|
| Model-evaluation daily partitions | 34 files, 2026-08-01 .. 2026-09-03 (today's slate present but unsettled) |
| Settlement daily partitions | 29 files, 2026-08-11 .. **2026-08-31** (no settlement partitions yet for 09-01/09-02/09-03 — expected, not a gap: those games haven't settled) |
| Recommendation daily partitions | 26 files (gaps 08-14, 08-17..08-21 — pre-existing, unrelated to this review) |
| Placed-bet ledger | 1 file, 385 records, 298 decided (144 WIN / 154 LOSS / 1 VOID / 86 pending) |
| Settled Kalshi tickers (all MLB) | 118,438 |
| EVALUATED production rows (model_evaluations, both probabilities present) | **8,821** (0 excluded for missing probability — up from 7,052 at the MLB-RSCH-0022 cutoff) |
| Rows with `modelFairProbability` | 8,821 / 8,821 (100% of EVALUATED rows) |
| Rows with `marketImpliedProbability` | 8,821 / 8,821 (100%) |
| Unique evaluated tickers | 5,695; of these **1,367 have no settlement yet** (future/unresolved games, not a data defect) |
| Audit rows (last-per-ticker × settled, MLB-RSCH-0022 methodology) | **4,328** across **370 games**, settle dates 2026-08-02..2026-08-31 |
| Family coverage (audit rows) | team_total 1,269; pitcher_strikeouts 809; winning_margin 502; game_total 515; inning_result 425; inning_total 273; first_inning_run 259; game_result 167; pitcher_outs 109 |
| `dataQuality` coverage | full 854 (19.7%) / none 107 (2.5%) / unset 3,367 (77.8%) |
| `lineupConfirmationState` coverage | CONFIRMED 854 / UNCONFIRMED 107 / UNKNOWN 3,367 (identical partition to dataQuality — the two fields co-occur exactly) |
| Doubleheader / gameId collision | No new pattern found; the known, previously-documented gameId-collision gap (`docs/EDGELAB_PHASE1.md`) is unchanged by this review |
| Non-YES/NO settlement records seen | 3,668 (correctly excluded, not silently dropped) |

**Material data-quality finding (new, this review):** the PlacedBet-ledger
calibration engine (`lib/edgelab/calibration.py`, §C) computes
`expectedWinRate = AVG(modelFairProbability)` and displays it via a
percentage formatter that assumes a 0–1 fraction. All 36 decided bets that
now carry `modelFairProbability` (up from 0 at the last Milestone-2 writeup)
store it on the **0–100 archived production scale** (e.g. `37.8`, `45.56`,
`72.0`), consistent with production's own convention — but the formatter
multiplies by 100 again, producing nonsensical values like `3217.6%`,
`3911.2%`, `2685.0%` in several buckets of `data/edgelab/reports/phase2_calibration.md`
(edge buckets `0-2`/`2-4`, confidence `MEDIUM`, several CLV buckets). This is
a **pipeline formatting/scale defect in the Milestone-2 calibration engine,
not a calibration finding** — the underlying settlement/edge/CLV/ROI numbers
in the same report are unaffected and reliable; only `expectedWinRate` and
the derived `calibrationError` column are wrong wherever they show an
implausible (>100%) value. Flagged for an engineering fix; not corrected
here (out of scope for a measurement-only review, and not a production
betting code change this review is chartered to make). The reliable source
for production-probability calibration in this review is §D, which reads
`modelFairProbability`/`marketImpliedProbability` directly off
`model_evaluations` records (production's own archived scale, /100), not
through this buggy join.

## C — Canonical calibration engine

Ran the existing engine, not a new one:

```
python3 scripts/edgelab/run_calibration.py
```

Regenerated `data/edgelab/analytics/latest_calibration.json` and
`data/edgelab/reports/phase2_calibration.md` against the current ledger
(298 decided bets, up from 14 at the last documented run in
`docs/EDGELAB_CALIBRATION.md`). Season-to-date: **n=298, win rate 48.3%, ROI
−6.8%, avg CLV +0.158, status `CALIBRATED`** (≥100). By recommendation path:
`RECOMMENDED_AND_BET` n=181, win 49.2%, ROI **−4.8%**, avg CLV **−0.376**,
status `CALIBRATED`; `MANUAL_BET` n=69 (win 47.8%, ROI −13.1%); `MODEL_BET`
n=36 (win 44.4%, ROI −2.2%). By market family: `inning_result` n=131 (ROI
−9.8%), `team_total` n=61 (ROI −8.4%), `game_result` n=52 (ROI −5.8%),
`pitcher_strikeouts` n=21 (ROI +10.5%, `DESCRIPTIVE_ONLY`) — no family
reaches `CALIBRATED` status individually except via the aggregate rows.
Sample-size labels applied exactly as specified (<20 `INSUFFICIENT_SAMPLE`,
20–99 `DESCRIPTIVE_ONLY`, ≥100 `CALIBRATED`); no small bucket is treated as
evidence. See §B for the `expectedWinRate`/`calibrationError` scale defect
that limits trust in those two specific columns this run.

## D — Production probability audit on new data

Extended MLB-RSCH-0022's own methodology (its committed functions, imported
read-only — nothing refit, no new experiment registered, its frozen artifact
and DEV/VAL split **left untouched**) across the corpus that has accumulated
since 2026-08-28, reported as new, separate, descriptive windows (never
merged into the frozen DEV/VAL/pooled figures):

**Sanity check** — re-running the identical DEV+VAL join (settle ≤
2026-08-28) now returns n=3,331/games=308 versus the committed artifact's
n=3,137/games=293: a modest amount of late-arriving archive completion for
already-closed dates (Brier 0.2254 model / 0.1696 market vs. the committed
0.2268 / 0.1719 — same conclusion, not materially different). This does
**not** touch the FORWARD window and does not reopen MLB-RSCH-0022's
findings; it just confirms the corpus is stable and directionally identical.

**Overall / season-to-date (all settled, 2026-08-02..2026-08-31, n=4,328,
370 games):**

| Metric | Production model | Kalshi market |
|---|---|---|
| Brier | 0.2239 | 0.1664 |
| Log loss | 0.6730 | 0.5437 |
| ECE | 0.0998 | 0.0422 |

Paired Brier delta (model − market): **+0.0575**, 90% game-clustered CI
[+0.0434, +0.0726], p ≈ 0 — materially unchanged from MLB-RSCH-0022's
original pooled +0.0549. **Kalshi still beats production on proper scoring,
overall and directionally in every family.**

Season-to-date family results (paired Brier delta, model − market; negative
would mean model beats market):

| Family | n | games | Model Brier | Market Brier | Δ | FDR-sig (10%) |
|---|---|---|---|---|---|---|
| first_inning_run | 259 | 259 | 0.2584 | 0.2490 | +0.0094 | no |
| game_result | 167 | 113 | 0.2473 | 0.2102 | +0.0370 | yes |
| game_total | 515 | 80 | 0.2155 | 0.1743 | +0.0412 | yes |
| inning_total | 273 | 39 | 0.2400 | 0.1992 | +0.0408 | no |
| inning_result | 425 | 132 | 0.2128 | 0.1666 | +0.0462 | yes |
| team_total | 1,269 | 343 | 0.2332 | 0.1843 | +0.0490 | yes |
| winning_margin | 502 | 93 | 0.2129 | 0.1456 | +0.0673 | yes |
| pitcher_strikeouts | 809 | 64 | 0.2003 | 0.1005 | +0.0998 | yes |
| pitcher_outs | 109 | 62 | 0.2679 | 0.1607 | +0.1072 | yes |

Ranking is essentially identical to MLB-RSCH-0022's original: NRFI/YRFI
least-bad and statistically tied with the market; pitcher props still worst.

Season-to-date probability bands (model):

| Band | n | mean model prob | outcome rate | bias |
|---|---|---|---|---|
| 0.0–0.2 | 1,361 | 0.093 | 0.230 | **−0.137** |
| 0.2–0.4 | 1,079 | 0.294 | 0.396 | **−0.102** |
| 0.4–0.6 | 913 | 0.501 | 0.483 | +0.018 |
| 0.6–0.8 | 551 | 0.678 | 0.590 | **+0.089** |
| 0.8–1.0 | 424 | 0.898 | 0.804 | **+0.094** |

Nearly identical in shape and magnitude to MLB-RSCH-0022's original bands
(−0.139/−0.108/+0.024/+0.105/+0.100). **The systematic overconfidence
signature is materially unchanged season-to-date.**

## E — Recent-window view (kept separate from season-to-date)

**Recent review window = new prospective evidence since the last completed
calibration cutoff (settle > 2026-08-28)** — this equals the frozen forward
scorer's own forward corpus: n=997, 62 games, dates 2026-08-29/30/31 (the
data ceiling — no settlements exist yet past 08-31; see §B).

| Metric | Production model | Kalshi market |
|---|---|---|
| Brier | 0.2190 | 0.1557 |
| Log loss | 0.6834 | 0.5000 |
| ECE | 0.0939 | 0.0539 |

Paired Brier delta: **+0.0633**, CI [+0.026, +0.103], p=0.002 — same
direction and similar magnitude to season-to-date (+0.0575) and to the
original MLB-RSCH-0022 pooled figure (+0.0549). **No improvement, no
deterioration outside noise** — trend vs. the prior audit is flat.

Family-level (recent window; every family here is `DESCRIPTIVE_ONLY` or
just above by row count, and below `MIN_GAMES_PRIMARY`=30 games in most
cases — treat directionally only):

| Family | n | games | Δ Brier | Note |
|---|---|---|---|---|
| first_inning_run | 33 | 33 | +0.0037 | still least-bad |
| game_result | 34 | 21 | +0.0107 | |
| inning_result | 98 | 21 | +0.0334 | |
| game_total | 133 | 17 | +0.0357 | |
| inning_total | 70 | 10 | +0.0498 | |
| team_total | 254 | 52 | +0.0477 | only family clearing both n≥100 and games≥30 here |
| winning_margin | 131 | 23 | +0.0763 | |
| pitcher_outs | 29 | 18 | +0.0877 | |
| pitcher_strikeouts | 215 | 18 | +0.1229 | still worst |

**Trailing 10 calendar days** (2026-08-24..2026-08-31 — the only 8 days with
settlement data inside a literal trailing-10-day window as of 2026-09-03;
today's own slate has not settled): n=3,362, 167 games. Model Brier 0.2187 /
market 0.1601, Δ **+0.0586** [CI +0.0409, +0.0768] — again statistically
indistinguishable from the season-to-date and forward-only figures. No
divergence between the "recent" and "trailing-10-day" views; they overlap
heavily with the same conclusion.

**Reading:** three additional settled dates (and 62 games) of genuinely new
evidence do not move the needle. This is expected — MLB-RSCH-0023 already
established that this data volume and shape does not support a validated
correction — and is itself evidence, not proof of stability, given the
small forward sample.

## F — Lineup / data-quality stratification

Retested on the newest data (season-to-date, n=4,328) and on the recent
window alone (n=997):

| Split | Recent window Δ Brier | Season-to-date Δ Brier |
|---|---|---|
| `dataQuality=full` / `lineupConfirmationState=CONFIRMED` | **+0.0289** (n=118) | **+0.0301** (n=854) |
| `dataQuality` unset / `lineupConfirmationState=UNKNOWN` | +0.0682 (n=876) | +0.0657 (n=3,367) |
| `lineupConfirmationState=UNCONFIRMED` | −0.0263 (n=3, too small to interpret) | +0.0199 (n=107) |

**This relationship REPLICATES, and holds up almost exactly at the same
magnitude as MLB-RSCH-0022's original finding** (originally +0.031 complete
vs. +0.064 incomplete; now +0.030 vs. +0.066 season-to-date, +0.029 vs.
+0.068 in the fresh recent window). Confirmed-lineup / full-data-quality
rows are consistently, reproducibly about **half** as miscalibrated as the
rest of the universe. This is real, directly useful bet-filtering
information — not a production probability fix, but a filter the manual
handicapping workflow can keep leaning on.

## G — Recommended-bet subset

Two corpora answer this from different angles; neither is mixed with the
full-universe numbers above:

1. **Actual placed-bet ledger** (`data/edgelab/bets/bets.jsonl`, §C):
   `RECOMMENDED_AND_BET` n=181 (now `CALIBRATED` by sample size), win rate
   49.2%, ROI **−4.8%**, avg CLV **−0.376**. This is real money outcomes on
   genuinely recommended-and-placed bets — a materially larger sample than
   the 14-decided-bet state documented previously, now large enough to be a
   real (if unflattering) signal: recommended bets are, if anything, running
   slightly worse on CLV than the ledger average (+0.158 season-to-date).
2. **Confidence-tagged market-evaluation rows** (the archived
   `confidence` field on `model_evaluations`, a weaker proxy for "genuinely
   recommended"): `HIGH` n=2 (still far too small — unchanged from
   MLB-RSCH-0022), `MEDIUM` n=149 season-to-date (paired Brier Δ +0.035,
   better than the pool average but still positive/model-worse), `PAPER`
   n=356 (Δ +0.015).

**Sufficient sample: YES for the placed-bet ledger's ROI/CLV read (n=181,
`CALIBRATED`), NO for a family-level or probability-calibration breakdown of
recommended bets specifically** (per-family recommended-bet counts are all
well under the 100-ticker/30-game interpretation floor). Do not read the
−4.8% ROI as proof recommended bets are worse than average without
family-level power to back it up — it is a real number, reported as
required, not yet a statistically resolved one.

## H — Forward holdout

Status as of this review: **CHECKPOINT_2 (INTERMEDIATE)**,
`INTERMEDIATE_UNCONFIRMED` — unchanged from before this review. Reran
`python3 scripts/edgelab/run_frozen_forward_scorer.py` (the script is
explicitly documented as idempotent and meant to be rerun freely as the
archive grows — this is not "rescoring a spent holdout," it never refits or
reinterprets a frozen parameter, and confirmation status only ever advances
past CHECKPOINT_3). **Result: byte-identical to the already-committed
`docs/EDGELAB_FROZEN_FORWARD_SCORECARD.md`** — no settlement data exists yet
beyond 2026-08-31, so there was nothing new to score. No new checkpoint was
reached; nothing was refit; the immutable frozen parameters (MLB-RSCH-0024
α=0.0004, MLB-RSCH-0026 β=0.9833/base=0.430536) were read, never touched.

- **MLB-RSCH-0022 reference finding on the forward window:** production −
  market Brier Δ = **+0.02501** [CI 0.0051, 0.0439] — CONTRADICTS nothing
  (same direction as the frozen finding: model worse), but status is
  `INTERMEDIATE_UNCONFIRMED`, not `FORWARD_SUPPORTS_FROZEN_FINDING` (needs
  CHECKPOINT_3).
- **MLB-RSCH-0024 frozen α** (market-residual shrink): Δ vs. raw market
  = **+0.000004** (essentially zero) — `INTERMEDIATE_UNCONFIRMED`.
- **MLB-RSCH-0026 frozen β** (Kalshi band shrink): Δ vs. raw market =
  **−0.000107** [CI −0.0003, +0.0001] — directionally favorable but CI
  spans zero and status is `INTERMEDIATE_UNCONFIRMED`.
- **Does any frozen candidate survive?** No candidate has reached
  `FORWARD_SUPPORTS_FROZEN_FINDING`. None can, structurally, below
  CHECKPOINT_3 — the decision rule (`lib.edgelab.research.frozen_forward_scorer.decide_status`)
  will not emit that status yet regardless of the point estimate.

## I — Retired recalibration stays retired

Overconfidence is confirmed again (§D, §E) at essentially the same
magnitude as MLB-RSCH-0022 found it. Per the standing governance:
MLB-RSCH-0023's logit-affine maps already failed VAL replication once and
are **not revived** here — re-finding the same bias is not new evidence for
a map that specifically failed to transport across exactly this kind of
week-to-week family-mix shift. MLB-RSCH-0025's run-mean-calibration
candidate is unrelated in target (expected runs, not win probability) and
also stays retired (holdout did not confirm). No new production calibrator
was fit in this review. If a genuinely different approach becomes worth
researching (e.g., MLB-RSCH-0023 itself proposed re-running its exact design
once 2–3 more weeks of a composition-balanced archive accumulate), that is a
**separate, new, preregistered research experiment** — not something to
opportunistically fit inside this measurement review.

## J — Economic interpretation

1. **Are model probabilities still too extreme?** Yes — the favorite-longshot
   overconfidence signature is present at essentially the same magnitude
   season-to-date and in the fresh recent window as it was at the original
   audit.
2. **Most miscalibrated families:** pitcher_outs (Δ +0.107) and
   pitcher_strikeouts (Δ +0.100) season-to-date — unchanged ranking from
   MLB-RSCH-0022.
3. **Closest to Kalshi:** first_inning_run (NRFI/YRFI), Δ +0.009, not
   FDR-significant — still the one family statistically indistinguishable
   from the market.
4. **Does confirmed-lineup/full-quality data materially improve
   reliability?** Yes, and it replicates almost exactly (§F): roughly half
   the model-market gap, both season-to-date and in the new evidence alone.
5. **Does the model beat Kalshi anywhere on proper scoring?** No family
   shows a negative (model-better) paired Brier delta season-to-date or in
   the recent window.
6. **Do the largest model-market disagreements still concentrate model
   error?** MLB-RSCH-0022's original disagreement-band table is unchanged by
   this review (not rescored here); nothing in the new evidence contradicts
   its caution against reading wide-disagreement rows as an edge.
7. **Is the confidence system aligned with realized outcomes?** Partially:
   `HIGH`/`MEDIUM` confidence rows show a smaller model-market gap than the
   unlabeled pool (§G item 2), consistent with confidence carrying some real
   information, but `HIGH` is still only n=2 — too small to certify.
8. **Are Bet Up To thresholds being driven by systematically overconfident
   probabilities?** The underlying probabilities feeding any such threshold
   remain overconfident in the same direction and magnitude documented in
   MLB-RSCH-0022; this review did not audit the threshold logic itself, but
   flags that its probability inputs are unchanged in their known bias.
9. **Does today's evidence justify changing how ChatGPT should weight the
   model during manual handicapping?** No new evidence to change the
   existing guidance: prefer complete-input rows (§F), distrust pitcher
   props and winning-margin edges most, treat NRFI/YRFI as the
   closest-to-market family, and do not treat the model's own stated
   probability as calibrated in either tail.

## K — Production change gate

**CALIBRATION DECISION: NO CHANGE — MISCALIBRATION EXISTS BUT NO VALIDATED FIX.**

Miscalibration (systematic overconfidence) is real, replicates on entirely
new evidence, and is materially unchanged in magnitude and shape from the
last completed audit. No validated correction exists: MLB-RSCH-0023's two
candidate maps already failed forward (VAL) replication once, and nothing in
this review's new data changes that verdict or justifies re-fitting them.
The forward holdout remains genuinely open (CHECKPOINT_2, confirmation
needs CHECKPOINT_3) and was not touched beyond a no-op idempotent rerun.
This does **not** rise to "PRODUCTION CHANGE WARRANTED" — that classification
requires materially stronger evidence than re-finding the same bias, and
none was produced here.

## L — Reports / GitHub

Regenerated calibration artifacts only; no production betting code changed.

- `python3 scripts/edgelab/run_calibration.py` → regenerated
  `data/edgelab/analytics/latest_calibration.json`,
  `data/edgelab/reports/phase2_calibration.md`.
- `python3 scripts/edgelab/run_frozen_forward_scorer.py` → reran (idempotent);
  output byte-identical to the already-committed
  `data/edgelab/analytics/latest_frozen_forward_scorecard.json` /
  `docs/EDGELAB_FROZEN_FORWARD_SCORECARD.md` — no diff to commit.
- This document: new, report-only.
- No changes to `MLB-RSCH-0022`'s committed artifact (its frozen DEV/VAL/pooled
  split and forward-holdout boundary were read-only inputs, never rerun in a
  way that would mix the forward window into them).
- `tests/edgelab/` run locally against these regenerated artifacts (see PR
  for the exact result); PR CI (`pr-ci.yml`) runs the full deterministic
  suite on push, per repository policy.
