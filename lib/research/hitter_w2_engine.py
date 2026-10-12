#!/usr/bin/env python3
"""
lib/research/hitter_w2_engine.py
================================
SIFT Wave 2, MLB hitter lane -- RESEARCH ONLY. Not wired into any production
path, app export, publication, staking or authority code.

Model name: MLB_HITTER_W2_PURE_V1 ("lineup base-out simulator").
Baseline:   MLB_HITTER_W2_BASELINE_V1 (season-to-date per-PA rates x slot PA).

Sports data only. Inputs are MLB Stats API game-feed extracts committed under
data/research/hitter_w2/mlb_feed/<season>/ (games, batting, pitching, pa).
No odds, totals, Kalshi quotes, settlements or market-selected populations are
read anywhere in this module; the forbidden-vocabulary test in
tests/research/test_hitter_w2_engine.py scans this file.

Challenger structure (preregistered in docs/research/HITTER_W2_PREREG.md):
  1. Per-PA outcome rates over {K, BB, HBP, 1B, 2B, 3B, HR, OUT} for each
     batter, opposing starter and opposing bullpen: current-season-to-date
     counts (strictly before the game date) + w * previous-season counts,
     shrunk toward the previous season's league rates with strength m.
  2. Matchup by odds-ratio (log5) of batter x pitcher / league, times a
     league platoon factor (vs the starter only) and a shrunk home-park factor.
  3. A full two-team game simulation: 9-man lineups, starter BF budget drawn
     from the starter's expected BF + empirical residuals, then bullpen;
     base-out transitions sampled from the EMPIRICAL (event class, outs, bases)
     transition table (post bases, outs added, runs, RBI) fitted on training
     seasons, so sacrifice flies, productive outs, extra bases taken, errors
     and double plays are all represented; late-inning substitution of the
     starter by slot and PA ordinal; home team skips the bottom of the 9th when
     leading; walk-off; extra innings with the automatic runner on 2B.
  Every draw is coherent: TB >= H >= HR per player, team RBI <= team runs,
  sum of R over the batting team == team runs.
"""
import bisect
import csv
import gzip
import math
import os
import random
from collections import defaultdict

CATS = ("K", "BB", "HBP", "1B", "2B", "3B", "HR", "OUT")
CI = {c: i for i, c in enumerate(CATS)}
HIT = {"1B": 1, "2B": 2, "3B": 3, "HR": 4}
STATS = ("PA", "H", "TB", "HR", "RBI", "R")

EVENT_CLASS = {
    "single": "1B", "double": "2B", "triple": "3B", "home_run": "HR",
    "walk": "BB", "intent_walk": "BB", "hit_by_pitch": "HBP",
    "strikeout": "K", "strikeout_double_play": "K", "strikeout_triple_play": "K",
}
# PA-ending events that are not one of the above -> batter "OUT" class (includes reach-on-error,
# fielder's choice, sacrifice flies/bunts, double plays). Runner-only events are not PAs.
NON_PA_EVENTS = {
    "caught_stealing_2b", "caught_stealing_3b", "caught_stealing_home", "pickoff_1b", "pickoff_2b",
    "pickoff_3b", "pickoff_caught_stealing_2b", "pickoff_caught_stealing_3b", "pickoff_caught_stealing_home",
    "stolen_base_2b", "stolen_base_3b", "stolen_base_home", "wild_pitch", "passed_ball", "balk",
    "other_advance", "runner_double_play", "other_out", "game_advisory", "defensive_indiff",
    "pickoff_error_1b", "pickoff_error_2b", "pickoff_error_3b", "error", "",
}

FEED_ROOT_DEFAULT = os.path.join("data", "research", "hitter_w2", "mlb_feed")


def event_class(event_type):
    if event_type in EVENT_CLASS:
        return EVENT_CLASS[event_type]
    if event_type in NON_PA_EVENTS or event_type is None:
        return None
    return "OUT"


