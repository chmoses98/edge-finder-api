# MLB postmortem — 2026-09-09

**The strongest day of the period: team totals 3-0 and the best single bet of the block (Yamamoto 8+ Ks) — with one correlated-exposure warning that the wins do not excuse.**

Eight positions, six winners, +$75.57 on $177.98 risked. Every team-total rung that was scanned and taken had a coherent starter + workload/TTO + bullpen + lineup mechanism behind it, on both the OVER and the NO side. Yamamoto 8+ strikeouts at 48% is the bet of the period. The Boston F5 loss is explicitly a good-process loss, and the $70 of correlated CIN/LAD exposure is explicitly a sizing warning even though both legs won.

## User-confirmed day (canonical receipt economics)

- Positions: 8
- Record: 6-2
- Risk: $177.98
- Realized return: $253.55
- P/L: +75.57
- ROI: +42.46%
- Import batch: `mlb-manual-2026-09-09-postmortem-v1`

Every one of these positions was written canonically; nothing in this batch was blocked, and no wager was inferred from a recommendation. The displayed Kalshi percentage is recorded verbatim as the executed entry price — never fee-adjusted, never recomputed from cost and payout.

## Wagers

| Market | Ticker | Side | Entry (displayed) | Cost | Paid out | Result | Net P/L | Grade |
|---|---|---|---|---|---|---|---|---|
| HOU/PHI game total NO on over 8.5 (8 or fewer runs) | `KXMLBTOTAL-26SEP091840HOUPHI-9` | NO | 54.0% | $15.00 | $0.00 | LOSS | -15.00 | C+ / BORDERLINE_SHOULD_PASS |
| Boston wins first 5 innings (three-way F5: tie loses) | `KXMLBF5-26SEP091845LAABOS-BOS` | YES | 56.0% | $25.00 | $0.00 | LOSS | -25.00 | B+ / GOOD_PROCESS_LOSS |
| TB/ATL game total over 7.5 | `KXMLBTOTAL-26SEP091915TBATL-8` | YES | 55.0% | $9.99 | $17.89 | WIN | +7.90 | B- / ACCEPTABLE_SMALL |
| New York Yankees 6+ runs (over 5.5) | `KXMLBTEAMTOTAL-26SEP091905COLNYY-NYY6` | YES | 47.0% | $20.00 | $41.77 | WIN | +21.77 | A- / STRONG |
| Kansas City 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP091940AZKC-KC4` | YES | 57.0% | $20.00 | $34.56 | WIN | +14.56 | A- / STRONG |
| Cincinnati 4+ runs NO (over 3.5 NO -- 3 or fewer runs) | `KXMLBTEAMTOTAL-26SEP092210CINLAD-CIN4` | NO | 60.0% | $40.00 | $65.74 | WIN | +25.74 | A- / STRONG |
| Yoshinobu Yamamoto 8+ strikeouts | `KXMLBKS-26SEP092210CINLAD-LADYYAMAMOTO18-8` | YES | 48.0% | $30.00 | $61.38 | WIN | +31.38 | A / STRONG |
| Teoscar Hernández 2+ hits + runs + RBIs | `KXMLBHRR-26SEP092210CINLAD-LADTHERNANDEZ37-2` | YES | 55.0% | $17.99 | $32.21 | WIN | +14.22 | B+ / GOOD |

## Outcome vs process

Result and process are graded separately and must not be collapsed into each other.

**Good process / win:**

- TB/ATL game total over 7.5 — B- (+7.90)
- New York Yankees 6+ runs (over 5.5) — A- (+21.77)
- Kansas City 4+ runs (over 3.5) — A- (+14.56)
- Cincinnati 4+ runs NO (over 3.5 NO -- 3 or fewer runs) — A- (+25.74)
- Yoshinobu Yamamoto 8+ strikeouts — A (+31.38)
- Teoscar Hernández 2+ hits + runs + RBIs — B+ (+14.22)

**Good process / loss:**

- Boston wins first 5 innings (three-way F5: tie loses) — B+ (-25.00)

**Weak process / win:** none.

**Weak process / loss:**

- HOU/PHI game total NO on over 8.5 (8 or fewer runs) — C+ (-15.00)

## Market-family breakdown

| Family | Record | Net P/L | Note |
|---|---|---|---|
| team_total | 3-0 | +62.07 | — |
| pitcher_strikeouts | 1-0 | +31.38 | Settlement still pending in the canonical ledger (GitHub issue #43). |
| hitter_hits_runs_rbis | 1-0 | +14.22 | Settlement still pending in the canonical ledger (GitHub issue #43). |
| game_total | 1-1 | -7.10 | — |
| inning_result (F5) | 0-1 | -25.00 | — |

## Game-level concentration

- **CIN @ LAD** — risked $87.99, 3-0, P/L +71.34. Yamamoto 8+ Ks ($30) + CIN 4+ NO ($40) alone is $70 of correlated same-game exposure (~7% of a ~$1,000 bankroll). Both won; the sizing is still elevated and must not become the normal template.

## What the analysis got right

- **yamamoto-8k-best-bet-of-period** — Yoshinobu Yamamoto 8+ strikeouts at 48% was one of the strongest bets of the entire period: K ability, expected batters faced/workload, opponent strikeout profile, and price all aligned simultaneously. Strikeout props deserve serious, systematic attention.
- **team-total-family-3-0** — NYY 6+, KC 4+ and CIN 4+ NO all had coherent starter + workload/TTO + bullpen + lineup scoring mechanisms, on both the OVER and the NO side. Team totals remain a priority family -- but do not overfit this 3-0.
- **teoscar-hrr-low-threshold** — Teoscar Hernandez 2+ hits+runs+RBIs at 55% is a sound low-threshold combined-stat rung with a favourable lineup slot and matchup.

## Analytical misses

- **bos-f5-good-process-loss** — Boston F5 YES at 56% LOST, and it is explicitly NOT a process failure: the pregame starter-quality mismatch was meaningful and the three-way tie risk was understood. This is the canonical good-process/loss row for the period.
- **tb-atl-over-b-tier-winner** — TB/ATL full-game over 7.5 at 55% cashed on a $9.99 stake. It was and remains a B-tier, acceptable-small position. Do NOT promote it retrospectively simply because it cashed.

## Process errors

- **cin-lad-correlated-exposure** — Yamamoto 8+ Ks ($30) + CIN 4+ NO ($40) = $70 of correlated same-game exposure, roughly 7% of a ~$1,000 bankroll. The thesis was strong and BOTH won -- correlated winners do not excuse oversizing, and this must not become the normal template.
- **hou-phi-9plus-no-borderline** — HOU/PHI game total 9+ NO at 54% was a borderline position that should have been passed: the suppression mechanism was thin relative to the price.

## Proposed investigations

- **strikeout-prop-systematic-coverage** — Give strikeout props systematic coverage: K ability x expected batters faced/workload x opponent strikeout profile x price, scored as a single joint condition.
- **same-game-correlated-exposure-cap** — Enforce one shared exposure budget per same-game / same-thesis cluster, evaluated BEFORE either leg is placed.
- **team-total-rung-scan** — Continue scanning every team-total rung on both the OVER and the NO side; do not overfit a single day's 3-0.

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
