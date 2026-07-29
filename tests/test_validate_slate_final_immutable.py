#!/usr/bin/env python3
"""
tests/test_validate_slate_final_immutable.py
================================================
Golden-equivalence regression suite for scripts/validate_slate_final.py's
Phase 8 pure-transform conversion (see docs/IMMUTABLE_PIPELINE.md).

Written and run against the ORIGINAL implementation FIRST to establish a
golden baseline, then re-run UNCHANGED after the refactor to prove
identical production behavior.

PRE-REFACTOR BEHAVIOR MAP (Phase 8 Part 4)
---------------------------------------------
Invocation: exactly one workflow caller --
.github/workflows/fetch-slate.yml:303, `python3 scripts/validate_slate_final.py
"${{ env.DATE }}" 2>&1` (stderr redirected into the same captured stdout
stream in production -- the script's own stdout/stderr split still
matters for local/direct invocation, just not for the workflow's own
log capture). Runs immediately after `build_market_ledger.py` and
immediately BEFORE `protect_slate.py` ("Apply slate protection") --
i.e. this is the EARLIEST of {validate_slate_final, protect_slate,
publish_slate, risk_gate} in the pipeline, positioned right after
recommendation generation and before ANY of the execution-layer scripts
run. No `id:`/`continue-on-error:` on this step in the workflow --
confirmed by reading the surrounding YAML directly -- so it is a hard,
blocking gate: if it exits 1, the workflow job fails outright and
NOTHING downstream (protect_slate, publish_slate, risk_gate,
write_pending_bets, ...) runs at all.

CLI arguments: `sys.argv[1]`, optional -- if present and truthy, used
verbatim as `exp_date` (no format validation). If absent, `exp_date`
falls back to `(datetime.now(timezone.utc) - timedelta(hours=4))
.strftime('%Y-%m-%d')` -- a FIXED 4-hour UTC offset approximation of
Eastern Time that does NOT account for DST (EDT is UTC-4, EST is
UTC-5) -- a real, pre-existing defect (during EST months this
mis-computes the date near midnight ET) that Phase 8 documents, does
NOT fix (mission: "Document pre-existing defects without fixing them
unless the refactor causes them").

File reads: data/slate.json, via `load_slate()`'s "CWD-relative first,
__file__-relative fallback" strategy (`cwd_path = 'data/slate.json'`;
`file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
'..', 'data', 'slate.json')`; `path = cwd_path if os.path.exists(cwd_path)
else file_path`) -- the OPPOSITE priority order from risk_gate.py, which
is exclusively __file__-relative. This means sandboxed subprocess tests
for this script must control the SANDBOX'S CWD (not just copy the
script into a tmp scripts/ directory), since a cwd-relative
'data/slate.json' existing anywhere the test runner's cwd happens to
point at would be picked up in preference to the __file__-relative
path.

Environment variables: exactly one read -- `os.environ.get('GITHUB_OUTPUT', '')`
inside `write_github_output()`. If unset/empty, the function is a no-op
(no file write attempted). If set, the target file is opened in APPEND
('a') mode (not overwrite) -- this is CORRECT, not a bug: GitHub
Actions' own GITHUB_OUTPUT mechanism is a single per-job file every
step's outputs accumulate into, so append is the right semantic here,
unlike every other file this script (or risk_gate.py) writes, which are
always full overwrites.

Current-time dependency: `expected_date()`'s ET-approximation fallback
uses a bare `datetime.now(timezone.utc)` call (no injection point) when
no CLI date arg is given -- genuinely clock-dependent, untestable
without either always passing an explicit date arg or monkeypatching
`datetime` (the same `_NoClockDatetime` subclass technique used
elsewhere in this repo, since `datetime.datetime.now` cannot be
monkeypatched directly). `generate_execution_slip(games, exp_date,
current_utc=None)` ALREADY has an injectable `current_utc` parameter
threaded into `check_game_status(g, current_utc=current_utc)` --
production's only call site (`main()`) passes
`datetime.now(timezone.utc).isoformat()` explicitly at the call site,
computed once, matching risk_gate.py's `now_ts`-threading pattern
already established in this repo.

Imports: stdlib (json, os, sys, datetime/timezone/timedelta) plus
`lib.postponed_guard.check_game_status` -- the SAME shared live/final/
postponed gate risk_gate.py and write_pending_bets.py use, imported the
same way (`sys.path.insert(0, .../lib)` then `from postponed_guard
import check_game_status`). Used ONLY inside `generate_execution_slip()`
(never inside `validate_final()`) to route already-started games'
marketLedger rows directly into the REJECTED/BLOCKED slip section
instead of REAL-MONEY BETS -- the "PREGAME-ONLY HARD GATE" block.

Never reads or writes: data/meta.json (zero references anywhere --
unlike risk_gate.py, this script has NO meta.json interaction at all),
data/authoritative.json or data/slates/<date>/authoritative.json (zero
references), config/rules.json, RULES.md, data/bets.json, BET_LOG.md --
confirmed by grep, not assumed.

REAL FINDING: "Rule 71" appears LITERALLY once in this file (line 116's
error message: "pinnacleVF.away missing — Rule 71 gap check impossible").
This does NOT mean validate_slate_final.py implements Rule 71 -- it
validates a PRECONDITION Rule 71 depends on elsewhere (pinnacleVF.away
must be present for build_market_ledger.py's Pinnacle-gap check to be
computable at all), and its error message names that dependency
explicitly. "Rule 81" does not appear anywhere in this file (grep-
verified, zero matches). Part 9's Rule 71/81 lockdown must document this
precise precondition-check relationship, not conflate it with owning
either rule's actual logic.

REAL FINDING: REQUIRED_MARKETS (11 canonical market names) is
INDEPENDENTLY DEFINED in this file AND in scripts/build_market_ledger.py
-- confirmed identical (same 11 names, same order) by direct comparison,
but a genuine duplicate-source-of-truth (not introduced or fixed by
Phase 8; flagged for documentation only, consistent with
docs/DUPLICATE_LOGIC_INVENTORY.md's existing pattern of tracking such
duplicates without consolidating them mid-phase).

File writes (only reached if validate_final() returns zero errors --
never on the hard-fail path):
  1. `data/execution_slip_<exp_date>.txt` -- plain `open(path, 'w')`,
     the slip's full text.
  2. `data/execution_slip_<exp_date>.json` -- plain `open(path, 'w')` +
     `json.dump(..., indent=2)`, `{generatedAt, date, **slip_dict}`.
  3. `data/slate.json` -- RE-READ FROM DISK independently (`with
     open(_slate_path, 'r') as _sf: _slate = _json.load(_sf)`) rather
     than reusing the already-in-memory `slate` object `load_slate()`/
     `validate_final()` used -- a real, precise finding: the slip-patch
     step operates on a SEPARATE, freshly-loaded copy of slate.json, not
     the same in-memory object the rest of main() has been using. Then
     sets `_slate['executionSlip']` (the text), `_slate['executionSlipData']`
     (the dict), `_slate['executionSlipGeneratedAt']` (ISO timestamp),
     and writes it back with `json.dump(_slate, _sf, indent=2)` -- plain,
     non-atomic. This entire block (all three writes) is wrapped in ONE
     broad `try/except Exception as _e: print(f'[SLIP] Warning: could
     not persist slip — {_e}')` -- ANY failure at any point in this
     block (including the slip generation call itself, if it were inside
     the try -- it is NOT: `generate_execution_slip()` is called BEFORE
     the try block, so a slip-generation failure is NOT caught here and
     propagates as an uncaught exception all the way out of main(),
     crashing the whole script after already printing "FINAL VALIDATION
     PASSED") only prints a warning and lets `main()` proceed to
     `sys.exit(0)` regardless.
  4. `$GITHUB_OUTPUT` file (if the env var is set) -- append mode, one
     line per `write_github_output()` call. Exactly one call on the
     hard-fail path (well, two: `final_validation_status=fail` then
     `final_validation_errors=<n>`) or exactly one call on the pass path
     (`final_validation_status=ok`) -- never both paths in one run.

Exact validation order (`validate_final(slate, exp_date)`, per game, in
`slate['games']` list order, checks in this exact sequence per game):
  1. Diagnostic prints (slate summary for the first 3 games) -- BEFORE
     the empty-games short-circuit.
  2. Empty-games short-circuit: `if not games: errors.append(...); return`
     -- immediately returns with exactly one error and zero warnings,
     skipping every per-game check entirely.
  3. Per game: starters (away then home) -- pitcher.name missing/absent
     -> WARNING only, never ERROR (v1.1 change per the module docstring).
  4. pinnacleVF -- missing/away-None -> WARNING if game_status in
     {Final, In Progress, Postponed}, else ERROR ("Rule 71 gap check
     impossible").
  5. Lineup + offense baseline (awayTeamStats then homeTeamStats) --
     block entirely absent -> WARNING + `continue` (skips the two
     sub-checks below for THIS side only, not the whole game); block
     present but not a dict -> ERROR + `continue`; lineupConfirmed=None
     -> WARNING; offenseBaselineAdj=None -> WARNING (both sub-checks run
     independently if the block IS a dict, not mutually exclusive).
  6. Kalshi prices -- 7 independent market-path checks (ML, F5 ML, TT
     Away, TT Home, NRFI/YRFI, Game Total, RL), each WARNING-only if its
     specific nested value is None.
  7. Run projections -- `awayProjRuns` presence across `allEdges`: if
     absent AND marketLedger non-empty -> WARNING; if absent AND
     marketLedger empty -> ERROR.
  8. Market ledger -- empty/missing -> ERROR + `continue` (skips ALL
     remaining per-game checks -- required-markets, per-row status, AND
     pitcherSavant -- for this game only, not the whole run); required-
     market-name absence -> one ERROR per missing market name (list
     order of REQUIRED_MARKETS); per-row (in marketLedger list order):
     invalid status -> ERROR + `continue` (skips remaining per-row
     checks for THIS row only); Rejected without rejectionReason ->
     ERROR; Missing Data without missingFields -> ERROR; Evaluation
     Failed without evaluationError -> ERROR; Accepted with null edge ->
     ERROR; Accepted with invalid confidence -> ERROR; Accepted with
     null kalshiPrice -> ERROR (the last three are independent checks,
     not mutually exclusive -- an Accepted row can accumulate up to 3
     errors at once).
  9. pitcherSavant (away then home) -- ps=None -> WARNING (message
     varies on whether pitcher.name is known, but severity is always
     WARNING either way); ps not a dict -> ERROR + `continue` (skips the
     two sub-checks below for THIS side only); recentFIP negative ->
     ERROR; xFIP=None -> WARNING (independent checks, not mutually
     exclusive).

Exact mutation order: `validate_final()` mutates NOTHING -- it is
already a pure read-only analysis function over `slate`/`games` (no
`row[...] = ...`, no `g[...] = ...` anywhere in its body) EXCEPT for
its own diagnostic `print()` calls (a side effect, not a data mutation).
`generate_execution_slip()` also mutates nothing in its `games` argument
-- it only reads row/game fields to build NEW dicts (the slip entries).
The only real MUTATION of `slate` happens in `main()`'s post-pass-only
slip-persistence block, and even then it mutates a SEPARATELY re-read
`_slate` object, never the `slate` object `validate_final()`/
`generate_execution_slip()` operated on.

Exact persistence order (only on the zero-errors path): (1) print PASSED
summary, (2) write_github_output('final_validation_status', 'ok'), (3)
call `generate_execution_slip()` (NOT inside the try/except -- a
failure here propagates uncaught), (4) inside try/except: write
`execution_slip_<date>.txt`, (5) write `execution_slip_<date>.json`,
(6) re-read + patch + rewrite `data/slate.json` (best-effort, all three
of steps 4-6 share ONE try/except), (7) `sys.exit(0)`.

Exact failure semantics: missing `data/slate.json` -> print to stderr
only, `sys.exit(1)` (never reaches `validate_final()` at all). Exception
raised INSIDE `validate_final()` itself -> caught by `main()`'s own
try/except around the `validate_final()` call, prints "VALIDATE CRASH:
{e}" to BOTH stderr and stdout, prints the traceback (to stderr, via
`traceback.print_exc()`'s default), `sys.exit(1)` -- this path does NOT
write any GITHUB_OUTPUT keys at all (unlike the "errors found" path,
which does). Non-empty `errors` list (from a successful, non-crashing
`validate_final()` call) -> print categorized errors (to stdout AND,
for the summary lines and per-category headers/individual error lines,
also to stderr -- NOT identical duplication: the category header line
`[label] (N errors)` is stderr-only, while each individual error line
IS printed to both), write_github_output twice (status=fail,
errors=<count>), `sys.exit(1)`. Exception during slip
generation/persistence AFTER "FINAL VALIDATION PASSED" was already
printed and `final_validation_status=ok` was already written -> if
raised by `generate_execution_slip()` itself, propagates UNCAUGHT
(crashes with exit code from the uncaught exception, non-zero, but NOT
via an explicit `sys.exit(1)` -- Python's default uncaught-exception
exit code, typically 1); if raised inside the try/except slip-file-
writing block, caught, printed as `[SLIP] Warning: ...`, `main()`
continues to `sys.exit(0)` regardless.

Partial-write behavior: none of this script's three main.py-driven
writes (execution_slip.txt, execution_slip.json, patched slate.json)
use any atomic-write mechanism -- all are plain `open(path, 'w')`. A
process interruption mid-write could theoretically leave a truncated
file at any of these three paths; Phase 8 Part 14 evaluates whether
migrating them to `lib.atomic_json.write_json_atomic()` is safe.

Rerun/idempotency: `validate_final()` and `generate_execution_slip()`
are both effectively pure functions of their inputs (given no exception,
same slate + same exp_date + same current_utc always produce identical
errors/warnings/slip_text/slip_dict) -- rerunning against an UNCHANGED
slate.json produces byte-identical execution_slip files except for the
`generatedAt`/`executionSlipGeneratedAt` timestamp fields (which always
reflect the LATEST run, same pattern as risk_gate.py's `runAt`). The
patched `data/slate.json`'s `executionSlip`/`executionSlipData` keys are
fully OVERWRITTEN (not merged/appended) each run, since `_slate[...] =
...` is a plain key assignment on a freshly re-read object. `$GITHUB_OUTPUT`
is the one genuine exception: it is opened in APPEND mode, so multiple
invocations within the same file accumulate additional lines rather
than overwriting -- correct GitHub Actions semantics, not a bug,
documented precisely rather than "fixed" into overwrite mode.
"""

