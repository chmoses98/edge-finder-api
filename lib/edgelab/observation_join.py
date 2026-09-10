#!/usr/bin/env python3
"""
lib/edgelab/observation_join.py
==============================
WAVE 1, subwave B1. The deterministic, look-ahead-safe join between a decision
record and the order-book observation that was actually visible when it was made.

THE CANONICAL SOURCE
--------------------
`data/edgelab/observations/<date>.jsonl[.gz]`, and nothing else. It is chosen
over the Kalshi market registry because the registry is a SNAPSHOT that is
rebuilt in place on every run -- it answers "what is the book now", never "what
was the book when we decided". The observation archive is:

  * full-universe   -- 542,716 rows across 40 daily partitions
  * immutable       -- append-only dated partitions, never rewritten
  * identified      -- marketObservationId, marketTicker, eventTicker,
                       seriesTicker, gameId, mlbGameId
  * timestamped     -- capturedAt, plus isValidPregameObservation and
                       gameStartedAtCapture
  * priced          -- yesBid, yesAsk, spreadCents on 100% of rows

No second price database is created. This module only reads.

LOOK-AHEAD SAFETY IS THE WHOLE POINT
------------------------------------
A quote captured AFTER a decision cannot have informed that decision, and using
one would silently manufacture hindsight -- the single most dangerous error
available to a CLV study. Every candidate is filtered by
`capturedAt <= decisionTimestamp` BEFORE any selection happens, so the
look-ahead guard cannot be bypassed by a change to the ranking rule.

TIE-BREAKING IS DETERMINISTIC, NOT ARBITRARY
--------------------------------------------
Two observations may share a capturedAt for the same ticker. Rather than let
dict or file ordering decide, the tie is broken by marketObservationId, which is
stable and unique. That makes the join reproducible across runs and machines.
Where a tie exists it is recorded (`tieBroken: true`) so a reader can see that a
rule fired rather than discovering it later.

STALENESS IS REPORTED, NOT SILENTLY ACCEPTED
--------------------------------------------
A matched quote always carries `quoteAgeSeconds`. Beyond STALE_AFTER_SECONDS it
is additionally flagged `stale`. B1 does not refuse stale quotes -- refusing
would change which markets appear, which is a B2 decision -- it makes staleness
measurable so the B2 cutover can be argued from data.
"""

import gzip
import json
import os
import re
from datetime import datetime, timezone

from lib.edgelab import canonical_price as _cp

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
OBSERVATIONS_DIR = os.path.join(ROOT, "data", "edgelab", "observations")

# A quote older than this is flagged. Chosen as one hour because the production
# capture cadence is sub-hourly, so an older quote means a capture was missed
# rather than that the market was quiet.
STALE_AFTER_SECONDS = 3600

JOIN_EXACT = "TICKER_LATEST_AT_OR_BEFORE_DECISION"
JOIN_RESOLVED_VIA_GAMEPK = "TICKER_RESOLVED_FROM_GAMEPK_FAMILY_AND_TEAM"
JOIN_NONE_BEFORE = "NO_OBSERVATION_AT_OR_BEFORE_DECISION"
JOIN_NO_TICKER_HISTORY = "TICKER_NEVER_OBSERVED"
JOIN_NO_TICKER = "DECISION_CARRIES_NO_MARKET_TICKER"
JOIN_UNRESOLVABLE_FAMILY = "SYNTHETIC_TICKER_FAMILY_HAS_NO_KNOWN_SERIES"
JOIN_UNRESOLVABLE_SIDE = "SYNTHETIC_TICKER_SIDE_TEAM_UNKNOWN"
JOIN_AMBIGUOUS_TICKER = "SYNTHETIC_TICKER_RESOLVED_TO_MULTIPLE_CONTRACTS"
JOIN_BAD_TIMESTAMP = "DECISION_TIMESTAMP_UNPARSEABLE"


# ISO-8601 -> aware UTC datetime, or None. Defined in
# lib.edgelab.canonical_price so that the production pricing seam, which needs
# quote age to decide whether a price may gate money, does not have to import
# this archive scanner to get it. Re-exported here under its original name so
# every existing caller is unchanged and there is exactly ONE implementation.
parse_ts = _cp.parse_instant


