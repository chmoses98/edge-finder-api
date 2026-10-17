"""Fast tests for the MLB live (prospective) layer: parsers, availability certainty mapping, as-of resolution, the hitter
readiness rule, incumbent pregame capture, outcome/void rules, idempotency and dormant safety.

Payload shapes are the documented MLB Stats API shapes (the same fields the repo's committed extractors read); they are
hand-assembled here (no network in the sandbox) from REAL recorded values only in mlb/live/smoke.py (the replay)."""
import gzip
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np
import pytest

HERE = os.path.dirname(__file__)
PF = os.path.join(HERE, "..")
sys.path.insert(0, PF)

from pf_core import availability as av, dists, prospective as pr  # noqa: E402
from mlb.live import availability as live_av, forecast as FC, history as HIST, incumbent as INC, results as RES, score as SC, statsapi as api  # noqa: E402

START = "2026-10-20T23:08:00Z"
T0 = "2026-10-20T15:00:00Z"


def sched_doc(posted=False, away_p=111, home_p=222, start=START, status=("Preview", "Scheduled")):
    lineup = lambda base: [{"id": base + i, "fullName": f"P{base + i}", "primaryPosition": {"abbreviation": "DH"}} for i in range(1, 10)]  # noqa: E731
    return {"dates": [{"date": "2026-10-20", "games": [{
        "gamePk": 9001, "gameType": "L", "gameDate": start, "officialDate": "2026-10-20",
        "status": {"abstractGameState": status[0], "detailedState": status[1]}, "venue": {"id": 3, "name": "Park"},
        "seriesDescription": "ALCS", "doubleHeader": "N", "gameNumber": 1,
        "teams": {"away": {"team": {"id": 1, "abbreviation": "AAA", "name": "Aaa"}, "probablePitcher": {"id": away_p, "fullName": "Away Ace"} if away_p else {}},
                  "home": {"team": {"id": 2, "abbreviation": "HHH", "name": "Hhh"}, "probablePitcher": {"id": home_p, "fullName": "Home Ace"} if home_p else {}}},
        "lineups": ({"awayPlayers": lineup(1000), "homePlayers": lineup(2000)} if posted else {})}]}]}


def games(**kw):
    return api.parse_schedule(sched_doc(**kw))


def test_parse_schedule_probables_and_lineup_order():
    g = games(posted=True)[0]
    assert g["game_pk"] == "9001" and g["away"]["abbr"] == "AAA" and g["home"]["probable_id"] == "222"
    assert g["away"]["lineup_posted"] and [p["slot"] for p in g["away"]["lineup"]] == list(range(1, 10))
    assert g["away"]["lineup"][0]["player_id"] == "1001"
    g2 = games(posted=False)[0]
    assert not g2["away"]["lineup_posted"] and g2["away"]["lineup"] == []
    assert games(away_p=None)[0]["away"]["probable_id"] is None


def test_certainty_mapping_projected_then_confirmed():
    pre = live_av.observations_from_games(games(posted=False), T0)
    st = [o for o in pre if o.kind == "starter"]
    assert {(o.status, o.certainty) for o in st} == {("PROBABLE", "PROJECTED")}
    assert not [o for o in pre if o.kind == "lineup"]
    post = live_av.observations_from_games(games(posted=True), "2026-10-20T21:30:00Z")
    st = [o for o in post if o.kind == "starter"]
    assert {(o.status, o.certainty) for o in st} == {("STARTING", "CONFIRMED")}
    lu = [o for o in post if o.kind == "lineup"]
    assert len(lu) == 18 and {(o.status, o.certainty) for o in lu} == {("STARTING", "CONFIRMED")}
    assert lu[0].detail["slot"] == 1 and all(o.validate() == [] for o in pre + post)
    assert all(o.captured_utc and o.event_start_utc == START and o.source_url and o.team in ("AAA", "HHH") for o in post)


def test_nothing_emitted_at_or_after_start_or_for_final_games():
    assert live_av.observations_from_games(games(), START) == []
    assert live_av.observations_from_games(games(), "2026-10-21T00:00:00Z") == []
    assert live_av.observations_from_games(games(status=("Final", "Final")), T0) == []
    assert live_av.observations_from_games(games(status=("Preview", "Postponed")), T0) == []


