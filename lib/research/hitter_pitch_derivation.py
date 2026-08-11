#!/usr/bin/env python3
"""
lib/research/hitter_pitch_derivation.py
==========================================
Hitter Projection Engine -- Phase 2 derived hitter feature tables.

Pure functions operating on a list of already-loaded, already-as-of-
filtered raw pitch dicts (see lib.research.statcast_pitch_store.
load_pitches_for_batter -- callers MUST pass an `as_of` cutoff there;
this module does no date filtering of its own beyond the window-bounds
helper below, so it trusts its caller for leakage safety). No I/O, no
network, no clock reads.

CANONICAL RAW PITCH RECORD SCHEMA (produced by api/savantpitches.js,
archived by lib.research.statcast_pitch_store)
------------------------------------------------------------------------
Identity:    gamePk, gameDate ('YYYY-MM-DD'), batterId, pitcherId,
             batterHand ('L'/'R'/None), pitcherHand, inning, atBatIndex,
             pitchNumber
Count/state: balls, strikes, outsWhenUp, onFirst, onSecond, onThird
Pitch type:  pitchType (Statcast code, e.g. 'FF'), pitchName
Velocity/shape: releaseSpeed, spinRate, inducedVertBreak,
             horizontalBreak, releaseHeight, releaseSide, extension,
             armAngle
Location:    plateX, plateZ, szTop, szBot
Result:      pitchCallType ('ball'/'called_strike'/'swinging_strike'/
             'foul'/'in_play'/'hit_by_pitch'/'other'), description (raw,
             kept for debugging only), events (terminal PA outcome,
             non-None only on the pitch that ends a PA)
Contact (only when pitchCallType == 'in_play'): launchSpeed,
             launchAngle, hitCoordX, hitCoordY, battedBallType,
             estimatedBA, estimatedWOBA, wobaValue

Every field is None when the authoritative source didn't expose it for
that pitch -- this module never fabricates a value to fill a gap; a
metric with an empty denominator returns None with sampleSize=0 rather
than 0.0 or a crash.
"""

from typing import Optional
from datetime import datetime, timedelta

from lib.research.pitch_taxonomy import (
    classify_pitch_family,
    velocity_bucket,
    classify_zone,
    classify_count_state,
    FASTBALL_FAMILIES,
)

# Same wOBA linear-weight formula api/enrich.js's type=batting handler
# already uses for team wOBA -- reused verbatim here so a per-batter
# wOBA computed from raw pitch events is on the same scale as every
# other wOBA value already flowing through this repo, not a second,
# subtly-different formula.
WOBA_WEIGHTS = {"BB": 0.69, "1B": 0.89, "2B": 1.27, "3B": 1.62, "HR": 2.10}

HARD_HIT_EV_THRESHOLD = 95.0
SWEET_SPOT_LA_MIN = 8.0
SWEET_SPOT_LA_MAX = 32.0

# events -> (statBucket, countsAsPA, countsAsAB). Only well-documented
# Statcast `events` values are mapped; anything unrecognized is excluded
# from PA/AB entirely rather than guessed into a bucket (see
# derive_baseline_talent_window()'s `unrecognizedEvents` field).
_EVENT_MAP = {
    "single": ("1B", True, True), "double": ("2B", True, True),
    "triple": ("3B", True, True), "home_run": ("HR", True, True),
    "walk": ("BB", True, False), "intent_walk": ("IBB", True, False),
    "hit_by_pitch": ("HBP", True, False),
    "strikeout": ("K", True, True), "strikeout_double_play": ("K", True, True),
    "field_out": ("OUT", True, True), "force_out": ("OUT", True, True),
    "grounded_into_double_play": ("OUT", True, True),
    "double_play": ("OUT", True, True), "triple_play": ("OUT", True, True),
    "fielders_choice": ("OUT", True, True), "fielders_choice_out": ("OUT", True, True),
    "field_error": ("OUT_REACHED_ON_ERROR", True, True),
    "sac_fly": ("SF", True, False), "sac_fly_double_play": ("SF", True, False),
    "sac_bunt": ("SH", True, False), "sac_bunt_double_play": ("SH", True, False),
    "catcher_interf": (None, False, False),
}


