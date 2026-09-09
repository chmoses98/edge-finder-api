#!/usr/bin/env python3
"""
tests/test_w1b1_canonical_price.py
==================================
WAVE 1, subwave B1. Guards the canonical executable-price object and the
decision-time observation join.

The tests that matter most are the refusals. A pricing layer that always
produces a number is exactly the defect being fixed: the old path always
produced one, because it fell back to a midpoint whenever the real ask was
missing. Here, "no executable price" is a first-class, explicitly-reasoned
outcome and most of this file exists to prove it happens when it should.
"""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import canonical_price as cp  # noqa: E402
from lib.edgelab import observation_join as oj  # noqa: E402


# ── the tradeable range, and why zero is not a price ─────────────────────────

def test_zero_is_absent_not_a_price():
    """
    The single rule that stops `(bid or 0)` arithmetic at the source. Kalshi
    quotes rest at 1..99c; a zero bid means no resting bid exists.
    """
    assert cp.normalize_cents(0) is None
    assert cp.normalize_cents(0.0) is None
    assert cp.normalize_cents(100) is None      # outside the resting range
    assert cp.normalize_cents(None) is None
    assert cp.normalize_cents(12) == 12
    assert cp.normalize_cents(0.12, cp.UNIT_DOLLARS) == 12


def test_the_unit_is_declared_and_never_guessed():
    """
    A magnitude heuristic was wrong in both directions on a money path: it read
    a real 1-cent quote as $1.00 and discarded it (3,810 such quotes in ten
    partitions), and would have read a registry $1.00 as a 1-cent bargain.
    """
    assert cp.normalize_cents(1) == 1                       # a real 1c quote
    assert cp.normalize_cents(1.0, cp.UNIT_DOLLARS) is None  # $1.00 = 100c, untradeable
    assert cp.normalize_cents(0.99, cp.UNIT_DOLLARS) == 99
    with pytest.raises(ValueError):
        cp.normalize_cents(50, "furlongs")


def test_a_one_sided_book_never_produces_a_halved_midpoint():
    """
    CR-5, at the object level. 102,684 of 542,716 archived observations carry
    yesBid == 0 with a real ask; the old registry computed (0+ask)/2 and called
    it a price. Here the YES side prices at the ask and the book is labelled.
    """
    price = cp.build_price(cp.SIDE_YES, yes_bid=0, yes_ask=81)
    assert price["executablePrice"] == 81, "YES must price at its own ask"
    assert price["priceBasis"] == cp.BASIS_YES_ASK
    assert price["book"]["bookState"] == cp.BOOK_ASK_ONLY
    assert price["book"]["yesBid"] is None, "a zero bid must not survive as 0"
    assert price["executablePrice"] != 40.5, "the halved midpoint must be unreachable"


def test_the_forbidden_basis_is_never_emitted():
    """MIDPOINT_AS_EXECUTABLE exists only so this can be asserted."""
    for kwargs in ({"yes_bid": 40, "yes_ask": 60}, {"yes_bid": 0, "yes_ask": 81},
                   {"yes_bid": 50}, {"yes_ask": 50}, {}):
        for side in (cp.SIDE_YES, cp.SIDE_NO):
            price = cp.build_price(side, **kwargs)
            assert price["priceBasis"] != cp.BASIS_FORBIDDEN_MIDPOINT
            assert price["priceBasis"] in (None,) + cp.VALID_BASES


def test_a_midpoint_is_never_the_executable_price():
    price = cp.build_price(cp.SIDE_YES, yes_bid=40, yes_ask=60)
    assert price["executablePrice"] == 60
    assert price["executablePrice"] != 50


# ── YES execution rule ───────────────────────────────────────────────────────

def test_yes_uses_the_genuine_ask():
    price = cp.build_price(cp.SIDE_YES, yes_bid=44, yes_ask=46, market_ticker="T")
    assert (price["executablePrice"], price["priceBasis"]) == (46, cp.BASIS_YES_ASK)
    assert price["book"]["spreadCents"] == 2


