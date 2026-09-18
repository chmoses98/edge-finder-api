# Offensive Form: Consistency and Market-Relative Performance

**Experiment:** `MLB-RSCH-0036` · **Report:**
`data/edgelab/experiment_reports/MLB-RSCH-0036/RPT-fd7a7ba5e4ea93a1.json`
(mirrored at `data/edgelab/analytics/latest_mlb_rsch_0036_team_offense_consistency.json`)

**Bottom line: `DESCRIPTIVE_ONLY`. No production weight was changed, and none
is proposed.**

---

## 1. Where recent scoring form currently enters the system (audit)

Before changing anything, here is every place a "recent runs/game" number was
already reaching a decision:

| Surface | What it used | Status after this work |
|---|---|---|
| **Model feature** — `api/slate.js` offence baseline | `rawBaseline = last7RpG×0.30 + last15RpG×0.30 + seasonRpG×0.40`, then Bayesian shrinkage, opponent adjustment and (if confirmed) a lineup adjustment; feeds run projections → team totals, game totals, F5/ML probabilities | **UNCHANGED.** No weight touched. |
| **Model feature** — `scripts/enrich_data.py` `compute_offense_baseline()` | the same three-way blend, recomputed in the Python pipeline | **UNCHANGED.** A test (`test_enrich_data_offense_baseline_is_identical_with_and_without_form_context`) proves every projection-feeding field is byte-identical with and without the new context. |
| **Displayed slate context** | `awayTeamStats.last7RpG` / `last15RpG` / `runsPerGame` | **Extended** with `offenseForm`, `offenseFormLine`, `offenseFormLabel`. |
| **Handicapping summary** — `RUN_THE_SLATE.md` PRE-SCAN | `L7: X.X │ L15: X.X │ Szn: X.X │ Flag: BOUNCEBACK/REGRESSION/NEUTRAL` — three means and a flag derived from means | **Replaced** with the distribution line (see §4). |
| **Docs** — `MODEL_CORE.md` §"Team Context", `RULES.md` | describe the blend and the bounceback/regression flag | Unchanged; the blend they describe is unchanged. |
| **Wagering decision logic** — `scripts/risk_gate.py`, `build_market_ledger.py` | reads the *projection*, not the rolling mean directly | Unchanged. |

The load-bearing problem was the last row of the middle block: a handicapper
(human or AI) was being handed **three means and nothing else**, and a mean over
a handful of games is dominated by its largest value.

---

## 2. Method

Leakage-free walk-forward, reusing this repository's existing research
machinery rather than a new one:

- **Rows:** one per (team, game) from MLB-RSCH-0003's already-committed
  schedule cache, 2022–2026. **20,478 team-games** (dev 2022–24 = 12,800;
  validation 2025 = 4,268; holdout 2026 = 3,410). Eligible only once a team has
  **20 strictly-prior completed games that season**.
- **Leakage control:** `is_strictly_before()` (imported unchanged; handles
  same-date doubleheader ordering and refuses to treat an unordered same-date
  game as prior). The target game's own runs are read only as the outcome.
- **Features:** computed by **`lib/team_offense_form.py`** — the *same* module
  that produces the slate's displayed line, so a research number and a displayed
  number cannot drift. Windows 5/7/10; thresholds ≥3/4/5/6; median, trimmed
  mean, EWMA, max, top-game share of window runs, mean − trimmed mean.
- **Freezing:** every regression is fit **once** on development and applied
  **unchanged** to validation and holdout. Windows, thresholds and the
  eligibility floor are preregistered constants, not chosen after looking.
- **Comparators:** `control` (season-to-date R/G + opponent runs-allowed +
  home/away) vs `meanOnly` (control + rolling means — the status quo) vs
  `consistency` (control + the shape statistics).
- **Outcomes:** (A) next-game runs scored; (B) P(next game ≥ 4 runs) — the
  threshold nearest a typical Kalshi team-total line — scored with Brier and
  log loss against an always-predict-the-base-rate floor.

