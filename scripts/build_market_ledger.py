#!/usr/bin/env python3
"""
scripts/build_market_ledger.py v1.0
=====================================
Converts allEdges (positive-filter) into a complete market evaluation ledger.

For every required market on every game, produces exactly one row with status:
  Accepted        — edge >= threshold, ready to bet
  Rejected        — evaluated, no qualifying edge (or gate blocked)
  Missing Data    — Kalshi price not in slate / required field null
  Evaluation Failed — unexpected error during evaluation

Written to g['marketLedger'] in data/slate.json.
Validates that every required market has a row before writing.

Required markets (11 per game):
  NRFI, YRFI, F5_ML_Away, F5_ML_Home,
  TT_Away_Over, TT_Home_Over,
  ML_Away, ML_Home,
  Game_Total, RL_Away, RL_Home

Run AFTER merge_odds.py and enrich_data.py.
"""

import json, math, sys, os
from datetime import datetime, timezone

# Phase 1A: Executable price logic
try:
    from executable_price import get_executable_prices, executable_prob_from_price, check_max_bet_price
except ImportError:
    def get_executable_prices(yes_bid, yes_ask, no_bid=None, no_ask=None):
        def nc(v):
            if v is None: return None
            f = float(v)
            return f if f > 1.0 else round(f * 100, 4)
        yb, ya = nc(yes_bid), nc(yes_ask)
        nb = nc(no_bid) if no_bid is not None else (round(100 - ya, 4) if ya is not None else None)
        na = nc(no_ask) if no_ask is not None else (round(100 - yb, 4) if yb is not None else None)
        mid = round((yb + ya) / 2, 4) if (yb is not None and ya is not None) else (yb or ya)
        return {'yes_bid': yb, 'yes_ask': ya, 'no_bid': nb, 'no_ask': na,
                'yes_executable': ya, 'no_executable': na, 'mid': mid}
    def executable_prob_from_price(p): return round(p / 100.0, 6) if p is not None else None
    def check_max_bet_price(exec_p, max_p):
        if exec_p is None or max_p is None: return True, None
        return (True, None) if exec_p <= max_p else (False, 'PRICE_MOVED_BEYOND_MAX')

# Phase 1F: Reason codes
try:
    from reason_codes import build_reason_codes
except ImportError:
    def build_reason_codes(row_status, row_data): return []

# Rule 71 patch: import bet eligibility classifier
# bet_eligibility.py separates LIVE BET ELIGIBILITY from CLV/REVIEW INTEGRITY
# Missing CLV data NEVER blocks a live actionable bet.
try:
    from bet_eligibility import apply_eligibility
except ImportError:
    # Fallback: no-op if module is not found (safe — fields just won't be set)
    def apply_eligibility(row, clv_snapshot_captured=None):
        return row

# ── Constants ─────────────────────────────────────────────────────────────────
CAL_HIGH    = 0.187
CAL_MEDIUM  = 0.255
CAL_PAPER   = 0.18

THRESHOLD_HIGH   = 3.0   # calibrated edge %
THRESHOLD_MEDIUM = 1.5
THRESHOLD_PAPER  = 1.0

REQUIRED_MARKETS = [
    'NRFI', 'YRFI',
    'F5_ML_Away', 'F5_ML_Home',
    'TT_Away_Over', 'TT_Home_Over',
    'ML_Away', 'ML_Home',
    'Game_Total', 'RL_Away', 'RL_Home',
]

# ── Poisson helpers ────────────────────────────────────────────────────────────
def poisson_pmf(k, lam):
    if lam <= 0: return 0.0
    return (lam**k * math.exp(-lam)) / math.factorial(k)

def p_team_wins(team_proj, opp_proj, max_r=20):
    """Returns (p_win, p_push) for team_proj vs opp_proj."""
    pw = pp = 0
    for a in range(max_r + 1):
        for h in range(max_r + 1):
            p = poisson_pmf(a, team_proj) * poisson_pmf(h, opp_proj)
            if a > h: pw += p
            elif a == h: pp += p
    return pw, pp

def p_over_total(proj, line, max_r=30):
    """P(combined total > line) where line is an integer (Kalshi style)."""
    return sum(poisson_pmf(r, proj) for r in range(int(line) + 1, max_r + 1))

def vig_free_2way(a_american, h_american):
    """Return (vf_away, vf_home) vig-free from two American odds."""
    def imp(o):
        if o is None: return None
        return abs(o) / (abs(o) + 100) if o < 0 else 100 / (o + 100)
    ia, ih = imp(a_american), imp(h_american)
    if ia is None or ih is None: return None, None
    tot = ia + ih
    if tot == 0: return None, None
    return ia / tot, ih / tot

def vig_free_1way(american, comp_american):
    """VF for a one-sided market (e.g. NRFI) given YES and NO prices."""
    vfa, vfb = vig_free_2way(american, comp_american)
    return vfa

def calibrated_edge(model_prob, kalshi_vf, cal_factor):
    """Legacy function kept for backward compat. Returns calibrated edge %."""
    if model_prob is None or kalshi_vf is None: return None
    raw = model_prob - kalshi_vf
    return round(raw * cal_factor * 100, 3)  # in percent

def raw_edge_pct(model_prob, market_prob):
    """Raw edge as percent (no calibration)."""
    if model_prob is None or market_prob is None: return None
    return round((model_prob - market_prob) * 100, 3)

def build_edge_fields(model_prob, kalshi_vf, yes_ask_cents, cal_factor, snapshot_ts=None):
    """
    Phase 1C: Build all edge fields for a market row.
    
    Args:
        model_prob:      model probability (0-1)
        kalshi_vf:       Kalshi VF probability (0-1) = mid-price based
        yes_ask_cents:   executable price for YES bet (0-100 cents)
        cal_factor:      calibration factor
        snapshot_ts:     price snapshot timestamp
    
    Returns:
        dict with all edge fields
    """
    exec_prob = executable_prob_from_price(yes_ask_cents) if yes_ask_cents is not None else kalshi_vf

    raw_vs_vf   = raw_edge_pct(model_prob, kalshi_vf)
    raw_vs_exec = raw_edge_pct(model_prob, exec_prob)
    cal_vs_vf   = round(raw_vs_vf * cal_factor, 3) if raw_vs_vf is not None else None
    cal_vs_exec = round(raw_vs_exec * cal_factor, 3) if raw_vs_exec is not None else None

    return {
        'marketProbVF':               round(kalshi_vf * 100, 3) if kalshi_vf is not None else None,
        'executablePriceUsed':        yes_ask_cents,
        'executableMarketProb':       round(exec_prob * 100, 3) if exec_prob is not None else None,
        'rawEdgeVsVF':                raw_vs_vf,
        'rawEdgeVsExecutable':        raw_vs_exec,
        'calibrationFactor':          cal_factor,
        'calibratedEdgeVsVF':         cal_vs_vf,
        'calibratedEdgeVsExecutable': cal_vs_exec,
        'edgeUsedForQualification':   'calibratedEdgeVsExecutable',
        'edgeUsedForDisplay':         'calibratedEdgeVsExecutable',
        # Legacy 'edge' field = calibratedEdgeVsExecutable for backward compat
        'edge':                       cal_vs_exec,
        'priceSnapshotTimestamp':     snapshot_ts,
        # CLV scaffold (Phase 1D): filled at betting/settlement time
        'modelSnapshotPrice':         yes_ask_cents,
        'executablePriceAtOutput':    yes_ask_cents,
        'actualEntryPrice':           None,
        'closingPrice':               None,
        'clvVsSnapshot':              None,
        'clvVsExecutableOutput':      None,
        'clvVsActualEntry':           None,
    }

def confidence_from_edge(edge_pct, f5_amplified=False):
    if edge_pct is None: return None
    threshold = THRESHOLD_PAPER if not f5_amplified else 1.0
    if edge_pct < threshold: return None  # below floor
    if edge_pct >= THRESHOLD_HIGH: return 'HIGH'
    if edge_pct >= THRESHOLD_MEDIUM: return 'MEDIUM'
    return 'PAPER'

