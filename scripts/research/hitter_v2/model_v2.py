#!/usr/bin/env python3
"""
scripts/research/hitter_v2/model_v2.py
======================================
SIFT MLB hitter V2 lane -- RESEARCH ONLY. Parameter fitting (training data
only) and per-team-game spec construction for the V2 challenger
MLB_HITTER_V2_PURE. Sports data only.

Per-PA category rates over {K, BB, HBP, 1B, 2B, 3B, HR, OUT}:

  vs the opposing starter (hand h; batter effective hand e):
      r = norm( b_h * p_e / L(h, e) * home_side * park * kappa_sp )
  vs the opposing bullpen:
      r = norm( b_mix * pen_e / L_mix * home_side * park * kappa_pen )

  b_all : batter overall rates, W2 shrinkage (cur + w prev + m_c league) / (n + m_c)
  b_h   : batter split vs hand h, shrunk with m_bsplit toward norm(b_all * F_bat[(side, h)])  [platoon]
  p_all : starter overall rates, W2 shrinkage
  p_e   : starter split vs batter hand e, shrunk with m_psplit toward norm(p_all * F_pit[(h, e)])
  L(h,e): league rates of pitchers of hand h vs batters of effective hand e
  pen_e : opposing bullpen rates vs batter hand e from recency-weighted (half-life) current-season
          counts + w_pen previous season, shrunk with m_pen toward the league bullpen rates vs e
  pi_L  : P(reliever is LHP | team, e), shrunk with m_mix toward the league share
  b_mix = pi_L b_L + (1 - pi_L) b_R ;  L_mix = pi_L L(L, e) + (1 - pi_L) L(R, e)
  league: league-environment prior = previous-season league rates blended with the current season's
          league counts to date with strength K_env PA
  home_side: per-category home / away batting factor (sqrt of the training home/away rate ratio)
  kappa : per-category multiplicative calibration (walk-rate calibration), vs starter and vs bullpen
          separately, fitted by iterative proportional scaling on the fitting season.

Substitution hazard: logistic regression on training at-risk rows with one-hot slot, slot-PA
ordinal bucket and (inning bucket x margin bucket) cells, plus a per-batter log-odds offset
log((O + a) / (E + a)) from his as-of observed vs expected removals (current + w_sub * previous).
"""
import math
import os
import sys
from collections import defaultdict

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_W2 = os.path.join(_ROOT, "scripts", "research", "hitter_w2")
if _W2 not in sys.path:
    sys.path.insert(0, _W2)

from lib.research import hitter_w2_engine as W2  # noqa: E402
from lib.research import hitter_v2_engine as V  # noqa: E402
import model as M2  # noqa: E402  (frozen W2 model code, imported, not modified)
import data_v2 as D  # noqa: E402

NC = len(W2.CATS)
INF = float("inf")
GRID_K_ENV = (INF, 60000.0, 20000.0)
GRID_M_BSPLIT = (100.0, 200.0, 400.0, 800.0, 1600.0, INF)
GRID_M_PSPLIT = (200.0, 400.0, 800.0, 1600.0, INF)
GRID_PEN_HL = (INF, 60, 30, 15)
GRID_W_PEN = (0.0, 0.25, 0.5, 1.0)
GRID_M_PEN = (500.0, 1000.0, 2000.0, 4000.0, 8000.0)
GRID_M_MIX = (50.0, 200.0, 800.0, INF)
GRID_A_SUB = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, INF)
GRID_W_SUB = (0.5, 1.0)
KAPPA_ITERS = 25


def norm(v):
    s = sum(v)
    return [x / s for x in v]


def _shrink(counts, m, prior):
    """(counts + m prior) / (n + m); m = inf -> prior."""
    if m == INF or not counts:
        return list(prior)
    n = sum(counts)
    return [(counts[i] + m * prior[i]) / (n + m) for i in range(NC)]


def _add(a, b, wb=1.0):
    if not a and not b:
        return None
    a = a or [0] * NC
    b = b or [0] * NC
    return [a[i] + wb * b[i] for i in range(NC)]


