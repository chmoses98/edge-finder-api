#!/usr/bin/env python3
"""
PHASE 7 & 8 — RULE 71 TRACKING & DOWNGRADE SYSTEM

Converts Rule 71 from a hard block into a tracking/downgrade system.

New behavior:
  - Rule 71 fires → still evaluate the market
  - Add rule71Flag: true
  - Add rule71Reason (what caused the flag)
  - Add marketGap (model% − Pinnacle VF%)
  - Add modelProbability
  - Add marketProbability
  - Downgrade confidence by one tier instead of blocking automatically

Hard block only when:
  - clear data error (impossible projection output)
  - missing key data (no pitcher, no odds)
  - stale odds (odds >8hr old)
  - invalid projection input
  - impossible projection output (win prob < 0 or > 1)

Phase 8: Rule 71 Reporting
  - total Rule 71 flags
  - bets allowed despite Rule 71
  - bets downgraded by Rule 71
  - bets hard-blocked by Rule 71
  - ROI of Rule 71-flagged bets
  - CLV of Rule 71-flagged bets
  - ROI of non-Rule-71 bets
  - CLV of non-Rule-71 bets
"""
import json, os
from datetime import datetime, timezone

BETS_PATH = os.path.join(os.path.dirname(__file__), "..", "bets.json")

# Rule 71 applies to these markets (per RULES.md)
RULE71_MARKETS = {"ML", "F5 ML", "Run Line", "F5 RL"}
RULE71_EXEMPT = {"Total", "Team Total", "NRFI", "YRFI", "F5 Total", "F5 RL"}
# NOTE: F5 RL is in both per original rules — exempt from R71 per RULES.md line 190
RULE71_MARKETS = {"ML", "F5 ML", "Run Line"}

# Gap threshold: >8% triggers flag
RULE71_GAP_THRESHOLD = 8.0

# Confidence tier ladder for downgrade
CONFIDENCE_TIERS = ["High", "Medium", "Paper", "Skip"]

HARD_BLOCK_REASONS = {
    "IMPOSSIBLE_PROBABILITY",  # model prob < 0 or > 100
    "MISSING_KEY_DATA",        # no pitcher, no run projection
    "STALE_ODDS",              # odds > 8hr old
    "INVALID_PROJECTION",      # NaN, infinite, or negative run projection
    "MISSING_PITCHER",         # no starter confirmed and not flagged
}


def downgrade_confidence(current_confidence):
    """Lower confidence by one tier."""
    tier_map = {"High": 0, "Medium": 1, "Paper": 2, "Skip": 3}
    current_idx = tier_map.get(current_confidence, 2)
    next_idx = min(current_idx + 1, len(CONFIDENCE_TIERS) - 1)
    return CONFIDENCE_TIERS[next_idx]


