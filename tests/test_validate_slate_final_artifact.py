#!/usr/bin/env python3
"""
tests/test_validate_slate_final_artifact.py
===============================================
Phase 8 Part 10 coverage for the data/pipeline/<date>/validation.json
pipeline artifact main() now publishes, using the existing
lib/pipeline_artifacts.py convention (the same one risk_gate.py,
build_market_ledger.py, and enrich_data.py already use).

DECISION (Phase 8 Part 10): introduce data/pipeline/<date>/validation.json.
Smallest-migration justification:
  - The payload (build_validation_artifact_payload()) is built entirely
    from the (errors, warnings) pair validate_final() already computed
    for the legacy path -- publishing it requires no new validation
    computation (Part 11: still exactly one validation per run).
  - The write is wired in via the exact same pattern risk_gate.py's
    execution-artifact publication already uses: a bare `try/except
    Exception` around `write_stage_artifact(...)` that can only print a
    WARNING on failure, never touch final_validation_status, never
    affect the exit code, and never touch slate.json or any other file.
  - Schema is deliberately narrow: {date, status, gameCount, errorCount,
    warningCount, errors, warnings} -- no settlement/P&L fields, no
    full-slate payload, no per-game-market decision detail (this script
    does not own those; build_market_ledger.py does).
  - status="canonical" (the payload IS the intended shape, not a
    stopgap full-slate snapshot) and source_stage="recommendations"
    (matching build_market_ledger.py's own published stage name, since
    that is what validate_slate_final.py actually validates).

These tests prove the artifact is additive-only: identical
errors/warnings/exit-code/stdout-minus-one-line behavior whether or not
the artifact write itself succeeds.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, LIB_DIR)
sys.path.insert(0, SCRIPTS_DIR)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_validate_slate_final_immutable import make_good_game, make_slate  # noqa: E402


@pytest.fixture
def vsf():
    if "validate_slate_final" in sys.modules:
        del sys.modules["validate_slate_final"]
    import validate_slate_final as _vsf
    return _vsf


def _wire(vsf, tmp_path, monkeypatch, date='2026-06-16'):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (tmp_path / 'scripts').mkdir(exist_ok=True)
    monkeypatch.setattr(vsf, '__file__', str(tmp_path / 'scripts' / 'validate_slate_final.py'))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', ['validate_slate_final.py', date])
    return data_dir


class TestBuildValidationArtifactPayloadPure:

    def test_pass_status_when_no_errors(self, vsf):
        payload = vsf.build_validation_artifact_payload([make_good_game()], '2026-06-16', [], ['w1'])
        assert payload['status'] == 'pass'
        assert payload['errorCount'] == 0
        assert payload['warningCount'] == 1
        assert payload['warnings'] == ['w1']

    def test_fail_status_when_errors_present(self, vsf):
        payload = vsf.build_validation_artifact_payload([make_good_game()], '2026-06-16', ['e1'], [])
        assert payload['status'] == 'fail'
        assert payload['errors'] == ['e1']

    def test_narrow_schema_no_settlement_or_full_slate_fields(self, vsf):
        payload = vsf.build_validation_artifact_payload([make_good_game()], '2026-06-16', [], [])
        assert set(payload.keys()) == {
            'date', 'status', 'gameCount', 'errorCount', 'warningCount', 'errors', 'warnings',
        }
        assert 'games' not in payload
        assert 'marketLedger' not in payload
        assert 'pnl' not in payload
        assert 'settlement' not in payload

    def test_does_not_mutate_errors_or_warnings_lists(self, vsf):
        errors = ['e1', 'e2']
        warnings = ['w1']
        payload = vsf.build_validation_artifact_payload([make_good_game()], '2026-06-16', errors, warnings)
        payload['errors'].append('injected')
        assert errors == ['e1', 'e2']  # caller's list untouched by mutating the returned copy


class TestValidationArtifactWiredIntoMain:

    def test_valid_slate_writes_validation_artifact_with_pass_status(self, vsf, tmp_path, monkeypatch):
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 0

        artifact_path = data_dir / 'pipeline' / '2026-06-16' / 'validation.json'
        assert artifact_path.exists()
        with open(artifact_path) as f:
            envelope = json.load(f)
        assert envelope['meta']['stage'] == 'validation'
        assert envelope['meta']['slateDate'] == '2026-06-16'
        assert envelope['meta']['producedBy'] == 'scripts/validate_slate_final.py'
        assert envelope['meta']['status'] == 'canonical'
        assert envelope['meta']['sourceStage'] == 'recommendations'
        assert envelope['data']['status'] == 'pass'
        assert envelope['data']['errorCount'] == 0

    def test_failing_slate_still_writes_validation_artifact_with_fail_status(self, vsf, tmp_path, monkeypatch):
        """
        The artifact must be published on the FAIL path too (main()
        exits 1 with errors, but the artifact-write happens before that
        branch, using the same in-memory errors/warnings) -- publishing
        validation results is not conditioned on validation passing.
        """
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        g['marketLedger'] = []
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 1

        artifact_path = data_dir / 'pipeline' / '2026-06-16' / 'validation.json'
        assert artifact_path.exists()
        with open(artifact_path) as f:
            envelope = json.load(f)
        assert envelope['data']['status'] == 'fail'
        assert envelope['data']['errorCount'] > 0

    def test_artifact_write_failure_does_not_change_exit_code_or_status(self, vsf, tmp_path, monkeypatch, capsys):
        """
        Best-effort proof: if write_stage_artifact() itself raises (e.g.
        simulating a disk-full or permission error), main() must still
        reach the exact same exit code and print the exact same
        FINAL VALIDATION PASSED line -- only a WARNING line is added.
        Never causes legacy validation to fail (Part 10's core
        requirement).
        """
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)

        import pipeline_artifacts

        def _boom(*a, **kw):
            raise OSError('simulated disk-full')

        monkeypatch.setattr(pipeline_artifacts, 'write_stage_artifact', _boom)

        with pytest.raises(SystemExit) as exc_info:
            vsf.main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert 'FINAL VALIDATION PASSED' in captured.out
        assert 'WARNING: could not write validation pipeline artifact' in captured.out
        assert not (data_dir / 'pipeline').exists() or not list((data_dir / 'pipeline').glob('**/validation.json'))

    def test_artifact_write_never_touches_slate_json_or_execution_slip_files(self, vsf, tmp_path, monkeypatch):
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)
        with pytest.raises(SystemExit):
            vsf.main()
        with open(data_dir / 'slate.json') as f:
            patched = json.load(f)
        # slate.json still only carries the legacy executionSlip* fields,
        # never anything validation-artifact-shaped.
        assert 'validation' not in patched
        assert set(patched.keys()) >= {'date', 'games', 'executionSlip', 'executionSlipData', 'executionSlipGeneratedAt'}

    def test_does_not_re_run_validation_to_build_the_artifact(self, vsf, tmp_path, monkeypatch):
        """
        Object-identity/call-count proof: build_validation_artifact_payload()
        must be called with the SAME errors/warnings objects
        validate_final() returned to main() -- not a second call to
        validate_final() or _validate_games_pure().
        """
        data_dir = _wire(vsf, tmp_path, monkeypatch)
        g = make_good_game()
        with open(data_dir / 'slate.json', 'w') as f:
            json.dump(make_slate([g]), f)

        games_calls = {'n': 0}
        original_games = vsf._validate_games_pure

        def _spy(*a, **kw):
            games_calls['n'] += 1
            return original_games(*a, **kw)

        monkeypatch.setattr(vsf, '_validate_games_pure', _spy)
        with pytest.raises(SystemExit):
            vsf.main()
        assert games_calls['n'] == 1
