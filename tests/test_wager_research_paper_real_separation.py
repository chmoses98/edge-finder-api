#!/usr/bin/env python3
"""
tests/test_wager_research_paper_real_separation.py
========================================================
Spread-correction mission Part 5 coverage: the research database and
reports must keep REAL/MANUAL bankroll performance and PAPER spread
performance structurally separate.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.build_wager_research_db as db  # noqa: E402
import scripts.generate_wager_research_report as rpt  # noqa: E402


def make_paper_ledger_row(ticker="T-1", date="2026-07-30", result="PENDING", net_profit=None):
    return {
        "date": date, "ticker": ticker, "gameId": 1, "awayTeam": "BOS", "homeTeam": "NYY",
        "marketFamily": "winning_margin", "period": "full_game", "side": "BOS", "line": 1.5,
        "alternateLine": True, "fairProbabilityPct": 20.0, "entryAskPct": 15.0,
        "entryMidpointPct": 14.5, "rawEdgePct": 5.0, "calibratedEdgePct": None,
        "confidenceTier": "MEDIUM", "rank": 3, "hypotheticalStake": 5.0,
        "trackingType": "PAPER", "countsTowardBankroll": False,
        "realMoneyBlockReasons": ["RULE_81"], "closingAskPct": None, "closingMidpointPct": None,
        "clvAskPct": None, "clvMidPct": None, "result": result,
        "hypotheticalNetProfit": net_profit,
        "hypotheticalRoiPct": (round(net_profit / 5.0 * 100, 3) if net_profit is not None else None),
        "settledAt": None,
    }


class TestBuildPaperRow:

    def test_paper_row_has_null_real_financials(self):
        row = db.build_paper_row(make_paper_ledger_row(net_profit=10.0, result="WIN"))
        assert row["stake"] is None
        assert row["netProfit"] is None
        assert row["roiPct"] is None
        assert row["hypotheticalNetProfit"] == 10.0
        assert row["trackingType"] == "PAPER"
        assert row["countsTowardBankroll"] is False

    def test_real_bet_row_marked_real_and_counts_toward_bankroll(self):
        bet = {"id": "b1", "date": "2026-07-30", "game": "BOS @ NYY", "market": "ML",
               "result": "WIN", "pl": 5.0, "stake": 5.0}
        row = db.build_row(bet, 0)
        assert row["trackingType"] == "REAL"
        assert row["countsTowardBankroll"] is True
        assert row["hypotheticalStake"] is None

    def test_manual_bet_tracked_as_manual(self):
        bet = {"id": "m1", "date": "2026-07-30", "game": "BOS @ NYY", "market": "ML",
               "source": "MANUAL", "status": "pending"}
        row = db.build_row(bet, 0)
        assert row["trackingType"] == "MANUAL"
        assert row["countsTowardBankroll"] is True


class TestMainIngestsPaperLedger:

    def test_paper_rows_merged_alongside_real_rows(self, tmp_path):
        bets_path = tmp_path / "bets.json"
        bets_path.write_text(json.dumps([
            {"id": "b1", "date": "2026-07-30", "game": "BOS @ NYY", "market": "ML",
             "result": "WIN", "pl": 5.0, "stake": 5.0},
        ]))
        ledger_path = tmp_path / "paper_spread_ledger.jsonl"
        with open(ledger_path, "w") as f:
            f.write(json.dumps(make_paper_ledger_row()) + "\n")

        result = db.main(bets_path=str(bets_path), out_dir=str(tmp_path / "out"),
                          paper_ledger_path=str(ledger_path))
        assert len(result["realRows"]) == 1
        assert len(result["paperRows"]) == 1
        assert len(result["rows"]) == 2
        assert result["report"]["paperRowsCount"] == 1

    def test_calibration_and_report_scoped_to_real_rows_only(self, tmp_path):
        """Paper rows must never contaminate real-money calibration/
        quality counts."""
        bets_path = tmp_path / "bets.json"
        bets_path.write_text(json.dumps([
            {"id": "b1", "date": "2026-07-30", "game": "BOS @ NYY", "market": "ML",
             "result": "WIN", "pl": 5.0, "stake": 5.0, "modelPct": 60},
        ]))
        ledger_path = tmp_path / "paper_spread_ledger.jsonl"
        with open(ledger_path, "w") as f:
            f.write(json.dumps(make_paper_ledger_row(result="WIN", net_profit=20.0)) + "\n")

        result = db.main(bets_path=str(bets_path), out_dir=str(tmp_path / "out"),
                          paper_ledger_path=str(ledger_path))
        assert result["report"]["sourceBetsCount"] == 1
        assert result["report"]["canonicalRowsCount"] == 1  # real rows only
        bins = result["calibration"]
        total_n = sum(b["sampleSize"] for b in bins)
        assert total_n == 1  # only the real WIN counted, not the paper WIN


class TestReportSeparation:

    def test_paper_performance_excluded_from_real_alltime_roi(self):
        real_row = {k: None for k in db.CANONICAL_FIELDS}
        real_row.update({
            "betId": "r1", "date": "2026-07-30", "result": "WIN", "stake": 10.0,
            "netProfit": 5.0, "roiPct": 50.0, "trackingType": "REAL", "countsTowardBankroll": True,
        })
        paper_row = db.build_paper_row(make_paper_ledger_row(result="WIN", net_profit=1000.0))
        summary = rpt.build_summary_report([real_row, paper_row])
        # A $1000 paper profit must not leak into real netProfit.
        assert summary["allTime"]["netProfit"] == 5.0
        assert summary["allTime"]["sampleSize"] == 1

    def test_paper_performance_reported_in_its_own_section(self):
        paper_row = db.build_paper_row(make_paper_ledger_row(result="WIN", net_profit=10.0))
        summary = rpt.build_summary_report([paper_row])
        assert summary["paperSpreadPerformance"]["allTime"]["sampleSize"] == 1
        assert summary["paperSpreadPerformance"]["allTime"]["record"]["wins"] == 1
        assert summary["allTime"]["sampleSize"] == 0

    def test_daily_report_separates_real_and_paper(self):
        real_row = {k: None for k in db.CANONICAL_FIELDS}
        real_row.update({
            "betId": "r1", "date": "2026-07-30", "result": "LOSS", "stake": 10.0,
            "netProfit": -10.0, "roiPct": -100.0, "trackingType": "REAL", "countsTowardBankroll": True,
        })
        paper_row = db.build_paper_row(make_paper_ledger_row(result="WIN", net_profit=10.0))
        daily = rpt.build_daily_report([real_row, paper_row], "2026-07-30")
        assert daily["summary"]["sampleSize"] == 1
        assert daily["summary"]["record"]["losses"] == 1
        assert daily["paperSpreadPerformance"]["record"]["wins"] == 1
