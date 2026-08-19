#!/usr/bin/env python3
"""
lib/kalshi_ticker_time.py
=================================
Canonical elapsed-clock-minutes distance between two Kalshi-ticker-style
'HHMM' time strings (e.g. the digits embedded in
'KXMLBHIT-26AUG181940ATHKC' -- '1940' = 7:40 PM ET). Extracted as ONE
small, shared helper (PR #93 review) after a real bug was found in TWO
independent doubleheader-disambiguation implementations --
scripts/build_hitter_projection_board.py's `_raw_markets_for_game` and
scripts/discover_kalshi_mlb_markets.py's `resolve_game_match` -- both of
which computed `abs(int(a) - int(b))` directly on the raw 'HHMM'
strings, treating them as plain integers. That is mathematically wrong
across an hour boundary: '1255' vs '1305' are 10 real minutes apart, but
`int('1305') - int('1255') == 50`. A later, more distant same-hour
candidate (e.g. '1330', raw difference 25) could therefore be preferred
over the true closest game. This module fixes both call sites by
computing genuine elapsed clock-minutes instead of raw integer
subtraction on the HHMM digits.

NOT circular/wraparound: every real caller compares two-or-more games on
the SAME calendar date sharing the same away/home team pair (a
doubleheader) -- this repository's data model keys doubleheader
candidates by (date, away, home) (scripts/discover_kalshi_mlb_markets.py's
build_slate_index) or by (away, home) scoped to one single-date slate
document (scripts/build_hitter_projection_board.py's
build_game_time_lookup), so a genuine midnight-UTC-or-ET rollover
between two candidates being compared is not a real scenario this
comparison needs to handle (two legs of one doubleheader are always
logged under the same `date`). Linear (non-modular) elapsed-minutes
distance is therefore the correct metric here, not a shortest-path-
around-the-clock distance.
"""


def hhmm_to_minutes(hhmm):
    """'HHMM' (exactly 4 digits, zero-padded, e.g. '0905') -> minutes since midnight
    (0-1439), or None if `hhmm` isn't a valid 4-digit 24-hour time string."""
    if not hhmm or not isinstance(hhmm, str) or len(hhmm) != 4 or not hhmm.isdigit():
        return None
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def hhmm_distance_minutes(a_hhmm, b_hhmm):
    """
    True elapsed clock-minutes between two 'HHMM' strings on the same calendar
    day -- e.g. hhmm_distance_minutes('1255', '1305') == 10 (never the raw
    integer-subtraction bug's 50; see this module's own docstring). Returns
    None if either string fails to parse as a valid HHMM time (never guessed
    as 0 or any other fabricated value).
    """
    a_minutes = hhmm_to_minutes(a_hhmm)
    b_minutes = hhmm_to_minutes(b_hhmm)
    if a_minutes is None or b_minutes is None:
        return None
    return abs(a_minutes - b_minutes)


def closest_by_hhmm(target_hhmm, candidates, *, key):
    """
    Pure. `candidates`: a non-empty list of items; `key(item)` returns that
    item's own 'HHMM' string. Returns (best_item, is_unique_closest_bool).
    `is_unique_closest_bool` is False whenever `target_hhmm` fails to parse,
    no candidate's own key parses, or two-or-more candidates are genuinely
    tied for closest -- a real ambiguity a caller must not silently resolve
    by picking whichever candidate a tie-break happened to return first.
    `best_item` is still returned in every case (the first candidate, or the
    first tied one) so a caller with a "fall back to earliest" policy can use
    it directly; a caller that must never guess checks the bool instead.
    """
    target_minutes = hhmm_to_minutes(target_hhmm)
    if target_minutes is None:
        return candidates[0], False
    scored = [(abs(target_minutes - m), c) for c in candidates
              for m in [hhmm_to_minutes(key(c))] if m is not None]
    if not scored:
        return candidates[0], False
    min_dist = min(d for d, _ in scored)
    best = [c for d, c in scored if d == min_dist]
    return best[0], (len(best) == 1)
