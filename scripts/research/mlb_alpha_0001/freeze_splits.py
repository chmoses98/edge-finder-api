#!/usr/bin/env python3
"""MLB-ALPHA-0001 Mission 3: freeze the calendar research splits.

Splits are chosen from COVERAGE FACTS ONLY (settlement availability,
checkpoint-label availability) -- never from strategy outcomes. Boundaries
are pure calendar order: earliest ~60% of reliable dates -> DISCOVERY,
next ~20% -> VALIDATION, final ~20% -> BLIND HOLDOUT.

Writes data/edgelab/research_artifacts/mlb_alpha_0001/frozen_splits.json
with sha256 hashes of each date list. Refuses to overwrite an existing
freeze (immutable once written).

RESEARCH ONLY.
"""

import hashlib
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
OUT = os.path.join(
    REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001", "frozen_splits.json"
)

# Coverage facts from coverage_manifest.json (Mission 1):
#  - observation archive: 2026-08-01 .. 2026-09-01
#  - settlement store:    2026-08-02 .. 2026-08-31
#  - 2026-08-01: observed but 0% of its tickers ever settled -> excluded
#  - 2026-08-17: full settlement hole (0 settlement rows)    -> excluded
#  - 2026-09-01: in-flight (unsettled, capture ongoing)      -> excluded
#  - 2026-08-02/03: usable but checkpoint labels are null (derived
#    checkpoints only) -- flagged, not excluded
RELIABLE_DATES = [
    "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06",
    "2026-08-07", "2026-08-08", "2026-08-09", "2026-08-10", "2026-08-11",
    "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15", "2026-08-16",
    "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22",
    "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27",
    "2026-08-28", "2026-08-29", "2026-08-30", "2026-08-31",
]

DISCOVERY = RELIABLE_DATES[:17]   # 2026-08-02 .. 2026-08-19 (ex 08-17): 58.6%
VALIDATION = RELIABLE_DATES[17:23]  # 2026-08-20 .. 2026-08-25: 20.7%
HOLDOUT = RELIABLE_DATES[23:]     # 2026-08-26 .. 2026-08-31: 20.7%


def h(dates):
    return hashlib.sha256(",".join(dates).encode()).hexdigest()


def main():
    if os.path.exists(OUT):
        print("REFUSING: %s already exists (splits are immutable)" % OUT)
        return 1
    assert len(DISCOVERY) + len(VALIDATION) + len(HOLDOUT) == len(RELIABLE_DATES)
    assert not (set(DISCOVERY) & set(VALIDATION)), "overlap"
    assert not (set(VALIDATION) & set(HOLDOUT)), "overlap"
    assert not (set(DISCOVERY) & set(HOLDOUT)), "overlap"
    assert max(DISCOVERY) < min(VALIDATION) < min(HOLDOUT), "not chronological"

    doc = {
        "program": "MLB-ALPHA-0001",
        "frozenBy": "scripts/research/mlb_alpha_0001/freeze_splits.py",
        "frozenBeforeAnyOutcomeScoring": True,
        "boundaryRationale": (
            "Pure calendar 60/20/20 over the 29 reliable settled dates. "
            "Excluded: 2026-08-01 (no settlements), 2026-08-17 (settlement "
            "hole), 2026-09-01 (in-flight). Boundaries chosen from coverage "
            "facts only, never from profitability."
        ),
        "excludedDates": {
            "2026-08-01": "observed but never settled",
            "2026-08-17": "full settlement hole (0 settlement rows for its games)",
            "2026-09-01": "in-flight capture, unsettled",
        },
        "degradedLabelDates": {
            "2026-08-02": "checkpoint labels null; derived checkpoints only",
            "2026-08-03": "checkpoint labels null; derived checkpoints only; partial capture (8 games)",
        },
        "discovery": {"dates": DISCOVERY, "sha256": h(DISCOVERY)},
        "validation": {"dates": VALIDATION, "sha256": h(VALIDATION)},
        "blindHoldout": {
            "dates": HOLDOUT,
            "sha256": h(HOLDOUT),
            "sealed": True,
            "sealNote": (
                "HOLDOUT must not be loaded, scored, or summarized by any "
                "discovery/validation script. Opening it requires explicit "
                "human (CEO) authorization recorded in the charter."
            ),
        },
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("frozen:", OUT)
    print("discovery %d dates, validation %d, holdout %d (sealed)" % (
        len(DISCOVERY), len(VALIDATION), len(HOLDOUT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
