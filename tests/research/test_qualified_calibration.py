#!/usr/bin/env python3
"""
tests/research/test_qualified_calibration.py
============================================
PHASES K/L/M: the statistics that decide whether a family finding is real.

Two corrections to #233, and the tests here exist to stop either from
being quietly undone.

SOURCE COMPLETENESS. #233 scored every settled market with an executable
pre-start ask, regardless of whether the capture behind that ask had
retrieved everything. It had not, 9 times in 21 days, and the missingness
was SYSTEMATIC -- the old sequential fetch meant a rate limit truncated
whatever series came last, so the same hitter prop families absorbed the
loss every time. That is bias in a known direction, not noise.

INDEPENDENCE. #233 printed Wilson intervals over 31,584 rows at ~145 rows
per independent game. Every prop, team total and game total on one game
shares that game's outcome, so those intervals were far too narrow and
"outside the interval" meant very little.

The load-bearing tests: a clustered interval must be WIDER than the naive
one on correlated data, and a family must not be called a finding on the
naive interval alone.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab.capture_completeness import PARTIAL, UNKNOWN_LEGACY
from scripts.research.full_universe.run_qualified_calibration import (
    benjamini_hochberg, classify_quote_source, clustered_bootstrap_gap,
    family_table, summarize, wilson,
)

UNCLASSIFIABLE = "UNCLASSIFIABLE_SNAPSHOT_PRUNED"


def _stamps(*pairs):
    return sorted((datetime.datetime.fromisoformat(t.replace("Z", "+00:00")), c)
                  for t, c in pairs)


def _row(game, yes, price=0.3, family="game_total"):
    return {"gameId": game, "ticker": "%s-%d" % (game, yes), "resolvedYes": bool(yes),
            "price": price, "marketFamily": family}


# ── attributing a quote to the capture that produced it ────────────────────

def test_a_quote_is_attributed_to_the_capture_it_came_from():
    stamps = _stamps(("2026-09-19T18:33:00Z", PARTIAL),
                     ("2026-09-19T21:24:00Z", UNKNOWN_LEGACY))
    assert classify_quote_source("2026-09-19T18:33:10Z", stamps) == PARTIAL
    assert classify_quote_source("2026-09-19T21:24:05Z", stamps) == UNKNOWN_LEGACY


def test_a_quote_older_than_snapshot_retention_is_unclassifiable_not_clean():
    """Pruned snapshots cannot vouch for themselves. That is a third state,
    distinct from a snapshot we still hold that carries no evidence."""
    stamps = _stamps(("2026-09-19T18:33:00Z", PARTIAL))
    assert classify_quote_source("2026-06-01T12:00:00Z", stamps) == UNCLASSIFIABLE
    assert classify_quote_source(None, stamps) == UNCLASSIFIABLE


def test_with_no_ledger_nothing_is_classified_as_clean():
    assert classify_quote_source("2026-09-19T18:33:00Z", []) == UNCLASSIFIABLE


# ── PHASE M: clustering must widen, not decorate ───────────────────────────

def test_the_clustered_interval_is_wider_than_the_naive_one_on_correlated_data():
    """THE point of Phase M. 20 games x 50 perfectly correlated rows each:
    the naive interval sees 1,000 trials, the clustered one sees 20."""
    rows = []
    for g in range(20):
        outcome = g % 2                       # every row in a game shares it
        rows.extend(_row("g%d" % g, outcome) for _ in range(50))
    stat = summarize(rows)
    naive = stat["naiveWilson95"]
    boot = stat["clusteredBootstrap95GapCI"]
    assert boot["hi"] - boot["lo"] > naive["hi"] - naive["lo"], (
        "clustering correlated rows must WIDEN the interval, not narrow it")


def test_the_bootstrap_resamples_games_not_rows():
    """If it resampled rows it would assume away the correlation that makes
    the naive interval wrong in the first place."""
    rows = []
    for g in range(30):
        rows.extend(_row("g%d" % g, g % 2) for _ in range(10))
    assert clustered_bootstrap_gap(rows)["games"] == 30


def test_the_bootstrap_is_deterministic():
    """A research number must be reproducible."""
    rows = [_row("g%d" % g, g % 2) for g in range(40)]
    assert clustered_bootstrap_gap(rows) == clustered_bootstrap_gap(rows)


def test_too_few_independent_games_refuses_rather_than_inventing_an_interval():
    rows = [_row("g1", 1), _row("g1", 0)]
    result = clustered_bootstrap_gap(rows)
    assert result["lo"] is None and result["hi"] is None
    assert "too few" in result["note"]


def test_rows_per_independent_game_is_reported():
    """The number that made #233's intervals untrustworthy must stay visible."""
    rows = []
    for g in range(4):
        rows.extend(_row("g%d" % g, g % 2) for _ in range(25))
    assert summarize(rows)["rowsPerIndependentGame"] == 25.0


