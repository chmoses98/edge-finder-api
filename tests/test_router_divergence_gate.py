#!/usr/bin/env python3
"""
PROD-9: the router holds authenticated MLB wagers the canonical ledger does not.

REAL FAILURE SHAPE. From 2026-09-24 to 2026-09-28 twelve MLB wagers the
router had captured sat on its proposal branch (PR #247); main's newest
router-delivered wager was 2026-09-22 while the router's was 2026-09-27. Every
other assertion passed -- none of them can see what the account holds.
"""

import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
for path in (ROOT, os.path.join(ROOT, "scripts", "ci")):
    if path not in sys.path:
        sys.path.insert(0, path)

import production_health_gate as G  # noqa: E402
import router_divergence_probe as P  # noqa: E402

NOW = datetime(2026, 9, 29, 13, 0, 0, tzinfo=timezone.utc)

# Verbatim from kalshi-bet-router deliver run 36510046293 (2026-09-29), plus
# the coverage line that router version 2026-09-29 prints.
LOG = """
2026-09-29T02:13:47.5424095Z     DUPLICATE_NOOP: 78
ROUTER_COVERAGE sport=CFB eligible=75 newest_game_date=2026-09-27 newest_first_fill_utc=2026-09-27 refused_provably=0
ROUTER_COVERAGE sport=MLB eligible=78 newest_game_date=2026-09-27 newest_first_fill_utc=2026-09-27 refused_provably=0
ROUTER_COVERAGE sport=NFL eligible=56 newest_game_date=2026-09-28 newest_first_fill_utc=2026-09-29 refused_provably=9
2026-09-29T02:14:14.3687673Z   reconciliation by identity (MLB wagers, 78 eligible row(s)): on ledger 78, proposed not merged 0, refused 0 , UNACCOUNTED 0
2026-09-29T02:14:19.5157108Z   reconciliation by identity (NFL wagers, 56 eligible row(s)): on ledger 56, proposed not merged 0, refused 0 , UNACCOUNTED 0
2026-09-29T02:14:19.5235875Z destinations that failed: 0
"""


def _run(created_at, coverage=None, reconciliation=None, run_id=1):
    return {"runId": run_id, "createdAt": created_at, "url": "https://example.invalid/%d" % run_id,
            "coverage": coverage, "reconciliation": reconciliation}


HEALTHY_COVERAGE = {"eligible": 78, "newestGameDate": "2026-09-27",
                    "newestFirstFillUtc": "2026-09-27", "refusedProvably": 0}
HEALTHY_RECON = {"eligible": 78, "on_ledger": 78, "proposed_not_merged": 0,
                 "refused": 0, "unaccounted": 0}


def _evidence(latest_at="2026-09-29T12:40:00Z", settled=None):
    return {"settleHours": 3,
            "latest": _run(latest_at, HEALTHY_COVERAGE, HEALTHY_RECON, 2),
            "settled": settled if settled is not None
            else _run("2026-09-29T09:00:00Z", HEALTHY_COVERAGE, HEALTHY_RECON, 1)}


def _prod9(evidence, canonical="2026-09-27"):
    state = {"routerEvidence": evidence, "canonicalRouterNewestGameDate": canonical,
             "canonicalRouterRowCount": 78}
    return G._router_divergence(state, NOW)


def test_parser_reads_the_routers_real_lines_for_mlb_only():
    parsed = P.parse_delivery_log(LOG)
    assert parsed["coverage"] == HEALTHY_COVERAGE
    assert parsed["reconciliation"] == HEALTHY_RECON
    assert parsed["destinationsFailed"] == 0


def test_in_sync_router_passes():
    assert _prod9(_evidence())["status"] == G.PASS


def test_the_september_24_to_28_state_is_red():
    """The router's newest MLB game 09-27, main's newest router row 09-22, and
    the 12 rows proposed but not merged."""
    stuck = _run("2026-09-27T20:00:00Z",
                 dict(HEALTHY_COVERAGE, eligible=77),
                 {"eligible": 77, "on_ledger": 65, "proposed_not_merged": 12,
                  "refused": 0, "unaccounted": 0})
    result = _prod9(_evidence(latest_at="2026-09-28T00:00:00Z", settled=stuck),
                    canonical="2026-09-22")
    assert result["status"] == G.FAIL
    assert "newer than the newest canonical" in result["summary"]
    assert "12 router-eligible MLB wager(s) not on main" in result["summary"]


def test_a_silent_router_is_red_even_when_everything_it_last_said_was_fine():
    result = _prod9(_evidence(latest_at="2026-09-28T09:00:00Z"))
    assert result["status"] == G.FAIL
    assert "capture has stopped" in result["summary"]


def test_refused_orders_that_are_provably_mlb_are_red():
    settled = _run("2026-09-29T09:00:00Z", dict(HEALTHY_COVERAGE, refusedProvably=2),
                   HEALTHY_RECON)
    result = _prod9(_evidence(settled=settled))
    assert result["status"] == G.FAIL
    assert "every leg is MLB" in result["summary"]


def test_a_router_that_stops_reporting_coverage_is_red_not_green():
    result = _prod9(_evidence(settled=_run("2026-09-29T09:00:00Z", None, None)))
    assert result["status"] == G.FAIL


def test_an_unobservable_router_is_red_and_an_absent_probe_is_not_applicable():
    assert _prod9({"fetchError": "HTTPError 403"})["status"] == G.FAIL
    assert _prod9(None)["status"] == G.NOT_APPLICABLE


def test_prod9_is_critical_so_it_can_fail_the_gate():
    result = _prod9({"fetchError": "x"})
    assert result["severity"] == G.CRITICAL_PRODUCTION
