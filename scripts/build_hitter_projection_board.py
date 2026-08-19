#!/usr/bin/env python3
"""
scripts/build_hitter_projection_board.py
===========================================
Hitter Projection Engine -- Phase 4/5 canonical hitter projection board.

I/O-only wrapper around lib.research.hitter_board_builder (all pricing/
distribution/matching/coverage logic lives there, exactly mirroring
scripts/build_projection_board.py's split from lib.kalshi_projection_board,
and scripts/build_hitter_feature_board.py's split from
lib.research.hitter_feature_context). Reuses build_hitter_feature_context()
for every Phase 1-3 field this board needs (starterContext, bullpenContext,
parkContext/weatherContext, defenseContext, platoonContext, baselineTalent)
rather than re-deriving any of it -- this script's only NEW data access is
the opposing starter's own archived raw pitches
(lib.research.statcast_pitch_store.load_pitches_for_pitcher, a Phase 4
addition) and this slate's discovered real Kalshi hitter-prop markets
(data/kalshi_search.json by default, or an explicit --kalshi-search-path /
`kalshi_search_path=` override -- see Phase 5's immutable-snapshot-linkage
requirement below. This script never fetches live.

COMPLETE MARKET PRESERVATION (Phase 5): every real archived hitter
contract in the given Kalshi snapshot gets exactly one row -- either
PROJECTED, or one of lib.research.hitter_board_builder's other STATUS_*
labels (LINEUP_UNCONFIRMED, GAME_STARTED, PLAYER_NOT_IN_STARTING_LINEUP,
PLAYER_ID_UNRESOLVED, MARKET_SEMANTICS_UNSUPPORTED,
AMBIGUOUS_TICKER_MATCH, MISSING_REQUIRED_CONTEXT, MODEL_ERROR). A
contract is never silently dropped just because it can't be priced.
"GAME_STARTED" is determined via lib.edgelab.checkpoints.classify_checkpoint
(a pure time comparison against the snapshot's own capture time -- no
extra live MLB-status fetch), reusing the SAME authoritative "has first
pitch happened" logic lib.edgelab.market_universe already relies on,
per this mission's instruction not to trust Kalshi's own status field
alone when a more authoritative repo-native path already exists.

IMMUTABLE SNAPSHOT LINKAGE (Phase 5): every row is stamped with the
exact `sourceCapturePath` this run was given and each contract's own
`marketObservedAt` (from that raw market's own `snapshot_ts` field) --
this board never mixes a fair probability computed from one Kalshi
capture with a price read from a different, later capture (see
tests/test_hitter_phase5_orchestration.py).

Writes data/pipeline/<date>/hitter_projection_board.json via
lib.pipeline_artifacts.write_stage_artifact() -- purely additive, same
failure posture as every other board script in this repository: never
touches data/slate.json, bets.json, config/rules.json, marketLedger, or
any settlement/staking/risk-gate file; never fails the pipeline (a
missing input file or write error is reported and the script exits 0).
EVERY row on this board is priced regardless of edge sign or size -- no
recommendation/staking gate is applied or imported here (see
lib.research.hitter_board_builder's own docstring).

Independently runnable for research/debugging (reads whatever the
mutable data/kalshi_search.json currently holds by default), but the
normal operational path is scripts/run_standalone_hitter_research.py,
which supplies an explicit immutable --kalshi-search-path.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.research.hitter_feature_context import build_hitter_feature_context  # noqa: E402
from lib.research.hitter_board_builder import build_game_contract_coverage, STATUS_PROJECTED  # noqa: E402
from lib.research.statcast_pitch_store import load_pitches_for_batter, load_pitches_for_pitcher  # noqa: E402
from lib.research.bat_tracking_store import load_history as load_bat_tracking_history  # noqa: E402
from lib.research.defense_store import latest_snapshot as latest_defense_snapshot  # noqa: E402
from lib.research.sprint_speed_store import latest_snapshot as latest_sprint_speed_snapshot  # noqa: E402
from lib.research.catcher_framing_store import latest_snapshot as latest_catcher_framing_snapshot  # noqa: E402
from lib.research.park_geometry import resolve_park_geometry  # noqa: E402
from lib.edgelab.checkpoints import classify_checkpoint  # noqa: E402
from scripts.fetch_umpire_assignment import load_umpire_assignment  # noqa: E402
from lib.pipeline_artifacts import write_stage_artifact  # noqa: E402

DEFAULT_SLATE_PATH = os.path.join("data", "slate.json")
DEFAULT_WEATHER_PATH = os.path.join("data", "weather.json")
DEFAULT_SAVANT_TEAM_PATH = os.path.join("data", "savant_team.json")
DEFAULT_KALSHI_SEARCH_PATH = os.path.join("data", "kalshi_search.json")

_HITTER_SERIES_PREFIXES = ("KXMLBHIT-", "KXMLBTB-", "KXMLBRBI-", "KXMLBHRR-")
DEFAULT_N_SIMS = 1500
ALL_STATUS_LABELS = (
    STATUS_PROJECTED, "LINEUP_UNCONFIRMED", "GAME_STARTED", "PLAYER_NOT_IN_STARTING_LINEUP",
    "PLAYER_ID_UNRESOLVED", "MARKET_SEMANTICS_UNSUPPORTED", "AMBIGUOUS_TICKER_MATCH",
    "MISSING_REQUIRED_CONTEXT", "MODEL_ERROR",
)


def load_json(path):
    with open(path) as f:
        return json.load(f)


def _weather_by_team(weather_path):
    try:
        doc = load_json(weather_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, None
    parks = doc.get("parks") or []
    return {p.get("team"): p for p in parks if p.get("team")}, doc.get("updatedAt")


def _savant_batters(savant_team_path):
    try:
        doc = load_json(savant_team_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {}, None
    return doc.get("batters") or {}, doc.get("battersDiscipline") or {}, doc.get("fetchedAt")


def _load_kalshi_snapshot(kalshi_search_path):
    """Returns (all_hitter_markets, captured_at) -- captured_at is the snapshot's OWN
    `fetched_at` field (the immutable capture time this whole run's game-started
    determination and every row's marketObservedAt are relative to), never "now"."""
    try:
        doc = load_json(kalshi_search_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return [], None
    captured_at = doc.get("fetched_at")
    out = []
    for m in (doc.get("markets") or []):
        ticker = m.get("market_ticker") or m.get("ticker") or ""
        if ticker.startswith(_HITTER_SERIES_PREFIXES):
            out.append(m)
    return out, captured_at


def _et_time_str(iso_ts):
    """ISO UTC timestamp -> ET 'HHMM' string, matching Kalshi's own event-ticker
    time encoding (e.g. 'KXMLBHIT-26AUG181940ATHKC' embeds '1940' = 7:40 PM ET).
    Mirrors scripts/discover_kalshi_mlb_markets.py's own _et_time_str exactly (same
    conversion) -- duplicated here rather than imported to avoid a script-to-script
    coupling for one small, pure, single-purpose helper. Returns None if unparseable."""
    if not iso_ts:
        return None
    try:
        s = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if ZoneInfo is not None:
        dt = dt.astimezone(ZoneInfo("America/New_York"))
    else:
        dt = dt.astimezone(timezone(timedelta(hours=-4)))
    return dt.strftime("%H%M")


def build_game_time_lookup(games):
    """{(away_abbr, home_abbr): [(time_str, gameId), ...]} sorted by time_str, for
    doubleheader-aware market-to-game disambiguation. Two real games on the same date
    between the same two teams (a doubleheader) share an IDENTICAL event-ticker
    away+home suffix -- this lookup, built once per slate, lets _raw_markets_for_game
    tell them apart by each candidate's OWN scheduled time, mirroring
    scripts/discover_kalshi_mlb_markets.py's build_slate_index/resolve_game_match
    (same disambiguation principle, scoped to hitter markets here)."""
    lookup = {}
    for g in games or []:
        away = (g.get("away") or {}).get("abbr")
        home = (g.get("home") or {}).get("abbr")
        time_str = _et_time_str(g.get("startTime"))
        if away and home and time_str:
            lookup.setdefault((away, home), []).append((time_str, g.get("gameId")))
    for key in lookup:
        lookup[key].sort()
    return lookup


def _ticker_time_near_suffix(ticker, suffix):
    """The 4-digit HHMM segment immediately preceding `suffix` in `ticker`
    (Kalshi's own ticker encoding always places the ET time directly before the
    away+home team-abbreviation suffix), or None if it isn't there / isn't digits --
    never guessed, never fabricated."""
    idx = ticker.rfind(suffix)
    if idx < 4:
        return None
    segment = ticker[idx - 4:idx]
    return segment if segment.isdigit() else None


def _raw_markets_for_game(all_hitter_markets, away_abbr, home_abbr, *, game_id=None, game_time_lookup=None):
    """
    Every raw hitter market for THIS specific game.

    When only one real game exists for this (away_abbr, home_abbr) pair on the
    slate (the overwhelming common case), this is exactly the original bare
    team-abbreviation-suffix match -- unchanged behavior.

    When MULTIPLE real games share the same away/home abbreviations on the same
    date (a doubleheader -- distinct gameIds, identical ticker suffix), a bare
    suffix match is ambiguous: both legs' tickers end in the same
    "{away}{home}" text. `game_time_lookup` (from build_game_time_lookup, built
    once per slate) disambiguates each matched market by its OWN embedded ticker
    time, assigning it to whichever candidate game's scheduled time is closest --
    never guessing, never silently merging two real games' markets together. A
    market whose ticker time can't be extracted is conservatively attributed to
    the earliest-time candidate only (matches
    scripts/discover_kalshi_mlb_markets.py's resolve_game_match's own
    no-scheduled-time fallback) rather than being duplicated across every
    candidate or silently dropped.
    """
    if not away_abbr or not home_abbr:
        return []
    suffix = f"{away_abbr}{home_abbr}"
    matched = [m for m in all_hitter_markets
               if suffix in (m.get("event_ticker") or m.get("eventTicker") or "")]

    candidates = (game_time_lookup or {}).get((away_abbr, home_abbr)) or []
    if len(candidates) <= 1 or game_id is None:
        return matched

    out = []
    for m in matched:
        ticker = m.get("event_ticker") or m.get("eventTicker") or ""
        ticker_time = _ticker_time_near_suffix(ticker, suffix)
        if ticker_time is None:
            best_game_id = candidates[0][1]
        else:
            best_game_id = min(candidates, key=lambda c: abs(int(c[0]) - int(ticker_time)))[1]
        if best_game_id == game_id:
            out.append(m)
    return out


def _confirmed_batter_ids(slate_doc):
    ids = set()
    for g in slate_doc.get("games") or []:
        for side in ("awayTeamStats", "homeTeamStats"):
            for h in ((g.get(side) or {}).get("confirmedLineup") or []):
                pid = h.get("playerId")
                if pid is not None:
                    ids.add(str(pid))
    return ids


def _confirmed_team_abbrs(slate_doc):
    abbrs = set()
    for g in slate_doc.get("games") or []:
        for side in ("away", "home"):
            abbr = (g.get(side) or {}).get("abbr")
            if abbr:
                abbrs.add(abbr)
    return abbrs


def _confirmed_catcher_ids(slate_doc):
    ids = set()
    for g in slate_doc.get("games") or []:
        for side in ("awayTeamStats", "homeTeamStats"):
            for h in ((g.get(side) or {}).get("confirmedLineup") or []):
                if h.get("position") == "C" and h.get("playerId") is not None:
                    ids.add(str(h["playerId"]))
    return ids


def _load_by_batter(loader, batter_ids, as_of_date):
    out = {}
    for pid in batter_ids:
        v = loader(pid, as_of=as_of_date)
        if v:
            out[pid] = v
    return out


def _load_bat_tracking(batter_ids, as_of_date):
    out = {}
    for pid in batter_ids:
        history = load_bat_tracking_history(pid, as_of=as_of_date)
        if history:
            out[pid] = {"latest": history[-1], "history": history}
    return out


def _load_umpire_assignments(slate_doc):
    out = {}
    for g in slate_doc.get("games") or []:
        game_id = g.get("gameId")
        if game_id is None:
            continue
        record = load_umpire_assignment(game_id)
        if record:
            out[game_id] = record
    return out


def _season_stats_from_baseline(baseline_talent):
    horizon = ((baseline_talent or {}).get("horizons") or {}).get("currentSeason") or {}
    stats = horizon.get("stats") or {}
    return {k: stats.get(k) for k in ("PA", "AB", "1B", "2B", "3B", "HR", "BB", "HBP", "K")}


def _is_game_started(game, captured_at):
    """Pure. Reuses lib.edgelab.checkpoints.classify_checkpoint's POST_START determination
    -- a time comparison between `captured_at` (the Kalshi snapshot's own capture time) and
    the game's own scheduled `startTime`, never a live MLB-status fetch and never a trust of
    Kalshi's own (unaudited) market status field. Returns False (never started) if either
    timestamp is unavailable -- a market is never marked GAME_STARTED on missing evidence."""
    scheduled_start = game.get("startTime")
    if not captured_at or not scheduled_start:
        return False
    try:
        return classify_checkpoint(captured_at, scheduled_start) == "POST_START"
    except (ValueError, TypeError):
        return False


def _hitter_entry(hitter_ctx, team_abbr, matchup_label, as_of_date, source_meta):
    """Builds one (hitter_dict, kwargs_or_None) pair for build_game_contract_coverage from one
    build_hitter_feature_context() hitter record. kwargs is None when target_slot is missing
    (the reconciliation pass then reports MISSING_REQUIRED_CONTEXT for that hitter's contracts
    rather than this function silently omitting the hitter from coverage entirely)."""
    identity = hitter_ctx.get("playerIdentity") or {}
    lineup = hitter_ctx.get("lineupContext") or {}
    player_id = identity.get("playerId")
    player_name = identity.get("name")
    batter_hand = identity.get("batSide")
    target_slot = lineup.get("order")

    hitter_dict = {"playerId": player_id, "name": player_name, "teamAbbr": team_abbr}
    if not target_slot or not (1 <= target_slot <= 9):
        return hitter_dict, None

    starter_context = hitter_ctx.get("starterContext") or {}
    starter_pitcher_id = starter_context.get("pitcherId")
    starter_pitches = load_pitches_for_pitcher(str(starter_pitcher_id), as_of=as_of_date) if starter_pitcher_id and as_of_date else None

    home_abbr = source_meta.get("_gameHomeAbbr")
    park_geometry_entry = resolve_park_geometry(home_abbr, as_of=as_of_date) if home_abbr else None
    weather_context = hitter_ctx.get("weatherContext") or {}
    field_relative_wind = weather_context.get("windRelativeToParkOrientation")

    defense_context = hitter_ctx.get("defenseContext") or {}
    opponent_defense = defense_context.get("opponentDefense") or {}
    hitter_speed = defense_context.get("hitterSpeed") or {}
    defense_snapshot = {"infieldOAA": opponent_defense.get("infieldOAA"),
                         "outfieldOAA": opponent_defense.get("outfieldOAA")} if opponent_defense.get("status") == "AVAILABLE" else None
    hitter_speed_snapshot = {"sprintSpeedFtPerSec": hitter_speed.get("sprintSpeedFtPerSec")} if hitter_speed.get("status") == "AVAILABLE" else None

    raw_pitches = None
    raw_pitches_by_batter = source_meta.get("rawPitchesByBatter") or {}
    if player_id is not None:
        raw_pitches = raw_pitches_by_batter.get(str(player_id))

    baseline_talent = hitter_ctx.get("baselineTalent") or {}
    season_stats = _season_stats_from_baseline(baseline_talent)
    platoon_context = hitter_ctx.get("platoonContext") or {}
    season_woba = season_stats.get("wOBA") if "wOBA" in season_stats else None

    kwargs = dict(
        player_id=player_id, player_name=player_name, batter_hand=batter_hand, target_slot=target_slot,
        matchup_label=matchup_label, raw_pitches=raw_pitches or [], season_stats=season_stats,
        starter_pitches=starter_pitches, starter_context=starter_context,
        bullpen_context=hitter_ctx.get("bullpenContext") or {},
        park_geometry_entry=park_geometry_entry, field_relative_wind=field_relative_wind,
        defense_snapshot=defense_snapshot, hitter_speed_snapshot=hitter_speed_snapshot,
        platoon_context=platoon_context, season_woba=season_woba,
    )
    return hitter_dict, kwargs


def _build_rows_for_game(game, weather_lookup, source_meta, all_hitter_markets, n_sims, seed_base,
                          captured_at, source_capture_path, research_run_id, generated_at,
                          game_time_lookup=None):
    away_abbr = (game.get("away") or {}).get("abbr")
    home_abbr = (game.get("home") or {}).get("abbr")
    matchup_label = f"{away_abbr} @ {home_abbr}"
    as_of_date = source_meta.get("asOfDate")
    game_markets = _raw_markets_for_game(
        all_hitter_markets, away_abbr, home_abbr,
        game_id=game.get("gameId"), game_time_lookup=game_time_lookup,
    )
    if not game_markets:
        return [], []

    game_started = _is_game_started(game, captured_at)
    # lib.research.hitter_feature_context.build_hitter_feature_context gates hitter
    # generation on `lineupConfirmedOfficial` specifically (NOT the looser `lineupConfirmed`
    # flag) -- reusing the exact same field here keeps this coverage pass's
    # LINEUP_UNCONFIRMED/PLAYER_NOT_IN_STARTING_LINEUP distinction consistent with which
    # hitters that function actually returns, rather than a second, subtly different notion
    # of "confirmed."
    lineup_confirmed_by_abbr = {
        away_abbr: bool((game.get("awayTeamStats") or {}).get("lineupConfirmedOfficial")),
        home_abbr: bool((game.get("homeTeamStats") or {}).get("lineupConfirmedOfficial")),
    }

    per_game_meta = dict(source_meta, _gameHomeAbbr=home_abbr)
    hitters_both_sides = []
    hitter_kwargs_by_player_id = {}
    for side in ("away", "home"):
        ctx = build_hitter_feature_context(game, side, weather_by_team=weather_lookup, source_meta=per_game_meta)
        team_abbr = away_abbr if side == "away" else home_abbr
        for hitter_ctx in (ctx.get("hitters") or []):
            hitter_dict, kwargs = _hitter_entry(hitter_ctx, team_abbr, matchup_label, as_of_date, per_game_meta)
            hitters_both_sides.append(hitter_dict)
            if kwargs is not None and hitter_dict.get("playerId") is not None:
                hitter_kwargs_by_player_id[hitter_dict["playerId"]] = kwargs

    result = build_game_contract_coverage(
        game_markets, hitters_both_sides, hitter_kwargs_by_player_id,
        away_abbr, home_abbr, matchup_label, lineup_confirmed_by_abbr, game_started,
        source_capture_path=source_capture_path, research_run_id=research_run_id, generated_at=generated_at,
        n_sims=n_sims, seed_base=seed_base,
    )
    return result["rows"], result["hitterSummaries"]


def main(date_str=None, slate_path=None, weather_path=None, savant_team_path=None,
         kalshi_search_path=None, n_sims=DEFAULT_N_SIMS, research_run_id=None, dry_run=False,
         emit_rows=False):
    started_at = time.time()
    slate_path = slate_path or DEFAULT_SLATE_PATH
    weather_path = weather_path or DEFAULT_WEATHER_PATH
    savant_team_path = savant_team_path or DEFAULT_SAVANT_TEAM_PATH
    kalshi_search_path = kalshi_search_path or DEFAULT_KALSHI_SEARCH_PATH

    try:
        slate_doc = load_json(slate_path)
    except FileNotFoundError:
        print(f"[build_hitter_projection_board] No slate file at {slate_path} — nothing to build")
        return {"date": date_str, "status": "NO_SLATE_FILE", "totalRows": 0}

    date_str = date_str or slate_doc.get("date")
    weather_lookup, weather_updated_at = _weather_by_team(weather_path)
    savant_batters, savant_batters_discipline, savant_fetched_at = _savant_batters(savant_team_path)
    all_hitter_markets, captured_at = _load_kalshi_snapshot(kalshi_search_path)
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    batter_ids = _confirmed_batter_ids(slate_doc)
    catcher_ids = _confirmed_catcher_ids(slate_doc)
    team_abbrs = _confirmed_team_abbrs(slate_doc)

    source_meta = {
        "weatherUpdatedAt": weather_updated_at,
        "savantTeamFetchedAt": savant_fetched_at,
        "asOfDate": date_str,
        "savantBatters": savant_batters,
        "savantBattersDiscipline": savant_batters_discipline,
        "rawPitchesByBatter": _load_by_batter(load_pitches_for_batter, batter_ids, date_str) if date_str else {},
        "batTrackingByBatter": _load_bat_tracking(batter_ids, date_str) if date_str else {},
        "defenseByTeam": {abbr: latest_defense_snapshot(abbr, as_of=date_str) for abbr in team_abbrs} if date_str else {},
        "sprintSpeedByBatter": _load_by_batter(latest_sprint_speed_snapshot, batter_ids, date_str) if date_str else {},
        "catcherFramingByCatcher": _load_by_batter(latest_catcher_framing_snapshot, catcher_ids, date_str) if date_str else {},
        "umpireByGame": _load_umpire_assignments(slate_doc),
    }
    source_meta["defenseByTeam"] = {k: v for k, v in source_meta["defenseByTeam"].items() if v}

    all_rows = []
    hitter_summaries = []
    seed_base = 0
    game_time_lookup = build_game_time_lookup(slate_doc.get("games"))
    for g in slate_doc.get("games") or []:
        rows, summaries = _build_rows_for_game(
            g, weather_lookup, source_meta, all_hitter_markets, n_sims, seed_base,
            captured_at, kalshi_search_path, research_run_id, generated_at,
            game_time_lookup=game_time_lookup,
        )
        # Stamp the row with its OWN game's real, stable gameId (never a
        # matchup-label string, which is ambiguous for doubleheaders / a
        # same-abbreviation rematch on the same date -- two distinct real
        # games can share an identical "AWY @ HOM" label). This is the
        # only field added here; every existing field this row already
        # carries is untouched. A caller (e.g.
        # lib.research.hitter_prospective_snapshot's checkpoint scheduler)
        # can now key off row["gameId"] directly instead of re-deriving
        # game identity from a matchup string.
        game_id = g.get("gameId")
        for row in rows:
            row["gameId"] = game_id
        all_rows.extend(rows)
        hitter_summaries.extend(summaries)
        seed_base += len(summaries) or 1

    rows_by_status = {status: sum(1 for r in all_rows if r["projectionStatus"] == status) for status in ALL_STATUS_LABELS}
    summary = {
        "date": date_str,
        "totalGames": len(slate_doc.get("games") or []),
        "totalHitters": len(hitter_summaries),
        "hittersProjected": sum(1 for h in hitter_summaries if h["status"] == STATUS_PROJECTED),
        "totalRows": len(all_rows),
        "rowsByProjectionStatus": rows_by_status,
        "totalHitterMarketsDiscovered": len(all_hitter_markets),
        "monteCarloSimulationsPerHitter": n_sims,
        "sourceCapturePath": kalshi_search_path,
        "marketObservedAt": captured_at,
        "researchRunId": research_run_id,
        "generatedAt": generated_at,
        "elapsedSeconds": round(time.time() - started_at, 2),
    }

    if not dry_run and date_str:
        try:
            path = write_stage_artifact(
                "hitter_projection_board", date_str, {"rows": all_rows, "hitterSummaries": hitter_summaries, "summary": summary},
                produced_by="scripts/build_hitter_projection_board.py",
                source_stage="market_ledger",
            )
            summary = dict(summary, artifactPath=path)
        except Exception as e:
            print(f"[build_hitter_projection_board] WARNING: failed to write pipeline artifact: {e}")
            summary = dict(summary, artifactWriteError=str(e))

    if emit_rows:
        # Additive, opt-in only -- every existing caller's return shape is
        # unchanged. Lets a caller that needs the actual row data (e.g. a
        # checkpoint-scoped scheduler that runs this against a filtered,
        # run-scoped slate rather than the canonical daily one -- see
        # lib/research/hitter_prospective_snapshot.py) get it directly,
        # without reading back data/pipeline/<date>/hitter_projection_board.json
        # (which, on a filtered/partial-slate call, would only reflect that
        # partial slate -- never a source of truth for a caller that isn't
        # itself the canonical once-daily board build).
        return dict(summary, rows=all_rows, hitterSummaries=hitter_summaries)

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", nargs="?", default=None)
    parser.add_argument("--kalshi-search-path", default=None,
                         help="Explicit immutable Kalshi snapshot to price against (Phase 5) -- "
                              "defaults to the mutable data/kalshi_search.json for standalone research use.")
    parser.add_argument("--research-run-id", default=None)
    parser.add_argument("--n-sims", type=int, default=DEFAULT_N_SIMS)
    args = parser.parse_args()
    result = main(date_str=args.date, kalshi_search_path=args.kalshi_search_path,
                  research_run_id=args.research_run_id, n_sims=args.n_sims)
    print(json.dumps(result, indent=2))
