"""
validate_slate_final: a game that is not going to start (cancelled,
postponed, suspended) or has already started/finished needs no pregame
pinnacleVF -- but a game that will not be played must never carry a
real-money Accepted row.

Regression anchor: 2026-09-27, BAL@NYY was `Cancelled` while the other 14
games were Final. The validator knew only ('Final', 'In Progress',
'Postponed'), so the cancelled game's missing pinnacleVF failed the whole
slate (runs 36356749630, 36360663154).

Deterministic: pure validate_final() on hand-built slates.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_validate_slate_final_immutable import (  # noqa: E402
    REQUIRED_MARKETS, make_good_game, make_ledger_row, make_slate)

import validate_slate_final as vsf  # noqa: E402

DATE = "2026-06-16"
RULE71 = "Rule 71"
NEVER_BET = "must never be bet"


def _no_price_game(status, ledger_status="Missing Data"):
    g = make_good_game(status=status)
    g["pinnacleVF"] = None
    g["marketLedger"] = [make_ledger_row(market=m, status=ledger_status) for m in REQUIRED_MARKETS]
    return g


@pytest.mark.parametrize("status", ["Cancelled", "Canceled", "Suspended", "Postponed",
                                    "Postponed - Rain", "Postponed - Other",
                                    "Final", "Game Over", "Completed Early",
                                    "In Progress", "Rain Delay", "Live"])
def test_missing_pinnacle_is_a_warning_for_games_that_will_not_start_pregame(status):
    errors, warnings = vsf.validate_final(make_slate([_no_price_game(status)]), DATE)
    assert not [e for e in errors if RULE71 in e], errors
    assert any("pinnacleVF.away missing for %s game" % status in w for w in warnings)


@pytest.mark.parametrize("status", ["Scheduled", "Pre-Game", "Warmup", "Delayed",
                                    "Delayed Start", ""])
def test_missing_pinnacle_is_still_an_error_for_a_game_that_will_be_played(status):
    errors, _ = vsf.validate_final(make_slate([_no_price_game(status)]), DATE)
    assert any(RULE71 in e for e in errors), status


def test_2026_09_27_fourteen_final_one_cancelled_validates_clean():
    games = [_no_price_game("Final") for _ in range(14)] + [_no_price_game("Cancelled")]
    for i, g in enumerate(games):
        g["away"]["abbr"], g["home"]["abbr"] = "A%02d" % i, "H%02d" % i
    errors, _ = vsf.validate_final(make_slate(games), DATE)
    assert errors == []


@pytest.mark.parametrize("status", ["Cancelled", "Canceled", "Postponed", "Suspended",
                                    "Postponed - Rain"])
@pytest.mark.parametrize("tier", ["HIGH", "MEDIUM"])
def test_a_not_played_game_with_a_real_money_accepted_row_is_a_hard_error(status, tier):
    g = _no_price_game(status)
    g["marketLedger"][0] = make_ledger_row(market=REQUIRED_MARKETS[0], status="Accepted",
                                           confidence=tier)
    errors, _ = vsf.validate_final(make_slate([g]), DATE)
    assert [e for e in errors if NEVER_BET in e] == [
        "KC@WSH/%s: Accepted real-money row on a %s game -- a game that will not be "
        "played must never be bet" % (REQUIRED_MARKETS[0], status)]


@pytest.mark.parametrize("status,ledger_status,tier", [
    ("Cancelled", "Accepted", "PAPER"),     # paper tracking is not a bet
    ("Cancelled", "Rejected", "HIGH"),
    ("Delayed Start", "Accepted", "HIGH"),  # still going to be played
    ("Scheduled", "Accepted", "HIGH"),
    ("Final", "Accepted", "HIGH"),          # already live-blocked by write_pending_bets
])
def test_never_bet_rule_does_not_fire_outside_its_scope(status, ledger_status, tier):
    g = make_good_game(status=status)
    g["marketLedger"] = [make_ledger_row(market=m, status=ledger_status, confidence=tier)
                         for m in REQUIRED_MARKETS]
    errors, _ = vsf.validate_final(make_slate([g]), DATE)
    assert not [e for e in errors if NEVER_BET in e]


def test_status_sets_are_derived_from_postponed_guard_not_hand_copied():
    import postponed_guard as pg
    assert vsf.NOT_PLAYED_STATUSES <= pg.POSTPONED_STATUSES
    assert not (vsf.NOT_PLAYED_STATUSES & (pg.IN_PLAY_STATUSES | pg.FINAL_STATUSES))
    assert {"Cancelled", "Postponed", "Suspended"} <= vsf.NOT_PLAYED_STATUSES
    assert not ({"Delayed", "Delayed Start", "Scheduled", "Pre-Game", "Warmup"}
                & vsf.NO_PREGAME_PRICE_STATUSES)
    assert {"Final", "In Progress", "Postponed", "Cancelled"} <= vsf.NO_PREGAME_PRICE_STATUSES