def test_yes_refuses_when_the_ask_is_absent():
    price = cp.build_price(cp.SIDE_YES, yes_bid=44, yes_ask=None)
    assert price["executablePrice"] is None
    assert price["refusalReason"] == cp.NO_YES_ASK
    assert price["book"]["bookState"] == cp.BOOK_BID_ONLY


# ── NO execution rule and its economics ──────────────────────────────────────

def test_no_prefers_a_genuine_no_ask():
    price = cp.build_price(cp.SIDE_NO, yes_bid=44, yes_ask=46, no_ask=55)
    assert (price["executablePrice"], price["priceBasis"]) == (55, cp.BASIS_NO_ASK)


def test_no_is_derived_from_the_yes_bid_and_the_economics_are_exact():
    """
    Buying NO is the other side of filling a resting YES bid, so the executable
    NO ask is exactly 100 - yesBid. Asserted as an identity, not a tolerance.
    """
    for yes_bid in (1, 12, 44, 50, 73, 99):
        price = cp.build_price(cp.SIDE_NO, yes_bid=yes_bid, yes_ask=yes_bid + 1)
        assert price["executablePrice"] == 100 - yes_bid
        assert price["priceBasis"] == cp.BASIS_DERIVED_NO_ASK_FROM_YES_BID


def test_no_is_never_derived_from_the_yes_ask():
    """
    The complement of the YES ask is the NO BID -- the wrong side of the book to
    buy at. Deriving from it would quote a price nobody is offering.
    """
    price = cp.build_price(cp.SIDE_NO, yes_bid=44, yes_ask=46)
    assert price["executablePrice"] == 56          # 100 - 44, not 100 - 46
    assert price["executablePrice"] != 54


def test_no_refuses_when_there_is_no_resting_yes_bid():
    """
    The consequence of proving yesBid == 0 means "absent": 100 - 0 = 100c is not
    an executable purchase, so the NO side must refuse rather than quote it.
    """
    for bid in (None, 0):
        price = cp.build_price(cp.SIDE_NO, yes_bid=bid, yes_ask=81)
        assert price["executablePrice"] is None, "100-0 must never be quoted"
        assert price["refusalReason"] == cp.NO_BID_FOR_NO_SIDE


def test_an_empty_book_refuses_on_both_sides():
    for side in (cp.SIDE_YES, cp.SIDE_NO):
        price = cp.build_price(side)
        assert price["executablePrice"] is None
        assert price["refusalReason"] == cp.NO_BOOK
        assert price["book"]["bookState"] == cp.BOOK_EMPTY


def test_an_unrecognised_side_refuses():
    price = cp.build_price("MAYBE", yes_bid=44, yes_ask=46)
    assert price["executablePrice"] is None
    assert price["refusalReason"] == cp.UNKNOWN_SIDE


def test_raw_book_is_preserved_alongside_the_rejection():
    """A reader must be able to see WHY a quote was refused, not only that it was."""
    price = cp.build_price(cp.SIDE_NO, yes_bid=0, yes_ask=81)
    assert price["book"]["yesBidRaw"] == 0
    assert price["book"]["yesBid"] is None


def test_executable_probability_is_never_a_midpoint():
    assert cp.executable_probability(cp.build_price(cp.SIDE_YES, yes_bid=40, yes_ask=60)) == 0.6
    assert cp.executable_probability(cp.build_price(cp.SIDE_YES, yes_bid=40)) is None


# ── decision-time join: look-ahead safety ────────────────────────────────────

def _obs(ticker, captured_at, yes_bid=44, yes_ask=46, obs_id=None):
    return {"marketTicker": ticker, "capturedAt": captured_at, "yesBid": yes_bid,
            "yesAsk": yes_ask, "noBid": None, "noAsk": None, "spreadCents": None,
            "eventTicker": "E", "marketObservationId": obs_id or ("o-" + captured_at)}


def _index(rows):
    idx = {}
    for r in rows:
        r = dict(r)
        r["_capturedAtDt"] = oj.parse_ts(r["capturedAt"])
        idx.setdefault(r["marketTicker"], []).append(r)
    for rows_ in idx.values():
        rows_.sort(key=lambda r: (r["_capturedAtDt"], str(r.get("marketObservationId") or "")))
    return idx


