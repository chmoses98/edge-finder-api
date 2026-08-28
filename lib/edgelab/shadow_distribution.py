"""
lib/edgelab/shadow_distribution.py
======================================
Research Lab experiment MLB-RSCH-0011: "Prospective Negative-Binomial
Shadow". Pure, research-only computation of PAIRED CONTROL_POISSON /
CANDIDATE_NB_0010 probabilities from the EXACT SAME expected home/away
run means (awayProjRuns/homeProjRuns) production already computed for a
given game/checkpoint via scripts.build_market_ledger.compute_game_
projection_context (imported unchanged, never reimplemented, never
called differently than production calls it).

FROZEN CANDIDATE SPECIFICATION -- do not refit here. The negative-
binomial dispersion parameter below is copied verbatim from the
canonical MLB-RSCH-0010 artifact
(data/edgelab/analytics/latest_mlb_rsch_0010_run_distribution.json,
fittedParameters.overdispersion), the winning D1 (independent
negative-binomial) specification MLB-RSCH-0010 selected via its own
preregistered DEV/VAL forward-selection rule. Verified byte-exact
against that artifact at the time this module was written (0.281513,
no discrepancy). MLB-RSCH-0011 must NEVER re-fit this value against
prospective results -- doing so would silently convert a frozen
transfer test into a new, un-preregistered curve-fit.

CANDIDATE CONSTRUCTION: D1 is the INDEPENDENT negative-binomial
candidate (no correlation term -- MLB-RSCH-0010's own D2/bivariate-
Poisson candidate did not win selection), built via
lib.edgelab.backtest.run_distributions.independent_joint_pmf(pmf_home,
pmf_away), each side's own negative_binomial_pmf(k, mean=projRuns,
dispersion=FROZEN_DISPERSION) -- the SAME mean as the Poisson control,
only the variance differs (dispersion=0 would degenerate to Poisson
exactly; see that function's own docstring).

SCOPE -- FULL-GAME FAMILIES ONLY. This module computes shadow
probabilities ONLY for markets whose probability is a function of
awayProjRuns/homeProjRuns (full 9-inning expected runs): moneyline,
game total, team total, run margin. It NEVER computes F3/F5/F7 or
NRFI/YRFI shadow probabilities -- MLB-RSCH-0010's dispersion parameter
was fit exclusively on FULL-GAME team-run counts; extrapolating it to
a first-inning or 5-inning horizon without separate research would
violate the MLB-RSCH-0011 mission's own explicit "do not extrapolate"
instruction. See UNSUPPORTED_HORIZONS below -- callers that need to
report on those markets must label them UNSUPPORTED_FOR_THIS_SHADOW,
never silently omit them without explanation.

FAIL-SAFE CONTRACT: every function in this module is pure and may
raise (ValueError) on missing/non-positive projection means -- it
NEVER fabricates a fallback probability. Callers (see
build_shadow_records_for_snapshot_cycle) MUST wrap per-game calls in
their own try/except so one game's bad input can never abort a whole
capture cycle or affect any other game's shadow record, and must
record an explicit FAILED status rather than silently dropping the
game. This module itself performs no I/O, no persistence, and never
touches any production probability, recommendation, edge, confidence,
Bet Up To, stake, bankroll, or slate output field.
"""

from lib.edgelab import ids
from lib.edgelab.backtest.run_distributions import (
    MAX_RUNS,
    home_win_and_push_prob,
    independent_joint_pmf,
    margin_at_least_prob,
    negative_binomial_pmf,
    team_total_over_prob,
    total_over_prob,
)
from scripts.build_market_ledger import poisson_pmf

# Frozen at MLB-RSCH-0011 registration time -- verified byte-exact
# against data/edgelab/analytics/latest_mlb_rsch_0010_run_distribution.json
# ("fittedParameters": {"overdispersion": 0.281513, "correlationLambdaC":
# 0.130999}). Only overdispersion is used here -- MLB-RSCH-0010's winning
# candidate (D1) carries no correlation term.
FROZEN_DISPERSION = 0.281513
CANDIDATE_VERSION = "MLB-RSCH-0010-D1-negative-binomial-v1"
CONTROL_VERSION = "production-independent-poisson-v1"

GAME_TOTAL_LINES = (7.5, 8.5, 9.5, 10.5)
TEAM_TOTAL_LINES = (2.5, 3.5, 4.5, 5.5)
MARGIN_THRESHOLDS = (2, 3)

