"""Exact Kalshi MLB event identity, including doubleheaders.

Regression fixtures for the four events the MLB-ALPHA-0001 blind holdout
had to exclude because the research parser could not read Kalshi's G1/G2
marker. Resolving them is DATA IDENTITY INFRASTRUCTURE and changes no
trading rule.
"""

import pytest

from lib.edgelab.mlb_alpha_identity import (
    parse_event_ticker, resolve_game_pk, split_team_pair,
    STATUS_RESOLVED, STATUS_UNRESOLVED,
)

# MLB team ids: BOS 111, NYY 147, AZ 109, SF 137, SEA 136, HOU 117
BOS, NYY, AZ, SF, SEA, HOU = 111, 147, 109, 137, 136, 117


def _g(pk, away, home, number=None, start=None):
    return {"gamePk": pk, "awayTeamId": away, "homeTeamId": home,
            "gameNumber": number, "scheduledStart": start}


# --------------------------------------------------------------- single game
def test_single_game_resolves_exactly():
    r = parse_event_ticker("KXMLBF5TOTAL-26AUG161920SEAHOU")
    assert r["status"] == STATUS_RESOLVED
    assert (r["awayTeam"], r["homeTeam"]) == ("SEA", "HOU")
    assert r["doubleheaderGame"] is None
    assert r["gameDate"] == "2026-08-16"
    assert r["scheduledStartUtc"].isoformat() == "2026-08-16T23:20:00"


def test_single_game_matches_the_only_scheduled_matchup():
    ident = parse_event_ticker("KXMLBF5TOTAL-26AUG161920SEAHOU")
    pk, reason = resolve_game_pk(ident, [_g(700001, SEA, HOU)])
    assert (pk, reason) == (700001, "unique_matchup")


# ---------------------------------------------------------- doubleheader G1/G2
@pytest.mark.parametrize("ticker,dh,start", [
    ("KXMLBF5TOTAL-26AUG291305BOSNYYG1", 1, "2026-08-29T17:05:00"),
    ("KXMLBF5TOTAL-26AUG291915BOSNYYG2", 2, "2026-08-29T23:15:00"),
])
def test_doubleheader_marker_is_parsed_not_rejected(ticker, dh, start):
    r = parse_event_ticker(ticker)
    assert r["status"] == STATUS_RESOLVED
    assert (r["awayTeam"], r["homeTeam"]) == ("BOS", "NYY")
    assert r["doubleheaderGame"] == dh
    assert r["scheduledStartUtc"].isoformat() == start


def test_doubleheader_resolves_by_game_number():
    sched = [_g(800001, BOS, NYY, number=1), _g(800002, BOS, NYY, number=2)]
    g1 = resolve_game_pk(parse_event_ticker("KXMLBF5TOTAL-26AUG291305BOSNYYG1"), sched)
    g2 = resolve_game_pk(parse_event_ticker("KXMLBF5TOTAL-26AUG291915BOSNYYG2"), sched)
    assert g1 == (800001, "doubleheader_resolved_by_game_number")
    assert g2 == (800002, "doubleheader_resolved_by_game_number")


def test_doubleheader_falls_back_to_start_time_when_game_number_absent():
    sched = [_g(800001, BOS, NYY, start="2026-08-29T17:05:00Z"),
             _g(800002, BOS, NYY, start="2026-08-29T23:15:00Z")]
    pk, reason = resolve_game_pk(parse_event_ticker("KXMLBF5TOTAL-26AUG291915BOSNYYG2"), sched)
    assert (pk, reason) == (800002, "doubleheader_resolved_by_start_time")


def test_the_other_holdout_doubleheader_pair_also_resolves():
    for ticker, dh in (("KXMLBF5TOTAL-26AUG291605AZSFG1", 1),
                       ("KXMLBF5TOTAL-26AUG292205AZSFG2", 2)):
        r = parse_event_ticker(ticker)
        assert r["status"] == STATUS_RESOLVED
        assert (r["awayTeam"], r["homeTeam"]) == ("AZ", "SF")
        assert r["doubleheaderGame"] == dh


# ------------------------------------------------------------------- refusals
def test_ambiguous_identity_is_refused_never_guessed():
    """Two same-matchup games with neither a game number nor a usable start
    must REFUSE, not pick one."""
    sched = [_g(800001, BOS, NYY), _g(800002, BOS, NYY)]
    ident = parse_event_ticker("KXMLBF5TOTAL-26AUG291305BOSNYY")   # no G marker
    pk, reason = resolve_game_pk(ident, sched)
    assert pk is None
    assert reason.startswith("ambiguous_2_candidates_refused")


def test_start_time_outside_tolerance_does_not_force_a_match():
    sched = [_g(800001, BOS, NYY, start="2026-08-29T17:05:00Z"),
             _g(800002, BOS, NYY, start="2026-08-29T17:20:00Z")]
    ident = parse_event_ticker("KXMLBF5TOTAL-26AUG292315BOSNYY")
    pk, reason = resolve_game_pk(ident, sched, start_tolerance_minutes=5)
    assert pk is None and "refused" in reason


def test_unknown_team_blob_is_unresolved():
    r = parse_event_ticker("KXMLBF5TOTAL-26AUG291915ZZZQQQ")
    assert r["status"] == STATUS_UNRESOLVED
    assert r["unresolvedReason"].startswith("no_valid_team_split")


def test_malformed_ticker_is_unresolved():
    for bad in ("", None, "NOT-A-TICKER", "KXMLBF5TOTAL-26XXX291915BOSNYY"):
        assert parse_event_ticker(bad)["status"] == STATUS_UNRESOLVED


def test_unresolved_identity_never_yields_a_game_pk():
    pk, reason = resolve_game_pk(parse_event_ticker("garbage"), [_g(1, BOS, NYY)])
    assert (pk, reason) == (None, "identity_unresolved")


def test_team_split_helper_rejects_and_reports_ambiguity_honestly():
    assert split_team_pair("BOSNYY") == [("BOS", "NYY")]
    assert split_team_pair("ZZZQQQ") == []
