#!/usr/bin/env python3
"""
tests/test_build_wager_research_db.py
=========================================
Coverage for scripts/build_wager_research_db.py: deterministic builds,
no duplicates, exact-ticker joins, gameId fallback, legacy fallback,
manual bets, pending bets, missing CLV, settlement math (push/void/win/
loss), CSV/JSONL parity, null preservation, and doubleheader isolation.
"""
import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.build_wager_research_db as db  # noqa: E402


def write_bets(tmp_path, bets):
    path = str(tmp_path / "bets.json")
    with open(path, "w") as f:
        json.dump(bets, f)
    return path


class TestDeterminismAndNoDuplicates:

    def test_identical_input_produces_identical_rows(self, tmp_path):
        bets = [
            {"id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "betSide": "AWAY",
             "price": -120, "stake": 10, "status": "pending"},
            {"id": "b2", "date": "2026-07-30", "game": "SF @ SD", "market": "Total", "betSide": None,
             "price": -105, "stake": 5, "status": "pending"},
        ]
        path = write_bets(tmp_path, bets)
        rows1 = db.build_rows(db.load_bets(path))
        rows2 = db.build_rows(db.load_bets(path))
        assert rows1 == rows2

    def test_no_duplicate_betids(self, tmp_path):
        bets = [
            {"id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "stake": 10, "status": "pending"},
            {"id": "b2", "date": "2026-07-30", "game": "SF @ SD", "market": "Total", "stake": 5, "status": "pending"},
        ]
        path = write_bets(tmp_path, bets)
        rows = db.build_rows(db.load_bets(path))
        ids = [r["betId"] for r in rows]
        assert len(ids) == len(set(ids))

    def test_no_wager_ever_dropped(self, tmp_path):
        bets = [{"date": "2026-07-30", "game": "X @ Y", "market": "Unknown Weird Market"}] * 5
        path = write_bets(tmp_path, bets)
        rows = db.build_rows(db.load_bets(path))
        assert len(rows) == 5


class TestIdentityJoinPriority:

    def test_exact_ticker_preferred(self, tmp_path):
        bet = {"id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML",
               "marketTicker": "KXMLBGAME-X-BOS", "gameId": 824974, "status": "pending"}
        row = db.build_row(bet, 0)
        assert row["joinMethod"] == "exact_ticker"
        assert row["ticker"] == "KXMLBGAME-X-BOS"

    def test_gameid_family_period_side_line_fallback(self, tmp_path):
        bet = {"id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "Team Total",
               "gameId": 824974, "betSide": "BOS", "line": 4.5, "status": "pending"}
        row = db.build_row(bet, 0)
        assert row["joinMethod"] == "gameId_family_period_side_line"
        assert "824974" in row["betId"] or row["ticker"] is None

    def test_legacy_fallback_when_no_ticker_or_gameid(self, tmp_path):
        bet = {"date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "betSide": "AWAY"}
        row = db.build_row(bet, 0)
        assert row["joinMethod"] == "legacy_fallback"
        assert "NO_EXACT_OR_GAME_IDENTITY" in row["dataQualityFlags"]

    def test_doubleheader_games_isolated_by_gameid(self):
        bet_g1 = {"id": "b1", "date": "2026-07-30", "game": "BOS @ NYY", "market": "ML",
                  "gameId": 2001, "betSide": "BOS", "status": "pending"}
        bet_g2 = {"id": "b2", "date": "2026-07-30", "game": "BOS @ NYY", "market": "ML",
                  "gameId": 2002, "betSide": "BOS", "status": "pending"}
        row1 = db.build_row(bet_g1, 0)
        row2 = db.build_row(bet_g2, 1)
        assert row1["betId"] != row2["betId"]
        assert row1["gameId"] != row2["gameId"]


class TestManualAndPendingBets:

    def test_manual_bet_preserved(self):
        bet = {"id": "m1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML",
               "source": "MANUAL", "status": "pending", "stake": 10}
        row = db.build_row(bet, 0)
        assert row["source"] == "MANUAL"
        assert row["result"] == "PENDING"

    def test_pending_bet_has_null_financials(self):
        bet = {"id": "p1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "status": "pending"}
        row = db.build_row(bet, 0)
        assert row["result"] == "PENDING"
        assert row["grossReturn"] is None
        assert row["netProfit"] is None
        assert row["roiPct"] is None

    def test_missing_clv_stays_null_not_zero(self):
        bet = {"id": "c1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "status": "settled",
               "result": "WIN", "pl": 5.0, "stake": 5.0}
        row = db.build_row(bet, 0)
        assert row["clvAskPct"] is None
        assert row["clvMidPct"] is None


class TestSettlementMath:

    def test_win_uses_stored_pl(self):
        bet = {"id": "w1", "date": "2026-07-30", "game": "X @ Y", "market": "ML",
               "result": "WIN", "pl": 8.5, "stake": 10.0}
        row = db.build_row(bet, 0)
        assert row["netProfit"] == 8.5
        assert row["grossReturn"] == 18.5
        assert row["roiPct"] == 85.0

    def test_loss_without_pl_defaults_to_negative_stake(self):
        bet = {"id": "l1", "date": "2026-07-30", "game": "X @ Y", "market": "ML",
               "result": "LOSS", "stake": 10.0}
        row = db.build_row(bet, 0)
        assert row["netProfit"] == -10.0
        assert row["grossReturn"] == 0.0
        assert row["roiPct"] == -100.0

    def test_win_without_pl_stays_null_never_guessed(self):
        bet = {"id": "w2", "date": "2026-07-30", "game": "X @ Y", "market": "ML",
               "result": "WIN", "stake": 10.0}
        row = db.build_row(bet, 0)
        assert row["netProfit"] is None
        assert row["grossReturn"] is None

    def test_push_returns_stake_zero_profit(self):
        bet = {"id": "pu1", "date": "2026-07-30", "game": "X @ Y", "market": "Total",
               "result": "PUSH", "stake": 10.0}
        row = db.build_row(bet, 0)
        assert row["netProfit"] == 0.0
        assert row["grossReturn"] == 10.0
        assert row["roiPct"] == 0.0

    def test_void_returns_stake_zero_profit(self):
        bet = {"id": "vo1", "date": "2026-07-30", "game": "X @ Y", "market": "Total",
               "result": "VOID", "stake": 10.0}
        row = db.build_row(bet, 0)
        assert row["netProfit"] == 0.0
        assert row["grossReturn"] == 10.0

    def test_never_overwrites_stored_result_from_score(self):
        """resolve_outcome/compute_financials must use ONLY the ledger's
        own stored result -- there is no score-based override path."""
        bet = {"id": "s1", "date": "2026-07-30", "game": "X @ Y", "market": "ML",
               "result": "WIN", "pl": 5.0, "stake": 5.0,
               "awayScore": 2, "homeScore": 10}  # would look like a LOSS if inferred from score
        row = db.build_row(bet, 0)
        assert row["result"] == "WIN"
        assert row["netProfit"] == 5.0


class TestNullPreservation:

    def test_absent_fields_are_none_not_zero(self):
        bet = {"date": "2026-07-30", "game": "X @ Y", "market": "ML"}
        row = db.build_row(bet, 0)
        assert row["stake"] is None
        assert row["clvAskPct"] is None
        assert row["park"] is None
        assert row["weather"] is None


class TestCsvJsonlParity:

    def test_csv_and_jsonl_have_same_row_count(self, tmp_path):
        bets = [
            {"id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "stake": 10, "status": "pending"},
            {"id": "b2", "date": "2026-07-30", "game": "SF @ SD", "market": "Total", "stake": 5, "status": "pending"},
        ]
        path = write_bets(tmp_path, bets)
        result = db.main(bets_path=path, out_dir=str(tmp_path / "out"), paper_ledger_path=str(tmp_path / "no_paper_ledger.jsonl"))
        jsonl_path = tmp_path / "out" / "wagers.jsonl"
        csv_path = tmp_path / "out" / "wagers.csv"
        with open(jsonl_path) as f:
            jsonl_count = sum(1 for _ in f)
        with open(csv_path, newline="") as f:
            csv_count = sum(1 for _ in csv.DictReader(f))
        assert jsonl_count == csv_count == 2


class TestCalibrationBins:

    def test_excludes_pending_bets(self):
        rows = [
            db.build_row({"id": "a", "date": "2026-07-30", "game": "X", "market": "ML",
                          "result": "WIN", "pl": 5, "stake": 10, "modelPct": 60}, 0),
            db.build_row({"id": "b", "date": "2026-07-30", "game": "X", "market": "ML",
                          "status": "pending", "modelPct": 60}, 1),
        ]
        bins = db.build_calibration_bins(rows)
        total_n = sum(b["sampleSize"] for b in bins)
        assert total_n == 1  # only the settled WIN counted

    def test_excludes_push_and_void(self):
        rows = [
            db.build_row({"id": "a", "date": "2026-07-30", "game": "X", "market": "Total",
                          "result": "PUSH", "stake": 10, "modelPct": 55}, 0),
            db.build_row({"id": "b", "date": "2026-07-30", "game": "X", "market": "Total",
                          "result": "VOID", "stake": 10, "modelPct": 55}, 1),
        ]
        bins = db.build_calibration_bins(rows)
        assert sum(b["sampleSize"] for b in bins) == 0

    def test_excludes_missing_model_prob(self):
        rows = [db.build_row({"id": "a", "date": "2026-07-30", "game": "X", "market": "ML",
                              "result": "WIN", "pl": 5, "stake": 10}, 0)]  # no modelPct
        bins = db.build_calibration_bins(rows)
        assert sum(b["sampleSize"] for b in bins) == 0

    def test_excludes_non_binary_market_family(self):
        rows = [db.build_row({"id": "a", "date": "2026-07-30", "game": "X", "market": "K Prop",
                              "result": "WIN", "pl": 5, "stake": 10, "modelPct": 60}, 0)]
        bins = db.build_calibration_bins(rows)
        assert sum(b["sampleSize"] for b in bins) == 0  # marketFamily is None (unrecognized) -> excluded

    def test_bin_sample_size_and_win_rate_correct(self):
        rows = [
            db.build_row({"id": f"w{i}", "date": "2026-07-30", "game": "X", "market": "ML",
                         "result": "WIN", "pl": 5, "stake": 10, "modelPct": 65}, i)
            for i in range(3)
        ] + [
            db.build_row({"id": "l1", "date": "2026-07-30", "game": "X", "market": "ML",
                         "result": "LOSS", "stake": 10, "modelPct": 65}, 3)
        ]
        bins = db.build_calibration_bins(rows)
        target = next(b for b in bins if b["binLabel"] == "60-70%")
        assert target["sampleSize"] == 4
        assert target["actualWinRatePct"] == 75.0


class TestBuildReport:

    def test_report_counts_match_rows(self, tmp_path):
        bets = [
            {"id": "b1", "date": "2026-07-30", "game": "BOS @ ATH", "market": "ML", "stake": 10, "status": "pending"},
        ]
        path = write_bets(tmp_path, bets)
        result = db.main(bets_path=path, out_dir=str(tmp_path / "out"), paper_ledger_path=str(tmp_path / "no_paper_ledger.jsonl"))
        assert result["report"]["sourceBetsCount"] == 1
        assert result["report"]["canonicalRowsCount"] == 1
        assert result["report"]["rowsDroppedCount"] == 0
