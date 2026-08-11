#!/usr/bin/env python3
"""
lib/research/hitter_feature_context.py
========================================
Hitter Projection Engine -- Phase 1 canonical feature foundation.

WHAT THIS MODULE IS (AND IS NOT)
----------------------------------
This is NOT a hitter probability model. It does not price 1+ hit, HR,
total bases, or any other Kalshi hitter market. It produces exactly one
thing: a canonical, non-fabricated PREGAME FEATURE RECORD per confirmed
hitter, consolidating every piece of pregame-available information this
repository already has about that hitter's matchup, plus explicit,
honest placeholders for every dimension a future model will need but
this repo cannot yet populate.

Every future hitter market (1+ hit, 2+ hits, alternate hit lines, HR,
total bases, alternate TB lines, RBI, runs, walks, strikeouts, fantasy
score, ...) will be priced from a full plate-appearance outcome
distribution built on top of records this module produces -- see this
module's docstring section "FIELD STATUS LEGEND" for how each of that
future model's required inputs is currently classified, and
docs/HITTER_FEATURE_FOUNDATION.md for the full audit this module's
schema is derived from.

REUSE, NOT DUPLICATION
------------------------
This module deliberately does not re-implement anything PR #77 (or any
earlier mission) already built:
  - Confirmed-lineup identity/order/handedness and per-batter platoon
    splits (scripts/fetch_lineups.py, scripts/fetch_batter_platoon_splits.py)
    are read as-is from g[side+'TeamStats']['confirmedLineup'].
  - The lineup-vs-starter-handedness platoon adjustment
    (lib.research.platoon_context.build_offense_platoon_context /
    hitter_platoon_value) is called directly, not reimplemented -- this
    module's own STATUS_LINEUP_UNCONFIRMED / STATUS_MISSING_DATA
    sentinels are literally the same constants platoon_context.py
    defines, imported here rather than re-declared with a different
    spelling.
  - Starter identity/handedness/arsenal-adjacent rate stats
    (api/pitchers.js, api/savant.js via scripts/fetch_savant_pitchers.py)
    are read as-is from g[oppSide]['pitcher'] / g[oppSide]['pitcherSavant'].
  - Bullpen quality/recent-workload exposure (api/bullpen.js,
    scripts/fetch_bullpen_usage.py) is read as-is from g[oppSide]['bullpen'].
  - Park run-index (api/slate.js's PARK_WEATHER) is read as-is from
    g['park'].
  - Live weather (api/weather.js / data/weather.json) is accepted as an
    optional caller-supplied lookup (this module has no network/file
    I/O of its own -- see SCOPE below) rather than re-fetched.

FIELD STATUS LEGEND
---------------------
Every domain block below (and every individual field inside a block
that this repo cannot yet populate) carries one of these five statuses,
matching the exact four-way classification this Phase 1 mission's own
audit used, plus OK/MISSING_DATA/LINEUP_UNCONFIRMED reused verbatim from
lib.research.platoon_context so a caller already familiar with that
module's vocabulary does not have to learn a second one:

  STATUS_OK / STATUS_AVAILABLE
      Real, non-fabricated data is present for this hitter today.
  STATUS_PARTIAL
      Some real data is present, but the field/block is materially
      thinner than the long-term design calls for (e.g. a single
      run-index park factor instead of event-specific, handedness-split
      factors).
  STATUS_MISSING_DATA
      The data SOURCE exists in this repo, but this specific
      hitter/game lacks it right now (e.g. lineup confirmed but this
      one batter has no seasonWOBA on file).
  STATUS_NOT_COMPUTED
      The underlying data IS reachable from an existing fetch layer in
      this repo (e.g. api/savant.js can return a batter's whiffPct/
      hardHitPct/barrelPct given playerIds) but no script currently
      wires/persists it per-batter -- this is a wiring gap, not a
      missing data source. See each field's "note" for the exact file
      that already has the capability.
  STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES
      No code path anywhere in this repo can currently produce this
      data at all (e.g. bat-tracking, catcher framing, umpire zone
      tendency, sprint speed/defense) -- confirmed absent by direct
      repository audit, not merely unwired.

Never populate a field with a fabricated/guessed value to avoid one of
these statuses -- an honest MISSING_DATA/NOT_COMPUTED/
UNAVAILABLE_FROM_CURRENT_SOURCES beats a plausible-looking number a
future backtest cannot distinguish from a real one.

SCOPE / SAFETY
----------------
Every function here is pure: no file I/O, no network, no clock reads
(the pipeline artifact envelope stamps createdAt -- this module never
does), no mutation of any argument, deterministic given deterministic
inputs. build_hitter_feature_context(g, offense_side, ...) is a pure
function of its arguments alone, so it is trivially safe to call from
any pipeline stage, any test, or any future replay -- it can never read
anything but what it is handed, so it can never leak postgame data into
a pregame record as long as its caller only ever hands it pregame `g`.

An unconfirmed lineup never fabricates hitters, batting order, or
identity -- build_hitter_feature_context() returns an empty `hitters`
list with status=STATUS_LINEUP_UNCONFIRMED instead (same requirement
platoon_context.py enforces, reused here rather than re-derived).
"""

