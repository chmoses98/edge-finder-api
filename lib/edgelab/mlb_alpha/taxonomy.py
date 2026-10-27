"""
lib/edgelab/mlb_alpha/taxonomy.py
=================================
What is this Kalshi series, is it MLB at all, and how far along is our ability
to price, settle and test it?

DESIGN RULES
  * An MLB-looking series is NEVER silently dropped. If no rule matches it is
    reported as UNCLASSIFIED_NEEDS_REVIEW, which is a visible research gap.
  * A "KXMLB" prefix is not proof of baseball: `KXMLBBGAME` is Mobile Legends
    esports. A title containing "baseball" is not proof of MLB: college
    baseball, the WBC and the Mexican league all match. Membership is decided
    by an explicit allow/deny table plus evidence strings, never by substring.
  * "Modelled" means a probability can be derived from a model we run, not that
    the model is any good. `modelStatus` says which model path would price the
    series; whether that price carries information is a separate question
    answered only by out-of-sample evidence.
  * Pure, stdlib-only, no I/O, no clock. Never raises on malformed input.

TICKER SHAPES (verified against the 2026 archive)
  event   <SERIES>-<YY><MON><DD><HHMM><AWAY><HOME>[G<n>]     (US/Eastern)
  market  GAME/F3/F5/F7 : -<TEAM>|-TIE
          SPREAD/F5SPREAD/TEAMTOTAL : -<TEAM><N>            (N = "wins by >= N" / "scores >= N")
          TOTAL/F5TOTAL : -<N>                              (N = "total >= N", Kalshi title says over N-0.5)
          RFI           : (none)
          INNINGWIN     : -<inning>-<TEAM>|-TIE
          INNINGTOTAL   : -<inning>-<N>
          EXTRAS        : -EXTRAS
          player props  : -<TEAM><PLAYERCODE>-<N>           (N = "N or more")
"""

import re
from datetime import timezone
from zoneinfo import ZoneInfo

from lib.edgelab.mlb_schedule import TEAM_ID_TO_ABBR

ET = ZoneInfo("America/New_York")
TEAM_ABBRS = frozenset(TEAM_ID_TO_ABBR.values())

# --- membership verdicts ----------------------------------------------------
MLB = "MLB"
NOT_MLB = "NOT_MLB"
MLB_EXHIBITION = "MLB_EXHIBITION"      # MLB players/teams, not a championship-season game
MIXED_COMBO = "MIXED_COMBO"            # multi-leg combination contracts; legs may include MLB
UNCLASSIFIED = "UNCLASSIFIED_NEEDS_REVIEW"

# --- domains ----------------------------------------------------------------
D_GAME = "GAME"                  # one contract set per MLB game
D_PLAYER_GAME = "PLAYER_GAME"    # player stat in one MLB game
D_SERIES = "SERIES"              # postseason series
D_SEASON = "SEASON_FUTURE"       # season-long totals, standings, futures
D_AWARD = "AWARD"
D_LEADER = "LEADER"
D_EVENT = "EVENT"                # All-Star, HR Derby, draft, ...
D_CORPORATE = "CORPORATE"        # labour, relocation, sale: no game path
D_OTHER = "OTHER"
D_NON_MLB = "NON_MLB"

# --- model paths ------------------------------------------------------------
MP_UNIFIED_SIM = "UNIFIED_SIM"        # derived from simulated team-run/inning states
MP_PLAYER_SIM = "PLAYER_SIM"          # needs lineup-level PA model (existing hitter engine)
MP_PITCHER_SIM = "PITCHER_SIM"        # needs starter workload / K model
MP_NONE = "NONE"

# --- model statuses ---------------------------------------------------------
MS_PRICED_SIM = "PRICED_BY_UNIFIED_SIM"
MS_PRICED_SIM_APPROX = "PRICED_BY_UNIFIED_SIM_APPROXIMATE"
MS_PLAYER_MODEL_EXISTS = "EXISTING_PLAYER_MODEL_NOT_WIRED"
MS_NO_MODEL = "NO_MODEL"
MS_NOT_APPLICABLE = "NOT_APPLICABLE"