def test_probable_change_and_late_scratch_emit_not_starting(tmp_path):
    first = [o.__dict__ for o in live_av.observations_from_games(games(posted=True), "2026-10-20T21:00:00Z")]
    # later capture: away probable replaced, one lineup player removed
    doc = sched_doc(posted=True, away_p=999)
    doc["dates"][0]["games"][0]["lineups"]["awayPlayers"].pop(0)
    later = live_av.observations_from_games(api.parse_schedule(doc), "2026-10-20T22:30:00Z", prior_rows=first)
    ns = [(o.kind, o.entity_id, o.certainty) for o in later if o.status == "NOT_STARTING"]
    assert ("starter", "111", "CONFIRMED") in ns and ("lineup", "1001", "CONFIRMED") in ns
    rows = first + [o.__dict__ for o in later]
    lat = av.asof(rows, "2026-10-20T22:45:00Z")
    assert av.resolve_participation(lat, "9001", "1001", "2026-10-20T22:45:00Z", requires_lineup=True).status == av.UNCERTAIN
    assert av.resolve_participation(lat, "9001", "1002", "2026-10-20T22:45:00Z", requires_lineup=True).status == av.CONFIRMED_ACTIVE


def test_resolve_who_uses_only_archived_asof_observations():
    rows = [o.__dict__ for o in live_av.observations_from_games(games(posted=False, away_p=111), "2026-10-20T14:00:00Z")]
    rows += [o.__dict__ for o in live_av.observations_from_games(games(posted=True, away_p=333), "2026-10-20T21:00:00Z")]
    g = games()[0]
    who = FC.resolve_who(av.asof(rows, "2026-10-20T20:00:00Z"), g)
    assert who["away"]["pitcher"]["id"] == "111" and who["away"]["pitcher"]["certainty"] == "PROJECTED" and who["away"]["lineup"] is None
    who = FC.resolve_who(av.asof(rows, "2026-10-20T22:00:00Z"), g)
    assert who["away"]["pitcher"]["id"] == "333" and who["away"]["pitcher"]["certainty"] == "CONFIRMED"
    assert [p["slot"] for p in who["away"]["lineup"]] == list(range(1, 10))
    # a capture AFTER the first pitch (e.g. the post-game overwrite of the probable) is never used
    late = [o.__dict__ for o in live_av.observations_from_games(games(posted=True, away_p=444), "2026-10-20T21:00:00Z")]
    for r in late:
        r["captured_utc"] = "2026-10-21T03:00:00Z"
    who = FC.resolve_who(av.asof(rows + late, "2026-10-21T04:00:00Z"), g)
    assert who["away"]["pitcher"]["id"] == "333"


def test_hitter_readiness_rule():
    ok = FC.hitter_readiness("CONFIRMED_POSTED_LINEUP", av.CONFIRMED_ACTIVE)
    assert ok["role"] == "shadow" and ok["shadow_exit_eligible"]
    for src, st in (("ASSUMED_LAST_LINEUP", av.UNVERIFIED), ("ASSUMED_LAST_LINEUP", av.LIKELY_ACTIVE),
                    ("CONFIRMED_POSTED_LINEUP", av.UNCERTAIN), ("CONFIRMED_POSTED_LINEUP", av.LIKELY_ACTIVE)):
        r = FC.hitter_readiness(src, st)
        assert r["role"] == "shadow" and not r["shadow_exit_eligible"]


def test_partial_lineup_is_not_confirmed():
    doc = sched_doc(posted=True)
    doc["dates"][0]["games"][0]["lineups"]["homePlayers"] = doc["dates"][0]["games"][0]["lineups"]["homePlayers"][:5]
    rows = [o.__dict__ for o in live_av.observations_from_games(api.parse_schedule(doc), T0)]
    who = FC.resolve_who(av.asof(rows, "2026-10-20T16:00:00Z"), games()[0])
    assert who["home"]["lineup"] is None and who["home"]["lineup_partial"] == 5


# --------------------------------------------------------------------------- incumbents (pregame-only capture)
def _tree(tmp, created, date="2026-10-20"):
    d = tmp / "data" / "pipeline" / date
    d.mkdir(parents=True)
    json.dump({"meta": {"createdAt": created, "producedBy": "scripts/build_market_ledger.py"},
               "data": {"games": [{"gameId": 9001, "away": "AAA", "home": "HHH", "awayProjRuns": 3.9, "homeProjRuns": 4.4,
                                    "totalProj": 8.3, "excludedFromSlate": False}]}}, open(d / "projections.json", "w"))
    return str(tmp)