from typing import Optional

from lib.research.platoon_context import (
    STATUS_OK,
    STATUS_LINEUP_UNCONFIRMED,
    STATUS_MISSING_DATA,
    MIN_PA_HITTER_SPLIT,
    classify_hand,
    resolve_effective_hand,
    hitter_platoon_value,
    build_offense_platoon_context,
)

# ── Statuses new to this module (see FIELD STATUS LEGEND above) ────────────
STATUS_AVAILABLE = "AVAILABLE"
STATUS_PARTIAL = "PARTIAL"
STATUS_NOT_COMPUTED = "NOT_COMPUTED"
STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES = "UNAVAILABLE_FROM_CURRENT_SOURCES"

SCHEMA_VERSION = "1.0"


def _field(status, value=None, note=None, **extra):
    """One leaf field inside a domain block -- status + optional value/note."""
    d = {"status": status, "value": value}
    if note:
        d["note"] = note
    d.update(extra)
    return d


def _unavailable(note, **extra):
    return _field(STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES, value=None, note=note, **extra)


def _not_computed(note, **extra):
    return _field(STATUS_NOT_COMPUTED, value=None, note=note, **extra)


# ── B. Statcast contact quality -- fields not currently persisted per-batter ─
_STATCAST_NOT_COMPUTED_FIELDS = {
    "xBA": "Savant leaderboard/custom exposes this column; never selected for batters by any fetcher in this repo.",
    "xSLG": "Same leaderboard as xBA -- not currently selected.",
    "xwOBAcon": "Not currently selected from the Savant leaderboard.",
    "avgEV": "api/savant.js can return exitVeloAvg per batter given playerIds; not persisted per-batter by any script (only the team-level average reaches data/savant_team.json).",
    "maxEV": "Not fetched anywhere.",
    "ev50": "Not fetched anywhere (requires per-batted-ball EV percentile, which requires raw batted-ball rows this repo does not ingest).",
    "ev90": "Same as ev50.",
    "hardHitPct": "api/savant.js can return this per batter given playerIds; not persisted per-batter (only api/enrich.js?type=batting's team-level aggregate is written to disk).",
    "barrelPct": "Same wiring gap as hardHitPct.",
    "barrelsPerPA": "Not fetched anywhere.",
    "barrelsPerBBE": "Not fetched anywhere (also requires a batted-ball-event denominator this repo does not track).",
    "avgLaunchAngle": "Not fetched anywhere.",
    "launchAngleDistribution": "Requires raw batted-ball-event rows this repo does not ingest.",
    "sweetSpotPct": "Not fetched anywhere.",
    "gbFbLdDistribution": "Team-level fbPct exists (data/savant_team.json); no batter-level GB/FB/LD split is fetched.",
    "xHR": "No expected-HR-equivalent metric is fetched anywhere.",
}

