#!/usr/bin/env python3
"""
tests/test_end_to_end_pipeline_sandbox.py
==============================================
Phase 10 (final architecture phase) end-to-end sandbox verification.

Chains the REAL production scripts, via real subprocess invocation, in
the exact order .github/workflows/fetch-slate.yml runs them:

    enrich_data.py -> build_market_ledger.py -> validate_slate_final.py
    -> protect_slate.py -> risk_gate.py -> write_pending_bets.py

against a hand-authored, synthetic starting fixture -- never real
repository data, never a live network call.

SCOPE BOUNDARY (documented, not a shortfall): the three earliest
Normalized-Slate-layer scripts that precede this chain in the real
workflow -- fetch_savant_pitchers.py, fetch_lineups.py, and
merge_odds.py -- all make live network calls (MLB Stats API, Baseball
Savant, Kalshi) to build their portion of data/slate.json. Invoking
them for real would violate "no production workflow dispatch" and
"no network access during verification"; mocking their network layer
convincingly is a large, separate undertaking with its own risk of
giving false confidence. This test instead starts from a synthetic
slate shaped like data/slate.json immediately BEFORE enrich_data.py
runs (i.e. already has games/pitchers/lineups/odds populated, as if
those three network-dependent scripts had already run) -- covering
every stage from Normalized Slate onward through Execution with real,
unmodified production code, while leaving the raw-fetch stage as an
explicitly out-of-scope boundary.

Verifies:
- The full chain exits 0 at every step against a clean, non-quarantined
  synthetic fixture.
- Every stage's canonical artifact appears with the expected shape
  (normalized_slate.json, projections.json, recommendations.json,
  validation.json, protection.json + authoritative.json,
  execution.json).
- Running the ENTIRE chain a second time (fresh sandbox, identical
  starting fixture) produces byte-identical stage artifacts once
  timestamps are normalized -- proving determinism end-to-end, not just
  per-script.
- Zero mutation of the real repository at any point (hash check).
- No workflow dispatched, no network call made, no bet executed against
  production data -- every artifact lives under a pytest tmp_path.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")

_ISO_TS_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)')
_COMPACT_TS_RE = re.compile(r'\d{8}T\d{6}Z')


def _normalize(text):
    text = _ISO_TS_RE.sub('<TS>', text)
    text = _COMPACT_TS_RE.sub('<COMPACT_TS>', text)
    return text


CHAIN_SCRIPTS = [
    "enrich_data.py",
    "build_market_ledger.py",
    "validate_slate_final.py",
    "protect_slate.py",
    "risk_gate.py",
    "write_pending_bets.py",
]

LIB_FILES = [
    "atomic_json.py", "postponed_guard.py", "sentinel_validator.py",
    "slate_manager.py", "pipeline_artifacts.py", "tracking_type.py",
    "clv_validator.py", "f5_settlement.py", "promotion_engine.py",
    "yrfi_nrfi_validator.py",
]

DATE = "2026-06-16"


def _future_scheduled_start():
    """
    PR #11 hardening review finding: these six scripts are real,
    unmocked production code -- their clock reads are the REAL wall
    clock, not a fixture-injected one. A hardcoded past-dated
    scheduledStartTime (e.g. "2026-06-17") silently trips
    lib.postponed_guard.check_first_pitch_passed()'s Signal-2 timestamp
    fallback regardless of the game's own `status` field, causing
    write_pending_bets.py's pregame gate to block EVERY game -- which
    the original Phase 10 happy-path test never caught, since it only
    asserted marketLedger was non-empty, not that a bet actually got
    written. Computed relative to the real clock at test-run time so it
    stays in the future no matter when this suite runs.
    """
    return (datetime.now(timezone.utc) + timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_synthetic_game():
    return {
        "gameId": "e2e-1",
        "away": {
            "abbr": "KC",
            "pitcher": {"id": "p1", "name": "Away Pitcher",
                        "pitcherSavant": {"xFIP": 3.9, "kPct": 24.0,
                                           "vsLHH": {"pa": 40, "kPct": 0},
                                           "vsRHH": {"pa": 60, "kPct": 0}}},
            "pitcherSavant": {"xFIP": 3.9, "kPct": 24.0},
            "lineup": [{"id": f"a{i}", "name": f"Away Batter {i}"} for i in range(9)],
            "lineupConfirmed": True,
            "bullpen": {},
        },
        "home": {
            "abbr": "WSH",
            "pitcher": {"id": "p2", "name": "Home Pitcher",
                        "pitcherSavant": {"xFIP": 4.3, "kPct": 21.0,
                                           "vsLHH": {"pa": 40, "kPct": 0},
                                           "vsRHH": {"pa": 60, "kPct": 0}}},
            "pitcherSavant": {"xFIP": 4.3, "kPct": 21.0},
            "lineup": [{"id": f"h{i}", "name": f"Home Batter {i}"} for i in range(9)],
            "lineupConfirmed": True,
            "bullpen": {},
        },
        "status": "Scheduled",
        "scheduledStartTime": _future_scheduled_start(),
        "park": {"parkFactor": 100},
        "pinnacleVF": {"away": 56.0, "home": 44.0},
        "awayTeamStats": {"lineupConfirmed": True, "lineupConfirmedOfficial": True},
        "homeTeamStats": {"lineupConfirmed": True, "lineupConfirmedOfficial": True},
        # odds.kalshi.ml is the field build_market_ledger.py's ML_Away/
        # ML_Home branch actually reads (confirmed by reading
        # scripts/build_market_ledger.py directly: `kalshi.get('ml', {})`
        # where `kalshi = (g.get('odds') or {}).get('kalshi') or {}`) --
        # a real, non-obvious finding from this hardening review: an
        # earlier top-level `markets` field (invented, not the real
        # schema) silently produced zero real-money bets, since
        # evaluate_game() never reads it at all. Near-pick'em pricing
        # (-110/-110) combined with the KC/WSH offense gap already
        # present in teamstats.json produces enough model-vs-market
        # divergence to clear the MEDIUM confidence threshold.
        "odds": {"kalshi": {"ml": {"away": -110, "home": -110}}},
        "marketLedger": [],
    }


def _make_synthetic_slate():
    return {"date": DATE, "games": [_make_synthetic_game()]}


def _make_teamstats():
    return {
        "teams": {
            "KC": {"record": {"runsScored": 480, "wins": 40, "losses": 35}},
            "WSH": {"record": {"runsScored": 430, "wins": 35, "losses": 40}},
        }
    }


def _sandbox(base_dir, slate=None):
    """Copy scripts/ + lib/ into base_dir and write the synthetic fixtures."""
    scripts_dir = base_dir / "scripts"
    lib_dir = base_dir / "lib"
    data_dir = base_dir / "data"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    for name in CHAIN_SCRIPTS:
        shutil.copy(os.path.join(SCRIPTS_DIR, name), scripts_dir / name)
    for name in LIB_FILES:
        src = os.path.join(LIB_DIR, name)
        if os.path.exists(src):
            shutil.copy(src, lib_dir / name)
    (lib_dir / "__init__.py").write_text("")

    with open(data_dir / "slate.json", "w") as f:
        json.dump(slate if slate is not None else _make_synthetic_slate(), f)
    with open(data_dir / "teamstats.json", "w") as f:
        json.dump(_make_teamstats(), f)
    with open(data_dir / "bullpen.json", "w") as f:
        json.dump({"bullpens": {}}, f)

    return scripts_dir, data_dir


def _run_chain(base_dir, slate=None, stop_on_failure=True):
    """
    Runs the full chain via subprocess, cwd=base_dir (matching every
    real workflow step's convention: no working-directory: override
    exists anywhere in .github/workflows/fetch-slate.yml). Returns a
    dict of {script_name: subprocess.CompletedProcess}.

    `slate`, if given, overrides the default synthetic fixture --
    lets adversarial scenarios (excluded game, live game, sentinel
    quarantine, validation failure) drive the SAME real chain with a
    different starting slate.json, without duplicating the sandbox
    plumbing.

    `stop_on_failure` mirrors the real workflow's `if: steps.X.outcome
    == 'success'` gating (confirmed via .github/workflows/fetch-slate.yml:
    validate_slate_final.py has no continue-on-error, so a real failure
    there stops the job before protect_slate.py ever runs). Set to
    False only when a test explicitly wants to observe what a later
    script does when run directly against an already-written
    upstream-failure state (not what the real workflow would do).
    """
    scripts_dir, data_dir = _sandbox(base_dir, slate=slate)
    env = dict(os.environ)
    results = {}
    for name in CHAIN_SCRIPTS:
        args = [sys.executable, str(scripts_dir / name)]
        if name in ("validate_slate_final.py", "protect_slate.py"):
            args.append(DATE)
        result = subprocess.run(
            args, cwd=str(base_dir), capture_output=True, text=True, env=env,
        )
        results[name] = result
        if stop_on_failure and result.returncode != 0:
            # Stop the chain early on a real failure -- matches the real
            # workflow's `if: steps.X.outcome == 'success'` gating, and
            # avoids masking a failure's true origin with cascading
            # failures from missing upstream output.
            break
    return results, data_dir


class TestEndToEndPipelineSandbox:

    def test_full_chain_completes_successfully(self, tmp_path):
        results, data_dir = _run_chain(tmp_path / "run1")
        failures = {name: r for name, r in results.items() if r.returncode != 0}
        assert not failures, "\n".join(
            f"{name}: exit={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
            for name, r in failures.items()
        )
        assert set(results.keys()) == set(CHAIN_SCRIPTS), (
            f"chain did not reach the end: only ran {list(results.keys())}"
        )

    def test_every_stage_artifact_present(self, tmp_path):
        results, data_dir = _run_chain(tmp_path / "run1")
        assert all(r.returncode == 0 for r in results.values())

        pipeline_dir = data_dir / "pipeline" / DATE
        for artifact in ("normalized_slate.json", "projections.json",
                          "recommendations.json", "validation.json",
                          "protection.json", "execution.json"):
            path = pipeline_dir / artifact
            assert path.exists(), f"missing pipeline artifact: {artifact}"
            envelope = json.loads(path.read_text())
            assert "meta" in envelope and "data" in envelope
            assert envelope["meta"]["stage"] == artifact.replace(".json", "")

        assert (data_dir / "slates" / DATE / "authoritative.json").exists()

    def test_recommendation_layer_produced_a_real_decision(self, tmp_path):
        """
        Not asserting every market's outcome (that would overfit this
        synthetic fixture to today's evaluate_game() full rule set) --
        but DOES assert the ML_Away market specifically reaches
        "Accepted" with a real-money tier, since the fixture was
        deliberately constructed (odds.kalshi.ml, lineupConfirmedOfficial,
        a real offense-gap in teamstats.json) to produce exactly that
        outcome. A weaker "marketLedger is non-empty" check would have
        (and, in the original Phase 10 version of this test, DID) pass
        even when write_pending_bets.py never actually wrote a real bet
        -- see test_accepted_bet_reaches_bets_json_end_to_end below for
        the full-chain proof this gap is now closed.
        """
        results, data_dir = _run_chain(tmp_path / "run1")
        assert all(r.returncode == 0 for r in results.values())
        slate = json.loads((data_dir / "slate.json").read_text())
        market_ledger = slate["games"][0].get("marketLedger", [])
        assert len(market_ledger) > 0, "evaluate_game() produced zero marketLedger rows"
        ml_away = next(row for row in market_ledger if row.get("market") == "ML_Away")
        assert ml_away["status"] == "Accepted"
        assert ml_away["confidenceTier"] in ("HIGH", "MEDIUM")

    def test_accepted_bet_reaches_bets_json_end_to_end(self, tmp_path):
        """
        PR #11 hardening review, Part 13's explicit "accepted bet"
        adversarial fixture: closes a real gap the original Phase 10
        end-to-end suite had -- it never actually verified a real,
        non-empty bets.json entry resulted from the full chain (only
        that marketLedger was non-empty, which is satisfied even by
        "Missing Data" rows). Independently found during this review:
        the ORIGINAL fixture used an invented top-level `markets` field
        that evaluate_game() never reads at all (the real schema is
        `game['odds']['kalshi']['ml']['away'/'home']`) -- so every prior
        Phase 10 sandbox run silently produced zero real-money bets
        without any test catching it.
        """
        results, data_dir = _run_chain(tmp_path / "run1")
        assert all(r.returncode == 0 for r in results.values())
        bets_path = data_dir.parent / "bets.json"
        assert bets_path.exists(), "expected a real Accepted bet to reach bets.json"
        bets = json.loads(bets_path.read_text())
        assert len(bets) == 1
        assert bets[0]["market"] == "ML_Away"
        assert bets[0]["confidenceTier"] == "MEDIUM"
        assert bets[0]["status"] == "pending"
        assert bets[0]["realMoneyBlocked"] is False

    def test_deterministic_across_two_independent_runs(self, tmp_path):
        run1_dir = tmp_path / "run1"
        run2_dir = tmp_path / "run2"
        results1, data_dir1 = _run_chain(run1_dir)
        results2, data_dir2 = _run_chain(run2_dir)
        assert all(r.returncode == 0 for r in results1.values())
        assert all(r.returncode == 0 for r in results2.values())

        pipeline1 = data_dir1 / "pipeline" / DATE
        pipeline2 = data_dir2 / "pipeline" / DATE
        for artifact in ("normalized_slate.json", "projections.json",
                          "recommendations.json", "validation.json",
                          "protection.json", "execution.json"):
            content1 = _normalize(json.dumps(json.loads((pipeline1 / artifact).read_text()), sort_keys=True))
            content2 = _normalize(json.dumps(json.loads((pipeline2 / artifact).read_text()), sort_keys=True))
            # savedPaths (protection.json) embeds the sandbox root path
            # itself (run1/ vs run2/) -- expected sandboxing noise, same
            # class of normalization every prior phase's differential
            # harness needed, not a real behavioral difference.
            content1 = content1.replace(str(run1_dir), '<SANDBOX_ROOT>')
            content2 = content2.replace(str(run2_dir), '<SANDBOX_ROOT>')
            assert content1 == content2, f"{artifact} differs between two independent runs of the same fixture"

    def test_no_real_repository_mutation(self, tmp_path):
        prod_paths = []
        for rel in ("data", "lib", "scripts", "config", "bets.json", "BET_LOG.md", "RULES.md"):
            full = os.path.join(ROOT, rel)
            if os.path.isfile(full):
                prod_paths.append(full)
            elif os.path.isdir(full):
                for dirpath, _, filenames in os.walk(full):
                    for fn in filenames:
                        prod_paths.append(os.path.join(dirpath, fn))

        def _hash_all():
            import hashlib
            h = hashlib.sha256()
            for p in sorted(prod_paths):
                try:
                    with open(p, "rb") as f:
                        h.update(f.read())
                except OSError:
                    pass
            return h.hexdigest()

        before = _hash_all()
        _run_chain(tmp_path / "run1")
        after = _hash_all()
        assert before == after, "end-to-end sandbox run mutated real repository files"

    def test_no_network_modules_imported_by_chain_scripts(self):
        """
        Confirms none of the six chained scripts import a network
        library at module scope -- this sandbox run makes zero live
        network calls by construction, not by luck.
        """
        import ast
        for name in CHAIN_SCRIPTS:
            path = os.path.join(SCRIPTS_DIR, name)
            with open(path) as f:
                tree = ast.parse(f.read(), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name not in ("requests", "urllib3", "http.client", "httpx"), (
                            f"{name} imports network library {alias.name!r} at module scope"
                        )
                elif isinstance(node, ast.ImportFrom):
                    assert node.module not in ("requests", "urllib3", "httpx"), (
                        f"{name} imports from network library {node.module!r} at module scope"
                    )


class TestEndToEndAdversarialScenarios:
    """
    PR #11 hardening review, Part 13: the original Phase 10 sandbox
    only exercised the clean, happy-path fixture end-to-end. These
    scenarios drive the SAME real six-script chain through the
    adversarial fixtures the review explicitly requires, closing a
    real coverage gap (not a Phase 10 regression -- the happy-path
    chain itself was correct; it just never exercised these branches).
    """

    def _variant_slate(self, **game_overrides):
        import copy
        slate = _make_synthetic_slate()
        slate["games"][0].update(game_overrides)
        return slate

    def test_excluded_game_produces_zero_bets_full_chain(self, tmp_path):
        slate = self._variant_slate(excludedFromSlate=True)
        results, data_dir = _run_chain(tmp_path / "run1", slate=slate)
        assert all(r.returncode == 0 for r in results.values()), results
        assert not (data_dir.parent / "bets.json").exists(), (
            "an excludedFromSlate game must never produce a bets.json entry"
        )

    def test_live_game_produces_zero_bets_full_chain(self, tmp_path):
        """
        An "In Progress" game reaches write_pending_bets.py's own
        pregame-only hard gate (check_game_status -> liveGameBlocked),
        which must block it there even though validate_slate_final.py
        only WARNS (not errors) on an in-progress game's missing
        pinnacleVF, and protect_slate.py has no live-game logic of its
        own at all.
        """
        slate = self._variant_slate(status="In Progress")
        results, data_dir = _run_chain(tmp_path / "run1", slate=slate)
        assert all(r.returncode == 0 for r in results.values()), {
            k: (v.returncode, v.stdout, v.stderr) for k, v in results.items()
        }
        assert not (data_dir.parent / "bets.json").exists(), (
            "an In Progress game must be blocked by write_pending_bets.py's own pregame gate"
        )
        assert "PREGAME GATE BLOCKED" in results["write_pending_bets.py"].stdout

    def test_validation_failure_stops_chain_before_protect_slate(self, tmp_path):
        """
        Removing pinnacleVF reproduces the exact real validation error
        found while building the original Phase 10 fixture
        ("pinnacleVF.away missing -- Rule 71 gap check impossible").
        Confirms the chain stops at validate_slate_final.py exactly as
        the real workflow would (no continue-on-error on that step),
        and that protect_slate.py/risk_gate.py/write_pending_bets.py
        never ran at all -- not that they ran and happened to no-op.
        """
        slate = self._variant_slate()
        del slate["games"][0]["pinnacleVF"]
        results, data_dir = _run_chain(tmp_path / "run1", slate=slate)
        assert results["validate_slate_final.py"].returncode == 1
        assert "pinnacleVF.away missing" in results["validate_slate_final.py"].stdout
        ran = set(results.keys())
        assert ran == {"enrich_data.py", "build_market_ledger.py", "validate_slate_final.py"}, (
            f"chain should have stopped after validate_slate_final.py failed, but ran: {ran}"
        )
        assert not (data_dir / "slates" / DATE / "authoritative.json").exists(), (
            "protect_slate.py must never have run"
        )
        assert not (data_dir.parent / "bets.json").exists(), "write_pending_bets.py must never have run"

    def test_sentinel_quarantine_does_not_block_downstream_execution_chain(self, tmp_path):
        """
        Real, non-obvious finding independently verified here: a
        sentinel-price quarantine in protect_slate.py ONLY skips the
        authoritative.json<->data/slate.json backwards-compat sync step
        -- it does NOT touch data/slate.json's own marketLedger content
        (already written earlier by build_market_ledger.py), and
        neither risk_gate.py nor write_pending_bets.py read
        authoritative.json at all. So a quarantined protection run does
        NOT block the Execution Layer from still processing whatever
        data/slate.json already contains. This is legacy behavior
        (unrelated to Phase 10, which never touched protect_slate.py or
        risk_gate.py), documented here as a real property of the
        pipeline this review is required to verify, not assume.
        """
        slate = self._variant_slate()
        # Sentinel hard-reject values per lib/sentinel_validator.py:
        # 19900, -19900, 100000, -100000.
        slate["games"][0]["odds"]["kalshi"]["ml"]["away"] = 19900
        results, data_dir = _run_chain(tmp_path / "run1", slate=slate)
        assert all(r.returncode == 0 for r in results.values()), {
            k: (v.returncode, v.stdout, v.stderr) for k, v in results.items()
        }
        assert "REJECTED_CONTAMINATED" in results["protect_slate.py"].stdout
        assert not (data_dir / "slates" / DATE / "authoritative.json").exists(), (
            "a quarantined run must not write authoritative.json"
        )
        # risk_gate.py and write_pending_bets.py still ran successfully
        # against data/slate.json's own (unquarantined-at-that-layer)
        # content -- this is the real, verified finding, not an
        # assumption.
        assert results["risk_gate.py"].returncode == 0
        assert results["write_pending_bets.py"].returncode == 0

    def test_duplicate_rerun_of_full_chain_does_not_duplicate_bets(self, tmp_path):
        """
        Running the ENTIRE chain twice against the identical starting
        slate.json (same sandbox, not a fresh one) must not duplicate
        bets.json entries on the second pass -- proving end-to-end
        idempotency, not just write_pending_bets.py's own unit-level
        idempotency already covered elsewhere.
        """
        base = tmp_path / "run1"
        results1, data_dir = _run_chain(base, slate=self._variant_slate())
        assert all(r.returncode == 0 for r in results1.values())
        # bets.json lives at the sandbox ROOT (BETS_PATH =
        # os.path.join(ROOT, 'bets.json')), a sibling of data/, not
        # inside it.
        bets_after_first = json.loads((base / "bets.json").read_text())
        assert len(bets_after_first) >= 1

        # Second pass: re-run the exact same six scripts, cwd unchanged,
        # against the state the first pass left behind (data/slate.json
        # is now whatever protect_slate.py/risk_gate.py already wrote).
        scripts_dir = base / "scripts"
        env = dict(os.environ)
        for name in CHAIN_SCRIPTS:
            args = [sys.executable, str(scripts_dir / name)]
            if name in ("validate_slate_final.py", "protect_slate.py"):
                args.append(DATE)
            result = subprocess.run(args, cwd=str(base), capture_output=True, text=True, env=env)
            assert result.returncode == 0, f"{name} failed on rerun: {result.stdout}\n{result.stderr}"

        bets_after_second = json.loads((base / "bets.json").read_text())
        assert len(bets_after_second) == len(bets_after_first), (
            "rerunning the full chain against unchanged inputs must not duplicate bets.json entries"
        )
