"""
lib/edgelab/mlb_alpha/sim.py
============================
One run-environment simulator that prices EVERY game-level Kalshi MLB contract
family from the same simulated games.

WHY ONE SIMULATOR
  Priced separately, the moneyline, run line, total, team total, F5 winner, F5
  total and RFI can contradict each other (a larger team total than game total,
  an F5 lead that implies a negative bullpen). Here they are all functions of the
  same simulated innings, so the full surface is coherent by construction and a
  cross-market disagreement is a real statement about the market, not about two
  unrelated models.

THE MODEL (RunEnvSim v1)
  * Each half-inning is a compound Poisson-geometric process: N ~ Poisson(nu)
    scoring events, each worth 1 + Geometric(q) runs. nu = mu * (1 - q), where mu is
    the half-inning's expected runs. This reproduces the empirical shape of MLB run
    scoring: ~72% scoreless innings, ~2.1x variance-to-mean per team-game, and no
    correlation between the two teams once expectations are conditioned on.
  * mu(team, inning) = lam_team * w_inning, sum(w_1..9) = 1. `lam` is the team's
    expected runs for nine batted innings. Opponent adjustment, starter, bullpen,
    park and weather enter ONLY through lam (and optionally the inning weights, e.g.
    a team with a short starter shifts weight to late innings).
  * Bottom of the 9th and extras are real rules, not approximations: the home team
    does not bat if leading after the top of the 9th, a walk-off stops at the
    scoring event that takes the lead (so the winning margin and the home run
    total are truncated exactly as in the box score), and extra innings start with
    a runner on second.
  * Common random numbers: the uniform draws behind every count are fixed per
    `Draws` object, so outputs are smooth in lam and solvers converge.

WHAT IT IS NOT
  Not a player-level model. Player props need a lineup/PA model (existing hitter
  engine) and are listed as unpriced, never approximated here.
  Not tuned to any price. Structural constants are fitted to 2022-2025 box scores
  (see scripts/research/mlb_alpha_0003/calibrate_sim.py); lam comes from a model or
  a market, and which is a research input, not a property of this module.

numpy is imported lazily so stdlib-only layers stay importable in CI.
"""

import math
from dataclasses import dataclass, field

KMAX = 8                    # scoring events per half inning kept (P(N>8) < 1e-6 for nu<=1)
MAX_EXTRA = 4               # extra innings simulated explicitly (10..13), then coin flip
DEFAULT_Q = 0.36
DEFAULT_WEIGHTS = (0.118, 0.112, 0.112, 0.111, 0.107, 0.110, 0.111, 0.111, 0.108)


@dataclass(frozen=True)
class SimParams:
    q: float = DEFAULT_Q                    # P(an extra run joins a scoring event)
    weights: tuple = DEFAULT_WEIGHTS        # inning weights, sum 1
    extra_nu: float = 0.92                  # Poisson mean of scoring events, one extra half inning
    extra_q: float = 0.20
    version: str = "RunEnvSim_v1"

    def normalised_weights(self):
        s = sum(self.weights)
        return tuple(w / s for w in self.weights)


class Draws:
    """Fixed random inputs: reuse across lam values for smooth, comparable outputs."""

    def __init__(self, n=100_000, seed=20261027, params=SimParams()):
        import numpy as np
        rng = np.random.default_rng(seed)
        self.n, self.seed, self.params = n, seed, params
        self.u = rng.random((n, 2, 9))                              # Poisson-count uniforms
        # cumulative runs after k scoring events (k = 0..KMAX): sizes 1 + Geometric(q)
        extra = np.floor(np.log(rng.random((n, 2, 9, KMAX))) / math.log(params.q)).astype(np.int16)
        cum = np.zeros((n, 2, 9, KMAX + 1), dtype=np.int16)
        cum[..., 1:] = np.cumsum(1 + extra, axis=-1)
        self.cum = cum
        # extras: independent uniforms and sizes per extra half-inning
        self.ux = rng.random((n, MAX_EXTRA, 2))
        exs = np.floor(np.log(rng.random((n, MAX_EXTRA, 2, KMAX))) / math.log(params.extra_q)).astype(np.int16)
        cx = np.zeros((n, MAX_EXTRA, 2, KMAX + 1), dtype=np.int16)
        cx[..., 1:] = np.cumsum(1 + exs, axis=-1)
        self.cumx = cx
        self.coin = rng.random(n)


def _poisson_counts(u, nu, kmax=KMAX):
    """Inverse-CDF Poisson counts from fixed uniforms (monotone in nu).
    `nu` may be a scalar or an array broadcast against `u` (one nu per simulated game)."""
    import numpy as np
    if np.ndim(nu) == 0:
        pmf = [math.exp(-float(nu))]
        for k in range(1, kmax + 1):
            pmf.append(pmf[-1] * float(nu) / k)
        cdf = np.cumsum(pmf)
        return np.minimum(np.searchsorted(cdf, u, side="left"), kmax)
    nu = np.asarray(nu, dtype=float)
    pmf = np.empty((nu.shape[0], kmax + 1))
    pmf[:, 0] = np.exp(-nu)
    for k in range(1, kmax + 1):
        pmf[:, k] = pmf[:, k - 1] * nu / k
    cdf = np.cumsum(pmf, axis=1)
    return np.minimum((u[:, None] > cdf).sum(axis=1), kmax)


