#!/usr/bin/env python3
"""
scripts/research/mlb_pitcher_long_leash.py
==========================================
RESEARCH ONLY. Long-leash bias of the frozen regular-season workload challenger
``mlb_pitcher_workload_joint_v1`` (PR #283), its diagnosis, and the preregistered
evaluation of one correction (docs/research/PITCHER_LONG_LEASH_PREREG.md).

    # 1. quantify + diagnose (frozen arms only; no variant involved)
    python3 scripts/research/mlb_pitcher_long_leash.py diagnose --out data/research/pitcher_long_leash/diagnosis.json
    # 2. development check of candidate corrections: fit 2022-23, validate 2024 (no 2025/2026 data)
    python3 scripts/research/mlb_pitcher_long_leash.py develop --out data/research/pitcher_long_leash/development_2024.json
    # 3. freeze the preregistered variant: fit on 2022-2024 only, from the frozen v1 params
    python3 scripts/research/mlb_pitcher_long_leash.py fit
    # 4. preregistered held-out evaluation (2025 + 2026 regular season), after the prereg commit
    python3 scripts/research/mlb_pitcher_long_leash.py evaluate --out data/research/pitcher_long_leash/holdout_2025_2026.json

The frozen arms (challenger v1, champion, naive baseline) are built exactly as
scripts/research/mlb_pitcher_workload_joint.py `evaluate` builds them (same rows,
same exclusions, same functions), and the frozen challenger's held-out means are
checked against the committed #283 sidecar before anything is scored.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (ROOT, os.path.join(ROOT, "scripts", "research")):
    if p not in sys.path:
        sys.path.insert(0, p)

import mlb_pitcher_workload_joint as W  # noqa: E402
from lib.research import pitcher_workload_joint as J  # noqa: E402
from lib.research import pitcher_workload_joint_leash as L  # noqa: E402

OUT_DIR = os.path.join(ROOT, "data", "research", "pitcher_long_leash")
VARIANT_PARAMS = os.path.join(OUT_DIR, "variant_params.json")
FROZEN_SIDECAR = os.path.join(ROOT, "data", "research", "pitcher_workload_joint", "holdout_2025_2026",
                              "challenger.pure_v0.jsonl.gz")
STATS = W.STATS
SHORT = {"pitcher_outs": "outs", "pitcher_batters_faced": "bf", "pitcher_strikeouts": "k"}
SEED = 20261012
REPS = 1000


# ----------------------------------------------------------------------------- tiers (as-of, frozen features)

def tiers_of(feat, params):
    """Leash tiers from the frozen challenger's own as-of features (no outcome)."""
    wp, wo = feat["w_pitches"], feat["w_outs"]
    out = {}
    out["pitch_leash"] = None if wp is None else ("short_lt80" if wp < 80 else ("long_ge95" if wp >= 95 else "mid_80_95"))
    out["pitch_leash_fine"] = None if wp is None else (
        "lt75" if wp < 75 else "75_85" if wp < 85 else "85_90" if wp < 90 else "90_95" if wp < 95 else "95_100" if wp < 100 else "ge100")
    out["depth"] = None if wo is None else ("lt15_outs" if wo < 15 else ("ge18_outs" if wo >= 18 else "15_18_outs"))
    return out


# ----------------------------------------------------------------------------- bootstrap

def _clusters(rows, f):
    g = defaultdict(lambda: [0.0, 0])
    for r in rows:
        c = g[r["game"]]
        c[0] += f(r)
        c[1] += 1
    return list(g.values())


def boot_mean(rows, f, reps=REPS, seed=SEED):
    """Mean of f over rows with a game-clustered bootstrap 95% CI."""
    cl = _clusters(rows, f)
    n = sum(c[1] for c in cl)
    point = sum(c[0] for c in cl) / n
    rng = random.Random(seed)
    sims = []
    for _ in range(reps):
        s = m = 0.0
        for _k in range(len(cl)):
            c = cl[rng.randrange(len(cl))]
            s += c[0]
            m += c[1]
        sims.append(s / m)
    sims.sort()
    return {"point": round(point, 4), "ci95": [round(sims[int(0.025 * reps)], 4), round(sims[int(0.975 * reps) - 1], 4)],
            "n": n, "games": len(cl)}


# ----------------------------------------------------------------------------- frozen arms on the held-out rows

