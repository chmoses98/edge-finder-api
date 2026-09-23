"""
Fail-safe budget guard for the MRV sportsbook leg (The Odds API).

The Odds API quota is shared with other project consumers.  The owner
authorised the MRV leg at its designed rate (~430 credits/day) but not
unlimited consumption, so every MRV odds request is gated by:

  1. HARD DAILY CEILING: MRV sportsbook credits consumed on the ET game date
     (summed from the persisted, append-only ledger) plus the expected cost
     of the next request must not exceed DAILY_CREDIT_CEILING (450).
  2. RESERVE: if the freshest provider-reported remaining quota
     (x-requests-remaining, from the ledger) is below RESERVE_REMAINING (5,000)
     no further MRV odds request is made.
  3. EVIDENCE: if the most recent response carried no remaining-quota header,
     the guard cannot prove the reserve holds and degrades.  Only the very
     first request with no ledger history at all is allowed without evidence
     (it is the request that produces the evidence).

A blocked leg is DEGRADED_BUDGET_GUARD with a reason; the Kalshi and MLB legs
of the cycle continue.  Missing sportsbook observations are recorded as
missing, never as zero disagreement.

The ledger lives at <root>/odds_budget/<ET date>.jsonl and is the ONLY source
of the daily spend: a retry, a new runId, a fresh process or a deleted state
file cannot reset the counter.  Credits consumed come from the provider's
x-requests-last header; when that header is absent the documented design
cost (markets x region-equivalents = 3) is charged, never zero.
"""
import json
import os
from datetime import datetime, timedelta

GUARD_VERSION = "MRV_ODDS_BUDGET_GUARD_V1_2026_09_23"
DAILY_CREDIT_CEILING = 450
RESERVE_REMAINING = 5000
DESIGN_COST_PER_REQUEST = 3          # 3 markets (h2h, spreads, totals) x 1 region-equivalent (4 bookmakers)
EVIDENCE_LOOKBACK_DAYS = 7

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED_BUDGET_GUARD"
STATUS_NOT_CONFIGURED = "NOT_CONFIGURED"
STATUS_FETCH_FAILED = "FETCH_FAILED"


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def ledger_path(root, date):
    return os.path.join(root, "odds_budget", date + ".jsonl")


def read_ledger(root, date):
    p = ledger_path(root, date)
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def append_ledger(root, date, row):
    p = ledger_path(root, date)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def spent_today(root, date):
    return sum(int(r.get("creditsCharged") or 0) for r in read_ledger(root, date))


def freshest_evidence(root, date, lookback=EVIDENCE_LOOKBACK_DAYS):
    """Latest ledger row whose request got an HTTP response from the provider, across the last `lookback`
    ET dates; None if none.  A transport failure (no response) carries no quota evidence either way."""
    d0 = datetime.strptime(date, "%Y-%m-%d")
    best = None
    for k in range(lookback):
        day = (d0 - timedelta(days=k)).strftime("%Y-%m-%d")
        for r in read_ledger(root, day):
            if not r.get("requestMade") or r.get("httpStatus") is None:
                continue
            if best is None or (r.get("respondedAt") or "") >= (best.get("respondedAt") or ""):
                best = r
    return best


def expected_cost(evidence):
    last = _int((evidence or {}).get("requestsLast"))
    return max(last, DESIGN_COST_PER_REQUEST) if last is not None else DESIGN_COST_PER_REQUEST


def decide(root, date):
    """
    -> {allowed, status, reason, spentToday, expectedCost, remainingEvidence, evidenceAt, ceiling, reserve}
    """
    spent = spent_today(root, date)
    ev = freshest_evidence(root, date)
    cost = expected_cost(ev)
    remaining = _int((ev or {}).get("requestsRemaining"))
    out = {"guardVersion": GUARD_VERSION, "spentToday": spent, "expectedCost": cost, "remainingEvidence": remaining,
           "evidenceAt": (ev or {}).get("respondedAt"), "ceiling": DAILY_CREDIT_CEILING, "reserve": RESERVE_REMAINING}
    if spent + cost > DAILY_CREDIT_CEILING:
        out.update(allowed=False, status=STATUS_DEGRADED, reason="DAILY_CEILING_REACHED")
    elif ev is not None and remaining is None:
        out.update(allowed=False, status=STATUS_DEGRADED, reason="REMAINING_QUOTA_UNKNOWN")
    elif remaining is not None and remaining < RESERVE_REMAINING:
        out.update(allowed=False, status=STATUS_DEGRADED, reason="REMAINING_BELOW_RESERVE")
    else:
        out.update(allowed=True, status=STATUS_OK, reason=("NO_PRIOR_EVIDENCE_FIRST_REQUEST" if ev is None else None))
    return out


def charge(headers):
    """Credits to record for a request that was actually made: provider x-requests-last, else the design cost."""
    last = _int((headers or {}).get("x-requests-last"))
    return (last if last is not None else DESIGN_COST_PER_REQUEST), ("PROVIDER_HEADER" if last is not None else "DESIGN_COST_FALLBACK")


def post_request_status(headers):
    """Status after a request was made: DEGRADED if the response itself shows the reserve breached or no evidence."""
    rem = _int((headers or {}).get("x-requests-remaining"))
    if rem is None:
        return STATUS_DEGRADED, "REMAINING_QUOTA_UNKNOWN_AFTER_REQUEST"
    if rem < RESERVE_REMAINING:
        return STATUS_DEGRADED, "REMAINING_BELOW_RESERVE_AFTER_REQUEST"
    return STATUS_OK, None
