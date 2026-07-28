#!/usr/bin/env python3
"""
tests/test_risk_gate_immutable.py
====================================
Golden-equivalence regression suite for scripts/risk_gate.py's Phase 7
pure-transform conversion (see docs/IMMUTABLE_PIPELINE.md).

Written and run against the ORIGINAL implementation FIRST to establish a
golden baseline, then re-run UNCHANGED after the refactor to prove
identical production behavior. Complements (does not replace)
tests/test_fire_fixes.py's existing TestTTSafetyGate (6 tests) and
TestPortfolioGate (5 tests), which already cover this file's two core
functions and continue to be re-run unchanged throughout this phase.

PRE-REFACTOR BEHAVIOR MAP (Phase 7 Part 2)
---------------------------------------------
Invocation: exactly one workflow caller —
.github/workflows/fetch-slate.yml:391-395, `python3 scripts/risk_gate.py`,
no CLI args. `if: steps.publish_slate.outcome == 'success'`,
`continue-on-error: true`. Runs immediately after the "Write meta and
commit authoritative slate" step (which writes data/meta.json with
{fetchedAt, date, status, oddsSource} and commits data/slate.json +
data/meta.json to git) and immediately before write_pending_bets.py,
which is gated on `steps.risk_gate.outcome == 'success'` — if risk_gate.py
fails/crashes, write_pending_bets.py is SKIPPED entirely (confirmed via
the workflow's own dependency-graph comment, not assumed).

File reads: data/slate.json (SLATE_PATH, required — hard-fails if
missing); data/meta.json (META_PATH, optional — read-modify-write,
tolerates missing file OR malformed JSON via a bare `except: pass`, in
which case `meta` starts as `{}`).

File writes: data/slate.json (unconditional, every run, in place —
TT downgrades + portfolio PAPER_ONLY downgrades applied directly onto
`slate['games'][*]['marketLedger'][*]` entries); data/meta.json
(unconditional, every run — adds/overwrites ONLY the top-level
`meta['risk_gate']` key; any other pre-existing top-level key, e.g. the
`fetchedAt`/`date`/`status`/`oddsSource` written by the workflow's
publish_slate step moments earlier, survives untouched since risk_gate.py
never reads or clears them).

Never reads or writes: data/bets.json, data/authoritative.json,
data/slates/<date>/authoritative.json, BET_LOG.md, config/rules.json,
RULES.md, projections.json, recommendations.json — confirmed by grep,
not assumed (see docs update for the full authoritative-ownership map).

Imports: stdlib (json, os, sys, datetime/timezone) plus
`lib.postponed_guard.check_game_status` — the SAME live/final/postponed
gate write_pending_bets.py and validate_bet_logging.py use, explicitly
centralized (per the module's own comment) so all three scripts agree on
which games can produce real-money output.

Environment variables: NONE read anywhere in this file.

CLI arguments: NONE accepted (sys.argv is never referenced) — the
workflow always invokes with zero arguments.

Current-time dependency: `now_ts = datetime.now(tz=timezone.utc).isoformat()`
computed ONCE in main(), then threaded explicitly into both
`apply_tt_safety(slate, now_ts=now_ts)` and
`apply_portfolio_rules(slate, now_ts=now_ts)` as their `current_utc` input
to `check_game_status()` — both already accept an optional injected
timestamp for testability (production omits it, defaulting to a live
`datetime.now()` read inside `check_first_pitch_passed()`). `now_ts` is
ALSO stored verbatim as `meta['risk_gate']['runAt']`.

NO BANKROLL CONCEPT EXISTS IN THIS FILE (real finding, contradicts an
assumption in this phase's mission text): there is no bankroll field,
no bankroll source, no percentage-of-bankroll calculation anywhere in
risk_gate.py. Every stake threshold is a FIXED unit ("u") constant
(TT_MAX_STAKE=20.0u, DAILY_RISK_CAP=40.0u) or a percentage of the
SLATE'S OWN total already-accepted real-money stake for this run
(TT_MAX_STAKE_PCT=0.40, ML_F5_MIN_STAKE_PCT=0.50) — never of any
external bankroll figure. `betSize` values themselves are read verbatim
from marketLedger entries (owned and computed by
`build_market_ledger.py`'s `bet_size()` — see its `MARKET_MULTIPLIERS`
table); risk_gate.py never computes a NEW stake, it only WRITES the
fixed constant `1.0` when downgrading an entry to PAPER. Consequently,
Part 9's bankroll-specific scenarios (missing/zero/negative/malformed/
stale bankroll) do not apply to this file and are not tested here as
such — the applicable equivalent (stake-value edge cases: 0.0, negative,
malformed/non-numeric, at/around each fixed cap) is tested instead.

NO RULE 71 OR RULE 81 LOGIC EXISTS IN THIS FILE (real finding): grepped
the whole file for "Rule 71"/"Rule71"/"Rule 81"/"Rule81" — zero matches.
Both rules are implemented entirely in scripts/build_market_ledger.py
(the Pinnacle-gap check), scripts/bet_eligibility.py, and
scripts/validate_slate_final.py — none of which this phase touches.
Part 8's Rule 71/81 boundary-test requirement is satisfied by this
documented absence, not by inventing tests for logic that does not exist
here.

NO DUPLICATE-DETECTION OR CORRELATION LOGIC EXISTS IN THIS FILE (real
finding): `apply_portfolio_rules()`'s only "state" is a per-family
(TT / ML_F5 / OTHER, keyed purely off `market` string membership in
TT_MARKETS/ML_F5_MARKETS) stake-and-bet-count tally, used for exactly
two concentration checks (TT max-bets/max-stake/max-pct;  ML+F5
min-pct) plus one flat daily cap. There is no same-team-across-games
check, no opposing-sides check, no F3/F5/full-game market-overlap
check, no ML/spread overlap check, no pitcher-prop overlap check, and no
NRFI/YRFI conflict check anywhere in this file — "duplicate market"
entries (two marketLedger rows with the identical `market` string in
the same game, which cannot normally occur since build_market_ledger.py
writes exactly one row per REQUIRED_MARKETS entry per game) are simply
tallied twice into the same family bucket, with no special-casing.
Part 15's scenario list is satisfied by this documented absence for the
correlation-specific items; the family-tally behavior itself (which DOES
exist) is fully tested below and in test_fire_fixes.py.

Rule evaluation order, exact (main()):
  1. slate.json existence check → hard fail (print + exit 1) if missing.
  2. slate.json load (json.load — uncaught JSONDecodeError propagates,
     no try/except here, matching every other script's convention).
  3. `now_ts` computed once.
  4. Pass 1 — `apply_tt_safety(slate, now_ts)`: for each game (in list
     order) not excludedFromSlate and not check_game_status-skipped, for
     each marketLedger entry (in list order) whose market is in
     TT_MARKETS: `enrich_tt_inputs()` ALWAYS runs first (unconditional on
     status/tier) adding/overwriting the `ttInputs` block; THEN, only if
     status=='Accepted' and tier in {HIGH, MEDIUM}, the two downgrade
     checks run in this exact order: (a) evidence completeness
     (`check_tt_evidence`), (b) edge threshold (`TT_MIN_EDGE_PCT=2.5`);
     if EITHER reason fires, entry becomes status='Accepted' (kept, not
     changed), confidence/confidenceTier='PAPER', betSize=1.0,
     realMoneyBlocked=True, blockReason=joined reasons (evidence reason
     first if both fire), gatesFired extended with both reasons in the
     same order. requiredRunsToWin is set (line+1) unconditionally
     whenever `line` is not None, regardless of whether the entry gets
     downgraded.
  5. Pass 2 — `apply_portfolio_rules(slate, now_ts)`: re-scans ALL games
     or entries in list order (a SEPARATE scan from pass 1, over the
     ALREADY-TT-DOWNGRADED slate) collecting every Accepted+HIGH/MEDIUM
     entry (including any entry pass 1 did NOT downgrade — a TT entry
     downgraded in pass 1 is now PAPER-tier and correctly excluded from
     this collection). Tallies by family. Rule order: (a) daily risk cap
     check (warning only, does not itself downgrade anything), (b) TT
     max-bets check (sorts qualifying TT entries by edge descending,
     downgrades every entry BEYOND the top TT_MAX_BETS=4 to PAPER,
     recomputes tt_stake_post AFTER this downgrade), (c) TT stake-cap
     check (warning only, using post-downgrade tt_stake_post), (d) TT
     dominance-pct check (warning only, using post-downgrade
     tt_stake_post / total_stake — NOTE: total_stake itself is NOT
     recomputed after the TT-max-bets downgrade, so a downgraded entry's
     stake still counts in the total_stake denominator even though it no
     longer counts in tt_stake_post's numerator — a real, precise
     asymmetry to preserve exactly, not "fix"), (e) ML+F5 underfill
     check (warning only, mlf5_pct also using the NOT-recomputed
     total_stake). Final decision: 'PAPER_ONLY' if the slate is 100% TT
     with zero ML/F5 bets (`decision_reason='ALL_TT_NO_ML_F5'`), else
     'PAPER_ONLY' if any warning starts with 'DAILY_RISK_CAP' or
     'TT_DOMINANCE' (`hard_block`, decision_reason = only the CAP/DOMINANCE
     warnings joined), else 'GO'. (There is no 'NO_GO' value ever
     actually returned despite the module docstring listing it as a
     possibility — confirmed by reading every return path: only 'GO' and
     'PAPER_ONLY' string literals appear.)
  6. If decision == 'PAPER_ONLY': a THIRD scan (main()'s own, not inside
     apply_portfolio_rules) forces every remaining Accepted+HIGH/MEDIUM
     entry (across ALL non-excluded games — this scan does NOT use
     check_game_status at all, unlike passes 1 and 2) to PAPER, with
     blockReason='RISK_GATE_PAPER_ONLY: <decision_reason>'.
  7. data/slate.json rewritten (plain `json.dump(slate, f, indent=2)`,
     NOT atomic) — unconditionally, every run, regardless of decision.
  8. data/meta.json read-modify-write (plain, NOT atomic) — unconditionally,
     every run; `meta['risk_gate']` set to `{runAt, decision, **report}`,
     every other pre-existing top-level key preserved verbatim.
  9. `return 0` always — main()'s own return value is always 0 on any
     successful (non-hard-fail) completion; the only nonzero exit is the
     missing-slate.json hard fail (`sys.exit(1)`, hit before any of the
     above runs).

Log lines: informational-only prints to stdout throughout (no
`file=sys.stderr` anywhere except none — confirmed: risk_gate.py never
writes to stderr at all, even on its one hard-fail path, unlike every
other converted script in this repo, which is a real, pre-existing
asymmetry preserved here, not fixed).

Tolerated malformed inputs: a missing/malformed data/meta.json (bare
`except Exception: pass`, `meta` starts `{}`); a marketLedger entry
missing any field this file reads (every read uses `.get(...)` with a
default, never direct `[...]` key access — unlike build_market_ledger.py's
projection_context reads); a non-numeric `edge`/`betSize` would raise
inside the f-string formatting or arithmetic — not specially tolerated
(see the golden tests below for the exact exception this produces).

Fatal malformed inputs: missing data/slate.json (hard fail, exit 1);
malformed (invalid JSON) data/slate.json (uncaught, propagates as
Python's default traceback + nonzero exit, matching fetch_lineups.py's
own no-top-level-guard convention).

Rerun/idempotency: NOT idempotent in general — re-running risk_gate.py
against its OWN previous output re-applies every rule from scratch
against whatever state the marketLedger entries are ALREADY in. Since a
downgraded entry's `confidenceTier` is now 'PAPER' (outside
REAL_MONEY_TIERS), it is excluded from re-consideration by both passes
on a second run — so a second run against an unchanged slate.json
produces the SAME final state (a fixed point), but the SECOND run's
`downgrades`/`concentration_warnings`/`decision_reason` lists reflect
only what fires on ITS OWN pass, which may differ from the first run's
lists (e.g. a first-run TT_MAX_BETS downgrade event won't recur on a
second run, since those entries are already PAPER). `meta.json`'s
`risk_gate` key is fully OVERWRITTEN (not merged/appended) on every run
— `runAt` always reflects the LATEST run's timestamp. This is
documented, not "fixed" into true idempotency.
"""

