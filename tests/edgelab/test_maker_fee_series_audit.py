#!/usr/bin/env python3
"""
tests/edgelab/test_maker_fee_series_audit.py
=================================================
Official per-series maker-fee audit (2026-09-02).

The audit could NOT establish a maker multiplier for any MLB series:
kalshi.com, docs.kalshi.com and api.elections.kalshi.com are all
egress-blocked in this environment, WebFetch returns EGRESS_BLOCKED for
every mirror, and WebSearch returned directly contradictory claims about
whether MLB series charge a maker fee at all.

These tests lock in the two things that audit DID establish:

  1. The registry must say UNKNOWN out loud (makerFeeMultiplier is None),
     never let the module-level FEE_MULTIPLIER_MAKER_DEFAULT = 0.0 stand
     in as a silent global answer for a value nobody verified.
  2. Resolving an UNKNOWN series must be CONSERVATIVE by default -- assume
     the maker fee IS charged. Assuming a fee that may not exist can only
     make a strategy look worse; assuming it away can make a losing
     strategy look profitable, which is the direction that costs money.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import kalshi_fees as kf


FULL_MICROSTRUCTURE_MLB_SERIES = (
    "KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL", "KXMLBF5",
    "KXMLBF5TOTAL", "KXMLBF5SPREAD", "KXMLBF3", "KXMLBF7", "KXMLBRFI",
    "KXMLBINNINGWIN", "KXMLBINNINGTOTAL", "KXMLBEXTRAS",
)


def test_every_full_microstructure_series_is_in_the_fee_registry():
    """The five tier-1 series that used to fall through to UNKNOWN_SERIES
    silently are now registered explicitly."""
    for ticker in FULL_MICROSTRUCTURE_MLB_SERIES:
        assert ticker in kf.SERIES_FEE_METADATA, ticker


def test_no_mlb_series_claims_a_known_maker_multiplier():
    """Nothing was verifiable, so nothing may claim to be verified."""
    for ticker, entry in kf.SERIES_FEE_METADATA.items():
        assert entry["makerFeeMultiplier"] is None, ticker
        assert entry["makerFeeRuleConfidence"] == "UNKNOWN_NO_OFFICIAL_SOURCE_RETRIEVABLE", ticker


def test_unknown_maker_multiplier_resolves_conservatively_by_default():
    """An unestablished maker fee is assumed CHARGED, not free."""
    for ticker in FULL_MICROSTRUCTURE_MLB_SERIES:
        mult, confidence = kf.maker_fee_multiplier_for_series(ticker)
        assert mult == kf.FEE_MULTIPLIER_MAKER_DESIGNATED, ticker
        assert confidence == "UNKNOWN_ASSUMED_CHARGED_CONSERVATIVE", ticker


def test_optimistic_leg_is_available_but_must_be_asked_for_explicitly():
    """The zero-maker-fee sensitivity leg still exists -- it just can't be
    reached by accident."""
    mult, confidence = kf.maker_fee_multiplier_for_series(
        "KXMLBGAME", assume_unknown_is_charged=False)
    assert mult == kf.FEE_MULTIPLIER_MAKER_DEFAULT == 0.0
    assert confidence == "UNKNOWN_ASSUMED_FREE_OPTIMISTIC"


def test_a_series_not_in_the_registry_at_all_is_still_conservative():
    """Fail-safe, not fail-open: an unrecognised ticker must not resolve to
    a free maker fee."""
    mult, confidence = kf.maker_fee_multiplier_for_series("KXNOTAREALSERIES")
    assert mult == kf.FEE_MULTIPLIER_MAKER_DESIGNATED
    assert confidence == "UNKNOWN_ASSUMED_CHARGED_CONSERVATIVE"


def test_conservative_maker_assumption_is_never_more_favourable_than_optimistic():
    """The whole point of the conservative default: for any price and size
    it must cost at least as much as the optimistic leg, so a candidate can
    never look BETTER under the safe assumption."""
    for price in (0.1, 0.3, 0.5, 0.7, 0.9):
        cons, _ = kf.maker_fee_multiplier_for_series("KXMLBF5")
        opt, _ = kf.maker_fee_multiplier_for_series("KXMLBF5", assume_unknown_is_charged=False)
        fee_cons = kf.maker_fee(25, price, multiplier=cons)
        fee_opt = kf.maker_fee(25, price, multiplier=opt)
        assert fee_cons >= fee_opt, (price, fee_cons, fee_opt)


def test_taker_side_is_untouched_by_this_audit():
    """The taker rate is well corroborated and must not have moved --
    production gates on it."""
    assert kf.FEE_MULTIPLIER_TAKER_STANDARD == 0.07
    assert kf.taker_fee(100, 0.5) == 1.75
    for entry in kf.SERIES_FEE_METADATA.values():
        assert entry["feeMultiplier"] == kf.FEE_MULTIPLIER_TAKER_STANDARD
        assert entry["takerFeeMultiplier"] == kf.FEE_MULTIPLIER_TAKER_STANDARD


def test_existing_maker_fee_default_behavior_is_unchanged():
    """Backward compatibility: this audit is additive. maker_fee()'s own
    default and FEE_MULTIPLIER_MAKER_DEFAULT keep their previous values, so
    no existing caller silently changes behavior -- the new resolver is
    opt-in."""
    assert kf.FEE_MULTIPLIER_MAKER_DEFAULT == 0.0
    assert kf.maker_fee(100, 0.5) == 0.0
