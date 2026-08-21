#!/usr/bin/env python3
"""
tests/edgelab/test_hitter_board_bridge.py
==============================================
Hitter Prop Methodology Repair mission: coverage for
lib/edgelab/hitter_board_bridge.py -- the pure read bridge from
scripts/build_hitter_projection_board.py's per-contract hitter-prop
board output to the same ticker-keyed lookup shape
lib.edgelab.kalshi_discovery_bridge already produces, so both merge
into one discovery_lookup for
lib.edgelab.model_evaluation.extend_full_universe_evaluations().
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import lib.pipeline_artifacts as pipeline_artifacts
from lib.edgelab.hitter_board_bridge import load_hitter_board_lookup

DATE = "2026-08-01"


def _seed_board(tmp_path, monkeypatch, rows):
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", str(tmp_path))
    pipeline_artifacts.write_stage_artifact(
        "hitter_projection_board", DATE,
        {"rows": rows, "hitterSummaries": [], "summary": {}},
        produced_by="scripts/build_hitter_projection_board.py",
    )


def _projected_row(ticker="KXMLBHIT-T-PLAYER1-1", family="hitter_hits", model_prob=0.42,
                    exec_price=0.35, raw_edge=0.07, ev=0.15, threshold=1, title="Player: 1+ hits?"):
    return {
        "marketTicker": ticker, "marketFamily": family, "threshold": threshold,
        "naturalLanguageMarket": title, "modelProbability": model_prob,
        "executableKalshiPrice": exec_price, "rawProbabilityEdge": raw_edge,
        "expectedValuePerDollar": ev, "projectionStatus": "PROJECTED", "projectionStatusReason": None,
    }


def _status_only_row(ticker, status, reason):
    return {
        "marketTicker": ticker, "marketFamily": "hitter_rbis", "threshold": 2,
        "naturalLanguageMarket": None, "modelProbability": None,
        "executableKalshiPrice": None, "rawProbabilityEdge": None,
        "expectedValuePerDollar": None, "projectionStatus": status, "projectionStatusReason": reason,
    }


def test_missing_artifact_returns_empty_dict_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_artifacts, "PIPELINE_ROOT", str(tmp_path))
    assert load_hitter_board_lookup("2026-01-01") == {}


def test_projected_row_maps_to_supported_with_percent_scaled_fields(tmp_path, monkeypatch):
    row = _projected_row()
    _seed_board(tmp_path, monkeypatch, [row])
    lookup = load_hitter_board_lookup(DATE)
    entry = lookup[row["marketTicker"]]
    assert entry["modelSupportStatus"] == "SUPPORTED"
    assert entry["fairProbabilityPct"] == 42.0
    assert entry["impliedProbabilityPct"] == 35.0
    assert entry["rawEdgePct"] == 7.0
    assert entry["expectedProfitPerDollar"] == 0.15
    assert entry["marketTitle"] == "Player: 1+ hits?"
    assert entry["line"] == 1
    assert entry["marketFamily"] == "hitter_hits"
    assert entry["modelSource"] == "lib.research.hitter_board_builder.build_hitter_projection_rows"


def test_lineup_unconfirmed_maps_to_missing_data_never_fabricates_probability(tmp_path, monkeypatch):
    row = _status_only_row("KXMLBRBI-T-P2-2", "LINEUP_UNCONFIRMED", "confirmed starting lineup not yet available for BOS")
    _seed_board(tmp_path, monkeypatch, [row])
    entry = load_hitter_board_lookup(DATE)[row["marketTicker"]]
    assert entry["modelSupportStatus"] == "MISSING_DATA"
    assert entry["fairProbabilityPct"] is None
    assert entry["unsupportedReason"] == "confirmed starting lineup not yet available for BOS"


def test_market_semantics_unsupported_maps_to_unsupported(tmp_path, monkeypatch):
    row = _status_only_row("KXMLBRBI-T-P3-2", "MARKET_SEMANTICS_UNSUPPORTED", "ticker/title did not classify")
    _seed_board(tmp_path, monkeypatch, [row])
    entry = load_hitter_board_lookup(DATE)[row["marketTicker"]]
    assert entry["modelSupportStatus"] == "UNSUPPORTED"
    assert entry["unsupportedReason"] == "ticker/title did not classify"


def test_stolen_bases_never_appears_since_the_board_never_projects_it(tmp_path, monkeypatch):
    """hitter_stolen_bases has no method anywhere in this codebase -- the board itself never emits a PROJECTED row for it, so this bridge structurally cannot fabricate one."""
    row = _status_only_row("KXMLBSB-T-P4-2", "MARKET_SEMANTICS_UNSUPPORTED", "not a supported family")
    row["marketFamily"] = "hitter_stolen_bases"
    _seed_board(tmp_path, monkeypatch, [row])
    entry = load_hitter_board_lookup(DATE)[row["marketTicker"]]
    assert entry["modelSupportStatus"] != "SUPPORTED"
    assert entry["fairProbabilityPct"] is None


def test_multiple_families_and_thresholds_all_present_independently(tmp_path, monkeypatch):
    rows = [
        _projected_row(ticker="KXMLBHIT-T-P-1", family="hitter_hits", threshold=1, model_prob=0.6),
        _projected_row(ticker="KXMLBHIT-T-P-2", family="hitter_hits", threshold=2, model_prob=0.3),
        _projected_row(ticker="KXMLBTB-T-P-2", family="hitter_total_bases", threshold=2, model_prob=0.4),
        _projected_row(ticker="KXMLBRBI-T-P-1", family="hitter_rbis", threshold=1, model_prob=0.25),
        _projected_row(ticker="KXMLBHRR-T-P-3", family="hitter_hits_runs_rbis", threshold=3, model_prob=0.15),
    ]
    _seed_board(tmp_path, monkeypatch, rows)
    lookup = load_hitter_board_lookup(DATE)
    assert len(lookup) == 5
    assert lookup["KXMLBHIT-T-P-1"]["fairProbabilityPct"] == 60.0
    assert lookup["KXMLBHIT-T-P-2"]["fairProbabilityPct"] == 30.0
    assert {v["marketFamily"] for v in lookup.values()} == {
        "hitter_hits", "hitter_total_bases", "hitter_rbis", "hitter_hits_runs_rbis",
    }
