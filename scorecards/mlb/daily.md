## MLB projection scorecard (daily) — last 7 days

Generated 2026-10-27T02:37:51Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1004 across 5 games. Settled: 731 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 4 (2) | 2.868 | +2.868 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 4 (2) | 2.104 | +2.104 | 1.552 | 100% | — |
| game_total | incumbent_team_runs (incumbent) | 2 (2) | 2.328 | +0.004 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 2 (2) | 2.132 | +1.139 | 1.431 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 22 (2) | 0.678 | +0.072 | — | — |  |
| hits | pf-mlb:F (challenger) | 36 (2) | 0.609 | -0.069 | 0.402 | 81% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 22 (2) | 1.134 | +0.130 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 36 (2) | 1.072 | +0.030 | 0.726 | 89% | — |
| home_runs | pf-mlb:F (challenger) | 36 (2) | 0.157 | +0.054 | 0.060 | 78% | — |
| outs | incumbent_pitcher_champion (incumbent) | 4 (2) | 3.137 | +3.069 | — | — |  |
| outs | pf-mlb:F (challenger) | 4 (2) | 3.112 | +2.530 | 2.385 | 50% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 4 (2) | 1.618 | +0.958 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 4 (2) | 1.743 | +1.165 | 1.072 | 75% | — |
| plate_appearances | pf-mlb:F (challenger) | 36 (2) | 0.444 | -0.001 | 0.259 | 89% | — |
| rbis | incumbent_hitter_engine (incumbent) | 21 (2) | 0.406 | +0.059 | — | — |  |
| rbis | pf-mlb:F (challenger) | 36 (2) | 0.520 | +0.024 | 0.282 | 86% | — |
| runs | pf-mlb:F (challenger) | 36 (2) | 0.457 | +0.076 | 0.222 | 86% | — |
| team_runs | incumbent_team_runs (incumbent) | 4 (2) | 1.164 | +0.002 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 4 (2) | 1.066 | +0.570 | 0.828 | 100% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 22 (2) | 0.860 | +0.327 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 36 (2) | 0.940 | +0.074 | 0.655 | 78% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 4 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 2 settled forecasts (< 100)
- hits: **INSUFFICIENT** — 36 settled forecasts (< 100)
- hits_runs_rbis: **INSUFFICIENT** — 36 settled forecasts (< 100)
- home_runs: **INSUFFICIENT** — 36 settled forecasts (< 100)
- outs: **INSUFFICIENT** — 4 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 4 settled forecasts (< 100)
- plate_appearances: **INSUFFICIENT** — 36 settled forecasts (< 100)
- rbis: **INSUFFICIENT** — 36 settled forecasts (< 100)
- runs: **INSUFFICIENT** — 36 settled forecasts (< 100)
- team_runs: **INSUFFICIENT** — 4 settled forecasts (< 100)
- total_bases: **INSUFFICIENT** — 36 settled forecasts (< 100)

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 4 | 2.1044 | 2.1044 | 4 | 2.8678 |
| normal_rest(>4d) | 4 | 2.1044 | 2.1044 | 4 | 2.8678 |
| went_deeper(actual BF>12) | 4 | 2.1044 | 2.1044 | 4 | 2.8678 |
| starter_CONFIRMED | 4 | 2.1044 | 2.1044 | 4 | 2.8678 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 4 | 3.1125 | 2.53 | 4 | 3.1369 |
| normal_rest(>4d) | 4 | 3.1125 | 2.53 | 4 | 3.1369 |
| went_deeper(actual BF>12) | 4 | 3.1125 | 2.53 | 4 | 3.1369 |
| starter_CONFIRMED | 4 | 3.1125 | 2.53 | 4 | 3.1369 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 4 | 1.7428 | 1.1651 | 4 | 1.6178 |
| normal_rest(>4d) | 4 | 1.7428 | 1.1651 | 4 | 1.6178 |
| went_deeper(actual BF>12) | 4 | 1.7428 | 1.1651 | 4 | 1.6178 |
| starter_CONFIRMED | 4 | 1.7428 | 1.1651 | 4 | 1.6178 |

**Participation:** pitcher forecasts resolved 10; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
