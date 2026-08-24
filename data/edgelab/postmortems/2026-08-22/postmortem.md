# 2026-08-22 Postmortem — MLB Kalshi Slate

**Record (imported, straight):** 6-4 | **1 wager BLOCKED_MISSING_EVIDENCE** (excluded from canonical ledger)
**Canonical (imported-only) totals:** Risk $186.00, Paid $199.04, P/L +$13.04, ROI +7.01%
**User-reported full-slate totals (incl. $80 SD F5):** Risk $266, Paid $199.04, P/L -$66.96, ROI -25.17%

## Summary

Excluding the blocked $80 MIN-SD F5 wager, this was a modestly profitable day (+7.01% ROI) carried by two clean moneylines (MIL, TOR) and three of four team totals (MIA 4+, LAD 4+, AZ 4+). Two of three protected-F5-style NO positions lost outright (DET/KC, CIN/AZ).

## Blocked Wager: MIN-SD F5

`2026-08-22-min-sd-f5-min-no-001` — **BLOCKED_MISSING_EVIDENCE**. MIN vs SD F5, NO on Minnesota wins F5 (economic meaning: SD leads or ties). Risk $80, Paid $0, LOSS. Placement itself was user-confirmed, but unlike every other wager this batch, no exact execution price or displayed probability was supplied. A pregame discussion referenced roughly -186 American odds, but per explicit instruction that is NOT treated as the executed price absent archived evidence resolving it — the canonical importer requires exactly one of entryPrice/entryOdds, and inventing either from an unconfirmed pregame reference would be fabrication. No archived evidence resolves the actual execution price, so this wager is excluded from the canonical ledger.

Starter mapping for this game, preserved for future reference: **Casey Mize = San Diego, Dean Kremer = Minnesota.**

Excluding this $80 wager reconciles exactly to the day-level counterfactual already known: risk $186, paid $199.04, P/L +$13.04, ROI +7.01% — independently recomputed here from the canonical ledger and confirmed to match exactly.

## Critical Findings

- The $80 SD F5 wager was a sizing/confidence-calibration failure independent of whether the pick itself was correct — roughly 26% of the then-discussed ~$312 bankroll.
- Heavy F5 protection only protects tie risk, not a wrong-side handicap. Protected F5 no longer defaults as the safest expression — require an uncertainty haircut before qualification.
- Raw model-vs-market gaps should trigger skepticism (starter/lineup/platoon inputs, stale prices, market definition, tie distribution, family calibration, model uncertainty), not automatic sizing increases.
- Compare F3/F5 team YES, Tie YES, protected NO, full-game ML, and team totals systematically before qualifying any single expression as default.
- Add tighter single-wager and family-concentration controls.

## Family Breakdown (canonical, imported-only)

- **Moneyline:** 2-0, $52.00 risk, +$40.85
- **Game total:** 0-1, $16.00 risk, -$16.00
- **Team total:** 3-1, $90.00 risk, +$16.14 (AZ 4+ arithmetic P/L $13.01 vs screenshot-stated $13.02 — raw figure preserved in evidence metadata, canonical figure used for accounting)
- **F5 (imported):** 1-2, $50.00 risk, -$29.95 (1 additional $80 wager blocked)

## Same-Game Concentration

CIN @ AZ carried two wagers: F5 NO-CIN lost, AZ 4+ won — mixed same-game exposure ($44 combined risk, -$8.99 net).