DECIDED = "2026-09-09T18:00:00Z"


def test_a_future_observation_can_never_join():
    """The single most dangerous error available to a CLV study."""
    idx = _index([_obs("T", "2026-09-09T18:00:01Z")])
    join = oj.join_observation("T", DECIDED, idx)
    assert join["matched"] is False
    assert join["joinMethod"] == oj.JOIN_NONE_BEFORE


def test_the_latest_observation_at_or_before_the_decision_wins():
    idx = _index([_obs("T", "2026-09-09T17:00:00Z", yes_ask=40),
                  _obs("T", "2026-09-09T17:59:00Z", yes_ask=46),
                  _obs("T", "2026-09-09T18:30:00Z", yes_ask=99)])
    join = oj.join_observation("T", DECIDED, idx)
    assert join["matched"] and join["observation"]["yesAsk"] == 46
    assert join["quoteAgeSeconds"] == 60.0
    assert join["candidatesAtOrBefore"] == 2


def test_an_observation_exactly_at_the_decision_instant_is_eligible():
    idx = _index([_obs("T", DECIDED)])
    join = oj.join_observation("T", DECIDED, idx)
    assert join["matched"] and join["quoteAgeSeconds"] == 0.0


def test_identical_timestamps_resolve_deterministically_and_say_so():
    """Ties are broken by observation id, and the tie is recorded."""
    idx = _index([_obs("T", "2026-09-09T17:00:00Z", yes_ask=40, obs_id="b"),
                  _obs("T", "2026-09-09T17:00:00Z", yes_ask=41, obs_id="a")])
    first = oj.join_observation("T", DECIDED, idx)
    assert first["tieBroken"] is True
    assert first["observation"]["marketObservationId"] == "b"      # max id wins
    for _ in range(5):
        assert oj.join_observation("T", DECIDED, idx)["observation"]["yesAsk"] == 40


def test_a_stale_quote_is_flagged_but_still_joined_in_b1():
    idx = _index([_obs("T", "2026-09-09T10:00:00Z")])
    join = oj.join_observation("T", DECIDED, idx)
    assert join["matched"] and join["stale"] is True
    assert join["quoteAgeSeconds"] == 28800.0


def test_a_fresh_quote_is_not_flagged_stale():
    idx = _index([_obs("T", "2026-09-09T17:55:00Z")])
    assert oj.join_observation("T", DECIDED, idx)["stale"] is False


def test_an_unobserved_ticker_refuses():
    assert oj.join_observation("NOPE", DECIDED, _index([_obs("T", DECIDED)]))["joinMethod"] \
        == oj.JOIN_NO_TICKER_HISTORY


def test_a_decision_without_a_ticker_or_timestamp_refuses():
    idx = _index([_obs("T", DECIDED)])
    assert oj.join_observation(None, DECIDED, idx)["joinMethod"] == oj.JOIN_NO_TICKER
    assert oj.join_observation("T", "not-a-time", idx)["joinMethod"] == oj.JOIN_BAD_TIMESTAMP


# ── the away/home contamination cases ────────────────────────────────────────

def test_each_side_of_a_two_way_market_uses_its_own_ticker_book():
    """
    build_market_ledger.py:1366-1367 reads the SAME key for away and home. Two
    sides of an MLB moneyline are two distinct Kalshi contracts, so each must
    receive the book for its own ticker.
    """
    idx = _index([_obs("KXMLBGAME-X-MIN", DECIDED, yes_bid=11, yes_ask=12),
                  _obs("KXMLBGAME-X-DET", DECIDED, yes_bid=87, yes_ask=88)])
    away, _ = oj.price_for_decision("KXMLBGAME-X-MIN", cp.SIDE_YES, DECIDED, idx)
    home, _ = oj.price_for_decision("KXMLBGAME-X-DET", cp.SIDE_YES, DECIDED, idx)
    assert away["executablePrice"] == 12
    assert home["executablePrice"] == 88
    assert away["executablePrice"] != home["executablePrice"]


