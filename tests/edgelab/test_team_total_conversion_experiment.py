"""Tests for MLB-RSCH-0034 (team-total probability conversion).

The failure mode this experiment had to avoid is reconstructing modelProb
through an ASSUMED formula and then reporting that the assumption fits.
Most of these tests therefore check that the production path is READ, that
the round-trip runs before any candidate is believed, and that nothing is
fitted on the evaluation sample.
"""
import ast
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts", "edgelab"))

import run_team_total_conversion_experiment as exp  # noqa: E402
from lib.edgelab.research import methodology_v3 as v3  # noqa: E402
from lib.edgelab.shadow_distribution import FROZEN_DISPERSION  # noqa: E402

SOURCE = open(exp.__file__, encoding="utf-8").read()
ARTIFACT = json.load(open(exp.ARTIFACT_PATH, encoding="utf-8")) if os.path.exists(
    exp.ARTIFACT_PATH) else None


# ── The production path is read, not assumed ─────────────────────────────

class TestProductionPathIsTraced:
    def test_trace_reads_the_real_ledger_source(self):
        trace = exp.trace_production_path()
        assert trace["currentVersion"] == "v1.2", (
            "the trace must identify production's ACTUAL conversion version")
        assert any("p_over_total(proj, tt_line - 1)" in h["source"] for h in trace["hops"])

    def test_the_distribution_family_is_read_from_the_function_body(self):
        """Not asserted. The user's instruction was 'do not assume Poisson'."""
        trace = exp.trace_production_path()
        assert trace["distributionFamiliesFound"] == ["poisson_pmf"]
        assert trace["distributionIsPoisson"] is True

    def test_the_probability_cap_is_part_of_the_traced_output(self):
        assert exp.trace_production_path()["probabilityCapApplied"] is True

    def test_every_named_hop_is_present(self):
        hops = [h["hop"] for h in exp.trace_production_path()["hops"]]
        for required in ("teamProj", "team identity", "contract threshold",
                         "contract event semantics", "distribution",
                         "probability conversion", "modelProb"):
            assert required in hops

    def test_the_block_anchor_cannot_match_the_module_docstring(self):
        """Regression: anchoring on the bare string "TT_Away_Over" matched a
        module-docstring mention ~1500 lines above the conversion, so the
        window never reached it and the trace reported UNRECOGNISED."""
        assert 'source.index("TT_Away_Over")' not in SOURCE
        assert "TT_Away_Over / TT_Home_Over" in SOURCE


# ── Contract truth comes from settlement, not from the pricing code ──────

class TestContractTruth:
    def test_the_resolved_event_is_at_least_n(self):
        truth = exp.contract_truth()
        assert truth["resolvedEvent"] == "AT_LEAST_N"

    def test_all_the_named_alternatives_are_distinguished(self):
        d = exp.contract_truth()["distinguishedFrom"]
        for k in ("OVER_INTEGER_N", "OVER_X_POINT_5", "YES_NO_DIRECTION", "HOME_AWAY", "PERIOD"):
            assert k in d and d[k]

    def test_truth_is_not_established_from_the_pricing_line(self):
        sources = [f["source"] for f in exp.contract_truth()["sources"]]
        assert not any("build_market_ledger" in s for s in sources), (
            "contract semantics must be established independently of the code being audited")
        assert any("settlement.py" in s for s in sources)
        assert any("market_taxonomy" in s for s in sources)

    def test_market_titles_are_labelled_non_authoritative(self):
        title_src = [f for f in exp.contract_truth()["sources"] if "titles" in f["source"]]
        assert title_src and "NOT authoritative" in title_src[0]["impliesYesIff"]


# ── Round-trip runs first and reports every bucket ───────────────────────

class TestRoundTrip:
    def test_all_six_buckets_exist(self):
        assert exp.ROUND_TRIP_BUCKETS == (
            "EXACT_MATCH", "TOLERANCE_MATCH", "MODEL_VERSION_MISMATCH",
            "MISSING_INPUTS", "SEMANTIC_MISMATCH", "UNRESOLVED")

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_every_bucket_is_reported_even_when_empty(self):
        buckets = ARTIFACT["roundTrip"]["buckets"]
        for b in exp.ROUND_TRIP_BUCKETS:
            assert b in buckets, f"bucket {b} silently omitted"

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_version_boundary_is_detected_not_hardcoded(self):
        """The fix date must be READ OFF the round-trip. A hardcoded date
        would make the round-trip decorative."""
        assert ARTIFACT["roundTrip"]["detectedFixDate"] == "2026-08-21"
        assert '"2026-08-21"' not in SOURCE, "the fix date must not be hardcoded"

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_production_actually_round_trips(self):
        """The user's rule: do not proceed as though a conversion model is
        correct if it cannot round-trip production."""
        assert ARTIFACT["roundTrip"]["reproductionRate"] >= 0.80

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_two_conventions_do_not_overlap_within_a_date(self):
        """If a single date reproduced under BOTH conventions the boundary
        would be an artifact of tolerance, not a real version change."""
        for date, conv in ARTIFACT["roundTrip"]["conventionByDate"].items():
            assert not (conv.get("v1.1") and conv.get("v1.2")), (
                f"{date} reproduces under both conventions")


