#!/usr/bin/env python3
"""
scripts/validate_platoon_first_inning_wiring.py
=====================================================
One-off validation script (NOT part of the fetch-slate pipeline, not
wired into any workflow) for the Baseball Input Data / Platoon Context
mission. Loads real games from a committed historical
data/slates/<date>/authoritative.json snapshot and runs them through
build_market_ledger.compute_game_projection_context() /
evaluate_game() twice:

  1. AS-IS (BEFORE) -- the exact real game dict from the snapshot. Every
     committed snapshot predates this mission, so `confirmedLineup`,
     starter `pitchHand`, `vsLHH`/`vsRHH`, and `firstInningSplit` are
     all genuinely absent on every game (confirmed by direct
     inspection -- see this mission's PR description) -- this
     reproduces, byte-for-byte, the OLD behavior this mission changes.
  2. AFTER -- the SAME real game (same odds, same xFIP, same park
     factor, same offenseBaselineAdj) with realistic ILLUSTRATIVE
     platoon/first-inning fields layered on top, since no committed
     snapshot has real fetched values for these new fields yet (the
     fetch paths that populate them are new/newly-broadened by this
     mission and have not run in production against a live slate as
     of this PR). Every injected value is labeled as illustrative in
     the printed output.

Usage: python3 scripts/validate_platoon_first_inning_wiring.py
"""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'scripts'))

import build_market_ledger as bml

SLATE_PATH = 'data/slates/2026-08-10/authoritative.json'


def load_game(away_abbr, home_abbr):
    with open(os.path.join(ROOT, SLATE_PATH)) as f:
        slate = json.load(f)
    for g in slate['games']:
        if g['away']['abbr'] == away_abbr and g['home']['abbr'] == home_abbr:
            return g
    raise SystemExit(f'{away_abbr}@{home_abbr} not found in {SLATE_PATH}')


def nrfi_yrfi_probs(g):
    rows = bml.evaluate_game(g)
    yrfi = next(r for r in rows if r['market'] == 'YRFI')
    nrfi = next(r for r in rows if r['market'] == 'NRFI')
    return nrfi, yrfi


def confirmed_lineup(hands, split_hand, split_woba_by_slot, season_woba=0.320):
    lineup = []
    for i, h in enumerate(hands, start=1):
        entry = {
            'order': i, 'playerId': f'illustrative_{i}', 'name': f'Batter {i}',
            'batSide': h, 'seasonWOBA': season_woba, 'platoonSplits': None,
        }
        woba = split_woba_by_slot.get(i)
        if woba is not None:
            key = 'vsLHP' if split_hand == 'L' else 'vsRHP'
            entry['platoonSplits'] = {key: {'woba': woba, 'pa': 120}}
        lineup.append(entry)
    return lineup


def section(title):
    print('\n' + '=' * 78)
    print(title)
    print('=' * 78)


def report_projection_move(label, g_before, g_after, note):
    before = bml.compute_game_projection_context(g_before)
    after = bml.compute_game_projection_context(g_after)
    print(f'\n--- {label} ---')
    print(f'  {note}')
    print(f'  awayProjRuns: {before["awayProjRuns"]:.3f} -> {after["awayProjRuns"]:.3f} '
          f'({after["awayProjRuns"] - before["awayProjRuns"]:+.3f})')
    print(f'  f5AwayProj:   {before["f5AwayProj"]:.3f} -> {after["f5AwayProj"]:.3f} '
          f'({after["f5AwayProj"] - before["f5AwayProj"]:+.3f})')
    print(f'  awayPlatoonContext.status: {after["awayPlatoonContext"]["status"]}')
    print(f'  awayPlatoonContext.aggregatePlatoonAdvantageRPG: '
          f'{after["awayPlatoonContext"]["aggregatePlatoonAdvantageRPG"]:+.4f}')
    print(f'  awayPlatoonContext.reason: {after["awayPlatoonContext"]["reason"]}')


