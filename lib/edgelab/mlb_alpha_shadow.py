"""
lib/edgelab/mlb_alpha_shadow.py
==============================
C01-PIT PROSPECTIVE SHADOW -- pure entry construction.

RESEARCH ONLY. Nothing here recommends, stakes, gates or places anything.
No production module imports it (pinned by test).

TWO STREAMS, ONE TRIGGER (frozen protocol, Sections D/E):
  * `c01pit_trigger_v1` -- the ONLY stream that may create an official
    entry, polling every 10 minutes inside [T-60, T-0).
  * everything else -- research context only. Such observations carry
    `canTriggerC01Pit = False` and can never create an entry, change its
    price, or change its timing.

An entry is created BEFORE the outcome exists and carries no outcome
field of any kind, by construction (pinned by test).
"""

from lib.edgelab import clv_convention
from lib.edgelab.kalshi_fees import max_contracts_for_cash, taker_fee
from lib.edgelab.mlb_alpha_identity import parse_event_ticker, STATUS_RESOLVED

TRIGGER_STREAM_ID = "c01pit_trigger_v1"
CANDIDATE_ID = "MLB-ALPHA-0001-C01-PIT"
RULE_SHA256 = "882f16d8330af1af12aec928a561302bfe81de6a5e5716a3a7fa352bc048376b"
SHADOW_ID = "MLB-ALPHA-0001-C01-PIT-SHADOW-V1"

BAND_LO, BAND_HI = 90, 99
WINDOW_OPEN_MIN, WINDOW_CLOSE_MIN = 60.0, 0.0
ORDER_USD = 10.00
SERIES_PREFIX = "KXMLBF5TOTAL-"

ELIGIBLE = "ELIGIBLE"
DECLINED = "DECLINED"


def can_trigger(stream_id):
    """Only the frozen trigger stream may fire an official entry."""
    return stream_id == TRIGGER_STREAM_ID


def _decline(reason, **extra):
    out = {"eligibility": DECLINED, "exclusionReason": reason}
    out.update(extra)
    return out


def evaluate_observation(obs, stream_id, now_utc=None):
    """
    Decide whether ONE observation creates an official C01-PIT shadow entry.

    `obs` is a captured quote dict: marketTicker, eventTicker, capturedAt
    (datetime), yesBid, yesAsk, optional noBid/noAsk, volume, openInterest,
    optional depth fields, threshold, captureId, provenance.

    Returns an append-only-ready record. Declines are returned too, with an
    explicit reason -- never silently dropped.
    """
    ticker = (obs or {}).get("marketTicker") or ""
    base = {
        "shadowId": SHADOW_ID,
        "candidateId": CANDIDATE_ID,
        "ruleSha256": RULE_SHA256,
        "triggerStream": stream_id,
        "canTriggerC01Pit": can_trigger(stream_id),
        "marketTicker": ticker,
        "eventTicker": (obs or {}).get("eventTicker"),
        "triggerCaptureId": (obs or {}).get("captureId"),
        "triggerTimestamp": (obs or {}).get("capturedAt"),
        "rawSourceProvenance": (obs or {}).get("provenance"),
        "dataQualityFlags": [],
    }

    if not can_trigger(stream_id):
        return dict(base, **_decline("not_the_frozen_trigger_stream"))
    if not ticker.startswith(SERIES_PREFIX):
        return dict(base, **_decline("outside_c01pit_universe"))

    ident = parse_event_ticker(obs.get("eventTicker"))
    if ident["status"] != STATUS_RESOLVED:
        return dict(base, **_decline("identity_%s" % ident["unresolvedReason"]))

    base.update({
        "gameDate": ident["gameDate"],
        "awayTeam": ident["awayTeam"],
        "homeTeam": ident["homeTeam"],
        "doubleheaderGame": ident["doubleheaderGame"],
        "scheduledFirstPitchUtc": ident["scheduledStartUtc"],
        "mlbGamePk": obs.get("mlbGamePk"),
        "threshold": obs.get("threshold"),
        "side": "BUY_YES",
    })

    captured = obs.get("capturedAt")
    if captured is None:
        return dict(base, **_decline("missing_capture_timestamp"))
    minutes = (ident["scheduledStartUtc"] - captured).total_seconds() / 60.0
    base["minutesToStart"] = round(minutes, 2)
    if minutes < WINDOW_CLOSE_MIN:
        return dict(base, **_decline("post_start_or_at_start"))
    if minutes > WINDOW_OPEN_MIN:
        return dict(base, **_decline("before_trigger_window_opens"))

    status = (obs.get("marketStatus") or "active").lower()
    if status not in ("active", "unknown"):
        return dict(base, **_decline("market_not_active"))

    yes_bid, yes_ask = obs.get("yesBid"), obs.get("yesAsk")
    base.update({
        "yesBid": yes_bid, "yesAsk": yes_ask,
        "noBid": obs.get("noBid") if obs.get("noBid") is not None
                 else (100.0 - yes_ask if yes_ask is not None else None),
        "noAsk": obs.get("noAsk") if obs.get("noAsk") is not None
                 else (100.0 - yes_bid if yes_bid is not None else None),
        "spreadCents": (yes_ask - yes_bid) if (yes_bid is not None and yes_ask is not None) else None,
        "volume": obs.get("volume"),
        "openInterest": obs.get("openInterest"),
        "yesAskSize": obs.get("yesAskSize"),
        "yesBidSize": obs.get("yesBidSize"),
        "noAskSize": obs.get("noAskSize"),
        "noBidSize": obs.get("noBidSize"),
        "depthAvailable": any(obs.get(k) is not None for k in
                              ("yesAskSize", "yesBidSize", "noAskSize", "noBidSize")),
    })
    if not base["depthAvailable"]:
        base["dataQualityFlags"].append("DEPTH_UNAVAILABLE")
    if yes_ask is None:
        return dict(base, **_decline("missing_yes_ask"))
    if not (BAND_LO <= yes_ask <= BAND_HI):
        return dict(base, **_decline("outside_price_band"))

    price = yes_ask / 100.0
    contracts = max_contracts_for_cash(ORDER_USD, price)
    if contracts <= 0:
        return dict(base, **_decline("order_size_buys_no_whole_contract"))
    fee = taker_fee(contracts, price)
    base.update({
        "entryExecutableCents": float(yes_ask),
        "hypotheticalContracts": contracts,
        "modeledCashDeployedUsd": round(contracts * price + fee, 4),
        "estimatedFeeUsd": fee,
        "orderSizeUsd": ORDER_USD,
        "executionCaveat": "TOP_OF_BOOK_PRICE_OBSERVED -- $10 fill NOT proven",
        "eligibility": ELIGIBLE,
        "exclusionReason": None,
    })
    return base


