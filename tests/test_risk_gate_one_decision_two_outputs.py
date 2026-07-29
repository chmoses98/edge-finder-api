#!/usr/bin/env python3
"""
tests/test_risk_gate_one_decision_two_outputs.py
====================================================
PR #8 hardening review, Part O: proves -- via object IDENTITY (Python's
id()), not merely equal values -- that the exact same in-memory slate
and report objects power BOTH the legacy data/slate.json+data/meta.json
persistence AND the execution.json artifact, with no second call to any
decision-making function.

Value equality alone (report_a == report_b) cannot distinguish "the same
object, read twice" from "two independently-computed dicts that happen
to be equal right now" -- a real double-computation bug could still pass
a naive value-equality test if both computations are deterministic and
receive identical inputs. Object identity (`is` / `id()`) is the only
way to structurally rule this out.
"""

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from test_risk_gate_immutable import make_entry, make_tt_entry, make_game, make_slate
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


class _IdentitySpy:
    """Wraps a function, recording the id() of every positional/keyword
    argument AND the id() of the return value for every call, without
    altering behavior."""

    def __init__(self, real_fn):
        self.real_fn = real_fn
        self.calls = []

    def __call__(self, *args, **kwargs):
        result = self.real_fn(*args, **kwargs)
        self.calls.append({
            'args_ids': [id(a) for a in args],
            'kwargs_ids': {k: id(v) for k, v in kwargs.items()},
            'args': args, 'kwargs': kwargs,
            'result': result, 'result_id': id(result),
        })
        return result


def _run_main_with_spies(rg, tmp_path, games, monkeypatch):
    slate_path = str(tmp_path / 'slate.json')
    meta_path = str(tmp_path / 'meta.json')
    rg.SLATE_PATH = slate_path
    rg.META_PATH = meta_path
    with open(slate_path, 'w') as f:
        json.dump(make_slate(games), f)

    write_atomic_spy = _IdentitySpy(rg.write_json_atomic)
    monkeypatch.setattr(rg, "write_json_atomic", write_atomic_spy)

    build_payload_spy = _IdentitySpy(rg.build_execution_artifact_payload)
    monkeypatch.setattr(rg, "build_execution_artifact_payload", build_payload_spy)

    portfolio_spy = _IdentitySpy(rg.apply_portfolio_rules)
    monkeypatch.setattr(rg, "apply_portfolio_rules", portfolio_spy)

    tt_safety_spy = _IdentitySpy(rg.apply_tt_safety)
    monkeypatch.setattr(rg, "apply_tt_safety", tt_safety_spy)

    exit_code = rg.main()
    return exit_code, write_atomic_spy, build_payload_spy, portfolio_spy, tt_safety_spy


