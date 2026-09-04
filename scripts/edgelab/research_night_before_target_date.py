#!/usr/bin/env python3
"""
scripts/edgelab/research_night_before_target_date.py
====================================================
RESEARCH ONLY. Prints the slate date that a night-before research capture
taken right now should request, or the literal string "SKIP" when this
moment is not one of the configured ET checkpoints.

Exists so .github/workflows/research-night-before-capture.yml never has to
compute a calendar date in shell. The previous shell version used
`date -d 'tomorrow'` unconditionally, which is correct at the 20:00 and
22:00 ET checkpoints but wrong at the 00:00 ET one -- by then the ET
calendar has already rolled forward, so "tomorrow" is two slates away. The
rule now lives in one tested function
(lib/edgelab/research/night_before_timing.night_before_target_slate_date)
rather than in a shell expression no test can reach.

Changes no production behaviour: reads a clock, prints a date, exits.

Usage:
    python3 scripts/edgelab/research_night_before_target_date.py
    python3 scripts/edgelab/research_night_before_target_date.py --now 2026-08-16T04:00:00Z
"""
import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.research.night_before_timing import (  # noqa: E402
    night_before_target_slate_date,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--now", default=None,
                        help="ISO-8601 instant to evaluate instead of the real clock "
                             "(must carry an offset, e.g. 2026-08-16T04:00:00Z). "
                             "Set explicitly for deterministic tests.")
    args = parser.parse_args(argv)
    now = args.now or datetime.now(timezone.utc).isoformat()
    print(night_before_target_slate_date(now))
    return 0


if __name__ == "__main__":
    sys.exit(main())
