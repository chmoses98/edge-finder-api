#!/usr/bin/env python3
"""
tests/edgelab/test_migrate_clv_closing_coverage.py
==================================================
Coverage for scripts/edgelab/migrate_clv_closing_coverage.py.

The migration repairs the 100x price-unit defect on historical rows and
labels closing-quote coverage honestly. Its whole value rests on refusing
rather than guessing, so most of these tests assert a refusal.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_spec = importlib.util.spec_from_file_location(
    "migrate_clv_closing_coverage",
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab",
                 "migrate_clv_closing_coverage.py"),
)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

START = "2026-09-20T17:40:00Z"


def _quote(captured_at, **over):
    q = {"clvQuoteId": "q1", "marketTicker": "T", "capturedAt": captured_at,
         "scheduledStart": START, "priceUnit": "PROBABILITY", "checkpoint": "FIRST_DAILY",
         "yesBid": 0.34, "yesAsk": 0.37, "noBid": 0.63, "noAsk": 0.66,
         "provenance": {"sourceFile": None}}
    q.update(over)
    return q


def _bet(**over):
    b = {"betId": "b1", "gameDate": "2026-09-20", "marketTicker": "T", "side": "NO",
         "entryPrice": 0.72, "clvQuoteId": "q1", "closingPrice": 0.0066, "clv": -71.34}
    b.update(over)
    return b


def test_repairs_the_100x_scale_and_labels_a_stale_quote_pre_close():
    quotes = {"q1": _quote("2026-09-20T08:11:00Z")}
    action, detail = mig.plan_row(_bet(), quotes, {})
    assert action == "REPAIR"
    assert detail["after"]["closingPrice"] == 0.66      # was 0.0066
    assert detail["after"]["clv"] == -6.0               # was -71.34
    assert detail["after"]["closingCoverageClass"] == "PRE_CLOSE"
    assert detail["secondsBeforeStart"] == 34140.0


def test_near_close_quote_is_labelled_true_close():
    quotes = {"q1": _quote("2026-09-20T17:20:00Z")}
    action, detail = mig.plan_row(_bet(), quotes, {})
    assert action == "REPAIR"
    assert detail["after"]["closingCoverageClass"] == "TRUE_CLOSE"


def test_second_run_reports_unchanged_not_a_rewrite():
    """Idempotency: the migration must converge, not oscillate."""
    quotes = {"q1": _quote("2026-09-20T08:11:00Z")}
    _, first = mig.plan_row(_bet(), quotes, {})
    migrated = _bet(closingPrice=first["after"]["closingPrice"],
                    clv=first["after"]["clv"],
                    closingCoverageClass=first["after"]["closingCoverageClass"])
    action, _ = mig.plan_row(migrated, quotes, {})
    assert action == "UNCHANGED"


def test_refuses_when_the_archived_quote_is_gone():
    action, detail = mig.plan_row(_bet(), {}, {})
    assert action == "REFUSED"
    assert detail["reason"] == mig.REFUSAL_QUOTE_MISSING


def test_refuses_when_no_declared_price_unit_exists_anywhere():
    """An undeclared unit is never inferred from magnitude -- that IS the defect."""
    quotes = {"q1": _quote("2026-09-20T08:11:00Z", priceUnit=None)}
    action, detail = mig.plan_row(_bet(), quotes, {})
    assert action == "REFUSED"
    assert detail["reason"] == mig.REFUSAL_UNIT_UNDECLARED


def test_refuses_a_post_start_quote():
    quotes = {"q1": _quote("2026-09-20T18:00:00Z")}
    action, detail = mig.plan_row(_bet(), quotes, {})
    assert action == "REFUSED"
    assert detail["reason"] == mig.REFUSAL_POST_START


def test_refuses_when_scheduled_start_is_unresolvable():
    quotes = {"q1": _quote("2026-09-20T08:11:00Z", scheduledStart=None)}
    action, detail = mig.plan_row(_bet(), quotes, {})
    assert action == "REFUSED"
    assert detail["reason"] == mig.REFUSAL_NO_START


def test_refuses_when_the_wagered_side_has_no_executable_price():
    quotes = {"q1": _quote("2026-09-20T08:11:00Z", yesAsk=None, yesBid=None, noAsk=None, noBid=None)}
    action, detail = mig.plan_row(_bet(side="YES"), quotes, {})
    assert action == "REFUSED"
    assert detail["reason"] == mig.REFUSAL_NO_EXECUTABLE


def test_refuses_when_entry_price_is_missing():
    quotes = {"q1": _quote("2026-09-20T08:11:00Z")}
    action, detail = mig.plan_row(_bet(entryPrice=None), quotes, {})
    assert action == "REFUSED"
    assert detail["reason"] == mig.REFUSAL_NO_ENTRY_PRICE


def test_uses_the_recorded_quote_and_never_reselects():
    """No hindsight can enter through re-selection: the row's own clvQuoteId wins.

    A later, more favourable quote for the same ticker must NOT be picked up.
    """
    quotes = {
        "q1": _quote("2026-09-20T08:11:00Z"),
        "q2": _quote("2026-09-20T17:30:00Z", clvQuoteId="q2", noAsk=0.95),
    }
    _, detail = mig.plan_row(_bet(), quotes, {})
    assert detail["clvQuoteId"] == "q1"
    assert detail["after"]["closingPrice"] == 0.66     # from q1, not q2's 0.95


def test_yes_side_pays_the_ask_never_the_bid():
    quotes = {"q1": _quote("2026-09-20T17:20:00Z")}
    _, detail = mig.plan_row(_bet(side="YES", entryPrice=0.30), quotes, {})
    assert detail["after"]["closingPrice"] == 0.37     # yesAsk, not yesBid 0.34


def test_cents_denominated_archive_row_converts_correctly():
    quotes = {"q1": _quote("2026-09-20T17:20:00Z", priceUnit="CENTS",
                           yesBid=34, yesAsk=37, noBid=63, noAsk=66)}
    _, detail = mig.plan_row(_bet(), quotes, {})
    assert detail["declaredPriceUnit"] == "CENTS"
    assert detail["after"]["closingPrice"] == 0.66
