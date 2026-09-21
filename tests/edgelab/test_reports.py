#!/usr/bin/env python3
"""
tests/edgelab/test_reports.py
=================================
Coverage for lib/edgelab/reports.py: aggregation counts, markdown
rendering, and the calibration export.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.reports import (
    build_calibration_rows, build_daily_report, build_postmortem, render_markdown, render_postmortem_markdown,
    build_rolling_window_report, render_rolling_window_markdown,
)

DATE = "2026-07-31"


def _sample_inputs():
    games = [{"gameId": "g1"}, {"gameId": "g2"}]
    markets = [{"marketTicker": f"T{i}"} for i in range(5)]
    observations = [
        {"marketFamily": "game_result"}, {"marketFamily": "game_result"},
        {"marketFamily": "team_total"},
    ]
    recommendations = [
        {"status": "RECOMMENDED", "marketTicker": "T1", "modelFairProbability": 60, "marketImpliedProbability": 55, "marketFamily": "game_result"},
        {"status": "BET_PLACED", "marketTicker": "T2", "modelFairProbability": 70, "marketImpliedProbability": 60, "marketFamily": "game_result"},
        {"status": "PASS_NO_EDGE", "marketTicker": "T3", "modelFairProbability": None},
        {"status": "PASS_LOW_LIQUIDITY", "marketTicker": "T4", "modelFairProbability": None},
        {"status": "NOT_EVALUATED", "marketTicker": "T5", "modelFairProbability": None},
        {"status": "INSUFFICIENT_MODEL_SUPPORT", "marketTicker": "T6", "modelFairProbability": None},
    ]
    clv_quotes = [{"isClosingQuote": True}, {"isClosingQuote": True}, {"isClosingQuote": False}]
    settlements = [
        {"marketTicker": "T1", "settlementStatus": "SETTLED", "result": "YES", "unavailableReason": None},
        {"marketTicker": "T2", "settlementStatus": "SETTLED", "result": "NO", "unavailableReason": None},
        {"marketTicker": "T3", "settlementStatus": "VOID", "result": None, "unavailableReason": None},
        {"marketTicker": "T4", "settlementStatus": "SETTLEMENT_UNRESOLVED", "result": None, "unavailableReason": "missing_final_score"},
        {"marketTicker": "T5", "settlementStatus": "SETTLEMENT_UNRESOLVED", "result": None, "unavailableReason": "player_prop_settlement_not_implemented"},
    ]
    bets = [
        {"clv": 2.5, "betId": "b1"},
        {"clv": -1.0, "betId": "b2"},
        {"clv": None, "betId": "b3"},
    ]
    research_runs = [
        {"errors": ["fetch failed once"], "warnings": ["NEW_UNCLASSIFIED_MLB_SERIES: KXFOO (Some title)", "5 legacy bets skipped"]},
    ]
    return games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs


def test_build_daily_report_counts():
    report = build_daily_report(DATE, *_sample_inputs())
    assert report["gamesObserved"] == 2
    assert report["marketsObserved"] == 5
    assert report["quotesCaptured"] == 3
    assert report["marketFamilyCounts"] == {"game_result": 2, "team_total": 1}
    assert report["placedBets"] == 3
    assert report["recommendedBets"] == 2  # RECOMMENDED + BET_PLACED
    assert report["passCountsByReason"] == {"PASS_NO_EDGE": 1, "PASS_LOW_LIQUIDITY": 1}
    assert report["notEvaluatedCount"] == 1
    assert report["insufficientModelSupportCount"] == 1
    assert report["closingQuotesCaptured"] == 2


def test_games_observed_excludes_superseded_duplicate_identities():
    """
    A Game row marked supersededBy (lib.edgelab.market_universe.
    mark_superseded_game_identities) is a duplicate identity for a game
    already counted under its canonical row -- gamesObserved must count
    real games, not raw Game rows (the 2026-08-04 case: 15 real games
    produced 30 stored rows).
    """
    games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs = _sample_inputs()
    games = [
        {"gameId": "g1"},
        {"gameId": "g2"},
        {"gameId": "2026-08-04_NYM_CLE_1840", "supersededBy": {"canonicalGameId": "824403"}},
    ]
    report = build_daily_report(DATE, games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs)
    assert report["gamesObserved"] == 2


def test_clv_summary_ignores_bets_without_clv():
    report = build_daily_report(DATE, *_sample_inputs())
    clv = report["clvSummary"]
    assert clv["betsTotal"] == 3
    assert clv["betsWithClv"] == 2
    # These sample rows carry a clv value but NO closingCoverageClass, so they
    # predate coverage tracking and their distance from first pitch is unknown.
    # Unknown is not closing-line evidence, so the headline excludes them and
    # they surface as UNCLASSIFIED. The raw mean is still reported, but only as
    # an explicitly-named diagnostic. Previously this asserted 0.75 as
    # "Average CLV", which is exactly the unqualified claim that let a
    # 14-hour-old quote be reported as closing-line value.
    assert clv["avgClvCents"] is None
    assert clv["clvEligibleCount"] == 0
    assert clv["unclassifiedCount"] == 2
    assert clv["avgClvCentsAllCoverageIncludingStale"] == 0.75  # (2.5 + -1.0) / 2
    assert clv["positiveClvCount"] == 0
    assert clv["negativeClvCount"] == 0


def test_settlement_completion_and_unresolved_reasons():
    report = build_daily_report(DATE, *_sample_inputs())
    sc = report["settlementCompletion"]
    assert sc["settled"] == 2
    assert sc["void"] == 1
    assert sc["unresolved"] == 2
    assert sc["notYetAttempted"] == 0  # 5 markets, 5 settlement rows
    assert report["settlementUnresolvedReasons"]["player_prop_settlement_not_implemented"] == 1


def test_warnings_split_new_series_from_general_data_quality():
    report = build_daily_report(DATE, *_sample_inputs())
    assert report["apiErrors"] == ["fetch failed once"]
    assert len(report["newUnclassifiedSeriesWarnings"]) == 1
    assert "KXFOO" in report["newUnclassifiedSeriesWarnings"][0]
    assert report["dataQualityWarnings"] == ["5 legacy bets skipped"]


def test_render_markdown_is_nonempty_and_includes_key_sections():
    report = build_daily_report(DATE, *_sample_inputs())
    md = render_markdown(report)
    assert "EdgeLab Daily Research Report" in md
    assert "Market family counts" in md
    assert "CLV summary" in md
    assert "Settlement completion" in md
    assert "game_result: 2" in md


def test_calibration_rows_require_both_model_prob_and_settled_result():
    games, markets, observations, recommendations, clv_quotes, settlements, bets, research_runs = _sample_inputs()
    rows = build_calibration_rows(recommendations, settlements)
    tickers = {r["marketTicker"] for r in rows}
    assert tickers == {"T1", "T2"}  # T3-T6 lack either a model prob or a settled result
    by_ticker = {r["marketTicker"]: r for r in rows}
    assert by_ticker["T1"]["won"] is True
    assert by_ticker["T2"]["won"] is False


# ---------------------------------------------------------------------------
# build_postmortem / render_postmortem_markdown (Canonical Placed-Bet
# Ledger milestone, requirement 14). Built EXCLUSIVELY from PlacedBet rows
# -- never from a recommendation list -- so a recommendation never counts
# as a placed bet here.
# ---------------------------------------------------------------------------

def _postmortem_bets():
    return [
        {  # settled WIN, model-supported, recommended
            "betId": "b1", "gameDate": "2026-08-01", "marketTicker": "T1", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "PIT ML", "side": "YES", "stake": 10.0, "entryPrice": 0.5, "status": "settled",
            "result": "WIN", "netProfitLoss": 10.0, "clv": 2.0, "source": "MODEL", "entryMethod": "PRODUCTION_RECOMMENDATION_CONFIRMED",
            "modelSupported": True, "modelEvaluationId": "me1", "recommendationId": "rec1", "snapshotId": "snap1",
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # settled LOSS, manual, not recommended
            "betId": "b2", "gameDate": "2026-08-01", "marketTicker": "T2", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "CIN ML", "side": "YES", "stake": 5.0, "entryPrice": 0.4, "status": "settled",
            "result": "LOSS", "netProfitLoss": -5.0, "clv": -1.0, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # still pending, manual
            "betId": "b3", "gameDate": "2026-08-01", "marketTicker": "T3", "marketFamily": "FAMILY_INNING_RESULT",
            "selection": "DET F5", "side": "NO", "stake": 3.0, "entryPrice": 0.6, "status": "pending",
            "result": None, "netProfitLoss": None, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_GITHUB_FORM",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # void
            "betId": "b4", "gameDate": "2026-08-01", "marketTicker": "T4", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "SEA ML", "side": "YES", "stake": 2.0, "entryPrice": 0.5, "status": "void",
            "result": "VOID", "netProfitLoss": 0.0, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # different date -- must be excluded
            "betId": "b5", "gameDate": "2026-07-31", "marketTicker": "T5", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "NYY ML", "side": "YES", "stake": 100.0, "entryPrice": 0.5, "status": "settled",
            "result": "WIN", "netProfitLoss": 100.0, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        },
        {  # cancelled -- must be excluded
            "betId": "b6", "gameDate": "2026-08-01", "marketTicker": "T6", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "BOS ML", "side": "YES", "stake": 1000.0, "entryPrice": 0.5, "status": "pending",
            "result": None, "netProfitLoss": None, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "REAL", "recordStatus": "CANCELLED",
        },
        {  # paper -- must never pollute real totals
            "betId": "b7", "gameDate": "2026-08-01", "marketTicker": "T7", "marketFamily": "FAMILY_GAME_RESULT",
            "selection": "TEX ML", "side": "YES", "stake": 500.0, "entryPrice": 0.5, "status": "settled",
            "result": "WIN", "netProfitLoss": 500.0, "clv": None, "source": "MANUAL", "entryMethod": "MANUAL_CHAT_CONFIRMED",
            "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
            "replayRunId": None, "trackingType": "PAPER", "recordStatus": "ACTIVE",
        },
    ]


def test_postmortem_filters_to_date_excludes_cancelled_and_paper():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    assert report["betsPlaced"] == 4  # b1-b4 only
    assert {b["betId"] for b in report["bets"]} == {"b1", "b2", "b3", "b4"}


def test_postmortem_daily_record_and_totals():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    assert report["dailyRecord"] == {"wins": 1, "losses": 1, "pushes": 0, "voids": 1, "pending": 1}
    assert report["totalRisked"] == 20.0  # 10 + 5 + 3 + 2
    assert report["totalRiskedSettled"] == 15.0  # 10 + 5 -- b3 pending, b4 void (not "settled")
    assert report["totalNetProfitLoss"] == 5.0  # 10 - 5 + 0
    assert report["roiPct"] == round(5.0 / 15.0 * 100, 2)
    assert report["unresolvedCount"] == 1
    assert report["unresolvedBetIds"] == ["b3"]


def test_postmortem_never_substitutes_recommendations_for_placed_bets():
    """Only PlacedBet rows count -- a recommendation list, even if passed accidentally, has no signature build_postmortem accepts."""
    import inspect
    sig = inspect.signature(build_postmortem)
    assert "recommendations" not in sig.parameters


def test_postmortem_gross_return_and_clv():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    by_id = {b["betId"]: b for b in report["bets"]}
    assert by_id["b1"]["grossReturn"] == 20.0  # stake 10 + netProfitLoss 10
    assert by_id["b2"]["grossReturn"] == 0.0  # a loss returns nothing
    assert by_id["b3"]["grossReturn"] is None  # still pending
    # These fixtures carry a clv value but no closingCoverageClass, so their
    # distance from first pitch is unknown and they are NOT closing-line
    # evidence. The headline excludes them; the raw mean survives only as an
    # explicitly-named diagnostic. Previously this asserted 0.5 as
    # "avgClvCents" -- the same unqualified claim that let a 14-hour-old
    # quote be reported as closing-line value.
    assert report["avgClvCents"] is None
    assert report["clvCoverage"]["clvEligibleCount"] == 0
    assert report["clvCoverage"]["unclassifiedCount"] == 2
    assert report["clvCoverage"]["avgClvCentsAllCoverageIncludingStale"] == 0.5


def test_postmortem_model_supported_vs_manual():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    ms = report["modelSupportedVsManual"]
    assert ms["modelSupported"]["count"] == 1
    assert ms["manual"]["count"] == 3


def test_postmortem_recommended_vs_non_recommended():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    rv = report["recommendedVsNonRecommended"]
    assert rv["recommended"]["count"] == 1
    assert rv["nonRecommended"]["count"] == 3


def test_postmortem_snapshot_and_replay_linkage_counts():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    assert report["snapshotLinkedCount"] == 1
    assert report["replayLinkedCount"] == 0


def test_postmortem_performance_by_market_family():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    fam = report["performanceByMarketFamily"]["FAMILY_GAME_RESULT"]
    assert fam["count"] == 2  # b1, b2 settled in this family (b4 void isn't "settled")
    assert fam["wins"] == 1
    assert fam["losses"] == 1


def test_postmortem_with_no_bets_for_date_is_all_zero_not_an_error():
    report = build_postmortem("2099-01-01", _postmortem_bets())
    assert report["betsPlaced"] == 0
    assert report["totalRisked"] == 0.0
    assert report["roiPct"] is None


def test_postmortem_bankroll_is_passthrough_when_provided():
    bankroll = {"availableBankroll": 1.0, "settledBankroll": 2.0, "totalExposure": 1.0, "userReportedBalance": None}
    report = build_postmortem("2026-08-01", _postmortem_bets(), bankroll_summary=bankroll)
    assert report["bankroll"] == bankroll


def test_render_postmortem_markdown_includes_key_sections():
    report = build_postmortem("2026-08-01", _postmortem_bets())
    md = render_postmortem_markdown(report)
    assert "Daily Postmortem" in md
    assert "Performance by market family" in md
    assert "Model-supported vs. manual" in md
    assert "Recommended vs. non-recommended" in md
    assert "Unresolved bets" in md


# ---------------------------------------------------------------------------
# realizedEconomics -- Aug 11 2026 game-identity repair mission, scenario 7:
# "report rendering when canonical settlement exists / only manual receipt
# economics exist / neither exists". A settlement-gated postmortem must
# never misleadingly imply "no outcomes are known" just because canonical
# settlement (status=="settled") hasn't populated yet, as long as
# lib.edgelab.bets.confirm_realized_return already established the real
# economics -- but a manually confirmed result must never be mislabeled as
# automatic/objective settlement either.
# ---------------------------------------------------------------------------

def _bet(bet_id, *, status="pending", result=None, net_pl=None,
         confirmed_net_pl=None, confirmed_return=None, stake=10.0,
         market_family="game_result", game_date="2026-08-11"):
    return {
        "betId": bet_id, "gameDate": game_date, "marketTicker": f"T{bet_id}", "marketFamily": market_family,
        "selection": "sel", "side": "YES", "stake": stake, "entryPrice": 0.5, "status": status,
        "result": result, "netProfitLoss": net_pl, "clv": None, "source": "MANUAL", "entryMethod": "IMPORTED_RECEIPT",
        "modelSupported": None, "modelEvaluationId": None, "recommendationId": None, "snapshotId": None,
        "replayRunId": None, "trackingType": "REAL", "recordStatus": "ACTIVE",
        "confirmedReceiptNetProfitLoss": confirmed_net_pl, "confirmedReceiptReturn": confirmed_return,
    }


def test_report_state_only_canonical_settlement_no_receipts():
    """State 1: canonical settlement exists, no confirmed receipts anywhere -- realizedEconomics equals dailyRecord exactly, no receipt-only bets."""
    bets = [_bet("b1", status="settled", result="WIN", net_pl=10.0)]
    report = build_postmortem("2026-08-11", bets)
    assert report["dailyRecord"]["wins"] == 1
    assert report["realizedEconomics"]["wins"] == 1
    assert report["realizedEconomics"]["settledCanonicallyCount"] == 1
    assert report["realizedEconomics"]["confirmedReceiptOnlyCount"] == 0
    row = report["bets"][0]
    assert row["economicsSource"] == "SETTLEMENT"
    assert row["knownResult"] == "WIN"
    md = render_postmortem_markdown(report)
    assert "## Realized economics" not in md  # nothing new to say beyond the canonical Record line


def test_report_state_only_confirmed_receipts_no_canonical_settlement():
    """
    State 2 (the actual 2026-08-11 shape): every bet has ONLY a confirmed
    manual receipt -- canonical settlement hasn't run for any of them.
    dailyRecord (strictly canonical) stays all-zero/pending, exactly as
    before this feature existed -- but realizedEconomics surfaces the
    true 6-8-style record, and the markdown renders the task's own
    example phrasing.
    """
    bets = [
        _bet("b1", status="pending", confirmed_net_pl=21.07, confirmed_return=45.66, stake=24.59),
        _bet("b2", status="pending", confirmed_net_pl=-19.64, confirmed_return=0.0, stake=19.64),
    ]
    report = build_postmortem("2026-08-11", bets)
    # Canonical/objective view: untouched, still shows nothing known.
    assert report["dailyRecord"] == {"wins": 0, "losses": 0, "pushes": 0, "voids": 0, "pending": 2}
    assert report["totalRiskedSettled"] == 0
    assert report["totalNetProfitLoss"] == 0
    # Realized-economics view: the real outcome.
    re = report["realizedEconomics"]
    assert re["wins"] == 1
    assert re["losses"] == 1
    assert re["settledCanonicallyCount"] == 0
    assert re["confirmedReceiptOnlyCount"] == 2
    # MONEY IS CANONICAL ONLY. These bets have no canonical settlement, so
    # they contribute no dollars -- the receipt's 21.07/-19.64 are evidence,
    # not accounting (canonical netProfitLoss is the single authority; see
    # lib.edgelab.bets.realized_bet_economics). The shortfall is counted
    # rather than hidden, and the outcome view still reports the real record.
    assert re["netProfitLoss"] == 0
    assert re["netProfitLossUnavailableCount"] == 2
    # Per-bet rows: the receipt stays fully visible beside the canonical figure.
    by_id = {b["betId"]: b for b in report["bets"]}
    assert by_id["b1"]["economicsSource"] is None      # no canonical dollars to label
    assert by_id["b1"]["knownResult"] == "WIN"
    assert by_id["b1"]["netProfitLoss"] is None        # never the receipt's 21.07
    assert by_id["b1"]["confirmedReceiptNetProfitLoss"] == 21.07
    assert by_id["b1"]["confirmedReceiptReturn"] == 45.66
    assert by_id["b1"]["status"] == "pending"  # raw ledger status/result never overwritten
    assert by_id["b1"]["result"] is None
    # unresolvedCount stays canonical-strict (still pending), but the
    # genuinely-unknown subset is called out separately.
    assert report["unresolvedCount"] == 2
    assert report["pendingWithoutKnownEconomicsCount"] == 0

    md = render_postmortem_markdown(report)
    assert "## Realized economics" in md
    assert "Canonical settlement pending; confirmed realized economics: 1-1" in md
    assert "0 canonically settled, 2 known only from a manually confirmed receipt" in md
    # The canonical top-line Record must still say what it always said -- never silently replaced.
    assert "- Record: 0-0-0 (pushes), 0 void, 2 still pending" in md


def test_report_state_neither_settlement_nor_receipt():
    """State 3: a genuinely unresolved bet (no settlement, no receipt) contributes to neither dailyRecord nor realizedEconomics, and is correctly flagged as truly unknown."""
    bets = [_bet("b1", status="pending")]
    report = build_postmortem("2026-08-11", bets)
    assert report["dailyRecord"]["pending"] == 1
    assert report["realizedEconomics"]["count"] == 0
    assert report["pendingWithoutKnownEconomicsCount"] == 1
    row = report["bets"][0]
    assert row["economicsSource"] is None
    assert row["knownResult"] is None
    assert row["grossReturn"] is None
    md = render_postmortem_markdown(report)
    assert "## Realized economics" not in md


def test_report_state_mixed_settlement_and_receipt_only():
    """A day with SOME canonically settled bets and SOME receipt-only bets: both counts appear distinctly, canonical Record is untouched."""
    bets = [
        _bet("b1", status="settled", result="WIN", net_pl=10.0),
        _bet("b2", status="pending", confirmed_net_pl=-5.0, confirmed_return=0.0),
    ]
    report = build_postmortem("2026-08-11", bets)
    assert report["dailyRecord"] == {"wins": 1, "losses": 0, "pushes": 0, "voids": 0, "pending": 1}
    re = report["realizedEconomics"]
    assert re["wins"] == 1
    assert re["losses"] == 1
    assert re["settledCanonicallyCount"] == 1
    assert re["confirmedReceiptOnlyCount"] == 1
    md = render_postmortem_markdown(report)
    assert "## Realized economics" in md
    assert "Realized economics (canonical settlement + confirmed receipts): 1-1" in md


def test_report_economics_source_describes_where_the_shown_dollars_came_from():
    """economicsSource is the one place a reader learns provenance. Since the
    shown dollars are now ALWAYS canonical settlement's, it may only ever say
    SETTLEMENT -- and must say nothing at all when there are no canonical
    dollars to describe. Claiming CONFIRMED_RECEIPT for a canonical figure, or
    SETTLEMENT for a bet settlement never graded, are both the mislabeling
    this field exists to prevent."""
    bets = [
        _bet("settled_only", status="settled", result="LOSS", net_pl=-10.0),
        _bet("receipt_only", status="pending", confirmed_net_pl=3.0, confirmed_return=8.0),
    ]
    report = build_postmortem("2026-08-11", bets)
    by_id = {b["betId"]: b for b in report["bets"]}
    assert by_id["settled_only"]["economicsSource"] == "SETTLEMENT"
    assert by_id["settled_only"]["netProfitLoss"] == -10.0
    assert by_id["receipt_only"]["economicsSource"] is None
    assert by_id["receipt_only"]["netProfitLoss"] is None
    # ...and the receipt is still readable on the row.
    assert by_id["receipt_only"]["confirmedReceiptNetProfitLoss"] == 3.0


# ══════════════════════════════════════════════════════════════════════════════
# Tier/confidence calibration & canonical rolling performance reporting
# ══════════════════════════════════════════════════════════════════════════════

def _rolling_bet(bet_id, *, game_date="2026-08-05", status="settled", result=None,
                  stake=10.0, net_pl=None, confirmed_net_pl=None, confirmed_return=None,
                  confidence=None, market_family="game_result",
                  model_fair_prob=None, manual_fair_prob=None, clv=None,
                  tracking_type="REAL", record_status="ACTIVE", updated_at=None):
    return {
        "betId": bet_id, "gameDate": game_date, "status": status, "result": result,
        "stake": stake, "netProfitLoss": net_pl,
        "confirmedReceiptNetProfitLoss": confirmed_net_pl, "confirmedReceiptReturn": confirmed_return,
        "confidence": confidence, "marketFamily": market_family,
        "modelFairProbability": model_fair_prob, "manualFairProbability": manual_fair_prob,
        "clv": clv, "trackingType": tracking_type, "recordStatus": record_status,
        "updatedAt": updated_at or f"{game_date}T12:00:00Z", "entryTimestamp": f"{game_date}T10:00:00Z",
    }


def test_rolling_window_uses_settled_canonical_wagers_only():
    """Pending, cancelled, paper-tracked, and pre-canonical-era rows are
    all excluded -- only a settled, active, real, canonical-era bet
    counts."""
    bets = [
        _rolling_bet("pending1", status="pending", result=None, net_pl=None),
        _rolling_bet("cancelled1", record_status="CANCELLED", result="WIN", net_pl=10.0),
        _rolling_bet("paper1", tracking_type="PAPER", result="WIN", net_pl=10.0),
        _rolling_bet("legacy1", game_date="2026-08-01", result="WIN", net_pl=10.0,
                      updated_at="2026-08-01T12:00:00Z"),
        _rolling_bet("canon1", result="WIN", net_pl=5.0, updated_at="2026-08-05T12:00:00Z"),
    ]
    report = build_rolling_window_report(bets, window_size=30)
    assert report["windowActual"] == 1
    assert report["overall"]["count"] == 1
    assert report["overall"]["netProfitLoss"] == 5.0
    assert report["newestBetIdInWindow"] == "canon1"


def test_rolling_window_caps_at_window_size_most_recent_first():
    bets = [
        _rolling_bet(f"b{i}", result="WIN", net_pl=1.0, updated_at=f"2026-08-05T00:00:{i:02d}Z")
        for i in range(35)
    ]
    report = build_rolling_window_report(bets, window_size=30)
    assert report["windowActual"] == 30
    assert report["windowRequested"] == 30
    assert report["newestBetIdInWindow"] == "b34"
    assert report["oldestBetIdInWindow"] == "b5"
    assert report["overall"]["count"] == 30


def test_rolling_window_smaller_than_requested_reports_true_size_not_padded():
    bets = [_rolling_bet("only1", result="WIN", net_pl=1.0)]
    report = build_rolling_window_report(bets, window_size=30)
    assert report["windowRequested"] == 30
    assert report["windowActual"] == 1
    assert report["windowSampleStatus"] == "INSUFFICIENT_SAMPLE"


def test_rolling_window_pl_is_canonical_not_the_confirmed_receipt():
    """The rolling window is an accounting surface, so it reports canonical
    netProfitLoss. A confirmed receipt that disagrees is evidence, surfaced
    separately (lib.edgelab.bets.confirmed_receipt_economics /
    realized_economics_disagreement), never substituted for the ledger --
    otherwise this window and the bankroll would describe the same wagers
    differently, which is exactly the inconsistency this replaced."""
    bets = [
        _rolling_bet("b1", result="WIN", net_pl=100.0, stake=10.0,
                      confirmed_net_pl=42.0, confirmed_return=52.0),
    ]
    report = build_rolling_window_report(bets)
    assert report["overall"]["netProfitLoss"] == 100.0
    assert report["overall"]["realizedReturn"] == 110.0   # stake + canonical net


def test_missing_fair_probability_and_clv_stay_unavailable():
    bets = [
        _rolling_bet("b1", result="WIN", net_pl=5.0),
        _rolling_bet("b2", result="LOSS", net_pl=-5.0, model_fair_prob=0.6, clv=2.0,
                      updated_at="2026-08-05T00:00:02Z"),
    ]
    report = build_rolling_window_report(bets)
    assert report["calibration"]["n"] == 1
    assert report["calibration"]["sampleStatus"] == "INSUFFICIENT_SAMPLE"
    assert report["clvCoverage"]["withClv"] == 1
    assert report["clvCoverage"]["withoutClv"] == 1
    assert report["clvCoverage"]["coveragePct"] == 50.0


def test_fully_missing_fair_probability_reports_none_not_fabricated():
    bets = [_rolling_bet("b1", result="WIN", net_pl=5.0)]
    report = build_rolling_window_report(bets)
    c = report["calibration"]
    assert c["n"] == 0
    assert c["avgPredictedProbability"] is None
    assert c["actualWinRate"] is None
    assert c["calibrationError"] is None
    assert report["clvCoverage"]["withClv"] == 0
    assert report["clvCoverage"]["avgClvCents"] is None


def test_manual_fair_probability_used_when_no_model_backing():
    bets = [_rolling_bet("b1", result="WIN", net_pl=5.0, manual_fair_prob=0.58)]
    report = build_rolling_window_report(bets)
    rows = report["calibration"]["rows"]
    assert len(rows) == 1
    assert rows[0]["probabilitySource"] == "MANUAL"
    assert rows[0]["predictedProbability"] == 0.58


def test_model_fair_probability_preferred_over_manual_when_both_present():
    bets = [_rolling_bet("b1", result="WIN", net_pl=5.0, model_fair_prob=0.62, manual_fair_prob=0.40)]
    report = build_rolling_window_report(bets)
    rows = report["calibration"]["rows"]
    assert rows[0]["probabilitySource"] == "MODEL"
    assert rows[0]["predictedProbability"] == 0.62


def test_tier_breakdown_groups_by_confidence_and_labels_unrecorded():
    bets = [
        _rolling_bet("b1", confidence="HIGH", result="WIN", net_pl=5.0),
        _rolling_bet("b2", confidence=None, result="LOSS", net_pl=-5.0, updated_at="2026-08-05T00:00:02Z"),
    ]
    report = build_rolling_window_report(bets)
    assert set(report["tierBreakdown"].keys()) == {"HIGH", "UNRECORDED"}
    assert report["tierBreakdown"]["HIGH"]["count"] == 1
    assert report["tierBreakdown"]["UNRECORDED"]["count"] == 1


def test_tier_breakdown_never_invents_a_tier_for_a_null_confidence():
    """Requirement: if a tier isn't stored on a canonical wager, never
    invent one -- 'UNRECORDED' is an explicit, separate bucket, never
    silently folded into PAPER or any other real tier."""
    bets = [_rolling_bet("b1", confidence=None, result="WIN", net_pl=5.0)]
    report = build_rolling_window_report(bets)
    assert "PAPER" not in report["tierBreakdown"]
    assert report["tierBreakdown"]["UNRECORDED"]["count"] == 1


def test_market_family_breakdown_groups_settled_bets():
    bets = [
        _rolling_bet("b1", market_family="game_result", result="WIN", net_pl=5.0),
        _rolling_bet("b2", market_family="team_total", result="LOSS", net_pl=-5.0,
                      updated_at="2026-08-05T00:00:02Z"),
    ]
    report = build_rolling_window_report(bets)
    assert set(report["marketFamilyBreakdown"].keys()) == {"game_result", "team_total"}


def test_render_rolling_window_markdown_includes_key_sections():
    bets = [_rolling_bet("b1", confidence="HIGH", result="WIN", net_pl=5.0)]
    report = build_rolling_window_report(bets)
    md = render_rolling_window_markdown(report)
    assert "Rolling Last-30" in md
    assert "Tier breakdown" in md
    assert "Market family breakdown" in md
    assert "Calibration" in md
    assert "CLV coverage" in md


# ---------------------------------------------------------------------------
# entryProvenance: HOW each bet reached the ledger.
#
# Added when the Kalshi bet router began importing rows built from the
# exchange's own fills (entryMethod IMPORTED_RECEIPT). Those rows carry no
# recommendationId and no modelFairProbability -- a Kalshi execution proves the
# owner placed the bet, not that anything recommended it -- so they fall into
# the existing "manual" bucket, which means NOT MODEL-BACKED and is not a claim
# about how the row was entered. These tests pin that the two questions stay
# separate and that neither is derived from the other.
# ---------------------------------------------------------------------------


def _provenance_bets():
    return [
        {"betId": "b1", "gameDate": DATE, "stake": 5.0, "entryMethod": "IMPORTED_RECEIPT",
         "source": "OTHER", "trackingType": "REAL"},
        {"betId": "b2", "gameDate": DATE, "stake": 3.0, "entryMethod": "IMPORTED_RECEIPT",
         "source": "OTHER", "trackingType": "REAL"},
        {"betId": "b3", "gameDate": DATE, "stake": 2.0, "entryMethod": "MANUAL_GITHUB_FORM",
         "source": "MODEL", "modelSupported": True, "trackingType": "REAL"},
        {"betId": "b4", "gameDate": DATE, "stake": 1.0, "trackingType": "REAL"},
    ]


def _report_with(bets):
    """The POSTMORTEM, not the daily report: modelSupportedVsManual and
    entryProvenance both live there, because both are about placed bets."""
    return build_postmortem(DATE, bets)


def test_entry_provenance_groups_by_entry_method():
    report = _report_with(_provenance_bets())

    provenance = report["entryProvenance"]
    assert provenance["IMPORTED_RECEIPT"]["count"] == 2
    assert provenance["IMPORTED_RECEIPT"]["stake"] == 8.0
    assert provenance["MANUAL_GITHUB_FORM"]["count"] == 1


def test_a_bet_with_no_entry_method_is_counted_not_dropped():
    """Dropping it would make the buckets stop summing to the total; folding it
    into a real method would assert something that is not known."""
    report = _report_with(_provenance_bets())

    assert report["entryProvenance"]["UNRECORDED"]["count"] == 1


def test_entry_provenance_buckets_sum_to_the_real_bet_total():
    bets = _provenance_bets()
    report = _report_with(bets)

    total = sum(stats["count"] for stats in report["entryProvenance"].values())
    assert total == len(bets)


def test_an_imported_receipt_is_not_counted_as_model_supported():
    """A Kalshi execution proves the bet was placed, not that a model backed it."""
    report = _report_with(_provenance_bets())

    assert report["modelSupportedVsManual"]["modelSupported"]["count"] == 1
    assert report["modelSupportedVsManual"]["manual"]["count"] == 3
    assert report["recommendedVsNonRecommended"]["recommended"]["count"] == 0


def test_the_two_breakdowns_answer_different_questions():
    """An IMPORTED_RECEIPT row is in 'manual' AND in its own provenance bucket.

    If a future change made one derive from the other, this would break.
    """
    report = _report_with(_provenance_bets())

    assert report["entryProvenance"]["IMPORTED_RECEIPT"]["count"] == 2
    assert report["modelSupportedVsManual"]["manual"]["count"] == 3
    assert (
        report["modelSupportedVsManual"]["manual"]["count"]
        != report["entryProvenance"]["IMPORTED_RECEIPT"]["count"]
    )


def test_markdown_renders_the_provenance_section():
    report = _report_with(_provenance_bets())
    markdown = render_postmortem_markdown(report)

    assert "## How each bet reached the ledger" in markdown
    assert "IMPORTED_RECEIPT: 2 bets" in markdown
    # And it says what it is NOT, so the two sections are not confused.
    assert "not whether a model backed it" in markdown


def test_markdown_still_renders_a_postmortem_built_before_this_field_existed():
    """A stored postmortem or a replayed run has no entryProvenance key."""
    report = _report_with(_provenance_bets())
    del report["entryProvenance"]

    markdown = render_postmortem_markdown(report)

    assert "## How each bet reached the ledger" not in markdown
    assert "## Model-supported vs. manual" in markdown


# ---------------------------------------------------------------------------
# ONE ACCOUNTING AUTHORITY FOR REALIZED P/L.
#
# The repository used to ship two answers at once: reports and postmortems
# called realized_bet_economics, which PREFERRED confirmedReceiptNetProfitLoss,
# while lib.edgelab.bankroll read canonical netProfitLoss directly. The same
# wager therefore reported different realized P/L depending on which surface a
# reader looked at, and nothing announced the disagreement. A 2026-09-17 audit
# measured it across the 304 settled rows carrying a receipt: +85.76 signed,
# 173.40 absolute, 237 rows differing by more than a cent.
#
# Canonical netProfitLoss is now the single authority. The receipt is still
# fully visible -- beside the canonical figure, with the difference stated --
# it simply no longer replaces it.
# ---------------------------------------------------------------------------

CANONICAL_NET_PL = 10.0      # X -- what the ledger says
RECEIPT_NET_PL = 25.0        # Y -- what the human's receipt says
RECEIPT_GROSS = 35.0


def _disagreeing_bet(bet_id="dis1"):
    return _bet(bet_id, status="settled", result="WIN", net_pl=CANONICAL_NET_PL,
                confirmed_net_pl=RECEIPT_NET_PL, confirmed_return=RECEIPT_GROSS, stake=10.0)


def test_postmortem_headline_reports_canonical_not_receipt():
    report = build_postmortem("2026-08-11", [_disagreeing_bet()])
    assert report["totalNetProfitLoss"] == CANONICAL_NET_PL
    assert report["realizedEconomics"]["netProfitLoss"] == CANONICAL_NET_PL
    assert report["performanceByMarketFamily"]["game_result"]["netProfitLoss"] == CANONICAL_NET_PL
    assert report["totalNetProfitLoss"] != RECEIPT_NET_PL


def test_postmortem_roi_uses_canonical():
    report = build_postmortem("2026-08-11", [_disagreeing_bet()])
    # 10.00 on a 10.00 stake settled == 100%, not the receipt's 250%
    assert report["roiPct"] == 100.0


def test_bankroll_reports_canonical_and_agrees_with_the_postmortem():
    from lib.edgelab.bankroll import compute_bankroll_summary
    bet = _disagreeing_bet()
    txns = [{"type": "STARTING_BALANCE", "amount": 100.0}]
    summary = compute_bankroll_summary(txns, [bet])
    report = build_postmortem("2026-08-11", [bet])
    assert summary["settledBankroll"] == round(100.0 + CANONICAL_NET_PL, 2)
    # THE WHOLE POINT: the two surfaces now describe the same wager identically.
    assert summary["settledBankroll"] - 100.0 == report["totalNetProfitLoss"]


def test_daily_report_aggregate_uses_canonical():
    from lib.edgelab import bets as bets_lib
    gross, net = bets_lib.realized_bet_economics(_disagreeing_bet())
    assert net == CANONICAL_NET_PL
    assert gross == 20.0          # stake + canonical net, never the receipt's 35.00


def test_receipt_evidence_is_still_visible_and_the_disagreement_is_stated():
    """Suppressing the receipt would be its own failure -- it must remain
    readable beside the canonical figure, with the gap spelled out."""
    report = build_postmortem("2026-08-11", [_disagreeing_bet()])
    row = report["bets"][0]
    assert row["netProfitLoss"] == CANONICAL_NET_PL
    assert row["confirmedReceipt"] is True
    assert row["confirmedReceiptNetProfitLoss"] == RECEIPT_NET_PL
    assert row["confirmedReceiptReturn"] == RECEIPT_GROSS
    disagreement = row["realizedEconomicsDisagreement"]
    assert disagreement["agrees"] is False
    assert disagreement["canonicalNetProfitLoss"] == CANONICAL_NET_PL
    assert disagreement["confirmedReceiptNetProfitLoss"] == RECEIPT_NET_PL
    assert disagreement["difference"] == round(RECEIPT_NET_PL - CANONICAL_NET_PL, 4)


def test_economics_source_never_claims_receipt_provenance_for_canonical_money():
    """The label must describe where the shown dollars actually came from."""
    report = build_postmortem("2026-08-11", [_disagreeing_bet()])
    assert report["bets"][0]["economicsSource"] == "SETTLEMENT"


def test_receipt_only_bet_contributes_no_canonical_money_and_says_so():
    """A bet known only through a receipt has NO canonical P/L. It must not
    quietly contribute the receipt's dollars, and the gap must be counted
    rather than hidden."""
    bets = [_bet("r1", status="pending", confirmed_net_pl=21.07, confirmed_return=45.66, stake=24.59)]
    report = build_postmortem("2026-08-11", bets)
    realized = report["realizedEconomics"]
    assert realized["netProfitLoss"] == 0
    assert realized["confirmedReceiptOnlyCount"] == 1
    assert realized["netProfitLossUnavailableCount"] == 1
    # ...and the receipt itself is still on the row.
    assert report["bets"][0]["confirmedReceiptNetProfitLoss"] == 21.07


def test_confirmed_receipt_economics_still_returns_the_receipt_pair():
    from lib.edgelab import bets as bets_lib
    assert bets_lib.confirmed_receipt_economics(_disagreeing_bet()) == (RECEIPT_GROSS, RECEIPT_NET_PL)
    assert bets_lib.confirmed_receipt_economics(_bet("none", status="settled", result="WIN", net_pl=1.0)) == (None, None)


# ---------------------------------------------------------------------------
# Regression: the 2026-09-17 "8 placed bets vs 5 REAL wagers" discrepancy.
#
# The canonical ledger held 8 rows for that date -- 5 real-money Kalshi
# receipt imports plus 3 LEGACY_BACKFILL model rows carried over from
# bets.json (sourceKeys 2026-09-17-181/182/183, reference-sized at
# $10 notional, never executed). The daily report printed only
# len(bets)=8, so a reader had no way to see that three of them were not
# wagers at all.
# ---------------------------------------------------------------------------

def _tracked(tracking_type, bet_id):
    return {"betId": bet_id, "gameDate": DATE, "trackingType": tracking_type}


def test_daily_report_separates_real_wagers_from_other_tracked_records():
    games, markets, observations, recommendations, clv_quotes, settlements, _, research_runs = _sample_inputs()
    bets = (
        [_tracked("REAL", f"real-{i}") for i in range(5)]
        + [_tracked(None, f"legacy-{i}") for i in range(3)]
    )
    report = build_daily_report(
        DATE, games, markets, observations, recommendations,
        clv_quotes, settlements, bets, research_runs,
    )
    # The all-records total stays what it was -- it is still a true count.
    assert report["placedBets"] == 8
    # ...but the real-money count is now stated separately, and is 5.
    assert report["realWagerCount"] == 5
    assert report["placedBetsByTrackingType"] == {"REAL": 5, "UNCLASSIFIED_LEGACY": 3}

    markdown = render_markdown(report)
    assert "- REAL-money wagers: 5" in markdown
    assert "- Placed bets (all tracked ledger records): 8" in markdown
    assert "- UNCLASSIFIED_LEGACY: 3" in markdown


def test_real_wager_count_does_not_absorb_paper_or_model_rows():
    games, markets, observations, recommendations, clv_quotes, settlements, _, research_runs = _sample_inputs()
    bets = [_tracked("REAL", "r1"), _tracked("PAPER", "p1"), _tracked("MODEL_ONLY", "m1")]
    report = build_daily_report(
        DATE, games, markets, observations, recommendations,
        clv_quotes, settlements, bets, research_runs,
    )
    assert report["placedBets"] == 3
    assert report["realWagerCount"] == 1
    assert report["placedBetsByTrackingType"] == {"REAL": 1, "PAPER": 1, "MODEL_ONLY": 1}


# ---------------------------------------------------------------------------
# Regression: a report must never headline CLV that is not closing-line
# evidence. See docs/EDGELAB_CLOSING_QUOTE_POLICY.md.
# ---------------------------------------------------------------------------

def _clv_bet(bet_id, clv, coverage, seconds=None):
    return {"betId": bet_id, "gameDate": DATE, "trackingType": "REAL", "clv": clv,
            "closingCoverageClass": coverage, "closingSecondsBeforeStart": seconds}


def _clv_daily_report_with(bets):
    games, markets, observations, recommendations, clv_quotes, settlements, _, research_runs = _sample_inputs()
    return build_daily_report(DATE, games, markets, observations, recommendations,
                              clv_quotes, settlements, bets, research_runs)


def test_headline_clv_uses_true_close_rows_only():
    report = _clv_daily_report_with([
        _clv_bet("a", 4.0, "TRUE_CLOSE", 600),
        _clv_bet("b", 2.0, "TRUE_CLOSE", 1200),
        _clv_bet("c", -60.0, "PRE_CLOSE", 34140),   # the stale-quote shape
    ])
    clv = report["clvSummary"]
    assert clv["avgClvCents"] == 3.0            # (4 + 2) / 2 -- the -60 is excluded
    assert clv["clvEligibleCount"] == 2
    assert clv["trueCloseCount"] == 2
    assert clv["preCloseOnlyCount"] == 1
    assert clv["positiveClvCount"] == 2 and clv["negativeClvCount"] == 0
    # ...and the stale value is still visible, just never as the headline.
    assert clv["avgClvCentsAllCoverageIncludingStale"] == -18.0


def test_all_pre_close_yields_no_headline_clv_rather_than_a_misleading_number():
    report = _clv_daily_report_with([
        _clv_bet("a", -59.0, "PRE_CLOSE", 34140),
        _clv_bet("b", -61.0, "PRE_CLOSE", 50000),
    ])
    clv = report["clvSummary"]
    assert clv["avgClvCents"] is None
    assert clv["clvEligibleCount"] == 0
    assert clv["preCloseOnlyCount"] == 2
    assert clv["avgClvCentsAllCoverageIncludingStale"] == -60.0


def test_markdown_labels_headline_as_true_close_only_and_flags_the_diagnostic():
    markdown = render_markdown(_clv_daily_report_with([
        _clv_bet("a", 4.0, "TRUE_CLOSE", 600),
        _clv_bet("b", -60.0, "PRE_CLOSE", 34140),
    ]))
    assert "Average CLV (cents), TRUE_CLOSE only: 4.0" in markdown
    assert "PRE_CLOSE only: 1" in markdown
    assert "NOT closing-line value" in markdown


def test_quote_age_distribution_is_reported():
    report = _clv_daily_report_with([
        _clv_bet("a", 1.0, "TRUE_CLOSE", 600),
        _clv_bet("b", 1.0, "PRE_CLOSE", 3600),
        _clv_bet("c", 1.0, "PRE_CLOSE", 34140),
    ])
    clv = report["clvSummary"]
    assert clv["medianSecondsBeforeStart"] == 3600
    assert clv["p90SecondsBeforeStart"] == 34140


def test_postmortem_headline_clv_is_true_close_only():
    """The postmortem headline is gated exactly like the daily report's."""
    bets = [
        {"betId": "t1", "gameDate": "2026-08-01", "trackingType": "REAL", "status": "settled",
         "result": "WIN", "stake": 10.0, "netProfitLoss": 5.0, "clv": 3.0,
         "closingCoverageClass": "TRUE_CLOSE", "closingSecondsBeforeStart": 600},
        {"betId": "t2", "gameDate": "2026-08-01", "trackingType": "REAL", "status": "settled",
         "result": "LOSS", "stake": 10.0, "netProfitLoss": -10.0, "clv": -59.0,
         "closingCoverageClass": "PRE_CLOSE", "closingSecondsBeforeStart": 34140},
    ]
    report = build_postmortem("2026-08-01", bets)
    assert report["avgClvCents"] == 3.0            # the -59 stale row is excluded
    assert report["clvCoverage"]["preCloseOnlyCount"] == 1
    assert report["clvCoverage"]["avgClvCentsAllCoverageIncludingStale"] == -28.0
    markdown = render_postmortem_markdown(report)
    assert "TRUE_CLOSE only: 3.0" in markdown
    assert "not closing-line evidence" in markdown
