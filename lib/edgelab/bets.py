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

import json
from datetime import datetime

from lib.edgelab import ids, schema, storage
from lib.edgelab import DEFAULT_PLATFORM, DEFAULT_SPORT, SCHEMA_VERSION

_RESULT_ENUM = {"WIN", "LOSS", "PUSH", "VOID"}
_ENTRY_METHODS = {
    "MANUAL_GITHUB_FORM", "MANUAL_CHAT_CONFIRMED",
    "PRODUCTION_RECOMMENDATION_CONFIRMED", "LEGACY_BACKFILL", "IMPORTED_RECEIPT",
}


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
    *, game_id=None, game_date=None, matchup=None, event_ticker=None, series_ticker=None,
    market_family=None, market_horizon=None,
    side="YES", threshold=None, contracts=None, estimated_payout=None,
    scheduled_start=None, entry_odds=None,
    source="MANUAL", entry_method=None, recommendation_id=None, model_evaluation_id=None,
    production_run_id=None, snapshot_id=None, replay_run_id=None,
    manual_fair_probability=None, model_fair_probability=None,
    estimated_edge_at_entry=None, model_supported=None, confidence=None, data_quality=None,
    correlation_group=None, correlation_groups=None, tracking_type=None,
    thesis_tags=None, rationale=None, record_status="ACTIVE",
    created_at=None, sport=DEFAULT_SPORT, platform=DEFAULT_PLATFORM,
):
    """
    Build one PlacedBet record for a bet being logged right now (manual
    chat-analysis entry, a confirmed production recommendation, or the
    GitHub Actions entry form). Only the fields that identify the exact
    contract and stake are required as function arguments -- every
    analytical field defaults to None/empty rather than blocking entry.
    Never fabricates model fields for a manual bet with no model
    evaluation: model_supported/modelFairProbability/estimatedEdgeAtEntry
    are exactly whatever the caller passes, never inferred here.

    sport/platform default to today's only real values (MLB/Kalshi) but,
    unlike the automated ingestion writers below, are overridable here --
    this is the one write path a human calls directly, so it's the
    cheapest place to support a future non-MLB/non-Kalshi manual entry
    without a code change elsewhere.

    This function only BUILDS the record dict -- it does not write
    anything. Callers that want duplicate/conflict detection, locking,
    and a receipt must pass the result to write_placed_bet(); do not
    call storage.upsert_records directly on a record built here (see
    write_placed_bet's docstring for why one canonical write path
    matters).
    """
    thesis_tags = list(thesis_tags or [])
    correlation_groups = list(correlation_groups or [])
    if entry_method is not None and entry_method not in _ENTRY_METHODS:
        raise ValueError(f"entry_method must be one of {sorted(_ENTRY_METHODS)}, got {entry_method!r}")
    now = created_at or ids.utc_now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "betId": ids.build_bet_id(game_id, market_ticker, entry_timestamp),
        "gameId": game_id,
        "gameDate": game_date,
        "matchup": matchup,
        "sport": sport,
        "platform": platform,
        "marketTicker": market_ticker,
        "eventTicker": event_ticker,
        "seriesTicker": series_ticker,
        "marketFamily": market_family,
        "marketHorizon": market_horizon,
        "selection": selection,
        "side": side,
        "threshold": threshold,
        "stake": stake,
        "entryPrice": entry_price,
        "entryOdds": entry_odds,
        "entryTimestamp": entry_timestamp,
        "contracts": contracts,
        "estimatedPayout": estimated_payout,
        "scheduledStart": scheduled_start,
        "source": source,
        "entryMethod": entry_method,
        "recommendationId": recommendation_id,
        "modelEvaluationId": model_evaluation_id,
        "productionRunId": production_run_id,
        "snapshotId": snapshot_id,
        "replayRunId": replay_run_id,
        "manualFairProbability": manual_fair_probability,
        "modelFairProbability": model_fair_probability,
        "estimatedEdgeAtEntry": estimated_edge_at_entry,
        "modelSupported": model_supported,
        "confidence": confidence,
        "dataQuality": data_quality,
        "correlationGroup": correlation_group,
        "correlationGroups": correlation_groups,
        "trackingType": tracking_type,
        "thesisTags": thesis_tags,
        "rationale": rationale,
        "recordStatus": record_status,
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
        "gameDate": date,
        "matchup": record.get("game"),
        "sport": DEFAULT_SPORT,
        "platform": DEFAULT_PLATFORM,
        "marketTicker": market_ticker,
        "eventTicker": record.get("eventTicker"),
        "seriesTicker": record.get("seriesTicker"),
        "marketFamily": record.get("marketIdentity") or record.get("market"),
        "marketHorizon": None,
        "selection": f"{record.get('market')} {record.get('side') or record.get('betSide') or ''}".strip(),
        "side": _derive_side(record.get("market")),
        "threshold": record.get("line"),
        "stake": record.get("betSize") if record.get("betSize") is not None else record.get("stake"),
        "entryPrice": _normalize_price_to_fraction(
            record.get("actualEntryPrice") if record.get("actualEntryPrice") is not None else record.get("kalshiPrice")
        ),
        "entryOdds": None,
        "entryTimestamp": entry_timestamp,
        "contracts": None,
        "estimatedPayout": None,
        "scheduledStart": record.get("scheduledStartTime"),
        "source": source,
        "entryMethod": "LEGACY_BACKFILL",
        "recommendationId": None,
        "modelEvaluationId": None,
        "productionRunId": None,
        "snapshotId": None,
        "replayRunId": None,
        "manualFairProbability": None,
        "modelFairProbability": record.get("modelProb"),
        "estimatedEdgeAtEntry": record.get("edgePct"),
        "modelSupported": None,
        "confidence": record.get("confidenceTier"),
        "dataQuality": "REAL_MONEY_BLOCKED" if record.get("realMoneyBlocked") else None,
        "correlationGroup": None,
        "correlationGroups": [],
        "trackingType": None,
        "thesisTags": [],
        "rationale": None,
        "recordStatus": "ACTIVE",
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
        "gameDate": date,
        "matchup": record.get("game"),
        "sport": DEFAULT_SPORT,
        "platform": DEFAULT_PLATFORM,
        "marketTicker": market_ticker,
        "eventTicker": None,
        "seriesTicker": None,
        "marketFamily": record.get("market"),
        "marketHorizon": None,
        "selection": f"{record.get('market')} {record.get('betTeam') or record.get('side') or ''}".strip(),
        "side": _derive_side(record.get("market")),
        "threshold": None,
        "stake": record.get("stake"),
        "entryPrice": _normalize_price_to_fraction(record.get("entryPrice")),
        "entryOdds": None,
        "entryTimestamp": entry_timestamp,
        "contracts": None,
        "estimatedPayout": None,
        "scheduledStart": record.get("scheduledStartTime"),
        "source": source,
        "entryMethod": "LEGACY_BACKFILL",
        "recommendationId": None,
        "modelEvaluationId": None,
        "productionRunId": None,
        "snapshotId": None,
        "replayRunId": None,
        "manualFairProbability": None,
        "modelFairProbability": record.get("modelProb"),
        "estimatedEdgeAtEntry": record.get("edgePct"),
        "modelSupported": None,
        "confidence": record.get("confidence"),
        "dataQuality": None,
        "correlationGroup": None,
        "correlationGroups": [],
        "trackingType": tracking_type,
        "thesisTags": [],
        "rationale": record.get("notes"),
        "recordStatus": "ACTIVE",
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


