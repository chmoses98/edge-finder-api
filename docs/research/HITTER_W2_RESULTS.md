# MLB hitter W2 challenger: held-out results

**Status: research only.** No production hitter output, app export, publication, staking or authority is
changed.

* **Preregistration:** `docs/research/HITTER_W2_PREREG.md`, commit `cd9e62e41`. It was committed and pushed
  before any held-out forecast was scored.
* **Audit:** `docs/research/HITTER_ENGINE_AUDIT.md`, commit `8de1315ae`.
* **Frozen parameters:** `data/research/hitter_w2/params_v1.json`
  (`sha256:e34c413e9d96727f581090710d43c54bc515276479176e8286d3b6e4d73c73e5`). They were fitted on 2024–2025
  only.

**Verdict: `REJECTED`, under the strict preregistered rule.** The challenger removes the incumbent's hits and
RBI biases and improves every proper score: CRPS, Brier, log loss and RMSE for H, TB, HR, RBI and R, all with
CIs excluding 0. It is rejected because:

1. **vs incumbent.** MAE *of the mean* is worse with a CI excluding 0 for HR (+0.026), RBI (+0.055) and R
   (+0.018).
2. **vs baseline, H1.** MAE of the mean for HR is worse (+0.0011, CI [+0.0005, +0.0019]).

MAE of the mean rewards a downward-biased mean on zero-heavy counts: the incumbent's RBI mean of 0.31 sits
closer to the modal 0. Both of the incumbent's biases were large, so this failure is a property of the
preregistered metric choice. That metric is not changed post hoc. An exploratory MAE of the *median* (not
preregistered) is essentially tied; see `holdout_2026/exploratory_mae_of_median_h2.json`.

## Reproduce

```bash
python3 scripts/research/hitter_w2/run_hitter_w2.py fit
python3 scripts/research/hitter_w2/run_hitter_w2.py forecast --n-sims 500 --out <cache.pkl.gz>
python3 scripts/research/hitter_w2/evaluate.py --forecast-cache <cache.pkl.gz> \
    --incumbent data/research/hitter_w2/audit/incumbent_rerun.jsonl.gz --out-dir <dir> --sidecar-dir <dir>
python3 -I <sift>/pure-contract/pure_gate.py validate|compare ...
python3 -I <sift>/pure-contract/pure_gate.py rerun --manifest scripts/research/hitter_w2/rerun_manifest.json ...
```

`evaluate.py` needs numpy. The pure_gate commands are in the PR body. All numbers below are from
`data/research/hitter_w2/holdout_2026/scorecard.json` and the `pure_compare_*` / `pure_gate_rerun_report`
files next to it.

## Population and counts

* **Holdout:** every starting batter in every 2026 regular-season game with a Final feed. That is 2,427 games,
  4,854 team-games and 43,686 player-games. No exclusions: every team-game had 9 starters in slots 1 to 9.
* **H1** (2026-03-25 to 08-11, untouched by the audit): 32,382 player-games, 1,799 games.
* **H2** (08-12 to 09-27): 11,304 player-games, 628 games.
* **Incumbent-matched H2:** 8,883 player-games, 519 games. These are the player-games where the incumbent
  rerun is PROJECTED and matched to a box-score starter. Of the incumbent rows, 232 were MODEL_ERROR and 20
  were not starters.

## Hits and RBI bias, before and after

Mean forecast minus actual, per starter-game.

| Population | Stat | Incumbent | Challenger | Baseline | Δ\|bias\| challenger − incumbent [95% CI] |
|---|---|---:|---:|---:|---|
| H2 matched (8,883) | H | **+0.135** | **+0.004** | −0.001 | −0.131 [−0.136, −0.097] |
| H2 matched | RBI | **−0.151** | **+0.007** | +0.004 | −0.144 [−0.162, −0.106] |
| H2 matched | R | −0.104 | +0.008 | +0.003 | −0.096 [−0.115, −0.056] |
| H2 matched | PA | +0.421 | +0.057 | +0.005 | −0.364 [−0.369, −0.358] |
| H2 matched | HR | −0.037 | +0.001 | +0.002 | −0.036 [−0.039, −0.020] |
| H2 matched | TB | −0.015 | +0.017 | +0.015 | +0.002 [−0.041, +0.038] |
| H1 full (32,382) | H | n/a | +0.022 | +0.011 | n/a |
| H1 full | RBI | n/a | +0.017 | +0.014 | n/a |

## Challenger vs incumbent, H2 matched (8,883 player-games, 519 games; challenger − incumbent, game-clustered 95% CI)

