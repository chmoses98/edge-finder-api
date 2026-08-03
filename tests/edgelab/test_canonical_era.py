#!/usr/bin/env python3
"""
tests/edgelab/test_canonical_era.py
=======================================
Coverage for lib/edgelab/canonical_era.py -- the canonical betting-era
boundary (2026-08-03). Confirms the constant, the date/bet classifiers,
and that this module is purely a read-only filter: it never writes,
mutates, or reorders bet rows, and never touches settlement/CLV/replay.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import canonical_era


def test_canonical_era_start_date_is_2026_08_03():
    assert canonical_era.CANONICAL_ERA_START_DATE == "2026-08-03"


def test_is_canonical_era_date_boundary():
    assert canonical_era.is_canonical_era_date("2026-08-03") is True
    assert canonical_era.is_canonical_era_date("2026-08-04") is True
    assert canonical_era.is_canonical_era_date("2026-08-02") is False
    assert canonical_era.is_canonical_era_date(None) is False
    assert canonical_era.is_canonical_era_date("") is False


def test_is_canonical_era_prefers_game_date():
    era_bet = {"gameDate": "2026-08-03", "entryTimestamp": "2026-07-01T00:00:00Z"}
    legacy_bet = {"gameDate": "2026-07-15", "entryTimestamp": "2026-08-03T00:00:00Z"}
    assert canonical_era.is_canonical_era(era_bet) is True
    assert canonical_era.is_canonical_era(legacy_bet) is False


def test_is_canonical_era_falls_back_to_entry_timestamp():
    bet = {"gameDate": None, "entryTimestamp": "2026-08-03T18:04:00Z"}
    assert canonical_era.is_canonical_era(bet) is True

    bet2 = {"gameDate": None, "entryTimestamp": "2026-06-18T21:30:00Z"}
    assert canonical_era.is_canonical_era(bet2) is False


def test_bet_with_no_resolvable_date_is_not_canonical_era():
    """Conservative default -- an unknown date is never silently counted as canonical-era."""
    assert canonical_era.is_canonical_era({}) is False
    assert canonical_era.is_canonical_era({"gameDate": None, "entryTimestamp": None}) is False


def test_canonical_era_bets_and_legacy_bets_partition_cleanly():
    bets = [
        {"betId": "legacy-1", "gameDate": "2026-06-18"},
        {"betId": "era-1", "gameDate": "2026-08-03"},
        {"betId": "era-2", "gameDate": "2026-08-10"},
        {"betId": "legacy-2", "gameDate": "2026-08-02"},
    ]
    era = canonical_era.canonical_era_bets(bets)
    legacy = canonical_era.legacy_bets(bets)

    assert {b["betId"] for b in era} == {"era-1", "era-2"}
    assert {b["betId"] for b in legacy} == {"legacy-1", "legacy-2"}
    assert len(era) + len(legacy) == len(bets)


def test_filters_never_mutate_input_rows():
    original = [{"betId": "x", "gameDate": "2026-08-03"}]
    snapshot = [dict(b) for b in original]
    canonical_era.canonical_era_bets(original)
    canonical_era.legacy_bets(original)
    assert original == snapshot


def test_module_is_read_only():
    """This module must never import a write path -- it only classifies already-loaded bets."""
    with open(canonical_era.__file__) as f:
        source = f.read()
    assert "write_placed_bet" not in source
    assert "append_records" not in source
    assert "upsert_records" not in source
    assert "write_all_records" not in source
    assert "import storage" not in source
    assert "from lib.edgelab import storage" not in source
