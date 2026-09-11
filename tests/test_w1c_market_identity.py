#!/usr/bin/env python3
"""
tests/test_w1c_market_identity.py
=================================
W1-C — canonical market identity + doubleheader safety.

The audit's CR-3 finding, restated as the thing that must become impossible:
on 2026-07-11 the exact Kalshi contract

    KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4

was attached to gamePk 823357 AND to gamePk 823356 -- two physically different
baseball games. On 2026-06-17 both SF@ATL legs were assigned the same
`kalshiKey` and the same event time, and one leg ended up with no contracts at
all because the registry key `f"{away}{home}"` let the second overwrite the
first.

These are FORWARD-LOOKING invariants on current code. The historical artifacts
stay contaminated -- they are the evidence -- and are read here read-only,
never rewritten.
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.edgelab import market_identity as mi   # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def _game(pk, start=None, leg=None, **kw):
    g = {"gamePk": pk}
    if start:
        g["scheduledStartTime"] = start
    if leg is not None:
        g["doubleheaderGameNumber"] = leg
    g.update(kw)
    return g


# ── the physical anchor ──────────────────────────────────────────────────────

def test_the_physical_anchor_is_the_gamepk():
    assert mi.physical_game_key({"gamePk": 824912}) == "824912"
    assert mi.physical_game_key({"gameId": 824913}) == "824913"
    assert mi.physical_game_key({}) is None
    assert mi.physical_game_key({"gamePk": None}) is None


def test_a_team_pair_is_never_a_physical_game_key():
    """`kalshiKey` names a matchup, not a game. It must not satisfy the anchor."""
    assert mi.physical_game_key({"kalshiKey": "MILPIT"}) is None
    assert mi.physical_game_key({"away": "MIL", "home": "PIT",
                                 "date": "2026-07-11"}) is None


# ── ordinary single game ─────────────────────────────────────────────────────

def test_an_ordinary_single_game_resolves():
    g = _game(824000, "2026-09-10T23:05:00Z")
    got, outcome, ev = mi.resolve_physical_game([g], event_time_hhmm="1905")
    assert outcome == mi.IDENTITY_PROVEN and got is g
    assert ev["basis"] == "SINGLE_CANDIDATE_FOR_DATE_AND_TEAMS"


def test_no_candidate_refuses():
    got, outcome, _ = mi.resolve_physical_game([], event_time_hhmm="1905")
    assert got is None
    assert outcome == mi.IDENTITY_REFUSED_NO_PHYSICAL_GAME_MATCH


# ── doubleheaders ────────────────────────────────────────────────────────────

def test_a_doubleheader_with_distinct_starts_resolves_to_the_right_leg():
    # MLB records UTC; Kalshi's ticker records EASTERN. 20:05Z IS 16:05 ET,
    # so the leg matching event '1605' is the one starting at 20:05Z. Reading
    # the Z-clock as if it were Eastern picks the OTHER leg -- confidently,
    # uniquely and wrongly -- which is what an earlier revision of
    # _start_hhmm did, on this exact matchup.
    leg1 = _game(823357, "2026-07-11T16:05:00Z")   # 12:05 PM ET
    leg2 = _game(823356, "2026-07-11T20:05:00Z")   # 16:05 PM ET
    got, outcome, ev = mi.resolve_physical_game([leg1, leg2], event_time_hhmm="1605")
    assert outcome == mi.IDENTITY_PROVEN
    assert mi.physical_game_key(got) == "823356"
    assert ev["basis"] == "UNIQUE_CLOSEST_SCHEDULED_START"
    assert ev["distanceMinutes"] == 0, "an exact event-time match, not a near one"

    got2, outcome2, _ = mi.resolve_physical_game([leg1, leg2], event_time_hhmm="1205")
    assert outcome2 == mi.IDENTITY_PROVEN
    assert mi.physical_game_key(got2) == "823357"


def test_an_explicit_leg_number_settles_it():
    legs = [_game(823357, "2026-07-11T16:05:00Z", leg=1),
            _game(823356, "2026-07-11T20:05:00Z", leg=2)]
    got, outcome, ev = mi.resolve_physical_game(legs, doubleheader_game_number=2)
    assert outcome == mi.IDENTITY_PROVEN
    assert mi.physical_game_key(got) == "823356"
    assert ev["basis"] == "DOUBLEHEADER_GAME_NUMBER"


def test_reordering_the_candidates_does_not_change_the_answer():
    """
    Order-independence is the whole difference between a resolution and a
    coincidence. The old join iterated a `set`, whose order is arbitrary.
    """
    a = _game(823357, "2026-07-11T16:05:00Z")   # 12:05 PM ET
    b = _game(823356, "2026-07-11T20:05:00Z")   # 16:05 PM ET
    for order in ([a, b], [b, a]):
        got, outcome, _ = mi.resolve_physical_game(order, event_time_hhmm="1605")
        assert outcome == mi.IDENTITY_PROVEN
        assert mi.physical_game_key(got) == "823356", "order changed the mapping"


def test_identical_start_times_refuse_rather_than_pick_one():
    """
    THE case. Two legs, same teams, same date, same recorded start -- exactly
    what the 2026-06-17 archive shows, where both legs carried '7:15 PM ET'.
    There is no evidence here that distinguishes them, so there is no answer,
    and inventing leg 1 vs leg 2 is precisely the defect.
    """
    legs = [_game(824912, "2026-06-17T19:15:00Z"),
            _game(824913, "2026-06-17T19:15:00Z")]
    got, outcome, ev = mi.resolve_physical_game(legs, event_time_hhmm="1915")
    assert got is None
    assert outcome == mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME
    assert ev["candidateCount"] == 2


def test_a_doubleheader_with_no_usable_time_refuses():
    legs = [_game(823356), _game(823357)]
    got, outcome, _ = mi.resolve_physical_game(legs, event_time_hhmm="1605")
    assert got is None and outcome == mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME


def test_a_doubleheader_with_no_event_time_at_all_refuses():
    legs = [_game(823356, "2026-07-11T16:05:00Z"),
            _game(823357, "2026-07-11T19:35:00Z")]
    got, outcome, _ = mi.resolve_physical_game(legs, event_time_hhmm=None)
    assert got is None and outcome == mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME


def test_a_leg_number_that_matches_nothing_does_not_fall_back_to_guessing():
    legs = [_game(823356, "2026-07-11T16:05:00Z", leg=1),
            _game(823357, "2026-07-11T19:35:00Z", leg=2)]
    got, outcome, _ = mi.resolve_physical_game(legs, doubleheader_game_number=3)
    assert got is None and outcome == mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME


def test_a_doubleheader_that_crosses_eastern_midnight_still_resolves():
    """
    A real shape from the archive: 2026-08-29 AZ@SF ran 20:05 ET and 02:05 ET
    the NEXT Eastern day. The UTC calendar date does not move between them but
    the Eastern one does, so anything that reads the UTC clock as Eastern gets
    both the time AND the day wrong.
    """
    legs = [_game(824100, "2026-08-30T00:05:00Z"),   # 20:05 ET, Aug 29
            _game(824101, "2026-08-30T06:05:00Z")]   # 02:05 ET, Aug 30
    assert mi._start_hhmm(legs[0]) == "2005"
    assert mi._start_hhmm(legs[1]) == "0205"

    got, outcome, ev = mi.resolve_physical_game(legs, event_time_hhmm="0205")
    assert outcome == mi.IDENTITY_PROVEN
    assert mi.physical_game_key(got) == "824101"
    assert ev["distanceMinutes"] == 0

    got2, outcome2, _ = mi.resolve_physical_game(legs, event_time_hhmm="2005")
    assert outcome2 == mi.IDENTITY_PROVEN
    assert mi.physical_game_key(got2) == "824100"


# ── ticker exclusivity ───────────────────────────────────────────────────────

def test_a_ticker_claimed_by_two_gamepks_is_a_violation():
    violations = mi.assert_ticker_exclusivity([
        ("KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", "823357"),
        ("KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", "823356"),
        ("KXMLBGAME-26JUL111605MILPIT-MIL", "823356"),
    ])
    assert len(violations) == 1
    assert violations[0]["marketTicker"] == "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4"
    assert violations[0]["gamePks"] == ["823356", "823357"]


def test_clean_assignments_have_no_violations():
    assert mi.assert_ticker_exclusivity([
        ("KXMLBGAME-26JUL111605MILPIT-MIL", "823356"),
        ("KXMLBGAME-26JUL111935MILPIT-MIL", "823357"),
    ]) == []


# ── registry keying ──────────────────────────────────────────────────────────

def test_the_registry_key_is_never_a_bare_team_pair():
    assert mi.registry_key(game_key="823356") == "gamePk:823356"
    assert (mi.registry_key(event_ticker_suffix="26JUL111605MILPIT")
            == "event:26JUL111605MILPIT")
    assert mi.registry_key() is None


def test_doubleheader_legs_get_distinct_registry_keys():
    """
    `f"{away}{home}"` gave both legs the key 'MILPIT' and the second silently
    overwrote the first. Both available keying strategies keep them apart.
    """
    by_pk = {mi.registry_key(game_key=k) for k in ("823356", "823357")}
    by_event = {mi.registry_key(event_ticker_suffix=s)
                for s in ("26JUL111605MILPIT", "26JUL111935MILPIT")}
    assert len(by_pk) == 2
    assert len(by_event) == 2


# ── contract semantics ───────────────────────────────────────────────────────

def test_moneyline_needs_a_selection_and_a_side():
    ident, outcome = mi.resolve_contract(
        "KXMLBGAME-26SEP102140BOSNYY-BOS", selection="BOS", side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_PROVEN
    assert ident["horizon"] == mi.HORIZON_FULL_GAME
    assert ident["family"] == "MONEYLINE"

    _, outcome = mi.resolve_contract("KXMLBGAME-26SEP102140BOSNYY-BOS",
                                     side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE


def test_run_line_needs_team_threshold_and_direction():
    ident, outcome = mi.resolve_contract(
        "KXMLBSPREAD-26SEP102140BOSNYY-BOS2", selection="BOS",
        threshold=1.5, direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_PROVEN and ident["family"] == "RUN_LINE"

    ident, outcome = mi.resolve_contract(
        "KXMLBSPREAD-26SEP102140BOSNYY-BOS2", selection="BOS", side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE
    assert set(ident["missing"]) == {"threshold", "direction"}


def test_game_total_needs_direction_and_threshold():
    ident, outcome = mi.resolve_contract(
        "KXMLBTOTAL-26SEP102140BOSNYY-9", threshold=9,
        direction=mi.DIRECTION_OVER, side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_PROVEN and ident["family"] == "GAME_TOTAL"

    _, outcome = mi.resolve_contract("KXMLBTOTAL-26SEP102140BOSNYY-9",
                                     threshold=9, side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE, (
        "OVER and UNDER on the same strike are opposite trades")


def test_team_total_needs_team_direction_and_threshold():
    ident, outcome = mi.resolve_contract(
        "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", selection="MIL",
        threshold=4, direction=mi.DIRECTION_OVER, side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_PROVEN and ident["family"] == "TEAM_TOTAL"
    for missing in ({"selection": None}, {"threshold": None}, {"direction": None}):
        kw = dict(selection="MIL", threshold=4, direction=mi.DIRECTION_OVER,
                  side=mi.SIDE_YES)
        kw.update(missing)
        _, outcome = mi.resolve_contract(
            "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", **kw)
        assert outcome == mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE


def test_horizons_are_isolated_per_series():
    for ticker, horizon in (
        ("KXMLBGAME-26SEP102140BOSNYY-BOS", mi.HORIZON_FULL_GAME),
        ("KXMLBF3-26SEP102140BOSNYY-BOS", mi.HORIZON_F3),
        ("KXMLBF5-26SEP102140BOSNYY-BOS", mi.HORIZON_F5),
        ("KXMLBF7-26SEP102140BOSNYY-BOS", mi.HORIZON_F7),
        ("KXMLBRFI-26SEP102140BOSNYY", mi.HORIZON_FIRST_INNING),
    ):
        ident, _ = mi.resolve_contract(ticker, selection="BOS",
                                       direction=mi.DIRECTION_WIN,
                                       side=mi.SIDE_YES)
        assert ident["horizon"] == horizon, ticker


def test_f5_and_full_game_are_not_interchangeable():
    f5, _ = mi.resolve_contract("KXMLBF5-26SEP102140BOSNYY-BOS",
                                selection="BOS", side=mi.SIDE_YES)
    full, _ = mi.resolve_contract("KXMLBGAME-26SEP102140BOSNYY-BOS",
                                  selection="BOS", side=mi.SIDE_YES)
    assert f5["horizon"] != full["horizon"]
    assert f5["marketTicker"] != full["marketTicker"]


def test_nrfi_and_yrfi_are_one_contract_two_sides():
    """One binary market. YES = a run scores (YRFI); NO = none does (NRFI)."""
    ticker = "KXMLBRFI-26SEP102140BOSNYY"
    yrfi, o1 = mi.resolve_contract(ticker, direction=mi.DIRECTION_EVENT_OCCURS,
                                   side=mi.SIDE_YES)
    nrfi, o2 = mi.resolve_contract(ticker,
                                   direction=mi.DIRECTION_EVENT_DOES_NOT_OCCUR,
                                   side=mi.SIDE_NO)
    assert o1 == o2 == mi.IDENTITY_PROVEN
    assert yrfi["marketTicker"] == nrfi["marketTicker"]
    assert yrfi["side"] != nrfi["side"]
    assert yrfi["horizon"] == nrfi["horizon"] == mi.HORIZON_FIRST_INNING


def test_the_side_is_required_for_every_family():
    for ticker, kw in (
        ("KXMLBGAME-26SEP102140BOSNYY-BOS", {"selection": "BOS"}),
        ("KXMLBTOTAL-26SEP102140BOSNYY-9",
         {"threshold": 9, "direction": mi.DIRECTION_OVER}),
        ("KXMLBRFI-26SEP102140BOSNYY", {"direction": mi.DIRECTION_EVENT_OCCURS}),
    ):
        ident, outcome = mi.resolve_contract(ticker, **kw)
        assert outcome == mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE
        assert "side" in ident["missing"]


def test_a_zero_threshold_is_a_real_strike():
    """0 is a strike; only None is missing. Truthiness would refuse a real
    contract and, worse, teach someone to use `or` here."""
    _, outcome = mi.resolve_contract(
        "KXMLBTEAMTOTAL-26SEP102140BOSNYY-BOS0", selection="BOS", threshold=0,
        direction=mi.DIRECTION_OVER, side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_PROVEN


def test_an_unknown_family_refuses_rather_than_defaulting_to_moneyline():
    _, outcome = mi.resolve_contract("KXNFLGAME-26SEP10-XYZ", selection="XYZ",
                                     side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_REFUSED_UNKNOWN_FAMILY
    _, outcome = mi.resolve_contract(None, side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_REFUSED_NO_CONTRACT


def test_player_prop_series_are_known_but_still_need_their_strike():
    ident, outcome = mi.resolve_contract("KXMLBKS-26SEP102140BOSNYY-GRAY6",
                                         selection="GRAY", threshold=6,
                                         side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_PROVEN
    assert ident["horizon"] == mi.HORIZON_PLAYER_PROP


# ── the historical contamination, read-only ──────────────────────────────────

HISTORICAL = {
    "2026-06-17": ("data/slates/2026-06-17/official_20260617T181908Z.json",
                   {"SF", "ATL"}),
    "2026-07-11": ("data/slates/2026-07-11/official_20260711T211016Z.json",
                   {"MIL", "PIT"}),
}


def _legs(path, teams):
    with open(os.path.join(ROOT, path)) as handle:
        doc = json.load(handle)
    games = doc.get("games") or (doc.get("data") or {}).get("games") or []
    out = []
    for g in games:
        away, home = g.get("away"), g.get("home")
        away = away.get("abbr") if isinstance(away, dict) else away
        home = home.get("abbr") if isinstance(home, dict) else home
        if {str(away), str(home)} == teams:
            out.append(g)
    return out


@pytest.mark.parametrize("date", sorted(HISTORICAL))
def test_the_contaminated_dates_really_do_hold_two_distinct_gamepks(date):
    path, teams = HISTORICAL[date]
    if not os.path.exists(os.path.join(ROOT, path)):
        pytest.skip("archived slate not present in this checkout")
    legs = _legs(path, teams)
    assert len(legs) == 2, "%s should be a doubleheader" % date
    keys = {mi.physical_game_key(g) for g in legs}
    assert len(keys) == 2, "the two legs must have distinct gamePks: %r" % keys


@pytest.mark.parametrize("date", sorted(HISTORICAL))
def test_the_legs_shared_one_team_pair_key_which_is_the_defect(date):
    path, teams = HISTORICAL[date]
    if not os.path.exists(os.path.join(ROOT, path)):
        pytest.skip("archived slate not present in this checkout")
    legs = _legs(path, teams)
    kalshi_keys = {g.get("kalshiKey") for g in legs}
    assert len(kalshi_keys) == 1, (
        "historical evidence: both legs shared one kalshiKey %r" % kalshi_keys)
    assert all(mi.physical_game_key(g) is not None for g in legs), (
        "the gamePk that could have told them apart was present all along")


def test_the_2026_07_11_ticker_collision_is_detected_by_the_new_invariant():
    """
    The exact archived contamination, run through the forward-looking check.
    Two team-total contracts were attached to both physical games; the
    exclusivity invariant must name them.
    """
    path, teams = HISTORICAL["2026-07-11"]
    if not os.path.exists(os.path.join(ROOT, path)):
        pytest.skip("archived slate not present in this checkout")
    legs = _legs(path, teams)
    assignments = []
    for g in legs:
        key = mi.physical_game_key(g)
        for row in (g.get("marketLedger") or []):
            ticker = row.get("marketTicker") or row.get("ticker")
            if ticker:
                assignments.append((ticker, key))
    violations = mi.assert_ticker_exclusivity(assignments)
    assert violations, "the archived collision must still be detectable"
    offending = {v["marketTicker"] for v in violations}
    assert "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4" in offending
    for v in violations:
        assert len(v["gamePks"]) == 2


_TICKER_SUFFIX = re.compile(r"-(\d{2}[A-Z]{3}\d{2})(\d{4})([A-Z]{2,3})([A-Z]{2,3})")


def _archived_event_times(game):
    """Every Kalshi EVENT time recoverable from a game's raw ticker suffixes.

    The registry parsed this out of the event suffix and then threw it away
    (trace §2a). It is still sitting in the tickers themselves, which is why
    the archive is more recoverable than `kalshiGameTime` alone suggests.
    """
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if "ticker" in key.lower() and isinstance(value, str):
                    match = _TICKER_SUFFIX.search(value)
                    if match:
                        found.add(match.group(2))
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk((game.get("odds") or {}).get("kalshi") or {})
    walk({"rows": game.get("marketLedger") or []})
    return found


def _authoritative(date, teams):
    path = os.path.join(ROOT, "data", "slates", date, "authoritative.json")
    if not os.path.exists(path):
        pytest.skip("archived slate not present in this checkout")
    with open(path) as handle:
        doc = json.load(handle)
    out = []
    for g in (doc.get("games") or []):
        away, home = g.get("away"), g.get("home")
        away = away.get("abbr") if isinstance(away, dict) else away
        home = home.get("abbr") if isinstance(home, dict) else home
        if {str(away), str(home)} == teams:
            out.append(g)
    return out


@pytest.mark.parametrize("date,teams,event_hhmm,correct_leg", [
    ("2026-06-17", {"SF", "ATL"}, "1915", "824913"),
    ("2026-07-11", {"MIL", "PIT"}, "1605", "823356"),
])
def test_the_archived_legs_resolve_to_exactly_one_game_under_the_new_code(
        date, teams, event_hhmm, correct_leg):
    """
    The two mandated reproductions, resolved rather than assumed.

    In the archive BOTH legs carry the identical set of tickers -- that is the
    contamination. Every one of those tickers encodes the same Kalshi event
    time, and the MLB scheduled starts of the two legs are hours apart, so the
    current resolver assigns them to exactly ONE physical game.

    The leg it picks is the one whose EASTERN start matches, because Kalshi
    writes Eastern into the ticker and MLB records UTC. On 2026-06-17 the legs
    are 18:00Z (2:00 PM ET) and 23:15Z (7:15 PM ET) and the event says 1915 --
    read as UTC that picks the 18:00Z leg by 75 minutes, confidently and
    wrongly. The assertion on distanceMinutes below is what makes that
    impossible to reintroduce: a correct read lands EXACTLY on the event time.
    """
    legs = _authoritative(date, teams)
    assert len(legs) == 2, "%s should be a doubleheader" % date

    times = set()
    for leg in legs:
        times |= _archived_event_times(leg)
    assert times == {event_hhmm}, (
        "only one leg's Kalshi event survives in the archive: %r" % sorted(times))

    candidates = [{"gamePk": mi.physical_game_key(g),
                   "scheduledStartTime": g.get("startTime")} for g in legs]
    got, outcome, evidence = mi.resolve_physical_game(
        candidates, event_time_hhmm=event_hhmm)
    assert outcome == mi.IDENTITY_PROVEN
    assert mi.physical_game_key(got) == correct_leg
    assert evidence["distanceMinutes"] == 0, (
        "an EXACT Eastern match, not a near one -- a non-zero distance here "
        "means the UTC clock is being compared to Kalshi's Eastern clock")


@pytest.mark.parametrize("date,teams", [
    ("2026-06-17", {"SF", "ATL"}),
    ("2026-07-11", {"MIL", "PIT"}),
])
def test_the_archived_display_time_alone_cannot_distinguish_the_legs(date, teams):
    """
    The honest negative result, stated about the right field.

    `kalshiGameTime` is the collapsed display copy the registry wrote, and BOTH
    legs carry the same value -- so anything relying on it alone has no way to
    tell them apart and must refuse. The underlying evidence is richer (the
    test above resolves it), but code that reads only this field is blind, and
    blind must mean REFUSE rather than "leg 1".
    """
    legs = _authoritative(date, teams)
    display = {g.get("kalshiGameTime") for g in legs}
    assert len(display) == 1, "both legs recorded the same display time"

    # A resolver given ONLY that field, for both legs, has nothing to work with.
    got, outcome, _ = mi.resolve_physical_game(
        [{"gamePk": mi.physical_game_key(g),
          "scheduledTimeStr": "1915"} for g in legs],
        event_time_hhmm="1915")
    assert got is None
    assert outcome == mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME


def test_kalshi_event_time_is_eastern_and_mlb_start_time_is_utc():
    """
    The unit-level form of the bug above. 20:05Z is 16:05 Eastern, and the
    Kalshi ticker for that game says '1605'. If these two ever stop agreeing,
    every doubleheader assignment in the system silently moves one leg over.
    """
    assert mi._start_hhmm({"scheduledStartTime": "2026-07-11T20:05:00Z"}) == "1605"
    assert mi._start_hhmm({"scheduledStartTime": "2026-07-11T16:05:00Z"}) == "1205"
    # A bare 4-digit field is already Eastern by lib/kalshi_ticker_time's
    # contract and is passed through untouched.
    assert mi._start_hhmm({"scheduledTimeStr": "1915"}) == "1915"
    assert mi._start_hhmm({}) is None
    assert mi._start_hhmm({"scheduledStartTime": "not a timestamp"}) is None


# ── W1-C composes with B2: identity and price are BOTH required ─────────────
#
# The failure mode this guards is seductive: a perfectly good order book
# exists, B2 proves an executable price from it, and the row goes actionable
# for a contract nobody could name. Knowing the price of something you cannot
# identify is not knowing anything.

from lib.edgelab import canonical_price as cp            # noqa: E402
from lib.edgelab import production_price as pp           # noqa: E402

_FRESH = "2026-09-10T11:59:30Z"
_DECIDED = "2026-09-10T12:00:00Z"


def _priced():
    """A B2 price that is genuinely, unimpeachably proven."""
    return pp.price_contract(
        market_ticker="KXMLBTOTAL-26SEP102140BOSNYY-9", side=cp.SIDE_YES,
        yes_bid="0.44", yes_ask="0.46", unit=cp.UNIT_DOLLARS,
        captured_at=_FRESH, decided_at=_DECIDED)


def test_a_proven_price_does_not_rescue_an_unproven_contract():
    price = _priced()
    assert price["actionable"] is True, "the price side really is proven"

    # ... and the very same ticker, with no direction, is not identifiable:
    # OVER 9 and UNDER 9 are opposite trades at the same strike.
    _, outcome = mi.resolve_contract("KXMLBTOTAL-26SEP102140BOSNYY-9",
                                     threshold=9, side=mi.SIDE_YES)
    assert outcome == mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE
    assert not mi.is_proven(outcome)
    actionable = price["actionable"] and mi.is_proven(outcome)
    assert actionable is False, (
        "a book must never make an unidentifiable contract tradable")


def test_a_proven_contract_does_not_rescue_an_unproven_price():
    """The converse, so the composition is symmetric and neither side is
    load-bearing alone."""
    _, outcome = mi.resolve_contract(
        "KXMLBTOTAL-26SEP102140BOSNYY-9", threshold=9,
        direction=mi.DIRECTION_OVER, side=mi.SIDE_YES)
    assert mi.is_proven(outcome)
    price = pp.price_contract(
        market_ticker="KXMLBTOTAL-26SEP102140BOSNYY-9", side=cp.SIDE_YES,
        yes_bid="0.44", yes_ask="0.46", unit=cp.UNIT_DOLLARS,
        captured_at=None, decided_at=_DECIDED)          # age unprovable
    assert price["actionable"] is False
    assert (price["actionable"] and mi.is_proven(outcome)) is False


def test_both_proven_is_the_only_actionable_combination():
    price = _priced()
    _, outcome = mi.resolve_contract(
        "KXMLBTOTAL-26SEP102140BOSNYY-9", threshold=9,
        direction=mi.DIRECTION_OVER, side=mi.SIDE_YES)
    assert price["actionable"] and mi.is_proven(outcome)


def test_an_ambiguous_physical_game_blocks_a_perfectly_priced_contract():
    """
    The doubleheader case end to end: the contract semantics are complete and
    the book is fresh and two-sided, but we cannot say WHICH baseball game it
    settles on. That must not trade.
    """
    price = _priced()
    legs = [_game(824912, "2026-06-17T19:15:00Z"),
            _game(824913, "2026-06-17T19:15:00Z")]
    _, game_outcome, _ = mi.resolve_physical_game(legs, event_time_hhmm="1915")
    _, contract_outcome = mi.resolve_contract(
        "KXMLBTOTAL-26SEP102140BOSNYY-9", threshold=9,
        direction=mi.DIRECTION_OVER, side=mi.SIDE_YES)
    assert price["actionable"] is True
    assert mi.is_proven(contract_outcome) is True
    assert mi.is_proven(game_outcome) is False
    assert (price["actionable"] and mi.is_proven(contract_outcome)
            and mi.is_proven(game_outcome)) is False


def test_b2_price_invariants_are_untouched_by_w1c():
    """W1-C must not have loosened anything B2 established."""
    assert pp.price_contract(
        market_ticker="T", side=cp.SIDE_YES, yes_bid="0.44", yes_ask="0.46",
        captured_at=_FRESH, decided_at=_DECIDED)["refusalReason"] == (
            pp.REFUSE_UNIT_NOT_DECLARED)
    assert pp.price_contract(
        market_ticker="T", side=cp.SIDE_YES, yes_bid="0.44", yes_ask="0.46",
        unit=cp.UNIT_DOLLARS, captured_at="2026-09-10T12:05:00Z",
        decided_at=_DECIDED)["refusalReason"] == pp.REFUSE_FUTURE_QUOTE
    bid_only = pp.price_contract(
        market_ticker="T", side=cp.SIDE_YES, yes_bid="0.44", yes_ask=None,
        unit=cp.UNIT_DOLLARS, captured_at=_FRESH, decided_at=_DECIDED)
    assert bid_only["actionable"] is False


# ── the money-path join no longer guesses ──────────────────────────────────

def test_the_registry_join_is_deterministic_and_collision_aware():
    """
    scripts/merge_odds.py::find_registry_entry used to iterate a `set` and
    return the first hit. Source-anchored so the set cannot come back.
    """
    import re
    with open(os.path.join(ROOT, "scripts", "merge_odds.py")) as handle:
        source = handle.read()
    body = source[source.index("def find_registry_entry("):
                  source.index("def compute_game_odds_fields(")]
    # The docstring QUOTES the removed code so the defect stays legible; strip
    # it, or the scan is satisfied only by deleting the explanation.
    body = body.split('"""')[-1]
    code = "\n".join(l for l in body.split("\n") if not l.strip().startswith("#"))
    assert "candidates = set()" not in code, "the arbitrary-order set is back"
    assert "ordered_keys" in code, "keys must be tried in a deterministic order"
    assert "colliding" in code, "a colliding team pair must refuse"


