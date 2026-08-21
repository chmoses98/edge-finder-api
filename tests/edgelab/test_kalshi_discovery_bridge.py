#!/usr/bin/env python3
"""
tests/edgelab/test_kalshi_discovery_bridge.py
==================================================
Coverage for lib/edgelab/kalshi_discovery_bridge.py: the pure read
bridge from scripts/discover_kalshi_mlb_markets.py's per-contract
discovery output to a ticker-keyed lookup
lib.edgelab.model_evaluation.extend_full_universe_evaluations() reads.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.kalshi_discovery_bridge import load_discovery_lookup


def _write_discovery(tmp_path, date, contracts):
    path = os.path.join(str(tmp_path), f"{date}.json")
    with open(path, "w") as f:
        json.dump({"date": date, "generatedAt": "t", "contracts": contracts}, f)
    return path


def test_loads_contracts_keyed_by_ticker(tmp_path):
    contracts = [
        {"ticker": "T1", "marketFamily": "team_total", "modelSupportStatus": "SUPPORTED", "fairProbabilityPct": 55.0},
        {"ticker": "T2", "marketFamily": "winning_margin", "modelSupportStatus": "SUPPORTED", "fairProbabilityPct": 40.0},
    ]
    _write_discovery(tmp_path, "2026-08-20", contracts)
    lookup = load_discovery_lookup("2026-08-20", discovery_dir=str(tmp_path))
    assert set(lookup) == {"T1", "T2"}
    assert lookup["T1"]["fairProbabilityPct"] == 55.0


def test_missing_file_returns_empty_dict_never_raises(tmp_path):
    assert load_discovery_lookup("2026-01-01", discovery_dir=str(tmp_path)) == {}


def test_malformed_json_returns_empty_dict_never_raises(tmp_path):
    path = os.path.join(str(tmp_path), "2026-08-20.json")
    with open(path, "w") as f:
        f.write("{not valid json")
    assert load_discovery_lookup("2026-08-20", discovery_dir=str(tmp_path)) == {}


def test_contracts_missing_ticker_are_skipped_not_crashed_on(tmp_path):
    contracts = [
        {"marketFamily": "team_total", "modelSupportStatus": "UNSUPPORTED"},  # no ticker, e.g. parse_error row
        {"ticker": "T1", "marketFamily": "team_total", "modelSupportStatus": "SUPPORTED", "fairProbabilityPct": 55.0},
    ]
    _write_discovery(tmp_path, "2026-08-20", contracts)
    lookup = load_discovery_lookup("2026-08-20", discovery_dir=str(tmp_path))
    assert set(lookup) == {"T1"}


def test_unexpected_top_level_shape_returns_empty_dict(tmp_path):
    path = os.path.join(str(tmp_path), "2026-08-20.json")
    with open(path, "w") as f:
        json.dump([1, 2, 3], f)  # not the expected {"contracts": [...]} shape
    assert load_discovery_lookup("2026-08-20", discovery_dir=str(tmp_path)) == {}


def test_loads_against_real_committed_discovery_output_if_present():
    """
    If this repo's own committed data/kalshi/discovery/*.json exists
    (it does as of this mission -- 2026-08-16 through 2026-08-20), this
    proves the loader works against REAL production output, not just
    hand-built fixtures.
    """
    real_dir = os.path.join("data", "kalshi", "discovery")
    candidates = [f[:-5] for f in os.listdir(real_dir) if f.endswith(".json") and f[:4].isdigit() and "_" not in f]
    if not candidates:
        return
    lookup = load_discovery_lookup(sorted(candidates)[-1])
    assert isinstance(lookup, dict)
    assert len(lookup) > 0
    sample = next(iter(lookup.values()))
    assert "modelSupportStatus" in sample
