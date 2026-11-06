#!/usr/bin/env python3
"""Fail-closed, read-only exact-ticker reconciliation plan; NEVER writes settlements."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

BASELINE = 23295
BINARY = {"EXCHANGE_CONFIRMED_YES", "EXCHANGE_CONFIRMED_NO"}

def build_plan(coverage, exchange):
    if coverage.get("family") != "hitter_hits" or exchange.get("marketFamily") != "hitter_hits":
        raise ValueError("Wrong family")
    if coverage.get("authority") != "READ_ONLY_RESEARCH_DIAGNOSTIC_NO_KALSHI_SETTLEMENT_VERIFICATION":
        raise ValueError("Invalid coverage provenance")
    if exchange.get("authority") != "READ_ONLY_OFFICIAL_KALSHI_HISTORY_RECONCILIATION":
        raise ValueError("Invalid exchange provenance")
    def index(rows):
        if not isinstance(rows, list):
            raise ValueError("Missing ticker inventory")
        result = {}
        for row in rows:
            ticker = row.get("marketTicker") if isinstance(row, dict) else None
            if not isinstance(ticker, str) or not ticker.startswith("KXMLBHIT-") or ticker in result:
                raise ValueError("Invalid or duplicate ticker")
            result[ticker] = row
        return result
    local = index(coverage.get("allTrackedTickers"))
    official = index(exchange.get("byTicker"))
    if local.keys() != official.keys() or len(local) != coverage.get("uniqueMarketTickers") or len(official) != exchange.get("uniqueTrackedTickers"):
        raise ValueError("Ticker denominator drift")
    checkpoints = sum(row["checkpointRows"] for row in local.values())
    if checkpoints != coverage.get("checkpointRows") or checkpoints != exchange.get("checkpointRows") or checkpoints > BASELINE:
        raise ValueError("Checkpoint denominator drift")
    if sum(exchange.get("exchangeClassificationCounts", {}).values()) != len(official):
        raise ValueError("Exchange classification denominator drift")
    counts, queue = Counter(), []
    for ticker in sorted(local):
        a, b = local[ticker], official[ticker]
        if (a["checkpointRows"] != b["checkpointRows"] or
            a["settlementStatus"] != b["recordedStatus"] or
            a.get("recordedResult") != b.get("recordedResult")):
            raise ValueError("Inconsistent ticker evidence: " + ticker)
        cls = b["exchangeClassification"]
        outcome = b.get("officialResult")
        recorded = a["settlementStatus"] == "SETTLED"
        if cls in BINARY and outcome not in ("yes", "no"):
            raise ValueError("Contradictory official binary evidence")
        if cls in BINARY and recorded and a.get("recordedResult") != outcome.upper():
            category = "CONFLICT_REQUIRES_RULE_REVIEW"
        elif cls in BINARY and not recorded:
            category = "BINARY_MISSING_LOCAL_REQUIRES_RULE_REVIEW"
        elif cls in BINARY:
            category = "BINARY_AGREES_NO_CHANGE"
        elif recorded:
            category = "LOCAL_SETTLED_UNCONFIRMED_BY_EXCHANGE"
        elif cls == "EXCHANGE_NON_BINARY_SCALAR":
            category = "SCALAR_OR_VOID_RULES_UNKNOWN"
        elif cls == "EXCHANGE_NOT_FINAL":
            category = "EXCHANGE_NOT_FINAL"
        elif cls == "EXCHANGE_MARKET_NOT_RETURNED":
            category = "EXCHANGE_MARKET_NOT_RETURNED"
        else:
            category = "EXCHANGE_RESULT_UNKNOWN"
        counts[category] += 1
        if category != "BINARY_AGREES_NO_CHANGE":
            queue.append({"marketTicker": ticker, "checkpointRows": a["checkpointRows"],
                          "category": category, "recordedStatus": a["settlementStatus"],
                          "recordedResult": a.get("recordedResult"),
                          "exchangeResult": outcome, "exchangeStatus": b.get("officialStatus"),
                          "exchangeRulesPrimary": b.get("exchangeRulesPrimary"),
                          "exchangeRulesSecondary": b.get("exchangeRulesSecondary"),
                          "gameIds": a.get("gameIds", []), "writeAuthorized": False,
                          "nextEvidence": "Verify exact contract rules, player/game identity and participation"})
    if sum(counts.values()) != len(local):
        raise AssertionError("Classification drift")
    return {"schemaVersion": 1, "authority": "READ_ONLY_REPAIR_PLAN_NO_SETTLEMENT_WRITES",
            "marketFamily": "hitter_hits", "originalCheckpointDenominator": BASELINE,
            "reconstructedCheckpointRows": checkpoints,
            "unreconstructedCheckpointRows": BASELINE - checkpoints,
            "uniqueTrackedTickers": len(local), "classificationCounts": dict(sorted(counts.items())),
            "reviewQueue": queue, "historicalPITForecastsRecovered": 0,
            "caveats": ["No settlement writes or historical forecast reconstruction.",
                        "Exchange payout does not independently prove DNP/VOID source rules.",
                        "Missing checkpoint identities cannot be inferred from reconstructed archive."]}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--coverage", required=True)
    p.add_argument("--exchange", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    paths = [Path(args.coverage), Path(args.exchange)]
    report = build_plan(*(json.loads(path.read_text()) for path in paths))
    report["inputSha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: report[k] for k in ("uniqueTrackedTickers", "reconstructedCheckpointRows", "unreconstructedCheckpointRows", "classificationCounts")}, indent=2))

if __name__ == "__main__":
    main()
