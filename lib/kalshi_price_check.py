#!/usr/bin/env python3
"""
lib/kalshi_price_check.py
==============================
Pure, network-free core for the standalone Kalshi price-check tool
(scripts/check_kalshi_prices.py). Every function here is pure: no file
I/O, no network, no clock reads (callers supply retrievedAt/
snapshotTimestamp explicitly), deterministic given deterministic
inputs.

This module does NOT import scripts/build_market_ledger.py,
scripts/risk_gate.py, scripts/write_pending_bets.py,
scripts/protect_slate.py, scripts/validate_slate_final.py, or any
other execution/recommendation/settlement module -- it is a pricing
and discovery tool only, safety-isolated by construction (see
tests/test_check_kalshi_prices_safety_isolation.py).

Input shape: one raw market record from api/kalshisearch.js's output
(the SAME shape data/kalshi_registry_snapshots/kalshi_search_*.json
already stores, since those files are captured live responses from
that exact endpoint) --

    {event_ticker, market_ticker, title, subtitle, open_time,
     close_time, market_type, status, snapshot_ts, yes_bid, yes_ask,
     mid, implied_pct, american_odds, last_price, volume,
     open_interest}

Reused, not duplicated: lib.research.market_taxonomy.classify_market()
and classify_inning_result_market() (family/scope/outcome/structure/
settlementBasis/settlementStatus) -- this module never re-implements
classification logic.
"""
import csv
import io
import re

from lib.research.market_taxonomy import classify_market, classify_inning_result_market

# ── Terminal statuses (Part 11 no-silent-drop guarantee) ────────────────────
STATUS_INCLUDED = "Included"
STATUS_FILTERED_OUT = "Filtered Out"
STATUS_CLASSIFICATION_UNKNOWN = "Classification Unknown"
STATUS_MISSING_PRICE = "Missing Price"
STATUS_MALFORMED_RECORD = "Malformed Record"
STATUS_DUPLICATE_RECORD = "Duplicate Record"
STATUS_UNSUPPORTED_MARKET = "Unsupported Market"

# ── Ticker suffix parsing (adapted, independently tested copy of the
# scoring heuristic in scripts/build_kalshi_registry.py's parse_suffix() --
# NOT imported from it, since that script executes real network calls at
# import time and cannot be safely imported by this tool) ──────────────────
TWO_LETTER_ABBRS = {"TB", "AZ", "SF", "SD", "KC"}
_EVENT_SUFFIX_RE = re.compile(r"(\d{2}[A-Z]{3}\d{2})(\d{4})([A-Z]+?)(?:G\d)?$")


def parse_event_teams(event_ticker):
    """
    Pure. Extracts (away, home) team abbreviations from an event
    ticker's embedded {DATE}{HHMM}{AWAY}{HOME}[G#] suffix, e.g.
    'KXMLBF5-26JUL292210SEALAD' -> ('SEA', 'LAD'). Returns (None, None)
    if the ticker doesn't match the expected shape -- never guesses.
    """
    if not event_ticker:
        return None, None
    m = _EVENT_SUFFIX_RE.search(event_ticker)
    if not m:
        return None, None
    teams = m.group(3)
    candidates = []
    for a_len in (2, 3):
        if len(teams) <= a_len:
            continue
        away, home = teams[:a_len], teams[a_len:]
        if not (away.isalpha() and home.isalpha()):
            continue
        score = 1 if away in TWO_LETTER_ABBRS else 0
        candidates.append((score, a_len, away, home))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    _, _, away, home = candidates[0]
    return away, home


_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def parse_kalshi_event_date(event_ticker):
    """
    Pure. Extracts the {YY}{MON}{DD} date component embedded in an
    event ticker (e.g. 'KXMLBF5-26JUL292210SEALAD' -> '2026-07-29')
    and returns it as an ISO YYYY-MM-DD string, or None if the ticker
    doesn't match the expected shape. Assumes 20xx (Kalshi's MLB
    tickers do not span the year-2000 boundary).

    Bug fix: normalize_market() previously hardcoded the `date` field
    to None for every record, regardless of input -- meaning a
    `--date` filter could never match anything, and any future
    diagnostic segmenting by date would be silently blind. This
    function is the fix: real per-record dates, parsed from the same
    ticker structure parse_event_teams() already parses.
    """
    if not event_ticker:
        return None
    m = _EVENT_SUFFIX_RE.search(event_ticker)
    if not m:
        return None
    date_part = m.group(1)  # e.g. "26JUL29"
    try:
        yy, mon, dd = date_part[:2], date_part[2:5], date_part[5:7]
        month_num = _MONTHS.index(mon) + 1
        return f"20{yy}-{month_num:02d}-{int(dd):02d}"
    except (ValueError, IndexError):
        return None


