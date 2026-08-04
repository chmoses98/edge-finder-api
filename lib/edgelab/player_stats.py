#!/usr/bin/env python3
"""
lib/edgelab/player_stats.py
==============================
Pure extraction of one player-prop family's authoritative final-stat
value from an MLB Stats API boxscore player entry's `stats.batting` /
`stats.pitching` sub-object (GitHub issue #43). Never estimates,
interpolates, or rounds -- a missing or internally-inconsistent
component leaves the value unresolved with a specific reason, exactly
like every other settle_* function in this repository (see
lib/edgelab/settlement.py's module docstring).
"""

FAMILY_PITCHER_STRIKEOUTS = "pitcher_strikeouts"
FAMILY_PITCHER_OUTS = "pitcher_outs"
FAMILY_HITTER_HITS = "hitter_hits"
FAMILY_HITTER_TOTAL_BASES = "hitter_total_bases"
FAMILY_HITTER_HITS_RUNS_RBIS = "hitter_hits_runs_rbis"
FAMILY_HITTER_RBIS = "hitter_rbis"
FAMILY_HITTER_STOLEN_BASES = "hitter_stolen_bases"

# Which of the player's two boxscore stat sub-objects (`stats.batting` /
# `stats.pitching`) each family is derived from.
STAT_CATEGORY_BY_FAMILY = {
    FAMILY_PITCHER_STRIKEOUTS: "pitching",
    FAMILY_PITCHER_OUTS: "pitching",
    FAMILY_HITTER_HITS: "batting",
    FAMILY_HITTER_TOTAL_BASES: "batting",
    FAMILY_HITTER_HITS_RUNS_RBIS: "batting",
    FAMILY_HITTER_RBIS: "batting",
    FAMILY_HITTER_STOLEN_BASES: "batting",
}


def extract_pitching_outs(pitching_stat):
    """
    Pure. Returns (outs, fields_used, reason) -- exactly one of
    (outs, reason) is set. Prefers an authoritative numeric `outs`
    field when present (some MLB Stats API responses include it
    directly). Otherwise converts `inningsPitched` (a string like
    "5.0"/"5.1"/"5.2") -- REJECTS any other decimal component (".3"
    through ".9", or a non-numeric whole part) rather than rounding or
    estimating, since only 0/1/2 thirds of an inning are ever valid in
    MLB's own inningsPitched convention.
    """
    if not pitching_stat:
        return None, {}, "missing_pitching_stat"

    outs = pitching_stat.get("outs")
    if outs is not None:
        try:
            return int(outs), {"outs": outs}, None
        except (TypeError, ValueError):
            pass  # fall through to inningsPitched

    ip_raw = pitching_stat.get("inningsPitched")
    if ip_raw is None:
        return None, {}, "missing_innings_pitched"

    ip_str = str(ip_raw)
    whole_str, _, frac_str = ip_str.partition(".")
    frac_str = frac_str or "0"

    try:
        whole = int(whole_str)
    except ValueError:
        return None, {"inningsPitched": ip_raw}, "invalid_innings_pitched_format"

    if whole < 0 or frac_str not in ("0", "1", "2"):
        return None, {"inningsPitched": ip_raw}, "invalid_innings_pitched_format"

    outs = whole * 3 + int(frac_str)
    return outs, {"inningsPitched": ip_raw, "outsDerivedFromInningsPitched": True}, None


