# Aug 8, 2026 — Postmortem

## Financial result

3-5, **-$18.26** on **$100.25** risked, **-18.21% ROI** (per user-confirmed Kalshi settled-position screenshots; see "Settlement status" below for why the repository's own automated settlement had not yet independently confirmed these figures as of this import).

| # | Matchup | Selection | Side | Stake | Entry | Result | Return | Net P/L |
|---|---|---|---|---|---|---|---|---|
| 1 | ATH @ BOS | Boston wins first 5 innings | YES | $21.69 | 0.60 | LOSS | $0.00 | -$21.69 |
| 2 | ATL @ NYY | Chris Sale 8+ strikeouts | YES | $11.83 | 0.61 | WIN | $19.14 | +$7.31 |
| 3 | ATH @ BOS | Athletics 4+ runs (NO) | NO | $13.77 | 0.54 | LOSS | $0.00 | -$13.77 |
| 4 | CIN @ WSH | Cincinnati wins first 3 innings | YES | $3.92 | 0.44 | LOSS | $0.00 | -$3.92 |
| 5 | TOR @ PHI | Toronto wins first 5 innings | YES | $14.67 | 0.35 | WIN | $40.99 | +$26.32 |
| 6 | CLE @ CWS | Cleveland wins first 5 innings | YES | $14.73 | 0.49 | LOSS | $0.00 | -$14.73 |
| 7 | CIN @ WSH | Washington full-game winner | YES | $9.80 | 0.44 | WIN | $21.86 | +$12.06 |
| 8 | TB @ SEA | Seattle full-game winner | YES | $9.84 | 0.54 | LOSS | $0.00 | -$9.84 |

## The core issue: process, not just variance

The largest problem of the night was **process, not variance**. ChatGPT was instructed to analyze every available Kalshi market for every eligible game before allocating bankroll. It produced a **$50 allocation before completing that comprehensive full-market review**, and the user placed wagers before ChatGPT later disclosed the review had been incomplete. That incomplete-review incident is preserved here as a workflow failure, not a footnote — it directly explains why Boston F5 ($21.69, the night's largest stake) went out ahead of Toronto F5 ($14.67), which later turned out to have the strongest completed-slate evidence of the night.

## Positive process findings

1. **Toronto F5 (WIN)** was the strongest completed-slate wager: model probability 45.82%, executable probability 34.48%, raw edge +11.34pp, calibrated edge +2.89%, max buy 41.9c, executed at 35c.
2. **Washington ML (WIN)** was a completed-slate accepted/model-supported wager and won comfortably (final 8-2).
3. **Chris Sale 8+ Ks (WIN)** was a good manual prop selection — Sale landed exactly on eight strikeouts over six innings, validating the choice of the 8+ threshold over chasing 9+.

## Negative process findings

1. **Boston F5** was the night's largest stake despite an incomplete comparative market review at allocation time.
2. **Boston F5 + Athletics 4+ runs (NO)** were positively correlated through the same Jake Bennett/Oakland-suppression thesis — combined risk was $35.46, sized as two independent bets rather than one exposure bucket.
3. **Cleveland F5** was a manual override against the completed slate (raw edge -2.09%, calibrated edge approx -0.53%). It lost because the game was tied 0-0 through five innings (Chicago won 6-3).
4. Cleveland illustrates a **recurring three-way F5 tie-risk pattern** already observed in previous postmortems.
5. A pitcher-dominance thesis (Gavin Williams, 5.2 IP / 2 ER / 7 K) should not automatically be expressed as a team F5 winner — pitcher Ks/outs, opponent team total, F5 total, and NRFI are all more precise expressions of the same thesis.
6. **Seattle ML** was a late manual re-handicap after Griffin Jax was scratched pregame. The scratch was real information, but no fresh standalone/model rerun was captured after it before adding exposure.
7. **Stake ordering was poor**: Boston F5 ($21.69) was larger than Toronto F5 ($14.67), despite Toronto later having much stronger completed-slate evidence.

## Required workflow improvements

- Never produce a final bankroll allocation until every eligible archived Kalshi market has been screened.
- Every recommendation/bet should carry a provenance tag: `MODEL_SUPPORTED`, `MANUAL_OVERRIDE`, or `MODEL_UNAVAILABLE`.
- A `MANUAL_OVERRIDE` against a negative model edge must explicitly document its reason.
- Treat materially correlated bets as one exposure bucket for sizing.
- Explicitly model/communicate tie probability for Kalshi F3/F5 three-way winner contracts.
- Material starter scratches should trigger a fresh standalone price capture / model refresh before recommending new exposure, whenever feasible.
- Allocate bankroll in descending order of validated edge/information quality, not chronological recommendation order.
- Preserve the Aug 8 incomplete-analysis incident in workflow/postmortem reporting.
- Continue the existing high-priority engineering fix: `build_recommendations.py` must map every pipeline market key, especially F5 markets, to the exact archived Kalshi ticker, using explicit `not_applicable`/`not_computed`/`parser_unresolved` field states rather than ambiguous nulls.

### New supporting evidence for the engineering fix, found during this import

- **Market identity fragmentation**: `data/edgelab/games/2026-08-08.jsonl` carries two separate `gameId` rows for CIN@WSH, TOR@PHI, CLE@CWS, and TB@SEA — one string-form fallback id (`scheduledStartTime: null`) and one numeric `mlbGamePk`-form id. The Market dimension table splits each game's own markets across both ids inconsistently (e.g. TOR@PHI's F3 markets sit under the string-form id while its F5/game_result/F7 markets sit under the numeric id). This caused the bulk importer's automatic away/home game-resolution to pick an inconsistent `gameId` for 4 of tonight's 6 games — harmless here only because every wager's `marketTicker` was independently verified against the archive and supplied explicitly, bypassing automatic resolution.
- **Recommendation ledger gap**: `data/edgelab/recommendations/2026-08-08.jsonl` does not exist for this date, even though a frozen `pre_game_decision` snapshot does (`data/edgelab/snapshots/2026-08-08/pre_game_decision/2026-08-08T213959Z/frozen/recommendation_output.json.gz`). As a result, no bet from tonight could be automatically linked to a real `recommendationId`/`modelEvaluationId` — the model-probability figures quoted above for Toronto F5 and Washington ML are preserved as qualitative narrative only, never fabricated onto the ledger rows themselves.

## Settlement status (this import session)

`scripts/edgelab/settle_markets.py --date 2026-08-08` was run and completed without error, but could not fetch real evidence: outbound HTTPS to `statsapi.mlb.com` is blocked by this session's sandboxed egress policy. Every market observed for the date — including all 8 of tonight's bets — was left `SETTLEMENT_UNRESOLVED` with an honest reason (`game_not_final` / `missing_final_score` / `missing_period_score_*`), never a fabricated result. Each bet's user-confirmed Kalshi receipt (return / net P&L, matching the table above) was recorded separately via `lib.edgelab.bets.confirm_realized_return` (`confirmedReceiptSource: MANUAL_POSTMORTEM_RECEIPT`) — this never touches the objective `result`/`status`/`returnAmount`/`netProfitLoss` fields, which remain settlement's own. This postmortem's `canonicalTotals` will therefore show $0 returned / pending status until `settle_markets.py` is re-run from an environment with real network access (e.g. the normal `edgelab-postgame.yml` GitHub Actions workflow); the existing idempotent settlement-merge logic will then correct these rows automatically. `totalsMatch` is expected to read `false` until then — this is intentional, not a data error.

Chris Sale 8+ Ks is a pitcher-strikeout prop; GitHub issue #43 (automatic pitcher/hitter prop settlement) is closed/completed in this repository, so once real network access is available this bet is expected to settle automatically via the same `settle_markets.py` MLB Stats API boxscore fetch as every other player-prop bet — no bypass of the settlement policy was applied.

## CLV

No closing price is fabricated for any market lacking a valid archived observation.

- **Toronto F5**: entry at 35c can be compared to the slate's reported executable price of 34.48c and max buy of 41.9c — a completed-slate comparison, **not** closing CLV.
- **Seattle ML**: this import's automatic bet-to-observation linkage found a valid pregame observation for `KXMLBGAME-26AUG082150TBSEA-SEA` at 53c, captured 2026-08-08T23:35:25Z — consistent with the reported ~52c original standalone snapshot. That snapshot predates the Griffin Jax scratch, so comparing it to the eventual 54c execution price is **not** ordinary CLV; the price moved on new information (the scratch), not market drift alone.

## Correlated exposure

ATH @ BOS carried $35.46 of combined risk (Boston F5 YES $21.69 + Athletics 4+ runs NO $13.77) from the same Oakland-suppression thesis — both legs lost; both should be sized as one exposure bucket going forward, not two independent stakes.
