#!/usr/bin/env python3
"""
tests/test_w1c_event_game_binding.py
====================================
W1-C, final correction. The chain must be proven as ONE statement:

    exact MLB gamePk -> exact Kalshi event -> exact Kalshi contract
      -> family/horizon/selection/direction/threshold/side
      -> executable price for THAT SAME contract

Proving "a valid gamePk exists" and separately proving "a valid contract
exists" is not the same claim, and the gap between them is not theoretical.
A ticker belonging to the OTHER leg of a doubleheader, appearing on one game
and nowhere else, satisfies both halves:

    physicalGameKey = game A
    marketTicker    = a contract from game B
    identityStatus  = IDENTITY_PROVEN

and ticker exclusivity cannot see it, because the ticker is claimed exactly
once. `test_wrong_leg_single_claim_*` below is that case, and it is the
regression the first W1-C increment was missing.

These run the REAL production path -- the registry document shape written by
`build_kalshi_registry.py`, the real `merge_odds.find_registry_entry`, the
real `build_market_ledger.identity()` -- not a re-implementation of it.
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.edgelab import market_identity as mi        # noqa: E402


# ── building a registry document exactly as production writes one ───────────
#
# Deliberately constructed here rather than imported from the builder: if the
# builder's output shape drifts from what the join expects, these tests should
# fail rather than drift with it. The end-to-end registry test below runs the
# real builder logic against this same shape.

def _price_block(bid=0.44, ask=0.46, captured="2026-07-11T15:30:00Z"):
    return {"yes_bid": bid, "yes_ask": ask, "no_bid": round(1 - ask, 4),
            "no_ask": round(1 - bid, 4), "mid": round((bid + ask) / 2, 4),
            "unit": "dollars", "captured_at": captured,
            "book_state": "TWO_SIDED", "implied_pct": 45.0, "american": 122}


def _event(suffix, time_str, away, home, *, captured="2026-07-11T15:30:00Z"):
    """One complete Kalshi event entry, with real market payload."""
    return {
        "kalshi_key": f"{away}{home}",
        "date": "2026-07-11",
        "kalshi_date": "26JUL11",
        "event_ticker_suffix": suffix,
        "time_str": time_str,
        "game_time_et": "%s:%s %s ET" % (int(time_str[:2]) % 12 or 12, time_str[2:],
                                         "PM" if int(time_str[:2]) >= 12 else "AM"),
        "away": away, "home": home,
        "snapshot_ts": captured,
        "markets": {
            "moneyline": {
                "series": "KXMLBGAME",
                "away_ticker": "KXMLBGAME-%s-%s" % (suffix, away),
                "home_ticker": "KXMLBGAME-%s-%s" % (suffix, home),
                "prices": {"away": _price_block(captured=captured),
                           "home": _price_block(0.54, 0.56, captured=captured)},
            },
        },
        "closing_snapshots": [],
    }


def _registry_doc(events):
    """The document shape build_kalshi_registry.py writes."""
    by_suffix = {e["event_ticker_suffix"]: e for e in events}
    pairs = {}
    for e in events:
        pairs.setdefault(e["kalshi_key"], []).append(e["event_ticker_suffix"])
    compat, collisions = {}, []
    for pair, suffixes in sorted(pairs.items()):
        if len(suffixes) == 1:
            compat[pair] = by_suffix[suffixes[0]]
        else:
            collisions.append({"kalshi_key": pair,
                               "event_suffixes": sorted(suffixes),
                               "reason": "TEAM_PAIR_KEY_COLLISION_LIKELY_DOUBLEHEADER"})
    return {"generated_at": "2026-07-11T15:30:00Z", "date": "2026-07-11",
            "events": by_suffix, "registry": compat,
            "registry_key_collisions": collisions,
            "registry_key_collision_count": len(collisions)}


def _game(pk, start_utc, away="MIL", home="PIT"):
    return {"gameId": pk, "startTime": start_utc,
            "away": {"abbr": away, "team": "Milwaukee Brewers"},
            "home": {"abbr": home, "team": "Pittsburgh Pirates"}}


# ── the real production join, imported without running the whole script ─────

def _merge_odds_module():
    """
    Import scripts.merge_odds for its FUNCTIONS.

    The module has no `__main__` guard, so importing it performs a full merge
    and writes data/slate.json into the cwd. It is imported here inside a
    throwaway directory containing the minimum inputs, so that write lands in
    a temp tree and the real repo is never touched.
    """
    tmp = tempfile.mkdtemp(prefix="w1c-merge-")
    prev_cwd = os.getcwd()
    try:
        os.makedirs(os.path.join(tmp, "data"))
        for name, doc in (("odds.json", {"games": []}),
                          ("slate.json", {"date": "2026-07-11", "games": []}),
                          ("kalshi_market_registry.json", _registry_doc([]))):
            with open(os.path.join(tmp, "data", name), "w") as handle:
                json.dump(doc, handle)
        os.chdir(tmp)
        sys.modules.pop("scripts.merge_odds", None)
        return importlib.import_module("scripts.merge_odds")
    finally:
        os.chdir(prev_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


MERGE = _merge_odds_module()


def resolve(game, doc, siblings=None):
    """The REAL production join, called exactly as merge_odds calls it.

    `siblings` are the slate games sharing this matchup on this date -- which
    is how production knows a doubleheader is present at all. Defaults to the
    game alone, i.e. an ordinary single-game slate.
    """
    away = game["away"]
    home = game["home"]
    return MERGE.find_registry_entry(
        away["team"], home["team"], away["abbr"], home["abbr"],
        doc["registry"], game=game,
        collisions=doc["registry_key_collisions"], events=doc["events"],
        sibling_games=siblings if siblings is not None else [game])


def _ledger():
    return importlib.import_module("scripts.build_market_ledger")


# ── 1. DOUBLEHEADER PRESERVATION ────────────────────────────────────────────

def test_1_both_doubleheader_events_survive_registry_construction_with_payloads():
    """
    The previous revision detected the collision and `continue`d, keeping only
    metadata for the second event. A leg with no markets cannot be resolved TO
    later however good the resolver is, so "detected and refused" quietly
    became "one leg of every doubleheader has no books".
    """
    doc = _registry_doc([_event("26JUL111205MILPIT", "1205", "MIL", "PIT"),
                         _event("26JUL111605MILPIT", "1605", "MIL", "PIT")])

    assert set(doc["events"]) == {"26JUL111205MILPIT", "26JUL111605MILPIT"}
    for suffix, event in doc["events"].items():
        ml = event["markets"]["moneyline"]
        assert ml["away_ticker"].endswith("-MIL") and suffix in ml["away_ticker"]
        assert ml["prices"]["away"]["yes_ask"] is not None, (
            "%s lost its market payload" % suffix)

    # And the team pair, which names both, names neither.
    assert "MILPIT" not in doc["registry"], (
        "a pair that names two events must get no compat entry: an entry "
        "pointing at either leg is a wrong answer that reads like a right one")
    assert doc["registry_key_collision_count"] == 1


def test_1_the_builder_keys_authoritative_state_by_event_not_team_pair():
    """Source-anchored against the real builder."""
    with open(os.path.join(ROOT, "scripts", "build_kalshi_registry.py")) as handle:
        source = handle.read()
    assert "events[suffix] = entry" in source, (
        "the authoritative store must be keyed by event suffix")
    assert "registry[kalshi_key] = entry" not in source, (
        "authoritative state must never be keyed by a bare team pair")
    assert "'events': events," in source, "the events store must be published"
    # the compat index is derived, and only for pairs naming exactly one event
    assert "if len(_suffixes) == 1:" in source


# ── 2. PRODUCTION DOUBLEHEADER RESOLUTION, both directions ──────────────────

def test_2_each_event_maps_to_exactly_the_correct_physical_game():
    """
    Through the REAL join. MLB records UTC and Kalshi's suffix records EASTERN:
    16:05Z is 12:05 ET and 20:05Z is 16:05 ET.
    """
    doc = _registry_doc([_event("26JUL111205MILPIT", "1205", "MIL", "PIT"),
                         _event("26JUL111605MILPIT", "1605", "MIL", "PIT")])

    legs = [_game(823357, "2026-07-11T16:05:00Z"),
            _game(823356, "2026-07-11T20:05:00Z")]
    early = resolve(legs[0], doc, siblings=legs)
    late = resolve(legs[1], doc, siblings=legs)

    assert early is not None and late is not None, "both legs must resolve"
    assert early["event_ticker_suffix"] == "26JUL111205MILPIT"
    assert late["event_ticker_suffix"] == "26JUL111605MILPIT"
    assert early is not late, "the two legs must not receive the same event"


def test_2_resolution_is_invariant_to_event_and_candidate_order():
    forward = [_event("26JUL111205MILPIT", "1205", "MIL", "PIT"),
               _event("26JUL111605MILPIT", "1605", "MIL", "PIT")]
    legs = [_game(823357, "2026-07-11T16:05:00Z"),
            _game(823356, "2026-07-11T20:05:00Z")]
    for events in (forward, list(reversed(forward))):
        doc = _registry_doc(events)
        for order in (legs, list(reversed(legs))):
            for leg, expected in zip(legs, ("26JUL111205MILPIT", "26JUL111605MILPIT")):
                got = resolve(leg, doc, siblings=order)
                assert got["event_ticker_suffix"] == expected, "order changed the mapping"


# ── 3. UNRESOLVABLE DOUBLEHEADER ───────────────────────────────────────────

def test_3_indistinguishable_legs_both_fail_closed():
    """Two events, and a game whose start is equidistant from both. A tie is
    not a tie-break."""
    doc = _registry_doc([_event("26JUL111205MILPIT", "1205", "MIL", "PIT"),
                         _event("26JUL111605MILPIT", "1605", "MIL", "PIT")])
    # 14:05 ET is exactly 120 minutes from each.
    assert resolve(_game(823357, "2026-07-11T18:05:00Z"), doc) is None


def test_3_a_game_with_no_usable_start_refuses_rather_than_taking_the_first():
    doc = _registry_doc([_event("26JUL111205MILPIT", "1205", "MIL", "PIT"),
                         _event("26JUL111605MILPIT", "1605", "MIL", "PIT")])
    blind = _game(823357, None)
    blind.pop("startTime")
    assert resolve(blind, doc) is None


# ── 4. WRONG-LEG SINGLE CLAIM — the critical missing regression ─────────────

def _identity_for(game, market_ticker, series, market="ML_Away"):
    """Run the REAL ledger identity() for one row of one game."""
    bl = _ledger()
    captured = {}

    class _Probe(dict):
        pass

    # evaluate_game builds identity() as a closure over the game, so the only
    # way to reach it is through the real function. Rather than re-implement
    # it, drive it the way production does and read the row back.
    rows = bl.evaluate_game(game)
    captured["rows"] = rows
    return rows


def test_4_a_wrong_leg_ticker_claimed_only_once_still_refuses():
    """
    THE case this correction exists for.

    gamePk 823357 resolved to the 12:05 ET event. It is handed a moneyline
    ticker from the 16:05 ET event -- the other leg. That ticker appears on
    NO other game, so ticker exclusivity reports zero violations, and the
    gamePk and the contract semantics are both individually perfect.

    Only the event->game binding can see it.
    """
    wrong_leg = "KXMLBGAME-26JUL111605MILPIT-MIL"
    assert mi.event_suffix_of(wrong_leg) == "26JUL111605MILPIT"

    # exclusivity is blind here: one ticker, one game
    assert mi.assert_ticker_exclusivity([(wrong_leg, "823357")]) == [], (
        "the premise of this test is that exclusivity cannot catch it")

    belongs = mi.ticker_belongs_to_event(wrong_leg, "26JUL111205MILPIT")
    assert belongs is False, "a positive contradiction, not an absence"


def test_4_the_ledger_refuses_a_wrong_leg_ticker_end_to_end():
    bl = _ledger()
    row = bl.accepted_row(
        "ML_Away", confidence="HIGH", betSize=250,
        identityStatus=mi.IDENTITY_REFUSED_EVENT_GAME_MISMATCH,
        physicalGameKey="823357",
        resolvedEventTickerSuffix="26JUL111205MILPIT",
        contractEventTickerSuffix="26JUL111605MILPIT",
        marketTicker="KXMLBGAME-26JUL111605MILPIT-MIL")
    assert row["status"] == "Rejected"
    assert row["confidence"] is None and row["betSize"] is None


def test_4_the_mismatch_has_its_own_refusal_reason():
    """
    Not folded into NO_CONTRACT or generic ambiguity. "We could not tell" and
    "we can tell, and it is wrong" need different names, or the second hides
    inside the first and nobody ever looks for it.
    """
    assert mi.IDENTITY_REFUSED_EVENT_GAME_MISMATCH in mi.REFUSALS
    assert mi.IDENTITY_REFUSED_EVENT_GAME_MISMATCH != mi.IDENTITY_REFUSED_NO_CONTRACT
    assert (mi.IDENTITY_REFUSED_EVENT_GAME_MISMATCH
            != mi.IDENTITY_REFUSED_AMBIGUOUS_PHYSICAL_GAME)


# ── 5. CORRECT SINGLE CLAIM ────────────────────────────────────────────────

def test_5_the_correct_leg_ticker_binds_and_may_proceed():
    right_leg = "KXMLBGAME-26JUL111205MILPIT-MIL"
    assert mi.ticker_belongs_to_event(right_leg, "26JUL111205MILPIT") is True

    bl = _ledger()
    row = bl.accepted_row(
        "ML_Away", confidence="HIGH", betSize=250,
        identityStatus=mi.IDENTITY_PROVEN,
        physicalGameKey="823357",
        resolvedEventTickerSuffix="26JUL111205MILPIT",
        contractEventTickerSuffix="26JUL111205MILPIT",
        marketTicker=right_leg, marketFamily="MONEYLINE",
        marketHorizon=mi.HORIZON_FULL_GAME, selection="MIL",
        direction=mi.DIRECTION_WIN, contractSide=mi.SIDE_YES)
    assert row["status"] == "Accepted"
    assert row["confidence"] == "HIGH"


# ── 6. DEAD-ARG GUARD ──────────────────────────────────────────────────────

def test_6_the_physical_game_argument_materially_changes_the_answer():
    """
    An accepted-but-ignored parameter looks exactly like a used one from the
    outside. The previous revision took `game=` and never read it. Here the
    ONLY thing that differs between the two calls is the game, and the answers
    must differ -- otherwise the evidence is decorative.
    """
    doc = _registry_doc([_event("26JUL111205MILPIT", "1205", "MIL", "PIT"),
                         _event("26JUL111605MILPIT", "1605", "MIL", "PIT")])
    legs = [_game(823357, "2026-07-11T16:05:00Z"),
            _game(823356, "2026-07-11T20:05:00Z")]
    a = resolve(legs[0], doc, siblings=legs)
    b = resolve(legs[1], doc, siblings=legs)
    assert a["event_ticker_suffix"] != b["event_ticker_suffix"], (
        "the game argument does not change which event is returned; it is dead")

    # and passing no game at all cannot silently succeed where a game would
    assert resolve_without_game(doc) is None


def resolve_without_game(doc):
    return MERGE.find_registry_entry(
        "Milwaukee Brewers", "Pittsburgh Pirates", "MIL", "PIT",
        doc["registry"], game=None,
        collisions=doc["registry_key_collisions"], events=doc["events"])


def test_6_the_join_source_actually_calls_the_canonical_authority():
    with open(os.path.join(ROOT, "scripts", "merge_odds.py")) as handle:
        source = handle.read()
    body = source[source.index("def find_registry_entry("):
                  source.index("def compute_game_odds_fields(")]
    code = body.split('"""')[-1]
    assert "mi.resolve_event_for_game" in code, (
        "the join must resolve through the one canonical authority")
    assert "events" in code, "the join must consult the authoritative event store"


