"""
lib/edgelab/paired_evaluation.py
====================================
Research Lab Milestone 0A: the standard, deterministic Control-vs-
Candidate paired evaluator.

CRITICAL REQUIREMENT (spec): Control and Candidate MUST be evaluated on
IDENTICAL ELIGIBLE OBSERVATIONS whenever a paired comparison is claimed.
pair_eligible_observations() below is the ONLY function in this
milestone that produces a paired row set, and every metric function in
this module operates exclusively on its output (`paired`) -- never on
`control_rows`/`candidate_rows` directly. A control-only or
candidate-only observation is reported (never silently dropped) via
`controlOnlyKeys`/`candidateOnlyKeys` on the pairing result, and a
duplicate key within one side (an ambiguous pairing -- should not
happen for a well-formed row set, but never silently resolved by
picking "whichever the dict comprehension kept") is reported via
`duplicateKeys`, with those keys excluded from `paired` rather than
guessed.

This module reuses, rather than reimplements:
  - lib.edgelab.research_stats for Brier score, log loss, calibration
    error, sample-size status, and the game-clustered bootstrap CI.
  - lib.edgelab.kalshi_fees for the ONE fee-aware execution-economics
    engine (see evaluate_market_economics_pair) -- never a second fee
    formula.

PROPER SCORING RULES ARE THE DEFAULT PRIMARY EVALUATION for a
probability-model experiment (spec: "Historical ROI must NOT become the
default optimization target. Proper scoring rules / calibration / true
predictive improvement should generally be primary"). evaluate_
probability_model_pair() is the metric a research report should lead
with; evaluate_market_economics_pair() is explicitly documented as
supplementary.
"""

from collections import defaultdict

from lib.edgelab import kalshi_fees as kf
from lib.edgelab.research_stats import (
    brier_and_log_loss_summary,
    expected_calibration_error,
    game_clustered_bootstrap_ci,
    independent_unit_count,
    sample_size_status,
)
from lib.edgelab.replay import brier_score

DUPLICATE_KEY_AMBIGUOUS = "DUPLICATE_KEY_AMBIGUOUS"


def default_pairing_key(row):
    """(gameId, marketTicker, researchCheckpoint) -- the natural identity of 'one eligible observation' for most EdgeLab research rows (matches lib.edgelab.research_dataset's own row grain)."""
    return (row.get("gameId"), row.get("marketTicker"), row.get("researchCheckpoint") or row.get("checkpoint"))


def _index_by_key(rows, key_fn):
    """Returns (by_key dict, duplicate_keys sorted list) -- a key appearing more than once is NEVER silently resolved to 'the last one seen'; it is excluded from by_key and reported."""
    counts = defaultdict(list)
    for row in rows:
        counts[key_fn(row)].append(row)
    by_key = {}
    duplicates = []
    for key, matches in counts.items():
        if len(matches) > 1:
            duplicates.append(key)
        else:
            by_key[key] = matches[0]
    return by_key, sorted(duplicates, key=str)


def pair_eligible_observations(control_rows, candidate_rows, key_fn=default_pairing_key):
    """
    The one and only pairing primitive this milestone provides. Returns
    a dict:
      paired            -- list of (key, control_row, candidate_row) for
                            every key present, unambiguously, on BOTH sides.
      controlOnlyKeys / candidateOnlyKeys -- keys present on only one side
                            (never silently dropped -- surfaced for the report).
      controlDuplicateKeys / candidateDuplicateKeys -- keys that were
                            ambiguous (appeared more than once) on that
                            side alone, excluded from pairing.
      nPaired / nControlOnly / nCandidateOnly -- raw counts, for the
                            report's dropped-observation accounting.
    """
    control_by_key, control_dupes = _index_by_key(control_rows, key_fn)
    candidate_by_key, candidate_dupes = _index_by_key(candidate_rows, key_fn)

    common_keys = sorted(set(control_by_key) & set(candidate_by_key), key=str)
    control_only = sorted(set(control_by_key) - set(candidate_by_key), key=str)
    candidate_only = sorted(set(candidate_by_key) - set(control_by_key), key=str)

    paired = [(key, control_by_key[key], candidate_by_key[key]) for key in common_keys]

    return {
        "paired": paired,
        "controlOnlyKeys": control_only,
        "candidateOnlyKeys": candidate_only,
        "controlDuplicateKeys": control_dupes,
        "candidateDuplicateKeys": candidate_dupes,
        "nPaired": len(paired),
        "nControlOnly": len(control_only),
        "nCandidateOnly": len(candidate_only),
        "nControlDuplicates": len(control_dupes),
        "nCandidateDuplicates": len(candidate_dupes),
    }


