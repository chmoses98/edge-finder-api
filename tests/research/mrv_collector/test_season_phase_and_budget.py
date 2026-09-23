"""Season-phase provenance (MRV_SEASON_PHASE_RULE_V1) and the Odds API budget guard (MRV_ODDS_BUDGET_GUARD_V1). No network."""
import json
import os
from datetime import datetime, timezone

import pytest

from lib.edgelab.research.mrv_collector import fetch as F, cycle as CY, health as H, odds_budget as OB, season_phase as SP
from lib.edgelab.research.mrv_collector.storage import Store
from tests.research.mrv_collector.fake_world import standard_world, sched_game, feed

NOW = datetime(2026, 9, 22, 20, 0, 0, tzinfo=timezone.utc)


def _run(world, root, **kw):
    f = F.Fetcher(transport=world.transport, clock=world.clock, sleeper=lambda s: None, base_sleep=0.0, min_sleep=0.0)
    return CY.run_cycle(f, Store(root), now=kw.pop("now", NOW), odds_api_key=kw.pop("odds_api_key", "k"), **kw)


# ---------------------------------------------------------------- season phase

def test_game_type_codes_map_to_documented_phases_and_never_guess():
    assert SP.phase_for_code("R") == SP.REGULAR_SEASON
    for c in ("F", "D", "L", "W", "C", "P"):
        assert SP.phase_for_code(c) == SP.POSTSEASON
    for c in ("S", "E", "A", "I", "N", "X", "", None):
        assert SP.phase_for_code(c) == SP.OTHER_OR_UNKNOWN
    assert SP.resolve("R", "R")["seasonPhase"] == SP.REGULAR_SEASON
    assert SP.resolve(None, "D")["seasonPhase"] == SP.POSTSEASON and SP.resolve(None, "D")["phaseBasis"] == "FEED"
    conflict = SP.resolve("R", "D")
    assert conflict["seasonPhase"] == SP.OTHER_OR_UNKNOWN and conflict["phaseBasis"] == "SCHEDULE_FEED_CONFLICT"
    assert SP.resolve(None, None)["seasonPhase"] == SP.OTHER_OR_UNKNOWN


def test_phase_is_carried_through_state_rows_crosssection_joins_and_manifest(tmp_path):
    w = standard_world()
    w.games[1]["gameType"] = "F"                       # NYY@BOS becomes a Wild Card game
    w.feeds[700002] = feed(status="Pre-Game", away_lu=(4, 5), home_lu=(6, 7), game_type="F")
    m = _run(w, str(tmp_path))
    s = Store(str(tmp_path))
    states = {r["gamePk"]: r for r in s.iter_gz("mlb_state", m["gameDate"])}
    assert states[700001]["seasonPhase"] == SP.REGULAR_SEASON and states[700001]["gameType"] == "R"
    assert states[700002]["seasonPhase"] == SP.POSTSEASON and states[700002]["gameType"] == "F"
    assert states[700001]["seasonPhaseRule"] == SP.SEASON_PHASE_RULE_VERSION
    x = list(s.iter_gz("kalshi_crosssection", m["gameDate"]))[0]["tickers"]
    assert x["KXMLBGAME-26SEP221910PITCWS-PIT"]["phase"] == SP.REGULAR_SEASON
    assert x["KXMLBGAME-26SEP221910NYYBOS-NYY"]["phase"] == SP.POSTSEASON
    assert x["KXMLBGAME-26SEP221910NYYBOS-NYY"]["gameOfficialDate"] == "2026-09-22"
    joins = {j["eventId"]: j for j in s.iter_gz("sportsbook_joins", m["gameDate"])}
    assert joins["e1"]["seasonPhase"] == SP.REGULAR_SEASON and joins["e2"]["seasonPhase"] == SP.POSTSEASON
    assert m["eligibleGamesByPhase"] == {SP.REGULAR_SEASON: 2, SP.POSTSEASON: 1, SP.OTHER_OR_UNKNOWN: 0}


def test_missing_game_type_is_other_or_unknown_not_regular(tmp_path):
    w = standard_world()
    del w.games[0]["gameType"]
    w.feeds[700001] = feed(away_lu=(1, 2, 3), game_type=None)
    w.feeds[700001]["gameData"]["game"] = {}
    m = _run(w, str(tmp_path))
    x = list(Store(str(tmp_path)).iter_gz("kalshi_crosssection", m["gameDate"]))[0]["tickers"]
    assert x["KXMLBGAME-26SEP221910PITCWS-PIT"]["phase"] == SP.OTHER_OR_UNKNOWN