# --- settlement truth in this repository -----------------------------------
ST_ARCHIVED = "SETTLEMENT_ARCHIVED"
ST_NOT_ARCHIVED = "SETTLEMENT_NOT_ARCHIVED"
ST_NOT_APPLICABLE = "NOT_APPLICABLE"

_PERIOD_INNINGS = {"FULL_GAME": 9, "F3": 3, "F5": 5, "F7": 7}


def _s(family, scope, domain, model_path, model_status, settlement, shape,
       edgelab_family=None, note=None):
    return {
        "family": family, "scope": scope, "domain": domain, "modelPath": model_path,
        "modelStatus": model_status, "settlement": settlement, "contractShape": shape,
        "edgelabFamily": edgelab_family, "note": note,
    }


# Exact per-series table for everything that has a game-level price path.
_PER_GAME = {
    "KXMLBGAME": _s("game_result", "FULL_GAME", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM,
                    ST_ARCHIVED, "WINNER", "game_result"),
    "KXMLBF3": _s("period_result", "F3", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                  ST_ARCHIVED, "WINNER_OR_TIE", "inning_result",
                  "inning split of team runs is calibrated, not observed, in the simulator"),
    "KXMLBF5": _s("period_result", "F5", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                  ST_ARCHIVED, "WINNER_OR_TIE", "inning_result"),
    "KXMLBF7": _s("period_result", "F7", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                  ST_ARCHIVED, "WINNER_OR_TIE", "inning_result"),
    "KXMLBSPREAD": _s("winning_margin", "FULL_GAME", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM,
                      ST_ARCHIVED, "TEAM_AT_LEAST_N", "winning_margin"),
    "KXMLBF5SPREAD": _s("period_margin", "F5", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                        ST_ARCHIVED, "TEAM_AT_LEAST_N", "winning_margin",
                        "archived settlements pre-#176 graded this on the full-game score"),
    "KXMLBTOTAL": _s("game_total", "FULL_GAME", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM,
                     ST_ARCHIVED, "TOTAL_AT_LEAST_N", "game_total",
                     "Kalshi settles rung N as total >= N; archive used > N before 2026-09-01"),
    "KXMLBF5TOTAL": _s("period_total", "F5", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                       ST_ARCHIVED, "TOTAL_AT_LEAST_N", "inning_total"),
    "KXMLBTEAMTOTAL": _s("team_total", "FULL_GAME", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM,
                         ST_ARCHIVED, "TEAM_AT_LEAST_N", "team_total"),
    "KXMLBRFI": _s("first_inning_run", "INNING_1", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                   ST_ARCHIVED, "BINARY", "first_inning_run",
                   "real-money suspended 2026-08 after losing to Kalshi and a constant"),
    "KXMLBINNINGWIN": _s("inning_winner", "INNING_N", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                         ST_NOT_ARCHIVED, "INNING_WINNER_OR_TIE", None,
                         "no linescore-level settlement truth in the archive"),
    "KXMLBINNINGTOTAL": _s("inning_total", "INNING_N", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                           ST_NOT_ARCHIVED, "INNING_TOTAL_AT_LEAST_N", None,
                           "no linescore-level settlement truth in the archive"),
    "KXMLBEXTRAS": _s("extra_innings", "FULL_GAME", D_GAME, MP_UNIFIED_SIM, MS_PRICED_SIM_APPROX,
                      ST_NOT_ARCHIVED, "BINARY", None),
}

# Player-game props: price path exists, a lineup-level model is a separate build.
_PLAYER_PROPS = {
    "KXMLBHIT": ("hitter_hits", "hitter_hits", MP_PLAYER_SIM),
    "KXMLBTB": ("hitter_total_bases", "hitter_total_bases", MP_PLAYER_SIM),
    "KXMLBHRR": ("hitter_hits_runs_rbis", "hitter_hits_runs_rbis", MP_PLAYER_SIM),
    "KXMLBRBI": ("hitter_rbis", "hitter_rbis", MP_PLAYER_SIM),
    "KXMLBSB": ("hitter_stolen_bases", "hitter_stolen_bases", MP_PLAYER_SIM),
    "KXMLBHR": ("hitter_home_runs", None, MP_PLAYER_SIM),
    "KXMLBKS": ("pitcher_strikeouts", "pitcher_strikeouts", MP_PITCHER_SIM),
    "KXMLBOUTS": ("pitcher_outs", "pitcher_outs", MP_PITCHER_SIM),
    "KXMLBHA": ("pitcher_hits_allowed", None, MP_PITCHER_SIM),
    "KXMLBWA": ("pitcher_walks_allowed", None, MP_PITCHER_SIM),
    "KXMLBWALK": ("hitter_walks", None, MP_PLAYER_SIM),
    "KXMLBERA": ("pitcher_earned_runs", None, MP_PITCHER_SIM),
}