def _percentile(values, pct):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * (pct / 100.0)
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return round(s[f] + (s[c] - s[f]) * (k - f), 3)


def window_bounds(as_of_date: str, window: str):
    """
    Return (since, until) ISO date strings for a named horizon,
    `until` always == as_of_date (exclusive, matching
    statcast_pitch_store.load_pitches_for_batter's as_of contract).
    'career'/'previousSeason'/'currentSeason' are season-boundary
    windows (Jan 1 of the relevant year); 'rolling90d'/'60d'/'30d' are
    pure calendar-day lookbacks. Raises ValueError on an unknown window
    name rather than silently returning an unbounded/wrong window.
    """
    as_of = datetime.strptime(as_of_date, "%Y-%m-%d")
    if window == "career":
        return None, as_of_date
    if window == "currentSeason":
        return f"{as_of.year}-01-01", as_of_date
    if window == "previousSeason":
        return f"{as_of.year - 1}-01-01", f"{as_of.year}-01-01"
    if window == "rolling90d":
        return (as_of - timedelta(days=90)).strftime("%Y-%m-%d"), as_of_date
    if window == "rolling60d":
        return (as_of - timedelta(days=60)).strftime("%Y-%m-%d"), as_of_date
    if window == "rolling30d":
        return (as_of - timedelta(days=30)).strftime("%Y-%m-%d"), as_of_date
    raise ValueError(f"unknown window {window!r}")


def _filter_window(pitches, since, until):
    out = []
    for p in pitches:
        d = p.get("gameDate")
        if d is None:
            continue
        if since is not None and d < since:
            continue
        if until is not None and not (d < until):
            continue
        out.append(p)
    return out


def derive_contact_quality(pitches) -> dict:
    """
    Contact-quality summary from every batted ball (pitchCallType ==
    'in_play') in `pitches`. Only computes metrics with an unambiguous,
    well-known definition (hardHit = EV>=95, sweetSpot = LA in
    [8,32]) -- deliberately does NOT compute an approximate Barrel%
    here (Statcast's real barrel definition is a two-dimensional
    EV/LA matrix, not a simple threshold; an approximation risks
    silently disagreeing with the season-leaderboard Barrel% already
    wired in Phase 2's statcastContact block, so this module leaves
    barrel to that authoritative source instead of guessing).
    """
    balls_in_play = [p for p in pitches if p.get("pitchCallType") == "in_play"]
    n = len(balls_in_play)
    if n == 0:
        return {"sampleSize": 0}

    evs = [p["launchSpeed"] for p in balls_in_play if p.get("launchSpeed") is not None]
    las = [p["launchAngle"] for p in balls_in_play if p.get("launchAngle") is not None]
    xbas = [p["estimatedBA"] for p in balls_in_play if p.get("estimatedBA") is not None]
    xwobacons = [p["estimatedWOBA"] for p in balls_in_play if p.get("estimatedWOBA") is not None]

    bb_types = [p.get("battedBallType") for p in balls_in_play if p.get("battedBallType")]
    bb_type_n = len(bb_types)
    bb_type_dist = None
    if bb_type_n:
        bb_type_dist = {
            "groundBallPct": round(100.0 * bb_types.count("ground_ball") / bb_type_n, 1),
            "flyBallPct": round(100.0 * bb_types.count("fly_ball") / bb_type_n, 1),
            "lineDrivePct": round(100.0 * bb_types.count("line_drive") / bb_type_n, 1),
            "popupPct": round(100.0 * bb_types.count("popup") / bb_type_n, 1),
        }

    spray = _derive_spray_distribution(balls_in_play)

    return {
        "sampleSize": n,
        "avgEV": round(sum(evs) / len(evs), 2) if evs else None,
        "maxEV": max(evs) if evs else None,
        "ev90": _percentile(evs, 90) if evs else None,
        "avgLaunchAngle": round(sum(las) / len(las), 2) if las else None,
        "hardHitPct": round(100.0 * sum(1 for e in evs if e >= HARD_HIT_EV_THRESHOLD) / len(evs), 1) if evs else None,
        "sweetSpotPct": round(100.0 * sum(1 for a in las if SWEET_SPOT_LA_MIN <= a <= SWEET_SPOT_LA_MAX) / len(las), 1) if las else None,
        "battedBallTypeDistribution": bb_type_dist,
        "xBA": round(sum(xbas) / len(xbas), 3) if xbas else None,
        "xwOBAcon": round(sum(xwobacons) / len(xwobacons), 3) if xwobacons else None,
        "sprayDistribution": spray,
    }


