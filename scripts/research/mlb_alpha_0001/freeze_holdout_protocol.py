#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section H: freeze the BLIND HOLDOUT protocol for
MLB-ALPHA-0001-C01-PIT, BEFORE any holdout outcome is read.

Nothing in this file reads holdout market data, holdout settlements, or
holdout outcomes. It only records, in advance and immutably:
  * the exact rule (by its already-frozen sha256);
  * the holdout dates;
  * the sample floor and the three possible verdicts;
  * which quantities are reported but explicitly NOT pass criteria.

Refuses to overwrite an existing freeze.

WHAT THE HOLDOUT IS FOR: a single screening question --
"does the PIT-executable translation replicate well enough to advance to
PROSPECTIVE SHADOW?" It is NOT a production-approval test. A positive
holdout authorizes no wager.
"""

import hashlib
import json
import os
import sys

ART = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
OUT = os.path.join(ART, "frozen_holdout_protocol.json")

C01_PIT_RULE_SHA256 = (
    "882f16d8330af1af12aec928a561302bfe81de6a5e5716a3a7fa352bc048376b")

PROTOCOL = {
    "program": "MLB-ALPHA-0001",
    "candidateId": "MLB-ALPHA-0001-C01-PIT",
    "candidateRuleSha256": C01_PIT_RULE_SHA256,
    "purpose": (
        "SCREENING GATE ONLY: does the PIT-executable translation replicate "
        "well enough to advance to PROSPECTIVE SHADOW? This is NOT a "
        "production-approval test, and a positive result authorizes no wager."),
    "rule": {
        "universe": "KXMLBF5TOTAL contracts (marketFamily=inning_total)",
        "side": "BUY_YES",
        "priceBandCentsInclusive": [90, 99],
        "entryTrigger": ("FIRST qualifying ACTIVE quote whose capture time falls "
                         "in the window [T-60 minutes, T-0) before scheduled "
                         "first pitch"),
        "settlement": "corrected AT_LEAST_N semantics (rung N pays YES iff F5 total >= N)",
        "order": "USD 10 taker, whole contracts, Tier C realistic execution",
        "executablePrice": "archived yesAsk (top of book)",
    },
    "holdoutDates": [
        "2026-08-26", "2026-08-27", "2026-08-28",
        "2026-08-29", "2026-08-30", "2026-08-31",
    ],
    "sampleFloor": {"independentGames": 30, "independentDates": 4},
    "verdicts": {
        "INCONCLUSIVE": [
            "fewer than 30 independent MLB games, OR",
            "fewer than 4 independent dates",
        ],
        "REPLICATED_FOR_PROSPECTIVE_SHADOW": [
            "sample floor met",
            "post-fee net ROI > 0",
            "no data-integrity failure",
            "no settlement-semantic mismatch",
            "no single date contributes > 50% of absolute total P/L",
            "results not dependent on ambiguous game identity",
        ],
        "FAILED_TO_REPLICATE": [
            "sample floor met AND post-fee net ROI <= 0, OR",
            "a material data-integrity failure invalidates the strategy",
        ],
    },
    "reportedButNotPassCriteria": [
        "game-clustered confidence interval",
        "null-centered cluster-bootstrap p-value",
        "wild-cluster-bootstrap p-value",
        "executable CLV",
        "fair-mid CLV",
        "win rate",
        "maximum drawdown",
        "date-by-date ROI",
    ],
    "rationaleForNoStatisticalPassCriterion": (
        "Six dates are a screening gate into a larger prospective shadow, not "
        "evidence sufficient for production. Adding a significance hurdle here "
        "would invite reading a small sample as confirmation; the statistics "
        "are reported for context and for designing the shadow, never as a "
        "gate."),
    "authorizationRequired": {
        "flagFile": "data/edgelab/research_artifacts/mlb_alpha_0001/HOLDOUT_AUTHORIZATION.json",
        "note": ("The holdout scorer refuses to run unless this file exists AND "
                 "contains the exact candidate rule sha256 above. It is NOT "
                 "created by this session."),
    },
    "frozenBeforeAnyHoldoutOutcomeRead": True,
    "holdoutStatus": "SEALED -- NOT AUTHORIZED",
}


def main():
    if os.path.exists(OUT):
        print("REFUSING: %s already exists (protocol is frozen)" % OUT)
        return 1
    body = json.dumps(PROTOCOL, indent=2, sort_keys=True)
    digest = hashlib.sha256(body.encode()).hexdigest()
    doc = dict(PROTOCOL)
    doc["protocolSha256"] = digest
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("frozen:", OUT)
    print("protocolSha256:", digest)
    print("candidateRuleSha256:", C01_PIT_RULE_SHA256)
    return 0


if __name__ == "__main__":
    sys.exit(main())
