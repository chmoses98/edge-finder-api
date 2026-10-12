"""Accuracy and dry-run value scorecards through the real `mlb_pitcher_shadow.py evaluate` path.

A synthetic store built from the real frozen arms (committed research cache, no network): one regular-season game,
one postseason game, a captured listing snapshot with a quote, an excluded probable starter, an unprojected actual
starter. Proves the code path, never evidence: both populations, the three arm roles, nothing dropped silently,
zero matured rows, the AT_LEAST strike / dollars mapping and the preregistered dry-run verdict."""
import importlib.util
import json
import os
from datetime import datetime, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = importlib.util.spec_from_file_location("mlb_pitcher_shadow", os.path.join(ROOT, "scripts", "research", "mlb_pitcher_shadow.py"))
SH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SH)
from lib.research import mlb_pitcher_shadow_scorecards as SC  # noqa: E402
from lib.research.shadow_pure_store import ShadowStore  # noqa: E402

PID = "678022"  # a 2026 starter with box-score history in the committed research cache


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _game(pk, kickoff, gtype, date):
    return {"pk": pk, "game_type": gtype, "official_date": date, "kickoff": kickoff, "abstract": "Preview",
            "detailed": "Scheduled", "schedule_fetched_at": date + "T15:00:00Z",
            "away": {"team_id": 133, "probable_id": PID, "probable_name": "Test Starter", "lineup_posted": True},
            "home": {"team_id": 117, "probable_id": "555", "probable_name": "Other Starter", "lineup_posted": True}}


GAMES = [(_game("999001", "2026-09-28T23:08:00Z", "R", "2026-09-28"), "2026-09-28T22:00:00Z"),
         (_game("999002", "2026-09-30T23:08:00Z", "D", "2026-09-30"), "2026-09-30T22:00:00Z")]


@pytest.fixture(scope="module")
def arms():
    return SH.Arms(os.path.join(ROOT, "data"))


def _store(tmp, arms, with_outcomes):
    st = ShadowStore(str(tmp / "store"))
    for g, as_of in GAMES:
        date, ts = g["official_date"], SH.stamp(_t(as_of))
        proj, why = arms.project(g, "away", as_of)
        assert why is None
        rows = SH.build_rows(arms, g, "away", "pregame", as_of, proj, f"run_{ts}")
        for arm in SH.ARMS:
            st.write_once(f"projections/{date}/{ts}_pregame__{arm}.jsonl", [r for r in rows if r["x_arm"] == arm], as_of)
        st.write_once(f"runs/{date}/{ts}_pregame.json", {
            "run_id": f"run_{ts}", "window": "pregame", "as_of": as_of, "rows": len(rows),
            "games": [{"game_id": g["pk"], "kickoff": g["kickoff"], "game_type": g["game_type"]}],
            "exclusions": [{"game_id": g["pk"], "side": "home", "player_id": "555", "reason": "challenger_lt3_prior_starts"}]})
        quote = {"quoted_at": as_of[:11] + "21:30:00Z", "yes_bid": 0.40, "yes_ask": 0.45, "last": 0.44}
        st.write_once(f"market/{date}/{ts}_pregame.jsonl", [
            {"record": "listing_snapshot", "sport": "MLB", "game_id": g["pk"], "player_id": PID, "statistic": "pitcher_strikeouts",
             "ticker": f"KXMLBKS-X{g['pk']}-ATHTSTARTER-5", "at_least": 5, "comparison": "AT_LEAST",
             "first_listed_at": as_of[:11] + "13:00:00Z", "quote": quote, "pure_as_of": as_of},
            {"record": "listing_snapshot", "sport": "MLB", "game_id": g["pk"], "player_id": None, "statistic": "pitcher_outs",
             "ticker": f"KXMLBOUTS-X{g['pk']}-HOU-15", "at_least": 15, "comparison": "AT_LEAST",
             "first_listed_at": as_of[:11] + "13:00:00Z", "quote": quote, "pure_as_of": as_of}], as_of)
    if with_outcomes:
        outs = [{"sport": "MLB", "game_id": g["pk"], "player_id": PID, "statistic": s, "actual": v, "played": True,
                 "source": "mlb_statsapi_v1.1_feed_live", "observed_at": g["official_date"] + "T03:30:00Z"}
                for g, _ in GAMES for s, v in (("pitcher_outs", 17), ("pitcher_batters_faced", 24), ("pitcher_strikeouts", 6))]
        st.write_once("outcomes/2026-10-01/20261001T033000Z.jsonl", outs)
        st.write_once("outcome_provenance/2026-10-01/20261001T033000Z.jsonl", [
            {"game_id": g["pk"], "status": "Final", "unprojected_actual_starters": ["777"]} for g, _ in GAMES])
    return st


