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
_TIMESTAMP_STATUSES = {"PROVIDED", "NOT_PROVIDED", "APPROXIMATE"}


def _content_fingerprint(record):
    """
    Record content excluding volatile bookkeeping timestamps, for change
    detection. recordedAt is excluded alongside createdAt/updatedAt for
    the same reason: build_manual_bet_record stamps it fresh on every
    call (ids.utc_now_iso()), so an identical resubmission of a
    timestamp-free bet (entryTimestamp=None, identity from
    importBatchId+sourceBetKey) must still fingerprint identically -- a
    true no-op retry, never a spurious CONFLICT just because recordedAt
    ticked forward.

    sourceRow is ALSO excluded here, for the same "must not manufacture a
    spurious CONFLICT" reason: it's a row's 0-based position within its
    payload, kept purely for display/audit (never part of betId's
    identity -- see build_bet_id/build_manual_bet_record). Reordering a
    payload or inserting a new row ahead of an existing one changes that
    existing row's sourceRow on its next resubmission even though nothing
    about the BET itself changed -- without this exclusion, that
    positional shift alone would incorrectly flag as a content conflict
    (found via a real reordering/insertion test case; see
    tests/edgelab/test_import_bet_batch.py's
    test_reordering_rows_does_not_duplicate_bets and
    test_inserting_a_new_row_does_not_change_existing_identities).
    """
    r = dict(record)
    r.pop("createdAt", None)
    r.pop("updatedAt", None)
    r.pop("recordedAt", None)
    r.pop("sourceRow", None)
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

    LIFECYCLE INHERITANCE (bug fix). A legacy source ledger records how a
    bet was ENTERED. It knows nothing about how that bet later SETTLED --
    only the settlement/CLV pipeline can know that. Without inheriting
    the stored row's pipeline-owned fields first, a re-ingest whose
    source content differs at all returns the legacy record wholesale and
    resets status/result/netProfitLoss/returnAmount back to their
    entry-time values.

    That is not hypothetical. scripts/edgelab/ingest_existing_bets.py runs
    in .github/workflows/edgelab-postgame.yml IMMEDIATELY AFTER
    scripts/edgelab/settle_markets.py, so every nightly run wrote
    settlements and then partially reverted them. Measured on the
    canonical ledger, one unscoped re-ingest sent 36 settled wagers back
    to `pending` and moved the ledger's own P/L by tens of dollars.

    write_placed_bet has always guarded against exactly this (see its
    _inherit_lifecycle_fields call and that function's docstring); this
    path -- the one deliberate bulk exception to write_placed_bet -- did
    not, so it silently lost the guarantee the rest of the module
    provides.

    The inheritance here is NULL-SAFE rather than unconditional, and that
    distinction is load-bearing. Unlike an entry-time resubmission, a
    legacy ledger IS a legitimate settlement source for bets no objective
    settlement can reach -- the manual/standalone betting days, and every
    bet predating the settlement archive, where the human record is the
    only record. So a legacy row that actually SUPPLIES a result still
    applies it (see test_reconcile_with_existing_preserves_created_at_on
    _real_change, which encodes exactly that intent); what it may no
    longer do is let its ABSENCE of a result null out a value the
    settlement pipeline already established. Both field lists come from
    the same constants write_placed_bet uses, so there is one definition
    of "pipeline-owned" rather than two that can drift.
    """
    old = existing_by_id.get(new_record["betId"])
    if old is None:
        return new_record
    candidate = _inherit_lifecycle_if_not_supplied(new_record, old)
    if _content_fingerprint(old) == _content_fingerprint(candidate):
        return old
    merged = dict(candidate)
    merged["createdAt"] = old.get("createdAt", new_record["createdAt"])
    return merged


def _odds_to_implied_probability(odds):
    """
    Standard, deterministic American-odds-to-implied-probability
    conversion. Pure math, no guessing:
      positive odds O:  p = 100 / (O + 100)
      negative odds O:  p = -O / (-O + 100)
    Caller is responsible for having already established that `odds`
    really is American odds (see the two source-scoped normalizers
    below) -- this function does not itself decide that.
    """
    return round(100.0 / (odds + 100.0), 4) if odds > 0 else round(-odds / (-odds + 100.0), 4)


def american_odds_to_probability(odds):
    """
    Public wrapper around _odds_to_implied_probability for entry surfaces
    that unambiguously collect American odds through their OWN explicitly
    named field (e.g. scripts/edgelab/import_bet_batch.py's `entryOdds`
    row field) -- unlike the legacy-ledger normalizers above, there is no
    magnitude ambiguity to resolve here: the caller already knows, from
    its own schema, that this number is American odds. Raises ValueError
    for odds in (-100, 100), which is not a legal American odds value.
    """
    if odds is None:
        return None
    odds = float(odds)
    if -100 < odds < 100:
        raise ValueError(f"{odds!r} is not a valid American odds value (must be <= -100 or >= 100)")
    return _odds_to_implied_probability(odds)


def _classify_price_value(value, *, allow_american_odds):
    """
    Low-level, source-agnostic numeric classifier -- returns
    (fraction_or_None, format_label, raw_odds_or_None).

    format_label is one of:
      "FRACTION"       -- already a valid 0-1 implied probability.
      "KALSHI_CENTS"   -- a whole number in (1, 100): Kalshi trades in
                          whole-cent increments only, so a non-integer
                          value in this range is never genuine Kalshi
                          cents (see "AMBIGUOUS" below).
      "AMERICAN_ODDS"  -- |v| >= 100, only ever classified this way when
                          `allow_american_odds=True` (see below).
      "AMBIGUOUS"      -- a non-integer value strictly between 1 and 100
                          (e.g. 1.35), OR |v| >= 100 when
                          allow_american_odds=False. Deliberately NOT
                          resolved: 1.35 could be American odds +135 with
                          a lost sign/digit, genuine decimal odds, or
                          malformed Kalshi input -- there is no way to
                          tell from the number alone, so this returns
                          (None, "AMBIGUOUS", None) rather than guess.
      "MALFORMED"      -- None or not parseable as a number.

    `allow_american_odds` is NOT inferred from the value -- it is a
    property of the CALLING SOURCE, declared explicitly by whichever of
    the two source-scoped wrapper functions below is used. This is the
    source-aware half of the design: the same number (e.g. -111) is
    classified differently depending on which field/source it came from,
    never from magnitude alone in isolation from that context.
    """
    if value is None:
        return None, "MALFORMED", None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None, "MALFORMED", None
    if 0 < v <= 1:
        return round(v, 4), "FRACTION", None
    if abs(v) >= 100:
        if not allow_american_odds:
            return None, "AMBIGUOUS", None
        return _odds_to_implied_probability(v), "AMERICAN_ODDS", v
    if 1 < v < 100 and v == int(v):
        return round(v / 100.0, 4), "KALSHI_CENTS", None
    return None, "AMBIGUOUS", None


def _normalize_kalshi_native_price(value):
    """
    STRICT normalization for a field whose own name/contract guarantees a
    pure Kalshi price -- root bets.json's actualEntryPrice/kalshiPrice/
    closingLine*. Never interprets American odds here, even when the
    magnitude would otherwise qualify: there is no verified evidence this
    source ever contains them (checked against the real committed file --
    every actualEntryPrice/kalshiPrice value there is already a clean
    0-1 fraction), so treating |v|>=100 as odds here would be guessing
    from a source that has never earned that latitude, not inferring
    from evidence. An out-of-range value here is corruption of a
    contractually-pure-Kalshi field, not an alternate valid encoding --
    returns None (missing required field -> the record fails schema
    validation and is excluded/warned, never guessed).
    """
    fraction, _, _ = _classify_price_value(value, allow_american_odds=False)
    return fraction


def _normalize_session_bets_price(value):
    """
    Source-aware normalization SPECIFIC to data/bets.json's entryPrice
    field (also reused for its closingPrice field, which real data shows
    is always already a clean fraction there). Confirmed against the
    real committed file (post-merge operational-readiness audit, then
    re-verified during PR #39's own maintainer review): this field mixes
    two genuinely different encodings across its 24 rows -- 5 are whole-
    cent Kalshi prices (e.g. 52, 48) and 19 are raw American odds (e.g.
    -135, +217) -- and EVERY ONE of those 19 conversions is independently
    cross-validated by the SAME record's own clv/pl fields (computed by
    the original legacy system, using the raw odds directly): recomputing
    CLV as (correctedEntryPrice - closingPrice)*100 and P/L via EdgeLab's
    own realized_return_for_bet formula against correctedEntryPrice
    reproduces the recorded clv/pl values exactly, for all 13 settled/
    CLV'd rows among the 19. This is why American-odds interpretation is
    given this latitude HERE specifically and not in
    _normalize_kalshi_native_price above -- it is backed by direct
    evidence from this exact source, not applied as a general heuristic.

    Returns (fraction_or_None, format_label, raw_odds_or_None) -- see
    _classify_price_value. Callers preserve raw_odds onto entryOdds so
    the original raw fact is never lost, even though entryPrice itself
    always ends up in probability space.
    """
    return _classify_price_value(value, allow_american_odds=True)


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


def resolve_recommendation_context(recommendation_id, game_date, storage_module=None):
    """
    Prospective Canonical Wager-Context Capture milestone: given a
    recommendationId a caller already has (or was just told about), look
    it up in that date's Recommendation ledger
    (data/edgelab/recommendations/<game_date>.jsonl) and return the
    decision-time context fields it carries, ready to snapshot onto a
    PlacedBet at entry time. Returns None (never fabricated) when
    recommendation_id or game_date is falsy, or when no row with that
    recommendationId exists in that date's ledger (e.g. the recommendation
    ledger hasn't been built yet, or recommendation_id was mistyped) --
    callers must treat None as "no context available", never retry with a
    guess.

    Scale conversions (verified against this schema's own convention,
    documented on each PlacedBet field this feeds -- see
    data/edgelab/schema_v1/placed_bet.schema.json):
      - modelFairProbability: Recommendation stores 0-100 (see
        lib.edgelab.recommendations, model_evaluation.schema.json) but
        PlacedBet.modelFairProbability is 0-1, matching entryPrice
        (confirmed by tests/edgelab/test_reports.py's rolling-window
        calibration fixtures, e.g. model_fair_prob=0.62) -- divided by
        100 here.
      - estimatedEdgeAtEntry: Recommendation.estimatedEdge is ALREADY
        percentage points (scripts/build_market_ledger.py's
        calibratedEdgeVsExecutable = rawEdge * calibrationFactor * 100,
        i.e. already post-friction/post-calibration -- not the raw
        model-vs-market gap), and PlacedBet.estimatedEdgeAtEntry uses the
        identical percentage-point scale (confirmed by
        tests/edgelab/test_calibration.py's edge-bucket fixtures, e.g.
        estimated_edge=3.99 bucketing into 2-4) -- copied verbatim, no
        conversion.
      - executablePriceAtEntry / betUpToPriceAtEntry: sourced from
        Recommendation.marketImpliedProbability / .priceCeiling, both
        0-100 (cents/percent) there -- divided by 100 to match
        entryPrice's 0-1 convention, for internal consistency across
        every price-space field on PlacedBet.
      - confidence / modelEvaluationId: copied verbatim (already the
        same representation on both entities).

    Never returns manualFairProbability -- a Recommendation has no such
    concept; that field is supplied ONLY by an explicit human caller (see
    build_manual_bet_record's own docstring) and must never be inferred
    from model output.
    """
    if not recommendation_id or not game_date:
        return None
    storage_module = storage_module or storage
    rec = next(
        (r for r in storage_module.read_partition("recommendations", game_date) if r.get("recommendationId") == recommendation_id),
        None,
    )
    if rec is None:
        return None

    def _pct_to_fraction(v):
        return round(v / 100.0, 4) if v is not None else None

    return {
        "modelFairProbability": _pct_to_fraction(rec.get("modelFairProbability")),
        "estimatedEdgeAtEntry": rec.get("estimatedEdge"),
        "confidence": rec.get("confidence"),
        "executablePriceAtEntry": _pct_to_fraction(rec.get("marketImpliedProbability")),
        "betUpToPriceAtEntry": _pct_to_fraction(rec.get("priceCeiling")),
        "modelEvaluationId": rec.get("modelEvaluationId"),
    }


_EXECUTION_ECONOMICS_FIELDS = (
    "contractCost", "averageFillPrice", "entryFees", "exitFees", "totalFees",
    "actualCashConsumed", "unusedAllocatedCash",
    "grossCashReturned", "grossSettlementPayout", "exitSaleProceeds", "realizedROI",
    "executionStatus", "feeStatus", "feeType", "feeMultiplier", "feeSource",
    "feeScheduleVersion", "feeEffectiveDate", "economicsSource", "economicsConfidence",
)


def _execution_economics_defaults(execution_economics):
    """
    Kalshi Fee-Aware Execution Economics milestone: expands an optional,
    pre-built execution_economics dict (same pattern as
    market_observation_linkage -- never computed here, only stored
    verbatim) into this record's 18 execution-economics fields, each
    defaulting to None when execution_economics is None or omits it.
    Rejects an unknown key so a caller's typo (e.g. 'contractsCost')
    fails loudly at build time instead of silently vanishing.
    """
    execution_economics = execution_economics or {}
    unknown = set(execution_economics) - set(_EXECUTION_ECONOMICS_FIELDS)
    if unknown:
        raise ValueError(f"execution_economics has unknown field(s): {sorted(unknown)}")
    return {field: execution_economics.get(field) for field in _EXECUTION_ECONOMICS_FIELDS}


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
    timestamp_status=None, import_batch_id=None, source_bet_key=None, source_row=None, market_observation_linkage=None,
    executable_price_at_entry=None, bet_up_to_price_at_entry=None,
    share_card_evidence=None, execution_economics=None,
):
    """
    Build one PlacedBet record for a bet being logged right now (manual
    chat-analysis entry, a confirmed production recommendation, or the
    GitHub Actions entry form). Only the fields that identify the exact
    contract and stake are required as function arguments -- every
    analytical field defaults to None/empty rather than blocking entry.
    Never fabricates model fields for a manual bet with no model
    evaluation: model_supported/modelFairProbability/estimatedEdgeAtEntry
    are exactly whatever the caller passes, never inferred here -- but
    model_supported=True specifically REQUIRES a real model_evaluation_id
    (raises ValueError otherwise; see below) precisely so a caller can't
    fabricate model backing that doesn't exist. modelEvaluationId itself
    is never independently derivable at entry time (it's backfilled later
    by lib.edgelab.bets.link_bets_to_recommendations once that day's
    Recommendation/ModelEvaluation ledger exists) -- callers should
    normally leave both None at entry and let the backfill set both
    together, only passing model_evaluation_id explicitly when one is
    already genuinely known (e.g. IMPORTED_RECEIPT).

    sport/platform default to today's only real values (MLB/Kalshi) but,
    unlike the automated ingestion writers below, are overridable here --
    this is the one write path a human calls directly, so it's the
    cheapest place to support a future non-MLB/non-Kalshi manual entry
    without a code change elsewhere.

    Timestamp-Optional Manual Imports milestone: entry_timestamp may now
    be None -- the user should never be required to look up an exact
    placement time. recordedAt (an automatically generated UTC logging
    timestamp, NEVER represented as the placement time) is always set.
    timestamp_status defaults to "PROVIDED" when entry_timestamp is given
    and "NOT_PROVIDED" when it is None; pass timestamp_status explicitly
    ONLY to mark a supplied entry_timestamp as "APPROXIMATE" (a caller's
    best guess it does not vouch for as exact) -- never pass
    "NOT_PROVIDED" together with a real entry_timestamp, and never pass
    "PROVIDED"/"APPROXIMATE" with entry_timestamp=None (both raise
    ValueError; this function refuses to let a caller mislabel a bet's
    own timestamp confidence).

    When entry_timestamp is None, betId identity comes from
    hash(import_batch_id, source_bet_key, market_ticker, side) instead of
    hash(gameId, marketTicker, entryTimestamp) -- see
    lib.edgelab.ids.build_bet_id. import_batch_id/source_bet_key are
    therefore REQUIRED together whenever entry_timestamp is None (raises
    ValueError otherwise -- a timestamp-free bet with no batch identity
    would only get an unstable ULID token, defeating "re-running the same
    import is a no-op"). source_bet_key must be a STABLE, caller-assigned
    per-row identifier (e.g. "bet-01") -- never the row's raw list
    position. A prior design used import_batch_id defaulted from a hash
    of the payload's own gameDate(s), and source_row (the list index) as
    the row discriminator; both were found (maintainer review) to be
    unsafe: gameDate-derived batch ids collide across separate same-day
    sessions, and a list-index row key changes identity when rows are
    reordered or a new row is inserted earlier in the list. Both
    import_batch_id and source_bet_key must now be supplied explicitly by
    the calling client (Claude generates them during the handoff -- the
    end user never sees or tracks them).

    source_row (the row's 0-based position in its payload) is still
    accepted and stored for display/audit purposes only -- it is NEVER
    part of betId's identity computation, exactly so reordering/insertion
    can't corrupt an existing row's identity.

    market_observation_linkage: an optional pre-computed linkage dict
    (see lib.edgelab.observation_linkage.build_linkage_field) describing
    the best archived pregame MarketObservation for this exact ticker.
    Never fabricated here -- this function only stores whatever the
    caller already resolved; pass None (the default) for a bet with no
    linkage attempt, or a dict with linkageStatus="UNLINKED" when linkage
    was attempted but found nothing.

    executable_price_at_entry / bet_up_to_price_at_entry (Prospective
    Canonical Wager-Context Capture milestone): like
    market_observation_linkage, these are never computed here -- pass
    whatever a caller already resolved (typically via
    resolve_recommendation_context() above, when a recommendation_id
    exists) or leave both None for a bet with no such context. Once set,
    they are ordinary fields subject to write_placed_bet's normal
    identical-content-is-a-no-op / differing-content-is-a-CONFLICT
    handling -- never silently overwritten by a later resubmission,
    exactly like every other entry-context field.

    share_card_evidence (Kalshi Fee-Aware Execution Economics milestone):
    an optional pre-built dict of RAW Kalshi share-card facts
    (shareCardInitialCost/shareCardPaidOut/shareCardDisplayedProbability/
    shareCardPositionState/capturedNote/recordedAt) -- stored verbatim,
    never interpreted here. `stake` above is NEVER derived from this --
    see lib.edgelab.execution_economics.determine_canonical_stake for the
    evidence-priority ladder a caller should run BEFORE calling this
    function to decide what `stake` itself should be.

    execution_economics: an optional pre-built dict of already-resolved
    execution-economics fields (contractCost/averageFillPrice/entryFees/
    exitFees/totalFees/grossCashReturned/grossSettlementPayout/
    exitSaleProceeds/realizedROI/executionStatus/feeStatus/feeType/
    feeMultiplier/feeSource/feeScheduleVersion/feeEffectiveDate/
    economicsSource/economicsConfidence -- see
    lib.edgelab.bets._EXECUTION_ECONOMICS_FIELDS) -- like
    market_observation_linkage, never computed here, only stored. Most
    callers at ENTRY time (before a fee is even known) leave this None;
    it is far more commonly populated later via a dedicated correction
    (mirroring confirm_realized_return's pattern) than at initial entry.

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

    if entry_timestamp is None:
        if timestamp_status is not None and timestamp_status != "NOT_PROVIDED":
            raise ValueError("timestamp_status must be 'NOT_PROVIDED' (or omitted) when entry_timestamp is None")
        timestamp_status = "NOT_PROVIDED"
        if import_batch_id is None or source_bet_key is None:
            raise ValueError(
                "entry_timestamp is None -- import_batch_id and source_bet_key are both required (and must "
                "be explicit, never auto-derived from a shared field like gameDate or a list position) so "
                "this bet's identity is stable across reruns, reordering, and separate same-day sessions "
                "(see lib.edgelab.ids.build_bet_id)"
            )
    else:
        if timestamp_status is None:
            timestamp_status = "PROVIDED"
        elif timestamp_status not in _TIMESTAMP_STATUSES or timestamp_status == "NOT_PROVIDED":
            raise ValueError(f"timestamp_status must be one of {sorted(_TIMESTAMP_STATUSES - {'NOT_PROVIDED'})} when entry_timestamp is provided, got {timestamp_status!r}")

    if model_supported and not model_evaluation_id:
        # Maintainer review finding: previously model_supported was
        # accepted verbatim with no check at all, so `log_bet.py
        # --model-supported` (with no --model-evaluation-id, which the
        # CLI doesn't even expose -- see its own docstring) could log a
        # purely manual bet falsely claiming real model backing,
        # corrupting this very milestone's own "model-supported vs.
        # manual" postmortem attribution.
        raise ValueError(
            "model_supported=True requires a real model_evaluation_id -- "
            "never fabricate model backing for a bet with no model evaluation"
        )
    now = created_at or ids.utc_now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "betId": ids.build_bet_id(game_id, market_ticker, entry_timestamp, import_batch_id=import_batch_id, source_bet_key=source_bet_key, side=side),
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
        "recordedAt": now,
        "timestampStatus": timestamp_status,
        "importBatchId": import_batch_id,
        "sourceBetKey": source_bet_key,
        "sourceRow": source_row,
        "marketObservationLinkage": market_observation_linkage,
        "executablePriceAtEntry": executable_price_at_entry,
        "betUpToPriceAtEntry": bet_up_to_price_at_entry,
        "contracts": contracts,
        "estimatedPayout": estimated_payout,
        **_execution_economics_defaults(execution_economics),
        "shareCardEvidence": share_card_evidence,
        "executionEconomicsReconciliation": None,
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

    raw_entry_price = record.get("actualEntryPrice") if record.get("actualEntryPrice") is not None else record.get("kalshiPrice")
    entry_price = _normalize_kalshi_native_price(raw_entry_price)
    entry_price_ambiguous = raw_entry_price is not None and entry_price is None

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
        "entryPrice": entry_price,
        "entryOdds": None,
        "entryTimestamp": entry_timestamp,
        "recordedAt": now,
        "timestampStatus": "PROVIDED",
        "importBatchId": None,
        "sourceBetKey": None,
        "sourceRow": None,
        "marketObservationLinkage": None,
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
        "closingPrice": _normalize_kalshi_native_price(record.get("closingLine") or record.get("closingLinePct")),
        "clvQuoteId": None,
        "clv": record.get("clv"),
        "result": result,
        "returnAmount": None,
        "netProfitLoss": record.get("pl"),
        "createdAt": now,
        "updatedAt": now,
        "validationStatus": "warning" if (not market_ticker or entry_price_ambiguous) else "valid",
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

    raw_entry_price = record.get("entryPrice")
    entry_price, entry_price_format, entry_odds = _normalize_session_bets_price(raw_entry_price)
    entry_price_ambiguous = raw_entry_price is not None and entry_price_format == "AMBIGUOUS"

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
        "entryPrice": entry_price,
        "entryOdds": entry_odds,
        "entryTimestamp": entry_timestamp,
        "recordedAt": now,
        "timestampStatus": "PROVIDED",
        "importBatchId": None,
        "sourceBetKey": None,
        "sourceRow": None,
        "marketObservationLinkage": None,
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
        "closingPrice": _normalize_session_bets_price(record.get("closingPrice"))[0],
        "clvQuoteId": None,
        "clv": record.get("clv"),
        "result": result,
        "returnAmount": None,
        "netProfitLoss": record.get("pl"),
        "createdAt": now,
        "updatedAt": now,
        "validationStatus": "warning" if (not market_ticker or entry_price_ambiguous) else "valid",
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
    """
    build_manual_bet_record itself always writes "...Z" (UTC, whole
    seconds), but from_legacy_root_bets_record/from_legacy_session_bets_record
    pass a legacy ledger's own entryTimestamp/timestamp value through
    VERBATIM (never reformatted) -- real committed data includes rows like
    "2026-06-17T22:45:46.170900+00:00" (fractional seconds, explicit
    offset instead of "Z"). A strict "...Z"-only parse silently returned
    None for every such row, so near-duplicate detection produced zero
    warnings for any legacy-ingested bet even when a real near-duplicate
    existed (found during the maintainer review of this milestone) --
    fails safe (never a false warning) but quietly drops real coverage.
    datetime.fromisoformat (Python 3.11+) accepts both shapes.
    """
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
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


# Fields exclusively owned by the settlement/CLV pipeline (settle_markets.py,
# collect_clv.py) and by write_placed_bet's own correction bookkeeping --
# never legitimately known by a manually-(re)submitted entry record.
# build_manual_bet_record always initializes the settlement/CLV fields to
# pending/None (it has no keyword argument for them at all) and
# recordStatus defaults to "ACTIVE" -- so comparing/overwriting with a
# freshly-built record verbatim would otherwise silently reset an
# already-settled, CLV-tracked bet back to pending on every retry or
# correction of an unrelated entry-time field (found during the
# maintainer review of this milestone; see
# tests/edgelab/test_write_placed_bet.py's
# test_*_never_resets_settlement_or_clv_state tests). These 8 have NO
# caller-facing parameter in build_manual_bet_record at all -- a freshly
# built record's value for every one of them is ALWAYS None/"pending"/
# "ACTIVE", never anything else -- so unconditionally taking the
# existing row's value is always correct for this group.
_ALWAYS_PRESERVE_FIELDS = (
    "status", "result", "returnAmount", "netProfitLoss",
    "closingPrice", "clv", "clvQuoteId", "recordStatus",
)

# Fields that CAN be legitimately supplied by a caller (a correction may
# genuinely want to add/change a recommendationId, for instance) but are
# also routinely backfilled asynchronously by a separate process AFTER
# initial entry -- scripts/edgelab/build_recommendations.py's
# link_bets_to_recommendations() sets recommendationId/modelEvaluationId/
# modelSupported hours or days after a bet is first logged. Without this,
# an unrelated correction (e.g. fixing a typo'd stake) submitted after
# that backfill ran would silently null the linkage right back out,
# since build_manual_bet_record defaults every one of these to None
# unless the caller happens to pass it again (found during the
# maintainer review of this milestone, the same root cause as the
# lifecycle-field bug above, for a different field set). snapshotId/
# productionRunId/replayRunId are included defensively for the same
# reason even though nothing backfills them yet today (replayRunId's own
# schema description already documents it as backfill-only, never set
# at entry time).
_PRESERVE_IF_NOT_SUPPLIED_FIELDS = (
    "recommendationId", "modelEvaluationId", "modelSupported",
    "snapshotId", "productionRunId", "replayRunId",
)


def _inherit_lifecycle_fields(record, existing):
    """
    Carry the EXISTING row's pipeline-owned fields onto a freshly-built
    candidate record before comparing or overwriting, so an entry-time
    resubmission/correction can never know better than (and therefore
    never silently resets) state that only the settlement/CLV pipeline or
    an asynchronous recommendation-linkage backfill can legitimately set.
    `existing` is None for a genuinely new betId -- nothing to inherit.
    """
    if existing is None:
        return record
    merged = dict(record)
    for field in _ALWAYS_PRESERVE_FIELDS:
        merged[field] = existing.get(field)
    for field in _PRESERVE_IF_NOT_SUPPLIED_FIELDS:
        if merged.get(field) is None:
            merged[field] = existing.get(field)
    return merged


def _inherit_lifecycle_if_not_supplied(record, existing):
    """Lifecycle inheritance for the bulk legacy-ingest path.

    _inherit_lifecycle_fields is unconditional: an entry-time
    resubmission can never know better than the pipeline, full stop. A
    legacy LEDGER is different -- for manual/standalone days and for
    every bet predating the settlement archive it is the only record of
    an outcome that exists, so it must still be able to supply one.

    The rule is therefore keyed on RESULT, not applied field by field.
    A legacy row's `status` is derived from its own `result`
    (see _derive_status), so it is never None and a purely null-safe
    merge would let a legacy "pending" overwrite a stored "settled"
    while the result field itself was correctly preserved -- leaving the
    row internally inconsistent, which is worse than either outcome.

    So:
      * legacy row SUPPLIES a result -> it is a settlement source; take
        its result/status/P&L as given.
      * legacy row supplies NO result -> it is describing entry only;
        every pipeline-owned field is preserved from the stored row.

    Either way the async-backfilled linkage fields are preserved unless
    the legacy row actually carries them. Both field lists are shared
    with write_placed_bet, so there is one definition of "pipeline-owned".
    """
    if existing is None:
        return record
    merged = dict(record)
    if record.get("result") is None:
        for field in _ALWAYS_PRESERVE_FIELDS:
            merged[field] = existing.get(field)
    for field in _PRESERVE_IF_NOT_SUPPLIED_FIELDS:
        if merged.get(field) is None:
            merged[field] = existing.get(field)
    return merged


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
    obs_linkage = record.get("marketObservationLinkage") or {}
    if obs_linkage.get("linkageStatus") == "LINKED":
        linked.append("marketObservation")
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
        "timestampStatus": record.get("timestampStatus"),
        "recordedAt": record.get("recordedAt"),
        "marketObservationLinkage": record.get("marketObservationLinkage"),
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
        recordStatus is set to "CORRECTED", never silently). Comparison
        and the eventual merge both inherit the EXISTING row's
        settlement/CLV lifecycle fields first (see
        _inherit_lifecycle_fields) -- an entry-time correction (e.g.
        fixing a typo'd stake) can never silently reset an
        already-settled, CLV-tracked bet back to pending, and an
        identical retry of the original entry-time fields after
        settlement still correctly resolves to DUPLICATE_NOOP rather than
        a spurious CONFLICT against fields the retry was never trying to
        change in the first place.

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

        # A manually-(re)submitted record can never know better than the
        # settlement/CLV pipeline about this bet's own lifecycle state --
        # inherit it from the existing row before comparing/merging so a
        # retry or a correction of an unrelated field never resets an
        # already-settled bet back to pending (see _inherit_lifecycle_fields).
        candidate = _inherit_lifecycle_fields(record, existing)

        if _content_fingerprint(existing) == _content_fingerprint(candidate):
            return build_receipt(existing, success=True, duplicate_status="DUPLICATE_NOOP", near_duplicates=near_dupes)

        diff = _diff_fields(existing, candidate)
        if on_conflict == "reject":
            return build_receipt(
                record, success=False, duplicate_status="CONFLICT",
                conflicting_fields=diff, near_duplicates=near_dupes,
            )

        merged = dict(candidate)
        merged["createdAt"] = existing.get("createdAt", record.get("createdAt"))
        merged["recordStatus"] = "CORRECTED"
        merged["updatedAt"] = ids.utc_now_iso()
        existing_rows[existing_index] = merged
        storage.write_all_records(path, existing_rows)
        return build_receipt(merged, success=True, duplicate_status="CORRECTED", conflicting_fields=diff, near_duplicates=near_dupes)


def cancel_placed_bet(bet_id, reason, *, path=None):
    """
    Mark an existing bet CANCELLED (logged in error) -- schema requirement
    6/14's promise that a cancelled bet is "excluded from ROI/postmortem
    aggregation without deleting the audit trail" (found unimplemented
    during the maintainer review of this milestone: recordStatus's
    CANCELLED value existed in the schema with nothing able to actually
    set it). This is the one sanctioned way to do so -- it never deletes
    the row, never touches its stake/entryPrice/settlement/CLV fields,
    and is idempotent (cancelling an already-cancelled bet again is a
    no-op, not an error).

    Raises ValueError only for a caller-programming-error (empty reason),
    matching lib.edgelab.bankroll.build_bankroll_transaction's convention
    for ADJUSTMENT. Returns a structured result dict for every routine
    outcome (not found / already cancelled / cancelled now) -- never
    raises for those.
    """
    if not reason:
        raise ValueError("cancel_placed_bet requires a non-empty reason")

    path = path or storage.singleton_path("bets", "bets.jsonl")

    with storage.locked(path):
        existing_rows = list(storage.read_records(path))
        index_by_id = {row["betId"]: i for i, row in enumerate(existing_rows) if row.get("betId")}
        idx = index_by_id.get(bet_id)
        if idx is None:
            return {"success": False, "betId": bet_id, "error": "bet not found", "recordStatus": None}

        existing = existing_rows[idx]
        if (existing.get("recordStatus") or "ACTIVE") == "CANCELLED":
            return {
                "success": True, "betId": bet_id, "recordStatus": "CANCELLED",
                "alreadyCancelled": True, "reason": existing.get("recordStatusReason"),
            }

        merged = dict(existing)
        merged["recordStatus"] = "CANCELLED"
        merged["recordStatusReason"] = reason
        merged["updatedAt"] = ids.utc_now_iso()
        existing_rows[idx] = merged
        storage.write_all_records(path, existing_rows)
        return {"success": True, "betId": bet_id, "recordStatus": "CANCELLED", "alreadyCancelled": False, "reason": reason}


# ---------------------------------------------------------------------------
# Confirmed-receipt realized economics (separate from objective settlement
# outcome -- see confirm_realized_return/realized_bet_economics below).
# ---------------------------------------------------------------------------

def confirm_realized_return(bet_id, receipt_return, receipt_net_profit_loss, source, note=None, *, path=None):
    """
    Records a manually confirmed REAL cash receipt for an already-placed
    bet -- e.g. a Kalshi receipt showing a partial-fill/fee-adjusted
    return that this system's binary settlement model cannot itself
    compute (lib.edgelab.settlement.realized_return_for_bet only ever
    returns 0 / -stake / stake*(1/price - 1); a real partial sell has no
    representation there). This is the ONE sanctioned way to record that
    discrepancy.

    Deliberately narrow: NEVER writes result/status/returnAmount/
    netProfitLoss -- those remain settlement's own, independently
    derived, OBJECTIVE contract-outcome fields (write_placed_bet /
    scripts/edgelab/settle_markets.py), completely untouched by a
    confirmed receipt. Only the separate confirmedReceipt* fields are
    ever set here; realized_bet_economics() is the one place that reads
    them back to prefer real receipt economics over the derived ones for
    reporting/reconciliation. A LOSS can therefore still report a
    nonzero confirmed gross return -- that is the whole point, not a bug.

    Idempotent: confirming the identical (receipt_return,
    receipt_net_profit_loss, source, note) tuple again is a no-op.
    Confirming a genuinely different receipt for a betId that already
    has one is treated as a correction (still never touching
    recordStatus -- this is a receipt update, not a cancellation or a
    correction of the bet's own entry-time fields).

    Raises ValueError only for a caller-programming-error (missing
    source or a betId that doesn't exist -- unlike cancel_placed_bet,
    silently no-op'ing on a typo'd betId here would risk a confirmed
    receipt quietly landing nowhere). Never raises for the ordinary
    NEW-vs-CORRECTED-vs-DUPLICATE_NOOP outcomes.
    """
    if not source:
        raise ValueError("confirm_realized_return requires a non-empty source")

    path = path or storage.singleton_path("bets", "bets.jsonl")

    with storage.locked(path):
        existing_rows = list(storage.read_records(path))
        index_by_id = {row["betId"]: i for i, row in enumerate(existing_rows) if row.get("betId")}
        idx = index_by_id.get(bet_id)
        if idx is None:
            raise ValueError(f"confirm_realized_return: no bet with betId={bet_id!r} exists")

        existing = existing_rows[idx]
        already_confirmed = existing.get("confirmedReceiptAt") is not None
        if (
            existing.get("confirmedReceiptReturn") == receipt_return
            and existing.get("confirmedReceiptNetProfitLoss") == receipt_net_profit_loss
            and existing.get("confirmedReceiptSource") == source
            and existing.get("confirmedReceiptNote") == note
        ):
            return {
                "success": True, "betId": bet_id, "duplicateStatus": "DUPLICATE_NOOP",
                "confirmedReceiptReturn": existing.get("confirmedReceiptReturn"),
                "confirmedReceiptNetProfitLoss": existing.get("confirmedReceiptNetProfitLoss"),
            }

        now = ids.utc_now_iso()
        merged = dict(existing)
        merged["confirmedReceiptReturn"] = receipt_return
        merged["confirmedReceiptNetProfitLoss"] = receipt_net_profit_loss
        merged["confirmedReceiptSource"] = source
        merged["confirmedReceiptNote"] = note
        merged["confirmedReceiptAt"] = now
        merged["updatedAt"] = now
        existing_rows[idx] = merged
        storage.write_all_records(path, existing_rows)
        return {
            "success": True, "betId": bet_id,
            "duplicateStatus": "CORRECTED" if already_confirmed else "NEW",
            "confirmedReceiptReturn": receipt_return,
            "confirmedReceiptNetProfitLoss": receipt_net_profit_loss,
        }


def realized_bet_economics(bet):
    """
    Pure. Returns (gross_return, net_profit_loss) reflecting this bet's
    actual realized economics -- preferring a manually confirmed receipt
    (confirmedReceiptReturn/confirmedReceiptNetProfitLoss, set only via
    confirm_realized_return above) over the value this system's own
    binary WIN/LOSS/PUSH/VOID settlement model derived (returnAmount/
    netProfitLoss). Settlement's own objective result/status fields are
    never read or affected here -- this function is only ever about the
    dollar amounts.

    Falls back to the ordinary derived economics (stake + netProfitLoss
    for a WIN/PUSH/VOID, 0.0 for a LOSS, mirroring
    lib.edgelab.reports._postmortem_bet_row's existing convention) when
    no confirmed receipt is present. Returns (None, None) for a bet with
    neither a confirmed receipt nor a derived netProfitLoss (still
    pending) -- never a guess.
    """
    if bet.get("confirmedReceiptNetProfitLoss") is not None:
        return bet.get("confirmedReceiptReturn"), bet.get("confirmedReceiptNetProfitLoss")
    net_pl = bet.get("netProfitLoss")
    if net_pl is None:
        return None, None
    stake = bet.get("stake") or 0
    gross_return = round(stake + net_pl, 2) if bet.get("result") in ("WIN", "PUSH", "VOID") else 0.0
    return gross_return, net_pl
