#!/usr/bin/env python3
"""
scripts/research/mlb_pitcher_shadow.py
======================================
W2 Phase 2 (RESEARCH ONLY): prospective PURE shadow collection for MLB starting
pitchers. No promotion, no authority, no staking, no publication to production data.

Arms (all frozen, sports-only inputs):
  champion    lib/research/pitcher_prop_projection.py + frozen params, as-is, on its own
              sports-only regular-season history (scripts/research/mlb_pitcher_workload_joint.Champion)
  challenger  mlb_pitcher_workload_joint_v1 (lib/research/pitcher_workload_joint.py, #283)
  baseline    naive_recent5_v1
Statistics: pitcher_outs, pitcher_batters_faced, pitcher_strikeouts. Each arm's three rows
come from ONE joint distribution (outs -> BF -> K for the challenger), ladders inside the row.

Subcommands
  capture    morning-of and ~T-60m (after lineups) captures of every probable starter
  outcomes   StatsAPI box-score outcomes for captured games that have finished
  reconcile  projection <-> outcome join + exchange-resolution vs sports-outcome mismatch flags
  evaluate   pure_gate compare on matured rows: full eligible population and the pregame
             market-listed cohort, per capture window, clustered by game; alongside it the vendored
             accuracy_scorecard and a DRY-RUN value_scorecard (lib/research/mlb_pitcher_shadow_scorecards.py)
  verify     manifest hash chain + write-once check

Record families under data/shadow_pure/mlb_pitcher/ (append-only, write-once, named by
captured_at, hash-chained in MANIFEST.jsonl):
  projections/<date>/<captured_at>_<window>__<arm>.jsonl  pure_forecast.v1 rows, one file per arm (validated by the
                                                    vendored pure_gate --require-v1 before write)
  market/<date>/<captured_at>.jsonl                 listing + quote snapshot COPIED read-only from
                                                    data/edgelab/{markets,observations}
  runs/<date>/<captured_at>_<window>.json           probable-starter source/timestamps, population
                                                    accounting, exclusions
  outcomes/<date>/<captured_at>.jsonl               sports outcomes (pure_gate outcome format)
  outcome_provenance/<date>/<captured_at>.jsonl     URL, fetched_at, payload sha256, starters
  reconciliation/<date>/<captured_at>.jsonl
  evaluations/<evaluated_at>.json
  scorecards/<evaluated_at>.accuracy_scorecard.json, .value_scorecard.json   (same evaluation run)

Postseason (gameType F/D/L/W): rows are produced but labelled
x_validation_status = BLOCKED_NOT_VALIDATED_POSTSEASON (the challenger is validated for the
regular season only; the champion's postseason path additionally carries a frozen parameter
fitted on exchange-resolution evidence, see docs/research/PITCHER_WORKLOAD_SOURCE_AUDIT.md).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_RESEARCH_SCRIPTS = os.path.join(ROOT, "scripts", "research")
if _RESEARCH_SCRIPTS not in sys.path:
    sys.path.insert(1, _RESEARCH_SCRIPTS)

import mlb_pitcher_workload_joint as W  # noqa: E402  (frozen #283 harness, unchanged)
from lib.research import pitcher_workload_joint as J  # noqa: E402
from lib.research import mlb_pitcher_shadow_scorecards as SC  # noqa: E402  (stdlib-only; vendored scorecards)
from lib.research import pitcher_workload_sources as S  # noqa: E402
from lib.research.mlb_pitcher_shadow_plan import (  # noqa: E402  (stdlib-only; shared with the workflow gate)
    FINAL, NOT_PLAYED, SCHEDULE_URL, capture_dates, iso, missed_windows, parse, parse_schedule, stamp, utcnow,
    window_for)
from lib.research.mlb_pitcher_shadow_plan import existing_windows as _existing_windows  # noqa: E402
from lib.research.mlb_pitcher_shadow_plan import jsonl as _jsonl  # noqa: E402
from lib.research.shadow_pure_store import ShadowStore  # noqa: E402

SHADOW_ROOT = os.path.join(ROOT, "data", "shadow_pure", "mlb_pitcher")
FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"
POSTSEASON_TYPES = ("F", "D", "L", "W")
STATS = W.STATS
ARMS = ("champion", "challenger", "baseline")
STALE_HISTORY_DAYS = 3
EVAL_PAIRS = (("champion", "challenger"), ("baseline", "challenger"), ("baseline", "champion"))
EVENT_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+)$")
MONTHS = {"MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11}
LISTED_FAMILIES = {"pitcher_outs": "pitcher_outs", "pitcher_strikeouts": "pitcher_strikeouts"}
# The capture's source ids (feature_lineage[].source must equal one of them).
SRC_HISTORY = "mlb_statsapi_boxscores_and_statcast_pitch_log"
SRC_SCHEDULE = "mlb_statsapi_schedule_probable_pitcher"


# ----------------------------------------------------------------------------- utils

def sha256_obj(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def norm_name(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", " ").replace("-", " ").split())


def load_gate():
    spec = importlib.util.spec_from_file_location("pure_gate_vendored", os.path.join(ROOT, "tools", "pure_gate", "pure_gate.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def fetch_json(url, timeout=30):
    from lib.edgelab.bullpen_usage import _fetch_json
    return _fetch_json(url, timeout)


# ----------------------------------------------------------------------------- schedule (pure)

# ----------------------------------------------------------------------------- arms

class Arms:
    def __init__(self, data_root):
        with open(W.PARAMS_PATH, encoding="utf-8") as fh:
            self.params = json.load(fh)
        self.sched, self.games, self.index = W.sports_dataset(data_root)
        self.champ = W.Champion(data_root)
        from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR
        self.team_c = TEAM_ID_TO_ABBR
        self.last_date = {}
        for g in self.games:
            self.last_date[g["season"]] = max(self.last_date.get(g["season"], ""), g["date"])
        self.frozen = {
            "challenger": W.frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_workload_joint.py"),
                                         os.path.join(ROOT, "lib", "research", "pitcher_workload_sources.py"), W.PARAMS_PATH]),
            "champion": W.frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_prop_projection.py"), W.CHAMPION_PARAMS]),
            "baseline": W.frozen_hash([os.path.join(ROOT, "scripts", "research", "mlb_pitcher_workload_joint.py"), W.PARAMS_PATH])}
        self.versions = {"challenger": J.MODEL_VERSION, "champion": "pitcher_prop_projection@params_asof_2026-10-07",
                         "baseline": "naive_recent5_v1"}

    def project(self, game, side, as_of):
        """{arm: {stat: (pmf, mean)}, features, observed} or (None, reason)."""
        opp_side = "home" if side == "away" else "away"
        pid = game[side]["probable_id"]
        date = game["official_date"]
        post = game["game_type"] in POSTSEASON_TYPES
        if not pid:
            return None, "no_probable_starter"
        if not post and self.last_date.get(date[:4], "") < iso(parse(date + "T00:00:00Z") - timedelta(days=STALE_HISTORY_DAYS))[:10]:
            return None, "history_stale_regular_season"
        team, opp = S._TEAM.get(game[side]["team_id"]), S._TEAM.get(game[opp_side]["team_id"])
        feat, observed = J.features(self.index, pid, team, opp, date, as_of)
        if feat["n_hist"] < 3:
            return None, "challenger_lt3_prior_starts"
        cp = self.champ.project(pid, date, self.team_c.get(game[opp_side]["team_id"]), postseason=post)
        if cp is None:
            return None, "champion_lt3_prior_starts"
        bp = W.baseline_project(self.index, pid, date, self.params["baseline"], as_of)
        if bp is None:
            return None, "baseline_missing_history_values"
        xp = W.challenger_project(feat, self.params)
        champ_obs = iso(parse(cp["observed"] + "T00:00:00Z") + timedelta(hours=29))
        return {"challenger": xp, "champion": cp, "baseline": bp, "feat": feat,
                "observed": max(x for x in (observed, champ_obs) if x), "history_observed": observed}, None


def validation_status(arm, post):
    if post:
        return "BLOCKED_NOT_VALIDATED_POSTSEASON"
    return {"challenger": "RESEARCH_SHADOW_ACCEPTED_CHALLENGER_REGULAR_SEASON", "champion": "RESEARCH_SHADOW_INCUMBENT",
            "baseline": "RESEARCH_SHADOW_BASELINE"}[arm]


def build_rows(arms, game, side, window, as_of, proj, run_id):
    post = game["game_type"] in POSTSEASON_TYPES
    pid = game[side]["probable_id"]
    sched_at = game["schedule_fetched_at"]
    smax = max(proj["observed"], sched_at)
    sources = [{"source_id": SRC_HISTORY, "class": "sports_only", "max_observed_at": proj["observed"],
                "description": "research_cache starter_workload box scores + Statcast pitch-log lines (regular season)"},
               {"source_id": SRC_SCHEDULE, "class": "sports_only", "max_observed_at": sched_at,
                "uri": SCHEDULE_URL.format(date=game["official_date"]),
                "description": "MLB StatsAPI schedule probable pitcher + lineup-posted flags"}]
    starter_lin = [{"name": "probable_starter_id", "value": pid, "source": SRC_SCHEDULE, "observed_at": sched_at, "class": "sports_only"},
                   {"name": "lineups_posted_both", "value": bool(game["away"]["lineup_posted"] and game["home"]["lineup_posted"]),
                    "source": SRC_SCHEDULE, "observed_at": sched_at, "class": "sports_only"}]
    rows = []
    ch_feats = W.challenger_features(proj["feat"])
    for arm in ARMS:
        p = proj[arm]
        if arm == "challenger":
            lin = W.challenger_lineage(proj["feat"], proj["history_observed"])
        elif arm == "champion":
            lin = [{"name": k, "value": v, "source": SRC_HISTORY, "observed_at": proj["observed"], "class": "sports_only"}
                   for k, v in p["features"].items()]
        else:
            lin = [{"name": "n_last_starts", "value": W.BASELINE_N, "source": SRC_HISTORY, "observed_at": proj["observed"], "class": "sports_only"}]
        lin = lin + starter_lin
        for stat in STATS:
            pmf, mean = p[stat]
            po = W.projection_obj(stat, pmf, mean)
            po["quantiles"] = [{"q": q, "value": J.quantile(pmf, q)} for q in (0.1, 0.25, 0.5, 0.75, 0.9)]
            row = {"schema_version": "pure_forecast.v1", "sport": "MLB", "game_id": game["pk"], "player_id": pid,
                   "statistic": stat, "as_of": as_of, "kickoff": game["kickoff"], "source_max_observed_at": smax,
                   "projection_mode": "PURE_INDEPENDENT", "model_version": arms.versions[arm],
                   "model_frozen_hash": arms.frozen[arm], "conditional_on_playing": True,
                   "participation_probability": None, "projection": po, "sources": sources, "feature_lineage": lin,
                   "x_arm": arm, "x_capture_window": window, "x_run_id": run_id, "x_game_type": game["game_type"],
                   "x_season_phase": "postseason" if post else "regular_season",
                   "x_validation_status": validation_status(arm, post),
                   "x_side": side, "x_team_id": game[side]["team_id"],
                   "x_opponent_team_id": game["home" if side == "away" else "away"]["team_id"],
                   "x_probable_starter": {"id": pid, "name": game[side]["probable_name"], "source": SRC_SCHEDULE,
                                          "observed_at": sched_at},
                   "x_expected_workload": {"outs": round(p["pitcher_outs"][1], 3),
                                           "batters_faced": round(p["pitcher_batters_faced"][1], 3)},
                   "x_matchup_inputs": {"opp_k_rate": ch_feats.get("opp_k_rate"), "opp_nonout_rate": ch_feats.get("opp_nonout_rate"),
                                        "opp_pa": ch_feats.get("opp_pa"), "bullpen_pitches_3d": ch_feats.get("bullpen_pitches_3d")},
                   "x_game_environment": "sports-only; no run environment, park or weather input; venue side only",
                   "x_history_through": proj["history_observed"]}
            if post and arm == "champion":
                row["x_dependency_note"] = ("frozen postseason_shift_outs was fitted on exchange-resolution evidence "
                                            "values (indirect); not independently validated")
            rows.append(row)
    return rows


# ----------------------------------------------------------------------------- existing state

def existing_windows(store, date):
    return defaultdict(set, _existing_windows(store.root, date))


# ----------------------------------------------------------------------------- market (read-only reuse)

def _family_stat(fam):
    return LISTED_FAMILIES.get(fam)


def market_snapshot(data_root, games, as_of, captured):
    """Listing + latest quote <= as_of for pitcher outs / strikeout contracts of the captured
    games, COPIED from the edgelab capture outputs (data/edgelab/markets, observations).
    `captured`: {(game_id, team_id): (player_id, player_name)}. Read-only."""
    dates = sorted({g["official_date"] for g in games})
    pk_by_event, rows = {}, []
    files = []
    for d in dates + [iso(parse(x + "T00:00:00Z") - timedelta(days=1))[:10] for x in dates]:
        for fam in ("markets", "observations"):
            for ext in (".jsonl", ".jsonl.gz"):
                p = os.path.join(data_root, "edgelab", fam, d + ext)
                if os.path.exists(p):
                    files.append((fam, p))
    listing, quote = {}, {}
    from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR
    for fam, p in sorted(set(files)):
        for r in _jsonl(p):
            stat = _family_stat(r.get("marketFamily"))
            if not stat:
                continue
            cap = ((r.get("provenance") or {}).get("capturedAt") or r.get("capturedAt") or "")[:19] + "Z"
            if len(cap) < 20 or cap > as_of:
                continue
            ev = r.get("eventTicker")
            if fam == "markets" and str(r.get("gameId") or "").isdigit():
                pk_by_event[ev] = str(r["gameId"])
            t = r.get("marketTicker")
            cur = listing.get(t)
            if cur is None or cap < cur["first_seen"]:
                listing[t] = {"first_seen": cap, "row": r, "stat": stat, "file": os.path.relpath(p, data_root)}
            if fam == "observations" and (t not in quote or cap >= quote[t]["quoted_at"]):
                quote[t] = {"quoted_at": cap, "yes_bid": r.get("yesBid"), "yes_ask": r.get("yesAsk"), "last": r.get("lastPrice"),
                            "file": os.path.relpath(p, data_root)}
    gp = {g["pk"]: g for g in games}
    for t, L in sorted(listing.items()):
        r = L["row"]
        pk = pk_by_event.get(r.get("eventTicker"))
        if pk is None:  # observation-only listing: officialDate (ET) + away/home codes from the event ticker
            m = EVENT_RE.search(r.get("eventTicker") or "")
            if m:
                d = f"20{m.group(1)}-{MONTHS.get(m.group(2), 0):02d}-{m.group(3)}"
                for g in games:
                    a, h = TEAM_ID_TO_ABBR.get(g["away"]["team_id"]), TEAM_ID_TO_ABBR.get(g["home"]["team_id"])
                    if g["official_date"] == d and m.group(5) == f"{a}{h}":
                        pk = g["pk"]
        if pk not in gp:
            continue
        g = gp[pk]
        team_abbr = r.get("team")
        side = "away" if TEAM_ID_TO_ABBR.get(g["away"]["team_id"]) == team_abbr else (
            "home" if TEAM_ID_TO_ABBR.get(g["home"]["team_id"]) == team_abbr else None)
        pid, how = None, "unmatched"
        if side:
            cand = captured.get((pk, g[side]["team_id"]))
            if cand and norm_name(cand[1]) == norm_name(r.get("player")):
                pid, how = cand[0], "probable_starter_name_and_team"
            elif cand and norm_name(cand[1]).split()[-1:] == norm_name(r.get("player")).split()[-1:]:
                pid, how = cand[0], "probable_starter_surname_and_team"
        q = quote.get(t)
        rows.append({"record": "listing_snapshot", "sport": "MLB", "game_id": pk, "player_id": pid,
                     "player_name": r.get("player"), "team": team_abbr, "id_match": how, "statistic": L["stat"],
                     "venue": "kalshi", "ticker": t, "at_least": r.get("threshold"),
                     "comparison": r.get("comparisonOperator"), "first_listed_at": L["first_seen"],
                     "listing_source_file": L["file"], "quote": q, "pure_as_of": as_of})
    return rows


# ----------------------------------------------------------------------------- capture

def cmd_capture(a):
    store = ShadowStore(a.store)
    gate = load_gate()
    now = utcnow()
    dates = capture_dates(now)
    if a.date:
        dates = [a.date]
    games = []
    for d in dates:
        f_at = iso(utcnow())
        doc = fetch_json(SCHEDULE_URL.format(date=d))
        if doc is None:
            print(json.dumps({"date": d, "error": "schedule_fetch_failed"}))
            return 1
        games += parse_schedule(doc, f_at)
    arms = None
    by_window = defaultdict(list)
    accounting = defaultdict(list)
    for g in games:
        done = existing_windows(store, g["official_date"])[g["pk"]]
        if a.force_window:
            w, why = (a.force_window, "forced_dry_run") if g.get("kickoff") and utcnow() < parse(g["kickoff"]) and \
                g.get("abstract") == "Preview" else (None, "at_or_after_kickoff_or_not_preview")
        else:
            w, why = window_for(g, now, done)
        accounting["window_decisions"].append({"game_id": g["pk"], "kickoff": g.get("kickoff"), "game_type": g["game_type"],
                                               "status": g.get("detailed"), "window": w, "reason": why})
        if w:
            by_window[w].append(g)
    written = []
    for w, gs in sorted(by_window.items()):
        if arms is None:
            arms = Arms(a.data_root)
        time.sleep(1)
        as_of_t = utcnow()
        as_of = iso(as_of_t)
        run_id = f"mlb_pitcher_shadow_{stamp(as_of_t)}_{w}"
        rows, excl, captured = [], [], {}
        for g in gs:
            if as_of_t >= parse(g["kickoff"]):
                excl.append({"game_id": g["pk"], "reason": "at_or_after_kickoff"})
                continue
            for side in ("away", "home"):
                proj, why = arms.project(g, side, as_of)
                if proj is None:
                    excl.append({"game_id": g["pk"], "side": side, "player_id": g[side]["probable_id"], "reason": why})
                    continue
                rr = build_rows(arms, g, side, w, as_of, proj, run_id)
                for r in rr:  # every input observed strictly before as_of, as_of strictly before kickoff
                    assert r["source_max_observed_at"] < r["as_of"] < r["kickoff"], r["game_id"]
                    assert all(x["observed_at"] < r["as_of"] for x in r["feature_lineage"])
                rows += rr
                captured[(g["pk"], g[side]["team_id"])] = (g[side]["probable_id"], g[side]["probable_name"])
        run_doc = {"run_id": run_id, "window": w, "as_of": as_of, "dry_run": bool(a.force_window),
                   "probable_starter_source": {"source": SRC_SCHEDULE,
                                               "fetched_at": {g["pk"]: g["schedule_fetched_at"] for g in gs},
                                               "urls": sorted({SCHEDULE_URL.format(date=g["official_date"]) for g in gs})},
                   "games": [{"game_id": g["pk"], "kickoff": g["kickoff"], "game_type": g["game_type"],
                              "probables": {s: {"id": g[s]["probable_id"], "name": g[s]["probable_name"],
                                                "lineup_posted": g[s]["lineup_posted"]} for s in ("away", "home")}} for g in gs],
                   "exclusions": excl, "rows": len(rows)}
        date = gs[0]["official_date"]
        if rows:
            for arm in ARMS:
                gate.validate_file_summary([r for r in rows if r["x_arm"] == arm], require_v1=True)
            run_doc["gate_validate"] = {arm: gate.validate_file_summary([r for r in rows if r["x_arm"] == arm], require_v1=True)["n"]
                                        for arm in ARMS}
            for arm in ARMS:  # one file per arm: each validates on its own with pure_gate's CLI
                written.append(store.write_once(f"projections/{date}/{stamp(as_of_t)}_{w}__{arm}.jsonl",
                                                [r for r in rows if r["x_arm"] == arm], as_of)["path"])
            mk = market_snapshot(a.data_root, gs, as_of, captured)
            written.append(store.write_once(f"market/{date}/{stamp(as_of_t)}_{w}.jsonl", mk, as_of)["path"])
            run_doc["listing_rows"] = len(mk)
        written.append(store.write_once(f"runs/{date}/{stamp(as_of_t)}_{w}.json", run_doc, as_of)["path"])
    missed = [] if a.force_window or a.date else missed_windows(games, utcnow(), store.root)
    if missed:  # a window that closed with no capture: recorded once, never back-filled
        written.append(store.write_once(f"missed/{dates[-1]}/{stamp(utcnow())}.jsonl", missed)["path"])
    print(json.dumps({"now": iso(now), "dates": dates, "games": len(games), "windows": {w: len(v) for w, v in by_window.items()},
                      "missed": missed, "written": written, "decisions": accounting["window_decisions"]}, indent=1))
    return 0


# ----------------------------------------------------------------------------- outcomes

def all_projections(store):
    rows = []
    for f in sorted(glob.glob(os.path.join(store.root, "projections", "*", "*.jsonl"))):
        rows += list(_jsonl(f))
    return rows


def all_rows(store, family):
    rows = []
    for f in sorted(glob.glob(os.path.join(store.root, family, "*", "*.jsonl"))):
        rows += list(_jsonl(f))
    return rows


def outcome_rows_from_feed(pk, feed, wanted, fetched_at):
    """Pure. Gate-format outcomes for wanted player ids + provenance. played = the pitcher was
    his team's starter (orderIndex 0), matching the arms' conditional-on-starting forecasts."""
    from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_pitcher_lines
    gd = feed.get("gameData") or {}
    box = (feed.get("liveData") or {}).get("boxscore") or {}
    lines = {}
    starters = {}
    for side in ("away", "home"):
        for ln in extract_pitcher_lines(box, side):
            lines[ln["playerId"]] = ln
            if ln["orderIndex"] == 0:
                starters[side] = ln["playerId"]
    plays = ((feed.get("liveData") or {}).get("plays") or {}).get("allPlays") or []
    ends = [(p.get("about") or {}).get("endTime") for p in plays if (p.get("about") or {}).get("endTime")]
    game_end = iso(parse(max(ends))) if ends else None
    out = []
    for pid in sorted(wanted):
        ln = lines.get(pid)
        played = bool(ln and ln["orderIndex"] == 0)
        for stat, key in (("pitcher_outs", "outs"), ("pitcher_batters_faced", "battersFaced"), ("pitcher_strikeouts", "strikeOuts")):
            v = (ln or {}).get(key)
            out.append({"sport": "MLB", "game_id": pk, "player_id": pid, "statistic": stat,
                        "actual": v if v is not None else 0, "played": played and v is not None,
                        "source": "mlb_statsapi_v1.1_feed_live", "observed_at": fetched_at})
    prov = {"game_id": pk, "url": FEED_URL.format(pk=pk), "fetched_at": fetched_at, "payload_sha256": sha256_obj(feed),
            "status": (gd.get("status") or {}).get("detailedState"), "game_type": (gd.get("game") or {}).get("type"),
            "game_end": game_end, "actual_starters": starters,
            "projected_not_starting": sorted(p for p in wanted if not (lines.get(p) and lines[p]["orderIndex"] == 0)),
            "unprojected_actual_starters": sorted(p for p in starters.values() if p not in wanted)}
    return out, prov


def cmd_outcomes(a):
    from lib.edgelab.mlb_boxscore import fetch_game_feed
    store = ShadowStore(a.store)
    now = utcnow()
    have = {(r["game_id"]) for r in all_rows(store, "outcome_provenance") if r.get("status") in FINAL or r.get("status") in NOT_PLAYED}
    wanted = defaultdict(set)
    kick = {}
    for r in all_projections(store):
        wanted[r["game_id"]].add(r["player_id"])
        kick[r["game_id"]] = r["kickoff"]
    for d in (a.all_starters_date or "").split(","):  # dry-run exercise: every final game of these dates
        if not d.strip():
            continue
        doc = fetch_json(SCHEDULE_URL.format(date=d.strip()))
        for g in parse_schedule(doc, iso(now)):
            if g["detailed"] in FINAL:
                wanted.setdefault(g["pk"], set())
                kick[g["pk"]] = g["kickoff"]
    todo = [pk for pk in sorted(wanted) if pk not in have and parse(kick[pk]) <= now - timedelta(hours=3)]
    outs, provs = [], []
    for pk in todo:
        f_at = iso(utcnow())
        feed = fetch_game_feed(pk, timeout=30)
        if feed is None:
            provs.append({"game_id": pk, "url": FEED_URL.format(pk=pk), "fetched_at": f_at, "status": "FETCH_FAILED"})
            continue
        status = ((feed.get("gameData") or {}).get("status") or {}).get("detailedState")
        if status not in FINAL:
            if status in NOT_PLAYED:
                provs.append({"game_id": pk, "url": FEED_URL.format(pk=pk), "fetched_at": f_at, "status": status})
            continue
        ws = wanted[pk] or set()
        if not ws:  # dry-run: score the actual starters
            from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_pitcher_lines
            box = (feed.get("liveData") or {}).get("boxscore") or {}
            ws = {ln["playerId"] for s in ("away", "home") for ln in extract_pitcher_lines(box, s) if ln["orderIndex"] == 0}
        o, p = outcome_rows_from_feed(pk, feed, ws, f_at)
        if p["game_end"] and p["game_end"] >= f_at:
            continue  # never record a line fetched before the game ended
        outs += o
        provs.append(p)
        time.sleep(0.25)
    written = []
    ts = stamp(utcnow())
    if outs:
        load_gate().outcomes_index(outs)
        written.append(store.write_once(f"outcomes/{now.strftime('%Y-%m-%d')}/{ts}.jsonl", outs)["path"])
    if provs:
        written.append(store.write_once(f"outcome_provenance/{now.strftime('%Y-%m-%d')}/{ts}.jsonl", provs)["path"])
    print(json.dumps({"games_checked": len(todo), "outcome_rows": len(outs), "written": written}, indent=1))
    return 0


# ----------------------------------------------------------------------------- reconcile

def latest_outcomes(store):
    o = {}
    for r in all_rows(store, "outcomes"):
        o[(r["game_id"], r["player_id"], r["statistic"])] = r
    return o


def settlement_values(data_root, since):
    """(gamePk, playerId, statistic) -> exchange-resolution evidence value. MISMATCH CHECK ONLY."""
    out = {}
    for f in sorted(glob.glob(os.path.join(data_root, "edgelab", "settlements", "*.jsonl*"))):
        if os.path.basename(f)[:10] < since:
            continue
        for r in _jsonl(f):
            stat = _family_stat(r.get("marketFamily"))
            ev = r.get("settlementEvidence") or {}
            c = ev.get("candidates") or []
            if not stat or len(c) != 1 or ev.get("actualValue") is None:
                continue
            out[(str(ev.get("gamePk") or r.get("gameId")), str(c[0]["playerId"]), stat)] = ev["actualValue"]
    return out


def cohort_keys(market_rows, proj_rows):
    """Pregame market-listed cohort: (game, player, stat, as_of) whose listing was first seen
    at or before that projection's as_of (defined pregame, never from resolution)."""
    first = {}
    for m in market_rows:
        if m.get("player_id"):
            k = (m["game_id"], m["player_id"], m["statistic"])
            first[k] = min(first.get(k, m["first_listed_at"]), m["first_listed_at"])
    return {(r["game_id"], r["player_id"], r["statistic"], r["as_of"]) for r in proj_rows
            if first.get((r["game_id"], r["player_id"], r["statistic"]), "9999") <= r["as_of"]}


