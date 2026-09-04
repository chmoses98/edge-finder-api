# MLB Probability Calibration Research — 2026-09-04

Status: **COMPLETE — RESEARCH ONLY. No production behaviour changed.**
A frozen, inactive calibration artifact and a pure apply function are on this
branch for review (§10). Everything here is reproducible from committed data
with the commands in §12.

This supersedes the measurement lineage MLB-RSCH-0022 → MLB-RSCH-0023 →
10-day review (2026-09-03) on one specific point: the corpus those audits
scored was not a pregame comparison (§2.3). Their qualitative conclusion —
Kalshi's contemporaneous price is a better probability than production's —
survives on a clean corpus, at roughly half the magnitude they reported.

---

## 1. Decision and headline answers

**Core question.** Can the model's raw probabilities be transformed into
materially more accurate probabilities out of sample?
**Yes — but only relative to the raw model, not relative to the market, and
not enough to make the model's edges tradeable.**

| Out-of-sample (walk-forward, 18 slate dates, 15,082 contracts, 232 games) | Brier | log loss | ECE | Δ Brier vs raw | Δ Brier vs Kalshi mid |
|---|---|---|---|---|---|
| Raw production probability (Poisson engine) | 0.2000 | 0.6159 | 0.071 | — | +0.0179 [+0.0135, +0.0220] |
| Frozen NB dispersion only (no parameter fit on this data) | 0.1971 | 0.5960 | 0.046 | −0.0029 [−0.0043, −0.0014] | +0.0150 |
| Family logit-affine map on raw probability (drop-in) | 0.1938 | 0.5721 | 0.019 | −0.0062 [−0.0086, −0.0035] | +0.0117 |
| NB + mean shift + family map (structural) | **0.1929** | **0.5683** | **0.016** | **−0.0071 [−0.0096, −0.0043]** | +0.0108 [+0.0079, +0.0134] |
| Walk-forward base rate by family/line (no model at all) | 0.1948 | 0.5716 | 0.006 | −0.0052 [−0.0101, +0.0001] | +0.0127 |
| Model/market logistic blend | 0.1824 | 0.5411 | 0.009 | −0.0176 | +0.0003 [−0.0001, +0.0007] |
| Kalshi mid (simultaneous, pregame) | 0.1821 | 0.5403 | 0.008 | −0.0179 | — |

CIs are 95% game-clustered bootstrap. Both calibrated maps beat the raw
model on 16 of 18 test dates and on both frozen pseudo-holdouts (§7.2), and
the improvement reproduces on the 2026-08-29..31 window the 10-day review
called its forward corpus (§7.3).

**What production should do (§10):**

- **A is false.** Raw production probabilities are not the best estimator of
  anything; they are systematically over-confident (slope 0.56) and, for
  ladders, under-dispersed.
- **D is true for probability accuracy.** The best deployable estimator of
  true event probability that we possess is the Kalshi mid itself; every
  model/market blend we fit collapses onto the market (model weight ≈ 0 in
  every family, §5). "Edge" defined as model-minus-market is measurably
  model error, growing monotonically with disagreement size.
- **C is the recommended change for the model's own output.** If production
  must publish a model probability, apply the structural recipe (frozen NB
  dispersion 0.281513 + a walk-forward mean shift + family logit-affine
  maps, hierarchically shrunk) or, as a smaller diff, the drop-in family
  maps on the existing Poisson probability. Expected effect: Brier −0.006 to
  −0.007, ECE 0.07 → 0.02, slope 0.56 → ~1.0.
- **F: quarantine pitcher_strikeouts, pitcher_outs, first_inning_run.** After
  calibration, pitcher_strikeouts is still *worse* than a walk-forward base
  rate by rung (0.1830 vs 0.1739); first_inning_run has resolution ≈ 0;
  pitcher_outs is biased 10–22 points low at every rung.
- **G: bet selection on calibrated edge is not validated at any threshold.**
  Every rule "buy YES when p − ask > t" loses after fees for every candidate
  (§8); production's own Accepted recommendations ran −6.6% ROI on 508
  settled paper/real recommendations (§8.3). Nothing here supports keeping a
  model-edge threshold as the trade trigger.

The standard was: would we stake real money on the claim that the new
probabilities are closer to truth than the current ones? For the calibrated
model versus the raw model: yes (walk-forward, two holdouts, 16/18 dates,
every family directionally). For any model-derived probability versus the
Kalshi mid: no, and the evidence says the opposite.

---

## 2. Data and lineage audit

### 2.1 What exists

| Store | Rows / span | Contains | Trust for calibration |
|---|---|---|---|
| `data/edgelab/model_evaluations/` | 105,335 rows, 8,869 EVALUATED, 2026-07-30..09-03 | production probabilities + an archived "marketImpliedProbability" | **Mixed — see §2.3** |
| `data/edgelab/snapshots/<date>/pre_game_decision/<ts>/frozen/` | **81 keyed captures**, 07-30..09-03, 1–8 per day | byte-frozen slate (all projection inputs), Kalshi universe (bid/ask/mid at the same instant), Engine A ledger, sportsbook odds | **Primary source** |
| `data/edgelab/observations/` | 471,054 quotes, 142k tickers, 08-01..09-03 | intraday Kalshi bid/ask/last/volume with checkpoint labels and a pregame-validity flag | closing-quote benchmark |
| `data/edgelab/settlements/` | 122,689 rows, 08-02..**08-31** | YES/NO outcomes per ticker | **needs two corrections (§2.4)** |
| `data/kalshi/discovery/<date>.json` | 31 dates, single final version each | Engine B output of the LAST run of the day | reference only |
| `data/kalshi_registry_snapshots/` | 359 raw snapshots 06-08..09-03 | raw Kalshi registry | source of observations |
| `data/research_cache/pinnacle_historical/` | 2022-2026, every 5th day, 834 games | Pinnacle ML/totals | proxy-model studies only (no production probabilities exist before 07-30) |
| `data/research_cache/{bullpen,batting,starter}_backtest/2026/` | finals through 08-26 | scores, boxscores | cross-check only |
| `bets.json`, `data/edgelab/bets/bets.jsonl` | 556 / 385 bets | real-money ledger | too small, selection-biased |

