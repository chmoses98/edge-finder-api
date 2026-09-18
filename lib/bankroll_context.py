"""
lib/bankroll_context.py
=======================
The bankroll a real-money handicap may size against: where it came from,
how old it is, and — far more often than is comfortable — why it may not
be used at all.

THE ONE SIZING AUTHORITY
------------------------
Exactly one thing may set stake sizes: the **authenticated Kalshi
account balance**, read by `chmoses98/kalshi-bet-router` and delivered
here. Everything else in this module is diagnostic context.

That is a change of posture, and it was forced by a real defect. The
previous version let this repository's own derived ledger become the
sizing authority whenever its evidence looked recent. Its cash
transaction history is **known incomplete** — one `STARTING_BALANCE` and
no deposits since — so `availableBankroll` was negative, and yet a single
freshly-updated wager was enough to stamp that negative number `FRESH`
and allow sizing against it. A recent timestamp on an incomplete ledger
is not freshness; it is a recent row.

So:

* the router-published authenticated balance is the **only** source that
  can produce ``sizingAllowed: true``;
* the derived ledger is retained under ``diagnostic`` and can **never**
  size, regardless of how fresh it looks;
* a bankroll that is absent, zero, negative, non-finite, unparseable or
  older than the freshness window produces no stake sizes at all.

**A handicap may always proceed.** Only *staking* stops.

HOW THE NUMBER GETS HERE
------------------------
Both `chmoses98/edge-finder-api` and `chmoses98/kalshi-bet-router` are
**public repositories**. On a public repo, workflow logs, job summaries
and uploaded artifacts are readable by anyone. So the balance cannot be
committed here, cannot be printed into a log here, and cannot arrive as
an artifact.

It arrives as an **encrypted GitHub Actions secret**
(``KALSHI_BANKROLL_CONTEXT``), sealed by the router against this
repository's Actions public key and decrypted only inside a workflow run
of this repository. The card build reads it from the environment, sizes
against it, and **commits a redacted card**: status, observation time,
age, source, semantic type and ``sizingAllowed`` — never the amount.

See `docs/BANKROLL_CONTEXT.md`, and
`kalshi-bet-router/docs/BANKROLL_DELIVERY.md` for the producing half.

WHAT THE NUMBER MEANS
---------------------
``KALSHI_AVAILABLE_CASH_BALANCE`` — Kalshi's ``balance`` field, the
account's **available cash**. Deliberately *not* ``portfolio_value``,
which is the mark-to-market value of open positions and would
double-count exposure already at risk. The producing side refuses to
substitute it; this side refuses to accept any other ``valueType`` as a
sizing basis.

FRESHNESS
---------
**30 minutes.** A balance is a live quantity: a fill, a settlement or a
deposit moves it, and any of those can happen between two slates. A
24-hour-old reading is not the current bankroll, and calling it current
for real-money staking is the failure this window exists to prevent.
"""
import json
import math
import os
from datetime import datetime, timezone

#: The sealed secret, as an environment variable holding the JSON context.
BANKROLL_CONTEXT_ENV = "KALSHI_BANKROLL_CONTEXT"
#: Or a path to it. The path must resolve OUTSIDE this repository (see
#: `_path_is_inside_repo`) so the number cannot be committed by accident.
BANKROLL_CONTEXT_PATH_ENV = "KALSHI_BANKROLL_CONTEXT_PATH"

STATUS_FRESH = "FRESH"
STATUS_STALE = "STALE"
STATUS_UNAVAILABLE = "UNAVAILABLE"

#: The only source that may set a stake size.
SOURCE_KALSHI_AUTHENTICATED = "kalshi_authenticated_balance"
#: Retained as diagnostic context. Never sizing-authoritative.
SOURCE_EDGELAB_LEDGER = "edge-finder-api:lib/edgelab/bankroll.compute_bankroll_summary"

#: The only value type that may set a stake size.
VALUE_AVAILABLE_CASH = "KALSHI_AVAILABLE_CASH_BALANCE"
VALUE_DERIVED_LEDGER = "DERIVED_LEDGER_AVAILABLE_BANKROLL"

