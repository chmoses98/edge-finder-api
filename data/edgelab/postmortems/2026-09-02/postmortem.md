# MLB postmortem — 2026-09-02

**Poor overall day; better second-half execution did not offset large early losses.**

## User-confirmed day (as supplied)

- Positions: 14
- Record: 6-8
- Risk: $334.99
- Realized return: $254.49
- P/L: -80.50
- ROI: -24.03%

## Canonical ledger for this date (what was actually written)

- Canonical wagers written: **11 of 14** (3 blocked — see below)
- Import batch: `mlb-manual-2026-09-02-postmortem-v1`
- Canonical record (user-confirmed receipts): **6-5**
- Canonical risk: $239.99 · realized return: $254.49 · P/L: +14.50 · ROI: +6.04%

> The canonical totals differ from the user-confirmed day by exactly the blocked rows: $95.00 of risk and -95.00 of P/L. This gap is reported, never silently reconciled — the supplied manifest was not edited and no blocked wager was fabricated into the ledger.

## Wagers

| Market | Ticker | Side | Stake | Entry (displayed) | Result | Gross return | Net P/L |
|---|---|---|---|---|---|---|---|
| SD wins first 5 innings | _blocked — not in ledger_ | — | $60.00 | 51% | LOSS (user-confirmed) | $0.00 | -60.00 |
| WSH wins first 5 innings | _blocked — not in ledger_ | — | $25.00 | 39% | LOSS (user-confirmed) | $0.00 | -25.00 |
| PHI/ARI first 5 innings over 4.5 | `KXMLBF5TOTAL-26SEP021540PHIAZ-5` | YES | $30.00 | 53% | LOSS | $0.00 | -30.00 |
| TOR/CLE first 5 innings NO on over 4.5 (4 or fewer first-five runs) | `KXMLBF5TOTAL-26SEP021840TORCLE-5` | NO | $25.00 | 61% | LOSS | $0.00 | -25.00 |
| CWS/HOU first 5 innings NO on over 4.5 (4 or fewer first-five runs) | `KXMLBF5TOTAL-26SEP022010CWSHOU-5` | NO | $20.00 | 49% | WIN | $40.10 | +20.10 |
| NYY/LAA first 5 innings NO on over 4.5 | `KXMLBF5TOTAL-26SEP022138NYYLAA-5` | NO | $39.99 | 70% | WIN | $56.54 | +16.55 |
| 3-leg MLB Kalshi combo (legs not established) | _blocked — not in ledger_ | — | $10.00 | — | LOSS (user-confirmed) | $0.00 | -10.00 |
| MIA/KC NRFI (no run in the 1st inning) | `KXMLBRFI-26SEP021940MIAKC` | NO | $15.00 | 53% | LOSS | $0.00 | -15.00 |
| Miami moneyline | `KXMLBGAME-26SEP021940MIAKC-MIA` | YES | $15.00 | 49% | WIN | $30.07 | +15.07 |
| Eury Perez 5+ strikeouts | `KXMLBKS-26SEP021940MIAKC-MIAEPREZ39-5` | YES | $10.00 | 58% | LOSS | $0.00 | -10.00 |
| Milwaukee moneyline | `KXMLBGAME-26SEP021940MILCHC-MIL` | YES | $25.00 | 59% | WIN | $41.77 | +16.77 |
| Milwaukee 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP021940MILCHC-MIL5` | YES | $20.00 | 52% | WIN | $37.82 | +17.82 |
| Jacob Misiorowski 8+ strikeouts | `KXMLBKS-26SEP021940MILCHC-MILJMISIOROWSKI32-8` | YES | $15.00 | 50% | LOSS | $0.00 | -15.00 |
| Yoshinobu Yamamoto 19+ outs | `KXMLBOUTS-26SEP022210STLLAD-LADYYAMAMOTO18-19` | YES | $25.00 | 51% | WIN | $48.19 | +23.19 |

CLV: not available for any of these wagers — no closing-quote linkage has been computed for this date, and none was fabricated.

## Analytical wins

- **mia-ml-better-expression** — Miami full-game moneyline was a better expression of the Miami thesis than forcing an early-game side.
- **yamamoto-workload-prop** — Yamamoto 19+ outs validated the value of workload-based pitcher props.
- **mil-tt-own-thesis** — Milwaukee moneyline and Milwaukee team total both won, but the team total had its own scoring thesis rather than being a duplicate of the side.

## Analytical misses

- **f5-three-way-tie-tax** — WSH and SD F5 both losing through a tie is a concrete three-way F5 tie-tax lesson: an F5 three-way YES requires an actual lead, and a tie loses.
- **phiari-f5-over-weakest-process** — PHI/ARI F5 over 4.5 was the weakest total process of the day.
- **torcle-f5-under-overstated** — The TOR/CLE F5 under probability was overstated.
- **k-props-need-workload-model** — Strikeout props require K rate x expected batters faced x survival/workload, not raw K talent alone.

## Process errors

- **sd-f5-stake-too-aggressive** — The SD F5 stake ($60) was too aggressive. This wager is BLOCKED out of the canonical ledger (see blockedRows) -- the finding is preserved, the wager is not fabricated.
- **same-pitcher-same-thesis-exposure** — Too much same-pitcher / same-thesis exposure is dangerous.

## Proposed investigations

- **price-the-f5-tie-tax** — Explicitly price the F5 three-way tie tax: an F5 YES requires an actual lead, and a tie loses.
- **k-prop-workload-pricing** — Price strikeout props as K rate x expected batters faced x survival/workload rather than raw K talent.

## Process grade: C-

## Blocked rows (user-confirmed, deliberately NOT written to the ledger)

### `2026-09-02|WSH-SD|F5_SIDE|SD_YES|60.00|51` — BLOCKED_AMBIGUOUS_MARKET

- Market: SD wins first 5 innings
- Stake: $60.00 · displayed entry 51%
- User-confirmed result: LOSS · realized return $0.00 · P/L -60.00
- Why blocked: The archived 2026-09-02 Kalshi market corpus contains NO WSH-vs-SD game. San Diego's only 2026-09-02 game is SD @ CIN and Washington's only 2026-09-02 game is ATL @ WSH, so the matchup this row asserts does not exist on this date and no single defensible ticker can be established. The canonical importer refused the row (NOT_FOUND against the point-in-time corpus).
  - Candidate reading: One WSH-vs-SD game (as the sourceBetKey and the 'both lost through a tie' finding imply): NO such game exists in the 2026-09-02 corpus.
  - Candidate reading: Two separate games: KXMLBF5-26SEP021240SDCIN-SD (SD F5 winner, SD @ CIN) for this row and KXMLBF5-26SEP021305ATLWSH-WSH (WSH F5 winner, ATL @ WSH) for the WSH row. Defensible only if the user confirms these were two different games.
- Unblocked by: User confirmation of which game each of these two F5 wagers belonged to.

### `2026-09-02|WSH-SD|F5_SIDE|WSH_YES|25.00|39` — BLOCKED_AMBIGUOUS_MARKET

- Market: WSH wins first 5 innings
- Stake: $25.00 · displayed entry 39%
- User-confirmed result: LOSS · realized return $0.00 · P/L -25.00
- Why blocked: Same as the SD row above: no WSH-vs-SD game exists in the archived 2026-09-02 corpus, so exactly one defensible ticker cannot be established.
  - Candidate reading: One WSH-vs-SD game: does not exist on 2026-09-02.
  - Candidate reading: KXMLBF5-26SEP021305ATLWSH-WSH (WSH F5 winner, ATL @ WSH), defensible only if the user confirms the two F5 wagers were on two different games.
- Unblocked by: User confirmation of which game each of these two F5 wagers belonged to.

### `2026-09-02|COMBO|THREE_LEG_UNRESOLVED|10.00` — BLOCKED_MISSING_EVIDENCE

- Market: 3-leg MLB Kalshi combo
- Stake: $10.00 · executed price UNKNOWN
- User-confirmed result: LOSS · realized return $0.00 · P/L -10.00
- Why blocked: Two independent blockers. (1) The exact three legs are not established by any durable repository evidence: a search of the canonical ledger, the archived market/observation corpus, the recommendation ledger, prior postmortems and the git history found no record of this combo's legs, and they are not supplied in the manifest. (2) The canonical PlacedBet schema has no multi-leg/combo representation, so even fully specified this wager could not be written as one canonical row -- and splitting it into separate straight bets would misstate a single $10 combo as multiple wagers. Legs are NOT invented.
- Unblocked by: The exact three legs from the user's Kalshi position history, plus native combo support in the canonical placed-bet schema.

## Research notes (POSTMORTEM_STANDARD §4B)

The per-wager analytical notes actually on record for this date are the analytical wins / misses / process errors above, each linked to its real canonical betId. No decision-time rationale (starting-pitcher edge, bullpen, lineup, weather, park, umpire, price discrepancy, why this market expression was chosen) was supplied for the remaining wagers, and none has been backfilled: a plausible-sounding justification invented after the fact is a policy violation regardless of how reasonable it looks. Each canonical row carries only a provenance `rationale` recording how it was imported and how its stake and entry price were determined — never a reconstructed thesis.

## Settlement and economics provenance

Every realized return above comes from the user's own confirmed receipt, recorded through the one sanctioned manual path (`lib.edgelab.bets.confirm_realized_return`, `confirmedReceiptSource: MANUAL_POSTMORTEM_RECEIPT`). That path deliberately never writes `result` / `status` / `returnAmount` / `netProfitLoss` — those remain the automatic settlement pipeline's own objective fields. The automatic settlement pipeline (`scripts/edgelab/settle_markets.py`) was **not** run as part of this import, so every wager on this date is still canonically `pending` and this postmortem's strict `dailyRecord` / `totalReturned` / `roiPct` blocks (settlement-only view) read zero. The `realizedEconomics` block is the user-confirmed view and reconciles exactly to the table above. Running the normal postgame workflow later will settle these rows and populate the objective fields; `confirmedReceiptSettlementComparison` will then surface any disagreement rather than silently overwriting either side.

Entry prices are the user's **displayed Kalshi percentage**, recorded verbatim. They are not fee-adjusted and were not recomputed from cost, max payout, realized return or fees. Stakes are the user's explicitly supplied wager amounts, not share-card initial costs.

Pitcher-prop rows on this date were imported canonically like any other wager, and their realized economics come from the user-confirmed manual receipt above — not from any automatic prop settlement. This import changed nothing about settlement support for player props.

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
    "betId": null,
    "canonicalStatus": "BLOCKED_AMBIGUOUS_MARKET",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": null,
    "confirmedReceiptReturn": null,
    "confirmedReceiptSource": null,
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": null,
    "entryOdds": null,
    "entryPrice": null,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": null,
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": null,
    "marketHorizon": null,
    "marketTicker": null,
    "matchup": null,
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "The archived 2026-09-02 Kalshi market corpus contains NO WSH-vs-SD game. San Diego's only 2026-09-02 game is SD @ CIN and Washington's only 2026-09-02 game is ATL @ WSH, so the matchup this row asserts does not exist on this date and no single defensible ticker can be established. The canonical importer refused the row (NOT_FOUND against the point-in-time corpus).",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "SD wins first 5 innings",
    "seriesTicker": null,
    "side": null,
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|WSH-SD|F5_SIDE|SD_YES|60.00|51",
    "stake": 60.0,
    "thesisTags": [],
    "threshold": null,
    "unresolvedFieldReasons": {
      "betId": "no canonical bet exists for this row",
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "marketTicker": "The archived 2026-09-02 Kalshi market corpus contains NO WSH-vs-SD game. San Diego's only 2026-09-02 game is SD @ CIN and Washington's only 2026-09-02 game is ATL @ WSH, so the matchup this row asserts does not exist on this date and no single defensible ticker can be established. The canonical importer refused the row (NOT_FOUND against the point-in-time corpus).",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "blocked"
  },
  {
    "betId": null,
    "canonicalStatus": "BLOCKED_AMBIGUOUS_MARKET",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": null,
    "confirmedReceiptReturn": null,
    "confirmedReceiptSource": null,
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": null,
    "entryOdds": null,
    "entryPrice": null,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": null,
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": null,
    "marketHorizon": null,
    "marketTicker": null,
    "matchup": null,
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Same as the SD row above: no WSH-vs-SD game exists in the archived 2026-09-02 corpus, so exactly one defensible ticker cannot be established.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "WSH wins first 5 innings",
    "seriesTicker": null,
    "side": null,
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|WSH-SD|F5_SIDE|WSH_YES|25.00|39",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": null,
    "unresolvedFieldReasons": {
      "betId": "no canonical bet exists for this row",
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "marketTicker": "Same as the SD row above: no WSH-vs-SD game exists in the archived 2026-09-02 corpus, so exactly one defensible ticker cannot be established.",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "blocked"
  },
  {
    "betId": "556a609d8468f6ddd35355283cc0fdc6ba118b40",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -30.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.53,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_PHI_AZ_1540",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP021540PHIAZ-5",
    "matchup": "PHI @ AZ",
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
    "selection": "PHI/ARI first 5 innings over 4.5",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|PHI-ARI|F5_TOTAL|OVER_4.5|30.00|53",
    "stake": 30.0,
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
    "betId": "4567bb20b71129803d32f3ff7c46d399f4eb590b",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -25.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.61,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_TOR_CLE_1840",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP021840TORCLE-5",
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
    "selection": "TOR/CLE first 5 innings NO on over 4.5 (4 or fewer first-five runs)",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|TOR-CLE|F5_TOTAL|NO_OVER_4.5|25.00|61",
    "stake": 25.0,
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
    "betId": "154f99cfc8751b6637ce80765f7f62d514b52984",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 20.1,
    "confirmedReceiptReturn": 40.1,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.49,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_CWS_HOU_2010",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP022010CWSHOU-5",
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
    "selection": "CWS/HOU first 5 innings NO on over 4.5 (4 or fewer first-five runs)",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|CWS-HOU|F5_TOTAL|NO_OVER_4.5|20.00|49",
    "stake": 20.0,
    "thesisTags": [],
    "threshold": 5,
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
    "betId": "908469f9bfac1e9f3445969b0bafccc71d0560e0",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 16.55,
    "confirmedReceiptReturn": 56.54,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.7,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_NYY_LAA_2138",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP022138NYYLAA-5",
    "matchup": "NYY @ LAA",
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
    "selection": "NYY/LAA first 5 innings NO on over 4.5",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|NYY-LAA|F5_TOTAL|NO_OVER_4.5|39.99|70",
    "stake": 39.99,
    "thesisTags": [],
    "threshold": 5,
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
    "betId": null,
    "canonicalStatus": "BLOCKED_MISSING_EVIDENCE",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": null,
    "confirmedReceiptReturn": null,
    "confirmedReceiptSource": null,
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": null,
    "entryOdds": null,
    "entryPrice": null,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": null,
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": null,
    "marketHorizon": null,
    "marketTicker": null,
    "matchup": null,
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Two independent blockers. (1) The exact three legs are not established by any durable repository evidence: a search of the canonical ledger, the archived market/observation corpus, the recommendation ledger, prior postmortems and the git history found no record of this combo's legs, and they are not supplied in the manifest. (2) The canonical PlacedBet schema has no multi-leg/combo representation, so even fully specified this wager could not be written as one canonical row -- and splitting it into separate straight bets would misstate a single $10 combo as multiple wagers. Legs are NOT invented.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "3-leg MLB Kalshi combo (legs not established)",
    "seriesTicker": null,
    "side": null,
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|COMBO|THREE_LEG_UNRESOLVED|10.00",
    "stake": 10.0,
    "thesisTags": [],
    "threshold": null,
    "unresolvedFieldReasons": {
      "betId": "no canonical bet exists for this row",
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "entryPrice": "the user's executed price for this wager is not known",
      "marketTicker": "Two independent blockers. (1) The exact three legs are not established by any durable repository evidence: a search of the canonical ledger, the archived market/observation corpus, the recommendation ledger, prior postmortems and the git history found no record of this combo's legs, and they are not supplied in the manifest. (2) The canonical PlacedBet schema has no multi-leg/combo representation, so even fully specified this wager could not be written as one canonical row -- and splitting it into separate straight bets would misstate a single $10 combo as multiple wagers. Legs are NOT invented.",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "blocked"
  },
  {
    "betId": "7e259f31d9621b150ff40596e95cda0e40dd78ba",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -15.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.53,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_MIA_KC_1940",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "first_inning_run",
    "marketHorizon": null,
    "marketTicker": "KXMLBRFI-26SEP021940MIAKC",
    "matchup": "MIA @ KC",
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
    "selection": "MIA/KC NRFI (no run in the 1st inning)",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|MIA-KC|NRFI|YES|15.00|53",
    "stake": 15.0,
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
    "betId": "29d73d39b8744ae2af6f53bd4404d2b32b7996f9",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 15.07,
    "confirmedReceiptReturn": 30.07,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.49,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_MIA_KC_1940",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "game_result",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBGAME-26SEP021940MIAKC-MIA",
    "matchup": "MIA @ KC",
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
    "selection": "Miami moneyline",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|MIA-KC|MIA_ML|YES|15.00|49",
    "stake": 15.0,
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
    "betId": "12030c0d47c6263b0b208b1742b712f0457cac3b",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -10.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.58,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_MIA_KC_1940",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "pitcher_strikeouts",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBKS-26SEP021940MIAKC-MIAEPREZ39-5",
    "matchup": "MIA @ KC",
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
    "selection": "Eury Perez 5+ strikeouts",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|MIA-KC|PITCHER_K|EURY_PEREZ_5_PLUS|10.00|58",
    "stake": 10.0,
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
    "betId": "a5585c8c01372be1dfced03a3f49200314b2b9e7",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 16.77,
    "confirmedReceiptReturn": 41.77,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.59,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_MIL_CHC_1940",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "game_result",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBGAME-26SEP021940MILCHC-MIL",
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
    "sourceBetKey": "2026-09-02|MIL-CHC|MIL_ML|YES|25.00|59",
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
    "betId": "1b9081957b29151710a8bab7565a1714181b3a7b",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 17.82,
    "confirmedReceiptReturn": 37.82,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.52,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_MIL_CHC_1940",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP021940MILCHC-MIL5",
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
    "selection": "Milwaukee 5+ runs (over 4.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|MIL-CHC|MIL_TEAM_TOTAL|OVER_4.5|20.00|52",
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
    "betId": "9674545e7000f68bbc4d68c15fd75c4b27553fbe",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -15.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.5,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_MIL_CHC_1940",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "pitcher_strikeouts",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBKS-26SEP021940MILCHC-MILJMISIOROWSKI32-8",
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
    "selection": "Jacob Misiorowski 8+ strikeouts",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|MIL-CHC|PITCHER_K|MISIOROWSKI_8_PLUS|15.00|50",
    "stake": 15.0,
    "thesisTags": [],
    "threshold": 8,
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
    "betId": "910a0d64f3f99c41c97fd67ca54d57e98880cd63",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 23.19,
    "confirmedReceiptReturn": 48.19,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-02",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.51,
    "eventTicker": null,
    "gameDate": "2026-09-02",
    "gameId": "2026-09-02_STL_LAD_2210",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-02-postmortem-v1",
    "marketFamily": "pitcher_outs",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBOUTS-26SEP022210STLLAD-LADYYAMAMOTO18-19",
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
    "selection": "Yoshinobu Yamamoto 19+ outs",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-02|STL-LAD|PITCHER_OUTS|YAMAMOTO_19_PLUS|25.00|51",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": 19,
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

