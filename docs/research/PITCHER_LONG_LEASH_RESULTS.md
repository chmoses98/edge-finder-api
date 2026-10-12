# Leash-conditioned BF | outs variant: preregistered held-out result

**Verdict: REJECTED** under the preregistered strict rule (`PITCHER_LONG_LEASH_PREREG.md`, commit `3ddd1a99`,
pushed before scoring). The single failing check is rule 4: mid-leash K ΔMAE vs the frozen challenger is
+0.0011 [+0.0001, +0.0022].

`mlb_pitcher_workload_joint_v1` stays the frozen challenger, the champion is unchanged, and nothing is promoted.
Research only.

**Reproduce:** `python3 scripts/research/mlb_pitcher_long_leash.py evaluate`, which writes
`data/research/pitcher_long_leash/holdout_2025_2026.json`.

**Population.** The 2025 + 2026 regular seasons on #283's population, with the 178 exclusions: **9,025 starts in
4,839 games**. Before scoring, the frozen challenger reproduced the #283 sidecar on 27,075 / 27,075 rows.

**Frozen hashes:**

| arm | hash |
|---|---|
| variant | `sha256:1f1189a2…3d73` |
| frozen challenger | `sha256:e740efbd…4ad1` (as in #283) |
| champion | `sha256:5741820d…46d9` |

**Bootstrap.** Paired and game-clustered, 1,000 reps, seed 20261012.

**Reused-holdout caveat.** The 2025–2026 data was scored before (#283), and the weakness was found there.
The variant's form and parameters come from 2022–2024 only.

## Overall (variant − arm, 95% CI)

| stat | variant MAE | Δ vs frozen challenger: MAE | Brier | log loss | Δ vs champion: MAE | log loss | Δ vs baseline: MAE |
|---|---|---|---|---|---|---|---|
| BF | 2.6455 | **−0.0303 [−0.0381, −0.0230]** | −0.0024 [−0.0032, −0.0016] | −0.0146 [−0.0172, −0.0120] | −0.1012 [−0.1178, −0.0838] | −0.2193 [−0.2289, −0.2095] | −0.2389 [−0.2673, −0.2100] |
| K | 1.7890 | −0.0015 [−0.0034, +0.0002] | −0.0002 [−0.0006, +0.0002] | −0.0011 [−0.0020, −0.0001] | −0.0114 [−0.0155, −0.0076] | −0.0166 [−0.0202, −0.0130] | −0.1124 [−0.1297, −0.0946] |
| outs | 2.7480 | 0 (identical by construction) | 0 | 0 | −0.0580 [−0.0736, −0.0430] | −0.1127 [−0.1246, −0.1002] | −0.2037 [−0.2319, −0.1759] |

Variant p10–p90 coverage is outs 0.872, BF 0.861 and K 0.865.

## By pitch-leash tier

| tier (starts / games) | stat | bias: variant | frozen challenger | champion | ΔMAE variant − frozen challenger | ΔMAE variant − champion |
|---|---|---|---|---|---|---|
| long ≥ 95 (912 / 862) | BF | **−0.255 [−0.434, −0.070]** | −0.651 [−0.827, −0.467] | −0.216 [−0.390, −0.024] | **−0.0770 [−0.1014, −0.0522]** | −0.0163 [−0.0599, +0.0259] |
| | K | −0.097 [−0.254, +0.066] | −0.201 [−0.357, −0.038] | −0.038 [−0.198, +0.124] | −0.0024 [−0.0088, +0.0047] | −0.0133 [−0.0266, +0.0002] |
| | outs | −0.258 [−0.474, −0.035] | −0.258 (identical) | −0.205 [−0.424, +0.017] | 0 | −0.0427 [−0.0834, −0.0014] |
| mid 80–95 (6,592 / 4,312) | BF | +0.140 [+0.052, +0.228] | +0.080 [−0.008, +0.168] | +0.091 | −0.0086 [−0.0131, −0.0042] | −0.0498 [−0.0644, −0.0348] |
| | K | +0.117 [+0.063, +0.174] | +0.099 [+0.045, +0.156] | +0.121 | **+0.0011 [+0.0001, +0.0022]** (fails rule 4) | −0.0048 [−0.0085, −0.0011] |
| short < 80 (1,521 / 1,393) | BF | +0.407 [+0.191, +0.623] | +1.058 [+0.841, +1.285] | +0.447 | −0.0963 [−0.1310, −0.0571] | −0.3747 [−0.4446, −0.3049] |
| | K | +0.060 [−0.046, +0.173] | +0.197 [+0.091, +0.309] | +0.078 | −0.0126 [−0.0208, −0.0047] | −0.0392 [−0.0573, −0.0228] |

**By innings depth (reported only).**

* 6+ IP starters (1,049 / 991): BF bias −0.695 → −0.449 and BF ΔMAE vs the frozen challenger −0.0575
  [−0.0743, −0.0408]. Against the champion, BF ΔMAE is +0.0226 [−0.0218, +0.0681] and outs ΔMAE is +0.0437
  [+0.0034, +0.0843]. The outs gap is the frozen challenger's own: outs are unchanged, and the kernel shrinkage
  (cause 3) is uncorrected.
* Under 5 IP: BF bias +0.894 → +0.544.

## Decision rule (mechanical; `decision` in the JSON)

| check | result |
|---|---|
| 1. Long-leash BF ΔMAE vs frozen challenger: CI < 0 | pass, −0.0770 [−0.1014, −0.0522] |
| 2. Long-leash BF \|bias\| reduced | pass, 0.651 → 0.255 |
| 3. No overall regression vs frozen challenger | pass |
| 4. No pitch-leash-tier ΔMAE regression vs frozen challenger | **FAIL: mid-leash K +0.0011 [+0.0001, +0.0022]** |
| 5. #283 acceptance vs champion | pass. Outs and K better; no regression; no outs/K tier regression |
| 6. Coverage in [0.75, 0.92] | pass |
| 7. Beats baseline MAE | pass |

## Reading (not part of the verdict)

**The diagnosed mechanism holds out of sample.** Conditioning non-outs on the leash does three things:

* it removes about 60% of the long-leash BF bias, and the long-leash BF disadvantage vs the champion (#283: +0.061)
  becomes −0.016 [−0.060, +0.026];
* it cuts the short-leash BF bias from +1.06 to +0.41;
* it improves BF MAE, Brier and log loss overall.

**Why it was rejected.** The failure is small and sits in K for the mid tier. The variant raises E[BF] slightly
for mid-leash starters (bias +0.08 → +0.14). Their K | BF rate was already high (K given the actual BF +0.11 in
the diagnosis), so K gets a little worse: 0.06% of the MAE.

**Next steps.** A follow-up would need its own preregistration and fresh data, which means prospective collection.
It would pair this BF | outs term with two things:

* a K | BF level correction;
* the cause-3 kernel fix, which addresses the remaining long-leash outs bias of −0.26.

The current held-out seasons can't serve as an unseen test for it.