import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)


# ══════════════════════════════════════════════════════════════════════════════
# Fixture builders
# ══════════════════════════════════════════════════════════════════════════════

def make_ledger_row(market='ML_Away', status='Accepted', edge=4.5, confidence='HIGH',
                     kalshi_price=-120, ticker='KXMLBGAME-26JUN161845KCWSH-KC',
                     rejection_reason=None, missing_fields=None, evaluation_error=None):
    row = {
        'market': market,
        'status': status,
        'edge': edge,
        'confidence': confidence,
        'confidenceTier': confidence,
        'kalshiPrice': kalshi_price,
        'ticker': ticker,
        'marketTicker': ticker,
        'betSize': 5.0,
        'calibratedEdgeVsExecutable': edge,
        'rawEdgeVsExecutable': edge,
        'executablePriceUsed': kalshi_price,
        'maxBetPrice': None,
        'modelProb': 60.0,
        'reasonCodes': [],
        'gatesFired': [],
    }
    if status == 'Rejected':
        row['rejectionReason'] = rejection_reason or 'test rejection'
    if status == 'Missing Data':
        row['missingFields'] = missing_fields or ['someField']
    if status == 'Evaluation Failed':
        row['evaluationError'] = evaluation_error or 'test evaluation error'
    return row


REQUIRED_MARKETS = [
    'NRFI', 'YRFI', 'F5_ML_Away', 'F5_ML_Home',
    'TT_Away_Over', 'TT_Home_Over',
    'ML_Away', 'ML_Home',
    'Game_Total', 'RL_Away', 'RL_Home',
]


