# W1-D results: sports-only joint pitcher workload challenger vs champion

**Verdict: `ACCEPTED_CHALLENGER` for 2025–2026 regular-season research collection only.** The 2026 postseason is
**BLOCKED_DATA**, and the market-selected postseason diagnostic is *worse* than the champion (§4). There is no
production, publication, staking or authority change. The champion `lib/research/pitcher_prop_projection.py` and
its params file are unchanged, and the challenger is not wired into any export.

**Commits on `claude/wave1-mlb-pitcher-workload`:**

| Commit | Content |
|---|---|
| `886c4ed` | Source audit: `PITCHER_WORKLOAD_SOURCE_AUDIT.md` |
| `74de33e` | Challenger code, frozen params (sha256 `b36ad628…`) and tests |
| `58bbd6e` | **Preregistration**: `PITCHER_WORKLOAD_PREREG.md`, pushed before held-out scoring |
| This commit | Held-out results and sidecars |

**Reproduce:**

    python3 scripts/research/mlb_pitcher_workload_joint.py fit                       # 2022-24 only -> params.json
    python3 scripts/research/mlb_pitcher_workload_joint.py evaluate --outdir OUT --postseason-diagnostic
    python3 scripts/research/mlb_pitcher_workload_mutation.py --out report.json      # runtime mutation test
    python3 -I <pure-contract>/pure_gate.py rerun --manifest scripts/research/mlb_pitcher_workload_rerun_manifest.json \
        --model-cmd   "python3 scripts/research/mlb_pitcher_workload_joint.py rerun --arm challenger --data-root data --out {out}/rerun.jsonl" \
        --control-cmd "python3 scripts/research/mlb_pitcher_workload_joint.py rerun --arm champion_postseason --data-root data --out {out}/rerun.jsonl"

Artifacts are in `data/research/pitcher_workload_joint/holdout_2025_2026/`:

- the scorecard;
- kit / PURE gate outputs;
- gzipped v0 sidecars for all three arms;
- outcomes;
- v1 samples (300 rows per arm; the full v1 files come from `evaluate`);
- the mutation reports.

## 1. Sample

- **Eligible:** 9,203 starter-games in the 2025 + 2026 regular seasons with ≥ 3 prior starts and observed BF.
- **Excluded:** 178 (1.9%). In these rows the champion's date-ordered history contains a suspended game whose
  final line was completed after the forecast cutoff. Keeping them would score the champion on a leaked line.
  This exclusion is a side finding: the champion's `History.before` orders by `officialDate` only.
- **Scored:** 9,025 starter-games in 4,839 games. That is 2025: 4,497 / 2,417 and 2026: 4,528 / 2,422.
- **Rows:** one per starter-game-stat for outs, BF and K. Ladder rungs are never pooled.
- **Outcomes:** official box scores, plus Statcast-derived lines for 2026-08-27 to 09-27 (788 rows).
- **Cutoff:** `as_of` = scheduled first pitch − 60 min. Every input game must have an observed proxy
  (first pitch + 5 h) ≤ `as_of`.

## 2. Held-out accuracy (identical rows; game-clustered paired bootstrap, 1,000 reps, seed 20261010)

