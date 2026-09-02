#!/usr/bin/env python3
"""MLB-ALPHA-0001 Section D: recompute the ALREADY-OPENED validation
inference with the repaired clustered test.

This is not a second peek: validation was opened once under the frozen
C01 rule and its record stands unchanged in validation_results.json.
The identical rows are re-analysed here only because the statistic
originally reported alongside them was not a valid p-value. The frozen
candidate rule is untouched, and the blind holdout is not read.
"""

import gzip
import json
import os
import sys

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from scripts.research.mlb_alpha_0001.family_a_discovery import (  # noqa: E402
    row_side_econ, SIZE_INFLATION, BOOT)
from scripts.research.mlb_alpha_0001.inference import clustered_roi_inference  # noqa: E402

SEED = 20260903


def c01_rows(split):
    path = os.path.join(ART, "entry_rows_%s.jsonl.gz" % split)
    out = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            if not r["marketTicker"].startswith("KXMLBF5TOTAL-"):
                continue
            if r["entryCheckpoint"] != "LAST_PREGAME":
                continue
            if r["settlementResult"] not in ("YES", "NO"):
                continue
            ya = r.get("yesExecAsk")
            if ya is None or not (90 <= ya <= 99):
                continue
            e = row_side_econ(ya, r["settlementResult"] == "YES")
            if e is not None:
                out.append((r, e))
    return out


def analyse(rows, rng):
    from collections import defaultdict
    net_g, cash_g = defaultdict(float), defaultdict(float)
    dates = set()
    for r, e in rows:
        g = r["gameDate"] + ":" + r["eventTicker"].split("-", 1)[1]
        net_g[g] += e["netPL"]
        cash_g[g] += e["cash"]
        dates.add(r["gameDate"])
    inf = clustered_roi_inference(net_g, cash_g, rng, B=BOOT)
    inf["pConservative"] = min(1.0, round(inf["pPrimary"] * SIZE_INFLATION, 6))
    inf.update({
        "contracts": len(rows), "uniqueGames": len(net_g), "dates": len(dates),
        "wins": sum(1 for r, _ in rows if r["settlementResult"] == "YES"),
        "losses": sum(1 for r, _ in rows if r["settlementResult"] == "NO"),
        "netPL": round(sum(e["netPL"] for _, e in rows), 2),
    })
    return inf


def main():
    rng = np.random.default_rng(SEED)
    doc = {
        "program": "MLB-ALPHA-0001",
        "section": "D_corrected_inference_for_frozen_candidate",
        "candidateId": "MLB-ALPHA-0001-C01",
        "note": ("Re-analysis of already-opened splits with the repaired "
                 "null-centered clustered test. The frozen rule and the "
                 "original one-shot validation record are unchanged. The "
                 "blind holdout is NOT read."),
        "withdrawnStatistic": ("2*min(P(ROI*<=0),P(ROI*>=0)) over an unshifted "
                               "cluster bootstrap -- never a hypothesis test"),
        "splits": {},
    }
    for split in ("discovery", "validation"):
        doc["splits"][split] = analyse(c01_rows(split), rng)
        print(split, json.dumps(doc["splits"][split], indent=1, sort_keys=True))
    path = os.path.join(ART, "c01_corrected_inference.json")
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", path)


if __name__ == "__main__":
    sys.exit(main())
