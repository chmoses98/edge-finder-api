# MLB postmortem — 2026-09-04

**A profitable day whose biggest leak was triple exposure to a single Houston thesis.**

## User-confirmed day (as supplied)

- Positions: 15
- Record: 8-7
- Risk: $544.97
- Realized return: $600.72
- P/L: +55.75
- ROI: +10.23%

## Canonical ledger for this date (what was actually written)

- Canonical wagers written: **15 of 15**
- Import batch: `mlb-manual-2026-09-04-postmortem-v1`
- Canonical record (user-confirmed receipts): **8-7**
- Canonical risk: $544.97 · realized return: $600.72 · P/L: +55.75 · ROI: +10.23%

## Wagers

| Market | Ticker | Side | Stake | Entry (displayed) | Result | Gross return | Net P/L |
|---|---|---|---|---|---|---|---|
| Detroit wins first 5 innings (game 1) | `KXMLBF5-26SEP041410DETCLEG1-DET` | YES | $20.00 | 40% | LOSS | $0.00 | -20.00 |
| Detroit 4+ runs (over 3.5, game 1) | `KXMLBTEAMTOTAL-26SEP041410DETCLEG1-DET4` | YES | $24.99 | 55% | WIN | $44.74 | +19.75 |
| ATL/PHI first 5 innings NO on over 4.5 | `KXMLBF5TOTAL-26SEP041840ATLPHI-5` | NO | $35.00 | 70% | LOSS | $0.00 | -35.00 |
| Pittsburgh 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP041845LAAPIT-PIT4` | YES | $44.99 | 66% | LOSS | $0.00 | -44.99 |
| MIN/CWS game total over 8.5 | `KXMLBTOTAL-26SEP041940MINCWS-9` | YES | $40.00 | 57% | LOSS | $0.00 | -40.00 |
| Chicago Cubs 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP041910CHCMIA-CHC5` | YES | $45.00 | 49% | WIN | $90.22 | +45.22 |
| Milwaukee 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP041810MILCIN-MIL5` | YES | $40.00 | 59% | WIN | $65.90 | +25.90 |
| TOR/KC game total over 9.5 | `KXMLBTOTAL-26SEP042010TORKC-10` | YES | $35.00 | 54% | WIN | $63.78 | +28.78 |
| Houston wins first 5 innings | `KXMLBF5-26SEP042010AZHOU-HOU` | YES | $35.00 | 46% | WIN | $74.67 | +39.67 |
| Houston 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP042010AZHOU-HOU5` | YES | $35.00 | 48% | LOSS | $0.00 | -35.00 |
| Houston 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP042010AZHOU-HOU4` | YES | $35.00 | 61% | LOSS | $0.00 | -35.00 |
| Tampa Bay moneyline | `KXMLBGAME-26SEP042005TBTEX-TB` | YES | $20.00 | 52% | WIN | $37.82 | +17.82 |
| St. Louis 6+ runs (over 5.5) | `KXMLBTEAMTOTAL-26SEP042040STLCOL-STL6` | YES | $40.00 | 53% | WIN | $74.25 | +34.25 |
| New York Yankees 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP042140NYYSD-NYY4` | YES | $25.00 | 52% | LOSS | $0.00 | -25.00 |
| Seattle 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP042210ATHSEA-SEA5` | YES | $69.99 | 46% | WIN | $149.34 | +79.35 |

CLV: not available for any of these wagers — no closing-quote linkage has been computed for this date, and none was fabricated.

## Market-family breakdown (as supplied)

| Family | Record | Net P/L |
|---|---|---|
| team_total | 5-4 | +64.48 |
| inning_result (F5) | 1-1 | +19.67 |
| inning_total (F5) | 0-1 | -35.00 |
| game_total | 1-1 | -11.22 |
| game_result | 1-0 | +17.82 |

## Analytical wins

- **det-tt-more-robust-than-f5** — The Detroit team total was a more robust expression than the Detroit F5 side.

## Analytical misses

- **pit-tt-min-cws-genuine-misses** — The PIT team total and the MIN/CWS over were genuine scoring-thesis misses.
- **atlphi-f5-one-run-threshold-miss** — ATL/PHI F5 total was a tight one-run threshold miss -- but the loss still counts fully.

## Process errors

- **hou-triple-exposure** — Biggest leak of the day: Houston triple exposure -- HOU F5 + HOU 5+ + HOU 4+ = $105 on one core thesis, net -30.33.
- **sea-oversized-despite-win** — SEA 5+ won, but the $69.99 stake was oversized. Winning does not validate oversized exposure.
- **one-thesis-one-expression-rule** — One thesis -> one best expression should become the portfolio rule.

## Proposed investigations

- **one-thesis-exposure-cap** — Cap combined exposure on one game thesis; HOU F5 + HOU 5+ + HOU 4+ was $105 on a single core thesis.

## Process grade: B

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
    "betId": "191a0b6c1ccaa78c244db40bf92c228420e5b7ef",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -20.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.4,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_DET_CLE_1410",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "inning_result",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5-26SEP041410DETCLEG1-DET",
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
    "selection": "Detroit wins first 5 innings (game 1)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|DET-CLE-G1|F5_SIDE|DET_YES|20.00|40",
    "stake": 20.0,
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
    "betId": "263f1d0d23718ef19514ffc118d65794bcbe144b",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 19.75,
    "confirmedReceiptReturn": 44.74,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.55,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_DET_CLE_1410",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP041410DETCLEG1-DET4",
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
    "selection": "Detroit 4+ runs (over 3.5, game 1)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|DET-CLE-G1|DET_TEAM_TOTAL|OVER_3.5|24.99|55",
    "stake": 24.99,
    "thesisTags": [],
    "threshold": 3.5,
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
    "betId": "e24809579b56404f3e417f4b7c7be4d5e12b392a",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -35.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.7,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_ATL_PHI_1840",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP041840ATLPHI-5",
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
    "selection": "ATL/PHI first 5 innings NO on over 4.5",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|ATL-PHI|F5_TOTAL|NO_OVER_4.5|35.00|70",
    "stake": 35.0,
    "thesisTags": [],
    "threshold": 5,
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
    "betId": "749b4b2bfaf5831e257a5bda1dca8e294a7ac191",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -44.99,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.66,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_LAA_PIT_1845",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP041845LAAPIT-PIT4",
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
    "selection": "Pittsburgh 4+ runs (over 3.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|LAA-PIT|PIT_TEAM_TOTAL|OVER_3.5|44.99|66",
    "stake": 44.99,
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
    "betId": "80ca4a8b385e576210a9204ee96ed8c0d2a02614",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -40.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.57,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_MIN_CWS_1940",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "game_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTOTAL-26SEP041940MINCWS-9",
    "matchup": "MIN @ CWS",
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
    "selection": "MIN/CWS game total over 8.5",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|MIN-CWS|GAME_TOTAL|OVER_8.5|40.00|57",
    "stake": 40.0,
    "thesisTags": [],
    "threshold": 9,
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
    "betId": "b65dcc972d963898e423af731388aa6d465fb5bc",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 45.22,
    "confirmedReceiptReturn": 90.22,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.49,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_CHC_MIA_1910",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP041910CHCMIA-CHC5",
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
    "sourceBetKey": "2026-09-04|CHC-MIA|CHC_TEAM_TOTAL|OVER_4.5|45.00|49",
    "stake": 45.0,
    "thesisTags": [],
    "threshold": 4.5,
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
    "betId": "455fb91bd088fa4983ecf8d3e5b26a56675e6f90",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 25.9,
    "confirmedReceiptReturn": 65.9,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.59,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_MIL_CIN_1810",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP041810MILCIN-MIL5",
    "matchup": "MIL @ CIN",
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
    "selection": "Milwaukee 5+ runs (over 4.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|MIL-CIN|MIL_TEAM_TOTAL|OVER_4.5|40.00|59",
    "stake": 40.0,
    "thesisTags": [],
    "threshold": 4.5,
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
    "betId": "297f97c81d43cd7b7ef9059090a322ab2657b3e6",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 28.78,
    "confirmedReceiptReturn": 63.78,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.54,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_TOR_KC_2010",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "game_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTOTAL-26SEP042010TORKC-10",
    "matchup": "TOR @ KC",
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
    "selection": "TOR/KC game total over 9.5",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|TOR-KC|GAME_TOTAL|OVER_9.5|35.00|54",
    "stake": 35.0,
    "thesisTags": [],
    "threshold": 10,
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
    "betId": "0e69412563df0a866c069eb04aa8d599a70e8957",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 39.67,
    "confirmedReceiptReturn": 74.67,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.46,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_AZ_HOU_2010",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "inning_result",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5-26SEP042010AZHOU-HOU",
    "matchup": "AZ @ HOU",
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
    "selection": "Houston wins first 5 innings",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|AZ-HOU|F5_SIDE|HOU_YES|35.00|46",
    "stake": 35.0,
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
    "betId": "14c27078c6c1197d296d1227f98ca7631d570972",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -35.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.48,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_AZ_HOU_2010",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP042010AZHOU-HOU5",
    "matchup": "AZ @ HOU",
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
    "selection": "Houston 5+ runs (over 4.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|AZ-HOU|HOU_TEAM_TOTAL|OVER_4.5|35.00|48",
    "stake": 35.0,
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
    "betId": "fae3519078775faeb163f09753af2f4ebe011ac1",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -35.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.61,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_AZ_HOU_2010",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP042010AZHOU-HOU4",
    "matchup": "AZ @ HOU",
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
    "selection": "Houston 4+ runs (over 3.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|AZ-HOU|HOU_TEAM_TOTAL|OVER_3.5|35.00|61",
    "stake": 35.0,
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
    "betId": "7987bac772f9c538a393dc12e031a76daf016f12",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 17.82,
    "confirmedReceiptReturn": 37.82,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.52,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_TB_TEX_2005",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "game_result",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBGAME-26SEP042005TBTEX-TB",
    "matchup": "TB @ TEX",
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
    "selection": "Tampa Bay moneyline",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-04|TB-TEX|TB_ML|YES|20.00|52",
    "stake": 20.0,
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
    "betId": "8f53d87a11634b519dbd0906e2ba04f03748946e",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 34.25,
    "confirmedReceiptReturn": 74.25,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.53,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_STL_COL_2040",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP042040STLCOL-STL6",
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
    "sourceBetKey": "2026-09-04|STL-COL|STL_TEAM_TOTAL|OVER_5.5|40.00|53",
    "stake": 40.0,
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
    "betId": "9074197a2628d5ff1840063bfc02b4a21993e6f0",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -25.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.52,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_NYY_SD_2140",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP042140NYYSD-NYY4",
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
    "sourceBetKey": "2026-09-04|NYY-SD|NYY_TEAM_TOTAL|OVER_3.5|25.00|52",
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
    "betId": "86f6032190bdcca3cbdeadce79e66f25cd941776",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 79.35,
    "confirmedReceiptReturn": 149.34,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-04",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.46,
    "eventTicker": null,
    "gameDate": "2026-09-04",
    "gameId": "2026-09-04_ATH_SEA_2210",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-04-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP042210ATHSEA-SEA5",
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
    "sourceBetKey": "2026-09-04|ATH-SEA|SEA_TEAM_TOTAL|OVER_4.5|69.99|46",
    "stake": 69.99,
    "thesisTags": [],
    "threshold": 4.5,
    "unresolvedFieldReasons": {
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "WIN",
    "validationStatus": "valid"
  }
]
```