def cmd_reconcile(a):
    store = ShadowStore(a.store)
    proj = [r for r in all_projections(store) if r["x_arm"] == "challenger"]
    done = {(r["game_id"], r["player_id"], r["statistic"], r["window"]) for r in all_rows(store, "reconciliation")}
    outc = latest_outcomes(store)
    since = min((r["kickoff"][:10] for r in proj), default="9999")
    sv = settlement_values(a.data_root, iso(parse(since + "T00:00:00Z") - timedelta(days=1))[:10]) if proj else {}
    cohort = cohort_keys(all_rows(store, "market"), proj)
    rows = []
    for r in proj:
        k = (r["game_id"], r["player_id"], r["statistic"])
        if k not in outc or k + (r["x_capture_window"],) in done:
            continue
        o = outc[k]
        s = sv.get(k)
        rows.append({"game_id": k[0], "player_id": k[1], "statistic": k[2], "window": r["x_capture_window"],
                     "as_of": r["as_of"], "season_phase": r["x_season_phase"], "actual": o["actual"], "played": o["played"],
                     "outcome_source": o["source"], "outcome_observed_at": o.get("observed_at"),
                     "market_listed_pregame": (k + (r["as_of"],)) in cohort,
                     "resolution_value": s,
                     "resolution_vs_sports": None if s is None else ("match" if s == o["actual"] else "MISMATCH")})
    written = []
    if rows:
        written.append(store.write_once(f"reconciliation/{utcnow().strftime('%Y-%m-%d')}/{stamp(utcnow())}.jsonl", rows)["path"])
    print(json.dumps({"reconciled": len(rows), "mismatches": sum(1 for r in rows if r["resolution_vs_sports"] == "MISMATCH"),
                      "written": written}))
    return 0