# A decision record does not always carry a Kalshi ticker. The most important
# families -- ML and F5 ML -- are keyed by a SYNTHETIC "<gamePk>:<family>"
# identifier (e.g. "823251:ML_Away"), which appears nowhere in the order book,
# so an exact-ticker join can never reach them. Measured on 2026-09-09: ML and
# F5 matched 0 observations while every real-ticker family matched 8/8.
#
# The real contract is still recoverable WITHOUT guessing, because the record
# carries enough identity to name it exactly:
#   * gameId            -> the MLB gamePk, which observations also carry
#   * marketFamily      -> the side, encoded in the suffix (_Away / _Home)
#   * provenance.sourceKey -> "WSH@SD|ML_Away", naming away and home explicitly
# and each Kalshi ticker ends in the team abbreviation of the side it pays out.
#
# Resolution therefore requires EXACTLY ONE candidate; two candidates (a
# doubleheader, or an abbreviation that suffixes two tickers) is an ambiguity
# this refuses rather than resolves. That refusal is CR-3's territory and is
# left for W1-C.
SYNTHETIC_TICKER_RE = re.compile(r"^(?P<game_pk>\d+):(?P<family>.+)$")

# family -> (Kalshi series, is the contract identified by a TEAM suffix?)
#
# A moneyline event lists one contract per team, so the team abbreviation picks
# the contract out. A first-inning-run event lists exactly ONE contract -- 56 of
# 56 games measured, never two -- so the gamePk alone identifies it and there is
# no suffix to match.
#
# DELIBERATELY ABSENT, and the reason matters: TT_Away_Over, TT_Home_Over,
# Game_Total, RL_Away and RL_Home also arrive as synthetic keys, and their Kalshi
# series (KXMLBTEAMTOTAL, KXMLBTOTAL, KXMLBSPREAD) are archived and reachable --
# but each lists MANY contracts per event, one per strike (740 team-total and 516
# game-total contracts on 2026-09-09 alone), and the decision record carries
# `threshold: null` on every synthetic row of those families. Without a strike
# there is no single contract to name, so these refuse rather than pick one. That
# is a defect in what the decision record RECORDS, reported by B1, not a join
# this module could fix by guessing.
FAMILY_SERIES = {
    "ML_AWAY": ("KXMLBGAME", True), "ML_HOME": ("KXMLBGAME", True),
    "F5_ML_AWAY": ("KXMLBF5", True), "F5_ML_HOME": ("KXMLBF5", True),
    "NRFI": ("KXMLBRFI", False), "YRFI": ("KXMLBRFI", False),
}


def parse_source_key_teams(source_key):
    """'WSH@SD|ML_Away' -> ('WSH', 'SD'). None when it is not that shape."""
    if not source_key or "|" not in str(source_key):
        return None, None
    matchup = str(source_key).split("|", 1)[0]
    if "@" not in matchup:
        return None, None
    away, home = matchup.split("@", 1)
    away, home = away.strip().upper(), home.strip().upper()
    return (away or None), (home or None)


# Cache key for the (series, gamePk) -> tickers lookup, stashed on the index
# object itself. Building it costs one pass over the archive; NOT having it cost
# a full scan of every ticker's whole history per synthetic row, which is
# quadratic -- 2,740 synthetic rows against 22,000 tickers made the full-archive
# run take longer than the CI job it has to fit inside.
_LOOKUP_KEY = "_seriesGamePkTickers"


def _series_gamepk_lookup(observation_index):
    """{(seriesTicker, gamePk): sorted[tickers]}, built once per index."""
    cached = getattr(observation_index, _LOOKUP_KEY, None)
    if cached is not None:
        return cached
    lookup = {}
    for ticker, rows in observation_index.items():
        seen = set()
        for row in rows:
            key = (row.get("seriesTicker"), str(row.get("gameId")))
            if key in seen:
                continue
            seen.add(key)
            lookup.setdefault(key, set()).add(ticker)
    lookup = {key: sorted(tickers) for key, tickers in lookup.items()}
    try:
        setattr(observation_index, _LOOKUP_KEY, lookup)
    except AttributeError:
        # A PLAIN dict cannot carry an attribute, which is why load_observations
        # returns ObservationIndex. A caller that hands in a bare dict (the unit
        # tests do, with a handful of rows) still gets the right answer, just
        # rebuilt each time -- correctness never depends on the cache.
        pass
    return lookup


