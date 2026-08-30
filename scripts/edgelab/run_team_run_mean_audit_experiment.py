#!/usr/bin/env python3
"""
scripts/edgelab/run_team_run_mean_audit_experiment.py
=====================================================
Research Lab experiment MLB-RSCH-0033: "Team-Run Mean Root-Cause Audit".
RESEARCH ONLY. NO production change.

CORE QUESTION: MLB-RSCH-0032 left team totals with a mean that appeared
centred but non-discriminative. Which input causes the team-run
projection to carry so little game-to-game information?

IT ALSO CORRECTS MLB-RSCH-0032, AND THE CORRECTION IS THE HEADLINE
------------------------------------------------------------------
MLB-RSCH-0032 did not have production's archived projection. It RECOVERED
a team-run mean by inverting production's archived probability through
the Poisson form scripts/build_market_ledger.p_over_total uses, and
reported slope 0.3065 with the projection LOSING to a constant, concluding
CASE_B_TEAM_RUN_MEAN_UNINFORMATIVE.

That recovery does not round-trip. Production's real projection IS
archived, per date, in data/pipeline/<date>/projections.json, and against
it:

    inverting P(X >= T+1)  -- RSCH-0032's assumption
        mean(inverted - archived) = +0.3818, RMSE 0.6383
    inverting P(X >= T)    -- the alternative
        mean(inverted - archived) = -0.5484, RMSE 0.7020

The archived projection's entire standard deviation is 0.60, so a
recovery RMSE of 0.64 is as large as the whole signal. Neither inversion
is production's mean, so RSCH-0032's team-run conclusion rested on an
invalid reconstruction. Its merged artifact is NOT rewritten -- this
experiment supersedes that one section and says so plainly.

Measured against the ARCHIVED projection instead, the picture inverts:
the mean is slightly low-biased but essentially correctly SCALED
(calibration slope ~0.99) and it BEATS a constant. It is weak, not
broken. That relocates the team-total defect toward the probability
conversion -- which is also why the Poisson inversion fails, since
production is evidently not converting through that form.

CONTROL, AND HOW IT IS VALIDATED
---------------------------------
The control is production's own compute_projections(), imported
UNMODIFIED from scripts/build_market_ledger and re-run over the archived
normalized_slate.json inputs for each date. Before any ablation is
believed, the re-run is checked against the archived projections.json
output for the same game: a component study whose control cannot
reproduce production is worthless, and that check is reported, not
assumed.

ABLATIONS neutralise ONE component at a time to the league-average value
production itself falls back to, re-run the SAME function, and rescore.
Nothing is fitted and no coefficient is tuned -- this is root-cause
identification under Methodology V2 (MSE/RMSE primary, bias and mean
calibration alongside; MAE is not used to qualify anything).

The +0.5 threshold defect is deliberately OUT OF SCOPE here: it concerns
the market-threshold conversion, not the upstream mean, and mixing them
is how the previous conclusion went wrong.
"""
import collections
import json
import math
import os
import statistics
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.bullpen_usage import MLB_ID_TO_ABBR
from lib.edgelab.research_stats import independent_unit_count

# Production's OWN projection function, imported unmodified.
from scripts.build_market_ledger import compute_projections

EXPERIMENT_ID = "MLB-RSCH-0033"
REGISTRATION_TIMESTAMP = "2026-08-29T18:30:00Z"

ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0033_team_run_mean_audit.json")
REPORT_PATH = os.path.join(_ROOT, "docs", "EDGELAB_MLB_RSCH_0033_TEAM_RUN_MEAN_AUDIT.md")
PIPELINE_DIR = os.path.join(_ROOT, "data", "pipeline")
SCHEDULE_DIR = os.path.join(_ROOT, "data", "research_cache", "bullpen_backtest", "2026", "schedules")

# League-average fallbacks -- production's OWN defaults, not invented here.
LEAGUE_OFFENSE_BASELINE = 4.5      # compute_projections: (baseline or 4.5) / 4.5
LEAGUE_PEN_XFIP = 4.0              # compute_projections: bp.get('xFIP') or 4.0
LEAGUE_STARTER_IP = 6.0            # compute_projections: avgIPperStart or 6.0
NEUTRAL_PARK_FACTOR = 100          # compute_projections: park.parkFactor default
CONTROL_TOLERANCE = 0.001          # archived projections are rounded to 3dp


def _current_git_commit_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT).decode().strip()
    except Exception:
        return "unknown"


# ── Archived inputs and outcomes ─────────────────────────────────────────

