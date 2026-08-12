#!/usr/bin/env python3
"""
scripts/build_hitter_projection_board.py
===========================================
Hitter Projection Engine -- Phase 4 canonical hitter projection board.

I/O-only wrapper around lib.research.hitter_board_builder (all pricing/
distribution/matching logic lives there, exactly mirroring
scripts/build_projection_board.py's split from lib.kalshi_projection_board,
and scripts/build_hitter_feature_board.py's split from
lib.research.hitter_feature_context). Reuses build_hitter_feature_context()
for every Phase 1-3 field this board needs (starterContext, bullpenContext,
parkContext/weatherContext, defenseContext, platoonContext, baselineTalent)
rather than re-deriving any of it -- this script's only NEW data access is
the opposing starter's own archived raw pitches
(lib.research.statcast_pitch_store.load_pitches_for_pitcher, a Phase 4
addition) and this slate's discovered real Kalshi hitter-prop markets
(data/kalshi_search.json, already fetched by scripts/fetch_kalshi_markets.py
ahead of time -- this script never fetches live).

Writes data/pipeline/<date>/hitter_projection_board.json via
lib.pipeline_artifacts.write_stage_artifact() -- purely additive, same
failure posture as every other board script in this repository: never
touches data/slate.json, bets.json, config/rules.json, marketLedger, or
any settlement/staking/risk-gate file; never fails the pipeline (a
missing input file or write error is reported and the script exits 0).
EVERY row on this board is priced regardless of edge sign or size -- no
recommendation/staking gate is applied or imported here (see
lib.research.hitter_board_builder's own docstring).

NOT wired into .github/workflows/fetch-slate.yml in this phase -- a
standalone, safe-to-run-anytime artifact, matching
scripts/build_hitter_feature_board.py's own precedent
(docs/HITTER_SIMULATION_ENGINE.md).
"""
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.research.hitter_feature_context import build_hitter_feature_context  # noqa: E402
from lib.research.hitter_board_builder import build_hitter_projection_rows  # noqa: E402
from lib.research.statcast_pitch_store import load_pitches_for_batter, load_pitches_for_pitcher  # noqa: E402
from lib.research.bat_tracking_store import load_history as load_bat_tracking_history  # noqa: E402
from lib.research.defense_store import latest_snapshot as latest_defense_snapshot  # noqa: E402
from lib.research.sprint_speed_store import latest_snapshot as latest_sprint_speed_snapshot  # noqa: E402
from lib.research.catcher_framing_store import latest_snapshot as latest_catcher_framing_snapshot  # noqa: E402
from lib.research.park_geometry import resolve_park_geometry  # noqa: E402
from scripts.fetch_umpire_assignment import load_umpire_assignment  # noqa: E402
from lib.pipeline_artifacts import write_stage_artifact  # noqa: E402

DEFAULT_SLATE_PATH = os.path.join("data", "slate.json")
DEFAULT_WEATHER_PATH = os.path.join("data", "weather.json")
DEFAULT_SAVANT_TEAM_PATH = os.path.join("data", "savant_team.json")
DEFAULT_KALSHI_SEARCH_PATH = os.path.join("data", "kalshi_search.json")

_HITTER_SERIES_PREFIXES = ("KXMLBHIT-", "KXMLBTB-", "KXMLBRBI-", "KXMLBHRR-")
DEFAULT_N_SIMS = 1500


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


def _hitter_raw_markets(kalshi_search_path):
    """All raw markets whose ticker matches one of this repo's 4 confirmed-real hitter-prop series prefixes -- a cheap pre-filter, not the authoritative classification (lib.research.market_taxonomy.classify_market still does that per-candidate in hitter_board_builder)."""
    try:
        doc = load_json(kalshi_search_path)
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    out = []
    for m in (doc.get("markets") or []):
        ticker = m.get("market_ticker") or m.get("ticker") or ""
        if ticker.startswith(_HITTER_SERIES_PREFIXES):
            out.append(m)
    return out


def _raw_markets_for_game(all_hitter_markets, away_abbr, home_abbr):
    if not away_abbr or not home_abbr:
        return []
    suffix = f"{away_abbr}{home_abbr}"
    return [m for m in all_hitter_markets
            if suffix in (m.get("event_ticker") or m.get("eventTicker") or "")]


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


