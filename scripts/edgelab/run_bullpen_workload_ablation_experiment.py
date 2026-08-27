#!/usr/bin/env python3
"""
scripts/edgelab/run_bullpen_workload_ablation_experiment.py
================================================================
Research Lab, experiment MLB-RSCH-0002: BULLPEN WORKLOAD ABLATION.

RESEARCH ONLY. Imports and calls scripts/build_market_ledger.py's
compute_projections()/p_team_wins()/p_over_total() and
lib/edgelab/bullpen_availability.py's compute_bullpen_workload_adjustment()
UNCHANGED -- never modifies, monkeypatches, or reimplements any of them.
Every difference between CONTROL and CANDIDATE below is produced by
varying compute_projections()'s INPUT dict (`g`), never its code.

QUESTION
-----------
Does the current production bullpen recent-workload adjustment
(lib.edgelab.bullpen_availability.compute_bullpen_workload_adjustment,
already live in scripts/build_market_ledger.py's compute_projections())
improve full-game predictive accuracy compared with the identical
projection logic with that one adjustment neutralized?

CONTROL: compute_projections(g) on a game's archived per-date pregame
snapshot (data/pipeline/<date>/normalized_slate.json) exactly as
captured -- i.e. "current production projection logic, including the
existing bullpen workload adjustment."

CANDIDATE: compute_projections(g2), where g2 is a deep copy of the SAME
g with ONLY away.bullpen.recentUsage/home.bullpen.recentUsage set to
None (compute_bullpen_workload_adjustment's own documented
missing-data contract: multiplier forced to 1.0, no other field
touched). Every other input -- season pen xFIP, starter xFIP, offense
baseline, park, platoon -- is IDENTICAL between control and candidate.

WHAT IS AND IS NOT RECONSTRUCTED (read before trusting evidenceLevel)
--------------------------------------------------------------------
This experiment does NOT independently re-fetch historical bullpen
recent-usage via lib.edgelab.pit_reconstruction's live MLB Stats API
adapters -- this research environment has no outbound network access to
statsapi.mlb.com (confirmed: a direct request returns HTTP 403 from the
environment's proxy). Instead it uses the recentUsage block ALREADY
EMBEDDED in each date's archived data/pipeline/<date>/normalized_slate.json
snapshot -- production's own prior live capture, not a fresh call, and
NOT the current overwritten data/bullpen.json (that file is never read
here). Every row is still required to pass an explicit, tested leakage
guard: recentUsage.dataAvailable is True AND recentUsage.asOfDate is
strictly before the game's own date (same "asOfDate exclusive of
game date" contract lib.edgelab.pit_reconstruction and
lib.research.statcast_pitch_store both already enforce) -- a row that
fails this check is EXCLUDED, never used.

This is weaker than an independently re-verified Milestone 2
reconstruction (it cannot prove the ENTIRE daily snapshot -- season pen
xFIP, starter xFIP, offense baseline -- was captured strictly pregame;
data/pipeline/<date>/normalized_slate.json is a single per-date file,
not a per-checkpoint archive, and this milestone did not independently
verify its own capture timing). Season-long bullpen talent, starter
quality, and team offense are UNAVAILABLE_HISTORICALLY per the
Milestone 2 audit (docs/EDGELAB_MILESTONE2_PIT_FEATURE_AUDIT.md) and
are NOT independently reconstructed here -- they are held IDENTICAL
between control and candidate (the "hold the historically captured
model/projection state fixed where available" fallback design), which
keeps the CONTROL-vs-CANDIDATE comparison valid for isolating the
bullpen-workload term specifically, but is why this experiment
registers at E1_RECONSTRUCTED_RETROSPECTIVE, not E2 -- see
register_control_and_experiment() below.

MARKET SCOPE
---------------
game_result, game_total, team_total -- all read compute_projections()'s
pen-xFIP-affected away_proj/home_proj, so all three are structurally
reachable by the ablation. F5/F3 are NOT included: compute_projections()
computes f5_away/f5_home from starter xFIP alone
(scripts/build_market_ledger.py's compute_projections, the f5_away/
f5_home block), never touching pen_xfip -- this is asserted directly
in tests/edgelab/test_run_bullpen_workload_ablation_experiment_script.py
(exact equality, not merely "smaller than"), matching the existing unit
test tests/test_bullpen_workload_pregame.py::TestF5ChangesMaterialLessThanFullGame.
game_total/team_total probabilities use p_over_total() (production's
own Poisson total-vs-line function) and are included only for
comparisonOperator == "OVER" rows (the only value observed in this
corpus) -- an unrecognized operator is excluded, not guessed.

Reuses lib.edgelab.research_dataset.build_opportunity_rows (the one
canonical join) for ticker/team/threshold/settlement resolution, and
lib.edgelab.paired_evaluation.pair_eligible_observations/
evaluate_probability_model_pair (the one pairing primitive) for the
CONTROL-vs-CANDIDATE probability comparison -- neither reimplemented.
"""
import copy
import glob
import json
import os
import sys
from collections import defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from lib.edgelab.storage import read_partition
from lib.edgelab.research_dataset import build_opportunity_rows
from lib.edgelab.paired_evaluation import pair_eligible_observations, evaluate_probability_model_pair
from lib.edgelab.bullpen_availability import compute_bullpen_workload_adjustment
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import candidate_identity as cand_id
from lib.edgelab import experiment_registry as exp_reg
from lib.edgelab import experiment_report as exp_report
from lib.edgelab import evidence_levels
from lib.edgelab import dispositions

