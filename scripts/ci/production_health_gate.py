#!/usr/bin/env python3
"""
scripts/ci/production_health_gate.py
====================================
REMEDIATION WAVE 0. The operational health gate the 2026-09 settlement outage
proved this repository did not have.

WHY THIS EXISTS
---------------
From 2026-09-02 to 2026-09-07 `clv-update.yml` failed on every scheduled run,
`edgelab-postgame.yml` was skipped on every one of them, `data/edgelab/
settlements/` and `recommendations/` stopped advancing at 2026-08-31, and the
bet backlog grew to 156 rows -- while `corpus-health-check.yml` reported
SUCCESS every single day. It reported success honestly: it checks corpus
SHAPE (are the archived rows well-formed and accounted for?), which was
genuinely fine. Nothing checked corpus FRESHNESS, or whether the pipeline
that produces it had actually run and persisted anything.

This gate is that missing check. It answers one question:

    "Is the production feedback loop alive, and did its output land in git?"

DESIGN PRINCIPLES
-----------------
1. DURABLE STATE ONLY. Every assertion reads committed repository state --
   partition dates, ledger contents, git history. No GitHub API, no token, no
   network. It therefore produces the same verdict locally and in CI, is
   deterministic, and cannot itself fail for an infrastructure reason and be
   dismissed as flaky. Crucially, it observes what actually PERSISTED, which
   is the property that broke: the outage's signature was work being computed
   and then discarded, and only durable state can distinguish those.

2. TWO SEVERITIES, AND ONLY ONE OF THEM IS RED.
     CRITICAL_PRODUCTION  -- the real-money feedback loop is blind or
                             losing data. Fails the workflow.
     RESEARCH_DEGRADATION -- a research collector or optional artifact is
                             behind. Reported, never fails the workflow.
   The audit's own conclusion was that research capture is the healthiest
   part of this system; making it able to page the operator would be exactly
   the noise that trains people to ignore the gate.

3. SEASON-AWARE, SO IT IS NOT NOISY IN THE OFFSEASON. Freshness assertions
   are conditioned on the production pipeline being in an active period at
   all (assertion PROD-1). With no recent slate there are no games to settle,
   and every downstream freshness assertion reports NOT_APPLICABLE rather
   than firing. One assertion covers "the pipeline itself stopped".

4. EXPLAINED BACKLOG DOES NOT COUNT. PROD-7 counts only bets that SHOULD have
   settled. Rows explicitly classified as legitimately unresolvable (void,
   unsupported family, missing canonical evidence -- see
   scripts/audit_settlement_backlog.py, which writes the classification
   artifact this reads) are excluded, so a permanent, understood residue
   never becomes a permanently red gate that everyone learns to ignore.

USAGE
-----
    python3 scripts/ci/production_health_gate.py                 # evaluate, exit 1 on CRITICAL
    python3 scripts/ci/production_health_gate.py --json          # machine-readable
    python3 scripts/ci/production_health_gate.py --write-artifact
    python3 scripts/ci/production_health_gate.py --warn-only     # never exit non-zero

READ-ONLY except for `--write-artifact`, which writes exactly one file:
data/edgelab/operational_health/production_health_gate.json.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(_HERE))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from lib.bet_backlog_classifier import is_non_terminal  # noqa: E402

# ── Severities ───────────────────────────────────────────────────────────────
CRITICAL_PRODUCTION = "CRITICAL_PRODUCTION"
RESEARCH_DEGRADATION = "RESEARCH_DEGRADATION"

# ── Statuses ─────────────────────────────────────────────────────────────────
PASS = "PASS"
FAIL = "FAIL"
NOT_APPLICABLE = "NOT_APPLICABLE"

# ── Thresholds ───────────────────────────────────────────────────────────────
# An MLB night's games finish after midnight ET and settle on the following
# day's run, so a one-day lag is normal and a two-day lag is the first point
# at which a missed run is unambiguous rather than merely late. Each of these
# is deliberately generous enough that a single late or retried run never
# fires the gate, and tight enough that TWO consecutive missed nights always
# does -- the outage went six days unnoticed.
MAX_SETTLEMENT_LAG_DAYS = 2
MAX_RECOMMENDATION_LAG_DAYS = 2
MAX_MODEL_EVALUATION_LAG_DAYS = 2
MAX_SLATE_LAG_DAYS = 2
MAX_LEDGER_COMMIT_LAG_DAYS = 3
# Settleable-but-unsettled rows tolerated before the backlog is a defect.
# Not zero: a handful of genuinely late/suspended games is normal operation.
MAX_UNEXPLAINED_BACKLOG = 15
# A bet is only "should have settled by now" once its game is this old.
BACKLOG_GRACE_DAYS = 3
# How far the production pipeline can be idle before freshness assertions are
# suspended as offseason//paused rather than reported as failures.
INACTIVE_PIPELINE_DAYS = 10

_DATE_PARTITION_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")
_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


# ── State collection (impure, tiny, isolated) ────────────────────────────────

def _latest_partition_date(directory):
    """Newest YYYY-MM-DD partition in a directory, or None."""
    if not os.path.isdir(directory):
        return None
    dates = []
    for name in os.listdir(directory):
        m = _DATE_PARTITION_RE.match(name)
        if m:
            dates.append(m.group(1))
    return max(dates) if dates else None


def _latest_dated_json(directory):
    """Newest YYYY-MM-DD.json in a directory, or None."""
    if not os.path.isdir(directory):
        return None
    dates = []
    for name in os.listdir(directory):
        if name.endswith(".json"):
            m = _DATE_RE.match(name)
            if m:
                dates.append(m.group(1))
    return max(dates) if dates else None


def _git_last_commit_date(root, path):
    """UTC date (YYYY-MM-DD) of the last commit touching `path`, or None."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format-local:%Y-%m-%d", "--", path],
            cwd=root, capture_output=True, text=True, timeout=60,
            env={**os.environ, "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (out.stdout or "").strip()
    return value if _DATE_RE.match(value) else None


def collect_state(root=None, now=None):
    """
    Gather every durable fact the assertions need. Pure-ish: reads the
    filesystem and git, never writes, never hits the network.
    """
    root = root or ROOT_DIR
    now = now or datetime.now(timezone.utc)
    edgelab = os.path.join(root, "data", "edgelab")

    bets_path = os.path.join(root, "bets.json")
    bets = []
    if os.path.exists(bets_path):
        try:
            with open(bets_path) as f:
                bets = json.load(f)
        except (ValueError, OSError):
            bets = []

    acknowledged = set()
    # Wave 0 artifacts live in their OWN namespace, deliberately not in
    # data/edgelab/health/: that directory is the daily heartbeat's, where
    # every *.json is contractually a YYYY-MM-DD.json record carrying a `date`
    # field (see tests/edgelab/test_daily_health_check_target_date.py). Writing
    # differently-shaped artifacts there breaks that reader.
    ack_path = os.path.join(edgelab, "operational_health",
                            "settlement_backlog_classification.json")
    if os.path.exists(ack_path):
        try:
            with open(ack_path) as f:
                payload = json.load(f)
            for row in payload.get("rows", []):
                if row.get("classification") in payload.get("acknowledgedClassifications", []):
                    acknowledged.add(row.get("betId"))
        except (ValueError, OSError):
            acknowledged = set()

    slate_date = None
    meta_path = os.path.join(root, "data", "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                slate_date = (json.load(f) or {}).get("date")
        except (ValueError, OSError):
            slate_date = None

    return {
        "now": now,
        "root": root,
        "settlementLatest": _latest_partition_date(os.path.join(edgelab, "settlements")),
        "recommendationLatest": _latest_partition_date(os.path.join(edgelab, "recommendations")),
        "modelEvaluationLatest": _latest_partition_date(os.path.join(edgelab, "model_evaluations")),
        "researchHeartbeatLatest": _latest_dated_json(os.path.join(edgelab, "health")),
        "slateDate": slate_date,
        "betsLedgerCommitDate": _git_last_commit_date(root, "bets.json"),
        "settlementsCommitDate": _git_last_commit_date(root, "data/edgelab/settlements"),
        "bets": bets,
        "acknowledgedUnresolvableBetIds": acknowledged,
    }


# ── Assertions (pure) ────────────────────────────────────────────────────────

def _lag_days(now, date_str):
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (now.date() - d.date()).days


def _result(assertion_id, severity, status, summary, detail=None):
    return {
        "id": assertion_id,
        "severity": severity,
        "status": status,
        "summary": summary,
        "detail": detail or {},
    }


def _freshness_assertion(assertion_id, severity, label, latest, lag, limit, pipeline_active):
    """Shared shape for the 'a partition stopped advancing' family."""
    if not pipeline_active:
        return _result(assertion_id, severity, NOT_APPLICABLE,
                       "%s freshness not evaluated: production pipeline is inactive" % label,
                       {"latestPartition": latest})
    if latest is None:
        return _result(assertion_id, severity, FAIL,
                       "%s has no dated partition at all" % label,
                       {"latestPartition": None, "maxLagDays": limit})
    if lag is None or lag > limit:
        return _result(assertion_id, severity, FAIL,
                       "%s is %s days stale (limit %d): latest partition %s"
                       % (label, lag, limit, latest),
                       {"latestPartition": latest, "lagDays": lag, "maxLagDays": limit})
    return _result(assertion_id, severity, PASS,
                   "%s current: latest partition %s (%d day(s) old)" % (label, latest, lag),
                   {"latestPartition": latest, "lagDays": lag, "maxLagDays": limit})


def evaluate_health(state):
    """
    Pure. Given collected state, return the ordered list of assertion results.
    Every branch is reachable from a hand-built state dict, which is how the
    tests drive it.
    """
    now = state["now"]
    results = []

    slate_lag = _lag_days(now, state.get("slateDate"))
    pipeline_active = slate_lag is not None and slate_lag <= INACTIVE_PIPELINE_DAYS

    # PROD-1 -- the production pipeline itself is producing slates.
    # Also the switch that suspends every downstream freshness assertion, so
    # this gate stays quiet in the offseason instead of crying every day.
    if slate_lag is None:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, FAIL,
            "data/meta.json carries no usable slate date -- cannot establish whether "
            "the production pipeline is running at all",
            {"slateDate": state.get("slateDate")}))
    elif slate_lag > INACTIVE_PIPELINE_DAYS:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, NOT_APPLICABLE,
            "Production pipeline inactive for %d days (last slate %s) -- treating as "
            "offseason/paused; downstream freshness assertions suspended"
            % (slate_lag, state.get("slateDate")),
            {"slateDate": state.get("slateDate"), "lagDays": slate_lag}))
    elif slate_lag > MAX_SLATE_LAG_DAYS:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, FAIL,
            "Slate pipeline stale: last slate %s is %d days old (limit %d)"
            % (state.get("slateDate"), slate_lag, MAX_SLATE_LAG_DAYS),
            {"slateDate": state.get("slateDate"), "lagDays": slate_lag}))
    else:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, PASS,
            "Slate pipeline active: last slate %s (%d day(s) old)"
            % (state.get("slateDate"), slate_lag),
            {"slateDate": state.get("slateDate"), "lagDays": slate_lag}))

    # PROD-2 -- settlement partitions advancing. THE assertion that would have
    # caught the outage on 2026-09-03, its second day.
    settlement_lag = _lag_days(now, state.get("settlementLatest"))
    results.append(_freshness_assertion(
        "PROD-2", CRITICAL_PRODUCTION, "Settlement corpus",
        state.get("settlementLatest"), settlement_lag,
        MAX_SETTLEMENT_LAG_DAYS, pipeline_active))

    # PROD-3 -- recommendation ledger advancing.
    rec_lag = _lag_days(now, state.get("recommendationLatest"))
    results.append(_freshness_assertion(
        "PROD-3", CRITICAL_PRODUCTION, "Recommendation ledger",
        state.get("recommendationLatest"), rec_lag,
        MAX_RECOMMENDATION_LAG_DAYS, pipeline_active))

    # PROD-4 -- the skip-cascade detector, derived from data rather than from
    # the Actions API. edgelab-postgame.yml only runs when clv-update.yml
    # concludes 'success', so a failing CLV workflow silently skips settlement
    # while the UPSTREAM producer (model evaluations, written by the slate
    # pipeline) keeps advancing. Predictions accumulating while outcomes do
    # not is the exact, durable fingerprint of that cascade -- and it is
    # visible without a token, which means it is also visible locally.
    eval_lag = _lag_days(now, state.get("modelEvaluationLatest"))
    if not pipeline_active:
        results.append(_result("PROD-4", CRITICAL_PRODUCTION, NOT_APPLICABLE,
                               "Skip-cascade check not evaluated: pipeline inactive"))
    elif eval_lag is None or settlement_lag is None:
        results.append(_result(
            "PROD-4", CRITICAL_PRODUCTION, FAIL,
            "Cannot compare prediction and settlement partitions -- one side is missing",
            {"modelEvaluationLatest": state.get("modelEvaluationLatest"),
             "settlementLatest": state.get("settlementLatest")}))
    elif settlement_lag - eval_lag > MAX_SETTLEMENT_LAG_DAYS:
        results.append(_result(
            "PROD-4", CRITICAL_PRODUCTION, FAIL,
            "Downstream settlement is not keeping up with upstream predictions: "
            "model evaluations at %s but settlements only at %s (%d-day divergence). "
            "This is the signature of edgelab-postgame.yml being skipped because "
            "clv-update.yml did not conclude 'success'."
            % (state.get("modelEvaluationLatest"), state.get("settlementLatest"),
               settlement_lag - eval_lag),
            {"modelEvaluationLatest": state.get("modelEvaluationLatest"),
             "settlementLatest": state.get("settlementLatest"),
             "divergenceDays": settlement_lag - eval_lag}))
    else:
        results.append(_result(
            "PROD-4", CRITICAL_PRODUCTION, PASS,
            "Prediction and settlement partitions are in step (evaluations %s, "
            "settlements %s)" % (state.get("modelEvaluationLatest"),
                                 state.get("settlementLatest")),
            {"divergenceDays": settlement_lag - eval_lag}))

    # PROD-5 -- durable persistence. The outage's true signature was not "no
    # work happened", it was "work happened and was thrown away": clv_update.py
    # succeeded every night and its commit step never ran. A ledger that has
    # not been committed in days, while games are being played, means output is
    # being computed and discarded.
    commit_lag = _lag_days(now, state.get("betsLedgerCommitDate"))
    if not pipeline_active:
        results.append(_result("PROD-5", CRITICAL_PRODUCTION, NOT_APPLICABLE,
                               "Ledger persistence not evaluated: pipeline inactive"))
    elif commit_lag is None:
        results.append(_result(
            "PROD-5", CRITICAL_PRODUCTION, FAIL,
            "Cannot determine when bets.json was last committed (no git history?)",
            {"betsLedgerCommitDate": state.get("betsLedgerCommitDate")}))
    elif commit_lag > MAX_LEDGER_COMMIT_LAG_DAYS:
        results.append(_result(
            "PROD-5", CRITICAL_PRODUCTION, FAIL,
            "Canonical ledger bets.json has not been committed for %d days (limit %d, "
            "last commit %s) while the slate pipeline is active -- settlement output is "
            "being computed and discarded rather than persisted"
            % (commit_lag, MAX_LEDGER_COMMIT_LAG_DAYS, state.get("betsLedgerCommitDate")),
            {"betsLedgerCommitDate": state.get("betsLedgerCommitDate"),
             "lagDays": commit_lag}))
    else:
        results.append(_result(
            "PROD-5", CRITICAL_PRODUCTION, PASS,
            "Ledger persisted recently: bets.json last committed %s (%d day(s) ago)"
            % (state.get("betsLedgerCommitDate"), commit_lag),
            {"lagDays": commit_lag}))

    # PROD-6 -- model-evaluation partitions advancing. Distinct from PROD-4:
    # PROD-4 compares the two sides, PROD-6 catches BOTH stopping together
    # (which PROD-4's divergence test cannot see).
    results.append(_freshness_assertion(
        "PROD-6", CRITICAL_PRODUCTION, "Model-evaluation corpus",
        state.get("modelEvaluationLatest"), eval_lag,
        MAX_MODEL_EVALUATION_LAG_DAYS, pipeline_active))

    # PROD-7 -- unexplained settlement backlog. Counts only rows that SHOULD
    # have settled: past the grace period, and not on the acknowledged
    # legitimately-unresolvable list.
    acknowledged = state.get("acknowledgedUnresolvableBetIds") or set()
    stale_unsettled = []
    for bet in state.get("bets") or []:
        if not is_non_terminal(bet):
            continue
        if bet.get("id") in acknowledged:
            continue
        bet_lag = _lag_days(now, str(bet.get("date") or "")[:10])
        if bet_lag is not None and bet_lag > BACKLOG_GRACE_DAYS:
            stale_unsettled.append(bet.get("id"))
    if len(stale_unsettled) > MAX_UNEXPLAINED_BACKLOG:
        results.append(_result(
            "PROD-7", CRITICAL_PRODUCTION, FAIL,
            "Unexplained settlement backlog: %d bets older than %d days are still "
            "non-terminal and are not on the acknowledged-unresolvable list (limit %d)"
            % (len(stale_unsettled), BACKLOG_GRACE_DAYS, MAX_UNEXPLAINED_BACKLOG),
            {"unexplainedBacklog": len(stale_unsettled),
             "limit": MAX_UNEXPLAINED_BACKLOG,
             "acknowledgedCount": len(acknowledged),
             "sampleBetIds": sorted(x for x in stale_unsettled if x)[:10]}))
    else:
        results.append(_result(
            "PROD-7", CRITICAL_PRODUCTION, PASS,
            "Settlement backlog within tolerance: %d unexplained (limit %d, %d "
            "acknowledged as legitimately unresolvable)"
            % (len(stale_unsettled), MAX_UNEXPLAINED_BACKLOG, len(acknowledged)),
            {"unexplainedBacklog": len(stale_unsettled),
             "acknowledgedCount": len(acknowledged)}))

    # PROD-8 -- "green while blind". The specific pathology of 2026-09: the
    # slate pipeline reported success daily, so every surface a human looked at
    # was green, while the settlement half of the loop was dead. Asserted as an
    # explicit conjunction so the report names it rather than leaving a human to
    # notice that PROD-1 passed and PROD-2 failed.
    if not pipeline_active:
        results.append(_result("PROD-8", CRITICAL_PRODUCTION, NOT_APPLICABLE,
                               "Blind-but-green check not evaluated: pipeline inactive"))
    else:
        producing = slate_lag is not None and slate_lag <= MAX_SLATE_LAG_DAYS
        settling = settlement_lag is not None and settlement_lag <= MAX_SETTLEMENT_LAG_DAYS
        if producing and not settling:
            results.append(_result(
                "PROD-8", CRITICAL_PRODUCTION, FAIL,
                "SYSTEM IS BLIND BUT GREEN: the slate pipeline is producing "
                "recommendations daily (last slate %s) while the settlement loop that "
                "would tell us whether they were any good has stopped (last settlement "
                "%s). Real money is being staked with no feedback."
                % (state.get("slateDate"), state.get("settlementLatest")),
                {"slateDate": state.get("slateDate"),
                 "settlementLatest": state.get("settlementLatest")}))
        else:
            results.append(_result(
                "PROD-8", CRITICAL_PRODUCTION, PASS,
                "Production and feedback halves of the loop are both live"))

    # RSCH-1 -- research heartbeat. Reported, never fatal: research degradation
    # must not be able to page the operator (see design principle 2).
    heartbeat_lag = _lag_days(now, state.get("researchHeartbeatLatest"))
    if not pipeline_active:
        results.append(_result("RSCH-1", RESEARCH_DEGRADATION, NOT_APPLICABLE,
                               "Research heartbeat not evaluated: pipeline inactive"))
    elif heartbeat_lag is None or heartbeat_lag > MAX_SETTLEMENT_LAG_DAYS:
        results.append(_result(
            "RSCH-1", RESEARCH_DEGRADATION, FAIL,
            "Research heartbeat stale (latest %s) -- reported, non-blocking"
            % state.get("researchHeartbeatLatest"),
            {"latest": state.get("researchHeartbeatLatest"), "lagDays": heartbeat_lag}))
    else:
        results.append(_result(
            "RSCH-1", RESEARCH_DEGRADATION, PASS,
            "Research heartbeat current (latest %s)" % state.get("researchHeartbeatLatest")))

    return results


