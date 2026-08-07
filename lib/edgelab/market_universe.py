"""
lib/edgelab/market_universe.py
=================================
Full-market-universe capture (EdgeLab Phase 1 section C/D).

Reuses the repository's existing parsing/classification stack instead of
re-deriving it:
  - lib.kalshi_mlb_contract_parser.parse_contract: ticker -> canonical
    fields (gameId fallback, away/home, prices).
  - lib.research.market_taxonomy.classify_market: family/scope/team/line/
    operator from the ticker+title.
  - lib.kalshi_mlb_single_game_registry.classify_series_for_price_check:
    the strict 17-family allowlist gate. A market that fails this gate is
    NEVER turned into a MarketObservation -- this is the single
    enforcement point for "no forbidden market leakage".

Reads already-captured raw evidence (data/kalshi_registry_snapshots/*.json,
already written by the existing capture-snapshots-scheduled.yml /
clv_capture.yml workflows) -- this module makes ZERO Kalshi API calls of
its own.

Authoritative game context (gameId, scheduledStartTime) comes from
data/pipeline/<date>/normalized_slate.json when available, keyed by
(away, home) team abbreviation. Never derived by reverse-parsing the
Kalshi ticker's ET-local HHMM encoding -- that would require guessing a
DST-aware timezone conversion this repo does not otherwise perform, and
Phase 1 does not fabricate unavailable fields. If no slate match exists,
gameId falls back to parse_contract's own deterministic fallback and
scheduledStart stays null.
"""

import glob
import json
import os

from lib.edgelab import checkpoints, ids
from lib.edgelab import DEFAULT_PLATFORM, DEFAULT_SPORT, SCHEMA_VERSION
from lib.kalshi_mlb_contract_parser import parse_contract
from lib.kalshi_mlb_single_game_registry import (
    SERIES_NOT_ALLOWLISTED,
    classify_series_for_price_check,
    detect_new_unclassified_mlb_series,
    is_mlb_series_prefix,
)
from lib.research.market_taxonomy import classify_market

SNAPSHOT_DIR = os.path.join("data", "kalshi_registry_snapshots")
PIPELINE_DIR = os.path.join("data", "pipeline")

_OPERATOR_MAP = {"greater_than": "OVER", "equals": "YES", "at_least": "AT_LEAST"}


def find_snapshots_for_date(date: str, snapshot_dir=SNAPSHOT_DIR):
    """All kalshi_search_<date>_*.json snapshot files for a date, oldest first."""
    pattern = os.path.join(snapshot_dir, f"kalshi_search_{date}_*.json")
    return sorted(glob.glob(pattern))


def find_latest_snapshot(date: str, snapshot_dir=SNAPSHOT_DIR):
    matches = find_snapshots_for_date(date, snapshot_dir)
    return matches[-1] if matches else None


