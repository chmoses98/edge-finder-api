#!/usr/bin/env python3
"""MLB-ALPHA-0002: consolidate every family's results into one
discovery_summary.json (strongest signal per family, BH survivors,
post-fee verdicts, sample sizes). Read-only over the artifacts."""

import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")


def load(name):
    p = os.path.join(ART, name)
    return json.load(open(p)) if os.path.exists(p) else None


def main():
    c, d, dm, e, t, ev = (load(n) for n in ("family_c_results.json", "family_d_results.json", "family_d_multibook_results.json",
                                            "family_e_results.json", "family_t_results.json", "event_study_lineups.json"))
    cm = load("pit_candle_panel.meta.json")
    out = {"programId": "MLB-ALPHA-0002", "panel": {"rows": cm["rows"], "games": cm["games"], "dates": len(cm["dates"]), "tickers": cm["tickers"]} if cm else None}
    if c:
        tested = [x for x in c["coarseRules"] if x.get("status") == "TESTED"]
        pos_fm = sorted([x for x in tested if x["bhSurvivor_fairMid"] and x["fairMidMoveSignalSideCents"] > 0], key=lambda x: -x["fairMidMoveSignalSideCents"])
        pos_pl = [x for x in tested if x["bhSurvivor_netPl"] and x["netPlPer10Usd"] > 0]
        by_kind = {}
        for x in tested:
            k = x["rule"].split("_")[1]
            by_kind.setdefault(k, []).append(x)
        strongest = {}
        for k, xs in by_kind.items():
            best = max(xs, key=lambda x: x["fairMidMoveSignalSideCents"] if x["bhSurvivor_fairMid"] else -9)
            strongest[k] = {"rule": best["rule"], "family": best["family"], "games": best["games"],
                            "fairMidCents": best["fairMidMoveSignalSideCents"], "fairMidCi95": best["fairMidCi95"],
                            "execClvCents": best["execClvCents"], "execClvCi95": best["execClvCi95"],
                            "netPlPer10Usd": best["netPlPer10Usd"], "netPlCi95": best["netPlCi95"], "bhFairMid": best["bhSurvivor_fairMid"]}
        out["familyC"] = {"cellsTested": len(tested), "hypothesesTested": c["hypothesesTested"],
                          "bhSurvivorsFairMid": sum(x["bhSurvivor_fairMid"] for x in tested),
                          "bhSurvivorsExecClv": sum(x["bhSurvivor_execClv"] for x in tested),
                          "bhSurvivorsNetPl": sum(x["bhSurvivor_netPl"] for x in tested),
                          "bhSurvivorsNetPlPositive": [{"rule": x["rule"], "family": x["family"], "games": x["games"], "netPl": x["netPlPer10Usd"], "ci": x["netPlCi95"]} for x in pos_pl],
                          "strongestByKind": strongest, "topFairMidCells": [{"rule": x["rule"], "family": x["family"], "games": x["games"], "fairMid": x["fairMidMoveSignalSideCents"], "ci": x["fairMidCi95"], "netPl": x["netPlPer10Usd"]} for x in pos_fm[:8]],
                          "walkForward": c["walkForward"]}
    out["familyD"] = {"pilot": {k: v for k, v in (d or {}).items() if k != "byKind"}, "byKind": (d or {}).get("byKind"),
                      "multibook": {k: v for k, v in (dm or {}).items()}}
    out["familyE"] = (e or {}).get("perFamily")
    out["familyT"] = t
    out["lineupEventStudy"] = ev
    with open(os.path.join(ART, "discovery_summary.json"), "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True, default=str); fh.write("\n")
    print(json.dumps(out.get("familyC", {}).get("strongestByKind"), indent=1, default=str)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
