#!/usr/bin/env python3
"""
lib/research/hitter_feature_context.py
========================================
Hitter Projection Engine -- Phase 1 canonical feature foundation
(extended in Phase 2 with raw-pitch-derived fields; see
docs/HITTER_STATCAST_FOUNDATION.md).

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
from lib.research.pitch_taxonomy import (
    classify_pitch_family,
    FASTBALL_FAMILIES,
    VELOCITY_BUCKETS,
)
from lib.research.hitter_pitch_derivation import (
    window_bounds,
    derive_baseline_talent_window,
    derive_plate_discipline,
    derive_contact_quality,
    derive_pitch_type_breakdown,
    derive_velocity_breakdown,
    derive_location_summary,
    derive_count_state_breakdown,
    compare_windows,
)

# ── Statuses new to this module (see FIELD STATUS LEGEND above) ────────────
STATUS_AVAILABLE = "AVAILABLE"
STATUS_PARTIAL = "PARTIAL"
STATUS_NOT_COMPUTED = "NOT_COMPUTED"
STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES = "UNAVAILABLE_FROM_CURRENT_SOURCES"

SCHEMA_VERSION = "2.0"


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


# ── B. Statcast contact quality -- fields with no per-batter source at all ──
# (barrelsPerPA/barrelsPerBBE/gbFbLdDistribution/xHR are still not
# derivable even with a Phase 2 raw archive -- exact Barrel% needs
# Savant's own EV/LA matrix definition, which this repo deliberately
# does not approximate; see hitter_pitch_derivation.derive_contact_quality's
# own docstring.)
_STATCAST_STILL_UNAVAILABLE_FIELDS = {
    "barrelsPerPA": "Not fetched anywhere.",
    "barrelsPerBBE": "Not fetched anywhere (also requires a batted-ball-event denominator this repo does not track).",
    "xHR": "No expected-HR-equivalent metric is fetched anywhere.",
}
# Fields only available once a Phase 2 raw pitch archive exists for this
# batter (derive_contact_quality) -- NOT_COMPUTED (not
# UNAVAILABLE_FROM_CURRENT_SOURCES) when no archive exists yet, since the
# ingestion path now exists (scripts/fetch_statcast_pitch_log.py), it
# just hasn't been run for this batter.
_STATCAST_RAW_DERIVED_FIELDS = ("xBA", "xSLG", "xwOBAcon", "avgEV", "maxEV", "ev90",
                                "avgLaunchAngle", "sweetSpotPct", "hardHitPct", "barrelPct")

# ── D. Plate discipline -- Heart/Shadow/Chase/Waste-band frequencies are
# not separately derived from the raw archive yet (derive_plate_discipline
# only computes binary in-zone/out-of-zone, not the finer 4-band split).
_PLATE_DISCIPLINE_STILL_UNAVAILABLE_FIELDS = ["heartFreq", "shadowFreq", "chaseFreq", "wasteFreq"]
_PLATE_DISCIPLINE_RAW_DERIVED_FIELDS = (
    "swingPct", "contactPct", "whiffPct", "zSwingPct", "zContactPct", "oSwingPct",
    "oContactPct", "zonePct", "calledStrikePct", "firstPitchSwingPct", "firstPitchStrikePct",
)

_BASELINE_HORIZON_KEYS = ("career", "previousSeason", "currentSeason", "rolling90d", "rolling60d", "rolling30d")

_BASELINE_STAT_FIELDS = (
    "PA", "AB", "H", "1B", "2B", "3B", "HR", "BB", "IBB", "HBP", "K", "SF",
    "AVG", "OBP", "SLG", "ISO", "BABIP", "wOBA", "wRCPlus", "KPct", "BBPct",
    "HRPerPA", "HRPerFB", "GBPct", "FBPct", "LDPct", "PullPct", "CenterPct", "OppoPct",
)


def _baseline_talent(hitter, raw_pitches=None, as_of_date=None) -> dict:
    """
    A. Hitter baseline talent, across the six required historical
    horizons. When `raw_pitches` (this batter's archived Phase 2 raw
    pitch history, already as-of-safe -- see this module's docstring)
    and `as_of_date` are both supplied, every horizon is computed for
    real from PA-terminal `events` via
    lib.research.hitter_pitch_derivation.derive_baseline_talent_window(),
    each independently (never averaged together -- per this mission's
    spec, shrinkage/weighting across horizons is future modeling work,
    not this foundation). Falls back to Phase 1's behavior (currentSeason
    wOBA-only from the confirmed-lineup record, every other horizon
    UNAVAILABLE_FROM_CURRENT_SOURCES) whenever raw pitch history hasn't
    been archived yet for this batter -- this is what keeps a hitter
    with no Phase 2 archive projecting identically to before Phase 2.
    """
    horizons = {}
    any_real_window = False
    for key in _BASELINE_HORIZON_KEYS:
        if raw_pitches and as_of_date:
            since, until = window_bounds(as_of_date, key)
            window_stats = derive_baseline_talent_window(raw_pitches, since, until)
            if window_stats.get("PA", 0) > 0:
                any_real_window = True
                horizons[key] = {
                    "status": STATUS_AVAILABLE,
                    "stats": {name: window_stats.get(name) for name in _BASELINE_STAT_FIELDS},
                    "sampleSize": window_stats.get("sampleSize"),
                    "datesCovered": window_stats.get("datesCovered"),
                    "source": "lib.research.statcast_pitch_store raw pitch archive (Hitter Projection Engine Phase 2)",
                }
                continue
            horizons[key] = {
                "status": STATUS_MISSING_DATA,
                "stats": {name: None for name in _BASELINE_STAT_FIELDS},
                "sampleSize": 0,
                "source": "lib.research.statcast_pitch_store raw pitch archive (Hitter Projection Engine Phase 2)",
                "note": "Raw pitch archive exists for this batter but has zero plate appearances in this window yet.",
            }
            continue

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
                "note": "Only wOBA (a rate stat) and PA are populated -- no counting-stat (H/HR/BB/K/...) history is ingested per batter anywhere in this repo yet. Populate a Phase 2 raw pitch archive for this batter (scripts/fetch_statcast_pitch_log.py) for a full counting-stat line.",
            }
        else:
            horizons[key] = {
                "status": STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
                "stats": stats,
                "source": None,
                "note": "No career / prior-season / rolling-window per-batter data source is ingested anywhere in this repo -- every existing batter fetch (api/savant.js, api/enrich.js?type=batting, scripts/fetch_lineups.py) returns a single current-season aggregate only. Populate a Phase 2 raw pitch archive for this batter for real windowed stats.",
            }
    return {
        "status": STATUS_AVAILABLE if any_real_window else STATUS_PARTIAL,
        "horizons": horizons,
        "note": "Older-horizon-as-prior shrinkage described in the Phase 1 spec is future modeling work -- "
                "every horizon here is reported independently, never blended.",
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


def _batter_discipline_lookup(battersDiscipline, player_id):
    if not battersDiscipline or player_id is None:
        return {}
    return battersDiscipline.get(str(player_id), battersDiscipline.get(player_id)) or {}


def _statcast_contact(hitter, savant_batters, battersDiscipline=None, raw_pitches=None) -> dict:
    """
    B. Statcast contact quality. xwOBA: data/savant_team.json (Phase 1).
    hardHitPct/barrelPct/exitVeloAvg (as avgEV): now sourced from
    api/enrich.js's battersDiscipline map (Phase 2 wiring fix -- these
    columns were already being fetched, just discarded before Phase 2).
    xBA/xSLG/xwOBAcon/avgEV/maxEV/ev90/avgLaunchAngle/sweetSpotPct: real,
    derived from this batter's Phase 2 raw pitch archive
    (lib.research.hitter_pitch_derivation.derive_contact_quality) when
    one has been ingested (scripts/fetch_statcast_pitch_log.py);
    NOT_COMPUTED (never UNAVAILABLE_FROM_CURRENT_SOURCES, since the
    ingestion path now exists) otherwise.
    """
    player_id = hitter.get("playerId")
    xwoba = None
    if savant_batters and player_id is not None:
        raw = savant_batters.get(str(player_id), savant_batters.get(player_id))
        if isinstance(raw, (int, float)):
            xwoba = raw
        elif isinstance(raw, dict):
            xwoba = raw.get("xwoba") or raw.get("xwOBA")

    disc = _batter_discipline_lookup(battersDiscipline, player_id)
    fields = {
        "xwOBA": _field(
            STATUS_AVAILABLE if xwoba is not None else STATUS_MISSING_DATA,
            value=xwoba,
            note=None if xwoba is not None else "No xwOBA on file for this playerId in data/savant_team.json.",
        ),
        "hardHitPct": _field(
            STATUS_AVAILABLE if disc.get("hardHitPct") is not None else STATUS_NOT_COMPUTED,
            value=disc.get("hardHitPct"),
            note=None if disc.get("hardHitPct") is not None else "api/enrich.js?type=batting battersDiscipline has no entry for this playerId yet.",
            source="api/enrich.js?type=batting battersDiscipline" if disc.get("hardHitPct") is not None else None,
        ),
        "barrelPct": _field(
            STATUS_AVAILABLE if disc.get("barrelPct") is not None else STATUS_NOT_COMPUTED,
            value=disc.get("barrelPct"),
            note=None if disc.get("barrelPct") is not None else "api/enrich.js?type=batting battersDiscipline has no entry for this playerId yet.",
        ),
    }

    contact_quality = derive_contact_quality(raw_pitches) if raw_pitches else {"sampleSize": 0}
    for name in _STATCAST_RAW_DERIVED_FIELDS:
        raw_key = {"avgLaunchAngle": "avgLaunchAngle"}.get(name, name)
        value = contact_quality.get(raw_key)
        if value is not None:
            fields[name] = _field(STATUS_AVAILABLE, value=value,
                                   sampleSize=contact_quality.get("sampleSize"),
                                   source="lib.research.hitter_pitch_derivation.derive_contact_quality (Phase 2 raw pitch archive)")
        elif name not in fields:
            fields[name] = _not_computed(
                f"No Phase 2 raw pitch archive ingested for this batter yet (scripts/fetch_statcast_pitch_log.py) "
                f"-- run ingestion to compute {name} from real batted-ball data."
            )
    for name, note in _STATCAST_STILL_UNAVAILABLE_FIELDS.items():
        fields[name] = _unavailable(note)

    any_real = xwoba is not None or bool(disc) or contact_quality.get("sampleSize", 0) > 0
    return {
        "status": STATUS_AVAILABLE if (contact_quality.get("sampleSize", 0) > 0 and xwoba is not None) else (STATUS_PARTIAL if any_real else STATUS_MISSING_DATA),
        "fields": fields,
        "source": "data/savant_team.json batters + battersDiscipline + Phase 2 raw pitch archive",
    }


def _plate_discipline(hitter, savant_batters, battersDiscipline=None, raw_pitches=None) -> dict:
    """
    D. Plate discipline. kPct/bbPct/whiffPct: api/enrich.js's
    battersDiscipline map (Phase 2 wiring fix). Swing%/Contact%/Zone%/
    Chase% and friends: real, derived from this batter's Phase 2 raw
    pitch archive (derive_plate_discipline) when one exists; NOT_COMPUTED
    otherwise. Heart/Shadow/Chase/Waste-band frequencies remain
    UNAVAILABLE_FROM_CURRENT_SOURCES -- derive_plate_discipline only
    computes a binary in-zone/out-of-zone split today, not the finer
    4-band Savant grouping (see pitch_taxonomy.classify_zone, which DOES
    support it -- a future pass can wire this the same way).
    """
    player_id = hitter.get("playerId")
    disc = _batter_discipline_lookup(battersDiscipline, player_id)
    fields = {}
    for name in ("kPct", "bbPct", "whiffPct"):
        value = disc.get(name)
        fields[name] = _field(
            STATUS_AVAILABLE if value is not None else STATUS_NOT_COMPUTED,
            value=value,
            note=None if value is not None else "api/enrich.js?type=batting battersDiscipline has no entry for this playerId yet.",
        )

    raw_discipline = derive_plate_discipline(raw_pitches) if raw_pitches else {"sampleSize": 0}
    for name in _PLATE_DISCIPLINE_RAW_DERIVED_FIELDS:
        if name == "whiffPct" and fields.get("whiffPct", {}).get("status") == STATUS_AVAILABLE:
            continue  # season-leaderboard whiffPct already populated above
        value = raw_discipline.get(name)
        if value is not None:
            fields[name] = _field(STATUS_AVAILABLE, value=value,
                                   sampleSize=raw_discipline.get("sampleSize"),
                                   source="lib.research.hitter_pitch_derivation.derive_plate_discipline (Phase 2 raw pitch archive)")
        elif name not in fields:
            fields[name] = _not_computed(
                "No Phase 2 raw pitch archive ingested for this batter yet (scripts/fetch_statcast_pitch_log.py)."
            )
    for name in _PLATE_DISCIPLINE_STILL_UNAVAILABLE_FIELDS:
        fields[name] = _unavailable(
            "derive_plate_discipline computes in-zone/out-of-zone only today, not the finer "
            "Heart/Shadow/Chase/Waste 4-band split -- future wiring, not this Phase 2 milestone."
        )

    any_real = bool(disc) or raw_discipline.get("sampleSize", 0) > 0
    return {
        "status": STATUS_AVAILABLE if raw_discipline.get("sampleSize", 0) > 0 else (STATUS_PARTIAL if any_real else STATUS_MISSING_DATA),
        "fields": fields,
    }


_BAT_TRACKING_INGESTED_FIELDS = (
    "avgBatSpeed", "maxBatSpeed", "fastSwingPct", "squaredUpRate", "squaredUpPerSwing",
    "blastRate", "swingLength", "attackAngle", "idealAttackAngleRate", "attackDirection", "swingTilt",
)
# Never attempted -- api/savantbattracking.js's leaderboard has no known
# column for pitch-level swing timing/miss classification (that requires
# per-swing bat-tracking event data, not a season leaderboard).
_BAT_TRACKING_UNATTEMPTED_FIELDS = ("timingEarlyPct", "timingOnTimePct", "timingLatePct",
                                    "horizontalMissClass", "verticalMissClass")


def _bat_tracking(bat_tracking_data=None) -> dict:
    """
    C. Bat tracking. api/savantbattracking.js (Phase 2) attempts
    Savant's bat-tracking leaderboard CSV export -- see that file's
    docstring for the column-name-verification caveat this block
    inherits. `bat_tracking_data` (from scripts/fetch_savant_bat_tracking.py
    via lib.research.bat_tracking_store) is {"latest": snapshot|None,
    "history": [snapshot, ...]}. A field is AVAILABLE only if the live
    fetch actually resolved a non-null value for it; every other field
    stays UNAVAILABLE_FROM_CURRENT_SOURCES honestly rather than assuming
    the attempted fetch succeeded.
    """
    latest = (bat_tracking_data or {}).get("latest") or {}
    history = (bat_tracking_data or {}).get("history") or []
    fields = {}
    any_resolved = False
    for name in _BAT_TRACKING_INGESTED_FIELDS:
        value = latest.get(name)
        if value is not None:
            any_resolved = True
            fields[name] = _field(STATUS_AVAILABLE, value=value, asOfDate=latest.get("asOfDate"),
                                   source="api/savantbattracking.js Savant bat-tracking leaderboard")
        else:
            fields[name] = _unavailable(
                "Attempted via api/savantbattracking.js (Savant's bat-tracking leaderboard CSV export) "
                "but this field did not resolve for this batter -- either no live snapshot has been "
                "fetched yet, or the live column name differs from the candidates coded there "
                "(this environment could not verify against a live Savant response; see that file's docstring)."
            )
    for name in _BAT_TRACKING_UNATTEMPTED_FIELDS:
        fields[name] = _unavailable(
            "Requires per-swing bat-tracking event data, not a season leaderboard -- not attempted."
        )
    return {
        "status": STATUS_AVAILABLE if any_resolved else STATUS_UNAVAILABLE_FROM_CURRENT_SOURCES,
        "fields": fields,
        "snapshotCount": len(history),
        "note": ("Live bat-tracking data resolved for this batter." if any_resolved else
                 "Savant's bat-tracking leaderboard was attempted (api/savantbattracking.js, the same "
                 "CSV-export mechanism every other Savant fetcher in this repo uses) but could not be "
                 "verified against a live response in this environment -- see that file's docstring for "
                 "exactly what was attempted."),
    }


def _pitch_type_matchup(raw_pitches) -> dict:
    """F. Pitch-type performance -- real, derived per pitch family from this batter's raw pitch archive."""
    if not raw_pitches:
        return {
            "status": STATUS_NOT_COMPUTED,
            "byPitchType": None,
            "note": "No Phase 2 raw pitch archive ingested for this batter yet (scripts/fetch_statcast_pitch_log.py).",
        }
    breakdown = derive_pitch_type_breakdown(raw_pitches)
    return {
        "status": STATUS_AVAILABLE if breakdown else STATUS_MISSING_DATA,
        "byPitchType": breakdown,
        "source": "lib.research.hitter_pitch_derivation.derive_pitch_type_breakdown (Phase 2 raw pitch archive)",
    }