from build_market_ledger import compute_projections, p_team_wins, p_over_total  # noqa: E402

REGISTRATION_TIMESTAMP = "2026-08-27T14:00:00Z"

DATA_DIR = os.path.join(_ROOT, "data")
PIPELINE_DIR = os.path.join(DATA_DIR, "pipeline")
EDGELAB_DIR = os.path.join(DATA_DIR, "edgelab")

CANONICAL_TOTAL_FAMILIES = {"game_total", "team_total"}
CANONICAL_FAMILIES = {"game_result"} | CANONICAL_TOTAL_FAMILIES

CHECKPOINT_LABEL = "SINGLE_DAILY_PROJECTION"

CONTROL_MODEL_ID = "CTRL-7252463d722626e6"  # reused from MLB-RSCH-0001 -- same production system identity


# ── Corpus loading ──────────────────────────────────────────────────────────

def _all_dates(entity):
    files = glob.glob(os.path.join(EDGELAB_DIR, entity, "*.jsonl*"))
    return sorted({os.path.basename(f).split(".")[0] for f in files})


def load_corpus():
    dates = sorted(set(_all_dates("observations")) | set(_all_dates("settlements")) | set(_all_dates("games")))
    observations, settlements, games = [], [], []
    for d in dates:
        observations.extend(read_partition("observations", d))
        settlements.extend(read_partition("settlements", d))
        games.extend(read_partition("games", d))
    return observations, settlements, games


_slate_cache = {}


def load_normalized_slate(date):
    if date not in _slate_cache:
        path = os.path.join(PIPELINE_DIR, date, "normalized_slate.json")
        if os.path.exists(path):
            with open(path) as f:
                doc = json.load(f)
            games = (doc.get("data") or {}).get("games") or []
            _slate_cache[date] = {str(g.get("gameId")): g for g in games if g.get("gameId") is not None}
        else:
            _slate_cache[date] = None
    return _slate_cache[date]


# ── PIT leakage guard (defense in depth, matching lib.edgelab.pit_reconstruction's contract) ──

def recent_usage_leakage_safe(recent_usage, game_date):
    """True iff recentUsage.dataAvailable and recentUsage.asOfDate is
    strictly before game_date -- a game-date match or missing asOfDate
    is never treated as safe."""
    if not recent_usage or not recent_usage.get("dataAvailable"):
        return False
    as_of = recent_usage.get("asOfDate")
    if not as_of or not game_date:
        return False
    return as_of < game_date


def neutralize_recent_usage(g):
    """Deep-copies g; sets away/home bullpen.recentUsage=None. The ONLY
    field this touches -- compute_projections()/compute_bullpen_workload_
    adjustment() are never modified, only their input."""
    g2 = copy.deepcopy(g)
    for side in ("away", "home"):
        bp = (g2.get(side) or {}).get("bullpen")
        if bp is not None:
            bp["recentUsage"] = None
    return g2


# ── Per-game projection state ────────────────────────────────────────────────

