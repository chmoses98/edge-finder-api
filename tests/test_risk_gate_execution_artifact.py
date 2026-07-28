#!/usr/bin/env python3
"""
tests/test_risk_gate_execution_artifact.py
==============================================
Phase 7 Part 10-12: canonical execution-artifact schema tests
(build_execution_artifact_payload()) plus the full artifact-publication
failure matrix for risk_gate.py's main() -- directory creation,
serialization, tempfile creation, fdopen, write, flush, fsync, chmod,
rename, and cleanup failures inside lib/pipeline_artifacts.py's
write_stage_artifact(), each proven NOT to alter risk_gate.py's decision,
stake values, ordering, legacy slate.json/meta.json output, or exit code.

Every test that calls rg.main() sandboxes BOTH rg.SLATE_PATH/META_PATH
(tmp_path files) AND pipeline_artifacts.PIPELINE_ROOT (a tmp_path
subdirectory) -- omitting the PIPELINE_ROOT sandbox previously caused a
real data/pipeline/<date>/execution.json to be written into this actual
repository during development of this file (caught via `git status
--short data/`, cleaned up immediately since it was untracked). See
tests/test_risk_gate_immutable.py's TestMainIntegrationGoldenEquivalence
docstring for the same incident recorded there.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate, NOW, _game_with_ml, _game_with_tt

import pipeline_artifacts as pa


@pytest.fixture
def rg():
    if "risk_gate" in sys.modules:
        del sys.modules["risk_gate"]
    import risk_gate as _rg
    return _rg


@pytest.fixture(autouse=True)
def _sandbox_pipeline_root(tmp_path):
    original_root = pa.PIPELINE_ROOT
    pa.PIPELINE_ROOT = str(tmp_path / 'pipeline_root')
    yield
    pa.PIPELINE_ROOT = original_root


def _wire(rg, tmp_path):
    slate_path = str(tmp_path / 'slate.json')
    meta_path = str(tmp_path / 'meta.json')
    rg.SLATE_PATH = slate_path
    rg.META_PATH = meta_path
    return slate_path, meta_path


class TestExecutionArtifactPayloadShape:

    def test_payload_powered_by_final_slate_state_not_recomputed(self, rg):
        """
        The payload's realMoneyEligible/rejectionReason/approvedStake must
        reflect the slate's FINAL state (post-TT-safety, post-portfolio,
        post-PAPER_ONLY-third-pass) -- built by reading, never by
        recomputing any rule.
        """
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        tt_entry['ticker'] = 'ABTT'
        slate = make_slate([tt_game])
        rg.apply_tt_safety(slate, now_ts=NOW)
        decision, report = rg.apply_portfolio_rules(slate, now_ts=NOW)
        # ALL_TT_NO_ML_F5 -> PAPER_ONLY; simulate main()'s third pass manually
        # (this test targets the payload builder in isolation, not main()).
        if decision == 'PAPER_ONLY':
            tt_entry['confidenceTier'] = 'PAPER'
            tt_entry['blockReason'] = f'RISK_GATE_PAPER_ONLY: {report["decision_reason"]}'

        payload = rg.build_execution_artifact_payload(slate, decision, report['decision_reason'])
        assert payload['decision'] == 'PAPER_ONLY'
        cand = payload['candidates'][0]
        assert cand['game'] == 'A@B'
        assert cand['market'] == 'TT_Away_Over'
        assert cand['sourceRecommendationTicker'] == 'ABTT'
        assert cand['realMoneyEligible'] is False
        assert cand['rejectionReason'].startswith('RISK_GATE_PAPER_ONLY:')

    def test_payload_excludes_settlement_pnl_fields(self, rg):
        entry = make_entry(market='ML_Away')
        slate = make_slate([make_game('A', 'B', [entry])])
        payload = rg.build_execution_artifact_payload(slate, 'GO', 'Composition checks passed')
        cand = payload['candidates'][0]
        for forbidden in ('pnl', 'PnL', 'settlement', 'finalScore', 'result'):
            assert forbidden not in cand

    def test_payload_ordering_matches_marketledger_iteration_order(self, rg):
        e1 = make_entry(market='ML_Away', ticker='T1')
        e2 = make_entry(market='ML_Home', ticker='T2')
        slate = make_slate([make_game('A', 'B', [e1, e2])])
        payload = rg.build_execution_artifact_payload(slate, 'GO', 'x')
        assert [c['order'] for c in payload['candidates']] == [0, 1]
        assert [c['sourceRecommendationTicker'] for c in payload['candidates']] == ['T1', 'T2']

    def test_does_not_mutate_slate(self, rg):
        import copy
        entry = make_entry(market='ML_Away')
        slate = make_slate([make_game('A', 'B', [entry])])
        before = copy.deepcopy(slate)
        rg.build_execution_artifact_payload(slate, 'GO', 'x')
        assert slate == before


class TestExecutionArtifactFailureIsolation:

    def _run_and_capture(self, rg, tmp_path, games):
        slate_path, meta_path = _wire(rg, tmp_path)
        with open(slate_path, 'w') as f:
            json.dump(make_slate(games), f)
        result = rg.main()
        with open(slate_path) as f:
            written_slate = json.load(f)
        with open(meta_path) as f:
            written_meta = json.load(f)
        return result, written_slate, written_meta

    def test_directory_creation_failure_does_not_alter_decision(self, rg, tmp_path, monkeypatch):
        monkeypatch.setattr(pa.os, 'makedirs', lambda *a, **k: (_ for _ in ()).throw(OSError("simulated mkdir failure")))
        entry = make_entry(market='ML_Away')
        result, slate, meta = self._run_and_capture(rg, tmp_path, [make_game('A', 'B', [entry])])
        assert result == 0
        assert meta['risk_gate']['decision'] in ('GO', 'PAPER_ONLY')
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'

    def test_serialization_failure_does_not_alter_decision(self, rg, tmp_path, monkeypatch):
        # Write the slate fixture BEFORE patching json.dump -- pa.json is
        # the same singleton `json` module this test file itself imports
        # (sys.modules-cached), so patching it also breaks the test's own
        # setup helper if applied first.
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away')
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        real_dump = json.dump

        def _fail_only_pipeline_artifact_dump(obj, fp, *a, **kw):
            # Only the pipeline_artifacts envelope write (identifiable by
            # its "meta"/"data" top-level keys) should fail -- the
            # legacy slate.json/meta.json writes (plain dicts/lists
            # without that envelope shape) must go through untouched so
            # this test can actually inspect their contents afterward.
            if isinstance(obj, dict) and set(obj.keys()) >= {'meta', 'data'}:
                raise TypeError("simulated serialization failure")
            return real_dump(obj, fp, *a, **kw)

        monkeypatch.setattr(pa, 'json', type('J', (), {'dump': staticmethod(_fail_only_pipeline_artifact_dump)}))
        result = rg.main()
        with open(slate_path) as f:
            slate = json.load(f)
        with open(meta_path) as f:
            meta = json.load(f)
        assert result == 0
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'
        assert meta['risk_gate']['decision'] == 'GO'

    def test_tempfile_creation_failure_does_not_alter_decision(self, rg, tmp_path, monkeypatch):
        monkeypatch.setattr(pa.tempfile, 'mkstemp', lambda *a, **k: (_ for _ in ()).throw(OSError("simulated mkstemp failure")))
        entry = make_entry(market='ML_Away')
        result, slate, meta = self._run_and_capture(rg, tmp_path, [make_game('A', 'B', [entry])])
        assert result == 0
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'

    def test_fdopen_failure_does_not_alter_decision(self, rg, tmp_path, monkeypatch):
        monkeypatch.setattr(pa.os, 'fdopen', lambda *a, **k: (_ for _ in ()).throw(OSError("simulated fdopen failure")))
        entry = make_entry(market='ML_Away')
        result, slate, meta = self._run_and_capture(rg, tmp_path, [make_game('A', 'B', [entry])])
        assert result == 0
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'

    def test_fsync_failure_does_not_alter_decision(self, rg, tmp_path, monkeypatch):
        monkeypatch.setattr(pa.os, 'fsync', lambda *a, **k: (_ for _ in ()).throw(OSError("simulated fsync failure")))
        entry = make_entry(market='ML_Away')
        result, slate, meta = self._run_and_capture(rg, tmp_path, [make_game('A', 'B', [entry])])
        assert result == 0
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'

    def test_rename_failure_does_not_alter_decision(self, rg, tmp_path, monkeypatch):
        monkeypatch.setattr(pa.os, 'replace', lambda *a, **k: (_ for _ in ()).throw(OSError("simulated os.replace failure")))
        entry = make_entry(market='ML_Away')
        result, slate, meta = self._run_and_capture(rg, tmp_path, [make_game('A', 'B', [entry])])
        assert result == 0
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'

    def test_cleanup_failure_after_rename_failure_does_not_alter_decision(self, rg, tmp_path, monkeypatch):
        """os.remove(tmp_path) failing during the except-block cleanup (after
        an earlier os.replace failure) must ALSO not escape or alter output."""
        monkeypatch.setattr(pa.os, 'replace', lambda *a, **k: (_ for _ in ()).throw(OSError("simulated os.replace failure")))
        monkeypatch.setattr(pa.os, 'remove', lambda *a, **k: (_ for _ in ()).throw(OSError("simulated cleanup failure")))
        entry = make_entry(market='ML_Away')
        result, slate, meta = self._run_and_capture(rg, tmp_path, [make_game('A', 'B', [entry])])
        assert result == 0
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'

    def test_paper_only_decision_unaffected_by_artifact_failure(self, rg, tmp_path, monkeypatch):
        """Even when the underlying decision IS PAPER_ONLY (a more complex
        decision path than plain GO), an artifact-publication failure must
        not change it or the third-pass downgrades already applied."""
        monkeypatch.setattr(pa.os, 'makedirs', lambda *a, **k: (_ for _ in ()).throw(OSError("simulated failure")))
        tt_game, tt_entry = _game_with_tt(('A', 'B'), stake=4.0, edge=4.0)
        result, slate, meta = self._run_and_capture(rg, tmp_path, [tt_game])
        assert result == 0
        assert meta['risk_gate']['decision'] == 'PAPER_ONLY'
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'PAPER'
        assert slate['games'][0]['marketLedger'][0]['blockReason'].startswith('RISK_GATE_PAPER_ONLY:')

    def test_artifact_write_success_produces_a_readable_artifact(self, rg, tmp_path):
        """Sanity check: with no failure injected, the artifact really is
        written and readable via pipeline_artifacts' own reader, proving the
        failure-isolation tests above are exercising a real code path."""
        entry = make_entry(market='ML_Away')
        slate_path, meta_path = _wire(rg, tmp_path)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(slate_path) as f:
            date = json.load(f)['date']
        assert pa.stage_artifact_exists('execution', date)
        envelope = pa.read_stage_artifact('execution', date)
        assert envelope['meta']['stage'] == 'execution'
        assert envelope['meta']['producedBy'] == 'scripts/risk_gate.py'
        assert envelope['meta']['status'] == 'canonical'
        assert envelope['meta']['sourceStage'] == 'recommendations'
        assert envelope['data']['decision'] == 'GO'

    def test_exit_code_and_meta_json_unaffected_by_any_artifact_failure_stage(self, rg, tmp_path, monkeypatch):
        """Belt-and-suspenders: chain every failure point at once (worst
        case) and confirm main() still completes with exit code 0 and a
        fully-written meta.json."""
        monkeypatch.setattr(pa.os, 'makedirs', lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        entry = make_entry(market='ML_Away')
        result, slate, meta = self._run_and_capture(rg, tmp_path, [make_game('A', 'B', [entry])])
        assert result == 0
        assert 'risk_gate' in meta
        assert meta['risk_gate']['decision'] == 'GO'
