#!/usr/bin/env python3
"""
tests/research/test_hitter_v2_engine.py
=======================================
SIFT MLB hitter V2 lane (research only): targeted tests for
lib/research/hitter_v2_engine.py and scripts/research/hitter_v2/
(MLB_HITTER_V2_PURE). Synthetic inputs, plus as-of / W2-compatibility checks on
the committed 2024-2025 feed extracts.
"""
import gzip
import csv
import os
import random
import re
import sys
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (ROOT, os.path.join(ROOT, "scripts", "research", "hitter_v2"),
           os.path.join(ROOT, "scripts", "research", "hitter_w2")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.research import hitter_v2_engine as V  # noqa: E402
from lib.research import hitter_w2_engine as W2  # noqa: E402

LEAGUE = [0.225, 0.085, 0.010, 0.145, 0.045, 0.004, 0.032, 0.454]
FEED = os.path.join(ROOT, "data", "research", "hitter_w2", "mlb_feed")


def _haz(p):
    return [{(kb, ib, mb): p for kb in range(4) for ib in range(6) for mb in range(7)} for _ in range(9)]


def _spec(hazard=0.0, runner_out=0.0, rates=None):
    r = [list(rates or LEAGUE)] * 9
    return V.TeamSpecV2(list(range(9)), r, r, list(LEAGUE), _haz(hazard), [runner_out] * 8, 22.0, [0.0])


def _sim(n, **kw):
    out = []
    for i in range(n):
        out.append(V.simulate_game(_spec(**kw), _spec(**kw), {}, random.Random(i)))
    return out


def test_draws_are_coherent_and_runs_reconcile_with_substitutes():
    for a, h in _sim(300, hazard=0.2):
        for st in (a, h):
            assert sum(ln[5] for ln in st.lines) + st.sub_runs == st.runs
            assert sum(ln[4] for ln in st.lines) <= st.runs
            for ln in st.lines:
                assert ln[2] >= ln[1] >= ln[3]
                assert ln[6] + ln[7] + ln[1] <= ln[0]  # BB + K + H <= PA
                assert ln[8] <= ln[0]


def test_home_team_skips_bottom_ninth_when_leading():
    games = _sim(400)
    away = sum(sum(ln[0] for ln in a.lines) for a, _h in games)
    home = sum(sum(ln[0] for ln in h.lines) for _a, h in games)
    assert home < away
    assert all(a.runs != h.runs for a, h in games if True)  # no ties within 15 innings for these rates


def test_substitution_hazard_reduces_starter_pa():
    none = sum(sum(ln[0] for ln in a.lines) for a, _h in _sim(200, hazard=0.0))
    some = sum(sum(ln[0] for ln in a.lines) for a, _h in _sim(200, hazard=0.3))
    assert some < none
    # hazard 0 -> nobody is ever substituted
    assert all(not any(a.subbed) for a, _h in _sim(50, hazard=0.0))


def test_runner_out_events_reduce_team_pa():
    base = sum(sum(ln[0] for ln in a.lines) for a, _h in _sim(200))
    ro = sum(sum(ln[0] for ln in a.lines) for a, _h in _sim(200, runner_out=0.5))
    assert ro < base


def test_buckets():
    assert [V.inning_bucket(i) for i in (1, 4, 5, 6, 7, 8, 9, 12)] == [0, 0, 1, 2, 3, 4, 5, 5]
    assert [V.margin_bucket(m) for m in (-9, -5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 9)] == \
        [0, 0, 1, 1, 2, 2, 3, 4, 4, 5, 5, 6, 6]
    assert [V.k_bucket(k) for k in (1, 2, 3, 4, 7)] == [0, 1, 2, 3, 3]


def test_stat_values_hrr_from_same_draw():
    lines = [(4, 2, 5, 1, 3, 1, 0, 1, 2), (3, 0, 0, 0, 0, 0, 1, 2, 3)]
    sv = V.stat_values(lines)
    assert sv["HRR"] == [6, 0] and sv["BB"] == [0, 1] and sv["K"] == [1, 2]


def test_eff_hand_switch_hitters():
    import data_v2 as D
    assert D.eff_hand("S", "L") == "R" and D.eff_hand("S", "R") == "L"
    assert D.eff_hand("L", "L") == "L" and D.eff_hand("R", "L") == "R"


def test_scoring_crps_equals_full_ladder_brier_sum():
    pytest.importorskip("numpy")               # analysis modules need numpy; CI is numpy-free
    import scoring_v2 as S
    rng = random.Random(0)
    pmf = {0: 0.3, 1: 0.4, 2: 0.2, 3: 0.1}
    for y in range(5):
        m = S.row_metrics(pmf, y, "H", rng)
        # RPS over k = 0..kmax of (F(k) - 1{y<=k})^2 = sum over rungs t>=1 of (P(X>=t) - 1{y>=t})^2
        ref = sum((sum(v for k, v in pmf.items() if k >= t) - (1.0 if y >= t else 0.0)) ** 2 for t in range(1, 6))
        assert abs(m["crps"] - ref) < 1e-12
    assert S.row_metrics({2: 1.0}, 2, "H", rng)["crps"] == 0.0
    assert S.row_metrics(pmf, 1, "H", rng)["abs_med"] == 0


def test_hazard_logistic_recovers_rates():
    pytest.importorskip("numpy")               # analysis modules need numpy; CI is numpy-free
    import model_v2 as MV
    rng = random.Random(1)
    rows = []
    for _ in range(20000):
        inning = rng.choice((3, 7, 9))
        margin = rng.choice((0, 6))
        p = 0.01 if inning < 7 else (0.05 if margin == 0 else 0.25)
        rows.append((1, "A", 3, 7, 2, inning, margin, int(rng.random() < p), "2025-06-01"))
    fit = MV.fit_hazard([rows])
    assert abs(MV.hazard_prob(fit["beta"], 3, 2, 9, 6) - 0.25) < 0.03
    assert abs(MV.hazard_prob(fit["beta"], 3, 2, 3, 0) - 0.01) < 0.01


def _strip(src):
    code = "\n".join(line for line in src.splitlines() if not line.strip().startswith("#"))
    return re.sub(r'"""[\s\S]*?"""', "", code).lower()


def test_v2_code_reads_no_market_vocabulary():
    files = [os.path.join(ROOT, "lib", "research", "hitter_v2_engine.py")] + [
        os.path.join(ROOT, "scripts", "research", "hitter_v2", f) for f in ("data_v2.py", "model_v2.py", "scoring_v2.py")]
    for path in files:
        code = _strip(open(path).read())
        for term in ("kalshi", "odds", "implied", "sportsbook", "pinnacle", "settle", "price", "ticker", "quote",
                     "teamtotals", "vegas", "moneyline", "totaleval", "modelprob", "wager"):
            assert term not in code, (path, term)


def test_pure_forecast_path_opens_market_file_only_in_negative_control():
    src = open(os.path.join(ROOT, "scripts", "research", "hitter_v2", "run_v2.py")).read()
    body = src[src.index("def stage_forecast"):src.index("def _control_cfg")]
    assert body.count("open(market_file)") == 1
    assert "if control_read_market:" in body


class _Spy(dict):
    def __init__(self, d, log, prefix=""):
        super().__init__(d)
        self._log, self._prefix = log, prefix

    def get(self, k, default=None):
        self._log.add(self._prefix + str(k))
        v = dict.get(self, k, default)
        return _Spy(v, self._log, self._prefix + str(k) + ".") if isinstance(v, dict) else (
            [(_Spy(x, self._log, self._prefix + str(k) + "[].") if isinstance(x, dict) else x) for x in v]
            if isinstance(v, list) else v)

    def __getitem__(self, k):
        return self.get(k)


def test_pregame_lineup_extractor_reads_only_sports_keys():
    import extract_pregame_lineups as X
    g = {"gameId": 1, "startTime": "2026-08-20T23:05:00Z", "odds": {"x": 1}, "kalshi": {"y": 2},
         "teamTotals": {}, "modelProb": {},
         "awayTeamStats": {"lineupConfirmedOfficial": True,
                           "confirmedLineup": [{"order": i, "playerId": str(100 + i), "position": "C",
                                                "platoonSplits": {"vsLHP": {}}} for i in range(1, 10)]},
         "away": {"pitcher": {"id": "5"}}, "home": {"pitcher": {"id": "6"}}}
    log = set()
    rows = X.extract_game_side(1, "away", datetime(2026, 8, 20, 20, tzinfo=timezone.utc), "f.json", _Spy(g, log))
    assert len(rows) == 9 and rows[0]["oppProbableId"] == "6" and rows[0]["ownProbableId"] == "5"
    allowed = {"awayTeamStats", "awayTeamStats.confirmedLineup", "awayTeamStats.confirmedLineup[].order",
               "awayTeamStats.confirmedLineup[].playerId", "awayTeamStats.confirmedLineup[].position",
               "startTime", "away", "away.pitcher", "away.pitcher.id", "home", "home.pitcher", "home.pitcher.id"}
    assert log <= allowed, log - allowed
    assert set(rows[0]) == set(X.COLUMNS)


def test_committed_pregame_lineups_are_pregame_and_sports_only():
    path = os.path.join(ROOT, "data", "research", "hitter_v2", "pregame_lineups_2026.csv.gz")
    with gzip.open(path, "rt", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows and set(rows[0]) == {"gamePk", "side", "captureUTC", "captureFile", "startUTC", "slot", "playerId",
                                     "position", "ownProbableId", "oppProbableId"}
    for r in rows:
        assert r["captureUTC"] < r["startUTC"].replace(".000", "")


@pytest.fixture(scope="module")
def season_2025():
    import data_v2 as D
    sd24 = W2.load_season(2024, FEED)
    sd25 = W2.load_season(2025, FEED)
    a24, a25 = D.annotate(sd24), D.annotate(sd25)
    return D, sd24, sd25, a24, a25


def test_records_match_w2_fields_and_are_as_of(season_2025):
    D, sd24, sd25, a24, a25 = season_2025
    import features as F
    bh, ph = D.hand_maps([sd24, sd25])
    prev = D.season_totals_v2(sd24, a24[0])
    end = "2025-04-20"
    filt = lambda pk: sd25["games"][pk]["officialDate"] <= end  # noqa: E731
    rec = D.build_records(sd25, a25[0], prev, D.official_provider(sd25), bh, ph, game_filter=filt)
    w2 = {(r["gamePk"], r["side"]): r for r in F.build_team_games(sd25, a25[0], F.season_totals(sd24, a24[0]))
          if not r.get("excluded")}
    games = sd25["games"]
    n = 0
    for r in rec:
        if r.get("excluded"):
            continue
        w = w2[(r["gamePk"], r["side"])]
        for k in ("sp_cur", "sp_prev", "sp_bf_cur", "pen_cur", "pen_prev", "oppStarterId"):
            assert r[k] == w[k], k
        for b, bw in zip(r["batters"], w["batters"]):
            assert (b["playerId"], b["cur"], b["prev"], b["box_cur"]) == (bw["playerId"], bw["cur"], bw["prev"],
                                                                          bw["box_cur"])
        # as-of: the batter's current-season count equals his PAs in games on EARLIER dates
        b = r["batters"][0]
        exp = sum(1 for p in a25[0] if p["batterId"] == b["playerId"]
                  and games[p["gamePk"]]["officialDate"] < r["date"])
        assert (sum(b["cur"]) if b["cur"] else 0) == exp
        assert sum(r["league_cur"]) == sum(1 for p in a25[0] if games[p["gamePk"]]["officialDate"] < r["date"])
        n += 1
        if n >= 60:
            break
    assert n == 60


def test_runner_out_and_at_risk_rows(season_2025):
    D, sd24, _sd25, a24, _a25 = season_2025
    _pas, ro, ar = a24
    assert all(0 <= ro[b][0] <= ro[b][1] for b in ro)
    assert sum(ro[b][0] for b in ro) > 300  # caught stealing / pickoff 3rd outs exist
    assert all(r[4] >= 1 and r[7] in (0, 1) for r in ar)
    # a slot leaves the risk set after its starter is replaced
    seen = set()
    for r in ar:
        key = (r[0], r[1], r[2])
        assert key not in seen
        if r[7]:
            seen.add(key)
