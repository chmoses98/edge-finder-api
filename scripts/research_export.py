#!/usr/bin/env python3
"""
scripts/research_export.py
==========================
The MLB research-graph ("explorer") adapter: publishes ``app/latest/explorer/`` beside the v1 app
export (``scripts/app_export.py``) from data this repository already commits on ``main``.

    python scripts/research_export.py --out app/latest [--data-root data] [--now ISO]
                                      [--commit-sha X] [--slate-start D] [--slate-end D]
                                      [--seasons 2025,2026] [--statcast-start D] [--statcast-end D]

Run it AFTER the v1 export into the same app root: ``run_id`` is read from ``<out>/manifest.json``
and ``--now`` defaults to that manifest's ``generated_at`` (the explorer and the v1 payload describe
the same publication). Pure re-expression: no network, no model fit, no threshold, no stake. Every
number is a stored value or simple arithmetic over stored values (per-game means, win/loss records,
rates, rolling means, ranks). What the repository does not hold is published as UNAVAILABLE.

Sources (all under --data-root; the audit scratchpad/phase2/audit_mlb.md section 10 is the authority):
    research_cache/bullpen_backtest/<yr>/schedules/<TEAM>.json   5-season results / opponents (VERIFIED, one-off)
    research_cache/batting_backtest/<yr>/boxscores.jsonl.gz      team batting box lines, joined on gamePk
    research_cache/starter_workload/<yr>/boxscores.jsonl.gz      per-pitcher lines, joined on gamePk
    slates/<date>/authoritative.json                             per-game model inputs (63 dates)
    team_offense_form.json                                       L5/L7/L10 form windows (analysis-only)
    bullpen.json, savant_team.json, oppquality.json              current snapshots (overwritten daily)
    statcast_raw/index/*.jsonl, statcast_raw/games/<pk>.jsonl    per-player per-game aggregates (2026-08-11..09-27)
    edgelab/observations/<date>, edgelab/clv_quotes/<date>       market price series + closing quotes
    edgelab/model_evaluations/<date>, edgelab/markets/<date>     projection-over-time per ticker
    research/calibration_bins.json                               REAL-wager calibration bins
    edgelab/postmortems/<date>/                                  postmortem inventory
    <out>/{manifest,events,markets,model_prices,recommendations,wagers}.json   the v1 publication

On failure the previous explorer tree is untouched (``research.publish_explorer`` is atomic) and the
process exits 1; the v1 payload is never touched by this script.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import os
import statistics
import sys
import traceback
import unicodedata

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
_CONTRACT_DIR = os.path.join(REPO_ROOT, "contract")
if _CONTRACT_DIR not in sys.path:
    sys.path.insert(0, _CONTRACT_DIR)


def _load_app_export():
    mod = sys.modules.get("app_export")
    if mod is not None and hasattr(mod, "build_bundle"):
        return mod
    spec = importlib.util.spec_from_file_location("app_export", os.path.join(REPO_ROOT, "scripts", "app_export.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["app_export"] = mod
    return mod


app_export = _load_app_export()

from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR  # noqa: E402

from edge_finder_contract import build, ids, research as R, timeutil  # noqa: E402

SPORT = app_export.SPORT
EVENT_SOURCE = app_export.EVENT_SOURCE
TEAM_SOURCE = app_export.TEAM_SOURCE
PLAYER_SOURCE = "mlbam_player_id"
RESEARCH_EXPORT_VERSION = "mlb-research-export/1.0.0"
PROP_FAMILY_PREFIXES = ("hitter_", "pitcher_")
EVIDENCE_CAP = 5
AUDIT_DATE = "2026-10-03"
ABBR_NORMALIZE = {"ARI": "AZ", "OAK": "ATH"}  # scripts/enrich_data.py:72, the repository's own table
TEAM_GAME_CAP = 162
MARKET_HISTORY_BUDGET = 380_000
PROFILE_BUDGET = 145_000  # bytes; the contract budget per profile / event document is 150 KB  # bytes; the contract budget per market_history document is 400 KB
PLAYER_GAME_CAP = 40
ROLLING_N = 10

SWINGS = {"foul", "hit_into_play", "swinging_strike", "swinging_strike_blocked", "foul_tip", "foul_bunt", "missed_bunt"}
WHIFFS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}

# Limitations quoted from the audit (scratchpad/phase2/audit_mlb.md, sections 4, 6, 10).
LIM = {
    "research_cache": "One-off research cache (manual research-multiseason-*.yml workflows), not refreshed on a cadence; "
                      "the 2026 season stops in late August (2,004 games).",
    "snapshot": "Current snapshot only: the file is overwritten every fetch; history exists only inside the daily slates.",
    "wrc_proxy": "wRC+ is a proxy (wrcSource='ops_proxy', rpgIndex = RpG/4.5*100, enrich_data.py:87).",
    "form": "Explicitly analysis-only (docs/RESEARCH_OFFENSIVE_FORM.md): no production model weight reads team_offense_form.json; 30-day lookback.",
    "opp_adj": "Simple, not recursive: rolling-15-game opposing-starter xERA, (avg-4.00)*0.08 capped +/-0.2; not applied to "
               "pitching/defense; magnitude <= 0.2 R/G; xERA/xFIP naming inconsistency; no park or handedness interaction.",
    "player_snapshot": "Player season stats are current-snapshot only (overwritten daily); vsLHH/vsRHH/velocity* null in sampled "
                       "game; per-season history only recoverable from slate copies.",
    "player_logs": "No per-game batter box lines in production; derive from Statcast pitches.",
    "statcast": "Only 48 game-days of Statcast (2026-08-11 -> 2026-09-27); published only for players on the current slate or "
                "in current markets.",
    "market_history": "snapshot series, median 3 points (median 3 obs/ticker, p90 5, max 17; ~6 distinct capture minutes per day).",
    "model_evals": "modelFairProbability non-null on 12% of model_evaluations rows; evals/ticker median 1, max 12.",
    "lineups": "The slate stores lineup status/confirmation only; batting orders are not published (audit: UNAVAILABLE in slate); "
               "handedness often unknown.",
    "matchup": "Platoon context status is often MISSING_DATA (handedness countUnknown); pitcherSavant vsLHH/vsRHH null in sample.",
    "usage": "bullpenUsageAvailableCount: 0 on off-days; usage = relievers used last game / back-to-back from bullpen.json recentUsage "
             "and starter pitches/outs from the research cache.",
}


class ResearchExportError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_jsonl_gz(path):
    if not os.path.exists(path):
        return []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _norm_abbr(abbr):
    a = str(abbr or "").strip().upper()
    return ABBR_NORMALIZE.get(a, a)


def _day(d):
    return f"{d}T00:00:00Z"


def _r(v, nd=4):
    return None if v is None else round(float(v), nd)


def _div(a, b, nd=4):
    return None if not b else round(float(a) / float(b), nd)


def _mean(vals, nd=4):
    vals = [float(v) for v in vals if v is not None]
    return round(statistics.fmean(vals), nd) if vals else None


def _in_range(d, rng):
    if not rng:
        return True
    start, end = rng
    return (start is None or d >= start) and (end is None or d <= end)


def _name_key(name):
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().replace(".", " ").split())


def is_prop_market(m):
    return str(m.get("market_family") or "").startswith(PROP_FAMILY_PREFIXES)


def team_pid(abbr):
    return ids.participant_id(SPORT, "TEAM", TEAM_SOURCE, _norm_abbr(abbr))


def player_pid(player_id):
    return ids.participant_id(SPORT, "PLAYER", PLAYER_SOURCE, str(player_id))


def event_pid(game_pk):
    return ids.event_id(SPORT, EVENT_SOURCE, str(game_pk))


def mid(slug):
    return ids.metric_id(SPORT, slug)


def sample_tier(n):
    """lib/edgelab/calibration.py sample-size tiers."""
    if n is None:
        return None
    return "INSUFFICIENT_N_LT_20" if n < 20 else "LIMITED_20_TO_99" if n < 100 else "ADEQUATE_N_GE_100"


# ---------------------------------------------------------------------------
# loaders (all reads happen here; build_explorer is pure)
# ---------------------------------------------------------------------------

def load_v1(app_root):
    def items(name):
        doc = _read_json(os.path.join(app_root, f"{name}.json"))
        return list(doc["items"]) if doc else []
    manifest = _read_json(os.path.join(app_root, "manifest.json"))
    if not manifest:
        raise ResearchExportError(f"no v1 manifest.json under {app_root}: run scripts/app_export.py first")
    runs = items("runs")
    return {"manifest": manifest, "events": items("events"), "markets": items("markets"),
            "model_prices": items("model_prices"), "recommendations": items("recommendations"),
            "wagers": items("wagers"), "settlements": items("settlements"),
            "slate_date": ((runs[0].get("source_ids") or {}).get("slate_date") if runs else None)}


def load_research_cache(data_root, seasons=None):
    """{season: {"games": {pk: game}, "batting": {pk: row}, "pitching": {pk: row}}} for completed regular-season games."""
    base = os.path.join(data_root, "research_cache")
    out = {}
    sched_root = os.path.join(base, "bullpen_backtest")
    if not os.path.isdir(sched_root):
        return out
    for yr in sorted(os.listdir(sched_root)):
        if not yr.isdigit() or (seasons and yr not in seasons):
            continue
        games = {}
        folder = os.path.join(sched_root, yr, "schedules")
        for fn in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            doc = _read_json(os.path.join(folder, fn)) or {}
            for day in doc.get("dates") or []:
                for g in day.get("games") or []:
                    st = (g.get("status") or {}).get("abstractGameState")
                    away, home = (g.get("teams") or {}).get("away") or {}, (g.get("teams") or {}).get("home") or {}
                    if g.get("gameType") != "R" or st != "Final" or away.get("score") is None or home.get("score") is None:
                        continue
                    a_id, h_id = (away.get("team") or {}).get("id"), (home.get("team") or {}).get("id")
                    if a_id not in TEAM_ID_TO_ABBR or h_id not in TEAM_ID_TO_ABBR:
                        continue
                    pk = int(g["gamePk"])
                    games[pk] = {
                        "pk": pk, "season": yr, "date": g.get("officialDate") or day.get("date"), "start": g.get("gameDate"),
                        "away": TEAM_ID_TO_ABBR[a_id], "home": TEAM_ID_TO_ABBR[h_id],
                        "away_name": (away.get("team") or {}).get("name"), "home_name": (home.get("team") or {}).get("name"),
                        "away_score": int(away["score"]), "home_score": int(home["score"]),
                        "venue": (g.get("venue") or {}).get("name"), "day_night": g.get("dayNight"),
                        "doubleheader": g.get("doubleHeader"), "game_number": g.get("gameNumber"),
                    }
        batting = {int(r["gamePk"]): r for r in _read_jsonl_gz(os.path.join(base, "batting_backtest", yr, "boxscores.jsonl.gz"))}
        pitching = {int(r["gamePk"]): r for r in _read_jsonl_gz(os.path.join(base, "starter_workload", yr, "boxscores.jsonl.gz"))}
        out[yr] = {"games": games, "batting": batting, "pitching": pitching}
    return out


def load_slates(data_root, slate_range=None):
    folder = os.path.join(data_root, "slates")
    out = []
    for d in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not app_export._DATE_RE.match(d) or not _in_range(d, slate_range):
            continue
        doc = _read_json(os.path.join(folder, d, "authoritative.json"))
        if doc and doc.get("date") == d:
            out.append((d, doc))
    return out


def load_statcast(data_root, player_ids, statcast_range=None):
    """Raw pitch rows for the given MLBAM ids within the date range, grouped by game; plus the
    index's measured span. Only the game files those players appear in are read."""
    base = os.path.join(data_root, "statcast_raw")
    wanted = {str(p) for p in player_ids}
    span = []
    game_pks = set()
    for name, key in (("batter_games.jsonl", "batterId"), ("pitcher_games.jsonl", "pitcherId")):
        for row in _read_jsonl_gz(os.path.join(base, "index", name)):
            span.append(row["gameDate"])
            if _in_range(row["gameDate"], statcast_range) and str(row.get(key)) in wanted:
                game_pks.add(int(row["gamePk"]))
    rows = []
    for pk in sorted(game_pks):
        for r in _read_jsonl_gz(os.path.join(base, "games", f"{pk}.jsonl")):
            if str(r.get("batterId")) in wanted or str(r.get("pitcherId")) in wanted:
                rows.append(r)
    return {"rows": rows, "index_start": min(span) if span else None, "index_end": max(span) if span else None,
            "range": list(statcast_range) if statcast_range else None}


def load_inputs(data_root, app_root, *, slate_range=None, seasons=None, statcast_range=None):
    """Everything build_explorer needs. ``slate_range`` (start, end) dates, ``seasons`` iterable of
    year strings and ``statcast_range`` (start, end) bound the reads (tests); None reads everything."""
    v1 = load_v1(app_root)
    date = v1["slate_date"]
    slates = load_slates(data_root, slate_range)
    if date and all(d != date for d, _ in slates):
        export_slate = _read_json(os.path.join(data_root, "slates", date, "authoritative.json"))
        if export_slate and export_slate.get("date") == date:
            slates.append((date, export_slate))
            slates.sort(key=lambda x: x[0])
    rc = load_research_cache(data_root, set(seasons) if seasons else None)
    inputs = {
        "v1": v1, "date": date, "slates": slates, "research_cache": rc,
        "offense_form": _read_json(os.path.join(data_root, "team_offense_form.json")),
        "bullpen": _read_json(os.path.join(data_root, "bullpen.json")),
        "savant": _read_json(os.path.join(data_root, "savant_team.json")),
        "oppquality": _read_json(os.path.join(data_root, "oppquality.json")),
        "calibration_bins": _read_json(os.path.join(data_root, "research", "calibration_bins.json")),
        "postmortem_dates": sorted(d for d in os.listdir(os.path.join(data_root, "edgelab", "postmortems"))
                                   if app_export._DATE_RE.match(d)) if os.path.isdir(os.path.join(data_root, "edgelab", "postmortems")) else [],
        "observations": app_export._partition(data_root, "observations", date) if date else [],
        "clv_quotes": app_export._partition(data_root, "clv_quotes", date) if date else [],
        "model_evaluations": app_export._partition(data_root, "model_evaluations", date) if date else [],
        "edgelab_markets": app_export._partition(data_root, "markets", date) if date else [],
    }
    universe = player_universe(inputs)
    inputs["statcast"] = load_statcast(data_root, sorted(universe), statcast_range)
    return inputs


# ---------------------------------------------------------------------------
# identity: teams and the bounded player universe
# ---------------------------------------------------------------------------

def _slate_players(slate):
    """(player_id, name, team_abbr, role, extra) for starters and confirmed-lineup batters of one slate."""
    out = []
    for g in slate.get("games") or []:
        for side in ("away", "home"):
            block = g.get(side) or {}
            abbr = _norm_abbr(block.get("abbr"))
            p = block.get("pitcher") or {}
            if p.get("id") and p.get("name"):
                out.append((str(p["id"]), p["name"], abbr, "starting pitcher", {"throws": p.get("pitchHand"), "game_pk": g.get("gameId")}))
            for b in ((g.get(f"{side}TeamStats") or {}).get("confirmedLineup") or []):
                if b.get("playerId") and b.get("name"):
                    out.append((str(b["playerId"]), b["name"], abbr, "confirmed lineup",
                                {"position": b.get("position"), "game_pk": g.get("gameId")}))
    return out


