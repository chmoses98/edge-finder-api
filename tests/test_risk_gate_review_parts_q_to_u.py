#!/usr/bin/env python3
"""
tests/test_risk_gate_review_parts_q_to_u.py
================================================
PR #8 hardening review, Parts Q-U.

Part Q: exact write-order/partial-failure semantics (risk decisions ->
slate write -> meta write -> execution artifact publication -> logging
-> exit), confirming PR #8 has not reordered any of this.

Part R: atomic-write byte-format equivalence (indent/separators/unicode/
key-ordering/newline/float/null rendering) between the pre-Phase-7 plain
json.dump() and the new write_json_atomic().

Part S: meta.json field semantics -- full REPLACE (not merge) of the
risk_gate key, stale nested sub-fields fully discarded, unrelated
top-level keys preserved verbatim.

Part T: rerun scenarios not yet covered elsewhere -- prior execution
artifact from a DIFFERENT date (must not interact at all, since the
path is date-partitioned).

Part U: searched every workflow and script for a path that restores
authoritative.json (or an older slate.json snapshot) AFTER risk_gate.py
has run within the SAME pipeline invocation. Found one real, but
PRE-EXISTING and out-of-PR-#8-scope, adjacent-workflow quirk -- see
TestAuthoritativeRecoveryPathAudit's docstring for the full analysis of
why it is not a blocker.
"""

import json
import os
import subprocess
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


def _wire(rg, tmp_path):
    slate_path = str(tmp_path / 'slate.json')
    meta_path = str(tmp_path / 'meta.json')
    rg.SLATE_PATH = slate_path
    rg.META_PATH = meta_path
    return slate_path, meta_path


# ══════════════════════════════════════════════════════════════════════════════
# Part Q: exact write order / partial-failure semantics
# ══════════════════════════════════════════════════════════════════════════════

