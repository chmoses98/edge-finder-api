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
        # gamesPitched=1 positively verifies participation, so this
        # exercises the STAT-extraction "missing_innings_pitched" path
        # distinctly from the participation gate above it.
        "ID660271": {"person": {"id": 660271, "fullName": "Emmet Sheehan"},
                     "stats": {"pitching": {"gamesPitched": 1, "someOtherField": 1}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "missing_innings_pitched"


def test_ambiguous_player_is_unresolved():
    """
    Team resolution is strict (see test_ticker_team_not_matching_either_side_is_unresolved),
    so the only way genuine name-ambiguity can still occur is a data
    anomaly WITHIN one correctly-resolved team's own roster (e.g. a
    duplicate boxscore entry) -- modeled here with two "Chris Taylor"
    entries both on the correctly-resolved LAD side.
    """
    market = _market(title="Chris Taylor: 1+ hits?", family="hitter_hits",
                      ticker="KXMLBHIT-26AUG021920BOSLAD-LADCTAYLOR1-1")
    boxscore = _boxscore(
        home_players={
            "ID1": {"person": {"id": 1, "fullName": "Chris Taylor"}, "stats": {"batting": {"hits": 1}}},
            "ID2": {"person": {"id": 2, "fullName": "Chris Taylor"}, "stats": {"batting": {"hits": 2}}},
        },
    )
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD",
    )
    assert status == SETTLEMENT_UNRESOLVED
    assert reason == "player_not_resolved_ambiguous_candidates"
    assert len(evidence["candidates"]) == 2


def test_ticker_team_not_matching_either_side_is_unresolved():
    """
    GitHub issue #43 correction round: if the ticker's own team token
    doesn't match EITHER known side, the market is unresolved --
    never falls back to searching both rosters, and never settles
    merely because the title's player name happens to uniquely match
    somewhere in the game.
    """
    market = _market(title="Chris Taylor: 1+ hits?", family="hitter_hits",
                      ticker="KXMLBHIT-26AUG021920BOSLAD-LADCTAYLOR1-1")
    boxscore = _boxscore(
        home_players={"ID1": {"person": {"id": 1, "fullName": "Chris Taylor"}, "stats": {"batting": {"hits": 5}}}},
    )
    # Neither NYY nor TOR matches the ticker's "LAD" team token.
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", boxscore, away_abbr="NYY", home_abbr="TOR",
    )
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "player_prop_team_unresolved"
    assert evidence["resolutionStatus"] is None  # never even reached player resolution


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


# ── Parser-integrity gate (GitHub issue #43 correction round) ──────────────

def test_ticker_and_title_threshold_disagreement_is_unresolved():
    market = _market(
        ticker="KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9",
        title="Emmet Sheehan: 8+ strikeouts?",  # ticker says 9, title says 8
    )
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(9)), away_abbr="BOS", home_abbr="LAD",
    )
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "player_prop_threshold_mismatch"


def test_title_threshold_unparseable_is_unresolved():
    market = _market(title="Emmet Sheehan strikeouts galore")  # no "N+ stat?" shape at all
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(9)), away_abbr="BOS", home_abbr="LAD",
    )
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "player_prop_market_not_parseable"


def test_malformed_player_token_is_unresolved():
    # Team resolves fine ("LAD"), but the remainder "123" has no
    # letters at all -- not the expected {initial}{lastname}{digits} shape.
    market = _market(ticker="KXMLBKS-26AUG021920BOSLAD-LAD123-9", title="Someone: 9+ strikeouts?")
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(9)), away_abbr="BOS", home_abbr="LAD",
    )
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "player_prop_token_malformed"


