#!/usr/bin/env python3
"""
scripts/research/hitter_v2/data_v2.py
=====================================
SIFT MLB hitter V2 lane -- RESEARCH ONLY. Strictly as-of feature records for
the V2 challenger (and, with identical inputs, the frozen W2 challenger and the
W2 baseline), built from the committed MLB Stats API feed extracts
(data/research/hitter_w2/mlb_feed/<season>/). Sports data only.

Every per-team-game record carries the W2 record fields (so the frozen W2 model
code consumes it unchanged) plus the V2 inputs:
  * batter counts split by opposing pitcher hand (current + previous season);
  * opposing starter counts split by the batter's effective hand;
  * opposing bullpen counts split by batter effective hand, the bullpen's
    reliever-hand mix vs that batter hand, and day-stamped bullpen counts for
    recency weighting;
  * current-season league counts to date (league-environment prior);
  * the at-risk substitution rows (for the per-batter removal propensity).

Point-in-time inputs. The lineup and the opposing starter come from a
`lineup_provider(gamePk, side)`:
  * training (2024-2025): official starting lineup + actual starter;
  * held-out point-in-time population: the pregame confirmed lineup and the
    probable starters of the pregame slate capture
    (data/research/hitter_v2/pregame_lineups_2026.csv.gz);
  * the secondary 2026 population: official starting lineup + the feed's
    probable starter (documented proxy).
All counts come from games on EARLIER dates (the accumulators are updated only
after the whole day is emitted). Handedness is treated as a static attribute.
"""
import csv
import gzip
import os
import sys
from collections import defaultdict
from datetime import date as _date

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.research import hitter_w2_engine as W2  # noqa: E402

NC = len(W2.CATS)
PREGAME_LINEUPS = os.path.join(_ROOT, "data", "research", "hitter_v2", "pregame_lineups_2026.csv.gz")


