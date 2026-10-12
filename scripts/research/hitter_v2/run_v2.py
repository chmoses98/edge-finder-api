#!/usr/bin/env python3
"""
scripts/research/hitter_v2/run_v2.py
====================================
SIFT MLB hitter V2 lane -- RESEARCH ONLY. Driver:

  dev       DESIGN loop on training data only: fit on 2024 + 2025-03..06, forecast 2025-07..09 for the
            W2 structure (refit identically), the V2 ablation chain and the baseline; writes
            data/research/hitter_v2/dev_2025/*.json (PA diagnostics + scores).
  fit       final fit of MLB_HITTER_V2_PURE on 2024-2025 only -> data/research/hitter_v2/params_v2.json
  forecast  2026 holdout forecasts (populations PIT and OFF_H1, see the prereg) for the V2 challenger,
            the FROZEN W2 challenger (data/research/hitter_w2/params_v1.json, its own code) and the
            baseline, on identical point-in-time records -> gzip pickle cache.

Sports inputs only. `--market-file` exists only for the runtime non-leakage proof; the PURE path never
opens it; `--control-read-market` is the deliberately market-reading NEGATIVE CONTROL.
"""
import argparse
import gzip
import hashlib
import json
import math
import os
import pickle
import sys
import time
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_HERE = os.path.dirname(os.path.abspath(__file__))
_W2 = os.path.join(_ROOT, "scripts", "research", "hitter_w2")
for _p in (_HERE, _W2):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.research import hitter_w2_engine as W2  # noqa: E402
from lib.research import hitter_v2_engine as V  # noqa: E402
import model as M2  # noqa: E402
import run_hitter_w2 as R2  # noqa: E402
import data_v2 as D  # noqa: E402
import model_v2 as MV  # noqa: E402
import scoring_v2 as S  # noqa: E402

OUT_DIR = os.path.join(_ROOT, "data", "research", "hitter_v2")
PARAMS_V2 = os.path.join(OUT_DIR, "params_v2.json")
MODEL_VERSION = "MLB_HITTER_V2_PURE"
W2_VERSION = "MLB_HITTER_W2_PURE_V1"
BASELINE_VERSION = "MLB_HITTER_W2_BASELINE_V1+BB_K_HRR"
N_SIMS = 500
SEED = 20261012
DEV_SPLIT = "2025-07-01"


def _filter_pas(pas, sd, before=None):
    if before is None:
        return pas
    g = sd["games"]
    return [p for p in pas if g[p["gamePk"]]["officialDate"] < before]


def _filter_ar(ar, sd, before=None):
    if before is None:
        return ar
    g = sd["games"]
    return [r for r in ar if g[r[0]]["officialDate"] < before]


