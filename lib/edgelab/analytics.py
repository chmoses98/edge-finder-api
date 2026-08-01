"""
lib/edgelab/analytics.py
===========================
DuckDB-based cross-date analytics query layer (Phase 2 Milestone 1 --
docs/EDGELAB_PHASE2_DESIGN.md §3-4). This module is READ-ONLY: nothing
here writes to data/edgelab/<entity>/ or touches production betting/
settlement logic in any way.

Design, matched point-for-point to the Milestone 1 requirements:

  - No server, no daemon, no paid service, no persisted .duckdb file:
    open_session() returns a fresh, disposable, in-memory DuckDB
    connection every time. Closing it (or letting it fall out of scope)
    leaves nothing behind.
  - Reads the existing git-committed JSONL/JSONL.gz partitions directly
    via SQL glob patterns (read_json_auto(..., union_by_name=true)) --
    no ETL step, no second copy of the data, no committed Parquet.
  - Cross-date by construction: every entity view globs across every
    date's file at once, so one query spans the whole history, not one
    date at a time.
  - union_by_name=true is what makes backward compatibility work: a
    glob mixing files written before and after a schema change (e.g.
    before/after the sport/platform fields existed) reads cleanly,
    with the missing columns coming back NULL for older files -- the
    canonical views below then apply COALESCE(sport, 'MLB') etc., so a
    query never needs to know which files predate which field.
  - Fails clearly, two distinct ways, deliberately not conflated:
      1. An entity directory with NO files at all (nothing has been
         produced for it yet -- a normal, common state early in the
         project's life) is reported as unavailable via
         AnalyticsSession.availability[entity]["available"] is False,
         never a crash -- see register_all_raw_views().
      2. An entity directory that DOES have files, but one of them is
         malformed JSON or corrupt gzip, raises AnalyticsDataError with
         the underlying DuckDB error message (which itself names the
         exact file and byte offset) the first time that view is
         actually queried -- never silently skipped or swallowed.
  - Never loads a full season into Python: every function here returns
    only the small, already-aggregated result of a SQL query
    (`.fetchall()`, at most a few hundred rows for the widest report),
    never a Python list built from iterating every raw row across every
    date -- DuckDB's own execution engine does the file scanning.
"""

import glob
import os

import duckdb

from lib.edgelab.market_family_mapping import MARKET_FAMILY_ALIASES, UNKNOWN, UNMAPPED
from lib.edgelab.storage import EDGELAB_ROOT

MIN_SAMPLE_SIZE = 20  # docs/EDGELAB_PHASE2_DESIGN.md §5.1 -- non-negotiable, not configurable per query.

_DATE_PARTITIONED_ENTITIES = (
    "observations", "games", "markets", "recommendations",
    "clv_quotes", "settlements", "research_runs",
)


class AnalyticsDataError(Exception):
    """
    Raised when a registered entity's underlying files exist but can't
    actually be read (malformed JSON, corrupt gzip, a row that violates
    the glob's inferred schema in an unrecoverable way). Always wraps
    the original DuckDB error message -- never replaces it with a
    generic "something went wrong".
    """


def _entity_glob_patterns(root):
    return {
        "observations": os.path.join(root, "observations", "*.jsonl*"),
        "games": os.path.join(root, "games", "*.jsonl"),
        "markets": os.path.join(root, "markets", "*.jsonl"),
        "recommendations": os.path.join(root, "recommendations", "*.jsonl"),
        "clv_quotes": os.path.join(root, "clv_quotes", "*.jsonl"),
        "settlements": os.path.join(root, "settlements", "*.jsonl"),
        "bets": os.path.join(root, "bets", "bets.jsonl"),
        "research_runs": os.path.join(root, "research_runs", "*.jsonl"),
    }


def _sql_quote(value: str) -> str:
    return value.replace("'", "''")


