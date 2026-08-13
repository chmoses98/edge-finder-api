"""
lib/edgelab/research_splits.py
===================================
EdgeLab Research Trustworthiness milestone: chronological (date-based,
NEVER contract-random) DEVELOPMENT/VALIDATION/HOLDOUT split
infrastructure for future strategy-validation research (spec section
15).

Splitting by individual contract/observation would leak information:
thousands of highly-correlated contracts come from a much smaller
number of games (see lib.edgelab.research_stats's whole reason for
existing), so a random per-contract shuffle would routinely put some of
a game's contracts in DEVELOPMENT and others from the SAME game in
HOLDOUT -- the two partitions would no longer be independent. This
module only ever splits by GAME DATE, preserving each date's full row
set on one side of the boundary.

This module does not itself run any strategy-discovery workflow -- it
only computes and labels the partition. The intended workflow (spec
section 15, enforced by convention/documentation here, not by a runtime
guard that can't tell a report author's real intent):
  1. discover signal on DEVELOPMENT,
  2. decide a rule,
  3. test once on VALIDATION,
  4. freeze the rule,
  5. evaluate on untouched HOLDOUT.
Never re-tune a rule after seeing HOLDOUT results.
"""

DEVELOPMENT = "DEVELOPMENT"
VALIDATION = "VALIDATION"
HOLDOUT = "HOLDOUT"

DEFAULT_SPLIT_RATIOS = {DEVELOPMENT: 0.6, VALIDATION: 0.2, HOLDOUT: 0.2}

# Below this many distinct dates, a chronological split is real but not
# yet meaningful for validation purposes -- each partition would cover
# too few games to say anything. Illustrative threshold, documented, not
# a hard technical limit.
MIN_DATES_FOR_MATURE_SPLIT = 30

MATURITY_FRAMEWORK_ONLY = "FRAMEWORK_ONLY_INSUFFICIENT_DATES"
MATURITY_USABLE = "USABLE"


def chronological_split(dates, ratios=None):
    """
    `dates`: iterable of gameDate strings (any order, duplicates fine).
    Sorted ascending then partitioned strictly BY POSITION (earliest
    dates -> DEVELOPMENT, latest -> HOLDOUT) -- never shuffled, so a
    date's full row set always stays together on one side.

    `ratios` must sum to 1.0 (validated) and use exactly the three keys
    DEVELOPMENT/VALIDATION/HOLDOUT; defaults to 60/20/20 (spec's
    suggested default).

    Returns a dict: {"DEVELOPMENT": [dates], "VALIDATION": [dates],
    "HOLDOUT": [dates], "totalDates": n, "maturity": ..., "ratiosUsed": ...}.
    An empty `dates` input returns three empty lists and
    maturity=FRAMEWORK_ONLY_INSUFFICIENT_DATES, never an error.
    """
    ratios = ratios or DEFAULT_SPLIT_RATIOS
    if set(ratios) != {DEVELOPMENT, VALIDATION, HOLDOUT}:
        raise ValueError(f"ratios must have exactly the keys {DEVELOPMENT}/{VALIDATION}/{HOLDOUT}, got {sorted(ratios)}")
    if abs(sum(ratios.values()) - 1.0) > 1e-9:
        raise ValueError(f"ratios must sum to 1.0, got {sum(ratios.values())}")

    unique_sorted = sorted(set(d for d in dates if d))
    n = len(unique_sorted)
    dev_end = round(n * ratios[DEVELOPMENT])
    val_end = dev_end + round(n * ratios[VALIDATION])

    maturity = MATURITY_FRAMEWORK_ONLY if n < MIN_DATES_FOR_MATURE_SPLIT else MATURITY_USABLE

    return {
        DEVELOPMENT: unique_sorted[:dev_end],
        VALIDATION: unique_sorted[dev_end:val_end],
        HOLDOUT: unique_sorted[val_end:],
        "totalDates": n,
        "maturity": maturity,
        "ratiosUsed": dict(ratios),
    }


def assign_split(game_date, split_map):
    """
    The split label ("DEVELOPMENT"/"VALIDATION"/"HOLDOUT") for one
    gameDate, given a `split_map` from chronological_split(). Returns
    None (never a guess) for a date not present in any partition (e.g.
    a null gameDate on the row).
    """
    for label in (DEVELOPMENT, VALIDATION, HOLDOUT):
        if game_date in split_map.get(label, ()):
            return label
    return None


def label_rows_with_split(rows, split_map, date_key="gameDate"):
    """Returns a NEW list of rows (shallow copies), each with a 'researchSplit' key added -- never mutates the input rows."""
    return [dict(r, researchSplit=assign_split(r.get(date_key), split_map)) for r in rows]
