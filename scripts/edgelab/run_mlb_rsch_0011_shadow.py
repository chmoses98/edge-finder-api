#!/usr/bin/env python3
"""
scripts/edgelab/run_mlb_rsch_0011_shadow.py
====================================================================
Research Lab experiment MLB-RSCH-0011: "Prospective Negative-Binomial
Shadow". RESEARCH ONLY -- no production probability, recommendation,
edge, confidence, Bet Up To, stake, bankroll, or slate output field is
ever read or written by this script.

Tests whether MLB-RSCH-0010's historical finding (the frozen D1
negative-binomial distribution beats Poisson on RSCH-0009's research
proxy mean model) TRANSFERS when applied to PRODUCTION's own real,
currently-live expected-run means (scripts.build_market_ledger.
compute_game_projection_context, imported and called UNCHANGED -- this
script never reimplements or approximates it).

THREE DISTINCT PIECES this script runs, each labeled by its own true
evidence provenance (never conflated):

  1. REGISTRATION -- registers this experiment's control (production's
     real mean model + its existing Poisson layer), candidate (the SAME
     means + MLB-RSCH-0010's frozen NB distribution), and experiment
     definition, all BEFORE any real result is inspected.

  2. REPLAY (run_replay) -- an honestly-labeled, LOWER-evidence-tier
     analysis over EXISTING once-daily PRE_GAME_DECISION snapshots. The
     awayProjRuns/homeProjRuns used were genuinely captured
     prospectively (frozen at real production decision time, see
     lib/edgelab/snapshot.py), but the CANDIDATE's own probability is
     computed NOW, by this script run -- so this is explicitly NOT E4
     evidence, and is kept entirely separate from this experiment's own
     new capture (item 3). See run_replay's own docstring.

  3. PROSPECTIVE E4 SCORING (score_prospective_shadow) -- scores
     whatever real records lib.edgelab.shadow_distribution's capture
     step (wired into scripts/edgelab/run_prospective_snapshots.py,
     MLB-RSCH-0011's own capture mechanism, running on the existing
     15-minute model-snapshot-scheduler.yml cron) has ACTUALLY written
     to data/edgelab/mlb_rsch_0011_shadow_evaluations/ and that have
     since settled (a real final score is known). This IS this
     experiment's genuine E4_PROSPECTIVE_SHADOW evidence, and is the
     ONLY thing this script's classification (see classify_shadow_
     evidence) is based on. Grows automatically as future scheduled
     cron cycles accumulate more captured/settled games -- this script
     can be re-run at any time to pick up more sample, with NO code
     change required.

Also runs a research-only current-slate smoke test (item 4) and prints
a deterministic shadow-status report (item 5) -- see main().
"""
import glob
import gzip
import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
_EDGELAB_SCRIPTS_DIR = os.path.join(_SCRIPTS_DIR, "edgelab")
if _EDGELAB_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _EDGELAB_SCRIPTS_DIR)

from lib.edgelab import candidate_identity as cand_id
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import evidence_levels as ev
from lib.edgelab import experiment_registry as reg
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab import shadow_distribution as sd
from lib.edgelab import storage
from lib.edgelab.research_stats import independent_unit_count, sample_size_status

import run_distribution_calibration_experiment as rsch0010  # noqa: E402
from build_market_ledger import compute_game_projection_context  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0011"
REGISTRATION_TIMESTAMP = "2026-08-28T23:00:00Z"

# Preregistered promotion/directional sample thresholds -- fixed here,
# before any prospective result is observed, never lowered later. 300
# independent games is a real, attainable-within-a-season bar (roughly
# 2-3 weeks of full MLB slates) that comfortably clears
# lib.edgelab.calibration's own n>=100 CALIBRATED floor while still
# requiring a meaningfully large out-of-sample prospective run, not a
# handful of early games.
MIN_INDEPENDENT_GAMES_FOR_PROMOTION = 300
MIN_INDEPENDENT_GAMES_FOR_DIRECTIONAL = 30