# ── D. Plate discipline -- entirely absent beyond the 3 outcome rates below ──
_PLATE_DISCIPLINE_UNAVAILABLE_FIELDS = [
    "swingPct", "contactPct", "zSwingPct", "zContactPct", "oSwingPct",
    "oContactPct", "zonePct", "calledStrikePct", "swingingStrikePct",
    "firstPitchSwingPct", "firstPitchStrikePct", "foulPct",
    "twoStrikeContactPct", "twoStrikeChasePct", "meatballSwingPct",
    "meatballContactPct", "heartFreq", "shadowFreq", "chaseFreq", "wasteFreq",
]

_BASELINE_HORIZON_KEYS = ("career", "previousSeason", "currentSeason", "rolling90d", "rolling60d", "rolling30d")

_BASELINE_STAT_FIELDS = (
    "PA", "AB", "H", "1B", "2B", "3B", "HR", "BB", "IBB", "HBP", "K", "SF",
    "AVG", "OBP", "SLG", "ISO", "BABIP", "wOBA", "wRCPlus", "KPct", "BBPct",
    "HRPerPA", "HRPerFB", "GBPct", "FBPct", "LDPct", "PullPct", "CenterPct", "OppoPct",
)


def _baseline_talent(hitter) -> dict:
    """
    A. Hitter baseline talent, across the six required historical
    horizons. Only currentSeason.wOBA (and, when present, seasonPA) is
    real data in this repo today -- everything else in every horizon is
    an explicit, honest placeholder (never a fabricated number) so a
    future ingestion pass has an exact schema to fill in, and so a
    consumer can immediately tell "we don't have this" from "this
    hitter genuinely walked zero times."
    """
    horizons = {}
    for key in _BASELINE_HORIZON_KEYS:
        stats = {name: None for name in _BASELINE_STAT_FIELDS}
        if key == "currentSeason":
            season_woba = hitter.get("seasonWOBA")
            season_pa = hitter.get("seasonPA")
            stats["wOBA"] = season_woba
            stats["PA"] = season_pa
            horizons[key] = {
                "status": STATUS_AVAILABLE if season_woba is not None else STATUS_MISSING_DATA,
                "stats": stats,
                "source": "scripts/fetch_lineups.py seasonWOBA (data/savant_team.json batters / data/teamstats.json batterWOBA)",
                "note": "Only wOBA (a rate stat) and PA are populated -- no counting-stat (H/HR/BB/K/...) history is ingested per batter anywhere in this repo yet.",
            }
        else:
            horizons[key] = {
                "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
                "stats": stats,
                "source": None,
                "note": "No career / prior-season / rolling-window per-batter data source is ingested anywhere in this repo -- every existing batter fetch (api/savant.js, api/enrich.js?type=batting, scripts/fetch_lineups.py) returns a single current-season aggregate only.",
            }
    return {
        "status": STATUS_PARTIAL,
        "horizons": horizons,
        "note": "Older-horizon-as-prior shrinkage described in the Phase 1 spec cannot be implemented until at least one additional horizon is ingested (Phase 2+).",
    }


