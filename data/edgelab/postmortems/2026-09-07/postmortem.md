# MLB postmortem — 2026-09-07

**Roughly break-even on results; the day's real finding is sizing and price discipline.**

## User-confirmed day (as supplied)

- Positions: 9
- Record: 4-5
- Risk: $410.02
- Realized return: $417.65
- P/L: +7.63
- ROI: +1.86%

## Canonical ledger for this date (what was actually written)

- Canonical wagers written: **8 of 9** (1 blocked — see below)
- Import batch: `mlb-manual-2026-09-07-postmortem-v1`
- Canonical record (user-confirmed receipts): **4-4**
- Canonical risk: $385.03 · realized return: $417.65 · P/L: +32.62 · ROI: +8.47%

> The canonical totals differ from the user-confirmed day by exactly the blocked rows: $24.99 of risk and -24.99 of P/L. This gap is reported, never silently reconciled — the supplied manifest was not edited and no blocked wager was fabricated into the ledger.

## Wagers

| Market | Ticker | Side | Stake | Entry (displayed) | Result | Gross return | Net P/L |
|---|---|---|---|---|---|---|---|
| Philadelphia wins first 5 innings | `KXMLBF5-26SEP071305ATLPHI-PHI` | YES | $50.00 | 56% | LOSS | $0.00 | -50.00 |
| CLE/BAL first 5 innings NO on over 3.5 (3 or fewer first-five runs) | `KXMLBF5TOTAL-26SEP071335CLEBAL-4` | NO | $25.00 | 45% | LOSS | $0.00 | -25.00 |
| Milwaukee 5+ runs (over 4.5) | `KXMLBTEAMTOTAL-26SEP071410CHCMIL-MIL5` | YES | $40.00 | 45% | LOSS | $0.00 | -40.00 |
| Noah Cameron 4+ strikeouts | `KXMLBKS-26SEP071410AZKC-KCNCAMERON65-4` | YES | $35.00 | 53% | WIN | $64.96 | +29.96 |
| Detroit 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP071510MINDET-DET4` | YES | $156.56 | 54.8% | WIN | $281.05 | +124.49 |
| 2-market Kalshi combo: Toronto wins first 5 innings + Toronto over 4.5 team runs | _blocked — not in ledger_ | — | $24.99 | — | LOSS (user-confirmed) | $0.00 | -24.99 |
| Los Angeles Dodgers win first 7 innings | `KXMLBF7-26SEP072110CINLAD-LAD` | YES | $24.27 | 49.9% | WIN | $46.93 | +22.66 |
| Miami wins first 5 innings | `KXMLBF5-26SEP071310NYMMIA-MIA` | YES | $39.20 | 48% | LOSS | $0.00 | -39.20 |
| San Francisco 4+ runs (over 3.5) | `KXMLBTEAMTOTAL-26SEP072010STLSF-SF4` | YES | $15.00 | 59% | WIN | $24.71 | +9.71 |

CLV: not available for any of these wagers — no closing-quote linkage has been computed for this date, and none was fabricated.

## Market-family breakdown (as supplied)

| Family | Record | Net P/L |
|---|---|---|
| team_total | 2-1 | +94.20 |
| pitcher_strikeouts | 1-0 | +29.96 |
| inning_result (F7) | 1-0 | +22.66 |
| inning_result (F5) | 0-2 | -89.20 |
| inning_total (F5) | 0-1 | -25.00 |
| combo | 0-1 | -24.99 — Not canonically representable -- see blockedRows. |

## Analytical wins

- **cameron-prop-cleanest-win** — Noah Cameron 4+ Ks was the cleanest prop win: Cameron struck out 7 over six scoreless innings. Low threshold plus normal workload was the thesis. This supports continuing to SCAN player props; it does not prove pitcher props broadly profitable.
- **lad-f7-horizon-selection** — LAD F7 was excellent horizon selection: Chase Burns' limited workload made F7 superior to F5. The Dodgers broke through after Burns left and led 5-2 after seven. A strong example of matching market horizon to the actual pitching-usage thesis.
- **det-tt-handicap-correct** — DET 4+: the handicap/expression was correct -- Joe Ryan was returning with limited workload, Detroit scored 5, and the later damage came after Ryan. The handicap being right does not validate the stake (see processErrors).

## Analytical misses

- **phi-f5-tie-tax** — PHI F5: Philadelphia eventually won 1-0, but the only run came in the eighth, so PHI F5 YES lost to a 0-0 tie after five -- another concrete three-way tie-tax lesson. Luzardo was dominant, but Grant Holmes was also excellent; the projected starter mismatch was overstated.
- **mia-f5-starter-handicap-failed** — MIA F5: Eury Perez allowed a career-high 7 runs in four innings. The starter-side handicap failed materially -- recent form should not overwhelm the downside distribution.
- **clebal-f5-under-known-failure-mode** — CLE/BAL F5 <=3: Cantillo's volatility/control was explicitly the known danger, and he surrendered 4 runs in just over two innings. The probability estimate on the under was too high. When the known failure mode is this severe it must reduce the fair probability enough, rather than merely being mentioned as a narrative caveat.
- **mil-tt-thesis-weaker-than-estimated** — MIL 5+ lost with Milwaukee scoring 4. Matthew Boyd held Milwaukee to one run through seven. This was more than a one-run bad beat: the starter-vulnerability thesis itself was weaker than estimated.

## Process errors

- **det-tt-kelly-violation** — DET 4+ at $156.56 represented 15.656% of a $1,000 bankroll and about 38.2% of the day's risk. This violates the intended fractional-Kelly discipline. Winning absolutely does not validate the size.
- **tor-combo-one-thesis-violation** — The Toronto two-market combo is a textbook violation of one thesis -> one best expression: a robust winning team-total thesis (TOR 5+, which won) was packaged with a more fragile same-game F5 leg (which lost), so the combo lost the entire $24.99. Do not package the best expression with a weaker correlated expression just to increase payout. This wager is BLOCKED out of the canonical ledger (see blockedRows).
- **sf-price-discipline-breach** — SF 4+ won, but the user executed at a displayed 59%. The prior recommended maximum was 57%, with 58% only a reduced-stake consideration and 59%+ a pass. Logged as a PRICE-DISCIPLINE BREACH despite the win: winning does not retroactively make an above-max execution correct.

## Proposed investigations

- **enforce-fractional-kelly-output** — Enforce the fractional-Kelly output instead of allowing ad hoc 10-16% bankroll positions. Quarter Kelly remains the default; do NOT move to half Kelly.
- **shared-exposure-caps** — Same-thesis positions must share exposure caps.
- **rollovers-are-exceptional** — User-directed rollovers are exceptional and must not redefine the standard sizing framework.

## Process grade: C+

## Blocked rows (user-confirmed, deliberately NOT written to the ledger)

### `2026-09-07|COMBO|TOR_F5_YES+TOR_TT_OVER_4.5|24.99` — BLOCKED_UNSUPPORTED_COMBO

- Market: 2-market Kalshi combo: Toronto wins first 5 innings + Toronto over 4.5 team runs
- Stake: $24.99 · executed price UNKNOWN
- Max payout shown: $62.71
- User-confirmed result: LOSS · realized return $0.00 · P/L -24.99
- Legs:
  - Toronto wins first 5 innings — LOSS
  - Toronto over 4.5 team runs (Toronto 5+) — WIN
- Why blocked: Same structural blocker as the 2026-09-03 combo: the canonical PlacedBet schema has no native multi-leg/combo representation. Both legs and the full economics are preserved here; the two legs were deliberately NOT written as two separate straight bets, because the user placed one $24.99 combo. No synthetic combo ticker was invented.
- Unblocked by: Native combo support in the canonical placed-bet schema.

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
    "betId": "8d5d9a7fbdecfb31b9e41a3f1676be2e3ce249e5",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -50.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.56,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": "2026-09-07_ATL_PHI_1305",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": "inning_result",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5-26SEP071305ATLPHI-PHI",
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
    "selection": "Philadelphia wins first 5 innings",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-07|ATL-PHI|F5_SIDE|PHI_YES|50.00|56",
    "stake": 50.0,
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
    "betId": "a5b6481f8b5b8986e3c448a13542e6f8e0536e28",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -25.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.45,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": "2026-09-07_CLE_BAL_1335",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": "inning_total",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5TOTAL-26SEP071335CLEBAL-4",
    "matchup": "CLE @ BAL",
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
    "selection": "CLE/BAL first 5 innings NO on over 3.5 (3 or fewer first-five runs)",
    "seriesTicker": null,
    "side": "NO",
    "snapshotId": null,
    "sourceBetKey": "2026-09-07|CLE-BAL|F5_TOTAL|NO_OVER_3.5|25.00|45",
    "stake": 25.0,
    "thesisTags": [],
    "threshold": 4,
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
    "betId": "e6dd54e7cc78b95620a53a427714588213c41e36",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -40.0,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.45,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": "2026-09-07_CHC_MIL_1410",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP071410CHCMIL-MIL5",
    "matchup": "CHC @ MIL",
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
    "sourceBetKey": "2026-09-07|CHC-MIL|MIL_TEAM_TOTAL|OVER_4.5|40.00|45",
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
    "betId": "97bc08a7dfefdf4c8d086f51011820a8a6d2e024",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 29.96,
    "confirmedReceiptReturn": 64.96,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.53,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": "2026-09-07_AZ_KC_1410",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": "pitcher_strikeouts",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBKS-26SEP071410AZKC-KCNCAMERON65-4",
    "matchup": "AZ @ KC",
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
    "selection": "Noah Cameron 4+ strikeouts",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-07|AZ-KC|PITCHER_K|NOAH_CAMERON_4_PLUS|35.00|53",
    "stake": 35.0,
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
    "betId": "7c02610d5d4b928cf4df24733f8ca5d6430ff1aa",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 124.49,
    "confirmedReceiptReturn": 281.05,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.548,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": "2026-09-07_MIN_DET_1510",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP071510MINDET-DET4",
    "matchup": "MIN @ DET",
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
    "selection": "Detroit 4+ runs (over 3.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-07|MIN-DET|DET_TEAM_TOTAL|OVER_3.5|156.56|54.8",
    "stake": 156.56,
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
    "betId": null,
    "canonicalStatus": "BLOCKED_UNSUPPORTED_COMBO",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": null,
    "confirmedReceiptReturn": null,
    "confirmedReceiptSource": null,
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": null,
    "entryOdds": null,
    "entryPrice": null,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": null,
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": null,
    "marketHorizon": null,
    "marketTicker": null,
    "matchup": null,
    "modelEvaluationId": null,
    "netProfitLoss": null,
    "notes": "Same structural blocker as the 2026-09-03 combo: the canonical PlacedBet schema has no native multi-leg/combo representation. Both legs and the full economics are preserved here; the two legs were deliberately NOT written as two separate straight bets, because the user placed one $24.99 combo. No synthetic combo ticker was invented.",
    "placedAt": null,
    "provenance": {
      "capturedAt": null,
      "sourceSystem": "chat"
    },
    "recommendationId": null,
    "replayRunId": null,
    "result": null,
    "selection": "2-market Kalshi combo: Toronto wins first 5 innings + Toronto over 4.5 team runs",
    "seriesTicker": null,
    "side": null,
    "snapshotId": null,
    "sourceBetKey": "2026-09-07|COMBO|TOR_F5_YES+TOR_TT_OVER_4.5|24.99",
    "stake": 24.99,
    "thesisTags": [],
    "threshold": null,
    "unresolvedFieldReasons": {
      "betId": "no canonical bet exists for this row",
      "closingPrice": "CLV collection has not run for this date",
      "clv": "CLV collection has not run for this date",
      "entryPrice": "the user's executed price for this wager is not known",
      "marketTicker": "Same structural blocker as the 2026-09-03 combo: the canonical PlacedBet schema has no native multi-leg/combo representation. Both legs and the full economics are preserved here; the two legs were deliberately NOT written as two separate straight bets, because the user placed one $24.99 combo. No synthetic combo ticker was invented.",
      "recommendationId": "no Recommendation ledger rows exist for this date, so no real model linkage is available",
      "result": "automatic settlement has not run for this date; the user-confirmed result is recorded separately as a MANUAL_POSTMORTEM_RECEIPT"
    },
    "userConfirmedResult": "LOSS",
    "validationStatus": "blocked"
  },
  {
    "betId": "94d7f935eff660d1caf21f132dab04e27e2c1981",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 22.66,
    "confirmedReceiptReturn": 46.93,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.499,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": "2026-09-07_CIN_LAD_2110",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": "inning_result",
    "marketHorizon": "F7",
    "marketTicker": "KXMLBF7-26SEP072110CINLAD-LAD",
    "matchup": "CIN @ LAD",
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
    "selection": "Los Angeles Dodgers win first 7 innings",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-07|CIN-LAD|F7_SIDE|LAD_YES|24.27|49.9",
    "stake": 24.27,
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
    "betId": "d2a37a54674191455d8d6b072bf0f47c95cc9be2",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": -39.2,
    "confirmedReceiptReturn": 0.0,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.48,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": "2026-09-07_NYM_MIA_1310",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": "inning_result",
    "marketHorizon": "F5",
    "marketTicker": "KXMLBF5-26SEP071310NYMMIA-MIA",
    "matchup": "NYM @ MIA",
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
    "selection": "Miami wins first 5 innings",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-07|NYM-MIA|F5_SIDE|MIA_YES|39.20|48",
    "stake": 39.2,
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
    "betId": "aaa6bbf2d6e9ad7bfbaf9fdfc8bc8ebde735848c",
    "canonicalStatus": "REPOSITORY_SAVED",
    "closingPrice": null,
    "clv": null,
    "confidence": null,
    "confirmedReceiptNetProfitLoss": 9.71,
    "confirmedReceiptReturn": 24.71,
    "confirmedReceiptSource": "MANUAL_POSTMORTEM_RECEIPT",
    "correlationGroups": [],
    "date": "2026-09-07",
    "entryMethod": "IMPORTED_RECEIPT",
    "entryOdds": null,
    "entryPrice": 0.59,
    "eventTicker": null,
    "gameDate": "2026-09-07",
    "gameId": "2026-09-07_STL_SF_2010",
    "grossReturn": null,
    "importBatchId": "mlb-manual-2026-09-07-postmortem-v1",
    "marketFamily": "team_total",
    "marketHorizon": "FULL_GAME",
    "marketTicker": "KXMLBTEAMTOTAL-26SEP072010STLSF-SF4",
    "matchup": "STL @ SF",
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
    "selection": "San Francisco 4+ runs (over 3.5)",
    "seriesTicker": null,
    "side": "YES",
    "snapshotId": null,
    "sourceBetKey": "2026-09-07|STL-SF|SF_TEAM_TOTAL|OVER_3.5|15.00|59",
    "stake": 15.0,
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
  }
]
```

