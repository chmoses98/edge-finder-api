#!/usr/bin/env python3
"""
tests/test_no_unverified_on_complete.py
========================================
Regression test: a post-slate review may NOT be marked 'complete'
(i.e., day_complete=True) while any logged bet for that date
has result='UNVERIFIED'.

Root cause: The June 11 post-slate review was considered done
while AZ@MIA YRFI and CHC@COL YRFI still had result='UNVERIFIED'.
These required fetching authoritative inning-1 play-by-play from
an external source (plaintextsports.com) before settling.

Any caller of build_june11_review() (or equivalent) must check
the returned 'complete' flag before presenting results as final.
"""

import unittest


# ── Minimal review helpers ───────────────────────────────────────────────────

class ReviewIncompleteError(Exception):
    """Raised when a review is accessed as complete while bets are UNVERIFIED."""
    pass


def build_review(bets: list, target_date: str) -> dict:
    """Produce a review for target_date from the full bets list."""
    day_bets = [b for b in bets
                if str(b.get("date", ""))[:10] == target_date
                or target_date in str(b.get("scheduledStartTime", ""))]

    real_bets, paper_bets = [], []
    real_pnl = paper_pnl = 0.0
    unverified = []

    for b in day_bets:
        result = b.get("result")
        pnl    = b.get("pnl") or 0.0
        is_paper = b.get("isPaper", True)

        if result == "UNVERIFIED":
            unverified.append(b.get("marketTicker", "?"))

        if is_paper:
            paper_bets.append(b)
            if result not in ("UNVERIFIED", None):
                paper_pnl += pnl
        else:
            real_bets.append(b)
            if result not in ("UNVERIFIED", None):
                real_pnl += pnl

    complete = len(unverified) == 0
    return {
        "real_bets":    real_bets,
        "paper_bets":   paper_bets,
        "real_pnl":     round(real_pnl, 2),
        "paper_pnl":    round(paper_pnl, 2),
        "unverified":   unverified,
        "complete":     complete,
    }


def assert_review_complete(review: dict) -> None:
    """
    Raise ReviewIncompleteError if any bet is still UNVERIFIED.
    Call this before presenting a review as final.
    """
    if not review["complete"]:
        tickers = ", ".join(review["unverified"])
        raise ReviewIncompleteError(
            f"Post-slate review is NOT complete: {len(review['unverified'])} bet(s) "
            f"still UNVERIFIED ({tickers}). Fetch authoritative inning/game data before "
            f"marking this day settled."
        )


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _bet(ticker, date, is_paper, result, pnl):
    return {"marketTicker": ticker, "date": date, "isPaper": is_paper,
            "result": result, "pnl": pnl}


JUNE11_SETTLED = [
    _bet("KXMLBRFI-26JUN111310STLNYM",           "2026-06-11", False, "WIN",   4.08),
    _bet("KXMLBRFI-26JUN111310MINDET",           "2026-06-11", False, "WIN",   3.77),
    _bet("KXMLBRFI-26JUN111310AZMIA",            "2026-06-11", False, "WIN",   3.85),  # settled
    _bet("KXMLBTEAMTOTAL-26JUN111310AZMIA-MIA5", "2026-06-11", False, "LOSS", -4.00),
    _bet("KXMLBF5-26JUN111410TEXKC-TEX",         "2026-06-11", False, "WIN",   6.48),
    _bet("KXMLBGAME-26JUN111310STLNYM-STL",      "2026-06-11", True,  "LOSS", -1.00),
    _bet("KXMLBRFI-26JUN111410TEXKC",            "2026-06-11", True,  "WIN",   0.82),
    _bet("KXMLBRFI-26JUN111510CHCCOL",           "2026-06-11", True,  "LOSS", -1.00),  # settled
    _bet("KXMLBF5-26JUN111905SEABAL-SEA",        "2026-06-11", False, "LOSS", -4.50),
    _bet("KXMLBRFI-26JUN111905SEABAL",           "2026-06-11", True,  "WIN",   1.13),
    _bet("KXMLBRFI-26JUN111940ATLCWS",           "2026-06-11", True,  "VOID",  0.00),
    _bet("KXMLBGAME-26JUN111940ATLCWS-ATL",      "2026-06-11", True,  "VOID",  0.00),
    _bet("KXMLBF5-26JUN111940ATLCWS-ATL",        "2026-06-11", True,  "VOID",  0.00),
]

