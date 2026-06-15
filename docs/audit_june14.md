# Audit: June 14, 2026 — Block Rule Analysis & Opportunity Table

Generated: 2026-06-14 (retroactive analysis)
Analyst: automated audit script
HEAD at audit time: 5543192

---

## 1. Block Rule Audit Table

| # | Rule | What It Blocks | Markets Affected | Current Behavior | Proposed Class | Hard/Soft/Probe | June 14 Blocked Count | CLV Available | Protected or Opportunity Cost | Recommended Action |
|---|------|---------------|-----------------|-----------------|---------------|----------------|----------------------|--------------|-------------------------------|-------------------|
| R11 | starter_unconfirmed | All props/F5 when starter not confirmed | F5 ML, K Props | Hard block → Paper | MARKET_MECHANICS_HARD | Hard | 0 (starters confirmed June 14) | N/A | Protection — prevents bad data bets | Keep hard; add REAL_PROBE eligible when pitcher/price/identity clean |
| R24 | opener_role | F5 ML blocked when starter avg <3 IP/start and no 1st-inning xERA | F5 ML, K Props | Hard block → Skip/Paper | MARKET_MECHANICS_HARD | Hard | 0 (no openers June 14) | N/A | Protection | Keep hard |
| R27/R30 | top5_offense_under | Under blocked at High when offense R/G ≥5.2 season or ≥5.5 rolling15 | Game Total Under | Hard block at High tier | DATA_HARD | Hard | 0 | N/A | Protection against model overfit | Keep hard |
| R31 | opener_under | Under suspect when opener on either side | Game Total Under | Soft flag | RISK_SOFT | Soft | 0 | N/A | Context | Keep soft |
| R33 | under_200_ml | ML blocked above -195 juice when RL available plus-money | ML | Hard redirect to RL | MARKET_MECHANICS_HARD | Hard | 0 | N/A | Protection | Keep hard |
| R34 | nrfi_high_total | NRFI blocked when game total ≥8.0 (unless dual sub-3.00 1st-inn xERA) | NRFI | Hard block | CALIBRATION | Hard | Unknown — no NRFI logged June 14 | No | Opportunity cost unclear without data | Keep hard; add tracking for blocked NRFI |
| R40 | nrfi_yrfi_composite | YRFI/NRFI requires 4-factor composite (1st-inn xERA, 1st-inn form, 1st-inn run rate, park/lineup) | YRFI, NRFI | Hard gate → Paper | CALIBRATION | Soft (become probe-eligible) | 15 YRFI gated to Paper (Rule 40/52) | No — no Kalshi YRFI tickers logged | Opportunity cost: YRFI went 8W 6L 1VOID from Paper on June 14 (57% WR at Paper) | Reclassify as CALIBRATION; add first-inning-specific inputs; make probe-eligible |
| R41 | no_solo_streak_bet | Streak as sole signal blocks any bet | All | Hard block | RISK_SOFT | Hard | 0 | N/A | Protection | Keep hard |
| R42/R29 | f5_price_unconfirmed | F5 bet Paper only if Kalshi F5 line not confirmed | F5 ML | Hard gate → Paper | MARKET_MECHANICS_HARD | Hard→Probe | 5 F5 logged MODEL_ONLY (price not confirmed for real) | CLV UNAVAILABLE for all 5 | Cost: 4 of 5 won (80% WR), no CLV tracked | Change to REAL_PROBE eligible; require exact ticker + valid price |
| R44 | tt_line_unconfirmed | TT bet Paper only if TT line not confirmed | Team Total | Hard gate → Paper | MARKET_MECHANICS_HARD | Hard | 14 TT logged Paper | No Kalshi TT tickers | Mixed: 8W 6L (57% WR) | Change to REAL_PROBE when ticker + price valid |
| R50 | tt_lineup_unconfirmed | TT bet Paper only if lineup not confirmed | Team Total | Hard gate → Paper | MARKET_MECHANICS_HARD | Hard | Overlaps with R44 | No | Protective but costly when lineup clear | Merge with R44; use CALIBRATION class |
| R51 (inferred) | ml_lineup_gate | ML blocked/Paper when lineup not confirmed | ML | Hard gate → Paper | RISK_SOFT | Soft→Probe | 3 ML bets logged Paper (Rule 51) | No Kalshi ML tickers | Cost: all 3 won (CWS, COL, TB ML all WIN) | Reclassify RISK_SOFT; make ML probe-eligible when pitcher+price+identity clean |
| R52 (inferred) | yrfi_composite_gate | YRFI requires full composite before real-money | YRFI | Hard gate → Paper | CALIBRATION | Soft→Probe | 15 YRFI gated (Rule 40/52) | No | 57% WR at Paper | Make probe-eligible after first-inning inputs validated |
| R66 | bullpen_fatigued | Soft downgrade when team bullpen threw 5+ IP last 2 days | All markets | T2 soft gate | RISK_SOFT | Soft | Unknown | N/A | Risk management | Keep soft |
| R70 | thin_sample_starter | Soft downgrade when starter <4 GS | All markets | T2 soft gate | CALIBRATION | Soft | Unknown | N/A | Calibration protection | Keep soft |
| R71 | rule71_unexplained_gap | ML blocked when model vs Pinnacle VF >8% unexplained | ML | Hard block | DATA_HARD | Hard | Unknown — no Rule71 fires noted June 14 | N/A | Protection against data errors | Keep hard |
| R71_F5 | f5_rule71_gap | F5 ML blocked when model vs Kalshi F5 VF >12% unexplained | F5 ML | Hard block | DATA_HARD | Hard | 0 (June 14 F5 gaps within limits) | N/A | Protection | Keep hard |
| R71_TOTAL | game_total_paper | Game Total Over/Under Paper only until WR≥52% N≥30 | Game Total | Hard block → Paper only | OPPORTUNITY_FILTER | Hard (keep) | All game totals June 14 would be Paper | No | WR 41% historically — correct block | Keep until promotion criteria met; track blocked bets |
| R81 | rl_suspended | All RL bets Paper until WR≥48% N≥20 AND avg CLV≥0% | Run Line | Hard block → Paper | CALIBRATION | Hard (keep) | 0 (no RL June 14) | N/A | Correct suspension given -4.09% avg CLV | Keep suspended |

