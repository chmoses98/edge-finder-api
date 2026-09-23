#!/usr/bin/env python3
"""Build the MRV collector health / research-readiness report from the persisted corpus. RESEARCH ONLY.

Usage: build_health.py [--root <storage root>] [--end-date YYYY-MM-DD] [--days 7] [--write]
Writes <root>/health/latest.json and <root>/health/<end-date>.json when --write.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab.research.mrv_collector import STORAGE_RELATIVE_ROOT, COLLECTOR_VERSION  # noqa: E402
from lib.edgelab.research.mrv_collector.health import build_health, GATES  # noqa: E402
from lib.edgelab.research.mrv_collector.storage import Store  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(REPO, STORAGE_RELATIVE_ROOT))
    ap.add_argument("--end-date", default=(datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    rep = build_health(Store(args.root), end_date=args.end_date, days=args.days)
    rep["collectorVersion"] = COLLECTOR_VERSION
    rep["gateDefinitions"] = GATES
    if args.write:
        d = os.path.join(args.root, "health")
        os.makedirs(d, exist_ok=True)
        for name in ("latest.json", args.end_date + ".json"):
            with open(os.path.join(d, name), "w") as f:
                json.dump(rep, f, indent=1, sort_keys=True, default=str)
                f.write("\n")
    print(json.dumps({"captures": rep["metrics"]["captures"], "cadence": rep["metrics"]["cadence"], "gates": rep["gates"]}, indent=1, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