def _platoon_block(hitter, opposing_starter_hand) -> dict:
    """
    E. Platoon -- thin wrapper around lib.research.platoon_context's own
    hitter_platoon_value(), reused verbatim rather than reimplemented.
    """
    woba, pa, used_fallback = hitter_platoon_value(hitter, opposing_starter_hand) if opposing_starter_hand else (None, None, False)
    effective_side = resolve_effective_hand(hitter.get("batSide"), opposing_starter_hand)
    raw_splits = hitter.get("platoonSplits")
    status = STATUS_AVAILABLE if woba is not None else STATUS_MISSING_DATA
    return {
        "status": status,
        "opposingStarterHand": opposing_starter_hand,
        "effectiveBatSide": effective_side,
        "platoonWOBA": woba,
        "platoonPA": pa,
        "usedSeasonFallback": used_fallback,
        "rawSplits": raw_splits,
        "sampleThresholds": {"minPA": MIN_PA_HITTER_SPLIT},
        "otherSplitFields": {
            name: _unavailable(
                "Only wOBA/ISO/SLG/PA/K%/BB% are fetched per platoon split "
                "(scripts/fetch_batter_platoon_splits.py / api/enrich.js?type=batterplatoon); "
                f"{name} is not among them."
            )
            for name in ("xwOBA", "SLGactual", "xSLG", "HRPerPA", "barrelPct", "hardHitPct", "EV",
                         "GBPct", "FBPct", "PullPct", "chasePct", "whiffPct", "contactPct")
        },
        "source": "lib.research.platoon_context.hitter_platoon_value (reused, not duplicated)",
    }


def _statcast_contact(hitter, savant_batters) -> dict:
    """B. Statcast contact quality -- only xwOBA is currently persisted per batter."""
    player_id = hitter.get("playerId")
    xwoba = None
    if savant_batters and player_id is not None:
        raw = savant_batters.get(str(player_id), savant_batters.get(player_id))
        # data/savant_team.json's `batters` map is a flat {playerId: xwOBA float}.
        if isinstance(raw, (int, float)):
            xwoba = raw
        elif isinstance(raw, dict):
            xwoba = raw.get("xwoba") or raw.get("xwOBA")
    fields = {
        "xwOBA": _field(
            STATUS_AVAILABLE if xwoba is not None else STATUS_MISSING_DATA,
            value=xwoba,
            note=None if xwoba is not None else "No xwOBA on file for this playerId in data/savant_team.json.",
        )
    }
    for name, note in _STATCAST_NOT_COMPUTED_FIELDS.items():
        fields[name] = _not_computed(note)
    return {
        "status": STATUS_PARTIAL,
        "fields": fields,
        "source": "data/savant_team.json batters (xwOBA only)",
    }


def _plate_discipline(hitter, savant_batters) -> dict:
    """
    D. Plate discipline. api/savant.js's leaderboard fetch can already
    return kPct/bbPct/whiffPct per batter given playerIds, but only the
    xwOBA half of that response is ever persisted to disk -- so those
    three are NOT_COMPUTED (wiring gap), while every Savant plate-
    discipline-percentile field (Swing%, Zone%, Chase%, Heart/Shadow/
    Chase/Waste, ...) is UNAVAILABLE_FROM_CURRENT_SOURCES (no fetcher
    anywhere requests those leaderboard columns at all).
    """
    fields = {
        "kPct": _not_computed("api/savant.js's batter leaderboard fetch returns kPct given playerIds; not persisted to any data/ file."),
        "bbPct": _not_computed("Same wiring gap as kPct."),
        "whiffPct": _not_computed("Same wiring gap as kPct."),
    }
    for name in _PLATE_DISCIPLINE_UNAVAILABLE_FIELDS:
        fields[name] = _unavailable(
            "No fetcher in this repo requests Savant's plate-discipline-percentile leaderboard columns."
        )
    return {"status": STATUS_PARTIAL, "fields": fields}


def _bat_tracking() -> dict:
    """C. Bat tracking -- confirmed zero references anywhere in this repo."""
    fields = {
        name: _unavailable(
            "No bat-tracking ingestion exists anywhere in this repo (confirmed by repository-wide "
            "audit: zero references to bat speed / swing length / attack angle / squared-up rate)."
        )
        for name in (
            "avgBatSpeed", "maxBatSpeed", "fastSwingPct", "squaredUpRate", "squaredUpPerSwing",
            "blastRate", "swingLength", "attackAngle", "idealAttackAngleRate", "attackDirection",
            "swingPathTilt", "timingEarlyPct", "timingOnTimePct", "timingLatePct",
            "horizontalMissClass", "verticalMissClass",
        )
    }
    return {
        "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "fields": fields,
        "note": "Savant's bat-tracking leaderboard has never been queried by any script in this repo -- new ingestion required (see docs/HITTER_FEATURE_FOUNDATION.md).",
    }