def _build_rows_for_side(game, side, weather_lookup, source_meta,
                          all_hitter_markets, n_sims, seed_base):
    ctx = build_hitter_feature_context(game, side, weather_by_team=weather_lookup, source_meta=source_meta)
    hitters = ctx.get("hitters") or []
    away_abbr = (game.get("away") or {}).get("abbr")
    home_abbr = (game.get("home") or {}).get("abbr")
    matchup_label = f"{away_abbr} @ {home_abbr}"
    raw_markets = _raw_markets_for_game(all_hitter_markets, away_abbr, home_abbr)
    as_of_date = source_meta.get("asOfDate")

    rows_out = []
    hitter_summaries = []
    for i, hitter in enumerate(hitters):
        identity = hitter.get("playerIdentity") or {}
        lineup = hitter.get("lineupContext") or {}
        player_id = identity.get("playerId")
        player_name = identity.get("name")
        batter_hand = identity.get("batSide")
        target_slot = lineup.get("order")

        starter_context = (hitter.get("starterContext") or {})
        starter_pitcher_id = starter_context.get("pitcherId")
        starter_pitches = load_pitches_for_pitcher(str(starter_pitcher_id), as_of=as_of_date) if starter_pitcher_id and as_of_date else None

        park_geometry_entry = resolve_park_geometry(home_abbr, as_of=as_of_date) if home_abbr else None
        weather_context = hitter.get("weatherContext") or {}
        field_relative_wind = weather_context.get("windRelativeToParkOrientation")

        defense_context = hitter.get("defenseContext") or {}
        opponent_defense = (defense_context.get("opponentDefense") or {})
        hitter_speed = (defense_context.get("hitterSpeed") or {})
        defense_snapshot = {"infieldOAA": opponent_defense.get("infieldOAA"),
                             "outfieldOAA": opponent_defense.get("outfieldOAA")} if opponent_defense.get("status") == "AVAILABLE" else None
        hitter_speed_snapshot = {"sprintSpeedFtPerSec": hitter_speed.get("sprintSpeedFtPerSec")} if hitter_speed.get("status") == "AVAILABLE" else None

        raw_pitches = None
        raw_pitches_by_batter = source_meta.get("rawPitchesByBatter") or {}
        if player_id is not None:
            raw_pitches = raw_pitches_by_batter.get(str(player_id))

        baseline_talent = hitter.get("baselineTalent") or {}
        season_stats = _season_stats_from_baseline(baseline_talent)
        platoon_context = hitter.get("platoonContext") or {}
        season_woba = season_stats.get("wOBA") if "wOBA" in season_stats else None

        result = build_hitter_projection_rows(
            player_id=player_id, player_name=player_name, batter_hand=batter_hand, target_slot=target_slot,
            matchup_label=matchup_label, raw_pitches=raw_pitches or [], season_stats=season_stats,
            starter_pitches=starter_pitches, starter_context=starter_context,
            bullpen_context=hitter.get("bullpenContext") or {},
            park_geometry_entry=park_geometry_entry, field_relative_wind=field_relative_wind,
            defense_snapshot=defense_snapshot, hitter_speed_snapshot=hitter_speed_snapshot,
            platoon_context=platoon_context, season_woba=season_woba,
            raw_markets_for_game=raw_markets, away_abbr=away_abbr, home_abbr=home_abbr,
            n_sims=n_sims, seed=seed_base + i,
        )
        rows_out.extend(result["rows"])
        hitter_summaries.append({
            "playerId": player_id, "name": player_name, "status": result["status"],
            "rowsProduced": len(result["rows"]),
        })

    return rows_out, hitter_summaries


def main(date_str=None, slate_path=None, weather_path=None, savant_team_path=None,
         kalshi_search_path=None, n_sims=DEFAULT_N_SIMS, dry_run=False):
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
    all_hitter_markets = _hitter_raw_markets(kalshi_search_path)

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
    for g in slate_doc.get("games") or []:
        for side in ("away", "home"):
            rows, summaries = _build_rows_for_side(
                g, side, weather_lookup, source_meta, all_hitter_markets, n_sims, seed_base,
            )
            all_rows.extend(rows)
            hitter_summaries.extend(summaries)
            seed_base += len(summaries) or 1

    summary = {
        "date": date_str,
        "totalGames": len(slate_doc.get("games") or []),
        "totalHitters": len(hitter_summaries),
        "hittersProjected": sum(1 for h in hitter_summaries if h["status"] == "PROJECTED"),
        "hittersWithNoLineupSlot": sum(1 for h in hitter_summaries if h["status"] == "NO_LINEUP_SLOT"),
        "hittersWithNoArchivedContracts": sum(1 for h in hitter_summaries if h["status"] == "NO_ARCHIVED_CONTRACTS"),
        "totalRows": len(all_rows),
        "totalHitterMarketsDiscovered": len(all_hitter_markets),
        "monteCarloSimulationsPerHitter": n_sims,
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

    return summary


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = main(date_str=arg_date)
    print(json.dumps(result, indent=2))
