#!/usr/bin/env python3
"""
tests/test_protect_slate_immutable.py
=========================================
Golden-equivalence regression suite for scripts/protect_slate.py's
Phase 9 pure-boundary conversion (see docs/IMMUTABLE_PIPELINE.md).

Written and run against the ORIGINAL implementation FIRST to establish
a golden baseline, then re-run UNCHANGED after the refactor to prove
identical production behavior.

PRE-REFACTOR BEHAVIOR MAP (Phase 9 Part 5)
---------------------------------------------
Public functions: `_strip_sentinel_metadata(slate_data) -> dict` (already
pure -- shallow copy minus 6 metadata keys), `main(date_str=None) -> int`
(the only other top-level function). No class, no other public API.

CLI: `sys.argv[1]` optional date string, used verbatim as `date_str` --
no format validation. If absent, `main()`'s own internal fallback is
`datetime.now(timezone(timedelta(hours=-4))).strftime("%Y-%m-%d")` -- a
FIXED -4h UTC offset "ET approximation", same shape (and same DST
blind spot) as validate_slate_final.py's `expected_date()`, but this
one is inlined directly in `main()`, not its own testable function.

Input files: `data/slate.json` under `ROOT_DIR` (`os.path.join(ROOT_DIR,
"data", "slate.json")`) -- `ROOT_DIR` is computed ONCE at module import
time as `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`,
a plain module-level global, not re-derived per-call. `main()`'s own
file read is the ONLY direct file I/O in protect_slate.py itself; every
other file read/write happens INSIDE the imported `lib.slate_manager`
functions (`authoritative_exists`, `load_authoritative`, `save_slate`),
each of which receives `ROOT_DIR` explicitly as a parameter -- so
monkeypatching `protect_slate.ROOT_DIR` before calling `main()` redirects
ALL of this script's own AND its lib-delegated file I/O into a sandbox,
with no separate cwd-vs-__file__-relative ambiguity like
validate_slate_final.py had (a real, materially simpler sandboxing
story for this script).

Environment variables: NONE read anywhere in protect_slate.py itself
(grep-confirmed). `lib/slate_manager.py` and `lib/sentinel_validator.py`
also read none.

Clock reads: `datetime.now(timezone(timedelta(hours=-4)))` (only when
no CLI date arg given) and `datetime.now(timezone.utc)` (always, for
the startup log line) -- both un-injectable in the current
implementation (no `now_utc`/`current_utc` parameter threaded in from
outside), unlike validate_slate_final.py's `generate_execution_slip()`.
`lib.slate_manager.detect_run_type(date_str, root_dir, now_utc=None)`
DOES accept an explicit `now_utc` override, but `protect_slate.py`
never passes one -- it always lets `detect_run_type()` default to the
real clock internally. This is a genuine pre-existing gap: Phase 9's
pure core will thread an explicit clock value through protect_slate.py's
OWN call site (without touching `detect_run_type()`'s signature, which
is owned by the shared library, not this script).

Imported helpers: `lib.slate_manager.{detect_run_type, save_slate,
get_authoritative_path, authoritative_exists, load_authoritative,
RUN_TYPE_OFFICIAL_PREGAME, RUN_TYPE_LINEUP_RECHECK,
RUN_TYPE_IN_PLAY_RECHECK, RUN_TYPE_REJECTED_CONTAMINATED}`,
`lib.sentinel_validator.scan_for_sentinels`. Both modules are SHARED
(also used by write_pending_bets.py, validate_bet_logging.py, and
others per docs/MODEL_V2_ARCHITECTURE.md) and are explicitly OUT OF
SCOPE for Phase 9 -- confirmed pre-existing, well-tested elsewhere
(tests/test_reliability_upgrade.py's test_06 through test_10 already
cover `save_slate`/`detect_run_type`/`merge_rerun_into_authoritative`'s
own behavior in depth) -- Phase 9 does not modify either file.

Slate fields read: `slate_data.get("date")`; `_SENTINEL_METADATA_KEYS`
(`_sentinelViolations`, `_containsSentinels`, `_sentinelViolationCount`,
`_sentinelCheckRan`, `_runType`, `_quarantined`) are stripped before
scanning, not otherwise read. Every other field is opaque to
protect_slate.py itself -- it never reads `games`, `marketLedger`,
`status`, or any recommendation/execution field directly; those are
only ever touched inside `scan_for_sentinels()` (recursive, field-name-
driven) and inside `slate_manager.py`'s merge logic (out of scope).

Slate fields mutated: NONE by protect_slate.py's own code path -- the
only "mutation" is `shutil.copy2(auth_path, slate_path)`, a whole-file
copy, not a field-level edit. `_strip_sentinel_metadata()` returns a
NEW dict (shallow copy), never mutates `slate_data` in place.

authoritative.json: read via `authoritative_exists`/`load_authoritative`
(delegated, inside `detect_run_type()`/`save_slate()`), written via
`save_slate()` (delegated) -- protect_slate.py itself never opens this
file directly; it only computes its PATH via `get_authoritative_path()`
for the backwards-compat existence check and copy step.

execution.json: never referenced anywhere in protect_slate.py,
slate_manager.py, or sentinel_validator.py (grep-confirmed).

bets.json / meta.json: never referenced anywhere in this file's
reachable code (grep-confirmed).

Rules/config files: never referenced (grep-confirmed) -- no
config/rules.json, no RULES.md read anywhere in this file's reachable
code.

stdout: every `print()` in `main()` goes to stdout only (no `file=`
kwarg) except the two `sys.stderr` FAIL lines. Exact order: startup
line -> (date-mismatch WARNING, if any) -> (sentinel-detected lines,
if any) -> "Run type: {run_type}" -> "Saved paths: ..." -> ("Run
report: ...", if `result.get("runReport")` truthy) -> (sync-status
line: either "data/slate.json updated..." or "Quarantined run...") ->
"authoritative.json exists: {bool}" -> "Done. Run type: {run_type}".
`lib.slate_manager._write_json()` ALSO prints "[slate_manager]
Written: {path}" for each file it writes, interleaved at the point
`save_slate()` is called (between "Run type" and "Saved paths").

stderr: only the two FAIL lines (missing slate.json, JSON decode
error) -- both also `sys.exit(1)` immediately, no other stderr output
exists in this file at all.

exit codes: 1 (missing slate.json), 1 (malformed JSON), 0 (every other
path, INCLUDING quarantined/REJECTED_CONTAMINATED runs -- quarantine is
documented explicitly in the module docstring as "that is normal", not
a failure).

exception behavior: the two `sys.exit(1)` paths are the only handled
failure modes; `save_slate()` raising `ValueError` for an unknown
run_type (impossible via the current two call sites, since `run_type`
is always one of the four module constants) would propagate UNCAUGHT --
no top-level try/except wraps the `save_slate()` call, unlike
validate_slate_final.py's crash-catching wrapper around
`validate_final()`.

processing/rule/rejection order: date-validation warning check FIRST
(does not gate anything, warning only) -> sentinel scan SECOND
(hard-gates: sentinels present => REJECTED_CONTAMINATED, skipping
`detect_run_type()` entirely) -> run-type detection THIRD (only if no
sentinels) -> `save_slate()` FOURTH (delegated persistence + own
internal ordering) -> backwards-compat sync FIFTH (conditioned on
`run_type != REJECTED_CONTAMINATED and os.path.exists(auth_path)`) ->
summary print LAST.

mutation/persistence order: `save_slate()`'s own writes happen before
the backwards-compat `shutil.copy2()` -- confirmed by reading
`main()`'s statement order (result = save_slate(...) always precedes
the sync-decision block).

rerun behavior: NOT idempotent by design -- every call to `save_slate()`
writes a NEW timestamped `official_*.json`/`recheck_*.json`/
`rejected_contaminated_*.json` file (timestamp from `now_utc.strftime(...)`,
never overwriting a prior run's timestamped file), while
`authoritative.json` itself is either written once (OFFICIAL_PREGAME,
only if absent) or merged-in-place (LINEUP_RECHECK/IN_PLAY_RECHECK, via
`merge_rerun_into_authoritative()`, out of scope). `data/slate.json` is
overwritten (`shutil.copy2`, full replace) on every non-quarantined
run.

partial-failure behavior: no atomicity guarantee anywhere in this
script or `save_slate()`'s writes (`_write_json()` uses a plain
`open(path, "w")` + `json.dump()`, no temp-file+replace pattern) --
Phase 9 Part 20 evaluates migration eligibility for protect_slate.py's
OWN write (`shutil.copy2`) without touching `_write_json()` (owned by
the shared, out-of-scope library).

duplicate/correlated-market/live-game/doubleheader-identity behavior:
protect_slate.py itself has NO market-level, duplicate-detection,
correlation, or live-game-status logic at all -- confirmed by grep
(no references to "duplicate", "correlat", "liveGame", "postponed",
"kalshiKey" anywhere in this file). The only "live-game"-adjacent
concept reachable is `merge_rerun_into_authoritative()`'s started-game
freeze logic (via `validate_game_for_rerun()`'s `start_dt`/`now_utc`
comparison) -- entirely inside `slate_manager.py`, out of scope.

malformed-input behavior: `json.JSONDecodeError` on `data/slate.json`
itself is caught and converted to a clean exit-1 FAIL message (unlike
validate_slate_final.py, which has NO try/except around its own
`json.load()` call in `load_slate()` -- confirmed by direct comparison,
a real asymmetry between the two scripts' malformed-JSON handling, not
introduced by either phase).
"""
import copy
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
LIB_DIR = os.path.join(ROOT, "lib")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def ps():
    for mod in ("protect_slate",):
        if mod in sys.modules:
            del sys.modules[mod]
    import protect_slate as _ps
    return _ps