# Explicit not-MLB table. Every entry carries the evidence for the verdict.
_NOT_MLB_EXACT = {
    "KXMLBBGAME": "Mobile Legends: Bang Bang esports (title 'Mobile Legends Bang Bang Game'); the KXMLB prefix is a coincidence",
    "KXEWCMLBB": "Mobile Legends: Bang Bang esports (Esports World Cup)",
    "KXMLBWORLD": "World Baseball Classic (national teams), not MLB",
    "KXLMBGAME": "Mexican Baseball League (LMB)",
    "KXCONGRESSBASEBALL": "Congressional Baseball Game (novelty)",
    "KXATTENDMLB": "celebrity attendance market, no MLB game outcome",
    "KXMEDIACOVERMLBTHESHOW": "video-game cover athlete market",
    "KXESPYMLB": "ESPY award market, not an MLB-administered outcome",
}
_NOT_MLB_PREFIX = (
    ("KXNCAA", "college baseball"),
    ("KXTEAMSINNCAA", "college baseball"),
    ("KXWBC", "World Baseball Classic (national teams)"),
)

_EXHIBITION_EXACT = {
    "KXMLBSTGAME": "spring-training exhibition games",
    "KXMLBASGAME": "All-Star Game exhibition",
    "KXMLBFTGAME": "Futures Game (prospects exhibition)",
}

_CORPORATE_EXACT = {
    "KXMLBCBA", "MLBCBA", "KXMLBSTRIKE", "KXMLBOAK", "MLBOAK", "KXMLBTEAMSALE",
    "KXCITYMLBEXPAND", "KXMLBTRADE", "KXMLBDRAFTPICK", "KXMLBDRAFTTOP",
}

# (regex on series ticker, domain, family, note). First match wins.
_RULES = [
    (r"^KXMLBSERIES(GAMES|GAMETOTAL|SPREAD|SCORE|EXACT|HR)$", D_SERIES, "playoff_series_prop", "postseason series structure"),
    (r"^KXMLBSERIES$", D_SERIES, "playoff_series_winner", None),
    (r"^KXMLB(WSOUTCOME|WSMVP)$", D_SERIES, "world_series_outcome", None),
    (r"^KXMLB(ALCSMVP|NLCSMVP)$", D_AWARD, "postseason_mvp", None),
    (r"^KXMLB(WS|AL|NL)$|^KXMLB$|^KXWS(AL|NL)$|^KXTEAMSINWS$", D_SEASON, "pennant_world_series_future", None),
    (r"^KXMLB(AL|NL)(EAST|CENT|WEST)$|^KXMLBDIVWINNER$|^KXMLBCHAMPDIVISION$|^KXMLBCHAMPLEAGUE$", D_SEASON, "division_future", None),
    (r"^KXMLBWINS(-[A-Z]+)?$", D_SEASON, "team_season_wins", "one series per franchise (base series also appears in market rows)"),
    (r"^KXMLB(100LOSS|500|BESTRECORD|WORSTRECORD|PLAYOFFS|PLAYOFFHOST|PLAYOFFHRS|LEADERPLAYOFF|SEEDWIN|MATCHUP|ALCSQUAL|NLCSQUAL)$", D_SEASON, "season_structure_future", None),
    (r"^KXMLB(LSTREAK|WSTREAK|SISTREAK|WALKOFFS|FASTPITCH|SEASONHR|SEASONGAMES|STAT|STATCOUNT|TEAMSTAT|TRIPLECROWN)$", D_SEASON, "season_stat_future", None),
    (r"^KXLEADERMLB", D_LEADER, "season_leader", None),
    (r"^KXMLB(AL|NL)(MVP|CY|ROTY|MOTY|CPOTY|RELOTY|HAARON)$|^KXMLB(AWARDFIN|AWARDCOMBO|GG|SS|EOTY)$", D_AWARD, "season_award", None),
    (r"^KXMLBPLAYER(OTM|OTW)$|^KXMLBPITCHEROTM$", D_AWARD, "monthly_weekly_award", None),
    (r"^KXMLBHRDERBY|^KXMLBALLSTAR|^KXMLBASG", D_EVENT, "all_star_event", "mid-season only"),
    (r"^KXMLB(FOD|FODTEAMS|OPENINGDAY|MENTION|DEBUT|RETURN|NEXTTEAM|COACH|PLAYEROTM)$|^KXNEXTTEAMMLB$|^KXCOACHOUTMLB(DATE)?$|^KXNEXTMANAGERMLB$|^KXMLBNEXT", D_EVENT, "league_news_event", "resolves on news, no game path"),
    (r"^KXMLBPITCH$", D_EVENT, "player_pitching_event", "season-level event, not a per-game ticker"),
    (r"^KXMLB(DELAY|STARTINGLINEUP|STARTER|START|NEXTHR)$", D_GAME, "game_specials", "per-game specials; settlement source not wired"),
]