| Stat | Arm | n | Games | MAE | Median abs error | Median signed error | RMSE | Bias | p10–p90 coverage | Threshold Brier | Log loss |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Outs | baseline | 9025 | 4839 | 2.952 | 2.400 | -0.200 | 3.871 | +0.102 | 0.868 | 0.2609 | 2.767 |
| Outs | champion | 9025 | 4839 | 2.806 | 2.164 | -0.159 | 3.703 | +0.129 | 0.860 | 0.2517 | 2.546 |
| Outs | challenger | 9025 | 4839 | 2.748 | 2.063 | -0.262 | 3.628 | +0.077 | 0.872 | 0.2439 | 2.434 |
| BF | baseline | 9025 | 4839 | 2.884 | 2.200 | -0.200 | 3.888 | +0.089 | 0.873 | 0.2593 | 2.776 |
| BF | champion | 9025 | 4839 | 2.747 | 2.103 | -0.224 | 3.745 | +0.120 | 0.959 | 0.2479 | 2.838 |
| BF | challenger | 9025 | 4839 | 2.676 | 2.040 | -0.160 | 3.677 | +0.171 | 0.876 | 0.2462 | 2.633 |
| K | baseline | 9025 | 4839 | 1.901 | 1.600 | +0.200 | 2.387 | +0.045 | 0.831 | 0.2676 | 2.274 |
| K | champion | 9025 | 4839 | 1.800 | 1.538 | +0.241 | 2.252 | +0.097 | 0.901 | 0.2479 | 2.214 |
| K | challenger | 9025 | 4839 | 1.790 | 1.528 | +0.214 | 2.244 | +0.085 | 0.869 | 0.2465 | 2.199 |

| Stat | Comparison | ΔMAE [95% CI] | ΔMSE [95% CI] | ΔBrier [95% CI] | Δlog loss [95% CI] | Δcoverage [95% CI] |
|---|---|---|---|---|---|---|
| Outs | challenger − champion | -0.0580 [-0.0735, -0.0420] | -0.5485 [-0.6795, -0.4218] | -0.0078 [-0.0097, -0.0058] | -0.1127 [-0.1247, -0.1005] | +0.0118 [+0.0079, +0.0159] |
| Outs | challenger − baseline | -0.2037 [-0.2310, -0.1748] | -1.8225 [-2.0579, -1.5845] | -0.0170 [-0.0200, -0.0137] | -0.3331 [-0.3516, -0.3166] | +0.0044 [-0.0006, +0.0098] |
| Outs | champion − baseline | -0.1457 [-0.1687, -0.1209] | -1.2740 [-1.4574, -1.0792] | -0.0092 [-0.0115, -0.0070] | -0.2205 [-0.2345, -0.2064] | -0.0073 [-0.0118, -0.0028] |
| BF | challenger − champion | -0.0708 [-0.0883, -0.0545] | -0.5030 [-0.6737, -0.3455] | -0.0017 [-0.0035, -0.0001] | -0.2048 [-0.2137, -0.1968] | -0.0824 [-0.0881, -0.0767] |
| BF | challenger − baseline | -0.2085 [-0.2420, -0.1765] | -1.5951 [-1.8913, -1.3160] | -0.0131 [-0.0169, -0.0096] | -0.1429 [-0.1570, -0.1300] | +0.0029 [-0.0036, +0.0091] |
| BF | champion − baseline | -0.1377 [-0.1642, -0.1131] | -1.0921 [-1.2991, -0.8683] | -0.0113 [-0.0139, -0.0089] | +0.0619 [+0.0477, +0.0753] | +0.0853 [+0.0802, +0.0914] |
| K | challenger − champion | -0.0099 [-0.0143, -0.0059] | -0.0391 [-0.0612, -0.0186] | -0.0014 [-0.0023, -0.0006] | -0.0155 [-0.0189, -0.0121] | -0.0323 [-0.0362, -0.0290] |
| K | challenger − baseline | -0.1109 [-0.1282, -0.0937] | -0.6634 [-0.7555, -0.5749] | -0.0212 [-0.0246, -0.0180] | -0.0753 [-0.0851, -0.0652] | +0.0377 [+0.0319, +0.0441] |
| K | champion − baseline | -0.1010 [-0.1171, -0.0854] | -0.6243 [-0.7128, -0.5452] | -0.0198 [-0.0229, -0.0169] | -0.0597 [-0.0695, -0.0507] | +0.0700 [+0.0643, +0.0764] |

The threshold Brier uses one rung per starter-game-stat: the strike whose champion/challenger mean probability is
closest to 0.5. This choice is outcome-blind, and the same rung is used for every arm.

`pure_gate.py compare` (CRPS from p10/p50/p90) agrees. Challenger − champion ΔCRPS is:

