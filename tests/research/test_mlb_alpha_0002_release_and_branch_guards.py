#!/usr/bin/env python3
"""
tests/research/test_mlb_alpha_0002_release_and_branch_guards.py
====================================================================
Guards for three MLB-ALPHA-0002 activation invariants that are easy to
regress silently because they only ever matter on a real GitHub runner:

  F. DETERMINISTIC RELEASE ARCHIVES. The publish workflow must build
     archives with normalized order/time/ownership/format and normalized
     gzip metadata, and must FAIL on any archive hash mismatch. The
     original workflow used a bare `tar -cf` and had explicitly downgraded
     its hash check to "informational" -- an integrity control that can
     never fail is not a control.

  G. CLEAN RELEASE TAG TARGET. The raw discovery branch carries the
     500+ MB payload in its history. The Release tag must target a clean
     main commit, and the workflow must refuse to publish if the discovery
     head is an ancestor of the tag target (which would make every large
     blob permanently reachable from a ref).

  H. PROSPECTIVE DATA BRANCH. Scheduled and ordinary manual capture must
     land on research/mlb-alpha-0002-prospective, never on the heavy raw
     discovery branch, and never on main/master/the default branch.
"""
import os
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

WF = os.path.join(REPO, ".github", "workflows")
PUBLISH = os.path.join(WF, "research-publish-raw-dataset.yml")
CAPTURE = os.path.join(WF, "research-mlb-alpha-0002-capture.yml")
BUILDER = os.path.join(REPO, "scripts", "research", "mlb_alpha_0002", "build_release_archives.py")
MANIFEST = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002",
                        "raw_data_manifest.json")

RAW_DISCOVERY_BRANCH = "claude/mlb-alpha-0002-signal-discovery"
PROSPECTIVE_BRANCH = "research/mlb-alpha-0002-prospective"


def _text(path):
    with open(path) as fh:
        return fh.read()


