"""
lib/edgelab/calibration.py
==============================
EdgeLab Phase 2 Milestone 2: the calibration engine
(docs/EDGELAB_CALIBRATION.md, docs/EDGELAB_PHASE2_DESIGN.md §5), built on
top of the Milestone 1 DuckDB query foundation (lib/edgelab/analytics.py).

This module is READ-ONLY, descriptive-statistics-only. Its only job is to
measure how well past model probabilities/edges/confidence levels lined
up with real settled outcomes. It never recommends a strategy change, it
never withholds a computed value based on its own sample-size status (the
status is a mandatory reading instruction, not a filter), and nothing
here feeds back into production betting/recommendation logic in any way.

Shared methodology (docs/EDGELAB_PHASE2_DESIGN.md §5.1, tiers per this
milestone's explicit instructions -- a different, three-tier scheme than
Milestone 1's single MIN_SAMPLE_SIZE=20 cutoff used elsewhere in
lib.edgelab.analytics):

    n < 20            -> INSUFFICIENT_SAMPLE  (noise, not evidence)
    20 <= n < 100      -> DESCRIPTIVE_ONLY      (a real number, not yet a
                                                  calibrated statistical
                                                  claim)
    n >= 100           -> CALIBRATED            (enough volume that the
                                                  reliability numbers are
                                                  a meaningful summary --
                                                  still not a trading
                                                  signal by itself)

Every calibration bucket below is measured over "decided" bets only --
settled bets whose result is WIN or LOSS (PUSH/VOID excluded from both
the win-rate numerator and denominator, since a push/void outcome can't
be compared against a predicted win probability). This keeps a single
`n` meaningful across every metric in a bucket row (win rate, ROI, CLV,
calibration error all share the same denominator), documented in
docs/EDGELAB_CALIBRATION.md.
"""
import collections
from datetime import datetime, timezone

from lib.edgelab.checkpoints import classify_checkpoint

MIN_N_INSUFFICIENT = 20   # n < 20  -> INSUFFICIENT_SAMPLE
MIN_N_CALIBRATED = 100    # n >= 100 -> CALIBRATED; between the two -> DESCRIPTIVE_ONLY

# A CLV value within this many cents/points of zero is treated as
# "neutral" by clv_sign_study() -- matches the 5-cent bucket width
# docs/EDGELAB_PHASE2_DESIGN.md §5.2 suggests for the fine-grained CLV
# bucket, so "neutral" isn't a separate, unrelated magic number.
NEUTRAL_CLV_BAND = 0.05

# A bet is "decided" for calibration purposes when it's been settled to
# a real win/loss outcome -- PUSH/VOID bets are real settled bets but
# have no win/loss to calibrate a predicted probability against, so
# they're excluded from every calibration bucket's n.
_DECIDED_BETS_FILTER = "status = 'settled' AND result IN ('WIN', 'LOSS')"

_METRICS_SELECT_SQL = """
    COUNT(*) AS n,
    SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS actualWinRate,
    AVG(modelFairProbability) AS expectedWinRate,
    SUM(stake) AS totalStake,
    SUM(netProfitLoss) AS totalNetProfitLoss,
    CASE WHEN SUM(stake) > 0 THEN SUM(netProfitLoss) / SUM(stake) ELSE NULL END AS roi,
    AVG(clv) AS avgClv
"""


def calibration_status(n: int) -> str:
    """Python-side mirror of calibration_status_sql -- kept in sync deliberately, see its docstring."""
    if n < MIN_N_INSUFFICIENT:
        return "INSUFFICIENT_SAMPLE"
    if n < MIN_N_CALIBRATED:
        return "DESCRIPTIVE_ONLY"
    return "CALIBRATED"


