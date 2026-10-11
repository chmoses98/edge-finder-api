#!/usr/bin/env python3
"""
scripts/research/hitter_w2/sidecars.py
======================================
SIFT W2 hitter lane -- RESEARCH ONLY. pure_forecast.v1 rows (shared contract,
sift-sports-intelligence pure-contract @ 558c592) for the challenger, the
baseline and the incumbent rerun, plus sports-truth outcome rows. One row per
player-game-statistic; the threshold ladder lives inside the row.
"""
from datetime import datetime, timedelta, timezone

STAT_NAMES = {"PA": "plate_appearances", "H": "hits", "TB": "total_bases", "HR": "home_runs", "RBI": "rbis",
              "R": "runs_scored"}
LADDER = {"PA": range(1, 8), "H": range(1, 5), "TB": range(1, 9), "HR": range(1, 3), "RBI": range(1, 6),
          "R": range(1, 5)}
SOURCE_ID = "mlb_statsapi_game_feed"


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def default_as_of(kickoff_iso):
    return iso(parse(kickoff_iso) - timedelta(minutes=60))


def _q(pmf, q):
    s = 0.0
    for k in sorted(pmf):
        s += pmf[k]
        if s >= q - 1e-12:
            return k
    return max(pmf)


def projection(pmf, stat):
    pmf = {int(k): float(v) for k, v in pmf.items() if v > 0}
    mean = sum(k * v for k, v in pmf.items())
    th, last = [], 1.0
    for t in LADDER[stat]:
        p = round(min(last, sum(v for k, v in pmf.items() if k >= t)), 6)
        th.append({"at_least": t, "probability": p})
        last = p
    qs = [{"q": q, "value": _q(pmf, q)} for q in (0.1, 0.25, 0.5, 0.75, 0.9)]
    return {"mean": round(mean, 6), "median": _q(pmf, 0.5), "p10": _q(pmf, 0.1), "p90": _q(pmf, 0.9),
            "quantiles": qs, "thresholds": th}


def row(model_version, frozen_hash, game_pk, player_id, stat, pmf, kickoff, as_of, lineage, run_id):
    src_max = as_of
    obs = [f for f in lineage]
    return {
        "schema_version": "pure_forecast.v1", "sport": "MLB", "game_id": str(game_pk),
        "player_id": f"mlbam:{player_id}", "statistic": f"hitter_{STAT_NAMES[stat]}",
        "as_of": as_of, "kickoff": kickoff, "source_max_observed_at": src_max,
        "projection_mode": "PURE_INDEPENDENT", "model_version": model_version, "model_frozen_hash": frozen_hash,
        "conditional_on_playing": True, "participation_probability": None,
        "projection": projection(pmf, stat),
        "sources": [{"source_id": SOURCE_ID, "class": "sports_only", "max_observed_at": src_max,
                     "description": "MLB Stats API game feeds of earlier dates + official starting lineup"}],
        "feature_lineage": obs, "x_run_id": run_id,
    }


def lineage(items, observed_at):
    return [{"name": n, "value": v, "source": SOURCE_ID, "observed_at": observed_at, "class": "sports_only"}
            for n, v in items]


def outcome(game_pk, player_id, stat, y, fetched_at):
    return {"sport": "MLB", "game_id": str(game_pk), "player_id": f"mlbam:{player_id}",
            "statistic": f"hitter_{STAT_NAMES[stat]}", "actual": y, "played": True,
            "source": "mlb_statsapi_boxscore", "observed_at": fetched_at}