def _cluster_counts(rows, game_key="gameId", date_key="gameDate", player_game_key=None):
    counts = {
        "nRows": len(rows),
        "nIndependentGames": independent_unit_count(rows, key=game_key),
        "nIndependentDates": len({r.get(date_key) for r in rows if r.get(date_key)}),
    }
    if player_game_key:
        counts["nPlayerGames"] = independent_unit_count(rows, key=player_game_key)
    return counts


def evaluate_probability_model_pair(
    pairing_result, *, control_probability_key="modelFairProbability",
    candidate_probability_key="modelFairProbability", outcome_key="outcome",
    game_key="gameId", date_key="gameDate", player_game_key=None,
    cluster_key="gameId", n_resamples=None, ci=None, seed=None,
):
    """
    Standard paired probability-model evaluation -- the PRIMARY metric
    set for a probability/event-model experiment (spec section 4). Every
    number here is computed over `pairing_result["paired"]` ONLY --
    never over the unpaired control_rows/candidate_rows a caller might
    also have on hand, so control and candidate are provably measured on
    identical observations.

    `outcome_key` must resolve to a 0/1 value on EITHER the control or
    candidate row (checked in that order) -- the ground-truth outcome
    doesn't depend on which model evaluated the market, so it is
    expected to be identical on both sides; a row where it differs
    between sides is a genuine data-integrity problem and is excluded
    (never averaged/guessed), reported under droppedForOutcomeMismatch.

    Returns a dict with `n`/`independentGames`/`independentDates`/
    (`playerGames` when player_game_key given), `sampleSizeStatus`
    (lib.edgelab.research_stats.sample_size_status), `control` and
    `candidate` sub-dicts (each: meanProbability, brierScore, logLoss,
    calibrationError), `pairedDelta` (candidate-minus-control on Brier
    score and log loss -- lower is better for both, so a NEGATIVE delta
    means the candidate improved), and `pairedDeltaConfidenceInterval`
    (game-clustered bootstrap CI on the Brier-score delta, reusing
    lib.edgelab.research_stats.game_clustered_bootstrap_ci -- never a
    naive per-row interval), plus `droppedForMissingProbability` /
    `droppedForOutcomeMismatch` counts.
    """
    kwargs = {}
    if n_resamples is not None:
        kwargs["n_resamples"] = n_resamples
    if ci is not None:
        kwargs["ci"] = ci
    if seed is not None:
        kwargs["seed"] = seed

    usable_control_rows, usable_candidate_rows, bootstrap_rows = [], [], []
    dropped_missing_probability = 0
    dropped_outcome_mismatch = 0

    for key, control_row, candidate_row in pairing_result["paired"]:
        control_p = control_row.get(control_probability_key)
        candidate_p = candidate_row.get(candidate_probability_key)
        control_outcome = control_row.get(outcome_key)
        candidate_outcome = candidate_row.get(outcome_key)
        outcome = control_outcome if control_outcome is not None else candidate_outcome

        if control_p is None or candidate_p is None or outcome is None:
            dropped_missing_probability += 1
            continue
        if control_outcome is not None and candidate_outcome is not None and control_outcome != candidate_outcome:
            dropped_outcome_mismatch += 1
            continue

        game_id = control_row.get(game_key) or candidate_row.get(game_key)
        date_val = control_row.get(date_key) or candidate_row.get(date_key)
        player_game = (control_row.get(player_game_key) or candidate_row.get(player_game_key)) if player_game_key else None

        usable_control_rows.append({game_key: game_id, date_key: date_val, player_game_key: player_game, "p": control_p, "o": outcome})
        usable_candidate_rows.append({game_key: game_id, date_key: date_val, player_game_key: player_game, "p": candidate_p, "o": outcome})
        bootstrap_rows.append({game_key: game_id, "controlP": control_p, "candidateP": candidate_p, "o": outcome})

    counts = _cluster_counts(usable_control_rows, game_key=game_key, date_key=date_key, player_game_key=player_game_key)

    control_pairs = [(r["p"], r["o"]) for r in usable_control_rows]
    candidate_pairs = [(r["p"], r["o"]) for r in usable_candidate_rows]
    control_brier, control_logloss = brier_and_log_loss_summary(control_pairs)
    candidate_brier, candidate_logloss = brier_and_log_loss_summary(candidate_pairs)
    control_ece = expected_calibration_error(control_pairs)
    candidate_ece = expected_calibration_error(candidate_pairs)

    def _mean_prob(pairs):
        valid = [p for p, _ in pairs if p is not None]
        return round(sum(valid) / len(valid), 4) if valid else None

    def _brier_delta_value_fn(rows_subset):
        valid = [r for r in rows_subset if r["controlP"] is not None and r["candidateP"] is not None and r["o"] is not None]
        if not valid:
            return None
        control_mean = sum(brier_score(r["controlP"], r["o"]) for r in valid) / len(valid)
        candidate_mean = sum(brier_score(r["candidateP"], r["o"]) for r in valid) / len(valid)
        return candidate_mean - control_mean

    ci_low, ci_high, ci_method = game_clustered_bootstrap_ci(bootstrap_rows, _brier_delta_value_fn, cluster_key=game_key, **kwargs)

    paired_delta_brier = (round(candidate_brier - control_brier, 6) if control_brier is not None and candidate_brier is not None else None)
    paired_delta_logloss = (round(candidate_logloss - control_logloss, 6) if control_logloss is not None and candidate_logloss is not None else None)

    result = {
        "n": counts["nRows"],
        "independentGames": counts["nIndependentGames"],
        "independentDates": counts["nIndependentDates"],
        "sampleSizeStatus": sample_size_status(counts["nRows"], independent_games=counts["nIndependentGames"]),
        "control": {"meanProbability": _mean_prob(control_pairs), "brierScore": control_brier, "logLoss": control_logloss, "calibrationError": control_ece},
        "candidate": {"meanProbability": _mean_prob(candidate_pairs), "brierScore": candidate_brier, "logLoss": candidate_logloss, "calibrationError": candidate_ece},
        "pairedDelta": {"brierScore": paired_delta_brier, "logLoss": paired_delta_logloss, "interpretation": "negative == candidate improved (lower Brier/log-loss is better)"},
        "pairedDeltaConfidenceInterval": {"low": ci_low, "high": ci_high, "method": ci_method, "metric": "brierScoreDelta"},
        "droppedForMissingProbability": dropped_missing_probability,
        "droppedForOutcomeMismatch": dropped_outcome_mismatch,
    }
    if player_game_key:
        result["playerGames"] = counts.get("nPlayerGames")
    return result


