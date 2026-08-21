"""
lib/edgelab/kalshi_discovery_bridge.py
===========================================
Universal ModelEvaluation Persistence mission: pure read bridge between
scripts/discover_kalshi_mlb_markets.py's already-computed, already-tested
per-contract fair probabilities (data/kalshi/discovery/<date>.json) and
lib.edgelab.model_evaluation.extend_full_universe_evaluations().

This module computes NO new statistical methodology of its own -- it
only reads a JSON file another script already wrote (via
lib.kalshi_probability_adapters.adapt_contract(), reusing production's
own poisson_pmf/p_team_wins/p_over_total primitives) and reshapes it
into a ticker-keyed lookup. Never raises: a missing or malformed
discovery file for a date yields an empty lookup (extend_full_universe_evaluations
already treats an empty discovery_lookup exactly as it treated the
absence of this bridge entirely, before this mission -- no regression
for any date discovery hasn't run for).

Deliberately in lib/edgelab/ (not lib/, and not scripts/) since this IS
persistence-layer plumbing, unlike lib.kalshi_probability_adapters/
scripts.discover_kalshi_mlb_markets themselves, which stay pure
research/discovery code with no EdgeLab dependency.
"""
import json
import os

DISCOVERY_DIR = os.path.join("data", "kalshi", "discovery")

# discover_kalshi_mlb_markets.py's own modelSupportStatus values --
# imported as plain strings (not the module itself) to avoid this
# lib/edgelab/ module depending on a scripts/ module.
STATUS_SUPPORTED = "SUPPORTED"
STATUS_UNSUPPORTED = "UNSUPPORTED"
STATUS_MISSING_DATA = "MISSING_DATA"


def load_discovery_lookup(date, discovery_dir=DISCOVERY_DIR):
    """
    Returns {marketTicker: contract_dict} for every contract in
    data/kalshi/discovery/<date>.json, or {} if that file doesn't exist
    or fails to parse (never raises, never fabricates a partial result
    from a corrupt file -- an empty lookup makes every ticker fall back
    to extend_full_universe_evaluations()'s pre-existing NOT_EVALUATED/
    NO_MODEL_SUPPORT behavior, exactly as if this bridge didn't exist).
    """
    path = os.path.join(discovery_dir, f"{date}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    contracts = doc.get("contracts") if isinstance(doc, dict) else None
    if not isinstance(contracts, list):
        return {}
    lookup = {}
    for c in contracts:
        ticker = c.get("ticker") if isinstance(c, dict) else None
        if ticker:
            lookup[ticker] = c
    return lookup