# ----------------------------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------------------------
def _read_csv_gz(path):
    with gzip.open(path, "rt", newline="") as f:
        return list(csv.DictReader(f))


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def load_season(season, root=FEED_ROOT_DEFAULT):
    d = os.path.join(root, str(season))
    games = {int(g["gamePk"]): g for g in _read_csv_gz(os.path.join(d, "games.csv.gz"))}
    bat = _read_csv_gz(os.path.join(d, "batting.csv.gz"))
    pit = _read_csv_gz(os.path.join(d, "pitching.csv.gz"))
    pa = _read_csv_gz(os.path.join(d, "pa.csv.gz"))
    return {"games": games, "batting": bat, "pitching": pit, "pa": pa}


def annotate_pa(season_data):
    """Adds pre-state (outsBefore, basesBefore), event class, batting side, pitcher role, and keeps
    only plate appearances. Pre-state = previous play's post-state within the same half-inning;
    the first play of an extra half-inning (inning >= 10) starts with the automatic runner on 2B.
    Returns list of dicts (new objects)."""
    games = season_data["games"]
    starters = {}
    for g in games.values():
        starters[int(g["gamePk"])] = {"A": _int(g["awayStarterId"], None), "H": _int(g["homeStarterId"], None)}
    out = []
    prev_key, outs, bases = None, 0, 0
    for r in season_data["pa"]:
        pk = int(r["gamePk"])
        inning = _int(r["inning"])
        half = r["half"]
        key = (pk, inning, half)
        if key != prev_key:
            outs = 0
            bases = 2 if inning >= 10 else 0
            prev_key = key
        outs_after = _int(r["outsAfter"])
        post = _int(r["postBases"])
        cls = event_class(r["eventType"])
        if cls is not None:
            bat_side = "A" if half == "T" else "H"
            fld_side = "H" if half == "T" else "A"
            pid = _int(r["pitcherId"], None)
            out.append({
                "gamePk": pk, "inning": inning, "half": half, "batSideTeam": bat_side,
                "batterId": _int(r["batterId"], None), "pitcherId": pid,
                "batHand": r["batSide"], "pitchHand": r["pitchHand"], "cls": cls,
                "outsBefore": outs, "basesBefore": bases, "outsAfter": outs_after, "postBases": post,
                "runs": _int(r["runsOnPlay"]), "rbi": _int(r["rbi"]),
                "vsStarter": int(pid is not None and pid == starters.get(pk, {}).get(fld_side)),
                "scorers": [int(x) for x in r["scorerIds"].split("|") if x],
            })
        outs = outs_after if outs_after < 3 else 0
        bases = post if outs_after < 3 else 0
    return out


# ----------------------------------------------------------------------------------------------
# Transition table
# ----------------------------------------------------------------------------------------------
def _popcount(b):
    return (b & 1) + ((b >> 1) & 1) + ((b >> 2) & 1)


def transition_consistent(p):
    d_outs = p["outsAfter"] - p["outsBefore"]
    if d_outs < 0 or p["outsAfter"] > 3:
        return False
    if p["outsAfter"] == 3:
        # inning over: the feed's post-play bases are empty, stranded runners are not listed
        return d_outs >= 1 and _popcount(p["basesBefore"]) + 1 - p["runs"] - d_outs >= 0
    return _popcount(p["basesBefore"]) + 1 == p["runs"] + d_outs + _popcount(p["postBases"])


def fit_transitions(pas):
    """{(cls, outs, bases): (cum_weights, outcomes[(d_outs, post_bases, runs, rbi)])}."""
    counts = defaultdict(lambda: defaultdict(int))
    kept = dropped = 0
    for p in pas:
        if not transition_consistent(p):
            dropped += 1
            continue
        kept += 1
        d_outs = p["outsAfter"] - p["outsBefore"]
        post = p["postBases"] if p["outsAfter"] < 3 else 0
        counts[(p["cls"], p["outsBefore"], p["basesBefore"])][(d_outs, post, p["runs"], min(p["rbi"], p["runs"]))] += 1
    table = {}
    for k, v in counts.items():
        items = sorted(v.items())
        cum, s = [], 0
        for _o, c in items:
            s += c
            cum.append(s)
        table[k] = (cum, [o for o, _c in items])
    return table, {"kept": kept, "dropped_inconsistent": dropped}


