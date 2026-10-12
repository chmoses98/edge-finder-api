#!/usr/bin/env python3
"""
scripts/research/hitter_w2/model.py
===================================
SIFT W2 hitter lane -- RESEARCH ONLY. Parameter fitting (training seasons
only) and per-game forecasting for MLB_HITTER_W2_PURE_V1 and the simple
baseline MLB_HITTER_W2_BASELINE_V1. Sports data only.
"""
import math
import os
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.research import hitter_w2_engine as E  # noqa: E402

GRID_W = (0.0, 0.25, 0.5, 0.75, 1.0)
GRID_M_BAT = (25, 50, 100, 200, 400, 800, 1600)
GRID_M_PIT = (100, 200, 400, 800, 1600, 3200)
GRID_W_PIT = (0.25, 0.5, 1.0)
GRID_M_PEN = (250, 500, 1000, 2000, 4000)
GRID_W_PEN = (0.0, 0.5, 1.0)
BF_SHRINK_K = 5.0
BF_PREV_W = 0.5
PARK_K = 6000.0
BASELINE_M = 50.0
SURVIVAL_MAX_K = 8
THRESHOLDS = {"PA": 5, "H": 1, "TB": 2, "HR": 1, "RBI": 1, "R": 1}


def _i(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


# ---------------------------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------------------------
def fit_batter_shrinkage(records, league):
    """Per-category m and common w by binary log-likelihood of each category indicator."""
    obs = []
    for rec in records:
        if rec.get("excluded"):
            continue
        for b in rec["batters"]:
            n = sum(b["y_cls"])
            if n:
                obs.append((b["cur"], b["prev"], b["y_cls"], n))
    best = None
    for w in GRID_W:
        ms, tot = [], 0.0
        for c in range(len(E.CATS)):
            bl, bm = None, None
            for m in GRID_M_BAT:
                ll = 0.0
                for cur, prev, y, n in obs:
                    ncur = sum(cur) if cur else 0
                    nprev = sum(prev) if prev else 0
                    r = ((cur[c] if cur else 0) + w * (prev[c] if prev else 0) + m * league[c]) / (ncur + w * nprev + m)
                    r = min(max(r, 1e-9), 1 - 1e-9)
                    ll -= y[c] * math.log(r) + (n - y[c]) * math.log(1 - r)
                if bl is None or ll < bl:
                    bl, bm = ll, m
            ms.append(bm)
            tot += bl
        if best is None or tot < best[0]:
            best = (tot, w, ms)
    return {"w": best[1], "m": best[2]}


def batter_rates(cur, prev, bp, league):
    ncur = sum(cur) if cur else 0
    nprev = sum(prev) if prev else 0
    raw = []
    for c in range(len(E.CATS)):
        m = bp["m"][c]
        raw.append(((cur[c] if cur else 0) + bp["w"] * (prev[c] if prev else 0) + m * league[c]) /
                   (ncur + bp["w"] * nprev + m))
    s = sum(raw)
    return [x / s for x in raw]


def _multi_ll(rates, y):
    return -sum(y[c] * math.log(max(rates[c], 1e-12)) for c in range(len(E.CATS)) if y[c])


def fit_pitcher_shrinkage(records, league, bp, key_cur, key_prev, ykey, grid_w, grid_m):
    best = None
    pre = []
    for rec in records:
        if rec.get("excluded"):
            continue
        for b in rec["batters"]:
            y = b[ykey]
            if sum(y):
                pre.append((batter_rates(b["cur"], b["prev"], bp, league), rec[key_cur], rec[key_prev], y))
    for w in grid_w:
        for m in grid_m:
            ll = 0.0
            for br, cur, prev, y in pre:
                pr = E.shrunk_rates(cur, prev, w, m, league)
                ll += _multi_ll(E.log5(br, pr, league), y)
            if best is None or ll < best[0]:
                best = (ll, w, m)
    return {"w": best[1], "m": best[2], "logloss_per_pa": best[0] / max(1, sum(sum(x[3]) for x in pre))}


def platoon_factors(pas):
    tot = defaultdict(lambda: [0] * len(E.CATS))
    by = defaultdict(lambda: [0] * len(E.CATS))
    for p in pas:
        bh, ph = p["batHand"] or "?", p["pitchHand"] or "?"
        tot[bh][E.CI[p["cls"]]] += 1
        by[(bh, ph)][E.CI[p["cls"]]] += 1
    out = {}
    for (bh, ph), c in by.items():
        if bh not in ("L", "R", "S") or ph not in ("L", "R"):
            continue
        n, nt = sum(c), sum(tot[bh])
        out[f"{bh}{ph}"] = [(c[i] / n) / (tot[bh][i] / nt) if tot[bh][i] else 1.0 for i in range(len(E.CATS))]
    return out


def park_factors(sd_list, pas_list):
    """Home/road per (venueId, homeTeamId): rate in games at the venue / rate in that home team's road
    games, shrunk toward 1 with PARK_K plate appearances."""
    at_v = defaultdict(lambda: [0] * len(E.CATS))
    road = defaultdict(lambda: [0] * len(E.CATS))
    for sd, pas in zip(sd_list, pas_list):
        games = sd["games"]
        for p in pas:
            g = games[p["gamePk"]]
            c = E.CI[p["cls"]]
            at_v[(g["venueId"], g["homeId"])][c] += 1
            road[g["awayId"]][c] += 1  # road games of the away team
    out = {}
    for (v, home), c in at_v.items():
        r = road.get(home)
        if not r:
            continue
        n, nr = sum(c), sum(r)
        lam = n / (n + PARK_K)
        out[v] = [1.0 + lam * (((c[i] / n) / (r[i] / nr)) - 1.0) if r[i] and c[i] else 1.0 for i in range(len(E.CATS))]
    return out


def lineup_maps(sd):
    m = {}
    for r in sd["batting"]:
        m[(_i(r["gamePk"]), r["side"], _i(r["playerId"]))] = (_i(r["slot"]), r["isStarter"] == "1")
    return m


def fit_survival(sd_list, pas_list):
    """s[slot][k] = P(the starter takes the slot's k-th PA | that PA occurs), forced non-increasing."""
    num = [[0] * SURVIVAL_MAX_K for _ in range(9)]
    den = [[0] * SURVIVAL_MAX_K for _ in range(9)]
    for sd, pas in zip(sd_list, pas_list):
        lm = lineup_maps(sd)
        cnt = defaultdict(int)
        for p in pas:
            info = lm.get((p["gamePk"], p["batSideTeam"], p["batterId"]))
            if not info or not (1 <= info[0] <= 9):
                continue
            slot = info[0] - 1
            key = (p["gamePk"], p["batSideTeam"], slot)
            k = min(cnt[key], SURVIVAL_MAX_K - 1)
            cnt[key] += 1
            den[slot][k] += 1
            num[slot][k] += int(info[1])
    out = []
    for s in range(9):
        row, last = [], 1.0
        for k in range(SURVIVAL_MAX_K):
            v = num[s][k] / den[s][k] if den[s][k] else last
            last = min(last, v)
            row.append(round(last, 6))
        out.append(row)
    return out


def sub_rates(sd_list, pas_list):
    c = [0] * len(E.CATS)
    for sd, pas in zip(sd_list, pas_list):
        lm = lineup_maps(sd)
        for p in pas:
            info = lm.get((p["gamePk"], p["batSideTeam"], p["batterId"]))
            if info and not info[1]:
                c[E.CI[p["cls"]]] += 1
    n = sum(c)
    return [x / n for x in c]


def expected_bf(cur, prev, league_bf):
    n, s = len(cur), sum(cur)
    np_, sp = len(prev), sum(prev)
    return (s + BF_PREV_W * sp + BF_SHRINK_K * league_bf) / (n + BF_PREV_W * np_ + BF_SHRINK_K)


def bf_residuals(records, league_bf):
    res = []
    seen = set()
    for rec in records:
        if rec.get("excluded") or rec.get("y_sp_bf") is None:
            continue
        key = (rec["gamePk"], rec["side"])
        if key in seen:
            continue
        seen.add(key)
        res.append(round(rec["y_sp_bf"] - expected_bf(rec["sp_bf_cur"], rec["sp_bf_prev"], league_bf), 4))
    return sorted(res)


def league_bf(sd):
    v = [_i(r["BF"]) for r in sd["pitching"] if _i(r["order"]) == 0 and r["BF"] != ""]
    return sum(v) / len(v)


def baseline_tables(records):
    """Slot x home PA pmf and league per-PA rates for the baseline, from one (previous) season."""
    pa_counts = defaultdict(lambda: defaultdict(int))
    tot = defaultdict(float)
    for rec in records:
        if rec.get("excluded"):
            continue
        for b in rec["batters"]:
            pa_counts[f"{b['slot']}{'H' if rec['home'] else 'A'}"][b["y"]["PA"]] += 1
            for k in ("PA", "H", "TB", "HR", "RBI", "R"):
                tot[k] += b["y"][k]
            for k, c in zip(E.CATS, b["y_cls"]):
                tot["cls_" + k] += c
            tot["PA_pbp"] += sum(b["y_cls"])
    pmf = {}
    for key, d in pa_counts.items():
        n = sum(d.values())
        pmf[key] = {int(k): v / n for k, v in sorted(d.items())}
    rates = {k: tot[k] / tot["PA"] for k in ("H", "TB", "HR", "RBI", "R")}
    rates.update({k: tot["cls_" + k] / tot["PA_pbp"] for k in E.CATS})
    return {"pa_pmf": pmf, "rates": rates}


# ---------------------------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------------------------
def team_spec(rec, P):
    league = P["league"]
    bp = P["batter"]
    sp = E.shrunk_rates(rec["sp_cur"], rec["sp_prev"], P["pitcher"]["w"], P["pitcher"]["m"], league)
    pen = E.shrunk_rates(rec["pen_cur"], rec["pen_prev"], P["bullpen"]["w"], P["bullpen"]["m"], league)
    park = P["park"].get(rec["venueId"])
    vs_s, vs_b = [], []
    for b in rec["batters"]:
        br = batter_rates(b["cur"], b["prev"], bp, league)
        rs = E.log5(br, sp, league)
        pf = P["platoon"].get(f"{b['batSide']}{rec['oppStarterHand']}")
        if pf:
            rs = E.apply_factor(rs, pf)
        rb = E.log5(br, pen, league)
        if park:
            rs = E.apply_factor(rs, park)
            rb = E.apply_factor(rb, park)
        vs_s.append(rs)
        vs_b.append(rb)
    subr = E.apply_factor(P["sub_rates"], park) if park else P["sub_rates"]
    exp_bf = expected_bf(rec["sp_bf_cur"], rec["sp_bf_prev"], P["league_bf"])
    return E.TeamSpec([b["playerId"] for b in rec["batters"]], vs_s, vs_b, subr, exp_bf, P["bf_residuals"],
                      P["survival"]), exp_bf


def challenger_game(rec_a, rec_h, P, table, n_sims, seed):
    spec_a, bf_a = team_spec(rec_a, P)
    spec_h, bf_h = team_spec(rec_h, P)
    res = E.run_game_sims(spec_a, spec_h, table, n_sims, seed)
    out = {}
    for side, rec, bfx in (("A", rec_a, bf_a), ("H", rec_h, bf_h)):
        lines = res[side]["lines"]
        runs = res[side]["runs"]
        players = []
        for s, b in enumerate(rec["batters"]):
            vals = lines[s]
            d = {}
            for j, k in enumerate(E.STATS):
                d[k] = E.pmf_from_values([v[j] for v in vals])
            n = len(vals)
            d["_joint"] = {
                "E_H_RBI": sum(v[1] * v[4] for v in vals) / n, "E_H_R": sum(v[1] * v[5] for v in vals) / n,
                "E_PA_vs_sp": sum(v[6] for v in vals) / n,
                "coherent": all(v[2] >= v[1] >= v[3] for v in vals),
            }
            players.append(d)
        # teammate joint moments: adjacent R_i * RBI_{i+1}, team RBI <= runs, sum R == runs per draw
        adj = []
        for s in range(9):
            t = (s + 1) % 9
            adj.append(sum(lines[s][i][5] * lines[t][i][4] for i in range(len(runs))) / len(runs))
        out[side] = {"players": players, "team_runs": E.pmf_from_values(runs), "E_adj_R_RBI": adj,
                     "exp_sp_bf": bfx}
    return out


def baseline_player(b, home, P):
    """Season-to-date per-PA rates (shrunk with BASELINE_M PAs toward the previous season's league
    rates; no previous-season player data, no opponent, no park) x the previous season's slot/home
    PA distribution. H, HR: binomial given PA; TB: per-PA categorical convolution; RBI, R: Poisson
    with mean PA x season-to-date rate."""
    T = P["baseline"]
    lr = T["rates"]
    cur = b["cur"]
    ncur = sum(cur) if cur else 0
    cat = {c: ((cur[E.CI[c]] if cur else 0) + BASELINE_M * lr[c]) / (ncur + BASELINE_M) for c in E.CATS}
    box = b["box_cur"]
    n = box[0] if box else 0
    rbi = ((box[4] if box else 0) + BASELINE_M * lr["RBI"]) / (n + BASELINE_M)
    r = ((box[5] if box else 0) + BASELINE_M * lr["R"]) / (n + BASELINE_M)
    h = cat["1B"] + cat["2B"] + cat["3B"] + cat["HR"]
    pa_pmf = T["pa_pmf"][f"{b['slot']}{'H' if home else 'A'}"]
    tb1 = E.per_pa_tb_pmf(cat)
    cache = {0: {0: 1.0}}
    for k in range(1, max(pa_pmf) + 1):
        cache[k] = E.convolve(cache[k - 1], tb1)
    return {
        "PA": dict(pa_pmf),
        "H": E.mixture_over_pa(pa_pmf, lambda k: E.binomial_pmf(k, h)),
        "HR": E.mixture_over_pa(pa_pmf, lambda k: E.binomial_pmf(k, cat["HR"])),
        "TB": E.mixture_over_pa(pa_pmf, lambda k: cache[k]),
        "RBI": E.mixture_over_pa(pa_pmf, lambda k: E.poisson_pmf(k * rbi)),
        "R": E.mixture_over_pa(pa_pmf, lambda k: E.poisson_pmf(k * r)),
    }
