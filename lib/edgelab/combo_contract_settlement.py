"""
lib/edgelab/combo_contract_settlement.py
==========================================
Settle a Kalshi COMBO contract (PlacedBet.wagerStructure == "COMBO_CONTRACT") from the exchange's OWN final result
for that contract. Pure: the network read is in scripts/edgelab/settle_combo_contracts.py.

WHY A COMBO CANNOT GO THROUGH settle_markets.py
-----------------------------------------------
Every other MLB market here is graded by re-deriving its outcome from the MLB Stats API linescore/boxscore for the
game its ticker names. A combo ticker names no game -- it is a multivariate collection contract whose legs can span
several games and several market families -- so there is no single game outcome to grade it against, and
settle_markets.py (correctly) never reaches it. Grading it by grading each leg and multiplying the results would be
this repository INVENTING the combo's payout rule; the exchange already publishes the answer for the one contract the
owner actually held.

THE RULE
--------
A combo contract is SETTLED only when the exchange returns that exact ticker with a terminal market status
(``settled`` / ``finalized``) AND a binary result (``yes`` / ``no``). Then:

    result YES/NO -> lib.edgelab.settlement.settle_bets_for_ticker -> WIN/LOSS by the bet's own side, P/L from the
    bet's own exact execution economics -- the same function, the same economics, as every straight Kalshi bet.

Everything else is SETTLEMENT_UNRESOLVED with a reason, and the bet stays pending, untouched:
  * the market was not returned, or a different ticker came back;
  * the status is not terminal (active / closed / determined -- "determined" is not yet final on Kalshi);
  * the result is not binary (void, scalar, empty) -- a voided or scalar combo's economics are not modelled here,
    and guessing a refund would be a guess.

The legs (PlacedBet.comboLegs) are never read: if the contract's own result and a leg-by-leg reading ever disagree,
the contract wins, by construction.
"""

from __future__ import annotations

from lib.edgelab import ids
from lib.edgelab.exchange_settlement import normalize_result
from lib.edgelab.settlement import (
    bet_needs_settlement_update,
    build_settlement_record,
    settle_bets_for_ticker,
)

COMBO_CONTRACT = "COMBO_CONTRACT"
SETTLEMENT_SOURCE = "kalshi_combo_contract_result"
TERMINAL_MARKET_STATUSES = frozenset({"settled", "finalized"})

UNRESOLVED_NOT_RETURNED = "COMBO_MARKET_NOT_RETURNED_BY_EXCHANGE"
UNRESOLVED_TICKER_MISMATCH = "COMBO_MARKET_TICKER_MISMATCH"
UNRESOLVED_NOT_FINAL = "COMBO_MARKET_NOT_FINAL"
UNRESOLVED_NON_BINARY = "COMBO_RESULT_NOT_BINARY"
UNRESOLVED_FETCH_FAILED = "COMBO_MARKET_FETCH_FAILED"


def is_combo_contract(bet: dict) -> bool:
    return (bet or {}).get("wagerStructure") == COMBO_CONTRACT


def combo_contract_outcome(ticker: str, payload) -> tuple[str, str | None, str | None]:
    """(settlementStatus, result, unavailableReason) for one combo contract, from the exchange's
    ``GET /markets/{ticker}`` body. ``payload`` None means the read failed."""
    if payload is None:
        return "SETTLEMENT_UNRESOLVED", None, UNRESOLVED_FETCH_FAILED
    market = payload.get("market") if isinstance(payload, dict) else None
    if not isinstance(market, dict):
        return "SETTLEMENT_UNRESOLVED", None, UNRESOLVED_NOT_RETURNED
    if market.get("ticker") != ticker:
        return "SETTLEMENT_UNRESOLVED", None, UNRESOLVED_TICKER_MISMATCH
    status = str(market.get("status") or "").strip().lower()
    if status not in TERMINAL_MARKET_STATUSES:
        return "SETTLEMENT_UNRESOLVED", None, f"{UNRESOLVED_NOT_FINAL}:{status or 'unknown'}"
    # The repository's one normaliser for the exchange's own result vocabulary (lib.edgelab.exchange_settlement).
    result = normalize_result(market.get("result"))
    if result in ("YES", "NO"):
        return "SETTLED", result, None
    return "SETTLEMENT_UNRESOLVED", None, f"{UNRESOLVED_NON_BINARY}:{(result or 'empty').lower()}"


def settle_combo_contract(bets_on_ticker: list[dict], ticker: str, payload, *, now: str | None = None):
    """Grade every COMBO_CONTRACT bet on one combo ticker from its contract's own result.

    Returns ``(bets_to_write, settlement_record)``:
      * ``bets_to_write`` -- only bets whose settled shape genuinely changed (an already-settled, unchanged bet is
        never rewritten; see lib.edgelab.settlement.bet_needs_settlement_update);
      * ``settlement_record`` -- the Settlement row for the ticker (SETTLED or SETTLEMENT_UNRESOLVED with a reason),
        merged by the caller with lib.edgelab.settlement.merge_settlement_record, which refuses to regress a stored
        terminal record.
    A non-combo bet passed in by mistake is ignored, never graded here.
    """
    now = now or ids.utc_now_iso()
    combos = [b for b in bets_on_ticker if is_combo_contract(b) and b.get("marketTicker") == ticker]
    status, result, reason = combo_contract_outcome(ticker, payload)
    settled = settle_bets_for_ticker(combos, status, result, now=now, game_id=None)
    to_write = []
    for original, computed in zip(combos, settled):
        if bet_needs_settlement_update(original, computed):
            computed["updatedAt"] = now
            to_write.append(computed)
    representative = settled[0] if settled else None
    record = build_settlement_record(
        market_ticker=ticker, game_id=None, market_family=(combos[0].get("marketFamily") if combos else None),
        settlement_status=status, result=result, settlement_source=SETTLEMENT_SOURCE,
        settled_at=now if status == "SETTLED" else None, unavailable_reason=reason,
        bet_id=combos[0]["betId"] if combos else None,
        realized_return=(representative.get("netProfitLoss")
                         if representative and not representative.get("settlementRefusalReason") else None),
        was_placed=bool(combos),
        source=SETTLEMENT_SOURCE,
        provenance={"sourceSystem": SETTLEMENT_SOURCE, "sourceFile": None, "sourceKey": ticker,
                    "capturedAt": now, "ingestedAt": now},
    )
    return to_write, record
