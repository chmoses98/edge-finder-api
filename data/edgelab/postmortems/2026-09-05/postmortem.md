# MLB postmortem — 2026-09-05

**User-confirmed actual subset only: one winning position of three.**

**SCOPE: USER-CONFIRMED ACTUAL SUBSET ONLY.** These three wagers are the only user-confirmed actual wagers supplied for 2026-09-05. This is not evidence that they were the user's complete day, and this postmortem does not claim to be one.

**Shadow card excluded.** Six 2026-09-05 positions were manual SHADOW bets and were deliberately NOT imported into the real-money canonical ledger by this import: CHC/MIA F5 5+ @ 48 (shadow $30), PHI by 2+ @ 41 (shadow $30), MIL 5+ @ 56 (shadow $30), CLE F5 YES ~47-48 (shadow $25), WSH/LAD F5 <=4 ~51 (shadow $25), SEA 5+ @ 50 (shadow $35). They may remain research/shadow evidence only.

## User-confirmed day (as supplied)

- Positions: 3
- Record: 1-2
- Risk: $69.99
- Realized return: $58.33
- P/L: -11.66
- ROI: -16.66%

## Canonical ledger for this date (what was actually written)

- Canonical wagers written: **3 of 3**
- Import batch: `mlb-manual-2026-09-05-postmortem-v1`
- Canonical record (user-confirmed receipts): **1-2**
- Canonical risk: $69.99 · realized return: $58.33 · P/L: -11.66 · ROI: -16.66%

## Wagers

| Market | Ticker | Side | Stake | Entry (displayed) | Result | Gross return | Net P/L |
|---|---|---|---|---|---|---|---|
| Cleveland wins first 5 innings | `KXMLBF5-26SEP051810DETCLE-CLE` | YES | $20.00 | 49% | LOSS | $0.00 | -20.00 |
| Philadelphia wins by 2+ runs (over 1.5) | `KXMLBSPREAD-26SEP051805ATLPHI-PHI2` | YES | $25.00 | 42% | WIN | $58.33 | +33.33 |
| Milwaukee 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP051840MILCIN-MIL5` | YES | $24.99 | 55% | LOSS | $0.00 | -24.99 |

CLV: not available for any of these wagers — no closing-quote linkage has been computed for this date, and none was fabricated.

## Analytical wins

- **phi-margin-only-winner** — PHI by 2+ was the one winning actual position of the confirmed subset.

## Analytical misses

- **cle-f5-and-mil-tt-lost** — CLE F5 and MIL 5+ both lost.

## Process errors

- **shadow-card-must-stay-separate** — Do not contaminate actual-performance reports with the six-position manual shadow card. The shadow card belongs in research/shadow evidence only and was NOT imported into the real-money canonical ledger by this import.

## Proposed investigations

- **keep-shadow-separate** — Keep the 2026-09-05 shadow card as clearly-labelled non-real-money research evidence, separate from actual performance reporting.
- **confirm-complete-day** — This is a user-confirmed SUBSET. Confirm whether 2026-09-05 had further actual wagers before treating these three as the complete day.

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
    "betId": "d4d45cbfdccac059c4cf2acc1f174bb5a4295a79",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -20.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-05",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.49,
    "eventTicker": null,
    "gameDate": "2026-09-05",
    "gameId": "2026-09-05_DET_CLE_1810",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-05-postmortem-v1",
    "marketFamily": "inning_result",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5-26SEP051810DETCLE-CLE",
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
    "selection": "Cleveland wins first 5 innings",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-05|DET-CLE|F5_SIDE|CLE_YES|20.00|49",
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
    "betId": "5eede8200786004ecf1faac58d8ef548c4a35e31",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 33.33,
    "confirmedReceiptReturn": 58.33,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-05",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.42,
    "eventTicker": null,
    "gameDate": "2026-09-05",
    "gameId": "2026-09-05_ATL_PHI_1805",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-05-postmortem-v1",
    "marketFamily": "winning_margin",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBSPREAD-26SEP051805ATLPHI-PHI2",
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
    "selection": "Philadelphia wins by 2+ runs (over 1.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-05|ATL-PHI|PHI_MARGIN|WIN_BY_2_PLUS|25.00|42",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": 1.5,
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
    "betId": "a13d73068b982d89d69c91fe479817b8f45978c6",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -24.99,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-05",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.55,
    "eventTicker": null,
    "gameDate": "2026-09-05",
    "gameId": "2026-09-05_MIL_CIN_1840",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-05-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP051840MILCIN-MIL5",
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
    "sourceBetKey": "2026-09-05|MIL-CIN|MIL_TEAM_TOTAL|OVER_4.5|24.99|55",
    "stake": 24.99,
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

