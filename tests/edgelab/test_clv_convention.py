"""Canonical CLV convention: positive means entered cheaper than the close."""

from lib.edgelab.clv_convention import (
    CONVENTION_ID, SIDE_YES, SIDE_NO, executable_price_cents, good_clv_cents,
    good_clv_from_quotes, good_clv_from_implied, is_good,
    invert_legacy_entry_minus_closing,
)


def test_convention_id_states_the_direction():
    assert CONVENTION_ID == "POSITIVE_IS_GOOD_CLOSING_MINUS_ENTRY_V1"


def test_yes_side_executable_price_is_the_yes_ask():
    assert executable_price_cents({"yesBid": 40.0, "yesAsk": 42.0}, SIDE_YES) == 42.0


def test_no_side_uses_archived_no_ask_when_present():
    q = {"yesBid": 40.0, "yesAsk": 42.0, "noAsk": 59.0}
    assert executable_price_cents(q, SIDE_NO) == 59.0


def test_no_side_falls_back_to_complement_of_yes_bid():
    assert executable_price_cents({"yesBid": 40.0, "yesAsk": 42.0}, SIDE_NO) == 60.0


def test_missing_book_side_returns_none_never_a_guess():
    assert executable_price_cents({"yesBid": 40.0}, SIDE_YES) is None
    assert executable_price_cents({"yesAsk": 42.0}, SIDE_NO) is None
    assert executable_price_cents(None, SIDE_YES) is None


def test_midpoint_is_never_used():
    """A quote whose mid would be 41 must price YES at the ask, 42."""
    assert executable_price_cents({"yesBid": 40.0, "yesAsk": 42.0}, SIDE_YES) == 42.0


def test_buying_cheaper_than_the_close_is_positive():
    assert good_clv_cents(33.0, 34.0) == 1.0
    assert is_good(good_clv_cents(33.0, 34.0))


def test_paying_more_than_the_close_is_negative():
    assert good_clv_cents(62.55, 61.0) == -1.55
    assert not is_good(good_clv_cents(62.55, 61.0))


def test_entering_at_the_close_is_zero_and_not_good():
    assert good_clv_cents(50.0, 50.0) == 0.0
    assert not is_good(0.0)


def test_yes_and_no_sides_are_both_evaluated_from_the_buyer_perspective():
    entry = {"yesBid": 40.0, "yesAsk": 42.0}      # YES ask 42, NO ask 60
    closing = {"yesBid": 45.0, "yesAsk": 46.0}    # YES ask 46, NO ask 55
    # YES buyer got 42 against a 46 close -> good
    assert good_clv_from_quotes(entry, closing, SIDE_YES) == 4.0
    # NO buyer paid 60 against a 55 close -> bad
    assert good_clv_from_quotes(entry, closing, SIDE_NO) == -5.0


def test_implied_probability_form_matches_the_cents_form():
    assert good_clv_from_implied(0.33, 0.34) == 1.0
    assert good_clv_from_implied(0.6255, 0.61) == -1.55


def test_none_inputs_propagate_as_none():
    assert good_clv_cents(None, 40.0) is None
    assert good_clv_cents(40.0, None) is None
    assert good_clv_from_implied(None, 0.5) is None
    assert invert_legacy_entry_minus_closing(None) is None


def test_legacy_inverter_negates_and_is_involutive():
    assert invert_legacy_entry_minus_closing(1.55) == -1.55
    assert invert_legacy_entry_minus_closing(
        invert_legacy_entry_minus_closing(1.55)) == 1.55


def test_legacy_inverter_is_not_called_from_production():
    """It exists for a future authorized migration only."""
    import subprocess, os
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')
    out = subprocess.run(
        ["grep", "-rn", "invert_legacy_entry_minus_closing",
         os.path.join(repo, "lib"), os.path.join(repo, "scripts"),
         os.path.join(repo, "api")],
        capture_output=True, text=True).stdout
    callers = [l for l in out.splitlines() if "clv_convention.py" not in l]
    assert callers == [], "legacy inverter must not be wired into production yet"
