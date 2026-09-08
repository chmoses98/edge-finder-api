# MLB postmortem — 2026-09-06

**A losing day for team totals, with one exceptional user-directed rollover winning.**

## User-confirmed day (as supplied)

- Positions: 8
- Record: 3-5
- Risk: $309.99
- Realized return: $278.14
- P/L: -31.85
- ROI: -10.27%

## Canonical ledger for this date (what was actually written)

- Canonical wagers written: **8 of 8**
- Import batch: `mlb-manual-2026-09-06-postmortem-v1`
- Canonical record (user-confirmed receipts): **3-5**
- Canonical risk: $309.99 · realized return: $278.14 · P/L: -31.85 · ROI: -10.27%

## Wagers

| Market | Ticker | Side | Stake | Entry (displayed) | Result | Gross return | Net P/L |
|---|---|---|---|---|---|---|---|
| Atlanta wins first 5 innings | `KXMLBF5-26SEP061310ATLPHI-ATL` | YES | $25.00 | 42% | LOSS | $0.00 | -25.00 |
| LAA/PIT first 5 innings NO on over 3.5 (3 or fewer first-five runs) | `KXMLBF5TOTAL-26SEP061335LAAPIT-4` | NO | $25.00 | 48% | WIN | $51.15 | +26.15 |
| Boston moneyline | `KXMLBGAME-26SEP061335BOSBAL-BOS` | YES | $24.99 | 55% | WIN | $44.74 | +19.75 |
| Cleveland 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP061340DETCLE-CLE4` | YES | $25.00 | 53% | LOSS | $0.00 | -25.00 |
| Chicago Cubs 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP061340CHCMIA-CHC5` | YES | $40.00 | 49% | LOSS | $0.00 | -40.00 |
| St. Louis 6+ runs (over 5.5) | `KXMLBTEAMTOTAL-26SEP061510STLCOL-STL6` | YES | $100.00 | 54% | WIN | $182.25 | +82.25 |
| New York Yankees 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP061610NYYSD-NYY4` | YES | $40.00 | 48% | LOSS | $0.00 | -40.00 |
| Seattle 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP061610ATHSEA-SEA5` | YES | $30.00 | 44% | LOSS | $0.00 | -30.00 |

CLV: not available for any of these wagers — no closing-quote linkage has been computed for this date, and none was fabricated.

## Market-family breakdown (as supplied)

| Family | Record | Net P/L |
|---|---|---|
| team_total | 1-4 | -52.75 |
| inning_total (F5) | 1-0 | +26.15 |
| game_result | 1-0 | +19.75 |
| inning_result (F5) | 0-1 | -25.00 |

## Analytical wins

- **stl-best-result** — STL 6+ was the day's best result and an independently justified scoring expression.
- **laapit-f5-under-expression** — LAA/PIT F5 <=3 was excellent expression selection and avoided late-game variance.

## Analytical misses

- **atl-f5-handicap-miss** — ATL F5 was a handicap miss: Atlanta eventually won but did not lead after five.
- **team-totals-1-4-not-abandonment** — Team totals went 1-4 (CLE 4+, CHC 5+, NYY 4+, SEA 5+ all lost), but one day's result does NOT justify abandoning the family. The correct response is a tighter probability gate, not removing team totals from consideration.

## Process errors

- **stl-rollover-sizing-exceptional** — The $100 STL rollover was exceptional user-directed sizing and should not become normal bankroll practice merely because it won.
- **protected-f5-semantics-check** — Protected/F5 contract semantics must always be explicitly checked before recommendation.
- **one-thesis-one-expression-mandatory** — One thesis -> one best expression remains mandatory.

## Proposed investigations

- **tighter-team-total-gate** — Tighten the team-total probability gate rather than removing the family from consideration after one poor day.
- **explicit-contract-semantics-check** — Add an explicit protected/F5 contract-semantics check before any recommendation.

## Process grade: C+

## Research notes (POSTMORTEM_STANDARD §4B)

The per-wager analytical notes actually on record for this date are the analytical wins / misses / process errors above, each linked to its real canonical betId. No decision-time rationale (starting-pitcher edge, bullpen, lineup, weather, park, umpire, price discrepancy, why this market expression was chosen) was supplied for the remaining wagers, and none has been backfilled: a plausible-sounding justification invented after the fact is a policy violation regardless of how reasonable it looks. Each canonical row carries only a provenance `rationale` recording how it was imported and how its stake and entry price were determined — never a reconstructed thesis.

## Settlement and economics provenance