| Stat | ΔCRPS [95% CI] |
|---|---|
| Outs | −0.050 [−0.061, −0.039] |
| BF | −0.237 [−0.251, −0.223] |
| K | −0.008 [−0.013, −0.004] |

`prop_projection_gate.py compare` gives the same ΔMAE CIs.

## 3. Subgroups (as-of definitions, preregistered)

| Subgroup | Stat | n | Games | MAE base / champ / chal | ΔMAE chal − champ [95% CI] | ΔBrier chal − champ [95% CI] | Bias champ / chal | Coverage champ / chal |
|---|---|---:|---:|---|---|---|---|---|
| season_2025 | Outs | 4497 | 2417 | 2.936 / 2.774 / 2.724 | -0.0497 [-0.0724, -0.0287] | -0.00748 [-0.01016, -0.00450] | +0.069 / +0.047 | 0.864 / 0.876 |
| season_2025 | K | 4497 | 2417 | 1.922 / 1.802 / 1.793 | -0.0094 [-0.0155, -0.0033] | -0.00150 [-0.00261, -0.00045] | +0.068 / +0.053 | 0.898 / 0.870 |
| season_2025 | BF | 4497 | 2417 | 2.818 / 2.667 / 2.616 | -0.0506 [-0.0763, -0.0266] | -0.00079 [-0.00317, +0.00164] | +0.066 / +0.108 | 0.962 / 0.881 |
| season_2026 | Outs | 4528 | 2422 | 2.967 / 2.838 / 2.772 | -0.0663 [-0.0888, -0.0435] | -0.00813 [-0.01101, -0.00532] | +0.190 / +0.107 | 0.857 / 0.869 |
| season_2026 | K | 4528 | 2422 | 1.881 / 1.799 / 1.788 | -0.0104 [-0.0171, -0.0040] | -0.00135 [-0.00261, -0.00008] | +0.127 / +0.117 | 0.904 / 0.868 |
| season_2026 | BF | 4528 | 2422 | 2.951 / 2.826 / 2.735 | -0.0909 [-0.1165, -0.0660] | -0.00262 [-0.00507, -0.00012] | +0.173 / +0.233 | 0.955 / 0.872 |
| short_leash_wpitches_lt80 | Outs | 1521 | 1393 | 3.171 / 3.018 / 2.779 | -0.2389 [-0.3005, -0.1787] | -0.01998 [-0.02697, -0.01276] | +0.359 / +0.113 | 0.844 / 0.861 |
| short_leash_wpitches_lt80 | K | 1521 | 1393 | 1.745 / 1.671 / 1.644 | -0.0266 [-0.0434, -0.0101] | -0.00568 [-0.00915, -0.00196] | +0.077 / +0.197 | 0.908 / 0.888 |
| short_leash_wpitches_lt80 | BF | 1521 | 1393 | 3.559 / 3.426 / 3.148 | -0.2784 [-0.3499, -0.2049] | -0.00537 [-0.01191, +0.00168] | +0.447 / +1.058 | 0.925 / 0.844 |
| mid_leash | Outs | 6592 | 4312 | 2.918 / 2.773 / 2.755 | -0.0184 [-0.0340, -0.0034] | -0.00445 [-0.00653, -0.00246] | +0.123 / +0.115 | 0.862 / 0.873 |
| mid_leash | K | 6592 | 4312 | 1.912 / 1.812 / 1.806 | -0.0059 [-0.0098, -0.0019] | -0.00053 [-0.00125, +0.00016] | +0.121 / +0.099 | 0.901 / 0.867 |
| mid_leash | BF | 6592 | 4312 | 2.797 / 2.653 / 2.612 | -0.0411 [-0.0563, -0.0277] | -0.00152 [-0.00311, +0.00000] | +0.091 / +0.080 | 0.963 / 0.880 |
| long_leash_wpitches_ge95 | Outs | 912 | 862 | 2.829 / 2.692 / 2.649 | -0.0427 [-0.0789, -0.0045] | -0.01181 [-0.01907, -0.00449] | -0.205 / -0.258 | 0.879 / 0.883 |
| long_leash_wpitches_ge95 | K | 912 | 862 | 2.084 / 1.933 / 1.922 | -0.0109 [-0.0264, +0.0038] | -0.00077 [-0.00286, +0.00128] | -0.038 / -0.201 | 0.890 / 0.851 |
| long_leash_wpitches_ge95 | BF | 912 | 862 | 2.390 / 2.289 / 2.350 | +0.0607 [+0.0146, +0.1077] | +0.00307 [-0.00197, +0.00801] | -0.216 / -0.651 | 0.980 / 0.906 |
| low_k_rate_lt0.20 | Outs | 2794 | 2383 | 2.909 / 2.751 / 2.705 | -0.0452 [-0.0716, -0.0171] | -0.00749 [-0.01079, -0.00407] | +0.331 / +0.315 | 0.859 / 0.871 |
| low_k_rate_lt0.20 | K | 2794 | 2383 | 1.736 / 1.643 / 1.637 | -0.0056 [-0.0118, +0.0010] | -0.00201 [-0.00335, -0.00056] | +0.015 / +0.042 | 0.907 / 0.879 |
| low_k_rate_lt0.20 | BF | 2794 | 2383 | 2.911 / 2.765 / 2.712 | -0.0539 [-0.0830, -0.0247] | +0.00062 [-0.00203, +0.00347] | +0.281 / +0.260 | 0.956 / 0.880 |
| mid_k_rate | Outs | 4944 | 3711 | 2.977 / 2.839 / 2.763 | -0.0755 [-0.0973, -0.0540] | -0.00834 [-0.01130, -0.00552] | +0.099 / +0.044 | 0.858 / 0.870 |
| mid_k_rate | K | 4944 | 3711 | 1.927 / 1.838 / 1.826 | -0.0128 [-0.0191, -0.0071] | -0.00131 [-0.00242, -0.00021] | +0.140 / +0.120 | 0.899 / 0.867 |
| mid_k_rate | BF | 4944 | 3711 | 2.893 / 2.774 / 2.700 | -0.0743 [-0.0987, -0.0498] | -0.00148 [-0.00403, +0.00085] | +0.104 / +0.164 | 0.960 / 0.872 |
| high_k_rate_gt0.26 | Outs | 1287 | 1195 | 2.947 / 2.801 / 2.782 | -0.0191 [-0.0558, +0.0177] | -0.00647 [-0.01188, -0.00094] | -0.192 / -0.314 | 0.871 / 0.883 |
| high_k_rate_gt0.26 | K | 1287 | 1195 | 2.162 / 1.996 / 1.988 | -0.0083 [-0.0232, +0.0063] | -0.00057 [-0.00276, +0.00162] | +0.112 / +0.046 | 0.897 / 0.856 |
| high_k_rate_gt0.26 | BF | 1287 | 1195 | 2.795 / 2.600 / 2.505 | -0.0944 [-0.1363, -0.0530] | -0.00764 [-0.01216, -0.00320] | -0.167 / +0.005 | 0.960 / 0.883 |
| first_start_after_return_ge15d | Outs | 361 | 355 | 3.005 / 3.107 / 2.725 | -0.3826 [-0.5335, -0.2261] | -0.04305 [-0.05866, -0.02624] | +1.802 / +0.450 | 0.842 / 0.875 |
| first_start_after_return_ge15d | K | 361 | 355 | 1.680 / 1.627 / 1.560 | -0.0666 [-0.0968, -0.0376] | -0.00698 [-0.01229, -0.00197] | +0.635 / +0.471 | 0.911 / 0.884 |
| first_start_after_return_ge15d | BF | 361 | 355 | 3.594 / 3.853 / 3.386 | -0.4667 [-0.5740, -0.3614] | -0.02225 [-0.03049, -0.01344] | +2.549 / +1.853 | 0.903 / 0.831 |
| season_debut | Outs | 497 | 377 | 3.183 / 3.089 / 2.960 | -0.1292 [-0.2489, -0.0102] | -0.02055 [-0.03390, -0.00693] | +1.541 / +0.403 | 0.827 / 0.857 |
| season_debut | K | 497 | 377 | 1.800 / 1.764 / 1.713 | -0.0514 [-0.0791, -0.0253] | -0.00668 [-0.01103, -0.00235] | +0.378 / +0.203 | 0.909 / 0.879 |
| season_debut | BF | 497 | 377 | 3.683 / 3.633 / 3.332 | -0.3006 [-0.3989, -0.2089] | -0.01832 [-0.02678, -0.01005] | +2.271 / +1.645 | 0.885 / 0.829 |
| outcome_source_statsapi_boxscore_research_cache | Outs | 8237 | 4414 | 2.939 / 2.787 / 2.730 | -0.0566 [-0.0723, -0.0412] | -0.00786 [-0.00976, -0.00583] | +0.081 / +0.029 | 0.865 / 0.875 |
| outcome_source_statsapi_boxscore_research_cache | K | 8237 | 4414 | 1.901 / 1.797 / 1.787 | -0.0098 [-0.0146, -0.0054] | -0.00141 [-0.00227, -0.00056] | +0.089 / +0.075 | 0.901 / 0.870 |
| outcome_source_statsapi_boxscore_research_cache | BF | 8237 | 4414 | 2.866 / 2.720 / 2.647 | -0.0732 [-0.0911, -0.0555] | -0.00207 [-0.00385, -0.00035] | +0.063 / +0.108 | 0.961 / 0.879 |
| outcome_source_statcast_pitch_log | Outs | 788 | 425 | 3.088 / 3.009 / 2.937 | -0.0727 [-0.1355, -0.0158] | -0.00727 [-0.01510, -0.00011] | +0.632 / +0.576 | 0.817 / 0.845 |
| outcome_source_statcast_pitch_log | K | 788 | 425 | 1.911 / 1.833 / 1.823 | -0.0104 [-0.0267, +0.0058] | -0.00152 [-0.00478, +0.00180] | +0.189 / +0.193 | 0.904 / 0.863 |

