"""The MLB projection feed is strict JSON: NaN / Infinity never reach the file a browser parses.

Regression for the 2026-10-19 publication whose hitters facing an unannounced starter carried `"factor": NaN` (invalid JSON, so SIFT
could not read the feed). A genuinely missing optional descriptor is null; an invalid required value or a missing required field
fails the publication; malformed input is rejected rather than half-written.
"""
import copy
import json
import math
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

PF = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PF not in sys.path:
    sys.path.insert(0, PF)
from mlb import feed  # noqa: E402
from mlb.live import run as RUN  # noqa: E402


def player(**kw):
    p = {"player_id": "607192", "name": "Test Pitcher", "team": "LAD", "opponent": "ATL", "game_id": "g1", "family": "pitcher_strikeouts",
         "mean": 6.2, "median": 6, "p10": 3, "p90": 9, "thresholds": {"5": 0.8, "7": 0.4}, "readiness": "READY",
         "opponent_adjustment": {"factor": 1.02, "sample_games": None}, "gate": {"status": "PASS", "findings": []},
         "participation": {"status": "CONFIRMED_ACTIVE"}, "adjustments": []}
    p.update(kw)
    return p


def doc(*players):
    return {"schema": "sift.projections.v1", "sport": "MLB", "generated_utc": "2026-10-19T05:29:42Z", "data_asof_utc": "2026-10-19T02:29:10Z",
            "model": {"id": "pf-mlb", "authority": "research_only"}, "slate": {}, "families": {"pitcher_strikeouts": {"readiness": "READY"}},
            "games": [], "players": list(players) or [player()]}


def strict_loads(text):
    """What a browser's JSON.parse accepts: no NaN / Infinity / -Infinity tokens."""
    def refuse(token):
        raise ValueError(f"not valid JSON: {token}")
    return json.loads(text, parse_constant=refuse)


# ------------------------------------------------------------------ non-finite values
def test_nan_in_the_optional_opponent_factor_is_published_as_null():
    d = doc(player(opponent_adjustment={"factor": float("nan"), "sample_games": None}))
    text = feed.dumps(feed.validate(d), sort_keys=True)
    assert strict_loads(text)["players"][0]["opponent_adjustment"] == {"factor": None, "sample_games": None}
    assert "NaN" not in text


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_in_a_required_number_fails_publication(bad):
    for field in ("mean", "median", "p10", "p90"):
        with pytest.raises(feed.FeedError, match=f"players.*{field}"):
            feed.validate(doc(player(**{field: bad})))


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_in_a_threshold_probability_fails_publication(bad):
    with pytest.raises(feed.FeedError):
        feed.validate(doc(player(thresholds={"5": bad})))


def test_infinity_in_the_optional_factor_is_also_null_not_infinity():
    d = doc(player(opponent_adjustment={"factor": float("inf"), "sample_games": None}))
    assert strict_loads(feed.dumps(feed.validate(d)))["players"][0]["opponent_adjustment"]["factor"] is None


def test_non_finite_anywhere_else_is_not_silently_nulled():
    d = doc()
    d["games"] = [{"game_id": "g1", "teams": [{"team": "LAD", "mean": float("nan")}]}]
    with pytest.raises(feed.FeedError, match="games"):
        feed.validate(d)
    d2 = doc(player(opportunity={"family": "batters_faced", "mean": float("inf")}))
    with pytest.raises(feed.FeedError, match="opportunity"):
        feed.validate(d2)


def test_dumps_refuses_a_stray_nan_even_if_validation_was_skipped():
    with pytest.raises(ValueError):
        feed.dumps({"x": float("nan")})


def test_validate_does_not_mutate_its_input():
    d = doc(player(opponent_adjustment={"factor": float("nan"), "sample_games": None}))
    before = copy.deepcopy(d)
    feed.validate(d)
    assert math.isnan(d["players"][0]["opponent_adjustment"]["factor"]) and d.keys() == before.keys()


