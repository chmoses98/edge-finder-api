#!/usr/bin/env python3
"""
tests/test_team_offense_form.py
===================================
Offensive-form consistency (Mission 3).

The property this whole mission exists for, asserted directly:
**one 20-run game must not, by itself, make an offense read as hot.**

Also pins that the addition is strictly ADDITIVE -- the production
offense-baseline blend that feeds run projections must be byte-identical
whether or not the form context is present.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.team_offense_form import (  # noqa: E402
    FORM_WINDOWS,
    build_form,
    ewma,
    format_form_line,
    hot_label,
    trimmed_mean,
    window_profile,
)
from lib.team_total_line_history import (  # noqa: E402
    REASON_NO_CROSSING,
    REASON_NON_MONOTONE,
    REASON_TOO_FEW_RUNGS,
    derive_team_total_lines,
    implied_line_from_ladder,
    margin_vs_line,
    team_lines_for_date,
)

_spec = importlib.util.spec_from_file_location(
    "fetch_team_offense_form", os.path.join(ROOT, "scripts", "fetch_team_offense_form.py"))
fetch_team_offense_form = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_team_offense_form)


# The mission's own example: a 7-game window whose mean is above league
# average purely because of a single explosion.
OUTLIER_WINDOW = [3, 4, 2, 20, 5, 1, 1]
# Same mean neighbourhood, but earned repeatedly.
CONSISTENT_WINDOW = [5, 4, 6, 4, 7, 5, 4]
COLD_WINDOW = [1, 2, 0, 3, 1, 2, 1]


# ── the headline property ───────────────────────────────────────────────

def test_one_twenty_run_game_cannot_by_itself_make_an_offense_hot():
    form = build_form(OUTLIER_WINDOW)
    profile = form["windows"]["L7"]

    # The mean alone WOULD say "hot" -- that is the trap.
    assert profile["mean"] > 5.0
    # Everything else says otherwise, loudly.
    assert profile["median"] == 3.0
    assert profile["thresholdClears"]["ge5"]["games"] == 2
    assert profile["outlierDependence"]["isOutlierDependent"] is True
    assert profile["topGameShareOfWindowRuns"] > 0.5

    label, reason = hot_label(form)
    assert label == "OUTLIER_INFLATED"
    assert "carried by one game" in reason


def test_repeated_scoring_is_what_earns_the_hot_label():
    form = build_form(CONSISTENT_WINDOW)
    label, reason = hot_label(form)
    assert label == "HOT"
    assert "repeated scoring" in reason
    assert form["windows"]["L7"]["outlierDependence"]["isOutlierDependent"] is False


def test_cold_offense_is_labelled_cold():
    assert hot_label(build_form(COLD_WINDOW))[0] == "COLD"


def test_the_two_windows_have_similar_means_but_opposite_labels():
    """Direct proof that the label is not a function of the mean."""
    outlier = build_form(OUTLIER_WINDOW)["windows"]["L7"]
    consistent = build_form(CONSISTENT_WINDOW)["windows"]["L7"]
    assert abs(outlier["mean"] - consistent["mean"]) < 0.25
    assert hot_label(build_form(OUTLIER_WINDOW))[0] != hot_label(build_form(CONSISTENT_WINDOW))[0]


# ── distribution math ───────────────────────────────────────────────────

def test_window_profile_reports_the_actual_scores_in_order():
    profile = window_profile(OUTLIER_WINDOW, 7)
    assert profile["scores"] == OUTLIER_WINDOW
    assert profile["max"] == 20
    assert profile["min"] == 1
    assert profile["totalRuns"] == 36


def test_threshold_clears_cover_every_ladder_rung():
    profile = window_profile(OUTLIER_WINDOW, 7)
    assert profile["thresholdClears"]["ge3"]["games"] == 4   # 3,4,20,5
    assert profile["thresholdClears"]["ge4"]["games"] == 3   # 4,20,5
    assert profile["thresholdClears"]["ge5"]["games"] == 2   # 20,5
    assert profile["thresholdClears"]["ge6"]["games"] == 1   # 20
    assert profile["thresholdClears"]["ge4"]["of"] == 7


def test_trimmed_mean_drops_the_extremes():
    assert trimmed_mean([1, 2, 3, 4, 100]) == 3.0
    assert trimmed_mean([1, 2]) is None, "trimming must never leave an empty set"


def test_ewma_weights_the_most_recent_game_highest():
    rising = ewma([1, 1, 1, 9], half_life=2)
    falling = ewma([9, 1, 1, 1], half_life=2)
    assert rising > falling


def test_a_window_with_too_few_games_is_none_not_padded():
    form = build_form([4, 5, 6])
    assert form["windows"]["L5"] is None
    assert form["windows"]["L7"] is None
    assert "unavailable" in format_form_line(form)


def test_games_with_no_recorded_runs_are_excluded_not_zero_filled():
    form = build_form([4, None, 5, None, 6, 7, 3, 2])
    assert form["windows"]["L5"]["scores"] == [5, 6, 7, 3, 2]


def test_every_configured_window_is_present():
    form = build_form(list(range(1, 15)))
    assert set(form["windows"]) == {f"L{w}" for w in FORM_WINDOWS}


# ── the human-readable line ─────────────────────────────────────────────

def test_form_line_shows_magnitude_and_consistency_together():
    line = format_form_line(build_form(OUTLIER_WINDOW))
    assert "5.1 avg" in line
    assert "3.0 med" in line
    assert "3,4,2,20,5,1,1" in line
    assert "3/7 >=4" in line
    assert "2/7 >=5" in line
    assert "max 20" in line
    assert "OUTLIER-DEPENDENT" in line


def test_form_line_omits_the_market_segment_when_no_lines_were_archived():
    """Absent must read as 'not measured', never as zero."""
    assert "TT overs" not in format_form_line(build_form(OUTLIER_WINDOW))


def test_form_line_shows_team_total_record_when_lines_exist():
    margins = [-1.5, 0.5, -2.5, 15.5, 0.5, -3.5, -2.5]
    line = format_form_line(build_form(OUTLIER_WINDOW, line_margins=margins))
    assert "TT overs 3/7" in line


def test_market_relative_stats_exclude_games_with_no_line():
    margins = [None, 0.5, None, 15.5, 0.5, -3.5, -2.5]
    market = build_form(OUTLIER_WINDOW, line_margins=margins)["windows"]["L7"]["marketRelative"]
    assert market["lineCoverage"] == {"withLine": 5, "of": 7}
    assert market["overs"] == 3
    assert market["oversPct"] == 0.6


# ── team-total line derivation ──────────────────────────────────────────

def _ladder(pairs):
    return sorted(pairs, key=lambda p: p[0])


def test_implied_line_is_the_fifty_percent_crossing():
    line, reason = implied_line_from_ladder(_ladder([(3.5, 0.60), (4.5, 0.40)]))
    assert reason is None
    assert line == pytest.approx(4.0, abs=0.01)


def test_implied_line_interpolates_between_rungs():
    line, reason = implied_line_from_ladder(_ladder([(3.5, 0.75), (4.5, 0.25)]))
    assert reason is None
    assert line == pytest.approx(4.0, abs=0.01)


def test_ladder_that_never_crosses_fifty_percent_is_refused():
    line, reason = implied_line_from_ladder(_ladder([(3.5, 0.70), (4.5, 0.60)]))
    assert line is None
    assert reason == REASON_NO_CROSSING


def test_materially_non_monotone_ladder_is_refused():
    line, reason = implied_line_from_ladder(_ladder([(3.5, 0.40), (4.5, 0.70)]))
    assert line is None
    assert reason.startswith(REASON_NON_MONOTONE)


def test_a_single_rung_is_never_enough_for_a_line():
    assert implied_line_from_ladder([(4.5, 0.5)]) == (None, REASON_TOO_FEW_RUNGS)


def _obs(ticker, team, threshold, yes_bid, yes_ask, *, checkpoint="T_MINUS_60",
         captured="2026-09-10T22:00:00Z", game_id="G1"):
    return {"marketTicker": ticker, "marketFamily": "team_total", "team": team,
            "threshold": threshold, "yesBid": yes_bid, "yesAsk": yes_ask,
            "comparisonOperator": "OVER", "checkpoint": checkpoint,
            "capturedAt": captured, "gameId": game_id}


def test_live_in_game_prices_never_enter_a_pregame_line():
    """A POST_START observation is a live price, not an expectation."""
    observations = [
        _obs("T-3", "MIL", 3.5, 58, 62),
        _obs("T-4", "MIL", 4.5, 38, 42),
        # A much later, in-game quote that would move the line if used.
        _obs("T-3", "MIL", 3.5, 90, 94, checkpoint="POST_START", captured="2026-09-11T02:00:00Z"),
    ]
    lines = derive_team_total_lines(observations)
    assert lines[("G1", "MIL")]["impliedLine"] == pytest.approx(4.0, abs=0.05)


def test_latest_pregame_quote_wins_for_a_ticker():
    observations = [
        _obs("T-3", "MIL", 3.5, 88, 92, captured="2026-09-10T12:00:00Z"),
        _obs("T-3", "MIL", 3.5, 58, 62, captured="2026-09-10T22:00:00Z"),
        _obs("T-4", "MIL", 4.5, 38, 42, captured="2026-09-10T22:00:00Z"),
    ]
    assert derive_team_total_lines(observations)[("G1", "MIL")]["impliedLine"] == pytest.approx(4.0, abs=0.05)


def test_a_team_with_two_distinct_ladders_that_date_is_excluded_not_averaged():
    """A doubleheader (or an identity collision) has no single line."""
    observations = [
        _obs("A-3", "NYY", 3.5, 58, 62, game_id="G1"),
        _obs("A-4", "NYY", 4.5, 38, 42, game_id="G1"),
        _obs("B-5", "NYY", 5.5, 58, 62, game_id="G2"),
        _obs("B-6", "NYY", 6.5, 38, 42, game_id="G2"),
    ]
    lines, reasons = team_lines_for_date(observations)
    assert "NYY" not in lines
    assert "MULTIPLE_GAMES_FOR_TEAM_THAT_DATE" in reasons["NYY"]


def test_margin_vs_line_is_none_when_either_side_is_missing():
    assert margin_vs_line(5, None) is None
    assert margin_vs_line(None, 4.0) is None
    assert margin_vs_line(5, 4.0) == 1.0


def test_line_derivation_works_on_the_real_committed_archive():
    """Exercised against real production data shapes, not only fixtures."""
    from lib.edgelab import storage
    path = os.path.join(ROOT, "data", "edgelab", "observations", "2026-09-10.jsonl.gz")
    if not os.path.exists(path):
        pytest.skip("no committed observation archive in this checkout")
    observations = list(storage.read_records(path))
    lines, _reasons = team_lines_for_date(observations)
    assert lines, "expected at least one usable team-total ladder in the real archive"
    for team, line in lines.items():
        assert 0.5 < line < 15.0, f"{team} implied line {line} is not a plausible team total"


# ── the production capture script ───────────────────────────────────────

def _schedule(date, away, home, away_runs, home_runs, *, status="Final", game_pk=1):
    return {"gamePk": game_pk, "status": {"detailedState": status}, "gameNumber": 1,
            "teams": {"away": {"team": {"id": away}, "score": away_runs},
                      "home": {"team": {"id": home}, "score": home_runs}}}


def test_parse_team_game_log_records_both_sides_chronologically():
    schedule = {"dates": [
        {"date": "2026-09-10", "games": [_schedule("2026-09-10", 158, 134, 7, 2, game_pk=1)]},
        {"date": "2026-09-11", "games": [_schedule("2026-09-11", 158, 134, 1, 4, game_pk=2)]},
    ]}
    log = fetch_team_offense_form.parse_team_game_log(schedule)
    assert [g["runsScored"] for g in log["MIL"]] == [7, 1]
    assert [g["runsScored"] for g in log["PIT"]] == [2, 4]
    assert log["MIL"][0]["opponent"] == "PIT"


def test_incomplete_games_are_skipped_not_scored_as_zero():
    schedule = {"dates": [{"date": "2026-09-10", "games": [
        _schedule("2026-09-10", 158, 134, 7, 2, game_pk=1),
        _schedule("2026-09-10", 147, 111, None, None, status="Postponed", game_pk=2),
        _schedule("2026-09-10", 112, 145, 3, 1, status="In Progress", game_pk=3),
    ]}]}
    log = fetch_team_offense_form.parse_team_game_log(schedule)
    assert [g["runsScored"] for g in log["MIL"]] == [7]
    assert "NYY" not in log
    assert "CHC" not in log


def test_capture_refuses_to_write_when_the_schedule_fetch_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_team_offense_form.mlb_schedule, "fetch_schedule_range",
                        lambda start, end, **kw: None)
    monkeypatch.setattr(sys, "argv", ["fetch_team_offense_form.py", "--as-of", "2026-09-16"])
    assert fetch_team_offense_form.main() == 1
    assert not os.path.exists(fetch_team_offense_form.OUTPUT_PATH)
    assert "writing nothing" in capsys.readouterr().err


def test_capture_writes_a_form_profile_per_team(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    days = []
    for index, runs in enumerate(OUTLIER_WINDOW + [4, 3, 5], start=1):
        days.append({"date": f"2026-09-{index:02d}",
                     "games": [_schedule("", 158, 134, runs, 3, game_pk=index)]})
    monkeypatch.setattr(fetch_team_offense_form.mlb_schedule, "fetch_schedule_range",
                        lambda start, end, **kw: {"dates": days})
    monkeypatch.setattr(sys, "argv",
                        ["fetch_team_offense_form.py", "--as-of", "2026-09-16", "--no-market-lines"])
    assert fetch_team_offense_form.main() == 0

    with open(fetch_team_offense_form.OUTPUT_PATH) as f:
        payload = json.load(f)
    milwaukee = payload["teams"]["MIL"]
    assert milwaukee["formLabel"]["label"] in {"HOT", "NEUTRAL", "COLD", "OUTLIER_INFLATED"}
    assert "L7" in milwaukee["formLines"]
    assert payload["marketLineSource"]["status"] == "SKIPPED_BY_FLAG"
    assert "No production model weight reads this file" in payload["note"]


# ── the addition must be strictly additive ──────────────────────────────

def _slate_team_abbrs(slate_path):
    """Every team abbreviation on a slate file, in stable order.

    Keeps the offensive-form fixture pinned to the slate actually committed
    in this checkout, so the test measures behaviour rather than measuring
    which teams happened to play on the day the fixture was written.
    """
    with open(slate_path) as fh:
        slate = json.load(fh)
    abbrs = []
    for game in slate.get("games") or []:
        for side in ("awayTeamStats", "homeTeamStats"):
            abbr = (game.get(side) or {}).get("abbr")
            if abbr and abbr not in abbrs:
                abbrs.append(abbr)
    return abbrs


def test_enrich_data_offense_baseline_is_identical_with_and_without_form_context(tmp_path):
    """
    The guard against silently changing production behaviour: run
    scripts/enrich_data.py twice over identical inputs -- once with
    data/team_offense_form.json present and once without -- and assert
    every projection-feeding field is byte-identical. Only the new
    descriptive keys may differ.
    """
    import shutil
    import subprocess

    required = ["slate.json", "teamstats.json", "bullpen.json", "oppquality.json", "savant_team.json"]
    source = os.path.join(ROOT, "data")
    if not all(os.path.exists(os.path.join(source, name)) for name in required):
        pytest.skip("this checkout does not carry a full committed slate fixture")

    def _run(with_form):
        work = tmp_path / ("with_form" if with_form else "without_form")
        (work / "data").mkdir(parents=True)
        for name in required:
            shutil.copy(os.path.join(source, name), work / "data" / name)
        if with_form:
            # The abbreviations MUST come from the slate this checkout
            # actually carries, not from a hardcoded list. OUTLIER_WINDOW is
            # synthetic, so the OUTLIER_INFLATED label is produced by the
            # fixture and would attach to any team -- but only if that team is
            # ON the slate. data/slate.json rolls forward daily, so a fixed
            # list (MIL/PIT/NYY/BOS) stops intersecting it, no form context
            # attaches to anything, and the final assertion below fails for a
            # reason unrelated to the behaviour under test. Measured on
            # 2026-09-21: the committed slate held BAL/DET/MIN/SF/TOR/WSH and
            # none of the four.
            teams = {}
            for abbr in _slate_team_abbrs(work / "data" / "slate.json"):
                form = build_form(OUTLIER_WINDOW, team=abbr, as_of_date="2026-09-16")
                label, reason = hot_label(form)
                form["formLabel"] = {"label": label, "reason": reason, "window": "L7"}
                form["formLines"] = {"L7": format_form_line(form)}
                teams[abbr] = form
            with open(work / "data" / "team_offense_form.json", "w") as f:
                json.dump({"schemaVersion": "1", "asOfDate": "2026-09-16", "teams": teams}, f)
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "enrich_data.py")],
            cwd=work, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stderr
        with open(work / "data" / "slate.json") as f:
            return json.load(f)

    with_form, without_form = _run(True), _run(False)
    projection_fields = (
        "offenseBaselineRaw", "offenseBaselineBayes", "offenseBaselineOppAdj",
        "offenseBaselineAdj", "lineupAdjApplied", "last7RpG", "last15RpG",
        "runsPerGame", "oppQualityAdj", "rpgIndex", "wrcPlus",
    )
    for a, b in zip(with_form["games"], without_form["games"]):
        for side in ("awayTeamStats", "homeTeamStats"):
            for field in projection_fields:
                assert (a.get(side) or {}).get(field) == (b.get(side) or {}).get(field), (
                    f"{side}.{field} changed when offensive-form context was added -- "
                    f"this addition must be descriptive only"
                )
    # ...and the new context really was attached in the with-form run.
    attached = [g[s].get("offenseFormLabel") for g in with_form["games"]
                for s in ("awayTeamStats", "homeTeamStats") if g.get(s)]
    assert attached, "no team stats on the committed slate to attach form context to"
    # OUTLIER_WINDOW is synthetic, so every team built above is inflated by
    # construction. This asserts the context actually reached the slate --
    # the mechanism -- not that any particular real team was hot.
    assert any(label == "OUTLIER_INFLATED" for label in attached)