def test_team_incumbent_only_if_written_before_first_pitch(tmp_path):
    g = games()
    now = api.parse_ts("2026-10-20T22:00:00Z")
    recs, st = INC.team_records(_tree(tmp_path, "2026-10-20T21:30:00Z"), g, now)
    assert {r["family"] for r in recs} == {"team_runs", "game_total"} and st["usable"] == 1
    assert all(r["model_role"] == "incumbent" and r["generated_utc"] == "2026-10-20T21:30:00Z" for r in recs)
    # the same file overwritten AFTER the first pitch is not a pregame forecast
    tmp2 = tmp_path / "b"
    recs2, st2 = INC.team_records(_tree(tmp2, "2026-10-21T01:00:00Z"), g, api.parse_ts("2026-10-21T01:05:00Z"))
    assert recs2 == [] and st2["not_pregame"] == 1
    # a run after the start also gets nothing from a file that is 'in the future' of the run
    recs3, _ = INC.team_records(_tree(tmp_path / "c", "2026-10-20T23:00:00Z"), g, now)
    assert recs3 == []


def test_pitcher_incumbent_from_shadow_store(tmp_path):
    d = tmp_path / "data" / "shadow_pure" / "mlb_pitcher" / "projections" / "2026-10-20"
    d.mkdir(parents=True)
    rows = []
    for stat, mean in (("pitcher_outs", 17.0), ("pitcher_batters_faced", 22.5), ("pitcher_strikeouts", 5.5), ("pitcher_walks", 1)):
        rows.append({"game_id": "9001", "player_id": "111", "statistic": stat, "as_of": "2026-10-20T20:00:00Z", "kickoff": START,
                     "model_version": "pitcher_prop_projection@x", "x_side": "away", "source_max_observed_at": "2026-10-20T19:59:00Z",
                     "x_probable_starter": {"name": "Away Ace"}, "x_capture_window": "morning",
                     "projection": {"mean": mean, "thresholds": [{"at_least": 5, "probability": 0.6}]}})
    rows.append(dict(rows[0], as_of="2026-10-20T23:30:00Z"))        # generated after the start: never used
    with open(d / "20261020T200000Z_morning__champion.jsonl", "w") as f:
        f.write("\n".join(json.dumps(r) for r in rows))
    recs, st = INC.pitcher_records(str(tmp_path), games(), api.parse_ts("2026-10-20T21:00:00Z"))
    assert {r["family"] for r in recs} == {"outs", "batters_faced", "pitcher_strikeouts"}
    assert all(r["team"] == "AAA" and r["probs"] == [] and r["thresholds"] for r in recs)


# --------------------------------------------------------------------------- results
def box(starter_a="111", reliever_a=None, hit_pa=4):
    pit = lambda pid, order, **s: {"player_id": pid, "name": pid, "order": order, "hand": "R", **{  # noqa: E731
        "outs": 18, "bf": 24, "k": 7, "bb": 1, "h": 4, "r": 2, "er": 2, "hr": 1, "pitches": 90}, **s}
    bat = lambda pid, slot, starter=True, pa=hit_pa, **s: {"player_id": pid, "name": pid, "slot": slot, "is_starter": starter,  # noqa: E731
                                                          "position": "DH", "bat_side": "R", "PA": pa, "H": 2, "TB": 3, "HR": 0,
                                                          "R": 1, "RBI": 1, "HRR": 4, **s}
    return {"pitchers": {"away": [pit(starter_a, 0)] + ([pit(reliever_a, 1, outs=3, bf=4, k=1)] if reliever_a else []),
                         "home": [pit("222", 0, outs=15, bf=20, k=4)]},
            "batters": {"away": [bat("1001", 1), bat("1002", 2, pa=0)], "home": [bat("2001", 1)]},
            "team": {"away": {"runs": 3}, "home": {"runs": 5}}}


