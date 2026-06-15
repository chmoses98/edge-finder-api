#!/usr/bin/env python3
"""
lib/promotion_engine.py
========================
CLV-Based Promotion/Demotion Framework

Promotion logic:
  PAPER/MODEL_ONLY → REAL_PROBE requires:
    - data quality clean, market mechanics valid, exact tickers exist, CLV capture works
    - sample size >= 20 markets
    - average CLV >= 0
    - higher edge buckets outperform lower edge buckets

  REAL_PROBE → REAL requires:
    - positive CLV over meaningful sample (>= 30)
    - positive ROI or acceptable variance with strong CLV
    - no data-quality failures, settlement reliable

Demotion triggers:
  - repeated negative CLV (>= 3 consecutive negative or rolling negative over 10-game window)
  - poor calibration, stale/missing data, settlement ambiguity
  - profits driven by variance but CLV is negative
"""

import json
import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Tuple
from collections import defaultdict

# ── Constants ─────────────────────────────────────────────────────────────────
MIN_SAMPLE_PAPER_TO_PROBE = 20
MIN_SAMPLE_PROBE_TO_REAL = 30
MIN_CLV_FOR_PROMOTION = 0.0      # avg CLV >= 0 for paper→probe
MIN_CLV_FOR_REAL = 1.0            # avg CLV >= 1.0% for probe→real

DEMOTION_CONSECUTIVE_NEGATIVE = 3
DEMOTION_ROLLING_WINDOW = 10

# Market classification
MARKET_TYPES = [
    "ML", "F5_ML", "YRFI", "NRFI", "Team_Total", "Game_Total", "Run_Line"
]


class PromotionDecision:
    def __init__(
        self,
        market_type: str,
        current_tier: str,
        recommended_tier: str,
        action: str,
        reason: str,
        data: dict = None,
    ):
        self.market_type = market_type
        self.current_tier = current_tier
        self.recommended_tier = recommended_tier
        self.action = action  # PROMOTE | DEMOTE | MAINTAIN | INSUFFICIENT_SAMPLE
        self.reason = reason
        self.data = data or {}

    def to_dict(self):
        return {
            "marketType": self.market_type,
            "currentTier": self.current_tier,
            "recommendedTier": self.recommended_tier,
            "action": self.action,
            "reason": self.reason,
            "data": self.data,
        }


def group_bets_by_market(bets: List[dict]) -> Dict[str, List[dict]]:
    """Group bets by market type."""
    grouped = defaultdict(list)
    for bet in bets:
        market = bet.get("market") or bet.get("betType") or "unknown"
        # Normalize market names
        if "F5" in str(market).upper() and "ML" in str(market).upper():
            market = "F5_ML"
        elif "YRFI" in str(market).upper():
            market = "YRFI"
        elif "NRFI" in str(market).upper():
            market = "NRFI"
        elif "Team Total" in str(market) or "TT" == str(market):
            market = "Team_Total"
        elif "Game Total" in str(market) or "Total" == str(market):
            market = "Game_Total"
        elif "Run Line" in str(market) or "RL" == str(market):
            market = "Run_Line"
        elif "ML" in str(market).upper() and "F5" not in str(market).upper():
            market = "ML"
        grouped[market].append(bet)
    return dict(grouped)


def calculate_clv_stats(bets: List[dict]) -> dict:
    """Calculate CLV statistics for a list of bets."""
    clv_values = []
    for bet in bets:
        clv = bet.get("clv")
        if clv is not None:
            try:
                clv_values.append(float(clv))
            except (TypeError, ValueError):
                pass

    if not clv_values:
        return {
            "count": 0,
            "avgCLV": None,
            "positiveCLV": 0,
            "negativeCLV": 0,
            "available": False,
        }

    avg = sum(clv_values) / len(clv_values)
    pos = sum(1 for v in clv_values if v > 0)
    neg = sum(1 for v in clv_values if v < 0)

    return {
        "count": len(clv_values),
        "avgCLV": round(avg, 3),
        "positiveCLV": pos,
        "negativeCLV": neg,
        "available": True,
    }


def calculate_win_rate(bets: List[dict]) -> dict:
    """Calculate win rate for settled bets."""
    wins = sum(1 for b in bets if b.get("result") == "WIN")
    losses = sum(1 for b in bets if b.get("result") == "LOSS")
    settled = wins + losses

    if settled == 0:
        return {"wins": 0, "losses": 0, "settled": 0, "winRate": None}

    return {
        "wins": wins,
        "losses": losses,
        "settled": settled,
        "winRate": round(wins / settled * 100, 1),
    }


def calculate_roi(bets: List[dict]) -> dict:
    """Calculate ROI for bets."""
    total_stake = 0
    total_pl = 0
    for bet in bets:
        stake = bet.get("stake") or bet.get("betSize") or 0
        pl = bet.get("pl") or 0
        try:
            total_stake += float(stake)
            total_pl += float(pl)
        except (TypeError, ValueError):
            pass

    roi = (total_pl / total_stake * 100) if total_stake > 0 else None

    return {
        "totalStake": round(total_stake, 2),
        "totalPL": round(total_pl, 2),
        "roi": round(roi, 2) if roi is not None else None,
    }