def _derive_spray_distribution(balls_in_play):
    """
    Pull/Center/Oppo from hitCoordX/hitCoordY using the standard
    Statcast-derived spray-angle formula (home plate at (125.42, 198.27)
    in Savant's hit-coordinate system), adjusted for batter handedness
    -- a LHB's "pull" side mirrors a RHB's. Balls missing hitCoordX/Y or
    batterHand are excluded from the denominator rather than guessed.
    """
    import math
    pulls = centers = oppos = 0
    total = 0
    for p in balls_in_play:
        hx, hy = p.get("hitCoordX"), p.get("hitCoordY")
        hand = p.get("batterHand")
        if hx is None or hy is None or hand not in ("L", "R"):
            continue
        angle = math.degrees(math.atan2((hx - 125.42), (198.27 - hy)))
        # angle > 0 is toward 1B/RF side, < 0 toward 3B/LF side (Savant's
        # own x-axis convention) -- a RHB pulling the ball goes toward
        # the 3B/LF side (negative angle); mirror for a LHB.
        pull_sign = -1 if hand == "R" else 1
        signed = angle * pull_sign
        total += 1
        if signed < -15:
            pulls += 1
        elif signed > 15:
            oppos += 1
        else:
            centers += 1
    if total == 0:
        return None
    return {
        "pullPct": round(100.0 * pulls / total, 1),
        "centerPct": round(100.0 * centers / total, 1),
        "oppoPct": round(100.0 * oppos / total, 1),
    }


def derive_plate_discipline(pitches) -> dict:
    """
    Swing/contact/whiff/zone/chase rates from every pitch in `pitches`
    (no batted-ball filter -- every pitch has a pitchCallType). Zone
    membership uses each pitch's own szTop/szBot when present, else the
    league-average zone (see lib.research.pitch_taxonomy.classify_zone).
    """
    n = len(pitches)
    if n == 0:
        return {"sampleSize": 0}

    def in_zone(p):
        x, z = p.get("plateX"), p.get("plateZ")
        if x is None or z is None:
            return None
        top = p.get("szTop") if p.get("szTop") is not None else 3.5
        bot = p.get("szBot") if p.get("szBot") is not None else 1.5
        return (-0.83 <= x <= 0.83) and (bot <= z <= top)

    swings = [p for p in pitches if p.get("pitchCallType") in ("swinging_strike", "foul", "in_play")]
    whiffs = [p for p in pitches if p.get("pitchCallType") == "swinging_strike"]
    contacts = [p for p in pitches if p.get("pitchCallType") in ("foul", "in_play")]
    called_strikes = [p for p in pitches if p.get("pitchCallType") == "called_strike"]

    zone_known = [p for p in pitches if in_zone(p) is not None]
    in_zone_pitches = [p for p in zone_known if in_zone(p)]
    out_zone_pitches = [p for p in zone_known if not in_zone(p)]
    z_swings = [p for p in in_zone_pitches if p in swings]
    o_swings = [p for p in out_zone_pitches if p in swings]
    z_contacts = [p for p in z_swings if p in contacts]
    o_contacts = [p for p in o_swings if p in contacts]

    first_pitches = [p for p in pitches if p.get("balls") == 0 and p.get("strikes") == 0]
    first_pitch_swings = [p for p in first_pitches if p in swings]
    first_pitch_strikes = [p for p in first_pitches if p.get("pitchCallType") in ("called_strike", "swinging_strike", "foul", "in_play")]

    def pct(numer, denom):
        return round(100.0 * len(numer) / len(denom), 1) if denom else None

    return {
        "sampleSize": n,
        "swingPct": pct(swings, pitches),
        "contactPct": pct(contacts, swings),
        "whiffPct": pct(whiffs, swings),
        "zonePct": pct(in_zone_pitches, zone_known) if zone_known else None,
        "zSwingPct": pct(z_swings, in_zone_pitches) if in_zone_pitches else None,
        "zContactPct": pct(z_contacts, z_swings) if z_swings else None,
        "oSwingPct": pct(o_swings, out_zone_pitches) if out_zone_pitches else None,
        "chasePct": pct(o_swings, out_zone_pitches) if out_zone_pitches else None,
        "oContactPct": pct(o_contacts, o_swings) if o_swings else None,
        "calledStrikePct": pct(called_strikes, pitches),
        "firstPitchSwingPct": pct(first_pitch_swings, first_pitches) if first_pitches else None,
        "firstPitchStrikePct": pct(first_pitch_strikes, first_pitches) if first_pitches else None,
    }


