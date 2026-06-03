"""
enrich_data.py — v2.0
Changes from v1:
  - Renamed wrcPlus → rpgIndex (honest label for what it actually is: season R/G normalized to 100)
  - Removed circular wRC+_implied_R/G conversion (it was rpg → index → rpg again)
  - Added wrcSource field documenting the data limitation
  - Added leagueRpG to teamstats for reference
  - Platoon split estimation unchanged
"""
import json

LEAGUE_AVG_RPG = 4.5

with open('data/teamstats.json') as f:
    ts = json.load(f)
with open('data/slate.json') as f:
    slate = json.load(f)

ts_teams = ts.get('teams', {})
for abbr, t in ts_teams.items():
    rec  = t.get('record', {})
    rs   = rec.get('runsScored', 0) or 0
    gp   = (rec.get('wins', 0) or 0) + (rec.get('losses', 0) or 0)
    if gp > 0 and rs > 0:
        rpg = rs / gp
        # rpgIndex: season R/G normalized to 100 at league average.
        # This is NOT park-adjusted wRC+. Use for relative offense comparison only.
        t['rpgIndex']    = round(rpg / LEAGUE_AVG_RPG * 100)
        t['runsPerGame'] = round(rpg, 2)
    else:
        t['rpgIndex']    = 100
        t['runsPerGame'] = None

# Keep wrcPlus as alias so downstream JSON consumers don't break immediately
# Mark it deprecated — remove after one full model audit cycle
for abbr, t in ts_teams.items():
    t['wrcPlus'] = t['rpgIndex']  # alias, deprecated

ts['rpgIndexSource'] = 'season_rpg_normalized'  # renamed from wrcSource
ts['wrcSource']      = 'season_rpg_normalized'  # keep for compat
ts['lgRpG']          = LEAGUE_AVG_RPG

with open('data/teamstats.json', 'w') as f:
    json.dump(ts, f)

enriched = 0
for game in slate.get('games', []):
    for side_key, abbr_key in [('awayTeamStats', 'away'), ('homeTeamStats', 'home')]:
        abbr = game.get(abbr_key, {}).get('abbr')
        if not abbr or abbr not in ts_teams:
            continue
        td   = ts_teams[abbr]
        stats = game.setdefault(side_key, {})
        stats['rpgIndex']    = td.get('rpgIndex')
        stats['wrcPlus']     = td.get('rpgIndex')   # deprecated alias
        stats['last7RpG']    = td.get('last7RpG')
        stats['last15RpG']   = td.get('last15RpG')
        stats['runsPerGame'] = td.get('runsPerGame')
        enriched += 1

# Platoon split estimation — unchanged
platoon_fixed = 0
for game in slate.get('games', []):
    for side in ['away', 'home']:
        ps = game.get(side, {}).get('pitcherSavant')
        if not ps:
            continue
        overall_k = ps.get('kPct') or 0
        if overall_k <= 0:
            continue
        for split_key, factor in [('vsLHH', 0.92), ('vsRHH', 1.05)]:
            sp = ps.get(split_key)
            if sp and sp.get('pa', 0) >= 20 and (sp.get('kPct') or 0) == 0:
                sp['kPct']      = round(overall_k * factor, 1)
                sp['estimated'] = True
                platoon_fixed  += 1

with open('data/slate.json', 'w') as f:
    json.dump(slate, f)

print(f'Enriched {enriched} team stat blocks, fixed {platoon_fixed} platoon splits')
print(f'Note: wrcPlus field kept as deprecated alias for rpgIndex — remove after full audit')