def calibration_status_sql(n_column: str) -> str:
    """
    Shared, non-configurable sample-size gate for every calibration bucket
    in this module -- the three-tier scheme this milestone specifies
    (n<20 / 20<=n<100 / n>=100), distinct from
    lib.edgelab.analytics.sample_size_status_sql's simpler two-tier gate
    used by the Milestone 1 descriptive reports. A bucket's underlying
    value (win rate, ROI, CLV) is always computed and returned regardless
    of status -- this is a required reading instruction, never a filter
    that withholds a number.
    """
    return (
        f"CASE WHEN {n_column} < {MIN_N_INSUFFICIENT} THEN 'INSUFFICIENT_SAMPLE' "
        f"WHEN {n_column} < {MIN_N_CALIBRATED} THEN 'DESCRIPTIVE_ONLY' "
        f"ELSE 'CALIBRATED' END"
    )


def _row_from_record(bucket_fields: dict, record) -> dict:
    """
    Builds one calibration-bucket output row from one SQL result record
    whose trailing columns are exactly _METRICS_SELECT_SQL's 6 columns,
    in order: n, actualWinRate, expectedWinRate, totalStake,
    totalNetProfitLoss, roi, avgClv. `bucket_fields` supplies whatever
    bucket-identifying columns preceded them (already turned into a
    dict by the caller). Computes calibrationError and status here, in
    exactly one place, so every dimension reports it identically.
    """
    n, actual_win_rate, expected_win_rate, total_stake, total_net_pl, roi, avg_clv = record
    calibration_error = None
    if actual_win_rate is not None and expected_win_rate is not None:
        calibration_error = round(actual_win_rate - expected_win_rate, 4)
    row = dict(bucket_fields)
    row.update({
        "n": n,
        "winRate": actual_win_rate,
        "actualWinRate": actual_win_rate,
        "expectedWinRate": expected_win_rate,
        "calibrationError": calibration_error,
        "roi": roi,
        "totalStake": total_stake,
        "totalNetProfitLoss": total_net_pl,
        "avgClv": avg_clv,
        "status": calibration_status(n),
    })
    return row


def _decided_bets_available(session) -> bool:
    return session.is_available("bets")


# ── Edge bucket ──────────────────────────────────────────────────────────

def edge_bucket_calibration(session, bucket_width=2):
    """
    Buckets decided bets by PlacedBet.estimatedEdgeAtEntry, in bucket_width
    -point-wide bins starting at 0 (docs/EDGELAB_PHASE2_DESIGN.md §5.2's
    "every 2 points" suggestion), e.g. "2-4", "4-6". A bet with a null
    edge gets its own explicit "UNKNOWN" bucket -- never silently dropped
    or folded into bucket 0.
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        WITH buckets AS (
            SELECT *,
                CASE WHEN estimatedEdgeAtEntry IS NULL THEN NULL
                     ELSE FLOOR(estimatedEdgeAtEntry / {bucket_width}) * {bucket_width}
                END AS bucketStart
            FROM v_placed_bets
            WHERE {_DECIDED_BETS_FILTER}
        )
        SELECT
            CASE WHEN bucketStart IS NULL THEN 'UNKNOWN'
                 ELSE CAST(bucketStart AS INTEGER) || '-' || CAST(bucketStart + {bucket_width} AS INTEGER)
            END AS edgeBucket,
            bucketStart,
            {_METRICS_SELECT_SQL}
        FROM buckets
        GROUP BY 1, 2
        ORDER BY bucketStart NULLS LAST
    """)
    return [_row_from_record({"edgeBucket": r[0]}, r[2:]) for r in rows]


# ── Confidence ───────────────────────────────────────────────────────────

def confidence_calibration(session):
    """
    Groups decided bets by PlacedBet.confidence exactly as recorded
    (already a clean, small vocabulary -- HIGH/MEDIUM/LOW/PAPER -- see
    docs/EDGELAB_PHASE2_DESIGN.md §5.2). A null confidence gets its own
    "UNKNOWN" bucket. This function only ever reads confidence; it never
    writes or influences how confidence is generated.
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        SELECT COALESCE(confidence, 'UNKNOWN') AS confidence, {_METRICS_SELECT_SQL}
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
        GROUP BY 1
        ORDER BY n DESC, confidence
    """)
    return [_row_from_record({"confidence": r[0]}, r[1:]) for r in rows]


# ── Market family ────────────────────────────────────────────────────────

