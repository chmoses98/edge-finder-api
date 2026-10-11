#!/usr/bin/env python3
"""
scripts/research/mlb_pitcher_postseason_eval.py
===============================================
W2 Phase 3 (RESEARCH ONLY): preregistered SPORTS-ONLY postseason evaluation of the
frozen pitcher workload challenger vs the frozen champion vs the naive baseline,
on the FULL population of 2026 postseason starts (every final game's two actual
starters), with labels from the sports-source postseason box score cache
(data/research_cache/starter_workload/<season>/postseason_boxscores.jsonl.gz).
Preregistration: docs/research/PITCHER_POSTSEASON_PREREG.md.

    python3 scripts/research/mlb_pitcher_postseason_eval.py fit-regime      # 2022-2025 postseasons only
    python3 scripts/research/mlb_pitcher_postseason_eval.py evaluate --outdir OUT

Arms (all frozen; nothing here refits the challenger, the champion or the baseline):
  challenger         mlb_pitcher_workload_joint_v1 exactly as frozen (#283): its loader
                     reads regular-season sports history only.
  challenger_regime  the same model plus ONE preregistered sports-only postseason term:
                     an additive shift `delta` on the latent expected-outs location,
                     fitted on 2022-2025 postseason starts (outs log loss, grid search);
                     BF | outs and K | BF follow through the unchanged joint chain.
  champion           lib/research/pitcher_prop_projection.project + frozen params
                     (postseason=True, i.e. its frozen postseason shift), with its
                     postseason history taken from the SPORTS-SOURCE cache (starts whose
                     gameEnd < as_of) instead of settlement evidence.
  baseline           naive_recent5_v1 (frozen).
  champion_deployed_path  REFERENCE ONLY, not PURE and not a gate comparator: the champion's
                     production postseason path that reads settlement evidence as history.

No market, odds or settlement file is read by any PURE arm. The reference arm and
nothing else reads data/edgelab/settlements.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_RESEARCH_SCRIPTS = os.path.join(ROOT, "scripts", "research")
if _RESEARCH_SCRIPTS not in sys.path:
    sys.path.insert(1, _RESEARCH_SCRIPTS)

import mlb_pitcher_workload_joint as W  # noqa: E402  (frozen #283 harness, reused unchanged)
from lib.research import pitcher_workload_joint as J  # noqa: E402
from lib.research import pitcher_workload_sources as S  # noqa: E402

PS_GLOB = os.path.join("research_cache", "starter_workload", "{season}", "postseason_boxscores.jsonl.gz")
FIT_SEASONS = ("2022", "2023", "2024", "2025")
EVAL_SEASON = "2026"
REGIME_GRID = [round(-6.0 + 0.1 * i, 1) for i in range(81)]  # -6.0 .. +2.0
REGIME_PATH = os.path.join(ROOT, "data", "research", "pitcher_postseason", "regime_params.json")
GATE = os.path.join(ROOT, "tools", "pure_gate", "pure_gate.py")
STATS = W.STATS
ARMS = ("challenger", "challenger_regime", "champion", "baseline")


def _jsonl(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_postseason_starts(data_root, seasons):
    """One row per (postseason game, side) with the actual starter's sports-source line."""
    from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR
    rows = []
    for season in seasons:
        p = os.path.join(data_root, PS_GLOB.format(season=season))
        if not os.path.exists(p):
            continue
        for r in _jsonl(p):
            for side, opp in (("away", "home"), ("home", "away")):
                ps = r.get(f"{side}Pitchers") or []
                if not ps or ps[0].get("orderIndex") != 0:
                    continue
                st = ps[0]
                tid, oid = r[f"{side}Team"]["id"], r[f"{opp}Team"]["id"]
                rows.append({"pk": str(r["gamePk"]), "season": str(r["season"]), "date": r["officialDate"],
                             "kickoff": r["scheduledStart"], "game_end": r["gameEnd"], "game_type": r["gameType"],
                             "series": r.get("seriesDescription"), "pid": str(st["playerId"]),
                             "team": S._TEAM.get(tid), "opp": S._TEAM.get(oid),
                             "team_c": TEAM_ID_TO_ABBR.get(tid), "opp_c": TEAM_ID_TO_ABBR.get(oid),
                             "outs": st.get("outs"), "bf": st.get("battersFaced"), "k": st.get("strikeOuts"),
                             "pitches": st.get("numberOfPitches"), "ip": st.get("inningsPitched"),
                             "source": "statsapi_postseason_boxscore_cache", "fetched_at": r["fetched_at"],
                             "source_url": r["source_url"]})
    rows.sort(key=lambda x: (x["kickoff"], x["pk"], x["pid"]))
    return rows


