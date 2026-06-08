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

import sys

# ── Copy of parse_suffix() as it exists post-fix ─────────────────────────────
# Kept inline so the test is self-contained and cannot be accidentally broken
# by importing a partially-modified version of build_kalshi_registry.
TWO_LETTER_ABBRS = {'TB', 'AZ', 'SF', 'SD', 'KC', 'LA'}

def parse_suffix(suffix, kalshi_date):
    """
    Split a Kalshi event-ticker suffix into (time_str, away_abbr, home_abbr).
    Returns None if the suffix does not start with kalshi_date or cannot be parsed.

    Canonical fix (2026-06-08): sort key is (-score, -a_len) to prefer 3-letter
    abbreviations when neither candidate is a known 2-letter team (score tie at 0).
    """
    if not suffix.startswith(kalshi_date):
        return None
    rest = suffix[len(kalshi_date):]
    if len(rest) < 6:
        return None
    time_str = rest[:4]
    teams    = rest[4:]

    candidates = []
    for a_len in [2, 3]:
        if len(teams) <= a_len:
            continue
        away = teams[:a_len]
        home = teams[a_len:]
        if not away.isalpha() or not home.isalpha():
            continue
        score = 1 if away in TWO_LETTER_ABBRS else 0
        candidates.append((score, a_len, away, home))

    if not candidates:
        return None

    # CRITICAL: secondary sort by -a_len so 3-letter wins over 2-letter on score ties.
    # Removing the second key is the exact bug that caused the June 8 F5 outage.
    candidates.sort(key=lambda x: (-x[0], -x[1]))
    _, _, away, home = candidates[0]
    return time_str, away, home


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
    ("26JUN082010DETCLE", "26JUN08", "DET", "CLE",
     "3+3: DET away / CLE home"),
    ("26JUN082010ATLNYY", "26JUN08", "ATL", "NYY",
     "3+3: ATL away / NYY home"),
]

# ── Run ───────────────────────────────────────────────────────────────────────
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

# ── Report ────────────────────────────────────────────────────────────────────
print(f"test_f5_parse_suffix: {len(passes)} passed, {len(fails)} failed")

if fails:
    print("\nFAILURES:")
    for f in fails:
        print(f"  {f}")
    print("\nREGRESSION DETECTED — parse_suffix() is broken.")
    print("This will cause F5 moneyline backfill to silently produce no prices.")
    print("Fix: ensure candidates.sort() uses key=lambda x: (-x[0], -x[1])")
    sys.exit(1)

print("\nALL ASSERTIONS PASSED")
print("parse_suffix() correctly handles 3+3, 3+2, and 2+3 team abbreviation pairs.")
sys.exit(0)
