# Kalshi combo contracts (`wagerStructure: COMBO_CONTRACT`)

## What a combo contract is

A Kalshi multivariate COMBO (series category "Exotics", collection `KXMVE…`) is **one exchange contract**: its own
ticker, its own YES/NO side, one executed price, one settlement. The exchange states its legs
(`mve_selected_legs`), but the owner holds the combo contract, not the legs.

That is different from `MULTI_LEG`, which records a user-described parlay that has **no single contract** to name
(no parent ticker, ≥ 2 legs, legs carry the economics' description). Both shapes stay supported; neither replaces
the other.

## How it is recorded

`lib/edgelab/bets.py` accepts `wager_structure="COMBO_CONTRACT"`:

* `marketTicker` is the combo contract's own ticker, treated as an **opaque market identity**; it is never
  resolved from or matched against the MLB market corpus (no MLB market record names it);
* `side`, `entryPrice`, `stake`, `contracts`, the exact execution economics and the
  `importBatchId`/`sourceBetKey` identity are recorded exactly as for any straight Kalshi wager — the betId is
  the same `hash(importBatchId, sourceBetKey, marketTicker, side)`, so a re-import is a `DUPLICATE_NOOP`;
* the legs, when the source knew them, travel in **`comboLegs`** — provenance only (`marketTicker`,
  `eventTicker`, `side`), never `legs[]`, never their own PlacedBet rows, never an input to any result. A combo
  with no leg metadata is exactly as well defined (`comboLegs: null`);
* `comboLegs` exists only on a COMBO_CONTRACT row, so every SINGLE and MULTI_LEG row keeps the exact shape it
  always had.

`scripts/edgelab/import_bet_batch.py` passes `wagerStructure`/`comboLegs` through, and refuses a COMBO_CONTRACT
row that arrives without its own ticker rather than resolving one. kalshi-bet-router sends these rows for an
order on a combo whose every leg it classified as MLB (`marketFamily: multi_market_combo`).

## How it settles

`settle_markets.py` grades MLB markets from MLB Stats API game outcomes. A combo names no single game, so that
path never reaches it — and grading each leg then multiplying would be this repository inventing the combo's
payout rule. The exchange already publishes the answer for the one contract held.

`lib/edgelab/combo_contract_settlement.py` (pure) + `scripts/edgelab/settle_combo_contracts.py` (I/O, Kalshi's
public `GET /markets/{ticker}`, no credential) settle every **pending** COMBO_CONTRACT wager:

| exchange says | outcome |
|---|---|
| the same ticker, status `settled`/`finalized`, result `yes`/`no` | `SETTLED` YES/NO → `settle_bets_for_ticker` grades WIN/LOSS by the bet's own side, P/L from its own exact execution economics |
| market not returned, a different ticker, a fetch failure | `SETTLEMENT_UNRESOLVED`, bet left pending |
| status not terminal (`active`, `closed`, `determined`) | `SETTLEMENT_UNRESOLVED`, re-asked next run |
| result not binary (`void`, `scalar`, empty) | `SETTLEMENT_UNRESOLVED` — a void/scalar refund is not modelled, so it is not guessed |

The contract's own result always wins over any reading of its legs (the legs are never read). A Settlement row is
written with `settlementSource: kalshi_combo_contract_result`, `gameId: null`, and merged with
`merge_settlement_record`, which never lets a non-terminal answer overwrite a stored SETTLED record. An already
settled combo is not considered again; an unchanged bet is never rewritten. Straight wagers, MULTI_LEG parlays and
player props are never read or written by this path.

It runs in `edgelab-settlement-reconcile.yml` (on every push that changes `bets.jsonl` — i.e. right after a router
import — and twice daily), after the settlement catch-up and before the commit step.
