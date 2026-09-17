#!/usr/bin/env python3
"""
tests/test_single_game_fetch.py
===================================
Single-game fetch (Mission 2): friendly selector resolution, fail-closed
doubleheader handling, and the archive-before-filter invariant.

The two properties that matter most and are asserted hardest:

  * a doubleheader (or any ambiguous selector) can NEVER silently
    resolve to one game -- it refuses and prints every candidate gamePk;
  * the raw Kalshi capture archived by a single-game run is the COMPLETE
    unfiltered market universe, and filtering is structurally unable to
    happen before that archive has been written AND verified.
"""
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib.single_game_selector import (  # noqa: E402
    REASON_AMBIGUOUS_GAME,
    REASON_AMBIGUOUS_TEAM,
    REASON_NO_GAME,
    REASON_UNKNOWN_GAME_PK,
    REASON_UNKNOWN_TEAM,
    annotate_game,
    describe_candidate,
    parse_selector,
    resolve_single_game,
    resolve_team_token,
)

_spec = importlib.util.spec_from_file_location(
    "fetch_single_game", os.path.join(ROOT, "scripts", "fetch_single_game.py"))
fetch_single_game = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_single_game)


DATE = "2026-09-17"


def _sched(game_pk, away_id, home_id, *, game_number=1, start="2026-09-17T17:10:00Z",
           status="Scheduled", venue="Fenway Park"):
    return {"gamePk": game_pk, "awayTeamId": away_id, "homeTeamId": home_id,
            "gameNumber": game_number, "scheduledStart": start, "status": status, "venue": venue}


NYY, BOS, MIL, PIT, CHC, CWS = 147, 111, 158, 134, 112, 145

SINGLE_DAY = [
    _sched(101, NYY, BOS),
    _sched(102, MIL, PIT, start="2026-09-17T16:35:00Z", venue="PNC Park"),
    _sched(103, CHC, CWS, start="2026-09-17T20:10:00Z", venue="Rate Field"),
]
DOUBLEHEADER_DAY = [
    _sched(201, NYY, BOS, game_number=1, start="2026-09-17T17:10:00Z"),
    _sched(202, NYY, BOS, game_number=2, start="2026-09-17T23:10:00Z"),
    _sched(203, MIL, PIT, start="2026-09-17T16:35:00Z"),
]


# ── team-token resolution ───────────────────────────────────────────────

@pytest.mark.parametrize("token,expected", [
    ("NYY", "NYY"), ("nyy", "NYY"), ("Yankees", "NYY"), ("New York Yankees", "NYY"),
    ("Red Sox", "BOS"), ("BOS", "BOS"), ("Boston", "BOS"),
    ("Brewers", "MIL"), ("Milwaukee", "MIL"),
    ("ARI", "AZ"), ("Arizona", "AZ"), ("Diamondbacks", "AZ"),
    ("OAK", "ATH"), ("Athletics", "ATH"),
    ("CHW", "CWS"), ("White Sox", "CWS"), ("Cubs", "CHC"),
    ("WAS", "WSH"), ("Nationals", "WSH"),
])
def test_friendly_team_names_and_abbreviations_resolve(token, expected):
    abbr, reason = resolve_team_token(token)
    assert (abbr, reason) == (expected, None)


@pytest.mark.parametrize("token,reason_code", [
    ("Chicago", REASON_AMBIGUOUS_TEAM),
    ("Sox", REASON_AMBIGUOUS_TEAM),
    ("New York", REASON_AMBIGUOUS_TEAM),
    ("LA", REASON_AMBIGUOUS_TEAM),
    ("Toronto Maple Leafs", REASON_UNKNOWN_TEAM),
    ("", REASON_UNKNOWN_TEAM),
])
def test_ambiguous_or_unknown_team_tokens_refuse(token, reason_code):
    abbr, reason = resolve_team_token(token)
    assert abbr is None
    assert reason.startswith(reason_code)


@pytest.mark.parametrize("selector", [
    "Yankees vs Red Sox", "Yankees vs. Red Sox", "NYY@BOS", "NYY @ BOS",
    "Yankees at Red Sox", "NYY/BOS", "Red Sox vs Yankees",
])
def test_matchup_selectors_parse_to_the_same_pair(selector):
    abbrs, reason = parse_selector(selector)
    assert reason is None
    assert set(abbrs) == {"NYY", "BOS"}


# ── game resolution ─────────────────────────────────────────────────────