def derive_baseline_talent_window(pitches, since: Optional[str], until: Optional[str]) -> dict:
    """
    Counting-stat baseline talent line derived from raw pitch `events`
    within [since, until) -- fills PR #78's baselineTalent horizons
    (which had only a single season wOBA scalar) with real PA/AB/H/BB/K/...
    counts wherever raw pitch history has been archived for this batter.
    Returns status=MISSING_DATA (PA=0) rather than fabricating a line
    when no events fall in the window.
    """
    windowed = _filter_window(pitches, since, until) if (since is not None or until is not None) else pitches
    terminal = [p for p in windowed if p.get("events")]

    counts = {"1B": 0, "2B": 0, "3B": 0, "HR": 0, "BB": 0, "IBB": 0, "HBP": 0, "K": 0, "SF": 0, "SH": 0}
    pa = ab = 0
    unrecognized = []
    dates = []
    for p in terminal:
        event = p.get("events")
        mapping = _EVENT_MAP.get(event)
        if mapping is None:
            if event not in _EVENT_MAP:
                unrecognized.append(event)
            continue
        bucket, counts_pa, counts_ab = mapping
        if counts_pa:
            pa += 1
        if counts_ab:
            ab += 1
        if bucket in counts:
            counts[bucket] += 1
        if p.get("gameDate"):
            dates.append(p["gameDate"])
        if event == "intent_walk":
            counts["BB"] = counts.get("BB", 0) + 1  # IBB is also a BB

    if pa == 0:
        return {
            "sampleSize": 0, "PA": 0,
            "datesCovered": None,
            "status": "MISSING_DATA",
        }

    h = counts["1B"] + counts["2B"] + counts["3B"] + counts["HR"]
    bb_total = counts["BB"]
    avg = round(h / ab, 3) if ab else None
    obp_denom = ab + bb_total + counts["HBP"] + counts["SF"]
    obp = round((h + bb_total + counts["HBP"]) / obp_denom, 3) if obp_denom else None
    slg = round((counts["1B"] + 2 * counts["2B"] + 3 * counts["3B"] + 4 * counts["HR"]) / ab, 3) if ab else None
    iso = round(slg - avg, 3) if (slg is not None and avg is not None) else None
    babip_denom = ab - counts["K"] - counts["HR"] + counts["SF"]
    babip = round((h - counts["HR"]) / babip_denom, 3) if babip_denom > 0 else None
    woba_denom = ab + bb_total + counts["SF"]
    woba = None
    if woba_denom:
        woba_num = (
            WOBA_WEIGHTS["BB"] * bb_total + WOBA_WEIGHTS["1B"] * counts["1B"]
            + WOBA_WEIGHTS["2B"] * counts["2B"] + WOBA_WEIGHTS["3B"] * counts["3B"]
            + WOBA_WEIGHTS["HR"] * counts["HR"]
        )
        woba = round(woba_num / woba_denom, 3)

    contact_quality = derive_contact_quality(windowed)

    return {
        "sampleSize": pa,
        "status": "AVAILABLE",
        "datesCovered": {"earliest": min(dates), "latest": max(dates)} if dates else None,
        "PA": pa, "AB": ab, "H": h,
        "1B": counts["1B"], "2B": counts["2B"], "3B": counts["3B"], "HR": counts["HR"],
        "BB": bb_total, "IBB": counts["IBB"], "HBP": counts["HBP"], "K": counts["K"], "SF": counts["SF"],
        "AVG": avg, "OBP": obp, "SLG": slg, "ISO": iso, "BABIP": babip, "wOBA": woba,
        "KPct": round(100.0 * counts["K"] / pa, 1), "BBPct": round(100.0 * bb_total / pa, 1),
        "HRPerPA": round(counts["HR"] / pa, 4),
        "GBPct": (contact_quality.get("battedBallTypeDistribution") or {}).get("groundBallPct"),
        "FBPct": (contact_quality.get("battedBallTypeDistribution") or {}).get("flyBallPct"),
        "LDPct": (contact_quality.get("battedBallTypeDistribution") or {}).get("lineDrivePct"),
        "PullPct": (contact_quality.get("sprayDistribution") or {}).get("pullPct"),
        "CenterPct": (contact_quality.get("sprayDistribution") or {}).get("centerPct"),
        "OppoPct": (contact_quality.get("sprayDistribution") or {}).get("oppoPct"),
        "unrecognizedEvents": sorted(set(unrecognized)) if unrecognized else [],
    }