def _name_index(inputs):
    """(team, name key) -> player id when exactly one id carries that name on that team, from every slate
    read and every research-cache pitcher line. Exact (accent/case-folded) names only, never fuzzy."""
    seen = {}
    for _d, slate in inputs["slates"]:
        for pid, name, abbr, _role, _x in _slate_players(slate):
            seen.setdefault((abbr, _name_key(name)), set()).add(pid)
    for yr in sorted(inputs["research_cache"]):
        season = inputs["research_cache"][yr]
        for pk, row in season["pitching"].items():
            g = season["games"].get(pk)
            if not g:
                continue
            for side in ("away", "home"):
                for p in row.get(f"{side}Pitchers") or []:
                    if p.get("playerId") and p.get("name"):
                        seen.setdefault((g[side], _name_key(p["name"])), set()).add(str(p["playerId"]))
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def player_universe(inputs):
    """{player_id: {name, team, roles, extra}}: starters and confirmed-lineup batters of the export
    slate, plus players named by a current v1 market whose (team, name) resolves exactly."""
    out = {}
    slate = next((s for d, s in inputs["slates"] if d == inputs["date"]), None)
    for pid, name, abbr, role, extra in _slate_players(slate or {}):
        cur = out.setdefault(pid, {"name": name, "team": abbr, "roles": set(), "extra": {}, "market_tickers": set()})
        cur["roles"].add(role)
        cur["extra"].update({k: v for k, v in extra.items() if v is not None})
    names = _name_index(inputs)
    unresolved = set()
    for m in inputs["v1"]["markets"]:
        ext = m.get("extensions") or {}
        if not ext.get("player"):
            continue
        key = (_norm_abbr(ext.get("team")), _name_key(ext["player"]))
        pid = names.get(key)
        if pid is None:
            unresolved.add(key)
            continue
        cur = out.setdefault(pid, {"name": ext["player"], "team": key[0], "roles": set(), "extra": {}, "market_tickers": set()})
        cur["roles"].add("current market")
        cur["market_tickers"].add(m["kalshi_ticker"])
    inputs["_unresolved_market_players"] = sorted(unresolved)
    return out


# ---------------------------------------------------------------------------
# the metric catalogue (registered only when published)
# ---------------------------------------------------------------------------

def _metric_specs():
    T, P, M = "TEAM", "PLAYER", "MARKET"
    rc, snap, slate, form = "research_cache", "snapshot", "slate", "form"
    S = {}

    def add(slug, name, short, desc, etype, cat, stype, unit, hib, src, *, sub=None, lims=()):
        S[slug] = dict(slug=slug, name=name, short=short, desc=desc, etype=etype, cat=cat, stype=stype, unit=unit, hib=hib,
                       src=src, sub=sub, lims=list(lims))

    # team season aggregates (research cache, joined on gamePk)
    add("win_pct", "Win percentage", "W%", "Wins / games over completed regular-season games (MLB Stats API schedules, research cache).",
        T, "results", "PERCENT", "fraction", True, rc)
    add("runs_per_game", "Runs scored per game", "R/G", "Runs scored / games (schedule final scores).", T, "offense", "RATE", "runs/game", True, rc)
    add("runs_allowed_per_game", "Runs allowed per game", "RA/G", "Runs allowed / games (schedule final scores).", T, "pitching", "RATE", "runs/game", False, rc)
    add("run_diff_per_game", "Run differential per game", "RD/G", "(Runs scored - runs allowed) / games.", T, "results", "RATE", "runs/game", True, rc)
    add("ops", "OPS", "OPS", "OBP + SLG from summed team batting box lines: OBP=(H+BB+HBP)/(AB+BB+HBP+SF), SLG=(1B+2*2B+3*3B+4*HR)/AB.",
        T, "offense", "RATE", None, True, rc)
    add("obp", "On-base percentage", "OBP", "(H+BB+HBP)/(AB+BB+HBP+SF) from summed team batting box lines.", T, "offense", "RATE", None, True, rc)
    add("batting_k_pct", "Batting strikeout rate", "K%", "Strikeouts / plate appearances (team batting box lines).", T, "offense", "PERCENT", "fraction", False, rc)
    add("batting_bb_pct", "Batting walk rate", "BB%", "Walks / plate appearances (team batting box lines).", T, "offense", "PERCENT", "fraction", True, rc)
    add("hr_per_game", "Home runs per game", "HR/G", "Home runs / games (team batting box lines).", T, "offense", "RATE", "HR/game", True, rc)
    add("pitching_k_pct", "Pitching strikeout rate", "P-K%", "Strikeouts / batters faced over every pitcher line (starter_workload cache).",
        T, "pitching", "PERCENT", "fraction", True, rc)
    add("pitching_bb_pct", "Pitching walk rate", "P-BB%", "Walks / batters faced over every pitcher line (starter_workload cache).",
        T, "pitching", "PERCENT", "fraction", False, rc)
    add("starter_outs_per_start", "Starter outs per start", "SP outs", "Mean outs recorded by the first pitcher (orderIndex 0) per game.",
        T, "usage", "RATE", "outs/start", True, rc)
    add("starter_pitches_per_start", "Starter pitches per start", "SP pitches", "Mean pitches thrown by the first pitcher (orderIndex 0) per game.",
        T, "usage", "RATE", "pitches/start", None, rc)
    add("runs_scored", "Runs scored (game)", "R", "Runs scored in one game (schedule final score); rolling = trailing 10-game mean.",
        T, "offense", "SCORE", "runs", True, rc)
    add("runs_allowed", "Runs allowed (game)", "RA", "Runs allowed in one game (schedule final score); rolling = trailing 10-game mean.",
        T, "pitching", "SCORE", "runs", False, rc)
    # current snapshots
    add("bullpen_xfip", "Bullpen xFIP", "BP xFIP", "Season bullpen xFIP (data/bullpen.json; embedded per game in the slate).",
        T, "pitching", "RATE", "runs/9", False, snap, sub="bullpen")
    add("bullpen_era", "Bullpen ERA", "BP ERA", "Season bullpen ERA (data/bullpen.json; embedded per game in the slate).",
        T, "pitching", "RATE", "runs/9", False, snap, sub="bullpen")
    add("team_xwoba", "Team xwOBA", "xwOBA", "Season team expected wOBA (Baseball Savant, data/savant_team.json teams).", T, "offense", "RATE", None, True, snap)
    add("opp_quality_adj", "Opponent-quality adjustment", "Opp adj",
        "Runs/game added to the offense baseline for the quality of opposing starters faced: rolling 15 completed games in 21 days, "
        "(mean opposing-starter xERA - 4.00) * 0.08, capped +/-0.2 (scripts/fetch_opp_quality.py). Positive = faced tougher starters.",
        T, "adjustment", "RATE", "runs/game", None, snap, lims=[LIM["opp_adj"]])
    add("opp_starter_xera_avg", "Opposing starters' mean xERA", "Opp SP xERA",
        "Mean Savant xERA (field named oppXFIPavg) of opposing starters over the rolling window (scripts/fetch_opp_quality.py).",
        T, "adjustment", "RATE", "runs/9", None, snap, lims=[LIM["opp_adj"]])
    add("park_factor", "Park factor", "PF", "Static run park factor of the team's home park as stored per game in the slate (100 = neutral).",
        T, "venue", "INDEX", None, None, slate)
    # recent form (analysis-only)
    add("form_runs_mean", "Recent runs per game", "Form R/G", "Mean runs scored over the last N completed games (data/team_offense_form.json windows). Analysis-only.",
        T, "form", "RATE", "runs/game", True, form, lims=[LIM["form"]])
    add("form_runs_median", "Recent median runs", "Form med", "Median runs scored over the last N completed games. Analysis-only.",
        T, "form", "RATE", "runs", True, form, lims=[LIM["form"]])
    add("form_overs_pct", "Recent team-total overs", "TT overs%",
        "Share of the last N games with a stored Kalshi team-total line where runs scored exceeded the line (marketRelative.oversPct). Analysis-only.",
        T, "form", "PERCENT", "fraction", None, form, lims=[LIM["form"]])
    # per-game slate model inputs (event matchup rows, opponent-adjustment history)
    add("slate_season_rpg", "Season runs per game (slate)", "R/G", "awayTeamStats/homeTeamStats.seasonRpG as stored in the day's authoritative slate.",
        T, "offense", "RATE", "runs/game", True, slate)
    add("slate_last7_rpg", "Last-7 runs per game (slate)", "L7 R/G", "last7RpG as stored in the slate (schedule linescores).", T, "form", "RATE", "runs/game", True, slate)
    add("slate_last15_rpg", "Last-15 runs per game (slate)", "L15 R/G", "last15RpG as stored in the slate.", T, "form", "RATE", "runs/game", True, slate)
    add("wrc_plus_proxy", "wRC+ (proxy)", "wRC+*", "wrcPlus as stored in the slate; an OPS/RpG proxy, not true wRC+.", T, "offense", "INDEX", None, True, slate,
        lims=[LIM["wrc_proxy"]])
    add("offense_baseline_adj", "Offense baseline (adjusted)", "Off base",
        "offenseBaselineAdj = Bayes-shrunk 0.3*L7+0.3*L15+0.4*season RpG + opponent-quality adj (+ lineup adj when confirmed); scripts/enrich_data.py.",
        T, "projection_input", "RATE", "runs/game", True, slate)
    add("projected_runs", "Projected runs", "Proj R", "The model's projected runs for the side (marketLedger awayProjRuns/homeProjRuns; scripts/compute_projections).",
        T, "projection", "RATE", "runs", None, slate)
    add("model_win_probability", "Model win probability", "Model W%", "The slate model's full-game win probability for the side (modelProb.away/home, percent).",
        T, "projection", "PROBABILITY", "%", None, slate)
    add("starter_xfip", "Starter xFIP (slate)", "SP xFIP", "pitcherSavant.xFIP of the probable starter as stored in the slate.", P, "pitching", "RATE", "runs/9", False, slate)
    add("starter_xera", "Starter xERA (slate)", "SP xERA", "pitcherSavant.xERA of the probable starter as stored in the slate.", P, "pitching", "RATE", "runs/9", False, slate)
    add("starter_k_pct", "Starter K% (slate)", "SP K%", "pitcherSavant.kPct (percent) of the probable starter as stored in the slate.", P, "pitching", "PERCENT", "%", True, slate)
    add("starter_bb_pct", "Starter BB% (slate)", "SP BB%", "pitcherSavant.bbPct (percent) of the probable starter as stored in the slate.", P, "pitching", "PERCENT", "%", False, slate)
    add("starter_recent_fip", "Starter recent FIP (slate)", "SP rFIP", "pitcherSavant.recentFIP (last sampled starts) as stored in the slate.", P, "pitching", "RATE", "runs/9", False, slate)
    add("starter_avg_ip", "Starter innings per start (slate)", "SP IP/GS", "pitcherSavant.avgIPperStart as stored in the slate.", P, "usage", "RATE", "innings", True, slate)
    # player snapshots (Savant)
    for slug, name, short, field, stype, unit, hib in (
            ("batter_xwoba", "Batter xwOBA", "xwOBA", "xwoba", "RATE", None, True),
            ("batter_k_pct", "Batter K%", "K%", "kPct", "PERCENT", "%", False),
            ("batter_bb_pct", "Batter BB%", "BB%", "bbPct", "PERCENT", "%", True),
            ("batter_hard_hit_pct", "Batter hard-hit%", "HH%", "hardHitPct", "PERCENT", "%", True),
            ("batter_barrel_pct", "Batter barrel%", "Brl%", "barrelPct", "PERCENT", "%", True),
            ("batter_exit_velo", "Batter average exit velocity", "EV", "exitVeloAvg", "RATE", "mph", True),
            ("pitcher_xera", "Pitcher xERA", "xERA", "xera", "RATE", "runs/9", False),
            ("pitcher_k_pct", "Pitcher K%", "K%", "kPct", "PERCENT", "%", True),
            ("pitcher_bb_pct", "Pitcher BB%", "BB%", "bbPct", "PERCENT", "%", False)):
        add(slug, name, short, f"Season {field} from Baseball Savant as stored in data/savant_team.json (current snapshot).",
            P, "season", stype, unit, hib, "player_snapshot", lims=[LIM["player_snapshot"]])
    # pitcher appearance logs (research cache)
    add("pitcher_outs", "Outs recorded (appearance)", "Outs", "Outs recorded in one appearance (starter_workload research cache).", P, "game_log", "COUNT", "outs", True,
        "player_logs", lims=[LIM["player_logs"]])
    add("pitcher_pitches", "Pitches thrown (appearance)", "Pitches", "Pitches thrown in one appearance (starter_workload research cache).", P, "usage", "COUNT", "pitches", None,
        "player_logs", lims=[LIM["player_logs"]])
    # Statcast aggregates
    add("sc_pitches", "Pitches thrown (Statcast)", "Pitches", "Count of Statcast pitch rows thrown by the pitcher.", P, "statcast", "COUNT", "pitches", None, "statcast",
        lims=[LIM["statcast"]])
    add("sc_velocity", "Average pitch velocity", "Velo", "Mean releaseSpeed over the pitcher's Statcast pitch rows (all pitch types).", P, "statcast", "RATE", "mph", True,
        "statcast", lims=[LIM["statcast"]])
    add("sc_whiff_pct", "Whiff rate", "Whiff%", "Swinging strikes / swings on the pitcher's Statcast rows (swing = foul, foul tip, in play, swinging strike, bunt attempts).",
        P, "statcast", "PERCENT", "fraction", True, "statcast", lims=[LIM["statcast"]])
    add("sc_xwoba_allowed", "xwOBA allowed", "xwOBA-A", "Mean per-PA estimatedWOBA (wobaValue when no estimate) over plate appearances the pitcher ended; simple mean, not the official denominator.",
        P, "statcast", "RATE", None, False, "statcast", lims=[LIM["statcast"]])
    add("sc_batter_xwoba", "Batter xwOBA (Statcast)", "xwOBA", "Mean per-PA estimatedWOBA (wobaValue when no estimate) over the batter's plate appearances; simple mean, not the official denominator.",
        P, "statcast", "RATE", None, True, "statcast", lims=[LIM["statcast"]])
    add("sc_exit_velo", "Average exit velocity", "EV", "Mean launchSpeed over the batter's batted balls (Statcast).", P, "statcast", "RATE", "mph", True, "statcast",
        lims=[LIM["statcast"]])
    add("sc_launch_angle", "Average launch angle", "LA", "Mean launchAngle over the batter's batted balls (Statcast).", P, "statcast", "RATE", "degrees", None, "statcast",
        lims=[LIM["statcast"]])
    add("sc_batter_pa", "Plate appearances (Statcast)", "PA", "PA-ending pitch rows (events set, truncated_pa excluded) for the batter.", P, "statcast", "COUNT", "PA", None,
        "statcast", lims=[LIM["statcast"]])
    # market-level
    add("model_fair_probability", "Model fair probability P(YES)", "Fair P", "edgelab/model_evaluations modelFairProbability oriented to the contract's YES side "
        "(lib/edgelab/decision_side.resolve_side, as the v1 export does), per evaluation run.", M, "projection", "PROBABILITY", "probability", None, "model_evals",
        lims=[LIM["model_evals"]])
    add("wager_clv", "Wager CLV (REAL)", "CLV", "Closing-line value of REAL / REAL_PROBE wagers, the ledger's own clv in percentage points (POSITIVE_IS_GOOD_V1); "
        "aggregates by market family in extensions; the wagers themselves are in the v1 wagers.json / performance.json.",
        M, "wagers", "RATE", "pct points", True, "wagers")
    add("wager_win_rate", "Wager win rate (REAL)", "Win%", "Settled REAL / REAL_PROBE wagers won / (won + lost), by market family in extensions; "
        "postmortem inventory (edgelab/postmortems) listed alongside.", M, "wagers", "PERCENT", "fraction", True, "wagers")
    add("model_calibration_error", "Model calibration error (REAL wagers)", "Cal err", "Per 10-point probability bin: actual win rate - mean predicted probability over "
        "REAL bankroll-counting wagers (data/research/calibration_bins.json, scripts/build_wager_research_db.py); bins in extensions.",
        M, "calibration", "PERCENT", "pct points", None, "calibration")
    return S


# ---------------------------------------------------------------------------
# the builder
# ---------------------------------------------------------------------------

class _Ctx:
    def __init__(self, inputs, run_id, generated_at):
        self.inputs = inputs
        self.run_id = run_id
        self.gen = timeutil.to_iso(generated_at)
        self.specs = _metric_specs()
        self.used = {}  # metric slug -> {"windows": set, "splits": set, "ranked": bool, "series": bool, "observed": bool}
        self.docs = []
        self.qualities = {}

    def mark(self, slug, *, window=None, split=None, ranked=False, series=False):
        u = self.used.setdefault(slug, {"windows": set(), "splits": set(), "ranked": False, "series": False})
        if window:
            u["windows"].add(window)
        if split:
            u["splits"].add(split)
        u["ranked"] = u["ranked"] or ranked
        u["series"] = u["series"] or series


