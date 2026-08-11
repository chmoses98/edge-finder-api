# Postmortem: 2026-08-10 MLB/Kalshi Session

**Import batch:** manual-2026-08-10-postmortem-v1

## Final reconciliation

- Wagers: 9
- Record: 3-6 (33.33% win rate)
- Total risked: $121.90
- Total returned: $57.04
- Net P/L: -$64.86
- ROI: approximately -53.21%

## Market-type performance

| Market type | Bets | Record | Risked | Returned | Net | ROI |
|---|---|---|---|---|---|---|
| F5 sides | 4 | 1-3 | $60.84 | $20.59 | -$40.25 | ~-66.16% |
| YRFI | 2 | 1-1 | $15.68 | $16.75 | +$1.07 | ~+6.82% |
| Team total | 1 | 0-1 | $24.76 | $0.00 | -$24.76 | -100% |
| Full-game ML | 1 | 0-1 | $10.80 | $0.00 | -$10.80 | -100% |
| Game total | 1 | 1-0 | $9.82 | $19.70 | +$9.88 | ~+100.61% |

## Per-bet classification

1. **Boston F5 YES** -- mixed / overconfident process. Starter/offense thesis legitimate, but a very large model-market disagreement was withheld from Tier A and manual analysis still pushed confidence too high; stake too large for the unresolved disagreement. LOSS, -$24.59. Would make again only at smaller stake unless disagreement is independently explained.
2. **Mets F5 YES** -- good cautious process. Model's 61.5% estimate was not trusted; manual fair range reduced to ~46-49%; 42% execution still offered plausible value; small stake reflected uncertainty. WIN, +$11.77. Win does not validate the model's extreme probability. Would make again, small.
3. **Baltimore F5 YES** -- reasonable process / variance. Starter matchup supported Baltimore; F5 isolated the starter advantage; manual fair ~49-52% vs. 45% execution. LOSS, -$16.68. Would make again near same price.
4. **MIL@SD YRFI YES** -- poor process. Positive model edge but large disagreement; dedicated first-inning xERA inputs missing; attractive +150 price substituted for probability confidence. LOSS, -$7.83. Would not make again at normal size without better first-inning-specific evidence.
5. **Boston team total Over 4.5 YES** -- major process failure. Manual recommendation described ~-108/58-61% fair; actual receipt was 28.88%/~+246 on the exact Boston Over 4.5 contract; model evaluation did not support the manual confidence; exact rung/ticker/semantics never reconciled before recommendation; stake also oversized. LOSS, -$24.76. Would not make again under this process.
6. **KC@LAD YRFI YES** -- speculative but controlled. Model 56.55% at 46%, +2.69% calibrated edge, max price 52.62%; dedicated first-inning xERA still missing; small stake limited damage from model incompleteness. WIN, +$8.90. Would make again only small until first-inning-specific modeling is stronger.
7. **St. Louis full-game ML YES** -- reasonable process / variance. Independent manual fair ~50-53% vs. 48% execution; modest stake. LOSS, -$10.80. Would make again near same price if baseball thesis unchanged.
8. **Colorado F5 YES** -- strong process / variance. Model probability 39.54%, model executable price 31.55%, calibrated edge +2.04%, bet-up-to 35.62%; user executed at 33%, inside maximum; confirmed lineups. LOSS, -$10.75. Would make again.
9. **TEX@LAA NO on Over 7.5 total runs** -- good baseball direction but contract-label process failure. Low-scoring baseball thesis reasonable; receipt confirms NO on Over 7.5; chat analysis incorrectly changed the natural-language threshold afterward (toward Under 8.5). WIN, +$9.88. Exact ticker-to-display-label reconciliation is mandatory going forward. Would make again only after exact market semantics confirmed before recommendation.

## Portfolio lessons

1. BOS F5 + BOS TT risked $49.35, about 40.5% of the day's total risk, on correlated Boston game-script assumptions.
2. Correlation limits should consider actual dollars at risk, not merely bet count.
3. Large model-market disagreements remain audit flags, not confidence boosters.
4. Automated PASS/PAPER status remains advisory for manual analysis, but every warning must be explicitly explained before override.
5. First-inning markets with missing dedicated first-inning inputs require an independent first-inning-specific handicap before real-money promotion.
6. Exact ticker/rung/natural-language contract identity must be reconciled before final recommendation.
7. Do not infer strategy quality from result alone: Colorado F5 lost with strong process; Mets F5 won despite major model disagreement; KC YRFI won despite incomplete first-inning inputs.
8. Exhaustive market search remains correct; selective staking and exact market identification need to improve.