def test_finite_round():
    assert feed.finite_round(1.23456) == 1.235
    for x in (float("nan"), float("inf"), None, "abc"):
        assert feed.finite_round(x) is None


# ------------------------------------------------------------------ missing fields
@pytest.mark.parametrize("field", ["player_id", "game_id", "family", "readiness"])
def test_missing_required_text_field_fails(field):
    p = player()
    del p[field]
    with pytest.raises(feed.FeedError, match=field):
        feed.validate(doc(p))


@pytest.mark.parametrize("field", ["mean", "median", "p10", "p90"])
def test_missing_or_null_required_number_fails(field):
    p = player()
    del p[field]
    with pytest.raises(feed.FeedError, match=field):
        feed.validate(doc(p))
    with pytest.raises(feed.FeedError, match=field):
        feed.validate(doc(player(**{field: None})))


def test_missing_gate_or_thresholds_or_unknown_status_fails():
    p = player()
    del p["gate"]
    with pytest.raises(feed.FeedError, match="gate"):
        feed.validate(doc(p))
    with pytest.raises(feed.FeedError, match="gate"):
        feed.validate(doc(player(gate={"status": "MAYBE"})))
    p = player()
    del p["thresholds"]
    with pytest.raises(feed.FeedError, match="thresholds"):
        feed.validate(doc(p))
    with pytest.raises(feed.FeedError, match="thresholds"):
        feed.validate(doc(player(thresholds={"5": 1.4})))


def test_missing_top_level_fields_fail():
    for k in ("schema", "sport", "generated_utc", "players", "games", "families"):
        d = doc()
        del d[k]
        with pytest.raises(feed.FeedError):
            feed.validate(d)
    d = doc()
    d["schema"] = "sift.projections.v0"
    with pytest.raises(feed.FeedError, match="schema"):
        feed.validate(d)


def test_missing_optional_descriptor_is_fine():
    p = player()
    del p["opponent_adjustment"]
    assert feed.validate(doc(p))["players"][0].get("opponent_adjustment") is None


def test_every_problem_is_named_and_long_lists_are_summarised():
    err = None
    try:
        feed.validate(doc(*[player(mean=float("nan"), median=None) for _ in range(6)]))
    except feed.FeedError as e:
        err = str(e)
    assert err and "was not published" in err and "more" in err


# ------------------------------------------------------------------ malformed JSON / wrong shapes
@pytest.mark.parametrize("raw", ["", "{", '{"players": [}', "NaN", '{"players": [{"mean": NaN}]}', "[1, 2"])
def test_malformed_or_non_finite_text_is_rejected_by_a_strict_parser(raw):
    with pytest.raises(ValueError):
        strict_loads(raw)


@pytest.mark.parametrize("not_a_feed", [None, [], "text", 5])
def test_wrong_shape_fails(not_a_feed):
    with pytest.raises(feed.FeedError):
        feed.validate(not_a_feed)


def test_player_row_that_is_not_an_object_fails():
    d = doc()
    d["players"] = [5]
    with pytest.raises(feed.FeedError, match="not an object"):
        feed.validate(d)


# ------------------------------------------------------------------ the publish stage
def _ctx(tmp_path, projection_out):
    work, arch = tmp_path / "work", tmp_path / "arch"
    work.mkdir()
    arch.mkdir()
    (work / "projection_out.json").write_text(json.dumps(projection_out))     # Python's default: writes NaN, like the real stage
    return SimpleNamespace(workdir=str(work), archive=str(arch), now=datetime(2026, 10, 19, 5, 29, tzinfo=timezone.utc)), arch


def test_publish_writes_strict_json_and_reports_nulled_optionals(tmp_path):
    ctx, arch = _ctx(tmp_path, doc(player(opponent_adjustment={"factor": float("nan"), "sample_games": None}), player(player_id="2")))
    res = RUN.stage_publish(ctx)
    assert res["players"] == 2 and res["optional_values_nulled"] == 1
    for f in (arch / "projections" / "mlb").glob("*.json"):
        text = f.read_text()
        assert "NaN" not in text and "Infinity" not in text
        assert strict_loads(text)["players"][0]["opponent_adjustment"]["factor"] is None
        assert "_nulled_optional" not in text


