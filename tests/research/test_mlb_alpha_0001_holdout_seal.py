"""MLB-ALPHA-0001 blind-holdout gate + spent-result immutability.

The holdout was authorized by the CEO on 2026-09-01, scored EXACTLY ONCE,
and is permanently SPENT. These tests assert the post-authorization
invariants and, critically, that NO test in this file can create, modify
or overwrite the canonical result.

TWO PROCESS DEFECTS THIS FILE NOW GUARDS AGAINST
------------------------------------------------
1. HOLDOUT_SCORING_TRIGGER_INCIDENT -- an earlier version called
   `scorer.main()` to prove the sealed scorer exited non-zero. Once the
   authorization file existed that call became DESTRUCTIVE: it performed
   the one-time scoring run and wrote the real artifact. Every test here
   now runs against an INJECTED tmp_path artifact root; none may touch the
   canonical location. An autouse fixture fails the test if the canonical
   result's bytes change for any reason.
2. CI IMPORT COUPLING -- the scorer imported numpy at module scope, so the
   pure-stdlib gate could not even be imported in CI (requirements-ci.txt
   installs duckdb + PyYAML only). The scientific stack now lives inside
   the scoring functions; a test below pins that.
"""

import hashlib
import importlib.util
import json
import os

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
SCORER = os.path.join(REPO, "scripts", "research", "mlb_alpha_0001", "score_holdout.py")
CANONICAL_RESULT = os.path.join(ART, "holdout_result.json")
CANONICAL_AUTH = os.path.join(ART, "HOLDOUT_AUTHORIZATION.json")


def _load():
    spec = importlib.util.spec_from_file_location("mlb_alpha_holdout_scorer", SCORER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _digest(path):
    if not os.path.exists(path):
        return None
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture(autouse=True)
def canonical_spent_result_is_immutable():
    """THE invariant: nothing in this file may alter the spent holdout."""
    before_result = _digest(CANONICAL_RESULT)
    before_auth = _digest(CANONICAL_AUTH)
    yield
    assert _digest(CANONICAL_RESULT) == before_result, \
        "a test modified the canonical spent holdout result"
    assert _digest(CANONICAL_AUTH) == before_auth, \
        "a test modified the canonical holdout authorization"


# --------------------------------------------------------------------------
# The gate itself -- exercised ONLY against injected, isolated paths.
# --------------------------------------------------------------------------

def test_gate_refuses_when_authorization_is_absent(tmp_path):
    mod = _load()
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse(auth_path=str(tmp_path / "absent.json"))


def test_gate_refuses_authorized_false(tmp_path):
    mod = _load()
    protocol = mod.load_protocol()
    f = tmp_path / "auth.json"
    f.write_text(json.dumps({"authorized": False,
                             "candidateRuleSha256": protocol["candidateRuleSha256"]}))
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse(auth_path=str(f))


def test_gate_refuses_a_non_matching_rule_hash(tmp_path):
    mod = _load()
    f = tmp_path / "auth.json"
    f.write_text(json.dumps({"authorized": True, "candidateRuleSha256": "0" * 64}))
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse(auth_path=str(f))


def test_gate_refuses_unreadable_authorization(tmp_path):
    mod = _load()
    f = tmp_path / "auth.json"
    f.write_text("{not json")
    with pytest.raises(mod.HoldoutSealed):
        mod.authorize_or_refuse(auth_path=str(f))


def test_gate_accepts_only_a_fully_matching_authorization(tmp_path):
    mod = _load()
    protocol = mod.load_protocol()
    f = tmp_path / "auth.json"
    f.write_text(json.dumps({"authorized": True,
                             "candidateRuleSha256": protocol["candidateRuleSha256"]}))
    assert mod.authorize_or_refuse(auth_path=str(f))["authorized"] is True


def test_spent_check_refuses_before_any_scoring(tmp_path):
    """A pre-existing result short-circuits main() with exit 3 -- and does so
    WITHOUT running the scoring pass. Injected root, so the canonical
    artifact is never read or written."""
    mod = _load()
    protocol = mod.load_protocol()
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"authorized": True,
                                "candidateRuleSha256": protocol["candidateRuleSha256"]}))
    (tmp_path / "holdout_result.json").write_text('{"already": "spent"}')

    def _explode(_protocol):
        raise AssertionError("scoring must not run when a result already exists")

    mod.score = _explode
    assert mod.main(art_root=str(tmp_path), auth_path=str(auth)) == 3