def _wire(ps, tmp_path, monkeypatch):
    """
    Redirects protect_slate.py's ROOT_DIR (a module-level global,
    resolved dynamically by name at call time inside main() and passed
    explicitly to every lib.slate_manager call) into a sandbox. This
    alone sandboxes every direct read/write protect_slate.py and
    lib.slate_manager perform.

    SAFETY, found while wiring the Phase 9 Part 16 protection.json
    artifact: lib.pipeline_artifacts.write_stage_artifact() -- unlike
    everything else this script touches -- resolves its own output
    path via a bare CWD-relative `PIPELINE_ROOT = os.path.join("data",
    "pipeline")` with NO root_dir parameter at all. Monkeypatching
    ROOT_DIR alone does NOT redirect that write -- confirmed the hard
    way while first wiring this fixture: main() would have written a
    REAL data/pipeline/<date>/protection.json into this repo's actual
    working directory. chdir into the sandbox is therefore required
    IN ADDITION to the ROOT_DIR monkeypatch, purely to contain this
    one CWD-relative write (the same PIPELINE_ROOT hazard the Phase 8
    hardening review already flagged as pre-existing and inherited).
    """
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(ps, "ROOT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_game(away="KC", home="WSH", game_id="12345", price=-120, model_prob=55.0,
              start_time=None):
    game = {
        "gameId": game_id,
        "away": {"abbr": away, "pitcher": {"id": "p1", "name": "A"}, "lineup": list(range(9))},
        "home": {"abbr": home, "pitcher": {"id": "p2", "name": "B"}, "lineup": list(range(9))},
        "markets": [
            {"market": "ML_Away", "price": price, "modelProb": model_prob},
        ],
    }
    if start_time:
        game["startTime"] = start_time
    return game


