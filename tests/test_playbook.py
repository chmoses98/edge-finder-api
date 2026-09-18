#!/usr/bin/env python3
"""
tests/test_playbook.py
==========================
The canonical handicapping playbook (Mission 4).

Two things are guarded here, and they are the two things that make the
playbook worth having:

  * every fresh slate session is REQUIRED to read one canonical
    methodology, and that methodology carries a visible version a later
    postmortem can match against;
  * lessons accumulate under a status and a citable evidence bar, so a
    single wager result can never rewrite the methodology.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.playbook import (  # noqa: E402
    CLASS_EMPIRICAL_EDGE,
    CLASS_MECHANICAL,
    LESSONS_PATH,
    MIN_DISTINCT_DATES_FOR_MECHANICAL,
    MIN_DISTINCT_DATES_FOR_SUPPORTED,
    VALID_EVIDENCE_CLASSES,
    PLAYBOOK_PATH,
    RENDERED_LESSONS_PATH,
    STATUS_HYPOTHESIS,
    STATUS_SUPPORTED,
    VALID_STATUSES,
    audit_against_postmortems,
    lesson_meets_promotion_bar,
    load_lessons,
    read_playbook_version,
    render_markdown,
    validate_lessons,
)

_spec = importlib.util.spec_from_file_location(
    "playbook_lessons", os.path.join(ROOT, "scripts", "playbook_lessons.py"))
playbook_lessons = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(playbook_lessons)

PLAYBOOK_FULL = os.path.join(ROOT, PLAYBOOK_PATH)
LESSONS_FULL = os.path.join(ROOT, LESSONS_PATH)
RUN_THE_SLATE = os.path.join(ROOT, "RUN_THE_SLATE.md")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ── the playbook itself ─────────────────────────────────────────────────

def test_playbook_exists_and_declares_a_version():
    assert os.path.isfile(PLAYBOOK_FULL)
    version = read_playbook_version(PLAYBOOK_FULL)
    assert version is not None, "a slate must be able to report WHICH methodology produced it"
    assert version.count(".") == 2


def test_core_methodology_stays_short_enough_to_read_every_run():
    """
    The playbook is only useful if an AI reads it on EVERY run without it
    drowning out the slate. The bar is roughly 1,000 words of core
    METHODOLOGY -- measured from section 1, since section 0 is a
    five-line terminology lookup rather than prose to read through.
    """
    text = _read(PLAYBOOK_FULL)
    core = text.split("## 1. GAME ELIGIBILITY", 1)[1]
    assert len(core.split()) <= 1000, f"core methodology is {len(core.split())} words"
    # ...and the whole file, glossary included, still fits comfortably.
    assert len(text.split()) <= 1400, f"whole playbook is {len(text.split())} words"


@pytest.mark.parametrize("section", [
    "VOCABULARY",
    "GAME ELIGIBILITY COMES FIRST",
    "THESIS FIRST, MARKET SECOND",
    "MARKET EXPRESSION",
    "PRICE AND BANKROLL ARE PART OF THE BET",
    "DO NOT OVERSTACK ONE THESIS",
    "RECENT FORM",
    "MODEL VS HANDICAP",
    "LINEUPS, FRESHNESS, EXECUTION",
    "LEARNING WITHOUT OVERFITTING",
    "OUTPUT DISCIPLINE",
])
def test_playbook_covers_every_required_section(section):
    assert section in " ".join(_read(PLAYBOOK_FULL).split())


def test_playbook_never_declares_a_market_family_universally_best():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "No family is automatically superior" in text
    assert "payoff structure, not an edge" in text
    for forbidden in ("F5 always", "team totals always", "always bet F5"):
        assert forbidden.lower() not in text.lower()


def test_playbook_gates_real_money_on_both_official_lineups():
    """The core operating requirement from the review."""
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "BOTH official lineups are confirmed" in text
    assert "lineupConfirmedOfficial" in text
    assert "Probable/projected lineups are **never** confirmed" in text
    assert "must not produce a real-money recommendation" in text


def test_playbook_requires_the_full_market_universe_for_eligible_games():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "inspect the **complete** market universe" in text
    assert "Compare **every available market** for the eligible game" in text


def test_playbook_defines_all_five_axes_in_its_terminology_block():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    for term in ("BETTING-ELIGIBLE GAME", "FULL MARKET UNIVERSE", "PRODUCTION MODEL SUPPORT",
                 "MANUAL HANDICAPPING ELIGIBILITY", "AUTOMATIC SETTLEMENT SUPPORT"):
        assert term in text


def test_playbook_requires_sizing_against_the_canonical_bankroll():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    # BOTH gates, and the fact that neither implies the other. A card can
    # honestly say sizingAllowed:true while its reader holds no number.
    assert "bankroll.sizingAllowed" in text
    assert "bankroll.numericBankrollAvailable" in text
    assert "one does not imply the other" in text
    assert "present **no dollar stakes**" in text
    assert "Never substitute a remembered or derived number" in text


def test_playbook_rejects_the_price_shape_superstition():
    """The correction: cheap is not an edge."""
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "Price shape is not edge" in text
    assert "no reason to prefer a cheap contract" in text


def test_playbook_marks_form_labels_as_descriptive_only():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "descriptive context only" in text
    assert "no demonstrated" in text and "out-of-sample predictive value" in text
    assert "no production weight uses it" in text


def test_playbook_states_both_evidence_classes():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "MECHANICAL" in text and "EMPIRICAL_EDGE" in text
    assert "registered, leakage-free, out-of-sample experiment" in text


def test_playbook_controls_correlated_exposure_explicitly():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "one exposure wearing five costumes" in text
    for axis in ("game", "team", "starting pitcher", "offensive thesis", "run-environment"):
        assert axis in text


def test_playbook_forbids_assuming_a_recommendation_was_placed():
    assert "Never record a recommended wager as placed" in " ".join(_read(PLAYBOOK_FULL).split())


def test_playbook_rejects_mean_only_hot_labels():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "Never label an offense hot from rolling mean runs/game alone" in text
    assert "OUTLIER_INFLATED" in text


# ── RUN_THE_SLATE wiring ────────────────────────────────────────────────

def test_run_the_slate_requires_the_playbook_at_the_start_of_every_analysis():
    text = " ".join(_read(RUN_THE_SLATE).split())
    assert "STEP 0 (MANDATORY)" in text
    assert "HANDICAPPING_PLAYBOOK.md" in text
    assert "PLAYBOOK_LESSONS.md" in text
    assert "Before any analysis, before any price" in text


def test_run_the_slate_carries_a_short_startup_prompt():
    """~8-12 lines: its job is to point at the durable memory, not repeat it."""
    text = _read(RUN_THE_SLATE)
    assert "START-OF-SLATE PROMPT" in text
    block = text.split("## START-OF-SLATE PROMPT", 1)[1].split("```")[1]
    lines = [line for line in block.strip().splitlines() if line.strip()]
    assert 10 <= len(lines) <= 14, f"startup prompt is {len(lines)} lines; it must stay tiny"
    lowered = " ".join(block.lower().split())   # the prompt is hard-wrapped
    for requirement in ("handicapping_playbook", "every available kalshi market", "thesis",
                        "correlated exposure", "lineups", "against", "threshold",
                        "never assume", "playbook_version", "bankroll", "unstarted",
                        # the consumer-visibility gate: a redacted card must
                        # never be read as permission to invent dollar stakes
                        "numeric", "no dollar stake sizes"):
        assert requirement in lowered, f"startup prompt does not cover {requirement!r}"


def test_run_the_slate_pre_scan_line_is_consistency_aware():
    text = _read(RUN_THE_SLATE)
    prescan = text.split("PRE-SCAN:", 1)[1][:1200]
    assert "med" in prescan and ">=4" in prescan and "OUTLIER-DEPENDENT" in prescan
    assert "offenseFormLine" in prescan
    assert "Do NOT recompute" in prescan


# ── lessons: structure and promotion discipline ─────────────────────────

def test_committed_lessons_are_structurally_valid():
    assert validate_lessons(load_lessons(LESSONS_FULL)) == []


def test_lesson_versions_match_the_playbook():
    payload = load_lessons(LESSONS_FULL)
    assert payload["playbookVersion"] == read_playbook_version(PLAYBOOK_FULL)


def test_every_lesson_carries_a_status_and_cited_evidence():
    for lesson in load_lessons(LESSONS_FULL)["lessons"]:
        assert lesson["status"] in VALID_STATUSES
        evidence = lesson["evidence"]
        assert evidence["distinctDates"], f"{lesson['id']} cites no dates"
        assert evidence["examples"], f"{lesson['id']} cites no examples"


def test_supported_lessons_all_clear_the_promotion_bar():
    for lesson in load_lessons(LESSONS_FULL)["lessons"]:
        if lesson["status"] == STATUS_SUPPORTED:
            assert lesson_meets_promotion_bar(lesson), lesson["id"]


def test_hypotheses_say_what_is_blocking_them():
    for lesson in load_lessons(LESSONS_FULL)["lessons"]:
        if lesson["status"] == STATUS_HYPOTHESIS:
            assert lesson.get("promotionBlockedBy"), lesson["id"]


def test_the_durable_repeated_findings_are_actually_recorded():
    """The lessons the postmortem corpus most repeatedly contains must be
    present -- otherwise the playbook is not the durable memory it claims
    to be."""
    ids = {l["id"] for l in load_lessons(LESSONS_FULL)["lessons"]}
    for expected in ("f5-three-way-tie-tax", "one-thesis-many-markets-is-one-exposure",
                     "best-expression-beats-favourite-family", "price-discipline-and-passing",
                     "recent-mean-runs-is-not-form"):
        assert expected in ids


def test_anecdotal_impressions_are_kept_at_hypothesis():
    """A run of same-direction days must not become standing guidance."""
    by_id = {l["id"]: l for l in load_lessons(LESSONS_FULL)["lessons"]}
    f7 = by_id["f7-as-a-default-horizon"]
    assert f7["status"] == STATUS_HYPOTHESIS
    assert f7["evidenceClass"] == CLASS_EMPIRICAL_EDGE
    assert "requires a registered" in f7["promotionBlockedBy"]


# ── validation catches the failure modes it exists for ──────────────────

def _lesson(**overrides):
    base = {
        "id": "x", "status": STATUS_SUPPORTED, "evidenceClass": CLASS_MECHANICAL,
        "title": "t", "guidance": "g", "tags": ["T"],
        "evidence": {"distinctDates": ["2026-01-01", "2026-01-02", "2026-01-03"], "examples": ["e"]},
    }
    base.update(overrides)
    return base


def test_a_single_result_cannot_be_recorded_as_supported():
    payload = {"lessons": [_lesson(evidence={"distinctDates": ["2026-01-01"], "examples": ["one bet lost"]})]}
    problems = validate_lessons(payload)
    assert any("shows no mechanism" in p for p in problems)
    assert str(MIN_DISTINCT_DATES_FOR_MECHANICAL) in " ".join(problems)


# ── evidence classes (correction pass, item 6) ──────────────────────────

def test_an_empirical_edge_claim_can_never_be_promoted_by_dates_alone():
    """
    The rule that stops a three-wager streak becoming a betting rule: a
    claim about MONEY needs an experiment, not a run of winners.
    """
    lesson = _lesson(
        evidenceClass=CLASS_EMPIRICAL_EDGE,
        evidence={"distinctDates": [f"2026-01-{d:02d}" for d in range(1, 31)],
                  "examples": ["thirty days of winners"]})
    assert lesson_meets_promotion_bar(lesson) is False
    problems = validate_lessons({"lessons": [lesson]})
    assert any("can never promote a claim about money" in p for p in problems)


def test_an_empirical_edge_claim_is_promoted_by_a_registered_out_of_sample_experiment():
    lesson = _lesson(
        evidenceClass=CLASS_EMPIRICAL_EDGE,
        evidence={"distinctDates": ["2026-01-01"], "examples": ["e"],
                  "registeredExperiments": ["MLB-RSCH-0036"],
                  "outOfSampleResult": "DESCRIPTIVE_ONLY across validation and holdout"})
    assert lesson_meets_promotion_bar(lesson) is True
    assert validate_lessons({"lessons": [lesson]}) == []


def test_an_experiment_id_without_an_out_of_sample_result_is_not_enough():
    lesson = _lesson(
        evidenceClass=CLASS_EMPIRICAL_EDGE,
        evidence={"distinctDates": ["2026-01-01"], "examples": ["e"],
                  "registeredExperiments": ["MLB-RSCH-0036"]})
    assert lesson_meets_promotion_bar(lesson) is False


def test_a_mechanical_claim_is_promoted_by_showing_the_mechanism():
    lesson = _lesson(
        evidenceClass=CLASS_MECHANICAL,
        evidence={"distinctDates": ["2026-01-01"], "examples": ["e"],
                  "mechanism": "a three-way contract has a tie branch"})
    assert lesson_meets_promotion_bar(lesson) is True


def test_a_lesson_without_an_evidence_class_is_rejected():
    lesson = _lesson()
    del lesson["evidenceClass"]
    problems = validate_lessons({"lessons": [lesson]})
    assert any("evidenceClass" in p for p in problems)


def test_an_unknown_evidence_class_is_rejected():
    problems = validate_lessons({"lessons": [_lesson(evidenceClass="VIBES")]})
    assert any("is not one of" in p and "MECHANICAL" in p for p in problems)


def test_every_committed_lesson_carries_a_valid_evidence_class():
    for lesson in load_lessons(LESSONS_FULL)["lessons"]:
        assert lesson["evidenceClass"] in VALID_EVIDENCE_CLASSES, lesson["id"]


def test_every_supported_empirical_edge_lesson_cites_an_experiment():
    for lesson in load_lessons(LESSONS_FULL)["lessons"]:
        if lesson["status"] == STATUS_SUPPORTED and lesson["evidenceClass"] == CLASS_EMPIRICAL_EDGE:
            assert lesson["evidence"].get("outOfSampleResult"), lesson["id"]
            assert lesson["evidence"].get("registeredExperiments"), lesson["id"]


def test_the_price_lesson_no_longer_institutionalises_a_price_shape():
    """The specific correction the review asked for."""
    by_id = {l["id"]: l for l in load_lessons(LESSONS_FULL)["lessons"]}
    lesson = by_id["price-discipline-and-passing"]
    # The GUIDANCE is what an analyst acts on -- it must carry no price
    # shape at all. (The retraction note deliberately still quotes the old
    # "50-55 cents" wording so the correction is auditable, which is why
    # this is scoped to `guidance` and `title`.)
    operative = (lesson["guidance"] + " " + lesson["title"]).lower()
    for superstition in ("50-55 cent", "near-even price", "buy near-even"):
        assert superstition not in operative
    assert "price shape is not edge" in operative
    assert "70-cent" in lesson["guidance"] or "70 cent" in lesson["guidance"]
    assert lesson["evidenceClass"] == CLASS_MECHANICAL
    assert lesson.get("supersedes"), "the retraction must be recorded, not silently edited away"


def test_market_family_lesson_makes_no_performance_claim():
    by_id = {l["id"]: l for l in load_lessons(LESSONS_FULL)["lessons"]}
    lesson = by_id["best-expression-beats-favourite-family"]
    assert lesson["evidenceClass"] == CLASS_MECHANICAL
    assert "payoff structure" in lesson["guidance"]
    assert "says nothing about which family is more profitable" in lesson["guidance"]


def test_an_unsourced_lesson_is_rejected():
    payload = {"lessons": [_lesson(evidence={"examples": ["trust me"]})]}
    assert any("distinctDates" in p for p in validate_lessons(payload))


def test_a_registered_experiment_counts_as_independent_evidence():
    lesson = _lesson(evidence={
        "distinctDates": ["2026-01-01"],
        "examples": ["MLB-RSCH-0005 found NO_USEFUL_SIGNAL over 12,800 team-games"],
    })
    assert lesson_meets_promotion_bar(lesson) is True


def test_duplicate_lesson_ids_are_rejected():
    payload = {"lessons": [_lesson(), _lesson()]}
    assert any("duplicate lesson id" in p for p in validate_lessons(payload))


def test_an_unknown_status_is_rejected():
    payload = {"lessons": [_lesson(status="PROBABLY_TRUE")]}
    assert any("is not one of" in p for p in validate_lessons(payload))


# ── audit / render / set-status ─────────────────────────────────────────

def test_audit_recounts_evidence_from_the_real_postmortem_corpus():
    payload = load_lessons(LESSONS_FULL)
    rows = audit_against_postmortems(payload, playbook_lessons.read_postmortem_findings(
        os.path.join(ROOT, "data", "edgelab", "postmortems", "*", "postmortem.json")))
    assert len(rows) == len(payload["lessons"])
    by_id = {r["id"]: r for r in rows}
    # The most-repeated finding in the corpus must show real tag support.
    assert len(by_id["one-thesis-many-markets-is-one-exposure"]["observedTagDates"]) >= 3


def test_audit_is_read_only():
    before = _read(LESSONS_FULL)
    playbook_lessons.cmd_audit(load_lessons(LESSONS_FULL))
    assert _read(LESSONS_FULL) == before


def test_rendered_lessons_file_is_in_sync_with_the_json():
    payload = load_lessons(LESSONS_FULL)
    expected = render_markdown(payload, playbook_version=read_playbook_version(PLAYBOOK_FULL))
    actual = _read(os.path.join(ROOT, RENDERED_LESSONS_PATH))
    assert actual == expected, (
        "PLAYBOOK_LESSONS.md is stale -- run `python3 scripts/playbook_lessons.py --render`"
    )


def test_set_status_requires_a_reason_and_records_it(tmp_path):
    path = tmp_path / "lessons.json"
    payload = {
        "schemaVersion": "1", "playbookVersion": "1.0.0", "lastUpdated": "2026-09-17",
        "lessons": [_lesson(id="a", status=STATUS_HYPOTHESIS, promotionBlockedBy="needs more dates")],
    }
    with open(path, "w") as f:
        json.dump(payload, f)

    assert playbook_lessons.cmd_set_status(
        json.load(open(path)), "a", STATUS_SUPPORTED, "three more same-direction dates", str(path)) == 0
    updated = json.load(open(path))["lessons"][0]
    assert updated["status"] == STATUS_SUPPORTED
    assert updated["statusHistory"][0]["from"] == STATUS_HYPOTHESIS
    assert updated["statusHistory"][0]["reason"] == "three more same-direction dates"


def test_set_status_refuses_to_write_an_invalid_result(tmp_path, capsys):
    path = tmp_path / "lessons.json"
    payload = {
        "schemaVersion": "1", "playbookVersion": "1.0.0", "lastUpdated": "2026-09-17",
        "lessons": [_lesson(id="a", status=STATUS_HYPOTHESIS, evidenceClass=CLASS_EMPIRICAL_EDGE,
                            promotionBlockedBy="no registered experiment",
                            evidence={"distinctDates": ["2026-01-01"], "examples": ["one day"]})],
    }
    with open(path, "w") as f:
        json.dump(payload, f)
    before = _read(str(path))

    assert playbook_lessons.cmd_set_status(
        json.load(open(path)), "a", STATUS_SUPPORTED, "I feel strongly about it", str(path)) == 1
    assert _read(str(path)) == before, "a promotion that fails the bar must not be written"
    assert "refusing to write" in capsys.readouterr().err