def test_the_registry_preserves_every_event_and_never_keys_state_by_a_team_pair():
    """
    build_kalshi_registry.py must RETAIN every colliding event whole, not merely
    decline to overwrite it.

    The first W1-C increment recorded the second leg as metadata and moved on.
    That stopped the silent overwrite, but the second leg's entire market
    payload was still discarded -- and a leg with no books cannot be resolved
    TO later, however good the resolver is. "Detected and refused" had quietly
    become "one leg of every doubleheader has no markets".
    """
    with open(os.path.join(ROOT, "scripts", "build_kalshi_registry.py")) as handle:
        source = handle.read()
    code = "\n".join(l for l in source.split("\n") if not l.strip().startswith("#"))

    assert "events[suffix] = entry" in code, (
        "every event must be stored whole under its own suffix")
    assert "registry[kalshi_key] = entry" not in code, (
        "authoritative state must never be keyed by a bare team pair")
    assert "'events': events," in code, "the events store must be published"
    assert "registry_key_collisions" in code

    # The compat index is DERIVED, and only where the pair names one event.
    derived = code[code.index("registry = {}\nfor _pair, _suffixes"):]
    assert "if len(_suffixes) == 1:" in derived
    assert "registry_key_collisions.append" in derived


# ── the ledger row: identity is proven, or the row is not actionable ────────
#
# Everything above proves the identity MODULE is fail-closed. These prove the
# PRODUCTION LEDGER actually uses it -- a canonical module nothing calls is a
# document, not a control.

