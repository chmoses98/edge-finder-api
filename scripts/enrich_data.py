"""
enrich_data.py — v4.0
Changes from v3:
  - Opponent quality adjustment now uses full 15-game rolling window from oppquality.json
    (replaces single-game proxy from v3)
  - oppquality.json is fetched by fetch-slate.yml and written to data/ before this script runs
  - All other enrichments unchanged (wOBA, offFBPct, HL bullpen, platoon splits)
"""
import json

LEAGUE_AVG_RPG  = 4.5
LEAGUE_AVG_XFIP = 4.00

# ── Load data files ──────────────────────────────────────────────────────────
with open('data/teamstats.json') as f:
    ts = json.load(f)
with open('data/slate.json') as f:
    slate = json.load(f)
with open('data/bullpen.json') as f:
    bullpen_data = json.load(f)

# Load oppquality.json — full rolling opponent xFIP data
try:
    with open('data/oppquality.json') as f:
        oppq = json.load(f)
    opp_teams = oppq.get('teams', {})
    print(f'oppquality.json loaded: {len(opp_teams)} teams')
except Exception as e:
    print(f'WARNING: oppquality.json not found or invalid ({e}) — opp quality adj will be null')
    opp_teams = {}

ts_teams = ts.get('teams', {})

# ── Step 1: Enrich teamstats with rpgIndex ───────────────────────────────────
for abbr, t in ts_teams.items():
    rec = t.get('record', {})
    rs  = rec.get('runsScored', 0) or 0
    gp  = (rec.get('wins', 0) or 0) + (rec.get('losses', 0) or 0)
    if gp > 0 and rs > 0:
        rpg = rs / gp
        t['rpgIndex']    = round(rpg / LEAGUE_AVG_RPG * 100)
        t['runsPerGame'] = round(rpg, 2)
    else:
        t['rpgIndex']    = 100
        t['runsPerGame'] = None
    t['wrcPlus'] = t['rpgIndex']  # deprecated alias

ts['rpgIndexSource'] = 'season_rpg_normalized'
ts['wrcSource']      = 'season_rpg_normalized'
ts['lgRpG']          = LEAGUE_AVG_RPG

with open('data/teamstats.json', 'w') as f:
    json.dump(ts, f)

# ── Step 2: Enrich slate game blocks ─────────────────────────────────────────
enriched = 0
opp_quality_resolved = 0
opp_quality_missing  = 0

for game in slate.get('games', []):
    for side_key, abbr_key in [('awayTeamStats', 'away'), ('homeTeamStats', 'home')]:
        abbr = game.get(abbr_key, {}).get('abbr')
        if not abbr or abbr not in ts_teams:
            continue
        td    = ts_teams[abbr]
        stats = game.setdefault(side_key, {})

        # Core stats
        stats['rpgIndex']    = td.get('rpgIndex')
        stats['wrcPlus']     = td.get('rpgIndex')
        stats['last7RpG']    = td.get('last7RpG')
        stats['last15RpG']   = td.get('last15RpG')
        stats['runsPerGame'] = td.get('runsPerGame')

        # wOBA and offensive FB% for lineup adj and park factor modifier
        stats['teamWOBA']    = td.get('teamWOBA')
        stats['teamFBPct']   = td.get('teamFBPct')

        # ── Opponent quality adjustment (now full rolling window) ────────────
        oq = opp_teams.get(abbr, {})
        opp_xfip_avg   = oq.get('oppXFIPavg')
        opp_quality_adj = oq.get('oppQualityAdj')
        games_resolved = oq.get('gamesResolved', 0)
        confidence     = oq.get('confidence', 'low')

        stats['oppXFIPavg']       = opp_xfip_avg
        stats['oppQualityAdj']    = opp_quality_adj
        stats['oppQualityGames']  = games_resolved
        stats['oppQualityConf']   = confidence
        stats['oppQualityNote']   = 'rolling_15_game' if opp_xfip_avg is not None else 'unavailable'

        if opp_xfip_avg is not None:
            opp_quality_resolved += 1
        else:
            opp_quality_missing  += 1

        enriched += 1

    # ── Enrich bullpen blocks with high-leverage xFIP ────────────────────────
    bullpens = bullpen_data.get('bullpens', {})
    for side in ['away', 'home']:
        abbr = game.get(side, {}).get('abbr')
        if not abbr or abbr not in bullpens:
            continue
        bp      = bullpens[abbr]
        game_bp = game.get(side, {}).get('bullpen')
        if game_bp is None:
            game_bp = {}
            game[side]['bullpen'] = game_bp

        game_bp['hlXFIP']       = bp.get('hlXFIP')
        game_bp['hlGrade']      = bp.get('hlGrade')
        game_bp['hlAvailable']  = bp.get('hlAvailable', False)
        game_bp['hlDivergence'] = bp.get('hlDivergence')
        game_bp['hlSamplePA']   = bp.get('hlSamplePA')

# ── Step 3: Platoon split estimation (unchanged) ─────────────────────────────
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
print(f'Opp quality adj: {opp_quality_resolved} resolved, {opp_quality_missing} missing (rolling 15-game)')
print(f'wOBA: from teamWOBA field | offFBPct: from teamFBPct field')
print(f'HL bullpen: hlXFIP merged into all bullpen blocks')