def _fallback_transition(cls, outs, bases):
    """Deterministic textbook advancement used only for a (cls, state) never seen in training."""
    runners = [bool(bases & 1), bool(bases & 2), bool(bases & 4)]
    if cls in ("K", "OUT"):
        return (1, bases if outs + 1 < 3 else 0, 0, 0)
    if cls in ("BB", "HBP"):
        r = 0
        b1, b2, b3 = runners
        if b1:
            if b2:
                if b3:
                    r = 1
                b3 = True
            b2 = True
        b1 = True
        return (0, int(b1) | (int(b2) << 1) | (int(b3) << 2), r, r)
    n = HIT[cls]
    r = 0
    newb = 0
    for i, on in enumerate(runners):
        if on:
            if i + 1 + n > 3:
                r += 1
            else:
                newb |= 1 << (i + n)
    if n == 4:
        r += 1
    else:
        newb |= 1 << (n - 1)
    return (0, newb, r, r)


# ----------------------------------------------------------------------------------------------
# Rate accumulators (as-of)
# ----------------------------------------------------------------------------------------------
def empty_counts():
    return [0] * len(CATS)


def league_rates(pas):
    c = empty_counts()
    for p in pas:
        c[CI[p["cls"]]] += 1
    n = sum(c)
    return [x / n for x in c]


def shrunk_rates(cur, prev, w, m, league):
    """(cur + w*prev + m*league) / (n_cur + w*n_prev + m) per category."""
    ncur = sum(cur) if cur else 0
    nprev = sum(prev) if prev else 0
    denom = ncur + w * nprev + m
    return [((cur[i] if cur else 0) + w * (prev[i] if prev else 0) + m * league[i]) / denom for i in range(len(CATS))]


def log5(batter, pitcher, league):
    raw = [batter[i] * pitcher[i] / league[i] if league[i] > 0 else 0.0 for i in range(len(CATS))]
    s = sum(raw)
    return [x / s for x in raw]


def apply_factor(rates, factor):
    raw = [rates[i] * factor[i] for i in range(len(CATS))]
    s = sum(raw)
    return [x / s for x in raw]


def multinomial_logloss(rates, cls):
    return -math.log(max(1e-12, rates[CI[cls]]))


# ----------------------------------------------------------------------------------------------
# Simulation
# ----------------------------------------------------------------------------------------------
class TeamSpec:
    """Pregame spec for one batting team. rates_vs_starter / rates_vs_bullpen: 9 lists of category
    rates (lineup order). starter_bf_draws: list of possible BF budgets of the OPPOSING starter
    (sampled uniformly). survival[slot][k]: P(starter still batting at slot's k-th PA (0-based))."""

    def __init__(self, player_ids, rates_vs_starter, rates_vs_bullpen, sub_rates, starter_bf_expect,
                 bf_residuals, survival):
        self.player_ids = player_ids
        self.cum_s = [_cum(r) for r in rates_vs_starter]
        self.cum_b = [_cum(r) for r in rates_vs_bullpen]
        self.cum_sub = _cum(sub_rates)
        self.starter_bf_expect = starter_bf_expect
        self.bf_residuals = bf_residuals
        self.survival = survival


def _cum(rates):
    out, s = [], 0.0
    for r in rates:
        s += r
        out.append(s)
    out[-1] = 1.0 + 1e-12
    return out


def _draw_cat(cum, rng):
    return CATS[bisect.bisect_right(cum, rng.random() * cum[-1] * (1 - 1e-12))]


