#!/usr/bin/env python3
"""
scripts/research/mlb_pitcher_workload_joint.py
==============================================
W1-D (RESEARCH ONLY): fit, evaluate and rerun the sports-only joint pitcher
workload challenger `lib/research/pitcher_workload_joint.py` against the frozen
champion `lib/research/pitcher_prop_projection.py` and a naive recent-start baseline.

    # 1. freeze parameters on development seasons only (2022-2024 regular season)
    python3 scripts/research/mlb_pitcher_workload_joint.py fit
    # 2. preregistered held-out evaluation (2025 + 2026 regular season), sidecars + scorecard
    python3 scripts/research/mlb_pitcher_workload_joint.py evaluate --outdir OUT
    # 3. rerun one arm on a data root (runtime market-mutation test / pure_gate rerun)
    python3 scripts/research/mlb_pitcher_workload_joint.py rerun --data-root D --arm challenger --out F

Arms
  challenger  mlb_pitcher_workload_joint_v1 (sports-only; reads only
              lib/research/pitcher_workload_sources.SOURCE_GLOBS)
  champion    lib/research/pitcher_prop_projection.project with the frozen
              data/edgelab/analytics/mlb_pitcher_prop_model_params.json (unchanged), history
              from the champion's own calibration loaders (box scores + Statcast lines)
  baseline    naive recent-start: mean of the last 5 starts; outs/BF discretised normal
              (development residual sd), K Poisson
  champion_postseason (negative control for the mutation test): the champion's
              production postseason path, lib/research/mlb_player_props.PitcherContext with
              postseason_starts_from_settlements(<settled Kalshi KS/OUTS rows>)
"""

from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.research import pitcher_workload_joint as J  # noqa: E402
from lib.research import pitcher_workload_sources as S  # noqa: E402

DEV_SEASONS = ("2022", "2023", "2024")
HOLDOUT_SEASONS = ("2025", "2026")
KERNEL_H = 0.5
POSTSEASON_START = "2026-09-29"
AS_OF_LEAD_MIN = 60
LADDERS = {"pitcher_outs": range(9, 22), "pitcher_batters_faced": range(15, 31), "pitcher_strikeouts": range(2, 11)}
STATS = ("pitcher_outs", "pitcher_batters_faced", "pitcher_strikeouts")
ACTUAL_KEY = {"pitcher_outs": "outs", "pitcher_batters_faced": "bf", "pitcher_strikeouts": "k"}
PARAMS_PATH = os.path.join(ROOT, "data", "research", "pitcher_workload_joint", "params.json")
CHAMPION_PARAMS = os.path.join(ROOT, "data", "edgelab", "analytics", "mlb_pitcher_prop_model_params.json")
BASELINE_N = 5


def as_of_for(kickoff):
    """Forecast cutoff: scheduled first pitch minus AS_OF_LEAD_MIN (ISO UTC, Z)."""
    return _iso(_parse(kickoff) - timedelta(minutes=AS_OF_LEAD_MIN))


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def frozen_hash(paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.relpath(p, ROOT).encode())
        h.update(sha256_file(p).encode())
    return "sha256:" + h.hexdigest()


# ----------------------------------------------------------------------------- data

def sports_dataset(data_root):
    sched = S.load_schedule(data_root)
    games = J.link_mirrors(S.load_pitching_games(data_root, sched))
    return sched, games, J.AsOfIndex(games)


def target_rows(games, index, seasons, min_hist=3):
    rows = []
    for g in games:
        st = g.get("starter")
        if g["season"] not in seasons or not st or st.get("outs") is None or not st.get("bf"):
            continue
        feat, observed = J.features(index, st["pid"], g["team"], g["opp"], g["date"], as_of_for(g["start_utc"]))
        if feat["n_hist"] < min_hist:
            continue
        rows.append({"pk": str(g["pk"]), "pid": st["pid"], "date": g["date"], "season": g["season"],
                     "kickoff": g["start_utc"], "team": g["team"], "opp": g["opp"], "source": g["source"],
                     "outs": st["outs"], "bf": st["bf"], "k": st["k"], "feat": feat, "observed": observed,
                     "postseason": False})
    return rows