def build_holdout(data_root, seasons=W.HOLDOUT_SEASONS, variant=None):
    """Rows, per-arm PMFs/means and diagnostics on the #283 scored population (same exclusions)."""
    with open(W.PARAMS_PATH, encoding="utf-8") as fh:
        params = json.load(fh)
    pfull = {**J.DEFAULT_PARAMS, **params}
    sched, games, index = W.sports_dataset(data_root)
    rows = W.target_rows(games, index, set(seasons))
    champ = W.Champion(data_root)
    excl = defaultdict(int)
    out = []
    for r in rows:
        cp = champ.project(r["pid"], r["date"], r["opp"])
        bp = W.baseline_project(index, r["pid"], r["date"], params["baseline"], W.as_of_for(r["kickoff"]))
        if cp is None:
            excl["champion_lt3_prior_starts"] += 1
            continue
        if cp["late_history"]:
            excl["champion_history_has_game_completed_after_as_of"] += 1
            continue
        if bp is None:
            excl["baseline_missing_history_values"] += 1
            continue
        _a, _k, _o, ok = W._times(r["kickoff"], r["observed"])
        if not ok:
            excl["source_observed_after_as_of"] += 1
            continue
        xp = J.project(r["feat"], params)
        arms = {"challenger": {"pitcher_outs": (xp["outs_pmf"], xp["expected_outs"]),
                               "pitcher_batters_faced": (xp["bf_pmf"], xp["expected_batters_faced"]),
                               "pitcher_strikeouts": (xp["k_pmf"], xp["expected_strikeouts"])},
                "champion": {s: cp[s] for s in STATS}, "baseline": {s: bp[s] for s in STATS}}
        if variant is not None:
            vp = L.project(r["feat"], variant)
            arms["variant"] = {"pitcher_outs": (vp["outs_pmf"], vp["expected_outs"]),
                               "pitcher_batters_faced": (vp["bf_pmf"], vp["expected_batters_faced"]),
                               "pitcher_strikeouts": (vp["k_pmf"], vp["expected_strikeouts"])}
        k0, r0 = J.rates(r["feat"], pfull)
        out.append({"key": (r["pk"], r["pid"]), "game": r["pk"], "season": r["season"], "actual": {s: r[SHORT[s]] for s in STATS},
                    "tiers": tiers_of(r["feat"], pfull), "arms": arms,
                    "diag": {"mu": xp["expected_outs_latent"], "k0": k0,
                             "nonout_given_actual_outs": J.nonout_mean(min(J.MAX_OUTS, r["outs"]), r0, pfull),
                             "leash_dev": L.leash_dev(r["feat"], pfull)}})
    return out, dict(excl), len(rows)


