#!/usr/bin/env python3
"""Run ONE MRV prospective capture cycle. RESEARCH ONLY, READ-ONLY.

Usage: run_cycle.py [--root <storage root>] [--trigger <name>] [--attempt N] [--no-props] [--no-trades] [--no-state]
Storage root defaults to <repo>/data/edgelab/research_artifacts/mrv_prospective/v1 (research branch only).
"""
import argparse
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab.research.mrv_collector import STORAGE_RELATIVE_ROOT  # noqa: E402
from lib.edgelab.research.mrv_collector.cycle import run_cycle  # noqa: E402
from lib.edgelab.research.mrv_collector.fetch import Fetcher  # noqa: E402
from lib.edgelab.research.mrv_collector.storage import Store  # noqa: E402
from lib.edgelab.research.mrv_collector.universe import load_policy  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(REPO, STORAGE_RELATIVE_ROOT))
    ap.add_argument("--policy", default=os.path.join(REPO, "data", "edgelab", "research_artifacts", "mrv_prospective", "mrv_series_policy.json"))
    ap.add_argument("--trigger", default=os.environ.get("GITHUB_EVENT_NAME") or "local")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--no-props", action="store_true")
    ap.add_argument("--no-trades", action="store_true")
    ap.add_argument("--no-state", action="store_true")
    args = ap.parse_args(argv)
    store = Store(args.root)
    manifest = run_cycle(Fetcher(), store, odds_api_key=(os.environ.get("ODDS_API_KEY") or "").strip() or None,
                         policy=load_policy(args.policy), trigger=args.trigger, attempt=args.attempt,
                         include_props=not args.no_props, include_trades=not args.no_trades, include_state=not args.no_state)
    print(json.dumps({k: manifest[k] for k in ("runId", "captureClass", "researchComplete", "wallClockSeconds", "reconciliation", "joins", "http", "failures")}, indent=1, sort_keys=True, default=str))
    return 0 if manifest["captureClass"] != "FAILED" else 2


if __name__ == "__main__":
    sys.exit(main())
