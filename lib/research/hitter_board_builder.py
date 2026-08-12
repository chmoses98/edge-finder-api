#!/usr/bin/env python3
"""
lib/research/hitter_board_builder.py
========================================
Hitter Projection Engine -- Phase 4 pure row-assembly logic for the
canonical hitter projection board (scripts/build_hitter_projection_board.py
is the thin I/O wrapper around this module, mirroring
lib.kalshi_projection_board / scripts/build_projection_board.py's own
split).

Every row on the board corresponds to ONE REAL, ARCHIVED Kalshi hitter
contract (this repository's own confirmed series: KXMLBHIT/hitter_hits,
KXMLBTB/hitter_total_bases, KXMLBRBI/hitter_rbis,
KXMLBHRR/hitter_hits_runs_rbis -- see SUPPORTED_REAL_FAMILIES). No row
is ever fabricated for a market this repository hasn't independently
confirmed exists on Kalshi (hitter_stolen_bases is a fifth confirmed
real series but is explicitly out of this mission's scope). Every
matched contract gets a row regardless of edge sign or size -- this
module never applies a recommendation/staking gate (that stays
entirely in scripts/risk_gate.py and friends, untouched by this
mission).
"""
from typing import Optional

from lib.research.market_taxonomy import classify_market
from lib.research.player_prop_parser import FAMILY_STAT_TEXT, normalized_name_variants
from lib.research.hitter_market_distributions import build_hitter_market_distributions
from lib.research.hitter_explainability import explain_hitter_pa_outcome
from lib.research.hitter_pricing import price_hitter_contract
from lib.research.pitch_environment_model import derive_pitcher_pitch_mix
from lib.research.hitter_pitch_derivation import derive_pa_outcomes_by_pitch_family, _count_pa_terminal_events

MARKET_FAMILY_TO_DISTRIBUTION_KEY = {
    "hitter_hits": "hits",
    "hitter_total_bases": "totalBases",
    "hitter_rbis": "rbis",
    "hitter_hits_runs_rbis": "hitsRunsRbis",
}
SUPPORTED_REAL_FAMILIES = frozenset(MARKET_FAMILY_TO_DISTRIBUTION_KEY)


def match_real_contracts_for_hitter(raw_markets: list, hitter_name: str,
                                     away_abbr: Optional[str], home_abbr: Optional[str]) -> list:
    """
    Classifies every raw market (see
    scripts/discover_kalshi_mlb_markets.extract_raw_markets for the raw
    shape) via lib.research.market_taxonomy.classify_market, keeps only
    the confirmed-real hitter families this engine prices, and matches
    the classified participant name against `hitter_name` via
    lib.research.player_prop_parser's own normalized-name-variant
    comparison (same matching convention settlement uses -- never a
    fuzzy/edit-distance match). Returns the list of matched, classified
    contract dicts (each carrying its own raw yes_bid/yes_ask/mid).
    """
    target_variants = normalized_name_variants(hitter_name)
    if not target_variants:
        return []
    matches = []
    for raw in raw_markets:
        ticker = raw.get("market_ticker") or raw.get("ticker")
        event_ticker = raw.get("event_ticker") or raw.get("eventTicker")
        title = raw.get("title")
        subtitle = raw.get("subtitle")
        classified = classify_market(ticker, event_ticker=event_ticker, title=title, subtitle=subtitle,
                                      away_team=away_abbr, home_team=home_abbr)
        if classified["family"] not in SUPPORTED_REAL_FAMILIES:
            continue
        if classified["classificationStatus"] not in ("classified", "classified_by_title_fallback_unverified_prefix"):
            continue
        participant = classified.get("participant")
        if not participant:
            continue
        if not (normalized_name_variants(participant) & target_variants):
            continue
        matches.append({**classified, "raw": raw})
    return matches


def _executable_yes_price(raw_market: dict) -> Optional[float]:
    mid = raw_market.get("mid")
    if mid is not None:
        return mid
    bid, ask = raw_market.get("yes_bid"), raw_market.get("yes_ask")
    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 4)
    return ask if ask is not None else bid


