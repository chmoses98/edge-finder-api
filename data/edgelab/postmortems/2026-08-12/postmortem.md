# 2026-08-12 Postmortem

## Executive summary

- 13 confirmed wagers, all Kalshi MLB.
- Record: 5-8.
- True rounded risk (each screenshot's Initial cost rounded up to the nearest whole dollar): **$175.00**.
- Total user-confirmed returned: **$144.41**.
- Net P/L: **-$30.59**.
- ROI: **-17.48%**.
- Team totals were the strongest market family (2-1, +$20.06, +47.76% ROI).
- Pitcher outs (1-3, -$25.60) and pitcher strikeouts (1-2, -$18.23) produced most of the losses.
- Do not infer from one slate that team totals are categorically superior to other families; sample remains small.

## Analytical wins

1. **Colorado TT Over 4.5 + Merrill Kelly NO 18+ outs** was a strong correlated thesis. Colorado scored six runs and Kelly lasted five innings.
2. **CWS TT Over 4.5** correctly isolated the Rhett Lowder/Cincinnati pitching vulnerability rather than forcing the full-game total.
3. **Tyler Mahle 5+ Ks** was a good threshold selection and cashed comfortably.
4. **Dodgers win by 1.5+** was a better expression than Dodgers F5 because the advantage was expected to increase later through lineup/bullpen depth.

## Analytical misses

1. **George Klassen NO 15+ outs** received far too high a fair probability without a documented hard pitch/innings restriction.
2. **Eric Lauer NO 18+ outs** repeated the same soft-workload problem; he recorded 19 outs.
3. **Robbie Ray 16+ outs** failed because he lasted only four innings; innings/workload volatility was underpriced.
4. **Ranger Suárez 5+ Ks** remained too aggressive given his recent IL return/workload uncertainty.
5. **Will Warren 5+ Ks** required both handicap review and settlement verification — see "Will Warren settlement verification" below.
6. **Toronto F5** was a marginal edge added after the initial card and should not have been promoted merely to increase F5 representation.
7. **Yordan Alvarez 2+ hits** reinforces the need for high evidentiary standards on multi-hit distributions.

## Will Warren settlement verification (bet 9)

Independently verified against this repository's own archived evidence rather than assumed. The game (SEA @ NYY, 2026-08-12, `mlbGamePk` 823511, status `Final`) already has an authoritative settlement record on file
(`data/edgelab/settlements/2026-08-12.jsonl`, `settlementId` `e822c558087b6f0e65c6a385e53476f0f4cbc363`), sourced from the MLB Stats API live/boxscore feed (`https://statsapi.mlb.com/api/v1.1/game/823511/feed/live`):

- `playerName`: Will Warren (`playerId` 701542)
- `statFields.strikeOuts`: **3**
- `participationEvidence`: gamesPitched 1, inningsPitched 4.0 (positively verified appearance)
- `threshold`: 5, `comparisonOperator`: AT_LEAST → `outcome`: **NO**

This was cross-checked against this repository's own archived Statcast pitch-by-pitch log for the same game (`data/statcast_raw/games/823511.jsonl`), which independently shows exactly 3 strikeout events for pitcher id 701542 (Will Warren).

**Conclusion: no discrepancy.** The official box score confirms Will Warren recorded 3 strikeouts, below the 5+ threshold. The market legitimately resolved NO. This is fully consistent with the user's Kalshi screenshot ($0.00 paid out on a YES purchase, LOSS). No season-stat-delta discrepancy was found against this archived evidence; if the user has a specific external season-stat source that disagrees, it was not available in this repository's archive and was not fabricated here.

## Process lessons

- A budget is a maximum, never a deployment target.
- Do not add bets simply because a market family is underrepresented (Toronto F5 was promoted partly for this reason).
- Apply the same evidence standard across every market family.
- Pitcher-outs UNDER/NO wagers based on workload need stronger evidence: an announced pitch cap, a documented return-from-injury restriction, a clearly documented manager usage restriction, or a robust pitch-count/batters-faced model. Klassen NO and Lauer NO both lacked this and lost.
- Recent inning patterns alone are not enough to support a large edge.
- Returning-from-IL pitcher overs should receive wider uncertainty bands and generally smaller stakes or passes (Ranger Suárez 5+ Ks lost).
- Team totals remain valuable when they isolate the exact starter/bullpen weakness (Colorado TT, CWS TT both won this way).
- Multi-hit props require genuine PA/hit-distribution modeling (Yordan Alvarez 2+ hits lost).
- Continue explicitly modeling F5 tie probability.
- Preserve correlation penalties.
- Do not use one slate to overfit market-family preferences.
- Add a settlement-consistency check when user-confirmed Kalshi player-prop payouts appear to conflict with official stat feeds (see Will Warren verification above — no conflict found this time, but the check is worth having going forward).

## Settlement/accounting note

All 13 bets were recorded through the canonical manual-bet importer (`scripts/edgelab/import_bet_batch.py`, `importBatchId=manual-2026-08-12-postmortem-v1`) and settled economically via the ONE sanctioned manual-receipt path (`lib.edgelab.bets.confirm_realized_return`, `source=MANUAL_POSTMORTEM_RECEIPT`) from the user's confirmed Kalshi screenshots — **not** via the automatic settlement pipeline. `scripts/edgelab/settle_markets.py --date 2026-08-12` was attempted in this session but the MLB Stats API is not reachable from this sandboxed environment (network egress blocked), so it could not (re)fetch box scores; the attempt was reverted rather than allowed to overwrite this date's already-archived settlement evidence with a spurious "unresolved" state. Canonical `result`/`status` on these 13 bets therefore remain `pending`/`None`; only `confirmedReceiptReturn`/`confirmedReceiptNetProfitLoss` carry the real, user-confirmed settled economics used throughout this postmortem. This mirrors the same pattern used for the 2026-08-11 postmortem.

Each bet's `stake` is the user's rounded whole-dollar TRUE COST (displayed Kalshi "Initial cost" rounded up), per the user's accounting rule. The exact unrounded displayed Initial cost and Paid Out are preserved verbatim on each bet's `shareCardEvidence` field for audit.
