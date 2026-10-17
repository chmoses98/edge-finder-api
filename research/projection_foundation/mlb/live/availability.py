"""Point-in-time availability capture for MLB (pf_core.availability observations).

What is captured, from where, and how certain it is
  * PROBABLE PITCHER  (kind "starter")  <- statsapi schedule `teams.<side>.probablePitcher`, fetched PREGAME by the runner.
        certainty PROJECTED / status PROBABLE   until that club's lineup card is posted;
        certainty CONFIRMED / status STARTING   once the lineup card is posted (the card names the starter; measured on the
        527 archived pregame lineup captures: the probable listed with a posted lineup equals the actual starter 99.7%,
        see LIVE_MLB.md).  A later different probable emits NOT_STARTING for the replaced pitcher.
  * BATTING ORDER     (kind "lineup")   <- statsapi schedule `lineups.<side>Players` (the club's SUBMITTED lineup card, empty
        until posted): certainty CONFIRMED, status STARTING, detail {slot, position}.  A player on an earlier posted card
        but absent from a later one gets NOT_STARTING (late scratch).
Nothing here is REPORTED (no injury designations are captured: the repo has no IL/restriction source), nothing is ever
upgraded, and an event whose captured_utc is not before its start is not emitted.

The feed's own "probable pitcher" field in the post-game game feed is overwritten with the actual starter (equal 99.90% of the
time), so it is never used: probables come only from these archived pregame observations.
"""
from __future__ import annotations

from datetime import timedelta

from pf_core import availability as av

from . import statsapi as api

SPORT = "MLB"
SRC_STARTER = "mlb_statsapi_schedule_probable_pitcher"
SRC_LINEUP = "mlb_statsapi_schedule_lineups"


def _obs(g, side, kind, status, certainty, source, url, captured, entity_id, name, detail):
    t = g[side]
    return av.Observation(sport=SPORT, event_id=g["game_pk"], event_start_utc=g["start_utc"], entity_id=str(entity_id),
                          entity_name=name or "", team=t.get("abbr") or str(t.get("team_id") or ""), kind=kind,
                          status=status, certainty=certainty, source=source, captured_utc=captured, source_updated_utc=None,
                          source_url=url, detail={"side": side, "official_date": g["official_date"],
                                                  "opponent": g["home" if side == "away" else "away"].get("abbr"), **detail})


def latest_prior(prior_rows: list[dict], game_pk: str, kind: str) -> dict[str, dict]:
    """entity_id -> newest earlier observation of `kind` for one event."""
    best: dict[str, dict] = {}
    for r in prior_rows:
        if r.get("event_id") == game_pk and r.get("kind") == kind:
            if r["entity_id"] not in best or r["captured_utc"] > best[r["entity_id"]]["captured_utc"]:
                best[r["entity_id"]] = r
    return best


def observations_from_games(games: list[dict], captured_utc: str, url_by_date: dict[str, str] | None = None,
                            prior_rows: list[dict] | None = None) -> list[av.Observation]:
    prior_rows = prior_rows or []
    cap = api.parse_ts(captured_utc)
    out: list[av.Observation] = []
    for g in games:
        if not api.is_preview(g) or api.parse_ts(g["start_utc"]) <= cap:
            continue                      # started / postponed / final: nothing usable pregame
        url = (url_by_date or {}).get(g["official_date"]) or api.SCHEDULE_URL.format(date=g["official_date"])
        prior_start = latest_prior(prior_rows, g["game_pk"], "starter")
        prior_lineup = latest_prior(prior_rows, g["game_pk"], "lineup")
        for side in ("away", "home"):
            t = g[side]
            posted = bool(t["lineup_posted"])
            cert = "CONFIRMED" if posted else "PROJECTED"
            pid = t.get("probable_id")
            if pid:
                out.append(_obs(g, side, "starter", "STARTING" if posted else "PROBABLE", cert, SRC_STARTER, url,
                                captured_utc, pid, t.get("probable_name"),
                                {"basis": "lineup_posted" if posted else "probable_pitcher_listing"}))
            for e, r in prior_start.items():
                if r["detail"].get("side") == side and r["status"] in ("STARTING", "PROBABLE") and pid and e != pid:
                    out.append(_obs(g, side, "starter", "NOT_STARTING", cert, SRC_STARTER, url, captured_utc, e,
                                    r.get("entity_name"), {"basis": "replaced_by_" + pid}))
            if posted:
                now_ids = set()
                for p in t["lineup"]:
                    now_ids.add(p["player_id"])
                    out.append(_obs(g, side, "lineup", "STARTING", "CONFIRMED", SRC_LINEUP, url, captured_utc,
                                    p["player_id"], p["name"], {"slot": p["slot"], "position": p["position"]}))
                for e, r in prior_lineup.items():
                    if r["detail"].get("side") == side and r["status"] == "STARTING" and e not in now_ids:
                        out.append(_obs(g, side, "lineup", "NOT_STARTING", "CONFIRMED", SRC_LINEUP, url, captured_utc, e,
                                        r.get("entity_name"), {"basis": "removed_from_posted_lineup",
                                                               "slot": r["detail"].get("slot")}))
    return out