def postseason_population(data_root, asof):
    """Sports-defined 2026 postseason starters: StatsAPI schedule fields (gameId, startTime,
    teams, probable pitcher id) of data/pipeline/<date>/normalized_slate.json. Only those
    fields are read; the file's other contents are ignored."""
    out = []
    for f in sorted(glob.glob(os.path.join(data_root, "pipeline", "*", "normalized_slate.json"))):
        d = os.path.basename(os.path.dirname(f))
        if d < POSTSEASON_START or d > asof:
            continue
        with open(f, encoding="utf-8") as fh:
            doc = json.load(fh)
        for g in (doc.get("data") or {}).get("games") or []:
            if not g.get("gameId") or not g.get("startTime"):
                continue
            for side, opp in (("away", "home"), ("home", "away")):
                sp = (g.get(side) or {}).get("pitcher") or {}
                if not sp.get("id"):
                    continue
                out.append({"pk": str(g["gameId"]), "pid": str(sp["id"]), "date": d, "kickoff": g["startTime"],
                            "team": (g.get(side) or {}).get("abbr"), "opp": (g.get(opp) or {}).get("abbr"),
                            "postseason": True, "source": "pipeline_schedule_probable_pitcher"})
    seen, uniq = set(), []
    for r in out:
        if (r["pk"], r["pid"]) not in seen:
            seen.add((r["pk"], r["pid"]))
            uniq.append(r)
    return uniq


# ----------------------------------------------------------------------------- arms

def champion_bf_pmf(c, P):
    bfd = [0.0] * (P.MAX_OUTS + P.MAX_EXTRA_PA + 1)
    for o, po in enumerate(c["outs_pmf"]):
        if po <= 0:
            continue
        lam = c["nonout_per_out"] * o
        for x in range(P.MAX_EXTRA_PA + 1):
            px = P._poisson(x, lam)
            if px < 1e-9:
                if x > lam:
                    break
                continue
            bfd[o + x] += po * px
    s = sum(bfd)
    return [x / s for x in bfd]


class Champion:
    """Frozen champion on sports-only regular-season history (box scores + Statcast lines),
    using the champion's own loaders so this is exactly the documented holdout arm."""

    def __init__(self, data_root):
        sys.path.insert(0, os.path.join(ROOT, "scripts", "research"))
        import mlb_pitcher_prop_calibration as C  # noqa: E402
        from lib.research import pitcher_prop_projection as P  # noqa: E402
        self.C, self.P = C, P
        with open(CHAMPION_PARAMS, encoding="utf-8") as fh:
            self.params = json.load(fh)
        sched = C.load_schedule(data_root)
        box, tg = C.load_boxscore_starts(data_root, sched)
        last_box = max(s["date"] for s in box if s["date"][:4] == "2026")
        sc, _ = C.load_statcast_starts(data_root, sched, last_box)
        self.hist = C.History(box + sc, tg)
        # gamePks whose final line exists only after a later resumption (officialDate earlier
        # than the scheduled/resumed first pitch date)
        own = S.load_schedule(data_root)
        self.late_pks = {pk for pk, g in own.items()
                         if g.get("start_utc") and g["start_utc"][:10] > g["date"] and
                         (_parse(g["start_utc"]) - datetime.fromisoformat(g["date"] + "T00:00:00+00:00")).days >= 2}

    def project(self, pid, date, opp, postseason=False):
        prior = self.hist.before(pid, date)
        if len(prior) < 3:
            return None
        ok, opa = self.hist.opp_rate(opp, date) if opp else (None, 0)
        c = self.P.project(prior, self.params, postseason=postseason, opp_k_rate=ok, opp_pa=opa)
        return {"pitcher_outs": (c["outs_pmf"], c["expected_outs"]),
                "pitcher_batters_faced": (champion_bf_pmf(c, self.P), c["expected_batters_faced"]),
                "pitcher_strikeouts": (c["k_pmf"], c["expected_strikeouts"]),
                "features": {"history_starts": len(prior), "mu_outs": round(c["mu_outs"], 4),
                             "k_rate": round(c["k_rate"], 5), "opponent_k_factor": round(c["opponent_k_factor"], 5),
                             "nonout_per_out": round(c["nonout_per_out"], 5)},
                "observed": max(s["date"] for s in prior),
                "late_history": any(s.get("pk") in self.late_pks for s in prior) if self.late_pks else False}


def _disc_normal(mu, sd, n):
    out = []
    for x in range(n + 1):
        lo, hi = (x - 0.5 - mu) / sd, (x + 0.5 - mu) / sd
        out.append(0.5 * (math.erf(hi / math.sqrt(2)) - math.erf(lo / math.sqrt(2))))
    s = sum(out)
    return [v / s for v in out]


