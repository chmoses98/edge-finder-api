"""Canonical CLV convention: POSITIVE MEANS GOOD.

Covers the helper, both sides, units, the writers that must delegate to it,
the historical migration's determinism, and a repository-wide guard against
reintroducing raw `entry - closing` semantics.
"""

import ast
import json
import os
import subprocess

import pytest

from lib.edgelab.clv_convention import (
    CONVENTION_ID, LEGACY_INVERTED_CONVENTION_ID, SIDE_YES, SIDE_NO,
    UNIT_CENTS, UNIT_PROBABILITY, UNIT_PERCENTAGE_POINTS,
    clv_for_yes, clv_for_no, clv_for_side, convert, convention_marker,
    executable_price_cents, good_clv_cents, good_clv_from_implied,
    good_clv_from_quotes, is_good, invert_legacy_entry_minus_closing,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


# ------------------------------------------------------------------ YES side
def test_yes_entry_below_close_is_positive():
    assert clv_for_yes(33.0, 34.0) == 1.0
    assert is_good(clv_for_yes(33.0, 34.0))


def test_yes_entry_above_close_is_negative():
    assert clv_for_yes(62.55, 61.0) == -1.55
    assert not is_good(clv_for_yes(62.55, 61.0))


def test_yes_entry_equal_close_is_zero():
    assert clv_for_yes(50.0, 50.0) == 0.0
    assert not is_good(0.0)


# ------------------------------------------------------------------- NO side
def test_no_entry_below_close_is_positive():
    assert clv_for_no(40.0, 45.0) == 5.0
    assert is_good(clv_for_no(40.0, 45.0))


def test_no_entry_above_close_is_negative():
    assert clv_for_no(60.0, 55.0) == -5.0


def test_no_entry_equal_close_is_zero():
    assert clv_for_no(60.0, 60.0) == 0.0


def test_side_dispatch_matches_the_explicit_helpers():
    assert clv_for_side(33.0, 34.0, SIDE_YES) == clv_for_yes(33.0, 34.0)
    assert clv_for_side(40.0, 45.0, SIDE_NO) == clv_for_no(40.0, 45.0)
    with pytest.raises(ValueError):
        clv_for_side(1.0, 2.0, "SOMETHING_ELSE")


# --------------------------------------------------------------- book access
def test_yes_uses_the_ask_and_no_uses_the_complement_of_the_bid():
    q = {"yesBid": 40.0, "yesAsk": 42.0}
    assert executable_price_cents(q, SIDE_YES) == 42.0
    assert executable_price_cents(q, SIDE_NO) == 60.0


def test_archived_no_ask_wins_over_the_derived_complement():
    q = {"yesBid": 40.0, "yesAsk": 42.0, "noAsk": 59.0}
    assert executable_price_cents(q, SIDE_NO) == 59.0


def test_missing_side_of_the_book_returns_none_never_a_guess():
    assert executable_price_cents({"yesBid": 40.0}, SIDE_YES) is None
    assert executable_price_cents({"yesAsk": 42.0}, SIDE_NO) is None
    assert executable_price_cents(None, SIDE_YES) is None
    assert good_clv_from_quotes({"yesBid": 40.0}, {"yesAsk": 44.0}, SIDE_YES) is None


def test_midpoint_is_never_substituted_for_a_missing_executable_price():
    """A quote whose mid is 41 must price YES at the ask (42), never 41."""
    q = {"yesBid": 40.0, "yesAsk": 42.0}
    assert executable_price_cents(q, SIDE_YES) == 42.0
    # and with no ask at all, it refuses rather than falling back to a mid
    assert executable_price_cents({"yesBid": 40.0}, SIDE_YES) is None


def test_both_legs_come_from_the_same_side():
    entry = {"yesBid": 40.0, "yesAsk": 42.0}     # YES 42, NO 60
    closing = {"yesBid": 45.0, "yesAsk": 46.0}   # YES 46, NO 55
    assert good_clv_from_quotes(entry, closing, SIDE_YES) == 4.0
    assert good_clv_from_quotes(entry, closing, SIDE_NO) == -5.0


# ------------------------------------------------------------------- units
def test_units_are_explicit_and_convert_correctly():
    assert convert(1.0, UNIT_CENTS, UNIT_PROBABILITY) == 0.01
    assert convert(0.01, UNIT_PROBABILITY, UNIT_PERCENTAGE_POINTS) == 1.0
    assert convert(5.0, UNIT_PERCENTAGE_POINTS, UNIT_CENTS) == 5.0


def test_unknown_unit_raises_rather_than_silently_mixing():
    with pytest.raises(ValueError):
        convert(1.0, "FURLONGS", UNIT_CENTS)
    with pytest.raises(ValueError):
        clv_for_yes(1.0, 2.0, unit="FURLONGS")


def test_implied_probability_form_returns_percentage_points():
    assert good_clv_from_implied(0.33, 0.34) == pytest.approx(1.0)
    assert good_clv_from_implied(0.6255, 0.61) == pytest.approx(-1.55)


def test_none_inputs_propagate_as_none():
    assert good_clv_cents(None, 40.0) is None
    assert good_clv_cents(40.0, None) is None
    assert good_clv_from_implied(None, 0.5) is None
    assert invert_legacy_entry_minus_closing(None) is None


def test_convention_marker_names_both_convention_and_unit():
    m = convention_marker(UNIT_CENTS)
    assert m == {"clvConvention": CONVENTION_ID, "clvUnit": UNIT_CENTS}
    assert CONVENTION_ID == "POSITIVE_IS_GOOD_V1"


def test_legacy_inverter_is_involutive_and_unused_in_production():
    assert invert_legacy_entry_minus_closing(1.55) == -1.55
    out = subprocess.run(
        ["grep", "-rn", "invert_legacy_entry_minus_closing",
         os.path.join(REPO, "lib"), os.path.join(REPO, "scripts"), os.path.join(REPO, "api")],
        capture_output=True, text=True).stdout
    callers = [l for l in out.splitlines() if "clv_convention.py" not in l]
    assert callers == [], "legacy inverter must not be wired into production"


# ------------------------------------------------- writers delegate, not copy
ACTIVE_WRITERS = [
    "lib/clv_validator.py",
    "lib/edgelab/clv.py",
    "scripts/clv_from_snapshot.py",
    "lib/research/hitter_projection_audit.py",
    "lib/edgelab/mlb_alpha_shadow.py",
]


@pytest.mark.parametrize("rel", ACTIVE_WRITERS)
def test_active_writers_use_the_canonical_helper(rel):
    src = open(os.path.join(REPO, rel)).read()
    assert "clv_convention" in src, "%s must delegate to the canonical helper" % rel


def test_edgelab_clv_writer_produces_positive_for_a_good_buy():
    from lib.edgelab.clv import compute_clv_for_bet
    bet = {"entryPrice": 0.33, "side": "YES"}
    quotes = [{"clvQuoteId": "q1", "isClosingQuote": True, "yesAsk": 34.0, "yesBid": 33.0}]
    out = compute_clv_for_bet(bet, quotes)
    assert out["clvCents"] == pytest.approx(1.0)
    assert out["clvConvention"] == CONVENTION_ID


def test_snapshot_writer_produces_positive_for_a_good_buy():
    from scripts.clv_from_snapshot import calculate_clv
    # entry -200 -> implied 0.6667; closes at 0.70 -> bought cheaper -> positive
    assert calculate_clv(-200, 0.70, True) > 0
    assert calculate_clv(-200, 0.60, True) < 0


# ---------------------------------------------- repository-wide sign guard
def test_no_active_writer_reintroduces_raw_entry_minus_closing():
    """
    Guard: the inverted formula may appear ONLY inside the explicitly named
    legacy helper. Parsed as source text across active code, with comments
    and docstrings excluded so prose describing the old bug cannot trip it.
    """
    offenders = []
    for root, _dirs, files in os.walk(REPO):
        if any(part in root for part in (".git", "node_modules", "/tests", "/data", "/docs")):
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, REPO)
            # The sanctioned legacy helper, and the read-only sign audit
            # whose whole job is to RECOGNISE the legacy convention.
            if rel.endswith("clv_convention.py") or rel.endswith("audit_stored_clv_sign.py"):
                continue
            try:
                tree = ast.parse(open(path).read())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                # Only ASSIGNMENTS INTO A CLV-NAMED TARGET count. A bare
                # entry-minus-closing subtraction is not automatically a CLV
                # bug: spread compression (entrySpread - closingSpread) is a
                # genuinely different quantity, and the sign audit computes
                # the legacy form on purpose in order to detect it.
                if not isinstance(node, ast.Assign):
                    continue
                targets = []
                for t in node.targets:
                    targets.append(getattr(t, "id", None) or getattr(t, "attr", None) or "")
                if not any("clv" in (t or "").lower() for t in targets):
                    continue
                for sub in ast.walk(node.value):
                    if not (isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Sub)):
                        continue
                    left = getattr(sub.left, "id", None) or getattr(sub.left, "attr", None)
                    right = getattr(sub.right, "id", None) or getattr(sub.right, "attr", None)
                    if not left or not right:
                        continue
                    if "entry" in left.lower() and "clos" in right.lower():
                        offenders.append("%s:%d %s = %s - %s"
                                         % (rel, node.lineno, targets[0], left, right))
    assert offenders == [], (
        "raw entry-minus-closing CLV semantics reintroduced outside the legacy helper: %s"
        % offenders)


