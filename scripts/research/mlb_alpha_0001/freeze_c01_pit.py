#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section C: freeze a PIT-EXECUTABLE translation of C01.

C01 as frozen enters at LAST_PREGAME, which is selected EX POST (the
latest archived active quote before first pitch). This file freezes a
version that could actually fire live.

HOW THE ENTRY POINT WAS CHOSEN -- capture mechanics ONLY, never outcomes.
The selection rule was fixed before any economics of any window were
computed:

  (1) Prefer the standardized checkpoint T_MINUS_5, per the mission.
      REJECTED: c01_execution_audit.json measures T_MINUS_5 present for
      only 9.5% of C01-eligible contracts (T_MINUS_15 12.8%,
      T_MINUS_30 12.5%). GitHub Actions cron cannot hit an exact minute,
      so these labels are sparse by construction -- inadequate coverage.

  (2) Falling back to a fixed deterministic window, predeclare an
      OPERATIONAL VIABILITY FLOOR: a live rule that fires on fewer than
      half of otherwise-eligible contracts is not operationally viable.
      Then take the MOST PROXIMATE window clearing that floor, because
      universe CLV (Section A, characterization -- not C01 outcomes)
      shows executable spreads tighten into first pitch, so later entry
      has strictly better execution.

      Measured coverage, C01-eligible contracts (discovery+validation):
          [T-15, T-0)  17.8%   below floor
          [T-30, T-0)  33.8%   below floor
          [T-60, T-0)  56.9%   CLEARS floor  <-- most proximate qualifier
          [T-90, T-0)  66.4%   clears, less proximate
          [T-120,T-0)  74.2%   clears, least proximate

  (3) FIRST (not last) qualifying quote inside the window: acting on the
      first observation you see requires no knowledge of any future
      quote, which is what makes it point-in-time executable at all.

No x/y grid was searched against profitability; the floor and the
"most proximate qualifier" tie-break were both fixed in advance, and the
window endpoints are round operational numbers, never fitted cutpoints.

The core market rule is otherwise IDENTICAL to C01.
"""

import hashlib
import json
import os
import sys

ART = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
OUT = os.path.join(ART, "frozen_candidate_c01_pit.json")

CANDIDATE = {
    "candidateId": "MLB-ALPHA-0001-C01-PIT",
    "translationOf": "MLB-ALPHA-0001-C01",
    "title": "F5-total deep-favorite YES, point-in-time executable window entry",
    "rule": {
        "universe": "KXMLBF5TOTAL contracts (marketFamily=inning_total)",
        "entryTrigger": (
            "the FIRST observation of the contract whose capture time falls "
            "inside the window [T-60 minutes, T-0) relative to scheduled "
            "first pitch, with marketStatus active and yesAsk in band"),
        "entryWindowMinutesBeforeStart": {"openAt": 60, "closeAt": 0},
        "entrySelection": "FIRST_QUALIFYING_QUOTE_IN_WINDOW",
        "side": "BUY_YES",
        "executablePrice": "archived yesAsk (top of book)",
        "priceBandCentsInclusive": [90, 99],
        "settlement": "corrected >= semantics (rung N pays YES iff F5 total >= N)",
        "order": "USD 10 taker, whole contracts, Tier C realistic execution",
        "eligibility": "active pregame quote in window, SETTLED YES/NO after correction",
    },
    "pointInTimeSafety": {
        "usesFutureQuotes": False,
        "usesFutureOutcomes": False,
        "rationale": ("entry fires on the first qualifying observation inside a "
                      "clock-defined window; no later quote and no settlement "
                      "information is required to decide"),
    },
    "executionCaveat": {
        "claim": "TOP_OF_BOOK_PRICE_OBSERVED",
        "tenDollarFillProven": False,
        "historicalCapacity": "UNKNOWN/UNVERIFIED -- no ask size or depth is archived",
    },
    "selectionProvenance": {
        "criterion": "capture coverage + proximity to first pitch, fixed in advance",
        "outcomeDataUsed": False,
        "coverageMeasured": {
            "T_MINUS_5": 0.095, "T_MINUS_15": 0.128, "T_MINUS_30": 0.125,
            "window_T60_T0": 0.569, "window_T90_T0": 0.664,
            "window_T120_T0": 0.742,
        },
        "operationalViabilityFloor": 0.50,
    },
    "scoringPolicy": {
        "discovery": "may be scored once as a translation sanity check",
        "validation": "NOT scored in this session -- validation was already "
                      "opened for C01; re-using it for a second rule would be a "
                      "new peek and requires explicit authorization",
        "blindHoldout": "SEALED -- requires explicit CEO authorization",
    },
}


def main():
    if os.path.exists(OUT):
        print("REFUSING: %s already exists (frozen)" % OUT)
        return 1
    CANDIDATE["ruleSha256"] = hashlib.sha256(
        json.dumps(CANDIDATE["rule"], sort_keys=True).encode()).hexdigest()
    doc = {"program": "MLB-ALPHA-0001", "frozenAt": "2026-09-01",
           "candidate": CANDIDATE,
           "blindHoldout": "SEALED -- NOT AUTHORIZED"}
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("frozen:", OUT)
    print("ruleSha256:", CANDIDATE["ruleSha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
