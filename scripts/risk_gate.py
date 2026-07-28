#!/usr/bin/env python3
"""
scripts/risk_gate.py v1.0
==========================
Post-evaluation safety pass. Reads data/slate.json after build_market_ledger.py
has run, enforces:

  1. TT Safety Gate
     - All TT bets require ttInputs evidence block
     - Missing critical evidence → downgrade to PAPER
     - TT Over N: requiredRunsToWin = N+1; edge threshold 2.5% minimum
     - Max 4 TT real-money bets per slate
     - Max 20u total TT real-money stake

  2. Portfolio Composition Gate
     - TT ≤ 40% of total real-money stake
     - ML+F5 must be ≥ 50% of real-money stake if ≥2 qualify
     - Max 40u total real-money stake (daily risk cap)
     - If slate is TT-dominated and ML/F5 < 2 qualify, output PAPER_ONLY

Writes a risk_gate_report to data/meta.json and exits:
  0 — GO (or PAPER_ONLY downgrade applied, slate still valid)
  1 — hard FAIL (e.g. slate.json unreadable)

Prints portfolio summary and GO / PAPER_ONLY / NO_GO decision.
"""

import json
import os
import sys
from datetime import datetime, timezone

ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLATE_PATH = os.path.join(ROOT, 'data', 'slate.json')
META_PATH  = os.path.join(ROOT, 'data', 'meta.json')

# Same live/final/postponed game gate used by write_pending_bets.py and
# validate_bet_logging.py. Centralized here so all three scripts agree on
# which games can produce real-money output — divergence between this file
# and write_pending_bets.py previously caused portfolio composition (and the
# downstream validate_bet_logging expected-count) to include bets that
# write_pending_bets correctly refused to log for live/final games.
sys.path.insert(0, os.path.join(ROOT, 'lib'))
from postponed_guard import check_game_status

REAL_MONEY_TIERS  = {'HIGH', 'MEDIUM'}
TT_MARKETS        = {'TT_Away_Over', 'TT_Home_Over'}
ML_F5_MARKETS     = {'ML_Away', 'ML_Home', 'F5_ML_Away', 'F5_ML_Home'}

# ── Thresholds ────────────────────────────────────────────────────────────────
TT_MIN_EDGE_PCT        = 2.5    # below this → downgrade TT to PAPER
TT_MAX_BETS            = 4      # max real-money TT bets per slate
TT_MAX_STAKE           = 20.0   # max total TT real-money stake (u)
TT_MAX_STAKE_PCT       = 0.40   # TT ≤ 40% of total stake
ML_F5_MIN_STAKE_PCT    = 0.50   # ML+F5 ≥ 50% of total stake if ≥2 qualify
DAILY_RISK_CAP         = 40.0   # max total real-money stake (u)

# Critical TT evidence fields — if any are None, downgrade to PAPER
TT_CRITICAL_FIELDS = [
    'awayProjRuns', 'homeProjRuns',   # projected runs (the betting team's)
    'kalshiPrice',                     # market price exists
    'modelProb',                       # model probability
    'line',                            # the line to beat
]

# Fields that must exist for the SPECIFIC TT side being bet
TT_CRITICAL_SIDE_FIELDS = {
    'TT_Away_Over': 'awayProjRuns',
    'TT_Home_Over': 'homeProjRuns',
}


def check_tt_evidence(entry):
    """
    Returns (ok, missing_fields) for a TT entry.
    ok=True if all critical evidence is present.
    """
    missing = []
    for field in TT_CRITICAL_FIELDS:
        if entry.get(field) is None:
            missing.append(field)
    # Side-specific
    mkt = entry.get('market', '')
    side_field = TT_CRITICAL_SIDE_FIELDS.get(mkt)
    if side_field and entry.get(side_field) is None:
        if side_field not in missing:
            missing.append(side_field)
    return (len(missing) == 0), missing


