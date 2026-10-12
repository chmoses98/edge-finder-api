# MLB hitter V2 challenger: held-out results

**Status: research only.** No production hitter output, scheduler, app export, publication, settlement, staking,
authority or bet routing is changed. Nothing is promoted.

* **Preregistration:** `docs/research/HITTER_V2_PREREG.md`, commit `77c4ab1`, pushed before any 2026 V2 forecast
  was produced. Code and frozen params: commit `b42613a`.
* **Frozen params:** V2 `data/research/hitter_v2/params_v2.json`
  (`sha256:b016ea53…f934`, fitted on 2024–2025); W2 `data/research/hitter_w2/params_v1.json` (`sha256:e34c413e…73e5`).
  Both hashes are recorded inside the forecast caches.
* **All numbers** come from `data/research/hitter_v2/holdout_2026/{scorecard,verdict,diagnostics}.json`
  (1,000 game-clustered bootstrap resamples, seed 20261012) and `pure_gate_rerun_report.json`.

## Verdict: `INCONCLUSIVE` under the preregistered rule (not accepted; nothing promoted)

* **No criterion fails.** C1 (vs champion), C3 (vs baseline), C4 (MAE guardrail), C5 (calibration), C6 (runtime
  non-leakage) and C7 (point-in-time) all pass.
* **One required improvement is not shown:** C2, the composite CRPS vs the frozen W2 challenger on the primary
  point-in-time population PIT, is −0.0009 [−0.0063, +0.0053]. The CI includes 0. V2 is clearly better than W2
  on **PA**, but on H, TB, HR, RBI, R and HRR the two are tied on PIT.
* **#285's rule applied verbatim (reported, not the decision): REJECTED**, for the same reason W2 was. MAE of the
  *mean* is worse vs the champion for HR (+0.0256 [+0.0237, +0.0274]), RBI (+0.0495 [+0.0452, +0.0538]) and R
  (+0.0142 [+0.0107, +0.0178]). The champion's means are biased low (RBI −0.151) on zero-heavy counts, and that
  helps MAE of the mean. On MAE of the *median* (C4), V2 is never worse.

**Plain reading.**

* V2 beats the production champion on every statistic's distribution quality, with large margins.
* It beats the simple baseline on every statistic except HR, where the two are tied.
* It is calibrated on every ladder rung.
* Its PA-count fixes work (PA CRPS −0.010 vs W2, PA bias +0.057 → +0.001 on PIT_matched).
* On PIT, those PA gains do not produce a measurable distribution-quality gain over W2 for the counting stats.
  On the larger secondary population OFF_H1, V2 is better than W2 on composite CRPS (−0.0038 [−0.0067, −0.0007]),
  on H, and on HRR.

## Populations and counts

| Population | Definition | Team-games | Player-games | Games |
|---|---|---:|---:|---:|
| **PIT** | Pregame confirmed lineup + probable starter from the latest slate capture before first pitch (2026-08-16..09-27) | 1,014 scored (24 sides with only a proxy lineup are not scored) | 9,115 | 519 |
| **PIT_matched** | PIT rows where the champion rerun is PROJECTED (232 champion MODEL_ERROR rows drop out) | — | 8,883 | 519 |
| **OFF_H1** | Official lineup proxy + feed probable starter, 2026-03-25..08-11 | 3,598 | 32,382 | 1,799 |

* **Exclusions.** 11 captured batters did not start and are excluded for every arm. No team-game was excluded
  for an incomplete lineup.
* **Probable vs actual starter.** The probable was not the actual starter in 3 of 1,014 PIT team-games and 7 of
  3,598 OFF_H1 team-games. The forecast uses the probable.
* **Champion rerun.** 9,135 hitter runs, of which 8,903 are PROJECTED. All PA/H/TB/HR/RBI/R/BB/K pmfs are
  identical to the committed #285 rerun (0 mismatches, 0 missing). HRR comes from the same draws
  (`data/research/hitter_v2/champion_rerun_hrr.jsonl.gz`).

## Primary metric: CRPS by statistic (lower is better; mean bias in parentheses)

### PIT_matched: all four arms (8,883 player-games, 519 games)