There is **no** historical sportsbook archive at contract level, and no
production probability before 2026-07-30 — the "multi-season" datasets carry
a research proxy model, not production. Kalshi's own settlement receipts are
not captured (statsapi / kalshi endpoints are also unreachable from this
environment), so outcomes come from the repository's settlement engine plus
the corrections below.

### 2.2 Which probabilities are genuinely out-of-sample

Every probability used here is either (a) archived prospectively before
first pitch in a frozen capture, or (b) recomputed from such a capture with
deterministic code that has no access to outcomes. Captures taken after a
game's scheduled start, or with a non-pregame slate status, are excluded per
game. Projections *do* change after first pitch (post-start captures absorb
actual starters and lineups — e.g. an F5 projection moving from 2.12 to 1.20
when an opener is confirmed), which is why the per-game pregame filter is
mandatory.

### 2.3 What was wrong with the prior audit corpus

The `kalshi_discovery_extension` rows (4,717 of the 8,869 EVALUATED rows, and
the majority of MLB-RSCH-0022's 3,137 audit rows) were written from the day's
final discovery run:

- **85–88% were created after the game started** (median +7 h, p90 +18 h)
  in every family except NRFI/YRFI.
- Their `marketImpliedProbability` is the **yes-ask at that time** — an
  in-game or already-settled price (e.g. 1¢ for "Snell 13+ K" after the game).
- Their model probability came from a slate whose projections had already
  changed post-start.
- Game-total / F5-total rows before 09-01 used the wrong rung semantics
  (`> N` instead of `>= N`), which the current code has since fixed.

Prospective-snapshot rows (Engine A at checkpoints) are clean (100% pregame),
but their market price is the slate's last pipeline fetch, not the checkpoint
price. MLB-RSCH-0024 partly repaired the benchmark (2,635 rows, 56 validation
games) and MLB-RSCH-0027 found the scope contamination; this report replaces
both corpora with a point-in-time replay.

### 2.4 Outcome corrections applied

- **Total ladders** (`game_total`, `inning_total`): the archived settlement
  engine graded `total > N`; Kalshi pays `total >= N`. The committed
  correction map flips 564 settlements; unresolvable rungs are dropped.
- **KXMLBF5SPREAD** (F5 winning margin): 1,512 archived settlements were
  graded on the full-game margin. Re-graded from independently verified F5
  linescores for 270 games (1,104 rows); the rest carry no outcome.
- Pitcher-prop settlements carry MLB Stats API evidence (actual K / outs) and
  were kept as-is.

### 2.5 Model-version breaks inside the window

| Date | Change | Handling |
|---|---|---|
| 08-02 | F5 legs priced three-way | Engine A rows before this are two-way; Engine B is recomputed |
| 08-08 | pitcher props first modelled | no pitcher rows before |
| 08-11 | NRFI/YRFI first-inning context (Engine A only) | Engine B still uses proj/9 |
| 08-14 | qualification moved to fee-aware net edge | affects Accepted flags only |
| **08-21** | team-total off-by-one fix (v1.1 → v1.2) | Engine A team_total v1.1 rows are a different model (mean p 0.30 vs realized 0.49; v1.2 0.52 vs 0.47) |
| 08-29 | KXMLBRFI suspended to paper | flag only |
| **09-01** | total-ladder rung `>= N` in both engines | Engine B recomputed with current code; Engine A game_total rows before 09-01 are the old code |

Because Engine B is replayed with current code on frozen inputs, its rows are
comparable across the whole window and describe *today's* engine. Engine A
rows are the archived production numbers and carry the breaks above.

### 2.6 Duplication structure

- 1–7 pregame captures per contract (multiple snapshots of the same event):
  the primary unit is the **last pregame capture per (ticker, side)**; all
  captures are used only for the time-to-start analysis.
- Ladders (8–13 rungs per game total, ±7 per team total, 3 spreads per side),
  mirror-image sides (both ML sides, YRFI/NRFI), and three F5 legs are
  correlated: **all CIs resample whole games** (274–282 games).

---

## 3. The research dataset

`scripts/edgelab/build_calibration_research_dataset.py` → `lib/edgelab/research/calibration_dataset.py`
writes `data/edgelab/research_artifacts/calibration_research/pit_rows.jsonl.gz`
(66,569 rows) and `pit_games.jsonl.gz`.

For every keyed capture, production's own discovery engine
(`scripts.discover_kalshi_mlb_markets.discover`) is replayed on the frozen
market universe and slate. **Validation:** the replay reproduces the archived
discovery file exactly on a post-fix date (2026-09-02: 565/565 supported
contracts, identical fair probabilities and prices), and on a pre-fix date
(2026-08-25) reproduces every family exactly except the two total-ladder
families, which differ by exactly the documented rung shift (973/973
matched). The Poisson re-pricing used for the structural candidate
reproduces every one of 14,223 run-based Engine B rows to <1e-5.

Row schema (per contract, per capture): engine (A = production 11-market
ledger, B = full-universe adapters), family/period/side/line/subject,
`modelP`, simultaneous `yesBid/yesAsk/mid`, volume, minutes to first pitch,
slate status, lineup state, closing pregame quote and checkpoint quotes from
the observation store, outcome + source, archived discovery probability,
model-era flags; per game: replayed projections and sportsbook odds
(Pinnacle/FanDuel/DraftKings/BetMGM ML, totals, team totals, F5).

Primary analysis set (Engine B, settled through 08-31, last pregame capture,
two-sided quote): **18,473 contracts / 282 games / 24 slate dates
(08-02..08-31)**. Engine A: 2,413 / 272 games.

---

## 4. How the model is miscalibrated

`scripts/edgelab/run_calibration_characterization.py` → `characterization.md/json`.

### 4.1 By family (Engine B, primary set; Δ = model − Kalshi mid, negative = model better)

| family | n | games | base | mean model | mean mid | Brier model | Brier mid | Δ [95% CI] | slope | ECE model | reliability | resolution model / market |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| first_inning_run | 268 | 268 | 0.504 | 0.595 | 0.484 | 0.2579 | 0.2471 | +0.011 [−0.004, +0.025] | 0.63 | 0.091 | 0.0099 | 0.001 / 0.008 |
| game_result | 548 | 274 | 0.500 | 0.500 | 0.500 | 0.2460 | 0.2258 | +0.020 [+0.009, +0.033] | 0.59 | 0.035 | 0.0038 | 0.008 / 0.025 |
| game_total | 2,986 | 274 | 0.558 | 0.537 | 0.557 | 0.1845 | 0.1742 | +0.010 [+0.004, +0.017] | 0.65 | 0.075 | 0.0072 | 0.068 / 0.072 |
| inning_result | 2,427 | 271 | 0.333 | 0.327 | 0.329 | 0.2181 | 0.1998 | +0.018 [+0.011, +0.026] | 0.12 | 0.045 | 0.0068 | 0.011 / 0.022 |
| inning_total | 1,863 | 271 | 0.609 | 0.561 | 0.610 | 0.1904 | 0.1755 | +0.015 [+0.007, +0.023] | 0.66 | 0.075 | 0.0099 | 0.057 / 0.063 |
| pitcher_outs | 478 | 262 | 0.479 | 0.359 | 0.496 | 0.2671 | 0.2399 | +0.027 [+0.011, +0.043] | 0.26 | 0.121 | 0.0203 | 0.002 / 0.010 |
| pitcher_strikeouts | 3,712 | 276 | 0.427 | 0.329 | 0.438 | 0.2003 | 0.1491 | +0.051 [+0.040, +0.063] | 0.46 | 0.121 | 0.0166 | 0.060 / 0.095 |
| team_total | 3,836 | 274 | 0.444 | 0.437 | 0.452 | 0.1965 | 0.1857 | +0.011 [+0.006, +0.016] | 0.63 | 0.070 | 0.0057 | 0.055 / 0.060 |
| winning_margin | 2,355 | 274 | 0.272 | 0.193 | 0.260 | 0.2009 | 0.1848 | +0.016 [+0.008, +0.025] | 0.45 | 0.082 | 0.0079 | 0.005 / 0.012 |
| **ALL** | 18,473 | 282 | 0.443 | 0.400 | 0.445 | **0.2023** | **0.1807** | **+0.022 [+0.018, +0.026]** | **0.56** | 0.074 | 0.0068 | 0.051 / 0.065 |

Reading the Murphy decomposition: of the 0.0216 gap to the market, at most
0.0068 (the model's reliability term) is recoverable by any recalibration;
the remainder is lower *resolution* — the model separates outcomes less well
than the market does. Calibration cannot fix that.

### 4.2 Shape of the error

Reliability, all families (model probability bands): 0–0.1 → observed 0.18;
0.1–0.2 → 0.24; 0.2–0.3 → 0.33; 0.3–0.4 → 0.41; 0.5–0.6 → 0.53; 0.7–0.8 →
0.70; 0.8–0.9 → 0.76; 0.9–1.0 → 0.90. Classic over-confidence (slope 0.56).
The Kalshi mid is within ±0.013 in every band except 0.9–1.0 (−0.026).

The mechanism is structural, not a scalar bias. By rung:

| game_total rung | 4 | 6 | 8 | 10 | 12 | 14 |
|---|---|---|---|---|---|---|
| model − realized | +0.05 | +0.08 | +0.02 | −0.06 | −0.11 | −0.18 |

| team_total rung | 1.5 | 2.5 | 3.5 | 4.5 | 5.5 | 6.5 | 7.5 |
|---|---|---|---|---|---|---|---|
| model − realized | +0.09 | +0.08 | +0.03 | −0.02 | −0.06 | −0.08 | −0.08 |

Too much mass in the middle, too little in both tails: the independent
Poisson run distribution is under-dispersed (empirical team-run variance is
2.1–2.3× Poisson, MLB-RSCH-0010). Spreads (1.5/2.5/3.5: −0.08/−0.07/−0.07)
and F5 totals show the same signature. Pitcher props are different: biased
**low at every rung** (strikeouts −0.06 to −0.15; outs −0.10 to −0.22),
i.e. a level error in the workload/K projection, not only a shape error. On
top of both, the run-projection mean is ~0.3 runs/team low (MLB-RSCH-0033),
which is why NB alone leaves a −0.03 mean bias (§7).

### 4.3 Other dimensions (Engine B; details in `characterization.md`)

- **Disagreement is error.** |model − mid| ≤ 0.025: Δ Brier +0.0001 [−0.0003, +0.0005] (model ≡ market). 0.05–0.10: +0.007. 0.10–0.20: +0.020. > 0.20: **+0.133 [+0.106, +0.157]**, with model mean 0.29 vs realized 0.52. Model-below-market rows are worse (+0.030) than model-above (+0.016).
- **Time to first pitch:** no trend (0–45 m +0.020; 45–90 m +0.020; 90–180 m +0.026; 3–6 h +0.020), also across all 33,631 pregame captures. The model does not get better as lineups arrive.
- **Lineup confirmed at capture:** confirmed +0.022 vs unconfirmed +0.019 — the "half the gap on confirmed rows" finding of RSCH-0022 does **not** reproduce; it was a source-mix artefact.
- **Market favourite vs dog:** favourites +0.030, dogs +0.017.
- **Week:** stable (+0.017 to +0.025 for weeks 32–36); week 31 (7 games) is noise.
- **Period:** F7 inning_result is the worst period (+0.030); F3/F5 +0.012.
- **Home/away (ML):** home-side contracts +0.025, away +0.020.
- **Closing quote (retrospective benchmark):** the last pregame capture mid and the closing mid are indistinguishable (Δ +0.0004 [−0.0002, +0.0011]); the market barely moves between our capture and close, so "capture mid" is an honest deployable benchmark, not a leak.

### 4.4 Engine A (the 11 production markets, archived numbers)

Pooled 2,413 rows / 272 games: Brier 0.2596 vs mid 0.2394, slope **0.21**,
ECE 0.086. Family slopes: game_result 0.67, inning_result 0.30, team_total
0.01, game_total 0.07, first_inning_run 0.09. At the single main line
production bets, the model has almost no discriminating power (§5.3).
Engine A and B agree exactly on first_inning_run/inning_result contracts;
they differ on game_result (0.72 cap + extra-innings blend, mean |Δ| 0.011),
team_total (v1.1 era) and game_total (pre-09-01 rung bug, mean |Δ| 0.13).

---

## 5. Does the model add information beyond the market?

### 5.1 Logistic blend (walk-forward)

`logit p = c + w_m·logit(model) + w_k·logit(mid)` fit on dates < D, scored
on D. Pooled: Brier 0.1824 vs market 0.1821, Δ +0.0003 [−0.0001, +0.0007].
Frozen-holdout weights (fit ≤ 08-24): global w_m = **−0.03**, w_k = 1.08;
by family w_m ∈ {game_total +0.10, pitcher_outs +0.24 (388 rows),
first_inning_run +0.12 (224), team_total +0.03, inning_result −0.02,
inning_total −0.03, winning_margin −0.09, pitcher_strikeouts −0.11,
game_result −0.16}. No family's blend beats the market out of sample
(closest: game_total +0.0005 [−0.0003, +0.0014]). Beats market on 7 of 18
dates, i.e. coin-flip.

### 5.2 Versus a model-free base rate

A walk-forward base rate keyed (family, period, line, side) with back-off
scores 0.1948 pooled — better than the raw model (0.2000) and within 0.001 of
the best calibrated model (0.1929–0.1938). By family (walk-forward Brier,
climatology / best calibrated model / market):

| family | climatology | calibrated model | market | verdict |
|---|---|---|---|---|
| game_total | 0.1852 | 0.1770 | 0.1751 | model informative, ≈ market after calibration |
| team_total | 0.1945 | 0.1909 | 0.1883 | informative |
| inning_result | 0.2222 | 0.2113 | 0.2016 | informative |
| game_result | 0.2500 | 0.2421 | 0.2312 | informative |
| inning_total | 0.1807 | 0.1815 | 0.1772 | **no information beyond line base rate** |
| winning_margin | 0.1912 | 0.1908 | 0.1876 | ≈ none |
| first_inning_run | 0.2558 | 0.2486 | 0.2485 | ≈ market only because market ≈ coin flip |
| pitcher_outs | 0.2523 | 0.2510 | 0.2431 | ≈ none |
| pitcher_strikeouts | 0.1739 | 0.1830 | 0.1476 | **worse than base rate even calibrated** |

### 5.3 At production's own lines (Engine A)

Walk-forward, 1,895 rows / 211 games: climatology 0.2501, calibrated model
0.2489, market 0.2416. Per family the calibrated Engine A probability ties
the base rate everywhere (first_inning 0.2502 vs 0.2500; game_result 0.2464
vs 0.2500; game_total 0.2513 vs 0.2524; inning_result 0.2453 vs 0.2456;
team_total 0.2525 vs 0.2534). **At the main line, production's model has no
measurable skill.** Its apparent skill in ladders comes from extreme rungs,
where the line itself does the work.

### 5.4 Versus a sportsbook reference

The frozen slates carry Pinnacle at fetch time (stale by up to hours, so this
favours Kalshi). Moneyline, 548 contracts / 274 games: model 0.2460, Kalshi
mid 0.2258, Pinnacle vig-free 0.2316 (Pinnacle − Kalshi +0.0058 [+0.0019,
+0.0108]). Game total at Pinnacle's exact line (134 games): model 0.2694,
Kalshi 0.2439, Pinnacle 0.2495. A model/Pinnacle blend puts weight −0.18 on
the model. The model adds nothing beyond either market; a Kalshi/Pinnacle
logit average is slightly worse than Kalshi alone here (staleness).

---

## 6. Methods tested and validation design

Candidates (all in `scripts/edgelab/run_calibration_walkforward.py`):

| id | description | parameters |
|---|---|---|
| C1 | global logit-affine (Platt) | 2 |
| C2 | family Platt, ridge-shrunk toward the global map (partial pooling, L2 = 25, ≥150 training rows per family) | 2 + 2/family |
| C3 | family beta calibration | 3/family |
| C4 | family isotonic regression | nonparametric |
| C5 / C6 | global / family model–market logistic blend | 3 (+3/family) |
| C7 | **structural:** independent negative-binomial re-pricing, dispersion 0.281513 frozen from MLB-RSCH-0010 (fit 2022–2024, confirmed 2025 and a locked 2026 holdout) | **0 fit here** |
| C8 / C9 | C7 + family Platt / + family blend | as above |
| C10 | C7 + mean shift (runs/team, grid {0, .15, .30, .45}) chosen by training log loss | 1 |
| C11 | C10 + family Platt | 1 + 2/family |
| B0 | walk-forward base rate by (family, period, line, side) with back-off | — |
| M1 | Kalshi-only logit shrink toward base rate (MLB-RSCH-0026 form) | 2 |

Validation:

1. **Rolling-origin walk-forward by slate date**: expanding window, minimum
   6 training dates / 500 rows; 18 test dates 08-08..08-31; every parameter
   fit strictly on dates < D. 15,082 out-of-sample contracts, 232 games.
2. **Frozen pseudo-holdout #1**: fit ≤ 08-24 (MLB-RSCH-0024's training end),
   score 08-25..31 (6,032 contracts, 95 games).
3. **Frozen pseudo-holdout #2**: fit ≤ 08-28, score 08-29..31 — the 10-day
   review's forward corpus (2,662 contracts, 43 games).
4. Per-date win counts; per-family blocks; game-clustered CIs throughout.
5. Nothing was tuned on the test windows; the mean-shift grid and ridge
   strength were fixed before any test score was read.

Methodological references used: Murphy (1973) Brier decomposition; Platt
(1999) / Kull et al. (2017, beta calibration); Zadrozny & Elkan (2002,
isotonic); Bergmeir & Benítez (2012) and Tashman (2000) on rolling-origin
evaluation; cluster bootstrap for correlated binary outcomes (Cameron &
Miller 2015); Ranjan & Gneiting (2010) on why linear opinion pools of
calibrated forecasts are miscalibrated, which motivates the logit-space
blend.

---

## 7. Results

### 7.1 Walk-forward, pooled (§1 table) and per date

Beats raw model / beats market, of 18 dates: C1 15/0, C2 16/0, C3 16/0,
C4 15/0, C7 15/0, C8 16/0, C10 16/0, C11 16/0, B0 12/1, C5 18/7, C9 18/7,
M1 18/7. The two dates on which the calibrated maps lost to raw are 08-21 (14
games, +0.003) and 08-29 (17 games, +0.002–0.003); NB-only lost on 08-19,
08-21 and 08-30 by ≤ 0.002.

### 7.2 Frozen holdouts

| candidate | HO#1 (fit ≤ 08-24; 95 games) Brier / Δ vs raw | HO#2 (fit ≤ 08-28; 43 games) Brier / Δ vs raw |
|---|---|---|
| raw model | 0.2068 | 0.2028 |
| C7 NB only | 0.2028 / −0.0040 [−0.0065, −0.0017] | 0.2004 / −0.0024 [−0.0060, +0.0013] |
| C2 family Platt | 0.1992 / −0.0076 [−0.0116, −0.0036] | 0.1952 / −0.0076 [−0.0135, −0.0014] |
| C4 isotonic | 0.1982 / −0.0086 | 0.1938 / −0.0091 |
| **C11 NB + shift + family Platt** | **0.1981 / −0.0087 [−0.0129, −0.0043]** | **0.1945 / −0.0083 [−0.0143, −0.0020]** |
| B0 climatology | 0.1975 / −0.0093 | 0.1913 / −0.0115 |
| C5 blend | 0.1874 / −0.0195 (≡ market) | 0.1803 / −0.0225 (≡ market) |
| market | 0.1873 | 0.1805 |

Parameters frozen at 08-24: drop-in global (a, b) = (0.063, 0.573); by
family b ∈ {inning_result 0.34, pitcher_strikeouts 0.45, pitcher_outs 0.53,
winning_margin 0.54, first_inning_run 0.57, game_result 0.57, game_total
0.66, inning_total 0.65, team_total 0.68}. Structural (mean shift 0.45):
global b = 0.71; game_total 0.98, team_total 1.02, inning_total 0.85,
inning_result 0.80, winning_margin 0.74, game_result 0.74. Re-fit on all
data (§10) the same slopes move by ≤ 0.10 (inning_result drop-in 0.34 →
0.23; every other family ≤ 0.06; globals 0.573 → 0.557 and 0.711 → 0.697)
— stable across windows, unlike MLB-RSCH-0023's b ≈ 0.13 (which was fit
on v1.1 team-total rows).

### 7.3 Does it reproduce prospectively?

HO#2 is exactly the 08-29..31 forward window. Calibration transfers
(−0.0083 with the CI excluding zero on 43 games); NB alone is directionally
right but not resolved at that sample; the market remains 0.014 better than
the best model-only candidate. No regime shift is visible: weekly Δ vs
market is +0.017..+0.025 throughout, and the frozen-at-08-24 parameters
score the same as walk-forward refits.

### 7.4 Per-family verdicts (walk-forward)

- **Calibration helps materially and reliably:** game_total (−0.005),
  team_total (−0.005), winning_margin (−0.005), inning_total (−0.007),
  pitcher_outs (−0.015), pitcher_strikeouts (−0.014), all CIs excluding zero.
- **NB is the right *shape* fix:** C7 improves every run-based family with
  no fitting; C11's family slopes come out ≈ 1.0 for game_total and
  team_total, i.e. after NB + mean shift those families need almost no map.
- **inning_result:** Platt alone hurts (+0.002); NB + shift + Platt helps
  (−0.006). The three-way structure needs the distribution fix first.
- **game_result / first_inning_run:** no significant change from any
  candidate (0.24 / 0.25 territory; sample 448 / 224).

---

## 8. Economic validation

`scripts/edgelab/run_calibration_economics.py` → `economics.md/json`, on
walk-forward out-of-sample rows only. Rule: buy YES at the executable ask
when p − ask > t; $10 taker orders, whole contracts, Kalshi taker fees;
CLV proxy = pregame closing mid − entry ask.

| candidate | t | bets | hit | mean ask | ROI | return/$ [95% CI] | CLV | CLV > 0 |
|---|---|---|---|---|---|---|---|---|
| raw model | 0.00 | 5,685 | 0.560 | 0.570 | −5.5% | −0.054 [−0.107, +0.001] | −0.0053 | 11% |
| raw model | 0.05 | 3,098 | 0.554 | 0.567 | −5.9% | −0.058 [−0.130, +0.009] | −0.0053 | 13% |
| raw model | 0.10 | 1,197 | 0.500 | 0.532 | −7.7% | −0.077 [−0.200, +0.051] | −0.0034 | 16% |
| C2 family Platt | 0.05 | 3,748 | 0.268 | 0.288 | −14.3% | −0.142 [−0.241, −0.031] | −0.0047 | 13% |
| C11 structural | 0.05 | 3,625 | 0.265 | 0.282 | −13.6% | −0.135 [−0.237, −0.022] | −0.0047 | 13% |
| C4 isotonic | 0.05 | 3,721 | 0.336 | 0.353 | −10.9% | −0.108 [−0.205, +0.002] | −0.0045 | 14% |
| NB only | 0.05 | 1,600 | 0.376 | 0.387 | −6.0% | −0.060 [−0.199, +0.077] | −0.0042 | 15% |
| model/market blend | 0.00 | 943 | 0.566 | 0.553 | −2.9% | −0.027 [−0.142, +0.103] | −0.0042 | 12% |
| model/market blend | 0.05 | 22 | — | — | −74% | — | — | — |

Findings:

- **No candidate produces a positive-return rule at any threshold.** The
  point estimate is negative everywhere; the only CIs that include zero are
  the wide ones.
- **Calibrated edges are worse to trade than raw edges**, not better: the
  maps raise tail probabilities toward their (higher) observed frequencies,
  so "edge" migrates to 25–35¢ longshots whose realized return after fees is
  −10 to −14%. This is the expected consequence of §5: once the probability
  is closer to truth, the remaining "edge" is spread + fee + residual error.
- **Monotonicity:** realized gross return by calibrated-edge bucket is flat
  around zero to negative (C11: −0.015, −0.013, −0.016, −0.015, −0.033 from
  the 0–2.5 pt bucket up to > 20 pt); the raw model's buckets are equally
  non-monotone (−0.009, 0.000, −0.002, −0.028, −0.054). Higher edge does not
  mean better outcomes for any probability we can build.
- **CLV is negative for every rule** (≈ −0.5¢ = the half-spread); CLV > 0 on
  only 11–18% of entries. There is no closing-line information in the model's
  disagreement.
- **False-positive edges:** the raw model shows a ≥ 5-point edge on 3,098
  contracts that hit 55% at a mean ask of 57¢ (below break-even after fees);
  C11 shows ≥ 5 points on 3,625 mostly different contracts that hit 27% at
  28¢. Calibration changes *which* edges are false, not how many.

### 8.3 Production's own recommendations

Engine A rows with `status = Accepted` (paper + real-money tiers), last
pregame capture, settled: **508 recommendations, 267 games, hit 46.5%,
ROI −6.6% [−15.8%, +2.8%]**; MEDIUM tier (121) −10.6%; by family
first_inning_run −3.3% (250), game_result −20.9% (57), inning_result −3.5%
(83), team_total −8.9% (118). Mean model probability on these was 0.564
against a realized 0.465 — the recommendations are exactly the rows where
the model is most over-confident, which is what selecting on model-minus-
market does to an over-confident model. Unselected rows: −5.7%.

---

## 9. Uncertainty

Game-clustered bootstrap of the family maps (structural recipe, all settled
data; 90% interval half-width of the calibrated probability at a given raw
value):

Columns are the calibrated probability (± 90% half-width) at raw NB-shifted probability 0.10 … 0.90.

| family | n | games | p10 | p20 | p35 | p50 | p65 | p80 | p90 |
|---|---|---|---|---|---|---|---|---|---|
| first_inning_run | 268 | 268 | 0.13 ±0.44 | 0.21 ±0.39 | 0.32 ±0.30 | 0.42 ±0.16 | 0.54 ±0.09 | 0.67 ±0.29 | 0.79 ±0.39 |
| game_result | 548 | 274 | 0.10 ±0.15 | 0.20 ±0.15 | 0.35 ±0.10 | 0.50 ±0.00 | 0.65 ±0.10 | 0.80 ±0.15 | 0.90 ±0.15 |
| game_total | 2986 | 274 | 0.09 ±0.02 | 0.18 ±0.03 | 0.33 ±0.04 | 0.48 ±0.04 | 0.63 ±0.04 | 0.79 ±0.03 | 0.90 ±0.02 |
| inning_result | 2427 | 271 | 0.14 ±0.04 | 0.23 ±0.03 | 0.35 ±0.00 | 0.46 ±0.03 | 0.58 ±0.07 | 0.71 ±0.09 | 0.82 ±0.09 |
| inning_total | 1863 | 271 | 0.14 ±0.03 | 0.25 ±0.05 | 0.39 ±0.05 | 0.53 ±0.04 | 0.66 ±0.04 | 0.79 ±0.03 | 0.88 ±0.02 |
| pitcher_outs | 478 | 262 | 0.38 ±0.11 | 0.43 ±0.07 | 0.48 ±0.04 | 0.52 ±0.06 | 0.56 ±0.10 | 0.61 ±0.14 | 0.66 ±0.18 |
| pitcher_strikeouts | 3712 | 276 | 0.33 ±0.03 | 0.42 ±0.03 | 0.51 ±0.03 | 0.58 ±0.03 | 0.64 ±0.03 | 0.72 ±0.03 | 0.79 ±0.03 |
| team_total | 3836 | 274 | 0.09 ±0.02 | 0.19 ±0.03 | 0.33 ±0.03 | 0.48 ±0.03 | 0.63 ±0.03 | 0.79 ±0.03 | 0.89 ±0.02 |
| winning_margin | 2355 | 274 | 0.15 ±0.04 | 0.23 ±0.02 | 0.34 ±0.03 | 0.44 ±0.07 | 0.54 ±0.10 | 0.66 ±0.13 | 0.77 ±0.14 |

Reading: for the large ladder families a calibrated 50% is known to about
±0.03–0.05 (team_total ±0.03, game_total ±0.04, inning_total ±0.04); for
the one-contract-per-game families the map is barely identified —
game_result ±0.15 at raw 0.2/0.8 (the 0.5 point is pinned by the
mirror-image sides), first_inning_run ±0.16 at 0.5 and ±0.3–0.4 in the
tails; pitcher_outs ±0.06–0.18.
Two uses follow: (i) any edge smaller than the half-width for that family is
not distinguishable from zero and should not trigger a bet; (ii) the
half-widths are a natural family-trust weight for sizing (small for
game_total/team_total/inning_total, large for the one-per-game families). A
richer treatment (posterior predictive of p per contract) is not supported
at this sample.

---

## 10. Production proposal

Nothing is wired. The branch carries:

- `lib/edgelab/research/frozen_calibration_map.py::apply_calibrated_probability(family, p, recipe)`
  — a pure function reading `data/edgelab/analytics/frozen_calibration_map_v1.json`
  (`productionActive: false`), with `is_quarantined(family)`.
- The artifact holds both recipes fit on all settled data (08-02..08-31,
  18,473 contracts / 282 games): **drop_in** (family (a, b) on the existing
  Poisson probability; global a = 0.056, b = 0.557) and **structural**
  (NB dispersion 0.281513, mean shift +0.45 runs/team/9 innings, family
  (a, b); global b = 0.697, game_total b = 0.97, team_total b = 0.96).
- Tests: `tests/edgelab/test_calibration_research_infrastructure.py` (17).

**Exact proposed change, if authorised (a separate PR, not this branch):**

1. In `lib/kalshi_probability_adapters.py` and the corresponding Engine A
   blocks of `scripts/build_market_ledger.py`, replace `poisson_pmf` by the
   negative-binomial pmf with dispersion 0.281513 for game_result, inning_
   result, game_total, inning_total, team_total, winning_margin (the
   first_inning_run λ/9 formula can stay Poisson), and add +0.45 runs/team
   (scaled by innings) to the projection means before pricing.
2. Apply `apply_calibrated_probability(family, p, "structural")` to the
   result and write it to `modelFairProbability`; stamp
   `calibrationVersion = "frozen_calibration_map_v1"` (the field exists and
   is null on all 105k archived rows).
3. Alternatively, the smaller diff: keep Poisson and apply recipe `drop_in`.
   Expected Brier −0.006 instead of −0.007; ECE 0.07 → 0.019.
4. Quarantine `pitcher_strikeouts`, `pitcher_outs`, `first_inning_run` from
   real-money eligibility (probabilities still computed and archived).
5. Do **not** keep model-minus-ask as the trade trigger. No threshold on any
   probability we can produce has positive expected return (§8). If bets
   continue, the only defensible trigger in this evidence is one that is
   *independent* of the model's disagreement with Kalshi (e.g. price
   improvement versus the mid, or a demonstrated CLV signal), and the
   probability used for sizing should be the Kalshi mid or the blend, not
   the model.

