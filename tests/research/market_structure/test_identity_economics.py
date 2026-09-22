"""MRV research primitives: identity + economics. Standard library only."""
import pytest

from lib.edgelab.research.market_structure import identity as I, economics as E


def test_parse_ladder_rung_and_game_key():
    r = I.parse_ticker("KXMLBF5TOTAL-26AUG122210TEXLAA-5")
    assert r["status"] == "RESOLVED"
    assert (r["family"], r["horizon"], r["rung"]) == ("inning_total", "F5", 5)
    assert r["physicalGameKey"] == "26AUG122210TEXLAA"
    assert (r["awayTeam"], r["homeTeam"]) == ("TEX", "LAA")


def test_parse_three_way_and_winner_sides():
    assert I.parse_ticker("KXMLBF5-26AUG122210TEXLAA-TIE")["side"] == "TIE"
    assert I.parse_ticker("KXMLBF5-26AUG122210TEXLAA-LAA")["side"] == "HOME"
    assert I.parse_ticker("KXMLBGAME-26AUG122210TEXLAA-TEX")["side"] == "AWAY"


def test_parse_team_ladders():
    s = I.parse_ticker("KXMLBSPREAD-26SEP022210STLLAD-LAD3")
    assert (s["family"], s["team"], s["rung"], s["side"]) == ("winning_margin", "LAD", 3, "HOME")
    t = I.parse_ticker("KXMLBTEAMTOTAL-26AUG121845CHCWSH-WSH8")
    assert (t["family"], t["team"], t["rung"]) == ("team_total", "WSH", 8)


def test_parse_refuses_out_of_scope_and_malformed():
    assert I.parse_ticker("KXMLBHRR-26AUG122210TEXLAA-XYZ1")["status"] == "UNRESOLVED"
    assert I.parse_ticker("KXMLBGAME-26AUG122210TEXLAA-BOS")["status"] == "UNRESOLVED"
    assert I.parse_ticker(None)["status"] == "UNRESOLVED"
    assert I.physical_game_key("KXMLBGAME-26AUG122210TEXLAA") == "26AUG122210TEXLAA"


def test_same_game_key_across_series():
    keys = {I.parse_ticker(t)["physicalGameKey"] for t in (
        "KXMLBGAME-26AUG122210TEXLAA-TEX", "KXMLBTOTAL-26AUG122210TEXLAA-8", "KXMLBF5-26AUG122210TEXLAA-TIE")}
    assert keys == {"26AUG122210TEXLAA"}


def test_executable_prices_never_use_midpoint_or_yes_ask_for_no():
    assert E.executable_yes_cents(43) == 43
    assert E.executable_no_cents(yes_bid=42) == 58
    assert E.executable_no_cents(yes_bid=42, no_ask=57) == 57
    assert E.executable_yes_cents(None) is None
    assert E.executable_yes_cents(100) is None and E.executable_yes_cents(0) is None


def test_taker_fee_matches_canonical_module():
    from lib.edgelab import kalshi_fees as kf
    assert E.taker_fee_cents(50, 10) == round(kf.taker_fee(10, 0.50) * 100, 2)
    assert E.taker_fee_cents(50, 1) == 2.0     # ceil(1.75c) per order
    assert E.fee_drag_cents(50) == 1.75
    assert E.fee_drag_cents(90) == pytest.approx(0.63)


def test_locked_profit_requires_beating_fees():
    # YES 48 + NO priced 49: 97c principal + 2 x fee(10 contracts) -> negative -> no arbitrage
    assert E.locked_profit_cents([48, 49]) < 0
    # deep mispricing: YES 30 + NO 30 -> 60c + fees < 100 -> locked
    assert E.locked_profit_cents([30, 30]) > 0
    assert E.locked_profit_cents([30, None]) is None


def test_settlement_order_is_canonical_tier_c():
    so = E.settlement_order(48, True)
    assert so["contracts"] > 0 and so["netProfitLoss"] > 0
    assert E.settlement_order(48, False)["netProfitLoss"] < 0
    assert E.settlement_order(None, True) is None
