#!/usr/bin/env python3
"""
lib/research/hitter_v2_engine.py
================================
SIFT MLB hitter lane, V2 challenger -- RESEARCH ONLY. Not wired into any
production path, app export, publication, staking, authority or bet routing.

Model name: MLB_HITTER_V2_PURE (preregistered in docs/research/HITTER_V2_PREREG.md).

This module is the two-team game simulator of the V2 challenger. It extends the
frozen W2 simulator (lib/research/hitter_w2_engine.py, which is imported and
NOT modified) with the structural fixes the W2 audit / results left open, in
the preregistered priority order:

  1. PA count
     a. state-dependent substitution hazard: P(the slot's starter is replaced
        before this PA | he took the slot's previous PA) depends on the slot,
        the slot's PA ordinal, the inning and the batting team's run margin,
        times a shrunk per-batter removal propensity (W2: one curve by slot x
        PA ordinal, blind to score and to the batter);
     b. inning-ending runner outs that are not plate appearances (caught
        stealing / pickoff for the 3rd out): before a PA with 2 outs and
        runners on, the inning ends with the empirical probability q(bases)
        and the batter leads off the next inning (W2: never happens);
     c. home team skips the bottom of the 9th when leading, walk-off, extra
        innings with the automatic runner (kept from W2).
  2. walk-rate calibration, 3. platoon splits, 4. bullpen quality: these
     enter through the per-slot category rates built in
     scripts/research/hitter_v2/model_v2.py (this module only consumes rates).
  5. baserunner advancement: empirical (event, outs, bases) transition table
     (kept from W2) -- see the prereg for why runner speed is not added.

Each draw is coherent: TB >= H >= HR per player, team RBI <= team runs, and
the sum of R over the batting team (starters + substitutes) equals team runs.
Sports data only: no odds, totals, Kalshi quotes, settlements or
market-selected populations are read anywhere in this module.
"""
import random

from lib.research import hitter_w2_engine as W2

CATS = W2.CATS
HIT = W2.HIT
# V2 projects these per starter (HRR = H + R + RBI from the same draw).
STATS = ("PA", "H", "TB", "HR", "RBI", "R", "BB", "K", "HRR")

INNING_BUCKETS = (4, 5, 6, 7, 8, 9)       # <=4, 5, 6, 7, 8, >=9
MARGIN_EDGES = (-5, -3, -1, 0, 1, 3, 5)   # buckets: <=-5, -4..-3, -2..-1, 0, 1..2, 3..4, >=5


def inning_bucket(inning):
    if inning <= 4:
        return 0
    return min(inning, 9) - 4


def margin_bucket(m):
    """Batting team runs minus fielding team runs -> 0..6."""
    if m <= -5:
        return 0
    if m <= -3:
        return 1
    if m <= -1:
        return 2
    if m == 0:
        return 3
    if m <= 2:
        return 4
    if m <= 4:
        return 5
    return 6


def k_bucket(k):
    """The slot's PA ordinal (0-based) of the PA at risk; k >= 1 only."""
    return min(k, 4) - 1


class TeamSpecV2:
    """Pregame spec for one batting team.

    rates_vs_starter / rates_vs_bullpen: 9 category-rate lists (lineup order).
    sub_rates: category rates of a substitute batter.
    hazard[slot][(kb, ib, mb)]: probability the slot's starter is replaced before the PA at risk,
        already including the batter's propensity.
    runner_out_q[bases]: P(inning ends on a non-PA runner out | 2 outs, bases) checked before a PA.
    starter_bf_expect / bf_residuals: opposing starter's batters-faced budget."""

    def __init__(self, player_ids, rates_vs_starter, rates_vs_bullpen, sub_rates, hazard, runner_out_q,
                 starter_bf_expect, bf_residuals):
        self.player_ids = player_ids
        self.cum_s = [W2._cum(r) for r in rates_vs_starter]
        self.cum_b = [W2._cum(r) for r in rates_vs_bullpen]
        self.cum_sub = W2._cum(sub_rates)
        self.hazard = hazard
        self.runner_out_q = runner_out_q
        self.starter_bf_expect = starter_bf_expect
        self.bf_residuals = bf_residuals


class _State:
    __slots__ = ("spec", "idx", "bf_vs_starter", "starter_budget", "slot_pa", "subbed", "runs", "lines",
                 "sub_runs")

    def __init__(self, spec, rng):
        self.spec = spec
        self.idx = 0
        self.bf_vs_starter = 0
        res = spec.bf_residuals[int(rng.random() * len(spec.bf_residuals))] if spec.bf_residuals else 0.0
        self.starter_budget = max(3, int(round(spec.starter_bf_expect + res)))
        self.slot_pa = [0] * 9
        self.subbed = [False] * 9
        self.runs = 0
        self.sub_runs = 0
        # per starter: PA, H, TB, HR, RBI, R, BB, K, PA_vs_starter, replaced (0/1, set at game end)
        self.lines = [[0] * 10 for _ in range(9)]


