# MLB postmortem — 2026-09-03

**A modestly profitable day carried by team totals, with avoidable thesis duplication.**

## Corrections in this revision (supersedes revision 1, commit 0c015448)

The 2026-09-03 combo was BLOCKED_UNSUPPORTED_COMBO in revision 1 solely because the canonical schema had no multi-leg representation. That representation now exists (wagerStructure=MULTI_LEG + embedded legs), so the wager is imported canonically and this date's canonical totals now match the user-reported day exactly.

- `2026-09-03|COMBO|SEA_ML+LAD_ML+BOS_TT_OVER_4.5|10.00` — was **BLOCKED_UNSUPPORTED_COMBO**, now **IMPORTED_AS_CANONICAL_MULTI_LEG_WAGER** as betId `e7fb8d372d3afa192c29ed4ffb8ed7251f17346d` (import batch `mlb-manual-2026-09-03-combo-v1`), stake $10.00, realized P/L -10.00.
  - leg: Seattle moneyline — KXMLBGAME-26SEP032140ATHSEA-SEA
  - leg: Los Angeles Dodgers moneyline — KXMLBGAME-26SEP032210STLLAD-LAD
  - leg: Boston over 4.5 team runs — KXMLBTEAMTOTAL-26SEP031915BOSBAL-BOS5

No per-leg outcome was recorded in the user's evidence for this combo.

The parent row owns the stake, realized return and result; its legs are carried inline and are never ledger rows, so this position is counted exactly once by bankroll, ROI and every report. Its combined executed price and CLV remain null with an explicit reason — neither is in durable evidence and neither was derived from the legs.

## User-confirmed day (as supplied)

- Positions: 11
- Record: 6-5
- Risk: $254.98
- Realized return: $273.86
- P/L: +18.88
- ROI: +7.40%

## Canonical ledger for this date (what was actually written)

- Canonical wagers written: **10 of 11** (1 blocked — see below)
- Import batch: `mlb-manual-2026-09-03-postmortem-v1`
- Canonical record (user-confirmed receipts): **6-4**
- Canonical risk: $244.98 · realized return: $273.86 · P/L: +28.88 · ROI: +11.79%

> The canonical totals differ from the user-confirmed day by exactly the blocked rows: $10.00 of risk and -10.00 of P/L. This gap is reported, never silently reconciled — the supplied manifest was not edited and no blocked wager was fabricated into the ledger.

## Wagers

| Market | Ticker | Side | Stake | Entry (displayed) | Result | Gross return | Net P/L |
|---|---|---|---|---|---|---|---|
| TOR/CLE first 5 innings NO on over 4.5 | `KXMLBF5TOTAL-26SEP031310TORCLE-5` | NO | $24.99 | 55% | LOSS | $0.00 | -24.99 |
| Pittsburgh 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP031235SFPIT-PIT5` | YES | $45.00 | 53% | WIN | $83.53 | +38.53 |
| CWS/HOU game total over 8.5 | `KXMLBTOTAL-26SEP031410CWSHOU-9` | YES | $20.00 | 51% | LOSS | $0.00 | -20.00 |
| Houston 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP031410CWSHOU-HOU5` | YES | $20.00 | 48% | WIN | $40.92 | +20.92 |
| TB/TEX first 5 innings NO on over 5.5 | `KXMLBF5TOTAL-26SEP032005TBTEX-6` | NO | $25.00 | 69% | WIN | $35.84 | +10.84 |
| Milwaukee moneyline | `KXMLBGAME-26SEP031915MILCHC-MIL` | YES | $19.99 | 55% | LOSS | $0.00 | -19.99 |
| Boston moneyline | `KXMLBGAME-26SEP031915BOSBAL-BOS` | YES | $25.00 | 53% | WIN | $46.40 | +21.40 |
| Boston 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP031915BOSBAL-BOS5` | YES | $15.00 | 47% | WIN | $31.33 | +16.33 |
| STL/LAD first 5 innings NO on over 5.5 | `KXMLBF5TOTAL-26SEP032210STLLAD-6` | NO | $25.00 | 69% | WIN | $35.84 | +10.84 |
| Seattle 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP032140ATHSEA-SEA5` | YES | $25.00 | 45% | LOSS | $0.00 | -25.00 |
| 3-leg MLB Kalshi combo: SEA ML + LAD ML + BOS team total over 4.5 | _blocked — not in ledger_ | — | $10.00 | — | LOSS (user-confirmed) | $0.00 | -10.00 |