Rollback: flip `productionActive` / skip the call; probabilities revert to
the current engine byte-for-byte. The map is monotone, so ranking within a
family is unchanged by step 2 alone; step 1 (NB) does reorder across rungs,
which is the point.

Expected improvement and uncertainty: Brier −0.007 (95% CI −0.010 to
−0.004) and ECE 0.07 → 0.016 on the model's own probability; **zero**
expected change in trading P&L from the probability change alone, because
the calibrated model still contains no information beyond the market.

Affected families: all nine Engine B families receive a map; the three
quarantined ones should not be traded.

---

## 11. What remains uncertain, and what evidence would settle it

- **Sample.** 282 games / 30 days, one season phase. Family-level CIs on the
  calibrated-versus-raw improvement are ±0.003–0.005; the market-versus-
  model gap is resolved at every family. Game_result and first_inning_run
  (one contract per game) are not resolved for calibration.
- **Kalshi's own biases.** The mid is within ±0.013 in all but the top band
  (−0.026 at 0.9–1.0, 1,011 rows). MLB-RSCH-0026's favourite-longshot claim
  does not reproduce here beyond that band.
- **Level bias of the projections** (+0.45 runs selected on every window)
  is large and may be seasonal (August offence) — a prospective check on
  September dates is needed before hard-coding it.