def _quality(ctx, key, *, as_of=None, coverage=None, sample=None):
    gen = ctx.gen
    table = {
        "research_cache": ("VERIFIED", "MLB Stats API research cache (data/research_cache)", False, [LIM["research_cache"]]),
        "snapshot": ("VERIFIED", "daily snapshots data/bullpen.json, data/savant_team.json, data/oppquality.json", True, [LIM["snapshot"]]),
        "slate": ("VERIFIED", "data/slates/<date>/authoritative.json", True, []),
        "form": ("VERIFIED", "data/team_offense_form.json (scripts/fetch_team_offense_form.py)", True, [LIM["form"]]),
        "player_snapshot": ("PARTIAL", "data/savant_team.json + slate pitcherSavant", True, [LIM["player_snapshot"]]),
        "player_logs": ("PARTIAL", "data/research_cache/starter_workload", False, [LIM["player_logs"], LIM["research_cache"]]),
        "statcast": ("PARTIAL", "data/statcast_raw (statcast-postgame-archive.yml)", True, [LIM["statcast"]]),
        "model_evals": ("PARTIAL", "data/edgelab/model_evaluations", True, [LIM["model_evals"]]),
        "market_history": ("PARTIAL", "data/edgelab/observations + data/edgelab/clv_quotes", True, [LIM["market_history"]]),
        "wagers": ("VERIFIED", "data/edgelab/bets/bets.jsonl via the v1 wagers.json (REAL / REAL_PROBE only)", True, []),
        "calibration": ("VERIFIED", "data/research/calibration_bins.json (REAL bankroll-counting wagers)", True,
                        ["Descriptive only; small bins (n<20) are not evidence; calibration factors in config/rules.json are static since 2026-06-07."]),
        "profile": ("VERIFIED", "edge-finder-api committed data (see metric sources)", True, []),
        "event": ("VERIFIED", "data/slates/<date>/authoritative.json + the v1 publication", True,
                  ["No injuries, weather values or umpires are stored; batting orders are not published."]),
    }
    status, source, production, lims = table[key]
    return R.quality(status=status, source=source, generated_at=gen, production=production, data_as_of=as_of,
                     methodology_version=RESEARCH_EXPORT_VERSION, coverage=coverage, sample_size=sample, limitations=lims)


def _obs(ctx, slug, entity_id, etype, value, window, as_of, *, source=None, status=None, context=None, sample=None, season=None,
         event_id=None, opponent_id=None, split=None, display=None, ext=None):
    spec = ctx.specs[slug]
    q = {"research_cache": "VERIFIED", "snapshot": "VERIFIED", "slate": "VERIFIED", "form": "VERIFIED", "player_snapshot": "PARTIAL",
         "player_logs": "PARTIAL", "statcast": "PARTIAL", "model_evals": "PARTIAL"}[spec["src"]]
    ctx.mark(slug, window=window["label"], split=split["dimension"] if split else None)
    return R.observation(sport=SPORT, metric_id=mid(slug), entity_id=entity_id, entity_type=etype, value=value, window=window,
                         as_of=as_of, source=source or spec["src"], quality_status=status or q, unit=spec["unit"], context=context,
                         sample_size=sample, season=season, event_id=event_id, opponent_id=opponent_id, split=split,
                         display_value=display, extensions=ext)


# ---------------------------------------------------------------------- teams

def _team_universe(inputs):
    """abbr -> participant (v1 event participants verbatim, else built with the v1 call)."""
    names = {}
    for yr in sorted(inputs["research_cache"]):
        for g in inputs["research_cache"][yr]["games"].values():
            names[g["away"]] = g["away_name"] or names.get(g["away"])
            names[g["home"]] = g["home_name"] or names.get(g["home"])
    for _d, slate in inputs["slates"]:
        for g in slate.get("games") or []:
            for side in ("away", "home"):
                b = g.get(side) or {}
                if b.get("abbr") and b.get("team"):
                    names[_norm_abbr(b["abbr"])] = b["team"]
    out = {abbr: app_export._team(abbr, names.get(abbr)) for abbr in sorted(set(TEAM_ID_TO_ABBR.values()) | set(names))}
    for ev in inputs["v1"]["events"]:
        for p in ev["participants"]:
            abbr = (p.get("source_ids") or {}).get(TEAM_SOURCE)
            if abbr:
                out[_norm_abbr(abbr)] = p
    return out


def _season_aggregates(season):
    """{abbr: {...sums...}} and per-team game lists for one research-cache season."""
    agg, logs = {}, {}
    for pk in sorted(season["games"], key=lambda k: (season["games"][k]["start"] or "", k)):
        g = season["games"][pk]
        bat = season["batting"].get(pk) or {}
        pit = season["pitching"].get(pk) or {}
        for side, opp in (("away", "home"), ("home", "away")):
            abbr = g[side]
            a = agg.setdefault(abbr, {"G": 0, "W": 0, "RS": 0, "RA": 0, "box_g": 0, "PA": 0, "AB": 0, "H": 0, "2B": 0, "3B": 0, "HR": 0,
                                      "BB": 0, "HBP": 0, "K": 0, "SF": 0, "pit_g": 0, "BF": 0, "PK": 0, "PBB": 0, "sp_outs": 0, "sp_pitches": 0,
                                      "home": {"G": 0, "W": 0, "RS": 0}, "away": {"G": 0, "W": 0, "RS": 0}})
            rs, ra = g[f"{side}_score"], g[f"{opp}_score"]
            a["G"] += 1
            a["W"] += 1 if rs > ra else 0
            a["RS"] += rs
            a["RA"] += ra
            a[side]["G"] += 1
            a[side]["W"] += 1 if rs > ra else 0
            a[side]["RS"] += rs
            b = bat.get(f"{side}Batting")
            if b:
                a["box_g"] += 1
                for k, f in (("PA", "plateAppearances"), ("AB", "atBats"), ("H", "hits"), ("2B", "doubles"), ("3B", "triples"),
                             ("HR", "homeRuns"), ("BB", "baseOnBalls"), ("HBP", "hitByPitch"), ("K", "strikeOuts"), ("SF", "sacFlies")):
                    a[k] += int(b.get(f) or 0)
            plist = pit.get(f"{side}Pitchers") or []
            if plist:
                a["pit_g"] += 1
                for p in plist:
                    a["BF"] += int(p.get("battersFaced") or 0)
                    a["PK"] += int(p.get("strikeOuts") or 0)
                    a["PBB"] += int(p.get("baseOnBalls") or 0)
                sp = min(plist, key=lambda p: p.get("orderIndex", 99))
                a["sp_outs"] += int(sp.get("outs") or 0)
                a["sp_pitches"] += int(sp.get("numberOfPitches") or 0)
            logs.setdefault(abbr, []).append({"pk": pk, "season": g["season"], "date": g["date"], "start": g["start"], "side": side,
                                              "opp": g[opp], "opp_name": g[f"{opp}_name"], "rs": rs, "ra": ra, "venue": g["venue"]})
    return agg, logs


def _season_values(a):
    single = a["H"] - a["2B"] - a["3B"] - a["HR"]
    obp = _div(a["H"] + a["BB"] + a["HBP"], a["AB"] + a["BB"] + a["HBP"] + a["SF"])
    slg = _div(single + 2 * a["2B"] + 3 * a["3B"] + 4 * a["HR"], a["AB"])
    return {
        "win_pct": (_div(a["W"], a["G"]), a["G"]), "runs_per_game": (_div(a["RS"], a["G"]), a["G"]),
        "runs_allowed_per_game": (_div(a["RA"], a["G"]), a["G"]), "run_diff_per_game": (_div(a["RS"] - a["RA"], a["G"]), a["G"]),
        "ops": (None if obp is None or slg is None else round(obp + slg, 4), a["box_g"]), "obp": (obp, a["box_g"]),
        "batting_k_pct": (_div(a["K"], a["PA"]), a["box_g"]), "batting_bb_pct": (_div(a["BB"], a["PA"]), a["box_g"]),
        "hr_per_game": (_div(a["HR"], a["box_g"]), a["box_g"]),
        "pitching_k_pct": (_div(a["PK"], a["BF"]), a["pit_g"]), "pitching_bb_pct": (_div(a["PBB"], a["BF"]), a["pit_g"]),
        "starter_outs_per_start": (_div(a["sp_outs"], a["pit_g"], 3), a["pit_g"]),
        "starter_pitches_per_start": (_div(a["sp_pitches"], a["pit_g"], 2), a["pit_g"]),
    }


SEASON_METRICS = ("win_pct", "runs_per_game", "runs_allowed_per_game", "run_diff_per_game", "ops", "obp", "batting_k_pct",
                  "batting_bb_pct", "hr_per_game", "pitching_k_pct", "pitching_bb_pct", "starter_outs_per_start", "starter_pitches_per_start")
PRIOR_SEASON_METRICS = ("win_pct", "runs_per_game", "runs_allowed_per_game", "ops", "pitching_k_pct")


def _ranking(ctx, slug, universe_label, window, values, as_of, quality, *, season=None, filt=None, etype="TEAM", path_for=None):
    spec = ctx.specs[slug]
    rows = [dict(r) for r in values if r.get("value") is not None]
    if len(rows) < 2:
        return None
    rk = R.ranking(sport=SPORT, metric_id=mid(slug), universe_label=universe_label, entity_type=etype, window=window, as_of=as_of,
                   higher_is_better=spec["hib"], values=rows, run_id=ctx.run_id, generated_at=ctx.gen, quality=quality, season=season,
                   universe_filter=filt, path_for=path_for or R.team_path,
                   links=[R.link(rel="METRIC", target_kind="metric_registry", label=spec["name"], target_id=mid(slug), path=R.app_path(R.METRICS_NAME))])
    ctx.mark(slug, window=window["label"], ranked=True)
    ctx.docs.append(rk)
    return rk


def build_team_layer(ctx, teams, published_events):
    """Rankings, observations, series and game logs per team; returns {abbr: profile parts}."""
    inputs = ctx.inputs
    parts = {abbr: {"metrics": [], "splits": {}, "series": [], "rankings": [], "games": [], "opponents": {}, "ext": {}} for abbr in teams}
    rc = inputs["research_cache"]
    seasons = sorted(rc)
    all_logs = {}
    aggs = {}
    latest = seasons[-1] if seasons else None
    for yr in seasons:
        agg, logs = _season_aggregates(rc[yr])
        aggs[yr] = agg
        for abbr, rows in logs.items():
            all_logs.setdefault(abbr, []).extend(rows)
        dates = sorted(g["date"] for g in rc[yr]["games"].values())
        if not dates:
            continue
        starts = sorted(g["start"] for g in rc[yr]["games"].values() if g["start"])
        as_of = starts[-1]
        w = R.window("SEASON", label=f"{yr} season", start=_day(dates[0]), end=_day(dates[-1]))
        q = _quality(ctx, "research_cache", as_of=as_of, coverage=f"{yr} regular season {dates[0]}..{dates[-1]}, {len(rc[yr]['games'])} games",
                     sample=len(rc[yr]["games"]))
        values = {abbr: _season_values(a) for abbr, a in agg.items()}
        keep = SEASON_METRICS if yr == latest else PRIOR_SEASON_METRICS
        for slug in SEASON_METRICS:
            rows = [{"entity_id": teams[a]["participant_id"], "display_name": teams[a]["display_name"], "short_name": a,
                     "value": v[slug][0], "sample_size": v[slug][1]} for a, v in sorted(values.items()) if a in teams]
            rk = _ranking(ctx, slug, f"MLB teams, {yr} regular season", w, rows, as_of, q, season=yr)
            if rk is None:
                continue
            for a, v in sorted(values.items()):
                if a not in teams or v[slug][0] is None:
                    continue
                pid = teams[a]["participant_id"]
                if slug in keep:
                    parts[a]["rankings"].append({"ranking_id": rk["ranking_id"], "metric_id": rk["metric_id"], "window_label": w["label"],
                                                 "split": None, "path": R.ranking_path(rk["ranking_id"])})
                    parts[a]["metrics"].append(_obs(ctx, slug, pid, "TEAM", v[slug][0], w, as_of, source="research_cache",
                                                    context=R.context_from_ranking(rk, pid), sample=v[slug][1], season=yr))
        # home/away splits for the latest season (no ranking: universe context is the season ranking above)
        if yr == latest:
            for a, s in sorted(agg.items()):
                if a not in teams:
                    continue
                pid = teams[a]["participant_id"]
                rows = []
                for side, label in (("home", "HOME"), ("away", "AWAY")):
                    sp = R.split("home_away", label)
                    rows.append(_obs(ctx, "win_pct", pid, "TEAM", _div(s[side]["W"], s[side]["G"]), w, as_of, source="research_cache",
                                     sample=s[side]["G"], season=yr, split=sp))
                    rows.append(_obs(ctx, "runs_per_game", pid, "TEAM", _div(s[side]["RS"], s[side]["G"]), w, as_of, source="research_cache",
                                     sample=s[side]["G"], season=yr, split=sp))
                parts[a]["splits"]["home_away"] = rows
    # per-game series and game logs (last TEAM_GAME_CAP games across the seasons read)
    rc_q = _quality(ctx, "research_cache", coverage=f"last {TEAM_GAME_CAP} completed regular-season games in {seasons[0]}..{seasons[-1]}" if seasons else None)
    for abbr, rows in sorted(all_logs.items()):
        if abbr not in teams:
            continue
        rows = sorted(rows, key=lambda r: (r["start"] or r["date"], r["pk"]))[-TEAM_GAME_CAP:]
        pid = teams[abbr]["participant_id"]
        for slug, key in (("runs_scored", "rs"), ("runs_allowed", "ra")):
            pts = []
            for r in rows:
                eid = event_pid(r["pk"])
                pts.append(R.point(x=str(r["pk"]), t=r["start"] or _day(r["date"]), value=r[key], quality_status="VERIFIED", event_id=eid,
                                   opponent_id=team_pid(r["opp"]), source="research_cache", path=R.event_path(eid) if eid in published_events else None))
            ser = R.time_series(sport=SPORT, metric_id=mid(slug), entity_id=pid, entity_type="TEAM", x_axis="GAME", points=R.rolling(pts, ROLLING_N),
                                as_of=rows[-1]["start"] or _day(rows[-1]["date"]), run_id=ctx.run_id, generated_at=ctx.gen, quality=rc_q,
                                unit=ctx.specs[slug]["unit"], rolling_window=ROLLING_N,
                                links=[R.link(rel="TEAM", target_kind="entity_profile", label=teams[abbr]["display_name"], target_id=pid, path=R.team_path(pid))])
            ctx.mark(slug, series=True)
            ctx.docs.append(ser)
            parts[abbr]["series"].append({"series_id": ser["series_id"], "metric_id": ser["metric_id"], "x_axis": "GAME", "split": None,
                                          "path": R.series_path(ser["series_id"])})
        for r in rows:
            eid = event_pid(r["pk"])
            opp = teams.get(r["opp"])
            parts[abbr]["games"].append(R.game_ref(
                event_id=eid, start_time_utc=r["start"] or _day(r["date"]), status="FINAL", opponent_id=team_pid(r["opp"]),
                opponent_name=(opp or {}).get("display_name") or r["opp_name"], home_away=r["side"].upper(),
                result={"for": r["rs"], "against": r["ra"], "outcome": "W" if r["rs"] > r["ra"] else "L" if r["rs"] < r["ra"] else "T"},
                competition=f"MLB {r['season']} regular season", path=R.event_path(eid) if eid in published_events else None))
            parts[abbr]["opponents"].setdefault(r["opp"], []).append(eid)
    for yr in seasons:
        for a, s in aggs[yr].items():
            if a in parts:
                parts[a]["ext"].setdefault("record_by_season", {})[yr] = {"games": s["G"], "wins": s["W"], "losses": s["G"] - s["W"],
                                                                          "runs_scored": s["RS"], "runs_allowed": s["RA"]}
    _snapshot_team_metrics(ctx, teams, parts)
    _form_team_metrics(ctx, teams, parts)
    _slate_team_history(ctx, teams, parts, published_events)
    return parts