CLV: not available for any of these wagers — no closing-quote linkage has been computed for this date, and none was fabricated.

## Market-family breakdown (as supplied)

| Family | Record | Net P/L |
|---|---|---|
| team_total | 3-1 | +50.78 |
| inning_total (F5) | 2-1 | -3.31 |
| game_result | 1-1 | +1.41 |
| game_total | 0-1 | -20.00 |
| combo | 0-1 | -10.00 — Not canonically representable -- see blockedRows. |

## Analytical wins

- **team-totals-promising** — Team totals were promising on the day (3-1, +50.78) but are not proven solved -- the manual sample remains small.
- **hou-tt-better-than-game-over** — The Houston team total was a better expression than the CWS/HOU full-game over.

## Analytical misses

- **torcle-f5-weakest-selection** — The TOR/CLE F5 total was the weakest selection of the day.
- **mil-ml-marginal** — MIL moneyline was marginal.
- **sea-5-plus-missed-by-one** — SEA 5+ was defensible but missed by one run.

## Process errors

- **cwshou-thesis-duplication** — Playing both the CWS/HOU full-game over and the HOU team total was unnecessary thesis duplication; the team total was the better expression.
- **bos-ml-plus-tt-correlated** — BOS moneyline + BOS team total created correlated exposure; prefer one best expression unless both are independently justified.
- **pit-tt-oversized** — The PIT 5+ stake ($45.00) was oversized.
- **second-best-expression-rule** — Once the best expression is identified, do not automatically bet the second-best expression as well.

## Proposed investigations

- **best-expression-only** — Once the best expression for a thesis is identified, do not automatically add the second-best expression.
- **team-total-sample-size** — Team totals look promising but the manual sample is small -- keep measuring before treating the family as solved.

## Process grade: B

## Blocked rows (user-confirmed, deliberately NOT written to the ledger)

### `2026-09-03|COMBO|SEA_ML+LAD_ML+BOS_TT_OVER_4.5|10.00` — BLOCKED_UNSUPPORTED_COMBO

- Market: 3-leg MLB Kalshi combo: SEA ML + LAD ML + BOS team total over 4.5
- Stake: $10.00 · executed price UNKNOWN
- User-confirmed result: LOSS · realized return $0.00 · P/L -10.00
- Legs:
  - SEA moneyline
  - LAD moneyline
  - BOS team total over 4.5
