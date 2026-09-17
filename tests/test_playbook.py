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
    LESSONS_PATH,
    MIN_DISTINCT_DATES_FOR_SUPPORTED,
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
    drowning out the slate. The mission's bar is roughly 1,000 words of
    core methodology.
    """
    core = _read(PLAYBOOK_FULL).split("## 1. THESIS FIRST", 1)[1]
    assert len(core.split()) <= 1000, f"core methodology is {len(core.split())} words"


@pytest.mark.parametrize("section", [
    "THESIS FIRST, MARKET SECOND",
    "MARKET EXPRESSION",
    "PRICE IS PART OF THE BET",
    "DO NOT OVERSTACK ONE THESIS",
    "RECENT FORM",
    "MODEL VS HANDICAP",
    "FULL MARKET SEARCH",
    "LINEUPS AND FRESHNESS",
    "LEARNING WITHOUT OVERFITTING",
    "OUTPUT DISCIPLINE",
])
def test_playbook_covers_every_required_section(section):
    assert section in " ".join(_read(PLAYBOOK_FULL).split())


def test_playbook_never_declares_a_market_family_universally_best():
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "No market family is automatically superior" in text
    for forbidden in ("F5 always", "team totals always", "always bet F5"):
        assert forbidden.lower() not in text.lower()


def test_playbook_requires_full_market_search_for_unstarted_games():
    # Whitespace-normalized: the source is hard-wrapped, so a phrase can
    # legitimately straddle a newline.
    text = " ".join(_read(PLAYBOOK_FULL).split())
    assert "complete available market universe" in text
    assert "Do not stop at the first attractive market" in text


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
    assert 8 <= len(lines) <= 14, f"startup prompt is {len(lines)} lines; it must stay tiny"
    lowered = block.lower()
    for requirement in ("handicapping_playbook", "every available market", "thesis",
                        "correlation", "lineups", "against", "threshold",
                        "never assume", "playbook_version"):
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
    """The distinction the mission asked for: a single day's result must
    not be recorded as standing guidance."""
    by_id = {l["id"]: l for l in load_lessons(LESSONS_FULL)["lessons"]}
    assert by_id["f7-as-a-default-horizon"]["status"] == STATUS_HYPOTHESIS
    assert by_id["family-level-win-rates-are-not-yet-evidence"]["status"] == STATUS_HYPOTHESIS


# ── validation catches the failure modes it exists for ──────────────────

def _lesson(**overrides):
    base = {
        "id": "x", "status": STATUS_SUPPORTED, "title": "t", "guidance": "g", "tags": ["T"],
        "evidence": {"distinctDates": ["2026-01-01", "2026-01-02", "2026-01-03"], "examples": ["e"]},
    }
    base.update(overrides)
    return base


def test_a_single_result_cannot_be_recorded_as_supported():
    payload = {"lessons": [_lesson(evidence={"distinctDates": ["2026-01-01"], "examples": ["one bet lost"]})]}
    problems = validate_lessons(payload)
    assert any("claims SUPPORTED" in p for p in problems)
    assert str(MIN_DISTINCT_DATES_FOR_SUPPORTED) in " ".join(problems)


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
        "lessons": [_lesson(id="a", status=STATUS_HYPOTHESIS, promotionBlockedBy="one date only",
                            evidence={"distinctDates": ["2026-01-01"], "examples": ["one day"]})],
    }
    with open(path, "w") as f:
        json.dump(payload, f)
    before = _read(str(path))

    assert playbook_lessons.cmd_set_status(
        json.load(open(path)), "a", STATUS_SUPPORTED, "I feel strongly about it", str(path)) == 1
    assert _read(str(path)) == before, "a promotion that fails the bar must not be written"
    assert "refusing to write" in capsys.readouterr().err
