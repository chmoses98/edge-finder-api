# MLB-RSCH-0031 -- Live Exposure / Recommendation Quality Audit

**RESEARCH ONLY. No production change. Nothing fitted. No probability produced.**

## A premise this audit corrects

Earlier reports -- including my own framing of MLB-RSCH-0028 and -0029 -- described hitter
props as *"~75% of recommendation volume"*. That figure counts archive **rows**, and those
rows are overwhelmingly `INSUFFICIENT_MODEL_SUPPORT`: the engine **declining** to recommend.

- Hitter rows in the archive: **62,722**
- Of those, declined: **62,720**
- Actually recommended: **0**
- User-confirmed hitter bets: **2**

**Real hitter recommendation exposure is essentially nil.** The urgency previously attached to
hitter-prop selection was misplaced -- the engine was already refusing that surface.

## Populations, never conflated

Archive rows: **84,359**  ·  latest archived date: **2026-08-29**

| Population | Definition |
|---|---|
| RECOMMENDED | status in ['RECOMMENDED', 'RECOMMENDED_NOT_BET'] |
| USER-CONFIRMED BET | status in ['BET_PLACED'] **and** `betPlaced == True` |
| DECLINED | status in ['INSUFFICIENT_MODEL_SUPPORT', 'NOT_EVALUATED', 'PASS_NO_EDGE', 'PASS_DATA_QUALITY', 'PASS_PRICE_TOO_HIGH'] |

A recommendation is never assumed to have become a bet. **No dollar figure is invented** --
the recommendation schema carries no stake field.

## Live exposure by window

| Window | Archive rows | Recommended | Confirmed bets | Live total | RED share |
|---|---:|---:|---:|---:|---:|
| FULL_2026 | 84,359 | 314 | 122 | 436 | 20.0% |
| LAST_30D | 84,359 | 314 | 122 | 436 | 20.0% |
| LAST_14D | 36,430 | 111 | 32 | 143 | 39.2% |
| LAST_7D | 26,124 | 92 | 32 | 124 | 42.7% |

## Exposure matrix -- where bankroll is actually pointed

| Family | Count | Share | Confirmed bets | Risk | Research status | Known defect |
|---|---:|---:|---:|:-:|---|---|
| KXMLBRFI | 139 | 31.9% | 2 | **YELLOW** | UNPROVEN | - |
| KXMLBTEAMTOTAL | 85 | 19.5% | 1 | **RED** | SEMANTIC_DEFECT, MODEL_TRAILS_MARKET | threshold stored as the ticker suffix integer, not the line (+0.5); p_over_total then shifts a further run via int(line)+1 |
| KXMLBF5 | 60 | 13.8% | 9 | **YELLOW** | INSUFFICIENT_SAMPLE | - |
| KXMLBGAME | 44 | 10.1% | 5 | **YELLOW** | INSUFFICIENT_SAMPLE | - |
| inning_result | 32 | 7.3% | 32 | **YELLOW** | UNASSESSED | RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope |
| pitcher_strikeouts | 20 | 4.6% | 20 | **YELLOW** | UNASSESSED | RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope |
| team_total | 19 | 4.4% | 19 | **YELLOW** | UNASSESSED | RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope |
| game_result | 12 | 2.8% | 12 | **YELLOW** | UNASSESSED | RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope |
| game_total | 10 | 2.3% | 10 | **YELLOW** | UNASSESSED | RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope |
| pitcher_outs | 6 | 1.4% | 6 | **YELLOW** | UNASSESSED | RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope |
| first_inning_run | 3 | 0.7% | 3 | **YELLOW** | UNASSESSED | RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope |
| hitter_hits | 2 | 0.5% | 2 | **YELLOW** | PARITY, EDGE_SIGNAL_UNTRUSTWORTHY | - |
| ML_Away | 2 | 0.5% | 0 | **RED** | MODEL_TRAILS_MARKET | - |
| winning_margin | 1 | 0.2% | 1 | **YELLOW** | UNASSESSED | RESEARCH_ONLY board pooled into audits before RSCH-0027 corrected the scope |
| UNLABELLED_FAMILY | 1 | 0.2% | 0 | **YELLOW** | UNASSESSED | missing marketFamily on the archived recommendation row |

## Risk concentration

- **GREEN** 0 (0.0%)
- **YELLOW** 349 (80.0%)
- **RED** 87 (20.0%)

Risk bands are **research communication only** and are not production settings.

## Team-total +0.5 threshold defect, on LIVE recommendations

