# MLB-RSCH-0025: Methodology-V2 Retest of the Mean-Calibration Candidates

Status: **COMPLETE. V2_RETEST_DEV_VAL_ONLY_HOLDOUT_DID_NOT_CONFIRM — REJECT.**

RESEARCH ONLY. No production changes, no candidate activation. MLB-RSCH-0014's
own registration, artifact and conclusion are unmodified.

## 1. Why this experiment existed

The Phase-1 Methodology-V2 triage audit found that of 21 candidate rows across
MLB-RSCH-0002..0020, MAE participated in 11 selections but *caused* rejection in
only three — MLB-RSCH-0014's C1/C2/C3 mean-calibration maps. Each was rejected
on "DEV MAE delta not negative" while, in the same artifact:

- **RMSE improved** (C1 3.1076, C2 3.1070, C3 3.1076 vs C0 3.1104), and
- **frozen-NB Brier improved in all five families on both DEV and VAL.**

MLB-RSCH-0014 recorded `holdout2026: null` / `passingCandidates: []`, so the
2026 holdout had **never** been unlocked for these candidates — a genuinely
blind test still existed.

## 2. Design

Candidate specifications frozen and reused unchanged: the fitters,
`calibrate_value` and `attach_calibrated_predictions` are imported from
`run_mean_calibration_experiment`, with kind tokens resolved from that module's
own `C1`/`C2`/`C3` constants. DEV-fit parameters reproduced RSCH-0014's
originals exactly (C1 `a=-1.187409, b=1.293828`; C2 four-parameter side-specific;
C3 `a=-1.320708, b=1.354664, c=-0.0069`), and C0's HFA reproduced at 0.0114.

Gate: `lib.edgelab.research.methodology_v2.mean_candidate_gates_v2` — DEV MSE
< 0, DEV NB-NLL ≤ 0, DEV Brier ≤ 0, VAL MSE < 0, VAL Brier ≤ 0. MAE is passed
in for reporting and is **structurally ignored**. Simplicity-first tie-break
(C1 → C3 → C2) fixed before any holdout access.

## 3. DEV + VAL: the MAE diagnosis is vindicated

| Candidate | DEV MSE Δ | VAL MSE Δ | DEV NLL Δ | DEV Brier Δ | VAL Brier Δ | (MAE Δ, secondary) | V2 gate |
|---|---|---|---|---|---|---|---|
| C1 global affine | **−0.018184** | **−0.023051** | −0.000806 | −0.000500 | −0.000323 | +0.00808 | **PASS** |
| C3 quadratic | −0.018188 | −0.022873 | −0.000805 | −0.000500 | −0.000321 | +0.008085 | **PASS** |
| C2 home/away affine | **−0.021775** | **−0.030565** | −0.000991 | −0.000570 | −0.000505 | +0.007527 | **PASS** |

**All three pass the full Methodology-V2 gate.** Every candidate MLB-RSCH-0014
rejected improves the mean-consistent metric, the distributional gate, and the
probability gate — on both splits. The Phase-1 hypothesis was correct: these
were rejected by the wrong loss function, not by baseball evidence.

The mechanism is visible in the bias: C1 takes DEV mean residual from
**−0.0897 to −0.000002** — it is doing precisely what a mean-calibration map
should do, which is exactly why MAE (a median-targeting loss) penalised it.

## 4. Blind 2026 holdout: it does not confirm

C1 (selected by the preregistered simplicity-first rule) was evaluated once on
the genuinely untouched 2026 holdout:

| Metric | C0 control | C1 | Δ |
|---|---|---|---|
| MSE | 10.426415 | 10.425059 | **−0.001897** (CI [−0.0268, +0.0235]) |
| RMSE | 3.228996 | 3.228786 | −0.00021 |
| Bias | −0.07906 | **+0.025522** | overshoots past zero |
| NB-NLL | — | — | **+0.000016** (CI [−0.0011, +0.0011]) |
| Brier (primary 4 families) | — | — | **+0.000012** |

Holdout Brier by family is genuinely split — moneyline −0.000178 and
team_total_home −0.000299 (better), but game_total +0.000283,
team_total_away +0.000244, run_margin +0.000054 (worse).

MSE still improves, but by a hair with a CI straddling zero, and both the
distributional and probability gates turn marginally *positive*. Under the
preregistered confirmation rule (holdout MSE < 0 **and** holdout Brier ≤ 0),
**C1 fails**. Classification `V2_RETEST_DEV_VAL_ONLY_HOLDOUT_DID_NOT_CONFIRM`,
disposition **REJECT**. No re-fit, no alternate candidate promoted, no threshold
relaxed.

## 5. What this means

Two findings, both worth keeping:

1. **The MAE methodology error was real and did suppress genuine
   mean-and-probability improvements.** C1/C2/C3 are not statistical noise:
   they improve MSE, NLL and Brier consistently across ~8,500 games of DEV+VAL.
   Methodology V2 is justified by direct evidence, not just theory.
2. **But the suppressed candidates were not season-saving.** The DEV/VAL gains
   are small (Brier ~5e-04) and do not survive a blind 2026 holdout, where the
   bias correction overshoots. A fixed affine map fitted on 2022–24 does not
   transport to 2026's run environment — the same transport failure
   MLB-RSCH-0023 found for probability recalibration.

The honest summary: the graveyard was *slightly* wrongly stocked, but nothing
in it is a 2026 profit lever.

## 6. Governance

- MLB-RSCH-0014 remains historically correct under its own v1 rules; nothing
  there was modified or reinterpreted.
- The 2026 holdout was unlocked exactly once, only after the V2 DEV+VAL gate
  passed, only for the simplicity-first selected candidate.
- Max disposition was SHADOW_CANDIDATE; the actual outcome is REJECT.
- Production unchanged.

## 7. Tests

`tests/edgelab/test_v2_calibration_retest_and_triage.py` — 24 tests covering
triage-audit integrity (HIGH-priority rows must be MAE-caused with an
unobserved holdout; V2-dead candidates such as S1/B3 must never be marked for
retest; summary counts internally consistent; audit writes only its own
artifact) and retest governance (candidate functions imported not
reimplemented, kind tokens resolved from RSCH-0014's own constants, no new
candidate invented, V2 gate helper used, registration guard invoked, MAE
labelled secondary, holdout gated behind selection, max disposition
SHADOW_CANDIDATE).