# ----------------------------------------------------------------------------- evaluate

def evaluate_rows(gate, proj, outc, cohort, reps=1000):
    """Pure-ish. {window: {phase: {population: {pair: compare-report}}}}"""
    out = {}
    o_rows = list(outc.values())
    for w in sorted({r["x_capture_window"] for r in proj}):
        out[w] = {}
        for phase in ("regular_season", "postseason"):
            sel = [r for r in proj if r["x_capture_window"] == w and r["x_season_phase"] == phase]
            if not sel:
                continue
            ent = {"status": "BLOCKED_NOT_VALIDATED_POSTSEASON" if phase == "postseason" else "RESEARCH_SHADOW"}
            for pop in ("full_eligible_population", "pregame_market_listed_cohort"):
                rows = sel if pop.startswith("full") else [r for r in sel if (r["game_id"], r["player_id"], r["statistic"], r["as_of"]) in cohort]
                byarm = defaultdict(list)
                for r in rows:
                    byarm[r["x_arm"]].append(r)
                keys = {(r["sport"], r["game_id"], r["player_id"], r["statistic"]) for r in rows}
                oc = [x for x in o_rows if ("MLB", x["game_id"], x["player_id"], x["statistic"]) in keys]
                res = {"rows_per_arm": {k: len(v) for k, v in byarm.items()}, "outcomes": len(oc)}
                for champ, chal in EVAL_PAIRS:
                    if byarm.get(champ) and byarm.get(chal) and oc:
                        res[f"{chal}_vs_{champ}"] = gate.compare(byarm[champ], byarm[chal], oc, bootstrap=reps)
                ent[pop] = res
            out[w][phase] = ent
    return out


