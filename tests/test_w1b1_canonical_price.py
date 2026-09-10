#!/usr/bin/env python3
"""
tests/test_w1b1_canonical_price.py
==================================
WAVE 1, subwave B1. Guards the canonical executable-price object, the
decision-time observation join, and the fail-closed side resolver.

The tests that matter most are the refusals. A pricing layer that always
produces a number is exactly the defect being fixed: the old path always
produced one, because it fell back to a midpoint whenever the real ask was
missing, and the first draft of the shadow report always produced a SIDE,
because it defaulted to YES whenever the record did not say. Here, "no
executable price" and "side unproven" are first-class, explicitly-reasoned
outcomes and most of this file exists to prove they happen when they should.
"""

import os
import sys
from decimal import Decimal

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import canonical_price as cp  # noqa: E402
from lib.edgelab import decision_side as ds  # noqa: E402
from lib.edgelab import observation_join as oj  # noqa: E402


# ── the executable band is a BOUND, not a whole-cent lattice ─────────────────

def test_zero_is_absent_and_says_so():
    """
    The single rule that stops `(bid or 0)` arithmetic at the source. A zero bid
    means no resting bid exists -- and it reports its own distinct status, so it
    can never be confused with a rejected-for-some-other-reason quote.
    """
    assert cp.normalize_quote(0) == (None, cp.QUOTE_ABSENT_ZERO)
    assert cp.normalize_quote(0.0) == (None, cp.QUOTE_ABSENT_ZERO)
    assert cp.normalize_quote("0.0000", cp.UNIT_DOLLARS) == (None, cp.QUOTE_ABSENT_ZERO)
    assert cp.normalize_quote(None) == (None, cp.QUOTE_ABSENT_NULL)


def test_a_genuine_subpenny_quote_is_a_price_and_zero_is_not():
    """
    THE defect this module was corrected for. Hard-coding a 1..99 INTEGER band
    rejected every quote finer than a cent -- so a real 0.5c bid and a
    no-such-bid 0 collapsed into the same answer. Kalshi supports sub-cent
    grids (deci_cent, tapered_deci_cent) and its wire format carries four
    decimal places of dollars, i.e. 0.01c resolution.
    """
    assert cp.normalize_cents(Decimal("0.5")) == Decimal("0.5")
    assert cp.normalize_cents(Decimal("0.1")) == Decimal("0.1")
    assert cp.normalize_cents(Decimal("0.01")) == Decimal("0.01")
    # ...and none of them is the same answer as "there is no quote".
    assert cp.normalize_cents(0) is None
    for subpenny in ("0.5", "0.1", "0.01"):
        value, status = cp.normalize_quote(Decimal(subpenny))
        assert status == cp.QUOTE_ACCEPTED and value > 0


@pytest.mark.parametrize("cents,expected", [
    ("0", None),                       # no resting quote
    ("0.01", Decimal("0.01")),         # finest the wire can express
    ("0.1", Decimal("0.1")),           # deci-cent grid
    ("0.5", Decimal("0.5")),           # tapered deci-cent grid
    ("1", Decimal("1")),               # the old MIN_TRADEABLE floor
    ("50", Decimal("50")),
    ("99", Decimal("99")),             # the old MAX_TRADEABLE ceiling
    ("99.5", Decimal("99.5")),         # above the old ceiling, still tradeable
    ("99.99", Decimal("99.99")),
    ("100", None),                     # settlement value, not a purchase
    ("100.01", None),
    ("-1", None),
])
def test_the_executable_band_is_open_on_both_ends(cents, expected):
    assert cp.normalize_cents(Decimal(cents)) == expected


def test_the_unit_is_declared_and_never_guessed():
    """
    A magnitude heuristic was wrong in both directions on a money path: it read
    a real 1-cent quote as $1.00 and discarded it (3,810 such quotes in ten
    partitions), and would have read a registry $1.00 as a 1-cent bargain.
    """
    assert cp.normalize_cents(1) == 1                        # a real 1c quote
    assert cp.normalize_cents(1.0, cp.UNIT_DOLLARS) is None   # $1.00 = 100c
    assert cp.normalize_cents("0.99", cp.UNIT_DOLLARS) == Decimal("99")
    assert cp.normalize_cents("0.0050", cp.UNIT_DOLLARS) == Decimal("0.5")
    with pytest.raises(ValueError):
        cp.normalize_cents(50, "furlongs")


