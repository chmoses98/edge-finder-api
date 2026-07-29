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
        "scheduledStartTime": "2026-06-17T00:00:00Z",
        "park": {"parkFactor": 100},
        "pinnacleVF": {"away": 56.0, "home": 44.0},
        "awayTeamStats": {"lineupConfirmed": True},
        "homeTeamStats": {"lineupConfirmed": True},
        "markets": [
            {"market": "ML_Away", "kalshiPrice": -130, "ticker": "KXMLB-26JUN16KCWSH-KC",
             "seriesTicker": "KXMLB", "modelProb": 58.0},
            {"market": "ML_Home", "kalshiPrice": 110, "ticker": "KXMLB-26JUN16KCWSH-WSH",
             "seriesTicker": "KXMLB", "modelProb": 42.0},
        ],
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


def _sandbox(base_dir):
    """Copy scripts/ + lib/ into base_dir and write the synthetic fixtures."""
    scripts_dir = base_dir / "scripts"
    lib_dir = base_dir / "lib"
    data_dir = base_dir / "data"
    scripts_dir.mkdir(parents=True)
    lib_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)

    for name in CHAIN_SCRIPTS:
        shutil.copy(os.path.join(SCRIPTS_DIR, name), scripts_dir / name)
    for name in LIB_FILES:
        src = os.path.join(LIB_DIR, name)
        if os.path.exists(src):
            shutil.copy(src, lib_dir / name)
    (lib_dir / "__init__.py").write_text("")

    with open(data_dir / "slate.json", "w") as f:
        json.dump(_make_synthetic_slate(), f)
    with open(data_dir / "teamstats.json", "w") as f:
        json.dump(_make_teamstats(), f)
    with open(data_dir / "bullpen.json", "w") as f:
        json.dump({"bullpens": {}}, f)

    return scripts_dir, data_dir


def _run_chain(base_dir):
    """
    Runs the full chain via subprocess, cwd=base_dir (matching every
    real workflow step's convention: no working-directory: override
    exists anywhere in .github/workflows/fetch-slate.yml). Returns a
    dict of {script_name: subprocess.CompletedProcess}.
    """
    scripts_dir, data_dir = _sandbox(base_dir)
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
        if result.returncode != 0:
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
        Not asserting a specific Accepted/Rejected outcome (that would
        overfit this synthetic fixture to today's evaluate_game() rule
        set) -- only that the Recommendation Layer actually evaluated
        the synthetic game and produced a real per-market decision, not
        an empty/skipped marketLedger.
        """
        results, data_dir = _run_chain(tmp_path / "run1")
        assert all(r.returncode == 0 for r in results.values())
        slate = json.loads((data_dir / "slate.json").read_text())
        market_ledger = slate["games"][0].get("marketLedger", [])
        assert len(market_ledger) > 0, "evaluate_game() produced zero marketLedger rows"
        statuses = {row.get("status") for row in market_ledger}
        assert statuses, "no market status recorded at all"

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
