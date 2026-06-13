#!/usr/bin/env python3
"""
scripts/run_kalshi_clv_step.py  (v2 — snapshot-first CLV)
==========================================================
Workflow helper: resolve Kalshi CLV for all real-money bets on a given date.

Replaces the previous version that exclusively called the live Kalshi
candlestick API (which returns 403 in the post-game review window).

Fallback order:
  1. clv_from_snapshot.run_snapshot_clv()  — reads archived kalshi_search
     snapshots from data/kalshi_registry_snapshots/.  No API calls.
  2. fetch_kalshi_clv_v2.run_clv()         — live Kalshi candlestick API.
     Only attempted for any bets that clv_from_snapshot could not resolve
     (FAIL_NO_SNAPSHOT_PRICE) AND where the candlestick API is likely to
     have historical data (typically available 24–48 h post-game).
     Failures here are expected and non-fatal.

Called by clv-update.yml Step 2.
Takes DATE as argv[1] or DATE env var.
"""
import sys
import json
import os

# Add scripts dir to path (when called from repo root)
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)

import clv_from_snapshot as snap_clv
import fetch_kalshi_clv_v2 as api_clv

date = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATE", "")
if not date:
    print("ERROR: DATE argument required (argv[1] or DATE env var)")
    sys.exit(1)

BETS_PATH = os.path.join(os.path.dirname(_here), "bets.json")

# ── Step 1: Snapshot-based CLV (primary, no API calls) ────────────────────────
print(f"\n=== Step 1: Snapshot CLV for {date} ===")
snap_results, snap_summary = snap_clv.run_snapshot_clv(
    date_str=date,
    bets_path=BETS_PATH,
    write=True,
)
print("Snapshot CLV summary:", json.dumps(snap_summary))

# ── Step 2: API CLV for any remaining unresolved bets (best-effort) ───────────
# Re-read bets.json to see what's still unresolved after Step 1
with open(BETS_PATH) as f:
    bets_after_step1 = json.load(f)

UNRESOLVED_STATUSES = {
    "FAIL_NO_SNAPSHOT_PRICE", "FAIL_NO_CANDLE", "unavailable",
    "not_yet_captured", "FAIL_NO_TICKER", "FAIL_NO_TIMESTAMP",
}
SETTLED = {"settled", "SETTLED", "WIN", "LOSS", "win", "loss", "PUSH", "push"}

api_targets = [
    b for b in bets_after_step1
    if (b.get("date") or "")[:10] == date
    and (b.get("betType", "").upper() == "REAL"
         or b.get("type", "").lower() == "real")
    and b.get("status") in SETTLED
    and b.get("marketTicker")
    and b.get("clvStatus") in UNRESOLVED_STATUSES
]

if api_targets:
    print(f"\n=== Step 2: API CLV for {len(api_targets)} unresolved bets ===")
    ids = [b["id"] for b in api_targets if b.get("id")]
    try:
        api_results, api_summary = api_clv.run_clv(
            bets_path=BETS_PATH, write=True, bet_ids=ids
        )
        print("API CLV summary:", json.dumps(api_summary))
    except Exception as e:
        print(f"API CLV failed (non-fatal): {e}")
        api_summary = {"error": str(e)}
else:
    print("\n=== Step 2: No unresolved bets after snapshot CLV — skipping API ===")
    api_summary = {"skipped": True, "reason": "all_resolved_by_snapshot"}

# ── Final report ──────────────────────────────────────────────────────────────
with open(BETS_PATH) as f:
    final_bets = json.load(f)

june12_real = [
    b for b in final_bets
    if (b.get("date") or "")[:10] == date
    and (b.get("betType", "").upper() == "REAL"
         or b.get("type", "").lower() == "real")
]

ok       = sum(1 for b in june12_real if b.get("clvStatus") == "OK")
unavail  = sum(1 for b in june12_real if b.get("clvStatus") not in ("OK", None))
total    = len(june12_real)
clv_vals = [b["clv"] for b in june12_real if b.get("clv") is not None]
avg_clv  = round(sum(clv_vals) / len(clv_vals), 2) if clv_vals else None

print(f"""
╔══════════════════════════════════════════════╗
║  CLV RESOLUTION COMPLETE — {date}       ║
╠══════════════════════════════════════════════╣
║  Real bets:    {total:3d}                           ║
║  CLV OK:       {ok:3d}  ({round(ok/total*100) if total else 0}% coverage)               ║
║  CLV unavail:  {unavail:3d}                           ║
║  Avg CLV:      {avg_clv}pp                        ║
╚══════════════════════════════════════════════╝
""")

sys.exit(0 if ok == total else 0)  # non-zero only if fatal error; partial CLV is OK