def _cents(decimal_price):
    return round(decimal_price * 100) if decimal_price is not None else None


def normalize_market(raw_market, source_mode=None, source_used=None,
                      snapshot_timestamp=None, retrieved_at=None):
    """
    Pure. Normalizes one raw market record into the Part 6 output
    schema. Never raises for a malformed record -- returns
    (record_or_None, status, reason). status is one of:
    STATUS_INCLUDED (normalized successfully), STATUS_MALFORMED_RECORD
    (missing market_ticker -- nothing usable to key or display),
    STATUS_MISSING_PRICE (normalized, but no yes_bid/yes_ask at all --
    still included, never dropped), STATUS_CLASSIFICATION_UNKNOWN
    (normalized, family=unknown).
    """
    ticker = raw_market.get("market_ticker") or raw_market.get("ticker")
    if not ticker:
        return None, STATUS_MALFORMED_RECORD, "missing market_ticker"

    event_ticker = raw_market.get("event_ticker")
    title = raw_market.get("title")
    subtitle = raw_market.get("subtitle")

    away_team, home_team = parse_event_teams(event_ticker)
    event_date = parse_kalshi_event_date(event_ticker)

    classified = classify_market(ticker, event_ticker=event_ticker, title=title, subtitle=subtitle)
    inning_result = None
    if classified["family"] == "inning_result":
        inning_result = classify_inning_result_market(
            ticker, event_ticker=event_ticker, title=title, subtitle=subtitle,
            away_team=away_team, home_team=home_team,
        )

    yes_bid = raw_market.get("yes_bid")
    yes_ask = raw_market.get("yes_ask")
    no_bid = round(1.0 - yes_ask, 4) if yes_ask is not None else None
    no_ask = round(1.0 - yes_bid, 4) if yes_bid is not None else None
    midpoint = raw_market.get("mid")
    if midpoint is None and yes_bid is not None and yes_ask is not None:
        midpoint = round((yes_bid + yes_ask) / 2.0, 4)
    spread = round(yes_ask - yes_bid, 4) if (yes_bid is not None and yes_ask is not None) else None

    record = {
        "date": event_date,
        "scheduledStart": raw_market.get("open_time"),
        "matchup": f"{away_team}@{home_team}" if away_team and home_team else None,
        "awayTeam": away_team,
        "homeTeam": home_team,
        "family": classified["family"],
        "scope": (inning_result or classified)["scope"],
        "outcome": (inning_result["outcome"] if inning_result else classified["outcome"]),
        "participant": classified.get("team"),
        "line": None,
        "marketStructure": inning_result["structure"] if inning_result else None,
        "seriesTicker": classified["seriesTicker"],
        "eventTicker": event_ticker,
        "ticker": ticker,
        "title": title,
        "subtitle": subtitle,
        "status": raw_market.get("status"),
        "yesBid": yes_bid,
        "yesAsk": yes_ask,
        "noBid": no_bid,
        "noAsk": no_ask,
        "midpoint": midpoint,
        "lastPrice": raw_market.get("last_price"),
        "bidAskSpread": spread,
        "volume": raw_market.get("volume"),
        "openInterest": raw_market.get("open_interest"),
        "closeTime": raw_market.get("close_time"),
        "expirationTime": raw_market.get("close_time"),
        "settlementStatus": (inning_result["settlementStatus"] if inning_result
                              else classified.get("settlementBasis")),
        "rulesText": None,
        "classificationStatus": classified["classificationStatus"],
        "sourceMode": source_mode,
        "sourceUsed": source_used,
        "snapshotTimestamp": snapshot_timestamp or raw_market.get("snapshot_ts"),
        "retrievedAt": retrieved_at,
        "yesAskCents": _cents(yes_ask),
        "yesAskProbability": yes_ask,
        "yesBidCents": _cents(yes_bid),
        "noAskCents": _cents(no_ask),
        "noBidCents": _cents(no_bid),
    }

    if classified["family"] == "unknown":
        return record, STATUS_CLASSIFICATION_UNKNOWN, "unrecognized series/title"
    if yes_bid is None and yes_ask is None:
        return record, STATUS_MISSING_PRICE, "no yes_bid/yes_ask present"
    return record, STATUS_INCLUDED, None