---

## 3. Findings

### 3.1 Statistically demonstrated predictive value — **none survives**

| Predictor | dev ρ (95% CI) | validation ρ | holdout ρ |
|---|---|---|---|
| `formMedian_10` | **+0.057** (+0.032, +0.080) | +0.030 (spans 0) | +0.022 (spans 0) |
| `formEwma_10` | **+0.059** (+0.032, +0.084) | **+0.051** (+0.017, +0.079) | +0.022 (spans 0) |
| `formTrimmedMean_10` | **+0.060** (+0.031, +0.086) | **+0.035** (+0.001, +0.062) | **+0.033** (+0.001, +0.059) |
| `formGe4Rate_10` | **+0.055** (+0.029, +0.078) | +0.033 (spans 0) | +0.029 (spans 0) |
| `formGe5Rate_10` | **+0.057** (+0.031, +0.082) | +0.025 (spans 0) | +0.029 (spans 0) |
| `formTopGameShare_10` | **−0.028** (−0.049, −0.005) | −0.008 (spans 0) | +0.003 (spans 0) |
| `formMeanMinusTrimmed_*` | spans 0 at every window | mixed | mixed |

Nineteen of 21 dev CIs exclude zero; on the 2026 holdout, **19 of 21 span
zero**. Only `formTrimmedMean_10` is confidently positive in all three splits,
and its effect size is ρ ≈ 0.03 — a rank correlation that explains ~0.1% of
next-game scoring variance.

**Out-of-sample prediction, frozen models:**

| Split | control MAE | meanOnly MAE | consistency MAE |
|---|---|---|---|
| development | 2.4325 | 2.4320 | **2.4303** |
| validation | **2.5011** | 2.5036 | 2.5019 |
| holdout | 2.5169 | 2.5150 | **2.5140** |

| Split (P ≥ 4 runs) | base rate | control Brier | meanOnly | consistency |
|---|---|---|---|---|
| development | 0.5529 | 0.24363 | 0.24360 | **0.24322** |
| validation | 0.5525 | **0.24379** | 0.24403 | 0.24421 |
| holdout | 0.5507 | **0.24632** | 0.24628 | 0.24649 |

The consistency model wins development (it was fit there), loses validation on
both metrics, and wins holdout on runs by 0.003 while losing it on probability.
Every margin is between 0.001 and 0.003 — roughly **one run per 400 games**.
That is noise, not an edge.

**The confound, stated plainly.** These consistency statistics are *levels*
(a team's recent median, its recent trimmed mean), and a level partly measures
team quality rather than form. That is exactly why the dev correlations look
larger here than MLB-RSCH-0005's deviation correlations (ρ ≈ 0.002–0.013, all
CIs spanning zero) while the frozen regression comparison shows no lift: the
control already contains the season-to-date baseline, so once you know how good
an offence is, knowing its recent shape adds essentially nothing.

**Verdict: INSUFFICIENT EVIDENCE of predictive value. No model change.**

### 3.2 Market-relative form — **insufficient evidence, by coverage**

Team-total lines were derived from this repository's own archived Kalshi
ladder (`lib/team_total_line_history.py`): the market's **median expected
runs**, i.e. the point where the ladder's P(over) crosses 50%, interpolated
between the bracketing rungs, refusing when the ladder does not bracket 50%, is
materially non-monotone, or has fewer than two priced rungs.

That archive spans **48 dates, 2026-08-01 → 2026-09-17, 1,001 usable team-game
lines**. It therefore covers **0% of development, 0% of validation and 19% of
holdout (648 rows)**. Within those 648 rows every CI spans zero:

| Predictor | holdout ρ (95% CI) |
|---|---|
| `formTtMeanMargin_10` | +0.056 (−0.002, +0.096) |
| `formTtMedianMargin_10` | +0.039 (−0.026, +0.093) |
| `formTtOverRate_10` | −0.010 (−0.068, +0.042) |