def game_projection_state(slate_game, game_date):
    """
    Returns None if this game is ineligible (missing recentUsage,
    leakage-unsafe, or compute_projections() itself reports missing
    fields), else a dict with control/candidate compute_projections()
    tuples plus the workload-adjustment breakdown used for segmentation.
    """
    away_bp = (slate_game.get("away") or {}).get("bullpen") or {}
    home_bp = (slate_game.get("home") or {}).get("bullpen") or {}
    away_ru = away_bp.get("recentUsage")
    home_ru = home_bp.get("recentUsage")

    if not recent_usage_leakage_safe(away_ru, game_date) or not recent_usage_leakage_safe(home_ru, game_date):
        return None

    control = compute_projections(slate_game)
    if control[0] is None or control[4]:
        return None

    candidate_game = neutralize_recent_usage(slate_game)
    candidate = compute_projections(candidate_game)
    if candidate[0] is None or candidate[4]:
        return None

    away_adj = compute_bullpen_workload_adjustment(away_ru)
    home_adj = compute_bullpen_workload_adjustment(home_ru)

    return {
        "control": control,
        "candidate": candidate,
        "awayAbbr": (slate_game.get("away") or {}).get("abbr"),
        "homeAbbr": (slate_game.get("home") or {}).get("abbr"),
        "awayAdjustment": away_adj,
        "homeAdjustment": home_adj,
        "combinedTotalPenalty": round(
            (away_adj["multiplier"] - 1.0) + (home_adj["multiplier"] - 1.0), 6
        ),
        "eitherAdjustmentApplied": away_adj["adjustmentApplied"] or home_adj["adjustmentApplied"],
        "eitherBackToBack": bool((away_ru or {}).get("backToBackRelievers")) or bool((home_ru or {}).get("backToBackRelievers")),
        "eitherHighLeverageTaxed": away_adj["components"]["taxedHighLeverageArmCount"] > 0 or home_adj["components"]["taxedHighLeverageArmCount"] > 0,
    }


def resolve_probability(state, canonical_family, team, threshold):
    """Returns (control_p, candidate_p) or (None, None) if unresolvable."""
    away_c, home_c = state["control"][0], state["control"][1]
    away_x, home_x = state["candidate"][0], state["candidate"][1]
    away_abbr, home_abbr = state["awayAbbr"], state["homeAbbr"]

    if canonical_family == "game_result":
        if team == home_abbr:
            pc, _ = p_team_wins(home_c, away_c)
            px, _ = p_team_wins(home_x, away_x)
        elif team == away_abbr:
            pc, _ = p_team_wins(away_c, home_c)
            px, _ = p_team_wins(away_x, home_x)
        else:
            return None, None
        return round(pc, 6), round(px, 6)

    if threshold is None:
        return None, None
    threshold = float(threshold)

    if canonical_family == "game_total":
        return round(p_over_total(away_c + home_c, threshold), 6), round(p_over_total(away_x + home_x, threshold), 6)

    if canonical_family == "team_total":
        if team == home_abbr:
            return round(p_over_total(home_c, threshold), 6), round(p_over_total(home_x, threshold), 6)
        elif team == away_abbr:
            return round(p_over_total(away_c, threshold), 6), round(p_over_total(away_x, threshold), 6)
        return None, None

    return None, None


OUTCOME_MAP = {"YES": 1, "NO": 0}


def build_eligible_market_rows(opportunity_rows, games_by_id):
    """
    Deduplicates opportunity_rows to one row per marketTicker (descriptive
    fields -- team/threshold/family/settlement -- don't change tick to
    tick), keeps only SETTLED rows in CANONICAL_FAMILIES with a resolvable
    OVER-only comparison for totals, and groups them by gameId.

    Returns (rows_by_game_id, exclusion_counts).
    """
    seen_tickers = {}
    exclusions = defaultdict(int)
    for row in opportunity_rows:
        ticker = row.get("marketTicker")
        if ticker in seen_tickers:
            continue
        family = row.get("canonicalMarketFamily")
        if family not in CANONICAL_FAMILIES:
            continue
        if row.get("settlementStatus") != "SETTLED":
            exclusions["not_settled"] += 1
            continue
        outcome = OUTCOME_MAP.get(row.get("settlementResult"))
        if outcome is None:
            exclusions["unresolvable_settlement_result"] += 1
            continue
        if family in CANONICAL_TOTAL_FAMILIES and row.get("comparisonOperator") not in (None, "OVER"):
            exclusions["non_over_comparison_operator"] += 1
            continue
        game = games_by_id.get(row.get("gameId"))
        if not game or not game.get("mlbGamePk") or not game.get("gameDate"):
            exclusions["no_mlb_game_pk_or_date"] += 1
            continue
        seen_tickers[ticker] = {
            "marketTicker": ticker,
            "gameId": row.get("gameId"),
            "gameDate": game.get("gameDate"),
            "mlbGamePk": str(game.get("mlbGamePk")),
            "canonicalMarketFamily": family,
            "team": row.get("team"),
            "threshold": row.get("threshold"),
            "outcome": outcome,
        }

    rows_by_game_id = defaultdict(list)
    for r in seen_tickers.values():
        rows_by_game_id[r["gameId"]].append(r)
    return rows_by_game_id, exclusions


