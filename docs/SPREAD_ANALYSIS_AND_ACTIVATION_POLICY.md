# SPREAD_ANALYSIS_AND_ACTIVATION_POLICY.md

Spread/F3-F7-correction mission -- separates spread-market ANALYSIS
(discovery, classification, modeling, ranking, paper tracking, CLV
tracking, settlement) from REAL-MONEY EXECUTION ELIGIBILITY, and
documents the (currently unmet) standard a spread market would need to
clear before real-money activation is proposed.

**No production probability, calibration factor, bet-sizing rule,
bankroll rule, or recommendation threshold changed. Rule 81 is
unmodified in `scripts/build_market_ledger.py` -- it still
unconditionally rejects `RL_Away`/`RL_Home` before any `modelProb` is
computed there, exactly as before this mission.**

## 1. The distinction this mission enforces

The universal Kalshi MLB market engine
(`scripts/discover_kalshi_mlb_markets.py`) now attaches SEVEN
independent status fields to every discovered contract, instead of the
single `modelSupportStatus` field it had before:

| Field | What it answers |
|---|---|
| `analysisStatus` | Was the contract parsed and classified at all? |
| `modelSupportStatus` | Can a fair probability be computed for this exact contract? |
| `paperTrackingStatus` | Is it eligible for the paper ledger (`data/research/paper_spread_ledger.jsonl`)? |
| `clvTrackingStatus` | Can a closing line be captured against it (real game/ticker identity resolved)? |
| `settlementSupportStatus` | Can this contract be settled from a final/period score? |
| `realMoneyEligibilityStatus` | Can real money be risked on it right now? |
| `realMoneyBlockReasons` | If blocked, exactly why. |

A Rule-81-blocked full-game spread contract is `analysisStatus:
ANALYZED`, `modelSupportStatus: SUPPORTED`, `paperTrackingStatus:
ELIGIBLE`, `clvTrackingStatus: ELIGIBLE`, `settlementSupportStatus:
SUPPORTED`, and ONLY `realMoneyEligibilityStatus: BLOCKED`. Rule 81
blocks execution, never analysis.

## 2. Spread real-money block reasons

| Period | `realMoneyBlockReasons` | Rationale |
|---|---|---|
| Full-game (`winning_margin`, `period=full_game`) | `RULE_81` | Mirrors `scripts/build_market_ledger.py`'s existing, unmodified Rule 81 suspension (WR 36%, CLV -4.09% on the historical sample that triggered it). |
| F3/F5/F7 spread | `NOT_YET_ACTIVATED_NO_HISTORICAL_PAPER_SAMPLE` | These periods have never been priced in production at all -- there is no historical sample to invoke Rule 81's specific numbers against. They are new capability added by this mission, paper-only from day one. |

Both reasons are DISTINCT and intentionally not conflated: Rule 81 is
an authoritative, numerically-justified production suspension; the
F3/F5/F7 reason is simply "never turned on yet, no data exists."

## 3. Paper tracking mechanism

`scripts/build_paper_spread_ledger.py` builds one row per spread
contract that is `modelSupportStatus: SUPPORTED`, real-money `BLOCKED`,
and clears the SAME minimum-edge floor (`THRESHOLD_PAPER`, imported
from `scripts/build_market_ledger.py`, never reimplemented) production
uses to decide whether ANY market is worth acting on at all. This is
the hypothetical-wager population -- a spread contract below the floor
is still visible/ranked in the discovery artifact but is not a paper
wager, mirroring how a below-floor `Rejected` row never reaches
`bets.json` either.

Rows are appended (idempotently, keyed by `(date, ticker)`) to
`data/research/paper_spread_ledger.jsonl` at ANALYSIS TIME, since the
fair probability/executable ask/edge at that moment can never be
reconstructed once the market closes. Settlement
(`settle_paper_spread_row()`) is a separate, pure function applied once
a final score is available -- this mission intentionally does not wire
it to an automated live score fetch (unlike `lib/f5_settlement.py`,
this repository's own real-bet settlement into `bets.json` is
human-curated via `BET_LOG.md`, not a fully-automated pipeline; inventing
a new automated settlement path for paper-only data would be a second,
inconsistent pattern rather than reuse of an existing one). Wiring
`settle_paper_spread_row()` to a live score source is documented,
tested, and ready -- it is a fast-follow, not a gap papered over as done.

## 4. Research database integration

`scripts/build_wager_research_db.py` now ingests the paper ledger
alongside `bets.json`, converting each paper row into the SAME
canonical schema real wagers use via `build_paper_row()`, tagged
`trackingType: "PAPER"`, `countsTowardBankroll: false`. A paper row's
profit/loss lives ONLY in `hypotheticalNetProfit`/`hypotheticalRoiPct`
-- `stake`/`netProfit`/`roiPct` are always `null` on a paper row by
construction, so no code path can accidentally blend hypothetical
paper profit into real bankroll ROI.

`scripts/generate_wager_research_report.py`'s `allTime`/
`last7SettledBettingDays`/`last30SettledBettingDays`/`currentSeason`
blocks are computed from REAL/MANUAL rows only. Paper spread
performance is reported in a separate, clearly-labeled
`paperSpreadPerformance` section (both in the daily and summary
reports), with its own sample size, record, hypothetical ROI, and CLV
breakdowns by period/favorite-underdog/line-type.

## 5. Real-money activation standard (proposed, NOT activated by this mission)

Rule 81 is the authoritative standard for the ONE market it already
governs (full-game spread) -- its own WR/CLV thresholds
(`WR>=48% N>=20 AND CLV>=0% N>=15`) remain the bar for full-game spread
reactivation and are unchanged by this mission.

No equivalent authoritative standard exists yet for F3/F5/F7 spread
(they have never been priced before). The following CONSERVATIVE
standard is proposed for when a future phase reviews activation --
this mission does **not** apply it automatically, and no single hot
streak should ever trigger activation on its own:

- Minimum 30 settled paper bets for the specific period+subfamily
  under review (full-game/F3/F5/F7 evaluated independently -- one
  period's sample never justifies another's activation).
- Positive ROI over that sample.
- Non-negative average ask CLV.
- Positive average midpoint CLV.
- Acceptable maximum drawdown (no fixed number proposed here --
  requires a human risk-tolerance decision at review time).
- No single subfamily (e.g. one specific alternate line, or one
  favorite/underdog split) accounting for a misleading majority of the
  sample's results -- a 30-bet sample dominated by 25 near-identical
  bets is not 30 independent data points.

## 6. What this mission tracks toward that standard

At minimum, for every settled paper spread wager:
- Settled paper sample size (overall and by period/favorite-underdog/
  line-type via `build_paper_breakdowns()`).
- Win rate (`record.wins`/`record.losses` in `summarize_paper()`).
- Hypothetical ROI (`hypotheticalRoiPct`).
- Average ask CLV / average midpoint CLV / positive CLV rate.
- Maximum drawdown is NOT yet computed by this mission (would require
  a running hypothetical-bankroll simulation across settled paper rows
  in date order) -- documented here as a known gap, not fabricated.

## 7. Explicitly not performed by this mission

No production probability, calibration, threshold, or bankroll rule
changed. Rule 81 not removed, weakened, or bypassed. No spread market
activated for real money. No automated settlement wired to a live
score feed (see section 3). No F3/F7 winner-market probability
computed (their outcome structure remains independently unverified --
see `docs/F3_F7_LIVE_DISCOVERY_REPORT.md`).