def test_money_arithmetic_is_exact_not_binary_float():
    """
    `0.29 * 100` is 28.999999999999996 in binary float. On a whole-cent grid
    that rounds away unnoticed, which is precisely why it survives until the
    grid gets finer -- at which point the error lands in the same decimal place
    as the price. Every conversion here goes through the string form.
    """
    assert cp.normalize_cents("0.29", cp.UNIT_DOLLARS) == Decimal("29")
    assert cp.normalize_cents("0.29", cp.UNIT_DOLLARS) - Decimal("29") == 0
    # 0.1 + 0.2 != 0.3 in float; the derived NO ask must not inherit that.
    price = cp.build_price(cp.SIDE_NO, yes_bid="0.1", yes_ask="0.3")
    assert price["executablePrice"] == Decimal("99.9")
    assert str(price["executablePrice"]) == "99.9"


def test_serialisation_preserves_precision_as_a_decimal_string():
    price = cp.build_price(cp.SIDE_YES, yes_bid="0.1", yes_ask="0.5")
    payload = cp.to_jsonable(price)
    assert payload["executablePrice"] == "0.5"
    assert isinstance(payload["executablePrice"], str)
    assert Decimal(payload["executablePrice"]) == price["executablePrice"]
    assert payload["priceUnitDeclared"] == cp.UNIT_CENTS


def test_the_declared_grid_is_recorded_and_never_snapped_to():
    """
    Off-grid is REPORTED, never corrected. Snapping a quote to a tick we believe
    in would replace an exchange fact with our own assumption.
    """
    on = cp.build_price(cp.SIDE_YES, yes_ask="44", grid=cp.GRID_LINEAR_CENT)
    off = cp.build_price(cp.SIDE_YES, yes_ask="44.5", grid=cp.GRID_LINEAR_CENT)
    assert on["book"]["yesAskOnGrid"] is True
    assert off["book"]["yesAskOnGrid"] is False
    assert off["executablePrice"] == Decimal("44.5"), "an off-grid quote is still a quote"
    deci = cp.build_price(cp.SIDE_YES, yes_ask="44.5", grid=cp.GRID_DECI_CENT)
    assert deci["book"]["yesAskOnGrid"] is True


def test_an_unknown_or_tapered_grid_checks_nothing_rather_than_inventing_a_tick():
    for grid in (cp.GRID_UNKNOWN, cp.GRID_TAPERED_DECI_CENT):
        price = cp.build_price(cp.SIDE_YES, yes_ask="44.5", grid=grid)
        assert price["book"]["yesAskOnGrid"] is None
        assert price["executablePrice"] == Decimal("44.5")


def test_the_archive_measurement_that_justifies_the_default_grid():
    """
    Every one of the 3,255 MLB markets whose raw metadata this repository
    archived declares `linear_cent` with a 1-cent step, and no archived quote is
    non-integer. That finding is why linear_cent is the empirical reality -- and
    why it is NOT the hard-coded invariant, since a grid change upstream would
    otherwise silently delete real quotes.
    """
    assert cp.GRID_TICK_CENTS[cp.GRID_LINEAR_CENT] == Decimal("1")
    assert cp.GRID_TICK_CENTS[cp.GRID_DECI_CENT] == Decimal("0.1")
    assert cp.GRID_TAPERED_DECI_CENT not in cp.GRID_TICK_CENTS
    # The default must be permissive, or the correction is undone.
    assert cp.GRID_UNKNOWN is None


# ── one-sided books ──────────────────────────────────────────────────────────

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
    assert price["book"]["yesBidStatus"] == cp.QUOTE_ABSENT_ZERO
    assert price["executablePrice"] != Decimal("40.5"), "the halved midpoint is unreachable"


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