# ── Registration ──────────────────────────────────────────────────────────

def register_control_and_experiment():
    """
    Reuses the EXISTING control (CTRL-7252463d722626e6, registered by
    MLB-RSCH-0001) -- same production system identity
    (scripts/build_market_ledger.py), not a new production model.
    Registers a NEW candidate (bullpen workload adjustment neutralized)
    and a NEW experiment (MLB-RSCH-0002).

    Evidence level: E1_RECONSTRUCTED_RETROSPECTIVE, not E2 -- see this
    module's docstring's "WHAT IS AND IS NOT RECONSTRUCTED" section. The
    bullpen-workload input specifically is used with a tested, explicit
    leakage guard; the OTHER held-fixed projection inputs (season pen
    xFIP, starter xFIP, offense baseline) are NOT independently proven
    PIT-safe this milestone, so the experiment as a whole cannot honestly
    claim E2.
    """
    try:
        control_registration = ctrl_id.load_control(CONTROL_MODEL_ID)
    except Exception:
        control_registration = None
    if control_registration is None:
        raise RuntimeError(
            f"{CONTROL_MODEL_ID!r} is not registered -- expected it to already exist from MLB-RSCH-0001 "
            f"(PR #117, merged). Run that experiment's registration first, or register a fresh control."
        )

    candidate_registration = cand_id.build_candidate_registration(
        name="bullpen_workload_adjustment_neutralized",
        base_control_model_id=CONTROL_MODEL_ID,
        change_description="compute_bullpen_workload_adjustment's contribution to season pen xFIP is neutralized "
                            "(recentUsage forced to None, multiplier forced to 1.0) -- every other input to "
                            "compute_projections(g) is byte-identical to control.",
        change_type=cand_id.CHANGE_TYPE_FEATURE_REMOVAL,
        implementation_ref="scripts/edgelab/run_bullpen_workload_ablation_experiment.py:neutralize_recent_usage",
        description="Research-only ablation variant of the production run-projection engine "
                     "(scripts/build_market_ledger.py:compute_projections) with the bullpen "
                     "recent-workload adjustment removed. productionCodePathsModified is always [] "
                     "per lib.edgelab.candidate_identity's own contract -- production code is never touched.",
        registered_at=REGISTRATION_TIMESTAMP,
    )
    cand_id.register_candidate(candidate_registration)

    pit_requirements = {
        "team_recent_game_log_reconstruction": "PREDICTIVE_INPUT",
        "settlement_outcome": "EVALUATION_TARGET",
        "archived_kalshi_market_observation": "AUXILIARY_METADATA",
    }

    experiment = exp_reg.build_experiment_definition(
        title="Bullpen Workload Ablation",
        hypothesis="The current production bullpen recent-workload adjustment "
                   "(lib.edgelab.bullpen_availability.compute_bullpen_workload_adjustment) improves "
                   "full-game predictive accuracy relative to the identical projection logic with that "
                   "adjustment neutralized.",
        research_question="Does removing the bullpen recent-workload adjustment from "
                           "scripts/build_market_ledger.py's compute_projections() change paired Brier "
                           "score / log-loss / calibration against realized outcomes, on full-game market "
                           "families the adjustment can structurally affect?",
        owner="edgelab_research_lab",
        control_model_id=CONTROL_MODEL_ID,
        candidate_variant_id=candidate_registration["candidateVariantId"],
        evidence_level="E1_RECONSTRUCTED_RETROSPECTIVE",
        target_population="MLB games with a settled game_result/game_total/team_total Kalshi market, an archived "
                           "pregame pipeline snapshot (data/pipeline/<date>/normalized_slate.json), and "
                           "leakage-safe bullpen recentUsage (asOfDate strictly before game date) for both teams.",
        market_families=["game_result", "game_total", "team_total"],
        eligibility_criteria=[
            "settlementStatus == SETTLED with a resolvable YES/NO result",
            "comparisonOperator == OVER for game_total/team_total (only value observed in this corpus)",
            "games entity row has a non-null mlbGamePk and gameDate",
            "a matching game exists in that date's normalized_slate.json",
            "both teams' recentUsage.dataAvailable is True",
            "both teams' recentUsage.asOfDate is strictly before the game's own date",
            "compute_projections() reports no missing fields for both control and candidate",
        ],
        exclusion_criteria=[
            "F3/F5 markets (structurally starter-only, cannot be affected by this adjustment)",
            "non-OVER comparison operators on total markets",
            "games without an archived normalized_slate.json for that date",
        ],
        prediction_checkpoints=[CHECKPOINT_LABEL],
        primary_metric="paired Brier score delta (candidate minus control) vs realized settlement outcome",
        secondary_metrics=["paired log-loss delta", "calibration error", "segment-level Brier/log-loss deltas"],
        chronological_split_policy="NONE_SINGLE_HISTORICAL_CORPUS",
        minimum_sample_requirement={"independentGames": 20},
        clustering_unit="gameId",
        experiment_type=exp_reg.EXPERIMENT_TYPE_EXPLORATORY,
        false_discovery_handling=exp_reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements=pit_requirements,
        registered_at=REGISTRATION_TIMESTAMP,
        experiment_id="MLB-RSCH-0002",
        notes="Component ablation, not a full historical model reconstruction -- see module docstring's "
              "'WHAT IS AND IS NOT RECONSTRUCTED' section for exactly what is/isn't independently PIT-proven.",
    )
    exp_reg.register_experiment(experiment)
    return control_registration, candidate_registration, experiment


