# MLB-RSCH-0024: Market-Anchored Residual Model

Status: **COMPLETE. LEVEL 0 — NO INCREMENTAL SIGNAL DEMONSTRATED.**

RESEARCH ONLY. No production changes, no candidate activation, no
staking/execution/fee changes. Zero new API calls. FORWARD window
(settle > 2026-08-28) untouched; a frozen forward-model artifact is
emitted for later scoring.

## 1. The question

When production and Kalshi disagree, does the production baseball model
contain **incremental predictive information beyond Kalshi's own
contemporaneous fair probability**?

Form (one parameter, logit space):

```
m = logit(kalshi fair mid);  r = logit(model) - m
p_candidate = sigmoid(m + alpha * r)
```

α=0 → market alone (M0). α=1 → production as-is (M1). 0<α<1 → the model
adds information but should be shrunk toward the market. α<0 → model
disagreement is an **anti-signal**. Fit on TRAIN by Bernoulli NLL,
bounded [−2, 3] (preregistered).

**The decisive test is M2 vs M0 (the market), never M2 vs M1** —
MLB-RSCH-0022 already showed M1 is the weaker standalone forecaster, so
beating it proves nothing.

## 2. Two design corrections this experiment required

**(a) Canonical fair price.** MLB-RSCH-0022 used each row's archived
`marketImpliedProbability`, whose `probabilityAdapter` is `kalshiVF`
(vig-free mid — correct) on 3,361 rows but `executableMarketProb` (an
**ask price**) on 3,691. Measured here: ask-adapter rows carry **+0.049**
mean upward bias vs **+0.013** for vig-free rows. Pooling them would
corrupt any market benchmark. This experiment therefore reconstructs a
true vig-free midpoint from the observation archive's own
`yesBid`/`yesAsk` (latest valid pregame observation per ticker; median
spread 1¢), retaining bid, ask, mid, and executable price separately.
**The executable ask is used only in the secondary economics, never as a
truth probability.**

**(b) Family-balanced chronological design.** MLB-RSCH-0023 failed
because its DEV window lacked major families. Auditing daily coverage
first showed full nine-family capture begins **2026-08-23**, so both
halves below contain **all nine** eligible families:

| Split | Rows | Games | Families | Dates |
|---|---|---|---|---|
| TRAIN | 1,454 | 179 | 9 | 08-04 .. 08-24 |
| VAL | 1,181 | 56 | 9 | 08-25 .. 08-26 |
| FORWARD | — | — | — | settle > 08-28, untouched |

Eligible rows 2,635 of 3,137 audit rows (502 excluded: no reconstructable
pregame fair price — never imputed). Hitter props are absent from the
evaluated+settled join and were not forced in; three-way structures were
not binarized.

## 3. Headline result

| Forecaster | VAL Brier | VAL log loss | VAL ECE |
|---|---|---|---|
| **M0 Kalshi fair mid** | **0.20686** | **0.60199** | 0.0593 |
| M1 production | 0.21955 | 0.66306 | 0.0987 |
| M2 residual (α fit) | 0.20685 | 0.60199 | 0.0603 |

**Global α = 0.0004**, game-clustered CI **[−0.218, +0.205]**.
TRAIN NLL: 0.58331 at α=0, 0.66313 at α=1, 0.58331 at the fitted α — the
optimizer, free to choose anything in [−2, 3], landed on **"ignore the
model entirely."**

**PRIMARY: VAL M2 − M0 Brier = −0.000002** (CI [−0.000, 0.000]);
log loss −0.000004. That is not an improvement; it is arithmetic noise
around "M2 ≡ M0."

Reference: M1 − M0 = **+0.012694** Brier (production is worse than the
market, replicating MLB-RSCH-0022 on this independent, corrected
benchmark).

**Selection FAILS** on two preregistered gates: α's CI includes 0, and
VAL improvement is concentrated in a single family.

## 4. Tier and family structure (the informative part)

| Tier | TRAIN n | α | α CI | VAL Δ vs market |
|---|---|---|---|---|
| TIER_TOTALS | 702 | **+0.102** | [−0.184, +0.361] | −0.00103 |
| TIER_INNING | 283 | **−0.474** | [−1.023, +0.148] | +0.00404 |
| TIER_MARGIN | 153 | **−0.585** | [−1.328, **−0.065**] | +0.01097 |
| TIER_GAME_OUTCOME | 57 | below minimum sample | — | — |
| TIER_PROPS | 259 (19 games) | below minimum sample | — | — |

Exploratory family α (BH-FDR at 10%; **none significant**):

| Family | α | α CI | p | VAL Δ vs market | M1−M0 |
|---|---|---|---|---|---|
| game_total | +0.177 | [−0.308, +1.585] | 0.56 | −0.00209 | +0.00224 |
| first_inning_run | +0.173 | [−0.367, +0.727] | 0.75 | +0.00330 | +0.03190 |
| team_total | +0.049 | [−0.165, +0.245] | 0.77 | +0.00009 | +0.00803 |
| winning_margin | **−0.585** | [−1.328, −0.065] | 0.08 | +0.01097 | −0.00100 |
| inning_result | **−1.125** | [−2.000, +0.036] | 0.12 | +0.01861 | −0.00471 |