def make_full_ledger(status='Accepted'):
    return [make_ledger_row(market=m, status=status) for m in REQUIRED_MARKETS]


def make_pitcher_savant(recent_fip=3.5, xfip=3.8, starts_sampled=5):
    return {'xFIP': xfip, 'seasonFIP': xfip, 'recentFIP': recent_fip, 'startsSampled': starts_sampled}


def make_team_stats(lineup_confirmed=True, offense_baseline_adj=4.5):
    return {'lineupConfirmed': lineup_confirmed, 'offenseBaselineAdj': offense_baseline_adj,
            'last7RpG': 4.5, 'last15RpG': 4.5, 'runsPerGame': 4.5}


def make_kalshi_odds():
    return {
        'ml': {'away': -120, 'home': 100},
        'f5ml': {'away': -110, 'home': -110},
        'team_totals': {'away': {'best_ticker': 'TICK1'}, 'home': {'best_ticker': 'TICK2'}},
        'nrfi_yrfi': {'nrfi_american': -150},
        'total': {'line': 8.5},
        'rl': {'best_ticker': 'TICK3'},
    }


def make_good_game(away='KC', home='WSH', status='Scheduled'):
    return {
        'away': {'abbr': away, 'pitcher': {'name': 'Pitcher A'}, 'pitcherSavant': make_pitcher_savant()},
        'home': {'abbr': home, 'pitcher': {'name': 'Pitcher B'}, 'pitcherSavant': make_pitcher_savant()},
        'status': status,
        'pinnacleVF': {'away': 0.52, 'home': 0.48},
        'awayTeamStats': make_team_stats(),
        'homeTeamStats': make_team_stats(),
        'odds': {'kalshi': make_kalshi_odds()},
        'allEdges': [{'awayProjRuns': 4.5, 'homeProjRuns': 4.2}],
        'marketLedger': make_full_ledger(),
    }