def normalize_batch(raw_markets, source_mode=None, source_used=None,
                     snapshot_timestamp=None, retrieved_at=None):
    """
    Pure. Normalizes every raw market, returning
    (records, status_counts, malformed_reasons) where status_counts
    maps each terminal status to a count and malformed_reasons is a
    list of (ticker_or_None, reason) for STATUS_MALFORMED_RECORD
    entries. Guarantees len(raw_markets) == sum(status_counts.values())
    -- the Part 11 no-silent-drop reconciliation.
    """
    records = []
    status_counts = {}
    malformed_reasons = []
    seen_tickers = set()

    for raw in raw_markets:
        record, status, reason = normalize_market(
            raw, source_mode=source_mode, source_used=source_used,
            snapshot_timestamp=snapshot_timestamp, retrieved_at=retrieved_at,
        )
        if status == STATUS_MALFORMED_RECORD:
            malformed_reasons.append((None, reason))
            status_counts[status] = status_counts.get(status, 0) + 1
            continue

        ticker = record["ticker"]
        if ticker in seen_tickers:
            status = STATUS_DUPLICATE_RECORD
        else:
            seen_tickers.add(ticker)
        record["_recordStatus"] = status
        records.append(record)
        status_counts[status] = status_counts.get(status, 0) + 1

    return records, status_counts, malformed_reasons


# ── Filtering (Part 5/11) ────────────────────────────────────────────────────
def _ci_contains(haystack, needle):
    if not needle:
        return True
    if not haystack:
        return False
    return needle.lower() in str(haystack).lower()


def _ci_eq(a, b):
    if b is None:
        return True
    if a is None:
        return False
    return str(a).lower() == str(b).lower()


# Sequential filter-stage order -- also the order diagnostics are
# reported in, so "exactly which filter reduced the result count to
# zero" always names the FIRST stage whose output first hit zero, not
# some later stage that had nothing left to remove.
FILTER_STAGE_ORDER = [
    "date", "game", "team", "away_team", "home_team", "family", "scope",
    "outcome", "participant", "pitcher", "hitter", "event_ticker",
    "series_ticker", "status", "closed_exclusion", "unknown_exclusion",
    "max_results",
]