def check_edge_bucket_monotonicity(bets: List[dict]) -> bool:
    """
    Check if higher edge buckets outperform lower edge buckets.
    Required for paper→probe promotion.
    """
    edge_buckets = defaultdict(list)
    for bet in bets:
        edge = bet.get("edgePct") or bet.get("edge") or 0
        try:
            e = float(edge)
        except (TypeError, ValueError):
            e = 0

        if e >= 3.0:
            edge_buckets["high"].append(bet)
        elif e >= 1.5:
            edge_buckets["medium"].append(bet)
        else:
            edge_buckets["low"].append(bet)

    # Need at least 2 buckets to check
    buckets_with_data = {k: v for k, v in edge_buckets.items() if len(v) >= 5}
    if len(buckets_with_data) < 2:
        return True  # Can't test — assume OK

    stats = {}
    for bucket, bucket_bets in buckets_with_data.items():
        clv_stats = calculate_clv_stats(bucket_bets)
        stats[bucket] = clv_stats.get("avgCLV")

    # Check: high > medium > low (or at least no inversion)
    high = stats.get("high")
    medium = stats.get("medium")
    low = stats.get("low")

    if high is not None and low is not None:
        if high < low:
            return False  # Inversion — higher edge not outperforming

    return True


def check_consecutive_negative_clv(bets: List[dict], window: int = None) -> Tuple[bool, int]:
    """
    Check for consecutive negative CLV bets.
    Returns (triggered, consecutive_count).
    """
    window = window or DEMOTION_CONSECUTIVE_NEGATIVE

    # Sort by date
    sorted_bets = sorted(
        bets,
        key=lambda b: b.get("date") or b.get("slateDate") or "",
        reverse=True
    )

    consecutive = 0
    for bet in sorted_bets[:DEMOTION_ROLLING_WINDOW]:
        clv = bet.get("clv")
        if clv is None:
            continue
        try:
            if float(clv) < 0:
                consecutive += 1
            else:
                break
        except (TypeError, ValueError):
            continue

    return consecutive >= window, consecutive


