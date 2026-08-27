#!/usr/bin/env python3
"""
scripts/edgelab/run_proxy_vs_pinnacle_experiment.py
====================================================================
Research Lab, experiment MLB-RSCH-0008: "Proxy Model vs. Historical
Pinnacle". The first true multi-season market backtest in this
program -- does a strictly PIT-safe reconstructed MLB proxy model add
predictive value relative to historical Pinnacle fair probabilities?

NOT a claim the proxy recreates production's actual historical
probability -- see lib.edgelab.backtest.proxy_model's own module
docstring and docs/EDGELAB_HISTORICAL_SHARP_MARKET_AUDIT.md §6
(every family classifies C. PROXY_MODEL_POSSIBLE, never A.
EXACT_PIT_RECONSTRUCTABLE). RESEARCH ONLY. NO production changes.

WHAT THIS REUSES:
  - lib.edgelab.backtest.pinnacle_reconstruction -- per-game snapshot
    selection (strict pregame cutoff, MAX_MINUTES_BEFORE_START), two-
    sided de-vig, exact-line total matching.
  - lib.edgelab.backtest.proxy_model -- team baselines (MLB-RSCH-0005's
    own season_to_date_rate), expected runs, and the proxy probability
    functions (p_team_wins/p_over_total, reused UNCHANGED from
    scripts/build_market_ledger.py -- the same reuse MLB-RSCH-0002
    already established).
  - lib.edgelab.backtest.bullpen_backtest_reconstruction's
    extract_team_games_from_schedule -- the SAME already-committed
    MLB-RSCH-0003 schedule cache, read-only, reused a FOURTH time (by
    MLB-RSCH-0004, MLB-RSCH-0005, and now this) for team game logs and
    final scores. No new MLB-side fetch.
  - lib.edgelab.paired_evaluation.pair_eligible_observations /
    evaluate_probability_model_pair -- repurposed for PROXY-vs-
    PINNACLE scoring, the exact pattern MLB-RSCH-0001 already
    established for MODEL-vs-MARKET (see that experiment's own
    _model_vs_market_pairing docstring for the precedent this mirrors).
  - lib.edgelab.research_stats -- Brier/log-loss, calibration error,
    independent_unit_count, sample_size_status, game-clustered
    bootstrap.

DEVELOPMENT/VALIDATION/HOLDOUT: 2022-2024 / 2025 (if confirmed
reachable, else the documented alternative split -- see
DEV_SEASONS/VALIDATION_SEASONS below, fixed BEFORE any result is
examined) / 2026 (locked). run_hypothesis_tests() is one fixed
function applied unchanged to all three groups, same holdout-isolation
discipline as every prior MLB-RSCH milestone.

ONE dev-fit parameter (HOME_FIELD_RUNS_ADJUSTMENT): fit once via
lib.edgelab.backtest.proxy_model.fit_home_field_adjustment on
DEVELOPMENT rows only, then frozen and reused UNCHANGED for
validation/holdout -- proven by TestFrozenProxyUnchanged (object-
identity / value-equality check across all three splits).
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_SCRIPTS_DIR = os.path.join(_ROOT, "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
_BACKTEST_SCRIPTS_DIR = os.path.join(_SCRIPTS_DIR, "edgelab", "backtest")
if _BACKTEST_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _BACKTEST_SCRIPTS_DIR)

from lib.edgelab.backtest.bullpen_backtest_reconstruction import extract_team_games_from_schedule
from lib.edgelab.backtest.pinnacle_reconstruction import (
    select_closest_pregame_snapshot,
    devig_two_sided,
    matched_total_line,
    MAX_MINUTES_BEFORE_START,
)
from lib.edgelab.backtest.proxy_model import (
    team_baseline,
    expected_runs,
    game_ml_proxy_probability,
    game_total_proxy_probability,
    fit_home_field_adjustment,
)
from lib.edgelab.bullpen_usage import MLB_TEAM_ID_MAP
from lib.edgelab import experiment_registry as reg
from lib.edgelab import dispositions as disp
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import paired_evaluation as pe
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.research_stats import (
    DEFAULT_BOOTSTRAP_SEED,
    brier_and_log_loss_summary,
    expected_calibration_error,
    independent_unit_count,
    sample_size_status,
)

import fetch_mlb_multiseason_bullpen_cache as schedule_fetcher  # noqa: E402
import clv_update as clv  # noqa: E402

EXPERIMENT_ID = "MLB-RSCH-0008"
REGISTRATION_TIMESTAMP = "2026-08-27T23:00:00Z"

DEV_SEASONS = [2022, 2023, 2024]
VALIDATION_SEASONS = [2025]
HOLDOUT_SEASONS = [2026]
ALL_SEASONS = DEV_SEASONS + VALIDATION_SEASONS + HOLDOUT_SEASONS

MIN_GAMES_EXPLORATORY = 50  # same interpretability floor MLB-RSCH-0001 established for this corpus track
MIN_GAMES_CONFIDENT = 50

DISAGREEMENT_BANDS = (
    ("<2.5%", 0.0, 0.025),
    ("2.5-5%", 0.025, 0.05),
    ("5-7.5%", 0.05, 0.075),
    ("7.5-10%", 0.075, 0.10),
    ("10%+", 0.10, None),
)
PRICE_BANDS = (
    ("0-20%", 0.0, 0.20),
    ("20-35%", 0.20, 0.35),
    ("35-50%", 0.35, 0.50),
    ("50-65%", 0.50, 0.65),
    ("65-80%", 0.65, 0.80),
    ("80-100%", 0.80, 1.0),
)

PINNACLE_CACHE_ROOT = os.path.join(_ROOT, "data", "research_cache", "pinnacle_historical")
EDGELAB_DIR = os.path.join(_ROOT, "data", "edgelab")

_ID_TO_ABBR = {v: k for k, v in MLB_TEAM_ID_MAP.items()}


# ── Registration ──────────────────────────────────────────────────────────

def _current_git_commit_sha():
    import subprocess
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def register_experiment():
    """Registers MLB-RSCH-0008 BEFORE any cache is loaded or result is
    computed -- first call in main(), structurally enforced by this
    script's own test file's TestPreregistrationOrdering."""
    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0008_proxy_model_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(config_text="MLB-RSCH-0008 proxy model v1: season_to_date_rate offense/run-prevention baselines + dev-fit home-field runs adjustment + p_team_wins/p_over_total"),
        probability_adapter_identity="lib.edgelab.backtest.proxy_model.game_ml_proxy_probability;game_total_proxy_probability",
        model_engine_family="pit_safe_research_proxy_v1_pythagorean_poisson",
        required_input_provenance=["team_recent_game_log_reconstruction"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=(
            "A NEW, explicitly-labeled historical RESEARCH proxy -- not a reconstruction of any production model. "
            "Built entirely from PIT-safe season-to-date team offense/run-prevention baselines (MLB-RSCH-0005's own "
            "reconstruction primitive) combined via production's own p_team_wins/p_over_total Poisson math (reused "
            "unchanged, the same reuse MLB-RSCH-0002 established), plus one dev-fit home-field runs constant."
        ),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Proxy Model vs. Historical Pinnacle",
        hypothesis=(
            "H1: a PIT-safe reconstructed proxy model's disagreement with Pinnacle's de-vigged fair probability "
            "contains real predictive information -- proxy accuracy should not simply degrade as disagreement "
            "grows. H2: disagreement direction (proxy-favors-favorite vs proxy-favors-underdog; proxy-bullish-over "
            "vs proxy-bullish-under) may be asymmetrically informative. H3: proxy value, if any, may be regime-"
            "specific (concentrated in certain Pinnacle fair-probability bands) rather than uniform."
        ),
        research_question=(
            "Does a strictly PIT-safe reconstructed MLB proxy model add predictive value relative to historical "
            "Pinnacle fair probabilities over a large multi-season sample -- for game moneyline and game total, "
            "the two cleanest, primary market families? Evaluated across development (2022-2024), validation "
            "(2025 if confirmed reachable, else a documented alternative split), and a locked 2026 holdout."
        ),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E2_PIT_HISTORICAL,
        target_population=(
            "MLB regular-season games 2022-2026 with a matched, causally-valid pregame Pinnacle snapshot "
            "(strictly before scheduled first pitch, within MAX_MINUTES_BEFORE_START) for game moneyline and/or "
            "game total, matched to the existing MLB-RSCH-0003 schedule cache by team abbreviation and date."
        ),
        market_families=["game_result", "game_total"],
        eligibility_criteria=[
            "a Pinnacle h2h and/or totals market observed strictly before this game's own scheduled start, within MAX_MINUTES_BEFORE_START",
            "both teams have >= MIN_PRIOR_GAMES_FOR_BASELINE prior completed games this season (proxy baseline eligibility)",
            "for the total market: both Over and Under quoted at the exact same line",
        ],
        exclusion_criteria=[
            "no qualifying pregame snapshot for this game (post-start, or no snapshot within the lookback window)",
            "either team's first ~20 games of a season (no reliable baseline)",
            "a totals market with only one side, or mismatched lines, at the sampled snapshot",
        ],
        prediction_checkpoints=["PREGAME_CLOSEST_VALID_SNAPSHOT"],
        primary_metric="paired proxy-minus-Pinnacle Brier score delta and log-loss delta (negative = proxy better), game-clustered 95% CI",
        secondary_metrics=[
            "disagreement-magnitude banded paired delta", "direction-asymmetry paired delta",
            "Pinnacle-fair-probability-banded paired delta", "independent Pinnacle calibration (ECE, by favorite/underdog, by price band)",
        ],
        chronological_split_policy=f"SEASON_BASED: development={DEV_SEASONS}, validation={VALIDATION_SEASONS}, holdout={HOLDOUT_SEASONS} (locked)",
        minimum_sample_requirement={"independentGames": MIN_GAMES_EXPLORATORY},
        clustering_unit="gamePk",
        experiment_type=reg.EXPERIMENT_TYPE_CONFIRMATORY,
        false_discovery_handling=reg.FDR_BENJAMINI_HOCHBERG,
        pit_requirements={"team_recent_game_log_reconstruction": "PREDICTIVE_INPUT"},
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "evidenceLevel E2_PIT_HISTORICAL: the proxy's own inputs (season_to_date_rate) are proven PIT-safe by "
            "MLB-RSCH-0005's own test suite; historical Pinnacle snapshots are real, timestamped market data, not a "
            "reconstruction. This is NOT E3 (no chronological walk-forward beyond the single dev/val/holdout split) "
            "and NOT a claim the proxy reproduces any production model's actual historical probability."
        ),
    )
    reg.register_experiment(definition)
    return control, definition


# ── ET date helper (matches fetch_historical_pinnacle_cache's own convention) ──

def _et_date_of_utc_iso(commence_time_iso):
    dt = datetime.strptime(commence_time_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    et_dt = dt - timedelta(hours=4)
    return et_dt.strftime("%Y-%m-%d")


def _epoch(iso_str):
    return int(datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


# ── MLB schedule loading (reused, read-only, no new fetch) ─────────────────

def load_team_games_by_id(season):
    games_by_team_id = {}
    for team_abbr, team_id in MLB_TEAM_ID_MAP.items():
        schedule = schedule_fetcher.load_cached_schedule(season, team_abbr)
        games = extract_team_games_from_schedule(schedule, team_id) if schedule else []
        for g in games:
            g["team"] = team_abbr
        games_by_team_id[team_id] = games
    return games_by_team_id


def _game_index_by_home_away_date(games_by_team_id):
    """{(homeAbbr, awayAbbr, date): homeSideGameRecord} -- the home
    side's own record already carries runsScored=home runs,
    runsAllowed=away runs (extract_team_games_from_schedule's own
    convention)."""
    index = {}
    for team_id, games in games_by_team_id.items():
        for g in games:
            if g["side"] != "home":
                continue
            opp_abbr = _ID_TO_ABBR.get(g.get("opponentTeamId"))
            if not opp_abbr:
                continue
            index[(g["team"], opp_abbr, g["date"])] = g
    return index


# ── Pinnacle cache loading + per-game snapshot selection ───────────────────

def _load_pinnacle_cache_dates(season):
    season_dir = os.path.join(PINNACLE_CACHE_ROOT, str(season))
    if not os.path.isdir(season_dir):
        return []
    out = []
    for fname in sorted(os.listdir(season_dir)):
        if not fname.endswith(".json"):
            continue
        with open(os.path.join(season_dir, fname)) as f:
            out.append(json.load(f))
    return out


def _pinnacle_game_candidates(date_caches):
    """Groups every raw Pinnacle game observation, across ALL cached
    snapshot requests for this season, by (homeAbbr, awayAbbr, etDate)
    -- each entry carries every candidate snapshot for that specific
    game, for select_closest_pregame_snapshot to choose among."""
    by_game = defaultdict(list)
    for date_cache in date_caches:
        for snap in date_cache.get("snapshots", []):
            requested_at = snap.get("requestedAt")
            for game in snap.get("games") or []:
                home_abbr = clv.to_abbr(game.get("home_team", ""))
                away_abbr = clv.to_abbr(game.get("away_team", ""))
                commence_time = game.get("commence_time")
                if not commence_time:
                    continue
                et_date = _et_date_of_utc_iso(commence_time)
                key = (home_abbr, away_abbr, et_date)
                by_game[key].append({
                    "requestedAt": requested_at, "commenceTime": commence_time,
                    "bookmakers": game.get("bookmakers") or [],
                })
    return by_game


def _pinnacle_markets_from_snapshot(snapshot):
    pinnacle_books = [b for b in snapshot.get("bookmakers") or [] if b.get("key") == "pinnacle"]
    if not pinnacle_books:
        return None, None
    markets_by_key = {m.get("key"): m for m in pinnacle_books[0].get("markets") or []}
    return markets_by_key.get("h2h"), markets_by_key.get("totals")


# ── Row building ─────────────────────────────────────────────────────────

def build_matched_rows(season):
    games_by_team_id = load_team_games_by_id(season)
    schedule_index = _game_index_by_home_away_date(games_by_team_id)
    pinnacle_candidates = _pinnacle_game_candidates(_load_pinnacle_cache_dates(season))

    rows = []
    for (home_abbr, away_abbr, et_date), candidates in pinnacle_candidates.items():
        schedule_game = schedule_index.get((home_abbr, away_abbr, et_date))
        if schedule_game is None:
            continue
        commence_epoch = _epoch(candidates[0]["commenceTime"])
        selected = select_closest_pregame_snapshot(
            [{"requestedAt": c["requestedAt"], "bookmakers": c["bookmakers"]} for c in candidates],
            commence_epoch,
        )
        if selected is None:
            continue

        home_team_games = games_by_team_id.get(MLB_TEAM_ID_MAP.get(home_abbr), [])
        away_team_games = games_by_team_id.get(MLB_TEAM_ID_MAP.get(away_abbr), [])
        home_b = team_baseline(home_team_games, schedule_game)
        away_target = next((g for g in away_team_games if g["gamePk"] == schedule_game["gamePk"]), None)
        away_b = team_baseline(away_team_games, away_target) if away_target else None
        if home_b is None or away_b is None:
            continue

        h2h_market, totals_market = _pinnacle_markets_from_snapshot(selected)

        row = {
            "season": season, "gamePk": schedule_game["gamePk"], "date": et_date,
            "homeAbbr": home_abbr, "awayAbbr": away_abbr,
            "actualHomeRuns": schedule_game.get("runsScored"), "actualAwayRuns": schedule_game.get("runsAllowed"),
            "minutesBeforeStart": selected["minutesBeforeStart"],
            "homeBaseline": home_b, "awayBaseline": away_b,
            "h2hMarket": h2h_market, "totalsMarket": totals_market,
        }
        rows.append(row)
    return rows


def extract_ml_devig(row):
    """Pure. Devigs the h2h market against the correct home/away sides
    by matching outcome names to the row's own homeAbbr/awayAbbr (via
    clv.to_abbr on the outcome's own name -- robust to whichever full-
    name spelling The Odds API used, same conversion used everywhere
    else in this pipeline)."""
    market = row.get("h2hMarket")
    if not market:
        return None, None, None
    home_price = away_price = None
    for outcome in market.get("outcomes") or []:
        side_abbr = clv.to_abbr(outcome.get("name", ""))
        if side_abbr == row["homeAbbr"]:
            home_price = outcome.get("price")
        elif side_abbr == row["awayAbbr"]:
            away_price = outcome.get("price")
    return devig_two_sided(home_price, away_price)


def extract_total_devig(row):
    """Pure. Finds the totals market's own quoted line (the FIRST line
    present -- Pinnacle typically quotes one primary line per snapshot)
    and de-vigs Over/Under at that EXACT line only."""
    market = row.get("totalsMarket")
    if not market:
        return None, None, None, None
    points = {o.get("point") for o in market.get("outcomes") or [] if o.get("point") is not None}
    if not points:
        return None, None, None, None
    line = sorted(points)[0]
    over_price, under_price = matched_total_line(market, line)
    fair_over, fair_under, overround = devig_two_sided(over_price, under_price)
    return line, fair_over, fair_under, overround


def enrich_row(row, home_field_adjustment):
    """Applies the FROZEN proxy (expected_runs with home_field_adjustment
    fixed) and extracts Pinnacle's own de-vigged fair probabilities, to
    ONE row. Pure -- never mutates dev/val/holdout differently; the
    caller applies this SAME function, with the SAME frozen
    home_field_adjustment, to every split."""
    eh, ea = expected_runs(row["homeBaseline"], row["awayBaseline"], home_field_adjustment=home_field_adjustment)
    row["expectedHomeRuns"], row["expectedAwayRuns"] = eh, ea

    ml_fair_home, ml_fair_away, ml_overround = extract_ml_devig(row)
    row["pinnacleMlHomeFair"], row["pinnacleMlAwayFair"], row["pinnacleMlOverround"] = ml_fair_home, ml_fair_away, ml_overround
    proxy_ml_home, proxy_ml_push = game_ml_proxy_probability(eh, ea)
    row["proxyMlHomeProb"] = proxy_ml_home

    total_line, total_fair_over, total_fair_under, total_overround = extract_total_devig(row)
    row["pinnacleTotalLine"], row["pinnacleTotalOverFair"], row["pinnacleTotalUnderFair"], row["pinnacleTotalOverround"] = total_line, total_fair_over, total_fair_under, total_overround
    if total_line is not None:
        row["proxyTotalOverProb"] = game_total_proxy_probability(eh, ea, total_line)
    else:
        row["proxyTotalOverProb"] = None

    if row.get("actualHomeRuns") is not None and row.get("actualAwayRuns") is not None:
        row["actualHomeWin"] = 1 if row["actualHomeRuns"] > row["actualAwayRuns"] else 0
        actual_total = row["actualHomeRuns"] + row["actualAwayRuns"]
        row["actualOver"] = (1 if actual_total > total_line else 0) if total_line is not None else None
    else:
        row["actualHomeWin"], row["actualOver"] = None, None
    return row


# ── Paired proxy-vs-Pinnacle analysis (reuses lib.edgelab.paired_evaluation) ──

def _pairing_rows(rows, proxy_key, pinnacle_key, outcome_key):
    pinnacle_rows, proxy_rows = [], []
    for r in rows:
        proxy_p, pinnacle_p, outcome = r.get(proxy_key), r.get(pinnacle_key), r.get(outcome_key)
        if proxy_p is None or pinnacle_p is None or outcome is None:
            continue
        identity = {"gameId": r["gamePk"], "marketTicker": f"{r['gamePk']}::{proxy_key}", "researchCheckpoint": "PREGAME", "gameDate": r["date"], "outcome": outcome}
        pinnacle_rows.append({**identity, "modelFairProbability": pinnacle_p})
        proxy_rows.append({**identity, "modelFairProbability": proxy_p})
    return pinnacle_rows, proxy_rows


def paired_analysis(rows, proxy_key, pinnacle_key, outcome_key, label):
    """The full metric bundle for one segment: reuses pe.pair_eligible_
    observations/evaluate_probability_model_pair (repurposed PROXY-vs-
    PINNACLE, the same MODEL-vs-MARKET repurposing MLB-RSCH-0001
    already established) plus brier_and_log_loss_summary/
    expected_calibration_error/independent_unit_count/sample_size_status."""
    pinnacle_rows, proxy_rows = _pairing_rows(rows, proxy_key, pinnacle_key, outcome_key)
    n = len(pinnacle_rows)
    independent_games = independent_unit_count(rows, key="gamePk") if n else 0
    result = {
        "label": label, "n": n, "independentGames": independent_games,
        "sampleSizeStatus": sample_size_status(n, independent_games=independent_games),
    }
    if n == 0:
        result["pairedBrierDelta_proxyMinusPinnacle"] = None
        result["pairedDeltaConfidenceInterval95"] = {"low": None, "high": None, "method": "NO_DATA"}
        return result

    proxy_pairs = [(r["modelFairProbability"], r["outcome"]) for r in proxy_rows]
    pinnacle_pairs = [(r["modelFairProbability"], r["outcome"]) for r in pinnacle_rows]
    proxy_brier, proxy_logloss = brier_and_log_loss_summary(proxy_pairs)
    pinnacle_brier, pinnacle_logloss = brier_and_log_loss_summary(pinnacle_pairs)
    result["proxyBrierScore"], result["proxyLogLoss"] = proxy_brier, proxy_logloss
    result["pinnacleBrierScore"], result["pinnacleLogLoss"] = pinnacle_brier, pinnacle_logloss
    result["proxyCalibrationECE"] = expected_calibration_error(proxy_pairs)
    result["pinnacleCalibrationECE"] = expected_calibration_error(pinnacle_pairs)

    pairing = pe.pair_eligible_observations(pinnacle_rows, proxy_rows)
    assert pairing["nControlOnly"] == 0 and pairing["nCandidateOnly"] == 0, "proxy/Pinnacle pairing must be a perfect 1:1 match by construction"

    n_resamples = 500 if independent_games >= 5 else 0
    if n_resamples:
        evaluation = pe.evaluate_probability_model_pair(pairing, n_resamples=n_resamples, seed=DEFAULT_BOOTSTRAP_SEED, ci=0.95)
        result["pairedBrierDelta_proxyMinusPinnacle"] = evaluation["pairedDelta"]["brierScore"]
        result["pairedLogLossDelta_proxyMinusPinnacle"] = evaluation["pairedDelta"]["logLoss"]
        result["pairedDeltaConfidenceInterval95"] = evaluation["pairedDeltaConfidenceInterval"]
    else:
        result["pairedBrierDelta_proxyMinusPinnacle"] = round(proxy_brier - pinnacle_brier, 6) if proxy_brier is not None and pinnacle_brier is not None else None
        result["pairedLogLossDelta_proxyMinusPinnacle"] = round(proxy_logloss - pinnacle_logloss, 6) if proxy_logloss is not None and pinnacle_logloss is not None else None
        result["pairedDeltaConfidenceInterval95"] = {"low": None, "high": None, "method": "TOO_FEW_GAMES_FOR_BOOTSTRAP"}
    return result


def disagreement_band_analysis(rows, proxy_key, pinnacle_key, outcome_key, label_prefix):
    out = {}
    for label, low, high in DISAGREEMENT_BANDS:
        def _in_band(r, low=low, high=high):
            p, m = r.get(proxy_key), r.get(pinnacle_key)
            if p is None or m is None:
                return False
            diff = abs(p - m)
            if diff < low:
                return False
            if high is not None and diff >= high:
                return False
            return True
        bucket_rows = [r for r in rows if _in_band(r)]
        out[label] = paired_analysis(bucket_rows, proxy_key, pinnacle_key, outcome_key, f"{label_prefix}/{label}")
    return out


def direction_analysis(rows, proxy_key, pinnacle_key, outcome_key, label_prefix):
    proxy_higher = [r for r in rows if r.get(proxy_key) is not None and r.get(pinnacle_key) is not None and r[proxy_key] > r[pinnacle_key]]
    proxy_lower = [r for r in rows if r.get(proxy_key) is not None and r.get(pinnacle_key) is not None and r[proxy_key] < r[pinnacle_key]]
    return {
        "proxyHigherThanPinnacle": paired_analysis(proxy_higher, proxy_key, pinnacle_key, outcome_key, f"{label_prefix}/PROXY_HIGHER"),
        "proxyLowerThanPinnacle": paired_analysis(proxy_lower, proxy_key, pinnacle_key, outcome_key, f"{label_prefix}/PROXY_LOWER"),
    }


def price_band_analysis(rows, proxy_key, pinnacle_key, outcome_key, label_prefix):
    out = {}
    for label, low, high in PRICE_BANDS:
        bucket_rows = [r for r in rows if r.get(pinnacle_key) is not None and low <= r[pinnacle_key] < (high if high is not None else 1.0 + 1e-9)]
        out[label] = paired_analysis(bucket_rows, proxy_key, pinnacle_key, outcome_key, f"{label_prefix}/{label}")
    return out


def pinnacle_calibration(rows, pinnacle_key, outcome_key):
    pairs = [(r[pinnacle_key], r[outcome_key]) for r in rows if r.get(pinnacle_key) is not None and r.get(outcome_key) is not None]
    favorite_pairs = [(p, o) for p, o in pairs if p >= 0.5]
    underdog_pairs = [(p, o) for p, o in pairs if p < 0.5]
    return {
        "n": len(pairs), "overallECE": expected_calibration_error(pairs),
        "favoriteECE": expected_calibration_error(favorite_pairs), "favoriteN": len(favorite_pairs),
        "underdogECE": expected_calibration_error(underdog_pairs), "underdogN": len(underdog_pairs),
    }


def robustness_checks(dev_result, val_result, holdout_result, dev_rows, market_label):
    """Mission's 'critical robustness' checklist: never call a regime
    real from pooled significance alone."""
    def _confident_favorable(result):
        ci = result.get("pairedDeltaConfidenceInterval95") or {}
        return ci.get("high") is not None and ci["high"] < 0 and result.get("independentGames", 0) >= MIN_GAMES_CONFIDENT

    by_season = defaultdict(list)
    for r in dev_rows:
        by_season[r["season"]].append(r)
    season_consistency = {
        str(season): paired_analysis(season_rows, f"proxy{market_label}Prob", f"pinnacle{market_label}Fair", f"actual{market_label}Outcome", f"{market_label}/{season}")
        for season, season_rows in sorted(by_season.items())
    }

    return {
        "developmentDirectionFavorable": _confident_favorable(dev_result),
        "validationDirectionFavorable": _confident_favorable(val_result) if val_result else None,
        "holdoutDirectionFavorable": _confident_favorable(holdout_result) if holdout_result else None,
        "seasonBySeasonWithinDevelopment": season_consistency,
        "adequateIndependentGameCount": dev_result.get("independentGames", 0) >= MIN_GAMES_CONFIDENT,
    }


# ── Final classification ────────────────────────────────────────────────

SIGNAL_PROXY_BEATS = "PROXY_BEATS_SHARP_MARKET"
SIGNAL_PARTIAL = "PARTIAL_REGIME_SPECIFIC_ADVANTAGE"
SIGNAL_SHARP_DOMINANT = "SHARP_MARKET_DOMINANT"
SIGNAL_PARITY = "PARITY_NO_INCREMENTAL_SIGNAL"
SIGNAL_INSUFFICIENT = "INSUFFICIENT"


def classify_family_signal(dev_result, val_result, holdout_result, band_results):
    if dev_result is None or dev_result.get("independentGames", 0) < MIN_GAMES_EXPLORATORY:
        return SIGNAL_INSUFFICIENT
    dev_ci = dev_result.get("pairedDeltaConfidenceInterval95") or {}
    dev_lo, dev_hi = dev_ci.get("low"), dev_ci.get("high")
    dev_games = dev_result.get("independentGames", 0)

    if dev_games < MIN_GAMES_CONFIDENT:
        return SIGNAL_INSUFFICIENT

    confident_worse = dev_lo is not None and dev_lo > 0
    confident_better = dev_hi is not None and dev_hi < 0

    def _confident_val_or_holdout_better(result):
        if not result:
            return False
        ci = result.get("pairedDeltaConfidenceInterval95") or {}
        return ci.get("high") is not None and ci["high"] < 0 and result.get("independentGames", 0) >= MIN_GAMES_CONFIDENT

    if confident_worse:
        return SIGNAL_SHARP_DOMINANT
    if confident_better and _confident_val_or_holdout_better(val_result) and _confident_val_or_holdout_better(holdout_result):
        return SIGNAL_PROXY_BEATS
    if confident_better or any(
        (b.get("pairedDeltaConfidenceInterval95") or {}).get("high") is not None
        and b["pairedDeltaConfidenceInterval95"]["high"] < 0
        and b.get("independentGames", 0) >= MIN_GAMES_CONFIDENT
        for b in band_results.values()
    ):
        return SIGNAL_PARTIAL
    return SIGNAL_PARITY


# ── main ──────────────────────────────────────────────────────────────────

def coverage_report(rows_by_season):
    return {
        str(season): {
            "matchedGames": len(rows),
            "validPregameSnapshotRate": round(sum(1 for r in rows if r.get("minutesBeforeStart") is not None) / len(rows), 4) if rows else None,
            "mlEligible": sum(1 for r in rows if r.get("pinnacleMlHomeFair") is not None and r.get("proxyMlHomeProb") is not None),
            "totalEligible": sum(1 for r in rows if r.get("pinnacleTotalOverFair") is not None and r.get("proxyTotalOverProb") is not None),
        }
        for season, rows in rows_by_season.items()
    }


def main():
    experiment = register_experiment()[1]

    rows_by_season = {season: build_matched_rows(season) for season in ALL_SEASONS}
    dev_rows_raw = [r for s in DEV_SEASONS for r in rows_by_season.get(s, [])]

    # ---- ONE dev-fit parameter, fit ONCE, frozen. ----
    home_field_adjustment = fit_home_field_adjustment(dev_rows_raw)

    for season, rows in rows_by_season.items():
        for row in rows:
            enrich_row(row, home_field_adjustment)

    dev_rows = [r for s in DEV_SEASONS for r in rows_by_season.get(s, [])]
    val_rows = [r for s in VALIDATION_SEASONS for r in rows_by_season.get(s, [])]
    holdout_rows = [r for s in HOLDOUT_SEASONS for r in rows_by_season.get(s, [])]

    coverage = coverage_report(rows_by_season)

    ml_dev = paired_analysis(dev_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "ML/DEV")
    ml_val = paired_analysis(val_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "ML/VAL") if val_rows else None
    ml_holdout = paired_analysis(holdout_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "ML/HOLDOUT") if holdout_rows else None
    ml_bands = disagreement_band_analysis(dev_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "ML/BAND")
    ml_direction = direction_analysis(dev_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "ML/DIR")
    ml_price_bands = price_band_analysis(dev_rows, "proxyMlHomeProb", "pinnacleMlHomeFair", "actualHomeWin", "ML/PRICE")
    ml_calibration = pinnacle_calibration(dev_rows, "pinnacleMlHomeFair", "actualHomeWin")
    ml_robustness = robustness_checks(ml_dev, ml_val, ml_holdout, dev_rows, "Ml")
    ml_signal = classify_family_signal(ml_dev, ml_val, ml_holdout, ml_bands)

    total_dev = paired_analysis(dev_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "TOTAL/DEV")
    total_val = paired_analysis(val_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "TOTAL/VAL") if val_rows else None
    total_holdout = paired_analysis(holdout_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "TOTAL/HOLDOUT") if holdout_rows else None
    total_bands = disagreement_band_analysis(dev_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "TOTAL/BAND")
    total_direction = direction_analysis(dev_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "TOTAL/DIR")
    total_price_bands = price_band_analysis(dev_rows, "proxyTotalOverProb", "pinnacleTotalOverFair", "actualOver", "TOTAL/PRICE")
    total_calibration = pinnacle_calibration(dev_rows, "pinnacleTotalOverFair", "actualOver")
    total_robustness = robustness_checks(total_dev, total_val, total_holdout, dev_rows, "Total")
    total_signal = classify_family_signal(total_dev, total_val, total_holdout, total_bands)

    disposition = disp.RESEARCH_CANDIDATE

    report = {
        "experimentId": EXPERIMENT_ID,
        "evidenceLevel": experiment["evidenceLevel"],
        "generatedAt": REGISTRATION_TIMESTAMP,
        "homeFieldRunsAdjustment": home_field_adjustment,
        "coverage": coverage,
        "gameResult": {
            "development": ml_dev, "validation": ml_val, "holdout": ml_holdout,
            "disagreementBands": ml_bands, "direction": ml_direction, "priceBands": ml_price_bands,
            "pinnacleCalibration": ml_calibration, "robustness": ml_robustness, "signalClassification": ml_signal,
        },
        "gameTotal": {
            "development": total_dev, "validation": total_val, "holdout": total_holdout,
            "disagreementBands": total_bands, "direction": total_direction, "priceBands": total_price_bands,
            "pinnacleCalibration": total_calibration, "robustness": total_robustness, "signalClassification": total_signal,
        },
        "disposition": disposition,
        "productionBehaviorChanged": False,
        "methodologicalLimitations": [
            "This proxy does NOT reconstruct any production model's actual historical probability -- season-aggregate "
            "team offense/starter quality/bullpen talent remain UNAVAILABLE_HISTORICALLY (Milestone 2, unchanged).",
            "Starter and bullpen recency baselines (MLB-RSCH-0003/0004) were NOT incorporated in this first proxy "
            "version, to keep the proxy modest per the mission's explicit instruction.",
            f"Pregame snapshot coverage is bounded by two fixed daily snapshot times (SNAPSHOT_TIMES_ET) and a "
            f"{MAX_MINUTES_BEFORE_START}-minute maximum lookback -- games with no qualifying snapshot are excluded, not approximated.",
            "Home field is a single dev-fit additive runs constant, not a park-specific or team-specific adjustment.",
        ],
    }
    report["methodologicalLimitations"] = [m for m in report["methodologicalLimitations"] if m]

    report_id = rlids.build_experiment_report_id(EXPERIMENT_ID, experiment["controlModelId"], None, REGISTRATION_TIMESTAMP)
    report["experimentReportId"] = report_id

    report_dir = os.path.join(EDGELAB_DIR, "experiment_reports", EXPERIMENT_ID)
    os.makedirs(report_dir, exist_ok=True)
    with open(os.path.join(report_dir, f"{report_id}.json"), "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    analytics_path = os.path.join(EDGELAB_DIR, "analytics", "latest_mlb_rsch_0008_proxy_vs_pinnacle.json")
    os.makedirs(os.path.dirname(analytics_path), exist_ok=True)
    with open(analytics_path, "w") as f:
        json.dump(report, f, indent=2, sort_keys=True, default=str)

    print(json.dumps({
        "experimentId": EXPERIMENT_ID, "coverage": coverage, "homeFieldRunsAdjustment": home_field_adjustment,
        "gameResultSignal": ml_signal, "gameTotalSignal": total_signal,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
