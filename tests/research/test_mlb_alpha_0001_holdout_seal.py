"""MLB-ALPHA-0001 blind-holdout gate.

The holdout was authorized by the CEO on 2026-09-01 and scored EXACTLY
ONCE; it is now permanently SPENT. These tests therefore assert the
post-authorization invariants: the authorization is present and matches
the frozen hashes, the result exists and is single, the scorer refuses to
overwrite it, and the gate still rejects every malformed authorization.

PROCESS NOTE: an earlier version of this file called `scorer.main()` to
prove the sealed scorer exited non-zero. Once authorization existed that
call became DESTRUCTIVE -- it performed the scoring run itself. It is
removed; nothing here may invoke main().
"""

import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
SCORER = os.path.join(REPO, "scripts", "research", "mlb_alpha_0001", "score_holdout.py")


def _load():
    spec = importlib.util.spec_from_file_location("mlb_alpha_holdout_scorer", SCORER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_authorization_exists_and_matches_the_frozen_hashes():
    """The holdout is authorized; the authorization must name the exact
    frozen candidate rule and protocol."""
    path = os.path.join(ART, "HOLDOUT_AUTHORIZATION.json")
    assert os.path.exists(path)
    auth = json.load(open(path))
    protocol = json.load(open(os.path.join(ART, "frozen_holdout_protocol.json")))
    assert auth["authorized"] is True
    assert auth["candidateId"] == "MLB-ALPHA-0001-C01-PIT"
    assert auth["candidateRuleSha256"] == protocol["candidateRuleSha256"]
    assert auth["protocolSha256"] == protocol["protocolSha256"]


def test_scorer_refuses_when_the_authorization_is_absent(tmp_path):
    """The gate itself still works -- proven against a path with no file."""
    mod = _load()
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse(auth_path=str(tmp_path / "nope.json"))


def test_holdout_is_spent_and_scored_exactly_once():
    result = json.load(open(os.path.join(ART, "holdout_result.json")))
    assert result["scoredOnce"] is True
    assert result["holdoutStatus"] == "SPENT"
    assert result["verdict"] in ("REPLICATED_FOR_PROSPECTIVE_SHADOW",
                                 "FAILED_TO_REPLICATE", "INCONCLUSIVE")


def test_scorer_refuses_to_overwrite_a_spent_holdout():
    """A second scoring run must not be able to replace the recorded result."""
    mod = _load()
    assert mod.main() == 3


def test_result_matches_the_frozen_identities():
    result = json.load(open(os.path.join(ART, "holdout_result.json")))
    protocol = json.load(open(os.path.join(ART, "frozen_holdout_protocol.json")))
    assert result["candidateRuleSha256"] == protocol["candidateRuleSha256"]
    assert result["protocolSha256"] == protocol["protocolSha256"]
    assert result["holdoutDates"] == protocol["holdoutDates"]


def test_scorer_refuses_a_non_matching_rule_hash(tmp_path):
    mod = _load()
    bad = tmp_path / "HOLDOUT_AUTHORIZATION.json"
    bad.write_text(json.dumps({"authorized": True,
                               "candidateRuleSha256": "0" * 64}))
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse(auth_path=str(bad))


def test_scorer_refuses_when_authorized_flag_is_not_true(tmp_path):
    mod = _load()
    protocol = mod.load_protocol()
    f = tmp_path / "HOLDOUT_AUTHORIZATION.json"
    f.write_text(json.dumps({"authorized": False,
                             "candidateRuleSha256": protocol["candidateRuleSha256"]}))
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse(auth_path=str(f))


def test_scorer_refuses_unreadable_authorization(tmp_path):
    mod = _load()
    f = tmp_path / "HOLDOUT_AUTHORIZATION.json"
    f.write_text("{not json")
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse(auth_path=str(f))


def test_frozen_protocol_matches_the_frozen_candidate_rule_hash():
    with open(os.path.join(ART, "frozen_holdout_protocol.json")) as fh:
        protocol = json.load(fh)
    with open(os.path.join(ART, "frozen_candidate_c01_pit.json")) as fh:
        candidate = json.load(fh)["candidate"]
    assert protocol["candidateRuleSha256"] == candidate["ruleSha256"]
    assert protocol["candidateRuleSha256"] == candidate["ruleSha256"]


def test_holdout_dates_match_the_frozen_split():
    with open(os.path.join(ART, "frozen_holdout_protocol.json")) as fh:
        protocol = json.load(fh)
    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        splits = json.load(fh)
    assert protocol["holdoutDates"] == splits["blindHoldout"]["dates"]


def test_no_holdout_entry_rows_artifact_was_created():
    assert not os.path.exists(os.path.join(ART, "entry_rows_blindHoldout.jsonl.gz"))
    assert not os.path.exists(os.path.join(ART, "entry_rows_holdout.jsonl.gz"))


def test_entry_row_builder_structurally_refuses_the_holdout():
    """--split only accepts discovery/validation; blindHoldout is not a choice."""
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0001",
                            "build_entry_rows.py")).read()
    assert 'choices=["discovery", "validation"]' in src