def _half_inning(ts, opp_runs, table, rng, inning, walkoff_need=None):
    outs = 0
    bases = 2 if inning >= 10 else 0
    occ = [None, None, None]
    if inning >= 10:
        prev_slot = (ts.idx - 1) % 9
        occ[1] = prev_slot * 2 + int(ts.subbed[prev_slot])
    runs = 0
    spec = ts.spec
    ib = inning_bucket(inning)
    while outs < 3:
        # (1b) inning-ending runner out before the PA (2 outs, runners on)
        if outs == 2 and bases and rng.random() < spec.runner_out_q[bases]:
            break
        slot = ts.idx % 9
        k = ts.slot_pa[slot]
        # (1a) state-dependent substitution hazard
        if not ts.subbed[slot] and k > 0:
            mb = margin_bucket(ts.runs + runs - opp_runs)
            if rng.random() < spec.hazard[slot][(k_bucket(k), ib, mb)]:
                ts.subbed[slot] = True
        is_sub = ts.subbed[slot]
        vs_starter = ts.bf_vs_starter < ts.starter_budget
        if is_sub:
            cls = W2._draw_cat(spec.cum_sub, rng)
        else:
            cls = W2._draw_cat(spec.cum_s[slot] if vs_starter else spec.cum_b[slot], rng)
        if vs_starter:
            ts.bf_vs_starter += 1
        d_outs, post, r, rbi = W2._sample_transition(table, cls, outs, bases, rng)
        # runner identity bookkeeping (lead runners score first) -- identical to W2
        batter_tok = slot * 2 + int(is_sub)
        order = [t for t in (occ[2], occ[1], occ[0]) if t is not None]
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
        if not is_sub:
            ln = ts.lines[slot]
            ln[0] += 1
            if vs_starter:
                ln[8] += 1
            if cls in HIT:
                ln[1] += 1
                ln[2] += HIT[cls]
                if cls == "HR":
                    ln[3] += 1
            elif cls == "BB":
                ln[6] += 1
            elif cls == "K":
                ln[7] += 1
            ln[4] += rbi
        for tok in scorers:
            if tok is not None and tok >= 0:
                if tok % 2 == 0:
                    ts.lines[tok // 2][5] += 1
                else:
                    ts.sub_runs += 1
        runs += r
        ts.slot_pa[slot] += 1
        ts.idx += 1
        outs += d_outs
        bases = post if outs < 3 else 0
        if walkoff_need is not None and runs >= walkoff_need:
            break
    return runs


def simulate_game(away, home, table, rng, max_innings=15):
    a, h = _State(away, rng), _State(home, rng)
    inning = 1
    while True:
        a.runs += _half_inning(a, h.runs, table, rng, inning)
        if inning >= 9 and h.runs > a.runs:
            break  # home leads after the top of the 9th+: bottom not played
        need = a.runs - h.runs + 1 if inning >= 9 else None
        h.runs += _half_inning(h, a.runs, table, rng, inning, walkoff_need=need)
        if inning >= 9 and a.runs != h.runs:
            break
        if inning >= max_innings:
            break
        inning += 1
    return a, h


def run_game_sims(away, home, table, n_sims, seed):
    """Per side: per-slot list of stat tuples (PA,H,TB,HR,RBI,R,BB,K,PA_vs_sp,replaced), team runs, sub runs."""
    rng = random.Random(seed)
    res = {s: {"lines": [[] for _ in range(9)], "runs": [], "sub_runs": []} for s in ("A", "H")}
    for _ in range(n_sims):
        a, h = simulate_game(away, home, table, rng)
        for side, st in (("A", a), ("H", h)):
            res[side]["runs"].append(st.runs)
            res[side]["sub_runs"].append(st.sub_runs)
            for s in range(9):
                st.lines[s][9] = int(st.subbed[s])
                res[side]["lines"][s].append(tuple(st.lines[s]))
    return res


def stat_values(lines):
    """{stat: [value per draw]} for one slot, incl. HRR from the same draw."""
    out = {k: [v[j] for v in lines] for j, k in enumerate(("PA", "H", "TB", "HR", "RBI", "R", "BB", "K"))}
    out["HRR"] = [v[1] + v[5] + v[4] for v in lines]
    return out

