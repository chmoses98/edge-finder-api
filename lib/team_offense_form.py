"""
lib/team_offense_form.py
============================
ONE pure implementation of "what does this offense's recent scoring
actually look like", used by BOTH the production slate context
(scripts/fetch_team_offense_form.py -> scripts/enrich_data.py) and the
research backtest (lib/edgelab/backtest/team_offense_consistency.py).
Neither side re-implements any of it.

THE PROBLEM IT EXISTS TO FIX
----------------------------
The slate has always described an offense with a single number: rolling
runs/game (`last7RpG`, `last15RpG` from api/teamstats.js). A mean over a
handful of games is dominated by its largest value. A team that scores

    3, 4, 2, 20, 5, 1, 1

has an L7 mean of 5.1 -- above league average, "hot" by any
mean-only reading -- while its MEDIAN is 4, it cleared 5 runs twice in
seven games, and 56% of everything it scored came from one game. Calling
that offense hot is a handicapping error, and it is an error that a mean
literally cannot show you.

So every consumer gets the SHAPE of the distribution, not just its
centre: the actual game-by-game vector, median, threshold-clear counts,
the max, how much of the window's total came from that max, a trimmed
mean, an EWMA, and -- where the market archive supports it -- how the
team performed against its own closing team-total line.

WHAT THIS MODULE DOES NOT DO
----------------------------
It computes no probability, no edge, and no production model input. It
changes no weight anywhere: scripts/enrich_data.py's
compute_offense_baseline() blend is untouched, and this module is not
imported by it. Everything here is DESCRIPTIVE CONTEXT plus a research
feature source, which is exactly the status the research findings
(docs/RESEARCH_OFFENSIVE_FORM.md) support.

Never fabricates: a window with fewer completed games than it needs
returns None for that window rather than a padded or approximated value,
and a game with no recorded runs is excluded rather than assumed 0.
"""
import math

# Fixed, preregistered windows. 5/7/10 rather than the production
# feed's calendar-day L7/L15: a GAME window is what "last N games"
# means to a handicapper, and a calendar window silently varies in
# length with off-days (api/teamstats.js's last7RpG is 7 CALENDAR days,
# which can be anywhere from 4 to 7 games -- a real, and previously
# undocumented, inconsistency this module does not inherit).
FORM_WINDOWS = (5, 7, 10)

# The thresholds a team-total ladder actually trades at on Kalshi.
SCORING_THRESHOLDS = (3, 4, 5, 6)

# A window whose mean exceeds its trimmed mean by at least this many
# runs, OR whose single best game supplied at least this share of the
# window's total runs, is flagged outlier-dependent. Both are fixed
# constants, chosen for interpretability, never tuned against an
# outcome -- they gate a LABEL in a human-readable line, nothing more.
OUTLIER_MEAN_GAP_RUNS = 0.75
OUTLIER_TOP_GAME_SHARE = 0.35


def _mean(values):
    return sum(values) / len(values) if values else None


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def trimmed_mean(values, trim=1):
    """
    Pure. Mean after dropping the `trim` highest AND `trim` lowest
    values. None when trimming would leave nothing. This is the number
    that answers "what is this offense when you take away its single
    best and single worst night".
    """
    if not values or len(values) <= 2 * trim:
        return None
    ordered = sorted(values)
    return _mean(ordered[trim:len(ordered) - trim])


def ewma(values, half_life):
    """
    Pure. Exponentially weighted mean, most recent value LAST in
    `values` and weighted highest. None for an empty window.
    half_life is in games: a game `half_life` games ago counts half as
    much as the most recent one.
    """
    if not values:
        return None
    if half_life <= 0:
        return float(values[-1])
    decay = math.log(2) / half_life
    weights = [math.exp(-decay * age) for age in range(len(values) - 1, -1, -1)]
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total if total else None


def window_profile(runs_vector, window, *, line_margins=None):
    """
    Pure. The full shape of one rolling window, or None when the team
    does not yet have `window` completed games.

    `runs_vector`   chronological runs scored, OLDEST first, containing
                    ONLY games strictly before the game being described
                    (the caller owns leakage safety -- see
                    lib.edgelab.backtest.team_offense_consistency).
    `line_margins`  optional, same ordering/length as runs_vector:
                    (runs - closing team-total line) per game, or None
                    for a game with no usable archived line. Games
                    without a line are excluded from the market-relative
                    stats and counted in `lineCoverage`, never imputed.
    """
    if runs_vector is None or len(runs_vector) < window:
        return None
    recent = [int(r) for r in runs_vector[-window:]]
    total = sum(recent)
    top = max(recent)

    profile = {
        "window": window,
        "games": window,
        "scores": recent,
        "mean": round(_mean(recent), 2),
        "median": round(_median(recent), 1),
        "max": top,
        "min": min(recent),
        "totalRuns": total,
        "topGameShareOfWindowRuns": round(top / total, 3) if total else None,
        "trimmedMean": (round(trimmed_mean(recent), 2) if trimmed_mean(recent) is not None else None),
        "ewma": round(ewma(recent, half_life=window / 2.0), 2),
        "thresholdClears": {},
        "marketRelative": None,
    }
    for threshold in SCORING_THRESHOLDS:
        clears = sum(1 for r in recent if r >= threshold)
        profile["thresholdClears"][f"ge{threshold}"] = {
            "games": clears, "of": window, "pct": round(clears / window, 3),
        }

    trimmed = profile["trimmedMean"]
    mean_gap = (profile["mean"] - trimmed) if trimmed is not None else None
    share = profile["topGameShareOfWindowRuns"]
    profile["outlierDependence"] = {
        "meanMinusTrimmedMean": round(mean_gap, 2) if mean_gap is not None else None,
        "topGameShareOfWindowRuns": share,
        "isOutlierDependent": bool(
            (mean_gap is not None and mean_gap >= OUTLIER_MEAN_GAP_RUNS)
            or (share is not None and share >= OUTLIER_TOP_GAME_SHARE)
        ),
    }

    if line_margins is not None:
        recent_margins = list(line_margins[-window:])
        usable = [m for m in recent_margins if m is not None]
        overs = [m for m in usable if m > 0]
        profile["marketRelative"] = {
            "lineCoverage": {"withLine": len(usable), "of": window},
            "overs": len(overs),
            "oversPct": round(len(overs) / len(usable), 3) if usable else None,
            "meanMarginVsLine": round(_mean(usable), 2) if usable else None,
            "medianMarginVsLine": round(_median(usable), 2) if usable else None,
        }
    return profile


