"""The MLB-ALPHA-0001 blind holdout must stay sealed.

These tests prove the scorer cannot execute without an explicit, matching
authorization file, and that no research artifact contains holdout
outcomes.
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


def test_authorization_file_does_not_exist():
    """The seal itself: this session must not have authorized anything."""
    assert not os.path.exists(os.path.join(ART, "HOLDOUT_AUTHORIZATION.json")), \
        "a holdout authorization file exists -- the holdout is no longer sealed"


def test_scorer_refuses_without_authorization():
    mod = _load()
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse()


def test_scorer_main_exits_nonzero_while_sealed():
    mod = _load()
    assert mod.main() == 2


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
    assert protocol["holdoutStatus"].startswith("SEALED")


def test_holdout_dates_match_the_frozen_split():
    with open(os.path.join(ART, "frozen_holdout_protocol.json")) as fh:
        protocol = json.load(fh)
    with open(os.path.join(ART, "frozen_splits.json")) as fh:
        splits = json.load(fh)
    assert protocol["holdoutDates"] == splits["blindHoldout"]["dates"]


def test_no_entry_rows_were_built_for_the_holdout():
    assert not os.path.exists(os.path.join(ART, "entry_rows_blindHoldout.jsonl.gz"))
    assert not os.path.exists(os.path.join(ART, "entry_rows_holdout.jsonl.gz"))


def test_entry_row_builder_structurally_refuses_the_holdout():
    """--split only accepts discovery/validation; blindHoldout is not a choice."""
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0001",
                            "build_entry_rows.py")).read()
    assert 'choices=["discovery", "validation"]' in src