def fit_all(sd_prev, ann_prev, sd_fit, ann_fit, fit_before=None, log=print):
    """Fits the W2 structure (with W2's own routines) and the V2 additions on: previous season `sd_prev`
    (all) + fitting season `sd_fit` (dates < fit_before, or all). Returns (P_w2, P_v2)."""
    pa_p, ro_p, ar_p = ann_prev
    pa_f_all, ro_f, ar_f_all = ann_fit
    pa_f = _filter_pas(pa_f_all, sd_fit, fit_before)
    ar_f = _filter_ar(ar_f_all, sd_fit, fit_before)
    if fit_before is not None:
        # runner-out counts for the truncated fitting season are recomputed from the truncated feed
        g = sd_fit["games"]
        sd_trunc = dict(sd_fit, pa=[r for r in sd_fit["pa"] if g[int(r["gamePk"])]["officialDate"] < fit_before])
        ro_f = D.annotate(sd_trunc)[1]
    bh, ph = D.hand_maps([sd_prev, sd_fit])
    prev_tot = D.season_totals_v2(sd_prev, pa_p)
    prev_w2 = R2.F.season_totals(sd_prev, pa_p)
    rec_fit_w2 = R2.F.build_team_games(sd_fit, pa_f_all, prev_w2)
    rec_fit_w2 = [r for r in rec_fit_w2 if r.get("excluded") or fit_before is None or r["date"] < fit_before]
    league_prev = prev_w2["league"]
    t0 = time.time()
    bp = M2.fit_batter_shrinkage(rec_fit_w2, league_prev)
    pp = M2.fit_pitcher_shrinkage(rec_fit_w2, league_prev, bp, "sp_cur", "sp_prev", "y_cls_sp", M2.GRID_W_PIT, M2.GRID_M_PIT)
    pen = M2.fit_pitcher_shrinkage(rec_fit_w2, league_prev, bp, "pen_cur", "pen_prev", "y_cls_pen", M2.GRID_W_PEN, M2.GRID_M_PEN)
    table, tinfo = W2.fit_transitions(pa_p + pa_f)
    pa_f_sd = dict(sd_fit)
    Pw = {
        "model_version": W2_VERSION, "batter": bp, "pitcher": {"w": pp["w"], "m": pp["m"]},
        "bullpen": {"w": pen["w"], "m": pen["m"]},
        "platoon": M2.platoon_factors(pa_p + pa_f), "park": M2.park_factors([sd_prev, pa_f_sd], [pa_p, pa_f]),
        "survival": M2.fit_survival([sd_prev, sd_fit], [pa_p, pa_f]),
        "sub_rates": M2.sub_rates([sd_prev, sd_fit], [pa_p, pa_f]),
        "bf_residuals": M2.bf_residuals(rec_fit_w2, M2.league_bf(sd_prev)),
        "baseline": M2.baseline_tables(rec_fit_w2),
    }
    log(f"W2-structure shrinkage: batter {bp} pitcher {pp} bullpen {pen} ({time.time() - t0:.0f}s)")
    # ---- V2 additions ----
    Pv = dict(Pw)
    Pv["model_version"] = MODEL_VERSION
    Pv["T"] = MV.league_tables([pa_p, pa_f])
    Pv["runner_out_q"] = MV.runner_out_q([ro_p, ro_f])
    # substitution hazard: (1) cell model; (2) per-batter propensity (a, w) by fitting-season likelihood;
    # (3) one back-fitting step: refit the cell model with the propensity offsets held fixed, so the
    # offsets do not shift the mean hazard; (4) propensity series recomputed under the refit model.
    h0 = MV.fit_hazard([ar_p, ar_f])
    ser_prev0 = MV.propensity_series(sd_prev, ar_p, h0["beta"])
    ser_fit0 = MV.propensity_series(sd_fit, ar_f, h0["beta"])
    Pv["sub_prop"] = MV.fit_sub_propensity(ar_f, sd_fit, ser_fit0, MV.season_propensity_totals(ser_prev0), h0["beta"])
    a_s, w_s = Pv["sub_prop"]["a"], Pv["sub_prop"]["w"]
    offs = [MV.row_offsets(ar_p, ser_prev0, {}, a_s, w_s),
            MV.row_offsets(ar_f, ser_fit0, MV.season_propensity_totals(ser_prev0), a_s, w_s)]
    Pv["hazard"] = MV.fit_hazard([ar_p, ar_f], offs)
    Pv["hazard"]["beta_no_offset"] = h0["beta"]
    log(f"hazard rows {Pv['hazard']['n_rows']} events {Pv['hazard']['n_events']}; sub propensity {Pv['sub_prop']}")
    rec_fit_v2 = D.build_records(sd_fit, pa_f_all, prev_tot, D.official_provider(sd_fit), bh, ph,
                                 game_filter=(lambda pk: sd_fit["games"][pk]["officialDate"] < fit_before)
                                 if fit_before else None)
    Pv_fit = dict(Pv, league=league_prev)
    t0 = time.time()
    cfg, trace = MV.fit_rate_config(rec_fit_v2, Pv_fit, log=log)
    log(f"rate config {cfg} ({time.time() - t0:.0f}s)")
    Pv["cfg"] = cfg
    Pv["cfg_trace"] = trace
    Pv["perpa_logloss_fit"] = {
        "w2_form": MV.perpa_loglik(rec_fit_v2, Pv_fit, {"k_env": MV.INF, "park": True})[0],
        "v2": MV.perpa_loglik(rec_fit_v2, Pv_fit, cfg)[0]}
    # ablation chain (each step adds one preregistered fix group; kappa refit per step on the fitting season)
    sim = {"sub_propensity": True, "runner_outs": True, "park": True}
    c_pa = dict(sim, k_env=MV.INF)
    c_cal = dict(c_pa, k_env=cfg["k_env"], home_factor=True)
    c_cal["kappa_sp"], c_cal["kappa_pen"] = MV.fit_kappa(rec_fit_v2, Pv_fit, c_cal)
    c_plat = dict(c_cal, platoon_v2=True, m_bsplit=cfg["m_bsplit"], m_psplit=cfg["m_psplit"])
    c_plat["kappa_sp"], c_plat["kappa_pen"] = MV.fit_kappa(rec_fit_v2, Pv_fit, c_plat)
    c_full = dict(cfg, **sim)
    Pv["cfg"] = c_full
    Pv["chain"] = {"v2_1_pa": c_pa, "v2_2_pa_cal": c_cal, "v2_3_pa_cal_platoon": c_plat, "v2_full": c_full}
    Pv["perpa_logloss_fit"].update({k: MV.perpa_loglik(rec_fit_v2, Pv_fit, c)[0] for k, c in Pv["chain"].items()})
    log(f"per-PA log-loss on the fitting season: {Pv['perpa_logloss_fit']}")
    return Pw, Pv, table, tinfo