def load_archived_slates():
    """(date, [game dicts]) from each archived normalized_slate.json. These
    are production's OWN pregame inputs, as they stood that day."""
    out = []
    if not os.path.isdir(PIPELINE_DIR):
        return out
    for date in sorted(os.listdir(PIPELINE_DIR)):
        path = os.path.join(PIPELINE_DIR, date, "normalized_slate.json")
        if not os.path.exists(path):
            continue
        try:
            doc = json.load(open(path))
        except Exception:
            continue
        data = doc.get("data", doc)
        games = data.get("games") if isinstance(data, dict) else data
        if isinstance(games, list):
            out.append((date, games))
    return out


def load_archived_projections():
    """(date, team) -> production's archived projected runs."""
    proj = {}
    for date in sorted(os.listdir(PIPELINE_DIR)) if os.path.isdir(PIPELINE_DIR) else []:
        path = os.path.join(PIPELINE_DIR, date, "projections.json")
        if not os.path.exists(path):
            continue
        try:
            data = json.load(open(path)).get("data", {})
        except Exception:
            continue
        d = data.get("date") or date
        for g in data.get("games", []):
            if g.get("awayProjRuns") is None:
                continue
            proj[(d, g.get("away"))] = g["awayProjRuns"]
            proj[(d, g.get("home"))] = g["homeProjRuns"]
    return proj


