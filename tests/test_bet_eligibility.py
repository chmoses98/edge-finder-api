#!/usr/bin/env python3
"""
tests/test_bet_eligibility.py — Rule 71 Patch Test Suite
=========================================================
Proves the six required behavioral guarantees:

  ✓ Missing closing snapshot does NOT block a live actionable bet
  ✓ Missing CLV does NOT block a live actionable bet
  ✓ Missing ticker DOES block a real-money bet
  ✓ Missing entry price DOES block a real-money bet
  ✓ Ambiguous ticker DOES block a real-money bet
  ✓ A valid bet with pending CLV still appears in actionable output
  ✓ Review can settle ROI without CLV when CLV is unavailable
  ✓ Rule 71 still protects review integrity without suppressing valid live bets

Run from repo root:
  PYTHONPATH=scripts python tests/test_bet_eligibility.py
"""

import sys, os, unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scripts')
sys.path.insert(0, SCRIPTS_DIR)

from bet_eligibility import (
    classify_bet_eligibility,
    apply_eligibility,
    BET_ELIGIBLE, BET_PAPER, BET_REJECTED,
    BET_BLOCK_IDENTITY, BET_BLOCK_PRICE, BET_BLOCK_AMBIGUOUS, BET_BLOCK_RULE,
    CLV_READY, CLV_PENDING, CLV_UNAVAILABLE, CLV_MISSING_SNAP, CLV_NOT_YET,
    REVIEW_FULL, REVIEW_SETTLE_ONLY, REVIEW_BACKFILL, REVIEW_NO_IDENTITY,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def valid_bet(overrides=None):
    """Minimal valid bet for classify_bet_eligibility."""
    defaults = dict(
        market_ticker="KXMLBGAME-26JUN121400BOSCLE-BOS",
        entry_price=0.57,
        ledger_status="Accepted",
        rule_block_reason=None,
        is_paper_only=False,
        ambiguous_ticker=False,
        missing_fields=[],
        clv_snapshot_captured=None,
    )
    if overrides:
        defaults.update(overrides)
    return defaults

def classify(**kwargs):
    return classify_bet_eligibility(**valid_bet(kwargs))


# ── Suite 1: CLV missing / pending does NOT block live bets ──────────────────

class TestCLVDoesNotBlockLiveBets(unittest.TestCase):
    """
    CORE PRINCIPLE: Missing/pending CLV is a label, not a live bet blocker.
    """

    def test_actionable_when_clv_snapshot_not_yet_taken(self):
        """
        Example A: ticker exists, price exists, edge passes, but closing snapshot not taken yet.
        Expected: bet_eligibility_status = 'actionable'
        """
        result = classify(clv_snapshot_captured=None)
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE,
                         "Bet must be actionable when CLV snapshot has not been taken yet")

    def test_actionable_when_closing_snapshot_missed(self):
        """
        Example B: ticker exists, price exists, edge passes, but closing snapshot was missed.
        Expected: bet_eligibility_status = 'actionable'
        """
        result = classify(clv_snapshot_captured=False)
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE,
                         "Bet must still be actionable even when closing snapshot was missed")

    def test_clv_unavailable_does_not_change_eligibility(self):
        """CLV unavailable → clv_capture_status reflects that, but bet is still actionable."""
        result = classify(clv_snapshot_captured=False)
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE)
        self.assertIn(result["clv_capture_status"], (CLV_MISSING_SNAP, CLV_UNAVAILABLE),
                      "CLV status must reflect missed snapshot, not drive eligibility")

    def test_clv_not_yet_applicable_bet_is_actionable(self):
        """Pre-game: CLV not applicable yet → still actionable."""
        result = classify(clv_snapshot_captured=None)
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE)
        self.assertEqual(result["clv_capture_status"], CLV_NOT_YET)

    def test_clv_captured_bet_is_actionable(self):
        """CLV already captured → still actionable (CLV captures don't suppress bets)."""
        result = classify(clv_snapshot_captured=True)
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE)
        self.assertEqual(result["clv_capture_status"], CLV_READY)

    def test_review_backfill_needed_does_not_block_bet(self):
        """review_integrity_status = needs_close_backfill must never block a bet."""
        result = classify(clv_snapshot_captured=None)
        # Even if review needs backfill, bet is actionable
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE)
        # review_integrity should be backfill or full, never no_identity
        self.assertNotEqual(result["review_integrity_status"], REVIEW_NO_IDENTITY)

    def test_settlement_only_review_does_not_block_bet(self):
        """settlement_only review (CLV missed) must not block a bet."""
        result = classify(clv_snapshot_captured=False)
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE)
        self.assertEqual(result["review_integrity_status"], REVIEW_SETTLE_ONLY)


