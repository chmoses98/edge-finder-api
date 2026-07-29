#!/usr/bin/env python3
"""
lib/research/inning_result_snapshot_archive.py
===================================================
Model Performance Phase 2A, Part 13 -- pure helpers for the
append-safe historical F3/F5/F7 snapshot archive
(data/research/inning_result_snapshots/<date>.json).

build_snapshot_record() and merge_snapshots() are both pure: no file
I/O, no network, no clock reads (all timestamps are supplied by the
caller). Never imported by production.
"""

DEFAULT_PROJECTION_VERSION = "phase2a_v1"
DEFAULT_DISTRIBUTION_FAMILY = "independent_poisson"


def build_snapshot_record(shadow_row, extra=None):
    """
    Pure. Builds one Part 13 historical snapshot record from a shadow-
    ledger row (lib/research/inning_result_shadow_ledger.py's output
    shape) plus optional extra context.

    `recordId` is a stable, deterministic composite key
    (date:gameId:scope:outcome:ticker) so re-snapshotting the exact
    same market on the exact same date always produces the exact same
    ID -- the basis for merge_snapshots()'s idempotent-upsert behavior.

    `settlementTimestamp` is ALWAYS None here -- this function only
    ever builds a PRE-settlement projection snapshot. Settlement data
    is added later, by a separate step, using a separate timestamp
    field, never backfilled into the same write that created the
    projection snapshot (Part 13's "no future data leakage" and
    "distinct projection and settlement timestamps" requirements).
    """
    extra = extra or {}
    record_id = f"{shadow_row['date']}:{shadow_row['gameId']}:{shadow_row['scope']}:{shadow_row['outcome']}:{shadow_row['ticker']}"
    return {
        "recordId": record_id,
        "date": shadow_row["date"],
        "gameId": shadow_row["gameId"],
        "matchup": shadow_row["matchup"],
        "scheduledStart": extra.get("scheduledStart"),
        "scope": shadow_row["scope"],
        "marketStructure": shadow_row["marketStructure"],
        "outcome": shadow_row["outcome"],
        "ticker": shadow_row["ticker"],
        "title": extra.get("title"),
        "rulesMetadata": extra.get("rulesMetadata"),
        "confirmedLineupStatus": extra.get("confirmedLineupStatus"),
        "awayStartingPitcher": extra.get("awayStartingPitcher"),
        "homeStartingPitcher": extra.get("homeStartingPitcher"),
        "projectedAwayRuns": extra.get("projectedAwayRuns"),
        "projectedHomeRuns": extra.get("projectedHomeRuns"),
        "canonicalModelProb": shadow_row.get("canonicalModelProb"),
        "legacyConditionalProb": shadow_row.get("legacyConditionalProb"),
        "yesBid": shadow_row.get("yesBid"),
        "yesAsk": shadow_row.get("yesAsk"),
        "noBid": shadow_row.get("noBid"),
        "noAsk": shadow_row.get("noAsk"),
        "spread": shadow_row.get("spread"),
        "volume": shadow_row.get("volume"),
        "projectionTimestamp": extra.get("projectionTimestamp"),
        "settlementVerificationStatus": shadow_row.get("settlementStatus"),
        "settlementTimestamp": None,
        "settlementResult": None,
        "projectionVersion": extra.get("projectionVersion", DEFAULT_PROJECTION_VERSION),
        "distributionFamily": extra.get("distributionFamily", DEFAULT_DISTRIBUTION_FAMILY),
        "snapshotTimestamp": shadow_row.get("snapshotTimestamp"),
    }


def merge_snapshots(existing_records, new_records):
    """
    Pure. Upserts `new_records` into `existing_records` keyed by
    `recordId`, returning a new list sorted by recordId.
    Idempotent: re-running with byte-identical new_records against the
    same existing_records produces byte-identical output (a rerun is a
    true no-op). NOT a no-op only when a record with the same
    recordId genuinely changes (e.g. a later snapshot updates
    settlement fields) -- that is an intentional update, not an
    uncontrolled duplicate, since the key is stable and the old row is
    replaced rather than a second copy being appended.
    """
    by_id = {r["recordId"]: r for r in existing_records}
    for r in new_records:
        by_id[r["recordId"]] = r
    return sorted(by_id.values(), key=lambda r: r["recordId"])


def apply_settlement(existing_records, record_id, settlement_result, settlement_timestamp):
    """
    Pure. Returns a NEW list (existing_records is not mutated) with the
    matching record's settlementResult/settlementTimestamp filled in.
    Raises KeyError if record_id is not found -- never silently
    creates a new record from a settlement call, since a settlement
    with no matching projection snapshot would indicate a real data
    problem, not something to paper over.
    """
    ids = {r["recordId"] for r in existing_records}
    if record_id not in ids:
        raise KeyError(f"no existing snapshot record for {record_id!r} -- cannot attach settlement")
    result = []
    for r in existing_records:
        if r["recordId"] == record_id:
            updated = dict(r)
            updated["settlementResult"] = settlement_result
            updated["settlementTimestamp"] = settlement_timestamp
            result.append(updated)
        else:
            result.append(r)
    return result