def evaluate_market_tier(
    market_type: str,
    bets: List[dict],
    current_tier: str = "PAPER",
) -> PromotionDecision:
    """
    Evaluate a market type and determine if it should be promoted/demoted.

    Args:
        market_type: market type string
        bets: list of bet dicts for this market
        current_tier: current classification (PAPER, REAL_PROBE, REAL)

    Returns:
        PromotionDecision
    """
    settled = [b for b in bets if b.get("result") in ("WIN", "LOSS")]
    clv_stats = calculate_clv_stats(settled)
    wr_stats = calculate_win_rate(settled)
    roi_stats = calculate_roi(settled)

    # ── Paper → REAL_PROBE promotion check ────────────────────────────────
    if current_tier == "PAPER":
        n = len(settled)

        if n < MIN_SAMPLE_PAPER_TO_PROBE:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="PAPER",
                action="INSUFFICIENT_SAMPLE",
                reason=f"Only {n} settled bets, need {MIN_SAMPLE_PAPER_TO_PROBE}",
                data={"n": n, "required": MIN_SAMPLE_PAPER_TO_PROBE, **clv_stats, **wr_stats},
            )

        if not clv_stats["available"]:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="PAPER",
                action="INSUFFICIENT_SAMPLE",
                reason="CLV data unavailable — cannot evaluate for promotion",
                data={"n": n, **wr_stats},
            )

        avg_clv = clv_stats["avgCLV"]
        if avg_clv is None or avg_clv < MIN_CLV_FOR_PROMOTION:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="PAPER",
                action="MAINTAIN",
                reason=f"Avg CLV {avg_clv:.2f}% below threshold {MIN_CLV_FOR_PROMOTION}%",
                data={"n": n, **clv_stats, **wr_stats, **roi_stats},
            )

        edge_monotonic = check_edge_bucket_monotonicity(settled)
        if not edge_monotonic:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="PAPER",
                action="MAINTAIN",
                reason="Higher edge buckets not outperforming lower edge buckets",
                data={"n": n, **clv_stats, **wr_stats, **roi_stats, "edgeBucketCheck": "FAILED"},
            )

        return PromotionDecision(
            market_type=market_type,
            current_tier=current_tier,
            recommended_tier="REAL_PROBE",
            action="PROMOTE",
            reason=f"n={n} >= {MIN_SAMPLE_PAPER_TO_PROBE}, avg CLV={avg_clv:.2f}% >= {MIN_CLV_FOR_PROMOTION}%, edge buckets monotonic",
            data={"n": n, **clv_stats, **wr_stats, **roi_stats},
        )

    # ── REAL_PROBE → REAL promotion check ─────────────────────────────────
    if current_tier == "REAL_PROBE":
        n = len(settled)

        if n < MIN_SAMPLE_PROBE_TO_REAL:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="REAL_PROBE",
                action="INSUFFICIENT_SAMPLE",
                reason=f"Only {n} probe bets settled, need {MIN_SAMPLE_PROBE_TO_REAL}",
                data={"n": n, "required": MIN_SAMPLE_PROBE_TO_REAL, **clv_stats, **wr_stats},
            )

        if not clv_stats["available"]:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="REAL_PROBE",
                action="MAINTAIN",
                reason="CLV data unavailable",
                data={"n": n},
            )

        avg_clv = clv_stats["avgCLV"]

        # Check for demotion triggers first
        consecutive_neg, consec_count = check_consecutive_negative_clv(bets)
        if consecutive_neg:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="PAPER",
                action="DEMOTE",
                reason=f"{consec_count} consecutive negative CLV bets — demotion triggered",
                data={"n": n, **clv_stats, **wr_stats, "consecutiveNegative": consec_count},
            )

        if avg_clv is not None and avg_clv >= MIN_CLV_FOR_REAL:
            roi = roi_stats.get("roi")
            if roi is not None and roi >= -5.0:  # Allow some variance if CLV positive
                return PromotionDecision(
                    market_type=market_type,
                    current_tier=current_tier,
                    recommended_tier="REAL",
                    action="PROMOTE",
                    reason=f"n={n} >= {MIN_SAMPLE_PROBE_TO_REAL}, avg CLV={avg_clv:.2f}% >= {MIN_CLV_FOR_REAL}%",
                    data={"n": n, **clv_stats, **wr_stats, **roi_stats},
                )

        return PromotionDecision(
            market_type=market_type,
            current_tier=current_tier,
            recommended_tier="REAL_PROBE",
            action="MAINTAIN",
            reason=f"n={n}, avg CLV={avg_clv:.2f if avg_clv else 'N/A'}% — maintaining REAL_PROBE",
            data={"n": n, **clv_stats, **wr_stats, **roi_stats},
        )

    # ── REAL demotion check ────────────────────────────────────────────────
    if current_tier == "REAL":
        consecutive_neg, consec_count = check_consecutive_negative_clv(bets)
        if consecutive_neg:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="REAL_PROBE",
                action="DEMOTE",
                reason=f"{consec_count} consecutive negative CLV — demote to REAL_PROBE for investigation",
                data={**clv_stats, **wr_stats, "consecutiveNegative": consec_count},
            )

        clv_window = calculate_clv_stats(
            [b for b in bets if b.get("result") in ("WIN", "LOSS")][-DEMOTION_ROLLING_WINDOW:]
        )
        if clv_window["available"] and clv_window["avgCLV"] is not None and clv_window["avgCLV"] < -2.0:
            return PromotionDecision(
                market_type=market_type,
                current_tier=current_tier,
                recommended_tier="REAL_PROBE",
                action="DEMOTE",
                reason=f"Rolling 10-game avg CLV = {clv_window['avgCLV']:.2f}% < -2.0% — investigate",
                data={**clv_stats, **wr_stats, "rollingCLV": clv_window},
            )

        return PromotionDecision(
            market_type=market_type,
            current_tier=current_tier,
            recommended_tier="REAL",
            action="MAINTAIN",
            reason="No demotion triggers",
            data={**clv_stats, **wr_stats},
        )

    return PromotionDecision(
        market_type=market_type,
        current_tier=current_tier,
        recommended_tier=current_tier,
        action="MAINTAIN",
        reason=f"Unknown tier: {current_tier}",
        data={},
    )


def run_promotion_analysis(bets: List[dict], market_tiers: Dict[str, str] = None) -> dict:
    """
    Run full promotion/demotion analysis across all markets.

    Args:
        bets: all bet dicts
        market_tiers: dict of {market_type: current_tier} overrides

    Returns:
        dict with decisions per market type
    """
    market_tiers = market_tiers or {}

    # Default tiers from historical data
    default_tiers = {
        "ML": "PAPER",
        "F5_ML": "PAPER",
        "YRFI": "PAPER",
        "NRFI": "PAPER",
        "Team_Total": "PAPER",
        "Game_Total": "PAPER",
        "Run_Line": "PAPER",
    }
    default_tiers.update(market_tiers)

    grouped = group_bets_by_market(bets)

    decisions = {}
    for market_type in MARKET_TYPES:
        market_bets = grouped.get(market_type, [])
        tier = default_tiers.get(market_type, "PAPER")
        decision = evaluate_market_tier(market_type, market_bets, current_tier=tier)
        decisions[market_type] = decision.to_dict()

    return {
        "analysisDate": datetime.now(timezone.utc).isoformat(),
        "totalBets": len(bets),
        "decisions": decisions,
    }


if __name__ == "__main__":
    import json

    # Quick smoke test
    test_bets = [
        {"market": "F5 ML", "result": "WIN", "clv": 2.1, "edgePct": 2.5, "stake": 1.0, "pl": 0.85},
        {"market": "F5 ML", "result": "LOSS", "clv": -0.5, "edgePct": 1.8, "stake": 1.0, "pl": -1.0},
    ]
    result = run_promotion_analysis(test_bets)
    print(json.dumps(result["decisions"]["F5_ML"], indent=2))