def test_no_is_derived_exactly_for_a_subpenny_bid_too():
    price = cp.build_price(cp.SIDE_NO, yes_bid="0.5", yes_ask="1")
    assert price["executablePrice"] == Decimal("99.5")
    assert cp.to_jsonable(price)["executablePrice"] == "99.5"


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


def test_an_unrecognised_or_absent_side_refuses():
    for side in ("MAYBE", None, ""):
        price = cp.build_price(side, yes_bid=44, yes_ask=46)
        assert price["executablePrice"] is None
        assert price["refusalReason"] == cp.UNKNOWN_SIDE


def test_raw_book_is_preserved_alongside_the_rejection():
    """A reader must be able to see WHY a quote was refused, not only that it was."""
    price = cp.build_price(cp.SIDE_NO, yes_bid=0, yes_ask=81)
    assert price["book"]["yesBidRaw"] == 0
    assert price["book"]["yesBid"] is None
    assert price["book"]["yesBidStatus"] == cp.QUOTE_ABSENT_ZERO


def test_an_unreadable_quote_is_distinguished_from_an_absent_one():
    price = cp.build_price(cp.SIDE_YES, yes_bid="not-a-number", yes_ask=46)
    assert price["book"]["yesBidStatus"] == cp.QUOTE_REJECTED_UNPARSEABLE
    assert price["book"]["yesAskStatus"] == cp.QUOTE_ACCEPTED


def test_executable_probability_is_exact_and_never_a_midpoint():
    price = cp.build_price(cp.SIDE_YES, yes_bid=40, yes_ask=60)
    assert cp.executable_probability(price) == Decimal("0.6")
    assert cp.executable_probability(cp.build_price(cp.SIDE_YES, yes_bid=40)) is None
    sub = cp.build_price(cp.SIDE_YES, yes_ask="0.5")
    assert cp.executable_probability(sub) == Decimal("0.005")


# ── side resolution: fail closed, never default to YES ───────────────────────

def _contract(**kw):
    obs = {"marketFamily": "game_result", "team": "WSH", "outcomeLabel": "Win",
           "comparisonOperator": "OVER", "threshold": None,
           "title": "Washington wins"}
    obs.update(kw)
    return obs


def _decision(**kw):
    row = {"selection": "ML_Away", "side": None, "threshold": None,
           "provenance": {"sourceKey": "WSH@SD|ML_Away"}}
    row.update(kw)
    return row


def test_a_record_with_no_side_field_is_never_assumed_to_be_yes():
    """
    THE fail-open defect. `side` is null on 100% of 144,937 archived evaluation
    rows, so an "assume YES" fallback fired on every single one of them while
    reading as though it fired on none.
    """
    side, basis, refusal, _ = ds.resolve_side(
        {"selection": None, "side": None}, _contract())
    assert side is None
    assert basis is None
    assert refusal == ds.REFUSE_NO_SELECTION


def test_no_refusal_path_ever_returns_a_side():
    """Every refusal reason must come with side=None. Belt and braces."""
    cases = [
        ({"selection": None}, _contract()),
        ({"selection": "ML_Away"}, None),
        (_decision(selection="Game_Total"), _contract(marketFamily="game_total")),
        (_decision(selection="RL_Away"), _contract(marketFamily="winning_margin")),
        (_decision(selection="ML_Away", provenance={}), _contract()),
        (_decision(selection="Nonsense"), _contract(marketFamily="hitter_hits")),
    ]
    for record, obs in cases:
        side, basis, refusal, _ = ds.resolve_side(record, obs)
        assert side is None and basis is None and refusal, (record, obs)


def test_an_explicit_side_on_the_record_wins():
    side, basis, refusal, _ = ds.resolve_side(
        _decision(side="NO"), _contract())
    assert (side, basis, refusal) == (ds.SIDE_NO, ds.BASIS_DECLARED, None)


