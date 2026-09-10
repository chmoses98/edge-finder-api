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


def parse_ts(value):
    """ISO-8601 -> aware UTC datetime, or None. Tolerates 'Z' and offsets."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


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
    candidates = sorted({
        t for t, rows in observation_index.items()
        if (suffix is None or str(t).upper().endswith(suffix))
        and any(str(r.get("gameId")) == game_pk and r.get("seriesTicker") == series
                for r in rows)
    })
    if len(candidates) == 1:
        return candidates[0], JOIN_RESOLVED_VIA_GAMEPK, None
    described = "%s for gamePk %s%s" % (series, game_pk,
                                        (" ending in " + suffix) if suffix else "")
    if not candidates:
        return None, JOIN_NO_TICKER_HISTORY, "no observation of %s" % described
    return None, JOIN_AMBIGUOUS_TICKER, (
        "%d candidate tickers for %s (%s); refusing to choose"
        % (len(candidates), described, ", ".join(candidates)))


def _read_partition(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_observations(dates=None, root=None):
    """
    Returns {marketTicker: [observation, ...]} sorted by (capturedAt,
    marketObservationId) ascending. `dates` limits which daily partitions are
    read; None reads every partition present.

    Rows with no ticker or no parseable capturedAt are dropped here rather than
    at selection time -- an observation that cannot be placed in time cannot
    participate in a look-ahead-safe join at all.
    """
    directory = os.path.join(root or ROOT, "data", "edgelab", "observations")
    if not os.path.isdir(directory):
        return {}

    wanted = set(dates) if dates else None
    index = {}
    for name in sorted(os.listdir(directory)):
        if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
            continue
        partition_date = name.split(".jsonl")[0]
        if wanted is not None and partition_date not in wanted:
            continue
        for row in _read_partition(os.path.join(directory, name)):
            ticker = row.get("marketTicker")
            captured = parse_ts(row.get("capturedAt"))
            if not ticker or captured is None:
                continue
            row["_capturedAtDt"] = captured
            index.setdefault(ticker, []).append(row)

    for ticker, rows in index.items():
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
