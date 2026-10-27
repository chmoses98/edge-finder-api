"""MLB-ALPHA-0003: simulator invariants, cross-family coherence, anchor round trip."""
import gzip
import json
import os

import pytest

np = pytest.importorskip("numpy")

from lib.edgelab.mlb_alpha import anchor, pricer, sim, taxonomy as tx  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SURFACE = os.path.join(REPO, "data/edgelab/research_artifacts/mlb_alpha_0003/anchor_surface_v1.json.gz")
EV = "26OCT152000MILLAD"


@pytest.fixture(scope="module")
def draws():
    return sim.Draws(n=60_000, seed=11)


@pytest.fixture(scope="module")
def base(draws):
    return sim.run(4.0, 4.8, draws)


def test_scalar_and_array_lam_agree(draws):
    a = sim.run(4.4, 4.6, draws)
    b = sim.run(np.full(draws.n, 4.4), np.full(draws.n, 4.6), draws)
    assert (a.away == b.away).all() and (a.home == b.home).all()


def test_deterministic_given_seed():
    d1, d2 = sim.Draws(n=5000, seed=3), sim.Draws(n=5000, seed=3)
    r1, r2 = sim.run(4.0, 4.0, d1), sim.run(4.0, 4.0, d2)
    assert (r1.away == r2.away).all() and (r1.home == r2.home).all()


def test_no_ties_and_walkoff_rules(base):
    A, H = base.away.sum(axis=1), base.home.sum(axis=1)
    assert (A != H).all()                                   # baseball has no ties
    h8, a9 = base.home[:, :8].sum(axis=1), base.away[:, :9].sum(axis=1)
    # home bats the 9th iff it is not already ahead
    assert (base.home_batted[:, 8] == (h8 <= a9)).all()
    assert (base.home[~base.home_batted[:, 8], 8] == 0).all()
    # about 1 game in 11 goes to extras with league-average teams (long-run MLB: 8.7%)
    assert 0.07 < base.extras.mean() < 0.13


def test_monotone_in_lambda(draws):
    lo, hi = sim.run(4.0, 4.0, draws), sim.run(4.0, 5.0, draws)
    assert hi.home_win().mean() > lo.home_win().mean()
    assert (hi.home.sum(1) + hi.away.sum(1)).mean() > (lo.home.sum(1) + lo.away.sum(1)).mean()


def test_scoreless_inning_rate_is_realistic(draws):
    r = sim.run(4.45, 4.45, draws)
    assert 0.70 < (r.away[:, :8] == 0).mean() < 0.76        # MLB: ~72-73% of half innings are scoreless


def _p(r, ticker):
    out = pricer.probability(r, tx.parse_market_ticker(ticker))
    assert out["status"] in (pricer.PRICED, pricer.PRICED_RULE_UNVERIFIED), out
    return out["p"]


def test_cross_family_coherence_by_construction(base):
    # three-way period result sums to one
    for s in ("KXMLBF3", "KXMLBF5", "KXMLBF7"):
        tot = sum(_p(base, "%s-%s-%s" % (s, EV, x)) for x in ("MIL", "LAD", "TIE"))
        assert abs(tot - 1.0) < 1e-9
    # moneyline sums to one
    assert abs(_p(base, "KXMLBGAME-%s-MIL" % EV) + _p(base, "KXMLBGAME-%s-LAD" % EV) - 1.0) < 1e-9
    # ladders are monotone
    for s, n0 in (("KXMLBTOTAL", 5), ("KXMLBF5TOTAL", 2)):
        ps = [_p(base, "%s-%s-%d" % (s, EV, n)) for n in range(n0, n0 + 8)]
        assert all(a >= b for a, b in zip(ps, ps[1:]))
    # a team total never exceeds the game total at the same rung
    for n in range(2, 9):
        assert _p(base, "KXMLBTEAMTOTAL-%s-LAD%d" % (EV, n)) <= _p(base, "KXMLBTOTAL-%s-%d" % (EV, n)) + 1e-12
    # winning by >= 2 implies winning
    assert _p(base, "KXMLBSPREAD-%s-LAD2" % EV) <= _p(base, "KXMLBGAME-%s-LAD" % EV) + 1e-12
    # F5 total >= N implies game total >= N (period is a subset of the game)
    for n in range(2, 8):
        assert _p(base, "KXMLBF5TOTAL-%s-%d" % (EV, n)) <= _p(base, "KXMLBTOTAL-%s-%d" % (EV, n)) + 1e-12


def test_unpriceable_contracts_are_flagged_not_fabricated(base):
    for t in ("KXMLBHIT-%s-MILWCONTRERAS24-2" % EV, "KXMLBINNINGWIN-%s-9-TIE" % EV, "KXMLBINNINGTOTAL-%s-9-1" % EV):
        out = pricer.probability(base, tx.parse_market_ticker(t))
        assert out["p"] is None and out["status"] == pricer.UNSUPPORTED
    out = pricer.probability(base, tx.parse_market_ticker("KXMLBINNINGWIN-%s-3-TIE" % EV))
    assert out["status"] == pricer.PRICED_RULE_UNVERIFIED


@pytest.fixture(scope="module")
def surface():
    return anchor.Surface.from_json(json.load(gzip.open(SURFACE, "rt")))


@pytest.mark.parametrize("p_home,line,p_over", [(0.58, 8.5, 0.50), (0.40, 7.5, 0.47), (0.50, 10.5, 0.46), (0.62, 9.0, 0.52)])
def test_anchor_round_trip(surface, p_home, line, p_over):
    la, lh, resid = anchor.solve(surface, p_home, line, p_over)
    assert max(abs(resid[0]), abs(resid[1])) < 5e-3
    r = sim.run(la, lh, sim.Draws(n=150_000, seed=5))
    A, H = r.away.sum(axis=1), r.home.sum(axis=1)
    T = A + H
    assert abs((H > A).mean() - p_home) < 0.012
    if float(line).is_integer():
        over = (T > line).mean() / ((T > line).mean() + (T < line).mean())
    else:
        over = (T > line).mean()
    assert abs(over - p_over) < 0.012


def test_anchor_refuses_degenerate_inputs(surface):
    assert anchor.solve(surface, 0.99, 8.5, 0.5) is None
    assert anchor.solve(surface, 0.5, 8.5, 0.999) is None