def test_a_selection_equal_to_the_contract_title_is_yes():
    """
    The strongest evidence available -- the two strings are byte-identical.
    Measured: 9,490 of 9,490 title-style decision rows match, none disagree.
    """
    row = _decision(selection="Over 10.5 runs scored")
    obs = _contract(marketFamily="game_total", team=None,
                    title="Over 10.5 runs scored")
    side, basis, refusal, _ = ds.resolve_side(row, obs)
    assert (side, basis, refusal) == (ds.SIDE_YES, ds.BASIS_TITLE_IDENTITY, None)


# --- Away / Home ---

def test_a_moneyline_on_its_own_team_contract_is_yes():
    side, basis, _, evidence = ds.resolve_side(_decision(), _contract(team="WSH"))
    assert (side, basis) == (ds.SIDE_YES, ds.BASIS_TEAM_MATCHES_CONTRACT)
    assert evidence["selectionTeam"] == "WSH"


def test_a_full_game_moneyline_on_the_sibling_contract_is_no():
    """
    A regular-season MLB game cannot end tied, so "not the home team" IS "the
    away team" and buying NO on the home contract is the same bet.
    """
    side, basis, _, _ = ds.resolve_side(_decision(), _contract(team="SD"))
    assert (side, basis) == (ds.SIDE_NO, ds.BASIS_MONEYLINE_COMPLEMENT)


def test_the_home_selection_mirrors_the_away_selection():
    row = _decision(selection="ML_Home", provenance={"sourceKey": "WSH@SD|ML_Home"})
    assert ds.resolve_side(row, _contract(team="SD"))[0] == ds.SIDE_YES
    assert ds.resolve_side(row, _contract(team="WSH"))[0] == ds.SIDE_NO


def test_a_moneyline_on_an_unrelated_teams_contract_refuses():
    side, _, refusal, _ = ds.resolve_side(_decision(), _contract(team="LAD"))
    assert side is None and refusal == ds.REFUSE_TEAM_MISMATCH


def test_a_partial_game_moneyline_refuses_the_complement_because_it_can_tie():
    """
    A first-5-innings segment CAN end tied -- Kalshi lists a separate TIE
    contract in the same event and the archive carries 672 observations of one.
    So NO on the sibling team means "did not win the segment", which INCLUDES
    the tie, and is a different bet from this selection.
    """
    row = _decision(selection="F5_ML_Away",
                    provenance={"sourceKey": "WSH@SD|F5_ML_Away"})
    own = _contract(marketFamily="inning_result", team="WSH",
                    title="Will Washington be the F5 winner?")
    sibling = _contract(marketFamily="inning_result", team="SD",
                        title="Will San Diego be the F5 winner?")
    assert ds.resolve_side(row, own)[0] == ds.SIDE_YES
    side, _, refusal, _ = ds.resolve_side(row, sibling)
    assert side is None
    assert refusal == ds.REFUSE_SEGMENT_NOT_BINARY


def test_the_moneyline_and_f5_rules_differ_deliberately():
    """Regression: the tie-safety above must not be an accident of the fixture."""
    row_full = _decision(selection="ML_Away")
    row_f5 = _decision(selection="F5_ML_Away",
                       provenance={"sourceKey": "WSH@SD|F5_ML_Away"})
    assert ds.resolve_side(row_full, _contract(team="SD"))[0] == ds.SIDE_NO
    assert ds.resolve_side(row_f5,
                           _contract(marketFamily="inning_result", team="SD"))[0] is None


# --- YES / NO on a first-inning run ---

def test_yrfi_is_yes_and_nrfi_is_no_on_the_same_contract():
    """
    The contract's YES is "1st inning: Over 0.5 runs". A run either scores or it
    does not, so NRFI is the strict complement and prices as NO -- at 100-yesBid,
    not at the YES ask.
    """
    obs = _contract(marketFamily="first_inning_run", team=None, outcomeLabel=None,
                    title="1st inning: Over 0.5 runs")
    yes_side, yes_basis, _, _ = ds.resolve_side(_decision(selection="YRFI"), obs)
    no_side, no_basis, _, _ = ds.resolve_side(_decision(selection="NRFI"), obs)
    assert (yes_side, yes_basis) == (ds.SIDE_YES, ds.BASIS_FIRST_INNING_RUN_YES)
    assert (no_side, no_basis) == (ds.SIDE_NO, ds.BASIS_FIRST_INNING_RUN_NO)