# ── Row builder ────────────────────────────────────────────────────────────────
def make_row(market, **kwargs):
    """Base row structure. Caller fills in status and relevant fields.
    
    Phase 1 additions:
      executablePriceUsed      — yes_ask for YES bets, no_ask for NO bets (cents 0-100)
      executableMarketProb     — probability derived from executablePriceUsed
      rawEdgeVsVF              — modelProb - marketProbVF (no calibration)
      rawEdgeVsExecutable      — modelProb - executableMarketProb (no calibration)
      calibrationFactor        — calibration multiplier applied
      calibratedEdgeVsVF       — rawEdgeVsVF * calibrationFactor * 100 (percent)
      calibratedEdgeVsExecutable — rawEdgeVsExecutable * calibrationFactor * 100 (percent)
      edgeUsedForQualification — which edge field gates real-money
      edgeUsedForDisplay       — which edge field to show in output
      maxBetPrice              — maximum acceptable executable price (cents); reject if worse
      priceSnapshotTimestamp   — when this price was captured
      reasonCodes              — list of structured reason codes
      
      CLV fields (Phase 1D):
      modelSnapshotPrice       — model's price at analysis time (cents)
      executablePriceAtOutput  — executable price when slip was generated (cents)
      actualEntryPrice         — filled by user after bet placed (null until then)
      closingPrice             — null until settlement
      clvVsSnapshot            — CLV vs model snapshot (null until settlement)
      clvVsExecutableOutput    — CLV vs executable price at output (null until settlement)
      clvVsActualEntry         — CLV vs actual entry price (null until settlement)
    """
    row = {
        'market':             market,
        'status':             kwargs.get('status', 'Evaluation Failed'),
        'kalshiPrice':        kwargs.get('kalshiPrice'),
        'kalshiImplied':      kwargs.get('kalshiImplied'),
        'kalshiVF':           kwargs.get('kalshiVF'),
        'pinnacleVF':         kwargs.get('pinnacleVF'),
        'modelProb':          kwargs.get('modelProb'),
        # Phase 1C: Raw vs calibrated edge transparency
        'marketProbVF':                kwargs.get('marketProbVF'),
        'executablePriceUsed':         kwargs.get('executablePriceUsed'),
        'executableMarketProb':        kwargs.get('executableMarketProb'),
        'rawEdgeVsVF':                 kwargs.get('rawEdgeVsVF'),
        'rawEdgeVsExecutable':         kwargs.get('rawEdgeVsExecutable'),
        'calibrationFactor':           kwargs.get('calibrationFactor'),
        'calibratedEdgeVsVF':          kwargs.get('calibratedEdgeVsVF'),
        'calibratedEdgeVsExecutable':  kwargs.get('calibratedEdgeVsExecutable'),
        'edgeUsedForQualification':    kwargs.get('edgeUsedForQualification', 'calibratedEdgeVsExecutable'),
        'edgeUsedForDisplay':          kwargs.get('edgeUsedForDisplay', 'calibratedEdgeVsExecutable'),
        # Legacy: keep 'edge' = calibratedEdgeVsExecutable for backward compat
        'edge':               kwargs.get('edge'),
        'confidence':         kwargs.get('confidence'),
        'confidenceTier':     kwargs.get('confidenceTier'),
        'confidenceReasons':  kwargs.get('confidenceReasons', []),
        'betSize':            kwargs.get('betSize'),
        # Phase 1A: max bet price
        'maxBetPrice':        kwargs.get('maxBetPrice'),
        'priceSnapshotTimestamp': kwargs.get('priceSnapshotTimestamp'),
        # Phase 1D: CLV fields
        'modelSnapshotPrice':      kwargs.get('modelSnapshotPrice'),
        'executablePriceAtOutput': kwargs.get('executablePriceAtOutput'),
        'actualEntryPrice':        kwargs.get('actualEntryPrice', None),
        'closingPrice':            kwargs.get('closingPrice', None),
        'clvVsSnapshot':           kwargs.get('clvVsSnapshot', None),
        'clvVsExecutableOutput':   kwargs.get('clvVsExecutableOutput', None),
        'clvVsActualEntry':        kwargs.get('clvVsActualEntry', None),
        # Projections
        'awayProjRuns':       kwargs.get('awayProjRuns'),
        'homeProjRuns':       kwargs.get('homeProjRuns'),
        'totalProj':          kwargs.get('totalProj'),
        'f5AwayProj':         kwargs.get('f5AwayProj'),
        'f5HomeProj':         kwargs.get('f5HomeProj'),
        'rejectionReason':    kwargs.get('rejectionReason'),
        'missingFields':      kwargs.get('missingFields'),
        'evaluationError':    kwargs.get('evaluationError'),
        'gatesFired':         kwargs.get('gatesFired', []),
        'notes':              kwargs.get('notes'),
        'ticker':             kwargs.get('ticker'),
        'marketTicker':       kwargs.get('marketTicker'),
        'seriesTicker':       kwargs.get('seriesTicker'),
        'eventTicker':        kwargs.get('eventTicker'),
        'scheduledStartTime': kwargs.get('scheduledStartTime'),
        'line':               kwargs.get('line'),
        # Rule 71 patch: bet eligibility / CLV / review status
        'bet_eligibility_status':  kwargs.get('bet_eligibility_status'),
        'clv_capture_status':      kwargs.get('clv_capture_status'),
        'review_integrity_status': kwargs.get('review_integrity_status'),
        'eligibility_reason':      kwargs.get('eligibility_reason'),
        # Phase 1F: structured reason codes (populated after row is fully built)
        'reasonCodes':        kwargs.get('reasonCodes', []),
        # Phase 1B: Lineup fields — must flow from awayTeamStats/homeTeamStats via evaluate_game
        'lineupPosted':              kwargs.get('lineupPosted'),
        'lineupStatus':              kwargs.get('lineupStatus'),
        'lineupConfirmedOfficial':   kwargs.get('lineupConfirmedOfficial'),
        'lineupSource':              kwargs.get('lineupSource'),
        'lineupBattersExpected':     kwargs.get('lineupBattersExpected'),
        'lineupBattersFound':        kwargs.get('lineupBattersFound'),
        'lineupBattersResolved':     kwargs.get('lineupBattersResolved'),
        'lineupAdjAvailable':        kwargs.get('lineupAdjAvailable'),
        'lineupAdjApplied':          kwargs.get('lineupAdjApplied'),
        'lineupDataQuality':         kwargs.get('lineupDataQuality'),
        'lineupStatusReason':        kwargs.get('lineupStatusReason'),
    }
    return row

def missing_row(market, missing_fields):
    return make_row(market, status='Missing Data', missingFields=missing_fields)

def rejected_row(market, reason, **kwargs):
    return make_row(market, status='Rejected', rejectionReason=reason, **kwargs)

def accepted_row(market, **kwargs):
    return make_row(market, status='Accepted', **kwargs)

def failed_row(market, error):
    return make_row(market, status='Evaluation Failed', evaluationError=str(error)[:200])

# ── Size lookup ────────────────────────────────────────────────────────────────
MARKET_MULTIPLIERS = {
    'F5_ML_Away': 1.5, 'F5_ML_Home': 1.5,
    'TT_Away_Over': 1.25, 'TT_Home_Over': 1.25,
    'YRFI': 1.25,
    'ML_Away': 1.0, 'ML_Home': 1.0,
    'NRFI': 1.0,
    'RL_Away': 0.0, 'RL_Home': 0.0,  # suspended
    'Game_Total': 0.0,                 # paper only
}

def bet_size(conf, market):
    mult = MARKET_MULTIPLIERS.get(market, 1.0)
    if mult == 0.0: return 1.0  # paper
    base = {'HIGH': 4.0, 'MEDIUM': 3.0, 'PAPER': 1.0}.get(conf, 1.0)
    return min(8.0, round(base * mult * 2) / 2)