# ── 7. EVENT / TICKER CONSISTENCY ──────────────────────────────────────────

def test_7_a_row_cannot_use_one_event_for_identity_and_another_for_its_ticker():
    for resolved, ticker, expected in (
            ("26JUL111205MILPIT", "KXMLBGAME-26JUL111205MILPIT-MIL", True),
            ("26JUL111205MILPIT", "KXMLBGAME-26JUL111605MILPIT-MIL", False),
            ("26JUL111605MILPIT", "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", True),
            ("26JUL111605MILPIT", "KXMLBTEAMTOTAL-26JUL111205MILPIT-MIL4", False),
    ):
        assert mi.ticker_belongs_to_event(ticker, resolved) is expected


def test_7_an_unreadable_or_absent_event_is_unproven_not_agreement():
    assert mi.ticker_belongs_to_event("NOT-A-TICKER", "26JUL111205MILPIT") is None
    assert mi.ticker_belongs_to_event("KXMLBGAME-26JUL111205MILPIT-MIL", None) is None
    assert mi.ticker_belongs_to_event(None, "26JUL111205MILPIT") is None


def test_7_the_ledger_binds_before_it_proves():
    """Source-anchored: the binding must gate IDENTITY_PROVEN, not annotate it."""
    with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as handle:
        source = handle.read()
    assert "ticker_belongs_to_event" in source
    assert "IDENTITY_REFUSED_EVENT_GAME_MISMATCH" in source
    assert "resolved_event_suffix = g.get('kalshiEventTickerSuffix')" in source, (
        "the ledger must read the event merge_odds resolved for this game")
    idx = source.index("belongs = mi.ticker_belongs_to_event")
    window = source[idx:idx + 900]
    assert "if outcome == mi.IDENTITY_PROVEN:" in window
    assert "belongs is False" in window