| Stat | Actual mean | V2 | W2 (frozen) | Champion | Baseline | V2 − champion [95% CI] | V2 − W2 [95% CI] | V2 − baseline [95% CI] |
|---|---:|---|---|---|---|---|---|---|
| PA | 4.016 | **0.3687** (+0.001) | 0.3785 (+0.057) | 0.4595 (+0.421) | 0.3786 (+0.004) | −0.0908 [−0.1025, −0.0777] | −0.0099 [−0.0124, −0.0069] | −0.0099 [−0.0133, −0.0064] |
| H | 0.889 | 0.4547 (−0.015) | 0.4548 (+0.004) | 0.4645 (+0.135) | 0.4563 (−0.001) | −0.0098 [−0.0127, −0.0065] | −0.0001 [−0.0011, +0.0008] | −0.0016 [−0.0032, −0.0001] |
| TB | 1.445 | 0.8645 (−0.008) | 0.8644 (+0.019) | 0.8765 (−0.015) | 0.8676 (+0.014) | −0.0120 [−0.0157, −0.0081] | +0.0001 [−0.0015, +0.0018] | −0.0031 [−0.0055, −0.0007] |
| HR | 0.122 | 0.1075 (+0.001) | 0.1076 (+0.001) | 0.1098 (−0.037) | 0.1077 (+0.002) | −0.0023 [−0.0032, −0.0015] | −0.0001 [−0.0004, +0.0002] | −0.0002 [−0.0006, +0.0002] |
| RBI | 0.457 | 0.3496 (−0.005) | 0.3495 (+0.008) | 0.3632 (−0.151) | 0.3560 (+0.004) | −0.0135 [−0.0165, −0.0106] | +0.0001 [−0.0007, +0.0009] | −0.0064 [−0.0083, −0.0044] |
| R | 0.478 | 0.3205 (−0.006) | 0.3207 (+0.009) | 0.3288 (−0.104) | 0.3222 (+0.003) | −0.0083 [−0.0108, −0.0056] | −0.0002 [−0.0010, +0.0008] | −0.0017 [−0.0032, −0.0000] |
| BB | 0.349 | 0.2529 (+0.004) | n/a | 0.2656 (+0.112) | 0.2542 (+0.007) | −0.0128 [−0.0151, −0.0102] | n/a | −0.0014 [−0.0023, −0.0004] |
| K | 0.870 | 0.4302 (+0.016) | n/a | 0.4709 (+0.260) | 0.4344 (+0.005) | −0.0407 [−0.0456, −0.0359] | n/a | −0.0041 [−0.0057, −0.0027] |
| H+R+RBI | 1.823 | 0.9944 (−0.026) | 0.9946 (+0.021) | 1.0080 (−0.120) | 1.0325 (+0.006) | −0.0136 [−0.0186, −0.0081] | −0.0002 [−0.0023, +0.0022] | −0.0381 [−0.0436, −0.0315] |

### PIT: all captured starters (9,115 player-games, 519 games; no champion)

| Stat | V2 | W2 | Baseline | V2 − W2 [95% CI] | V2 − baseline [95% CI] |
|---|---|---|---|---|---|
| PA | 0.3690 (+0.002) | 0.3793 (+0.060) | 0.3791 (+0.007) | −0.0103 [−0.0129, −0.0075] | −0.0102 [−0.0136, −0.0067] |
| H | 0.4556 | 0.4559 | 0.4574 | −0.0002 [−0.0012, +0.0007] | −0.0017 [−0.0032, −0.0002] |
| TB | 0.8697 | 0.8698 | 0.8727 | −0.0001 [−0.0017, +0.0016] | −0.0030 [−0.0054, −0.0006] |
| HR | 0.1089 | 0.1090 | 0.1090 | −0.0001 [−0.0004, +0.0002] | −0.0001 [−0.0005, +0.0002] |
| RBI | 0.3515 | 0.3514 | 0.3581 | +0.0001 [−0.0006, +0.0009] | −0.0066 [−0.0086, −0.0046] |
| R | 0.3217 | 0.3220 | 0.3234 | −0.0002 [−0.0011, +0.0007] | −0.0016 [−0.0032, −0.0001] |
| BB | 0.2541 | n/a | 0.2554 | n/a | −0.0013 [−0.0023, −0.0004] |
| K | 0.4309 | n/a | 0.4351 | n/a | −0.0042 [−0.0057, −0.0027] |
| H+R+RBI | 0.9982 | 0.9987 | 1.0368 | −0.0004 [−0.0026, +0.0019] | −0.0386 [−0.0442, −0.0322] |

