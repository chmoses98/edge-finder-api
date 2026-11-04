"""Fail-closed exact-ticker and denominator tests for hitter result census."""
import unittest

from scripts.edgelab.audit_hitter_hit_coverage import reconcile_hitter_rows


def row(ticker, status="SETTLED", result="YES", linked=False, when="2026-08-02T12:00:00Z", reason=None, family="hitter_hits"):
    return {
        "marketTicker": ticker, "canonicalMarketFamily": family,
        "settlementStatus": status, "settlementResult": result,
        "modelEvaluationAvailable": linked, "capturedAt": when,
        "settlementUnavailableReason": reason, "gameId": 123,
    }


class HitterResultCoverageTests(unittest.TestCase):
    def test_rows_are_not_unique_tickers(self):
        data = [
            row("KXMLBHIT-A", linked=True),
            row("KXMLBHIT-A", linked=False, when="2026-08-02T13:00:00Z"),
            row("KXMLBHIT-B", "SETTLEMENT_UNRESOLVED", None, reason="player_participation_unverified"),
            row("KXMLBHIT-C", "NOT_SETTLED", None),
            row("KXMLBHIT-D", "VOID", None),
            row("KXMLBTB-OTHER", family="hitter_total_bases"),
        ]
        r = reconcile_hitter_rows(data, generated_at="fixed")
        self.assertEqual(r["checkpointRows"], 5)
        self.assertEqual(r["uniqueMarketTickers"], 4)
        self.assertEqual(r["checkpointRowsByCapturedDate"], {"2026-08-02": 5})
        self.assertEqual(r["checkpointRowsMissingCapturedDate"], 0)
        self.assertEqual(r["recordedSettledCheckpointRows"], 2)
        self.assertEqual(r["recordedSettledTickers"], 1)
        self.assertEqual(r["modelLinkedCheckpointRows"], 1)
        self.assertEqual(r["modelLinkedTickers"], 1)
        self.assertEqual(r["statusByCheckpointRow"]["SETTLEMENT_UNRESOLVED"], 1)
        self.assertEqual(len(r["notRecordedAsSettledTickers"]), 3)
        self.assertEqual(r["notRecordedAsSettledTickers"][1]["marketTicker"], "KXMLBHIT-C")

    def test_reject_contradictory_outcomes(self):
        with self.assertRaisesRegex(ValueError, "Contradictory"):
            reconcile_hitter_rows([row("X"), row("X", "SETTLED", "NO")])
        with self.assertRaisesRegex(ValueError, "Contradictory"):
            reconcile_hitter_rows([row("X"), row("X", "NOT_SETTLED", None)])

    def test_reject_unverified_results_and_identity(self):
        with self.assertRaisesRegex(ValueError, "missing YES/NO"):
            reconcile_hitter_rows([row("X", "SETTLED", None)])
        with self.assertRaisesRegex(ValueError, "asserted result"):
            reconcile_hitter_rows([row("X", "NOT_SETTLED", "YES")])
        with self.assertRaisesRegex(ValueError, "marketTicker"):
            reconcile_hitter_rows([row("", "SETTLED", "YES")])

    def test_missing_capture_timestamp_is_explicit_not_silently_dropped(self):
        r = reconcile_hitter_rows([row("KXMLBHIT-A", when=None), row("KXMLBHIT-B")])
        self.assertEqual(r["checkpointRows"], 2)
        self.assertEqual(r["checkpointRowsByCapturedDate"], {"2026-08-02": 1})
        self.assertEqual(r["checkpointRowsMissingCapturedDate"], 1)

    def test_empty_hitter_sample(self):
        r = reconcile_hitter_rows([row("KXMLBTB-X", family="hitter_total_bases")])
        self.assertEqual(r["checkpointRows"], 0)
        self.assertEqual(r["recordedSettledTickers"], 0)


if __name__ == "__main__":
    unittest.main()