Every realized return above comes from the user's own confirmed receipt, recorded through the one sanctioned manual path (`lib.edgelab.bets.confirm_realized_return`, `confirmedReceiptSource: MANUAL_POSTMORTEM_RECEIPT`). That path deliberately never writes `result` / `status` / `returnAmount` / `netProfitLoss` — those remain the automatic settlement pipeline's own objective fields. The automatic settlement pipeline (`scripts/edgelab/settle_markets.py`) was **not** run as part of this import, so every wager on this date is still canonically `pending` and this postmortem's strict `dailyRecord` / `totalReturned` / `roiPct` blocks (settlement-only view) read zero. The `realizedEconomics` block is the user-confirmed view and reconciles exactly to the table above. Running the normal postgame workflow later will settle these rows and populate the objective fields; `confirmedReceiptSettlementComparison` will then surface any disagreement rather than silently overwriting either side.

Entry prices are the user's **displayed Kalshi percentage**, recorded verbatim. They are not fee-adjusted and were not recomputed from cost, max payout, realized return or fees. Stakes are the user's explicitly supplied wager amounts, not share-card initial costs.

No recommendation or model linkage was attached to any wager on this date: the Recommendation ledger has no rows for 2026-09-02 through 2026-09-07, so every `recommendationId` and `modelEvaluationId` is legitimately `null`. None was invented.

## Cross-day principles preserved

1. **Every market family must continue to be evaluated**: moneyline, winning margins, F3, F5
   three-way, protected F5 NO expressions, F5 totals, F5 spreads, F7, full-game totals, full team-
   total ladders, NRFI/YRFI, pitcher props, hitter props.
2. **Do not remove team totals from consideration because of one poor day.** They have recently
   produced meaningful positive results, but the manual sample remains small.
3. **F3/F5/F7 should compete as expressions.** Do not default to F5 because it is familiar.
4. **Player props must remain part of the scan.** Workload and role are central to pitcher-prop
   pricing.
5. **F5 three-way YES requires an actual lead. A tie loses.** Explicitly price the tie tax.
6. **One thesis -> one best expression.** Avoid side + TT + alternate TT + combo duplication unless
   each leg has genuinely independent value.
7. **Fractional Kelly**: quarter Kelly is the current default, at the conservative end of the manual
   P range. Do not move to half Kelly yet. Hard portfolio discipline matters more than nominal Kelly
   formulas.
8. **Winning does not validate** oversizing, paying above max price, duplicate exposure, or poor
   probability estimation.
9. **Losing does not by itself invalidate** team totals, F7, props, or any other family. Process
   quality and price quality must be evaluated separately from outcome.

## Section C — Claude-ready canonical-ledger state

Current canonical state of every wager in this date's manifest, in `PlacedBet` field names. A blocked row carries `betId: null` and a `unresolvedFieldReasons` entry naming exactly what would resolve it — never a placeholder value.