# ---------------------------------------------------------------------------------------------
# Forecast helpers
# ---------------------------------------------------------------------------------------------
def pmf_from_values(vals):
    return W2.pmf_from_values(vals)


def w2_game(ra, rh, Pw, table, n_sims, seed):
    """Frozen W2 challenger (its own team_spec + simulator), plus HRR from the same draws."""
    spec_a, _ = M2.team_spec(ra, Pw)
    spec_h, _ = M2.team_spec(rh, Pw)
    res = W2.run_game_sims(spec_a, spec_h, table, n_sims, seed)
    out = {}
    for side in ("A", "H"):
        players = []
        for s in range(9):
            vals = res[side]["lines"][s]
            d = {k: pmf_from_values([v[j] for v in vals]) for j, k in enumerate(W2.STATS)}
            d["HRR"] = pmf_from_values([v[1] + v[5] + v[4] for v in vals])
            players.append(d)
        out[side] = {"players": players, "team_runs": pmf_from_values(res[side]["runs"])}
    return out


def v2_game(ra, rh, Pv, table, n_sims, seed, prop_lookup, cfg=None):
    cfg = cfg or Pv["cfg"]
    spec_a, bf_a = MV.team_spec_v2(ra, Pv, cfg, prop_lookup)
    spec_h, bf_h = MV.team_spec_v2(rh, Pv, cfg, prop_lookup)
    res = V.run_game_sims(spec_a, spec_h, table, n_sims, seed)
    out = {}
    for side, bf in (("A", bf_a), ("H", bf_h)):
        players = []
        for s in range(9):
            lines = res[side]["lines"][s]
            vals = V.stat_values(lines)
            d = {k: pmf_from_values(v) for k, v in vals.items()}
            n = len(lines)
            d["_joint"] = {"coherent": all(v[2] >= v[1] >= v[3] for v in lines),
                           "E_PA_vs_sp": sum(v[8] for v in lines) / n, "P_replaced": sum(v[9] for v in lines) / n,
                           "E_H_RBI": sum(v[1] * v[4] for v in lines) / n, "E_H_R": sum(v[1] * v[5] for v in lines) / n}
            players.append(d)
        runs = res[side]["runs"]
        adj = []
        for s in range(9):
            t = (s + 1) % 9
            adj.append(sum(res[side]["lines"][s][i][5] * res[side]["lines"][t][i][4] for i in range(len(runs))) / len(runs))
        out[side] = {"players": players, "team_runs": pmf_from_values(runs), "exp_sp_bf": bf,
                     "E_adj_R_RBI": adj,
                     "identity_ok": all(sum(res[side]["lines"][s][i][5] for s in range(9)) + res[side]["sub_runs"][i]
                                        == runs[i] for i in range(len(runs)))}
    return out


