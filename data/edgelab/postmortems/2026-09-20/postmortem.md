# Postmortem — MLB 2026-09-17 → 2026-09-20 (four-day REAL wager review)

Scope: the 37 REAL wagers in the canonical ledger across these four dates. Legacy, model and
paper tracked rows are excluded from every figure below.

## Result

**18-19, -$77.45 on $2,127.86 risked, -3.64% ROI.**

The four days lost money. The loss is modest relative to the volume put through, but it is a
loss and is not described here as flat.

| date | REAL | W-L | risked | P/L | ROI |
|---|---|---|---|---|---|
| 2026-09-17 | 5 | 3-2 | $294.9918 | +33.3482 | +11.3048% |
| 2026-09-18 | 8 | 3-5 | $309.9779 | -94.1779 | -30.3821% |
| 2026-09-19 | 9 | 4-5 | $647.4708 | -74.0608 | -11.4385% |
| 2026-09-20 | 15 | 8-7 | $875.4180 | +57.4420 | +6.5617% |
| **combined** | **37** | **18-19** | **$2,127.8585** | **-$77.4485** | **-3.6397%** |

## By market family

| family | n | W-L | risked | share | P/L | ROI |
|---|---|---|---|---|---|---|
| team_total | 19 | 10-9 | $1147.4348 | 53.9% | -38.5848 | -3.36% |
| inning_result | 6 | 2-4 | $359.9858 | 16.9% | +5.3942 | +1.50% |
| pitcher_strikeouts | 6 | 2-4 | $332.4619 | 15.6% | -44.9719 | -13.53% |
| game_total | 4 | 3-1 | $175.4811 | 8.2% | +3.8589 | +2.20% |
| winning_margin | 1 | 1-0 | $59.9997 | 2.8% | +49.3503 | +82.25% |
| game_result | 1 | 0-1 | $52.4952 | 2.5% | -52.4952 | -100.00% |

Team totals were **19 of 37 wagers and 53.9% of risked capital**, finishing 10-9 for -$38.58.
That is a **concentration finding and nothing more**. Nineteen wagers cannot establish or refute
an edge, and this is not evidence that team totals are intrinsically bad. The same caution runs
in both directions: winning_margin's +82% ROI rests on one wager, pitcher_strikeouts' -13.5% on
six. **No market-family edge claim is made from this sample.**

## Where the money actually went

### Correlated doubles cost more than the entire four-day loss

Six games carried two REAL wagers each — **$545.44 risked (25.6% of capital) returning -$164.88**.
That is more than twice the net loss of -$77.45, which means the rest of the book was net positive.

| game | n | risked | P/L | families |
|---|---|---|---|---|
| 26SEP201610SFLAD | 2 | $104.9945 | -104.9945 | pitcher_strikeouts, team_total |
| 26SEP201510SEACOL | 2 | $104.9886 | -104.9886 | team_total |
| 26SEP181940DETCWS | 2 | $54.9977 | -8.4877 | inning_result, team_total |
| 26SEP201607MINLAA | 2 | $87.4791 | +1.7209 | pitcher_strikeouts, team_total |
| 26SEP201610NYYAZ | 2 | $122.4852 | +15.2248 | game_result, pitcher_strikeouts |
| 26SEP201410DETCWS | 2 | $70.4942 | +36.6458 | game_total |

`26SEP201510SEACOL` is the clearest case: COL 4+ and SEA 5+ bought on the **same game** — two
tickets expressing one high-scoring thesis. The game held both under and the pair lost $104.99.
`26SEP201610SFLAD` lost $104.99 the same way across two different families. Two wagers on one
game that share a direction are one position, and the book was not sized as though they were.

### One slate, one family

2026-09-18 put five of eight wagers into F5 `inning_result`: **1-4 for -$114.48**, which is the
whole of that day's -$94.18 and then some. A single bad read of one market type became the worst
day of the stretch. This is about same-day exposure shape, not about F5.

### A price with no room in it

`003d2c486e` bought SEA 5+ at **0.98** for $52.49 and lost the full stake. At that price the
position risks 98 cents to win 2 and needs roughly a 98% hit rate just to break even before fees.
Even with the game thesis right, that expression has almost no margin for error — a game total or
a lower team-total rung would have carried the same view at a price that can absorb being wrong.

### Fees

**$35.76 paid on $2,127.86 risked (1.68%)** — **46% of the entire -$77.45 loss**. At this staking
cadence fee drag is not a rounding error and belongs in the pre-bet comparison between candidate
expressions of the same thesis.

### What was not the problem

The five largest positions returned **+$144.80 on $507.49**. The biggest bets did not go wrong, so
this stretch gives no evidence of a top-end sizing failure.

## Variance

Thirty-seven wagers is a small sample. An 18-19 record at these prices is not distinguishable from
break-even play, and -3.64% ROI is well inside the range ordinary variance produces over this many
bets. Nothing here supports a conclusion about model quality in either direction.

## Settlement note

`913466ffb7` (`KXMLBKS-26SEP201607MINLAA-LAARJOHNSON32-5`, NO on 5+ strikeouts) was correctly
graded **LOSS**. Ryan Johnson recorded **6 strikeouts** in 4.2 innings — MLB Stats API gamePk
823975, playerId 696270, jersey 32 LAA, unique candidate match, participation RESOLVED. An
external note claiming 2 strikeouts was wrong; canonical settlement recovered the real line from
the authoritative feed, and it was independently confirmed.

All 37 wagers were captured automatically by the Kalshi router as authenticated execution receipts
— 37 unique betIds, 37 unique sourceBetKeys, `importBatchId=kalshi-router-v1`. No screenshot
handoff, no manual reconstruction, no manual importer invoked.

On 2026-09-17 the ledger also holds three `LEGACY_BACKFILL` model rows (`2378a0c45d`,
`8e7b4739cc`, `f4b9f2d663`). Canonical settlement **refused** rather than defaulted them. They are
not wagers and appear in no figure above.

## CLV — not usable yet

**No CLV figure in this window may be used to judge model quality.** The 100x price-unit defect was
fixed prospectively in PR #230, but **57 of the 94 affected historical rows still take their
"closing" quote from the FIRST_DAILY checkpoint**, which is not closing-line evidence. Historical
CLV was deliberately not migrated here and remains unsuitable for performance conclusions.

## Process changes

1. **Cap correlated exposure per game, across all families.** Measured cost this stretch: -$164.88
   on 25.6% of capital. A per-market check does not catch it.
2. **Keep comparing expressions before placing.** Price the same thesis as ML, F5, F7, game total,
   team total and supported props, and record why the chosen one won. The 0.98 entry is the
   concrete case where a cheaper expression of the same view existed.
3. **Carry fee drag into that comparison explicitly.** 1.68% of capital, 46% of the loss.
4. **Flag when one family takes more than about half a single day's wagers** (09-18: five of eight).
   The aim is exposure shape, not avoiding a family.
5. **Fix FIRST_DAILY closing-quote selection before any historical CLV migration.** Tracked
   separately and explicitly out of scope here.
