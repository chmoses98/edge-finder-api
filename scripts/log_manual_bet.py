#!/usr/bin/env python3
"""
scripts/log_manual_bet.py
===========================
Validates and safely appends a manually-recommended (or model-recommended
but manually-executed) bet to the same bets.json ledger the automated
pipeline uses — so it is picked up by the same closing-line capture and
CLV settlement path (scripts/capture_closing_lines.py, clv_from_snapshot.py)
as every other real-money bet.

Required fields (all validated; see REQUIRED_FIELDS below):
  date, game, market, side, line (pass null for line-less markets like ML
  or NRFI/YRFI — the key must still be present), ticker, entryBid,
  entryAsk, entryMid, purchasedPrice, entryTimestamp, probability, stake,
  source ("MANUAL" or "MODEL").

`gameId` is optional (only required "when available").

Usage:
  python3 scripts/log_manual_bet.py --json '{"date": "2026-07-30", ...}'
  python3 scripts/log_manual_bet.py --file path/to/bet.json
  echo '{...}' | python3 scripts/log_manual_bet.py --stdin

Programmatic use:
  from log_manual_bet import validate_bet, log_bet
  entry = log_bet(data)   # validates, builds, appends, returns the stored entry
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from atomic_json import write_json_atomic

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
BETS_PATH = os.path.join(ROOT_DIR, "bets.json")

VALID_SOURCES = {"MANUAL", "MODEL"}

# Every one of these keys must be PRESENT in the input. All except `line`
# and `gameId` must also be non-null. `line` must be present but may be
# explicitly null for line-less markets (ML, F5 ML, NRFI, YRFI). `gameId`
# is fully optional ("when available") and is not in this list at all.
REQUIRED_KEYS = [
    "date", "game", "market", "side", "line", "ticker",
    "entryBid", "entryAsk", "entryMid", "purchasedPrice",
    "entryTimestamp", "probability", "stake", "source",
]
REQUIRED_NON_NULL = [k for k in REQUIRED_KEYS if k != "line"]


class ManualBetValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("Manual bet validation failed:\n  - " + "\n  - ".join(errors))


def _parse_date(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _parse_iso_ts(ts_str):
    if not isinstance(ts_str, str) or not ts_str:
        return None
    try:
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _to_pct(value):
    """Normalize a probability/price given as either a 0-1 fraction or a
    0-100 percentage into a 0-100 percentage."""
    v = float(value)
    return round(v * 100, 2) if v <= 1 else round(v, 2)


def _american_to_prob_pct(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    prob = 100 / (o + 100) if o >= 0 else abs(o) / (abs(o) + 100)
    return round(prob * 100, 2)


def validate_bet(data):
    """
    Validate a manual bet dict. Raises ManualBetValidationError with every
    problem found (not just the first) if invalid. Returns nothing on
    success.
    """
    errors = []

    if not isinstance(data, dict):
        raise ManualBetValidationError(["input must be a JSON object"])

    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(f"missing required field: {key}")

    for key in REQUIRED_NON_NULL:
        if key in data and data.get(key) is None:
            errors.append(f"required field must not be null: {key}")

    if "date" in data and data.get("date") is not None and not _parse_date(data["date"]):
        errors.append(f"date must be YYYY-MM-DD, got {data.get('date')!r}")

    if "entryTimestamp" in data and data.get("entryTimestamp") is not None:
        if _parse_iso_ts(data["entryTimestamp"]) is None:
            errors.append(f"entryTimestamp must be a parseable ISO-8601 timestamp, got {data.get('entryTimestamp')!r}")

    if "source" in data and data.get("source") not in VALID_SOURCES:
        errors.append(f"source must be one of {sorted(VALID_SOURCES)}, got {data.get('source')!r}")

    for key in ("entryBid", "entryAsk", "entryMid", "probability"):
        if key in data and data.get(key) is not None:
            try:
                v = float(data[key])
                if v < 0:
                    errors.append(f"{key} must be non-negative, got {v}")
            except (TypeError, ValueError):
                errors.append(f"{key} must be numeric, got {data.get(key)!r}")

    if "purchasedPrice" in data and data.get("purchasedPrice") is not None:
        try:
            float(data["purchasedPrice"])
        except (TypeError, ValueError):
            errors.append(f"purchasedPrice must be numeric (American odds), got {data.get('purchasedPrice')!r}")

    if "stake" in data and data.get("stake") is not None:
        try:
            stake = float(data["stake"])
            if stake <= 0:
                errors.append(f"stake must be > 0, got {stake}")
        except (TypeError, ValueError):
            errors.append(f"stake must be numeric, got {data.get('stake')!r}")

    if errors:
        raise ManualBetValidationError(errors)


def _next_id(bets, date_str):
    seq = 1
    prefix = f"{date_str}-"
    existing = [b.get("id", "") for b in bets if str(b.get("id", "")).startswith(prefix)]
    nums = []
    for bid in existing:
        tail = bid[len(prefix):]
        if tail.isdigit():
            nums.append(int(tail))
    if nums:
        seq = max(nums) + 1
    return f"{date_str}-{seq:03d}"


def build_bet_entry(data, bets):
    """Build the bets.json-shaped entry from validated input. Field names
    match the conventions already used elsewhere in this ledger (game,
    betSide, line, price/betTimeLine, marketTicker/ticker, stake,
    scheduledStartTime) so it is picked up by capture_closing_lines.py and
    clv_from_snapshot.py exactly like an automated bet."""
    date_str = data["date"]
    entry = {
        "id": _next_id(bets, date_str),
        "date": date_str,
        "gameId": data.get("gameId"),
        "game": data["game"],
        "market": data["market"],
        "betSide": data["side"],
        "line": data.get("line"),
        "marketTicker": data["ticker"],
        "ticker": data["ticker"],
        "entryBidPct": _to_pct(data["entryBid"]),
        "entryAskPct": _to_pct(data["entryAsk"]),
        "entryMidPct": _to_pct(data["entryMid"]),
        "purchasedPrice": data["purchasedPrice"],
        "price": data["purchasedPrice"],
        "betTimeLine": data["purchasedPrice"],
        "purchasedPricePct": _american_to_prob_pct(data["purchasedPrice"]),
        "entryTimestamp": data["entryTimestamp"],
        "probabilityPct": _to_pct(data["probability"]),
        "stake": float(data["stake"]),
        "betSize": float(data["stake"]),
        "source": data["source"],
        "status": data.get("status", "pending"),
        "notes": data.get("notes", ""),
    }
    if data.get("eventTicker"):
        entry["eventTicker"] = data["eventTicker"]
    if data.get("scheduledStartTime"):
        entry["scheduledStartTime"] = data["scheduledStartTime"]
    return entry


def log_bet(data, bets_path=None, allow_duplicate=False):
    """
    Validate `data`, build a ledger entry, and append it to bets.json.

    Duplicate guard: refuses to append a second entry with the same
    (date, ticker, entryTimestamp) unless allow_duplicate=True — protects
    against accidentally double-logging the same manual bet.

    Returns the stored entry dict.
    """
    validate_bet(data)

    path = bets_path or BETS_PATH
    if os.path.exists(path):
        with open(path) as f:
            bets = json.load(f)
    else:
        bets = []

    if not allow_duplicate:
        for b in bets:
            if (b.get("date") == data["date"]
                    and (b.get("marketTicker") or b.get("ticker")) == data["ticker"]
                    and b.get("entryTimestamp") == data["entryTimestamp"]):
                raise ValueError(
                    f"Duplicate manual bet: a bet with date={data['date']} "
                    f"ticker={data['ticker']} entryTimestamp={data['entryTimestamp']} "
                    "already exists in bets.json. Pass allow_duplicate=True to force."
                )

    entry = build_bet_entry(data, bets)
    bets.append(entry)

    write_json_atomic(bets, path, indent=2)

    return entry


def _load_input(args):
    if args.json:
        return json.loads(args.json)
    if args.file:
        with open(args.file) as f:
            return json.load(f)
    if args.stdin:
        return json.loads(sys.stdin.read())
    raise SystemExit("Provide one of --json, --file, or --stdin")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Validate and log a manual bet to bets.json")
    parser.add_argument("--json", help="Bet fields as a JSON string")
    parser.add_argument("--file", help="Path to a JSON file with bet fields")
    parser.add_argument("--stdin", action="store_true", help="Read bet fields as JSON from stdin")
    parser.add_argument("--allow-duplicate", action="store_true", help="Skip the duplicate guard")
    cli_args = parser.parse_args()

    payload = _load_input(cli_args)
    try:
        stored = log_bet(payload, allow_duplicate=cli_args.allow_duplicate)
    except ManualBetValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Logged manual bet {stored['id']}: {stored['game']} {stored['market']} "
          f"{stored['betSide']} @ {stored['purchasedPrice']} (stake={stored['stake']})")
    print(json.dumps(stored, indent=2))
