# MLB postmortem — 2026-09-08

**A near-scratch day whose real content is the split between structural and speculative pitcher-outs reasoning, and protected-F5 price discipline.**

Thirteen user-confirmed positions, six winners, and a result within $3 of scratch. The outcome is close to noise; the process spread is not. Painter 16+ NO and Houston F5 were one well-sized $20 split with an explicit workload/role/matchup justification, and both cashed. Misiorowski 18+ NO was the same market family with none of that justification, and it is graded a process failure regardless of its small size. Seattle F5 NO at ~55% and Pittsburgh F5 NO at ~65% were the same idea at two prices; only one of them was a bet.

## User-confirmed day (canonical receipt economics)

- Positions: 13
- Record: 6-7
- Risk: $250.98
- Realized return: $248.06
- P/L: -2.92
- ROI: -1.16%
- Import batch: `mlb-manual-2026-09-08-postmortem-v1`

Every one of these positions was written canonically; nothing in this batch was blocked, and no wager was inferred from a recommendation. The displayed Kalshi percentage is recorded verbatim as the executed entry price — never fee-adjusted, never recomputed from cost and payout.

## Wagers

| Market | Ticker | Side | Entry (displayed) | Cost | Paid out | Result | Net P/L | Grade |
|---|---|---|---|---|---|---|---|---|
| Ty France 1+ hits | `KXMLBHIT-26SEP082140WSHSD-SDTFRANCE4-1` | YES | 62.0% | $20.00 | $31.83 | WIN | +11.83 | B+ / GOOD |
| Toronto 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP082140TORATH-TOR5` | YES | 59.0% | $18.00 | $0.00 | LOSS | -18.00 | B- / DEFENSIBLE_BUT_OVERCONFIDENT |
| St. Louis moneyline | `KXMLBGAME-26SEP082145STLSF-STL` | YES | 51.0% | $10.00 | $0.00 | LOSS | -10.00 | C- / SHOULD_PASS |
| Seattle first 5 innings NO (Texas leads or tie after five) | `KXMLBF5-26SEP082140TEXSEA-SEA` | NO | 55.0% | $15.99 | $28.63 | WIN | +12.64 | B+ / GOOD |
| Jacob Misiorowski 18+ outs NO | `KXMLBOUTS-26SEP081940CHCMIL-MILJMISIOROWSKI32-18` | NO | 45.0% | $7.50 | $0.00 | LOSS | -7.50 | D / PROCESS_FAILURE |
| Milwaukee 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP081940CHCMIL-MIL5` | YES | 44.0% | $18.00 | $0.00 | LOSS | -18.00 | C / WEAK |
| Andrew Painter 16+ outs NO | `KXMLBOUTS-26SEP081840HOUPHI-PHIAPAINTER76-16` | NO | 44.0% | $12.00 | $26.35 | WIN | +14.35 | A / STRONG |
| Houston wins first 5 innings (three-way F5: tie loses) | `KXMLBF5-26SEP081840HOUPHI-HOU` | YES | 39.0% | $8.00 | $20.08 | WIN | +12.08 | B+ / GOOD_SPLIT_EXPRESSION |
| Sandy Alcantara 19+ outs | `KXMLBOUTS-26SEP081840NYMMIA-MIASALCANTARA22-19` | YES | 39.0% | $10.00 | $0.00 | LOSS | -10.00 | D+ / WEAK_WORKLOAD_THESIS |
| Pittsburgh first 5 innings NO (Chicago White Sox lead or tie after five) | `KXMLBF5-26SEP081940PITCWS-PIT` | NO | 64.9% | $19.99 | $0.00 | LOSS | -19.99 | D / PROCESS_FAILURE |
| TB/ATL first 5 innings over 4.5 | `KXMLBF5TOTAL-26SEP081915TBATL-5` | YES | 51.0% | $20.00 | $38.55 | WIN | +18.55 | A- / STRONG |
| MIN/DET first 5 innings over 4.5 | `KXMLBF5TOTAL-26SEP081840MINDET-5` | YES | 49.0% | $25.00 | $0.00 | LOSS | -25.00 | C- / TOO_SPECULATIVE |
| Cincinnati 4+ runs NO (over 3.5 NO -- 3 or fewer runs) | `KXMLBTEAMTOTAL-26SEP082210CINLAD-CIN4` | NO | 64.0% | $66.50 | $102.62 | WIN | +36.12 | A / STRONG |

## Outcome vs process

Result and process are graded separately and must not be collapsed into each other.

**Good process / win:**

- Ty France 1+ hits — B+ (+11.83)
- Seattle first 5 innings NO (Texas leads or tie after five) — B+ (+12.64)
- Andrew Painter 16+ outs NO — A (+14.35)
- Houston wins first 5 innings (three-way F5: tie loses) — B+ (+12.08)
- TB/ATL first 5 innings over 4.5 — A- (+18.55)
- Cincinnati 4+ runs NO (over 3.5 NO -- 3 or fewer runs) — A (+36.12)

**Good process / loss:**

- Toronto 5+ runs (over 4.5) — B- (-18.00)

**Weak process / win:** none.

**Weak process / loss:**

