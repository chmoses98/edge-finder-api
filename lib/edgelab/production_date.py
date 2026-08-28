"""
lib/edgelab/production_date.py
================================================================
ONE authoritative implementation of "what production date is this
run about?" for the EdgeLab Daily Pipeline Heartbeat.

Why this module exists (Heartbeat False-Failure Incident, 2026-08-27)
---------------------------------------------------------------------
.github/workflows/edgelab-daily-heartbeat.yml is scheduled at 23:45 UTC
precisely so that the production date it validates has already had all
of its fetch-slate.yml opportunities (16:00/20:00/22:00 UTC) and the
prior date's postgame opportunity (~06:00-07:45 UTC). But GitHub Actions
cron is best-effort: it can (and on this repository routinely does)
start a scheduled run hours after its intended checkpoint. Every one of
the first four scheduled heartbeat runs started AFTER midnight UTC:

    intended checkpoint      actual run created_at     delay
    2026-08-24T23:45Z        2026-08-25T00:00:10Z      15m
    2026-08-25T23:45Z        2026-08-26T00:00:30Z      15m
    2026-08-26T23:45Z        2026-08-27T05:06:16Z      5h21m
    2026-08-27T23:45Z        2026-08-28T07:05:23Z      7h20m

The old implementation resolved its target date from the wall clock at
process start, so each of those runs validated the NEXT production date
-- a date whose own slate cycle had not begun yet -- and manufactured a
full sheet of MISSING_* failures for it. Delay changed the date being
validated. That is the bug this module removes.

The invariant, stated once
---------------------------------------------------------------------
    DELAY MUST NOT CHANGE THE DATE BEING VALIDATED.

A scheduled heartbeat validates the production date of its INTENDED
scheduled checkpoint, computed as the latest occurrence of the run's own
cron expression (GitHub hands it to the workflow verbatim as
`github.event.schedule` -- the schedule literal is never duplicated
here, so changing the workflow's cron changes the semantics with it and
no second scheduler implementation can drift) at or before a durable
anchor timestamp for the run. Manual dispatch keeps its own, separately
documented semantics (see resolve_target_date).

Production dates are America/New_York dates
---------------------------------------------------------------------
Every other daily workflow in this repository resolves its date with
`TZ='America/New_York' date +%Y-%m-%d` (fetch-slate.yml, edgelab-
postgame.yml, clv-update.yml, capture-snapshots-scheduled.yml,
discover-kalshi-mlb-markets.yml, ...) and every EdgeLab partition
(data/pipeline/<date>/, data/edgelab/snapshots/<date>/, observations/,
settlements/) is keyed by that Eastern date, while the timestamps
INSIDE those artifacts (capturedAt, run keys) are UTC. So the ET
calendar date of an instant is the repo's canonical production date, and
comparing a UTC timestamp's leading "YYYY-MM-DD" against a production
date is a category error (a 2026-08-27T02:18Z capture belongs to
production date 2026-08-26 -- 22:18 ET). Conversion is done here with
zoneinfo, never with a fixed UTC-4/-5 offset, so DST transitions are
handled by the tz database rather than by arithmetic that is wrong for
half the year.

Purity: this module reads no files, opens no sockets, and never consults
repository data to decide a date -- picking whichever adjacent date
happens to have artifacts is exactly the failure mode a watchdog must
never have.
"""
import re
from datetime import date as _date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET_ZONE_NAME = "America/New_York"
ET_ZONE = ZoneInfo(ET_ZONE_NAME)

DATE_FORMAT = "%Y-%m-%d"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Trigger identities recorded in the resolution record's `triggerType`.
TRIGGER_SCHEDULE = "schedule"
TRIGGER_DISPATCH_EXPLICIT = "workflow_dispatch_explicit_date"
TRIGGER_DISPATCH_CURRENT_DAY = "workflow_dispatch_current_day"
TRIGGER_LOCAL_CURRENT_DAY = "local_current_day"