def _poisson_pmf(lam, n):
    out = [math.exp(-lam + x * math.log(lam) - math.lgamma(x + 1)) if lam > 0 else (1.0 if x == 0 else 0.0) for x in range(n + 1)]
    s = sum(out)
    return [v / s for v in out]


def baseline_means(index, pid, date, cutoff=None):
    prior = index.prior_starts(pid, date, cutoff)[-BASELINE_N:]
    if not prior:
        return None
    m = {}
    for stat, key in ACTUAL_KEY.items():
        vals = [g["starter"][key] for g in prior if g["starter"].get(key) is not None]
        m[stat] = sum(vals) / len(vals) if vals else None
    return m


def baseline_project(index, pid, date, bparams, cutoff=None):
    m = baseline_means(index, pid, date, cutoff)
    if m is None or any(v is None for v in m.values()):
        return None
    return {"pitcher_outs": (_disc_normal(m["pitcher_outs"], bparams["sd_outs"], J.MAX_OUTS), m["pitcher_outs"]),
            "pitcher_batters_faced": (_disc_normal(m["pitcher_batters_faced"], bparams["sd_bf"], J.MAX_BF), m["pitcher_batters_faced"]),
            "pitcher_strikeouts": (_poisson_pmf(m["pitcher_strikeouts"], J.MAX_BF), m["pitcher_strikeouts"])}


def challenger_project(feat, params):
    pr = J.project(feat, params)
    return {"pitcher_outs": (pr["outs_pmf"], pr["expected_outs"]),
            "pitcher_batters_faced": (pr["bf_pmf"], pr["expected_batters_faced"]),
            "pitcher_strikeouts": (pr["k_pmf"], pr["expected_strikeouts"]), "raw": pr}


# ----------------------------------------------------------------------------- fit

def cmd_fit(a):
    sched, games, index = sports_dataset(a.data_root)
    dev = set(a.dev_seasons.split(","))
    league = J.league_constants(games, dev)
    train = []
    for g in games:
        st = g.get("starter")
        if g["season"] not in dev or not st or not st.get("bf") or st.get("outs") is None:
            continue
        feat, _ = J.features(index, st["pid"], g["team"], g["opp"], g["date"], as_of_for(g["start_utc"]))
        if feat["n_hist"] < 1:
            continue
        train.append({"feat": feat, "bf": st["bf"], "outs": st["outs"], "k": st["k"], "pid": st["pid"], "date": g["date"],
                      "cutoff": as_of_for(g["start_utc"])})
    params = J.fit(train, league=league, kernel_h=a.kernel_h)
    # naive baseline dispersion, development only
    res = {"outs": [], "bf": []}
    for r in train:
        m = baseline_means(index, r["pid"], r["date"], r["cutoff"])
        if m and m["pitcher_outs"] is not None and m["pitcher_batters_faced"] is not None:
            res["outs"].append(r["outs"] - m["pitcher_outs"])
            res["bf"].append(r["bf"] - m["pitcher_batters_faced"])
    params["baseline"] = {"n_last": BASELINE_N, "sd_outs": round(statistics.pstdev(res["outs"]), 4),
                          "sd_bf": round(statistics.pstdev(res["bf"]), 4)}
    params["model_version"] = J.MODEL_VERSION
    params["dev_seasons"] = sorted(dev)
    params["fit"]["dev_rows"] = len(train)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(params, fh, sort_keys=True, separators=(",", ":"))
    print(json.dumps({k: v for k, v in params.items() if k not in ("outs_table",)}, sort_keys=True))
    return 0


# ----------------------------------------------------------------------------- sidecars

def _times(kickoff, observed_iso):
    k = _parse(kickoff)
    as_of = k - timedelta(minutes=AS_OF_LEAD_MIN)
    obs = _parse(observed_iso) if observed_iso and "T" in observed_iso else (
        datetime.fromisoformat(observed_iso + "T00:00:00+00:00") if observed_iso else as_of)
    return _iso(as_of), _iso(k), _iso(obs), obs <= as_of


def projection_obj(stat, pmf, mean):
    t = J.tail(pmf)
    thr = [{"at_least": n, "probability": round(t[n] if n < len(t) else 0.0, 6)} for n in LADDERS[stat]]
    return {"mean": round(mean, 4), "median": J.quantile(pmf, 0.5), "p10": J.quantile(pmf, 0.1),
            "p90": J.quantile(pmf, 0.9), "thresholds": thr}