def project_with_regime(feat, params, delta):
    """The frozen joint projection with the latent expected-outs location shifted by `delta`.
    J.project is called unchanged; only its expected_outs lookup is wrapped for the call, so
    delta == 0 reproduces J.project exactly (asserted in tests)."""
    if not delta:
        return J.project(feat, params)
    orig = J.expected_outs
    J.expected_outs = lambda f, p: max(1.0, min(26.0, orig(f, p) + delta))
    try:
        return J.project(feat, params)
    finally:
        J.expected_outs = orig


def _as_pair(pr):
    return {"pitcher_outs": (pr["outs_pmf"], pr["expected_outs"]),
            "pitcher_batters_faced": (pr["bf_pmf"], pr["expected_batters_faced"]),
            "pitcher_strikeouts": (pr["k_pmf"], pr["expected_strikeouts"])}


def _params(path=W.PARAMS_PATH):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------------- regime fit

def cmd_fit_regime(a):
    params = _params()
    _, games, index = W.sports_dataset(a.data_root)
    rows = []
    for r in load_postseason_starts(a.data_root, FIT_SEASONS):
        if r["outs"] is None or not r["team"]:
            continue
        feat, _obs = J.features(index, r["pid"], r["team"], r["opp"], r["date"], W.as_of_for(r["kickoff"]))
        if feat["n_hist"] < 3:
            continue
        rows.append((feat, r["outs"], r["season"]))
    curve = []
    for d in REGIME_GRID:
        nll = sum(W._nll(project_with_regime(f, params, d)["outs_pmf"], y) for f, y, _s in rows) / len(rows)
        curve.append((d, nll))
    best = min(curve, key=lambda x: (x[1], abs(x[0])))
    base_resid = [y - J.project(f, params)["expected_outs"] for f, y, _s in rows]
    out = {"term": "postseason additive shift on the challenger's latent expected-outs location",
           "fit_seasons": list(FIT_SEASONS), "n_starts": len(rows),
           "n_by_season": {s: sum(1 for *_x, ss in rows if ss == s) for s in FIT_SEASONS},
           "objective": "mean outs log loss", "grid": [REGIME_GRID[0], REGIME_GRID[-1], 0.1],
           "delta": best[0], "objective_at_delta": round(best[1], 5),
           "objective_at_zero": round(dict(curve)[0.0], 5),
           "mean_outs_residual_frozen_challenger": round(sum(base_resid) / len(base_resid), 4),
           "curve": [[d, round(v, 5)] for d, v in curve],
           "source": "data/research_cache/starter_workload/<2022..2025>/postseason_boxscores.jsonl.gz (sports only)"}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: v for k, v in out.items() if k != "curve"}, sort_keys=True))
    return 0


# ----------------------------------------------------------------------------- champion arms