# ── Projection engine (simplified — reads from enrich_data output) ────────────
def compute_projections(g):
    """
    Returns (away_proj, home_proj, f5_away, f5_home) using offenseBaselineAdj.
    Returns None tuple if critical data is missing.
    """
    away_stats = g.get('awayTeamStats', {})
    home_stats  = g.get('homeTeamStats', {})
    away_ps     = g.get('away', {}).get('pitcherSavant') or {}
    home_ps     = g.get('home', {}).get('pitcherSavant') or {}
    away_bp     = g.get('away', {}).get('bullpen', {})
    home_bp     = g.get('home', {}).get('bullpen', {})

    away_baseline = away_stats.get('offenseBaselineAdj')
    home_baseline  = home_stats.get('offenseBaselineAdj')

    # Use xFIP; fall back to seasonFIP if xFIP null
    away_xfip = away_ps.get('xFIP') or away_ps.get('seasonFIP')
    home_xfip  = home_ps.get('xFIP') or home_ps.get('seasonFIP')

    missing = []
    if away_baseline is None: missing.append('awayTeamStats.offenseBaselineAdj')
    if home_baseline is None:  missing.append('homeTeamStats.offenseBaselineAdj')
    if away_xfip is None:      missing.append('away.pitcherSavant.xFIP')
    if home_xfip is None:      missing.append('home.pitcherSavant.xFIP')
    if missing:
        return None, None, None, None, missing

    # Clamp xFIP
    away_xfip = max(2.80, min(5.50, away_xfip))
    home_xfip  = max(2.80, min(5.50, home_xfip))

    # recentFIP sanity gate — if negative, use seasonFIP or xFIP only
    # (pipeline bug: negative recentFIP on <3 starts)
    away_recent = away_ps.get('recentFIP')
    home_recent  = home_ps.get('recentFIP')
    if away_recent is not None and away_recent < 0:
        away_xfip = away_xfip  # already clamped to xFIP, skip recentFIP
    if home_recent is not None and home_recent < 0:
        home_xfip = home_xfip

    away_ip     = min(away_ps.get('avgIPperStart') or 6.0, 9.0)
    home_ip      = min(home_ps.get('avgIPperStart') or 6.0, 9.0)
    away_pen_xfip = away_bp.get('xFIP') or 4.0
    home_pen_xfip  = home_bp.get('xFIP') or 4.0

    # Clamp pen xFIP
    away_pen_xfip = max(2.5, min(6.0, away_pen_xfip))
    home_pen_xfip  = max(2.5, min(6.0, home_pen_xfip))

    # Park factor
    park_factor = g.get('park', {}).get('parkFactor', 100)
    park_adj = (park_factor - 100) / 100 * 0.5

    away_off_factor = (away_baseline or 4.5) / 4.5
    home_off_factor  = (home_baseline  or 4.5) / 4.5

    home_starter_ip = home_ip
    home_pen_ip     = max(0, 9.0 - home_starter_ip)
    away_starter_ip = away_ip
    away_pen_ip     = max(0, 9.0 - away_starter_ip)

    away_proj = away_off_factor * (home_starter_ip * home_xfip / 9 + home_pen_ip * home_pen_xfip / 9) + park_adj
    home_proj  = home_off_factor  * (away_starter_ip * away_xfip / 9 + away_pen_ip  * away_pen_xfip / 9) + park_adj

    # Clamp
    away_proj = max(2.5, min(7.0, away_proj))
    home_proj  = max(2.5, min(7.0, home_proj))

    # F5: starter only, 5/8.5 ratio, opener cap
    away_opener = away_ps.get('openerRole', False)
    home_opener  = home_ps.get('openerRole', False)

    f5_away_starter_ip = min(away_ip, 5.0) if not away_opener else 0
    f5_home_starter_ip  = min(home_ip,  5.0) if not home_opener  else 0

    # TTO adjustment
    away_tto = away_ps.get('ttoSplit')
    home_tto  = home_ps.get('ttoSplit')
    away_tto_adj = 1.0 - (away_tto * 0.15) if (away_tto and away_tto > 0.5) else 1.0
    home_tto_adj  = 1.0 - (home_tto  * 0.15) if (home_tto  and home_tto  > 0.5) else 1.0

    f5_away = away_off_factor * (f5_home_starter_ip * home_xfip / 9 * home_tto_adj) + park_adj * (5/9)
    f5_home  = home_off_factor  * (f5_away_starter_ip  * away_xfip / 9 * away_tto_adj)  + park_adj * (5/9)

    f5_away = max(1.2, min(4.1, f5_away))
    f5_home  = max(1.2, min(4.1, f5_home))

    return (
        round(away_proj, 3),
        round(home_proj, 3),
        round(f5_away, 3),
        round(f5_home, 3),
        []  # no missing fields
    )


