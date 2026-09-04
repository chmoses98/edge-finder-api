#!/usr/bin/env python3
"""
scripts/edgelab/build_calibration_research_dataset.py
======================================================
Builds the point-in-time calibration research dataset from the frozen
PRE_GAME_DECISION captures (see lib/edgelab/research/calibration_dataset.py).
RESEARCH ONLY: reads committed archives, writes only under
data/edgelab/research_artifacts/calibration_research/. No network.

Usage: python3 scripts/edgelab/build_calibration_research_dataset.py
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.research import calibration_dataset  # noqa: E402


def main():
    manifest = calibration_dataset.build()
    print({k: v for k, v in manifest.items() if k not in ("eraBoundaries",)})


if __name__ == "__main__":
    main()