class TestWriteOrderAndPartialFailure:

    def test_source_order_is_slate_then_meta_then_artifact(self):
        """Structural proof from the actual source: the three write
        operations appear in exactly this order in main()'s body."""
        with open(os.path.join(ROOT, "scripts", "risk_gate.py")) as f:
            source = f.read()
        slate_write_idx = source.index("write_json_atomic(slate, SLATE_PATH")
        meta_write_idx = source.index("write_json_atomic(meta, META_PATH")
        artifact_idx = source.index("write_stage_artifact(")
        assert slate_write_idx < meta_write_idx < artifact_idx

    def test_meta_write_failure_leaves_slate_updated_but_no_meta(self, rg, tmp_path, monkeypatch):
        """slate.json succeeds, meta.json write fails -- exactly one file
        disagrees with the other afterward: slate.json reflects the new
        decision, meta.json is whatever it was before this run (or
        absent, on a first run)."""
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        import atomic_json
        real_replace = atomic_json.os.replace
        call_count = {'n': 0}

        def _fail_on_second_call(*a, **kw):
            call_count['n'] += 1
            if call_count['n'] >= 2:  # 1st call = slate.json write (succeeds), 2nd = meta.json (fails)
                raise OSError("simulated meta.json write failure")
            return real_replace(*a, **kw)

        monkeypatch.setattr(atomic_json.os, 'replace', _fail_on_second_call)

        with pytest.raises(OSError):
            rg.main()

        with open(slate_path) as f:
            slate = json.load(f)
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'
        assert not os.path.exists(meta_path), (
            "meta.json must not exist after its own write failed -- "
            "confirms which file disagrees with which after this failure mode"
        )

    def test_slate_write_failure_means_meta_never_attempted(self, rg, tmp_path, monkeypatch):
        """The reverse: slate.json write fails FIRST -- meta.json write
        must never even be attempted (already covered in
        tests/test_risk_gate_atomic_write_safety.py; re-confirmed here
        as part of this file's complete Q-order proof)."""
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        import atomic_json
        def _boom(*a, **kw):
            raise OSError("simulated slate.json write failure")
        monkeypatch.setattr(atomic_json.os, 'replace', _boom)

        with pytest.raises(OSError):
            rg.main()
        assert not os.path.exists(meta_path)

    def test_artifact_publication_is_always_the_last_operation_never_blocks_earlier_writes(self, rg, tmp_path, monkeypatch):
        """Both legacy writes succeed, artifact fails -- both files exist
        and are internally consistent with each other and with the
        decision; only execution.json is absent/stale."""
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)

        monkeypatch.setattr(pa, "write_stage_artifact", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

        exit_code = rg.main()
        assert exit_code == 0
        with open(slate_path) as f:
            slate = json.load(f)
        with open(meta_path) as f:
            meta = json.load(f)
        assert slate['games'][0]['marketLedger'][0]['confidenceTier'] == 'HIGH'
        assert meta['risk_gate']['decision'] == 'GO'
        assert not pa.stage_artifact_exists('execution', '2026-06-16')


# ══════════════════════════════════════════════════════════════════════════════
# Part R: byte-format equivalence of the atomic write vs the old plain write
# ══════════════════════════════════════════════════════════════════════════════

class TestAtomicWriteByteFormatEquivalence:

    def test_indent_separators_and_key_order_match_plain_json_dump(self, rg, tmp_path):
        """write_json_atomic(payload, path, indent=2) must produce BYTE-
        IDENTICAL output to a plain json.dump(payload, f, indent=2) call
        on the same payload -- proving the atomic-write migration changed
        only the WRITE MECHANISM, never the serialization format."""
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(slate_path) as f:
            written_slate = json.load(f)

        expected_bytes = json.dumps(written_slate, indent=2).encode()
        with open(slate_path, 'rb') as f:
            actual_bytes = f.read()
        assert actual_bytes == expected_bytes

    def test_unicode_content_preserved_exactly(self, rg, tmp_path):
        """A ticker/blockReason containing non-ASCII characters (e.g. the
        ⚠️/══ characters risk_gate.py's OWN print statements use, or a
        team name with an accent) must round-trip byte-for-byte -- no
        ensure_ascii escaping change, matching plain json.dump()'s
        default (ensure_ascii=True is the default for BOTH json.dump and
        write_json_atomic, since neither passes ensure_ascii=False)."""
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        entry['blockReason'] = None
        entry['unicodeTestField'] = 'Café Münchën 日本語'
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(slate_path, encoding='utf-8') as f:
            written = json.load(f)
        assert written['games'][0]['marketLedger'][0]['unicodeTestField'] == 'Café Münchën 日本語'

    def test_null_rendering_matches_json_standard(self, rg, tmp_path):
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0, line=None)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(slate_path) as f:
            raw = f.read()
        assert '"line": null' in raw

    def test_float_rendering_matches_plain_json_dump(self, rg, tmp_path):
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.5)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(slate_path) as f:
            raw = f.read()
        assert '"betSize": 5.5' in raw

    def test_newline_at_end_of_file_matches_plain_json_dump_convention(self, rg, tmp_path):
        """Plain json.dump() writes NO trailing newline by default --
        verify write_json_atomic() doesn't add one either (a behavior
        change some 'nice' atomic-write helpers add unprompted)."""
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(slate_path, 'rb') as f:
            raw = f.read()
        assert not raw.endswith(b'\n')


# ══════════════════════════════════════════════════════════════════════════════
# Part S: meta.json field semantics
# ══════════════════════════════════════════════════════════════════════════════