- Why blocked: The legs ARE known and preserved here verbatim, but the canonical PlacedBet schema has no native multi-leg/combo representation: one row is one marketTicker with one side, one stake and one entry price. Writing three straight bets would misstate one $10 combo as three separate wagers and would triple-count risk, so no canonical bet was written and no synthetic combo ticker was invented. The user's executed combo price is also not known.
- Unblocked by: Native combo support in the canonical placed-bet schema.

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
    "betId": "55bbfd8f53c3d2ddb2a07356f25bf39f66f1cdbb",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -24.99,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.55,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_TOR_CLE_1310",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP031310TORCLE-5",
    "matchup": "TOR @ CLE",
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
    "selection": "TOR/CLE first 5 innings NO on over 4.5",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-03|TOR-CLE|F5_TOTAL|NO_OVER_4.5|24.99|55",
    "stake": 24.99,
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
    "betId": "dc112c68dbd0eafc58570fa1f87db66f02de5f08",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 38.53,
    "confirmedReceiptReturn": 83.53,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.53,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_SF_PIT_1235",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP031235SFPIT-PIT5",
    "matchup": "SF @ PIT",
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
    "selection": "Pittsburgh 5+ runs (over 4.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-03|SF-PIT|PIT_TEAM_TOTAL|OVER_4.5|45.00|53",
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
    "betId": "e3d277111f022a0b614e9fdf35c5be3e4b1842fc",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -20.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.51,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_CWS_HOU_1410",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "game_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTOTAL-26SEP031410CWSHOU-9",
    "matchup": "CWS @ HOU",
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
    "selection": "CWS/HOU game total over 8.5",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-03|CWS-HOU|GAME_TOTAL|OVER_8.5|20.00|51",
    "stake": 20.0,
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
    "betId": "400e0f900efb91bf2edaa72b0926a8823880a384",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 20.92,
    "confirmedReceiptReturn": 40.92,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.48,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_CWS_HOU_1410",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP031410CWSHOU-HOU5",
    "matchup": "CWS @ HOU",
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
    "sourceBetKey": "2026-09-03|CWS-HOU|HOU_TEAM_TOTAL|OVER_4.5|20.00|48",
    "stake": 20.0,
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
    "betId": "1c745e00b0fc9c10170060c5762cb8e8e41d0172",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 10.84,
    "confirmedReceiptReturn": 35.84,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.69,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_TB_TEX_2005",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP032005TBTEX-6",
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
    "selection": "TB/TEX first 5 innings NO on over 5.5",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-03|TB-TEX|F5_TOTAL|NO_OVER_5.5|25.00|69",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": 6,
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
    "betId": "2d7397d3fed2ac419ca2c5e99f663c1cfd441fb9",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -19.99,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.55,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_MIL_CHC_1915",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "game_result",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBGAME-26SEP031915MILCHC-MIL",
    "matchup": "MIL @ CHC",
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
    "selection": "Milwaukee moneyline",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-03|MIL-CHC|MIL_ML|YES|19.99|55",
    "stake": 19.99,
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
    "betId": "41ad485e49da73030a056df370cecc1d65d51ace",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 21.4,
    "confirmedReceiptReturn": 46.4,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.53,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_BOS_BAL_1915",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "game_result",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBGAME-26SEP031915BOSBAL-BOS",
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
    "sourceBetKey": "2026-09-03|BOS-BAL|BOS_ML|YES|25.00|53",
    "stake": 25.0,
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
    "betId": "6046fd4f864ae860904d0187eed670c9de4a53d2",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 16.33,
    "confirmedReceiptReturn": 31.33,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.47,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_BOS_BAL_1915",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP031915BOSBAL-BOS5",
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
    "selection": "Boston 5+ runs (over 4.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-03|BOS-BAL|BOS_TEAM_TOTAL|OVER_4.5|15.00|47",
    "stake": 15.0,
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
    "betId": "2d31c06b187249ad2399058ad54df177206c8192",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 10.84,
    "confirmedReceiptReturn": 35.84,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.69,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_STL_LAD_2210",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP032210STLLAD-6",
    "matchup": "STL @ LAD",
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
    "selection": "STL/LAD first 5 innings NO on over 5.5",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-03|STL-LAD|F5_TOTAL|NO_OVER_5.5|25.00|69",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": 6,
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
    "betId": "9c0848cba0f1cea41dfd8ab8f87c681db5445f85",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -25.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.45,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": "2026-09-03_ATH_SEA_2140",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP032140ATHSEA-SEA5",
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
    "sourceBetKey": "2026-09-03|ATH-SEA|SEA_TEAM_TOTAL|OVER_4.5|25.00|45",
    "stake": 25.0,
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
    "betId": null,
    "canonicalStatus": "BLOCKED_UNSUPPORTED_COMBO",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": null,
    "confirmedReceiptReturn": null,
    "confirmedReceiptSource": null,
    "correlationGroups": [],
    "date": "2026-09-03",
    "entryMethod": null,
    "entryOdds": null,
    "entryPrice": null,
    "eventTicker": null,
    "gameDate": "2026-09-03",
    "gameId": null,
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-03-postmortem-v1",
    "marketFamily": null,
    "marketHorizon": null,
    "marketTicker": null,
    "matchup": null,
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "The legs ARE known and preserved here verbatim, but the canonical PlacedBet schema has no native multi-leg/combo representation: one row is one marketTicker with one side, one stake and one entry price. Writing three straight bets would misstate one $10 combo as three separate wagers and would triple-count risk, so no canonical bet was written and no synthetic combo ticker was invented. The user's executed combo price is also not known.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "3-leg MLB Kalshi combo: SEA ML + LAD ML + BOS team total over 4.5",
    "seriesTicker": null,
    "side": null,
    "snapshotId": null,
    "sourceBetKey": "2026-09-03|COMBO|SEA_ML+LAD_ML+BOS_TT_OVER_4.5|10.00",
    "stake": 10.0,
    "thesisTags": [],
    "threshold": null,
    "unresolvedFieldReasons": {
      "betId": "no canonical bet exists for this row",
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "entryPrice": "the user's executed price for this wager is not known",
      "marketTicker": "The legs ARE known and preserved here verbatim, but the canonical PlacedBet schema has no native multi-leg/combo representation: one row is one marketTicker with one side, one stake and one entry price. Writing three straight bets would misstate one $10 combo as three separate wagers and would triple-count risk, so no canonical bet was written and no synthetic combo ticker was invented. The user's executed combo price is also not known.",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "blocked"
  }
]
```