_RULES_C = [(re.compile(p), d, f, n) for p, d, f, n in _RULES]


def classify_series(series_ticker, title=None):
    """
    Classify one Kalshi series ticker. Always returns a dict; never raises.
    Keys: seriesTicker, title, membership, membershipReason, domain, family,
    scope, modelPath, modelStatus, settlement, contractShape, edgelabFamily, note.
    """
    t = (series_ticker or "").strip().upper()
    base = {
        "seriesTicker": t or None, "title": title, "membership": UNCLASSIFIED,
        "membershipReason": None, "domain": D_OTHER, "family": None, "scope": None,
        "modelPath": MP_NONE, "modelStatus": MS_NO_MODEL, "settlement": ST_NOT_APPLICABLE,
        "contractShape": None, "edgelabFamily": None, "note": None,
    }
    if not t:
        base["membershipReason"] = "empty_series_ticker"
        return base

    if t in _NOT_MLB_EXACT:
        base.update(membership=NOT_MLB, membershipReason=_NOT_MLB_EXACT[t],
                    domain=D_NON_MLB, modelStatus=MS_NOT_APPLICABLE)
        return base
    for prefix, why in _NOT_MLB_PREFIX:
        if t.startswith(prefix):
            base.update(membership=NOT_MLB, membershipReason=why, domain=D_NON_MLB,
                        modelStatus=MS_NOT_APPLICABLE)
            return base
    if t.startswith("KXMVE"):
        base.update(membership=MIXED_COMBO,
                    membershipReason="Kalshi multi-variate (combination) event; legs can span sports",
                    domain=D_OTHER, family="multi_leg_combo", modelStatus=MS_NO_MODEL,
                    note="price only by decomposing legs (see docs/KALSHI_COMBO_CONTRACT_WAGERS.md); never pooled with single-leg MLB markets")
        return base
    if t in _EXHIBITION_EXACT:
        base.update(membership=MLB_EXHIBITION, membershipReason=_EXHIBITION_EXACT[t],
                    domain=D_EVENT, family="exhibition_game", modelStatus=MS_NO_MODEL,
                    note="not a championship-season game; excluded from regular/postseason pooling")
        return base
    if t in _CORPORATE_EXACT:
        base.update(membership=MLB, membershipReason="MLB league/business event",
                    domain=D_CORPORATE, family="league_business_event",
                    modelStatus=MS_NOT_APPLICABLE, note="no per-game price path")
        return base

    if t in _PER_GAME:
        base.update(_PER_GAME[t])
        base["membership"] = MLB
        base["membershipReason"] = "allowlisted per-game MLB series"
        return base
    if t in _PLAYER_PROPS:
        fam, edge_fam, path = _PLAYER_PROPS[t]
        base.update(
            membership=MLB, membershipReason="allowlisted MLB player-game prop series",
            domain=D_PLAYER_GAME, family=fam, scope="FULL_GAME", modelPath=path,
            modelStatus=MS_PLAYER_MODEL_EXISTS if edge_fam else MS_NO_MODEL,
            settlement=ST_ARCHIVED if edge_fam else ST_NOT_ARCHIVED,
            contractShape="PLAYER_AT_LEAST_N", edgelabFamily=edge_fam,
        )
        if not edge_fam:
            base["note"] = "settlement rows exist only as family 'unknown' (tens of rows); not testable yet"
        return base

    for rx, domain, family, note in _RULES_C:
        if rx.search(t):
            base.update(membership=MLB, membershipReason="matched MLB rule %s" % rx.pattern[:48],
                        domain=domain, family=family, modelStatus=MS_NO_MODEL,
                        settlement=ST_NOT_ARCHIVED, note=note)
            return base

    base["membershipReason"] = "no taxonomy rule matched; reported, never dropped"
    if t.startswith("KXMLB") or "MLB" in t:
        base["note"] = "MLB-looking ticker with no rule: needs human review before any use"
    return base


