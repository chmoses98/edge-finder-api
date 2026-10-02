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

   Short gaps are schedule-aware too. Freshness is measured against the
   most recent date that was NOT a verified MLB off-day (MLB schedule
   evidence collected by scripts/ci/mlb_schedule_probe.py, regular season +
   every postseason round). Without that, the 3-10 days after the World
   Series, a gap between postseason rounds or the All-Star break made every
   partition look stale and the gate CRITICAL each morning. Evidence that
   is missing, failed or does not cover a date never skips that date, so
   the gate falls back to plain calendar days (fail closed); and the LAST
   real game day must still be fully settled, recommended and evaluated.

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

#: The canonical placed-bet ledger every settlement and CLV pass writes.
CANONICAL_LEDGER_PATH = "data/edgelab/bets/bets.jsonl"

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

# PROD-9. The router is dispatched every ~20 minutes by its own conductor; a
# day without a completed delivery run means the capture half of the system
# has stopped, however healthy everything downstream of it looks.
MAX_ROUTER_SILENCE_HOURS = 24
#: The importBatchId every router-delivered MLB row carries.
ROUTER_IMPORT_BATCH_ID = "kalshi-router-v1"

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


def _canonical_router_rows(root):
    """(count, newest gameDate) of router-delivered rows on the canonical ledger."""
    path = os.path.join(root, CANONICAL_LEDGER_PATH)
    count, newest = 0, None
    if not os.path.exists(path):
        return 0, None
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("importBatchId") != ROUTER_IMPORT_BATCH_ID:
                continue
            count += 1
            date = row.get("gameDate")
            if isinstance(date, str) and _DATE_RE.match(date):
                newest = max(newest or date, date[:10])
    return count, newest


def _load_schedule_evidence(path):
    if not path:
        return None
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        return {"fetchError": "schedule evidence unreadable: %s" % exc}


def _load_router_evidence(path):
    if not path:
        return None
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        return {"fetchError": "router evidence unreadable: %s" % exc}


def collect_state(root=None, now=None, router_evidence_path=None, schedule_evidence_path=None):
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
                if row.get("classification") not in payload.get("acknowledgedClassifications", []):
                    continue
                bet_id = row.get("betId")
                # WAVE 0.07. Never let a falsy id into this set. 30 rows of the
                # root ledger carry no `id` at all, so they classify with
                # betId=None; if any ONE of them were ever acknowledged, `None`
                # would enter this set and the membership test below
                # (`bet.get("id") in acknowledged`) would then silently
                # acknowledge EVERY id-less row at once -- 30 unexplained bets
                # vanishing from the gate on the strength of a single
                # classification. Skipping them keeps the gate strictly
                # stricter: an id-less row can never be excluded, so it always
                # counts until it is given a stable identity.
                if bet_id:
                    acknowledged.add(bet_id)
        except (ValueError, OSError):
            acknowledged = set()

    router_rows, router_newest = _canonical_router_rows(root)

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
        # The CANONICAL ledger. `bets.json` at the repo root is the legacy
        # model-bet ledger (docs/CANONICAL_BET_LEDGER.md): since 2026-09-18 it
        # only changes when write_pending_bets.py adds a model row, so its
        # commit date measured that script's output, not whether settlement
        # persisted -- and held PROD-5 red for ten days while every settlement
        # and CLV pass was landing on data/edgelab/bets/bets.jsonl.
        "betsLedgerCommitDate": _git_last_commit_date(root, CANONICAL_LEDGER_PATH),
        "legacyBetsJsonCommitDate": _git_last_commit_date(root, "bets.json"),
        "settlementsCommitDate": _git_last_commit_date(root, "data/edgelab/settlements"),
        "bets": bets,
        "acknowledgedUnresolvableBetIds": acknowledged,
        "canonicalRouterRowCount": router_rows,
        "canonicalRouterNewestGameDate": router_newest,
        "routerEvidence": _load_router_evidence(router_evidence_path),
        "scheduleEvidence": _load_schedule_evidence(schedule_evidence_path),
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