def compute_tt_inputs(entry):
    """
    Pure. Returns the ttInputs summary block for a TT marketLedger entry
    as a NEW dict, reading `entry` but never mutating it. Same field-by-
    field construction enrich_tt_inputs() has always used; factored out
    so the decision logic below can be evaluated without touching the
    caller's entry until the impure wrapper (apply_tt_safety) decides to
    apply it.
    """
    mkt  = entry.get('market', '')
    line = entry.get('line')
    tt_inputs = {
        'projectedTeamRuns':    entry.get('awayProjRuns') if 'Away' in mkt else entry.get('homeProjRuns'),
        'teamTotalLine':        line,
        'requiredRunsToWin':    (int(line) + 1) if line is not None else None,
        'modelProbOver':        entry.get('modelProb'),
        'marketImpliedProb':    entry.get('kalshiImplied') or entry.get('kalshiVF'),
        'edgePct':              entry.get('edge') or entry.get('calibratedEdgeVsExecutable'),
        'calibrationFactor':    entry.get('calibrationFactor'),
        'awayProjRuns':         entry.get('awayProjRuns'),
        'homeProjRuns':         entry.get('homeProjRuns'),
        # Pitcher/context fields — populated if available from enrich_data output
        # These are advisory — absence triggers PAPER downgrade
        'opposingStarterName':  None,   # not yet in ledger — future enrichment
        'starterXfip':          None,
        'bullpenRating':        None,
        'parkAdjustment':       entry.get('awayProjRuns'),  # park_adj embedded in proj
        'weatherAdjustment':    None,
        'confirmedLineup':      entry.get('lineupPosted'),
        'lineupDataQuality':    entry.get('lineupDataQuality'),
        'dataCompletenessScore': None,
        'missingInputs':        [],
    }
    missing_advisory = []
    if tt_inputs['opposingStarterName'] is None:
        missing_advisory.append('opposingStarterName')
    if tt_inputs['starterXfip'] is None:
        missing_advisory.append('starterXfip')
    if tt_inputs['bullpenRating'] is None:
        missing_advisory.append('bullpenRating')
    if tt_inputs['weatherAdjustment'] is None:
        missing_advisory.append('weatherAdjustment')
    tt_inputs['missingInputs'] = missing_advisory
    tt_inputs['dataCompletenessScore'] = round(
        1.0 - len(missing_advisory) / 4.0, 2
    )
    return tt_inputs


def enrich_tt_inputs(entry):
    """Add ttInputs summary block to a TT marketLedger entry in place."""
    entry['ttInputs'] = compute_tt_inputs(entry)
    return entry


def evaluate_candidate_tt_risk(entry):
    """
    Pure decision function (Phase 7 Part 6). Takes a read-only TT
    marketLedger entry and returns a decision object describing exactly
    what apply_tt_safety() should do with it, without reading the clock,
    touching any file, printing, or mutating `entry` in any way:

      {
        'ttInputs':           the enrichment block (always present),
        'requiredRunsToWin':  int(line)+1, or None if line is None,
        'evaluated':          True iff status=='Accepted' and tier is
                               real-money (HIGH/MEDIUM) — the same gate
                               apply_tt_safety has always used before
                               running the two downgrade checks,
        'reasons':            [] unless evaluated and at least one rule
                               failed; evidence-check reason (if any)
                               always precedes the edge-check reason
                               (legacy order, not reordered),
        'downgrade':          bool(reasons),
      }
    """
    tt_inputs = compute_tt_inputs(entry)
    line = entry.get('line')
    required_runs = (int(line) + 1) if line is not None else None

    tier   = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
    status = entry.get('status', '')
    evaluated = (status == 'Accepted' and tier in REAL_MONEY_TIERS)

    reasons = []
    if evaluated:
        edge = entry.get('edge') or entry.get('calibratedEdgeVsExecutable') or 0
        ev_ok, missing_ev = check_tt_evidence(entry)
        if not ev_ok:
            reasons.append(f"TT_MODEL_INPUTS_INCOMPLETE: missing {missing_ev}")
        if edge < TT_MIN_EDGE_PCT:
            reasons.append(f"TT_EDGE_BELOW_2.5pct: edge={edge:.2f}%")

    return {
        'ttInputs': tt_inputs,
        'requiredRunsToWin': required_runs,
        'evaluated': evaluated,
        'reasons': reasons,
        'downgrade': bool(reasons),
    }


