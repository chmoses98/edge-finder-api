#!/usr/bin/env python3
"""
scripts/research/hitter_w2/evaluate.py
======================================
SIFT W2 hitter lane -- RESEARCH ONLY. Scores the preregistered comparison
(docs/research/HITTER_W2_PREREG.md): challenger vs baseline on the full 2026
holdout (H1 primary window, H2 secondary window) and challenger vs incumbent
rerun on matched H2 player-games. Writes the scorecard JSON and the
pure_forecast.v1 sidecars + outcomes.
"""
import argparse
import glob
import gzip
import json
import os
import pickle
import random
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_HERE = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from lib.research import hitter_w2_engine as E  # noqa: E402
import scoring as S  # noqa: E402
import sidecars as SC  # noqa: E402
import run_hitter_w2 as R  # noqa: E402
import decompose_incumbent as D  # noqa: E402

H1 = ("2026-03-01", "2026-08-11")
H2 = ("2026-08-12", "2026-09-30")
PRIMARY_STATS = ("H", "TB", "HR", "RBI", "R")
INCUMBENT_HASH = "git:f7371b2"


def slot_group(s):
    return "1-2" if s <= 2 else ("3-5" if s <= 5 else "6-9")


def confirmed_capture_index():
    """(gamePk, side) with an official lineup in a pregame slate capture (2026)."""
    import incumbent_rerun as IR
    out = set()
    for d in sorted(os.listdir(IR.SLATES)):
        if not d.startswith("2026"):
            continue
        for (gid, side) in IR.pregame_confirmed_sides(d):
            out.add((str(gid), "A" if side == "away" else "H"))
    return out


