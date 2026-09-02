# 2026-09-02 -- PARTIAL / INTRADAY OBSERVATION (NOT A COMPLETED DAILY POSTMORTEM)

> **This is not a finished postmortem.** Only ONE user-confirmed wager has been
> recorded for 2026-09-02 as of this import (`importBatchId=manual-postmortem-20260902-partial-v1`).
> The 9/2 slate is still in progress. Do not infer a day-level record, family
> breakdown, or ROI from this single position -- none exists yet. This record will
> need a genuine new revision once the day's slate is actually complete.

## The one confirmed position

**SD @ CIN -- SD F5 YES**, stake $60, result **LOSS**, paid $0.

- Canonical intended stake: $60 (a USER SIZING OVERRIDE -- see below).
- Execution evidence: user-reported displayed **Kalshi payout multiplier 1.97x**
  (Kalshi's redesigned UI shows a multiplier rather than a cents price for this
  flow). No cents price or displayed probability was reported. The raw multiplier
  is preserved verbatim in `shareCardEvidence.shareCardDisplayedMultiplier`; the
  bet's required `entryPrice` field holds a flagged, non-authoritative gross
  `1/1.97` approximation only (`dataQuality=MULTIPLIER_DERIVED_ESTIMATE_UNVERIFIED`).
- Game path (user-confirmed, not independently re-derived): SD led 3-0 through the
  first five before Cincinnati hit a home run that flipped the F5 result. The exact
  inning, hitter, and final F5 score are **not asserted** here absent verified game
  data.

## Sizing context -- flagged, not resolved

The working bankroll before this wager was approximately $630; $60 represents
approximately 9.5% of bankroll. The prior recommendation had been materially
smaller, and the user explicitly overrode the suggested sizing, citing that Casey
Mize is an Auburn alumnus. **This is recorded as a USER SIZING OVERRIDE.**
Personal/team affinity must never become part of a calibrated staking model. The
key sizing lesson already visible from this one data point: a single F5 position
at ~9.5% of bankroll is too large for the normal manual workflow absent explicit
exceptional-edge justification.

## Result vs. process -- explicitly NOT resolved here

RESULT = LOSS. The pregame thesis was a starter-quality gap (Casey Mize vs. Brandon
Williamson). The position held a 3-0 lead before losing. **This is deliberately NOT
classified as a "good process, bad result" case just because it once led.** A real
process verdict requires reviewing the archived pregame market, the closing
multiplier/closing price, confirmed lineup state, starter inputs, the actual
first-five run sequence, whether the estimated pregame win probability was
reasonable, and whether the $60 size was appropriate -- treating the result and the
sizing decision as separate questions. None of that review is attempted in this
partial import.