def _sample_transition(table, cls, outs, bases, rng):
    t = table.get((cls, outs, bases))
    if t is None:
        return _fallback_transition(cls, outs, bases)
    cum, outs_list = t
    return outs_list[bisect.bisect_right(cum, rng.random() * cum[-1])]


class _TeamState:
    __slots__ = ("spec", "idx", "bf_vs_starter", "starter_budget", "slot_pa", "subbed", "runs", "lines")

    def __init__(self, spec, rng):
        self.spec = spec
        self.idx = 0
        self.bf_vs_starter = 0
        res = spec.bf_residuals[int(rng.random() * len(spec.bf_residuals))] if spec.bf_residuals else 0.0
        self.starter_budget = max(3, int(round(spec.starter_bf_expect + res)))
        self.slot_pa = [0] * 9
        self.subbed = [False] * 9
        self.runs = 0
        # per starter: PA, H, TB, HR, RBI, R, PA_vs_starter
        self.lines = [[0, 0, 0, 0, 0, 0, 0] for _ in range(9)]


def _half_inning(ts, table, rng, inning, home_batting, need_lead_by=None):
    """Simulates one half inning; returns runs. need_lead_by: for the home team in the 9th+,
    the game ends as soon as home leads (walk-off)."""
    outs = 0
    bases = 2 if inning >= 10 else 0
    # tokens on bases: slot*2 + is_sub (or -1 for the automatic runner, whose identity is the
    # previous batter; credited as the slot before the leadoff batter)
    occ = [None, None, None]
    if inning >= 10:
        prev_slot = (ts.idx - 1) % 9
        occ[1] = prev_slot * 2 + int(ts.subbed[prev_slot])
    runs = 0
    spec = ts.spec
    while outs < 3:
        slot = ts.idx % 9
        k = ts.slot_pa[slot]
        if not ts.subbed[slot] and k > 0:
            s_prev = spec.survival[slot][min(k - 1, len(spec.survival[slot]) - 1)]
            s_now = spec.survival[slot][min(k, len(spec.survival[slot]) - 1)]
            if s_prev > 0 and rng.random() > s_now / s_prev:
                ts.subbed[slot] = True
        is_sub = ts.subbed[slot]
        vs_starter = ts.bf_vs_starter < ts.starter_budget
        if is_sub:
            cls = _draw_cat(spec.cum_sub, rng)
        else:
            cls = _draw_cat(spec.cum_s[slot] if vs_starter else spec.cum_b[slot], rng)
        if vs_starter:
            ts.bf_vs_starter += 1
        d_outs, post, r, rbi = _sample_transition(table, cls, outs, bases, rng)
        # ---- runner identity bookkeeping (lead runners score first) ----
        batter_tok = slot * 2 + int(is_sub)
        order = [occ[2], occ[1], occ[0]]
        order = [t for t in order if t is not None]
        n_before = len(order)
        scorers = order[:r] if r <= n_before else order + [batter_tok]
        remaining = order[r:] if r <= n_before else []
        batter_alive = r <= n_before
        n_out = d_outs
        if cls in ("K", "OUT"):
            if batter_alive and n_out > 0:
                batter_alive = False
                n_out -= 1
            if n_out > 0:
                remaining = remaining[: max(0, len(remaining) - n_out)]
        elif n_out > 0:
            remaining = remaining[n_out:]
        pool = remaining + ([batter_tok] if batter_alive else [])
        occ = [None, None, None]
        if outs + d_outs < 3:
            for base in (2, 1, 0):
                if post & (1 << base) and pool:
                    occ[base] = pool.pop(0)
        # ---- stats ----
        if not is_sub:
            ln = ts.lines[slot]
            ln[0] += 1
            if vs_starter:
                ln[6] += 1
            if cls in HIT:
                ln[1] += 1
                ln[2] += HIT[cls]
                if cls == "HR":
                    ln[3] += 1
            ln[4] += rbi
        for tok in scorers:
            if tok is not None and tok >= 0 and tok % 2 == 0:
                ts.lines[tok // 2][5] += 1
        runs += r
        ts.slot_pa[slot] += 1
        ts.idx += 1
        outs += d_outs
        bases = post if outs < 3 else 0
        if need_lead_by is not None and runs >= need_lead_by:
            break
    return runs


def simulate_game(away, home, table, rng, max_innings=15):
    """One game. Returns (away_state, home_state)."""
    a, h = _TeamState(away, rng), _TeamState(home, rng)
    inning = 1
    while True:
        a.runs += _half_inning(a, table, rng, inning, False)
        if inning >= 9 and h.runs > a.runs:
            break  # home leads after top of 9th+: bottom not played
        if inning >= 9:
            h.runs += _half_inning(h, table, rng, inning, True, need_lead_by=a.runs - h.runs + 1)
        else:
            h.runs += _half_inning(h, table, rng, inning, True)
        if inning >= 9 and a.runs != h.runs:
            break
        if inning >= max_innings:
            break
        inning += 1
    return a, h


def run_game_sims(away, home, table, n_sims, seed):
    """Returns per-team dict with per-slot stat value lists, team runs list, and joint products."""
    rng = random.Random(seed)
    res = {"A": {"lines": [[] for _ in range(9)], "runs": []}, "H": {"lines": [[] for _ in range(9)], "runs": []}}
    for _ in range(n_sims):
        a, h = simulate_game(away, home, table, rng)
        for side, st in (("A", a), ("H", h)):
            res[side]["runs"].append(st.runs)
            for s in range(9):
                ln = st.lines[s]
                res[side]["lines"][s].append((ln[0], ln[1], ln[2], ln[3], ln[4], ln[5], ln[6]))
    return res


def pmf_from_values(vals):
    n = len(vals)
    pmf = defaultdict(float)
    for v in vals:
        pmf[v] += 1.0 / n
    return dict(sorted(pmf.items()))


# ----------------------------------------------------------------------------------------------
# Baseline helpers
# ----------------------------------------------------------------------------------------------
def poisson_pmf(lam, kmax=12):
    out, p = {}, math.exp(-lam)
    for k in range(kmax + 1):
        out[k] = p
        p *= lam / (k + 1)
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}


