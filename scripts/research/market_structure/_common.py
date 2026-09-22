"""Shared CLI helpers for MRV runners. RESEARCH ONLY."""
import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

ARTIFACT_DIR = os.path.join(REPO, "data", "edgelab", "research_artifacts", "market_structure")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_artifact(name, obj, out_dir=ARTIFACT_DIR):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, sort_keys=True, default=str)
        f.write("\n")
    os.replace(tmp, path)
    return path


def price_band(cents):
    if cents is None:
        return None
    for lo, hi in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100)):
        if lo <= cents < hi:
            return "%02d-%02d" % (lo, hi)
    return None


def lead_bucket(minutes_to_start):
    if minutes_to_start is None:
        return None
    m = minutes_to_start
    if m < 5:
        return "T-5..0"
    if m < 30:
        return "T-30..5"
    if m < 60:
        return "T-60..30"
    if m < 120:
        return "T-120..60"
    if m < 240:
        return "T-240..120"
    return "T>240"
