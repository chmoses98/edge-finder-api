#!/usr/bin/env python3
"""PURE forecast acceptance gate: validate, compare, and rerun-based market non-leakage proof.

Read-only research tooling for the shared PURE (market-independent) player-stat forecast contract.
Standard library only, Python 3.10+.

Subcommands
-----------
validate  Check a JSONL export against pure_forecast.v1 (or the kit interim format, read as v0).
compare   Paired champion-vs-challenger scorecard on matched player-game-stat rows with outcomes.
rerun     Runtime non-leakage proof: actually execute an upstream model with market inputs normal,
          deleted and randomized at an identical sports snapshot and seed, require bit-identical
          model-owned outputs, and require a market-reading negative control to change.
mutation  Kit-compatible diff of two pre-computed output files. It can DETECT a difference, but it
          never certifies non-leakage: identical copies prove nothing (exit status 3, NOT_CERTIFIED).

Nothing here reads or produces market prices for a PURE forecast. Market quotes belong in a
separate downstream comparison record (see ADOPTION.md).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

GATE_VERSION = "pure_gate 1.0.0"
MODE = "PURE_INDEPENDENT"
SCHEMA_V1 = "pure_forecast.v1"
SCHEMA_V0 = "pure_forecast.v0"  # the kit interim format; rows carry no schema_version field
TINY = 1e-9

# ---------------------------------------------------------------------------------------------
# Field sets
# ---------------------------------------------------------------------------------------------

IDENTITY = ("sport", "game_id", "player_id", "statistic")
V0_REQUIRED = ("sport", "game_id", "player_id", "statistic", "as_of", "kickoff",
               "source_max_observed_at", "projection_mode", "model_version",
               "conditional_on_playing", "projection", "model_features")
V1_REQUIRED = ("schema_version", "sport", "game_id", "player_id", "statistic", "as_of", "kickoff",
               "source_max_observed_at", "projection_mode", "model_version", "model_frozen_hash",
               "conditional_on_playing", "participation_probability", "projection", "sources",
               "feature_lineage")
V1_OPTIONAL_PREFIX = "x_"  # producer metadata (run ids, notes); excluded from the model signature
PROJECTION_REQUIRED = ("mean", "median", "p10", "p90", "thresholds")
PROJECTION_ALLOWED = PROJECTION_REQUIRED + ("quantiles",)
FEATURE_REQUIRED = ("name", "value", "source", "observed_at", "class")
SOURCE_REQUIRED = ("source_id", "class", "max_observed_at")
SOURCE_ALLOWED = SOURCE_REQUIRED + ("uri", "snapshot_sha256", "description")
SPORTS_ONLY = "sports_only"
FROZEN_HASH_RE = re.compile(r"^(sha256:[0-9a-f]{64}|git:[0-9a-f]{7,40})$")
OUTCOME_REQUIRED = IDENTITY + ("actual", "played")
OUTCOME_ALLOWED = OUTCOME_REQUIRED + ("source", "observed_at")

# Market vocabulary. Matching is token based (camelCase and non-alphanumerics split) so that e.g.
# "navigation" never matches "vig"; the kit's substring list is kept as well for key names so that
# everything the interim kit gate rejected is still rejected here.
MARKET_TOKENS = frozenset({
    "kalshi", "polymarket", "prophetx", "novig", "pinnacle", "draftkings", "fanduel", "betmgm",
    "caesars", "bookmaker", "bookmakers", "sportsbook", "sportsbooks", "odds", "spread", "spreads",
    "vig", "vigorish", "juice", "overround", "moneyline", "moneylines", "price", "prices", "priced",
    "pricing", "market", "markets", "wager", "wagers", "wagering", "bet", "bets", "betting",
    "implied", "clv", "consensus", "handicap", "parlay", "teaser", "ats", "ticker", "tickers",
    "settlement", "settlements", "quote", "quotes", "orderbook", "bid", "ask",
})
MARKET_PHRASES = (("closing", "line"), ("opening", "line"), ("money", "line"), ("over", "under"),
                  ("point", "spread"), ("implied", "total"), ("no", "vig"), ("team", "line"))
KIT_SUBSTRINGS = ("market", "odds", "implied", "bookmaker", "kalshi", "pinnacle", "consensus",
                  "betting", "wager", "price", "closing_line", "spread_line")
_TOKEN_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")

# Statistic names that encode a ladder rung ("receiving_yards_over_50", "points_25+").
_RUNG_SUFFIX_RE = re.compile(r"^(?P<base>.+?)[_\- ](?:over_|at_least_|ge_|gte_|o)?(?P<n>\d+(?:\.\d+)?)\+?$")
_RUNG_WORD_RE = re.compile(r"(?:^|[_\- ])(?:at_least|over_under|ladder|rung)(?:$|[_\- ])|\+$")


class GateError(ValueError):
    """A contract violation. The gate fails closed on every one of them."""


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------

def load_jsonl(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for n, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                val = json.loads(line, parse_constant=_reject_constant)
            except (ValueError, json.JSONDecodeError) as exc:
                raise GateError(f"{path}:{n}: invalid JSON: {exc}") from exc
            if not isinstance(val, dict):
                raise GateError(f"{path}:{n}: expected a JSON object per line")
            rows.append(val)
    return rows


def _reject_constant(token: str) -> Any:
    raise ValueError(f"non-finite JSON constant {token}")


def iso_utc(value: Any, desc: str = "timestamp") -> datetime:
    if not isinstance(value, str):
        raise GateError(f"{desc} must be an ISO-8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError(f"{desc}: invalid ISO-8601 timestamp {value!r}") from exc
    if result.tzinfo is None:
        raise GateError(f"{desc}: timezone required in timestamp {value!r}")
    return result.astimezone(timezone.utc)


def finite_number(value: Any, desc: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GateError(f"{desc} must be numeric")
    v = float(value)
    if not math.isfinite(v):
        raise GateError(f"{desc} must be finite")
    return v


def nonempty_str(value: Any, desc: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GateError(f"{desc} must be a nonempty string")
    return value


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def market_term(text: str, *, substrings: bool = False) -> str | None:
    """Return the offending market term in ``text`` or None."""
    toks = tokens(text)
    for t in toks:
        if t in MARKET_TOKENS:
            return t
    for a, b in MARKET_PHRASES:
        for i in range(len(toks) - 1):
            if toks[i] == a and toks[i + 1] == b:
                return f"{a}_{b}"
    if substrings:
        low = text.lower()
        for s in KIT_SUBSTRINGS:
            if s in low:
                return s
    return None


def scan_keys(obj: Any, location: str) -> None:
    """Fail on any market-like key anywhere below ``obj`` (and on non-finite numbers)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            hit = market_term(str(k), substrings=True)
            if hit:
                raise GateError(f"market-dependent key forbidden ({hit!r}): {location}.{k}")
            scan_keys(v, f"{location}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            scan_keys(item, f"{location}[{i}]")
    elif isinstance(obj, float) and not math.isfinite(obj):
        raise GateError(f"non-finite number at {location}")


def scan_value(value: Any, location: str) -> None:
    if isinstance(value, str):
        hit = market_term(value)
        if hit:
            raise GateError(f"market-dependent source/feature forbidden ({hit!r}) at {location}: {value!r}")


def key(row: dict) -> tuple[str, str, str, str]:
    return tuple(str(row[f]) for f in IDENTITY)  # type: ignore[return-value]


def schema_version(row: dict) -> str:
    if "schema_version" not in row:
        return SCHEMA_V0
    v = row["schema_version"]
    if v == SCHEMA_V1:
        return SCHEMA_V1
    raise GateError(f"unknown schema_version {v!r}; supported: {SCHEMA_V1} (and the unversioned kit "
                    f"interim format, read as {SCHEMA_V0})")


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------

def _validate_projection(p: Any, strict: bool) -> None:
    """strict (v1): no unknown keys. Lenient (v0 kit interim): unknown keys allowed, as the kit did,
    but every key is still scanned for market vocabulary."""
    if not isinstance(p, dict):
        raise GateError("projection must be an object")
    scan_keys(p, "projection")
    for n in PROJECTION_REQUIRED[:4]:
        if n not in p:
            raise GateError(f"projection missing {n}")
        finite_number(p[n], f"projection.{n}")
    if "thresholds" not in p or not isinstance(p["thresholds"], list):
        raise GateError("projection.thresholds must be a list (empty allowed)")
    extra = sorted(set(p) - set(PROJECTION_ALLOWED))
    if extra and strict:
        raise GateError(f"unexpected projection fields {extra}")
    if not p["p10"] <= p["median"] <= p["p90"]:
        raise GateError("quantile order must be p10 <= median <= p90")
    last_p, last_t = float("inf"), -float("inf")
    for i, t in enumerate(p["thresholds"]):
        if not isinstance(t, dict) or "at_least" not in t or "probability" not in t:
            raise GateError(f"projection.thresholds[{i}] requires at_least and probability")
        if strict and set(t) != {"at_least", "probability"}:
            raise GateError(f"projection.thresholds[{i}] must be exactly {{at_least, probability}}")
        value = finite_number(t["at_least"], f"thresholds[{i}].at_least")
        prob = finite_number(t["probability"], f"thresholds[{i}].probability")
        if not 0.0 <= prob <= 1.0:
            raise GateError(f"thresholds[{i}].probability {prob} outside [0,1]")
        if value <= last_t:
            raise GateError("thresholds must be strictly ascending in at_least (one entry per rung)")
        if prob > last_p + TINY:
            raise GateError("threshold ladder must be monotone: P(X >= t) non-increasing in t")
        last_t, last_p = value, prob
    if "quantiles" in p:
        qs = p["quantiles"]
        if not isinstance(qs, list):
            raise GateError("projection.quantiles must be a list")
        merged = {0.1: float(p["p10"]), 0.5: float(p["median"]), 0.9: float(p["p90"])}
        last_q = 0.0
        for i, item in enumerate(qs):
            if not isinstance(item, dict) or set(item) != {"q", "value"}:
                raise GateError(f"projection.quantiles[{i}] must be exactly {{q, value}}")
            q = finite_number(item["q"], f"quantiles[{i}].q")
            v = finite_number(item["value"], f"quantiles[{i}].value")
            if not 0.0 < q < 1.0 or q <= last_q:
                raise GateError("quantile levels must be strictly ascending inside (0,1)")
            last_q = q
            for anchor in (0.1, 0.5, 0.9):
                if abs(q - anchor) < TINY and abs(v - merged[anchor]) > TINY:
                    raise GateError(f"quantile q={anchor} disagrees with p10/median/p90")
            merged[round(q, 12)] = v
        ordered = [merged[q] for q in sorted(merged)]
        if any(b < a - TINY for a, b in zip(ordered, ordered[1:])):
            raise GateError("quantile values must be non-decreasing in q")


def _validate_common(row: dict) -> tuple[datetime, datetime, datetime]:
    for name in IDENTITY + ("model_version",):
        nonempty_str(row[name], name)
    if row["projection_mode"] != MODE:
        raise GateError(f"projection_mode must be {MODE}, got {row['projection_mode']!r}")
    if not isinstance(row["conditional_on_playing"], bool):
        raise GateError("conditional_on_playing must be boolean")
    cutoff = iso_utc(row["as_of"], "as_of")
    kickoff = iso_utc(row["kickoff"], "kickoff")
    observed = iso_utc(row["source_max_observed_at"], "source_max_observed_at")
    if not observed <= cutoff < kickoff:
        raise GateError("require source_max_observed_at <= as_of < kickoff (source cutoff, forecast "
                        "cutoff, game start)")
    statistic = row["statistic"]
    if _RUNG_WORD_RE.search(statistic):
        raise GateError(f"statistic {statistic!r} encodes a ladder rung; emit one row per "
                        "player-game-stat with the ladder inside projection.thresholds")
    return observed, cutoff, kickoff


def validate_v0(row: dict) -> None:
    """Kit interim format (unversioned). Same rules as the kit prototype, plus token-based market scan."""
    missing = [f for f in V0_REQUIRED if f not in row]
    if missing:
        raise GateError(f"missing required fields {missing}")
    extra = sorted(set(row) - set(V0_REQUIRED))
    if extra:
        raise GateError(f"unexpected fields in PURE forecast; keep market output separate: {extra}")
    _validate_common(row)
    _validate_projection(row["projection"], strict=False)
    if not isinstance(row["model_features"], dict):
        raise GateError("model_features must be an object")
    scan_keys(row["model_features"], "model_features")


def validate_v1(row: dict) -> None:
    missing = [f for f in V1_REQUIRED if f not in row]
    if missing:
        raise GateError(f"missing required fields {missing}")
    extra = sorted(k for k in set(row) - set(V1_REQUIRED) if not k.startswith(V1_OPTIONAL_PREFIX))
    if extra:
        raise GateError(f"unexpected fields in PURE forecast; market data belongs in a separate "
                        f"comparison record: {extra}")
    # Market vocabulary is refused in every key of the row, including x_ metadata.
    scan_keys(row, "row")
    observed, _cutoff, _kickoff = _validate_common(row)
    if not isinstance(row["model_frozen_hash"], str) or not FROZEN_HASH_RE.match(row["model_frozen_hash"]):
        raise GateError("model_frozen_hash must be 'sha256:<64 hex>' or 'git:<7-40 hex>'")
    pp = row["participation_probability"]
    if pp is not None:
        pp = finite_number(pp, "participation_probability")
        if not 0.0 <= pp <= 1.0:
            raise GateError("participation_probability must be in [0,1] or null (not modeled)")
    _validate_projection(row["projection"], strict=True)
    if not row["conditional_on_playing"] and pp is not None:
        for t in row["projection"]["thresholds"]:
            if t["at_least"] > 0 and t["probability"] > pp + TINY:
                raise GateError("unconditional forecast: P(X >= t) for t > 0 cannot exceed "
                                "participation_probability")

    sources = row["sources"]
    if not isinstance(sources, list) or not sources:
        raise GateError("sources must be a nonempty list")
    source_time: dict[str, datetime] = {}
    for i, s in enumerate(sources):
        loc = f"sources[{i}]"
        if not isinstance(s, dict):
            raise GateError(f"{loc} must be an object")
        miss = [f for f in SOURCE_REQUIRED if f not in s]
        if miss:
            raise GateError(f"{loc} missing {miss}")
        bad = sorted(set(s) - set(SOURCE_ALLOWED))
        if bad:
            raise GateError(f"{loc} unexpected fields {bad}")
        sid = nonempty_str(s["source_id"], f"{loc}.source_id")
        if sid in source_time:
            raise GateError(f"duplicate source_id {sid!r}")
        if s["class"] != SPORTS_ONLY:
            raise GateError(f"{loc}.class must be {SPORTS_ONLY!r}, got {s['class']!r}")
        for f in ("source_id", "uri", "description"):
            if f in s:
                scan_value(s[f], f"{loc}.{f}")
        t = iso_utc(s["max_observed_at"], f"{loc}.max_observed_at")
        if t > observed:
            raise GateError(f"{loc}.max_observed_at is after source_max_observed_at (source cutoff)")
        source_time[sid] = t

    lineage = row["feature_lineage"]
    if not isinstance(lineage, list) or not lineage:
        raise GateError("feature_lineage must be a nonempty list")
    names: set[str] = set()
    for i, f in enumerate(lineage):
        loc = f"feature_lineage[{i}]"
        if not isinstance(f, dict):
            raise GateError(f"{loc} must be an object")
        miss = [x for x in FEATURE_REQUIRED if x not in f]
        if miss:
            raise GateError(f"{loc} missing {miss}")
        if set(f) != set(FEATURE_REQUIRED):
            raise GateError(f"{loc} unexpected fields {sorted(set(f) - set(FEATURE_REQUIRED))}")
        name = nonempty_str(f["name"], f"{loc}.name")
        if name in names:
            raise GateError(f"duplicate feature name {name!r}")
        names.add(name)
        scan_value(name, f"{loc}.name")
        src = nonempty_str(f["source"], f"{loc}.source")
        scan_value(src, f"{loc}.source")
        if src not in source_time:
            raise GateError(f"{loc}.source {src!r} is not declared in sources")
        if f["class"] != SPORTS_ONLY:
            raise GateError(f"{loc}.class must be {SPORTS_ONLY!r}, got {f['class']!r}")
        v = f["value"]
        if v is not None and not isinstance(v, (str, bool, int, float)):
            raise GateError(f"{loc}.value must be a scalar or null")
        if isinstance(v, float) and not math.isfinite(v):
            raise GateError(f"{loc}.value must be finite")
        ts = iso_utc(f["observed_at"], f"{loc}.observed_at")
        if ts > observed:
            raise GateError(f"lineage cutoff violation: {loc} ({name}) observed_at {f['observed_at']} "
                            f"is after source_max_observed_at {row['source_max_observed_at']}")
        if ts > source_time[src]:
            raise GateError(f"lineage cutoff violation: {loc} ({name}) observed after its source "
                            f"{src!r} max_observed_at")


def validate(row: dict) -> str:
    """Validate one row. Returns its schema version."""
    if not isinstance(row, dict):
        raise GateError("row must be an object")
    version = schema_version(row)
    (validate_v1 if version == SCHEMA_V1 else validate_v0)(row)
    return version


def assert_no_ladder_rows(keys: Iterable[tuple[str, str, str, str]]) -> None:
    """Refuse ladder rungs exported as separate player-game rows (they would be pooled in scoring)."""
    seen: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for sport, game, player, stat in keys:
        m = _RUNG_SUFFIX_RE.match(stat)
        if m:
            seen[(sport, game, player, m.group("base"))].add(m.group("n"))
    for k, rungs in seen.items():
        if len(rungs) >= 2:
            raise GateError(f"ladder rungs exported as separate rows for {k[:3]} statistic {k[3]!r} "
                            f"({sorted(rungs)}); pooling rungs as player-games is refused - emit one "
                            "row per player-game-stat with projection.thresholds")


def index_rows(rows: list[dict], *, check: bool = True) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for i, row in enumerate(rows, 1):
        if check:
            try:
                validate(row)
            except GateError as exc:
                gid = row.get("game_id", "?") if isinstance(row, dict) else "?"
                raise GateError(f"row {i} ({gid}): {exc}") from exc
        k = key(row)
        if k in out:
            raise GateError(f"duplicate player-game-statistic row {k}")
        out[k] = row
    assert_no_ladder_rows(out)
    return out


def validate_file_summary(rows: list[dict], *, require_v1: bool = False) -> dict:
    idx = index_rows(rows)
    versions = Counter(schema_version(r) for r in idx.values())
    if require_v1 and versions.get(SCHEMA_V0):
        raise GateError(f"{versions[SCHEMA_V0]} row(s) use the v0 interim format but --require-v1 was set")
    warnings = []
    if versions.get(SCHEMA_V0):
        warnings.append("v0 interim rows carry no per-feature source/observed_at lineage, no frozen model "
                        "hash and no participation probability; their lineage is NOT verified")
    not_modeled = sum(1 for r in idx.values() if r.get("participation_probability", None) is None
                      and schema_version(r) == SCHEMA_V1)
    if not_modeled:
        warnings.append(f"{not_modeled} v1 row(s) declare participation_probability=null (not modeled)")
    return {"passed": True, "n": len(idx), "mode": MODE, "schema_versions": dict(sorted(versions.items())),
            "conditional_on_playing": sum(1 for r in idx.values() if r["conditional_on_playing"]),
            "statistics": dict(sorted(Counter(f"{k[0]}:{k[3]}" for k in idx).items())),
            "warnings": warnings, "gate": GATE_VERSION,
            "note": "Validates an exported artifact. Runtime independence is proven only by `rerun`."}


# ---------------------------------------------------------------------------------------------
# Model signature and the (non-certifying) two-file diff
# ---------------------------------------------------------------------------------------------

def model_owned(row: dict) -> dict:
    """Fields the model owns; x_ producer metadata (run ids, wall-clock notes) is excluded."""
    return {k: v for k, v in row.items() if not k.startswith(V1_OPTIONAL_PREFIX)}


def model_signature(row: dict) -> str:
    blob = json.dumps(model_owned(row), sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def mutate_compare(base: list[dict], mutated: list[dict]) -> dict:
    a, b = index_rows(base), index_rows(mutated)
    added, removed = sorted(set(b) - set(a)), sorted(set(a) - set(b))
    modified = [k for k in sorted(set(a) & set(b)) if model_signature(a[k]) != model_signature(b[k])]
    identical = not (added or removed or modified)
    return {
        "passed": identical,
        "outputs_identical": identical,
        "certified": False,
        "verdict": "NOT_CERTIFIED_PRECOMPUTED_FILES" if identical else "FAIL_OUTPUTS_DIFFER",
        "base_rows": len(a), "mutated_rows": len(b),
        "changed_model_outputs": [list(x) for x in modified[:20]],
        "missing_in_mutated": [list(x) for x in removed[:20]],
        "added_in_mutated": [list(x) for x in added[:20]],
        "note": ("Two pre-computed files cannot prove non-leakage (they may be copies). A difference "
                 "is evidence of market dependence; identity is not evidence of independence. Use "
                 "`rerun` to certify."),
    }


# ---------------------------------------------------------------------------------------------
# Paired comparison
# ---------------------------------------------------------------------------------------------

def outcomes_index(rows: list[dict]) -> dict[tuple, dict]:
    d: dict[tuple, dict] = {}
    for r in rows:
        if not isinstance(r, dict) or not all(k in r for k in OUTCOME_REQUIRED):
            raise GateError("outcome missing sport/game_id/player_id/statistic/actual/played")
        extra = sorted(set(r) - set(OUTCOME_ALLOWED))
        if extra:
            raise GateError(f"outcome has unexpected fields {extra}: one sports-truth row per "
                            "player-game-stat, no per-rung or market settlement fields")
        scan_keys(r, "outcome")
        if "source" in r:
            nonempty_str(r["source"], "outcome source")
            scan_value(r["source"], "outcome.source")
        if "observed_at" in r:
            iso_utc(r["observed_at"], "outcome observed_at")
        k = key(r)
        if k in d:
            raise GateError(f"duplicate outcome {k}")
        if not isinstance(r["played"], bool):
            raise GateError(f"outcome played not bool for {k}")
        finite_number(r["actual"], f"outcome actual {k}")
        d[k] = r
    assert_no_ladder_rows(d)
    return d


def selected_threshold(a: dict, b: dict) -> tuple[dict, dict] | None:
    """One representative rung per player-game: the common strike (exact numeric match) whose
    average of the two models' probabilities is closest to 0.5; ties -> lower strike. The choice is
    outcome-blind and symmetric in the two models."""
    br = {float(t["at_least"]): t for t in b["projection"]["thresholds"]}
    common = [(t, br[float(t["at_least"])]) for t in a["projection"]["thresholds"]
              if float(t["at_least"]) in br]
    if not common:
        return None
    return min(common, key=lambda pr: (abs((pr[0]["probability"] + pr[1]["probability"]) / 2 - 0.5),
                                       float(pr[0]["at_least"])))


def quantile_levels(row: dict) -> dict[float, float]:
    p = row["projection"]
    levels = {0.1: float(p["p10"]), 0.5: float(p["median"]), 0.9: float(p["p90"])}
    for item in p.get("quantiles", []) or []:
        levels[round(float(item["q"]), 12)] = float(item["value"])
    return levels


def crps_from_quantiles(levels: dict[float, float], common: list[float], y: float) -> float:
    """Quantile-score approximation of CRPS: 2 * mean pinball loss over the given levels."""
    total = 0.0
    for q in common:
        x = levels[q]
        total += (q - (1.0 if y < x else 0.0)) * (y - x)
    return 2.0 * total / len(common)


METRICS = ("abs", "sq", "err", "cov", "brier", "crps")


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 6)


def cluster_bootstrap(pairs: list[dict], bootstrap: int, seed: int) -> dict[str, list[float] | None]:
    """Paired, game-clustered percentile bootstrap of challenger-minus-champion metric means.

    Whole games are resampled with replacement; every metric uses the same resampled games."""
    clusters: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for p in pairs:
        c = clusters[p["cluster"]]
        for m in METRICS:
            d = p.get(f"d_{m}")
            if d is not None:
                c[m][0] += d
                c[m][1] += 1
    out: dict[str, list[float] | None] = {m: None for m in METRICS}
    if len(clusters) < 3 or bootstrap < 1:
        return out
    rng = random.Random(seed)
    names = sorted(clusters)
    draws: dict[str, list[float]] = {m: [] for m in METRICS}
    for _ in range(bootstrap):
        sums = {m: [0.0, 0] for m in METRICS}
        for _g in names:
            c = clusters[rng.choice(names)]
            for m in METRICS:
                if m in c:
                    sums[m][0] += c[m][0]
                    sums[m][1] += c[m][1]
        for m in METRICS:
            if sums[m][1]:
                draws[m].append(sums[m][0] / sums[m][1])
    for m in METRICS:
        d = sorted(draws[m])
        if len(d) >= max(1, bootstrap // 2):
            out[m] = [round(d[int((len(d) - 1) * 0.025)], 6), round(d[int((len(d) - 1) * 0.975)], 6)]
    return out


def per_stat_summary(pairs: list[dict], bootstrap: int = 1000, seed: int = 20261010) -> dict:
    if not pairs:
        return {"n_player_games": 0, "verdict": "NO_MATCHED_ROWS"}
    n = len(pairs)
    games = {p["cluster"] for p in pairs}
    ci = cluster_bootstrap(pairs, bootstrap, seed)
    bp = [p for p in pairs if p.get("brier_champ") is not None]
    cp = [p for p in pairs if p.get("crps_champ") is not None]
    mse_a = sum(p["sq_champ"] for p in pairs) / n
    mse_b = sum(p["sq_chal"] for p in pairs) / n
    return {
        "n_player_games": n,
        "n_games": len(games),
        "few_game_clusters_warning": len(games) < 20,
        "champion_mae": _r(sum(p["abs_champ"] for p in pairs) / n),
        "challenger_mae": _r(sum(p["abs_chal"] for p in pairs) / n),
        "delta_mae_challenger_minus_champion": _r(_mean([p["d_abs"] for p in pairs])),
        "delta_mae_game_cluster_bootstrap_95ci": ci["abs"],
        "champion_rmse": _r(math.sqrt(mse_a)),
        "challenger_rmse": _r(math.sqrt(mse_b)),
        "delta_mean_squared_error": _r(mse_b - mse_a),
        "delta_mse_game_cluster_bootstrap_95ci": ci["sq"],
        "champion_bias": _r(_mean([p["err_champ"] for p in pairs])),
        "challenger_bias": _r(_mean([p["err_chal"] for p in pairs])),
        "champion_p10_p90_coverage": _r(_mean([p["cov_champ"] for p in pairs])),
        "challenger_p10_p90_coverage": _r(_mean([p["cov_chal"] for p in pairs])),
        "delta_coverage_game_cluster_bootstrap_95ci": ci["cov"],
        "n_common_thresholds_scored_one_per_player_game": len(bp),
        "n_without_common_threshold": n - len(bp),
        "champion_brier": _r(_mean([p["brier_champ"] for p in bp])),
        "challenger_brier": _r(_mean([p["brier_chal"] for p in bp])),
        "delta_brier_game_cluster_bootstrap_95ci": ci["brier"],
        "n_crps": len(cp),
        "crps_min_common_quantile_levels": min((p["crps_levels"] for p in cp), default=None),
        "champion_crps_quantile_approx": _r(_mean([p["crps_champ"] for p in cp])),
        "challenger_crps_quantile_approx": _r(_mean([p["crps_chal"] for p in cp])),
        "delta_crps_game_cluster_bootstrap_95ci": ci["crps"],
        "status": "DESCRIPTIVE_ONLY_NOT_AUTOMATIC_PROMOTION",
    }


def compare(champion: list[dict], challenger: list[dict], outcomes: list[dict],
            bootstrap: int = 1000, seed: int = 20261010) -> dict:
    a, b, o = index_rows(champion), index_rows(challenger), outcomes_index(outcomes)
    both = set(a) & set(b)
    common = sorted(both & set(o))
    coverage = {
        "champion_rows": len(a), "challenger_rows": len(b), "outcome_rows": len(o),
        "champion_only_without_challenger": len(set(a) - set(b)),
        "challenger_only_without_champion": len(set(b) - set(a)),
        "matched_forecasts_without_outcome": len(both - set(o)),
        "outcomes_without_matched_forecasts": len(set(o) - both),
        "settled_with_both_forecasts": len(common),
    }
    by_stat: dict[str, list[dict]] = defaultdict(list)
    excl: dict[str, Counter] = defaultdict(Counter)
    for k in sorted(set(a) | set(b)):
        st = f"{k[0]}:{k[3]}"
        if k not in b:
            excl[st]["champion_only"] += 1
        elif k not in a:
            excl[st]["challenger_only"] += 1
        elif k not in o:
            excl[st]["no_outcome"] += 1
    skipped_dnp = 0
    for k in common:
        ca, cb, outcome = a[k], b[k], o[k]
        st = f"{k[0]}:{k[3]}"
        if ca["conditional_on_playing"] != cb["conditional_on_playing"]:
            raise GateError(f"conditionality mismatch for {k}: compare like with like")
        if iso_utc(ca["as_of"]) != iso_utc(cb["as_of"]) or iso_utc(ca["kickoff"]) != iso_utc(cb["kickoff"]):
            raise GateError(f"as-of horizon or kickoff mismatch for {k}")
        if not outcome["played"] and ca["conditional_on_playing"]:
            skipped_dnp += 1
            excl[st]["conditional_dnp"] += 1
            continue
        y = float(outcome["actual"])
        pa, pb = ca["projection"], cb["projection"]
        am, bm = float(pa["mean"]), float(pb["mean"])
        p: dict[str, Any] = {
            "cluster": f"{k[0]}|{k[1]}",
            "err_champ": am - y, "err_chal": bm - y,
            "abs_champ": abs(am - y), "abs_chal": abs(bm - y),
            "sq_champ": (am - y) ** 2, "sq_chal": (bm - y) ** 2,
            "cov_champ": int(pa["p10"] <= y <= pa["p90"]), "cov_chal": int(pb["p10"] <= y <= pb["p90"]),
        }
        pair = selected_threshold(ca, cb)
        if pair:
            ta, tb = pair
            hit = float(y >= float(ta["at_least"]))
            p["brier_champ"] = (ta["probability"] - hit) ** 2
            p["brier_chal"] = (tb["probability"] - hit) ** 2
        la, lb = quantile_levels(ca), quantile_levels(cb)
        levels = sorted(set(la) & set(lb))
        if len(levels) >= 3:
            p["crps_champ"] = crps_from_quantiles(la, levels, y)
            p["crps_chal"] = crps_from_quantiles(lb, levels, y)
            p["crps_levels"] = len(levels)
        for m, (x, z) in {"abs": ("abs_champ", "abs_chal"), "sq": ("sq_champ", "sq_chal"),
                          "err": ("err_champ", "err_chal"), "cov": ("cov_champ", "cov_chal"),
                          "brier": ("brier_champ", "brier_chal"), "crps": ("crps_champ", "crps_chal")}.items():
            p[f"d_{m}"] = (p[z] - p[x]) if x in p else None
        by_stat[st].append(p)
    return {
        "gate": GATE_VERSION,
        "metric_policy": ("matched unique player-game-statistic rows only; one representative common ladder "
                          "rung per player-game (average probability closest to 0.5, outcome-blind); CRPS "
                          "approximated by 2x mean pinball loss over common quantile levels; paired "
                          "percentile bootstrap resampling whole games (sport|game_id); deltas are "
                          "challenger minus champion"),
        "bootstrap": {"replicates": bootstrap, "seed": seed},
        "coverage": coverage,
        "conditional_dnp_excluded": skipped_dnp,
        "exclusions_by_statistic": {s: dict(sorted(c.items())) for s, c in sorted(excl.items())},
        "statistic_scorecards": {st: per_stat_summary(pairs, bootstrap, seed)
                                 for st, pairs in sorted(by_stat.items())},
        "note": ("Scores use sports outcomes and player-stat projections only. Market odds are never model "
                 "inputs. Promotion needs a preregistered, untouched chronological holdout."),
    }


# ---------------------------------------------------------------------------------------------
# rerun: runtime non-leakage proof
# ---------------------------------------------------------------------------------------------

def sha256_path(path: Path) -> str:
    """Content hash of a file, or of a directory tree (relative names + contents)."""
    h = hashlib.sha256()
    if path.is_dir():
        for p in sorted(x for x in path.rglob("*") if x.is_file()):
            h.update(p.relative_to(path).as_posix().encode() + b"\0")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    elif path.is_file():
        h.update(path.read_bytes())
    else:
        return "ABSENT"
    return h.hexdigest()


def semantic_digest(path: Path) -> str:
    """Hash of a market input's *values*: JSON/JSONL are canonicalized, so re-serializing an input
    whose values did not change does not count as an effective randomization."""
    def one(p: Path) -> bytes:
        suffix = p.suffix.lower()
        try:
            if suffix == ".json":
                return json.dumps(json.loads(p.read_text(encoding="utf-8")), sort_keys=True).encode()
            if suffix in (".jsonl", ".ndjson"):
                return json.dumps([json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()
                                   if x.strip()], sort_keys=True).encode()
        except (ValueError, UnicodeDecodeError):
            pass
        return p.read_bytes()
    h = hashlib.sha256()
    files = sorted(x for x in path.rglob("*") if x.is_file()) if path.is_dir() else [path]
    for f in files:
        if f.exists():
            h.update((f.relative_to(path).as_posix() if path.is_dir() else "").encode() + b"\0")
            h.update(hashlib.sha256(one(f)).digest())
    return h.hexdigest()


def _perturb_number(x: float | int, rng: random.Random) -> float | int:
    scale = abs(float(x)) + 1.0
    if isinstance(x, int) and not isinstance(x, bool):
        y = int(round(x * rng.uniform(-1.5, 2.5) + rng.gauss(0.0, scale)))
        return y if y != x else x + 1 + rng.randrange(5)
    y = round(float(x) * rng.uniform(-1.5, 2.5) + rng.gauss(0.0, scale), 6)
    return y if y != x else float(x) + 0.5 + rng.random()


def _randomize_json(obj: Any, rng: random.Random) -> Any:
    if isinstance(obj, bool):
        return rng.random() < 0.5
    if isinstance(obj, (int, float)):
        return _perturb_number(obj, rng)
    if isinstance(obj, dict):
        return {k: _randomize_json(v, rng) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_randomize_json(v, rng) for v in obj]
    return obj  # identifiers/strings kept so joins still resolve


_NUM_CELL = re.compile(r"^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$")


def _randomize_delimited(text: str, delim: str, rng: random.Random) -> str:
    lines = text.splitlines(keepends=True)
    out = lines[:1]
    for line in lines[1:]:
        end = "\n" if line.endswith("\n") else ""
        cells = line.rstrip("\r\n").split(delim)
        new = []
        for c in cells:
            if _NUM_CELL.match(c):
                v = float(c)
                new.append(str(_perturb_number(int(v) if v.is_integer() and "." not in c else v, rng)))
            else:
                new.append(c)
        out.append(delim.join(new) + end)
    return "".join(out)


def randomize_file(src: Path, dst: Path, rng: random.Random, randomizer_cmd: list[str] | None,
                   seed: int, cwd: Path, timeout: float) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if randomizer_cmd:
        argv = [t.format_map({"src": str(src), "dst": str(dst), "seed": str(seed)}) for t in randomizer_cmd]
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0 or not dst.exists():
            raise GateError(f"randomizer failed for {src}: {proc.stderr[-500:]}")
        return
    suffix = src.suffix.lower()
    if suffix == ".json":
        dst.write_text(json.dumps(_randomize_json(json.loads(src.read_text(encoding="utf-8")), rng),
                                  sort_keys=True) + "\n", encoding="utf-8")
    elif suffix in (".jsonl", ".ndjson"):
        lines = []
        for line in src.read_text(encoding="utf-8").splitlines():
            lines.append(json.dumps(_randomize_json(json.loads(line), rng), sort_keys=True) if line.strip() else line)
        dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif suffix in (".csv", ".tsv"):
        dst.write_text(_randomize_delimited(src.read_text(encoding="utf-8"), "\t" if suffix == ".tsv" else ",", rng),
                       encoding="utf-8")
    else:
        # Unknown binary format: same length, random bytes. A pipeline that reads it will then fail
        # or change, which the gate reports; supply randomizer_cmd for a format-preserving mutation.
        dst.write_bytes(bytes(rng.randrange(256) for _ in range(src.stat().st_size)))


def materialize(src: Path, dst: Path, arm: str, rng: random.Random, spec: dict, seed: int, cwd: Path,
                timeout: float) -> None:
    """Write the market input at ``dst`` for an arm: normal copy, deleted (absent) or randomized."""
    if dst.is_dir():
        shutil.rmtree(dst)
    elif dst.exists() or dst.is_symlink():
        dst.unlink()
    if arm == "deleted":
        return
    if arm == "normal":
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return
    rcmd = spec.get("randomizer_cmd")
    rcmd = shlex.split(rcmd) if isinstance(rcmd, str) else rcmd
    if src.is_dir():
        for f in sorted(x for x in src.rglob("*") if x.is_file()):
            randomize_file(f, dst / f.relative_to(src), rng, rcmd, seed, cwd, timeout)
    else:
        randomize_file(src, dst, rng, rcmd, seed, cwd, timeout)


def load_manifest(path: Path) -> dict:
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(m, dict):
        raise GateError("manifest must be a JSON object")
    base = path.parent.resolve()
    cwd = (base / m.get("cwd", ".")).resolve()
    mode = m.get("mode", "placeholder")
    if mode not in ("placeholder", "in_place"):
        raise GateError("manifest.mode must be 'placeholder' or 'in_place'")
    mi = m.get("market_inputs")
    if not isinstance(mi, dict) or not mi:
        raise GateError("manifest.market_inputs must be a nonempty object {name: path or {path, ...}}; "
                        "without real market inputs to mutate there is nothing to certify")
    markets = {}
    for name, spec in mi.items():
        if not re.match(r"^[A-Za-z0-9_]+$", name):
            raise GateError(f"market input name {name!r} must be alphanumeric/underscore")
        spec = {"path": spec} if isinstance(spec, str) else dict(spec)
        if not isinstance(spec.get("path"), str):
            raise GateError(f"market input {name!r} needs a path")
        p = (cwd / spec["path"]).resolve()
        if not p.exists():
            raise GateError(f"market input {name!r} does not exist: {p}")
        spec["path"] = p
        markets[name] = spec
    sports = {}
    for name, rel in (m.get("sports_inputs") or {}).items():
        p = (cwd / rel).resolve()
        if not p.exists():
            raise GateError(f"sports input {name!r} does not exist: {p}")
        sports[name] = p
    outputs = m.get("outputs")
    if not isinstance(outputs, list) or not outputs or not all(isinstance(x, str) and x for x in outputs):
        raise GateError("manifest.outputs must list model-owned output paths relative to {out}")
    for o in outputs:
        if os.path.isabs(o) or ".." in Path(o).parts:
            raise GateError(f"output {o!r} must be a relative path under {{out}}")
    compare_mode = m.get("compare", "bytes")
    if compare_mode not in ("bytes", "pure_jsonl"):
        raise GateError("manifest.compare must be 'bytes' or 'pure_jsonl'")
    return {"cwd": cwd, "mode": mode, "markets": markets, "sports": sports, "outputs": outputs,
            "compare": compare_mode, "env": {str(k): str(v) for k, v in (m.get("env") or {}).items()}}


def _check_template(argv: list[str], manifest: dict, role: str) -> None:
    joined = " ".join(argv)
    if "{out}" not in joined:
        raise GateError(f"{role} command must write its outputs under {{out}}")
    if manifest["mode"] == "placeholder" and "{market_dir}" not in joined:
        missing = [n for n in manifest["markets"] if f"{{market[{n}]}}" not in joined]
        if missing:
            raise GateError(f"{role} command never receives market input(s) {missing}: in placeholder mode "
                            "the command must reference {market[NAME]} or {market_dir}, exactly as the "
                            "production pipeline is wired; if the pipeline reads fixed paths use "
                            "mode='in_place'")


def _output_digest(out_dir: Path, outputs: list[str], compare_mode: str) -> tuple[dict[str, str], str | None]:
    digests: dict[str, str] = {}
    for rel in outputs:
        p = out_dir / rel
        if not p.is_file():
            return digests, f"missing output {rel}"
        if compare_mode == "bytes":
            data = p.read_bytes()
            if not data:
                return digests, f"empty output {rel}"
            digests[rel] = hashlib.sha256(data).hexdigest()
        else:
            try:
                idx = index_rows(load_jsonl(p))
            except GateError as exc:
                return digests, f"invalid PURE output {rel}: {exc}"
            if not idx:
                return digests, f"empty output {rel}"
            h = hashlib.sha256()
            for k in sorted(idx):
                h.update(model_signature(idx[k]).encode())
            digests[rel] = h.hexdigest()
    return digests, None


def _run_arm(role: str, arm: str, kind: str, argv_t: list[str], manifest: dict, work: Path, seed: int,
             mutation_seed: int, timeout: float) -> dict:
    """Run one arm. Paths seen by the command are identical across arms (no arm label leaks)."""
    run_dir = work / "run"
    out_dir = run_dir / "out"
    mdir = run_dir / "market"
    for d in (out_dir, mdir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    rng = random.Random(mutation_seed)
    market_paths: dict[str, str] = {}
    backups: dict[str, Path] = {}
    try:
        for name, spec in sorted(manifest["markets"].items()):
            src: Path = spec["path"]
            if manifest["mode"] == "placeholder":
                dst = mdir / name / src.name
                (mdir / name).mkdir(parents=True, exist_ok=True)
                materialize(src, dst, kind, rng, spec, mutation_seed, manifest["cwd"], timeout)
                market_paths[name] = str(dst)
            else:
                bak = work / "backup" / name / src.name  # written once per rerun(), before any arm
                backups[name] = bak
                if kind != "normal":
                    materialize(bak, src, kind, rng, spec, mutation_seed, manifest["cwd"], timeout)
                market_paths[name] = str(src)
        mapping = {"out": str(out_dir), "seed": str(seed), "market_dir": str(mdir),
                   "market": market_paths, "sports": {k: str(v) for k, v in manifest["sports"].items()}}
        try:
            argv = [t.format_map(mapping) for t in argv_t]
        except (KeyError, IndexError, ValueError) as exc:
            raise GateError(f"{role} command template has an unknown placeholder: {exc}") from exc
        env = dict(os.environ)
        env.update({"PYTHONHASHSEED": "0", "TZ": "UTC", "PURE_GATE_SEED": str(seed)})
        env.update(manifest["env"])
        t0 = time.monotonic()
        try:
            proc = subprocess.run(argv, cwd=manifest["cwd"], env=env, capture_output=True, text=True,
                                  timeout=timeout)
            rc, so, se = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            rc, so, se = -999, "", f"timeout after {timeout}s"
        except OSError as exc:
            rc, so, se = -998, "", f"could not execute: {exc}"
        dur = round(time.monotonic() - t0, 3)
        digests, problem = _output_digest(out_dir, manifest["outputs"], manifest["compare"]) if rc == 0 else ({}, None)
        keep = work / "results" / f"{role}-{arm}"
        if keep.exists():
            shutil.rmtree(keep)
        shutil.copytree(out_dir, keep)
    finally:
        if manifest["mode"] == "in_place":
            for name, bak in backups.items():
                materialize(bak, manifest["markets"][name]["path"], "normal", rng, {}, mutation_seed,
                            manifest["cwd"], timeout)
    return {"role": role, "arm": arm, "exit_code": rc, "seconds": dur, "outputs_sha256": digests,
            "output_problem": problem, "stdout_tail": so[-400:], "stderr_tail": se[-400:],
            "ok": rc == 0 and problem is None, "kept_outputs": str(keep)}


def _same(a: dict, b: dict) -> bool:
    return a["ok"] and b["ok"] and a["outputs_sha256"] == b["outputs_sha256"]


def rerun(model_cmd: str | list[str], control_cmd: str | list[str], manifest_path: str | Path, *,
          seed: int = 7, mutation_seed: int = 20261010, random_draws: int = 1,
          workdir: str | Path | None = None, timeout: float = 1800.0) -> dict:
    manifest = load_manifest(Path(manifest_path))
    model_t = shlex.split(model_cmd) if isinstance(model_cmd, str) else list(model_cmd)
    control_t = shlex.split(control_cmd) if isinstance(control_cmd, str) else list(control_cmd)
    if not model_t or not control_t:
        raise GateError("both a model command and a negative-control command are required")
    if model_t == control_t:
        raise GateError("negative control must be a different (deliberately market-reading) command")
    _check_template(model_t, manifest, "model")
    _check_template(control_t, manifest, "control")
    if random_draws < 1:
        raise GateError("random_draws must be >= 1")

    own_tmp = workdir is None
    work = Path(tempfile.mkdtemp(prefix="pure_gate_rerun_")) if own_tmp else Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    market_before = {n: sha256_path(s["path"]) for n, s in manifest["markets"].items()}
    sports_before = {n: sha256_path(p) for n, p in manifest["sports"].items()}

    if manifest["mode"] == "in_place":
        # Fresh backups of the real market files; every arm restores from these in a finally block.
        shutil.rmtree(work / "backup", ignore_errors=True)
        for name, spec in manifest["markets"].items():
            bak = work / "backup" / name / spec["path"].name
            bak.parent.mkdir(parents=True)
            (shutil.copytree if spec["path"].is_dir() else shutil.copy2)(spec["path"], bak)

    # Randomization must actually change every market input, otherwise the arm is vacuous.
    effective = {}
    for i in range(random_draws):
        ms = mutation_seed + i
        rng = random.Random(ms)
        for name, spec in sorted(manifest["markets"].items()):
            probe = work / "probe" / f"{i}" / name / spec["path"].name
            materialize(spec["path"], probe, "randomized", rng, spec, ms, manifest["cwd"], timeout)
            effective[f"{name}@{ms}"] = semantic_digest(probe) != semantic_digest(spec["path"])
    shutil.rmtree(work / "probe", ignore_errors=True)

    arms = ["normal", "normal_repeat", "deleted"] + [f"randomized_{i}" for i in range(random_draws)]
    runs: dict[str, dict[str, dict]] = {"model": {}, "control": {}}
    for role, tmpl in (("model", model_t), ("control", control_t)):
        for arm in arms:
            kind = "normal" if arm.startswith("normal") else ("deleted" if arm == "deleted" else "randomized")
            ms = mutation_seed + (int(arm.split("_")[1]) if kind == "randomized" else 0)
            runs[role][arm] = _run_arm(role, arm, kind, tmpl, manifest, work, seed, ms, timeout)

    market_after = {n: sha256_path(s["path"]) for n, s in manifest["markets"].items()}
    sports_after = {n: sha256_path(p) for n, p in manifest["sports"].items()}
    m, c = runs["model"], runs["control"]
    mutated = [a for a in arms if not a.startswith("normal")]
    checks = {
        "randomization_effective": all(effective.values()),
        "market_inputs_restored_unchanged": market_before == market_after,
        "sports_inputs_unchanged": sports_before == sports_after,
        "model_normal_ok": m["normal"]["ok"],
        "model_deterministic": _same(m["normal"], m["normal_repeat"]),
        "model_identical_under_all_mutations": all(_same(m["normal"], m[a]) for a in mutated),
        "control_normal_ok": c["normal"]["ok"],
        "control_deterministic": _same(c["normal"], c["normal_repeat"]),
        "control_responds_to_every_mutation": all(not _same(c["normal"], c[a]) for a in mutated),
    }
    per_arm = {
        "model_identical": {a: _same(m["normal"], m[a]) for a in mutated},
        "control_responded": {a: ("output_changed" if c[a]["ok"] else "failed_or_invalid")
                              if not _same(c["normal"], c[a]) else "NO_RESPONSE" for a in mutated},
    }
    if not (checks["randomization_effective"] and checks["market_inputs_restored_unchanged"]
            and checks["sports_inputs_unchanged"] and checks["model_normal_ok"] and checks["control_normal_ok"]):
        verdict = "ERROR_INVALID_EXPERIMENT"
    elif not (checks["model_deterministic"] and checks["control_deterministic"]):
        verdict = "FAIL_NONDETERMINISTIC"
    elif not all(per_arm["model_identical"].values()):
        verdict = "FAIL_MARKET_LEAK"
    elif not checks["control_responds_to_every_mutation"]:
        verdict = "FAIL_NO_POWER"
    else:
        verdict = "PASS_RUNTIME_NONLEAKAGE"
    report = {
        "gate": GATE_VERSION, "verdict": verdict, "passed": verdict == "PASS_RUNTIME_NONLEAKAGE",
        "certified": verdict == "PASS_RUNTIME_NONLEAKAGE",
        "mode": manifest["mode"], "compare": manifest["compare"], "seed": seed,
        "mutation_seed": mutation_seed, "random_draws": random_draws,
        "model_command": model_t, "control_command": control_t,
        "market_inputs": {n: {"path": str(s["path"]), "sha256": market_before[n]} for n, s in manifest["markets"].items()},
        "sports_inputs": {n: {"path": str(p), "sha256": sports_before[n]} for n, p in manifest["sports"].items()},
        "randomization_effective_by_input": effective,
        "checks": checks, "per_arm": per_arm, "runs": runs,
        "note": ("Each arm ran the actual command at identical seed, sports inputs and command-line paths. "
                 "Model-owned outputs must be bit-identical with market inputs deleted or randomized; the "
                 "market-reading negative control must change, proving the mutation reaches readers. This "
                 "cannot detect market information baked into the sports snapshot itself or into frozen "
                 "parameters; pair it with the source/dependency audit."),
    }
    if own_tmp and report["passed"]:
        shutil.rmtree(work, ignore_errors=True)
        report["workdir"] = None
    else:
        report["workdir"] = str(work)
    return report


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="Validate a JSONL PURE forecast export (v1, or kit interim v0)")
    v.add_argument("--forecasts", required=True)
    v.add_argument("--require-v1", action="store_true", help="refuse unversioned kit-interim (v0) rows")
    c = sub.add_parser("compare", help="Matched champion-vs-challenger scorecard against sports outcomes")
    c.add_argument("--champion", required=True)
    c.add_argument("--challenger", required=True)
    c.add_argument("--outcomes", required=True)
    c.add_argument("--bootstrap", type=int, default=1000)
    c.add_argument("--bootstrap-seed", type=int, default=20261010)
    r = sub.add_parser("rerun", help="Rerun an upstream model with market inputs normal/deleted/randomized")
    r.add_argument("--model-cmd", required=True, help="command template; placeholders {out} {seed} "
                   "{market[NAME]} {market_dir} {sports[NAME]}")
    r.add_argument("--control-cmd", required=True, help="deliberately market-reading negative control")
    r.add_argument("--manifest", required=True)
    r.add_argument("--seed", type=int, default=7, help="model seed, identical in every arm")
    r.add_argument("--mutation-seed", type=int, default=20261010)
    r.add_argument("--random-draws", type=int, default=1)
    r.add_argument("--workdir")
    r.add_argument("--timeout", type=float, default=1800.0)
    m = sub.add_parser("mutation", help="Kit-compatible diff of two pre-computed files (never certifies)")
    m.add_argument("--before", required=True)
    m.add_argument("--after", required=True)
    for s in (v, c, r, m):
        s.add_argument("--output", help="Write the JSON report to this path")
    ns = parser.parse_args(argv)
    try:
        if ns.command == "validate":
            result = validate_file_summary(load_jsonl(ns.forecasts), require_v1=ns.require_v1)
        elif ns.command == "compare":
            if ns.bootstrap < 0:
                raise GateError("bootstrap must be nonnegative")
            result = compare(load_jsonl(ns.champion), load_jsonl(ns.challenger), load_jsonl(ns.outcomes),
                             ns.bootstrap, ns.bootstrap_seed)
        elif ns.command == "rerun":
            result = rerun(ns.model_cmd, ns.control_cmd, ns.manifest, seed=ns.seed,
                           mutation_seed=ns.mutation_seed, random_draws=ns.random_draws,
                           workdir=ns.workdir, timeout=ns.timeout)
        else:
            result = mutate_compare(load_jsonl(ns.before), load_jsonl(ns.after))
    except (GateError, OSError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    output = json.dumps(result, sort_keys=True, indent=2, allow_nan=False)
    if ns.output:
        Path(ns.output).write_text(output + "\n", encoding="utf-8")
    print(output)
    if ns.command == "mutation":
        return 3 if result["outputs_identical"] else 1
    return 0 if result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
