#!/usr/bin/env python3
"""
tests/edgelab/test_storage_concurrency.py
=============================================
Coverage for lib/edgelab/storage.py's locked() -- Canonical Placed-Bet
Ledger milestone, requirement 17/18: two entry surfaces writing the same
path at once (e.g. two chats submitting a bet, or a background CLV job
racing an interactive form submission) must never lose an update.
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage


def test_concurrent_appends_never_lose_an_update(tmp_path):
    path = str(tmp_path / "concurrent.jsonl")
    n_threads = 8
    errors = []

    def worker(i):
        try:
            storage.append_records(path, [{"id": f"rec-{i}", "v": i}], "id")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    rows = list(storage.read_records(path))
    assert len(rows) == n_threads
    assert {r["id"] for r in rows} == {f"rec-{i}" for i in range(n_threads)}


def test_concurrent_upserts_never_lose_an_update(tmp_path):
    path = str(tmp_path / "concurrent_upsert.jsonl")
    n_threads = 8

    def worker(i):
        storage.upsert_records(path, [{"id": f"rec-{i}", "v": i}], "id")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = list(storage.read_records(path))
    assert len(rows) == n_threads


def test_locked_creates_a_sidecar_lock_file(tmp_path):
    path = str(tmp_path / "x.jsonl")
    with storage.locked(path):
        assert os.path.exists(path + ".lock")


def test_write_all_records_overwrites_atomically(tmp_path):
    path = str(tmp_path / "x.jsonl")
    storage.write_all_records(path, [{"id": "a"}, {"id": "b"}])
    rows = list(storage.read_records(path))
    assert [r["id"] for r in rows] == ["a", "b"]
    storage.write_all_records(path, [{"id": "c"}])
    rows = list(storage.read_records(path))
    assert [r["id"] for r in rows] == ["c"]