def test_nrfi_prices_the_other_end_of_the_book_than_yrfi():
    """The point of getting the side right, in cents."""
    obs = _contract(marketFamily="first_inning_run", team=None,
                    title="1st inning: Over 0.5 runs")
    yrfi = cp.build_price(ds.resolve_side(_decision(selection="YRFI"), obs)[0],
                          yes_bid=58, yes_ask=61)
    nrfi = cp.build_price(ds.resolve_side(_decision(selection="NRFI"), obs)[0],
                          yes_bid=58, yes_ask=61)
    assert yrfi["executablePrice"] == 61
    assert nrfi["executablePrice"] == 42          # 100 - 58
    assert nrfi["executablePrice"] != yrfi["executablePrice"]


# --- OVER / UNDER ---

def test_a_team_total_matches_on_team_and_line_including_the_half_run_convention():
    """
    Kalshi writes the line as "over 4.5 runs"; the model writes the same line as
    "5+". Measured: the offset is exactly +0.5 on 1,432 of 1,432 archived rows.
    """
    row = _decision(selection="TT_Away_Over", threshold=5,
                    provenance={"sourceKey": "WSH@SD|TT_Away_Over"})
    obs = _contract(marketFamily="team_total", team="WSH", outcomeLabel=None,
                    threshold=4.5, title="Will Washington score over 4.5 runs?")
    side, basis, _, evidence = ds.resolve_side(row, obs)
    assert (side, basis) == (ds.SIDE_YES, ds.BASIS_TEAM_TOTAL_SAME_LINE)
    assert evidence["decisionLine"] == 5 and evidence["contractLine"] == 4.5


def test_a_team_total_on_a_different_line_refuses():
    row = _decision(selection="TT_Away_Over", threshold=5,
                    provenance={"sourceKey": "WSH@SD|TT_Away_Over"})
    obs = _contract(marketFamily="team_total", team="WSH", threshold=6.5)
    side, _, refusal, _ = ds.resolve_side(row, obs)
    assert side is None and refusal == ds.REFUSE_LINE_MISMATCH


def test_a_team_total_on_the_other_teams_contract_refuses():
    row = _decision(selection="TT_Away_Over", threshold=5,
                    provenance={"sourceKey": "WSH@SD|TT_Away_Over"})
    obs = _contract(marketFamily="team_total", team="SD", threshold=4.5)
    side, _, refusal, _ = ds.resolve_side(row, obs)
    assert side is None and refusal == ds.REFUSE_TEAM_MISMATCH


def test_a_directionless_total_selection_refuses():
    """
    'Game_Total' names a market but not an Over or an Under. The only things
    that could supply the missing direction are the model's own probability or
    the market's price, and a side chosen from the price is not a side.
    """
    row = _decision(selection="Game_Total")
    obs = _contract(marketFamily="game_total", team=None, threshold=8.5,
                    title="Over 8.5 runs scored")
    side, _, refusal, _ = ds.resolve_side(row, obs)
    assert side is None and refusal == ds.REFUSE_NO_DIRECTION


def test_a_run_line_selection_refuses_because_the_handicap_is_not_recorded():
    for token in ("RL_Away", "RL_Home"):
        row = _decision(selection=token,
                        provenance={"sourceKey": "WSH@SD|" + token})
        obs = _contract(marketFamily="winning_margin", team="WSH", threshold=1.5)
        side, _, refusal, _ = ds.resolve_side(row, obs)
        assert side is None
        assert refusal == ds.REFUSE_RUN_LINE_HANDICAP_UNPROVEN