def _snapshot_team_metrics(ctx, teams, parts):
    inputs = ctx.inputs
    sources = []
    bp = (inputs["bullpen"] or {}).get("bullpens") or {}
    if bp:
        sources.append(("bullpen_xfip", {_norm_abbr(k): v.get("xFIP") for k, v in bp.items()}, inputs["bullpen"].get("fetchedAt"), "data/bullpen.json"))
        sources.append(("bullpen_era", {_norm_abbr(k): v.get("era") for k, v in bp.items()}, inputs["bullpen"].get("fetchedAt"), "data/bullpen.json"))
    sv = (inputs["savant"] or {}).get("teams") or {}
    if sv:
        sources.append(("team_xwoba", {_norm_abbr(k): v.get("xwoba") for k, v in sv.items()}, inputs["savant"].get("fetchedAt"), "data/savant_team.json"))
    oq = (inputs["oppquality"] or {}).get("teams") or {}
    if oq:
        sources.append(("opp_quality_adj", {_norm_abbr(k): v.get("oppQualityAdj") for k, v in oq.items()}, inputs["oppquality"].get("fetchedAt"), "data/oppquality.json"))
        sources.append(("opp_starter_xera_avg", {_norm_abbr(k): v.get("oppXFIPavg") for k, v in oq.items()}, inputs["oppquality"].get("fetchedAt"), "data/oppquality.json"))
    for slug, vals, as_of, src in sources:
        if not as_of:
            continue
        season = str(timeutil.parse_ts(as_of).year)
        w = R.window("SEASON", label=f"{season} to date (snapshot)", end=as_of)
        q = _quality(ctx, "snapshot", as_of=as_of, coverage=f"{len(vals)} teams, snapshot {timeutil.to_iso(as_of)}", sample=len(vals))
        rows = [{"entity_id": teams[a]["participant_id"], "display_name": teams[a]["display_name"], "short_name": a, "value": v}
                for a, v in sorted(vals.items()) if a in teams]
        rk = _ranking(ctx, slug, f"MLB teams, {season} season to date", w, rows, as_of, q, season=season)
        for a, v in sorted(vals.items()):
            if a not in teams or v is None:
                continue
            pid = teams[a]["participant_id"]
            parts[a]["metrics"].append(_obs(ctx, slug, pid, "TEAM", v, w, as_of, source=src, season=season,
                                            context=R.context_from_ranking(rk, pid) if rk else None))
            if rk:
                parts[a]["rankings"].append({"ranking_id": rk["ranking_id"], "metric_id": rk["metric_id"], "window_label": w["label"],
                                             "split": None, "path": R.ranking_path(rk["ranking_id"])})
        if slug == "opp_quality_adj":
            for a, row in sorted(oq.items()):
                a = _norm_abbr(a)
                if a in parts:
                    parts[a]["ext"]["opp_quality_snapshot"] = {"games_resolved": row.get("gamesResolved"), "games_total": row.get("gamesTotal"),
                                                              "confidence": row.get("confidence"), "window_days": inputs["oppquality"].get("windowDays"),
                                                              "league_avg_xfip_constant": inputs["oppquality"].get("leagueAvgXFIP")}
    for a, v in sorted(bp.items()):
        a = _norm_abbr(a)
        if a in parts:
            parts[a]["ext"]["bullpen_snapshot"] = {k: v.get(k) for k in ("grade", "whip", "kPer9", "bbPer9", "hr9", "inningsPitched", "hlXFIP", "hlGrade",
                                                                       "hlSamplePA", "hlMethod")}
            ru = v.get("recentUsage") or {}
            parts[a]["ext"]["bullpen_recent_usage"] = {k: ru.get(k) for k in ("dataAvailable", "unavailableReason", "asOfDate", "gamesConsidered",
                                                                               "relieversUsedLastGame", "backToBackRelievers", "teamPitchCountLastGame")}


def _form_team_metrics(ctx, teams, parts):
    form = ctx.inputs["offense_form"] or {}
    tf = form.get("teams") or {}
    as_of = form.get("generatedAt")
    if not tf or not as_of:
        return
    for n in form.get("formWindows") or [5, 7, 10]:
        label = f"L{n}"
        w = R.window("LAST_N", n=n, label=f"L{n} (as of {form.get('asOfDate')})")
        q = _quality(ctx, "form", as_of=as_of, coverage=f"30-day lookback from {form.get('windowStartDate')} to {form.get('asOfDate')}", sample=len(tf))
        for slug, getter in (("form_runs_mean", lambda win: win.get("mean")), ("form_runs_median", lambda win: win.get("median")),
                             ("form_overs_pct", lambda win: (win.get("marketRelative") or {}).get("oversPct"))):
            vals = {}
            for a, t in tf.items():
                win = (t.get("windows") or {}).get(label) or {}
                vals[_norm_abbr(a)] = (getter(win), win.get("games"), win)
            rows = [{"entity_id": teams[a]["participant_id"], "display_name": teams[a]["display_name"], "short_name": a, "value": v[0], "sample_size": v[1]}
                    for a, v in sorted(vals.items()) if a in teams]
            rk = _ranking(ctx, slug, f"MLB teams, last {n} games", w, rows, as_of, q, season=str(form.get("asOfDate") or "")[:4] or None,
                          filt="teams with a stored value in data/team_offense_form.json")
            for a, (v, g, win) in sorted(vals.items()):
                if a not in teams or v is None:
                    continue
                pid = teams[a]["participant_id"]
                ext = {"analysis_only": True}
                if slug == "form_overs_pct":
                    ext["line_coverage"] = (win.get("marketRelative") or {}).get("lineCoverage")
                parts[a]["metrics"].append(_obs(ctx, slug, pid, "TEAM", v, w, as_of, source="data/team_offense_form.json", sample=g,
                                                context=R.context_from_ranking(rk, pid) if rk else None, ext=ext))
                if rk:
                    parts[a]["rankings"].append({"ranking_id": rk["ranking_id"], "metric_id": rk["metric_id"], "window_label": w["label"],
                                                 "split": None, "path": R.ranking_path(rk["ranking_id"])})
    for a, t in sorted(tf.items()):
        a = _norm_abbr(a)
        if a in parts:
            parts[a]["ext"]["form"] = {"analysis_only": True, "as_of_date": t.get("asOfDate"), "form_label": t.get("formLabel"),
                                       "form_lines": t.get("formLines"), "note": form.get("note")}


def _slate_team_history(ctx, teams, parts, published_events):
    """Opponent-adjustment and offense-baseline history per team, one point per slate game (x_axis DATE),
    plus the latest home park factor."""
    hist = {}
    park = {}
    for d, slate in ctx.inputs["slates"]:
        for g in slate.get("games") or []:
            pk = g.get("gameId")
            if pk in (None, ""):
                continue
            for side, opp in (("away", "home"), ("home", "away")):
                abbr = _norm_abbr((g.get(side) or {}).get("abbr"))
                ts = g.get(f"{side}TeamStats") or {}
                hist.setdefault(abbr, []).append({"date": d, "start": g.get("startTime") or _day(d), "pk": pk,
                                                  "opp": _norm_abbr((g.get(opp) or {}).get("abbr")),
                                                  "opp_quality_adj": ts.get("oppQualityAdj"), "offense_baseline_adj": ts.get("offenseBaselineAdj")})
            pf = g.get("park") or {}
            h = _norm_abbr((g.get("home") or {}).get("abbr"))
            if pf.get("parkFactor") is not None:
                park[h] = (d, g.get("startTime") or _day(d), pf)
    q = _quality(ctx, "slate", coverage=f"{len(ctx.inputs['slates'])} slate dates")
    for abbr, rows in sorted(hist.items()):
        if abbr not in teams:
            continue
        pid = teams[abbr]["participant_id"]
        rows = sorted(rows, key=lambda r: (r["start"], str(r["pk"])))
        for slug, status in (("opp_quality_adj", "VERIFIED"), ("offense_baseline_adj", "VERIFIED")):
            pts = []
            for r in rows:
                if r[slug] is None:
                    continue
                eid = event_pid(r["pk"])
                pts.append(R.point(x=f"{r['date']}:{r['pk']}", t=r["start"], value=r[slug], quality_status=status, event_id=eid,
                                   opponent_id=team_pid(r["opp"]) if r["opp"] else None, source=f"data/slates/{r['date']}/authoritative.json",
                                   path=R.event_path(eid) if eid in published_events else None))
            if not pts:
                continue
            ser = R.time_series(sport=SPORT, metric_id=mid(slug), entity_id=pid, entity_type="TEAM", x_axis="DATE", points=pts,
                                as_of=pts[-1]["t"], run_id=ctx.run_id, generated_at=ctx.gen, quality=q, unit=ctx.specs[slug]["unit"],
                                links=[R.link(rel="TEAM", target_kind="entity_profile", label=teams[abbr]["display_name"], target_id=pid, path=R.team_path(pid))])
            ctx.mark(slug, series=True)
            ctx.docs.append(ser)
            parts[abbr]["series"].append({"series_id": ser["series_id"], "metric_id": ser["metric_id"], "x_axis": "DATE", "split": None,
                                          "path": R.series_path(ser["series_id"])})
    for abbr, (d, start, pf) in sorted(park.items()):
        if abbr not in teams:
            continue
        pid = teams[abbr]["participant_id"]
        w = R.window("GAME", label=f"home park as of slate {d}")
        parts[abbr]["metrics"].append(_obs(ctx, "park_factor", pid, "TEAM", pf.get("parkFactor"), w, start,
                                           source=f"data/slates/{d}/authoritative.json", season=d[:4],
                                           ext={"park": pf.get("name"), "dome": pf.get("dome")}))
        parts[abbr]["ext"]["home_park"] = {"name": pf.get("name"), "park_factor": pf.get("parkFactor"), "dome": pf.get("dome"), "as_of_slate": d}


# ---------------------------------------------------------------------- players

def _statcast_aggregates(rows, player_id):
    """Per-game aggregates for one player as pitcher and as batter."""
    pid = str(player_id)
    games = {}
    for r in rows:
        as_p = str(r.get("pitcherId")) == pid
        as_b = str(r.get("batterId")) == pid
        if not (as_p or as_b):
            continue
        g = games.setdefault(int(r["gamePk"]), {"date": r.get("gameDate"), "p": [], "b": []})
        if as_p:
            g["p"].append(r)
        if as_b:
            g["b"].append(r)

    def pa_values(rs):
        return [r["estimatedWOBA"] if r.get("estimatedWOBA") is not None else r.get("wobaValue")
                for r in rs if r.get("events") and r.get("events") != "truncated_pa"]

    out = []
    for pk in sorted(games, key=lambda k: (games[k]["date"] or "", k)):
        g = games[pk]
        row = {"game_pk": pk, "date": g["date"]}
        if g["p"]:
            ps = g["p"]
            mix = {}
            for r in ps:
                key = r.get("pitchType") or "UN"
                mix[key] = mix.get(key, 0) + 1
            swings = [r for r in ps if r.get("description") in SWINGS]
            row["pitcher"] = {
                "pitches": len(ps), "velocity": _mean([r.get("releaseSpeed") for r in ps], 2),
                "whiff_pct": _div(sum(1 for r in swings if r.get("description") in WHIFFS), len(swings)),
                "xwoba_allowed": _mean(pa_values(ps), 3), "pa": len(pa_values(ps)),
                "pitch_mix": {k: round(v / len(ps), 3) for k, v in sorted(mix.items())},
                "hand": ps[0].get("pitcherHand"),
            }
        if g["b"]:
            bs = g["b"]
            bip = [r for r in bs if r.get("launchSpeed") is not None]
            row["batter"] = {"pa": len(pa_values(bs)), "xwoba": _mean(pa_values(bs), 3),
                             "exit_velo": _mean([r["launchSpeed"] for r in bip], 1),
                             "launch_angle": _mean([r.get("launchAngle") for r in bip], 1), "batted_balls": len(bip),
                             "hand": bs[0].get("batterHand")}
        out.append(row)
    return out