# ── Row construction ──────────────────────────────────────────────────────

def median(values):
    values = sorted(values)
    n = len(values)
    if n == 0:
        return None
    mid = n // 2
    return values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2


def build_control_candidate_rows(rows_by_game_id, states_by_game_id):
    """
    For each (gameId, ticker) with a resolvable state and probability,
    returns two parallel lists (control_rows, candidate_rows) suitable
    for lib.edgelab.paired_evaluation.pair_eligible_observations --
    identical keys, differing only in modelFairProbability.

    "High workload" is defined relative to THIS corpus's own median
    combinedTotalPenalty across eligible games (computed here, not a
    fixed guessed constant) -- descriptive of the observed distribution,
    not a tuned coefficient.
    """
    control_rows, candidate_rows = [], []
    unresolvable = 0
    workload_median = median([s["combinedTotalPenalty"] for s in states_by_game_id.values()]) or 0.0
    for game_id, market_rows in rows_by_game_id.items():
        state = states_by_game_id.get(game_id)
        if state is None:
            unresolvable += len(market_rows)
            continue
        for r in market_rows:
            pc, px = resolve_probability(state, r["canonicalMarketFamily"], r["team"], r["threshold"])
            if pc is None or px is None:
                unresolvable += 1
                continue
            base = {
                "gameId": game_id,
                "marketTicker": r["marketTicker"],
                "researchCheckpoint": CHECKPOINT_LABEL,
                "gameDate": r["gameDate"],
                "outcome": r["outcome"],
                "marketFamily": r["canonicalMarketFamily"],
                "highWorkloadSegment": state["combinedTotalPenalty"] >= workload_median,
                "backToBackPresent": state["eitherBackToBack"],
                "highLeverageTaxedPresent": state["eitherHighLeverageTaxed"],
                "favoriteTeam": r["team"] == (state["homeAbbr"] if state["control"][1] >= state["control"][0] else state["awayAbbr"]),
            }
            control_rows.append(dict(base, modelFairProbability=pc))
            candidate_rows.append(dict(base, modelFairProbability=px))
    return control_rows, candidate_rows, unresolvable


