"""Strict publication of the MLB projection feed (sift.projections.v1).

The feed is read by browsers with JSON.parse, which rejects the NaN / Infinity tokens Python's json module writes by default. A
non-finite number is therefore never serialised: it is either an explicitly optional descriptor that is genuinely missing (written
as null), or an invalid value in a required field (publication fails, the last good feed stays in place and ages out visibly).
"""
from __future__ import annotations

import json
import math
from typing import Any

# Descriptors that may be genuinely missing: a hitter whose opposing starter is not yet announced has no starter factor. They are
# informational (the projection's mean, range and probabilities never read them back), so null is honest; anything else is not.
OPTIONAL_NULLABLE = {("players", "[]", "opponent_adjustment", "factor"), ("players", "[]", "opponent_adjustment", "sample_games")}
GATE_STATUSES = {"PASS", "REVIEW", "BLOCK"}
PLAYER_NUMBERS = ("mean", "median", "p10", "p90")
PLAYER_TEXT = ("player_id", "game_id", "family", "readiness")


class FeedError(ValueError):
    """The feed must not be published; the message names every problem found (first few)."""


def finite_round(x: Any, nd: int = 3) -> float | None:
    """round(float(x), nd), or None when x is missing or not finite (never NaN / Infinity)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if math.isfinite(v) else None


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _walk(o: Any, path: tuple, bad: list, fix: list) -> Any:
    """Copy of o with optional non-finite numbers replaced by None; collects required non-finite paths in `bad`."""
    if isinstance(o, dict):
        return {k: _walk(v, path + (k,), bad, fix) for k, v in o.items()}
    if isinstance(o, list):
        return [_walk(v, path + ("[]",), bad, fix) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        if path in OPTIONAL_NULLABLE:
            fix.append(".".join(path))
            return None
        bad.append(".".join(path) + f" = {o}")
    return o


def validate(doc: Any) -> dict:
    """The feed as it may be published: optional non-finite descriptors nulled, everything else checked. Raises FeedError."""
    if not isinstance(doc, dict):
        raise FeedError("the feed is not a JSON object")
    problems: list[str] = []
    nonfinite: list[str] = []
    nulled: list[str] = []
    out = _walk(doc, (), nonfinite, nulled)
    problems += [f"non-finite value in a required field: {p}" for p in nonfinite]
    if out.get("schema") != "sift.projections.v1":
        problems.append("schema is not sift.projections.v1")
    if out.get("sport") != "MLB":
        problems.append("sport is not MLB")
    if not isinstance(out.get("generated_utc"), str) or not out.get("generated_utc"):
        problems.append("generated_utc is missing")
    if not isinstance(out.get("players"), list):
        problems.append("players is missing or not a list")
    if not isinstance(out.get("games"), list):
        problems.append("games is missing or not a list")
    if not isinstance(out.get("families"), dict):
        problems.append("families is missing or not an object")
    for i, p in enumerate(out.get("players") if isinstance(out.get("players"), list) else []):
        who = f"players[{i}] ({p.get('name') if isinstance(p, dict) else '?'})"
        if not isinstance(p, dict):
            problems.append(f"{who} is not an object")
            continue
        for k in PLAYER_TEXT:
            if not isinstance(p.get(k), str) or not p.get(k):
                problems.append(f"{who}: {k} is missing")
        for k in PLAYER_NUMBERS:
            if not _is_num(p.get(k)):
                problems.append(f"{who}: {k} is missing or not a number")
        th = p.get("thresholds")
        if not isinstance(th, dict) or not all(_is_num(v) and 0 <= v <= 1 for v in th.values()):
            problems.append(f"{who}: thresholds are missing or not probabilities")
        g = p.get("gate")
        if not isinstance(g, dict) or g.get("status") not in GATE_STATUSES:
            problems.append(f"{who}: gate status is missing or unknown")
    if problems:
        shown = "; ".join(problems[:8]) + (f"; and {len(problems) - 8} more" if len(problems) > 8 else "")
        raise FeedError(f"the MLB projection feed is invalid and was not published: {shown}")
    out["_nulled_optional"] = len(nulled)       # stripped again by dumps(); reported by the publish stage
    return out


def dumps(doc: dict, **kw) -> str:
    """Serialise a validated feed; allow_nan=False makes a stray NaN / Infinity an error instead of a corrupt file."""
    doc = {k: v for k, v in doc.items() if k != "_nulled_optional"}
    return json.dumps(doc, allow_nan=False, **kw)


def nullify_nonfinite(o: Any) -> tuple[Any, int]:
    """(copy of o with every NaN / Infinity replaced by None, how many were replaced).

    For the prospective scorecards only: every number there is a descriptive statistic, and a non-finite one is undefined rather than
    invalid (e.g. the z of a paired difference over one cluster has no standard error). Never use this on projection values.
    """
    n = 0

    def walk(v):
        nonlocal n
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        if isinstance(v, float) and not math.isfinite(v):
            n += 1
            return None
        return v
    return walk(o), n
