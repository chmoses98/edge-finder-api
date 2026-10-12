#!/usr/bin/env python3
"""Projection accuracy scorecard for player-stat distribution forecasts (pure_forecast.v1 and kin).

Read-only research tooling, vendored alongside ``pure_gate.py``. Standard library only, Python 3.10+.
It scores forecasts against sports outcomes. It knows nothing about prices: betting value is a separate
question answered by ``value_scorecard.py`` and promoted separately (see SCORECARDS.md).

What it reports, per population (the full eligible population and the pregame market-listed cohort) and per
``sport:statistic`` (scale-dependent metrics are never pooled across statistics):

* accounting: eligible identities, forecasts, missing forecasts (failed or missed captures), forecasts without
  outcomes, conditional DNP exclusions, scored rows, games, players. Nothing is dropped silently.
* point accuracy: MAE, RMSE and mean bias of the forecast mean; MAE of the median.
* intervals: p10-p90 coverage (nominal 80%) and every symmetric quantile pair the rows publish.
* calibration: quantile calibration table, interval-censored PIT histogram with a chi-square uniformity
  statistic, per-rung reliability (mean probability vs observed frequency).
* proper scores: CRPS (quantile approximation, plus a ladder threshold-decomposition variant), Brier and log
  score per ladder threshold, ranked probability score over the ladder.
* head-to-head: challenger minus each comparator (production champion, statistical baseline, independent
  sports-only model, market-informed benchmark), paired on identical rows, with game-clustered (and optionally
  player-clustered) percentile bootstrap CIs.
* breakdowns by any row attribute through a configurable key function.

Every output is descriptive (``DESCRIPTIVE_ONLY_NOT_AUTOMATIC_PROMOTION``). Promotion needs a preregistered,
untouched chronological holdout.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

VERSION = "accuracy_scorecard 1.0.0"
IDENTITY = ("sport", "game_id", "player_id", "statistic")
TINY = 1e-9
LOG_EPS = 1e-6  # probabilities are clipped to [LOG_EPS, 1 - LOG_EPS] for the log score (clips are counted)

CONTRACT_PURE = "pure_forecast"        # validated by pure_gate.validate (v1, or kit rows read as v0)
CONTRACT_PROJECTION = "projection_only"  # identity + as_of + projection object (for non-PURE comparators)
CONTRACTS = (CONTRACT_PURE, CONTRACT_PROJECTION)

ROLE_CHALLENGER = "challenger"
ROLES = ("production_champion", "statistical_baseline", "independent_sports_only",
         "market_informed_benchmark", "other")
ROLE_NOTES = {
    "production_champion": "challenger vs the production champion: the accuracy-promotion comparison",
    "statistical_baseline": "challenger vs a simple statistical baseline: a challenger that cannot beat it "
                            "adds nothing",
    "independent_sports_only": "challenger vs another sports-only (market-independent) model",
    "market_informed_benchmark": ("PRICE_OF_INDEPENDENCE_DIAGNOSTIC: the benchmark uses market information. "
                                  "A PURE challenger scoring worse here is expected and is not a defect; a "
                                  "challenger scoring better is not evidence of betting value"),
    "other": "descriptive comparison",
}
LOSS_METRICS = ("abs", "sq", "abs_median", "crps_q", "crps_ladder", "brier", "log", "rps")
H2H_METRICS = LOSS_METRICS + ("err", "cov80")

POP_FULL = "full_eligible"
POP_COHORT = "pregame_market_listed"

CAPTURE_WINDOWS = ((2.0, "le_2h"), (6.0, "2h_6h"), (24.0, "6h_24h"), (72.0, "24h_72h"))


class ScorecardError(ValueError):
    """Input that cannot be scored honestly. The scorecard fails closed."""


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def load_gate():
    """The pure_gate module vendored next to this file (or importable), else None."""
    mod = sys.modules.get("pure_gate")
    if mod is not None:
        return mod
    sibling = Path(__file__).resolve().with_name("pure_gate.py")
    if sibling.exists():
        spec = importlib.util.spec_from_file_location("pure_gate", sibling)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules["pure_gate"] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop("pure_gate", None)
                raise
            return mod
    try:
        import pure_gate  # type: ignore
        return pure_gate
    except ImportError:
        return None


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


def opt_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return parse_ts(value)
    except ScorecardError:
        return None


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def num(value: Any, desc: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScorecardError(f"{desc} must be numeric")
    v = float(value)
    if not math.isfinite(v):
        raise ScorecardError(f"{desc} must be finite")
    return v


def identity(row: dict) -> tuple[str, str, str, str]:
    for f in IDENTITY:
        if not isinstance(row.get(f), str) or not row[f].strip():
            raise ScorecardError(f"{f} must be a nonempty string")
    return tuple(row[f] for f in IDENTITY)  # type: ignore[return-value]


def _r(x: float | None, nd: int = 6) -> float | None:
    return None if x is None else round(x, nd)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def chi2_sf(x: float, df: int) -> float:
    """Survival function of the chi-square distribution (regularized upper incomplete gamma)."""
    if x <= 0:
        return 1.0
    a, z = df / 2.0, x / 2.0
    if z < a + 1.0:  # series for P, return 1 - P
        term = total = 1.0 / a
        ap = a
        for _ in range(10000):
            ap += 1.0
            term *= z / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        p = total * math.exp(-z + a * math.log(z) - math.lgamma(a))
        return max(0.0, min(1.0, 1.0 - p))
    # continued fraction for Q (Lentz)
    b = z + 1.0 - a
    c = 1.0 / 1e-300
    d = 1.0 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return max(0.0, min(1.0, math.exp(-z + a * math.log(z) - math.lgamma(a)) * h))


# ---------------------------------------------------------------------------------------------
# Forecast rows
# ---------------------------------------------------------------------------------------------

class Forecast:
    __slots__ = ("ident", "as_of", "kickoff", "conditional", "mean", "median", "p10", "p90",
                 "levels", "ladder", "row")

    def __init__(self, row: dict, ident: tuple, as_of: datetime, kickoff: datetime | None,
                 conditional: bool, proj: dict):
        self.row = row
        self.ident = ident
        self.as_of = as_of
        self.kickoff = kickoff
        self.conditional = conditional
        self.mean = float(proj["mean"])
        self.median = float(proj["median"])
        self.p10 = float(proj["p10"])
        self.p90 = float(proj["p90"])
        levels = {0.1: self.p10, 0.5: self.median, 0.9: self.p90}
        for item in proj.get("quantiles") or []:
            levels[round(float(item["q"]), 12)] = float(item["value"])
        self.levels = dict(sorted(levels.items()))
        self.ladder = {float(t["at_least"]): float(t["probability"]) for t in proj.get("thresholds") or []}

    @property
    def fkey(self) -> tuple:
        return self.ident + (iso(self.as_of),)


def _check_projection(p: Any) -> None:
    """Structural checks for projection_only rows (pure_gate.validate does this for PURE rows)."""
    if not isinstance(p, dict):
        raise ScorecardError("projection must be an object")
    for n in ("mean", "median", "p10", "p90"):
        if n not in p:
            raise ScorecardError(f"projection missing {n}")
        num(p[n], f"projection.{n}")
    if not p["p10"] <= p["median"] <= p["p90"]:
        raise ScorecardError("projection requires p10 <= median <= p90")
    th = p.get("thresholds", [])
    if not isinstance(th, list):
        raise ScorecardError("projection.thresholds must be a list")
    last_t, last_p = -math.inf, math.inf
    for i, t in enumerate(th):
        if not isinstance(t, dict) or "at_least" not in t or "probability" not in t:
            raise ScorecardError(f"thresholds[{i}] requires at_least and probability")
        a, pr = num(t["at_least"], "at_least"), num(t["probability"], "probability")
        if not 0.0 <= pr <= 1.0:
            raise ScorecardError(f"thresholds[{i}].probability outside [0,1]")
        if a <= last_t:
            raise ScorecardError("thresholds must be strictly ascending in at_least")
        if pr > last_p + TINY:
            raise ScorecardError("threshold ladder must be non-increasing in at_least")
        last_t, last_p = a, pr
    qs = p.get("quantiles", [])
    if not isinstance(qs, list):
        raise ScorecardError("projection.quantiles must be a list")
    merged = {0.1: float(p["p10"]), 0.5: float(p["median"]), 0.9: float(p["p90"])}
    last_q = 0.0
    for i, item in enumerate(qs):
        if not isinstance(item, dict) or "q" not in item or "value" not in item:
            raise ScorecardError(f"quantiles[{i}] requires q and value")
        q, v = num(item["q"], "q"), num(item["value"], "value")
        if not 0.0 < q < 1.0 or q <= last_q:
            raise ScorecardError("quantile levels must be strictly ascending inside (0,1)")
        last_q = q
        anchor = merged.get(round(q, 12))
        if anchor is not None and abs(anchor - v) > TINY:
            raise ScorecardError(f"quantile q={q} disagrees with p10/median/p90")
        merged[round(q, 12)] = v
    vals = [merged[q] for q in sorted(merged)]
    if any(b < a - TINY for a, b in zip(vals, vals[1:])):
        raise ScorecardError("quantile values must be non-decreasing in q")


def parse_forecasts(rows: Iterable[dict], *, contract: str, label: str, validate: bool = True,
                    default_conditional: bool | None = None) -> dict[tuple, Forecast]:
    """Index one arm's rows by (identity, as_of). Duplicates are refused."""
    if contract not in CONTRACTS:
        raise ScorecardError(f"unknown contract {contract!r}; one of {CONTRACTS}")
    gate = load_gate() if (validate and contract == CONTRACT_PURE) else None
    if validate and contract == CONTRACT_PURE and gate is None:
        raise ScorecardError("pure_gate.py is not available next to accuracy_scorecard.py; vendor it or "
                             "pass validate=False / --no-validate")
    out: dict[tuple, Forecast] = {}
    for i, row in enumerate(rows, 1):
        try:
            if not isinstance(row, dict):
                raise ScorecardError("row must be an object")
            if gate is not None:
                try:
                    gate.validate(row)
                except gate.GateError as exc:
                    raise ScorecardError(f"pure_gate: {exc}") from exc
            ident = identity(row)
            if "projection" not in row:
                raise ScorecardError("missing projection")
            if contract == CONTRACT_PROJECTION or gate is None:
                _check_projection(row["projection"])
            as_of = parse_ts(row.get("as_of"), "as_of")
            kickoff = opt_ts(row.get("kickoff"))
            if kickoff is not None and not as_of < kickoff:
                raise ScorecardError("as_of must be before kickoff")
            cond = row.get("conditional_on_playing", default_conditional)
            if not isinstance(cond, bool):
                raise ScorecardError("conditional_on_playing must be boolean (or give the arm a default)")
            f = Forecast(row, ident, as_of, kickoff, cond, row["projection"])
        except ScorecardError as exc:
            raise ScorecardError(f"{label} row {i}: {exc}") from exc
        if f.fkey in out:
            raise ScorecardError(f"{label}: duplicate forecast for {f.fkey}")
        out[f.fkey] = f
    return out