def check_frozen_reproduction(rows):
    """Frozen challenger means must equal the committed #283 sidecar (4 dp) on every scored row."""
    want = {}
    with gzip.open(FROZEN_SIDECAR, "rt", encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            want[(d["game_id"], d["player_id"], d["statistic"])] = d["projection"]["mean"]
    bad = n = 0
    for r in rows:
        for s in STATS:
            n += 1
            w = want.get((r["key"][0], r["key"][1], s))
            if w is None or abs(round(r["arms"]["challenger"][s][1], 4) - w) > 1e-9:
                bad += 1
    return {"rows_checked": n, "sidecar_rows": len(want), "mismatches": bad, "ok": bad == 0 and n == len(want)}


# ----------------------------------------------------------------------------- scoring

def scored(rows, stat, arms):
    """W.score_rows per arm on identical keys (one outcome-blind rung: champion/challenger mean prob ~ 0.5)."""
    data = {"_actual": {}}
    for a in arms:
        data[a] = {}
    for r in rows:
        data["_actual"][r["key"]] = r["actual"][stat]
        for a in arms:
            data[a][r["key"]] = r["arms"][a][stat]
    keys = [r["key"] for r in rows]
    return W.score_rows(data, stat, keys)


def bias_table(rows, arms, tier_key):
    """Per tier, stat, arm: bias (mean - actual) with a game-clustered CI; plus challenger-champion delta."""
    out = {}
    groups = defaultdict(list)
    for r in rows:
        t = r["tiers"][tier_key]
        if t is not None:
            groups[t].append(r)
    groups["all"] = list(rows)
    for t, rs in sorted(groups.items()):
        out[t] = {}
        for stat in STATS:
            cell = {}
            for a in arms:
                cell[a] = boot_mean([{"game": r["game"], "v": r["arms"][a][stat][1] - r["actual"][stat]} for r in rs], lambda x: x["v"])
            cell["challenger_minus_champion"] = boot_mean(
                [{"game": r["game"], "v": r["arms"]["challenger"][stat][1] - r["arms"]["champion"][stat][1]} for r in rs], lambda x: x["v"])
            out[t][stat] = cell
    return out


def decomposition(rows, tier_key):
    """Frozen challenger only. BF bias = outs bias + non-out bias. The non-out part given the ACTUAL
    outs (model E[BF - outs | outs = actual] - actual non-outs) isolates the BF | outs stage; K given the
    ACTUAL BF (k0 * BF - K) isolates the K | BF rate; E[outs] - latent mu isolates the kernel mixture."""
    groups = defaultdict(list)
    for r in rows:
        t = r["tiers"][tier_key]
        if t is not None:
            groups[t].append(r)
    out = {}
    for t, rs in sorted(groups.items()):
        ch = lambda r, s: r["arms"]["challenger"][s][1]  # noqa: E731
        o, b, k = "pitcher_outs", "pitcher_batters_faced", "pitcher_strikeouts"
        out[t] = {
            "outs_bias": boot_mean([{"game": r["game"], "v": ch(r, o) - r["actual"][o]} for r in rs], lambda x: x["v"]),
            "latent_mu_minus_actual_outs": boot_mean([{"game": r["game"], "v": r["diag"]["mu"] - r["actual"][o]} for r in rs], lambda x: x["v"]),
            "kernel_mean_minus_latent_mu": boot_mean([{"game": r["game"], "v": ch(r, o) - r["diag"]["mu"]} for r in rs], lambda x: x["v"]),
            "bf_bias": boot_mean([{"game": r["game"], "v": ch(r, b) - r["actual"][b]} for r in rs], lambda x: x["v"]),
            "nonout_bias_given_actual_outs": boot_mean(
                [{"game": r["game"], "v": r["diag"]["nonout_given_actual_outs"] - (r["actual"][b] - r["actual"][o])} for r in rs], lambda x: x["v"]),
            "k_bias": boot_mean([{"game": r["game"], "v": ch(r, k) - r["actual"][k]} for r in rs], lambda x: x["v"]),
            "k_bias_given_actual_bf": boot_mean([{"game": r["game"], "v": r["diag"]["k0"] * r["actual"][b] - r["actual"][k]} for r in rs], lambda x: x["v"]),
            "actual_mean": {"outs": round(sum(r["actual"][o] for r in rs) / len(rs), 3),
                            "bf": round(sum(r["actual"][b] for r in rs) / len(rs), 3),
                            "k": round(sum(r["actual"][k] for r in rs) / len(rs), 3)},
        }
    return out


def nonout_residual_grid(rows):
    """Mean (actual non-outs - frozen curve given actual outs) by actual-outs band x pitch-leash tier."""
    g = defaultdict(list)
    for r in rows:
        t = r["tiers"]["pitch_leash"]
        if t is None:
            continue
        o = r["actual"]["pitcher_outs"]
        band = f"{(min(o, 26) // 3) * 3:02d}-{(min(o, 26) // 3) * 3 + 2:02d}"
        g[(band, t)].append((r["actual"]["pitcher_batters_faced"] - o) - r["diag"]["nonout_given_actual_outs"])
    return {f"{b}|{t}": {"n": len(v), "mean": round(sum(v) / len(v), 3)} for (b, t), v in sorted(g.items())}


# ----------------------------------------------------------------------------- development rows (2022-2024)

def dev_rows(games, index, seasons, min_hist=1):
    tr = []
    for g in games:
        st = g.get("starter")
        if g["season"] not in seasons or not st or not st.get("bf") or st.get("outs") is None:
            continue
        feat, _ = J.features(index, st["pid"], g["team"], g["opp"], g["date"], W.as_of_for(g["start_utc"]))
        if feat["n_hist"] < min_hist:
            continue
        tr.append({"feat": feat, "bf": st["bf"], "outs": st["outs"], "k": st["k"], "pk": str(g["pk"]), "pid": st["pid"]})
    return tr


def dev_diag_rows(rows, params):
    """In-sample diagnostic rows (frozen challenger on its own development seasons, n_hist >= 3)."""
    pfull = {**J.DEFAULT_PARAMS, **params}
    out = []
    for r in rows:
        if r["feat"]["n_hist"] < 3:
            continue
        xp = J.project(r["feat"], params)
        k0, r0 = J.rates(r["feat"], pfull)
        out.append({"key": (r["pk"], r["pid"]), "game": r["pk"],
                    "actual": {"pitcher_outs": r["outs"], "pitcher_batters_faced": r["bf"], "pitcher_strikeouts": r["k"]},
                    "tiers": tiers_of(r["feat"], pfull),
                    "arms": {"challenger": {"pitcher_outs": (None, xp["expected_outs"]),
                                            "pitcher_batters_faced": (None, xp["expected_batters_faced"]),
                                            "pitcher_strikeouts": (None, xp["expected_strikeouts"])}},
                    "diag": {"mu": xp["expected_outs_latent"], "k0": k0,
                             "nonout_given_actual_outs": J.nonout_mean(min(J.MAX_OUTS, r["outs"]), r0, pfull)}})
    return out


# ----------------------------------------------------------------------------- commands

def cmd_diagnose(a):
    with open(W.PARAMS_PATH, encoding="utf-8") as fh:
        params = json.load(fh)
    rows, excl, eligible = build_holdout(a.data_root)
    rep = check_frozen_reproduction(rows)
    if not rep["ok"]:
        raise SystemExit(f"frozen challenger does not reproduce the #283 sidecar: {rep}")
    sched, games, index = W.sports_dataset(a.data_root)
    dev = dev_diag_rows(dev_rows(games, index, set(W.DEV_SEASONS)), params)
    arms = ("challenger", "champion", "baseline")
    out = {"frozen_reproduction": rep, "holdout": {"seasons": list(W.HOLDOUT_SEASONS), "eligible": eligible, "exclusions": excl,
                                                   "scored": len(rows), "games": len({r["game"] for r in rows}),
                                                   "bias": {t: bias_table(rows, arms, t) for t in ("pitch_leash", "pitch_leash_fine", "depth")},
                                                   "decomposition": {t: decomposition(rows, t) for t in ("pitch_leash", "depth")},
                                                   "nonout_residual_by_outs_band": nonout_residual_grid(rows)},
           "development_in_sample": {"seasons": list(W.DEV_SEASONS), "rows": len(dev),
                                     "note": "frozen v1 params were fitted on these seasons: in-sample, used only to show the "
                                             "mechanism replicates where no held-out outcome is involved",
                                     "decomposition": {t: decomposition(dev, t) for t in ("pitch_leash", "depth")},
                                     "nonout_residual_by_outs_band": nonout_residual_grid(dev)},
           "bootstrap": {"reps": REPS, "seed": SEED, "cluster": "gamePk"}}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    print(json.dumps({"reproduction": rep, "scored": len(rows)}))
    return 0


def _metrics(rows_scored):
    return W.summarize(rows_scored)


def cmd_develop(a):
    """Candidate corrections on development data only: frozen-v1 recipe refitted on 2022-23, each
    candidate fitted on 2022-23 on top of it, all scored on the 2024 regular season (n_hist >= 3)."""
    sched, games, index = W.sports_dataset(a.data_root)
    fit_s, val_s = {"2022", "2023"}, {"2024"}
    tr = dev_rows(games, index, fit_s)
    base = J.fit(tr, league=J.league_constants(games, fit_s), kernel_h=W.KERNEL_H)
    cands = {"v1_refit": None,
             "B1_leash_nonout_const": ("leash_gamma", "frozen"),
             "B2_leash_nonout_by_outs": ("leash_gamma_by_outs", "frozen"),
             "A_bin_normalized_kernel": ("frozen", "bin_normalized"),
             "A_B2": ("leash_gamma_by_outs", "bin_normalized")}
    fitted = {k: (base if v is None else L.fit(tr, base, nonout_mode=v[0], kernel_mode=v[1])) for k, v in cands.items()}
    va = W.target_rows(games, index, val_s)
    pfull = {**J.DEFAULT_PARAMS, **base}
    rows = []
    for r in va:
        arms = {}
        for k, p in fitted.items():
            pr = J.project(r["feat"], p) if k == "v1_refit" else L.project(r["feat"], p)
            arms[k] = {"pitcher_outs": (pr["outs_pmf"], pr["expected_outs"]), "pitcher_batters_faced": (pr["bf_pmf"], pr["expected_batters_faced"]),
                       "pitcher_strikeouts": (pr["k_pmf"], pr["expected_strikeouts"])}
        # score_rows needs "champion"/"challenger" keys to pick the rung: use v1_refit for both (outcome-blind)
        arms["champion"] = arms["v1_refit"]
        arms["challenger"] = arms["v1_refit"]
        rows.append({"key": (r["pk"], r["pid"]), "game": r["pk"], "actual": {s: r[SHORT[s]] for s in STATS},
                     "tiers": tiers_of(r["feat"], pfull), "arms": arms})
    out = {"fit_seasons": sorted(fit_s), "validation_seasons": sorted(val_s), "n_validation": len(rows),
           "fitted": {k: {n: p.get(n) for n in ("leash_gamma", "leash_gamma_slope", "k_nonout_prior", "pitcher_alpha_r", "nonout_var_ratio", "opp_alpha_o",
                                                 "k_bf_prior", "opp_alpha_k", "k_resid_slope", "phi_k", "nonout_mode", "kernel_mode")}
                      for k, p in fitted.items()},
           "candidates": {}}
    for k in cands:
        res = {}
        for stat in STATS:
            sc = scored(rows, stat, ("champion", "challenger", k) if k != "v1_refit" else ("champion", "challenger"))
            arm = sc[k] if k in sc else sc["challenger"]
            res[stat] = {"overall": _metrics(arm), "by_pitch_leash": {}}
            for t in ("short_lt80", "mid_80_95", "long_ge95"):
                idx = [i for i, r in enumerate(rows) if r["tiers"]["pitch_leash"] == t]
                res[stat]["by_pitch_leash"][t] = _metrics([arm[i] for i in idx])
            if k != "v1_refit":
                res[stat]["minus_v1_refit"] = W.paired_ci(sc[k], sc["challenger"], reps=REPS, seed=SEED)
        out["candidates"][k] = res
        print(k, json.dumps({s: res[s]["overall"] for s in STATS}))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return 0


def cmd_fit(a):
    """Freeze the preregistered variant: development seasons 2022-2024 only, on top of the frozen v1 params."""
    with open(W.PARAMS_PATH, encoding="utf-8") as fh:
        base = json.load(fh)
    sched, games, index = W.sports_dataset(a.data_root)
    tr = dev_rows(games, index, set(W.DEV_SEASONS))
    p = L.fit(tr, base, nonout_mode=a.nonout_mode, kernel_mode=a.kernel_mode)
    p["variant_fit"]["dev_seasons"] = list(W.DEV_SEASONS)
    p["variant_fit"]["base_params_sha256"] = W.sha256_file(W.PARAMS_PATH)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(p, fh, sort_keys=True, separators=(",", ":"))
    print(json.dumps({k: v for k, v in p.items() if k not in ("outs_table",)}, sort_keys=True))
    return 0


def cmd_evaluate(a):
    with open(a.params, encoding="utf-8") as fh:
        variant = json.load(fh)
    rows, excl, eligible = build_holdout(a.data_root, variant=variant)
    rep = check_frozen_reproduction(rows)
    if not rep["ok"]:
        raise SystemExit(f"frozen challenger does not reproduce the #283 sidecar: {rep}")
    frozen = {"variant": W.frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_workload_joint_leash.py"),
                                        os.path.join(ROOT, "lib", "research", "pitcher_workload_joint.py"),
                                        os.path.join(ROOT, "lib", "research", "pitcher_workload_sources.py"), a.params]),
              "challenger": W.frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_workload_joint.py"),
                                           os.path.join(ROOT, "lib", "research", "pitcher_workload_sources.py"), W.PARAMS_PATH]),
              "champion": W.frozen_hash([os.path.join(ROOT, "lib", "research", "pitcher_prop_projection.py"), W.CHAMPION_PARAMS])}
    arms = ("variant", "challenger", "champion", "baseline")
    out = {"frozen_reproduction": rep, "frozen_hashes": frozen, "seasons": list(W.HOLDOUT_SEASONS), "eligible": eligible,
           "exclusions": excl, "scored": len(rows), "games": len({r["game"] for r in rows}), "stats": {},
           "bootstrap": {"reps": REPS, "seed": SEED, "cluster": "gamePk"}}
    for stat in STATS:
        sc = scored(rows, stat, arms)
        st = {"overall": {k: _metrics(v) for k, v in sc.items()},
              "variant_minus_challenger": W.paired_ci(sc["variant"], sc["challenger"], reps=REPS, seed=SEED),
              "variant_minus_champion": W.paired_ci(sc["variant"], sc["champion"], reps=REPS, seed=SEED),
              "variant_minus_baseline": W.paired_ci(sc["variant"], sc["baseline"], reps=REPS, seed=SEED),
              "by_pitch_leash": {}, "by_depth": {}}
        for tk, dest in (("pitch_leash", "by_pitch_leash"), ("depth", "by_depth")):
            groups = defaultdict(list)
            for i, r in enumerate(rows):
                if r["tiers"][tk] is not None:
                    groups[r["tiers"][tk]].append(i)
            for t, idx in sorted(groups.items()):
                pick = {k: [v[i] for i in idx] for k, v in sc.items()}
                st[dest][t] = {"summary": {k: _metrics(v) for k, v in pick.items()},
                               "variant_minus_challenger": W.paired_ci(pick["variant"], pick["challenger"], reps=REPS, seed=SEED),
                               "variant_minus_champion": W.paired_ci(pick["variant"], pick["champion"], reps=REPS, seed=SEED),
                               "bias": {k: boot_mean([{"game": x["game"], "v": x["err"]} for x in v], lambda z: z["v"]) for k, v in pick.items()}}
        out["stats"][stat] = st
        print(stat, json.dumps(st["overall"]))
    out["decision"] = verdict(out)
    print(json.dumps(out["decision"]))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    return 0


