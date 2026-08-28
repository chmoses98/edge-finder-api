# MLB-RSCH-0022: Production Probability Calibration & Market-Relative Skill

Status: **COMPLETE (walk-forward audit of archived production predictions,
2026-08-02 .. 2026-08-28). MARKET_SUPERIOR_EVERYWHERE / SYSTEMATIC
OVERCONFIDENCE FOUND.**

RESEARCH ONLY. Nothing fit, no thresholds tuned, no ROI-based selection,
zero new API calls, production unchanged.

## 1. What this is

The first direct audit of PRODUCTION's own archived pregame Kalshi-market
probabilities (`data/edgelab/model_evaluations/`, EVALUATED rows, last
evaluation per ticker) against settled Kalshi outcomes
(`data/edgelab/settlements/`) and against Kalshi's own contemporaneous
price (each row's archived `marketImpliedProbability`). These are the
exact numbers production used during August 2026 — not a research proxy.

Evidence level: **E4_PROSPECTIVE_SHADOW** — per
`lib.edgelab.pit_provenance`'s own rules, prospectively-captured
predictive inputs support E4 ("a live, forward-looking claim"), never
E2/E3 (which assert proven *historical* PIT depth). No shadow variant
model is involved; the label here means exactly "prospectively captured,
prospectively evaluated."

## 2. Corpus

| | |
|---|---|
| Settled tickers available | 100,695 |
| EVALUATED production rows | 7,052 (0 excluded for missing probabilities) |
| Audit rows (last-per-ticker × settled) | **3,137 across 293 games, 13 families** |
| DEV half (settle ≤ 08-17) | 408 |
| VAL half (08-18 .. 08-28) | 2,729 |
| FORWARD holdout | settle dates > 08-28 — **preregistered, not computed here** |

The DEV/VAL date halves are heavily imbalanced because the prospective
capture system ramped up in mid-August. This weakens the two-half
replication rule (several families are `INSUFFICIENT_ONE_HALF` in the
thin DEV half); the pooled FDR-controlled results are the primary
findings, with the replication verdicts reported as-is. This imbalance
is an artifact of archive growth, disclosed rather than patched.

## 3. Headline result (pooled, n=3,137 / 293 games)

| | Production model | Kalshi price |
|---|---|---|
| Brier | **0.2268** | **0.1719** |
| Log loss | 0.6750 | 0.5667 |
| Expected calibration error | **0.1020** | **0.0445** |

Paired Brier delta (model − market): **+0.0549**, 90% game-clustered CI
[+0.0391, +0.0718], bootstrap p ≈ 0. **Kalshi's own prices are a
substantially better probability forecast than production's numbers,
overall and in every family.**

## 4. Per family (pooled; negative delta would mean model beats market)

| Family | n | games | Δ Brier | CI | p | FDR sig (10%) |
|---|---|---|---|---|---|---|
| first_inning_run (NRFI/YRFI) | 214 | 214 | +0.0089 | [-0.0044, +0.0216] | 0.257 | no |
| inning_total | 196 | 28 | +0.0252 | wide | 0.46 | below floor |
| game_total | 361 | 60 | +0.0377 | [+0.004, +0.073] | 0.065 | yes |
| inning_result | 311 | 104 | +0.0467 | [+0.018, +0.075] | 0.008 | yes |
| game_result (ML) | 125 | 87 | +0.0487 | [+0.019, +0.077] | 0.006 | yes |
| team_total | 953 | 276 | +0.0499 | [+0.035, +0.064] | ~0 | yes |
| winning_margin | 352 | 67 | +0.0676 | [+0.036, +0.101] | 0.002 | yes |
| pitcher_strikeouts | 551 | 43 | +0.0935 | [+0.061, +0.126] | ~0 | yes |
| pitcher_outs | 74 | 41 | +0.1131 | [+0.063, +0.163] | ~0 | below floor |

Replication across the (imbalanced) halves: `game_result` and
`team_total` formally replicate as MARKET_BETTER; most others were
insufficient in the thin DEV half; none replicated as MODEL_BETTER.

**Reading**: the *least-bad* family is NRFI/YRFI (statistically
indistinguishable from the market). The *worst* families are the pitcher
props — refuting the plausible hypothesis that prop markets, lacking
sharp benchmarks, would be where production shines. Right now they are
where production is weakest relative to Kalshi's own pricing.

## 5. The mechanism: systematic overconfidence (probabilities too extreme)

Model reliability by fixed price band (pooled):

| Model prob band | n | mean model prob | outcome rate | bias |
|---|---|---|---|---|
| 0.0–0.2 | 947 | 0.094 | **0.233** | **−0.139** |
| 0.2–0.4 | 805 | 0.292 | 0.400 | −0.108 |
| 0.4–0.6 | 684 | 0.504 | 0.480 | +0.024 |
| 0.6–0.8 | 404 | 0.677 | 0.572 | **+0.105** |
| 0.8–1.0 | 297 | 0.901 | 0.801 | **+0.100** |

A textbook overconfidence signature: low-probability calls happen far
more often than stated, high-probability calls less often. Production's
probability surface is systematically **too extreme** in both
directions. (Calibration OLS slopes < 1 in most families corroborate.)
The market's low band is nearly perfectly calibrated (0.087 stated vs
0.087 realized); its one notable soft spot in this sample is the
0.6–0.8 band (+0.163 bias, n=262) — a descriptive observation only.

