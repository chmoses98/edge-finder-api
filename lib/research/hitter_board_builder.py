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

Hitter Projection Engine Phase 5 (COMPLETE MARKET PRESERVATION):
`build_hitter_projection_rows` only ever emits a row for a contract it
could actually match to a confirmed hitter -- it silently has nothing
to say about every OTHER real archived hitter contract in the same
game (an unconfirmed lineup, a bench player with a phantom market, an
already-started game, ...). `build_game_contract_coverage` is the
higher-level function that guarantees EVERY real hitter contract for a
game gets exactly one row: either the PROJECTED row from
`build_hitter_projection_rows`, or an explicit STATUS_* row from
`classify_unmatched_contract_status` below -- never a silent drop.
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

# Phase 5 -- every archived hitter contract/rung gets exactly one of
# these statuses. STATUS_PROJECTED rows come from
# build_hitter_projection_rows; every other status comes from
# classify_unmatched_contract_status's reconciliation pass.
STATUS_PROJECTED = "PROJECTED"
STATUS_LINEUP_UNCONFIRMED = "LINEUP_UNCONFIRMED"
STATUS_GAME_STARTED = "GAME_STARTED"
STATUS_PLAYER_NOT_IN_STARTING_LINEUP = "PLAYER_NOT_IN_STARTING_LINEUP"
STATUS_PLAYER_ID_UNRESOLVED = "PLAYER_ID_UNRESOLVED"
STATUS_MARKET_SEMANTICS_UNSUPPORTED = "MARKET_SEMANTICS_UNSUPPORTED"
STATUS_AMBIGUOUS_TICKER_MATCH = "AMBIGUOUS_TICKER_MATCH"
STATUS_MISSING_REQUIRED_CONTEXT = "MISSING_REQUIRED_CONTEXT"
STATUS_MODEL_ERROR = "MODEL_ERROR"


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


def _market_observed_at(raw_market: dict) -> Optional[str]:
    """The immutable snapshot's own per-market capture timestamp -- `snapshot_ts` is the field
    every raw market row in data/kalshi_registry_snapshots/*.json actually carries (confirmed
    against real archived snapshots). Never falls back to "now" -- a market observation's time
    is a property of the SNAPSHOT it was read from, not of whenever this code happens to run."""
    return raw_market.get("snapshot_ts") or raw_market.get("snapshotTs")