def verified_off_days(evidence):
    """
    Pure. The set of ISO dates the MLB schedule evidence (see
    scripts/ci/mlb_schedule_probe.py) PROVES had no playable game. Missing,
    failed or malformed evidence proves nothing -> empty set, so every
    assertion keeps its calendar-day behaviour (fail closed). Dates outside
    the evidence window are never off-days.
    """
    if not isinstance(evidence, dict) or evidence.get("fetchError"):
        return frozenset()
    counts = evidence.get("playableGamesByDate")
    start, end = evidence.get("start"), evidence.get("end")
    if not isinstance(counts, dict) or not isinstance(start, str) or not isinstance(end, str):
        return frozenset()
    off = set()
    for day, n in counts.items():
        if not isinstance(day, str) or not _DATE_RE.match(day) or len(day) != 10:
            continue
        if not (start <= day <= end):
            continue
        if isinstance(n, int) and not isinstance(n, bool) and n == 0:
            off.add(day)
    return frozenset(off)


def _effective_lag(now, date_str, limit, off_days):
    """
    Pure. The lag the freshness rule `lag > limit -> FAIL` should see once
    verified off-days are skipped.

    The calendar rule is "the partition must cover now - limit". Here the
    required date is walked back from now - limit over consecutive VERIFIED
    off-days to the most recent date that was not one (a real game day, or
    a date the evidence does not cover). The returned lag is
    `limit + (required - latest)`, so FAIL iff latest < required. With no
    off-days it is exactly the calendar lag.
    """
    lag = _lag_days(now, date_str)
    if lag is None or not off_days:
        return lag
    required = now.date() - timedelta(days=limit)
    while required.isoformat() in off_days:
        required -= timedelta(days=1)
    latest = datetime.strptime(date_str, "%Y-%m-%d").date()
    return limit + (required - latest).days


def _skipped_off_days(now, date_str, off_days):
    """Pure. Verified off-days strictly after `date_str`, up to today -- for
    the report only."""
    if not date_str or not off_days:
        return []
    return sorted(d for d in off_days if date_str < d <= now.date().isoformat())


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
    off_days = verified_off_days(state.get("scheduleEvidence"))

    # The CALENDAR lag decides "inactive for > INACTIVE_PIPELINE_DAYS"
    # (unchanged); every freshness LIMIT is judged on the off-day-aware lag.
    slate_calendar_lag = _lag_days(now, state.get("slateDate"))
    pipeline_active = (slate_calendar_lag is not None
                       and slate_calendar_lag <= INACTIVE_PIPELINE_DAYS)
    slate_lag = _effective_lag(now, state.get("slateDate"), MAX_SLATE_LAG_DAYS, off_days)
    slate_off_days = _skipped_off_days(now, state.get("slateDate"), off_days)

    # PROD-1 -- the production pipeline itself is producing slates.
    # Also the switch that suspends every downstream freshness assertion, so
    # this gate stays quiet in the offseason instead of crying every day.
    if slate_calendar_lag is None:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, FAIL,
            "data/meta.json carries no usable slate date -- cannot establish whether "
            "the production pipeline is running at all",
            {"slateDate": state.get("slateDate")}))
    elif slate_calendar_lag > INACTIVE_PIPELINE_DAYS:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, NOT_APPLICABLE,
            "Production pipeline inactive for %d days (last slate %s) -- treating as "
            "offseason/paused; downstream freshness assertions suspended"
            % (slate_calendar_lag, state.get("slateDate")),
            {"slateDate": state.get("slateDate"), "lagDays": slate_calendar_lag}))
    elif slate_lag > MAX_SLATE_LAG_DAYS:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, FAIL,
            "Slate pipeline stale: last slate %s is %d days old (limit %d%s)"
            % (state.get("slateDate"), slate_calendar_lag, MAX_SLATE_LAG_DAYS,
               "; %d verified MLB off-day(s) skipped" % len(slate_off_days)
               if slate_off_days else ""),
            {"slateDate": state.get("slateDate"), "lagDays": slate_calendar_lag,
             "effectiveLagDays": slate_lag, "verifiedOffDays": slate_off_days}))
    elif slate_calendar_lag > MAX_SLATE_LAG_DAYS:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, PASS,
            "No MLB game day missed: last slate %s (%d calendar day(s) old), and the MLB "
            "schedule shows no playable game on %d day(s) since (%s) -- off-day/offseason "
            "gap, not an outage"
            % (state.get("slateDate"), slate_calendar_lag, len(slate_off_days),
               ", ".join(slate_off_days)),
            {"slateDate": state.get("slateDate"), "lagDays": slate_calendar_lag,
             "effectiveLagDays": slate_lag, "verifiedOffDays": slate_off_days}))
    else:
        results.append(_result(
            "PROD-1", CRITICAL_PRODUCTION, PASS,
            "Slate pipeline active: last slate %s (%d day(s) old)"
            % (state.get("slateDate"), slate_calendar_lag),
            {"slateDate": state.get("slateDate"), "lagDays": slate_calendar_lag}))

    # PROD-2 -- settlement partitions advancing. THE assertion that would have
    # caught the outage on 2026-09-03, its second day.
    settlement_lag = _effective_lag(now, state.get("settlementLatest"),
                                    MAX_SETTLEMENT_LAG_DAYS, off_days)
    results.append(_freshness_assertion(
        "PROD-2", CRITICAL_PRODUCTION, "Settlement corpus",
        state.get("settlementLatest"), settlement_lag,
        MAX_SETTLEMENT_LAG_DAYS, pipeline_active))

    # PROD-3 -- recommendation ledger advancing.
    rec_lag = _effective_lag(now, state.get("recommendationLatest"),
                             MAX_RECOMMENDATION_LAG_DAYS, off_days)
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
    eval_lag = _effective_lag(now, state.get("modelEvaluationLatest"),
                              MAX_MODEL_EVALUATION_LAG_DAYS, off_days)
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
    commit_lag = _effective_lag(now, state.get("betsLedgerCommitDate"),
                                MAX_LEDGER_COMMIT_LAG_DAYS, off_days)
    if not pipeline_active:
        results.append(_result("PROD-5", CRITICAL_PRODUCTION, NOT_APPLICABLE,
                               "Ledger persistence not evaluated: pipeline inactive"))
    elif commit_lag is None:
        results.append(_result(
            "PROD-5", CRITICAL_PRODUCTION, FAIL,
            "Cannot determine when the canonical ledger %s was last committed (no git history?)"
            % CANONICAL_LEDGER_PATH,
            {"betsLedgerCommitDate": state.get("betsLedgerCommitDate")}))
    elif commit_lag > MAX_LEDGER_COMMIT_LAG_DAYS:
        results.append(_result(
            "PROD-5", CRITICAL_PRODUCTION, FAIL,
            "Canonical ledger %s has not been committed for %d days (limit %d, "
            "last commit %s) while the slate pipeline is active -- settlement output is "
            "being computed and discarded rather than persisted"
            % (CANONICAL_LEDGER_PATH, commit_lag, MAX_LEDGER_COMMIT_LAG_DAYS,
               state.get("betsLedgerCommitDate")),
            {"betsLedgerCommitDate": state.get("betsLedgerCommitDate"),
             "legacyBetsJsonCommitDate": state.get("legacyBetsJsonCommitDate"),
             "lagDays": commit_lag}))
    else:
        results.append(_result(
            "PROD-5", CRITICAL_PRODUCTION, PASS,
            "Ledger persisted recently: %s last committed %s (%d day(s) ago)"
            % (CANONICAL_LEDGER_PATH, state.get("betsLedgerCommitDate"), commit_lag),
            {"lagDays": commit_lag,
             "legacyBetsJsonCommitDate": state.get("legacyBetsJsonCommitDate")}))

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

    results.append(_router_divergence(state, now))

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