def test_matchup_selector_resolves_on_a_normal_single_game_day():
    game, candidates, reason = resolve_single_game(SINGLE_DAY, selector="Yankees vs Red Sox")
    assert reason is None
    assert game["gamePk"] == 101
    assert game["matchup"] == "NYY@BOS"
    assert len(candidates) == 1


def test_team_abbreviation_alone_resolves_when_that_team_plays_once():
    game, _candidates, reason = resolve_single_game(SINGLE_DAY, selector="MIL")
    assert reason is None
    assert game["gamePk"] == 102
    assert game["matchup"] == "MIL@PIT"


def test_doubleheader_fails_closed_and_lists_every_candidate_gamepk():
    """The headline safety property: a doubleheader must never silently
    resolve to one leg."""
    game, candidates, reason = resolve_single_game(DOUBLEHEADER_DAY, selector="Yankees vs Red Sox")
    assert game is None
    assert reason.startswith(REASON_AMBIGUOUS_GAME)
    assert "doubleheader" in reason
    assert sorted(c["gamePk"] for c in candidates) == [201, 202]
    printed = [describe_candidate(c) for c in candidates]
    assert any("gamePk=201" in line for line in printed)
    assert any("gamePk=202" in line for line in printed)


def test_doubleheader_is_resolvable_with_an_exact_gamepk():
    game, _candidates, reason = resolve_single_game(DOUBLEHEADER_DAY, game_pk=202)
    assert reason is None
    assert game["gamePk"] == 202
    assert game["isDoubleheaderLeg"] is True


def test_gamepk_is_still_validated_against_the_schedule():
    """An exact id is an identity, not a licence to skip verification."""
    game, _candidates, reason = resolve_single_game(DOUBLEHEADER_DAY, game_pk=999999)
    assert game is None
    assert reason.startswith(REASON_UNKNOWN_GAME_PK)


def test_a_team_not_playing_that_day_refuses_rather_than_picking_something():
    game, candidates, reason = resolve_single_game(SINGLE_DAY, selector="Padres")
    assert (game, candidates) == (None, [])
    assert reason.startswith(REASON_NO_GAME)


def test_gamepk_overrides_an_otherwise_ambiguous_selector():
    game, _candidates, reason = resolve_single_game(
        DOUBLEHEADER_DAY, selector="Yankees vs Red Sox", game_pk=201)
    assert reason is None
    assert game["gamePk"] == 201


# ── archive-before-filter invariant ─────────────────────────────────────

def _payload(n=25):
    return {"date": DATE, "markets": [
        {"ticker": f"KXMLBGAME-26SEP171635MILPIT-T{i}", "yes_bid": 0.4, "yes_ask": 0.42,
         "title": f"market {i}", "status": "active"} for i in range(n)
    ]}


def test_archive_writes_the_complete_unfiltered_universe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _payload(37)
    path, count = fetch_single_game.archive_full_universe(payload, DATE)
    assert count == 37
    with open(path) as f:
        archived = json.load(f)
    assert len(fetch_single_game.extract_markets(archived)) == 37
    assert archived["date"] == DATE
    assert archived["capture_source"] == "fetch_single_game"


def test_archive_never_overwrites_the_primary_dated_snapshot(tmp_path, monkeypatch):
    """The canonical `kalshi_search_<date>.json` belongs to the scheduled
    capture workflow; a single-game run is strictly additive."""
    monkeypatch.chdir(tmp_path)
    snapshot_dir = fetch_single_game.SNAPSHOT_DIR
    os.makedirs(snapshot_dir, exist_ok=True)
    primary = os.path.join(snapshot_dir, f"kalshi_search_{DATE}.json")
    with open(primary, "w") as f:
        json.dump({"markets": [{"ticker": "CANONICAL"}]}, f)
    before = open(primary, "rb").read()

    path, _count = fetch_single_game.archive_full_universe(_payload(5), DATE)
    assert os.path.abspath(path) != os.path.abspath(primary)
    assert open(primary, "rb").read() == before


def test_verify_archive_rejects_a_truncated_capture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, _count = fetch_single_game.archive_full_universe(_payload(5), DATE)
    ok, reason = fetch_single_game.verify_archive_is_complete(path, expected_count=5)
    assert (ok, reason) == (True, None)
    ok, reason = fetch_single_game.verify_archive_is_complete(path, expected_count=9)
    assert ok is False
    assert "complete unfiltered universe" in reason


def test_verify_archive_rejects_an_empty_universe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path, _count = fetch_single_game.archive_full_universe({"markets": []}, DATE)
    ok, reason = fetch_single_game.verify_archive_is_complete(path, expected_count=0)
    assert ok is False
    assert "empty" in reason