def test_swapping_the_two_books_changes_the_answer():
    """Regression: proves the test above is actually ticker-sensitive."""
    idx = _index([_obs("KXMLBGAME-X-MIN", DECIDED, yes_bid=87, yes_ask=88),
                  _obs("KXMLBGAME-X-DET", DECIDED, yes_bid=11, yes_ask=12)])
    away, _ = oj.price_for_decision("KXMLBGAME-X-MIN", cp.SIDE_YES, DECIDED, idx)
    assert away["executablePrice"] == 88, "swapped books must produce a different price"


def test_a_missing_away_ask_cannot_contaminate_the_home_contract():
    idx = _index([_obs("KXMLBGAME-X-MIN", DECIDED, yes_bid=11, yes_ask=None),
                  _obs("KXMLBGAME-X-DET", DECIDED, yes_bid=87, yes_ask=88)])
    away, _ = oj.price_for_decision("KXMLBGAME-X-MIN", cp.SIDE_YES, DECIDED, idx)
    home, _ = oj.price_for_decision("KXMLBGAME-X-DET", cp.SIDE_YES, DECIDED, idx)
    assert away["executablePrice"] is None and away["refusalReason"] == cp.NO_YES_ASK
    assert home["executablePrice"] == 88, "the sibling contract must be unaffected"


def test_a_missing_home_ask_cannot_contaminate_the_away_contract():
    idx = _index([_obs("KXMLBGAME-X-MIN", DECIDED, yes_bid=11, yes_ask=12),
                  _obs("KXMLBGAME-X-DET", DECIDED, yes_bid=87, yes_ask=None)])
    away, _ = oj.price_for_decision("KXMLBGAME-X-MIN", cp.SIDE_YES, DECIDED, idx)
    home, _ = oj.price_for_decision("KXMLBGAME-X-DET", cp.SIDE_YES, DECIDED, idx)
    assert away["executablePrice"] == 12
    assert home["executablePrice"] is None


def test_price_for_decision_returns_no_price_when_nothing_joined():
    price, join = oj.price_for_decision("UNSEEN", cp.SIDE_YES, DECIDED, _index([]))
    assert price is None and join["matched"] is False


# ── the module must stay a pricing seam, not a betting engine ────────────────

def test_the_price_module_contains_no_qualification_or_staking_logic():
    """
    B1 is shadow-only. If this module ever learns about actionable, confidence,
    Bet Up To or stake, the separation between pricing and authority is gone.
    """
    import ast
    path = os.path.join(ROOT, "lib", "edgelab", "canonical_price.py")
    with open(path) as handle:
        tree = ast.parse(handle.read())
    # Strip docstrings: the module prose legitimately DESCRIBES the boundary it
    # must not cross, so scanning it would flag the very sentence that states
    # the rule. Only executable code is inspected.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and \
                node.body and isinstance(node.body[0], ast.Expr) and \
                isinstance(node.body[0].value, ast.Constant) and \
                isinstance(node.body[0].value.value, str):
            node.body.pop(0)
    source = ast.unparse(tree)
    for forbidden in ("actionable", "confidence", "betUpTo", "bet_up_to",
                      "stake", "bankroll", "kelly", "qualif", "recommend"):
        assert forbidden not in source, (
            "canonical_price.py must not contain %r -- pricing and wagering "
            "authority stay separate until B2" % forbidden)


# ── synthetic-ticker resolution (the ML / F5 coverage defect) ────────────────

def _eval_row(**kw):
    row = {"marketTicker": "823251:ML_Away", "marketFamily": "ML_Away",
           "gameId": 823251, "createdAt": DECIDED,
           "provenance": {"sourceKey": "WSH@SD|ML_Away"}}
    row.update(kw)
    return row


def _gamepk_index():
    rows = []
    for ticker, team in (("KXMLBGAME-26SEP091610WSHSD-WSH", "WSH"),
                         ("KXMLBGAME-26SEP091610WSHSD-SD", "SD")):
        r = _obs(ticker, DECIDED)
        r["gameId"] = 823251
        r["seriesTicker"] = "KXMLBGAME"
        rows.append(r)
    return _index(rows)


