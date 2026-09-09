#!/usr/bin/env python3
"""
tests/test_wave0_08_deterministic_run_ids.py
============================================
WAVE 0.08, Mission B. Guards the write-once contract of
`lib.edgelab.ids.new_run_id`.

THE DEFECT
----------
`new_run_id` interpolated a second-resolution UTC timestamp into the id even
when the caller supplied BOTH a github_run_id and a deterministic
content_signature -- the exact combination that is supposed to re-derive one
stable id for identical work. Two invocations processing identical inputs
therefore produced different ids whenever they straddled a UTC second boundary,
so a genuine retry manufactured the duplicate manifest that content_signature
exists to prevent.

That is why
`tests/edgelab/test_standalone_full_universe_evaluation.py::
TestStandaloneFullUniverseEvaluation::test_identical_rerun_is_idempotent_no_op`
failed intermittently under a loaded full-suite run (it calls run() twice and
asserts the two runIds match) while passing every time in isolation.

NOTE ON METHOD
--------------
None of these tests freeze, mock or patch the clock. Determinism is asserted
across REAL elapsed wall-clock time, including a stress loop that deliberately
crosses at least one true second boundary. A clock mock would have made the old
implementation pass while leaving production broken.
"""

import os
import re
import sys
import time

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.edgelab import ids  # noqa: E402

SIG = ids.build_run_content_signature("wave0_08", "snapshot-a.json")
OTHER_SIG = ids.build_run_content_signature("wave0_08", "snapshot-b.json")


def _det(**kw):
    base = {"github_run_id": "999000111", "content_signature": SIG}
    base.update(kw)
    return ids.new_run_id("STANDALONE_FULL_UNIVERSE_EVALUATION", **base)


# ── 1 & 2: identical semantic input is stable across real time ───────────────

def test_1_same_semantic_input_across_a_real_second_boundary_is_identical():
    """
    Crosses a genuine UTC second boundary between the two calls. Under the old
    implementation this assertion failed deterministically; it now passes
    because the id contains no clock at all.
    """
    first = _det()
    start = time.time()
    # Wait until the UTC second actually ticks over, then mint again.
    while int(time.time()) == int(start):
        time.sleep(0.01)
    second = _det()
    assert first == second
    assert int(time.time()) != int(start), "the test must really cross a second"


def test_2_same_semantic_input_far_apart_in_time_is_identical():
    """
    Simulates "minutes apart" without sleeping for minutes: the id is a pure
    function of its inputs, so equality cannot depend on elapsed time at all.
    Proven structurally -- no wall-clock component appears in the output.
    """
    first = _det()
    time.sleep(1.1)
    second = _det()
    assert first == second
    # And the strong form: no YYYYMMDDTHHMMSSZ stamp anywhere in the id.
    assert not re.search(r"\d{8}T\d{6}Z", first), (
        "a deterministic run id must contain no wall-clock timestamp; got %r" % first)


def test_2b_repeated_minting_over_a_stress_loop_never_varies():
    """The blunt version: mint many ids over real elapsed time; all identical."""
    minted = set()
    deadline = time.time() + 2.5
    while time.time() < deadline:
        minted.add(_det())
    assert len(minted) == 1, "ids drifted over elapsed time: %r" % sorted(minted)


# ── 3 & 4: things that SHOULD differ still differ ────────────────────────────

def test_3_different_content_signatures_produce_different_ids():
    assert _det() != _det(content_signature=OTHER_SIG)


def test_4_run_attempt_is_part_of_identity():
    """A manual re-run of the same workflow run is a distinct run."""
    a1 = _det(github_run_attempt="1")
    a2 = _det(github_run_attempt="2")
    none_attempt = _det()
    assert a1 != a2
    assert a1 != none_attempt
    # ...but each is still internally stable.
    assert a1 == _det(github_run_attempt="1")


def test_4b_different_run_type_or_run_id_produce_different_ids():
    assert _det() != ids.new_run_id("SETTLEMENT", github_run_id="999000111",
                                    content_signature=SIG)
    assert _det() != _det(github_run_id="222")


# ── 5: uniqueness is preserved where semantics are unavailable ───────────────

def test_5_missing_content_signature_still_avoids_same_run_collisions():
    """
    The regression the content_signature mechanism was originally added for: two
    invocations of the same run_type inside ONE GitHub run, with no semantic
    identity to offer, must not collide even within a single wall-clock second.
    """
    minted = {ids.new_run_id("MARKET_OBSERVATION_INGEST", github_run_id="999000111")
              for _ in range(200)}
    assert len(minted) == 200, "same-run ids collided without a content signature"


def test_5b_local_runs_are_unaffected():
    """No github_run_id: unchanged ULID-style timestamp + random suffix."""
    local = ids.new_run_id("PROSPECTIVE_SNAPSHOT")
    assert re.match(r"^PROSPECTIVE_SNAPSHOT_\d{8}T\d{6}Z_[0-9a-f]{8}$", local), local
    assert local != ids.new_run_id("PROSPECTIVE_SNAPSHOT")


def test_5c_the_non_deterministic_paths_still_carry_a_timestamp():
    """
    The timestamp is not removed globally -- only from the deterministic path,
    where it was actively harmful. Where uniqueness rather than reproducibility
    is the goal it still carries real information.
    """
    no_sig = ids.new_run_id("CLV_COLLECTION", github_run_id="999000111")
    assert re.search(r"\d{8}T\d{6}Z", no_sig), no_sig


# ── 6 & 7: neighbouring contracts and historical data ────────────────────────

def test_6_other_write_once_id_contracts_are_unaffected():
    """
    build_snapshot_id / build_replay_run_id / scored-replay ids are pure hashes
    and must stay byte-stable; this change must not have touched them.
    """
    for name in ("build_snapshot_id", "build_replay_run_id",
                 "build_scored_replay_result_id", "build_run_content_signature"):
        assert hasattr(ids, name), "%s disappeared" % name

    assert ids.build_run_content_signature("a", "b") == \
        ids.build_run_content_signature("a", "b")
    assert ids.build_run_content_signature("a", "b") != \
        ids.build_run_content_signature("a", "c")
    assert ids.build_scored_replay_result_id("r1", "v1") == \
        ids.build_scored_replay_result_id("r1", "v1")


def test_7_historical_production_run_ids_remain_readable():
    """
    No migration is required, and this asserts why: run ids are compared for
    equality only. A historical timestamped id and a new deterministic id
    coexist in the same corpus without either becoming unreadable.
    """
    historical = "BET_LEDGER_INGEST_20260909T115719Z_gh34348115802_5861f00b"
    current = _det()
    for value in (historical, current):
        assert isinstance(value, str) and value
        assert " " not in value
        assert value.split("_")[0].isupper()
    assert historical != current


def test_7b_no_production_code_parses_a_run_id():
    """
    Guards the assumption test_7 rests on. If a future change starts parsing a
    timestamp out of a run id, this fails and the no-migration claim must be
    revisited rather than silently becoming false.
    """
    import subprocess
    proc = subprocess.run(
        ["grep", "-rnE", r"runId.*(strptime|\\d\{8\}T|split\(\"_\"\)|split\('_'\))",
         os.path.join(ROOT, "lib"), os.path.join(ROOT, "scripts"),
         "--include=*.py"],
        capture_output=True, text=True, timeout=60)
    assert proc.stdout.strip() == "", (
        "something now parses a run id; the 'no migration needed' guarantee in "
        "new_run_id's docstring must be re-checked:\n%s" % proc.stdout)