### Block Class Definitions

- **DATA_HARD**: Missing/invalid input data that makes model output unreliable. Must block all classifications.
- **MARKET_MECHANICS_HARD**: Market structure prevents safe placement (no ticker, stale price, unconfirmed line). Hard block for REAL; can be REAL_PROBE eligible if ticker + price valid.
- **RISK_SOFT**: Risk management rule. Downgrades tier but does not block entirely. Probe-eligible.
- **CALIBRATION**: Model performance insufficient to risk real money. Paper/probe-eligible with tracking.
- **OPPORTUNITY_FILTER**: Market suspended due to negative historical performance. Track all blocked bets.

---

## 2. June 14 Blocked-Opportunity Table

| Game | Market | Side | Ticker | Block Rule | Block Class | trackingType | Result | CLV Status | Notes |
|------|--------|------|--------|-----------|-------------|-------------|--------|-----------|-------|
| MIA@PIT | F5 ML | MIA Away | KXMLBF5-26JUN141215MIAPIT-MIA | Rule 42 (F5 price/unconfirmed) | MARKET_MECHANICS_HARD | MODEL_ONLY | WIN | UNAVAILABLE | No Kalshi price confirmed; paper only |
| NYY@TOR | F5 ML | NYY Away | KXMLBF5-26JUN141337NYYTOR-NYY | Rule 42 (F5 price/unconfirmed) | MODEL_ONLY | LOSS | UNAVAILABLE | F5 tied 2-2 → graded LOSS; ticker exists |
| COL@ATH | F5 ML | COL Away | KXMLBF5-26JUN141505COLATH-COL | Rule 42 (F5 price/unconfirmed) | MODEL_ONLY | WIN | UNAVAILABLE | Ticker exists; price unconfirmed |
| CHC@SF | F5 ML | SF Home | KXMLBF5-26JUN141510CHCSF-SF | Rule 42 (F5 price/unconfirmed) | MODEL_ONLY | WIN | UNAVAILABLE | Ticker exists |
| TB@LAA | F5 ML | TB Away | KXMLBF5-26JUN141607TBLAA-TB | Rule 42 (F5 price/unconfirmed) | MODEL_ONLY | WIN | UNAVAILABLE | Boxscore-anchored F5 |
| MIA@PIT | YRFI | YRFI | None | Rule 40/52 (composite gate) | CALIBRATION | PAPER | LOSS | UNAVAILABLE | No Kalshi YRFI ticker |
| SD@BAL | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | LOSS | UNAVAILABLE | — |
| SEA@WSH | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | WIN | UNAVAILABLE | — |
| NYY@TOR | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | LOSS | UNAVAILABLE | — |
| AZ@CIN | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | WIN | UNAVAILABLE | — |
| DET@CLE | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | VOID | UNAVAILABLE | Game postponed → VOID correct |
| ATL@NYM | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | WIN | UNAVAILABLE | — |
| HOU@KC | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | WIN | UNAVAILABLE | — |
| STL@MIN | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | LOSS | UNAVAILABLE | — |
| LAD@CWS | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | WIN | UNAVAILABLE | — |
| PHI@MIL | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | WIN | UNAVAILABLE | — |
| COL@ATH | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | WIN | UNAVAILABLE | — |
| CHC@SF | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | LOSS | UNAVAILABLE | — |
| TB@LAA | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | LOSS | UNAVAILABLE | — |
| TEX@BOS | YRFI | YRFI | None | Rule 40/52 | CALIBRATION | PAPER | WIN | UNAVAILABLE | — |
| NYY@TOR | Team Total | TOR Over 4 | None | Rule 44/50 (lineup/TT unconfirmed) | MARKET_MECHANICS_HARD | PAPER | LOSS | UNAVAILABLE | — |
| HOU@KC | Team Total | HOU Over 5 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | LOSS | UNAVAILABLE | — |
| STL@MIN | Team Total | STL Over 5 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | LOSS | UNAVAILABLE | — |
| LAD@CWS | Team Total | CWS Over 4 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | WIN | UNAVAILABLE | — |
| PHI@MIL | Team Total | PHI Over 4 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | LOSS | UNAVAILABLE | — |
| COL@ATH | Team Total | COL Over 6 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | WIN | UNAVAILABLE | — |
| COL@ATH | Team Total | ATH Over 8 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | WIN | UNAVAILABLE | — |
| CHC@SF | Team Total | CHC Over 4 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | LOSS | UNAVAILABLE | — |
| CHC@SF | Team Total | SF Over 4 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | WIN | UNAVAILABLE | — |
| TB@LAA | Team Total | TB Over 5 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | WIN | UNAVAILABLE | — |
| TB@LAA | Team Total | LAA Over 5 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | LOSS | UNAVAILABLE | — |
| TEX@BOS | Team Total | TEX Over 5 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | WIN | UNAVAILABLE | — |
| TEX@BOS | Team Total | BOS Over 5 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | LOSS | UNAVAILABLE | — |
| DET@CLE | Team Total | DET Over 4 | None | Rule 44/50 | MARKET_MECHANICS_HARD | PAPER | VOID | UNAVAILABLE | Postponed → VOID correct |
| LAD@CWS | ML | CWS ML | None | Rule 51 (ML lineup gate) | RISK_SOFT | PAPER | WIN | UNAVAILABLE | Rule 51 gated; lineup incomplete |
| COL@ATH | ML | COL ML | None | Rule 51 | RISK_SOFT | PAPER | WIN | UNAVAILABLE | Rule 51 gated; lineup incomplete |
| TB@LAA | ML | TB ML | None | Rule 51 | RISK_SOFT | PAPER | WIN | UNAVAILABLE | Rule 51 gated; lineup incomplete |