def build_hitter_projection_rows(
    player_id, player_name: str, batter_hand: Optional[str], target_slot: Optional[int],
    matchup_label: str, raw_pitches: list, season_stats: dict,
    starter_pitches: Optional[list], starter_context: Optional[dict], bullpen_context: Optional[dict],
    park_geometry_entry: Optional[dict], field_relative_wind: Optional[dict],
    defense_snapshot: Optional[dict], hitter_speed_snapshot: Optional[dict],
    platoon_context: Optional[dict], season_woba: Optional[float],
    raw_markets_for_game: list, away_abbr: Optional[str], home_abbr: Optional[str],
    n_sims: int = 1500, seed: int = 0,
    source_capture_path: Optional[str] = None, research_run_id: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> dict:
    """
    Top-level per-hitter board-row builder. Returns
    {"status": "PROJECTED"|"NO_LINEUP_SLOT"|"NO_ARCHIVED_CONTRACTS",
    "rows": [...], "distributions": {...}|None, "explainability": {...}|None}.

    `target_slot` (1-9, this hitter's confirmed batting-order position)
    is REQUIRED for a full game-simulation projection (RBI/runs/PA-count
    all need lineup position) -- a hitter without a confirmed slot gets
    status="NO_LINEUP_SLOT" and zero rows (never a fabricated slot).

    Phase 5 immutable-snapshot linkage: `source_capture_path` identifies
    the EXACT immutable Kalshi snapshot `raw_markets_for_game` was read
    from -- stamped onto every row alongside each contract's own
    `marketObservedAt` (from that raw market's own `snapshot_ts`), so a
    row's executable price can always be traced back to the single
    snapshot it came from, never a later/different mutable file
    (this mission's own explicit invariant -- see
    tests/test_hitter_phase5_orchestration.py's snapshot-linkage tests).
    `research_run_id`/`generated_at` are stamped identically for the
    same reason at the run level.
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
            "projectionStatus": STATUS_PROJECTED,
            "projectionStatusReason": None,
            "marketObservedAt": _market_observed_at(contract["raw"]),
            "sourceCapturePath": source_capture_path,
            "researchRunId": research_run_id,
            "projectionGeneratedAt": generated_at,
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


def _status_only_row(raw_market: dict, classified: dict, status: str, reason: str, matchup_label: Optional[str],
                      source_capture_path: Optional[str], research_run_id: Optional[str], generated_at: Optional[str]) -> dict:
    """One board row for a real archived hitter contract this run did NOT project -- every field
    a PROJECTED row carries is still present (None where genuinely inapplicable), so a consumer
    never needs to branch on projectionStatus to find a contract's identity/ticker/price."""
    family = classified.get("family")
    threshold = classified.get("line")
    participant = classified.get("participant")
    stat_text = FAMILY_STAT_TEXT.get(family) if family else None
    natural_language = (
        f"{participant}: {threshold}+ {stat_text}?" if participant and threshold is not None and stat_text
        else raw_market.get("title")
    )
    return {
        "marketTicker": classified.get("marketTicker") or raw_market.get("market_ticker") or raw_market.get("ticker"),
        "naturalLanguageMarket": natural_language,
        "player": participant,
        "playerId": None,
        "matchup": matchup_label,
        "marketFamily": family,
        "threshold": threshold,
        "distributionUsed": None,
        "modelProbability": None,
        "fairAmericanOdds": None,
        "executableKalshiPrice": _executable_yes_price(raw_market),
        "executableAmericanOdds": None,
        "rawProbabilityEdge": None,
        "expectedValuePerDollar": None,
        "pricingStatus": "NOT_PROJECTED",
        "monteCarloStderr": None,
        "projectionStatus": status,
        "projectionStatusReason": reason,
        "marketObservedAt": _market_observed_at(raw_market),
        "sourceCapturePath": source_capture_path,
        "researchRunId": research_run_id,
        "projectionGeneratedAt": generated_at,
        "sampleSizeDiagnostics": None,
        "modelLimitations": [],
    }


def build_ambiguous_doubleheader_row(raw_market: dict, away_abbr: Optional[str], home_abbr: Optional[str],
                                      reason: str, source_capture_path: Optional[str] = None,
                                      research_run_id: Optional[str] = None, generated_at: Optional[str] = None) -> dict:
    """
    One board row for a real archived hitter contract whose OWNING GAME
    could not be deterministically resolved among two-or-more real
    doubleheader candidates sharing the same away/home abbreviations
    (see scripts/build_hitter_projection_board.py's
    `_raw_markets_for_game`/`find_ambiguous_doubleheader_markets`: a
    missing/unparseable ticker time, or a genuine tie between
    equally-close candidate games). This function's caller must never
    guess a gameId for such a market -- reuses the SAME
    STATUS_AMBIGUOUS_TICKER_MATCH status this module already uses for a
    different kind of ambiguity (multiple confirmed hitters matching one
    contract's player name), since both are "this contract's owning
    entity cannot be determined without guessing" cases. The row still
    carries every field a normal unmatched-contract row carries (via
    `_status_only_row`) so this market is fully preserved on the board,
    never silently dropped just because neither candidate game claimed
    it -- only its `matchup` label is the shared "AWAY @ HOME" text
    rather than a specific game's own label, and its gameId (stamped by
    the caller, not this function) is deliberately left unset rather
    than attributed to either candidate.
    """
    classified = classify_market(
        raw_market.get("market_ticker") or raw_market.get("ticker"),
        event_ticker=raw_market.get("event_ticker") or raw_market.get("eventTicker"),
        title=raw_market.get("title"), subtitle=raw_market.get("subtitle"),
        away_team=away_abbr, home_team=home_abbr,
    )
    matchup_label = f"{away_abbr} @ {home_abbr}"
    return _status_only_row(raw_market, classified, STATUS_AMBIGUOUS_TICKER_MATCH, reason, matchup_label,
                             source_capture_path, research_run_id, generated_at)


def _find_matching_hitters(participant_name: Optional[str], hitters: list) -> list:
    variants = normalized_name_variants(participant_name) if participant_name else frozenset()
    if not variants:
        return []
    return [h for h in hitters if normalized_name_variants(h.get("name")) & variants]


def classify_unmatched_contract_status(
    raw_market: dict, away_abbr: Optional[str], home_abbr: Optional[str],
    game_started: bool, lineup_confirmed_by_abbr: dict, hitters_both_sides: list,
    model_error_by_player_id: Optional[dict] = None,
) -> tuple:
    """
    Pure classification for ONE real hitter contract that
    build_hitter_projection_rows did not already cover with a PROJECTED
    row. Returns (classified_dict, status, reason). Checked in this
    order (structural failures before context-dependent ones, matching
    lib.edgelab.player_prop_settlement's own precedent):
    unsupported market shape -> game already started -> lineup not yet
    confirmed for this contract's team -> player identity unresolved ->
    zero/multiple confirmed-hitter name matches -> a per-hitter model
    failure already recorded for the one matching hitter -> a generic
    "matched but not covered" fallback.
    """
    classified = classify_market(
        raw_market.get("market_ticker") or raw_market.get("ticker"),
        event_ticker=raw_market.get("event_ticker") or raw_market.get("eventTicker"),
        title=raw_market.get("title"), subtitle=raw_market.get("subtitle"),
        away_team=away_abbr, home_team=home_abbr,
    )
    if classified["family"] not in SUPPORTED_REAL_FAMILIES or classified["classificationStatus"] not in (
            "classified", "classified_by_title_fallback_unverified_prefix"):
        return classified, STATUS_MARKET_SEMANTICS_UNSUPPORTED, "ticker/title did not classify into a family+threshold this engine supports"

    if game_started:
        return classified, STATUS_GAME_STARTED, "game has already started as of this capture -- not presented as a fresh pregame fair price"

    team_abbr = classified.get("team")
    if not lineup_confirmed_by_abbr.get(team_abbr, False):
        return classified, STATUS_LINEUP_UNCONFIRMED, f"confirmed starting lineup not yet available for {team_abbr or 'this team'}"

    participant = classified.get("participant")
    if not participant:
        return classified, STATUS_PLAYER_ID_UNRESOLVED, "ticker/title did not parse to a resolvable player display name"

    matches = _find_matching_hitters(participant, hitters_both_sides)
    if len(matches) == 0:
        return classified, STATUS_PLAYER_NOT_IN_STARTING_LINEUP, "no confirmed starting-lineup hitter matched this contract's player name"
    if len(matches) > 1:
        return classified, STATUS_AMBIGUOUS_TICKER_MATCH, f"{len(matches)} confirmed starting-lineup hitters matched this contract's player name"

    hitter = matches[0]
    model_error_by_player_id = model_error_by_player_id or {}
    if hitter.get("playerId") in model_error_by_player_id:
        return classified, STATUS_MODEL_ERROR, model_error_by_player_id[hitter.get("playerId")]
    return classified, STATUS_MISSING_REQUIRED_CONTEXT, "confirmed hitter matched but this contract's threshold/family was not covered by that hitter's own projection"


def build_game_contract_coverage(
    all_game_markets: list, hitters_both_sides: list, hitter_kwargs_by_player_id: dict,
    away_abbr: Optional[str], home_abbr: Optional[str], matchup_label: str,
    lineup_confirmed_by_abbr: dict, game_started: bool,
    source_capture_path: Optional[str] = None, research_run_id: Optional[str] = None,
    generated_at: Optional[str] = None, n_sims: int = 1500, seed_base: int = 0,
) -> dict:
    """
    Top-level per-GAME board builder guaranteeing every real archived
    hitter contract in `all_game_markets` gets exactly one row. Calls
    build_hitter_projection_rows ONCE per confirmed hitter (never once
    per contract -- a hitter's full distribution is simulated a single
    time and every one of their matched thresholds is read off it,
    per this mission's own performance requirement), wraps each call in
    try/except so one hitter's modeling failure (STATUS_MODEL_ERROR)
    never erases any other hitter's rows, then reconciles every
    still-uncovered contract via classify_unmatched_contract_status.

    Returns {"rows": [...], "hitterSummaries": [...]}.

    Each entry in `hitters_both_sides` must carry its own `"teamAbbr"` --
    this function independently re-checks `lineup_confirmed_by_abbr` for
    that team before attempting a projection, rather than trusting a
    caller-supplied hitter list to already be lineup-confirmation-
    filtered (a hitter whose team's confirmation flips between when the
    caller built this list and when this function runs must never be
    silently projected on stale confirmation).
    """
    rows = []
    hitter_summaries = []
    covered_tickers = set()
    model_error_by_player_id = {}

    if not game_started:
        for i, hitter in enumerate(hitters_both_sides):
            player_id = hitter.get("playerId")
            if not lineup_confirmed_by_abbr.get(hitter.get("teamAbbr"), False):
                continue
            kwargs = hitter_kwargs_by_player_id.get(player_id)
            if kwargs is None:
                continue
            try:
                result = build_hitter_projection_rows(
                    **kwargs, raw_markets_for_game=all_game_markets, away_abbr=away_abbr, home_abbr=home_abbr,
                    n_sims=n_sims, seed=seed_base + i,
                    source_capture_path=source_capture_path, research_run_id=research_run_id, generated_at=generated_at,
                )
            except Exception as e:
                model_error_by_player_id[player_id] = f"{type(e).__name__}: {e}"
                hitter_summaries.append({"playerId": player_id, "name": hitter.get("name"), "status": STATUS_MODEL_ERROR, "rowsProduced": 0})
                continue

            for row in result["rows"]:
                covered_tickers.add(row["marketTicker"])
            rows.extend(result["rows"])
            hitter_summaries.append({"playerId": player_id, "name": hitter.get("name"), "status": result["status"], "rowsProduced": len(result["rows"])})

    for raw in all_game_markets:
        ticker = raw.get("market_ticker") or raw.get("ticker")
        if ticker in covered_tickers:
            continue
        classified, status, reason = classify_unmatched_contract_status(
            raw, away_abbr, home_abbr, game_started, lineup_confirmed_by_abbr, hitters_both_sides,
            model_error_by_player_id=model_error_by_player_id,
        )
        rows.append(_status_only_row(raw, classified, status, reason, matchup_label,
                                      source_capture_path, research_run_id, generated_at))

    return {"rows": rows, "hitterSummaries": hitter_summaries}