def test_a_genuinely_well_calibrated_market_is_not_flagged():
    """The guard against a method that finds an edge in anything."""
    rows = [_row("g%d" % g, 1 if g % 10 < 3 else 0, price=0.3) for g in range(200)]
    stat = summarize(rows)
    assert abs(stat["calibrationGap"]) < 0.01
    assert stat["gapSignificantUnderClustering"] is False


# ── multiplicity ───────────────────────────────────────────────────────────

def test_benjamini_hochberg_rejects_a_lone_borderline_result_among_many():
    """A dozen families tested at 95% produce roughly one interval excluding
    zero by chance. p=0.02 is not a finding once you have looked at eleven."""
    p_values = dict({"suspect": 0.02}, **{"f%d" % i: 0.5 for i in range(10)})
    assert benjamini_hochberg(p_values)["survivors"] == []


def test_benjamini_hochberg_accepts_a_genuinely_strong_result():
    p_values = dict({"strong": 0.0001}, **{"f%d" % i: 0.5 for i in range(10)})
    assert benjamini_hochberg(p_values)["survivors"] == ["strong"]


def test_multiplicity_correction_counts_every_family_tested():
    assert benjamini_hochberg({"a": 0.01, "b": 0.02, "c": 0.9})["familiesTested"] == 3


def test_a_family_is_never_labelled_a_candidate_on_the_naive_interval_alone():
    """#233's game_total was 'outside its Wilson interval'. That label must
    not be reachable without surviving clustering AND multiplicity."""
    rows = []
    for g in range(30):
        rows.extend(_row("g%d" % g, g % 3 == 0, price=0.2, family="game_total")
                    for _ in range(40))
    table = family_table(rows, minimum_n=10)
    table.pop("_multiplicityCorrection")
    for stat in table.values():
        if stat["finding"] == "CANDIDATE_FOR_HOLDOUT":
            assert stat["gapSignificantUnderClustering"] is True
            assert stat["survivesMultiplicityCorrection"] is True


def test_every_family_carries_an_explicit_finding_label():
    rows = []
    for g in range(30):
        rows.extend(_row("g%d" % g, g % 2, family="f%d" % (g % 3)) for _ in range(20))
    table = family_table(rows, minimum_n=10)
    table.pop("_multiplicityCorrection")
    assert table
    for stat in table.values():
        assert stat["finding"] in ("NO_EVIDENCE", "EXPLORATORY", "CANDIDATE_FOR_HOLDOUT")


# ── the naive interval is kept only for comparison ─────────────────────────

def test_the_naive_wilson_interval_is_still_reported_so_the_gap_is_visible():
    rows = [_row("g%d" % g, g % 2) for g in range(50)]
    stat = summarize(rows)
    assert stat["naiveWilson95"]["lo"] is not None
    assert "clusteredBootstrap95GapCI" in stat


def test_wilson_on_no_data_returns_nothing_rather_than_a_false_interval():
    assert wilson(0, 0) == (None, None)