def link_bets_to_recommendations(bets, recommendations):
    """
    Backfills PlacedBet.recommendationId/modelEvaluationId (Phase 2
    Milestone 3, docs/EDGELAB_MODEL_EVALUATION.md) for bets whose
    marketTicker matches a Recommendation this ingestion run just built.
    A separate, explicit backfill step is needed (rather than only
    setting these at bet-creation time) because the two ledgers are
    written on different schedules -- a bet can be logged before or
    after the day's recommendation/model-evaluation ledger updates for
    the same ticker.

    Never overwrites a field that's already set (a bet that already
    carries a real link -- however it got there -- keeps it), and never
    fabricates a link for a ticker with no matching recommendation.
    Returns only the bets that actually gained a new link, as full
    updated copies, so a caller can `storage.upsert_records` just those
    rows -- the exact mechanism scripts/edgelab/settle_markets.py already
    uses to update existing PlacedBet rows in place.
    """
    by_ticker = {}
    for rec in recommendations:
        ticker = rec.get("marketTicker")
        if ticker and ticker not in by_ticker:
            by_ticker[ticker] = (rec.get("recommendationId"), rec.get("modelEvaluationId"))

    updated = []
    for bet in bets:
        match = by_ticker.get(bet.get("marketTicker"))
        if not match:
            continue
        recommendation_id, model_evaluation_id = match
        new_bet = dict(bet)
        changed = False
        if not new_bet.get("recommendationId") and recommendation_id:
            new_bet["recommendationId"] = recommendation_id
            changed = True
        if not new_bet.get("modelEvaluationId") and model_evaluation_id:
            new_bet["modelEvaluationId"] = model_evaluation_id
            new_bet["modelSupported"] = True
            changed = True
        if changed:
            new_bet["updatedAt"] = ids.utc_now_iso()
            updated.append(new_bet)
    return updated


