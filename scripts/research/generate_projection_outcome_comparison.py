#!/usr/bin/env python3
"""
scripts/research/generate_projection_outcome_comparison.py
================================================================
Model Performance Phase 1 (Market Audit), Part 7A -- RESEARCH-ONLY
comparison between the CURRENT production three-way methodology
(independent Poisson, tie computed then discarded via renormalization
-- replicated here byte-for-byte from scripts/build_market_ledger.py's
`p_team_wins()`/net-of-tie formula, NOT imported from it, so this
script has zero runtime dependency on production code) and the
CANDIDATE methodology (lib/research/three_way_projection.py: same
underlying independent-Poisson joint distribution, but the tie
probability is retained and NEVER renormalized away).

Only synthetic/fixture game projections are used as input -- this
script does not read data/slate.json, bets.json, or any real
production recommendation, and does not write to any of them. Output
is written only to data/research/projection_outcome_comparison.json.

METHODS ACTUALLY IMPLEMENTED THIS PHASE (adequate data + time exist):
  1. production_current       -- replicated current formula (renormalized)
  2. candidate_retained_tie   -- lib.research.three_way_projection (tie retained)

METHODS EVALUATED BUT NOT IMPLEMENTED THIS PHASE (see
docs/research/PROJECTION_UPGRADE_ROADMAP.md for why each is deferred --
primarily: negative-binomial/bivariate-Poisson require fitting
overdispersion/correlation parameters against real historical team
scoring data this phase did not have time to assemble and validate;
empirical/simulation methods require a much larger historical score
matrix than currently exists in this repository; market-informed
priors require a leakage-safe sharp-book price feed this repository
does not currently ingest at all):
  3. negative_binomial         -- NOT IMPLEMENTED (Wave 2 candidate)
  4. bivariate_poisson         -- NOT IMPLEMENTED (Wave 2 candidate)
  5. empirical_simulation      -- NOT IMPLEMENTED (Wave 3+ candidate)
  6. market_informed            -- NOT IMPLEMENTED (Wave 2+ candidate,
                                    leakage risk must be resolved first)
"""
import json
import math
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.research.three_way_projection import three_way_result_probs_for_horizon, HORIZON_INNINGS

OUTPUT_PATH = os.path.join(ROOT, "data", "research", "projection_outcome_comparison.json")

NOT_IMPLEMENTED_METHODS = {
    "negative_binomial": "requires fitting an overdispersion parameter against real historical team-scoring variance -- not available this phase; see roadmap Wave 2",
    "bivariate_poisson": "requires a fitted team-score correlation parameter against real historical paired-score data -- not available this phase; see roadmap Wave 2",
    "empirical_simulation": "requires a substantially larger historical joint-score matrix than currently exists in this repository -- see roadmap Wave 3",
    "market_informed": "requires a leakage-safe sharp-book price feed this repository does not currently ingest -- see roadmap Wave 2, leakage risk must be resolved first",
}


def poisson_pmf(k, lam):
    if lam is None or lam <= 0:
        return 1.0 if k == 0 and lam == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def production_current_three_way(away_proj, home_proj, max_r=20):
    """
    Byte-for-byte replication of scripts/build_market_ledger.py's
    current formula (p_team_wins() + the "net of tie" renormalization
    applied at both the full-game ML_Away/ML_Home site and the
    F5_ML_Away/F5_ML_Home site) -- NOT imported from production, so
    this research script has no runtime coupling to it, but matching
    it exactly so the comparison is honest.
    """
    pw = pp = 0.0
    for a in range(max_r + 1):
        for h in range(max_r + 1):
            p = poisson_pmf(a, away_proj) * poisson_pmf(h, home_proj)
            if a > h:
                pw += p
            elif a == h:
                pp += p
    p_home_win = 1 - pw - pp
    denom = 1 - pp
    p_away_net = pw / denom if denom > 0 else pw
    p_home_net = p_home_win / denom if denom > 0 else p_home_win
    return {
        "awayWinProb": p_away_net,
        "homeWinProb": p_home_net,
        "tieProbComputedThenDiscarded": pp,
        "note": "tie WAS computed (pp) but discarded via renormalization -- "
                "matches production's ACTUAL current behavior, not an idealized one",
    }


FIXTURE_GAMES = [
    {
        "gameId": "research-fixture-1",
        "label": "evenly_matched_moderate_total",
        "awayFullProj": 4.5, "homeFullProj": 4.3,
    },
    {
        "gameId": "research-fixture-2",
        "label": "large_away_favorite",
        "awayFullProj": 6.8, "homeFullProj": 2.9,
    },
    {
        "gameId": "research-fixture-3",
        "label": "low_scoring_pitchers_duel",
        "awayFullProj": 2.2, "homeFullProj": 2.0,
    },
    {
        "gameId": "research-fixture-4",
        "label": "high_scoring_shootout",
        "awayFullProj": 7.5, "homeFullProj": 7.1,
    },
]


def compare_game(fixture):
    away_full = fixture["awayFullProj"]
    home_full = fixture["homeFullProj"]
    horizons = {}

    for horizon in ("full_game", "F3", "F5", "F7"):
        fraction = HORIZON_INNINGS[horizon] / 9.0
        away_h = away_full * fraction
        home_h = home_full * fraction

        production = production_current_three_way(away_h, home_h)
        candidate = three_way_result_probs_for_horizon(away_full, home_full, horizon)

        horizons[horizon] = {
            "awayProj": away_h,
            "homeProj": home_h,
            "productionCurrent": production,
            "candidateRetainedTie": {
                "awayWinProb": candidate["awayWinProb"],
                "tieProb": candidate["tieProb"],
                "homeWinProb": candidate["homeWinProb"],
                "truncationMass": candidate["truncationMass"],
            },
            "delta": {
                "awayWinProbDelta": candidate["awayWinProb"] - production["awayWinProb"],
                "homeWinProbDelta": candidate["homeWinProb"] - production["homeWinProb"],
                "tieProbRecoveredByCandidate": candidate["tieProb"],
            },
            "methodsNotImplementedThisPhase": NOT_IMPLEMENTED_METHODS,
        }

    return {
        "gameId": fixture["gameId"],
        "label": fixture["label"],
        "awayFullProj": away_full,
        "homeFullProj": home_full,
        "horizons": horizons,
    }


def build_comparison():
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "generatorScript": "scripts/research/generate_projection_outcome_comparison.py",
        "note": (
            "RESEARCH-ONLY artifact built from SYNTHETIC fixture projections, "
            "not real historical games and not real production data. Never "
            "consumed by production betting logic. Does not affect "
            "marketLedger, execution.json, bets.json, or current recommendations."
        ),
        "methodsImplemented": ["production_current", "candidate_retained_tie"],
        "methodsNotImplementedThisPhase": NOT_IMPLEMENTED_METHODS,
        "games": [compare_game(f) for f in FIXTURE_GAMES],
    }


def main():
    comparison = build_comparison()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(comparison, f, indent=2, sort_keys=True)
    print(f"Wrote comparison for {len(comparison['games'])} fixture games to "
          f"{os.path.relpath(OUTPUT_PATH, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