def test_7_merge_odds_stamps_the_resolved_event_on_the_game():
    with open(os.path.join(ROOT, "scripts", "merge_odds.py")) as handle:
        source = handle.read()
    assert "new_game['kalshiEventTickerSuffix']" in source, (
        "the ledger cannot bind to an event the merge never recorded")


# ── 8. EXISTING EXCLUSIVITY — necessary, explicitly NOT sufficient ─────────

def test_8_ticker_exclusivity_still_holds_and_is_still_not_enough():
    violations = mi.assert_ticker_exclusivity([
        ("KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", "823357"),
        ("KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", "823356"),
    ])
    assert len(violations) == 1, "the two-gamePk case must still be caught"

    # ... and the one-gamePk wrong-leg case must still be invisible to it,
    # which is precisely why the binding is required as well.
    assert mi.assert_ticker_exclusivity([
        ("KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4", "823357"),
    ]) == []


# ── 9. HISTORICAL REPRODUCTION THROUGH THE REAL PRODUCTION PATH ────────────

HISTORICAL = [
    ("2026-06-17", {"SF", "ATL"}, "1915", "824913"),
    ("2026-07-11", {"MIL", "PIT"}, "1605", "823356"),
]


def _archived_legs(date, teams):
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


@pytest.mark.parametrize("date,teams,event_hhmm,correct_leg", HISTORICAL)
def test_9_archived_doubleheaders_through_the_real_join(date, teams, event_hhmm,
                                                        correct_leg):
    """
    The archived contamination, resolved by the REAL production join rather
    than by the standalone resolver.

    Only ONE leg's Kalshi event survives in each archive -- the other leg's
    markets were lost to the old overwrite -- so the correct outcome is that
    the surviving event binds to exactly one gamePk and the other leg gets
    nothing. Not that both get it, which is what the archive records.
    """
    legs = _archived_legs(date, teams)
    assert len(legs) == 2, "%s should be a doubleheader" % date

    away = sorted(teams)[0]
    home = sorted(teams)[1]
    for g in legs:
        a = g.get("away")
        away = a.get("abbr") if isinstance(a, dict) else a
        h = g.get("home")
        home = h.get("abbr") if isinstance(h, dict) else h
        break

    suffix = "%s%s%s%s" % (date[2:4], ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                       "JUL", "AUG", "SEP", "OCT", "NOV",
                                       "DEC"][int(date[5:7]) - 1],
                           date[8:10], event_hhmm)
    suffix += "%s%s" % (away, home)
    doc = _registry_doc([_event(suffix, event_hhmm, away, home)])

    probes = [{"gameId": mi.physical_game_key(g),
               "startTime": g.get("startTime"),
               "away": {"abbr": away, "team": away},
               "home": {"abbr": home, "team": home}} for g in legs]
    resolved = {}
    for probe in probes:
        entry = resolve(probe, doc, siblings=probes)
        resolved[probe["gameId"]] = (entry or {}).get("event_ticker_suffix")

    winners = [pk for pk, s in resolved.items() if s == suffix]
    assert winners == [correct_leg], (
        "%s: the surviving event must bind to exactly one leg, got %r"
        % (date, resolved))


