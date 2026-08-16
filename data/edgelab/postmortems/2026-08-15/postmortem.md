# MLB/Kalshi Postmortem — 2026-08-15

Import batch: `manual-2026-08-15-chat-postmortem-v1` (17 user-confirmed, real-money wagers, Kalshi settlement/share-card evidence).

## Summary

- 17 confirmed wagers, 9 wins / 8 losses.
- Intended bankroll risk (user accounting): **$295**.
- Screenshot Paid Out total (raw evidence): **$261.99**.
- User-facing P/L (intended-stake denominator): **-$33.01** (ROI **-11.19%**).
- Raw screenshot Initial Cost total (**$289.79**) is preserved separately as execution evidence and is **not** the intended-risk denominator — the user's own whole-dollar intent ($295) is.
- Canonical fee-aware ledger economics are reported separately below and are **not** forced to equal the user's rounded-allocation arithmetic: canonical total risked $295.00, canonical net P/L **-$32.36** (fee-aware simulation of `stake` as an allocated budget through `lib.edgelab.execution_economics`, vs. the user's own screenshot-based -$33.01). The two numbers measure different things — user accounting vs. fee-aware canonical economics — and are expected to diverge slightly.

## Market family performance (user accounting)

| Family | Bets | Record | Risk | Return | P/L | ROI |
|---|---|---|---|---|---|---|
| Protected F5 winner-NO | 6 | 4-2 | $117 | $132.40 | +$15.40 | +13.16% |
| Full-game ML | 2 | 1-1 | $25 | $26.91 | +$1.91 | +7.64% |
| F5 totals | 4 | 2-2 | $53 | $36.74 | -$16.26 | -30.68% |
| Pitcher props | 4 | 2-2 | $85 | $65.94 | -$19.06 | -22.42% |
| Team total | 1 | 0-1 | $15 | $0 | -$15 | -100% |

The Misiorowski $0.79 residual screenshot payout is preserved verbatim as raw `shareCardEvidence` and is not folded into any family total's canonical economics (no verified early-close/exit-fill evidence exists for that position).

## Process findings

**A. Keep F5 tie protection.** The protected F5 strategy (buy the opponent's F5-winner NO so the preferred team's lead OR a tie both win) went 4-2 for +$15.40 and better matched starter-driven theses than three-way favorite F5 YES contracts. This is a prospective process finding requiring a larger sample — six bets is not enough to call this a durable edge.

**B. Contract structure vs. handicap.** COL, MIN, LAD, and KC F5-NO legs worked. BAL and STL F5-NO losses show tie protection cannot rescue an incorrect baseball-side handicap. Continue separately evaluating (1) whether the baseball edge exists, and (2) whether F5 NO is the best expression of it.

**C. Correlation must be defined by thesis.** STL F5-NO and STL team-total-under were superficially different market families but shared one core thesis: Boyd/Chicago suppresses STL scoring. Both lost together when St. Louis scored 8. Future exposure controls should group wagers by underlying handicap driver, not market-family label alone.

**D. Pitcher outs require concrete usage evidence.** J.T. Ginn 17+ outs was good process — actual recent usage after his IL return supported a six-inning workload, and he delivered exactly 6.0 IP / 18 outs. This preserves the prior Aug 14 Burns lesson: never invent workload caps or manager hooks without evidence.

**E. Extreme strikeout thresholds need greater edge or smaller stake.** Schlittler 7+ cleared exactly at seven, a reasonable ladder rung. Misiorowski pitched very well (6.0 IP, 1 R) but recorded only 6 strikeouts, showing that excellent pitcher performance does not imply a 9+ K cash. For 8+/9+/10+ thresholds, require greater documented pricing error and/or reduce stake — tail variance is substantial.

**F. Adverse market movement.** MIA-CIN F5 under moved dramatically toward plus-money before execution. The stake was reduced, which was good risk control, but future large adverse moves should trigger a full re-handicap and should more often turn a marginal bet into PASS, rather than being read automatically as a better price.

**G. F5 total selectivity.** NYY-TOR and BAL-TB F5 unders won; MIA-CIN and CWS-DET lost. Do not conclude F5 unders are bad from this sample, but they were the worst-ROI family on Aug 15 besides the one-off team total. Require stronger pitcher/offense agreement rather than leaning heavily on recent-form narratives.

**H. Staking.** Gore 7+ K at $25 was aggressive relative to the confidence/variance profile, especially given the slate already carried substantial correlated pitcher exposure.

**I. Hitter props.** No real-money hitter bets were placed from the unproven projection engine. Continue archiving/calibrating hitter projections — do not promote them into production-confidence recommendations solely from apparent model EV until the calibration sample supports it.

These are recorded as research/process findings only — no production betting-rule or code changes were made as part of this postmortem.

## CLV note

CLV is `CLV_UNAVAILABLE` for all 17 bets on this date. `scheduledStart`/`scheduledStartTime` is null on every 2026-08-15 Game and MarketObservation row (`data/pipeline/2026-08-15/normalized_slate.json` was never captured — a standalone/manual-only Kalshi research day). The repo-supported second-source backfill (`lib.edgelab.mlb_schedule`, a live MLB Stats API schedule fetch) is blocked by this session's network policy (`statsapi.mlb.com` returns 403 at the egress proxy). Reverse-engineering the Kalshi ticker's embedded ET-local HHMM encoding is explicitly disallowed by `lib.edgelab.market_universe`'s own documented convention (DST-conversion guessing). Empirically, `marketStatus` stays `"active"` in the archived observation corpus for hours after every game's actual finish, with prices visibly collapsing/gapping post-game (e.g. the Misiorowski ticker moving from a stable ~51/52 through 22:29 UTC to 36/40 by 23:48 UTC) — so a naive "last active quote" closing-quote selection would silently pick a post-game quote. Rather than accept that, CLV is left unavailable and documented here.
