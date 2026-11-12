"""
lib/edgelab/hitter_snapshot_join.py
===================================
Pure, no-IO point-in-time (PIT) join between archived hitter market observations and the
prospective hitter projection snapshots (data/edgelab/hitter_projection_snapshots/<date>.jsonl).

Why this exists (#309): ModelEvaluation hitter rows were only ever fed from the transient
data/pipeline/<date>/hitter_projection_board.json (lib.edgelab.hitter_board_bridge). That artifact survives for
only a handful of dates and, when built after first pitch, is all GAME_STARTED, so every archived hitter
evaluation is NO_MODEL_SUPPORT / DATA_QUALITY_BLOCK. The prospective snapshot store is the only durable record of
predictions that really existed before the events, each with its own generation timestamp -- but nothing joined it
to observations.

Rules (never relaxed to raise coverage):
  * exact marketTicker identity, plus matching marketFamily and threshold when both sides state them;
  * the snapshot must be projectionStatus == PROJECTED with a numeric modelProbability in [0, 1];
  * projectionGeneratedAt must parse as a tz-aware UTC instant; unparseable/naive timestamps are rejected,
    never guessed;
  * no look-ahead: projectionGeneratedAt <= observation capturedAt (the latest eligible snapshot wins; ties
    broken by snapshot id so the choice is deterministic);
  * optional pregame guard: if the caller supplies the contract's start instant, the snapshot must have been
    generated at or before it;
  * every rejection carries an explicit reason; nothing is fabricated and a rejected observation stays unjoined.
"""
from datetime import datetime, timezone

STATUS_PROJECTED = "PROJECTED"

R_NO_SNAPSHOT = "NO_SNAPSHOT_FOR_TICKER"
R_IDENTITY = "IDENTITY_MISMATCH"
R_NOT_PROJECTED = "SNAPSHOT_STATUS_NOT_PROJECTED"
R_NO_PROBABILITY = "SNAPSHOT_PROBABILITY_MISSING_OR_INVALID"
R_BAD_SNAPSHOT_TIME = "SNAPSHOT_TIMESTAMP_INVALID"
R_BAD_OBS_TIME = "OBSERVATION_TIMESTAMP_INVALID"
R_AFTER_OBSERVATION = "SNAPSHOT_GENERATED_AFTER_OBSERVATION"
R_AFTER_GAME_START = "SNAPSHOT_GENERATED_AFTER_GAME_START"


def parse_utc(value):
    """tz-aware UTC datetime, or None for anything missing, unparseable or timezone-naive (never guessed)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc)


def _valid_probability(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= float(value) <= 1.0


def _identity_ok(snapshot, observation):
    if snapshot.get("marketTicker") != observation.get("marketTicker"):
        return False
    for key in ("marketFamily", "threshold"):
        a, b = snapshot.get(key), observation.get(key)
        if a is not None and b is not None and a != b:
            return False
    return True


def select_pit_snapshot(snapshots_for_ticker, observation, game_start=None):
    """
    Returns (snapshot | None, reason | None, diagnostics).

    `snapshots_for_ticker`: every archived snapshot row for this ticker. `observation`: the market observation
    (needs marketTicker, capturedAt). `game_start`: optional tz-aware start instant for the pregame guard.
    The selected snapshot is the latest eligible one at or before the observation; if none is eligible the
    reason is the dominant rejection (the rejection that got furthest through the checks).
    """
    diag = {"candidates": len(snapshots_for_ticker or []), "eligible": 0, "reasonCounts": {}}
    if not snapshots_for_ticker:
        return None, R_NO_SNAPSHOT, diag

    obs_at = parse_utc(observation.get("capturedAt"))
    if obs_at is None:
        return None, R_BAD_OBS_TIME, diag
    start = parse_utc(game_start) if game_start is not None else None

    eligible = []
    # ordered by how far a candidate got; the reported reason is the furthest-progressed rejection
    progress = [R_IDENTITY, R_NOT_PROJECTED, R_NO_PROBABILITY, R_BAD_SNAPSHOT_TIME, R_AFTER_OBSERVATION, R_AFTER_GAME_START]
    for snap in snapshots_for_ticker:
        reason = None
        if not _identity_ok(snap, observation):
            reason = R_IDENTITY
        elif snap.get("projectionStatus") != STATUS_PROJECTED:
            reason = R_NOT_PROJECTED
        elif not _valid_probability(snap.get("modelProbability")):
            reason = R_NO_PROBABILITY
        else:
            gen = parse_utc(snap.get("projectionGeneratedAt"))
            if gen is None:
                reason = R_BAD_SNAPSHOT_TIME
            elif gen > obs_at:
                reason = R_AFTER_OBSERVATION
            elif start is not None and gen > start:
                reason = R_AFTER_GAME_START
            else:
                eligible.append((gen, str(snap.get("hitterProjectionSnapshotId") or ""), snap))
        if reason:
            diag["reasonCounts"][reason] = diag["reasonCounts"].get(reason, 0) + 1

    diag["eligible"] = len(eligible)
    if eligible:
        eligible.sort(key=lambda t: (t[0], t[1]))
        gen, _, snap = eligible[-1]
        diag["snapshotAgeSeconds"] = (obs_at - gen).total_seconds()
        return snap, None, diag

    best = max(diag["reasonCounts"], key=lambda r: (progress.index(r), diag["reasonCounts"][r]))
    return None, best, diag

def ticker_event_start_utc(ticker):
    """Parse the encoded ET scheduled start only for the known MLB hitter ticker form.

    Unknown formats and invalid or ambiguous local wall times fail closed. The
    encoded schedule is not independent proof of actual first pitch.
    """
    import re
    from zoneinfo import ZoneInfo

    if not isinstance(ticker, str):
        return None
    match = re.fullmatch(
        r"KXMLBHIT-(\\d{2})([A-Z]{3})(\\d{2})(\\d{2})(\\d{2})[A-Z0-9]+-[A-Z0-9-]+",
        ticker,
    )
    if match is None:
        return None
    year, mon, day, hour, minute = match.groups()
    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
              "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
    if mon not in months:
        return None
    try:
        wall = datetime(2000 + int(year), months[mon], int(day), int(hour), int(minute))
        eastern = ZoneInfo("America/New_York")
        candidates = []
        for fold in (0, 1):
            aware = wall.replace(tzinfo=eastern, fold=fold)
            utc = aware.astimezone(timezone.utc)
            if utc.astimezone(eastern).replace(tzinfo=None) == wall:
                candidates.append(utc)
        return candidates[0] if len(set(candidates)) == 1 else None
    except (ValueError, OverflowError):
        return None