def extract_total_bases(batting_stat):
    """
    Pure. Returns (total_bases, fields_used, reason). Prefers an
    authoritative `totalBases` field. Otherwise derives from
    hits/doubles/triples/homeRuns:
        singles = hits - doubles - triples - homeRuns
        totalBases = singles + 2*doubles + 3*triples + 4*homeRuns
    Rejects (never estimates) if any source field is missing, negative,
    non-numeric, or internally inconsistent (derived singles < 0).
    """
    if not batting_stat:
        return None, {}, "missing_batting_stat"

    total_bases = batting_stat.get("totalBases")
    if total_bases is not None:
        try:
            return int(total_bases), {"totalBases": total_bases}, None
        except (TypeError, ValueError):
            pass  # fall through to derivation

    hits, doubles = batting_stat.get("hits"), batting_stat.get("doubles")
    triples, home_runs = batting_stat.get("triples"), batting_stat.get("homeRuns")
    components = {"hits": hits, "doubles": doubles, "triples": triples, "homeRuns": home_runs}

    if any(v is None for v in (hits, doubles, triples, home_runs)):
        return None, components, "missing_total_bases_components"

    try:
        hits, doubles, triples, home_runs = (int(hits), int(doubles), int(triples), int(home_runs))
    except (TypeError, ValueError):
        return None, components, "invalid_total_bases_components"

    if any(v < 0 for v in (hits, doubles, triples, home_runs)):
        return None, components, "invalid_total_bases_components"

    singles = hits - doubles - triples - home_runs
    if singles < 0:
        return None, components, "inconsistent_total_base_components"

    total_bases = singles + 2 * doubles + 3 * triples + 4 * home_runs
    fields_used = dict(components, singlesDerived=singles, totalBasesDerived=True)
    return total_bases, fields_used, None


def _simple_stat_field(stat, field_name):
    """Shared by every family that is just one already-final integer field (no derivation needed)."""
    if not stat:
        return None, {}, f"missing_{field_name}_stat"
    value = stat.get(field_name)
    if value is None:
        return None, {}, f"missing_{field_name}"
    try:
        return int(value), {field_name: value}, None
    except (TypeError, ValueError):
        return None, {field_name: value}, f"invalid_{field_name}"


def extract_hits_runs_rbis(batting_stat):
    """Pure. hits + runs + rbi, all three required (never partially guessed)."""
    if not batting_stat:
        return None, {}, "missing_batting_stat"
    hits, runs, rbi = batting_stat.get("hits"), batting_stat.get("runs"), batting_stat.get("rbi")
    components = {"hits": hits, "runs": runs, "rbi": rbi}
    if any(v is None for v in (hits, runs, rbi)):
        return None, components, "missing_hits_runs_rbi_components"
    try:
        hits, runs, rbi = int(hits), int(runs), int(rbi)
    except (TypeError, ValueError):
        return None, components, "invalid_hits_runs_rbi_components"
    if any(v < 0 for v in (hits, runs, rbi)):
        return None, components, "invalid_hits_runs_rbi_components"
    return hits + runs + rbi, {"hits": hits, "runs": runs, "rbi": rbi}, None


def extract_stat_value(family, batting_stat, pitching_stat):
    """
    Pure. Dispatches to the right extraction for `family` (one of the
    seven player-prop family constants). Returns (actual_value,
    stat_category, fields_used, reason) -- actual_value is None iff
    reason is set; stat_category is "batting"/"pitching"/None.
    """
    if family == FAMILY_PITCHER_STRIKEOUTS:
        value, fields, reason = _simple_stat_field(pitching_stat, "strikeOuts")
        return value, "pitching", fields, reason

    if family == FAMILY_PITCHER_OUTS:
        value, fields, reason = extract_pitching_outs(pitching_stat)
        return value, "pitching", fields, reason

    if family == FAMILY_HITTER_HITS:
        value, fields, reason = _simple_stat_field(batting_stat, "hits")
        return value, "batting", fields, reason

    if family == FAMILY_HITTER_TOTAL_BASES:
        value, fields, reason = extract_total_bases(batting_stat)
        return value, "batting", fields, reason

    if family == FAMILY_HITTER_RBIS:
        value, fields, reason = _simple_stat_field(batting_stat, "rbi")
        return value, "batting", fields, reason

    if family == FAMILY_HITTER_STOLEN_BASES:
        value, fields, reason = _simple_stat_field(batting_stat, "stolenBases")
        return value, "batting", fields, reason

    if family == FAMILY_HITTER_HITS_RUNS_RBIS:
        value, fields, reason = extract_hits_runs_rbis(batting_stat)
        return value, "batting", fields, reason

    return None, None, {}, "unrecognized_player_prop_family"