### OFF_H1: secondary consistency population (32,382 player-games, 1,799 games)

| Stat | V2 | W2 | Baseline | V2 − W2 [95% CI] | V2 − baseline [95% CI] |
|---|---|---|---|---|---|
| PA | 0.3605 (+0.026) | 0.3701 (+0.059) | 0.3704 (+0.007) | −0.0095 [−0.0108, −0.0084] | −0.0099 [−0.0116, −0.0083] |
| H | 0.4529 (+0.005) | 0.4535 (+0.021) | 0.4557 (+0.011) | −0.0006 [−0.0012, −0.0001] | −0.0028 [−0.0037, −0.0019] |
| TB | 0.8698 | 0.8706 | 0.8749 | −0.0008 [−0.0017, +0.0001] | −0.0051 [−0.0067, −0.0034] |
| HR | 0.1086 | 0.1086 | 0.1091 | −0.0000 [−0.0001, +0.0002] | −0.0005 [−0.0008, −0.0002] |
| RBI | 0.3495 (+0.006) | 0.3499 (+0.016) | 0.3595 (+0.014) | −0.0004 [−0.0008, +0.0000] | −0.0100 [−0.0111, −0.0090] |
| R | 0.3181 | 0.3184 | 0.3201 | −0.0003 [−0.0007, +0.0002] | −0.0020 [−0.0029, −0.0011] |
| BB | 0.2573 (+0.000) | n/a | 0.2596 (+0.007) | n/a | −0.0023 [−0.0029, −0.0017] |
| K | 0.4382 (+0.014) | n/a | 0.4445 (−0.003) | n/a | −0.0063 [−0.0074, −0.0053] |
| H+R+RBI | 0.9953 (+0.018) | 0.9969 (+0.056) | 1.0389 (+0.035) | −0.0017 [−0.0028, −0.0005] | −0.0436 [−0.0467, −0.0404] |

### Composite CRPS (per player-game sum)

| Comparison | Statistics | n | Δ [95% CI] | Criterion |
|---|---|---:|---|---|
| V2 − W2, PIT | H, TB, HR, RBI, R, HRR | 9,115 | −0.0009 [−0.0063, +0.0053] | C2 "better": **INCONCLUSIVE** |
| V2 − W2, OFF_H1 | same | 32,382 | −0.0038 [−0.0067, −0.0007] | C2 "not worse": pass (better) |
| V2 − baseline, PIT | all 8 decision stats | 9,115 | −0.0572 [−0.0689, −0.0450] | C3: pass |
| V2 − baseline, OFF_H1 | all 8 | 32,382 | −0.0727 [−0.0792, −0.0660] | C3: pass |
| V2 − champion, PIT_matched | all 8 | 8,883 | −0.1130 [−0.1269, −0.0980] | reported |
| W2 − champion, PIT_matched | W2's 6 | 8,883 | −0.0592 [−0.0724, −0.0449] | reported |

## Guardrails

**C4: MAE of the predictive median.** Rule: the upper CI bound must be ≤ 2% of the comparator's MAE. All 24
checks pass.

| Comparison | Statistic | ΔMAE(median) [95% CI] | Margin |
|---|---|---|---:|
| vs champion (PIT_matched) | H | −0.0014 [−0.0064, +0.0044] | 0.0133 |
| | RBI | −0.0003 [−0.0008, 0.0000] | 0.0091 |
| | R | −0.0010 [−0.0056, +0.0040] | 0.0096 |
| | HR | 0 [0, 0] | 0.0024 |
| | BB | −0.0195 | — |
| | K | −0.0383 | — |
| | HRR | −0.0191 | — |
| vs baseline (PIT and OFF_H1) | worst upper bound | +0.0049 (TB, PIT) | 0.0245 |