def _starter_context(opp_pitcher, opp_savant) -> dict:
    """L. Starter quality -- reused from api/pitchers.js + api/savant.js's existing pitcherSavant block."""
    if not opp_pitcher and not opp_savant:
        return {"status": STATUS_MISSING_DATA, "note": "No opposing pitcher/pitcherSavant data on this game."}
    pitch_hand = classify_hand(opp_pitcher.get("pitchHand"))
    return {
        "status": STATUS_AVAILABLE,
        "pitcherId": opp_pitcher.get("id"),
        "name": opp_pitcher.get("name"),
        "pitchHand": pitch_hand,
        "xERA": opp_savant.get("xERA"),
        "xFIP": opp_savant.get("xFIP") or opp_savant.get("recentFIP"),
        "kPct": opp_savant.get("kPct"),
        "bbPct": opp_savant.get("bbPct"),
        "fbPct": opp_savant.get("fbPct"),
        "hardHitPct": opp_savant.get("hardHitPct"),
        "barrelPct": opp_savant.get("barrelPct"),
        "exitVeloAvg": opp_savant.get("exitVeloAvg"),
        "vsLHH": opp_savant.get("vsLHH"),
        "vsRHH": opp_savant.get("vsRHH"),
        "firstInningSplit": opp_savant.get("firstInningSplit"),
        "velocityRecent": opp_savant.get("velocityRecent"),
        "velocitySeason": opp_savant.get("velocitySeason"),
        "ttoSplit": opp_savant.get("ttoSplit"),
        "pitchArsenal": _unavailable(
            "No per-pitch-type usage/velocity/spin/movement/release/extension arsenal breakdown is "
            "ingested anywhere -- every existing pitcher fetch returns season-aggregate rate stats "
            "(K%, BB%, xERA, FB%, hard-hit%, barrel%) only, never broken out by pitch type."
        ),
        "source": "g[oppSide]['pitcher'] (api/pitchers.js) + g[oppSide]['pitcherSavant'] (api/savant.js via scripts/fetch_savant_pitchers.py) -- reused, not duplicated",
    }


def _bullpen_context(opp_bullpen) -> dict:
    """M. Bullpen exposure -- reused from api/bullpen.js + scripts/fetch_bullpen_usage.py."""
    if not opp_bullpen:
        return {"status": STATUS_MISSING_DATA, "note": "No opposing bullpen data on this game."}
    return {
        "status": STATUS_AVAILABLE,
        "teamQuality": {
            "era": opp_bullpen.get("era"),
            "xFIP": opp_bullpen.get("xFIP"),
            "whip": opp_bullpen.get("whip"),
            "kPer9": opp_bullpen.get("kPer9"),
            "bbPer9": opp_bullpen.get("bbPer9"),
            "hr9": opp_bullpen.get("hr9"),
            "grade": opp_bullpen.get("grade"),
            "hlXFIP": opp_bullpen.get("hlXFIP"),
            "hlGrade": opp_bullpen.get("hlGrade"),
            "hlAvailable": opp_bullpen.get("hlAvailable"),
        },
        "recentUsage": opp_bullpen.get("recentUsage"),
        "paVsStarterVsBullpenModel": _not_computed(
            "P(PA vs starter) / P(PA vs RHP bullpen) / P(PA vs LHP bullpen) is a Phase 2+ model -- "
            "this block currently exposes raw bullpen quality/workload only."
        ),
        "source": "g[oppSide]['bullpen'] (api/bullpen.js + scripts/fetch_bullpen_usage.py) -- reused, not duplicated",
    }