# A scheduled run that did not begin within the same clock minute as its
# own checkpoint is recorded as delayed. This is descriptive metadata
# only -- it never influences which date is chosen (that is always the
# checkpoint's own ET date, delayed or not).
DELAYED_RUN_THRESHOLD_SECONDS = 60


class TargetDateError(ValueError):
    """Raised when a target date cannot be resolved deterministically.

    Deliberately fatal rather than falling back to a wall-clock guess:
    a heartbeat that silently validates the wrong date is worse than a
    heartbeat that fails loudly saying it does not know which date it is
    supposed to validate.
    """


# ---------------------------------------------------------------------
# Date / timestamp primitives
# ---------------------------------------------------------------------

def validate_date(value, *, field="date"):
    """Strict YYYY-MM-DD validation (format AND real calendar date).

    "2026-8-1", "08/26/2026", "2026-08-26T00:00:00Z", "2026-02-30" and
    "yesterday" are all rejected -- the date that reaches the health
    check is the one its artifact is filed under, so a malformed one
    must never be silently coerced.
    """
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise TargetDateError(f"{field} must be exactly YYYY-MM-DD, got {value!r}")
    try:
        datetime.strptime(value, DATE_FORMAT)
    except ValueError as exc:
        raise TargetDateError(f"{field} is not a real calendar date: {value!r} ({exc})") from exc
    return value


def parse_utc_timestamp(value, *, field="timestamp"):
    """Parse an ISO-8601 instant (GitHub's `...Z` form included) to aware UTC."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise TargetDateError(f"{field} must be an ISO-8601 timestamp, got {value!r}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise TargetDateError(f"{field} is not a valid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_utc_iso(dt):
    """Canonical `YYYY-MM-DDTHH:MM:SSZ` rendering, matching ids.utc_now_iso()."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def et_date_for_instant(dt):
    """The America/New_York calendar date an instant belongs to (DST-aware)."""
    return parse_utc_timestamp(dt).astimezone(ET_ZONE).strftime(DATE_FORMAT)


def et_date_for_timestamp(value):
    """et_date_for_instant for a stored artifact timestamp; None if unparseable.

    Used for `capturedAt` fields, which are UTC instants written into
    artifacts filed under an Eastern production date -- an unparseable or
    absent value must degrade to "cannot confirm", never to a wrong date.
    """
    try:
        return et_date_for_instant(parse_utc_timestamp(value))
    except TargetDateError:
        return None


def et_today(now=None):
    """Today's production date in America/New_York."""
    now = parse_utc_timestamp(now) if now is not None else datetime.now(timezone.utc)
    return et_date_for_instant(now)


def previous_date(date_str):
    """The calendar day before `date_str` -- the heartbeat's settlement date.

    Pure calendar arithmetic on the production (Eastern) date, so it is
    unaffected by DST: production dates are dates, not 24-hour spans.
    """
    validate_date(date_str)
    return (datetime.strptime(date_str, DATE_FORMAT) - timedelta(days=1)).strftime(DATE_FORMAT)


# ---------------------------------------------------------------------
# Cron: the run's OWN schedule expression, evaluated backwards
# ---------------------------------------------------------------------
_CRON_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _parse_cron_field(spec, low, high, field_name):
    values = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            raise TargetDateError(f"empty {field_name} entry in cron field {spec!r}")
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise TargetDateError(f"invalid step {step_text!r} in cron {field_name} field {spec!r}")
            step = int(step_text)
        if part == "*":
            start, end = low, high
        elif "-" in part.lstrip("-"):
            start_text, _, end_text = part.partition("-")
            if not (start_text.isdigit() and end_text.isdigit()):
                raise TargetDateError(f"invalid range {part!r} in cron {field_name} field {spec!r}")
            start, end = int(start_text), int(end_text)
        elif part.isdigit():
            start = end = int(part)
        else:
            raise TargetDateError(
                f"unsupported cron {field_name} value {part!r} in {spec!r} -- "
                "GitHub Actions crons in this repository are numeric (*, n, a-b, */n, lists)"
            )
        if start < low or end > high or start > end:
            raise TargetDateError(f"cron {field_name} value {part!r} out of range {low}-{high}")
        values.update(range(start, end + 1, step))
    if not values:
        raise TargetDateError(f"cron {field_name} field {spec!r} matches nothing")
    return values