| Stat | CRPS | Brier (threshold) | Log loss | RMSE² (ΔMSE) | MAE of mean |
|---|---|---|---|---|---|
| H (≥1) | **−0.0091** [−0.0120, −0.0062] | −0.0048 [−0.0068, −0.0030] | −0.0107 [−0.0151, −0.0067] | −0.0255 [−0.0334, −0.0178] | −0.0090 [−0.0132, −0.0049] |
| TB (≥2) | **−0.0116** [−0.0153, −0.0077] | −0.0014 [−0.0028, +0.0000] | −0.0032 [−0.0063, −0.0001] | −0.0292 [−0.0496, −0.0061] | −0.0013 [−0.0074, +0.0053] |
| HR (≥1) | **−0.0024** [−0.0033, −0.0015] | −0.0024 [−0.0033, −0.0015] | −0.0421 [−0.0607, −0.0254] | −0.0025 [−0.0036, −0.0015] | **+0.0258** [+0.0241, +0.0278] |
| RBI (≥1) | **−0.0130** [−0.0160, −0.0102] | −0.0108 [−0.0130, −0.0085] | −0.0301 [−0.0364, −0.0241] | −0.0280 [−0.0361, −0.0208] | **+0.0547** [+0.0505, +0.0590] |
| R (≥1) | **−0.0071** [−0.0099, −0.0044] | −0.0061 [−0.0085, −0.0036] | −0.0138 [−0.0191, −0.0085] | −0.0137 [−0.0193, −0.0082] | **+0.0185** [+0.0147, +0.0224] |
| PA (≥5) | −0.0809 [−0.0924, −0.0694] | −0.0395 [−0.0476, −0.0313] | −0.0953 [−0.1155, −0.0749] | −0.1915 [−0.2151, −0.1674] | −0.0871 [−0.1024, −0.0710] |

**Absolute levels.**

| Stat | Model | CRPS | Brier | Reliability ECE |
|---|---|---:|---:|---:|
| H | Incumbent | 0.4645 | 0.2383 | 0.058 |
| H | Challenger | 0.4553 | 0.2335 | 0.006 |
| RBI | Incumbent | 0.3632 | 0.2200 | 0.094 |
| RBI | Challenger | 0.3502 | 0.2093 | 0.005 |

The pure_gate `compare` gives the same picture: quantile CRPS, its own outcome-blind rung Brier, and MAE worse
for HR, RBI and R (`pure_compare_chal_vs_incumbent.json`).

## Challenger vs baseline, H1 primary window (32,382 player-games, 1,799 games; challenger − baseline)

| Stat | CRPS | Brier | Log loss | ΔMSE | MAE of mean |
|---|---|---|---|---|---|
| H | −0.0023 [−0.0031, −0.0014] | −0.0013 [−0.0018, −0.0008] | −0.0029 [−0.0040, −0.0017] | −0.0069 [−0.0091, −0.0044] | −0.0034 [−0.0045, −0.0022] |
| TB | −0.0044 [−0.0060, −0.0028] | −0.0014 [−0.0020, −0.0008] | −0.0030 [−0.0044, −0.0017] | −0.0221 [−0.0311, −0.0129] | +0.0009 [−0.0018, +0.0035] |
| HR | −0.0005 [−0.0008, −0.0002] | −0.0005 [−0.0008, −0.0002] | −0.0029 [−0.0042, −0.0015] | −0.0007 [−0.0010, −0.0003] | **+0.0011** [+0.0005, +0.0019] |
| RBI | **−0.0098** [−0.0107, −0.0088] | −0.0083 [−0.0092, −0.0074] | −0.0177 [−0.0198, −0.0157] | −0.0073 [−0.0095, −0.0051] | −0.0013 [−0.0027, +0.0002] |
| R | −0.0018 [−0.0027, −0.0009] | −0.0014 [−0.0021, −0.0006] | −0.0030 [−0.0047, −0.0014] | −0.0044 [−0.0063, −0.0028] | −0.0011 [−0.0023, +0.0001] |

**H2, challenger vs baseline.** No primary metric is worse with a CI excluding 0. RBI CRPS is −0.0075
[−0.0091, −0.0060], and H CRPS is −0.0017 [−0.0032, −0.0004].

## Decision rule, item by item

| Criterion | Result |
|---|---|
| 1. vs incumbent: no primary metric worse (CI excl. 0) | **FAIL**: MAE of the mean for HR, RBI and R |
| 1. CRPS H and RBI better (CI excl. 0) | pass |
| 1. \|bias\| H and RBI smaller (CI excl. 0) | pass |
| 2. vs baseline H1: no primary metric worse | **FAIL**: HR MAE +0.0011 [+0.0005, +0.0019] |
| 2. RBI CRPS better | pass |
| 3. vs baseline H2: no primary metric worse | pass |
| 4. Runtime non-leakage | pass (`PASS_RUNTIME_NONLEAKAGE`) |

## Calibration and coverage (H1, challenger / baseline)

| Stat | p10–p90 coverage (challenger / baseline) | Randomized PIT in [0.1, 0.9] (challenger / baseline) | Threshold ECE (challenger / baseline) |
|---|---|---|---|
| H | 0.956 / 0.954 | 0.797 / 0.793 | 0.013 / 0.024 |
| RBI | 0.939 / 0.917 | 0.801 / 0.769 | 0.012 / 0.072 |
| R | 0.945 / 0.947 | 0.802 / 0.805 | 0.020 / 0.017 |
| PA | 0.906 / 0.907 | 0.786 / 0.785 | 0.019 / 0.008 |