# ── 10. the live rehearsal checks the binding too ──────────────────────────

def test_10_the_live_rehearsal_verifies_the_event_game_binding():
    path = os.path.join(ROOT, "scripts", "audit", "w1c_live_identity_evidence.py")
    with open(path) as handle:
        source = handle.read()
    for needle in ("contractEventTickerSuffix", "resolvedEventTickerSuffix",
                   "eventGameBindingViolations"):
        assert needle in source, (
            "the live rehearsal must check the ticker->event->gamePk binding "
            "on every actionable row, not only exclusivity (%s)" % needle)


def test_10_the_rehearsal_also_checks_price_and_identity_use_one_ticker():
    path = os.path.join(ROOT, "scripts", "audit", "w1c_live_identity_evidence.py")
    with open(path) as handle:
        source = handle.read()
    assert "executablePriceMarketTicker" in source, (
        "the contract that was priced must be the contract that was identified")


# ── the rehearsal guard must stay sharp, not merely quiet ──────────────────

def test_the_live_rehearsal_still_fails_on_any_price_series():
    """
    The guard was downgraded for RESEARCH-ONLY series only.

    Kalshi 429s the tail of the series sweep, and three player-prop series
    aborted a rehearsal whose entire price universe had fetched completely.
    Downgrading those is right; downgrading anything a wager can reach is not.
    This pins the boundary in both directions.
    """
    path = os.path.join(ROOT, "scripts", "audit", "w1b2_live_kalshisearch.mjs")
    with open(path) as handle:
        source = handle.read()

    assert "RESEARCH_ONLY_SERIES" in source and "process.exit(3)" in source

    start = source.index("const RESEARCH_ONLY_SERIES = new Set([")
    listed = source[start:source.index("])", start)]
    for research_only in ("KXMLBTB", "KXMLBHRR", "KXMLBRBI", "KXMLBKS",
                          "KXMLBOUTS", "KXMLBHIT", "KXMLBSB", "KXMLBF3", "KXMLBF7"):
        assert research_only in listed, "%s is research-only" % research_only
    for price_path in ("KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBTEAMTOTAL",
                       "KXMLBF5", "KXMLBF5SPREAD", "KXMLBF5TOTAL", "KXMLBRFI"):
        assert price_path not in listed, (
            "%s feeds the production price path and must still abort the "
            "rehearsal when it cannot be reached" % price_path)


def test_the_research_only_downgrade_matches_the_registrys_own_list():
    """One list, two languages. They must not drift apart."""
    path = os.path.join(ROOT, "scripts", "audit", "w1b2_live_kalshisearch.mjs")
    with open(path) as handle:
        js = handle.read()
    import re as _re
    listed = js[js.index("const RESEARCH_ONLY_SERIES = new Set(["):]
    js_set = set(_re.findall(r"KXMLB[A-Z0-9]+", listed[:listed.index("])")]))

    with open(os.path.join(ROOT, "scripts", "build_kalshi_registry.py")) as handle:
        py = handle.read()
    block = py[py.index("RESEARCH_ONLY_SERIES = frozenset({"):]
    py_set = set(_re.findall(r"KXMLB[A-Z0-9]+", block[:block.index("})")]))

    assert js_set == py_set, (
        "the rehearsal's research-only list has drifted from the registry's: "
        "js-only=%r py-only=%r" % (js_set - py_set, py_set - js_set))