import json
import os
import sys
import shutil
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)


def make_entry(market='ML_Away', tier='HIGH', status='Accepted',
                edge=4.5, stake=5.0, ticker='KXMLBGAME-26JUN161845KCWSH-KC',
                line=None, kalshi_price=-120, model_prob=70.0, exec_price=54.5,
                lineup_posted=True, away_proj=4.8, home_proj=4.5):
    return {
        'market': market,
        'confidenceTier': tier,
        'confidence': tier,
        'status': status,
        'edge': edge,
        'calibratedEdgeVsExecutable': edge,
        'betSize': stake,
        'ticker': ticker,
        'marketTicker': ticker,
        'line': line,
        'kalshiPrice': kalshi_price,
        'kalshiImplied': 54.5,
        'kalshiVF': 54.5,
        'executablePriceUsed': exec_price,
        'modelProb': model_prob,
        'lineupPosted': lineup_posted,
        'lineupDataQuality': 'full',
        'awayProjRuns': away_proj,
        'homeProjRuns': home_proj,
    }


def make_tt_entry(side='Away', **kwargs):
    kwargs.setdefault('market', f'TT_{side}_Over')
    kwargs.setdefault('line', 4)
    kwargs.setdefault('ticker', f'KXMLBTEAMTOTAL-26JUN161845KCWSH-KC{side}')
    return make_entry(**kwargs)