def _segment_result(control_rows, candidate_rows, predicate):
    c = [r for r in control_rows if predicate(r)]
    x = [r for r in candidate_rows if predicate(r)]
    pairing = pair_eligible_observations(c, x)
    return evaluate_probability_model_pair(pairing, cluster_key="gameId")


def build_segments(control_rows, candidate_rows):
    segments = {
        "overall": lambda r: True,
        "highWorkload": lambda r: r["highWorkloadSegment"],
        "lowWorkload": lambda r: not r["highWorkloadSegment"],
        "backToBackPresent": lambda r: r["backToBackPresent"],
        "backToBackAbsent": lambda r: not r["backToBackPresent"],
        "highLeverageTaxedPresent": lambda r: r["highLeverageTaxedPresent"],
        "highLeverageTaxedAbsent": lambda r: not r["highLeverageTaxedPresent"],
    }
    results = {}
    for name, predicate in segments.items():
        results[name] = _segment_result(control_rows, candidate_rows, predicate)

    by_family = {}
    families_present = {r["marketFamily"] for r in control_rows}
    for family in sorted(families_present):
        by_family[family] = _segment_result(control_rows, candidate_rows, lambda r, f=family: r["marketFamily"] == f)
    results["byMarketFamily"] = by_family
    return results


# ── Functional-form diagnostics ─────────────────────────────────────────────

def functional_form_diagnostics(states_by_game_id):
    """
    Inspects the CURRENT adjustment's shape across every eligible game
    (not just those with a settled market) -- multiplier range, cap/
    floor usage, neutral frequency, distribution by team, concentration
    in small samples. No coefficients are tuned here.
    """
    multipliers = []
    neutral_count = 0
    capped_count = 0
    by_team = defaultdict(list)
    component_counts = defaultdict(int)

    for state in states_by_game_id.values():
        for side_abbr, adj in ((state["awayAbbr"], state["awayAdjustment"]), (state["homeAbbr"], state["homeAdjustment"])):
            m = adj["multiplier"]
            multipliers.append(m)
            by_team[side_abbr].append(m)
            if not adj["adjustmentApplied"]:
                neutral_count += 1
            if round(m - 1.0, 4) >= 0.12:  # MAX_TOTAL_PENALTY, mirrored as a literal since this is diagnostic-only
                capped_count += 1
            c = adj["components"]
            if c["backToBackCount"] > 0:
                component_counts["backToBack"] += 1
            if c["heavilyUsedRelieverCount"] > 0:
                component_counts["heavyRecentPitch"] += 1
            if c["taxedHighLeverageArmCount"] > 0:
                component_counts["highLeverageTaxed"] += 1
            if (c["teamWorkloadRatio"] or 0) > 0:
                component_counts["overallWorkload"] += 1

    n = len(multipliers)
    team_means = sorted(
        ((team, round(sum(ms) / len(ms), 4), len(ms)) for team, ms in by_team.items()),
        key=lambda t: -t[1],
    )
    small_sample_extreme = [t for t in team_means if t[1] >= 1.08 and t[2] < 5]

    return {
        "nTeamGameObservations": n,
        "multiplierMin": round(min(multipliers), 4) if multipliers else None,
        "multiplierMax": round(max(multipliers), 4) if multipliers else None,
        "multiplierMean": round(sum(multipliers) / n, 4) if n else None,
        "neutralFraction": round(neutral_count / n, 4) if n else None,
        "cappedFraction": round(capped_count / n, 4) if n else None,
        "componentFiringFractions": {k: round(v / n, 4) for k, v in component_counts.items()} if n else {},
        "topTeamMeanMultipliers": team_means[:5],
        "extremeAdjustmentsConcentratedInSmallSamples": [
            {"team": t[0], "meanMultiplier": t[1], "nObservations": t[2]} for t in small_sample_extreme
        ],
    }


# ── Conclusion classification ────────────────────────────────────────────

