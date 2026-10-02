"""
Production health gate: freshness skips VERIFIED MLB off-days.

Before: every freshness limit counted calendar days, so the 3rd-10th day
after the World Series (and any >= 3-day gap with no MLB games: a pause
between postseason rounds, the All-Star break) made PROD-1/2/3/6 (and later
PROD-5) CRITICAL every morning until the 10-day "inactive" escape.

Now: the required date is walked back over consecutive days the MLB
schedule evidence PROVES had no playable game -- so the LAST real game day
must still be fully slated, settled, recommended and evaluated -- and
missing/failed/out-of-window evidence skips nothing (calendar days, fail
closed). Router divergence (PROD-9) and the backlog (PROD-7) keep the real
clock.

Deterministic: pure evaluator on hand-built states; probe tested with an
injected fetch and a fixed `now`. No network, no wall clock, no live data.
"""

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "ci"))

import production_health_gate as G  # noqa: E402
import mlb_schedule_probe as probe  # noqa: E402
from lib.edgelab import slate_day_contract as contract  # noqa: E402

LAST_GAME_DAY = date(2026, 10, 31)   # e.g. World Series Game 7


def _d(offset):
    return (LAST_GAME_DAY + timedelta(days=offset)).isoformat()


def _now(offset):
    day = LAST_GAME_DAY + timedelta(days=offset)
    return datetime(day.year, day.month, day.day, 13, 0, 0, tzinfo=timezone.utc)


def _evidence(now_offset, game_day_offsets, lookback=14):
    """Probe-shaped evidence for [now-lookback, now]."""
    start, end = now_offset - lookback, now_offset
    return {
        "source": contract.SCHEDULE_SOURCE,
        "start": _d(start), "end": _d(end),
        "playableGamesByDate": {_d(i): (3 if i in game_day_offsets else 0)
                                for i in range(start, end + 1)},
    }


#: every day up to and including LAST_GAME_DAY is a game day; nothing after.
SEASON = set(range(-30, 1))


def _state(now_offset, evidence=None, **overrides):
    base = {
        "now": _now(now_offset),
        "root": ROOT,
        "slateDate": _d(0),
        "settlementLatest": _d(0),
        "recommendationLatest": _d(0),
        "modelEvaluationLatest": _d(0),
        "researchHeartbeatLatest": _d(now_offset - 1),
        "betsLedgerCommitDate": _d(1),
        "bets": [],
        "acknowledgedUnresolvableBetIds": set(),
        "scheduleEvidence": evidence,
    }
    base.update(overrides)
    return base


def _results(state):
    return {r["id"]: r for r in G.evaluate_health(state)}


def _overall(state):
    return G.summarize(G.evaluate_health(state))["overall"]


# ── the season-end gap ───────────────────────────────────────────────────────

@pytest.mark.parametrize("k", range(1, 11))
def test_days_after_the_last_game_are_healthy_with_schedule_evidence(k):
    state = _state(k, _evidence(k, SEASON))
    assert _overall(state) == "HEALTHY", G.evaluate_health(state)
    assert _results(state)["PROD-1"]["status"] == G.PASS


@pytest.mark.parametrize("k", range(3, 11))
def test_without_evidence_the_same_gap_is_still_critical(k):
    """Documents the behaviour being fixed, and that missing evidence keeps it
    (fail closed)."""
    state = _state(k, None)
    assert _overall(state) == "CRITICAL"
    assert _results(state)["PROD-1"]["status"] == G.FAIL


@pytest.mark.parametrize("k", [11, 12, 40])
def test_long_offseason_still_uses_the_unchanged_inactive_escape(k):
    for evidence in (None, _evidence(k, SEASON)):
        r = _results(_state(k, evidence))
        assert r["PROD-1"]["status"] == G.NOT_APPLICABLE
        assert r["PROD-2"]["status"] == G.NOT_APPLICABLE


