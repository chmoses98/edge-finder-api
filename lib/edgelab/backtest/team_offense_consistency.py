"""
lib/edgelab/backtest/team_offense_consistency.py
====================================================
PIT-safe reconstruction of OFFENSIVE-FORM CONSISTENCY features, for
MLB-RSCH-0036.

RELATIONSHIP TO MLB-RSCH-0005
-----------------------------
MLB-RSCH-0005 (lib/edgelab/backtest/team_offense_recency_reconstruction.py)
already asked "does recent runs/game deviation from a team's own season
baseline predict next-game scoring?" and answered NO_USEFUL_SIGNAL:
5/10/20-game deviation correlations were indistinguishable from zero on
12,800 development team-games, and the frozen candidate regression beat
the control by 0.0008 MAE -- i.e. nothing.

This module asks the DIFFERENT question that result leaves open: the
mean was noise, but a mean is only one number about a distribution. Does
the SHAPE of recent scoring -- median, threshold-clear rate, outlier
dependence, a trimmed mean, an EWMA -- carry information the mean threw
away? And, where this repository's own market archive permits, does
LINE-RELATIVE form (how often a team beat its own closing team total)
carry more than any raw-runs measure can?

REUSE, NOT DUPLICATION
----------------------
  * the distribution math itself is lib/team_offense_form.py -- the SAME
    module the production slate context uses, so a research finding and
    the number a handicapper reads can never drift apart;
  * leakage filtering is lib.edgelab.backtest.bullpen_backtest_
    reconstruction.is_strictly_before(), imported unchanged (it already
    handles same-date doubleheader ordering, and refuses to treat a
    same-date game with unknown gameNumber as prior);
  * the team-game log comes from MLB-RSCH-0003's already-committed
    schedule cache via extract_team_games_from_schedule();
  * market lines come from lib/team_total_line_history.py, applied to
    this repository's own archived Kalshi observations.
Nothing here re-derives any of the above.

LEAKAGE DISCIPLINE
------------------
Every function takes a team's FULL game list (past, target, future) and
filters internally. A row is eligible only once the team has
MIN_PRIOR_GAMES strictly-prior completed games this season, which also
guarantees the widest window (10) is fillable from real games -- never
padded. The target game's own runs are only ever read as the OUTCOME.

The market-relative features obey the same rule twice over: a game's
line is the latest PREGAME ladder for THAT game, and only games strictly
before the target contribute to the feature.
"""
from lib.edgelab.backtest.bullpen_backtest_reconstruction import is_strictly_before
from lib.team_offense_form import FORM_WINDOWS, SCORING_THRESHOLDS, window_profile

# Same eligibility floor as MLB-RSCH-0005, so the two experiments'
# populations are directly comparable rather than subtly different.
MIN_PRIOR_GAMES = 20

# The next-game outcomes this experiment predicts. (A) runs scored, and
# (B) clearing each team-total threshold the Kalshi ladder actually
# trades at.
OUTCOME_THRESHOLDS = SCORING_THRESHOLDS


def prior_games(team_games, as_of_game):
    """Pure. Every game strictly before `as_of_game`, chronological."""
    return [g for g in team_games if is_strictly_before(g, as_of_game)]


def flatten_profile(profile, window, prefix="form"):
    """
    Pure. One window profile -> flat, regression-ready float features.
    Deliberately excludes the raw score vector (a list is descriptive,
    not a feature) and any label (a label is a presentation decision,
    not evidence).
    """
    if profile is None:
        return {}
    flat = {
        f"{prefix}Mean_{window}": profile["mean"],
        f"{prefix}Median_{window}": profile["median"],
        f"{prefix}Max_{window}": float(profile["max"]),
        f"{prefix}Min_{window}": float(profile["min"]),
        f"{prefix}TrimmedMean_{window}": profile["trimmedMean"],
        f"{prefix}Ewma_{window}": profile["ewma"],
        f"{prefix}TopGameShare_{window}": profile["topGameShareOfWindowRuns"],
        f"{prefix}MeanMinusTrimmed_{window}": profile["outlierDependence"]["meanMinusTrimmedMean"],
        f"{prefix}MeanMinusMedian_{window}": (
            round(profile["mean"] - profile["median"], 3) if profile["median"] is not None else None
        ),
    }
    for threshold in SCORING_THRESHOLDS:
        flat[f"{prefix}Ge{threshold}Rate_{window}"] = profile["thresholdClears"][f"ge{threshold}"]["pct"]
    market = profile.get("marketRelative")
    if market and market["lineCoverage"]["withLine"]:
        flat[f"{prefix}TtOverRate_{window}"] = market["oversPct"]
        flat[f"{prefix}TtMeanMargin_{window}"] = market["meanMarginVsLine"]
        flat[f"{prefix}TtMedianMargin_{window}"] = market["medianMarginVsLine"]
        flat[f"{prefix}TtLineCoverage_{window}"] = market["lineCoverage"]["withLine"] / market["lineCoverage"]["of"]
    return flat


