#!/usr/bin/env python3
"""
tests/test_w1c_doubleheader_marker.py
=====================================
W1-C micro-fixes. Two canonical-consistency properties, and the identity
semantics that follow from the second.

MICRO-FIX 1 — UNVERIFIED IS NOT PROVEN
--------------------------------------
`IDENTITY_PROVEN` has to mean the identity claim was proven. A result that
said PROVEN while carrying `contractClaimVerified: False` was a contradiction
in one object, and it put the burden on every downstream reader to know about
the exception. Research-only contracts are refused instead. They stay
archived, researchable and unsettled -- they just are not called proven.

MICRO-FIX 2 — ONE SUFFIX PARSER, AND IT READS G1/G2
---------------------------------------------------
Kalshi publishes the doubleheader leg directly in the event suffix:

    KXMLBGAME-26SEP111305BOSNYYG1-BOS      <- leg 1
    KXMLBGAME-26SEP111905BOSNYYG2-NYY      <- leg 2

`lib/kalshi_mlb_contract_parser` has always read that marker. Two other paths
re-derived suffix structure themselves and could not:

  * `market_identity.event_suffix_of` used a regex allowing only ALPHABETIC
    characters after the HHMM, so a marked suffix did not match at all -- in
    the module whose whole job is telling doubleheader legs apart;
  * `build_kalshi_registry.parse_suffix` required both halves of the team
    segment to be alphabetic, so a marked leg was unparseable and dropped with
    a WARN, in the builder this subwave made "keep every leg".

Both now delegate to the canonical parser. The rule lives once.

THE IDENTITY SEMANTICS
----------------------
Kalshi's marker is the strongest leg evidence available: it is the venue
saying which leg it listed. MLB start times are the physical evidence. When
both are determinable and they disagree, that is a POSITIVE CONTRADICTION and
the resolver refuses -- it does not fall back to closest-time, because falling
back would mean choosing the weaker evidence precisely because the stronger
one disagreed with it.

So the marker can only ever remove an answer, or supply one where the clock
could not. It can never quietly move a contract to a different game.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.edgelab import market_identity as mi              # noqa: E402
from lib import kalshi_mlb_contract_parser as kmcp         # noqa: E402


# A real doubleheader shape: BOS at NYY, 1:05 PM ET and 7:05 PM ET.
SUFFIX_PLAIN = "26SEP111905BOSNYY"
SUFFIX_G1 = "26SEP111305BOSNYYG1"
SUFFIX_G2 = "26SEP111905BOSNYYG2"
LEG1_START = "2026-09-11T17:05:00Z"          # 13:05 ET
LEG2_START = "2026-09-11T23:05:00Z"          # 19:05 ET


def _game(pk, start, **kw):
    g = {"gamePk": pk, "startTime": start}
    g.update(kw)
    return g


def _event(suffix):
    return {"event_ticker_suffix": suffix, "kalshi_key": "BOSNYY",
            "date": "2026-09-11", "away": "BOS", "home": "NYY"}


def _legs():
    return [_game(700001, LEG1_START), _game(700002, LEG2_START)]


# ── 1-3. event_suffix_of, with and without the marker ───────────────────────

def test_event_suffix_of_reads_an_ordinary_suffix():
    assert mi.event_suffix_of(
        "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4") == "26JUL111605MILPIT"
    assert mi.event_suffix_of("KXMLBRFI-26SEP101940PITCWS") == "26SEP101940PITCWS"
    assert mi.event_suffix_of("KXMLBGAME-26SEP102140SDSF-SF") == "26SEP102140SDSF"


def test_event_suffix_of_reads_a_g1_suffix():
    assert mi.event_suffix_of("KXMLBGAME-%s-BOS" % SUFFIX_G1) == SUFFIX_G1
    assert mi.event_game_number("KXMLBGAME-%s-BOS" % SUFFIX_G1) == 1


def test_event_suffix_of_reads_a_g2_suffix():
    assert mi.event_suffix_of("KXMLBGAME-%s-NYY" % SUFFIX_G2) == SUFFIX_G2
    assert mi.event_game_number("KXMLBGAME-%s-NYY" % SUFFIX_G2) == 2


def test_a_marked_suffix_used_to_be_invisible_to_the_identity_path():
    """The regression in its original shape: every marked leg returned None,
    so the ticker could not be bound to its event at all."""
    import re
    old = re.compile(r"(\d{2}[A-Z]{3}\d{2}\d{4}[A-Z]{2,4}[A-Z]{2,4})")
    assert old.fullmatch(SUFFIX_G1) is None, "the old regex really did miss it"
    assert mi.event_suffix_of("KXMLBGAME-%s-BOS" % SUFFIX_G1) == SUFFIX_G1


def test_the_contract_condition_still_parses_under_a_marker():
    parsed = kmcp.parse_contract_condition(
        "KXMLBTEAMTOTAL-%s-BOS4" % SUFFIX_G1)
    assert parsed["parseStatus"] == kmcp.PARSE_STATUS_PARSED
    assert parsed["selection"] == "BOS" and parsed["minimumInclusive"] == 4


# ── 4. the canonical parser is the single source ────────────────────────────

def test_the_canonical_parser_returns_teams_time_and_leg():
    for suffix, expected_leg in ((SUFFIX_G1, 1), (SUFFIX_G2, 2),
                                 (SUFFIX_PLAIN, None)):
        parsed = kmcp.parse_raw_event_suffix(suffix)
        assert parsed["parsed"] is True, suffix
        assert parsed["away"] == "BOS" and parsed["home"] == "NYY", suffix
        assert parsed["date"] == "2026-09-11", suffix
        assert parsed["game_number"] == expected_leg, suffix
    assert kmcp.parse_raw_event_suffix(SUFFIX_G1)["time_str"] == "1305"
    assert kmcp.parse_raw_event_suffix(SUFFIX_G2)["time_str"] == "1905"


def test_the_two_letter_split_survives_delegation():
    """The abbreviation rule the registry used to own is the parser's now, so
    the cases it exists for must still come out right."""
    for suffix, away, home in (("26SEP102140SDSF", "SD", "SF"),
                               ("26SEP101905COLNYY", "COL", "NYY"),
                               ("26SEP101910HOUTB", "HOU", "TB"),
                               ("26SEP112140TEXAZ", "TEX", "AZ"),
                               ("26SEP101910KCBOS", "KC", "BOS")):
        parsed = kmcp.parse_raw_event_suffix(suffix)
        assert (parsed["away"], parsed["home"]) == (away, home), suffix


def test_a_malformed_suffix_is_not_parsed_rather_than_half_parsed():
    for bad in ("", "nonsense", "26XXX111305BOSNYY", "26SEP11BOSNYY"):
        assert kmcp.parse_raw_event_suffix(bad)["parsed"] is False, bad
    assert mi.event_suffix_of("KXMLBGAME-nonsense-BOS") is None


def test_every_suffix_reader_agrees_because_there_is_only_one():
    """Structural: no path may carry its own suffix regex or its own G rule."""
    for path, forbidden in (
        (os.path.join(ROOT, "lib", "edgelab", "market_identity.py"),
         ("_EVENT_SUFFIX_RE", "[A-Z]{2,4}[A-Z]{2,4}")),
        (os.path.join(ROOT, "scripts", "build_kalshi_registry.py"),
         ("TWO_LETTER_ABBRS)", "a_len")),
    ):
        with open(path) as handle:
            source = handle.read()
        for token in forbidden:
            assert token not in source, "%s still re-derives suffix structure (%s)" % (
                path, token)


# ── 5. the registry keeps both marked legs, complete ────────────────────────

def test_the_registry_shape_keeps_two_marked_legs_as_distinct_entries():
    """Suffixes are unique per leg BECAUSE of the marker, so the suffix-keyed
    store cannot collapse them -- and the pair that names both names neither."""
    events = {}
    pairs = {}
    for suffix in (SUFFIX_G1, SUFFIX_G2):
        parsed = kmcp.parse_raw_event_suffix(suffix)
        assert parsed["parsed"], "a leg that does not parse is a leg that is lost"
        entry = {"event_ticker_suffix": suffix,
                 "kalshi_key": "%s%s" % (parsed["away"], parsed["home"]),
                 "time_str": parsed["time_str"],
                 "doubleheaderGameNumber": parsed["game_number"],
                 "markets": {"moneyline": {"away_ticker": "KXMLBGAME-%s-BOS" % suffix}}}
        events[suffix] = entry
        pairs.setdefault(entry["kalshi_key"], []).append(suffix)

    assert set(events) == {SUFFIX_G1, SUFFIX_G2}
    assert events[SUFFIX_G1]["doubleheaderGameNumber"] == 1
    assert events[SUFFIX_G2]["doubleheaderGameNumber"] == 2
    assert len(pairs["BOSNYY"]) == 2, "the team pair names two events"
    for suffix, entry in events.items():
        assert entry["markets"]["moneyline"]["away_ticker"].startswith(
            "KXMLBGAME-%s-" % suffix), "%s lost its market payload" % suffix


def test_the_registry_builder_delegates_and_records_the_marker():
    """The builder's own two functions, read from source rather than imported
    (`build_kalshi_registry.py` fires live HTTP at import)."""
    with open(os.path.join(ROOT, "scripts", "build_kalshi_registry.py")) as handle:
        source = handle.read()
    assert "kmcp.parse_raw_event_suffix(suffix)" in source
    assert "def parse_event_game_number(suffix):" in source
    assert "'doubleheaderGameNumber': parse_event_game_number(suffix)," in source


# ── 6-7. G1 maps to leg 1, G2 to leg 2, and reversal refuses ────────────────

def _resolve(game, events, siblings):
    return mi.resolve_event_for_game(game, events, sibling_games=siblings)


def test_g1_resolves_to_physical_leg_1_and_g2_to_leg_2():
    legs = _legs()
    events = [_event(SUFFIX_G1), _event(SUFFIX_G2)]

    first, outcome_1, ev1 = _resolve(legs[0], events, legs)
    assert outcome_1 == mi.IDENTITY_PROVEN
    assert first["event_ticker_suffix"] == SUFFIX_G1
    assert ev1["basis"] == "DOUBLEHEADER_GAME_NUMBER_AND_START_AGREE"
    assert ev1["doubleheaderGameNumber"] == 1

    second, outcome_2, ev2 = _resolve(legs[1], events, legs)
    assert outcome_2 == mi.IDENTITY_PROVEN
    assert second["event_ticker_suffix"] == SUFFIX_G2
    assert ev2["doubleheaderGameNumber"] == 2

    assert first is not second, "one event per leg, never the same one twice"


def test_g1_never_resolves_to_game_2():
    legs = _legs()
    events = [_event(SUFFIX_G1), _event(SUFFIX_G2)]
    for game, forbidden in ((legs[0], SUFFIX_G2), (legs[1], SUFFIX_G1)):
        resolved, outcome, _ = _resolve(game, events, legs)
        assert mi.is_proven(outcome)
        assert resolved["event_ticker_suffix"] != forbidden


def test_reversed_physical_leg_evidence_refuses():
    """
    The MLB side STATES its leg numbers and they run backwards against the
    clock: the 1:05 game calls itself leg 2 and the 7:05 game calls itself
    leg 1. Kalshi's G1 then points at the late game while its own HHMM points
    at the early one, and neither leg may resolve.

    Note what this test could NOT be. Merely swapping which gamePk carries
    which start time proves nothing: with no stated leg numbers the rank is
    derived from the clock, so the two sources agree by construction. A
    contradiction needs a second, independent statement of the leg -- which is
    what `doubleheaderGameNumber` on the game is.
    """
    reversed_legs = [_game(700001, LEG1_START, doubleheaderGameNumber=2),
                     _game(700002, LEG2_START, doubleheaderGameNumber=1)]
    events = [_event(SUFFIX_G1), _event(SUFFIX_G2)]
    for game in reversed_legs:
        resolved, outcome, evidence = _resolve(game, events, reversed_legs)
        assert resolved is None
        assert outcome == mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME
        assert evidence["basis"] == "LEG_NUMBER_CONTRADICTS_PHYSICAL_EVIDENCE"
        contradiction = evidence["legNumberContradictions"][0]
        assert contradiction["gamePkByLegNumber"] != contradiction["gamePkByStartTime"]


def test_a_stated_leg_number_agreeing_with_the_clock_is_proven():
    """The same shape, stated correctly: the marker and the game's own leg
    number and the clock all say the same thing."""
    legs = [_game(700001, LEG1_START, doubleheaderGameNumber=1),
            _game(700002, LEG2_START, doubleheaderGameNumber=2)]
    events = [_event(SUFFIX_G1), _event(SUFFIX_G2)]
    for index, game in enumerate(legs):
        resolved, outcome, evidence = _resolve(game, events, legs)
        assert outcome == mi.IDENTITY_PROVEN
        assert resolved["event_ticker_suffix"] == (SUFFIX_G1, SUFFIX_G2)[index]
        assert evidence["basis"] == "DOUBLEHEADER_GAME_NUMBER_AND_START_AGREE"


# ── 8-9. agreement proves, contradiction refuses ────────────────────────────

def test_marker_and_start_time_agreeing_is_proven():
    legs = _legs()
    resolved, outcome, evidence = _resolve(legs[0], [_event(SUFFIX_G1),
                                                     _event(SUFFIX_G2)], legs)
    assert outcome == mi.IDENTITY_PROVEN
    assert evidence["basis"] == "DOUBLEHEADER_GAME_NUMBER_AND_START_AGREE"
    assert resolved["event_ticker_suffix"] == SUFFIX_G1


def test_marker_and_start_time_contradicting_refuses_without_falling_back():
    """
    G1 sits on the LATE event and G2 on the early one. Closest-time alone would
    still produce a confident answer for both legs -- that is exactly the
    fallback that must not happen after a positive contradiction.
    """
    legs = _legs()
    events = [_event("26SEP111905BOSNYYG1"), _event("26SEP111305BOSNYYG2")]
    for game in legs:
        resolved, outcome, evidence = _resolve(game, events, legs)
        assert resolved is None, "closest-time must not rescue a contradiction"
        assert outcome == mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME
        assert evidence["basis"] == "LEG_NUMBER_CONTRADICTS_PHYSICAL_EVIDENCE"
        contradiction = evidence["legNumberContradictions"][0]
        assert contradiction["gamePkByLegNumber"] != contradiction["gamePkByStartTime"]


def test_a_stated_leg_number_that_does_not_match_refuses():
    """`resolve_physical_game` given a leg number, where the candidates state
    their legs and none is the one asked for. The field that should have
    settled it disagreed, so the start times do not get to settle it instead."""
    legs = [_game(700001, LEG1_START, doubleheaderGameNumber=1),
            _game(700002, LEG2_START, doubleheaderGameNumber=2)]
    resolved, outcome, evidence = mi.resolve_physical_game(
        legs, event_time_hhmm="1305", doubleheader_game_number=3)
    assert resolved is None
    assert outcome == mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME
    assert evidence["basis"] == "LEG_NUMBER_STATED_AND_DOES_NOT_MATCH"


# ── 10. no marker: nothing changes ──────────────────────────────────────────

def test_without_a_marker_the_existing_time_resolution_is_unchanged():
    legs = _legs()
    events = [_event("26SEP111305BOSNYY"), _event("26SEP111905BOSNYY")]
    first, o1, ev1 = _resolve(legs[0], events, legs)
    second, o2, ev2 = _resolve(legs[1], events, legs)
    assert o1 == o2 == mi.IDENTITY_PROVEN
    assert first["event_ticker_suffix"] == "26SEP111305BOSNYY"
    assert second["event_ticker_suffix"] == "26SEP111905BOSNYY"
    assert ev1["basis"] == ev2["basis"] == "UNIQUE_EVENT_RESOLVING_TO_THIS_GAME"
    assert ev1["doubleheaderGameNumber"] is None


def test_the_ordinary_single_game_slate_is_unchanged():
    solo = _game(800001, LEG2_START)
    resolved, outcome, evidence = _resolve(solo, [_event(SUFFIX_PLAIN)], [solo])
    assert outcome == mi.IDENTITY_PROVEN
    assert evidence["basis"] == "SINGLE_EVENT_AND_SINGLE_GAME_FOR_DATE_AND_TEAMS"


def test_the_archived_doubleheaders_still_resolve_exactly_as_accepted():
    """Neither archived board carries a marker, so this correction must leave
    the accepted SF@ATL and MIL@PIT results byte-identical."""
    cases = (
        ([_game(824912, "2026-06-17T18:00:00Z"),
          _game(824913, "2026-06-17T23:15:00Z")],
         ["26JUN171400SFATL", "26JUN171915SFATL"], "SFATL"),
        ([_game(823357, "2026-07-11T16:05:00Z"),
          _game(823356, "2026-07-11T20:05:00Z")],
         ["26JUL111205MILPIT", "26JUL111605MILPIT"], "MILPIT"),
    )
    for legs, suffixes, pair in cases:
        events = [dict(_event(s), kalshi_key=pair) for s in suffixes]
        for index, game in enumerate(legs):
            resolved, outcome, _ = _resolve(game, events, legs)
            assert outcome == mi.IDENTITY_PROVEN, (pair, game["gamePk"])
            assert resolved["event_ticker_suffix"] == suffixes[index]


# ── 11-12. the universal invariant ──────────────────────────────────────────

_PROBES = [
    ("KXMLBGAME-26SEP101940PITCWS-PIT",
     dict(selection="PIT", direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)),
    ("KXMLBF3-26SEP101940PITCWS-PIT",
     dict(selection="PIT", direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)),
    ("KXMLBF5-26SEP101940PITCWS-PIT",
     dict(selection="PIT", direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)),
    ("KXMLBF7-26SEP101940PITCWS-PIT",
     dict(selection="PIT", direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)),
    ("KXMLBSPREAD-26SEP101940PITCWS-PIT2",
     dict(selection="PIT", direction=mi.DIRECTION_OVER, threshold=1.5,
          side=mi.SIDE_YES)),
    ("KXMLBF5SPREAD-26SEP101940PITCWS-PIT3",
     dict(selection="PIT", direction=mi.DIRECTION_OVER, threshold=2.5,
          side=mi.SIDE_YES)),
    ("KXMLBTOTAL-26SEP101940PITCWS-9",
     dict(direction=mi.DIRECTION_OVER, threshold=9, side=mi.SIDE_YES)),
    ("KXMLBF5TOTAL-26SEP101940PITCWS-7",
     dict(direction=mi.DIRECTION_OVER, threshold=7, side=mi.SIDE_YES)),
    ("KXMLBTEAMTOTAL-26SEP101940PITCWS-PIT4",
     dict(selection="PIT", direction=mi.DIRECTION_OVER, threshold=4,
          side=mi.SIDE_YES)),
    ("KXMLBRFI-26SEP101940PITCWS",
     dict(direction=mi.DIRECTION_EVENT_OCCURS, side=mi.SIDE_YES)),
    # ... and the same families under a doubleheader marker.
    ("KXMLBGAME-%s-BOS" % SUFFIX_G1,
     dict(selection="BOS", direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)),
    ("KXMLBTEAMTOTAL-%s-NYY5" % SUFFIX_G2,
     dict(selection="NYY", direction=mi.DIRECTION_OVER, threshold=5,
          side=mi.SIDE_YES)),
]


def test_every_production_supported_series_is_proven_and_verified():
    for ticker, claim in _PROBES:
        ident, outcome = mi.resolve_contract(ticker, **claim)
        assert outcome == mi.IDENTITY_PROVEN, ticker
        assert ident["contractClaimVerified"] is True, ticker


def test_identity_proven_implies_the_contract_claim_was_verified():
    """
    THE UNIVERSAL INVARIANT. Across every series on any identity path --
    production and research alike -- and across correct claims, wrong claims,
    incomplete claims and malformed tickers, a PROVEN result must always carry
    `contractClaimVerified is True`.

    `is not True` is deliberate: `None` means the check never ran, and "never
    ran" is not "passed". If a partial identity is ever wanted, it gets its own
    status rather than being folded into this one.
    """
    wrong_claims = [
        (ticker, dict(claim, selection="ZZZ")) for ticker, claim in _PROBES
    ] + [
        (ticker, dict(claim, threshold=99)) for ticker, claim in _PROBES
    ] + [
        (ticker, dict(claim, side=mi.SIDE_NO)) for ticker, claim in _PROBES
    ] + [
        (ticker, {}) for ticker, _claim in _PROBES
    ]
    research = [("KXMLBKS-26SEP102140BOSNYY-GRAY6",
                 dict(selection="GRAY", threshold=6, side=mi.SIDE_YES)),
                ("KXMLBTB-26SEP101940PITCWS-CWSTPETERS29-4",
                 dict(selection="CWS", threshold=4, side=mi.SIDE_YES)),
                ("KXMLBSB-26SEP101940PITCWS-PITRFLORES43-1",
                 dict(selection="PIT", threshold=1, side=mi.SIDE_YES))]
    malformed = [("KXNFLGAME-26SEP10-XYZ", dict(selection="XYZ", side=mi.SIDE_YES)),
                 ("KXMLBTOTAL-26SEP101940PITCWS-NINE",
                  dict(direction=mi.DIRECTION_OVER, threshold=9, side=mi.SIDE_YES)),
                 (None, dict(side=mi.SIDE_YES))]

    checked = 0
    for ticker, claim in _PROBES + wrong_claims + research + malformed:
        ident, outcome = mi.resolve_contract(ticker, **claim)
        checked += 1
        if outcome == mi.IDENTITY_PROVEN:
            assert ident.get("contractClaimVerified") is True, (
                "%s was stamped PROVEN with contractClaimVerified=%r"
                % (ticker, ident.get("contractClaimVerified")))
        else:
            assert outcome in mi.REFUSALS, "%s -> %s is not in the vocabulary" % (
                ticker, outcome)
    assert checked >= 50, "vacuous: only %d probes" % checked


def test_research_only_contracts_are_still_unreachable_from_the_ledger():
    """Micro-fix 1 changes their STATUS, not their reachability. The structural
    guard that no undescribed series appears in a ledger identity call stands."""
    import re
    with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as handle:
        source = handle.read()
    declared = set(re.findall(r"identity\([^)]*?'(KXMLB[A-Z0-9]+)'", source))
    assert declared, "no identity() call sites found -- the guard is vacuous"
    for series in declared:
        assert series not in mi.PLAYER_PROP_SERIES, series
        assert kmcp.contract_grammar_for(series) is not None, series
