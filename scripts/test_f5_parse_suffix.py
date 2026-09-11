#!/usr/bin/env python3
"""
scripts/test_f5_parse_suffix.py
================================
Regression test for the parse_suffix() bug fixed on 2026-06-08.

Bug: when AWAY and HOME team abbreviations are both 3 letters (neither in
TWO_LETTER_ABBRS), parse_suffix preferred a 2-letter split (e.g. "SE"/"ABAL"
instead of "SEA"/"BAL") because the sort key only ranked by score and left the
a_len=2 candidate first on ties.

Fix: sort key changed to (-score, -a_len) so that 3-letter splits win on score ties.

This test is the canonical regression guard.  If it fails, the backfill in
backfill_from_search() will silently produce no F5 prices.

Run standalone:  python3 scripts/test_f5_parse_suffix.py
Exit 0 = all assertions passed.
Exit 1 = regression detected.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.kalshi_mlb_contract_parser import parse_raw_event_suffix  # noqa: E402


def parse_suffix(suffix, kalshi_date):
    """
    Split a Kalshi event-ticker suffix into (time_str, away_abbr, home_abbr).
    Returns None if the suffix does not start with kalshi_date or cannot be parsed.

    Canonical fix (2026-06-08): prefer 3-letter abbreviations when neither
    candidate is a known 2-letter team. The CASES below are still the eight real
    suffixes that broke on June 8 plus the 2-letter cases, and they are still
    what guards that rule.

    W1-C micro-fix 2: this used to hold its OWN inline copy of the split, on the
    reasoning that a self-contained harness cannot be broken by a
    partially-modified builder. That protection stopped being worth its cost the
    moment the real rule moved: `build_kalshi_registry.parse_suffix` now
    delegates to the canonical parser, so an inline copy would be a harness
    asserting things about code nobody runs -- and it would have kept passing
    while the real parser regressed. It reads the canonical parser directly now,
    which is a pure library module with no import-time I/O, so the original
    hazard (importing the builder, which fires live HTTP at import) does not
    apply. `G1`/`G2` cases below are the marker this copy could never read.
    """
    if not suffix.startswith(kalshi_date):
        return None
    parsed = parse_raw_event_suffix(suffix)
    if not parsed['parsed']:
        return None
    return parsed['time_str'], parsed['away'], parsed['home']


# ── Test cases ────────────────────────────────────────────────────────────────
# Format: (suffix, kalshi_date, expected_away, expected_home, description)
# All 8 suffixes that were broken before the fix, plus known-good 2-letter cases.

CASES = [
    # 3+3 letter pairs — these all produced garbage splits before the fix
    ("26JUN081835SEABAL", "26JUN08", "SEA", "BAL",
     "3+3: SEA away / BAL home (bug produced SE/ABAL)"),
    ("26JUN081840NYYCLE", "26JUN08", "NYY", "CLE",
     "3+3: NYY away / CLE home (bug produced NY/YCLE)"),
    ("26JUN081907PHITOR", "26JUN08", "PHI", "TOR",
     "3+3: PHI away / TOR home (bug produced PH/ITOR)"),
    ("26JUN082138HOULAA", "26JUN08", "HOU", "LAA",
     "3+3: HOU away / LAA home (bug produced HO/ULAA)"),
    ("26JUN082205MILATH", "26JUN08", "MIL", "ATH",
     "3+3: MIL away / ATH home (bug produced MI/LATH)"),

    # 3+2 pairs (home is 2-letter but AWAY is 3-letter — score=0 for away, still need 3-letter split)
    ("26JUN081840BOSTB",  "26JUN08", "BOS", "TB",
     "3+2: BOS away / TB home (bug produced BO/STB)"),
    ("26JUN082140CINSD",  "26JUN08", "CIN", "SD",
     "3+2: CIN away / SD home (bug produced CI/NSD)"),
    ("26JUN082145WSHSF",  "26JUN08", "WSH", "SF",
     "3+2: WSH away / SF home (bug produced WS/HSF)"),

    # 2-letter AWAY teams — score=1, must still work after fix
    ("26JUN081340TBMIA",  "26JUN08", "TB",  "MIA",
     "2+3: TB away (2-letter) / MIA home — must not regress"),
    ("26JUN081340SFCHC",  "26JUN08", "SF",  "CHC",
     "2+3: SF away (2-letter) / CHC home — must not regress"),
    ("26JUN081340KCMIN",  "26JUN08", "KC",  "MIN",
     "2+3: KC away (2-letter) / MIN home — must not regress"),
    ("26JUN081340AZCOL",  "26JUN08", "AZ",  "COL",
     "2+3: AZ away (2-letter) / COL home — must not regress"),

    # Edge: suffix from different date — must return None
    ("26JUN07SEABAL",     "26JUN08", None, None,
     "wrong date prefix — must return None"),

    # Edge: teams string too short — must return None
    ("26JUN081840AB",     "26JUN08", None, None,
     "teams string too short — must return None"),

    # Additional 3+3 pairs from other common matchups
    ("26JUN082010PITHOU", "26JUN08", "PIT", "HOU",
     "3+3: PIT away / HOU home"),

    # Regression guard: LAD away against PIT — 'LA' was in TWO_LETTER_ABBRS causing
    # parse_suffix to prefer the 2+4 split ('LA'/'DPIT') over the correct 3+3 ('LAD'/'PIT')
    ("26JUN111840LADPIT", "26JUN11", "LAD", "PIT",
     "3+3: LAD away / PIT home — guards LA-in-TWO_LETTER_ABBRS bug"),
    ("26JUN082010DETCLE", "26JUN08", "DET", "CLE",
     "3+3: DET away / CLE home"),
    ("26JUN082010ATLNYY", "26JUN08", "ATL", "NYY",
     "3+3: ATL away / NYY home"),

    # W1-C micro-fix 2. Kalshi's own doubleheader marker. The inline copy this
    # file used to carry required BOTH halves of the team segment to be
    # alphabetic, so a marked leg returned None and the leg was dropped -- and
    # this harness would have gone on reporting a pass while it happened.
    ("26SEP111305BOSNYYG1", "26SEP11", "BOS", "NYY",
     "doubleheader leg 1: G1 marker stripped, teams preserved"),
    ("26SEP111905BOSNYYG2", "26SEP11", "BOS", "NYY",
     "doubleheader leg 2: G2 marker stripped, teams preserved"),
    ("26SEP112140SDSFG1", "26SEP11", "SD", "SF",
     "doubleheader + 2-letter pair: both rules at once"),
]

# ── Run ───────────────────────────────────────────────────────────────────────
# WAVE 0.05A: the loop and report below used to run at MODULE SCOPE and end in
# a bare sys.exit(). This filename matches pytest's default `test_*.py`
# collection glob, so `python3 -m pytest` from the repo root imported it during
# collection and that module-scope sys.exit aborted the whole session with
# INTERNALERROR, running zero tests -- the same defect class as
# scripts/regression_test.py. Importing this module is now inert.


def run_cases():
    """Pure. Returns (passes, fails) over CASES. Prints nothing, exits nothing."""
    fails = []
    passes = []

    for suffix, kdate, exp_away, exp_home, desc in CASES:
        result = parse_suffix(suffix, kdate)

        if exp_away is None:
            # Expect None
            if result is not None:
                fails.append(f"FAIL [{desc}]\n"
                             f"  expected: None\n"
                             f"  got:      {result}")
            else:
                passes.append(desc)
            continue

        if result is None:
            fails.append(f"FAIL [{desc}]\n"
                         f"  expected: ({exp_away}, {exp_home})\n"
                         f"  got:      None")
            continue

        _, got_away, got_home = result
        if got_away != exp_away or got_home != exp_home:
            fails.append(f"FAIL [{desc}]\n"
                         f"  expected: away={exp_away!r} home={exp_home!r}\n"
                         f"  got:      away={got_away!r} home={got_home!r}")
        else:
            passes.append(desc)
    return passes, fails


def main(argv=None):
    """The CLI. Output text and exit codes are exactly the flat script's."""
    passes, fails = run_cases()

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"test_f5_parse_suffix: {len(passes)} passed, {len(fails)} failed")

    if fails:
        print("\nFAILURES:")
        for f in fails:
            print(f"  {f}")
        print("\nREGRESSION DETECTED — parse_suffix() is broken.")
        print("This will cause F5 moneyline backfill to silently produce no prices.")
        print("Fix: ensure candidates.sort() uses key=lambda x: (-x[0], -x[1])")
        return 1

    print("\nALL ASSERTIONS PASSED")
    print("parse_suffix() correctly handles 3+3, 3+2, and 2+3 team abbreviation pairs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