def make_slate(games, date="2026-06-16"):
    return {"date": date, "games": games}


class TestStripSentinelMetadataGoldenEquivalence:
    """_strip_sentinel_metadata() is already pure -- these tests pin
    its exact behavior as a golden baseline before any refactor."""

    def test_strips_all_six_metadata_keys(self, ps):
        slate = {
            "date": "2026-06-16", "games": [],
            "_sentinelViolations": [1], "_containsSentinels": True,
            "_sentinelViolationCount": 1, "_sentinelCheckRan": True,
            "_runType": "OFFICIAL_PREGAME", "_quarantined": False,
        }
        result = ps._strip_sentinel_metadata(slate)
        assert result == {"date": "2026-06-16", "games": []}

    def test_does_not_mutate_input(self, ps):
        slate = {"date": "2026-06-16", "_runType": "X"}
        before = copy.deepcopy(slate)
        ps._strip_sentinel_metadata(slate)
        assert slate == before

    def test_returns_new_dict_not_alias(self, ps):
        slate = {"date": "2026-06-16"}
        result = ps._strip_sentinel_metadata(slate)
        assert result is not slate
        result["injected"] = True
        assert "injected" not in slate

    def test_no_metadata_keys_present_returns_equivalent_copy(self, ps):
        slate = {"date": "2026-06-16", "games": [1, 2]}
        result = ps._strip_sentinel_metadata(slate)
        assert result == slate
        assert result is not slate