def _park_context(park) -> dict:
    """P. Park -- reused from api/slate.js's PARK_WEATHER, exposed on g['park']."""
    if not park:
        return {"status": STATUS_MISSING_DATA, "note": "No g['park'] on this game."}
    return {
        "status": STATUS_PARTIAL,
        "name": park.get("name"),
        "dome": park.get("dome"),
        "runFactor": park.get("parkFactor"),
        "hrFactor": _unavailable("Only a single run-scoring index exists (api/slate.js PARK_WEATHER) -- no event-specific (1B/2B/3B/HR) park factor is computed anywhere."),
        "handednessSplitFactor": _unavailable("No LHB/RHB-specific park factor exists anywhere."),
        "wallDistances": _unavailable("No wall-distance/dimension dataset exists anywhere."),
        "wallHeights": _unavailable("Same as wallDistances."),
        "altitude": _unavailable("No explicit altitude field exists anywhere (only implicit via COL's high run index)."),
        "foulTerritory": _unavailable("No foul-territory dataset exists anywhere."),
        "roofStatus": _field(STATUS_PARTIAL, value=park.get("dome"), note="Only a boolean dome flag exists -- no open/closed-roof-today distinction for retractable-roof parks."),
        "source": "g['park'] (api/slate.js PARK_WEATHER) -- reused, not duplicated",
    }


def _weather_context(weather) -> dict:
    """Q. Weather -- caller-supplied lookup only (this module has no I/O of its own)."""
    if not weather:
        return {
            "status": STATUS_MISSING_DATA,
            "note": "No weather record supplied for this game's home team -- pass weather_by_team= to build_hitter_feature_context() (see data/weather.json).",
        }
    if weather.get("dome"):
        return {"status": STATUS_AVAILABLE, "dome": True, "note": "Dome/retractable roof -- weather not a factor."}
    return {
        "status": STATUS_AVAILABLE,
        "temp": weather.get("temp"),
        "feelsLike": weather.get("feelsLike"),
        "windSpeed": weather.get("wind"),
        "windGust": weather.get("windGust"),
        "windDir": weather.get("windDir"),
        "windDeg": weather.get("windDeg"),
        "humidity": weather.get("humidity"),
        "condition": weather.get("condition"),
        "precipChance": weather.get("precipChance"),
        "pressure": _unavailable("data/weather.json's OpenWeatherMap feed does not include barometric pressure."),
        "windRelativeToParkOrientation": _unavailable("No park-orientation/home-plate-azimuth dataset exists to resolve raw wind degrees into blowing-out/blowing-in."),
        "ballFlightAdjustment": _not_computed("A single derived ball-flight adjustment (combining wind/temp/altitude) is a Phase 2+ model -- raw fields only for now."),
        "source": "data/weather.json (api/weather.js) -- caller-supplied, reused not duplicated",
    }


def _uncertainty_flags(blocks: dict) -> list:
    flags = []
    for name, block in blocks.items():
        status = block.get("status")
        if status == STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES:
            flags.append(f"NO_DATA_SOURCE:{name}")
        elif status == STATUS_MISSING_DATA:
            flags.append(f"MISSING_FOR_THIS_HITTER:{name}")
        elif status == STATUS_NOT_COMPUTED:
            flags.append(f"NOT_WIRED:{name}")
        elif status == STATUS_PARTIAL:
            flags.append(f"PARTIAL:{name}")
    return sorted(flags)