class TestObjectIdentityAcrossBothOutputs:

    def _assert_one_decision_two_outputs(self, write_spy, payload_spy, portfolio_spy, tt_spy):
        # Exactly one call to each decision-making function.
        assert len(tt_spy.calls) == 1, f"apply_tt_safety called {len(tt_spy.calls)} times, expected 1"
        assert len(portfolio_spy.calls) == 1, f"apply_portfolio_rules called {len(portfolio_spy.calls)} times, expected 1"
        assert len(payload_spy.calls) == 1, f"build_execution_artifact_payload called {len(payload_spy.calls)} times, expected 1"
        # Exactly two calls to write_json_atomic (slate.json, meta.json).
        assert len(write_spy.calls) == 2, f"write_json_atomic called {len(write_spy.calls)} times, expected 2"

        # The (decision, report) tuple apply_portfolio_rules returned is
        # the ONLY decision computation in this whole run.
        portfolio_result = portfolio_spy.calls[0]
        # apply_portfolio_rules is called positionally: (slate, now_ts=...)
        slate_passed_to_portfolio = portfolio_spy.calls[0]['args'][0]

        # The slate object passed to write_json_atomic's FIRST call
        # (slate.json write) must be the EXACT SAME object (by id())
        # apply_portfolio_rules mutated -- not a copy, not a re-read.
        slate_id_written = write_spy.calls[0]['args_ids'][0]
        assert slate_id_written == id(slate_passed_to_portfolio), (
            "the slate object written to slate.json is NOT the same "
            "object apply_portfolio_rules operated on"
        )

        # The slate object passed to build_execution_artifact_payload
        # must ALSO be that exact same object.
        slate_id_for_payload = payload_spy.calls[0]['args_ids'][0]
        assert slate_id_for_payload == id(slate_passed_to_portfolio), (
            "build_execution_artifact_payload received a DIFFERENT slate "
            "object than the one apply_portfolio_rules decided on -- "
            "possible second computation or stale snapshot"
        )

        # The `decision` and `decision_reason` values passed into
        # build_execution_artifact_payload must trace back to the EXACT
        # (decision, report) tuple apply_portfolio_rules returned --
        # decision_reason is read from that SAME report dict object
        # (report['decision_reason']), not a separately reconstructed
        # value. Strings are immutable/often interned, so id() equality
        # on them isn't a meaningful proof by itself; instead we prove it
        # via the report dict's identity and value together: the report
        # dict returned by apply_portfolio_rules (portfolio_spy's
        # result_id) must be a dict whose 'decision_reason' key VALUE
        # equals exactly what was passed to build_execution_artifact_payload,
        # and that report dict is the only report object that ever
        # existed in this call chain (never reconstructed/re-derived).
        actual_decision, actual_report = portfolio_spy.calls[0]['result']
        decision_passed = payload_spy.calls[0]['args'][1]
        decision_reason_passed = payload_spy.calls[0]['args'][2]
        assert decision_passed == actual_decision
        assert decision_reason_passed == actual_report['decision_reason']
        assert decision_reason_passed is actual_report['decision_reason']

    def test_normal_success_identity_proof(self, rg, tmp_path, monkeypatch):
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        exit_code, write_spy, payload_spy, portfolio_spy, tt_spy = _run_main_with_spies(
            rg, tmp_path, [make_game('A', 'B', [entry])], monkeypatch
        )
        assert exit_code == 0
        self._assert_one_decision_two_outputs(write_spy, payload_spy, portfolio_spy, tt_spy)

    def test_no_candidates_identity_proof(self, rg, tmp_path, monkeypatch):
        exit_code, write_spy, payload_spy, portfolio_spy, tt_spy = _run_main_with_spies(
            rg, tmp_path, [make_game('A', 'B', [])], monkeypatch
        )
        assert exit_code == 0
        self._assert_one_decision_two_outputs(write_spy, payload_spy, portfolio_spy, tt_spy)

    def test_all_rejected_identity_proof(self, rg, tmp_path, monkeypatch):
        entries = [make_entry(market='ML_Away', status='Rejected', ticker=f'R{i}') for i in range(3)]
        exit_code, write_spy, payload_spy, portfolio_spy, tt_spy = _run_main_with_spies(
            rg, tmp_path, [make_game('A', 'B', entries)], monkeypatch
        )
        assert exit_code == 0
        self._assert_one_decision_two_outputs(write_spy, payload_spy, portfolio_spy, tt_spy)

    def test_mixed_decisions_identity_proof(self, rg, tmp_path, monkeypatch):
        tt = make_tt_entry(tier='HIGH', edge=4.0, ticker='T1')
        ml1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=3.0, ticker='M1')
        ml2 = make_entry(market='ML_Home', tier='HIGH', edge=4.0, stake=3.0, ticker='M2')
        exit_code, write_spy, payload_spy, portfolio_spy, tt_spy = _run_main_with_spies(
            rg, tmp_path, [make_game('A', 'B', [tt, ml1, ml2])], monkeypatch
        )
        assert exit_code == 0
        self._assert_one_decision_two_outputs(write_spy, payload_spy, portfolio_spy, tt_spy)

    def test_duplicate_candidates_identity_proof(self, rg, tmp_path, monkeypatch):
        import copy
        e1 = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='DUP')
        e2 = copy.deepcopy(e1)
        exit_code, write_spy, payload_spy, portfolio_spy, tt_spy = _run_main_with_spies(
            rg, tmp_path, [make_game('A', 'B', [e1, e2])], monkeypatch
        )
        assert exit_code == 0
        self._assert_one_decision_two_outputs(write_spy, payload_spy, portfolio_spy, tt_spy)

    def test_paper_only_decision_identity_proof(self, rg, tmp_path, monkeypatch):
        """The PAPER_ONLY-triggering, all-TT-no-ML/F5 case, where main()'s
        third pass ALSO mutates the slate before the artifact is built --
        still exactly one decision-computation, one artifact build."""
        entry = make_tt_entry(tier='HIGH', edge=4.0, stake=4.0)
        exit_code, write_spy, payload_spy, portfolio_spy, tt_spy = _run_main_with_spies(
            rg, tmp_path, [make_game('A', 'B', [entry])], monkeypatch
        )
        assert exit_code == 0
        self._assert_one_decision_two_outputs(write_spy, payload_spy, portfolio_spy, tt_spy)
        # Confirm the payload really does reflect the third-pass mutation
        # (proving the SAME final slate state, not a pre-third-pass
        # snapshot) by checking the actual artifact content.
        with open(str(tmp_path / 'meta.json')) as f:
            meta = json.load(f)
        assert meta['risk_gate']['decision'] == 'PAPER_ONLY'
        envelope = pa.read_stage_artifact('execution', '2026-06-16')
        assert envelope['data']['candidates'][0]['rejectionReason'].startswith('RISK_GATE_PAPER_ONLY:')

    def test_artifact_publication_failure_does_not_indicate_a_second_computation(self, rg, tmp_path, monkeypatch):
        """Even when the artifact publication step fails, the decision
        computation itself must still have happened exactly once --
        proven by the same call-count assertions holding even under
        failure."""
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        slate_path = str(tmp_path / 'slate.json')
        meta_path = str(tmp_path / 'meta.json')
        rg.SLATE_PATH = slate_path
        rg.META_PATH = meta_path
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        portfolio_spy = _IdentitySpy(rg.apply_portfolio_rules)
        monkeypatch.setattr(rg, "apply_portfolio_rules", portfolio_spy)
        tt_spy = _IdentitySpy(rg.apply_tt_safety)
        monkeypatch.setattr(rg, "apply_tt_safety", tt_spy)

        def _boom(*a, **kw):
            raise RuntimeError("simulated artifact publication failure")
        monkeypatch.setattr(pa, "write_stage_artifact", _boom)

        exit_code = rg.main()
        assert exit_code == 0
        assert len(tt_spy.calls) == 1
        assert len(portfolio_spy.calls) == 1

    def test_legacy_slate_write_failure_does_not_prevent_identity_check_on_prior_calls(self, rg, tmp_path, monkeypatch):
        """If the slate.json write itself fails (propagates uncaught),
        the decision was still computed exactly once before the failure
        -- proven by call counts captured up to the point of failure."""
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        slate_path = str(tmp_path / 'slate.json')
        meta_path = str(tmp_path / 'meta.json')
        rg.SLATE_PATH = slate_path
        rg.META_PATH = meta_path
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        portfolio_spy = _IdentitySpy(rg.apply_portfolio_rules)
        monkeypatch.setattr(rg, "apply_portfolio_rules", portfolio_spy)
        tt_spy = _IdentitySpy(rg.apply_tt_safety)
        monkeypatch.setattr(rg, "apply_tt_safety", tt_spy)

        import atomic_json
        def _boom(*a, **kw):
            raise OSError("simulated slate.json write failure")
        monkeypatch.setattr(atomic_json.os, 'replace', _boom)

        with pytest.raises(OSError):
            rg.main()

        # Decision was computed exactly once, even though persistence failed.
        assert len(tt_spy.calls) == 1
        assert len(portfolio_spy.calls) == 1
        assert not os.path.exists(meta_path)  # meta.json never reached