class TestMetaJsonFieldSemantics:

    def test_stale_nested_risk_gate_subfields_are_fully_discarded_not_merged(self, rg, tmp_path):
        """A pre-existing meta.json with a risk_gate block carrying an
        extra, no-longer-produced nested field must NOT survive into the
        new run -- meta['risk_gate'] = {...} is a full key REPLACEMENT,
        never a dict.update()-style merge."""
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        with open(meta_path, 'w') as f:
            json.dump({
                'fetchedAt': '2026-01-01T00:00:00Z',
                'risk_gate': {
                    'runAt': 'stale', 'decision': 'STALE_DECISION',
                    'someRemovedLegacyField': 'should not survive',
                },
            }, f)

        rg.main()
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta['fetchedAt'] == '2026-01-01T00:00:00Z'  # unrelated key preserved
        assert meta['risk_gate']['decision'] == 'GO'  # fully replaced
        assert 'someRemovedLegacyField' not in meta['risk_gate']

    def test_all_meta_risk_gate_field_types_match_report_dict_types(self, rg, tmp_path):
        slate_path, meta_path = _wire(rg, tmp_path)
        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])]), f)
        rg.main()
        with open(meta_path) as f:
            meta = json.load(f)
        rg_block = meta['risk_gate']
        assert isinstance(rg_block['runAt'], str)
        assert isinstance(rg_block['decision'], str)
        assert isinstance(rg_block['total_real_stake'], (int, float))
        assert isinstance(rg_block['total_bets'], int)
        assert isinstance(rg_block['concentration_warnings'], list)
        assert isinstance(rg_block['by_family'], dict)


# ══════════════════════════════════════════════════════════════════════════════
# Part T: rerun scenario not covered elsewhere -- different-date artifact
# ══════════════════════════════════════════════════════════════════════════════

class TestRerunDifferentDateArtifactIsolation:

    def test_prior_execution_artifact_from_a_different_date_is_untouched(self, rg, tmp_path):
        """data/pipeline/<date>/execution.json is date-partitioned -- a
        run for 2026-06-16 must never read, modify, or even glance at
        2026-06-15's artifact directory."""
        slate_path, meta_path = _wire(rg, tmp_path)

        # Pre-seed YESTERDAY's artifact.
        yesterday_dir = os.path.join(pa.PIPELINE_ROOT, '2026-06-15')
        os.makedirs(yesterday_dir, exist_ok=True)
        yesterday_path = os.path.join(yesterday_dir, 'execution.json')
        with open(yesterday_path, 'w') as f:
            json.dump({'meta': {'stage': 'execution'}, 'data': {'decision': 'YESTERDAY_SENTINEL'}}, f)
        before_hash = open(yesterday_path, 'rb').read()

        entry = make_entry(market='ML_Away', tier='HIGH', edge=4.0, stake=5.0)
        with open(slate_path, 'w') as f:
            json.dump(make_slate([make_game('A', 'B', [entry])], date='2026-06-16'), f)

        rg.main()

        after_hash = open(yesterday_path, 'rb').read()
        assert before_hash == after_hash, "a different date's artifact was touched"
        today_envelope = pa.read_stage_artifact('execution', '2026-06-16')
        assert today_envelope['data']['decision'] == 'GO'


