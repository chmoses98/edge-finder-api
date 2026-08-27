#!/usr/bin/env python3
"""
tests/edgelab/test_research_lab_production_safety.py
=========================================================
Research Lab Milestone 0A: structural production-safety guards, same
grep-verified-absence pattern as tests/edgelab/test_no_automatic_wagering.py.
Proves this milestone's new modules cannot mutate production behavior --
not merely that nobody happened to write such code today, but that a
future accidental addition would fail these tests loudly.
"""
import ast
import inspect
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESEARCH_LAB_MODULE_NAMES = [
    "research_lab_ids", "evidence_levels", "dispositions", "pit_provenance",
    "control_identity", "candidate_identity", "experiment_registry",
    "paired_evaluation", "experiment_report",
]
RESEARCH_LAB_FILES = [os.path.join(ROOT, "lib", "edgelab", f"{name}.py") for name in RESEARCH_LAB_MODULE_NAMES]

# Modules that own real production decision logic -- Milestone 0A must
# import NONE of these, in either direction (spec: "no production
# imports from research back into live selection logic").
FORBIDDEN_PRODUCTION_IMPORTS = [
    "scripts.build_market_ledger", "scripts.risk_gate", "scripts.settle_markets",
    "scripts.log_bet", "scripts.record_bet_from_workflow", "lib.edgelab.bets",
    "lib.edgelab.bankroll", "lib.edgelab.recommendations",
]

FORBIDDEN_PATTERNS = [
    r"place_order", r"createorder", r"submit_order", r"kalshi\.post",
    r"requests\.(post|put)\(", r"kelly", r"auto[_-]?stake", r"auto[_-]?bet",
]

PRODUCTION_FILE_WRITE_PATTERNS = [
    "data/slate.json", "data/bets.json", "BET_LOG.md", "data/pending_bets.json",
    "config/rules.json", "data/pipeline/",
]


def test_research_lab_files_exist():
    for path in RESEARCH_LAB_FILES:
        assert os.path.exists(path), path


def test_no_order_placement_or_auto_staking_language():
    for path in RESEARCH_LAB_FILES:
        with open(path) as f:
            source = f.read()
        for pattern in FORBIDDEN_PATTERNS:
            assert not re.search(pattern, source, re.IGNORECASE), f"{path}: unexpected match for {pattern!r}"


def test_no_research_lab_module_imports_production_decision_modules():
    for path in RESEARCH_LAB_FILES:
        with open(path) as f:
            source = f.read()
        for forbidden in FORBIDDEN_PRODUCTION_IMPORTS:
            assert forbidden not in source, f"{path}: unexpected reference to production module {forbidden!r}"


def test_no_research_lab_module_writes_a_production_file_path():
    """A production path may legitimately be MENTIONED in a docstring
    (e.g. pit_provenance.py's audit notes cite data/slate.json as a
    read-only source elsewhere in the repo) -- what must never appear is
    an actual write-shaped call (open(...) in write mode, os.makedirs,
    a storage helper) touching one of these paths. Checked per-line so a
    prose mention on its own line never false-positives."""
    write_indicators = ("open(", "os.makedirs(", ".write(", "write_stage_artifact(", "upsert_records(", "append_records(")
    for path in RESEARCH_LAB_FILES:
        with open(path) as f:
            lines = f.readlines()
        for lineno, line in enumerate(lines, start=1):
            if not any(indicator in line for indicator in write_indicators):
                continue
            for forbidden in PRODUCTION_FILE_WRITE_PATTERNS:
                assert forbidden not in line, f"{path}:{lineno}: write-shaped line references production path {forbidden!r}: {line.strip()!r}"