class AnalyticsSession:
    """
    Thin wrapper around one disposable DuckDB connection plus the
    per-entity availability info computed when it was opened. Use as a
    context manager or call .close() explicitly; either way, nothing
    persists after close() beyond the source JSONL files themselves.
    """

    def __init__(self, con, availability):
        self.con = con
        self.availability = availability

    def is_available(self, entity: str) -> bool:
        return bool(self.availability.get(entity, {}).get("available"))

    def sql(self, query: str):
        """
        Runs `query` and returns the DuckDB relation. Any underlying
        file-read failure (malformed JSON, corrupt gzip) is re-raised as
        AnalyticsDataError with the original message intact -- callers
        must not catch and discard this; a caller that wants
        "unavailable" vs "malformed" to look the same to its own caller
        has to decide that explicitly, it's never decided here.
        """
        try:
            return self.con.sql(query)
        except duckdb.Error as exc:
            raise AnalyticsDataError(str(exc)) from exc

    def fetchall(self, query: str):
        return self.sql(query).fetchall()

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def register_all_raw_views(con, root):
    """
    For every known entity, checks (via a plain Python glob -- cheap,
    no DuckDB involved yet) whether any matching file exists. If none
    do, the entity is recorded unavailable and NO view is created for
    it (so a caller can check availability before querying instead of
    getting a confusing "no files found" exception mid-report). If at
    least one file matches, creates `raw_<entity>` as a VIEW over the
    glob. DuckDB's read_json_auto samples each matched file to infer
    its columns as part of binding the view, so a malformed JSON file
    or corrupt gzip stream surfaces here, at registration time (i.e.
    inside open_session()), not lazily deferred to the first query --
    re-raised as AnalyticsDataError with the original DuckDB message,
    which itself names the exact file and byte offset.

    Returns {entity: {"available": bool, "fileCount": int, "pattern": str}}.
    """
    availability = {}
    for entity, pattern in _entity_glob_patterns(root).items():
        files = glob.glob(pattern)
        availability[entity] = {"available": bool(files), "fileCount": len(files), "pattern": pattern}
        if not files:
            continue
        quoted = _sql_quote(pattern)
        # filename=true + rename to __edgelab_filename (unlikely to ever
        # collide with a real EdgeLab field name) -- this is how
        # row_counts_by_entity_and_date() recovers each row's partition
        # date without depending on every entity carrying its own
        # reliable per-record date field.
        try:
            con.execute(
                f"CREATE OR REPLACE VIEW raw_{entity} AS "
                f"SELECT * EXCLUDE (filename), filename AS __edgelab_filename "
                f"FROM read_json_auto('{quoted}', union_by_name=true, filename=true)"
            )
        except duckdb.Error as exc:
            raise AnalyticsDataError(str(exc)) from exc
    return availability


def register_family_mapping_table(con):
    """
    The ONE table every canonicalizing view joins against -- built
    directly from lib.edgelab.market_family_mapping.MARKET_FAMILY_ALIASES,
    never duplicated as SQL CASE WHEN logic. Adding a new raw spelling
    means editing that one Python dict; this function picks the change
    up automatically the next time a session opens.
    """
    rows = ", ".join(
        f"('{_sql_quote(raw)}', '{_sql_quote(canonical)}')"
        for raw, canonical in MARKET_FAMILY_ALIASES.items()
    )
    con.execute(
        f"CREATE OR REPLACE TABLE family_mapping AS "
        f"SELECT * FROM (VALUES {rows}) AS t(rawValue, canonicalFamily)"
    )


def _view_columns(con, view_name: str) -> set:
    return {row[0] for row in con.execute(f"DESCRIBE {view_name}").fetchall()}


def _col_expr(con, view_name: str, alias: str, column: str, columns_cache: dict) -> str:
    """
    Returns a raw (unaliased) SQL expression for referencing `column` on
    `alias` -- `alias.column` when `view_name` actually has that column,
    or the literal `NULL` when every single row read from `view_name`
    lacks it entirely (a field is optional in the schema and every
    currently committed/fixture file happens to omit it), since
    DuckDB's read_json_auto never infers a column at all in that case
    and a plain `alias.column` reference would fail to bind, not just
    return NULL. Checked once per view via DESCRIBE and cached in
    `columns_cache` (keyed by view_name). Callers that need this
    expression more than once for the same column (e.g. marketFamily,
    referenced in a SELECT item, a CASE expression, and a JOIN
    condition) must reuse the same returned string rather than calling
    this twice, so all three see the same column-presence decision.
    """
    if view_name not in columns_cache:
        columns_cache[view_name] = _view_columns(con, view_name)
    return f"{alias}.{column}" if column in columns_cache[view_name] else "NULL"


def _select_or_null(con, view_name: str, alias: str, column: str, columns_cache: dict) -> str:
    """`column AS column`, or `NULL AS column` when the raw view lacks it entirely -- see _col_expr."""
    return f"{_col_expr(con, view_name, alias, column, columns_cache)} AS {column}"