# ---------------------------------------------------------------------------------------------
# League-level tables (training PAs)
# ---------------------------------------------------------------------------------------------
def league_tables(pas_list):
    """Platoon factors and matchup-league rates from training PAs."""
    tot = W2.empty_counts()
    by_bs = defaultdict(W2.empty_counts)          # batter raw side
    by_bs_h = defaultdict(W2.empty_counts)        # (raw side, pitch hand)
    by_h = defaultdict(W2.empty_counts)           # pitch hand
    by_h_e = defaultdict(W2.empty_counts)         # (pitch hand, eff hand)
    pen_e = defaultdict(W2.empty_counts)          # bullpen vs eff hand
    pen_mix = defaultdict(lambda: [0, 0])         # batter raw side -> [LHP, RHP] bullpen PAs
    home = {True: W2.empty_counts(), False: W2.empty_counts()}
    for pas in pas_list:
        for p in pas:
            c = W2.CI[p["cls"]]
            bs = p["batHand"] or "R"
            h = p["pitchHand"] or "R"
            e = D.eff_hand(bs, h)
            tot[c] += 1
            by_bs[bs][c] += 1
            by_bs_h[(bs, h)][c] += 1
            by_h[h][c] += 1
            by_h_e[(h, e)][c] += 1
            home[p["batSideTeam"] == "H"][c] += 1
            if not p["vsStarter"]:
                pen_e[e][c] += 1
                pen_mix[bs][0 if h == "L" else 1] += 1
    lr = norm(tot)
    F_bat = {f"{bs}{h}": [(c[i] / sum(c)) / (by_bs[bs][i] / sum(by_bs[bs])) for i in range(NC)]
             for (bs, h), c in by_bs_h.items() if bs in ("L", "R", "S") and h in ("L", "R")}
    F_pit = {f"{h}{e}": [(c[i] / sum(c)) / (by_h[h][i] / sum(by_h[h])) for i in range(NC)]
             for (h, e), c in by_h_e.items() if h in ("L", "R")}
    # matchup league rates relative to the overall league (multiplied by the env league at use time)
    L_rel = {f"{h}{e}": [(c[i] / sum(c)) / lr[i] for i in range(NC)] for (h, e), c in by_h_e.items()
             if h in ("L", "R")}
    pen_rel = {e: [(c[i] / sum(c)) / lr[i] for i in range(NC)] for e, c in pen_e.items()}
    mix = {e: v[0] / (v[0] + v[1]) for e, v in pen_mix.items()}
    hr = [(home[True][i] / sum(home[True])) / (home[False][i] / sum(home[False])) for i in range(NC)]
    home_f = {"H": [math.sqrt(x) for x in hr], "A": [1 / math.sqrt(x) for x in hr]}
    return {"F_bat": F_bat, "F_pit": F_pit, "L_rel": L_rel, "pen_rel": pen_rel, "pi_L_league": mix,
            "home": home_f}


def runner_out_q(ro_list):
    out = [0.0] * 8
    for b in range(1, 8):
        n = sum(ro[b][0] for ro in ro_list)
        d = sum(ro[b][1] for ro in ro_list)
        out[b] = n / d if d else 0.0
    return out


# ---------------------------------------------------------------------------------------------
# Substitution hazard
# ---------------------------------------------------------------------------------------------
N_HAZ = 9 + 4 + 6 * 7 + 2
SEPT_IDX = N_HAZ - 1   # September (expanded 28-man rosters) indicator; N_HAZ - 2 is its 'not September' twin


def is_september(date):
    return date[5:7] == "09"


def haz_features(slot, k, inning, margin, sept=False):
    return (slot - 1, 9 + V.k_bucket(k), 13 + V.inning_bucket(inning) * 7 + V.margin_bucket(margin),
            SEPT_IDX if sept else SEPT_IDX - 1)


