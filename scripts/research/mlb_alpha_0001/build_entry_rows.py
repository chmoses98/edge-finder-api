#!/usr/bin/env python3
"""MLB-ALPHA-0001: build frozen strategy-entry rows for one research split.

UNIT OF ANALYSIS (Mission 2, see charter):
  CONTRACT     = one Kalshi marketTicker (one settlement event)
  OBSERVATION  = one timestamped quote of that contract
  GAME         = underlying MLB game (eventTicker date+time+teams)
  DATE         = the game's calendar date (from eventTicker)
  ENTRY ROW    = one contract at one predeclared entry checkpoint

Two predeclared entry checkpoints, both deterministic and pregame-guarded:
  FIRST_DAILY_DERIVED  earliest ACTIVE observation of the contract on its
                       game date, captured strictly before scheduled start
  LAST_PREGAME         latest  ACTIVE observation of the contract on its
                       game date, captured strictly before scheduled start

Scheduled start is decoded from the eventTicker (YYMMMDDHHMM, US/Eastern,
UTC-4 across the whole August 2026 archive) -- validated against the
`scheduledStart` field with 0.0 min median error on 26,261 rows.

Executable prices:
  YES buy: yesAsk.            NO buy: 100 - yesBid (binary complement of
  the YES book; the archive never populated noBid/noAsk directly).

Settlement join: settlementStatus=SETTLED with result YES/NO only.

The blind holdout is REFUSED: this builder only accepts discovery or
validation. RESEARCH ONLY.
"""

import argparse
import glob
import gzip
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
EDGELAB = os.path.join(REPO, "data", "edgelab")
ART = os.path.join(EDGELAB, "research_artifacts", "mlb_alpha_0001")

MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}
EVENT_RE = re.compile(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+)$")
ET_UTC_OFFSET_HOURS = 4  # EDT; entire reliable archive is August 2026


def parse_event(event_ticker):
    """-> (game_date 'YYYY-MM-DD', scheduled_start_utc datetime, teams) or None."""
    m = EVENT_RE.match(event_ticker or "")
    if not m:
        return None
    yy, mon, dd, hhmm, teams = m.groups()
    try:
        local = datetime(2000 + int(yy), MON[mon], int(dd),
                         int(hhmm[:2]), int(hhmm[2:]))
    except (KeyError, ValueError):
        return None
    start_utc = local + timedelta(hours=ET_UTC_OFFSET_HOURS)
    return "%04d-%02d-%02d" % (2000 + int(yy), MON[mon], int(dd)), start_utc, teams


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)


def iter_jsonl(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def partition_paths(subdir):
    out = {}
    for p in sorted(glob.glob(os.path.join(EDGELAB, subdir, "*.jsonl*"))):
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$", os.path.basename(p))
        if m:
            out[m.group(1)] = p
    return out


def load_settlement_map():
    settled = {}
    for date, path in partition_paths("settlements").items():
        for r in iter_jsonl(path):
            if r.get("settlementStatus") == "SETTLED" and r.get("result") in ("YES", "NO"):
                settled[r["marketTicker"]] = r["result"]
    return settled


META_FIELDS = (
    "marketFamily", "seriesTicker", "eventTicker", "team", "player",
    "threshold", "comparisonOperator", "outcomeLabel", "homeTeam",
    "awayTeam", "marketHorizon", "subtitle",
)
QUOTE_FIELDS = ("yesBid", "yesAsk", "lastPrice", "spreadCents", "volume",
                "openInterest", "marketStatus", "capturedAt", "checkpoint")


def build_for_date(date, path):
    """Entry rows for contracts whose GAME date == this partition date."""
    per_ticker = {}
    for r in iter_jsonl(path):
        ev = parse_event(r.get("eventTicker"))
        if ev is None:
            continue
        game_date, start_utc, _teams = ev
        if game_date != date:
            continue
        if (r.get("marketStatus") or "active").lower() not in ("active", "unknown"):
            continue
        cap = parse_ts(r["capturedAt"])
        if cap >= start_utc:
            continue  # pregame guard
        t = r["marketTicker"]
        slot = per_ticker.setdefault(t, {"first": None, "last": None, "meta": None,
                                         "startUtc": start_utc})
        if slot["meta"] is None:
            slot["meta"] = {k: r.get(k) for k in META_FIELDS}
            slot["meta"]["gameId"] = r.get("gameId")
        if slot["first"] is None or cap < slot["first"][0]:
            slot["first"] = (cap, r)
        if slot["last"] is None or cap > slot["last"][0]:
            slot["last"] = (cap, r)
    return per_ticker


def emit_rows(date, per_ticker, settled):
    rows = []
    for ticker, slot in per_ticker.items():
        result = settled.get(ticker)
        for cp_name, key in (("FIRST_DAILY_DERIVED", "first"),
                             ("LAST_PREGAME", "last")):
            pair = slot[key]
            if pair is None:
                continue
            cap, r = pair
            row = {
                "programId": "MLB-ALPHA-0001",
                "gameDate": date,
                "marketTicker": ticker,
                "entryCheckpoint": cp_name,
                "scheduledStartUtc": slot["startUtc"].isoformat() + "Z",
                "minutesToStart": round(
                    (slot["startUtc"] - cap).total_seconds() / 60.0, 1),
                "settlementResult": result,  # YES / NO / None
            }
            row.update(slot["meta"])
            for k in QUOTE_FIELDS:
                row[k] = r.get(k)
            yb, ya = r.get("yesBid"), r.get("yesAsk")
            row["noExecAsk"] = (100.0 - yb) if yb is not None else None
            row["yesExecAsk"] = ya
            rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True, choices=["discovery", "validation"],
                    help="blindHoldout is intentionally NOT accepted")
    args = ap.parse_args()

    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        splits = json.load(fh)
    dates = splits[args.split]["dates"]

    settled = load_settlement_map()
    parts = partition_paths("observations")
    out_path = os.path.join(ART, "entry_rows_%s.jsonl.gz" % args.split)
    n = 0
    games = set()
    with gzip.open(out_path, "wt") as out:
        for date in dates:
            if date not in parts:
                print("WARNING: no observation partition for", date)
                continue
            per_ticker = build_for_date(date, parts[date])
            rows = emit_rows(date, per_ticker, settled)
            for row in rows:
                out.write(json.dumps(row, sort_keys=True) + "\n")
                games.add(row["eventTicker"])
            n += len(rows)
            print(date, len(per_ticker), "tickers ->", len(rows), "entry rows")
    digest = hashlib.sha256(open(out_path, "rb").read()).hexdigest()
    meta = {
        "split": args.split,
        "dates": dates,
        "rows": n,
        "uniqueEvents": len(games),
        "file": os.path.relpath(out_path, REPO),
        "sha256": digest,
        "builder": "scripts/research/mlb_alpha_0001/build_entry_rows.py",
    }
    with open(os.path.join(ART, "entry_rows_%s.meta.json" % args.split), "w") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("wrote", out_path, n, "rows,", len(games), "events; sha256", digest[:16])


if __name__ == "__main__":
    sys.exit(main())