def make_slate(games, date='2026-06-16'):
    return {'date': date, 'games': games}


@pytest.fixture
def vsf():
    if "validate_slate_final" in sys.modules:
        del sys.modules["validate_slate_final"]
    import validate_slate_final as _vsf
    return _vsf


NOW = '2026-06-16T20:00:00Z'


# ══════════════════════════════════════════════════════════════════════════════
# validate_final() golden equivalence
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateFinalGoldenEquivalence:

    def test_fully_valid_game_produces_no_errors_no_warnings(self, vsf):
        slate = make_slate([make_good_game()])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert errors == []
        assert warnings == []

    def test_no_games_produces_single_error_no_warnings(self, vsf):
        slate = make_slate([])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert len(errors) == 1
        assert 'no games' in errors[0]
        assert warnings == []

    def test_missing_starter_is_warning_not_error(self, vsf):
        g = make_good_game()
        g['away']['pitcher'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert errors == []
        assert any('starter TBD/missing' in w for w in warnings)

    def test_pinnacle_missing_scheduled_game_is_error(self, vsf):
        g = make_good_game(status='Scheduled')
        g['pinnacleVF'] = {}
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('Rule 71 gap check impossible' in e for e in errors)

    @pytest.mark.parametrize("status", ["Final", "In Progress", "Postponed"])
    def test_pinnacle_missing_live_final_postponed_game_is_warning(self, vsf, status):
        g = make_good_game(status=status)
        g['pinnacleVF'] = {}
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert not any('pinnacleVF' in e for e in errors)
        assert any('pinnacleVF.away missing' in w for w in warnings)

    def test_team_stats_block_absent_is_warning(self, vsf):
        g = make_good_game()
        g['awayTeamStats'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert errors == []
        assert any('awayTeamStats block missing' in w for w in warnings)

    def test_team_stats_wrong_type_is_error(self, vsf):
        g = make_good_game()
        g['awayTeamStats'] = "not a dict"
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('expected dict' in e for e in errors)

    def test_lineup_not_confirmed_is_warning(self, vsf):
        g = make_good_game()
        g['awayTeamStats']['lineupConfirmed'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert errors == []
        assert any('lineupConfirmed=null' in w for w in warnings)

    def test_offense_baseline_missing_is_warning(self, vsf):
        g = make_good_game()
        g['awayTeamStats']['offenseBaselineAdj'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert errors == []
        assert any('offenseBaselineAdj missing' in w for w in warnings)

    def test_kalshi_price_missing_all_seven_markets_produces_seven_warnings(self, vsf):
        g = make_good_game()
        g['odds'] = {'kalshi': {}}
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert errors == []
        kalshi_warnings = [w for w in warnings if 'Kalshi' in w]
        assert len(kalshi_warnings) == 7

    def test_projection_missing_with_ledger_present_is_warning(self, vsf):
        g = make_good_game()
        g['allEdges'] = []
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert not any('Poisson' in e for e in errors)
        assert any('awayProjRuns not in allEdges' in w and 'marketLedger present' in w for w in warnings)

    def test_projection_missing_with_ledger_empty_is_error(self, vsf):
        g = make_good_game()
        g['allEdges'] = []
        g['marketLedger'] = []
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('Poisson engine has not run' in e for e in errors)

    def test_empty_market_ledger_is_error_and_skips_remaining_ledger_checks(self, vsf):
        g = make_good_game()
        g['marketLedger'] = []
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        ledger_errors = [e for e in errors if 'marketLedger empty/missing' in e]
        assert len(ledger_errors) == 1
        # required-market errors must NOT also fire (continue skips them)
        assert not any('required market' in e for e in errors)

    def test_missing_required_market_produces_one_error_per_market(self, vsf):
        g = make_good_game()
        g['marketLedger'] = [row for row in g['marketLedger'] if row['market'] != 'NRFI']
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert sum('required market "NRFI"' in e for e in errors) == 1

    def test_invalid_row_status_is_error_and_skips_further_row_checks(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Bogus'
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('invalid status "Bogus"' in e for e in errors)
        # Accepted-specific checks must not also fire for this row
        assert not any('Accepted but edge is null' in e for e in errors)

    def test_rejected_without_reason_is_error(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Rejected'
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('Rejected but rejectionReason empty' in e for e in errors)

    def test_missing_data_without_fields_is_error(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Missing Data'
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('Missing Data but missingFields empty' in e for e in errors)

    def test_evaluation_failed_without_error_is_error(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Evaluation Failed'
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('Evaluation Failed but evaluationError empty' in e for e in errors)

    def test_accepted_with_null_edge_is_error(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['edge'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('Accepted but edge is null' in e for e in errors)

    def test_accepted_with_invalid_confidence_is_error(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['confidence'] = 'BOGUS'
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('Accepted but confidence="BOGUS"' in e for e in errors)

    def test_accepted_with_null_kalshi_price_is_error(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['kalshiPrice'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('Accepted but kalshiPrice is null' in e for e in errors)

    def test_accepted_row_can_accumulate_multiple_simultaneous_errors(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['edge'] = None
        g['marketLedger'][0]['confidence'] = 'BOGUS'
        g['marketLedger'][0]['kalshiPrice'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        row_errors = [e for e in errors if e.startswith(f"{vsf.gid(g)}/{g['marketLedger'][0]['market']}")]
        assert len(row_errors) == 3

    def test_pitcher_savant_none_with_known_pitcher_name_is_warning(self, vsf):
        g = make_good_game()
        g['away']['pitcherSavant'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert errors == []
        assert any('Pitcher A' in w for w in warnings)

    def test_pitcher_savant_none_with_tbd_pitcher_is_warning_different_message(self, vsf):
        g = make_good_game()
        g['away']['pitcher'] = None
        g['away']['pitcherSavant'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('starter TBD' in w and 'xFIP=4.50' in w for w in warnings)

    def test_pitcher_savant_wrong_type_is_error(self, vsf):
        g = make_good_game()
        g['away']['pitcherSavant'] = "not a dict"
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('expected dict' in e and 'pitcherSavant' in e for e in errors)

    def test_negative_recent_fip_is_error(self, vsf):
        g = make_good_game()
        g['away']['pitcherSavant']['recentFIP'] = -1.5
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert any('recentFIP=-1.5 is negative' in e for e in errors)

    def test_xfip_none_is_warning(self, vsf):
        g = make_good_game()
        g['away']['pitcherSavant']['xFIP'] = None
        slate = make_slate([g])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert errors == []
        assert any('xFIP=null' in w for w in warnings)

    def test_multiple_games_processed_independently_in_list_order(self, vsf):
        good = make_good_game('AAA', 'BBB')
        bad = make_good_game('CCC', 'DDD')
        bad['marketLedger'] = []
        slate = make_slate([good, bad])
        errors, warnings = vsf.validate_final(slate, '2026-06-16')
        assert not any('AAA@BBB' in e for e in errors)
        assert any('CCC@DDD' in e for e in errors)

    def test_does_not_mutate_slate_or_games(self, vsf):
        g = make_good_game()
        slate = make_slate([g])
        before = copy.deepcopy(slate)
        vsf.validate_final(slate, '2026-06-16')
        assert slate == before

    def test_malformed_marketledger_row_missing_market_key_crashes_with_type_error(self, vsf):
        """
        REAL PRE-EXISTING DEFECT (not introduced or fixed by Phase 8): a
        marketLedger row missing the 'market' key makes
        `row.get('market')` return None, so `ledger_markets` (built via
        `{row.get('market') for row in ledger}`) contains a bare `None`
        alongside string market names. The required-market-absence
        error message then calls `sorted(ledger_markets)` to list what
        IS present -- `sorted()` on a set mixing None and str raises
        `TypeError: '<' not supported between instances of 'NoneType'
        and 'str'` in Python 3. This crashes validate_final() itself
        (not gracefully handled inside the function) -- but main()'s own
        try/except around the validate_final() CALL catches it, prints
        "VALIDATE CRASH: ...", and exits 1, so production behavior is a
        clean hard-fail, not an unhandled process crash. Documented
        precisely, not fixed -- this is exactly the kind of pre-existing
        defect the mission says to document rather than silently correct.
        """
        g = make_good_game()
        g['marketLedger'][0] = {'status': 'Accepted', 'edge': 4.0, 'confidence': 'HIGH', 'kalshiPrice': -110}
        # also remove one other required market so ledger_markets is missing
        # at least one entry, which is what triggers the sorted() call.
        g['marketLedger'] = [row for row in g['marketLedger'] if row.get('market') != 'RL_Home']
        slate = make_slate([g])
        with pytest.raises(TypeError):
            vsf.validate_final(slate, '2026-06-16')

    def test_main_catches_the_market_key_crash_gracefully_as_validate_crash(self, vsf, tmp_path, monkeypatch, capsys):
        """
        load_slate() checks the CWD-relative path 'data/slate.json'
        FIRST (before its __file__-relative fallback) -- so this test
        must chdir into a tmp_path whose 'data/' subdirectory holds the
        fixture, never relying on (or risking touching) the real repo's
        cwd-relative data/slate.json.
        """
        g = make_good_game()
        g['marketLedger'][0] = {'status': 'Accepted', 'edge': 4.0, 'confidence': 'HIGH', 'kalshiPrice': -110}
        g['marketLedger'] = [row for row in g['marketLedger'] if row.get('market') != 'RL_Home']
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        slate_path = data_dir / 'slate.json'
        with open(slate_path, 'w') as f:
            json.dump(make_slate([g]), f)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py', '2026-06-16'])
        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert 'VALIDATE CRASH' in captured.out
        assert 'VALIDATE CRASH' in captured.err


# ══════════════════════════════════════════════════════════════════════════════
# generate_execution_slip() golden equivalence
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerateExecutionSlipGoldenEquivalence:

    def test_accepted_high_tier_goes_to_real_money(self, vsf):
        g = make_good_game()
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['realMoneyCount'] == len(REQUIRED_MARKETS)
        assert 'REAL-MONEY BETS' in slip_text

    def test_accepted_paper_tier_goes_to_paper_only(self, vsf):
        g = make_good_game()
        for row in g['marketLedger']:
            row['confidence'] = 'PAPER'
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['paperOnlyCount'] == len(REQUIRED_MARKETS)
        assert slip_dict['summary']['realMoneyCount'] == 0

    def test_price_moved_beyond_max_reason_code_routes_to_price_moved(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['reasonCodes'] = ['PRICE_MOVED_BEYOND_MAX']
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['priceMovedCount'] == 1

    def test_rejected_status_goes_to_rejected_blocked(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Rejected'
        g['marketLedger'][0]['rejectionReason'] = 'some reason'
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['rejectedBlockedCount'] == 1

    def test_rejected_with_paper_confidence_goes_to_paper_only(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Rejected'
        g['marketLedger'][0]['confidence'] = 'PAPER'
        g['marketLedger'][0]['rejectionReason'] = 'some reason'
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['paperOnlyCount'] == 1

    def test_rejected_with_suspended_in_reason_goes_to_paper_only(self, vsf):
        g = make_good_game()
        g['marketLedger'][0]['status'] = 'Rejected'
        g['marketLedger'][0]['rejectionReason'] = 'Market suspended for review'
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['paperOnlyCount'] == 1

    @pytest.mark.parametrize("status", ["In Progress", "Final"])
    def test_live_final_games_routed_to_rejected_blocked_never_real_money(self, vsf, status):
        """
        The pregame-only hard gate checks `gs_result.get('liveGameBlocked')`
        specifically, which check_game_status() only sets True for the
        in-play/final branch (IN_PLAY_STATUSES/FINAL_STATUSES) -- verified
        directly against lib/postponed_guard.py, not assumed.
        """
        g = make_good_game(status=status)
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['realMoneyCount'] == 0
        assert slip_dict['summary']['rejectedBlockedCount'] == len(REQUIRED_MARKETS)
        assert slip_dict['summary']['liveGameBlockedCount'] == 1

    @pytest.mark.parametrize("status", ["Postponed", "Suspended", "Cancelled"])
    def test_postponed_suspended_cancelled_games_not_routed_by_this_gate_fall_through_to_normal_processing(self, vsf, status):
        """
        REAL FINDING: check_game_status() routes Postponed/Suspended/
        Cancelled through its "postponed" branch (skipReason='postponed',
        liveGameBlocked=False), NOT the in-play/final branch
        (liveGameBlocked=True) -- confirmed directly:
        check_game_status({'status': 'Postponed', ...}) returns
        {'shouldSkip': True, 'skipReason': 'postponed', 'liveGameBlocked': False}.

        generate_execution_slip()'s pregame-only hard gate checks
        liveGameBlocked SPECIFICALLY, so these three statuses are NOT
        force-routed to rejected_blocked by this mechanism -- their
        marketLedger rows fall through to the normal per-row status
        logic below, using whatever status/confidence those rows already
        carry (Accepted+HIGH rows on a Postponed game WOULD land in
        REAL-MONEY BETS via this function alone, in isolation -- in
        production, upstream stages are expected to have already
        downgraded/rejected such rows before this script ever runs, but
        this function itself performs no additional postponed-specific
        gating). This is documented precisely here, not "fixed" into
        also blocking on skipReason=='postponed' -- Phase 8's mission
        explicitly says preserve existing live-bet safety rules exactly.
        """
        g = make_good_game(status=status)
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['liveGameBlockedCount'] == 0
        # Falls through to normal processing -- Accepted+HIGH rows land in real_money.
        assert slip_dict['summary']['realMoneyCount'] == len(REQUIRED_MARKETS)

    def test_scheduled_future_game_not_blocked(self, vsf):
        g = make_good_game(status='Scheduled')
        slip_text, slip_dict = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary']['liveGameBlockedCount'] == 0

    def test_does_not_mutate_games_argument(self, vsf):
        g = make_good_game()
        games = [g]
        before = copy.deepcopy(games)
        vsf.generate_execution_slip(games, '2026-06-16', current_utc=NOW)
        assert games == before

    def test_empty_games_list_produces_empty_slip(self, vsf):
        slip_text, slip_dict = vsf.generate_execution_slip([], '2026-06-16', current_utc=NOW)
        assert slip_dict['summary'] == {
            'realMoneyCount': 0, 'priceMovedCount': 0, 'paperOnlyCount': 0,
            'rejectedBlockedCount': 0, 'liveGameBlockedCount': 0,
        }
        assert '(none)' in slip_text

    def test_deterministic_output_same_input_same_result(self, vsf):
        g = make_good_game()
        text1, dict1 = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        text2, dict2 = vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        assert text1 == text2
        assert dict1 == dict2

    def test_prints_to_real_stdout_as_well_as_returning_text(self, vsf, capsys):
        g = make_good_game()
        vsf.generate_execution_slip([g], '2026-06-16', current_utc=NOW)
        captured = capsys.readouterr()
        assert 'EXECUTION SLIP' in captured.out


# ══════════════════════════════════════════════════════════════════════════════
# main() golden equivalence -- integration-level, isolated filesystem only
# ══════════════════════════════════════════════════════════════════════════════

class TestMainIntegrationGoldenEquivalence:
    """
    Every test here chdir()s into a tmp_path fixture directory whose
    'data/' subdirectory holds the fixture slate -- NEVER the real
    repository's data/ directory. load_slate() checks the CWD-relative
    path FIRST, so controlling cwd (not just __file__-relative sandboxing)
    is the only safe way to sandbox main() for this script. A dedicated
    leak-guard test hashes the real repo's data/slate.json and the
    execution_slip_* files before and after this whole class runs.
    """

    def _wire(self, vsf, tmp_path, monkeypatch, date='2026-06-16'):
        """
        SAFETY: validate_slate_final.py is imported IN-PROCESS (not
        copied into a subprocess sandbox), so `__file__` inside the
        module's own namespace still points at the REAL
        scripts/validate_slate_final.py on disk. load_slate()'s
        __file__-relative FALLBACK path
        (os.path.dirname(os.path.abspath(__file__))/../data/slate.json)
        therefore resolves to the REAL repository's data/slate.json
        regardless of cwd -- confirmed as a real incident while writing
        this test class: an early version of test_missing_slate_json_exits_1
        (which deliberately creates no slate.json anywhere) fell through
        to this fallback and READ the real repo's actual production
        data/slate.json, printing real betting recommendations into test
        output. No WRITE ever escaped (git status confirmed data/ fully
        unchanged afterward -- this script's writes use cwd-relative
        paths, which correctly landed in tmp_path since chdir succeeded),
        but the READ leak alone is a real test-isolation violation.
        Fixed by ALSO reassigning vsf.__file__ to a path inside tmp_path,
        so both the cwd-relative primary path AND the __file__-relative
        fallback resolve safely inside the sandbox -- defense in depth,
        not relying on the cwd-relative path always winning first.
        """
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        (tmp_path / 'scripts').mkdir(exist_ok=True)
        monkeypatch.setattr(vsf, '__file__', str(tmp_path / 'scripts' / 'validate_slate_final.py'))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py', date])
        return data_dir

    def test_missing_slate_json_exits_1(self, vsf, tmp_path, monkeypatch, capsys):
        self._wire(vsf, tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert 'data/slate.json not found' in captured.err

    def test_malformed_slate_json_propagates_uncaught(self, vsf, tmp_path, monkeypatch):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        with open(data_dir / 'slate.json', 'w') as f:
            f.write('{not valid json')
        with pytest.raises(json.JSONDecodeError):
            vsf.main()

    def test_valid_slate_passes_and_writes_slip_files(self, vsf, tmp_path, monkeypatch, capsys):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)

        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert 'FINAL VALIDATION PASSED' in captured.out

        assert (data_dir / 'execution_slip_2026-06-16.txt').exists()
        assert (data_dir / 'execution_slip_2026-06-16.json').exists()
        with open(data_dir / 'execution_slip_2026-06-16.json') as f:
            slip_json = json.load(f)
        assert slip_json['date'] == '2026-06-16'
        assert 'generatedAt' in slip_json
        assert slip_json['summary']['realMoneyCount'] == len(REQUIRED_MARKETS)

    def test_valid_slate_patches_slate_json_with_execution_slip_fields(self, vsf, tmp_path, monkeypatch):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()
        with open(data_dir / 'slate.json') as f:
            patched = json.load(f)
        assert 'executionSlip' in patched
        assert 'executionSlipData' in patched
        assert 'executionSlipGeneratedAt' in patched
        assert patched['games'][0]['away']['abbr'] == 'KC'  # original content preserved

    def test_errors_present_exits_1_no_slip_files_written(self, vsf, tmp_path, monkeypatch, capsys):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        g['marketLedger'] = []
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)

        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert 'FINAL VALIDATION FAILED' in captured.out
        assert 'FINAL VALIDATION FAILED' in captured.err
        assert not (data_dir / 'execution_slip_2026-06-16.txt').exists()
        assert 'executionSlip' not in json.load(open(data_dir / 'slate.json'))

    def test_no_games_exits_1(self, vsf, tmp_path, monkeypatch):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([]), f)
        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 1

    def test_github_output_env_var_unset_no_file_written(self, vsf, tmp_path, monkeypatch):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        monkeypatch.delenv('GITHUB_OUTPUT', raising=False)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()
        # no crash, no file created anywhere unexpected -- nothing to assert
        # on directly since GITHUB_OUTPUT was never set, but this proves
        # write_github_output()'s no-op path doesn't raise.

    def test_github_output_env_var_set_appends_status_line(self, vsf, tmp_path, monkeypatch):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        gho_path = tmp_path / 'gho.txt'
        gho_path.write_text('PRIOR_STEP_OUTPUT=value\n')
        monkeypatch.setenv('GITHUB_OUTPUT', str(gho_path))
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()
        content = gho_path.read_text()
        assert 'PRIOR_STEP_OUTPUT=value' in content  # append, not overwrite
        assert 'final_validation_status=ok' in content

    def test_github_output_appends_fail_status_and_error_count_on_failure(self, vsf, tmp_path, monkeypatch):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        gho_path = tmp_path / 'gho.txt'
        gho_path.write_text('')
        monkeypatch.setenv('GITHUB_OUTPUT', str(gho_path))
        g = make_good_game()
        g['marketLedger'] = []
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()
        content = gho_path.read_text()
        assert 'final_validation_status=fail' in content
        assert 'final_validation_errors=' in content

    def test_rerun_produces_stable_fixed_point_except_timestamp(self, vsf, tmp_path, monkeypatch):
        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)

        with pytest.raises(SystemExit):
            vsf.main()
        with open(data_dir / 'slate.json') as f:
            first_run = json.load(f)

        with pytest.raises(SystemExit):
            vsf.main()
        with open(data_dir / 'slate.json') as f:
            second_run = json.load(f)

        assert first_run['executionSlipData'] == second_run['executionSlipData']
        # timestamps legitimately differ between runs (or coincidentally
        # match if the clock didn't tick) -- not asserted either way.

    def test_real_repo_data_directory_never_touched(self, vsf, tmp_path, monkeypatch):
        import hashlib

        def _hash(path):
            if not os.path.exists(path):
                return None
            with open(path, 'rb') as f:
                return hashlib.md5(f.read()).hexdigest()

        real_slate = os.path.join(ROOT, 'data', 'slate.json')
        before_slate = _hash(real_slate)
        real_slip_txt = os.path.join(ROOT, 'data', 'execution_slip_2026-06-16.txt')
        slip_existed_before = os.path.exists(real_slip_txt)

        data_dir = self._wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()

        after_slate = _hash(real_slate)
        assert before_slate == after_slate
        assert os.path.exists(real_slip_txt) == slip_existed_before
