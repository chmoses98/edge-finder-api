# MLB-RSCH-0028 -- Hitter Prop Probability Validity / Edge Audit

**RESEARCH ONLY. No production change. No candidate activated. Parameters fitted: 0.**

## Why this experiment exists

Hitter props are the large majority of what this system surfaces -- roughly 61,683 of 82,304
recommendation rows and 77,135 of 100,695 settled contracts -- yet MLB-RSCH-0022, -0024 and -0027
contained **zero** hitter rows. Every previous statement that "production trails Kalshi" described
only a minority of the system's output. This is the first audit of the majority.

## The dominant structural caveat

**5,168 eligible rows resolve to only 261 playerGameKeys across 36 games and 7 dates** -- 19.8 rows
per player-game. The same player-game recurs at up to five checkpoints and across multiple ladder
rungs, so these are **repeated measures**, not independent observations. Treating the row count as
the sample size would overstate precision by roughly an order of magnitude. Every interval below
clusters on `playerGameKey`; game- and date-clustered variants are reported alongside.

## Corpus and exclusions

- Snapshot rows: **9,256**
- Eligible after every preregistered rule: **5,168**
- Excluded: **4,088**, every one reason-coded:

| Reason | Rows |
|---|---:|
| `NO_VALID_PREGAME_QUOTE_AT_OR_BEFORE_CHECKPOINT` | 3486 |
| `PROJECTION_STATUS_MODEL_ERROR` | 211 |
| `NO_SETTLED_OUTCOME` | 177 |
| `PROJECTION_STATUS_PLAYER_NOT_IN_STARTING_LINEUP` | 177 |
| `PROJECTION_STATUS_LINEUP_UNCONFIRMED` | 28 |
| `DEGENERATE_FAIR_MIDPOINT` | 9 |

## Join / settlement audit

**Mechanism:** exact canonical Kalshi marketTicker equality between the hitter snapshot and the settlement archive -- no player/threshold string parsing, no sourceBetKey, no fuzzy matching, so a join is either exact or absent

- Snapshot tickers: 5,954 · joined: 5,774 (**97.0%**)
- Unresolved: 180 — present in archive with a null outcome: 180; absent entirely: 0

every unresolved ticker IS present in the settlement archive carrying a null outcome -- captured but not yet resolved by Kalshi. None is a join failure, so the join mechanism itself is 100% effective on this archive.

## Headline: model vs Kalshi contemporaneous fair price

Paired delta is **model minus market** — negative means the model is better.

| | Model | Kalshi fair |
|---|---:|---:|
| Brier | 0.158417 | 0.155417 |
| Log loss | 0.488581 | 0.477272 |
| ECE | 0.028144 | 0.015101 |
| Calibration slope | 0.8635 | 0.9847 |

**Paired Brier delta: +0.003000** [-0.0002, 0.0064] (clustered on `playerGameKey`)

- game-clustered: [-0.0024, 0.0073]
- date-clustered: [-0.0007, 0.0044]
- paired log-loss delta: +0.011309

**Verdict: `PARITY`**

## By family

| Family | Rows | Keys | Model | Market | Paired delta [CI] | p | FDR | Verdict |
|---|---:|---:|---:|---:|---|---:|:-:|---|
| hitter_hits | 1172 | 254 | 0.145683 | 0.142754 | +0.0029 [-0.0023, 0.0085] | 0.3716 | no | PARITY |
| hitter_total_bases | 1423 | 252 | 0.150173 | 0.148135 | +0.0020 [-0.002, 0.0058] | 0.4065 | no | PARITY |
| hitter_hits_runs_rbis | 1795 | 256 | 0.176346 | 0.173836 | +0.0025 [-0.0015, 0.008] | 0.3616 | no | PARITY |
| hitter_rbis | 778 | 252 | 0.151314 | 0.145317 | +0.0060 [-0.0006, 0.0127] | 0.1322 | no | PARITY |

## Declared-edge buckets

Production declares edge against the **executable ask** (its own definition, preserved unchanged).
The question is whether a larger declared edge buys a larger realized advantage over Kalshi.

| Declared edge | Rows | Keys | Paired delta | Verdict |
|---|---:|---:|---:|---|
| [-1.000,+0.000) | 2719 | 244 | +0.0031 | PARITY |
| [+0.000,+0.025) | 762 | 213 | +0.0007 | PARITY |
| [+0.025,+0.050) | 656 | 193 | +0.0002 | PARITY |
| [+0.050,+0.075) | 383 | 148 | +0.0045 | PARITY |
| [+0.075,+0.100) | 263 | 105 | +0.0089 | PARITY |
| [+0.100,+0.150) | 319 | 94 | +0.0117 | PARITY |
| [+0.150,+1.010) | 66 | 27 | -0.0226 | INSUFFICIENT_SAMPLE |

- Monotone improving with declared edge: **False**
- **Edge inversion (model relatively WORSE at high declared edge): True**

## By checkpoint

| Checkpoint | Rows | Keys | Model Brier | Market Brier | Paired delta | Mean ask | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| T_MINUS_90 | 1415 | 106 | 0.164721 | 0.16259 | +0.0021 | 0.2493 | PARITY |
| T_MINUS_60 | 740 | 53 | 0.159813 | 0.152429 | +0.0074 | 0.2547 | MARKET_BEATS_MODEL |
| T_MINUS_30 | 1506 | 119 | 0.149409 | 0.146473 | +0.0029 | 0.2469 | PARITY |
| LINEUP_CONFIRMATION | 1262 | 98 | 0.167274 | 0.166703 | +0.0006 | 0.2775 | PARITY |
| HITTER_CLOSING_WINDOW | 245 | 18 | 0.127545 | 0.119861 | +0.0077 | 0.2472 | PARITY |

