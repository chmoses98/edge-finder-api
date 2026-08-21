#!/usr/bin/env python3
"""
lib/research/hitter_market_distributions.py
===============================================
Hitter Projection Engine -- Phase 4 market-distribution layer. Runs
lib.research.lineup_game_simulator.simulate_game() N times (a seeded
Monte Carlo draw per game, deterministic given a master seed) and
aggregates the resulting per-game stat lines into full probability
distributions for every stat category this engine can support --
hits, home runs, total bases, RBI, runs, walks, strikeouts, and the
real Kalshi hitter_hits_runs_rbis family's own summed
hits+runs+rbi stat (lib.edgelab.player_stats.extract_hits_runs_rbis).

ONE ENGINE, NOT SEPARATE MODELS PER MARKET: every distribution below
is read off the SAME set of simulated game stat lines -- there is no
separate hits regression, HR regression, or RBI regression. This is
the direct implementation of this mission's core instruction ("the
objective is to build ONE coherent hitter simulation engine capable of
independently pricing the full hitter-prop universe from the same
underlying simulated baseball process").

REAL VS. HYPOTHETICAL MARKETS: this repository's own confirmed Kalshi
series-catalogue audit (lib.research.market_taxonomy) found real, live
series for exactly hitter_hits (KXMLBHIT), hitter_total_bases
(KXMLBTB), hitter_hits_runs_rbis (KXMLBHRR), hitter_rbis (KXMLBRBI),
and hitter_stolen_bases (KXMLBSB, explicitly out of this mission's
scope). No confirmed real Kalshi series exists in this repository's
archive for standalone home runs, walks, strikeouts, runs, or a
fantasy-score stat. This module still computes ALL of those
distributions internally (the "one coherent engine" requirement is
about the underlying process, not about which markets happen to be
tradable today), but lib.research.hitter_pricing / the projection
board only prices the confirmed-real families against real archived
tickers -- never fabricates a market that doesn't exist.

ALL MARKETS OBSERVED IN THIS REPOSITORY'S ARCHIVE ARE LITERAL "N+"
CONTRACTS (YES iff actual >= N -- lib.edgelab.player_prop_settlement's
own docstring, cross-checked against 46,784 real rows). Every
distribution here therefore exposes an `atLeast` map {N: P(stat>=N)},
which is exactly what those contracts price against -- never an
"over N.5" framing (that belongs only to the separate game/team-total
families this module does not touch).
"""
import math
import random
import statistics
from typing import Optional

from lib.research.lineup_game_simulator import simulate_game
from lib.research.hitter_pa_outcome_model import live_simulation_resample_targets

# Full-integer-distribution stat keys read directly off each simulated
# game's stat line (lib.research.lineup_game_simulator.simulate_game's
# return dict already carries every one of these).
_COUNT_STATS = ("H", "HR", "TB", "RBI", "R", "BB", "K", "PA")

MAX_REPORTED_THRESHOLD = 10  # atLeast[N] reported for N=0..this; a real Kalshi archive has never shown a hitter-prop threshold above single digits


def _pmf_from_counts(values):
    """{value: count} -> {value: probability}, given a list of observed integer values."""
    n = len(values)
    pmf = {}
    for v in values:
        pmf[v] = pmf.get(v, 0) + 1
    return {k: v / n for k, v in pmf.items()}


def _at_least(pmf, max_n=MAX_REPORTED_THRESHOLD):
    """{N: P(stat >= N)} for N=0..max_n, from a {value: probability} pmf."""
    out = {}
    for n in range(0, max_n + 1):
        out[n] = sum(p for v, p in pmf.items() if v >= n)
    return out


def _binomial_mc_stderr(p, n_sims):
    """Standard Monte Carlo error on an estimated probability p from n_sims draws."""
    if n_sims <= 0:
        return None
    return math.sqrt(max(0.0, p * (1.0 - p)) / n_sims)