def _router_divergence(state, now):
    """PROD-9 -- the router holds MLB wagers the canonical ledger does not.

    WHY: 2026-09-24..28, twelve real MLB wagers sat on the router's proposal
    branch for up to 4.7 days while every assertion above passed -- each of
    them can only see the ledger this repository HAS. The router is the only
    party holding authenticated fills, so its own delivery runs are the
    evidence (scripts/ci/router_divergence_probe.py collects them).

    Judged on the SETTLED run -- the newest one at least a few hours older than
    the latest -- because a fill normally reaches main two router runs after it
    is captured, and a divergence that young is latency, not a defect. The
    LATEST run is used only to prove the router is still running.

    Not conditioned on the slate pipeline: the router records what was PLACED,
    and a wager placed during a slate outage is exactly the one to not lose.
    """
    evidence = state.get("routerEvidence")
    canonical = state.get("canonicalRouterNewestGameDate")
    if evidence is None:
        return _result("PROD-9", CRITICAL_PRODUCTION, NOT_APPLICABLE,
                       "Router divergence not evaluated: no router evidence supplied "
                       "(the workflow collects it; a local run does not)")
    if evidence.get("fetchError"):
        return _result("PROD-9", CRITICAL_PRODUCTION, FAIL,
                       "Cannot observe kalshi-bet-router's delivery runs, so wagers the "
                       "account placed cannot be proven to be on this ledger: %s"
                       % evidence["fetchError"], {"evidence": evidence})

    problems = []
    latest = evidence.get("latest") or {}
    latest_at = _parse_utc(latest.get("createdAt"))
    if latest_at is None:
        problems.append("the router's newest delivery run has no timestamp")
    else:
        silent_hours = (now - latest_at).total_seconds() / 3600.0
        if silent_hours > MAX_ROUTER_SILENCE_HOURS:
            problems.append("the router's newest completed delivery run is %.0f hours old "
                            "(limit %d) -- capture has stopped"
                            % (silent_hours, MAX_ROUTER_SILENCE_HOURS))

    judged = evidence.get("settled")
    if judged is None:
        problems.append("no router delivery run old enough to judge (history shorter than "
                        "%s hours)" % evidence.get("settleHours"))
        judged = {}
    coverage = judged.get("coverage")
    reconciliation = judged.get("reconciliation")
    if coverage is None:
        problems.append("router run %s printed no MLB coverage line" % judged.get("runId"))
    else:
        if coverage.get("refusedProvably"):
            problems.append("%d order(s) whose every leg is MLB were REFUSED by the router "
                            "and are not on this ledger" % coverage["refusedProvably"])
        router_newest = coverage.get("newestGameDate")
        if router_newest and (canonical is None or router_newest > canonical):
            problems.append("router has authenticated MLB fills for game date %s, newer than "
                            "the newest canonical router-delivered MLB wager (%s)"
                            % (router_newest, canonical))
    if reconciliation is None:
        problems.append("router run %s printed no MLB reconciliation line -- the MLB "
                        "delivery did not complete" % judged.get("runId"))
    else:
        stuck = (reconciliation.get("proposed_not_merged", 0) + reconciliation.get("refused", 0)
                 + reconciliation.get("unaccounted", 0))
        if stuck:
            problems.append("%d router-eligible MLB wager(s) not on main (proposed not merged "
                            "%d, refused %d, unaccounted %d)"
                            % (stuck, reconciliation.get("proposed_not_merged", 0),
                               reconciliation.get("refused", 0),
                               reconciliation.get("unaccounted", 0)))

    detail = {"latestRun": latest.get("url"), "judgedRun": judged.get("url"),
              "routerCoverage": coverage, "routerReconciliation": reconciliation,
              "canonicalRouterNewestGameDate": canonical,
              "canonicalRouterRowCount": state.get("canonicalRouterRowCount")}
    if problems:
        return _result("PROD-9", CRITICAL_PRODUCTION, FAIL,
                       "ROUTER/LEDGER DIVERGENCE: " + "; ".join(problems), detail)
    return _result("PROD-9", CRITICAL_PRODUCTION, PASS,
                   "Every authenticated MLB wager the router holds is on the canonical "
                   "ledger (router newest game date %s, canonical %s, %s eligible)"
                   % ((coverage or {}).get("newestGameDate"), canonical,
                      (reconciliation or {}).get("eligible")), detail)


