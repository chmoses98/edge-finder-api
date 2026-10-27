"""MLB-ALPHA-0003: series taxonomy, ticker parser, registry ladder."""
from datetime import timezone

from lib.edgelab.mlb_alpha import registry as rg
from lib.edgelab.mlb_alpha import taxonomy as tx


# ---- membership: the KXMLB prefix and the word "baseball" prove nothing -----
def test_mobile_legends_is_not_mlb_even_with_mlb_prefix():
    c = tx.classify_series("KXMLBBGAME", "Mobile Legends Bang Bang Game")
    assert c["membership"] == tx.NOT_MLB and "Mobile Legends" in c["membershipReason"]


def test_other_baseball_competitions_are_not_mlb():
    for s in ("KXNCAABBGAME", "KXNCAABASEBALL", "KXWBCGAME", "KXWBCRFI", "KXMLBWORLD", "KXLMBGAME", "KXCONGRESSBASEBALL"):
        assert tx.classify_series(s)["membership"] == tx.NOT_MLB, s


def test_exhibitions_are_mlb_but_not_pooled():
    for s in ("KXMLBSTGAME", "KXMLBASGAME", "KXMLBFTGAME"):
        assert tx.classify_series(s)["membership"] == tx.MLB_EXHIBITION, s


def test_unknown_series_is_reported_not_dropped():
    c = tx.classify_series("KXMLBBRANDNEW")
    # matches no rule: must surface as UNCLASSIFIED with a review note
    assert c["membership"] == tx.UNCLASSIFIED and "review" in (c["note"] or "")


def test_combo_series_is_labelled_not_pooled():
    assert tx.classify_series("KXMVECROSSCATEGORY")["membership"] == tx.MIXED_COMBO


def test_every_per_game_family_has_a_model_path_decision():
    for s in ("KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBTEAMTOTAL", "KXMLBF3", "KXMLBF5", "KXMLBF7",
              "KXMLBF5SPREAD", "KXMLBF5TOTAL", "KXMLBRFI", "KXMLBINNINGWIN", "KXMLBINNINGTOTAL", "KXMLBEXTRAS"):
        c = tx.classify_series(s)
        assert c["membership"] == tx.MLB and c["modelPath"] == tx.MP_UNIFIED_SIM, s
    # priced-by-sim is not the same as having settlement truth
    assert tx.classify_series("KXMLBINNINGWIN")["settlement"] == tx.ST_NOT_ARCHIVED


# ---- ticker parser -----------------------------------------------------------
def test_event_segment_dst_aware():
    summer = tx.parse_event_segment("26OCT152000MILLAD")
    assert summer["scheduledStartUtc"].tzinfo == timezone.utc
    assert summer["scheduledStartUtc"].isoformat() == "2026-10-16T00:00:00+00:00"   # EDT, UTC-4
    winter = tx.parse_event_segment("26NOV022000MILTB")
    assert winter["scheduledStartUtc"].isoformat() == "2026-11-03T01:00:00+00:00"   # EST, UTC-5


def test_parse_game_family_markets():
    p = tx.parse_market_ticker("KXMLBSPREAD-26OCT152000MILLAD-LAD4")
    assert (p["parseStatus"], p["team"], p["selection"], p["threshold"]) == ("PARSED", "LAD", "HOME", 4)
    p = tx.parse_market_ticker("KXMLBTOTAL-26OCT152000MILLAD-8")
    assert p["threshold"] == 8 and p.get("selection") is None
    p = tx.parse_market_ticker("KXMLBF5-26OCT152000MILLAD-TIE")
    assert p["selection"] == "TIE"
    p = tx.parse_market_ticker("KXMLBINNINGWIN-26OCT161800TBCLE-9-CLE")
    assert (p["inning"], p["team"], p["selection"]) == (9, "CLE", "HOME")
    p = tx.parse_market_ticker("KXMLBINNINGTOTAL-26OCT161800TBCLE-8-2")
    assert (p["inning"], p["threshold"]) == (8, 2)
    p = tx.parse_market_ticker("KXMLBEXTRAS-26OCT262000MILTB-EXTRAS")
    assert p["parseStatus"] == "PARSED"
    p = tx.parse_market_ticker("KXMLBRFI-26OCT262000MILTB")
    assert p["parseStatus"] == "PARSED"


