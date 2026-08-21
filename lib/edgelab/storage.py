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

Gzip support (`.jsonl.gz` paths): MarketObservation is the one entity
whose git-committed volume doesn't fit a season (see
docs/EDGELAB_PHASE1.md's storage-growth section) -- measured at ~20x
smaller gzipped on real repo data. Any path ending in `.gz` is
transparently read/written compressed; callers never need a separate
API. `mtime=0` is pinned on every gzip write specifically so
byte-identical logical content produces a byte-identical compressed
file -- gzip's header otherwise embeds the current wall-clock time,
which would silently break the "a rerun against unchanged input is a
true no-op" guarantee (and make every workflow run look like a change
to `git diff --cached`, even with nothing new to commit).

Concurrency: `append_records`/`upsert_records` are read-modify-write,
not true appends -- two processes racing on the same path could
otherwise both read the same "existing" snapshot and the second
`os.replace` would silently discard the first process's update
(canonical placed-bet-ledger milestone: two entry surfaces, e.g. a
GitHub Actions form run and a local `log_bet.py` invocation, writing
the same file at once). `locked()` closes that race with an
exclusive `fcntl.flock` on a sidecar `<path>.lock` file held for the
whole read+compute+write critical section, so a second same-host
writer blocks until the first finishes rather than racing it. This is
a same-host, same-filesystem guarantee only -- across separate GitHub
Actions runners, safety still comes from each workflow's own
`concurrency:` group serializing runs, and git itself as the eventual
merge point (see docs/CANONICAL_BET_LEDGER.md's concurrency section).
"""

import fcntl
import gzip
import json
import os
import tempfile
from contextlib import contextmanager

EDGELAB_ROOT = os.path.join("data", "edgelab")


def partition_path(entity: str, date: str, compressed: bool = False) -> str:
    """Path for a date-partitioned entity, e.g. observations/2026-07-31.jsonl[.gz]."""
    suffix = ".jsonl.gz" if compressed else ".jsonl"
    return os.path.join(EDGELAB_ROOT, entity, f"{date}{suffix}")


def singleton_path(entity: str, filename: str) -> str:
    """Path for a non-date-partitioned entity, e.g. the one canonical bets.jsonl."""
    return os.path.join(EDGELAB_ROOT, entity, filename)


def _is_gz(path: str) -> bool:
    return path.endswith(".gz")


def read_records(path: str):
    """Yield each JSON record in a JSONL (optionally gzip-compressed) file. Empty list if the file doesn't exist yet."""
    if not os.path.exists(path):
        return
    opener = gzip.open if _is_gz(path) else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def resolve_partition_path(entity: str, date: str) -> str:
    """
    Corpus Storage Growth mission: returns the ACTUAL on-disk path for
    (entity, date) -- the plain `.jsonl` file if it exists, otherwise its
    `.jsonl.gz` sibling (a finalized date compact_finalized_partitions()
    already compacted), otherwise the plain path unchanged (matching
    read_records' own graceful "doesn't exist yet" -> empty contract, so
    a caller can pass this result straight to read_records without a
    None-check). Every READ of a single specific (entity, date) partition
    across this codebase should resolve through this (or read_partition
    below) rather than assuming `partition_path(entity, date)` -- a bare
    call to partition_path is still exactly right for WRITING (a writer
    always targets today's still-uncompressed file, or observations' own
    write-time compressed=True convention, neither of which this
    function changes).
    """
    plain = partition_path(entity, date)
    if os.path.exists(plain):
        return plain
    gz = plain + ".gz"
    return gz if os.path.exists(gz) else plain


def read_partition(entity: str, date: str):
    """read_records() for whichever suffix (entity, date) actually has on disk -- see resolve_partition_path."""
    return read_records(resolve_partition_path(entity, date))


def partition_exists(entity: str, date: str) -> bool:
    """
    True if EITHER the plain or gzip-compacted partition file for
    (entity, date) exists on disk -- for an existence-only check (e.g.
    "was this date ever ingested at all") that a bare `os.path.exists(
    partition_path(...))` would wrongly answer False for once that date
    has been compacted.
    """
    path = partition_path(entity, date)
    return os.path.exists(path) or os.path.exists(path + ".gz")


def _atomic_write_lines(path: str, lines):
    dest_dir = os.path.dirname(path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    umask = os.umask(0o022)
    os.umask(umask)
    default_mode = 0o666 & ~umask
    tmp_suffix = ".jsonl.gz.tmp" if _is_gz(path) else ".jsonl.tmp"
    fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=tmp_suffix, dir=dest_dir)
    try:
        os.chmod(tmp_path, default_mode)
        with os.fdopen(fd, "wb") as raw_f:
            if _is_gz(path):
                # mtime=0: see module docstring -- required for deterministic,
                # rerun-stable compressed output.
                with gzip.GzipFile(fileobj=raw_f, mode="wb", mtime=0, compresslevel=9) as gz_f:
                    for line in lines:
                        gz_f.write(line.encode("utf-8"))
                        gz_f.write(b"\n")
            else:
                for line in lines:
                    raw_f.write(line.encode("utf-8"))
                    raw_f.write(b"\n")
            raw_f.flush()
            os.fsync(raw_f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def write_all_records(path: str, rows):
    """
    Atomically overwrite `path` with exactly `rows`, sorted-keys JSONL,
    same as append_records/upsert_records' output -- but for callers
    that need custom merge/conflict logic those two don't support (e.g.
    lib.edgelab.bets.write_placed_bet's reject-on-conflict semantics).
    Does NOT itself acquire `locked(path)` -- callers must already hold
    it (this exists specifically to be called from inside a `with
    locked(path):` block without re-entering the lock).
    """
    lines = [json.dumps(row, sort_keys=True) for row in rows]
    _atomic_write_lines(path, lines)


@contextmanager
def locked(path: str):
    """
    Exclusive advisory lock (fcntl.flock) on `<path>.lock`, held for the
    duration of a read-modify-write critical section on `path`. Blocks
    (does not fail) while another same-host process holds it -- callers
    doing an interactive write (e.g. the GitHub Actions bet-entry form)
    should not see spurious failures just because a background job (CLV
    collection, settlement) is mid-write. See module docstring.
    """
    dest_dir = os.path.dirname(path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    lock_path = path + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def append_records(path: str, records, id_field: str):
    """
    Merge `records` into the JSONL file at `path`, skipping any whose
    `id_field` value is already present. Returns (written_count, skipped_count).

    Existing rows are never rewritten or reordered beyond their original
    relative order; new rows are appended after them in the order given.
    The whole read+compute+write cycle runs under an exclusive lock on
    this path (see locked) so concurrent writers never race.
    """
    with locked(path):
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
    Returns (updated_count, inserted_count). Runs under an exclusive lock
    on this path (see locked) so concurrent writers never race.
    """
    with locked(path):
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


# ── Historical Partition Compaction (Corpus Storage Growth mission) ──────
#
# Every entity above is already date-partitioned (one file per calendar
# date) -- the gap was never partitioning, it was that a FINALIZED date's
# plain-text .jsonl file (nothing writes to a past date once its day is
# over -- confirmed by auditing every writer of settlements/clv_quotes/
# model_evaluations/markets/recommendations) sits committed forever at
# full uncompressed size, growing the repository ~20-50MB/day across
# those five entities combined. This never introduces new infrastructure:
# it reuses this module's OWN existing, already-tested gzip convention
# (read_records/write_all_records already transparently handle a `.gz`
# suffix; observations/<date>.jsonl.gz already proves this exact pattern
# in production at ~20x smaller compressed on real repo data) -- the only
# new piece is WHEN to flip a finalized date's suffix from .jsonl to
# .jsonl.gz, which these two functions do, safely and reversibly (nothing
# is ever deleted before its compressed replacement is read back and
# verified record-for-record identical).


def compact_partition_to_gzip(jsonl_path: str):
    """
    Compresses one plain `<date>.jsonl` partition file to a
    `<date>.jsonl.gz` sibling, IN PLACE. Idempotent and safe to call
    repeatedly or concurrently with itself (same-host locked): if
    `jsonl_path` no longer exists but its `.gz` sibling does, this is a
    no-op returning that existing path (already compacted); if neither
    exists, returns None (nothing to compact).

    Never deletes the original until the compressed replacement has been
    read back and confirmed to contain the exact same records (compared
    via each row's own sorted-key JSON serialization, order-preserving)
    -- a verification failure raises RuntimeError and leaves the
    original `.jsonl` untouched (removes the bad `.gz` attempt first),
    so a compaction run can never silently lose data. Runs under the
    same `locked()` this module's other writers use, so it cannot race a
    concurrent read/write of the same path.
    """
    gz_path = jsonl_path + ".gz"
    with locked(jsonl_path):
        if not os.path.exists(jsonl_path):
            return gz_path if os.path.exists(gz_path) else None

        original_rows = list(read_records(jsonl_path))
        original_lines = [json.dumps(row, sort_keys=True) for row in original_rows]

        write_all_records(gz_path, original_rows)

        compacted_lines = [json.dumps(row, sort_keys=True) for row in read_records(gz_path)]
        if compacted_lines != original_lines:
            os.remove(gz_path)
            raise RuntimeError(
                f"compact_partition_to_gzip: verification failed for {jsonl_path!r} -- "
                f"{len(original_lines)} original record(s) vs {len(compacted_lines)} after "
                f"compaction. Original left untouched; the bad .gz attempt was removed."
            )

        os.remove(jsonl_path)
        return gz_path


def compact_finalized_partitions(entity: str, root: str = EDGELAB_ROOT, keep_recent: int = 1):
    """
    Compacts every already-finalized plain `.jsonl` partition file for
    `entity` to `.jsonl.gz`, EXCEPT the `keep_recent` most-recent dates
    (by filename sort order -- YYYY-MM-DD partition filenames already
    sort chronologically, so no wall-clock/timezone reasoning is needed
    here at all) which are left uncompressed for easy same-day access and
    diffing -- the file(s) a live pipeline may still be actively
    appending to today. A `.jsonl.gz` file already present is left alone
    (idempotent; never re-compacts).

    Returns a list of one dict per date, oldest first:
      {"date", "action": "compacted" | "skipped_recent" | "already_compacted",
       "recordsBefore", "recordsAfter", "bytesBefore", "bytesAfter"}
    `recordsBefore`/`recordsAfter` are always equal for a successful
    "compacted" entry (compact_partition_to_gzip already verified this
    before returning) -- included here so a caller can log/commit an
    honest before/after count without re-reading every file itself.
    """
    entity_dir = os.path.join(root, entity)
    if not os.path.isdir(entity_dir):
        return []

    dates = sorted(
        fname[: -len(".jsonl")]
        for fname in os.listdir(entity_dir)
        if fname.endswith(".jsonl") and not fname.endswith(".jsonl.gz")
    )
    recent_dates = set(dates[-keep_recent:]) if keep_recent > 0 else set()

    results = []
    for date in dates:
        jsonl_path = os.path.join(entity_dir, f"{date}.jsonl")
        if date in recent_dates:
            results.append({"date": date, "action": "skipped_recent"})
            continue

        bytes_before = os.path.getsize(jsonl_path)
        records_before = sum(1 for _ in read_records(jsonl_path))
        gz_path = compact_partition_to_gzip(jsonl_path)
        results.append({
            "date": date,
            "action": "compacted",
            "recordsBefore": records_before,
            "recordsAfter": sum(1 for _ in read_records(gz_path)),
            "bytesBefore": bytes_before,
            "bytesAfter": os.path.getsize(gz_path),
        })

    # Any date whose .jsonl is already gone (a prior run already compacted
    # it) but whose .gz sibling exists -- report it too, so a caller can
    # produce a complete corpus-wide summary without a second pass.
    already_compacted_dates = sorted(
        fname[: -len(".jsonl.gz")]
        for fname in os.listdir(entity_dir)
        if fname.endswith(".jsonl.gz")
    )
    reported_dates = {r["date"] for r in results}
    for date in already_compacted_dates:
        if date not in reported_dates:
            results.append({"date": date, "action": "already_compacted"})

    return sorted(results, key=lambda r: r["date"])