def _coalesce_or_default(con, view_name: str, alias: str, column: str, default_sql_literal: str, columns_cache: dict) -> str:
    """
    Same as _select_or_null, but substituting `default_sql_literal` for
    a NULL value too (used for sport/platform, which must always read
    back as a real value, never NULL, per docs/EDGELAB_PHASE2_DESIGN.md
    §2.1).
    """
    expr = _col_expr(con, view_name, alias, column, columns_cache)
    if expr == "NULL":
        return f"{default_sql_literal} AS {column}"
    return f"COALESCE({expr}, {default_sql_literal}) AS {column}"


def _canonical_family_case_sql(raw_column: str) -> str:
    """
    Shared CASE expression fragment: UNKNOWN for null/empty/placeholder
    raw values, UNMAPPED for a real but unrecognized spelling, else the
    joined family_mapping.canonicalFamily. Used by every canonical view
    below so the UNKNOWN/UNMAPPED rule is defined exactly once.
    """
    return (
        f"CASE "
        f"WHEN {raw_column} IS NULL OR TRIM({raw_column}) = '' OR LOWER(TRIM({raw_column})) IN ('n/a','na','none','null','unknown') THEN '{UNKNOWN}' "
        f"WHEN fm.canonicalFamily IS NOT NULL THEN fm.canonicalFamily "
        f"ELSE '{UNMAPPED}' "
        f"END"
    )


def register_canonical_views(con, availability):
    """
    Builds the canonical fact views (docs/EDGELAB_PHASE2_DESIGN.md §3.2)
    over whichever raw_<entity> views actually exist. A view is only
    created when its underlying raw view is available -- querying an
    unavailable canonical view raises DuckDB's own clear "table does not
    exist" error rather than this module inventing a fake empty result.
    """
    columns_cache = {}

    if availability.get("bets", {}).get("available"):
        view, alias = "raw_bets", "b"
        col = lambda c: _select_or_null(con, view, alias, c, columns_cache)  # noqa: E731
        family_expr = _col_expr(con, view, alias, "marketFamily", columns_cache)
        con.execute(f"""
            CREATE OR REPLACE VIEW v_placed_bets AS
            SELECT
                {col('betId')}, {col('gameId')},
                {_coalesce_or_default(con, view, alias, 'sport', "'MLB'", columns_cache)},
                {_coalesce_or_default(con, view, alias, 'platform', "'KALSHI'", columns_cache)},
                {col('marketTicker')},
                {family_expr} AS rawMarketFamily,
                {_canonical_family_case_sql(family_expr)} AS canonicalMarketFamily,
                {col('selection')}, {col('side')}, {col('stake')}, {col('entryPrice')}, {col('entryTimestamp')},
                {col('source')}, {col('recommendationId')}, {col('confidence')}, {col('trackingType')},
                {col('thesisTags')}, {col('correlationGroup')}, {col('status')}, {col('closingPrice')},
                {col('clv')}, {col('result')}, {col('returnAmount')}, {col('netProfitLoss')}
            FROM raw_bets b
            LEFT JOIN family_mapping fm ON fm.rawValue = {family_expr}
        """)

    if availability.get("observations", {}).get("available"):
        view, alias = "raw_observations", "o"
        col = lambda c: _select_or_null(con, view, alias, c, columns_cache)  # noqa: E731
        family_expr = _col_expr(con, view, alias, "marketFamily", columns_cache)
        con.execute(f"""
            CREATE OR REPLACE VIEW v_market_observations AS
            SELECT
                {col('marketObservationId')}, {col('runId')}, {col('capturedAt')}, {col('gameId')},
                {_coalesce_or_default(con, view, alias, 'sport', "'MLB'", columns_cache)},
                {_coalesce_or_default(con, view, alias, 'platform', "'KALSHI'", columns_cache)},
                {col('marketTicker')},
                {family_expr} AS rawMarketFamily,
                {_canonical_family_case_sql(family_expr)} AS canonicalMarketFamily,
                {col('marketHorizon')}, {col('player')}, {col('team')}, {col('threshold')},
                {col('yesBid')}, {col('yesAsk')}, {col('noBid')}, {col('noAsk')}, {col('lastPrice')},
                {col('marketStatus')}, {col('lineupConfirmationState')}, {col('checkpoint')}
            FROM raw_observations o
            LEFT JOIN family_mapping fm ON fm.rawValue = {family_expr}
        """)

    if availability.get("recommendations", {}).get("available"):
        view, alias = "raw_recommendations", "r"
        col = lambda c: _select_or_null(con, view, alias, c, columns_cache)  # noqa: E731
        family_expr = _col_expr(con, view, alias, "marketFamily", columns_cache)
        con.execute(f"""
            CREATE OR REPLACE VIEW v_recommendations AS
            SELECT
                {col('recommendationId')}, {col('runId')}, {col('gameId')},
                {_coalesce_or_default(con, view, alias, 'sport', "'MLB'", columns_cache)},
                {_coalesce_or_default(con, view, alias, 'platform', "'KALSHI'", columns_cache)},
                {col('marketTicker')},
                {family_expr} AS rawMarketFamily,
                {_canonical_family_case_sql(family_expr)} AS canonicalMarketFamily,
                {col('status')}, {col('modelFairProbability')}, {col('marketImpliedProbability')},
                {col('estimatedEdge')}, {col('confidence')}, {col('passReason')}, {col('betPlaced')}, {col('betId')},
                {col('createdAt')}
            FROM raw_recommendations r
            LEFT JOIN family_mapping fm ON fm.rawValue = {family_expr}
        """)

    if availability.get("settlements", {}).get("available"):
        view, alias = "raw_settlements", "s"
        col = lambda c: _select_or_null(con, view, alias, c, columns_cache)  # noqa: E731
        family_expr = _col_expr(con, view, alias, "marketFamily", columns_cache)
        con.execute(f"""
            CREATE OR REPLACE VIEW v_settlements AS
            SELECT
                {col('settlementId')}, {col('gameId')},
                {_coalesce_or_default(con, view, alias, 'sport', "'MLB'", columns_cache)},
                {_coalesce_or_default(con, view, alias, 'platform', "'KALSHI'", columns_cache)},
                {col('marketTicker')},
                {family_expr} AS rawMarketFamily,
                {_canonical_family_case_sql(family_expr)} AS canonicalMarketFamily,
                {col('settlementStatus')}, {col('unavailableReason')}, {col('result')}, {col('betId')},
                {col('realizedReturn')}, {col('settledAt')}
            FROM raw_settlements s
            LEFT JOIN family_mapping fm ON fm.rawValue = {family_expr}
        """)

    if availability.get("clv_quotes", {}).get("available"):
        view, alias = "raw_clv_quotes", "q"
        col = lambda c: _select_or_null(con, view, alias, c, columns_cache)  # noqa: E731
        con.execute(f"""
            CREATE OR REPLACE VIEW v_clv_quotes AS
            SELECT
                {col('clvQuoteId')}, {col('runId')}, {col('betId')}, {col('marketTicker')}, {col('gameId')},
                {col('capturedAt')}, {col('checkpoint')}, {col('yesBid')}, {col('yesAsk')}, {col('noBid')}, {col('noAsk')},
                {col('marketStatus')}, {col('isClosingQuote')}
            FROM raw_clv_quotes q
        """)


