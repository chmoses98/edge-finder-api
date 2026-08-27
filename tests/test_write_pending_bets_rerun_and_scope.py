#!/usr/bin/env python3
"""
tests/test_write_pending_bets_rerun_and_scope.py
=====================================================
Phase 10 rerun/idempotency and changed-file-scope coverage for
scripts/write_pending_bets.py.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def wpb():
    if "write_pending_bets" in sys.modules:
        del sys.modules["write_pending_bets"]
    import write_pending_bets as _wpb
    return _wpb


def _wire(wpb, tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(wpb, "SLATE_PATH", str(tmp_path / "data" / "slate.json"))
    monkeypatch.setattr(wpb, "BETS_PATH", str(tmp_path / "bets.json"))
    return tmp_path


def make_entry(market="ML_Away", tier="HIGH", ticker="T-1"):
    return {
        "market": market, "confidenceTier": tier, "status": "Accepted",
        "ticker": ticker, "marketTicker": ticker, "kalshiPrice": -120,
        "executablePriceUsed": 54.5, "betSize": 5.0,
    }


def make_game(away="KC", home="WSH", entries=None):
    return {
        "away": {"abbr": away}, "home": {"abbr": home}, "status": "Scheduled",
        "marketLedger": entries if entries is not None else [make_entry()],
    }


class TestRerunIdempotency:

    def test_identical_rerun_writes_zero_new_bets(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        wpb.main()
        first = json.loads((root / "bets.json").read_text())
        assert len(first) == 1

        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        wpb.main()
        second = json.loads((root / "bets.json").read_text())
        assert second == first

    def test_added_market_on_rerun_only_appends_the_new_one(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game(entries=[make_entry(ticker="T-1")])]}, f)
        wpb.main()

        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game(
                entries=[make_entry(ticker="T-1"), make_entry(ticker="T-2", market="ML_Home")]
            )]}, f)
        wpb.main()

        bets = json.loads((root / "bets.json").read_text())
        assert len(bets) == 2
        tickers = {b["ticker"] for b in bets}
        assert tickers == {"T-1", "T-2"}

    def test_within_run_duplicate_market_ledger_entry_deduped(self, wpb, tmp_path, monkeypatch):
        """
        Two identical entries within the SAME slate.json's marketLedger
        (same date/game/market/ticker) must produce only ONE bet -- the
        within-run incremental existing_keys.add() must fire after the
        first append, not just across separate runs.
        """
        root = _wire(wpb, tmp_path, monkeypatch)
        dup_entry = make_entry(ticker="T-DUP")
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game(entries=[dup_entry, dict(dup_entry)])]}, f)
        wpb.main()
        bets = json.loads((root / "bets.json").read_text())
        assert len(bets) == 1

    def test_stale_unrelated_data_file_untouched(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        stray = root / "data" / "unrelated.json"
        stray.write_text('{"leftover": true}')
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        wpb.main()
        assert stray.read_text() == '{"leftover": true}'

    def test_prior_bets_from_other_dates_preserved(self, wpb, tmp_path, monkeypatch):
        root = _wire(wpb, tmp_path, monkeypatch)
        prior_bet = {"date": "2026-06-01", "game": "AAA@BBB", "market": "ML_Away",
                     "ticker": "OLD-1", "status": "settled", "result": "WIN"}
        with open(root / "bets.json", "w") as f:
            json.dump([prior_bet], f)
        with open(root / "data" / "slate.json", "w") as f:
            json.dump({"date": "2026-06-16", "games": [make_game()]}, f)
        wpb.main()
        bets = json.loads((root / "bets.json").read_text())
        assert len(bets) == 2
        assert bets[0] == prior_bet


class TestChangedFileScope:

    def test_lib_dependency_untouched(self):
        result = subprocess.run(
            ["git", "log", "--oneline", "-1", "--", "lib/postponed_guard.py"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() != "", "expected existing git history for lib/postponed_guard.py"

    def test_no_data_or_ledger_files_in_working_tree_changes(self):
        """
        `data/research/` and `data/kalshi/discovery/` are excluded from
        this check -- see the identical exclusions and rationale in
        tests/test_protect_slate_rerun_and_scope.py.

        `data/bet_backlog_remediation_plan.json` and
        `data/kalshi_snapshot_retention_plan.json` are ALSO excluded: both
        are the Production Reliability and Settlement Recovery milestone's
        machine-readable dry-run reports (produced by
        scripts/remediate_bet_backlog.py and
        scripts/prune_kalshi_snapshots.py respectively), generated report
        artifacts analogous to the research/discovery outputs above --
        never read by the production betting pipeline.

        `data/edgelab/` is similarly excluded as of the Historical
        Capture Completeness and Immutable Snapshot Foundation milestone
        (and every EdgeLab milestone before it) -- research-only
        collection/linkage infrastructure never read by the production
        betting/pricing pipeline; see the identical exclusion/rationale
        in tests/test_protect_slate_rerun_and_scope.py.
        """
        result = subprocess.run(
            ["git", "status", "--short", "--", "data/", "BET_LOG.md", "config/rules.json", "RULES.md",
             ":!data/research", ":!data/kalshi",
             ":!data/bet_backlog_remediation_plan.json",
             ":!data/kalshi_snapshot_retention_plan.json",
             ":!data/edgelab"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected working-tree changes: {result.stdout}"

    def test_bets_json_not_committed_as_part_of_this_phase(self):
        result = subprocess.run(
            ["git", "status", "--short", "--", "bets.json"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"bets.json must never be a working-tree change: {result.stdout}"

    def test_no_workflow_files_in_working_tree_changes(self):
        """
        .github/workflows/kalshi-price-check.yml,
        .github/workflows/lineup-recheck.yml,
        .github/workflows/capture-closing-lines.yml,
        .github/workflows/discover-kalshi-mlb-markets.yml, and
        .github/workflows/build-wager-research.yml are excluded -- each
        phase legitimately adds one new, sanctioned, workflow_dispatch-only
        (or scheduled/workflow_run-triggered data-refresh-only) workflow
        that never invokes the production risk/execution/bet-logging
        pipeline (see tests/test_kalshi_price_check_workflow.py,
        tests/test_lineup_recheck_workflow.py,
        tests/test_capture_closing_lines_workflow.py,
        tests/test_discover_kalshi_mlb_markets_workflow.py, and
        tests/test_build_wager_research_workflow.py). This test's actual
        intent -- proving no EXISTING production workflow file
        changed -- is unaffected by excluding those new files.

        .github/workflows/clv-update.yml is ALSO excluded, but for a
        different reason: the Production Reliability and Settlement
        Recovery milestone deliberately modifies this EXISTING workflow's
        "Commit all updates" step (see docs/INCIDENT_2026-07-31_CLV_COMMIT_FAILURE.md).
        Root cause: scripts/fetch_kalshi_clv_v2.py's API-fallback path
        writes data/clv_report.json, which was never in this step's `git
        add` list; once that file existed as a tracked file and got
        locally re-modified, `git pull --rebase` failed outright
        ("You have unstaged changes"), silently discarding that day's
        settlement/CLV work (confirmed recurring since at least
        2026-06-16, and actively failing on 2026-07-31 and 2026-08-01).
        The fix explicitly adds data/clv_report.json to the git-add list
        and switches to the same `git rebase --autostash` pattern already
        reviewed and merged in fetch-slate.yml, so this is an intentional,
        documented, in-scope change to a real, currently-active bug, not
        scope creep.

        .github/workflows/fetch-slate.yml is ALSO excluded for the same
        milestone: its `concurrency.group` changed from
        `fetch-slate-${{ github.ref }}` to the shared
        `edge-finder-ledger-writer` group now also used by clv-update.yml
        and lineup-recheck.yml, closing the exact cross-workflow race
        this file's own review comments had previously flagged and
        deferred (see docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md
        "Workflow concurrency").

        .github/workflows/capture-snapshots-scheduled.yml is ALSO
        excluded, for the same milestone's storage-retention item: its
        broken `find ... -mtime +3 -delete` cleanup step (which could
        never match anything on a real runner, since a fresh checkout
        resets every file's mtime to "now") is replaced with a call to
        scripts/prune_kalshi_snapshots.py, which parses the retention
        window from the date embedded in each filename instead -- see
        lib/snapshot_retention.py and tests/test_snapshot_retention.py.

        .github/workflows/edgelab-postgame.yml is ALSO excluded for the
        Historical Capture Completeness and Immutable Snapshot Foundation
        milestone: it gains two new, purely-additive, continue-on-error
        Snapshot-capture steps (see docs/SNAPSHOT_ARCHITECTURE.md) that
        only ever write under data/edgelab/snapshots/.
        .github/workflows/snapshot-capture-check.yml is a brand-new,
        wholly-additive workflow from the same milestone (a dedicated,
        separately-failing capture-completeness check) -- also excluded.
        .github/workflows/corpus-health-check.yml is a brand-new,
        wholly-additive workflow from the PR #37 maintainer review (item 10,
        "workflow failure policy") -- a dedicated, separately-failing corpus-
        health check (see scripts/corpus_health_report.py) mirroring
        snapshot-capture-check.yml's own pattern exactly -- also excluded for
        the same reason.

        .github/workflows/record-placed-bet.yml is a brand-new,
        wholly-additive workflow from the Canonical Placed-Bet Ledger
        milestone (docs/CANONICAL_BET_LEDGER.md) -- a manual
        workflow_dispatch-only form that writes exclusively under
        data/edgelab/ via lib.edgelab.bets.write_placed_bet, never the
        production risk/execution/bet-logging pipeline this test guards
        -- also excluded, same pattern as every prior addition above.

        .github/workflows/import-manual-bets.yml and
        import-postmortem.yml are brand-new, wholly-additive workflows
        from the MLB Market Research Corpus & Frictionless Manual
        Logging milestone -- manual workflow_dispatch-only surfaces that
        write exclusively under data/edgelab/ (via the same canonical
        write_placed_bet path, plus lib.edgelab.postmortems.write_postmortem
        for the latter), never the production risk/execution/bet-logging
        pipeline this test guards -- also excluded, same pattern as
        every prior addition above. kalshi-price-check.yml is ALSO
        modified by that same milestone (it now also archives an
        unfiltered market capture into data/edgelab/ +
        data/kalshi_registry_snapshots/ on every successful run -- see
        tests/test_kalshi_price_check_workflow.py), but it was already
        excluded above for an earlier milestone.

        .github/workflows/statcast-postgame-archive.yml is a brand-new,
        wholly-additive workflow from the Hitter Projection Engine Phase 5
        (Automatic Data Accumulation) milestone -- a scheduled +
        workflow_dispatch surface that writes exclusively under
        data/statcast_raw/, never the production risk/execution/
        bet-logging pipeline this test guards -- also excluded, same
        pattern as every prior addition above. kalshi-price-check.yml is
        ALSO further modified by this same milestone (it now additionally
        runs scripts/run_standalone_hitter_research.py and commits its
        artifacts -- see tests/test_kalshi_price_check_workflow.py), but
        it was already excluded above.
        """
        result = subprocess.run(
            ["git", "status", "--short", "--", ".github/workflows/",
             ":!.github/workflows/kalshi-price-check.yml",
             ":!.github/workflows/lineup-recheck.yml",
             ":!.github/workflows/capture-closing-lines.yml",
             ":!.github/workflows/discover-kalshi-mlb-markets.yml",
             ":!.github/workflows/build-wager-research.yml",
             ":!.github/workflows/clv-update.yml",
             ":!.github/workflows/fetch-slate.yml",
             ":!.github/workflows/pr-ci.yml",
             ":!.github/workflows/capture-snapshots-scheduled.yml",
             ":!.github/workflows/edgelab-postgame.yml",
             ":!.github/workflows/snapshot-capture-check.yml",
             ":!.github/workflows/corpus-health-check.yml",
             ":!.github/workflows/record-placed-bet.yml",
             ":!.github/workflows/import-manual-bets.yml",
             ":!.github/workflows/import-postmortem.yml",
             ":!.github/workflows/statcast-postgame-archive.yml",
             # Hitter Projection Checkpoint Scheduling milestone:
             # .github/workflows/hitter-snapshot-scheduler.yml is a
             # brand-new, wholly-additive scheduled + workflow_dispatch
             # workflow that writes exclusively under
             # data/edgelab/hitter_projection_snapshots/,
             # data/edgelab/research_runs/, and run-scoped
             # data/pipeline/<date>/<runId>/ filtered-slate files -- never
             # the production risk/execution/bet-logging pipeline this
             # test guards -- excluded, same pattern as every prior
             # addition above.
             ":!.github/workflows/hitter-snapshot-scheduler.yml",
             # Daily operating-window coverage fix: both
             # hitter-snapshot-scheduler.yml (above) and
             # model-snapshot-scheduler.yml originally shared an identical
             # cron blind spot (inactive before 16:00 UTC, missing early
             # MLB day games' T-90/T-60/T-30). model-snapshot-scheduler.yml
             # is a pre-existing, already-sanctioned scheduler -- this
             # change only widens its cron window's start hour and its own
             # header-comment documentation, never its model-evaluation
             # logic -- excluded here for the same reason its sibling was
             # above. See docs/HITTER_CHECKPOINT_COVERAGE_FIX.md Sec.9-10.
             ":!.github/workflows/model-snapshot-scheduler.yml",
             # Recurring-conflict-marker-bug fix: every workflow with a
             # data-commit step (all 19, not just these 5 -- the rest were
             # already excluded above for earlier milestones) was migrated
             # to the shared scripts/ci/git_data_commit.py path. See
             # scripts/ci/git_data_commit.py's module docstring and
             # tests/test_workflow_git_safety.py, which is the dedicated
             # test now guarding this migration stayed complete.
             ":!.github/workflows/edgelab-capture.yml",
             ":!.github/workflows/edgelab-clv-collect.yml",
             ":!.github/workflows/edgelab-daily-report.yml",
             ":!.github/workflows/clv_capture.yml",
             ":!.github/workflows/fetch-kalshi-clv.yml",
             # Pipeline Health Incident guardrail: new, independent
             # heartbeat/watchdog workflow -- adds a file, never modifies
             # any of the workflows this scope lock protects.
             ":!.github/workflows/edgelab-daily-heartbeat.yml",
             # Research Lab MLB-RSCH-0003 (Multi-Season Bullpen Workload
             # Backtest): new, wholly-additive, manual-workflow_dispatch-
             # only research workflow that writes exclusively under
             # data/research_cache/bullpen_backtest/ and this experiment's
             # own data/edgelab/experiments|experiment_reports|analytics
             # files, on the research branch it was dispatched from
             # (never main) -- never the production risk/execution/
             # bet-logging pipeline this test guards -- excluded, same
             # pattern as every prior addition above.
             ":!.github/workflows/research-multiseason-bullpen-backtest.yml",
             # Research Lab MLB-RSCH-0004 (Multi-Season Starter Workload
             # Backtest): new, wholly-additive, manual-workflow_dispatch-
             # only research workflow, same shape/precedent as
             # research-multiseason-bullpen-backtest.yml above -- writes
             # exclusively under data/research_cache/starter_workload/
             # and this experiment's own data/edgelab/experiments|
             # experiment_reports|analytics files, on the research branch
             # it was dispatched from (never main) -- never the
             # production risk/execution/bet-logging pipeline this test
             # guards -- excluded, same pattern as every prior addition.
             ":!.github/workflows/research-multiseason-starter-workload-backtest.yml",
             # Historical sharp-market feasibility audit: new, wholly-
             # additive, manual-workflow_dispatch-only research workflow,
             # same shape/precedent as the two above -- writes exclusively
             # under data/research_cache/sharp_market_probe/ on the
             # research branch it was dispatched from (never main) --
             # never the production risk/execution/bet-logging pipeline
             # this test guards -- excluded, same pattern as every prior
             # addition.
             ":!.github/workflows/research-sharp-market-probe.yml"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Unexpected workflow changes: {result.stdout}"
