## MLB projection scorecard (daily) — last 7 days

Generated 2026-10-29T22:02:09Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 1372 across 7 games. Settled: 1157 (void/DNP 6, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| batters_faced | incumbent_pitcher_champion (incumbent) | 9 (5) | 2.781 | +2.166 | — | — |  |
| batters_faced | pf-mlb:E (challenger) | 10 (5) | 1.674 | +0.710 | 1.295 | 100% | — |
| game_total | incumbent_team_runs (incumbent) | 4 (4) | 2.218 | -0.279 | — | — |  |
| game_total | pf-mlb:D_convolution (challenger) | 5 (5) | 2.316 | +0.756 | 1.486 | 100% | — |
| hits | incumbent_hitter_engine (incumbent) | 22 (2) | 0.678 | +0.072 | — | — |  |
| hits | pf-mlb:F (challenger) | 90 (5) | 0.729 | -0.025 | 0.472 | 77% | — |
| hits_runs_rbis | incumbent_hitter_engine (incumbent) | 22 (2) | 1.134 | +0.130 | — | — |  |
| hits_runs_rbis | pf-mlb:F (challenger) | 90 (5) | 1.397 | +0.083 | 0.882 | 86% | — |
| home_runs | pf-mlb:F (challenger) | 90 (5) | 0.147 | +0.065 | 0.050 | 82% | — |
| outs | incumbent_pitcher_champion (incumbent) | 9 (5) | 2.706 | +1.509 | — | — |  |
| outs | pf-mlb:F (challenger) | 10 (5) | 2.394 | +0.874 | 1.770 | 80% | — |
| pitcher_strikeouts | incumbent_pitcher_champion (incumbent) | 9 (5) | 1.620 | +0.965 | — | — |  |
| pitcher_strikeouts | pf-mlb:F (challenger) | 10 (5) | 1.544 | +1.031 | 1.006 | 80% | — |
| plate_appearances | pf-mlb:F (challenger) | 90 (5) | 0.622 | -0.045 | 0.404 | 74% | — |
| rbis | incumbent_hitter_engine (incumbent) | 21 (2) | 0.406 | +0.059 | — | — |  |
| rbis | pf-mlb:F (challenger) | 90 (5) | 0.536 | +0.029 | 0.287 | 86% | — |
| runs | pf-mlb:F (challenger) | 90 (5) | 0.477 | +0.076 | 0.235 | 84% | — |
| team_runs | incumbent_team_runs (incumbent) | 8 (4) | 1.941 | -0.139 | — | — |  |
| team_runs | pf-mlb:D (challenger) | 10 (5) | 1.736 | +0.378 | 1.271 | 90% | — |
| total_bases | incumbent_hitter_engine (incumbent) | 22 (2) | 0.860 | +0.327 | — | — |  |
| total_bases | pf-mlb:F (challenger) | 90 (5) | 1.136 | +0.130 | 0.723 | 81% | — |

**Drift checks**

- batters_faced: **INSUFFICIENT** — 10 settled forecasts (< 100)
- game_total: **INSUFFICIENT** — 5 settled forecasts (< 100)
- hits: **INSUFFICIENT** — 90 settled forecasts (< 100)
- hits_runs_rbis: **INSUFFICIENT** — 90 settled forecasts (< 100)
- home_runs: **INSUFFICIENT** — 90 settled forecasts (< 100)
- outs: **INSUFFICIENT** — 10 settled forecasts (< 100)
- pitcher_strikeouts: **INSUFFICIENT** — 10 settled forecasts (< 100)
- plate_appearances: **INSUFFICIENT** — 90 settled forecasts (< 100)
- rbis: **INSUFFICIENT** — 90 settled forecasts (< 100)
- runs: **INSUFFICIENT** — 90 settled forecasts (< 100)
- team_runs: **INSUFFICIENT** — 10 settled forecasts (< 100)
- total_bases: **INSUFFICIENT** — 90 settled forecasts (< 100)

**Segment checks (priority pitcher families; MAE / bias per model role)**

_batters_faced_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 10 | 1.674 | 0.71 | 9 | 2.7805 |
| short_rest(<=4d) | 2 | 2.0887 | 0.8263 | 1 | 7.8333 |
| normal_rest(>4d) | 8 | 1.5703 | 0.681 | 8 | 2.1489 |
| early_exit(actual BF<=12) | 1 | 1.2624 | -1.2624 | 0 | — |
| went_deeper(actual BF>12) | 9 | 1.7197 | 0.9292 | 9 | 2.7805 |
| starter_CONFIRMED | 10 | 1.674 | 0.71 | 9 | 2.7805 |

_outs_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 10 | 2.3943 | 0.8742 | 9 | 2.7064 |
| short_rest(<=4d) | 2 | 1.3263 | 1.3263 | 1 | 4.5041 |
| normal_rest(>4d) | 8 | 2.6612 | 0.7612 | 8 | 2.4817 |
| early_exit(actual BF<=12) | 1 | 0.2925 | 0.2925 | 0 | — |
| went_deeper(actual BF>12) | 9 | 2.6278 | 0.9389 | 9 | 2.7064 |
| starter_CONFIRMED | 10 | 2.3943 | 0.8742 | 9 | 2.7064 |

_pitcher_strikeouts_

| Segment | challenger n | challenger MAE | challenger bias | incumbent n | incumbent MAE |
|---|---|---|---|---|---|
| opener_flag=0 | 10 | 1.5438 | 1.0311 | 9 | 1.6199 |
| short_rest(<=4d) | 2 | 0.8843 | 0.8843 | 1 | 1.6266 |
| normal_rest(>4d) | 8 | 1.7087 | 1.0678 | 8 | 1.619 |
| early_exit(actual BF<=12) | 1 | 0.0362 | 0.0362 | 0 | — |
| went_deeper(actual BF>12) | 9 | 1.7113 | 1.1417 | 9 | 1.6199 |
| starter_CONFIRMED | 10 | 1.5438 | 1.0311 | 9 | 1.6199 |

**Participation:** pitcher forecasts resolved 16; starter did not appear 1; starter relief appearance 1; unforecast actual starters 0; hitter forecasts void 0