- St. Louis moneyline — C- (-10.00)
- Jacob Misiorowski 18+ outs NO — D (-7.50)
- Milwaukee 5+ runs (over 4.5) — C (-18.00)
- Sandy Alcantara 19+ outs — D+ (-10.00)
- Pittsburgh first 5 innings NO (Chicago White Sox lead or tie after five) — D (-19.99)
- MIN/DET first 5 innings over 4.5 — C- (-25.00)

## Market-family breakdown

| Family | Record | Net P/L | Note |
|---|---|---|---|
| hitter_hits | 1-0 | +11.83 | — |
| inning_result (F5) | 2-1 | +4.73 | — |
| team_total | 1-2 | +0.12 | — |
| pitcher_outs | 1-2 | -3.15 | Settlement still pending in the canonical ledger (GitHub issue #43: no automatic player-prop settlement path). Economics here are the user-confirmed receipts only. |
| inning_total (F5) | 1-1 | -6.45 | — |
| game_result | 0-1 | -10.00 | — |

## Game-level concentration

- **CIN @ LAD** — risked $66.50, 1-0, P/L +36.12. Largest single position of the period (~6.7% of a ~$1,000 bankroll) on a single team-total NO rung.
- **CHC @ MIL** — risked $25.50, 0-2, P/L -25.50. Two same-game positions on opposite-signed theses (Misiorowski outs UNDER plus MIL 5+); both lost.
- **HOU @ PHI** — risked $20.00, 2-0, P/L +26.43. Painter 16+ NO ($12) + HOU F5 ($8): a well-sized same-thesis split sharing ONE $20 exposure budget across related but non-identical cash paths.

## What the analysis got right

- **painter-structural-workload-edge** — Andrew Painter 16+ outs NO at 44% was the model structural outs bet: an explicit, documented workload/role constraint plus a severe pitch-count matchup, not a recent-innings extrapolation. This is the template pitcher-outs unders must meet.
- **hou-f5-split-expression** — Houston F5 YES at 39% was a correctly-sized secondary expression of the same HOU/PHI thesis ($8 against Painter's $12, one $20 budget). Related but non-identical cash paths, and the three-way tie risk was priced into the stake, not ignored.
- **cin-team-total-no-rung-scan** — Cincinnati 4+ runs NO at 64% came from scanning every team-total rung rather than defaulting to the headline number, with a coherent starter + bullpen + lineup suppression mechanism behind it.
- **sea-f5-no-protected-price** — Seattle F5 NO at ~55% is protected F5 done correctly: the tie branch is bought at a price that still pays meaningfully. Contrast with PIT F5 NO at ~65% the same night.
- **tb-atl-f5-total-mechanism** — TB/ATL first 5 innings over 4.5 at 51% had a real two-sided scoring mechanism rather than a recent-scoring extrapolation.
- **ty-france-low-threshold-hit-prop** — Ty France 1+ hits at 62% is the low-threshold, high-floor end of the hitter board -- a defensible small position, not a lottery rung.

## Analytical misses

- **tor-5plus-overconfident** — Toronto 5+ runs at 59%: a defensible family and rung, but the implied scoring distribution was too confident for the price paid. Defensible-but-overconfident, not a process failure.
- **mil-5plus-weak-mechanism** — Milwaukee 5+ runs at 44% lacked the coherent starter + bullpen + lineup mechanism the winning team-total positions had. Scanning every rung does not mean betting every rung.
- **alcantara-19plus-workload-thesis** — Sandy Alcantara 19+ outs YES at 39% rested on a workload thesis that was not durably established. A high outs threshold needs a documented role expectation, not an assumption.

## Process errors

- **misiorowski-outs-under-process-failure** — Jacob Misiorowski 18+ outs NO is a PROCESS FAILURE, not a bad result. His elite strikeout/whiff/xFIP profile and normal workload made an under-outs recommendation insufficiently justified. Pitcher-outs unders require a STRUCTURAL innings-suppression mechanism (pitch/innings cap, injury return, opener/bulk role, demonstrably short leash, severe pitch-count matchup, or another durable workload constraint). Variance is not a workload edge.
- **pit-f5-no-price-discipline** — Pittsburgh F5 NO at 64.9% is a PROCESS FAILURE on price discipline, not on the idea. Protected F5 is not banned -- but above roughly 60-65% the payout no longer compensates the variance, and this was also a poor handicap on the merits. Compare SEA F5 NO at ~55% the same night.
- **stl-ml-recent-form-edge** — St. Louis moneyline at 51% was manufactured largely out of recent offensive form. Recent form must not manufacture a large edge on a coin-flip-priced market. Should have passed.
- **min-det-f5-over-too-speculative** — MIN/DET first 5 innings over 4.5 at 49% for $25 leaned on recent scoring and recent FIP rather than a durable mechanism, and it was the second-largest stake of the day. Too speculative for the size.

## Proposed investigations

- **outs-under-requires-structural-mechanism** — Gate every pitcher-outs UNDER behind an explicit, named structural innings-suppression mechanism. Reject the recommendation when the only support is recent short or volatile innings.
- **protected-f5-price-ceiling** — Apply a price ceiling to protected F5 (side NO): above roughly 60-65%, require exceptional evidence before the position is allowed.
- **recent-form-edge-cap** — Cap how much recent offensive form / recent FIP may contribute to a stated edge, so recent form cannot manufacture a large edge on a near-coin-flip price.
- **full-universe-scan-without-threshold-drift** — Keep scanning every game and every family/rung, and verify that the widening candidate universe never lowers the betting threshold.

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
