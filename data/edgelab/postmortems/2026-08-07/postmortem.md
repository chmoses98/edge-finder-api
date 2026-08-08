# MLB Betting Postmortem — August 7, 2026

## Results

Five user-confirmed wagers were placed.

Record: 2-3
Risked: $59.08
Returned: $49.48
Net P/L: -$9.60
ROI: -16.25%

Full-game ML finished 2-1 for +$8.11 on $41.37 risked.
YRFI finished 0-2 for -$17.71.

## Analytical wins

Washington ML was the strongest process and result of the slate.

The handicap identified a major starting-pitcher disparity between Cade Cavalli and Chase Petty. Petty entered with substantially weaker xERA/xFIP and strikeout ability. In the actual game, Petty allowed three first-inning runs and failed to complete two innings, while Cavalli worked into the seventh with eight strikeouts. Washington won 5-3.

The Yankees full-game ML also won, 3-2. Choosing the full-game contract instead of a three-way F5 team contract avoided unnecessary five-inning tie risk and retained the Yankees bullpen as part of the thesis.

## Analytical misses

Milwaukee ML was the largest side miss.

The slate/model estimated Milwaukee around 72%, far above the approximately 62-63% Kalshi market and roughly 61-62% sharp-market probability. The manual handicap reduced the estimate substantially but still played Milwaukee.

Milwaukee led 4-0 after two innings, but Shane Drohan allowed six runs through five innings and Milwaukee eventually lost 8-6.

This should reinforce the existing rule that a model/market disagreement greater than seven percentage points is an AUDIT FLAG rather than automatic value. Where the divergence cannot be independently explained, the correct action may be to pass even when the baseball thesis initially appears reasonable.

Both YRFI wagers lost.

COL-STL was 0-0 after the first inning.
LAD-ARI was 0-0 after the first inning.

The LAD-ARI game eventually finished 4-3, demonstrating that a high full-game scoring environment does not necessarily create a high first-inning scoring probability.

The pregame slate explicitly lacked first-inning-specific xERA inputs for YRFI evaluation and used proxy/full-game information. The manual analysis nevertheless leaned heavily on full-game starting-pitcher xERA/xFIP, lineup strength, and projected full-game scoring.

That was too much reliance on non-first-inning-specific evidence.

## Process errors / workflow findings

1. The independent baseball handicap was too anchored to the slate/model outputs.

The desired analyst workflow is:
- handicap the matchup independently first,
- establish a fair probability where practical,
- then compare the independent view with the model and Kalshi market.

The August 7 analysis allowed model projections to become too influential, particularly in large model/market disagreement games.

2. YRFI/NRFI should be materially downgraded when first-inning-specific inputs are unavailable.

Full-game pitcher xERA/xFIP, park, lineup quality, and projected full-game total are not adequate substitutes for:
- first-inning pitcher splits,
- top-of-order matchup quality,
- first-inning team scoring rates,
- first-inning walk/strikeout profiles,
- first-inning pitch efficiency,
- first-inning park/environment data where available.

Do not treat full-game scoring environment alone as sufficient YRFI evidence.

3. Executable Kalshi pricing was displayed incorrectly.

All five bets exposed the same presentation issue.

The analysis displayed American odds derived from midpoint probabilities rather than the executable YES ask.

Examples from the archived August 7 slate:

COL-STL YRFI:
bid 52¢
ask 53¢
mid 52.5%
displayed American approximately -111
actual executable purchase: 53¢, approximately -113

LAD-ARI YRFI:
bid 54¢
ask 55¢
mid 54.5%
displayed American approximately -120
actual executable purchase: 55¢, approximately -122

MIN-MIL Milwaukee ML:
bid 62¢
ask 63¢
mid 62.5%
displayed American approximately -167
actual executable purchase: 63¢, approximately -170

Washington ML and Yankees ML showed the same one-cent midpoint-versus-ask discrepancy.

For betting recommendations, "current odds" must reflect the price the user can actually execute.

For a YES purchase:
- current executable probability/price must use YES ask.
- American odds displayed to the user must be converted from YES ask.
- bet-up-to enforcement must compare against YES ask.

For a NO purchase:
- use the actual executable NO-side price from the order book/canonical market representation, not a midpoint approximation.

Bid, ask, midpoint and last price should remain archived separately for research and CLV analysis.

Do not silently substitute midpoint for executable price.

## Proposed investigations

- Audit every recommendation/output path that creates the user-facing "current American odds" field and ensure it uses executable ask rather than midpoint.
- Audit bet-up-to enforcement for the same issue.
- Preserve bid / ask / midpoint / last as distinct fields.
- Determine whether historical recommended-price fields need an explicit priceBasis such as EXECUTABLE_ASK, MIDPOINT, LAST, or VIG_FREE.
- Review YRFI/NRFI qualification when first-inning-specific data is unavailable.
- Review large model-versus-sharp-market divergences to determine whether an additional real-money confirmation gate is warranted.
- Continue the already identified ticker-mapping cleanup so every pipeline market key maps to the exact archived Kalshi ticker and field states distinguish not_applicable, not_computed, and parser_unresolved.