def _ledger():
    import importlib
    return importlib.import_module("scripts.build_market_ledger")


def test_the_ledger_row_whitelist_carries_every_identity_field():
    """
    make_row is an explicit whitelist: a key it does not name is silently
    dropped. Identity that vanished between evaluate_game() and the ledger
    would leave rows looking exactly as unidentified as before W1-C.
    """
    row = _ledger().make_row("ML_Away", status="Rejected")
    for field in ("physicalGameKey", "marketFamily", "marketHorizon",
                  "selection", "direction", "threshold", "contractSide",
                  "identityStatus", "identityMissing"):
        assert field in row, "make_row drops %s" % field


def test_an_accepted_row_requires_proven_identity():
    bl = _ledger()
    row = bl.accepted_row(
        "ML_Away", confidence="HIGH", betSize=100,
        identityStatus=mi.IDENTITY_PROVEN, marketTicker="KXMLBGAME-X-KC",
        marketFamily="MONEYLINE", marketHorizon=mi.HORIZON_FULL_GAME,
        selection="KC", direction=mi.DIRECTION_WIN, contractSide=mi.SIDE_YES)
    assert row["status"] == "Accepted"
    assert row["confidence"] == "HIGH"


@pytest.mark.parametrize("status", [
    None,                                              # identity never supplied
    mi.IDENTITY_REFUSED_NO_CONTRACT,
    mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME,
    mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_INCOMPLETE,
    mi.IDENTITY_REFUSED_UNKNOWN_FAMILY,
    mi.IDENTITY_REFUSED_SERIES_MISMATCH,
    mi.IDENTITY_REFUSED_NO_PHYSICAL_GAME_MATCH,
])
def test_an_unproven_row_cannot_be_accepted_however_good_the_price(status):
    """
    The W1-B2 composition rule, enforced at the seam that matters: a perfect
    two-sided fresh book does not license a trade in a contract the system
    cannot uniquely name. Absent identity is treated exactly like refused
    identity -- a caller that proved nothing has not proved anything.
    """
    kwargs = dict(confidence="HIGH", confidenceTier="HIGH", betSize=250,
                  executablePriceUsed=46.0)
    if status is not None:
        kwargs["identityStatus"] = status
    row = _ledger().accepted_row("ML_Away", **kwargs)
    assert row["status"] == "Rejected"
    # The tier fields are dropped, not merely ignored: a refused row still
    # carrying confidence='HIGH' and a betSize is one careless downstream
    # read away from being staked.
    assert row["confidence"] is None
    assert row["betSize"] is None
    assert "W1-C" in (row["rejectionReason"] or "")