# --- market ticker parser ----------------------------------------------------

_EVENT_RE = re.compile(
    r"^(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hhmm>\d{4})(?P<teams>[A-Z]+?)(?:G(?P<dh>\d))?$")
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def _split_teams(blob):
    out = []
    for cut in range(2, len(blob) - 1):
        a, h = blob[:cut], blob[cut:]
        if a in TEAM_ABBRS and h in TEAM_ABBRS:
            out.append((a, h))
    return out


def parse_event_segment(segment):
    """Parse '26OCT152000MILLAD' / '26AUG291915BOSNYYG2'. Returns dict or None.
    Scheduled start is converted from US/Eastern with real DST rules (the older
    lib.edgelab.mlb_alpha_identity assumes UTC-4 forever, which is wrong from
    2026-11-01 onward)."""
    m = _EVENT_RE.match(segment or "")
    if not m:
        return None
    mon = _MON.get(m.group("mon"))
    if mon is None:
        return None
    splits = _split_teams(m.group("teams"))
    if len(splits) != 1:
        return None
    from datetime import datetime
    try:
        local = datetime(2000 + int(m.group("yy")), mon, int(m.group("dd")),
                         int(m.group("hhmm")[:2]), int(m.group("hhmm")[2:]), tzinfo=ET)
    except ValueError:
        return None
    away, home = splits[0]
    dh = m.group("dh")
    return {
        "gameDate": local.strftime("%Y-%m-%d"),
        "scheduledStartUtc": local.astimezone(timezone.utc),
        "awayTeam": away, "homeTeam": home,
        "doubleheaderGame": int(dh) if dh else None,
        "gameKey": "%s_%s_%s_%s%s" % (local.strftime("%Y-%m-%d"), away, home, m.group("hhmm"),
                                      "_G%s" % dh if dh else ""),
    }


_INT = re.compile(r"^\d+$")


def _team_and_n(suffix, teams):
    for team in sorted(teams, key=len, reverse=True):
        if suffix.startswith(team) and _INT.match(suffix[len(team):] or "x"):
            return team, int(suffix[len(team):])
    return None, None


