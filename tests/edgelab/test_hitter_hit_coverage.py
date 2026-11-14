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

    def test_unresolved_tickers_never_have_empty_reason_manifest(self):
        report = reconcile_hitter_rows([
            row("KXMLBHIT-UNKNOWN", "NOT_SETTLED", None),
            row("KXMLBHIT-VOID", "VOID", None),
            row("KXMLBHIT-KNOWN", "SETTLEMENT_UNRESOLVED", None,
                reason="player_participation_unverified"),
        ])
        by_ticker = {r["marketTicker"]: r for r in report["notRecordedAsSettledTickers"]}
        fallback = "ARCHIVED_UNAVAILABLE_REASON_MISSING_REQUIRES_SOURCE_ADJUDICATION"
        self.assertEqual(by_ticker["KXMLBHIT-UNKNOWN"]["unavailableReasons"], [fallback])
        self.assertEqual(by_ticker["KXMLBHIT-VOID"]["unavailableReasons"], [fallback])
        self.assertEqual(by_ticker["KXMLBHIT-KNOWN"]["unavailableReasons"],
                         ["player_participation_unverified"])
        self.assertEqual(report["checkpointRows"], 3)
        self.assertEqual(report["uniqueMarketTickers"], 3)

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

    def test_timestamp_utc_day_and_fail_closed_invalid_time(self):
        report = reconcile_hitter_rows([
            row("A", when="2026-08-02T23:30:00-05:00"),
            row("B", when="2026-08-03T01:00:00Z"),
        ])
        self.assertEqual(report["checkpointRowsByCapturedDate"], {"2026-08-03": 2})
        for invalid in ("2026-99-99T00:00:00Z", "2026-08-02T12:00:00", 123):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                reconcile_hitter_rows([row("A", when=invalid)])

    def test_reject_doubleheader_game_identity_collision(self):
        first = row("SAME-TICKER")
        second = dict(row("SAME-TICKER", when="2026-08-02T13:00:00Z"), gameId=456)
        with self.assertRaisesRegex(ValueError, "Conflicting gameIds"):
            reconcile_hitter_rows([first, second])

    def test_first_last_observation_use_absolute_utc_time(self):
        # Lexical ISO sorting incorrectly puts the +02:00 capture after Z.
        report = reconcile_hitter_rows([
            row("SAME", when="2026-08-03T01:00:00+02:00"),
            row("SAME", when="2026-08-03T00:30:00Z"),
            row("NO-CAPTURE", when=None),
        ])
        by_ticker = {r["marketTicker"]: r for r in report["allTrackedTickers"]}
        self.assertEqual(by_ticker["SAME"]["observedAtFirst"], "2026-08-02T23:00:00Z")
        self.assertEqual(by_ticker["SAME"]["observedAtLast"], "2026-08-03T00:30:00Z")
        self.assertIsNone(by_ticker["NO-CAPTURE"]["observedAtFirst"])
        self.assertIsNone(by_ticker["NO-CAPTURE"]["observedAtLast"])
        self.assertEqual(report["checkpointRowsByCapturedDate"], {"2026-08-02": 1, "2026-08-03": 1})
        self.assertEqual(report["checkpointRowsMissingCapturedDate"], 1)

    def test_empty_hitter_sample(self):
        r = reconcile_hitter_rows([row("KXMLBTB-X", family="hitter_total_bases")])
        self.assertEqual(r["checkpointRows"], 0)
        self.assertEqual(r["recordedSettledTickers"], 0)


if __name__ == "__main__":
    unittest.main()