def test_filtering_is_refused_when_the_archive_check_fails(tmp_path, monkeypatch, capsys):
    """
    End-to-end ordering guard: a capture whose archive cannot be verified
    must abort BEFORE any single-game artifact is produced.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_single_game.mlb_schedule, "fetch_schedule", lambda date, **kw: {"__fake__": True})
    monkeypatch.setattr(fetch_single_game.mlb_schedule, "parse_schedule_games", lambda payload: SINGLE_DAY)
    monkeypatch.setattr(fetch_single_game, "verify_archive_is_complete",
                        lambda path, expected_count: (False, "simulated truncated archive"))

    import scripts.check_kalshi_prices as price_check
    monkeypatch.setattr(price_check, "fetch_live",
                        lambda base, timeout=15: (_payload(10), 200, "http://fake", 1234))

    monkeypatch.setattr(sys, "argv", ["fetch_single_game.py", "--date", DATE, "--game", "MIL"])
    assert fetch_single_game.main() == fetch_single_game.EXIT_ERROR
    assert not os.path.exists(os.path.join(fetch_single_game.SINGLE_GAME_DIR, DATE))
    assert "refusing to filter" in capsys.readouterr().err


# ── end-to-end artifact ─────────────────────────────────────────────────

def _real_universe():
    """Every market from a REAL committed complete-universe snapshot --
    so the single-game extraction is exercised against production data
    shapes, not a hand-built fixture."""
    path = os.path.join(ROOT, "data", "kalshi_registry_snapshots", "kalshi_search_2026-09-17_0817.json")
    if not os.path.exists(path):
        pytest.skip("no committed complete-universe snapshot available in this checkout")
    with open(path) as f:
        return json.load(f)


def test_end_to_end_single_game_artifact_from_a_real_universe(tmp_path, monkeypatch, capsys):
    universe = _real_universe()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_single_game.mlb_schedule, "fetch_schedule", lambda date, **kw: {"__fake__": True})
    monkeypatch.setattr(fetch_single_game.mlb_schedule, "parse_schedule_games",
                        lambda payload: [_sched(823334, MIL, PIT, start="2026-09-17T16:35:00Z", venue="PNC Park")])

    import scripts.check_kalshi_prices as price_check
    monkeypatch.setattr(price_check, "fetch_live",
                        lambda base, timeout=15: (universe, 200, "http://fake", 999))
    monkeypatch.setattr(sys, "argv", ["fetch_single_game.py", "--date", "2026-09-17", "--game", "Brewers at Pirates"])

    assert fetch_single_game.main() == fetch_single_game.EXIT_OK
    capsys.readouterr()

    artifact_path = os.path.join("data", "single_game", "2026-09-17", "823334.json")
    with open(artifact_path) as f:
        artifact = json.load(f)

    assert artifact["matchup"] == "MIL@PIT"
    assert artifact["gamePk"] == 823334
    assert artifact["rawArchive"]["completeUnfilteredArchiveVerified"] is True
    assert artifact["rawArchive"]["filteringHappenedAfterArchive"] is True
    # The archived universe must be strictly larger than the one game.
    assert artifact["rawArchive"]["universeMarketCount"] > artifact["marketSummary"]["total"]
    # Every market family Kalshi lists for this game must be present --
    # not just moneyline/F5.
    families = artifact["marketSummary"]["byFamily"]
    for expected in ("game_result", "inning_result", "team_total", "game_total"):
        assert families.get(expected, 0) > 0, f"{expected} markets missing from the single-game artifact"
    assert all(m["matchup"] == "MIL@PIT" for m in artifact["markets"])
    # No model output is computed here: the artifact reports prices and
    # canonical context, never a probability, edge, or recommendation of
    # its own. (Context copied verbatim out of the canonical slate may
    # legitimately contain model fields -- that block is absent here.)
    model_fields = {"modelProb", "modelProbability", "edge", "recommendation",
                    "fairProbability", "betUpToPrice", "confidence"}
    for market in artifact["markets"]:
        assert model_fields.isdisjoint(market.keys()), f"model output leaked into a market row: {market}"

    # Discoverability: the index and latest pointer must both find it.
    with open(os.path.join("data", "single_game", "2026-09-17", "index.json")) as f:
        index = json.load(f)
    assert [e["gamePk"] for e in index["artifacts"]] == [823334]
    with open(os.path.join("data", "single_game", "latest.json")) as f:
        latest = json.load(f)
    assert latest["path"] == "data/single_game/2026-09-17/823334.json"

    # data/slate.json must NOT have been created or touched.
    assert not os.path.exists(os.path.join("data", "slate.json"))


def test_ambiguous_dispatch_exits_with_the_dedicated_code(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(fetch_single_game.mlb_schedule, "fetch_schedule", lambda date, **kw: {"__fake__": True})
    monkeypatch.setattr(fetch_single_game.mlb_schedule, "parse_schedule_games", lambda payload: DOUBLEHEADER_DAY)
    monkeypatch.setattr(sys, "argv", ["fetch_single_game.py", "--date", DATE, "--game", "NYY@BOS"])

    assert fetch_single_game.main() == fetch_single_game.EXIT_AMBIGUOUS
    err = capsys.readouterr().err
    assert "gamePk=201" in err and "gamePk=202" in err
    assert "--game-pk" in err
    # Nothing at all may be written for a refused selection -- not even
    # an archive fetch was attempted.
    assert not os.path.exists(fetch_single_game.SNAPSHOT_DIR)


def test_slate_context_absence_is_reported_not_fabricated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = fetch_single_game.load_slate_context(DATE, 823334)
    assert context["status"] == "NOT_AVAILABLE"
    assert context["game"] is None
    assert "no canonical slate artifact" in context["reason"]


def test_slate_context_from_a_matching_slate_is_returned_verbatim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("data", exist_ok=True)
    game_block = {"gameId": 823334, "marketLedger": [{"market": "F5_ML_Away"}], "away": {"abbr": "MIL"}}
    with open(os.path.join("data", "slate.json"), "w") as f:
        json.dump({"date": DATE, "games": [{"gameId": 999}, game_block]}, f)

    context = fetch_single_game.load_slate_context(DATE, 823334)
    assert context["status"] == "LOADED"
    assert context["game"] == game_block
    assert context["marketLedgerRows"] == 1


def test_slate_context_from_a_different_date_is_never_used(tmp_path, monkeypatch):
    """A stale slate must not silently supply context for another day."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("data", exist_ok=True)
    with open(os.path.join("data", "slate.json"), "w") as f:
        json.dump({"date": "2026-09-01", "games": [{"gameId": 823334}]}, f)
    context = fetch_single_game.load_slate_context(DATE, 823334)
    assert context["status"] == "NOT_AVAILABLE"


