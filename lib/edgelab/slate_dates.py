"""
Which slate date the production pipeline is publishing right now.

scripts/app_export.py publishes the newest slate at or before today (ET), so between midnight ET and the
next morning's slate fetch it still publishes yesterday's slate (resolve_export_date). Every producer that
feeds that export for a *started* game (scripts/refresh_game_state.py, the watchdog's stage F) must key on
the same date, not on the Eastern calendar date: a game with a 20:00 ET first pitch ends after midnight ET.
"""
from __future__ import annotations

import os
import re

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def slate_dates(data_root):
    """Every date with a published slate (data/slates/<date>/authoritative.json), ascending."""
    folder = os.path.join(data_root, "slates")
    if not os.path.isdir(folder):
        return []
    return sorted(d for d in os.listdir(folder)
                  if _DATE_RE.match(d) and os.path.exists(os.path.join(folder, d, "authoritative.json")))


def export_slate_date(data_root, today):
    """The newest published slate date at or before `today` (ET); `today` itself when none is published."""
    past = [d for d in slate_dates(data_root) if d <= today]
    return past[-1] if past else today
