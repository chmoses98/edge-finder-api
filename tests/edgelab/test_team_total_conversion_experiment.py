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
    def test_the_residual_no_longer_asserts_an_auc_ceiling(self):
        """The r-squared -> AUC ceiling was withdrawn. The residual text must
        not reintroduce it, and must not claim a ceiling at all."""
        residual = ARTIFACT["classification"]["residual"]
        # The phrase may appear ONLY inside the withdrawal itself, never as a
        # standing assertion -- so require the withdrawal to be adjacent.
        if "caps attainable AUC" in residual:
            assert "That claim was WITHDRAWN" in residual, (
                "the AUC-ceiling phrase appears without being withdrawn")
        assert "r-squared does not determine AUC" in residual
        assert "monotone in teamProj" in residual, (
            "the one structural claim that DOES hold must survive")
        assert "says nothing about a ceiling on AUC" in residual


class TestTheAucCeilingClaimIsWithdrawn:
    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_claim_is_recorded_as_withdrawn(self):
        c = ARTIFACT["aucCeilingClaim"]
        assert "caps attainable AUC near 0.55" in c["claimWithdrawn"]

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_refutation_is_computed_not_asserted(self):
        """A single r-squared must be shown to admit a RANGE of AUCs."""
        ref = ARTIFACT["aucCeilingClaim"]["refutation"]
        lo, hi = ref["aucRange"]
        assert hi - lo > 0.01, "refutation does not exhibit spread across thresholds"
        assert len(ref["byThreshold"]) >= 4

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_refutation_contradicts_the_withdrawn_number(self):
        lo, _ = ARTIFACT["aucCeilingClaim"]["refutation"]["aucRange"]
        assert lo > 0.55, (
            "the counter-example must actually exceed the claimed cap, otherwise it "
            "does not refute anything")

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_replacement_states_its_assumptions(self):
        sim = ARTIFACT["achievableAucSimulation"]
        assert sim["status"] == "COMPUTED"
        assert len(sim["assumptions"]) >= 2
        assert any("TRUE conditional mean" in a for a in sim["assumptions"])
        assert "NOT a ceiling" in sim["interpretation"]

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_both_distribution_families_are_simulated(self):
        assert set(ARTIFACT["achievableAucSimulation"]["byFamily"]) == {"POISSON", "FROZEN_NB"}


class TestStratifiedAudit:
    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_both_raw_pooled_and_standardized_are_reported(self):
        agg = ARTIFACT["aggregation"]
        assert "rawPooled" in agg and "thresholdStandardized" in agg

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_standardization_weights_are_fixed_a_priori_not_by_outcome(self):
        st = ARTIFACT["aggregation"]["thresholdStandardized"]["c2MinusMarket"]
        assert "fixed a priori" in st["weightSource"]
        assert st["weights"], "weights must be recorded so they can be audited"

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_pooled_market_deficit_does_not_survive_standardization(self):
        """The correction that changed this experiment's conclusion."""
        raw = ARTIFACT["aggregation"]["rawPooled"]["c2MinusMarket"]
        std = ARTIFACT["aggregation"]["thresholdStandardized"]["c2MinusMarket"]
        assert raw["excludesNull"] is True
        assert std["excludesNull"] is False

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_nb_gain_over_production_does_survive_standardization(self):
        std = ARTIFACT["aggregation"]["thresholdStandardized"]["c2MinusProduction"]
        assert std["excludesNull"] is True and std["standardizedEffect"] < 0

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_every_stratum_reports_its_own_constant_not_the_pooled_one(self):
        for block in ARTIFACT["stratified"]["allEra"]["byThreshold"].values():
            if block.get("status") == "INSUFFICIENT_SAMPLE":
                continue
            assert "constantBrierWithinStratum" in block
            assert "pairedC2MinusStratumConstant" in block

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_strata_below_the_floor_are_not_scored(self):
        blocks = ARTIFACT["stratified"]["allEra"]["byThreshold"]
        small = [b for b in blocks.values()
                 if b.get("status") == "INSUFFICIENT_SAMPLE"]
        assert small, "no stratum fell below the floor -- the floor is not being applied"
        for b in small:
            assert "c2Brier" not in b, "an under-powered stratum was scored anyway"

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_within_stratum_auc_has_its_own_higher_floor(self):
        for b in ARTIFACT["stratified"]["allEra"]["byThreshold"].values():
            if b.get("status") == "INSUFFICIENT_SAMPLE":
                continue
            if b["rows"] < ARTIFACT["stratified"]["aucFloor"]:
                assert b["withinStratumAucC2"] is None

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_home_and_away_are_reported_separately(self):
        sides = ARTIFACT["stratified"]["allEra"]["bySide"]
        assert any("HOME" in k for k in sides) and any("AWAY" in k for k in sides)

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_stratification_is_labelled_exploratory(self):
        assert "EXPLORATORY" in ARTIFACT["stratified"]["note"]


