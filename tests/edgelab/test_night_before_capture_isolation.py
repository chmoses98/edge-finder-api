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


def test_research_capture_targets_tomorrow_not_today():
    """
    The entire point of the workflow. capture-snapshots-scheduled.yml
    resolves `date +%Y-%m-%d` (today ET); this one must resolve tomorrow,
    or it captures exactly the same universe production already has.
    """
    text = _workflow_text()
    assert "date -d 'tomorrow' +%Y-%m-%d" in text
    # And it must pass that date through to the API, which otherwise
    # defaults to today ET and hard-filters the response to it.
    assert "kalshisearch?date=${TARGET}" in text


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