def apply_tt_safety(slate, now_ts=None):
    """
    Applies TT safety rules to all TT entries in the marketLedger.
    Modifies slate in place. Returns list of downgrade events.

    Thin impure shell around evaluate_candidate_tt_risk(): the pure
    function decides what should happen to each candidate, this function
    is the only place that actually writes those decisions back onto the
    entry — mutation is isolated here, not inside the decision function.
    """
    downgrades = []

    for g in slate.get('games', []):
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"

        # Skip quarantined games entirely — they produce no real-money output
        if g.get('excludedFromSlate'):
            continue

        # Skip live/final/postponed games — write_pending_bets.py will never
        # log a real-money bet for these, so they must not factor into the
        # TT safety pass either (same gate, same "now").
        if check_game_status(g, current_utc=now_ts).get('shouldSkip'):
            continue

        for entry in g.get('marketLedger', []):
            mkt  = entry.get('market', '')
            if mkt not in TT_MARKETS:
                continue

            decision = evaluate_candidate_tt_risk(entry)

            # Always enrich ttInputs
            entry['ttInputs'] = decision['ttInputs']

            if not decision['evaluated']:
                continue

            # Set requiredRunsToWin whenever line is present, regardless
            # of whether this entry gets downgraded — only reached for
            # evaluated (Accepted + real-money-tier) entries, exactly as
            # the original single-pass implementation did (the `continue`
            # above ran before this line ever executed for other
            # statuses/tiers).
            if entry.get('line') is not None:
                entry['requiredRunsToWin'] = decision['requiredRunsToWin']

            if decision['downgrade']:
                reasons = decision['reasons']
                entry['status']          = 'Accepted'    # keep in ledger
                entry['confidence']      = 'PAPER'
                entry['confidenceTier']  = 'PAPER'
                entry['betSize']         = 1.0
                entry['realMoneyBlocked'] = True
                entry['blockReason']     = '; '.join(reasons)
                entry.setdefault('gatesFired', []).extend(reasons)
                downgrades.append({
                    'game': game, 'market': mkt, 'reason': reasons
                })

    return downgrades