def main():
    # ── Example 1: meaningful platoon advantage ────────────────────────────
    section('EXAMPLE 1 -- Meaningful platoon advantage (illustrative confirmedLineup/starter split)')
    g1 = load_game('BOS', 'TOR')
    print(f'Real game: BOS @ TOR | homeXFIP={g1["home"]["pitcherSavant"].get("xFIP")} '
          f'| park={g1["park"].get("parkFactor")} | awayTeamSeasonWOBA(real)={g1["awayTeamStats"].get("teamSeasonWOBA")}')

    home_baseline_xfip = g1['home']['pitcherSavant'].get('xFIP')  # real, e.g. 6.61
    g1_after = copy.deepcopy(g1)
    g1_after['home']['pitcher']['pitchHand'] = 'R'  # illustrative
    # Illustrative vsLHH split WORSE than the starter's own real season
    # baseline (not an arbitrary absolute number) -- this is what makes it
    # a genuine "vulnerable to this specific lineup's handedness" signal
    # rather than just "a below-average pitcher" (which the baseline
    # already captures via offenseBaselineAdj/xFIP on both sides).
    g1_after['home']['pitcherSavant']['vsLHH'] = {'xERA': round(home_baseline_xfip + 1.2, 2), 'pa': 140}  # illustrative
    lineup = confirmed_lineup(
        ['L'] * 9, split_hand='R',
        split_woba_by_slot={1: 0.415, 2: 0.440, 3: 0.405, 4: 0.360, 5: 0.350, 6: 0.340, 7: 0.330, 8: 0.310, 9: 0.300},
        season_woba=g1['awayTeamStats'].get('teamSeasonWOBA') or 0.320,
    )  # illustrative -- a heavily left-handed, RHP-mashing lineup
    g1_after['awayTeamStats']['confirmedLineup'] = lineup

    report_projection_move(
        'BOS @ TOR', g1, g1_after,
        note=f'Illustrative: BOS runs out an all-lefty top-heavy lineup that mashes RHP (top-3 wOBA ~.42-.44 '
             f'vs RHP, vs team season wOBA {g1["awayTeamStats"].get("teamSeasonWOBA")}), and TOR\'s real starter '
             f'(season xFIP={home_baseline_xfip}) is given an illustrative vsLHH split 1.2 runs WORSE than his '
             f'own baseline ({round(home_baseline_xfip + 1.2, 2)} xERA).',
    )

    # ── Example 2: roughly neutral game ─────────────────────────────────────
    section('EXAMPLE 2 -- Roughly neutral game (mixed-handedness lineup, no strong starter split)')
    g2 = load_game('NYM', 'ATL')
    home_baseline_xfip2 = g2['home']['pitcherSavant'].get('xFIP')
    team_woba2 = g2['awayTeamStats'].get('teamSeasonWOBA') or 0.320
    g2_after = copy.deepcopy(g2)
    g2_after['home']['pitcher']['pitchHand'] = 'R'
    # Illustrative: BOTH hand splits set EXACTLY equal to the starter's own
    # real season baseline -- by construction, no vulnerability to either
    # lineup handedness beyond what offenseBaselineAdj/xFIP already price.
    g2_after['home']['pitcherSavant']['vsLHH'] = {'xERA': home_baseline_xfip2, 'pa': 130}
    g2_after['home']['pitcherSavant']['vsRHH'] = {'xERA': home_baseline_xfip2, 'pa': 130}
    lineup2 = confirmed_lineup(
        ['R', 'L', 'R', 'S', 'R', 'L', 'R', 'L', 'R'], split_hand='R',
        split_woba_by_slot={i: team_woba2 for i in range(1, 10)},
        season_woba=team_woba2,
    )
    # A switch hitter's vsLHP split is also needed here since the mixed
    # lineup includes one (facing an R-handed starter their EFFECTIVE side
    # is L, but their own vsLHP/vsRHP splits are still keyed by pitcher
    # hand -- see lib.research.platoon_context's own note on this).
    for h in lineup2:
        h['platoonSplits'] = {'vsLHP': {'woba': team_woba2, 'pa': 120}, 'vsRHP': {'woba': team_woba2, 'pa': 120}}
    g2_after['awayTeamStats']['confirmedLineup'] = lineup2

    report_projection_move(
        'NYM @ ATL', g2, g2_after,
        note=f'Illustrative: mixed-handedness lineup with every hitter\'s vs-hand wOBA set EXACTLY equal to the '
             f'team\'s own season baseline ({team_woba2}), and starter\'s split set exactly equal to his own '
             f'real season xFIP ({home_baseline_xfip2}) for both hands -- by construction, no platoon edge '
             f'either way.',
    )

    # ── Example 3: NRFI/YRFI dedicated first-inning evidence ───────────────
    section('EXAMPLE 3 -- NRFI/YRFI dedicated first-inning evidence changes fair probability')
    g3 = load_game('PHI', 'STL')
    nrfi_before, yrfi_before = nrfi_yrfi_probs(g3)
    print(f'Real game: PHI @ STL | Kalshi YRFI={g3["odds"]["kalshi"].get("nrfi_yrfi", {}).get("yrfi_american")}')
    print(f'  BEFORE (naive proj/9, no dedicated evidence):')
    print(f'    NRFI modelProb={nrfi_before["modelProb"]}%  YRFI modelProb={yrfi_before["modelProb"]}%')
    print(f'    firstInningContext.dedicatedEvidenceApplied={nrfi_before["firstInningContext"]["dedicatedEvidenceApplied"]}')

    g3_after = copy.deepcopy(g3)
    # Illustrative: STL's starter has a real, adequately-sampled dedicated
    # first-inning weakness (well above his full-game run-prevention level).
    g3_after['home']['pitcherSavant']['firstInningSplit'] = {
        'firstInningXERA': 6.80, 'appearances': 14,
    }
    nrfi_after, yrfi_after = nrfi_yrfi_probs(g3_after)
    print(f'  AFTER (STL starter illustrative firstInningXERA=6.80, appearances=14):')
    print(f'    NRFI modelProb={nrfi_after["modelProb"]}%  YRFI modelProb={yrfi_after["modelProb"]}%')
    print(f'    firstInningContext.dedicatedEvidenceApplied={nrfi_after["firstInningContext"]["dedicatedEvidenceApplied"]}')
    print(f'    awayLambdaFormula: {nrfi_after["firstInningContext"]["awayLambdaFormula"]}')
    print(f'  Movement: NRFI {nrfi_before["modelProb"]}% -> {nrfi_after["modelProb"]}% '
          f'({nrfi_after["modelProb"] - nrfi_before["modelProb"]:+.2f}pp), '
          f'YRFI {yrfi_before["modelProb"]}% -> {yrfi_after["modelProb"]}% '
          f'({yrfi_after["modelProb"] - yrfi_before["modelProb"]:+.2f}pp)')
    print(f'  F5/full-game isolation check: awayProjRuns/f5AwayProj must NOT move from this first-inning-only field:')
    ctx_before = bml.compute_game_projection_context(g3)
    ctx_after = bml.compute_game_projection_context(g3_after)
    print(f'    awayProjRuns {ctx_before["awayProjRuns"]} -> {ctx_after["awayProjRuns"]} '
          f'(unchanged={ctx_before["awayProjRuns"] == ctx_after["awayProjRuns"]})')
    print(f'    f5AwayProj   {ctx_before["f5AwayProj"]} -> {ctx_after["f5AwayProj"]} '
          f'(unchanged={ctx_before["f5AwayProj"] == ctx_after["f5AwayProj"]})')


if __name__ == '__main__':
    main()
