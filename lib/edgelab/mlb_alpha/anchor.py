"""
lib/edgelab/mlb_alpha/anchor.py
===============================
Recover the simulator's run environment (lam_away, lam_home) from market
information, so the simulator can price every OTHER contract coherently.

An anchor is a vig-free pair of statements about the same game:
    p_home   probability the home team wins (moneyline)
    total    a total line T and p_over, the vig-free probability of Over at T
             (half lines: P(R >= ceil(T)); integer lines: P(R > T)/(P(R > T)+P(R < T)),
              because the book's push removes the middle)
Two equations, two unknowns. The map (lam_away, lam_home) -> (p_home, p_over) is
tabulated once on a grid (`Surface`) so each game costs an interpolation, not a
fresh simulation.

The anchor source (Pinnacle only, a multi-book consensus, or Kalshi's own
moneyline/total ladder) is a research input chosen by the caller and recorded with
the result; this module does not prefer one.
"""

import math


class Surface:
    """Tabulated p_home(m, r) and P(total >= k)(m, r) with m = lam_a + lam_h, r = lam_h / lam_a."""

    def __init__(self, ms, rs, p_home, p_ge, params_version):
        self.ms, self.rs = list(ms), list(rs)
        self.p_home = p_home          # [i_m][i_r]
        self.p_ge = p_ge              # [i_m][i_r][k] k = 0..KMAX_TOTAL
        self.version = params_version

    @classmethod
    def build(cls, draws, ms, rs, kmax=40):
        import numpy as np
        from lib.edgelab.mlb_alpha import sim
        ph = np.zeros((len(ms), len(rs)))
        pg = np.zeros((len(ms), len(rs), kmax + 1))
        for i, m in enumerate(ms):
            for j, r in enumerate(rs):
                la = m / (1.0 + r)
                lh = m - la
                res = sim.run(la, lh, draws)
                a, h = res.away.sum(axis=1), res.home.sum(axis=1)
                ph[i, j] = (h > a).mean()
                t = a + h
                for k in range(kmax + 1):
                    pg[i, j, k] = (t >= k).mean()
        return cls(ms, rs, ph, pg, draws.params.version)

    def to_json(self):
        import numpy as np
        return {"ms": self.ms, "rs": self.rs, "pHome": np.asarray(self.p_home).tolist(),
                "pGe": np.asarray(self.p_ge).tolist(), "version": self.version}

    @classmethod
    def from_json(cls, d):
        import numpy as np
        return cls(d["ms"], d["rs"], np.array(d["pHome"]).tolist(), np.array(d["pGe"]).tolist(), d.get("version"))

    # --- interpolation -----------------------------------------------------
    def _locate(self, axis, x):
        n = len(axis)
        if x <= axis[0]:
            return 0, 0.0
        if x >= axis[-1]:
            return n - 2, 1.0
        lo, hi = 0, n - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if axis[mid] <= x:
                lo = mid
            else:
                hi = mid
        return lo, (x - axis[lo]) / (axis[hi] - axis[lo])

    def _bilinear(self, grid, m, r):
        i, fm = self._locate(self.ms, m)
        j, fr = self._locate(self.rs, r)
        return ((1 - fm) * (1 - fr) * grid[i][j] + fm * (1 - fr) * grid[i + 1][j]
                + (1 - fm) * fr * grid[i][j + 1] + fm * fr * grid[i + 1][j + 1])

    def p_home_at(self, m, r):
        return float(self._bilinear(self.p_home, m, r))

    def p_ge_at(self, m, r, k):
        k = max(0, min(int(k), len(self.p_ge[0][0]) - 1))
        i, fm = self._locate(self.ms, m)
        j, fr = self._locate(self.rs, r)
        g = self.p_ge
        return float((1 - fm) * (1 - fr) * g[i][j][k] + fm * (1 - fr) * g[i + 1][j][k]
                     + (1 - fm) * fr * g[i][j + 1][k] + fm * fr * g[i + 1][j + 1][k])

    def p_over_at(self, m, r, line):
        """Model counterpart of the book's no-vig Over probability at `line`."""
        if abs(line - round(line)) < 1e-9:
            t = int(round(line))
            over = self.p_ge_at(m, r, t + 1)
            under = 1.0 - self.p_ge_at(m, r, t)
            return over / (over + under) if over + under > 0 else float("nan")
        return self.p_ge_at(m, r, int(math.ceil(line)))


def solve(surface, p_home, line, p_over, iters=60):
    """Return (lam_away, lam_home, residuals) matching (p_home, p_over@line), or None if outside the grid."""
    if not (0.05 < p_home < 0.95 and 0.02 < p_over < 0.98):
        return None
    m, r = 8.8, 1.0
    for _ in range(iters):
        # r from the moneyline at current m (monotone increasing in r)
        lo, hi = surface.rs[0], surface.rs[-1]
        if (surface.p_home_at(m, lo) - p_home) * (surface.p_home_at(m, hi) - p_home) > 0:
            return None
        for _b in range(40):
            mid = 0.5 * (lo + hi)
            if surface.p_home_at(m, mid) < p_home:
                lo = mid
            else:
                hi = mid
        r = 0.5 * (lo + hi)
        # m from the total at current r (over probability increases with m)
        lo, hi = surface.ms[0], surface.ms[-1]
        if (surface.p_over_at(lo, r, line) - p_over) * (surface.p_over_at(hi, r, line) - p_over) > 0:
            return None
        for _b in range(40):
            mid = 0.5 * (lo + hi)
            if surface.p_over_at(mid, r, line) < p_over:
                lo = mid
            else:
                hi = mid
        m_new = 0.5 * (lo + hi)
        if abs(m_new - m) < 1e-6:
            m = m_new
            break
        m = m_new
    la = m / (1.0 + r)
    lh = m - la
    res = (surface.p_home_at(m, r) - p_home, surface.p_over_at(m, r, line) - p_over)
    return la, lh, res
