## MLB projection scorecard (daily) — last 7 days

Generated 2026-10-28T00:11:30Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1145 across 6 games. Settled: 875 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 6 (3) | 2.528 | +2.280 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 6 (3) | 1.654 | +1.348 | 1.316 | 100% | — |
| game_total | incumbent_team_runs (incumbent) | 3 (3) | 2.442 | -0.887 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 3 (3) | 2.390 | -0.209 | 1.619 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 22 (2) | 0.678 | +0.072 | — | — |  |
| hits | pf-mlb:F (challenger) | 54 (3) | 0.724 | -0.093 | 0.476 | 76% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 22 (2) | 1.134 | +0.130 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 54 (3) | 1.356 | -0.125 | 0.885 | 87% | — |
| home_runs | pf-mlb:F (challenger) | 54 (3) | 0.141 | +0.072 | 0.044 | 81% | — |
| outs | incumbent_pitcher_champion (incumbent) | 6 (3) | 2.845 | +1.762 | — | — |  |
| outs | pf-mlb:F (challenger) | 6 (3) | 2.844 | +1.268 | 2.106 | 67% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 6 (3) | 1.301 | +0.861 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 6 (3) | 1.448 | +1.063 | 0.905 | 83% | — |
| plate_appearances | pf-mlb:F (challenger) | 54 (3) | 0.560 | +0.087 | 0.351 | 80% | — |
| rbis | incumbent_hitter_engine (incumbent) | 21 (2) | 0.406 | +0.059 | — | — |  |
| rbis | pf-mlb:F (challenger) | 54 (3) | 0.540 | -0.046 | 0.315 | 89% | — |
| runs | pf-mlb:F (challenger) | 54 (3) | 0.477 | +0.018 | 0.245 | 83% | — |
| team_runs | incumbent_team_runs (incumbent) | 6 (3) | 2.208 | -0.443 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 6 (3) | 2.157 | -0.105 | 1.526 | 83% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 22 (2) | 0.860 | +0.327 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 54 (3) | 1.037 | +0.073 | 0.683 | 81% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 6 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 3 settled forecasts (< 100)
- hits: **INSUFFICIENT** — 54 settled forecasts (< 100)
- hits_runs_rbis: **INSUFFICIENT** — 54 settled forecasts (< 100)
- home_runs: **INSUFFICIENT** — 54 settled forecasts (< 100)
- outs: **INSUFFICIENT** — 6 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 6 settled forecasts (< 100)
- plate_appearances: **INSUFFICIENT** — 54 settled forecasts (< 100)
- rbis: **INSUFFICIENT** — 54 settled forecasts (< 100)
- runs: **INSUFFICIENT** — 54 settled forecasts (< 100)
- team_runs: **INSUFFICIENT** — 6 settled forecasts (< 100)
- total_bases: **INSUFFICIENT** — 54 settled forecasts (< 100)

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 6 | 1.6538 | 1.3479 | 6 | 2.5281 |
| normal_rest(>4d) | 6 | 1.6538 | 1.3479 | 6 | 2.5281 |
| went_deeper(actual BF>12) | 6 | 1.6538 | 1.3479 | 6 | 2.5281 |
| starter_CONFIRMED | 6 | 1.6538 | 1.3479 | 6 | 2.5281 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 6 | 2.8437 | 1.2679 | 6 | 2.8454 |
| normal_rest(>4d) | 6 | 2.8437 | 1.2679 | 6 | 2.8454 |
| went_deeper(actual BF>12) | 6 | 2.8437 | 1.2679 | 6 | 2.8454 |
| starter_CONFIRMED | 6 | 2.8437 | 1.2679 | 6 | 2.8454 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 6 | 1.4479 | 1.0627 | 6 | 1.3014 |
| normal_rest(>4d) | 6 | 1.4479 | 1.0627 | 6 | 1.3014 |
| went_deeper(actual BF>12) | 6 | 1.4479 | 1.0627 | 6 | 1.3014 |
| starter_CONFIRMED | 6 | 1.4479 | 1.0627 | 6 | 1.3014 |

**Participation:** pitcher forecasts resolved 12; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