def load_incumbent(paths):
    inc = {}
    for p in paths:
        with gzip.open(p, "rt") as fh:
            for line in fh:
                r = json.loads(line)
                if r.get("status") != "PROJECTED":
                    continue
                inc[(str(r["gameId"]), str(r["playerId"]))] = r
    return inc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--forecast-cache", required=True)
    ap.add_argument("--incumbent", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sidecar-dir", required=True)
    a = ap.parse_args(argv)
    with gzip.open(a.forecast_cache, "rb") as f:
        fc = pickle.load(f)
    inc = load_incumbent(a.incumbent)
    listed = D.listed_player_games()
    captured = confirmed_capture_index()
    params_hash = fc["meta"]["params_hash"]
    os.makedirs(a.out_dir, exist_ok=True)
    os.makedirs(a.sidecar_dir, exist_ok=True)

    rows_all, rows_inc = [], []
    corr = defaultdict(float)
    coherent = {"player_draw_sets": 0, "incoherent": 0}
    side_files = {k: gzip.open(os.path.join(a.sidecar_dir, f"{k}.pure_v1.jsonl.gz"), "wt")
                  for k in ("challenger", "baseline", "incumbent", "challenger_h2_matched", "outcomes")}
    for pk in sorted(fc["games"]):
        g = fc["games"][pk]
        for side in ("A", "H"):
            rec = g["records"][side]
            res = g["challenger"][side]
            kick = rec["startUTC"]
            fetched = None
            team_y_rbi = sum(b["y"]["RBI"] for b in rec["batters"])
            for i, b in enumerate(rec["batters"]):
                ch = res["players"][i]
                ba = g["baseline"][side][i]
                coherent["player_draw_sets"] += 1
                coherent["incoherent"] += int(not ch["_joint"]["coherent"])
                ikey = (str(pk), str(b["playerId"]))
                ir = inc.get(ikey)
                in_h1 = H1[0] <= rec["date"] <= H1[1]
                in_h2 = H2[0] <= rec["date"] <= H2[1]
                sub = {"window": "H1" if in_h1 else ("H2" if in_h2 else "other"), "slot": slot_group(b["slot"]),
                       "hand": rec["oppStarterHand"] or "?", "home": "home" if rec["home"] else "away",
                       "month": rec["date"][:7], "captured": (str(pk), side) in captured,
                       "listed": ikey in listed}
                as_of = SC.default_as_of(kick)
                lin = SC.lineage(R.challenger_lineage(rec, b, res), as_of)
                for st in E.STATS:
                    y = b["y"][st]
                    rows_all.append({"cluster": pk, "stat": st, "y": y, "sub": sub,
                                     "pmfs": {"challenger": ch[st], "baseline": ba[st]}})
                    side_files["challenger"].write(json.dumps(SC.row(R.MODEL_VERSION, params_hash, pk, b["playerId"],
                                                                     st, ch[st], kick, as_of, lin, "hitter_w2"), sort_keys=True) + "\n")
                    side_files["baseline"].write(json.dumps(SC.row(R.BASELINE_VERSION, params_hash, pk, b["playerId"],
                                                                   st, ba[st], kick, as_of, lin[:3] + lin[6:8], "hitter_w2"),
                                                            sort_keys=True) + "\n")
                    side_files["outcomes"].write(json.dumps(SC.outcome(pk, b["playerId"], st, y, "2026-10-11T02:19:51Z"),
                                                            sort_keys=True) + "\n")
                    if ir is not None and in_h2:
                        ipmf = {int(k): v for k, v in ir[st]["pmf"].items()}
                        rows_inc.append({"cluster": pk, "stat": st, "y": y, "sub": sub,
                                         "pmfs": {"incumbent": ipmf, "challenger": ch[st], "baseline": ba[st]}})
                        ias = ir["asOf"]
                        ilin = SC.lineage([("lineup_slot", ir["slot"]), ("opp_starter_mlbam_id", ir["starterPitcherId"]),
                                           ("archived_pa", ir["seasonPA_archive"]), ("slate_capture", ir["captureFile"])], ias)
                        side_files["incumbent"].write(json.dumps(SC.row("HITTER_ENGINE_INCUMBENT_RERUN", INCUMBENT_HASH, pk,
                                                                        b["playerId"], st, ipmf, kick, ias, ilin, "hitter_w2"),
                                                                 sort_keys=True) + "\n")
                        clin = SC.lineage(R.challenger_lineage(rec, b, res), ias)
                        side_files["challenger_h2_matched"].write(json.dumps(SC.row(R.MODEL_VERSION, params_hash, pk,
                                                                                    b["playerId"], st, ch[st], kick, ias,
                                                                                    clin, "hitter_w2"), sort_keys=True) + "\n")
                # joint-moment checks (model expectation vs empirical) per player
                y = b["y"]
                corr["n_pg"] += 1
                corr["sim_E_H_RBI"] += ch["_joint"]["E_H_RBI"]
                corr["emp_H_RBI"] += y["H"] * y["RBI"]
                corr["sim_E_H_R"] += ch["_joint"]["E_H_R"]
                corr["emp_H_R"] += y["H"] * y["R"]
                corr["sim_E_H"] += E.pmf_mean(ch["H"])
                corr["sim_E_RBI"] += E.pmf_mean(ch["RBI"])
                corr["sim_E_R"] += E.pmf_mean(ch["R"])
                corr["emp_H"] += y["H"]
                corr["emp_RBI"] += y["RBI"]
                corr["emp_R"] += y["R"]
                nxt = rec["batters"][(i + 1) % 9]["y"]
                corr["sim_adj_R_RBI"] += res["E_adj_R_RBI"][i]
                corr["emp_adj_R_RBI"] += y["R"] * nxt["RBI"]
            corr["n_team"] += 1
            corr["sim_team_runs"] += E.pmf_mean(res["team_runs"])
            corr["emp_team_runs"] += rec["y_team_runs"]
            corr["emp_team_rbi"] += team_y_rbi
    for f in side_files.values():
        f.close()

    scored_all = S.score_rows(rows_all)
    scored_inc = S.score_rows(rows_inc)
    card = {"prereg": "docs/research/HITTER_W2_PREREG.md", "params_hash": params_hash,
            "n_sims": fc["meta"]["n_sims"], "excluded_team_games": fc["meta"]["excluded"],
            "coherence": coherent, "joint_moments": {k: (v / corr["n_pg"] if k.startswith(("sim_E", "emp_H", "emp_RBI",
                                                                                          "emp_R", "sim_adj", "emp_adj")) and k != "n_pg"
                                                         else v) for k, v in corr.items()}}
    nt = corr["n_team"]
    for k in ("sim_team_runs", "emp_team_runs", "emp_team_rbi"):
        card["joint_moments"][k] = corr[k] / nt
    jm = card["joint_moments"]
    jm["sim_cov_H_RBI"] = jm["sim_E_H_RBI"] - jm["sim_E_H"] * jm["sim_E_RBI"]
    jm["emp_cov_H_RBI"] = jm["emp_H_RBI"] - jm["emp_H"] * jm["emp_RBI"]
    jm["sim_cov_H_R"] = jm["sim_E_H_R"] - jm["sim_E_H"] * jm["sim_E_R"]
    jm["emp_cov_H_R"] = jm["emp_H_R"] - jm["emp_H"] * jm["emp_R"]
    jm["note"] = ("sim_* are model expectations of the product moments averaged over player-games (they include "
                  "between-player mean variation only through the mean-product term); emp_* are realized products. "
                  "cov = E[XY] - E[X]E[Y] at the population level.")

    def block(scored, models, pairs, sel=lambda s: True):
        out = {}
        for st in E.STATS:
            ss = [s for s in scored if s["stat"] == st and sel(s)]
            if not ss:
                continue
            out[st] = {"summary": S.summarize(ss, models)}
            for a_, b_ in pairs:
                out[st][f"{b_}_minus_{a_}"] = S.paired_bootstrap(ss, a_, b_)
        return out

    card["H1_challenger_vs_baseline"] = block(scored_all, ("challenger", "baseline"), [("baseline", "challenger")],
                                              lambda s: s["sub"]["window"] == "H1")
    card["H2_challenger_vs_baseline"] = block(scored_all, ("challenger", "baseline"), [("baseline", "challenger")],
                                              lambda s: s["sub"]["window"] == "H2")
    card["H2_matched_incumbent"] = block(scored_inc, ("incumbent", "challenger", "baseline"),
                                         [("incumbent", "challenger"), ("incumbent", "baseline")])
    # subgroups (descriptive)
    subs = {}
    for key, vals in (("slot", ("1-2", "3-5", "6-9")), ("hand", ("L", "R")), ("home", ("home", "away")),
                      ("captured", (True, False))):
        for v in vals:
            subs[f"{key}={v}"] = {st: {m: {k: round(x, 5) if isinstance(x, float) else x for k, x in d.items()
                                           if k in ("bias", "mae", "rmse", "crps", "brier", "logloss", "mean_forecast")}
                                       for m, d in blk["summary"].items() if isinstance(d, dict)}
                                  for st, blk in block(scored_all, ("challenger", "baseline"), [],
                                                       lambda s, key=key, v=v: s["sub"][key] == v).items()}
    for key, vals in (("listed", (True, False)), ("slot", ("1-2", "3-5", "6-9"))):
        for v in vals:
            subs[f"H2_incumbent_matched:{key}={v}"] = {
                st: {m: {k: round(x, 5) for k, x in d.items() if k in ("bias", "crps", "brier", "mae")}
                     for m, d in blk["summary"].items() if isinstance(d, dict)}
                for st, blk in block(scored_inc, ("incumbent", "challenger", "baseline"), [],
                                     lambda s, key=key, v=v: s["sub"][key] == v).items()}
    months = sorted({s["sub"]["month"] for s in scored_all})
    for mo in months:
        subs[f"month={mo}"] = {st: {m: {k: round(d[k], 5) for k in ("bias", "crps")}
                                    for m, d in blk["summary"].items() if isinstance(d, dict)}
                               for st, blk in block(scored_all, ("challenger", "baseline"), [],
                                                    lambda s, mo=mo: s["sub"]["month"] == mo).items()}
    card["subgroups"] = subs
    with open(os.path.join(a.out_dir, "scorecard.json"), "w") as f:
        json.dump(card, f, indent=1, sort_keys=True, default=str)
    print(json.dumps({k: card[k] for k in ("coherence",)}, indent=1))


if __name__ == "__main__":
    main()
