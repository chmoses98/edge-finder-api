#!/usr/bin/env python3
"""
scripts/discover_kalshi_series_catalogue.py
================================================
Spread/F3-F7-correction mission, Part 2 + Part 4 -- a genuinely
prefix-agnostic, live Kalshi discovery pass that does NOT depend on
already knowing a series ticker.

This sandbox environment has no network egress to
api.elections.kalshi.com (independently re-confirmed by the prior
Model Performance Phase 2A mission -- see
docs/research/INNING_RESULT_MIGRATION.md section 3), so this script's
real output can only be produced by a GitHub Actions run (the same
runner that already successfully produces data/kalshi_search.json and
every kalshi_registry_snapshot). Every network call below is wrapped
so a genuine failure (including "no egress") writes an honest error
artifact instead of crashing the calling workflow -- this script's
correctness in the sandbox is verified via unit tests that inject a
fake HTTP layer (tests/test_discover_kalshi_series_catalogue.py), not
via a real network call.

Three independent broad passes, none of which requires a pre-known
series ticker:
  1. GET /series (unfiltered) -- Kalshi's exchange series catalogue.
     Every series whose ticker or title suggests MLB association
     (startswith "KXMLB", or title contains "mlb"/"baseball", or an F3/
     F7 semantic marker) is retained as "MLB-associated", INCLUDING any
     series outside this repository's existing 8-series allowlist.
  2. For every MLB-associated series found in pass 1 (known + newly
     discovered), GET /markets?series_ticker=<ticker>&status=open and
     &status=closed (a modest page cap) -- covers "recently closed
     markets" the existing open-only broad pass in api/kalshisearch.js
     never queries.
  3. GET /markets?status=open&limit=1000 and
     GET /markets?status=closed&limit=1000, UNFILTERED by series,
     scanned for F3/F7 semantic text markers in title/subtitle/ticker
     across the last N days' date substrings -- catches an F3/F7
     market even if Kalshi's real series ticker has no relationship to
     "MLB" or "KX" at all.

Writes:
    data/kalshi/discovery/<date>_series_catalogue.json  (every series
        encountered with ticker/title/#events/#markets/MLB association
        evidence/classification+support status/inclusion-exclusion
        reason -- Part 4 "discovery completeness report")
    data/kalshi/discovery/<date>_f3_f7_search.json  (every F3/F7
        semantic match found across all three passes, or an explicit
        empty result with the exact queries attempted -- never "not
        available" without showing the work)
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
DISCOVERY_DIR = os.path.join(ROOT_DIR, "data", "kalshi", "discovery")

KNOWN_MLB_SERIES = {
    "KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL", "KXMLBTEAMTOTAL",
    "KXMLBF5", "KXMLBF5SPREAD", "KXMLBF5TOTAL", "KXMLBRFI",
}

F3_MARKERS = ("first 3", "first three", "3 innings", "f3")
F7_MARKERS = ("first 7", "first seven", "7 innings", "f7")

MONTHS = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']


def _kalshi_date(dt):
    return str(dt.year)[2:] + MONTHS[dt.month - 1] + str(dt.day).zfill(2)


def _http_get(url, timeout=20):
    """
    Returns (json_or_None, error_str_or_None). Never raises -- a
    network failure (including this sandbox's denied egress) is a
    recorded, honest result, not a crash.
    """
    try:
        req = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read()), None
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"


def _paged_get(url_base, key, max_pages=10, http_get=_http_get):
    """Follows Kalshi's cursor pagination. Returns (items, error_or_None)."""
    items = []
    cursor = ""
    for _ in range(max_pages):
        url = f"{url_base}&cursor={cursor}" if cursor else url_base
        data, err = http_get(url)
        if err:
            return items, err
        if data is None:
            return items, "empty_response"
        page_items = data.get(key) or []
        items.extend(page_items)
        cursor = data.get("cursor") or ""
        if not cursor or not page_items:
            break
    return items, None


def _has_marker(text, markers):
    t = (text or "").lower()
    return any(m in t for m in markers) or bool(re.search(r"\bf3\b" if markers is F3_MARKERS else r"\bf7\b", t))


def _mlb_association_evidence(ticker, title):
    ticker = ticker or ""
    title = (title or "").lower()
    if ticker.upper().startswith("KXMLB"):
        return "ticker_prefix_KXMLB"
    if "mlb" in title or "baseball" in title:
        return "title_contains_mlb_or_baseball"
    if _has_marker(title, F3_MARKERS) or _has_marker(title, F7_MARKERS):
        return "title_contains_f3_f7_horizon_language"
    return None


def discover_series_catalogue(http_get=_http_get):
    """
    Pass 1: GET /series (unfiltered). Returns (series_records, error).
    Each record: ticker, title, category, mlbAssociationEvidence,
    knownAllowlisted (bool), inclusionReason.
    """
    data, err = http_get(f"{KALSHI_BASE}/series?limit=1000")
    if err:
        return [], err
    all_series = (data or {}).get("series") or []
    records = []
    for s in all_series:
        ticker = s.get("ticker")
        title = s.get("title")
        evidence = _mlb_association_evidence(ticker, title)
        if evidence is None:
            continue
        records.append({
            "seriesTicker": ticker,
            "title": title,
            "category": s.get("category"),
            "mlbAssociationEvidence": evidence,
            "knownAllowlisted": ticker in KNOWN_MLB_SERIES,
            "inclusionReason": "mlb_associated_via_" + evidence,
        })
    return records, None


def discover_markets_for_series(series_ticker, http_get=_http_get):
    """
    Pass 2: every open AND recently-closed market for one series
    ticker. Returns (markets, errors_dict).
    """
    errors = {}
    open_markets, err = _paged_get(
        f"{KALSHI_BASE}/markets?series_ticker={series_ticker}&status=open&limit=200", "markets",
        http_get=http_get,
    )
    if err:
        errors["open"] = err
    closed_markets, err = _paged_get(
        f"{KALSHI_BASE}/markets?series_ticker={series_ticker}&status=closed&limit=200", "markets",
        max_pages=3, http_get=http_get,
    )
    if err:
        errors["closed"] = err
    return open_markets + closed_markets, errors


def broad_f3_f7_text_search(lookback_days=7, http_get=_http_get, now=None):
    """
    Pass 3: unfiltered /markets?status=<open|closed>&limit=1000, scanned
    for F3/F7 semantic markers across the last `lookback_days` days'
    date substrings (matching this repo's own YYMONDD ticker
    convention). Returns (matches, meta) where meta records exactly
    which queries were attempted and any errors -- so an empty
    `matches` list is never reported as "not available" without
    showing the work.
    """
    now = now or datetime.now(timezone.utc)
    date_codes = {_kalshi_date(now - timedelta(days=d)) for d in range(lookback_days)}
    matches = []
    meta = {"queriesAttempted": [], "errors": {}}

    for status in ("open", "closed"):
        url = f"{KALSHI_BASE}/markets?status={status}&limit=1000"
        meta["queriesAttempted"].append(url)
        mkts, err = _paged_get(url, "markets", max_pages=5, http_get=http_get)
        if err:
            meta["errors"][status] = err
            continue
        for m in mkts:
            et = m.get("event_ticker") or ""
            if not any(code in et for code in date_codes):
                continue
            title = m.get("title") or ""
            subtitle = m.get("subtitle") or ""
            ticker = m.get("ticker") or ""
            combined = f"{title} {subtitle} {ticker}"
            horizon = "F3" if (_has_marker(combined, F3_MARKERS)) else (
                "F7" if _has_marker(combined, F7_MARKERS) else None)
            if horizon:
                matches.append({
                    "horizon": horizon, "ticker": ticker, "eventTicker": et,
                    "title": title, "subtitle": subtitle, "status": status,
                    "seriesTicker": et.split("-", 1)[0] if et else None,
                })
    return matches, meta


def main(date_str=None, out_dir=None, http_get=_http_get):
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = out_dir or DISCOVERY_DIR

    series_records, series_err = discover_series_catalogue(http_get=http_get)

    per_series_markets = {}
    per_series_errors = {}
    for rec in series_records:
        mkts, errs = discover_markets_for_series(rec["seriesTicker"], http_get=http_get)
        per_series_markets[rec["seriesTicker"]] = mkts
        rec["eventCount"] = len({m.get("event_ticker") for m in mkts if m.get("event_ticker")})
        rec["marketCount"] = len(mkts)
        if errs:
            per_series_errors[rec["seriesTicker"]] = errs

    f3_f7_matches, search_meta = broad_f3_f7_text_search(http_get=http_get)

    catalogue = {
        "date": date_str,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seriesCatalogueQueryError": series_err,
        "mlbAssociatedSeries": series_records,
        "mlbAssociatedSeriesCount": len(series_records),
        "newSeriesOutsideAllowlistCount": sum(1 for r in series_records if not r["knownAllowlisted"]),
        "perSeriesQueryErrors": per_series_errors,
    }

    f3_f7_report = {
        "date": date_str,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "queriesAttempted": search_meta["queriesAttempted"],
        "errors": search_meta["errors"],
        "matches": f3_f7_matches,
        "matchCount": len(f3_f7_matches),
        "conclusion": (
            "NOT_FOUND_AFTER_EXHAUSTIVE_SEARCH_THIS_RUN" if not f3_f7_matches and not search_meta["errors"]
            else ("SEARCH_INCOMPLETE_SEE_ERRORS" if search_meta["errors"] and not f3_f7_matches
                  else "MATCHES_FOUND_SEE_matches_FIELD")
        ),
    }

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{date_str}_series_catalogue.json"), "w") as f:
        json.dump(catalogue, f, indent=2)
    with open(os.path.join(out_dir, f"{date_str}_f3_f7_search.json"), "w") as f:
        json.dump(f3_f7_report, f, indent=2)

    return {"catalogue": catalogue, "f3f7Search": f3_f7_report}


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = main(date_str=arg_date)
    print(json.dumps({
        "mlbAssociatedSeriesCount": result["catalogue"]["mlbAssociatedSeriesCount"],
        "newSeriesOutsideAllowlistCount": result["catalogue"]["newSeriesOutsideAllowlistCount"],
        "f3f7MatchCount": result["f3f7Search"]["matchCount"],
        "f3f7Conclusion": result["f3f7Search"]["conclusion"],
    }, indent=2))