def first_eligible_entry(observations, stream_id):
    """Apply the FIRST-qualifying-quote rule over one contract's ordered
    trigger-stream observations. Returns the entry, or None."""
    for obs in sorted(observations, key=lambda o: o.get("capturedAt")):
        rec = evaluate_observation(obs, stream_id)
        if rec.get("eligibility") == ELIGIBLE:
            return rec
    return None


# ---------------------------------------------------------------------------
# Section K -- PROSPECTIVE CLV.
#
# The blind holdout's CLV was identically zero on every row because the
# in-window entry quote WAS the last archived pregame quote. The fix is in
# COLLECTION, not in the historical number: the shadow keeps capturing
# observational quotes after the trigger fires and up to first pitch, so a
# genuinely LATER closing quote exists. Those extra captures can never
# alter the official entry (canTriggerC01Pit = false).
#
# Canonical sign convention (lib.edgelab.clv_convention):
#     positive = GOOD = entered CHEAPER than the close.
# Legacy inverted stored `clv` values are never read.
# ---------------------------------------------------------------------------

def select_closing_observation(observations, scheduled_start_utc, entry_captured_at=None):
    """
    The latest ACTIVE observation strictly before first pitch, and -- when
    `entry_captured_at` is given -- strictly AFTER the entry, so the close
    is a genuinely later quote rather than the entry itself. Returns None
    rather than inventing a close.
    """
    best = None
    for o in observations or []:
        cap = o.get("capturedAt")
        if cap is None or scheduled_start_utc is None:
            continue
        if (o.get("marketStatus") or "active").lower() not in ("active", "unknown"):
            continue
        if cap >= scheduled_start_utc:
            continue
        if entry_captured_at is not None and cap <= entry_captured_at:
            continue
        if best is None or cap > best.get("capturedAt"):
            best = o
    return best


def executable_clv_cents(entry_yes_ask, closing_yes_ask):
    """BUY-YES executable CLV in cents. Delegates to the canonical helper
    (lib.edgelab.clv_convention) rather than re-deriving the sign here."""
    return clv_convention.clv_for_yes(entry_yes_ask, closing_yes_ask,
                                      unit=clv_convention.UNIT_CENTS)


def _mid(bid, ask):
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def fair_mid_clv_cents(entry_bid, entry_ask, closing_bid, closing_ask):
    """
    NON-EXECUTABLE consensus-movement diagnostic: closing mid - entry mid,
    positive = the market's fair midpoint moved toward the purchased side.
    Requires both sides of both books; never fabricated, and never used for
    fill economics or ROI.
    """
    e, c = _mid(entry_bid, entry_ask), _mid(closing_bid, closing_ask)
    if e is None or c is None:
        return None
    return round(c - e, 4)


def clv_block(entry, closing):
    """Both CLV measures plus the spread decomposition for one entry."""
    if not entry:
        return None
    e_bid, e_ask = entry.get("yesBid"), entry.get("yesAsk")
    c_bid = c_ask = None
    if closing:
        c_bid, c_ask = closing.get("yesBid"), closing.get("yesAsk")
    e_spread = (e_ask - e_bid) if (e_bid is not None and e_ask is not None) else None
    c_spread = (c_ask - c_bid) if (c_bid is not None and c_ask is not None) else None
    return {
        "executableClvCents": executable_clv_cents(e_ask, c_ask),
        "fairMidClvCents": fair_mid_clv_cents(e_bid, e_ask, c_bid, c_ask),
        "entrySpreadCents": e_spread,
        "closingSpreadCents": c_spread,
        "spreadCompressionCents": (round(e_spread - c_spread, 4)
                                   if (e_spread is not None and c_spread is not None) else None),
        "closingCapturedAt": (closing or {}).get("capturedAt"),
        "closingIsLaterThanEntry": bool(
            closing and entry.get("capturedAt") is not None
            and closing.get("capturedAt") is not None
            and closing["capturedAt"] > entry["capturedAt"]),
        "clvConvention": clv_convention.CONVENTION_ID,
        "clvUnit": clv_convention.UNIT_CENTS,
    }