def test_one_contract_has_one_ticker_or_none():
    """
    contract_ticker_for: the merged family block and the book actually being
    priced must name the SAME contract. Two claims and no way to choose is
    not a tie-break, it is a refusal.
    """
    ct = _ledger().contract_ticker_for
    assert ct({"ticker": "A"}, "A") == "A"
    assert ct({"ticker": "A"}, None) == "A"      # one source is not a conflict
    assert ct(None, "A") == "A"
    assert ct({}, "A") == "A"
    assert ct({"ticker": "A"}, "B") is None      # disagreement refuses
    assert ct(None, None) is None


def test_the_ledger_never_re_derives_side_semantics_of_its_own():
    """
    Source-anchored. Every identity() call in the ledger must hand over the
    market label so the side comes from the ONE canonical table. A call site
    that supplied its own direction/side would be a second identity system,
    which is exactly what this subwave exists to prevent.
    """
    with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as handle:
        source = handle.read()
    calls = [i for i in range(len(source)) if source.startswith("**identity(", i)]
    assert calls, "no identity() call sites found — the scan is vacuous"
    for i in calls:
        # Balanced-paren scan: a nested call such as
        # identity(contract_ticker_for(...), ...) would otherwise be cut at
        # the inner ')' and the label missed.
        depth, j = 0, source.index("(", i)
        for j in range(j, len(source)):
            if source[j] == "(":
                depth += 1
            elif source[j] == ")":
                depth -= 1
                if depth == 0:
                    break
        call = source[i:j + 1]
        assert "market=" in call, "identity() call without a market label: %s" % call


