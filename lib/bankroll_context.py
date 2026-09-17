"""
lib/bankroll_context.py
===========================
Read-only BANKROLL CONTEXT for MLB handicapping: what the handicapper is
allowed to size against, where that number came from, how old it is, and
whether it is fresh enough to stake on.

WHAT THE ROUTER ACTUALLY PROVIDES (audited, not assumed)
--------------------------------------------------------
`chmoses98/kalshi-bet-router` @ a215e7e2 was read directly. It does NOT
maintain a bankroll or account-balance artifact, and that is a deliberate
design decision rather than a gap:

  * `src/kalshi_router/client.py`'s `READ_ONLY_PATH_PREFIXES` does not
    include `/portfolio/balance`. The client refuses to SIGN a request to
    it before the request is ever built.
  * `tests/test_client.py` pins that refusal three separate times, and
    lists `/portfolio/balance` under `TRADING_ROUTES` with the comment
    "No Kalshi trading endpoint may be implemented -- pinned, not
    assumed."
  * `docs/OPERATIONS.md` ("Privacy") states that "no raw fill payload,
    fill id, subaccount identifier, balance or account metadata is
    persisted anywhere", and `tests/test_privacy.py` asserts that no
    monetary value at all appears in rendered output.
  * What the router DOES emit downstream is an importer payload of
    normalized wager rows, written to `RUNNER_TEMP` and pushed into
    `data/edgelab/bets/bets.jsonl` here -- never account state.

So there is currently no router bankroll to reuse. This module is built
so that the moment one exists it becomes the source with no code change
here: a router-published artifact is looked for FIRST, at a documented,
env-overridable path, and it wins outright when present.

WHAT IS USED IN THE MEANTIME
----------------------------
The bankroll ledger this repository ALREADY has --
`lib/edgelab/bankroll.compute_bankroll_summary()` over
`data/edgelab/bankroll/transactions.jsonl` plus the canonical wager
ledger. No second bankroll authority is created here; this module is an
adapter, not a ledger. It computes nothing about money itself: every
dollar figure comes from that existing canonical function.

That value is honestly labelled for what it is --
`DERIVED_LEDGER_AVAILABLE_BANKROLL`, not an observed Kalshi account
balance -- so a handicapper is never misled about which one they are
sizing against.

HARD RULES
----------
  * No bankroll number is ever hard-coded. There is no literal dollar
    amount anywhere in this module.
  * `userReportedBalance` (a human typing what they think their balance
    is) is surfaced as INFORMATION ONLY and can never be the sizing
    basis. A manually entered number must not pretend to be canonical.
  * A value older than the freshness window is `STALE`, and `STALE`
    means `sizingAllowed=False`. The handicap still proceeds; only the
    STAKING does not.
  * Nothing here writes, fetches, or mutates anything in the router
    repository. It is a pure read of local, already-committed evidence.
"""
import json
import os
from datetime import datetime, timedelta, timezone

# Where a router-published bankroll artifact WOULD be read from, in
# priority order. The env var exists so the router can publish anywhere
# without a change here; the repo-relative path is the conventional
# landing spot for router-delivered state.
ROUTER_BANKROLL_ENV = "KALSHI_ROUTER_BANKROLL_PATH"
ROUTER_BANKROLL_DEFAULT_PATH = os.path.join("data", "router", "bankroll.json")

STATUS_FRESH = "FRESH"
STATUS_STALE = "STALE"
STATUS_UNAVAILABLE = "UNAVAILABLE"

SOURCE_ROUTER = "kalshi-bet-router:published-bankroll-artifact"
SOURCE_EDGELAB_LEDGER = "edge-finder-api:lib/edgelab/bankroll.compute_bankroll_summary"

VALUE_OBSERVED_BALANCE = "OBSERVED_ACCOUNT_BALANCE"
VALUE_DERIVED_LEDGER = "DERIVED_LEDGER_AVAILABLE_BANKROLL"

# How old a bankroll observation may be and still be staked against.
# 24h covers "yesterday's settlement ran, today's slate is being built"
# without ever letting a week-old number size a real wager.
DEFAULT_MAX_AGE_HOURS = 24

REASON_NO_ROUTER_ARTIFACT = (
    "kalshi-bet-router publishes no bankroll/account-balance artifact: its read-only client "
    "refuses /portfolio/balance (pinned by tests/test_client.py) and docs/OPERATIONS.md's "
    "privacy rule forbids persisting balance or account metadata anywhere"
)


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _age_hours(observed_at, now):
    observed = _parse_iso(observed_at)
    if observed is None:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return round((now - observed).total_seconds() / 3600.0, 3)


