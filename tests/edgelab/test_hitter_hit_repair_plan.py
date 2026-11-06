"""Independent synthetic tests: no postgame forecast reconstruction or settlement writes."""
import unittest
from scripts.edgelab.hitter_hit_repair_plan import build_plan

def sample():
    rows = [dict(marketTicker="KXMLBHIT-T"+str(i), checkpointRows=2,
                 settlementStatus=status, recordedResult=result, gameIds=[str(i)])
            for i, status, result in [(1, "SETTLED", "YES"), (2, "NOT_SETTLED", None),
                                      (3, "SETTLED", "NO"), (4, "VOID", None)]]
    classes = ["EXCHANGE_CONFIRMED_YES", "EXCHANGE_CONFIRMED_NO",
               "EXCHANGE_CONFIRMED_YES", "EXCHANGE_NON_BINARY_SCALAR"]
    results = [dict(marketTicker=row["marketTicker"], checkpointRows=2,
                    recordedStatus=row["settlementStatus"], recordedResult=row["recordedResult"],
                    exchangeClassification=cls, officialResult=result)
               for row, cls, result in zip(rows, classes, ["yes", "no", "yes", "scalar"])]
    return (dict(family="hitter_hits", authority="READ_ONLY_RESEARCH_DIAGNOSTIC_NO_KALSHI_SETTLEMENT_VERIFICATION",
                 allTrackedTickers=rows, uniqueMarketTickers=4, checkpointRows=8),
            dict(marketFamily="hitter_hits", authority="READ_ONLY_OFFICIAL_KALSHI_HISTORY_RECONCILIATION",
                 byTicker=results, uniqueTrackedTickers=4, checkpointRows=8,
                 exchangeClassificationCounts={"EXCHANGE_CONFIRMED_YES": 2,
                                               "EXCHANGE_CONFIRMED_NO": 1,
                                               "EXCHANGE_NON_BINARY_SCALAR": 1}))

class RepairPlanTests(unittest.TestCase):
    def test_categories_and_no_write(self):
        plan = build_plan(*sample())
        self.assertEqual(plan["classificationCounts"], {
            "BINARY_AGREES_NO_CHANGE": 1,
            "BINARY_MISSING_LOCAL_REQUIRES_RULE_REVIEW": 1,
            "CONFLICT_REQUIRES_RULE_REVIEW": 1,
            "SCALAR_OR_VOID_RULES_UNKNOWN": 1})
        self.assertEqual(len(plan["reviewQueue"]), 3)
        self.assertTrue(all(x["writeAuthorized"] is False for x in plan["reviewQueue"]))
        self.assertEqual(plan["historicalPITForecastsRecovered"], 0)
    def test_checkpoint_mismatch_rejected(self):
        a, b = sample()
        b["byTicker"][0]["checkpointRows"] = 3
        with self.assertRaises(ValueError): build_plan(a, b)
    def test_duplicate_ticker_rejected(self):
        a, b = sample()
        b["byTicker"][1]["marketTicker"] = b["byTicker"][0]["marketTicker"]
        with self.assertRaises(ValueError): build_plan(a, b)
    def test_false_binary_rejected(self):
        a, b = sample()
        b["byTicker"][1]["officialResult"] = "scalar"
        with self.assertRaises(ValueError): build_plan(a, b)
    def test_classification_totals_must_match_per_ticker_evidence(self):
        a, b = sample()
        b["exchangeClassificationCounts"]["EXCHANGE_CONFIRMED_YES"] = 1
        b["exchangeClassificationCounts"]["EXCHANGE_CONFIRMED_NO"] = 2
        with self.assertRaisesRegex(ValueError, "classification evidence drift"): build_plan(a, b)
    def test_invalid_checkpoint_count_rejected(self):
        a, b = sample()
        a["allTrackedTickers"][0]["checkpointRows"] = -1
        with self.assertRaisesRegex(ValueError, "Invalid checkpoint count"): build_plan(a, b)
    def test_baseline_drift_rejected(self):
        a, b = sample()
        a["checkpointRows"] = b["checkpointRows"] = 23296
        with self.assertRaises(ValueError): build_plan(a, b)

if __name__ == "__main__":
    unittest.main()