# ------------------------------------------------------- migration behaviour
MANIFEST = os.path.join(REPO, "data/edgelab/analytics/clv_sign_migration_manifest.json")
RECEIPT = os.path.join(REPO, "data/edgelab/analytics/clv_sign_migration_receipt.json")
LEDGER = os.path.join(REPO, "data/edgelab/bets/bets.jsonl")


def test_migration_receipt_records_hashes_and_counts():
    r = json.load(open(RECEIPT))
    assert r["convention"] == CONVENTION_ID
    assert r["beforeSha256"] != r["afterSha256"]
    assert r["discrepancies"] == 0
    assert r["rowsChanged"] > 0


def test_migration_left_no_unexplained_discrepancy():
    m = json.load(open(MANIFEST))
    assert m["counts"].get("OTHER_DISCREPANCY", 0) == 0


def test_zero_rows_are_zero_and_null_rows_stayed_null():
    m = {r["betId"]: r for r in json.load(open(MANIFEST))["rows"]}
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    for row in rows:
        cls = m[row["betId"]]["classification"]
        if cls == "ZERO_UNAMBIGUOUS":
            assert abs(float(row["clv"])) <= 0.02
        elif cls == "UNRESOLVED_MISSING_SOURCE_FIELDS":
            assert row.get("clv") is None