def evaluate_rule71(
    market_type,
    model_prob_pct,
    pinnacle_vf_pct,
    kalshi_pct=None,
    current_confidence="High",
    additional_context=None,
):
    """
    Evaluate whether Rule 71 should fire for a given bet.

    Returns:
        {
            "fires": bool,
            "hardBlock": bool,
            "rule71Flag": bool,
            "rule71Reason": str or None,
            "marketGap": float or None,
            "modelProbability": float,
            "marketProbability": float,
            "originalConfidence": str,
            "adjustedConfidence": str,
            "action": "ALLOW" | "DOWNGRADE" | "HARD_BLOCK",
        }
    """
    ctx = additional_context or {}
    result = {
        "fires": False,
        "hardBlock": False,
        "rule71Flag": False,
        "rule71Reason": None,
        "marketGap": None,
        "modelProbability": model_prob_pct,
        "marketProbability": pinnacle_vf_pct,
        "originalConfidence": current_confidence,
        "adjustedConfidence": current_confidence,
        "action": "ALLOW",
    }

    # Rule 71 only applies to specific markets
    market_canonical = {
        "ML": "ML", "MONEYLINE": "ML",
        "F5 ML": "F5 ML", "F5": "F5 ML",
        "Run Line": "Run Line", "RL": "Run Line",
    }.get(market_type, market_type)

    if market_canonical not in RULE71_MARKETS:
        return result  # exempt market

    # Check for hard-block conditions first (data errors, not market disagreement)
    hard_block_reason = ctx.get("hardBlockReason")
    if hard_block_reason in HARD_BLOCK_REASONS:
        result["fires"] = True
        result["hardBlock"] = True
        result["rule71Flag"] = True
        result["rule71Reason"] = hard_block_reason
        result["adjustedConfidence"] = "Skip"
        result["action"] = "HARD_BLOCK"
        return result

    if model_prob_pct is None or pinnacle_vf_pct is None:
        return result  # can't evaluate without both probs

    gap = round(model_prob_pct - pinnacle_vf_pct, 2)
    result["marketGap"] = gap
    abs_gap = abs(gap)

    if abs_gap <= RULE71_GAP_THRESHOLD:
        return result  # gap within tolerance — no flag

    # Rule 71 fires
    result["fires"] = True
    result["rule71Flag"] = True

    # Determine reason
    if kalshi_pct is not None:
        kalshi_gap = abs(model_prob_pct - kalshi_pct)
        if kalshi_gap > RULE71_GAP_THRESHOLD:
            # Both Pinnacle AND Kalshi disagree with model — strongest signal
            result["rule71Reason"] = (
                f"MODEL_DIVERGES_FROM_BOTH_MARKETS: "
                f"model={model_prob_pct:.1f}% vs Pinnacle={pinnacle_vf_pct:.1f}% (gap={gap:+.1f}%) "
                f"AND vs Kalshi={kalshi_pct:.1f}% (gap={model_prob_pct-kalshi_pct:+.1f}%)"
            )
        else:
            # Pinnacle disagrees but Kalshi agrees with model → Kalshi inefficiency
            # Per RULES.md Rule 71: this is a Kalshi inefficiency, NOT a model error
            result["rule71Flag"] = False
            result["fires"] = False
            result["rule71Reason"] = (
                f"KALSHI_INEFFICIENCY: Pinnacle={pinnacle_vf_pct:.1f}% disagrees "
                f"but Kalshi={kalshi_pct:.1f}% confirms model={model_prob_pct:.1f}% — NOT a Rule 71 block"
            )
            result["action"] = "ALLOW"
            return result
    else:
        # No Kalshi data — Pinnacle-only gap
        result["rule71Reason"] = (
            f"PINNACLE_DIVERGENCE_NO_KALSHI: model={model_prob_pct:.1f}% "
            f"vs Pinnacle={pinnacle_vf_pct:.1f}% (gap={gap:+.1f}%) — Kalshi unavailable"
        )

    # Downgrade confidence by one tier (not hard block)
    original = current_confidence
    adjusted = downgrade_confidence(current_confidence)
    result["originalConfidence"] = original
    result["adjustedConfidence"] = adjusted
    result["action"] = "DOWNGRADE"

    return result


# ── Phase 8: Rule 71 Reporting ────────────────────────────────────────────────

