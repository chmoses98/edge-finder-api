#!/usr/bin/env python3
"""
tests/test_research_export.py
=============================
The MLB research-graph adapter (scripts/research_export.py) on the REAL committed corpus: the v1
export (scripts/app_export.py) publishes into tmp_path first, then the explorer is built beside it.
To stay well under 60 s the loaders are bounded (a handful of slate dates, one research-cache
season, one Statcast week); the production run reads everything (see docs/APP_EXPORT.md).
Everything is written under tmp_path only.
"""
import importlib.util
import json
import os
import shutil
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "contract")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from edge_finder_contract import CAPABILITIES, ids, packet, publish, research as R  # noqa: E402


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "scripts", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


app_export = _load("app_export")
research_export = _load("research_export")

DATA = os.path.join(ROOT, "data")
NOW = "2026-10-02T15:00:00Z"          # v1 exports the 2026-10-01 slate (PHI @ ATL)
EMPTY_NOW = "2026-10-03T15:00:00Z"    # 2026-10-03: markets but no slate games (postseason not captured)
BOUNDS = {"slate_range": ("2026-09-28", "2026-10-01"), "seasons": ["2026"], "statcast_range": ("2026-09-20", "2026-09-27")}
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "app-export.yml")

# The audit's capability matrix (scratchpad/phase2/audit_mlb.md sections 4 and 10) for a slate day.
EXPECTED_CAPABILITIES = {
    "team_profiles": "VERIFIED", "player_profiles": "PARTIAL", "event_research": "VERIFIED", "team_metrics": "VERIFIED",
    "player_metrics": "PARTIAL", "team_game_logs": "VERIFIED", "player_game_logs": "PARTIAL", "historical_results": "VERIFIED",
    "opponents": "VERIFIED", "opponent_adjustment": "VERIFIED", "schedule_strength": "UNAVAILABLE", "recent_form_windows": "VERIFIED",
    "usage": "PARTIAL", "lineups": "PARTIAL", "injuries": "UNAVAILABLE", "matchup_metrics": "PARTIAL",
    "projection_distributions": "RESEARCH", "raw_projections": "VERIFIED", "market_prices": "VERIFIED",
    "market_price_history": "PARTIAL", "advanced_stats": "PARTIAL", "situational_splits": "PARTIAL", "player_props": "RESEARCH",
    "team_props": "VERIFIED", "game_markets": "VERIFIED", "play_by_play": "PARTIAL", "weather": "UNAVAILABLE",
    "venue_effects": "VERIFIED", "calibration": "VERIFIED", "historical_accuracy": "VERIFIED", "clv": "VERIFIED",
    "wager_history": "VERIFIED", "rankings": "VERIFIED", "time_series": "VERIFIED", "comparisons": "VERIFIED", "search": "VERIFIED",
}
BUDGETS = {"teams": 150_000, "players": 150_000, "events": 150_000, "market_history": 400_000}


def _real_data_present():
    need = [os.path.join(DATA, "edgelab", "bets", "bets.jsonl"), os.path.join(DATA, "slates", "2026-10-01", "authoritative.json"),
            os.path.join(DATA, "research_cache", "bullpen_backtest", "2026", "schedules"), os.path.join(DATA, "statcast_raw", "index")]
    return all(os.path.exists(p) for p in need)


pytestmark = pytest.mark.skipif(not _real_data_present(), reason="committed data corpus not present in this checkout")


def _read(root, rel):
    with open(os.path.join(str(root), rel), encoding="utf-8") as fh:
        return json.load(fh)


def _v1(root, now):
    assert app_export.main(["--out", str(root), "--data-root", DATA, "--now", now]) == 0
    return root


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    root = _v1(tmp_path_factory.mktemp("app") / "latest", NOW)
    inputs = research_export.load_inputs(DATA, str(root), **BOUNDS)
    index = research_export.export_explorer(str(root), data_root=DATA, inputs=inputs)
    return root, inputs, index


# 1. the tree verifies and describes the same publication as the v1 manifest
def test_explorer_verifies_and_shares_the_v1_run(published):
    root, _inputs, index = published
    assert R.verify_explorer(root) == []
    assert publish.verify_published(root) == []
    manifest = _read(root, "manifest.json")
    assert index["run_id"] == manifest["run_id"] == index["base_manifest_run_id"]
    assert index["generated_at"] == manifest["generated_at"]
    assert index["counts"]["teams"] == 30 and index["counts"]["events"] >= 1 and index["counts"]["players"] >= 2
    assert index["counts"]["rankings"] >= 10 and index["counts"]["series"] >= 60 and index["counts"]["market_history"] >= 1


