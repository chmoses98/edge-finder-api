"""Exchange-vs-canonical settlement cross-check."""

from lib.edgelab.exchange_settlement import (
    classify, compare_settlements, summarize, is_quarantined, normalize_result,
    AGREE, MISMATCH, CANONICAL_MISSING, EXCHANGE_MISSING, VOID_DISAGREEMENT,
)


def test_kalshi_lowercase_results_normalize_to_the_canonical_form():
    assert normalize_result("yes") == "YES"
    assert normalize_result("no") == "NO"
    assert normalize_result(None) is None


def test_agreement_and_mismatch():
    assert classify("YES", "yes") == AGREE
    assert classify("NO", "no") == AGREE
    assert classify("NO", "yes") == MISMATCH
    assert classify("YES", "no") == MISMATCH


def test_one_sided_presence_is_reported_not_dropped():
    assert classify(None, "yes") == CANONICAL_MISSING
    assert classify("YES", None) == EXCHANGE_MISSING


def test_void_disagreement_is_its_own_class():
    assert classify("VOID", "yes") == VOID_DISAGREEMENT
    assert classify("YES", "cancelled") == VOID_DISAGREEMENT
    assert classify("VOID", "voided") == AGREE


def test_only_real_disagreements_quarantine():
    assert is_quarantined(MISMATCH)
    assert is_quarantined(VOID_DISAGREEMENT)
    assert not is_quarantined(AGREE)
    assert not is_quarantined(EXCHANGE_MISSING)


def test_comparison_covers_the_union_of_both_sides():
    comp = compare_settlements({"A": "YES", "B": "NO"}, {"A": "yes", "C": "no"})
    assert set(comp) == {"A", "B", "C"}
    assert comp["A"]["classification"] == AGREE
    assert comp["B"]["classification"] == EXCHANGE_MISSING
    assert comp["C"]["classification"] == CANONICAL_MISSING


def test_summary_reports_agreement_rate_and_quarantine_list():
    comp = compare_settlements({"A": "YES", "B": "NO"}, {"A": "yes", "B": "yes"})
    s = summarize(comp)
    assert s["tickersCompared"] == 2
    assert s["counts"][MISMATCH] == 1
    assert s["quarantinedTickers"] == ["B"]
    assert s["quarantinedCount"] == 1
    assert s["agreementRate"] == 0.5


def test_the_f5spread_defect_shape_would_have_been_caught():
    """A family-wide horizon defect shows up as a MISMATCH block, which is
    exactly what was invisible while exchange truth went uncaptured."""
    canonical = {"KXMLBF5SPREAD-X-%d" % i: "YES" for i in range(10)}
    exchange = {"KXMLBF5SPREAD-X-%d" % i: "no" for i in range(10)}
    s = summarize(compare_settlements(canonical, exchange))
    assert s["counts"][MISMATCH] == 10
    assert s["quarantinedCount"] == 10
    assert s["agreementRate"] == 0.0


def test_nothing_is_overwritten_by_comparing():
    canonical = {"A": "YES"}
    exchange = {"A": "no"}
    compare_settlements(canonical, exchange)
    assert canonical == {"A": "YES"} and exchange == {"A": "no"}