def cmd_evaluate(a):
    store = ShadowStore(a.store)
    gate = load_gate()
    proj = all_projections(store)
    # one capture per (window, key): the earliest in that window
    seen, uniq = set(), []
    for r in sorted(proj, key=lambda r: r["as_of"]):
        k = (r["x_capture_window"], r["x_arm"], r["game_id"], r["player_id"], r["statistic"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    outc = latest_outcomes(store)
    matured = [r for r in uniq if (r["game_id"], r["player_id"], r["statistic"]) in outc]
    cohort = cohort_keys(all_rows(store, "market"), matured)
    now = utcnow()
    rep = {"evaluated_at": iso(now), "projection_rows": len(uniq), "matured_rows": len(matured),
           "note": "research shadow only; no promotion, authority or staking. Postseason rows BLOCKED_NOT_VALIDATED.",
           "results": evaluate_rows(gate, matured, outc, cohort, a.reps) if matured else {}}
    runs = [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(os.path.join(store.root, "runs", "*", "*.json")))]
    acc, val = SC.build_safe(uniq, outc, all_rows(store, "market"), runs, all_rows(store, "missed"),
                             all_rows(store, "outcome_provenance"), now=now, run_id=f"mlb_pitcher_shadow_eval_{stamp(now)}",
                             reps=a.reps)
    path, written = None, []
    if not a.print_only:
        path = store.write_once(f"evaluations/{stamp(now)}.json", rep)["path"]
        written = [store.write_once(f"scorecards/{stamp(now)}.accuracy_scorecard.json", acc)["path"],
                   store.write_once(f"scorecards/{stamp(now)}.value_scorecard.json", val)["path"]]
    print(json.dumps({"matured_rows": len(matured), "written": path, "scorecards_written": written,
                      "scorecards": SC.summary_safe(acc, val)}))
    return 0


def cmd_verify(a):
    v = ShadowStore(a.store).verify()
    print(json.dumps(v, indent=1))
    return 0 if v["ok"] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=SHADOW_ROOT)
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture")
    c.add_argument("--date", help="officialDate to capture (default: today ET)")
    c.add_argument("--force-window", choices=("morning", "pregame"),
                   help="DRY-RUN ONLY: capture every not-started game now under this window label")
    o = sub.add_parser("outcomes")
    o.add_argument("--all-starters-date", help="DRY-RUN exercise: outcomes for every final game of these comma-separated dates")
    sub.add_parser("reconcile")
    e = sub.add_parser("evaluate")
    e.add_argument("--reps", type=int, default=1000)
    e.add_argument("--print-only", action="store_true")
    sub.add_parser("verify")
    a = ap.parse_args(argv)
    if a.cmd == "capture" and a.force_window and os.path.abspath(a.store) == os.path.abspath(SHADOW_ROOT):
        ap.error("--force-window is a dry-run option and may not write to the committed shadow store")
    return {"capture": cmd_capture, "outcomes": cmd_outcomes, "reconcile": cmd_reconcile,
            "evaluate": cmd_evaluate, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