class ChampionSports:
    """Frozen champion (code + params, postseason=True) whose postseason history is the
    sports-source cache: a postseason start enters a projection only if its gameEnd < as_of."""

    def __init__(self, data_root, ps_rows):
        self.base = W.Champion(data_root)
        self.ps = defaultdict(list)
        for r in ps_rows:
            if r["outs"] is not None:
                self.ps[r["pid"]].append({"pid": r["pid"], "date": r["date"], "pk": r["pk"], "outs": r["outs"],
                                          "bf": r["bf"], "k": r["k"], "postseason": True, "game_end": r["game_end"],
                                          "source": "statsapi_postseason_boxscore_cache"})

    def project(self, r, as_of):
        P = self.base.P
        reg = self.base.hist.before(r["pid"], r["date"])
        ps = [s for s in self.ps.get(r["pid"], []) if s["game_end"] < as_of and s["date"] <= r["date"] and s["pk"] != r["pk"]]
        prior = sorted(reg + ps, key=lambda s: (s["date"], str(s.get("pk"))))
        if len(prior) < 3:
            return None
        ok, opa = self.base.hist.opp_rate(r["opp_c"], r["date"]) if r["opp_c"] else (None, 0)
        c = P.project(prior, self.base.params, postseason=True, opp_k_rate=ok, opp_pa=opa)
        late = any(s.get("pk") in self.base.late_pks for s in reg) if self.base.late_pks else False
        return {"pitcher_outs": (c["outs_pmf"], c["expected_outs"]),
                "pitcher_batters_faced": (W.champion_bf_pmf(c, P), c["expected_batters_faced"]),
                "pitcher_strikeouts": (c["k_pmf"], c["expected_strikeouts"]),
                "features": {"history_starts": len(prior), "postseason_history_starts": len(ps),
                             "mu_outs": round(c["mu_outs"], 4), "k_rate": round(c["k_rate"], 5),
                             "opponent_k_factor": round(c["opponent_k_factor"], 5)},
                "late_history": late,
                "observed": max([s["game_end"] for s in ps] + [W._iso(W._parse(s["date"] + "T00:00:00Z") + timedelta(hours=29))
                                                               for s in reg[-1:]]) if (ps or reg) else None}


# ----------------------------------------------------------------------------- evaluate

def _summ_ci(sc, a, b, reps):
    return W.paired_ci(sc[a], sc[b], reps=reps)