For the HR row, both arms' median is 0 for every player.

**MAE of the mean (reported only).** V2 vs champion:

| Statistic | V2 | Champion |
|---|---:|---:|
| H | 0.686 | 0.695 |
| TB | 1.321 | 1.330 |
| HR | 0.213 | 0.187 |
| RBI | 0.624 | 0.575 |
| R | 0.576 | 0.561 |

**C5: calibration of V2 on PIT and OFF_H1, every decision statistic.** All 16 checks pass. Worst values:

| Measure | Worst value | Where | Tolerance |
|---|---:|---|---:|
| PIT-in-80 | 0.794 | K, OFF_H1 | 0.80 ± 0.03 |
| Max-rung ECE | 0.0129 | K, OFF_H1 | ≤ 0.025 |
| Max-rung \|CITL\| | 0.0124 | K, OFF_H1 | ≤ 0.02 |

On PIT_matched the champion's max-rung ECE values are:

| Statistic | Champion max-rung ECE |
|---|---:|
| H | 0.058 |
| RBI | 0.094 |
| BB | 0.081 |
| K | 0.113 |

**C6: `pure_gate rerun` returned `PASS_RUNTIME_NONLEAKAGE`.**

* **Setup.** PIT games of 2026-09-12 and 09-13, 100 simulations. The market inputs were the #285 slate
  sportsbook totals and the Kalshi hitter snapshot rows.
* **Model.** V2's output was byte-identical across the normal, deleted and randomized ×2 arms, and
  deterministic.
* **Negative control.** The `--control-read-market` variant failed when the input was deleted and changed when it
  was randomized.
* `sports_inputs_unchanged` and `market_inputs_restored_unchanged` are both true.

**C7: point-in-time.**

* 0 of 1,014 scored PIT team-games have a capture at or after first pitch.
* The as-of, W2-compatibility and extractor key-access tests pass.

## Diagnostics per structural error (PIT_matched unless stated)

**D1 PA by lineup slot × home/away.** Mean forecast minus actual:

| Arm | Range across the 18 slot×side cells |
|---|---|
| Champion | +0.24 to +0.66 |
| W2 | −0.02 to +0.13 |
| V2 | −0.06 to +0.07 |
| Baseline | −0.07 to +0.07 |

Example cells:

| Cell | Actual P(PA ≥ 5) | V2 | W2 | Champion |
|---|---:|---:|---:|---:|
| Slot 1, away | 0.556 | 0.583 | 0.631 | 0.779 |
| Slot 9, home | 0.038 | 0.031 | 0.040 | 0.154 |

OFF_H1: V2's cells are all between +0.002 and +0.054. W2's are between +0.019 and +0.115.

**D2 home vs away (home-9th truncation).** Mean starter PA:

| Side | Actual | V2 | W2 | Champion |
|---|---:|---:|---:|---:|
| Away | 4.096 | 4.099 | 4.169 | 4.436 |
| Home | 3.937 | 3.936 | 3.980 | 4.438 |

The champion has no home/away difference at all.

**D3 substitution, P(starter replaced).**

| Slot | V2 forecast | Actual |
|---|---:|---:|
| 1 | 0.094 | 0.109 |
| 4 | 0.083 | 0.082 |
| 7 | 0.154 | 0.186 |
| 9 | 0.226 | 0.224 |
| Catchers | 0.157 | 0.171 |

V2 is still slightly low in slots 6 and 7. In OFF_H1, V2 is 0.01 to 0.02 low in most slots.

**D4 walk rate, BB per PA.**

| Population | Actual | V2 | Baseline | Champion |
|---|---:|---:|---:|---:|
| PIT_matched | 0.0868 | 0.0879 | 0.0884 | 0.1038 |
| OFF_H1 | 0.0888 | 0.0883 | 0.0904 | — |

**D5 platoon, vs the probable starter.**

| Matchup | Stat | V2 bias | Other arm |
|---|---|---:|---|
| Opposite hand | H | +0.000 | W2 +0.027 |
| Same hand | H | −0.044 | W2 −0.031 |
| Same hand | K | −0.001 | Baseline −0.035 |