# ── Main evaluation ────────────────────────────────────────────────────────────
def evaluate_game(g):
    """
    Returns list of market rows (one per REQUIRED_MARKETS entry).
    Every required market gets exactly one row.
    """
    rows = {}
    kalshi = (g.get('odds') or {}).get('kalshi') or {}
    pvf     = g.get('pinnacleVF', {}) or {}
    away_ps = g.get('away', {}).get('pitcherSavant') or {}
    home_ps  = g.get('home', {}).get('pitcherSavant') or {}
    away_ts = g.get('awayTeamStats', {}) or {}
    home_ts  = g.get('homeTeamStats', {}) or {}

    away_opener = away_ps.get('openerRole', False)
    home_opener  = home_ps.get('openerRole', False)
    total_line  = (kalshi.get('total') or {}).get('line')
    # Legacy gate field (backward compat) — True only when >=6/9 batters resolved
    away_lineup = away_ts.get('lineupConfirmed', False)
    home_lineup  = home_ts.get('lineupConfirmed', False)

    # Phase 1B: Separated lineup fields — read from awayTeamStats / homeTeamStats
    away_lineup_official    = away_ts.get('lineupConfirmedOfficial', False)
    home_lineup_official     = home_ts.get('lineupConfirmedOfficial', False)
    away_lineup_adj_avail   = away_ts.get('lineupAdjAvailable', False)
    home_lineup_adj_avail    = home_ts.get('lineupAdjAvailable', False)
    away_lineup_adj_applied  = away_ts.get('lineupAdjApplied', False)
    home_lineup_adj_applied   = home_ts.get('lineupAdjApplied', False)
    away_lineup_status       = away_ts.get('lineupStatus', 'unknown')
    home_lineup_status        = home_ts.get('lineupStatus', 'unknown')
    away_lineup_source       = away_ts.get('lineupSource', 'mlb_stats_api')
    home_lineup_source        = home_ts.get('lineupSource', 'mlb_stats_api')
    away_lineup_posted       = away_ts.get('lineupPosted', False)
    home_lineup_posted        = home_ts.get('lineupPosted', False)
    away_batters_expected    = away_ts.get('lineupBattersExpected', 9)
    home_batters_expected     = home_ts.get('lineupBattersExpected', 9)
    away_batters_found       = away_ts.get('lineupBattersFound', 0)
    home_batters_found        = home_ts.get('lineupBattersFound', 0)
    away_batters_resolved    = away_ts.get('lineupBattersResolved', 0)
    home_batters_resolved     = home_ts.get('lineupBattersResolved', 0)
    away_lineup_quality      = away_ts.get('lineupDataQuality', 'none')
    home_lineup_quality       = home_ts.get('lineupDataQuality', 'none')
    away_lineup_reason       = away_ts.get('lineupStatusReason', '')
    home_lineup_reason        = home_ts.get('lineupStatusReason', '')

    if 'lineupConfirmedOfficial' not in away_ts:
        import sys as _sys
        print(f'DATA-HEALTH WARNING: awayTeamStats missing lineupConfirmedOfficial — '
              f'fetch_lineups.py may not have run for this game', file=_sys.stderr)
    if 'lineupConfirmedOfficial' not in home_ts:
        import sys as _sys
        print(f'DATA-HEALTH WARNING: homeTeamStats missing lineupConfirmedOfficial — '
              f'fetch_lineups.py may not have run for this game', file=_sys.stderr)

    # ── Game-level identity (shared across all market rows) ───────────────
    # eventTicker is derived from kalshiKey + game time
    game_event_ticker = None
    kalshi_key = g.get('kalshiKey', '')
    game_time_et = g.get('kalshiGameTime', '')
    # The event_ticker is NOT the game key directly; rows use market-level tickers.
    # scheduledStartTime comes from the Odds API commence time
    scheduled_start = g.get('oddsApiCommenceTime')
    
    # Phase 1A: helper to normalize prices to cents scale
    def _to_cents(v):
        if v is None: return None
        f = float(v)
        return round(f * 100 if f <= 1.0 else f, 2)
    
    # Phase 1A: price snapshot timestamp
    snapshot_ts = g.get('kalshiSnapshotTs') or g.get('snapshot_ts')

    # Compute projections once
    away_proj, home_proj, f5_away, f5_home, proj_missing = compute_projections(g)
    total_proj = round(away_proj + home_proj, 3) if away_proj else None

    proj_context = dict(
        awayProjRuns=away_proj, homeProjRuns=home_proj,
        totalProj=total_proj, f5AwayProj=f5_away, f5HomeProj=f5_home,
    )

    # Phase 1B: per-game lineup context dicts — injected into every row
    away_lineup_ctx = dict(
        lineupPosted=away_lineup_posted,
        lineupStatus=away_lineup_status,
        lineupConfirmedOfficial=away_lineup_official,
        lineupSource=away_lineup_source,
        lineupBattersExpected=away_batters_expected,
        lineupBattersFound=away_batters_found,
        lineupBattersResolved=away_batters_resolved,
        lineupAdjAvailable=away_lineup_adj_avail,
        lineupAdjApplied=away_lineup_adj_applied,
        lineupDataQuality=away_lineup_quality,
        lineupStatusReason=away_lineup_reason,
    )
    home_lineup_ctx = dict(
        lineupPosted=home_lineup_posted,
        lineupStatus=home_lineup_status,
        lineupConfirmedOfficial=home_lineup_official,
        lineupSource=home_lineup_source,
        lineupBattersExpected=home_batters_expected,
        lineupBattersFound=home_batters_found,
        lineupBattersResolved=home_batters_resolved,
        lineupAdjAvailable=home_lineup_adj_avail,
        lineupAdjApplied=home_lineup_adj_applied,
        lineupDataQuality=home_lineup_quality,
        lineupStatusReason=home_lineup_reason,
    )

    # ── Identity context helper: returns identity kwargs for a market ─────
    def identity(market_ticker=None, series_ticker=None, event_ticker=None):
        return dict(
            marketTicker=market_ticker,
            ticker=market_ticker,
            seriesTicker=series_ticker,
            eventTicker=event_ticker,
            scheduledStartTime=scheduled_start,
        )

    # ── Helper: pinnacle gap check (Rule 71) ──────────────────────────────
    def pin_gap_ok_ml(model_prob, pvf_prob, market_label):
        """Returns (ok, gates_fired)"""
        if pvf_prob is None:
            return True, []  # can't check — no block
        gap = abs(model_prob - pvf_prob) * 100
        if gap > 8.0:
            return False, [f'Rule71: model {model_prob*100:.1f}% vs PinVF {pvf_prob*100:.1f}% = {gap:.1f}% gap > 8%']
        return True, []

    # ── ML_Away ───────────────────────────────────────────────────────────
    ml = kalshi.get('ml', {}) or {}
    ml_away_am = ml.get('away')
    ml_home_am = ml.get('home')
    pvf_away = (pvf.get('away') or 0) / 100 if pvf.get('away') else None
    pvf_home  = (pvf.get('home')  or 0) / 100 if pvf.get('home')  else None
    # Phase 1A: extract executable prices (yes_ask) from registry
    ml_away_yes_ask = ml.get('yes_ask_cents') or ml.get('yes_ask')  # may be None if registry lacks it
    ml_home_yes_ask = ml.get('yes_ask_cents') or ml.get('yes_ask')
    snapshot_ts = g.get('kalshiSnapshotTs') or g.get('snapshot_ts')

    if ml_away_am is None or ml_home_am is None:
        rows['ML_Away'] = missing_row('ML_Away', ['odds.kalshi.ml.away', 'odds.kalshi.ml.home'])
        rows['ML_Home']  = missing_row('ML_Home',  ['odds.kalshi.ml.away', 'odds.kalshi.ml.home'])
    elif away_proj is None:
        rows['ML_Away'] = missing_row('ML_Away', proj_missing)
        rows['ML_Home']  = missing_row('ML_Home',  proj_missing)
    else:
        try:
            vf_away, vf_home = vig_free_2way(ml_away_am, ml_home_am)
            p_away_win, p_push = p_team_wins(away_proj, home_proj)
            # Exclude push for ML
            p_away_net = p_away_win / (1 - p_push) if (1 - p_push) > 0 else p_away_win
            p_home_net = 1 - p_away_net

            # Extra-inning blend for close games
            margin = abs(away_proj - home_proj)
            if margin < 1.5:
                p_away_net = p_away_net * 0.90 + 0.50 * 0.10
                p_home_net = p_home_net * 0.90 + 0.50 * 0.10

            # Win prob ceiling
            p_away_net = min(p_away_net, 0.72)
            p_home_net = min(p_home_net, 0.72)

            edge_away = calibrated_edge(p_away_net, vf_away, CAL_MEDIUM)
            edge_home  = calibrated_edge(p_home_net,  vf_home,  CAL_MEDIUM)

            # Phase 1C: build full edge fields using executable price (yes_ask)
            # yes_ask for the away YES market; for home we take the home yes_ask
            # Registry price_block stores yes_ask at decimal scale — convert to cents
            def _to_cents(v):
                if v is None: return None
                f = float(v)
                return round(f * 100 if f <= 1.0 else f, 2)
            away_yes_ask_c = _to_cents(ml.get('away_yes_ask') or ml.get('yes_ask'))
            home_yes_ask_c = _to_cents(ml.get('home_yes_ask') or ml.get('yes_ask'))
            # Fallback: derive from american odds if yes_ask not in registry  
            if away_yes_ask_c is None and ml_away_am is not None:
                # Convert american to implied prob cents (approximate)
                imp = abs(ml_away_am)/(abs(ml_away_am)+100) if ml_away_am < 0 else 100/(ml_away_am+100)
                away_yes_ask_c = round(imp * 100, 2)
            if home_yes_ask_c is None and ml_home_am is not None:
                imp = abs(ml_home_am)/(abs(ml_home_am)+100) if ml_home_am < 0 else 100/(ml_home_am+100)
                home_yes_ask_c = round(imp * 100, 2)

            ef_away = build_edge_fields(p_away_net, vf_away, away_yes_ask_c, CAL_MEDIUM, snapshot_ts)
            ef_home  = build_edge_fields(p_home_net,  vf_home,  home_yes_ask_c, CAL_MEDIUM, snapshot_ts)

            conf_away = confidence_from_edge(edge_away)
            conf_home  = confidence_from_edge(edge_home)

            # Rule 51: ML lineup gate — uses lineupConfirmedOfficial per Phase 1B spec.
            gates_away = []
            gates_home  = []
            if not (away_lineup_official and home_lineup_official):
                missing_sides = []
                if not away_lineup_official: missing_sides.append('away')
                if not home_lineup_official: missing_sides.append('home')
                gate_msg = f'Rule 51: lineupConfirmedOfficial=False ({", ".join(missing_sides)}) — ML downgraded to PAPER'
                gates_away.append(gate_msg)
                gates_home.append(gate_msg)
                if conf_away not in (None,): conf_away = 'PAPER'
                if conf_home  not in (None,): conf_home  = 'PAPER'

            # Rule 71 gate
            ok_away, rule71_away = pin_gap_ok_ml(p_away_net, pvf_away, 'ML_Away')
            ok_home,  rule71_home  = pin_gap_ok_ml(p_home_net, pvf_home,  'ML_Home')
            if not ok_away:
                gates_away.extend(rule71_away)
                conf_away = None  # blocked

            if not ok_home:
                gates_home.extend(rule71_home)
                conf_home = None

            for market, model_p, vf, am, conf, gates, ml_lineup_ctx in [
                ('ML_Away', p_away_net, vf_away, ml_away_am, conf_away, gates_away, away_lineup_ctx),
                ('ML_Home',  p_home_net,  vf_home,  ml_home_am,  conf_home,  gates_home,  home_lineup_ctx),
            ]:
                pvf_val = pvf_away if market == 'ML_Away' else pvf_home
                if conf is None:
                    edge_val = calibrated_edge(model_p, vf, CAL_MEDIUM)
                    ef = ef_away if market == 'ML_Away' else ef_home
                    if gates:
                        row = rejected_row(
                            market,
                            reason='; '.join(gates),
                            kalshiPrice=am, kalshiVF=round(vf*100,2),
                            pinnacleVF=round(pvf_val*100,2) if pvf_val else None,
                            modelProb=round(model_p*100,2),
                            gatesFired=gates,
                            **ef,
                            **proj_context,
                            **ml_lineup_ctx,
                        )
                    else:
                        row = rejected_row(
                            market,
                            reason=f'edge {edge_val}% below {THRESHOLD_PAPER}% floor',
                            kalshiPrice=am, kalshiVF=round(vf*100,2),
                            pinnacleVF=round(pvf_val*100,2) if pvf_val else None,
                            modelProb=round(model_p*100,2),
                            **ef,
                            **proj_context,
                            **ml_lineup_ctx,
                        )
                    row['reasonCodes'] = build_reason_codes('Rejected', row)
                    rows[market] = row
                else:
                    ml_ticker = ml.get('away_ticker') if market == 'ML_Away' else ml.get('home_ticker')
                    ef = ef_away if market == 'ML_Away' else ef_home
                    max_bet = ef.get('executablePriceUsed')
                    row = accepted_row(
                        market,
                        kalshiPrice=am, kalshiImplied=round(vf*100,2), kalshiVF=round(vf*100,2),
                        pinnacleVF=round(pvf_val*100,2) if pvf_val else None,
                        modelProb=round(model_p*100,2),
                        confidence=conf, betSize=bet_size(conf, market),
                        gatesFired=gates,
                        **ef,
                        maxBetPrice=max_bet,
                        confidenceTier=conf,
                        **identity(ml_ticker, 'KXMLBGAME'),
                        **proj_context,
                        **ml_lineup_ctx,
                    )
                    row['reasonCodes'] = build_reason_codes('Accepted', row)
                    rows[market] = row
        except Exception as e:
            import traceback as _tb
            _tbstr = _tb.format_exc()
            for _mkt, _lctx in [('ML_Away', away_lineup_ctx), ('ML_Home', home_lineup_ctx)]:
                if _mkt not in rows:
                    _row = failed_row(_mkt, f'{type(e).__name__}: {e}')
                    _row['evaluationError'] = f'{type(e).__name__}: {e}' + '\n' + _tbstr[:400]
                    _row.update(_lctx)
                    rows[_mkt] = _row

    # ── RL_Away / RL_Home ─────────────────────────────────────────────────
    # Suspended per Rule 81 — always Rejected with documented reason
    rl = kalshi.get('rl', {}) or {}
    rl_ticker = rl.get('best_ticker')
    for market in ['RL_Away', 'RL_Home']:
        rows[market] = rejected_row(
            market,
            reason='Rule 81: RL suspended — WR 36%, CLV -4.09%. Paper until WR>=48% N>=20 AND CLV>=0% N>=15',
            kalshiPrice=rl.get('american'),
            **identity(rl_ticker, 'KXMLBSPREAD'),
            **proj_context
        )

    # ── Game_Total ────────────────────────────────────────────────────────
    tot = kalshi.get('total', {}) or {}
    tot_line = tot.get('line')
    tot_am   = tot.get('american')
    if tot_line is None:
        rows['Game_Total'] = missing_row('Game_Total', ['odds.kalshi.total.line'])
    elif away_proj is None:
        rows['Game_Total'] = missing_row('Game_Total', proj_missing)
    else:
        try:
            # Paper only per Rule 71 market suspension (WR 41%)
            rows['Game_Total'] = rejected_row(
                'Game_Total',
                reason=f'Rule 71 market suspension: Game Total WR 41%, CLV -1.43%. Paper only until WR>=52% N>=30',
                kalshiPrice=tot_am, line=tot_line,
                modelProb=round(p_over_total(total_proj, tot_line)*100, 2) if total_proj else None,
                **identity(tot.get('best_ticker'), 'KXMLBTOTAL'),
                **proj_context
            )
        except Exception as e:
            rows['Game_Total'] = failed_row('Game_Total', e)

    # ── TT_Away_Over / TT_Home_Over ───────────────────────────────────────
    tt = kalshi.get('team_totals', {}) or {}
    for market, side_key, lineup_ok_official, lineup_ctx, proj in [
        ('TT_Away_Over', 'away', away_lineup_official, away_lineup_ctx, away_proj),
        ('TT_Home_Over', 'home', home_lineup_official,  home_lineup_ctx,  home_proj),
    ]:
        tt_side = tt.get(side_key, {}) or {}
        tt_ticker = tt_side.get('best_ticker')
        tt_line   = tt_side.get('line')
        tt_am     = tt_side.get('american')
        tt_implied = tt_side.get('implied_pct')

        if tt_ticker is None:
            _r = missing_row(market, [f'odds.kalshi.team_totals.{side_key}.best_ticker'])
            _r.update(lineup_ctx)
            rows[market] = _r
        elif proj is None:
            _r = missing_row(market, proj_missing)
            _r.update(lineup_ctx)
            rows[market] = _r
        else:
            try:
                gates = []
                # Rule 50: TT lineup gate — uses lineupConfirmedOfficial per Phase 1B spec.
                if not lineup_ok_official:
                    gates.append('Rule 50: lineupConfirmedOfficial=False → TT Paper only')

                if tt_line is not None and tt_implied is not None:
                    kalshi_vf = tt_implied / 100
                    # FIX (v1.1): use tt_line directly, NOT tt_line - 1.
                    # p_over_total(proj, N) = P(runs > N) = P(runs >= N+1).
                    # Over 4 requires 5+ runs → p_over_total(proj, 4) = P(5+).
                    # The old call p_over_total(proj, tt_line - 1) computed P(runs >= tt_line)
                    # which INCLUDED exactly tt_line runs — inflating TT Over N probability
                    # by PMF(tt_line) ≈ 15–20 ppts for typical projections near 4–5 runs.
                    model_p = p_over_total(proj, tt_line)
                    model_p = min(model_p, 0.95)

                    edge_val = calibrated_edge(model_p, kalshi_vf, CAL_MEDIUM)
                    conf = confidence_from_edge(edge_val)

                    if not lineup_ok_official:
                        conf = 'PAPER'

                    # FIX 3: TT executable price — derive from yes_ask if present,
                    # else implied_pct, else American odds conversion
                    tt_yes_ask_c = _to_cents(tt_side.get('yes_ask'))
                    if tt_yes_ask_c is None and tt_implied is not None:
                        tt_yes_ask_c = round(float(tt_implied), 4)
                    if tt_yes_ask_c is None and tt_am is not None:
                        _imp = abs(tt_am)/(abs(tt_am)+100) if tt_am < 0 else 100/(tt_am+100)
                        tt_yes_ask_c = round(_imp * 100, 2)

                    ef_tt = build_edge_fields(model_p, kalshi_vf, tt_yes_ask_c, CAL_MEDIUM, snapshot_ts)
                    tt_max_bet = tt_yes_ask_c

                    if conf is None:
                        row = rejected_row(
                            market,
                            reason=f'edge {edge_val}% below {THRESHOLD_PAPER}% floor',
                            kalshiPrice=tt_am, kalshiVF=round(kalshi_vf*100,2),
                            modelProb=round(model_p*100,2),
                            line=tt_line, gatesFired=gates,
                            **ef_tt,
                            maxBetPrice=tt_max_bet,
                            **identity(tt_ticker, 'KXMLBTEAMTOTAL'),
                            **proj_context,
                            **lineup_ctx,
                        )
                        row['reasonCodes'] = build_reason_codes('Rejected', row)
                        rows[market] = row
                    else:
                        row = accepted_row(
                            market,
                            kalshiPrice=tt_am, kalshiImplied=tt_implied,
                            kalshiVF=round(kalshi_vf*100,2),
                            modelProb=round(model_p*100,2),
                            confidence=conf, betSize=bet_size(conf, market),
                            line=tt_line, gatesFired=gates,
                            **ef_tt,
                            maxBetPrice=tt_max_bet,
                            confidenceTier=conf,
                            **identity(tt_ticker, 'KXMLBTEAMTOTAL'),
                            **proj_context,
                            **lineup_ctx,
                        )
                        row['reasonCodes'] = build_reason_codes('Accepted', row)
                        rows[market] = row
                else:
                    _r = missing_row(market, [f'odds.kalshi.team_totals.{side_key}.line'])
                    _r.update(lineup_ctx)
                    rows[market] = _r
            except Exception as e:
                import traceback as _tb
                _tbstr = _tb.format_exc()
                _r = failed_row(market, f'{type(e).__name__}: {e}')
                _r['evaluationError'] = f'{type(e).__name__}: {e}' + '\n' + _tbstr[:400]
                _r.update(lineup_ctx)
                rows[market] = _r

    # ── F5_ML_Away / F5_ML_Home ───────────────────────────────────────────
    f5ml = kalshi.get('f5ml', {}) or {}
    f5_away_am = f5ml.get('away')
    f5_home_am  = f5ml.get('home')
    f5_tie_am   = f5ml.get('tie_american')

    for market, side_opener, proj_val, am_val, opp_proj_val, opp_am in [
        ('F5_ML_Away', away_opener, f5_away, f5_away_am, f5_home, f5_home_am),
        ('F5_ML_Home',  home_opener,  f5_home,  f5_home_am,  f5_away, f5_away_am),
    ]:
        if am_val is None:
            rows[market] = missing_row(market, [f'odds.kalshi.f5ml.{market.split("_")[-1].lower()}'])
        elif f5_away is None or f5_home is None:
            rows[market] = missing_row(market, proj_missing)
        else:
            try:
                gates = []
                # Rule 53: F5 lineup gate — uses lineupConfirmedOfficial per Phase 1B spec.
                if not (away_lineup_official and home_lineup_official):
                    missing_sides_f5 = []
                    if not away_lineup_official: missing_sides_f5.append('away')
                    if not home_lineup_official: missing_sides_f5.append('home')
                    gates.append(f'Rule 53: F5 requires confirmed lineups for both teams — {", ".join(missing_sides_f5)} unconfirmed (lineupConfirmedOfficial=False) → PAPER')
                # Rule 24: opener blocks F5 entirely for that side
                # (opener is the pitcher throwing for the OPPONENT when we evaluate the offense side)
                # F5_ML_Away = away wins F5. If HOME is opener, away faces opener → F5 unqualified.
                home_is_opener = home_opener
                away_is_opener = away_opener
                if market == 'F5_ML_Away' and away_is_opener:
                    rows[market] = rejected_row(
                        market,
                        reason=f'Rule 24: away pitcher is opener (avgIP={away_ps.get("avgIPperStart",0):.1f}) — F5 UNQUALIFIED',
                        kalshiPrice=am_val, gatesFired=['Rule24'],
                        **proj_context
                    )
                    continue
                if market == 'F5_ML_Home' and home_is_opener:
                    rows[market] = rejected_row(
                        market,
                        reason=f'Rule 24: home pitcher is opener (avgIP={home_ps.get("avgIPperStart",0):.1f}) — F5 UNQUALIFIED',
                        kalshiPrice=am_val, gatesFired=['Rule24'],
                        **proj_context
                    )
                    continue

                # Three-way market: normalize VF over away+home (ignore tie for edge calc)
                vf_away_f5, vf_home_f5 = vig_free_2way(f5_away_am, f5_home_am)
                if vf_away_f5 is None:
                    rows[market] = missing_row(market, ['F5 VF calculation failed — null prices'])
                    continue

                p_away_f5_win, p_push_f5 = p_team_wins(f5_away, f5_home)
                p_home_f5_win = 1 - p_away_f5_win - p_push_f5
                # Net of tie
                p_away_f5_net = p_away_f5_win / (1 - p_push_f5) if p_push_f5 < 1 else 0
                p_home_f5_net  = p_home_f5_win  / (1 - p_push_f5) if p_push_f5 < 1 else 0

                model_p = p_away_f5_net if market == 'F5_ML_Away' else p_home_f5_net
                kalshi_vf = vf_away_f5 if market == 'F5_ML_Away' else vf_home_f5

                # f5Amplified: xERAGap >= 1.5
                away_xfip = away_ps.get('xFIP') or away_ps.get('seasonFIP') or 4.0
                home_xfip  = home_ps.get('xFIP') or home_ps.get('seasonFIP') or 4.0
                xera_gap    = abs(away_xfip - home_xfip)
                f5_amplified = xera_gap >= 1.5

                edge_val = calibrated_edge(model_p, kalshi_vf, CAL_MEDIUM)
                conf = confidence_from_edge(edge_val, f5_amplified=f5_amplified)

                # Apply Rule 53 lineup downgrade if gate fired
                if any('Rule 53' in g for g in gates) and conf not in (None,):
                    conf = 'PAPER'
                _f5_lineup_ctx = away_lineup_ctx.copy()

                # Rule 71 F5: block if model vs Kalshi F5 VF > 12%
                gap = abs(model_p - kalshi_vf) * 100
                if gap > 12.0:
                    gates.append(f'Rule71-F5: model {model_p*100:.1f}% vs KalshiF5VF {kalshi_vf*100:.1f}% = {gap:.1f}% > 12%')
                    conf = None

                if conf is None:
                    row = rejected_row(
                        market,
                        reason=gates[0] if gates else f'edge {edge_val}% below threshold',
                        kalshiPrice=am_val, kalshiVF=round(kalshi_vf*100,2),
                        modelProb=round(model_p*100,2), edge=edge_val, gatesFired=gates,
                        notes=f'f5Amplified={f5_amplified}, xERAGap={xera_gap:.2f}',
                        rawEdgeVsVF=raw_edge_pct(model_p, kalshi_vf),
                        calibrationFactor=CAL_MEDIUM,
                        calibratedEdgeVsVF=round((raw_edge_pct(model_p, kalshi_vf) or 0) * CAL_MEDIUM, 3),
                        **proj_context
                    )
                    row['reasonCodes'] = build_reason_codes('Rejected', row)
                    rows[market] = row
                else:
                    f5_ticker = f5ml.get('away_ticker') if market == 'F5_ML_Away' else f5ml.get('home_ticker')
                    # Phase 1A: get executable price for F5 (yes_ask from registry)
                    f5_prices = (f5ml.get('prices') or {}).get('away' if market == 'F5_ML_Away' else 'home') or {}
                    def _tc(v):
                        if v is None: return None
                        f = float(v); return round(f * 100 if f <= 1.0 else f, 2)
                    f5_yes_ask_c = _tc(f5_prices.get('yes_ask'))
                    if f5_yes_ask_c is None and am_val is not None:
                        imp = abs(am_val)/(abs(am_val)+100) if am_val < 0 else 100/(am_val+100)
                        f5_yes_ask_c = round(imp * 100, 2)
                    ef_f5 = build_edge_fields(model_p, kalshi_vf, f5_yes_ask_c, CAL_MEDIUM, snapshot_ts)
                    max_bet = f5_yes_ask_c
                    row = accepted_row(
                        market,
                        kalshiPrice=am_val, kalshiImplied=round(kalshi_vf*100,2),
                        kalshiVF=round(kalshi_vf*100,2),
                        modelProb=round(model_p*100,2),
                        confidence=conf, betSize=bet_size(conf, market),
                        gatesFired=gates,
                        notes=f'f5Amplified={f5_amplified}, xERAGap={xera_gap:.2f}',
                        **ef_f5,
                        maxBetPrice=max_bet,
                        confidenceTier=conf,
                        **identity(f5_ticker, 'KXMLBF5'),
                        **proj_context,
                        **_f5_lineup_ctx,
                    )
                    row['reasonCodes'] = build_reason_codes('Accepted', row)
                    rows[market] = row
            except Exception as e:
                import traceback as _tb
                _tbstr = _tb.format_exc()
                _r = failed_row(market, f'{type(e).__name__}: {e}')
                _r['evaluationError'] = f'{type(e).__name__}: {e}' + '\n' + _tbstr[:400]
                rows[market] = _r

    # ── NRFI / YRFI ───────────────────────────────────────────────────────
    rfi = kalshi.get('nrfi_yrfi', {}) or {}
    nrfi_am = rfi.get('nrfi_american')
    yrfi_am = rfi.get('yrfi_american')
    nrfi_implied = rfi.get('nrfi_implied')
    yrfi_implied = rfi.get('yrfi_implied')

    if nrfi_am is None or yrfi_am is None:
        rows['NRFI'] = missing_row('NRFI', ['odds.kalshi.nrfi_yrfi.nrfi_american'])
        rows['YRFI'] = missing_row('YRFI', ['odds.kalshi.nrfi_yrfi.yrfi_american'])
    elif away_proj is None:
        rows['NRFI'] = missing_row('NRFI', proj_missing)
        rows['YRFI'] = missing_row('YRFI', proj_missing)
    else:
        try:
            gates_nrfi = []
            gates_yrfi = []

            # Rule 34: NRFI blocked when game total >= 8.0
            if total_line is not None and total_line >= 8:
                gates_nrfi.append(f'Rule 34: NRFI blocked — Kalshi total line={total_line} >= 8.0')

            # Four-factor composite (simplified from what we have)
            # Factor 1: both pitchers' 1st-inning xERA
            away_fi = away_ps.get('firstInningSplit', {}) or {}
            home_fi  = home_ps.get('firstInningSplit', {}) or {}
            away_fi_xera = away_fi.get('firstInningXERA')
            home_fi_xera  = home_fi.get('firstInningXERA')

            fi_data_missing = []
            if away_fi_xera is None:
                fi_data_missing.append(f'away.pitcherSavant.firstInningSplit.firstInningXERA')
            if home_fi_xera is None:
                fi_data_missing.append(f'home.pitcherSavant.firstInningSplit.firstInningXERA')

            # P(NRFI) = P(away scores 0 in 1st) * P(home scores 0 in 1st)
            # Approximate: 1st-inning runs ~ Poisson(total_proj / 9) per team
            inning1_away = (away_proj / 9) if away_proj else None
            inning1_home  = (home_proj  / 9) if home_proj  else None

            p_nrfi_away = poisson_pmf(0, inning1_home)  # away scores 0 against home pitcher
            p_nrfi_home  = poisson_pmf(0, inning1_away)   # home scores 0 against away pitcher
            p_nrfi = p_nrfi_away * p_nrfi_home
            p_yrfi = 1.0 - p_nrfi

            # VF from NRFI/YRFI single binary market
            vf_nrfi = (nrfi_implied or 50) / 100
            vf_yrfi = (yrfi_implied or 50) / 100

            edge_nrfi = calibrated_edge(p_nrfi, vf_nrfi, CAL_MEDIUM)
            edge_yrfi = calibrated_edge(p_yrfi, vf_yrfi, CAL_MEDIUM)

            conf_nrfi = confidence_from_edge(edge_nrfi)
            conf_yrfi = confidence_from_edge(edge_yrfi)

            if gates_nrfi:
                conf_nrfi = None

            # Rule 52: YRFI/NRFI lineup gate — uses lineupConfirmedOfficial per Phase 1B.
            if not (away_lineup_official and home_lineup_official):
                missing_sides_rfi = []
                if not away_lineup_official: missing_sides_rfi.append('away')
                if not home_lineup_official: missing_sides_rfi.append('home')
                rfi_gate_msg = f'Rule 52: YRFI/NRFI requires confirmed lineups for both teams — {", ".join(missing_sides_rfi)} unconfirmed (lineupConfirmedOfficial=False) → PAPER'
                gates_nrfi.append(rfi_gate_msg)
                gates_yrfi.append(rfi_gate_msg)
                if conf_nrfi not in (None,): conf_nrfi = 'PAPER'
                if conf_yrfi not in (None,): conf_yrfi  = 'PAPER'

            # Build NRFI / YRFI rows
            # Rule 40: four-factor composite required for NRFI/YRFI.
            # If Factor 1 (both pitchers' 1st-inning xERA) is missing, the composite is
            # incomplete — maximum allowed status is PAPER for BOTH NRFI and YRFI.
            # This is enforced here in the ledger (not just in explanatory text) so that
            # no YRFI or NRFI can be classified as MEDIUM or HIGH when 1st-inning xERA
            # data is absent.  Same gate fires for both sides of the binary market.
            nrfi_notes = f'1st-inn approx: away={inning1_away:.3f} home={inning1_home:.3f} R/inn'
            yrfi_notes_extra = ''
            if fi_data_missing:
                rule40_msg = (
                    f'Rule 40 incomplete — first-inning xERA missing for: '
                    f'{fi_data_missing}; paper cap applied'
                )
                gates_nrfi.append(rule40_msg)
                gates_yrfi.append(rule40_msg)
                nrfi_notes += f' | Missing Factor 1 (1st-inn xERA): {fi_data_missing} — Paper cap'
                yrfi_notes_extra = f' | Missing Factor 1 (1st-inn xERA): {fi_data_missing} — Paper cap'
                if conf_nrfi not in (None,): conf_nrfi = 'PAPER'
                if conf_yrfi not in (None,): conf_yrfi = 'PAPER'

            def _tc2(v):
                if v is None: return None
                f = float(v); return round(f * 100 if f <= 1.0 else f, 2)
            rfi_yes_bid = rfi.get('yrfi_bid')
            rfi_yes_ask = rfi.get('yrfi_ask')

            if conf_nrfi is None:
                rows['NRFI'] = rejected_row(
                    'NRFI',
                    reason=gates_nrfi[0] if gates_nrfi else f'edge {edge_nrfi}% below {THRESHOLD_PAPER}% floor',
                    kalshiPrice=nrfi_am, kalshiVF=round(vf_nrfi*100,2),
                    modelProb=round(p_nrfi*100,2), edge=edge_nrfi, gatesFired=gates_nrfi,
                    notes=nrfi_notes, **proj_context, **away_lineup_ctx,
                )
            else:
                nrfi_executable = round(100 - _tc2(rfi_yes_bid), 2) if rfi_yes_bid is not None else None
                ef_nrfi = build_edge_fields(p_nrfi, vf_nrfi, nrfi_executable, CAL_MEDIUM, snapshot_ts)
                row = accepted_row(
                    'NRFI',
                    kalshiPrice=nrfi_am, kalshiImplied=nrfi_implied, kalshiVF=round(vf_nrfi*100,2),
                    modelProb=round(p_nrfi*100,2),
                    confidence=conf_nrfi, betSize=bet_size(conf_nrfi, 'NRFI'),
                    notes=nrfi_notes,
                    gatesFired=gates_nrfi,
                    **ef_nrfi,
                    maxBetPrice=nrfi_executable,
                    confidenceTier=conf_nrfi,
                    **identity(rfi.get('ticker'), 'KXMLBRFI'),
                    **proj_context,
                    **away_lineup_ctx,
                )
                row['reasonCodes'] = build_reason_codes('Accepted', row)
                rows['NRFI'] = row

            yrfi_notes = f'P(YRFI)={p_yrfi*100:.1f}% (1-NRFI)' + yrfi_notes_extra
            if conf_yrfi is None:
                rows['YRFI'] = rejected_row(
                    'YRFI',
                    reason=f'edge {edge_yrfi}% below {THRESHOLD_PAPER}% floor',
                    kalshiPrice=yrfi_am, kalshiVF=round(vf_yrfi*100,2),
                    modelProb=round(p_yrfi*100,2), edge=edge_yrfi, gatesFired=gates_yrfi,
                    notes=yrfi_notes, **proj_context, **away_lineup_ctx,
                )
            else:
                yrfi_yes_ask_c = _tc2(rfi.get('yrfi_ask')) if rfi.get('yrfi_ask') is not None else None
                ef_yrfi = build_edge_fields(p_yrfi, vf_yrfi, yrfi_yes_ask_c, CAL_MEDIUM, snapshot_ts)
                row = accepted_row(
                    'YRFI',
                    kalshiPrice=yrfi_am, kalshiImplied=yrfi_implied, kalshiVF=round(vf_yrfi*100,2),
                    modelProb=round(p_yrfi*100,2),
                    confidence=conf_yrfi, betSize=bet_size(conf_yrfi, 'YRFI'),
                    notes=yrfi_notes,
                    gatesFired=gates_yrfi,
                    **ef_yrfi,
                    maxBetPrice=yrfi_yes_ask_c,
                    confidenceTier=conf_yrfi,
                    **identity(rfi.get('ticker'), 'KXMLBRFI'),
                    **proj_context,
                    **away_lineup_ctx,
                )
                row['reasonCodes'] = build_reason_codes('Accepted', row)
                rows['YRFI'] = row
        except Exception as e:
            import traceback as _tb
            _tbstr = _tb.format_exc()
            for _mkt in ('NRFI', 'YRFI'):
                if _mkt not in rows:
                    _r = failed_row(_mkt, f'{type(e).__name__}: {e}')
                    _r['evaluationError'] = f'{type(e).__name__}: {e}' + '\n' + _tbstr[:400]
                    rows[_mkt] = _r

    # ── Ensure all required markets have a row ─────────────────────────────
    for mkt in REQUIRED_MARKETS:
        if mkt not in rows:
            rows[mkt] = failed_row(
                mkt,
                f'Market not evaluated — missing from evaluation logic (programming error)'
            )

    # Rule 71 patch: apply bet_eligibility_status, clv_capture_status, review_integrity_status
    # to every row AFTER all edge/confidence/price logic is complete.
    # apply_eligibility() NEVER changes status/edge/confidence/betSize.
    # Missing CLV data does NOT block a live actionable bet.
    result_rows = [rows[m] for m in REQUIRED_MARKETS]
    for row in result_rows:
        apply_eligibility(row, clv_snapshot_captured=None)
    return result_rows