- **Pitcher props** need a model fix, not a map: the strikeout probability
  uses a binomial at the *expected* batters faced instead of mixing over the
  workload distribution, and its level is 6–15 points low at every rung.
- **Most valuable new data**, in order: (1) Kalshi settlement receipts
  (removes the two archive corrections); (2) contract-level closing
  sportsbook prices captured per game before first pitch (Pinnacle F5 and
  team totals are not available historically); (3) order-book depth to turn
  the spread assumption into real executable capacity; (4) continued frozen
  captures — the pipeline that produced this dataset is already running.

**Prospective experiment (preregistered here):** keep both recipes frozen as
committed; score every new settled date with
`run_calibration_walkforward.py`'s frozen-holdout mode using train end
2026-08-31 (no refits). Decision rule: after ≥ 60 new games, the structural
recipe is confirmed if Δ Brier vs raw < 0 with the game-clustered CI
excluding zero AND the direction holds on a majority of dates AND no family
worsens by more than 0.005; the blend/market conclusion is re-examined only
if any family's blend beats the market with CI excluding zero on ≥ 100
games.

---

## 12. Reproduction

```
python3 scripts/edgelab/build_calibration_research_dataset.py      # ~45 s, writes pit_rows / pit_games
python3 scripts/edgelab/run_calibration_characterization.py         # characterization.{json,md}
python3 scripts/edgelab/run_calibration_walkforward.py B            # walkforward.{json,md}, predictions
python3 scripts/edgelab/run_calibration_walkforward.py A            # Engine A variant
python3 scripts/edgelab/run_calibration_economics.py                # economics.{json,md}
python3 scripts/edgelab/plot_calibration_research.py                # plots/*.png
python3 scripts/edgelab/freeze_calibration_map.py                   # frozen_calibration_map_v1.json
python3 -m pytest tests/edgelab/test_calibration_research_infrastructure.py -q
```