def build_risk_portfolio(real_entries):
    """
    Pure decision function (Phase 7 Part 6). `real_entries` is a list of
    (game_label, entry) tuples for every currently-real-money-tier
    Accepted entry — the SAME collection apply_portfolio_rules has always
    built before doing anything else. Never mutates any entry it is
    given, never reads the clock, never touches a file, never prints.

    Returns (report, to_downgrade, decision):
      report        — the risk_gate_report dict, in the exact key order
                       the original single-function implementation built
                       it in (so meta.json's serialized bytes are
                       unaffected by this refactor).
      to_downgrade  — list of (game_label, entry) tuples the caller must
                       force to PAPER (TT_MAX_BETS_EXCEEDED only — the
                       later PAPER_ONLY-driven full-portfolio downgrade
                       is a separate step, still performed by main()).
      decision      — 'GO' or 'PAPER_ONLY' (see apply_portfolio_rules'
                       docstring for why 'NO_GO' is never produced).
    """
    report = {
        'by_family': {},
        'total_real_stake': 0.0,
        'total_bets': 0,
        'tt_stake': 0.0,
        'tt_bets': 0,
        'ml_f5_stake': 0.0,
        'ml_f5_bets': 0,
        'concentration_warnings': [],
        'downgrades_applied': [],
    }

    # Tally by family
    fam_map = {}
    for game, entry in real_entries:
        mkt   = entry.get('market', '')
        stake = float(entry.get('betSize') or 0)
        if mkt in TT_MARKETS:    fam = 'TT'
        elif mkt in ML_F5_MARKETS: fam = 'ML_F5'
        else:                      fam = 'OTHER'

        if fam not in fam_map:
            fam_map[fam] = {'bets': 0, 'stake': 0.0, 'entries': []}
        fam_map[fam]['bets'] += 1
        fam_map[fam]['stake'] += stake
        fam_map[fam]['entries'].append((game, entry))

    total_stake = sum(v['stake'] for v in fam_map.values())
    total_bets  = sum(v['bets']  for v in fam_map.values())

    tt_stake   = fam_map.get('TT', {}).get('stake', 0.0)
    tt_bets    = fam_map.get('TT', {}).get('bets', 0)
    mlf5_stake = fam_map.get('ML_F5', {}).get('stake', 0.0)
    mlf5_bets  = fam_map.get('ML_F5', {}).get('bets', 0)

    report.update({
        'by_family':        {k: {'bets': v['bets'], 'stake': v['stake']} for k, v in fam_map.items()},
        'total_real_stake': total_stake,
        'total_bets':       total_bets,
        'tt_stake':         tt_stake,
        'tt_bets':          tt_bets,
        'ml_f5_stake':      mlf5_stake,
        'ml_f5_bets':       mlf5_bets,
    })

    warnings = []
    to_downgrade = []

    # ── Rule: daily risk cap ───────────────────────────────────────────────
    if total_stake > DAILY_RISK_CAP:
        warnings.append(f"DAILY_RISK_CAP exceeded: {total_stake:.1f}u > {DAILY_RISK_CAP}u")

    # ── Rule: TT max bets ──────────────────────────────────────────────────
    tt_entries = fam_map.get('TT', {}).get('entries', [])
    if tt_bets > TT_MAX_BETS:
        # Keep top N by edge, downgrade the rest — decided here, applied
        # by the caller (this function never mutates `entry`).
        tt_entries_sorted = sorted(
            tt_entries,
            key=lambda x: float(x[1].get('edge') or x[1].get('calibratedEdgeVsExecutable') or 0),
            reverse=True
        )
        kept = tt_entries_sorted[:TT_MAX_BETS]
        to_downgrade = tt_entries_sorted[TT_MAX_BETS:]
        warnings.append(f"TT_CONCENTRATION: {tt_bets} TT bets (max {TT_MAX_BETS}) → downgraded {len(to_downgrade)}")
    else:
        kept = tt_entries

    downgrades = [f"{game} {entry.get('market')} → PAPER (TT cap)" for game, entry in to_downgrade]

    # tt_stake_post: stake of the TT entries NOT downgraded above. `kept`
    # entries are never touched by this function, so their tier is still
    # whatever real_entries collected them with (always real-money-tier at
    # this point) — summing their stake here is equivalent to summing
    # AFTER the caller applies the downgrade, without this function ever
    # mutating anything itself.
    tt_stake_post = sum(
        float(e.get('betSize') or 0)
        for _, e in kept
        if (e.get('confidenceTier') or e.get('confidence') or '').upper() in REAL_MONEY_TIERS
    )

    # ── Rule: TT stake cap ─────────────────────────────────────────────────
    if tt_stake_post > TT_MAX_STAKE:
        warnings.append(f"TT_STAKE_CAP: TT stake {tt_stake_post:.1f}u > {TT_MAX_STAKE}u")

    # ── Rule: TT % of total ────────────────────────────────────────────────
    # NOTE: total_stake is deliberately NOT recomputed post-downgrade — a
    # downgraded TT entry's stake still counts in this denominator even
    # though it no longer counts in tt_stake_post's numerator. This is a
    # precise, load-bearing legacy asymmetry preserved exactly, not fixed.
    tt_pct = tt_stake_post / total_stake if total_stake > 0 else 0
    if tt_pct > TT_MAX_STAKE_PCT:
        warnings.append(f"TT_DOMINANCE: TT is {tt_pct:.0%} of stake (max {TT_MAX_STAKE_PCT:.0%})")

    # ── Rule: ML/F5 must be ≥50% if ≥2 qualify ────────────────────────────
    mlf5_pct = mlf5_stake / total_stake if total_stake > 0 else 0
    if mlf5_bets >= 2 and mlf5_pct < ML_F5_MIN_STAKE_PCT:
        warnings.append(
            f"ML_F5_UNDERFILL: ML+F5 only {mlf5_pct:.0%} of stake "
            f"(min {ML_F5_MIN_STAKE_PCT:.0%} when ≥2 qualify)"
        )

    report['concentration_warnings'] = warnings
    report['downgrades_applied']     = downgrades

    # ── GO / PAPER_ONLY decision ───────────────────────────────────────────
    hard_block = any(
        w.startswith('DAILY_RISK_CAP') or w.startswith('TT_DOMINANCE')
        for w in warnings
    )

    if tt_bets > 0 and mlf5_bets == 0 and total_bets == tt_bets:
        # Card is 100% TT with no ML/F5
        decision = 'PAPER_ONLY'
        report['decision_reason'] = 'ALL_TT_NO_ML_F5'
    elif hard_block:
        decision = 'PAPER_ONLY'
        report['decision_reason'] = '; '.join(w for w in warnings if 'DOMINANCE' in w or 'CAP' in w)
    else:
        decision = 'GO'
        report['decision_reason'] = 'Composition checks passed'

    return report, to_downgrade, decision