def build_context(*, bankroll, source, value_type, observed_at, now,
                  max_age_hours=DEFAULT_MAX_AGE_HOURS, unavailable_reason=None,
                  detail=None, user_reported=None):
    """
    Pure. The one bankroll-context shape every consumer reads. Status is
    derived, never passed in, so an UNAVAILABLE value can never be
    labelled FRESH by a careless caller.
    """
    age = _age_hours(observed_at, now)

    if bankroll is None:
        status = STATUS_UNAVAILABLE
        reason = unavailable_reason or "no bankroll value could be resolved"
    elif observed_at is None or age is None:
        status = STATUS_UNAVAILABLE
        reason = "bankroll value carries no usable observation timestamp, so its age cannot be judged"
        bankroll = None
    elif age > max_age_hours:
        status = STATUS_STALE
        reason = f"bankroll observed {age:.1f}h ago, older than the {max_age_hours}h sizing window"
    elif age < -1:
        status = STATUS_UNAVAILABLE
        reason = f"bankroll observation timestamp is {abs(age):.1f}h in the future -- refusing to trust it"
        bankroll = None
    else:
        status = STATUS_FRESH
        reason = None

    return {
        "schemaVersion": "1",
        "bankroll": bankroll,
        "currency": "USD",
        "source": source,
        "valueType": value_type,
        "observedAt": observed_at,
        "ageHours": age,
        "maxAgeHours": max_age_hours,
        "status": status,
        "sizingAllowed": status == STATUS_FRESH,
        "unavailableReason": reason,
        "detail": detail or {},
        # Informational only. A human-typed balance is never the sizing
        # basis -- see this module's docstring.
        "userReportedBalance": user_reported,
        "userReportedBalanceIsInformationalOnly": True,
        "resolvedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ── Source 1 (preferred): a router-published artifact ───────────────────

def load_router_artifact(path=None):
    """
    Read a router-published bankroll artifact, if one exists. Returns
    (payload, reason). `payload` is None whenever nothing usable is
    there -- a missing file, unreadable JSON, or a payload without a
    numeric balance -- always with a reason, never a guess.

    Expected shape (kept minimal on purpose, so the router is free to
    publish a superset):
        {"bankroll"|"availableBalance"|"balance": <number>,
         "observedAt": "<ISO-8601 UTC>",
         "valueType": "OBSERVED_ACCOUNT_BALANCE"}
    """
    resolved = path or os.environ.get(ROUTER_BANKROLL_ENV) or ROUTER_BANKROLL_DEFAULT_PATH
    if not os.path.exists(resolved):
        return None, f"no router bankroll artifact at {resolved!r}: {REASON_NO_ROUTER_ARTIFACT}"
    try:
        with open(resolved, encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError) as exc:
        return None, f"router bankroll artifact at {resolved!r} is unreadable: {exc}"
    if not isinstance(payload, dict):
        return None, f"router bankroll artifact at {resolved!r} is not a JSON object"

    value = None
    for key in ("bankroll", "availableBalance", "balance", "availableCash"):
        if isinstance(payload.get(key), (int, float)) and not isinstance(payload.get(key), bool):
            value = float(payload[key])
            break
    if value is None:
        return None, (
            f"router bankroll artifact at {resolved!r} carries no numeric bankroll/"
            f"availableBalance/balance/availableCash field"
        )
    return {
        "bankroll": value,
        "observedAt": payload.get("observedAt") or payload.get("capturedAt"),
        "valueType": payload.get("valueType") or VALUE_OBSERVED_BALANCE,
        "path": resolved,
        "raw": payload,
    }, None


# ── Source 2 (fallback): this repo's EXISTING canonical ledger ──────────

def ledger_observed_at(transactions, bets):
    """
    Pure. The most recent piece of real evidence behind the derived
    bankroll: the latest cash transaction, or the latest update to a
    REAL wager. None when the ledger holds no dated evidence at all --
    which build_context() then treats as UNAVAILABLE rather than fresh.
    """
    stamps = []
    for txn in transactions or []:
        stamps.append(txn.get("occurredAt"))
        stamps.append(txn.get("createdAt"))
    for bet in bets or []:
        if bet.get("trackingType") not in (None, "REAL"):
            continue
        stamps.append(bet.get("updatedAt"))
        stamps.append(bet.get("recordedAt"))
        stamps.append(bet.get("createdAt"))
    usable = [s for s in stamps if _parse_iso(s) is not None]
    return max(usable) if usable else None


def context_from_ledger_summary(summary, *, observed_at, now, max_age_hours=DEFAULT_MAX_AGE_HOURS,
                                router_reason=None):
    """
    Pure. Wrap the EXISTING canonical bankroll summary (never recomputed
    here) as a bankroll context.

    `availableBankroll` is the sizing field: settled cash and realized
    P&L minus stake already at risk in pending REAL wagers -- i.e. what
    could actually be staked right now without going negative on paper.
    """
    return build_context(
        bankroll=(summary or {}).get("availableBankroll"),
        source=SOURCE_EDGELAB_LEDGER,
        value_type=VALUE_DERIVED_LEDGER,
        observed_at=observed_at,
        now=now,
        max_age_hours=max_age_hours,
        unavailable_reason="the canonical bankroll ledger produced no availableBankroll",
        detail={
            "settledBankroll": (summary or {}).get("settledBankroll"),
            "totalExposure": (summary or {}).get("totalExposure"),
            "availableBankroll": (summary or {}).get("availableBankroll"),
            "pendingRealBetCount": (summary or {}).get("pendingRealBetCount"),
            "settledRealBetCount": (summary or {}).get("settledRealBetCount"),
            "cashTransactionCount": (summary or {}).get("cashTransactionCount"),
            "sizingField": "availableBankroll",
            "routerArtifactReason": router_reason,
            "note": (
                "DERIVED from this repository's canonical bankroll ledger, not an observed "
                "Kalshi account balance. kalshi-bet-router does not publish one -- see "
                "lib/bankroll_context.py's module docstring for the audit."
            ),
        },
        user_reported=(summary or {}).get("userReportedBalance"),
    )


# ── The one entry point ─────────────────────────────────────────────────

def load_bankroll_context(*, now=None, max_age_hours=DEFAULT_MAX_AGE_HOURS,
                          router_path=None, transactions=None, bets=None):
    """
    Resolve the bankroll a handicap may size against.

    Order: a router-published artifact if one exists, otherwise this
    repository's existing canonical bankroll ledger. Never a literal, and
    never a human-typed balance.

    `transactions`/`bets` may be injected (tests, or a caller that has
    already loaded them); when omitted they are read from the canonical
    paths.
    """
    now = now or datetime.now(tz=timezone.utc)

    artifact, router_reason = load_router_artifact(router_path)
    if artifact is not None:
        return build_context(
            bankroll=artifact["bankroll"],
            source=SOURCE_ROUTER,
            value_type=artifact["valueType"],
            observed_at=artifact["observedAt"],
            now=now,
            max_age_hours=max_age_hours,
            detail={"artifactPath": artifact["path"], "sizingField": "bankroll"},
        )

    try:
        from lib.edgelab import bankroll as bankroll_ledger
        from lib.edgelab import storage

        if transactions is None:
            transactions = list(storage.read_records(
                storage.singleton_path("bankroll", "transactions.jsonl")))
        if bets is None:
            bets = list(storage.read_records(storage.singleton_path("bets", "bets.jsonl")))
        summary = bankroll_ledger.compute_bankroll_summary(transactions, bets)
    except Exception as exc:  # noqa: BLE001 -- an unreadable ledger is data, not a crash
        return build_context(
            bankroll=None, source=SOURCE_EDGELAB_LEDGER, value_type=VALUE_DERIVED_LEDGER,
            observed_at=None, now=now, max_age_hours=max_age_hours,
            unavailable_reason=f"canonical bankroll ledger could not be read: {type(exc).__name__}: {exc}",
            detail={"routerArtifactReason": router_reason},
        )

    return context_from_ledger_summary(
        summary, observed_at=ledger_observed_at(transactions, bets), now=now,
        max_age_hours=max_age_hours, router_reason=router_reason,
    )


def describe_for_output(context):
    """
    Pure. The one line a handicap must print so the bankroll actually
    used for sizing is never ambiguous.
    """
    if not context or context.get("status") == STATUS_UNAVAILABLE:
        return (
            "BANKROLL: UNAVAILABLE — stake sizing is NOT current. "
            f"Reason: {(context or {}).get('unavailableReason', 'no bankroll context')}"
        )
    if context["status"] == STATUS_STALE:
        return (
            f"BANKROLL: ${context['bankroll']:,.2f} but STALE "
            f"({context['ageHours']:.1f}h old, window {context['maxAgeHours']}h) — "
            f"do NOT size real-money stakes against it. Source: {context['source']}"
        )
    return (
        f"BANKROLL: ${context['bankroll']:,.2f} ({context['valueType']}, "
        f"{context['ageHours']:.1f}h old) — sizing allowed. Source: {context['source']}"
    )