## Exactly-paired checkpoint transitions

Same player, game, family AND threshold observed at both checkpoints — far more informative than
comparing unrelated checkpoint populations, which differ in which games they even contain.

| Transition | Pairs | Model Brier change | Market Brier change | Mean abs model move | Later better |
|---|---:|---:|---:|---:|:-:|
| T_MINUS_90->T_MINUS_30 | 903 | +0.00000 | -0.00006 | 0.0000 | no |
| T_MINUS_30->LINEUP_CONFIRMATION | 48 | +0.00000 | +0.00000 | 0.0000 | no |
| LINEUP_CONFIRMATION->HITTER_CLOSING_WINDOW | 14 | -0.00018 | +0.00000 | 0.0028 | yes |

## Lineup-confirmation value (paired on the same market rung)

- Pairs: 180 across 13 playerGameKeys
- Model Brier: 0.210289 pre-lineup -> 0.217298 confirmed (**improvement -0.00701**)
- Market Brier: 0.197636 -> 0.197345 (improvement +0.00029)
- Paired delta vs market: +0.01265 pre -> +0.01995 confirmed
- **Confirmation helps the model: False**

## Disagreement direction

| Direction | Rows | Keys | Paired delta | Verdict |
|---|---:|---:|---:|---|
| MODEL_ABOVE_MARKET | 2445 | 240 | +0.0028 | PARITY |
| MODEL_BELOW_MARKET | 2719 | 244 | +0.0031 | PARITY |

## Calibration (fixed deciles)

| Bin | Rows | Model predicted | Actual | Market predicted |
|---|---:|---:|---:|---:|
| [0.0,0.1) | 1623 | 0.0605 | 0.0826 | 0.0595 |
| [0.1,0.2) | 1092 | 0.1493 | 0.1832 | 0.1446 |
| [0.2,0.3) | 878 | 0.2426 | 0.2506 | 0.2442 |
| [0.3,0.4) | 451 | 0.3453 | 0.3149 | 0.343 |
| [0.4,0.5) | 344 | 0.4443 | 0.4477 | 0.4492 |
| [0.5,0.6) | 74 | 0.5443 | 0.5946 | 0.55 |
| [0.6,0.7) | 344 | 0.6626 | 0.5581 | 0.6461 |
| [0.7,0.8) | 362 | 0.7328 | 0.7017 | 0.7316 |

## Sample-depth bins (archived PA count, PIT-safe)

| Bin | Rows | Keys | Paired delta | Verdict |
|---|---:|---:|---:|---|
| ZERO_ARCHIVED_PA | 0 | - | - | INSUFFICIENT_SAMPLE |
| 1_49_PA | 4905 | 243 | +0.0033 | PARITY |
| 50_199_PA | 263 | 18 | -0.0026 | PARITY |
| 200_PLUS_PA | 0 | - | - | INSUFFICIENT_SAMPLE |

## Chronological structure

DEVELOPMENT (<= 2026-08-22): 4326 rows / 208 keys, delta 0.003568

VALIDATION (later): 842 rows / 53 keys, delta 8.1e-05

With so few independent player-games, the validation block supports a **directional** read only;
no confirmatory claim is made from it. Leave-one-date-out deltas are reported as robustness.

## Secondary fee-aware economics

Computed only AFTER the predictive verdict. Never selective. **Never implies any recommendation was
actually bet** -- automatic bet settlement remains GitHub issue #43's separate concern, not
implemented or claimed here.

| Segment | Opportunities | Wins | Avg entry | Gross (pre-fee) | Fees | Net | ROI |
|---|---:|---:|---:|---:|---:|---:|---:|
| OVERALL | 2445 | 725 | 0.2894 | 17.42 | 39.14 | -21.72 | -0.0307 |
| hitter_hits | 1046 | 280 | 0.2625 | 5.405 | 15.98 | -10.575 | -0.0385 |
| hitter_total_bases | 485 | 104 | 0.1998 | 7.095 | 7.41 | -0.315 | -0.0033 |
| hitter_hits_runs_rbis | 857 | 328 | 0.3844 | -1.395 | 15.0 | -16.395 | -0.0498 |
| hitter_rbis | 57 | 13 | 0.1173 | 6.315 | 0.75 | 5.565 | 0.8325 |
| T_MINUS_90 | 765 | 243 | 0.2803 | 28.555 | 12.14 | 16.415 | 0.0765 |
| T_MINUS_60 | 340 | 97 | 0.304 | -6.365 | 5.59 | -11.955 | -0.1157 |
| T_MINUS_30 | 824 | 214 | 0.2804 | -17.055 | 13.11 | -30.165 | -0.1306 |
| LINEUP_CONFIRMATION | 399 | 148 | 0.3145 | 22.515 | 6.42 | 16.095 | 0.1283 |
| HITTER_CLOSING_WINDOW | 117 | 23 | 0.284 | -10.23 | 1.88 | -12.11 | -0.3644 |

## Result

- Classification: **PARITY**
- Disposition: **LEVEL_0_MEASUREMENT_ONLY** (maximum permitted: SHADOW_CANDIDATE)
- Shadow candidate justified: **False**
- Production activation authorized: False
- Correction fitted: False · user wagers used: False
