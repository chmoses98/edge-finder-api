#!/usr/bin/env python3
"""
tests/test_build_paper_spread_ledger.py
============================================
Coverage for scripts/build_paper_spread_ledger.py: paper-eligible
row construction, edge-floor filtering, idempotent append, and pure
settlement math.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import scripts.build_paper_spread_ledger as psl  # noqa: E402


def make_contract(ticker="T-1", edge=5.0, family="winning_margin", period="full_game",
                   supported=True, blocked=True, block_reasons=None, side="BOS", line=1.5,
                   paper_tracking_status="ELIGIBLE"):
    return {
        "ticker": ticker, "gameId": 100, "awayTeam": "BOS", "homeTeam": "NYY",
        "marketFamily": family, "period": period, "side": side, "line": line,
        "alternateLine": True, "fairProbabilityPct": 20.0, "yesAsk": 15.0, "yesBid": 14.0,
        "rawEdgePct": edge, "rank": 3,
        "modelSupportStatus": "SUPPORTED" if supported else "UNSUPPORTED",
        "realMoneyEligibilityStatus": "BLOCKED" if blocked else "NOT_GOVERNED_BY_THIS_ARTIFACT",
        "realMoneyBlockReasons": block_reasons or (["RULE_81"] if blocked else []),
        "paperTrackingStatus": paper_tracking_status if supported else "NOT_ELIGIBLE",
    }


class TestBuildPaperRows:

    def test_supported_blocked_above_floor_becomes_paper_row(self):
        rows = psl.build_paper_rows("2026-07-30", [make_contract(edge=5.0)])
        assert len(rows) == 1
        assert rows[0]["trackingType"] == "PAPER"
        assert rows[0]["countsTowardBankroll"] is False
        assert rows[0]["result"] == "PENDING"

    def test_below_floor_excluded(self):
        rows = psl.build_paper_rows("2026-07-30", [make_contract(edge=0.1)])
        assert rows == []

    def test_unsupported_excluded(self):
        rows = psl.build_paper_rows("2026-07-30", [make_contract(edge=5.0, supported=False)])
        assert rows == []

    def test_non_spread_family_excluded(self):
        rows = psl.build_paper_rows("2026-07-30", [make_contract(edge=5.0, family="game_result")])
        assert rows == []

    def test_null_edge_excluded_not_crashed(self):
        c = make_contract(edge=5.0)
        c["rawEdgePct"] = None
        rows = psl.build_paper_rows("2026-07-30", [c])
        assert rows == []

    def test_f3_winner_market_paper_tracked_after_structure_verification(self):
        """
        Spread/F3-F7-correction mission: F3/F7 winner markets are now
        modeled (structure independently verified) and BLOCKED (never
        yet activated) -- they must paper-track the same as spreads,
        despite this module's filename.
        """
        c = make_contract(ticker="F3-1", edge=5.0, family="inning_result", period="F3",
                          side="Away", line=None, block_reasons=["NOT_YET_ACTIVATED_NO_HISTORICAL_PAPER_SAMPLE"])
        rows = psl.build_paper_rows("2026-07-30", [c])
        assert len(rows) == 1
        assert rows[0]["marketFamily"] == "inning_result"
        assert rows[0]["period"] == "F3"

    def test_f5_winner_market_not_paper_tracked_governed_elsewhere(self):
        """F5 winner markets are NOT_GOVERNED_BY_THIS_ARTIFACT (already
        production-evaluated), not BLOCKED -- must not enter this
        paper ledger."""
        c = make_contract(ticker="F5-1", edge=5.0, family="inning_result", period="F5",
                          side="Away", line=None, blocked=False)
        rows = psl.build_paper_rows("2026-07-30", [c])
        assert rows == []

    def test_real_money_eligible_contract_not_paper_tracked(self):
        """A contract that is somehow NOT blocked (hypothetically real-
        money eligible) must not enter the paper ledger -- paper
        tracking is specifically for blocked spread markets."""
        rows = psl.build_paper_rows("2026-07-30", [make_contract(edge=5.0, blocked=False)])
        assert rows == []

    def test_live_or_started_game_not_paper_tracked(self):
        """A contract whose discovery-computed paperTrackingStatus is
        NOT_ELIGIBLE (e.g. because the underlying game already started
        -- see compute_status_fields()'s pregame gate) must never
        become a paper wager, even if otherwise supported and blocked."""
        rows = psl.build_paper_rows(
            "2026-07-30", [make_contract(edge=5.0, paper_tracking_status="NOT_ELIGIBLE")])
        assert rows == []


class TestIdempotentAppend:

    def test_rerun_produces_no_duplicates(self, tmp_path):
        ledger_path = str(tmp_path / "paper_spread_ledger.jsonl")
        rows = psl.build_paper_rows("2026-07-30", [make_contract(ticker="T-1", edge=5.0)])
        first = psl.append_rows(ledger_path, rows)
        second = psl.append_rows(ledger_path, rows)
        assert first == 1
        assert second == 0
        assert len(psl.load_existing_rows(ledger_path)) == 1

    def test_new_ticker_on_rerun_appends_only_new_one(self, tmp_path):
        ledger_path = str(tmp_path / "paper_spread_ledger.jsonl")
        psl.append_rows(ledger_path, psl.build_paper_rows("2026-07-30", [make_contract(ticker="T-1", edge=5.0)]))
        appended = psl.append_rows(ledger_path, psl.build_paper_rows(
            "2026-07-30", [make_contract(ticker="T-1", edge=5.0), make_contract(ticker="T-2", edge=6.0)]))
        assert appended == 1
        rows = psl.load_existing_rows(ledger_path)
        assert {r["ticker"] for r in rows} == {"T-1", "T-2"}

    def test_different_date_same_ticker_not_deduped(self, tmp_path):
        """(date, ticker) is the identity key -- a re-listed ticker on a
        different date is a genuinely different market instance."""
        ledger_path = str(tmp_path / "paper_spread_ledger.jsonl")
        psl.append_rows(ledger_path, psl.build_paper_rows("2026-07-30", [make_contract(ticker="T-1", edge=5.0)]))
        appended = psl.append_rows(ledger_path, psl.build_paper_rows("2026-07-31", [make_contract(ticker="T-1", edge=5.0)]))
        assert appended == 1


class TestSettlement:

    def _row(self, side="BOS", line=1.5, entry_ask=15.0, stake=5.0):
        return {
            "awayTeam": "BOS", "homeTeam": "NYY", "side": side, "line": line,
            "entryAskPct": entry_ask, "hypotheticalStake": stake, "result": "PENDING",
        }

    def test_win_over_line_computes_positive_profit(self):
        row = self._row(side="BOS", line=1.5, entry_ask=20.0, stake=5.0)
        settled = psl.settle_paper_spread_row(row, away_final_score=10, home_final_score=2)
        assert settled["result"] == "WIN"
        assert settled["hypotheticalNetProfit"] > 0
        assert row["result"] == "PENDING"  # original not mutated

    def test_loss_under_line_computes_negative_stake(self):
        row = self._row(side="BOS", line=1.5, entry_ask=20.0, stake=5.0)
        settled = psl.settle_paper_spread_row(row, away_final_score=3, home_final_score=2)
        assert settled["result"] == "LOSS"
        assert settled["hypotheticalNetProfit"] == -5.0

    def test_exact_margin_equal_to_line_plus_half_is_loss_not_push(self):
        """line=1.5 means 'wins by MORE than 1.5' -- margin of exactly 1
        (not > 1.5) is a loss, never a push (Kalshi spread contracts
        have no push leg at a .5 line)."""
        row = self._row(side="BOS", line=1.5)
        settled = psl.settle_paper_spread_row(row, away_final_score=3, home_final_score=2)
        assert settled["result"] == "LOSS"

    def test_missing_scores_stays_pending(self):
        row = self._row()
        settled = psl.settle_paper_spread_row(row, away_final_score=None, home_final_score=None)
        assert settled["result"] == "PENDING"

    def test_side_not_matching_away_or_home_stays_pending(self):
        row = self._row(side="XYZ")
        settled = psl.settle_paper_spread_row(row, away_final_score=10, home_final_score=2)
        assert settled["result"] == "PENDING"


class TestMainIntegration:

    def test_main_reads_discovery_file_and_writes_ledger(self, tmp_path):
        discovery_path = tmp_path / "2026-07-30.json"
        discovery_path.write_text(json.dumps({
            "date": "2026-07-30",
            "contracts": [make_contract(ticker="T-1", edge=5.0)],
        }))
        ledger_path = str(tmp_path / "paper_spread_ledger.jsonl")
        result = psl.main(date_str="2026-07-30", discovery_path=str(discovery_path), ledger_path=ledger_path)
        assert result["appended"] == 1
        assert os.path.exists(ledger_path)

    def test_missing_discovery_file_returns_status_not_crash(self, tmp_path):
        result = psl.main(date_str="2026-07-30", discovery_path=str(tmp_path / "nope.json"),
                           ledger_path=str(tmp_path / "ledger.jsonl"))
        assert result["status"] == "NO_DISCOVERY_FILE"
        assert result["appended"] == 0