def make_game(away='KC', home='WSH', entries=None, excluded=False,
              status='Scheduled', start_time='2026-06-16T22:46:00Z'):
    g = {
        'away': {'abbr': away},
        'home': {'abbr': home},
        'startTime': start_time,
        'status': status,
        'marketLedger': entries or [],
    }
    if excluded:
        g['excludedFromSlate'] = True
        g['exclusionReason'] = 'test fixture'
    return g


def make_slate(games, date='2026-06-16'):
    return {'date': date, 'games': games}


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


NOW = '2026-06-16T20:00:00Z'  # before the fixtures' 22:46 start time


# ══════════════════════════════════════════════════════════════════════════════
# apply_tt_safety() golden equivalence
# ══════════════════════════════════════════════════════════════════════════════

class TestTTSafetyGoldenEquivalence:

    def test_no_recommendations_produces_no_downgrades(self, rg):
        slate = make_slate([make_game(entries=[])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []

    def test_one_eligible_recommendation_ttinputs_added(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0)
        slate = make_slate([make_game(entries=[entry])])
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert 'ttInputs' in entry

    def test_one_ineligible_recommendation_rejected_status_skipped(self, rg):
        """A Rejected-status entry never enters the tier/status check at all."""
        entry = make_tt_entry(status='Rejected', tier='HIGH', edge=4.0)
        slate = make_slate([make_game(entries=[entry])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []
        assert entry['status'] == 'Rejected'
        assert 'ttInputs' in entry  # still enriched unconditionally

    def test_multiple_eligible_recommendations_all_enriched(self, rg):
        entries = [make_tt_entry(edge=4.0, ticker=f'T{i}') for i in range(3)]
        slate = make_slate([make_game(entries=entries)])
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert all('ttInputs' in e for e in entries)

    def test_mixed_eligible_ineligible_only_eligible_evaluated(self, rg):
        good = make_tt_entry(edge=4.0, tier='HIGH')
        low_edge = make_tt_entry(edge=1.0, tier='HIGH', ticker='T2')
        slate = make_slate([make_game(entries=[good, low_edge])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert good['confidenceTier'] == 'HIGH'
        assert low_edge['confidenceTier'] == 'PAPER'
        assert len(downgrades) == 1

    def test_accepted_row_high_tier_good_edge_stays_high(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=5.0)
        slate = make_slate([make_game(entries=[entry])])
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert entry['confidenceTier'] == 'HIGH'
        assert entry.get('realMoneyBlocked') is None

    def test_missing_row_status_not_downgraded_by_tt_pass(self, rg):
        entry = make_tt_entry(status='Missing Data', tier=None, edge=None)
        entry['confidenceTier'] = None
        entry['confidence'] = None
        slate = make_slate([make_game(entries=[entry])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []

    def test_failed_row_status_not_downgraded_by_tt_pass(self, rg):
        entry = make_tt_entry(status='Evaluation Failed')
        slate = make_slate([make_game(entries=[entry])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []

    def test_required_runs_to_win_not_set_for_non_evaluated_entries(self, rg):
        """
        requiredRunsToWin is only ever set for entries that reach the
        evidence/edge checks (status=='Accepted' and tier in
        HIGH/MEDIUM) -- a non-evaluated entry (wrong status, or PAPER
        tier) with a `line` present must NOT gain a requiredRunsToWin
        field, even though ttInputs.requiredRunsToWin is always computed.
        This is a precise legacy conditional (Part 7): the original
        single-pass implementation only reached the requiredRunsToWin
        assignment AFTER the status/tier `continue` gate.
        """
        entry = make_tt_entry(status='Missing Data', tier=None, edge=None)
        entry['confidenceTier'] = None
        entry['confidence'] = None
        slate = make_slate([make_game(entries=[entry])])
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert 'requiredRunsToWin' not in entry
        assert entry['ttInputs']['requiredRunsToWin'] == 5  # still computed inside ttInputs

    def test_required_runs_to_win_not_set_for_paper_tier_entry(self, rg):
        entry = make_tt_entry(status='Accepted', tier='PAPER', edge=4.0)
        slate = make_slate([make_game(entries=[entry])])
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert 'requiredRunsToWin' not in entry

    def test_both_evidence_and_edge_fail_reason_order_evidence_first(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        entry['awayProjRuns'] = None
        slate = make_slate([make_game(entries=[entry])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert len(downgrades[0]['reason']) == 2
        assert downgrades[0]['reason'][0].startswith('TT_MODEL_INPUTS_INCOMPLETE')
        assert downgrades[0]['reason'][1].startswith('TT_EDGE_BELOW')
        assert entry['blockReason'] == '; '.join(downgrades[0]['reason'])

    def test_sentinel_and_null_price_fields_treated_as_missing_evidence(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=4.0, kalshi_price=None)
        entry['kalshiPrice'] = None
        slate = make_slate([make_game(entries=[entry])])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert 'kalshiPrice' in downgrades[0]['reason'][0]

    def test_excluded_game_produces_no_downgrades_and_no_enrichment(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=1.0)  # would otherwise downgrade
        slate = make_slate([make_game(entries=[entry], excluded=True)])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []
        assert 'ttInputs' not in entry

    @pytest.mark.parametrize("status", ["Postponed", "Cancelled", "Suspended"])
    def test_postponed_cancelled_suspended_games_skipped_entirely(self, rg, status):
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        slate = make_slate([make_game(entries=[entry], status=status)])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []
        assert 'ttInputs' not in entry

    @pytest.mark.parametrize("status", ["In Progress", "Final"])
    def test_live_and_final_games_skipped_entirely(self, rg, status):
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        slate = make_slate([make_game(entries=[entry], status=status)])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert downgrades == []

    def test_doubleheader_same_teams_distinct_entries_independent(self, rg):
        e1 = make_tt_entry(edge=4.0, ticker='G1')
        e2 = make_tt_entry(edge=1.0, ticker='G2')
        g1 = make_game('NYY', 'BOS', [e1])
        g2 = make_game('NYY', 'BOS', [e2])
        slate = make_slate([g1, g2])
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert e1['confidenceTier'] == 'HIGH'
        assert e2['confidenceTier'] == 'PAPER'

    def test_repeated_run_on_already_downgraded_entry_is_stable(self, rg):
        entry = make_tt_entry(tier='HIGH', edge=1.0)
        slate = make_slate([make_game(entries=[entry])])
        rg.apply_tt_safety(slate, now_ts=NOW)
        first_state = dict(entry)
        rg.apply_tt_safety(slate, now_ts=NOW)
        assert entry['confidenceTier'] == first_state['confidenceTier'] == 'PAPER'

    def test_mixed_validity_multi_game_slate(self, rg):
        good = make_tt_entry(edge=4.0, tier='HIGH', ticker='G1')
        bad = make_tt_entry(edge=1.0, tier='HIGH', ticker='G2')
        excluded_entry = make_tt_entry(edge=1.0, tier='HIGH', ticker='G3')
        slate = make_slate([
            make_game('AAA', 'BBB', [good]),
            make_game('CCC', 'DDD', [bad]),
            make_game('EEE', 'FFF', [excluded_entry], excluded=True),
        ])
        downgrades = rg.apply_tt_safety(slate, now_ts=NOW)
        assert good['confidenceTier'] == 'HIGH'
        assert bad['confidenceTier'] == 'PAPER'
        assert 'ttInputs' not in excluded_entry
        assert len(downgrades) == 1


# ══════════════════════════════════════════════════════════════════════════════
# apply_portfolio_rules() golden equivalence (Phase 7 Part 4, portfolio half)
# ══════════════════════════════════════════════════════════════════════════════

def _game_with_ml(game_id, stake=5.0, edge=4.0, market='ML_Away', tier='HIGH'):
    away, home = game_id
    entry = make_entry(market=market, tier=tier, stake=stake, edge=edge,
                        ticker=f'{away}{home}-{market}')
    return make_game(away, home, [entry]), entry


def _game_with_tt(game_id, stake=5.0, edge=4.0, side='Away'):
    away, home = game_id
    entry = make_tt_entry(side=side, stake=stake, edge=edge, tier='HIGH',
                           ticker=f'{away}{home}-TT{side}')
    return make_game(away, home, [entry]), entry


class TestPortfolioGoldenEquivalence:

    def test_daily_risk_cap_exactly_at_threshold_no_warning(self, rg):
        # 8 ML_F5 bets @ 5.0u = 40.0u exactly; check is `>`, not `>=`.
        pairs = [_game_with_ml((f'A{i}', f'B{i}'), stake=5.0, market='ML_Away') for i in range(8)]
        slate = make_slate([g for g, _ in pairs])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_real_stake'] == 40.0
        assert not any(w.startswith('DAILY_RISK_CAP') for w in report['concentration_warnings'])

    def test_daily_risk_cap_just_above_threshold_warns(self, rg):
        pairs = [_game_with_ml((f'A{i}', f'B{i}'), stake=5.0, market='ML_Away') for i in range(8)]
        extra_game, extra_entry = _game_with_ml(('X', 'Y'), stake=0.01, market='ML_Away')
        slate = make_slate([g for g, _ in pairs] + [extra_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_real_stake'] > 40.0
        assert any(w.startswith('DAILY_RISK_CAP') for w in report['concentration_warnings'])

    def test_tt_max_bets_exactly_4_no_downgrade(self, rg):
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), edge=4.0 + i) for i in range(4)]
        slate = make_slate([g for g, _ in pairs])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['tt_bets'] == 4
        assert not any('TT_CONCENTRATION' in w for w in report['concentration_warnings'])
        assert all(e['confidenceTier'] == 'HIGH' for _, e in pairs)

    def test_tt_max_bets_5_downgrades_lowest_edge(self, rg):
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), edge=float(5 - i)) for i in range(5)]
        # edges: 5.0, 4.0, 3.0, 2.0, 1.0 -- lowest (1.0, index 4) must be downgraded.
        slate = make_slate([g for g, _ in pairs])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert any('TT_CONCENTRATION' in w for w in report['concentration_warnings'])
        kept = [e for _, e in pairs if e['confidenceTier'] == 'HIGH']
        downgraded = [e for _, e in pairs if e['confidenceTier'] == 'PAPER']
        assert len(kept) == 4
        assert len(downgraded) == 1
        assert downgraded[0]['edge'] == 1.0
        assert downgraded[0]['blockReason'] == 'TT_MAX_BETS_EXCEEDED: capped at 4'
        assert downgraded[0]['betSize'] == 1.0

    def test_tt_max_bets_downgrade_does_not_recompute_total_stake(self, rg):
        """
        Documented, load-bearing asymmetry (Phase 7 Part 7): after the
        TT-max-bets downgrade, tt_stake_post excludes the downgraded entry's
        stake, but total_stake (the denominator for TT_DOMINANCE and
        ML_F5_UNDERFILL) is NOT recomputed -- the downgraded entry's stake
        still counts in the total. This must be preserved exactly, not
        "fixed" during the Phase 7 refactor.
        """
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=5.0, edge=float(5 - i)) for i in range(5)]
        slate = make_slate([g for g, _ in pairs])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        # total_stake includes all 5 entries' stake (5 * 5.0 = 25.0), even
        # though only 4 remain real-money after the cap downgrade.
        assert report['total_real_stake'] == 25.0
        assert report['tt_stake'] == 25.0  # tt_stake in report is PRE-downgrade tally

    def test_tt_stake_cap_exactly_20_no_warning(self, rg):
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=5.0, edge=4.0) for i in range(4)]
        slate = make_slate([g for g, _ in pairs])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['tt_stake'] == 20.0
        assert not any(w.startswith('TT_STAKE_CAP') for w in report['concentration_warnings'])

    def test_tt_stake_cap_just_above_20_warns(self, rg):
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=5.01, edge=4.0) for i in range(4)]
        slate = make_slate([g for g, _ in pairs])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert any(w.startswith('TT_STAKE_CAP') for w in report['concentration_warnings'])

    def test_tt_dominance_pct_exactly_40_no_warning(self, rg):
        # TT = 4.0u, ML_F5 = 6.0u -> total 10.0u, tt_pct = 0.40 exactly.
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        ml_game, ml_entry = _game_with_ml(('C', 'D'), stake=6.0)
        slate = make_slate([tt_game, ml_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert not any(w.startswith('TT_DOMINANCE') for w in report['concentration_warnings'])

    def test_tt_dominance_pct_just_above_40_warns(self, rg):
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.01, edge=4.0)
        ml_game, ml_entry = _game_with_ml(('C', 'D'), stake=6.0)
        slate = make_slate([tt_game, ml_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert any(w.startswith('TT_DOMINANCE') for w in report['concentration_warnings'])
        assert decision == 'PAPER_ONLY'

    def test_ml_f5_underfill_exactly_50pct_no_warning(self, rg):
        ml1, e1 = _game_with_ml(('A', 'B'), stake=5.0, market='ML_Away')
        ml2, e2 = _game_with_ml(('C', 'D'), stake=5.0, market='ML_Home')
        other, e3 = _game_with_ml(('E', 'F'), stake=10.0, market='Spread_Away')
        slate = make_slate([ml1, ml2, other])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['ml_f5_bets'] == 2
        assert not any(w.startswith('ML_F5_UNDERFILL') for w in report['concentration_warnings'])

    def test_ml_f5_underfill_just_below_50pct_warns(self, rg):
        ml1, e1 = _game_with_ml(('A', 'B'), stake=4.99, market='ML_Away')
        ml2, e2 = _game_with_ml(('C', 'D'), stake=0.0, market='ML_Home')
        other, e3 = _game_with_ml(('E', 'F'), stake=10.0, market='Spread_Away')
        slate = make_slate([ml1, ml2, other])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['ml_f5_bets'] == 2
        assert any(w.startswith('ML_F5_UNDERFILL') for w in report['concentration_warnings'])

    def test_ml_f5_underfill_requires_at_least_2_bets(self, rg):
        # Only 1 ML_F5 bet -- underfill check does not apply regardless of pct.
        ml1, e1 = _game_with_ml(('A', 'B'), stake=0.01, market='ML_Away')
        other, e2 = _game_with_ml(('E', 'F'), stake=10.0, market='Spread_Away')
        slate = make_slate([ml1, other])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['ml_f5_bets'] == 1
        assert not any(w.startswith('ML_F5_UNDERFILL') for w in report['concentration_warnings'])

    def test_all_tt_no_ml_f5_forces_paper_only(self, rg):
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        slate = make_slate([tt_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert decision == 'PAPER_ONLY'
        assert report['decision_reason'] == 'ALL_TT_NO_ML_F5'

    def test_hard_block_decision_reason_only_includes_cap_and_dominance(self, rg):
        # Force both DAILY_RISK_CAP and TT_DOMINANCE simultaneously; also
        # trigger ML_F5_UNDERFILL, which must NOT appear in decision_reason.
        pairs = [_game_with_tt((f'A{i}', f'B{i}'), stake=10.0, edge=4.0) for i in range(4)]
        ml1, e1 = _game_with_ml(('X', 'Y'), stake=0.01, market='ML_Away')
        ml2, e2 = _game_with_ml(('Z', 'W'), stake=0.01, market='ML_Home')
        slate = make_slate([g for g, _ in pairs] + [ml1, ml2])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert decision == 'PAPER_ONLY'
        assert 'DAILY_RISK_CAP' in report['decision_reason']
        assert 'TT_DOMINANCE' in report['decision_reason']
        assert 'ML_F5_UNDERFILL' not in report['decision_reason']

    def test_composition_passes_gives_go(self, rg):
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        ml1, e1 = _game_with_ml(('C', 'D'), stake=3.0, market='ML_Away')
        ml2, e2 = _game_with_ml(('E', 'F'), stake=3.0, market='ML_Home')
        slate = make_slate([tt_game, ml1, ml2])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert decision == 'GO'
        assert report['decision_reason'] == 'Composition checks passed'

    def test_stake_zero_counted_as_bet_with_zero_stake(self, rg):
        ml_game, entry = _game_with_ml(('A', 'B'), stake=0.0, market='ML_Away')
        slate = make_slate([ml_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['ml_f5_bets'] == 1
        assert report['ml_f5_stake'] == 0.0

    def test_negative_stake_included_verbatim_not_clamped(self, rg):
        """
        risk_gate.py performs no validation/clamping on betSize -- a negative
        stake (which should never occur upstream, but is not rejected here)
        flows straight into the family tally and total_stake sum unchanged.
        """
        ml_game, entry = _game_with_ml(('A', 'B'), stake=-5.0, market='ML_Away')
        slate = make_slate([ml_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['ml_f5_stake'] == -5.0
        assert report['total_real_stake'] == -5.0

    def test_none_stake_treated_as_zero(self, rg):
        ml_game, entry = _game_with_ml(('A', 'B'), stake=5.0, market='ML_Away')
        entry['betSize'] = None
        slate = make_slate([ml_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['ml_f5_stake'] == 0.0
        assert report['ml_f5_bets'] == 1

    def test_excluded_game_not_counted_in_portfolio(self, rg):
        ml_game, entry = _game_with_ml(('A', 'B'), stake=10.0, market='ML_Away')
        ml_game['excludedFromSlate'] = True
        slate = make_slate([ml_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0
        assert report['total_real_stake'] == 0.0

    @pytest.mark.parametrize("status", ["Postponed", "Cancelled", "Suspended", "In Progress", "Final"])
    def test_live_final_postponed_games_not_counted_in_portfolio(self, rg, status):
        ml_game, entry = _game_with_ml(('A', 'B'), stake=10.0, market='ML_Away')
        ml_game['status'] = status
        slate = make_slate([ml_game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['total_bets'] == 0

    def test_other_family_bucket_used_for_non_tt_non_ml_f5_markets(self, rg):
        game, entry = _game_with_ml(('A', 'B'), stake=7.0, market='Spread_Away')
        slate = make_slate([game])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert report['by_family']['OTHER']['stake'] == 7.0
        assert 'TT' not in report['by_family']
        assert 'ML_F5' not in report['by_family']

    def test_no_recommendations_produces_go_with_zero_stake(self, rg):
        slate = make_slate([make_game(entries=[])])
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        assert decision == 'GO'
        assert report['total_real_stake'] == 0.0
        assert report['total_bets'] == 0


# ══════════════════════════════════════════════════════════════════════════════
# main() golden equivalence -- integration-level, isolated filesystem only
# ══════════════════════════════════════════════════════════════════════════════

class TestMainIntegrationGoldenEquivalence:
    """
    Every test here points rg.SLATE_PATH/rg.META_PATH at a tmp_path fixture
    directory, NEVER at the real repository's data/ directory -- risk_gate.py
    resolves these paths via `os.path.dirname(__file__)` at import time, so
    reassigning the already-imported module's globals (rather than relying on
    cwd= or environment variables) is the only safe way to sandbox main().
    A dedicated leak-guard test hashes the real repo's data/slate.json and
    data/meta.json before and after this whole class runs to prove none of
    these tests ever touch real production data.

    Since Phase 7 Part 10-11 added a best-effort data/pipeline/<date>/
    execution.json write inside main() (via lib/pipeline_artifacts.py's
    write_stage_artifact()), pipeline_artifacts.PIPELINE_ROOT is ALSO
    reassigned to a tmp_path subdirectory here -- PIPELINE_ROOT is a
    relative ("data/pipeline") module-level global resolved against cwd
    at call time, not at import time, so a first run of this class
    genuinely wrote a stray data/pipeline/<date>/execution.json into the
    real repository (caught immediately via `git status --short data/`,
    cleaned up with `rm -rf data/pipeline` since it was an untracked
    directory this session created). This autouse fixture is the fix.
    """

    @pytest.fixture(autouse=True)
    def _sandbox_pipeline_root(self, tmp_path):
        import pipeline_artifacts as pa
        original_root = pa.PIPELINE_ROOT
        pa.PIPELINE_ROOT = str(tmp_path / 'pipeline')
        yield
        pa.PIPELINE_ROOT = original_root

    def _wire_paths(self, rg, tmp_path):
        slate_path = str(tmp_path / 'slate.json')
        meta_path = str(tmp_path / 'meta.json')
        rg.SLATE_PATH = slate_path
        rg.META_PATH = meta_path
        return slate_path, meta_path

    def test_missing_slate_json_hard_fails_exit_1(self, rg, tmp_path, capsys):
        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            rg.main()
        assert exc_info.value.code == 1
        assert not os.path.exists(meta_path)

    def test_malformed_slate_json_propagates_uncaught(self, rg, tmp_path):
        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        with open(slate_path, 'w') as f:
            f.write('{not valid json')
        with pytest.raises(json.JSONDecodeError):
            rg.main()

    def test_missing_meta_json_tolerated_new_file_created(self, rg, tmp_path):
        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game(entries=[])]), f)
        assert not os.path.exists(meta_path)
        result = rg.main()
        assert result == 0
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta['risk_gate']['decision'] == 'GO'

    def test_malformed_meta_json_tolerated_via_bare_except(self, rg, tmp_path):
        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game(entries=[])]), f)
        with open(meta_path, 'w') as f:
            f.write('{not valid json')
        result = rg.main()
        assert result == 0
        with open(meta_path) as f:
            meta = json.load(f)
        # Malformed prior content is discarded (meta started as {}), not merged.
        assert meta['risk_gate']['decision'] == 'GO'

    def test_meta_json_preexisting_keys_preserved_verbatim(self, rg, tmp_path):
        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game(entries=[])]), f)
        with open(meta_path, 'w') as f:
            json.dump({'fetchedAt': '2026-06-16T18:00:00Z', 'date': '2026-06-16',
                       'status': 'ok', 'oddsSource': 'kalshi'}, f)
        rg.main()
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta['fetchedAt'] == '2026-06-16T18:00:00Z'
        assert meta['date'] == '2026-06-16'
        assert meta['status'] == 'ok'
        assert meta['oddsSource'] == 'kalshi'
        assert 'risk_gate' in meta

    def test_paper_only_third_pass_forces_all_remaining_real_money_to_paper(self, rg, tmp_path):
        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        tt_entry['ticker'] = 'ABTT'
        # All-TT, no ML/F5 -> forces PAPER_ONLY, which the 3rd pass applies.
        with open(slate_path, 'w') as f:
            json.dump(make_slate([tt_game]), f)
        rg.main()
        with open(slate_path) as f:
            written_slate = json.load(f)
        written_entry = written_slate['games'][0]['marketLedger'][0]
        assert written_entry['confidenceTier'] == 'PAPER'
        assert written_entry['blockReason'].startswith('RISK_GATE_PAPER_ONLY:')
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta['risk_gate']['decision'] == 'PAPER_ONLY'

    def test_go_decision_leaves_real_money_entries_untouched_by_third_pass(self, rg, tmp_path):
        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        ml1, e1 = _game_with_ml(('C', 'D'), stake=3.0, market='ML_Away')
        ml2, e2 = _game_with_ml(('E', 'F'), stake=3.0, market='ML_Home')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([tt_game, ml1, ml2]), f)
        rg.main()
        with open(slate_path) as f:
            written_slate = json.load(f)
        for g in written_slate['games']:
            for e in g['marketLedger']:
                assert e['confidenceTier'] == 'HIGH'
                assert 'blockReason' not in e

    def test_rerun_on_own_output_is_a_stable_fixed_point(self, rg, tmp_path):
        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=1.0)  # low edge -> downgrade
        with open(slate_path, 'w') as f:
            json.dump(make_slate([tt_game]), f)
        rg.main()
        with open(slate_path) as f:
            first_run_slate = json.load(f)
        rg.main()
        with open(slate_path) as f:
            second_run_slate = json.load(f)
        first_entry = first_run_slate['games'][0]['marketLedger'][0]
        second_entry = second_run_slate['games'][0]['marketLedger'][0]
        assert first_entry['confidenceTier'] == second_entry['confidenceTier'] == 'PAPER'

    def test_real_repo_data_directory_never_touched(self, rg, tmp_path):
        """
        Leak guard: hash the REAL repository's data/slate.json and
        data/meta.json (if present) before running a main() call wired at
        tmp_path, then again after -- must be byte-identical. This is the
        same class of safety incident caught in the PR #7 review for
        build_market_ledger.py's subprocess test (which overwrote the real
        data/slate.json via a __file__-relative path); risk_gate.py resolves
        SLATE_PATH/META_PATH the same __file__-relative way, so this guard
        is required here too.
        """
        import hashlib
        real_slate = os.path.join(ROOT, 'data', 'slate.json')
        real_meta = os.path.join(ROOT, 'data', 'meta.json')
        real_pipeline_dir = os.path.join(ROOT, 'data', 'pipeline')

        def _hash(path):
            if not os.path.exists(path):
                return None
            with open(path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()

        before_slate, before_meta = _hash(real_slate), _hash(real_meta)
        pipeline_dir_existed_before = os.path.exists(real_pipeline_dir)

        slate_path, meta_path = self._wire_paths(rg, tmp_path)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game(entries=[])]), f)
        rg.main()

        after_slate, after_meta = _hash(real_slate), _hash(real_meta)
        assert before_slate == after_slate
        assert before_meta == after_meta
        # The Phase 7 execution-artifact write must land in the sandboxed
        # PIPELINE_ROOT (via the class's autouse fixture), never create a
        # real data/pipeline/ directory in the repository.
        assert os.path.exists(real_pipeline_dir) == pipeline_dir_existed_before
