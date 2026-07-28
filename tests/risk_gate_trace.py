#!/usr/bin/env python3
"""
tests/risk_gate_trace.py
==========================
Test-only decision-trace mechanism for scripts/risk_gate.py (Phase 7 Part 5).

This module makes NO changes to scripts/risk_gate.py -- it is a pure
observation harness that calls the existing, unmodified
apply_tt_safety()/apply_portfolio_rules() and reconstructs a normalized
trace of what happened by snapshotting each candidate's mutable fields
before and after each pass, and by cross-referencing the functions' own
existing return values (the downgrades list and the report dict). It
cannot and does not alter production output: the two production calls it
wraps (`rg.apply_tt_safety(slate, now_ts)` and
`rg.apply_portfolio_rules(slate, now_ts)`) are invoked exactly as
main() invokes them, with no monkeypatching, no extra arguments, and no
change to slate's mutation. Diff the trace call's returned decision/report
against a direct call and they are identical (see
test_risk_gate_decision_trace.py's test_trace_call_returns_identical_
decision_and_report_to_direct_call).

Why test-only, not a production change: Part 6 (the pure-function
refactor) has not started yet -- risk_gate.py's current shape (in-place
mutation, no injectable observation point) is still the untouched
original. Building the trace as an external wrapper lets Part 5's
requirement ("record the legacy gate sequence... without altering
production output") be satisfied with zero risk to production behavior,
and gives Part 7 (rule-order preservation) a concrete tool to diff
before/after refactor traces against, rather than just comparing final
slate state.

Trace entry shape, per candidate (game+market identity):
  {
    'candidate': 'AWAY@HOME|MARKET|TICKER',
    'rules_evaluated': ['TT_EVIDENCE', 'TT_EDGE'] or
                        ['PORTFOLIO_TT_MAX_BETS'] etc. (order = evaluation
                        order, exactly as risk_gate.py itself evaluates
                        them -- this trace does not reorder anything),
    'pass_fail': {'TT_EVIDENCE': True, 'TT_EDGE': False, ...},
    'first_terminal_reason': the first rule name that failed, or None,
    'stake_before': betSize snapshot before this pass,
    'stake_after': betSize snapshot after this pass,
    'tier_before': confidenceTier snapshot before this pass,
    'tier_after': confidenceTier snapshot after this pass,
    'final_classification': confidenceTier after ALL passes complete,
    'execution_included': bool -- status=='Accepted' and tier in
                           REAL_MONEY_TIERS after ALL passes complete.
  }

Family exposure trace (portfolio pass only), per family:
  {
    'family': 'TT'/'ML_F5'/'OTHER',
    'bets_before': int, 'stake_before': float  (pre-max-bets-downgrade tally,
                    i.e. report['tt_bets']/report['tt_stake'] etc.),
    'bets_after': int, 'stake_after': float     (recomputed by this trace
                    from live entry state post-run -- risk_gate.py itself
                    only recomputes this for the TT family as
                    tt_stake_post; this trace recomputes it for every
                    family uniformly, for observational purposes only),
  }
"""

import copy

TT_RULE_NAMES = ['TT_EVIDENCE', 'TT_EDGE']


def _candidate_id(game_label, entry):
    return f"{game_label}|{entry.get('market')}|{entry.get('ticker')}"


def _snapshot(entry):
    return {
        'betSize': entry.get('betSize'),
        'confidenceTier': entry.get('confidenceTier'),
        'status': entry.get('status'),
    }