def baseline_player(b, home, Pw):
    """W2 baseline (frozen code) + BB, K (binomial given PA with season-to-date rates shrunk with 50 PA
    toward the previous-season league rates) + HRR (Poisson with mean PA x (H+R+RBI) per-PA rate)."""
    d = M2.baseline_player(b, home, Pw)
    T = Pw["baseline"]
    lr = T["rates"]
    cur = b["cur"]
    ncur = sum(cur) if cur else 0
    rate = {c: ((cur[W2.CI[c]] if cur else 0) + M2.BASELINE_M * lr[c]) / (ncur + M2.BASELINE_M) for c in ("BB", "K")}
    box = b["box_cur"]
    n = box[0] if box else 0
    hrr_lr = lr["H"] + lr["R"] + lr["RBI"]
    hrr = ((box[1] + box[5] + box[4] if box else 0) + M2.BASELINE_M * hrr_lr) / (n + M2.BASELINE_M)
    pa_pmf = d["PA"]
    d["BB"] = W2.mixture_over_pa(pa_pmf, lambda k: W2.binomial_pmf(k, rate["BB"]))
    d["K"] = W2.mixture_over_pa(pa_pmf, lambda k: W2.binomial_pmf(k, rate["K"]))
    d["HRR"] = W2.mixture_over_pa(pa_pmf, lambda k: W2.poisson_pmf(k * hrr, kmax=20))
    return d


def make_prop_lookup(sd_cur, ar_cur, prev_series, beta):
    ser = MV.propensity_series(sd_cur, ar_cur, beta)
    prev_tot = MV.season_propensity_totals(prev_series)

    def look(pid, date):
        oc, ec = MV.propensity_asof(ser, pid, date)
        op, ep = prev_tot.get(pid, (0.0, 0.0))
        return oc, ec, op, ep
    return look


def by_game(records):
    g = defaultdict(dict)
    for r in records:
        if r.get("excluded"):
            continue
        g[r["gamePk"]][r["side"]] = r
    return {pk: v for pk, v in g.items() if "A" in v and "H" in v}