def _evaluate(st, monkeypatch, capsys, now):
    monkeypatch.setattr(SH, "utcnow", lambda: _t(now))
    assert SH.main(["--store", st.root, "evaluate", "--reps", "50"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    recs = {p.rsplit(".", 2)[-2]: json.load(open(os.path.join(st.root, p))) for p in out["scorecards_written"]}
    return out, recs


def test_zero_matured_rows_give_insufficient_sample_not_an_error(arms, tmp_path, monkeypatch, capsys):
    st = _store(tmp_path, arms, with_outcomes=False)
    out, recs = _evaluate(st, monkeypatch, capsys, "2026-09-28T22:30:00Z")   # before anything has finished
    assert out["matured_rows"] == 0 and set(recs) == {"accuracy_scorecard", "value_scorecard"}
    assert recs["accuracy_scorecard"]["matured_games"] == 0
    assert out["scorecards"]["accuracy_scorecard"]["regular_season"] == {"full_eligible": "INSUFFICIENT_SAMPLE",
                                                                         "pregame_market_listed": "INSUFFICIENT_SAMPLE"}
    assert recs["value_scorecard"]["mode"] == "DRY_RUN_DIAGNOSTIC"
    assert recs["value_scorecard"]["primary"]["verdict"] == "INSUFFICIENT_SAMPLE"
    assert st.verify()["ok"]


@pytest.fixture
def evaluated(arms, tmp_path, monkeypatch, capsys):
    st = _store(tmp_path, arms, with_outcomes=True)
    return st, *_evaluate(st, monkeypatch, capsys, "2026-10-02T12:00:00Z")


def test_accuracy_scorecard_populations_roles_and_accounting(evaluated):
    st, out, recs = evaluated
    acc = recs["accuracy_scorecard"]
    assert acc["status"] == "DESCRIPTIVE_ONLY_NOT_AUTOMATIC_PROMOTION" and acc["matured_games"] == 2
    assert set(acc["scorecards"]) == {"regular_season", "postseason"}
    assert acc["scorecards"]["postseason"]["validation_status"] == "BLOCKED_NOT_VALIDATED_POSTSEASON"
    rs = acc["scorecards"]["regular_season"]
    sc = rs["scorecard"]
    assert {a: v["role"] for a, v in sc["config"]["arms"].items()} == {
        "challenger": "challenger", "champion": "production_champion", "baseline": "statistical_baseline"}
    full = sc["populations"]["full_eligible"]["accounting"]["arms"]["challenger"]
    assert full["eligible_identities"] == 6            # the projected starter AND the excluded one, 3 statistics each
    assert full["missing_forecast"] == 3 and full["scored_rows"] == 3
    assert rs["excluded_at_capture"] == {"challenger_lt3_prior_starts": 1}
    listed = sc["populations"]["pregame_market_listed"]["accounting"]["arms"]["challenger"]
    assert listed["scored_rows"] == 1                  # only the strikeouts listing has a matched player
    h2h = sc["populations"]["full_eligible"]["by_statistic"]["MLB:pitcher_strikeouts"]["head_to_head"]
    assert h2h["champion"]["role"] == "production_champion" and h2h["baseline"]["n_pairs"] == 1
    assert acc["collection_accounting"]["unprojected_actual_starters"] == 2
    assert acc["market_family_mapping"]["dropped_unmatched_player"] == 2
    assert out["written"] and st.verify()["ok"]


def test_value_scorecard_is_a_preregistered_dry_run(evaluated):
    _st, _out, recs = evaluated
    v = recs["value_scorecard"]
    assert v["mode"] == "DRY_RUN_DIAGNOSTIC" and "no order" in v["wagering"]
    assert v["preregistration"]["file_sha256"] == SC.file_sha256(SC.PREREG_PATH)
    prim = v["evaluations"]["challenger/pregame/regular_season"]
    assert prim["role"] == "primary" and prim["eligible_for_independent_value_verdict"] and not prim["market_informed_source"]
    assert prim["verdict"] == "INSUFFICIENT_SAMPLE"
    r = prim["report"]
    assert r["accounting"]["rungs_with_entry_quote"] == 1     # AT_LEAST 5 -> the at_least 5 rung, dollars
    assert r["config"]["dnp_policy"] == "void" and r["config"]["fees"]["role"] == "taker"
    assert r["preregistration"]["thresholds"] == {"min_decisions": 200, "min_games": 40, "min_roi_ci_lower": 0.0}
    post = v["evaluations"]["challenger/pregame/postseason"]
    assert post["validation_status"] == "BLOCKED_NOT_VALIDATED_POSTSEASON" and not post["eligible_for_independent_value_verdict"]
    assert v["evaluations"]["challenger/pregame/regular_season/dnp_settle_by_actual"]["role"] == "dnp_sensitivity"
    assert [k for k, e in v["evaluations"].items() if e["verdict_counts_toward_preregistered_decision"]] == [
        "challenger/pregame/regular_season"]


def test_strike_and_quote_mapping():
    assert SC.kalshi_at_least(9, "AT_LEAST") == 9.0 and SC.kalshi_at_least(8.5, "GREATER_THAN") == 9.0
    assert SC.kalshi_at_least(9, "LESS_THAN") is None and SC.kalshi_at_least(9, "AT_LEAST", integer_ladder=False) == 8.5
    base = {"game_id": "1", "player_id": "2", "statistic": "pitcher_strikeouts", "ticker": "KXMLBKS-X-9", "at_least": 9,
            "comparison": "AT_LEAST", "first_listed_at": "2026-09-28T13:00:00Z", "pure_as_of": "2026-09-28T22:00:00Z"}
    q = {"quoted_at": "2026-09-28T21:00:00Z", "yes_bid": 0.07, "yes_ask": 0.13}
    rows = [{**base, "quote": q}, {**base, "quote": q},
            {**base, "quote": {**q, "quoted_at": "2026-09-28T22:10:00Z"}},
            {**base, "quote": {**q, "yes_bid": 0.2}},
            {**base, "comparison": "BETWEEN", "quote": q},
            {**base, "quote": None}]
    cohort, quotes, c = SC.market_family(rows, {"1": "2026-09-28T23:08:00Z"})
    assert len(quotes) == 1 and quotes[0]["at_least"] == 9.0 and quotes[0]["price_unit"] == "dollars"
    assert c["quote_duplicate_across_snapshots"] == 1 and c["quote_dropped_observed_after_snapshot_as_of"] == 1
    assert c["quote_dropped_crossed"] == 1 and c["quote_dropped_no_or_unknown_strike"] == 1 and c["quote_absent"] == 1
    assert len(cohort) == 6 and cohort[0]["open_time_basis"] == "first_listed_at_proxy"


def test_a_scorecard_failure_is_recorded_and_never_stops_the_evaluation(monkeypatch):
    def boom(*a, **k):
        raise ValueError("synthetic")
    monkeypatch.setattr(SC, "build", boom)
    acc, val = SC.build_safe([], {}, [], [], [], [], now=_t("2026-10-01T00:00:00Z"), run_id="r")
    assert acc["state"] == val["state"] == "ERROR" and SC.summary_safe(acc, val)["scorecards_state"] == "ERROR"


def test_scorecards_module_is_stdlib_only_and_touches_no_wagering_surface():
    src = open(os.path.join(ROOT, "lib", "research", "mlb_pitcher_shadow_scorecards.py"), encoding="utf-8").read()
    imports = [x.strip() for x in src.splitlines() if x.startswith(("import ", "from "))]
    assert {x.split()[1].split(".")[0] for x in imports} <= {"__future__", "hashlib", "importlib", "json", "math", "os",
                                                              "sys", "collections", "datetime"}