def build_player_layer(ctx, teams, universe, published_events, v1_markets_by_ticker):
    """Player profiles for the bounded universe. Returns {player_id: profile}."""
    inputs = ctx.inputs
    savant = inputs["savant"] or {}
    sv_as_of = savant.get("fetchedAt")
    batters, disc, pitchers = savant.get("batters") or {}, savant.get("battersDiscipline") or {}, savant.get("pitchers") or {}
    # pitcher appearances (research cache)
    appearances = {}
    for yr in sorted(inputs["research_cache"]):
        season = inputs["research_cache"][yr]
        for pk, row in season["pitching"].items():
            g = season["games"].get(pk)
            if not g:
                continue
            for side, opp in (("away", "home"), ("home", "away")):
                for p in row.get(f"{side}Pitchers") or []:
                    if str(p.get("playerId")) in universe:
                        appearances.setdefault(str(p["playerId"]), []).append({"pk": pk, "start": g["start"] or _day(g["date"]), "date": g["date"],
                                                                                "team": g[side], "opp": g[opp], "line": p, "season": yr})
    # latest slate pitcherSavant per starter
    starter_block = {}
    for d, slate in inputs["slates"]:
        for g in slate.get("games") or []:
            for side in ("away", "home"):
                b = g.get(side) or {}
                pid = str((b.get("pitcher") or {}).get("id") or "")
                if pid in universe and b.get("pitcherSavant"):
                    starter_block[pid] = (d, g.get("startTime") or _day(d), b["pitcherSavant"], g.get("gameId"))
    sc = inputs["statcast"]
    sc_rows_by_player = {}
    for r in sc["rows"]:
        for k in ("pitcherId", "batterId"):
            if str(r.get(k)) in universe:
                sc_rows_by_player.setdefault(str(r[k]), []).append(r)
    sc_start = (sc["range"] or [None, None])[0] or sc["index_start"]
    sc_end = (sc["range"] or [None, None])[1] or sc["index_end"]
    profiles = {}
    for player_id in sorted(universe):
        u = universe[player_id]
        pid = player_pid(player_id)
        team = teams.get(u["team"])
        metrics, series_refs, games, ext = [], [], [], {"roles": sorted(u["roles"]), "mlbam_player_id": player_id}
        # Savant snapshot
        if sv_as_of:
            w = R.window("SEASON", label=f"{savant.get('season')} to date (snapshot)", end=sv_as_of)
            if player_id in batters:
                metrics.append(_obs(ctx, "batter_xwoba", pid, "PLAYER", batters[player_id], w, sv_as_of, source="data/savant_team.json", season=savant.get("season")))
            for slug, field in (("batter_k_pct", "kPct"), ("batter_bb_pct", "bbPct"), ("batter_hard_hit_pct", "hardHitPct"),
                                ("batter_barrel_pct", "barrelPct"), ("batter_exit_velo", "exitVeloAvg")):
                v = (disc.get(player_id) or {}).get(field)
                if v is not None:
                    metrics.append(_obs(ctx, slug, pid, "PLAYER", v, w, sv_as_of, source="data/savant_team.json", season=savant.get("season")))
            for slug, field in (("pitcher_xera", "xera"), ("pitcher_k_pct", "kPct"), ("pitcher_bb_pct", "bbPct")):
                v = (pitchers.get(player_id) or {}).get(field)
                if v is not None:
                    metrics.append(_obs(ctx, slug, pid, "PLAYER", v, w, sv_as_of, source="data/savant_team.json", season=savant.get("season")))
        if player_id in starter_block:
            d, start, blk, gpk = starter_block[player_id]
            w = R.window("SEASON", label=f"slate {d} (pitcherSavant)", end=start)
            for slug, field in (("starter_xfip", "xFIP"), ("starter_xera", "xERA"), ("starter_k_pct", "kPct"), ("starter_bb_pct", "bbPct"),
                                ("starter_recent_fip", "recentFIP"), ("starter_avg_ip", "avgIPperStart")):
                if blk.get(field) is not None:
                    metrics.append(_obs(ctx, slug, pid, "PLAYER", blk[field], w, start, source=f"data/slates/{d}/authoritative.json", season=d[:4],
                                        status="PARTIAL"))
            ext["starter_savant_latest_slate"] = {"slate_date": d, "game_pk": gpk,
                                                  **{k: blk.get(k) for k in ("seasonIP", "seasonStarts", "whiffPct", "hardHitPct", "barrelPct", "exitVeloAvg",
                                                                             "fbPct", "seasonFIP", "ttoSplit", "tto1", "tto3", "vsLHH", "vsRHH",
                                                                             "velocityRecent", "velocitySeason")}}
        # pitcher appearance log (research cache)
        apps = sorted(appearances.get(player_id, []), key=lambda a: (a["start"], a["pk"]))[-PLAYER_GAME_CAP:]
        if apps:
            q = _quality(ctx, "player_logs", as_of=apps[-1]["start"], coverage=f"last {len(apps)} appearances (cap {PLAYER_GAME_CAP})", sample=len(apps))
            for slug, field in (("pitcher_outs", "outs"), ("pitcher_pitches", "numberOfPitches")):
                pts = []
                for a in apps:
                    eid = event_pid(a["pk"])
                    pts.append(R.point(x=str(a["pk"]), t=a["start"], value=a["line"].get(field), quality_status="PARTIAL", event_id=eid,
                                       opponent_id=team_pid(a["opp"]), source="research_cache/starter_workload",
                                       path=R.event_path(eid) if eid in published_events else None))
                ser = R.time_series(sport=SPORT, metric_id=mid(slug), entity_id=pid, entity_type="PLAYER", x_axis="GAME", points=R.rolling(pts, 5),
                                    as_of=apps[-1]["start"], run_id=ctx.run_id, generated_at=ctx.gen, quality=q, unit=ctx.specs[slug]["unit"], rolling_window=5,
                                    links=[R.link(rel="PLAYER", target_kind="entity_profile", label=u["name"], target_id=pid, path=R.player_path(pid))])
                ctx.mark(slug, series=True)
                ctx.docs.append(ser)
                series_refs.append({"series_id": ser["series_id"], "metric_id": ser["metric_id"], "x_axis": "GAME", "split": None, "path": R.series_path(ser["series_id"])})
            for a in apps:
                eid = event_pid(a["pk"])
                games.append(R.game_ref(event_id=eid, start_time_utc=a["start"], status="FINAL", opponent_id=team_pid(a["opp"]),
                                        opponent_name=(teams.get(a["opp"]) or {}).get("display_name"), home_away=None, result=None,
                                        competition=f"MLB {a['season']} regular season", path=R.event_path(eid) if eid in published_events else None))
            ext["pitching_log"] = [{"game_pk": a["pk"], "date": a["date"], "opp": a["opp"], "starter": a["line"].get("orderIndex") == 0,
                                    **{k: a["line"].get(k) for k in ("outs", "numberOfPitches", "battersFaced", "hits", "earnedRuns", "baseOnBalls", "strikeOuts")}}
                                   for a in apps]
        # Statcast
        per_game = _statcast_aggregates(sc_rows_by_player.get(player_id, []), player_id)[-PLAYER_GAME_CAP:]
        if per_game and sc_start and sc_end:
            w = R.window("DATE_RANGE", label=f"Statcast {sc_start}..{sc_end}", start=_day(sc_start), end=_day(sc_end))
            last_t = _day(per_game[-1]["date"])
            pg = [g for g in per_game if "pitcher" in g]
            bg = [g for g in per_game if "batter" in g]
            allrows = sc_rows_by_player.get(player_id, [])
            if pg:
                prow = [r for r in allrows if str(r.get("pitcherId")) == player_id]
                swings = [r for r in prow if r.get("description") in SWINGS]
                pa = [r["estimatedWOBA"] if r.get("estimatedWOBA") is not None else r.get("wobaValue")
                      for r in prow if r.get("events") and r.get("events") != "truncated_pa"]
                metrics.append(_obs(ctx, "sc_pitches", pid, "PLAYER", len(prow), w, last_t, source="data/statcast_raw", sample=len(pg)))
                metrics.append(_obs(ctx, "sc_velocity", pid, "PLAYER", _mean([r.get("releaseSpeed") for r in prow], 2), w, last_t,
                                    source="data/statcast_raw", sample=len(prow)))
                metrics.append(_obs(ctx, "sc_whiff_pct", pid, "PLAYER", _div(sum(1 for r in swings if r.get("description") in WHIFFS), len(swings)),
                                    w, last_t, source="data/statcast_raw", sample=len(swings)))
                metrics.append(_obs(ctx, "sc_xwoba_allowed", pid, "PLAYER", _mean(pa, 3), w, last_t, source="data/statcast_raw", sample=len(pa)))
            if bg:
                brow = [r for r in allrows if str(r.get("batterId")) == player_id]
                pa = [r["estimatedWOBA"] if r.get("estimatedWOBA") is not None else r.get("wobaValue")
                      for r in brow if r.get("events") and r.get("events") != "truncated_pa"]
                bip = [r for r in brow if r.get("launchSpeed") is not None]
                metrics.append(_obs(ctx, "sc_batter_pa", pid, "PLAYER", len(pa), w, last_t, source="data/statcast_raw", sample=len(bg)))
                metrics.append(_obs(ctx, "sc_batter_xwoba", pid, "PLAYER", _mean(pa, 3), w, last_t, source="data/statcast_raw", sample=len(pa)))
                metrics.append(_obs(ctx, "sc_exit_velo", pid, "PLAYER", _mean([r["launchSpeed"] for r in bip], 1), w, last_t,
                                    source="data/statcast_raw", sample=len(bip)))
                metrics.append(_obs(ctx, "sc_launch_angle", pid, "PLAYER", _mean([r.get("launchAngle") for r in bip], 1), w, last_t,
                                    source="data/statcast_raw", sample=len(bip)))
            q = _quality(ctx, "statcast", as_of=last_t, coverage=f"{sc_start}..{sc_end}, {len(per_game)} games", sample=len(per_game))
            for slug, kind, field in (("sc_velocity", "pitcher", "velocity"), ("sc_batter_xwoba", "batter", "xwoba")):
                rows = [g for g in per_game if kind in g and g[kind].get(field) is not None]
                if not rows:
                    continue
                pts = []
                for g in rows:
                    eid = event_pid(g["game_pk"])
                    pts.append(R.point(x=str(g["game_pk"]), t=_day(g["date"]), value=g[kind][field], quality_status="PARTIAL", event_id=eid,
                                       sample_size=g[kind]["pitches"] if kind == "pitcher" else g[kind]["pa"], source="data/statcast_raw",
                                       path=R.event_path(eid) if eid in published_events else None))
                ser = R.time_series(sport=SPORT, metric_id=mid(slug), entity_id=pid, entity_type="PLAYER", x_axis="GAME", points=R.rolling(pts, 5),
                                    as_of=last_t, run_id=ctx.run_id, generated_at=ctx.gen, quality=q, unit=ctx.specs[slug]["unit"], rolling_window=5,
                                    links=[R.link(rel="PLAYER", target_kind="entity_profile", label=u["name"], target_id=pid, path=R.player_path(pid))])
                ctx.mark(slug, series=True)
                ctx.docs.append(ser)
                series_refs.append({"series_id": ser["series_id"], "metric_id": ser["metric_id"], "x_axis": "GAME", "split": None, "path": R.series_path(ser["series_id"])})
            ext["statcast_games"] = per_game
            ext["statcast_window"] = {"start": sc_start, "end": sc_end, "label": f"Statcast {sc_start}..{sc_end} (pitch log window)"}
        markets = [R.market_ref(v1_markets_by_ticker[t]) for t in sorted(u["market_tickers"]) if t in v1_markets_by_ticker]
        meta = {"team": u["team"], "roles": sorted(u["roles"])}
        for k in ("position", "throws"):
            if u["extra"].get(k):
                meta[k] = u["extra"][k]
        surname = u["name"].split()[-1] if u["name"].split() else u["name"]
        entity = build.participant(sport=SPORT, participant_type="PLAYER", source=PLAYER_SOURCE, source_id=player_id, display_name=u["name"],
                                   short_name=surname, metadata=meta)
        links = []
        if team:
            links.append(R.link(rel="TEAM", target_kind="entity_profile", label=team["display_name"], target_id=team["participant_id"],
                                path=R.team_path(team["participant_id"])))
        for ref in series_refs:
            links.append(R.link(rel="SERIES", target_kind="time_series", label=ctx.specs[ref["metric_id"].split(".", 1)[1]]["name"],
                                target_id=ref["series_id"], path=ref["path"]))
        q = R.quality(status="PARTIAL", source="data/savant_team.json, data/slates, data/research_cache/starter_workload, data/statcast_raw",
                      generated_at=ctx.gen, production=True, data_as_of=max([o["as_of"] for o in metrics] or [None], key=lambda x: x or ""),
                      methodology_version=RESEARCH_EXPORT_VERSION, coverage="players on the current slate or in current markets",
                      limitations=[LIM["player_snapshot"], LIM["player_logs"], LIM["statcast"]])
        profiles[player_id] = {"entity": entity, "team": team, "metrics": metrics, "series": series_refs, "games": games, "markets": markets,
                               "links": links, "quality": q, "ext": ext}
    return profiles


# ---------------------------------------------------------------------- events

def _rec_authority(v1):
    out = {}
    for r in v1["recommendations"]:
        cur = out.get(r["market_id"])
        if cur is None or (cur[0] and not r["research_only"]):
            out[r["market_id"]] = (bool(r["research_only"]), r["authority"])
    return out


def _projection(mp, rec_auth):
    research_only, authority = rec_auth.get(mp["market_id"], (True, "RESEARCH_ONLY"))
    tier = mp.get("support_status")
    status = "VERIFIED" if tier in ("TRUSTED_PRODUCTION", "MARKET_LEDGER") else "RESEARCH"
    return R.projection_ref(mp, research_only=research_only, authority=authority, quality_status=status, metric_id=mid("model_fair_probability"))


def _slate_game_obs(ctx, g, d, side, team, players_by_id):
    """(slug -> observation) for one side of a slate game."""
    ts = g.get(f"{side}TeamStats") or {}
    blk = g.get(side) or {}
    bp = blk.get("bullpen") or {}
    start = g.get("startTime") or _day(d)
    pid = team["participant_id"]
    eid = event_pid(g["gameId"])
    gw = R.window("GAME", label=f"pregame slate {d}")
    sw = R.window("SEASON", label=f"{d[:4]} to date (slate {d})", end=start)
    out = {}
    first = (g.get("marketLedger") or [{}])[0]

    def add(slug, value, w, *, sample=None, entity=pid, etype="TEAM", status=None):
        if value is None:
            return
        out[slug] = _obs(ctx, slug, entity, etype, value, w, start, source=f"data/slates/{d}/authoritative.json", season=d[:4], event_id=eid,
                         sample=sample, status=status)
    add("slate_season_rpg", ts.get("seasonRpG"), sw, sample=ts.get("gamesPlayed"))
    add("slate_last7_rpg", ts.get("last7RpG"), R.window("LAST_N", n=7, label=f"L7 (slate {d})"))
    add("slate_last15_rpg", ts.get("last15RpG"), R.window("LAST_N", n=15, label=f"L15 (slate {d})"))
    add("wrc_plus_proxy", ts.get("wrcPlus"), sw)
    add("offense_baseline_adj", ts.get("offenseBaselineAdj"), gw)
    add("opp_quality_adj", ts.get("oppQualityAdj"), gw, sample=ts.get("oppQualityGames"))
    add("bullpen_xfip", bp.get("xFIP"), sw)
    add("bullpen_era", bp.get("era"), sw)
    add("projected_runs", first.get(f"{side}ProjRuns") if first.get(f"{side}ProjRuns") is not None else (g.get("modelProb") or {}).get(f"{side}ProjRuns"), gw)
    add("model_win_probability", (g.get("modelProb") or {}).get(side), gw)
    sp_id = str((blk.get("pitcher") or {}).get("id") or "")
    sav = blk.get("pitcherSavant") or {}
    if sp_id and sp_id in players_by_id:
        for slug, field in (("starter_xfip", "xFIP"), ("starter_xera", "xERA"), ("starter_k_pct", "kPct"), ("starter_bb_pct", "bbPct")):
            add(slug, sav.get(field), sw, entity=player_pid(sp_id), etype="PLAYER", status="PARTIAL")
    return out


def _model_inputs(g, d):
    def side(s):
        blk = g.get(s) or {}
        ts = g.get(f"{s}TeamStats") or {}
        bp = blk.get("bullpen") or {}
        form = ts.get("offenseForm") or {}
        return {
            "team": blk.get("abbr"), "record": ts.get("record"),
            "starter": {"id": (blk.get("pitcher") or {}).get("id"), "name": (blk.get("pitcher") or {}).get("name"),
                        "hand": (blk.get("pitcher") or {}).get("pitchHand"), "savant": blk.get("pitcherSavant")},
            "bullpen": {k: bp.get(k) for k in bp if k != "recentUsage"} | {"recentUsage": bp.get("recentUsage")},
            "team_form": {"windows": {k: {x: v.get(x) for x in ("games", "mean", "median", "min", "max", "ewma", "trimmedMean", "marketRelative",
                                                                  "outlierDependence")} for k, v in (form.get("windows") or {}).items()},
                          "form_label": form.get("formLabel"), "as_of_date": form.get("asOfDate"), "analysis_only": True},
            "opp_quality": {k: ts.get(k) for k in ("oppXFIPavg", "oppQualityAdj", "oppQualityGames", "oppQualityConf", "oppQualityNote")},
            "offense_baseline": {k: ts.get(k) for k in ("offenseBaselineRaw", "offenseBaselineBayes", "offenseBaselineOppAdj", "offenseBaselineAdj",
                                                        "lineupAdj", "lineupWOBADelta")},
            "team_rates": {k: ts.get(k) for k in ("seasonRpG", "last7RpG", "last15RpG", "wrcPlus", "teamWOBA", "teamKPct", "teamBBPct", "teamHardHit", "teamBarrel")},
        }
    ledger = []
    for r in g.get("marketLedger") or []:
        ledger.append({k: r.get(k) for k in ("market", "status", "modelProb", "kalshiVF", "pinnacleVF", "marketProbVF", "edge", "netExecutableEdge",
                                             "executablePriceMarketTicker", "executablePriceSide", "executablePriceUsed", "betUpToPriceNet",
                                             "confidenceTier", "rejectionReason", "line", "awayProjRuns", "homeProjRuns", "totalProj",
                                             "f5AwayProj", "f5HomeProj", "closingPrice")})
    odds = g.get("odds") or {}
    return {
        "slate_date": d, "game_pk": g.get("gameId"), "away": side("away"), "home": side("home"), "park": g.get("park"),
        "lineup_status": g.get("lineupStatus"), "lineup_confirmed": g.get("lineupConfirmed"), "lineup_source": g.get("lineupSource"),
        "model": {k: (g.get("modelProb") or {}).get(k) for k in ("away", "home", "awayProjRuns", "homeProjRuns", "totalProj", "confidence",
                                                                  "awayProjDetail", "homeProjDetail")},
        "total_eval": g.get("totalEval"), "team_totals": g.get("teamTotals"), "f5": g.get("f5"), "nrfi": g.get("nrfi"),
        "pinnacle_vig_free": g.get("pinnacleVF"), "pinnacle_f5_vig_free": g.get("pinnacleF5VF"),
        "odds": {"pinnacle": odds.get("pinnacle"), "kalshi": {k: v for k, v in (odds.get("kalshi") or {}).items() if k in ("ml", "rl", "total")}},
        "market_ledger": ledger,
        "comparison_note": "marketLedger modelProb (model, percent) vs kalshiVF (Kalshi vig-free, percent) vs pinnacleVF (Pinnacle vig-free, percent).",
    }