def generate_rule71_report(bets_path=None):
    """
    Generate Rule 71 tracking report across all bets.

    Returns report dict with:
      - total_flags
      - bets_allowed_despite_flag
      - bets_downgraded
      - bets_hard_blocked
      - roi_flagged_bets
      - clv_flagged_bets
      - roi_non_flagged_bets
      - clv_non_flagged_bets
    """
    path = bets_path or BETS_PATH
    with open(path) as f:
        bets = json.load(f)

    flagged = []
    non_flagged = []

    for b in bets:
        gates = b.get("gatesFired", [])
        is_flagged = (
            b.get("rule71Flag") is True
            or any("R71" in str(g) or "Rule71" in str(g) or "rule71" in str(g) for g in gates)
        )
        if is_flagged:
            flagged.append(b)
        else:
            non_flagged.append(b)

    def calc_roi(bet_list):
        settled = [b for b in bet_list
                   if b.get("result") in ("WIN", "LOSS", "PUSH")
                   and b.get("pl") is not None
                   and (b.get("size") or b.get("betSize"))]
        if not settled:
            return None
        total_wagered = sum(float(b.get("size") or b.get("betSize") or 0) for b in settled)
        total_pl = sum(float(b.get("pl") or 0) for b in settled)
        if total_wagered == 0:
            return None
        return round(total_pl / total_wagered * 100, 2)

    def calc_avg_clv(bet_list):
        clv_bets = [b for b in bet_list if b.get("clv") is not None]
        if not clv_bets:
            return None
        return round(sum(float(b["clv"]) for b in clv_bets) / len(clv_bets), 3)

    def count_by_action(bet_list):
        """Count bets by Rule 71 action stored in gatesFired."""
        allowed = sum(1 for b in bet_list if any("suspended" in str(g) for g in b.get("gatesFired", [])))
        # Downgraded: original confidence would have been higher
        # We check if confidence != "High" on a bet that fired R71 but was placed
        downgraded = sum(1 for b in bet_list
                        if b.get("rule71Action") == "DOWNGRADE"
                        or b.get("rule71Downgraded"))
        hard_blocked = sum(1 for b in bet_list
                          if b.get("rule71Action") == "HARD_BLOCK"
                          or any("blocked" in str(g).lower() for g in b.get("gatesFired", [])))
        return allowed, downgraded, hard_blocked

    f_allowed, f_downgraded, f_hard_blocked = count_by_action(flagged)

    flagged_settled = [b for b in flagged if b.get("result") in ("WIN", "LOSS", "PUSH")]
    non_flagged_settled = [b for b in non_flagged if b.get("result") in ("WIN", "LOSS", "PUSH")]

    # Win rates
    def win_rate(bet_list):
        settled = [b for b in bet_list if b.get("result") in ("WIN", "LOSS")]
        if not settled:
            return None
        wins = sum(1 for b in settled if b.get("result") == "WIN")
        return round(wins / len(settled) * 100, 1)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rule71_summary": {
            "total_bets": len(bets),
            "total_flags": len(flagged),
            "non_flagged": len(non_flagged),
            "bets_allowed_despite_flag": f_allowed,
            "bets_downgraded_by_rule71": f_downgraded,
            "bets_hard_blocked_by_rule71": f_hard_blocked,
        },
        "rule71_flagged_performance": {
            "total": len(flagged),
            "settled": len(flagged_settled),
            "win_rate_pct": win_rate(flagged_settled),
            "roi_pct": calc_roi(flagged),
            "avg_clv": calc_avg_clv(flagged),
            "total_pl": round(sum(float(b.get("pl") or 0) for b in flagged), 2),
        },
        "non_rule71_performance": {
            "total": len(non_flagged),
            "settled": len(non_flagged_settled),
            "win_rate_pct": win_rate(non_flagged_settled),
            "roi_pct": calc_roi(non_flagged),
            "avg_clv": calc_avg_clv(non_flagged),
            "total_pl": round(sum(float(b.get("pl") or 0) for b in non_flagged), 2),
        },
        "recommendation": _generate_recommendation(flagged, non_flagged),
        "sample_flagged_bets": [
            {
                "id": b.get("id"),
                "game": b.get("game"),
                "market": b.get("market"),
                "bet": b.get("bet"),
                "result": b.get("result"),
                "clv": b.get("clv"),
                "pl": b.get("pl"),
                "gatesFired": b.get("gatesFired", []),
                "rule71Action": b.get("rule71Action"),
            }
            for b in flagged[:20]
        ],
    }

    return report