def _distribution_block(values, n_sims, max_n=MAX_REPORTED_THRESHOLD):
    pmf = _pmf_from_counts(values)
    at_least = _at_least(pmf, max_n=max_n)
    at_least_stderr = {n: round(_binomial_mc_stderr(p, n_sims), 4) for n, p in at_least.items()}
    return {
        "pmf": {int(k): round(v, 4) for k, v in sorted(pmf.items())},
        "atLeast": {int(k): round(v, 4) for k, v in at_least.items()},
        "atLeastMonteCarloStderr": at_least_stderr,
        "mean": round(statistics.mean(values), 3) if values else None,
        "sampleSize": n_sims,
    }


def _check_atleast_monotonic(at_least: dict) -> bool:
    keys = sorted(at_least.keys())
    return all(at_least[keys[i]] >= at_least[keys[i + 1]] - 1e-9 for i in range(len(keys) - 1))


def _check_bounds(at_least: dict) -> bool:
    return all(-1e-9 <= v <= 1.0 + 1e-9 for v in at_least.values())


def run_invariant_checks(distributions: dict, game_stat_lines: list) -> dict:
    """
    Structural sanity checks required by this mission's own spec:
    monotonic non-increasing atLeast thresholds, [0,1] bounds on every
    probability, and the cross-stat coherence rule "HR>=1 implies
    H>=1 and TB>=4" verified directly against the raw simulated game
    lines (not re-derived from the aggregated distributions, so this
    check can never pass merely because two unrelated distributions
    happen to look consistent).
    """
    checks = {}
    for name, dist in distributions.items():
        checks[f"{name}_atLeast_monotonic"] = _check_atleast_monotonic(dist["atLeast"])
        checks[f"{name}_atLeast_bounds_0_1"] = _check_bounds(dist["atLeast"])

    hr_implies_hit_and_tb = all(
        (line["HR"] == 0) or (line["H"] >= 1 and line["TB"] >= 4) for line in game_stat_lines
    )
    checks["hr_implies_hit_and_4plus_tb"] = hr_implies_hit_and_tb

    hits_hierarchy_ok = (
        distributions["hits"]["atLeast"].get(2, 0.0) <= distributions["hits"]["atLeast"].get(1, 1.0) + 1e-9
        and distributions["hits"]["atLeast"].get(3, 0.0) <= distributions["hits"]["atLeast"].get(2, 1.0) + 1e-9
    )
    checks["hits_threshold_hierarchy"] = hits_hierarchy_ok

    hr_hierarchy_ok = distributions["homeRuns"]["atLeast"].get(2, 0.0) <= distributions["homeRuns"]["atLeast"].get(1, 1.0) + 1e-9
    checks["homeRuns_threshold_hierarchy"] = hr_hierarchy_ok

    checks["allPassed"] = all(bool(v) for v in checks.values())
    return checks


def _check_convergence(values, n_sims, split=2):
    """
    Cheap convergence diagnostic: splits the N simulated draws into
    `split` contiguous chunks and reports the max pairwise difference
    in each chunk's mean -- a large spread signals n_sims is too low
    for a stable estimate. Not a formal MCMC diagnostic (this is plain
    i.i.d. Monte Carlo, no chain autocorrelation to check), just a
    cheap split-half stability read.
    """
    if n_sims < split * 20:
        return {"status": "TOO_FEW_SIMULATIONS_FOR_CONVERGENCE_CHECK", "chunkMeans": []}
    chunk_size = n_sims // split
    chunk_means = [
        statistics.mean(values[i * chunk_size:(i + 1) * chunk_size])
        for i in range(split)
    ]
    spread = max(chunk_means) - min(chunk_means)
    return {"status": "OK", "chunkMeans": [round(m, 3) for m in chunk_means], "maxChunkSpread": round(spread, 3)}