# ---------------------------------------------------------------------------
# Canonical write API (Canonical Placed-Bet Ledger milestone).
#
# write_placed_bet() is THE one function every entry surface must call to
# record a bet -- scripts/edgelab/log_bet.py, the "Record Placed Bet"
# GitHub Actions form, and any future chat-driven writer. It is the only
# place duplicate-vs-conflict-vs-tranche detection is implemented; no
# other script may append/upsert directly onto data/edgelab/bets/bets.jsonl
# for a NEW bet (ingest_existing_bets.py's bulk legacy-reconciliation path
# is the one deliberate exception -- see its own module docstring -- since
# it has different bulk-upsert semantics and already de-dupes via
# reconcile_with_existing before ever reaching storage).
# ---------------------------------------------------------------------------

def _parse_entry_timestamp(ts):
    if not ts:
        return None
    try:
        # entryTimestamp is always written as "...Z" (UTC) by every writer
        # in this module -- normalize the one format we ourselves produce.
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _find_near_duplicates(record, existing_rows, window_seconds):
    """
    Informational only -- NEVER blocks a write, and never changes what
    gets recorded. Flags other ACTIVE bets on the same ticker+side placed
    within `window_seconds` of this one (a plausible second tranche, or
    two chats/people submitting "the same" bet independently) so a
    receipt reader can double-check it was intentional. A genuine second
    tranche is always written regardless of this warning.
    """
    this_dt = _parse_entry_timestamp(record.get("entryTimestamp"))
    ticker = record.get("marketTicker")
    side = record.get("side")
    if this_dt is None or not ticker:
        return []
    warnings = []
    for row in existing_rows:
        if row.get("betId") == record.get("betId"):
            continue
        if row.get("marketTicker") != ticker or row.get("side") != side:
            continue
        if (row.get("recordStatus") or "ACTIVE") == "CANCELLED":
            continue
        other_dt = _parse_entry_timestamp(row.get("entryTimestamp"))
        if other_dt is None:
            continue
        if abs((this_dt - other_dt).total_seconds()) <= window_seconds:
            warnings.append({
                "betId": row.get("betId"),
                "entryTimestamp": row.get("entryTimestamp"),
                "stake": row.get("stake"),
                "entryPrice": row.get("entryPrice"),
            })
    return warnings


def _diff_fields(old, new):
    """Field-level diff between two records' content fingerprints (volatile timestamps excluded)."""
    old_fp = _content_fingerprint(old)
    new_fp = _content_fingerprint(new)
    fields = sorted(set(old_fp) | set(new_fp))
    diffs = []
    for field in fields:
        if old_fp.get(field) != new_fp.get(field):
            diffs.append({"field": field, "existing": old_fp.get(field), "incoming": new_fp.get(field)})
    return diffs


def _potential_gross_return(stake, entry_price):
    """Stake * (1/entryPrice) -- the full return INCLUDING stake if this bet wins. Never a win/loss calc; that requires settlement evidence."""
    try:
        stake = float(stake)
        entry_price = float(entry_price)
    except (TypeError, ValueError):
        return None
    if not (0 < entry_price < 1):
        return None
    return round(stake * (1.0 / entry_price), 2)


def _linkage_status(record):
    linked = [
        name for name, field in (
            ("recommendation", "recommendationId"),
            ("modelEvaluation", "modelEvaluationId"),
            ("productionRun", "productionRunId"),
            ("snapshot", "snapshotId"),
            ("replayRun", "replayRunId"),
        ) if record.get(field)
    ]
    return ("LINKED" if linked else "UNLINKED"), linked


