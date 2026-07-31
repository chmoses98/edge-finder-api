#!/usr/bin/env python3
"""
scripts/discover_kalshi_mlb_markets.py
=========================================
Universal Kalshi MLB market discovery — the entry point of the market
engine described in docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md.

Unlike scripts/build_kalshi_registry.py (which only ever queries a fixed
allowlist of 8 known series tickers), this script's PRIMARY input is
data/kalshi_search.json's `markets` list — the output of
api/kalshisearch.js's broad, unfiltered `/markets?status=open&limit=1000`
pass, which is the one existing fetch in this repository that is NOT
restricted to a pre-known series prefix (see
docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md section 1). Reusing this
already-fetched file means discovery adds no new live Kalshi API calls
of its own in the common case — it classifies whatever the existing
pipeline already pulled, and preserves every market this repository has
never seen before instead of silently ignoring it.

Every discovered contract is parsed (lib.kalshi_mlb_contract_parser),
classified (lib.kalshi_mlb_market_classifier), matched to its slate game
for a real gameId and projection context, and priced
(lib.kalshi_probability_adapters) — an UNSUPPORTED or MISSING_DATA
contract is still written to the discovery output, never dropped.

Writes:
    data/kalshi/discovery/<date>.json          (every contract, full detail)
    data/kalshi/discovery/<date>_summary.json  (discovered/classified/
                                                 modeled/exposed/unsupported/
                                                 parse-failure counts)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, ROOT_DIR)

from lib.kalshi_mlb_contract_parser import parse_contract  # noqa: E402
from lib.kalshi_mlb_market_classifier import classify_contract, SUBJECT_TEAM  # noqa: E402
from lib.kalshi_probability_adapters import (  # noqa: E402
    adapt_contract, STATUS_SUPPORTED, STATUS_UNSUPPORTED, STATUS_MISSING_DATA,
)
from scripts.build_market_ledger import compute_game_projection_context, THRESHOLD_PAPER  # noqa: E402

DEFAULT_SEARCH_PATH = os.path.join(ROOT_DIR, "data", "kalshi_search.json")
DEFAULT_SLATE_PATH = os.path.join(ROOT_DIR, "data", "slate.json")
DISCOVERY_DIR = os.path.join(ROOT_DIR, "data", "kalshi", "discovery")

ET_ZONE = ZoneInfo("America/New_York") if ZoneInfo else None


def _et_time_str(iso_ts):
    """ISO UTC timestamp -> ET 'HHMM' string, matching Kalshi's own
    ticker time encoding. Returns None if unparseable."""
    if not iso_ts:
        return None
    try:
        s = iso_ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    if ET_ZONE is not None:
        dt = dt.astimezone(ET_ZONE)
    else:
        dt = dt.astimezone(timezone(timedelta(hours=-4)))
    return dt.strftime("%H%M")


def load_json(path):
    with open(path) as f:
        return json.load(f)


def extract_raw_markets(search_doc):
    """
    Every raw market this run should consider classifying, deduplicated
    by ticker: the broad `markets` list plus the (already prefix-
    agnostic) `discoveredUnknownSeriesMarkets` list, so a market present
    in only one of the two is never missed.
    """
    seen = {}
    for m in (search_doc.get("markets") or []):
        t = m.get("market_ticker") or m.get("ticker")
        if t:
            seen[t] = m
    for m in (search_doc.get("discoveredUnknownSeriesMarkets") or []):
        t = m.get("market_ticker") or m.get("ticker")
        if t and t not in seen:
            seen[t] = m
    return list(seen.values())


def build_slate_index(slate_doc, date_str):
    """
    {(date, away, home): [ {gameId, time_str, game_dict}, ... sorted by time_str ] }
    for every game on date_str -- used both for doubleheader-aware
    known_games (contract parser) and for resolving each contract's
    real gameId + projection context.
    """
    index = {}
    known_games = []
    for g in (slate_doc.get("games") or []):
        away = (g.get("away") or {}).get("abbr")
        home = (g.get("home") or {}).get("abbr")
        start = g.get("startTime")
        time_str = _et_time_str(start)
        if not (away and home and time_str):
            continue
        key = (date_str, away, home)
        index.setdefault(key, []).append({
            "gameId": g.get("gameId"), "time_str": time_str, "game": g,
        })
        known_games.append({"date": date_str, "away": away, "home": home, "time_str": time_str})
    for key in index:
        index[key].sort(key=lambda e: e["time_str"])
    return index, known_games


def resolve_game_match(canonical, slate_index):
    """
    Match a parsed contract to its slate game via (date, away, home),
    disambiguating a doubleheader by closest scheduled time. Returns
    (real_game_id_or_None, game_dict_or_None).
    """
    key = (canonical.get("date"), canonical.get("awayTeam"), canonical.get("homeTeam"))
    candidates = slate_index.get(key)
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0]["gameId"], candidates[0]["game"]
    contract_time = canonical.get("scheduledTimeStr")
    if not contract_time:
        return candidates[0]["gameId"], candidates[0]["game"]
    best = min(candidates, key=lambda e: abs(int(e["time_str"]) - int(contract_time)))
    return best["gameId"], best["game"]


def resolve_projection_context(classification, game):
    """
    Builds the projection_context dict lib.kalshi_probability_adapters
    expects, including the side-resolved teamProj/oppProj for
    winning_margin/team_total families. Returns {} (not None) if the
    game could not be matched -- adapters correctly report
    MISSING_DATA/UNSUPPORTED for an empty context rather than crashing.
    """
    if not game:
        return {}
    ctx = compute_game_projection_context(game)
    if classification.get("subjectType") == SUBJECT_TEAM and classification.get("subjectId"):
        away_abbr = (game.get("away") or {}).get("abbr")
        home_abbr = (game.get("home") or {}).get("abbr")
        team = classification["subjectId"]
        if team == away_abbr:
            ctx["teamProj"] = ctx.get("awayProjRuns")
            ctx["oppProj"] = ctx.get("homeProjRuns")
        elif team == home_abbr:
            ctx["teamProj"] = ctx.get("homeProjRuns")
            ctx["oppProj"] = ctx.get("awayProjRuns")
    return ctx


def compute_edge_fields(fair_prob, yes_ask_pct):
    """
    Edge/EV fields for one exact contract, reusing the same executable-
    price and edge conventions scripts/build_market_ledger.py already
    uses elsewhere (executable_prob_from_price = price/100;
    THRESHOLD_PAPER = the existing minimum-edge floor, imported not
    reinvented). Returns a dict; every field is None (never 0) if
    fair_prob or yes_ask_pct is unavailable.
    """
    if fair_prob is None or yes_ask_pct is None or yes_ask_pct <= 0:
        return {
            "impliedProbabilityPct": None, "rawEdgePct": None,
            "expectedProfitPerDollar": None, "betUpToPct": None,
        }
    implied = yes_ask_pct  # ask price IS the implied probability (0-100 scale)
    raw_edge = round(fair_prob * 100 - implied, 3)
    cost = yes_ask_pct / 100.0
    expected_profit_per_dollar = round((fair_prob / cost) - 1, 4)
    bet_up_to_pct = round(fair_prob * 100 - THRESHOLD_PAPER, 3)
    return {
        "impliedProbabilityPct": round(implied, 3),
        "rawEdgePct": raw_edge,
        "expectedProfitPerDollar": expected_profit_per_dollar,
        "betUpToPct": bet_up_to_pct,
    }


_LADDER_FAMILIES = {"winning_margin", "game_total", "inning_total", "team_total"}


def mark_alternate_lines(contracts):
    """
    Mutates `contracts` in place: within each (gameId, marketFamily,
    period, subjectId) group that has more than one line, the single
    line whose implied probability is closest to 50% is the primary/
    default line (alternateLine=False); every other line in that same
    group is an alternate (alternateLine=True). A group with only one
    line is not an alternate of itself (alternateLine=False). Contracts
    with no line (moneyline, F5 winner, NRFI/YRFI) are left with
    alternateLine=None -- "alternate" is not a meaningful concept for a
    market family with exactly one line.
    """
    groups = {}
    for c in contracts:
        if c.get("marketFamily") not in _LADDER_FAMILIES or c.get("line") is None:
            continue
        key = (c.get("gameId"), c.get("marketFamily"), c.get("period"), c.get("subjectId"))
        groups.setdefault(key, []).append(c)

    for members in groups.values():
        priced = [c for c in members if c.get("impliedProbabilityPct") is not None]
        primary = min(priced, key=lambda c: abs(c["impliedProbabilityPct"] - 50.0)) if priced else members[0]
        for c in members:
            c["alternateLine"] = (c is not primary)


def discover(date_str, search_doc, slate_doc):
    """
    Pure core: given already-loaded search/slate documents, returns
    (contracts_list, summary_dict). No file I/O of its own -- callers
    (main() below, and tests) supply the loaded documents directly.
    """
    slate_index, known_games = build_slate_index(slate_doc, date_str)
    raw_markets = extract_raw_markets(search_doc)

    contracts = []
    counts = {
        "discovered": 0, "classified": 0, "modeled": 0, "exposed": 0,
        "unsupported": 0, "parseFailures": 0,
    }

    for raw in raw_markets:
        counts["discovered"] += 1
        try:
            parsed = parse_contract(raw, known_games=known_games)
        except Exception as e:
            counts["parseFailures"] += 1
            contracts.append({
                "ticker": raw.get("market_ticker") or raw.get("ticker"),
                "classificationStatus": "parse_error",
                "unsupportedReason": f"{type(e).__name__}: {e}",
                "raw": raw,
            })
            continue

        if parsed.get("date") and parsed["date"] != date_str:
            continue  # a different slate date's contract, not part of this discovery run

        classification = classify_contract(parsed)
        if classification.get("classificationStatus") in ("classified", "classified_by_title_fallback_unverified_prefix"):
            counts["classified"] += 1

        real_game_id, game = resolve_game_match(parsed, slate_index)
        gameId = real_game_id if real_game_id is not None else parsed.get("gameId")

        ctx = resolve_projection_context(classification, game)
        prob, model_status, reason = adapt_contract(
            classification.get("marketFamily"), classification.get("period"),
            classification.get("side"), classification.get("line"), ctx,
        )
        if model_status == STATUS_SUPPORTED:
            counts["modeled"] += 1
            counts["exposed"] += 1
        else:
            counts["unsupported"] += 1

        fair_prob = prob
        fair_prob_pct = round(prob * 100, 3) if prob is not None else None
        edge_fields = compute_edge_fields(fair_prob, parsed["yesAsk"])

        contract = {
            "ticker": parsed["ticker"],
            "eventTicker": parsed["eventTicker"],
            "seriesTicker": parsed["seriesTicker"],
            "marketTitle": parsed["marketTitle"],
            "gameId": gameId,
            "date": parsed["date"],
            "awayTeam": parsed["awayTeam"],
            "homeTeam": parsed["homeTeam"],
            "doubleheaderGameNumber": parsed["doubleheaderGameNumber"],
            "marketFamily": classification["marketFamily"],
            "period": classification["period"],
            "subjectType": classification["subjectType"],
            "subjectId": classification["subjectId"],
            "subjectName": classification["subjectName"],
            "side": classification["side"],
            "line": classification["line"],
            "alternateLine": None,  # resolved by the ladder-grouping stage downstream
            "yesBid": parsed["yesBid"],
            "yesAsk": parsed["yesAsk"],
            "noBid": parsed["noBid"],
            "noAsk": parsed["noAsk"],
            "volume": parsed["volume"],
            "marketStatus": parsed["marketStatus"],
            "closeTime": parsed["closeTime"],
            "classificationStatus": classification["classificationStatus"],
            "modelSupportStatus": model_status,
            "fairProbabilityPct": fair_prob_pct,
            "unsupportedReason": reason,
            # Edge/EV fields (docs/KALSHI_MLB_MARKET_COVERAGE_AUDIT.md
            # Phase 2 "Edge calculations") -- None (never 0) whenever
            # fair_prob or yesAsk is unavailable.
            **edge_fields,
        }
        contracts.append(contract)

    mark_alternate_lines(contracts)

    summary = {
        "date": date_str,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **counts,
    }
    return contracts, summary


def main(date_str=None, search_path=None, slate_path=None, out_dir=None, dry_run=False):
    search_path = search_path or DEFAULT_SEARCH_PATH
    slate_path = slate_path or DEFAULT_SLATE_PATH
    out_dir = out_dir or DISCOVERY_DIR

    try:
        search_doc = load_json(search_path)
    except FileNotFoundError:
        print(f"[discover_kalshi_mlb_markets] No search file at {search_path} — nothing to discover")
        return {"date": date_str, "discovered": 0, "classified": 0, "modeled": 0,
                "exposed": 0, "unsupported": 0, "parseFailures": 0, "status": "NO_SEARCH_FILE"}

    date_str = date_str or search_doc.get("date")
    try:
        slate_doc = load_json(slate_path)
    except FileNotFoundError:
        slate_doc = {"games": []}

    contracts, summary = discover(date_str, search_doc, slate_doc)

    if not dry_run:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"{date_str}.json"), "w") as f:
            json.dump({"date": date_str, "generatedAt": summary["generatedAt"], "contracts": contracts}, f, indent=2)
        with open(os.path.join(out_dir, f"{date_str}_summary.json"), "w") as f:
            json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    arg_date = sys.argv[1] if len(sys.argv) > 1 else None
    result = main(date_str=arg_date)
    print(json.dumps(result, indent=2))
