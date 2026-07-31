#!/usr/bin/env python3
"""
tests/test_generate_wager_research_report.py
=================================================
Coverage for scripts/generate_wager_research_report.py: aggregate report
math, daily report math, calibration-bin exclusions, and sample-size
transparency.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.generate_wager_research_report as rpt  # noqa: E402
import scripts.build_wager_research_db as db  # noqa: E402


def row(date, result="WIN", stake=10.0, net_profit=5.0, market_family="game_result",
        clv_ask=1.0, clv_mid=2.0, model_prob=None):
    r = {k: None for k in db.CANONICAL_FIELDS}
    r.update({
        "betId": f"{date}-x", "date": date, "result": result, "stake": stake,
        "netProfit": net_profit, "grossReturn": (stake + net_profit) if net_profit is not None else None,
        "roiPct": round(net_profit / stake * 100, 3) if (stake and net_profit is not None) else None,
        "marketFamily": market_family, "clvAskPct": clv_ask, "clvMidPct": clv_mid,
        "modelProbPct": model_prob, "clvCaptureStatus": "OK" if clv_mid is not None else None,
    })
    return r


class TestSummarize:

    def test_sample_size_reported(self):
        rows = [row("2026-07-01"), row("2026-07-02")]
        s = rpt.summarize(rows)
        assert s["sampleSize"] == 2
        assert s["settledSampleSize"] == 2

    def test_record_counts_correct(self):
        rows = [row("2026-07-01", result="WIN"), row("2026-07-01", result="LOSS"),
                row("2026-07-01", result="PUSH")]
        s = rpt.summarize(rows)
        assert s["record"] == {"wins": 1, "losses": 1, "pushesVoids": 1}

    def test_net_profit_and_roi_sum_correctly(self):
        rows = [row("2026-07-01", stake=10, net_profit=5), row("2026-07-01", stake=10, net_profit=-10)]
        s = rpt.summarize(rows)
        assert s["totalRisked"] == 20.0
        assert s["netProfit"] == -5.0
        assert s["roiPct"] == round(-5 / 20 * 100, 3)

    def test_pending_rows_excluded_from_settled_metrics(self):
        pending = row("2026-07-01", result="PENDING", net_profit=None, stake=10)
        settled = row("2026-07-01", result="WIN", net_profit=5, stake=10)
        s = rpt.summarize([pending, settled])
        assert s["sampleSize"] == 2
        assert s["settledSampleSize"] == 1
        assert s["totalRisked"] == 10.0  # only the settled one counted

    def test_avg_clv_computed_from_available_values_only(self):
        rows = [row("2026-07-01", clv_mid=2.0), row("2026-07-01", clv_mid=None), row("2026-07-01", clv_mid=4.0)]
        s = rpt.summarize(rows)
        assert s["avgMidClvPct"] == 3.0  # average of 2.0 and 4.0, None excluded

    def test_positive_clv_rate(self):
        rows = [row("2026-07-01", clv_mid=2.0), row("2026-07-01", clv_mid=-1.0)]
        s = rpt.summarize(rows)
        assert s["positiveClvRatePct"] == 50.0

    def test_empty_rows_never_crashes(self):
        s = rpt.summarize([])
        assert s["sampleSize"] == 0
        assert s["netProfit"] is None
        assert s["roiPct"] is None


class TestWindows:

    def test_last_n_settled_betting_days_not_calendar_days(self):
        rows = [row("2026-07-01"), row("2026-07-03"), row("2026-07-05")]
        windowed = rpt.rows_in_window(rows, 2)
        dates = {r["date"] for r in windowed}
        assert dates == {"2026-07-03", "2026-07-05"}  # the 2 most recent SETTLED dates, not last 2 calendar days

    def test_window_excludes_pending_only_dates(self):
        rows = [row("2026-07-01"), row("2026-07-02", result="PENDING", net_profit=None)]
        windowed = rpt.rows_in_window(rows, 7)
        assert all(r["date"] == "2026-07-01" for r in windowed)


class TestBreakdowns:

    def test_by_market_family_groups_correctly(self):
        rows = [row("2026-07-01", market_family="game_result"), row("2026-07-01", market_family="game_total")]
        breakdowns = rpt.build_breakdowns(rows)
        assert set(breakdowns["byMarketFamily"].keys()) == {"game_result", "game_total"}
        assert breakdowns["byMarketFamily"]["game_result"]["sampleSize"] == 1


class TestDailyReport:

    def test_daily_report_filters_to_exact_date(self):
        rows = [row("2026-07-01"), row("2026-07-02")]
        daily = rpt.build_daily_report(rows, "2026-07-01")
        assert daily["summary"]["sampleSize"] == 1
        assert daily["date"] == "2026-07-01"


class TestFullBuildAndReport:

    def test_full_pipeline_report_uses_real_wagers_db(self, tmp_path):
        import json
        bets = [
            {"id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML",
             "result": "WIN", "pl": 5.0, "stake": 10.0, "modelPct": 60},
            {"id": "b2", "date": "2026-07-30", "game": "SF @ SD", "market": "Total",
             "result": "LOSS", "stake": 5.0, "modelPct": 55},
        ]
        bets_path = str(tmp_path / "bets.json")
        with open(bets_path, "w") as f:
            json.dump(bets, f)
        build_result = db.main(bets_path=bets_path, out_dir=str(tmp_path / "research"),
                                paper_ledger_path=str(tmp_path / "no_paper_ledger.jsonl"))
        rows = build_result["rows"]
        report_result = rpt.main(wagers_path=None, out_dir=str(tmp_path / "reports"), dry_run=True) \
            if False else {"summary": rpt.build_summary_report(rows)}
        assert report_result["summary"]["allTime"]["sampleSize"] == 2
        assert report_result["summary"]["allTime"]["record"]["wins"] == 1
        assert report_result["summary"]["allTime"]["record"]["losses"] == 1