def derive_pitch_type_breakdown(pitches) -> dict:
    """Hitter performance grouped by pitch family (see pitch_taxonomy.classify_pitch_family)."""
    by_family = {}
    for p in pitches:
        family = classify_pitch_family(p.get("pitchType"), p.get("pitchName"))
        by_family.setdefault(family, []).append(p)
    return {
        family: {
            "discipline": derive_plate_discipline(fam_pitches),
            "contactQuality": derive_contact_quality(fam_pitches),
        }
        for family, fam_pitches in by_family.items()
    }


def derive_velocity_breakdown(pitches) -> dict:
    """
    Hitter performance by velocity bucket, scoped WITHIN each fastball
    family only (see pitch_taxonomy.velocity_bucket's own docstring for
    why an 87mph slider never joins an 87mph fastball's bucket).
    """
    by_family_bucket = {}
    for p in pitches:
        family = classify_pitch_family(p.get("pitchType"), p.get("pitchName"))
        if family not in FASTBALL_FAMILIES:
            continue
        bucket = velocity_bucket(family, p.get("releaseSpeed"))
        if bucket is None:
            continue
        by_family_bucket.setdefault(family, {}).setdefault(bucket, []).append(p)
    return {
        family: {
            bucket: {
                "discipline": derive_plate_discipline(bucket_pitches),
                "contactQuality": derive_contact_quality(bucket_pitches),
            }
            for bucket, bucket_pitches in buckets.items()
        }
        for family, buckets in by_family_bucket.items()
    }


def derive_location_summary(pitches) -> dict:
    """Zone-frequency summary -- continuous plateX/plateZ are preserved on the input pitches, never discarded here."""
    zone_counts = {}
    n_located = 0
    for p in pitches:
        zone = classify_zone(p.get("plateX"), p.get("plateZ"), p.get("szTop"), p.get("szBot"))
        if zone is None:
            continue
        n_located += 1
        zone_counts[zone] = zone_counts.get(zone, 0) + 1
    if n_located == 0:
        return {"sampleSize": 0}
    return {
        "sampleSize": n_located,
        "zoneFrequency": {z: round(100.0 * c / n_located, 1) for z, c in zone_counts.items()},
    }


def derive_count_state_breakdown(pitches) -> dict:
    """Discipline outcomes grouped by count-state bucket (see pitch_taxonomy.classify_count_state)."""
    buckets = {
        "hitterAhead": [], "pitcherAhead": [], "twoStrikes": [], "threeBallCount": [],
        "isFirstPitch": [], "is02": [], "is12": [],
    }
    for p in pitches:
        state = classify_count_state(p.get("balls"), p.get("strikes"))
        for key in buckets:
            if state.get(key):
                buckets[key].append(p)
    return {key: derive_plate_discipline(group) for key, group in buckets.items()}


def compare_windows(recent: dict, baseline: dict, fields) -> dict:
    """
    Simple recent-vs-baseline delta for the given field names (present
    in both dicts' top level, e.g. two derive_plate_discipline() or
    derive_contact_quality() outputs) -- NOT change-point detection,
    just the raw comparison this mission's spec asks the architecture to
    support. A field missing (None) in either side yields delta=None.
    """
    deltas = {}
    for field in fields:
        r, b = recent.get(field), baseline.get(field)
        deltas[field] = {
            "recent": r, "baseline": b,
            "delta": round(r - b, 3) if (isinstance(r, (int, float)) and isinstance(b, (int, float))) else None,
        }
    return deltas