def _market_history(ctx, ev, markets):
    tickers = {m["kalshi_ticker"]: m for m in markets}
    if not tickers:
        return None
    closing = {}
    for q in ctx.inputs["clv_quotes"]:
        t = str(q.get("marketTicker") or "").upper()
        if t in tickers and q.get("capturedAt"):
            closing.setdefault(t, []).append(q)
    series = {}
    seen = set()
    for o in ctx.inputs["observations"]:
        t = str(o.get("marketTicker") or "").upper()
        if t not in tickers or not o.get("capturedAt"):
            continue
        key = (t, timeutil.to_iso(o["capturedAt"]))
        if key in seen:
            continue
        seen.add(key)
        is_close = any(timeutil.to_iso(q["capturedAt"]) == key[1] and q.get("isClosingQuote") for q in closing.get(t, []))
        series.setdefault(t, []).append(R.price_point(
            captured_at=o["capturedAt"], yes_bid=o.get("yesBid"), yes_ask=o.get("yesAsk"), last_price=o.get("lastPrice"),
            volume=o.get("volume"), open_interest=o.get("openInterest"),
            source=f"observations:{o.get('checkpoint') or 'NONE'}" + ("+closing_quote" if is_close else "")))
    for t, qs in closing.items():
        for q in qs:
            key = (t, timeutil.to_iso(q["capturedAt"]))
            if key in seen:
                continue
            seen.add(key)
            series.setdefault(t, []).append(R.price_point(
                captured_at=q["capturedAt"], yes_bid=q.get("yesBid"), yes_ask=q.get("yesAsk"), last_price=q.get("lastPrice"),
                source=f"clv_quotes:{q.get('checkpoint') or 'NONE'}" + ("+closing_quote" if q.get("isClosingQuote") else "")))
    if not series:
        return None
    # game-level tickers first, then player props, whole tickers only, while the document stays in budget
    ordered = sorted(series.items(), key=lambda kv: (is_prop_market(tickers[kv[0]]), kv[0]))
    rows, used, omitted = [], 2_000, []
    for t, pts in ordered:
        size = len(json.dumps(pts, separators=(",", ":"))) + 120
        if used + size > MARKET_HISTORY_BUDGET:
            omitted.append(t)
            continue
        used += size
        rows.append({"market_id": tickers[t]["market_id"], "kalshi_ticker": t, "points": pts})
    if not rows:
        return None
    as_of = max(p["captured_at"] for r in rows for p in r["points"])
    depth = sorted(len(r["points"]) for r in rows)
    q = _quality(ctx, "market_history", as_of=as_of, coverage=f"{len(rows)} tickers, {sum(depth)} points (median {depth[len(depth) // 2]} per ticker)",
                 sample=sum(depth))
    if omitted:
        q = dict(q, limitations=q["limitations"] + [f"{len(omitted)} player-prop ticker(s) omitted to keep this document under 400 KB; "
                                                    "their quotes remain in data/edgelab/observations"])
    return R.market_history(sport=SPORT, run_id=ctx.run_id, generated_at=ctx.gen, event_id=ev["event_id"], as_of=as_of, series=rows, quality=q,
                            links=[R.link(rel="EVENT_RESEARCH", target_kind="event_research", label="event research", target_id=ev["event_id"],
                                          path=R.event_path(ev["event_id"]))])


def _projection_series(ctx, ev, markets):
    """model_evaluations per ticker over runs (x_axis RUN), oriented to P(YES)."""
    tickers = {m["kalshi_ticker"]: m for m in markets}
    market_rows = {r.get("marketTicker"): r for r in ctx.inputs["edgelab_markets"] if r.get("marketTicker")}
    by_t = {}
    for r in ctx.inputs["model_evaluations"]:
        t = str(r.get("marketTicker") or "").strip().upper()
        if t not in tickers or r.get("modelFairProbability") is None or not r.get("createdAt"):
            continue
        side, _basis, _refusal, _evidence = app_export.resolve_side(r, market_rows.get(t))
        if side is None:
            continue
        fair, _mkt = app_export._orient(side, r["modelFairProbability"], r.get("marketImpliedProbability"))
        status = "VERIFIED" if r.get("qualityTier") == "TRUSTED_PRODUCTION" else "RESEARCH"
        label = r.get("checkpoint") or r.get("artifactSource") or "evaluation"
        by_t.setdefault(t, []).append(R.point(x=f"{label} {r.get('runId')}", t=r["createdAt"], value=fair, quality_status=status,
                                              event_id=ev["event_id"], source=f"edgelab/model_evaluations {r.get('qualityTier')}",
                                              path=R.event_path(ev["event_id"])))
    out = []
    for t, pts in sorted(by_t.items()):
        if len(pts) < 2:
            continue  # a single evaluation is not a series; it is already the v1 model price
        m = tickers[t]
        q = _quality(ctx, "model_evals", as_of=max(p["t"] for p in pts), coverage=f"{len(pts)} evaluation(s) on {ctx.inputs['date']}", sample=len(pts))
        ser = R.time_series(sport=SPORT, metric_id=mid("model_fair_probability"), entity_id=m["market_id"], entity_type="MARKET", x_axis="RUN",
                            points=pts, as_of=max(p["t"] for p in pts), run_id=ctx.run_id, generated_at=ctx.gen, quality=q, unit="probability",
                            links=[R.link(rel="EVENT_RESEARCH", target_kind="event_research", label="event research", target_id=ev["event_id"],
                                          path=R.event_path(ev["event_id"]))])
        ctx.mark("model_fair_probability", series=True)
        out.append((m, ser))
    return out


def build_event_layer(ctx, teams, player_profiles, published_events):
    inputs = ctx.inputs
    v1 = inputs["v1"]
    slate_games = {}
    for d, slate in inputs["slates"]:
        if d == inputs["date"]:
            for g in slate.get("games") or []:
                if g.get("gameId") not in (None, ""):
                    slate_games[str(g["gameId"])] = (d, g)
    markets_by_event = {}
    for m in v1["markets"]:
        if m.get("event_id"):
            markets_by_event.setdefault(m["event_id"], []).append(m)
    prices_by_event = {}
    for mp in v1["model_prices"]:
        if mp.get("event_id"):
            prices_by_event.setdefault(mp["event_id"], []).append(mp)
    wagers_by_event = {}
    for w in v1["wagers"]:
        if w.get("event_id"):
            wagers_by_event.setdefault(w["event_id"], []).append(w["wager_id"])
    rec_auth = _rec_authority(v1)
    players_by_id = {pid: p for pid, p in player_profiles.items()}
    events_out = {}
    for ev in v1["events"]:
        eid = ev["event_id"]
        pk = str((ev.get("source_ids") or {}).get(EVENT_SOURCE) or "")
        d, g = slate_games.get(pk, (None, None))
        home_id, away_id = ev.get("home_participant"), ev.get("away_participant")
        part_by_id = {p["participant_id"]: p for p in ev["participants"]}
        participants = [{"participant_id": p_id, "display_name": part_by_id[p_id]["display_name"], "home_away": ha, "path": R.team_path(p_id)}
                        for p_id, ha in ((home_id, "HOME"), (away_id, "AWAY")) if p_id in part_by_id]
        matchup, players, lineups, venue, notes, ext = [], [], [], None, [], {}
        if g is not None:
            obs = {}
            for side in ("away", "home"):
                abbr = _norm_abbr((g.get(side) or {}).get("abbr"))
                obs[side] = _slate_game_obs(ctx, g, d, side, teams[abbr], players_by_id)
            for slug in ("projected_runs", "model_win_probability", "slate_season_rpg", "slate_last7_rpg", "slate_last15_rpg", "wrc_plus_proxy",
                         "offense_baseline_adj", "opp_quality_adj", "bullpen_xfip", "bullpen_era", "starter_xfip", "starter_xera",
                         "starter_k_pct", "starter_bb_pct"):
                h, a = obs["home"].get(slug), obs["away"].get(slug)
                if h is None and a is None:
                    continue
                note = "probable starters (PLAYER observations)" if slug.startswith("starter_") else None
                if slug == "wrc_plus_proxy":
                    note = LIM["wrc_proxy"]
                matchup.append({"metric_id": mid(slug), "name": ctx.specs[slug]["name"], "home": h, "away": a, "note": note})
            for side in ("away", "home"):
                abbr = _norm_abbr((g.get(side) or {}).get("abbr"))
                ts = g.get(f"{side}TeamStats") or {}
                lineups.append({"team_id": teams[abbr]["participant_id"], "team": abbr, "status": ts.get("lineupStatus"),
                                "confirmed": ts.get("lineupConfirmed"), "official": ts.get("lineupConfirmedOfficial"), "source": ts.get("lineupSource"),
                                "batters_expected": ts.get("lineupBattersExpected"), "batters_found": ts.get("lineupBattersFound"),
                                "batters_resolved": ts.get("lineupBattersResolved"), "data_quality": ts.get("lineupDataQuality"),
                                "reason": ts.get("lineupStatusReason"), "lineup_adj": ts.get("lineupAdj"), "lineup_woba_delta": ts.get("lineupWOBADelta"),
                                "batting_order_published": False})
            seen_p = set()
            for pid_raw, name, abbr, role, _x in _slate_players({"games": [g]}):
                if pid_raw in seen_p or pid_raw not in player_profiles:
                    continue
                seen_p.add(pid_raw)
                players.append({"participant_id": player_pid(pid_raw), "display_name": name, "team_id": teams[abbr]["participant_id"],
                                "role": role, "path": R.player_path(player_pid(pid_raw))})
            # never in slate (batting) order: the audit rates batting orders UNAVAILABLE, so the list is sorted
            players.sort(key=lambda p: (p["team_id"], p["role"] != "starting pitcher", p["display_name"], p["participant_id"]))
            pf = g.get("park") or {}
            venue = {"name": g.get("venue") or pf.get("name"), "park_factor": pf.get("parkFactor"), "dome": pf.get("dome"),
                     "park_adjustment_formula": "park_adj = (pf-100)/100*0.5 (compute_projections)"}
            ext["model_inputs"] = _model_inputs(g, d)
        else:
            notes.append("No authoritative slate game for this event: matchup inputs unavailable.")
        notes.extend(["Weather values are not stored (dome flag only); injuries and umpires are not stored.",
                      "Batting orders are not published (audit: UNAVAILABLE in slate); lineup status only.",
                      "Projections are research evidence, not bets; see the v1 recommendations for the repository's own decisions."])
        all_mkts = sorted(markets_by_event.get(eid, []), key=lambda m: (m["market_family"], m["kalshi_ticker"]))
        mkts = [m for m in all_mkts if not is_prop_market(m)]
        props = [m for m in all_mkts if is_prop_market(m)]
        fams = {}
        for m in props:
            fams[m["market_family"]] = fams.get(m["market_family"], 0) + 1
        ext["player_prop_markets"] = {"count": len(props), "by_family": dict(sorted(fams.items())),
                                      "where": "v1 markets.json (this event_id) and the player profiles; omitted here to keep the document small"}
        mh = _market_history(ctx, ev, all_mkts)
        if mh is not None:
            ctx.docs.append(mh)
        pser = _projection_series(ctx, ev, all_mkts)
        links = [R.link(rel="TEAM", target_kind="entity_profile", label=p["display_name"], target_id=p["participant_id"], path=p["path"])
                 for p in participants]
        if mh is not None:
            links.append(R.link(rel="MARKET_HISTORY", target_kind="market_history", label="Kalshi price history (snapshot series)",
                                target_id=eid, path=R.market_history_path(eid)))
        for m, ser in pser:
            ctx.docs.append(ser)
            links.append(R.link(rel="SERIES", target_kind="time_series", label=f"model P(YES) over runs: {m['kalshi_ticker']}",
                                target_id=ser["series_id"], path=R.series_path(ser["series_id"])))
        er = R.event_research(
            sport=SPORT, run_id=ctx.run_id, generated_at=ctx.gen, event=ev, quality=_quality(ctx, "event", as_of=(g or {}).get("lineupCheckedAt")),
            participants=participants, matchup=matchup, players=players,
            projections=[_projection(mp, rec_auth) for mp in sorted(prices_by_event.get(eid, []), key=lambda r: r["market_id"])],
            distributions=[], markets=[R.market_ref(m) for m in mkts], market_history_path=R.market_history_path(eid) if mh is not None else None,
            context={"injuries": [], "lineups": lineups, "weather": None, "venue": venue, "notes": notes},
            wagers=sorted(wagers_by_event.get(eid, [])), links=links, extensions=ext)
        events_out[eid] = er
        ctx.docs.append(er)
    return events_out


# ---------------------------------------------------------------------- registry-only aggregates

def _wager_aggregates(v1, settlements):
    by_family = {}
    overall = {"wagers": 0, "settled": 0, "won": 0, "lost": 0, "push": 0, "void": 0, "clv_n": 0, "clv_sum": 0.0}
    res_by_wager = {s["wager_id"]: s["result"] for s in settlements}
    for w in v1["wagers"]:
        ext = w.get("extensions") or {}
        fam = str(ext.get("market_family") or "unknown")
        for agg in (overall, by_family.setdefault(fam, {"wagers": 0, "settled": 0, "won": 0, "lost": 0, "push": 0, "void": 0, "clv_n": 0, "clv_sum": 0.0})):
            agg["wagers"] += 1
            res = res_by_wager.get(w["wager_id"])
            if res:
                agg["settled"] += 1
                agg[{"WON": "won", "LOST": "lost", "PUSH": "push", "VOID": "void"}.get(res, "void")] += 1 if res in ("WON", "LOST", "PUSH", "VOID") else 0
            if ext.get("clv_pct_points") is not None and ext.get("clv_convention") == "POSITIVE_IS_GOOD_V1":
                agg["clv_n"] += 1
                agg["clv_sum"] += float(ext["clv_pct_points"])

    def fin(a):
        dec = a["won"] + a["lost"]
        return {"wagers": a["wagers"], "settled": a["settled"], "won": a["won"], "lost": a["lost"], "push": a["push"], "void": a["void"],
                "win_rate": _div(a["won"], dec), "win_rate_sample_tier": sample_tier(dec),
                "clv_n": a["clv_n"], "clv_mean_pct_points": _div(a["clv_sum"], a["clv_n"], 4), "clv_sample_tier": sample_tier(a["clv_n"])}
    return fin(overall), {k: fin(v) for k, v in sorted(by_family.items())}


# ---------------------------------------------------------------------- capabilities