def v0_row(game_id, pid, stat, as_of, kickoff, observed, model_version, proj, features):
    return {"sport": "MLB", "game_id": game_id, "player_id": pid, "statistic": stat, "as_of": as_of,
            "kickoff": kickoff, "source_max_observed_at": observed, "model_version": model_version,
            "projection_mode": "PURE_INDEPENDENT", "conditional_on_playing": True, "projection": proj,
            "model_features": features}


def v1_row(base, frozen, lineage, sources, proj):
    p = dict(proj)
    p["quantiles"] = [{"q": q, "value": v} for q, v in ((0.1, proj["p10"]), (0.5, proj["median"]), (0.9, proj["p90"]))]
    row = {k: v for k, v in base.items() if k != "model_features"}
    row.update({"schema_version": "pure_forecast.v1", "model_frozen_hash": frozen, "participation_probability": None,
                "sources": sources, "feature_lineage": lineage, "projection": p})
    return row


def challenger_lineage(feat, observed):
    lin = []
    for k in J.FEATURE_KEYS:
        v = feat[k]
        if isinstance(v, tuple):
            v = round(v[0] / v[1], 5) if v[1] else None
        elif isinstance(v, float):
            v = round(v, 5)
        lin.append({"name": k, "value": v, "source": "mlb_statsapi_boxscores_and_statcast_pitch_log",
                    "observed_at": observed, "class": "sports_only"})
    return lin


def challenger_features(feat):
    out = {}
    for k in J.FEATURE_KEYS:
        v = feat[k]
        if isinstance(v, tuple):
            v = round(v[0] / v[1], 5) if v[1] else None
        elif isinstance(v, float):
            v = round(v, 5)
        out[k] = v
    return out


# ----------------------------------------------------------------------------- scoring

def _nll(pmf, y):
    return -math.log(max(1e-9, pmf[y] if 0 <= y < len(pmf) else 0.0))


def score_rows(rows_by_arm, stat, keys):
    """Per-arm per-row scores on matched keys. Threshold: one rung per starter-game-stat,
    the ladder strike whose champion/challenger mean probability is closest to 0.5
    (outcome-blind, symmetric), applied identically to every arm."""
    out = {arm: [] for arm in rows_by_arm if arm != "_actual"}
    for key in keys:
        y = rows_by_arm["_actual"][key]
        ch, cl = rows_by_arm["champion"][key], rows_by_arm["challenger"][key]
        tc, tl = J.tail(ch[0]), J.tail(cl[0])
        best = min(LADDERS[stat], key=lambda n: (abs((tc[n] + tl[n]) / 2 - 0.5), n))
        for arm, d in rows_by_arm.items():
            if arm == "_actual":
                continue
            pmf, mean = d[key]
            t = J.tail(pmf)
            hit = 1.0 if y >= best else 0.0
            out[arm].append({"key": key, "game": key[0], "err": mean - y, "ae": abs(mean - y), "se": (mean - y) ** 2,
                             "cov": 1.0 if J.quantile(pmf, 0.1) <= y <= J.quantile(pmf, 0.9) else 0.0,
                             "brier": (t[best] - hit) ** 2, "nll": _nll(pmf, y)})
    return out


METRICS = ("ae", "se", "brier", "nll", "cov", "err")


def summarize(sc):
    n = len(sc)
    if not n:
        return {"n": 0}
    return {"n": n, "games": len({r["game"] for r in sc}),
            "mae": round(sum(r["ae"] for r in sc) / n, 4),
            "median_abs_error": round(statistics.median(r["ae"] for r in sc), 4),
            "median_signed_error": round(statistics.median(r["err"] for r in sc), 4),
            "rmse": round(math.sqrt(sum(r["se"] for r in sc) / n), 4),
            "bias": round(sum(r["err"] for r in sc) / n, 4),
            "p10_p90_coverage": round(sum(r["cov"] for r in sc) / n, 4),
            "threshold_brier": round(sum(r["brier"] for r in sc) / n, 5),
            "log_loss": round(sum(r["nll"] for r in sc) / n, 4)}


