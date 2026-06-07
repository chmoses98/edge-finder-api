# RULES_INDEX.md — Rule Cross-Reference
# Last updated: June 6, 2026 — v1.0
# DO NOT edit rule text here. This is a lookup index only.
# Rule text lives in RULES.md. Rule numbers are canonical and never resequenced.

| Rule | Topic | Tier | Gate type | Location in RULES.md |
|------|-------|------|-----------|----------------------|
| 1 | Under with elite offense — take RL or TT instead | — | Guidance | Core Betting Rules |
| 2 | NRFI requires elite starters on BOTH sides | — | Guidance | Core Betting Rules |
| 3 | true_xFIP from last 5 starts — no season xFIP alone | — | Required | Core Betting Rules |
| 4 | Same-day starter confirmation mandatory | T1 | Hard gate | Core Betting Rules |
| 5 | Skip ATL -1.5 RL — ML or TT instead | — | Guidance | Core Betting Rules |
| 6 | TJS return pitchers: do not assume rustiness | — | Guidance | Core Betting Rules |
| 7 | YRFI: requires specific 1st-inning pattern evidence | — | Required | Core Betting Rules |
| 8 | K props: two-sided matchup analysis required | — | Required | Core Betting Rules |
| 9 | Kalshi is primary bet source and edge target; YES = away | — | Architecture | Core Betting Rules |
| 10 | Pitcher prop bets: same-day starter confirmation | T1 | Hard gate | Core Betting Rules |
| 11 | Starter unconfirmed = Paper only for all props/F5 | T1 | Hard gate | Core Betting Rules |
| 12 | Team streak = noise; underlying metrics primary | — | Guidance | Core Betting Rules |
| 13 | ATL TT Over: requires specific game-day reason | — | Required | Core Betting Rules |
| 14 | Pinnacle doubleheader line gaps >15% = likely data error | — | Validation | Core Betting Rules |
| 15 | Rain = postponement risk only; never scoring suppressor | — | Guidance | Core Betting Rules |
| 16 | K rate is primary for total suppression | — | Guidance | Core Betting Rules |
| 17 | Elite starter Under lean — overridden by top-5 offense + weak opp starter | T1 | Hard gate | Core Betting Rules |
| 18 | TT Over: requires recent offensive form (last 7-game) | — | Required | Core Betting Rules |
| 19 | High BB/9 (>3.5) = T1 for K props; T3 for other markets | T1/T3 | Hard/Scalar | Core Betting Rules |
| 20 | Home/road splits mandatory before ML or F5 on pitcher | — | Required | Core Betting Rules |
| 21 | Medium bet cap: 10/session during losing streak, $35 max | — | Sizing | Core Betting Rules |
| 22 | ML within 15 cents of pick'em = extra-inning risk | T2 | Soft gate | Core Betting Rules |
| 23 | IL returners: regress toward prior-season quality faster | — | Guidance | Core Betting Rules |
| 24 | Opener (<3 IP avg): F5/K props UNQUALIFIED | T1 | Hard gate | Core Betting Rules |
| 25 | F5 ML mandatory every game with confirmed starters | T1 | Required | Core Betting Rules |
| 26 | RL must be evaluated independently every game | — | Required | Core Betting Rules |
| 27 | Game total Over valid even with elite starter (conditions) | T1 | Hard gate | Core Betting Rules |
| 28 | Model gap vs Pinnacle VF >10%: flag and reduce size | T3 | Scalar | Core Betting Rules |
| 29 | F5 line verification required before Medium/High | T1 | Hard gate | Core Betting Rules |
| 30 | Under at High: verify neither offense is top-5 R/G | T1 | Hard gate | Core Betting Rules |
| 31 | Opener on either side = Under suspect on game totals | T1 | Hard gate | Core Betting Rules |
| 32 | Same-game thesis conflict check (ML vs Total Under) | T2 | Soft gate | Core Betting Rules |
| 33 | Never buy ML juice above -195 when RL available at plus money | T1 | Hard gate | Core Betting Rules |
| 34 | NRFI blocked when game total ≥8.0 | T1 | Hard gate | Core Betting Rules |
| 35 | Prior-day 7+ runs: require K/9 >9 AND BB/9 <3.0 for Under | T2 | Soft gate | Core Betting Rules |
| 36 | YRFI: do not log based solely on low K% for elite starters | T1 | Hard gate | Core Betting Rules |
| 37 | Kalshi divergence >15%: investigate only when Pinnacle confirms | T2 | Soft gate | Core Betting Rules |
| 38 | Season run differential: background context only | — | Guidance | Core Betting Rules |
| 39 | Lopsided matchup Over belongs in TT, not game total | T1 | Hard gate | Core Betting Rules |
| 40 | NRFI/YRFI requires four-factor composite | — | Required | Core Betting Rules |
| 41 | Streak: not standalone signal; max weight 0.1 | T1 | Hard gate | Core Betting Rules |
| 42 | F5 price verification: hard pre-log gate | T1 | Hard gate | Core Betting Rules |
| 43 | Per-tier calibration factors only; no flat factor | — | Required | Core Betting Rules |
| 44 | TT line must be confirmed before Medium/High | T1 | Hard gate | Core Betting Rules |
| 45 | Park factors: apply numerically with GB%/FB% modifier | — | Required | Core Betting Rules |
| 46 | xERAGap F5: strongest confirmed signal; prioritize | T3 | Scalar | Core Betting Rules |
| 47 | Bullpen: use specific tier label in factors{} | — | Required | Core Betting Rules |
| 48 | xFIP primary; xERA secondary context only | — | Architecture | Core Betting Rules |
| 49 | Handedness scalar required before K props and total projection | — | Required | Core Betting Rules |
| 50 | Lineup adjustment required before TT Medium/High | T1 | Hard gate | Core Betting Rules |
| 51 | Projected runs must be shown in every game analysis | — | Required | Core Betting Rules |
| 52 | true probability from Poisson first; Kalshi is comparison | — | Architecture | Core Betting Rules |
| 53 | CLV: pull via web search at settlement; never estimate | T1 | Hard gate | Core Betting Rules |
| 54 | Compute Poisson live for edges near tier threshold | — | Required | Core Betting Rules |
| 55 | Record betTimeLine at bet-log time | — | Required | Core Betting Rules |
| 56 | F5 projection: use 5/8.5 ratio, not 5/9 | — | Required | Core Betting Rules |
| 57 | Park factors: GB%/FB% modifier for Coors/GABP/Dodger | — | Required | Core Betting Rules |
| 58 | Calibration factors: do not update until N≥50 per tier | — | Required | Core Betting Rules |
| 59 | Kalshi is primary market; Pinnacle is sanity check | — | Architecture | Core Betting Rules |
| 60 | Factor label standardization: no pitcher-specific keys | — | Required | Core Betting Rules |
| 61 | Every qualifying bet requires full written analysis | T1 | Hard gate | Signal Priority / What Does Well |
| 62 | Game total Under ≤8.0: cap at Medium; ≤7.5 cap at Paper | T1 | Hard gate | Signal Priority / What Does Well |
| 63 | Under buffer: <1.0 = Paper; 1.0–1.49 = Medium max; ≥1.5 = High eligible | T2 | Soft gate | Signal Priority / What Does Well |
| 64 | Three-layer analysis framework required for all Total/TT bets | T2 | Soft gate | Signal Priority / What Does Well |
| 65 | Rolling 15-game R/G = primary offense input for Total/TT | — | Required | Signal Priority / What Does Well |
| 66 | Bullpen availability check required before Under Medium/High | — | Required | Signal Priority / What Does Well |
| 67 | Every market evaluated must appear with edge or rejection reason | T1 | Hard gate | Signal Priority / What Does Well |
| 68 | Streak weight assignment defaults | T1 | Hard gate | Signal Priority / What Does Well |
| 69 | F5 edge threshold 1.0% when f5Amplified=True and xERAGap ≥1.5 | T3 | Scalar | Signal Priority / What Does Well |
| 70 | Edge >12% at near-even ML price: mandatory model review | T2 | Soft gate | Signal Priority / What Does Well |
| 71 | Model% diverges from Pinnacle VF >8%: BLOCKED until explained | T1 | Hard gate | Signal Priority / What Does Well |
| 72 | Suspended/postponed games: void handling | — | Settlement | (After Rule 60 block) |
| 73 | Both starters must appear in factors{} for ML/RL/F5 | T1 | Hard gate | (After Rule 71 block) |
| 74 | Pinnacle line moves 10+ cents against bet: soft gate | T2 | Soft gate | (After Rule 73 block) |
| 75 | edgePct must reflect calibration-adjusted edge | — | Required | (After Rule 74 block) |
| 76 | Multiple bets same game: correlated stack prohibited at full size | T1 | Hard gate | (After Rule 75 block) |
| 77 | Opposite-side bets same game same market: prohibited | T1 | Hard gate | (After Rule 76 block) |
| 78 | Team Totals: edge ≥2.0% calibrated for real money | T2 | Soft gate | (After Rule 77 block) |
| 79 | Session output: full analysis first pass, no abbreviated output | T1 | Hard gate | (After Rule 78 block) |
| 80 | Written thesis mandatory before GitHub push | T1 | Hard gate | (After Rule 79 block) |
| 81 | Run Line: Paper-only until WR ≥48% N≥20 AND CLV ≥0% N≥15 | T1 | Hard gate | (After Rule 80 block) |
| 82 | Same-day pitcher scratch gate: mandatory pre-place check | T1 | Hard gate | (After Rule 81 block) |
| 83 | ML bet sizing capped at 1.0x multiplier until CLV positive N≥30 | T1 | Hard gate | (After Rule 82 block) |

---

## Gate tier summary

| Tier | Count | Effect |
|------|-------|--------|
| T1 Hard gate | 28 rules | Auto-block; no override |
| T2 Soft gate | 10 rules | One failure = downgrade one tier; two = block at Medium |
| T3 Scalar | 4 rules | Affects size only, not log/no-log |
| Required/Guidance | 41 rules | Procedural; violations logged as model failures |

## Quick reference: most commonly triggered gates

| Scenario | Rules to check |
|----------|---------------|
| Logging any Under | 17, 22, 27, 30, 31, 32, 35, 62, 63, 64, 65, 66 |
| Logging any F5 | 24, 25, 29, 42, 46, 56, 69, 73 |
| Logging any ML | 4, 11, 20, 33, 41, 61, 67, 70, 71, 73, 77, 80, 83 |
| Logging any TT | 18, 44, 50, 64, 65, 66, 67, 78 |
| Logging any NRFI/YRFI | 2, 7, 34, 36, 40 |
| Multi-bet same game | 76, 77 |
| Settlement | 53, 55, 72, 74, 75 |