* V2's CRPS is the lowest of all arms in every matchup for BB and K.
* For H, V2's CRPS is the lowest for opposite-hand matchups. For same-hand and switch hitters, V2 and W2 are
  within 0.002.
* In the final fit the individual batter split was shrunk away entirely (m_bsplit = ∞), so the platoon fix is the
  pitcher's own split plus the league factor.

**D6 bullpen quality, H bias by tercile of the opposing bullpen's as-of OBP allowed.**

| Population | Tercile | V2 | W2 | Champion |
|---|---|---:|---:|---:|
| PIT_matched | Best pens | +0.004 | +0.017 | +0.185 |
| PIT_matched | Worst pens | −0.053 | −0.025 | +0.070 |
| OFF_H1 | Best pens | +0.006 | +0.017 | — |
| OFF_H1 | Worst pens | −0.010 | +0.010 | — |

V2 now under-projects against the worst bullpens on PIT. The 30-day half-life may shrink bad pens toward the
league too fast late in the season.

**D7 R, RBI and HRR by slot group.**

* V2's R and RBI biases are within ±0.02 in every slot group, on both PIT_matched and OFF_H1.
* HRR is −0.065 for slots 3–5 on PIT_matched (W2 −0.019); on OFF_H1 it is +0.027 (W2 +0.067).

**Coherence.**

* 0 incoherent draws in 41,497 player draw sets.
* 0 violations of the team-run identity (starters' R + substitutes' R = team runs) on any simulated draw.

## What the result does and does not show

* **Shown.** These hold on the point-in-time population, with game-clustered CIs:
  * The PA-count fixes (state-dependent substitution hazard with batter propensity and September rosters,
    inning-ending runner outs) remove W2's residual PA over-projection.
  * Calibration and bullpen handedness/recency bring BB and K to distribution quality clearly better than the
    baseline and far better than the champion, while staying calibrated on every ladder rung.
* **Not shown.** That V2 is a better *counting-stat* forecaster than W2 on PIT. The per-stat differences there are
  ≤ 0.0004 CRPS with CIs spanning 0. OFF_H1 shows small V2 gains (composite −0.0038), but OFF_H1 uses a post-game
  lineup proxy, and the rule requires the PIT improvement.
* **Not changed post hoc.** The decision metric, the MAE definition, the margins and the populations are as
  preregistered.

## Reproduce

```bash
python3 scripts/research/hitter_v2/extract_pregame_lineups.py            # sports keys only
python3 scripts/research/hitter_v2/run_v2.py dev                         # training-data design loop
python3 scripts/research/hitter_v2/run_v2.py fit                         # -> params_v2.json (2024-2025)
python3 scripts/research/hitter_v2/champion_hrr_rerun.py --out <champ.jsonl.gz>
python3 scripts/research/hitter_v2/run_v2.py forecast --population PIT --out <pit.pkl.gz>
python3 scripts/research/hitter_v2/run_v2.py forecast --population OFF_H1 --out <off.pkl.gz>
python3 scripts/research/hitter_v2/evaluate_v2.py --pit-cache <pit.pkl.gz> --off-cache <off.pkl.gz> \
    --champion data/research/hitter_v2/champion_rerun_hrr.jsonl.gz --out-dir data/research/hitter_v2/holdout_2026
```

The forecast caches (≈ 64 MB) are not committed; they regenerate deterministically. `pure_gate` command:
`scripts/research/hitter_v2/rerun_manifest.json` with `run_v2.py forecast --population PIT --start 2026-09-12
--end 2026-09-13 --n-sims 100 --arms v2 …` (control adds `--control-read-market`).

## Next steps (not done here)

* **If anything, shadow-collect V2 as research only.** The verdict authorizes nothing, and promotion would need
  a new preregistration.
* **Larger point-in-time sample.** The PIT window is 519 games. A prospective forward-collected window
  (2027 regular season), or 2026 postseason if backfilled, would resolve whether the V2 vs W2 composite gap is
  real.
* **Remaining structural errors:**
  * the sim's run generation given calibrated events is ~4% high for the away team (training-data finding);
  * worst-bullpen under-projection;
  * no runner speed;
  * no intentional-walk state dependence.
