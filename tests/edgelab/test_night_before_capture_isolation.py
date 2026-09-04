#!/usr/bin/env python3
"""
tests/edgelab/test_night_before_capture_isolation.py
====================================================
Guards the one property that makes
.github/workflows/research-night-before-capture.yml safe to run at all:
the research night-before capture must be INVISIBLE to production.

The night-before timing study added a workflow that fetches TOMORROW's
Kalshi MLB universe during tonight's evening -- a slate date the
production capture never requests. If those files landed in
data/kalshi_registry_snapshots/, production would treat a
research capture of tomorrow's markets as if it were a real slate
capture: scripts/edgelab/ingest_market_observations.py would ingest it
into the production observation store, the CLV collector would source
quotes from it, and lib/snapshot_retention.py would start pruning it on
the production 21-day clock.

So the research capture writes to its own directory, and these tests fail
if that separation is ever quietly undone.
"""
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from lib.edgelab.research import night_before_timing as nbt  # noqa: E402

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "research-night-before-capture.yml")
PRODUCTION_SNAPSHOT_DIR = "data/kalshi_registry_snapshots"
RESEARCH_SNAPSHOT_DIR = "data/kalshi_research_night_before_snapshots"


def _workflow_text():
    with open(WORKFLOW) as handle:
        return handle.read()


def test_workflow_parses_and_declares_only_contents_write():
    document = yaml.safe_load(_workflow_text())
    assert document["name"] == "Research Night-Before Market Capture"
    permissions = document["jobs"]["capture"]["permissions"]
    assert permissions == {"contents": "write"}


def test_research_capture_never_writes_into_the_production_snapshot_dir():
    text = _workflow_text()
    for line in text.splitlines():
        # The production directory may only appear inside explanatory comments.
        if PRODUCTION_SNAPSHOT_DIR in line:
            assert line.strip().startswith("#"), (
                "research capture references the production snapshot directory "
                f"outside a comment: {line!r}"
            )


def test_research_capture_resolves_its_target_through_the_tested_helper():
    """
    The entire point of the workflow. capture-snapshots-scheduled.yml
    resolves `date +%Y-%m-%d` (today ET); this one must resolve a
    night-before slate. It must do so through the tested helper, never a
    bare shell date expression -- see
    test_midnight_checkpoint_does_not_skip_a_slate for the bug that shell
    version had.
    """
    text = _workflow_text()
    assert "scripts/edgelab/research_night_before_target_date.py" in text
    # The old, buggy unconditional form must be gone outside of comments.
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        assert "date -d 'tomorrow'" not in line, (
            "workflow still resolves its target with an unconditional "
            f"`date -d 'tomorrow'`: {line!r}"
        )
    # And it must pass that date through to the API, which otherwise
    # defaults to today ET and hard-filters the response to it.
    assert "kalshisearch?date=${TARGET}" in text


def test_workflow_skips_firings_that_are_not_et_checkpoints():
    """The DST union schedule fires more often than it captures."""
    text = _workflow_text()
    assert "SKIP=true" in text and "SKIP=false" in text
    document = yaml.safe_load(text)
    steps = document["jobs"]["capture"]["steps"]
    guarded = [s for s in steps if s.get("if") == "env.SKIP == 'false'"]
    # fetch, archive and commit must all be gated -- a skipped firing must
    # not fetch, must not write a file, and must not commit.
    assert len(guarded) == 3, [s.get("name") for s in steps]


# --------------------------------------------------------------------------
# Target-date resolution (the midnight bug)
# --------------------------------------------------------------------------

def _target(now_iso):
    return nbt.night_before_target_slate_date(now_iso)


def test_evening_checkpoints_target_tomorrow():
    # 2026-08-15 20:00 ET == 2026-08-16 00:00 UTC (EDT).
    assert _target("2026-08-16T00:00:00Z") == "2026-08-16"
    # 2026-08-15 22:00 ET == 2026-08-16 02:00 UTC.
    assert _target("2026-08-16T02:00:00Z") == "2026-08-16"


def test_midnight_checkpoint_does_not_skip_a_slate():
    """
    THE REGRESSION. At 00:00 ET on 2026-08-16 the ET calendar has already
    rolled over, so `date -d 'tomorrow'` yields 2026-08-17 -- two slates
    ahead of the 20:00/22:00 captures taken a few hours earlier on the same
    evening, and one slate past the games about to be played. All three
    checkpoints of one evening must agree on ONE slate date.
    """
    evening_2000 = _target("2026-08-16T00:00:00Z")   # 20:00 ET Aug 15
    evening_2200 = _target("2026-08-16T02:00:00Z")   # 22:00 ET Aug 15
    midnight = _target("2026-08-16T04:00:00Z")       # 00:00 ET Aug 16
    assert evening_2000 == evening_2200 == midnight == "2026-08-16"