def classify_adjustment(paired_delta_brier, ci_low, ci_high, independent_games, min_games=20):
    """
    Conservative 5-way classification of the CURRENT (unmodified)
    bullpen workload adjustment. Sign convention: pairedDelta.brierScore
    = candidate_brier - control_brier (lower Brier is better). A
    POSITIVE delta means removing the adjustment (candidate) made
    predictions WORSE -- i.e. the adjustment HELPS. A NEGATIVE delta
    means removing it made predictions BETTER -- i.e. the adjustment
    HURTS.
    """
    if independent_games < min_games or paired_delta_brier is None or ci_low is None or ci_high is None:
        return "WEAK_UNPROVEN"
    excludes_zero = ci_low > 0 or ci_high < 0
    if not excludes_zero:
        return "WEAK_UNPROVEN"
    large_magnitude = abs(paired_delta_brier) >= 0.01
    if ci_low > 0:
        return "CLEARLY_HELPFUL" if large_magnitude else "PROBABLY_HELPFUL"
    return "CLEARLY_HARMFUL" if large_magnitude else "PROBABLY_HARMFUL"


# ── Markdown summary ─────────────────────────────────────────────────────

def _fmt(v, digits=4):
    return "n/a" if v is None else (round(v, digits) if isinstance(v, float) else v)


def render_markdown_summary(overall, segments, diagnostics, classification, exclusions, unresolvable, n_eligible_games):
    lines = []
    lines.append("# MLB-RSCH-0002: Bullpen Workload Ablation\n")
    lines.append("RESEARCH ONLY. Production behavior unchanged -- see script module docstring.\n")
    lines.append(f"**Conclusion: {classification.replace('_', ' ')}**\n")
    lines.append("## Overall paired result\n")
    lines.append(
        f"- n={overall['n']}, independentGames={overall['independentGames']}, "
        f"independentDates={overall['independentDates']}\n"
        f"- Control Brier={_fmt(overall['control']['brierScore'])}, "
        f"Candidate Brier={_fmt(overall['candidate']['brierScore'])}\n"
        f"- Paired delta (candidate - control) Brier={_fmt(overall['pairedDelta']['brierScore'])}, "
        f"logLoss={_fmt(overall['pairedDelta']['logLoss'])}\n"
        f"- 90% CI on Brier delta: [{_fmt(overall['pairedDeltaConfidenceInterval']['low'])}, "
        f"{_fmt(overall['pairedDeltaConfidenceInterval']['high'])}]\n"
    )
    lines.append("## Segments\n")
    for name in ("highWorkload", "lowWorkload", "backToBackPresent", "backToBackAbsent",
                 "highLeverageTaxedPresent", "highLeverageTaxedAbsent"):
        s = segments[name]
        lines.append(
            f"- **{name}**: n={s['n']}, games={s['independentGames']}, "
            f"pairedDeltaBrier={_fmt(s['pairedDelta']['brierScore'])}\n"
        )
    lines.append("## Market family\n")
    for family, s in segments["byMarketFamily"].items():
        lines.append(
            f"- **{family}**: n={s['n']}, games={s['independentGames']}, "
            f"pairedDeltaBrier={_fmt(s['pairedDelta']['brierScore'])}\n"
        )
    lines.append("## Functional-form diagnostics\n")
    lines.append(
        f"- multiplier range [{_fmt(diagnostics['multiplierMin'])}, {_fmt(diagnostics['multiplierMax'])}], "
        f"mean={_fmt(diagnostics['multiplierMean'])}\n"
        f"- neutral (multiplier==1.0) fraction={_fmt(diagnostics['neutralFraction'])}\n"
        f"- capped (at MAX_TOTAL_PENALTY) fraction={_fmt(diagnostics['cappedFraction'])}\n"
        f"- component firing fractions: {diagnostics['componentFiringFractions']}\n"
        f"- top team mean multipliers: {diagnostics['topTeamMeanMultipliers']}\n"
        f"- extreme adjustments in small samples (n<5, mean>=1.08): "
        f"{diagnostics['extremeAdjustmentsConcentratedInSmallSamples']}\n"
    )
    lines.append("## Eligibility\n")
    lines.append(f"- games with a resolvable projection state: {n_eligible_games}\n")
    lines.append(f"- market rows excluded pre-projection: {dict(exclusions)}\n")
    lines.append(f"- market rows unresolvable (no matching game state / probability): {unresolvable}\n")
    return "".join(lines)


# ── main ──────────────────────────────────────────────────────────────────