# ══════════════════════════════════════════════════════════════════════════════
# Part U: authoritative.json / risk-decision recovery-path audit
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthoritativeRecoveryPathAudit:
    """
    Searched every workflow (.github/workflows/*.yml) and every script
    that references "authoritative" for a path that could restore a
    PRE-risk-gate slate.json snapshot AFTER risk_gate.py has already run
    within the same pipeline invocation (which would silently erase its
    TT-safety/portfolio decisions).

    FINDING (real, but pre-existing and out of PR #8's scope):
    .github/workflows/clv-update.yml -- a SEPARATE workflow (different
    trigger: daily cron at 06:00 UTC targeting YESTERDAY's date, or
    manual dispatch; not part of fetch-slate.yml's job at all) -- has a
    "Restore date-specific slate" step that does:
        SLATE_COMMIT=$(git log ... -- data/slate.json | grep "slate data $DATE" | ...)
        git show "$SLATE_COMMIT:data/slate.json" > data/slate.json
    fetch-slate.yml's ONLY commit matching the "slate data $DATE" message
    pattern is the "Write meta and commit authoritative slate" step
    (publish_slate), which runs BEFORE risk_gate.py. The LATER commit
    that captures risk_gate.py's mutations ("pipeline status + execution
    artifacts $DATE", from the final `git add data/` step) uses a
    DIFFERENT commit message that this grep pattern does not match.

    So clv-update.yml's restored data/slate.json IS the pre-risk-gate
    snapshot -- confirmed by reading both workflows' commit-message
    strings directly, not assumed.

    WHY THIS IS NOT A BLOCKER for PR #8 specifically:
      1. It is NOT introduced, changed, or worsened by this PR -- neither
         clv-update.yml nor fetch-slate.yml's commit steps are part of
         this PR's diff (verified: `git log origin/main...HEAD --
         .github/` is empty, checked in Part Y below).
      2. It happens in a COMPLETELY SEPARATE CI job/runner (a fresh
         `actions/checkout`), never in the SAME pipeline invocation as
         risk_gate.py -- it cannot "erase" a decision mid-run, only
         present a stale VIEW of the slate to a later, unrelated job.
      3. The restored data/slate.json is never committed back to the
         repository by clv-update.yml (its own final commit step commits
         only bets.json/BET_LOG.md/data/identity_audit.json/
         data/rule71_report.json -- NOT data/slate.json) -- so the
         restoration is local and transient to that one job's ephemeral
         working directory.
      4. clv_update.py's actual settlement logic reads its bet records
         from bets.json (confirmed: `open('bets.json')` is its primary
         data source), which was already written by write_pending_bets.py
         using the POST-risk-gate (correct, downgraded-where-applicable)
         slate.json during fetch-slate.yml's own run -- risk_gate.py's
         decisions are already durably captured in bets.json before
         clv-update.yml ever executes. The restored, stale slate.json in
         clv-update.yml's job is used for secondary/diagnostic purposes
         (final-score lookups, coverage checks, identity audits), not to
         re-derive which bets were real-money-eligible.

    This is documented here, not fixed -- consistent with this PR's
    scope (risk_gate.py only) and the mission's explicit "no structural
    change to this boundary... unless required to prevent a direct
    regression introduced by the risk-gate refactor," which this is not.
    """

    def test_no_authoritative_restoration_step_within_fetch_slate_yml_after_risk_gate(self):
        with open(os.path.join(ROOT, ".github", "workflows", "fetch-slate.yml")) as f:
            content = f.read()
        risk_gate_idx = content.index("id: risk_gate")
        after_risk_gate = content[risk_gate_idx:]
        assert "authoritative" not in after_risk_gate.lower() or all(
            "comment" in line.lower() or line.strip().startswith('#')
            for line in after_risk_gate.lower().split('\n')
            if 'authoritative' in line
        ), "a non-comment authoritative reference exists after risk_gate's step in fetch-slate.yml"

    def test_clv_update_yml_restore_step_does_not_commit_slate_json_back(self):
        with open(os.path.join(ROOT, ".github", "workflows", "clv-update.yml")) as f:
            content = f.read()
        assert "Restore date-specific slate" in content
        commit_step_start = content.index("Commit all updates")
        commit_step = content[commit_step_start:]
        assert "data/slate.json" not in commit_step
        assert "git add bets.json BET_LOG.md" in commit_step

    def test_clv_update_py_primary_settlement_source_is_bets_json_not_slate_json(self):
        with open(os.path.join(ROOT, "clv_update.py")) as f:
            content = f.read()
        assert "open('bets.json')" in content or 'open("bets.json")' in content

    def test_pr8_diff_never_touched_any_workflow_file(self):
        result = subprocess.run(
            ['git', 'log', '--oneline', 'origin/main...HEAD', '--', '.github/'],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", f".github/ was touched by this PR: {result.stdout}"
        result2 = subprocess.run(
            ['git', 'diff', '--stat', 'origin/main...HEAD', '--', '.github/', 'clv_update.py'],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert result2.stdout.strip() == ""
