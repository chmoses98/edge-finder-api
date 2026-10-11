#!/usr/bin/env python3
"""
scripts/research/hitter_w2/decompose_incumbent.py
=================================================
SIFT W2 hitter lane -- RESEARCH ONLY. Audit decomposition of the INCUMBENT
hitter engine's signed errors on the full eligible population, using the
incumbent rerun (incumbent_rerun.py) joined to official MLB box-score / play-by-
play outcomes (data/research/hitter_w2/mlb_feed/2026/).

Components:
  * level bias for PA, H, TB, HR, RBI, R (game-clustered bootstrap CIs)
  * H bias = PA-count part + per-PA-rate part (starter part + bullpen part)
  * RBI bias = HR-RBI part + non-HR part; non-HR RBI = PA x runners-on-base per PA
    x non-HR RBI per runner-on (log-ratio shares)
  * R bias = times-on-base part + scoring-rate part
  * PA by lineup slot; share of PA vs the starter
  * observation choice: market-listed cohort (a hitter-projection snapshot row existed
    for that player-game pregame) vs not listed; Kalshi-settlement actualValue vs box score
Writes JSON to --out.
"""
import argparse
import glob
import gzip
import json
import math
import os
import pickle
import random
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def boot_ci(rows, f, reps=1000, seed=7):
    """rows: list of (cluster, values-tuple); f(list_of_value_tuples)->float. Game-clustered."""
    cl = defaultdict(list)
    for c, v in rows:
        cl[c].append(v)
    names = sorted(cl)
    rng = random.Random(seed)
    point = f([v for c in names for v in cl[c]])
    draws = []
    for _ in range(reps):
        vals = []
        for _k in range(len(names)):
            vals.extend(cl[names[int(rng.random() * len(names))]])
        draws.append(f(vals))
    draws.sort()
    return {"value": round(point, 5), "ci95": [round(draws[int(0.025 * reps)], 5), round(draws[int(0.975 * reps) - 1], 5)]}


def mean_of(i):
    return lambda vals: sum(v[i] for v in vals) / len(vals)


def listed_player_games():
    """(gamePk, playerId) with >=1 PROJECTED hitter-projection snapshot row (market-listed cohort,
    defined pregame from snapshot rows, never from settlement)."""
    out = set()
    for f in glob.glob(os.path.join(_ROOT, "data", "edgelab", "hitter_projection_snapshots", "*.jsonl")):
        with open(f) as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("projectionStatus") == "PROJECTED" and r.get("gameId") and r.get("playerId"):
                    out.add((str(r["gameId"]), str(r["playerId"])))
    return out


