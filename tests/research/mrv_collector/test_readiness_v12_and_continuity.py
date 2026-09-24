"""
MRV readiness V1.2 (maturity window T-240..T-5, six core families) and the
self-dispatch continuity chain.  Deterministic, no network.

The V1.2 population tests build a persisted corpus directly (manifests,
cross-sections, MLB state, sportsbook joins, books) so every game's distance
to first pitch is exact.
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from lib.edgelab.research.mrv_collector import fetch as F, cycle as CY, health as H, odds_budget as OB, season_phase as SP
from lib.edgelab.research.mrv_collector import readiness_v12 as R
from lib.edgelab.research.mrv_collector.storage import Store
from tests.research.mrv_collector.fake_world import standard_world

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WF = os.path.join(REPO, ".github", "workflows", "research-mrv-prospective-capture.yml")
sys.path.insert(0, os.path.join(REPO, "scripts", "research", "mrv_collector"))
import chain_successor as CS  # noqa: E402

CYCLE = datetime(2026, 9, 23, 18, 0, 0, tzinfo=timezone.utc)
DATE = "2026-09-23"
ALL_CHECKS_TRUE = {k: True for k in ("cadence.medianGap", "cadence.p90Gap", "cadence.maxInWindowGap", "completeness.completeShare",
                                     "completeness.unaccountedRows", "completeness.failedShare", "sportsbook.ambiguousJoins",
                                     "sportsbook.booksPerEvent", "sportsbook.notBudgetDegraded", "timestamps.perFetchShare",
                                     "informationEvents.gamesWithStateShare", "informationEvents.lineupTransitions")}


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def write_cycle(root, games, *, run_id="MRV1_20260923T180000Z_aaaaaa", at=CYCLE, date=DATE):
    """
    games: [{pk, minutes (to first pitch at cycle start), families:[series], matched:bool,
             books:[(series, twoSided)], phase, officialDate}]
    """
    s = Store(root)
    s.write_manifest(date, run_id, {"runId": run_id, "cycleStartedAt": iso(at), "cycleCompletedAt": iso(at + timedelta(minutes=3)),
                                    "eligibleGamePks": [g["pk"] for g in games], "captureClass": "COMPLETE", "gameDate": date})
    tickers, joins, books, state = {}, [], [], []
    for g in games:
        start = at + timedelta(minutes=g["minutes"])
        state.append({"gamePk": g["pk"], "observedAt": iso(at + timedelta(minutes=2)), "state": {"scheduledStart": start.strftime("%Y-%m-%dT%H:%M:%SZ")}})
        for f in g.get("families", ()):
            tickers["%s-%d-X" % (f, g["pk"])] = {"s": f, "gamePk": g["pk"], "phase": g.get("phase", SP.REGULAR_SEASON),
                                                 "gameOfficialDate": g.get("officialDate", DATE)}
        joins.append({"runId": run_id, "gamePk": g["pk"] if g.get("matched") else None,
                      "status": "MATCHED" if g.get("matched") else "UNMATCHED"})
        for i, (series, two) in enumerate(g.get("books", ())):
            books.append({"runId": run_id, "gamePk": g["pk"], "seriesTicker": series, "marketTicker": "%s-%d-%d" % (series, g["pk"], i),
                          "respondedAt": iso(at + timedelta(seconds=30)),
                          "book": {"twoSided": two, "levelsYes": 3, "levelsNo": 3 if two else 0}})
    s.append_gz("kalshi_crosssection", date, [{"runId": run_id, "cycleStartedAt": iso(at), "tickers": tickers}])
    s.append_gz("mlb_state", date, state)
    s.append_gz("sportsbook_joins", date, joins)
    s.append_gz("kalshi_books", date, books)
    return s


def v12(root, dates=(DATE,)):
    return R.build_readiness_v12(Store(root), list(dates), {}, ALL_CHECKS_TRUE)


FULL = list(R.CORE_FAMILIES)
IN_WINDOW_OK = {"pk": 1, "minutes": 120, "families": FULL, "matched": True, "books": [("KXMLBTOTAL", True)]}


def test_definition_is_versioned_and_bound_to_the_predeclared_window_and_core_set():
    assert R.GATE_VERSION == "MRV_READINESS_GATES_V1_2_2026_09_24"
    assert (R.WINDOW_MAX_MINUTES, R.WINDOW_MIN_MINUTES) == (240.0, 5.0)
    assert R.CORE_FAMILIES == ("KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL", "KXMLBF5", "KXMLBF5TOTAL")
    g = R.GATES_V1_2
    assert g["familyCoverage"]["coreFamiliesPresentShareMin"] == H.GATES["familyCoverage"]["coreFamiliesPresentShareMin"] == 0.98
    assert g["familyCoverage"]["starvedGameCyclesMax"] == 0
    assert g["sportsbook"]["matchedGameShareMin"] == H.GATES["sportsbook"]["matchedGameShareMin"] == 0.90
    assert g["orderBook"]["twoSidedBookShareMin"] == g["orderBook"]["booksWithDepthShareMin"] == 0.95
    assert (g["sample"]["uniqueGamesMin"], g["sample"]["datesMin"], g["sample"]["contractsPerCoreFamilyMin"]) == (60, 10, 80)
    assert H.GATES["gateVersion"] == "MRV_READINESS_GATES_V1_1_2026_09_23"      # V1.1 kept, not edited


# 1 / 2 -------------------------------------------------------------- family coverage
def test_1_game_twelve_hours_out_with_only_the_game_market_does_not_fail_coverage(tmp_path):
    write_cycle(str(tmp_path), [IN_WINDOW_OK, {"pk": 2, "minutes": 720, "families": ["KXMLBGAME"]}])
    r = v12(str(tmp_path))
    assert r["metrics"]["familyCoverage"]["starvedGameCycles"] == 0
    assert r["metrics"]["gameCycles"]["byStatus"][R.BEFORE_WINDOW] == 1        # captured, reported, outside the window
    assert r["checks"]["familyCoverage.starvedGameCycles"] and r["checks"]["familyCoverage.coreFamiliesPresentShare"]


def test_2_same_game_at_t_minus_120_with_only_the_game_market_fails_coverage(tmp_path):
    write_cycle(str(tmp_path), [IN_WINDOW_OK, {"pk": 2, "minutes": 120, "families": ["KXMLBGAME"]}])
    r = v12(str(tmp_path))
    assert r["metrics"]["familyCoverage"]["starvedGameCycles"] == 1
    assert not r["checks"]["familyCoverage.starvedGameCycles"] and not r["checks"]["familyCoverage.coreFamiliesPresentShare"]


# 3 / 4 -------------------------------------------------------------- sportsbook
def test_3_missing_sportsbook_event_twelve_hours_out_does_not_hurt_matched_share(tmp_path):
    write_cycle(str(tmp_path), [IN_WINDOW_OK, {"pk": 2, "minutes": 720, "families": FULL, "matched": False}])
    r = v12(str(tmp_path))
    assert r["metrics"]["sportsbook"]["matchedGameShare"] == 1.0 and r["checks"]["sportsbook.matchedGameShare"]


def test_4_missing_sportsbook_event_at_t_minus_120_hurts_matched_share(tmp_path):
    write_cycle(str(tmp_path), [IN_WINDOW_OK, {"pk": 2, "minutes": 120, "families": FULL, "matched": False}])
    r = v12(str(tmp_path))
    assert r["metrics"]["sportsbook"]["matchedGameShare"] == 0.5 and not r["checks"]["sportsbook.matchedGameShare"]
    assert r["metrics"]["sportsbook"]["unmatchedInWindowGamePks"] == [2]


# 5 / 6 -------------------------------------------------------------- core two-sided books
def test_5_one_sided_inning_total_book_does_not_hurt_core_two_sided_share(tmp_path):
    g = dict(IN_WINDOW_OK, books=[("KXMLBTOTAL", True), ("KXMLBINNINGTOTAL", False)])
    r = v12(str(write_cycle(str(tmp_path), [g]).root))
    assert r["metrics"]["orderBook"]["core"]["twoSidedShare"] == 1.0 and r["checks"]["orderBook.twoSidedShare"]
    assert r["metrics"]["orderBook"]["nonCore"] == {"books": 1, "twoSidedShare": 0.0, "withDepthShare": 1.0}   # still reported


def test_6_one_sided_total_book_at_t_minus_120_hurts_core_two_sided_share(tmp_path):
    g = dict(IN_WINDOW_OK, books=[("KXMLBTOTAL", True), ("KXMLBTOTAL", False)])
    r = v12(str(write_cycle(str(tmp_path), [g]).root))
    assert r["metrics"]["orderBook"]["core"]["twoSidedShare"] == 0.5 and not r["checks"]["orderBook.twoSidedShare"]


# 7-10 --------------------------------------------------------------- window edges
@pytest.mark.parametrize("minutes,status", [(240, R.IN_WINDOW), (5, R.IN_WINDOW), (240.5, R.BEFORE_WINDOW), (241, R.BEFORE_WINDOW),
                                            (4.5, R.AFTER_WINDOW), (0, R.AFTER_WINDOW), (-30, R.AFTER_WINDOW)])
def test_7_to_10_window_edges(minutes, status):
    assert R.window_status(CYCLE + timedelta(minutes=minutes), CYCLE) == status
    assert R.window_status(None, CYCLE) == R.UNKNOWN_START


def test_7_to_10_window_edges_through_the_corpus(tmp_path):
    games = [dict(IN_WINDOW_OK, pk=1, minutes=240), dict(IN_WINDOW_OK, pk=2, minutes=5),
             {"pk": 3, "minutes": 241, "families": ["KXMLBGAME"]}, {"pk": 4, "minutes": 4, "families": ["KXMLBGAME"]}]
    r = v12(str(write_cycle(str(tmp_path), games).root))
    assert r["metrics"]["gameCycles"]["byStatus"] == {R.IN_WINDOW: 2, R.BEFORE_WINDOW: 1, R.AFTER_WINDOW: 1}
    assert r["metrics"]["familyCoverage"]["starvedGameCycles"] == 0 and r["metrics"]["sample"]["uniqueGames"] == 2


def test_game_with_unknown_start_is_excluded_and_counted_never_guessed(tmp_path):
    s = write_cycle(str(tmp_path), [IN_WINDOW_OK])
    s.write_manifest(DATE, "MRV1_20260923T181000Z_bbbbbb", {"runId": "MRV1_20260923T181000Z_bbbbbb", "cycleStartedAt": iso(CYCLE),
                                                            "cycleCompletedAt": iso(CYCLE), "eligibleGamePks": [99], "captureClass": "COMPLETE"})
    r = v12(str(tmp_path))
    assert r["metrics"]["gameCycles"]["byStatus"][R.UNKNOWN_START] == 1


# 11 / 12 ------------------------------------------------------------ season phase
def test_11_postseason_observations_are_excluded_from_the_regular_season_sample(tmp_path):
    write_cycle(str(tmp_path), [IN_WINDOW_OK, dict(IN_WINDOW_OK, pk=2, phase=SP.POSTSEASON),
                                dict(IN_WINDOW_OK, pk=3, phase=SP.OTHER_OR_UNKNOWN)])
    smp = v12(str(tmp_path))["metrics"]["sample"]
    assert smp["uniqueGames"] == 1 and smp["contractsPerCoreFamily"]["KXMLBGAME"] == 1
    assert smp["byPhase"][SP.POSTSEASON]["uniqueGames"] == 1 and smp["byPhase"][SP.OTHER_OR_UNKNOWN]["uniqueGames"] == 1


def test_12_five_regular_plus_five_postseason_dates_fail_the_ten_date_gate(tmp_path):
    dates = []
    for i in range(10):
        d = "2026-09-%02d" % (10 + i)
        dates.append(d)
        at = datetime(2026, 9, 10 + i, 18, 0, tzinfo=timezone.utc)
        ph = SP.REGULAR_SEASON if i < 5 else SP.POSTSEASON
        write_cycle(str(tmp_path), [dict(IN_WINDOW_OK, pk=100 + i, phase=ph, officialDate=d)],
                    run_id="MRV1_%sT180000Z_%06x" % (d.replace("-", ""), i), at=at, date=d)
    r = v12(str(tmp_path), dates)
    assert r["metrics"]["sample"]["dates"] == 5 and r["metrics"]["sample"]["byPhase"][SP.POSTSEASON]["dates"] == 5
    assert r["checks"]["sample.dates"] is False and r["researchReady"] is False


def test_contracts_listed_only_outside_the_window_do_not_count_toward_the_sample(tmp_path):
    write_cycle(str(tmp_path), [IN_WINDOW_OK, {"pk": 2, "minutes": 720, "families": FULL}])
    smp = v12(str(tmp_path))["metrics"]["sample"]
    assert smp["uniqueGames"] == 1 and all(v == 1 for v in smp["contractsPerCoreFamily"].values())


# 16 ------------------------------------------------------------------ budget degradation
def test_16_budget_degradation_keeps_kalshi_mlb_capture_and_cannot_satisfy_sportsbook_readiness(tmp_path):
    w = standard_world(odds_remaining=4000)
    OB.append_ledger(str(tmp_path), "2026-09-22", {"requestMade": True, "httpStatus": 200, "creditsCharged": 3, "requestsLast": "3",
                                                   "requestsRemaining": "4000", "respondedAt": "2026-09-22T19:50:00Z"})
    now = datetime(2026, 9, 22, 20, 0, 0, tzinfo=timezone.utc)
    f = F.Fetcher(transport=w.transport, clock=w.clock, sleeper=lambda s: None, base_sleep=0.0, min_sleep=0.0)
    ms = [CY.run_cycle(f, Store(str(tmp_path)), now=now + timedelta(minutes=10 * i), odds_api_key="k") for i in range(2)]
    assert all(m["odds"]["status"] == OB.STATUS_DEGRADED and m["captureClass"] == "COMPLETE" for m in ms) and w.odds_calls == 0
    assert all(m["reconciliation"]["booksReceived"] > 0 and m["written"]["kalshi_quotes"] + m["reconciliation"]["marketsReferenced"] > 0 for m in ms)
    rep = H.build_health(Store(str(tmp_path)), end_date="2026-09-22", days=1)
    r = rep["readinessV1_2"]
    assert r["metrics"]["sportsbook"]["gamesInWindow"] == 2 and r["metrics"]["sportsbook"]["matchedGameShare"] == 0.0
    assert r["checks"]["sportsbook.notBudgetDegraded"] is False and r["checks"]["sportsbook.matchedGameShare"] is False
    assert r["infrastructureHealthy"] is False and r["researchReady"] is False
    assert rep["gates"]["gateVersion"] == "MRV_READINESS_GATES_V1_1_2026_09_23"          # dual reporting: V1.1 still present


# 13-15 --------------------------------------------------------------- continuity chain
class Runner(object):
    def __init__(self, runs_json='[]', list_rc=0, dispatch_rc=0):
        self.calls, self.runs_json, self.list_rc, self.dispatch_rc = [], runs_json, list_rc, dispatch_rc

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "run", "list"]:
            return subprocess.CompletedProcess(cmd, self.list_rc, stdout=self.runs_json, stderr="")
        return subprocess.CompletedProcess(cmd, self.dispatch_rc, stdout="", stderr="")

    def dispatches(self):
        return [c for c in self.calls if c[:3] == ["gh", "workflow", "run"]]


ARGS = ["--workflow", "research-mrv-prospective-capture.yml", "--ref", "main"]


def test_13_a_completed_job_requests_exactly_one_successor_with_the_normal_inputs():
    r = Runner('[{"databaseId": 7, "status": "in_progress"}]')
    assert CS.main(ARGS, runner=r, env={"GITHUB_RUN_ID": "7"}) == 0
    assert r.dispatches() == [["gh", "workflow", "run", "research-mrv-prospective-capture.yml", "--ref", "main",
                               "-f", "budget_minutes=340", "-f", "cadence_minutes=10"]]


@pytest.mark.parametrize("status", ["queued", "pending", "waiting", "requested", "in_progress"])
def test_13_no_redundant_successor_when_another_run_is_already_active(status):
    r = Runner('[{"databaseId": 7, "status": "in_progress"}, {"databaseId": 8, "status": "%s"}]' % status)
    assert CS.main(ARGS, runner=r, env={"GITHUB_RUN_ID": "7"}) == 0
    assert r.dispatches() == []


def test_13_a_run_on_another_branch_is_not_a_successor():
    r = Runner('[{"databaseId": 7, "status": "in_progress", "headBranch": "main"}, {"databaseId": 9, "status": "queued", "headBranch": "feature"}]')
    CS.main(ARGS, runner=r, env={"GITHUB_RUN_ID": "7"})
    assert len(r.dispatches()) == 1


def test_13_a_queued_main_run_is_the_successor():
    r = Runner('[{"databaseId": 7, "status": "in_progress", "headBranch": "main"}, {"databaseId": 9, "status": "pending", "headBranch": "main"}]')
    CS.main(ARGS, runner=r, env={"GITHUB_RUN_ID": "7"})
    assert r.dispatches() == []


def test_13_unreadable_run_list_still_requests_one_successor_only():
    r = Runner(list_rc=1)
    CS.main(ARGS, runner=r, env={"GITHUB_RUN_ID": "7"})
    assert len(r.dispatches()) == 1


def test_13_a_failed_dispatch_is_a_visible_step_failure():
    assert CS.main(ARGS, runner=Runner(dispatch_rc=1), env={"GITHUB_RUN_ID": "7"}) == 1


@pytest.mark.parametrize("value", ["false", "FALSE", " 0 ", "no", "off", "disabled"])
def test_15_kill_switch_prevents_dispatch(value):
    r = Runner()
    assert CS.main(ARGS, runner=r, env={"GITHUB_RUN_ID": "7", "MRV_CONTINUOUS_CAPTURE_ENABLED": value}) == 0
    assert r.dispatches() == []


@pytest.mark.parametrize("value", [None, "", "true", "1", "yes"])
def test_15_unset_or_true_kill_switch_means_enabled(value):
    env = {"GITHUB_RUN_ID": "7"}
    if value is not None:
        env["MRV_CONTINUOUS_CAPTURE_ENABLED"] = value
    r = Runner()
    CS.main(ARGS, runner=r, env=env)
    assert len(r.dispatches()) == 1


def _wf():
    with open(WF) as f:
        return yaml.safe_load(f)


def test_14_only_a_fully_successful_default_branch_job_can_request_a_successor():
    wf = _wf()
    steps = wf["jobs"]["capture"]["steps"]
    last = steps[-1]
    assert last["name"] == "Request the successor capture job"
    assert last["if"] == "success() && github.ref_name == github.event.repository.default_branch"
    assert last["env"]["MRV_CONTINUOUS_CAPTURE_ENABLED"] == "${{ vars.MRV_CONTINUOUS_CAPTURE_ENABLED }}"
    assert last["run"].count("chain_successor.py") == 1 and "gh workflow run" not in last["run"]
    names = [s.get("name") for s in steps]
    for required in ("Bounded multi-cycle capture (read-only; persists after every cycle)", "Verify persistence stayed inside the MRV v1 path",
                     "Verify the rows reached the research branch"):
        assert names.index(required) < names.index(last["name"])
    assert sum("chain_successor.py" in (s.get("run") or "") or "gh workflow run" in (s.get("run") or "") for s in steps) == 1


def test_14_a_failed_cycle_fails_the_capture_step(tmp_path, monkeypatch):
    import run_loop as RL
    monkeypatch.setattr(RL.subprocess, "call", lambda *a, **k: 2)
    assert RL.main(["--budget-minutes", "5", "--cadence-minutes", "0", "--no-git", "--max-cycles", "1"]) == 1
    monkeypatch.setattr(RL.subprocess, "call", lambda *a, **k: 0)
    assert RL.main(["--budget-minutes", "5", "--cadence-minutes", "0", "--no-git", "--max-cycles", "1"]) == 0


def test_15_kill_switch_also_skips_cron_runs_and_permissions_stay_minimal():
    wf = _wf()
    assert wf["permissions"] == {"contents": "write", "actions": "write"}
    assert wf["concurrency"] == {"group": "mrv-prospective-capture-v1", "cancel-in-progress": False}
    job_if = wf["jobs"]["capture"]["if"]
    assert "github.event_name != 'schedule'" in job_if and "vars.MRV_CONTINUOUS_CAPTURE_ENABLED" in job_if
    for v in CS.KILL_VALUES:
        assert '"%s"' % v in job_if
    on = wf.get("on", wf.get(True))
    assert on["schedule"] == [{"cron": "11 * * * *"}] and "workflow_dispatch" in on      # cron kept as the fallback


def test_odds_guard_constants_unchanged():
    assert (OB.DAILY_CREDIT_CEILING, OB.RESERVE_REMAINING) == (450, 5000)
