"""
lib/team_total_line_history.py
==================================
Derive each team's MARKET-EXPECTED run total for a past game from this
repository's own archived Kalshi team-total ladder, so recent offensive
form can be measured against what the market expected rather than
against a fixed number.

WHY THIS MATTERS MORE THAN RAW RUNS/GAME
----------------------------------------
"Scored 5" means two completely different things depending on whether
the market expected 5.5 or 3.5. A line-relative record ("cleared its own
team total in 2 of the last 7") is a statement about the offense BEATING
informed expectations; a raw mean is a statement about schedule, park
and opponent as much as about the offense.

WHAT THE ARCHIVE ACTUALLY GIVES US
----------------------------------
Kalshi does not list a single team-total line. It lists a LADDER of
"will <team> score over N.5 runs?" contracts. The market's implied
expectation is therefore not read off a field -- it is the point where
the ladder's own P(over) crosses 50%, which is the market's MEDIAN
expected runs. That is exactly the right reference for "did this offense
beat what the market expected of it".

REFUSES RATHER THAN GUESSES
---------------------------
No line is produced when the ladder does not bracket 50%, has fewer than
two priced rungs, or is materially non-monotone (P(over) must be
non-increasing as the threshold rises; a small crossing is tolerated as
bid/ask noise, a large one means the ladder is not usable evidence). A
refusal carries a reason code and the game is simply excluded from
line-relative statistics -- never imputed, never back-filled from
another game, and never replaced by a model number.

Pure: every function takes already-loaded records. The reader that
supplies them (scripts/fetch_team_offense_form.py, the research
experiment) owns all I/O.
"""

TEAM_TOTAL_FAMILY = "team_total"

# A team-total observation captured at or after first pitch is a LIVE
# price, not a pregame expectation, and must never enter a "what did the
# market expect before the game" line. lib.edgelab.market_universe
# already classifies this checkpoint; the name is reused, not redefined.
POST_START_CHECKPOINT = "POST_START"

# P(over) must be non-increasing as the threshold rises. Two adjacent
# rungs whose probabilities cross by less than this are treated as
# bid/ask noise; anything larger means the ladder is internally
# inconsistent and no line is derived from it.
MONOTONICITY_TOLERANCE = 0.06

MIN_RUNGS = 2

REASON_NO_RUNGS = "NO_PRICED_RUNGS"
REASON_TOO_FEW_RUNGS = "TOO_FEW_PRICED_RUNGS"
REASON_NON_MONOTONE = "LADDER_NOT_MONOTONE"
REASON_NO_CROSSING = "LADDER_DOES_NOT_BRACKET_50_PERCENT"


def _mid_probability(observation):
    """
    Pure. The observation's implied P(YES), from the bid/ask midpoint
    when both sides are present, else whichever single side is. Prices
    in this archive are in CENTS (see lib.edgelab.market_universe);
    returns a 0-1 probability, or None when nothing is priced.
    """
    bid, ask = observation.get("yesBid"), observation.get("yesAsk")
    if bid is not None and ask is not None:
        value = (float(bid) + float(ask)) / 2.0
    elif ask is not None:
        value = float(ask)
    elif bid is not None:
        value = float(bid)
    elif observation.get("lastPrice") is not None:
        value = float(observation["lastPrice"])
    else:
        return None
    return max(0.0, min(1.0, value / 100.0))


def latest_pregame_rungs(observations):
    """
    Pure. {(gameId, team): [(threshold, probability), ...ascending...]}
    built from the LATEST pregame observation of each team-total ticker.

    "Latest pregame" = the highest capturedAt among observations whose
    checkpoint is not POST_START. That is the closest thing the archive
    has to a closing line, and it never mixes in a live in-game price.
    """
    best_by_ticker = {}
    for obs in observations:
        if obs.get("marketFamily") != TEAM_TOTAL_FAMILY:
            continue
        if obs.get("checkpoint") == POST_START_CHECKPOINT:
            continue
        if obs.get("gameStartedAtCapture"):
            continue
        ticker = obs.get("marketTicker")
        threshold, team, game_id = obs.get("threshold"), obs.get("team"), obs.get("gameId")
        if not ticker or threshold is None or not team or not game_id:
            continue
        # Kalshi lists team totals as OVER rungs; anything else would
        # need its own inversion and is deliberately not guessed at.
        if (obs.get("comparisonOperator") or "OVER").upper() != "OVER":
            continue
        captured = obs.get("capturedAt") or ""
        current = best_by_ticker.get(ticker)
        if current is None or captured > current["capturedAt"]:
            best_by_ticker[ticker] = {
                "capturedAt": captured, "threshold": float(threshold),
                "team": team, "gameId": game_id, "observation": obs,
            }

    ladders = {}
    for entry in best_by_ticker.values():
        probability = _mid_probability(entry["observation"])
        if probability is None:
            continue
        ladders.setdefault((entry["gameId"], entry["team"]), []).append(
            (entry["threshold"], probability)
        )
    for key in ladders:
        ladders[key].sort(key=lambda pair: pair[0])
    return ladders