def _velocity_matchup(raw_pitches) -> dict:
    """
    H. Velocity matchup -- bucketed WITHIN each fastball family only
    (see pitch_taxonomy.velocity_bucket's docstring); breaking/offspeed
    pitches are represented in pitchTypeMatchup instead, never forced
    into this fastball-only bucket scale.
    """
    if not raw_pitches:
        return {
            "status": STATUS_NOT_COMPUTED,
            "byVelocityBucket": None,
            "buckets": list(VELOCITY_BUCKETS),
            "note": "No Phase 2 raw pitch archive ingested for this batter yet (scripts/fetch_statcast_pitch_log.py).",
        }
    breakdown = derive_velocity_breakdown(raw_pitches)
    return {
        "status": STATUS_AVAILABLE if breakdown else STATUS_MISSING_DATA,
        "byVelocityBucket": breakdown,
        "buckets": list(VELOCITY_BUCKETS),
        "fastballFamiliesOnly": sorted(FASTBALL_FAMILIES),
        "source": "lib.research.hitter_pitch_derivation.derive_velocity_breakdown (Phase 2 raw pitch archive)",
    }


def _pitch_shape_context(raw_pitches) -> dict:
    """
    I. Pitch-shape support -- a stable per-pitch-type shape SUMMARY
    (average velocity/IVB/horizontal break/spin/release point/extension/
    arm angle across every pitch of that type this batter has faced),
    NOT clustering or nearest-neighbor similarity -- that is explicitly
    future modeling work this record's schema is designed to feed, not
    something this Phase 2 milestone computes.
    """
    if not raw_pitches:
        return {
            "status": STATUS_NOT_COMPUTED,
            "byPitchType": None,
            "note": "No Phase 2 raw pitch archive ingested for this batter yet (scripts/fetch_statcast_pitch_log.py).",
        }
    by_family = {}
    for p in raw_pitches:
        family = classify_pitch_family(p.get("pitchType"), p.get("pitchName"))
        by_family.setdefault(family, []).append(p)

    def _avg(values):
        vals = [v for v in values if v is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    summary = {}
    for family, pitches in by_family.items():
        summary[family] = {
            "sampleSize": len(pitches),
            "avgReleaseSpeed": _avg(p.get("releaseSpeed") for p in pitches),
            "avgInducedVertBreak": _avg(p.get("inducedVertBreak") for p in pitches),
            "avgHorizontalBreak": _avg(p.get("horizontalBreak") for p in pitches),
            "avgSpinRate": _avg(p.get("spinRate") for p in pitches),
            "avgReleaseHeight": _avg(p.get("releaseHeight") for p in pitches),
            "avgReleaseSide": _avg(p.get("releaseSide") for p in pitches),
            "avgExtension": _avg(p.get("extension") for p in pitches),
            "avgArmAngle": _avg(p.get("armAngle") for p in pitches),
        }
    return {
        "status": STATUS_AVAILABLE,
        "byPitchType": summary,
        "note": "Per-pitch-type shape summary only -- similarity/clustering/nearest-neighbor modeling is future work.",
        "source": "lib.research.pitch_taxonomy (Phase 2 raw pitch archive)",
    }


def _location_context(raw_pitches) -> dict:
    """J. Location/heat-map support -- zone-frequency summary; continuous plateX/plateZ stay on each archived pitch, never discarded."""
    if not raw_pitches:
        return {
            "status": STATUS_NOT_COMPUTED,
            "zoneFrequency": None,
            "note": "No Phase 2 raw pitch archive ingested for this batter yet (scripts/fetch_statcast_pitch_log.py).",
        }
    summary = derive_location_summary(raw_pitches)
    return {
        "status": STATUS_AVAILABLE if summary.get("sampleSize", 0) > 0 else STATUS_MISSING_DATA,
        "zoneFrequency": summary.get("zoneFrequency"),
        "sampleSize": summary.get("sampleSize", 0),
        "note": "Heart/Shadow/Chase/Waste frequency only -- finer spatial-grid binning is available via "
                "lib.research.pitch_taxonomy.spatial_grid_bin() on each archived pitch's plateX/plateZ "
                "directly for a future heat-map model; not pre-binned here.",
        "source": "lib.research.hitter_pitch_derivation.derive_location_summary (Phase 2 raw pitch archive)",
    }


def _count_state_context(raw_pitches) -> dict:
    """K. Count-state support -- discipline outcomes grouped by count-state bucket."""
    if not raw_pitches:
        return {
            "status": STATUS_NOT_COMPUTED,
            "byCountState": None,
            "note": "No Phase 2 raw pitch archive ingested for this batter yet (scripts/fetch_statcast_pitch_log.py).",
        }
    breakdown = derive_count_state_breakdown(raw_pitches)
    return {
        "status": STATUS_AVAILABLE,
        "byCountState": breakdown,
        "note": "Per-count-state discipline outcomes only -- pitch-sequence simulation is future modeling work.",
        "source": "lib.research.hitter_pitch_derivation.derive_count_state_breakdown (Phase 2 raw pitch archive)",
    }


_RECENT_CHANGE_DISCIPLINE_FIELDS = ("whiffPct", "chasePct", "swingPct", "zContactPct")
_RECENT_CHANGE_CONTACT_FIELDS = ("avgEV", "ev90", "hardHitPct", "avgLaunchAngle")
_RECENT_CHANGE_BAT_TRACKING_FIELDS = ("avgBatSpeed", "swingLength", "squaredUpRate", "attackAngle")


def _recent_change_context(raw_pitches, as_of_date, bat_tracking_data=None) -> dict:
    """
    U. Recent-change support -- compares rolling30d vs. currentSeason
    plate-discipline/contact-quality (real deltas, from the same raw
    archive every other Phase 2 block reads) and, when bat-tracking
    history has more than one snapshot, the most recent snapshot vs. the
    earliest archived one. This is comparison only, per this mission's
    spec -- NOT change-point detection (no significance test, no
    breakpoint estimate); a future model decides what a given delta means.
    """
    comparisons = {}
    if raw_pitches and as_of_date:
        recent_since, recent_until = window_bounds(as_of_date, "rolling30d")
        season_since, _season_until = window_bounds(as_of_date, "currentSeason")
        # Baseline is the season-to-date EXCLUDING the recent window
        # itself (season start up to where "recent" begins) -- a
        # baseline that still contained the recent pitches would dilute
        # any real recent-vs-longer-term difference, defeating the
        # point of the comparison.
        baseline_since, baseline_until = season_since, recent_since
        recent_pitches = [p for p in raw_pitches if p.get("gameDate") and recent_since <= p["gameDate"] < recent_until]
        baseline_pitches = [p for p in raw_pitches if p.get("gameDate") and baseline_since <= p["gameDate"] < baseline_until]
        if recent_pitches and baseline_pitches:
            comparisons["plateDiscipline"] = compare_windows(
                derive_plate_discipline(recent_pitches), derive_plate_discipline(baseline_pitches),
                _RECENT_CHANGE_DISCIPLINE_FIELDS,
            )
            comparisons["contactQuality"] = compare_windows(
                derive_contact_quality(recent_pitches), derive_contact_quality(baseline_pitches),
                _RECENT_CHANGE_CONTACT_FIELDS,
            )

    history = (bat_tracking_data or {}).get("history") or []
    if len(history) >= 2:
        comparisons["batTracking"] = compare_windows(history[-1], history[0], _RECENT_CHANGE_BAT_TRACKING_FIELDS)

    if comparisons:
        return {
            "status": STATUS_AVAILABLE,
            "comparisons": comparisons,
            "note": "Raw recent-vs-baseline deltas only -- NOT change-point detection (no significance test).",
            "source": "lib.research.hitter_pitch_derivation.compare_windows (Phase 2 raw pitch archive / bat-tracking history)",
        }
    return {
        "status": STATUS_NOT_COMPUTED,
        "comparisons": None,
        "note": "Requires either enough raw-pitch-archive history to compare rolling30d vs. currentSeason, "
                "or 2+ archived bat-tracking snapshots -- neither available yet for this batter.",
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
    source_meta = source_meta or {}
    as_of_date = source_meta.get("asOfDate")
    player_id = hitter.get("playerId")
    raw_pitches = None
    raw_pitches_by_batter = source_meta.get("rawPitchesByBatter")
    if raw_pitches_by_batter and player_id is not None:
        raw_pitches = raw_pitches_by_batter.get(str(player_id), raw_pitches_by_batter.get(player_id))
    battersDiscipline = source_meta.get("savantBattersDiscipline")
    bat_tracking_data = None
    bat_tracking_by_batter = source_meta.get("batTrackingByBatter")
    if bat_tracking_by_batter and player_id is not None:
        bat_tracking_data = bat_tracking_by_batter.get(str(player_id), bat_tracking_by_batter.get(player_id))

    baseline_talent = _baseline_talent(hitter, raw_pitches=raw_pitches, as_of_date=as_of_date)
    platoon_context = _platoon_block(hitter, opposing_starter_hand)
    statcast_contact = _statcast_contact(hitter, savant_batters, battersDiscipline=battersDiscipline, raw_pitches=raw_pitches)
    plate_discipline = _plate_discipline(hitter, savant_batters, battersDiscipline=battersDiscipline, raw_pitches=raw_pitches)
    bat_tracking = _bat_tracking(bat_tracking_data)
    starter_context = _starter_context(opp_pitcher, opp_savant)
    pitch_type_matchup = _pitch_type_matchup(raw_pitches)
    velocity_matchup = _velocity_matchup(raw_pitches)
    pitch_shape_context = _pitch_shape_context(raw_pitches)
    location_context = _location_context(raw_pitches)
    count_context = _count_state_context(raw_pitches)
    bullpen_context = _bullpen_context(opp_bullpen)
    park_context = _park_context(park)
    weather_context = _weather_context(weather)
    spray_context = {
        "status": STATUS_AVAILABLE if raw_pitches else STATUS_NOT_COMPUTED,
        "sprayDistribution": derive_contact_quality(raw_pitches).get("sprayDistribution") if raw_pitches else None,
        "note": ("Pull/Center/Oppo derived from this batter's Phase 2 raw pitch archive." if raw_pitches else
                 "No Phase 2 raw pitch archive ingested for this batter yet (scripts/fetch_statcast_pitch_log.py)."),
        "source": "lib.research.hitter_pitch_derivation.derive_contact_quality (Phase 2 raw pitch archive)" if raw_pitches else None,
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
    recent_change_context = _recent_change_context(raw_pitches, as_of_date, bat_tracking_data)

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
        "rawPitchArchiveCount": len(raw_pitches) if raw_pitches else 0,
        "batTrackingSnapshotCount": bat_tracking.get("snapshotCount", 0),
    }
    fallbacks_used = []
    if platoon_context.get("usedSeasonFallback"):
        fallbacks_used.append("platoonContext: shrunk to season wOBA (platoon-split PA below floor)")
    if not raw_pitches:
        fallbacks_used.append("No Phase 2 raw pitch archive for this batter -- pitch-type/velocity/shape/location/count/xBA-xSLG-xwOBAcon blocks fall back to NOT_COMPUTED")

    # dataFreshness echoes upstream fetch timestamps only -- the bulky
    # per-batter lookups (rawPitchesByBatter/savantBattersDiscipline/
    # batTrackingByBatter) are inputs to this function, not freshness
    # metadata, and are deliberately excluded so this record doesn't
    # balloon with the same lookup table repeated on every hitter.
    _FRESHNESS_KEYS = ("savantTeamFetchedAt", "weatherUpdatedAt", "asOfDate", "batTrackingFetchedAt")
    record = dict(blocks)
    record["dataAvailability"] = {name: b.get("status") for name, b in blocks.items()}
    record["dataFreshness"] = {k: source_meta[k] for k in _FRESHNESS_KEYS if k in source_meta}
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
    source_meta: optional dict, all keys optional:
      - "savantTeamFetchedAt"/"weatherUpdatedAt"/"batTrackingFetchedAt":
        upstream fetch timestamps, echoed into every hitter's
        dataFreshness block as-is.
      - "asOfDate": the slate date ('YYYY-MM-DD') this record is being
        built for -- REQUIRED for any raw-pitch-archive-derived block
        (baselineTalent horizons, statcastContact/plateDiscipline's
        derived fields, pitchTypeMatchup, velocityMatchup,
        pitchShapeContext, locationContext, countContext, sprayContext,
        recentChangeContext) to activate; without it those blocks fall
        back to Phase 1 behavior even if raw pitches are supplied,
        since window boundaries can't be computed without a reference date.
      - "savantBatters": {playerId: xwOBA} (Phase 1, unchanged).
      - "savantBattersDiscipline": {playerId: {kPct, bbPct, whiffPct,
        hardHitPct, barrelPct, exitVeloAvg}} (Phase 2 -- api/enrich.js's
        battersDiscipline map, previously-fetched-but-discarded columns).
      - "rawPitchesByBatter": {playerId: [pitch, ...]} -- this batter's
        archived Phase 2 raw pitches, already as-of-filtered by the
        CALLER (see lib.research.statcast_pitch_store.
        load_pitches_for_batter(batter_id, as_of=asOfDate)) BEFORE being
        passed in here -- this module does no date filtering of its own
        on this list beyond what lib.research.hitter_pitch_derivation's
        window helpers additionally enforce, so a caller that forgets
        the as_of cutoff when loading is not caught by this function.
      - "batTrackingByBatter": {playerId: {"latest": snapshot|None,
        "history": [snapshot, ...]}} (Phase 2).

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
