#!/usr/bin/env python3
"""
lib/kalshi_discovery.py
============================
Model Performance Phase 2A -- pure helper extracted from
scripts/build_kalshi_registry.py so it can be unit-tested without
triggering that script's unconditional top-level live Kalshi HTTP
calls (build_kalshi_registry.py has no `if __name__` guard; importing
it executes real network requests).

discover_unknown_series() is the pure, no-I/O half of the "broad,
prefix-agnostic discovery retention" fix: given the markets this
script already pulled per allowlisted series (all_by_series_map) and
the already-loaded contents of data/kalshi_search.json (search_json),
it returns every market whose series ticker is NOT in the known
SERIES_CATALOGUE allowlist, deduplicated by ticker. It does not fetch
anything itself, does not write anything, and does not change
SERIES_CATALOGUE or any production-facing registry shape -- it is
purely additive discovery-retention scaffolding.
"""


def discover_unknown_series(all_by_series_map, known_series, kalshi_date, search_json):
    """
    Pure. Returns a list of raw market dicts whose series ticker is not
    in `known_series`, deduplicated by ticker (`ticker` key for markets
    already fetched by this script, `market_ticker` for markets sourced
    from `search_json`'s `discoveredUnknownSeriesMarkets` field).

    Args:
        all_by_series_map: dict[series_ticker -> list[raw market dict]],
            exactly the shape scripts/build_kalshi_registry.py's own
            `all_by_series` variable already has.
        known_series: iterable of series tickers already in the
            allowlist (SERIES_CATALOGUE's keys) -- markets under these
            series are skipped since the main allowlisted loop already
            handles them.
        kalshi_date: the KALSHI_DATE string (e.g. "26JUL29") used to
            filter search_json's markets to today's date only.
        search_json: the already-loaded contents of
            data/kalshi_search.json (a dict), or {} if unavailable.
            Only its `discoveredUnknownSeriesMarkets` field (populated
            by api/kalshisearch.js's own broad-discovery addition) is
            consulted.
    """
    known = set(known_series)
    unknown = []
    seen_tickers = set()

    for series, mkts in (all_by_series_map or {}).items():
        if series in known:
            continue
        for m in mkts or []:
            t = m.get("ticker")
            if t and t not in seen_tickers:
                seen_tickers.add(t)
                unknown.append({**m, "_discoverySource": "build_kalshi_registry_direct_pull"})

    for m in (search_json or {}).get("discoveredUnknownSeriesMarkets", []) or []:
        et = m.get("event_ticker", "")
        if kalshi_date not in et:
            continue
        t = m.get("market_ticker")
        if t and t not in seen_tickers:
            seen_tickers.add(t)
            unknown.append({**m, "_discoverySource": "kalshisearch_broad_discovery"})

    return unknown