There is **no preregistered outs or K subgroup with a significant ΔMAE regression**. The largest gains are where
workload context matters most:

| Subgroup | Outs ΔMAE | BF ΔMAE | Champion outs bias | Challenger outs bias |
|---|---:|---:|---:|---:|
| First start after a ≥ 15-day layoff | −0.38 | −0.47 | +1.80 | +0.45 |
| Short leash | −0.24 | −0.28 | | |
| Season debut | −0.13 | −0.30 | | |

**Weakness:** for long-leash pitchers (recency-weighted prior pitches ≥ 95), the challenger's BF is significantly
worse: ΔMAE +0.061 [+0.015, +0.108], bias −0.65. The league non-out curve under-projects BF for deep outings. This
is reported but is not a gate failure, because the gate covers outs and K only.

**Not evaluable:**

- **Lineup confirmed / unconfirmed:** BLOCKED_DATA. There is no as-of lineup status before 2026-07-30, and neither
  arm reads lineups.
- **Manager hook:** no manager identity exists in any committed source. The team-season hook proxy was dropped in
  development, because removing it changed 2024 outs log loss by less than 0.001.

## 4. 2026 postseason: diagnostic only, market-selected, not a gate

- **Population:** 50 probable starters from the StatsAPI schedule snapshots.
- **Labels:** only for starters with a settled Kalshi KS/OUTS market. The labels are the settlement evidence
  values sourced from MLB_STATS_API. That leaves **37 pitcher-games across 22 games**.