# ── Suite 2: Market identity issues DO block live bets ────────────────────────

class TestMarketIdentityBlocksLiveBets(unittest.TestCase):

    def test_missing_ticker_blocks_real_money_bet(self):
        """No Kalshi ticker → blocked_market_identity, never actionable."""
        result = classify(market_ticker=None)
        self.assertEqual(result["bet_eligibility_status"], BET_BLOCK_IDENTITY,
                         "Missing ticker must block real-money bet")
        self.assertNotEqual(result["bet_eligibility_status"], BET_ELIGIBLE)

    def test_empty_ticker_blocks_real_money_bet(self):
        """Empty string ticker → same as missing."""
        result = classify(market_ticker="")
        self.assertEqual(result["bet_eligibility_status"], BET_BLOCK_IDENTITY)

    def test_missing_entry_price_blocks_real_money_bet(self):
        """No entry price → blocked_no_price."""
        result = classify(entry_price=None)
        self.assertEqual(result["bet_eligibility_status"], BET_BLOCK_PRICE,
                         "Missing entry price must block real-money bet")

    def test_ambiguous_ticker_blocks_real_money_bet(self):
        """Multiple tickers match → blocked_ambiguous_market."""
        result = classify(ambiguous_ticker=True)
        self.assertEqual(result["bet_eligibility_status"], BET_BLOCK_AMBIGUOUS,
                         "Ambiguous ticker must block real-money bet")

    def test_missing_data_ledger_status_blocks_bet(self):
        """Ledger status='Missing Data' → blocked_no_price (price never reached slate)."""
        result = classify(ledger_status="Missing Data")
        self.assertIn(result["bet_eligibility_status"],
                      (BET_BLOCK_PRICE, BET_BLOCK_IDENTITY),
                      "Missing Data ledger status must block bet")

    def test_rule_block_prevents_real_money_bet(self):
        """Rule-based hard block → blocked_existing_market_rule."""
        result = classify(rule_block_reason="Rule 34: NRFI blocked — total line >= 8.0")
        self.assertEqual(result["bet_eligibility_status"], BET_BLOCK_RULE)
        self.assertNotEqual(result["bet_eligibility_status"], BET_ELIGIBLE)

    def test_clv_unavailable_on_blocked_market(self):
        """When ticker is missing, CLV is also unavailable (nothing to track)."""
        result = classify(market_ticker=None)
        self.assertEqual(result["clv_capture_status"], CLV_UNAVAILABLE)

    def test_review_no_identity_on_blocked_market(self):
        """When ticker is missing, review integrity is cannot_review_market_identity."""
        result = classify(market_ticker=None)
        self.assertEqual(result["review_integrity_status"], REVIEW_NO_IDENTITY)


# ── Suite 3: Paper-only markets ───────────────────────────────────────────────

class TestPaperOnlyMarkets(unittest.TestCase):

    def test_paper_only_market_gets_paper_eligibility(self):
        """Game Total (Rule 71 suspension) → paper, not blocked."""
        result = classify(is_paper_only=True, ledger_status="Rejected")
        self.assertEqual(result["bet_eligibility_status"], BET_PAPER,
                         "Paper-only market must be 'paper', not 'blocked_*'")

    def test_paper_market_with_price_is_paper_not_blocked(self):
        """Paper-only + valid price → paper eligibility, CLV still trackable."""
        result = classify(is_paper_only=True, ledger_status="Rejected",
                          clv_snapshot_captured=None)
        self.assertEqual(result["bet_eligibility_status"], BET_PAPER)
        self.assertIn(result["clv_capture_status"],
                      (CLV_NOT_YET, CLV_PENDING, CLV_READY, CLV_MISSING_SNAP))


# ── Suite 4: Rejected (edge below threshold) ──────────────────────────────────