def test_every_accepted_basis_is_in_the_declared_vocabulary():
    obs_ml = _contract(team="WSH")
    obs_rfi = _contract(marketFamily="first_inning_run", team=None,
                        title="1st inning: Over 0.5 runs")
    for record, obs in ((_decision(), obs_ml),
                        (_decision(selection="YRFI"), obs_rfi),
                        (_decision(selection="NRFI"), obs_rfi),
                        (_decision(side="YES"), obs_ml)):
        side, basis, refusal, _ = ds.resolve_side(record, obs)
        assert side in (ds.SIDE_YES, ds.SIDE_NO)
        assert basis in ds.VALID_BASES and refusal is None


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
    for name in ("canonical_price.py", "decision_side.py"):
        path = os.path.join(ROOT, "lib", "edgelab", name)
        with open(path) as handle:
            tree = ast.parse(handle.read())
        # Strip docstrings: the module prose legitimately DESCRIBES the boundary
        # it must not cross, so scanning it would flag the very sentence that
        # states the rule. Only executable code is inspected.
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
                "%s must not contain %r -- pricing and wagering authority stay "
                "separate until B2" % (name, forbidden))


# ── synthetic-ticker resolution (the ML / F5 / RFI coverage defect) ──────────

def _eval_row(**kw):
    row = {"marketTicker": "823251:ML_Away", "marketFamily": "ML_Away",
           "gameId": 823251, "createdAt": DECIDED, "selection": "ML_Away",
           "provenance": {"sourceKey": "WSH@SD|ML_Away"}}
    row.update(kw)
    return row


def _gamepk_index(extra=()):
    rows = []
    for ticker in ("KXMLBGAME-26SEP091610WSHSD-WSH", "KXMLBGAME-26SEP091610WSHSD-SD"):
        r = _obs(ticker, DECIDED)
        r["gameId"] = 823251
        r["seriesTicker"] = "KXMLBGAME"
        r["marketFamily"] = "game_result"
        r["team"] = ticker.rsplit("-", 1)[1]
        rows.append(r)
    rows.extend(extra)
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


def test_a_first_inning_run_family_resolves_without_a_team_suffix():
    """
    An RFI event lists exactly ONE contract -- 56 of 56 games measured -- so the
    gamePk alone names it and there is no team suffix to match.
    """
    rfi = _obs("KXMLBRFI-26SEP091610WSHSD", DECIDED)
    rfi["gameId"] = 823251
    rfi["seriesTicker"] = "KXMLBRFI"
    row = _eval_row(marketTicker="823251:NRFI", marketFamily="NRFI",
                    selection="NRFI", provenance={"sourceKey": "WSH@SD|NRFI"})
    ticker, method, _ = oj.resolve_market_ticker(row, _gamepk_index(extra=[rfi]))
    assert ticker == "KXMLBRFI-26SEP091610WSHSD"
    assert method == oj.JOIN_RESOLVED_VIA_GAMEPK


def test_a_real_kalshi_ticker_is_used_as_is():
    row = _eval_row(marketTicker="KXMLBTOTAL-26SEP091610WSHSD-9",
                    marketFamily="KXMLBTOTAL")
    ticker, method, _ = oj.resolve_market_ticker(row, _gamepk_index())
    assert ticker == "KXMLBTOTAL-26SEP091610WSHSD-9"
    assert method == oj.JOIN_EXACT


def test_a_strike_bearing_family_refuses_because_the_record_carries_no_line():
    """
    TT_*/Game_Total/RL_* synthetic keys name a market with MANY contracts per
    event, one per strike (740 team-total contracts on 2026-09-09 alone), and
    every such decision row carries threshold=None. Without a strike there is no
    single contract to name.
    """
    for family in ("TT_Away_Over", "Game_Total", "RL_Home"):
        row = _eval_row(marketTicker="823251:" + family, marketFamily=family,
                        selection=family, threshold=None)
        ticker, method, refusal = oj.resolve_market_ticker(row, _gamepk_index())
        assert ticker is None, family
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


# ── the whole chain, end to end, fail-closed ─────────────────────────────────

