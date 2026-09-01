#!/usr/bin/env python3
"""MLB-ALPHA-0001: score the frozen candidates ONCE on the VALIDATION split.

Refuses to run when validation results already exist (one-shot rule) or
when the candidate freeze is missing. Never touches the blind holdout.
RESEARCH ONLY.
"""

import gzip
import json
import os
import sys
from collections import defaultdict

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")

from scripts.research.mlb_alpha_0001.family_a_discovery import row_side_econ  # noqa: E402

BOOT = 2000
SEED = 20260903


def score_c01(rows):
    sel = []
    for r in rows:
        if r["marketFamily"] != "inning_total":
            continue
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
        if e is None:
            continue
        sel.append((r, e))
    games = defaultdict(float)
    cash_g = defaultdict(float)
    dates = set()
    for r, e in sel:
        g = r["gameDate"] + ":" + r["eventTicker"].split("-", 1)[1]
        games[g] += e["netPL"]
        cash_g[g] += e["cash"]
        dates.add(r["gameDate"])
    total_net = sum(e["netPL"] for _, e in sel)
    total_cash = sum(e["cash"] for _, e in sel)
    total_gross = sum(e["grossPL"] for _, e in sel)
    out = {
        "contracts": len(sel),
        "uniqueGames": len(games),
        "dates": len(dates),
        "wins": sum(1 for r, _ in sel if r["settlementResult"] == "YES"),
        "losses": sum(1 for r, _ in sel if r["settlementResult"] == "NO"),
        "avgEntryPriceCents": round(
            float(np.mean([r["yesExecAsk"] for r, _ in sel])), 2) if sel else None,
        "grossROI": round(total_gross / (10.0 * len(sel)), 4) if sel else None,
        "netPL": round(total_net, 2),
        "netROI": round(total_net / total_cash, 4) if total_cash else None,
    }
    if len(games) >= 1:
        rng = np.random.default_rng(SEED)
        g = list(games)
        net = np.array([games[x] for x in g])
        cash = np.array([cash_g[x] for x in g])
        idx = rng.integers(0, len(g), size=(BOOT, len(g)))
        net_s = net[idx].sum(axis=1)
        cash_s = cash[idx].sum(axis=1)
        rois = np.where(cash_s > 0, net_s / np.maximum(cash_s, 1e-9), 0.0)
        lo, hi = np.percentile(rois, [5, 95])
        p = 2 * min(float((rois <= 0).mean()), float((rois >= 0).mean()))
        out.update({"ci90": [round(float(lo), 4), round(float(hi), 4)],
                    "bootP": round(max(p, 1.0 / BOOT), 5)})
    return out


def main():
    out_path = os.path.join(ART, "validation_results.json")
    if os.path.exists(out_path):
        print("REFUSING: validation already scored (one-shot rule):", out_path)
        return 1
    with open(os.path.join(ART, "frozen_candidates.json")) as fh:
        frozen = json.load(fh)
    rows = []
    with gzip.open(os.path.join(ART, "entry_rows_validation.jsonl.gz"), "rt") as fh:
        for line in fh:
            rows.append(json.loads(line))

    results = []
    for c in frozen["candidates"]:
        assert c["candidateId"] == "MLB-ALPHA-0001-C01"
        val = score_c01(rows)
        gate = frozen["validationGate"]
        verdict_pass = (
            val["uniqueGames"] >= gate["minIndependentGames"]
            and (val["netROI"] or 0) > 0
        )
        results.append({
            "candidateId": c["candidateId"],
            "ruleSha256": c["ruleSha256"],
            "discovery": c["discovery"],
            "validation": val,
            "gate": gate,
            "verdict": "PASS" if verdict_pass else "FAIL",
        })
        print(c["candidateId"], "->", results[-1]["verdict"])
        print(json.dumps(val, indent=1, sort_keys=True))

    doc = {
        "program": "MLB-ALPHA-0001",
        "split": "validation",
        "scoredOnce": True,
        "results": results,
        "blindHoldout": "STILL SEALED",
    }
    with open(out_path, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