def paired_ci(a, b, reps=1000, seed=20261010):
    """Challenger(a) - comparator(b) deltas with a game-clustered paired bootstrap."""
    assert [r["key"] for r in a] == [r["key"] for r in b]
    groups = defaultdict(lambda: [0.0] * (len(METRICS) + 1))
    for ra, rb in zip(a, b):
        g = groups[ra["game"]]
        for i, m in enumerate(METRICS):
            g[i] += ra[m] - rb[m]
        g[-1] += 1
    keys = sorted(groups)
    tot = [sum(groups[k][i] for k in keys) for i in range(len(METRICS) + 1)]
    point = {m: tot[i] / tot[-1] for i, m in enumerate(METRICS)}
    rng = random.Random(seed)
    sims = {m: [] for m in METRICS}
    for _ in range(reps):
        acc = [0.0] * (len(METRICS) + 1)
        for _k in keys:
            g = groups[keys[rng.randrange(len(keys))]]
            for i in range(len(acc)):
                acc[i] += g[i]
        for i, m in enumerate(METRICS):
            sims[m].append(acc[i] / acc[-1])
    out = {}
    names = {"ae": "delta_mae", "se": "delta_mse", "brier": "delta_threshold_brier", "nll": "delta_log_loss",
             "cov": "delta_p10_p90_coverage", "err": "delta_bias"}
    for m in METRICS:
        s = sorted(sims[m])
        out[names[m]] = {"point": round(point[m], 5), "ci95": [round(s[int(0.025 * reps)], 5), round(s[int(0.975 * reps) - 1], 5)]}
    return out


# ----------------------------------------------------------------------------- evaluate

def subgroup_of(feat):
    tags = []
    wp = feat["w_pitches"]
    if wp is not None:
        tags.append("short_leash_wpitches_lt80" if wp < 80 else ("long_leash_wpitches_ge95" if wp >= 95 else "mid_leash"))
    kr = (feat["k_rate_hist_k"] + 100 * 0.2184) / (feat["k_rate_hist_bf"] + 100)
    tags.append("low_k_rate_lt0.20" if kr < 0.20 else ("high_k_rate_gt0.26" if kr > 0.26 else "mid_k_rate"))
    if feat["season_starts"] == 0:
        tags.append("season_debut")
    elif feat["days_rest"] is not None and feat["days_rest"] >= 15:
        tags.append("first_start_after_return_ge15d")
    return tags


