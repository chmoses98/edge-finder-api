#!/usr/bin/env python3
"""
tests/edgelab/test_compact_edgelab_partitions_script.py
==============================================================
Coverage for scripts/edgelab/compact_edgelab_partitions.py -- the CLI
driver for the Corpus Storage Growth mission's gzip-compaction of
finalized (not-most-recent) date partitions across the five entities
that were actually growing the committed repository day over day.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from scripts.edgelab.compact_edgelab_partitions import ENTITIES, compact_all, main


def _seed(entity, date, records):
    path = storage.partition_path(entity, date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    storage.append_records(path, records, "id")


def test_entities_list_matches_the_actually_growing_daily_directories():
    assert set(ENTITIES) == {"settlements", "clv_quotes", "model_evaluations", "markets", "recommendations"}


def test_compact_all_compacts_every_entity_except_most_recent_date(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for entity in ENTITIES:
        _seed(entity, "2026-07-30", [{"id": "a"}])
        _seed(entity, "2026-07-31", [{"id": "b"}])

    results = compact_all()

    for entity in ENTITIES:
        by_date = {r["date"]: r for r in results[entity]}
        assert by_date["2026-07-30"]["action"] == "compacted"
        assert by_date["2026-07-31"]["action"] == "skipped_recent"


def test_main_writes_a_research_run_record_and_exits_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for entity in ENTITIES:
        _seed(entity, "2026-07-30", [{"id": "a"}, {"id": "b"}])
        _seed(entity, "2026-07-31", [{"id": "c"}])

    monkeypatch.setattr(sys, "argv", ["compact_edgelab_partitions.py", "--json"])
    exit_code = main()
    assert exit_code == 0

    research_runs_dir = os.path.join("data", "edgelab", "research_runs")
    run_records = []
    for fname in os.listdir(research_runs_dir):
        if fname.endswith(".jsonl"):
            run_records.extend(storage.read_records(os.path.join(research_runs_dir, fname)))
    corpus_runs = [r for r in run_records if r["runType"] == "CORPUS_COMPACTION"]
    assert len(corpus_runs) == 1
    counts = corpus_runs[0]["counts"]
    assert counts["filesCompacted"] == len(ENTITIES)  # one finalized date per entity
    for entity in ENTITIES:
        assert counts["byEntity"][entity]["compacted"] == 1
        assert counts["byEntity"][entity]["skippedRecent"] == 1


def test_main_is_idempotent_second_run_reports_already_compacted(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _seed("settlements", "2026-07-30", [{"id": "a"}])
    _seed("settlements", "2026-07-31", [{"id": "b"}])

    monkeypatch.setattr(sys, "argv", ["compact_edgelab_partitions.py"])
    assert main() == 0
    capsys.readouterr()
    assert main() == 0  # must not raise/crash on an already-compacted date

    captured = capsys.readouterr()
    assert "already_compacted=1" in captured.out
