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

STRICT NUMERIC VALIDATION (issue #43 correction round): every counting
statistic (strikeouts, direct pitcher outs, hits, direct total bases,
runs, RBIs, stolen bases, and every total-base/hits-runs-RBIs
derivation component) is parsed via parse_nonnegative_int() -- never a
bare `int(value)` conversion, which silently TRUNCATES a fractional
value (`int(3.5) == 3`) instead of rejecting it as the malformed
statistic it is. parse_nonnegative_int() accepts ONLY an exact
nonnegative integer, in int/float/str form, and rejects everything else
(booleans, negative numbers, non-integral floats, decimal strings,
NaN/inf, malformed strings, arbitrary objects) -- see its own docstring.
`inningsPitched` parsing is kept entirely separate and independently
strict (see extract_pitching_outs): only .0/.1/.2 fractional components
are ever valid outs-recorded representations.
"""
import math

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


def parse_nonnegative_int(value):
    """
    Pure. Strict parser for a counting statistic -- accepts ONLY a value
    representing an EXACT nonnegative integer:
      - a Python `int` (excluding `bool` -- `True`/`False` are `int`
        subclasses in Python and must never silently parse as 1/0)
        that is >= 0.
      - a Python `float` that is an exact whole number (3.0) and not
        NaN/inf -- a non-integral float (3.5) is REJECTED, never
        truncated.
      - a `str` of only ASCII digits (e.g. "3") -- REJECTED for a
        decimal point ("3.5"), a sign ("-1", "+3"), whitespace-only
        content, or any non-digit character.
    Returns None for anything else (booleans, negative numbers,
    non-integral floats, decimal strings, NaN/inf, malformed strings,
    arbitrary objects/None) -- deliberately never uses a bare `int()`
    conversion, which would silently truncate `int(3.5) == 3` instead of
    rejecting the malformed value.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        if value != int(value):
            return None
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.isascii() or not stripped.isdigit():
            return None
        return int(stripped)
    return None


def extract_pitching_outs(pitching_stat):
    """
    Pure. Returns (outs, fields_used, reason) -- exactly one of
    (outs, reason) is set. Prefers an authoritative numeric `outs`
    field when present (some MLB Stats API responses include it
    directly), strictly parsed via parse_nonnegative_int(). Otherwise
    converts `inningsPitched` (a string like "5.0"/"5.1"/"5.2") --
    REJECTS any other decimal component (".3" through ".9", a negative
    or non-numeric whole part, or a non-string/malformed value) rather
    than rounding or estimating, since only 0/1/2 thirds of an inning
    are ever valid in MLB's own inningsPitched convention.
    """
    if not pitching_stat:
        return None, {}, "missing_pitching_stat"

    raw_outs = pitching_stat.get("outs")
    if raw_outs is not None:
        outs = parse_nonnegative_int(raw_outs)
        if outs is not None:
            return outs, {"outs": raw_outs}, None
        # An `outs` field is present but malformed (e.g. 17.5, "abc",
        # a bool) -- fall through to inningsPitched rather than
        # silently truncating it.

    ip_raw = pitching_stat.get("inningsPitched")
    if ip_raw is None:
        return None, {}, "missing_innings_pitched"
    if isinstance(ip_raw, bool) or not isinstance(ip_raw, (str, int, float)):
        return None, {"inningsPitched": ip_raw}, "invalid_innings_pitched_format"
    if isinstance(ip_raw, float) and (math.isnan(ip_raw) or math.isinf(ip_raw)):
        return None, {"inningsPitched": ip_raw}, "invalid_innings_pitched_format"

    ip_str = str(ip_raw)
    whole_str, dot, frac_str = ip_str.partition(".")
    frac_str = frac_str if dot else "0"

    if not whole_str.isascii() or not whole_str.isdigit() or not frac_str.isascii() or not frac_str.isdigit():
        return None, {"inningsPitched": ip_raw}, "invalid_innings_pitched_format"
    if frac_str not in ("0", "1", "2"):
        return None, {"inningsPitched": ip_raw}, "invalid_innings_pitched_format"

    outs = int(whole_str) * 3 + int(frac_str)
    return outs, {"inningsPitched": ip_raw, "outsDerivedFromInningsPitched": True}, None


def extract_total_bases(batting_stat):
    """
    Pure. Returns (total_bases, fields_used, reason). Prefers an
    authoritative `totalBases` field (strictly parsed). Otherwise
    derives from hits/doubles/triples/homeRuns (each strictly parsed):
        singles = hits - doubles - triples - homeRuns
        totalBases = singles + 2*doubles + 3*triples + 4*homeRuns
    Rejects (never estimates) if any source field is missing,
    non-integral, negative, malformed, or internally inconsistent
    (derived singles < 0).
    """
    if not batting_stat:
        return None, {}, "missing_batting_stat"

    raw_total_bases = batting_stat.get("totalBases")
    if raw_total_bases is not None:
        total_bases = parse_nonnegative_int(raw_total_bases)
        if total_bases is not None:
            return total_bases, {"totalBases": raw_total_bases}, None
        # Malformed totalBases (e.g. 12.5) -- fall through to derivation
        # from components rather than silently truncating it.

    raw_hits, raw_doubles = batting_stat.get("hits"), batting_stat.get("doubles")
    raw_triples, raw_home_runs = batting_stat.get("triples"), batting_stat.get("homeRuns")
    components = {"hits": raw_hits, "doubles": raw_doubles, "triples": raw_triples, "homeRuns": raw_home_runs}

    if any(v is None for v in (raw_hits, raw_doubles, raw_triples, raw_home_runs)):
        return None, components, "missing_total_bases_components"

    hits = parse_nonnegative_int(raw_hits)
    doubles = parse_nonnegative_int(raw_doubles)
    triples = parse_nonnegative_int(raw_triples)
    home_runs = parse_nonnegative_int(raw_home_runs)
    if any(v is None for v in (hits, doubles, triples, home_runs)):
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
    raw_value = stat.get(field_name)
    if raw_value is None:
        return None, {}, f"missing_{field_name}"
    value = parse_nonnegative_int(raw_value)
    if value is None:
        return None, {field_name: raw_value}, f"invalid_{field_name}"
    return value, {field_name: raw_value}, None


def extract_hits_runs_rbis(batting_stat):
    """Pure. hits + runs + rbi, all three required (never partially guessed), each strictly parsed."""
    if not batting_stat:
        return None, {}, "missing_batting_stat"
    raw_hits, raw_runs, raw_rbi = batting_stat.get("hits"), batting_stat.get("runs"), batting_stat.get("rbi")
    components = {"hits": raw_hits, "runs": raw_runs, "rbi": raw_rbi}
    if any(v is None for v in (raw_hits, raw_runs, raw_rbi)):
        return None, components, "missing_hits_runs_rbi_components"

    hits = parse_nonnegative_int(raw_hits)
    runs = parse_nonnegative_int(raw_runs)
    rbi = parse_nonnegative_int(raw_rbi)
    if any(v is None for v in (hits, runs, rbi)):
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