FAMILY_MONEYLINE = "moneyline"
FAMILY_GAME_TOTAL = "game_total"
FAMILY_TEAM_TOTAL_AWAY = "team_total_away"
FAMILY_TEAM_TOTAL_HOME = "team_total_home"
FAMILY_RUN_MARGIN = "run_margin"

# Preregistered primary/secondary split (MLB-RSCH-0011 mission section
# "PRIMARY SHADOW FAMILIES"). Documentation only -- compute_paired_probabilities
# always computes every supported cell; a caller/report decides which to
# lead with.
PRIMARY_FAMILIES = (FAMILY_GAME_TOTAL, FAMILY_TEAM_TOTAL_AWAY, FAMILY_TEAM_TOTAL_HOME)
SECONDARY_FAMILIES = (FAMILY_MONEYLINE, FAMILY_RUN_MARGIN)

# Never computed by this module -- see module docstring's SCOPE section.
UNSUPPORTED_HORIZONS = ("F3", "F5", "F7", "NRFI", "YRFI")
UNSUPPORTED_FOR_THIS_SHADOW = "UNSUPPORTED_FOR_THIS_SHADOW"

STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED_ISOLATED"


def _poisson_pmf_fn(lam):
    def f(k):
        return poisson_pmf(k, lam)
    return f


def _nb_pmf_fn(mean, dispersion):
    def f(k):
        return negative_binomial_pmf(k, mean, dispersion)
    return f


def compute_paired_probabilities(away_proj_runs, home_proj_runs, *, dispersion=FROZEN_DISPERSION, max_runs=MAX_RUNS):
    """
    Pure. Returns {cellKey: {"control": p, "candidate": p}} for every
    full-game cell this shadow supports (moneyline home/away win, 4 game-
    total lines, 4x2 team-total lines, win-by-N+/lose-by-N+ for N in
    MARGIN_THRESHOLDS). `away_proj_runs`/`home_proj_runs` must be the
    EXACT SAME values production's own compute_game_projection_context
    produced for this game/checkpoint -- this function does not itself
    fetch or recompute them (see build_shadow_records_for_snapshot_cycle).

    Raises ValueError for None/non-positive means -- never silently
    substitutes a default lambda (a non-positive projection is a real
    upstream data problem, not a valid degenerate case for either
    Poisson or negative-binomial run scoring).
    """
    if away_proj_runs is None or home_proj_runs is None:
        raise ValueError(f"awayProjRuns/homeProjRuns must both be present, got away={away_proj_runs!r} home={home_proj_runs!r}")
    if away_proj_runs <= 0 or home_proj_runs <= 0:
        raise ValueError(f"awayProjRuns/homeProjRuns must both be positive, got away={away_proj_runs!r} home={home_proj_runs!r}")

    control_home_pmf, control_away_pmf = _poisson_pmf_fn(home_proj_runs), _poisson_pmf_fn(away_proj_runs)
    candidate_home_pmf, candidate_away_pmf = _nb_pmf_fn(home_proj_runs, dispersion), _nb_pmf_fn(away_proj_runs, dispersion)
    control_joint = independent_joint_pmf(control_home_pmf, control_away_pmf)
    candidate_joint = independent_joint_pmf(candidate_home_pmf, candidate_away_pmf)

    cells = {}

    ctrl_home_win, ctrl_push = home_win_and_push_prob(control_joint, max_runs=max_runs)
    cand_home_win, cand_push = home_win_and_push_prob(candidate_joint, max_runs=max_runs)
    cells[f"{FAMILY_MONEYLINE}_home_win"] = {"control": ctrl_home_win, "candidate": cand_home_win}
    cells[f"{FAMILY_MONEYLINE}_away_win"] = {
        "control": 1.0 - ctrl_home_win - ctrl_push,
        "candidate": 1.0 - cand_home_win - cand_push,
    }

    for line in GAME_TOTAL_LINES:
        cells[f"{FAMILY_GAME_TOTAL}_over_{line}"] = {
            "control": total_over_prob(control_joint, line, max_runs=max_runs),
            "candidate": total_over_prob(candidate_joint, line, max_runs=max_runs),
        }

    for line in TEAM_TOTAL_LINES:
        cells[f"{FAMILY_TEAM_TOTAL_AWAY}_over_{line}"] = {
            "control": team_total_over_prob(control_away_pmf, line, max_runs=max_runs),
            "candidate": team_total_over_prob(candidate_away_pmf, line, max_runs=max_runs),
        }
        cells[f"{FAMILY_TEAM_TOTAL_HOME}_over_{line}"] = {
            "control": team_total_over_prob(control_home_pmf, line, max_runs=max_runs),
            "candidate": team_total_over_prob(candidate_home_pmf, line, max_runs=max_runs),
        }

    for margin in MARGIN_THRESHOLDS:
        cells[f"{FAMILY_RUN_MARGIN}_win_by_at_least_{margin}"] = {
            "control": margin_at_least_prob(control_joint, margin, max_runs=max_runs),
            "candidate": margin_at_least_prob(candidate_joint, margin, max_runs=max_runs),
        }
        # "home loses by margin+" == "away wins by margin+" == margin_at_least_prob
        # on the joint with home/away arguments swapped (see module tests).
        cells[f"{FAMILY_RUN_MARGIN}_lose_by_at_least_{margin}"] = {
            "control": margin_at_least_prob(lambda h, a, _j=control_joint: _j(a, h), margin, max_runs=max_runs),
            "candidate": margin_at_least_prob(lambda h, a, _j=candidate_joint: _j(a, h), margin, max_runs=max_runs),
        }

    return cells