**Summary of June 14 blocked opportunities:**
- YRFI: 15 Paper, 8W 6L 1VOID (57% WR exclud void), opportunity cost if real
- Team Total: 14 Paper, 8W 5L 1VOID (62% WR excl void), opportunity cost
- F5 ML (MODEL_ONLY): 5, 4W 1L (80% WR), opportunity cost
- ML: 3 Paper, 3W 0L (100% WR), small sample but notable
- **No CLV was captured for any blocked bet** — critical gap

---

## 3. Market-Specific Findings

### Moneyline (ML)
- **Rules gating:** Rule 51 (lineup gate) gated 3 ML bets to Paper on June 14
- **Passed as REAL:** ATL F5 ML (REAL, LOSS)
- **Failures:** No Rule 71 fires. No juice cap fires.
- **Recommendations:** Reclassify Rule 51 as RISK_SOFT; make ML probe-eligible when pitcher/price/identity clean and lineup partially confirmed
- **CLV:** All 3 Paper ML = UNAVAILABLE. The REAL ATL F5 ML CLV = UNAVAILABLE.

### F5 ML
- **Rules gating:** Rule 42 (price unconfirmed) → 5 MODEL_ONLY
- **Passed as REAL:** ATL F5 ML Away (REAL, LOSS $4.50)
- **Performance:** MODEL_ONLY F5 = 4W 1L (80% WR), all CLV UNAVAILABLE
- **Issue:** F5 multiplier (1.5×) causing MODEL_ONLY bets to show stake of $1.50 — must ensure F5 multiplier does not inflate paper bets to look real
- **F5 tie settlement:** NYY F5 = 2-2 (tie) → graded LOSS. Notes confirm this. Correct per Kalshi rules.
- **Recommendations:** Implement linescore API for F5 settlement; NYY@TOR tied 2-2 → LOSS is correct

