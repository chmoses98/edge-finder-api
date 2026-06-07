#!/usr/bin/env python3
"""Fetch Kalshi closing prices using market detail + trade history approach."""
import json, urllib.request, os, time
from datetime import datetime, timezone, timedelta

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

def kget(url):
    try:
        req = urllib.request.Request(url, headers={"Accept":"application/json","User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read()), None
    except Exception as e:
        return None, str(e)

os.makedirs("data", exist_ok=True)
results = {}

# Confirmed tickers from kalshi_search.json (snapshot at 00:37 UTC 6/7 - pre-game)
# These were ACTIVE (not yet settled) at snapshot time, so prices are pre-game
confirmed = {
    "2026-06-06-020": "KXMLBF5-26JUN061935CLETEX-TEX",
    "2026-06-06-021": "KXMLBRFI-26JUN061935CLETEX",
    "2026-06-06-023": "KXMLBF5-26JUN062110MILCOL-COL",
    "2026-06-06-024": "KXMLBRFI-26JUN062110MILCOL",
    "2026-06-06-025": "KXMLBGAME-26JUN062210LAALAD-LAA",
    "2026-06-06-026": "KXMLBF5-26JUN062210LAALAD-LAA",
    "2026-06-06-027": "KXMLBRFI-26JUN062210LAALAD",
    "2026-06-06-028": "KXMLBGAME-26JUN062210NYMSD-NYM",
    "2026-06-06-029": "KXMLBF5-26JUN062210NYMSD-NYM",
    "2026-06-06-030": "KXMLBRFI-26JUN062210NYMSD",
}

# For confirmed tickers: use trade history endpoint to find last pre-game trade
# GET /markets/{ticker}/trades?limit=100 - returns recent trades with timestamps
print("=== Fetching trade history for confirmed tickers ===")
for bet_id, ticker in confirmed.items():
    data, err = kget(f"{KALSHI_BASE}/markets/{ticker}/trades?limit=50")
    if data:
        trades = data.get("trades", [])
        print(f"{bet_id}: {len(trades)} trades")
        if trades:
            # Find last trade before first pitch
            # Game times (ET->UTC): 1935ET=2335UTC, 2110ET=0110UTC+1, 2210ET=0210UTC+1
            # Approximate first pitch UTC for each game
            first_pitch = {
                "20": 1749248100, "21": 1749248100,  # CLE@TEX 23:35 UTC
                "23": 1749254400, "24": 1749254400,  # MIL@COL 01:10 UTC
                "25": 1749261000, "26": 1749261000, "27": 1749261000,  # LAA@LAD 02:10 UTC
                "28": 1749261000, "29": 1749261000, "30": 1749261000,  # NYM@SD 02:10 UTC
            }
            game_num = bet_id[-3:].lstrip("0") or "0"
            fp_ts = first_pitch.get(game_num, 0)
            pre_game = [t for t in trades if int(t.get("created_time","0")[:10].replace("-","") or 0) < fp_ts
                       or True]  # grab all, filter below by timestamp
            sample = trades[0] if trades else {}
            print(f"  Sample trade keys: {list(sample.keys())}")
            print(f"  Sample trade: {json.dumps(sample)[:200]}")
        results[bet_id] = {"ticker": ticker, "trades_count": len(trades), 
                          "sample": trades[0] if trades else None}
    else:
        print(f"{bet_id}: ERROR - {err}")
        results[bet_id] = {"ticker": ticker, "error": err}
    time.sleep(0.2)

# Also try fetching a known early-game ticker via market endpoint to verify the ticker format
print("\n=== Verifying early game ticker format ===")
test_tickers = [
    "KXMLBGAME-26JUN062210NYMSD-NYM",  # confirmed
    "KXMLBGAME-26JUN061410KCMIN-MIN",  # constructed
    "KXMLBGAME-26JUN061410KCMIN-KC",   # try KC side  
    "KXMLBGAME-26JUN061410MINKCR-MIN", # try different order
]
for t in test_tickers:
    data, err = kget(f"{KALSHI_BASE}/markets/{t}")
    if data and "market" in data:
        m = data["market"]
        print(f"✅ {t}: status={m.get('status')} result={m.get('result','?')}")
        results[f"verify_{t}"] = {"found": True, "status": m.get("status"), "result": m.get("result"), "close_time": m.get("close_time")}
    else:
        print(f"❌ {t}: {err}")
        results[f"verify_{t}"] = {"found": False, "error": err}

with open("data/kalshi_clv_20260606.json", "w") as f:
    json.dump(results, f, indent=2)
print("\nDone.")