def _parse_utc(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


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
    parser.add_argument("--router-evidence", default=None,
                        help="JSON written by scripts/ci/router_divergence_probe.py (PROD-9)")
    parser.add_argument("--schedule-evidence", default=None,
                        help="JSON written by scripts/ci/mlb_schedule_probe.py (off-day-aware "
                             "freshness; absent/failed = calendar days)")
    args = parser.parse_args(argv)

    state = collect_state(root=args.root, router_evidence_path=args.router_evidence,
                          schedule_evidence_path=args.schedule_evidence)
    results = evaluate_health(state)
    summary = summarize(results)

    payload = {
        "checkedAt": state["now"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": summary,
        "assertions": results,
        "verifiedOffDays": sorted(verified_off_days(state.get("scheduleEvidence"))),
        "thresholds": {
            "maxSettlementLagDays": MAX_SETTLEMENT_LAG_DAYS,
            "maxRecommendationLagDays": MAX_RECOMMENDATION_LAG_DAYS,
            "maxModelEvaluationLagDays": MAX_MODEL_EVALUATION_LAG_DAYS,
            "maxSlateLagDays": MAX_SLATE_LAG_DAYS,
            "maxLedgerCommitLagDays": MAX_LEDGER_COMMIT_LAG_DAYS,
            "maxUnexplainedBacklog": MAX_UNEXPLAINED_BACKLOG,
            "backlogGraceDays": BACKLOG_GRACE_DAYS,
            "inactivePipelineDays": INACTIVE_PIPELINE_DAYS,
            "maxRouterSilenceHours": MAX_ROUTER_SILENCE_HOURS,
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
