#!/usr/bin/env python3
"""
tests/edgelab/test_storage.py
=================================
Coverage for lib/edgelab/storage.py: append/upsert dedup semantics, and
the gzip-compressed path support added for MarketObservation (see
docs/EDGELAB_PHASE1.md's storage-growth section) -- most importantly
that gzip output is deterministic (mtime pinned) so a rerun against
unchanged input is a byte-identical file, not just equivalent content.
"""
import gzip
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage


def test_partition_path_compressed_flag():
    assert storage.partition_path("observations", "2026-07-31") == os.path.join("data", "edgelab", "observations", "2026-07-31.jsonl")
    assert storage.partition_path("observations", "2026-07-31", compressed=True) == os.path.join("data", "edgelab", "observations", "2026-07-31.jsonl.gz")


def test_gzip_path_round_trips(tmp_path):
    path = str(tmp_path / "obs.jsonl.gz")
    records = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
    written, skipped = storage.append_records(path, records, "id")
    assert written == 2
    assert skipped == 0
    assert os.path.exists(path)
    # It really is gzip on disk, not a plain file with a misleading extension.
    with open(path, "rb") as f:
        magic = f.read(2)
    assert magic == b"\x1f\x8b"

    rows = list(storage.read_records(path))
    assert {r["id"] for r in rows} == {"a", "b"}


def test_gzip_output_is_a_meaningful_size_reduction(tmp_path):
    path_plain = str(tmp_path / "obs.jsonl")
    path_gz = str(tmp_path / "obs.jsonl.gz")
    records = [{"marketTicker": f"KXMLBHIT-26JUL311810PITCIN-PLAYER{i}", "yesBid": 10 + i, "yesAsk": 11 + i, "id": str(i)} for i in range(500)]
    storage.append_records(path_plain, records, "id")
    storage.append_records(path_gz, records, "id")
    plain_size = os.path.getsize(path_plain)
    gz_size = os.path.getsize(path_gz)
    assert gz_size < plain_size / 3  # real repo data measured ~20x; a conservative floor here


def test_gzip_rerun_with_unchanged_content_is_byte_identical(tmp_path):
    """
    Critical for the workflow's `git diff --cached --quiet` guard: gzip
    embeds a wall-clock timestamp in its header by default, which would
    make every rerun look like a change even with nothing new to
    commit, unless mtime is pinned.
    """
    path = str(tmp_path / "obs.jsonl.gz")
    records = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
    storage.append_records(path, records, "id")
    with open(path, "rb") as f:
        first_bytes = f.read()

    import time
    time.sleep(1.1)  # ensure wall-clock actually advances between writes

    # Re-running against a file whose content is already fully covered by
    # `records` should not even touch the file (append_records dedups all
    # of it away) -- but exercise the harder case: force a rewrite with
    # equivalent records in a different object order to prove the OUTPUT
    # BYTES are still deterministic, not just the dedup logic.
    storage._atomic_write_lines(path, [
        __import__("json").dumps(r, sort_keys=True) for r in records
    ])
    with open(path, "rb") as f:
        second_bytes = f.read()

    assert first_bytes == second_bytes


def test_gzip_dedup_and_upsert_work_the_same_as_plain_jsonl(tmp_path):
    path = str(tmp_path / "obs.jsonl.gz")
    storage.append_records(path, [{"id": "a", "v": 1}], "id")
    written, skipped = storage.append_records(path, [{"id": "a", "v": 1}, {"id": "b", "v": 2}], "id")
    assert written == 1
    assert skipped == 1
    rows = list(storage.read_records(path))
    assert len(rows) == 2

    updated, inserted = storage.upsert_records(path, [{"id": "a", "v": 99}], "id")
    assert updated == 1
    assert inserted == 0
    rows = {r["id"]: r["v"] for r in storage.read_records(path)}
    assert rows == {"a": 99, "b": 2}


def test_missing_gzip_file_returns_empty_not_an_error(tmp_path):
    path = str(tmp_path / "does_not_exist.jsonl.gz")
    assert list(storage.read_records(path)) == []