def main():
    observations, settlements, games = load_corpus()
    games_by_id = {g["gameId"]: g for g in games if g.get("gameId")}

    opportunity_rows = build_opportunity_rows(observations, settlements=settlements, games=games)
    rows_by_game_id, exclusions = build_eligible_market_rows(opportunity_rows, games_by_id)

    states_by_game_id = {}
    for game_id, market_rows in rows_by_game_id.items():
        game_date = market_rows[0]["gameDate"]
        mlb_game_pk = market_rows[0]["mlbGamePk"]
        slate_games = load_normalized_slate(game_date)
        if not slate_games or mlb_game_pk not in slate_games:
            continue
        state = game_projection_state(slate_games[mlb_game_pk], game_date)
        if state is not None:
            states_by_game_id[game_id] = state

    control_rows, candidate_rows, unresolvable = build_control_candidate_rows(rows_by_game_id, states_by_game_id)

    pairing = pair_eligible_observations(control_rows, candidate_rows)
    overall = evaluate_probability_model_pair(pairing, cluster_key="gameId")
    segments = build_segments(control_rows, candidate_rows)
    diagnostics = functional_form_diagnostics(states_by_game_id)

    classification = classify_adjustment(
        overall["pairedDelta"]["brierScore"],
        overall["pairedDeltaConfidenceInterval"]["low"],
        overall["pairedDeltaConfidenceInterval"]["high"],
        overall["independentGames"],
    )

    control_registration, candidate_registration, experiment = register_control_and_experiment()

    report = exp_report.build_experiment_report(
        experiment=experiment,
        control_registration=control_registration,
        candidate_registration=candidate_registration,
        pairing_result=pairing,
        probability_evaluation=overall,
        disposition=dispositions.RESEARCH_CANDIDATE,
        evidence_level="E1_RECONSTRUCTED_RETROSPECTIVE",
        evaluation_date_range=[min((r["gameDate"] for r in control_rows), default=None),
                                max((r["gameDate"] for r in control_rows), default=None)],
        pit_provenance_status="COMPONENT_ABLATION_HELD_FIXED_ELSEWHERE",
        pit_limitations=[
            "Bullpen recentUsage is read from data/pipeline/<date>/normalized_slate.json's already-archived "
            "capture (production's own prior live fetch), not independently re-fetched via "
            "lib.edgelab.pit_reconstruction -- this research environment has no outbound network access to "
            "statsapi.mlb.com. Every row still passes an explicit asOfDate < gameDate leakage guard.",
            "Season-long pen xFIP, starter xFIP, and offense baseline are held identical between control and "
            "candidate but are NOT independently proven pregame-safe this milestone (UNAVAILABLE_HISTORICALLY "
            "per the Milestone 2 audit) -- this is why evidence level is E1, not E2.",
        ],
        methodological_limitations=[
            "Component ablation, not a full independent historical model reconstruction.",
            "game_total/team_total resolved only for comparisonOperator == OVER rows (the only value observed).",
            "MAE/RMSE against realized final score not computed -- no raw box-score final-score entity was "
                "available in the corpus within this milestone's scope; Brier/log-loss/calibration against the "
                "settled market outcome is the primary metric instead.",
        ],
        secondary_metrics={"segments": {k: v for k, v in segments.items() if k != "byMarketFamily"},
                            "byMarketFamily": segments["byMarketFamily"],
                            "functionalFormDiagnostics": diagnostics,
                            "classification": classification},
        generated_at=REGISTRATION_TIMESTAMP,
    )
    exp_report.write_experiment_report(report)

    analytics_path = os.path.join(EDGELAB_DIR, "analytics", "latest_mlb_rsch_0002_bullpen_workload_ablation.json")
    os.makedirs(os.path.dirname(analytics_path), exist_ok=True)
    with open(analytics_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True)

    summary_md = render_markdown_summary(overall, segments, diagnostics, classification, exclusions, unresolvable, len(states_by_game_id))
    summary_path = os.path.join(EDGELAB_DIR, "reports", "mlb_rsch_0002_bullpen_workload_ablation_summary.md")
    os.makedirs(os.path.dirname(summary_path), exist_ok=True)
    with open(summary_path, "w") as f:
        f.write(summary_md)

    print(json.dumps({
        "experimentId": experiment["experimentId"],
        "nEligibleGames": len(states_by_game_id),
        "overall": overall,
        "classification": classification,
        "disposition": report["disposition"],
    }, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