def capture_stats(games: list[dict], captured_utc: str) -> dict:
    cap = api.parse_ts(captured_utc)
    pre = [g for g in games if api.is_preview(g) and api.parse_ts(g["start_utc"]) > cap]
    sides = [(g, s) for g in pre for s in ("away", "home")]
    n = len(sides)
    return {"games_seen": len(games), "games_pregame": len(pre), "team_sides": n,
            "probables_listed": sum(1 for g, s in sides if g[s]["probable_id"]),
            "lineups_posted": sum(1 for g, s in sides if g[s]["lineup_posted"]),
            "probable_share": round(sum(1 for g, s in sides if g[s]["probable_id"]) / n, 4) if n else None,
            "lineup_posted_share": round(sum(1 for g, s in sides if g[s]["lineup_posted"]) / n, 4) if n else None,
            "minutes_to_next_start": round(min(((api.parse_ts(g["start_utc"]) - cap).total_seconds() / 60 for g in pre),
                                               default=float("nan")), 1) if pre else None}


def freshness_at_start(rows: list[dict]) -> dict:
    """Over all events in `rows`: minutes between the last capture before the start and the start (lower = fresher), by kind.
    Used for the scorecard's capture statistics."""
    last: dict[tuple, float] = {}
    for r in rows:
        cap, st = api.parse_ts(r["captured_utc"]), api.parse_ts(r["event_start_utc"])
        if cap >= st:
            continue
        k = (r["event_id"], r["kind"])
        m = (st - cap).total_seconds() / 60
        last[k] = min(last.get(k, 1e9), m)
    out = {}
    for kind in ("starter", "lineup"):
        v = sorted(m for (e, k), m in last.items() if k == kind)
        out[kind] = {"events": len(v), "median_minutes_before_start": round(v[len(v) // 2], 1) if v else None,
                     "p90_minutes_before_start": round(v[int(len(v) * 0.9)], 1) if v else None}
    return out


def capture(games: list[dict], archive: str, captured_utc: str, url_by_date: dict | None = None,
            prior_days: int = 3) -> dict:
    """Write one immutable availability snapshot for the pregame games in `games`; returns the stage summary."""
    root = archive
    cap = api.parse_ts(captured_utc)
    days = sorted({(cap - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(prior_days)})
    prior = av.load_snapshots(root, SPORT, days)
    obs = observations_from_games(games, captured_utc, url_by_date, prior)
    res = av.write_snapshot(obs, root, SPORT, captured_utc) if obs else {"written": 0, "rejected": 0, "duplicate": False, "path": None}
    stats = capture_stats(games, captured_utc)
    n_obs = len(obs)
    return {"stage": "capture", "observations": n_obs, "written": res["written"], "rejected": res["rejected"],
            "duplicate": res["duplicate"], "snapshot": res["path"], "errors": res.get("errors", []),
            "not_starting_emitted": sum(1 for o in obs if o.status == "NOT_STARTING"), **stats}
