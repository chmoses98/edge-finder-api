import pytest

from lib.edgelab.research.market_structure import coherence as C, book as B, sharp as SH, leadlag as L, tape as T, candles as CD, settlements as SE


def test_ladder_pairs_flags_only_true_inversions():
    res = C.ladder_pairs({7: (60, 62), 8: (50, 52), 9: (40, 42)})
    assert all(not r["preFeeViolation"] for r in res) and len(res) == 3
    inv = C.ladder_pairs({7: (60, 62), 8: (70, 72)})   # P(>=8) quoted above P(>=7)
    assert inv[0]["preFeeViolation"] and inv[0]["postFeeViolation"]


def test_pre_fee_inversion_can_die_to_fees():
    r = C.ladder_pairs({7: (60, 62), 8: (62, 64)})[0]   # bid(8)=62 == ask(7): 0c slack -> no violation
    assert not r["preFeeViolation"]
    r = C.ladder_pairs({7: (60, 62), 8: (63, 65)})[0]   # 1c slack, fees ~3.6c on the pair -> pre-fee only
    assert r["preFeeViolation"] and not r["postFeeViolation"]


def test_three_way_bounds():
    ok = C.three_way({"AWAY": (40, 42), "HOME": (45, 47), "TIE": (10, 12)})
    assert ok["sumAsks"] == 101 and not ok["postFeeViolation"]
    arb = C.three_way({"AWAY": (30, 31), "HOME": (30, 31), "TIE": (5, 6)})
    assert arb["preFeeViolation"] and arb["postFeeViolation"]
    assert C.three_way({"AWAY": (40, 42), "HOME": (45, 47)}) is None


def test_cross_horizon_and_team_vs_game_dominance():
    # F5 total >= 7 quoted above game total >= 7 -> violation
    assert C.cross_horizon({7: (60, 62)}, {7: (70, 72)})[0]["preFeeViolation"]
    assert not C.cross_horizon({7: (60, 62)}, {7: (30, 32)})[0]["preFeeViolation"]
    assert not C.team_vs_game({8: (50, 52)}, {8: (20, 22)})[0]["preFeeViolation"]
    # spread >= 2 quoted above the moneyline -> violation
    assert C.spread_vs_ml((55, 57), {2: (60, 62)})[0]["preFeeViolation"]


def test_book_walk_prices_yes_against_no_bids_and_reports_slippage():
    bk = {"yes_dollars": [["0.45", "300"]], "no_dollars": [["0.43", "20"], ["0.40", "500"]]}
    assert B.top_of_book(bk)[:2] == (45, 57)
    w = B.walk_buy(bk, "YES", 500)
    assert w["filled"] and w["slippageCents"] == 0.0 and w["levelsUsed"] == 1
    w = B.walk_buy(bk, "YES", 5000)
    assert w["filled"] and w["slippageCents"] > 0 and w["levelsUsed"] == 2
    assert not B.walk_buy(bk, "NO", 10 ** 7)["filled"]
    assert B.walk_buy({}, "YES", 100)["topPriceCents"] is None


def test_novig_and_consensus():
    p = SH.novig_two_way(1.83, 1.91)
    assert p[0] + p[1] == pytest.approx(1.0) and p[0] > 0.5
    assert SH.consensus({"pinnacle": {"pHome": 0.6}, "draftkings": {"pHome": 0.62}}) == (pytest.approx(0.61), 2)
    row = {"away": "Toronto Blue Jays", "home": "Cleveland Guardians", "commenceTime": "2026-09-02T23:10:00Z",
           "bookmakers": [{"key": "pinnacle", "markets": [{"key": "h2h", "last_update": "2026-09-02T23:00:00Z",
                          "outcomes": [{"name": "Cleveland Guardians", "price": 2.0}, {"name": "Toronto Blue Jays", "price": 2.0}]}]}]}
    pr = SH.odds_row_probabilities(row)
    assert (pr["awayAbbr"], pr["homeAbbr"]) == ("TOR", "CLE") and pr["books"]["pinnacle"]["pHome"] == pytest.approx(0.5)


def test_leadlag_grid_rows_never_look_past_first_pitch():
    a = {t: 1.0 for t in range(0, 20000, 60)}
    b = {t: 2.0 for t in range(0, 20000, 60)}
    rows = L.grid_rows(a, b, 18000, 30)
    assert rows and all(r == (0.0, 0.0, 0.0) for r in rows)
    assert L.ladder_implied_mean({7: 60, 8: 40, 9: 20}) == pytest.approx(7.2)


def test_candle_series_treats_empty_sides_as_none():
    rec = {"candlesticks": [{"end_period_ts": 60, "yes_bid": {"close_dollars": "0.0000"}, "yes_ask": {"close_dollars": "0.9900"}, "volume_fp": "1", "open_interest_fp": "2"},
                            {"end_period_ts": 120, "yes_bid": {"close_dollars": "0.4200"}, "yes_ask": {"close_dollars": "0.4400"}, "volume_fp": "1", "open_interest_fp": "2"}]}
    s = CD.series_from_record(rec)
    assert s[60][0] is None and s[120] == (42, 44, 1.0, 2.0)
    assert CD.mid(s[120]) == 43.0 and CD.mid(s[60]) is None


def test_tape_decomposition_signs():
    series = {t: (48, 50, 0.0, 0.0) for t in range(0, 7200, 60)}
    series[3600 + 1800] = (52, 54, 0.0, 0.0)     # mid rises 4c 30 minutes later
    tr = {"yes_price_dollars": "0.5000", "count_fp": "10", "created_time": "1970-01-01T00:59:30Z", "taker_side": "yes"}
    row = T.decompose_print(tr, series, start_ts=7200, last_pregame_ts=7140)
    assert row["halfSpread"] == 1.0 and row["drift_30"] == 4.0 and row["takerNet_30"] == pytest.approx(4.0 - 1.0 - 1.75)
    tr_no = dict(tr, taker_side="no")
    row_no = T.decompose_print(tr_no, series, start_ts=7200, last_pregame_ts=7140)
    assert row_no["price"] == 50 and row_no["drift_30"] == -4.0
    assert T.decompose_print(dict(tr, created_time="1970-01-01T02:30:00Z"), series, 7200, 7140) is None  # in-game


def test_settlement_correction_is_date_aware():
    arch = {"KXMLBTOTAL-26AUG121845CHCWSH-8": "YES", "KXMLBTOTAL-26AUG121845CHCWSH-9": "NO",
            "KXMLBTOTAL-26SEP121845CHCWSH-9": "YES"}
    # August: archived rule was "> N", so rung 9 corrected = archived rung 8
    assert SE.corrected_outcome("KXMLBTOTAL-26AUG121845CHCWSH-9", arch) == "YES"
    # September: archive already uses ">= N"
    assert SE.corrected_outcome("KXMLBTOTAL-26SEP121845CHCWSH-9", arch) == "YES"
    assert SE.corrected_outcome("KXMLBTOTAL-26AUG121845CHCWSH-12", arch) is None  # rung 11 not archived: never guessed