def parse_market_ticker(ticker):
    """
    Parse any Kalshi MLB-associated market ticker.

    Returns dict with: ticker, seriesTicker, kind (GAME_MARKET / PLAYER_GAME_MARKET /
    SERIES_MARKET / SEASON_MARKET / UNPARSED), classification (classify_series),
    plus for game markets: game fields, selection ('HOME'/'AWAY'/'TIE'/None), team,
    threshold (>= N integer), inning, player, side semantic. `parseStatus` is
    PARSED or an explicit reason string. Never raises, never guesses.
    """
    out = {"ticker": ticker, "seriesTicker": None, "kind": "UNPARSED",
           "parseStatus": "EMPTY_TICKER", "classification": None}
    if not ticker or not isinstance(ticker, str):
        return out
    parts = ticker.strip().upper().split("-")
    series = parts[0]
    out["seriesTicker"] = series
    cls = classify_series(series)
    out["classification"] = cls
    if len(parts) < 2:
        out["parseStatus"] = "NO_EVENT_SEGMENT"
        return out

    if cls["domain"] in (D_GAME, D_PLAYER_GAME) or cls["membership"] == MLB_EXHIBITION:
        ev = parse_event_segment(parts[1])
        if ev is None:
            out["parseStatus"] = "EVENT_SEGMENT_UNRESOLVED:%s" % parts[1]
            return out
        out.update(ev)
        teams = {ev["awayTeam"]: "AWAY", ev["homeTeam"]: "HOME"}
        rest = parts[2:]
        shape = cls["contractShape"]
        out["kind"] = "PLAYER_GAME_MARKET" if cls["domain"] == D_PLAYER_GAME else "GAME_MARKET"
        out["parseStatus"] = "PARSED"

        def fail(why):
            out["parseStatus"] = why
            return out

        if cls["domain"] == D_PLAYER_GAME:
            if len(rest) != 2 or not _INT.match(rest[1]):
                return fail("MALFORMED_PLAYER_SUFFIX")
            blob = rest[0]
            team = next((t for t in sorted(teams, key=len, reverse=True) if blob.startswith(t)), None)
            if team is None:
                return fail("PLAYER_TEAM_NOT_IN_EVENT")
            out.update(team=team, side=teams[team], player=blob[len(team):], threshold=int(rest[1]))
            return out
        if shape in ("WINNER", "WINNER_OR_TIE"):
            if len(rest) != 1:
                return fail("MALFORMED_WINNER_SUFFIX")
            if rest[0] == "TIE" and shape == "WINNER_OR_TIE":
                out.update(selection="TIE", team=None)
            elif rest[0] in teams:
                out.update(selection=teams[rest[0]], team=rest[0])
            else:
                return fail("TEAM_NOT_IN_EVENT")
            return out
        if shape == "TEAM_AT_LEAST_N":
            if len(rest) != 1:
                return fail("MALFORMED_TEAM_N_SUFFIX")
            team, n = _team_and_n(rest[0], teams)
            if team is None:
                return fail("TEAM_NOT_IN_EVENT_OR_NO_N")
            out.update(team=team, selection=teams[team], threshold=n)
            return out
        if shape == "TOTAL_AT_LEAST_N":
            if len(rest) != 1 or not _INT.match(rest[0]):
                return fail("MALFORMED_TOTAL_SUFFIX")
            out.update(threshold=int(rest[0]))
            return out
        if shape == "BINARY":
            if series == "KXMLBEXTRAS" and rest != ["EXTRAS"]:
                return fail("MALFORMED_EXTRAS_SUFFIX")
            if series == "KXMLBRFI" and rest:
                return fail("UNEXPECTED_RFI_SUFFIX")
            return out
        if shape == "INNING_WINNER_OR_TIE":
            if len(rest) != 2 or not _INT.match(rest[0]):
                return fail("MALFORMED_INNINGWIN_SUFFIX")
            out["inning"] = int(rest[0])
            if rest[1] == "TIE":
                out.update(selection="TIE")
            elif rest[1] in teams:
                out.update(selection=teams[rest[1]], team=rest[1])
            else:
                return fail("TEAM_NOT_IN_EVENT")
            return out
        if shape == "INNING_TOTAL_AT_LEAST_N":
            if len(rest) != 2 or not (_INT.match(rest[0]) and _INT.match(rest[1])):
                return fail("MALFORMED_INNINGTOTAL_SUFFIX")
            out.update(inning=int(rest[0]), threshold=int(rest[1]))
            return out
        # game-specials with no described grammar
        out["parseStatus"] = "SERIES_GRAMMAR_NOT_DESCRIBED"
        return out

    # non-game series: identity only
    out["kind"] = "SERIES_MARKET" if cls["domain"] == D_SERIES else "SEASON_MARKET"
    out["parseStatus"] = "PARSED_IDENTITY_ONLY" if len(parts) >= 3 else "NO_MARKET_SEGMENT"
    out["eventSegment"] = parts[1]
    out["marketSegment"] = "-".join(parts[2:]) or None
    return out