def test_every_ledger_market_label_has_declared_semantics():
    """
    REQUIRED_MARKETS is what production evaluates. A label missing from
    LEDGER_MARKET_SEMANTICS would resolve to no side at all and refuse every
    row of that family silently, which looks like "no edges today".
    """
    for market in _ledger().REQUIRED_MARKETS:
        assert mi.ledger_market_semantics(market) is not None, \
            "%s has no declared contract semantics" % market


def test_nrfi_is_the_no_side_and_yrfi_the_yes_side_of_one_contract():
    """The single most expensive thing to get backwards in this family."""
    _, nrfi_dir, nrfi_side = mi.ledger_market_semantics("NRFI")
    _, yrfi_dir, yrfi_side = mi.ledger_market_semantics("YRFI")
    assert nrfi_side == mi.SIDE_NO and yrfi_side == mi.SIDE_YES
    assert nrfi_dir == mi.DIRECTION_EVENT_DOES_NOT_OCCUR
    assert yrfi_dir == mi.DIRECTION_EVENT_OCCURS


def test_a_family_claim_that_contradicts_the_ticker_refuses():
    """
    The caller says KXMLBGAME, the ticker says KXMLBTEAMTOTAL. One of them is
    wrong, nothing here can say which, so neither is trusted.
    """
    _, outcome = mi.resolve_contract(
        "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", selection="MIL",
        direction=mi.DIRECTION_OVER, threshold=4, side=mi.SIDE_YES,
        expected_series="KXMLBGAME")
    assert outcome == mi.IDENTITY_REFUSED_SERIES_MISMATCH