def test_a_synthetic_ticker_resolves_to_the_real_contract():
    """
    ML and F5 decision rows are keyed '<gamePk>:<family>', which appears nowhere
    in the order book -- measured: 0 of 8 matched before this resolver existed.
    """
    ticker, method, refusal = oj.resolve_market_ticker(_eval_row(), _gamepk_index())
    assert ticker == "KXMLBGAME-26SEP091610WSHSD-WSH"
    assert method == oj.JOIN_RESOLVED_VIA_GAMEPK and refusal is None


def test_the_home_side_resolves_to_the_other_contract():
    row = _eval_row(marketTicker="823251:ML_Home", marketFamily="ML_Home",
                    provenance={"sourceKey": "WSH@SD|ML_Home"})
    ticker, _, _ = oj.resolve_market_ticker(row, _gamepk_index())
    assert ticker == "KXMLBGAME-26SEP091610WSHSD-SD"


def test_away_and_home_never_resolve_to_the_same_contract():
    idx = _gamepk_index()
    away, _, _ = oj.resolve_market_ticker(_eval_row(), idx)
    home, _, _ = oj.resolve_market_ticker(
        _eval_row(marketTicker="823251:ML_Home", marketFamily="ML_Home",
                  provenance={"sourceKey": "WSH@SD|ML_Home"}), idx)
    assert away != home


def test_a_real_kalshi_ticker_is_used_as_is():
    row = _eval_row(marketTicker="KXMLBTOTAL-26SEP091610WSHSD-9",
                    marketFamily="KXMLBTOTAL")
    ticker, method, _ = oj.resolve_market_ticker(row, _gamepk_index())
    assert ticker == "KXMLBTOTAL-26SEP091610WSHSD-9"
    assert method == oj.JOIN_EXACT


def test_an_unmapped_family_refuses_rather_than_guessing_a_series():
    """NRFI/TT_Over carry synthetic keys with no known series -- refuse."""
    row = _eval_row(marketTicker="823251:NRFI", marketFamily="NRFI")
    ticker, method, refusal = oj.resolve_market_ticker(row, _gamepk_index())
    assert ticker is None
    assert method == oj.JOIN_UNRESOLVABLE_FAMILY and refusal


def test_a_missing_source_key_refuses_rather_than_guessing_the_team():
    row = _eval_row(provenance={})
    ticker, method, _ = oj.resolve_market_ticker(row, _gamepk_index())
    assert ticker is None and method == oj.JOIN_UNRESOLVABLE_SIDE


def test_two_candidate_contracts_refuse_rather_than_choosing():
    """A doubleheader gives two contracts for one gamePk+team. CR-3 territory."""
    rows = []
    for ticker in ("KXMLBGAME-26SEP091610WSHSD-WSH", "KXMLBGAME-26SEP092010WSHSD-WSH"):
        r = _obs(ticker, DECIDED)
        r["gameId"] = 823251
        r["seriesTicker"] = "KXMLBGAME"
        rows.append(r)
    ticker, method, refusal = oj.resolve_market_ticker(_eval_row(), _index(rows))
    assert ticker is None
    assert method == oj.JOIN_AMBIGUOUS_TICKER
    assert "refusing to choose" in refusal


def test_resolution_requires_the_matching_series():
    """An F5 family must not resolve to a full-game contract."""
    row = _eval_row(marketTicker="823251:F5_ML_Away", marketFamily="F5_ML_Away",
                    provenance={"sourceKey": "WSH@SD|F5_ML_Away"})
    ticker, method, _ = oj.resolve_market_ticker(row, _gamepk_index())
    assert ticker is None, "KXMLBGAME observations must not satisfy an F5 family"
    assert method == oj.JOIN_NO_TICKER_HISTORY


def test_source_key_team_parsing():
    assert oj.parse_source_key_teams("WSH@SD|ML_Away") == ("WSH", "SD")
    assert oj.parse_source_key_teams("garbage") == (None, None)
    assert oj.parse_source_key_teams(None) == (None, None)
