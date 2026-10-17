"""Live projection stage: frozen models -> participation-aware projections -> immutable pregame forecast archive.

Everything about WHO plays comes from the archived availability observations (as of `now`), never from a post-game field:
  * pitcher = the latest archived PROBABLE/STARTING starter observation for that club (certainty PROJECTED until the lineup
    card is posted, CONFIRMED after);
  * hitters = the CONFIRMED posted lineup (kind "lineup") with its batting slot; a club without a posted card gets the
    old 'assumed last lineup' projection, labelled, gate REVIEW, participation UNVERIFIED, published but NOT archived.
Hitter readiness rule (hitter_readiness): a hitter projection can leave 'shadow' only when its batting slot came from a
CONFIRMED pregame lineup observation AND participation resolved to CONFIRMED_ACTIVE; today every row is still role 'shadow'
(promotion is the owner's decision) and carries `shadow_exit_eligible` so the evidence can be counted.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from pf_core import availability as av, prospective as pr
from . import statsapi as api

SPORT = "MLB"
PRIORITY_PITCHER_FAMILIES = ("batters_faced", "outs", "pitcher_strikeouts")
HITTER_FAMILIES = ("plate_appearances", "hits", "total_bases", "home_runs", "runs", "rbis", "hits_runs_rbis")
HISTORY_HOURS = 3.0          # a game that started < 3h ago has no final box yet: excluded from model history


def hitter_readiness(slot_source: str, participation_status: str) -> dict:
    """The point-in-time batting-order readiness rule (see module docstring)."""
    ok = slot_source == "CONFIRMED_POSTED_LINEUP" and participation_status == av.CONFIRMED_ACTIVE
    return {"role": "shadow", "shadow_exit_eligible": bool(ok),
            "shadow_reason": ("slot from a CONFIRMED pregame lineup observation" if ok else
                              "batting slot is not from a confirmed pregame lineup observation: participation is "
                              "UNVERIFIED/LIKELY_ACTIVE and the gate marks REVIEW")}


# --------------------------------------------------------------------------- as-of resolution of who plays
def resolve_who(lat: dict, game: dict) -> dict:
    """Probable pitcher + posted lineup per side for one game, from archived observations only."""
    pk = game["game_pk"]
    res = {}
    for side in ("away", "home"):
        starters = [r for (ev, ent, kind), r in lat.items() if ev == pk and kind == "starter" and
                    r["detail"].get("side") == side and r["status"] in ("PROBABLE", "STARTING")]
        sp = max(starters, key=lambda r: r["captured_utc"]) if starters else None
        lineup = [r for (ev, ent, kind), r in lat.items() if ev == pk and kind == "lineup" and
                  r["detail"].get("side") == side and r["status"] == "STARTING" and r["certainty"] == "CONFIRMED"]
        lineup.sort(key=lambda r: int(r["detail"].get("slot") or 99))
        lu = [{"player_id": r["entity_id"], "slot": int(r["detail"]["slot"]), "name": r["entity_name"],
               "position": r["detail"].get("position")} for r in lineup]
        res[side] = {"pitcher": ({"id": sp["entity_id"], "name": sp["entity_name"], "certainty": sp["certainty"],
                                  "captured_utc": sp["captured_utc"]} if sp else None),
                     "lineup": lu if len(lu) >= 9 else None}
        if lu and len(lu) < 9:
            res[side]["lineup_partial"] = len(lu)
    return res


def slate_from_games(games: list[dict], lat: dict, abbr_known: set[str]) -> tuple[dict, dict, list[str]]:
    """Slate dict in the shape mlb.project.build_inputs reads, filled from as-of observations. Returns (slate, who, skipped)."""
    out, who_by_game, skipped = [], {}, []
    for g in games:
        if g["away"]["abbr"] not in abbr_known or g["home"]["abbr"] not in abbr_known:
            skipped.append(f"{g['game_pk']}: unmapped team {g['away']['abbr']}/{g['home']['abbr']}")
            continue
        who = resolve_who(lat, g)
        who_by_game[g["game_pk"]] = who
        sg = {"gameId": g["game_pk"], "startTime": g["start_utc"], "officialDate": g["official_date"],
              "venueId": g.get("venue_id"), "seriesDescription": g.get("series") or g.get("game_type"),
              "lineups": {"A": who["away"]["lineup"], "H": who["home"]["lineup"]}}
        for side, key in (("away", "away"), ("home", "home")):
            p = who[side]["pitcher"]
            sg[key] = {"abbr": g[side]["abbr"], "pitcher": {"id": p["id"], "name": p["name"]} if p else {}}
        out.append(sg)
    return {"date": games[0]["official_date"] if games else None, "games": out}, who_by_game, skipped


# --------------------------------------------------------------------------- archive records
def _digest(r: dict) -> str:
    key = json.dumps([round(float(r["mean"]), 5), (r.get("participation") or {}).get("status"), r.get("gate_status"),
                      [round(float(x), 5) for x in (r.get("probs") or [])]], sort_keys=True)
    return hashlib.sha1(key.encode()).hexdigest()


def challenger_records(out: dict, inputs: dict, now_iso: str, game_start: dict[str, str],
                       both_probables: set[str] | None = None) -> list[dict]:
    asof_p, asof_h, asof_t = (inputs["asof_utc"][k] for k in ("pitchers", "hitters", "teams"))
    version = out["model"]["version"]
    recs = []
    base = {"sport": SPORT, "model_role": "challenger", "model_version": version, "generated_utc": now_iso}
    for p in out["players"]:
        fam, pmf = p["family"], p.get("_pmf")
        if pmf is None or "participation" not in p:
            continue
        is_pitch = fam in PRIORITY_PITCHER_FAMILIES and "_flags" in p
        is_hit = fam in HITTER_FAMILIES and "slot" in p
        if not (is_pitch or is_hit):
            continue
        if is_hit and not p.get("_confirmed_slot"):
            continue            # assumed-lineup hitters are published (labelled), never archived as forecasts
        rec = dict(base, family=fam, model_id=f"pf-mlb:{p['model']}", game_id=p["game_id"],
                   game_start_utc=game_start[p["game_id"]], entity_id=p["player_id"], entity_name=p.get("name") or "",
                   team=p["team"], opponent=p["opponent"], data_asof_utc=asof_p if is_pitch else asof_h,
                   mean=p["_mean_exact"], lo=pmf["lo"], probs=pmf["probs"], gate_status=p["gate"]["status"],
                   participation=p["participation"], readiness=p["readiness"], role="shadow")
        if is_pitch:
            rec["flags"] = p["_flags"]
        else:
            rec.update(slot=p["slot"], slot_source=p["slot_source"],
                       **{k: v for k, v in hitter_readiness(p["slot_source"], p["participation"]["status"]).items() if k != "role"})
        recs.append(rec)
    for g in out["games"]:
        if both_probables is not None and g["game_id"] not in both_probables:
            continue            # team/game-total models are evaluated with both starters known: no probable, no forecast
        for t in g["teams"]:
            if "_pmf" not in t:
                continue
            tm = t["team"]
            recs.append(dict(base, family=t["family"], model_id=f"pf-mlb:{t.get('model') or 'D_convolution'}", game_id=g["game_id"],
                             game_start_utc=game_start[g["game_id"]], entity_id=tm, entity_name=tm, team=tm,
                             data_asof_utc=asof_t, mean=t["_mean_exact"], lo=t["_pmf"]["lo"], probs=t["_pmf"]["probs"],
                             gate_status=(t.get("reconciliation") or {}).get("status") or "PASS",
                             participation=None, readiness=t.get("readiness") or "SHADOW VALIDATION", role="shadow"))
    return recs


def drop_unchanged(recs: list[dict], archive: str, now: datetime) -> tuple[list[dict], int]:
    """Skip a forecast identical (mean, pmf, participation, gate) to the latest one already archived for the same
    (game, entity, family, model): the earlier record already is the as-of forecast, and re-archiving it every run would
    only grow the archive."""
    if not recs:
        return recs, 0
    days = sorted({api.parse_ts(r["game_start_utc"]).strftime("%Y-%m-%d") for r in recs})
    ex = pr.load_forecasts(archive, SPORT, days)
    last = {}
    if len(ex):
        ex = ex.sort_values("generated_utc")
        for _, row in ex.iterrows():
            last[(row["game_id"], row["entity_id"], row["family"], row["model_id"])] = _digest(row.to_dict())
    keep = [r for r in recs if last.get((r["game_id"], r["entity_id"], r["family"], r["model_id"])) != _digest(r)]
    return keep, len(recs) - len(keep)