def test_prod1_names_the_verified_off_days():
    r = _results(_state(5, _evidence(5, SEASON)))["PROD-1"]
    assert r["detail"]["verifiedOffDays"] == [_d(i) for i in range(1, 6)]
    assert "no playable game" in r["summary"]


# ── fail closed ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("evidence", [
    {"fetchError": "schedule fetch failed or response malformed"},
    {"start": _d(-9), "end": _d(5)},                                   # no counts
    {"playableGamesByDate": {_d(i): 0 for i in range(1, 6)}},         # no window
    "garbage",
])
def test_failed_or_malformed_evidence_skips_nothing(evidence):
    assert _overall(_state(5, evidence)) == "CRITICAL"


def test_dates_outside_the_evidence_window_are_never_off_days():
    ev = _evidence(5, SEASON)
    ev["start"] = _d(4)            # window no longer covers D+1..D+3
    assert _overall(_state(5, ev)) == "CRITICAL"


@pytest.mark.parametrize("bad", [True, None, "0", 0.0, -1])
def test_only_an_integer_zero_is_an_off_day(bad):
    ev = _evidence(3, SEASON)
    ev["playableGamesByDate"][_d(1)] = bad
    assert _d(1) not in G.verified_off_days(ev)


# ── the last game day is still enforced exactly ───────────────────────────────

@pytest.mark.parametrize("field,assertion", [
    ("settlementLatest", "PROD-2"),
    ("recommendationLatest", "PROD-3"),
    ("modelEvaluationLatest", "PROD-6"),
])
@pytest.mark.parametrize("k", [3, 6, 10])
def test_last_game_day_not_processed_is_still_critical(field, assertion, k):
    state = _state(k, _evidence(k, SEASON), **{field: _d(-1)})
    r = _results(state)
    assert r[assertion]["status"] == G.FAIL, r[assertion]
    assert _overall(state) == "CRITICAL"


def test_settlement_stopped_while_slates_continue_is_still_blind_but_green():
    state = _state(3, _evidence(3, SEASON | {1, 2, 3}), slateDate=_d(2),
                   recommendationLatest=_d(2), modelEvaluationLatest=_d(2),
                   settlementLatest=_d(-2))
    r = _results(state)
    assert r["PROD-2"]["status"] == G.FAIL
    assert r["PROD-8"]["status"] == G.FAIL


def test_a_missed_real_game_day_inside_a_gap_is_critical():
    """Postseason: no games D+1, D+2; games on D+3 and D+4 that were never
    slated. On D+6 the required slate date walks back only to D+4."""
    state = _state(6, _evidence(6, SEASON | {3, 4}))
    r = _results(state)
    assert r["PROD-1"]["status"] == G.FAIL
    assert r["PROD-1"]["detail"]["effectiveLagDays"] > G.MAX_SLATE_LAG_DAYS


def test_all_star_break_with_an_exhibition_game_is_not_a_missed_game_day():
    """Sun = last regular game; Mon-Thu no regular games, the All-Star Game
    (gameType A) on Tue is not a production game; the gate runs Fri 13:00Z."""
    start, end = _d(-9), _d(5)
    response = {"dates": [
        {"date": _d(i), "games": [{"gameType": "R", "status": {"detailedState": "Final"}}]}
        for i in range(-9, 1)
    ] + [{"date": _d(2), "games": [{"gameType": "A", "status": {"detailedState": "Final"}}]}]}
    ev = probe.build_evidence(response, start, end, "2026-10-31T00:00:00Z")
    assert ev["playableGamesByDate"][_d(2)] == 0
    assert _overall(_state(5, ev)) == "HEALTHY"


# ── in-season behaviour is unchanged ──────────────────────────────────────────

@pytest.mark.parametrize("overrides", [
    {},
    {"slateDate": _d(-3)},
    {"settlementLatest": _d(-4)},
    {"recommendationLatest": _d(-3), "modelEvaluationLatest": _d(-1)},
    {"betsLedgerCommitDate": _d(-5)},
])
def test_every_day_a_game_day_gives_exactly_the_calendar_verdict(overrides):
    every_day = set(range(-30, 30))
    with_ev = G.evaluate_health(_state(0, _evidence(0, every_day), **overrides))
    without = G.evaluate_health(_state(0, None, **overrides))
    assert [(r["id"], r["status"]) for r in with_ev] == [(r["id"], r["status"]) for r in without]