def _yaml(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def _triggers(wf):
    """YAML 1.1 parses a bare `on:` key as the boolean True, so a workflow's
    trigger block lands under True rather than "on" -- read both."""
    return wf.get("on") or wf.get(True) or {}


def _dispatch_input(wf, name):
    return _triggers(wf)["workflow_dispatch"]["inputs"][name]


# --------------------------------------------------------------------------
# Part F -- deterministic archives
# --------------------------------------------------------------------------

def test_archive_builder_writes_every_header_field_explicitly():
    """GNU tar with reproducibility flags is deterministic on ONE machine but
    not across machines (publication run 33693701849 proved it: same 58
    verified-identical files, different .tar hashes). So every field is now
    written explicitly rather than delegated to whatever tar is installed."""
    src = _text(BUILDER)
    for token in ("FIXED_MTIME = 0", "info.mtime = FIXED_MTIME", "info.uid = 0",
                  "info.gid = 0", 'info.uname = ""', 'info.gname = ""',
                  "info.mode = FIXED_MODE", "tarfile.REGTYPE",
                  "format=tarfile.GNU_FORMAT"):
        assert token in src, token
    # Members sorted byte-wise, so ordering is locale-independent.
    assert "found.sort(key=lambda t: t[0])" in src
    # gzip header must carry no filename and no timestamp.
    assert 'filename=""' in src and "mtime=0" in src


def test_archive_builder_emits_no_directory_entries():
    """Directory entries carry their own mode/mtime and are the least
    portable part of a tar; tarfile.extractall creates parents implicitly,
    which is exactly how hydrate_raw_dataset.py extracts."""
    src = _text(BUILDER)
    assert "NO directory entries" in src
    assert "tarfile.DIRTYPE" not in src


def test_publish_workflow_builds_via_the_deterministic_builder_not_a_bare_tar():
    src = _text(PUBLISH)
    assert "build_release_archives.py" in src
    # Neither an ad-hoc tar nor the superseded shell builder may reappear.
    assert "tar -cf dist/" not in src
    assert "build_release_archives.sh" not in src


def test_archive_hash_mismatch_is_fatal_not_informational():
    src = _text(PUBLISH)
    assert "refusing to publish" in src
    # The exact language the old workflow used to excuse a mismatch.
    assert "A difference is informational" not in src
    assert "archive-level mismatches:" not in src


def test_manifest_carries_frozen_deterministic_archive_hashes():
    import json
    man = json.load(open(MANIFEST))
    archives = man.get("archives") or []
    assert archives, "no frozen archives[] hashes to verify against"
    for a in archives:
        assert a.get("sha256Deterministic") is True, a.get("asset")
        assert len(a.get("sha256") or "") == 64, a.get("asset")
    build = man.get("archiveBuild") or {}
    assert build.get("determinism") == "BYTE_DETERMINISTIC_CROSS_ENVIRONMENT"
    assert "build_release_archives.py" in (build.get("builder") or "")


def test_publish_refuses_when_the_manifest_has_no_frozen_hashes():
    """Fail-closed: an empty archives[] must abort, never publish
    unverifiable assets."""
    assert "refusing to publish unverifiable assets" in _text(PUBLISH)


def test_per_file_hash_verification_still_required():
    src = _text(PUBLISH)
    assert "per-file SHA256 verification FAILED" in src


# --------------------------------------------------------------------------
# Part G -- clean release tag target
# --------------------------------------------------------------------------

def test_publish_checks_out_the_clean_tag_target_not_the_data_branch():
    wf = _yaml(PUBLISH)
    steps = wf["jobs"]["publish"]["steps"]
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    ref = str(checkout.get("with", {}).get("ref", ""))
    assert "tag_target_ref" in ref, ref
    assert "data_branch" not in ref, "the primary checkout must never be the heavy data branch"


def test_tag_target_must_be_contained_in_main():
    src = _text(PUBLISH)
    assert "git merge-base --is-ancestor" in src
    assert "refusing to tag off-main" in src


def test_publish_refuses_if_the_discovery_head_is_an_ancestor_of_the_tag_target():
    src = _text(PUBLISH)
    assert "IS an ancestor of tag target" in src
    assert "tagging would preserve the raw Git history" in src


def test_release_is_created_against_an_explicit_target_sha():
    src = _text(PUBLISH)
    assert "--target" in src, "gh release create must pin the tag target explicitly"


def test_publish_verifies_the_invariant_again_after_publishing():
    src = _text(PUBLISH)
    assert "Post-publish verification" in src
    assert "published tag descends from the raw discovery branch" in src
    assert "published tag target is not contained in main" in src


def test_payload_must_not_be_present_in_the_tag_targets_tree():
    assert "the payload leaked into main" in _text(PUBLISH)


def test_data_branch_is_documented_as_read_only_build_input():
    wf = _yaml(PUBLISH)
    desc = _dispatch_input(wf, "data_branch")["description"]
    assert "READ-ONLY" in desc.upper()


# --------------------------------------------------------------------------
# Part H -- prospective data branch
# --------------------------------------------------------------------------

def test_capture_defaults_to_the_prospective_branch():
    wf = _yaml(CAPTURE)
    default = _dispatch_input(wf, "branch")["default"]
    assert default == PROSPECTIVE_BRANCH
    assert default != RAW_DISCOVERY_BRANCH


def test_capture_refuses_the_raw_discovery_branch_even_if_explicitly_passed():
    src = _text(CAPTURE)
    assert RAW_DISCOVERY_BRANCH in src
    assert "Refusing to write prospective capture to the raw discovery branch" in src


def test_capture_still_refuses_main_master_and_the_default_branch():
    src = _text(CAPTURE)
    assert 'Refusing to write to protected branch' in src
    assert '"$B" = "main"' in src
    assert '"$B" = "master"' in src
    assert '"$B" = "$DEFAULT_BRANCH"' in src


def test_scheduled_capture_falls_back_to_the_prospective_branch():
    """A `schedule` trigger passes no inputs, so the shell fallback matters
    as much as the declared input default."""
    assert 'B="${INPUT_BRANCH:-%s}"' % PROSPECTIVE_BRANCH in _text(CAPTURE)


def test_no_workflow_writes_prospective_capture_to_the_discovery_branch():
    """The discovery branch may appear only as read-only Release input or
    inside an explicit refusal."""
    for name in os.listdir(WF):
        path = os.path.join(WF, name)
        src = _text(path)
        if RAW_DISCOVERY_BRANCH not in src:
            continue
        assert name in ("research-publish-raw-dataset.yml",
                        "research-mlb-alpha-0002-capture.yml",
                        "research-kalshi-history-recovery.yml"), name


# --------------------------------------------------------------------------
# Capture persistence -- a run that collects data and drops it is a FAILURE
# --------------------------------------------------------------------------

def test_capture_commit_step_does_not_swallow_errors():
    """Run 33693708429 collected 7,081 quotes / 400 books / 4,076 trades and
    discarded all of it because the research branch did not exist; the
    commit aborted correctly but the trailing `|| echo` turned that into a
    green run. Silence is not success.

    Checks executable lines only -- the workflow deliberately quotes the old
    pattern in a comment explaining why it was removed."""
    executable = [
        line for line in _text(CAPTURE).splitlines()
        if not line.lstrip().startswith("#")
    ]
    for line in executable:
        assert "|| echo" not in line, line.strip()


def test_capture_verifies_rows_actually_reached_the_research_branch():
    src = _text(CAPTURE)
    assert "Verify the rows reached the research branch" in src
    assert "no capture partitions present on" in src
    assert "uncommitted capture rows remain in the working tree" in src


def test_capture_distinguishes_an_empty_run_from_a_broken_one():
    """A genuinely empty capture must still pass; only a failure to PERSIST
    produced rows may fail the job."""
    src = _text(CAPTURE)
    assert "this is not an error" in src


# --------------------------------------------------------------------------
# Order-book depth health -- a stored null is NOT a captured book
# --------------------------------------------------------------------------

CAPTURE_SCRIPT = os.path.join(REPO, "scripts", "research", "mlb_alpha_0002",
                              "prospective_capture.py")


def test_capture_reports_orderbook_health_explicitly():
    """Run 33695085423 stored 400/400 rows with orderbook=null while
    reporting 0 HTTP errors. Row count alone therefore cannot be trusted as
    evidence that depth was captured -- the manifest must say so directly."""
    src = _text(CAPTURE_SCRIPT)
    assert '"orderbookHealth"' in src
    assert '"allNull"' in src
    assert "booksNonEmpty" in src and "booksNullOrEmpty" in src


def test_capture_records_the_response_shape_when_depth_is_missing():
    """Kalshi is unreachable from the analysis environment, so the run
    manifest itself has to carry enough shape information to identify why a
    book came back empty."""
    src = _text(CAPTURE_SCRIPT)
    assert "orderbookShapeDiagnostic" in src
    assert "responseTopLevelKeys" in src


def test_orderbook_diagnostic_records_keys_not_order_contents():
    """Diagnostics must not start logging book contents wholesale."""
    src = _text(CAPTURE_SCRIPT)
    assert "Payload KEYS only -- never order contents" in src


def test_capture_reads_the_fixed_point_orderbook_key():
    """Measured: the endpoint's only top-level key is now `orderbook_fp`.
    Reading the legacy `orderbook` key produced 400/400 null books while
    reporting zero HTTP errors."""
    src = _text(CAPTURE_SCRIPT)
    assert 'd.get("orderbook_fp")' in src
    assert "orderbookSourceKey" in src, "the price unit's provenance must be stored with the book"