def parse_outcomes(rows: list[dict], *, validate: bool = True) -> dict[tuple, dict]:
    gate = load_gate() if validate else None
    if gate is not None:
        try:
            return dict(gate.outcomes_index(rows))
        except gate.GateError as exc:
            raise ScorecardError(f"outcomes: pure_gate: {exc}") from exc
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
# Market-listed cohort (from the separate market family; listings only, never settlement)
# ---------------------------------------------------------------------------------------------

OBSERVED_ALIASES = ("observed_at", "row_observed_at", "board_observed_at", "quoted_at")
REFERENCE_AS_OF_ALIASES = ("pure_as_of", "as_of")


class Cohort:
    """Pregame market-listed cohort. An identity is listed at time T iff some market-family row for it has
    ``open_time <= T`` and was observed by our own records at or before T (``observed_at <= T``)."""

    def __init__(self, market_rows: Iterable[dict] | None):
        self.first_listed: dict[tuple, datetime] = {}
        self.reference_as_of: dict[tuple, datetime] = {}
        self.stats = Counter()
        self.available = market_rows is not None
        for r in market_rows or []:
            self.stats["market_rows"] += 1
            try:
                k = (r.get("sport"), r["game_id"], str(r["player_id"]), r["statistic"])
            except KeyError:
                self.stats["rows_missing_identity"] += 1
                continue
            if r.get("player_id") in (None, ""):
                self.stats["rows_missing_identity"] += 1
                continue
            opened = opt_ts(r.get("open_time"))
            seen = next((opt_ts(r.get(a)) for a in OBSERVED_ALIASES if opt_ts(r.get(a))), None)
            ref = next((opt_ts(r.get(a)) for a in REFERENCE_AS_OF_ALIASES if opt_ts(r.get(a))), None)
            if ref is not None:
                prev = self.reference_as_of.get(k)
                self.reference_as_of[k] = ref if prev is None or ref < prev else prev
            if opened is None or seen is None:
                self.stats["rows_without_open_time_or_observed_at"] += 1
                continue
            listed = max(opened, seen)
            prev = self.first_listed.get(k)
            if prev is None or listed < prev:
                self.first_listed[k] = listed
        self.stats["identities_with_listing_time"] = len(self.first_listed)

    def _get(self, d: dict, ident: tuple):
        return d.get(ident, d.get((None,) + ident[1:]))

    def listed(self, ident: tuple, at: datetime | None) -> bool | None:
        if not self.available or at is None:
            return None
        t = self._get(self.first_listed, ident)
        return t is not None and t <= at

    def reference(self, ident: tuple) -> datetime | None:
        return self._get(self.reference_as_of, ident)


