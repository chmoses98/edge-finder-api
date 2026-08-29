# MLB-RSCH-0032 -- YELLOW Family Validity Audit

**RESEARCH ONLY. No production change. Parameters fitted: 0. Actionability labels from Methodology V3.**

## Why these families

MLB-RSCH-0031 found ~80% of live recommendation exposure sits in YELLOW families -- unproven
or below sample floor -- and that those, not the RED ones, carry most of the historical
hypothetical loss. This asks whether they deserve to stay YELLOW.

## Semantics established before analysis

- **KXMLBRFI**: one binary contract per game; ticker == eventTicker; YES == a run scored in the first inning by either team; no threshold, no team side
- **KXMLBF5**: THREE-WAY market carrying an explicit -TIE settled outcome alongside both sides

## Production (YELLOW) families

Paired delta is **model minus market** — negative means the model is better.
`base` is the Brier of a constant base-rate predictor: a model worse than it carries no
discriminative information at all.

| Family | Rows | Games | Dates | Model | Market | Base rate | Paired delta [CI] | Slope | LODO | Class |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---|
| KXMLBRFI | 225 | 225 | 21 | 0.257733 | 0.24813 | 0.249995 | +0.00960 [-0.0026, 0.0217] | 0.7987 | 6/19 | **MODEL_TRAILS_MARKET** |
| KXMLBF5 | 67 | 67 | 20 | 0.224112 | 0.226731 | 0.243261 | -0.00262 [-0.0185, 0.0137] | 1.7447 | 3/6 | **INSUFFICIENT_SAMPLE** |
| KXMLBGAME | 49 | 49 | 17 | 0.246343 | 0.229706 | 0.241566 | +0.01664 [0.0002, 0.0323] | 1.5551 | 1/2 | **INSUFFICIENT_SAMPLE** |

### Methodology V3 labels (never collapsed)

| Family | STATISTICAL_SIGNAL | PREDICTIVE_MATERIALITY | EXECUTABLE_CAPACITY | IMPLEMENTATION_READINESS |
|---|:-:|:-:|:-:|:-:|
| KXMLBRFI | no | no | yes | yes |
| KXMLBF5 | no | no | yes | no |
| KXMLBGAME | yes | no | yes | no |

## Declared-edge reliability

### KXMLBRFI

monotone improving: **None** · inversion: **None** · qualifying buckets: 2

| Edge bucket | Rows | Games | Mean model p | Realized rate | Paired delta |
|---|---:|---:|---:|---:|---:|
| [-1.000,+0.000) | 0 | - | - | - | - |
| [+0.000,+0.025) | 0 | - | - | - | - |
| [+0.025,+0.050) | 6 | 6 | 0.5888 | 0.6667 | -0.00853 |
| [+0.050,+0.075) | 28 | 28 | 0.5856 | 0.6071 | -0.00842 |
| [+0.075,+0.100) | 56 | 56 | 0.5879 | 0.5179 | +0.00411 |
| [+0.100,+0.150) | 106 | 106 | 0.5989 | 0.4717 | +0.01555 |
| [+0.150,+1.010) | 29 | 29 | 0.5994 | 0.4483 | +0.01963 |

### KXMLBF5

monotone improving: **None** · inversion: **None** · qualifying buckets: 0

| Edge bucket | Rows | Games | Mean model p | Realized rate | Paired delta |
|---|---:|---:|---:|---:|---:|
| [-1.000,+0.000) | 0 | - | - | - | - |
| [+0.000,+0.025) | 0 | - | - | - | - |
| [+0.025,+0.050) | 5 | 5 | 0.4151 | 0.2 | +0.01511 |
| [+0.050,+0.075) | 23 | 23 | 0.4487 | 0.4783 | -0.00566 |
| [+0.075,+0.100) | 25 | 25 | 0.4794 | 0.28 | +0.02379 |
| [+0.100,+0.150) | 14 | 14 | 0.4733 | 0.6429 | -0.05111 |
| [+0.150,+1.010) | 0 | - | - | - | - |

### KXMLBGAME

monotone improving: **None** · inversion: **None** · qualifying buckets: 0

