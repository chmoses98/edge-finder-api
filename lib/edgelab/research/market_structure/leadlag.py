"""
Cross-family lead/lag on minute grids (MRV-LL-*).

For two aligned series A (candidate leader) and B (follower) of fair mids,
build rows on a `step`-minute grid for horizon h:
   x = A(t) - A(t-h)     (leader's past move)
   z = B(t) - B(t-h)     (follower's own past move, control)
   y = B(t+h) - B(t)     (follower's next move)
The test statistic is the partial slope of y on x controlling z, pooled
across games with a game-clustered bootstrap.
"""
from lib.edgelab.research.market_structure.stats import partial_slope


def grid_rows(a_mid, b_mid, start_ts, h_min, *, first_before=240, last_before=5, step=5):
    """a_mid/b_mid: {minute_ts: mid}. Returns [(x, z, y)] for grid points inside the pregame window."""
    h = 60 * h_min
    out = []
    t = start_ts - 60 * first_before
    end = start_ts - 60 * last_before
    while t <= end:
        a0, a1 = a_mid.get(t - h), a_mid.get(t)
        b0, b1, b2 = b_mid.get(t - h), b_mid.get(t), b_mid.get(t + h)
        if None not in (a0, a1, b0, b1, b2) and t + h <= start_ts:
            out.append((a1 - a0, b1 - b0, b2 - b1))
        t += 60 * step
    return out


def slope_stat(rows):
    """value_fn for cluster_bootstrap: rows are dicts with x,z,y."""
    return partial_slope([(r["x"], r["z"], r["y"]) for r in rows])


def ladder_implied_mean(rung_mids):
    """
    E[total] approximated by sum over archived rungs of P(total >= N) (each mid /100)
    plus the count of rungs below the lowest archived rung (assumed certain).
    Comparable across time only for a FIXED rung set, which the caller enforces.
    """
    if not rung_mids:
        return None
    lo = min(rung_mids)
    return (lo - 1) + sum(v / 100.0 for v in rung_mids.values())
