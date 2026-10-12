#!/usr/bin/env python3
"""Betting value scorecard: calibrated probabilities against executable Kalshi quotes at or before as_of.

Read-only research tooling, vendored alongside ``pure_gate.py``. Standard library only, Python 3.10+. It places
no orders, reads no live venue, and changes no wagering, staking or routing code. It answers one question about
a recorded history: would buying at the executable price available at the forecast's as_of, after Kalshi fees
and slippage, have made money, with the uncertainty that the sample allows?

Rules it enforces:

* Entry uses only quotes observed AT OR BEFORE as_of (never later), on a market already open at as_of.
* YES is bought at the YES ask; NO is bought at the NO ask, which is 1 - YES bid ("100 - bid" in cents).
* Fees: Kalshi's quadratic fee, round_up(coefficient x multiplier x C x P x (1 - P)) to the cent, taker
  coefficient 0.07 by default (maker 0.0175), the same constants as SIFT's src/opportunity/pricing.ts and
  nfl-edge-finder's fee schedule.
* Correlated evidence is aggregated: one decision per (sport, game, player, statistic), chosen by a declared
  rule that sees only as_of information (CandidateView). Outcomes are joined after selection. Bootstrap CIs
  resample whole games (optionally players).
* Closing-line value uses only quotes after as_of and at or before the market's close / kickoff. It is a
  diagnostic and never enters the verdict.
* The verdict is computed against preregistered minimums. A market-informed probability source can only ever
  be NOT_INDEPENDENT_EVIDENCE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Any, Callable, Iterable

VERSION = "value_scorecard 1.0.0"
IDENTITY = ("sport", "game_id", "player_id", "statistic")
TINY = 1e-9

# Kalshi fee constants. Same values as SIFT src/opportunity/pricing.ts (KALSHI_TAKER_RATE = 0.07) and
# nfl-edge-finder nfl_edge/execution/fees.py (taker_base_coefficient 0.07, maker_base_coefficient 0.0175).
KALSHI_TAKER_COEFFICIENT = Decimal("0.07")
KALSHI_MAKER_COEFFICIENT = Decimal("0.0175")
CENT = Decimal("0.01")
TAKER, MAKER = "taker", "maker"
YES, NO = "YES", "NO"

VERDICTS = ("NOT_INDEPENDENT_EVIDENCE", "INSUFFICIENT_SAMPLE", "NO_EVIDENCE_OF_VALUE",
            "POSITIVE_BUT_INCONCLUSIVE", "VALIDATED_VALUE")
DEFAULT_THRESHOLDS = {"min_decisions": 200, "min_games": 40, "min_roi_ci_lower": 0.0}
DEFAULT_EDGE_BANDS = (-0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, 0.20)
DNP_POLICIES = ("settle_by_actual", "void")
AS_OF_POLICIES = ("earliest", "latest", "unique")


class ScorecardError(ValueError):
    """Input that cannot be evaluated honestly. The scorecard fails closed."""


class HindsightError(ScorecardError):
    """A selection rule tried to use information that was not available at as_of."""


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for n, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                val = json.loads(line, parse_constant=_reject_constant)
            except ValueError as exc:
                raise ScorecardError(f"{path}:{n}: invalid JSON: {exc}") from exc
            if not isinstance(val, dict):
                raise ScorecardError(f"{path}:{n}: expected one JSON object per line")
            rows.append(val)
    return rows


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON constant {token}")


def parse_ts(value: Any, desc: str = "timestamp") -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ScorecardError(f"{desc}: timezone required")
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value:
        raise ScorecardError(f"{desc} must be an ISO-8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScorecardError(f"{desc}: invalid ISO-8601 timestamp {value!r}") from exc
    if result.tzinfo is None:
        raise ScorecardError(f"{desc}: timezone required in {value!r}")
    return result.astimezone(timezone.utc)


def opt_ts(value: Any, desc: str = "timestamp") -> datetime | None:
    return None if value in (None, "") else parse_ts(value, desc)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def num(value: Any, desc: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScorecardError(f"{desc} must be numeric")
    v = float(value)
    if not math.isfinite(v):
        raise ScorecardError(f"{desc} must be finite")
    return v


def identity(row: dict) -> tuple[str, str, str, str]:
    for f in IDENTITY:
        v = row.get(f)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and f == "player_id":
            v = str(v)
        if not isinstance(v, str) or not v.strip():
            raise ScorecardError(f"{f} must be a nonempty string")
    return tuple(str(row[f]) for f in IDENTITY)  # type: ignore[return-value]


def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None else round(float(x), nd)


def _strike(x: float) -> float:
    return round(float(x), 9)


# ---------------------------------------------------------------------------------------------
# Fees and prices
# ---------------------------------------------------------------------------------------------

def _dec(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def kalshi_fee(price: Any, contracts: Any = 1, *, role: str = TAKER, multiplier: Any = 1,
               taker_coefficient: Any = KALSHI_TAKER_COEFFICIENT,
               maker_coefficient: Any = KALSHI_MAKER_COEFFICIENT, rounding: str = "cent") -> Decimal:
    """Kalshi's fee in dollars for one order of ``contracts`` at ``price`` (dollars per contract):

        round_up(coefficient x multiplier x C x P x (1 - P)) to the next cent

    with coefficient 0.07 for takers and 0.0175 for makers (only on series that charge maker fees). Exact
    decimal arithmetic. ``rounding="none"`` returns the raw quadratic fee. For whole-cent prices, Kalshi's newer
    fixed-point rounding (trade fee to $0.000001 plus a per-order rounding accumulator, see nfl-edge-finder
    nfl_edge/execution/fees.py) is never more expensive than the per-order cent round-up, so this is the
    conservative estimate there."""
    if role not in (TAKER, MAKER):
        raise ScorecardError(f"role must be {TAKER!r} or {MAKER!r}")
    p, c = _dec(price), _dec(contracts)
    if c <= 0:
        raise ScorecardError("contracts must be positive")
    if p <= 0 or p >= 1:
        return Decimal("0")
    coef = _dec(taker_coefficient if role == TAKER else maker_coefficient)
    raw = coef * _dec(multiplier) * c * p * (Decimal(1) - p)
    if rounding == "none":
        return raw
    if rounding != "cent":
        raise ScorecardError("rounding must be 'cent' or 'none'")
    return (raw / CENT).quantize(Decimal("1"), rounding=ROUND_CEILING) * CENT


class FeeModel:
    def __init__(self, role: str = TAKER, contracts: int = 1, multiplier: float = 1.0, slippage: float = 0.0,
                 tick: float = 0.01, rounding: str = "cent"):
        if role not in (TAKER, MAKER):
            raise ScorecardError(f"role must be {TAKER!r} or {MAKER!r}")
        if contracts < 1:
            raise ScorecardError("contracts must be >= 1")
        if slippage < 0:
            raise ScorecardError("slippage must be >= 0")
        if not 0 < tick < 1:
            raise ScorecardError("tick must be in (0, 1)")
        self.role, self.contracts, self.multiplier = role, int(contracts), float(multiplier)
        self.slippage, self.tick, self.rounding = float(slippage), float(tick), rounding
        self._grid: list[tuple[float, float]] | None = None

    def fee_per_contract(self, price: float) -> float:
        fee = kalshi_fee(_dec(round(price, 6)), self.contracts, role=self.role, multiplier=self.multiplier,
                         rounding=self.rounding)
        return float(fee / self.contracts)

    def cost(self, quoted_price: float) -> tuple[float, float, float] | None:
        """(executed price, fee per contract, all-in cost per contract) or None when not executable."""
        px = quoted_price + self.slippage
        if not 0.0 < px < 1.0:
            return None
        fee = self.fee_per_contract(px)
        return px, fee, px + fee

    def bet_up_to(self, p: float) -> float | None:
        """Highest quoted price on the tick grid at which buying has EV >= 0 after slippage and fees."""
        if self._grid is None:
            steps = int(round(1.0 / self.tick))
            grid = []
            for i in range(1, steps):
                x = round(i * self.tick, 6)
                c = self.cost(x)
                if c is not None:
                    grid.append((x, c[2]))
            self._grid = grid
        best = None
        for x, cost in self._grid:
            if p - cost >= -TINY:
                best = x
        return best

    def describe(self) -> dict:
        coef = KALSHI_TAKER_COEFFICIENT if self.role == TAKER else KALSHI_MAKER_COEFFICIENT
        return {"role": self.role, "coefficient": str(coef), "multiplier": self.multiplier,
                "contracts_per_order": self.contracts, "rounding": self.rounding,
                "slippage_per_contract": self.slippage, "tick": self.tick,
                "formula": "round_up(coefficient x multiplier x C x P x (1-P)) to $0.01, per order, / C",
                "price_policy": ("entry at the executable ask for the side (YES ask; NO ask = 1 - YES bid) plus "
                                 "slippage; role only selects the fee coefficient")}


def quote_prices(q: dict) -> tuple[float | None, float | None]:
    """(yes_bid, yes_ask) in dollars from a market-family quote row."""
    if "yes_bid_cents" in q or "yes_ask_cents" in q:
        scale, fields = 0.01, ("yes_bid_cents", "yes_ask_cents")
    else:
        unit = q.get("price_unit", "dollars")
        if unit not in ("dollars", "cents"):
            raise ScorecardError(f"price_unit must be dollars or cents, got {unit!r}")
        scale, fields = (0.01 if unit == "cents" else 1.0), ("yes_bid", "yes_ask")
    out = []
    for f in fields:
        v = q.get(f)
        if v in (None, ""):
            out.append(None)
            continue
        if isinstance(v, str):
            try:
                v = float(v)
            except ValueError as exc:
                raise ScorecardError(f"quote {f} not numeric: {v!r}") from exc
        x = num(v, f) * scale
        if not -TINY <= x <= 1.0 + TINY:
            raise ScorecardError(f"quote {f}={x} outside [0, 1] dollars")
        out.append(round(x, 6))
    bid, ask = out
    if bid is not None and ask is not None and bid > ask + TINY:
        raise ScorecardError(f"crossed quote: yes_bid {bid} > yes_ask {ask}")
    return bid, ask


def side_price(side: str, bid: float | None, ask: float | None) -> float | None:
    if side == YES:
        return ask if ask is not None and 0.0 < ask < 1.0 else None
    if bid is None or not 0.0 < bid < 1.0:
        return None
    return round(1.0 - bid, 6)


def side_mid(side: str, bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    mid = (bid + ask) / 2.0
    return mid if side == YES else 1.0 - mid


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------

def probabilities_from_forecasts(rows: Iterable[dict]) -> list[dict]:
    """Probability rows from pure_forecast rows: one per ladder rung, P(actual >= at_least).

    A row conditional on playing with a participation probability is converted to an unconditional
    probability (pp x p for at_least > 0). Without one it stays conditional and is labelled so."""
    out = []
    for r in rows:
        pp = r.get("participation_probability")
        cond = bool(r.get("conditional_on_playing"))
        for t in (r.get("projection") or {}).get("thresholds") or []:
            p = float(t["probability"])
            basis = "unconditional"
            if cond:
                if pp is not None and float(t["at_least"]) > 0:
                    p, basis = float(pp) * p, "unconditional_from_participation_probability"
                elif pp is None:
                    basis = "conditional_on_playing"
            out.append({"sport": r["sport"], "game_id": r["game_id"], "player_id": r["player_id"],
                        "statistic": r["statistic"], "at_least": float(t["at_least"]), "as_of": r["as_of"],
                        "kickoff": r.get("kickoff"), "p_yes": p, "probability_basis": basis,
                        "model_version": r.get("model_version"),
                        "market_informed": r.get("projection_mode") not in (None, "PURE_INDEPENDENT")})
    return out


class Rung:
    __slots__ = ("ident", "at_least", "as_of", "kickoff", "p_yes", "row")

    def __init__(self, ident, at_least, as_of, kickoff, p_yes, row):
        self.ident, self.at_least, self.as_of, self.kickoff, self.p_yes, self.row = (
            ident, at_least, as_of, kickoff, p_yes, row)


def parse_probabilities(rows: Iterable[dict]) -> tuple[dict[tuple, Rung], Counter]:
    """Index by (identity, at_least, as_of). Accepts p_yes, or side + probability (NO is converted)."""
    out: dict[tuple, Rung] = {}
    flags = Counter()
    for i, r in enumerate(rows, 1):
        try:
            ident = identity(r)
            t = _strike(num(r.get("at_least"), "at_least"))
            as_of = parse_ts(r.get("as_of"), "as_of")
            kickoff = opt_ts(r.get("kickoff"), "kickoff")
            if kickoff is not None and not as_of < kickoff:
                raise ScorecardError("as_of must be before kickoff")
            if "p_yes" in r:
                p = num(r["p_yes"], "p_yes")
            elif "probability" in r and r.get("side") in (YES, NO):
                p = num(r["probability"], "probability")
                p = p if r["side"] == YES else 1.0 - p
            else:
                raise ScorecardError("need p_yes, or side (YES/NO) and probability")
            if not 0.0 <= p <= 1.0:
                raise ScorecardError("probability outside [0, 1]")
        except ScorecardError as exc:
            raise ScorecardError(f"probability row {i}: {exc}") from exc
        if r.get("market_informed") is True:
            flags["market_informed_rows"] += 1
        if r.get("probability_basis") == "conditional_on_playing":
            flags["conditional_on_playing_rows"] += 1
        k = ident + (t, iso(as_of))
        prev = out.get(k)
        if prev is not None:
            if abs(prev.p_yes - p) > 1e-6:
                raise ScorecardError(f"conflicting probabilities for {k}")
            continue
        out[k] = Rung(ident, t, as_of, kickoff, p, r)
    return out, flags


class Quote:
    __slots__ = ("ident", "at_least", "observed_at", "open_time", "close_time", "bid", "ask", "ticker",
                 "market", "order", "kickoff")

    def __init__(self, row: dict, order: int):
        sport = row.get("sport")
        pid = row.get("player_id")
        if pid in (None, ""):
            raise ScorecardError("player_id missing")
        for f in ("game_id", "statistic"):
            if not isinstance(row.get(f), str) or not row[f]:
                raise ScorecardError(f"{f} must be a nonempty string")
        self.ident = (sport, row["game_id"], str(pid), row["statistic"])
        self.at_least = _strike(num(row.get("at_least"), "at_least"))
        obs = row.get("observed_at") or row.get("row_observed_at") or row.get("board_observed_at") \
            or row.get("quoted_at")
        self.observed_at = parse_ts(obs, "quote observed_at")
        self.open_time = opt_ts(row.get("open_time"), "open_time")
        self.close_time = opt_ts(row.get("close_time"), "close_time")
        self.kickoff = opt_ts(row.get("kickoff"), "kickoff")
        self.bid, self.ask = quote_prices(row)
        self.ticker = row.get("ticker")
        series = row.get("market") or row.get("series") or row.get("series_ticker")
        if not series and isinstance(self.ticker, str) and "-" in self.ticker:
            series = self.ticker.split("-")[0]
        self.market = series or "UNKNOWN"
        self.order = order


class QuoteBook:
    def __init__(self, rows: Iterable[dict]):
        self.by_rung: dict[tuple, list[Quote]] = defaultdict(list)
        self.n = 0
        for i, r in enumerate(rows):
            try:
                q = Quote(r, i)
            except ScorecardError as exc:
                raise ScorecardError(f"quote row {i + 1}: {exc}") from exc
            self.by_rung[q.ident + (q.at_least,)].append(q)
            self.n += 1
        for v in self.by_rung.values():
            v.sort(key=lambda q: (q.observed_at, str(q.ticker or ""), q.order))

    def quotes(self, ident: tuple, at_least: float) -> list[Quote]:
        return self.by_rung.get(ident + (at_least,)) or self.by_rung.get((None,) + ident[1:] + (at_least,)) or []

    def entry(self, rung: Rung, max_age_s: float | None, counts: Counter) -> Quote | None:
        """Latest quote observed at or before as_of on a market open at as_of (never a later quote)."""
        qs = self.quotes(rung.ident, rung.at_least)
        if not qs:
            counts["rungs_without_any_quote"] += 1
            return None
        best = None
        later = 0
        for q in qs:
            if q.observed_at > rung.as_of:
                later += 1
                continue
            if q.open_time is not None and q.open_time > rung.as_of:
                continue
            if q.close_time is not None and q.close_time <= rung.as_of:
                continue
            best = q
        if later:
            counts["quotes_after_as_of_ignored_for_entry"] += later
        if best is None:
            counts["rungs_without_quote_at_or_before_as_of"] += 1
            return None
        if max_age_s is not None and (rung.as_of - best.observed_at).total_seconds() > max_age_s:
            counts["rungs_with_only_stale_quote"] += 1
            return None
        assert best.observed_at <= rung.as_of  # the no-later-quote invariant
        return best

    def closing(self, rung: Rung, entry: Quote) -> tuple[Quote | None, datetime | None]:
        """Latest quote after as_of and at or before min(kickoff, close_time) on the same ticker if known."""
        cut_candidates = [t for t in (rung.kickoff, entry.kickoff, entry.close_time) if t is not None]
        if not cut_candidates:
            return None, None
        cutoff = min(cut_candidates)
        best = None
        for q in self.quotes(rung.ident, rung.at_least):
            if entry.ticker and q.ticker and q.ticker != entry.ticker:
                continue
            if rung.as_of < q.observed_at <= cutoff:
                best = q
        return best, cutoff


def parse_outcomes(rows: Iterable[dict]) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for i, r in enumerate(rows, 1):
        try:
            k = identity(r)
            if not isinstance(r.get("played"), bool):
                raise ScorecardError("played must be boolean")
            num(r.get("actual"), "actual")
        except ScorecardError as exc:
            raise ScorecardError(f"outcome row {i}: {exc}") from exc
        if k in out:
            raise ScorecardError(f"duplicate outcome {k}")
        out[k] = r
    return out


# ---------------------------------------------------------------------------------------------
# Candidates (as_of information only) and selection rules
# ---------------------------------------------------------------------------------------------

_VIEW_FIELDS = ("sport", "game_id", "player_id", "statistic", "at_least", "side", "as_of", "probability",
                "p_yes", "yes_bid", "yes_ask", "spread", "quoted_price", "executed_price", "fee_per_contract",
                "cost_per_contract", "edge_before_fees", "edge_after_fees", "expected_roi", "bet_up_to",
                "quote_observed_at", "quote_age_seconds", "ticker", "market")


class CandidateView:
    """Everything a selection rule may see: the forecast and the executable quote at as_of. Immutable. It holds
    no outcome, no settlement, no later quote and no closing line; asking for anything else raises
    HindsightError."""
    __slots__ = _VIEW_FIELDS + ("_frozen",)

    def __init__(self, **kw):
        for f in _VIEW_FIELDS:
            object.__setattr__(self, f, kw[f])
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name, value):
        raise HindsightError("CandidateView is immutable")

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise HindsightError(f"selection rules see as_of information only; {name!r} is not available "
                             f"(fields: {', '.join(_VIEW_FIELDS)})")

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in _VIEW_FIELDS}


def _tiebreak(v: CandidateView) -> tuple:
    return (v.at_least, 0 if v.side == YES else 1)


def rule_max_edge_after_fees(min_edge: float = 0.0) -> Callable[[list[CandidateView]], CandidateView | None]:
    """The candidate with the largest EV per contract after fees and slippage, if it is > 0 and >= min_edge.
    Ties: lower strike, then YES."""
    def rule(cands: list[CandidateView]) -> CandidateView | None:
        ok = [c for c in cands if c.edge_after_fees > TINY and c.edge_after_fees >= min_edge - TINY]
        if not ok:
            return None
        return min(ok, key=lambda c: (-round(c.edge_after_fees, 9),) + _tiebreak(c))
    rule.__name__ = f"max_edge_after_fees(min_edge={min_edge:g})"
    return rule


def rule_max_expected_roi(min_edge: float = 0.0) -> Callable[[list[CandidateView]], CandidateView | None]:
    """The candidate with the largest expected ROI (EV / cost) among those with EV > 0 and >= min_edge."""
    def rule(cands: list[CandidateView]) -> CandidateView | None:
        ok = [c for c in cands if c.edge_after_fees > TINY and c.edge_after_fees >= min_edge - TINY]
        if not ok:
            return None
        return min(ok, key=lambda c: (-round(c.expected_roi, 9),) + _tiebreak(c))
    rule.__name__ = f"max_expected_roi(min_edge={min_edge:g})"
    return rule


def rule_nearest_even_rung(min_edge: float = 0.0) -> Callable[[list[CandidateView]], CandidateView | None]:
    """Fix the rung whose model P(YES) is closest to 0.5 (model-only choice), then take the better side there
    if its EV is > 0 and >= min_edge."""
    def rule(cands: list[CandidateView]) -> CandidateView | None:
        if not cands:
            return None
        rung = min({c.at_least: c.p_yes for c in cands}.items(), key=lambda kv: (abs(kv[1] - 0.5), kv[0]))[0]
        here = [c for c in cands if c.at_least == rung
                and c.edge_after_fees > TINY and c.edge_after_fees >= min_edge - TINY]
        return min(here, key=lambda c: (-round(c.edge_after_fees, 9),) + _tiebreak(c)) if here else None
    rule.__name__ = f"nearest_even_rung(min_edge={min_edge:g})"
    return rule


RULES = {"max_edge_after_fees": rule_max_edge_after_fees, "max_expected_roi": rule_max_expected_roi,
         "nearest_even_rung": rule_nearest_even_rung}


def select_decisions(groups: dict[tuple, list[CandidateView]],
                     rule: Callable[[list[CandidateView]], CandidateView | None]) -> dict[tuple, CandidateView]:
    """Apply the rule to each group's as_of-only candidate views. The rule never receives outcomes; it must
    return one of the views it was given (or None), and it must be deterministic: it is run twice on
    independently built copies and must choose the same candidate both times."""
    chosen: dict[tuple, CandidateView] = {}
    for g in sorted(groups):
        views = list(groups[g])
        pick = rule(list(views))
        again = rule([CandidateView(**v.to_dict()) for v in views])
        if pick is not None and not any(pick is v for v in views):
            raise HindsightError(f"rule returned an object that is not one of the offered candidates for {g}")
        if (pick is None) != (again is None) or (pick is not None and pick.to_dict() != again.to_dict()):
            raise HindsightError(f"rule is not a deterministic function of the as_of candidates for {g}")
        if pick is not None:
            chosen[g] = pick
    return chosen


# ---------------------------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------------------------

def _pct(d: list[float], lo: float = 0.025, hi: float = 0.975) -> list[float] | None:
    if not d:
        return None
    d = sorted(d)
    return [round(d[int((len(d) - 1) * lo)], 6), round(d[int((len(d) - 1) * hi)], 6)]


def cluster_bootstrap_ratio(items: list[dict], num_f: str, den_f: str, cluster_f: str, bootstrap: int,
                            seed: int) -> list[float] | None:
    """Percentile CI of sum(num) / sum(den), resampling whole clusters with replacement."""
    cl: dict[Any, list[float]] = {}
    for it in items:
        if it.get(num_f) is None or it.get(den_f) is None:
            continue
        c = cl.setdefault(it[cluster_f], [0.0, 0.0])
        c[0] += it[num_f]
        c[1] += it[den_f]
    if len(cl) < 3 or bootstrap < 1:
        return None
    names = sorted(cl, key=str)
    nums = [cl[n][0] for n in names]
    dens = [cl[n][1] for n in names]
    rng = random.Random(seed)
    k = len(names)
    pop = range(k)
    draws = []
    for _ in range(bootstrap):
        idx = rng.choices(pop, k=k)
        d = sum(map(dens.__getitem__, idx))
        if d:
            draws.append(sum(map(nums.__getitem__, idx)) / d)
    return _pct(draws) if len(draws) >= max(1, bootstrap // 2) else None


# ---------------------------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------------------------

def _band_label(x: float, edges: list[float]) -> str:
    if x < edges[0]:
        return f"lt_{edges[0]:g}"
    for a, b in zip(edges, edges[1:]):
        if a <= x < b:
            return f"{a:g}_to_{b:g}"
    return f"ge_{edges[-1]:g}"


def _summary(ds: list[dict], clusters: list[str], bootstrap: int, seed: int) -> dict:
    settled = [d for d in ds if d.get("return") is not None]
    out: dict[str, Any] = {"n_decisions": len(settled),
                           "n_games": len({d["game"] for d in settled}),
                           "n_players": len({d["player"] for d in settled})}
    if not settled:
        return out
    n = len(settled)
    cost = sum(d["cost_per_contract"] for d in settled)
    ret = sum(d["return"] for d in settled)
    out.update({
        "total_cost_per_contract_units": _r(cost), "total_return": _r(ret),
        "roi_after_fees": _r(ret / cost) if cost else None,
        "mean_return_per_contract": _r(ret / n),
        "hit_rate": _r(sum(d["won"] for d in settled) / n),
        "mean_probability": _r(sum(d["probability"] for d in settled) / n),
        "mean_executed_price": _r(sum(d["executed_price"] for d in settled) / n),
        "mean_fee_per_contract": _r(sum(d["fee_per_contract"] for d in settled) / n),
        "mean_spread": _r(sum(d["spread"] for d in settled if d["spread"] is not None) /
                          max(1, sum(1 for d in settled if d["spread"] is not None))),
        "mean_predicted_edge_after_fees": _r(sum(d["edge_after_fees"] for d in settled) / n),
        "realized_minus_predicted_per_contract": _r(ret / n - sum(d["edge_after_fees"] for d in settled) / n),
        "n_dnp_settled": sum(1 for d in settled if d["dnp"]),
    })
    for c in clusters:
        out[f"roi_ci95_{c}_clustered"] = cluster_bootstrap_ratio(settled, "return", "cost_per_contract", c,
                                                                 bootstrap, seed)
        out[f"mean_return_ci95_{c}_clustered"] = cluster_bootstrap_ratio(settled, "return", "one", c,
                                                                         bootstrap, seed)
    return out


def _bands(rows: list[dict], edges: list[float]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in rows:
        if d.get("return") is not None:
            groups[_band_label(d["edge_after_fees"], edges)].append(d)
    order = [f"lt_{edges[0]:g}"] + [f"{a:g}_to_{b:g}" for a, b in zip(edges, edges[1:])] + [f"ge_{edges[-1]:g}"]
    out = []
    for name in order:
        g = groups.get(name)
        if not g:
            continue
        n = len(g)
        cost = sum(d["cost_per_contract"] for d in g)
        ret = sum(d["return"] for d in g)
        out.append({"band": name, "n": n, "n_games": len({d["game"] for d in g}),
                    "mean_predicted_probability": _r(sum(d["probability"] for d in g) / n),
                    "observed_win_rate": _r(sum(d["won"] for d in g) / n),
                    "mean_cost_per_contract": _r(cost / n),
                    "mean_predicted_edge_after_fees": _r(sum(d["edge_after_fees"] for d in g) / n),
                    "mean_realized_return_per_contract": _r(ret / n),
                    "roi_after_fees": _r(ret / cost) if cost else None})
    return out


def verdict(summary: dict, *, thresholds: dict, clusters: list[str], market_informed: bool) -> dict:
    reasons = []
    if market_informed:
        return {"verdict": "NOT_INDEPENDENT_EVIDENCE",
                "reasons": ["the probability source is market-informed; its disagreement with the market it "
                            "learned from is not independent evidence of value"]}
    n, g = summary.get("n_decisions", 0), summary.get("n_games", 0)
    if n < thresholds["min_decisions"]:
        reasons.append(f"n_decisions {n} < preregistered min_decisions {thresholds['min_decisions']}")
    if g < thresholds["min_games"]:
        reasons.append(f"n_games {g} < preregistered min_games {thresholds['min_games']}")
    if reasons:
        return {"verdict": "INSUFFICIENT_SAMPLE", "reasons": reasons}
    roi = summary.get("roi_after_fees")
    lows = {c: (summary.get(f"roi_ci95_{c}_clustered") or [None])[0] for c in clusters}
    if roi is None or roi <= 0:
        return {"verdict": "NO_EVIDENCE_OF_VALUE", "reasons": [f"ROI after fees {roi} <= 0"]}
    if all(v is not None and v > thresholds["min_roi_ci_lower"] for v in lows.values()):
        return {"verdict": "VALIDATED_VALUE",
                "reasons": [f"ROI after fees {roi} > 0 and every clustered 95% CI lower bound {lows} > "
                            f"{thresholds['min_roi_ci_lower']}"]}
    return {"verdict": "POSITIVE_BUT_INCONCLUSIVE",
            "reasons": [f"ROI after fees {roi} > 0 but a clustered 95% CI lower bound {lows} is not > "
                        f"{thresholds['min_roi_ci_lower']}"]}


def evaluate(probability_rows: list[dict], quote_rows: list[dict], outcome_rows: list[dict], *,
             rule: Callable[[list[CandidateView]], CandidateView | None] | None = None,
             rule_name: str | None = None,
             fees: FeeModel | None = None,
             thresholds: dict | None = None,
             prereg: dict | None = None,
             market_informed_source: bool = False,
             cluster_by: Iterable[str] = ("game",),
             bootstrap: int = 2000, seed: int = 20261010,
             as_of_policy: str = "earliest",
             dnp_policy: str = "settle_by_actual",
             max_quote_age_seconds: float | None = None,
             calibration_fit_end: str | datetime | None = None,
             edge_bands: Iterable[float] = DEFAULT_EDGE_BANDS,
             include_decisions: bool = False) -> dict:
    """Evaluate one probability source against executable quotes and outcomes.

    One decision per (sport, game_id, player_id, statistic): the as_of is fixed by ``as_of_policy`` (never by
    outcome), the rule picks at most one (threshold, side) from that as_of's candidates, then outcomes are
    joined. ``prereg`` (the parsed preregistration, if any) is echoed with its sha256."""
    if as_of_policy not in AS_OF_POLICIES:
        raise ScorecardError(f"as_of_policy must be one of {AS_OF_POLICIES}")
    if dnp_policy not in DNP_POLICIES:
        raise ScorecardError(f"dnp_policy must be one of {DNP_POLICIES}")
    clusters = list(dict.fromkeys(cluster_by))
    for c in clusters:
        if c not in ("game", "player"):
            raise ScorecardError(f"cluster must be 'game' or 'player', got {c!r}")
    if "game" not in clusters:
        clusters.insert(0, "game")
    th = dict(DEFAULT_THRESHOLDS)
    th_source = "defaults"
    if thresholds:
        unknown = set(thresholds) - set(DEFAULT_THRESHOLDS)
        if unknown:
            raise ScorecardError(f"unknown thresholds {sorted(unknown)}")
        th.update(thresholds)
        th_source = "preregistration" if prereg else "caller"
    fees = fees or FeeModel()
    if rule is None:
        rule = rule_max_edge_after_fees(0.0)
    rule_label = rule_name or getattr(rule, "__name__", "custom_rule")
    edges = sorted(float(x) for x in edge_bands)
    fit_end = parse_ts(calibration_fit_end, "calibration_fit_end") if calibration_fit_end else None

    rungs, flags = parse_probabilities(probability_rows)
    book = QuoteBook(quote_rows)
    outcomes = parse_outcomes(outcome_rows)
    market_informed = bool(market_informed_source or flags["market_informed_rows"])
    counts = Counter()

    # ---- fix one as_of per decision group, by policy (never by outcome or by price)
    times: dict[tuple, set[datetime]] = defaultdict(set)
    for r in rungs.values():
        times[r.ident].add(r.as_of)
    chosen_as_of: dict[tuple, datetime] = {}
    for g, ts in times.items():
        if len(ts) > 1:
            if as_of_policy == "unique":
                raise ScorecardError(f"{g} has {len(ts)} as_of times; as_of_policy='unique'")
            counts["groups_with_multiple_as_of"] += 1
        chosen_as_of[g] = min(ts) if as_of_policy in ("earliest", "unique") else max(ts)
    counts["rungs_at_other_as_of_dropped"] = sum(1 for r in rungs.values() if r.as_of != chosen_as_of[r.ident])

    # ---- build candidate views from as_of information only
    groups: dict[tuple, list[CandidateView]] = defaultdict(list)
    private: dict[int, dict] = {}
    for k in sorted(rungs):
        r = rungs[k]
        if r.as_of != chosen_as_of[r.ident]:
            continue
        counts["rungs_considered"] += 1
        if fit_end is not None and r.as_of <= fit_end:
            counts["rungs_in_sample_excluded"] += 1
            continue
        q = book.entry(r, max_quote_age_seconds, counts)
        if q is None:
            continue
        if q.kickoff is not None and r.as_of >= q.kickoff:
            counts["rungs_at_or_after_kickoff_excluded"] += 1
            continue
        counts["rungs_with_entry_quote"] += 1
        spread = round(q.ask - q.bid, 6) if q.bid is not None and q.ask is not None else None
        for side in (YES, NO):
            px = side_price(side, q.bid, q.ask)
            c = fees.cost(px) if px is not None else None
            if c is None:
                counts[f"{side.lower()}_side_not_executable"] += 1
                continue
            p = r.p_yes if side == YES else 1.0 - r.p_yes
            exec_px, fee, cost = c
            v = CandidateView(
                sport=r.ident[0], game_id=r.ident[1], player_id=r.ident[2], statistic=r.ident[3],
                at_least=r.at_least, side=side, as_of=iso(r.as_of), probability=round(p, 9),
                p_yes=round(r.p_yes, 9), yes_bid=q.bid, yes_ask=q.ask, spread=spread, quoted_price=px,
                executed_price=round(exec_px, 6), fee_per_contract=round(fee, 6), cost_per_contract=round(cost, 6),
                edge_before_fees=round(p - exec_px, 9), edge_after_fees=round(p - cost, 9),
                expected_roi=round((p - cost) / cost, 9), bet_up_to=fees.bet_up_to(p),
                quote_observed_at=iso(q.observed_at),
                quote_age_seconds=round((r.as_of - q.observed_at).total_seconds(), 3),
                ticker=q.ticker, market=q.market)
            groups[r.ident].append(v)
            private[id(v)] = {"rung": r, "quote": q}
    counts["groups_total"] = len(times)
    counts["groups_with_candidates"] = len(groups)
    counts["groups_without_executable_candidate"] = len(times) - len(groups)

    chosen = select_decisions(groups, rule)
    counts["groups_where_rule_declined"] = len(groups) - len(chosen)

    # ---- join outcomes AFTER selection
    def settle(v: CandidateView) -> dict:
        r, q = private[id(v)]["rung"], private[id(v)]["quote"]
        d = v.to_dict()
        d.update({"game": f"{v.sport}|{v.game_id}", "player": f"{v.sport}|{v.player_id}", "one": 1.0,
                  "return": None, "won": None, "dnp": False, "actual": None})
        o = outcomes.get(r.ident)
        if o is None:
            d["settlement"] = "MISSING_OUTCOME"
        elif not o["played"] and dnp_policy == "void":
            d["settlement"] = "VOID_DNP"
            d["dnp"] = True
        else:
            y = float(o["actual"])
            yes_wins = y >= v.at_least
            won = yes_wins if v.side == YES else not yes_wins
            d.update({"actual": y, "won": 1.0 if won else 0.0, "dnp": not o["played"],
                      "return": round((1.0 if won else 0.0) - v.cost_per_contract, 9), "settlement": "SETTLED"})
        close, cutoff = book.closing(r, q)
        d["close_cutoff"] = iso(cutoff)
        if close is not None:
            cm = side_mid(v.side, close.bid, close.ask)
            cp = side_price(v.side, close.bid, close.ask)
            d["closing_quote_observed_at"] = iso(close.observed_at)
            d["clv_mid"] = None if cm is None else round(cm - v.executed_price, 6)
            d["clv_executable"] = None if cp is None else round(cp - v.quoted_price, 6)
        else:
            d["closing_quote_observed_at"] = d["clv_mid"] = d["clv_executable"] = None
        return d

    decisions = [settle(chosen[g]) for g in sorted(chosen)]
    all_cands = [settle(v) for g in sorted(groups) for v in groups[g]]
    sc = Counter(d["settlement"] for d in decisions)

    summary = _summary(decisions, clusters, bootstrap, seed)
    clv_rows = [d for d in decisions if d["clv_mid"] is not None]
    clv = {"n_with_pre_close_quote": len(clv_rows), "n_decisions": len(decisions),
           "role": "DIAGNOSTIC_ONLY_NOT_IN_VERDICT",
           "definition": ("closing mid for the side (last quote after as_of and at or before min(kickoff, "
                          "close_time) on the same ticker) minus the executed entry price; clv_executable uses the "
                          "closing ask for the side minus the quoted entry ask")}
    if clv_rows:
        clv["mean_clv_mid"] = _r(sum(d["clv_mid"] for d in clv_rows) / len(clv_rows))
        ce = [d for d in clv_rows if d["clv_executable"] is not None]
        clv["mean_clv_executable"] = _r(sum(d["clv_executable"] for d in ce) / len(ce)) if ce else None
        clv["share_positive_clv_mid"] = _r(sum(1 for d in clv_rows if d["clv_mid"] > 0) / len(clv_rows))
        for c in clusters:
            clv[f"mean_clv_mid_ci95_{c}_clustered"] = cluster_bootstrap_ratio(clv_rows, "clv_mid", "one", c,
                                                                              bootstrap, seed)

    def by(field: str) -> dict:
        g: dict[str, list[dict]] = defaultdict(list)
        for d in decisions:
            g[str(d[field])].append(d)
        return {k: _summary(v, clusters, bootstrap, seed) for k, v in sorted(g.items())}

    stat_key = lambda d: f"{d['sport']}:{d['statistic']}"  # noqa: E731
    by_stat: dict[str, list[dict]] = defaultdict(list)
    for d in decisions:
        by_stat[stat_key(d)].append(d)

    v = verdict(summary, thresholds=th, clusters=clusters, market_informed=market_informed)
    prereg_blob = json.dumps(prereg, sort_keys=True, separators=(",", ":")).encode() if prereg else None
    report: dict[str, Any] = {
        "scorecard": "value",
        "version": VERSION,
        "config": {"rule": rule_label, "fees": fees.describe(), "cluster_by": clusters, "bootstrap": bootstrap,
                   "seed": seed, "as_of_policy": as_of_policy, "dnp_policy": dnp_policy,
                   "max_quote_age_seconds": max_quote_age_seconds, "calibration_fit_end": iso(fit_end),
                   "edge_bands": edges, "decision_unit": "one decision per (sport, game_id, player_id, statistic)"},
        "preregistration": {"thresholds": th, "thresholds_source": th_source,
                            "sha256": hashlib.sha256(prereg_blob).hexdigest() if prereg_blob else None},
        "market_informed_source": market_informed,
        "input_flags": dict(sorted(flags.items())),
        "accounting": {**dict(sorted(counts.items())), "probability_rungs": len(rungs), "quote_rows": book.n,
                       "outcome_rows": len(outcomes), "decisions": len(decisions),
                       "decisions_by_settlement": dict(sorted(sc.items()))},
        "decisions_summary": summary,
        "closing_line_value": clv,
        "edge_bands": {"decisions": _bands(decisions, edges),
                       "all_executable_candidates_descriptive": _bands(all_cands, edges),
                       "note": ("reliability by predicted-edge band; the all-candidates table counts every rung and "
                                "both sides, which are correlated, so it has no CIs and is not evidence")},
        "by_statistic": {k: _summary(vv, clusters, bootstrap, seed) for k, vv in sorted(by_stat.items())},
        "by_market": by("market"),
        "by_side": by("side"),
        "verdict": {**v, "thresholds": th},
        "note": ("Betting value only, separate from accuracy promotion. Entry quotes are at or before as_of; CLV is "
                 "diagnostic. Not a recommendation, not a stake, and not wagering authority."),
    }
    if include_decisions:
        report["decisions"] = [{k: x for k, x in d.items() if k not in ("one",)} for d in decisions]
    return report


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def _as_list(v: Any) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _cli_evaluate(args: argparse.Namespace) -> dict:
    prereg = None
    if args.prereg:
        with open(args.prereg, encoding="utf-8") as fh:
            prereg = json.load(fh)
        if not isinstance(prereg, dict):
            raise ScorecardError("preregistration must be a JSON object")
    pr = prereg or {}

    def pick(name: str, default: Any = None) -> Any:
        v = getattr(args, name, None)
        given = v is not None and not (isinstance(v, list) and not v)
        if name in pr and given and v != pr[name] and _as_list(v) != _as_list(pr[name]):
            raise ScorecardError(f"--{name.replace('_', '-')} {v!r} contradicts the preregistration "
                                 f"({pr[name]!r}); preregistered settings cannot be overridden")
        if name in pr:
            return pr[name]
        return v if given else default

    if not args.quotes or not args.outcomes or not (args.probabilities or args.forecasts):
        raise ScorecardError("--quotes, --outcomes and one of --probabilities / --forecasts are required")
    prob_rows: list[dict] = []
    if args.probabilities:
        prob_rows += load_jsonl(args.probabilities)
    if args.forecasts:
        prob_rows += probabilities_from_forecasts(load_jsonl(args.forecasts))
    rule_name = pick("rule", "max_edge_after_fees")
    if rule_name not in RULES:
        raise ScorecardError(f"unknown rule {rule_name!r}; one of {sorted(RULES)}")
    min_edge = float(pick("min_edge", 0.0))
    fees = FeeModel(role=pick("role", TAKER), contracts=int(pick("contracts", 1)),
                    multiplier=float(pick("fee_multiplier", 1.0)), slippage=float(pick("slippage", 0.0)),
                    tick=float(pick("tick", 0.01)))
    thresholds = {k: pr[k] for k in DEFAULT_THRESHOLDS if k in pr}
    for k in DEFAULT_THRESHOLDS:
        v = getattr(args, k, None)
        if v is not None:
            if k in pr and v != pr[k]:
                raise ScorecardError(f"--{k.replace('_', '-')} contradicts the preregistration")
            thresholds[k] = v
    return evaluate(
        prob_rows, load_jsonl(args.quotes), load_jsonl(args.outcomes),
        rule=RULES[rule_name](min_edge), rule_name=f"{rule_name}(min_edge={min_edge:g})", fees=fees,
        thresholds=thresholds or None, prereg=prereg,
        market_informed_source=bool(args.market_informed_source or pr.get("market_informed_source", False)),
        cluster_by=_as_list(pick("cluster", ["game"])) or ["game"],
        bootstrap=int(pick("bootstrap", 2000)), seed=int(pick("seed", 20261010)),
        as_of_policy=pick("as_of_policy", "earliest"), dnp_policy=pick("dnp_policy", "settle_by_actual"),
        max_quote_age_seconds=pick("max_quote_age_seconds"), calibration_fit_end=pick("calibration_fit_end"),
        edge_bands=pick("edge_bands", list(DEFAULT_EDGE_BANDS)), include_decisions=bool(args.decisions_out))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("evaluate", help="score calibrated probabilities against executable quotes")
    e.add_argument("--probabilities", help="JSONL: identity, at_least, as_of, p_yes (or side + probability)")
    e.add_argument("--forecasts", help="pure_forecast JSONL; its ladder rungs become probability rows")
    e.add_argument("--quotes", help="market-family JSONL: identity, at_least, observed_at, yes_bid/yes_ask "
                                    "(dollars) or yes_bid_cents/yes_ask_cents, open_time, close_time, ticker")
    e.add_argument("--outcomes", help="sports outcomes JSONL (pure_gate outcome format)")
    e.add_argument("--prereg", help="preregistration JSON (thresholds, rule, fees...); cannot be overridden")
    e.add_argument("--rule", choices=sorted(RULES))
    e.add_argument("--min-edge", dest="min_edge", type=float)
    e.add_argument("--role", choices=[TAKER, MAKER])
    e.add_argument("--contracts", type=int)
    e.add_argument("--fee-multiplier", dest="fee_multiplier", type=float)
    e.add_argument("--slippage", type=float, help="dollars per contract added to the entry price")
    e.add_argument("--tick", type=float)
    e.add_argument("--min-decisions", dest="min_decisions", type=int)
    e.add_argument("--min-games", dest="min_games", type=int)
    e.add_argument("--min-roi-ci-lower", dest="min_roi_ci_lower", type=float)
    e.add_argument("--market-informed-source", dest="market_informed_source", action="store_true")
    e.add_argument("--cluster", action="append", choices=["game", "player"], default=[])
    e.add_argument("--bootstrap", type=int)
    e.add_argument("--seed", type=int)
    e.add_argument("--as-of-policy", dest="as_of_policy", choices=list(AS_OF_POLICIES))
    e.add_argument("--dnp-policy", dest="dnp_policy", choices=list(DNP_POLICIES))
    e.add_argument("--max-quote-age-seconds", dest="max_quote_age_seconds", type=float)
    e.add_argument("--calibration-fit-end", dest="calibration_fit_end",
                   help="decisions with as_of at or before this time are in-sample and excluded")
    e.add_argument("--output", help="write the JSON report here (default stdout)")
    e.add_argument("--decisions-out", dest="decisions_out", help="also write per-decision JSONL here")
    f = sub.add_parser("fee", help="Kalshi fee for one order")
    f.add_argument("--price", type=float, required=True, help="dollars per contract, e.g. 0.55")
    f.add_argument("--contracts", type=int, default=1)
    f.add_argument("--role", choices=[TAKER, MAKER], default=TAKER)
    f.add_argument("--multiplier", type=float, default=1.0)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "fee":
            fee = kalshi_fee(_dec(args.price), args.contracts, role=args.role, multiplier=_dec(args.multiplier))
            print(json.dumps({"price": args.price, "contracts": args.contracts, "role": args.role,
                              "multiplier": args.multiplier, "fee_total": float(fee),
                              "fee_per_contract": float(fee / args.contracts), "version": VERSION}))
            return 0
        report = _cli_evaluate(args)
    except (ScorecardError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "version": VERSION}), file=sys.stderr)
        return 2
    decisions = report.pop("decisions", None)
    text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if args.decisions_out and decisions is not None:
        with open(args.decisions_out, "w", encoding="utf-8") as fh:
            for d in decisions:
                fh.write(json.dumps(d, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