| Edge bucket | Rows | Games | Mean model p | Realized rate | Paired delta |
|---|---:|---:|---:|---:|---:|
| [-1.000,+0.000) | 0 | - | - | - | - |
| [+0.000,+0.025) | 0 | - | - | - | - |
| [+0.025,+0.050) | 9 | 9 | 0.6098 | 0.4444 | +0.01289 |
| [+0.050,+0.075) | 34 | 34 | 0.5372 | 0.3529 | +0.02084 |
| [+0.075,+0.100) | 5 | 5 | 0.5674 | 0.8 | -0.04404 |
| [+0.100,+0.150) | 0 | - | - | - | - |
| [+0.150,+1.010) | 1 | 1 | 0.4951 | 0.0 | +0.21097 |

## Fee-aware executable capacity

| Family | Gross +edge rows | Net-EV+ opportunities | Wins | Fees | Net P/L | ROI |
|---|---:|---:|---:|---:|---:|---:|
| KXMLBRFI | 225 | 225 | 113 | 4.5 | -0.11 | -0.001 |
| KXMLBF5 | 67 | 67 | 28 | 1.32 | 1.0244 | 0.0399 |
| KXMLBGAME | 49 | 49 | 20 | 0.98 | -4.7946 | -0.2013 |

## Research-only boards

Audited **separately**; unrelated boards are never pooled to raise n.

| Board | Rows | Games | Model | Market | Base rate | Paired delta | Slope | Class |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| pitcher_strikeouts | 566 | 44 | 0.203141 | 0.106961 | 0.241232 | +0.09618 | 0.7116 | INSUFFICIENT_SAMPLE |
| team_total | 492 | 41 | 0.180076 | 0.128742 | 0.246761 | +0.05133 | 0.8359 | INSUFFICIENT_SAMPLE |
| game_total | 369 | 61 | 0.213621 | 0.176952 | 0.246914 | +0.03667 | 0.6593 | INSUFFICIENT_SAMPLE |
| winning_margin | 357 | 68 | 0.212803 | 0.147149 | 0.211002 | +0.06565 | 0.9445 | INSUFFICIENT_SAMPLE |
| inning_result | 251 | 42 | 0.221383 | 0.160949 | 0.222663 | +0.06043 | 0.5334 | INSUFFICIENT_SAMPLE |
| inning_total | 196 | 28 | 0.221183 | 0.195988 | 0.249766 | +0.02519 | 0.6713 | INSUFFICIENT_SAMPLE |
| game_result | 84 | 43 | 0.262659 | 0.203024 | 0.249858 | +0.05964 | 0.1531 | INSUFFICIENT_SAMPLE |
| pitcher_outs | 76 | 42 | 0.277323 | 0.1549 | 0.248442 | +0.12242 | -0.3806 | INSUFFICIENT_SAMPLE |
| first_inning_run | 1 | 1 | 0.16358 | 0.0 | 0.0 | +0.16358 | None | INSUFFICIENT_SAMPLE |

## Synthetic-identifier recovery -- attempted and reported unrecoverable

- Synthetic rows by family: `{'ML_Away': 400, 'ML_Home': 400, 'F5_ML_Away': 315, 'F5_ML_Home': 363, 'NRFI': 383, 'YRFI': 29}`
- Mechanism tested: gamePk -> games-archive mlbGamePk/kalshiKey index -> unique settled Kalshi ticker
- **Recovered: 0**

ZERO of the synthetic first-inning rows resolved to a unique settled KXMLBRFI ticker: those gamePk values are absent from the games-archive index. No approximate, fuzzy or date-proximity match was attempted.

KXMLBF5 settles three ways -- its archived tickers include an explicit -TIE outcome -- so the two-way F5_ML_Home/F5_ML_Away synthetic rows are not the same contract and were not bridged.

## Frozen prospective design for families that cannot be validated yet

| Family | Games now | Required | Short by | Dates now | Required |
|---|---:|---:|---:|---:|---:|
| KXMLBF5 | 67 | 100 | 33 | 20 | 10 |
| KXMLBGAME | 49 | 100 | 51 | 17 | 10 |

**Floors are not lowered to manufacture a result.** These families wait for data.

## Result

- Production action authorized: False