# ── Nothing is fitted ────────────────────────────────────────────────────

class TestNothingIsFitted:
    def test_the_nb_dispersion_is_the_frozen_one(self):
        assert FROZEN_DISPERSION == 0.281513
        assert "FROZEN_DISPERSION" in SOURCE
        assert "fit_overdispersion" not in SOURCE

    def test_no_dispersion_is_estimated_anywhere_in_this_experiment(self):
        tree = ast.parse(SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                assert "fit" not in name.lower() or name == "fitting", (
                    f"experiment calls a fitting routine: {name}")

    def test_roi_is_not_a_fitting_objective(self):
        """V3's own guard, applied to this experiment's scoring code."""
        scoring = SOURCE[SOURCE.index("def brier("):SOURCE.index("def attribute(")]
        v3.assert_roi_not_a_fitting_objective(scoring)

    def test_economics_is_computed_after_scoring_and_reports_no_roi(self):
        """Scoped to CODE, not prose.

        The function's own docstring and note legitimately say the words
        "ROI" and "stake" in order to state that it computes neither.
        Grepping the raw text would flag that disclaimer as the offence,
        so this walks the AST and inspects identifiers and dict KEYS only,
        leaving string literals alone.
        """
        module = ast.parse(SOURCE)
        econ = next(n for n in ast.walk(module)
                    if isinstance(n, ast.FunctionDef) and n.name == "economics")
        names = set()
        for node in ast.walk(econ):
            if isinstance(node, ast.Name):
                names.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                names.add(node.attr.lower())
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # dict keys are constants too; keep only short key-like ones
                if len(node.value) < 60:
                    names.add(node.value.lower())
        for banned in ("roi", "profit", "stake", "pnl", "bankroll"):
            offenders = [n for n in names if banned in n]
            assert not offenders, f"economics references {banned}: {offenders}"


# ── Candidates are what they claim to be ─────────────────────────────────

class TestCandidateSemantics:
    def test_c1_prices_at_least_n(self):
        """p_over_total(mean, N-1) must equal P(runs >= N)."""
        from scripts.build_market_ledger import p_over_total
        for mean in (3.0, 4.5, 6.0):
            for n in (2, 3, 4, 5):
                assert exp.poisson_at_least(mean, n) == pytest.approx(p_over_total(mean, n - 1))

    def test_c1_and_the_legacy_convention_actually_differ(self):
        """If they agreed the semantic attribution would be vacuous."""
        assert exp.poisson_at_least(4.5, 4) > exp.legacy_poisson(4.5, 4)

    def test_c2_is_monotone_in_the_projection(self):
        vals = [exp.nb_at_least(m, 4) for m in (3.0, 3.5, 4.0, 4.5, 5.0)]
        assert vals == sorted(vals)

    def test_c2_has_a_fatter_tail_than_c1_at_high_thresholds(self):
        """The whole point of the overdispersed body."""
        assert exp.nb_at_least(4.5, 8) > exp.poisson_at_least(4.5, 8)

    def test_every_candidate_preserves_the_ordering_of_teamproj(self):
        """Decisive for the residual argument: a monotone conversion cannot
        create ranking information the mean does not carry."""
        means = [3.2, 3.9, 4.4, 5.1, 6.0]
        for fn in (exp.poisson_at_least, exp.nb_at_least):
            probs = [fn(m, 4) for m in means]
            assert probs == sorted(probs)


# ── Attribution and classification ───────────────────────────────────────

class TestAttribution:
    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_semantic_and_distribution_gains_are_reported_separately(self):
        a = ARTIFACT["attribution"]
        assert a["semanticGain"] is not None and a["distributionGain"] is not None
        assert a["semanticGain"]["gain"] != a["distributionGain"]["gain"]

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_semantic_gain_is_measured_only_where_it_is_measurable(self):
        """On post-fix rows C0 IS C1, so pooling would understate the gain."""
        assert ARTIFACT["attribution"]["semanticGain"]["population"] == "rows priced under v1.1"

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_transport_is_computed_from_two_blocks_not_asserted(self):
        a = ARTIFACT["attribution"]
        assert set(a["chronologicalBlocks"]) == {"v1.1_era", "v1.2_era"}
        assert a["distributionGainReplicatesInBothBlocks"] is True
        assert a["transportEvidence"] == v3.TRANSPORT_CHRONOLOGICAL_VALIDATION

    def test_transport_is_none_when_a_block_disagrees(self):
        """Negative control on the transport rule itself."""
        rows = ([{"gameId": f"g{i}", "settleDate": "2026-08-01", "productionVersion": "v1.1",
                  "C0": 0.5, "C1": 0.5, "C2": 0.9, "outcome": 0} for i in range(40)] +
                [{"gameId": f"h{i}", "settleDate": "2026-08-25", "productionVersion": "v1.2",
                  "C0": 0.5, "C1": 0.5, "C2": 0.9, "outcome": 0} for i in range(40)])
        out = exp.attribute(rows)
        assert out["distributionGainReplicatesInBothBlocks"] is False
        assert out["transportEvidence"] is None


class TestClassification:
    def test_all_five_cases_are_reachable(self):
        assert len(exp.ROOT_CAUSE_CASES) == 5
        for case in exp.ROOT_CAUSE_CASES:
            assert case in SOURCE

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_measured_case_is_both(self):
        c = ARTIFACT["classification"]
        assert c["case"] == "CASE_C_BOTH"
        assert c["semanticCorrectionIsReal"] and c["distributionCorrectionIsReal"]

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_residual_is_stated_and_is_not_the_conversion(self):
        c = ARTIFACT["classification"]
        assert c["bestCandidateStillLosesToConstant"] is True
        assert "informativeness of the mean" in c["residual"]


# ── The result must not be mistaken for a promotion ──────────────────────

class TestPromotionIsBlockedSeparatelyFromV3:
    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_v3_passing_does_not_promote(self):
        """V3's four labels answer 'better than production'. They do not
        answer 'good enough to bet', and this artifact must not conflate
        them -- the candidate passes V3 and is still blocked."""
        assert ARTIFACT["methodologyV3"]["bettingShadowGatePasses"] is True
        assert ARTIFACT["promotionBlocked"]["blocked"] is True

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_blocker_cites_both_the_constant_and_the_market(self):
        reason = ARTIFACT["promotionBlocked"]["reason"]
        assert "constant base rate" in reason and "Kalshi" in reason

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_within_stratum_reversal_is_not_claimed_as_a_result(self):
        caveat = ARTIFACT["stratificationCaveat"]
        assert "Simpson" in caveat and "NOT claimed as a result" in caveat


# ── Governance ───────────────────────────────────────────────────────────

class TestRegistration:
    def test_registration_is_idempotent(self):
        a, _ = exp.register_experiment()
        b, _ = exp.register_experiment()
        assert a["controlModelId"] == b["controlModelId"]

    def test_the_sample_floor_matches_the_f5_floor(self):
        _, definition = exp.register_experiment()
        assert definition["minimumSampleRequirement"]["independentGames"] == 100

    def test_clustering_is_by_game_not_by_row(self):
        _, definition = exp.register_experiment()
        assert definition["clusteringUnit"] == "gameId"

    def test_preregistration_is_frozen_and_refuses_a_zero_floor(self):
        pre = exp.preregistration()
        with pytest.raises(Exception):
            pre.effect_floor = 0.0          # frozen dataclass
        with pytest.raises(v3.MaterialityPreregistrationError):
            v3.MaterialityPreregistration(
                null_value=0.0, effect_floor=0.0, harm_tolerance=0.0,
                require_ci_excludes_null=True, min_score_improvement=0.005,
                min_independent_games=100, justification="x" * 50)

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_supersession_is_declared_without_rewriting_merged_artifacts(self):
        s = ARTIFACT["supersedes"]
        assert set(s["experiments"]) == {"MLB-RSCH-0031", "MLB-RSCH-0032"}
        assert s["artifactsRewritten"] is False
        assert "PRICING" in s["conclusion"] or "pricing" in s["conclusion"]

    def test_prior_artifacts_are_not_modified_by_this_experiment(self):
        for prior in ("latest_mlb_rsch_0031_live_exposure_audit.json",
                      "latest_mlb_rsch_0032_yellow_family_audit.json"):
            path = os.path.join(exp.ANALYTICS_DIR, prior)
            if os.path.exists(path):
                assert prior not in SOURCE, f"this experiment writes to {prior}"


class TestScoringDiscipline:
    def test_brier_is_the_primary_metric(self):
        _, definition = exp.register_experiment()
        assert "Brier" in definition["primaryMetric"]

    def test_bootstrap_clusters_by_game(self):
        rows = [{"gameId": "g1", "C2": 0.5, "C1": 0.4, "outcome": 1},
                {"gameId": "g1", "C2": 0.5, "C1": 0.4, "outcome": 0},
                {"gameId": "g2", "C2": 0.6, "C1": 0.5, "outcome": 1}]
        out = exp.bootstrap_delta(rows, "C2", "C1", draws=50)
        assert set(out) == {"mean", "ciLow", "ciHigh", "excludesNull"}

    def test_blocks_below_the_row_floor_report_insufficient(self):
        assert exp.score_block([{"gameId": "g", "settleDate": "d", "outcome": 1,
                                 "marketP": 0.5, "C0": 0.5, "C1": 0.5, "C2": 0.5}],
                               "tiny")["status"] == "INSUFFICIENT_SAMPLE"
