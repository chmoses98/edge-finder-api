"""Tests for the monitoring-mode status reporter.

It exists to make monitoring cheap, so the things worth asserting are
that it stays READ-ONLY, that it starts nothing, and that reaching a
floor is never rendered as an approval.
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

import monitoring_status as ms  # noqa: E402

SOURCE = open(ms.__file__, encoding="utf-8").read()


class TestItIsReadOnly:
    def test_it_only_ever_writes_under_reports(self):
        """Every open(..., 'w') target must be the reports directory."""
        tree = ast.parse(SOURCE)
        write_calls = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                mode = [a for a in node.args[1:] if isinstance(a, ast.Constant)]
                if mode and "w" in str(mode[0].value):
                    write_calls += 1
                    joined = ast.dump(node.args[0])
                    assert "REPORTS_DIR" in joined, "writes outside data/edgelab/reports/"
        assert write_calls >= 1, "expected the reporter to write its output"

    @pytest.mark.parametrize("entity", ["observations", "model_evaluations", "settlements",
                                        "recommendations", "games", "bets"])
    def test_it_never_writes_a_canonical_entity(self, entity):
        assert 'partition_path("%s"' % entity not in SOURCE
        assert "upsert_records" not in SOURCE and "append_records" not in SOURCE

    def test_it_writes_no_experiment_or_control_registration(self):
        for banned in ("register_experiment", "register_control", "build_experiment_definition"):
            assert banned not in SOURCE


class TestItStartsNothing:
    def test_it_schedules_nothing_and_dispatches_nothing(self):
        """Scoped to IMPORTS and CALLS, not raw text.

        The module docstring legitimately contains the words "schedules"
        and "starts" in order to say it does neither; grepping the source
        flags that disclaimer as the offence."""
        tree = ast.parse(SOURCE)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for banned in ("subprocess", "requests", "urllib", "http", "socket", "croniter"):
            assert banned not in imported, f"the monitor reaches outside itself: {banned}"
        called = {getattr(c.func, "id", getattr(c.func, "attr", "")) or ""
                  for c in ast.walk(tree) if isinstance(c, ast.Call)}
        for banned in ("run", "Popen", "urlopen", "post", "put", "system"):
            assert banned not in called, f"the monitor executes something: {banned}"

    def test_it_is_not_wired_into_any_workflow(self):
        """Deliberate: a monitor that runs itself daily creates daily churn,
        which is the opposite of monitoring mode."""
        import glob
        for path in glob.glob(os.path.join(_ROOT, ".github", "workflows", "*.yml")):
            assert "monitoring_status.py" not in open(path, encoding="utf-8").read(), (
                f"{os.path.basename(path)} runs the monitor automatically")

    def test_it_declares_no_promotion_vocabulary(self):
        rendered = ms.render_markdown(ms.collect()).upper()
        for banned in ("APPROVED", "PROMOTE THIS", "PRODUCTION_APPROVED"):
            assert banned not in rendered


class TestItReportsTheRightThings:
    def test_it_reports_sprint_closed_and_monitoring_mode(self):
        status = ms.collect()
        assert status["sprintStatus"] == "CLOSED"
        assert status["mode"] == "PROSPECTIVE_MONITORING"
        assert status["closedOn"] == "2026-08-30"

    def test_all_five_triggers_are_listed(self):
        assert len(ms.collect()["triggers"]) == 5

    def test_the_f5_floor_matches_the_preregistered_one(self):
        assert ms.F5_GAME_FLOOR == 100

    def test_the_team_total_floors_match_the_registration(self):
        artifact = os.path.join(_ROOT, "data", "edgelab", "analytics",
                                "latest_mlb_rsch_0035_team_total_nb_shadow.json")
        if not os.path.exists(artifact):
            pytest.skip("RSCH-0035 artifact not present on this branch")
        payload = json.load(open(artifact, encoding="utf-8"))
        material = [c for c in payload["checkpoints"] if c["name"] == "CHECKPOINT_2_FIRST_MATERIAL"]
        assert material, "the material checkpoint disappeared from the registration"
        assert ms.TEAM_TOTAL_NB_GAME_FLOOR == material[0]["minGames"]
        assert ms.TEAM_TOTAL_NB_DATE_FLOOR == material[0]["minDates"]

    def test_an_empty_shadow_reports_pending_natural_cycle(self):
        tt = ms.collect()["teamTotalNbShadow"]
        if tt["capturedRows"] == 0:
            assert tt["health"] == "HEALTH_PENDING_NATURAL_CYCLE"

    def test_reaching_a_floor_is_described_as_a_review_trigger_not_an_approval(self):
        assert "trigger to REVIEW, never an approval" in ms.collect()["note"]

    def test_it_renders_without_raising(self):
        assert "Monitoring Status" in ms.render_markdown(ms.collect())

    def test_f5_short_by_is_never_negative(self):
        f5 = ms.collect()["f5"]
        if f5.get("shortBy") is not None:
            assert f5["shortBy"] >= 0