def _build_single_hitter_context(
    hitter, offense_side, opp_side, opp_pitcher, opp_savant, opp_bullpen,
    park, weather, opposing_starter_hand, savant_batters, source_meta,
) -> dict:
    order = hitter.get("order")
    bat_side = classify_hand(hitter.get("batSide"))

    player_identity = {
        "status": STATUS_AVAILABLE if hitter.get("playerId") else STATUS_MISSING_DATA,
        "playerId": hitter.get("playerId"),
        "name": hitter.get("name"),
        "batSide": bat_side,
    }
    lineup_context = {
        "status": STATUS_OK,
        "order": order,
        "offenseSide": offense_side,
        "lineupConfirmed": True,
        "topOrderWeighted": isinstance(order, int) and 1 <= order <= 3,
    }
    pa_context = {
        "status": STATUS_NOT_COMPUTED,
        "expectedPA": None,
        "paDistribution": None,
        "note": "PA-count distribution (P(3 PA), P(4 PA), ...) is a Phase 2+ model requiring "
                "batting-order position, starter/bullpen workload, and game-environment inputs "
                "this module already exposes elsewhere in this record -- not yet combined into a distribution.",
    }
    baseline_talent = _baseline_talent(hitter)
    platoon_context = _platoon_block(hitter, opposing_starter_hand)
    statcast_contact = _statcast_contact(hitter, savant_batters)
    plate_discipline = _plate_discipline(hitter, savant_batters)
    bat_tracking = _bat_tracking()
    starter_context = _starter_context(opp_pitcher, opp_savant)
    pitch_type_matchup = {
        "status": STATUS_NOT_COMPUTED,
        "byPitchType": None,
        "note": "Hitter x pitch-type performance (Swing%/Chase%/Contact%/Whiff%/wOBA/xwOBA/ISO/EV/"
                "run-value-per-100 per pitch type) requires per-pitch Statcast ingestion this repo "
                "does not yet have (see statcastContact/plateDiscipline notes).",
    }
    velocity_matchup = {
        "status": STATUS_NOT_COMPUTED,
        "byVelocityBucket": None,
        "buckets": ["<93", "93-95", "95-97", "97-99", "99+"],
        "note": "Hitter response to velocity (bucketed initially, continuous later) requires the "
                "same per-pitch Statcast ingestion as pitchTypeMatchup.",
    }
    pitch_shape_context = {
        "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "note": "Pitch-shape similarity (velocity/IVB/horizontal break/spin/release height/release "
                "side/extension/arm angle) requires per-pitch Statcast rows -- none ingested anywhere.",
    }
    location_context = {
        "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "note": "Pitch-location heat maps require per-pitch Statcast rows -- none ingested anywhere.",
    }
    count_context = {
        "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "note": "Count-state sequencing requires per-pitch Statcast rows -- none ingested anywhere.",
    }
    bullpen_context = _bullpen_context(opp_bullpen)
    park_context = _park_context(park)
    weather_context = _weather_context(weather)
    spray_context = {
        "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "note": "No Pull%/Center%/Oppo%, spray angle, or EV-by-direction data is ingested per batter anywhere.",
    }
    defense_context = {
        "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "note": "No team/positional OAA or defensive-range data is ingested anywhere.",
    }
    catcher_context = {
        "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "note": "No catcher framing/blocking data is ingested anywhere (confirmed absent by repository audit).",
    }
    umpire_context = {
        "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "note": "No umpire zone-size/called-strike-tendency data is ingested anywhere (confirmed absent by repository audit).",
    }
    recent_change_context = {
        "status": STATUS_NOT_COMPUTED,
        "changePoints": None,
        "note": "Change-point detection over bat speed/swing length/attack angle/EV/chase% requires "
                "both timestamped bat-tracking data (not yet ingested) and a detection method not yet built.",
    }

    blocks = {
        "playerIdentity": player_identity,
        "lineupContext": lineup_context,
        "paContext": pa_context,
        "baselineTalent": baseline_talent,
        "platoonContext": platoon_context,
        "statcastContact": statcast_contact,
        "plateDiscipline": plate_discipline,
        "batTracking": bat_tracking,
        "starterContext": starter_context,
        "pitchTypeMatchup": pitch_type_matchup,
        "velocityMatchup": velocity_matchup,
        "pitchShapeContext": pitch_shape_context,
        "locationContext": location_context,
        "countContext": count_context,
        "bullpenContext": bullpen_context,
        "parkContext": park_context,
        "weatherContext": weather_context,
        "sprayContext": spray_context,
        "defenseContext": defense_context,
        "catcherContext": catcher_context,
        "umpireContext": umpire_context,
        "recentChangeContext": recent_change_context,
    }

    sample_sizes = {
        "platoonPA": platoon_context.get("platoonPA"),
        "seasonPA": hitter.get("seasonPA"),
    }
    fallbacks_used = []
    if platoon_context.get("usedSeasonFallback"):
        fallbacks_used.append("platoonContext: shrunk to season wOBA (platoon-split PA below floor)")

    record = dict(blocks)
    record["dataAvailability"] = {name: b.get("status") for name, b in blocks.items()}
    record["dataFreshness"] = dict(source_meta or {})
    record["sampleSizes"] = sample_sizes
    record["fallbacksUsed"] = fallbacks_used
    record["uncertaintyFlags"] = _uncertainty_flags(blocks)
    record["schemaVersion"] = SCHEMA_VERSION
    return record


def build_hitter_feature_context(g, offense_side, weather_by_team: Optional[dict] = None, source_meta: Optional[dict] = None) -> dict:
    """
    Top-level entry point. Pure function of (g, offense_side, ...) --
    offense_side is 'away' or 'home'; the OPPOSING side's starter/
    bullpen are read automatically, matching
    lib.research.platoon_context.build_offense_platoon_context()'s own
    single-offense-side call shape.

    weather_by_team: optional {teamFullName: weatherRecord} lookup (see
    data/weather.json's `parks` list) -- this module never fetches
    weather itself, only consumes what the caller supplies.
    source_meta: optional dict of upstream fetch timestamps
    (e.g. {"savantTeamFetchedAt": ..., "bullpenFetchedAt": ...}) echoed
    into every hitter's dataFreshness block as-is.

    Returns:
      {
        "offenseSide": "away"|"home",
        "gameId": ...,
        "lineupConfirmed": bool,
        "status": STATUS_OK | STATUS_LINEUP_UNCONFIRMED,
        "reason": str,
        "hitters": [<per-hitter canonical feature record>, ...],
      }

    Never raises. Never fabricates hitters/order/identity for an
    unconfirmed lineup -- returns hitters=[] with
    status=STATUS_LINEUP_UNCONFIRMED instead.
    """
    opp_side = "home" if offense_side == "away" else "away"
    off_ts = g.get(f"{offense_side}TeamStats") or {}
    lineup_confirmed = bool(off_ts.get("lineupConfirmedOfficial"))
    confirmed_lineup = off_ts.get("confirmedLineup") or []

    result = {
        "offenseSide": offense_side,
        "gameId": g.get("gameId"),
        "lineupConfirmed": lineup_confirmed,
        "hitters": [],
    }

    if not lineup_confirmed or not confirmed_lineup:
        result["status"] = STATUS_LINEUP_UNCONFIRMED
        result["reason"] = "Lineup not officially confirmed — no per-hitter feature records built (no fabricated batting order)"
        return result

    opp = g.get(opp_side) or {}
    opp_pitcher = opp.get("pitcher") or {}
    opp_savant = opp.get("pitcherSavant") or {}
    opp_bullpen = opp.get("bullpen") or {}
    park = g.get("park") or {}

    home_team_name = (g.get("home") or {}).get("team")
    weather = (weather_by_team or {}).get(home_team_name) if home_team_name else None

    opposing_starter_hand = classify_hand(opp_pitcher.get("pitchHand"))
    savant_batters = (source_meta or {}).get("savantBatters")

    for hitter in confirmed_lineup:
        result["hitters"].append(
            _build_single_hitter_context(
                hitter, offense_side, opp_side, opp_pitcher, opp_savant, opp_bullpen,
                park, weather, opposing_starter_hand, savant_batters, source_meta,
            )
        )

    result["status"] = STATUS_OK
    result["reason"] = f"{len(result['hitters'])} confirmed hitters"
    return result