SNAPSHOTS_ROOT = os.path.join("data", "edgelab", "snapshots")
PRE_GAME_DECISION_STAGE_DIR = "pre_game_decision"
SHADOW_ENTITY = "mlb_rsch_0011_shadow_evaluations"

CLASSIFICATION_NO_EVIDENCE_YET = "SHADOW_STARTED_NO_EVIDENCE_YET"
CLASSIFICATION_EARLY_DIRECTIONAL = "SHADOW_EARLY_DIRECTIONAL"
CLASSIFICATION_REINFORCED = "SHADOW_CANDIDATE_REINFORCED"
CLASSIFICATION_WEAKENED = "SHADOW_CANDIDATE_WEAKENED"

REPLAY_EVIDENCE_LABEL = "PRE_SHADOW_REPLAY_NOT_E4"


# ── Registration ─────────────────────────────────────────────────────────

def _current_git_commit_sha():
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def register_experiment():
    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0011_production_poisson_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0011 control: production scripts.build_market_ledger.compute_game_projection_context "
                        "(the REAL, currently-live expected-run mean model) + production's existing independent-Poisson "
                        "probability layer (p_team_wins/p_over_total), both UNMODIFIED"
        ),
        probability_adapter_identity="scripts.build_market_ledger.p_team_wins;p_over_total (production, unmodified)",
        model_engine_family="production_pipeline_poisson_v1",
        required_input_provenance=["model_evaluation_probability_prospective_snapshot"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "Production's REAL, currently-live expected-run mean model -- deliberately NOT "
            "MLB-RSCH-0009/0010's research proxy mean model. This is the true current-production "
            "control MLB-RSCH-0011 tests transfer against: does MLB-RSCH-0010's historical distribution "
            "finding survive contact with the actual production mean model, not just the research proxy?"
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    candidate = cand_id.build_candidate_registration(
        name="mlb_rsch_0011_production_mean_plus_nb_0010_v1",
        base_control_model_id=control["controlModelId"],
        change_description=(
            "Replace the control's independent-Poisson run-scoring distribution with MLB-RSCH-0010's "
            "frozen winning D1 independent negative-binomial distribution (dispersion=0.281513), holding "
            "the SAME production expected-run means fixed -- a pure distribution-layer ablation, never a "
            "mean-model change."
        ),
        change_type=cand_id.CHANGE_TYPE_DISTRIBUTION_CHANGE,
        implementation_ref="lib.edgelab.shadow_distribution.compute_paired_probabilities",
        description=(
            "MLB-RSCH-0011's shadow candidate: production's real current expected-run means, "
            "MLB-RSCH-0010's frozen negative-binomial distribution layer on top. Frozen at registration "
            f"time -- dispersion={sd.FROZEN_DISPERSION}, never refit against prospective results."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    cand_id.register_candidate(candidate)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Prospective Negative-Binomial Shadow",
        hypothesis=(
            "MLB-RSCH-0010's historical finding (the frozen negative-binomial run-scoring distribution "
            "beats Poisson on a research-proxy mean model) TRANSFERS to production's REAL, currently-live "
            "expected-run mean model when evaluated prospectively, with control and candidate receiving "
            "IDENTICAL means -- only the distribution layer differs."
        ),
        research_question=(
            "Holding production's actual expected-run means fixed and identical between control and "
            "candidate, does MLB-RSCH-0010's frozen negative-binomial distribution (dispersion=0.281513) "
            "produce a repeatable, prospective out-of-sample probability improvement over production's "
            "own Poisson layer, for game total / team total (primary) and moneyline / run margin "
            "(secondary)?"
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        candidate_variant_id=candidate["candidateVariantId"],
        evidence_level=ev.E4_PROSPECTIVE_SHADOW,
        target_population=(
            "Every MLB game lib.edgelab.prospective_snapshot's prospective-snapshot cycle evaluates going "
            "forward (5 core checkpoints: T_MINUS_90/60/30, LINEUP_CONFIRMATION, MODEL_CLOSING_WINDOW), "
            "for which compute_game_projection_context produced non-null, positive awayProjRuns AND "
            "homeProjRuns."
        ),
        market_families=["game_total", "team_total", "moneyline", "run_line_margin"],
        eligibility_criteria=[
            "compute_game_projection_context produced non-null, positive awayProjRuns AND homeProjRuns for this game/checkpoint",
            "the game later settles with a known final score (for scoring against outcomes -- capture itself does not require this)",
        ],
        exclusion_criteria=[
            "F3/F5/F7 (any sub-full-game horizon) -- MLB-RSCH-0010's dispersion was fit on full-game team runs only, never extrapolated to a shortened horizon",
            "NRFI/YRFI -- inning-level scoring requires its own dispersion research; not assumed transferable from the full-game parameter",
        ],
        prediction_checkpoints=["T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "LINEUP_CONFIRMATION", "MODEL_CLOSING_WINDOW"],
        primary_metric=(
            "mean Brier across {game_total_over@7.5/8.5/9.5/10.5, team_total_away_over@2.5/3.5/4.5/5.5, "
            "team_total_home_over@2.5/3.5/4.5/5.5} vs control (candidate minus control), game-clustered 95% CI"
        ),
        secondary_metrics=[
            "moneyline Brier/log-loss delta", "run-margin (win-by-N+/lose-by-N+) Brier delta",
            "tail calibration (team shutout, team 10+ runs, game 15+ total runs, 5+/7+ run margin)",
            "Kalshi fair-probability/executable-price/hypothetical-edge comparison -- DEFERRED this pass, see report section 21",
            "Pinnacle comparison on the existing MLB-RSCH-0008/0009 matched sample -- DEFERRED this pass, see report section 22",
        ],
        chronological_split_policy=(
            "PROSPECTIVE_ONLY: every E4 observation this experiment counts is captured going forward from "
            "REGISTRATION_TIMESTAMP; no historical refitting, no re-use of pre-registration data for "
            "candidate selection or dispersion fitting."
        ),
        minimum_sample_requirement={"independentGames": MIN_INDEPENDENT_GAMES_FOR_PROMOTION},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={
            "model_evaluation_probability_prospective_snapshot": "PREDICTIVE_INPUT",
            "settlement_outcome": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            f"FROZEN CANDIDATE: dispersion={sd.FROZEN_DISPERSION} (verified against "
            "data/edgelab/analytics/latest_mlb_rsch_0010_run_distribution.json at registration time) -- "
            "never refit against prospective results. A separate, LOWER-evidence-tier replay analysis "
            f"({REPLAY_EVIDENCE_LABEL}) over existing PRE_GAME_DECISION daily snapshots is reported "
            "alongside this experiment's own genuine E4 evidence for context, but is explicitly NOT "
            "counted toward minimumSampleRequirement or any promotion criterion -- see run_replay's own "
            "docstring and the MLB-RSCH-0011 report section 8/17."
        ),
    )
    reg.register_experiment(definition)
    return control, candidate, definition


# ── Shared outcome derivation (used by both the replay and E4 scoring) ────

def _outcomes_for_actual(actual_home, actual_away):
    """{cellKey: 0/1} ground truth for every cell sd.compute_paired_probabilities computes, given one game's real final score. Pure."""
    actual_total = actual_home + actual_away
    outcomes = {
        f"{sd.FAMILY_MONEYLINE}_home_win": 1 if actual_home > actual_away else 0,
        f"{sd.FAMILY_MONEYLINE}_away_win": 1 if actual_away > actual_home else 0,
    }
    for line in sd.GAME_TOTAL_LINES:
        outcomes[f"{sd.FAMILY_GAME_TOTAL}_over_{line}"] = 1 if actual_total > line else 0
    for line in sd.TEAM_TOTAL_LINES:
        outcomes[f"{sd.FAMILY_TEAM_TOTAL_AWAY}_over_{line}"] = 1 if actual_away > line else 0
        outcomes[f"{sd.FAMILY_TEAM_TOTAL_HOME}_over_{line}"] = 1 if actual_home > line else 0
    for margin in sd.MARGIN_THRESHOLDS:
        outcomes[f"{sd.FAMILY_RUN_MARGIN}_win_by_at_least_{margin}"] = 1 if (actual_home - actual_away) >= margin else 0
        outcomes[f"{sd.FAMILY_RUN_MARGIN}_lose_by_at_least_{margin}"] = 1 if (actual_away - actual_home) >= margin else 0
    return outcomes


def _score_cells(cells_by_game, actual_runs_by_game_id, game_date_by_game_id=None):
    """
    Shared scoring core: `cells_by_game` = [(gameId, {cellKey: {"control","candidate"}})].
    Builds paired control/candidate row lists keyed by (gameId, cellKey),
    evaluates overall + per-family via lib.edgelab.paired_evaluation
    (reused, never reimplemented). Skips any game with no resolvable
    real final score -- never fabricates an outcome.
    """
    game_date_by_game_id = game_date_by_game_id or {}
    control_rows, candidate_rows, per_game = [], [], []

    for game_id, cells in cells_by_game:
        try:
            actual = actual_runs_by_game_id.get(int(game_id))
        except (TypeError, ValueError):
            actual = None
        if not actual or actual[0] is None or actual[1] is None:
            continue
        actual_home, actual_away = actual
        outcomes = _outcomes_for_actual(actual_home, actual_away)
        game_date = game_date_by_game_id.get(game_id)
        for cell_key, pair in cells.items():
            outcome = outcomes.get(cell_key)
            if outcome is None:
                continue
            control_rows.append({"gameId": game_id, "cellKey": cell_key, "gameDate": game_date, "modelFairProbability": pair["control"], "outcome": outcome})
            candidate_rows.append({"gameId": game_id, "cellKey": cell_key, "gameDate": game_date, "modelFairProbability": pair["candidate"], "outcome": outcome})
        per_game.append({"gameId": game_id, "actualHomeRuns": actual_home, "actualAwayRuns": actual_away})

    def key_fn(row):
        return (row["gameId"], row["cellKey"])

    pairing = pe.pair_eligible_observations(control_rows, candidate_rows, key_fn=key_fn)
    overall = pe.evaluate_probability_model_pair(pairing, game_key="gameId", date_key="gameDate")

    by_family = {}
    for family_prefix in sd.PRIMARY_FAMILIES + sd.SECONDARY_FAMILIES:
        fam_control = [r for r in control_rows if r["cellKey"].startswith(family_prefix)]
        fam_candidate = [r for r in candidate_rows if r["cellKey"].startswith(family_prefix)]
        fam_pairing = pe.pair_eligible_observations(fam_control, fam_candidate, key_fn=key_fn)
        by_family[family_prefix] = pe.evaluate_probability_model_pair(fam_pairing, game_key="gameId", date_key="gameDate")

    return {
        "independentGames": independent_unit_count(per_game, key="gameId"),
        "perGameSample": per_game,
        "overall": overall,
        "byFamily": by_family,
    }


# ── (2) REPLAY over existing PRE_GAME_DECISION daily snapshots ────────────

def discover_pre_game_decision_snapshots():
    """[(date, runKey, manifestPath), ...] for every PRE_GAME_DECISION snapshot on disk, sorted chronologically."""
    out = []
    if not os.path.isdir(SNAPSHOTS_ROOT):
        return out
    for date in sorted(os.listdir(SNAPSHOTS_ROOT)):
        stage_dir = os.path.join(SNAPSHOTS_ROOT, date, PRE_GAME_DECISION_STAGE_DIR)
        if not os.path.isdir(stage_dir):
            continue
        for run_key in sorted(os.listdir(stage_dir)):
            manifest_path = os.path.join(stage_dir, run_key, "manifest.json")
            if os.path.exists(manifest_path):
                out.append((date, run_key, manifest_path))
    return out


def _load_raw_projections(manifest_path):
    """Returns (games, error). `games` is raw_projections.json.gz's own data.games list (the genuinely prospectively-frozen production means for that day) -- None with an explicit reason if the component isn't AVAILABLE or the frozen file is missing."""
    with open(manifest_path) as f:
        manifest = json.load(f)
    for component in manifest.get("components", []):
        if component.get("componentType") != "RAW_PROJECTIONS":
            continue
        if component.get("availabilityStatus") != "AVAILABLE":
            return None, f"RAW_PROJECTIONS availabilityStatus={component.get('availabilityStatus')!r}"
        snapshot_path = component.get("snapshotPath")
        if not snapshot_path or not os.path.exists(snapshot_path):
            return None, "RAW_PROJECTIONS component AVAILABLE but frozen file missing on disk"
        opener = gzip.open if snapshot_path.endswith(".gz") else open
        with opener(snapshot_path, "rt") as f:
            payload = json.load(f)
        return (payload.get("data") or {}).get("games") or [], None
    return None, "manifest has no RAW_PROJECTIONS component"


def build_2026_actual_runs_by_game_id():
    """
    {gamePk (int): (actualHomeRuns, actualAwayRuns)} for the 2026 season,
    reusing MLB-RSCH-0010's own build_rows_with_frozen_lambdas()
    UNCHANGED (which itself reuses MLB-RSCH-0009's own team-schedule
    corpus loader) -- never re-fetched or re-derived independently. Only
    the gamePk/actualHomeRuns/actualAwayRuns fields are used; the
    proxy-model lambdaHome/lambdaAway fields on those rows are ignored
    entirely (MLB-RSCH-0011 uses production's OWN means, never the
    research proxy).
    """
    rows_by_season = rsch0010.build_rows_with_frozen_lambdas()[0]
    lookup = {}
    for r in rows_by_season.get(2026, []):
        game_pk = r.get("gamePk")
        if game_pk is not None:
            lookup[int(game_pk)] = (r.get("actualHomeRuns"), r.get("actualAwayRuns"))
    return lookup


def run_replay():
    """
    NOT E4 evidence -- see module docstring item 2. Uses ONLY
    awayProjRuns/homeProjRuns actually captured prospectively (frozen
    inside each real PRE_GAME_DECISION snapshot at genuine production
    decision time), matched against MLB-RSCH-0010's own 2026 actual-runs
    corpus. Never recomputes/reconstructs a mean from later data.
    """
    actual_runs_by_game_id = build_2026_actual_runs_by_game_id()
    snapshots = discover_pre_game_decision_snapshots()

    cells_by_game = []
    game_date_by_game_id = {}
    seen_game_ids = set()
    skipped_snapshots = []
    skipped_games = 0

    for date, run_key, manifest_path in snapshots:
        games, err = _load_raw_projections(manifest_path)
        if err:
            skipped_snapshots.append({"date": date, "runKey": run_key, "reason": err})
            continue
        for g in games:
            game_id = g.get("gameId")
            away_proj, home_proj = g.get("awayProjRuns"), g.get("homeProjRuns")
            if game_id is None or game_id in seen_game_ids:
                continue
            if away_proj is None or home_proj is None or away_proj <= 0 or home_proj <= 0:
                skipped_games += 1
                continue
            try:
                cells = sd.compute_paired_probabilities(away_proj, home_proj)
            except ValueError:
                skipped_games += 1
                continue
            seen_game_ids.add(game_id)
            game_date_by_game_id[game_id] = date
            cells_by_game.append((game_id, cells))

    result = _score_cells(cells_by_game, actual_runs_by_game_id, game_date_by_game_id)
    result.update({
        "evidenceLabel": REPLAY_EVIDENCE_LABEL,
        "snapshotsDiscovered": len(snapshots),
        "snapshotsSkipped": skipped_snapshots,
        "gamesSkippedForMissingOrInvalidProjections": skipped_games,
        "gamesConsideredBeforeOutcomeJoin": len(cells_by_game),
    })
    return result


# ── (3) Score the experiment's OWN genuine E4 prospective capture ─────────

def discover_shadow_files():
    root = os.path.join("data", "edgelab", SHADOW_ENTITY)
    if not os.path.isdir(root):
        return []
    return sorted(glob.glob(os.path.join(root, "*.jsonl")) + glob.glob(os.path.join(root, "*.jsonl.gz")))


def load_prospective_shadow_records():
    records = []
    for path in discover_shadow_files():
        records.extend(storage.read_records(path))
    return records


def score_prospective_shadow(actual_runs_by_game_id=None):
    """
    This experiment's REAL E4_PROSPECTIVE_SHADOW evidence: scores every
    computationStatus=SUCCESS record lib.edgelab.shadow_distribution's
    capture step has actually written, for every game that has since
    settled with a known real final score. A game the capture mechanism
    recorded but that hasn't been played/settled yet contributes nothing
    (never a fabricated outcome) -- this function can be re-run at any
    later time to pick up more settled sample automatically, with no
    code change.
    """
    actual_runs_by_game_id = actual_runs_by_game_id if actual_runs_by_game_id is not None else build_2026_actual_runs_by_game_id()
    records = load_prospective_shadow_records()
    success_records = [r for r in records if r.get("computationStatus") == sd.STATUS_SUCCESS]
    failed_records = [r for r in records if r.get("computationStatus") == sd.STATUS_FAILED]

    cells_by_game = [(r["gameId"], r["cells"]) for r in success_records if r.get("cells")]
    game_date_by_game_id = {r["gameId"]: (r.get("capturedAt") or "")[:10] for r in success_records}

    result = _score_cells(cells_by_game, actual_runs_by_game_id, game_date_by_game_id)
    captured_game_ids = {r["gameId"] for r in success_records}
    result.update({
        "evidenceLabel": "E4_PROSPECTIVE_SHADOW",
        "totalCapturedRecords": len(records),
        "successfulCaptureRecords": len(success_records),
        "failedCaptureRecords": len(failed_records),
        "capturedGamesAwaitingSettlement": len(captured_game_ids) - result["independentGames"],
    })
    return result


# ── (4) Current-slate smoke test ───────────────────────────────────────────

def run_current_slate_smoke_test(slate_path="data/slate.json"):
    """
    RESEARCH ONLY. Reuses compute_game_projection_context (production's
    real function, unmodified) against whatever data/slate.json holds
    right now, and reports a Game/ExpectedRuns/Poisson/NB/Difference
    table for the game-total market. Never recommends a wager from the
    difference. Returns None if data/slate.json is unavailable.
    """
    if not os.path.exists(slate_path):
        return None
    with open(slate_path) as f:
        slate = json.load(f)
    games = slate.get("games") or []
    examples = []
    for g in games:
        try:
            ctx = compute_game_projection_context(g)
        except Exception as exc:
            examples.append({"gameId": g.get("gameId"), "status": "FAILED", "reason": str(exc)})
            continue
        away_proj, home_proj = ctx.get("awayProjRuns"), ctx.get("homeProjRuns")
        if away_proj is None or home_proj is None or away_proj <= 0 or home_proj <= 0:
            examples.append({"gameId": g.get("gameId"), "status": "UNSUPPORTED", "reason": "missing/non-positive projection means"})
            continue
        try:
            cells = sd.compute_paired_probabilities(away_proj, home_proj)
        except ValueError as exc:
            examples.append({"gameId": g.get("gameId"), "status": "UNSUPPORTED", "reason": str(exc)})
            continue
        away_abbr, home_abbr = (g.get("away") or {}).get("abbr"), (g.get("home") or {}).get("abbr")
        examples.append({
            "gameId": g.get("gameId"), "status": "OK", "game": f"{away_abbr}@{home_abbr}",
            "awayProjRuns": away_proj, "homeProjRuns": home_proj, "totalProj": ctx.get("totalProj"),
            "gameTotalCells": {k: v for k, v in cells.items() if k.startswith(sd.FAMILY_GAME_TOTAL)},
        })
    return {"slateDate": slate.get("date"), "gamesConsidered": len(games), "examples": examples}


# ── (5) Classification + report assembly ────────────────────────────────────

def classify_shadow_evidence(prospective_independent_games, prospective_primary_delta):
    """Pure. Based ONLY on this experiment's own genuine E4 sample -- the replay never counts here."""
    if prospective_independent_games == 0 or prospective_primary_delta is None:
        return CLASSIFICATION_NO_EVIDENCE_YET
    if prospective_independent_games < MIN_INDEPENDENT_GAMES_FOR_DIRECTIONAL:
        return CLASSIFICATION_EARLY_DIRECTIONAL
    return CLASSIFICATION_REINFORCED if prospective_primary_delta < 0 else CLASSIFICATION_WEAKENED


def _primary_aggregate_delta(by_family_result):
    deltas = [
        by_family_result[fam]["pairedDelta"]["brierScore"]
        for fam in sd.PRIMARY_FAMILIES
        if by_family_result.get(fam) and by_family_result[fam]["pairedDelta"]["brierScore"] is not None
    ]
    return round(sum(deltas) / len(deltas), 6) if deltas else None


def main():
    print(f"[{EXPERIMENT_ID}] registering experiment/control/candidate...")
    control, candidate, definition = register_experiment()
    print(f"[{EXPERIMENT_ID}] controlModelId={control['controlModelId']} candidateVariantId={candidate['candidateVariantId']}")

    print(f"[{EXPERIMENT_ID}] running replay over existing PRE_GAME_DECISION snapshots (NOT E4 evidence)...")
    replay = run_replay()
    print(f"[{EXPERIMENT_ID}] replay: independentGames={replay['independentGames']} snapshotsDiscovered={replay['snapshotsDiscovered']}")

    print(f"[{EXPERIMENT_ID}] scoring this experiment's own genuine E4 prospective capture...")
    actual_runs = build_2026_actual_runs_by_game_id()
    prospective = score_prospective_shadow(actual_runs)
    print(f"[{EXPERIMENT_ID}] prospective E4: independentGames={prospective['independentGames']} capturedRecords={prospective['totalCapturedRecords']}")

    print(f"[{EXPERIMENT_ID}] running current-slate smoke test (research only, no wager recommendation)...")
    smoke_test = run_current_slate_smoke_test()

    primary_delta = _primary_aggregate_delta(prospective["byFamily"])
    classification = classify_shadow_evidence(prospective["independentGames"], primary_delta)
    print(f"[{EXPERIMENT_ID}] classification={classification} primaryDelta={primary_delta}")

    import datetime
    report = {
        "experimentId": EXPERIMENT_ID,
        "generatedAt": datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "controlModelId": control["controlModelId"],
        "candidateVariantId": candidate["candidateVariantId"],
        "frozenDispersion": sd.FROZEN_DISPERSION,
        "candidateVersion": sd.CANDIDATE_VERSION,
        "familiesSupported": {
            "primary": list(sd.PRIMARY_FAMILIES),
            "secondary": list(sd.SECONDARY_FAMILIES),
        },
        "familiesUnsupported": {
            "horizons": list(sd.UNSUPPORTED_HORIZONS),
            "reason": "MLB-RSCH-0010's dispersion parameter was fit exclusively on full-game team-run counts; extrapolating it to a sub-full-game horizon (F3/F5/F7) or inning-level scoring (NRFI/YRFI) without separate research would violate this milestone's own no-extrapolation instruction.",
        },
        "preregistration": {
            "minimumSampleForPromotion": MIN_INDEPENDENT_GAMES_FOR_PROMOTION,
            "minimumSampleForDirectional": MIN_INDEPENDENT_GAMES_FOR_DIRECTIONAL,
            "primaryMetric": definition["primaryMetric"],
        },
        "replay": replay,
        "prospectiveE4": prospective,
        "currentSlateSmokeTest": smoke_test,
        "classification": classification,
        "classificationBasis": "prospectiveE4 ONLY -- the replay is reported for context and is explicitly excluded from this classification and from minimumSampleRequirement.",
    }

    out_path = os.path.join("data", "edgelab", "analytics", "latest_mlb_rsch_0011_shadow_status.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)
        f.write("\n")
    print(f"[{EXPERIMENT_ID}] wrote {out_path}")

    return report


if __name__ == "__main__":
    main()
