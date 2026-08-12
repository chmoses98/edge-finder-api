# MLB Postmortem -- 2026-08-11

## Executive Summary

The card finished 6-8, -$37.84 on $216.26 risked (-17.50% ROI).

The standalone Kalshi workflow successfully surfaced several valuable derivative markets,
particularly PIT-MIA Under 7.5, Sean Burke 8+ Ks, Cristopher Sánchez 6+ Ks, PHI-STL Under 8.5,
Cubs F5, and Tampa Bay F5.

The primary failure was not that standalone analysis lacked value. Card quality deteriorated as
additional capital was forced into weaker late-card positions. The final four additional
wagers -- Texas TT Over 4.5, Kyle Harrison 6+ Ks, Yordan Alvarez 2+ hits, and Blake Snell Under
15 outs -- lost $48.08 combined and converted a previously positive card into a losing slate.

## Primary Lessons

1. Budget should be treated as maximum exposure, never a required amount to deploy.
2. Continue evaluating the complete archived Kalshi market universe rather than limiting analysis
   to sides/F5.
3. Every recommended market should have an independent fair-probability estimate or defensible
   probability range.
4. Do not automatically translate a starting-pitcher mismatch into an F5 winner wager.
5. Explicitly model F5 tie probability.
6. Apply a correlation penalty when multiple positions depend on the same pitcher/game thesis.
7. Returning-from-IL starters should receive automatic uncertainty and stake downgrades,
   particularly for pitcher overs.
8. Team totals need independent team run projections rather than simply fading a weak opposing
   starter.
9. Hitter multi-hit props require a real plate-appearance/hit-distribution projection before
   recommendation.
10. Soft managerial workload expectations should not be treated as hard pitch/innings limits for
    pitcher-outs markets.
11. The highest-ranked standalone opportunities appeared materially stronger than the lower-ranked
    bets added to fill the available budget. Stop wagering when the edge disappears.
12. Do not interpret one slate as enough evidence to abandon F5 markets or declare pitcher-K props
    categorically superior; track performance by market family over a meaningful sample.

## Market-Family Performance

| Family | Record | Stake | Net P/L | ROI |
|---|---|---|---|---|
| F5 winner | 2-3 | $94.40 | -$22.31 | ~-23.6% |
| Game/team totals | 2-2 | $54.12 | -$3.60 | ~-6.7% |
| Pitcher strikeouts | 2-1 | $49.14 | +$6.67 | ~+13.6% |
| Pitcher outs | 0-1 | $11.76 | -$11.76 | -- |
| Hitter hits | 0-1 | $6.84 | -$6.84 | -- |

## Bet-Specific Observations

- **Cubs F5** won, but Shota Imanaga did not deliver the expected run suppression. The relative
  Irvin-vs-Imanaga starter matchup still favored Chicago enough to win the F5, while the correlated
  Washington Under 3.5 position failed. Do not treat the Cubs F5 win as full validation of the
  Imanaga suppression thesis.
- **Yankees F5** lost despite Ryan Weathers pitching very well and New York later winning the full
  game. The depleted/weak New York offense and F5 tie/early-run risk should have reduced confidence
  in the F5 winner expression.
- **PIT-MIA Under 7.5** was a strong expression of the pitching environment and won cleanly in a
  2-0 game.
- **Washington Under 3.5** was an overconfident extension of the same Imanaga thesis already
  represented through Cubs F5.
- **PHI-STL Under 8.5** was a strong handicap; both starters delivered six scoreless innings.
- **Sean Burke 8+ Ks** at plus money was a strong alternate-threshold selection and cashed with
  eight strikeouts across seven strong innings.
- **Cristopher Sánchez 6+ Ks** was a strong threshold selection and cashed with seven strikeouts in
  six scoreless innings.
- **Tampa Bay F5** was a good starting-pitcher mismatch expression and won.
- **Milwaukee F5** plus **Kyle Harrison 6+ Ks** created too much correlated exposure to a pitcher
  recently returning from the IL.
- **Houston F5** overestimated the reliability of the Hunter Brown vs. Carson Whisenhunt starter
  mismatch. Houston ultimately recovered later, but San Francisco led through five.
- **Texas TT Over 4.5** over-weighted Ryan Johnson's weak season numbers without a sufficiently
  independent Texas team-run projection.
- **Kyle Harrison 6+ Ks** had legitimate strikeout rationale but should not have been an A-tier/
  high-stake play immediately after an IL stint for forearm tightness.
- **Yordan Alvarez 2+ hits** was insufficiently modeled and appears to have been a marginal bet
  added partly because capital remained available.
- **Blake Snell Under 15 outs** leaned too heavily on an expected workload restriction. Snell was
  efficient/dominant enough to work beyond the assumed five-inning ceiling.

## Settlement Provenance Note

All 14 bets in this postmortem were imported through the canonical manual-bet importer
(`scripts/edgelab/import_bet_batch.py`, `importBatchId=manual-2026-08-11-postmortem-v1`) and then
settled via the one sanctioned manual-receipt path
(`lib.edgelab.bets.confirm_realized_return`, `source=MANUAL_POSTMORTEM_RECEIPT`) using the
user-confirmed Kalshi screenshot results, **not** via the repository's automatic settlement
pipeline (`scripts/edgelab/settle_markets.py`).

The automatic pipeline was run for 2026-08-11 and returned `SETTLEMENT_UNRESOLVED` for all 4,322
markets observed that date, because none of the 15 games archived for 2026-08-11 has a resolved
`mlbGamePk` (`data/edgelab/games/2026-08-11.jsonl`), and no `data/pipeline/2026-08-11/
normalized_slate.json` exists to backfill one. This is a data-completeness gap in this
environment's archived captures for this date, not specific to pitcher/hitter player props (issue
#43, closed by PR #44, already covers automatic player-prop settlement when a resolvable game feed
exists) -- it affects every market family observed that day.

As a result, each bet's canonical `status`/`result` fields remain `pending`/`null` (untouched,
never hand-edited), while `confirmedReceiptReturn`/`confirmedReceiptNetProfitLoss` carry the real,
user-confirmed settled economics that `lib.edgelab.bets.realized_bet_economics()` prefers for
reporting. The 6-8 W-L record and the $216.26 / $178.42 / -$37.84 / -17.50% ROI reconciliation are
reported here (`reportedTotals`) exactly as confirmed by the user; `canonicalTotals`, which is
gated on `status=="settled"`, will show `totalsMatch: false` for `totalReturned`/`netProfitLoss`/
`roi` until this environment gets a real settlement pathway for this date. This mismatch is
reported explicitly rather than forced.

One further provenance note: Blake Snell's market (`KXMLBOUTS-26AUG112210KCLAD-LADBSNELL7-15`)
carried a Kalshi UI display bug on its settlement-explanation text ("Outcome is Michael Wacha: 20
outs") -- unrelated to the actual KC/Michael Wacha 18+ outs market. The user confirmed the
payout/result itself ($0.00 / -$11.76, LOSS) is trustworthy; it is recorded as a normal settled
LOSS, not disputed or altered because of the UI bug.