def test_the_live_evidence_script_restores_the_slate_merge_odds_overwrites():
    """
    `scripts/merge_odds.py` has no `__main__` guard, so importing it performs a
    full merge and rewrites `data/slate.json` in the cwd. The live evidence
    script imports it to probe the REAL join, so it must undo that write.

    A path check cannot be the guard: the CI rehearsal runs from inside a copy
    of the checkout, where the script's own root IS the working directory, so
    no inspection distinguishes the copy from the original. Source-anchored
    here because exercising it for real would mean importing merge_odds inside
    the test suite -- which is the very side effect being guarded against.
    """
    path = os.path.join(ROOT, "scripts", "audit", "w1c_live_identity_evidence.py")
    with open(path) as handle:
        source = handle.read()
    body = source[source.index("def reorder_invariance_report("):
                  source.index("def main(")]
    code = "\n".join(l for l in body.split("\n") if not l.strip().startswith("#"))
    code = code.split('"""')[-1]

    assert "from scripts.merge_odds import" in code, "the probe must use the REAL join"
    assert 'os.path.join(os.getcwd(), "data", "slate.json")' in code, (
        "the slate merge_odds overwrites must be located relative to the cwd, "
        "which is where merge_odds itself writes it")
    assert "finally:" in code, "the restore must survive an import that raises"
    assert code.index("before = handle.read()") < code.index("from scripts.merge_odds import"), (
        "the slate must be read BEFORE the import that overwrites it")
    assert 'handle.write(before)' in code, "the original bytes must be written back"


def test_the_live_evidence_script_never_runs_wager_or_gate_code():
    """No rehearsal may reach recommendation authority. Model-driven
    real-money authority stays OFF, and that is a property of what the code
    can call, not of what it happens to do today."""
    for name in ("w1c_live_identity_evidence.py", "w1c_identity_blast_radius.py"):
        with open(os.path.join(ROOT, "scripts", "audit", name)) as handle:
            source = handle.read()
        for banned in ("risk_gate", "write_pending_bets", "validate_slate_final"):
            assert banned not in source, "%s can reach %s" % (name, banned)