def resolve_market_ticker(record, observation_index):
    """
    Returns (ticker, method, refusal_reason) for one decision record.

    A record already carrying a real Kalshi ticker is used as-is. A synthetic
    "<gamePk>:<family>" key is resolved against the observation archive, and
    only when the answer is unique.
    """
    ticker = record.get("marketTicker")
    if ticker and not SYNTHETIC_TICKER_RE.match(str(ticker)):
        return ticker, JOIN_EXACT, None

    match = SYNTHETIC_TICKER_RE.match(str(ticker or ""))
    if not match:
        return None, JOIN_NO_TICKER, JOIN_NO_TICKER

    family = (record.get("marketFamily") or match.group("family") or "").upper()
    mapping = FAMILY_SERIES.get(family)
    if not mapping:
        return None, JOIN_UNRESOLVABLE_FAMILY, (
            "no Kalshi series is known for family %r" % family)
    series, identified_by_team = mapping

    suffix = None
    if identified_by_team:
        away, home = parse_source_key_teams(
            (record.get("provenance") or {}).get("sourceKey"))
        team = away if family.endswith("_AWAY") else (
            home if family.endswith("_HOME") else None)
        if not team:
            return None, JOIN_UNRESOLVABLE_SIDE, (
                "cannot name the team this side pays out on (family %r, sourceKey %r)"
                % (family, (record.get("provenance") or {}).get("sourceKey")))
        suffix = "-" + team

    game_pk = str(match.group("game_pk"))
    candidates = sorted(
        t for t in _series_gamepk_lookup(observation_index).get((series, game_pk), ())
        if suffix is None or str(t).upper().endswith(suffix)
    )
    if len(candidates) == 1:
        return candidates[0], JOIN_RESOLVED_VIA_GAMEPK, None
    described = "%s for gamePk %s%s" % (series, game_pk,
                                        (" ending in " + suffix) if suffix else "")
    if not candidates:
        return None, JOIN_NO_TICKER_HISTORY, "no observation of %s" % described
    return None, JOIN_AMBIGUOUS_TICKER, (
        "%d candidate tickers for %s (%s); refusing to choose"
        % (len(candidates), described, ", ".join(candidates)))


class ObservationIndex(dict):
    """
    {marketTicker: [observation, ...]}, and nothing more.

    A dict subclass purely so the (series, gamePk) lookup built by
    _series_gamepk_lookup can be cached on it. Every mapping operation behaves
    exactly as a dict's does, so no caller needs to know this type exists.
    """

    __slots__ = (_LOOKUP_KEY,)