The incumbent on H2 has these PIT-in-80 values:

| Stat | Incumbent PIT-in-80 |
|---|---:|
| PA | 0.663 |
| RBI | 0.756 |
| HR | 0.771 |

## Joint coherence and correlation (full holdout, 43,686 player-games)

* **Coherence.** Every challenger draw satisfies TB ≥ H ≥ HR: 0 incoherent player draw sets. The
  team-level identities hold by construction and are unit-tested:
  * team RBI ≤ team runs;
  * the sum of starters' R + substitutes' R = team runs.
* **Moments.**

| Moment | Model-implied | Empirical |
|---|---:|---:|
| cov(H, RBI) per player-game | 0.349 | 0.348 |
| cov(H, R) | 0.299 | 0.302 |
| E[R_slot i × RBI_slot i+1] (adjacent teammates) | 0.396 | 0.389 |
| Team runs per team-game | 4.56 | 4.48 |

* The incumbent simulates each hitter in his own independent game, so it has no teammate correlation.

## Preregistered subgroups (bias, H / RBI; see `scorecard.json → subgroups`)

**Full-holdout subgroups, challenger vs baseline.**

| Subgroup | Challenger H | Challenger RBI | Baseline H | Baseline RBI |
|---|---:|---:|---:|---:|
| Slot 1–2 | +0.022 | +0.006 | +0.011 | +0.041 |
| Slot 3–5 | +0.016 | +0.019 | +0.012 | −0.003 |
| Slot 6–9 | +0.022 | +0.021 | +0.010 | +0.014 |
| vs LHP | +0.010 | +0.011 | +0.003 | +0.019 |
| vs RHP | +0.025 | +0.020 | +0.015 | +0.013 |
| Home | +0.008 | +0.004 | +0.001 | +0.003 |
| Away | +0.032 | +0.030 | +0.022 | +0.026 |
| Pregame confirmed lineup captured in `data/slates` | +0.002 | +0.006 | −0.002 | +0.004 |
| Not captured | +0.025 | +0.020 | +0.014 | +0.017 |

The challenger's CRPS is lower than the baseline's in every slot, hand, home/away and capture subgroup for H
and RBI.

**H2 matched, by market listing.**

| Cohort | n | Incumbent H | Incumbent RBI | Challenger H | Challenger RBI |
|---|---:|---:|---:|---:|---:|
| Market-listed | 241 | +0.126 | −0.193 | +0.022 | +0.006 |
| Not listed | 8,642 | +0.135 | −0.150 | +0.003 | +0.007 |

**Residual PA bias.** The challenger's PA is +0.02 to +0.09 too high in every month; 2026-09 is +0.086.

## Runtime non-leakage

`pure_gate rerun` (`holdout_2026/pure_gate_rerun_report.json`) returned **PASS_RUNTIME_NONLEAKAGE**.

**Setup.** Placeholder mode, games of 2026-09-12 to 09-13, 100 simulations, seed 20261011, two randomized
draws. The market inputs were:

* sportsbook game totals from the committed slate captures;
* the committed Kalshi hitter-projection snapshot rows for those dates.

**Model.** The challenger's sidecar was byte-identical in the normal, deleted and randomized arms, and
deterministic.

**Negative control.** The `--control-read-market` variant, which scales non-out rates by the game total, was
deterministic and responded to every mutation:

| Arm | Control result |
|---|---|
| Deleted | Failed |
| Randomized | Output changed |

`market_inputs_restored_unchanged` and `sports_inputs_unchanged` are both true.

## Indirect dependency inspection (challenger)

* **Population:** every box-score starter, with no market gate.
* **Training sample:** all 2024–2025 regular-season PAs, with no market filter.
* **Parameters:** grid log-likelihood on sports outcomes. No price, settlement or market agreement is used.
* **Observations:** the official MLB box score and play-by-play, not settlements.
* **Inputs:** no slate odds, team totals or model-probability blocks are read. A unit test scans `features.py`,
  `model.py`, `sidecars.py` and the engine for market vocabulary.
* **Lineup proxy.** The lineup is the official starting lineup from the post-game feed, used as a stand-in for
  the pregame confirmed lineup. This is a timing caveat, not a market dependency. Bias is smaller where a
  pregame capture exists.

## pure_gate validate

| Sidecar | Rows | Validate result |
|---|---:|---|
| Challenger | 262,116 | pass |
| Baseline | 262,116 | pass |
| Incumbent | 53,298 | pass |
| Challenger, H2 matched | 53,298 | pass |

Every row passes `validate --require-v1`. The only warning is `participation_probability` null: the forecasts
are conditional on starting.

The full sidecars (about 27 MB gzip) are regenerated by `evaluate.py`. 300-row samples are committed.