def cmd_evaluate(a):
    os.makedirs(a.outdir, exist_ok=True)
    with open(a.params, encoding="utf-8") as fh:
        params = json.load(fh)
    frozen = {"challenger": frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_workload_joint.py"),
                                         os.path.join(ROOT, "lib", "research", "pitcher_workload_sources.py"), a.params]),
              "champion": frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_prop_projection.py"), CHAMPION_PARAMS]),
              "baseline": frozen_hash([os.path.abspath(__file__), a.params])}
    versions = {"challenger": J.MODEL_VERSION, "champion": "pitcher_prop_projection@params_asof_2026-10-07",
                "baseline": "naive_recent5_v1"}
    sched, games, index = sports_dataset(a.data_root)
    rows = target_rows(games, index, set(a.seasons.split(",")))
    champ = Champion(a.data_root)
    files = {}
    for arm in ("challenger", "champion", "baseline"):
        files[(arm, "v0")] = open(os.path.join(a.outdir, f"{arm}.pure_v0.jsonl"), "w", encoding="utf-8")
        files[(arm, "v1")] = open(os.path.join(a.outdir, f"{arm}.pure_v1.jsonl"), "w", encoding="utf-8")
    outcomes = open(os.path.join(a.outdir, "outcomes.jsonl"), "w", encoding="utf-8")
    data = {stat: {"challenger": {}, "champion": {}, "baseline": {}, "_actual": {}} for stat in STATS}
    meta = {}
    excl = defaultdict(int)
    src = [{"source_id": "mlb_statsapi_boxscores_and_statcast_pitch_log", "class": "sports_only",
            "description": "research_cache starter_workload box scores (2022-2026-08-26) and Statcast pitch-log lines (2026-08-27..09-27); schedule first-pitch times",
            "max_observed_at": None}]
    for r in rows:
        cp = champ.project(r["pid"], r["date"], r["opp"])
        bp = baseline_project(index, r["pid"], r["date"], params["baseline"], as_of_for(r["kickoff"]))
        if cp is None:
            excl["champion_lt3_prior_starts"] += 1
            continue
        if cp["late_history"]:
            # the champion orders history by officialDate only; a suspended game resumed after
            # this forecast's cutoff would leak a line it could not have seen -> exclude row
            excl["champion_history_has_game_completed_after_as_of"] += 1
            continue
        if bp is None:
            excl["baseline_missing_history_values"] += 1
            continue
        as_of, kickoff, observed, ok = _times(r["kickoff"], r["observed"])
        if not ok:
            excl["source_observed_after_as_of"] += 1
            continue
        xp = challenger_project(r["feat"], params)
        key = (r["pk"], r["pid"])
        meta[key] = {"season": r["season"], "tags": subgroup_of(r["feat"]), "source": r["source"]}
        lin = challenger_lineage(r["feat"], observed)
        for stat in STATS:
            y = r[ACTUAL_KEY[stat]]
            data[stat]["_actual"][key] = y
            outcomes.write(json.dumps({"sport": "MLB", "game_id": r["pk"], "player_id": r["pid"], "statistic": stat,
                                       "actual": y, "played": True, "source": r["source"]}, sort_keys=True) + "\n")
            for arm, proj, feats in (("challenger", xp, challenger_features(r["feat"])),
                                     ("champion", cp, cp["features"]),
                                     ("baseline", bp, {"n_last_starts": BASELINE_N})):
                pmf, mean = proj[stat]
                data[stat][arm][key] = (pmf, mean)
                po = projection_obj(stat, pmf, mean)
                base = v0_row(r["pk"], r["pid"], stat, as_of, kickoff, observed, versions[arm], po, feats)
                files[(arm, "v0")].write(json.dumps(base, sort_keys=True) + "\n")
                if arm == "challenger":
                    lineage = lin
                else:
                    lineage = [{"name": k, "value": v, "source": "mlb_statsapi_boxscores_and_statcast_pitch_log",
                                "observed_at": observed, "class": "sports_only"} for k, v in feats.items()]
                s1 = [dict(src[0], max_observed_at=observed)]
                files[(arm, "v1")].write(json.dumps(v1_row(base, frozen[arm], lineage, s1, po), sort_keys=True) + "\n")
    for f in files.values():
        f.close()
    outcomes.close()
    # scorecard
    card = {"params": a.params, "frozen_hashes": frozen, "versions": versions, "seasons": a.seasons,
            "eligible_rows_n_hist_ge3_with_bf": len(rows), "exclusions": dict(excl), "stats": {}}
    for stat in STATS:
        keys = sorted(data[stat]["_actual"])
        sc = score_rows(data[stat], stat, keys)
        st = {"overall": {arm: summarize(v) for arm, v in sc.items()},
              "challenger_minus_champion": paired_ci(sc["challenger"], sc["champion"], reps=a.reps),
              "challenger_minus_baseline": paired_ci(sc["challenger"], sc["baseline"], reps=a.reps),
              "champion_minus_baseline": paired_ci(sc["champion"], sc["baseline"], reps=a.reps)}
        groups = defaultdict(list)
        for i, row in enumerate(sc["challenger"]):
            m = meta[row["key"]]
            groups["season_" + m["season"]].append(i)
            groups["outcome_source_" + m["source"]].append(i)
            for t in m["tags"]:
                groups[t].append(i)
        st["subgroups"] = {}
        for g, idx in sorted(groups.items()):
            pick = {arm: [v[i] for i in idx] for arm, v in sc.items()}
            st["subgroups"][g] = {"summary": {arm: summarize(v) for arm, v in pick.items()},
                                  "challenger_minus_champion": paired_ci(pick["challenger"], pick["champion"], reps=a.reps)
                                  if len({r["game"] for r in pick["challenger"]}) >= 3 else None}
        card["stats"][stat] = st
        print(stat, json.dumps(st["overall"]), json.dumps(st["challenger_minus_champion"]))
    card["subgroups_blocked"] = {"lineup_confirmed_vs_unconfirmed": "BLOCKED_DATA: no as-of lineup status before 2026-07-30; neither arm reads lineups",
                                 "postseason": "see postseason_diagnostic.json (market-selected outcomes, diagnostic only)"}
    with open(os.path.join(a.outdir, "scorecard.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=1, sort_keys=True)
    if a.postseason_diagnostic:
        postseason_diagnostic(a, params, games, index, champ)
    return 0


def _settled_postseason_actuals(data_root):
    """DIAGNOSTIC-ONLY outcome labels: settlement evidence (MLB StatsAPI values) of settled
    Kalshi pitcher KS/OUTS markets. Market-selected population; never a model input."""
    from lib.research import mlb_player_props as M
    rows = []
    for f in sorted(glob.glob(os.path.join(data_root, "edgelab", "settlements", "*.jsonl*"))):
        if os.path.basename(f) < "2026-09-28":
            continue
        op = gzip.open if f.endswith(".gz") else open
        with op(f, "rt", encoding="utf-8") as fh:
            rows += [json.loads(x) for x in fh if x.strip()]
    out = {}
    for r in rows:
        if r.get("marketFamily") not in M.PITCHER_ENGINE_FAMILIES:
            continue
        ev = r.get("settlementEvidence") or {}
        c = ev.get("candidates") or []
        if len(c) != 1 or ev.get("actualValue") is None:
            continue
        key = (str(ev.get("gamePk") or r.get("gameId")), str(c[0]["playerId"]))
        out.setdefault(key, {})["pitcher_outs" if r["marketFamily"] == "pitcher_outs" else "pitcher_strikeouts"] = ev["actualValue"]
    return rows, out


def postseason_diagnostic(a, params, games, index, champ):
    from lib.research import mlb_player_props as M
    pop = postseason_population(a.data_root, a.asof)
    settle_rows, actual = _settled_postseason_actuals(a.data_root)
    ctrl = ChampionPostseason(a.data_root, settle_rows)
    res = {s: {"challenger": [], "champion_regular_history": [], "champion_postseason_path": [], "y": [], "game": []}
           for s in ("pitcher_outs", "pitcher_strikeouts")}
    n_pop = len(pop)
    for r in pop:
        y = actual.get((r["pk"], r["pid"]))
        if not y:
            continue
        feat, _ = J.features(index, r["pid"], r["team"], r["opp"], r["date"], as_of_for(r["kickoff"]))
        if feat["n_hist"] < 3:
            continue
        xp = challenger_project(feat, params)
        cr = champ.project(r["pid"], r["date"], r["opp"], postseason=True)
        cpp = ctrl.project(r)
        if cr is None or cpp is None or cr["late_history"]:
            continue
        for stat in res:
            if stat not in y:
                continue
            d = res[stat]
            d["y"].append(y[stat])
            d["game"].append(r["pk"])
            d["challenger"].append(xp[stat])
            d["champion_regular_history"].append(cr[stat])
            d["champion_postseason_path"].append(cpp[stat])
    out = {"population": "2026 postseason probable starters from pipeline schedule snapshots", "n_population": n_pop,
           "label_source": "settled Kalshi pitcher KS/OUTS settlementEvidence.actualValue (MLB_STATS_API values)",
           "status": "DIAGNOSTIC_ONLY_MARKET_SELECTED_NOT_A_GATE", "stats": {}}
    for stat, d in res.items():
        st = {"n": len(d["y"]), "games": len(set(d["game"]))}
        for arm in ("challenger", "champion_regular_history", "champion_postseason_path"):
            errs = [m - yy for (pmf, m), yy in zip(d[arm], d["y"])]
            if not errs:
                continue
            st[arm] = {"mae": round(sum(abs(e) for e in errs) / len(errs), 4), "bias": round(sum(errs) / len(errs), 4),
                       "rmse": round(math.sqrt(sum(e * e for e in errs) / len(errs)), 4),
                       "log_loss": round(sum(_nll(pmf, yy) for (pmf, _m), yy in zip(d[arm], d["y"])) / len(errs), 4),
                       "p10_p90_coverage": round(sum(1 for (pmf, _m), yy in zip(d[arm], d["y"])
                                                     if J.quantile(pmf, .1) <= yy <= J.quantile(pmf, .9)) / len(errs), 4)}
        out["stats"][stat] = st
    with open(os.path.join(a.outdir, "postseason_diagnostic.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print("postseason diagnostic", json.dumps(out["stats"]))


class ChampionPostseason:
    """NEGATIVE CONTROL: the champion's production postseason path. Reads settled Kalshi
    KS/OUTS rows (via lib/research/mlb_player_props.postseason_starts_from_settlements) as
    history observations, exactly as scripts/app_export.py does."""

    def __init__(self, data_root, settle_rows=None):
        from lib.research import mlb_player_props as M
        from lib.research import pitcher_prop_projection as P
        self.M, self.P = M, P
        params, starts, team_games = M.load_pitcher_model(data_root)
        if settle_rows is None:
            settle_rows = []
            for f in sorted(glob.glob(os.path.join(data_root, "edgelab", "settlements", "*.jsonl*"))):
                if os.path.basename(f) < POSTSEASON_START:
                    continue
                op = gzip.open if f.endswith(".gz") else open
                with op(f, "rt", encoding="utf-8") as fh:
                    settle_rows += [json.loads(x) for x in fh if x.strip()]
        self.ctx = M.PitcherContext(params, starts, team_games, M.postseason_starts_from_settlements(settle_rows))

    def project(self, r):
        prior = self.ctx.history(r["pid"], r["date"])
        if len(prior) < 3:
            return None
        ok, opa = self.ctx.opp_rate(r["opp"], r["date"])
        c = self.P.project(prior, self.ctx.params, postseason=True, opp_k_rate=ok, opp_pa=opa)
        return {"pitcher_outs": (c["outs_pmf"], c["expected_outs"]), "pitcher_strikeouts": (c["k_pmf"], c["expected_strikeouts"]),
                "pitcher_batters_faced": (champion_bf_pmf(c, self.P), c["expected_batters_faced"])}


# ----------------------------------------------------------------------------- rerun (mutation test)

def cmd_rerun(a):
    """Deterministic projection of one arm on a FIXED, sports-defined population: every
    2026 regular-season start from --from-date (n_hist >= 3) plus the 2026 postseason
    probable starters through --asof. Output: canonical JSONL (sorted keys/rows)."""
    out_rows = []
    if a.arm == "challenger":
        with open(a.params, encoding="utf-8") as fh:
            params = json.load(fh)
        sched, games, index = sports_dataset(a.data_root)
        pop = [r for r in target_rows(games, index, {"2026"}) if r["date"] >= a.from_date]
        for r in postseason_population(a.data_root, a.asof):
            feat, obs = J.features(index, r["pid"], r["team"], r["opp"], r["date"], as_of_for(r["kickoff"]))
            if feat["n_hist"] >= 3:
                pop.append({**r, "feat": feat, "observed": obs})
        for r in pop:
            xp = challenger_project(r["feat"], params)
            for stat in STATS:
                pmf, mean = xp[stat]
                out_rows.append({"game_id": r["pk"], "player_id": r["pid"], "statistic": stat, "postseason": r["postseason"],
                                 "projection": projection_obj(stat, pmf, mean), "model_version": J.MODEL_VERSION,
                                 "model_features": challenger_features(r["feat"])})
    elif a.arm == "champion_postseason":
        ctrl = ChampionPostseason(a.data_root)
        for r in postseason_population(a.data_root, a.asof):
            cp = ctrl.project(r)
            if cp is None:
                continue
            for stat in STATS:
                pmf, mean = cp[stat]
                out_rows.append({"game_id": r["pk"], "player_id": r["pid"], "statistic": stat, "postseason": True,
                                 "projection": projection_obj(stat, pmf, mean), "model_version": "champion_postseason_path"})
    else:
        raise SystemExit(f"unknown arm {a.arm}")
    out_rows.sort(key=lambda x: (x["game_id"], x["player_id"], x["statistic"]))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for row in out_rows:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps({"arm": a.arm, "rows": len(out_rows), "sha256": sha256_file(a.out)}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit")
    f.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    f.add_argument("--dev-seasons", default=",".join(DEV_SEASONS))
    f.add_argument("--kernel-h", type=float, default=KERNEL_H)
    f.add_argument("--out", default=PARAMS_PATH)
    e = sub.add_parser("evaluate")
    e.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    e.add_argument("--params", default=PARAMS_PATH)
    e.add_argument("--seasons", default=",".join(HOLDOUT_SEASONS))
    e.add_argument("--outdir", required=True)
    e.add_argument("--reps", type=int, default=1000)
    e.add_argument("--asof", default="2026-10-10")
    e.add_argument("--postseason-diagnostic", action="store_true")
    r = sub.add_parser("rerun")
    r.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    r.add_argument("--params", default=PARAMS_PATH)
    r.add_argument("--arm", choices=("challenger", "champion_postseason"), required=True)
    r.add_argument("--from-date", default="2026-09-01")
    r.add_argument("--asof", default="2026-10-10")
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return {"fit": cmd_fit, "evaluate": cmd_evaluate, "rerun": cmd_rerun}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