### YRFI/NRFI
- **Rules gating:** Rule 40/52 composite gate → ALL 15 YRFI bets gated to Paper
- **YRFI Performance June 14:** 8W 6L 1VOID = 57% WR (excl void)
- **Critical finding:** YRFI composite currently allows bullpen/full-game factors. Need first-inning-specific inputs only.
- **DET@CLE YRFI:** VOID (postponed) — correct
- **No NRFI logged June 14** — no qualifying games or blocked by composite gate
- **Recommendations:** Add first-inning-specific lambda tracking; make YRFI probe-eligible when all first-inning inputs valid

### Team Totals
- **Rules gating:** Rule 44 (TT line unconfirmed) + Rule 50 (lineup unconfirmed) → 14 TT bets Paper
- **Performance:** 8W 5L 1VOID = 62% WR (excl void)
- **MIL TT Real Win:** PHI@MIL MIL TT Home Over 3 — REAL, WIN. The one REAL TT bet hit.
- **Recommendations:** Keep in CALIBRATION class. Add Kalshi TT ticker fields. Track CLV for all blocked TT.

### Game Totals
- **Rules gating:** Rule 71 (WR 41%) → Paper only for all game totals
- **No game totals logged June 14** (not even as Paper tracking rows)
- **Critical gap:** Blocked game totals are not being tracked at all — cannot measure whether the block is still correct
- **Recommendations:** Add Paper tracking rows for all blocked game totals with market identity + CLV fields

### DET@CLE — Postponement
- **Status:** VOID correctly applied to DET YRFI and DET TT tracking rows
- **Gap:** No formal postponed/cancelled guard in slate generation — VOID was applied manually
- **Recommendations:** Add automated postponed game guard in pregame check

### NYY@TOR F5 Settlement
- **Score:** F5 ended 2-2 (tied)
- **Graded:** LOSS (notes confirm: "F5 ended tied 2-2. Graded LOSS")
- **Settlement source:** Noted as "Boxscore-anchored" — not from linescore API
- **RBI issue:** TB@LAA notes mention "Boxscore-anchored F5 settlement" and "RBI discrepancy"
- **Recommendations:** Implement linescore API primary source for F5 settlement; crosscheck RBI

---

## 4. Key Gaps Identified

1. **CLV infrastructure missing** — No pregame Kalshi snapshots captured for June 14. All bets show clvStatus=UNAVAILABLE.
2. **Authoritative slate not versioned** — data/slate.json is overwritten on reruns
3. **Sentinel price validation missing** — No guard against 19900/-19900 etc.
4. **Postponed game automation missing** — DET@CLE void applied manually
5. **F5 settlement source** — Using boxscore/RBI, not linescore API
6. **YRFI inputs** — Composite gate may include bullpen/full-game factors (Rule 40 references "all 4 factors" but does not explicitly exclude bullpen)
7. **Game totals not tracked when blocked** — OPPORTUNITY_FILTER blocks leave no tracking rows
8. **Rule 51 (ML lineup gate)** — 3 wins missed; should be RISK_SOFT not implied hard block
9. **REAL_PROBE lane missing** — No mechanism for controlled real-money probe bets
10. **trackingType enforcement** — betSize=1.50 for MODEL_ONLY F5 bets creates confusion (F5 multiplier 1.5× applied to Paper tier $1.00 base)