def build_receipt(record, *, success, duplicate_status, errors=None, conflicting_fields=None, near_duplicates=None):
    """
    The one receipt shape every write returns (see
    docs/CANONICAL_BET_LEDGER.md's receipt semantics section). Never
    claims a bet is saved unless `success` is True and `duplicateStatus`
    is NEW/DUPLICATE_NOOP/CORRECTED -- CONFLICT and INVALID both mean
    nothing was written.
    """
    linkage_status, linked = _linkage_status(record)
    return {
        "success": success,
        "betId": record.get("betId"),
        "market": {
            "marketTicker": record.get("marketTicker"),
            "selection": record.get("selection"),
            "side": record.get("side"),
        },
        "stake": record.get("stake"),
        "entryPrice": record.get("entryPrice"),
        "potentialGrossReturn": _potential_gross_return(record.get("stake"), record.get("entryPrice")),
        "timestamp": record.get("entryTimestamp"),
        "linkageStatus": linkage_status,
        "linkedEntities": linked,
        "duplicateStatus": duplicate_status,
        "settlementStatus": record.get("status"),
        "clvStatus": "AVAILABLE" if record.get("clv") is not None else "UNAVAILABLE",
        "errors": errors or [],
        "conflictingFields": conflicting_fields or [],
        "nearDuplicateWarnings": near_duplicates or [],
        "generatedAt": ids.utc_now_iso(),
    }


def write_placed_bet(record, *, path=None, on_conflict="reject", near_duplicate_window_seconds=180):
    """
    THE canonical write function for the placed-bet ledger. Validates,
    detects duplicates/conflicts, writes atomically under a same-host
    lock (lib.edgelab.storage.locked), and returns a receipt.

    Duplicate/tranche/conflict semantics (Canonical Placed-Bet Ledger
    milestone, requirement 6):
      - No existing row with this betId -> INSERT. duplicateStatus="NEW".
        A genuine second tranche on the same ticker naturally lands here:
        its entryTimestamp differs from the first tranche's, so
        ids.build_bet_id derives a DIFFERENT betId -- it is never
        confused with a duplicate of the first tranche.
      - An existing row with this betId whose content is identical (see
        _content_fingerprint -- volatile timestamps excluded) ->
        deterministic no-op. duplicateStatus="DUPLICATE_NOOP", nothing
        is written, the ALREADY-STORED row is returned in the receipt.
        Retrying an identical submission (e.g. a chat message resent, or
        two chats independently reporting the exact same confirmed bet)
        is always safe to call again.
      - An existing row with this betId whose content DIFFERS -> refused
        by default. duplicateStatus="CONFLICT", nothing is written, and
        `conflictingFields` in the receipt lists exactly what differs.
        This is the case the old upsert-only path silently clobbered
        (same ticker+timestamp, different stake/price) -- now it must be
        resolved explicitly by the caller passing on_conflict="overwrite"
        (used for a deliberate correction; the corrected row's
        recordStatus is set to "CORRECTED", never silently).

    Never raises on a routine validation/duplicate/conflict outcome --
    callers must check receipt["success"] rather than assume a write
    happened. Raises ValueError only for a caller-programming-error
    on_conflict value.
    """
    if on_conflict not in ("reject", "overwrite"):
        raise ValueError(f"on_conflict must be 'reject' or 'overwrite', got {on_conflict!r}")

    path = path or storage.singleton_path("bets", "bets.jsonl")

    errors = schema.validate_record("placed_bet", record)
    if errors:
        return build_receipt(record, success=False, duplicate_status="INVALID", errors=errors)

    with storage.locked(path):
        existing_rows = list(storage.read_records(path))
        index_by_id = {row["betId"]: i for i, row in enumerate(existing_rows) if row.get("betId")}
        existing_index = index_by_id.get(record["betId"])
        existing = existing_rows[existing_index] if existing_index is not None else None

        near_dupes = _find_near_duplicates(record, existing_rows, near_duplicate_window_seconds)

        if existing is None:
            storage.write_all_records(path, existing_rows + [record])
            return build_receipt(record, success=True, duplicate_status="NEW", near_duplicates=near_dupes)

        if _content_fingerprint(existing) == _content_fingerprint(record):
            return build_receipt(existing, success=True, duplicate_status="DUPLICATE_NOOP", near_duplicates=near_dupes)

        diff = _diff_fields(existing, record)
        if on_conflict == "reject":
            return build_receipt(
                record, success=False, duplicate_status="CONFLICT",
                conflicting_fields=diff, near_duplicates=near_dupes,
            )

        merged = dict(record)
        merged["createdAt"] = existing.get("createdAt", record.get("createdAt"))
        merged["recordStatus"] = "CORRECTED"
        merged["updatedAt"] = ids.utc_now_iso()
        existing_rows[existing_index] = merged
        storage.write_all_records(path, existing_rows)
        return build_receipt(merged, success=True, duplicate_status="CORRECTED", conflicting_fields=diff, near_duplicates=near_dupes)