def build_decision_trace(rg, slate, now_ts=None):
    """
    Runs the exact same two production calls main() runs
    (apply_tt_safety then apply_portfolio_rules) against `slate` in place,
    and returns (tt_downgrades, decision, report, trace) where
    tt_downgrades/decision/report are the SAME values a direct,
    untraced call would produce (byte-identical -- see the equivalence
    test), and `trace` is an additional, purely observational dict:
      {
        'candidates': [ per-candidate trace entries, in the same order
                        risk_gate.py itself iterates games/entries ],
        'family_exposure': [ per-family exposure trace entries ],
      }
    """
    # Build the candidate list and pre-TT-pass snapshots BEFORE calling
    # apply_tt_safety, in the exact game/entry iteration order
    # risk_gate.py itself uses (list order, no sorting).
    candidates = []
    for g in slate.get('games', []):
        away = g.get('away', {}).get('abbr', '')
        home = g.get('home', {}).get('abbr', '')
        game_label = f"{away}@{home}"
        if g.get('excludedFromSlate'):
            continue
        for entry in g.get('marketLedger', []):
            candidates.append({
                'game_label': game_label,
                'entry': entry,
                'pre_tt': _snapshot(entry),
            })

    tt_downgrades = rg.apply_tt_safety(slate, now_ts=now_ts)

    # Index TT downgrade reasons by candidate id for pass/fail reconstruction.
    tt_reasons_by_candidate = {}
    for d in tt_downgrades:
        # d['game']/d['market'] identify the game+market, not the exact
        # ticker (risk_gate.py's own downgrade events don't carry ticker) --
        # safe here because TT_MARKETS entries are 1-per-market-per-game in
        # every fixture and in production (build_market_ledger.py writes
        # exactly one row per REQUIRED_MARKETS entry per game).
        tt_reasons_by_candidate[(d['game'], d['market'])] = d['reason']

    for c in candidates:
        entry = c['entry']
        c['post_tt'] = _snapshot(entry)
        key = (c['game_label'], entry.get('market'))
        reasons = tt_reasons_by_candidate.get(key)
        if entry.get('market') in ('TT_Away_Over', 'TT_Home_Over') and reasons is not None:
            pass_fail = {}
            reason_prefixes = {'TT_EVIDENCE': 'TT_MODEL_INPUTS_INCOMPLETE', 'TT_EDGE': 'TT_EDGE_BELOW'}
            fired = {rule: any(r.startswith(prefix) for r in reasons)
                     for rule, prefix in reason_prefixes.items()}
            for rule in TT_RULE_NAMES:
                pass_fail[rule] = not fired[rule]
            first_terminal = next((rule for rule in TT_RULE_NAMES if fired[rule]), None)
            c['rules_evaluated'] = list(TT_RULE_NAMES)
            c['pass_fail'] = pass_fail
            c['first_terminal_reason'] = first_terminal
        elif entry.get('market') in ('TT_Away_Over', 'TT_Home_Over'):
            c['rules_evaluated'] = list(TT_RULE_NAMES)
            c['pass_fail'] = {rule: True for rule in TT_RULE_NAMES}
            c['first_terminal_reason'] = None
        else:
            c['rules_evaluated'] = []
            c['pass_fail'] = {}
            c['first_terminal_reason'] = None

    decision, report = rg.apply_portfolio_rules(slate, now_ts=now_ts)

    family_exposure = []
    for fam in ('TT', 'ML_F5', 'OTHER'):
        fam_report = report['by_family'].get(fam)
        if fam_report is None:
            continue
        bets_after = 0
        stake_after = 0.0
        fam_markets = {
            'TT': {'TT_Away_Over', 'TT_Home_Over'},
            'ML_F5': {'ML_Away', 'ML_Home', 'F5_ML_Away', 'F5_ML_Home'},
        }.get(fam)
        for c in candidates:
            entry = c['entry']
            mkt = entry.get('market', '')
            is_fam = (mkt in fam_markets) if fam_markets is not None else (
                mkt not in {'TT_Away_Over', 'TT_Home_Over', 'ML_Away', 'ML_Home',
                            'F5_ML_Away', 'F5_ML_Home'}
            )
            if not is_fam:
                continue
            tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
            if entry.get('status') == 'Accepted' and tier in ('HIGH', 'MEDIUM'):
                bets_after += 1
                stake_after += float(entry.get('betSize') or 0)
        family_exposure.append({
            'family': fam,
            'bets_before': fam_report['bets'],
            'stake_before': fam_report['stake'],
            'bets_after': bets_after,
            'stake_after': stake_after,
        })

    for c in candidates:
        entry = c['entry']
        tier = (entry.get('confidenceTier') or entry.get('confidence') or '').upper()
        c['final_classification'] = entry.get('confidenceTier')
        c['execution_included'] = (entry.get('status') == 'Accepted' and tier in ('HIGH', 'MEDIUM'))
        c['candidate'] = _candidate_id(c['game_label'], entry)
        c['stake_before'] = c['pre_tt']['betSize']
        c['stake_after'] = c['post_tt']['betSize']
        c['tier_before'] = c['pre_tt']['confidenceTier']
        c['tier_after'] = c['post_tt']['confidenceTier']
        del c['entry']  # do not leak a live reference out of the trace

    trace = {'candidates': candidates, 'family_exposure': family_exposure}
    return tt_downgrades, decision, report, trace
