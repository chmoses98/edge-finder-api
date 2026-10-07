#!/usr/bin/env python3
"""
tests/test_mlb_player_props.py
==============================
MLB player-prop research projections (lib/research/pitcher_prop_projection.py,
lib/research/mlb_player_props.py) and their edge_finder.app.v1 publication.
Synthetic fixtures only; nothing here reads or writes repository data.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "contract")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.research import mlb_player_props as MP  # noqa: E402
from lib.research import pitcher_prop_projection as PP  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CAL = _load("mlb_pitcher_prop_calibration", "scripts/research/mlb_pitcher_prop_calibration.py")

STARTS = [{"pid": "607192", "date": f"2026-09-{d:02d}", "outs": o, "bf": b, "k": k}
          for d, o, b, k in ((2, 18, 24, 8), (8, 15, 22, 6), (14, 17, 23, 9), (20, 16, 22, 7), (26, 18, 24, 8))]


# ---------------------------------------------------------------- pitcher model

def test_outs_and_strikeouts_come_from_one_consistent_workload():
    reg = PP.project(STARTS, postseason=False)
    post = PP.project(STARTS, {"postseason_shift_outs": -2.0}, postseason=True)
    assert abs(sum(reg["outs_pmf"]) - 1) < 1e-9 and abs(sum(reg["k_pmf"]) - 1) < 1e-6
    # a shorter postseason leash lowers BOTH families together
    assert post["expected_outs"] < reg["expected_outs"] - 1.5
    assert post["expected_strikeouts"] < reg["expected_strikeouts"]
    assert post["expected_batters_faced"] < reg["expected_batters_faced"]
    for n in range(12, 22):
        assert PP.p_at_least(post["p_outs_ge"], n) <= PP.p_at_least(reg["p_outs_ge"], n) + 1e-12
    # monotone ladders
    ks = [PP.p_at_least(reg["p_k_ge"], n) for n in range(0, 15)]
    assert all(a >= b for a, b in zip(ks, ks[1:])) and ks[0] == 1.0


def test_postseason_shift_only_applies_to_postseason_games():
    a = PP.project(STARTS, {"postseason_shift_outs": -2.0}, postseason=False)
    b = PP.project(STARTS, {"postseason_shift_outs": 0.0}, postseason=False)
    assert a["expected_outs"] == pytest.approx(b["expected_outs"])
    assert a["postseason_shift_applied"] == 0.0


def test_outs_distribution_is_not_geometric():
    """The incumbent engine's constant hook makes outs ~geometric (sd ~ mean); real starter
    outs have sd ~4.3. The research model must stay in that range."""
    pr = PP.project(STARTS)
    mean = pr["expected_outs"]
    sd = sum((o - mean) ** 2 * p for o, p in enumerate(pr["outs_pmf"])) ** 0.5
    assert 3.0 < sd < 6.0


def test_settlement_only_starts_inform_workload_but_not_the_k_rate():
    with_post = STARTS + [{"pid": "607192", "date": "2026-10-01", "outs": 9, "bf": None, "k": 9}]
    s = PP.history_summary(with_post)
    assert s["bf"] == sum(x["bf"] for x in STARTS) and s["k"] == sum(x["k"] for x in STARTS)
    assert s["last_outs"] == 9


# ---------------------------------------------------------------- calibration helpers / leakage

def test_history_never_includes_the_game_date_or_later():
    hist = CAL.History(STARTS + [{"pid": "607192", "date": "2026-10-07", "outs": 3, "bf": 9, "k": 0}], [])
    assert [s["date"] for s in hist.before("607192", "2026-10-07")] == [s["date"] for s in STARTS]


def test_postseason_shift_is_estimated_only_from_earlier_postseason_starts():
    starts = [dict(s, pid="1") for s in STARTS]
    ps = [{"pid": "1", "pk": "a", "date": "2026-10-01", "outs": 6, "k": 2},
          {"pid": "1", "pk": "b", "date": "2026-10-09", "outs": 27, "k": 9}]
    hist = CAL.History(starts, [])
    shift_before_oct5, n, raw = CAL.postseason_shift(ps, hist, dict(PP.DEFAULT_PARAMS), "2026-10-05")
    assert n == 1 and raw < 0 and shift_before_oct5 < 0          # the 10-09 start is not visible
    assert abs(shift_before_oct5) < abs(raw)                       # shrunk toward zero


def test_calibration_metrics_are_sane():
    ps, ys = [], []
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        ps += [p] * 200
        ys += [1] * int(round(p * 200)) + [0] * (200 - int(round(p * 200)))
    a, b = CAL.calib(ps, ys)
    assert abs(b - 1.0) < 0.2 and abs(a) < 0.2
    e, rel = CAL.ece(ps, ys)
    assert e < 0.05 and rel
    d = CAL.paired_delta_ci(ps, [0.5] * len(ps), ys, [i // 5 for i in range(len(ps))], reps=200)
    assert d["delta_brier"] < 0 and d["ci95"][0] <= d["delta_brier"] <= d["ci95"][1]


def test_statcast_starter_lines_use_the_outs_state():
    def pa(i, inning, outs, pitcher, event):
        return {"atBatIndex": i, "pitchNumber": 1, "inning": inning, "outsWhenUp": outs, "pitcherId": pitcher, "events": event}
    rows = [pa(0, 1, 0, "H", "strikeout"), pa(1, 1, 1, "H", "walk"), pa(2, 1, 1, "H", "grounded_into_double_play"),
            pa(3, 1, 0, "A", "single"), pa(4, 1, 0, "A", "field_out"), pa(5, 1, 1, "A", "strikeout"),
            pa(6, 1, 2, "A", "field_out"), pa(7, 2, 0, "H2", "field_out")]
    lines = CAL._pa_outs_from_pitches(rows)
    assert lines["home"] == ("H", {"outs": 3, "bf": 3, "k": 1})
    assert lines["away"] == ("A", {"outs": 3, "bf": 4, "k": 1})


# ---------------------------------------------------------------- publication records

def _slate(confirmed=True, starter=("Tyler Glasnow", "607192")):
    return {"date": "2026-10-07", "games": [{
        "gameId": 849822, "away": {"abbr": "LAD", "pitcher": {"name": starter[0], "id": starter[1]} if starter else None},
        "home": {"abbr": "ATL", "pitcher": {"name": "Tyler Mahle", "id": "641816"}},
        "awayTeamStats": {"lineupConfirmed": confirmed, "lineupStatus": "confirmed" if confirmed else "unconfirmed",
                          "confirmedLineup": [{"order": 2, "playerId": "660271", "name": "Shohei Ohtani"}] if confirmed else []},
        "homeTeamStats": {"lineupConfirmed": True, "confirmedLineup": [{"order": 1, "playerId": "1", "name": "Ronald Acuna Jr.",
                                                                          "platoonSplits": {"vsRHP": {"kPct": 24}}}]},
    }]}


EV = {"event_id": "evt_x", "status": "SCHEDULED", "start_time_utc": "2026-10-07T22:00:00Z",
      "source_ids": {"mlb_game_pk": "849822"}, "extensions": {"game_date": "2026-10-07", "game_type": "D"}}


def _m(ticker, player, team="LAD", thr=None):
    return {"marketTicker": ticker, "player": player, "team": team,
            "threshold": thr if thr is not None else int(ticker.rsplit("-", 1)[1])}


MARKETS = [
    _m("KXMLBKS-26OCT071800LADATL-LADTGLASNOW31-9", "Tyler Glasnow"),
    _m("KXMLBOUTS-26OCT071800LADATL-LADTGLASNOW31-18", "Tyler Glasnow"),
    _m("KXMLBKS-26OCT071800LADATL-LADBSNELL7-6", "Blake Snell"),
    _m("KXMLBHIT-26OCT071800LADATL-LADSOHTANI17-2", "Shohei Ohtani"),
    _m("KXMLBTB-26OCT071800LADATL-LADSOHTANI17-3", "Shohei Ohtani"),
    _m("KXMLBHIT-26OCT071800LADATL-LADMBETTS50-2", "Mookie Betts"),
    _m("KXMLBSB-26OCT071800LADATL-LADSOHTANI17-1", "Shohei Ohtani"),
    _m("KXMLBRBI-26OCT071800LADATL-LADXX-1", None),
    {"marketTicker": "KXMLBGAME-26OCT071800LADATL-LAD", "team": "LAD"},
]
SNAPS = [
    {"marketTicker": "KXMLBHIT-26OCT071800LADATL-LADSOHTANI17-2", "createdAt": "2026-10-07T20:00:00Z",
     "projectionStatus": "PROJECTED", "modelProbability": 41.0, "sampleSizeDiagnostics": {"hitterArchivedPACount": 180}},
    # a snapshot written AFTER the export time must never be used (no look-ahead)
    {"marketTicker": "KXMLBTB-26OCT071800LADATL-LADSOHTANI17-3", "createdAt": "2026-10-07T23:00:00Z",
     "projectionStatus": "PROJECTED", "modelProbability": 0.3},
]


def _records(now="2026-10-07T21:00:00Z", slate=None, ev=None, ctx=True):
    ctx_obj = MP.PitcherContext(dict(PP.DEFAULT_PARAMS, postseason_shift_outs=-1.2, postseason_shift_n=30, asof="t"),
                                STARTS, []) if ctx else None
    ev = ev or EV
    return MP.build_records(slate=slate or _slate(), markets=MARKETS, now_iso=now, event_for=lambda t: ev,
                            pregame_closed=lambda e: e["status"] != "SCHEDULED" or e["start_time_utc"] <= now,
                            player_id_for=lambda i: f"pid_{i}", pitcher_ctx=ctx_obj, hitter_rows=SNAPS)


def test_every_prop_market_gets_a_record_and_game_markets_do_not():
    recs = _records()
    assert "KXMLBGAME-26OCT071800LADATL-LAD" not in recs
    assert len(recs) == len(MARKETS) - 1
    for r in recs.values():
        assert r["schema"] == "mlb.player_prop.v1" and r["betting_eligible"] is False
        assert r["projection_status"] and r["status_reason"]
        # a probability exists ONLY for a projection status
        assert (r["model_probability_yes"] is not None) == r["projection_status"].endswith("_PROJECTION")
        assert r["projection_status"] != "VERIFIED_PROJECTION"


def test_pitcher_statuses_and_postseason_path():
    recs = _records()
    k = recs["KXMLBKS-26OCT071800LADATL-LADTGLASNOW31-9"]
    assert k["projection_status"] == "RESEARCH_PROJECTION" and k["player_id"] == "pid_607192"
    assert k["expected_stat"]["stat"] == "strikeouts" and k["validation"]["status"] == "RESEARCH"
    assert any(d["label"] == "Postseason leash" for d in k["drivers"])
    outs = recs["KXMLBOUTS-26OCT071800LADATL-LADTGLASNOW31-18"]
    assert outs["projection_status"] == "RESEARCH_PROJECTION"
    assert recs["KXMLBKS-26OCT071800LADATL-LADBSNELL7-6"]["projection_status"] == "PLAYER_NOT_STARTING"
    no_sp = _records(slate=_slate(starter=None))
    assert no_sp["KXMLBKS-26OCT071800LADATL-LADTGLASNOW31-9"]["projection_status"] == "MISSING_REQUIRED_CONTEXT"
    no_model = _records(ctx=False)
    assert no_model["KXMLBKS-26OCT071800LADATL-LADTGLASNOW31-9"]["projection_status"] == "MISSING_REQUIRED_CONTEXT"


def test_hitter_statuses_lineup_gating_and_no_lookahead():
    recs = _records()
    hit = recs["KXMLBHIT-26OCT071800LADATL-LADSOHTANI17-2"]
    assert hit["projection_status"] == "RESEARCH_PROJECTION" and hit["model_probability_yes"] == 0.41
    assert hit["lineup_slot"] == 2 and hit["lineup_status"] == "CONFIRMED" and hit["player_id"] == "pid_660271"
    tb = recs["KXMLBTB-26OCT071800LADATL-LADSOHTANI17-3"]
    assert tb["projection_status"] == "MISSING_REQUIRED_CONTEXT" and tb["model_probability_yes"] is None
    assert recs["KXMLBHIT-26OCT071800LADATL-LADMBETTS50-2"]["projection_status"] == "PLAYER_NOT_STARTING"
    unconfirmed = _records(slate=_slate(confirmed=False))
    assert unconfirmed["KXMLBHIT-26OCT071800LADATL-LADSOHTANI17-2"]["projection_status"] == "LINEUP_UNCONFIRMED"


def test_unsupported_ambiguous_and_started_states():
    recs = _records()
    assert recs["KXMLBSB-26OCT071800LADATL-LADSOHTANI17-1"]["projection_status"] == "NO_MODEL_SUPPORT"
    assert recs["KXMLBRBI-26OCT071800LADATL-LADXX-1"]["projection_status"] == "AMBIGUOUS_MARKET"
    started = _records(now="2026-10-07T22:05:00Z")
    assert started["KXMLBKS-26OCT071800LADATL-LADTGLASNOW31-9"]["projection_status"] == "GAME_STARTED"
    assert started["KXMLBKS-26OCT071800LADATL-LADTGLASNOW31-9"]["model_probability_yes"] is None
    assert started["KXMLBSB-26OCT071800LADATL-LADSOHTANI17-1"]["projection_status"] == "NO_MODEL_SUPPORT"
    unmatched = MP.build_records(slate=_slate(), markets=MARKETS[:1], now_iso="2026-10-07T21:00:00Z",
                                 event_for=lambda t: None, pregame_closed=lambda e: False, player_id_for=str)
    assert unmatched["KXMLBKS-26OCT071800LADATL-LADTGLASNOW31-9"]["projection_status"] == "MISSING_REQUIRED_CONTEXT"


def test_settled_postseason_props_become_pitcher_history():
    rows = [{"marketFamily": "pitcher_outs", "marketTicker": "KXMLBOUTS-26OCT061800LADATL-LADYYAMAMOTO18-18",
             "settlementEvidence": {"actualValue": 21, "candidates": [{"playerId": 808967}]}},
            {"marketFamily": "pitcher_strikeouts", "marketTicker": "KXMLBKS-26OCT061800LADATL-LADYYAMAMOTO18-9",
             "settlementEvidence": {"actualValue": 10, "candidates": [{"playerId": 808967}]}},
            {"marketFamily": "pitcher_outs", "marketTicker": "KXMLBOUTS-26SEP201800LADATL-LADYYAMAMOTO18-18",
             "settlementEvidence": {"actualValue": 18, "candidates": [{"playerId": 808967}]}}]
    got = MP.postseason_starts_from_settlements(rows)
    assert got == [{"pid": "808967", "date": "2026-10-06", "outs": 21, "k": 10, "bf": None}]


# ---------------------------------------------------------------- exporter integration

def test_exporter_attaches_player_prop_and_player_id_and_keeps_props_out_of_model_prices(tmp_path):
    base = _load("_app_contract_fixture2", "tests/test_app_contract_v1.py")
    root = base.make_data_root(tmp_path / "data")
    markets = list(base.MARKETS) + [
        {**base._market("KXMLBKS-26OCT011400PHIATL-PHIANOLA27-6", "pitcher_strikeouts", "Aaron Nola: 6+ strikeouts?",
                        team="PHI", threshold=6, operator="AT_LEAST"), "player": "Aaron Nola"},
        {**base._market("KXMLBSB-26OCT011400PHIATL-ATLRACUNA13-1", "hitter_stolen_bases", "Ronald Acuna Jr.: 1+ stolen bases?",
                        team="ATL", threshold=1, operator="AT_LEAST"), "player": "Ronald Acuna Jr."},
    ]
    base._write_jsonl(os.path.join(root, "edgelab", "markets", f"{base.DATE}.jsonl"), markets)
    base._write_jsonl(os.path.join(root, "edgelab", "settlements", f"{base.DATE}.jsonl"), [])   # pregame, not Final
    assert base.run_export(root, tmp_path / "out", now="2026-10-01T23:00:00Z", extra=["--date", base.DATE]) == 0
    items = {m["kalshi_ticker"]: m for m in base._load(tmp_path / "out", "markets.json")["items"]}
    ks = items["KXMLBKS-26OCT011400PHIATL-PHIANOLA27-6"]
    pp = ks["extensions"]["player_prop"]
    assert pp["schema"] == "mlb.player_prop.v1" and pp["player_name"] == "Aaron Nola"
    # no research artifacts in the synthetic root -> explicit missing context, never a guess
    assert pp["projection_status"] == "MISSING_REQUIRED_CONTEXT" and pp["model_probability_yes"] is None
    # Nola is the slate's probable starter -> resolved to the explorer's player participant id
    assert ks["player_id"] == base.app_export._player_pid("605400")
    sb = items["KXMLBSB-26OCT011400PHIATL-ATLRACUNA13-1"]["extensions"]["player_prop"]
    assert sb["projection_status"] == "NO_MODEL_SUPPORT"
    assert "player_prop" not in items["KXMLBGAME-26OCT011400PHIATL-PHI"]["extensions"]
    priced = {p["market_id"] for p in base._load(tmp_path / "out", "model_prices.json")["items"]}
    assert ks["market_id"] not in priced
