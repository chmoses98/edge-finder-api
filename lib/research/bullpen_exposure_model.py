#!/usr/bin/env python3
"""
lib/research/bullpen_exposure_model.py
=========================================
Hitter Projection Engine -- Phase 4 starter -> bullpen exposure.

A hitter prop cannot assume every PA is against the starter. Rather
than pre-computing a standalone P(PA vs starter) distribution, this
module provides the reusable PRIMITIVES lib.research.lineup_game_simulator
calls at each half-inning boundary so exposure EMERGES from the actual
simulated game state (this mission's own preference: "prefer deriving
PA opportunities from the game simulation where feasible") -- innings
pitched so far, workload budget, and bullpen handedness composition all
feed a stochastic decision, not a fixed lookup.
"""

import math
from typing import Optional

DEFAULT_AVG_IP_PER_START = 5.2  # generic fallback when starterContext has no avgIPperStart on file
DEFAULT_BULLPEN_RHP_SHARE = 0.6  # typical MLB bullpen R/L composition when no recentUsage handedness mix is available


def should_starter_continue(innings_pitched: float, avg_ip_per_start: Optional[float], rng) -> bool:
    """
    Stochastic decision, re-evaluated at each half-inning boundary in
    lib.research.lineup_game_simulator: as `innings_pitched` approaches
    (and then exceeds) `avg_ip_per_start`, the probability of pulling
    the starter rises smoothly (logistic), never a hard cutoff at a
    single inning number.
    """
    budget = (avg_ip_per_start if avg_ip_per_start is not None else DEFAULT_AVG_IP_PER_START) - innings_pitched
    p_continue = 1.0 / (1.0 + math.exp(-1.2 * budget))
    return rng.random() < p_continue


def choose_bullpen_pitcher_hand(bullpen_context: Optional[dict], rng) -> str:
    """
    Weighted by this team's actual recent bullpen handedness mix
    (PR #77/#80's bullpenContext.recentUsage.handednessMix) when
    available; otherwise a documented generic MLB bullpen composition
    default (never fabricates a specific mix that isn't there).
    """
    recent_usage = (bullpen_context or {}).get("recentUsage") or {}
    mix = recent_usage.get("handednessMix") if isinstance(recent_usage, dict) else None
    if isinstance(mix, dict):
        r = mix.get("R") or mix.get("RHP") or 0
        l = mix.get("L") or mix.get("LHP") or 0
        total = r + l
        if total > 0:
            return "R" if rng.random() < (r / total) else "L"
    return "R" if rng.random() < DEFAULT_BULLPEN_RHP_SHARE else "L"


def bullpen_pitcher_quality(bullpen_context: Optional[dict]) -> dict:
    """
    Approximate kPct/bbPct-equivalent from bullpenContext.teamQuality's
    kPer9/bbPer9 (this repo's existing bullpen schema, PR #77/#80 --
    reused, not re-fetched). The *9 -> *Pct conversion is a documented
    rough approximation (roughly batters-faced-per-9-innings scaling),
    not an exact statistical identity -- flagged in the returned dict's
    `approximate=True` so callers/explainability never treat it as a
    real observed rate.
    """
    quality = (bullpen_context or {}).get("teamQuality") or {}
    k_per9, bb_per9 = quality.get("kPer9"), quality.get("bbPer9")
    return {
        "kPct": round(min(45.0, k_per9 * 2.3), 1) if k_per9 is not None else None,
        "bbPct": round(min(20.0, bb_per9 * 2.3), 1) if bb_per9 is not None else None,
        "approximate": True,
        "source": "bullpenContext.teamQuality kPer9/bbPer9 (approximate conversion)",
    }