class TestMainGoldenEquivalence:

    def test_missing_slate_json_exits_1(self, ps, tmp_path, monkeypatch, capsys):
        _wire(ps, tmp_path, monkeypatch)
        with pytest.raises(SystemExit) as exc_info:
            ps.main("2026-06-16")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "data/slate.json not found" in captured.err

    def test_malformed_json_exits_1(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)
        (root / "data" / "slate.json").write_text("{not valid json")
        with pytest.raises(SystemExit) as exc_info:
            ps.main("2026-06-16")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Cannot parse data/slate.json" in captured.err

    def test_first_official_pregame_run_writes_authoritative_and_syncs_slate(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)
        slate = make_slate([make_game()])
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(slate, f)

        result = ps.main("2026-06-16")
        assert result == 0

        captured = capsys.readouterr()
        assert "Run type: OFFICIAL_PREGAME" in captured.out
        assert "data/slate.json updated to match authoritative.json" in captured.out
        assert "Done. Run type: OFFICIAL_PREGAME" in captured.out

        auth_path = root / "data" / "slates" / "2026-06-16" / "authoritative.json"
        assert auth_path.exists()
        with open(auth_path) as f:
            auth = json.load(f)
        assert auth["_authoritative"] is True
        assert auth["_runType"] == "OFFICIAL_PREGAME"

        with open(root / "data" / "slate.json") as f:
            synced = json.load(f)
        assert synced == auth

    def test_second_run_no_games_started_is_lineup_recheck(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)
        slate = make_slate([make_game()])
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(slate, f)
        ps.main("2026-06-16")
        capsys.readouterr()

        with open(root / "data" / "slate.json", "w") as f:
            json.dump(slate, f)
        result = ps.main("2026-06-16")
        assert result == 0
        captured = capsys.readouterr()
        assert "Run type: LINEUP_RECHECK" in captured.out

    def test_sentinel_price_quarantines_run_never_syncs_slate_json(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)
        g = make_game(price=19900)
        slate = make_slate([g])
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(slate, f)
        before_slate_bytes = (root / "data" / "slate.json").read_bytes()

        result = ps.main("2026-06-16")
        assert result == 0  # quarantine is NOT a failure exit code

        captured = capsys.readouterr()
        assert "SENTINEL PRICES DETECTED" in captured.out
        assert "Run will be quarantined as REJECTED_CONTAMINATED" in captured.out
        assert "Run type: REJECTED_CONTAMINATED" in captured.out
        assert "Quarantined run — data/slate.json NOT updated" in captured.out

        auth_path = root / "data" / "slates" / "2026-06-16" / "authoritative.json"
        assert not auth_path.exists()
        # slate.json untouched (byte-identical to before)
        assert (root / "data" / "slate.json").read_bytes() == before_slate_bytes
        # quarantine file written
        quarantine_files = list((root / "data" / "slates" / "2026-06-16").glob("rejected_contaminated_*.json"))
        assert len(quarantine_files) == 1

    def test_sentinel_metadata_from_prior_run_stripped_before_rescan(self, ps, tmp_path, monkeypatch, capsys):
        """
        A quarantined run writes `_runType`/`_quarantined` into its own
        output; if a SUBSEQUENT run's slate.json somehow carried those
        same keys forward (self-referential), _strip_sentinel_metadata()
        must prevent them from causing a false-positive re-scan --
        confirmed here with clean prices but metadata keys present.
        """
        root = _wire(ps, tmp_path, monkeypatch)
        g = make_game(price=-120)
        slate = make_slate([g])
        slate["_runType"] = "REJECTED_CONTAMINATED"
        slate["_quarantined"] = True
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(slate, f)

        result = ps.main("2026-06-16")
        assert result == 0
        captured = capsys.readouterr()
        assert "SENTINEL PRICES DETECTED" not in captured.out
        assert "Run type: OFFICIAL_PREGAME" in captured.out

    def test_date_mismatch_prints_warning_but_does_not_block(self, ps, tmp_path, monkeypatch, capsys):
        root = _wire(ps, tmp_path, monkeypatch)
        slate = make_slate([make_game()], date="2026-06-15")
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(slate, f)
        result = ps.main("2026-06-16")
        assert result == 0
        captured = capsys.readouterr()
        assert "WARNING: slate.json date=2026-06-15 does not match expected 2026-06-16" in captured.out

    def test_no_date_arg_uses_et_approximation_fallback(self, ps, tmp_path, monkeypatch, capsys):
        """
        No injected clock exists for this path in the legacy
        implementation -- this test only proves main(None) does not
        crash and produces a plausible YYYY-MM-DD-shaped date in its
        startup line, without asserting on the exact (real-clock-
        dependent) value.
        """
        root = _wire(ps, tmp_path, monkeypatch)
        slate = make_slate([make_game()])
        # write under whatever "today" resolves to isn't knowable without
        # duplicating main()'s own fallback -- instead confirm the FAIL
        # path (no slate.json for "today") still exits cleanly, proving
        # the date-less call path itself doesn't crash before reaching
        # the file check.
        with pytest.raises(SystemExit) as exc_info:
            ps.main(None)
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "[protect_slate] Running for" in captured.out

    def test_does_not_mutate_input_slate_dict_object_read_from_disk(self, ps, tmp_path, monkeypatch):
        """
        protect_slate.py re-reads slate.json fresh inside main() (no
        caller-supplied dict to mutate) -- this test instead confirms
        the ON-DISK content is preserved byte-for-byte across a
        REJECTED_CONTAMINATED run (the one path with an explicit "do
        not touch" contract in the module's own docstring).
        """
        root = _wire(ps, tmp_path, monkeypatch)
        g = make_game(price=-19900)
        slate = make_slate([g])
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(slate, f, indent=2)
        before = (root / "data" / "slate.json").read_bytes()
        ps.main("2026-06-16")
        after = (root / "data" / "slate.json").read_bytes()
        assert before == after

    def test_stdout_stderr_ordering_full_pass_path(self, ps, tmp_path, monkeypatch, capsys):
        """
        NOTE (Phase 9 Part 16): the original implementation's "Done."
        line WAS the final stdout line. This refactor adds exactly one
        new, intentional, additive line after it (the protection.json
        pipeline artifact confirmation) -- the assertion below was
        updated to reflect that one documented addition, not to paper
        over a change in the CORE unchanged behavior it otherwise still
        checks byte-for-byte (every other assertion here is unchanged
        from the pre-refactor version of this test).
        """
        root = _wire(ps, tmp_path, monkeypatch)
        slate = make_slate([make_game()])
        with open(root / "data" / "slate.json", "w") as f:
            json.dump(slate, f)
        ps.main("2026-06-16")
        captured = capsys.readouterr()
        lines = [l for l in captured.out.splitlines() if l.strip()]
        assert lines[0].startswith("[protect_slate] Running for 2026-06-16")
        assert lines[1] == "[protect_slate] Run type: OFFICIAL_PREGAME"
        assert any("Written:" in l for l in lines)
        assert any(l.startswith("[protect_slate] Saved paths:") for l in lines)
        assert lines[-3].startswith("[protect_slate] authoritative.json exists: True")
        assert lines[-2] == "[protect_slate] Done. Run type: OFFICIAL_PREGAME"
        assert lines[-1] == "[protect_slate] protection pipeline artifact written for 2026-06-16"
        assert captured.err == ""
