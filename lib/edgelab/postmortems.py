"""
lib/edgelab/postmortems.py
==============================
Structured Postmortem Ingestion milestone (Part 5): a repository-backed
way to save a completed daily postmortem supplied after the betting day
-- typically a ChatGPT-to-Claude handoff containing the finished
Markdown narrative plus structured JSON findings. Distinct from
scripts/edgelab/generate_postmortem.py (which computes pure arithmetic
FROM the canonical ledger): this module stores the human/model
ANALYTICAL content (wins, misses, process errors, proposed
investigations) and links it explicitly to real canonical betIds --
never substituting a recommendation for a bet that was never actually
placed, and never fabricating an analytical finding that wasn't supplied.

Storage (data/edgelab/postmortems/<gameDate>/):
  postmortem.json     -- current (ACTIVE) revision, schema_v1/postmortem.schema.json
  postmortem.md       -- current revision's human-readable Markdown
  revisions.jsonl      -- append-only history of every SUPERSEDED revision
  bet_linkage.json     -- resolved/unresolved bet references, snapshotted
  import_receipts.json -- one receipt per import attempt (append-only)

One logical postmortem per calendar date (postmortemId = sha1('postmortem'
+ gameDate)); a correction is a NEW REVISION of the same postmortemId,
never a new id and never an in-place overwrite that destroys the prior
narrative -- the superseded revision is preserved in revisions.jsonl.
"""

import os

from lib.edgelab import ids, schema, storage
from lib.edgelab import SCHEMA_VERSION

_TOTALS_TOLERANCE = 0.01


def _postmortem_dir(game_date):
    return os.path.join("data", "edgelab", "postmortems", game_date)


def _content_fingerprint(record):
    """
    Excludes volatile bookkeeping (createdAt/updatedAt/revision/
    recordStatus/supersededBy, and provenance.capturedAt/ingestedAt --
    both are stamped fresh to "now" on every build_postmortem_record call
    for this entity, unlike e.g. PlacedBet where capturedAt is meaningful
    entry-time data) for change detection, so an identical resubmission
    of the same findings/markdown is a true no-op rather than a spurious
    CORRECTED just because bookkeeping timestamps ticked forward.
    """
    r = dict(record)
    for field in ("createdAt", "updatedAt", "revision", "recordStatus", "supersededBy"):
        r.pop(field, None)
    prov = dict(r.get("provenance") or {})
    prov.pop("ingestedAt", None)
    prov.pop("capturedAt", None)
    r["provenance"] = prov
    return r


def compute_canonical_totals(linked_bets):
    """
    Pure. Recomputes totalRisked/totalReturned/netProfitLoss/roi from the
    REAL canonical bet rows this postmortem actually links to (never all
    of a date's bets -- only the ones the caller resolved), the same
    arithmetic lib.edgelab.reports.build_postmortem uses so a postmortem's
    own numbers can never quietly drift from what the ledger says.
    Excludes CANCELLED rows and non-REAL tracking types (paper/probe),
    matching build_postmortem's own convention.
    """
    real = [
        b for b in linked_bets
        if (b.get("recordStatus") or "ACTIVE") != "CANCELLED" and b.get("trackingType") in (None, "REAL")
    ]
    settled = [b for b in real if b.get("status") == "settled"]
    total_risked = round(sum(b.get("stake") or 0 for b in real), 2)
    total_risked_settled = round(sum(b.get("stake") or 0 for b in settled), 2)
    total_net_pl = round(sum(b.get("netProfitLoss") or 0 for b in settled), 2)
    total_returned = round(sum(
        (b.get("stake") or 0) + (b.get("netProfitLoss") or 0)
        for b in settled if b.get("result") in ("WIN", "PUSH", "VOID")
    ), 2)
    roi = round((total_net_pl / total_risked_settled) * 100, 2) if total_risked_settled else None
    return {"totalRisked": total_risked, "totalReturned": total_returned, "netProfitLoss": total_net_pl, "roi": roi}


def _totals_match(reported, canonical):
    if not reported:
        return None
    for key in ("totalRisked", "totalReturned", "netProfitLoss", "roi"):
        r, c = reported.get(key), canonical.get(key)
        if r is None or c is None:
            if r != c:
                return False
            continue
        if abs(r - c) > _TOTALS_TOLERANCE:
            return False
    return True


def resolve_bet_references(bet_ids, all_bets_by_id):
    """
    Splits caller-supplied bet references into (resolved_bets,
    unresolved_references) -- a reference that isn't a real, existing
    betId is NEVER silently dropped or guessed at; it's kept in the
    unresolved list for visibility (data/edgelab/schema_v1/postmortem.schema.json's
    unresolvedBetReferences).
    """
    resolved, unresolved = [], []
    for ref in bet_ids or []:
        bet = all_bets_by_id.get(ref)
        if bet is None:
            unresolved.append({"reference": ref, "reason": "no canonical bet with this betId exists"})
        else:
            resolved.append(bet)
    return resolved, unresolved