def apply_portfolio_rules(slate, now_ts=None):
    """
    Enforces concentration limits after TT safety pass.
    Returns (go_decision, report_dict).
    go_decision: 'GO', 'PAPER_ONLY', or 'NO_GO'

    Thin impure shell around build_risk_portfolio(): collects the
    real-money-tier candidates (the only I/O-adjacent, clock-dependent
    part — check_game_status(now_ts)), hands them to the pure decision
    function, then applies the ONLY mutation this stage performs
    (TT_MAX_BETS_EXCEEDED downgrades) here, not inside the decision
    function.
    """
    # Collect all Accepted real-money entries
    real_entries = []   # (game, entry_ref)
    for g in slate.get('games', []):
        # Skip quarantined games
        if g.get('excludedFromSlate'):
            continue
        # Skip live/final/postponed games — same gate write_pending_bets.py
        # applies. Without this, portfolio composition (and the GO/PAPER_ONLY
        # decision) counts stake that will never actually be logged.
        if check_game_status(g, current_utc=now_ts).get('shouldSkip'):
            continue
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game = f"{away}@{home}"
        for entry in g.get('marketLedger', []):
            if entry.get('status') != 'Accepted':
                continue
            tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
            if tier not in REAL_MONEY_TIERS:
                continue
            real_entries.append((game, entry))

    report, to_downgrade, decision = build_risk_portfolio(real_entries)

    for game, entry in to_downgrade:
        entry['confidence']      = 'PAPER'
        entry['confidenceTier']  = 'PAPER'
        entry['betSize']         = 1.0
        entry['realMoneyBlocked'] = True
        entry['blockReason']     = f'TT_MAX_BETS_EXCEEDED: capped at {TT_MAX_BETS}'

    return decision, report


def main():
    if not os.path.exists(SLATE_PATH):
        print(f"ERROR: {SLATE_PATH} not found"); sys.exit(1)

    with open(SLATE_PATH) as f:
        slate = json.load(f)

    date = slate.get('date', 'unknown')
    now_ts = datetime.now(tz=timezone.utc).isoformat()
    print(f"[risk_gate] Running for slate date: {date}")

    # ── Pass 1: TT safety ─────────────────────────────────────────────────
    tt_downgrades = apply_tt_safety(slate, now_ts=now_ts)
    print(f"  TT safety pass: {len(tt_downgrades)} bets downgraded to PAPER")
    for d in tt_downgrades:
        print(f"    {d['game']} {d['market']}: {d['reason']}")

    # ── Pass 2: Portfolio rules ───────────────────────────────────────────
    decision, report = apply_portfolio_rules(slate, now_ts=now_ts)

    print(f"\n  Portfolio composition:")
    print(f"    Total real-money stake: {report['total_real_stake']:.1f}u across {report['total_bets']} bets")
    for fam, d in report['by_family'].items():
        print(f"    {fam}: {d['bets']} bets, {d['stake']:.1f}u")
    if report['concentration_warnings']:
        for w in report['concentration_warnings']:
            print(f"    ⚠️  {w}")
    for d in report['downgrades_applied']:
        print(f"    ↓  {d}")

    print(f"\n  ══ DECISION: {decision} ══")
    print(f"  Reason: {report['decision_reason']}")

    if decision == 'PAPER_ONLY':
        # Force ALL remaining real-money bets to PAPER
        downgraded_count = 0
        for g in slate.get('games', []):
            if g.get('excludedFromSlate'):
                continue
            for entry in g.get('marketLedger', []):
                tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
                if entry.get('status') == 'Accepted' and tier in REAL_MONEY_TIERS:
                    entry['confidence']      = 'PAPER'
                    entry['confidenceTier']  = 'PAPER'
                    entry['betSize']         = 1.0
                    entry['realMoneyBlocked'] = True
                    entry['blockReason']     = f'RISK_GATE_PAPER_ONLY: {report["decision_reason"]}'
                    downgraded_count += 1
        print(f"  Downgraded {downgraded_count} bets to PAPER (portfolio rule)")

    # ── Write back slate with modifications ───────────────────────────────
    with open(SLATE_PATH, 'w') as f:
        json.dump(slate, f, indent=2)
    print(f"\n  Slate updated in-place: {SLATE_PATH}")

    # ── Append risk_gate_report to meta.json ──────────────────────────────
    meta = {}
    if os.path.exists(META_PATH):
        try:
            with open(META_PATH) as f:
                meta = json.load(f)
        except Exception:
            pass

    meta['risk_gate'] = {
        'runAt':    now_ts,
        'decision': decision,
        **report,
    }
    with open(META_PATH, 'w') as f:
        json.dump(meta, f, indent=2)
    print(f"  risk_gate_report written to meta.json")

    return 0


if __name__ == '__main__':
    sys.exit(main())