def build_hitter_projection_rows(
    player_id, player_name: str, batter_hand: Optional[str], target_slot: Optional[int],
    matchup_label: str, raw_pitches: list, season_stats: dict,
    starter_pitches: Optional[list], starter_context: Optional[dict], bullpen_context: Optional[dict],
    park_geometry_entry: Optional[dict], field_relative_wind: Optional[dict],
    defense_snapshot: Optional[dict], hitter_speed_snapshot: Optional[dict],
    platoon_context: Optional[dict], season_woba: Optional[float],
    raw_markets_for_game: list, away_abbr: Optional[str], home_abbr: Optional[str],
    n_sims: int = 1500, seed: int = 0,
) -> dict:
    """
    Top-level per-hitter board-row builder. Returns
    {"status": "PROJECTED"|"NO_LINEUP_SLOT"|"NO_ARCHIVED_CONTRACTS",
    "rows": [...], "distributions": {...}|None, "explainability": {...}|None}.

    `target_slot` (1-9, this hitter's confirmed batting-order position)
    is REQUIRED for a full game-simulation projection (RBI/runs/PA-count
    all need lineup position) -- a hitter without a confirmed slot gets
    status="NO_LINEUP_SLOT" and zero rows (never a fabricated slot).
    """
    if not target_slot or not (1 <= target_slot <= 9):
        return {"status": "NO_LINEUP_SLOT", "rows": [], "distributions": None, "explainability": None}

    matched = match_real_contracts_for_hitter(raw_markets_for_game, player_name, away_abbr, home_abbr)
    if not matched:
        return {"status": "NO_ARCHIVED_CONTRACTS", "rows": [], "distributions": None, "explainability": None}

    hitter_pa_by_family = derive_pa_outcomes_by_pitch_family(raw_pitches) if raw_pitches else {}
    starter_pitch_mix = None
    if starter_pitches:
        mix_result = derive_pitcher_pitch_mix(starter_pitches, batter_hand=batter_hand)
        if mix_result["status"] != "MISSING_DATA":
            starter_pitch_mix = mix_result["mix"]

    dist_result = build_hitter_market_distributions(
        n_sims=n_sims, target_slot=target_slot, target_hitter_pitches=raw_pitches or [],
        batter_hand=batter_hand, starter_context=starter_context or {}, bullpen_context=bullpen_context or {},
        starter_pitch_mix=starter_pitch_mix, bullpen_pitch_mix=None,
        park_geometry_entry=park_geometry_entry, field_relative_wind=field_relative_wind,
        defense_snapshot=defense_snapshot, hitter_speed_snapshot=hitter_speed_snapshot,
        seed=seed,
    )
    distributions = dist_result["distributions"]

    explainability = explain_hitter_pa_outcome(
        hitter_pa_by_family, season_stats or {}, pitcher_pitch_mix=starter_pitch_mix,
        platoon_context=platoon_context, season_woba=season_woba, starter_context=starter_context,
    )

    _, raw_pa, _raw_ab, _dates, _unrec = _count_pa_terminal_events(raw_pitches or [])
    sample_diagnostics = {
        "hitterArchivedPACount": raw_pa,
        "hitterPAByFamilyKnownFamilies": sorted(hitter_pa_by_family.keys()),
        "starterPitchMixStatus": "DERIVED_FROM_ARCHIVE" if starter_pitch_mix else "GENERIC_DEFAULT_FALLBACK",
        "bullpenPitchMixStatus": "GENERIC_DEFAULT_FALLBACK",
        "monteCarloSimulations": n_sims,
    }
    model_limitations = [
        "Bullpen relief-pitcher pitch mix uses a generic fallback -- no per-reliever archive is wired into this board.",
        "Baserunning uses a simplified 'advance exactly N bases' convention (lib.research.lineup_game_simulator docstring), not real productive-out/tag-up nuance.",
        "Park orientation/wind is down-weighted (WIND_ORIENTATION_CONFIDENCE_WEIGHT=0.3) -- orientationDeg is marked approximate_unverified upstream (PR #80).",
    ]
    if raw_pa == 0:
        model_limitations.insert(0, "No archived raw pitch history for this hitter -- every rate below falls back to season/league-prior shrinkage levels only.")

    rows = []
    for contract in matched:
        family = contract["family"]
        threshold = contract["line"]
        dist_key = MARKET_FAMILY_TO_DISTRIBUTION_KEY[family]
        if threshold is None or threshold not in distributions[dist_key]["atLeast"]:
            continue
        model_prob = distributions[dist_key]["atLeast"][threshold]
        executable_price = _executable_yes_price(contract["raw"])
        pricing = price_hitter_contract(model_prob, executable_price)
        stat_text = FAMILY_STAT_TEXT.get(family, family)

        rows.append({
            "marketTicker": contract["marketTicker"],
            "naturalLanguageMarket": f"{player_name}: {threshold}+ {stat_text}?",
            "player": player_name,
            "playerId": player_id,
            "matchup": matchup_label,
            "marketFamily": family,
            "threshold": threshold,
            "distributionUsed": dist_key,
            "modelProbability": pricing["modelProbability"],
            "fairAmericanOdds": pricing["fairAmericanOdds"],
            "executableKalshiPrice": executable_price,
            "executableAmericanOdds": pricing["executableAmericanOdds"],
            "rawProbabilityEdge": pricing["rawProbabilityEdge"],
            "expectedValuePerDollar": pricing["expectedValuePerDollar"],
            "pricingStatus": pricing["pricingStatus"],
            "monteCarloStderr": distributions[dist_key]["atLeastMonteCarloStderr"].get(threshold),
            "projectionStatus": "PROJECTED",
            "sampleSizeDiagnostics": sample_diagnostics,
            "modelLimitations": model_limitations,
        })

    return {
        "status": "PROJECTED",
        "rows": rows,
        "distributions": distributions,
        "explainability": explainability,
        "invariantChecks": dist_result["invariantChecks"],
        "convergence": dist_result["convergence"],
    }
