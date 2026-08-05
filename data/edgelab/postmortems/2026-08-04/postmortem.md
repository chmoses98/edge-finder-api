# 2026-08-04 MLB Betting Postmortem

## Summary

The card finished 2-10 for -94.68 on 123 risked, a -76.98% ROI. The largest problem was excessive exposure to three-way F5 winner contracts: six F5 bets represented 66 dollars and lost 55.62. Pittsburgh–Milwaukee Under 7.5 and Texas F5 were the two wins.

## Correct handicaps

- Pittsburgh–Milwaukee projected as a low-scoring game because of the starting pitchers and Milwaukee bullpen.
- Texas F5 correctly isolated MacKenzie Gore's starter advantage and avoided full-game bullpen exposure.
- Skubal's general run-prevention outlook was correct even though Dodgers F5 and 8+ strikeouts both lost.
- Oakland's preference for starter exposure over bullpen exposure was directionally correct; Oakland led 3-0 through four before a fifth-inning tie.

## Process failures

- The Mets–Guardians total was initially misread as though eight runs would cash. The actual contract was Under 7.5, and the stake should have been reduced or passed.
- White Sox F5 relied too heavily on an xFIP-driven model outlier and underestimated Boston's offense, Fenway and Patrick Sandoval.
- Dodgers F5 stake was increased because of excitement surrounding Skubal's Dodgers debut rather than because of increased expected value.
- Several later wagers were placed beyond the originally recommended maximum prices.
- Angels–Orioles YRFI was recommended without validated first-inning-specific inputs.
- The portfolio overused F5 winners whenever one starter appeared better, without adequately pricing offense, opponent starter quality or tie probability.

## Variance versus process

- Athletics F5 was primarily an acceptable-variance loss after Oakland led 3-0 through four and tied in the fifth.
- Skubal 8+ strikeouts was a reasonable small plus-money ceiling bet, though it was correlated with Dodgers F5.
- Angels ML contained meaningful variance because Los Angeles collected 11 hits but went 1-for-12 with runners in scoring position.
- White Sox F5, the Mets total sizing, Dodgers F5 sizing and Angels YRFI were primarily process failures.

## Market conclusions

- Reduce F5 winner exposure and cap it at 20-25% of total daily risk.
- Keep game totals available, but translate every Kalshi integer threshold into the corresponding conventional half-run line.
- Keep pitcher props small until automatic prop settlement and the research model improve.
- Require first-inning-specific inputs before placing real-money NRFI/YRFI bets.
- Treat model-versus-market disagreements above seven percentage points as audit flags rather than automatic value.

## Improvements

1. Translate every Kalshi total before displaying or pricing it.
2. Require starter edge, offensive edge, opponent-starter vulnerability and tie-adjusted value for F5 recommendations.
3. Cap F5 concentration.
4. Enforce maximum entry prices mechanically.
5. Do not increase stakes for narrative excitement.
6. Require first-inning-specific data for RFI markets.
7. Record recommendation price, actual entry price and true closing price separately.
8. Keep daily exposure closer to 25-35% of bankroll unless unusually strong independent edges exist.
9. Challenge major model-market outliers before recommending them.
10. Prefer fewer, less-correlated positions.
