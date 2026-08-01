"""
lib/edgelab/bets.py
======================
The canonical EdgeLab placed-bet ledger (Phase 1 section E): one shape,
covering both bets logged going forward (build_manual_bet_record) and
bets ingested from the two pre-existing, differently-shaped ledgers
(bets.json via scripts/log_manual_bet.py, data/bets.json via
scripts/log_session_bets.py -- see docs/EDGELAB_PHASE1.md's audit
section for the field-name reconciliation table).

Neither legacy ledger is modified or replaced; this module only reads
them and writes normalized copies into data/edgelab/bets/bets.jsonl,
upserting by betId so re-ingesting is idempotent (a bet whose legacy
record was later updated -- e.g. settled -- re-ingests as an update to
the same row, never a duplicate).
"""

from lib.edgelab import ids
from lib.edgelab import SCHEMA_VERSION

_RESULT_ENUM = {"WIN", "LOSS", "PUSH", "VOID"}


def _content_fingerprint(record):
    """Record content excluding volatile bookkeeping timestamps, for change detection."""
    r = dict(record)
    r.pop("createdAt", None)
    r.pop("updatedAt", None)
    prov = dict(r.get("provenance") or {})
    prov.pop("ingestedAt", None)
    r["provenance"] = prov
    return r


def reconcile_with_existing(new_record, existing_by_id):
    """
    Before upserting a freshly-normalized legacy-ingest record, compare it
    against whatever is already stored under the same betId (ignoring
    volatile timestamps). Unchanged content is returned byte-identical to
    the existing row (so a rerun against an unchanged legacy ledger is a
    true no-op, not a timestamp-only diff on every row every day);
    genuinely changed content keeps its original createdAt but gets a
    fresh updatedAt.
    """
    old = existing_by_id.get(new_record["betId"])
    if old is None:
        return new_record
    if _content_fingerprint(old) == _content_fingerprint(new_record):
        return old
    merged = dict(new_record)
    merged["createdAt"] = old.get("createdAt", new_record["createdAt"])
    return merged


