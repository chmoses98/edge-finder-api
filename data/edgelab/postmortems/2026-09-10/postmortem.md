# MLB postmortem — 2026-09-10

**A losing day driven by one unacceptable concentration: $98 on a single weak shared thesis around Jared Jones.**

Four positions on a short slate, and the day is decided by one cluster. Jones 16+ NO ($50) and CWS 4+ ($48) were not two positions; they were $98 on one weak shared thesis, and both lost in full. Wheeler 18+ YES and NYY 6+ both won, and the NYY stake had doubled off the prior day's result without a new reason.

## User-confirmed day (canonical receipt economics)

- Positions: 4
- Record: 2-2
- Risk: $158.00
- Realized return: $120.08
- P/L: -37.92
- ROI: -24.00%
- Import batch: `mlb-manual-2026-09-10-postmortem-v1`

Every one of these positions was written canonically; nothing in this batch was blocked, and no wager was inferred from a recommendation. The displayed Kalshi percentage is recorded verbatim as the executed entry price — never fee-adjusted, never recomputed from cost and payout.

## Wagers

| Market | Ticker | Side | Entry (displayed) | Cost | Paid out | Result | Net P/L | Grade |
|---|---|---|---|---|---|---|---|---|
| Zack Wheeler 18+ outs | `KXMLBOUTS-26SEP101305HOUPHI-PHIZWHEELER45-18` | YES | 60.0% | $20.00 | $32.87 | WIN | +12.87 | A- / STRONG |
| Chicago White Sox 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP101940PITCWS-CWS4` | YES | 52.0% | $48.00 | $0.00 | LOSS | -48.00 | D / WEAK_CORRELATED_THESIS_AND_OVERSIZED |
| Jared Jones 16+ outs NO | `KXMLBOUTS-26SEP101940PITCWS-PITJJONES17-16` | NO | 53.0% | $50.00 | $0.00 | LOSS | -50.00 | D- / PROCESS_FAILURE_AND_OVERSIZED |
| New York Yankees 6+ runs (over 5.5) | `KXMLBTEAMTOTAL-26SEP101905COLNYY-NYY6` | YES | 45.0% | $40.00 | $87.21 | WIN | +47.21 | B+ / GOOD_BUT_STAKE_AGGRESSIVE |

## Outcome vs process

Result and process are graded separately and must not be collapsed into each other.

**Good process / win:**

- Zack Wheeler 18+ outs — A- (+12.87)
- New York Yankees 6+ runs (over 5.5) — B+ (+47.21)

**Good process / loss:** none.

**Weak process / win:** none.

**Weak process / loss:**

- Chicago White Sox 4+ runs (over 3.5) — D (-48.00)
- Jared Jones 16+ outs NO — D- (-50.00)

## Market-family breakdown

| Family | Record | Net P/L | Note |
|---|---|---|---|
| team_total | 1-1 | -0.79 | — |
| pitcher_outs | 1-1 | -37.13 | Settlement still pending in the canonical ledger (GitHub issue #43). |

## Game-level concentration

- **PIT @ CWS** — risked $98.00, 0-2, P/L -98.00. Jones 16+ NO ($50) + CWS 4+ ($48) = $98 concentrated on ONE weak shared thesis (Jones gets hit early). Unacceptable concentration; both lost in full.
- **COL @ NYY** — risked $40.00, 1-0, P/L +47.21. Same repeatable NYY 6+ thesis as 2026-09-09, but at double the stake ($20 -> $40). Winning does not validate the escalation.

## What the analysis got right

- **wheeler-structural-workload-yes** — Zack Wheeler 18+ outs YES at 60% is the OTHER side of the same structural discipline as Painter: an explicit workload/role/matchup justification for a durable innings expectation. Structural outs reasoning keeps earning its place; speculative recent-IP reasoning does not.
- **nyy-team-total-thesis-repeatable** — The NYY 6+ thesis repeated cleanly against Colorado pitching for the second straight day. The thesis was right; see processErrors for the stake escalation that rode along with it.

## Analytical misses

- **cws-4plus-correlated-thesis** — Chicago White Sox 4+ runs at 52% was not an independent team-total read; it was the offensive mirror of the Jones 16+ NO thesis, sized as if it were independent.

## Process errors

- **jared-jones-16-no-process-failure** — Jared Jones 16+ outs NO at 53% for $50 is the period's worst row and a PROCESS FAILURE AND OVERSIZED. Pregame profile: 27.6% K, 32% whiff, 3.63 xERA, 3.82 xFIP, 5.4 IP/start, 3.29 recent FIP. The wager over-weighted his recent variable innings and ignored the substantial dominant-start branch. Grade this a BAD RECOMMENDATION, not merely a bad result.
- **pit-cws-shared-thesis-concentration** — Jones 16+ NO ($50) + CWS 4+ ($48) = $98 combined exposure on ONE weak shared thesis. Same-thesis splits are allowed, but they share ONE exposure budget -- compare Painter NO + HOU F5 at $20 combined on 2026-09-08. This was an unacceptable concentration and it lost in full.
- **nyy-stake-escalation** — NYY 6+ went from $20 on 2026-09-09 to $40 on 2026-09-10 on the same thesis. It won. Winning does not validate oversizing, and a secondary expression recommended for small exposure must not silently become a large independent position.

## Proposed investigations

- **grade-recommendations-independently-of-outcome** — Record a process grade for every recommendation at decision time, independent of the eventual result, and reconcile it in the postmortem across all four outcome/process quadrants.
- **shared-thesis-budget-enforcement** — Block a second expression of an already-funded thesis from being sized as an independent position (Jones NO + CWS 4+ = $98 must be impossible without an explicit override).
- **stake-escalation-guard** — Flag any repeat of a prior day's thesis whose stake has materially escalated, and require that escalation be justified by something other than the prior day's result.

## Period findings (2026-09-08 → 2026-09-10)

These govern the whole user-confirmed manual block and are recorded on each date so every date's structured record stands alone.

1. **Full-universe scan remains required** — Every game and every available Kalshi family/rung should be considered. A larger candidate universe must NOT lower the betting threshold.
2. **Pitcher-outs UNDER rule** — Do not bet pitcher-outs unders primarily because recent innings have been short or volatile. Require a structural innings-suppression mechanism: a documented pitch/innings cap, injury return, opener/bulk role, demonstrably short managerial leash, severe pitch-count matchup, or another durable workload constraint. Variance is not a workload edge.
3. **Jared Jones was a process failure** — Pregame profile: 27.6% K, 32% whiff, 3.63 xERA, 3.82 xFIP, 5.4 IP/start, 3.29 recent FIP. The wager over-weighted his recent variable innings and ignored the substantial dominant-start branch. Jared Jones 16+ NO is graded a BAD RECOMMENDATION, not merely a bad result.
4. **Misiorowski outs-under was also a process failure** — His elite strikeout/whiff/xFIP profile and normal workload made the under-outs recommendation insufficiently justified.
5. **Structural outs bets performed differently** — Painter 16+ NO and Wheeler 18+ YES both had explicit workload/role/matchup justification. Treat structural workload reasoning separately from speculative recent-IP reasoning.
6. **Team totals remain a priority market family** — Successful NYY/KC/CIN positions had coherent starter + workload/TTO + bullpen + lineup scoring mechanisms. Do not overfit the 5-3 result, but continue scanning every rung.
7. **Strikeout props deserve serious attention** — Yamamoto 8+ at 48% was one of the strongest bets in the period because K ability x expected batters faced/workload x opponent strikeout profile x price all aligned.
8. **Same-thesis splits are allowed but share one exposure budget** — Painter NO + HOU F5 = $12 + $8 = $20 combined: a well-sized split with related but non-identical cash paths. Jones NO + CWS 4+ = $50 + $48 = $98 combined: an unacceptable concentration around a weak shared thesis.
9. **Correlated winners do not excuse oversizing** — Yamamoto 8+ Ks + CIN 4+ NO totaled $70. The thesis was strong and both won, but 7% of a ~$1,000 bankroll is still elevated correlated exposure and must not become the normal template.
10. **Protected F5 price discipline** — Protected F5 is not banned. At ~55%, SEA F5 NO had a meaningful payout and a coherent tie-protection rationale. At ~65%, PIT F5 NO was an unattractive high-variance risk/reward profile and a poor handicap. Protected F5 above roughly 60-65% should require exceptional evidence.
11. **BOS F5 is a good-process loss** — Do not classify it as a process failure merely because it lost. The pregame starter-quality mismatch was meaningful.
12. **TB-ATL full-game Over 7.5 was a B-tier winner** — Do not promote it retrospectively simply because it cashed.
13. **Recent form must not manufacture large edges** — STL ML and MIN-DET F5 Over are examples where recent offense/recent FIP was weighted too heavily.
14. **Sizing discipline** — Winning does not validate oversizing. A secondary expression recommended for small exposure must not silently become a large independent position.
15. **Outcome vs process** — The postmortem must explicitly separate good process/win, good process/loss, weak process/win, and weak process/loss.

## Settlement provenance

entryPrice is the DISPLAYED Kalshi percentage, recorded verbatim (never fee-adjusted, never recomputed from cost/payout). stake is the user-confirmed cost. Realized returns were recorded through lib.edgelab.bets.confirm_realized_return with source=MANUAL_POSTMORTEM_RECEIPT; objective settlement provenance was never overwritten.

Pitcher and hitter props in this batch are canonically imported with their user-confirmed receipt economics preserved, but they remain status=pending in the canonical ledger: the automatic player-prop settlement path still does not exist (GitHub issue #43). They are NOT claimed to have been automatically settled.
