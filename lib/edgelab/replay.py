"""
lib/edgelab/replay.py
========================
Level 2 Historical Replay Engine milestone: the smallest trustworthy
replay engine that can rerun historical production decisions from
immutable Snapshots (lib.edgelab.snapshot), using only inputs that were
genuinely available at decision time.

Research-only. Never reads or writes bets.json, data/slate.json,
config/rules.json (except to read the frozen copy inside a Snapshot),
or any other production file. Does not change model probabilities,
recommendation logic, thresholds, staking, risk gates, settlement, CLV,
or market selection — it CALLS the existing production functions
(scripts.build_market_ledger.evaluate_game, scripts.risk_gate.apply_tt_safety/
apply_portfolio_rules/build_execution_artifact_payload) against frozen
historical inputs rather than duplicating their math. See
docs/REPLAY_ENGINE.md for the full design.

REPLAY MODES
------------
CANDIDATE_MODEL: runs the CURRENT checkout's production functions
against a Snapshot's frozen decision-time inputs. This is the only mode
implemented in this milestone.

HISTORICAL_PRODUCTION: would check out and execute the exact historical
production commit captured in the snapshot. UNSUPPORTED this milestone
(see docs/REPLAY_ENGINE.md's "why HISTORICAL_PRODUCTION is unsupported"
section) — checking out and running an old commit inside a shared research
process is unsafe (dependency drift, no isolation, no guarantee the old
commit even runs under the current Python/toolchain) and unnecessary for
this milestone's objective (comparing today's code against historical
inputs is exactly what a regression/reproduction test needs). Requesting
this mode returns runStatus=REJECTED_UNSUPPORTED_MODE, never a fabricated
result.

WHY THIS DOES NOT DUPLICATE MODEL MATH
---------------------------------------
Every probability/edge/pricing computation is a direct call into
scripts.build_market_ledger's already-pure functions
(compute_game_projection_context, evaluate_game) and scripts.risk_gate's
already-isolable functions (apply_tt_safety, apply_portfolio_rules,
build_execution_artifact_payload — all three operate on an in-memory
slate dict with no file I/O). The ONE small block replicated rather than
imported is risk_gate.py main()'s inline "PAPER_ONLY third pass" (see
_apply_paper_only_downgrade) — it is not itself a callable function in
risk_gate.py, and is a mechanical decision-application step (no
probability/pricing math), not model logic. It is directly verified
against real risk_gate.py behavior in tests/edgelab/test_replay.py.
"""

import hashlib
import json
import os
import subprocess
import tempfile
from copy import deepcopy
from datetime import datetime, timezone

from lib.edgelab import ids
from lib.edgelab import snapshot as snap
from lib.edgelab.calibration import MIN_N_CALIBRATED, MIN_N_INSUFFICIENT, calibration_status
from scripts.build_market_ledger import F5_PRICING_VERSION_CURRENT, compute_game_projection_context, evaluate_game
from scripts import risk_gate as _risk_gate

REPLAY_FRAMEWORK_VERSION = "2"  # bumped: maintainer review pass changed comparison/output semantics (see docs/REPLAY_ENGINE.md)
REPLAY_RUNS_ROOT = os.path.join("data", "edgelab", "replay_runs")

MODE_CANDIDATE = "CANDIDATE_MODEL"
MODE_HISTORICAL_PRODUCTION = "HISTORICAL_PRODUCTION"
VALID_MODES = frozenset({MODE_CANDIDATE, MODE_HISTORICAL_PRODUCTION})

ELIGIBLE_LEVEL_2 = "ELIGIBLE_LEVEL_2"
ELIGIBLE_LEVEL_1_ONLY = "ELIGIBLE_LEVEL_1_ONLY"
INELIGIBLE_MISSING_INPUT = "INELIGIBLE_MISSING_INPUT"
INELIGIBLE_CONFIG_AMBIGUITY = "INELIGIBLE_CONFIG_AMBIGUITY"
INELIGIBLE_TEMPORAL_SKEW = "INELIGIBLE_TEMPORAL_SKEW"
INELIGIBLE_INTEGRITY_FAILURE = "INELIGIBLE_INTEGRITY_FAILURE"
INELIGIBLE_UNSUPPORTED_VERSION = "INELIGIBLE_UNSUPPORTED_VERSION"
# Corpus-health audit (2026-08-25 follow-up): NOT an "ineligible" status --
# ineligibility implies a decision existed and this snapshot fails to
# support replaying it. A schedule-triggered run never had an
# authoritative, risk-gated decision to begin with (fetch-slate.yml's
# BLOCK 7 never executes on a `schedule` trigger -- a deliberate safety
# boundary, untouched here), so "there was a decision and we cannot
# replay it" (INELIGIBLE_*) and "there was never supposed to be a
# decision in this run type" (this) are deliberately different states,
# both in status name and in run outcome (see RUN_STATUS_NOT_APPLICABLE_
# NO_DECISION below, distinct from RUN_STATUS_REJECTED_INELIGIBLE).
RESEARCH_ONLY_NO_DECISION = "RESEARCH_ONLY_NO_DECISION"
ELIGIBLE_STATUSES = frozenset({ELIGIBLE_LEVEL_2, ELIGIBLE_LEVEL_1_ONLY})

SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS = frozenset({"1"})

# Level-2-required componentTypes -- deliberately DIFFERENT from (narrower
# in intent than) lib.edgelab.snapshot's own REQUIRED_COMPONENT_TYPES:
# PRODUCTION_SLATE_INPUT is snapshot-required (it's the published record)
# but not replay-required (evaluate_game() never reads it -- it reads
# NORMALIZED_SLATE). Replay eligibility is its own, stricter/narrower
# concept, not a re-read of the snapshot's own completenessStatus.
LEVEL_2_REQUIRED_COMPONENT_TYPES = frozenset({
    "NORMALIZED_SLATE", "RAW_PROJECTIONS", "RECOMMENDATION_OUTPUT",
    "MARKET_UNIVERSE", "RISK_GATE_OUTPUT", "EFFECTIVE_CONFIG",
})
# Missing one of these caps eligibility at Level 1 (approximate) rather
# than blocking replay entirely.
LEVEL_1_NICE_TO_HAVE_COMPONENT_TYPES = frozenset({
    "LINEUP_STATE", "BULLPEN_STATE", "WEATHER", "MARKET_OBSERVATIONS",
    "EXECUTABLE_PRICES", "BID_ASK", "PARK_FACTORS",
})

RUN_STATUS_COMPLETED = "COMPLETED"
RUN_STATUS_FAILED = "FAILED"
RUN_STATUS_REJECTED_INELIGIBLE = "REJECTED_INELIGIBLE"
RUN_STATUS_REJECTED_UNSUPPORTED_MODE = "REJECTED_UNSUPPORTED_MODE"
# Corpus-health audit (2026-08-25 follow-up): a schedule-triggered
# snapshot's honest outcome -- never "rejected" (nothing here is broken
# or ineligible; there was simply never a betting decision to replay for
# this run type). See RESEARCH_ONLY_NO_DECISION above.
RUN_STATUS_NOT_APPLICABLE_NO_DECISION = "NOT_APPLICABLE_NO_DECISION"