```json
[
  {
    "betId": "28d5fa5c3bcb1ccbd4cb93dbf356986a7e352d86",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -25.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-06",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.42,
    "eventTicker": null,
    "gameDate": "2026-09-06",
    "gameId": "2026-09-06_ATL_PHI_1310",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-06-postmortem-v1",
    "marketFamily": "inning_result",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5-26SEP061310ATLPHI-ATL",
    "matchup": "ATL @ PHI",
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Imported from the user-confirmed wager manifest; stake is the user's explicitly supplied wager amount and entryPrice is the user-reported displayed Kalshi percentage, verbatim.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "Atlanta wins first 5 innings",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-06|ATL-PHI|F5_SIDE|ATL_YES|25.00|42",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": null,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "valid"
  },
  {
    "betId": "044b41857e5bd02ba92a406835ed6fb54c562b80",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 26.15,
    "confirmedReceiptReturn": 51.15,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-06",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.48,
    "eventTicker": null,
    "gameDate": "2026-09-06",
    "gameId": "2026-09-06_LAA_PIT_1335",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-06-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP061335LAAPIT-4",
    "matchup": "LAA @ PIT",
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Imported from the user-confirmed wager manifest; stake is the user's explicitly supplied wager amount and entryPrice is the user-reported displayed Kalshi percentage, verbatim.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "LAA/PIT first 5 innings NO on over 3.5 (3 or fewer first-five runs)",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-06|LAA-PIT|F5_TOTAL|NO_OVER_3.5|25.00|48",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": 4,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "WIN",
    "validationStatus": "valid"
  },
  {
    "betId": "819b8ef6342dde15b996439145c3e96fc318d320",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 19.75,
    "confirmedReceiptReturn": 44.74,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-06",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.55,
    "eventTicker": null,
    "gameDate": "2026-09-06",
    "gameId": "2026-09-06_BOS_BAL_1335",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-06-postmortem-v1",
    "marketFamily": "game_result",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBGAME-26SEP061335BOSBAL-BOS",
    "matchup": "BOS @ BAL",
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Imported from the user-confirmed wager manifest; stake is the user's explicitly supplied wager amount and entryPrice is the user-reported displayed Kalshi percentage, verbatim.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "Boston moneyline",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-06|BOS-BAL|BOS_ML|YES|24.99|55",
    "stake": 24.99,
    "thesisTags": [],
    "threshold": null,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "WIN",
    "validationStatus": "valid"
  },
  {
    "betId": "06ab909900107891c0efad25affd94601bf3d989",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -25.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-06",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.53,
    "eventTicker": null,
    "gameDate": "2026-09-06",
    "gameId": "2026-09-06_DET_CLE_1340",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-06-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP061340DETCLE-CLE4",
    "matchup": "DET @ CLE",
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Imported from the user-confirmed wager manifest; stake is the user's explicitly supplied wager amount and entryPrice is the user-reported displayed Kalshi percentage, verbatim.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "Cleveland 4+ runs (over 3.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-06|DET-CLE|CLE_TEAM_TOTAL|OVER_3.5|25.00|53",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": 3.5,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "valid"
  },
  {
    "betId": "58b1743fb1c54af0d30019547e5626980db6b551",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -40.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-06",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.49,
    "eventTicker": null,
    "gameDate": "2026-09-06",
    "gameId": "2026-09-06_CHC_MIA_1340",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-06-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP061340CHCMIA-CHC5",
    "matchup": "CHC @ MIA",
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Imported from the user-confirmed wager manifest; stake is the user's explicitly supplied wager amount and entryPrice is the user-reported displayed Kalshi percentage, verbatim.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "Chicago Cubs 5+ runs (over 4.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-06|CHC-MIA|CHC_TEAM_TOTAL|OVER_4.5|40.00|49",
    "stake": 40.0,
    "thesisTags": [],
    "threshold": 4.5,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "valid"
  },
  {
    "betId": "396e21f0e552a50e295811670bbdffe8703f70e9",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 82.25,
    "confirmedReceiptReturn": 182.25,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-06",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.54,
    "eventTicker": null,
    "gameDate": "2026-09-06",
    "gameId": "2026-09-06_STL_COL_1510",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-06-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP061510STLCOL-STL6",
    "matchup": "STL @ COL",
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Imported from the user-confirmed wager manifest; stake is the user's explicitly supplied wager amount and entryPrice is the user-reported displayed Kalshi percentage, verbatim.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "St. Louis 6+ runs (over 5.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-06|STL-COL|STL_TEAM_TOTAL|OVER_5.5|100.00|54",
    "stake": 100.0,
    "thesisTags": [],
    "threshold": 5.5,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "WIN",
    "validationStatus": "valid"
  },
  {
    "betId": "298519734a47fcc6536959944d6624049d32450b",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -40.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-06",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.48,
    "eventTicker": null,
    "gameDate": "2026-09-06",
    "gameId": "2026-09-06_NYY_SD_1610",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-06-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP061610NYYSD-NYY4",
    "matchup": "NYY @ SD",
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Imported from the user-confirmed wager manifest; stake is the user's explicitly supplied wager amount and entryPrice is the user-reported displayed Kalshi percentage, verbatim.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "New York Yankees 4+ runs (over 3.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-06|NYY-SD|NYY_TEAM_TOTAL|OVER_3.5|40.00|48",
    "stake": 40.0,
    "thesisTags": [],
    "threshold": 3.5,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "valid"
  },
  {
    "betId": "bf585cb2e092d9a217f4d0289cc54df3313e08de",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -30.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-06",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.44,
    "eventTicker": null,
    "gameDate": "2026-09-06",
    "gameId": "2026-09-06_ATH_SEA_1610",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-06-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP061610ATHSEA-SEA5",
    "matchup": "ATH @ SEA",
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Imported from the user-confirmed wager manifest; stake is the user's explicitly supplied wager amount and entryPrice is the user-reported displayed Kalshi percentage, verbatim.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "Seattle 5+ runs (over 4.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-06|ATH-SEA|SEA_TEAM_TOTAL|OVER_4.5|30.00|44",
    "stake": 30.0,
    "thesisTags": [],
    "threshold": 4.5,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "valid"
  }
]
```