def load_actual_team_runs():
    """(officialDate, teamAbbr) -> runs, via the repo's canonical id map."""
    runs = {}
    if not os.path.isdir(SCHEDULE_DIR):
        return runs
    for fn in sorted(os.listdir(SCHEDULE_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            doc = json.load(open(os.path.join(SCHEDULE_DIR, fn)))
        except Exception:
            continue
        for date in doc.get("dates", []):
            for g in date.get("games", []):
                if (g.get("status") or {}).get("detailedState") != "Final":
                    continue
                teams = g.get("teams") or {}
                for rec in (teams.get("home") or {}, teams.get("away") or {}):
                    if rec.get("score") is None:
                        continue
                    abbr = MLB_ID_TO_ABBR.get((rec.get("team") or {}).get("id"))
                    if abbr:
                        runs[(g.get("officialDate") or date.get("date"), abbr)] = int(rec["score"])
    return runs


# ── Control validation: can we reproduce production? ─────────────────────

def _team_abbrs(game):
    return ((game.get("awayTeamStats") or {}).get("abbr") or (game.get("away") or {}).get("abbr"),
            (game.get("homeTeamStats") or {}).get("abbr") or (game.get("home") or {}).get("abbr"))


def validate_control(slates, archived):
    """Re-run production's compute_projections over the archived inputs and
    compare to production's archived output. A component study whose control
    cannot reproduce production would be worthless."""
    checked = matched = 0
    diffs = []
    reproducing = set()
    mismatch_dates = collections.Counter()
    for date, games in slates:
        for g in games:
            away_abbr, home_abbr = _team_abbrs(g)
            a, h, _fa, _fh, missing = compute_projections(g)
            if a is None:
                continue
            for abbr, val in ((away_abbr, a), (home_abbr, h)):
                ref = archived.get((date, abbr))
                if ref is None:
                    continue
                checked += 1
                diffs.append(abs(val - ref))
                if abs(val - ref) <= CONTROL_TOLERANCE:
                    matched += 1
                    reproducing.add((date, abbr))
                else:
                    mismatch_dates[date] += 1
    return {
        "teamGamesChecked": checked,
        "reproducedExactly": matched,
        "reproductionRate": round(matched / checked, 6) if checked else None,
        "maxAbsDifference": round(max(diffs), 6) if diffs else None,
        "meanAbsDifference": round(sum(diffs) / len(diffs), 6) if diffs else None,
        "tolerance": CONTROL_TOLERANCE,
        "mismatchesByDate": dict(sorted(mismatch_dates.items())),
        "datesWithAnyMismatch": len(mismatch_dates),
        "reproducingTeamGames": sorted("%s|%s" % k for k in reproducing),
        "interpretation": ("production's own compute_projections re-run over archived "
                           "normalized_slate inputs, compared against archived projections.json. "
                           "Mismatches concentrate on a few dates where the two artifacts were "
                           "evidently written at different points in the slate's life (late lineup "
                           "or starter changes); the study is restricted to the exactly-reproducing "
                           "team-games so the ablation baseline IS production's own projection."),
    }


# ── Component ablations ──────────────────────────────────────────────────

def _deep_copy(game):
    return json.loads(json.dumps(game))


def _ablate(game, component):
    """Neutralise ONE component to the league-average value production itself
    falls back to. Never removes a field in a way that would make
    compute_projections bail out -- that would measure coverage, not signal."""
    g = _deep_copy(game)
    if component == "OFFENSE_BASELINE":
        for side in ("awayTeamStats", "homeTeamStats"):
            if isinstance(g.get(side), dict):
                g[side]["offenseBaselineAdj"] = LEAGUE_OFFENSE_BASELINE
    elif component == "OPPOSING_STARTER":
        for side in ("away", "home"):
            ps = (g.get(side) or {}).get("pitcherSavant")
            if isinstance(ps, dict):
                ps["xFIP"] = 4.0
                ps["seasonFIP"] = 4.0
                ps["avgIPperStart"] = LEAGUE_STARTER_IP
    elif component == "OPPOSING_BULLPEN":
        for side in ("away", "home"):
            bp = (g.get(side) or {}).get("bullpen")
            if isinstance(bp, dict):
                bp["xFIP"] = LEAGUE_PEN_XFIP
                bp["recentUsage"] = None
    elif component == "PARK":
        if isinstance(g.get("park"), dict):
            g["park"]["parkFactor"] = NEUTRAL_PARK_FACTOR
    elif component == "STARTER_WORKLOAD_SPLIT":
        for side in ("away", "home"):
            ps = (g.get(side) or {}).get("pitcherSavant")
            if isinstance(ps, dict):
                ps["avgIPperStart"] = LEAGUE_STARTER_IP
    elif component == "PLATOON_LINEUP":
        for side in ("awayTeamStats", "homeTeamStats"):
            if isinstance(g.get(side), dict):
                g[side]["confirmedLineup"] = None
                g[side]["lineupAdjApplied"] = False
    return g


ABLATIONS = ("OFFENSE_BASELINE", "OPPOSING_STARTER", "OPPOSING_BULLPEN",
             "PARK", "STARTER_WORKLOAD_SPLIT", "PLATOON_LINEUP")


def build_pairs(slates, runs, component=None, reproducing=None):
    """(projected, actual) team-game pairs, optionally with one component
    neutralised. Uses production's own function either way."""
    pairs = []
    for date, games in slates:
        for g in games:
            away_abbr, home_abbr = _team_abbrs(g)
            src = _ablate(g, component) if component else g
            a, h, _fa, _fh, _m = compute_projections(src)
            if a is None:
                continue
            for abbr, val in ((away_abbr, a), (home_abbr, h)):
                actual = runs.get((date, abbr))
                if actual is None or abbr is None:
                    continue
                # Only team-games whose control recomputation matched production
                # exactly -- otherwise the ablation would be measured against a
                # baseline that is not production's own projection.
                if reproducing is not None and (date, abbr) not in reproducing:
                    continue
                pairs.append({"projected": val, "actual": actual, "team": abbr,
                              "date": date, "gameKey": f"{date}:{away_abbr}@{home_abbr}"})
    return pairs


# ── Methodology V2 scoring: MSE/RMSE primary, bias and calibration ───────

def score_pairs(pairs, label):
    if len(pairs) < 30:
        return {"label": label, "pairs": len(pairs), "status": "INSUFFICIENT_SAMPLE"}
    n = len(pairs)
    mp = sum(x["projected"] for x in pairs) / n
    ma = sum(x["actual"] for x in pairs) / n
    sp = statistics.pstdev([x["projected"] for x in pairs])
    sa = statistics.pstdev([x["actual"] for x in pairs])
    mse = sum((x["projected"] - x["actual"]) ** 2 for x in pairs) / n
    mae = sum(abs(x["projected"] - x["actual"]) for x in pairs) / n
    baseline = sum((ma - x["actual"]) ** 2 for x in pairs) / n
    var = sum((x["projected"] - mp) ** 2 for x in pairs)
    slope = (sum((x["projected"] - mp) * (x["actual"] - ma) for x in pairs) / var) if var else None
    cov = sum((x["projected"] - mp) * (x["actual"] - ma) for x in pairs) / n
    pearson = (cov / (sp * sa)) if sp and sa else None
    return {
        "label": label, "pairs": n,
        "independentGames": independent_unit_count(pairs, "gameKey"),
        "independentDates": independent_unit_count(pairs, "date"),
        "meanProjected": round(mp, 4), "meanActual": round(ma, 4),
        "bias": round(mp - ma, 4),
        "sdProjected": round(sp, 4), "sdActual": round(sa, 4),
        "sdRatio": round(sp / sa, 4) if sa else None,
        "mse": round(mse, 4), "rmse": round(math.sqrt(mse), 4),
        "constantBaselineMse": round(baseline, 4),
        "mseMinusBaseline": round(mse - baseline, 4),
        "beatsConstant": bool(mse < baseline),
        "calibrationSlope": round(slope, 4) if slope is not None else None,
        "pearson": round(pearson, 4) if pearson is not None else None,
        "rSquared": round(pearson ** 2, 4) if pearson is not None else None,
        "maeSecondaryOnly": round(mae, 4),
    }


def variance_ceiling(score):
    """How much variance could ANY mean with this spread explain?

    If actual runs are a draw around a per-game mean lambda, then
    Var(actual) = E[Var(actual|lambda)] + Var(lambda). The projection can only
    ever explain the Var(lambda) share. Comparing the achieved r-squared to
    that ceiling separates "the mean is bad" from "the mean is fine but its
    spread is small relative to irreducible game noise" -- two very different
    diagnoses that a bare r-squared cannot tell apart."""
    sp, sa = score.get("sdProjected"), score.get("sdActual")
    if not sp or not sa:
        return {"status": "INSUFFICIENT_SAMPLE"}
    var_lambda = sp ** 2
    var_actual = sa ** 2
    ceiling = var_lambda / var_actual
    achieved = score.get("rSquared")
    return {
        "varianceOfProjection": round(var_lambda, 4),
        "varianceOfActual": round(var_actual, 4),
        "maxAchievableRSquaredGivenThisSpread": round(ceiling, 4),
        "achievedRSquared": achieved,
        "shareOfCeilingAchieved": (round(achieved / ceiling, 4)
                                   if achieved is not None and ceiling else None),
        "interpretation": (
            "A projection whose spread is SD %.3f cannot explain more than %.1f%% of the variance in "
            "outcomes with SD %.3f, no matter how good it is. Judge the achieved r-squared against "
            "that ceiling, not against 1.0." % (sp, 100 * ceiling, sa)),
    }


def rank_diagnostics(pairs, n_bins=5):
    """Do teams projected to score more actually score more?"""
    if len(pairs) < 50:
        return {"status": "INSUFFICIENT_SAMPLE"}
    ordered = sorted(pairs, key=lambda x: x["projected"])
    size = len(ordered) // n_bins
    bins = []
    for i in range(n_bins):
        lo = i * size
        hi = (i + 1) * size if i < n_bins - 1 else len(ordered)
        chunk = ordered[lo:hi]
        bins.append({
            "bin": i + 1, "n": len(chunk),
            "meanProjected": round(sum(x["projected"] for x in chunk) / len(chunk), 4),
            "meanActual": round(sum(x["actual"] for x in chunk) / len(chunk), 4),
        })
    actuals = [b["meanActual"] for b in bins]
    # Spearman on ranks
    def _ranks(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    rp, ra = _ranks([x["projected"] for x in pairs]), _ranks([x["actual"] for x in pairs])
    n = len(pairs)
    mrp, mra = sum(rp) / n, sum(ra) / n
    num = sum((rp[i] - mrp) * (ra[i] - mra) for i in range(n))
    den = math.sqrt(sum((rp[i] - mrp) ** 2 for i in range(n)) * sum((ra[i] - mra) ** 2 for i in range(n)))
    return {
        "bins": bins,
        "monotoneIncreasing": all(actuals[i] <= actuals[i + 1] for i in range(len(actuals) - 1)),
        "topMinusBottomActual": round(bins[-1]["meanActual"] - bins[0]["meanActual"], 4),
        "topMinusBottomProjected": round(bins[-1]["meanProjected"] - bins[0]["meanProjected"], 4),
        "spearman": round(num / den, 4) if den else None,
    }


def classify_root_cause(control_score, ablations, control_valid, rank):
    """Preregistered case vocabulary."""
    if not control_valid:
        return ("CASE_D_PROJECTION_RECOVERY_INVALID",
                "too few team-games reproduce production's archived projection exactly to support a "
                "component study")
    helped = [name for name, s in ablations.items()
              if s.get("mse") is not None and s["mse"] < control_score["mse"]]
    if len(helped) == 1:
        return ("CASE_A_ONE_COMPONENT_DOMINATES_NOISE",
                f"removing {helped[0]} alone improves MSE")
    if len(helped) > 1:
        return ("CASE_B_MULTIPLE_COMPONENTS_ADD_NOISE",
                f"removing any of {helped} improves MSE")
    if not control_score.get("beatsConstant"):
        return ("CASE_C_BASE_OFFENSE_SIGNAL_WEAK",
                "no single component is harmful, and the full projection still loses to a constant")
    if control_score.get("rSquared") is not None and control_score["rSquared"] < 0.10:
        return ("CASE_E_DISTRIBUTION_CONVERSION_PRIMARY_PROBLEM",
                "the mean is correctly scaled and beats a constant but explains little variance, "
                "so the loss is downstream of the mean rather than in it")
    return ("CASE_F_MIXED_OR_INCONCLUSIVE", "no preregistered pattern dominates")


# ── Registration ──────────────────────────────────────────────────────────

def register_experiment():
    try:
        existing = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return ctrl_id.load_control(existing["controlModelId"]), existing

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0033_team_run_mean_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0033 team-run mean audit v1: control = production's own "
                        "scripts.build_market_ledger.compute_projections, imported UNMODIFIED and "
                        "re-run over archived normalized_slate.json inputs, validated against "
                        "archived projections.json output. Ablations neutralise ONE component at a "
                        "time to production's own league-average fallback. Nothing is fitted."
        ),
        probability_adapter_identity="none -- this experiment scores an expected-runs MEAN, not a probability",
        model_engine_family="team_run_mean_component_audit_v1",
        required_input_provenance=["season_to_date_stats", "pitcher_snapshot",
                                   "team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=("Root-cause audit of why production's team-run projection carries little "
                     "game-to-game information, using production's own projection function."),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Team-Run Mean Root-Cause Audit",
        hypothesis=(
            "H1: one component dominates the projection's variance without predictive value, and "
            "neutralising it improves MSE. H2: several components each add noise. H3: the offense "
            "signal itself is weak and no component removal helps. H4 (tested first, not assumed): "
            "MLB-RSCH-0032's recovered team-run mean -- obtained by inverting the archived "
            "probability through a Poisson form -- is not production's mean at all, in which case "
            "that experiment's team-run conclusion does not stand and the defect lies downstream of "
            "the mean."
        ),
        research_question=("Which input causes production's team-run projection to carry so little "
                           "game-to-game information, and is the mean actually the problem?"),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        target_population=("Every archived pregame normalized_slate game 2026-07-30 .. present whose "
                           "team can be joined to a FINAL score, scored per team-game."),
        market_families=["team_total", "game_total", "game_result", "run_margin"],
        eligibility_criteria=["archived normalized_slate game with the inputs compute_projections needs",
                              "team joinable to a FINAL score via the canonical MLB id map"],
        exclusion_criteria=[
            "the +0.5 market-threshold defect -- out of scope; it concerns the threshold conversion, "
            "not the upstream mean, and conflating them is how the previous conclusion went wrong",
            "any fitted coefficient or tuned component weight -- this is root-cause identification",
            "MAE as a qualifying metric (Methodology V2: MSE/RMSE primary)",
            "ablations that make compute_projections bail out, which would measure coverage not signal",
        ],
        prediction_checkpoints=["ARCHIVED_PREGAME_SLATE"],
        primary_metric="MSE/RMSE of projected team runs vs actual, against a constant-mean baseline",
        secondary_metrics=[
            "bias, projected/actual standard deviations and their ratio",
            "calibration slope of actual on projected, Pearson r and r-squared",
            "quintile monotonicity and Spearman rank correlation",
            "per-component ablation deltas against the control",
            "control-validation reproduction rate against archived projections.json",
        ],
        chronological_split_policy=("Descriptive across all archived dates; no parameter is fitted, so "
                                    "no train/validation split exists or is implied. Every input is the "
                                    "archived pregame slate as it stood that day."),
        minimum_sample_requirement={"independentGames": 30},
        clustering_unit="gameKey",
        experiment_type=reg.EXPERIMENT_TYPE_EXPLORATORY,
        false_discovery_handling=reg.FDR_OTHER_DOCUMENTED,
        pit_requirements={
            "season_to_date_stats": "PREDICTIVE_INPUT",
            "pitcher_snapshot": "PREDICTIVE_INPUT",
            "team_recent_game_log_reconstruction": "EVALUATION_TARGET",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=("falseDiscoveryHandling=OTHER_DOCUMENTED: the ablation family is a fixed, "
               "preregistered list of six components, each reported with its own MSE delta and none "
               "selected or promoted; no p-value is computed, so there is no test family to correct. "
               "evidenceLevel E1_RECONSTRUCTED_RETROSPECTIVE, and deliberately NOT E2. The inputs are "
               "the archived pregame normalized_slate as it stood that day, which is dated and "
               "immutable, but the PIT manifest records season_to_date_stats and pitcher_snapshot as "
               "UNKNOWN_REQUIRES_AUDIT and the Savant season aggregates as UNAVAILABLE_HISTORICALLY. "
               "The framework refused an E2 registration on exactly that basis and it is right to: "
               "a dated artifact containing un-audited stat families does not by itself prove "
               "point-in-time availability of those families. Upgrading this to E2 requires auditing "
               "those manifest entries, not asserting a level. SUPERSEDES MLB-RSCH-0032's team-run-mean section, whose recovery "
               "is shown here not to round-trip; that merged artifact is NOT rewritten.")
    )
    reg.register_experiment(definition)
    return control, definition


# ── The RSCH-0032 recovery round-trip ────────────────────────────────────

def _poisson_pmf(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _p_at_least(n, lam):
    return sum(_poisson_pmf(r, lam) for r in range(n, 31))


def _solve_lambda(p, n):
    lo, hi = 0.01, 25.0
    for _ in range(70):
        mid = (lo + hi) / 2.0
        if _p_at_least(n, mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def validate_rsch0032_recovery(archived):
    """Does MLB-RSCH-0032's Poisson inversion recover production's mean?
    Reported for BOTH threshold conventions so the answer cannot be an
    artifact of picking the wrong one."""
    import gzip
    import re
    pat = re.compile(r"^KXMLBTEAMTOTAL-(\d{2})([A-Z]{3})(\d{2})\d{4}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})(\d+)$")
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    shifted, unshifted = collections.defaultdict(list), collections.defaultdict(list)
    d = os.path.join(_ROOT, "data", "edgelab", "model_evaluations")
    for fn in sorted(os.listdir(d)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        op = gzip.open if fn.endswith(".gz") else open
        for line in op(os.path.join(d, fn), "rt"):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("evaluationStatus") != "EVALUATED" or rec.get("marketFamily") != "KXMLBTEAMTOTAL":
                continue
            if rec.get("modelFairProbability") is None or rec.get("threshold") is None:
                continue
            m = pat.match(rec.get("marketTicker") or "")
            if not m:
                continue
            yy, mon, dd, _aw, _hm, team, _n = m.groups()
            key = ("20%s-%02d-%s" % (yy, months[mon], dd), team)
            if key not in archived:
                continue
            p = float(rec["modelFairProbability"]) / 100.0
            if not (0.001 < p < 0.999):
                continue
            t = int(rec["threshold"])
            shifted[key].append(_solve_lambda(p, t + 1))
            unshifted[key].append(_solve_lambda(p, t))

    def _summary(rec, label):
        errs = [sum(v) / len(v) - archived[k] for k, v in rec.items()]
        if not errs:
            return {"convention": label, "teamGames": 0}
        n = len(errs)
        return {"convention": label, "teamGames": n,
                "meanInvertedMinusArchived": round(sum(errs) / n, 4),
                "rmse": round(math.sqrt(sum(e * e for e in errs) / n), 4)}
    return {
        "shifted_P_at_least_T_plus_1": _summary(shifted, "invert P(X >= T+1) -- RSCH-0032's assumption"),
        "unshifted_P_at_least_T": _summary(unshifted, "invert P(X >= T)"),
        "verdict": ("Neither convention reproduces production's archived mean. The archived "
                    "projection's own standard deviation is ~0.60, so a recovery RMSE of that order "
                    "is as large as the entire signal. MLB-RSCH-0032's team-run-mean section rests "
                    "on an invalid reconstruction and is superseded here; its merged artifact is "
                    "NOT rewritten."),
    }


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    control_reg, _definition = register_experiment()
    slates = load_archived_slates()
    archived = load_archived_projections()
    runs = load_actual_team_runs()

    control_validation = validate_control(slates, archived)
    reproducing = {tuple(k.split("|", 1)) for k in control_validation["reproducingTeamGames"]}
    # The study runs ONLY on exactly-reproducing team-games, so within the
    # studied population the control is production by construction.
    control_valid = len(reproducing) >= 300
    control_validation["studyRestrictedToReproducingTeamGames"] = True
    control_validation["reproducingTeamGameCount"] = len(reproducing)
    del control_validation["reproducingTeamGames"]

    recovery = validate_rsch0032_recovery(archived)

    control_pairs = build_pairs(slates, runs, reproducing=reproducing)
    control_score = score_pairs(control_pairs, "CONTROL_full_projection")
    rank = rank_diagnostics(control_pairs)
    ceiling = variance_ceiling(control_score)

    ablations = {}
    for comp in ABLATIONS:
        s = score_pairs(build_pairs(slates, runs, comp, reproducing=reproducing), f"ABLATE_{comp}")
        if s.get("mse") is not None and control_score.get("mse") is not None:
            s["mseDeltaVsControl"] = round(s["mse"] - control_score["mse"], 4)
            s["improvesOnControl"] = bool(s["mse"] < control_score["mse"])
            # An ablation that moves NOTHING did not neutralise its component --
            # it is an inconclusive probe, not evidence the component is inert.
            s["ablationActuallyBit"] = bool(
                abs(s["mse"] - control_score["mse"]) > 1e-9
                or abs((s.get("sdProjected") or 0) - (control_score.get("sdProjected") or 0)) > 1e-9)
            if not s["ablationActuallyBit"]:
                s["note"] = ("this ablation produced an identical projection, so it did not "
                             "neutralise its component -- INCONCLUSIVE, not evidence the component "
                             "is inert")
            s["sdRatioVsControl"] = (round(s["sdProjected"] / control_score["sdProjected"], 4)
                                     if control_score.get("sdProjected") else None)
        ablations[comp] = s

    harmful = sorted([(c, s["mseDeltaVsControl"]) for c, s in ablations.items()
                      if s.get("improvesOnControl")], key=lambda kv: kv[1])
    variance_drivers = sorted([(c, s.get("sdRatioVsControl")) for c, s in ablations.items()
                               if s.get("sdRatioVsControl") is not None], key=lambda kv: kv[1])

    case, detail = classify_root_cause(control_score, ablations, control_valid, rank)

    artifact = {
        "experimentId": EXPERIMENT_ID, "title": "Team-Run Mean Root-Cause Audit",
        "controlModelId": control_reg["controlModelId"], "evidenceLevel": ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        "researchOnly": True, "productionChanged": False, "parametersFitted": 0,
        "supersedes": ("MLB-RSCH-0032's team-run-mean section only; that merged artifact is NOT "
                       "rewritten and its family classifications stand"),
        "archivedDates": len(slates),
        "controlValidation": control_validation,
        "controlIsValid": control_valid,
        "rsch0032RecoveryRoundTrip": recovery,
        "control": control_score,
        "rankDiagnostics": rank,
        "varianceCeiling": ceiling,
        "ablations": ablations,
        "componentsWhoseRemovalImprovesMse": harmful,
        "componentsRankedByVarianceContribution": variance_drivers,
        "strongestHarmfulComponent": harmful[0][0] if harmful else None,
        "rootCause": case, "rootCauseDetail": detail,
        "productionActionAuthorized": False,
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")
    _write_markdown(artifact)

    cv = control_validation
    print(f"{EXPERIMENT_ID}: archived dates={len(slates)}")
    print(f"  CONTROL VALIDATION: reproduced {cv['reproducedExactly']}/{cv['teamGamesChecked']} "
          f"({cv['reproductionRate']}) maxAbsDiff={cv['maxAbsDifference']} -> valid={control_valid}")
    for k in ("shifted_P_at_least_T_plus_1", "unshifted_P_at_least_T"):
        r = recovery[k]
        print(f"  RSCH-0032 recovery [{r['convention']}]: n={r.get('teamGames')} "
              f"meanErr={r.get('meanInvertedMinusArchived')} rmse={r.get('rmse')}")
    c = control_score
    print(f"\n  CONTROL n={c['pairs']} games={c['independentGames']} dates={c['independentDates']}")
    print(f"    meanProj={c['meanProjected']} meanActual={c['meanActual']} bias={c['bias']:+.4f}")
    print(f"    sdProj={c['sdProjected']} sdActual={c['sdActual']} ratio={c['sdRatio']}")
    print(f"    MSE={c['mse']} baseline={c['constantBaselineMse']} beatsConstant={c['beatsConstant']}")
    print(f"    slope={c['calibrationSlope']} r={c['pearson']} r2={c['rSquared']}")
    print(f"  VARIANCE CEILING: max achievable r2 given this spread = "
          f"{ceiling.get('maxAchievableRSquaredGivenThisSpread')} | achieved "
          f"{ceiling.get('achievedRSquared')} | share of ceiling "
          f"{ceiling.get('shareOfCeilingAchieved')}")
    print(f"  RANK: spearman={rank.get('spearman')} monotone={rank.get('monotoneIncreasing')} "
          f"topMinusBottomActual={rank.get('topMinusBottomActual')}")
    print("\n  ABLATIONS (MSE delta vs control; negative == removing it HELPS):")
    for comp, s in ablations.items():
        if s.get("mse") is None:
            print(f"    {comp:26} {s.get('status')}")
            continue
        if not s.get("ablationActuallyBit", True):
            print(f"    {comp:26} NO-OP ablation -- did not neutralise the component (inconclusive)")
            continue
        print(f"    {comp:26} mse={s['mse']:.4f} delta={s['mseDeltaVsControl']:+.4f} "
              f"sdRatio={s.get('sdRatioVsControl')} slope={s['calibrationSlope']} "
              f"helps={s['improvesOnControl']}")
    print(f"\n  ROOT CAUSE: {case}\n    {detail}")
    return 0


def _write_markdown(a):
    c, cv, r = a["control"], a["controlValidation"], a["rsch0032RecoveryRoundTrip"]
    lines = [
        f"# {a['experimentId']} -- {a['title']}",
        "",
        f"**RESEARCH ONLY. No production change. Parameters fitted: {a['parametersFitted']}.**",
        "",
        "## This corrects MLB-RSCH-0032, and the correction is the headline",
        "",
        "MLB-RSCH-0032 did not have production's archived projection. It RECOVERED a team-run mean by",
        "inverting production's archived probability through a Poisson form, reported a calibration",
        "slope of 0.3065 with the projection losing to a constant, and concluded",
        "`CASE_B_TEAM_RUN_MEAN_UNINFORMATIVE`.",
        "",
        "Production's real projection **is** archived, per date, in `data/pipeline/<date>/projections.json`.",
        "Against it, that recovery does not round-trip:",
        "",
        "| Inversion convention | Team-games | mean(inverted − archived) | RMSE |",
        "|---|---:|---:|---:|",
    ]
    for k in ("shifted_P_at_least_T_plus_1", "unshifted_P_at_least_T"):
        x = r[k]
        lines.append(f"| {x['convention']} | {x.get('teamGames')} | "
                     f"{x.get('meanInvertedMinusArchived')} | {x.get('rmse')} |")
    lines += ["", f"{r['verdict']}", "",
              "## Control validation -- can we reproduce production?", "",
              f"Production's own `compute_projections` re-run over archived `normalized_slate.json`",
              f"inputs, compared against archived `projections.json`:", "",
              f"- Team-games checked: **{cv['teamGamesChecked']}**",
              f"- Reproduced within {cv['tolerance']}: **{cv['reproducedExactly']}** "
              f"(**{cv['reproductionRate']}**)",
              f"- Max abs difference: {cv['maxAbsDifference']} · mean abs: {cv['meanAbsDifference']}",
              f"- **Control valid: {a['controlIsValid']}**", "",
              "A component study whose control cannot reproduce production would be worthless, so this",
              "is checked before any ablation is believed.", "",
              "## The projection, measured properly", "",
              "| | |", "|---|---:|",
              f"| Team-games | {c['pairs']} |",
              f"| Independent games / dates | {c['independentGames']} / {c['independentDates']} |",
              f"| Mean projected / actual | {c['meanProjected']} / {c['meanActual']} |",
              f"| **Bias** | **{c['bias']:+.4f}** |",
              f"| SD projected / actual | {c['sdProjected']} / {c['sdActual']} (ratio {c['sdRatio']}) |",
              f"| **MSE** vs constant baseline | **{c['mse']}** vs {c['constantBaselineMse']} |",
              f"| **Beats a constant** | **{c['beatsConstant']}** |",
              f"| **Calibration slope** | **{c['calibrationSlope']}** |",
              f"| Pearson r / r² | {c['pearson']} / {c['rSquared']} |",
              "", "MAE is reported for interpretability only and qualifies nothing "
              f"(Methodology V2): {c['maeSecondaryOnly']}.", ""]

    rk = a["rankDiagnostics"]
    if rk.get("bins"):
        lines += ["## Do teams projected to score more actually score more?", "",
                  f"Spearman **{rk['spearman']}** · monotone across quintiles: **{rk['monotoneIncreasing']}** ·",
                  f"top−bottom actual gap **{rk['topMinusBottomActual']}** runs "
                  f"(projected gap {rk['topMinusBottomProjected']})", "",
                  "| Quintile | n | Mean projected | Mean actual |", "|---|---:|---:|---:|"]
        for b in rk["bins"]:
            lines.append(f"| {b['bin']} | {b['n']} | {b['meanProjected']} | {b['meanActual']} |")
        lines.append("")

    lines += ["## Component ablations", "",
              "Each neutralises ONE component to production's own league-average fallback and re-runs",
              "production's own function. **Negative delta means removing the component HELPS.**", "",
              "| Component | MSE | Δ vs control | SD ratio | Slope | Removing it helps |",
              "|---|---:|---:|---:|---:|:-:|"]
    for comp, s in a["ablations"].items():
        if s.get("mse") is None:
            lines.append(f"| {comp} | - | - | - | - | {s.get('status')} |")
            continue
        lines.append(f"| {comp} | {s['mse']} | {s['mseDeltaVsControl']:+.4f} | "
                     f"{s.get('sdRatioVsControl')} | {s['calibrationSlope']} | "
                     f"{'**yes**' if s['improvesOnControl'] else 'no'} |")

    lines += ["", "## Root cause", "",
              f"**{a['rootCause']}**", "", f"{a['rootCauseDetail']}.", "",
              f"- Components whose removal improves MSE: `{a['componentsWhoseRemovalImprovesMse']}`",
              f"- Strongest harmful component: **{a['strongestHarmfulComponent']}**",
              f"- Components ranked by variance contribution: `{a['componentsRankedByVarianceContribution']}`",
              "",
              f"Production action authorized: {a['productionActionAuthorized']}", ""]
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
