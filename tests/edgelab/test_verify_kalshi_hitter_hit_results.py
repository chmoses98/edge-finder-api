"""Exact-ticker exchange-native settlement verification: never infer outcomes."""
import json
import unittest

from scripts.edgelab.verify_kalshi_hitter_hit_results import (
    _fetch_batch, classify, verify_manifest,
)

AUTHORITY = "READ_ONLY_RESEARCH_DIAGNOSTIC_NO_KALSHI_SETTLEMENT_VERIFICATION"


def entry(ticker="KXMLBHIT-26AUG01NYNATL-NYNPLAYER1-1", recorded="YES", status="SETTLED"):
    return {
        "marketTicker": ticker, "checkpointRows": 3,
        "recordedResult": recorded, "settlementStatus": status,
    }


def manifest(rows):
    return {
        "family": "hitter_hits", "authority": AUTHORITY,
        "allTrackedTickers": rows, "uniqueMarketTickers": len(rows),
        "checkpointRows": sum(x["checkpointRows"] for x in rows),
        "generatedAt": "2026-08-20T20:00:00Z",
    }


class OfficialKalshiHitterTests(unittest.TestCase):
    def test_conflict_is_quarantined_not_auto_corrected(self):
        ticker = entry()["marketTicker"]
        result = verify_manifest(
            manifest([entry()]),
            fetch_batch=lambda _: ({ticker: {"ticker": ticker, "status": "finalized", "result": "no"}}, [{"sha256": "test"}]),
        )
        self.assertEqual(result["recordedResultConflicts"], 1)
        self.assertEqual(result["officialBinaryConfirmedTickers"], 1)
        self.assertEqual(result["byTicker"][0]["recordedResult"], "YES")
        self.assertEqual(result["byTicker"][0]["officialResult"], "no")

    def test_nonparticipant_scalar_and_missing_never_become_no(self):
        ticker = entry()["marketTicker"]
        r = classify(entry(status="SETTLEMENT_UNRESOLVED", recorded=None), {
            "ticker": ticker, "status": "finalized", "result": "scalar",
        })
        self.assertEqual(r["exchangeClassification"], "EXCHANGE_NON_BINARY_SCALAR")
        self.assertIsNone(r["recordedResult"])
        r = classify(entry(status="NOT_SETTLED", recorded=None), None)
        self.assertEqual(r["exchangeClassification"], "EXCHANGE_MARKET_NOT_RETURNED")
        r = classify(entry(status="NOT_SETTLED", recorded=None), {
            "ticker": ticker, "status": "active", "result": "yes",
        })
        self.assertEqual(r["exchangeClassification"], "EXCHANGE_NOT_FINAL")

    def test_reject_invalid_manifest_and_unrequested_ticker(self):
        e = entry()
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            verify_manifest({**manifest([e]), "uniqueMarketTickers": 2}, fetch_batch=lambda _: ({}, []))
        with self.assertRaisesRegex(ValueError, "unrequested"):
            verify_manifest(manifest([e]), fetch_batch=lambda _: ({"OTHER": {"ticker": "OTHER"}}, []))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            verify_manifest(manifest([e, e]), fetch_batch=lambda _: ({}, []))

    def test_reject_forged_checkpoint_denominators_before_exchange_query(self):
        e = entry()
        def no_fetch(_):
            self.fail("Untrusted denominator reached exchange query")
        for bad in (
            {**manifest([e]), "checkpointRows": 23295},
            manifest([{**e, "checkpointRows": 0}]),
            manifest([{**e, "checkpointRows": True}]),
        ):
            with self.assertRaisesRegex(ValueError, "checkpoint denominator"):
                verify_manifest(bad, fetch_batch=no_fetch)

    def test_bidirectional_official_coverage_gaps(self):
        a = entry("KXMLBHIT-26AUG01-A", recorded="YES", status="SETTLED")
        b = entry("KXMLBHIT-26AUG01-B", recorded=None, status="NOT_SETTLED")
        c = entry("KXMLBHIT-26AUG01-C", recorded="NO", status="SETTLED")
        official = {
            b["marketTicker"]: {"ticker": b["marketTicker"], "status": "finalized", "result": "yes"},
            c["marketTicker"]: {"ticker": c["marketTicker"], "status": "finalized", "result": "no"},
        }
        result = verify_manifest(manifest([a, b, c]), fetch_batch=lambda _: (official, []))
        self.assertEqual(result["recordedSettledWithoutOfficialBinary"], 1)
        self.assertEqual(result["officialBinaryMissingFromRecorded"], 1)
        self.assertEqual(result["recordedSettledWithoutOfficialBinaryTickers"], [a["marketTicker"]])
        self.assertEqual(result["officialBinaryMissingFromRecordedTickers"], [b["marketTicker"]])
        self.assertEqual(result["officialBinaryConfirmedTickers"], 2)
        self.assertEqual(result["recordedResultConflicts"], 0)

    def test_exact_response_batch_checksum_and_lookup(self):
        ticker = entry()["marketTicker"]
        raw = json.dumps({"markets": [{"ticker": ticker, "result": "yes", "status": "finalized"}], "cursor": ""}).encode()
        markets, proof = _fetch_batch([ticker], request_fetch=lambda _: raw)
        self.assertEqual(markets[ticker]["result"], "yes")
        self.assertEqual(len(proof), 1)
        with self.assertRaisesRegex(ValueError, "unrequested"):
            _fetch_batch([ticker], request_fetch=lambda _: json.dumps({
                "markets": [{"ticker": "KXMLBHIT-OTHER"}], "cursor": ""
            }).encode())


if __name__ == "__main__":
    unittest.main()