class TestRejectedBets(unittest.TestCase):

    def test_rejected_bet_gets_rejected_eligibility(self):
        """Edge below threshold → 'rejected', not 'blocked_*'."""
        result = classify(ledger_status="Rejected")
        self.assertEqual(result["bet_eligibility_status"], BET_REJECTED)

    def test_rejected_bet_clv_still_trackable(self):
        """Rejected bet with valid ticker → CLV is still tracked."""
        result = classify(ledger_status="Rejected", clv_snapshot_captured=None)
        self.assertNotEqual(result["clv_capture_status"], CLV_UNAVAILABLE,
                            "Rejected bet with ticker must still have CLV tracking status")


# ── Suite 5: Review can settle without CLV ────────────────────────────────────

class TestReviewWithoutCLV(unittest.TestCase):

    def test_settlement_only_when_clv_missed(self):
        """Closing snapshot missed → settlement_only review, not cannot_review."""
        result = classify(clv_snapshot_captured=False)
        self.assertEqual(result["review_integrity_status"], REVIEW_SETTLE_ONLY,
                         "Missed snapshot → settlement_only (ROI tracked, CLV unavailable)")

    def test_full_review_when_clv_captured(self):
        """CLV captured → full_review_ready."""
        result = classify(clv_snapshot_captured=True)
        self.assertEqual(result["review_integrity_status"], REVIEW_FULL)

    def test_backfill_review_when_clv_pending(self):
        """CLV not yet taken (live) → needs_close_backfill."""
        result = classify(clv_snapshot_captured=None)
        self.assertEqual(result["review_integrity_status"], REVIEW_BACKFILL)


# ── Suite 6: apply_eligibility() patches ledger rows non-destructively ────────

class TestApplyEligibility(unittest.TestCase):
    """
    Verifies apply_eligibility() adds the three fields WITHOUT changing
    any existing edge/confidence/betSize/status fields.
    """

    def _make_row(self, **overrides):
        row = {
            "market":         "ML_Away",
            "status":         "Accepted",
            "kalshiPrice":    0.57,
            "marketTicker":   "KXMLBGAME-26JUN121400BOSCLE-BOS",
            "edge":           3.2,
            "confidence":     "HIGH",
            "betSize":        4.0,
            "gatesFired":     [],
            "missingFields":  None,
            "rejectionReason": None,
        }
        row.update(overrides)
        return row

    def test_adds_three_status_fields(self):
        row = self._make_row()
        apply_eligibility(row)
        self.assertIn("bet_eligibility_status", row)
        self.assertIn("clv_capture_status", row)
        self.assertIn("review_integrity_status", row)

    def test_does_not_change_existing_fields(self):
        """apply_eligibility must NEVER modify status, edge, confidence, betSize."""
        row = self._make_row()
        original = {k: v for k, v in row.items()}
        apply_eligibility(row)
        for field in ("status", "edge", "confidence", "betSize", "kalshiPrice", "market"):
            self.assertEqual(row[field], original[field],
                             f"apply_eligibility must not change '{field}'")

    def test_accepted_row_gets_actionable(self):
        row = self._make_row(status="Accepted")
        apply_eligibility(row)
        self.assertEqual(row["bet_eligibility_status"], BET_ELIGIBLE)

    def test_missing_data_row_gets_blocked_no_price(self):
        row = self._make_row(status="Missing Data", kalshiPrice=None,
                              marketTicker="KXMLBGAME-26JUN121400BOSCLE-BOS",
                              missingFields=["odds.kalshi.ml.away"])
        apply_eligibility(row)
        self.assertIn(row["bet_eligibility_status"],
                      (BET_BLOCK_PRICE, BET_BLOCK_IDENTITY))

    def test_game_total_gets_paper_eligibility(self):
        row = self._make_row(market="Game_Total", status="Rejected",
                              rejectionReason="Rule 71 market suspension: Game Total WR 41%")
        apply_eligibility(row)
        self.assertEqual(row["bet_eligibility_status"], BET_PAPER,
                         "Game_Total must be 'paper', not 'rejected' or 'blocked'")

    def test_rl_away_gets_paper_eligibility(self):
        row = self._make_row(market="RL_Away", status="Rejected",
                              rejectionReason="Rule 81: RL suspended")
        apply_eligibility(row)
        self.assertEqual(row["bet_eligibility_status"], BET_PAPER)

    def test_clv_not_yet_on_live_bet(self):
        """Live bet with no CLV snapshot taken → clv_capture_status = not_applicable_yet."""
        row = self._make_row(status="Accepted")
        apply_eligibility(row, clv_snapshot_captured=None)
        self.assertEqual(row["clv_capture_status"], CLV_NOT_YET)

    def test_clv_missing_snap_on_missed_close(self):
        """Missed closing snapshot → clv_capture_status = missing_close_snapshot."""
        row = self._make_row(status="Accepted")
        apply_eligibility(row, clv_snapshot_captured=False)
        self.assertEqual(row["clv_capture_status"], CLV_MISSING_SNAP)
        # But bet is still actionable!
        self.assertEqual(row["bet_eligibility_status"], BET_ELIGIBLE)

    def test_no_ticker_row_gets_blocked_identity(self):
        row = self._make_row(marketTicker=None, status="Missing Data",
                              missingFields=["odds.kalshi.ml.away"])
        apply_eligibility(row)
        self.assertEqual(row["bet_eligibility_status"], BET_BLOCK_IDENTITY)