def fit_hazard(at_risk_list, offsets_list=None, iters=30, l2=1.0):
    """Logistic regression by Newton/IRLS on one-hot features (no intercept: the cell one-hots span it),
    with an optional fixed per-row log-odds offset (the batter's removal propensity).
    Rows: (gamePk, side, slot, starterId, k, inning, margin, event, officialDate)."""
    X = []
    y = []
    off = []
    for j, ar in enumerate(at_risk_list):
        for i, r in enumerate(ar):
            X.append(haz_features(r[2], r[4], r[5], r[6], is_september(r[8])))
            y.append(r[7])
            off.append(offsets_list[j][i] if offsets_list is not None else 0.0)
    n = len(X)
    idx = np.array(X, dtype=np.int64)
    yv = np.array(y, dtype=np.float64)
    ov = np.array(off, dtype=np.float64)
    beta = np.zeros(N_HAZ)
    beta[13:13 + 42] = math.log(max(yv.mean(), 1e-4) / (1 - max(yv.mean(), 1e-4)))
    for _ in range(iters):
        eta = beta[idx].sum(axis=1) + ov
        p = 1 / (1 + np.exp(-eta))
        w = p * (1 - p)
        g = np.zeros(N_HAZ)
        H = np.zeros((N_HAZ, N_HAZ))
        for j in range(4):
            np.add.at(g, idx[:, j], yv - p)
        for a in range(4):
            for b in range(4):
                np.add.at(H, (idx[:, a], idx[:, b]), w)
        g -= l2 * beta * 1e-3
        H += np.eye(N_HAZ) * (l2 * 1e-3)
        step = np.linalg.solve(H, g)
        beta += step
        if np.abs(step).max() < 1e-8:
            break
    return {"beta": [round(float(b), 8) for b in beta], "n_rows": n, "n_events": int(yv.sum())}


def hazard_prob(beta, slot, k, inning, margin, offset=0.0, sept=False):
    a, b, c, d = haz_features(slot, k, inning, margin, sept)
    eta = beta[a] + beta[b] + beta[c] + beta[d] + offset
    return 1 / (1 + math.exp(-eta))


def propensity_series(sd, at_risk, beta):
    """Per player, chronological per-date cumulative (O, E) from at-risk rows, for as-of lookup.
    Returns {playerId: [(date, O_before_date, E_before_date)]} built per date."""
    games = sd["games"]
    per = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))
    for r in at_risk:
        pk, _side, slot, starter, k, inning, margin, ev, d = r
        e = hazard_prob(beta, slot, k, inning, margin, sept=is_september(d))
        x = per[starter][d]
        x[0] += ev
        x[1] += e
    out = {}
    for pid, dd in per.items():
        cum_o = cum_e = 0.0
        ser = []
        for d in sorted(dd):
            ser.append((d, cum_o, cum_e))
            cum_o += dd[d][0]
            cum_e += dd[d][1]
        ser.append(("9999-99-99", cum_o, cum_e))
        out[pid] = ser
    return out


def propensity_asof(series, pid, date):
    """(O, E) strictly before date."""
    ser = series.get(pid)
    if not ser:
        return 0.0, 0.0
    lo = 0
    for i, (d, o, e) in enumerate(ser):
        if d >= date:
            return o, e
        lo = i
    return ser[lo][1], ser[lo][2]


def row_offsets(at_risk, series, prev_tot, a, w):
    out = []
    for r in at_risk:
        oc, ec = propensity_asof(series, r[3], r[8])
        op, ep = prev_tot.get(r[3], (0.0, 0.0))
        out.append(sub_offset(oc, ec, op, ep, a, w))
    return out


def season_propensity_totals(series):
    return {pid: (ser[-1][1], ser[-1][2]) for pid, ser in series.items()}


def sub_offset(o_cur, e_cur, o_prev, e_prev, a, w):
    if a == INF:
        return 0.0
    o = o_cur + w * o_prev
    e = e_cur + w * e_prev
    return math.log((o + a) / (e + a))


def fit_sub_propensity(at_risk_fit, sd_fit, series_fit, prev_tot, beta):
    """Grid (a, w) by log-likelihood on the fitting season's at-risk rows."""
    rows = []
    for r in at_risk_fit:
        pk, _side, slot, starter, k, inning, margin, ev, d = r
        oc, ec = propensity_asof(series_fit, starter, d)
        op, ep = prev_tot.get(starter, (0.0, 0.0))
        a_, b_, c_, d_ = haz_features(slot, k, inning, margin, is_september(d))
        rows.append((beta[a_] + beta[b_] + beta[c_] + beta[d_], ev, oc, ec, op, ep))
    best = None
    for a in GRID_A_SUB:
        for w in GRID_W_SUB:
            ll = 0.0
            for eta, ev, oc, ec, op, ep in rows:
                p = 1 / (1 + math.exp(-(eta + sub_offset(oc, ec, op, ep, a, w))))
                ll += math.log(p if ev else 1 - p)
            if best is None or ll > best[0]:
                best = (ll, a, w)
            if a == INF:
                break
    return {"a": best[1], "w": best[2], "loglik": best[0], "n_rows": len(rows)}