# 2. determinism: a fresh load + build + publish is byte-identical
def test_two_publishes_are_byte_identical(published, tmp_path):
    root, _inputs, _index = published
    other = tmp_path / "latest"
    shutil.copytree(root, other, ignore=shutil.ignore_patterns("explorer"))
    research_export.export_explorer(str(other), data_root=DATA, inputs=research_export.load_inputs(DATA, str(other), **BOUNDS))
    assert R.digest_tree(other) == R.digest_tree(root)


# 3. identity: every v1 event has event research, every v1 participant a profile, same ids
def test_every_v1_event_and_participant_is_covered_with_v1_ids(published):
    root, inputs, index = published
    events = _read(root, "events.json")["items"]
    files = index["files"]
    for ev in events:
        assert f"events/{ev['event_id']}.json" in files
        er = _read(root, f"explorer/events/{ev['event_id']}.json")
        assert er["event"] == ev
        for p in ev["participants"]:
            assert f"teams/{p['participant_id']}.json" in files
            abbr = p["source_ids"]["mlb_team_abbr"]
            assert p["participant_id"] == app_export._team(abbr)["participant_id"]
        for pl in er["players"]:
            prof = _read(root, pl["path"])
            mlbam = prof["entity"]["source_ids"]["mlbam_player_id"]
            assert pl["participant_id"] == ids.participant_id("MLB", "PLAYER", "mlbam_player_id", mlbam)
        assert er["markets"] and all(m["event_id"] == ev["event_id"] for m in er["markets"])
        v1_prices = {mp["model_price_id"] for mp in _read(root, "model_prices.json")["items"] if mp.get("event_id") == ev["event_id"]}
        assert {p["model_price_id"] for p in er["projections"]} == v1_prices
    # the ARI->AZ / OAK->ATH normalisation: research-cache ids are the v1 slate ids
    teams = {t["participant_id"] for t in index["teams"]}
    assert app_export._team("AZ")["participant_id"] in teams and app_export._team("ATH")["participant_id"] in teams
    assert research_export.team_pid("ARI") == app_export._team("AZ")["participant_id"]
    assert research_export.team_pid("OAK") == app_export._team("ATH")["participant_id"]
    # research-cache game references use the v1 event id scheme
    prof = _read(root, index["teams"][0]["path"])
    g = prof["games"][0]
    assert g["event_id"].startswith("evt_") and g["result"]["outcome"] in ("W", "L", "T")


# 4. capability statuses equal the audit's matrix
def test_capability_statuses_match_the_audit(published):
    root, _inputs, _index = published
    caps = _read(root, "explorer/capabilities.json")
    got = {c["capability"]: c["status"] for c in caps["items"]}
    assert set(got) == set(CAPABILITIES)
    assert got == EXPECTED_CAPABILITIES
    assert caps["audit_date"] == "2026-10-03"
    registered = {m["metric_id"] for m in _read(root, "explorer/metrics.json")["items"]}
    for c in caps["items"]:
        assert set(c["metrics"]) <= registered, c["capability"]
    for c in caps["items"]:
        if c["status"] in ("PARTIAL", "RESEARCH"):
            assert c["limitations"], c["capability"]
        if c["status"] == "UNAVAILABLE":
            assert c["reasons"] and not c["evidence"], c["capability"]


# 5. the handicap packet for the first event
def test_game_packet_validates_with_markets_and_both_participants(published):
    root, _inputs, _index = published
    events = _read(root, "events.json")["items"]
    ev = events[0]
    pk = packet.build(app_root=root, scope_kind="GAME", event_id=ev["event_id"])
    assert pk["markets"] and all(m["event_id"] == ev["event_id"] for m in pk["markets"])
    evidence_ids = {e["entity_id"] for e in pk["evidence"]}
    assert {p["participant_id"] for p in ev["participants"]} <= evidence_ids
    team_ev = [e for e in pk["evidence"] if e["entity_type"] == "TEAM"]
    assert all(e["observations"] for e in team_ev)
    assert pk["quality"]["missing"] == []
    assert pk["quality"]["capabilities"]["team_profiles"] == "VERIFIED"
    assert packet.render_text(pk) == packet.render_text(packet.build(app_root=root, scope_kind="GAME", event_id=ev["event_id"]))


# 6. no secret-shaped strings
def test_no_secret_shaped_strings(published):
    root, _inputs, _index = published
    assert R.no_secret_shaped_strings(root) == []


