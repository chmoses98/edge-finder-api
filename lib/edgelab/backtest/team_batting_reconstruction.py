"""
lib/edgelab/backtest/team_batting_reconstruction.py
====================================================================
MLB-RSCH-0012: PIT-safe team-level batting-component reconstruction --
the O2/O3 candidate offense estimators' data layer.

Reuses, does not reimplement:
  - lib.edgelab.backtest.bullpen_backtest_reconstruction.is_strictly_before
    (MLB-RSCH-0003) -- the SAME no-lookahead guard every other backtest
    component in this repo uses, re-exported here rather than
    duplicated.
  - lib.edgelab.backtest.team_offense_recency_reconstruction.
    prior_games_this_season/season_to_date_rate (MLB-RSCH-0005) --
    field-agnostic, so this module attaches NEW per-game rate fields
    (bbRate, kRate, hrRate, xbhRate, obpProxy, sluggingProxy) onto each
    team-game record and calls those two functions UNCHANGED, exactly
    the reuse pattern lib.edgelab.backtest.proxy_enrichment already
    established for bullpen ER/9.

TRANSPARENT COMPONENT MODELING, NOT A BLACK BOX (mission instruction):
every derived rate below is a simple, auditable ratio of officially
reported team-game aggregate batting counts (MLB Stats API boxscore's
own teams.<side>.teamStats.batting block) -- no wOBA-style linear-
weights constant is used or approximated, since a genuine wOBA
constant set would itself need to be fit from full-season run-value
data this module has no PIT-safe access to reconstruct per the
mission's own instruction ("do not reproduce wOBA constants from
future data unless season-specific constants can be constructed
PIT-safely").
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.backtest.bullpen_backtest_reconstruction import is_strictly_before  # noqa: F401  (re-exported for callers)

# Every field this module ever reads from a boxscore's teamStats.batting
# block -- an aggregate, officially reported per-game team total, never
# a per-player sum this module computes itself (avoids silently missing
# a substitution/pinch-hitter row a player-level sum would need to
# handle correctly).
_BATTING_COUNT_FIELDS = (
    "plateAppearances", "atBats", "hits", "doubles", "triples", "homeRuns",
    "baseOnBalls", "strikeOuts", "hitByPitch", "sacFlies", "runs",
)


def _nonneg_int(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def extract_team_batting_line(boxscore, side):
    """
    Pure. One team's own official aggregate batting line for one game,
    from boxscore["teams"][side]["teamStats"]["batting"]. Returns None
    (never a fabricated all-zero line) if the boxscore or that block is
    unavailable -- an unavailable batting line for one game must never
    silently look identical to "this team went 0-for-everything."
    """
    if not boxscore:
        return None
    team_block = (boxscore.get("teams") or {}).get(side) or {}
    batting = (team_block.get("teamStats") or {}).get("batting") or {}
    if not batting:
        return None
    line = {field: _nonneg_int(batting.get(field)) for field in _BATTING_COUNT_FIELDS}
    if line.get("plateAppearances") is None:
        return None
    return line


def _rate(numerator, denominator):
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def derived_batting_rates(line):
    """
    Pure. {bbRate, kRate, hrRate, xbhRate, obpProxy, sluggingProxy,
    isoProxy} from one game's raw batting line -- every rate is a plain
    ratio of officially reported counts, denominated by plateAppearances
    (rate stats) or atBats (slugging), matching standard definitions:
      obpProxy    = (H + BB + HBP) / (AB + BB + HBP + SF)   -- standard OBP
      totalBases  = H + 2B + 2*3B + 3*HR
      sluggingProxy = totalBases / AB                        -- standard SLG
      isoProxy    = sluggingProxy - (H / AB)                 -- Isolated Power (SLG - AVG)
    None fields propagate (never a fabricated 0.0) when their inputs are missing.
    """
    if line is None:
        return None
    pa, ab, h = line.get("plateAppearances"), line.get("atBats"), line.get("hits")
    doubles, triples, hr = line.get("doubles"), line.get("triples"), line.get("homeRuns")
    bb, k, hbp, sf = line.get("baseOnBalls"), line.get("strikeOuts"), line.get("hitByPitch"), line.get("sacFlies")

    xbh = None
    if doubles is not None and triples is not None and hr is not None:
        xbh = doubles + triples + hr

    obp_denominator = None
    obp_numerator = None
    if ab is not None and bb is not None:
        hbp_safe = hbp or 0
        sf_safe = sf or 0
        if h is not None:
            obp_numerator = h + bb + hbp_safe
            obp_denominator = ab + bb + hbp_safe + sf_safe

    total_bases = None
    if h is not None and doubles is not None and triples is not None and hr is not None:
        total_bases = h + doubles + 2 * triples + 3 * hr

    slugging = _rate(total_bases, ab)
    avg = _rate(h, ab)
    iso = round(slugging - avg, 6) if slugging is not None and avg is not None else None

    return {
        "bbRate": _rate(bb, pa),
        "kRate": _rate(k, pa),
        "hrRate": _rate(hr, pa),
        "xbhRate": _rate(xbh, pa),
        "obpProxy": _rate(obp_numerator, obp_denominator),
        "sluggingProxy": slugging,
        "isoProxy": iso,
    }


DERIVED_RATE_FIELDS = ("bbRate", "kRate", "hrRate", "xbhRate", "obpProxy", "sluggingProxy", "isoProxy")


def team_batting_games(team_games, batting_lines_by_game_pk):
    """
    Pure. `team_games`: one team's extract_team_games_from_schedule()
    output (has "date"/"gameNumber"/"gamePk", already sorted).
    `batting_lines_by_game_pk`: {gamePk: {"away": line_or_None, "home":
    line_or_None}} -- the cached per-game batting fetch, keyed by
    gamePk with BOTH sides (a single cached record serves both teams'
    schedules, same dedup convention as the bullpen cache). Returns a
    NEW list, same date/gameNumber ordering, each entry carrying the
    raw batting line's fields plus every DERIVED_RATE_FIELDS value for
    THIS team's own side of that game (None for a game whose batting
    line isn't cached/available -- never fabricated). Shaped so
    lib.edgelab.backtest.team_offense_recency_reconstruction.
    season_to_date_rate() can be called directly on the result for any
    of the count or rate field names above.
    """
    out = []
    for g in team_games:
        game_pk = g.get("gamePk")
        side = g.get("side")
        sides = batting_lines_by_game_pk.get(game_pk) or {}
        line = sides.get(side) if side else None
        rates = derived_batting_rates(line)
        entry = dict(g)
        for field in _BATTING_COUNT_FIELDS:
            entry[field] = line.get(field) if line else None
        for field in DERIVED_RATE_FIELDS:
            entry[field] = rates.get(field) if rates else None
        out.append(entry)
    return out