def test_router_divergence_and_backlog_keep_the_real_clock():
    bets = [{"id": "b%d" % i, "date": _d(-5), "status": "pending"} for i in range(20)]
    state = _state(5, _evidence(5, SEASON), bets=bets,
                   routerEvidence={"fetchError": "probe crashed"})
    r = _results(state)
    assert r["PROD-7"]["status"] == G.FAIL
    assert r["PROD-9"]["status"] == G.FAIL
    assert _overall(state) == "CRITICAL"


# ── the probe and the pure schedule reduction ─────────────────────────────────

def test_playable_games_by_date_counts_competitive_playable_games_only():
    response = {"dates": [
        {"date": "2026-10-01", "games": [
            {"gameType": "F", "status": {"detailedState": "Final"}},
            {"gameType": "S", "status": {"detailedState": "Final"}},
            {"gameType": "D", "status": {"detailedState": "Postponed"}},
        ]},
        {"date": "2026-10-03", "games": [{"gameType": "W", "status": {"detailedState": "Scheduled"}}]},
        {"date": "2026-09-01", "games": [{"gameType": "R"}]},   # outside the window
    ]}
    assert contract.playable_games_by_date(response, "2026-10-01", "2026-10-03") == {
        "2026-10-01": 1, "2026-10-02": 0, "2026-10-03": 1}


@pytest.mark.parametrize("response,start,end", [
    (None, "2026-10-01", "2026-10-03"),
    ({"totalGames": 0}, "2026-10-01", "2026-10-03"),
    ({"dates": []}, "2026-10-03", "2026-10-01"),
    ({"dates": []}, "not-a-date", "2026-10-01"),
])
def test_playable_games_by_date_never_reads_unknown_as_no_games(response, start, end):
    assert contract.playable_games_by_date(response, start, end) is None


def test_probe_writes_evidence_for_a_fixed_et_window(tmp_path):
    calls = []

    def fake_fetch(start, end):
        calls.append((start, end))
        return {"dates": [{"date": "2026-10-31", "games": [{"gameType": "W"}]}]}

    out = tmp_path / "ev.json"
    now = datetime(2026, 11, 5, 13, 0, 0, tzinfo=timezone.utc)
    assert probe.main(["--out", str(out)], now=now, fetch=fake_fetch) == 0
    ev = json.loads(out.read_text())
    assert calls == [("2026-10-22", "2026-11-05")]
    assert ev["playableGamesByDate"]["2026-10-31"] == 1
    assert ev["playableGamesByDate"]["2026-11-01"] == 0
    assert G.verified_off_days(ev) >= {"2026-11-01", "2026-11-04"}


def test_probe_records_a_failed_fetch_and_the_gate_skips_nothing(tmp_path):
    out = tmp_path / "ev.json"
    now = datetime(2026, 11, 5, 13, 0, 0, tzinfo=timezone.utc)
    assert probe.main(["--out", str(out)], now=now, fetch=lambda s, e: None) == 0
    ev = json.loads(out.read_text())
    assert "fetchError" in ev
    assert G.verified_off_days(ev) == frozenset()


def test_probe_lookback_covers_the_inactive_window():
    assert probe.DEFAULT_LOOKBACK_DAYS >= G.INACTIVE_PIPELINE_DAYS + G.MAX_LEDGER_COMMIT_LAG_DAYS


def test_schedule_evidence_loader(tmp_path):
    assert G._load_schedule_evidence(None) is None
    assert "fetchError" in G._load_schedule_evidence(str(tmp_path / "missing.json"))
    p = tmp_path / "ev.json"
    p.write_text(json.dumps({"start": "x"}))
    assert G._load_schedule_evidence(str(p)) == {"start": "x"}
