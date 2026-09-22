# Full-universe calibration, completeness-qualified and independence-aware

**Phases K/L/M.** This supersedes the family-level findings in
[#233](https://github.com/chmoses98/edge-finder-api/pull/233). The aggregate
result survives. **Every family-level finding does not.**

## Two corrections

### 1. Source completeness (K/L)

#233 scored every settled market carrying an executable pre-start ask, with no
regard for whether the capture that produced that ask had retrieved everything
it asked for. It had not, 9 times in 21 days — and the missingness was
**systematic**, not random: the old sequential fetch meant a rate limit
truncated whatever series came last, so the same hitter prop families absorbed
the loss every time.

Every row now carries the completeness class of the capture its defining quote
came from:

| source capture class | rows |
|---|---|
| `UNKNOWN_LEGACY` | 24,669 |
| `PARTIAL` | **6,915 (21.9%)** |
| `COMPLETE` | **0** |

**There is no COMPLETE arm, and there cannot yet be one.** Zero historical
captures can prove completeness — the pre-v4 path discarded live cursors at the
page cap with no failure recorded, so a clean legacy snapshot is
`UNKNOWN_LEGACY`, never `COMPLETE` (see
[EDGELAB_ARCHIVE_COMPLETENESS.md](EDGELAB_ARCHIVE_COMPLETENESS.md)).
Manufacturing a COMPLETE arm out of legacy data would be exactly the laundering
this work exists to prevent.

The headline arm therefore excludes rows sourced from a **known-PARTIAL**
capture. That is the strongest qualification the evidence currently supports.

### 2. Independence (M)

#233 printed Wilson intervals over 31,584 rows and flagged its own problem:
**145.5 rows per independent game**. Every hitter prop, team total and game
total on one game shares that game's outcome, so an interval assuming 31,584
independent trials is far too narrow.

Uncertainty here is a **clustered bootstrap**: resample whole *games* with
replacement (2,000 iterations, fixed seed), recompute, take percentiles. A game
is the unit that is actually independent. Both intervals are reported so the
difference is visible rather than asserted.

### 3. Multiplicity

Eleven families are tested at once. At 95% you expect roughly one interval to
exclude zero **by chance alone**, so "the CI excludes zero" is not a finding
until multiplicity is accounted for. Benjamini–Hochberg FDR control at α = 0.05
is applied across families.

## Aggregate result: unchanged and now properly bounded

| arm | n | games | gap | clustered 95% CI |
|---|---|---|---|---|
| **qualified** (excl. PARTIAL) | 24,669 | 178 | **+0.0014** | [−0.0151, +0.0176] |
| unqualified (#233's population) | 31,584 | 217 | −0.0004 | [−0.0142, +0.0140] |
| PARTIAL-sourced only | 6,915 | 79 | −0.0070 | [−0.0231, +0.0094] |

Kalshi's executable pre-start price is **well calibrated in aggregate**, and the
gap is not significant under clustering in any arm. Source truncation did **not**
materially bias the aggregate result — which is worth stating plainly, because
it was the thing most likely to have.

Note the clustered interval is roughly **3× wider** than the naive Wilson
interval on the same data. That ratio is the whole of Phase M.

## Family-level: nothing survives

| family | n | games | gap | p | clustered 95% CI | label |
|---|---|---|---|---|---|---|
| `hitter_stolen_bases` | 1,228 | 113 | −0.0202 | 0.021 | [−0.0361, −0.0043] | EXPLORATORY |
| `inning_result` | 662 | 85 | −0.0096 | 0.033 | [−0.0186, −0.0009] | EXPLORATORY |
| `game_total` | 826 | 85 | +0.0518 | 0.094 | [−0.0103, +0.1123] | NO_EVIDENCE |
| `inning_total` | 518 | 85 | +0.0514 | 0.144 | [−0.0153, +0.1175] | NO_EVIDENCE |
| `team_total` | 1,053 | 84 | +0.0249 | 0.294 | [−0.0230, +0.0723] | NO_EVIDENCE |
| `winning_margin` | 741 | 84 | +0.0186 | 0.329 | [−0.0200, +0.0564] | NO_EVIDENCE |
| `pitcher_strikeouts` | 1,022 | 88 | −0.0168 | 0.487 | [−0.0599, +0.0288] | NO_EVIDENCE |
| `hitter_total_bases` | 5,008 | 139 | −0.0046 | 0.559 | [−0.0223, +0.0132] | NO_EVIDENCE |
| `hitter_rbis` | 2,733 | 141 | −0.0033 | 0.726 | [−0.0218, +0.0155] | NO_EVIDENCE |
| `hitter_hits_runs_rbis` | 6,414 | 139 | +0.0017 | 0.927 | [−0.0202, +0.0243] | NO_EVIDENCE |
| `hitter_hits` | 4,105 | 136 | +0.0005 | 0.969 | [−0.0166, +0.0178] | NO_EVIDENCE |

**BH-FDR over 11 families → survivors: NONE.**

No family is a `CANDIDATE_FOR_HOLDOUT`. **There is no measured family-level edge.**

## Explicit retractions and revisions

### `game_total` — RETRACTED

#233 reported a gap of +0.0376 "outside its interval". On qualified evidence the
gap is +0.0518 but the clustered CI is **[−0.0103, +0.1123]**, which comfortably
includes zero (p = 0.094).

The #233 finding was an artifact of the naive Wilson interval. It is **withdrawn**,
not merely qualified.

### `hitter_stolen_bases` — DOWNGRADED to EXPLORATORY

This is the family flagged in
[#237](https://github.com/chmoses98/edge-finder-api/pull/237) as truncation-biased
(`KXMLBSB` was truncated 5 times in 21 days). It is the one finding that got
*stronger* scrutiny and partly held up:

- it **survives** removal of PARTIAL-sourced evidence: gap −0.0202 on 1,228 rows
  across 113 games, versus −0.0217 in #233;
- it **survives** game-level clustering: CI [−0.0361, −0.0043] excludes zero;
- it **fails** multiplicity correction: p = 0.021 against a BH threshold of
  0.0045.

So the truncation-bias concern turned out not to be what kills it — the
multiple-comparisons problem is. It is **EXPLORATORY**: not a measured edge, not
promotable, and not to be cited as a property of the family.

### `inning_result` — EXPLORATORY, newly visible

Not flagged in #233. Clustered CI [−0.0186, −0.0009] barely excludes zero
(p = 0.033); fails FDR. Same status, same caution.

### Families named as truncation-biased: no material change

`hitter_hits_runs_rbis`, `hitter_rbis` and `hitter_total_bases` were all
`NO_EVIDENCE` before and after qualification. Removing PARTIAL-sourced evidence
moved their gaps by ≤ 0.004 — within noise.

## What a gap would still not be

Even had a family survived, a calibration gap is **not a profit**:

- buying YES pays the **ask**, so the spread is already against you;
- a *negative* gap means the market over-prices YES, so acting on it means
  **selling** YES — i.e. buying NO at NO's own, usually worse, ask;
- **Kalshi fees land on top** of both;
- eleven days of resolvable start times is not a season.

No result here was promoted into production policy, and no model, threshold,
staking or eligibility logic was touched.

## Reproducing

```
python3 scripts/edgelab/build_capture_completeness_ledger.py
python3 scripts/research/full_universe/run_qualified_calibration.py
```

Output: `data/edgelab/reports/full_universe_qualified_calibration.json`.
The bootstrap seed is fixed, so the numbers above are reproducible exactly.