# ---------------------------------------------------------------------------------------------
# Per-PA rates
# ---------------------------------------------------------------------------------------------
def env_league(rec, P, k_env):
    prev = P["league"]
    cur = rec.get("league_cur")
    if k_env == INF or not cur:
        return list(prev)
    n = sum(cur)
    return [(cur[i] + k_env * prev[i]) / (n + k_env) for i in range(NC)]


def _lrel(league, rel):
    return norm([league[i] * rel[i] for i in range(NC)])


def batter_split(b, h, b_all, P, m_bsplit, w):
    prior = norm([b_all[i] * P["T"]["F_bat"].get(f"{b['batSide']}{h}", [1.0] * NC)[i] for i in range(NC)])
    cnt = _add((b.get("cur_vs") or {}).get(h), (b.get("prev_vs") or {}).get(h), w)
    return _shrink(cnt, m_bsplit, prior)


def pitcher_split(rec, e, p_all, P, m_psplit, w):
    h = rec["oppStarterHand"] or "R"
    prior = norm([p_all[i] * P["T"]["F_pit"].get(f"{h}{e}", [1.0] * NC)[i] for i in range(NC)])
    cnt = _add((rec.get("sp_cur_vs") or {}).get(e), (rec.get("sp_prev_vs") or {}).get(e), w)
    return _shrink(cnt, m_psplit, prior)


def bullpen_rates(rec, e, league, P, hl, w_pen, m_pen):
    prior = _lrel(league, P["T"]["pen_rel"][e])
    if hl == INF:
        cur = (rec.get("pen_cur_vs") or {}).get(e)
    else:
        cur = (rec.get("pen_decay") or {}).get(hl, {}).get(e)
    cnt = _add(cur, (rec.get("pen_prev_vs") or {}).get(e), w_pen)
    return _shrink(cnt, m_pen, prior)


def pen_mix(rec, bs, P, m_mix, w_pen):
    """P(reliever is LHP | opposing bullpen, batter raw side bs)."""
    lg = P["T"]["pi_L_league"].get(bs, P["T"]["pi_L_league"]["R"])
    c = (rec.get("pen_mix_cur") or {}).get(bs) or [0, 0]
    pv = (rec.get("pen_mix_prev") or {}).get(bs) or [0, 0]
    nl = c[0] + w_pen * pv[0]
    n = c[0] + c[1] + w_pen * (pv[0] + pv[1])
    if m_mix == INF:
        return lg
    return (nl + m_mix * lg) / (n + m_mix)