# ---------------------------------------------------------------------------------------------
# DEV (training data only)
# ---------------------------------------------------------------------------------------------
def stage_dev(feed_root, out_dir, n_sims=300):
    os.makedirs(out_dir, exist_ok=True)
    sd24 = W2.load_season(2024, feed_root)
    sd25 = W2.load_season(2025, feed_root)
    ann24, ann25 = D.annotate(sd24), D.annotate(sd25)
    Pw, Pv, table, tinfo = fit_all(sd24, ann24, sd25, ann25, fit_before=DEV_SPLIT)
    prev_tot = D.season_totals_v2(sd24, ann24[0])
    bh, ph = D.hand_maps([sd24, sd25])
    rec = D.build_records(sd25, ann25[0], prev_tot, D.official_provider(sd25), bh, ph,
                          game_filter=lambda pk: sd25["games"][pk]["officialDate"] >= DEV_SPLIT)
    league25_prev = R2.F.season_totals(sd24, ann24[0])["league"]
    Pw_h = dict(Pw, league=league25_prev, league_bf=M2.league_bf(sd24))
    Pv_h = dict(Pv, league=league25_prev, league_bf=M2.league_bf(sd24))
    beta = Pv["hazard"]["beta_no_offset"]  # propensity E uses the model the offsets were tuned on
    look = make_prop_lookup(sd25, ann25[2], MV.propensity_series(sd24, ann24[2], beta), beta)
    cfgs = Pv["chain"]
    games = by_game(rec)
    rows = []
    t0 = time.time()
    for n, pk in enumerate(sorted(games)):
        g = games[pk]
        seed = SEED + pk % 100000
        fw = w2_game(g["A"], g["H"], Pw_h, table, n_sims, seed)
        fv = {name: v2_game(g["A"], g["H"], Pv_h, table, n_sims, seed, look, cfg) for name, cfg in cfgs.items()}
        for side in ("A", "H"):
            r = g[side]
            for i, b in enumerate(r["batters"]):
                if not b["started"] or b["y"] is None:
                    continue
                base = baseline_player(b, r["home"], Pw_h)
                for st in V.STATS:
                    pm = {"w2": fw[side]["players"][i].get(st), "baseline": base[st]}
                    for name in cfgs:
                        pm[name] = fv[name][side]["players"][i][st]
                    pm = {k: v for k, v in pm.items() if v is not None}
                    rows.append({"cluster": pk, "stat": st, "y": b["y"][st], "pmfs": pm,
                                 "sub": {"slot": b["slot"], "home": r["home"]}})
        if n % 200 == 0:
            print(f"dev {n}/{len(games)} ({time.time() - t0:.0f}s)", flush=True)
    scored = S.score_rows(rows)
    arms = ["w2", "baseline"] + list(cfgs)
    report = {"dev_split": DEV_SPLIT, "n_sims": n_sims, "fit": {"cfg": Pv["cfg"], "sub_prop": Pv["sub_prop"],
              "hazard_rows": Pv["hazard"]["n_rows"], "runner_out_q": Pv["runner_out_q"],
              "perpa_logloss_fit": Pv["perpa_logloss_fit"], "cfg_trace": Pv["cfg_trace"]},
              "configs": {k: {kk: vv for kk, vv in v.items()} for k, v in cfgs.items()}, "stats": {}, "pa_by_slot": {}}
    for st in V.STATS:
        ss = [s for s in scored if s["stat"] == st]
        avail = [a for a in arms if all(a in s["m"] for s in ss)]
        summ = S.summarize(ss, avail)
        report["stats"][st] = {a: {k: summ[a][k] for k in ("bias", "crps", "abs_med", "abs", "rmse", "pit_in80",
                                                             "max_rung_ece", "max_rung_abs_citl")}
                               for a in avail}
        report["stats"][st]["n"] = summ["n"]
        report["stats"][st]["mean_actual"] = summ["mean_actual"]
        if "v2_full" in avail and "w2" in avail:
            report["stats"][st]["v2_full_minus_w2"] = S.paired_bootstrap(ss, "w2", "v2_full", reps=300)
    ss = [s for s in scored if s["stat"] == "PA"]
    for slot in range(1, 10):
        for home in (False, True):
            sub = [s for s in ss if s["sub"]["slot"] == slot and s["sub"]["home"] == home]
            key = f"{slot}{'H' if home else 'A'}"
            report["pa_by_slot"][key] = {"n": len(sub), "actual": sum(s["y"] for s in sub) / len(sub),
                                         **{a: sum(s["m"][a]["mean"] for s in sub) / len(sub) for a in arms}}
    with open(os.path.join(out_dir, "dev_report.json"), "w") as f:
        json.dump(report, f, indent=1, sort_keys=True, default=str)
    for st in V.STATS:
        line = report["stats"][st]
        print(st, {a: (round(line[a]["bias"], 4), round(line[a]["crps"], 4)) for a in arms if a in line})
    return report


# ---------------------------------------------------------------------------------------------
# FINAL fit / forecast
# ---------------------------------------------------------------------------------------------
def _jsonable_params(Pv, table, tinfo):
    out = {k: v for k, v in Pv.items() if k not in ("league", "league_bf")}
    out["transition_info"] = tinfo
    out["transitions"] = {f"{k[0]}|{k[1]}|{k[2]}": [v[0], v[1]] for k, v in sorted(table.items())}
    out["cfg"] = {k: (None if isinstance(v, float) and math.isinf(v) else v) for k, v in Pv["cfg"].items()}
    out["cfg_inf_keys"] = sorted(k for k, v in Pv["cfg"].items() if isinstance(v, float) and math.isinf(v))
    out["sub_prop"] = {k: (None if isinstance(v, float) and math.isinf(v) else v) for k, v in Pv["sub_prop"].items()}
    out["sub_prop_inf_keys"] = sorted(k for k, v in Pv["sub_prop"].items() if isinstance(v, float) and math.isinf(v))
    return out


