#!/usr/bin/env python3
"""
scripts/calibrate.py — Canonical calibration and health check script
Last updated: June 6, 2026 — v1.0

Run via bash_tool at session start and after each settled session:
    curl -sf -H "Authorization: token <TOKEN>" \
      https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/scripts/calibrate.py | python3

Outputs:
  - Per-tier win rate, expected WR, calibration ratio, and factor update recommendation
  - Per-signal win rates
  - Per-market CLV averages vs targets
  - Rolling 30 and 100-bet CLV health
  - Multiplier sunset warnings (categories past N>=30 threshold)
  - edgePct consistency check (flags bets where edgePct tier != confidence tier)

DO NOT update calibration factors until N>=50 per tier (MODEL_CORE Section 3).
"""

import json, math, sys, urllib.request, collections
from datetime import datetime

import os
TOKEN = os.environ.get("WORKFLOW_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
if not TOKEN:
    # Fallback: accept token as CLI arg
    if len(sys.argv) > 1:
        TOKEN = sys.argv[1]
    else:
        print("ERROR: No GitHub token found. Set WORKFLOW_TOKEN env var or pass as arg.")
        sys.exit(1)
REPO  = "chmoses98/edge-finder-api"

def fetch_bets():
    url = f"https://raw.githubusercontent.com/{REPO}/main/bets.json"
    req = urllib.request.Request(url, headers={"Authorization": f"token {TOKEN}", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    return data if isinstance(data, list) else data.get("bets", [])

# Current calibration factors and multipliers (update in MODEL_CORE Section 3/4, not here)
CAL = {"High": 0.187, "Medium": 0.255, "Paper": 0.18}
MULTIPLIERS = {
    "K Props": {"n_threshold": 30, "current": 1.50},
    "F5 ML":   {"n_threshold": 30, "current": 1.50},
    "NRFI":    {"n_threshold": 30, "current": 1.00},
    "YRFI":    {"n_threshold": 30, "current": 1.25},
    "Team Total": {"n_threshold": 30, "current": 1.25},
    "ML":      {"n_threshold": 30, "current": 1.00},
    "Run Line": {"n_threshold": 30, "current": None},  # SUSPENDED
    "Game Total": {"n_threshold": 30, "current": None},  # PAPER ONLY
}
CLV_TARGETS = {
    "ML": 1.0, "Run Line": 1.5, "Game Total": 1.0, "Team Total": 1.5,
    "NRFI": 1.5, "YRFI": 1.5, "F5 ML": 1.5, "F5 RL": 1.5,
}
MIN_CLV_SAMPLE = {
    "ML": 30, "Run Line": 20, "Game Total": 20, "Team Total": 15,
    "NRFI": 15, "YRFI": 15, "F5 ML": 20, "F5 RL": 20,
}

def pct(v): return f"{v:.1%}"
def fmt(v, d=2): return f"{v:.{d}f}"

print(f"\n{'='*60}")
print(f"CALIBRATION REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print(f"{'='*60}\n")

try:
    bets = fetch_bets()
except Exception as e:
    print(f"ERROR: Could not fetch bets.json — {e}")
    sys.exit(1)

print(f"Total bets in ledger: {len(bets)}")
settled = [b for b in bets if b.get("result") in ("WIN", "LOSS", "PUSH")]
wins_all = sum(1 for b in settled if b.get("result") == "WIN")
losses_all = sum(1 for b in settled if b.get("result") == "LOSS")
print(f"Settled: {len(settled)} | W={wins_all} L={losses_all} WR={wins_all/(wins_all+losses_all):.1%}\n")

# --- PER-TIER CALIBRATION ---
print("── PER-TIER CALIBRATION ──")
print(f"{'Tier':<10} {'N':>5} {'WR':>7} {'95% CI':>13} {'Exp WR':>8} {'Ratio':>7} {'Factor':>8} {'Update?'}")
for conf in ["High", "Medium", "Paper"]:
    subset = [b for b in settled if b.get("confidence") == conf and b.get("result") in ("WIN","LOSS")]
    if not subset:
        print(f"  {conf:<10} {'0':>5} {'—':>7} {'—':>13} {'—':>8} {'—':>7} {CAL[conf]:>8.3f} {'N/A'}")
        continue
    w = sum(1 for b in subset if b.get("result") == "WIN")
    l = sum(1 for b in subset if b.get("result") == "LOSS")
    n = w + l
    wr = w / n
    se = math.sqrt(wr * (1 - wr) / n)
    ci = 1.96 * se
    mps = [float(b["modelPct"]) / 100 if float(b.get("modelPct", 0)) > 1 else float(b.get("modelPct", 0))
           for b in subset if b.get("modelPct")]
    exp_wr = sum(mps) / len(mps) if mps else 0
    ratio = wr / exp_wr if exp_wr else 0
    new_f = CAL[conf] * ratio
    update = f"→ {new_f:.3f}" if n >= 50 and abs(ratio - 1.0) > 0.05 else ("HOLD (N<50)" if n < 50 else "HOLD (stable)")
    print(f"  {conf:<10} {n:>5} {pct(wr):>7} {f'[{pct(wr-ci)},{pct(wr+ci)}]':>13} {pct(exp_wr):>8} {ratio:>7.3f} {CAL[conf]:>8.3f} {update}")

# --- SIGNAL-TYPE WIN RATES ---
print("\n── SIGNAL WIN RATES ──")
signal_rec = collections.defaultdict(lambda: {"W": 0, "L": 0})
for b in settled:
    factors = b.get("factors", {})
    if isinstance(factors, dict):
        for k in factors:
            if b.get("result") == "WIN": signal_rec[k]["W"] += 1
            elif b.get("result") == "LOSS": signal_rec[k]["L"] += 1
print(f"  {'Signal':<30} {'W':>4} {'L':>4} {'WR':>7} {'Note'}")
for sig, rec in sorted(signal_rec.items(), key=lambda x: -(x[1]["W"] + x[1]["L"])):
    n = rec["W"] + rec["L"]
    if n < 2: continue
    wr = rec["W"] / n
    note = "✅" if wr >= 0.55 else ("⚠️" if wr >= 0.48 else "🚨")
    print(f"  {sig:<30} {rec['W']:>4} {rec['L']:>4} {pct(wr):>7} {note}")

# --- PER-MARKET CLV ---
print("\n── PER-MARKET CLV (vs targets from MODEL_CORE Section 17) ──")
clv_by_market = collections.defaultdict(list)
for b in settled:
    mkt = b.get("market", "Unknown")
    clv = b.get("clv")
    if clv is not None:
        clv_by_market[mkt].append(float(clv))
print(f"  {'Market':<20} {'N':>5} {'Avg CLV':>9} {'Target':>8} {'Status'}")
for mkt in sorted(clv_by_market.keys()):
    clvs = clv_by_market[mkt]
    avg  = sum(clvs) / len(clvs)
    n    = len(clvs)
    tgt  = CLV_TARGETS.get(mkt, 1.0)
    min_n = MIN_CLV_SAMPLE.get(mkt, 15)
    samp = "(below min)" if n < min_n else ""
    status = "✅ HEALTHY" if avg >= tgt else ("⚠️ WARNING" if avg >= 0.5 else "🚨 RED FLAG")
    print(f"  {mkt:<20} {n:>5} {avg:>+9.2f}% {f'≥{tgt}%':>8} {status} {samp}")

# --- ROLLING CLV HEALTH ---
print("\n── ROLLING CLV HEALTH ──")
all_clvs = [float(b["clv"]) for b in settled if b.get("clv") is not None]
if all_clvs:
    for window, label in [(30, "Last 30"), (100, "Last 100")]:
        chunk = all_clvs[-window:] if len(all_clvs) >= window else all_clvs
        avg   = sum(chunk) / len(chunk)
        status = "HEALTHY ✅" if avg >= 1.5 else ("WARNING ⚠️" if avg >= 0.5 else "RED FLAG 🚨")
        n_str  = f"N={len(chunk)}" + (" (partial)" if len(chunk) < window else "")
        print(f"  {label} bets [{n_str}]: avg CLV {avg:+.2f}% — {status}")
    if len(all_clvs) >= 30 and sum(all_clvs[-30:]) / 30 < 0.5:
        print("  ⛔ RED FLAG PROTOCOL ACTIVE: avg 30-bet CLV below +0.5%")
        print("     → Pause new bets pending root-cause review (MODEL_CORE Section 17)")
else:
    print("  No CLV data available.")

# --- MULTIPLIER SUNSET CHECK ---
print("\n── MULTIPLIER SUNSET CHECK ──")
market_counts = collections.Counter(b.get("market","?") for b in settled)
for mkt, info in MULTIPLIERS.items():
    n = market_counts.get(mkt, 0)
    threshold = info["n_threshold"]
    current   = info["current"]
    if current is None:
        print(f"  {mkt:<20} N={n:>3} — SUSPENDED (manual review required)")
    elif n >= threshold:
        print(f"  {mkt:<20} N={n:>3} ≥ {threshold} — ⚠️  SUNSET THRESHOLD MET: freeze multiplier ({current}x) and recalibrate at N≥50")
    else:
        print(f"  {mkt:<20} N={n:>3} / {threshold} — active {current}x ({threshold-n} bets to sunset check)")

# --- edgePct CONSISTENCY CHECK ---
print("\n── edgePct CONSISTENCY CHECK ──")
mismatches = []
for b in bets:
    ep   = b.get("edgePct")
    conf = b.get("confidence","")
    if ep is None: continue
    ep = float(ep)
    if conf == "High"   and ep < 3.0:  mismatches.append((b["id"], conf, ep, "edgePct below High threshold"))
    if conf == "Medium" and ep >= 3.0: mismatches.append((b["id"], conf, ep, "edgePct at High tier but confidence=Medium"))
    if conf == "Paper"  and ep >= 1.5: mismatches.append((b["id"], conf, ep, "edgePct at Medium+ tier but confidence=Paper"))
print(f"  Bets with edgePct/confidence mismatch: {len(mismatches)}")
print(f"  NOTE: Calibration uses confidence field (correct). Mismatches are display/audit issues only.")
print(f"  Resolution: Option B (leave as-is) — see C11 decision in change plan.")
if mismatches[:3]:
    print(f"  Sample mismatches:")
    for bid, conf, ep, reason in mismatches[:3]:
        print(f"    {bid}: confidence={conf}, edgePct={ep:.2f} — {reason}")

print(f"\n{'='*60}\n")
