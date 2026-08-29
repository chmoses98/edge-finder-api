# MLB-RSCH-0027 -- Production Scope Integrity and Family-Resolved Skill

**RESEARCH ONLY. No production change. No candidate activated. Parameters fitted: 0.**

## Why this experiment

RSCH-0023, -0024, -0025 and -0026 each fitted a correction to the same three-week market
archive, and each failed to transport out of the window it was fitted on. Rather than fit a
fifth, this experiment audits the foundation all four rested on: **the definition of the
corpus**. Every bet-selection decision for the rest of 2026 rests on how far production
trails the market and in which families. That number came from RSCH-0022. This asks whether
it was measured on the right rows.

## Two scope defects

### D1 -- scope contamination

The audit corpus applies no `qualityTier` filter, so it pools two different populations:
**TRUSTED_PRODUCTION** families (what production actually prices and trades) and
**RESEARCH_ONLY** boards (exploratory surfaces that are not production and never were).

- RESEARCH_ONLY share of the pooled corpus: **74.6%**
- Degenerate market prices (exactly 0.0 or 1.0) in production: **0**
- Degenerate market prices in the research boards: **308**

Every production-family row uses the `kalshiVF` vig-free adapter. The ask-price adapter and
every degenerate price live exclusively in the research boards -- the benchmark corruption
RSCH-0024 identified was never in the production corpus at all.

### D2 -- corpus loss

Six production families key their evaluations by a synthetic `<gamePk>:<FAMILY>` identifier
rather than a Kalshi ticker. The settlement archive is ticker-keyed, so these rows cannot
join **by construction** -- they are invisible to every audit this program has run, not
excluded by any rule.

- Synthetic-key rows by family: `{'ML_Away': 378, 'ML_Home': 379, 'F5_ML_Away': 310, 'F5_ML_Home': 355, 'NRFI': 376, 'YRFI': 29}`
- Recovered here (moneyline, settled from the dated final score): **323**
- Not recoverable without an inning-resolved linescore: `['F5_ML_Away', 'F5_ML_Home', 'NRFI', 'YRFI']`

## Corpus comparison

Paired delta is **production minus Kalshi**, so a *negative* number means production is better.

| Corpus | Rows | Games | Rows/game | Model Brier | Market Brier | Paired delta [95% CI] |
|---|---:|---:|---:|---:|---:|---|
| Pooled (RSCH-0022 reproduction) | 3137 | 293 | 10.71 | 0.2268 | 0.1719 | +0.0549 [0.0391, 0.0718] |
| RESEARCH_ONLY boards | 2341 | 77 | 30.4 | 0.2105 | 0.1469 | +0.0636 [0.0417, 0.0865] |
| Production (settlement-joined) | 796 | 237 | 3.36 | 0.2750 | 0.2454 | +0.0295 [0.0201, 0.0391] |
| Recovered moneylines (never before scored) | 323 | 175 | 1.85 | 0.2469 | 0.2165 | +0.0304 [0.0144, 0.0472] |
| **Production (scope-corrected + recovered)** | 1119 | 247 | 4.53 | 0.2669 | 0.2371 | +0.0298 [0.0206, 0.0392] |

## Preregistered family-resolved test

A family is called `PRODUCTION_SHOWS_SKILL` only if **all four** conditions hold:

1. paired Brier delta < 0 with game-clustered bootstrap CI upper bound < 0
2. survives Benjamini-Hochberg FDR at 0.1
3. meets sample floors (>= 100 rows, >= 20 games)
4. direction still holds in HOLDOUT (settle > 2026-08-24)

| Family | Rows | Games | Model | Market | Paired delta [CI] | p | Holdout | Prod. cal. slope | Verdict |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| KXMLBF5 | 63 | 63 | 0.2252 | 0.2266 | -0.0014 [-0.0176, 0.015] | None | -0.028323 | 1.6246 | INSUFFICIENT_SAMPLE |
| KXMLBGAME | 47 | 47 | 0.2446 | 0.2299 | +0.0148 [-0.0011, 0.0302] | None | 0.059355 | 1.5205 | INSUFFICIENT_SAMPLE |
| KXMLBRFI | 213 | 213 | 0.2562 | 0.2481 | +0.0081 [-0.0054, 0.0214] | 0.3183 | 0.013201 | 1.0422 | INCONCLUSIVE |
| KXMLBTEAMTOTAL | 473 | 236 | 0.2930 | 0.2483 | +0.0447 [0.0292, 0.0608] | 0.0005 | 0.067397 | -0.0711 | PRODUCTION_TRAILS_MARKET |
| ML_Away | 162 | 162 | 0.2484 | 0.2190 | +0.0294 [0.0135, 0.046] | 0.0025 | 0.064557 | 0.5361 | PRODUCTION_TRAILS_MARKET |
| ML_Home | 161 | 161 | 0.2454 | 0.2140 | +0.0314 [0.0154, 0.0471] | 0.003 | 0.065402 | 0.6924 | PRODUCTION_TRAILS_MARKET |

## Preregistration honesty

This experiment mixes a descriptive correction with a confirmatory test, and they are
labeled separately because they earn different trust.

- **Observed before preregistration** (descriptive, no inferential claim): defects D1 and D2,
  and the aggregate pooled-vs-production Brier point estimates. These were found while
  scoping which experiment to run, so they carry no p-value and no confidence claim.
- **Preregistered and genuinely unseen**: everything on the recovered corpus. Those rows have
  never been scored by any experiment in this program -- they could not be, they do not join.

No prior experiment artifact is rewritten. RSCH-0022, -0024 and -0026 stand exactly as merged;
this reports a new finding about the corpus they used.

## Result

- Finding: **PRODUCTION_TRAILS_MARKET_IN_EVERY_QUALIFYING_FAMILY**
- Disposition: **LEVEL_0_NO_PRODUCTION_FAMILY_BEATS_MARKET** (maximum permitted: LEVEL_1_SHADOW_CANDIDATE)
- Forward-window rows touched: 0
- Production activation authorized: False

No family passed the scoring rule, so no economics were computed. Economics never rescue a failed forecaster.
