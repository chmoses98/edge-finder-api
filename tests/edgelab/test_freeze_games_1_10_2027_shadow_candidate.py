#!/usr/bin/env python3
"""
tests/edgelab/test_freeze_games_1_10_2027_shadow_candidate.py
=========================================================
Coverage for scripts/edgelab/freeze_games_1_10_2027_shadow_candidate.py --
the durable RESEARCH-ONLY 2027 shadow-candidate freeze artifact.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for p in (_ROOT, os.path.join(_ROOT, "scripts"), os.path.join(_ROOT, "scripts", "edgelab")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

import freeze_games_1_10_2027_shadow_candidate as freeze_mod  # noqa: E402
from lib.edgelab import candidate_identity as cand_id


class TestFreezeIsWriteOnceAndReproducible:
    def test_build_and_freeze_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cand_id, "CANDIDATE_VARIANTS_ROOT", str(tmp_path / "candidate_variants"))
        reg1, path1 = freeze_mod.build_and_freeze()
        reg2, path2 = freeze_mod.build_and_freeze()
        assert reg1 == reg2
        assert path1 == path2

    def test_uses_fixed_timestamp_not_current_time(self):
        assert freeze_mod.FREEZE_TIMESTAMP == "2026-08-28T16:00:00Z"


class TestProductionIsolationEnforced:
    def test_production_code_paths_modified_is_always_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cand_id, "CANDIDATE_VARIANTS_ROOT", str(tmp_path / "candidate_variants"))
        reg, _ = freeze_mod.build_and_freeze()
        assert reg["productionCodePathsModified"] == []

    def test_production_active_is_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cand_id, "CANDIDATE_VARIANTS_ROOT", str(tmp_path / "candidate_variants"))
        reg, _ = freeze_mod.build_and_freeze()
        assert reg["productionActive"] is False

    def test_status_is_shadow_candidate_for_2027_never_promotion(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cand_id, "CANDIDATE_VARIANTS_ROOT", str(tmp_path / "candidate_variants"))
        reg, _ = freeze_mod.build_and_freeze()
        assert reg["status"] == "SHADOW_CANDIDATE_FOR_2027"
        assert "PROMOTION" not in reg["status"]


class TestRequiredMetadataPresent:
    def test_all_required_immutable_fields_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cand_id, "CANDIDATE_VARIANTS_ROOT", str(tmp_path / "candidate_variants"))
        reg, _ = freeze_mod.build_and_freeze()
        for field in (
            "candidateVariantId", "baseControlModelId", "originatingExperiments", "formula",
            "applicability", "fallbackBehavior", "requiredInputs", "frozenNbRelationship",
            "evidenceReceipts", "status", "productionActive", "intendedEarliestShadowStart",
            "requiredProspectiveEvaluationThreshold", "reactivationRequirement",
        ):
            assert field in reg, f"missing required field {field}"

    def test_k_prior_matches_rsch0017_frozen_value(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cand_id, "CANDIDATE_VARIANTS_ROOT", str(tmp_path / "candidate_variants"))
        reg, _ = freeze_mod.build_and_freeze()
        assert reg["formula"]["kPrior"] == 20

    def test_applicability_restricted_to_games_1_10(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cand_id, "CANDIDATE_VARIANTS_ROOT", str(tmp_path / "candidate_variants"))
        reg, _ = freeze_mod.build_and_freeze()
        assert "1-10" in reg["applicability"]

    def test_originating_experiments_reference_both_rsch_ids(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cand_id, "CANDIDATE_VARIANTS_ROOT", str(tmp_path / "candidate_variants"))
        reg, _ = freeze_mod.build_and_freeze()
        assert reg["originatingExperiments"]["discovery"] == "MLB-RSCH-0017"
        assert reg["originatingExperiments"]["confirmation"] == "MLB-RSCH-0018"

    def test_refuses_to_freeze_if_rsch0018_not_confirmed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(freeze_mod, "RSCH0018_ARTIFACT", str(tmp_path / "bad_rsch0018.json"))
        import json
        bad = {"classification": "NO_CONFIRMATION", "disposition": "REJECT"}
        with open(tmp_path / "bad_rsch0018.json", "w") as f:
            json.dump(bad, f)
        with pytest.raises(ValueError):
            freeze_mod.build_and_freeze()


class TestReusesCanonicalRegistryNotANewOne:
    def test_imports_candidate_identity_module(self):
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "freeze_games_1_10_2027_shadow_candidate.py")).read()
        assert "from lib.edgelab import candidate_identity as cand_id" in source
        assert "cand_id.register_candidate(" in source
        assert "cand_id.build_candidate_registration(" in source
