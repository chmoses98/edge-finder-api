"""Independent synthetic raw-to-standardized funnel denominator tests."""
import unittest
from scripts.edgelab.audit_hitter_hit_funnel import summarize

class FunnelTests(unittest.TestCase):
    def test_different_denominators_and_utc_dates(self):
        raw = [
            {"marketTicker": "KXMLBHIT-A", "marketFamily": "hitter_hits", "capturedAt": "2026-08-02T23:30:00-05:00", "checkpoint": "INTERMEDIATE"},
            {"marketTicker": "KXMLBHIT-A", "marketFamily": "hitter_hits", "capturedAt": "2026-08-03T05:00:00Z", "checkpoint": "T_MINUS_90"},
            {"marketTicker": "KXMLBHIT-B", "marketFamily": "hitter_hits", "capturedAt": "2026-08-03T06:00:00Z", "checkpoint": "INTERMEDIATE"},
        ]
        # Use actual family canonicalization in the production function; synthetic
        # rows may be classified UNKNOWN and must never be silently called hits.
        result = summarize(raw, [{"canonicalMarketFamily": "hitter_hits", "marketTicker": "KXMLBHIT-A", "capturedAt": "2026-08-03T05:00:00Z"}], ["2026-08-03"])
        self.assertEqual(result["rawObservationRows"], 3)
        self.assertEqual(result["standardizedHitterHitRows"], 1)
        self.assertEqual(result["byCaptureUTCDate"]["2026-08-03"]["rawAll"], 3)
        self.assertEqual(sum(result["rawFamilyClassification"].values()), 3)

    def test_unique_tickers_do_not_conflate_repeated_checkpoints(self):
        raw = [
            {"marketTicker": "KXMLBHIT-A", "marketFamily": "hitter_hits", "capturedAt": "2026-08-03T05:00:00Z"},
            {"marketTicker": "KXMLBHIT-A", "marketFamily": "hitter_hits", "capturedAt": "2026-08-03T06:00:00Z"},
        ]
        standardized = [
            {"marketTicker": "KXMLBHIT-A", "canonicalMarketFamily": "hitter_hits", "capturedAt": "2026-08-03T05:00:00Z"},
            {"marketTicker": "KXMLBHIT-A", "canonicalMarketFamily": "hitter_hits", "capturedAt": "2026-08-03T06:00:00Z"},
        ]
        result = summarize(raw, standardized, ["2026-08-03"])
        self.assertEqual(result["standardizedHitterHitRows"], 2)
        self.assertEqual(result["standardizedHitterHitCheckpointLabels"], {"None": 2})
        self.assertEqual(result["historicalReportCheckpointRows"], 23295)
        self.assertEqual(result["historicalReportUnreconstructedRows"], 23293)
        self.assertEqual(result["standardizedHitterHitUniqueTickers"], 1)
        self.assertEqual(result["rawObservationRows"], 2)
        self.assertEqual(result["rawHitterTickersWithoutStandardizedCheckpoint"], 0)

    def test_invalid_capture_time_is_visible(self):
        result = summarize([{"marketTicker": "X", "capturedAt": "not-a-date"}], [], ["2026-08-01"])
        self.assertEqual(result["byCaptureUTCDate"]["MISSING_OR_INVALID"]["rawAll"], 1)
        self.assertEqual(result["standardizedHitterHitRows"], 0)

if __name__ == "__main__":
    unittest.main()