## 6. Descriptive economics (fixed bands, taker fees; never a tuned rule)

| \|model − market\| band | n | win rate | gross EV/contract | fee-aware net EV |
|---|---|---|---|---|
| 0.00–0.05 | 683 | 0.508 | +0.012 | −0.003 |
| 0.05–0.10 | 669 | 0.471 | −0.010 | −0.026 |
| 0.10–0.20 | 827 | 0.508 | +0.029 | +0.011 |
| 0.20–1.01 | 958 | 0.301 | +0.035 | +0.020 |

Reported for completeness. Given the model loses to the market on proper
scoring in every family, the positive net EV in the widest-disagreement
band must NOT be read as an exploitable edge — with an overconfident
model, the largest disagreements are exactly where model error
concentrates, and this band's 30% win rate is consistent with longshot
positions whose apparent EV rides on a small sample (198 games). No
betting conclusion is drawn from this table.

## 7. Bet-filter signal (preregistered dimensions, descriptive)

- `dataQuality = full` / `lineupConfirmationState = CONFIRMED` rows show
  roughly **half** the model-vs-market gap (+0.031) of `UNKNOWN` rows
  (+0.064): production is least-bad when its inputs are complete —
  directionally useful for bet filtering, not yet a rule.
- `PAPER`-confidence rows (+0.013) show a smaller gap than the general
  pool; genuinely recommended-bet rows are too few here to score
  separately (HIGH n=2).
- Last-vs-first evaluation per ticker: −0.00018 (CI spans 0) — no
  measurable intraday sharpening.

## 8. Answers to the preregistered questions

**Calibrated anywhere?** Not well, anywhere; least miscalibrated family
ECEs are ~0.05 (inning_result), worst ~0.18 (pitcher_outs).
**Skillful vs the market anywhere?** No family shows model-better-than-
market; NRFI/YRFI is the only statistical tie. **Where is the market
beating us most?** Pitcher props, winning margin, team totals.
**Favorite-longshot structure?** Yes — H3 confirmed in direction:
miscalibration is worst in the extreme bands, in the overconfident
(too-extreme) direction.

## 9. Profitability translation (Section H)

1. **Families affected**: all 13 audited; most acutely pitcher props,
   winning_margin, team_total.
2. **Remaining 2026 volume**: at August's capture rate (~190 settled,
   evaluated contracts/day), roughly **5,500–6,500 more scoreable
   contracts** remain in the regular season, plus postseason.
3. **Raw probability vs filtering**: this audit changes neither yet — it
   *measures*. Its immediate use is protective bet-filtering knowledge
   (distrust model edges in the worst families; prefer complete-input
   rows) and it precisely motivates a recalibration experiment.
4. **Expected direction**: shrinking production's probability surface
   toward the market/base rates (slope < 1 in logit space) should
   substantially improve Brier — to be TESTED, not assumed.
5. **Exploitable disagreement?** Not demonstrated. The market currently
   out-forecasts us; apparent wide-disagreement EV is not trustworthy.
6. **Fee-aware economics**: descriptive only (section 6).
7. **Before real-money use**: any correction must be fit on DEV dates,
   validated on later dates, and confirmed on the preregistered FORWARD
   window (settle > 08-28) — then human production-change review.

## 10. Implementation readiness

**LEVEL 0 — INTERESTING ONLY** for any betting change (this audit fits
nothing). The *finding* (overconfidence + family ranking) is LEVEL-1-
grade motivation for a separately preregistered recalibration
experiment (MLB-RSCH-0023) whose product could reach LEVEL 1–2.

## 11. Tests

`tests/edgelab/test_run_production_calibration_audit_experiment_script.py`
— 27 tests: registration idempotency + E4-per-PIT-framework, nothing-fit
proofs, last/first-per-ticker row rules, unsettled-never-fabricated,
settlement-family labeling, DEV/VAL boundary, paired-delta directionality,
deterministic bootstrap p-values, full BH step-up cases, replication
verdicts, fixed-band economics accounting, forward-holdout-not-computed
proofs, no-network proofs. Full `tests/edgelab/` suite green.

## 12. Honest caveats

- One month of data; 293 games; game-clustered CIs are wide at family
  level. The pooled overconfidence signature, however, is enormous
  relative to its uncertainty.
- The cohort is "markets production evaluated that later settled" — not
  a random sample of all markets, and overwhelmingly non-recommended
  markets (full-universe capture). The recommended-bet subset is too
  small to score separately yet.
- Kalshi's price is used as a probability forecast including any
  vig/spread — a handicap that works AGAINST the market in this
  comparison, which it wins anyway.
- The FORWARD window (settle > 2026-08-28) is the preregistered genuine
  holdout for these findings and for any successor recalibration —
  untouched by this run.