def _generate_recommendation(flagged, non_flagged):
    """
    Generate data-driven recommendation. Never recommends deleting Rule 71
    until actual CLV and ROI data prove it is hurting performance.
    """
    flagged_clv = [b for b in flagged if b.get("clv") is not None]
    non_flagged_clv = [b for b in non_flagged if b.get("clv") is not None]

    if len(flagged_clv) < 10:
        return (
            f"INSUFFICIENT_DATA: Only {len(flagged_clv)} Rule 71-flagged bets have CLV data. "
            "Need ≥10 bets with valid CLV before any Rule 71 modification is warranted. "
            "Continue data accumulation."
        )

    avg_f_clv = sum(float(b["clv"]) for b in flagged_clv) / len(flagged_clv)
    avg_nf_clv = sum(float(b["clv"]) for b in non_flagged_clv) / len(non_flagged_clv) if non_flagged_clv else 0

    settled_flagged = [b for b in flagged if b.get("result") in ("WIN", "LOSS", "PUSH")]
    settled_non = [b for b in non_flagged if b.get("result") in ("WIN", "LOSS", "PUSH")]

    if not settled_flagged:
        return "INSUFFICIENT_SETTLEMENT_DATA: No Rule 71-flagged bets settled yet."

    roi_f = (sum(float(b.get("pl") or 0) for b in settled_flagged) /
             sum(float(b.get("size") or b.get("betSize") or 0) for b in settled_flagged) * 100
             if settled_flagged else None)

    roi_nf = (sum(float(b.get("pl") or 0) for b in settled_non) /
              sum(float(b.get("size") or b.get("betSize") or 0) for b in settled_non) * 100
              if settled_non else None)

    if roi_f is not None and roi_nf is not None:
        if roi_f > 0 and avg_f_clv > 0:
            return (
                f"RULE71_HELPING: Flagged bets ROI={roi_f:.1f}% CLV={avg_f_clv:.2f}% — "
                "Rule 71 downgrade still appropriate; flagged bets are profitable despite flags."
            )
        elif roi_f < roi_nf and avg_f_clv < avg_nf_clv:
            return (
                f"RULE71_WORKING_AS_INTENDED: Flagged ROI={roi_f:.1f}% < Non-flagged ROI={roi_nf:.1f}%; "
                f"Flagged CLV={avg_f_clv:.2f}% < Non-flagged CLV={avg_nf_clv:.2f}%. "
                "Rule 71 correctly identifying lower-value bets. Maintain current threshold."
            )
        else:
            return (
                f"REVIEW_THRESHOLD: Flagged ROI={roi_f:.1f}% Non-flagged={roi_nf:.1f}%. "
                f"Flagged CLV={avg_f_clv:.2f}% Non-flagged={avg_nf_clv:.2f}%. "
                "Accumulate more data before modifying Rule 71 threshold."
            )

    return "ACCUMULATING_DATA: Continue logging and settling bets."


def print_rule71_report(report):
    """Print formatted Rule 71 report to stdout."""
    print("\n" + "=" * 60)
    print("RULE 71 TRACKING REPORT")
    print("=" * 60)
    s = report["rule71_summary"]
    print(f"Total bets:                  {s['total_bets']}")
    print(f"Rule 71 flags:               {s['total_flags']}")
    print(f"Non-flagged bets:            {s['non_flagged']}")
    print(f"Allowed despite flag:        {s['bets_allowed_despite_flag']}")
    print(f"Downgraded by Rule 71:       {s['bets_downgraded_by_rule71']}")
    print(f"Hard-blocked by Rule 71:     {s['bets_hard_blocked_by_rule71']}")

    print("\n--- RULE 71 FLAGGED PERFORMANCE ---")
    fp = report["rule71_flagged_performance"]
    print(f"  Total:     {fp['total']} | Settled: {fp['settled']}")
    print(f"  Win rate:  {fp['win_rate_pct']}%")
    print(f"  ROI:       {fp['roi_pct']}%")
    print(f"  Avg CLV:   {fp['avg_clv']}")
    print(f"  Total P/L: ${fp['total_pl']:+.2f}")

    print("\n--- NON-RULE-71 PERFORMANCE ---")
    nf = report["non_rule71_performance"]
    print(f"  Total:     {nf['total']} | Settled: {nf['settled']}")
    print(f"  Win rate:  {nf['win_rate_pct']}%")
    print(f"  ROI:       {nf['roi_pct']}%")
    print(f"  Avg CLV:   {nf['avg_clv']}")
    print(f"  Total P/L: ${nf['total_pl']:+.2f}")

    print(f"\nRECOMMENDATION: {report['recommendation']}")
    print("=" * 60)


if __name__ == "__main__":
    report = generate_rule71_report()
    print_rule71_report(report)

    # Write report
    out_path = os.path.join(os.path.dirname(__file__), "..", "data", "rule71_report.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {out_path}")
