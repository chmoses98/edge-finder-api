#!/usr/bin/env python3
"""
tests/edgelab/test_player_prop_settlement.py
=================================================
Coverage for lib/edgelab/player_prop_settlement.py (GitHub issue #43):
contract semantics (N+ / AT_LEAST, no push), game-final gating, player
resolution wiring, missing-statistic handling, and the (currently always
inert, since no ingestion path captures it) Kalshi-official-result
conflict path.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.player_prop_settlement import SETTLED, SETTLEMENT_UNRESOLVED, settle_player_prop_market


def _market(family="pitcher_strikeouts", ticker=None, title="Emmet Sheehan: 9+ strikeouts?"):
    ticker = ticker or "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9"
    parts = ticker.split("-")
    event_ticker = "-".join(parts[:2]) if len(parts) >= 2 else ticker
    return {
        "marketTicker": ticker,
        "eventTicker": event_ticker,
        "title": title,
        "marketFamily": family,
        "gameId": "12345",
    }


def _boxscore(home_players=None, away_players=None):
    return {
        "away": {"players": away_players or {}},
        "home": {"players": home_players or {}},
    }


def _sheehan(strikeouts):
    return {
        "ID660271": {
            "person": {"id": 660271, "fullName": "Emmet Sheehan"},
            "jerseyNumber": "80",
            "stats": {"pitching": {"strikeOuts": strikeouts, "inningsPitched": "6.0"}},
        }
    }


def test_actual_value_above_threshold_is_yes():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(11)), away_abbr="BOS", home_abbr="LAD",
    )
    assert (status, result, reason) == (SETTLED, "YES", None)
    assert evidence["actualValue"] == 11
    assert evidence["threshold"] == 9


def test_actual_value_equal_to_threshold_is_yes_never_a_push():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(9)), away_abbr="BOS", home_abbr="LAD",
    )
    assert (status, result) == (SETTLED, "YES")
    assert result != "PUSH"


def test_actual_value_below_threshold_is_no():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(8)), away_abbr="BOS", home_abbr="LAD",
    )
    assert (status, result, reason) == (SETTLED, "NO", None)


def test_no_push_result_ever_produced():
    for strikeouts in (0, 5, 9, 10, 100):
        market = _market(title="Emmet Sheehan: 9+ strikeouts?")
        _, result, _, _ = settle_player_prop_market(
            market, "Final", _boxscore(home_players=_sheehan(strikeouts)), away_abbr="BOS", home_abbr="LAD",
        )
        assert result in ("YES", "NO")


def test_non_final_game_is_unresolved():
    market = _market()
    for status in ("In Progress", "Suspended", "Delayed", "Postponed", None):
        s, result, reason, evidence = settle_player_prop_market(
            market, status, _boxscore(home_players=_sheehan(9)), away_abbr="BOS", home_abbr="LAD",
        )
        assert s == SETTLEMENT_UNRESOLVED
        assert result is None
        assert reason == "game_not_final"


def test_boxscore_fetch_failed_is_unresolved():
    market = _market()
    status, result, reason, evidence = settle_player_prop_market(market, "Final", {}, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert reason == "boxscore_fetch_failed"


def test_missing_statistic_is_unresolved():
    market = _market(family="pitcher_outs", ticker="KXMLBOUTS-26AUG021920BOSLAD-LADESHEEHAN80-17",
                      title="Emmet Sheehan: 17+ Outs Recorded?")
    boxscore = _boxscore(home_players={
        "ID660271": {"person": {"id": 660271, "fullName": "Emmet Sheehan"}, "stats": {"pitching": {"someOtherField": 1}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "missing_innings_pitched"


def test_ambiguous_player_is_unresolved():
    market = _market(title="Chris Taylor: 1+ hits?", family="hitter_hits",
                      ticker="KXMLBHIT-26AUG021920BOSLAD-LADCTAYLOR1-1")
    boxscore = _boxscore(
        away_players={"ID1": {"person": {"id": 1, "fullName": "Chris Taylor"}, "stats": {"batting": {"hits": 1}}}},
        home_players={"ID2": {"person": {"id": 2, "fullName": "Chris Taylor"}, "stats": {"batting": {"hits": 2}}}},
    )
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", boxscore, away_abbr="BOS", home_abbr="BOS",  # both sides "BOS" forces team ambiguity
    )
    assert status == SETTLEMENT_UNRESOLVED
    assert reason == "player_not_resolved_ambiguous_candidates"
    assert len(evidence["candidates"]) == 2


def test_player_zero_candidates_is_unresolved():
    market = _market()
    boxscore = _boxscore(home_players={
        "ID1": {"person": {"id": 1, "fullName": "Somebody Else"}, "stats": {"pitching": {"strikeOuts": 5}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert reason == "player_not_resolved_zero_candidates"


def test_unverified_dnp_rule_leaves_unresolved_never_void_or_no():
    """
    A player entirely absent from the final boxscore (did not
    participate) must never be auto-assigned NO/VOID -- this
    repository has no verified Kalshi participation rule (see module
    docstring), so it is always SETTLEMENT_UNRESOLVED via the same
    "zero candidates" path as any other absent player.
    """
    market = _market()
    boxscore = _boxscore(home_players={})  # Sheehan didn't pitch / isn't in the boxscore at all
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "player_not_resolved_zero_candidates"


def test_verified_kalshi_result_agreeing_settles_normally():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(11)), away_abbr="BOS", home_abbr="LAD",
        kalshi_official_result="YES",
    )
    assert (status, result, reason) == (SETTLED, "YES", None)
    assert evidence["kalshiOfficialResult"] == "YES"


def test_kalshi_and_mlb_result_conflict_is_unresolved_preserving_both():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(11)), away_abbr="BOS", home_abbr="LAD",
        kalshi_official_result="NO",  # MLB stats say YES (11 >= 9) -- conflict
    )
    assert status == SETTLEMENT_UNRESOLVED
    assert reason == "kalshi_mlb_result_conflict"
    assert evidence["kalshiOfficialResult"] == "NO"
    assert evidence["actualValue"] == 11  # MLB-derived evidence preserved alongside the conflicting Kalshi value


def test_unrecognized_family_is_unresolved():
    market = _market(family="not_a_real_family")
    status, result, reason, evidence = settle_player_prop_market(market, "Final", _boxscore(), away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert reason == "unrecognized_player_prop_family"


def test_unparseable_market_is_unresolved():
    market = _market(ticker="totally-not-a-valid-ticker")
    status, result, reason, evidence = settle_player_prop_market(market, "Final", _boxscore(home_players=_sheehan(9)), away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert reason == "player_prop_market_not_parseable"


def test_zero_stat_appearance_settles_as_no_not_unresolved():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(0)), away_abbr="BOS", home_abbr="LAD",
    )
    assert (status, result, reason) == (SETTLED, "NO", None)
    assert evidence["actualValue"] == 0


def test_evidence_always_a_dict_never_none():
    market = _market()
    for game_status in ("Final", "In Progress", None):
        _, _, _, evidence = settle_player_prop_market(market, game_status, {}, away_abbr="BOS", home_abbr="LAD")
        assert isinstance(evidence, dict)


def test_evidence_records_player_identity_and_provenance():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    fetch_meta = {"gamePk": 999, "sourceEndpoint": "x", "sourcePayloadHash": "abc", "fetchedAt": "2026-08-03T00:00:00Z"}
    _, _, _, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(9)), away_abbr="BOS", home_abbr="LAD", fetch_meta=fetch_meta,
    )
    assert evidence["playerId"] == 660271
    assert evidence["playerName"] == "Emmet Sheehan"
    assert evidence["gamePk"] == 999
    assert evidence["sourcePayloadHash"] == "abc"
    assert evidence["comparisonOperator"] == "AT_LEAST"
