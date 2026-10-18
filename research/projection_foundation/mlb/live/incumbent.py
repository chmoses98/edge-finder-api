"""Incumbent (production-model) forecasts, archived UNCHANGED and only while the game has not started.

The incumbent's own files are overwritten or re-run during the day (see LIVE_MLB.md, "Pregame timestamp integrity"). Nothing
here edits them or the pipelines that write them; each projection-foundation run READS what exists right now and archives it
immutably (model_role "incumbent") through pf_core.prospective.archive_forecasts, which rejects late records. A version that
was overwritten between two runs is lost to the archive, so run cadence bounds how many intermediate versions are kept; the
version that counts for scoring is the last one archived before the start.

Sources (all committed by existing production/research automation):
  pitchers   data/shadow_pure/mlb_pitcher/projections/<date>/*__champion.jsonl   (frozen production pitcher engine,
             W2 pure-shadow collector; mean + P(X>=k) ladder; own as_of; hash-chained store)
  team runs  data/pipeline/<date>/projections.json                               (production team-run projections;
             meta.createdAt = LAST write, the file is overwritten during the day)
  hitters    data/edgelab/hitter_projection_snapshots/<date>.jsonl                (production hitter engine snapshots for
             Kalshi-priced players; distributionMean; projectionGeneratedAt)
Incumbents carry a mean (and the pitcher ladder in `thresholds`); no pmf, so scoring against them is MAE-based.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timedelta

from . import statsapi as api

PITCHER_STAT = {"pitcher_outs": "outs", "pitcher_batters_faced": "batters_faced", "pitcher_strikeouts": "pitcher_strikeouts"}
HITTER_MARKET = {"hitter_hits": "hits", "hitter_total_bases": "total_bases", "hitter_rbis": "rbis",
                 "hitter_hits_runs_rbis": "hits_runs_rbis"}


def _rec(**kw):
    base = {"sport": "MLB", "model_role": "incumbent", "role": "incumbent", "lo": 0, "probs": [], "gate_status": None, "participation": None,
            "readiness": "INCUMBENT"}
    base.update(kw)
    return base


def _games_by_pk(games):
    return {g["game_pk"]: g for g in games}


def _usable(start_utc: str, generated_utc: str | None, now: datetime) -> bool:
    if not generated_utc:
        return False
    s, g = api.parse_ts(start_utc), api.parse_ts(generated_utc)
    return g < s and g <= now < s


def pitcher_records(repo: str, games: list[dict], now: datetime) -> tuple[list[dict], dict]:
    gp = _games_by_pk(games)
    root = os.path.join(repo, "data", "shadow_pure", "mlb_pitcher", "projections")
    dates = sorted({api.parse_ts(g["start_utc"]).strftime("%Y-%m-%d") for g in games} |
                   {d for g in games for d in (g["official_date"],)})
    # files are filed under the CAPTURE date: scan the capture dates around the games
    span = sorted({api.et_date(now - timedelta(days=i)) for i in range(0, 3)} | set(dates))
    stats = {"rows_seen": 0, "usable": 0, "after_start_or_stale": 0}
    out = []
    for d in span:
        for f in sorted(glob.glob(os.path.join(root, d, "*__champion.jsonl"))):
            for line in open(f, encoding="utf-8"):
                if not line.strip():
                    continue
                r = json.loads(line)
                g = gp.get(str(r.get("game_id")))
                fam = PITCHER_STAT.get(r.get("statistic"))
                if g is None or fam is None:
                    continue
                stats["rows_seen"] += 1
                if not _usable(g["start_utc"], r.get("as_of"), now):
                    stats["after_start_or_stale"] += 1
                    continue
                side = r.get("x_side")
                team = (g.get(side) or {}).get("abbr") if side in ("away", "home") else None
                pj = r["projection"]
                out.append(_rec(family=fam, model_id="incumbent_pitcher_champion", model_version=r.get("model_version"),
                                game_id=g["game_pk"], game_start_utc=g["start_utc"], entity_id=str(r["player_id"]),
                                entity_name=(r.get("x_probable_starter") or {}).get("name") or "", team=team or "",
                                generated_utc=r["as_of"], data_asof_utc=r.get("source_max_observed_at") or r["as_of"],
                                mean=float(pj["mean"]),
                                thresholds={str(t["at_least"]): t["probability"] for t in pj.get("thresholds", [])},
                                capture_window=r.get("x_capture_window"), source_file=os.path.relpath(f, repo)))
                stats["usable"] += 1
    return out, stats


def team_records(repo: str, games: list[dict], now: datetime) -> tuple[list[dict], dict]:
    gp = _games_by_pk(games)
    stats = {"files": 0, "games_seen": 0, "usable": 0, "not_pregame": 0}
    out = []
    for d in sorted({g["official_date"] for g in games}):
        p = os.path.join(repo, "data", "pipeline", d, "projections.json")
        if not os.path.exists(p):
            continue
        doc = json.load(open(p, encoding="utf-8"))
        created = (doc.get("meta") or {}).get("createdAt")
        stats["files"] += 1
        for pg in (doc.get("data") or {}).get("games", []):
            g = gp.get(str(pg.get("gameId")))
            if g is None or pg.get("excludedFromSlate"):
                continue
            stats["games_seen"] += 1
            if not _usable(g["start_utc"], created, now):
                stats["not_pregame"] += 1       # file (re)written at/after this game's first pitch: not a pregame forecast
                continue
            common = dict(model_id="incumbent_team_runs", model_version=(doc.get("meta") or {}).get("producedBy"),
                          game_id=g["game_pk"], game_start_utc=g["start_utc"], generated_utc=created, data_asof_utc=created,
                          file_created_utc=created)
            for side, key in (("away", "awayProjRuns"), ("home", "homeProjRuns")):
                if pg.get(key) is not None:
                    out.append(_rec(family="team_runs", entity_id=g[side]["abbr"], entity_name=g[side]["abbr"],
                                    team=g[side]["abbr"], mean=float(pg[key]), **common))
            if pg.get("totalProj") is not None:
                out.append(_rec(family="game_total", entity_id="TOTAL", entity_name="game total", team="TOTAL",
                                mean=float(pg["totalProj"]), **common))
            stats["usable"] += 1
    return out, stats


def hitter_records(repo: str, games: list[dict], now: datetime) -> tuple[list[dict], dict]:
    gp = _games_by_pk(games)
    stats = {"rows_seen": 0, "usable": 0, "not_pregame": 0}
    out = []
    for d in sorted({g["official_date"] for g in games}):
        p = os.path.join(repo, "data", "edgelab", "hitter_projection_snapshots", f"{d}.jsonl")
        if not os.path.exists(p):
            continue
        seen = set()
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            g = gp.get(str(r.get("gameId")))
            fam = HITTER_MARKET.get(r.get("marketFamily"))
            if g is None or fam is None or r.get("projectionStatus") != "PROJECTED" or r.get("distributionMean") is None:
                continue
            stats["rows_seen"] += 1
            if not _usable(g["start_utc"], r.get("projectionGeneratedAt"), now):
                stats["not_pregame"] += 1
                continue
            k = (g["game_pk"], str(r["playerId"]), fam, r["projectionGeneratedAt"])
            if k in seen:                      # one row per Kalshi rung: the mean is the same
                continue
            seen.add(k)
            out.append(_rec(family=fam, model_id="incumbent_hitter_engine", model_version=r.get("engineCommitSha"),
                            game_id=g["game_pk"], game_start_utc=g["start_utc"], entity_id=str(r["playerId"]),
                            entity_name=r.get("player") or "", team="", generated_utc=r["projectionGeneratedAt"],
                            data_asof_utc=r["projectionGeneratedAt"], mean=float(r["distributionMean"]),
                            checkpoint=r.get("checkpoint")))
            stats["usable"] += 1
    return out, stats


def all_records(repo: str, games: list[dict], now: datetime) -> tuple[list[dict], dict]:
    games = [g for g in games if api.is_preview(g) and api.parse_ts(g["start_utc"]) > now]
    recs, stats = [], {}
    for name, fn in (("pitchers", pitcher_records), ("team_runs", team_records), ("hitters", hitter_records)):
        r, s = fn(repo, games, now)
        recs += r
        stats[name] = s
    return recs, stats
