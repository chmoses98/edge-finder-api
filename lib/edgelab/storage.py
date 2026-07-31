"""
lib/edgelab/storage.py
========================
Append-with-dedup JSONL storage for the EdgeLab schema.

Design choice: rather than a true blind append (fast, but duplicates a
record if a workflow reruns the same capture), every write reads the
day's existing file, builds a set of already-present IDs, drops
incoming records whose ID already exists, and rewrites the file
atomically (temp file + fsync + os.replace, same pattern as
lib/atomic_json.py). For EdgeLab's per-day partition sizes (tens to
low-thousands of rows) this is cheap and buys idempotent reruns "for
free" -- a second run of the same workflow against the same snapshot
file is a guaranteed no-op, never a duplicate row.

Records are written one JSON object per line, sorted-keys, so two runs
producing equivalent data byte-diff to nothing (same convention as
lib/pipeline_artifacts.py's write_stage_artifact()).
"""

import json
import os
import tempfile

EDGELAB_ROOT = os.path.join("data", "edgelab")


def partition_path(entity: str, date: str) -> str:
    """Path for a date-partitioned entity, e.g. observations/2026-07-31.jsonl."""
    return os.path.join(EDGELAB_ROOT, entity, f"{date}.jsonl")


def singleton_path(entity: str, filename: str) -> str:
    """Path for a non-date-partitioned entity, e.g. the one canonical bets.jsonl."""
    return os.path.join(EDGELAB_ROOT, entity, filename)


def read_records(path: str):
    """Yield each JSON record in a JSONL file. Empty list if the file doesn't exist yet."""
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _atomic_write_lines(path: str, lines):
    dest_dir = os.path.dirname(path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    umask = os.umask(0o022)
    os.umask(umask)
    default_mode = 0o666 & ~umask
    fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".jsonl.tmp", dir=dest_dir)
    try:
        os.chmod(tmp_path, default_mode)
        with os.fdopen(fd, "w") as f:
            for line in lines:
                f.write(line)
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def append_records(path: str, records, id_field: str):
    """
    Merge `records` into the JSONL file at `path`, skipping any whose
    `id_field` value is already present. Returns (written_count, skipped_count).

    Existing rows are never rewritten or reordered beyond their original
    relative order; new rows are appended after them in the order given.
    """
    existing = list(read_records(path))
    existing_ids = {row.get(id_field) for row in existing if row.get(id_field) is not None}

    to_write = list(existing)
    written = 0
    skipped = 0
    for record in records:
        rid = record.get(id_field)
        if rid is not None and rid in existing_ids:
            skipped += 1
            continue
        to_write.append(record)
        if rid is not None:
            existing_ids.add(rid)
        written += 1

    lines = [json.dumps(row, sort_keys=True) for row in to_write]
    _atomic_write_lines(path, lines)
    return written, skipped


def upsert_records(path: str, records, id_field: str):
    """
    Like append_records, but a record whose id_field matches an existing
    row REPLACES that row in place (by identity, not appended again) --
    for entities that are revised over time (Recommendation, PlacedBet,
    Settlement) rather than pure time-series (MarketObservation, ClvQuote).
    Preserves original row order; updated rows keep their original position.
    Returns (updated_count, inserted_count).
    """
    existing = list(read_records(path))
    index_by_id = {row.get(id_field): i for i, row in enumerate(existing) if row.get(id_field) is not None}

    updated = 0
    inserted = 0
    for record in records:
        rid = record.get(id_field)
        if rid is not None and rid in index_by_id:
            existing[index_by_id[rid]] = record
            updated += 1
        else:
            existing.append(record)
            if rid is not None:
                index_by_id[rid] = len(existing) - 1
            inserted += 1

    lines = [json.dumps(row, sort_keys=True) for row in existing]
    _atomic_write_lines(path, lines)
    return updated, inserted