def _write_day(store, official_date, phase, n_games, base_pk):
    """Synthetic persisted corpus for one ET date: a COMPLETE manifest and a cross-section with n_games x 6 core families x 15 contracts."""
    run_id = "MRV1_%sT200000Z_000001" % official_date.replace("-", "")
    tickers = {}
    for gi in range(n_games):
        pk = base_pk + gi
        for fam in ("KXMLBGAME", "KXMLBTOTAL", "KXMLBSPREAD", "KXMLBTEAMTOTAL", "KXMLBF5", "KXMLBF5TOTAL"):
            for c in range(15):
                tickers["%s-%d-%d" % (fam, pk, c)] = {"gamePk": pk, "s": fam, "phase": phase, "gameOfficialDate": official_date, "q": "x"}
    store.append_gz("kalshi_crosssection", official_date, [{"runId": run_id, "tickers": tickers}])
    store.write_manifest(official_date, run_id, {"runId": run_id, "gameDate": official_date, "cycleStartedAt": official_date + "T20:00:00.000000Z",
                                                 "captureClass": "COMPLETE", "reconciliation": {"unaccountedRows": 0, "gamesWithMarkets": n_games},
                                                 "starvation": {"count": 0}, "oddsStatus": "OK", "eligibleGamePks": list(range(base_pk, base_pk + n_games))})


def test_five_regular_plus_five_postseason_dates_do_not_satisfy_the_ten_date_regular_gate(tmp_path):
    s = Store(str(tmp_path))
    for i in range(5):
        _write_day(s, "2026-09-%02d" % (21 + i), SP.REGULAR_SEASON, 8, 800000 + i * 100)
    for i in range(5):
        _write_day(s, "2026-09-%02d" % (26 + i), SP.POSTSEASON, 8, 900000 + i * 100)
    rep = H.build_health(s, end_date="2026-09-30", days=10)
    cov = rep["metrics"]["coverage"]
    assert cov["byPhase"][SP.REGULAR_SEASON]["dates"] == 5 and cov["byPhase"][SP.POSTSEASON]["dates"] == 5
    assert cov["dates"] == 5 and cov["uniqueGames"] == 40        # regular only; 80 games / 10 dates exist in total
    assert cov["cycleDates"] == 10
    assert rep["gates"]["checks"]["sample.dates"] is False
    assert rep["gates"]["checks"]["sample.uniqueGames"] is False
    assert rep["gates"]["researchReady"] is False


def test_ten_regular_season_dates_do_satisfy_the_date_and_game_floor(tmp_path):
    s = Store(str(tmp_path))
    for i in range(10):
        _write_day(s, "2026-09-%02d" % (13 + i), SP.REGULAR_SEASON, 7, 800000 + i * 100)
    rep = H.build_health(s, end_date="2026-09-22", days=10)
    chk = rep["gates"]["checks"]
    assert rep["metrics"]["coverage"]["dates"] == 10 and rep["metrics"]["coverage"]["uniqueGames"] == 70
    assert chk["sample.dates"] and chk["sample.uniqueGames"] and chk["sample.contractsPerCoreFamily"]


def test_sample_floor_is_not_weakened():
    s = H.GATES["sample"]
    assert (s["uniqueGamesMin"], s["datesMin"], s["contractsPerCoreFamilyMin"]) == (60, 10, 80)
    assert H.GATES["samplePhase"] == SP.REGULAR_SEASON


# ---------------------------------------------------------------- odds budget guard

def test_healthy_quota_captures_sportsbook_and_records_spend(tmp_path):
    w = standard_world(odds_remaining=14000)
    m = _run(w, str(tmp_path))
    assert m["odds"]["status"] == OB.STATUS_OK and m["odds"]["requestMade"] and m["odds"]["creditsConsumedThisCycle"] == 3
    assert m["written"]["sportsbook_odds"] > 0 and m["odds"]["spentTodayAfter"] == 3
    led = OB.read_ledger(str(tmp_path), m["gameDate"])
    assert len(led) == 1 and led[0]["creditsCharged"] == 3 and led[0]["requestsRemaining"] == "13997" and led[0]["chargeBasis"] == "PROVIDER_HEADER"


def test_daily_ceiling_of_450_credits_is_enforced(tmp_path):
    w = standard_world(odds_remaining=100000, odds_cost=200)
    ms = [_run(w, str(tmp_path), now=NOW.replace(minute=i * 10)) for i in range(4)]
    assert [m["odds"]["status"] for m in ms] == [OB.STATUS_OK, OB.STATUS_OK, OB.STATUS_DEGRADED, OB.STATUS_DEGRADED]
    assert ms[2]["odds"]["reason"] == "DAILY_CEILING_REACHED" and not ms[2]["odds"]["requestMade"]
    assert w.odds_calls == 2 and OB.spent_today(str(tmp_path), ms[0]["gameDate"]) == 400 <= OB.DAILY_CREDIT_CEILING
    assert ms[3]["written"]["sportsbook_odds"] == 0 and ms[3]["joins"] == {"matched": 0, "ambiguous": 0, "unmatched": 0}


def test_guard_decision_arithmetic_at_the_ceiling(tmp_path):
    root, d = str(tmp_path), "2026-09-22"
    OB.append_ledger(root, d, {"requestMade": True, "httpStatus": 200, "creditsCharged": 447, "requestsLast": "3",
                               "requestsRemaining": "9000", "respondedAt": "2026-09-22T20:00:00Z"})
    assert OB.decide(root, d)["allowed"] is True                     # 447 + 3 == 450
    OB.append_ledger(root, d, {"requestMade": True, "httpStatus": 200, "creditsCharged": 1, "requestsLast": "3",
                               "requestsRemaining": "8997", "respondedAt": "2026-09-22T20:10:00Z"})
    dec = OB.decide(root, d)
    assert dec["allowed"] is False and dec["reason"] == "DAILY_CEILING_REACHED"   # 448 + 3 > 450