def test_price_for_record_refuses_when_the_side_cannot_be_proven():
    """
    The end-to-end guarantee: a record whose side is unprovable gets NO price,
    not a YES-priced one. Everything upstream of the side succeeds here -- the
    ticker resolves and the quote joins -- so only the side refusal can be
    producing the None.
    """
    total = _obs("KXMLBTOTAL-26SEP091610WSHSD-9", DECIDED, yes_bid=44, yes_ask=46)
    total["gameId"] = 823251
    total["seriesTicker"] = "KXMLBTOTAL"
    total["marketFamily"] = "game_total"
    total["title"] = "Over 8.5 runs scored"
    total["threshold"] = 8.5
    row = _eval_row(marketTicker="KXMLBTOTAL-26SEP091610WSHSD-9",
                    marketFamily="KXMLBTOTAL", selection="Game_Total")
    price, detail = oj.price_for_record(row, _gamepk_index(extra=[total]))
    assert detail["join"]["matched"] is True, "the quote WAS available"
    assert detail["side"] is None
    assert detail["sideRefusal"] == ds.REFUSE_NO_DIRECTION
    assert price is None, "an unproven side must never be priced as YES"


def test_price_for_record_prices_a_proven_side_and_records_its_provenance():
    price, detail = oj.price_for_record(_eval_row(), _gamepk_index())
    assert detail["side"] == ds.SIDE_YES
    assert detail["sideBasis"] == ds.BASIS_TEAM_MATCHES_CONTRACT
    assert price["executablePrice"] == 46
    assert price["sideBasis"] == ds.BASIS_TEAM_MATCHES_CONTRACT
    assert price["priceBasis"] == cp.BASIS_YES_ASK


def _rfi_index():
    """One RFI contract, whose YES is 'a run scores in the 1st'."""
    rfi = _obs("KXMLBRFI-26SEP091610WSHSD", DECIDED, yes_bid=36, yes_ask=37)
    rfi["gameId"] = 823251
    rfi["seriesTicker"] = "KXMLBRFI"
    rfi["marketFamily"] = "first_inning_run"
    rfi["team"] = None
    rfi["title"] = "1st inning: Over 0.5 runs"
    return _gamepk_index(extra=[rfi])


def _rfi_row(selection):
    return _eval_row(marketTicker="823251:" + selection, marketFamily=selection,
                     selection=selection,
                     provenance={"sourceKey": "WSH@SD|" + selection})


def test_a_resolved_ticker_never_decides_the_side_by_itself():
    """
    REGRESSION, and the reason this test exists is worth stating. An earlier
    version short-circuited: if the synthetic key resolved via gamePk, call it
    YES -- reasoning that the join finds a contract by matching the team this
    selection backs. True for moneylines, FALSE for the first-inning-run family,
    where the event has ONE contract and the gamePk alone resolves it. NRFI
    therefore resolved to the "a run scores" contract and was declared YES: the
    exact opposite of the bet. The live corpus showed it as a legacy 63.5c
    against a shadow 37.0c on the same row -- complements to the cent.

    A side must come from what the contract MEANS, never from how it was found.
    """
    idx = _rfi_index()
    nrfi_price, nrfi = oj.price_for_record(_rfi_row("NRFI"), idx)
    yrfi_price, yrfi = oj.price_for_record(_rfi_row("YRFI"), idx)

    assert nrfi["tickerMethod"] == oj.JOIN_RESOLVED_VIA_GAMEPK
    assert yrfi["tickerMethod"] == oj.JOIN_RESOLVED_VIA_GAMEPK, "same resolution path"

    assert nrfi["side"] == ds.SIDE_NO, "NRFI is the complement, not the contract's YES"
    assert nrfi["sideBasis"] == ds.BASIS_FIRST_INNING_RUN_NO
    assert yrfi["side"] == ds.SIDE_YES
    assert yrfi["sideBasis"] == ds.BASIS_FIRST_INNING_RUN_YES

    # And the prices must land on opposite ends of the same book.
    assert yrfi_price["executablePrice"] == 37            # the YES ask
    assert nrfi_price["executablePrice"] == 64            # 100 - yesBid(36)
    assert (yrfi_price["executablePrice"]
            + nrfi_price["executablePrice"]) != 100, "ask and 100-bid, not a midpoint pair"