Two things are worth noting for later, *as hypotheses only*: the **margin**
versus the line points positive while the **over-rate** is flat (a margin keeps
information a binary clear throws away), and the mean margin is the largest
effect anywhere in this experiment. Neither is evidence. The question the
mission raised — is line-relative form more meaningful than raw R/G? — is
**not yet answerable**, purely because the archive is six weeks old, and it
becomes answerable simply by waiting: the capture already runs daily.

### 3.3 Descriptive usefulness for handicapping — **clear, and acted on**

This is where the value is, and it needs no statistical claim at all. The
mission's own example:

```
scores 3, 4, 2, 20, 5, 1, 1   →   L7 mean 5.14  (above league average)
                                  L7 median 3
                                  cleared 5 runs in 2 of 7
                                  56% of the window's runs came from one game
```

A mean of 5.14 reads as a hot offence. Nothing about that team is hot. The
whole point of §4 is that this is now impossible to miss — and the negative
`formTopGameShare_10` development correlation (−0.028, CI excluding zero) is at
least *directionally* consistent with the handicapping intuition that an
outlier-carried window is if anything a mild negative, though it does not
replicate and must not be leaned on.

---

## 4. What actually changed in production

**Nothing in the model.** What changed is what a handicapper is shown.

`scripts/fetch_team_offense_form.py` → `data/team_offense_form.json` →
`scripts/enrich_data.py` attaches `offenseForm` / `offenseFormLine` /
`offenseFormLabel` to every slate team block, and `RUN_THE_SLATE.md`'s PRE-SCAN
now requires that line verbatim:

```
L7: 5.1 avg | 3.0 med | 3,4,2,20,5,1,1 | 3/7 >=4 | 2/7 >=5 |
    max 20 (56% of L7 runs) | OUTLIER-DEPENDENT | TT overs 2/7
```

The label is deliberately conservative and is **not** a function of the mean:

- `HOT` requires a **majority** of the window clearing 4 runs **and** a median
  ≥ 4 **and** no outlier dependence — repeated scoring, not one explosion;
- `OUTLIER_INFLATED` is what a high mean carried by one game gets. It reads as
  a warning, not a green light;
- `COLD`, `NEUTRAL`, `UNKNOWN` otherwise.

The `TT overs` segment appears only where archived lines actually covered those
games. Its absence means *not measured*, never *zero*.

`HANDICAPPING_PLAYBOOK.md` §5 makes reading this line a methodology
requirement, and `PLAYBOOK_LESSONS.md` carries
`recent-mean-runs-is-not-form` as **SUPPORTED**, citing MLB-RSCH-0005 and this
experiment.

---

## 5. Limitations

- A team's first 20 completed games each season are excluded — no reliable
  baseline or 10-game window exists yet. Never approximated.
- The frozen regressions are plain closed-form OLS: no regularization, no
  interactions, no nonlinearity. A deliberately simple, non-tuned comparator,
  not a production-grade scoring model.
- **Starting pitcher, park and weather are not controlled for.** This measures
  only what a form statistic adds over a season/opponent baseline.
- The probability arm is a linear probability model; predictions are clipped to
  [0.01, 0.99] purely for scoring safety, not as a fitted parameter.
- H7 is bounded by a six-week market archive (see §3.2) and must be read as
  coverage evidence only.

---

## 6. Reproducing it

```bash
python3 scripts/edgelab/run_team_offense_consistency_experiment.py
python3 scripts/edgelab/run_team_offense_consistency_experiment.py --skip-market-arm
```

No network access and no new fetch: it reads MLB-RSCH-0003's committed schedule
cache and this repository's own committed Kalshi observations. Deterministic
(fixed bootstrap seed), so a rerun reproduces the committed report.
`productionBehaviorChanged` is hard-coded `False`.