@dataclass
class SimResult:
    away: object                 # (n, 13) runs by inning (away batting)
    home: object                 # (n, 13) runs by inning (home batting; 0 where not batted)
    home_batted: object          # (n, 13) bool
    extras: object               # (n,) bool: game went past 9 innings
    params: SimParams = field(default_factory=SimParams)
    lam: tuple = (None, None)

    @property
    def n(self):
        return self.away.shape[0]

    def away_runs(self, through=13):
        return self.away[:, :through].sum(axis=1)

    def home_runs(self, through=13):
        return self.home[:, :through].sum(axis=1)

    def home_win(self):
        return self.home.sum(axis=1) > self.away.sum(axis=1)


def run(lam_away, lam_home, draws, weights=None, weights_home=None):
    """Simulate `draws.n` games for expected nine-inning runs (lam_away, lam_home);
    each may be a scalar (one game, many simulations) or an (n,) array (a league mix).
    weights / weights_home override the inning weights per team (length 9)."""
    import numpy as np
    p = draws.params
    n = draws.n
    wa = tuple(weights) if weights is not None else p.normalised_weights()
    wh = tuple(weights_home) if weights_home is not None else (tuple(weights) if weights is not None else p.normalised_weights())
    wa = np.array(wa) / sum(wa)
    wh = np.array(wh) / sum(wh)
    away = np.zeros((n, 13), dtype=np.int16)
    home = np.zeros((n, 13), dtype=np.int16)
    batted = np.zeros((n, 13), dtype=bool)
    batted[:, :8] = True
    lam = (np.asarray(lam_away, dtype=float) if np.ndim(lam_away) else float(lam_away),
           np.asarray(lam_home, dtype=float) if np.ndim(lam_home) else float(lam_home))
    wt = (wa, wh)
    idx = np.arange(n)
    # regulation innings: every inning but the home 9th is independent of the score
    for t, arr in ((0, away), (1, home)):
        for i in range(9):
            if t == 1 and i == 8:
                continue
            mu = lam[t] * wt[t][i]
            nu = mu * (1.0 - p.q)
            counts = _poisson_counts(draws.u[:, t, i], nu)
            arr[:, i] = draws.cum[idx, t, i, counts]
    # bottom of the 9th: skipped when home leads, stops at the lead-taking event
    a9 = away[:, :9].sum(axis=1)
    h8 = home[:, :8].sum(axis=1)
    need = a9 - h8                          # runs home must exceed; < 0 -> home already leads
    bats9 = need >= 0
    mu9 = lam[1] * wt[1][8]
    counts9 = _poisson_counts(draws.u[:, 1, 8], mu9 * (1.0 - p.q))
    cum9 = draws.cum[:, 1, 8, :]            # (n, KMAX+1)
    ks = np.arange(KMAX + 1)[None, :]
    ok = (cum9 > need[:, None]) & (ks <= counts9[:, None]) & (ks >= 1)
    first = np.where(ok.any(axis=1), ok.argmax(axis=1), -1)
    ended = first >= 0
    r9 = np.where(ended, cum9[idx, np.clip(first, 0, KMAX)], cum9[idx, counts9])
    home[:, 8] = np.where(bats9, r9, 0)
    batted[:, 8] = bats9
    # extras
    tied = home.sum(axis=1) == away.sum(axis=1)
    extras = tied.copy()
    sub = np.where(tied)[0]
    if sub.size:
        a_tot = away[sub].sum(axis=1).astype(np.int32)
        h_tot = home[sub].sum(axis=1).astype(np.int32)
        alive = np.ones(sub.size, dtype=bool)
        nu_ex = p.extra_nu
        for e in range(MAX_EXTRA):
            if not alive.any():
                break
            ca = _poisson_counts(draws.ux[sub, e, 0], nu_ex)
            ra = draws.cumx[sub, e, 0, :][np.arange(sub.size), ca]
            away[sub[alive], 9 + e] = ra[alive]
            a_tot = a_tot + np.where(alive, ra, 0)
            need_e = a_tot - h_tot
            ch = _poisson_counts(draws.ux[sub, e, 1], nu_ex)
            cumh = draws.cumx[sub, e, 1, :]
            ok = (cumh > need_e[:, None]) & (ks <= ch[:, None]) & (ks >= 1)
            first = np.where(ok.any(axis=1), ok.argmax(axis=1), -1)
            rh = np.where(first >= 0, cumh[np.arange(sub.size), np.clip(first, 0, KMAX)], cumh[np.arange(sub.size), ch])
            home[sub[alive], 9 + e] = rh[alive]
            batted[sub[alive], 9 + e] = True
            h_tot = h_tot + np.where(alive, rh, 0)
            alive = alive & (h_tot == a_tot)
        # still tied after the explicit extras: break the tie by the stored coin (runs unchanged)
        still = sub[alive]
        if still.size:
            win_home = draws.coin[still] < 0.5
            home[still, 9 + MAX_EXTRA - 1] += win_home.astype(np.int16)
            away[still, 9 + MAX_EXTRA - 1] += (~win_home).astype(np.int16)
    return SimResult(away=away, home=home, home_batted=batted, extras=extras, params=p, lam=lam)