# ---------------------------------------------------------------------------------------------
# Per-row scores
# ---------------------------------------------------------------------------------------------

def pinball_crps(levels: dict[float, float], qs: Iterable[float], y: float) -> float:
    """CRPS quantile approximation: 2 x mean pinball loss over the levels ``qs``.

    CRPS(F, y) = 2 * integral over q in (0,1) of the pinball loss of F^-1(q). Averaging over a finite set of
    levels is a Riemann approximation of that integral. For a point forecast (all quantiles equal to x) and a
    level set whose mean is 0.5 it equals |y - x| exactly."""
    qs = list(qs)
    total = 0.0
    for q in qs:
        x = levels[q]
        total += (q - (1.0 if y < x else 0.0)) * (y - x)
    return 2.0 * total / len(qs)


def ladder_widths(strikes: list[float]) -> list[float]:
    """Midpoint-rule widths for the threshold decomposition over a strike grid."""
    k = len(strikes)
    if k < 2:
        return []
    w = []
    for i in range(k):
        if i == 0:
            w.append(strikes[1] - strikes[0])
        elif i == k - 1:
            w.append(strikes[-1] - strikes[-2])
        else:
            w.append((strikes[i + 1] - strikes[i - 1]) / 2.0)
    return w


def ladder_crps(ladder: dict[float, float], strikes: list[float], y: float) -> float | None:
    """Threshold-decomposition CRPS restricted to the ladder's range.

    CRPS(F, y) = integral over t of (P(X >= t) - 1{y >= t})^2 dt (the Brier score integrated over thresholds).
    Evaluated with the midpoint rule on the strike grid. For an integer-valued statistic with a unit-spaced
    half-integer ladder this is the exact CRPS over [t_1 - 1/2, t_K + 1/2]; outside the ladder's range the
    integrand is omitted, so it is a threshold-weighted CRPS (a lower bound on the full CRPS)."""
    if len(strikes) < 2:
        return None
    w = ladder_widths(strikes)
    return sum(wi * (ladder[t] - (1.0 if y >= t else 0.0)) ** 2 for wi, t in zip(w, strikes))


def pit_bounds(f: Forecast, y: float) -> tuple[float, float, bool]:
    """Interval [L, U] that must contain the (randomized) PIT value given only the published knots.

    Ladder rung (t, p): P(X < t) = 1 - p, so y >= t implies PIT >= 1 - p and y < t implies PIT <= 1 - p.
    Quantile (q, v): y > v implies PIT >= q and y < v implies PIT <= q (y == v gives no bound; it handles
    ties in discrete statistics). Inconsistent knots (L > U) collapse to the midpoint and are counted."""
    lo, hi = 0.0, 1.0
    for t, p in f.ladder.items():
        c = 1.0 - p
        if y >= t:
            lo = max(lo, c)
        else:
            hi = min(hi, c)
    for q, v in f.levels.items():
        if y > v + TINY:
            lo = max(lo, q)
        elif y < v - TINY:
            hi = min(hi, q)
    if lo > hi + TINY:
        mid = (lo + hi) / 2.0
        return mid, mid, True
    return lo, min(max(hi, lo), 1.0), False


def score_row(f: Forecast, y: float) -> dict[str, Any]:
    s: dict[str, Any] = {"err": f.mean - y, "abs": abs(f.mean - y), "sq": (f.mean - y) ** 2,
                         "abs_median": abs(f.median - y), "cov80": 1.0 if f.p10 <= y <= f.p90 else 0.0}
    pairs = {}
    for q, lo in f.levels.items():
        if q < 0.5:
            hi_q = round(1.0 - q, 12)
            if hi_q in f.levels:
                pairs[f"q{q:g}_q{hi_q:g}"] = (round(1.0 - 2 * q, 12), 1.0 if lo <= y <= f.levels[hi_q] else 0.0)
    s["intervals"] = pairs
    s["below"] = {q: (1.0 if y < v - TINY else 0.0, 1.0 if y <= v + TINY else 0.0) for q, v in f.levels.items()}
    s["crps_q"] = pinball_crps(f.levels, f.levels, y)
    s["crps_levels"] = len(f.levels)
    strikes = sorted(f.ladder)
    s["crps_ladder"] = ladder_crps(f.ladder, strikes, y)
    rungs = {}
    for t in strikes:
        p, o = f.ladder[t], 1.0 if y >= t else 0.0
        pc = min(max(p, LOG_EPS), 1.0 - LOG_EPS)
        rungs[t] = {"p": p, "o": o, "brier": (p - o) ** 2,
                    "log": -math.log(pc if o else 1.0 - pc), "clipped": pc != p}
    s["rungs"] = rungs
    if rungs:
        s["brier"] = _mean([r["brier"] for r in rungs.values()])
        s["log"] = _mean([r["log"] for r in rungs.values()])
        s["rps"] = sum(r["brier"] for r in rungs.values())
        s["rps_norm"] = s["rps"] / len(rungs)
    s["pit"] = pit_bounds(f, y)
    return s


def paired_scores(a: Forecast, b: Forecast, y: float) -> dict[str, float | None]:
    """Challenger (a) and comparator (b) scores on the knots both publish."""
    out: dict[str, float | None] = {}
    sa, sb = {"err": a.mean - y}, {"err": b.mean - y}
    for s, f in ((sa, a), (sb, b)):
        s["abs"], s["sq"] = abs(f.mean - y), (f.mean - y) ** 2
        s["abs_median"] = abs(f.median - y)
        s["cov80"] = 1.0 if f.p10 <= y <= f.p90 else 0.0
    levels = sorted(set(a.levels) & set(b.levels))
    sa["crps_q"], sb["crps_q"] = pinball_crps(a.levels, levels, y), pinball_crps(b.levels, levels, y)
    strikes = sorted(set(a.ladder) & set(b.ladder))
    if strikes:
        for s, f in ((sa, a), (sb, b)):
            br, lg = [], []
            for t in strikes:
                p, o = f.ladder[t], 1.0 if y >= t else 0.0
                pc = min(max(p, LOG_EPS), 1.0 - LOG_EPS)
                br.append((p - o) ** 2)
                lg.append(-math.log(pc if o else 1.0 - pc))
            s["brier"], s["log"], s["rps"] = sum(br) / len(br), sum(lg) / len(lg), sum(br)
            s["crps_ladder"] = ladder_crps(f.ladder, strikes, y)
    for m in H2H_METRICS:
        x, z = sa.get(m), sb.get(m)
        out[f"a_{m}"], out[f"b_{m}"] = x, z
        out[f"d_{m}"] = (x - z) if (x is not None and z is not None) else None
    out["n_common_levels"] = len(levels)
    out["n_common_strikes"] = len(strikes)
    return out