def build_shadow_records_for_snapshot_cycle(evaluated_snapshots, *, compute_projection_context_fn, run_id, experiment_id, evidence_level, now=None):
    """
    For each `{"gameId", "checkpoint", "game"}` entry in
    `evaluated_snapshots` (one per game production actually evaluated
    this prospective-snapshot cycle -- see
    lib.edgelab.prospective_snapshot.run_prospective_snapshot_cycle's
    own third return value), independently recomputes
    `compute_projection_context_fn(game)` (the SAME pure, deterministic
    production function, called against the SAME game object production
    just evaluated a moment earlier in this cycle -- guaranteed to
    return byte-identical awayProjRuns/homeProjRuns, since neither the
    function nor its input changed) and builds ONE shadow record per
    game carrying every paired control/candidate cell.

    Every game is wrapped in its own try/except -- one game's bad or
    missing projection data produces a single FAILED_ISOLATED record
    (explicit failureReason, no probability fields, no fabricated
    fallback) and never aborts the rest of the cycle. Returns
    (records, failures) -- `failures` is a short list of
    {"gameId", "checkpoint", "reason"} for run-log/monitoring, `records`
    contains one dict per input entry regardless of success/failure.

    NEVER writes anything -- pure. NEVER calls evaluate_game or any
    production recommendation/edge/staking function. NEVER mutates
    `evaluated_snapshots` or the game objects it references.
    """
    now = now or ids.utc_now_iso()
    records = []
    failures = []

    for entry in evaluated_snapshots:
        game_id, checkpoint, game = entry.get("gameId"), entry.get("checkpoint"), entry.get("game")
        base = {
            "shadowEvaluationId": ids.build_model_evaluation_id(f"{run_id}:{experiment_id}", f"{game_id}:{checkpoint}"),
            "experimentId": experiment_id,
            "evidenceLevel": evidence_level,
            "runId": run_id,
            "gameId": game_id,
            "checkpoint": checkpoint,
            "candidateVersion": CANDIDATE_VERSION,
            "controlVersion": CONTROL_VERSION,
            "frozenDispersion": FROZEN_DISPERSION,
            "capturedAt": now,
        }
        try:
            ctx = compute_projection_context_fn(game)
            away_proj_runs, home_proj_runs = ctx.get("awayProjRuns"), ctx.get("homeProjRuns")
            cells = compute_paired_probabilities(away_proj_runs, home_proj_runs)
            records.append(dict(
                base,
                computationStatus=STATUS_SUCCESS,
                awayProjRuns=away_proj_runs,
                homeProjRuns=home_proj_runs,
                totalProj=ctx.get("totalProj"),
                cells=cells,
                failureReason=None,
            ))
        except Exception as exc:  # a bad/missing projection for one game must never break the cycle -- see docstring
            reason = f"shadow computation failed for gameId={game_id!r} checkpoint={checkpoint!r}: {exc}"
            failures.append({"gameId": game_id, "checkpoint": checkpoint, "reason": reason})
            records.append(dict(
                base,
                computationStatus=STATUS_FAILED,
                awayProjRuns=None,
                homeProjRuns=None,
                totalProj=None,
                cells=None,
                failureReason=reason,
            ))

    return records, failures
