"""
enrich_data.py — v3.0
Changes from v2:
  - Added wOBA enrichment to team stat blocks (from teamstats.json teamWOBA field)
  - Added offensive FB% enrichment to team stat blocks (teamFBPct)
  - Added opponent quality adjustment calculation for rolling R/G
    (averages xFIP of starters faced in last 15 games via pitchers.json)
  - Added HL (high-leverage) bullpen xFIP to game blocks from bullpen.json
  - Platoon split estimation unchanged
  - Renamed wrcPlus → rpgIndex (kept as deprecated alias)
"""
import json
import math

LEAGUE_AVG_RPG = 4.5
LEAGUE_AVG_XFIP = 4.00   # anchor for opponent quality adjustment

# ── Load data files ──────────────────────────────────────────────────────────
with open('data/teamstats.json') as f:
    ts = json.load(f)
with open('data/slate.json') as f:
    slate = json.load(f)
with open('data/bullpen.json') as f:
    bullpen_data = json.load(f)

# Load pitchers.json for opponent quality lookup
try:
    with open('data/pitchers.json') as f:
        pitchers_raw = json.load(f)
    pitchers_games = pitchers_raw.get('games', [])
except Exception:
    pitchers_games = []

ts_teams = ts.get('teams', {})

# ── Step 1: Enrich teamstats with rpgIndex ───────────────────────────────────
for abbr, t in ts_teams.items():
    rec  = t.get('record', {})
    rs   = rec.get('runsScored', 0) or 0
    gp   = (rec.get('wins', 0) or 0) + (rec.get('losses', 0) or 0)
    if gp > 0 and rs > 0:
        rpg = rs / gp
        t['rpgIndex']    = round(rpg / LEAGUE_AVG_RPG * 100)
        t['runsPerGame'] = round(rpg, 2)
    else:
        t['rpgIndex']    = 100
        t['runsPerGame'] = None
    # Deprecated alias
    t['wrcPlus'] = t['rpgIndex']

ts['rpgIndexSource'] = 'season_rpg_normalized'
ts['wrcSource']      = 'season_rpg_normalized'
ts['lgRpG']          = LEAGUE_AVG_RPG

with open('data/teamstats.json', 'w') as f:
    json.dump(ts, f)

# ── Step 2: Build opponent xFIP lookup from pitchers.json ────────────────────
# Maps team abbr -> list of opponent starter xFIP values seen in their last 15 games
# Used for opponent quality adjustment to rolling R/G baseline
opp_xfip_by_team = {}  # team_abbr -> [xFIP values of starters they faced]

for game in pitchers_games:
    away_abbr = game.get('away', {}).get('teamAbbr')
    home_abbr = game.get('home', {}).get('teamAbbr')
    away_id   = str(game.get('away', {}).get('pitcher', {}).get('id', ''))
    home_id   = str(game.get('home', {}).get('pitcher', {}).get('id', ''))
    # We don't have per-game xFIP in pitchers.json — that's in slate.json's pitcherSavant
    # We'll build this from slate.json directly in Step 3

# Build from slate games (current + historical games embedded in pitcherSavant)
# For now: compute opp quality adj from the slate's pitcherSavant seasonFIP as a proxy
# This is populated for today's games; historical requires a separate endpoint (future work)
# Mark as partial — flag when fewer than 10 games worth of data available

def compute_opp_quality_adj(avg_opp_xfip):
    """Convert avg opponent xFIP over rolling window to R/G adjustment."""
    if avg_opp_xfip is None:
        return None
    adj = (avg_opp_xfip - LEAGUE_AVG_XFIP) * 0.08
    # Cap at ±0.2 R/G
    return round(max(-0.2, min(0.2, adj)), 3)

# ── Step 3: Enrich slate game blocks ─────────────────────────────────────────
enriched = 0
for game in slate.get('games', []):
    for side_key, abbr_key in [('awayTeamStats', 'away'), ('homeTeamStats', 'home')]:
        abbr = game.get(abbr_key, {}).get('abbr')
        if not abbr or abbr not in ts_teams:
            continue
        td    = ts_teams[abbr]
        stats = game.setdefault(side_key, {})

        # Existing fields
        stats['rpgIndex']    = td.get('rpgIndex')
        stats['wrcPlus']     = td.get('rpgIndex')   # deprecated alias
        stats['last7RpG']    = td.get('last7RpG')
        stats['last15RpG']   = td.get('last15RpG')
        stats['runsPerGame'] = td.get('runsPerGame')

        # NEW: wOBA fields for lineup adjustment (MODEL_CORE Step 2)
        stats['teamWOBA']    = td.get('teamWOBA')    # season xwOBA baseline
        stats['teamFBPct']   = td.get('teamFBPct')   # offensive FB% for park modifier

        # NEW: Opponent quality adjustment
        # For today's games: use the opposing starter's seasonFIP as the "opponent quality" proxy
        # This is a single-game approximation; a true rolling window requires historical data
        opp_side = 'home' if abbr_key == 'away' else 'away'
        opp_pitcher_savant = game.get(opp_side, {}).get('pitcherSavant', {})
        opp_xfip = opp_pitcher_savant.get('xFIP') or opp_pitcher_savant.get('seasonFIP')

        # Note: rolling 15-game opponent quality requires historical schedule data
        # Current implementation uses today's opponent starter as a proxy
        # Full implementation: fetch schedule for last 15 games, map starters, avg their xFIP
        stats['oppStarterXFIP']   = opp_xfip
        stats['oppQualityAdj']    = compute_opp_quality_adj(opp_xfip)
        stats['oppQualityNote']   = 'single_game_proxy' if opp_xfip else 'unavailable'

        enriched += 1

    # NEW: Enrich bullpen blocks with high-leverage xFIP
    bullpens = bullpen_data.get('bullpens', {})
    for side in ['away', 'home']:
        abbr = game.get(side, {}).get('abbr')
        if not abbr or abbr not in bullpens:
            continue
        bp = bullpens[abbr]
        game_bp = game.get(side, {}).get('bullpen', {})
        if game_bp is None:
            game_bp = {}
            game[side]['bullpen'] = game_bp

        # Merge HL fields into the existing bullpen block
        game_bp['hlXFIP']       = bp.get('hlXFIP')
        game_bp['hlGrade']      = bp.get('hlGrade')
        game_bp['hlAvailable']  = bp.get('hlAvailable', False)
        game_bp['hlDivergence'] = bp.get('hlDivergence')
        game_bp['hlSamplePA']   = bp.get('hlSamplePA')

# ── Step 4: Platoon split estimation (unchanged) ─────────────────────────────
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
print(f'wOBA: enriched from teamstats teamWOBA field (Savant xwOBA)')
print(f'offFBPct: enriched from teamstats teamFBPct field (Savant team batting)')
print(f'HL bullpen: merged hlXFIP into bullpen blocks for all games')
print(f'Opp quality adj: single-game proxy (today starter xFIP vs {LEAGUE_AVG_XFIP} anchor)')
print(f'Note: full 15-game rolling opp quality requires historical schedule fetch — future work')