def test_remaining_below_5000_stops_sportsbook_requests(tmp_path):
    w = standard_world(odds_remaining=5002, odds_cost=3)
    m1 = _run(w, str(tmp_path))
    assert m1["odds"]["requestMade"] and m1["odds"]["status"] == OB.STATUS_DEGRADED
    assert m1["odds"]["reason"] == "REMAINING_BELOW_RESERVE_AFTER_REQUEST"      # response showed 4999
    m2 = _run(w, str(tmp_path), now=NOW.replace(minute=10))
    assert not m2["odds"]["requestMade"] and m2["odds"]["reason"] == "REMAINING_BELOW_RESERVE"
    assert m2["odds"]["guard"]["remainingEvidence"] == 4999 and w.odds_calls == 1


def test_missing_remaining_header_degrades_rather_than_assuming_quota(tmp_path):
    w = standard_world(odds_omit_remaining=True)
    _run(w, str(tmp_path))
    m2 = _run(w, str(tmp_path), now=NOW.replace(minute=10))
    assert not m2["odds"]["requestMade"] and m2["odds"]["reason"] == "REMAINING_QUOTA_UNKNOWN" and w.odds_calls == 1


def test_kalshi_and_mlb_collection_continue_after_sportsbook_cutoff(tmp_path):
    w = standard_world(odds_remaining=4000)
    OB.append_ledger(str(tmp_path), "2026-09-22", {"requestMade": True, "httpStatus": 200, "creditsCharged": 3, "requestsLast": "3",
                                                   "requestsRemaining": "4000", "respondedAt": "2026-09-22T19:50:00Z"})
    m = _run(w, str(tmp_path))
    assert m["odds"]["status"] == OB.STATUS_DEGRADED and w.odds_calls == 0
    assert m["captureClass"] == "COMPLETE" and m["reconciliation"]["booksReceived"] == 18
    assert m["written"]["kalshi_quotes"] > 0 and m["written"]["mlb_state"] > 0 and m["written"]["sportsbook_odds"] == 0
    assert not any(f["stage"] == "odds" for f in m["failures"])


def test_readiness_is_false_while_the_sportsbook_leg_is_degraded(tmp_path):
    w = standard_world(odds_remaining=100000, odds_cost=200)
    for i in range(4):
        _run(w, str(tmp_path), now=NOW.replace(minute=i * 10))
    rep = H.build_health(Store(str(tmp_path)), end_date="2026-09-22", days=1)
    sb = rep["metrics"]["sportsbook"]
    assert sb["legStatusByCycle"] == {"OK": 2, OB.STATUS_DEGRADED: 2} and sb["degradedCycles"] == 2
    assert rep["gates"]["checks"]["sportsbook.notBudgetDegraded"] is False
    assert rep["gates"]["infrastructureHealthy"] is False and rep["gates"]["researchReady"] is False
    assert "sportsbook.notBudgetDegraded" in rep["gates"]["failing"]


def test_retries_and_fresh_state_do_not_reset_the_daily_spend(tmp_path):
    w = standard_world(odds_remaining=100000, odds_cost=200)
    _run(w, str(tmp_path), attempt=1)
    _run(w, str(tmp_path), attempt=2, now=NOW.replace(minute=5))
    s = Store(str(tmp_path))
    os.remove(s.state_path())                                    # a crashed/reset process loses its state file
    m3 = _run(w, str(tmp_path), attempt=3, now=NOW.replace(minute=10))
    assert m3["odds"]["reason"] == "DAILY_CEILING_REACHED" and w.odds_calls == 2
    assert OB.spent_today(str(tmp_path), m3["gameDate"]) == 400


def test_the_odds_request_is_single_attempt_so_retries_cannot_spend(tmp_path):
    w = standard_world(fail_urls=["/sports/baseball_mlb/odds"])
    m = _run(w, str(tmp_path))
    assert sum(1 for u in w.requested if "/sports/baseball_mlb/odds" in u) == 1
    assert m["odds"]["status"] == OB.STATUS_FETCH_FAILED
    assert m["odds"]["creditsConsumedThisCycle"] == OB.DESIGN_COST_PER_REQUEST and m["odds"]["chargeBasis"] == "DESIGN_COST_FALLBACK"
    led = OB.read_ledger(str(tmp_path), m["gameDate"])
    assert len(led) == 1 and led[0]["creditsCharged"] >= OB.DESIGN_COST_PER_REQUEST     # never charged as zero


def test_no_key_is_reported_not_configured_and_fails_the_sportsbook_gate(tmp_path):
    w = standard_world()
    m = _run(w, str(tmp_path), odds_api_key=None)
    assert m["oddsStatus"] == OB.STATUS_NOT_CONFIGURED and w.odds_calls == 0
    rep = H.build_health(Store(str(tmp_path)), end_date="2026-09-22", days=1)
    assert rep["gates"]["checks"]["sportsbook.notBudgetDegraded"] is False