# ── workflow structure ──────────────────────────────────────────────────

WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "fetch-single-game.yml")


def _workflow():
    import yaml
    with open(WORKFLOW_PATH) as f:
        return yaml.safe_load(f)


def _workflow_source():
    with open(WORKFLOW_PATH) as f:
        return f.read()


def test_single_game_workflow_exists_with_the_documented_inputs():
    doc = _workflow()
    assert doc["name"] == "Fetch Single Game"
    # PyYAML parses a bare `on:` key as the boolean True (YAML 1.1).
    inputs = doc.get("on", doc.get(True))["workflow_dispatch"]["inputs"]
    for name in ("date", "game", "game_pk", "source"):
        assert name in inputs, f"input {name!r} is missing"
    assert inputs["date"]["default"] == "", "date must default to today ET inside the script, not to a stale literal"


def test_single_game_workflow_never_writes_a_production_slate_file():
    executable = "\n".join(l for l in _workflow_source().splitlines() if not l.strip().startswith("#"))
    for forbidden in ('"data/slate.json"', '"bets.json"', '"data/bets.json"', "risk_gate", "write_pending_bets"):
        assert forbidden not in executable, f"{forbidden} must never appear in the single-game workflow"


def test_single_game_workflow_commits_the_raw_universe_archive_too():
    """The complete unfiltered capture this run archived is repository
    evidence -- it must be committed alongside the filtered artifact."""
    source = _workflow_source()
    assert '"data/kalshi_registry_snapshots/"' in source
    assert '"data/single_game/"' in source
    assert "scripts/ci/git_data_commit.py" in source


def test_single_game_workflow_surfaces_the_ambiguous_exit_code():
    source = _workflow_source()
    assert "exit_code }}' == '3'" in source or 'exit_code }}" = "3"' in source, \
        "the workflow must tell the operator specifically that the selector was ambiguous"


def test_single_game_workflow_passes_free_text_inputs_through_env_only():
    offenders = []
    for lineno, line in enumerate(_workflow_source().splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or "${{" not in stripped or "inputs." not in stripped:
            continue
        if stripped.startswith("if:") or ": ${{" in stripped:
            continue
        offenders.append(f"line {lineno}: {stripped}")
    assert offenders == [], f"workflow_dispatch inputs interpolated into a run body: {offenders}"