All outputs live under `data/edgelab/research_artifacts/calibration_research/`
(dataset, tables, out-of-sample predictions, plots, `uncertainty_family_platt.json`).
No network access is used; nothing under `data/edgelab/{model_evaluations,
settlements,observations,snapshots}` is modified.

---

## 13. Index of answers to the brief's 18 questions

1. **Data:** §2.1 — 81 frozen pregame captures (Aug 2026) with simultaneous Kalshi quotes, 471k intraday quotes, 122k settlements, sportsbook odds at fetch time; no production probabilities before 07-30, no historical sportsbook contract archive.
2. **Trustworthy:** the replayed frozen captures + observation store + corrected settlements (§2.3–2.4); not the archived discovery-extension rows.
3. **How miscalibrated:** over-confident (slope 0.56), under-dispersed ladders, pitcher props biased low, projection mean ~0.3–0.45 runs low (§4).
4. **Well calibrated where:** within ±0.025 of the market the model is as good as the market; game_total/team_total after NB need almost no map (§4.3, §7.4).
5. **Problematic families:** pitcher_strikeouts, pitcher_outs, first_inning_run; inning_total and winning_margin add nothing beyond the line base rate (§5.2).
6. **Beyond Kalshi:** no — blend weight ≈ 0 in every family, blend ≡ market (§5.1).
7. **Beyond sportsbook:** no — model/Pinnacle blend weight −0.18; Kalshi mid beats stale Pinnacle (§5.4).
8. **Methods:** §6 (12 candidates + 2 baselines).
9. **Validation:** rolling-origin by date, two frozen holdouts, game-clustered CIs (§6).
10. **Winner:** NB + mean shift + hierarchical family Platt (C11); drop-in family Platt (C2) within 0.001 (§7).
11. **Size/stability:** −0.007 Brier [−0.010, −0.004], 16/18 dates, both holdouts; parameters stable across windows (§7.2).
12. **Prospective reproduction:** yes on 08-29..31 (§7.3).
13. **CLV/ROI/monotonicity:** no positive rule, CLV negative, no monotonicity for any candidate (§8).
14. **Production probabilities:** structural or drop-in recipe (§10); for decisions, the Kalshi mid.
15. **Disable/downgrade:** quarantine the three families above (§10).
16. **Thresholds/sizing:** no model-edge threshold is validated; stop using model-minus-ask as the trigger (§8, §10).
17. **Remaining uncertainty:** §9, §11.
18. **Most useful new data:** Kalshi settlement receipts, per-game pregame sportsbook closes, order-book depth (§11).