def apply_filters(records, filters):
    """
    Pure. Applies user-specified filters (all optional, all combinable,
    case-insensitive) to already-normalized records, as a SEQUENTIAL
    pipeline of named stages. Filtering happens AFTER normalization/
    retention (Part 11: "Filtering must occur after raw retention and
    normalization where possible").

    Returns (kept, stage_report) where stage_report is:
        {
            "removedByStage": {stage_name: count_removed_at_that_stage, ...},
            "remainingAfterStage": [(stage_name, remaining_count), ...],
        }

    This directly answers "exactly which filter reduced the result
    count to zero" -- see find_zero_stage(). A record removed by an
    earlier stage is never double-counted by a later one (each stage
    only ever sees what the previous stage kept).

    Recognized filters keys (all optional): date, game (matchup
    substring), team (away OR home), away_team, home_team, family,
    scope, outcome, participant, pitcher, hitter, ticker (exact,
    case-insensitive, takes priority -- see below), event_ticker,
    series_ticker, status, include_closed (bool), include_unknown
    (bool), max_results (int).

    Exact ticker lookup takes priority over every other filter: if
    `ticker` is supplied, only that exact ticker (case-insensitive) is
    matched, and no other filter is consulted.
    """
    filters = filters or {}
    ticker_filter = filters.get("ticker")
    if ticker_filter:
        kept = [r for r in records if _ci_eq(r["ticker"], ticker_filter)]
        removed = len(records) - len(kept)
        return kept, {
            "removedByStage": {"ticker_exact": removed},
            "remainingAfterStage": [("ticker_exact", len(kept))],
        }

    current = list(records)
    removed_by_stage = {}
    remaining_after_stage = [("initial", len(current))]

    def _run_stage(name, predicate):
        nonlocal current
        keep = [r for r in current if predicate(r)]
        removed_by_stage[name] = len(current) - len(keep)
        current = keep
        remaining_after_stage.append((name, len(current)))

    _run_stage("date", lambda r: filters.get("date") is None or _ci_eq(r.get("date"), filters["date"]))
    _run_stage("game", lambda r: _ci_contains(r.get("matchup"), filters.get("game")))

    team = filters.get("team")
    _run_stage("team", lambda r: not team or (_ci_contains(r.get("awayTeam"), team) or _ci_contains(r.get("homeTeam"), team)))
    _run_stage("away_team", lambda r: filters.get("away_team") is None or _ci_eq(r.get("awayTeam"), filters["away_team"]))
    _run_stage("home_team", lambda r: filters.get("home_team") is None or _ci_eq(r.get("homeTeam"), filters["home_team"]))
    _run_stage("family", lambda r: filters.get("family") is None or _ci_eq(r.get("family"), filters["family"]))
    _run_stage("scope", lambda r: filters.get("scope") is None or _ci_eq(r.get("scope"), filters["scope"]))
    _run_stage("outcome", lambda r: filters.get("outcome") is None or _ci_eq(r.get("outcome"), filters["outcome"]))

    participant = filters.get("participant")
    _run_stage("participant", lambda r: not participant or (
        _ci_contains(r.get("participant"), participant)
        or _ci_contains(r.get("title"), participant)
        or _ci_contains(r.get("subtitle"), participant)
    ))
    pitcher = filters.get("pitcher")
    _run_stage("pitcher", lambda r: not pitcher or (_ci_contains(r.get("title"), pitcher) or _ci_contains(r.get("subtitle"), pitcher)))
    hitter = filters.get("hitter")
    _run_stage("hitter", lambda r: not hitter or (_ci_contains(r.get("title"), hitter) or _ci_contains(r.get("subtitle"), hitter)))

    _run_stage("event_ticker", lambda r: filters.get("event_ticker") is None or _ci_eq(r.get("eventTicker"), filters["event_ticker"]))
    _run_stage("series_ticker", lambda r: filters.get("series_ticker") is None or _ci_eq(r.get("seriesTicker"), filters["series_ticker"]))
    _run_stage("status", lambda r: filters.get("status") is None or _ci_eq(r.get("status"), filters["status"]))
    _run_stage("closed_exclusion", lambda r: filters.get("include_closed", False) or not _ci_eq(r.get("status"), "closed"))
    _run_stage("unknown_exclusion", lambda r: filters.get("include_unknown", True) or r.get("family") != "unknown")

    max_results = filters.get("max_results")
    if max_results is not None and len(current) > max_results:
        removed_by_stage["max_results"] = len(current) - max_results
        current = current[:max_results]
    else:
        removed_by_stage["max_results"] = 0
    remaining_after_stage.append(("max_results", len(current)))

    return current, {
        "removedByStage": removed_by_stage,
        "remainingAfterStage": remaining_after_stage,
    }


def find_zero_stage(remaining_after_stage):
    """
    Pure. Given apply_filters()'s remaining_after_stage list, returns
    the name of the FIRST stage after which the remaining count hit
    zero, or None if the count never reached zero. Skips the synthetic
    "initial" entry (that's the pre-filter count, not a stage).
    """
    for name, remaining in remaining_after_stage:
        if name == "initial":
            continue
        if remaining == 0:
            return name
    return None