def binomial_pmf(n, p):
    return {k: math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(n + 1)}


def convolve(a, b):
    out = defaultdict(float)
    for i, pa in a.items():
        for j, pb in b.items():
            out[i + j] += pa * pb
    return dict(out)


def per_pa_tb_pmf(rates):
    """Single-PA total-bases pmf from category rates (dict cat->p)."""
    return {0: 1 - rates["1B"] - rates["2B"] - rates["3B"] - rates["HR"], 1: rates["1B"], 2: rates["2B"],
            3: rates["3B"], 4: rates["HR"]}


def mixture_over_pa(pa_pmf, per_n):
    out = defaultdict(float)
    for n, pn in pa_pmf.items():
        for k, pk in per_n(n).items():
            out[k] += pn * pk
    return dict(sorted(out.items()))


# ----------------------------------------------------------------------------------------------
# Distribution summaries / scoring
# ----------------------------------------------------------------------------------------------
def pmf_mean(pmf):
    return sum(k * p for k, p in pmf.items())


def pmf_quantile(pmf, q):
    s = 0.0
    for k in sorted(pmf):
        s += pmf[k]
        if s >= q - 1e-12:
            return k
    return max(pmf)


def pmf_at_least(pmf, t):
    return sum(p for k, p in pmf.items() if k >= t)


def crps_discrete(pmf, y, kmax=None):
    kmax = kmax if kmax is not None else max(max(pmf), y) + 1
    s, cdf = 0.0, 0.0
    for k in range(0, kmax + 1):
        cdf += pmf.get(k, 0.0)
        s += (cdf - (1.0 if y <= k else 0.0)) ** 2
    return s
