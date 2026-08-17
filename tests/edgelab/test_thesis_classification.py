#!/usr/bin/env python3
"""
tests/edgelab/test_thesis_classification.py
================================================
Regression tests for lib/edgelab/thesis_classification.py -- the
same-underlying-thesis correlation classification that complements
scripts/risk_gate.py's existing (production, 8-market-scoped)
CORRELATION_RULES gate.

Required coverage per the MLB Model Expression Guardrails spec (section
3): ML + opponent team-total under; F5 protected side + opposing starter
outs under; pitcher K ladder thresholds; same-game but genuinely
different thesis; repeated alternate lines; exposure cap/qualification
behavior.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.thesis_classification import (
    classify_pair_severity,
    aggregate_thesis_exposure,
    thesis_tags_for_market,
    underlying_identity,
    DUPLICATE_THESIS,
    MODERATELY_CORRELATED,
    INDEPENDENT_THESIS,
)


def _entry(market, game_id="g1", **overrides):
    e = {"market": market, "gameId": game_id}
    e.update(overrides)
    return e


class TestKnownVocabularyOnly:
    def test_every_market_thesis_tag_is_in_controlled_vocabulary(self):
        from lib.edgelab.tags import THESIS_TAGS
        for market in ("ML_Away", "F5_ML_Away", "TT_Away_Over", "NRFI", "YRFI",
                       "pitcher_strikeouts", "pitcher_outs"):
            assert thesis_tags_for_market(market) <= THESIS_TAGS

    def test_unknown_market_has_no_tags(self):
        assert thesis_tags_for_market("SOME_UNKNOWN_MARKET") == frozenset()


class TestMLPlusOppositeTeamTotalUnder:
    """ML on Team A + Team B's team-total UNDER: both hinge on Team A's starter suppressing Team B's offense.

    The link is never guessed from team abbreviations alone -- the
    Team-B-side entry explicitly declares which opposing team's
    suppression its bet depends on, via `opposingTeamAbbr`.
    """

    def test_classified_as_moderately_correlated_with_explicit_link(self):
        ml_away = _entry("ML_Away", awayAbbr="STL")
        opp_tt_under = _entry("TT_Home_Under", homeAbbr="CHC", opposingTeamAbbr="STL")
        severity, tags = classify_pair_severity(ml_away, opp_tt_under)
        assert severity == MODERATELY_CORRELATED

    def test_no_link_declared_is_independent(self):
        """Without the explicit link, two opposite-side markets are never assumed correlated."""
        ml_away = _entry("ML_Away", awayAbbr="STL")
        opp_tt_under = _entry("TT_Home_Under", homeAbbr="CHC")
        severity, _ = classify_pair_severity(ml_away, opp_tt_under)
        assert severity == INDEPENDENT_THESIS


class TestF5ProtectedSidePlusOpposingStarterOutsUnder:
    """Seattle F5 side + Houston starter Hunter Brown's outs under: Seattle's offense beating Brown is the shared driver behind both."""

    def test_classified_as_moderately_correlated_with_explicit_link(self):
        f5_side = _entry("F5_ML_Away", awayAbbr="SEA")
        opp_outs_under = _entry("pitcher_outs", pitcherName="Hunter Brown", opposingTeamAbbr="SEA")
        severity, tags = classify_pair_severity(f5_side, opp_outs_under)
        assert severity == MODERATELY_CORRELATED

    def test_ml_plus_f5_plus_outs_under_all_pairwise_correlated(self):
        """Seattle F5 protected side + Seattle ML + Hunter Brown outs under -- the Aug 16 postmortem stack."""
        ml = _entry("ML_Away", awayAbbr="SEA")
        f5 = _entry("F5_ML_Away", awayAbbr="SEA")
        outs = _entry("pitcher_outs", pitcherName="Hunter Brown", opposingTeamAbbr="SEA")
        sev_ml_f5, _ = classify_pair_severity(ml, f5)
        sev_ml_outs, _ = classify_pair_severity(ml, outs)
        sev_f5_outs, _ = classify_pair_severity(f5, outs)
        assert sev_ml_f5 == DUPLICATE_THESIS       # same team, ML+F5 win-thesis pair
        assert sev_ml_outs == MODERATELY_CORRELATED
        assert sev_f5_outs == MODERATELY_CORRELATED


class TestPitcherKLadderThresholds:
    """Multiple alternate strikeout-ladder thresholds on the same pitcher are a repeated alternate line -- DUPLICATE_THESIS."""

    def test_two_k_thresholds_same_pitcher_are_duplicate(self):
        k6 = _entry("pitcher_strikeouts_6plus", pitcherName="Logan Henderson")
        k8 = _entry("pitcher_strikeouts_8plus", pitcherName="Logan Henderson")
        severity, tags = classify_pair_severity(k6, k8)
        assert severity == DUPLICATE_THESIS
        assert "PITCHER_DOMINANCE" in tags

    def test_k_thresholds_different_pitchers_are_independent(self):
        k6_a = _entry("pitcher_strikeouts_6plus", pitcherName="Pitcher A", game_id="g1")
        k6_b = _entry("pitcher_strikeouts_6plus", pitcherName="Pitcher B", game_id="g2")
        severity, _ = classify_pair_severity(k6_a, k6_b)
        assert severity == INDEPENDENT_THESIS

    def test_pitcher_ks_plus_pitcher_outs_same_pitcher_moderately_correlated(self):
        ks = _entry("pitcher_strikeouts", pitcherName="Hunter Brown")
        outs = _entry("pitcher_outs", pitcherName="Hunter Brown")
        severity, _ = classify_pair_severity(ks, outs)
        assert severity == MODERATELY_CORRELATED


class TestSameGameGenuinelyDifferentThesis:
    def test_nrfi_and_pitcher_strikeouts_different_pitcher_no_shared_tag_independent(self):
        # NRFI's tag set (LOW_SCORING_ENVIRONMENT, STARTER_EDGE) doesn't
        # overlap with an unrelated market with no thesis mapping at all.
        nrfi = _entry("NRFI")
        unrelated = _entry("SOME_UNKNOWN_MARKET")
        severity, tags = classify_pair_severity(nrfi, unrelated)
        assert severity == INDEPENDENT_THESIS
        assert tags == frozenset()

    def test_full_game_side_and_offense_upside_different_teams_independent(self):
        ml_away = _entry("ML_Away", awayAbbr="STL")
        tt_home_over = _entry("TT_Home_Over", homeAbbr="CHC")
        severity, _ = classify_pair_severity(ml_away, tt_home_over)
        assert severity == INDEPENDENT_THESIS

    def test_different_games_never_correlated_even_with_identical_market(self):
        a = _entry("NRFI", game_id="g1")
        b = _entry("NRFI", game_id="g2")
        severity, _ = classify_pair_severity(a, b)
        assert severity == INDEPENDENT_THESIS


class TestNrfiYrfiComplementaryPair:
    def test_nrfi_and_yrfi_always_duplicate(self):
        nrfi = _entry("NRFI")
        yrfi = _entry("YRFI")
        severity, _ = classify_pair_severity(nrfi, yrfi)
        assert severity == DUPLICATE_THESIS


class TestUnderlyingIdentity:
    def test_team_identity_extracted_for_offense_markets(self):
        assert underlying_identity(_entry("ML_Away", awayAbbr="STL")) == ("team", "STL")
        assert underlying_identity(_entry("TT_Home_Over", homeAbbr="CHC")) == ("team", "CHC")

    def test_pitcher_identity_extracted_for_pitcher_props(self):
        assert underlying_identity(_entry("pitcher_outs", pitcherName="Hunter Brown")) == ("pitcher", "Hunter Brown")

    def test_unknown_market_returns_unknown_identity_never_fabricated(self):
        assert underlying_identity(_entry("SOME_UNKNOWN_MARKET")) == ("unknown", None)


class TestAggregateThesisExposure:
    """4/5/6: exposure aggregation across a mixed set of entries."""

    def test_repeated_alternate_k_lines_form_one_cluster(self):
        entries = [
            _entry("pitcher_strikeouts_6plus", pitcherName="P1", betSize=5.0),
            _entry("pitcher_strikeouts_7plus", pitcherName="P1", betSize=3.0),
            _entry("pitcher_strikeouts_9plus", pitcherName="P1", betSize=2.0),
        ]
        report = aggregate_thesis_exposure(entries)
        assert len(report["clusters"]) == 1
        assert report["clusters"][0]["aggregateStake"] == 10.0
        assert len(report["clusters"][0]["members"]) == 3

    def test_genuinely_independent_bets_produce_no_clusters(self):
        entries = [
            _entry("NRFI", betSize=5.0, game_id="g1"),
            _entry("pitcher_strikeouts", pitcherName="Unrelated Pitcher", betSize=5.0, game_id="g2"),
        ]
        report = aggregate_thesis_exposure(entries)
        assert report["clusters"] == []
        assert report["independentCount"] == 2

    def test_moderately_correlated_pair_reported_but_not_clustered(self):
        entries = [
            _entry("F5_ML_Away", awayAbbr="SEA", betSize=5.0),
            _entry("pitcher_outs", pitcherName="Hunter Brown", betSize=3.0, opposingTeamAbbr="SEA"),
        ]
        report = aggregate_thesis_exposure(entries)
        assert report["clusters"] == []
        assert len(report["moderatelyCorrelatedPairs"]) == 1
        assert report["independentCount"] == 0

    def test_a_bet_involved_in_no_correlation_is_not_a_cluster_of_one(self):
        entries = [
            _entry("pitcher_strikeouts_6plus", pitcherName="P1", betSize=5.0),
            _entry("pitcher_strikeouts_9plus", pitcherName="P1", betSize=2.0),
            _entry("NRFI", betSize=4.0, game_id="g2"),
        ]
        report = aggregate_thesis_exposure(entries)
        assert len(report["clusters"]) == 1
        assert len(report["clusters"][0]["members"]) == 2
        assert report["independentCount"] == 1

    def test_never_mutates_input_entries(self):
        entries = [
            _entry("pitcher_strikeouts_6plus", pitcherName="P1", betSize=5.0),
            _entry("pitcher_strikeouts_9plus", pitcherName="P1", betSize=2.0),
        ]
        before = [dict(e) for e in entries]
        aggregate_thesis_exposure(entries)
        assert entries == before

    def test_empty_input_produces_empty_report(self):
        report = aggregate_thesis_exposure([])
        assert report["clusters"] == []
        assert report["moderatelyCorrelatedPairs"] == []
        assert report["independentCount"] == 0

    def test_can_preserve_multiple_correlated_positions_when_reported_not_forced(self):
        """This module never downgrades -- it only reports; the exceptional-EV override stays entirely a caller decision (risk_gate.py)."""
        entries = [
            _entry("pitcher_strikeouts_6plus", pitcherName="P1", betSize=5.0),
            _entry("pitcher_strikeouts_8plus", pitcherName="P1", betSize=5.0),
        ]
        report = aggregate_thesis_exposure(entries)
        # Both members are still present and untouched -- no stake removed.
        assert len(report["clusters"][0]["members"]) == 2
        assert entries[0]["betSize"] == 5.0
        assert entries[1]["betSize"] == 5.0


class TestDeterminism:
    def test_repeated_calls_produce_identical_results(self):
        entries = [
            _entry("pitcher_strikeouts_6plus", pitcherName="P1", betSize=5.0),
            _entry("pitcher_strikeouts_9plus", pitcherName="P1", betSize=2.0),
            _entry("NRFI", betSize=4.0, game_id="g2"),
            _entry("YRFI", betSize=1.0, game_id="g2"),
        ]
        r1 = aggregate_thesis_exposure(entries)
        r2 = aggregate_thesis_exposure(entries)
        assert r1 == r2