def _gate(cmd):
    p = subprocess.run([sys.executable, "-I", GATE] + cmd, capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"returncode": p.returncode, "stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:]}


def cmd_evaluate(a):
    os.makedirs(a.outdir, exist_ok=True)
    params = _params()
    with open(a.regime, encoding="utf-8") as fh:
        regime = json.load(fh)
    delta = regime["delta"]
    _, games, index = W.sports_dataset(a.data_root)
    all_ps = load_postseason_starts(a.data_root, (EVAL_SEASON,))
    champ = ChampionSports(a.data_root, all_ps)
    ref = W.ChampionPostseason(a.data_root) if a.reference_arm else None
    frozen = {"challenger": W.frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_workload_joint.py"),
                                           os.path.join(ROOT, "lib", "research", "pitcher_workload_sources.py"), W.PARAMS_PATH]),
              "champion": W.frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_prop_projection.py"), W.CHAMPION_PARAMS]),
              "baseline": W.frozen_hash([os.path.join(ROOT, "scripts", "research", "mlb_pitcher_workload_joint.py"), W.PARAMS_PATH])}
    frozen["challenger_regime"] = W.frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_workload_joint.py"),
                                                 os.path.join(ROOT, "lib", "research", "pitcher_workload_sources.py"),
                                                 W.PARAMS_PATH, a.regime])
    versions = {"challenger": J.MODEL_VERSION, "challenger_regime": J.MODEL_VERSION + "+postseason_regime_v1",
                "champion": "pitcher_prop_projection@params_asof_2026-10-07+sports_postseason_history",
                "baseline": "naive_recent5_v1"}
    data = {stat: {arm: {} for arm in ARMS + ("_actual",)} for stat in STATS}
    refd = {stat: {} for stat in STATS}
    meta, excl = {}, defaultdict(int)
    files = {arm: open(os.path.join(a.outdir, f"{arm}.pure_v1.jsonl"), "w", encoding="utf-8") for arm in ARMS}
    outcomes = open(os.path.join(a.outdir, "outcomes.jsonl"), "w", encoding="utf-8")
    for r in all_ps:
        if r["outs"] is None or r["bf"] is None or r["k"] is None:
            excl["starter_line_missing_outs_bf_or_k"] += 1
            continue
        if not r["team"]:
            excl["unknown_team_id"] += 1
            continue
        as_of = W.as_of_for(r["kickoff"])
        feat, observed = J.features(index, r["pid"], r["team"], r["opp"], r["date"], as_of)
        if feat["n_hist"] < 3:
            excl["challenger_lt3_prior_starts"] += 1
            continue
        cp = champ.project(r, as_of)
        if cp is None:
            excl["champion_lt3_prior_starts"] += 1
            continue
        if cp["late_history"]:
            excl["champion_history_has_game_completed_after_as_of"] += 1
            continue
        bp = W.baseline_project(index, r["pid"], r["date"], params["baseline"], as_of)
        if bp is None:
            excl["baseline_missing_history_values"] += 1
            continue
        obs = max(x for x in (observed, cp["observed"]) if x)
        as_of_s, kickoff_s, observed_s, ok = W._times(r["kickoff"], obs)
        if not ok:
            excl["source_observed_after_as_of"] += 1
            continue
        xp = W.challenger_project(feat, params)
        xr = _as_pair(project_with_regime(feat, params, delta))
        key = (r["pk"], r["pid"])
        meta[key] = {"game_type": r["game_type"], "series": r["series"]}
        if ref is not None:
            rr = ref.project({"pid": r["pid"], "date": r["date"], "opp": r["opp_c"]})
            for stat in STATS:
                refd[stat][key] = rr[stat] if rr else None
        lin = W.challenger_lineage(feat, observed_s)
        for stat in STATS:
            y = r[W.ACTUAL_KEY[stat]]
            data[stat]["_actual"][key] = y
            outcomes.write(json.dumps({"sport": "MLB", "game_id": r["pk"], "player_id": r["pid"], "statistic": stat,
                                       "actual": y, "played": True, "source": "mlb_statsapi_postseason_boxscore",
                                       "observed_at": r["game_end"]}, sort_keys=True) + "\n")
            for arm, proj, feats in (("challenger", xp, W.challenger_features(feat)),
                                     ("challenger_regime", xr, dict(W.challenger_features(feat), postseason_regime_delta=delta)),
                                     ("champion", cp, cp["features"]),
                                     ("baseline", bp, {"n_last_starts": W.BASELINE_N})):
                pmf, mean = proj[stat]
                data[stat][arm][key] = (pmf, mean)
                po = W.projection_obj(stat, pmf, mean)
                base = W.v0_row(r["pk"], r["pid"], stat, as_of_s, kickoff_s, observed_s, versions[arm], po, feats)
                lineage = lin if arm.startswith("challenger") else [
                    {"name": k, "value": v, "source": "mlb_statsapi_boxscores_and_statcast_pitch_log",
                     "observed_at": observed_s, "class": "sports_only"} for k, v in feats.items()]
                src = [{"source_id": "mlb_statsapi_boxscores_and_statcast_pitch_log", "class": "sports_only",
                        "description": "research_cache regular-season box scores + Statcast lines; postseason box score cache (champion)",
                        "max_observed_at": observed_s}]
                files[arm].write(json.dumps(W.v1_row(base, frozen[arm], lineage, src, po), sort_keys=True) + "\n")
    for f in files.values():
        f.close()
    outcomes.close()
    card = {"prereg": a.prereg, "frozen_hashes": frozen, "versions": versions, "regime": {"delta": delta, "file": os.path.relpath(a.regime, ROOT)},
            "population": "every final 2026 postseason game's two actual starters (sports-source cache), not market-selected",
            "eligible_starts": len(all_ps), "exclusions": dict(excl), "stats": {}}
    for stat in STATS:
        keys = sorted(data[stat]["_actual"])
        sc = W.score_rows(data[stat], stat, keys)
        st = {"n": len(keys), "games": len({k[0] for k in keys}),
              "overall": {arm: W.summarize(v) for arm, v in sc.items()}, "deltas": {}}
        for x, y in (("challenger", "champion"), ("challenger", "baseline"), ("champion", "baseline"),
                     ("challenger_regime", "champion"), ("challenger_regime", "baseline"),
                     ("challenger_regime", "challenger")):
            st["deltas"][f"{x}_minus_{y}"] = _summ_ci(sc, x, y, a.reps)
        groups = defaultdict(list)
        for i, row in enumerate(sc["challenger"]):
            groups["game_type_" + meta[row["key"]]["game_type"]].append(i)
        st["subgroups"] = {}
        for g, idx in sorted(groups.items()):
            pick = {arm: [v[i] for i in idx] for arm, v in sc.items()}
            ng = len({r["game"] for r in pick["challenger"]})
            st["subgroups"][g] = {"n": len(idx), "games": ng, "summary": {arm: W.summarize(v) for arm, v in pick.items()},
                                  "challenger_minus_champion": W.paired_ci(pick["challenger"], pick["champion"], reps=a.reps) if ng >= 3 else None,
                                  "challenger_regime_minus_champion": W.paired_ci(pick["challenger_regime"], pick["champion"], reps=a.reps) if ng >= 3 else None}
        if ref is not None:
            kk = [k for k in keys if refd[stat].get(k)]
            errs = [refd[stat][k][1] - data[stat]["_actual"][k] for k in kk]
            st["reference_champion_deployed_path"] = {
                "note": "NOT PURE (reads settlement evidence as history); reference only, never a gate comparator",
                "n": len(kk), "mae": round(sum(abs(e) for e in errs) / len(errs), 4) if errs else None,
                "bias": round(sum(errs) / len(errs), 4) if errs else None}
        card["stats"][stat] = st
        print(stat, json.dumps(st["overall"]), json.dumps(st["deltas"]["challenger_minus_champion"]))
    card["decision"] = decide(card)
    # shared gate
    gate = {}
    for arm in ARMS:
        gate[f"validate_{arm}"] = _gate(["validate", "--require-v1", "--forecasts", os.path.join(a.outdir, f"{arm}.pure_v1.jsonl")])
    for x, y in (("champion", "challenger"), ("champion", "challenger_regime"), ("baseline", "challenger")):
        gate[f"compare_{y}_vs_{x}"] = _gate(["compare", "--champion", os.path.join(a.outdir, f"{x}.pure_v1.jsonl"),
                                             "--challenger", os.path.join(a.outdir, f"{y}.pure_v1.jsonl"),
                                             "--outcomes", os.path.join(a.outdir, "outcomes.jsonl"), "--bootstrap", str(a.reps)])
    with open(os.path.join(a.outdir, "pure_gate_report.json"), "w", encoding="utf-8") as fh:
        json.dump(gate, fh, indent=1, sort_keys=True)
    with open(os.path.join(a.outdir, "scorecard.json"), "w", encoding="utf-8") as fh:
        json.dump(card, fh, indent=1, sort_keys=True)
    print(json.dumps(card["decision"], indent=1))
    return 0