def _capabilities(ctx, files_present, evidence):
    """files_present: set of kinds; evidence: dict name -> list of app paths (only real published files)."""
    def cap(name, status, summary, *, ev=None, lims=None, reasons=None, coverage=None, since=None, metrics=None, windows=None, splits=None, etypes=None):
        ev = [p for p in (ev or []) if p][:EVIDENCE_CAP]
        if status in ("VERIFIED", "PARTIAL") and not ev:
            return R.capability(capability=name, status="UNAVAILABLE", summary=summary + " (nothing to show in this publication)",
                                reasons=reasons or ["no published file supports it in this publication"])
        return R.capability(capability=name, status=status, summary=summary, evidence=ev if status in ("VERIFIED", "PARTIAL") else [],
                            limitations=lims, reasons=reasons, coverage=coverage, since=since, metrics=metrics, windows=windows, splits=splits,
                            entity_types=etypes)
    rc_seasons = sorted(ctx.inputs["research_cache"])
    rc_cov = f"research cache seasons {rc_seasons[0]}..{rc_seasons[-1]}" if rc_seasons else None
    since_rc = min((g["date"] for yr in rc_seasons for g in ctx.inputs["research_cache"][yr]["games"].values()), default=None)
    slate_dates = [d for d, _ in ctx.inputs["slates"]]
    team_metrics = sorted(mid(s) for s in ctx.used if ctx.specs[s]["etype"] == "TEAM")
    player_metrics = sorted(mid(s) for s in ctx.used if ctx.specs[s]["etype"] == "PLAYER")
    sc = ctx.inputs["statcast"]
    out = [
        cap("team_profiles", "VERIFIED", "All 30 MLB teams: season results and rates (research cache), current snapshots, form windows, game logs.",
            ev=evidence.get("team"), coverage=rc_cov, since=since_rc, etypes=["TEAM"]),
        cap("player_profiles", "PARTIAL", "Players on the current slate (probable starters, confirmed-lineup batters) or named by a current market.",
            ev=evidence.get("player"), lims=[LIM["player_snapshot"], LIM["player_logs"]], etypes=["PLAYER"],
            coverage=f"{evidence.get('player_count', 0)} players"),
        cap("event_research", "VERIFIED", "One document per v1 event: slate model inputs, matchup rows, projections, markets, price history.",
            ev=evidence.get("event"), coverage=f"slate {ctx.inputs['date']}", etypes=["EVENT"]),
        cap("team_metrics", "VERIFIED", "Season results/rates from the 5-season research cache and current daily snapshots, ranked over 30 teams.",
            ev=evidence.get("team"), lims=["Snapshot metrics (bullpen, xwOBA, opponent quality) have history only inside the 63 daily slates (PARTIAL)."],
            metrics=team_metrics, coverage=rc_cov, since=since_rc, etypes=["TEAM"]),
        cap("player_metrics", "PARTIAL", "Savant season snapshot, slate starter blocks, Statcast window aggregates.", ev=evidence.get("player"),
            lims=[LIM["player_snapshot"]], metrics=player_metrics, etypes=["PLAYER"]),
        cap("team_game_logs", "VERIFIED", f"Per-team game logs (last {TEAM_GAME_CAP} games) with opponent, score and result; per-game runs series.",
            ev=evidence.get("team_series"), lims=[f"Capped at the last {TEAM_GAME_CAP} games per team in profiles and series; full seasons are aggregated.",
                                                 LIM["research_cache"]], coverage=rc_cov, since=since_rc, etypes=["TEAM"]),
        cap("player_game_logs", "PARTIAL", "Pitcher appearance lines (research cache) and Statcast per-game aggregates for profiled players.",
            ev=evidence.get("player_series"), lims=[LIM["player_logs"], f"Capped at the last {PLAYER_GAME_CAP} games per player."], etypes=["PLAYER"]),
        cap("historical_results", "VERIFIED", "Final scores, winners and opponents for every completed regular-season game in the research cache.",
            ev=evidence.get("team"), coverage=rc_cov, since=since_rc),
        cap("opponents", "VERIFIED", "Opponent ids on every game reference and series point; opponents list per team.", ev=evidence.get("team"),
            coverage=rc_cov, since=since_rc),
        cap("opponent_adjustment", "VERIFIED", "Opponent-quality adjustment (opposing-starter xERA): current snapshot ranked over 30 teams and per-slate history.",
            ev=evidence.get("opp_adj"), lims=[LIM["opp_adj"]],
            metrics=[mid(x) for x in ("opp_quality_adj", "opp_starter_xera_avg") if x in ctx.used]),
        cap("schedule_strength", "UNAVAILABLE", "No team-level strength of schedule is stored.",
            reasons=["Audit: UNAVAILABLE as a team-level SOS; only the rolling opposing-starter xERA exists (published under opponent_adjustment)."]),
        cap("recent_form_windows", "VERIFIED", "L5/L7/L10 runs mean/median and team-total overs from data/team_offense_form.json, ranked; analysis-only.",
            ev=evidence.get("form"), lims=[LIM["form"]], windows=sorted({w for s in ("form_runs_mean",) for w in ctx.used.get(s, {}).get("windows", ())}),
            metrics=[mid(x) for x in ("form_runs_mean", "form_runs_median", "form_overs_pct") if x in ctx.used]),
        cap("usage", "PARTIAL", "Bullpen recent usage (event model inputs, team profiles) and starter pitches/outs per appearance.",
            ev=evidence.get("usage"), lims=[LIM["usage"]]),
        cap("lineups", "PARTIAL", "Lineup status / confirmation per side in event research context.", ev=evidence.get("event"), lims=[LIM["lineups"]]),
        cap("injuries", "UNAVAILABLE", "No injury or availability data is stored.", reasons=["Audit: nothing beyond lineup confirmation + bullpen fatigue."]),
        cap("matchup_metrics", "PARTIAL", "Home/away matchup rows of the slate's model inputs per event (team rates, bullpen, starters, projections).",
            ev=evidence.get("event"), lims=[LIM["matchup"]]),
        cap("projection_distributions", "RESEARCH", "Not published: no quantiles or samples are persisted.",
            lims=["Poisson pmf only implied in build_market_ledger.py; NB-vs-Poisson shadow cells (edgelab/mlb_rsch_0011_shadow_evaluations) are research; "
                  "recent shadow rows FAILED_ISOLATED. No samples/quantiles persisted."]),
        cap("raw_projections", "VERIFIED", "Projected runs per side and the v1 model prices as projection references.", ev=evidence.get("event")),
        cap("market_prices", "VERIFIED", "Current Kalshi quotes as v1 market references (events, team and player profiles).", ev=evidence.get("market_prices")),
        cap("market_price_history", "PARTIAL", "Per-event Kalshi quote series with checkpoint tags and closing quotes.", ev=evidence.get("market_history"),
            lims=[LIM["market_history"]], coverage=f"observations of {ctx.inputs['date']}"),
        cap("advanced_stats", "PARTIAL", "xwOBA (team, batter), xERA/xFIP (pitchers), Statcast per-game velocity / EV / LA / xwOBA / whiff.",
            ev=evidence.get("player") or evidence.get("team"), lims=["Only 48 game-days of Statcast.", LIM["statcast"]],
            coverage=f"Statcast index {sc['index_start']}..{sc['index_end']}" if sc.get("index_start") else None),
        cap("situational_splits", "PARTIAL", "Home/away splits (win%, runs per game) for the latest research-cache season.", ev=evidence.get("team"),
            lims=["No game-state/leverage splits precomputed; handedness splits only per pitch in Statcast (not published as splits)."],
            splits=["home_away"]),
        cap("player_props", "RESEARCH",
            "Every prop market carries extensions.player_prop (mlb.player_prop.v1): a research projection or the reason "
            "it cannot be priced. Research projections are not edges and never enter model prices or recommendations.",
            lims=["Pitcher K/outs (lib/research/pitcher_prop_projection.py): better than the incumbent engine out of sample "
                  "but worse than Kalshi (2026 settled markets: K Brier 0.166 vs 0.157, outs 0.259 vs 0.239; postseason outs "
                  "0.313 vs 0.199, n=25) -- data/edgelab/analytics/mlb_pitcher_prop_calibration_2026-10-07.json.",
                  "Hitter H/TB/H+R+RBI/RBI: hitter engine snapshots, at parity with Kalshi at best (MLB-RSCH-0028); "
                  "published only for confirmed-lineup starters with a pregame snapshot.",
                  "Stolen bases, home runs, runs, hits/ER/walks allowed: NO_MODEL_SUPPORT.",
                  "Prop prices/settlements are VERIFIED in the v1 markets."]),
        cap("team_props", "VERIFIED", "Team-total markets (KXMLBTEAMTOTAL ladder; ledger TT rows) as market references.", ev=evidence.get("team_props")),
        cap("game_markets", "VERIFIED", "ML, run line, total, F5, NRFI/YRFI, winning margin as market references with model prices.", ev=evidence.get("game_markets")),
        cap("play_by_play", "PARTIAL", "Statcast pitch log aggregated per player per game (not the pitch rows).", ev=evidence.get("player_statcast"),
            lims=["Statcast pitch log only (643 games, 2026-08-11 -> 2026-09-27); no PBP for games before 2026-08-11.",
                  "Published as per-player per-game aggregates, not pitch-level rows."]),
        cap("weather", "UNAVAILABLE", "No weather values are stored.", reasons=["Audit: data/weather.json = dome/notes only; slate stores 0 weather keys."]),
        cap("venue_effects", "VERIFIED", "Static park factor per home park (slate) on team profiles and event venue context.", ev=evidence.get("venue"),
            lims=["Single static factor per park (source api/slate.js)."]),
        cap("calibration", "VERIFIED", "Probability-bin calibration of REAL bankroll-counting wagers (metric registry extensions).",
            ev=evidence.get("calibration"), lims=["Descriptive; most bins are below n=100."]),
        cap("historical_accuracy", "VERIFIED", "REAL wager outcomes by market family plus the postmortem inventory (metric registry extensions).",
            ev=evidence.get("wagers")),
        cap("clv", "VERIFIED", "Ledger CLV of REAL wagers (POSITIVE_IS_GOOD_V1) by market family (metric registry); per-wager values in v1 wagers.json.",
            ev=evidence.get("wagers"), lims=["clv null on ~21% of ledger rows."]),
        cap("wager_history", "VERIFIED", "REAL / REAL_PROBE wagers are the v1 wagers.json; event research lists the wager ids per event.",
            ev=evidence.get("wagers")),
        cap("rankings", "VERIFIED", "Full 30-team universes per metric and window.", ev=evidence.get("ranking")),
        cap("time_series", "VERIFIED", "Per-game team runs, slate-date opponent adjustment, pitcher/Statcast per-game, model P(YES) per run.",
            ev=evidence.get("series")),
        cap("comparisons", "VERIFIED", "Every ranked observation carries rank, universe, league average/median, best and worst; events carry home/away matchup rows.",
            ev=evidence.get("team")),
        cap("search", "VERIFIED", "Teams, players, events, metrics and rankings.", ev=[R.app_path(R.SEARCH_NAME)]),
    ]
    split_dims = [{"dimension": "home_away", "values": ["HOME", "AWAY"], "status": "VERIFIED"}]
    windows = []
    for yr in rc_seasons:
        windows.append(R.window("SEASON", label=f"{yr} season"))
    for n in (5, 7, 10):
        windows.append(R.window("LAST_N", n=n))
    notes = [
        "RESEARCH (exists in the repository, not published here): hitter/pitcher prop probabilities (edgelab/hitter_projection_snapshots), "
        "NB distribution shadows (edgelab/mlb_rsch_0011_shadow_evaluations), Pinnacle historical (research_cache/pinnacle_historical, ~37 dates/season), "
        "replay scoring (edgelab/scored_replay_runs).",
        "UNAVAILABLE: injuries, weather values, umpires, play-by-play before 2026-08-11, player season-stat history, batting orders in slate, "
        "team schedule strength, postseason 2026.",
        f"Slate dates read: {slate_dates[0]}..{slate_dates[-1]} ({len(slate_dates)})" if slate_dates else "No slate dates read.",
    ]
    if ctx.inputs.get("_unresolved_market_players"):
        notes.append(f"{len(ctx.inputs['_unresolved_market_players'])} current-market player name(s) did not resolve to exactly one MLBAM id on that team "
                     "and have no profile.")
    return R.capability_manifest(sport=SPORT, run_id=ctx.run_id, generated_at=ctx.gen, capabilities=out, audit_date=AUDIT_DATE,
                                 split_dimensions=split_dims, windows=windows, notes=notes)


# ---------------------------------------------------------------------- top level

def data_as_of(inputs):
    stamps = []
    for o in inputs["observations"]:
        if o.get("capturedAt"):
            stamps.append(timeutil.to_iso(o["capturedAt"]))
    for r in inputs["model_evaluations"]:
        if r.get("createdAt"):
            stamps.append(timeutil.to_iso(r["createdAt"]))
    for _d, s in inputs["slates"]:
        for k in ("_officialRunAt", "lastRerunAt"):
            if s.get(k):
                stamps.append(timeutil.to_iso(s[k]))
    for doc, k in ((inputs["offense_form"], "generatedAt"), (inputs["bullpen"], "fetchedAt"), (inputs["savant"], "fetchedAt"),
                   (inputs["oppquality"], "fetchedAt")):
        if doc and doc.get(k):
            stamps.append(timeutil.to_iso(doc[k]))
    for yr in inputs["research_cache"]:
        for g in inputs["research_cache"][yr]["games"].values():
            if g["start"]:
                stamps.append(timeutil.to_iso(g["start"]))
    if inputs["statcast"].get("index_end"):
        stamps.append(_day(inputs["statcast"]["index_end"]))
    return max(stamps, key=timeutil.parse_ts) if stamps else None


