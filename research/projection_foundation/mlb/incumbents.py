"""Incumbent forecasts available for comparison (all read-only, already committed).

Pitchers  data/research/pitcher_workload_joint/holdout_2025_2026/champion.pure_v0.jsonl.gz
          = production research engine lib/research/pitcher_prop_projection.py, RE-RUN walk-forward
          over 2025-2026 (retroactive generation, as-of first pitch - 60 min, params fitted 2022-24).
          It is a faithful walk-forward reproduction, NOT an archived pregame forecast.
          challenger.pure_v0 = the W1-D joint-workload research challenger (prior best research model).
Hitters   data/research/hitter_w2/audit/incumbent_rerun.jsonl.gz = production hitter engine
          re-run over 2026-08-16..09-27 with archived pregame inputs (fidelity vs archived snapshots
          mean |diff| 0.013-0.015 probability). Full pmfs for PA/H/TB/HR/R/RBI.
Teams     data/pipeline/<date>/projections.json = production team-run projections ARCHIVED pregame
          (createdAt before first pitch), 2026-07-30 onward.
"""
from __future__ import annotations

import glob
import gzip
import json
import os

import numpy as np
import pandas as pd

from pf_core import dists
from . import data as D

PJ = os.path.join(D.DATA, "research", "pitcher_workload_joint", "holdout_2025_2026")
STAT_MAP = {"pitcher_outs": "outs", "pitcher_batters_faced": "bf", "pitcher_strikeouts": "k"}


def load_pitcher_sidecar(arm: str = "champion") -> pd.DataFrame:
    rows = []
    with gzip.open(os.path.join(PJ, f"{arm}.pure_v0.jsonl.gz"), "rt") as f:
        for line in f:
            d = json.loads(line)
            th = {int(t["at_least"]): float(t["probability"]) for t in d["projection"]["thresholds"]}
            rows.append({"game_pk": int(d["game_id"]), "pitcher_id": int(d["player_id"]),
                         "stat": STAT_MAP[d["statistic"]], "inc_mean": float(d["projection"]["mean"]),
                         "inc_th": th, "as_of": d["as_of"]})
    return pd.DataFrame(rows)


def load_hitter_incumbent() -> pd.DataFrame:
    p = os.path.join(D.DATA, "research", "hitter_w2", "audit", "incumbent_rerun.jsonl.gz")
    rows = []
    with gzip.open(p, "rt") as f:
        for line in f:
            d = json.loads(line)
            if d.get("status") != "PROJECTED":
                continue
            r = {"game_pk": int(d["gameId"]), "player_id": int(d["playerId"]), "inc_asof": d.get("asOf"),
                 "inc_slot": d.get("slot")}
            for s in ("PA", "H", "TB", "HR", "R", "RBI"):
                pm = d.get(s) or {}
                r[f"inc_{s}_pmf"] = {int(k): float(v) for k, v in (pm.get("pmf") or {}).items()}
                r[f"inc_{s}_mean"] = pm.get("mean")
            rows.append(r)
    df = pd.DataFrame(rows)
    # one row per player-game: keep the latest as-of (the closest pregame rerun)
    return df.sort_values("inc_asof").drop_duplicates(["game_pk", "player_id"], keep="last")


def pmf_dicts_to_dist(pmfs, K: int) -> dists.DistBatch:
    P = np.zeros((len(pmfs), K))
    for i, pm in enumerate(pmfs):
        for k, v in pm.items():
            P[i, min(int(k), K - 1)] += v
    return dists.DistBatch(0, P)


def thresholds_to_pge(th_list, rungs) -> np.ndarray:
    out = np.full((len(th_list), len(rungs)), np.nan)
    for i, th in enumerate(th_list):
        for j, k in enumerate(rungs):
            if k in th:
                out[i, j] = th[k]
    return out


def load_team_incumbent() -> pd.DataFrame:
    rows = []
    for f in sorted(glob.glob(os.path.join(D.DATA, "pipeline", "*", "projections.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        created = (d.get("meta") or {}).get("createdAt")
        for g in (d.get("data") or {}).get("games", []):
            if g.get("excludedFromSlate"):
                continue
            for side, key in (("A", "awayProjRuns"), ("H", "homeProjRuns")):
                if g.get(key) is None:
                    continue
                rows.append({"game_pk": int(g["gameId"]), "side": side, "inc_mean": float(g[key]),
                             "inc_created": created, "file_date": os.path.basename(os.path.dirname(f))})
    df = pd.DataFrame(rows)
    if len(df):
        df["inc_created"] = pd.to_datetime(df["inc_created"], utc=True, errors="coerce")
    return df