def build_form(runs_vector, *, line_margins=None, windows=FORM_WINDOWS, team=None,
               as_of_date=None, source=None, games_available=None):
    """
    Pure. Every window's profile plus the metadata a consumer needs to
    judge how much to trust it. `windows` that cannot be filled are
    present with a None value -- an explicit "not enough games", never a
    silently shortened window.
    """
    vector = [int(r) for r in (runs_vector or []) if r is not None]
    return {
        "team": team,
        "asOfDate": as_of_date,
        "source": source,
        "gamesAvailable": games_available if games_available is not None else len(vector),
        "windows": {
            f"L{w}": window_profile(vector, w, line_margins=line_margins) for w in windows
        },
        "unavailableReason": None if vector else "no completed games with recorded runs",
    }


def format_form_line(form, window="L7"):
    """
    Pure. The ONE human-readable line the slate output contract shows per
    team. Deliberately compact and deliberately built so a mean alone can
    never carry it:

        L7: 5.1 avg | 4.0 med | 3,4,2,20,5,1,1 | 3/7 >=4 | 2/7 >=5 |
        max 20 (56% of L7 runs) | OUTLIER-DEPENDENT | TT overs 2/7

    The TT segment appears only when archived team-total lines actually
    covered those games; its absence means "not measured", never "zero".
    """
    profile = ((form or {}).get("windows") or {}).get(window)
    if not profile:
        available = (form or {}).get("gamesAvailable")
        return f"{window}: unavailable ({available} completed games on record)"

    parts = [
        f"{window}: {profile['mean']:.1f} avg",
        f"{profile['median']:.1f} med",
        ",".join(str(s) for s in profile["scores"]),
    ]
    for threshold in (4, 5):
        clear = profile["thresholdClears"][f"ge{threshold}"]
        parts.append(f"{clear['games']}/{clear['of']} >={threshold}")
    share = profile["topGameShareOfWindowRuns"]
    parts.append(
        f"max {profile['max']}" + (f" ({share * 100:.0f}% of {window} runs)" if share is not None else "")
    )
    if profile["outlierDependence"]["isOutlierDependent"]:
        parts.append("OUTLIER-DEPENDENT")
    market = profile.get("marketRelative")
    if market and market["lineCoverage"]["withLine"]:
        parts.append(f"TT overs {market['overs']}/{market['lineCoverage']['withLine']}")
    return " | ".join(parts)


def hot_label(form, window="L7"):
    """
    Pure. A deliberately CONSERVATIVE label, and the direct answer to
    "one 20-run game must not by itself justify calling a team hot".

    HOT requires repeatability, not magnitude: a majority of the window's
    games clearing 4 runs AND a median at or above 4 AND no outlier
    dependence. A window whose mean is high only because of one
    explosion lands in OUTLIER_INFLATED, which reads as a warning rather
    than as a green light. Returns (label, reason).
    """
    profile = ((form or {}).get("windows") or {}).get(window)
    if not profile:
        return "UNKNOWN", f"no {window} window available"

    outlier = profile["outlierDependence"]["isOutlierDependent"]
    ge4 = profile["thresholdClears"]["ge4"]
    ge4_rate = ge4["games"] / ge4["of"]
    median = profile["median"]

    if outlier and profile["mean"] >= 4.5:
        return "OUTLIER_INFLATED", (
            f"{window} mean {profile['mean']} is carried by one game "
            f"(max {profile['max']}, {profile['topGameShareOfWindowRuns'] * 100:.0f}% of window runs; "
            f"median {median}) -- not a repeatable hot streak"
        )
    if ge4_rate > 0.5 and median >= 4 and not outlier:
        return "HOT", (
            f"{ge4['games']}/{ge4['of']} games cleared 4 runs with a {median} median -- "
            f"repeated scoring, not one explosion"
        )
    if median <= 2 and ge4_rate <= 0.3:
        return "COLD", f"median {median} and only {ge4['games']}/{ge4['of']} games clearing 4 runs"
    return "NEUTRAL", (
        f"{ge4['games']}/{ge4['of']} games clearing 4 runs, median {median}, mean {profile['mean']}"
    )