def open_session(root=None) -> AnalyticsSession:
    """
    Opens one fresh, in-memory, disposable DuckDB session with every
    raw_<entity> and canonical v_<entity> view registered against
    `root` (defaults to the real data/edgelab/). Nothing is persisted;
    callers should use this as a context manager or call .close().
    """
    root = root or EDGELAB_ROOT
    con = duckdb.connect(database=":memory:")
    availability = register_all_raw_views(con, root)
    register_family_mapping_table(con)
    register_canonical_views(con, availability)
    return AnalyticsSession(con, availability)


def sample_size_status_sql(n_column: str) -> str:
    """
    Shared deterministic sample-size gate (docs/EDGELAB_PHASE2_DESIGN.md
    §5.1) -- a CASE expression, not a Python post-filter, so every query
    that groups by anything applies the exact same threshold the exact
    same way. n < MIN_SAMPLE_SIZE (20) is INSUFFICIENT_SAMPLE, full stop
    -- no partial credit, no "close enough". At/above threshold is
    DESCRIPTIVE_ONLY: these are Milestone 1 numbers, not a calibrated
    statistical claim (see docs/EDGELAB_PHASE2_DESIGN.md §5 -- that's a
    later milestone) and must never be read as "actionable evidence".
    """
    return f"CASE WHEN {n_column} < {MIN_SAMPLE_SIZE} THEN 'INSUFFICIENT_SAMPLE' ELSE 'DESCRIPTIVE_ONLY' END"


# ── Named queries (scripts/edgelab/run_analytics.py's building blocks) ──────
#
# Every function below returns a small list of plain dicts (already
# aggregated by DuckDB -- never a per-row Python materialization of the
# underlying JSONL) and is independently unit-testable against a session
# opened over a fixture directory, not just through the CLI.