def test_outcome_rows_participation_rules():
    g = games()[0]
    wanted = {("111", "pitcher_strikeouts"), ("333", "pitcher_strikeouts"), ("1001", "hits"), ("1002", "hits"),
              ("9999", "hits"), ("AAA", "team_runs"), ("TOTAL", "game_total")}
    rows = {(r["entity_id"], r["family"]): r for r in RES.outcome_rows(g, box(), wanted)}
    assert rows[("111", "pitcher_strikeouts")]["participated"] and rows[("111", "pitcher_strikeouts")]["actual"] == 7
    assert not rows[("333", "pitcher_strikeouts")]["participated"] and rows[("333", "pitcher_strikeouts")]["reason"] == "did_not_appear"
    assert rows[("1001", "hits")]["participated"]
    assert not rows[("1002", "hits")]["participated"] and rows[("1002", "hits")]["reason"] == "no_plate_appearance"
    assert rows[("9999", "hits")]["reason"] == "not_in_box"
    assert rows[("AAA", "team_runs")]["actual"] == 3 and rows[("TOTAL", "game_total")]["actual"] == 8
    # the real starters we did not forecast are recorded as unforecast
    unf = {(r["entity_id"], r["family"]) for r in RES.outcome_rows(g, box(), wanted) if not r["forecasted"]}
    assert ("222", "outs") in unf and ("111", "outs") in unf and ("222", "pitcher_strikeouts") in unf
    # a probable who only came in relief is void, the line is still recorded
    r = {(x["entity_id"], x["family"]): x for x in RES.outcome_rows(g, box(starter_a="555", reliever_a="111"), {("111", "pitcher_strikeouts")})}
    assert not r[("111", "pitcher_strikeouts")]["participated"] and r[("111", "pitcher_strikeouts")]["reason"] == "relief_appearance"
    assert r[("111", "pitcher_strikeouts")]["actual"] == 1


def test_changed_rows_only_when_the_stat_changed():
    g = games()[0]
    wanted = {("111", "pitcher_strikeouts")}
    a = RES.outcome_rows(g, box(), wanted)
    latest = {(r["game_id"], r["entity_id"], r["family"]): r for r in a}
    assert RES.changed_rows(RES.outcome_rows(g, box(), wanted), latest) == []
    b2 = box()
    b2["pitchers"]["away"][0]["k"] = 8
    ch = RES.changed_rows(RES.outcome_rows(g, b2, wanted), latest)
    assert len(ch) == 1 and ch[0]["actual"] == 8


def test_parse_boxscore_roundtrip_statsapi_shape():
    doc = {"teams": {s: {"pitchers": [10 + i for i in range(2)], "teamStats": {"batting": {"runs": 4}}, "players": {
        "ID10": {"person": {"id": 10, "fullName": "SP", "pitchHand": {"code": "L"}}, "stats": {"pitching": {
            "outs": 18, "battersFaced": 24, "strikeOuts": 7, "baseOnBalls": 1, "hits": 4, "runs": 2, "earnedRuns": 2,
            "homeRuns": 1, "numberOfPitches": 95}}},
        "ID11": {"person": {"id": 11, "fullName": "RP"}, "stats": {"pitching": {"outs": 9, "battersFaced": 11}}},
        "ID50": {"person": {"id": 50, "fullName": "Bat", "batSide": {"code": "S"}}, "battingOrder": "300", "position": {"abbreviation": "SS"},
                 "stats": {"batting": {"plateAppearances": 5, "atBats": 4, "hits": 3, "doubles": 1, "triples": 0, "homeRuns": 1,
                                       "runs": 2, "rbi": 3, "baseOnBalls": 1}}},
        "ID51": {"person": {"id": 51, "fullName": "Sub"}, "battingOrder": "301", "stats": {"batting": {"plateAppearances": 1}}},
        "ID52": {"person": {"id": 52, "fullName": "Bench"}, "stats": {}}}} for s in ("away", "home")}}
    b = api.parse_boxscore(doc)
    p = b["pitchers"]["away"]
    assert [x["order"] for x in p] == [0, 1] and p[0]["bf"] == 24 and p[0]["k"] == 7 and p[0]["hand"] == "L" and p[1]["k"] is None
    h = b["batters"]["away"]
    assert [x["player_id"] for x in h] == ["50", "51"] and h[0]["slot"] == 3 and h[0]["is_starter"] and not h[1]["is_starter"]
    assert h[0]["TB"] == (3 - 1 - 0 - 1) + 2 + 4 and h[0]["HRR"] == 3 + 2 + 3 and b["team"]["home"]["runs"] == 4
    assert api.parse_boxscore({"liveData": {"boxscore": {"teams": doc["teams"]}}}) is not None
    assert api.parse_boxscore(None) is None and api.parse_boxscore({}) is None