CMP_UNCHANGED = "UNCHANGED"
CMP_PROBABILITY_CHANGED_ONLY = "PROBABILITY_CHANGED_ONLY"
CMP_EDGE_CHANGED = "EDGE_CHANGED"
CMP_RECOMMENDATION_ADDED = "RECOMMENDATION_ADDED"
CMP_RECOMMENDATION_REMOVED = "RECOMMENDATION_REMOVED"
CMP_TIER_UPGRADE = "TIER_UPGRADE"
CMP_TIER_DOWNGRADE = "TIER_DOWNGRADE"
CMP_EXPRESSION_CHANGED = "EXPRESSION_CHANGED"  # reserved -- not populated this milestone, see schema
CMP_NOT_COMPARABLE = "NOT_COMPARABLE"
CMP_ORIGINAL_DATA_MISSING = "ORIGINAL_DATA_MISSING"

_TIER_ORDER = {"PAPER": 0, "MEDIUM": 1, "HIGH": 2}
_PROBABILITY_EPSILON = 0.01  # percentage points -- production rounds to 2 decimals already


class ReplayError(Exception):
    """Raised for a genuine replay execution failure (never for an ineligible snapshot -- that's a runStatus, not an exception)."""


def _git_commit_sha():
    """Same convention as lib.edgelab.snapshot._git_commit_sha()."""
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _git_working_tree_dirty():
    """
    True when tracked files differ from HEAD (staged or unstaged). Maintainer
    review finding (item 4): a bare `candidateModelCommitSha = git rev-parse
    HEAD` labels the executed code with a commit whose committed content may
    NOT be what actually ran, if the working tree has local uncommitted
    edits to e.g. scripts/build_market_ledger.py or scripts/risk_gate.py --
    a materially misleading identity claim. Returns False (not dirty) if git
    itself is unavailable, matching _git_commit_sha()'s own fail-open
    convention for a missing git binary; a None commit_sha already signals
    "no identity available" in that case regardless.
    """
    try:
        result = subprocess.run(["git", "diff", "--quiet", "HEAD"], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode != 0


def _candidate_model_commit_identity():
    """
    Returns (candidateModelCommitSha, limitation_reason_or_None). The SHA is
    suffixed "-dirty" (a standard git convention, e.g. `git describe
    --dirty`) whenever the working tree has uncommitted changes to tracked
    files, so a dirty run's identity (and therefore its replayRunId, which
    embeds this string) can never collide with -- or be silently confused
    with -- a clean run at the same commit.
    """
    sha = _git_commit_sha()
    if sha and _git_working_tree_dirty():
        return f"{sha}-dirty", "CANDIDATE_WORKING_TREE_DIRTY"
    return sha, None


def canonical_json_bytes(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_RUN_HASH_EXCLUDED_FIELDS = frozenset({"manifestHash", "startedAt", "completedAt", "provenance", "workflowRunId"})


def compute_run_manifest_hash(run: dict) -> str:
    candidate = {k: v for k, v in run.items() if k not in _RUN_HASH_EXCLUDED_FIELDS}
    return sha256_bytes(canonical_json_bytes(candidate))


# ── 1. Replay eligibility (item 2) ───────────────────────────────────────

def assess_replay_eligibility(manifest: dict) -> dict:
    """
    Mechanical rule table, evaluated in order, first match wins. See
    docs/REPLAY_ENGINE.md for the full writeup.

      1. INELIGIBLE_UNSUPPORTED_VERSION -- manifest.schemaVersion not supported.
      2. INELIGIBLE_INTEGRITY_FAILURE   -- verify_snapshot() fails.
      3. RESEARCH_ONLY_NO_DECISION      -- this run was schedule-triggered
         (lib.edgelab.snapshot.is_schedule_triggered_run()); no authoritative,
         risk-gated decision exists for this manifest at all, by
         architecture, not by defect. Checked BEFORE temporal-skew/missing-
         input specifically because those two are questions about
         DECISION-replay fidelity, which does not apply here -- "there was
         a decision and we cannot replay it" (the INELIGIBLE_* family) and
         "there was never supposed to be a decision in this run type"
         (this) are different states; see corpus-health audit (2026-08-25
         follow-up) and RUN_STATUS_NOT_APPLICABLE_NO_DECISION.
      4. INELIGIBLE_TEMPORAL_SKEW       -- temporalConsistency.skewDetected.
      5. INELIGIBLE_MISSING_INPUT       -- any Level-2-required component MISSING.
      6. INELIGIBLE_CONFIG_AMBIGUITY    -- rulesConfigVersion unknown (cannot
         even identify which config version was in force). NOTE:
         EFFECTIVE_CONFIG being merely PARTIAL (the normal case in this
         repository -- see lib.edgelab.snapshot's capture_effective_config)
         does NOT trigger this on its own, as long as rulesConfigVersion
         IS known -- Level 2 replay fidelity is about INPUT preservation,
         not about whether every hardcoded-in-code threshold is captured
         (that is a separate, LEVEL_3_CODE_PINNED / productionCommitSha
         concern). This is deliberate and documented -- do not silently
         promote a config-ambiguous snapshot, but do not conflate
         "partial provenance" with "ambiguous version" either.
      7. ELIGIBLE_LEVEL_1_ONLY          -- some Level-1-nice-to-have
         component MISSING -- still replayable, but only approximately.
      8. ELIGIBLE_LEVEL_2               -- otherwise.

    Returns {"eligibilityStatus": ..., "limitationReasons": [...]}.
    """
    if manifest.get("schemaVersion") not in SUPPORTED_SNAPSHOT_SCHEMA_VERSIONS:
        return {"eligibilityStatus": INELIGIBLE_UNSUPPORTED_VERSION,
                "limitationReasons": ["UNSUPPORTED_SNAPSHOT_SCHEMA_VERSION"]}

    verification = snap.verify_snapshot(manifest)
    if verification["overallStatus"] != "VERIFIED":
        return {"eligibilityStatus": INELIGIBLE_INTEGRITY_FAILURE,
                "limitationReasons": ["SNAPSHOT_INTEGRITY_FAILURE"]}

    if snap.is_schedule_triggered_run(manifest):
        return {"eligibilityStatus": RESEARCH_ONLY_NO_DECISION,
                "limitationReasons": ["NO_AUTHORITATIVE_DECISION_SCHEDULE_TRIGGERED_RUN"]}

    temporal = manifest.get("temporalConsistency") or {}
    if temporal.get("skewDetected"):
        return {"eligibilityStatus": INELIGIBLE_TEMPORAL_SKEW,
                "limitationReasons": ["TEMPORAL_SKEW_DETECTED"]}

    components_by_type = {c["componentType"]: c for c in manifest.get("components", [])}

    missing_required = sorted(
        t for t in LEVEL_2_REQUIRED_COMPONENT_TYPES
        if (components_by_type.get(t) or {}).get("availabilityStatus") != snap.AVAILABLE
        and (components_by_type.get(t) or {}).get("availabilityStatus") != snap.PARTIAL
    )
    if missing_required:
        return {"eligibilityStatus": INELIGIBLE_MISSING_INPUT,
                "limitationReasons": [f"MISSING_COMPONENT_{t}" for t in missing_required]}

    limitations = []
    effective_config = components_by_type.get("EFFECTIVE_CONFIG") or {}
    if effective_config.get("availabilityStatus") == snap.PARTIAL:
        limitations.append("EFFECTIVE_CONFIG_PARTIAL")

    if manifest.get("rulesConfigVersion") is None:
        return {"eligibilityStatus": INELIGIBLE_CONFIG_AMBIGUITY,
                "limitationReasons": limitations + ["RULES_CONFIG_VERSION_UNKNOWN"]}

    if manifest.get("productionCommitSha") is None:
        limitations.append("PRODUCTION_COMMIT_UNKNOWN")

    missing_nice = sorted(
        t for t in LEVEL_1_NICE_TO_HAVE_COMPONENT_TYPES
        if (components_by_type.get(t) or {}).get("availabilityStatus") in (snap.MISSING, None)
    )
    if missing_nice:
        return {"eligibilityStatus": ELIGIBLE_LEVEL_1_ONLY,
                "limitationReasons": limitations + [f"MISSING_COMPONENT_{t}" for t in missing_nice]}

    return {"eligibilityStatus": ELIGIBLE_LEVEL_2, "limitationReasons": limitations}


def derive_replay_fidelity_from_eligibility(eligibility_status: str, production_commit_sha, candidate_commit_sha) -> str:
    if eligibility_status != ELIGIBLE_LEVEL_2:
        return snap.LEVEL_1_APPROXIMATE
    if production_commit_sha and candidate_commit_sha and production_commit_sha == candidate_commit_sha:
        return snap.LEVEL_3_CODE_PINNED
    return snap.LEVEL_2_PRODUCTION_EQUIVALENT


# ── 2. Candidate-mode replay execution (item 5/6) ────────────────────────

def _apply_paper_only_downgrade(slate: dict, decision_reason: str):
    """Mirrors scripts/risk_gate.py's main() inline PAPER_ONLY third pass
    exactly -- see this module's docstring for why this one block is
    replicated rather than imported."""
    for g in slate.get("games", []):
        if g.get("excludedFromSlate"):
            continue
        for entry in g.get("marketLedger", []):
            tier = (entry.get("confidenceTier") or entry.get("confidence") or "").upper()
            if entry.get("status") == "Accepted" and tier in _risk_gate.REAL_MONEY_TIERS:
                entry["confidence"] = "PAPER"
                entry["confidenceTier"] = "PAPER"
                entry["betSize"] = 1.0
                entry["realMoneyBlocked"] = True
                entry["blockReason"] = f"RISK_GATE_PAPER_ONLY: {decision_reason}"


def _market_key(row: dict) -> str:
    ticker = row.get("ticker") or row.get("marketTicker")
    market_name = row.get("market")
    return f"{ticker}:{market_name}" if ticker else f"UNKNOWN:{market_name}"


def run_candidate_replay(manifest: dict):
    """
    Executes CANDIDATE_MODEL replay: rebuilds marketLedger rows from the
    frozen NORMALIZED_SLATE games via the real production functions
    (compute_game_projection_context + evaluate_game), then replays the
    risk-gate/portfolio decision on the result. Structurally cannot see
    settlement/closing-line data -- it never loads or is passed anything
    from POST_GAME_SETTLEMENT/CLOSING_LINE (see
    tests/edgelab/test_replay.py::TestPostgameLeakagePrevention).

    Returns a dict with replayedGames (list of game dicts with a fresh
    marketLedger), replayedRiskGatePayload, and the corresponding ORIGINAL
    data read straight from the snapshot's own frozen components (never
    recomputed) for comparison.
    """
    # Walk-forward integrity guard (item 9): candidate evaluation must only
    # ever be handed a PRE_GAME_DECISION manifest -- POST_GAME_SETTLEMENT/
    # CLOSING_LINE snapshots structurally cannot enter this function at
    # all, regardless of what a caller passes in. This is redundant with
    # (not a replacement for) snapshot.py's own structural exclusion of
    # SETTLEMENT/CLV from PRE_GAME_DECISION manifests -- belt and
    # suspenders, both independently enforced.
    if manifest.get("snapshotStage") != snap.STAGE_PRE_GAME_DECISION:
        raise ReplayError(
            f"run_candidate_replay() requires a {snap.STAGE_PRE_GAME_DECISION} manifest, "
            f"got {manifest.get('snapshotStage')!r} -- postgame/closing data must never enter candidate evaluation"
        )

    # Second, independent guard: even for a genuine PRE_GAME_DECISION
    # manifest, assert its own SETTLEMENT/CLV components are the
    # structurally-excluded NOT_APPLICABLE_FOR_STAGE placeholders, never
    # AVAILABLE -- if this ever fires, a future snapshot.py change broke
    # the look-ahead-bias guarantee this replay engine depends on, and
    # candidate evaluation must fail loudly rather than silently ingest
    # postgame data.
    for component in manifest.get("components", []):
        if component["componentType"] in ("SETTLEMENT", "CLV") and component["availabilityStatus"] == snap.AVAILABLE:
            raise ReplayError(
                f"{component['componentType']} is AVAILABLE on a {snap.STAGE_PRE_GAME_DECISION} manifest -- "
                f"this must never happen (look-ahead-bias guarantee violated); refusing to execute candidate replay"
            )

    # Third guard, maintainer review finding (item 9/10): apply_tt_safety()/
    # apply_portfolio_rules() fall back to the REAL wall clock
    # (datetime.now()) via check_game_status() whenever now_ts is None --
    # a determinism and leakage hole if this manifest's productionRunId
    # were ever missing (replay's game-skip decision would then depend on
    # WHEN replay is run, not the frozen historical decision time). Every
    # ELIGIBLE PRE_GAME_DECISION snapshot has a real productionRunId by
    # construction (RECOMMENDATION_OUTPUT being AVAILABLE, which
    # eligibility already requires, implies _production_run_key() found a
    # real recommendations.json to derive one from) -- this assertion
    # exists so a future change that breaks that invariant fails loudly
    # here rather than silently reading the clock.
    if not manifest.get("productionRunId"):
        raise ReplayError(
            "manifest has no productionRunId -- refusing to execute candidate replay, since "
            "apply_tt_safety()/apply_portfolio_rules() would otherwise fall back to the real "
            "wall clock for their game-skip decision, breaking replay determinism"
        )

    normalized_envelope = snap.load_frozen_component(manifest, "NORMALIZED_SLATE")
    if normalized_envelope is None:
        raise ReplayError("NORMALIZED_SLATE is not available/frozen in this snapshot -- cannot execute candidate replay")

    original_rec_envelope = snap.load_frozen_component(manifest, "RECOMMENDATION_OUTPUT")
    original_risk_envelope = snap.load_frozen_component(manifest, "RISK_GATE_OUTPUT")

    normalized_games = (normalized_envelope.get("data") or {}).get("games") or []
    original_games = (original_rec_envelope.get("data") or {}).get("games") or [] if original_rec_envelope else []
    original_games_by_id = {g.get("gameId"): g for g in original_games}

    replayed_games = deepcopy(normalized_games)
    for g in replayed_games:
        projection_context = compute_game_projection_context(g)
        g["marketLedger"] = evaluate_game(g, projection_context)

    replay_slate = {"date": manifest.get("snapshotDate"), "games": replayed_games}
    reference_ts = manifest.get("productionRunId")  # the frozen decision-time reference timestamp
    _risk_gate.apply_tt_safety(replay_slate, now_ts=reference_ts)
    decision, report = _risk_gate.apply_portfolio_rules(replay_slate, now_ts=reference_ts)
    if decision == "PAPER_ONLY":
        _apply_paper_only_downgrade(replay_slate, report["decision_reason"])
    replayed_risk_payload = _risk_gate.build_execution_artifact_payload(replay_slate, decision, report["decision_reason"])

    return {
        "replayedGames": replayed_games,
        "originalGamesById": original_games_by_id,
        "originalRiskGatePayload": (original_risk_envelope or {}).get("data") or {},
        "replayedRiskGatePayload": replayed_risk_payload,
    }


# ── 3. Decision comparison (item 7) ──────────────────────────────────────

def _values_differ(a, b, epsilon=_PROBABILITY_EPSILON):
    if a is None or b is None:
        return a is not b
    return abs(a - b) > epsilon


def _row_edge(row):
    if row is None:
        return None
    if row.get("calibratedEdgeVsExecutable") is not None:
        return row["calibratedEdgeVsExecutable"]
    return row.get("edge")


def classify_comparison(original_row, replayed_row, original_candidate, replayed_candidate):
    """
    Returns (comparisonClassification, changeReasons: list[str]). See
    docs/REPLAY_ENGINE.md for the exact decision table. A changed
    decision is NEVER itself interpreted as an improvement here -- this
    function is purely descriptive.
    """
    if original_row is None:
        return CMP_ORIGINAL_DATA_MISSING, ["NO_ORIGINAL_ROW_FOR_THIS_MARKET"]

    orig_prob = original_row.get("modelProb")
    replay_prob = replayed_row.get("modelProb") if replayed_row else None

    if orig_prob is None and replay_prob is None:
        # Neither side ever prices this market at all (e.g. RL markets --
        # a structural non-pricing situation shared by both original and
        # replay, not a data-availability gap on either side).
        return CMP_NOT_COMPARABLE, ["NEITHER_ORIGINAL_NOR_REPLAY_PRODUCED_A_PROBABILITY"]

    # Maintainer review finding (item 6): a row that EXISTS but never
    # produced a modelProb WHILE REPLAY DID (e.g. the original production
    # run classified it "Missing Data" for a data-quality reason, not
    # because the market is unpriceable) provides no real historical
    # baseline to compare against -- a materially different cause than a
    # genuine probability change, and must not be collapsed into
    # PROBABILITY_CHANGED_ONLY/RECOMMENDATION_ADDED/etc, which would
    # misrepresent "we have no original to compare" as "the decision
    # changed".
    if orig_prob is None:
        return CMP_ORIGINAL_DATA_MISSING, ["ORIGINAL_ROW_HAS_NO_MODEL_PROBABILITY"]

    if replay_prob is None:
        # Symmetric replay-side gap -- original DOES have a valid
        # baseline, but replay itself failed to produce a comparable
        # probability. A real, distinct finding from ORIGINAL_DATA_MISSING
        # (this is about replay's own output, not archived data).
        return CMP_NOT_COMPARABLE, ["REPLAY_DID_NOT_PRODUCE_A_PROBABILITY"]

    orig_status = (original_candidate or {}).get("status")
    replay_status = (replayed_candidate or {}).get("status")
    orig_tier = (original_candidate or {}).get("tier")
    replay_tier = (replayed_candidate or {}).get("tier")
    orig_accepted = orig_status == "Accepted"
    replay_accepted = replay_status == "Accepted"

    prob_changed = _values_differ(orig_prob, replay_prob)
    edge_changed = _values_differ(_row_edge(original_row), _row_edge(replayed_row))

    reasons = []
    if prob_changed:
        reasons.append("PROBABILITY_CHANGED")
    if edge_changed:
        reasons.append("EDGE_CHANGED")

    if not orig_accepted and replay_accepted:
        reasons.append("RECOMMENDATION_NEWLY_ACCEPTED")
        return CMP_RECOMMENDATION_ADDED, reasons
    if orig_accepted and not replay_accepted:
        reasons.append("RECOMMENDATION_NO_LONGER_ACCEPTED")
        return CMP_RECOMMENDATION_REMOVED, reasons
    if orig_accepted and replay_accepted and orig_tier != replay_tier:
        direction = _TIER_ORDER.get(replay_tier, -1) - _TIER_ORDER.get(orig_tier, -1)
        reasons.append(f"TIER_{orig_tier}_TO_{replay_tier}")
        return (CMP_TIER_UPGRADE if direction > 0 else CMP_TIER_DOWNGRADE), reasons
    if edge_changed:
        return CMP_EDGE_CHANGED, reasons
    if prob_changed:
        return CMP_PROBABILITY_CHANGED_ONLY, reasons
    return CMP_UNCHANGED, []


# Item 10: mismatch categorization -- do not force equality by hardcoding
# expected outputs; instead mechanically classify WHY a real mismatch
# occurred wherever a concrete signal exists in the data itself.
MISMATCH_EXPECTED_MODEL_VERSION_CHANGED = "EXPECTED_MODEL_VERSION_CHANGED"
MISMATCH_EXPECTED_CONFIG_INCOMPLETE = "EXPECTED_CONFIG_INCOMPLETE"
MISMATCH_HISTORICAL_ARTIFACT_LIMITATION = "HISTORICAL_ARTIFACT_LIMITATION"
MISMATCH_REPLAY_ENGINE_DEFECT_SUSPECTED = "REPLAY_ENGINE_DEFECT_SUSPECTED"
MISMATCH_PRODUCTION_NONDETERMINISM_SUSPECTED = "PRODUCTION_NONDETERMINISM_SUSPECTED"


def classify_mismatch_reason(original_row, market_name, limitation_reasons):
    """
    Best-effort, evidence-based categorization of a changed decision --
    never a narrative guess presented as fact. Checked in order, most
    concrete evidence first; falls through to a suspected-defect flag
    (never silently "explained away") when no concrete signal is found.
    """
    if market_name in ("F5_ML_Away", "F5_ML_Home"):
        original_version = (original_row or {}).get("f5PricingVersion")
        if original_version != F5_PRICING_VERSION_CURRENT:
            return MISMATCH_EXPECTED_MODEL_VERSION_CHANGED, (
                f"original row's f5PricingVersion={original_version!r} != current "
                f"F5_PRICING_VERSION_CURRENT={F5_PRICING_VERSION_CURRENT!r}"
            )
    if "EFFECTIVE_CONFIG_PARTIAL" in limitation_reasons:
        return MISMATCH_EXPECTED_CONFIG_INCOMPLETE, (
            "EFFECTIVE_CONFIG is PARTIAL for this snapshot -- a hardcoded-in-code "
            "threshold not captured in config/rules.json may have changed since capture"
        )
    return MISMATCH_REPLAY_ENGINE_DEFECT_SUSPECTED, (
        "no concrete evidence (pricing version, config completeness) explains this "
        "mismatch -- requires manual investigation; NOT assumed benign"
    )


# ── 4. Settlement / CLV linkage (item 8) ─────────────────────────────────

def _linked_settlement_and_clv(manifest):
    """
    Reuses the linked POST_GAME_SETTLEMENT snapshot's ALREADY-COMPUTED
    settlement/CLV records (produced by lib.edgelab.settlement /
    lib.edgelab.clv originally) -- never re-derives a settlement from raw
    scores here. Returns (settlement_rows, clv_rows, reason_if_unavailable).
    """
    date = manifest.get("snapshotDate")
    postgame = snap.load_manifest(snap.STAGE_POST_GAME_SETTLEMENT, date)
    if postgame is None:
        return None, None, "NO_POSTGAME_SNAPSHOT_FOR_DATE"
    if manifest.get("snapshotId") not in (postgame.get("linkedSnapshotIds") or []):
        return None, None, "POSTGAME_SNAPSHOT_NOT_LINKED_TO_THIS_PREGAME_RUN"
    verification = snap.verify_snapshot(postgame)
    if verification["overallStatus"] != "VERIFIED":
        return None, None, "POSTGAME_SNAPSHOT_INTEGRITY_FAILURE"
    settlement_rows = snap.load_frozen_component(postgame, "SETTLEMENT")
    clv_rows = snap.load_frozen_component(postgame, "CLV")
    if settlement_rows is None and clv_rows is None:
        return None, None, "POSTGAME_SNAPSHOT_HAS_NO_SETTLEMENT_OR_CLV_DATA"
    return settlement_rows or [], clv_rows or [], None


def _settlement_linkage_for_ticker(settlement_by_ticker, unavailable_reason, ticker):
    if unavailable_reason:
        return {"status": "UNRESOLVED", "result": None, "reason": unavailable_reason}
    record = settlement_by_ticker.get(ticker) if ticker else None
    if record is None:
        return {"status": "UNRESOLVED", "result": None, "reason": "NO_SETTLEMENT_RECORD_FOR_THIS_MARKET"}
    if record.get("settlementStatus") != "SETTLED":
        return {"status": "UNRESOLVED", "result": None,
                "reason": record.get("unavailableReason") or f"SETTLEMENT_STATUS_{record.get('settlementStatus')}"}
    return {"status": "RESOLVED", "result": record.get("result"), "reason": None}


def _closing_clv_by_ticker(clv_rows):
    """
    Maintainer review finding (item 8, Forward Replay Corpus milestone):
    a ticker can have MULTIPLE ClvQuote rows -- one per observation
    checkpoint (FIRST_DAILY, T_MINUS_90, ..., CLOSING) -- confirmed
    against real 2026-08-02 data: 620 of 4844 tickers have more than one
    row. Only the row with isClosingQuote=True is the genuine closing
    quote (per clv_quote.schema.json: "True only for the single quote
    selected as the final valid pre-suspension/pre-start tradable quote
    for this market") -- the checkpoint label alone is NOT reliable for
    this (confirmed: real rows exist where isClosingQuote=True but
    checkpoint is T_MINUS_90, not "CLOSING", when a market suspended
    early). Picking "whichever row happens to be last in file iteration
    order" (the previous behavior) could silently substitute an earlier,
    non-closing checkpoint's price for the actual close.

    Filters to isClosingQuote=True rows only, grouped by ticker. A
    ticker with zero such rows has no determined closing quote yet
    (UNRESOLVED downstream, via NO_CLV_QUOTE_FOR_THIS_MARKET) -- never
    substituted with a non-closing checkpoint. A ticker with MORE than
    one row flagged isClosingQuote=True is a genuine upstream data-
    quality issue (never expected in practice) -- reported as ambiguous
    rather than guessed at, and excluded from the returned map.

    Returns (closing_by_ticker, ambiguous_ticker_list).
    """
    closing_rows_by_ticker = {}
    for row in clv_rows:
        ticker = row.get("marketTicker")
        if ticker and row.get("isClosingQuote"):
            closing_rows_by_ticker.setdefault(ticker, []).append(row)

    resolved, ambiguous = {}, []
    for ticker, rows in closing_rows_by_ticker.items():
        if len(rows) == 1:
            resolved[ticker] = rows[0]
        else:
            ambiguous.append(ticker)
    return resolved, ambiguous


def _clv_linkage_for_ticker(clv_by_ticker, unavailable_reason, ticker, entry_implied_pct):
    """
    CLV here is a market-level (not placed-bet-level) proxy: closing
    quote's implied probability minus the entry implied probability
    (kalshiVF) the replayed row actually used, in percentage points.
    Deliberately NOT the placed-bet CLV lib.edgelab.clv computes for a
    real bet (a replayed decision may never have been bet) -- labeled
    as such, never conflated with it.
    """
    if unavailable_reason:
        return {"status": "UNRESOLVED", "clvValue": None, "reason": unavailable_reason}
    record = clv_by_ticker.get(ticker) if ticker else None
    if record is None:
        return {"status": "UNRESOLVED", "clvValue": None, "reason": "NO_CLV_QUOTE_FOR_THIS_MARKET"}
    yes_bid, yes_ask = record.get("yesBid"), record.get("yesAsk")
    if yes_bid is None or yes_ask is None or entry_implied_pct is None:
        return {"status": "UNRESOLVED", "clvValue": None, "reason": "INCOMPLETE_CLOSING_QUOTE_OR_ENTRY_PRICE"}
    closing_implied_pct = (yes_bid + yes_ask) / 2.0
    return {"status": "RESOLVED", "clvValue": round(closing_implied_pct - entry_implied_pct, 3), "reason": None}


# ── 5. Scoring: Brier score / log loss / calibration error (item 8) ─────
# Not duplicated from anywhere else in this repo -- lib.edgelab.calibration
# is descriptive-bucket-based and does not compute these; standard
# formulas only, no model logic.

def brier_score(probability: float, outcome: int) -> float:
    """probability in [0,1], outcome in {0,1}."""
    return (probability - outcome) ** 2


def log_loss(probability: float, outcome: int, epsilon: float = 1e-9) -> float:
    p = min(max(probability, epsilon), 1 - epsilon)
    return -(outcome * _ln(p) + (1 - outcome) * _ln(1 - p))


def _ln(x):
    import math
    return math.log(x)


def score_resolved_results(resolved):
    """
    resolved: list of {"probability": float in [0,1], "outcome": 0|1}.
    Returns None (not a fabricated all-zero report) if empty. Applies the
    existing sample-size gate (lib.edgelab.calibration.calibration_status)
    -- never withholds the computed number, the status is a reading
    instruction, same convention as calibration.py.

    roi is always null: ROI requires a real staked amount, and a replayed
    decision is research-only -- it may never have been placed as a real
    PlacedBet with a real stake (see clvLinkage's docstring for the same
    point about CLV). Fabricating a flat-unit stake to compute a number
    would be inventing data never actually risked, exactly what this
    milestone's item 8 forbids ("never infer outcome from a related-but-
    different market" extends to never inferring a stake that never
    existed). The field exists (per item 8's report list) but stays null
    until a real staking/ledger link is designed -- see docs/REPLAY_ENGINE.md.
    """
    n = len(resolved)
    if n == 0:
        return None
    briers = [brier_score(r["probability"], r["outcome"]) for r in resolved]
    losses = [log_loss(r["probability"], r["outcome"]) for r in resolved]
    win_count = sum(r["outcome"] for r in resolved)
    actual_win_rate = win_count / n
    expected_win_rate = sum(r["probability"] for r in resolved) / n
    return {
        "n": n,
        "sampleSizeStatus": calibration_status(n),
        "winRate": round(actual_win_rate, 4),
        "expectedWinRate": round(expected_win_rate, 4),
        "calibrationError": round(actual_win_rate - expected_win_rate, 4),
        "avgBrierScore": round(sum(briers) / n, 6),
        "avgLogLoss": round(sum(losses) / n, 6),
        "roi": None,
    }


# ── 6. Output writer ─────────────────────────────────────────────────────

def replay_run_dir(replay_run_id: str) -> str:
    return os.path.join(REPLAY_RUNS_ROOT, replay_run_id)


def _atomic_write_json(dest_path: str, obj):
    dest_dir = os.path.dirname(dest_path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".replay.", suffix=".tmp", dir=dest_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def write_replay_outputs(run: dict, results: list):
    """
    Write-once: refuses to overwrite an existing replay run directory
    with different content (mirrors lib.edgelab.snapshot's write-once
    discipline). Returns {"outcome": "created"|"noop_verified"|"conflict", ...}.
    """
    out_dir = replay_run_dir(run["replayRunId"])
    run_path = os.path.join(out_dir, "replay_run.json")
    results_path = os.path.join(out_dir, "replay_results.jsonl")

    if os.path.exists(run_path):
        with open(run_path) as f:
            existing = json.load(f)
        if existing.get("manifestHash") == run.get("manifestHash"):
            return {"outcome": "noop_verified", "path": out_dir}
        conflict_dir = os.path.join(out_dir, "conflicts", datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        os.makedirs(conflict_dir, exist_ok=True)
        _atomic_write_json(os.path.join(conflict_dir, "candidate_replay_run.json"), run)
        return {"outcome": "conflict", "path": conflict_dir}

    os.makedirs(out_dir, exist_ok=True)
    _atomic_write_json(run_path, run)
    lines = [json.dumps(r, sort_keys=True) for r in results]
    dest_dir = os.path.dirname(results_path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".replay.", suffix=".tmp", dir=dest_dir)
    try:
        with os.fdopen(fd, "w") as f:
            for line in lines:
                f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, results_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return {"outcome": "created", "path": out_dir}


def load_replay_run(replay_run_id: str):
    path = os.path.join(replay_run_dir(replay_run_id), "replay_run.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_replay_results(replay_run_id: str):
    path = os.path.join(replay_run_dir(replay_run_id), "replay_results.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# ── Walk-forward integrity helpers (item 9) ──────────────────────────────
#
# No model fitting exists yet in this repository, so there is nothing yet
# that could "see" future data during a fit -- the safeguards below are
# the ones already relevant (structural leakage prevention, chronological
# processing); candidate-parameter training-cutoff identification and
# cache-invalidation-by-date become relevant only once a future milestone
# introduces a fitted model. See docs/REPLAY_ENGINE.md.

def sorted_snapshot_dates(dates):
    """Chronological (oldest first) -- the historical-execution CLI must
    always process dates in this order, never sorted by discovery order
    or reversed, so a hypothetical future fitted-model replay can never
    accidentally see a later date before an earlier one in the same run."""
    return sorted(dates)


# ── 7. Orchestration (item 4/6/11) ────────────────────────────────────────

def execute_replay(manifest: dict, replay_mode: str = MODE_CANDIDATE, allow_level_1: bool = False,
                    candidate_model_version=None, workflow_run_id=None):
    """
    The single entrypoint: assesses eligibility, executes (only if
    eligible or allow_level_1 is explicitly set), compares every market,
    joins settlement/CLV where trustworthy, and returns (run, results)
    -- NEITHER is written to disk here; call write_replay_outputs()
    separately (mirrors lib.edgelab.snapshot's build-then-commit split).

    Refuses to run an ineligible Level 2 replay unless allow_level_1 is
    explicitly True (the CLI's --allow-approximate flag) -- never a
    silent fidelity downgrade (item 12).
    """
    if replay_mode not in VALID_MODES:
        raise ValueError(f"unknown replayMode {replay_mode!r}, must be one of {sorted(VALID_MODES)}")

    started_at = ids.utc_now_iso()
    candidate_commit_sha, dirty_tree_limitation = _candidate_model_commit_identity()
    eligibility = assess_replay_eligibility(manifest)
    eligibility_status = eligibility["eligibilityStatus"]
    limitation_reasons = list(eligibility["limitationReasons"])
    if dirty_tree_limitation:
        limitation_reasons.append(dirty_tree_limitation)

    base_fields = {
        "schemaVersion": "1",
        "snapshotId": manifest.get("snapshotId"),
        "snapshotManifestHash": manifest.get("manifestHash"),
        "snapshotDate": manifest.get("snapshotDate"),
        "productionRunId": manifest.get("productionRunId"),
        "replayFrameworkVersion": REPLAY_FRAMEWORK_VERSION,
        "replayMode": replay_mode,
        "candidateModelCommitSha": candidate_commit_sha,
        "candidateModelVersion": candidate_model_version,
        "productionModelCommitSha": manifest.get("productionCommitSha"),
        "pricingVersions": dict(manifest.get("pricingVersionsByFamily") or {}),
        "eligibilityStatus": eligibility_status,
        "startedAt": started_at,
        "workflowRunId": workflow_run_id,
    }

    if replay_mode == MODE_HISTORICAL_PRODUCTION:
        run = {
            **base_fields,
            "replayRunId": ids.build_replay_run_id(manifest.get("snapshotId"), replay_mode, candidate_commit_sha or "", REPLAY_FRAMEWORK_VERSION),
            "replayFidelity": snap.LEVEL_1_APPROXIMATE,
            "completedAt": ids.utc_now_iso(),
            "runStatus": RUN_STATUS_REJECTED_UNSUPPORTED_MODE,
            "limitationReasons": sorted(set(limitation_reasons + ["HISTORICAL_PRODUCTION_REPLAY_UNSUPPORTED_THIS_MILESTONE"])),
            "summary": {"marketsEvaluated": 0, "marketsComparable": 0, "decisionsChanged": 0,
                        "settledResolved": 0, "settledUnresolved": 0, "clvResolved": 0},
            "performance": None,
            "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": None,
                           "capturedAt": started_at, "ingestedAt": started_at},
        }
        run["manifestHash"] = compute_run_manifest_hash(run)
        return run, []

    if eligibility_status == RESEARCH_ONLY_NO_DECISION:
        # Honest, non-fatal, non-"rejected" outcome: this manifest never
        # had an authoritative decision to replay in the first place (see
        # assess_replay_eligibility's rule 3). Deliberately its own
        # runStatus, not RUN_STATUS_REJECTED_INELIGIBLE -- nothing here
        # failed or was ineligible; a decision simply never existed for
        # this run type.
        run = {
            **base_fields,
            "replayRunId": ids.build_replay_run_id(manifest.get("snapshotId"), replay_mode, candidate_commit_sha or "", REPLAY_FRAMEWORK_VERSION),
            "replayFidelity": snap.LEVEL_1_APPROXIMATE,
            "completedAt": ids.utc_now_iso(),
            "runStatus": RUN_STATUS_NOT_APPLICABLE_NO_DECISION,
            "limitationReasons": sorted(set(limitation_reasons)),
            "summary": {"marketsEvaluated": 0, "marketsComparable": 0, "decisionsChanged": 0,
                        "settledResolved": 0, "settledUnresolved": 0, "clvResolved": 0},
            "performance": None,
            "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": None,
                           "capturedAt": started_at, "ingestedAt": started_at},
        }
        run["manifestHash"] = compute_run_manifest_hash(run)
        return run, []

    if eligibility_status not in ELIGIBLE_STATUSES or (eligibility_status == ELIGIBLE_LEVEL_1_ONLY and not allow_level_1):
        run = {
            **base_fields,
            "replayRunId": ids.build_replay_run_id(manifest.get("snapshotId"), replay_mode, candidate_commit_sha or "", REPLAY_FRAMEWORK_VERSION),
            "replayFidelity": snap.LEVEL_1_APPROXIMATE,
            "completedAt": ids.utc_now_iso(),
            "runStatus": RUN_STATUS_REJECTED_INELIGIBLE,
            "limitationReasons": sorted(set(limitation_reasons)),
            "summary": {"marketsEvaluated": 0, "marketsComparable": 0, "decisionsChanged": 0,
                        "settledResolved": 0, "settledUnresolved": 0, "clvResolved": 0},
            "performance": None,
            "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": None,
                           "capturedAt": started_at, "ingestedAt": started_at},
        }
        run["manifestHash"] = compute_run_manifest_hash(run)
        return run, []

    if eligibility_status == ELIGIBLE_LEVEL_2 and allow_level_1:
        # allow_level_1 requested but the snapshot is actually fully
        # eligible -- proceed at the higher fidelity honestly achieved,
        # never downgrade just because the caller asked for permission
        # to run at a lower one.
        pass

    replay_fidelity = derive_replay_fidelity_from_eligibility(
        eligibility_status, manifest.get("productionCommitSha"), candidate_commit_sha,
    )

    try:
        execution = run_candidate_replay(manifest)
    except ReplayError as e:
        run = {
            **base_fields,
            "replayRunId": ids.build_replay_run_id(manifest.get("snapshotId"), replay_mode, candidate_commit_sha or "", REPLAY_FRAMEWORK_VERSION),
            "replayFidelity": snap.LEVEL_1_APPROXIMATE,
            "completedAt": ids.utc_now_iso(),
            "runStatus": RUN_STATUS_FAILED,
            "limitationReasons": sorted(set(limitation_reasons + [f"EXECUTION_ERROR: {e}"])),
            "summary": {"marketsEvaluated": 0, "marketsComparable": 0, "decisionsChanged": 0,
                        "settledResolved": 0, "settledUnresolved": 0, "clvResolved": 0},
            "performance": None,
            "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": None,
                           "capturedAt": started_at, "ingestedAt": started_at},
        }
        run["manifestHash"] = compute_run_manifest_hash(run)
        return run, []

    replay_run_id = ids.build_replay_run_id(manifest.get("snapshotId"), replay_mode, candidate_commit_sha or "", REPLAY_FRAMEWORK_VERSION)

    settlement_rows, clv_rows, linkage_unavailable_reason = _linked_settlement_and_clv(manifest)
    if linkage_unavailable_reason:
        limitation_reasons.append(f"SETTLEMENT_CLV_UNAVAILABLE: {linkage_unavailable_reason}")
    settlement_by_ticker = {r.get("marketTicker"): r for r in (settlement_rows or []) if r.get("marketTicker")}
    clv_by_ticker, ambiguous_clv_tickers = _closing_clv_by_ticker(clv_rows or [])
    if ambiguous_clv_tickers:
        limitation_reasons.append(f"CLV_AMBIGUOUS_CLOSING_QUOTE_FOR_{len(ambiguous_clv_tickers)}_MARKETS")

    replayed_risk_candidates = {
        (c.get("game"), c.get("market")): c for c in execution["replayedRiskGatePayload"].get("candidates", [])
    }
    original_risk_candidates = {
        (c.get("game"), c.get("market")): c for c in execution["originalRiskGatePayload"].get("candidates", [])
    }

    results = []
    markets_evaluated = 0
    markets_comparable = 0
    decisions_changed = 0
    settled_resolved = 0
    settled_unresolved = 0
    clv_resolved = 0
    resolved_for_scoring = []

    for g in execution["replayedGames"]:
        game_id = g.get("gameId")
        away = (g.get("away") or {}).get("abbr", "")
        home = (g.get("home") or {}).get("abbr", "")
        game_label = f"{away}@{home}"
        original_game = execution["originalGamesById"].get(game_id) or {}
        original_rows_by_market = {row.get("market"): row for row in (original_game.get("marketLedger") or [])}

        for replayed_row in g.get("marketLedger") or []:
            markets_evaluated += 1
            market_name = replayed_row.get("market")
            original_row = original_rows_by_market.get(market_name)
            replay_candidate = replayed_risk_candidates.get((game_label, market_name))
            original_candidate = original_risk_candidates.get((game_label, market_name))

            classification, change_reasons = classify_comparison(original_row, replayed_row, original_candidate, replay_candidate)
            comparable = classification not in (CMP_NOT_COMPARABLE, CMP_ORIGINAL_DATA_MISSING)
            changed = classification not in (CMP_UNCHANGED, CMP_NOT_COMPARABLE, CMP_ORIGINAL_DATA_MISSING)
            if comparable:
                markets_comparable += 1
            if changed:
                decisions_changed += 1

            ticker = replayed_row.get("ticker") or replayed_row.get("marketTicker")
            # Maintainer review finding (item 8): the CLV entry price must be
            # the EXECUTABLE price a real bet would have entered at
            # (executableMarketProb, derived from executablePriceUsed --
            # yes_ask/no_ask), not kalshiVF (the vig-free MIDPOINT). Falls
            # back to kalshiVF only when no executable price exists on the
            # row at all (e.g. no registry ask -- see executable_ask_price_cents's
            # documented American-odds fallback), so CLV is never silently
            # unresolved merely because the sharper field is absent.
            entry_implied_pct = replayed_row.get("executableMarketProb")
            if entry_implied_pct is None:
                entry_implied_pct = replayed_row.get("kalshiVF")
            settlement_linkage = _settlement_linkage_for_ticker(settlement_by_ticker, linkage_unavailable_reason, ticker)
            clv_linkage = _clv_linkage_for_ticker(clv_by_ticker, linkage_unavailable_reason, ticker, entry_implied_pct)
            if settlement_linkage["status"] == "RESOLVED":
                settled_resolved += 1
                replay_prob = replayed_row.get("modelProb")
                if replay_prob is not None and settlement_linkage["result"] in ("YES", "NO"):
                    outcome = 1 if settlement_linkage["result"] == "YES" else 0
                    resolved_for_scoring.append({"probability": replay_prob / 100.0, "outcome": outcome})
            else:
                settled_unresolved += 1
            if clv_linkage["status"] == "RESOLVED":
                clv_resolved += 1

            comparison_metadata = {"gameLabel": game_label}
            if changed:
                mismatch_reason, mismatch_evidence = classify_mismatch_reason(original_row, market_name, limitation_reasons)
                comparison_metadata["mismatchReason"] = mismatch_reason
                comparison_metadata["mismatchEvidence"] = mismatch_evidence

            market_key = _market_key(replayed_row)
            result = {
                "schemaVersion": "1",
                "replayResultId": ids.build_replay_result_id(replay_run_id, game_id, market_key),
                "replayRunId": replay_run_id,
                "gameId": game_id,
                "marketTicker": ticker,
                "marketFamily": ticker.split("-", 1)[0] if ticker else None,
                "selection": market_name,
                "side": None,
                "threshold": replayed_row.get("line"),
                "originalModelProbability": original_row.get("modelProb") if original_row else None,
                "replayedModelProbability": replayed_row.get("modelProb"),
                "originalMarketPrice": original_row.get("kalshiVF") if original_row else None,
                "replayedMarketPrice": replayed_row.get("kalshiVF"),
                # Maintainer review finding (item 8): originalMarketPrice/
                # replayedMarketPrice above are the vig-free MIDPOINT
                # (kalshiVF), NOT the executable price that actually
                # determined edge/recommendation (edgeUsedForQualification
                # is calibratedEdgeVsExecutable, derived from
                # executablePriceUsed/executableMarketProb). These two
                # fields are copied VERBATIM from the row -- original from
                # the frozen RECOMMENDATION_OUTPUT row exactly as production
                # wrote it, replayed from the freshly re-evaluated row built
                # from the same frozen NORMALIZED_SLATE odds -- never a
                # later registry snapshot, closing price, last trade, or a
                # reconstructed complement. Null when the row never had a
                # real ask (no registry price + no American-odds fallback
                # available at capture time) -- never silently substituted.
                "originalExecutablePriceUsed": original_row.get("executablePriceUsed") if original_row else None,
                "replayedExecutablePriceUsed": replayed_row.get("executablePriceUsed"),
                "originalExecutableMarketProb": original_row.get("executableMarketProb") if original_row else None,
                "replayedExecutableMarketProb": replayed_row.get("executableMarketProb"),
                "originalEdge": _row_edge(original_row),
                "replayedEdge": _row_edge(replayed_row),
                "originalRecommendationStatus": (original_candidate or {}).get("status"),
                "replayedRecommendationStatus": (replay_candidate or {}).get("status"),
                "originalTier": (original_candidate or {}).get("tier"),
                "replayedTier": (replay_candidate or {}).get("tier"),
                "originalPassReason": (original_candidate or {}).get("rejectionReason"),
                "replayedPassReason": (replay_candidate or {}).get("rejectionReason"),
                "originalPreferredExpression": None,
                "replayedPreferredExpression": None,
                "changedDecision": changed,
                "changeReasons": change_reasons,
                "comparisonClassification": classification,
                "settlementLinkage": settlement_linkage,
                "clvLinkage": clv_linkage,
                "comparisonMetadata": comparison_metadata,
                "validationStatus": "valid",
                "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": market_key,
                               "capturedAt": started_at, "ingestedAt": ids.utc_now_iso()},
            }
            results.append(result)

    performance = score_resolved_results(resolved_for_scoring)

    run = {
        **base_fields,
        "replayRunId": replay_run_id,
        "replayFidelity": replay_fidelity,
        "completedAt": ids.utc_now_iso(),
        "runStatus": RUN_STATUS_COMPLETED,
        "limitationReasons": sorted(set(limitation_reasons)),
        "summary": {
            "marketsEvaluated": markets_evaluated, "marketsComparable": markets_comparable,
            "decisionsChanged": decisions_changed, "settledResolved": settled_resolved,
            "settledUnresolved": settled_unresolved, "clvResolved": clv_resolved,
        },
        "performance": performance,
        "provenance": {"sourceSystem": "replay_engine", "sourceFile": None, "sourceKey": None,
                       "capturedAt": started_at, "ingestedAt": started_at},
    }
    run["manifestHash"] = compute_run_manifest_hash(run)
    return run, results