def season_to_date_mean(games, field="runsScored"):
    values = [g[field] for g in games if g.get(field) is not None]
    return sum(values) / len(values) if values else None


def reconstruct_consistency_features(team_games, opponent_games, as_of_game,
                                     *, line_by_key=None, min_prior_games=MIN_PRIOR_GAMES,
                                     windows=FORM_WINDOWS):
    """
    Pure. Every consistency feature for one (team, target game), or None
    when the team is not yet eligible.

    `line_by_key` is an optional {(date, team): implied_line} mapping
    (see lib.team_total_line_history). When supplied, each prior game
    also contributes its margin versus that game's own pregame market
    line, and the market-relative features appear. When a game has no
    archived line it contributes None and is excluded from those
    statistics -- never imputed.
    """
    prior = prior_games(team_games, as_of_game)
    if len(prior) < min_prior_games:
        return None

    runs_vector = [g["runsScored"] for g in prior if g.get("runsScored") is not None]
    if len(runs_vector) < min_prior_games:
        return None

    margins = None
    if line_by_key is not None:
        margins = []
        for game in prior:
            if game.get("runsScored") is None:
                continue
            line = line_by_key.get((game.get("date"), game.get("team")))
            margins.append(None if line is None else round(game["runsScored"] - line, 3))

    season_baseline = season_to_date_mean(prior)
    opponent_baseline = season_to_date_mean(prior_games(opponent_games or [], as_of_game), "runsAllowed")

    features = {
        "asOfDate": as_of_game.get("date"),
        "priorGamesThisSeason": len(prior),
        "seasonToDateRunsPerGame": season_baseline,
        "opponentSeasonToDateRunsAllowedPerGame": opponent_baseline,
        "isHome": 1.0 if as_of_game.get("side") == "home" else 0.0,
    }
    for window in windows:
        profile = window_profile(runs_vector, window, line_margins=margins)
        features.update(flatten_profile(profile, window))
        # The deviation-from-own-baseline feature MLB-RSCH-0005 tested,
        # recomputed here on the same rows so the two experiments'
        # results are comparable rather than merely adjacent.
        if profile is not None and season_baseline is not None:
            features[f"formMeanDeviation_{window}"] = round(profile["mean"] - season_baseline, 3)
    return features


def consistency_outcome(target_game):
    """
    Pure. The next-game outcome side of a row, straight from the target
    game's own recorded runs. None when runs are missing -- never
    assumed, never zero-filled.
    """
    runs = target_game.get("runsScored")
    if runs is None:
        return None
    outcome = {"runsScored": float(runs)}
    for threshold in OUTCOME_THRESHOLDS:
        outcome[f"scored{threshold}Plus"] = 1.0 if runs >= threshold else 0.0
    return outcome


# ── probability-scoring helpers (no new dependency) ─────────────────────

def brier_score(rows, probability_key, outcome_key):
    """Pure. Mean squared error of a probability forecast. Lower is
    better; 0.25 is the score of always saying 50%."""
    pairs = [(r[probability_key], r[outcome_key]) for r in rows
             if r.get(probability_key) is not None and r.get(outcome_key) is not None]
    if not pairs:
        return None
    return round(sum((p - y) ** 2 for p, y in pairs) / len(pairs), 6)


def log_loss(rows, probability_key, outcome_key, epsilon=1e-6):
    """Pure. Mean negative log-likelihood, with probabilities clipped
    into (epsilon, 1-epsilon) so a confidently-wrong forecast is
    penalized heavily but never infinitely."""
    import math

    pairs = [(r[probability_key], r[outcome_key]) for r in rows
             if r.get(probability_key) is not None and r.get(outcome_key) is not None]
    if not pairs:
        return None
    total = 0.0
    for p, y in pairs:
        p = min(1.0 - epsilon, max(epsilon, p))
        total -= y * math.log(p) + (1.0 - y) * math.log(1.0 - p)
    return round(total / len(pairs), 6)
