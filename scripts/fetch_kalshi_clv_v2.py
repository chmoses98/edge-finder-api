#!/usr/bin/env python3
"""
PHASE 3 — HISTORICAL KALSHI CLV
fetch_kalshi_clv_v2.py

For every settled bet with a marketTicker:
  1. Use stored marketTicker (never reconstruct from game+market+side)
  2. Pull Kalshi candlestick data — highest resolution available
  3. Select last valid candle BEFORE scheduled first pitch
  4. Never estimate or infer price
  5. Fail loudly when data is missing

CLV validation (Phase 4):
  Fails if:
  - marketTicker missing
  - scheduledStartTime missing
  - no candle before start
  - selected candle is after start
  - price estimated or inferred

Stores on each bet:
  closingPrice, closingTimestamp, clv, clvStatus, clvSource, clvError
"""
import json, os, sys, time
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
BETS_PATH = os.path.join(os.path.dirname(__file__), "..", "bets.json")

# Resolution ladder: try finest first
CANDLESTICK_INTERVALS = [1, 5, 15, 60]  # minutes

# Markets where YES = YRFI (run scored); NO = NRFI
# The market_ticker for NRFI/YRFI is shared — YES_price = YRFI prob
YRFI_YES_MARKETS = {"YRFI"}
NRFI_YES_MARKETS = {"NRFI"}


def kget(url, retries=2):
    for attempt in range(retries + 1):
        try:
            req = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=30) as r:
                return json.loads(r.read()), None
        except HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            return None, f"HTTP {e.code}"
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            return None, str(e)
    return None, "max retries exceeded"


def parse_ts(ts_str):
    """Parse ISO timestamp → Unix epoch."""
    if not ts_str:
        return None
    try:
        # Handle Z suffix
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except:
        return None


def get_candlestick_clv(ticker, scheduled_start_ts, market_type, window_seconds=7200):
    """
    Fetch Kalshi candlestick data and return last valid candle before scheduled_start_ts.

    Returns:
        {
            "closingPrice": float (0.0–1.0 as YES probability),
            "closingTimestamp": str (ISO),
            "clvSource": str,
            "numCandles": int,
            "intervalMinutes": int,
        }
        OR raises ValueError with clvError message.
    """
    start_ts = scheduled_start_ts - window_seconds
    end_ts = scheduled_start_ts  # exclusive — we want pre-game only

    for interval in CANDLESTICK_INTERVALS:
        # Try both current and historical endpoints
        for endpoint in [
            f"{KALSHI_BASE}/markets/{ticker}/candlesticks",
            f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks",
        ]:
            url = f"{endpoint}?start_ts={start_ts}&end_ts={end_ts}&period_interval={interval}"
            data, err = kget(url)

            if err:
                continue
            if not data:
                continue

            candles = data.get("candlesticks", [])
            if not candles:
                continue

            # Filter to candles strictly before scheduled start
            valid = [
                c for c in candles
                if c.get("end_period_ts", 0) <= scheduled_start_ts
            ]
            if not valid:
                # All candles after start — data corruption or wrong time
                continue

            # Select last valid candle
            last = max(valid, key=lambda c: c.get("end_period_ts", 0))
            candle_ts = last.get("end_period_ts", 0)

            # Validate candle is actually before start (belt-and-suspenders)
            if candle_ts > scheduled_start_ts:
                raise ValueError(
                    f"selected candle end_period_ts {candle_ts} > start {scheduled_start_ts} — rejected"
                )

            # Extract price — YES side bid/ask midpoint
            bid_raw = last.get("yes_bid", {}).get("close") if isinstance(last.get("yes_bid"), dict) else last.get("yes_bid")
            ask_raw = last.get("yes_ask", {}).get("close") if isinstance(last.get("yes_ask"), dict) else last.get("yes_ask")

            try:
                bid = float(bid_raw) if bid_raw is not None else None
                ask = float(ask_raw) if ask_raw is not None else None
            except (TypeError, ValueError):
                bid, ask = None, None

            if bid is not None and ask is not None and bid > 0 and ask > 0:
                mid = round((bid + ask) / 2, 6)
            elif bid is not None and bid > 0:
                mid = bid
            elif ask is not None and ask > 0:
                mid = ask
            else:
                # Try last_price field
                lp = last.get("last_price")
                try:
                    mid = float(lp) if lp else None
                except:
                    mid = None

            if mid is None or mid <= 0 or mid >= 1:
                continue

            candle_iso = datetime.fromtimestamp(candle_ts, tz=timezone.utc).isoformat()

            return {
                "closingPrice": mid,
                "closingTimestamp": candle_iso,
                "clvSource": f"candlestick_{interval}min",
                "numCandles": len(valid),
                "intervalMinutes": interval,
                "endpoint": endpoint.split("/v2/")[1].split("?")[0],
            }

    raise ValueError(f"no valid candlestick found in {window_seconds}s window before {scheduled_start_ts}")


