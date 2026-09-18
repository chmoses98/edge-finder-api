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

# Recent offensive FORM (distribution, not just a rolling mean) --
# written by scripts/fetch_team_offense_form.py earlier in the same
# workflow run. Purely additive handicapping context: it is attached to
# each team block below and is NEVER read by compute_offense_baseline()
# or by any projection/pricing math. A missing/stale file degrades to
# `offenseForm: None` with an explicit reason -- never a fabricated
# profile, and never a reason to fail the slate.
try:
    with open('data/team_offense_form.json') as f:
        offense_form_payload = json.load(f)
    offense_form_teams = offense_form_payload.get('teams', {})
    print(f"team_offense_form.json: {len(offense_form_teams)} teams "
          f"(as of {offense_form_payload.get('asOfDate')})")
except Exception as e:
    print(f'WARNING: team_offense_form.json not found ({e}) — offenseForm context will be null')
    offense_form_payload = {}
    offense_form_teams = {}

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

        # ── Recent offensive FORM (shape, not just the mean) ────────────
        # A rolling mean is dominated by its largest value: an offense
        # that scored 3,4,2,20,5,1,1 has an L7 mean of 5.1 while its
        # median is 3 and it cleared 5 runs twice in seven games.
        # last7RpG alone cannot show that, so every team block now also
        # carries the scores themselves, the median, threshold clears,
        # the max and its share of the window, and (where this repo's
        # Kalshi archive supports it) performance against its own team
        # total. Context ONLY -- offenseBaselineRaw/Bayes/OppAdj below
        # are computed from exactly the same inputs as before.
        team_form = offense_form_teams.get(abbr)
        if team_form:
            # The full per-game log stays in data/team_offense_form.json --
            # attaching it to every team block would repeat ~30 rows per
            # team inside data/slate.json for no added information: each
            # window profile already carries its own `scores` vector,
            # which is the consistency evidence a handicapper reads.
            stats['offenseForm'] = {k: v for k, v in team_form.items() if k != 'gameLog'}
        else:
            stats['offenseForm'] = None
        if team_form:
            stats['offenseFormLine']  = (team_form.get('formLines') or {}).get('L7')
            stats['offenseFormLabel'] = (team_form.get('formLabel') or {}).get('label')
            stats['offenseFormReason'] = (team_form.get('formLabel') or {}).get('reason')
            stats['offenseFormAsOf']  = team_form.get('asOfDate')
        else:
            stats['offenseFormLine']  = None
            stats['offenseFormLabel'] = 'UNKNOWN'
            stats['offenseFormReason'] = (
                'no team_offense_form.json entry for this team — run '
                'scripts/fetch_team_offense_form.py'
            )
            stats['offenseFormAsOf']  = None

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

        # Recent-usage context (scripts/fetch_bullpen_usage.py --
        # previous-day usage, back-to-back appearances, recent pitch
        # counts, high-leverage save/hold workload, handedness mix).
        # Copied verbatim -- never recomputed here, never guessed when
        # absent. Data/context only: this block is never read by
        # scripts/build_market_ledger.py's projection/pricing math (see
        # that script's bullpen usage, which only ever reads
        # away_bp/home_bp.get('xFIP')/.get('vulnerable') -- the existing
        # season-quality fields, unchanged by this addition).
        game_bp['recentUsage'] = bp.get('recentUsage')

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

# ── Phase 3 immutable pipeline: also publish this stage's output as its
# own artifact (data/pipeline/<date>/normalized_slate.json). enrich_data.py
# is the last enrichment step before build_market_ledger.py runs, so its
# output is the "Normalized Slate" layer boundary (see
# docs/IMMUTABLE_PIPELINE.md). Purely additive — best-effort, never
# allowed to affect the primary data/slate.json write above, which is
# already complete by this point.
try:
    import os as _os
    import sys as _sys
    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'lib'))
    from pipeline_artifacts import write_stage_artifact as _write_stage_artifact
    _write_stage_artifact('normalized_slate', slate.get('date', ''), slate, produced_by='scripts/enrich_data.py')
except Exception as _e:
    print(f'WARNING: could not write normalized_slate pipeline artifact: {_e}')

print(f'\nEnriched {enriched} team stat blocks, fixed {platoon_fixed} platoon splits')
print(f'wOBA: {sum(1 for t in savant_teams.values() if t.get("xwoba"))} teams resolved')
print(f'FB%:  {sum(1 for t in savant_teams.values() if t.get("fbPct"))} teams resolved')
print(f'Opp quality: {opp_quality_resolved} resolved, {opp_quality_missing} missing')
print(f'Individual batter wOBA: {len(savant_batters)} batters in teamstats.batterWOBA')
print(f'Lineup adj applied: {lineup_adj_applied} | skipped (unconfirmed): {lineup_adj_skipped}')
print(f'offenseBaselineAdj written to all {enriched} team blocks')
_form_attached = sum(
    1 for g in slate.get('games', []) for k in ('awayTeamStats', 'homeTeamStats')
    if (g.get(k) or {}).get('offenseForm')
)
print(f'offenseForm context attached to {_form_attached} team blocks '
      f'(descriptive only — no projection weight reads it)')