JUNE11_WITH_UNVERIFIED = [
    b if b["marketTicker"] not in (
        "KXMLBRFI-26JUN111310AZMIA",
        "KXMLBRFI-26JUN111510CHCCOL"
    )
    else dict(b, result="UNVERIFIED", pnl=None)
    for b in JUNE11_SETTLED
]


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestNoUnverifiedOnComplete(unittest.TestCase):

    def test_assert_complete_raises_when_unverified(self):
        """assert_review_complete must raise when any bet is UNVERIFIED."""
        review = build_review(JUNE11_WITH_UNVERIFIED, "2026-06-11")
        self.assertFalse(review["complete"],
                         "review.complete must be False when UNVERIFIED bets exist")
        with self.assertRaises(ReviewIncompleteError) as ctx:
            assert_review_complete(review)
        msg = str(ctx.exception)
        self.assertIn("UNVERIFIED", msg)
        self.assertIn("KXMLBRFI-26JUN111310AZMIA", msg)
        self.assertIn("KXMLBRFI-26JUN111510CHCCOL", msg)

    def test_assert_complete_passes_when_all_settled(self):
        """assert_review_complete must not raise when every bet has a final result."""
        review = build_review(JUNE11_SETTLED, "2026-06-11")
        self.assertTrue(review["complete"],
                        "review.complete must be True when all bets are settled")
        # Should not raise
        assert_review_complete(review)

    def test_unverified_bets_excluded_from_pnl(self):
        """UNVERIFIED bets must not contribute to the P/L total."""
        review = build_review(JUNE11_WITH_UNVERIFIED, "2026-06-11")
        # AZ@MIA YRFI (real) and CHC@COL YRFI (paper) are UNVERIFIED → excluded
        # Real settled: MIN WIN +3.77, AZ TT LOSS -4.00, TEX F5 WIN +6.48, SEA F5 LOSS -4.50 = +1.75
        expected_real = round(3.77 - 4.00 + 6.48 - 4.50 + 4.08, 2)  # STL YRFI WIN still counted
        self.assertAlmostEqual(review["real_pnl"], expected_real, places=1,
                               msg=f"real_pnl should exclude UNVERIFIED AZ@MIA YRFI")

    def test_settled_review_real_pnl(self):
        """After settlement, real P/L must equal the sum of all 6 settled real bets."""
        review = build_review(JUNE11_SETTLED, "2026-06-11")
        expected = round(4.08 + 3.77 + 3.85 - 4.00 + 6.48 - 4.50, 2)  # +9.68
        self.assertAlmostEqual(review["real_pnl"], expected, places=1,
                               msg=f"Expected real P/L {expected}, got {review['real_pnl']}")

    def test_settled_review_paper_pnl(self):
        """After settlement, paper P/L: STL ML -1.00, TEX YRFI +0.82, CHC YRFI -1.00, SEA YRFI +1.13 = -0.05."""
        review = build_review(JUNE11_SETTLED, "2026-06-11")
        expected = round(-1.00 + 0.82 - 1.00 + 1.13, 2)  # -0.05
        self.assertAlmostEqual(review["paper_pnl"], expected, places=1,
                               msg=f"Expected paper P/L {expected}, got {review['paper_pnl']}")

    def test_unverified_tickers_listed(self):
        """unverified list must contain exactly the two previously-UNVERIFIED tickers."""
        review = build_review(JUNE11_WITH_UNVERIFIED, "2026-06-11")
        self.assertEqual(len(review["unverified"]), 2)
        self.assertIn("KXMLBRFI-26JUN111310AZMIA", review["unverified"])
        self.assertIn("KXMLBRFI-26JUN111510CHCCOL", review["unverified"])

    def test_void_bets_do_not_count_as_unverified(self):
        """VOID results (ATL@CWS postponement) must not appear in unverified list."""
        review = build_review(JUNE11_SETTLED, "2026-06-11")
        self.assertEqual(review["unverified"], [],
                         "VOID bets must not be in unverified list")

    def test_complete_flag_reflects_all_bets_settled(self):
        """complete=True iff zero UNVERIFIED bets."""
        settled = build_review(JUNE11_SETTLED, "2026-06-11")
        pending = build_review(JUNE11_WITH_UNVERIFIED, "2026-06-11")
        self.assertTrue(settled["complete"])
        self.assertFalse(pending["complete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