def test_publish_fails_and_leaves_the_last_good_feed_when_a_required_value_is_invalid(tmp_path):
    ctx, arch = _ctx(tmp_path, doc())
    RUN.stage_publish(ctx)
    good = (arch / "projections" / "mlb" / "latest.json").read_text()
    (tmp_path / "work" / "projection_out.json").write_text(json.dumps(doc(player(mean=float("nan")))))
    with pytest.raises(feed.FeedError, match="was not published"):
        RUN.stage_publish(ctx)
    assert (arch / "projections" / "mlb" / "latest.json").read_text() == good
    assert sorted(p.name for p in (arch / "projections" / "mlb").glob("*.json")) == sorted(["latest.json", "2026-10-19T0529Z.json"])


def test_publish_heals_a_previously_published_corrupt_feed(tmp_path):
    ctx, arch = _ctx(tmp_path, doc(player(opponent_adjustment={"factor": float("nan"), "sample_games": None})))
    d = arch / "projections" / "mlb"
    d.mkdir(parents=True)
    (d / "latest.json").write_text(json.dumps(doc(player(opponent_adjustment={"factor": float("nan"), "sample_games": None}))))   # NaN on disk
    res = RUN.stage_publish(ctx)
    assert res["changed"] is True
    strict_loads((d / "latest.json").read_text())


def test_unchanged_content_is_not_republished(tmp_path):
    ctx, arch = _ctx(tmp_path, doc(player(opponent_adjustment={"factor": float("nan"), "sample_games": None})))
    assert RUN.stage_publish(ctx)["changed"] is True
    assert RUN.stage_publish(ctx)["changed"] is False


# ------------------------------------------------------------------ scorecards (undefined statistics)
def test_scorecard_undefined_statistics_become_null_and_are_counted():
    card = {"promotion": {"z": float("nan"), "ci95": [float("-inf"), 0.2], "n_clusters": 1}, "rows": [{"se": float("inf"), "mean": 1.5}]}
    out, n = feed.nullify_nonfinite(card)
    assert n == 3 and out["promotion"] == {"z": None, "ci95": [None, 0.2], "n_clusters": 1} and out["rows"][0] == {"se": None, "mean": 1.5}
    assert math.isnan(card["promotion"]["z"])                      # the input is untouched
    strict_loads(json.dumps(out, allow_nan=False))


# ------------------------------------------------------------------ the CI gate on what is about to be published
def test_strict_json_gate_flags_non_finite_published_files_but_not_old_history(tmp_path):
    sys.path.insert(0, os.path.join(PF, "ci"))
    import strict_json_check as gate
    for d in ("projections/mlb", "summary/mlb", "scorecards/mlb"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "projections/mlb/latest.json").write_text('{"players": []}')
    (tmp_path / "projections/mlb/2026-10-19T0529Z.json").write_text('{"factor": NaN}')       # immutable history is left alone
    (tmp_path / "summary/mlb/latest.json").write_text("{}")
    (tmp_path / "scorecards/mlb/daily.json").write_text('{"z": 1.5}')
    assert gate.check(str(tmp_path)) == []
    (tmp_path / "scorecards/mlb/season.json").write_text('{"z": NaN}')
    (tmp_path / "projections/mlb/latest.json").write_text('{"x": Infinity}')
    bad = gate.check(str(tmp_path))
    assert len(bad) == 2 and any("season.json" in b for b in bad) and any("latest.json" in b and "Infinity" in b for b in bad)
    (tmp_path / "summary/mlb/latest.json").write_text("{")
    assert any("summary" in b for b in gate.check(str(tmp_path)))                            # malformed JSON is flagged too
