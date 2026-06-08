"""
enrich_data.py — v6.0
Changes from v5.0:
  - lineupAdj now applied to offense_baseline in each game block
    Previously: fetch_lineups.py computed lineupWOBADelta but enrich_data.py
    never used it — the field sat unused in the JSON.
    Now: reads lineupAdj (already in R/G terms) from awayTeamStats/homeTeamStats
    and writes offense_baseline_adj = raw_baseline + lineupAdj (when lineupConfirmed=True)
  - offense_baseline_adj written to teamStats block for downstream use by Claude analysis
  - lineupConfirmed flag preserved and surfaced clearly
  - All other enrichments unchanged
"""
import json

LEAGUE_AVG_RPG  = 4.5
LEAGUE_AVG_XFIP = 4.00
LINEUP_ADJ_CAP  = 0.25   # matches fetch_lineups.py cap

# ── Load data files ──────────────────────────────────────────────────────────
with open('data/teamstats.json') as f:
    ts = json.load(f)
with open('data/slate.json') as f:
    slate = json.load(f)
with open('data/bullpen.json') as f:
    bullpen_data = json.load(f)

# Load oppquality.json
try:
    with open('data/oppquality.json') as f:
        oppq = json.load(f)
    opp_teams = oppq.get('teams', {})
    print(f'oppquality.json: {len(opp_teams)} teams')
except Exception as e:
    print(f'WARNING: oppquality.json not found ({e})')
    opp_teams = {}

# Load savant_team.json (wOBA, FB%, individual batter wOBA)
try:
    with open('data/savant_team.json') as f:
        savant_team = json.load(f)
    savant_teams   = savant_team.get('teams', {})
    savant_batters = savant_team.get('batters', {})\
    
    print(f'savant_team.json: {len(savant_teams)} teams, {len(savant_batters)} batters')
except Exception as e:
    print(f'WARNING: savant_team.json not found ({e}) — wOBA/FB% will be null')
    savant_teams   = {}
    savant_batters = {}

ts_teams = ts.get('teams', {})

# Abbr normalization: some sources use non-standard abbreviations.
# teamstats.json uses these abbreviations; normalize slate abbrs to match.
ABBR_NORMALIZE = {
    'ARI': 'AZ',    # Arizona Diamondbacks: MLB API uses ARI, teamstats uses AZ
    'OAK': 'ATH',   # Athletics: old abbreviation
}
def normalize_abbr(abbr):
    return ABBR_NORMALIZE.get(abbr, abbr)

# ── Step 1: Enrich teamstats with rpgIndex ───────────────────────────────────
for abbr, t in ts_teams.items():
    rec = t.get('record', {})\
    
    rs  = rec.get('runsScored', 0) or 0
    gp  = (rec.get('wins', 0) or 0) + (rec.get('losses', 0) or 0)
    if gp > 0 and rs > 0:
        rpg = rs / gp
        t['rpgIndex']    = round(rpg / LEAGUE_AVG_RPG * 100)
        t['runsPerGame'] = round(rpg, 2)
    else:
        t['rpgIndex']    = 100
        t['runsPerGame'] = None
    t['wrcPlus'] = t['rpgIndex']

ts['rpgIndexSource'] = 'season_rpg_normalized'
ts['wrcSource']      = 'season_rpg_normalized'
ts['lgRpG']          = LEAGUE_AVG_RPG

# Embed batterWOBA into teamstats for downstream use
ts['batterWOBA'] = savant_batters

with open('data/teamstats.json', 'w') as f:
    json.dump(ts, f)

# ── Step 2: Enrich slate game blocks ─────────────────────────────────────────
enriched             = 0
opp_quality_resolved = 0
opp_quality_missing  = 0
lineup_adj_applied   = 0
lineup_adj_skipped   = 0

def compute_offense_baseline(last7, last15, season, opp_adj):
    """
    Compute raw offense baseline using the three-way blend.
    offense_baseline = (L7*0.30 + L15*0.30 + Szn*0.40) with Bayesian shrinkage
    Then apply opponent quality adjustment.
    Returns (raw_blend, bayesian_baseline, baseline_with_opp_adj)
    """
    if last7 is None and last15 is None:
        raw = float(season or LEAGUE_AVG_RPG)
    elif last7 is None:
        raw = float(last15) * 0.5 + float(season or LEAGUE_AVG_RPG) * 0.5
    elif last15 is None:
        raw = float(last7) * 0.5 + float(season or LEAGUE_AVG_RPG) * 0.5
    else:
        raw = float(last7) * 0.30 + float(last15) * 0.30 + float(season or LEAGUE_AVG_RPG) * 0.40

    # Bayesian shrinkage toward league average
    bayesian = (15 * raw + 20 * LEAGUE_AVG_RPG) / 35

    # Opponent quality adjustment
    adj_final = bayesian + float(opp_adj or 0)
    return round(raw, 3), round(bayesian, 3), round(adj_final, 3)