SIZING_ELIGIBLE_SOURCES = frozenset({SOURCE_KALSHI_AUTHENTICATED})
SIZING_ELIGIBLE_VALUE_TYPES = frozenset({VALUE_AVAILABLE_CASH})

#: A balance is a live quantity. See the module docstring.
DEFAULT_MAX_AGE_MINUTES = 30

#: Tolerance for clock skew between the router's runner and this one. A
#: reading from slightly "the future" is a clock difference; a reading from
#: materially the future is a broken producer and is refused.
FUTURE_SKEW_TOLERANCE_MINUTES = 5

#: What a CONSUMER may actually do, which is not the same question as
#: whether a bankroll exists. See `dollar_sizing_verdict`.
SIZING_PERMITTED = "DOLLAR_SIZING_PERMITTED"
SIZING_NO_NUMBER = "NO_DOLLAR_SIZING_FOR_THIS_CONSUMER"
SIZING_NOT_AUTHORISED = "NO_DOLLAR_SIZING"

REASON_REDACTED_FOR_CONSUMER = (
    "NO_DOLLAR_SIZING_FOR_THIS_CONSUMER: an authenticated, fresh bankroll EXISTS and the "
    "private workflow that produced this artifact was permitted to size against it -- but its "
    "numeric value is redacted from this copy, because this repository is public. You do not "
    "know the amount, so you cannot compute a dollar stake. Handicap normally, give edge, "
    "confidence and a bet-up-to fraction, and say that the bankroll exists but is redacted "
    "from this consumer."
)

REASON_DERIVED_LEDGER = (
    "DERIVED_LEDGER_NOT_SIZING_AUTHORITATIVE: this is a figure derived from this "
    "repository's own ledger, whose cash transaction history is not proven complete. "
    "A recent wager timestamp does not make an incomplete ledger current. Retained as "
    "diagnostic context only; real-money stake sizing requires the authenticated "
    "Kalshi account balance published by kalshi-bet-router."
)
REASON_NO_SECRET = (
    f"no authenticated Kalshi balance was supplied. The router publishes it as the "
    f"encrypted Actions secret {BANKROLL_CONTEXT_ENV}; this process did not receive it. "
    f"Handicap normally and present NO dollar stake sizes."
)


def _parse_iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_minutes(observed_at, now):
    observed = _parse_iso(observed_at)
    if observed is None:
        return None
    return round((now - observed).total_seconds() / 60.0, 3)


def _usable_amount(value):
    """
    Pure. ``(amount, reason)``. A bankroll that cannot be staked against
    is not a small bankroll, it is no bankroll.

    Rejected, each because sizing against it would be a real-money error
    rather than a small one:

    * ``None`` / non-numeric / ``bool``  -- there is no number here
    * ``NaN`` / ``inf``                  -- arithmetic on it silently poisons
                                            every stake it touches
    * ``<= 0``                           -- nothing can be deployed; a
                                            NEGATIVE value is worse than
                                            useless, because a percentage of
                                            it is a negative stake
    """
    if value is None:
        return None, "no bankroll value was supplied"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"bankroll value was {type(value).__name__}, expected a number"
    number = float(value)
    if not math.isfinite(number):
        return None, "bankroll value is not a finite number"
    if number <= 0:
        return None, (
            f"bankroll is {number:.2f}, which is not a deployable amount -- refusing to "
            f"produce stake sizes against a zero or negative bankroll"
        )
    return number, None