class TestCurrentEraDecision:
    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_current_era_is_judged_on_its_own_sample(self):
        """Obsolete v1.1 rows must not decide a question about today."""
        d = ARTIFACT["currentEraDecision"]
        assert d["independentGames"] < ARTIFACT["overall"]["independentGames"]

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_a_favourable_sign_alone_cannot_produce_case_2(self):
        """The correction that matters most in this file.

        An earlier version of current_era_decision() returned CASE_2 as
        soon as every scored stratum had a negative point estimate, with
        no reference to whether any interval excluded zero. On this corpus
        that promoted two strata whose CIs both span zero, under a
        standardized aggregate that also spans zero."""
        d = ARTIFACT["currentEraDecision"]
        if d["case"] == "CASE_2_CONSISTENT_WITHIN_THRESHOLD_VALUE":
            assert d["significantStrata"], (
                "CASE_2 reached with no stratum reaching significance")
        if not d["significantStrata"] and not d["standardizedSupportsC2"]:
            assert d["case"] != "CASE_2_CONSISTENT_WITHIN_THRESHOLD_VALUE"

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_sign_and_significance_are_reported_separately(self):
        d = ARTIFACT["currentEraDecision"]
        assert "favourableBySignOnly" in d and "significantStrata" in d, (
            "collapsing sign and significance into one field is how the "
            "over-reading happened")

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_measured_verdict_is_exploratory_not_promotable(self):
        d = ARTIFACT["currentEraDecision"]
        assert d["case"] == "CASE_3_SINGLE_THRESHOLD_ONLY"
        assert d["significantStrata"] == []
        assert d["independentGames"] >= 100, (
            "the current era now clears its floor, so the verdict rests on "
            "evidence rather than on sample")

    def test_all_four_cases_are_reachable(self):
        for case in ("CASE_1_NB_LOSES_POOLED_AND_WITHIN_THRESHOLD",
                     "CASE_2_CONSISTENT_WITHIN_THRESHOLD_VALUE",
                     "CASE_3_SINGLE_THRESHOLD_ONLY",
                     "CASE_4_INSUFFICIENT_CURRENT_ERA_SAMPLE"):
            assert case in SOURCE

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_no_production_candidate_is_created(self):
        pb = ARTIFACT["promotionBlocked"]
        assert pb["blocked"] is True
        assert "NOT a production candidate" in pb["notPromoted"]

    @pytest.mark.skipif(ARTIFACT is None, reason="artifact not generated")
    def test_the_blocker_no_longer_rests_on_the_withdrawn_pooled_claim(self):
        reason = ARTIFACT["promotionBlocked"]["reason"]
        assert "threshold-mix artifact" in reason
        assert "sample, not a measured loss" in reason


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
    def test_the_within_stratum_reversal_is_neither_promoted_nor_dismissed(self):
        """The earlier caveat dismissed the reversal ('the pooled comparison
        is the one that counts'). Standardisation showed that dismissal was
        wrong, so the text must now do neither."""
        caveat = ARTIFACT["stratificationCaveat"]
        assert "SUPERSEDED WITHIN THIS ARTIFACT" in caveat
        # The old dismissal may be QUOTED, but only in order to repudiate it.
        if "the pooled comparison is the one that counts" in caveat:
            assert "That dismissal was wrong" in caveat
        assert "EXPLORATORY" in caveat
        assert "neither promoted nor dismissed" in caveat or (
            "rather than either promoted or dismissed" in caveat)


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