def test_target_date_is_dst_safe_across_the_fall_transition():
    """
    2026-11-01 is the EDT->EST switch. 20:00 ET is 00:00 UTC before it and
    01:00 UTC after; a cron pinned to one hour would silently drift onto a
    non-checkpoint. Resolution reads the real ET wall clock instead.
    """
    # EDT side: 2026-10-30 20:00 ET == 2026-10-31 00:00 UTC.
    assert _target("2026-10-31T00:00:00Z") == "2026-10-31"
    # EST side: 2026-11-04 20:00 ET == 2026-11-05 01:00 UTC.
    assert _target("2026-11-05T01:00:00Z") == "2026-11-05"
    # EST side midnight: 2026-11-05 00:00 ET == 2026-11-05 05:00 UTC.
    assert _target("2026-11-05T05:00:00Z") == "2026-11-05"
    # And the EST 22:00 checkpoint.
    assert _target("2026-11-05T03:00:00Z") == "2026-11-05"


def test_non_checkpoint_firings_skip():
    """The union schedule fires at 6 UTC hours; only 3 are real checkpoints."""
    for utc_hour in ("2026-08-16T01:00:00Z",   # 21:00 ET -- not a checkpoint
                     "2026-08-16T03:00:00Z",   # 23:00 ET -- not a checkpoint
                     "2026-08-16T05:00:00Z",   # 01:00 ET -- not a checkpoint
                     "2026-08-16T18:00:00Z"):  # 14:00 ET -- daytime
        assert _target(utc_hour) == nbt.CAPTURE_SKIP


def test_target_date_never_guesses_from_an_unusable_clock():
    for bad in (None, "", "not-a-timestamp", "2026-08-16T04:00:00"):
        assert _target(bad) == nbt.CAPTURE_SKIP


def test_research_capture_marks_every_file_as_research():
    text = _workflow_text()
    assert "'captureClass'] = 'RESEARCH_NIGHT_BEFORE'" in text
    assert "'productionBehaviorChanged'] = False" in text


def test_production_ingest_and_retention_do_not_read_the_research_dir():
    """
    Both production consumers of captured Kalshi snapshots must be blind to
    the research directory. Checked against their real source, so moving
    either one onto a shared glob breaks this test rather than silently
    pulling research captures into the production corpus.
    """
    for relative in ("scripts/edgelab/ingest_market_observations.py",
                     "lib/snapshot_retention.py",
                     "scripts/prune_kalshi_snapshots.py",
                     "scripts/edgelab/collect_clv.py"):
        path = os.path.join(ROOT, relative)
        if not os.path.exists(path):
            continue
        with open(path) as handle:
            source = handle.read()
        assert RESEARCH_SNAPSHOT_DIR not in source, (
            f"{relative} references the research night-before snapshot directory"
        )


def test_research_filenames_cannot_collide_with_production_filenames():
    """
    Production files are kalshi_search_<date>[_HHMM].json. A research file
    must not match that pattern even if someone later copies one across.
    """
    production_pattern = re.compile(r"^kalshi_search_\d{4}-\d{2}-\d{2}(_\d{4})?\.json$")
    sample = "night_before_2026-09-05_20260905T000000Z.json"
    assert not production_pattern.match(sample)
    assert "night_before_${TARGET}_${TS}.json" in _workflow_text()


# --------------------------------------------------------------------------
# MLB season / activity guard
#
# Purpose: stop the collector producing pointless empty commits every night
# through the winter. Explicitly NOT a "were there games" filter -- an
# in-season zero-market capture is real evidence about when Kalshi lists
# tomorrow's contracts, and must never be suppressed.
# --------------------------------------------------------------------------

def test_entire_regular_season_and_postseason_are_inside_the_window():
    """The guard must not clip either end of real baseball."""
    # Earliest realistic Opening Day, including international openers.
    for date in ("2026-03-01", "2026-03-20", "2026-03-26", "2026-04-01"):
        assert nbt.is_mlb_season_active_date(date), date
    # Regular season through the end of September.
    for date in ("2026-06-15", "2026-07-04", "2026-09-30"):
        assert nbt.is_mlb_season_active_date(date), date
    # THE POSTSEASON. Wild card through the latest plausible World Series
    # game, plus weeks of margin after it.
    for date in ("2026-10-01", "2026-10-15", "2026-10-31",
                 "2026-11-01", "2026-11-05", "2026-11-08", "2026-11-30"):
        assert nbt.is_mlb_season_active_date(date), date


def test_only_december_january_february_are_inactive():
    for date in ("2026-12-01", "2026-12-25", "2027-01-01",
                 "2027-01-31", "2027-02-01", "2027-02-28"):
        assert not nbt.is_mlb_season_active_date(date), date


def test_season_guard_covers_exactly_march_through_november():
    """Locks the documented window so a future edit cannot silently narrow it."""
    active = {m for m in range(1, 13)
              if nbt.is_mlb_season_active_date(f"2026-{m:02d}-15")}
    assert active == {3, 4, 5, 6, 7, 8, 9, 10, 11}
    assert nbt.MLB_SEASON_ACTIVE_FIRST_MONTH == 3
    assert nbt.MLB_SEASON_ACTIVE_LAST_MONTH == 11