def build_context(*, bankroll, source, value_type, observed_at, now,
                  max_age_minutes=DEFAULT_MAX_AGE_MINUTES, unavailable_reason=None,
                  diagnostic=None, user_reported=None):
    """
    Pure. The one bankroll-context shape every consumer reads.

    ``status`` and ``sizingAllowed`` are **derived here**, never passed
    in, so no caller can label a value it did not earn. Sizing requires
    all of:

      1. an authenticated-balance source AND an available-cash value type
      2. a usable amount (finite, strictly positive)
      3. a parseable observation timestamp
      4. an age inside the freshness window, and not in the future
    """
    amount, amount_reason = _usable_amount(bankroll)
    age = _age_minutes(observed_at, now)
    authoritative = (
        source in SIZING_ELIGIBLE_SOURCES and value_type in SIZING_ELIGIBLE_VALUE_TYPES
    )

    if amount is None:
        status, reason = STATUS_UNAVAILABLE, (unavailable_reason or amount_reason)
    elif not authoritative:
        # The value is real and may be worth reading. It is simply not
        # allowed to set a stake size.
        status, reason = STATUS_UNAVAILABLE, (unavailable_reason or REASON_DERIVED_LEDGER)
    elif observed_at is None or age is None:
        status = STATUS_UNAVAILABLE
        reason = "bankroll value carries no usable observation timestamp, so its age cannot be judged"
        amount = None
    elif age < -FUTURE_SKEW_TOLERANCE_MINUTES:
        status = STATUS_UNAVAILABLE
        reason = (
            f"bankroll observation timestamp is {abs(age):.1f} minutes in the future -- "
            f"refusing to trust it"
        )
        amount = None
    elif age > max_age_minutes:
        status = STATUS_STALE
        reason = (
            f"bankroll observed {age:.1f} minutes ago, older than the "
            f"{max_age_minutes}-minute sizing window -- this is NOT the current bankroll"
        )
    else:
        status, reason = STATUS_FRESH, None

    sizing_allowed = status == STATUS_FRESH and authoritative and amount is not None
    context = {
        "schemaVersion": "3",
        "bankroll": amount,
        "currency": "USD",
        "source": source,
        "valueType": value_type,
        "observedAt": observed_at,
        "ageMinutes": age,
        "maxAgeMinutes": max_age_minutes,
        "status": status,
        "sizingAuthoritativeSource": authoritative,
        # THE AUTHORITY QUESTION: was a fresh, authenticated bankroll
        # available to whoever produced this?
        "sizingAllowed": sizing_allowed,
        # THE VISIBILITY QUESTION, WHICH IS NOT THE SAME ONE: is the
        # numeric amount in the hands of whoever is reading this? A
        # workflow runner holds it; a chat session reading the committed
        # public artifact does not. Conflating the two lets a redacted
        # card imply that dollar stakes can be computed from it.
        "numericBankrollAvailable": amount is not None,
        "unavailableReason": reason,
        "diagnostic": diagnostic or {},
        # Informational only. A human-typed balance is never a sizing basis.
        "userReportedBalance": user_reported,
        "userReportedBalanceIsInformationalOnly": True,
        "resolvedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    context["consumerSizingVerdict"] = dollar_sizing_verdict(context)
    return context


def dollar_sizing_verdict(context):
    """
    Pure. What may the CONSUMER HOLDING THIS OBJECT actually present?

    Dollar stake sizing requires BOTH, and neither implies the other:

      1. a fresh, sizing-authoritative bankroll  (`sizingAllowed`)
      2. the numeric amount in this reader's hands
         (`numericBankrollAvailable`)

    ``sizingAllowed: true`` means the PRIVATE WORKFLOW was permitted to
    use the balance. It does NOT mean every downstream reader knows the
    amount. A committed card on a public repository is exactly the case
    where (1) holds and (2) does not, and a consumer that reads only (1)
    will invent dollar figures it has no basis for.

    Fails closed: anything unrecognised is ``NO_DOLLAR_SIZING``.
    """
    context = context or {}
    # `is True`, not truthiness: a string "yes", a non-empty dict or a 1 from
    # some future producer must not buy sizing permission. This gate is
    # exactly where a sloppy value becomes a real-money stake.
    if context.get("sizingAllowed") is not True:
        return {
            "verdict": SIZING_NOT_AUTHORISED,
            "mayPresentDollarStakes": False,
            "reason": context.get("unavailableReason") or "no sizing-authoritative bankroll",
        }
    if context.get("numericBankrollAvailable") is not True:
        return {
            "verdict": SIZING_NO_NUMBER,
            "mayPresentDollarStakes": False,
            "reason": REASON_REDACTED_FOR_CONSUMER,
        }
    return {
        "verdict": SIZING_PERMITTED,
        "mayPresentDollarStakes": True,
        "reason": None,
    }


# ── Source 1 (the only sizing authority): the sealed router context ─────

def _path_is_inside_repo(path, repo_root=None):
    """
    True when `path` lives inside this repository's working tree.

    Used to REFUSE such a path outright. This repository is public; a
    bankroll file inside the tree is one ``git add -A`` away from being a
    permanent public record of the owner's balance. Making that
    impossible is better than remembering not to do it.
    """
    root = os.path.abspath(repo_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        return os.path.commonpath([os.path.abspath(path), root]) == root
    except ValueError:  # different drives on Windows
        return False


def load_secret_context(*, env=None, repo_root=None):
    """
    Read the router-published bankroll context. Returns
    ``(payload, reason)`` — ``payload`` is None whenever nothing usable
    arrived, always with a reason and never a guess.

    Two shapes, both of which the router's workflow can produce:

    * ``KALSHI_BANKROLL_CONTEXT``      the JSON itself (the sealed secret,
                                       decrypted into the environment)
    * ``KALSHI_BANKROLL_CONTEXT_PATH`` a path to it, which **must** resolve
                                       outside this repository
    """
    source_env = os.environ if env is None else env

    raw = (source_env.get(BANKROLL_CONTEXT_ENV) or "").strip()
    origin = BANKROLL_CONTEXT_ENV
    if not raw:
        path = (source_env.get(BANKROLL_CONTEXT_PATH_ENV) or "").strip()
        if not path:
            return None, REASON_NO_SECRET
        if _path_is_inside_repo(path, repo_root):
            return None, (
                f"{BANKROLL_CONTEXT_PATH_ENV} points inside this repository ({path!r}). "
                f"This repository is PUBLIC; a bankroll file in the working tree is one "
                f"'git add' away from publishing the owner's balance permanently. Refusing "
                f"to read it -- use a path under RUNNER_TEMP."
            )
        if not os.path.exists(path):
            return None, f"{BANKROLL_CONTEXT_PATH_ENV} points at {path!r}, which does not exist"
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        except OSError as exc:
            return None, f"bankroll context at {path!r} is unreadable: {type(exc).__name__}"
        origin = f"{BANKROLL_CONTEXT_PATH_ENV}={path}"

    try:
        payload = json.loads(raw)
    except ValueError:
        # The reason must never echo the payload: on a public runner the
        # log is public, and a malformed secret is still a balance.
        return None, f"bankroll context from {origin} is not valid JSON"
    if not isinstance(payload, dict):
        return None, f"bankroll context from {origin} is not a JSON object"
    return {"payload": payload, "origin": origin}, None


def context_from_secret(payload, *, now, max_age_minutes=DEFAULT_MAX_AGE_MINUTES, origin=None):
    """
    Pure. Turn the router's published context into this module's shape.

    Strict about `valueType`. The router publishes available cash and
    says so; anything else — above all a portfolio mark-to-market value —
    is accepted as *context* and refused as a *sizing basis*, because it
    would overstate deployable cash.
    """
    value_type = payload.get("valueType")
    unavailable = None
    if value_type not in SIZING_ELIGIBLE_VALUE_TYPES:
        unavailable = (
            f"bankroll context declares valueType {value_type!r}, which is not an "
            f"available-cash figure. Refusing to size against it: a portfolio "
            f"mark-to-market value overstates deployable cash."
        )
    source = payload.get("source")
    if source not in SIZING_ELIGIBLE_SOURCES and unavailable is None:
        unavailable = (
            f"bankroll context declares source {source!r}, which is not the authenticated "
            f"Kalshi balance. Refusing to size against it."
        )
    return build_context(
        bankroll=payload.get("bankroll"),
        source=source if source in SIZING_ELIGIBLE_SOURCES else (source or "UNKNOWN"),
        value_type=value_type if value_type in SIZING_ELIGIBLE_VALUE_TYPES else (value_type or "UNKNOWN"),
        observed_at=payload.get("observedAt"),
        now=now,
        max_age_minutes=max_age_minutes,
        unavailable_reason=unavailable,
        diagnostic={"origin": origin, "producerSchemaVersion": payload.get("schemaVersion")},
    )


# ── Source 2 (diagnostic ONLY): this repo's derived ledger ──────────────

def ledger_observed_at(transactions, bets):
    """
    Pure. The most recent piece of evidence behind the derived figure.

    Reported, but note what it is NOT: evidence that the ledger is
    complete. A wager updated a minute ago says nothing about whether
    last week's deposit was ever recorded, which is exactly why this
    source can no longer authorise sizing.
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


def context_from_ledger_summary(summary, *, observed_at, now,
                                max_age_minutes=DEFAULT_MAX_AGE_MINUTES,
                                secret_reason=None):
    """
    Pure. Wrap the existing canonical bankroll summary as **diagnostic**
    context. It can never size: ``SOURCE_EDGELAB_LEDGER`` is not in
    ``SIZING_ELIGIBLE_SOURCES``, so ``build_context`` refuses it
    structurally rather than by a flag someone could flip.
    """
    summary = summary or {}
    settled = summary.get("settledBankroll")
    exposure = summary.get("totalExposure")
    available = summary.get("availableBankroll")

    # An accounting-integrity note, reported rather than acted on: this
    # source cannot size either way, so the value of the check is that a
    # reader can see WHY the derived figure should not be trusted.
    integrity = []
    if isinstance(available, (int, float)) and available <= 0:
        integrity.append("availableBankroll is not positive")
    if all(isinstance(v, (int, float)) for v in (settled, exposure, available)):
        if abs((settled - exposure) - available) > 0.01:
            integrity.append("settledBankroll - totalExposure != availableBankroll")
    if not summary.get("cashTransactionCount"):
        integrity.append("no cash transactions recorded")

    return build_context(
        bankroll=available,
        source=SOURCE_EDGELAB_LEDGER,
        value_type=VALUE_DERIVED_LEDGER,
        observed_at=observed_at,
        now=now,
        max_age_minutes=max_age_minutes,
        unavailable_reason=REASON_DERIVED_LEDGER,
        diagnostic={
            "settledBankroll": settled,
            "totalExposure": exposure,
            "availableBankroll": available,
            "pendingRealBetCount": summary.get("pendingRealBetCount"),
            "settledRealBetCount": summary.get("settledRealBetCount"),
            "cashTransactionCount": summary.get("cashTransactionCount"),
            "mostRecentLedgerEvidenceAt": observed_at,
            "accountingIntegrityConcerns": integrity,
            "cashHistoryProvenComplete": False,
            "authenticatedBalanceReason": secret_reason,
            "note": (
                "DIAGNOSTIC ONLY. Derived from recorded transactions and graded wagers, "
                "not read from the Kalshi account. It cannot authorise stake sizing at "
                "any age."
            ),
        },
        user_reported=summary.get("userReportedBalance"),
    )


# ── The one entry point ─────────────────────────────────────────────────

def load_bankroll_context(*, now=None, max_age_minutes=DEFAULT_MAX_AGE_MINUTES,
                          env=None, transactions=None, bets=None, repo_root=None):
    """
    Resolve the bankroll a handicap may size against.

    The authenticated Kalshi balance if it arrived; otherwise the derived
    ledger as **diagnostic context with sizing refused**. Never a
    literal, never a human-typed balance, and never a derived figure
    promoted because it happened to look recent.
    """
    now = now or datetime.now(tz=timezone.utc)

    secret, secret_reason = load_secret_context(env=env, repo_root=repo_root)
    if secret is not None:
        return context_from_secret(
            secret["payload"], now=now, max_age_minutes=max_age_minutes,
            origin=secret["origin"],
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
            observed_at=None, now=now, max_age_minutes=max_age_minutes,
            unavailable_reason=(
                f"no authenticated Kalshi balance, and the diagnostic ledger could not be "
                f"read either: {type(exc).__name__}"
            ),
            diagnostic={"authenticatedBalanceReason": secret_reason},
        )

    return context_from_ledger_summary(
        summary, observed_at=ledger_observed_at(transactions, bets), now=now,
        max_age_minutes=max_age_minutes, secret_reason=secret_reason,
    )


# ── Publishing: what may be committed, and what may only be used ────────

#: Fields safe to commit to a PUBLIC repository. The amount is not one.
REDACTED_FIELDS = (
    "schemaVersion", "currency", "source", "valueType", "observedAt", "ageMinutes",
    "maxAgeMinutes", "status", "sizingAuthoritativeSource", "sizingAllowed",
    "unavailableReason", "resolvedAt",
)


def redacted(context):
    """
    Pure. The committable projection of a bankroll context.

    An allowlist, not a deletion pass: a field added to the context later
    cannot leak by being forgotten here. Everything a reader needs to
    trust or distrust the sizing survives — status, age, window, source,
    semantics, ``sizingAllowed`` — and the amount does not.

    ``diagnostic`` is dropped wholesale: on the derived-ledger path it
    carries dollar figures, and although those are already committed
    elsewhere in this repository, re-publishing them beside a bankroll
    field is exactly how a redaction stops meaning anything.
    """
    if not context:
        return {"status": STATUS_UNAVAILABLE, "sizingAllowed": False, "bankrollRedacted": True}
    out = {key: context.get(key) for key in REDACTED_FIELDS if key in context}
    out["bankroll"] = None
    out["bankrollRedacted"] = True
    # REDACTING THE NUMBER REVOKES THE READER'S ABILITY TO SIZE WITH IT.
    #
    # `sizingAllowed` survives, and it should: it is the true statement
    # that a fresh authenticated bankroll existed and the producing
    # workflow was permitted to use it. But a reader of THIS object does
    # not hold the amount, and a reader that checks only `sizingAllowed`
    # will invent dollar figures. So visibility is set false here and the
    # verdict is recomputed from the redacted object, not inherited.
    out["numericBankrollAvailable"] = False
    out["consumerSizingVerdict"] = dollar_sizing_verdict(out)
    out["bankrollRedactionReason"] = (
        "This repository is PUBLIC. The numeric balance is delivered to the card build as "
        "an encrypted Actions secret and is used for sizing, but is never committed. "
        "Everything needed to judge whether that sizing is trustworthy is above -- but NOT "
        "the amount, so a reader of this file cannot compute dollar stakes. See "
        "consumerSizingVerdict."
    )
    return out


def describe_for_output(context, *, reveal=False):
    """
    Pure. The one line an output must carry so the bankroll actually used
    for sizing is never ambiguous.

    **Redacted by default.** This runs inside a public repository's
    Actions logs; ``reveal=True`` is for a local operator run only.
    """
    if not context or context.get("status") == STATUS_UNAVAILABLE:
        return (
            "BANKROLL: UNAVAILABLE — handicap normally, but present NO dollar stake sizes. "
            f"Reason: {(context or {}).get('unavailableReason', 'no bankroll context')}"
        )

    verdict = (context.get("consumerSizingVerdict")
               or dollar_sizing_verdict(context))
    if verdict["verdict"] == SIZING_NO_NUMBER:
        age = context.get("ageMinutes")
        age_text = f"{age:.1f} min old" if isinstance(age, (int, float)) else "age unknown"
        return (
            f"BANKROLL: EXISTS and is {context['status']} ({age_text}, observed "
            f"{context.get('observedAt')}) but its VALUE IS REDACTED from this consumer — "
            f"give edge, confidence and bet-up-to fractions, and present NO dollar stake "
            f"sizes. Source: {context['source']}"
        )

    amount = (
        f"${context['bankroll']:,.2f}" if reveal and context.get("bankroll") is not None
        else "$***"
    )
    age = context.get("ageMinutes")
    age_text = f"{age:.1f} min old" if isinstance(age, (int, float)) else "age unknown"

    if context["status"] == STATUS_STALE:
        return (
            f"BANKROLL: {amount} but STALE ({age_text}, window "
            f"{context['maxAgeMinutes']} min) — do NOT size real-money stakes against it. "
            f"Source: {context['source']}"
        )
    return (
        f"BANKROLL: {amount} ({context['valueType']}, {age_text}, observed "
        f"{context.get('observedAt')}) — sizing allowed. Source: {context['source']}"
    )
