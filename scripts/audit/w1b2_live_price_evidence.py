#!/usr/bin/env python3
"""
scripts/audit/w1b2_live_price_evidence.py
=========================================
W1-B2, CEO review of PR #206, BLOCKER 3.

Prices EVERY CURRENT NOT-STARTED market in a freshly built Kalshi registry
through the real production seam -- lib.edgelab.production_price -- with
`decidedAt` set to the ACTUAL rehearsal instant.

Why this exists. The frozen rehearsal pins `W1_B2_DECISION_AT` to the
committed capture's own timestamp, which makes the legacy-vs-B2 comparison
deterministic but proves nothing about freshness: a quote aged against its own
capture time is zero seconds old by construction. This report does the
opposite. It takes a registry built moments ago from the live exchange and
asks the question production will ask:

    given the clock as it is right now, can each of these contracts prove a
    contract, a side, a unit, a genuine executable price and a capture time
    that is neither in the future nor stale?

and reports the measured answers -- real quote ages, real refusals, real
one-sided books -- rather than assuming them.

READ-ONLY. It reads a registry file and writes one JSON report to the path
given. It never fetches, never writes into the repository, never constructs a
recommendation and never places a wager. Model-driven real-money authority is
untouched.
"""

import argparse
import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, ROOT)

from lib.edgelab import canonical_price as cp          # noqa: E402
from lib.edgelab import production_price as pp         # noqa: E402

# Registry market blocks -> the family label used in the report, and how the
# contract's sides are addressed. Mirrors what scripts/merge_odds.py builds a
# book from; nothing here re-derives a price.
TWO_SIDED_FAMILIES = {
    "moneyline": ("ML_Away", "ML_Home"),
    "f5_moneyline": ("F5_ML_Away", "F5_ML_Home"),
}
SINGLE_BLOCK_FAMILIES = {
    "spread": "RunLine",
    "total": "Game_Total",
    "team_total_away": "TT_Away",
    "team_total_home": "TT_Home",
}


def _parse_et_game_start(date_str, game_time_et):
    """
    The game's scheduled first pitch, as an aware UTC instant.

    `game_time_et` is a local Eastern wall-clock string. Eastern is UTC-4
    during the MLB season, which is the only period this registry covers, so
    the offset is applied directly rather than pulling in a tz database that
    the CI image may not carry.
    """
    if not date_str or not game_time_et:
        return None
    text = str(game_time_et).strip()
    for fmt in ("%H:%M", "%H%M", "%I:%M %p"):
        try:
            t = datetime.strptime(text, fmt)
        except ValueError:
            continue
        try:
            d = datetime.strptime(str(date_str), "%Y-%m-%d")
        except ValueError:
            return None
        return datetime(d.year, d.month, d.day, t.hour, t.minute,
                        tzinfo=timezone(timedelta(hours=-4))).astimezone(timezone.utc)
    return None


def _book_from_price_block(pb, ticker):
    """
    The same shape scripts/merge_odds.py's `_book()` hands the decision layer,
    built from the same registry price block. Deliberately NOT a second
    pricing implementation: it copies fields across and states the unit and
    capture time the block itself declares.
    """
    pb = pb or {}
    return {
        "ticker": ticker,
        "yes_bid": pb.get("yes_bid"),
        "yes_ask": pb.get("yes_ask"),
        "no_bid": pb.get("no_bid"),
        "no_ask": pb.get("no_ask"),
        "unit": pb.get("unit"),
        "captured_at": pb.get("captured_at"),
        "book_state": pb.get("book_state"),
        "price_level_structure": pb.get("price_level_structure"),
        "source": pb.get("_source") or "kalshi_registry_direct",
    }


def _price(book, side, decided_at):
    return pp.price_contract(
        market_ticker=book.get("ticker"), side=side,
        yes_bid=book.get("yes_bid"), yes_ask=book.get("yes_ask"),
        no_bid=book.get("no_bid"), no_ask=book.get("no_ask"),
        unit=book.get("unit"),
        grid=book.get("price_level_structure") or cp.GRID_UNKNOWN,
        captured_at=book.get("captured_at"), decided_at=decided_at,
        source=book.get("source"),
        side_basis="CONTRACT_YES_IS_THIS_SELECTION" if side == cp.SIDE_YES
                   else "CONTRACT_NO_IS_THIS_SELECTION",
    )