def test_season_guard_fails_open_on_an_unreadable_date():
    """
    A guard that cannot parse its input must never be the reason a real
    capture is skipped. Fail open, not closed.
    """
    for bad in (None, "", "not-a-date", "2026-13-45", 12345):
        assert nbt.is_mlb_season_active_date(bad) is True


# --------------------------------------------------------------------------
# The collector's full go/no-go decision
# --------------------------------------------------------------------------

def test_in_season_checkpoints_capture():
    assert nbt.night_before_capture_decision("2026-08-16T00:00:00Z") == (
        "2026-08-16", nbt.CAPTURE_REASON_CHECKPOINT)
    assert nbt.night_before_capture_decision("2026-08-16T04:00:00Z") == (
        "2026-08-16", nbt.CAPTURE_REASON_CHECKPOINT)


def test_postseason_checkpoint_still_captures():
    """A November checkpoint is baseball, not offseason."""
    target, reason = nbt.night_before_capture_decision("2026-11-05T01:00:00Z")
    assert target == "2026-11-05"
    assert reason == nbt.CAPTURE_REASON_CHECKPOINT


def test_offseason_checkpoint_is_skipped_with_its_own_reason():
    target, reason = nbt.night_before_capture_decision("2026-01-16T01:00:00Z")
    assert target is None
    assert reason == nbt.CAPTURE_REASON_OFFSEASON


def test_non_checkpoint_reason_is_distinguished_from_offseason():
    """The two skip reasons must be separately loggable."""
    _, reason = nbt.night_before_capture_decision("2026-08-16T01:00:00Z")
    assert reason == nbt.CAPTURE_REASON_NOT_A_CHECKPOINT
    assert nbt.CAPTURE_REASON_NOT_A_CHECKPOINT != nbt.CAPTURE_REASON_OFFSEASON


def test_explicit_manual_target_bypasses_both_gates():
    """Manual workflow_dispatch stays usable for research, per spec."""
    target, reason = nbt.night_before_capture_decision(
        "2026-12-16T01:00:00Z", explicit_target_date="2026-12-17")
    assert target == "2026-12-17"
    assert reason == nbt.CAPTURE_REASON_EXPLICIT_TARGET
    # Also bypasses the checkpoint gate at a non-checkpoint hour.
    target, reason = nbt.night_before_capture_decision(
        "2026-08-16T13:00:00Z", explicit_target_date="2026-08-20")
    assert target == "2026-08-20"
    assert reason == nbt.CAPTURE_REASON_EXPLICIT_TARGET


def test_guard_does_not_suppress_zero_market_captures():
    """
    The guard is date-based only. Nothing in the decision path consults the
    number of markets returned, because an in-season zero-market capture is
    evidence that tomorrow's contracts were not yet listed.
    """
    # The decision is a pure function of (clock, explicit date). It takes no
    # market data at all, so it structurally cannot suppress a zero-market
    # capture -- it runs BEFORE the fetch.
    import inspect
    parameters = set(inspect.signature(nbt.night_before_capture_decision).parameters)
    assert parameters == {"now_utc", "explicit_target_date", "capture_hours"}

    # The workflow must not gate archive/commit on a non-zero market count
    # (the production capture workflow does exactly that, deliberately; this
    # research one must not).
    document = yaml.safe_load(_workflow_text())
    steps = document["jobs"]["capture"]["steps"]
    for step in steps:
        condition = str(step.get("if", ""))
        assert "markets_count" not in condition, (step.get("name"), condition)
        # The only gate on archive/commit is the skip flag.
        if step.get("name") in ("Archive research snapshot", "Commit research snapshot"):
            assert condition == "env.SKIP == 'false'", (step.get("name"), condition)


# --------------------------------------------------------------------------
# CLI contract the workflow depends on
# --------------------------------------------------------------------------

def _run_cli(*args):
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "edgelab",
                                      "research_night_before_target_date.py"), *args],
        capture_output=True, text=True, check=True, cwd=ROOT)
    return result.stdout.strip()


def test_cli_prints_a_bare_date_or_a_prefixed_skip_reason():
    assert _run_cli("--now", "2026-08-16T00:00:00Z", "--target-date", "") == "2026-08-16"
    assert _run_cli("--now", "2026-01-16T01:00:00Z", "--target-date", "") == \
        "SKIP:OUTSIDE_MLB_SEASON_WINDOW"
    assert _run_cli("--now", "2026-08-16T01:00:00Z", "--target-date", "") == \
        "SKIP:NOT_AN_ET_CHECKPOINT"
    assert _run_cli("--now", "2026-12-16T01:00:00Z",
                    "--target-date", "2026-12-17") == "2026-12-17"


def test_workflow_parses_the_skip_prefix_and_logs_both_reasons():
    text = _workflow_text()
    assert "SKIP:*)" in text
    assert "${DECISION#SKIP:}" in text
    assert "NOT_AN_ET_CHECKPOINT)" in text
    assert "OUTSIDE_MLB_SEASON_WINDOW)" in text
    assert "SKIP_REASON=$REASON" in text