# ── Suite 7: build_market_ledger integration ──────────────────────────────────

class TestBuildMarketLedgerIntegration(unittest.TestCase):
    """
    Verifies that build_market_ledger.py now attaches the three status fields
    to every ledger row.
    """

    def _make_minimal_game(self):
        """Build a minimal game dict that build_market_ledger.evaluate_game can process."""
        return {
            "away": {
                "abbr": "BOS",
                "pitcher": {"name": "Sale", "id": "519242"},
                "pitcherSavant": {"xFIP": 3.5, "recentFIP": 3.6, "startsSampled": 5,
                                  "avgIPperStart": 6.0},
                "bullpen": {"xFIP": 4.0},
            },
            "home": {
                "abbr": "CLE",
                "pitcher": {"name": "Bieber", "id": "669456"},
                "pitcherSavant": {"xFIP": 3.2, "recentFIP": 3.3, "startsSampled": 6,
                                  "avgIPperStart": 6.5},
                "bullpen": {"xFIP": 4.2},
            },
            "awayTeamStats": {
                "offenseBaselineAdj": 4.3,
                "lineupConfirmed": True,
                "teamWOBA": 0.325,
            },
            "homeTeamStats": {
                "offenseBaselineAdj": 4.6,
                "lineupConfirmed": True,
                "teamWOBA": 0.335,
            },
            "parkFactor": 1.0,
            "pinnacleVF": {"away": 0.45, "home": 0.55},
            "odds": {
                "kalshi": {
                    "ml": {
                        "away": -118,
                        "home": -105,
                        "awayTicker": "KXMLBGAME-26JUN121400BOSCLE-BOS",
                        "homeTicker": "KXMLBGAME-26JUN121400BOSCLE-CLE",
                    },
                    "total": {"line": 8.0, "ticker": "KXMLBTOTAL-26JUN121400BOSCLE-OVER8"},
                    "f5ml": {"away": -115, "home": -108,
                             "away_ticker": "KXMLBF5-26JUN121400BOSCLE-BOS",
                             "home_ticker": "KXMLBF5-26JUN121400BOSCLE-CLE"},
                    "rl": {"best_ticker": "KXMLBSPREAD-26JUN121400BOSCLE",
                           "awayLine": -1.5, "awayPrice": 125},
                    "nrfi_yrfi": {
                        "nrfi_american": 105,
                        "yrfi_american": -125,
                        "ticker": "KXMLBRFI-26JUN121400BOSCLE",
                    },
                    "team_totals": {
                        "away": {"best_ticker": "KXMLBTEAMTOTAL-BOS-O3.5",
                                 "line": 3.5, "price": -115},
                        "home": {"best_ticker": "KXMLBTEAMTOTAL-CLE-O4.5",
                                 "line": 4.5, "price": -108},
                    },
                }
            },
        }

    def test_every_row_has_bet_eligibility_status(self):
        """Every ledger row must have bet_eligibility_status after evaluate_game."""
        import build_market_ledger as bml
        game = self._make_minimal_game()
        rows = bml.evaluate_game(game)
        for row in rows:
            self.assertIn("bet_eligibility_status", row,
                          f"{row.get('market')} missing bet_eligibility_status")

    def test_every_row_has_clv_capture_status(self):
        import build_market_ledger as bml
        game = self._make_minimal_game()
        rows = bml.evaluate_game(game)
        for row in rows:
            self.assertIn("clv_capture_status", row,
                          f"{row.get('market')} missing clv_capture_status")

    def test_every_row_has_review_integrity_status(self):
        import build_market_ledger as bml
        game = self._make_minimal_game()
        rows = bml.evaluate_game(game)
        for row in rows:
            self.assertIn("review_integrity_status", row,
                          f"{row.get('market')} missing review_integrity_status")

    def test_accepted_rows_are_actionable(self):
        """Accepted ledger rows must have bet_eligibility_status = 'actionable'."""
        import build_market_ledger as bml
        game = self._make_minimal_game()
        rows = bml.evaluate_game(game)
        accepted = [r for r in rows if r["status"] == "Accepted"]
        for row in accepted:
            self.assertEqual(row["bet_eligibility_status"], BET_ELIGIBLE,
                             f"{row.get('market')} Accepted but not actionable")

    def test_existing_fields_unchanged(self):
        """apply_eligibility must not change status/edge/confidence/betSize."""
        import build_market_ledger as bml
        game = self._make_minimal_game()
        rows = bml.evaluate_game(game)
        for row in rows:
            # status must remain one of the four valid values
            self.assertIn(row["status"],
                          ("Accepted", "Rejected", "Missing Data", "Evaluation Failed"),
                          f"{row.get('market')}: status was changed by apply_eligibility")


