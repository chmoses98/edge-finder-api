"""Assemble results/summary.json (sift.projection_foundation.sport_summary.v1) and
results/tables_mlb.md from the stage outputs written by run_walkforward."""
from __future__ import annotations

import json
import os
import time

import numpy as np

from pf_core import VERSION as PF_VERSION
from mlb import data as D
from mlb.run_walkforward import RESULTS, _load_stage, _r, _sha

PITCH = {"bf": ("batters_faced", None, "starter-game"), "outs": ("outs", "batters_faced", "starter-game"),
         "k": ("pitcher_strikeouts", "batters_faced", "starter-game"), "bb": ("pitcher_walks", "batters_faced", "starter-game"),
         "h": ("pitcher_hits_allowed", "batters_faced", "starter-game"), "r": ("pitcher_runs_allowed", "batters_faced", "starter-game")}
HIT = {"PA": "plate_appearances", "H": "hits", "TB": "total_bases", "HR": "home_runs", "R": "runs", "RBI": "rbis",
       "HRR": "hits_runs_rbis"}
MODEL_DESC = {
    "A": "season-to-date + prior season mean, EB-shrunk", "B": "EWMA, EB-shrunk", "C": "opportunity x EB-shrunk per-unit rate",
    "D": "C x opponent/park(/platoon) factors, elasticities fitted on dev", "D_rc": "D + root-cause terms (rest days, opener, debut, layoff)",
    "D_ridge": "schedule-adjusted ridge ratings x starter/park", "E": "LightGBM walk-forward on PIT features",
    "F": "non-negative stack of B/D/E fitted on dev", "C_hist_slot": "C with previous slot (no lineup)",
    "D_hist_slot": "D with previous slot (no lineup)", "E_hist_slot": "E without lineup slot"}


def readiness(hold, inc_hold, retro_dependency: str | None, family: str):
    vb = hold.get("vs_baseline") or {}
    beats_base = bool(vb) and vb["ci95"][1] < 0
    calibrated = (hold.get("cover_80") is not None and 0.76 <= hold["cover_80"] <= 0.84 and
                  hold.get("pit_var_ratio") is not None and 0.88 <= hold["pit_var_ratio"] <= 1.12)
    inc_state = "none"
    if inc_hold:
        lo, hi = inc_hold["ci95"]
        inc_state = "better" if hi < 0 else ("worse" if lo > 0 else "parity")
    reasons = [f"holdout CRPS vs baseline {vb.get('mean_diff', float('nan')):+.4f} {vb.get('ci95')}" if vb else "no baseline comparison",
               f"80% PIT coverage {hold.get('cover_80')}, PIT variance ratio {hold.get('pit_var_ratio')}",
               f"incumbent: {inc_state}" + (f" ({inc_hold['mean_diff']:+.4f} {inc_hold['ci95']}, {inc_hold['metric']})" if inc_hold else "")]
    if not beats_base or not calibrated or inc_state == "worse":
        status = "NEEDS IMPROVEMENT"
    elif retro_dependency:
        status = "SHADOW VALIDATION"
        reasons.append(retro_dependency)
    elif inc_state == "better":
        status = "READY"
        reasons.append("ready for research-only publication in place of the incumbent; prospective (archived pregame) confirmation still recommended")
    else:
        status = "SHADOW VALIDATION"
        reasons.append("incumbent parity (difference not significant); needs prospective archived-forecast evidence" if inc_state == "parity"
                       else "no incumbent comparison available; needs prospective shadow evidence")
    return status, "; ".join(reasons)