def _ci_above(d):
    return d["ci95"][0] > 0


def _ci_below(d):
    return d["ci95"][1] < 0


def decide(card, min_starts=40):
    """Preregistered strict rule (docs/research/PITCHER_POSTSEASON_PREREG.md section 5)."""
    out = {}
    for arm in ("challenger", "challenger_regime"):
        why = []
        st = card["stats"]
        n = st["pitcher_outs"]["n"]
        r1 = any(_ci_below(st[s]["deltas"][f"{arm}_minus_champion"][m])
                 for s in ("pitcher_outs", "pitcher_strikeouts") for m in ("delta_mae", "delta_threshold_brier"))
        worse = [f"{s}:{m}:vs_{c}" for s in STATS for c in ("champion", "baseline")
                 for m in ("delta_mae", "delta_threshold_brier", "delta_log_loss")
                 if _ci_above(st[s]["deltas"][f"{arm}_minus_{c}"][m])]
        cov = {s: st[s]["overall"][arm]["p10_p90_coverage"] for s in ("pitcher_outs", "pitcher_strikeouts")}
        r3 = all(0.75 <= v <= 0.92 for v in cov.values())
        r4 = all(st[s]["overall"][arm]["mae"] < st[s]["overall"]["baseline"]["mae"] for s in ("pitcher_outs", "pitcher_strikeouts"))
        if n < min_starts:
            why.append(f"n={n} < {min_starts}")
        if worse:
            why.append("significant worsening: " + ", ".join(worse))
        if not r3:
            why.append(f"coverage outside [0.75,0.92]: {cov}")
        if not r4:
            why.append("does not beat baseline MAE on outs and K")
        if not r1:
            why.append("no outs/K MAE or Brier improvement vs champion with CI below 0")
        if worse or not r3 or not r4:
            verdict = "REJECTED"
        elif n < min_starts or not r1:
            verdict = "INCONCLUSIVE"
        else:
            verdict = "POSTSEASON_RESEARCH_PASS"
        out[arm] = {"verdict": verdict, "postseason_status": "RESEARCH_ONLY_NOT_PROMOTED" if verdict == "POSTSEASON_RESEARCH_PASS" else "BLOCKED",
                    "n_starts": n, "rule1_improvement": r1, "rule2_no_significant_worsening": not worse,
                    "rule3_coverage": r3, "rule4_beats_baseline": r4, "reasons": why}
    return out