Derivation: ticker suffix integer N -> line N-0.5; production's stored value is never used as the source of truth.

- Live team-total recommendations: **85**
- Audited (ticker parsed AND display numeral present): **61**
- Unparsed tickers: 0  ·  no usable thresholdDisplay: **24**
- **Mismatched: 61**  ·  rate: **1.0**


**thresholdDisplay is what the recommendation shows a human. A mismatch here means the recommendation names a line the contract does not settle on.**

| Ticker | Displayed threshold | Ticker-derived line | Date |
|---|---|---:|---|
| `KXMLBTEAMTOTAL-26AUG072140HOUSD-HOU4` | Team Total Over 4 | 3.5 | 2026-08-08 |
| `KXMLBTEAMTOTAL-26AUG072140HOUSD-SD4` | Team Total Over 4 | 3.5 | 2026-08-08 |
| `KXMLBTEAMTOTAL-26AUG072145TBSEA-TB4` | Team Total Over 4 | 3.5 | 2026-08-08 |
| `KXMLBTEAMTOTAL-26AUG072215DETSF-DET5` | Team Total Over 5 | 4.5 | 2026-08-08 |
| `KXMLBTEAMTOTAL-26AUG081505ATLNYY-ATL4` | Team Total Over 4 | 3.5 | 2026-08-09 |

## Hypothetical settled performance by evidence class

**Hypothetical only.** Recommendations are never assumed to have been placed.

| Band | Settled | Games | Wins | Net P/L | Net ROI | ROI CI (game-clustered) |
|---|---:|---:|---:|---:|---:|---|
| RED | 79 | 60 | 40 | -0.11 | -0.0029 | [-0.1847, 0.1718] |
| YELLOW | 210 | 129 | 97 | -4.184 | -0.0431 | [-0.1618, 0.0839] |

## RED counterfactual

RED membership comes from EVIDENCE_MAP only -- derived from merged experiment artifacts, never tuned to historical ROI.

- Volume removed: **87 rows (20.0%)**
- RED hypothetical net P/L: **-0.11** of -4.294 overall
- Share of hypothetical P/L removed: **0.0256**
- Remaining opportunity volume: **349**
- Remaining families: `{'KXMLBRFI': 139, 'KXMLBF5': 60, 'KXMLBGAME': 44, 'inning_result': 32, 'pitcher_strikeouts': 20, 'team_total': 19, 'game_result': 12, 'game_total': 10, 'pitcher_outs': 6, 'first_inning_run': 3, 'hitter_hits': 2, 'winning_margin': 1, 'UNLABELLED_FAMILY': 1}`

## User-confirmed wagers (execution exposure, not model validation)

- Count: **122**  ·  stake field available: False
- By family: `{'inning_result': 32, 'pitcher_strikeouts': 20, 'team_total': 19, 'game_result': 12, 'game_total': 10, 'KXMLBF5': 9, 'pitcher_outs': 6, 'KXMLBGAME': 5, 'first_inning_run': 3, 'KXMLBRFI': 2, 'hitter_hits': 2, 'winning_margin': 1, 'KXMLBTEAMTOTAL': 1}`
- By risk band: `{'YELLOW': 121, 'RED': 1}`
- By confidence: `{'None': 105, 'MEDIUM': 14, 'PAPER': 3}`

USER-CONFIRMED wagers only (betPlaced == True). Never inferred from recommendations; no ledger was read or written.

## Declared-edge distribution

| Segment | Rows | [-1.000,+0.025) | [+0.025,+0.050) | [+0.050,+0.075) | [+0.075,+0.100) | [+0.100,+0.150) | [+0.150,+1.010) |
|---|---:|---:|---:|---:|---:|---:|---:|
| ALL_LIVE | 436 | 1 | 0 | 0 | 0 | 0 | 3 |
| KXMLBTEAMTOTAL | 85 | 1 | 0 | 0 | 0 | 0 | 0 |
| KXMLBRFI | 139 | 0 | 0 | 0 | 0 | 0 | 1 |
| KXMLBF5 | 60 | 0 | 0 | 0 | 0 | 0 | 1 |
| KXMLBGAME | 44 | 0 | 0 | 0 | 0 | 0 | 1 |
| HITTER_FAMILIES | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| DECLINED_HITTER_ROWS | 62720 | 0 | 0 | 0 | 0 | 0 | 0 |

## Classification

**MODERATE_RESEARCH_RISK**

- Risk bands are production settings: False
- Production action authorized: False
