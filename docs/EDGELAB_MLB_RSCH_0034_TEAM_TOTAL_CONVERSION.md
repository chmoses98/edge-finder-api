# MLB-RSCH-0034 — Team-Total Probability Conversion

**Experiment:** `MLB-RSCH-0034` · **Evidence level:** `E1_RECONSTRUCTED_RETROSPECTIVE`
**Control:** `CTRL-1cce63c95bcfeb2f`
**Status:** RESEARCH ONLY. No production change. Nothing fitted.

---

## The headline corrects this program's own earlier claim

MLB-RSCH-0031 and MLB-RSCH-0032 both reported a **"+0.5 team-total threshold defect reaching live recommendations."**

**For pricing, that is no longer true, and it was already untrue when those experiments were written.**

Production contains a documented **v1.2** fix:

```python
model_p = p_over_total(proj, tt_line - 1)
model_p = min(model_p, 0.95)
```

`p_over_total(proj, L) = P(runs >= L + 1)`, so `L = N - 1` prices exactly **P(runs >= N)** — which is precisely what the contract settles. The conversion is **semantically correct today**.

The round-trip dates the change without being told it: archived rows reproduce under the **old v1.1** convention through **2026-08-20**, and under **v1.2** from **2026-08-21** onward. Both prior experiments measured a corpus dominated by pre-fix rows and neither separated the two production versions.

Their merged artifacts are **NOT rewritten**. This supersedes that one conclusion, for pricing only.

> One thing v1.2 does **not** change: the emitted row still stores `line = tt_line`, the raw ticker-suffix digit `N`. That matches Kalshi's own ticker convention and is correct as *metadata* — but it means any downstream consumer that renders it as "Over N" would be off by one against a contract that pays on `runs >= N`. This experiment did **not** audit downstream rendering, so that is flagged as an open question, not a finding.

---

## Round-trip, run BEFORE any candidate was scored

| Bucket | n |
|---|---:|
| EXACT_MATCH (v1.2) | 110 |
| TOLERANCE_MATCH (v1.2) | 18 |
| MODEL_VERSION_MISMATCH (reproduces under v1.1) | 300 |
| SEMANTIC_MISMATCH (neither convention) | 65 |
| MISSING_INPUTS | 0 |
| UNRESOLVED | 0 |

**86.8% of archived modelProb is reproducible from archived inputs once the model VERSION is respected.** No date reproduces under both conventions, so the boundary is a real version change rather than a tolerance artifact.

The 65 residual mismatches concentrate where `projections.json` and the evaluation row were written at different points in a slate's life — the same effect MLB-RSCH-0033 saw in its 40 non-reproducing team-games.

---

## Contract truth, established independently of the pricing code

A `KXMLBTEAMTOTAL` ticker suffixed `-<TEAM><N>` settles **YES iff that team scores AT LEAST N runs in the full game** — equivalently "over (N − 0.5)". It is **not** "over N".

Confirmed from three sources, none of which is `build_market_ledger`:

- `lib/research/market_taxonomy.py::_team_and_margin_from_suffix` stores `threshold = N - 0.5`
- `lib/edgelab/settlement.py::settle_market` (`FAMILY_TEAM_TOTAL`) pays YES iff the team's runs exceed that stored threshold
- `scripts/build_kalshi_registry.py` documents the suffix convention (`over_n=4` means "scores over 3.5")

Market **titles** were sampled and are explicitly recorded as display labels, **not** authoritative for the inequality.

---

## Results

### All eligible rows

| n / games / dates | 493 / 245 / 21 |
| base rate | 0.4706 |
| **C0 production** | Brier **0.2918**, slope -0.042, AUC 0.4902 |
| **C1 semantics-only** | Brier **0.2737**, slope 0.0352 |
| **C2 frozen-NB** | Brier **0.2622**, slope 0.0502, AUC 0.5162 |
| Kalshi vig-free fair | Brier 0.2480 |
| constant base rate | Brier 0.2491 |

### Pre-fix rows (production still on v1.1)

| n / games / dates | 300 / 150 / 13 |
| base rate | 0.4800 |
| **C0 production** | Brier **0.2913**, slope 0.1234, AUC 0.5269 |
| **C1 semantics-only** | Brier **0.2658**, slope 0.0993 |
| **C2 frozen-NB** | Brier **0.2597**, slope 0.1287, AUC 0.5304 |
| Kalshi vig-free fair | Brier 0.2494 |
| constant base rate | Brier 0.2496 |

### Post-fix rows (production already on v1.2 — C0 *is* C1 here)