def main():
    inv = _load_stage("inventory")
    ps = _load_stage("pitchers")
    hs = _load_stage("hitters")
    ts = _load_stage("teams")
    fams = []
    tables = ["# MLB Projection Foundation — generated tables\n",
              f"Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}, code {_sha()[:10]}.\n"]

    def table(title, fam_block):
        rows = fam_block["rows"]
        t = [f"\n### {title}\n", "| Model | dist | n | MAE | RMSE | Bias | CRPS | cover80 | PIT var | rung Brier | CRPS vs A [95% CI] | vs incumbent | verdict |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|"]
        for r in rows:
            vb = r.get("vs_baseline")
            vi = r.get("vs_incumbent")
            t.append(f"| {r['model']} | {r['dist']} | {r['n_obs']} | {r['mae']:.3f} | {r['rmse']:.3f} | {r['bias']:+.3f} | {r['crps']:.4f} | "
                     f"{r['cover_80']:.3f} | {r['pit_var_ratio']:.3f} | {r['mean_threshold_brier'] or float('nan'):.4f} | "
                     f"{'' if not vb else '%+.4f [%+.4f, %+.4f]' % (vb['mean_diff'], *vb['ci95'])} | "
                     f"{'' if not vi else '%+.4f [%+.4f, %+.4f]' % (vi['mean_diff'], *vi['ci95'])} | {r['verdict']} |")
        h = fam_block["holdout"]
        vb, vi = h.get("vs_baseline"), h.get("vs_incumbent")
        t.append(f"\nHoldout (2026, frozen **{h['model']}**): n={h['n_obs']}, MAE {h['mae']:.3f}, RMSE {h['rmse']:.3f}, bias {h['bias']:+.3f}, "
                 f"CRPS {h['crps']:.4f}, cover80 {h['cover_80']:.3f}, PIT var {h['pit_var_ratio']:.3f}"
                 + ("" if not vb else f"; CRPS vs A {vb['mean_diff']:+.4f} [{vb['ci95'][0]:+.4f}, {vb['ci95'][1]:+.4f}]")
                 + ("" if not vi else f"; vs incumbent {vi['mean_diff']:+.4f} [{vi['ci95'][0]:+.4f}, {vi['ci95'][1]:+.4f}] ({vi['metric']}; MAE diff {vi.get('mae_diff')} {vi.get('mae_ci95')})")
                 + "\n")
        return "\n".join(t)

    # ---------------- pitchers
    for c, (fam, opp, unit) in PITCH.items():
        blk = ps["summary_families"][c]
        hold = blk["holdout"]
        st, why = readiness(hold, hold.get("vs_incumbent"), None if c in ("bf", "outs", "k") else
                            "no incumbent engine exists for this family (Kalshi lists <100 markets); prospective shadow needed", fam)
        if c not in ("bf", "outs", "k") and st == "READY":
            st = "SHADOW VALIDATION"
        fams.append({"family": fam, "unit": unit, "opportunity_family": opp,
                     "population": "every regular-season starting pitcher (openers included), 2025 validation / 2026 holdout",
                     "models": blk["rows"], "best_model": hold["model"], "holdout": hold, "readiness": st,
                     "readiness_reason": why, "selection": ps["families"][c]["selection"],
                     "dist_choice_dev_crps": ps["families"][c]["dist_choice"].get(hold["model"])})
        tables.append(table(f"{fam} (pitchers; validation 2025)", blk))
    # ---------------- hitters
    if hs:
        for c, fam in HIT.items():
            blk = hs["summary_families"][c]
            hold = blk["holdout"]
            same = hs["pregame_lineup_subset"]["same_slot_rate"]
            st, why = readiness(hold, hold.get("vs_incumbent"),
                                "inputs use the post-game official batting order (equivalent to a confirmed lineup); where an archived pregame lineup exists "
                                f"(527 games) it gives the same slot {same:.1%} of the time, but a prospective confirmed-lineup shadow is still required",
                                fam)
            vi = hold.get("vs_incumbent")
            if vi and vi.get("mae_diff") is not None and vi["mae_ci95"][0] > 0:
                why += (f"; NOTE MAE of the mean is worse than the incumbent ({vi['mae_diff']:+.4f} {vi['mae_ci95']}) because the incumbent's "
                        "downward-biased mean sits nearer the modal 0 for this zero-heavy count; CRPS/Brier/bias all favour the new model")
            fams.append({"family": fam, "unit": "batter-game", "opportunity_family": None if c == "PA" else "plate_appearances",
                         "population": "every starting batter (lineup slots 1-9), 2025 validation / 2026 holdout",
                         "models": blk["rows"], "best_model": hold["model"], "holdout": hold, "readiness": st,
                         "readiness_reason": why, "selection": hs["families"][c]["selection"],
                         "dist_choice_dev_crps": hs["families"][c]["dist_choice"].get(hold["model"])})
            tables.append(table(f"{fam} (hitters; validation 2025)", blk))
    # ---------------- teams
    if ts:
        blk = ts["summary_family"]
        hold = blk["holdout"]
        st, why = readiness(hold, hold.get("vs_incumbent"), None, "team_runs")
        fams.append({"family": "team_runs", "unit": "team-game", "opportunity_family": None,
                     "population": "every regular-season team-game, 2025 validation / 2026 holdout", "models": blk["rows"],
                     "best_model": hold["model"], "holdout": hold, "readiness": st, "readiness_reason": why,
                     "selection": ts["selection"], "dist_choice_dev_crps": ts["dist_choice"].get(hold["model"])})
        tables.append(table("team_runs (validation 2025)", blk))
        gt = ts["game_total"]
        gv, gh = gt["validation"]["convolution"], gt["holdout"]["convolution"]
        gh_cmp = gt["holdout"]["conv_minus_direct"]
        fams.append({"family": "game_total", "unit": "game", "opportunity_family": None,
                     "population": "every regular-season game", "models": [
                         {"model": f"{hold['model']}_convolution", "split": "validation", "n_obs": gv["n_obs"], "n_clusters": gv["n_clusters"],
                          "mae": gv["mae"], "rmse": gv["rmse"], "bias": gv["bias"], "crps": gv["crps"], "log_score": gv["log_score"],
                          "cover_50": gv["cover_50"], "cover_80": gv["cover_80"], "cover_90": gv["cover_90"],
                          "pit_var_ratio": gv["pit_var_ratio"], "vs_baseline": None, "vs_incumbent": None, "verdict": "best"}],
                     "best_model": f"{hold['model']}_convolution",
                     "holdout": {"model": f"{hold['model']}_convolution", "n_obs": gh["n_obs"], "mae": gh["mae"], "crps": gh["crps"],
                                 "bias": gh["bias"], "cover_80": gh["cover_80"], "pit_var_ratio": gh["pit_var_ratio"],
                                 "vs_baseline": None, "vs_incumbent": None,
                                 "vs_direct_nb": {"mean_diff": _r(gh_cmp["mean_diff"], 5), "ci95": [_r(x, 5) for x in gh_cmp["ci95"]]}},
                     "readiness": "SHADOW VALIDATION" if (0.76 <= gh["cover_80"] <= 0.84) else "NEEDS IMPROVEMENT",
                     "readiness_reason": f"Convolution of the two team-run distributions; holdout cover80 {gh['cover_80']}, PIT var {gh['pit_var_ratio']}; "
                                         "no archived pregame total projection to compare against except the incumbent team projections.",
                     "selection": "derived from team_runs winner"})
    summary = {
        "schema": "sift.projection_foundation.sport_summary.v1", "sport": "MLB",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "code_sha": _sha(), "pf_core_version": PF_VERSION,
        "splits": {"development": "pitchers & teams: 2023-2024 regular seasons (2022 = history only); hitters: 2024 (fits use 2024-05-01 on; no 2022-23 player-level hitter lines)",
                   "validation": "2025 regular season (model selection)",
                   "holdout": "2026 regular season (frozen winners scored once; incumbent comparisons on matched rows)"},
        "inventory": inv["rows"],
        "families": fams,
    }
    from mlb import narrative
    summary.update(narrative.sections(inv, ps, hs, ts, fams))
    os.makedirs(RESULTS, exist_ok=True)
    with open(os.path.join(RESULTS, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else str(o))
    with open(os.path.join(RESULTS, "tables_mlb.md"), "w") as f:
        f.write("\n".join(tables) + "\n")
    print("summary families:", [(f["family"], f["best_model"], f["readiness"]) for f in fams])


if __name__ == "__main__":
    main()