- **Challenger inputs:** regular-season history only, because no sports-source postseason line is committed. It
  has no postseason regime term and its bullpen feature is 0.
- **`champion_regular_history`:** the champion with regular-season history plus the frozen settlement-fitted
  shift of −1.21 outs.
- **`champion_postseason_path`:** the production path, which also adds settlement-derived postseason starts to
  history.

| Postseason diagnostic | n | Games | Arm | MAE | Bias | RMSE | Log loss | p10–p90 coverage |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| Outs | 37 | 22 | challenger | 3.605 | +1.449 | 4.495 | 2.980 | 0.838 |
| Outs | 37 | 22 | champion_regular_history | 3.391 | +1.330 | 4.318 | 2.823 | 0.838 |
| Outs | 37 | 22 | champion_postseason_path | 3.407 | +1.335 | 4.317 | 2.888 | 0.838 |
| K | 37 | 22 | challenger | 2.004 | +0.396 | 2.428 | 2.304 | 0.811 |
| K | 37 | 22 | champion_regular_history | 1.868 | +0.186 | 2.347 | 2.273 | 0.865 |
| K | 37 | 22 | champion_postseason_path | 1.857 | +0.195 | 2.339 | 2.271 | 0.865 |

Paired game-clustered ΔMAE, challenger − champion_postseason_path:

| Stat | ΔMAE [95% CI] |
|---|---|
| Outs | +0.198 [+0.001, +0.422] |
| K | +0.147 [+0.045, +0.263] |

**In the postseason the sports-only challenger is worse than the champion.** All arms over-project postseason
outs by about 1.3–1.4. Part of the champion's postseason advantage comes from its settlement-derived shift and
history, which are exactly the market-conditional observations a PURE model may not use. This is the measured
price of independence until sports-source postseason box scores are archived.

## 5. Runtime non-leakage

**`scripts/research/mlb_pitcher_workload_mutation.py`: `PASS_RUNTIME_NONLEAKAGE`.** It actually re-runs both
models on a symlink farm of `data/` in four arms: normal, normal repeat, market removed, and market randomized.

- In the removed and randomized arms, every Kalshi/odds/CLV artifact plus the settlements and observations is
  removed, or randomized from 2026-09-28 onward.
- The odds subtrees embedded in the postseason schedule snapshots are dropped or randomized too.
- **Challenger:** 2,187 rows, sha256 `9c36b00c433c…`, bit-identical in all 4 arms.
- **Negative control** (the champion's settlement-reading postseason path, 132 rows): deterministic on normal and
  normal repeat (`d0e7ba64…`). It **changed** with market files removed (`1e268d98…`) and with them randomized
  (`bb811308…`).

**`pure_gate.py rerun` (in-place on `data/edgelab/settlements`, format-preserving randomizer):
`PASS_RUNTIME_NONLEAKAGE`.** Every check passed:

| Check | Result |
|---|---|
| Model deterministic | true |
| Model identical under deletion and randomization | true |
| Control deterministic | true |
| Control responded to every mutation | true (output changed) |
| Randomization effective | true |
| Market inputs restored byte-identical | true |
| Sports inputs unchanged | true |

**Limitations of this proof:** the rerun covers the declared market inputs. The training population is all
regular-season starts with no market selection. The params were fitted only on sports data from 2022–2024.

## 6. Development history (negative results kept)

All of these were tried on 2022–23 → 2024 only:

- **BF-first chain** with outs | BF beta-binomial: rejected. It smears the inning-boundary lumps (2024 outs log
  loss 2.677 vs 2.524).
- **Poisson BF | outs:** rejected. Non-outs per start *fall* as outs rise (7.7 at 9–14 outs vs 4.9 at 21), so a
  constant rate over-disperses and biases BF.
- **Recentred residual tables:** rejected (outs log loss 2.546–2.662).
- **Team-hook proxy, early-season flag, few-history flag:** dropped (< 0.001 log-loss effect).
- **Kept:** pitch-count leash, rest / return / debut, bullpen load and opponent on-base. Each worsened 2024 outs
  MAE and log loss when removed.