def implied_line_from_ladder(rungs):
    """
    Pure. (implied_line, reason). The market's MEDIAN expected runs: the
    threshold at which the ladder's P(over) crosses 50%, linearly
    interpolated between the two bracketing rungs.

    Returns (None, reason_code) for every ladder that cannot support a
    line -- see this module's docstring.
    """
    if not rungs:
        return None, REASON_NO_RUNGS
    if len(rungs) < MIN_RUNGS:
        return None, REASON_TOO_FEW_RUNGS

    for (t_low, p_low), (t_high, p_high) in zip(rungs, rungs[1:]):
        if p_high - p_low > MONOTONICITY_TOLERANCE:
            return None, (
                f"{REASON_NON_MONOTONE}: P(over {t_high})={p_high:.2f} exceeds "
                f"P(over {t_low})={p_low:.2f}"
            )

    above = [(t, p) for t, p in rungs if p >= 0.5]
    below = [(t, p) for t, p in rungs if p < 0.5]
    if not above or not below:
        return None, REASON_NO_CROSSING

    t_low, p_low = above[-1]      # highest threshold still >= 50%
    t_high, p_high = below[0]     # lowest threshold already < 50%
    if t_high <= t_low:
        return None, REASON_NO_CROSSING
    span = p_low - p_high
    if span <= 0:
        return None, REASON_NON_MONOTONE
    fraction = (p_low - 0.5) / span
    return round(t_low + fraction * (t_high - t_low), 3), None


def derive_team_total_lines(observations):
    """
    Pure. {(gameId, team): {"impliedLine", "rungs", "unavailableReason"}}
    for every team-total ladder in `observations`. An entry always
    exists for every ladder seen, so a caller can distinguish "no market
    was archived" from "a market was archived but was not usable".
    """
    result = {}
    for key, rungs in latest_pregame_rungs(observations).items():
        line, reason = implied_line_from_ladder(rungs)
        result[key] = {
            "impliedLine": line,
            "rungs": rungs,
            "unavailableReason": reason,
        }
    return result


def margin_vs_line(runs_scored, implied_line):
    """Pure. runs - the market's median expectation, or None when either
    side is missing. Positive means the offense beat what the market
    expected of it."""
    if runs_scored is None or implied_line is None:
        return None
    return round(float(runs_scored) - float(implied_line), 3)


REASON_MULTIPLE_GAMES = "MULTIPLE_GAMES_FOR_TEAM_THAT_DATE"


def team_lines_for_date(observations):
    """
    Pure. {team: implied_line} for ONE date's observations, plus a
    parallel {team: reason} for every team whose line could not be
    established.

    Returns (lines, reasons).

    A team with two distinct usable ladders that date is a doubleheader
    (or an identity collision -- the archive's gameId is a real gamePk
    for some rows and a composite "date_away_home_HHMM" string for
    others). Either way "the" line for that team on that date is not a
    single well-defined thing, so the team is EXCLUDED with
    MULTIPLE_GAMES_FOR_TEAM_THAT_DATE rather than having one leg's line
    silently stand in for both.
    """
    per_team = {}
    for (game_id, team), entry in derive_team_total_lines(observations).items():
        per_team.setdefault(team, []).append((game_id, entry))

    lines, reasons = {}, {}
    for team, entries in per_team.items():
        usable = [(game_id, e) for game_id, e in entries if e["impliedLine"] is not None]
        distinct = {round(e["impliedLine"], 3) for _gid, e in usable}
        if not usable:
            reasons[team] = entries[0][1]["unavailableReason"]
        elif len(distinct) > 1:
            reasons[team] = f"{REASON_MULTIPLE_GAMES}: {sorted(distinct)}"
        else:
            lines[team] = usable[0][1]["impliedLine"]
    return lines, reasons