_DATE_FIELD_BY_ENTITY = {
    # Entities partitioned as data/edgelab/<entity>/<date>.jsonl* -- the
    # date lives in the filename, not reliably in every record, so
    # row-count-by-date uses regexp_extract(filename, ...) uniformly
    # across every date-partitioned entity rather than a different
    # per-record date field per entity.
    "observations": "raw_observations",
    "games": "raw_games",
    "markets": "raw_markets",
    "recommendations": "raw_recommendations",
    "clv_quotes": "raw_clv_quotes",
    "settlements": "raw_settlements",
    "research_runs": "raw_research_runs",
}


def row_counts_by_entity_and_date(session: AnalyticsSession):
    """
    One row per (entity, date, rowCount). Entities partitioned by date
    (data/edgelab/<entity>/<date>.jsonl*) use the date embedded in the
    filename (every raw_<entity> view carries a `__edgelab_filename`
    column for exactly this -- see register_all_raw_views), since not
    every entity's own records reliably carry a per-row date field.
    `bets` (a single running ledger, not date-partitioned) is grouped by
    entryTimestamp's own date instead. Returns a plain list of dicts,
    sorted by (entity, date) for deterministic output.
    """
    results = []
    for entity in _DATE_FIELD_BY_ENTITY:
        if not session.is_available(entity):
            continue
        view = _DATE_FIELD_BY_ENTITY[entity]
        rows = session.fetchall(f"""
            SELECT '{entity}' AS entity,
                   regexp_extract(__edgelab_filename, '([0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}})', 1) AS date,
                   COUNT(*) AS rowCount
            FROM {view}
            GROUP BY 2
            ORDER BY 2
        """)
        results.extend({"entity": r[0], "date": r[1], "rowCount": r[2]} for r in rows)

    if session.is_available("bets"):
        rows = session.fetchall("""
            SELECT 'bets' AS entity, SUBSTR(entryTimestamp, 1, 10) AS date, COUNT(*) AS rowCount
            FROM raw_bets
            GROUP BY 2
            ORDER BY 2
        """)
        results.extend({"entity": r[0], "date": r[1], "rowCount": r[2]} for r in rows)

    return sorted(results, key=lambda r: (r["entity"], r["date"] or ""))


def unmapped_market_family_values(session: AnalyticsSession):
    """
    Audit query: every distinct raw PlacedBet.marketFamily spelling that
    canonicalized to UNMAPPED (a real, non-empty value not yet in
    lib.edgelab.market_family_mapping.MARKET_FAMILY_ALIASES), with its
    count -- the concrete list a maintainer acts on when adding a new
    mapping. Never includes UNKNOWN (null/placeholder) values; those
    aren't unmapped spellings, they're an absence of a value.
    """
    if not session.is_available("bets"):
        return []
    rows = session.fetchall(f"""
        SELECT rawMarketFamily, COUNT(*) AS n
        FROM v_placed_bets
        WHERE canonicalMarketFamily = '{UNMAPPED}'
        GROUP BY 1
        ORDER BY n DESC, rawMarketFamily
    """)
    return [{"rawMarketFamily": r[0], "count": r[1]} for r in rows]


def bets_by_canonical_family(session: AnalyticsSession):
    """Placed-bet counts grouped by canonical family, with a sample-size status."""
    if not session.is_available("bets"):
        return []
    rows = session.fetchall(f"""
        SELECT canonicalMarketFamily, COUNT(*) AS n, {sample_size_status_sql('COUNT(*)')} AS sampleStatus
        FROM v_placed_bets
        GROUP BY 1
        ORDER BY n DESC, canonicalMarketFamily
    """)
    return [{"canonicalMarketFamily": r[0], "count": r[1], "sampleStatus": r[2]} for r in rows]