def format_job_summary_markdown(metadata):
    """
    Pure. Renders the full diagnostic metadata dict (produced by
    scripts/check_kalshi_prices.py's run()) as Markdown suitable for a
    GitHub Actions job summary. Factored out as a pure, independently
    testable function specifically so the workflow never needs a
    fragile inline heredoc to format it (bug: an earlier version of
    this workflow step used a Python heredoc nested inside a bash `if`
    block inside a `{...} >> $GITHUB_STEP_SUMMARY` redirect, which is
    fragile to YAML/bash indentation rules -- this function replaces
    that with a plain, testable script invocation).
    """
    lines = [f"**Source used:** {metadata.get('sourceUsed')}"]
    fetch_info = metadata.get("fetchInfo") or {}
    if fetch_info:
        lines.append("")
        lines.append(f"**Endpoint:** {fetch_info.get('endpoint')}")
        lines.append(f"**HTTP status:** {fetch_info.get('httpStatus')}")
        lines.append(f"**Response size (bytes):** {fetch_info.get('responseSizeBytes')}")
        lines.append(f"**`markets` key present in response:** {fetch_info.get('marketsKeyPresent')}")
    if metadata.get("fallbackReason"):
        lines.append("")
        lines.append(f"**Fallback reason:** {metadata['fallbackReason']}")
    lines.append("")
    lines.append(f"**Raw records fetched:** {metadata.get('rawRecordsFetched')}")
    lines.append(f"**Normalized:** {metadata.get('normalizedRecordCount')}")
    lines.append(f"**Classified:** {metadata.get('classifiedCount')}")
    lines.append(f"**Unknown:** {metadata.get('unknownCount')}")
    lines.append(f"**Malformed:** {metadata.get('malformedRecordCount')}")
    lines.append("")
    for stage, count in (metadata.get("removedByFilterStage") or {}).items():
        if count:
            lines.append(f"**Filtered by {stage}:** {count}")
    lines.append("")
    lines.append(f"**Returned:** {metadata.get('resultCount')}")
    lines.append("")
    lines.append(f"**Reason:** {metadata.get('diagnosis')}")
    return "\n".join(lines)


def diagnose_result(raw_count, normalized_count, stage_report, final_count):
    """
    Pure. Always returns a human-readable explanation of the pipeline
    outcome -- populated whether the result is zero or not, so a
    surprisingly small (but nonzero) result is equally explainable.
    Checked in this order (each one a genuine, distinct root cause):
      1. Live/snapshot source returned zero raw records at all.
      2. Every raw record failed normalization (malformed).
      3. A specific filter stage removed the last remaining record(s).
      4. Otherwise, a plain "N record(s) matched" statement.
    """
    if raw_count == 0:
        return "Live endpoint (or snapshot) returned zero raw records."
    if normalized_count == 0:
        return "All raw records failed normalization (no usable market_ticker on any record)."
    if final_count == 0:
        stage = find_zero_stage(stage_report["remainingAfterStage"])
        if stage:
            return f"All records removed by the {stage!r} filter stage."
        return "No records matched the combination of filters applied."
    return f"{final_count} record(s) matched."


# ── Three-way grouping (Part 8) ──────────────────────────────────────────────
def group_inning_result_threeway(records):
    """
    Pure. Groups inning-result records by (eventTicker, scope) into
    Away/Tie/Home. Returns a list of group dicts:

        {
            "eventTicker": ..., "scope": ..., "matchup": ...,
            "structure": ...,   # from any member row, "UNVERIFIED" if any disagree
            "away": record_or_None, "tie": record_or_None, "home": record_or_None,
            "missingLegs": [...],   # e.g. ["Tie"] if that leg wasn't discovered
            "sumYesAsk": float_or_None, "sumMidpoint": float_or_None,
        }

    Never synthesizes a missing leg -- a missing outcome is reported in
    missingLegs, not fabricated. For F3/F7 (STRUCTURE_UNVERIFIED), all
    discovered raw legs are still grouped and shown, but the group is
    labeled unverified and missingLegs is not treated as a real gap the
    same way it is for F5.
    """
    groups = {}
    for r in records:
        if r.get("family") != "inning_result":
            continue
        key = (r.get("eventTicker"), r.get("scope"))
        g = groups.setdefault(key, {
            "eventTicker": r.get("eventTicker"), "scope": r.get("scope"),
            "matchup": r.get("matchup"), "structure": r.get("marketStructure"),
            "away": None, "tie": None, "home": None,
        })
        outcome = r.get("outcome")
        if outcome == "Away":
            g["away"] = r
        elif outcome == "Home":
            g["home"] = r
        elif outcome == "Tie":
            g["tie"] = r

    result = []
    for g in groups.values():
        missing = []
        if g["structure"] == "THREE_WAY":
            for leg_name, leg in (("Away", g["away"]), ("Tie", g["tie"]), ("Home", g["home"])):
                if leg is None:
                    missing.append(leg_name)
        yes_asks = [g[k]["yesAsk"] for k in ("away", "tie", "home") if g[k] and g[k]["yesAsk"] is not None]
        mids = [g[k]["midpoint"] for k in ("away", "tie", "home") if g[k] and g[k]["midpoint"] is not None]
        g["missingLegs"] = missing
        g["sumYesAsk"] = round(sum(yes_asks), 4) if yes_asks else None
        g["sumMidpoint"] = round(sum(mids), 4) if mids else None
        result.append(g)
    return result