def summarize(results):
    """Pure. Roll assertion results up into an overall verdict."""
    critical_failures = [
        r for r in results
        if r["severity"] == CRITICAL_PRODUCTION and r["status"] == FAIL
    ]
    research_failures = [
        r for r in results
        if r["severity"] == RESEARCH_DEGRADATION and r["status"] == FAIL
    ]
    return {
        "overall": "CRITICAL" if critical_failures else "HEALTHY",
        "criticalFailureCount": len(critical_failures),
        "researchDegradationCount": len(research_failures),
        "criticalFailureIds": [r["id"] for r in critical_failures],
        "researchDegradationIds": [r["id"] for r in research_failures],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _render(results, summary):
    lines = []
    lines.append("PRODUCTION HEALTH GATE")
    lines.append("=" * 78)
    for r in results:
        mark = {PASS: "PASS", FAIL: "FAIL", NOT_APPLICABLE: "n/a "}[r["status"]]
        tag = "CRIT" if r["severity"] == CRITICAL_PRODUCTION else "rsch"
        lines.append("[%s] %-5s %-7s %s" % (mark, tag, r["id"], r["summary"]))
    lines.append("=" * 78)
    lines.append("OVERALL: %s  (critical failures: %d, research degradations: %d)"
                 % (summary["overall"], summary["criticalFailureCount"],
                    summary["researchDegradationCount"]))
    if summary["criticalFailureIds"]:
        lines.append("CRITICAL: " + ", ".join(summary["criticalFailureIds"]))
        lines.append("The production feedback loop is not healthy. This gate is RED "
                     "on purpose -- do not re-run it to get green.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Production feedback-loop health gate")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--write-artifact", action="store_true",
                        help="write data/edgelab/operational_health/production_health_gate.json")
    parser.add_argument("--warn-only", action="store_true",
                        help="always exit 0 (for local inspection; never use in CI)")
    parser.add_argument("--root", default=ROOT_DIR)
    args = parser.parse_args(argv)

    state = collect_state(root=args.root)
    results = evaluate_health(state)
    summary = summarize(results)

    payload = {
        "checkedAt": state["now"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary,
        "assertions": results,
        "thresholds": {
            "maxSettlementLagDays": MAX_SETTLEMENT_LAG_DAYS,
            "maxRecommendationLagDays": MAX_RECOMMENDATION_LAG_DAYS,
            "maxModelEvaluationLagDays": MAX_MODEL_EVALUATION_LAG_DAYS,
            "maxSlateLagDays": MAX_SLATE_LAG_DAYS,
            "maxLedgerCommitLagDays": MAX_LEDGER_COMMIT_LAG_DAYS,
            "maxUnexplainedBacklog": MAX_UNEXPLAINED_BACKLOG,
            "backlogGraceDays": BACKLOG_GRACE_DAYS,
            "inactivePipelineDays": INACTIVE_PIPELINE_DAYS,
        },
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_render(results, summary))

    if args.write_artifact:
        out_dir = os.path.join(args.root, "data", "edgelab", "operational_health")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "production_health_gate.json")
        from lib.atomic_json import write_json_atomic
        write_json_atomic(payload, out_path, indent=2)
        print("\nartifact: %s" % os.path.relpath(out_path, args.root))

    if args.warn_only:
        return 0
    return 1 if summary["overall"] == "CRITICAL" else 0


if __name__ == "__main__":
    sys.exit(main())