def get_market_detail_price(ticker, scheduled_start_ts):
    """
    Fallback: market detail endpoint.
    Only use last_price if market is settled/finalized (price frozen before game).
    We do NOT use this for active/open markets — it could be live price.
    """
    data, err = kget(f"{KALSHI_BASE}/markets/{ticker}")
    if err or not data:
        raise ValueError(f"market detail fetch failed: {err}")

    m = data.get("market", {})
    status = m.get("status", "")
    close_time = m.get("close_time")
    last_price = m.get("last_price")

    # Only accept if market is closed/settled
    if status not in ("finalized", "settled"):
        raise ValueError(f"market detail fallback only valid for settled markets; status={status}")

    # Verify close_time is before scheduled_start (pre-game close)
    if close_time:
        close_ts = parse_ts(close_time)
        if close_ts and close_ts > scheduled_start_ts + 300:  # 5-min grace
            raise ValueError(
                f"market close_time {close_time} is after scheduled start — not a pre-game close"
            )

    try:
        price = float(last_price) if last_price is not None else None
    except:
        price = None

    if price is None or price <= 0 or price >= 1:
        raise ValueError(f"market detail last_price invalid: {last_price}")

    return {
        "closingPrice": price,
        "closingTimestamp": close_time or "unknown",
        "clvSource": "market_detail_last_price",
        "status": status,
    }


def price_to_american(p):
    """Convert YES probability (0–1) to American odds."""
    if p <= 0 or p >= 1:
        return None
    if p > 0.5:
        return round(-p / (1 - p) * 100)
    else:
        return round((1 - p) / p * 100)


def price_to_implied_pct(p):
    return round(p * 100, 2)


def calculate_clv(entry_price_american, closing_yes_prob, market_type, bet_is_yes):
    """
    Calculate CLV = (closing_VF_prob − entry_VF_prob) × 100.

    For YES bets: closing_prob = closing_yes_prob
    For NO bets: closing_prob = 1 − closing_yes_prob

    entry_price_american: American odds at bet time (e.g. -135, +115)
    Returns CLV in percentage points (positive = beat the close).
    """
    if entry_price_american is None or closing_yes_prob is None:
        return None

    # Entry implied prob from American odds (no vig adjustment needed — it's our actual price)
    ep = abs(entry_price_american)
    if entry_price_american < 0:
        entry_implied = ep / (ep + 100)
    else:
        entry_implied = 100 / (ep + 100)

    # Closing prob for our side
    if bet_is_yes:
        closing_implied = closing_yes_prob
    else:
        closing_implied = 1 - closing_yes_prob

    clv = round((closing_implied - entry_implied) * 100, 3)
    return clv


