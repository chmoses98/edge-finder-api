#!/usr/bin/env python3
"""
lib/research/park_wind_derivation.py
=======================================
Hitter Projection Engine -- Phase 3 field-relative wind.

Pure function turning raw wind (speed + compass degrees the wind is
coming FROM, meteorological convention -- matches data/weather.json's
`windDeg`) plus a park's orientation (lib.research.park_geometry) into
directional components a future contact-conversion model can consume,
WITHOUT pretending outdoor wind matters when a roof is confirmed closed.

This does not attempt full air-density/ball-flight physics (temperature/
humidity/pressure/altitude -> carry distance) -- that is explicitly
future work (see this module's docstring's "not built" note and
lib.research.hitter_feature_context's weatherContext.ballFlightAdjustment
placeholder, unchanged from Phase 1).
"""

from typing import Optional
import math

from lib.research.park_geometry import resolve_park_geometry


def wind_field_relative_components(wind_speed, wind_deg_from, orientation_deg) -> Optional[dict]:
    """
    wind_speed: mph (or any consistent unit -- passed through unchanged).
    wind_deg_from: compass degrees the wind is blowing FROM (meteorological
      convention, matches data/weather.json's windDeg).
    orientation_deg: this park's home-plate-to-center-field bearing.

    Returns None if any input is missing/non-numeric (never fabricates
    a zero component in place of missing wind/orientation data).

    componentTowardCF: positive = blowing OUT toward CF (helps fly balls
      carry), negative = blowing IN from CF toward the plate.
    componentTowardRF: positive = blowing toward the RF line side,
      negative = toward the LF line side (this is a park-relative axis,
      not yet resolved to a specific hitter's pull/oppo side -- that
      requires the hitter's own batSide, applied by the caller, e.g.
      lib.research.hitter_feature_context's sprayContext).
    """
    try:
        speed = float(wind_speed)
        deg_from = float(wind_deg_from)
        orientation = float(orientation_deg)
    except (TypeError, ValueError):
        return None

    blowing_toward_deg = (deg_from + 180.0) % 360.0
    rf_bearing = (orientation + 90.0) % 360.0

    component_cf = round(speed * math.cos(math.radians(blowing_toward_deg - orientation)), 2)
    component_rf = round(speed * math.cos(math.radians(blowing_toward_deg - rf_bearing)), 2)

    if component_cf > 0:
        label = "blowing_out"
    elif component_cf < 0:
        label = "blowing_in"
    else:
        label = "neutral"

    return {
        "componentTowardCF": component_cf,
        "componentTowardRF": component_rf,
        "blowingTowardDeg": round(blowing_toward_deg, 1),
        "label": label,
    }


def build_field_relative_wind_context(weather: dict, team_abbr: str, as_of: Optional[str] = None) -> dict:
    """
    Top-level entry point combining a weather record (see
    lib.research.hitter_feature_context._weather_context's input shape
    -- temp/wind/windDeg/dome) with this team's park geometry.

    Never claims outdoor wind matters for a confirmed-closed roof:
    - roofType == 'fixed_dome' (park_geometry, static fact) -> always
      NOT_APPLICABLE, regardless of what the weather feed's `dome` flag says.
    - weather.get('dome') truthy (today's live/assumed roof state, per
      the existing Phase 1 weatherContext limitation: retractable-roof
      parks are conservatively always reported as dome=true because no
      live open/closed-today signal exists -- see
      hitter_feature_context._park_context's roofStatus note) ->
      NOT_APPLICABLE for today specifically.
    """
    geometry = resolve_park_geometry(team_abbr, as_of=as_of)
    if not geometry:
        return {"status": "MISSING_DATA", "note": f"No park geometry on file for team {team_abbr!r}."}

    if geometry.get("roofType") == "fixed_dome":
        return {"status": "NOT_APPLICABLE", "reason": "fixed_dome_roof",
                "note": "This park has a permanently closed/fixed roof -- outdoor wind never applies."}

    if not weather:
        return {"status": "MISSING_DATA", "note": "No weather record supplied."}

    if weather.get("dome"):
        return {"status": "NOT_APPLICABLE", "reason": "roof_closed_or_retractable_assumed_closed",
                "note": "Weather feed reports this game as dome/roof conditions today -- outdoor wind not applicable. "
                        "(For a retractable-roof park, this repo has no live open/closed-today signal -- see "
                        "hitter_feature_context parkContext.roofStatus's own PARTIAL status for the same limitation.)"}

    components = wind_field_relative_components(weather.get("wind"), weather.get("windDeg"), geometry.get("orientationDeg"))
    if components is None:
        return {"status": "MISSING_DATA", "note": "Wind speed/direction or park orientation missing -- cannot compute field-relative components."}

    return {
        "status": "AVAILABLE",
        **components,
        "orientationConfidence": geometry.get("orientationConfidence"),
        "source": "lib.research.park_wind_derivation + config/park_geometry.json (orientationDeg unverified against a live source -- see that file's note)",
    }