| n / games / dates | 193 / 95 / 8 |
| base rate | 0.4560 |
| **C0 production** | Brier **0.2926**, slope -0.091, AUC 0.495 |
| **C1 semantics-only** | Brier **0.2859**, slope 0.0033 |
| **C2 frozen-NB** | Brier **0.2660**, slope 0.0238, AUC 0.5181 |
| Kalshi vig-free fair | Brier 0.2458 |
| constant base rate | Brier 0.2481 |

---

## Attribution: both corrections are real, and separately measured

| Correction | Population | Brier before → after | Gain | Game-clustered 95% CI |
|---|---|---|---:|---|
| **Semantics** (C0→C1) | v1.1-era rows only | 0.2913 → 0.2658 | **0.0256** | [-0.0453, -0.0059] |
| **Distribution** (C1→C2) | all eligible rows | 0.2737 → 0.2622 | **0.0115** | [-0.0175, -0.0053] |

Both intervals exclude zero. The semantic gain is measured **only where it is measurable** — on post-fix rows C0 and C1 are the same number by construction, so pooling would understate it. That is a population restriction, not a selection on outcome.

**Transport is computed, not asserted.** The distribution gain replicates in *both* disjoint chronological blocks (v1.1 era +0.0060, v1.2 era +0.0200), which is what earns `CHRONOLOGICAL_VALIDATION`.

---

## Classification: `CASE_C_BOTH`

both the semantic correction and the distribution change improve the proper score with game-clustered CIs excluding zero

### And yet the family is still not usable

| Comparison | Brier delta | 95% CI | |
|---|---:|---|---|
| best candidate vs **constant base rate** | +0.0135 | [+0.0030, +0.0245] | **loses** |
| best candidate vs **Kalshi vig-free fair** | +0.0141 | [+0.0027, +0.0262] | **loses** |

**Fixing the conversion was necessary and is not sufficient.**

The residual is the informativeness of the mean itself. MLB-RSCH-0033 measured it at r² = 0.0377 — an implied correlation near 0.19 — which caps attainable AUC near 0.55. Measured AUC on post-fix rows is **0.4950**, CI [0.4163, 0.5821].

The decisive structural point: **every candidate here is monotone in `teamProj` at a fixed threshold**, so all of them preserve the ordering of teams exactly. No change of distribution can manufacture ranking information the mean does not carry. That is why the negative post-fix slope cannot be repaired by C2, and why no further conversion work is worth doing on this family.

---

## Methodology V3 — and why passing it is not permission

All four labels pass: the candidate **is** genuinely better than production.

**Promotion is blocked anyway**, and the blocker is recorded separately on purpose. V3's four labels answer *"is this better than production?"* They do not answer *"is this good enough to bet?"* A family that cannot beat its own base rate cannot be priced against a sharp market however much its internal conversion improves.

Preregistered before any candidate was scored: effect floor 0.005, minimum score improvement 0.005, 100 independent games, 15 independent dates, clustering by `gameId` (both contracts in a game share one game state), 25 executable opportunities, chronological transport. The NB dispersion is RSCH-0010's frozen **0.281513**, imported and never estimated on this sample.

### A reversal that is reported but NOT claimed

Within every threshold stratum meeting the row floor, C2's Brier is slightly *below* the market's, while pooled it is *above*. That is a Simpson effect — the thresholds carry different base rates and the pooled constant cannot vary across them. It carries no interval, and three strata tested without multiplicity control is exactly the favourable-sign reading V3 exists to refuse. **The pooled comparison is the one that counts.**

---

## Threshold detail

| Contract | n | games | base | C2 | constant | market |
|---|---:|---:|---:|---:|---:|---:|
| AT_LEAST_2 | 13 | — | — | *insufficient sample* | | |
| AT_LEAST_3 | 41 | 38 | 0.610 | 0.2447 | 0.2380 | 0.2473 |
| AT_LEAST_4 | 234 | 174 | 0.444 | 0.2521 | 0.2469 | 0.2533 |
| AT_LEAST_5 | 157 | 136 | 0.446 | 0.2492 | 0.2471 | 0.2516 |
| AT_LEAST_6 | 29 | — | — | *insufficient sample* | | |
| AT_LEAST_7 | 9 | — | — | *insufficient sample* | | |
| AT_LEAST_8 | 10 | — | — | *insufficient sample* | | |

---

## Scope and refusals

- No production change is proposed. The one unambiguous conversion bug this experiment could have found **was already fixed by production before the experiment ran**, so the authorised "team-total correction PR" is **not** created — its precondition is not met.
- Swapping Poisson for the negative binomial is a **modelling choice, not a bug fix**, and it does not make the family beat a constant. It is not proposed for production.
- No dispersion, coefficient or threshold was fitted. No ROI, stake or P&L figure was computed anywhere in the scoring path.
