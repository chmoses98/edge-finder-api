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

from lib.edgelab import ids
from lib.edgelab import SCHEMA_VERSION
from lib.kalshi_mlb_contract_parser import parse_contract
from lib.kalshi_mlb_single_game_registry import classify_series_for_price_check
from lib.research.market_taxonomy import classify_market

SNAPSHOT_DIR = os.path.join("data", "kalshi_registry_snapshots")
PIPELINE_DIR = os.path.join("data", "pipeline")

_OPERATOR_MAP = {"greater_than": "OVER", "equals": "YES"}


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
        context[(away, home)] = {
            "gameId": g.get("gameId"),
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


def build_observations_from_snapshot(snapshot_path: str, run_id: str, game_context=None, source_system="kalshi_registry_snapshots"):
    """
    Returns (observations, excluded) for one raw snapshot file.

    observations: list of MarketObservation dicts (schema_v1), one per
    legitimate market found in the snapshot.
    excluded: list of {"marketTicker", "seriesTicker", "title", "exclusionReason"}
    for every market the strict registry gate rejected -- kept for the
    daily report's "forbidden market" and "new/unclassified series"
    telemetry, never turned into a MarketObservation.
    """
    with open(snapshot_path) as f:
        snapshot = json.load(f)

    game_context = game_context or {}
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
        if not allowed:
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

        record = {
            "schemaVersion": SCHEMA_VERSION,
            "marketObservationId": ids.build_market_observation_id(ticker, captured_at),
            "runId": run_id,
            "capturedAt": captured_at,
            "gameId": game_id,
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
            "side": taxonomy.get("outcome"),
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
            "marketStatus": parsed.get("marketStatus"),
            "validationStatus": "valid" if taxonomy.get("classificationStatus") == "classified" else "warning",
            "parserStatus": "parsed" if taxonomy.get("classificationStatus") == "classified" else "partial",
            "lineupConfirmationState": None,
            "checkpoint": None,
            "isClosingCandidate": None,
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
            "marketFamily": obs["marketFamily"],
            "marketHorizon": obs.get("marketHorizon"),
            "title": obs.get("title"),
            "subtitle": obs.get("subtitle"),
            "player": obs.get("player"),
            "team": obs.get("team"),
            "side": obs.get("side"),
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