# Ordinal encoding used only for averaging "confidence" into one number
# in market_family_report()'s "average confidence" column -- confidence
# is otherwise treated as a category, never as a number, everywhere else
# in this module. PAPER is a paper-trading marker, not a real HIGH/MEDIUM/
# LOW judgment call, so it (and a null confidence) is excluded from this
# specific average rather than assigned an arbitrary score.
_CONFIDENCE_ORDINAL_SQL = "CASE confidence WHEN 'LOW' THEN 1 WHEN 'MEDIUM' THEN 2 WHEN 'HIGH' THEN 3 ELSE NULL END"


def market_family_calibration(session):
    """Calibration-standard row (n/winRate/roi/CLV/calibrationError/status) grouped by canonicalMarketFamily."""
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        SELECT canonicalMarketFamily, {_METRICS_SELECT_SQL}
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
        GROUP BY 1
        ORDER BY n DESC, canonicalMarketFamily
    """)
    return [_row_from_record({"canonicalMarketFamily": r[0]}, r[1:]) for r in rows]


def market_family_report(session):
    """
    The Milestone 2 "market-family report": everything
    market_family_calibration() has, plus average estimated edge and an
    ordinal-encoded average confidence (see _CONFIDENCE_ORDINAL_SQL) --
    one row per canonical family, "Bets" is the same count as `n`.
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        SELECT canonicalMarketFamily,
            AVG(estimatedEdgeAtEntry) AS avgEdge,
            AVG({_CONFIDENCE_ORDINAL_SQL}) AS avgConfidenceScore,
            {_METRICS_SELECT_SQL}
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
        GROUP BY 1
        ORDER BY n DESC, canonicalMarketFamily
    """)
    results = []
    for r in rows:
        row = _row_from_record({"canonicalMarketFamily": r[0]}, r[3:])
        row["bets"] = row["n"]
        row["avgEdge"] = r[1]
        row["avgConfidenceScore"] = r[2]
        results.append(row)
    return results


# ── Thesis tags ──────────────────────────────────────────────────────────

def thesis_tag_calibration(session):
    """
    Per-tag calibration row for every tag in PlacedBet.thesisTags
    (array -- a bet can contribute to more than one tag's bucket, this
    is deliberate, see docs/EDGELAB_CALIBRATION.md). As of Milestone 2,
    real data has 0% thesisTags coverage (docs/EDGELAB_PHASE2_DESIGN.md
    §1.2/§9 risk #3), so this returns an empty list against the real
    ledger today -- that's the honest, correct answer, not a bug; the
    dimension is fully implemented and will populate once tagging
    starts.
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        WITH decided AS (
            SELECT * FROM v_placed_bets WHERE {_DECIDED_BETS_FILTER}
        ),
        tagged AS (
            SELECT UNNEST(thesisTags) AS thesisTag, result, netProfitLoss, stake, clv, modelFairProbability
            FROM decided
            WHERE thesisTags IS NOT NULL AND len(thesisTags) > 0
        )
        SELECT thesisTag, {_METRICS_SELECT_SQL}
        FROM tagged
        GROUP BY 1
        ORDER BY n DESC, thesisTag
    """)
    return [_row_from_record({"thesisTag": r[0]}, r[1:]) for r in rows]


def thesis_tag_cooccurrence(session):
    """
    How often each pair of thesis tags appears together on the same bet
    -- a pure tagging-pattern statistic, deliberately computed over EVERY
    placed bet regardless of settlement status (co-occurrence is about
    how bets are tagged, not how they turned out), unlike every other
    function in this module. Returns one row per unordered tag pair that
    has co-occurred at least once, most frequent first.
    """
    if not session.is_available("bets"):
        return []
    rows = session.fetchall("""
        SELECT t1.tag AS tagA, t2.tag AS tagB, COUNT(DISTINCT betId) AS coOccurrenceCount
        FROM v_placed_bets, UNNEST(thesisTags) AS t1(tag), UNNEST(thesisTags) AS t2(tag)
        WHERE t1.tag < t2.tag
        GROUP BY 1, 2
        ORDER BY coOccurrenceCount DESC, tagA, tagB
    """)
    return [{"tagA": r[0], "tagB": r[1], "coOccurrenceCount": r[2]} for r in rows]


# ── CLV ──────────────────────────────────────────────────────────────────

def clv_bucket_calibration(session, bucket_width=5):
    """
    Fine-grained CLV bucket (docs/EDGELAB_PHASE2_DESIGN.md §5.2's "every
    5 cents" suggestion). PlacedBet.clv is already in cents/percentage
    points (entry implied prob minus closing implied prob); a null CLV
    (CLV never collected/available for that bet) gets its own "UNKNOWN"
    bucket.
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        WITH buckets AS (
            SELECT *,
                CASE WHEN clv IS NULL THEN NULL
                     ELSE FLOOR(clv / {bucket_width}) * {bucket_width}
                END AS bucketStart
            FROM v_placed_bets
            WHERE {_DECIDED_BETS_FILTER}
        )
        SELECT
            CASE WHEN bucketStart IS NULL THEN 'UNKNOWN'
                 ELSE CAST(bucketStart AS INTEGER) || '-' || CAST(bucketStart + {bucket_width} AS INTEGER)
            END AS clvBucket,
            bucketStart,
            {_METRICS_SELECT_SQL}
        FROM buckets
        GROUP BY 1, 2
        ORDER BY bucketStart NULLS LAST
    """)
    return [_row_from_record({"clvBucket": r[0]}, r[2:]) for r in rows]


def clv_sign_study(session):
    """
    Coarse 3-way CLV comparison this milestone explicitly asks for:
    POSITIVE (clv > NEUTRAL_CLV_BAND), NEGATIVE (clv < -NEUTRAL_CLV_BAND),
    NEUTRAL (|clv| <= NEUTRAL_CLV_BAND) -- win rate/ROI/calibration
    compared across the three. A null CLV gets its own "UNKNOWN" group
    rather than being folded into NEUTRAL (an unmeasured CLV is not the
    same claim as a measured-and-near-zero one).
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        SELECT
            CASE
                WHEN clv IS NULL THEN 'UNKNOWN'
                WHEN clv > {NEUTRAL_CLV_BAND} THEN 'POSITIVE'
                WHEN clv < -{NEUTRAL_CLV_BAND} THEN 'NEGATIVE'
                ELSE 'NEUTRAL'
            END AS clvSign,
            {_METRICS_SELECT_SQL}
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
        GROUP BY 1
        ORDER BY CASE clvSign WHEN 'POSITIVE' THEN 0 WHEN 'NEUTRAL' THEN 1 WHEN 'NEGATIVE' THEN 2 ELSE 3 END
    """)
    return [_row_from_record({"clvSign": r[0]}, r[1:]) for r in rows]


# ── Timing bucket ────────────────────────────────────────────────────────

# Fixed, deterministic display order -- matches classify_checkpoint's own
# distance-descending target order, plus the two non-distance labels it
# can never actually return here (FIRST_DAILY/LINEUP_CONFIRMATION require
# flags this module never sets, since those describe OBSERVATION capture
# cadence, not a bet's own entry timing) kept out of the ordering
# entirely rather than reserving dead slots for them.
_TIMING_BUCKET_ORDER = ("T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "T_MINUS_15", "T_MINUS_5", "INTERMEDIATE", "POST_START", "UNKNOWN")


def _to_naive_utc_datetime(value):
    """
    Returns a timezone-naive datetime representing the same UTC instant,
    whether DuckDB handed back a plain string (entryTimestamp is always
    present, so read_json_auto infers VARCHAR) or a Python datetime
    object (scheduledStart is optional; when every sampled value happens
    to parse cleanly DuckDB infers a naive TIMESTAMP instead) --
    entryTimestamp and scheduledStart can be inferred as different
    underlying types across one glob. classify_checkpoint()'s ISO-8601
    parser would otherwise receive one timezone-aware value and one
    naive value for the same query and crash comparing them.

    Critically, entryTimestamp is NOT always UTC on the wire -- real
    committed rows carry genuine non-UTC offsets (e.g. "-04:00" from an
    Eastern-time write path) -- so this converts to UTC before dropping
    tzinfo, rather than merely stripping the offset marker, which would
    silently leave the wall-clock time unconverted. scheduledStart is
    always written as "...Z" (UTC) by every producer, so a naive
    TIMESTAMP DuckDB hands back for it is already the correct UTC
    wall-clock value with nothing to convert.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def timing_bucket_calibration(session):
    """
    Buckets decided bets by how long before scheduled first pitch the bet
    was actually entered, reusing lib.edgelab.checkpoints.classify_checkpoint
    (the exact same distance-to-target classifier ClvQuote checkpoints
    already use -- see lib/edgelab/clv.py) against
    PlacedBet.entryTimestamp/scheduledStart, rather than inventing a
    second bucketing scheme. A bet with no recorded scheduledStart
    classifies as INTERMEDIATE (classify_checkpoint's own documented
    behavior when distance can't be computed) -- reported under its own
    label, not merged into another bucket.

    This one dimension is computed in Python, not SQL: classify_checkpoint
    carries real tolerance/nearest-target logic that must stay identical
    to the one ClvQuote already uses, and duplicating it as a second SQL
    CASE expression would be exactly the "second, wrong migration" risk
    docs/EDGELAB_PHASE2_DESIGN.md §9 warns about. This still never loads
    more than the small, already-decided-bets subset into Python -- not
    the raw JSONL history.
    """
    if not _decided_bets_available(session):
        return []
    raw_rows = session.fetchall(f"""
        SELECT entryTimestamp, scheduledStart, result, netProfitLoss, stake, clv, modelFairProbability
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
    """)

    buckets = collections.defaultdict(list)
    for entry_ts, scheduled_start, result, net_pl, stake, clv, model_prob in raw_rows:
        entry_dt = _to_naive_utc_datetime(entry_ts)
        scheduled_dt = _to_naive_utc_datetime(scheduled_start)
        bucket = classify_checkpoint(entry_dt, scheduled_dt) if entry_dt else "UNKNOWN"
        buckets[bucket].append((result, net_pl, stake, clv, model_prob))

    results = []
    for bucket, bets in buckets.items():
        n = len(bets)
        wins = sum(1 for b in bets if b[0] == "WIN")
        actual_win_rate = wins / n if n else None
        model_probs = [b[4] for b in bets if b[4] is not None]
        expected_win_rate = sum(model_probs) / len(model_probs) if model_probs else None
        total_stake = sum(b[2] for b in bets if b[2] is not None)
        total_net_pl = sum(b[1] for b in bets if b[1] is not None)
        roi = total_net_pl / total_stake if total_stake else None
        clv_values = [b[3] for b in bets if b[3] is not None]
        avg_clv = sum(clv_values) / len(clv_values) if clv_values else None
        record = (n, actual_win_rate, expected_win_rate, total_stake, total_net_pl, roi, avg_clv)
        results.append(_row_from_record({"timingBucket": bucket}, record))

    def sort_key(row):
        bucket = row["timingBucket"]
        return _TIMING_BUCKET_ORDER.index(bucket) if bucket in _TIMING_BUCKET_ORDER else len(_TIMING_BUCKET_ORDER)

    return sorted(results, key=sort_key)


# ── Recommendation-path analysis ────────────────────────────────────────

def recommendation_path_calibration(session):
    """
    Compares five categories this milestone explicitly asks for:
    RECOMMENDED_AND_BET, MANUAL_BET, MODEL_BET (all backed by a real
    placed bet -- full win/ROI/CLV/calibration metrics, same shape as
    every other bucket in this module) and RECOMMENDED_NOT_BET, PASSED
    (Recommendation rows where no bet was ever placed).

    The last two categories deliberately do NOT report winRate/roi/avgClv:
    there is no PlacedBet (no stake, no side actually risked, no realized
    return) to measure a real outcome from. Fabricating a hypothetical
    "would have won" by guessing which side the model favored and
    assuming a stake would be inventing data that was never actually
    risked -- exactly the kind of strategy-shaped judgment this milestone
    is explicitly not allowed to make. Instead they report `n` and the
    average of what IS on record for a recommendation that was never
    acted on (modelFairProbability, marketImpliedProbability,
    estimatedEdge), so the two categories are still comparable to each
    other and to the bet-backed categories on model confidence/edge,
    just not on win/loss.
    """
    results = []

    if _decided_bets_available(session):
        rows = session.fetchall(f"""
            SELECT
                CASE
                    WHEN recommendationId IS NOT NULL THEN 'RECOMMENDED_AND_BET'
                    WHEN source = 'MANUAL' THEN 'MANUAL_BET'
                    WHEN source = 'MODEL' THEN 'MODEL_BET'
                    ELSE 'OTHER_BET'
                END AS recommendationPath,
                {_METRICS_SELECT_SQL}
            FROM v_placed_bets
            WHERE {_DECIDED_BETS_FILTER}
            GROUP BY 1
            ORDER BY n DESC, recommendationPath
        """)
        results.extend(_row_from_record({"recommendationPath": r[0]}, r[1:]) for r in rows)

    if session.is_available("recommendations"):
        rows = session.fetchall("""
            SELECT
                CASE
                    WHEN status = 'RECOMMENDED_NOT_BET' THEN 'RECOMMENDED_NOT_BET'
                    WHEN status LIKE 'PASS_%' THEN 'PASSED'
                    ELSE NULL
                END AS recommendationPath,
                COUNT(*) AS n,
                AVG(modelFairProbability) AS avgModelFairProbability,
                AVG(marketImpliedProbability) AS avgMarketImpliedProbability,
                AVG(estimatedEdge) AS avgEstimatedEdge
            FROM v_recommendations
            WHERE status = 'RECOMMENDED_NOT_BET' OR status LIKE 'PASS_%'
            GROUP BY 1
            ORDER BY recommendationPath
        """)
        for path, n, avg_model_prob, avg_market_prob, avg_edge in rows:
            results.append({
                "recommendationPath": path,
                "n": n,
                "winRate": None,
                "actualWinRate": None,
                "expectedWinRate": None,
                "calibrationError": None,
                "roi": None,
                "totalStake": None,
                "totalNetProfitLoss": None,
                "avgClv": None,
                "avgModelFairProbability": avg_model_prob,
                "avgMarketImpliedProbability": avg_market_prob,
                "avgEstimatedEdge": avg_edge,
                "status": calibration_status(n),
            })

    return results


# ── Historical trend reports ─────────────────────────────────────────────

# entryTimestamp is not reliably UTC on the wire -- real committed rows
# carry genuine non-UTC offsets (e.g. "-04:00" from an Eastern-time write
# path), and a plain CAST(entryTimestamp AS TIMESTAMP) silently keeps the
# offset string's wall-clock digits without shifting to UTC (DuckDB just
# drops the offset). Casting through TIMESTAMPTZ first forces a correct
# UTC conversion; casting that back down to a plain TIMESTAMP before
# DATE_TRUNC avoids DuckDB's ICU/pytz dependency that DATE_TRUNC on a
# TIMESTAMPTZ would otherwise pull in.
_ENTRY_TS_UTC_SQL = "CAST(CAST(entryTimestamp AS TIMESTAMPTZ) AS TIMESTAMP)"


def _trend_report(session, period_label, date_trunc_unit, strftime_format):
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        SELECT STRFTIME(DATE_TRUNC('{date_trunc_unit}', {_ENTRY_TS_UTC_SQL}), '{strftime_format}') AS period,
               {_METRICS_SELECT_SQL}
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
        GROUP BY 1
        ORDER BY 1
    """)
    return [_row_from_record({"period": r[0], "periodType": period_label}, r[1:]) for r in rows]


def daily_trend_report(session):
    """One row per calendar date (UTC), keyed 'YYYY-MM-DD'."""
    return _trend_report(session, "daily", "day", "%Y-%m-%d")


def weekly_trend_report(session):
    """One row per week (Monday-start, DuckDB's DATE_TRUNC('week', ...) default), keyed by the week's start date."""
    return _trend_report(session, "weekly", "week", "%Y-%m-%d")


def monthly_trend_report(session):
    """One row per calendar month (UTC), keyed 'YYYY-MM'."""
    return _trend_report(session, "monthly", "month", "%Y-%m")


def season_to_date_report(session):
    """
    A single row spanning every decided bet ever recorded -- "season to
    date" per docs/EDGELAB_PHASE2_DESIGN.md §7.3: the widest possible
    window, same methodology, no separate code path.
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        SELECT {_METRICS_SELECT_SQL}
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
    """)
    if not rows or rows[0][0] == 0:
        return []
    return [_row_from_record({"period": "SEASON_TO_DATE", "periodType": "season"}, rows[0])]


# ── Milestone 4 dimensions: model version/source, data quality, correlation group ──
#
# Same shared methodology (n/winRate/roi/CLV/calibrationError/status) as
# every other dimension above, reading v_placed_bets's new
# modelSource/dataQuality/correlationGroups columns (Milestone 4,
# docs/EDGELAB_EVALUATION_METADATA.md) -- sourced only from a linked
# ModelEvaluation (there is no PlacedBet-side equivalent to fall back
# to), so a bet with no link contributes to the 'UNKNOWN'/no-group
# bucket rather than being silently dropped.

def model_version_source_calibration(session):
    """
    Groups decided bets by (modelVersion, modelSource) via the linked
    ModelEvaluation. modelVersion is null for every real record today
    (no upstream source exists -- see docs/EDGELAB_EVALUATION_METADATA.md),
    so every real bucket currently reads 'UNKNOWN' / a real modelSource
    string; the dimension is fully implemented and will differentiate
    automatically once/if a real model-version source ever exists.
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        SELECT COALESCE(modelVersion, 'UNKNOWN') AS modelVersion, COALESCE(modelSource, 'UNKNOWN') AS modelSource,
               {_METRICS_SELECT_SQL}
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
        GROUP BY 1, 2
        ORDER BY n DESC, modelVersion, modelSource
    """)
    return [_row_from_record({"modelVersion": r[0], "modelSource": r[1]}, r[2:]) for r in rows]


def data_quality_calibration(session):
    """Groups decided bets by the linked ModelEvaluation's dataQuality (e.g. full/partial/insufficient/none); 'UNKNOWN' when no link resolves."""
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        SELECT COALESCE(dataQuality, 'UNKNOWN') AS dataQuality, {_METRICS_SELECT_SQL}
        FROM v_placed_bets
        WHERE {_DECIDED_BETS_FILTER}
        GROUP BY 1
        ORDER BY n DESC, dataQuality
    """)
    return [_row_from_record({"dataQuality": r[0]}, r[1:]) for r in rows]


def correlation_group_calibration(session):
    """
    Per-correlation-group calibration row (UNNEST'd from the linked
    ModelEvaluation's correlationGroups array -- a bet can belong to more
    than one group at once, e.g. an F5 ML bet is both an F5_SIDE_ and a
    STARTER_SUCCESS_ group, and deliberately contributes to both buckets).
    A bet with no correlationGroups (no link, or a market type
    correlation_groups_for_row doesn't cover) contributes to no bucket
    here -- purely descriptive, never used to filter or resize anything.
    """
    if not _decided_bets_available(session):
        return []
    rows = session.fetchall(f"""
        WITH decided AS (
            SELECT * FROM v_placed_bets WHERE {_DECIDED_BETS_FILTER}
        ),
        grouped AS (
            SELECT UNNEST(correlationGroups) AS correlationGroup, result, netProfitLoss, stake, clv, modelFairProbability
            FROM decided
            WHERE correlationGroups IS NOT NULL AND len(correlationGroups) > 0
        )
        SELECT correlationGroup, {_METRICS_SELECT_SQL}
        FROM grouped
        GROUP BY 1
        ORDER BY n DESC, correlationGroup
    """)
    return [_row_from_record({"correlationGroup": r[0]}, r[1:]) for r in rows]
