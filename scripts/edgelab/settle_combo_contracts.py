#!/usr/bin/env python3
"""
scripts/edgelab/settle_combo_contracts.py
============================================
Settle every pending COMBO_CONTRACT wager in data/edgelab/bets/bets.jsonl from the exchange's own final result for
its combo contract. The policy is lib/edgelab/combo_contract_settlement.py; this file only reads the ledger, asks
Kalshi's PUBLIC market endpoint (no credential) about each pending combo ticker, and writes what the policy decided
through the same storage upserts settle_markets.py uses.

    python3 scripts/edgelab/settle_combo_contracts.py [--dry-run]

Idempotent: an already-settled combo is not considered again; a combo whose contract is not final yet is left
pending and re-asked on the next run; a Settlement row is merged with lib.edgelab.settlement.merge_settlement_record,
which never lets a non-terminal answer overwrite a stored SETTLED/VOID record. Straight wagers, MULTI_LEG parlays and
player props are never read or written here.

Exit 0 unless the ledger itself cannot be read (2).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import ids, storage  # noqa: E402
from lib.edgelab.combo_contract_settlement import is_combo_contract, settle_combo_contract  # noqa: E402
from lib.edgelab.settlement import merge_settlement_record  # noqa: E402

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
ATTEMPTS = 3


def fetch_market(ticker, *, opener=urllib.request.urlopen, sleep=time.sleep):
    """The exchange's ``GET /markets/{ticker}`` body, or None when it cannot be read (the policy then leaves the
    combo pending with COMBO_MARKET_FETCH_FAILED). A 404 is an answer -- the exchange does not know the ticker --
    and is returned as an empty body rather than retried."""
    url = f"{KALSHI_BASE}/markets/{urllib.parse.quote(ticker, safe='')}"
    for attempt in range(ATTEMPTS):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                           "User-Agent": "edge-finder-api/combo-settlement"})
            with opener(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            if attempt + 1 < ATTEMPTS:
                sleep(2 ** attempt)
        except (urllib.error.URLError, TimeoutError, ValueError):
            if attempt + 1 < ATTEMPTS:
                sleep(2 ** attempt)
    return None


def settle_pending_combos(bets, existing_settlements_for, fetch=fetch_market, now=None):
    """Pure orchestration over injected I/O. ``existing_settlements_for(date) -> {settlementId: record}``.
    Returns (bet_updates, settlements_by_date, summary)."""
    now = now or ids.utc_now_iso()
    pending = [b for b in bets if is_combo_contract(b) and b.get("status") != "settled" and b.get("marketTicker")]
    by_ticker = {}
    for bet in pending:
        by_ticker.setdefault(bet["marketTicker"], []).append(bet)
    bet_updates, settlements_by_date = [], {}
    summary = {"pendingCombos": len(pending), "tickers": len(by_ticker), "settled": 0, "unresolved": 0,
               "betsWritten": 0, "unresolvedReasons": {}}
    for ticker, on_ticker in sorted(by_ticker.items()):
        payload = fetch(ticker)
        to_write, record = settle_combo_contract(on_ticker, ticker, payload, now=now)
        if record["settlementStatus"] == "SETTLED":
            summary["settled"] += 1
        else:
            summary["unresolved"] += 1
            reason = (record.get("unavailableReason") or "unknown").split(":")[0]
            summary["unresolvedReasons"][reason] = summary["unresolvedReasons"].get(reason, 0) + 1
        bet_updates.extend(to_write)
        date = on_ticker[0].get("gameDate")
        if date:
            existing = existing_settlements_for(date).get(record["settlementId"])
            merged = merge_settlement_record(existing, record)
            if merged is not existing:
                settlements_by_date.setdefault(date, []).append(merged)
    summary["betsWritten"] = len(bet_updates)
    return bet_updates, settlements_by_date, summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    bets_path = storage.singleton_path("bets", "bets.jsonl")
    try:
        bets = list(storage.read_records(bets_path))
    except OSError as exc:
        print(f"[settle_combo_contracts] could not read the bet ledger: {exc}", file=sys.stderr)
        return 2

    cache = {}

    def existing_for(date):
        if date not in cache:
            cache[date] = {r["settlementId"]: r for r in storage.read_records(storage.resolve_partition_path("settlements", date))
                           if r.get("settlementId")}
        return cache[date]

    bet_updates, settlements_by_date, summary = settle_pending_combos(bets, existing_for)
    if not args.dry_run:
        for date, records in sorted(settlements_by_date.items()):
            storage.upsert_records(storage.resolve_partition_path("settlements", date), records, "settlementId")
        if bet_updates:
            storage.upsert_records(bets_path, bet_updates, "betId")
    print(f"[settle_combo_contracts] pending_combos={summary['pendingCombos']} tickers={summary['tickers']} "
          f"settled={summary['settled']} unresolved={summary['unresolved']} bets_written={summary['betsWritten']} "
          f"unresolved_reasons={summary['unresolvedReasons']}" + (" DRY RUN" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