The only two α's whose CIs approach excluding zero are **negative**
(winning_margin, inning_result) — production's disagreement in those
families is directionally *harmful*. Neither survives FDR, so neither is
claimed as established; but nothing here suggests a positive signal
waiting to be found.

**H3 refuted**: first_inning_run had MLB-RSCH-0022's smallest
model-vs-market gap, yet its α is indistinguishable from zero and its
VAL delta is unfavorable. A small standalone gap does not imply
incremental information.

## 5. Disagreement magnitude — H4 confirmed

| \|model−market\| | TRAIN n | bucket α | VAL M1 − M0 |
|---|---|---|---|
| <2.5pp | 156 | −0.348 | +0.0011 |
| 2.5–5pp | 182 | −0.096 | +0.0033 |
| 5–7.5pp | 230 | +0.021 | +0.0081 |
| 7.5–10pp | 193 | −0.709 | +0.0082 |
| 10pp+ | 693 | +0.046 | **+0.0246** |

Bucket α's are erratic and mostly ≤0; meanwhile production's *harm*
versus the market grows monotonically with disagreement size, reaching
+0.0246 Brier in the 10pp+ bucket. **Large disagreement is error, not
edge** — the preregistered H4 direction, confirmed on out-of-time data.

## 6. Direction, input quality, price band

- **Directional**: model-above-market α = −0.453 (anti-signal);
  model-below-market α = +0.069. Production's bullish disagreements are
  the more harmful kind.
- **Input quality** (preregistered interaction): high-quality rows α =
  +0.040 vs lower/unknown α = −0.008 — both ≈0. Notably, on VAL the
  M1−M0 gap is *larger* for high-quality rows (+0.0157) than for
  lower/unknown (+0.0124), which does **not** reproduce MLB-RSCH-0022's
  pooled half-gap pattern (VAL high-quality n=94 only). Reported as-is.
- **Price band / Kalshi structural bias** (VAL, market's own
  calibration): the market is not perfectly calibrated here — bias
  −0.058 in 0–0.2, **+0.090** in 0.4–0.6, +0.076 in 0.6–0.8, +0.073 in
  0.8–1.0 (it is favourite-biased in this short window). Its ECE is
  still 0.059 vs production's 0.099. **This is potential market
  mispricing, a different and separate thing from model incremental
  signal**, and this experiment does not claim it is exploitable.

## 7. Secondary economics — and the number that matters most

Under M2 (α≈0, i.e. the fair mid), **zero rows clear the executable
ask**. This is structural, not a bug: the ask sits a half-spread above
the mid (median ask − mid = 0.5pp), so a forecaster equal to the mid can
never show positive EV at the ask. Verified directly: fair mid > ask in
**0 of 1,181** VAL rows.

The economically decisive counterfactual, computed for context:
**production's own 353 apparent "+EV" rows in VAL** (modelP > executable
ask) went 175 wins / 353 for **gross P/L −14.59 units, −4.1% per
contract *before fees***. Fees would make it worse.

That is the practical translation of α≈0: the disagreements production
currently treats as edges were, in this window, systematically
value-destroying.

## 8. Answers to the preregistered questions

**Does production add incremental information beyond Kalshi?** **No** —
globally α≈0 with CI spanning zero, and M2 ≡ M0 to six decimals.
**Where might it?** Nowhere established. TOTALS is the only tier with a
positive α and a (tiny) favorable VAL delta; game_total is the single
improving family — below the concentration gate, not FDR-significant.
**Where does it actively hurt?** Winning margin and inning result
(negative α), and everywhere disagreement is large.
**Is large disagreement edge or error?** **Error** (H4 confirmed).

## 9. Classification and governance

**LEVEL 0 — NO INCREMENTAL SIGNAL DEMONSTRATED.** Not a shadow
candidate. Production unchanged; nothing activated; no ROI was used to
select anything (economics computed only after scoring, and reported
even though unfavorable).

A frozen forward-model artifact
(`data/edgelab/analytics/frozen_mlb_rsch_0024_forward_model.json`) is
emitted with the exact α, family mapping, clamps, bounds, training-end
date and a deterministic forward-evaluation rule, so post-08-28
settlements can be scored later **without refitting** — the honest way
to let evidence accumulate.

## 10. Tests

`tests/edgelab/test_run_market_residual_experiment_script.py` — 39
tests: residual-form endpoint exactness (α=0 ⇒ market, α=1 ⇒ model),
negative-α direction, unit-interval bounds, α recovery on deterministic
synthetics (≈1 informative, ≈0 pure noise, negative for anti-signal),
bounds/determinism/minimum-sample, fair-price-is-mid-not-ask proofs,
scoring-never-touches-executable-ask proofs, primary-comparison-is-vs-
market proofs (including a test that a candidate beating only production
FAILS selection), all selection gates, fixed buckets/tiers, FORWARD-
untouched proofs, no-ROI-fitting proofs, loader-reuse proofs, BH-FDR.

## 11. Honest caveats

- VAL is 1,181 rows but only **56 independent games** (two settle
  dates) — game-clustered CIs are correspondingly wide. The α≈0
  conclusion rests primarily on TRAIN's 179 games, where the NLL surface
  is decisive.
- Two tiers (game outcome, props) fell below the preregistered minimum
  sample and were not fit — props especially deserve a later look with
  more games.
- Kalshi's own favourite bias in this window is real but short-sample;
  it is *not* evidence our model can exploit it.