def team_rates(rec, P, cfg):
    """Per slot (rates_vs_starter, rates_vs_bullpen) for a V2 config dict."""
    k_env = cfg["k_env"]
    league = env_league(rec, P, k_env)
    bp = P["batter"]
    sp_all = W2.shrunk_rates(rec["sp_cur"], rec["sp_prev"], P["pitcher"]["w"], P["pitcher"]["m"], league)
    park = P["park"].get(rec["venueId"]) if cfg.get("park", True) else None
    homef = P["T"]["home"]["H" if rec["home"] else "A"] if cfg.get("home_factor") else None
    ks = cfg.get("kappa_sp")
    kb = cfg.get("kappa_pen")
    h = rec["oppStarterHand"] or "R"
    out_s, out_b = [], []
    for b in rec["batters"]:
        b_all = M2.batter_rates(b["cur"], b["prev"], bp, league)
        e = D.eff_hand(b["batSide"], h)
        if cfg.get("platoon_v2"):
            bh = batter_split(b, h, b_all, P, cfg["m_bsplit"], bp["w"])
            pe = pitcher_split(rec, e, sp_all, P, cfg["m_psplit"], P["pitcher"]["w"])
            L = _lrel(league, P["T"]["L_rel"][f"{h}{e}"])
            rs = norm([bh[i] * pe[i] / L[i] for i in range(NC)])
        else:  # W2 form
            rs = W2.log5(b_all, sp_all, league)
            pf = P["platoon"].get(f"{b['batSide']}{h}")
            if pf:
                rs = W2.apply_factor(rs, pf)
        if cfg.get("bullpen_v2"):
            piL = pen_mix(rec, b["batSide"], P, cfg["m_mix"], cfg["w_pen"])
            if cfg.get("platoon_v2"):
                bL = batter_split(b, "L", b_all, P, cfg["m_bsplit"], bp["w"])
                bR = batter_split(b, "R", b_all, P, cfg["m_bsplit"], bp["w"])
            else:
                bL = bR = b_all
            if b["batSide"] == "S":
                # switch hitter: bats R vs a LHP reliever, L vs a RHP reliever
                vl = norm([bL[i] * x / y for i, (x, y) in enumerate(zip(
                    bullpen_rates(rec, "R", league, P, cfg["pen_hl"], cfg["w_pen"], cfg["m_pen"]),
                    _lrel(league, P["T"]["L_rel"]["LR"])))])
                vr = norm([bR[i] * x / y for i, (x, y) in enumerate(zip(
                    bullpen_rates(rec, "L", league, P, cfg["pen_hl"], cfg["w_pen"], cfg["m_pen"]),
                    _lrel(league, P["T"]["L_rel"]["RL"])))])
                rb = [piL * vl[i] + (1 - piL) * vr[i] for i in range(NC)]
            else:
                e0 = b["batSide"]
                pen_e = bullpen_rates(rec, e0, league, P, cfg["pen_hl"], cfg["w_pen"], cfg["m_pen"])
                LL = _lrel(league, P["T"]["L_rel"][f"L{e0}"])
                LR = _lrel(league, P["T"]["L_rel"][f"R{e0}"])
                b_mix = [piL * bL[i] + (1 - piL) * bR[i] for i in range(NC)]
                L_mix = [piL * LL[i] + (1 - piL) * LR[i] for i in range(NC)]
                # the bullpen's own hand mix is inside pen_e; dividing by the league rate at the same
                # hand mix counts the platoon effect once
                rb = norm([b_mix[i] * pen_e[i] / L_mix[i] for i in range(NC)])
        else:
            pen = W2.shrunk_rates(rec["pen_cur"], rec["pen_prev"], P["bullpen"]["w"], P["bullpen"]["m"], league)
            rb = W2.log5(b_all, pen, league)
        if park:
            rs = W2.apply_factor(rs, park)
            rb = W2.apply_factor(rb, park)
        if homef:
            rs = W2.apply_factor(rs, homef)
            rb = W2.apply_factor(rb, homef)
        if ks:
            rs = W2.apply_factor(rs, ks)
        if kb:
            rb = W2.apply_factor(rb, kb)
        out_s.append(rs)
        out_b.append(rb)
    return out_s, out_b


def perpa_loglik(records, P, cfg, which=("sp", "pen")):
    ll, n = 0.0, 0
    for rec in records:
        if rec.get("excluded"):
            continue
        rs, rb = team_rates(rec, P, cfg)
        for i, b in enumerate(rec["batters"]):
            if "sp" in which:
                y = b["y_cls_sp"]
                for c in range(NC):
                    if y[c]:
                        ll += y[c] * math.log(max(rs[i][c], 1e-12))
                        n += y[c]
            if "pen" in which:
                y = b["y_cls_pen"]
                for c in range(NC):
                    if y[c]:
                        ll += y[c] * math.log(max(rb[i][c], 1e-12))
                        n += y[c]
    return -ll / max(n, 1), n


