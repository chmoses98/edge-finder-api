#!/usr/bin/env python3
"""
tests/edgelab/test_canonical_era_summary.py
===============================================
Coverage for lib.edgelab.reports.build_canonical_era_summary -- the
cumulative canonical-era performance view. Confirms pre-era wagers never
affect official totals by default, on-or-after-era wagers do, and
include_legacy=True gives an explicitly-labelled full-history view
without ever becoming the default.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import canonical_era
from lib.edgelab.reports import build_canonical_era_summary


def _bet(bet_id, game_date, *, stake=10.0, status="settled", result="WIN", net_pl=9.0,
         clv=1.5, market_family="ML", record_status="ACTIVE", tracking_type="REAL"):
    return {
        "betId": bet_id, "gameDate": game_date, "stake": stake, "status": status,
        "result": result, "netProfitLoss": net_pl, "clv": clv, "marketFamily": market_family,
        "recordStatus": record_status, "trackingType": tracking_type,
    }


def test_legacy_bets_excluded_from_official_summary_by_default():
    bets = [
        _bet("legacy-loss", "2026-06-18", stake=100.0, result="LOSS", net_pl=-100.0),
        _bet("era-win", "2026-08-03", stake=10.0, result="WIN", net_pl=9.0),
    ]
    summary = build_canonical_era_summary(bets)

    assert summary["legacyIncluded"] is False
    assert summary["canonicalEraStartDate"] == canonical_era.CANONICAL_ERA_START_DATE
    assert summary["betsPlaced"] == 1
    assert summary["totalRisked"] == 10.0
    assert summary["totalNetProfitLoss"] == 9.0
    assert summary["dailyRecord"]["wins"] == 1
    assert summary["dailyRecord"]["losses"] == 0


def test_on_or_after_era_wagers_do_affect_official_summary():
    bets = [
        _bet("era-1", "2026-08-03", stake=20.0, result="WIN", net_pl=18.0),
        _bet("era-2", "2026-08-04", stake=30.0, result="LOSS", net_pl=-30.0),
    ]
    summary = build_canonical_era_summary(bets)

    assert summary["betsPlaced"] == 2
    assert summary["totalRisked"] == 50.0
    assert summary["totalNetProfitLoss"] == -12.0
    assert summary["dailyRecord"]["wins"] == 1
    assert summary["dailyRecord"]["losses"] == 1


def test_include_legacy_true_produces_full_history_view_explicitly_labelled():
    bets = [
        _bet("legacy-1", "2026-06-18", stake=100.0, result="LOSS", net_pl=-100.0),
        _bet("era-1", "2026-08-03", stake=10.0, result="WIN", net_pl=9.0),
    ]
    era_only = build_canonical_era_summary(bets, include_legacy=False)
    full_history = build_canonical_era_summary(bets, include_legacy=True)

    assert era_only["betsPlaced"] == 1
    assert era_only["legacyIncluded"] is False

    assert full_history["betsPlaced"] == 2
    assert full_history["legacyIncluded"] is True
    assert full_history["totalRisked"] == 110.0


def test_performance_by_market_family_excludes_legacy_by_default():
    bets = [
        _bet("legacy-yrfi", "2026-06-18", market_family="YRFI", stake=8.0, result="LOSS", net_pl=-8.0),
        _bet("era-yrfi", "2026-08-03", market_family="YRFI", stake=8.0, result="WIN", net_pl=7.0),
    ]
    summary = build_canonical_era_summary(bets)
    assert summary["performanceByMarketFamily"]["YRFI"]["count"] == 1
    assert summary["performanceByMarketFamily"]["YRFI"]["netProfitLoss"] == 7.0


def test_roi_and_clv_computed_only_over_era_bets_by_default():
    bets = [
        _bet("legacy", "2026-06-18", stake=100.0, result="LOSS", net_pl=-100.0, clv=-5.0),
        _bet("era", "2026-08-03", stake=10.0, result="WIN", net_pl=9.0, clv=2.0),
    ]
    summary = build_canonical_era_summary(bets)
    assert summary["roiPct"] == 90.0  # 9 / 10 * 100, legacy bet's -100% ROI never mixed in
    assert summary["avgClvCents"] == 2.0


def test_cancelled_and_paper_excluded_same_as_build_postmortem():
    bets = [
        _bet("era-cancelled", "2026-08-03", stake=50.0, record_status="CANCELLED"),
        _bet("era-paper", "2026-08-03", stake=50.0, tracking_type="PAPER"),
        _bet("era-real", "2026-08-03", stake=10.0, result="WIN", net_pl=9.0),
    ]
    summary = build_canonical_era_summary(bets)
    assert summary["betsPlaced"] == 1
    assert summary["totalRisked"] == 10.0


def test_bankroll_summary_is_passthrough():
    fake_bankroll = {"settledBankroll": 359.0}
    summary = build_canonical_era_summary([], fake_bankroll)
    assert summary["bankroll"] == fake_bankroll


def test_never_mutates_input_bets():
    bets = [_bet("era-1", "2026-08-03")]
    snapshot = [dict(b) for b in bets]
    build_canonical_era_summary(bets)
    assert bets == snapshot
