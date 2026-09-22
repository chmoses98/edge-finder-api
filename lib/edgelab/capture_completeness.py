#!/usr/bin/env python3
"""
lib/edgelab/capture_completeness.py
===================================
PHASES I/J: classify what a capture PROVED, and never more than that.

The contract every capture is judged against:

    DISCOVERED == ARCHIVED + EXPLICITLY EXCLUDED + EXPLICITLY FAILED

THE THREE CLASSES

COMPLETE
    Every series paginated to exhaustion and the broad pass did too, on
    the evidence the snapshot itself carries. Only a capture written by
    the v4 contract can reach this class, because only v4 records
    per-page cursor state.

PARTIAL
    The snapshot records a failure, a truncation, or a live cursor left
    in hand. Provable from the snapshot alone, including for legacy
    snapshots, because an explicit `fetchFailures` entry means the same
    thing in every version.

UNKNOWN_LEGACY
    The snapshot predates the completeness contract and records no
    failure. This is NOT complete.

WHY A CLEAN LEGACY SNAPSHOT IS NOT COMPLETE

This is the whole point of the class, so it is worth being blunt: under
the old capture path a live cursor at `maxPages = 10` was discarded with
NO failure recorded anywhere. A 2,000-market ceiling per series could be
hit and the snapshot would look pristine. `fetchFailureCount: 0`
therefore means "nothing reported an error", which is a strictly weaker
claim than "we retrieved everything".

Marking those COMPLETE would launder an absence of evidence into
evidence of absence -- and would do it on exactly the busiest captures,
which are the ones a page cap bites first. So they are labelled
UNKNOWN_LEGACY and research that needs completeness must exclude them.

That is expensive. It is also true.
"""

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
FAILED = "FAILED"
UNKNOWN_LEGACY = "UNKNOWN_LEGACY"

# Snapshots written by this contract version or later record per-page cursor
# state, which is what makes COMPLETE provable.
CONTRACT_V4 = "kalshi_capture_v4"
CONTRACTS_WITH_PAGINATION_EVIDENCE = frozenset({CONTRACT_V4})


def _failure_series(snapshot):
    names = []
    for failure in snapshot.get("fetchFailures") or []:
        url = failure.get("url") or ""
        if "series_ticker=" in url:
            names.append(url.split("series_ticker=")[-1].split("&")[0])
        else:
            names.append("__broad_discovery__")
    return sorted(set(names))


def classify_snapshot(snapshot):
    """
    Pure. What this snapshot PROVES about its own completeness.

    Returns {"class", "reason", "contractVersion", "incompleteSeries",
             "truncationReasons", "marketsArchived"}.
    """
    contract = snapshot.get("captureContractVersion")
    archived = len(snapshot.get("markets") or [])
    failures = snapshot.get("fetchFailureCount")
    if failures is None:
        failures = len(snapshot.get("fetchFailures") or [])

    base = {
        "contractVersion": contract,
        "marketsArchived": archived,
        "incompleteSeries": [],
        "truncationReasons": [],
    }

    if contract in CONTRACTS_WITH_PAGINATION_EVIDENCE:
        paginations = snapshot.get("pagination") or []
        incomplete = [p for p in paginations if not p.get("complete")]
        # The PRICE UNIVERSE is the per-series scopes. The broad discovery pass
        # has no series filter, so it pages the entire Kalshi exchange and can
        # never be exhausted inside one invocation -- requiring it for COMPLETE
        # made COMPLETE unreachable, which the first live v4 capture proved
        # (all 17 series complete, broad pass truncated at 40,000 records of
        # which 39,938 were not even for that slate date). A contract that
        # cannot be satisfied disqualifies every capture from research forever.
        #
        # Broad truncation is still recorded, just not allowed to invalidate a
        # price universe that was in fact captured whole.
        series_incomplete = [p for p in incomplete if p.get("scope") != "discovery"]
        reasons = sorted({p.get("truncationReason") for p in incomplete
                          if p.get("truncationReason")})
        series = sorted({p.get("series") for p in series_incomplete if p.get("series")})
        declared = snapshot.get("captureStatus")

        if declared == FAILED or (archived == 0 and incomplete):
            return dict(base, **{"class": FAILED, "incompleteSeries": series,
                                 "truncationReasons": reasons,
                                 "reason": "capture retrieved nothing and reported failures"})
        if series_incomplete or failures:
            return dict(base, **{"class": PARTIAL, "incompleteSeries": series,
                                 "truncationReasons": reasons,
                                 "reason": "at least one series did not paginate to exhaustion"})
        if not paginations:
            # v4 claiming completeness with no evidence behind it is not
            # something to take on trust.
            return dict(base, **{"class": UNKNOWN_LEGACY,
                                 "reason": "contract version claims v4 but carries no "
                                           "pagination evidence"})
        return dict(base, **{
            "class": COMPLETE,
            "truncationReasons": reasons,   # broad-pass truncation stays visible
            "reason": "every series paginated to exhaustion"})

    # ---- legacy ----------------------------------------------------------
    if failures:
        return dict(base, **{"class": PARTIAL,
                             "incompleteSeries": _failure_series(snapshot),
                             "truncationReasons": ["RECORDED_FETCH_FAILURE"],
                             "reason": "snapshot records explicit fetch failures"})

    return dict(base, **{
        "class": UNKNOWN_LEGACY,
        "reason": ("predates the completeness contract: a live cursor at the old "
                   "maxPages=10 ceiling was discarded with no failure recorded, so "
                   "fetchFailureCount=0 means 'nothing reported an error', not "
                   "'everything was retrieved'"),
    })


def reconcile_snapshot(snapshot):
    """
    Pure. PHASE I: does DISCOVERED == ARCHIVED + EXCLUDED, per capture?

    `unaccounted` must be zero. A non-zero value is a row the source
    returned that the capture can neither show nor explain.

    Legacy snapshots do not record how many rows the source returned, so
    there is nothing to reconcile and `reconcilable` is False -- stated
    rather than quietly scored as balanced.
    """
    recon = snapshot.get("reconciliation")
    if not recon:
        return {"reconcilable": False, "unaccounted": None,
                "reason": "snapshot records no source row count to reconcile against"}
    received = recon.get("sourceRecordsReceived", 0)
    archived = recon.get("marketsArchived", 0)
    discovered = recon.get("discoveredUnknownSeriesArchived", 0)
    excluded = recon.get("explicitlyExcluded", 0)
    return {
        "reconcilable": True,
        "sourceRecordsReceived": received,
        "marketsArchived": archived,
        "discoveredUnknownSeriesArchived": discovered,
        "explicitlyExcluded": excluded,
        "unaccounted": received - archived - discovered - excluded,
        "balanced": received - archived - discovered - excluded == 0,
    }


def is_research_qualified(classification, allow_unknown_legacy=False):
    """
    Whether evidence from this capture may back a headline research claim.

    PARTIAL is refused because its missingness is SYSTEMATIC, not random:
    the old sequential fetch meant a rate limit truncated whatever came
    last, so the same hitter prop families absorbed the loss every time
    (KXMLBHRR x6, KXMLBRBI x6, KXMLBSB x5, KXMLBTB x2 over 21 days).
    Mixing that into a family-level calibration does not add noise, it
    adds bias in a known direction.
    """
    if classification == COMPLETE:
        return True
    if classification == UNKNOWN_LEGACY:
        return bool(allow_unknown_legacy)
    return False