def stage_fit(feed_root, out_path=PARAMS_V2):
    sd24 = W2.load_season(2024, feed_root)
    sd25 = W2.load_season(2025, feed_root)
    ann24, ann25 = D.annotate(sd24), D.annotate(sd25)
    Pw, Pv, table, tinfo = fit_all(sd24, ann24, sd25, ann25, fit_before=None)
    # The W2 structure refit on the same data must reproduce the frozen W2 params (same routines)
    frozen, _ = R2.load_params()
    for k in ("batter", "pitcher", "bullpen"):
        assert Pw[k] == frozen[k], (k, Pw[k], frozen[k])
    out = _jsonable_params(Pv, table, tinfo)
    out["fit_seasons"] = {"previous": 2024, "fitting": 2025}
    out["league_bf_for_holdout"] = M2.league_bf(sd25)
    with open(out_path, "w") as f:
        json.dump(out, f, sort_keys=True, separators=(",", ":"), default=float)
    print("wrote", out_path, params_hash(out_path))


def params_hash(path=PARAMS_V2):
    with open(path, "rb") as f:
        return "sha256:" + hashlib.sha256(f.read()).hexdigest()


def load_params_v2(path=PARAMS_V2):
    with open(path) as f:
        P = json.load(f)
    for k in P.pop("cfg_inf_keys"):
        P["cfg"][k] = MV.INF
    for k in P.pop("sub_prop_inf_keys"):
        P["sub_prop"][k] = MV.INF
    P["cfg"]["pen_hl"] = P["cfg"]["pen_hl"] if P["cfg"]["pen_hl"] == MV.INF else int(P["cfg"]["pen_hl"])
    P["baseline"]["pa_pmf"] = {key: {int(k): v for k, v in d.items()} for key, d in P["baseline"]["pa_pmf"].items()}
    table = {}
    for k, v in P["transitions"].items():
        cls, outs, bases = k.split("|")
        table[(cls, int(outs), int(bases))] = (v[0], [tuple(o) for o in v[1]])
    return P, table


def holdout_records(feed_root, population):
    """population: 'PIT' (pregame captured lineups + capture probables, 2026-08..09) or
    'OFF_H1' (official lineup + feed probable, 2026-03-25..08-11)."""
    sd25 = W2.load_season(2025, feed_root)
    sd26 = W2.load_season(2026, feed_root)
    ann25, ann26 = D.annotate(sd25), D.annotate(sd26)
    prev = D.season_totals_v2(sd25, ann25[0])
    bh, ph = D.hand_maps([W2.load_season(2024, feed_root), sd25, sd26])
    if population == "PIT":
        prov = D.pregame_provider(sd26)
        filt = None
    else:
        prov = D.official_provider(sd26, use_probable=True)
        filt = lambda pk: sd26["games"][pk]["officialDate"] <= "2026-08-11"  # noqa: E731
    rec = D.build_records(sd26, ann26[0], prev, prov, bh, ph, game_filter=filt)
    return sd25, ann25, sd26, ann26, rec


