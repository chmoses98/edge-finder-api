#!/usr/bin/env python3
"""
lib/kalshi_period_projections.py
====================================
Spread/total-correction mission -- period-scaled run projections for
F3 (first 3 innings) and F7 (first 7 innings), generalizing the EXACT
formula structure `scripts/build_market_ledger.py`'s `compute_projections()`
already uses for F5 (starter-innings-capped, times-through-order
adjusted, park-adjusted proportionally to the horizon) to any
sub-game inning boundary.

WHY A SEPARATE MODULE, NOT AN IMPORT OF build_market_ledger.py's LOGIC
------------------------------------------------------------------------
scripts/build_market_ledger.py is the real-money execution gate --
"do not change existing model probabilities... unless strictly
required" applies to it absolutely. This module does not import from
it and does not modify it; it re-reads the same read-only game-dict
input fields (offenseBaselineAdj, xFIP/seasonFIP, bullpen xFIP,
avgIPperStart, openerRole, ttoSplit, parkFactor) directly from `g`,
duplicating a few lines rather than importing a script whose
compute_projections() is entangled with the rest of that file. This
mirrors the existing precedent in lib/kalshi_mlb_contract_parser.py's
TWO_LETTER_TEAM_ABBRS duplication (see that module's docstring for the
same rationale).

THIS IS A DOCUMENTED MODELING SIMPLIFICATION
------------------------------------------------
Production's own F5 formula assumes "starter only" specifically
because starters are rarely pulled before completing 5 innings. That
same "starter only, TTO-adjusted" structure is reused here for F3
(an even safer assumption -- starters are pulled before 3 innings only
in unusual circumstances) and for F7 (a WEAKER assumption -- real
starters are pulled before completing 7 innings more often than before
5). This is reused for F7 anyway for structural consistency (one
formula, one clamp policy, no second inconsistent projection engine)
-- documented explicitly here, not silently assumed correct. No
artificial min/max clamp is applied for F3/F7 (F5's own 1.2-4.1 clamp
in production is tuned for a 5-inning horizon specifically and does
not transfer) -- an unclamped output is a deliberate signal that this
is a new, uncalibrated horizon rather than silently reusing a bound
tuned for a different one.

Never raises. Returns (None, None, [missing_field, ...]) if required
inputs are missing -- never fabricates a projection from partial data.
"""

HORIZON_INNINGS = {"F3": 3, "F5": 5, "F7": 7, "full_game": 9}


def compute_period_projection(g, innings):
    """
    Pure. Generalizes build_market_ledger.py's F5 formula
    (away_off_factor * (opp_starter_ip_capped * opp_xfip/9 * opp_tto_adj)
    + park_adj * (innings/9)) to an arbitrary sub-game inning boundary.

    Args:
        g: one game dict from data/slate.json (same shape
           compute_projections() reads).
        innings: 3 (F3) or 7 (F7). (F5 already has a production
           projection; callers wanting F5 should keep using
           build_market_ledger.compute_game_projection_context()'s
           f5AwayProj/f5HomeProj rather than calling this function
           with innings=5, to avoid two independently-computed F5
           numbers ever disagreeing.)

    Returns (away_period_proj, home_period_proj, missing_fields).
    """
    away_stats = g.get('awayTeamStats', {}) or {}
    home_stats = g.get('homeTeamStats', {}) or {}
    away_ps = (g.get('away', {}) or {}).get('pitcherSavant') or {}
    home_ps = (g.get('home', {}) or {}).get('pitcherSavant') or {}

    away_baseline = away_stats.get('offenseBaselineAdj')
    home_baseline = home_stats.get('offenseBaselineAdj')
    away_xfip = away_ps.get('xFIP') or away_ps.get('seasonFIP')
    home_xfip = home_ps.get('xFIP') or home_ps.get('seasonFIP')

    missing = []
    if away_baseline is None: missing.append('awayTeamStats.offenseBaselineAdj')
    if home_baseline is None: missing.append('homeTeamStats.offenseBaselineAdj')
    if away_xfip is None: missing.append('away.pitcherSavant.xFIP')
    if home_xfip is None: missing.append('home.pitcherSavant.xFIP')
    if missing:
        return None, None, missing

    away_xfip = max(2.80, min(5.50, away_xfip))
    home_xfip = max(2.80, min(5.50, home_xfip))

    away_ip = min(away_ps.get('avgIPperStart') or 6.0, 9.0)
    home_ip = min(home_ps.get('avgIPperStart') or 6.0, 9.0)

    park_factor = g.get('park', {}).get('parkFactor', 100)
    park_adj = (park_factor - 100) / 100 * 0.5

    away_off_factor = (away_baseline or 4.5) / 4.5
    home_off_factor = (home_baseline or 4.5) / 4.5

    away_opener = away_ps.get('openerRole', False)
    home_opener = home_ps.get('openerRole', False)

    period_home_starter_ip = min(home_ip, float(innings)) if not home_opener else 0
    period_away_starter_ip = min(away_ip, float(innings)) if not away_opener else 0

    away_tto = away_ps.get('ttoSplit')
    home_tto = home_ps.get('ttoSplit')
    away_tto_adj = 1.0 - (away_tto * 0.15) if (away_tto and away_tto > 0.5) else 1.0
    home_tto_adj = 1.0 - (home_tto * 0.15) if (home_tto and home_tto > 0.5) else 1.0

    fraction = innings / 9.0
    away_period = away_off_factor * (period_home_starter_ip * home_xfip / 9 * home_tto_adj) + park_adj * fraction
    home_period = home_off_factor * (period_away_starter_ip * away_xfip / 9 * away_tto_adj) + park_adj * fraction

    return round(away_period, 3), round(home_period, 3), []


def compute_period_projection_context(g, scope):
    """
    Pure. Returns {'awayProj': ..., 'homeProj': ..., 'totalProj': ...,
    'missingFields': [...]} for scope in ('F3', 'F7'). Raises
    ValueError for any other scope -- callers should route F5/full_game
    through the existing production projection context instead of this
    module, so no two independently-computed numbers for the same
    horizon can ever disagree.
    """
    if scope not in ('F3', 'F7'):
        raise ValueError(f"compute_period_projection_context only supports F3/F7, got {scope!r}")
    innings = HORIZON_INNINGS[scope]
    away_proj, home_proj, missing = compute_period_projection(g, innings)
    total_proj = round(away_proj + home_proj, 3) if (away_proj is not None and home_proj is not None) else None
    return {
        'awayProj': away_proj,
        'homeProj': home_proj,
        'totalProj': total_proj,
        'missingFields': missing,
    }