def _int(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def eff_hand(bat_side, pitch_hand):
    """Batter's effective hand vs a pitcher (switch hitters bat opposite)."""
    if bat_side == "S":
        return "R" if pitch_hand == "L" else "L"
    return bat_side if bat_side in ("L", "R") else "R"


def _ord(d):
    return _date.fromisoformat(d).toordinal()


# ----------------------------------------------------------------------------------------------
# Annotation (all plays, incl. non-PA runner outs; score margin; slot ordinals)
# ----------------------------------------------------------------------------------------------
def annotate(sd):
    """Returns (pas, runner_out_counts, at_risk).

    pas: W2-annotated PAs (same fields as hitter_w2_engine.annotate_pa) plus `margin` (batting minus
         fielding runs before the PA), `slot`, `isStarterBat`, `kSlot` (0-based PA ordinal of the slot).
    runner_out_counts: {bases: [n_inning_ending_runner_outs, n_plays_started]} at 2 outs.
    at_risk: rows (gamePk, side, slot, starterId, k, inning, margin, event, officialDate) for each PA of a
         slot whose starter took the slot's previous PA; event = this PA is taken by a non-starter."""
    games = sd["games"]
    lineup = {}
    for r in sd["batting"]:
        lineup[(_int(r["gamePk"]), r["side"], _int(r["playerId"]))] = (_int(r["slot"]), r["isStarter"] == "1")
    starters_by_slot = defaultdict(dict)
    for (pk, side, pid), (slot, st) in lineup.items():
        if st:
            starters_by_slot[(pk, side)][slot] = pid
    w2 = W2.annotate_pa(sd)
    # W2 keeps PA rows in order; walk raw rows in the same order to attach margin and runner outs.
    ro = {b: [0, 0] for b in range(1, 8)}
    at_risk = []
    i_pa = 0
    prev_key, outs, bases = None, 0, 0
    score = {}
    slot_k = defaultdict(int)
    alive = {}  # (pk, side, slot) -> starter still in the slot
    for r in sd["pa"]:
        pk = int(r["gamePk"])
        inning = _int(r["inning"])
        half = r["half"]
        key = (pk, inning, half)
        if key != prev_key:
            outs, bases = 0, (2 if inning >= 10 else 0)
            prev_key = key
        sc = score.setdefault(pk, {"A": 0, "H": 0})
        bat_side = "A" if half == "T" else "H"
        fld_side = "H" if half == "T" else "A"
        cls = W2.event_class(r["eventType"])
        outs_after = _int(r["outsAfter"])
        if outs == 2 and bases:
            ro[bases][1] += 1
            if cls is None and outs_after == 3:
                ro[bases][0] += 1
        if cls is not None:
            p = w2[i_pa]
            i_pa += 1
            assert p["gamePk"] == pk and p["batterId"] == _int(r["batterId"], None)
            p["margin"] = sc[bat_side] - sc[fld_side]
            info = lineup.get((pk, bat_side, p["batterId"]))
            slot = info[0] if info else 0
            p["slot"] = slot
            p["isStarterBat"] = bool(info and info[1])
            if 1 <= slot <= 9:
                sk = (pk, bat_side, slot)
                k = slot_k[sk]
                p["kSlot"] = k
                starter = starters_by_slot[(pk, bat_side)].get(slot)
                if k == 0:
                    alive[sk] = p["batterId"] == starter
                elif alive.get(sk):
                    ev = int(p["batterId"] != starter)
                    at_risk.append((pk, bat_side, slot, starter, k, inning, p["margin"], ev,
                                    games[pk]["officialDate"]))
                    if ev:
                        alive[sk] = False
                slot_k[sk] = k + 1
            else:
                p["kSlot"] = None
        sc[bat_side] += _int(r["runsOnPlay"])
        outs = outs_after if outs_after < 3 else 0
        bases = _int(r["postBases"]) if outs_after < 3 else 0
    assert i_pa == len(w2)
    return w2, ro, at_risk


def hand_maps(sds):
    """Static handedness: batter batSide and pitcher pitchHand from any feed row."""
    bh, ph = {}, {}
    for sd in sds:
        for r in sd["batting"]:
            if r["batSide"]:
                bh[_int(r["playerId"])] = r["batSide"]
        for r in sd["pitching"]:
            if r["pitchHand"]:
                ph[_int(r["pitcherId"])] = r["pitchHand"]
    return bh, ph


# ----------------------------------------------------------------------------------------------
# Season totals (previous-season inputs)
# ----------------------------------------------------------------------------------------------
def _acc_pa(acc, p, games, phand_of):
    c = W2.CI[p["cls"]]
    bh = p["batHand"] or "R"
    ph = p["pitchHand"] or "R"
    eh = eff_hand(bh, ph)
    acc["bat"][p["batterId"]][c] += 1
    acc["bat_vs"][(p["batterId"], ph)][c] += 1
    acc["pit"][p["pitcherId"]][c] += 1
    acc["pit_vs"][(p["pitcherId"], eh)][c] += 1
    acc["league"][c] += 1
    if not p["vsStarter"]:
        g = games[p["gamePk"]]
        team = str(g["homeId"] if p["batSideTeam"] == "A" else g["awayId"])
        acc["pen"][team][c] += 1
        acc["pen_vs"][(team, eh)][c] += 1
        acc["pen_mix"][(team, bh)][0 if ph == "L" else 1] += 1


def new_acc():
    e = W2.empty_counts
    return {"bat": defaultdict(e), "bat_vs": defaultdict(e), "pit": defaultdict(e), "pit_vs": defaultdict(e),
            "pen": defaultdict(e), "pen_vs": defaultdict(e), "pen_mix": defaultdict(lambda: [0, 0]),
            "league": e()}


def season_totals_v2(sd, pas):
    acc = new_acc()
    for p in pas:
        _acc_pa(acc, p, sd["games"], None)
    bf = defaultdict(list)
    for r in sd["pitching"]:
        if _int(r["order"]) == 0 and r["BF"] != "":
            bf[_int(r["pitcherId"])].append(_int(r["BF"]))
    out = {k: dict(v) if isinstance(v, defaultdict) else v for k, v in acc.items()}
    out["bf"] = dict(bf)
    n = sum(acc["league"])
    out["league_rates"] = [x / n for x in acc["league"]]
    return out


# ----------------------------------------------------------------------------------------------
# Lineup providers
# ----------------------------------------------------------------------------------------------
def official_provider(sd, use_probable=False):
    """Official starting lineup from the feed; opposing starter = actual starter (training) or the
    feed's probable starter (secondary 2026 population)."""
    rows = defaultdict(list)
    for r in sd["batting"]:
        if r["isStarter"] == "1":
            rows[(_int(r["gamePk"]), r["side"])].append((_int(r["slot"]), _int(r["playerId"]), r["position"]))
    games = sd["games"]

    def prov(pk, side):
        lu = sorted(rows.get((pk, side), []))
        g = games[pk]
        key = "homeProbableId" if side == "A" else "awayProbableId"
        akey = "homeStarterId" if side == "A" else "awayStarterId"
        opp_sp = _int(g[key], None) if use_probable else None
        if opp_sp is None:
            opp_sp = _int(g[akey], None)
        return {"lineup": lu, "opp_sp": opp_sp, "source": "official_starting_lineup", "as_of": None,
                "scored": True}
    return prov


def load_pregame_lineups(path=PREGAME_LINEUPS):
    out = defaultdict(list)
    meta = {}
    with gzip.open(path, "rt", newline="") as f:
        for r in csv.DictReader(f):
            k = (int(r["gamePk"]), r["side"])
            out[k].append((_int(r["slot"]), _int(r["playerId"]), r["position"]))
            meta[k] = (r["captureUTC"], _int(r["oppProbableId"], None), r["captureFile"],
                       _int(r["ownProbableId"], None))
    return out, meta


def pregame_provider(sd, path=PREGAME_LINEUPS):
    """Point-in-time: captured pregame confirmed lineup + the capture's probable starter. A side
    without a capture is NOT scored; for the game simulation it gets a proxy lineup = that team's
    official lineup from its previous game (known pregame), and the opposing probable comes from the
    other side's capture."""
    caps, meta = load_pregame_lineups(path)
    games = sd["games"]
    starters = defaultdict(list)
    for r in sd["batting"]:
        if r["isStarter"] == "1":
            starters[(_int(r["gamePk"]), r["side"])].append((_int(r["slot"]), _int(r["playerId"]), r["position"]))
    team_games = defaultdict(list)
    for pk, g in games.items():
        team_games[g["awayId"]].append((g["officialDate"], g["startUTC"], pk, "A"))
        team_games[g["homeId"]].append((g["officialDate"], g["startUTC"], pk, "H"))
    for v in team_games.values():
        v.sort()

    def prev_lineup(pk, side):
        g = games[pk]
        team = g["awayId"] if side == "A" else g["homeId"]
        best = None
        for (d, st, pk2, s2) in team_games[team]:
            if d < g["officialDate"] and starters.get((pk2, s2)):
                best = (pk2, s2)
        return sorted(starters[best]) if best else None

    def prov(pk, side):
        opp = "H" if side == "A" else "A"
        k, ko = (pk, side), (pk, opp)
        if k in caps:
            opp_sp = meta[k][1]
            return {"lineup": sorted(caps[k]), "opp_sp": opp_sp, "source": "pregame_slate_capture",
                    "as_of": meta[k][0], "capture_file": meta[k][2], "scored": True}
        if ko in caps:
            # our opposing starter is the captured side's own probable starter (same capture)
            return {"lineup": prev_lineup(pk, side), "opp_sp": meta[ko][3],
                    "source": "previous_game_lineup_proxy", "as_of": meta[ko][0], "scored": False}
        return None
    return prov


# ----------------------------------------------------------------------------------------------
# Chronological record builder
# ----------------------------------------------------------------------------------------------
def build_records(sd, pas, prev, provider, bat_hand, pit_hand, game_filter=None):
    """One record per (gamePk, side) for which provider returns a 9-man lineup. Outcomes `y` are the
    box-score line of that player in that game (None if he did not bat; `started` False if he was not
    in the official starting lineup)."""
    games = sd["games"]
    by_game_pa = defaultdict(list)
    for p in pas:
        by_game_pa[p["gamePk"]].append(p)
    bat_rows = {}
    for r in sd["batting"]:
        bat_rows[(_int(r["gamePk"]), _int(r["playerId"]))] = r
    starter_bf = {}
    for r in sd["pitching"]:
        if _int(r["order"]) == 0:
            starter_bf[(_int(r["gamePk"]), r["side"])] = _int(r["BF"], None)
    order = sorted(games, key=lambda pk: (games[pk]["officialDate"], games[pk]["startUTC"], pk))
    acc = new_acc()
    bf = defaultdict(list)
    box = defaultdict(lambda: [0] * 7)
    pen_daily = defaultdict(list)  # team -> [(date_ordinal, eff_hand, counts)]
    last_obs = None
    records = []
    i = 0
    while i < len(order):
        d = games[order[i]]["officialDate"]
        day = []
        while i < len(order) and games[order[i]]["officialDate"] == d:
            day.append(order[i])
            i += 1
        dord = _ord(d)
        for pk in day:
            if game_filter is not None and not game_filter(pk):
                continue
            g = games[pk]
            for side in ("A", "H"):
                pv = provider(pk, side)
                if pv is None:
                    continue
                lu = pv["lineup"]
                if not lu or len(lu) != 9 or [s for s, _p, _pos in lu] != list(range(1, 10)):
                    records.append({"gamePk": pk, "side": side, "excluded": "LINEUP_NOT_9"})
                    continue
                opp_team = g["homeId"] if side == "A" else g["awayId"]
                opp_sp = pv["opp_sp"]
                sp_hand = pit_hand.get(opp_sp) or "R"
                team_pas = [p for p in by_game_pa.get(pk, []) if p["batSideTeam"] == side]
                bats = []
                for slot, pid, pos in lu:
                    bs = bat_hand.get(pid) or "R"
                    br = bat_rows.get((pk, pid))
                    mine = [p for p in team_pas if p["batterId"] == pid]
                    y = None
                    if br is not None:
                        y = {k: _int(br[k]) for k in ("PA", "H", "TB", "HR", "RBI", "R", "BB", "K", "HBP")}
                        y["HRR"] = y["H"] + y["R"] + y["RBI"]
                    bats.append({
                        "playerId": pid, "slot": slot, "batSide": bs, "position": pos,
                        "cur": list(acc["bat"][pid]) if pid in acc["bat"] else None,
                        "prev": list(prev["bat"][pid]) if prev and pid in prev["bat"] else None,
                        "cur_vs": {h: list(acc["bat_vs"][(pid, h)]) for h in ("L", "R") if (pid, h) in acc["bat_vs"]},
                        "prev_vs": {h: list(prev["bat_vs"][(pid, h)]) for h in ("L", "R")
                                    if prev and (pid, h) in prev["bat_vs"]},
                        "box_cur": list(box[pid]) if pid in box else None,
                        "y": y, "started": bool(br is not None and br["isStarter"] == "1"
                                                and _int(br["slot"]) == slot),
                        "started_any_slot": bool(br is not None and br["isStarter"] == "1"),
                        "y_cls": W2.empty_counts() if not mine else _cls(mine),
                        "y_cls_sp": _cls([p for p in mine if p["vsStarter"]]),
                        "y_cls_pen": _cls([p for p in mine if not p["vsStarter"]]),
                        "y_pa_vs_sp": sum(1 for p in mine if p["vsStarter"]),
                    })
                pen_key = str(opp_team)
                rec = {
                    "gamePk": pk, "side": side, "date": d, "startUTC": g["startUTC"], "venueId": g["venueId"],
                    "home": side == "H", "teamId": g["awayId"] if side == "A" else g["homeId"],
                    "oppTeamId": opp_team, "oppStarterId": opp_sp, "oppStarterHand": sp_hand,
                    "actualOppStarterId": _int(g["homeStarterId" if side == "A" else "awayStarterId"], None),
                    "sp_cur": list(acc["pit"][opp_sp]) if opp_sp in acc["pit"] else None,
                    "sp_prev": list(prev["pit"][opp_sp]) if prev and opp_sp in prev["pit"] else None,
                    "sp_cur_vs": {h: list(acc["pit_vs"][(opp_sp, h)]) for h in ("L", "R") if (opp_sp, h) in acc["pit_vs"]},
                    "sp_prev_vs": {h: list(prev["pit_vs"][(opp_sp, h)]) for h in ("L", "R")
                                   if prev and (opp_sp, h) in prev["pit_vs"]},
                    "sp_bf_cur": list(bf[opp_sp]) if opp_sp in bf else [],
                    "sp_bf_prev": list(prev["bf"].get(opp_sp, [])) if prev else [],
                    "pen_cur": list(acc["pen"][pen_key]) if pen_key in acc["pen"] else None,
                    "pen_prev": list(prev["pen"][pen_key]) if prev and pen_key in prev["pen"] else None,
                    "pen_cur_vs": {h: list(acc["pen_vs"][(pen_key, h)]) for h in ("L", "R") if (pen_key, h) in acc["pen_vs"]},
                    "pen_prev_vs": {h: list(prev["pen_vs"][(pen_key, h)]) for h in ("L", "R")
                                    if prev and (pen_key, h) in prev["pen_vs"]},
                    "pen_mix_cur": {h: list(acc["pen_mix"][(pen_key, h)]) for h in ("L", "R", "S") if (pen_key, h) in acc["pen_mix"]},
                    "pen_mix_prev": {h: list(prev["pen_mix"][(pen_key, h)]) for h in ("L", "R", "S")
                                     if prev and (pen_key, h) in prev["pen_mix"]},
                    "pen_decay": _pen_decay(pen_daily.get(pen_key, []), dord),
                    "league_cur": list(acc["league"]),
                    "y_sp_bf": starter_bf.get((pk, "H" if side == "A" else "A")),
                    "y_team_runs": _int(g["awayRuns" if side == "A" else "homeRuns"]),
                    "innings": _int(g["innings"]),
                    "source_max_observed_at": last_obs, "lineup_source": pv["source"], "lineup_as_of": pv["as_of"],
                    "capture_file": pv.get("capture_file"), "scored": pv["scored"], "batters": bats,
                }
                records.append(rec)
        # ---- update accumulators with the day's games (all games, regardless of filter) ----
        for pk in day:
            g = games[pk]
            day_pen = defaultdict(lambda: {"L": W2.empty_counts(), "R": W2.empty_counts()})
            for p in by_game_pa.get(pk, []):
                _acc_pa(acc, p, games, None)
                if not p["vsStarter"]:
                    team = str(g["homeId"] if p["batSideTeam"] == "A" else g["awayId"])
                    eh = eff_hand(p["batHand"] or "R", p["pitchHand"] or "R")
                    day_pen[team][eh][W2.CI[p["cls"]]] += 1
            for team, v in day_pen.items():
                pen_daily[team].append((dord, v))
            for side in ("A", "H"):
                b = starter_bf.get((pk, side))
                sp = _int(g["awayStarterId" if side == "A" else "homeStarterId"], None)
                if b is not None and sp is not None:
                    bf[sp].append(b)
            st = g["startUTC"]
            if st and (last_obs is None or st > last_obs):
                last_obs = st
        for pk in day:
            for r in _bat_rows_for(sd, pk):
                bb = box[_int(r["playerId"])]
                for j, k in enumerate(("PA", "H", "TB", "HR", "RBI", "R")):
                    bb[j] += _int(r[k])
                bb[6] += 1
    return records


_BAT_INDEX = {}


def _bat_rows_for(sd, pk):
    key = id(sd)
    if key not in _BAT_INDEX:
        idx = defaultdict(list)
        for r in sd["batting"]:
            idx[_int(r["gamePk"])].append(r)
        _BAT_INDEX.clear()
        _BAT_INDEX[key] = idx
    return _BAT_INDEX[key].get(pk, [])


def _cls(pas):
    c = W2.empty_counts()
    for p in pas:
        c[W2.CI[p["cls"]]] += 1
    return c


PEN_HALF_LIVES = (15, 30, 60)


def _pen_decay(daily, dord):
    """{half_life: {'L': decayed counts, 'R': decayed counts}} of the bullpen's current-season PAs on
    earlier dates, weight 0.5 ** (days_ago / half_life)."""
    out = {}
    for hl in PEN_HALF_LIVES:
        acc = {"L": [0.0] * NC, "R": [0.0] * NC}
        for o, v in daily:
            age = dord - o
            if age <= 0:
                continue
            w = 0.5 ** (age / hl)
            for h in ("L", "R"):
                c = v[h]
                a = acc[h]
                for j in range(NC):
                    if c[j]:
                        a[j] += w * c[j]
        out[hl] = acc
    return out


# ----------------------------------------------------------------------------------------------
# Substitution propensity inputs
# ----------------------------------------------------------------------------------------------
def at_risk_with_dates(sd, at_risk):
    games = sd["games"]
    return [(games[r[0]]["officialDate"],) + r for r in at_risk]