def fit_kappa(records, P, cfg):
    """Iterative proportional scaling of per-category factors so that summed predicted category
    probabilities match observed counts on the fitting season (separately vs starter / bullpen)."""
    ks = [1.0] * NC
    kb = [1.0] * NC
    base = dict(cfg, kappa_sp=None, kappa_pen=None)
    cache = []
    for rec in records:
        if rec.get("excluded"):
            continue
        rs, rb = team_rates(rec, P, base)
        for i, b in enumerate(rec["batters"]):
            cache.append((rs[i], rb[i], b["y_cls_sp"], b["y_cls_pen"]))
    for _ in range(KAPPA_ITERS):
        for which in ("sp", "pen"):
            kap = ks if which == "sp" else kb
            exp = [0.0] * NC
            obs = [0.0] * NC
            for rs, rb, ysp, ypen in cache:
                r = rs if which == "sp" else rb
                y = ysp if which == "sp" else ypen
                n = sum(y)
                if not n:
                    continue
                q = norm([r[c] * kap[c] for c in range(NC)])
                for c in range(NC):
                    exp[c] += n * q[c]
                    obs[c] += y[c]
            for c in range(NC):
                kap[c] *= obs[c] / exp[c]
            s = sum(kap) / NC
            for c in range(NC):
                kap[c] /= s
    return [round(x, 6) for x in ks], [round(x, 6) for x in kb]


def fit_rate_config(records, P, log=print):
    """Coordinate grid search on the fitting season's per-PA multinomial log-loss."""
    cfg = {"k_env": INF, "platoon_v2": True, "m_bsplit": INF, "m_psplit": INF, "bullpen_v2": True,
           "pen_hl": INF, "w_pen": 0.5, "m_pen": 2000.0, "m_mix": INF, "home_factor": True, "park": True}
    trace = []

    def ev(c, which):
        v, n = perpa_loglik(records, P, c, which)
        return v

    for key, grid, which in (("k_env", GRID_K_ENV, ("sp", "pen")), ("m_bsplit", GRID_M_BSPLIT, ("sp", "pen")),
                             ("m_psplit", GRID_M_PSPLIT, ("sp",)), ("pen_hl", GRID_PEN_HL, ("pen",)),
                             ("w_pen", GRID_W_PEN, ("pen",)), ("m_pen", GRID_M_PEN, ("pen",)),
                             ("m_mix", GRID_M_MIX, ("pen",)), ("pen_hl", GRID_PEN_HL, ("pen",)),
                             ("m_pen", GRID_M_PEN, ("pen",))):
        scores = []
        for v in grid:
            c = dict(cfg)
            c[key] = v
            scores.append((ev(c, which), v))
        best = min(scores)
        cfg[key] = best[1]
        trace.append({"param": key, "chosen": best[1], "scores": [(round(s, 6), v) for s, v in scores]})
        log(f"  {key}: {best[1]}  ({[(round(s, 5), v) for s, v in scores]})")
    ks, kb = fit_kappa(records, P, cfg)
    cfg["kappa_sp"], cfg["kappa_pen"] = ks, kb
    return cfg, trace


# ---------------------------------------------------------------------------------------------
# Team spec
# ---------------------------------------------------------------------------------------------
def team_spec_v2(rec, P, cfg, prop_lookup):
    rs, rb = team_rates(rec, P, cfg)
    beta = P["hazard"]["beta"]
    a, w = P["sub_prop"]["a"], P["sub_prop"]["w"]
    hazard = []
    sept = is_september(rec["date"])
    for b in rec["batters"]:
        off = 0.0
        if cfg.get("sub_propensity", True):
            oc, ec, op, ep = prop_lookup(b["playerId"], rec["date"])
            off = sub_offset(oc, ec, op, ep, a, w)
        tab = {}
        for kb in range(4):
            k = kb + 1
            for ib in range(6):
                inning = (4, 5, 6, 7, 8, 9)[ib]
                for mb in range(7):
                    margin = (-5, -3, -1, 0, 1, 3, 5)[mb]
                    tab[(kb, ib, mb)] = hazard_prob(beta, b["slot"], k, inning, margin, off, sept)
        hazard.append(tab)
    subr = W2.apply_factor(P["sub_rates"], P["park"][rec["venueId"]]) if rec["venueId"] in P["park"] else P["sub_rates"]
    exp_bf = M2.expected_bf(rec["sp_bf_cur"], rec["sp_bf_prev"], P["league_bf"])
    q = P["runner_out_q"] if cfg.get("runner_outs", True) else [0.0] * 8
    return V.TeamSpecV2([b["playerId"] for b in rec["batters"]], rs, rb, subr, hazard, q, exp_bf,
                        P["bf_residuals"]), exp_bf