def _check_accepted_identity_and_concentration(games):
    """
    Post-build checks:
    1. Warn if any Accepted row has null marketTicker.
    2. Non-blocking portfolio concentration warning (real bets only).
    """
    import sys
    from collections import Counter

    accepted_bets = []

    for g in games:
        away_abbr = g.get('away', {}).get('abbr', '?')
        home_abbr = g.get('home', {}).get('abbr', '?')
        game_id   = f'{away_abbr}@{home_abbr}'
        for row in g.get('marketLedger', []):
            if row.get('status') == 'Accepted':
                if not row.get('marketTicker'):
                    print(
                        f'DATA-HEALTH WARNING: Accepted row has null marketTicker '
                        f'for market {row.get("market")} game {game_id}',
                        file=sys.stderr
                    )
                accepted_bets.append({
                    'market':  row.get('market'),
                    'betType': row.get('betType', 'REAL'),
                })

    # Portfolio concentration warning (non-blocking)
    real_bets = [b for b in accepted_bets if b.get('betType') != 'PAPER']
    if real_bets:
        market_counts = Counter(b['market'] for b in real_bets)
        total = len(real_bets)
        for market, count in market_counts.items():
            pct = count / total
            if pct > 0.45:
                print(
                    f'PORTFOLIO WARNING: {count}/{total} accepted real bets are {market}. '
                    f'Review concentration before placing full card.'
                )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    path = os.path.join(os.path.dirname(__file__), '..', 'data', 'slate.json')
    with open(path) as f:
        slate = json.load(f)

    games = slate.get('games', [])
    total_rows = 0
    status_counts = {s: 0 for s in ['Accepted', 'Rejected', 'Missing Data', 'Evaluation Failed']}

    for g in games:
        away = g.get('away', {}).get('abbr', '?')
        home  = g.get('home', {}).get('abbr', '?')

        # Skip quarantined games — all their markets are excluded from real-money
        if g.get('excludedFromSlate'):
            reason = g.get('exclusionReason', 'QUARANTINED')
            ledger = [
                rejected_row(m, f'EXCLUDED: {reason}')
                for m in REQUIRED_MARKETS
            ]
            g['marketLedger'] = ledger
            print(f'{away}@{home}: EXCLUDED (quarantined) — {reason[:80]}')
            continue

        try:
            ledger = evaluate_game(g)
        except Exception as e:
            import traceback as _tb
            _tbstr = _tb.format_exc()
            print(f'ERROR evaluating {away}@{home}: {type(e).__name__}: {e}', file=sys.stderr)
            print(_tbstr, file=sys.stderr)
            _errmsg = f'Game-level error: {type(e).__name__}: {e}' + '\n' + _tbstr[:600]
            ledger = [failed_row(m, _errmsg) for m in REQUIRED_MARKETS]

        # Validate completeness before writing
        ledger_markets = {row['market'] for row in ledger}
        for req in REQUIRED_MARKETS:
            if req not in ledger_markets:
                ledger.append(failed_row(req, 'Not evaluated — missing from ledger'))

        g['marketLedger'] = ledger
        total_rows += len(ledger)

        for row in ledger:
            s = row.get('status', 'Evaluation Failed')
            status_counts[s] = status_counts.get(s, 0) + 1

        # Print game summary
        accepted = [r['market'] for r in ledger if r['status'] == 'Accepted']
        missing  = [r['market'] for r in ledger if r['status'] == 'Missing Data']
        failed   = [r['market'] for r in ledger if r['status'] == 'Evaluation Failed']
        print(f'{away}@{home}: {len(ledger)} rows | '
              f'Accepted={len(accepted)} Rejected={len(ledger)-len(accepted)-len(missing)-len(failed)} '
              f'MissingData={len(missing)} Failed={len(failed)}')
        if accepted: print(f'  ACCEPTED: {accepted}')
        if missing:  print(f'  MISSING:  {missing}')
        if failed:   print(f'  FAILED:   {failed}')

    with open(path, 'w') as f:
        json.dump(slate, f)

    # ── Phase 3 immutable pipeline: also publish this stage's output as
    # its own artifact (data/pipeline/<date>/recommendations.json).
    # build_market_ledger.py is what populates marketLedger (the
    # Recommendation Layer, see docs/IMMUTABLE_PIPELINE.md), before
    # risk_gate.py's portfolio decisions run. Purely additive —
    # best-effort, never allowed to affect the primary data/slate.json
    # write above, which is already complete by this point.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'lib'))
        from pipeline_artifacts import write_stage_artifact as _write_stage_artifact
        _write_stage_artifact('recommendations', slate.get('date', ''), slate)
    except Exception as _e:
        print(f'WARNING: could not write recommendations pipeline artifact: {_e}')

    print(f'\nTotal: {len(games)} games, {total_rows} market rows')
    for s, c in status_counts.items():
        print(f'  {s}: {c}')
    print(f'Written marketLedger to all games in {path}')

    # F5 moneyline visibility check — final pipeline stage
    # Counts F5_ML rows in the completed ledger by status.
    # Missing Data = price never reached slate. Rejected/Accepted = price present.
    _f5_accepted   = 0
    _f5_rejected   = 0
    _f5_missing    = 0
    _f5_failed     = 0
    for g in games:
        for row in g.get('marketLedger', []):
            if row.get('market') in ('F5_ML_Away', 'F5_ML_Home'):
                s = row.get('status', '')
                if s == 'Accepted':      _f5_accepted += 1
                elif s == 'Rejected':    _f5_rejected += 1
                elif s == 'Missing Data': _f5_missing += 1
                else:                    _f5_failed   += 1
    _f5_with_price = _f5_accepted + _f5_rejected  # price present = evaluated (not missing)
    _f5_total_rows = _f5_accepted + _f5_rejected + _f5_missing + _f5_failed
    _games_with_f5 = _f5_with_price // 2  # 2 rows per game (Away + Home)
    print(f'\n[F5-VISIBILITY] F5_ML rows in ledger: {_f5_total_rows} total '
          f'(Accepted={_f5_accepted} Rejected={_f5_rejected} MissingData={_f5_missing} Failed={_f5_failed})')
    print(f'[F5-VISIBILITY] Games with F5 moneyline price in final slate: {_games_with_f5}/{len(games)}')
    if _f5_missing > 0 and _f5_with_price == 0:
        print('[F5-VISIBILITY] WARNING: F5 moneyline discovery succeeded but mapping into the slate failed.')
        print('[F5-VISIBILITY] All F5_ML rows show Missing Data — price never reached odds.kalshi.f5ml.')
        print('[F5-VISIBILITY] Root cause: check parse_suffix() in build_kalshi_registry.py (June 8 bug pattern).')
    elif _f5_missing > 0:
        print(f'[F5-VISIBILITY] NOTE: {_f5_missing} F5_ML rows still Missing Data '
              f'(partial — {_games_with_f5} game(s) have prices, {_f5_missing // 2} do not).')
    elif _f5_with_price > 0:
        print(f'[F5-VISIBILITY] OK: F5 moneyline prices present in final slate for all {_games_with_f5} game(s) evaluated.')

    # ── F5 sportsbook vs Kalshi distinction ──────────────────────────────────
    # If sportsbook F5 odds are present but Kalshi F5 is missing, emit targeted warnings.
    # Never say "F5 not offered on any book" when FD/DK/MGM F5 data is present.
    _sb_f5_games = 0
    _kal_f5_games = 0
    for _gf in games:
        _odds = _gf.get('odds') or {}
        _sb_f5 = (
            (_odds.get('fanduel') or {}).get('f5ml') or
            (_odds.get('draftkings') or {}).get('f5ml') or
            (_odds.get('betmgm') or {}).get('f5ml')
        )
        _kal_f5 = (_odds.get('kalshi') or {}).get('f5ml') or {}
        if _sb_f5:
            _sb_f5_games += 1
        if _kal_f5.get('away') is not None:
            _kal_f5_games += 1

    if _sb_f5_games > 0 and _kal_f5_games == 0:
        print(f'[F5-VISIBILITY] Sportsbook F5 available ({_sb_f5_games} game(s) on FD/DK/MGM); '
              f'Kalshi KXMLBF5 missing — cannot log Kalshi F5 bet.')
        print('[F5-VISIBILITY] Rule 25 NOTE: F5 analysis uses sportsbook odds but Kalshi KXMLBF5 price unavailable.')
        print('[F5-VISIBILITY] Sportsbook F5 odds detected: FD/DK/MGM.')
    elif _sb_f5_games > 0 and _kal_f5_games > 0:
        print(f'[F5-VISIBILITY] OK: Sportsbook F5 ({_sb_f5_games} games) AND Kalshi F5 ({_kal_f5_games} games) both present.')
    elif _sb_f5_games == 0:
        print('[F5-VISIBILITY] NOTE: No sportsbook F5 odds detected (FD/DK/MGM all absent).')

    # Post-build: identity check and portfolio concentration warning
    _check_accepted_identity_and_concentration(games)


if __name__ == '__main__':
    main()