# 7. lower-confidence statuses survive into profiles, events, series and the packet
def test_research_and_partial_statuses_are_preserved(published):
    root, _inputs, index = published
    events = _read(root, "events.json")["items"]
    er = _read(root, f"explorer/events/{events[0]['event_id']}.json")
    research_proj = [p for p in er["projections"] if p["quality_status"] == "RESEARCH"]
    assert research_proj, "the RESEARCH_ONLY model prices of the slate must stay RESEARCH"
    prices = {mp["model_price_id"]: mp for mp in _read(root, "model_prices.json")["items"]}
    for p in er["projections"]:
        tier = prices[p["model_price_id"]]["support_status"]
        assert (p["quality_status"] == "RESEARCH") == (tier not in ("TRUSTED_PRODUCTION", "MARKET_LEDGER"))
    # the same RESEARCH projection refs inside the team profile
    for part in er["participants"]:
        prof = _read(root, part["path"])
        for p in prof["projections"]:
            assert p["quality_status"] == {x["model_price_id"]: x["quality_status"] for x in er["projections"]}[p["model_price_id"]]
    # RESEARCH points inside model-probability series
    series = [_read(root, ln["path"]) for ln in er["links"] if ln["rel"] == "SERIES"]
    for s in series:
        for pt in s["points"]:
            assert pt["quality_status"] in ("VERIFIED", "RESEARCH")
    # packet: research-only model evidence is flagged, and every observation keeps its profile status
    pk = packet.build(app_root=root, scope_kind="GAME", event_id=events[0]["event_id"])
    flagged = set(pk["quality"]["research_only_items"])
    for p in research_proj:
        if p["research_only"]:
            assert p["market_id"] in flagged
    for e in pk["evidence"]:
        prof_path = f"explorer/teams/{e['entity_id']}.json" if e["entity_type"] == "TEAM" else f"explorer/players/{e['entity_id']}.json"
        prof = _read(root, prof_path)
        statuses = {(o["metric_id"], o["window"]["label"]): o["quality_status"] for o in prof["metrics"]}
        for o in e["observations"]:
            if (o["metric_id"], o["window"]) in statuses and o["split"] is None:
                assert o["quality_status"] == statuses[(o["metric_id"], o["window"])]
        if e["entity_type"] == "PLAYER":
            assert all(o["quality_status"] == "PARTIAL" for o in e["observations"])


# 8. a failing publish leaves the previous tree intact
def test_a_failing_publish_leaves_the_previous_tree_intact(published):
    root, inputs, index = published
    before = R.digest_tree(root)
    docs = research_export.build_explorer(inputs, run_id=index["run_id"], generated_at=index["generated_at"])
    broken = [d for d in docs if d["kind"] != "metric_registry"]
    with pytest.raises(R.ExplorerError):
        R.publish_explorer(app_root=root, sport="MLB", run_id=index["run_id"], generated_at=index["generated_at"], documents=broken,
                           quality=index["quality"])
    assert R.digest_tree(root) == before
    assert R.verify_explorer(root) == []
    assert not [p for p in os.listdir(root) if p.startswith(".explorer-staging-")]


# 9. budgets and labels
def test_document_budgets_and_labels(published):
    root, _inputs, _index = published
    ex = os.path.join(str(root), "explorer")
    for sub, limit in BUDGETS.items():
        for name in os.listdir(os.path.join(ex, sub)):
            assert os.path.getsize(os.path.join(ex, sub, name)) <= limit, (sub, name)
    assert os.path.getsize(os.path.join(ex, "search_index.json")) <= 300_000
    assert os.path.getsize(os.path.join(ex, "index.json")) <= 300_000
    caps = {c["capability"]: c for c in _read(root, "explorer/capabilities.json")["items"]}
    assert any("snapshot series, median 3 points" in x for x in caps["market_price_history"]["limitations"])
    assert any("analysis-only" in x for x in caps["recent_form_windows"]["limitations"])
    reg = {m["metric_id"]: m for m in _read(root, "explorer/metrics.json")["items"]}
    assert any("2026-08-11" in x for x in reg["met_mlb.sc_batter_xwoba"]["known_limitations"]) if "met_mlb.sc_batter_xwoba" in reg else True
    for m in reg.values():
        assert m["description"] and m["methodology_version"] == research_export.RESEARCH_EXPORT_VERSION
        if m["supports"]["rank"]:
            assert any(r["metric_id"] == m["metric_id"] for r in _rankings(root))
    # REAL-only wager aggregates in the registry match the v1 wagers file
    wagers = _read(root, "wagers.json")["items"]
    assert reg["met_mlb.wager_win_rate"]["extensions"]["overall"]["wagers"] == len(wagers)
    assert all(b["sampleTier"] for b in reg["met_mlb.model_calibration_error"]["extensions"]["bins"])
    # no batting order: event players are not in slate order
    events = _read(root, "events.json")["items"]
    er = _read(root, f"explorer/events/{events[0]['event_id']}.json")
    assert all(not lu["batting_order_published"] for lu in er["context"]["lineups"])
    assert er["context"]["weather"] is None and er["context"]["injuries"] == []