def test_parenthetical_team_conflicting_with_ticker_team_is_unresolved():
    market = _market(
        ticker="KXMLBHIT-26AUG021920BOSLAD-LADMMUNCY13-1", family="hitter_hits",
        title="Max Muncy (BOS): 1+ hits?",  # parenthetical says BOS, ticker's own team token is LAD
    )
    boxscore = _boxscore(home_players={
        "ID1": {"person": {"id": 1, "fullName": "Max Muncy"}, "stats": {"batting": {"hits": 2}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "player_prop_parenthetical_team_conflict"


def test_stat_text_family_mismatch_is_unresolved():
    # KXMLBHIT / hitter_hits ticker, but the title's own wording describes
    # total bases -- the market family and its title text disagree.
    market = _market(
        ticker="KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-5", family="hitter_hits",
        title="Shohei Ohtani: 5+ total bases?",
    )
    boxscore = _boxscore(home_players={
        "ID1": {"person": {"id": 1, "fullName": "Shohei Ohtani"}, "stats": {"batting": {"hits": 5}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "player_prop_stat_text_family_mismatch"


def test_never_settles_merely_because_title_has_uniquely_matching_name():
    """
    A title with a name that uniquely matches a player somewhere in the
    game must NOT settle the market when a structural integrity check
    (here: team unresolved) already failed -- see
    test_ticker_team_not_matching_either_side_is_unresolved for the
    dedicated team-resolution case; this test exercises the same
    principle via a stat-text/family mismatch instead.
    """
    market = _market(
        ticker="KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-5", family="hitter_hits",
        title="Shohei Ohtani: 5+ total bases?",
    )
    boxscore = _boxscore(home_players={
        # Only one "Shohei Ohtani" exists anywhere in the game -- an
        # unrestricted match would happily resolve and settle.
        "ID1": {"person": {"id": 1, "fullName": "Shohei Ohtani"}, "stats": {"batting": {"hits": 5, "totalBases": 12}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert evidence["resolutionStatus"] is None  # never even reached player resolution


def test_all_seven_families_exact_title_wording_validated():
    cases = [
        ("pitcher_strikeouts", "KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9", "Emmet Sheehan: 9+ strikeouts?"),
        ("pitcher_outs", "KXMLBOUTS-26AUG021920BOSLAD-LADESHEEHAN80-17", "Emmet Sheehan: 17+ Outs Recorded?"),
        ("hitter_hits", "KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-2", "Shohei Ohtani: 2+ hits?"),
        ("hitter_total_bases", "KXMLBTB-26AUG021920BOSLAD-LADSOHTANI17-5", "Shohei Ohtani: 5+ total bases?"),
        ("hitter_hits_runs_rbis", "KXMLBHRR-26AUG021920BOSLAD-LADSOHTANI17-3", "Shohei Ohtani: 3+ hits + runs + RBIs?"),
        ("hitter_rbis", "KXMLBRBI-26AUG021920BOSLAD-LADSOHTANI17-2", "Shohei Ohtani: 2+ RBIs?"),
        ("hitter_stolen_bases", "KXMLBSB-26AUG021920BOSLAD-LADSOHTANI17-1", "Shohei Ohtani: 1+ stolen bases?"),
    ]
    boxscore = _boxscore(home_players={
        "ID1": {"person": {"id": 1, "fullName": "Emmet Sheehan"},
                "stats": {"pitching": {"strikeOuts": 9, "inningsPitched": "5.2"}}},
        "ID2": {"person": {"id": 2, "fullName": "Shohei Ohtani"},
                "stats": {"batting": {"gamesPlayed": 1, "hits": 3, "runs": 1, "rbi": 2, "doubles": 0, "triples": 0,
                                       "homeRuns": 0, "totalBases": 5, "stolenBases": 1}}},
    })
    for family, ticker, title in cases:
        market = _market(family=family, ticker=ticker, title=title)
        status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
        assert status == SETTLED, f"{family} unexpectedly unresolved: {reason}"
        assert result in ("YES", "NO")


def test_zero_stat_appearance_settles_as_no_not_unresolved():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    status, result, reason, evidence = settle_player_prop_market(
        market, "Final", _boxscore(home_players=_sheehan(0)), away_abbr="BOS", home_abbr="LAD",
    )
    assert (status, result, reason) == (SETTLED, "NO", None)
    assert evidence["actualValue"] == 0


# ── Participation verification (GitHub issue #43 correction round) ─────────

def test_player_listed_but_unused_is_unresolved_never_settled_as_no():
    """
    A player on the active roster who never entered the game: they ARE
    listed (name-matches), but their stat sub-object is entirely empty
    -- being listed is not proof of participation.
    """
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    boxscore = _boxscore(home_players={
        "ID660271": {"person": {"id": 660271, "fullName": "Emmet Sheehan"}, "jerseyNumber": "80",
                     "stats": {"pitching": {}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert result is None
    assert reason == "player_participation_unverified"
    assert evidence["participationStatus"] == "UNVERIFIED"


def test_official_pitcher_appearance_with_zero_strikeouts_settles_as_no():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    boxscore = _boxscore(home_players={
        "ID660271": {"person": {"id": 660271, "fullName": "Emmet Sheehan"},
                     "stats": {"pitching": {"gamesPitched": 1, "strikeOuts": 0, "inningsPitched": "1.0"}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert (status, result, reason) == (SETTLED, "NO", None)
    assert evidence["participationStatus"] == "RESOLVED"


def test_official_batter_appearance_with_zero_hits_settles_as_no():
    market = _market(title="Shohei Ohtani: 2+ hits?", family="hitter_hits",
                      ticker="KXMLBHIT-26AUG021920BOSLAD-LADSOHTANI17-2")
    boxscore = _boxscore(home_players={
        "ID1": {"person": {"id": 1, "fullName": "Shohei Ohtani"},
                "stats": {"batting": {"gamesPlayed": 1, "atBats": 4, "hits": 0}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert (status, result, reason) == (SETTLED, "NO", None)
    assert evidence["participationStatus"] == "RESOLVED"


def test_official_pinch_runner_appearance_with_zero_plate_appearances_settles():
    """A pinch-runner-only appearance (zero PA, zero AB) still counts as verified participation."""
    market = _market(title="Shohei Ohtani: 1+ stolen bases?", family="hitter_stolen_bases",
                      ticker="KXMLBSB-26AUG021920BOSLAD-LADSOHTANI17-1")
    boxscore = _boxscore(home_players={
        "ID1": {"person": {"id": 1, "fullName": "Shohei Ohtani"},
                "stats": {"batting": {"gamesPlayed": 1, "plateAppearances": 0, "atBats": 0, "stolenBases": 1}}},
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert (status, result, reason) == (SETTLED, "YES", None)


def test_missing_participation_fields_is_unresolved():
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")
    boxscore = _boxscore(home_players={
        "ID660271": {"person": {"id": 660271, "fullName": "Emmet Sheehan"}},  # no "stats" key at all
    })
    status, result, reason, evidence = settle_player_prop_market(market, "Final", boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status == SETTLEMENT_UNRESOLVED
    assert reason == "player_participation_unverified"


def test_dnp_never_automatically_becomes_no_or_void():
    """
    Neither "absent entirely" (test_player_zero_candidates_is_unresolved)
    nor "listed but unused" (above) ever produces a SETTLED result of
    any kind -- both are SETTLEMENT_UNRESOLVED, never NO and never VOID,
    since no verified Kalshi participation rule exists (see module
    docstring).
    """
    market = _market(title="Emmet Sheehan: 9+ strikeouts?")

    absent_boxscore = _boxscore(home_players={})
    status_absent, result_absent, _, _ = settle_player_prop_market(market, "Final", absent_boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status_absent not in ("SETTLED", "VOID")
    assert result_absent is None

    listed_unused_boxscore = _boxscore(home_players={
        "ID660271": {"person": {"id": 660271, "fullName": "Emmet Sheehan"}, "stats": {"pitching": {}}},
    })
    status_unused, result_unused, _, _ = settle_player_prop_market(market, "Final", listed_unused_boxscore, away_abbr="BOS", home_abbr="LAD")
    assert status_unused not in ("SETTLED", "VOID")
    assert result_unused is None


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
