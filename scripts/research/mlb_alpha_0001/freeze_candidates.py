#!/usr/bin/env python3
"""MLB-ALPHA-0001: freeze the candidate leaderboard after A+B discovery.

DISCOVERY IS CLOSED. Of 584 evaluated Family-A cells (513 tested,
BH-FDR q=0.10) and the full Family-B structural audit, exactly ONE
positive cell survived FDR with the candidate floor met and no known
data-integrity dependency left unresolved:

  C01  BUY YES on KXMLBF5TOTAL (F5 combined-total ladder) contracts
       whose executable yesAsk is in [90, 99] cents at the LAST_PREGAME
       checkpoint.

Rules are exact and deterministic; the file refuses to overwrite an
existing freeze. Validation may be scored ONCE against these rules and
never tuned. RESEARCH ONLY.
"""

import hashlib
import json
import os
import sys

ART = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
OUT = os.path.join(ART, "frozen_candidates.json")

CANDIDATES = [
    {
        "candidateId": "MLB-ALPHA-0001-C01",
        "title": "F5-total deep-favorite YES at last pregame quote",
        "rule": {
            "universe": "KXMLBF5TOTAL contracts (marketFamily=inning_total)",
            "entryCheckpoint": "LAST_PREGAME",
            "side": "BUY_YES",
            "executablePrice": "archived yesAsk",
            "priceBandCentsInclusive": [90, 99],
            "settlement": "corrected >= semantics (rung N pays YES iff F5 total >= N)",
            "order": "USD 10 taker, whole contracts, Tier C realistic execution",
            "eligibility": "active pregame quote, SETTLED YES/NO after correction",
        },
        "economicRationale": (
            "Favorite-longshot bias on scalar ladders: near start, low F5-total "
            "rungs are near-certainties whose YES side stays quoted ~94-96c while "
            "true frequency is ~98-99%; retail flow prefers the cheap NO lottery "
            "side and the taker fee at 90+c is small (0.07*p*(1-p) < 0.7c)."
        ),
        "discovery": {
            "contracts": 276, "uniqueGames": 203, "dates": 17,
            "wins": 272, "losses": 4,
            "avgEntryPriceCents": 94.98,
            "grossROI": 0.0378, "feeOnlyROI": 0.0272, "netROI": 0.0331,
            "ci90": [0.0191, 0.045], "bootP": 0.0005,
            "fdrSurvivor": True, "maxDrawdown": -19.03,
            "dateConcentration": 0.106,
            "firstHalfNetPL": 43.28, "secondHalfNetPL": 44.90,
        },
        "knownRisks": [
            "FIRST_DAILY entries in the same band lose (-6.3%): the effect is "
            "checkpoint-specific; if the LAST_PREGAME capture is not actually "
            "executable at that quote, the edge may not survive live",
            "depends on the proven >= settlement-semantics correction",
            "win rate 98.6% -- a small number of adverse settlements flips the sign",
            "single-month, single-regime archive",
        ],
    },
]

REJECTED_NOTES = {
    "winning_margin BUY_YES 10-20c LAST_PREGAME": (
        "Original FDR survivor (+33.6%) was driven by KXMLBF5SPREAD rows whose "
        "settlements are corrupted (settled on full-game margins, defect #2). "
        "The clean full-game-only remainder is +13.0% with p=0.44 -- noise."
    ),
    "cheap-NO totals cells (+83..+128%)": (
        "Entirely an artifact of the >N vs >=N settlement-semantics defect; "
        "disappear after correction."
    ),
    "all Family B relative-value kinds": (
        "Every RV corrective trade is negative after spread+fees "
        "(e.g. F5TOTAL ladder inversions -38.7%, p=0.0005); no pure arbitrage "
        "exists post-fee anywhere in the archive."
    ),
    "other positive Family A cells": (
        "None passed BH-FDR q=0.10; best non-survivors: pitcher_strikeouts "
        "BUY_NO 30-40c (p=0.129), first_inning_run BUY_NO 50-60c (p=0.130)."
    ),
}


def main():
    if os.path.exists(OUT):
        print("REFUSING: %s already exists (candidates are frozen)" % OUT)
        return 1
    for c in CANDIDATES:
        c["ruleSha256"] = hashlib.sha256(
            json.dumps(c["rule"], sort_keys=True).encode()).hexdigest()
    doc = {
        "program": "MLB-ALPHA-0001",
        "discoveryClosed": True,
        "maxCandidatesAllowed": 10,
        "candidateCount": len(CANDIDATES),
        "candidates": CANDIDATES,
        "rejectedDuringDiscovery": REJECTED_NOTES,
        "validationPolicy": "score each candidate ONCE on the frozen validation split; losers are rejected, never tuned and retried",
        "validationGate": {
            "minIndependentGames": 40,
            "requirePositiveNetROI": True,
            "requireEffectDirectionPreserved": True,
        },
        "blindHoldout": "SEALED -- requires explicit CEO authorization",
    }
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("frozen %d candidate(s) -> %s" % (len(CANDIDATES), OUT))
    for c in CANDIDATES:
        print(" ", c["candidateId"], c["ruleSha256"][:16])
    return 0


if __name__ == "__main__":
    sys.exit(main())
