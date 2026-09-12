"""
tests/test_w1a_root_settlement_authority.py
===========================================
CEO review of PR #208, blockers 1 and 2.

BLOCKER 1 -- the root-ledger money-writing path could still turn a wager into a
monetary WIN/LOSS/PUSH from generic baseball semantics (matchup + orientation +
direction + line + final score) without the exact Kalshi contract ever being
proven.

BLOCKER 2 -- F5 settlement chose its physical game with
`f5_gamepks.get((away, home))`, a team-pair key. A doubleheader has the same
date and the same two clubs for both legs, so the dict that lookup read from had
already silently discarded one of them.

WHY THESE TESTS DRIVE main() AND NOT resolve_wager_side()
---------------------------------------------------------
A unit test of the resolver proves the resolver. It does not prove that the
code which WRITES MONEY asks the resolver, and that is precisely what was
wrong: at the reviewed head the resolver was correct and the money path did not
consult it. So every test here runs `clv_update.main()` -- the real production
entry point, the real settlement loop, the real persistence to bets.json -- with
only the network boundary stubbed, and then asserts on the LEDGER FILE that came
out the other side.

The assertion that matters is always the same one: `result`, `status` and `pl`
must be absent unless the canonical chain proved the contract, the side and the
physical game.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import wager_settlement_semantics as wss  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# The doubleheader fixture: MIL @ PIT, 2026-07-11, two physical games.
# This is the real archived CR-3 matchup -- the one where one Kalshi ticker was
# claimed by gamePk 823356 AND 823357.
# ─────────────────────────────────────────────────────────────────────────────
DH_DATE = "2026-07-11"
DH_GAME = "MIL@PIT"
LEG1_PK = "823356"
LEG2_PK = "823357"
# 16:05 ET / 19:15 ET, expressed as the UTC the MLB schedule actually returns.
LEG1_START_UTC = "2026-07-11T20:05:00Z"
LEG2_START_UTC = "2026-07-11T23:15:00Z"

LEG1_F5_TICKER = "KXMLBF5-26JUL111605MILPIT-MIL"
LEG2_F5_TICKER = "KXMLBF5-26JUL111915MILPIT-MIL"
LEG1_ML_TICKER = "KXMLBGAME-26JUL111605MILPIT-MIL"
LEG2_ML_TICKER = "KXMLBGAME-26JUL111915MILPIT-MIL"

# Leg 1: MIL leads 5-1 after five, and wins 7-2.   -> an away F5/ML bet WINS
# Leg 2: MIL trails 0-4 after five, and loses 1-9. -> the SAME bet shape LOSES
# The two legs are deliberately opposite so that settling from the wrong one is
# never merely inaccurate -- it inverts the result and the sign of the P/L.
LEG_TRUTH = {
    LEG1_PK: {"f5": (5, 1), "final": (7, 2)},
    LEG2_PK: {"f5": (0, 4), "final": (1, 9)},
}

DOUBLEHEADER_SCHEDULE = [
    {"gamePk": LEG1_PK, "gameNumber": 1, "awayAbbr": "MIL", "homeAbbr": "PIT",
     "gameDateIso": DH_DATE, "startTime": LEG1_START_UTC, "abstractGameState": "Final"},
    {"gamePk": LEG2_PK, "gameNumber": 2, "awayAbbr": "MIL", "homeAbbr": "PIT",
     "gameDateIso": DH_DATE, "startTime": LEG2_START_UTC, "abstractGameState": "Final"},
]


def _linescore(game_pk):
    """An MLB linescore payload for one exact gamePk, or None."""
    truth = LEG_TRUTH.get(str(game_pk))
    if truth is None:
        return None
    away_f5, home_f5 = truth["f5"]
    away_final, home_final = truth["final"]
    # All five runs in the first inning keeps the fixture unambiguous; the F5
    # library sums innings 1-5 either way.
    innings = [{"num": 1, "away": {"runs": away_f5}, "home": {"runs": home_f5}}]
    innings += [{"num": n, "away": {"runs": 0}, "home": {"runs": 0}} for n in range(2, 10)]
    return {"innings": innings,
            "teams": {"away": {"runs": away_final}, "home": {"runs": home_final}}}


@pytest.fixture
def cu(monkeypatch):
    """clv_update with ONLY the network boundary stubbed."""
    monkeypatch.setenv("ODDS_API_KEY", "dummy-test-key")
    # clv_update reads ODDS_API_KEY into a module-level constant AT IMPORT, so
    # it has to be re-imported under the dummy key. The original module object
    # is kept and put back on teardown: other modules (e.g.
    # scripts/edgelab/backtest/probe_odds_api_historical_pinnacle.py) import
    # clv_update's FUNCTION OBJECTS directly and are themselves cached, so
    # leaving a different clv_update in sys.modules breaks their identity
    # assertions. Restoring makes this fixture inert outside its own tests
    # whatever order the suites are collected in.
    _original = sys.modules.pop("clv_update", None)
    import clv_update as _cu

    # fetch_scores is the team-pair-keyed Odds API map. It is deliberately left
    # POPULATED AND WRONG for the doubleheader: it can only hold one leg, and
    # the leg it holds here is leg 2's final. If any money path still consulted
    # it, the leg-1 assertions below would fail.
    monkeypatch.setattr(_cu, "fetch_scores", lambda date_str: {
        ("MIL", "PIT"): {"away_score": 1, "home_score": 9, "completed": True},
        ("SD", "TEX"): {"away_score": 5, "home_score": 2, "completed": True},
    })
    monkeypatch.setattr(_cu, "fetch_mlb_schedule_games",
                        lambda date_str: list(DOUBLEHEADER_SCHEDULE))
    monkeypatch.setattr(_cu, "fetch_mlb_linescore", _linescore)
    monkeypatch.setattr(_cu, "fetch_final_score_for_game",
                        lambda game_pk: (
                            {"away_score": LEG_TRUTH[str(game_pk)]["final"][0],
                             "home_score": LEG_TRUTH[str(game_pk)]["final"][1],
                             "completed": True}
                            if str(game_pk) in LEG_TRUTH else None))
    yield _cu
    if _original is not None:
        sys.modules["clv_update"] = _original
    else:
        sys.modules.pop("clv_update", None)


def _run(cu, tmp_path, monkeypatch, bets, date=DH_DATE):
    monkeypatch.chdir(tmp_path)
    if not (tmp_path / "data").exists():
        (tmp_path / "data").mkdir()
    with open(tmp_path / "bets.json", "w") as handle:
        json.dump(bets, handle)
    sys.argv = ["clv_update.py", date]
    cu.main()
    rows = json.loads((tmp_path / "bets.json").read_text())
    return {row["id"]: row for row in rows}


def _wager(bet_id, ticker, market="F5 ML", **extra):
    row = {"id": bet_id, "date": DH_DATE, "game": DH_GAME, "market": market,
           "betSide": "AWAY", "status": "pending", "result": None, "pl": None,
           "price": -110, "betSize": 10.0}
    if ticker is not None:
        row["marketTicker"] = ticker
    row.update(extra)
    return row


def _assert_ungraded(row):
    """The single assertion this whole file exists for."""
    assert row.get("result") is None, row.get("result")
    assert row.get("status") != "SETTLED"
    assert row.get("pl") is None
    assert row.get("settlementRefusalReason"), "a refusal must say why"


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKER 2 -- end-to-end doubleheader settlement through the production path
# ─────────────────────────────────────────────────────────────────────────────

class TestDoubleheaderRootSettlementEndToEnd:

    def test_each_leg_settles_only_from_its_own_physical_game(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("dh-leg1", LEG1_F5_TICKER),
            _wager("dh-leg2", LEG2_F5_TICKER),
        ])
        # Leg 1: MIL led 5-1 after five -> the away F5 bet WON.
        assert rows["dh-leg1"]["result"] == "WIN"
        assert rows["dh-leg1"]["settlementGamePk"] == LEG1_PK
        # Leg 2: MIL trailed 0-4 after five -> the SAME bet shape LOST.
        assert rows["dh-leg2"]["result"] == "LOSS"
        assert rows["dh-leg2"]["settlementGamePk"] == LEG2_PK
        # And the P/L signs are opposite, which is what a wrong-leg settlement
        # would have silently inverted.
        assert rows["dh-leg1"]["pl"] > 0
        assert rows["dh-leg2"]["pl"] < 0

    def test_leg1_cannot_be_settled_from_leg2_truth(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [_wager("only-leg1", LEG1_F5_TICKER)])
        row = rows["only-leg1"]
        assert row["settlementGamePk"] == LEG1_PK
        assert row["settlementGamePk"] != LEG2_PK
        assert row["result"] == "WIN"
        # Leg 2's F5 was 0-4; had leg 2's truth been used this would be LOSS.
        assert row["awayScore"] == 5 and row["homeScore"] == 1

    def test_leg2_cannot_be_settled_from_leg1_truth(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [_wager("only-leg2", LEG2_F5_TICKER)])
        row = rows["only-leg2"]
        assert row["settlementGamePk"] == LEG2_PK
        assert row["result"] == "LOSS"
        assert row["awayScore"] == 0 and row["homeScore"] == 4

    def test_reversing_the_schedule_order_changes_nothing(self, cu, tmp_path, monkeypatch):
        forward = _run(cu, tmp_path, monkeypatch, [
            _wager("dh-leg1", LEG1_F5_TICKER), _wager("dh-leg2", LEG2_F5_TICKER)])
        monkeypatch.setattr(cu, "fetch_mlb_schedule_games",
                            lambda date_str: list(reversed(DOUBLEHEADER_SCHEDULE)))
        reverse = _run(cu, tmp_path, monkeypatch, [
            _wager("dh-leg1", LEG1_F5_TICKER), _wager("dh-leg2", LEG2_F5_TICKER)])
        for bet_id in ("dh-leg1", "dh-leg2"):
            assert forward[bet_id]["settlementGamePk"] == reverse[bet_id]["settlementGamePk"]
            assert forward[bet_id]["result"] == reverse[bet_id]["result"]
            assert forward[bet_id]["pl"] == reverse[bet_id]["pl"]

    def test_removing_the_exact_game_binding_causes_refusal(self, cu, tmp_path, monkeypatch):
        """No ticker -> no event -> no provable leg -> no grade, on a date where
        the baseball truth for BOTH legs is sitting right there."""
        rows = _run(cu, tmp_path, monkeypatch, [_wager("dh-noticker", None)])
        _assert_ungraded(rows["dh-noticker"])
        assert rows["dh-noticker"]["settlementRefusalReason"] == wss.SIDE_UNPROVEN_NO_CONTRACT

    def test_a_team_pair_key_provably_cannot_distinguish_the_legs(self):
        """
        The defect, demonstrated rather than asserted. Building the old
        `{(away, home): gamePk}` map over this fixture LOSES a game: two
        physical games collapse to one key, so whichever leg is written last
        wins and the other becomes unreachable.
        """
        team_pair_map = {}
        for game in DOUBLEHEADER_SCHEDULE:
            team_pair_map[(game["awayAbbr"], game["homeAbbr"])] = game["gamePk"]
        assert len(DOUBLEHEADER_SCHEDULE) == 2
        assert len(team_pair_map) == 1          # one leg silently discarded
        assert team_pair_map[("MIL", "PIT")] == LEG2_PK
        # Reversing the input flips which leg survives -- the hallmark of a key
        # that is not an identity.
        flipped = {}
        for game in reversed(DOUBLEHEADER_SCHEDULE):
            flipped[(game["awayAbbr"], game["homeAbbr"])] = game["gamePk"]
        assert flipped[("MIL", "PIT")] == LEG1_PK
        assert flipped != team_pair_map

    def test_production_no_longer_builds_a_team_pair_gamepk_map(self):
        """The lossy map is not merely unused -- it is gone."""
        import clv_update as cu_mod
        assert not hasattr(cu_mod, "fetch_mlb_schedule_gamepks")
        with open(os.path.join(ROOT, "clv_update.py")) as handle:
            # Comments are stripped: this file DESCRIBES the removed lookup in
            # prose so the next reader knows why it went, and a raw substring
            # search would match that description and pass vacuously in reverse.
            code = "\n".join(line.split("#", 1)[0] for line in handle)
        assert "f5_gamepks" not in code
        assert "gamepks[(" not in code

    def test_the_schedule_helper_returns_every_leg(self, cu):
        games = cu.fetch_mlb_schedule_games(DH_DATE)
        assert len(games) == 2
        assert {g["gamePk"] for g in games} == {LEG1_PK, LEG2_PK}

    def test_full_game_moneyline_is_leg_specific_too(self, cu, tmp_path, monkeypatch):
        """
        Blocker 1's other half. The non-F5 path took its final score from
        fetch_scores(), which is ALSO team-pair keyed -- in this fixture it
        holds leg 2's 1-9. Leg 1 must still settle WIN from its own 7-2.
        """
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("ml-leg1", LEG1_ML_TICKER, market="ML"),
            _wager("ml-leg2", LEG2_ML_TICKER, market="ML"),
        ])
        assert rows["ml-leg1"]["result"] == "WIN"
        assert rows["ml-leg1"]["awayScore"] == 7 and rows["ml-leg1"]["homeScore"] == 2
        assert rows["ml-leg1"]["settlementGamePk"] == LEG1_PK
        assert rows["ml-leg2"]["result"] == "LOSS"
        assert rows["ml-leg2"]["awayScore"] == 1 and rows["ml-leg2"]["homeScore"] == 9
        assert rows["ml-leg2"]["settlementGamePk"] == LEG2_PK
        assert rows["ml-leg1"]["settlementTruthSource"] == "MLB_LINESCORE_GAMEPK_%s" % LEG1_PK


# ─────────────────────────────────────────────────────────────────────────────
# BLOCKER 1 -- the money path cannot grade without canonical proof
# ─────────────────────────────────────────────────────────────────────────────
# Every case below has ABUNDANT baseball truth available. That is the point:
# refusing when the score is unknown is easy, and is not what was broken.

class TestMoneyPathCannotGradeWithoutCanonicalProof:

    def test_A_moneyline_with_team_and_final_score_but_no_contract(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("A", None, market="ML", game="SD @ TEX", date="2026-07-11")])
        _assert_ungraded(rows["A"])

    def test_B_total_with_under_and_line_and_final_score_but_no_contract(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("B", None, market="Total", betSide="UNDER", line=8.5)])
        _assert_ungraded(rows["B"])

    def test_C_team_total_with_team_over_line_and_final_score_but_no_contract(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("C", None, market="Team Total", betSide="AWAY OVER", line=4)])
        _assert_ungraded(rows["C"])

    def test_D_f5_with_final_linescore_but_ambiguous_doubleheader_identity(self, cu, tmp_path, monkeypatch):
        """
        The exact shape blocker 2 describes: an F5 row on a doubleheader date
        with both legs' linescores available and no evidence of which leg it is.
        """
        rows = _run(cu, tmp_path, monkeypatch, [_wager("D", None, market="F5 ML")])
        _assert_ungraded(rows["D"])

    def test_E_ticker_contradicting_the_recorded_selection(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            # The row records a club that is not either team in this ticker's
            # own event. (A SIBLING club would not belong here: on a full-game
            # moneyline "PIT wins" IS the proven NO side of MIL's contract, and
            # treating that as a contradiction would break approved W1-A
            # behaviour -- see test_home_selection_on_the_away_teams_ticker...)
            _wager("E", LEG1_ML_TICKER, market="ML", betSide=None, side="NYY")])
        _assert_ungraded(rows["E"])
        assert rows["E"]["settlementRefusalReason"] == \
            wss.SIDE_CONTRADICTED_TEAM_NOT_ON_THIS_CONTRACT
        assert rows["E"]["settlementRefusalClass"] == wss.REFUSAL_CONTRADICTION

    def test_F_ticker_strike_contradicting_the_recorded_line(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("F", "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", market="Team Total",
                   betSide="AWAY OVER", line=6)])
        _assert_ungraded(rows["F"])
        assert rows["F"]["settlementRefusalReason"] == wss.SIDE_CONTRADICTED_THRESHOLD

    def test_G_ticker_belonging_to_another_gamepk(self, cu, tmp_path, monkeypatch):
        """Leg 1's ticker on a row that records leg 2's gamePk."""
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("G", LEG1_ML_TICKER, market="ML", gamePk=LEG2_PK)])
        _assert_ungraded(rows["G"])
        assert rows["G"]["settlementRefusalReason"] == wss.SETTLEMENT_CONTRADICTED_GAME
        assert rows["G"]["settlementRefusalClass"] == wss.REFUSAL_CONTRADICTION

    def test_a_ticker_for_a_game_not_on_the_schedule_refuses(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("no-such-game", "KXMLBGAME-26JUL111605SDTEX-SD", market="ML", game="SD @ TEX")])
        _assert_ungraded(rows["no-such-game"])
        assert rows["no-such-game"]["settlementRefusalReason"] == \
            wss.SETTLEMENT_UNPROVEN_NO_PHYSICAL_GAME

    def test_a_player_prop_still_defers_through_the_money_path(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("prop", None, market="K Prop", bet="Sale K Over 8")])
        assert rows["prop"].get("result") is None
        assert rows["prop"].get("pl") is None


# ─────────────────────────────────────────────────────────────────────────────
# Positive controls -- the same shapes, with the exact evidence present
# ─────────────────────────────────────────────────────────────────────────────
# Without these the suite above could be satisfied by a money path that simply
# never settles anything.

class TestPositiveControlsWithCompleteEvidence:

    def test_moneyline_settles_when_the_contract_and_game_are_proven(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("pos-ml", LEG1_ML_TICKER, market="ML")])
        row = rows["pos-ml"]
        assert row["result"] == "WIN"
        assert row["settlementGamePk"] == LEG1_PK
        assert row["settlementMarketTicker"] == LEG1_ML_TICKER
        assert row["settlementSideBasis"]
        assert row["settlementRefusalReason"] is None
        assert row["pl"] is not None

    def test_team_total_settles_on_its_own_strike(self, cu, tmp_path, monkeypatch):
        # Leg 1 final 7-2: MIL (away) scored 7, so "MIL 4+ runs" is a WIN.
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("pos-tt", "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4",
                   market="Team Total", betSide="AWAY OVER", line=4)])
        assert rows["pos-tt"]["result"] == "WIN"
        assert rows["pos-tt"]["settlementGamePk"] == LEG1_PK

    def test_total_settles_both_directions_on_its_own_strike(self, cu, tmp_path, monkeypatch):
        # Leg 1 final 7-2 = 9 runs, against the -9 rung (>= 9).
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("pos-over", "KXMLBTOTAL-26JUL111605MILPIT-9",
                   market="Total", betSide="OVER", line=9),
            _wager("pos-under", "KXMLBTOTAL-26JUL111605MILPIT-9",
                   market="Total", betSide="UNDER", line=9),
        ])
        # Leg 1 finished 7-2 = EXACTLY 9 runs, sitting on the contract's own
        # rung. KXMLBTOTAL-...-9 pays YES at ">= 9", so Over WINS and Under
        # LOSES. Graded with determine_result's sportsbook ">" against a
        # whole-number line, both would have been a PUSH -- this asserts the
        # boundary the proven contract rung fixed.
        assert rows["pos-over"]["result"] == "WIN"
        assert rows["pos-under"]["result"] == "LOSS"

    def test_an_authorized_row_records_its_whole_proof_chain(self, cu, tmp_path, monkeypatch):
        rows = _run(cu, tmp_path, monkeypatch, [
            _wager("pos-proof", LEG2_ML_TICKER, market="ML")])
        row = rows["pos-proof"]
        for field in ("settlementGamePk", "settlementGamePkBasis",
                      "settlementMarketTicker", "settlementSideBasis",
                      "settlementTruthSource"):
            assert row.get(field), field
        assert row["settlementGamePk"] == LEG2_PK
        assert row["settlementTruthSource"].endswith(LEG2_PK)


# ─────────────────────────────────────────────────────────────────────────────
# The gate itself, on the shapes main() cannot easily reach
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthorizationGateDirectly:

    def test_no_schedule_is_a_refusal_not_an_authorization(self):
        out = wss.authorize_root_ledger_settlement(
            {"game": DH_GAME, "market": "ML", "betSide": "AWAY",
             "marketTicker": LEG1_ML_TICKER}, schedule_games=None)
        assert out["authorized"] is False
        assert out["refusalReason"] == wss.SETTLEMENT_UNPROVEN_NO_SCHEDULE

    def test_an_empty_schedule_refuses(self):
        out = wss.authorize_root_ledger_settlement(
            {"game": DH_GAME, "market": "ML", "betSide": "AWAY",
             "marketTicker": LEG1_ML_TICKER}, schedule_games=[])
        assert out["authorized"] is False
        assert out["refusalReason"] == wss.SETTLEMENT_UNPROVEN_NO_PHYSICAL_GAME

    def test_two_legs_with_no_distinguishing_evidence_refuse(self):
        """
        Both legs present, neither carrying a start time or a leg number. There
        is genuinely nothing to choose between them, and the gate must not.
        """
        blind = [{"gamePk": LEG1_PK, "awayAbbr": "MIL", "homeAbbr": "PIT",
                  "gameDateIso": DH_DATE},
                 {"gamePk": LEG2_PK, "awayAbbr": "MIL", "homeAbbr": "PIT",
                  "gameDateIso": DH_DATE}]
        out = wss.authorize_root_ledger_settlement(
            {"game": DH_GAME, "market": "ML", "betSide": "AWAY",
             "marketTicker": LEG1_ML_TICKER}, schedule_games=blind)
        assert out["authorized"] is False
        assert out["refusalReason"] == wss.SETTLEMENT_UNPROVEN_AMBIGUOUS_PHYSICAL_GAME

    def test_a_matching_recorded_gamepk_is_corroboration_not_a_conflict(self):
        out = wss.authorize_root_ledger_settlement(
            {"game": DH_GAME, "market": "ML", "betSide": "AWAY",
             "marketTicker": LEG1_ML_TICKER, "gamePk": LEG1_PK},
            schedule_games=DOUBLEHEADER_SCHEDULE)
        assert out["authorized"] is True
        assert out["physicalGameKey"] == LEG1_PK

    def test_the_gate_never_mutates_the_row_it_reads(self):
        row = {"game": DH_GAME, "market": "ML", "betSide": "AWAY",
               "marketTicker": LEG1_ML_TICKER}
        snapshot = json.dumps(row, sort_keys=True)
        wss.authorize_root_ledger_settlement(row, schedule_games=DOUBLEHEADER_SCHEDULE)
        assert json.dumps(row, sort_keys=True) == snapshot

    def test_the_gate_is_deterministic_across_repeated_calls(self):
        row = {"game": DH_GAME, "market": "ML", "betSide": "AWAY",
               "marketTicker": LEG2_ML_TICKER}
        first = wss.authorize_root_ledger_settlement(row, schedule_games=DOUBLEHEADER_SCHEDULE)
        second = wss.authorize_root_ledger_settlement(row, schedule_games=DOUBLEHEADER_SCHEDULE)
        assert json.dumps(first, sort_keys=True, default=str) == \
            json.dumps(second, sort_keys=True, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# The EdgeLab path: ticker identity by indexing, gamePk by contradiction check
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeLabSettlementIdentity:
    """
    CEO review of PR #208: prove the EdgeLab path cannot grade a row merely
    because its stored `side` happens to be YES/NO, when ticker or game
    identity contradicts the settlement being consumed.
    """

    def test_ticker_identity_is_proven_by_the_indexing_itself(self):
        """
        Documents the ACTUAL call chain rather than re-deriving it.
        settle_markets.py builds `bets_by_ticker[bet["marketTicker"]]` and reads
        it back with `bets_by_ticker.get(market["marketTicker"])`, so a bet can
        only ever be handed the settlement of its own exact ticker string. No
        parse is involved and none is needed.
        """
        source_path = os.path.join(ROOT, "scripts", "edgelab", "settle_markets.py")
        with open(source_path) as handle:
            source = handle.read()
        assert 'bets_by_ticker.setdefault(bet["marketTicker"], []).append(bet)' in source
        assert 'bets_by_ticker.get(market["marketTicker"], [])' in source

    def test_a_bet_whose_gamepk_contradicts_the_settlement_is_not_graded(self):
        from lib.edgelab import settlement as es
        bet = {"betId": "x", "marketTicker": LEG1_ML_TICKER, "side": "YES",
               "gameId": LEG2_PK, "stake": 10.0, "entryPrice": 0.5,
               "status": "pending", "result": None}
        settled = es.settle_bets_for_ticker([bet], "SETTLED", "YES", game_id=LEG1_PK)
        assert len(settled) == 1
        assert settled[0].get("result") is None
        assert settled[0].get("status") == "pending"
        assert settled[0]["settlementRefusalReason"] == wss.SETTLEMENT_CONTRADICTED_GAME
        assert settled[0]["settlementRefusalClass"] == wss.REFUSAL_CONTRADICTION

    def test_a_stored_side_of_yes_is_not_by_itself_permission_to_grade(self):
        """A perfectly valid YES, on the wrong game, still refuses."""
        from lib.edgelab import settlement as es
        for side in ("YES", "NO"):
            bet = {"betId": "x", "marketTicker": LEG1_ML_TICKER, "side": side,
                   "gameId": LEG2_PK, "stake": 10.0, "entryPrice": 0.5,
                   "status": "pending", "result": None}
            settled = es.settle_bets_for_ticker([bet], "SETTLED", "YES", game_id=LEG1_PK)
            assert settled[0].get("result") is None, side
            assert settled[0].get("netProfitLoss") is None, side

    def test_an_agreeing_gamepk_still_grades_both_sides(self):
        from lib.edgelab import settlement as es
        for side, result, expected in (("YES", "YES", "WIN"), ("YES", "NO", "LOSS"),
                                       ("NO", "NO", "WIN"), ("NO", "YES", "LOSS")):
            bet = {"betId": "x", "marketTicker": LEG1_ML_TICKER, "side": side,
                   "gameId": LEG1_PK, "stake": 10.0, "entryPrice": 0.5,
                   "status": "pending", "result": None}
            settled = es.settle_bets_for_ticker([bet], "SETTLED", result, game_id=LEG1_PK)
            assert settled[0]["result"] == expected
            assert settled[0].get("settlementRefusalReason") is None

    def test_a_bet_with_no_gamepk_is_still_graded_on_ticker_identity_alone(self):
        """
        Most archived rows carry no gameId. Ticker identity already proves the
        match for them, so absence must not become a refusal -- that would be
        fail-closed applied to evidence that was never required.
        """
        from lib.edgelab import settlement as es
        bet = {"betId": "x", "marketTicker": LEG1_ML_TICKER, "side": "YES",
               "stake": 10.0, "entryPrice": 0.5, "status": "pending", "result": None}
        settled = es.settle_bets_for_ticker([bet], "SETTLED", "YES", game_id=LEG1_PK)
        assert settled[0]["result"] == "WIN"

    def test_the_gamepk_refusal_is_idempotent(self):
        from lib.edgelab import settlement as es
        bet = {"betId": "x", "marketTicker": LEG1_ML_TICKER, "side": "YES",
               "gameId": LEG2_PK, "stake": 10.0, "entryPrice": 0.5,
               "status": "pending", "result": None}
        first = es.settle_bets_for_ticker([bet], "SETTLED", "YES", game_id=LEG1_PK)[0]
        second = es.settle_bets_for_ticker([first], "SETTLED", "YES", game_id=LEG1_PK)[0]
        assert es.bet_needs_settlement_update(first, second) is False


# ─────────────────────────────────────────────────────────────────────────────
# Disposition of the four CEO findings, through the CORRECTED chain
# ─────────────────────────────────────────────────────────────────────────────

def _committed_schedule():
    sys.path.insert(0, os.path.join(ROOT, "scripts", "audit"))
    from w1a_settlement_semantics_audit import load_committed_schedule
    return load_committed_schedule()


def _root_row(bet_id):
    with open(os.path.join(ROOT, "bets.json")) as handle:
        for row in json.load(handle):
            if row.get("id") == bet_id:
                return row
    return None


class TestCeoFindingDispositions:
    """
    The four findings PR #208 returned for adjudication, re-run through the
    corrected money path. READ-ONLY: these assert what the chain now SAYS about
    each row. Nothing is rewritten -- the ledger is canonical evidence and stays
    byte-identical.
    """

    def test_the_13_25_misgrade_is_downgraded_to_unresolved(self):
        """
        2026-06-02-COL-LAA-TT-HOME-OVER. Its own betSide ("HOME OVER"), line
        and final score make the ARITHMETIC unambiguous, and PR #208 reported it
        as a proven $13.25 correction on that basis.

        Under the corrected chain that is no longer good enough, and the
        downgrade is the point: the row carries NO marketTicker, so its exact
        Kalshi contract was never recorded and cannot be proven. The CEO's own
        rule -- if any required proof element is absent it is not a proven
        monetary correction -- makes this UNRESOLVED / manual review.
        """
        row = _root_row("2026-06-02-COL-LAA-TT-HOME-OVER")
        if row is None:
            pytest.skip("row not present in this checkout")
        assert not (row.get("marketTicker") or row.get("ticker"))
        auth = wss.authorize_root_ledger_settlement(
            row, schedule_games=_committed_schedule())
        assert auth["authorized"] is False
        assert auth["refusalReason"] == wss.SIDE_UNPROVEN_NO_CONTRACT
        assert auth["refusalClass"] == wss.REFUSAL_MISSING_EVIDENCE

    @pytest.mark.parametrize("bet_id,expected_side,expected_game_pk", [
        ("2026-09-07-174", "YES", "823742"),
        ("2026-09-07-175", "YES", "823742"),
        ("2026-09-10-180", "YES", "824550"),
    ])
    def test_the_three_contradictions_are_fully_proven(self, bet_id, expected_side,
                                                       expected_game_pk):
        """
        These three DO carry an exact ticker, and the corrected chain proves
        every link: contract, side, and a unique physical game. They remain
        genuine findings for adjudication -- with provenance now attached.
        """
        row = _root_row(bet_id)
        if row is None:
            pytest.skip("row not present in this checkout")
        auth = wss.authorize_root_ledger_settlement(
            row, schedule_games=_committed_schedule())
        assert auth["authorized"] is True, auth["refusalReason"]
        assert auth["side"] == expected_side
        assert auth["physicalGameKey"] == expected_game_pk
        assert auth["sideBasis"]
        assert auth["marketTicker"]

    def test_the_two_chc_mil_rows_bind_to_the_same_single_game(self):
        """They are the same physical game, so a doubleheader is not what went
        wrong there -- the ledger simply recorded the two sides swapped."""
        schedule = _committed_schedule()
        rows = [_root_row("2026-09-07-174"), _root_row("2026-09-07-175")]
        if any(r is None for r in rows):
            pytest.skip("rows not present in this checkout")
        keys = {wss.authorize_root_ledger_settlement(
            r, schedule_games=schedule)["physicalGameKey"] for r in rows}
        assert keys == {"823742"}
