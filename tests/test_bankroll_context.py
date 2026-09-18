"""
tests/test_bankroll_context.py
==============================
The bankroll a real-money handicap may size against.

The defect these tests exist to prevent is specific and was real: the
previous implementation let this repository's own DERIVED ledger become
the sizing authority whenever its most recent evidence looked fresh. That
ledger's cash history is known incomplete, so `availableBankroll` was
NEGATIVE -- and one freshly-updated wager was enough to stamp that
negative figure FRESH and allow staking against it.

So the tests below are organised around the five ways that can happen:

  * a value from the wrong SOURCE                (derived ledger)
  * a value of the wrong KIND                    (portfolio value)
  * a value that is too OLD                      (24h is not "current")
  * a value that is not a usable NUMBER          (zero, negative, NaN)
  * a value that leaked somewhere PUBLIC         (this repo is public)
"""
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import bankroll_context as bc  # noqa: E402

NOW = datetime(2026, 9, 18, 18, 0, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _published(bankroll=1234.56, minutes_ago=2, **overrides):
    """A context shaped exactly as kalshi-bet-router publishes it."""
    payload = {
        "schemaVersion": "1",
        "bankroll": bankroll,
        "currency": "USD",
        "observedAt": _iso(NOW - timedelta(minutes=minutes_ago)),
        "source": bc.SOURCE_KALSHI_AUTHENTICATED,
        "valueType": bc.VALUE_AVAILABLE_CASH,
    }
    payload.update(overrides)
    return payload


def _load(payload=None, *, env=None, **kwargs):
    environment = dict(env or {})
    if payload is not None:
        environment[bc.BANKROLL_CONTEXT_ENV] = json.dumps(payload)
    return bc.load_bankroll_context(now=NOW, env=environment, **kwargs)


# ═══ 1. authenticated router balance, FRESH -> sizing allowed ═══════════

def test_authenticated_fresh_balance_allows_sizing():
    ctx = _load(_published(bankroll=1234.56, minutes_ago=2))
    assert ctx["sizingAllowed"] is True
    assert ctx["status"] == bc.STATUS_FRESH
    assert ctx["bankroll"] == pytest.approx(1234.56)
    assert ctx["source"] == bc.SOURCE_KALSHI_AUTHENTICATED
    assert ctx["valueType"] == bc.VALUE_AVAILABLE_CASH
    assert ctx["ageMinutes"] == pytest.approx(2, abs=0.01)


def test_the_context_records_the_exact_observation_instant():
    observed = _iso(NOW - timedelta(minutes=7))
    ctx = _load(_published(minutes_ago=7))
    assert ctx["observedAt"] == observed
    assert ctx["maxAgeMinutes"] == bc.DEFAULT_MAX_AGE_MINUTES == 30


def test_a_balance_read_from_a_file_outside_the_repo_is_accepted(tmp_path):
    path = tmp_path / "bankroll.json"
    path.write_text(json.dumps(_published(bankroll=500.0)), encoding="utf-8")
    ctx = bc.load_bankroll_context(
        now=NOW, env={bc.BANKROLL_CONTEXT_PATH_ENV: str(path)})
    assert ctx["sizingAllowed"] is True
    assert ctx["bankroll"] == pytest.approx(500.0)


# ═══ 2. the balance route is READ-ONLY; no trading is enabled ═══════════

def test_nothing_here_can_trade_or_mutate_the_account():
    """This module READS a published number. It holds no credential, makes
    no request, and exposes nothing that could act on the account.

    The producing side's own refusal is pinned in kalshi-bet-router
    (`test_no_mutating_method_exists_on_the_client`,
    `test_balance_allowlist_is_exact_match_not_a_prefix`,
    `TRADING_ROUTES`). This is the destination half: permission to read a
    balance never became permission to place an order HERE either.
    """
    source = open(os.path.join(ROOT, "lib", "bankroll_context.py"), encoding="utf-8").read()
    lowered = source.lower()
    # No way to reach the network, and no credential to reach it with.
    for capability in ("requests.", "urllib.request", "urllib.parse", "http.client",
                       "import socket", "subprocess", "kalshi_private_key",
                       "kalshi_api_key", "kalshi-access-key"):
        assert capability not in lowered, f"{capability!r} must not appear in this module"

    exported = [n for n in dir(bc) if not n.startswith("_")]
    for verb in ("order", "place", "trade", "cancel", "withdraw", "deposit", "transfer"):
        assert not any(verb in n.lower() for n in exported), exported


# ═══ 3. a stale observed balance -> NO sizing ══════════════════════════

@pytest.mark.parametrize("minutes_ago", [31, 60, 6 * 60, 24 * 60, 48 * 60])
def test_a_stale_balance_never_sizes(minutes_ago):
    ctx = _load(_published(minutes_ago=minutes_ago))
    assert ctx["sizingAllowed"] is False
    assert ctx["status"] == bc.STATUS_STALE
    assert "NOT the current bankroll" in ctx["unavailableReason"]


def test_a_twenty_four_hour_old_balance_is_not_current():
    """Named explicitly because it is the exact thing the old 24h window
    called FRESH."""
    ctx = _load(_published(minutes_ago=24 * 60))
    assert ctx["status"] == bc.STATUS_STALE
    assert ctx["sizingAllowed"] is False


def test_the_window_boundary_is_thirty_minutes():
    assert _load(_published(minutes_ago=29))["sizingAllowed"] is True
    assert _load(_published(minutes_ago=30))["sizingAllowed"] is True
    assert _load(_published(minutes_ago=31))["sizingAllowed"] is False


def test_a_future_dated_balance_is_refused_beyond_clock_skew():
    assert _load(_published(minutes_ago=-2))["sizingAllowed"] is True     # skew
    far = _load(_published(minutes_ago=-90))
    assert far["sizingAllowed"] is False
    assert far["bankroll"] is None
    assert "future" in far["unavailableReason"]


# ═══ 4. unavailable -> NO sizing, but the handicap still proceeds ══════

def test_no_secret_at_all_means_no_sizing():
    ctx = bc.load_bankroll_context(now=NOW, env={}, transactions=[], bets=[])
    assert ctx["sizingAllowed"] is False
    assert ctx["bankroll"] is None or ctx["status"] != bc.STATUS_FRESH


def test_the_unavailable_message_tells_the_handicapper_what_to_do():
    ctx = bc.load_bankroll_context(now=NOW, env={}, transactions=[], bets=[])
    line = bc.describe_for_output(ctx)
    assert "UNAVAILABLE" in line
    assert "NO dollar stake sizes" in line


# ═══ 5. a malformed response -> NO sizing ══════════════════════════════

@pytest.mark.parametrize("raw", ["", "   ", "not json", "[1,2,3]", '"a string"', "null", "{"])
def test_malformed_secret_payloads_never_size(raw):
    ctx = bc.load_bankroll_context(
        now=NOW, env={bc.BANKROLL_CONTEXT_ENV: raw}, transactions=[], bets=[])
    assert ctx["sizingAllowed"] is False


@pytest.mark.parametrize("bad", [None, "1234.56", True, float("nan"), float("inf"), [], {}])
def test_a_non_numeric_bankroll_never_sizes(bad):
    ctx = _load(_published(bankroll=bad))
    assert ctx["sizingAllowed"] is False
    assert ctx["bankroll"] is None


def test_a_context_missing_its_observation_timestamp_never_sizes():
    payload = _published()
    payload.pop("observedAt")
    ctx = _load(payload)
    assert ctx["sizingAllowed"] is False
    assert "timestamp" in ctx["unavailableReason"]


def test_a_portfolio_value_is_refused_as_a_sizing_basis():
    """The explicit mission constraint: never substitute mark-to-market
    portfolio value, which would overstate deployable cash."""
    ctx = _load(_published(bankroll=9876.54, valueType="KALSHI_PORTFOLIO_VALUE"))
    assert ctx["sizingAllowed"] is False
    assert ctx["sizingAuthoritativeSource"] is False
    assert "overstates deployable cash" in ctx["unavailableReason"]


def test_a_context_from_an_unknown_source_is_refused():
    ctx = _load(_published(source="some_other_system"))
    assert ctx["sizingAllowed"] is False
    assert "not the authenticated" in ctx["unavailableReason"]


def test_a_malformed_payload_is_never_echoed_into_the_reason():
    """The reason reaches a PUBLIC Actions log. A malformed secret is
    still a balance."""
    ctx = bc.load_bankroll_context(
        now=NOW, env={bc.BANKROLL_CONTEXT_ENV: '{"bankroll": 918273.44,'},
        transactions=[], bets=[])
    assert "918273" not in json.dumps(ctx)


# ═══ 6. an incomplete derived ledger can NEVER become authoritative ════

def _fresh_ledger_rows():
    """A ledger whose only cash record is an old starting balance, but
    whose most recent WAGER was updated seconds ago. This is the exact
    shape that used to be stamped FRESH."""
    transactions = [{
        "type": "STARTING_BALANCE", "amount": 350.0,
        "occurredAt": "2026-08-03T00:00:00Z", "createdAt": "2026-08-03T00:00:00Z",
    }]
    bets = [{
        "id": "bet-1", "trackingType": "REAL", "status": "PENDING", "stake": 426.0,
        "updatedAt": _iso(NOW - timedelta(seconds=30)),
        "recordedAt": _iso(NOW - timedelta(seconds=30)),
    }]
    return transactions, bets


def test_a_recent_wager_timestamp_cannot_make_the_derived_ledger_sizing_authoritative():
    transactions, bets = _fresh_ledger_rows()
    ctx = bc.load_bankroll_context(now=NOW, env={}, transactions=transactions, bets=bets)

    assert ctx["source"] == bc.SOURCE_EDGELAB_LEDGER
    assert ctx["sizingAuthoritativeSource"] is False
    assert ctx["sizingAllowed"] is False
    assert ctx["status"] == bc.STATUS_UNAVAILABLE
    assert "DERIVED_LEDGER_NOT_SIZING_AUTHORITATIVE" in ctx["unavailableReason"]

    # The evidence IS recent -- that is the whole point. Freshness was
    # never the missing ingredient; completeness was.
    assert ctx["diagnostic"]["mostRecentLedgerEvidenceAt"] == bets[0]["updatedAt"]
    assert ctx["diagnostic"]["cashHistoryProvenComplete"] is False


def test_the_derived_ledger_is_retained_as_diagnostic_context():
    transactions, bets = _fresh_ledger_rows()
    ctx = bc.load_bankroll_context(now=NOW, env={}, transactions=transactions, bets=bets)
    diagnostic = ctx["diagnostic"]
    assert "settledBankroll" in diagnostic
    assert "totalExposure" in diagnostic
    assert diagnostic["cashHistoryProvenComplete"] is False
    assert "DIAGNOSTIC ONLY" in diagnostic["note"]


def test_accounting_integrity_concerns_are_reported_on_an_inconsistent_summary():
    """Reported, not acted on: this source cannot size either way, so the
    value of the check is that a reader can see WHY it should be distrusted."""
    ctx = bc.context_from_ledger_summary(
        {"settledBankroll": 350.0, "totalExposure": 426.0, "availableBankroll": -744.32,
         "cashTransactionCount": 1},
        observed_at=_iso(NOW), now=NOW,
    )
    concerns = ctx["diagnostic"]["accountingIntegrityConcerns"]
    assert "availableBankroll is not positive" in concerns
    assert "settledBankroll - totalExposure != availableBankroll" in concerns
    assert ctx["sizingAllowed"] is False


def test_the_derived_ledger_loses_to_the_authenticated_balance():
    transactions, bets = _fresh_ledger_rows()
    ctx = _load(_published(bankroll=2000.0), transactions=transactions, bets=bets)
    assert ctx["source"] == bc.SOURCE_KALSHI_AUTHENTICATED
    assert ctx["bankroll"] == pytest.approx(2000.0)
    assert ctx["sizingAllowed"] is True


def test_an_unreadable_ledger_is_data_not_a_crash():
    class Exploding(list):
        def __iter__(self):
            raise OSError("ledger is gone")

    ctx = bc.load_bankroll_context(now=NOW, env={}, transactions=Exploding(), bets=[])
    assert ctx["sizingAllowed"] is False


# ═══ 7. zero or negative bankroll can NEVER enable sizing ══════════════

@pytest.mark.parametrize("amount", [0, 0.0, -0.01, -1, -744.32, -100000.0])
def test_a_non_positive_bankroll_never_sizes(amount):
    ctx = _load(_published(bankroll=amount))
    assert ctx["sizingAllowed"] is False
    assert ctx["bankroll"] is None
    assert "not a deployable amount" in ctx["unavailableReason"]


def test_a_negative_bankroll_is_refused_even_when_perfectly_fresh():
    """Freshness is not the only gate, and this is the combination that
    would otherwise slip through: a real, authenticated, seconds-old
    reading of a negative number."""
    ctx = _load(_published(bankroll=-744.32, minutes_ago=0))
    assert ctx["status"] == bc.STATUS_UNAVAILABLE
    assert ctx["sizingAllowed"] is False


def test_build_context_cannot_be_told_to_allow_sizing():
    """`sizingAllowed` is derived, never supplied, so no caller can grant
    it to a value that did not earn it."""
    ctx = bc.build_context(
        bankroll=-50.0, source=bc.SOURCE_KALSHI_AUTHENTICATED,
        value_type=bc.VALUE_AVAILABLE_CASH, observed_at=_iso(NOW), now=NOW,
    )
    assert ctx["sizingAllowed"] is False


# ═══ 8. no secret or account metadata is persisted ═════════════════════

def test_the_module_contains_no_hard_coded_dollar_amount():
    """No literal money anywhere: not a default, not a fallback, not an
    example. A number that appears in this file is a number nobody read
    off the account."""
    import re

    source = open(os.path.join(ROOT, "lib", "bankroll_context.py"), encoding="utf-8").read()
    assert not re.search(r"\$\s*[\d,]*\d", source), "a dollar amount literal appears"
    # The only bare numeric literals permitted are the freshness policy
    # constants, which are minutes, not money.
    assert bc.DEFAULT_MAX_AGE_MINUTES == 30
    assert "350" not in source and "1234" not in source


def test_the_committable_projection_withholds_the_amount():
    ctx = _load(_published(bankroll=1234.56))
    public = bc.redacted(ctx)
    assert public["bankroll"] is None
    assert public["bankrollRedacted"] is True
    assert "1234.56" not in json.dumps(public)
    assert "1234" not in json.dumps(public)


def test_the_committable_projection_keeps_everything_needed_to_judge_it():
    ctx = _load(_published(bankroll=1234.56, minutes_ago=3))
    public = bc.redacted(ctx)
    for key in ("status", "sizingAllowed", "observedAt", "ageMinutes",
                "maxAgeMinutes", "source", "valueType"):
        assert key in public, key
    assert public["sizingAllowed"] is True
    assert public["status"] == bc.STATUS_FRESH


def test_the_projection_is_an_allowlist_so_a_new_field_cannot_leak():
    ctx = _load(_published())
    ctx["someFutureSecretField"] = "SECRET-VALUE-1"
    ctx["diagnostic"] = {"accountId": "ACCT-LEAK", "raw": {"balance": 123456}}
    public = bc.redacted(ctx)
    blob = json.dumps(public)
    for secret in ("SECRET-VALUE-1", "ACCT-LEAK", "123456"):
        assert secret not in blob
    assert "diagnostic" not in public


def test_the_printed_line_is_redacted_by_default():
    ctx = _load(_published(bankroll=1234.56))
    assert "1,234.56" not in bc.describe_for_output(ctx)
    assert "$***" in bc.describe_for_output(ctx)


def test_the_printed_line_can_be_revealed_for_a_local_operator_run():
    ctx = _load(_published(bankroll=1234.56))
    assert "$1,234.56" in bc.describe_for_output(ctx, reveal=True)


def test_a_bankroll_path_inside_this_public_repository_is_refused(tmp_path):
    """A bankroll file in the working tree is one `git add` away from
    publishing the owner's balance permanently."""
    inside = os.path.join(ROOT, "data", "router", "bankroll.json")
    payload, reason = bc.load_secret_context(
        env={bc.BANKROLL_CONTEXT_PATH_ENV: inside})
    assert payload is None
    assert "PUBLIC" in reason
    assert "RUNNER_TEMP" in reason


def test_a_bankroll_path_outside_the_repository_is_allowed(tmp_path):
    path = tmp_path / "bankroll.json"
    path.write_text(json.dumps(_published()), encoding="utf-8")
    payload, reason = bc.load_secret_context(env={bc.BANKROLL_CONTEXT_PATH_ENV: str(path)})
    assert reason is None
    assert payload["payload"]["bankroll"] == pytest.approx(1234.56)


# ═══ 9. the card records the bankroll and timestamp it used ════════════
#  (see tests/test_handicapping_card.py -- it builds a real card)


# ── general shape ──────────────────────────────────────────────────────

def test_a_user_reported_balance_is_never_the_sizing_basis():
    transactions, bets = _fresh_ledger_rows()
    ctx = bc.load_bankroll_context(
        now=NOW, env={}, transactions=transactions, bets=bets)
    assert ctx["userReportedBalanceIsInformationalOnly"] is True
    assert ctx["sizingAllowed"] is False


def test_every_context_answers_the_same_four_questions():
    for ctx in (
        _load(_published()),
        _load(_published(minutes_ago=999)),
        bc.load_bankroll_context(now=NOW, env={}, transactions=[], bets=[]),
    ):
        for key in ("bankroll", "status", "sizingAllowed", "observedAt",
                    "ageMinutes", "source", "valueType", "unavailableReason"):
            assert key in ctx, key
        assert isinstance(ctx["sizingAllowed"], bool)


def test_nan_and_infinity_are_not_deployable():
    for value in (float("nan"), float("inf"), float("-inf")):
        amount, reason = bc._usable_amount(value)
        assert amount is None
        assert reason
    assert math.isnan(float("nan"))  # sanity