def test_parse_player_prop():
    p = tx.parse_market_ticker("KXMLBHIT-26OCT152000MILLAD-MILWCONTRERAS24-2")
    assert (p["team"], p["player"], p["threshold"], p["side"]) == ("MIL", "WCONTRERAS24", 2, "AWAY")


def test_doubleheader_marker_and_two_letter_teams():
    p = tx.parse_market_ticker("KXMLBGAME-26AUG291915BOSNYYG2-BOS")
    assert p["doubleheaderGame"] == 2 and p["awayTeam"] == "BOS" and p["gameKey"].endswith("_G2")
    p = tx.parse_market_ticker("KXMLBGAME-26OCT032030SDMIL-SD")
    assert p["awayTeam"] == "SD" and p["selection"] == "AWAY"


def test_refuses_instead_of_guessing():
    assert tx.parse_market_ticker("KXMLBGAME-26OCT032030SDMIL-XYZ")["parseStatus"] == "TEAM_NOT_IN_EVENT"
    assert tx.parse_market_ticker("KXMLBTEAMTOTAL-26OCT032030SDMIL-XYZ4")["parseStatus"].startswith("TEAM_NOT_IN_EVENT")
    assert not tx.parse_market_ticker("")["parseStatus"].startswith("PARSED")
    assert tx.parse_market_ticker(None)["kind"] == "UNPARSED"


# ---- registry ladder ---------------------------------------------------------
def _reg():
    series = [{"seriesTicker": "KXMLBTOTAL", "title": "t"}, {"seriesTicker": "KXMLBINNINGWIN", "title": "i"},
              {"seriesTicker": "KXMLBBGAME", "title": "Mobile Legends"}, {"seriesTicker": "KXMLBBRANDNEW", "title": "?"}]
    markets = [{"ticker": "KXMLBTOTAL-26OCT152000MILLAD-8", "ts": "2026-10-15T12:00:00Z", "yesBid": 0.4, "yesAsk": 0.45, "volume": 10},
               {"ticker": "KXMLBINNINGWIN-26OCT161800TBCLE-9-CLE", "ts": "2026-10-16T12:00:00Z", "yesBid": None, "yesAsk": None}]
    settle = [{"marketTicker": "KXMLBTOTAL-26OCT152000MILLAD-8", "settlementStatus": "SETTLED", "result": "YES", "settledAt": "2026-10-16T05:00:00Z"}]
    return rg.build_registry(series, markets, settle, {"production": {"KXMLBTOTAL"}}, as_of="test")


def test_registry_never_drops_and_reports_gaps():
    reg = _reg()
    by = {e["seriesTicker"]: e for e in reg["series"]}
    assert set(by) == {"KXMLBTOTAL", "KXMLBINNINGWIN", "KXMLBBGAME", "KXMLBBRANDNEW"}
    assert by["KXMLBBGAME"]["researchStatus"] == "EXCLUDED_NOT_MLB"
    assert by["KXMLBBRANDNEW"]["researchStatus"] == "NEEDS_REVIEW"
    iw = by["KXMLBINNINGWIN"]
    assert iw["highestRung"] == "PRICEABLE" and any("SETTLEMENT_TRUTH_MISSING" in g for g in iw["gaps"])
    tot = by["KXMLBTOTAL"]
    assert tot["highestRung"] == "SETTLEABLE"      # 1 settled contract is far below the test floor
    assert any("BELOW_TEST_FLOOR" in g for g in tot["gaps"])


def test_registry_rejects_cents_masquerading_as_dollars():
    reg = rg.build_registry(
        [{"seriesTicker": "KXMLBTOTAL"}],
        [{"ticker": "KXMLBTOTAL-26OCT152000MILLAD-8", "ts": "x", "yesBid": 40.0, "yesAsk": 45.0}], [], None)
    assert reg["series"][0]["observed"]["quotedRows"] == 0     # never rescaled by magnitude


def test_qualified_is_never_assigned_by_the_registry():
    reg = _reg()
    assert all("QUALIFIED" not in e["ladderRungs"] for e in reg["series"])