def is_yes_bet(bet_str, market_type, market_ticker, side):
    """
    Determine if this bet is the YES side of the Kalshi market.

    For ML/F5 ML/Run Line: YES = team named in marketTicker suffix wins
    For NRFI: YES = run scored in first inning (which is YRFI!)
    For YRFI: YES = run scored in first inning
    """
    if not market_ticker:
        return True  # assume YES if unknown

    ticker_upper = market_ticker.upper()
    bet_upper = (bet_str or "").upper()
    side_upper = (side or "").upper()

    if market_type in ("NRFI", "YRFI"):
        # KXMLBRFI tickers: YES = YRFI (run scored)
        if market_type == "YRFI":
            return True
        else:  # NRFI
            return False  # NRFI bet = NO side of the RFI market

    # For sided markets: ticker ends with -TEAMABBR
    # YES = that team wins
    m = market_ticker.rsplit("-", 1)
    if len(m) == 2:
        ticker_team = m[-1].upper()
        # If bet mentions this team → YES bet
        if ticker_team in bet_upper or ticker_team in side_upper:
            return True
        # If bet mentions the other team → NO bet
        return False

    return True


def process_bet_clv(b):
    """
    Process CLV for a single bet. Returns updated bet dict with CLV fields.
    Raises nothing — all errors stored in clvError/clvStatus.
    """
    bet_id = b.get("id", "?")
    market_ticker = b.get("marketTicker")
    series_ticker = b.get("seriesTicker")
    scheduled_start_raw = b.get("scheduledStartTime")
    bet_str = b.get("bet", "")
    side = b.get("betSide") or b.get("side") or ""
    entry_price = b.get("betTimeLine") or b.get("price")
    market_type_raw = (b.get("market") or "")
    market_map = {
        "ML": "ML", "F5 ML": "F5 ML", "F5": "F5 ML",
        "RL": "Run Line", "Run Line": "Run Line",
        "F5 RL": "F5 RL", "Total": "Total", "TT": "Team Total",
        "Team Total": "Team Total", "NRFI": "NRFI", "YRFI": "YRFI",
    }
    market_type = market_map.get(market_type_raw, market_type_raw)

    # Phase 4 — Validation gates
    updated = dict(b)

    if not market_ticker:
        updated["clvStatus"] = "FAIL_NO_TICKER"
        updated["clvError"] = "marketTicker missing — backfill required"
        updated["clv"] = None
        updated["closingPrice"] = None
        updated["closingTimestamp"] = None
        updated["clvSource"] = None
        return updated

    if not scheduled_start_raw:
        updated["clvStatus"] = "FAIL_NO_TIMESTAMP"
        updated["clvError"] = "scheduledStartTime missing"
        updated["clv"] = None
        updated["closingPrice"] = None
        updated["closingTimestamp"] = None
        updated["clvSource"] = None
        return updated

    scheduled_start_ts = parse_ts(scheduled_start_raw)
    if not scheduled_start_ts:
        updated["clvStatus"] = "FAIL_INVALID_TIMESTAMP"
        updated["clvError"] = f"cannot parse scheduledStartTime: {scheduled_start_raw}"
        updated["clv"] = None
        updated["closingPrice"] = None
        updated["closingTimestamp"] = None
        updated["clvSource"] = None
        return updated

    # Attempt candlestick fetch
    try:
        result = get_candlestick_clv(market_ticker, scheduled_start_ts, market_type)
    except ValueError as e:
        # Try market detail fallback for settled markets
        try:
            result = get_market_detail_price(market_ticker, scheduled_start_ts)
        except ValueError as e2:
            updated["clvStatus"] = "FAIL_NO_CANDLE"
            updated["clvError"] = f"candlestick: {e} | detail: {e2}"
            updated["clv"] = None
            updated["closingPrice"] = None
            updated["closingTimestamp"] = None
            updated["clvSource"] = None
            return updated

    closing_yes_prob = result["closingPrice"]

    # Determine YES/NO side
    bet_is_yes = is_yes_bet(bet_str, market_type, market_ticker, side)

    # Calculate CLV
    clv = calculate_clv(entry_price, closing_yes_prob, market_type, bet_is_yes)

    # Build closing line in American odds
    if bet_is_yes:
        closing_american = price_to_american(closing_yes_prob)
    else:
        closing_american = price_to_american(1 - closing_yes_prob)

    updated["closingPrice"] = closing_yes_prob
    updated["closingPriceAmerican"] = closing_american
    updated["closingImpliedPct"] = price_to_implied_pct(closing_yes_prob if bet_is_yes else 1 - closing_yes_prob)
    updated["closingTimestamp"] = result["closingTimestamp"]
    updated["clv"] = clv
    updated["clvStatus"] = "OK"
    updated["clvSource"] = result["clvSource"]
    updated["clvError"] = None
    updated["betIsYes"] = bet_is_yes

    return updated


