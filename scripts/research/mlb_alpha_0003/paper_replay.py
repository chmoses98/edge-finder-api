#!/usr/bin/env python3
"""
End-to-end replay of the scan -> verdict -> paper-trade -> settle pipeline over an archived prospective
corpus (MRV 10-minute corpus by default). This demonstrates what runs automatically TODAY; it is not
evidence of profitability: a replay of a handful of cycles cannot be (and the output says so).

  python3 scripts/research/mlb_alpha_0003/paper_replay.py --mrv-root <.../mrv_prospective/v1> --out <dir>

Trading rule in this replay: engine verdict PAPER_SHADOW (conservative net edge >= 3c, robust across game
scripts, correlation-aware leg selection), $10 taker order at the captured book, with the NEXT cycle's
book used when it is worse (latency sensitivity). Nothing is sent anywhere.
"""
import argparse
import collections
import gzip
import json
import os
import sys
from multiprocessing import Pool

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab.mlb_alpha import adapters, anchor, engine, paper  # noqa: E402
from lib.edgelab.research.market_structure import settlements  # noqa: E402

_W = {}
RULE_ID = "ENGINE_V1_PAPER_SHADOW"


def _init(surface_path):
    _W["surface"] = anchor.Surface.from_json(json.load(gzip.open(surface_path, "rt")))
    _W["cfg"] = engine.EngineConfig(n_sim=60_000, n_script_sim=25_000)
    _W["validation"] = engine.ValidationRegistry()      # empty: every family is UNVALIDATED


def _scan(task):
    run_id, gk, game = task
    if not game["anchor"]:
        return run_id, gk, None
    res = engine.scan_game(gk, game["markets"], game["anchor"], _W["surface"], _W["validation"], _W["cfg"], game.get("consensus"))
    # keep only what is needed downstream
    return run_id, gk, {"anchorStatus": res["anchorStatus"],
                        "verdicts": collections.Counter(o["verdict"] for o in res["opportunities"]),
                        "whyPass": collections.Counter((o["verdict"], (o.get("why") or "")[:48]) for o in res["opportunities"] if o["verdict"].startswith("PASS")),
                        "selected": [{k: o[k] for k in ("ticker", "side", "executablePrice", "orderContracts", "modelProbSide", "netEdgeConservative",
                                                         "scriptsPositive", "robustNetEdge", "correlationWithOtherSelections", "modelSigma")}
                                     for o in res["selected"]]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mrv-root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--surface", default=os.path.join(REPO, "data/edgelab/research_artifacts/mlb_alpha_0003/anchor_surface_v1.json.gz"))
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args(argv)

    snaps = list(adapters.mrv_snapshots(a.mrv_root))
    tasks = []
    for s in snaps:
        for gk, g in s["games"].items():
            in_window = [m for m in g["markets"] if 5 < m["minToStart"] <= 240]
            if in_window:
                tasks.append((s["runId"], gk, dict(g, markets=in_window)))
    print("cycles", len(snaps), "game-scans", len(tasks), flush=True)
    results = {}
    with Pool(a.procs, initializer=_init, initargs=(a.surface,)) as pool:
        for run_id, gk, r in pool.imap_unordered(_scan, tasks, chunksize=4):
            results[(run_id, gk)] = r

    verdicts, why = collections.Counter(), collections.Counter()
    no_anchor = 0
    for r in results.values():
        if r is None:
            no_anchor += 1
            continue
        verdicts.update(r["verdicts"])
        why.update(r["whyPass"])
    # paper trading over the selected legs
    outcomes = settlements.build_outcomes(REPO)
    book_by_key = {}
    ordered_runs = [s["runId"] for s in snaps]
    for s in snaps:
        for gk, g in s["games"].items():
            for m in g["markets"]:
                book_by_key[(s["runId"], m["ticker"])] = m["book"]
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "paper_replay_ledger.jsonl")
    if os.path.exists(path):
        os.remove(path)
    ledger = paper.PaperLedger(path, bankroll=1000.0)
    signals = 0
    for (run_id, gk), r in sorted(results.items()):
        if not r:
            continue
        i = ordered_runs.index(run_id)
        nxt = ordered_runs[i + 1] if i + 1 < len(ordered_runs) else None
        for leg in r["selected"]:
            signals += 1
            ledger.submit_taker(run_id=run_id, rule_id=RULE_ID, ticker=leg["ticker"], game=gk, side=leg["side"],
                                contracts=leg["orderContracts"], book=book_by_key[(run_id, leg["ticker"])],
                                next_book=book_by_key.get((nxt, leg["ticker"])) if nxt else None, mode=paper.TAKER_NEXT_BOOK,
                                model_prob=leg["modelProbSide"], meta={k: leg[k] for k in ("netEdgeConservative", "scriptsPositive")})
    settled = 0
    for f in [e for e in ledger.events if e["type"] == "FILL"]:
        res = outcomes.get(f["ticker"])
        if res in ("YES", "NO"):
            ledger.settle(f["ticker"], res == "YES", settled_at=None)
            settled += 1
    summary = {"program": "MLB-ALPHA-0003", "section": "paper replay", "corpus": "MRV v1 (10-minute cadence)",
               "cycles": len(snaps), "gameScans": len(tasks), "scansWithoutAnchor": no_anchor,
               "verdictCounts": dict(verdicts), "topPassReasons": [[list(k), v] for k, v in why.most_common(8)],
               "signals": signals, "ledger": ledger.report(),
               "reading": "Replay of a two-day, 41-game corpus. A result of this size is an infrastructure demonstration, never evidence."}
    with open(os.path.join(a.out, "paper_replay_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True, default=str)
    print(json.dumps(summary, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