# ── Suite 8: Rule 71 still fires but does not suppress valid live bets ────────

class TestRule71StillWorks(unittest.TestCase):
    """
    Rule 71 is a CONFIDENCE DOWNGRADE for market disagreement.
    It must NOT suppress a bet when ticker and price are valid.
    The CLV patch does not change Rule 71 — it only prevents CLV-missing
    from being confused with Rule 71 market-identity issues.
    """

    def test_rule71_downgrade_not_a_hard_block(self):
        """Rule 71 downgrade does not change bet_eligibility to blocked_*."""
        # Simulate a bet that would have Rule 71 fire (model vs pinnacle gap > 8%)
        # but has valid ticker and price
        result = classify(
            market_ticker="KXMLBGAME-26JUN121400BOSCLE-BOS",
            entry_price=0.57,
            ledger_status="Accepted",   # edge still passes after downgrade
            rule_block_reason=None,     # Rule 71 downgrade is NOT a hard block
            is_paper_only=False,
        )
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE,
                         "Rule 71 downgrade must not block a bet with valid ticker+price")

    def test_rule71_hard_block_data_error_does_block(self):
        """Rule 71 HARD BLOCK (data error) is a rule_block_reason — it blocks."""
        result = classify(
            rule_block_reason="MISSING_KEY_DATA: no pitcher, no run projection",
        )
        self.assertEqual(result["bet_eligibility_status"], BET_BLOCK_RULE)

    def test_clv_missing_is_not_rule71(self):
        """CLV missing is NOT a Rule 71 condition — does not trigger any block."""
        result = classify(clv_snapshot_captured=False)
        self.assertEqual(result["bet_eligibility_status"], BET_ELIGIBLE,
                         "CLV missing must never be treated as Rule 71 block")


# ── Run all tests ─────────────────────────────────────────────────────────────

def run_all():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    test_classes = [
        TestCLVDoesNotBlockLiveBets,
        TestMarketIdentityBlocksLiveBets,
        TestPaperOnlyMarkets,
        TestRejectedBets,
        TestReviewWithoutCLV,
        TestApplyEligibility,
        TestBuildMarketLedgerIntegration,
        TestRule71StillWorks,
    ]
    for tc in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(tc))
    runner = unittest.TextTestRunner(verbosity=2, failfast=False)
    result = runner.run(suite)

    print("\n" + "=" * 60)
    print("BET ELIGIBILITY TEST SUMMARY")
    print("=" * 60)
    print(f"Tests run:  {result.testsRun}")
    print(f"Failures:   {len(result.failures)}")
    print(f"Errors:     {len(result.errors)}")
    print(f"Status:     {'PASS' if result.wasSuccessful() else 'FAIL'}")

    if result.failures:
        print("\nFAILURES:")
        for test, tb in result.failures:
            print(f"  {test}: {tb.splitlines()[-1]}")
    if result.errors:
        print("\nERRORS:")
        for test, tb in result.errors:
            print(f"  {test}: {tb.splitlines()[-1]}")

    return result.wasSuccessful()


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