def run_clv(bets_path=None, write=False, bet_ids=None, settled_only=True):
    """
    Run CLV calculation for all bets (or filtered subset).
    """
    path = bets_path or BETS_PATH
    with open(path) as f:
        bets = json.load(f)

    if bet_ids:
        targets = [b for b in bets if b.get("id") in bet_ids]
    elif settled_only:
        targets = [b for b in bets if b.get("status") in ("SETTLED", "WIN", "LOSS", "PUSH")]
    else:
        targets = bets

    # Only process bets where CLV is supported
    SUPPORTED = {"ML", "F5 ML", "Run Line", "F5 RL", "Total", "Team Total", "NRFI", "YRFI"}
    market_map = {
        "ML": "ML", "F5 ML": "F5 ML", "F5": "F5 ML",
        "RL": "Run Line", "Run Line": "Run Line",
        "Total": "Total", "TT": "Team Total", "Team Total": "Team Total",
        "NRFI": "NRFI", "YRFI": "YRFI",
    }
    targets = [b for b in targets if market_map.get(b.get("market", ""), "") in SUPPORTED]

    print(f"Processing CLV for {len(targets)} bets...")

    results = {}
    ok, fail_ticker, fail_candle, fail_other = 0, 0, 0, 0

    for i, b in enumerate(targets):
        bid = b.get("id", f"bet_{i}")
        print(f"  [{i+1}/{len(targets)}] {bid} {b.get('game')} {b.get('market')} {b.get('bet')}...")
        updated = process_bet_clv(b)
        results[bid] = updated

        status = updated.get("clvStatus", "?")
        if status == "OK":
            ok += 1
            clv = updated.get("clv")
            print(f"    ✓ CLV={clv:+.2f}% closing={updated.get('closingPriceAmerican')}")
        elif "NO_TICKER" in status:
            fail_ticker += 1
            print(f"    ✗ {status}: {updated.get('clvError','')[:60]}")
        elif "NO_CANDLE" in status or "FAIL" in status:
            fail_candle += 1
            print(f"    ✗ {status}: {updated.get('clvError','')[:80]}")
        else:
            fail_other += 1
            print(f"    ? {status}")

        time.sleep(0.3)  # rate limit

    summary = {
        "total_processed": len(targets),
        "clv_ok": ok,
        "fail_no_ticker": fail_ticker,
        "fail_no_candle": fail_candle,
        "fail_other": fail_other,
        "coverage_pct": round(ok / len(targets) * 100, 1) if targets else 0,
    }

    print("\nCLV SUMMARY")
    print("=" * 40)
    for k, v in summary.items():
        print(f"  {k}: {v}")

    if write:
        # Merge results back into bets
        bet_map = {b["id"]: b for b in bets if "id" in b}
        for bid, updated in results.items():
            bet_map[bid] = updated
        updated_bets = [bet_map.get(b.get("id"), b) for b in bets]
        with open(path, "w") as f:
            json.dump(updated_bets, f, indent=2)
        print(f"\nWrote {len(updated_bets)} bets to {path}")

    # Write CLV report
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "results": {bid: {
            "clvStatus": r.get("clvStatus"),
            "clv": r.get("clv"),
            "closingPrice": r.get("closingPrice"),
            "closingTimestamp": r.get("closingTimestamp"),
            "clvSource": r.get("clvSource"),
            "clvError": r.get("clvError"),
        } for bid, r in results.items()},
    }
    report_path = os.path.join(DATA_DIR, "clv_report.json")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"CLV report written to {report_path}")

    return results, summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--all", action="store_true", help="Include unsettled bets")
    parser.add_argument("--ids", nargs="+", help="Specific bet IDs")
    args = parser.parse_args()
    run_clv(write=args.write, bet_ids=args.ids, settled_only=not args.all)