def _rankings(root):
    folder = os.path.join(str(root), "explorer", "rankings")
    for name in sorted(os.listdir(folder)):
        yield _read(root, f"explorer/rankings/{name}")


# 10. an empty slate day still publishes teams, capabilities, metrics and search
def test_empty_slate_day_publishes_teams_capabilities_metrics_search(tmp_path):
    root = _v1(tmp_path / "latest", EMPTY_NOW)
    assert _read(root, "events.json")["items"] == []
    research_export.export_explorer(str(root), data_root=DATA, **BOUNDS)
    assert R.verify_explorer(root) == []
    index = R.read_index(root)
    assert index["counts"]["teams"] == 30 and index["counts"]["events"] == 0 and index["counts"]["metrics"] > 0
    assert index["warnings"] and "no v1 events" in index["warnings"][0]
    caps = {c["capability"]: c["status"] for c in _read(root, "explorer/capabilities.json")["items"]}
    assert caps["team_profiles"] == "VERIFIED" and caps["search"] == "VERIFIED" and caps["rankings"] == "VERIFIED"
    for name in ("event_research", "market_price_history", "lineups", "matchup_metrics", "raw_projections"):
        assert caps[name] == "UNAVAILABLE", name
    assert _read(root, "explorer/search_index.json")["count"] >= 30


# 11. workflow wiring: the explorer runs right after the v1 export, on its own, and fails the job distinctly
def test_workflow_runs_the_explorer_after_the_v1_export():
    with open(WORKFLOW, encoding="utf-8") as fh:
        source = fh.read()
    steps = yaml.safe_load(source)["jobs"]["export"]["steps"]
    order = [s.get("id") or s["name"] for s in steps]
    assert order.index("research_export") == order.index("export") + 1
    step = steps[order.index("research_export")]
    assert step["continue-on-error"] is True and "steps.export.outcome == 'success'" in step["if"]
    assert "scripts/research_export.py --out app/latest" in step["run"] and "GITHUB_STEP_SUMMARY" in step["run"]
    assert "--min-interval-minutes 180" in step["run"]
    commit = steps[order.index("research_export") + 1]
    assert '"app/latest/"' in commit["run"] and commit["if"] == "always()"
    assert "steps.research_export.outcome" in steps[-1]["run"]


# 12. refresh gate (research.refresh_due): a second export within the interval is skipped and leaves the tree
#     byte-identical; a changed v1 event set rebuilds at once; an elapsed interval is due again
_CLI_BOUNDS = ["--slate-start", BOUNDS["slate_range"][0], "--slate-end", BOUNDS["slate_range"][1], "--seasons", ",".join(BOUNDS["seasons"]),
               "--statcast-start", BOUNDS["statcast_range"][0], "--statcast-end", BOUNDS["statcast_range"][1]]


def _copy(root, tmp_path):
    other = tmp_path / "latest"
    shutil.copytree(root, other)
    return other


def test_a_second_export_within_the_interval_is_skipped(published, tmp_path, capsys):
    root, _inputs, _index = published
    other = _copy(root, tmp_path)
    before = R.digest_tree(other)
    assert research_export.main(["--out", str(other), "--data-root", DATA, "--min-interval-minutes", "180", *_CLI_BOUNDS]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["skipped"] is True and "unchanged" in out["reason"]
    assert R.digest_tree(other) == before
    manifest = _read(other, "manifest.json")
    later = app_export.timeutil.plus(manifest["generated_at"], 3 * 3600 + 1)
    due, reason = research_export.refresh_decision(str(other), now=later, min_interval_minutes=180)
    assert due and "refresh every 10800 s" in reason


def test_a_changed_v1_event_set_triggers_a_rebuild(published, tmp_path, capsys):
    root, _inputs, _index = published
    other = _copy(root, tmp_path)
    before = R.digest_tree(other)
    events = _read(other, "events.json")
    dropped = events["items"][0]["event_id"]
    events["items"] = events["items"][1:]
    with open(os.path.join(str(other), "events.json"), "w", encoding="utf-8") as fh:
        json.dump(events, fh)
    due, reason = research_export.refresh_decision(str(other), min_interval_minutes=180)
    assert due and "v1 events changed" in reason
    assert research_export.main(["--out", str(other), "--data-root", DATA, "--min-interval-minutes", "180", *_CLI_BOUNDS]) == 0
    assert "skipped" not in capsys.readouterr().out
    assert R.digest_tree(other) != before
    assert R.verify_explorer(other) == []
    index = R.read_index(other)
    assert dropped not in {e["event_id"] for e in index["events"]}
    assert not os.path.exists(os.path.join(str(other), "explorer", "events", f"{dropped}.json"))