def stage_forecast(feed_root, population, out_cache, n_sims=N_SIMS, seed=SEED, start=None, end=None,
                   market_file=None, control_read_market=False, arms=("v2", "w2", "baseline")):
    Pv, table_v = load_params_v2()
    Pw, table_w = R2.load_params()
    sd25, ann25, sd26, ann26, rec = holdout_records(feed_root, population)
    league_prev = R2.F.season_totals(sd25, ann25[0])["league"]
    Pw_h = dict(Pw, league=league_prev, league_bf=Pw["league_bf_for_holdout"])
    Pv_h = dict(Pv, league=league_prev, league_bf=Pv["league_bf_for_holdout"])
    if control_read_market:
        with open(market_file) as f:
            market = json.load(f)
    else:
        market = None
    beta = Pv["hazard"]["beta_no_offset"]  # propensity E uses the model the offsets were tuned on
    look = make_prop_lookup(sd26, ann26[2], MV.propensity_series(sd25, ann25[2], beta), beta)
    games = by_game(rec)
    out = {"meta": {"population": population, "params_v2_hash": params_hash(), "params_w2_hash": R2.params_hash(),
                    "n_sims": n_sims, "seed": seed,
                    "excluded": [(r["gamePk"], r["side"], r["excluded"]) for r in rec if r.get("excluded")]},
           "games": {}}
    t0 = time.time()
    for n, pk in enumerate(sorted(games)):
        g = games[pk]
        d = g["A"]["date"]
        if (start and d < start) or (end and d > end):
            continue
        s = seed + pk % 100000
        cfg = Pv_h["cfg"]
        if market is not None:
            cfg = _control_cfg(cfg, market.get(str(pk)))
        entry = {"records": g}
        if "v2" in arms:
            entry["v2"] = v2_game(g["A"], g["H"], Pv_h, table_v, n_sims, s, look, cfg)
        if "w2" in arms:
            entry["w2"] = w2_game(g["A"], g["H"], Pw_h, table_w, n_sims, s)
        if "baseline" in arms:
            entry["baseline"] = {sd: [baseline_player(b, g[sd]["home"], Pw_h) for b in g[sd]["batters"]]
                                 for sd in ("A", "H")}
        out["games"][pk] = entry
        if n % 200 == 0:
            print(f"{population} {n}/{len(games)} games ({time.time() - t0:.0f}s)", flush=True)
    if out_cache.endswith(".jsonl"):
        write_v2_jsonl(out, out_cache)
    else:
        with gzip.open(out_cache, "wb") as f:
            pickle.dump(out, f, protocol=4)
    return out


def _control_cfg(cfg, total):
    """NEGATIVE CONTROL ONLY: scales the non-out vs-bullpen calibration factors by a market total."""
    if not total:
        return cfg
    c = dict(cfg)
    scale = max(0.5, min(1.5, float(total) / 9.0))
    c["kappa_pen"] = [k * (scale if W2.CATS[i] not in ("K", "OUT") else 1.0) for i, k in enumerate(cfg["kappa_pen"])]
    return c


def write_v2_jsonl(out, path):
    """Deterministic per-player-stat summary rows of the V2 forecasts (non-leakage rerun output)."""
    with open(path, "w") as f:
        for pk in sorted(out["games"]):
            g = out["games"][pk]
            for side in ("A", "H"):
                for i, b in enumerate(g["records"][side]["batters"]):
                    for st in V.STATS:
                        pmf = g["v2"][side]["players"][i][st]
                        f.write(json.dumps({"gamePk": pk, "playerId": b["playerId"], "stat": st,
                                            "pmf": {str(k): round(v, 6) for k, v in sorted(pmf.items())}},
                                           sort_keys=True) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=("dev", "fit", "forecast"))
    ap.add_argument("--feed-root", default=os.path.join(_ROOT, "data", "research", "hitter_w2", "mlb_feed"))
    ap.add_argument("--population", default="PIT", choices=("PIT", "OFF_H1"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--n-sims", type=int, default=N_SIMS)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--arms", default="v2,w2,baseline")
    ap.add_argument("--market-file", default=None, help="present only for the non-leakage rerun; never read by PURE")
    ap.add_argument("--quotes-file", default=None, help="present only for the non-leakage rerun; never read")
    ap.add_argument("--control-read-market", action="store_true", help="NEGATIVE CONTROL: read --market-file")
    a = ap.parse_args(argv)
    if a.stage == "dev":
        stage_dev(a.feed_root, a.out or os.path.join(OUT_DIR, "dev_2025"), n_sims=a.n_sims)
    elif a.stage == "fit":
        stage_fit(a.feed_root)
    else:
        stage_forecast(a.feed_root, a.population, a.out, a.n_sims, a.seed, a.start, a.end, a.market_file,
                       a.control_read_market, tuple(a.arms.split(",")))


if __name__ == "__main__":
    main()