def build_hitter_market_distributions(
    n_sims: int,
    target_slot: int,
    target_hitter_pitches: list,
    batter_hand: Optional[str],
    starter_context: dict,
    bullpen_context: dict,
    starter_pitch_mix: Optional[dict],
    bullpen_pitch_mix: Optional[dict],
    park_geometry_entry: Optional[dict],
    field_relative_wind: Optional[dict],
    defense_snapshot: Optional[dict],
    hitter_speed_snapshot: Optional[dict],
    other_hitter_rates: Optional[list] = None,
    n_innings: int = 9,
    seed: int = 0,
    max_threshold: int = MAX_REPORTED_THRESHOLD,
    hitter_pa_by_family: Optional[dict] = None,
    season_stats: Optional[dict] = None,
    platoon_context: Optional[dict] = None,
    season_woba: Optional[float] = None,
) -> dict:
    """
    Runs `n_sims` independent simulated games (each with its own
    sub-seed derived deterministically from `seed`, so the whole call
    is reproducible given the same seed and inputs) and returns the
    full set of market distributions this hitter can be priced
    against, plus Monte Carlo uncertainty/convergence diagnostics and
    the internal-consistency invariant checks this mission's spec
    requires (see run_invariant_checks). `n_sims` is caller-supplied
    (a research/high-resolution mode can request more; production
    board-building should use a modest default -- see
    scripts/build_hitter_projection_board.py) rather than a module-
    level constant, so runtime is always the caller's own choice.

    `hitter_pa_by_family`/`season_stats`/`platoon_context`/`season_woba`
    (Hitter Prop Methodology Repair mission): when supplied, feed
    lib.research.hitter_pa_outcome_model.live_simulation_resample_targets()
    -- computed ONCE here (not per simulated game; platoon/pitcher-
    quality context doesn't vary game-to-game within one board-build)
    and passed to every simulate_game() call as `resample_targets`, so
    the target hitter's own platoon/pitcher-quality adjustment (real
    input, previously computed only for explainability text -- see that
    function's docstring) actually reaches the simulated probability.
    `starter_pitch_mix` is reused as `pitcher_pitch_mix` for this
    computation -- the same family-usage-share shape
    lib.research.hitter_explainability already passes it as. All four
    default to None, reproducing this function's exact pre-mission
    behavior (no resampling) when omitted.
    """
    resample_targets = live_simulation_resample_targets(
        hitter_pa_by_family or {}, season_stats or {}, pitcher_pitch_mix=starter_pitch_mix,
        platoon_context=platoon_context, season_woba=season_woba, starter_context=starter_context,
    )

    master_rng = random.Random(seed)
    game_lines = []
    for _ in range(n_sims):
        sim_seed = master_rng.randrange(2**32)
        game_rng = random.Random(sim_seed)
        stats = simulate_game(
            target_slot=target_slot,
            target_hitter_pitches=target_hitter_pitches,
            batter_hand=batter_hand,
            starter_context=starter_context,
            bullpen_context=bullpen_context,
            starter_pitch_mix=starter_pitch_mix,
            bullpen_pitch_mix=bullpen_pitch_mix,
            park_geometry_entry=park_geometry_entry,
            field_relative_wind=field_relative_wind,
            defense_snapshot=defense_snapshot,
            hitter_speed_snapshot=hitter_speed_snapshot,
            rng=game_rng,
            other_hitter_rates=other_hitter_rates,
            n_innings=n_innings,
            resample_targets=resample_targets,
        )
        game_lines.append(stats)

    hits_runs_rbis_values = [line["H"] + line["R"] + line["RBI"] for line in game_lines]

    distributions = {
        "hits": _distribution_block([line["H"] for line in game_lines], n_sims, max_threshold),
        "homeRuns": _distribution_block([line["HR"] for line in game_lines], n_sims, max_threshold),
        "totalBases": _distribution_block([line["TB"] for line in game_lines], n_sims, max_threshold),
        "rbis": _distribution_block([line["RBI"] for line in game_lines], n_sims, max_threshold),
        "runs": _distribution_block([line["R"] for line in game_lines], n_sims, max_threshold),
        "walks": _distribution_block([line["BB"] for line in game_lines], n_sims, max_threshold),
        "strikeouts": _distribution_block([line["K"] for line in game_lines], n_sims, max_threshold),
        "hitsRunsRbis": _distribution_block(hits_runs_rbis_values, n_sims, max_threshold),
        "plateAppearances": _distribution_block([line["PA"] for line in game_lines], n_sims, max_threshold),
    }

    convergence = {
        "hits": _check_convergence([line["H"] for line in game_lines], n_sims),
        "homeRuns": _check_convergence([line["HR"] for line in game_lines], n_sims),
    }

    invariants = run_invariant_checks(distributions, game_lines)

    return {
        "simulations": n_sims,
        "seed": seed,
        "distributions": distributions,
        "convergence": convergence,
        "invariantChecks": invariants,
    }
