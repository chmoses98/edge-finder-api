#!/usr/bin/env python3
"""
scripts/research/hitter_w2/features.py
======================================
SIFT W2 hitter lane -- RESEARCH ONLY. Builds strictly as-of (date-level, every
input from games on EARLIER dates) per-team-game feature records from the
committed MLB feed extracts. Sports data only.

A record per (gamePk, batting side) holds: the 9 starters (slot order), each
batter's current-season-to-date and previous-season category counts, the
opposing starter's allowed counts and BF history, the opposing team's bullpen
allowed counts, the home venue, and the outcomes (only used for scoring).
"""
import os
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.research import hitter_w2_engine as E  # noqa: E402


def _int(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def _cls_counts(pas):
    c = E.empty_counts()
    for p in pas:
        c[E.CI[p["cls"]]] += 1
    return c


def season_totals(sd, pas):
    """End-of-season totals used as the NEXT season's 'previous season' input."""
    bat = defaultdict(E.empty_counts)
    pit = defaultdict(E.empty_counts)
    pen = defaultdict(E.empty_counts)
    for p in pas:
        bat[p["batterId"]][E.CI[p["cls"]]] += 1
        pit[p["pitcherId"]][E.CI[p["cls"]]] += 1
        if not p["vsStarter"]:
            g = sd["games"][p["gamePk"]]
            fld_team = g["homeId"] if p["batSideTeam"] == "A" else g["awayId"]
            pen[str(fld_team)][E.CI[p["cls"]]] += 1
    bf = defaultdict(list)
    for r in sd["pitching"]:
        if _int(r["order"]) == 0 and r["BF"] != "":
            bf[_int(r["pitcherId"])].append(_int(r["BF"]))
    box = defaultdict(lambda: [0] * 7)  # PA, H, TB, HR, RBI, R, games(started)
    for r in sd["batting"]:
        b = box[_int(r["playerId"])]
        for i, k in enumerate(("PA", "H", "TB", "HR", "RBI", "R")):
            b[i] += _int(r[k])
        b[6] += 1
    return {"bat": dict(bat), "pit": dict(pit), "pen": dict(pen), "bf": dict(bf), "box": dict(box),
            "league": E.league_rates(pas)}


def build_team_games(sd, pas, prev):
    """Chronological pass over one season. Returns list of team-game records."""
    games = sd["games"]
    by_game_pa = defaultdict(list)
    for p in pas:
        by_game_pa[p["gamePk"]].append(p)
    bat_rows = defaultdict(list)
    for r in sd["batting"]:
        bat_rows[_int(r["gamePk"])].append(r)
    starter_bf = {}
    for r in sd["pitching"]:
        if _int(r["order"]) == 0:
            starter_bf[(_int(r["gamePk"]), r["side"])] = _int(r["BF"], None)
    order = sorted(games, key=lambda pk: (games[pk]["officialDate"], games[pk]["startUTC"], pk))
    bat = defaultdict(E.empty_counts)
    pit = defaultdict(E.empty_counts)
    pen = defaultdict(E.empty_counts)
    bf = defaultdict(list)
    box = defaultdict(lambda: [0] * 7)
    last_obs = None  # latest (startUTC) of games on earlier dates
    records = []
    i = 0
    while i < len(order):
        date = games[order[i]]["officialDate"]
        day = []
        while i < len(order) and games[order[i]]["officialDate"] == date:
            day.append(order[i])
            i += 1
        # ---- emit records for the day (as-of the start of the day) ----
        for pk in day:
            g = games[pk]
            rows = bat_rows.get(pk, [])
            for side in ("A", "H"):
                starters = sorted((r for r in rows if r["side"] == side and r["isStarter"] == "1"),
                                  key=lambda r: _int(r["slot"]))
                if len(starters) != 9 or [_int(r["slot"]) for r in starters] != list(range(1, 10)):
                    records.append({"gamePk": pk, "side": side, "excluded": "LINEUP_NOT_9_STARTERS"})
                    continue
                opp = "H" if side == "A" else "A"
                opp_sp = _int(g[("homeStarterId" if side == "A" else "awayStarterId")], None)
                opp_sp_hand = g["homeStarterHand" if side == "A" else "awayStarterHand"]
                opp_team = g["homeId"] if side == "A" else g["awayId"]
                team_pas = [p for p in by_game_pa.get(pk, []) if p["batSideTeam"] == side]
                bats = []
                for r in starters:
                    pid = _int(r["playerId"])
                    mine = [p for p in team_pas if p["batterId"] == pid]
                    bats.append({
                        "playerId": pid, "slot": _int(r["slot"]), "batSide": r["batSide"],
                        "cur": list(bat[pid]) if pid in bat else None,
                        "prev": list(prev["bat"][pid]) if prev and pid in prev["bat"] else None,
                        "box_cur": list(box[pid]) if pid in box else None,
                        "y": {k: _int(r[k]) for k in ("PA", "H", "TB", "HR", "RBI", "R", "BB", "K", "HBP")},
                        "y_pa_vs_sp": sum(1 for p in mine if p["vsStarter"]),
                        "y_h_vs_sp": sum(1 for p in mine if p["vsStarter"] and p["cls"] in E.HIT),
                        "y_rob": sum(E._popcount(p["basesBefore"]) for p in mine),
                        "y_rob2p": sum(int(bool(p["basesBefore"] & 6)) for p in mine),
                        "y_rbi_hr": sum(p["rbi"] for p in mine if p["cls"] == "HR"),
                        "y_rbi_nonhr": sum(p["rbi"] for p in mine if p["cls"] != "HR"),
                        "y_reach_err": sum(1 for p in mine if p["cls"] == "OUT" and p["outsAfter"] == p["outsBefore"]),
                        "y_pa_pbp": len(mine),
                        "y_cls": _cls_counts(mine),
                        "y_cls_sp": _cls_counts([p for p in mine if p["vsStarter"]]),
                        "y_cls_pen": _cls_counts([p for p in mine if not p["vsStarter"]]),
                    })
                records.append({
                    "gamePk": pk, "side": side, "date": date, "startUTC": g["startUTC"],
                    "venueId": g["venueId"], "home": side == "H", "teamId": g["awayId"] if side == "A" else g["homeId"],
                    "oppTeamId": opp_team, "oppStarterId": opp_sp, "oppStarterHand": opp_sp_hand,
                    "sp_cur": list(pit[opp_sp]) if opp_sp in pit else None,
                    "sp_prev": list(prev["pit"][opp_sp]) if prev and opp_sp in prev["pit"] else None,
                    "sp_bf_cur": list(bf[opp_sp]) if opp_sp in bf else [],
                    "sp_bf_prev": list(prev["bf"].get(opp_sp, [])) if prev else [],
                    "pen_cur": list(pen[str(opp_team)]) if str(opp_team) in pen else None,
                    "pen_prev": list(prev["pen"][str(opp_team)]) if prev and str(opp_team) in prev["pen"] else None,
                    "y_sp_bf": starter_bf.get((pk, opp)),
                    "y_team_runs": _int(g["awayRuns" if side == "A" else "homeRuns"]),
                    "innings": _int(g["innings"]),
                    "source_max_observed_at": last_obs,
                    "batters": bats,
                })
        # ---- update accumulators with the day's games ----
        for pk in day:
            for p in by_game_pa.get(pk, []):
                bat[p["batterId"]][E.CI[p["cls"]]] += 1
                pit[p["pitcherId"]][E.CI[p["cls"]]] += 1
                if not p["vsStarter"]:
                    g = games[pk]
                    fld_team = g["homeId"] if p["batSideTeam"] == "A" else g["awayId"]
                    pen[str(fld_team)][E.CI[p["cls"]]] += 1
            for side in ("A", "H"):
                b = starter_bf.get((pk, side))
                g = games[pk]
                sp = _int(g["awayStarterId" if side == "A" else "homeStarterId"], None)
                if b is not None and sp is not None:
                    bf[sp].append(b)
            for r in bat_rows.get(pk, []):
                bb = box[_int(r["playerId"])]
                for j, k in enumerate(("PA", "H", "TB", "HR", "RBI", "R")):
                    bb[j] += _int(r[k])
                bb[6] += 1
            st = games[pk]["startUTC"]
            if st and (last_obs is None or st > last_obs):
                last_obs = st
    return records