def _normalize_price_to_fraction(value):
    """Kalshi prices show up as either a 0-1 fraction or 0-100 cents across legacy ledgers. Never guesses when value is None."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v / 100.0, 4) if v > 1.0 else round(v, 4)


def _parse_game_string(game: str):
    """'AWAY@HOME' -> (away, home). Returns (None, None) if not that shape."""
    if not game or "@" not in game:
        return None, None
    away, home = game.split("@", 1)
    return away.strip() or None, home.strip() or None


def _normalize_result(value):
    if value in _RESULT_ENUM:
        return value
    return None


def _derive_side(market_name):
    """
    Kalshi's RFI market has a single ticker per game where YES=YRFI (a run
    scores in the 1st) and NO=NRFI (no run) -- so a bet on "NRFI" is the
    NO side of that market, even though every other family's legacy
    ledger convention (you always buy the side named in your own ticker)
    is YES. Every other market family IS always YES on its own ticker.
    """
    name = (market_name or "").upper()
    if "NRFI" in name:
        return "NO"
    return "YES"


def _derive_status(result):
    if result is None:
        return "pending"
    if result == "VOID":
        return "void"
    return "settled"


def build_manual_bet_record(
    market_ticker, selection, stake, entry_price, entry_timestamp,
    *, game_id=None, event_ticker=None, series_ticker=None, market_family=None,
    side="YES", threshold=None, contracts=None, estimated_payout=None,
    scheduled_start=None, source="MANUAL", recommendation_id=None,
    manual_fair_probability=None, model_fair_probability=None,
    estimated_edge_at_entry=None, confidence=None, data_quality=None,
    correlation_group=None, tracking_type=None, thesis_tags=None, rationale=None,
    created_at=None,
):
    """
    Build one PlacedBet record for a bet being logged right now (manual
    chat-analysis entry or otherwise). Only the fields that identify the
    exact contract and stake are required as function arguments -- every
    analytical field defaults to None/empty rather than blocking entry.
    """
    thesis_tags = list(thesis_tags or [])
    now = created_at or ids.utc_now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "betId": ids.build_bet_id(game_id, market_ticker, entry_timestamp),
        "gameId": game_id,
        "marketTicker": market_ticker,
        "eventTicker": event_ticker,
        "seriesTicker": series_ticker,
        "marketFamily": market_family,
        "selection": selection,
        "side": side,
        "threshold": threshold,
        "stake": stake,
        "entryPrice": entry_price,
        "entryTimestamp": entry_timestamp,
        "contracts": contracts,
        "estimatedPayout": estimated_payout,
        "scheduledStart": scheduled_start,
        "source": source,
        "recommendationId": recommendation_id,
        "manualFairProbability": manual_fair_probability,
        "modelFairProbability": model_fair_probability,
        "estimatedEdgeAtEntry": estimated_edge_at_entry,
        "confidence": confidence,
        "dataQuality": data_quality,
        "correlationGroup": correlation_group,
        "trackingType": tracking_type,
        "thesisTags": thesis_tags,
        "rationale": rationale,
        "status": "pending",
        "closingPrice": None,
        "clvQuoteId": None,
        "clv": None,
        "result": None,
        "returnAmount": None,
        "netProfitLoss": None,
        "createdAt": now,
        "updatedAt": None,
        "validationStatus": "valid",
        "provenance": {
            "sourceSystem": "manual_entry",
            "sourceFile": None,
            "sourceKey": None,
            "capturedAt": entry_timestamp,
            "ingestedAt": now,
        },
    }


def from_legacy_root_bets_record(record, index, source_file="bets.json"):
    """
    Normalize one record from the root bets.json ledger (written by
    scripts/log_manual_bet.py / scripts/write_pending_bets.py).
    """
    now = ids.utc_now_iso()
    date = record.get("date")
    away, home = _parse_game_string(record.get("game"))
    game_id = ids.build_game_id(None, date, away, home) if date and away and home else None
    entry_timestamp = record.get("entryTimestamp") or (f"{date}T00:00:00Z" if date else None)
    market_ticker = record.get("ticker") or record.get("marketTicker")
    result = _normalize_result(record.get("result"))
    created_by = record.get("createdBy") or ""
    source = "MODEL" if "write_pending_bets" in created_by else "OTHER"

    return {
        "schemaVersion": SCHEMA_VERSION,
        "betId": ids.build_bet_id(game_id, market_ticker, entry_timestamp),
        "gameId": game_id,
        "marketTicker": market_ticker,
        "eventTicker": record.get("eventTicker"),
        "seriesTicker": record.get("seriesTicker"),
        "marketFamily": record.get("marketIdentity") or record.get("market"),
        "selection": f"{record.get('market')} {record.get('side') or record.get('betSide') or ''}".strip(),
        "side": _derive_side(record.get("market")),
        "threshold": record.get("line"),
        "stake": record.get("betSize") if record.get("betSize") is not None else record.get("stake"),
        "entryPrice": _normalize_price_to_fraction(
            record.get("actualEntryPrice") if record.get("actualEntryPrice") is not None else record.get("kalshiPrice")
        ),
        "entryTimestamp": entry_timestamp,
        "contracts": None,
        "estimatedPayout": None,
        "scheduledStart": record.get("scheduledStartTime"),
        "source": source,
        "recommendationId": None,
        "manualFairProbability": None,
        "modelFairProbability": record.get("modelProb"),
        "estimatedEdgeAtEntry": record.get("edgePct"),
        "confidence": record.get("confidenceTier"),
        "dataQuality": "REAL_MONEY_BLOCKED" if record.get("realMoneyBlocked") else None,
        "correlationGroup": None,
        "trackingType": None,
        "thesisTags": [],
        "rationale": None,
        "status": _derive_status(result),
        "closingPrice": _normalize_price_to_fraction(record.get("closingLine") or record.get("closingLinePct")),
        "clvQuoteId": None,
        "clv": record.get("clv"),
        "result": result,
        "returnAmount": None,
        "netProfitLoss": record.get("pl"),
        "createdAt": now,
        "updatedAt": now,
        "validationStatus": "valid" if market_ticker else "warning",
        "provenance": {
            "sourceSystem": "bets_json",
            "sourceFile": source_file,
            "sourceKey": record.get("id") or str(index),
            "capturedAt": entry_timestamp,
            "ingestedAt": now,
        },
    }


def from_legacy_session_bets_record(record, index, source_file="data/bets.json"):
    """
    Normalize one record from data/bets.json (written by
    scripts/log_session_bets.py).
    """
    now = ids.utc_now_iso()
    date = record.get("date")
    away, home = _parse_game_string(record.get("game"))
    game_id = ids.build_game_id(None, date, away, home) if date and away and home else None
    entry_timestamp = record.get("timestamp") or (f"{date}T00:00:00Z" if date else None)
    market_ticker = record.get("ticker")
    result = _normalize_result(record.get("result"))
    bet_type = (record.get("type") or "").lower()
    tracking_type = {"real": "REAL", "paper": "PAPER", "probe": "REAL_PROBE"}.get(bet_type)
    origin = record.get("origin") or ""
    source = "MANUAL" if origin == "session_analysis" else "OTHER"

    return {
        "schemaVersion": SCHEMA_VERSION,
        "betId": ids.build_bet_id(game_id, market_ticker, entry_timestamp),
        "gameId": game_id,
        "marketTicker": market_ticker,
        "eventTicker": None,
        "seriesTicker": None,
        "marketFamily": record.get("market"),
        "selection": f"{record.get('market')} {record.get('betTeam') or record.get('side') or ''}".strip(),
        "side": _derive_side(record.get("market")),
        "threshold": None,
        "stake": record.get("stake"),
        "entryPrice": _normalize_price_to_fraction(record.get("entryPrice")),
        "entryTimestamp": entry_timestamp,
        "contracts": None,
        "estimatedPayout": None,
        "scheduledStart": record.get("scheduledStartTime"),
        "source": source,
        "recommendationId": None,
        "manualFairProbability": None,
        "modelFairProbability": record.get("modelProb"),
        "estimatedEdgeAtEntry": record.get("edgePct"),
        "confidence": record.get("confidence"),
        "dataQuality": None,
        "correlationGroup": None,
        "trackingType": tracking_type,
        "thesisTags": [],
        "rationale": record.get("notes"),
        "status": _derive_status(result),
        "closingPrice": _normalize_price_to_fraction(record.get("closingPrice")),
        "clvQuoteId": None,
        "clv": record.get("clv"),
        "result": result,
        "returnAmount": None,
        "netProfitLoss": record.get("pl"),
        "createdAt": now,
        "updatedAt": now,
        "validationStatus": "valid" if market_ticker else "warning",
        "provenance": {
            "sourceSystem": "data_bets_json",
            "sourceFile": source_file,
            "sourceKey": str(index),
            "capturedAt": entry_timestamp,
            "ingestedAt": now,
        },
    }