# ── Human-readable table formatting (Part 9) ─────────────────────────────────
_TABLE_COLUMNS = ["Matchup", "Market", "Scope", "Outcome", "Line", "YES Bid", "YES Ask",
                  "NO Bid", "NO Ask", "Spread", "Volume", "Status", "Ticker", "Price Time"]


def _fmt_cents(v):
    return f"{round(v * 100)}¢" if v is not None else "n/a"


def format_table(records):
    """Pure. Renders records as a simple, GitHub-Actions-summary-safe
    fixed-column text table (no external table library dependency)."""
    lines = [" | ".join(_TABLE_COLUMNS)]
    for r in records:
        lines.append(" | ".join([
            r.get("matchup") or "n/a",
            r.get("family") or "unknown",
            r.get("scope") or "n/a",
            r.get("outcome") or "n/a",
            str(r.get("line")) if r.get("line") is not None else "n/a",
            _fmt_cents(r.get("yesBid")),
            _fmt_cents(r.get("yesAsk")),
            _fmt_cents(r.get("noBid")),
            _fmt_cents(r.get("noAsk")),
            _fmt_cents(r.get("bidAskSpread")),
            str(r.get("volume")) if r.get("volume") is not None else "n/a",
            r.get("status") or "n/a",
            r.get("ticker") or "n/a",
            r.get("snapshotTimestamp") or r.get("retrievedAt") or "n/a",
        ]))
    return "\n".join(lines)


def format_threeway_groups(groups):
    """
    Pure. Renders inning-result Away/Tie/Home groups (from
    group_inning_result_threeway()) in the Part 8 human-readable
    format, with explicit missing-leg warnings and structure-
    verification status. Never assumes a three-way structure for a
    group whose structure is not THREE_WAY -- unresolved groups show
    every raw discovered leg and an explicit "structure unresolved"
    label, with no synthesized missing outcome.
    """
    if not groups:
        return ""
    lines = []
    for g in groups:
        lines.append(f"{g['matchup']} — {g['scope']} result")
        for leg_name, key in (("Away", "away"), ("Tie", "tie"), ("Home", "home")):
            leg = g.get(key)
            if leg is None:
                lines.append(f"  {leg_name}: NOT DISCOVERED")
                continue
            lines.append(
                f"  {leg_name}: ticker={leg['ticker']} "
                f"YES bid={_fmt_cents(leg['yesBid'])} YES ask={_fmt_cents(leg['yesAsk'])} "
                f"NO bid={_fmt_cents(leg['noBid'])} NO ask={_fmt_cents(leg['noAsk'])}"
            )
        if g["structure"] == "THREE_WAY":
            lines.append(f"  Sum of YES asks: {_fmt_cents(g['sumYesAsk'])}")
            lines.append(f"  Sum of midpoints: {_fmt_cents(g['sumMidpoint'])}")
            if g["missingLegs"]:
                lines.append(f"  WARNING: missing leg(s): {', '.join(g['missingLegs'])}")
            lines.append("  Structure: VERIFIED three-way")
        else:
            lines.append(f"  Structure: UNRESOLVED (scope={g['scope']!r}) — not assumed to be three-way")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_csv(records):
    """Pure. Renders records as CSV text using the full normalized schema."""
    if not records:
        fieldnames = ["ticker"]
    else:
        fieldnames = [k for k in records[0].keys() if not k.startswith("_")]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in records:
        writer.writerow(r)
    return buf.getvalue()