# ── Market/economics (supplementary -- see module docstring) ───────────────

PRICE_KIND_BID = "BID"
PRICE_KIND_ASK = "ASK"
PRICE_KIND_MID = "MID"
PRICE_KIND_LAST = "LAST"
PRICE_KIND_VIG_FREE = "VIG_FREE_NORMALIZED"
PRICE_KIND_EXECUTABLE = "EXECUTABLE"
PRICE_KIND_EXECUTABLE_APPROXIMATED = "EXECUTABLE_APPROXIMATED"


def evaluate_market_economics_pair(
    pairing_result, *, control_price_key="executableYesPrice", candidate_price_key="executableYesPrice",
    outcome_key="settlementResult", order_size=None,
):
    """
    SUPPLEMENTARY market/economics comparison over the SAME paired row
    set evaluate_probability_model_pair used -- never a substitute for
    the proper-scoring-rule evaluation above (module docstring). Reuses
    lib.edgelab.kalshi_fees exclusively for fee math -- never a second
    fee formula. A YES purchase price is expected to already be an
    EXECUTABLE price (yesAsk-preferred, matching
    lib.edgelab.research_dataset._executable_yes_price's own
    convention) -- if the caller's rows only carry a raw mid/last price,
    that must be labeled PRICE_KIND_EXECUTABLE_APPROXIMATED by the
    caller in its own report notes; this function does not itself infer
    price kind from a bare key name.
    """
    order_size = order_size if order_size is not None else kf.DEFAULT_RESEARCH_ORDER_SIZE
    granularity = kf.QUANTITY_GRANULARITY_UNKNOWN

    def _side_pl(rows, price_key):
        total_pl, total_stake, n_settled = 0.0, 0.0, 0
        for row in rows:
            price = row.get(price_key)
            result = row.get(outcome_key)
            if price is None or result not in ("YES", "NO"):
                continue
            won = result == "YES"
            sim = kf.simulate_settlement_order(order_size, price, won, quantity_granularity=granularity)
            if sim is None:
                continue
            total_pl += sim["netProfitLoss"]
            total_stake += sim["actualCashConsumed"]
            n_settled += 1
        roi = (total_pl / total_stake) if total_stake else None
        return {"nSettled": n_settled, "totalPl": round(total_pl, 4), "totalCashConsumed": round(total_stake, 4), "roi": (round(roi, 4) if roi is not None else None)}

    control_rows = [c for _, c, _ in pairing_result["paired"]]
    candidate_rows = [d for _, _, d in pairing_result["paired"]]

    control_econ = _side_pl(control_rows, control_price_key)
    candidate_econ = _side_pl(candidate_rows, candidate_price_key)

    return {
        "orderSizeAssumption": order_size,
        "quantityGranularityAssumption": granularity,
        "control": control_econ,
        "candidate": candidate_econ,
        "pairedRoiDelta": (
            round(candidate_econ["roi"] - control_econ["roi"], 4)
            if control_econ["roi"] is not None and candidate_econ["roi"] is not None else None
        ),
        "warning": "SUPPLEMENTARY metric -- historical ROI must never be treated as the default optimization "
                   "target for a probability-model experiment; lead with evaluate_probability_model_pair's "
                   "proper-scoring-rule metrics instead.",
    }