def load_game_context(date: str, pipeline_dir=PIPELINE_DIR):
    """
    {(awayAbbr, homeAbbr): {"gameId": ..., "scheduledStart": ..., "status": ...,
    "venue": ..., "kalshiKey": ...}} sourced from that date's
    data/pipeline/<date>/normalized_slate.json. Returns {} if the artifact
    doesn't exist (e.g. ingesting a snapshot from before that pipeline
    artifact existed, or a date the slate pipeline never ran for) --
    never raises, never fabricates a fallback.
    """
    path = os.path.join(pipeline_dir, date, "normalized_slate.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            envelope = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    games = (envelope.get("data") or {}).get("games") or []
    context = {}
    for g in games:
        away = (g.get("away") or {}).get("abbr")
        home = (g.get("home") or {}).get("abbr")
        if not away or not home:
            continue
        raw_game_id = g.get("gameId")
        context[(away, home)] = {
            "gameId": str(raw_game_id) if raw_game_id is not None else None,
            "scheduledStart": g.get("startTime"),
            "status": g.get("status"),
            "venue": g.get("venue"),
            "kalshiKey": g.get("kalshiKey"),
        }
    return context


def _extract_captured_at(snapshot: dict, raw_market: dict, snapshot_path: str):
    return (
        raw_market.get("snapshot_ts")
        or snapshot.get("fetched_at")
        or ids.utc_now_iso()
    )


def build_observations_from_snapshot(
    snapshot_path: str, run_id: str, game_context=None, source_system="kalshi_registry_snapshots",
    existing_tickers_seen_today=None, github_run_id=None, commit_sha=None,
):
    """
    Returns (observations, excluded) for one raw snapshot file.

    observations: list of MarketObservation dicts (schema_v1). Includes
    every CLASSIFIED (confirmed single-game-MLB allowlist) market, PLUS
    every UNCLASSIFIED_MLB market -- a KXMLB*-prefixed series Kalshi
    returned that isn't in the allowlist yet and also isn't a recognized
    non-single-game pattern (award/futures/other-competition) -- so the
    Market Research Corpus milestone's "archive every MLB contract,
    including currently unclassified markets" requirement never silently
    drops a brand-new market family. This is strictly an ADDITION to the
    research corpus: lib.kalshi_mlb_single_game_registry.classify_series_for_price_check
    (the production-facing gate used by the standalone price checker and
    the slate pipeline) is completely unchanged and still excludes these.
    excluded: list of {"marketTicker", "seriesTicker", "title", "exclusionReason"}
    for every market that is neither CLASSIFIED nor UNCLASSIFIED_MLB (a
    confirmed non-MLB-competition, futures/award, or otherwise-excluded
    market) -- kept for the daily report's "forbidden market" telemetry,
    never turned into a MarketObservation.

    existing_tickers_seen_today: set of marketTicker values already
    observed earlier today (before this call), used to classify the
    FIRST_DAILY checkpoint correctly across multiple ingestion runs/
    snapshot files for the same date -- see
    scripts/edgelab/ingest_market_observations.py, which loads this from
    the day's already-committed observations partition.
    github_run_id/commit_sha: GITHUB_RUN_ID/GITHUB_SHA of the capturing
    workflow run, when known -- preserved on every observation for audit
    (Market Research Corpus milestone). Null for a local/manual run.
    """
    with open(snapshot_path) as f:
        snapshot = json.load(f)

    game_context = game_context or {}
    existing_tickers_seen_today = existing_tickers_seen_today or set()
    seen_this_call = set()
    ingested_at = ids.utc_now_iso()
    observations = []
    excluded = []

    all_raw_markets = list(snapshot.get("markets") or [])
    for m in snapshot.get("discoveredUnknownSeriesMarkets") or []:
        m = dict(m)
        m["_broadDiscoveryOnly"] = True
        all_raw_markets.append(m)

    for raw in all_raw_markets:
        ticker = raw.get("ticker") or raw.get("market_ticker")
        if not ticker:
            continue
        series_ticker = raw.get("series_ticker") or raw.get("seriesTicker") or ticker.split("-", 1)[0]
        title = raw.get("title")

        allowed, reason = classify_series_for_price_check(series_ticker, title)
        registry_status = "CLASSIFIED"
        if not allowed:
            if reason == SERIES_NOT_ALLOWLISTED and is_mlb_series_prefix(series_ticker):
                registry_status = "UNCLASSIFIED_MLB"
            else:
                excluded.append({
                    "marketTicker": ticker,
                    "seriesTicker": series_ticker,
                    "title": title,
                    "exclusionReason": reason,
                })
                continue

        parsed = parse_contract(raw)
        taxonomy = classify_market(
            parsed["ticker"], parsed["eventTicker"], parsed.get("marketTitle"), parsed.get("marketSubtitle"),
            away_team=parsed.get("awayTeam"), home_team=parsed.get("homeTeam"),
        )

        ctx = None
        if parsed.get("awayTeam") and parsed.get("homeTeam"):
            ctx = game_context.get((parsed["awayTeam"], parsed["homeTeam"]))

        game_id = ctx["gameId"] if ctx and ctx.get("gameId") else parsed.get("gameId")
        scheduled_start = ctx["scheduledStart"] if ctx else None

        captured_at = _extract_captured_at(snapshot, raw, snapshot_path)
        yes_bid, yes_ask = parsed.get("yesBid"), parsed.get("yesAsk")
        spread_cents = (
            round((yes_ask - yes_bid) * 1.0, 2)
            if yes_bid is not None and yes_ask is not None
            else None
        )
        scope = taxonomy.get("scope")
        horizon = {"full_game": "FULL_GAME", "F3": "F3", "F5": "F5", "F7": "F7"}.get(scope)
        operator = _OPERATOR_MAP.get(taxonomy.get("operator"))

        is_first_of_day = ticker not in existing_tickers_seen_today and ticker not in seen_this_call
        seen_this_call.add(ticker)
        checkpoint = checkpoints.classify_checkpoint(
            captured_at, scheduled_start, is_first_of_day=is_first_of_day,
        )
        game_started = (checkpoint == "POST_START") if scheduled_start else None
        market_status = parsed.get("marketStatus")
        market_status_lower = (market_status or "active").lower()
        is_valid_pregame = (
            (not game_started) and market_status_lower in ("active", "unknown")
            if game_started is not None else None
        )

        record = {
            "schemaVersion": SCHEMA_VERSION,
            "marketObservationId": ids.build_market_observation_id(ticker, captured_at),
            "runId": run_id,
            "capturedAt": captured_at,
            "gameId": game_id,
            "sport": DEFAULT_SPORT,
            "platform": DEFAULT_PLATFORM,
            "mlbGameId": ctx["gameId"] if ctx else None,
            "scheduledStart": scheduled_start,
            "awayTeam": parsed.get("awayTeam"),
            "homeTeam": parsed.get("homeTeam"),
            "seriesTicker": series_ticker,
            "eventTicker": parsed.get("eventTicker") or ticker.split("-", 1)[0],
            "marketTicker": ticker,
            "marketFamily": taxonomy.get("family"),
            "marketHorizon": horizon,
            "title": title,
            "subtitle": raw.get("subtitle"),
            "player": taxonomy.get("participant"),
            "team": taxonomy.get("team"),
            "outcomeLabel": taxonomy.get("outcome"),
            "threshold": taxonomy.get("line"),
            "comparisonOperator": operator,
            "yesBid": yes_bid,
            "yesAsk": yes_ask,
            "noBid": parsed.get("noBid"),
            "noAsk": parsed.get("noAsk"),
            "lastPrice": parsed.get("lastPrice"),
            "volume": parsed.get("volume"),
            "openInterest": raw.get("open_interest"),
            "spreadCents": spread_cents,
            "marketStatus": market_status,
            "validationStatus": "valid" if taxonomy.get("classificationStatus") == "classified" else "warning",
            "parserStatus": "parsed" if taxonomy.get("classificationStatus") == "classified" else "partial",
            "lineupConfirmationState": None,
            "checkpoint": checkpoint,
            "isClosingCandidate": is_valid_pregame,
            "gameStartedAtCapture": game_started,
            "isValidPregameObservation": is_valid_pregame,
            "registryClassificationStatus": registry_status,
            "githubRunId": github_run_id,
            "commitSha": commit_sha,
            "createdAt": ingested_at,
            "source": source_system,
            "provenance": {
                "sourceSystem": source_system,
                "sourceFile": snapshot_path,
                "sourceKey": ticker,
                "capturedAt": captured_at,
                "ingestedAt": ingested_at,
            },
        }
        observations.append(record)

    return observations, excluded


# Checkpoints that are always worth a committed row on their own, regardless
# of whether the price changed since the last retained tick -- the named
# pregame-distance research buckets (Market Research Corpus milestone's
# "record standard checkpoints" / "retain the first valid daily observation"
# requirement). FIRST_DAILY is included here defensively even though the
# per-ticker "no previous row" branch below already retains it independently.
_ALWAYS_RETAIN_CHECKPOINTS = frozenset({
    "FIRST_DAILY", "LINEUP_CONFIRMATION", "T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "T_MINUS_15", "T_MINUS_5",
})

# Fields whose change makes an observation "meaningfully different" from the
# last retained tick for the same ticker -- a genuine price or status move,
# never discarded regardless of checkpoint.
_CHANGE_DETECTION_FIELDS = ("yesBid", "yesAsk", "noBid", "noAsk", "lastPrice", "marketStatus")


def _observation_changed(previous, observation):
    if previous is None:
        return True
    return any(previous.get(f) != observation.get(f) for f in _CHANGE_DETECTION_FIELDS)


def select_observations_for_retention(new_observations, previous_by_ticker=None):
    """
    Decide which freshly-built MarketObservation rows are worth committing
    to the corpus this run, vs. a duplicate tick with nothing new to say
    (Market Research Corpus milestone: "avoid commits containing only
    identical duplicate observations" without ever silently discarding a
    real price/status change). Pure and order-preserving.

    Always retains:
      - the first observation of a ticker today (no entry in
        `previous_by_ticker`, or checkpoint == FIRST_DAILY),
      - a named pregame-distance checkpoint (T_MINUS_90/60/30/15/5,
        LINEUP_CONFIRMATION) -- see _ALWAYS_RETAIN_CHECKPOINTS,
      - the specific tick where gameStartedAtCapture first flips to True
        (the last-pregame -> first-post-start transition -- meaningful
        context even when price/status happen not to have moved),
      - any tick whose yesBid/yesAsk/noBid/noAsk/lastPrice/marketStatus
        differ from the last RETAINED observation for that same ticker.

    Only drops a plain recurring-poll tick (INTERMEDIATE, or a repeat
    POST_START tick) that is identical on every field above to the last
    retained observation for its ticker -- never a genuine change.

    `previous_by_ticker`: {marketTicker: last-retained-observation-dict},
    normally the day's already-committed observations (one per ticker, the
    most recent), so retention is correct across separate ingestion runs,
    not just within one run's own batch. Mutated copy only -- the caller's
    dict is never mutated in place.
    """
    previous_by_ticker = dict(previous_by_ticker or {})
    retained = []
    for obs in new_observations:
        ticker = obs.get("marketTicker")
        previous = previous_by_ticker.get(ticker)
        became_post_start = (
            obs.get("gameStartedAtCapture") is True
            and previous is not None
            and previous.get("gameStartedAtCapture") is not True
        )
        if (
            obs.get("checkpoint") in _ALWAYS_RETAIN_CHECKPOINTS
            or became_post_start
            or _observation_changed(previous, obs)
        ):
            retained.append(obs)
            previous_by_ticker[ticker] = obs
    return retained


def new_unclassified_series_warnings(observations, excluded):
    """
    Future-proofing telemetry (Market Research Corpus milestone), reusing
    lib.kalshi_mlb_single_game_registry.detect_new_unclassified_mlb_series:
    now that a genuinely unclassified KXMLB* series is ARCHIVED as an
    observation (registryClassificationStatus=UNCLASSIFIED_MLB) rather
    than dropped into `excluded`, this reconstructs the same
    exclusionReason-shaped records that function expects from both
    sources, so "a brand-new Kalshi MLB series appeared" is still
    surfaced as a NEW_UNCLASSIFIED_MLB_SERIES warning for human review --
    it is never silently absorbed into the corpus without comment just
    because it's no longer in `excluded`.
    """
    synthetic = [
        {"marketTicker": o.get("marketTicker"), "seriesTicker": o.get("seriesTicker"), "title": o.get("title"), "exclusionReason": SERIES_NOT_ALLOWLISTED}
        for o in observations if o.get("registryClassificationStatus") == "UNCLASSIFIED_MLB"
    ]
    return detect_new_unclassified_mlb_series(list(excluded) + synthetic)


def build_game_records(observations, game_context, source_system="kalshi_registry_snapshots"):
    """
    One Game dimension record per distinct gameId seen in `observations`.
    A Kalshi ticker's embedded date fixes a market (and therefore a game)
    to a single calendar day, so first-seen-per-day dedup (via
    storage.append_records against games/<date>.jsonl) is sufficient --
    no cross-day scan is needed.
    """
    now = ids.utc_now_iso()
    seen = {}
    for obs in observations:
        gid = obs.get("gameId")
        if not gid or gid in seen:
            continue
        away, home = obs.get("awayTeam"), obs.get("homeTeam")
        ctx = game_context.get((away, home)) if away and home else None
        game_date = (obs.get("scheduledStart") or obs["capturedAt"])[:10]
        seen[gid] = {
            "schemaVersion": SCHEMA_VERSION,
            "gameId": gid,
            "sport": DEFAULT_SPORT,
            "platform": DEFAULT_PLATFORM,
            "mlbGamePk": ctx["gameId"] if ctx and ctx.get("gameId") else None,
            "gameDate": game_date,
            "scheduledStartTime": obs.get("scheduledStart"),
            "actualStartTime": None,
            "awayTeam": away,
            "homeTeam": home,
            "venue": ctx.get("venue") if ctx else None,
            "status": ctx.get("status") if ctx else None,
            "doubleheaderGameNumber": None,
            "kalshiKey": ctx.get("kalshiKey") if ctx else None,
            "createdAt": now,
            "updatedAt": None,
            "source": source_system,
            "validationStatus": "valid" if ctx else "warning",
            "provenance": {
                "sourceSystem": source_system,
                "sourceFile": obs["provenance"]["sourceFile"],
                "sourceKey": gid,
                "capturedAt": obs["capturedAt"],
                "ingestedAt": now,
            },
        }
    return list(seen.values())


def backfill_missing_game_pks(games, game_context, *, source_path=None, now=None):
    """
    Pure. Root-cause fix for Game rows stuck with mlbGamePk=null: a
    Game record's mlbGamePk is set ONLY from whatever game_context (see
    load_game_context, sourced from data/pipeline/<date>/
    normalized_slate.json) was available at the moment build_game_records
    first created that specific row -- and once created, nothing ever
    revisits it (storage.upsert_records only replaces a row when a NEW
    observation shares its exact gameId; a row keyed by the synthetic
    string fallback is never retroactively merged with a same-game
    numeric-gameId row that appears later). For an early-starting game
    whose Kalshi markets stop being freshly captured (once the game
    starts/closes) before that day's slate enrichment
    (scripts/enrich_data.py) has run, this means mlbGamePk stays null
    forever even after the authoritative match becomes available --
    found via the real Aug 5 2026 case (TOR@HOU, SF@TEX, TB@COL,
    LAD@CHC; all four are the day's earliest-starting games).

    This function is the self-healing half of the fix: given the
    ALREADY-STORED Game records for a date and a FRESH game_context
    (loaded the same way build_game_records already does), it backfills
    mlbGamePk (and the other context-derived fields: venue/status/
    kalshiKey) on any row that (a) still has mlbGamePk=null and (b)
    whose OWN (awayTeam, homeTeam) now has an exact, unique match in
    game_context -- the identical (date implicit in which file was
    loaded) + away + home resolution build_game_records itself already
    uses, never a fuzzy or team-name-similarity match. gameId itself is
    NEVER changed (everything else -- markets, bets, settlements --
    already references it as the join key), and every other field
    (createdAt, provenance, etc.) is preserved untouched -- only the
    previously-null context fields are filled in, plus a
    mlbGamePkBackfill marker recording exactly how/when this happened so
    a future reader never mistakes it for an original first-ingestion
    resolution.

    Returns only the rows that actually changed, for the caller to
    upsert -- a row that already has mlbGamePk, or that still has no
    game_context match, is never touched or returned (so a genuinely
    unresolvable game -- e.g. one truly absent from the slate -- is left
    exactly as before, never guessed).
    """
    now = now or ids.utc_now_iso()
    updated = []
    for g in games:
        if g.get("mlbGamePk") is not None:
            continue
        away, home = g.get("awayTeam"), g.get("homeTeam")
        ctx = game_context.get((away, home)) if away and home else None
        if not ctx or not ctx.get("gameId"):
            continue
        merged = dict(g)
        merged["mlbGamePk"] = ctx["gameId"]
        merged["venue"] = merged.get("venue") or ctx.get("venue")
        merged["status"] = merged.get("status") or ctx.get("status")
        merged["kalshiKey"] = merged.get("kalshiKey") or ctx.get("kalshiKey")
        merged["validationStatus"] = "valid"
        merged["updatedAt"] = now
        merged["mlbGamePkBackfill"] = {
            "backfilledAt": now,
            "method": "DATE_AWAY_HOME_UNIQUE_MATCH",
            "matchedAgainst": source_path or os.path.join(PIPELINE_DIR, g.get("gameDate") or "", "normalized_slate.json"),
        }
        updated.append(merged)
    return updated


def build_market_records(observations, source_system="kalshi_registry_snapshots"):
    """One Market dimension record per distinct marketTicker seen in `observations`."""
    now = ids.utc_now_iso()
    seen = {}
    for obs in observations:
        ticker = obs["marketTicker"]
        if ticker in seen:
            continue
        seen[ticker] = {
            "schemaVersion": SCHEMA_VERSION,
            "marketTicker": ticker,
            "eventTicker": obs["eventTicker"],
            "seriesTicker": obs["seriesTicker"],
            "gameId": obs.get("gameId"),
            "sport": DEFAULT_SPORT,
            "platform": DEFAULT_PLATFORM,
            "marketFamily": obs["marketFamily"],
            "marketHorizon": obs.get("marketHorizon"),
            "title": obs.get("title"),
            "subtitle": obs.get("subtitle"),
            "player": obs.get("player"),
            "team": obs.get("team"),
            "outcomeLabel": obs.get("outcomeLabel"),
            "threshold": obs.get("threshold"),
            "comparisonOperator": obs.get("comparisonOperator"),
            "createdAt": now,
            "updatedAt": None,
            "source": source_system,
            "validationStatus": obs["validationStatus"],
            "parserStatus": obs["parserStatus"],
            "provenance": dict(obs["provenance"], ingestedAt=now),
        }
    return list(seen.values())