def build_postmortem_record(
    game_date, bet_ids, all_bets_by_id, *,
    markdown_path=None, reported_totals=None, performance_by_market_family=None,
    game_level_concentration=None, analytical_wins=None, analytical_misses=None,
    process_errors=None, proposed_investigations=None, structured_findings=None,
    revision=1, record_status="ACTIVE", superseded_by=None,
    created_at=None, source="postmortem_import",
):
    """
    Build one Postmortem record (does not write anything -- see
    write_postmortem). Never substitutes a Recommendation for a betId
    that doesn't actually exist in the canonical ledger -- see
    resolve_bet_references. canonicalTotals/totalsMatch are always
    independently recomputed here, never taken from the caller's claim.
    """
    resolved_bets, unresolved = resolve_bet_references(bet_ids, all_bets_by_id)
    canonical_totals = compute_canonical_totals(resolved_bets)
    now = created_at or ids.utc_now_iso()
    return {
        "schemaVersion": SCHEMA_VERSION,
        "postmortemId": ids.build_postmortem_id(game_date),
        "gameDate": game_date,
        "revision": revision,
        "recordStatus": record_status,
        "supersededBy": superseded_by,
        "markdownPath": markdown_path,
        "linkedBetIds": [b["betId"] for b in resolved_bets],
        "unresolvedBetReferences": unresolved,
        "reportedTotals": reported_totals,
        "canonicalTotals": canonical_totals,
        "totalsMatch": _totals_match(reported_totals, canonical_totals),
        "performanceByMarketFamily": performance_by_market_family or [],
        "gameLevelConcentration": game_level_concentration or [],
        "analyticalWins": analytical_wins or [],
        "analyticalMisses": analytical_misses or [],
        "processErrors": process_errors or [],
        "proposedInvestigations": proposed_investigations or [],
        "structuredFindings": structured_findings,
        "createdAt": now,
        "updatedAt": None,
        "source": source,
        "validationStatus": "warning" if unresolved else "valid",
        "provenance": {
            "sourceSystem": source, "sourceFile": None, "sourceKey": game_date,
            "capturedAt": now, "ingestedAt": now,
        },
    }


def write_postmortem(record, markdown_text):
    """
    THE canonical write function for the postmortem ledger (mirrors
    lib.edgelab.bets.write_placed_bet's posture: one write path, explicit
    idempotency/correction semantics, a real receipt every time).

    - No existing postmortem.json for this date -> write revision 1.
      duplicateStatus="NEW".
    - Existing postmortem.json, identical content (see
      _content_fingerprint) -> true no-op. duplicateStatus="DUPLICATE_NOOP",
      nothing is written or revised.
    - Existing postmortem.json, content differs -> a correction: the
      existing revision is appended to revisions.jsonl marked
      recordStatus=CORRECTED/supersededBy=<new revision>, and the new
      record (revision = existing.revision + 1) becomes current.
      duplicateStatus="CORRECTED".
    """
    errors = schema.validate_record("postmortem", record)
    if errors:
        return {"success": False, "duplicateStatus": "INVALID", "errors": errors, "postmortemId": record.get("postmortemId")}

    game_date = record["gameDate"]
    pm_dir = _postmortem_dir(game_date)
    json_path = os.path.join(pm_dir, "postmortem.json")
    md_path = os.path.join(pm_dir, "postmortem.md")
    revisions_path = os.path.join(pm_dir, "revisions.jsonl")

    # markdownPath is deterministic from gameDate alone, so it's set on the
    # candidate BEFORE the fingerprint comparison below -- otherwise a
    # freshly-built record (markdownPath=None, not yet written) would never
    # fingerprint-match the on-disk existing row (which already has it
    # set), turning every identical resubmission into a spurious CORRECTED.
    candidate = dict(record)
    candidate["markdownPath"] = md_path

    lock_path = json_path
    with storage.locked(lock_path):
        existing = None
        if os.path.exists(json_path):
            import json
            with open(json_path) as f:
                existing = json.load(f)

        if existing is not None and _content_fingerprint(existing) == _content_fingerprint(candidate):
            return {
                "success": True, "duplicateStatus": "DUPLICATE_NOOP", "postmortemId": existing["postmortemId"],
                "revision": existing["revision"], "errors": [],
            }

        if existing is not None:
            candidate["revision"] = existing["revision"] + 1
            candidate["createdAt"] = existing.get("createdAt", record["createdAt"])
            candidate["updatedAt"] = ids.utc_now_iso()
            superseded = dict(existing)
            superseded["recordStatus"] = "CORRECTED"
            superseded["supersededBy"] = candidate["revision"]
            storage.append_records(revisions_path, [superseded], "revision")
            duplicate_status = "CORRECTED"
        else:
            duplicate_status = "NEW"

        os.makedirs(pm_dir, exist_ok=True)
        storage.write_all_records(json_path, [candidate])  # single-object file; reuses the same atomic-write primitive
        with open(md_path, "w") as f:
            f.write(markdown_text or "")

        return {
            "success": True, "duplicateStatus": duplicate_status, "postmortemId": candidate["postmortemId"],
            "revision": candidate["revision"], "errors": [],
        }
