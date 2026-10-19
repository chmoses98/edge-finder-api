## MLB projection scorecard (weekly) — last 28 days

Generated 2026-10-19T15:52:26Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 160 across 2 games. Settled: 144 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 2 (1) | 5.318 | +2.151 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 2 (1) | 5.666 | +2.684 | 4.118 | 50% | — |
| game_total | incumbent_team_runs (incumbent) | 1 (1) | 2.281 | +2.281 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 1 (1) | 3.715 | +3.715 | 1.954 | 100% | — |
| hits | pf-mlb:F (challenger) | 18 (1) | 0.672 | -0.095 | 0.426 | 72% | — |
| hits_runs_rbis | pf-mlb:F (challenger) | 18 (1) | 1.088 | +0.218 | 0.670 | 100% | — |
| home_runs | pf-mlb:F (challenger) | 18 (1) | 0.100 | +0.100 | 0.010 | 78% | — |
| outs | incumbent_pitcher_champion (incumbent) | 2 (1) | 6.676 | +2.181 | — | — |  |
| outs | pf-mlb:F (challenger) | 2 (1) | 6.274 | +2.424 | 4.535 | 50% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 2 (1) | 1.064 | +0.677 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 2 (1) | 1.248 | +0.797 | 0.863 | 100% | — |
| plate_appearances | pf-mlb:F (challenger) | 18 (1) | 0.430 | +0.113 | 0.253 | 89% | — |
| rbis | pf-mlb:F (challenger) | 18 (1) | 0.427 | +0.165 | 0.177 | 89% | — |
| runs | pf-mlb:F (challenger) | 18 (1) | 0.427 | +0.180 | 0.179 | 72% | — |
| team_runs | incumbent_team_runs (incumbent) | 2 (1) | 1.141 | +1.141 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 2 (1) | 1.858 | +1.858 | 1.011 | 100% | — |
| total_bases | pf-mlb:F (challenger) | 18 (1) | 0.895 | +0.237 | 0.548 | 83% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 2 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 1 settled forecasts (< 100)
- hits: **INSUFFICIENT** — 18 settled forecasts (< 100)
- hits_runs_rbis: **INSUFFICIENT** — 18 settled forecasts (< 100)
- home_runs: **INSUFFICIENT** — 18 settled forecasts (< 100)
- outs: **INSUFFICIENT** — 2 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 2 settled forecasts (< 100)
- plate_appearances: **INSUFFICIENT** — 18 settled forecasts (< 100)
- rbis: **INSUFFICIENT** — 18 settled forecasts (< 100)
- runs: **INSUFFICIENT** — 18 settled forecasts (< 100)
- team_runs: **INSUFFICIENT** — 2 settled forecasts (< 100)
- total_bases: **INSUFFICIENT** — 18 settled forecasts (< 100)

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 2 | 5.6663 | 2.6838 | 2 | 5.3178 |
| normal_rest(>4d) | 2 | 5.6663 | 2.6838 | 2 | 5.3178 |
| went_deeper(actual BF>12) | 2 | 5.6663 | 2.6838 | 2 | 5.3178 |
| starter_CONFIRMED | 2 | 5.6663 | 2.6838 | 2 | 5.3178 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 2 | 6.2738 | 2.4237 | 2 | 6.6764 |
| normal_rest(>4d) | 2 | 6.2738 | 2.4237 | 2 | 6.6764 |
| went_deeper(actual BF>12) | 2 | 6.2738 | 2.4237 | 2 | 6.6764 |
| starter_CONFIRMED | 2 | 6.2738 | 2.4237 | 2 | 6.6764 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 2 | 1.2482 | 0.797 | 2 | 1.0636 |
| normal_rest(>4d) | 2 | 1.2482 | 0.797 | 2 | 1.0636 |
| went_deeper(actual BF>12) | 2 | 1.2482 | 0.797 | 2 | 1.0636 |
| starter_CONFIRMED | 2 | 1.2482 | 0.797 | 2 | 1.0636 |

**Participation:** pitcher forecasts resolved 4; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
