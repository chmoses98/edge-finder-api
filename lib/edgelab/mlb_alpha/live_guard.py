"""
lib/edgelab/mlb_alpha/live_guard.py
===================================
Design and safety layer for OPTIONAL live execution.  HARD-DISABLED.

This module contains the controls a live path must pass through -- bankroll sourcing from an
authenticated balance, per-trade / per-game / portfolio limits, price ceilings, idempotency,
order reconciliation and an emergency stop -- so they can be reviewed, tested and frozen BEFORE
anyone is asked to authorise execution.  It performs no I/O of any kind:

  * no network library is imported (a test parses this file's AST to prove it)
  * no environment variable, key, token or credential is read
  * `submit_order` raises LiveExecutionDisabled unconditionally; the only way to a different
    behaviour is to change the code constant below in a reviewed commit, and even then an
    authorisation file naming the exact rule hash is required.

Nothing here is evidence that any strategy is profitable; `ExecutionPolicy.validate` additionally
refuses any strategy whose validation status is not QUALIFIED.
"""

import hashlib
import json
import os

# Changing this constant is a code change that needs explicit approval (CEO authorisation, per the
# program's production firewall).  Tests assert it is False.
LIVE_EXECUTION_CODE_ENABLED = False

AUTHORIZATION_FILE_REQUIRED = "data/edgelab/research_artifacts/mlb_alpha_0003/LIVE_EXECUTION_AUTHORIZATION.json"


class LiveExecutionDisabled(RuntimeError):
    """Raised by every order-submitting entry point while live execution is disabled."""


class RiskLimitBreach(ValueError):
    """An order intent violated a configured limit."""


class BankrollProvider:
    """Interface only.  A real provider would read the AUTHENTICATED account balance; no implementation
    exists in this repository, deliberately.  `PaperBankroll` is the only concrete provider."""

    def available_usd(self):
        raise NotImplementedError


class PaperBankroll(BankrollProvider):
    def __init__(self, usd):
        self.usd = float(usd)

    def available_usd(self):
        return self.usd


class ExecutionPolicy:
    def __init__(self, *, bankroll, per_trade_usd=10.0, per_game_usd=25.0, max_open_usd=100.0,
                 max_daily_loss_usd=50.0, price_ceiling=0.97, price_floor=0.03, max_bankroll_fraction_per_trade=0.02,
                 require_status="QUALIFIED"):
        if not isinstance(bankroll, BankrollProvider):
            raise TypeError("bankroll must be a BankrollProvider")
        self.bankroll = bankroll
        self.per_trade_usd, self.per_game_usd, self.max_open_usd = per_trade_usd, per_game_usd, max_open_usd
        self.max_daily_loss_usd = max_daily_loss_usd
        self.price_ceiling, self.price_floor = price_ceiling, price_floor
        self.max_fraction = max_bankroll_fraction_per_trade
        self.require_status = require_status

    def validate(self, intent, *, open_by_game, open_total, realised_loss_today, validation_status, stop):
        """Raise on any breach; return the intent unchanged when it may proceed (still not submitted)."""
        if stop.is_set():
            raise RiskLimitBreach("EMERGENCY_STOP_SET")
        if validation_status != self.require_status:
            raise RiskLimitBreach("STRATEGY_NOT_%s (is %s)" % (self.require_status, validation_status))
        cash = float(intent["cashUsd"])
        price = float(intent["limitPrice"])
        if not (self.price_floor <= price <= self.price_ceiling):
            raise RiskLimitBreach("PRICE_OUTSIDE_BAND")
        if cash > self.per_trade_usd + 1e-9:
            raise RiskLimitBreach("PER_TRADE_LIMIT")
        if open_by_game.get(intent["game"], 0.0) + cash > self.per_game_usd + 1e-9:
            raise RiskLimitBreach("PER_GAME_LIMIT")
        if open_total + cash > self.max_open_usd + 1e-9:
            raise RiskLimitBreach("PORTFOLIO_LIMIT")
        if realised_loss_today >= self.max_daily_loss_usd:
            raise RiskLimitBreach("DAILY_LOSS_LIMIT")
        bal = self.bankroll.available_usd()
        if cash > bal + 1e-9:
            raise RiskLimitBreach("INSUFFICIENT_BANKROLL")
        if cash > self.max_fraction * bal + 1e-9:
            raise RiskLimitBreach("BANKROLL_FRACTION_LIMIT")
        return intent


class EmergencyStop:
    """File-backed kill switch.  Presence of the file stops everything; there is no code path that deletes it."""

    def __init__(self, path):
        self.path = path

    def is_set(self):
        return os.path.exists(self.path)

    def trigger(self, reason):
        with open(self.path, "w") as fh:
            json.dump({"reason": reason}, fh)


def client_order_id(strategy_rule_hash, run_id, ticker, side):
    """Deterministic idempotency key: re-sending the same decision can never create a second order."""
    return hashlib.sha256(("|".join([strategy_rule_hash, run_id, ticker, side])).encode()).hexdigest()[:32]


def reconcile(intended, reported):
    """
    Compare what we intended with what the exchange reports.  Both are {client_order_id: {contracts, avgPrice}}.
    Returns a dict of discrepancies; an empty dict means the books agree.  Pure function.
    """
    out = {"missing": [], "unexpected": [], "quantityMismatch": [], "priceMismatch": []}
    for k, v in intended.items():
        r = reported.get(k)
        if r is None:
            out["missing"].append(k)
            continue
        if abs(r["contracts"] - v["contracts"]) > 1e-9:
            out["quantityMismatch"].append({"id": k, "intended": v["contracts"], "reported": r["contracts"]})
        if v.get("limitPrice") is not None and r["avgPrice"] is not None and r["avgPrice"] > v["limitPrice"] + 1e-9:
            out["priceMismatch"].append({"id": k, "limit": v["limitPrice"], "filled": r["avgPrice"]})
    for k in reported:
        if k not in intended:
            out["unexpected"].append(k)
    return {k: v for k, v in out.items() if v}


def submit_order(*_args, **_kwargs):
    """The only order-submitting entry point in this package.  It never submits."""
    if not LIVE_EXECUTION_CODE_ENABLED:
        raise LiveExecutionDisabled(
            "live execution is disabled by construction; see docs/EDGELAB_MLB_ALPHA_0003_PROGRAM.md "
            "'Live execution design' for the approval steps that must precede any change")
    raise LiveExecutionDisabled("no exchange client exists in this repository; a separately approved component is required")