def _candidates(entry):
    """(family, ticker, price_block) for every priceable contract in a game."""
    markets = entry.get("markets") or {}
    for key, (away_label, home_label) in TWO_SIDED_FAMILIES.items():
        block = markets.get(key) or {}
        prices = block.get("prices") or {}
        for side_key, label in (("away", away_label), ("home", home_label)):
            pb = prices.get(side_key)
            if pb:
                yield label, block.get("%s_ticker" % side_key), pb
        tie = prices.get("tie")
        if tie:
            yield "%s_Tie" % away_label.rsplit("_", 1)[0], block.get("tie_ticker"), tie

    for key, label in SINGLE_BLOCK_FAMILIES.items():
        block = markets.get(key) or {}
        bl = block.get("best_line") or {}
        pb = bl.get("prices") or bl.get("price") or block.get("prices")
        if isinstance(pb, dict) and ("yes_ask" in pb or "yes_bid" in pb):
            yield label, bl.get("ticker") or block.get("ticker"), pb
        for line in (block.get("all_lines") or []):
            lpb = line.get("prices") or line.get("price")
            if isinstance(lpb, dict) and ("yes_ask" in lpb or "yes_bid" in lpb):
                yield label, line.get("ticker"), lpb

    rfi = markets.get("rfi") or {}
    yrfi = (rfi.get("prices") or {}).get("yrfi")
    if yrfi:
        yield "YRFI", rfi.get("ticker"), yrfi
        yield "NRFI", rfi.get("ticker"), yrfi   # same contract, NO side


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    idx = min(int(round((pct / 100.0) * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[idx]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="W1-B2 live executable-price and freshness evidence")
    parser.add_argument("--registry", required=True,
                        help="a freshly built kalshi_market_registry.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--decided-at", default=None,
                        help="the rehearsal decision instant; defaults to NOW, "
                             "which is the point -- do not pin this back to a "
                             "quote's own capture time")
    args = parser.parse_args(argv)

    decided_at = args.decided_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    decided_dt = cp.parse_instant(decided_at)

    with open(args.registry) as handle:
        doc = json.load(handle)
    registry = doc.get("registry") or {}

    families = collections.defaultdict(collections.Counter)
    refusals = collections.Counter()
    book_states = collections.Counter()
    bases = collections.Counter()
    ages = []
    one_sided = []
    totals = collections.Counter()
    games_seen = []

    for key, entry in sorted(registry.items()):
        start = _parse_et_game_start(entry.get("date"), entry.get("game_time_et"))
        started = bool(start and start <= decided_dt)
        games_seen.append({"key": key, "away": entry.get("away"),
                           "home": entry.get("home"),
                           "scheduledStartUtc": start.strftime("%Y-%m-%dT%H:%M:%SZ")
                                                 if start else None,
                           "notStarted": not started})
        totals["games"] += 1
        if started:
            totals["gamesAlreadyStartedSkipped"] += 1
            continue
        totals["gamesNotStarted"] += 1

        for family, ticker, pb in _candidates(entry):
            book = _book_from_price_block(pb, ticker)
            side = cp.SIDE_NO if family == "NRFI" else cp.SIDE_YES
            result = _price(book, side, decided_at)
            prov = result["provenance"]

            totals["contracts"] += 1
            families[family]["contracts"] += 1
            state = prov.get("bookState") or "UNKNOWN"
            book_states[state] += 1
            families[family][state] += 1

            if state in ("ASK_ONLY", "BID_ONLY"):
                one_sided.append({
                    "family": family, "ticker": ticker, "side": side,
                    "bookState": state, "actionable": result["actionable"],
                    "executablePriceCents": (
                        None if result["executablePriceCents"] is None
                        else str(result["executablePriceCents"])),
                    "refusalReason": result["refusalReason"],
                })

            if result["actionable"]:
                totals["actionable"] += 1
                families[family]["actionable"] += 1
                bases[prov.get("priceBasis") or "UNKNOWN"] += 1
            else:
                totals["refused"] += 1
                families[family]["refused"] += 1
                refusals["%s :: %s" % (family, result["refusalReason"])] += 1

            age = prov.get("quoteAgeSeconds")
            if age is not None:
                ages.append(age)
                if age < 0:
                    totals["futureDatedQuotes"] += 1

    payload = {
        "reportVersion": "w1b2-live-price-evidence-1",
        "decidedAt": decided_at,
        "decisionClock": "REAL rehearsal time; NOT pinned to any quote's capture time",
        "registryGeneratedAt": doc.get("generated_at"),
        "registryLastSnapshotTs": doc.get("last_snapshot_ts"),
        "modelDrivenRealMoneyAuthority": "OFF",
        "recommendationAuthorityExercised": False,
        "wagersCreated": 0,
        "totals": dict(totals),
        "quoteAgeSeconds": {
            "count": len(ages),
            "min": min(ages) if ages else None,
            "median": _percentile(ages, 50),
            "p95": _percentile(ages, 95),
            "max": max(ages) if ages else None,
            "negative": sum(1 for a in ages if a < 0),
        },
        "familyCoverage": {f: dict(c) for f, c in sorted(families.items())},
        "refusalReasons": dict(refusals),
        "bookStateDistribution": dict(book_states),
        "priceBasisDistribution": dict(bases),
        "oneSidedBooks": one_sided[:200],
        "oneSidedBookCount": len(one_sided),
        "games": games_seen,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)

    print("W1-B2 LIVE EXECUTABLE-PRICE EVIDENCE")
    print("  decided at             %s (real clock)" % decided_at)
    print("  games in registry      %s" % totals.get("games", 0))
    print("  games not started      %s" % totals.get("gamesNotStarted", 0))
    print("  contracts priced       %s" % totals.get("contracts", 0))
    print("  actionable             %s" % totals.get("actionable", 0))
    print("  refused                %s" % totals.get("refused", 0))
    print("  future-dated quotes    %s" % totals.get("futureDatedQuotes", 0))
    q = payload["quoteAgeSeconds"]
    print("  quote age s (min/med/p95/max)  %s / %s / %s / %s"
          % (q["min"], q["median"], q["p95"], q["max"]))
    print("  book states            %s" % dict(book_states))
    print("  one-sided books        %s" % len(one_sided))
    if refusals:
        print("  refusals:")
        for reason, count in sorted(refusals.items()):
            print("    %-70s %s" % (reason, count))
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