def test_unauthorized_main_never_scores(tmp_path):
    mod = _load()

    def _explode(_protocol):
        raise AssertionError("scoring must not run without authorization")

    mod.score = _explode
    assert mod.main(art_root=str(tmp_path),
                    auth_path=str(tmp_path / "absent.json")) == 2


# --------------------------------------------------------------------------
# Import hygiene -- the gate must work in CI, which installs no numpy.
# --------------------------------------------------------------------------

def test_gate_layer_imports_without_the_scientific_stack():
    """requirements-ci.txt installs duckdb + PyYAML only, so importing this
    module must not require numpy; the scoring stack stays behind the gate.

    Parsed with ast rather than substring-matched, so a comment mentioning
    numpy cannot trip it and a real import cannot hide from it."""
    import ast
    tree = ast.parse(open(SCORER).read())
    module_level = []
    for node in tree.body:                      # top level only
        if isinstance(node, ast.Import):
            module_level += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_level.append(node.module.split(".")[0])
    heavy = {"numpy", "pandas", "scipy", "duckdb"}
    assert not (set(module_level) & heavy), \
        "module-scope import of %s breaks the stdlib-only gate in CI" % (
            sorted(set(module_level) & heavy),)


def test_scoring_functions_do_import_numpy_locally():
    """The counterpart: the heavy stack must still be imported where it is
    actually used, so the fix is a relocation and not a deletion."""
    import ast
    tree = ast.parse(open(SCORER).read())
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    inner = []
    for name in ("score", "_summarize"):
        for node in ast.walk(fns[name]):
            if isinstance(node, ast.Import):
                inner += [a.name.split(".")[0] for a in node.names]
    assert "numpy" in inner


def test_requirements_ci_does_not_need_numpy_for_the_gate():
    reqs = open(os.path.join(REPO, "requirements-ci.txt")).read().lower()
    assert "numpy" not in reqs, \
        "numpy was added to CI deps; the gate should not need it"


# --------------------------------------------------------------------------
# Frozen identities and the spent record.
# --------------------------------------------------------------------------

def test_holdout_is_spent_and_scored_exactly_once():
    result = json.load(open(CANONICAL_RESULT))
    assert result["scoredOnce"] is True
    assert result["holdoutStatus"] == "SPENT"
    assert result["verdict"] == "INCONCLUSIVE"


def test_result_matches_the_frozen_identities():
    result = json.load(open(CANONICAL_RESULT))
    protocol = json.load(open(os.path.join(ART, "frozen_holdout_protocol.json")))
    assert result["candidateRuleSha256"] == protocol["candidateRuleSha256"]
    assert result["protocolSha256"] == protocol["protocolSha256"]
    assert result["holdoutDates"] == protocol["holdoutDates"]


def test_frozen_protocol_matches_the_frozen_candidate_rule_hash():
    protocol = json.load(open(os.path.join(ART, "frozen_holdout_protocol.json")))
    candidate = json.load(open(os.path.join(ART, "frozen_candidate_c01_pit.json")))["candidate"]
    assert protocol["candidateRuleSha256"] == candidate["ruleSha256"]
    assert candidate["ruleSha256"] == (
        "882f16d8330af1af12aec928a561302bfe81de6a5e5716a3a7fa352bc048376b")


def test_holdout_dates_match_the_frozen_split_and_stay_spent():
    protocol = json.load(open(os.path.join(ART, "frozen_holdout_protocol.json")))
    splits = json.load(open(os.path.join(ART, "frozen_splits.json")))
    assert protocol["holdoutDates"] == splits["blindHoldout"]["dates"]
    assert splits["blindHoldout"].get("status") == "SPENT"


def test_no_holdout_entry_rows_artifact_was_created():
    assert not os.path.exists(os.path.join(ART, "entry_rows_blindHoldout.jsonl.gz"))
    assert not os.path.exists(os.path.join(ART, "entry_rows_holdout.jsonl.gz"))


def test_entry_row_builder_structurally_refuses_the_holdout():
    src = open(os.path.join(REPO, "scripts", "research", "mlb_alpha_0001",
                            "build_entry_rows.py")).read()
    assert 'choices=["discovery", "validation"]' in src