def roi_by_canonical_family(session: AnalyticsSession):
    """
    ROI = SUM(netProfitLoss) / SUM(stake) for SETTLED bets only, grouped
    by canonical family. Never computed for a bucket below
    MIN_SAMPLE_SIZE without an explicit INSUFFICIENT_SAMPLE flag --
    the roi VALUE is still returned (never withheld), but sampleStatus
    makes it unmistakable that a small-n ROI is descriptive noise, not
    evidence, per docs/EDGELAB_PHASE2_DESIGN.md §5.1's mandatory gate.
    """
    if not session.is_available("bets"):
        return []
    rows = session.fetchall(f"""
        SELECT
            canonicalMarketFamily,
            COUNT(*) AS n,
            SUM(stake) AS totalStake,
            SUM(netProfitLoss) AS totalNetProfitLoss,
            CASE WHEN SUM(stake) > 0 THEN SUM(netProfitLoss) / SUM(stake) ELSE NULL END AS roi,
            {sample_size_status_sql('COUNT(*)')} AS sampleStatus
        FROM v_placed_bets
        WHERE status = 'settled' AND netProfitLoss IS NOT NULL AND stake IS NOT NULL
        GROUP BY 1
        ORDER BY n DESC, canonicalMarketFamily
    """)
    return [
        {
            "canonicalMarketFamily": r[0], "n": r[1], "totalStake": r[2],
            "totalNetProfitLoss": r[3], "roi": r[4], "sampleStatus": r[5],
        }
        for r in rows
    ]


def clv_summary_by_canonical_family(session: AnalyticsSession):
    """Average CLV, positive/negative counts, grouped by canonical family, with a sample-size status."""
    if not session.is_available("bets"):
        return []
    rows = session.fetchall(f"""
        SELECT
            canonicalMarketFamily,
            COUNT(*) AS n,
            AVG(clv) AS avgClv,
            SUM(CASE WHEN clv > 0 THEN 1 ELSE 0 END) AS positiveCount,
            SUM(CASE WHEN clv < 0 THEN 1 ELSE 0 END) AS negativeCount,
            {sample_size_status_sql('COUNT(*)')} AS sampleStatus
        FROM v_placed_bets
        WHERE clv IS NOT NULL
        GROUP BY 1
        ORDER BY n DESC, canonicalMarketFamily
    """)
    return [
        {
            "canonicalMarketFamily": r[0], "n": r[1], "avgClv": r[2],
            "positiveCount": r[3], "negativeCount": r[4], "sampleStatus": r[5],
        }
        for r in rows
    ]


# Which entity + column each completeness metric is measured against.
# thesisTags/correlationGroup/recommendationId/sport/platform are
# measured on placed bets; lineupConfirmationState only exists on
# MarketObservation.
_COMPLETENESS_TARGETS = (
    ("bets", "thesisTags", "thesisTags IS NOT NULL AND len(thesisTags) > 0"),
    ("bets", "correlationGroup", "correlationGroup IS NOT NULL"),
    ("bets", "recommendationId", "recommendationId IS NOT NULL"),
    ("bets", "sport", "sport IS NOT NULL"),
    ("bets", "platform", "platform IS NOT NULL"),
    ("observations", "lineupConfirmationState", "lineupConfirmationState IS NOT NULL"),
    ("observations", "sport", "sport IS NOT NULL"),
    ("observations", "platform", "platform IS NOT NULL"),
)

_COMPLETENESS_VIEW_BY_ENTITY = {"bets": "v_placed_bets", "observations": "v_market_observations"}


def completeness_metrics(session: AnalyticsSession):
    """
    Population-completeness percentage for each field in
    _COMPLETENESS_TARGETS. Measured against the CANONICAL views (so
    sport/platform's COALESCE default doesn't make a never-populated
    field look 100% complete -- completeness must reflect what was
    actually WRITTEN, so this queries the raw_<entity> view's own
    column presence/nullness, not the canonicalized default).
    """
    results = []
    for entity, field, _ in _COMPLETENESS_TARGETS:
        if not session.is_available(entity):
            results.append({"entity": entity, "field": field, "populated": 0, "total": 0, "pct": None, "status": "UNAVAILABLE"})
            continue
        raw_view = f"raw_{entity}"
        columns = _view_columns(session.con, raw_view)
        if field not in columns:
            results.append({"entity": entity, "field": field, "populated": 0, "total": 0, "pct": 0.0, "status": "FIELD_NEVER_WRITTEN"})
            continue
        predicate = {
            "thesisTags": "thesisTags IS NOT NULL AND len(thesisTags) > 0",
            "correlationGroup": "correlationGroup IS NOT NULL",
            "recommendationId": "recommendationId IS NOT NULL",
            "sport": "sport IS NOT NULL",
            "platform": "platform IS NOT NULL",
            "lineupConfirmationState": "lineupConfirmationState IS NOT NULL",
        }[field]
        total, populated = session.fetchall(f"""
            SELECT COUNT(*), SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END)
            FROM {raw_view}
        """)[0]
        pct = round(100.0 * populated / total, 2) if total else None
        results.append({"entity": entity, "field": field, "populated": populated, "total": total, "pct": pct, "status": "OK"})
    return results