def parse_cron(expression):
    """Parse a standard 5-field cron expression (the GitHub Actions dialect).

    Returns {minutes, hours, daysOfMonth, months, daysOfWeek,
    domRestricted, dowRestricted}. Day-of-week accepts both 0 and 7 for
    Sunday. Non-numeric extensions (names, L, W, #) are rejected loudly
    rather than silently mis-evaluated -- GitHub itself only guarantees
    the POSIX subset for `schedule:`.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise TargetDateError(f"cron expression must be a non-empty string, got {expression!r}")
    fields = expression.split()
    if len(fields) != 5:
        raise TargetDateError(f"cron expression must have exactly 5 fields, got {expression!r}")
    names = ("minute", "hour", "day-of-month", "month", "day-of-week")
    parsed = [
        _parse_cron_field(field, low, high, name)
        for field, (low, high), name in zip(fields, _CRON_FIELD_RANGES, names)
    ]
    days_of_week = {0 if v == 7 else v for v in parsed[4]}
    return {
        "expression": expression.strip(),
        "minutes": parsed[0],
        "hours": parsed[1],
        "daysOfMonth": parsed[2],
        "months": parsed[3],
        "daysOfWeek": days_of_week,
        "domRestricted": fields[2] != "*",
        "dowRestricted": fields[4] != "*",
    }


def _day_matches(spec, day):
    if day.month not in spec["months"]:
        return False
    # cron day-of-week: 0 = Sunday; Python's weekday(): 0 = Monday.
    dow = (day.weekday() + 1) % 7
    dom_hit = day.day in spec["daysOfMonth"]
    dow_hit = dow in spec["daysOfWeek"]
    if spec["domRestricted"] and spec["dowRestricted"]:
        # Standard Vixie-cron OR semantics when both fields are restricted.
        return dom_hit or dow_hit
    if spec["domRestricted"]:
        return dom_hit
    if spec["dowRestricted"]:
        return dow_hit
    return True


def latest_cron_occurrence_at_or_before(expression, instant, *, max_lookback_days=400):
    """The most recent firing of `expression` (UTC, as GitHub evaluates it) at or before `instant`.

    This is the heartbeat's INTENDED checkpoint: for cron '45 23 * * *'
    and an actual start of 2026-08-27T05:06:16Z it returns
    2026-08-26T23:45:00Z, no matter how large the delay, because a
    delayed run is still the run for the checkpoint it was queued from.
    """
    spec = parse_cron(expression)
    cutoff = parse_utc_timestamp(instant).replace(second=0, microsecond=0)
    day = cutoff.date()
    for offset in range(max_lookback_days + 1):
        candidate_day = day - timedelta(days=offset)
        if not _day_matches(spec, candidate_day):
            continue
        for hour in sorted(spec["hours"], reverse=True):
            for minute in sorted(spec["minutes"], reverse=True):
                candidate = datetime(
                    candidate_day.year, candidate_day.month, candidate_day.day,
                    hour, minute, tzinfo=timezone.utc,
                )
                if candidate <= cutoff:
                    return candidate
    raise TargetDateError(
        f"cron {expression!r} has no occurrence within {max_lookback_days} days before {to_utc_iso(cutoff)}"
    )


# ---------------------------------------------------------------------
# The one resolution entry point
# ---------------------------------------------------------------------

def resolve_target_date(*, event_name, dispatch_date=None, schedule_expression=None,
                        anchor=None, anchor_source=None, now=None):
    """Resolve the production date this heartbeat run must validate.

    Exactly three semantics, in this precedence order:

    1. An explicit manual date (`workflow_dispatch.inputs.date`) is
       ALWAYS authoritative and is used verbatim after strict
       YYYY-MM-DD validation -- an operator asking about 2026-08-11 gets
       2026-08-11, whatever the clock says.

    2. A `schedule` run validates the ET production date of its INTENDED
       checkpoint: the latest occurrence of its own cron expression
       (`github.event.schedule`, passed in, never re-declared here) at or
       before `anchor`. `anchor` must be a timestamp that identifies the
       run rather than the attempt -- the workflow passes the run's REST
       `created_at`, which is preserved across re-runs, so re-running a
       failed 2026-08-26 heartbeat on 2026-08-28 still validates
       2026-08-26. Delay never moves the date; it is only recorded, as
       `delayedRun`/`delaySeconds`.

    3. Manual dispatch with no date (and any other/local invocation)
       validates the CURRENT production date in America/New_York at
       dispatch time -- the same `TZ='America/New_York' date +%Y-%m-%d`
       convention every other daily workflow in this repo uses. Manual
       dispatch deliberately never inherits scheduled-checkpoint
       semantics: a human pressing "Run workflow" is asking about today.

    Returns the resolution record embedded in the health artifact as
    `dateResolution` (targetDate, settlementDate, triggerType,
    scheduleExpression, scheduledCheckpointUtc, anchorTimestamp,
    anchorSource, delayedRun, delaySeconds, resolvedAt).
    """
    now_dt = parse_utc_timestamp(now) if now is not None else datetime.now(timezone.utc)
    record = {
        "targetDate": None,
        "settlementDate": None,
        "triggerType": None,
        "eventName": event_name or None,
        "scheduleExpression": None,
        "scheduledCheckpointUtc": None,
        "anchorTimestamp": None,
        "anchorSource": None,
        "delayedRun": False,
        "delaySeconds": None,
        "resolvedAt": to_utc_iso(now_dt),
        "productionDateTimezone": ET_ZONE_NAME,
    }

    if dispatch_date is not None and str(dispatch_date).strip():
        record["targetDate"] = validate_date(str(dispatch_date).strip(), field="workflow_dispatch input date")
        record["triggerType"] = TRIGGER_DISPATCH_EXPLICIT
    elif event_name == TRIGGER_SCHEDULE:
        if not schedule_expression or not str(schedule_expression).strip():
            raise TargetDateError(
                "a schedule run must carry its own cron expression (github.event.schedule) -- "
                "refusing to guess a production date from the wall clock, which is exactly the "
                "2026-08-27 false-failure this resolver exists to prevent"
            )
        if anchor is None:
            raise TargetDateError("a schedule run must carry an anchor timestamp for its checkpoint")
        anchor_dt = parse_utc_timestamp(anchor, field="anchor")
        checkpoint = latest_cron_occurrence_at_or_before(schedule_expression, anchor_dt)
        delay_seconds = int((anchor_dt - checkpoint).total_seconds())
        record["targetDate"] = et_date_for_instant(checkpoint)
        record["triggerType"] = TRIGGER_SCHEDULE
        record["scheduleExpression"] = str(schedule_expression).strip()
        record["scheduledCheckpointUtc"] = to_utc_iso(checkpoint)
        record["anchorTimestamp"] = to_utc_iso(anchor_dt)
        record["anchorSource"] = anchor_source
        record["delaySeconds"] = delay_seconds
        record["delayedRun"] = delay_seconds >= DELAYED_RUN_THRESHOLD_SECONDS
    else:
        record["targetDate"] = et_today(now_dt)
        record["triggerType"] = (
            TRIGGER_DISPATCH_CURRENT_DAY if event_name == "workflow_dispatch" else TRIGGER_LOCAL_CURRENT_DAY
        )
        record["anchorTimestamp"] = to_utc_iso(now_dt)
        record["anchorSource"] = anchor_source or "wall_clock_at_dispatch"

    record["settlementDate"] = previous_date(record["targetDate"])
    return record