# --------------------------------------------------------------------------- archive integration (synthetic forecasts)
def fc(role="challenger", mean=5.0, gen="2026-10-20T20:00:00Z", entity="111", family="pitcher_strikeouts"):
    d = dists.poisson(np.array([mean]), K=25)
    return {"sport": "MLB", "family": family, "model_id": f"{role}-m", "model_role": role, "model_version": "t", "game_id": "9001",
            "game_start_utc": START, "entity_id": entity, "entity_name": "X", "team": "AAA", "generated_utc": gen,
            "data_asof_utc": gen, "mean": float(d.mean[0]), "lo": 0, "probs": d.P[0].tolist() if role == "challenger" else [],
            "gate_status": "PASS", "participation": {"status": "CONFIRMED_ACTIVE", "certainty": "CONFIRMED"}, "readiness": "READY",
            "official_date": "2026-10-20", "flags": {"opener_flag": 0, "days_rest": 5}}


def test_unchanged_forecast_not_rearchived_and_late_rejected(tmp_path):
    root = str(tmp_path)
    now = api.parse_ts("2026-10-20T20:05:00Z")
    r = fc()
    keep, skipped = FC.drop_unchanged([r], root, now)
    assert len(keep) == 1 and skipped == 0
    pr.archive_forecasts(keep, root, "2026-10-20T20:05:00Z")
    keep, skipped = FC.drop_unchanged([dict(r, generated_utc="2026-10-20T21:00:00Z")], root, now)
    assert keep == [] and skipped == 1
    keep, skipped = FC.drop_unchanged([dict(fc(mean=6.0), generated_utc="2026-10-20T21:00:00Z")], root, now)
    assert len(keep) == 1
    s = pr.archive_forecasts([fc(gen="2026-10-20T23:30:00Z")], root, "2026-10-20T23:31:00Z")
    assert s["late_rejected"] == 1 and s["written"] == 0


def test_settle_void_correction_and_segments(tmp_path):
    root = str(tmp_path)
    recs = [fc("challenger", 5.0), fc("incumbent", 4.0), fc("challenger", 6.0, entity="333"), fc("incumbent", 5.0, entity="333")]
    pr.archive_forecasts(recs, root, "2026-10-20T20:05:00Z")
    g = games()[0]
    rows = RES.outcome_rows(g, box(), {("111", "pitcher_strikeouts"), ("333", "pitcher_strikeouts")})
    pr.archive_outcomes(rows, root, "MLB", "2026-10-21T06:00:00Z")
    s = pr.settle(root, "MLB", "2026-10-21T07:00:00Z")
    assert s["settled"] == 2 and s["void"] == 2           # 333 never appeared: void for both roles
    assert pr.settle(root, "MLB", "2026-10-21T08:00:00Z")["settled"] == 0
    b2 = box()
    b2["pitchers"]["away"][0]["k"] = 8
    new = RES.changed_rows(RES.outcome_rows(g, b2, {("111", "pitcher_strikeouts")}), {(r["game_id"], r["entity_id"], r["family"]): r for r in rows})
    pr.archive_outcomes(new, root, "MLB", "2026-10-22T06:00:00Z")
    assert pr.settle(root, "MLB", "2026-10-22T07:00:00Z")["corrections"] == 2     # one per role, original untouched
    assert pr.settle(root, "MLB", "2026-10-22T08:00:00Z")["corrections"] == 0
    st = pr.load_settlements(root, "MLB")
    assert sorted(st.loc[~st["void"], "actual"]) == [7.0, 7.0]
    seg = SC.segments(root, "MLB")
    assert seg["participation"]["starter_did_not_appear"] == 1
    assert "opener_flag=0" in seg["families"]["pitcher_strikeouts"]


def test_no_games_is_a_noop_success(tmp_path):
    arch = tmp_path / "arch"
    arch.mkdir()
    subprocess.run(["git", "init", "-q", str(arch)], check=True)
    rec = tmp_path / "rec"
    rec.mkdir()
    out = subprocess.run([sys.executable, "-m", "mlb.live.run", "--stage", "all", "--archive", str(arch), "--recorded", str(rec),
                          "--now", "2026-12-15T15:00:00Z", "--no-step-summary", "--repo", str(tmp_path)],
                         cwd=PF, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "MLB-PF SUMMARY: no MLB games in the window (offseason/off-day): no-op success" in out.stdout
    latest = json.load(open(arch / "projections" / "mlb" / "latest.json"))
    assert latest["games"] == [] and latest["slate"]["status"] == "NO_GAMES_IN_WINDOW"
    assert "archive_guard: OK" in out.stdout