class TestExecutionArtifactFailureIsolationExtended:
    """PR #8 hardening review, Part P's two remaining specific gaps not
    already covered by tests/test_risk_gate_execution_artifact.py's 14
    Part-12 failure-isolation tests: warning-message content safety, and
    malformed-pre-existing-artifact rerun behavior."""

    def test_warning_message_does_not_leak_payload_content_on_serialization_failure(self, rg, tmp_path, monkeypatch, capsys):
        """A serialization failure's exception message must describe the
        FAILURE (e.g. a type name), never dump the actual candidate data
        (tickers, stakes, prices) into stdout."""
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, ticker='SECRET_TICKER_XYZ')
        slate_path = str(tmp_path / 'slate.json')
        meta_path = str(tmp_path / 'meta.json')
        rg.SLATE_PATH = slate_path
        rg.META_PATH = meta_path
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        def _fail_with_generic_message(*a, **kw):
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(pa, "write_stage_artifact", _fail_with_generic_message)

        exit_code = rg.main()
        assert exit_code == 0
        captured = capsys.readouterr()
        warning_lines = [l for l in captured.out.splitlines() if 'could not write execution pipeline artifact' in l]
        assert len(warning_lines) == 1
        # The warning line contains only the exception's own short message,
        # never the candidate ticker/stake/price values themselves.
        assert 'SECRET_TICKER_XYZ' not in warning_lines[0]
        assert warning_lines[0] == "WARNING: could not write execution pipeline artifact: simulated failure"

    def test_malformed_pre_existing_execution_json_is_fully_overwritten_not_merged(self, rg, tmp_path):
        """write_stage_artifact() never READS an existing artifact before
        writing -- it always serializes fresh and atomically replaces.
        A malformed pre-existing execution.json must therefore have zero
        effect on a subsequent successful run: the file is fully
        overwritten, never parsed, never merged with."""
        slate_path = str(tmp_path / 'slate.json')
        meta_path = str(tmp_path / 'meta.json')
        rg.SLATE_PATH = slate_path
        rg.META_PATH = meta_path
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        # Pre-seed a malformed (invalid JSON) execution.json at the exact
        # path write_stage_artifact() will target.
        artifact_dir = os.path.join(pa.PIPELINE_ROOT, '2026-06-16')
        os.makedirs(artifact_dir, exist_ok=True)
        artifact_path = os.path.join(artifact_dir, 'execution.json')
        with open(artifact_path, 'w') as f:
            f.write('{not valid json at all !!!')

        exit_code = rg.main()
        assert exit_code == 0

        envelope = pa.read_stage_artifact('execution', '2026-06-16')
        assert envelope['data']['decision'] == 'GO'
        assert len(envelope['data']['candidates']) == 1