def _read_partition(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


# Why a row can fail to enter the index. Named, counted and reported rather
# than silently `continue`d: an observation that vanishes without a reason is
# indistinguishable from one that was never captured, and that is exactly how a
# coverage table comes to describe less than it claims.
DROP_NO_TICKER = "DROPPED_NO_MARKET_TICKER"
DROP_BAD_TIMESTAMP = "DROPPED_UNPARSEABLE_CAPTURED_AT"
DROP_REASONS = (DROP_NO_TICKER, DROP_BAD_TIMESTAMP)


def observations_dir(root=None):
    return os.path.join(root or ROOT, "data", "edgelab", "observations")


def discover_observation_partitions(root=None):
    """
    Every partition date present under data/edgelab/observations, sorted.

    THE POINT OF THIS FUNCTION is that it asks the observation archive what it
    contains, rather than being told by some other directory. The full-universe
    coverage view previously took its dates from data/edgelab/model_evaluations,
    which silently scoped "the full archived market universe" to the dates on
    which the MODEL happened to produce a decision partition. The two
    directories have the same COUNT of partitions and different MEMBERS:
    observations carry 2026-08-01 and 2026-08-14, which model_evaluations does
    not, and model_evaluations carries 2026-07-30 and 2026-07-31, which
    observations do not. 28,364 archived observations were therefore outside a
    table that described itself as complete.
    """
    directory = observations_dir(root)
    if not os.path.isdir(directory):
        return []
    return sorted({
        name.split(".jsonl")[0] for name in os.listdir(directory)
        if name.endswith(".jsonl") or name.endswith(".jsonl.gz")
    })


def _partition_paths(dates=None, root=None):
    """[(date, path)] for the requested dates, or every partition when None."""
    directory = observations_dir(root)
    if not os.path.isdir(directory):
        return []
    wanted = set(dates) if dates is not None else None
    out = []
    for name in sorted(os.listdir(directory)):
        if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
            continue
        partition_date = name.split(".jsonl")[0]
        if wanted is not None and partition_date not in wanted:
            continue
        out.append((partition_date, os.path.join(directory, name)))
    return out


def admit_row(row):
    """
    (captured_at_datetime, drop_reason). Exactly one of the two is None.

    An observation that cannot be named or cannot be placed in time cannot take
    part in a look-ahead-safe join at all, so it is refused here rather than at
    selection time -- but it is refused with a REASON, so the count reconciles.
    """
    if not row.get("marketTicker"):
        return None, DROP_NO_TICKER
    captured = parse_ts(row.get("capturedAt"))
    if captured is None:
        return None, DROP_BAD_TIMESTAMP
    return captured, None


def iter_observations(dates=None, root=None, stats=None):
    """
    Stream (row, partition_date) for every ADMITTED observation, without
    building an index. Used by the full-universe view, which needs per-ticker
    aggregates rather than per-ticker history and must not pay to hold 550k
    rows in memory alongside the decision-side index.

    `stats`, when given, is filled in place with the reconciliation counters.
    """
    counters = stats if stats is not None else {}
    counters.setdefault("partitionsDiscovered", 0)
    counters.setdefault("partitionDates", [])
    counters.setdefault("rawRowsRead", 0)
    counters.setdefault("admittedRows", 0)
    counters.setdefault("droppedRows", {reason: 0 for reason in DROP_REASONS})
    counters.setdefault("rowsPerPartition", {})

    for partition_date, path in _partition_paths(dates=dates, root=root):
        counters["partitionsDiscovered"] += 1
        counters["partitionDates"].append(partition_date)
        raw_here = 0
        for row in _read_partition(path):
            counters["rawRowsRead"] += 1
            raw_here += 1
            captured, drop_reason = admit_row(row)
            if drop_reason is not None:
                counters["droppedRows"][drop_reason] += 1
                continue
            row["_capturedAtDt"] = captured
            counters["admittedRows"] += 1
            yield row, partition_date
        counters["rowsPerPartition"][partition_date] = raw_here


def load_observations(dates=None, root=None, stats=None):
    """
    Returns {marketTicker: [observation, ...]} sorted by (capturedAt,
    marketObservationId) ascending. `dates` limits which daily partitions are
    read; None reads every partition present.

    `stats`, when given, receives the same reconciliation counters
    iter_observations produces, so a caller can prove that every raw row either
    entered the index or was dropped for a named reason.
    """
    index = ObservationIndex()
    for row, _partition_date in iter_observations(dates=dates, root=root, stats=stats):
        index.setdefault(row["marketTicker"], []).append(row)

    for _ticker, rows in index.items():
        rows.sort(key=lambda r: (r["_capturedAtDt"],
                                 str(r.get("marketObservationId") or "")))
    return index


def join_observation(market_ticker, decision_timestamp, observation_index,
                     stale_after_seconds=STALE_AFTER_SECONDS):
    """
    Pure. Returns the join result for one (ticker, decision time) pair:

        {"matched": bool, "observation": dict|None, "joinMethod": str,
         "quoteAgeSeconds": float|None, "stale": bool, "tieBroken": bool,
         "candidatesAtOrBefore": int, "refusalReason": str|None}

    Never returns an observation captured after `decision_timestamp`.
    """
    result = {"matched": False, "observation": None, "joinMethod": None,
              "quoteAgeSeconds": None, "stale": False, "tieBroken": False,
              "candidatesAtOrBefore": 0, "refusalReason": None}

    if not market_ticker:
        result["joinMethod"] = JOIN_NO_TICKER
        result["refusalReason"] = JOIN_NO_TICKER
        return result

    decided_at = parse_ts(decision_timestamp)
    if decided_at is None:
        result["joinMethod"] = JOIN_BAD_TIMESTAMP
        result["refusalReason"] = JOIN_BAD_TIMESTAMP
        return result

    history = observation_index.get(market_ticker)
    if not history:
        result["joinMethod"] = JOIN_NO_TICKER_HISTORY
        result["refusalReason"] = JOIN_NO_TICKER_HISTORY
        return result

    # The look-ahead guard. Applied before any ranking so no future change to
    # selection can reach past it.
    eligible = [r for r in history if r["_capturedAtDt"] <= decided_at]
    result["candidatesAtOrBefore"] = len(eligible)
    if not eligible:
        result["joinMethod"] = JOIN_NONE_BEFORE
        result["refusalReason"] = JOIN_NONE_BEFORE
        return result

    chosen = eligible[-1]
    latest_ts = chosen["_capturedAtDt"]
    result["tieBroken"] = sum(1 for r in eligible if r["_capturedAtDt"] == latest_ts) > 1

    age = (decided_at - latest_ts).total_seconds()
    result.update({
        "matched": True,
        "observation": chosen,
        "joinMethod": JOIN_EXACT,
        "quoteAgeSeconds": round(age, 3),
        "stale": age > stale_after_seconds,
    })
    return result


def price_for_decision(market_ticker, side, decision_timestamp,
                       observation_index, stale_after_seconds=STALE_AFTER_SECONDS,
                       side_basis=None, side_evidence=None):
    """
    Convenience seam: join, then build the canonical Price from what was joined.
    Returns (price_dict, join_result). `price_dict` is None when nothing joined,
    so a caller can distinguish "no observation" from "observed but unexecutable".

    The observation archive stores quotes in CENTS -- the unit is declared here,
    at the one place that knows which archive the numbers came from, and never
    inferred downstream from their magnitude.
    """
    from lib.edgelab import canonical_price as cp

    join = join_observation(market_ticker, decision_timestamp, observation_index,
                            stale_after_seconds=stale_after_seconds)
    if not join["matched"]:
        return None, join

    obs = join["observation"]
    price = cp.build_price(
        side,
        yes_bid=obs.get("yesBid"), yes_ask=obs.get("yesAsk"),
        no_bid=obs.get("noBid"), no_ask=obs.get("noAsk"),
        unit=cp.UNIT_CENTS, grid=cp.GRID_UNKNOWN,
        market_ticker=obs.get("marketTicker"), event_ticker=obs.get("eventTicker"),
        observation_id=obs.get("marketObservationId"),
        captured_at=obs.get("capturedAt"), spread_cents=obs.get("spreadCents"),
        quote_age_seconds=join["quoteAgeSeconds"], join_method=join["joinMethod"],
        source="data/edgelab/observations",
        side_basis=side_basis, side_evidence=side_evidence,
    )
    return price, join


def price_for_record(record, observation_index,
                     stale_after_seconds=STALE_AFTER_SECONDS):
    """
    The whole decision-time chain for one record, in the one order that keeps
    each step honest:

        resolve the contract  ->  join the quote that was visible  ->
        prove which side is being bought  ->  price it

    Side resolution deliberately comes AFTER the join, because the proof needs
    the contract's own YES semantics, which only the observation carries.

    Returns (price, detail). `price` is None whenever any step refuses; `detail`
    always says which step did and why:

        {"ticker", "tickerMethod", "tickerRefusal", "join",
         "side", "sideBasis", "sideRefusal", "sideEvidence"}
    """
    from lib.edgelab import canonical_price as cp
    from lib.edgelab import decision_side as ds

    detail = {"ticker": None, "tickerMethod": None, "tickerRefusal": None,
              "join": None, "side": None, "sideBasis": None,
              "sideRefusal": None, "sideEvidence": None}

    ticker, method, ticker_refusal = resolve_market_ticker(record, observation_index)
    detail.update({"ticker": ticker, "tickerMethod": method,
                   "tickerRefusal": ticker_refusal})
    if ticker is None:
        return None, detail

    decided_at = record.get("createdAt") or record.get("capturedAt")
    join = join_observation(ticker, decided_at, observation_index,
                            stale_after_seconds=stale_after_seconds)
    detail["join"] = join
    if not join["matched"]:
        return None, detail

    side, basis, side_refusal, evidence = ds.resolve_side(
        record, join["observation"], ticker_method=method)
    detail.update({"side": side, "sideBasis": basis,
                   "sideRefusal": side_refusal, "sideEvidence": evidence})
    if side is None:
        # FAIL CLOSED. An unproven side is not a YES; there is no price.
        return None, detail

    obs = join["observation"]
    price = cp.build_price(
        side,
        yes_bid=obs.get("yesBid"), yes_ask=obs.get("yesAsk"),
        no_bid=obs.get("noBid"), no_ask=obs.get("noAsk"),
        unit=cp.UNIT_CENTS, grid=cp.GRID_UNKNOWN,
        market_ticker=obs.get("marketTicker"), event_ticker=obs.get("eventTicker"),
        observation_id=obs.get("marketObservationId"),
        captured_at=obs.get("capturedAt"), spread_cents=obs.get("spreadCents"),
        quote_age_seconds=join["quoteAgeSeconds"], join_method=join["joinMethod"],
        source="data/edgelab/observations",
        side_basis=basis, side_evidence=evidence,
    )
    return price, detail