def cmd_rerun(a):
    """Deterministic projections (no outcomes) of the PURE arms, or of the settlement-reading
    reference control, on the fixed 2026 postseason population: canonical JSONL for
    `pure_gate.py rerun` (runtime market deletion / randomisation proof)."""
    params = _params()
    with open(a.regime, encoding="utf-8") as fh:
        delta = json.load(fh)["delta"]
    _, games, index = W.sports_dataset(a.data_root)
    pop = load_postseason_starts(a.data_root, (EVAL_SEASON,))
    out = []
    if a.arm == "pure_arms":
        champ = ChampionSports(a.data_root, pop)
        for r in pop:
            if not r["team"]:
                continue
            as_of = W.as_of_for(r["kickoff"])
            feat, _obs = J.features(index, r["pid"], r["team"], r["opp"], r["date"], as_of)
            if feat["n_hist"] < 3:
                continue
            arms = {"challenger": W.challenger_project(feat, params),
                    "challenger_regime": _as_pair(project_with_regime(feat, params, delta)),
                    "baseline": W.baseline_project(index, r["pid"], r["date"], params["baseline"], as_of),
                    "champion": champ.project(r, as_of)}
            for arm, pr in sorted(arms.items()):
                if pr is None:
                    continue
                for stat in STATS:
                    pmf, mean = pr[stat]
                    out.append({"arm": arm, "game_id": r["pk"], "player_id": r["pid"], "statistic": stat,
                                "projection": W.projection_obj(stat, pmf, mean)})
    elif a.arm == "champion_deployed_path":
        ref = W.ChampionPostseason(a.data_root)
        for r in pop:
            pr = ref.project({"pid": r["pid"], "date": r["date"], "opp": r["opp_c"]})
            if pr is None:
                continue
            for stat in STATS:
                pmf, mean = pr[stat]
                out.append({"arm": a.arm, "game_id": r["pk"], "player_id": r["pid"], "statistic": stat,
                            "projection": W.projection_obj(stat, pmf, mean)})
    out.sort(key=lambda x: (x["arm"], x["game_id"], x["player_id"], x["statistic"]))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        for row in out:
            fh.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps({"arm": a.arm, "rows": len(out), "sha256": W.sha256_file(a.out)}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fit-regime")
    f.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    f.add_argument("--out", default=REGIME_PATH)
    e = sub.add_parser("evaluate")
    e.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    e.add_argument("--regime", default=REGIME_PATH)
    e.add_argument("--outdir", required=True)
    e.add_argument("--reps", type=int, default=1000)
    e.add_argument("--prereg", default="")
    e.add_argument("--reference-arm", action="store_true", help="also score the deployed (settlement-reading) path")
    r = sub.add_parser("rerun")
    r.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    r.add_argument("--regime", default=REGIME_PATH)
    r.add_argument("--arm", choices=("pure_arms", "champion_deployed_path"), required=True)
    r.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return {"fit-regime": cmd_fit_regime, "evaluate": cmd_evaluate, "rerun": cmd_rerun}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