# ---------------------------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------------------------

def cluster_bootstrap(items: list[dict], metrics: Iterable[str], cluster_field: str, bootstrap: int,
                      seed: int) -> dict[str, list[float] | None]:
    """Percentile CI of the mean of each metric, resampling whole clusters with replacement.

    The statistic is the ratio of resampled sums (sum of values / number of values), so clusters of different
    sizes are weighted by their rows. Every metric uses the same resampled clusters. Fewer than 3 clusters,
    or fewer than half the replicates defined, gives no CI."""
    metrics = list(metrics)
    clusters: dict[Any, dict[str, list[float]]] = {}
    for it in items:
        c = clusters.setdefault(it[cluster_field], {m: [0.0, 0] for m in metrics})
        for m in metrics:
            v = it.get(m)
            if v is not None:
                c[m][0] += v
                c[m][1] += 1
    out: dict[str, list[float] | None] = {m: None for m in metrics}
    if len(clusters) < 3 or bootstrap < 1:
        return out
    rng = random.Random(seed)
    names = sorted(clusters, key=str)
    n = len(names)
    sums = {m: [clusters[c][m][0] for c in names] for m in metrics}
    counts = {m: [clusters[c][m][1] for c in names] for m in metrics}
    draws: dict[str, list[float]] = {m: [] for m in metrics}
    population = range(n)
    for _ in range(bootstrap):
        idx = rng.choices(population, k=n)
        for m in metrics:
            cnt = sum(map(counts[m].__getitem__, idx))
            if cnt:
                draws[m].append(sum(map(sums[m].__getitem__, idx)) / cnt)
    for m in metrics:
        d = sorted(draws[m])
        if d and len(d) >= max(1, bootstrap // 2):
            out[m] = [round(d[int((len(d) - 1) * 0.025)], 6), round(d[int((len(d) - 1) * 0.975)], 6)]
    return out


# ---------------------------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------------------------

def pit_histogram(bounds: list[tuple[float, float, bool]], bins: int) -> dict:
    """Non-randomized PIT histogram (each row spreads unit mass uniformly over its [L, U] interval)."""
    n = len(bounds)
    if not n:
        return {"n": 0}
    hist = [0.0] * bins
    total_mean = 0.0
    second = 0.0
    for lo, hi, _bad in bounds:
        total_mean += (lo + hi) / 2.0
        second += (lo * lo + lo * hi + hi * hi) / 3.0
        if hi - lo < TINY:
            j = min(int(lo * bins), bins - 1)
            hist[j] += 1.0
            continue
        for j in range(bins):
            a, b = j / bins, (j + 1) / bins
            ov = min(hi, b) - max(lo, a)
            if ov > 0:
                hist[j] += ov / (hi - lo)
    expected = n / bins
    chi2 = sum((h - expected) ** 2 / expected for h in hist)
    mean = total_mean / n
    widths = [hi - lo for lo, hi, _ in bounds]
    return {
        "n": n, "bins": bins,
        "histogram_fraction": [round(h / n, 6) for h in hist],
        "chi2_uniformity": round(chi2, 6), "df": bins - 1, "p_value_iid": round(chi2_sf(chi2, bins - 1), 6),
        "max_abs_deviation_from_uniform": round(max(abs(h / n - 1.0 / bins) for h in hist), 6),
        "pit_mean": round(mean, 6), "pit_variance": round(second / n - mean * mean, 6),
        "uniform_reference": {"mean": 0.5, "variance": round(1 / 12, 6)},
        "mean_interval_width": round(sum(widths) / n, 6),
        "n_inconsistent_knots": sum(1 for _, _, bad in bounds if bad),
        "note": ("interval-censored PIT: each row's PIT is only known to lie in [L, U] given its published "
                 "knots, and its unit mass is spread uniformly over that interval. Uniform in expectation for "
                 "a calibrated forecast. The chi-square p-value assumes independent rows; rows from one game "
                 "are correlated, so read it as optimistic"),
    }


def arm_summary(scored: list[dict], bins: int, *, scale_free_only: bool = False,
                ladder_table: bool = True) -> dict:
    if not scored:
        return {"n_rows": 0}
    n = len(scored)
    s = [x["s"] for x in scored]
    out: dict[str, Any] = {
        "n_rows": n,
        "n_games": len({x["game"] for x in scored}),
        "n_players": len({x["player"] for x in scored}),
        "n_capture_times": len({x["as_of"] for x in scored}),
    }
    if not scale_free_only:
        out.update({
            "mae": _r(sum(r["abs"] for r in s) / n),
            "rmse": _r(math.sqrt(sum(r["sq"] for r in s) / n)),
            "bias_mean_minus_actual": _r(sum(r["err"] for r in s) / n),
            "mae_of_median": _r(sum(r["abs_median"] for r in s) / n),
            "crps_quantile_approx": _r(sum(r["crps_q"] for r in s) / n),
            "crps_quantile_levels_min": min(r["crps_levels"] for r in s),
            "crps_quantile_levels_max": max(r["crps_levels"] for r in s),
        })
        cl = [r["crps_ladder"] for r in s if r["crps_ladder"] is not None]
        out["crps_ladder_threshold_decomposition"] = _r(_mean(cl))
        out["n_crps_ladder"] = len(cl)
    cov: dict[str, dict] = {"p10_p90": {"nominal": 0.8, "observed": _r(sum(r["cov80"] for r in s) / n), "n": n}}
    agg: dict[str, list] = defaultdict(list)
    nominal: dict[str, float] = {}
    for r in s:
        for name, (nom, hit) in r["intervals"].items():
            agg[name].append(hit)
            nominal[name] = nom
    for name in sorted(agg, key=lambda k: -nominal[k]):
        if name == "q0.1_q0.9":
            continue
        cov[name] = {"nominal": _r(nominal[name]), "observed": _r(_mean(agg[name])), "n": len(agg[name])}
    out["interval_coverage"] = cov
    qtab: dict[float, list] = defaultdict(list)
    for r in s:
        for q, pair in r["below"].items():
            qtab[q].append(pair)
    out["quantile_calibration"] = [
        {"q": q, "n": len(v), "frac_actual_below": _r(sum(a for a, _ in v) / len(v)),
         "frac_actual_at_or_below": _r(sum(b for _, b in v) / len(v))}
        for q, v in sorted(qtab.items())]
    out["pit"] = pit_histogram([r["pit"] for r in s], bins)
    lad = [r for r in s if r.get("rungs")]
    out["n_rows_with_ladder"] = len(lad)
    if lad:
        out["brier_mean_over_rungs"] = _r(sum(r["brier"] for r in lad) / len(lad))
        out["log_score_mean_over_rungs"] = _r(sum(r["log"] for r in lad) / len(lad))
        if not scale_free_only:
            out["rps_mean"] = _r(sum(r["rps"] for r in lad) / len(lad))
            out["rps_normalized_mean"] = _r(sum(r["rps_norm"] for r in lad) / len(lad))
    if ladder_table and lad:
        per: dict[float, list] = defaultdict(list)
        for r in lad:
            for t, v in r["rungs"].items():
                per[t].append(v)
        out["ladder"] = [{
            "at_least": t, "n": len(v),
            "mean_probability": _r(sum(x["p"] for x in v) / len(v)),
            "observed_frequency": _r(sum(x["o"] for x in v) / len(v)),
            "brier": _r(sum(x["brier"] for x in v) / len(v)),
            "log_score": _r(sum(x["log"] for x in v) / len(v)),
            "n_log_clipped": sum(1 for x in v if x["clipped"]),
        } for t, v in sorted(per.items())]
    return out


def h2h_summary(pairs: list[dict], role: str, clusters: list[str], bootstrap: int, seed: int) -> dict:
    if not pairs:
        return {"n_pairs": 0, "role": role, "note": ROLE_NOTES.get(role, "")}
    out: dict[str, Any] = {
        "role": role, "note": ROLE_NOTES.get(role, ""),
        "n_pairs": len(pairs), "n_games": len({p["game"] for p in pairs}),
        "n_players": len({p["player"] for p in pairs}),
        "few_game_clusters_warning": len({p["game"] for p in pairs}) < 20,
        "n_pairs_with_common_ladder": sum(1 for p in pairs if p["n_common_strikes"]),
        "min_common_quantile_levels": min(p["n_common_levels"] for p in pairs),
        "deltas_challenger_minus_comparator": {},
    }
    cis = {c: cluster_bootstrap(pairs, [f"d_{m}" for m in H2H_METRICS], c, bootstrap, seed) for c in clusters}
    for m in H2H_METRICS:
        vals = [p[f"d_{m}"] for p in pairs if p[f"d_{m}"] is not None]
        if not vals:
            continue
        a = [p[f"a_{m}"] for p in pairs if p[f"d_{m}"] is not None]
        b = [p[f"b_{m}"] for p in pairs if p[f"d_{m}"] is not None]
        entry: dict[str, Any] = {"n": len(vals), "challenger": _r(sum(a) / len(a)),
                                 "comparator": _r(sum(b) / len(b)), "delta": _r(sum(vals) / len(vals))}
        for c in clusters:
            ci = cis[c][f"d_{m}"]
            entry[f"ci95_{c}_clustered"] = ci
            if m in LOSS_METRICS:
                entry[f"reading_{c}_clustered"] = (
                    "NO_CI" if ci is None else "CHALLENGER_LOWER_LOSS" if ci[1] < 0
                    else "CHALLENGER_HIGHER_LOSS" if ci[0] > 0 else "NOT_DISTINGUISHABLE")
        out["deltas_challenger_minus_comparator"][m] = entry
    d = out["deltas_challenger_minus_comparator"]
    if "sq" in d:
        rm_a = math.sqrt(sum(p["a_sq"] for p in pairs) / len(pairs))
        rm_b = math.sqrt(sum(p["b_sq"] for p in pairs) / len(pairs))
        out["rmse"] = {"challenger": _r(rm_a), "comparator": _r(rm_b), "delta": _r(rm_a - rm_b),
                       "note": "CI is on delta MSE (sq), the additive quantity"}
    out["status"] = "DESCRIPTIVE_ONLY_NOT_AUTOMATIC_PROMOTION"
    return out


# ---------------------------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------------------------

def capture_window(ctx: dict) -> str:
    f: Forecast = ctx["forecast_obj"]
    if f.kickoff is None:
        return "no_kickoff"
    h = (f.kickoff - f.as_of).total_seconds() / 3600.0
    for limit, name in CAPTURE_WINDOWS:
        if h <= limit:
            return name
    return "gt_72h"


def _resolve(obj: Any, path: list[str]) -> Any:
    for part in path:
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj


def path_getter(path: str) -> Callable[[dict], Any]:
    """Key function from a dotted path. Looked up in the challenger row, then the outcome row.
    ``lineage.NAME`` reads a feature_lineage value; ``forecast.``/``outcome.`` force the source."""
    parts = path.split(".")

    def get(ctx: dict) -> Any:
        if parts[0] == "lineage" and len(parts) == 2:
            for item in ctx["forecast"].get("feature_lineage") or []:
                if isinstance(item, dict) and item.get("name") == parts[1]:
                    return item.get("value")
            return None
        if parts[0] in ("forecast", "outcome"):
            return _resolve(ctx[parts[0]], parts[1:])
        v = _resolve(ctx["forecast"], parts)
        return v if v is not None else _resolve(ctx["outcome"], parts)
    return get


def binned(getter: Callable[[dict], Any], edges: list[float]) -> Callable[[dict], Any]:
    edges = sorted(edges)

    def get(ctx: dict) -> Any:
        v = getter(ctx)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
        if v < edges[0]:
            return f"lt_{edges[0]:g}"
        for a, b in zip(edges, edges[1:]):
            if a <= v < b:
                return f"{a:g}_to_{b:g}"
        return f"ge_{edges[-1]:g}"
    return get


BUILTIN_BREAKDOWNS: dict[str, Callable[[dict], Any]] = {
    "capture_window": capture_window,
    "player": lambda ctx: ctx["forecast_obj"].ident[2],
    "game": lambda ctx: ctx["forecast_obj"].ident[1],
    "market_listed": lambda ctx: {True: "listed", False: "not_listed", None: "unknown"}[ctx["listed"]],
}


def parse_breakdown_spec(spec: str) -> tuple[str, Callable[[dict], Any]]:
    """``NAME=PATH[|bins=a,b,...]``, ``PATH``, or a builtin (capture_window, player, game, market_listed)."""
    body, _, opts = spec.partition("|")
    name, eq, path = body.partition("=")
    if not eq:
        name, path = body.split(".")[-1], body
    name, path = name.strip(), path.strip()
    if not name or not path:
        raise ScorecardError(f"bad breakdown spec {spec!r}")
    fn = BUILTIN_BREAKDOWNS.get(path) or path_getter(path)
    if opts:
        k, _, v = opts.partition("=")
        if k.strip() != "bins":
            raise ScorecardError(f"bad breakdown option {opts!r}; only bins=a,b,... is supported")
        try:
            edges = [float(x) for x in v.split(",") if x.strip()]
        except ValueError as exc:
            raise ScorecardError(f"bad bins in {spec!r}") from exc
        if not edges:
            raise ScorecardError(f"bad bins in {spec!r}")
        fn = binned(fn, edges)
    return name, fn


def _label(v: Any) -> str:
    if v is None or v == "":
        return "UNKNOWN"
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


# ---------------------------------------------------------------------------------------------
# The scorecard
# ---------------------------------------------------------------------------------------------

def score(challenger_rows: list[dict], outcome_rows: list[dict], *,
          comparators: dict[str, dict] | None = None,
          market_rows: list[dict] | None = None,
          eligible_rows: list[dict] | None = None,
          breakdowns: dict[str, Callable[[dict], Any]] | None = None,
          cluster_by: Iterable[str] = ("game",),
          bootstrap: int = 1000, breakdown_bootstrap: int = 200, seed: int = 20261010,
          pit_bins: int = 10, validate: bool = True,
          challenger_label: str = "challenger", challenger_contract: str = CONTRACT_PURE) -> dict:
    """Score the challenger (and each comparator) against outcomes.

    comparators: {name: {"rows": [...], "role": one of ROLES, "contract": "pure_forecast" | "projection_only",
                         "conditional_on_playing": optional default for rows without the field}}
    market_rows: the separate market family (listings/quotes with open_time and observed_at). Defines the
                 pregame market-listed cohort only. None means no cohort is reported.
    eligible_rows: identities (optionally with as_of) defining the full eligible population. When absent the
                   population is the union of challenger forecasts and outcomes, and a forecast missing for an
                   identity with neither a forecast nor an outcome cannot be seen (reported as such).
    breakdowns: {name: key_function(ctx)}. ctx has forecast (challenger row), outcome (row), forecast_obj,
                listed (bool | None). A key function returning None is labelled UNKNOWN.
    """
    clusters = list(dict.fromkeys(cluster_by))
    for c in clusters:
        if c not in ("game", "player"):
            raise ScorecardError(f"cluster must be 'game' or 'player', got {c!r}")
    if pit_bins < 2:
        raise ScorecardError("pit_bins must be >= 2")
    arms: dict[str, dict] = {}
    chal = parse_forecasts(challenger_rows, contract=challenger_contract, label=challenger_label,
                           validate=validate)
    arms[challenger_label] = {"role": ROLE_CHALLENGER, "forecasts": chal, "contract": challenger_contract}
    for name, spec in (comparators or {}).items():
        if name in arms:
            raise ScorecardError(f"duplicate arm name {name!r}")
        role = spec.get("role", "other")
        if role not in ROLES:
            raise ScorecardError(f"unknown role {role!r} for {name}; one of {ROLES}")
        contract = spec.get("contract") or (CONTRACT_PROJECTION if role == "market_informed_benchmark"
                                            else CONTRACT_PURE)
        arms[name] = {"role": role, "contract": contract,
                      "forecasts": parse_forecasts(spec["rows"], contract=contract, label=name, validate=validate,
                                                   default_conditional=spec.get("conditional_on_playing"))}
    outcomes = parse_outcomes(outcome_rows, validate=validate)
    cohort = Cohort(market_rows)

    eligible_ref: dict[tuple, datetime | None] = {}
    if eligible_rows is not None:
        for i, r in enumerate(eligible_rows, 1):
            try:
                k = identity(r)
            except ScorecardError as exc:
                raise ScorecardError(f"eligible row {i}: {exc}") from exc
            t = opt_ts(r.get("as_of"))
            prev = eligible_ref.get(k)
            eligible_ref[k] = t if prev is None or (t is not None and t < prev) else prev
        eligible_source = "explicit eligible list"
    else:
        for k in {f.ident for f in chal.values()} | set(outcomes):
            eligible_ref[k] = None
        eligible_source = ("union of challenger forecasts and outcomes (no eligible list given: an identity "
                           "with neither a forecast nor an outcome is invisible)")
    by_ident: dict[str, dict[tuple, list[Forecast]]] = {}
    for name, arm in arms.items():
        d: dict[tuple, list[Forecast]] = defaultdict(list)
        for f in arm["forecasts"].values():
            d[f.ident].append(f)
        by_ident[name] = d

    def reference_as_of(k: tuple) -> datetime | None:
        t = eligible_ref.get(k)
        if t is not None:
            return t
        for name in arms:
            fs = by_ident[name].get(k)
            if fs:
                return min(f.as_of for f in fs)
        return cohort.reference(k)

    populations = [POP_FULL] + ([POP_COHORT] if cohort.available else [])
    bks = dict(breakdowns or {})

    def in_pop(pop: str, listed: bool | None) -> bool:
        return pop == POP_FULL or listed is True

    report_pops: dict[str, Any] = {}
    for pop in populations:
        # ---- accounting
        accounting: dict[str, Any] = {"arms": {}}
        for name, arm in arms.items():
            acc = Counter()
            by_stat: dict[str, Counter] = defaultdict(Counter)
            missing_examples = []
            for k in sorted(eligible_ref):
                ref = reference_as_of(k)
                listed = cohort.listed(k, ref)
                if pop == POP_COHORT and listed is None:
                    acc["eligible_cohort_membership_unknown"] += 1
                    continue
                if not in_pop(pop, listed):
                    continue
                st = f"{k[0]}:{k[3]}"
                acc["eligible_identities"] += 1
                by_stat[st]["eligible_identities"] += 1
                if not by_ident[name].get(k):
                    acc["missing_forecast"] += 1
                    by_stat[st]["missing_forecast"] += 1
                    if k in outcomes:
                        acc["missing_forecast_with_outcome"] += 1
                    if len(missing_examples) < 20:
                        missing_examples.append(list(k))
            for f in arm["forecasts"].values():
                listed = cohort.listed(f.ident, f.as_of)
                if not in_pop(pop, listed):
                    continue
                st = f"{f.ident[0]}:{f.ident[3]}"
                acc["forecast_rows"] += 1
                by_stat[st]["forecast_rows"] += 1
                if f.ident not in eligible_ref:
                    acc["forecast_rows_outside_eligible_population"] += 1
                    by_stat[st]["forecast_rows_outside_eligible_population"] += 1
                    continue
                o = outcomes.get(f.ident)
                if o is None:
                    acc["forecast_rows_missing_outcome"] += 1
                    by_stat[st]["forecast_rows_missing_outcome"] += 1
                elif f.conditional and not o["played"]:
                    acc["conditional_dnp_excluded"] += 1
                    by_stat[st]["conditional_dnp_excluded"] += 1
                else:
                    acc["scored_rows"] += 1
                    by_stat[st]["scored_rows"] += 1
            idents = {f.ident for f in arm["forecasts"].values()
                      if in_pop(pop, cohort.listed(f.ident, f.as_of))}
            acc["identities_with_forecast"] = len(idents)
            acc["identities_with_multiple_capture_times"] = sum(
                1 for k in idents if len(by_ident[name][k]) > 1)
            accounting["arms"][name] = {"role": arm["role"], "contract": arm["contract"], **dict(sorted(acc.items())),
                                "missing_forecast_examples": missing_examples,
                                "by_statistic": {s: dict(sorted(c.items())) for s, c in sorted(by_stat.items())}}
        accounting["outcome_rows"] = len(outcomes)
        accounting["outcomes_outside_eligible_population"] = sum(1 for k in outcomes if k not in eligible_ref)

        # ---- per-arm scoring
        scored: dict[str, list[dict]] = {}
        for name, arm in arms.items():
            rows = []
            for f in sorted(arm["forecasts"].values(), key=lambda f: f.fkey):
                if f.ident not in eligible_ref:
                    continue
                listed = cohort.listed(f.ident, f.as_of)
                if not in_pop(pop, listed):
                    continue
                o = outcomes.get(f.ident)
                if o is None or (f.conditional and not o["played"]):
                    continue
                y = float(o["actual"])
                rows.append({"f": f, "o": o, "y": y, "s": score_row(f, y), "listed": listed,
                             "stat": f"{f.ident[0]}:{f.ident[3]}", "game": f"{f.ident[0]}|{f.ident[1]}",
                             "player": f"{f.ident[0]}|{f.ident[2]}", "as_of": iso(f.as_of)})
            scored[name] = rows

        # ---- head-to-head pairs (identical identity and as_of)
        pairs: dict[str, list[dict]] = {}
        pair_acc: dict[str, dict] = {}
        for name, arm in arms.items():
            if name == challenger_label:
                continue
            comp = arm["forecasts"]
            cnt = Counter()
            ps = []
            for x in scored[challenger_label]:
                f = x["f"]
                g = comp.get(f.fkey)
                if g is None:
                    if by_ident[name].get(f.ident):
                        cnt["comparator_has_identity_at_other_as_of"] += 1
                    else:
                        cnt["comparator_missing"] += 1
                    continue
                if g.conditional != f.conditional:
                    cnt["conditionality_mismatch_excluded"] += 1
                    continue
                p = paired_scores(f, g, x["y"])
                p.update({"game": x["game"], "player": x["player"], "stat": x["stat"], "x": x})
                ps.append(p)
            cnt["paired"] = len(ps)
            pairs[name] = ps
            pair_acc[name] = dict(sorted(cnt.items()))

        # ---- group summaries
        def summarize(group_rows: dict[str, list[dict]], group_pairs: dict[str, list[dict]], boot: int,
                      ladder_table: bool = True) -> dict:
            out: dict[str, Any] = {"arms": {}, "head_to_head": {}}
            for name in arms:
                out["arms"][name] = arm_summary(group_rows.get(name, []), pit_bins, ladder_table=ladder_table)
            for name, ps in group_pairs.items():
                out["head_to_head"][name] = h2h_summary(ps, arms[name]["role"], clusters, boot, seed)
            return out

        stats = sorted({x["stat"] for rows in scored.values() for x in rows})
        by_statistic = {}
        for st in stats:
            by_statistic[st] = summarize({n: [x for x in rows if x["stat"] == st] for n, rows in scored.items()},
                                         {n: [p for p in ps if p["stat"] == st] for n, ps in pairs.items()},
                                         bootstrap)
        pooled = {n: arm_summary(rows, pit_bins, scale_free_only=True, ladder_table=False)
                  for n, rows in scored.items()}

        breakdown_out: dict[str, Any] = {}
        for bname, fn in bks.items():
            labels: dict[int, str] = {}
            for rows in scored.values():
                for x in rows:
                    if id(x) not in labels:
                        ctx = {"forecast": x["f"].row, "outcome": x["o"], "forecast_obj": x["f"],
                               "listed": x["listed"]}
                        labels[id(x)] = _label(fn(ctx))
            # comparator rows are labelled by the challenger row for the same (identity, as_of) when there
            # is one, so every arm is broken down by the same attribute values
            chal_label = {x["f"].fkey: labels[id(x)] for x in scored[challenger_label]}
            for name, rows in scored.items():
                for x in rows:
                    if x["f"].fkey in chal_label:
                        labels[id(x)] = chal_label[x["f"].fkey]
            grow: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
            gpair: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
            for n, rows in scored.items():
                for x in rows:
                    grow[(x["stat"], labels[id(x)])][n].append(x)
            for n, ps in pairs.items():
                for p in ps:
                    gpair[(p["stat"], labels[id(p["x"])])][n].append(p)
            groups: dict[str, dict] = {st: {} for st in stats}
            for st, v in sorted(grow):
                groups[st][v] = summarize(grow[(st, v)], {n: gpair[(st, v)].get(n, []) for n in pairs},
                                          breakdown_bootstrap, ladder_table=False)
            breakdown_out[bname] = groups

        report_pops[pop] = {
            "definition": ("every eligible player-game-statistic" if pop == POP_FULL else
                           "identities with a market-family listing that opened and was observed by our own "
                           "records at or before the forecast's as_of (pregame records only, never settlement)"),
            "accounting": accounting,
            "head_to_head_pairing": pair_acc,
            "by_statistic": by_statistic,
            "all_statistics_scale_free": pooled,
            "breakdowns": breakdown_out,
        }

    gate = load_gate() if validate else None
    return {
        "scorecard": "accuracy",
        "version": VERSION,
        "gate": getattr(gate, "GATE_VERSION", None),
        "status": "DESCRIPTIVE_ONLY_NOT_AUTOMATIC_PROMOTION",
        "config": {"challenger": challenger_label,
                   "arms": {n: {"role": a["role"], "contract": a["contract"]} for n, a in arms.items()},
                   "cluster_by": clusters, "bootstrap": bootstrap, "breakdown_bootstrap": breakdown_bootstrap,
                   "seed": seed, "pit_bins": pit_bins, "breakdowns": sorted(bks), "validated": validate,
                   "eligible_population": eligible_source,
                   "market_family": dict(sorted(cohort.stats.items())) if cohort.available else None},
        "metric_policy": (
            "point metrics use the forecast mean (MAE of the median reported too); scale-dependent metrics are "
            "per sport:statistic only; CRPS = 2 x mean pinball loss over published quantile levels (ladder "
            "variant: midpoint-rule threshold decomposition over the ladder's range); PIT is interval-censored "
            "from the published knots; Brier and log score per ladder rung; RPS = sum of rung Brier scores; "
            "head-to-head on identical (identity, as_of) rows using only the knots both arms publish, deltas are "
            "challenger minus comparator, percentile bootstrap resampling whole clusters"),
        "populations": report_pops,
        "note": ("Accuracy only. Never evidence of betting value (see value_scorecard). Promotion needs a "
                 "preregistered, untouched chronological holdout."),
    }


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

COMPARATOR_FLAGS = (("champion", "production_champion"), ("baseline", "statistical_baseline"),
                    ("independent", "independent_sports_only"), ("market_benchmark", "market_informed_benchmark"))


def _cli_score(args: argparse.Namespace) -> dict:
    cfg: dict[str, Any] = {}
    if args.config:
        with open(args.config, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if not isinstance(cfg, dict):
            raise ScorecardError("config must be a JSON object")
    base = Path(args.config).resolve().parent if args.config else Path.cwd()

    def pick(name: str, default: Any = None) -> Any:
        v = getattr(args, name, None)
        if v not in (None, []):
            return v
        return cfg.get(name, default)

    def path(p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() or not args.config else base / q

    challenger = pick("challenger")
    outcomes = pick("outcomes")
    if not challenger or not outcomes:
        raise ScorecardError("--challenger and --outcomes are required (on the command line or in --config)")
    projection_only = set(pick("projection_only", []) or [])
    comparators: dict[str, dict] = {}
    for flag, role in COMPARATOR_FLAGS:
        p = pick(flag)
        if p:
            comparators[flag] = {"rows": load_jsonl(path(p)), "role": role,
                                 "contract": CONTRACT_PROJECTION if (flag in projection_only or
                                                                     role == "market_informed_benchmark")
                                 else CONTRACT_PURE}
    for spec in pick("comparator", []) or []:
        if isinstance(spec, dict):
            name, role, p, contract = spec["name"], spec.get("role", "other"), spec["path"], spec.get("contract")
        else:
            head, eq, p = str(spec).partition("=")
            name, _, role = head.partition(":")
            role = role or "other"
            if not eq or not name:
                raise ScorecardError(f"bad --comparator {spec!r}; use NAME[:ROLE]=PATH")
            contract = None
        comparators[name] = {"rows": load_jsonl(path(p)), "role": role,
                             "contract": contract or (CONTRACT_PROJECTION if name in projection_only or
                                                      role == "market_informed_benchmark" else CONTRACT_PURE)}
    breakdowns = dict(parse_breakdown_spec(s) for s in (pick("breakdown", []) or []))
    market = pick("market")
    eligible = pick("eligible")
    return score(
        load_jsonl(path(challenger)), load_jsonl(path(outcomes)),
        comparators=comparators,
        market_rows=load_jsonl(path(market)) if market else None,
        eligible_rows=load_jsonl(path(eligible)) if eligible else None,
        breakdowns=breakdowns,
        cluster_by=pick("cluster", ["game"]) or ["game"],
        bootstrap=int(pick("bootstrap", 1000)), breakdown_bootstrap=int(pick("breakdown_bootstrap", 200)),
        seed=int(pick("seed", 20261010)), pit_bins=int(pick("pit_bins", 10)),
        validate=not (args.no_validate or cfg.get("no_validate", False)),
        challenger_label=pick("challenger_label", "challenger"),
        challenger_contract=CONTRACT_PROJECTION if "challenger" in projection_only else CONTRACT_PURE)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="score a challenger (and comparators) against sports outcomes")
    s.add_argument("--config", help="JSON file whose keys mirror these options (command line wins)")
    s.add_argument("--challenger", help="challenger forecasts (pure_forecast.v1 JSONL)")
    s.add_argument("--challenger-label", dest="challenger_label")
    s.add_argument("--outcomes", help="sports outcomes in pure_gate outcome format (JSONL)")
    s.add_argument("--champion", help="production champion forecasts")
    s.add_argument("--baseline", help="simple statistical baseline forecasts")
    s.add_argument("--independent", help="independent sports-only model forecasts")
    s.add_argument("--market-benchmark", dest="market_benchmark",
                   help="market-informed benchmark forecasts (validated as projection_only)")
    s.add_argument("--comparator", action="append", default=[], help="extra arm NAME[:ROLE]=PATH")
    s.add_argument("--projection-only", dest="projection_only", action="append", default=[],
                   help="arm name (challenger, champion, baseline, independent, or a --comparator NAME) whose "
                        "rows are checked as projection_only instead of pure_forecast")
    s.add_argument("--market", help="market-family listings/quotes JSONL (defines the pregame-listed cohort)")
    s.add_argument("--eligible", help="eligible population JSONL (identity fields, optional as_of)")
    s.add_argument("--breakdown", action="append", default=[],
                   help="NAME=PATH[|bins=a,b] or builtin capture_window|player|game|market_listed")
    s.add_argument("--cluster", action="append", choices=["game", "player"], default=[])
    s.add_argument("--bootstrap", type=int)
    s.add_argument("--breakdown-bootstrap", dest="breakdown_bootstrap", type=int)
    s.add_argument("--seed", type=int)
    s.add_argument("--pit-bins", dest="pit_bins", type=int)
    s.add_argument("--no-validate", dest="no_validate", action="store_true",
                   help="skip pure_gate validation (structural checks still run)")
    s.add_argument("--output", help="write the JSON report here (default stdout)")
    args = ap.parse_args(argv)
    try:
        report = _cli_score(args)
    except (ScorecardError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc), "version": VERSION}), file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