def verdict(out):
    """docs/research/PITCHER_LONG_LEASH_PREREG.md decision rule, applied mechanically."""
    st = out["stats"]
    lo = lambda d: d["ci95"][0]  # noqa: E731
    hi = lambda d: d["ci95"][1]  # noqa: E731
    bf_long = st["pitcher_batters_faced"]["by_pitch_leash"]["long_ge95"]
    checks = {}
    checks["1_primary_long_leash_bf_dmae_ci_below_0"] = hi(bf_long["variant_minus_challenger"]["delta_mae"]) < 0
    checks["2_long_leash_bf_abs_bias_reduced"] = abs(bf_long["bias"]["variant"]["point"]) < abs(bf_long["bias"]["challenger"]["point"])
    checks["3_no_overall_regression_vs_challenger"] = all(
        lo(st[s]["variant_minus_challenger"][m]) <= 0 for s in STATS for m in ("delta_mae", "delta_threshold_brier", "delta_log_loss"))
    checks["4_no_tier_regression_vs_challenger"] = all(
        lo(st[s]["by_pitch_leash"][t]["variant_minus_challenger"]["delta_mae"]) <= 0
        for s in STATS for t in ("short_lt80", "mid_80_95", "long_ge95"))
    vc = {s: st[s]["variant_minus_champion"] for s in STATS}
    checks["5a_outs_or_k_better_than_champion"] = any(
        hi(vc[s][m]) < 0 for s in ("pitcher_outs", "pitcher_strikeouts") for m in ("delta_mae", "delta_threshold_brier"))
    checks["5b_no_overall_regression_vs_champion"] = all(
        lo(vc[s][m]) <= 0 for s in STATS for m in ("delta_mae", "delta_threshold_brier", "delta_log_loss"))
    checks["5c_no_tier_outs_k_regression_vs_champion"] = all(
        lo(st[s]["by_pitch_leash"][t]["variant_minus_champion"]["delta_mae"]) <= 0
        for s in ("pitcher_outs", "pitcher_strikeouts") for t in ("short_lt80", "mid_80_95", "long_ge95")
        if st[s]["by_pitch_leash"][t]["summary"]["variant"]["n"] >= 100)
    checks["6_coverage_in_0.75_0.92"] = all(0.75 <= st[s]["overall"]["variant"]["p10_p90_coverage"] <= 0.92 for s in STATS)
    checks["7_beats_baseline_mae"] = all(st[s]["overall"]["variant"]["mae"] < st[s]["overall"]["baseline"]["mae"] for s in STATS)
    return {"checks": checks, "verdict": "ACCEPTED_RESEARCH_VARIANT" if all(checks.values()) else "REJECTED",
            "status": "research only; frozen challenger and champion unchanged; nothing promoted"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diagnose")
    d.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    d.add_argument("--out", default=os.path.join(OUT_DIR, "diagnosis.json"))
    v = sub.add_parser("develop")
    v.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    v.add_argument("--out", default=os.path.join(OUT_DIR, "development_2024.json"))
    f = sub.add_parser("fit")
    f.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    f.add_argument("--nonout-mode", default="leash_gamma")
    f.add_argument("--kernel-mode", default="bin_normalized")
    f.add_argument("--out", default=VARIANT_PARAMS)
    e = sub.add_parser("evaluate")
    e.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    e.add_argument("--params", default=VARIANT_PARAMS)
    e.add_argument("--out", default=os.path.join(OUT_DIR, "holdout_2025_2026.json"))
    a = ap.parse_args(argv)
    return {"diagnose": cmd_diagnose, "develop": cmd_develop, "fit": cmd_fit, "evaluate": cmd_evaluate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
