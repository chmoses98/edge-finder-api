#!/usr/bin/env python3
"""Format-preserving randomizer for one market JSONL(.gz) file, for
`pure_gate.py rerun` (`randomizer_cmd`): python3 <this> SRC DST SEED.
Perturbs every numeric field except identifiers and reshuffles YES/NO results
(same rule as scripts/research/mlb_pitcher_workload_mutation.randomize_file)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mlb_pitcher_workload_mutation import randomize_file  # noqa: E402

if __name__ == "__main__":
    src, dst, seed = sys.argv[1], sys.argv[2], sys.argv[3]
    randomize_file(src, dst, seed)