def build_explorer(inputs, *, run_id, generated_at):
    """Pure: inputs (from :func:`load_inputs`) -> the explorer documents."""
    ctx = _Ctx(inputs, run_id, generated_at)
    v1 = inputs["v1"]
    published_events = {ev["event_id"] for ev in v1["events"]}
    teams = _team_universe(inputs)
    universe = player_universe(inputs)
    v1_markets_by_ticker = {m["kalshi_ticker"]: m for m in v1["markets"]}
    team_parts = build_team_layer(ctx, teams, published_events)
    player_parts = build_player_layer(ctx, teams, universe, published_events, v1_markets_by_ticker)
    events = build_event_layer(ctx, teams, player_parts, published_events)

    # team profiles
    events_by_team = {}
    for ev in v1["events"]:
        for p in ev["participants"]:
            events_by_team.setdefault(p["participant_id"], []).append(ev)
    mp_by_event = {}
    for mp in v1["model_prices"]:
        if mp.get("event_id"):
            mp_by_event.setdefault(mp["event_id"], []).append(mp)
    rec_auth = _rec_authority(v1)
    players_by_team = {}
    for player_id, pp in player_parts.items():
        if pp["team"]:
            players_by_team.setdefault(pp["team"]["participant_id"], []).append(pp)
    team_quality = R.quality(status="VERIFIED", source="data/research_cache, data/*.json snapshots, data/team_offense_form.json, data/slates",
                             generated_at=ctx.gen, production=False, data_as_of=data_as_of(inputs), methodology_version=RESEARCH_EXPORT_VERSION,
                             coverage="all 30 MLB teams", limitations=[LIM["research_cache"], LIM["snapshot"]])
    evidence = {"team": [], "player": [], "event": [], "series": [], "ranking": [], "team_series": [], "player_series": [], "player_statcast": [],
                "market_history": [], "market_prices": [], "team_props": [], "game_markets": [], "form": [], "opp_adj": [], "usage": [], "venue": []}
    for abbr in sorted(teams):
        t = teams[abbr]
        pid = t["participant_id"]
        parts = team_parts[abbr]
        evs = sorted(events_by_team.get(pid, []), key=lambda e: (e["start_time_utc"], e["event_id"]))
        games = list(parts["games"])
        for ev in evs:
            opp = next((p for p in ev["participants"] if p["participant_id"] != pid), None)
            games.append(R.game_ref(event_id=ev["event_id"], start_time_utc=ev["start_time_utc"], status=ev["status"],
                                    opponent_id=opp["participant_id"] if opp else None, opponent_name=opp["display_name"] if opp else None,
                                    home_away="HOME" if ev.get("home_participant") == pid else "AWAY", competition="MLB",
                                    path=R.event_path(ev["event_id"])))
        team_markets = sorted((m for m in v1["markets"] if m.get("participant_id") == pid and not is_prop_market(m)),
                              key=lambda m: (m["market_family"], m["kalshi_ticker"]))
        projections = [_projection(mp, rec_auth) for ev in evs for mp in sorted(mp_by_event.get(ev["event_id"], []), key=lambda r: r["market_id"])
                       if mp.get("market_id") in {m["market_id"] for m in team_markets}]
        opponents = []
        for opp_abbr, eids in sorted(parts["opponents"].items()):
            if opp_abbr in teams:
                opponents.append({"participant_id": teams[opp_abbr]["participant_id"], "display_name": teams[opp_abbr]["display_name"],
                                  "event_ids": sorted(set(eids)), "path": R.team_path(teams[opp_abbr]["participant_id"])})
        links = [R.link(rel="SERIES", target_kind="time_series", label=ctx.specs[s["metric_id"].split(".", 1)[1]]["name"], target_id=s["series_id"],
                        path=s["path"]) for s in parts["series"]]
        links += [R.link(rel="EVENT_RESEARCH", target_kind="event_research", label="current event", target_id=ev["event_id"], path=R.event_path(ev["event_id"]))
                  for ev in evs]
        links.append(R.link(rel="CAPABILITIES", target_kind="capability_manifest", label="what MLB can show", path=R.app_path(R.CAPABILITIES_NAME)))
        team_players = [{"participant_id": pp["entity"]["participant_id"], "display_name": pp["entity"]["display_name"],
                         "role": ", ".join(pp["ext"]["roles"]), "path": R.player_path(pp["entity"]["participant_id"])}
                        for pp in sorted(players_by_team.get(pid, []), key=lambda p: (p["entity"]["display_name"], p["entity"]["participant_id"]))]
        market_refs = [R.market_ref(m) for m in team_markets]
        ext = dict(parts["ext"])
        prof = None
        for attempt in range(3):
            prof = R.entity_profile(
                sport=SPORT, run_id=run_id, generated_at=ctx.gen, entity=t, entity_type="TEAM", quality=team_quality,
                season=sorted(inputs["research_cache"])[-1] if inputs["research_cache"] else None, league="MLB", metrics=parts["metrics"],
                splits=parts["splits"], series=parts["series"], rankings=parts["rankings"], games=games, players=team_players,
                opponents=opponents, markets=market_refs, projections=projections, availability=[], links=links, extensions=ext)
            if len(json.dumps(prof, separators=(",", ":"))) <= PROFILE_BUDGET:
                break
            # over budget (e.g. a doubleheader day): the event research documents carry the same refs
            if attempt == 0:
                projections = []
                ext["omitted"] = ["projections (see the event research documents)"]
            else:
                market_refs = []
                ext["omitted"] = ext["omitted"] + ["markets (see the event research documents / v1 markets.json)"]
        ctx.docs.append(prof)
        path = R.team_path(pid)
        evidence["team"].append(path)
        if parts["series"]:
            evidence["team_series"].append(parts["series"][0]["path"])
        if parts["ext"].get("form"):
            evidence["form"].append(path)
        if any(o["metric_id"] == mid("opp_quality_adj") for o in parts["metrics"]):
            evidence["opp_adj"].append(path)
        if parts["ext"].get("bullpen_recent_usage"):
            evidence["usage"].append(path)
        if parts["ext"].get("home_park"):
            evidence["venue"].append(path)
        if team_markets:
            evidence["market_prices"].append(path)
            if any(m["market_family"] == "team_total" for m in team_markets):
                evidence["team_props"].append(path)
            if any(m["market_family"] in ("game_result", "game_total", "winning_margin", "first_inning_run", "inning_result") for m in team_markets):
                evidence["game_markets"].append(path)

    # player profiles
    for player_id in sorted(player_parts):
        pp = player_parts[player_id]
        team = pp["team"]
        prof = R.entity_profile(
            sport=SPORT, run_id=run_id, generated_at=ctx.gen, entity=pp["entity"], entity_type="PLAYER", quality=pp["quality"],
            season=(inputs["date"] or "")[:4] or None, league="MLB",
            team={"participant_id": team["participant_id"], "display_name": team["display_name"], "short_name": team.get("short_name"),
                  "path": R.team_path(team["participant_id"])} if team else None,
            metrics=pp["metrics"], series=pp["series"], games=pp["games"], markets=pp["markets"], availability=[], links=pp["links"],
            extensions=pp["ext"])
        ctx.docs.append(prof)
        path = R.player_path(pp["entity"]["participant_id"])
        evidence["player"].append(path)
        if pp["series"]:
            evidence["player_series"].append(path)
        if pp["ext"].get("statcast_games"):
            evidence["player_statcast"].append(path)
        if pp["ext"].get("pitching_log"):
            evidence["usage"].append(path)
        if pp["markets"]:
            evidence["market_prices"].append(path)

    for eid, er in sorted(events.items()):
        path = R.event_path(eid)
        evidence["event"].append(path)
        if er["market_history_path"]:
            evidence["market_history"].append(er["market_history_path"])
        if er["markets"]:
            evidence["market_prices"].append(path)
            fams = {m["market_family"] for m in er["markets"]}
            if "team_total" in fams:
                evidence["team_props"].append(path)
            if fams & {"game_result", "game_total", "winning_margin", "first_inning_run", "inning_result"}:
                evidence["game_markets"].append(path)
        if (er["extensions"].get("model_inputs") or {}).get("park"):
            evidence["venue"].append(path)
        if er["extensions"].get("model_inputs"):
            evidence["usage"].append(path)
    for d in ctx.docs:
        if d["kind"] == "ranking":
            evidence["ranking"].append(R.ranking_path(d["ranking_id"]))
        elif d["kind"] == "time_series":
            evidence["series"].append(R.series_path(d["series_id"]))
            if d["entity_type"] == "PLAYER":
                evidence["player_series"].append(R.series_path(d["series_id"]))
    # registry-only aggregates: wagers, CLV, calibration
    wager_overall, wager_by_family = _wager_aggregates(v1, v1["settlements"])
    registry_ext = {}
    if v1["wagers"]:
        registry_ext["wager_win_rate"] = {"overall": wager_overall, "by_market_family": wager_by_family,
                                          "postmortems": {"dates": len(inputs["postmortem_dates"]),
                                                          "first": inputs["postmortem_dates"][0] if inputs["postmortem_dates"] else None,
                                                          "last": inputs["postmortem_dates"][-1] if inputs["postmortem_dates"] else None,
                                                          "path": "data/edgelab/postmortems/<date>/postmortem.{json,md}"},
                                          "v1_paths": ["wagers.json", "settlements.json", "performance.json"]}
        registry_ext["wager_clv"] = {"overall": {k: wager_overall[k] for k in ("clv_n", "clv_mean_pct_points", "clv_sample_tier")},
                                     "by_market_family": {k: {x: v[x] for x in ("clv_n", "clv_mean_pct_points", "clv_sample_tier")}
                                                          for k, v in wager_by_family.items() if v["clv_n"]},
                                     "convention": "POSITIVE_IS_GOOD_V1 (percentage points)", "v1_paths": ["wagers.json", "performance.json"]}
    bins = inputs["calibration_bins"] or []
    if bins:
        registry_ext["model_calibration_error"] = {"bins": [dict(b, sampleTier=sample_tier(b.get("sampleSize"))) for b in bins],
                                                   "population": "REAL bankroll-counting wagers (scripts/build_wager_research_db.py build_calibration_bins)",
                                                   "source_file": "data/research/calibration_bins.json"}
    for slug in registry_ext:
        ctx.mark(slug)
    if registry_ext.get("wager_win_rate"):
        evidence["wagers"] = [R.app_path(R.METRICS_NAME)] + sorted({R.event_path(e) for e, er in events.items() if er["wagers"]})
    if registry_ext.get("model_calibration_error"):
        evidence["calibration"] = [R.app_path(R.METRICS_NAME)]

    # metric registry
    as_of = data_as_of(inputs)
    metrics = []
    for slug in sorted(ctx.used):
        spec = ctx.specs[slug]
        u = ctx.used[slug]
        qkey = {"research_cache": "research_cache", "snapshot": "snapshot", "slate": "slate", "form": "form", "player_snapshot": "player_snapshot",
                "player_logs": "player_logs", "statcast": "statcast", "model_evals": "model_evals", "wagers": "wagers",
                "calibration": "calibration"}[spec["src"]]
        q = _quality(ctx, qkey, as_of=as_of)
        hist = None
        if spec["src"] == "research_cache" and inputs["research_cache"]:
            hist = min(g["date"] for yr in inputs["research_cache"] for g in inputs["research_cache"][yr]["games"].values())
        elif spec["src"] == "statcast":
            hist = inputs["statcast"].get("index_start")
        elif spec["src"] == "slate" and inputs["slates"]:
            hist = inputs["slates"][0][0]
        metrics.append(R.metric(
            sport=SPORT, slug=slug, name=spec["name"], short_name=spec["short"], description=spec["desc"], entity_type=spec["etype"],
            category=spec["cat"], subcategory=spec["sub"], unit=spec["unit"], stat_type=spec["stype"], source=q["source"], quality=q,
            freshness="UNKNOWN", higher_is_better=spec["hib"],
            comparison_universe="MLB teams (30)" if u["ranked"] else None,
            supports=R.supports(rank=u["ranked"], percentile=u["ranked"], time_series=u["series"], windows=len(u["windows"]) > 1,
                                splits=bool(u["splits"]), opponent_adjustment=slug in ("opp_quality_adj", "offense_baseline_adj"),
                                home_away="home_away" in u["splits"]),
            windows=sorted(u["windows"]), splits=sorted(u["splits"]), methodology_version=RESEARCH_EXPORT_VERSION, historical_start=hist,
            update_frequency={"research_cache": "one-off (manual research workflow)", "snapshot": "3x daily (fetch-slate)", "slate": "3x daily + rechecks",
                              "form": "daily", "player_snapshot": "3x daily (fetch-slate)", "player_logs": "one-off", "statcast": "daily (10:00 UTC)",
                              "model_evals": "per production run", "wagers": "per ledger update", "calibration": "daily (07:15 UTC)"}[spec["src"]],
            known_limitations=spec["lims"], extensions=registry_ext.get(slug)))
    ctx.docs.append(R.metric_registry(sport=SPORT, run_id=run_id, generated_at=ctx.gen, metrics=metrics))

    kinds = {d["kind"] for d in ctx.docs}
    evidence["player_count"] = len(player_parts)
    ctx.docs.append(_capabilities(ctx, kinds, {k: (sorted(dict.fromkeys(v)) if isinstance(v, list) else v) for k, v in evidence.items()}))

    # search index
    entries = []
    for abbr in sorted(teams):
        t = teams[abbr]
        entries.append(R.search_entry(id=t["participant_id"], kind="TEAM", label=t["display_name"], path=R.team_path(t["participant_id"]), sport=SPORT,
                                      aliases=[abbr] + ([k for k, v in ABBR_NORMALIZE.items() if v == abbr]), league="MLB",
                                      season=sorted(inputs["research_cache"])[-1] if inputs["research_cache"] else None))
    for player_id in sorted(player_parts):
        pp = player_parts[player_id]
        e = pp["entity"]
        entries.append(R.search_entry(id=e["participant_id"], kind="PLAYER", label=e["display_name"], path=R.player_path(e["participant_id"]),
                                      sport=SPORT, secondary=", ".join(pp["ext"]["roles"]), aliases=[e["short_name"]] if e.get("short_name") else [],
                                      team=e["metadata"].get("team"), position=e["metadata"].get("position"), league="MLB"))
    for eid, er in sorted(events.items()):
        ev = er["event"]
        names = {p["participant_id"]: p for p in ev["participants"]}
        away, home = names.get(ev.get("away_participant")), names.get(ev.get("home_participant"))
        label = f"{away['display_name']} @ {home['display_name']}" if away and home else " vs ".join(p["display_name"] for p in ev["participants"])
        entries.append(R.search_entry(id=eid, kind="EVENT", label=label, path=R.event_path(eid), sport=SPORT,
                                      secondary=f"{ev['start_time_utc'][:10]} {ev.get('venue') or ''}".strip(),
                                      aliases=[f"{away['short_name']}@{home['short_name']}"] if away and home else [], league="MLB", season=ev.get("season")))
    for m in metrics:
        entries.append(R.search_entry(id=m["metric_id"], kind="METRIC", label=m["name"], path=R.app_path(R.METRICS_NAME), sport=SPORT,
                                      secondary=m["category"], aliases=[m["short_name"]]))
    for d in ctx.docs:
        if d["kind"] == "ranking":
            spec = ctx.specs[d["metric_id"].split(".", 1)[1]]
            entries.append(R.search_entry(id=d["ranking_id"], kind="RANKING", label=f"{spec['name']} ranking ({d['window']['label']})",
                                          path=R.ranking_path(d["ranking_id"]), sport=SPORT, secondary=d["universe"]["label"], season=d["universe"]["season"]))
    ctx.docs.append(R.search_index(sport=SPORT, run_id=run_id, generated_at=ctx.gen, entries=entries))
    return ctx.docs


def export_explorer(app_root, *, data_root, now=None, commit_sha=None, slate_range=None, seasons=None, statcast_range=None, inputs=None):
    """Load, build and publish ``<app_root>/explorer``. ``now`` defaults to the v1 manifest's generated_at."""
    inputs = inputs or load_inputs(data_root, app_root, slate_range=slate_range, seasons=seasons, statcast_range=statcast_range)
    manifest = inputs["v1"]["manifest"]
    run_id = manifest["run_id"]
    generated_at = timeutil.to_iso(now) if now is not None else manifest["generated_at"]
    docs = build_explorer(inputs, run_id=run_id, generated_at=generated_at)
    as_of = data_as_of(inputs)
    q = R.quality(status="VERIFIED", source="edge-finder-api committed data (main)", generated_at=generated_at, production=True,
                  data_as_of=as_of, methodology_version=RESEARCH_EXPORT_VERSION,
                  coverage=f"slate {inputs['date']}; research cache {','.join(sorted(inputs['research_cache']))}")
    warnings = []
    if not inputs["v1"]["events"]:
        warnings.append(f"no v1 events for {inputs['date']}: team profiles, rankings, series, capabilities, metrics and search only")
    if inputs.get("_unresolved_market_players"):
        warnings.append(f"{len(inputs['_unresolved_market_players'])} market player name(s) unresolved to an MLBAM id (no profile)")
    index = R.publish_explorer(app_root=app_root, sport=SPORT, run_id=run_id, generated_at=generated_at, documents=docs, quality=q,
                               as_of=as_of, commit_sha=commit_sha if commit_sha is not None else manifest.get("commit_sha"),
                               base_manifest_run_id=run_id, warnings=warnings,
                               windows=[R.window("LAST_N", n=n) for n in (5, 7, 10)])
    return index


def refresh_decision(app_root, *, now=None, min_interval_minutes):
    """(due, reason) from ``research.refresh_due``; ``now`` defaults to the v1 manifest's generated_at
    (the time of the v1 export that just ran)."""
    manifest = load_v1(app_root)["manifest"]
    when = timeutil.to_iso(now) if now is not None else manifest["generated_at"]
    return R.refresh_due(app_root, now=when, min_interval_seconds=float(min_interval_minutes) * 60.0)


def _range(start, end):
    return (start, end) if (start or end) else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="the app root the v1 export just published (e.g. app/latest)")
    ap.add_argument("--data-root", default=os.path.join(REPO_ROOT, "data"))
    ap.add_argument("--now", default=None, help="aware ISO timestamp; default: the v1 manifest's generated_at")
    ap.add_argument("--commit-sha", default=None)
    ap.add_argument("--slate-start", default=None)
    ap.add_argument("--slate-end", default=None)
    ap.add_argument("--seasons", default=None, help="comma-separated research-cache seasons (default: all)")
    ap.add_argument("--statcast-start", default=None)
    ap.add_argument("--statcast-end", default=None)
    ap.add_argument("--min-interval-minutes", type=float, default=0,
                    help="skip the rebuild (exit 0, explorer/ untouched) when the published explorer is younger than this and the "
                         "v1 events are unchanged (research.refresh_due); 0 = always rebuild")
    args = ap.parse_args(argv)
    out_root = os.path.abspath(args.out)
    if args.min_interval_minutes > 0:
        try:
            due, reason = refresh_decision(out_root, now=args.now, min_interval_minutes=args.min_interval_minutes)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return 1
        if not due:
            print(json.dumps({"skipped": True, "reason": reason}, indent=2))
            return 0
        print(f"research export due: {reason}", file=sys.stderr)
    try:
        index = export_explorer(out_root, data_root=os.path.abspath(args.data_root), now=args.now, commit_sha=args.commit_sha,
                                slate_range=_range(args.slate_start, args.slate_end),
                                seasons=[s.strip() for s in args.seasons.split(",")] if args.seasons else None,
                                statcast_range=_range(args.statcast_start, args.statcast_end))
    except Exception:  # noqa: BLE001 -- report and exit non-zero; the previous explorer tree is untouched
        traceback.print_exc()
        print("research export FAILED; the previous explorer tree (if any) is untouched", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": index["run_id"], "as_of": index["as_of"], "counts": index["counts"],
                      "bytes": R.tree_bytes(out_root), "warnings": index["warnings"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