def test_research_lab_modules_only_write_under_their_own_data_edgelab_subdirectories():
    """Every persistence path this milestone writes to must live under
    data/edgelab/{control_models,candidate_variants,experiments,experiment_reports}/
    -- never a bare data/ path, never an existing EdgeLab entity directory
    this milestone doesn't own (observations/, bets/, settlements/, etc.)."""
    allowed_roots = {"control_models", "candidate_variants", "experiments", "experiment_reports"}
    for path in RESEARCH_LAB_FILES:
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                if name.endswith("_ROOT") and isinstance(node.value, ast.Call):
                    # os.path.join("data", "edgelab", "<subdir>")
                    args = node.value.args
                    if len(args) >= 3 and all(isinstance(a, ast.Constant) for a in args[:3]):
                        literal_args = [a.value for a in args[:3]]
                        assert literal_args[0] == "data" and literal_args[1] == "edgelab", f"{path}:{name} does not root under data/edgelab"
                        assert literal_args[2] in allowed_roots, f"{path}:{name} writes to unexpected subdirectory {literal_args[2]!r}"


def test_disposition_module_has_no_way_to_emit_production():
    """Direct proof (not just grep) that no function in dispositions.py
    can return PRODUCTION -- every public callable is invoked and its
    result checked."""
    from lib.edgelab import dispositions as disp
    for value in disp.AUTOMATICALLY_ASSIGNABLE_DISPOSITIONS:
        assert disp.assign_disposition(value) != disp.PRODUCTION
    try:
        disp.assign_disposition(disp.PRODUCTION)
        assert False, "assign_disposition(PRODUCTION) must raise, never return"
    except disp.ProductionDispositionForbiddenError:
        pass


def test_experiment_report_builder_signature_has_no_bypass_parameter():
    """A future edit adding a parameter like `force_disposition` or
    `allow_production` would be caught here."""
    from lib.edgelab import experiment_report as er
    sig = inspect.signature(er.build_experiment_report)
    forbidden_substrings = ("force", "override", "bypass", "allow_production", "skip_validation")
    for param_name in sig.parameters:
        lowered = param_name.lower()
        assert not any(s in lowered for s in forbidden_substrings), f"suspicious bypass-shaped parameter: {param_name}"


def test_candidate_registration_contract_structurally_forbids_production_paths():
    """validate_candidate_registration must reject ANY nonempty
    productionCodePathsModified, not merely a specific hardcoded string --
    fuzzed with several different values."""
    from lib.edgelab import candidate_identity as cand
    base = cand.build_candidate_registration(
        name="x", base_control_model_id="CTRL-x", change_description="y",
        change_type=cand.CHANGE_TYPE_OTHER, implementation_ref="NOT_YET_IMPLEMENTED",
    )
    for bad_paths in (["scripts/risk_gate.py"], ["config/rules.json"], ["a", "b"]):
        mutated = dict(base)
        mutated["productionCodePathsModified"] = bad_paths
        try:
            cand.validate_candidate_registration(mutated)
            assert False, f"expected rejection for productionCodePathsModified={bad_paths}"
        except ValueError:
            pass


def test_no_research_lab_module_imports_kalshi_order_client():
    """lib.edgelab.kalshi_fees (pure fee-math, research-safe, already
    reused throughout EdgeLab research code) is an explicit, deliberate
    exception -- it is not a Kalshi API/order client. Any OTHER
    'import kalshi*'/'from lib.kalshi' reference would be a real
    production order-client import and must never appear here."""
    for path in RESEARCH_LAB_FILES:
        with open(path) as f:
            source = f.read()
        for match in re.finditer(r"^\s*(?:from|import)\s+\S*kalshi\S*", source, re.IGNORECASE | re.MULTILINE):
            assert "kalshi_fees" in match.group(0).lower(), f"{path}: unexpected Kalshi import: {match.group(0)!r}"


def test_no_research_lab_workflow_calls_a_kalshi_order_endpoint():
    workflows_dir = os.path.join(ROOT, ".github", "workflows")
    import glob
    for path in glob.glob(os.path.join(workflows_dir, "*research*.yml")) + glob.glob(os.path.join(workflows_dir, "*experiment*.yml")):
        with open(path) as f:
            text = f.read().lower()
        for forbidden in ("/portfolio/orders", "place_order", "createorder"):
            assert forbidden not in text, f"{path}: unexpected order-placement reference"