for game in slate.get('games', []):
    for side_key, abbr_key in [('awayTeamStats', 'away'), ('homeTeamStats', 'home')]:
        abbr_raw = game.get(abbr_key, {}).get('abbr')
        abbr = normalize_abbr(abbr_raw) if abbr_raw else None
        if not abbr or abbr not in ts_teams:
            if abbr_raw and abbr_raw not in ts_teams:
                print(f'  WARNING: {abbr_raw} not in teamstats (normalized: {abbr}) — offenseBaselineAdj will be null')
            continue
        td    = ts_teams[abbr]
        stats = game.setdefault(side_key, {})

        # Core rolling stats
        stats['rpgIndex']    = td.get('rpgIndex')
        stats['wrcPlus']     = td.get('rpgIndex')
        stats['last7RpG']    = td.get('last7RpG')
        stats['last15RpG']   = td.get('last15RpG')
        stats['runsPerGame'] = td.get('runsPerGame')

        # Savant team batting metrics
        sv = savant_teams.get(abbr, {})
        stats['teamWOBA']    = sv.get('xwoba')
        stats['teamFBPct']   = sv.get('fbPct')
        stats['teamBBPct']   = sv.get('bbPct')
        stats['teamKPct']    = sv.get('kPct')
        stats['teamHardHit'] = sv.get('hardHit')
        stats['teamBarrel']  = sv.get('barrel')

        # Opponent quality (rolling 15-game)
        oq              = opp_teams.get(abbr, {})
        opp_xfip_avg    = oq.get('oppXFIPavg')
        opp_quality_adj = oq.get('oppQualityAdj')
        games_resolved  = oq.get('gamesResolved', 0)
        confidence      = oq.get('confidence', 'low')

        stats['oppXFIPavg']      = opp_xfip_avg
        stats['oppQualityAdj']   = opp_quality_adj
        stats['oppQualityGames'] = games_resolved
        stats['oppQualityConf']  = confidence
        stats['oppQualityNote']  = 'rolling_15_game' if opp_xfip_avg is not None else 'unavailable'

        if opp_xfip_avg is not None:
            opp_quality_resolved += 1
        else:
            opp_quality_missing  += 1

        # ── Compute offense_baseline (raw) ──────────────────────────────────
        raw_blend, bayesian_base, base_with_opp = compute_offense_baseline(
            stats.get('last7RpG'),
            stats.get('last15RpG'),
            stats.get('runsPerGame'),
            opp_quality_adj
        )
        stats['offenseBaselineRaw']    = raw_blend
        stats['offenseBaselineBayes']  = bayesian_base
        stats['offenseBaselineOppAdj'] = base_with_opp

        # ── Apply lineup adjustment (only when confirmed) ────────────────────
        lineup_confirmed = stats.get('lineupConfirmed', False)
        lineup_adj       = stats.get('lineupAdj')       # already in R/G terms, capped ±0.25
        lineup_delta     = stats.get('lineupWOBADelta')

        if lineup_confirmed and lineup_adj is not None:
            # Apply the lineup adjustment on top of the opp-quality-adjusted baseline
            adj_final = round(base_with_opp + lineup_adj, 3)
            # Hard cap: final adjusted baseline must stay in [2.5, 7.0]
            adj_final = max(2.5, min(7.0, adj_final))
            stats['offenseBaselineAdj'] = adj_final
            stats['lineupAdjApplied']   = True
            lineup_adj_applied += 1

            if abs(lineup_adj) >= 0.05:
                direction = 'UP' if lineup_adj > 0 else 'DOWN'
                print(f'  {abbr} lineup adj {direction}: base={base_with_opp} '
                      f'+ lineup_adj={lineup_adj:+.3f} = final={adj_final} '
                      f'(wOBA delta={lineup_delta:+.4f})')
        else:
            # No confirmed lineup — use baseline without lineup adjustment
            stats['offenseBaselineAdj'] = base_with_opp
            stats['lineupAdjApplied']   = False
            lineup_adj_skipped += 1

            if not lineup_confirmed and stats.get('lineupBattersResolved', 0) == 0:
                stats['lineupNote'] = 'lineup_not_posted'
            elif not lineup_confirmed:
                stats['lineupNote'] = f"partial_{stats.get('lineupBattersResolved', 0)}_of_9"
            else:
                stats['lineupNote'] = 'lineup_adj_null'

        enriched += 1

    # ── Enrich bullpen blocks with high-leverage xFIP ────────────────────────
    bullpens = bullpen_data.get('bullpens', {})
    for side in ['away', 'home']:
        abbr_raw = game.get(side, {}).get('abbr')
        abbr = normalize_abbr(abbr_raw) if abbr_raw else None
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

# ── Step 3: Platoon split estimation ─────────────────────────────────────────
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

print(f'\nEnriched {enriched} team stat blocks, fixed {platoon_fixed} platoon splits')
print(f'wOBA: {sum(1 for t in savant_teams.values() if t.get("xwoba"))} teams resolved')
print(f'FB%:  {sum(1 for t in savant_teams.values() if t.get("fbPct"))} teams resolved')
print(f'Opp quality: {opp_quality_resolved} resolved, {opp_quality_missing} missing')
print(f'Individual batter wOBA: {len(savant_batters)} batters in teamstats.batterWOBA')
print(f'Lineup adj applied: {lineup_adj_applied} | skipped (unconfirmed): {lineup_adj_skipped}')
print(f'offenseBaselineAdj written to all {enriched} team blocks')
