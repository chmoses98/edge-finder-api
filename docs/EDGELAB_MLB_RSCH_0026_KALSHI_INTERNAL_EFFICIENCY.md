# MLB-RSCH-0026: Kalshi Internal Market Efficiency

Status: **COMPLETE. LEVEL 0 — NO VALIDATED MARKET INEFFICIENCY (frozen for FORWARD rerun).**

RESEARCH ONLY. No production changes, no candidate activation. Strictly
independent of our baseball model: production's probability is dropped at
corpus construction and never read by any fitting or scoring path.

## 1. Question and hypothesis

MLB-RSCH-0024 showed our model adds no incremental information beyond Kalshi's
price, but surfaced one positive signal: **Kalshi's own miscalibration**. This
experiment asks whether Kalshi's pregame vig-free fair price can be transformed
using only decision-time market information to predict outcomes better
out-of-time.

Preregistered single hypothesis (fixed before the chronological split was
evaluated): a classic **favourite-longshot bias**, corrected by ONE monotone
logit-shrink toward the base rate,
`p = sigmoid(logit(base) + β·(logit(fair) − logit(base)))`, with a single β
bounded [0.2, 2.0] fit on TRAIN by Bernoulli NLL. β=1 is the market unchanged.
No per-band parameters, no optimized cutoffs.

The pooled motivation was strong and monotone (all 2,635 rows): longshots
priced 0.132 settle 0.161 (bias −0.029); favourites priced 0.696 settle 0.622
(bias +0.074).

## 2. Design

- Corpus: 2,635 settled binary contracts / 235 games / 9 families, 08-04..08-26.
- TRAIN = settle ≤ 08-24 (1,454 rows / 179 games); VAL = 08-25..28 (1,181 / 56).
  Both halves carry all nine families (full-family capture began 08-23).
- FORWARD (> 08-28): **verified empty** at design time — the settled archive
  ends 2026-08-27 — so it is untouched by construction, not by discipline alone.

## 3. Result: the gate fails, and the reason is the finding

| | TRAIN | VAL |
|---|---|---|
| Market Brier | 0.201333 | 0.206855 |
| Shrunk Brier | 0.201293 | 0.206557 |
| Paired Brier Δ | −0.00004 | **−0.000297** (CI [−0.0005, −0.0001]) |
| Log-loss Δ | — | **−0.000979** |
| ECE | — | 0.059318 → **0.056969** |
| Improving price bands | — | **5 of 5** |

Fitted **β = 0.9833**, game-clustered CI **[0.835, 1.158]** — the CI includes
1.0, so under the preregistered rule the candidate **FAILS**. Classification
`LEVEL_0_NO_VALIDATED_MARKET_INEFFICIENCY`. Economics were deliberately **not
computed**: economics never rescue a forecaster that failed proper scoring.

This is a genuine near-miss worth understanding rather than dismissing: VAL
improves on *every* scoring criterion and in *every* band, yet β is essentially
1. Both facts are true because the shrink is tiny — it is nudging an already
well-calibrated price by a hair, and the CI cannot distinguish that nudge from
doing nothing.

## 4. Why: the bias lives in VAL, not TRAIN

| Price band | TRAIN bias | VAL bias |
|---|---|---|
| 0.0–0.2 | +0.0039 | **−0.0581** |
| 0.2–0.4 | +0.0277 | −0.0183 |
| 0.4–0.6 | +0.0139 | **+0.0900** |
| 0.6–0.8 | +0.0711 | +0.0758 |
| 0.8–1.0 | −0.0114 | +0.0725 |

**TRAIN is nearly calibrated; VAL is badly miscalibrated in a textbook
favourite-longshot pattern.** The pooled bias that motivated this experiment is
almost entirely a VAL-period phenomenon. A β fitted on TRAIN therefore *cannot*
learn the correction — and honestly reports β ≈ 1. Fitting β on VAL instead
would manufacture exactly the in-sample illusion this program's rules forbid,
so it was not done.

Family β's, where sample floors allowed, are correspondingly unstable and
contradictory: game_total 0.888 and winning_margin 0.672 (shrink) versus
inning_result 1.645 and first_inning_run 1.406 (sharpen). No coherent
market-wide structure at this depth. Five families fell below the preregistered
sample floor and were not fit — notably pitcher_strikeouts (228 TRAIN rows but
only 19 independent games).

Date stability across the two VAL dates is directionally consistent
(08-25 −0.000347 over 954 rows; 08-26 −0.000090 over 227) but that is two days.

## 5. Honest interpretation

Two readings are consistent with this evidence, and the data cannot yet
separate them:

1. Kalshi genuinely became favourite-longshot-biased in late August, and TRAIN
   simply predates it — in which case a correction fitted on *current* data
   could be real and valuable.
2. The VAL-period bias is a two-day, 56-independent-game artifact of a
   specific slate composition.

Distinguishing these requires the FORWARD window — which is exactly what the
frozen artifact exists for. **No claim of exploitable market inefficiency is
made.** In particular, calibration bias is not profit: even the VAL bias would
have to survive the bid/ask spread (median 1¢, plus the half-spread the ask
sits above the mid) and taker fees before it could pay.

## 6. Frozen artifact and automatic rerun threshold

`data/edgelab/analytics/frozen_mlb_rsch_0026_forward_model.json` carries the
exact β (0.9833), base rate (0.430536), clamps, bounds, fair-price definition,
`usesProductionModel: false`, and a deterministic forward-evaluation rule
(score post-08-28 rows with this exact β; never refit).

**Preregistered automatic rerun threshold:** rerun this experiment once the
settled archive contains at least **1,000 rows across ≥60 independent games**
with settle date > 2026-08-28 — enough to fit β on a genuinely current TRAIN
window and validate on a later one, which is the design this run could not have.

## 7. Governance

Production probability never entered any fitting or scoring path. No ROI
selection; economics were not even computed given the scoring failure. No band
cutoff, sample floor, or threshold was changed after seeing results. The
FORWARD window remains untouched. Max disposition was LEVEL 1; the actual
outcome is LEVEL 0.
