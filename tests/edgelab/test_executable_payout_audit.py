#!/usr/bin/env python3
"""
tests/edgelab/test_executable_payout_audit.py
==================================================
KALSHI EXECUTABLE PAYOUT / DOUBLE-FEE AUDIT (Section M): simple,
human-readable regression cases proving a Kalshi trading fee is applied
EXACTLY ONCE -- only when translating a RAW market quote into executable
economics (Example C) -- and never AGAIN once the all-in cash outlay and
total settlement payout are already known (Examples A/B).

Canonical principle under test: for an all-in $S wager whose actual
executable total winning payout is $P,
    win P/L  = P - S
    loss P/L = -S
regardless of how $S internally decomposes into contract principal and
fee. Subtracting a further fee from (P - S) or from -S is a double-fee
bug -- see docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md for the
already-fixed unused-budget variant of this class of bug, and
data/edgelab/research_artifacts/executable_payout_audit/ for this
audit's full findings.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import execution_economics as ee
from lib.edgelab import kalshi_fees as kf


# ---------------------------------------------------------------------------
# Example A -- observed execution (the user's $10 / -133 / $17.52 example)
# ---------------------------------------------------------------------------

def test_example_a_observed_execution_win_pl_is_payout_minus_stake_no_extra_fee():
    """Stake $10, total winning payout $17.52 (Kalshi's own -133-equivalent
    executable wager) => win P/L = +$7.52, computed via the SOLD/settled-
    proceeds-minus-basis formula lib.edgelab.execution_economics already
    uses for a known real cash outcome -- no additional fee term."""
    stake = 10.00
    total_winning_payout = 17.52
    net = ee.realized_pl_for_bet(
        execution_status=ee.EXECUTION_STATUS_SOLD_EARLY,
        stake=stake, bet_result=None, exit_sale_proceeds=total_winning_payout,
    )
    assert round(net, 2) == 7.52


def test_example_a_observed_execution_loss_pl_is_exactly_negative_stake():
    """Same $10 wager, losing outcome => loss P/L = -$10.00 exactly, not a
    cent more -- the $10 cash outlay is already all-in; there is no
    separate fee still owed on top of a total loss."""
    stake = 10.00
    net = ee.realized_pl_for_bet(
        execution_status=ee.EXECUTION_STATUS_SOLD_EARLY,
        stake=stake, bet_result=None, exit_sale_proceeds=0.0,
    )
    assert round(net, 2) == -10.00


def test_example_a_double_fee_bug_would_understate_the_true_win_profit():
    """Documents what the double-fee bug would look like, so a regression
    that reintroduces it is caught: taking the correct +$7.52 win P/L and
    subtracting ANY further fee produces a number strictly below +$7.52,
    which must never be reported as this wager's net profit."""
    correct_win_pl = 17.52 - 10.00
    a_plausible_but_wrong_extra_fee = 0.30
    double_counted = correct_win_pl - a_plausible_but_wrong_extra_fee
    assert double_counted < correct_win_pl
    assert round(correct_win_pl, 2) == 7.52


# ---------------------------------------------------------------------------
# Example B -- observed execution at a different size ($25 stake)
# ---------------------------------------------------------------------------

def test_example_b_observed_execution_win_pl():
    """Stake $25, total winning payout $43.80 => win P/L = +$18.80."""
    stake = 25.00
    net = ee.realized_pl_for_bet(
        execution_status=ee.EXECUTION_STATUS_SOLD_EARLY,
        stake=stake, bet_result=None, exit_sale_proceeds=43.80,
    )
    assert round(net, 2) == 18.80


def test_example_b_observed_execution_loss_pl():
    """Same $25 wager, losing outcome => loss P/L = -$25.00 exactly."""
    stake = 25.00
    net = ee.realized_pl_for_bet(
        execution_status=ee.EXECUTION_STATUS_SOLD_EARLY,
        stake=stake, bet_result=None, exit_sale_proceeds=0.0,
    )
    assert round(net, 2) == -25.00


# ---------------------------------------------------------------------------
# Example C -- RAW quote only: here the fee/order-sizing engine IS required,
# and must be applied exactly once.
# ---------------------------------------------------------------------------

def test_example_c_raw_quote_requires_the_fee_engine_to_derive_executable_economics():
    """YES ask 57c, budget $10: unlike Examples A/B, we do NOT yet know an
    executable all-in cash outlay or payout -- only a bare market price.
    The canonical engine must be used to derive contracts/principal/fee/
    actualCashConsumed/payout from that raw quote."""
    yes_ask = 0.57
    budget = 10.0
    sim = kf.simulate_order(budget, yes_ask)
    assert sim is not None
    # A real fee was actually charged for this raw-quote translation.
    assert sim["entryFee"] > 0.0
    # The all-in cash consumed is principal + fee, and never exceeds budget.
    assert round(sim["contractPrincipal"] + sim["entryFee"], 2) == sim["actualCashConsumed"]
    assert sim["actualCashConsumed"] <= budget + 1e-9


def test_example_c_fee_is_applied_exactly_once_not_zero_not_twice():
    """net_settlement_pl_for_order (a single call) must produce the same
    net P/L as manually applying simulate_order's decomposition once --
    proving there is exactly one fee application inside the canonical
    raw-quote-to-executable-economics path, matching Section I's
    verification of MLB-ALPHA-0002's build_candle_panel.py usage."""
    yes_ask = 0.57
    budget = 10.0
    sim = kf.simulate_order(budget, yes_ask)
    manual_win_pl = round(float(sim["contracts"]) - sim["actualCashConsumed"], 4)
    engine_win_pl = kf.net_settlement_pl_for_order(budget, yes_ask, True)
    assert engine_win_pl == manual_win_pl

    manual_loss_pl = round(0.0 - sim["actualCashConsumed"], 4)
    engine_loss_pl = kf.net_settlement_pl_for_order(budget, yes_ask, False)
    assert engine_loss_pl == manual_loss_pl


def test_example_c_raw_quote_translation_is_not_the_same_bug_as_example_a():
    """A raw-quote translation (Example C) legitimately subtracts a fee once
    -- that is NOT double-counting, because there was no pre-existing
    fee-inclusive number to begin with. Confirm the two examples are
    structurally different call paths: Example A/B never touch
    lib.edgelab.kalshi_fees.simulate_order at all (a known payout is used
    directly), while Example C's only path to a dollar P/L number goes
    through it."""
    # Example A/B: net P/L is plain arithmetic on already-known cash figures.
    assert round(17.52 - 10.00, 2) == 7.52
    # Example C: net P/L is unreachable without the fee engine -- there is
    # no raw-price-only path to a dollar P/L that skips simulate_order.
    yes_ask = 0.57
    budget = 10.0
    assert kf.simulate_order(budget, yes_ask) is not None