def test_canonical_ledger_rows_carry_the_convention_marker():
    rows = [json.loads(l) for l in open(LEDGER) if l.strip()]
    stamped = [r for r in rows if r.get("clvConvention") == CONVENTION_ID]
    assert len(stamped) > 0
    for r in stamped:
        assert r.get("clvUnit") == UNIT_PERCENTAGE_POINTS


def test_migration_is_idempotent():
    """Re-running the migration must produce zero further changes."""
    import hashlib
    before = hashlib.sha256(open(LEDGER, "rb").read()).hexdigest()
    subprocess.run(["python3", os.path.join(REPO, "scripts/edgelab/migrate_clv_sign.py"),
                    "--apply"], cwd=REPO, capture_output=True, text=True, check=True)
    after = hashlib.sha256(open(LEDGER, "rb").read()).hexdigest()
    assert before == after, "migration is not idempotent"


def test_legacy_sourced_reports_declare_their_convention():
    """Reports reading the un-migratable legacy root bets.json must say so,
    rather than presenting inverted CLV as if it were canonical."""
    rep = json.load(open(os.path.join(REPO, "data/rule71_report.json")))
    assert rep["clvConvention"] == LEGACY_INVERTED_CONVENTION_ID
    assert "beat the close" in rep["clvConventionNote"]