def settlement_actuals():
    fam = {"KXMLBHIT": "H", "KXMLBTB": "TB", "KXMLBRBI": "RBI", "KXMLBHRR": "HRR"}
    out = {}
    for f in glob.glob(os.path.join(_ROOT, "data", "edgelab", "settlements", "*.jsonl.gz")):
        with gzip.open(f, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                t = r.get("marketTicker") or ""
                s = fam.get(t.split("-")[0])
                ev = r.get("settlementEvidence") or {}
                if not s or ev.get("actualValue") is None or ev.get("playerId") is None:
                    continue
                out[(str(ev.get("gamePk") or r.get("gameId")), str(ev["playerId"]), s)] = ev["actualValue"]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", required=True)
    ap.add_argument("--records-cache", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if os.path.exists(a.records_cache):
        with gzip.open(a.records_cache, "rb") as f:
            rec26 = pickle.load(f)
    else:
        import run_hitter_w2 as R
        _sd, rec26, _prev = R.holdout_records(os.path.join(_ROOT, "data", "research", "hitter_w2", "mlb_feed"))
        with gzip.open(a.records_cache, "wb") as f:
            pickle.dump(rec26, f, protocol=4)
    actual = {}
    for rec in rec26:
        if rec.get("excluded"):
            continue
        for b in rec["batters"]:
            actual[(str(rec["gamePk"]), str(b["playerId"]))] = (rec, b)
    inc_rows, status = [], defaultdict(int)
    unmatched = defaultdict(int)
    with gzip.open(a.rerun, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            status[r["status"]] += 1
            if r["status"] != "PROJECTED":
                continue
            k = (str(r["gameId"]), str(r["playerId"]))
            if k not in actual:
                unmatched["NOT_A_BOX_SCORE_STARTER_OR_GAME_MISSING"] += 1
                continue
            rec, b = actual[k]
            side = "away" if rec["side"] == "A" else "home"
            if side != r["side"]:
                unmatched["SIDE_MISMATCH"] += 1
                continue
            if b["slot"] != r["slot"]:
                unmatched["SLOT_DIFFERS_FROM_BOX_SCORE"] += 1
            inc_rows.append((r, rec, b))
    listed = listed_player_games()
    out = {"incumbent_rerun_status": dict(status), "unmatched": dict(unmatched), "n_matched": len(inc_rows),
           "n_games": len({x[1]["gamePk"] for x in inc_rows}),
           "dates": [min(x[0]["date"] for x in inc_rows), max(x[0]["date"] for x in inc_rows)]}

    def rows_for(sel):
        res = []
        for r, rec, b in inc_rows:
            if not sel(r, rec, b):
                continue
            y = b["y"]
            v = (r["PA"]["mean"], y["PA"], r["H"]["mean"], y["H"], r["TB"]["mean"], y["TB"], r["HR"]["mean"], y["HR"],
                 r["RBI"]["mean"], y["RBI"], r["R"]["mean"], y["R"],
                 r["ins_PA_starter"], b["y_pa_vs_sp"], r["ins_H_starter"], b["y_h_vs_sp"],
                 r["ins_PA_bullpen"], y["PA"] - b["y_pa_vs_sp"], r["ins_H_bullpen"], y["H"] - b["y_h_vs_sp"],
                 r["ins_ROB"], b["y_rob"], r["ins_RBI_nonHR"], b["y_rbi_nonhr"],
                 r["H"]["mean"] + r["BB"]["mean"], y["H"] + y["BB"] + y["HBP"],
                 r["BB"]["mean"], y["BB"], r["K"]["mean"], y["K"], b["y_pa_pbp"])
            res.append((rec["gamePk"], v))
        return res

    def bias_block(rows):
        blk = {}
        for name, i in (("PA", 0), ("H", 2), ("TB", 4), ("HR", 6), ("RBI", 8), ("R", 10), ("BB", 26), ("K", 28)):
            blk[name] = {
                "mean_forecast": round(sum(v[i] for _c, v in rows) / len(rows), 5),
                "mean_actual": round(sum(v[i + 1] for _c, v in rows) / len(rows), 5),
                "bias": boot_ci(rows, lambda vals, i=i: sum(v[i] - v[i + 1] for v in vals) / len(vals)),
                "ratio_forecast_over_actual": round(sum(v[i] for _c, v in rows) / max(1e-9, sum(v[i + 1] for _c, v in rows)), 4),
            }
        return blk

    allrows = rows_for(lambda r, rec, b: True)
    out["bias_full_population"] = bias_block(allrows)
    out["n_full_population"] = len(allrows)
    lr = rows_for(lambda r, rec, b: (str(rec["gamePk"]), str(b["playerId"])) in listed)
    nr = rows_for(lambda r, rec, b: (str(rec["gamePk"]), str(b["playerId"])) not in listed)
    out["observation_choice"] = {"market_listed_cohort": {"n": len(lr), **(bias_block(lr) if lr else {})},
                                 "not_listed": {"n": len(nr), **(bias_block(nr) if nr else {})}}

    # ---- H decomposition: H_hat - H = (PA_hat - PA) * r_hat + (PA * r_hat - H); rate part split S/B ----
    def hdec(vals):
        n = len(vals)
        pa_part = sum((v[0] - v[1]) * (v[2] / v[0] if v[0] else 0) for v in vals) / n
        rate_part = sum(v[1] * (v[2] / v[0] if v[0] else 0) - v[3] for v in vals) / n
        return pa_part, rate_part
    out["hits_decomposition"] = {
        "total_bias": boot_ci(allrows, lambda vals: sum(v[2] - v[3] for v in vals) / len(vals)),
        "pa_count_part": boot_ci(allrows, lambda vals: hdec(vals)[0]),
        "per_pa_rate_part": boot_ci(allrows, lambda vals: hdec(vals)[1]),
        "rate_part_vs_starter": boot_ci(allrows, lambda vals: sum(
            v[13] * (v[14] / v[12] if v[12] else 0) - v[15] for v in vals) / len(vals)),
        "rate_part_vs_bullpen": boot_ci(allrows, lambda vals: sum(
            v[17] * (v[18] / v[16] if v[16] else 0) - v[19] for v in vals) / len(vals)),
        "h_per_pa_forecast": round(sum(v[2] for _c, v in allrows) / sum(v[0] for _c, v in allrows), 5),
        "h_per_pa_actual": round(sum(v[3] for _c, v in allrows) / sum(v[1] for _c, v in allrows), 5),
        "h_per_pa_vs_starter_forecast": round(sum(v[14] for _c, v in allrows) / sum(v[12] for _c, v in allrows), 5),
        "h_per_pa_vs_starter_actual": round(sum(v[15] for _c, v in allrows) / sum(v[13] for _c, v in allrows), 5),
        "h_per_pa_vs_bullpen_forecast": round(sum(v[18] for _c, v in allrows) / sum(v[16] for _c, v in allrows), 5),
        "h_per_pa_vs_bullpen_actual": round(sum(v[19] for _c, v in allrows) / sum(v[17] for _c, v in allrows), 5),
        "share_pa_vs_starter_forecast": round(sum(v[12] for _c, v in allrows) / sum(v[0] for _c, v in allrows), 4),
        "share_pa_vs_starter_actual": round(sum(v[13] for _c, v in allrows) / sum(v[1] for _c, v in allrows), 4),
        "bb_per_pa_forecast": round(sum(v[26] for _c, v in allrows) / sum(v[0] for _c, v in allrows), 5),
        "bb_per_pa_actual": round(sum(v[27] for _c, v in allrows) / sum(v[1] for _c, v in allrows), 5),
        "k_per_pa_forecast": round(sum(v[28] for _c, v in allrows) / sum(v[0] for _c, v in allrows), 5),
        "k_per_pa_actual": round(sum(v[29] for _c, v in allrows) / sum(v[1] for _c, v in allrows), 5),
    }
    # ---- RBI decomposition ----
    S = lambda i: sum(v[i] for _c, v in allrows)  # noqa: E731
    pa_f, pa_a = S(0), S(1)
    rob_f, rob_a = S(20), S(21)
    nh_f, nh_a = S(22), S(23)
    hr_rbi_f = S(8) - nh_f
    hr_rbi_a = S(9) - nh_a
    n = len(allrows)
    lr_total = math.log(nh_f / nh_a)
    comp = {"pa": math.log(pa_f / pa_a), "runners_on_per_pa": math.log((rob_f / pa_f) / (rob_a / pa_a)),
            "nonhr_rbi_per_runner_on": math.log((nh_f / rob_f) / (nh_a / rob_a))}
    out["rbi_decomposition"] = {
        "total_bias": out["bias_full_population"]["RBI"]["bias"],
        "hr_rbi_part_per_player_game": round((hr_rbi_f - hr_rbi_a) / n, 5),
        "nonhr_rbi_part_per_player_game": round((nh_f - nh_a) / n, 5),
        "nonhr_rbi_part_ci": boot_ci(allrows, lambda vals: sum(v[22] - v[23] for v in vals) / len(vals)),
        "runners_on_per_pa_forecast": round(rob_f / pa_f, 5), "runners_on_per_pa_actual": round(rob_a / pa_a, 5),
        "nonhr_rbi_per_runner_on_forecast": round(nh_f / rob_f, 5), "nonhr_rbi_per_runner_on_actual": round(nh_a / rob_a, 5),
        "log_ratio_nonhr_rbi_total": round(lr_total, 5),
        "log_ratio_components": {k: round(v, 5) for k, v in comp.items()},
        "share_of_log_ratio": {k: round(v / lr_total, 4) for k, v in comp.items()},
    }
    ob_f, ob_a = S(24), S(25)
    r_f, r_a = S(10), S(11)
    out["runs_decomposition"] = {
        "on_base_events_per_player_game_forecast": round(ob_f / n, 5), "actual": round(ob_a / n, 5),
        "runs_per_on_base_forecast": round(r_f / ob_f, 5), "runs_per_on_base_actual": round(r_a / ob_a, 5),
        "log_ratio_total": round(math.log(r_f / r_a), 5),
        "log_ratio_on_base": round(math.log(ob_f / ob_a), 5),
        "log_ratio_scoring_rate": round(math.log((r_f / ob_f) / (r_a / ob_a)), 5),
    }
    # ---- by slot / by home-away ----
    by_slot = {}
    for s in range(1, 10):
        rs = rows_for(lambda r, rec, b, s=s: b["slot"] == s)
        by_slot[s] = {k: {"forecast": round(sum(v[i] for _c, v in rs) / len(rs), 4),
                          "actual": round(sum(v[i + 1] for _c, v in rs) / len(rs), 4)}
                      for k, i in (("PA", 0), ("H", 2), ("RBI", 8), ("R", 10))}
        by_slot[s]["n"] = len(rs)
    out["by_slot"] = by_slot
    for side in ("A", "H"):
        rs = rows_for(lambda r, rec, b, side=side: rec["side"] == side)
        out[f"pa_{'away' if side == 'A' else 'home'}"] = {"forecast": round(sum(v[0] for _c, v in rs) / len(rs), 4),
                                                         "actual": round(sum(v[1] for _c, v in rs) / len(rs), 4), "n": len(rs)}
    # ---- incumbent data-depth diagnostics ----
    arch = [r["seasonPA_archive"] or 0 for r, _rec, _b in inc_rows]
    arch.sort()
    out["incumbent_input_depth"] = {
        "archived_pa_median": arch[len(arch) // 2], "archived_pa_p90": arch[int(len(arch) * 0.9)],
        "share_season_woba_passed": sum(1 for r, _a, _b in inc_rows if r["seasonWobaPassed"]) / len(inc_rows),
        "share_starter_mix_derived": sum(1 for r, _a, _b in inc_rows if r["starterMixDerived"]) / len(inc_rows),
        "share_batter_hand_missing": sum(1 for r, _a, _b in inc_rows if not r["batterHand"]) / len(inc_rows),
        "share_coherent_draws": sum(1 for r, _a, _b in inc_rows if r["coherent"]) / len(inc_rows),
    }
    # ---- settlement actualValue vs box score (observation-source agreement) ----
    sett = settlement_actuals()
    agree = defaultdict(lambda: [0, 0])
    for (gpk, pid, stat), v in sett.items():
        if (gpk, pid) not in actual:
            continue
        _rec, b = actual[(gpk, pid)]
        y = b["y"]
        truth = {"H": y["H"], "TB": y["TB"], "RBI": y["RBI"], "HRR": y["H"] + y["R"] + y["RBI"]}[stat]
        agree[stat][0] += 1
        agree[stat][1] += int(truth == v)
    out["settlement_actual_vs_boxscore"] = {k: {"n": v[0], "agree": v[1]} for k, v in agree.items()}
    with open(a.out, "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(json.dumps({k: out[k] for k in ("n_matched", "bias_full_population", "hits_decomposition",
                                          "rbi_decomposition", "runs_decomposition")}, indent=1))


if __name__ == "__main__":
    main()
