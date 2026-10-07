#!/usr/bin/env python3
"""
scripts/app_export.py
=====================
Edge Finder app export for the MLB repository: a pure adapter from this
repository's committed production data onto the shared app contract
(contract/edge_finder_contract/) published atomically into app/latest.

    python scripts/app_export.py --out app/latest [--data-root data] [--now ISO]
                                 [--commit-sha X] [--workflow-run-id Y] [--date YYYY-MM-DD]

NOTHING here changes a model, a threshold, a stake or a bet authority. It only
reads what the production pipelines already committed and re-expresses it in
the contract's objects. Null is the answer wherever the repository holds
nothing (fees, CLV, bankroll, thesis prose).

Sources (all relative to --data-root, default <repo>/data), see docs/APP_EXPORT.md:
    slates/<date>/authoritative.json or slate.json  events + marketLedger
    edgelab/games/<date>.jsonl                      events (fallback gameId -> mlbGamePk)
    edgelab/markets/<date>.jsonl[.gz]               market identity
    edgelab/observations/<date>.jsonl[.gz]          latest order book per ticker
    kalshi/discovery/<date>.json                    period / side / line / eligibility
    edgelab/model_evaluations/<date>.jsonl[.gz]     model prices
    edgelab/recommendations/<date>.jsonl[.gz]       recommendations (canonical)
    pipeline/<date>/execution.json                  recommendations (pregame fallback)
    handicapping_card/latest.json                   real-money eligibility (never dollars)
    edgelab/bets/bets.jsonl                         wagers (trackingType REAL / REAL_PROBE)
    edgelab/settlements/<date>.jsonl[.gz]           settlements for those wagers
    edgelab/operational_health/production_health_gate.json, edgelab/health/<date>.json,
    meta.json                                       health inputs

"No slate today": the export date is the newest date at or before today (America/
New_York) that has a non-empty markets partition or a pipeline slate directory,
and health reports the resulting staleness honestly.

On any failure only health.json is rewritten (export_failed=True); the previous
payload is left byte-for-byte intact and the process exits 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
_CONTRACT_DIR = os.path.join(REPO_ROOT, "contract")
if _CONTRACT_DIR not in sys.path:
    sys.path.insert(0, _CONTRACT_DIR)

from lib.edgelab import storage  # noqa: E402
from lib.edgelab.decision_side import resolve_side  # noqa: E402
from lib.edgelab.production_date import et_date_for_instant  # noqa: E402

from edge_finder_contract import (  # noqa: E402
    board, build, freshness, health, ids, linkage, performance, publish, timeutil,
)
from lib.research import mlb_player_props  # noqa: E402

SPORT = "MLB"
SOURCE_REPO = "chmoses98/edge-finder-api"
SOURCE_BRANCH = "main"
EVENT_SOURCE = "mlb_game_pk"
TEAM_SOURCE = "mlb_team_abbr"
MARKET_SOURCE = "edgelab_markets"
BET_AUTHORITY = "MANUAL"
MODEL_REQUIRED = True
REAL_TRACKING_TYPES = ("REAL", "REAL_PROBE")

THRESHOLDS = {
    "market_data": freshness.Thresholds(20 * 60, 2 * 60 * 60),
    "model": freshness.Thresholds(30 * 60, 6 * 60 * 60),
}
EXPORT_CADENCE_SECONDS = 30 * 60

_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")
# KXMLBSB-26OCT011400PHIATL-PHITTURNER7-1 -> "26OCT011400PHIATL"
_EVENT_SUFFIX_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+)$")
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

EVENT_STATUS = {
    "pre-game": "SCHEDULED", "scheduled": "SCHEDULED", "warmup": "SCHEDULED", "pregame": "SCHEDULED",
    "delayed start": "SCHEDULED", "in progress": "LIVE", "live": "LIVE", "manager challenge": "LIVE",
    "final": "FINAL", "game over": "FINAL", "completed early": "FINAL", "completed": "FINAL",
    "postponed": "POSTPONED", "cancelled": "CANCELLED", "canceled": "CANCELLED",
}
MARKET_STATUS = {"active": "OPEN", "open": "OPEN", "closed": "CLOSED", "settled": "SETTLED",
                 "finalized": "SETTLED", "unopened": "UNOPENED", "initialized": "UNOPENED"}
DISCOVERY_SIDE = {"over": "OVER", "under": "UNDER", "home": "HOME", "away": "AWAY", "tie": "TIE",
                  "draw": "DRAW", "yes": "OTHER"}
DATA_QUALITY = {"full": "OK", "ok": "OK", "partial": "DEGRADED", "degraded": "DEGRADED",
                "missing": "CANNOT_TRUST_INPUTS", "untrusted": "CANNOT_TRUST_INPUTS"}
BET_RESULT = {"WIN": "WON", "WON": "WON", "LOSS": "LOST", "LOST": "LOST", "PUSH": "PUSH", "VOID": "VOID"}
SKIPPED_REC_STATUSES = ("INSUFFICIENT_MODEL_SUPPORT", "NOT_EVALUATED")


class ExportError(RuntimeError):
    """A problem in this repository's inputs that the export must not paper over."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_card(data_root):
    """The current handicapping card. ``handicapping_card/latest.json`` is a pointer (date, path and
    summary counts, where ``bettingEligibleGames`` is a COUNT); the card itself, with the per-game
    market lists, is the dated file it names. Older pointers without ``path`` fall back to the
    dated file of their ``date``; a pointer whose card is missing yields no card."""
    pointer = _read_json(os.path.join(data_root, "handicapping_card", "latest.json"))
    if not pointer:
        return None
    if isinstance(pointer.get("bettingEligibleGames"), list):
        return pointer  # already a full card
    name = os.path.basename(pointer.get("path") or "") or (f"{pointer['date']}.json" if pointer.get("date") else "")
    return _read_json(os.path.join(data_root, "handicapping_card", name)) if name else None


def _partition(data_root, entity, date):
    """Rows of data/edgelab/<entity>/<date>.jsonl[.gz] (gz-aware, [] when absent)."""
    if not date:
        return []
    plain = os.path.join(data_root, "edgelab", entity, f"{date}.jsonl")
    path = plain if os.path.exists(plain) else plain + ".gz"
    return list(storage.read_records(path))


def _partition_dates(data_root, entity):
    folder = os.path.join(data_root, "edgelab", entity)
    if not os.path.isdir(folder):
        return []
    out = set()
    for name in os.listdir(folder):
        base = name[:-9] if name.endswith(".jsonl.gz") else name[:-6] if name.endswith(".jsonl") else None
        if base and _DATE_RE.match(base):
            out.add(base)
    return sorted(out)


def _partition_has_rows(data_root, entity, date):
    for _ in _partition(data_root, entity, date):
        return True
    return False


def _ts(value):
    """Canonical aware-UTC ISO or None. A naive input is an error (never emitted)."""
    if value in (None, ""):
        return None
    return timeutil.to_iso(value)


def _latest(stamps):
    real = [s for s in stamps if s]
    return max(real, key=timeutil.parse_ts) if real else None


def _is_ticker(value):
    return isinstance(value, str) and bool(_TICKER_RE.match(value.strip().upper()))


def _pct(value):
    return build.from_percent(value)


def _event_key_from_ticker(ticker):
    """(date, kalshi team key) encoded in a Kalshi MLB ticker, or None."""
    parts = str(ticker or "").split("-")
    if len(parts) < 2:
        return None
    m = _EVENT_SUFFIX_RE.match(parts[1])
    if not m:
        return None
    yy, mon, dd, _hhmm, teams = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return None
    return (f"20{yy}-{month:02d}-{dd}", teams)


def _event_key_from_suffix(date, suffix):
    m = _EVENT_SUFFIX_RE.match(str(suffix or ""))
    if m:
        return (date, m.group(5))
    return None


# ---------------------------------------------------------------------------
# input loading
# ---------------------------------------------------------------------------

def resolve_export_date(data_root, today):
    """Newest date <= today with a non-empty markets partition or a pipeline slate directory."""
    candidates = set()
    for d in _partition_dates(data_root, "markets"):
        if d <= today and _partition_has_rows(data_root, "markets", d):
            candidates.add(d)
    pipeline = os.path.join(data_root, "pipeline")
    if os.path.isdir(pipeline):
        for d in os.listdir(pipeline):
            if _DATE_RE.match(d) and d <= today and os.path.exists(os.path.join(pipeline, d, "recommendations.json")):
                candidates.add(d)
    for d in _partition_dates(data_root, "games"):
        if d <= today and _partition_has_rows(data_root, "games", d):
            candidates.add(d)
    if not candidates:
        raise ExportError(f"no slate, markets or games partition at or before {today} under {data_root}")
    return max(candidates)


def load_inputs(data_root, date):
    slate = _read_json(os.path.join(data_root, "slates", date, "authoritative.json"))
    if not slate or slate.get("date") != date:
        candidate = _read_json(os.path.join(data_root, "slate.json"))
        slate = candidate if candidate and candidate.get("date") == date else None
    pipeline_dir = os.path.join(data_root, "pipeline", date)
    bets = list(storage.read_records(os.path.join(data_root, "edgelab", "bets", "bets.jsonl")))
    real_bets = [b for b in bets if b.get("trackingType") in REAL_TRACKING_TYPES
                 and b.get("recordStatus", "ACTIVE") == "ACTIVE"]
    settlement_dates = {date}
    for b in real_bets:
        if b.get("gameDate"):
            settlement_dates.add(b["gameDate"])
        key = _event_key_from_ticker(b.get("marketTicker"))
        if key:
            settlement_dates.add(key[0])
    settlements = []
    for d in sorted(settlement_dates):
        settlements.extend(_partition(data_root, "settlements", d))
    return {
        "date": date,
        "slate": slate,
        "games": _partition(data_root, "games", date),
        "markets": _partition(data_root, "markets", date),
        "observations": _partition(data_root, "observations", date),
        "discovery": _read_json(os.path.join(data_root, "kalshi", "discovery", f"{date}.json")) or {},
        "model_evaluations": _partition(data_root, "model_evaluations", date),
        "recommendations": _partition(data_root, "recommendations", date),
        "execution": _read_json(os.path.join(pipeline_dir, "execution.json")),
        "pipeline_recommendations": _read_json(os.path.join(pipeline_dir, "recommendations.json")),
        "provenance": _read_json(os.path.join(pipeline_dir, "provenance.json")),
        "card": _load_card(data_root),
        "real_bets": real_bets,
        "settlements": settlements,
        "gate": _read_json(os.path.join(data_root, "edgelab", "operational_health", "production_health_gate.json")),
        "daily_health": _read_json(os.path.join(data_root, "edgelab", "health", f"{date}.json")),
        "meta": _read_json(os.path.join(data_root, "meta.json")),
        # research-only player-prop projection inputs (lib/research/mlb_player_props.py)
        "pitcher_model": mlb_player_props.load_pitcher_model(data_root),
        "postseason_settlements": [r for d in _partition_dates(data_root, "settlements")
                                   if d >= "2026-09-29" and d <= date for r in _partition(data_root, "settlements", d)],
        "hitter_snapshots": mlb_player_props.load_hitter_snapshots(data_root, date),
    }


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def _team(abbr, display=None):
    abbr = str(abbr).strip().upper()
    return build.participant(sport=SPORT, participant_type="TEAM", source=TEAM_SOURCE, source_id=abbr,
                             display_name=display or abbr, short_name=abbr)


def _event_status(raw, final_from_settlement=False):
    if final_from_settlement:
        return "FINAL"
    return EVENT_STATUS.get(str(raw or "").strip().lower(), "UNKNOWN" if raw else "SCHEDULED")


#: Statuses at which a pregame recommendation can still be acted on.
_PREGAME_REC_STATUSES = ("RECOMMENDED", "RESEARCH_CANDIDATE", "WATCH")


def _time_gate(status, start_iso, confidence, now_iso):
    """(status, basis). The slate's status is a snapshot from its fetch, often hours old; a game
    whose scheduled first pitch has passed must never be exported as SCHEDULED (a pregame
    candidate). It becomes LIVE with basis FIRST_PITCH_PASSED until the schedule or settlement
    says otherwise (FINAL / POSTPONED / CANCELLED are never overridden)."""
    if (status == "SCHEDULED" and confidence != "PLACEHOLDER" and start_iso and now_iso
            and timeutil.parse_ts(start_iso) <= timeutil.parse_ts(now_iso)):
        return "LIVE", "FIRST_PITCH_PASSED"
    return status, None


def pregame_closed(event, now_iso):
    """True when no pregame recommendation may be offered for this event: it is live, final,
    postponed/cancelled, or its scheduled first pitch has passed."""
    if event is None:
        return True
    if event["status"] not in ("SCHEDULED", "UNKNOWN"):
        return True
    return timeutil.parse_ts(event["start_time_utc"]) <= timeutil.parse_ts(now_iso)


def build_events(inputs, now_iso, warnings):
    """events, plus the lookups the other builders need:
    by_pk {gamePk str: event}, by_key {(date, kalshi teams): event}, by_alias {fallback gameId: event}."""
    date = inputs["date"]
    slate = inputs["slate"]
    final_pks = {str(s.get("gameId")) for s in inputs["settlements"]
                 if ((s.get("settlementEvidence") or {}).get("gameStatus") == "Final")}
    events, by_pk, by_key, by_alias = [], {}, {}, {}
    extra_ids = {}
    for row in inputs["games"]:
        pk = row.get("mlbGamePk") or (row.get("supersededBy") or {}).get("canonicalGameId")
        if pk:
            extra_ids.setdefault(str(pk), []).append(row)

    for g in (slate or {}).get("games", []) or []:
        pk = g.get("gameId")
        if pk in (None, ""):
            continue
        pk = str(pk)
        away, home = g.get("away") or {}, g.get("home") or {}
        a = _team(away.get("abbr"), away.get("team"))
        h = _team(home.get("abbr"), home.get("team"))
        extra = extra_ids.get(pk, [])
        source_ids = {"kalshi_event_key": f"{slate.get('kalshiDate') or ''}{g.get('kalshiKey') or ''}" or None,
                      "odds_api_event_id": g.get("oddsApiEventId")}
        if extra:
            source_ids["edgelab_game_id"] = extra[0].get("gameId")
        status, status_basis = _time_gate(_event_status(g.get("status"), pk in final_pks),
                                          _ts(g["startTime"]), "SCHEDULED", now_iso)
        ev = build.event(
            sport=SPORT, source=EVENT_SOURCE, source_id=pk, start_time_utc=g["startTime"],
            participants=[a, h], home_participant=h["participant_id"], away_participant=a["participant_id"],
            league="MLB", season=date[:4], competition=None,
            status=status,
            start_time_source=g.get("scheduleSource") or "statsapi", start_time_confidence="SCHEDULED",
            venue=g.get("venue"), source_ids=source_ids,
            schedule_updated_at=g.get("lineupCheckedAt"), last_updated_at=now_iso,
            extensions={
                "game_date": date, "kalshi_key": g.get("kalshiKey"),
                "kalshi_event_ticker_suffix": g.get("kalshiEventTickerSuffix"),
                "lineup_confirmed": g.get("lineupConfirmed"), "lineup_status": g.get("lineupStatus"),
                "lineup_source": g.get("lineupSource"), "lineup_checked_at": _ts(g.get("lineupCheckedAt")),
                "away_pitcher": ((away.get("pitcher") or {}).get("name")),
                "home_pitcher": ((home.get("pitcher") or {}).get("name")),
                "park": (g.get("park") or {}).get("name"), "park_factor": (g.get("park") or {}).get("parkFactor"),
                "slate_status_raw": g.get("status"), "status_basis": status_basis,
                "game_type": g.get("gameType"), "series_description": g.get("seriesDescription"),
                "series_game_number": g.get("seriesGameNumber"),
            })
        events.append(ev)
        by_pk[pk] = ev
        if g.get("kalshiKey"):
            by_key[(date, str(g["kalshiKey"]))] = ev
        key = _event_key_from_suffix(date, g.get("kalshiEventTickerSuffix"))
        if key:
            by_key[key] = ev
        for row in extra:
            if row.get("gameId"):
                by_alias[str(row["gameId"])] = ev

    for pk, rows in extra_ids.items():
        if pk in by_pk:
            continue
        row = rows[0]
        start = row.get("scheduledStartTime")
        if start:
            start_iso, confidence, source = _ts(start), "SCHEDULED", row.get("source") or "edgelab_games"
        else:
            start_iso, confidence, source = f"{row.get('gameDate') or date}T00:00:00Z", "PLACEHOLDER", "edgelab_games_date_only"
            warnings.append(f"event {pk}: no scheduled start time in games partition; placeholder midnight UTC")
        a, h = _team(row.get("awayTeam")), _team(row.get("homeTeam"))
        status, status_basis = _time_gate(_event_status(row.get("status"), pk in final_pks), start_iso, confidence, now_iso)
        ev = build.event(
            sport=SPORT, source=EVENT_SOURCE, source_id=pk, start_time_utc=start_iso, participants=[a, h],
            home_participant=h["participant_id"], away_participant=a["participant_id"], league="MLB",
            season=date[:4], status=status,
            start_time_source=source, start_time_confidence=confidence, venue=row.get("venue"),
            source_ids={"edgelab_game_id": row.get("gameId"), "kalshi_event_key": row.get("kalshiKey")},
            schedule_updated_at=row.get("updatedAt"), last_updated_at=now_iso,
            extensions={"game_date": row.get("gameDate") or date, "kalshi_key": row.get("kalshiKey"),
                        "doubleheader_game_number": row.get("doubleheaderGameNumber"),
                        "slate_status_raw": row.get("status"), "status_basis": status_basis})
        events.append(ev)
        by_pk[pk] = ev
        if row.get("kalshiKey"):
            by_key[(row.get("gameDate") or date, str(row["kalshiKey"]))] = ev
        for r in rows:
            if r.get("gameId"):
                by_alias[str(r["gameId"])] = ev
    events.sort(key=lambda e: (e["start_time_utc"], e["event_id"]))
    return events, {"by_pk": by_pk, "by_key": by_key, "by_alias": by_alias}


def _event_for(lookups, *, game_id=None, ticker=None):
    if game_id not in (None, ""):
        gid = str(game_id)
        ev = lookups["by_pk"].get(gid) or lookups["by_alias"].get(gid)
        if ev:
            return ev
    key = _event_key_from_ticker(ticker)
    if key:
        return lookups["by_key"].get(key)
    return None


# ---------------------------------------------------------------------------
# markets
# ---------------------------------------------------------------------------

def _latest_observations(observations):
    best = {}
    for o in observations:
        t = o.get("marketTicker")
        if not _is_ticker(t) or not o.get("capturedAt"):
            continue
        cur = best.get(t)
        if cur is None or timeutil.parse_ts(o["capturedAt"]) > timeutil.parse_ts(cur["capturedAt"]):
            best[t] = o
    return best


PLAYER_SOURCE = "mlbam_player_id"   # same participant scheme as scripts/research_export.py player profiles


def _player_pid(mlbam_id):
    return ids.participant_id(SPORT, "PLAYER", PLAYER_SOURCE, str(mlbam_id)) if mlbam_id else None


def build_player_props(inputs, lookups, now_iso):
    """{ticker: mlb.player_prop.v1 record} for every player-prop market (research only)."""
    params, starts, team_games = inputs.get("pitcher_model") or (None, [], [])
    ctx = None
    if params:
        ctx = mlb_player_props.PitcherContext(
            params, starts, team_games,
            mlb_player_props.postseason_starts_from_settlements(inputs.get("postseason_settlements") or []))
    return mlb_player_props.build_records(
        slate=inputs["slate"], markets=inputs["markets"], now_iso=now_iso,
        event_for=lambda t: _event_for(lookups, ticker=t),
        pregame_closed=lambda ev: pregame_closed(ev, now_iso), player_id_for=_player_pid,
        pitcher_ctx=ctx, hitter_rows=inputs.get("hitter_snapshots"))


def build_markets(inputs, lookups, warnings):
    obs = _latest_observations(inputs["observations"])
    props = inputs.get("_player_props") or {}
    discovery = {c.get("ticker"): c for c in (inputs["discovery"].get("contracts") or []) if _is_ticker(c.get("ticker"))}
    settled = {}
    for s in inputs["settlements"]:
        if s.get("settlementStatus") == "SETTLED" and _is_ticker(s.get("marketTicker")):
            settled[s["marketTicker"]] = s
    markets, index = [], {}
    for row in inputs["markets"]:
        ticker = row.get("marketTicker")
        if not _is_ticker(ticker):
            warnings.append(f"market row without a Kalshi ticker skipped: {ticker!r}")
            continue
        ticker = ticker.strip().upper()
        if ticker in index:
            continue
        o = obs.get(ticker) or {}
        c = discovery.get(ticker) or {}
        ev = _event_for(lookups, game_id=row.get("gameId") or c.get("gameId") or o.get("gameId"), ticker=ticker)
        observed = str(o.get("marketStatus") or c.get("marketStatus") or "").lower()
        if ticker in settled:
            status = "SETTLED"
        else:
            status = MARKET_STATUS.get(observed, "UNKNOWN")
            if status == "OPEN" and ev is not None and ev["status"] == "FINAL":
                # The game is final (MLB Stats API settlement evidence) but no settlement row names this
                # ticker and the last observation predates the end: the market cannot still be tradable.
                status = "CLOSED"
        team = row.get("team") or (c.get("subjectId") if c.get("subjectType") == "TEAM" else None)
        side_raw = str(c.get("side") or "").strip()
        side = DISCOVERY_SIDE.get(side_raw.lower())
        if side is None and side_raw:
            side = "PARTICIPANT"
        period = row.get("marketHorizon") or c.get("period")
        ext = {
            "title": row.get("title"), "team": row.get("team"), "player": row.get("player"),
            "comparison_operator": row.get("comparisonOperator"), "outcome_label": row.get("outcomeLabel"),
            "subject_type": c.get("subjectType"), "model_support_status": c.get("modelSupportStatus"),
            "real_money_eligibility_status": c.get("realMoneyEligibilityStatus"),
            "observation_checkpoint": o.get("checkpoint"), "spread": o.get("spreadCents"),
            "settlement_result": (settled.get(ticker) or {}).get("result"), "observed_status": observed or None,
            "player_prop": props.get(ticker),
        }
        m = build.market(
            sport=SPORT, kalshi_ticker=ticker, market_family=row.get("marketFamily") or c.get("marketFamily"),
            yes_description=row.get("title") or c.get("marketTitle") or f"YES on {ticker}", source=MARKET_SOURCE,
            event_id=ev["event_id"] if ev else None, kalshi_event_ticker=row.get("eventTicker"),
            kalshi_series_ticker=row.get("seriesTicker"), period=str(period).upper() if period else None,
            participant_id=_team(team)["participant_id"] if team else None,
            player_id=(props.get(ticker) or {}).get("player_id"), side=side,
            line=c.get("line"), threshold=row.get("threshold"),
            yes_bid=o.get("yesBid"), yes_ask=o.get("yesAsk"), no_bid=o.get("noBid"), no_ask=o.get("noAsk"),
            last_price=o.get("lastPrice"), volume=o.get("volume"), open_interest=o.get("openInterest"),
            market_status=status, close_time_utc=c.get("closeTime"), captured_at=o.get("capturedAt"),
            raw_market_reference=o.get("marketObservationId"),
            extensions={k: v for k, v in ext.items() if v is not None})
        markets.append(m)
        index[ticker] = m
    return markets, index


def ensure_market_stub(index, markets, ticker, *, lookups, family=None, description=None, game_id=None,
                       market_status="SETTLED"):
    ticker = ticker.strip().upper()
    if ticker in index:
        return index[ticker]
    ev = _event_for(lookups, game_id=game_id, ticker=ticker)
    m = build.market_stub(sport=SPORT, kalshi_ticker=ticker, market_family=family or "unknown",
                          yes_description=description, event_id=ev["event_id"] if ev else None,
                          source="bets_ledger", market_status=market_status)
    markets.append(m)
    index[ticker] = m
    return m


# ---------------------------------------------------------------------------
# model prices
# ---------------------------------------------------------------------------

def _ledger_rows(inputs):
    out = []
    for g in ((inputs["slate"] or {}).get("games") or []):
        for row in g.get("marketLedger") or []:
            out.append((str(g.get("gameId")), row))
    return out


def _orient(selection_side, fair_pct, market_pct):
    """P(YES) from a selection-oriented probability pair (percent)."""
    fair = _pct(fair_pct)
    mkt = _pct(market_pct)
    if selection_side == "NO":
        fair = None if fair is None else round(1.0 - fair, 6)
        mkt = None if mkt is None else round(1.0 - mkt, 6)
    return fair, mkt


def build_model_prices(inputs, run_id, lookups, market_index, now, warnings):
    market_rows = {m.get("marketTicker"): m for m in inputs["markets"] if m.get("marketTicker")}
    ledger_by_ticker = {}
    for pk, row in _ledger_rows(inputs):
        t = row.get("executablePriceMarketTicker")
        if _is_ticker(t):
            ledger_by_ticker.setdefault(t.strip().upper(), (pk, row))

    chosen = {}
    for r in inputs["model_evaluations"]:
        t = r.get("marketTicker")
        if r.get("modelFairProbability") is None or not _is_ticker(t) or not r.get("createdAt"):
            continue
        t = t.strip().upper()
        rank = (1 if r.get("artifactSource") == "prospective_snapshot" else 0, timeutil.parse_ts(r["createdAt"]))
        if t not in chosen or rank > chosen[t][0]:
            chosen[t] = (rank, r)

    prices, refused = [], 0
    for t in sorted(chosen):
        r = chosen[t][1]
        side, basis, refusal, _evidence = resolve_side(r, market_rows.get(t))
        if side is None:
            refused += 1
            continue
        fair, mkt = _orient(side, r["modelFairProbability"], r.get("marketImpliedProbability"))
        if t not in market_index:
            ensure_market_stub(market_index, inputs["_markets_list"], t, lookups=lookups,
                               family=r.get("marketFamily"), game_id=r.get("gameId"))
        m = market_index[t]
        ledger = ledger_by_ticker.get(t)
        ext = {
            "model_evaluation_id": r.get("modelEvaluationId"), "native_run_id": r.get("runId"),
            "checkpoint": r.get("checkpoint"), "artifact_source": r.get("artifactSource"),
            "evaluation_status": r.get("evaluationStatus"), "selection": r.get("selection"),
            "selection_side": side, "side_basis": basis, "probability_adapter": r.get("probabilityAdapter"),
            "quality_tier": r.get("qualityTier"), "estimated_edge_pct": r.get("estimatedEdge"),
            "model_fair_odds": r.get("modelFairOdds"), "thesis_tags": r.get("thesisTags") or [],
            "lineup_confirmation_state": r.get("lineupConfirmationState"),
        }
        if ledger:
            lrow = ledger[1]
            ext["market_ledger"] = {
                "market": lrow.get("market"), "status": lrow.get("status"), "model_prob_pct": lrow.get("modelProb"),
                "net_executable_edge_pct": lrow.get("netExecutableEdge"), "bet_up_to_price_net": lrow.get("betUpToPriceNet"),
                "confidence_tier": lrow.get("confidenceTier"), "selection_side": lrow.get("executablePriceSide"),
            }
        version = "+".join(x for x in (r.get("modelConfigVersion"), (r.get("modelCommitSha") or "")[:12]) if x) or None
        prices.append(build.model_price(
            run_id=run_id, market_id=m["market_id"], fair_probability=fair, generated_at=r["createdAt"],
            event_id=m.get("event_id"), model_version=version, market_probability=mkt,
            inputs_as_of=(r.get("provenance") or {}).get("capturedAt") or r["createdAt"],
            freshness_status=freshness.status_for(r["createdAt"], component="model", now=now, thresholds=THRESHOLDS["model"]),
            data_quality_status=DATA_QUALITY.get(str(r.get("dataQuality") or "").lower(), "UNKNOWN"),
            support_status=r.get("qualityTier"), extensions={k: v for k, v in ext.items() if v is not None}))
        ledger_by_ticker.pop(t, None)

    # marketLedger rows whose ticker has no model_evaluation row at all
    generated = (inputs["pipeline_recommendations"] or {}).get("meta", {}).get("createdAt") \
        or (inputs["slate"] or {}).get("_officialRunAt")
    for t, (pk, lrow) in sorted(ledger_by_ticker.items()):
        if lrow.get("modelProb") is None or lrow.get("executablePriceSide") not in ("YES", "NO") or not generated:
            continue
        fair, mkt = _orient(lrow["executablePriceSide"], lrow["modelProb"], lrow.get("marketProbVF"))
        if t not in market_index:
            ensure_market_stub(market_index, inputs["_markets_list"], t, lookups=lookups, game_id=pk)
        m = market_index[t]
        prices.append(build.model_price(
            run_id=run_id, market_id=m["market_id"], fair_probability=fair, generated_at=generated,
            event_id=m.get("event_id"), model_version=None, market_probability=mkt,
            inputs_as_of=lrow.get("priceSnapshotTimestamp") or generated,
            freshness_status=freshness.status_for(generated, component="model", now=now, thresholds=THRESHOLDS["model"]),
            data_quality_status="UNKNOWN", support_status="MARKET_LEDGER",
            extensions={"source": "slate_market_ledger", "market": lrow.get("market"), "status": lrow.get("status"),
                        "selection_side": lrow["executablePriceSide"], "net_executable_edge_pct": lrow.get("netExecutableEdge"),
                        "bet_up_to_price_net": lrow.get("betUpToPriceNet"), "confidence_tier": lrow.get("confidenceTier")}))
    if refused:
        warnings.append(f"{refused} model evaluation row(s) skipped: contract side could not be proven (decision_side refused)")
    return prices


# ---------------------------------------------------------------------------
# recommendations
# ---------------------------------------------------------------------------

def _real_money_tickers(inputs):
    """Tickers the handicapping card / execution gate marks real-money eligible. Never dollars."""
    out = set()
    card = inputs["card"] or {}
    games = card.get("bettingEligibleGames")
    for g in games if isinstance(games, list) else []:
        for m in g.get("markets") or []:
            if m.get("realMoneyEligible") and _is_ticker(m.get("ticker")):
                out.add(m["ticker"].strip().upper())
    for c in ((inputs["execution"] or {}).get("data") or {}).get("candidates") or []:
        if c.get("realMoneyEligible") and _is_ticker(c.get("sourceRecommendationTicker")):
            out.add(c["sourceRecommendationTicker"].strip().upper())
    return out


def _ledger_side(inputs, game_id, market_name, ticker):
    for pk, row in _ledger_rows(inputs):
        if pk == str(game_id) and row.get("market") == market_name:
            t = row.get("executablePriceMarketTicker")
            if t and ticker and t.strip().upper() == ticker and row.get("executablePriceSide") in ("YES", "NO"):
                return row["executablePriceSide"], "SLATE_MARKET_LEDGER_EXECUTABLE_PRICE_SIDE"
    return None, None


def _map_status(native, real_money, bet_is_real):
    """-> (status, authority, research_only)."""
    if native in ("RECOMMENDED", "Accepted", "ACCEPTED"):
        if real_money:
            return "RECOMMENDED", "MANUAL", False
        return "RESEARCH_CANDIDATE", "RESEARCH_ONLY", True
    if native == "BET_PLACED":
        if bet_is_real:
            return "RECOMMENDED", "MANUAL", False
        return "RESEARCH_CANDIDATE", "RESEARCH_ONLY", True
    if native in ("PAPER", "MODEL_ONLY", "RESEARCH_CANDIDATE"):
        return "RESEARCH_CANDIDATE", "RESEARCH_ONLY", True
    if native == "WATCH":
        return "WATCH", "RESEARCH_ONLY", True
    if native in ("Rejected", "REJECTED") or str(native).startswith("PASS"):
        return "PASS", "RESEARCH_ONLY", True
    if native in ("EXPIRED",):
        return "EXPIRED", "RESEARCH_ONLY", True
    return "NOT_PLAYABLE", "RESEARCH_ONLY", True


def _gate_recommendation(status, authority, research_only, event, now):
    """-> (status, authority, research_only, reason). A pregame recommendation for a game that is
    live, final, past its first pitch, postponed or cancelled is never exported as actionable:
    RECOMMENDED / RESEARCH_CANDIDATE / WATCH become EXPIRED (game started) or NOT_PLAYABLE
    (postponed / cancelled). Authority and research_only are history (what the recommendation was
    when issued) and are kept; the native status stays in extensions.native_status."""
    if status not in _PREGAME_REC_STATUSES or not pregame_closed(event, timeutil.to_iso(now)):
        return status, authority, research_only, None
    if event["status"] in ("POSTPONED", "CANCELLED"):
        return "NOT_PLAYABLE", authority, research_only, f"EVENT_{event['status']}: no pregame action on this game"
    return "EXPIRED", authority, research_only, "GAME_STARTED: the pregame window closed at first pitch"


def build_recommendations(inputs, run_id, lookups, market_index, now, warnings):
    market_rows = {m.get("marketTicker"): m for m in inputs["markets"] if m.get("marketTicker")}
    real_money = _real_money_tickers(inputs)
    bets_by_id = {b["betId"]: b for b in inputs["real_bets"] if b.get("betId")}
    recs, skipped, unproven = [], 0, 0

    canonical = [r for r in inputs["recommendations"] if r.get("status") not in SKIPPED_REC_STATUSES]
    skipped = len(inputs["recommendations"]) - len(canonical)
    if canonical:
        for r in canonical:
            t = r.get("marketTicker")
            if not _is_ticker(t) or not r.get("createdAt"):
                unproven += 1
                continue
            t = t.strip().upper()
            bet = bets_by_id.get(r.get("betId"))
            side, basis = (bet.get("side"), "PLACED_BET_SIDE") if bet and bet.get("side") in ("YES", "NO") else (None, None)
            if side is None:
                side, basis = _ledger_side(inputs, r.get("gameId"), r.get("marketName"), t)
            if side is None and r.get("marketName"):
                side, basis, _refusal, _ev = resolve_side({"selection": r.get("marketName"), "provenance": r.get("provenance")},
                                                          market_rows.get(t))
            if side is None:
                unproven += 1
                continue
            ev = _event_for(lookups, game_id=r.get("gameId"), ticker=t)
            if ev is None:
                unproven += 1
                continue
            m = ensure_market_stub(market_index, inputs["_markets_list"], t, lookups=lookups,
                                   family=r.get("marketFamily"), game_id=r.get("gameId"))
            status, authority, research_only = _map_status(r.get("status"), t in real_money,
                                                           bool(bet) and bet.get("trackingType") in REAL_TRACKING_TYPES)
            status, authority, research_only, gate_reason = _gate_recommendation(status, authority, research_only, ev, now)
            recs.append(build.recommendation(
                sport=SPORT, source_repo=SOURCE_REPO, event_id=ev["event_id"], market_id=m["market_id"], run_id=run_id,
                selection=side, market_description=f"{r.get('marketName') or m['yes_description']} ({side} on {t})",
                created_at=r["createdAt"], status=status, authority=authority, research_only=research_only,
                native_id=r.get("recommendationId"), current_probability=_pct(r.get("marketImpliedProbability")),
                current_price=_pct(r.get("marketImpliedProbability")), fair_probability=_pct(r.get("modelFairProbability")),
                edge=_pct(r.get("estimatedEdge")), bet_up_to_price=_pct(r.get("priceCeiling")),
                bet_up_to_probability=_pct(r.get("priceCeiling")), confidence=r.get("confidence"),
                reason_not_playable=gate_reason or (r.get("passReason") if status in ("PASS", "NOT_PLAYABLE") else None),
                data_freshness=freshness.status_for(r["createdAt"], component="recommendations", now=now),
                source_ids={"recommendation_id": r.get("recommendationId"), "model_evaluation_id": r.get("modelEvaluationId"),
                            "bet_id": r.get("betId"), "native_run_id": r.get("runId")},
                extensions={"native_status": r.get("status"), "market_name": r.get("marketName"), "side_basis": basis,
                            "pass_reason": r.get("passReason"), "ev_per_dollar": r.get("evPerDollar"),
                            "rank_within_game": r.get("rankWithinGame"), "bet_placed": r.get("betPlaced"),
                            "real_money_eligible": t in real_money, "source": "edgelab_recommendations"}))
    else:
        execution = inputs["execution"] or {}
        created = (execution.get("meta") or {}).get("createdAt")
        for c in (execution.get("data") or {}).get("candidates") or []:
            t = c.get("sourceRecommendationTicker")
            if not _is_ticker(t) or not created:
                unproven += 1
                continue
            t = t.strip().upper()
            pk = None
            for gpk, row in _ledger_rows(inputs):
                if row.get("market") == c.get("market") and (row.get("executablePriceMarketTicker") or "").upper() == t:
                    pk = gpk
            side, basis = _ledger_side(inputs, pk, c.get("market"), t)
            ev = _event_for(lookups, game_id=pk, ticker=t)
            if side is None or ev is None:
                unproven += 1
                continue
            m = ensure_market_stub(market_index, inputs["_markets_list"], t, lookups=lookups, game_id=pk)
            status, authority, research_only = _map_status(c.get("status"), bool(c.get("realMoneyEligible")), False)
            status, authority, research_only, gate_reason = _gate_recommendation(status, authority, research_only, ev, now)
            recs.append(build.recommendation(
                sport=SPORT, source_repo=SOURCE_REPO, event_id=ev["event_id"], market_id=m["market_id"], run_id=run_id,
                selection=side, market_description=f"{c.get('market')} ({side} on {t})", created_at=created,
                status=status, authority=authority, research_only=research_only,
                native_id=f"execution:{inputs['date']}:{c.get('game')}:{c.get('market')}",
                bet_up_to_price=_pct(c.get("approvedPrice")), confidence=c.get("tier"),
                reason_not_playable=gate_reason or (c.get("rejectionReason") if status in ("PASS", "NOT_PLAYABLE") else None),
                data_freshness=freshness.status_for(created, component="recommendations", now=now),
                extensions={"native_status": c.get("status"), "market_name": c.get("market"), "side_basis": basis,
                            "real_money_eligible": bool(c.get("realMoneyEligible")), "source": "pipeline_execution"}))
    if skipped:
        warnings.append(f"{skipped} full-universe recommendation row(s) with status in {SKIPPED_REC_STATUSES} not exported")
    if unproven:
        warnings.append(f"{unproven} recommendation row(s) skipped: ticker, event or contract side could not be established")
    return recs


# ---------------------------------------------------------------------------
# theses
# ---------------------------------------------------------------------------

def build_theses(inputs, run_id, lookups, model_prices, now_iso):
    tags_by_event = {}
    for p in model_prices:
        if p.get("event_id"):
            tags_by_event.setdefault(p["event_id"], set()).update(p["extensions"].get("thesis_tags") or [])
    out = []
    for g in ((inputs["slate"] or {}).get("games") or []):
        ev = lookups["by_pk"].get(str(g.get("gameId")))
        if ev is None:
            continue
        ledger = g.get("marketLedger") or []
        first = ledger[0] if ledger else {}
        evidence = {
            "away_pitcher": ev["extensions"].get("away_pitcher"), "home_pitcher": ev["extensions"].get("home_pitcher"),
            "projected_runs": {"away": first.get("awayProjRuns"), "home": first.get("homeProjRuns"),
                               "total": first.get("totalProj"), "f5_away": first.get("f5AwayProj"), "f5_home": first.get("f5HomeProj")},
            "ledger_rows": [{"market": r.get("market"), "status": r.get("status"), "model_prob_pct": r.get("modelProb"),
                             "net_executable_edge_pct": r.get("netExecutableEdge")} for r in ledger],
        }
        out.append(build.thesis(
            sport=SPORT, run_id=run_id, event_id=ev["event_id"], generated_at=g.get("lineupCheckedAt") or now_iso,
            summary=None, supporting_factors=sorted(tags_by_event.get(ev["event_id"], ())),
            context_notes={"lineups": g.get("lineupStatus")}, confidence_label=None, evidence=evidence))
    return out


# ---------------------------------------------------------------------------
# wagers and settlements
# ---------------------------------------------------------------------------

def _wager_source(bet):
    if bet.get("importBatchId") == "kalshi-router-v1":
        return "KALSHI_ROUTER"
    if bet.get("entryMethod") == "LEGACY_BACKFILL":
        return "LEGACY_IMPORT"
    return "MANUAL"


def _fees(bet):
    """executionEconomics.totalFees when the row has that block, else the row's own totalFees
    (router receipts write it at the top level: feeStatus ACTUAL_API_FILL). Null otherwise."""
    econ = bet.get("executionEconomics") or {}
    if econ.get("totalFees") is not None:
        return econ["totalFees"]
    return bet.get("totalFees")


def build_wagers_and_settlements(inputs, lookups, market_index, warnings):
    settlement_rows = {}
    for s in inputs["settlements"]:
        if s.get("betId"):
            cur = settlement_rows.get(s["betId"])
            if cur is None or (s.get("settlementStatus") == "SETTLED" and cur.get("settlementStatus") != "SETTLED"):
                settlement_rows[s["betId"]] = s
    wagers, settlements, skipped = [], [], 0
    for b in inputs["real_bets"]:
        t = b.get("marketTicker")
        if not _is_ticker(t) or b.get("side") not in ("YES", "NO") or b.get("stake") is None or b.get("entryPrice") is None:
            skipped += 1
            continue
        t = t.strip().upper()
        placed_at, basis = b.get("entryTimestamp"), "ENTRY_TIMESTAMP"
        if not placed_at:
            placed_at, basis = b.get("recordedAt") or b.get("createdAt"), "RECORDED_AT"
        if not placed_at:
            skipped += 1
            continue
        contracts, contracts_basis = b.get("contracts"), "LEDGER"
        if contracts is None:
            contracts, contracts_basis = round(float(b["stake"]) / float(b["entryPrice"]), 4), "DERIVED_STAKE_OVER_ENTRY_PRICE"
        status = {"settled": "SETTLED", "pending": "PENDING", "void": "VOID"}.get(str(b.get("status") or "").lower(), "UNKNOWN")
        m = ensure_market_stub(market_index, inputs["_markets_list"], t, lookups=lookups, family=b.get("marketFamily"),
                               game_id=b.get("gameId"), market_status="SETTLED" if status == "SETTLED" else "UNKNOWN")
        ext = {
            "bet_id": b.get("betId"), "tracking_type": b.get("trackingType"), "import_batch_id": b.get("importBatchId"),
            "entry_method": b.get("entryMethod"), "market_family": b.get("marketFamily"), "game_date": b.get("gameDate"),
            "placed_at_basis": basis, "timestamp_status": b.get("timestampStatus"), "contracts_basis": contracts_basis,
            "confidence": b.get("confidence"), "model_fair_probability_pct_at_entry": b.get("modelFairProbability"),
            "estimated_edge_pct_at_entry": b.get("estimatedEdgeAtEntry"), "closing_price": b.get("closingPrice"),
            "clv_pct_points": b.get("clv"), "clv_convention": b.get("clvConvention"),
            "execution_status": b.get("executionStatus"), "economics_source": b.get("economicsSource"),
            "contract_cost": b.get("contractCost"), "native_result": b.get("result"), "native_status": b.get("status"),
        }
        w = build.wager(
            sport=SPORT, kalshi_ticker=t, selection=b["side"], contracts=contracts, stake=b["stake"],
            average_price=b.get("averageFillPrice") if b.get("averageFillPrice") is not None else b["entryPrice"],
            placed_at=placed_at, source=_wager_source(b), destination_repo=SOURCE_REPO,
            source_bet_key=b.get("sourceBetKey"), native_id=b.get("betId"), event_id=m.get("event_id"), side="BUY",
            fees=_fees(b), settlement_status=status,
            source_ids={"bet_id": b.get("betId"), "source_bet_key": b.get("sourceBetKey"),
                        "recommendation_id": b.get("recommendationId"), "model_evaluation_id": b.get("modelEvaluationId")},
            extensions={k: v for k, v in ext.items() if v is not None})
        if status == "SETTLED" or (status == "VOID" and b.get("result")):
            row = settlement_rows.get(b.get("betId")) or {}
            settled_row = row.get("settlementStatus") == "SETTLED"
            settled_at = row.get("settledAt") if settled_row else None
            at_basis = "SETTLEMENT_ROW"
            if not settled_at:
                settled_at, at_basis = b.get("updatedAt") or b.get("recordedAt"), "BET_UPDATED_AT"
            evidence = row.get("settlementEvidence") or {}
            if evidence.get("kalshiOfficialResult"):
                verification = "EXCHANGE_CONFIRMED"
            elif settled_row:
                verification = "MODEL_DERIVED"
            else:
                verification = "UNVERIFIED"
            s = build.settlement(
                wager_id=w["wager_id"], market_id=w["market_id"], result=BET_RESULT.get(str(b.get("result") or "").upper(), "UNKNOWN"),
                settled_at=settled_at, source="edgelab_settlement" if settled_row else "bets_ledger",
                verification_status=verification, winning_side=row.get("result") if row.get("result") in ("YES", "NO") else None,
                # The ledger's returnAmount equals netProfitLoss on every row that has it (it is a net
                # figure, not a gross payout), so gross_payout stays null rather than mislabelled.
                gross_payout=None, fees=None, net_pnl=b.get("netProfitLoss"),
                refusals=[row["unavailableReason"]] if row.get("unavailableReason") else [],
                source_ids={"settlement_id": row.get("settlementId"), "bet_id": b.get("betId")},
                extensions={"settled_at_basis": at_basis, "settlement_status_native": row.get("settlementStatus"),
                            "return_amount_native": b.get("returnAmount"),
                            "evidence_source": evidence.get("sourceSystem"), "game_status": evidence.get("gameStatus"),
                            "was_placed": row.get("wasPlaced"), "was_recommended": row.get("wasRecommended")})
            settlements.append(s)
            w["settlement_id"] = s["settlement_id"]
            w["settlement_status"] = "SETTLED" if status == "SETTLED" else status
            w["payout"] = s["gross_payout"]
            w["profit_loss"] = s["net_pnl"]
        wagers.append(w)
    if skipped:
        warnings.append(f"{skipped} real wager row(s) skipped: no Kalshi ticker, side, stake, price or timestamp")
    return wagers, settlements


# ---------------------------------------------------------------------------
# health / run / publish
# ---------------------------------------------------------------------------

def _next_half_hour(now_dt):
    base = now_dt.replace(second=0, microsecond=0)
    minutes = 30 - (base.minute % 30)
    return base + timedelta(minutes=minutes)


def _extra_components(inputs, now):
    comps = {}
    gate = inputs["gate"] or {}
    if gate.get("checkedAt"):
        summary = gate.get("summary") or {}
        comps["production_gate"] = health.component(
            gate["checkedAt"], thresholds=freshness.DEFAULT_THRESHOLDS["settlement"], now=now, required=False,
            degraded=summary.get("overall") not in (None, "HEALTHY"),
            detail=f"production_health_gate overall={summary.get('overall')}")
    daily = inputs["daily_health"] or {}
    if daily.get("checkedAt"):
        comps["daily_health"] = health.component(
            daily["checkedAt"], thresholds=freshness.DEFAULT_THRESHOLDS["settlement"], now=now, required=False,
            degraded=daily.get("healthStatus") not in (None, "HEALTHY"),
            detail=f"edgelab daily health {daily.get('date')} status={daily.get('healthStatus')}")
    return comps


def build_bundle(inputs, *, now, commit_sha=None, workflow_run_id=None):
    now_iso = timeutil.to_iso(now)
    warnings = []
    date = inputs["date"]
    events, lookups = build_events(inputs, now_iso, warnings)
    inputs["_player_props"] = build_player_props(inputs, lookups, now_iso)
    markets, market_index = build_markets(inputs, lookups, warnings)
    inputs["_markets_list"] = markets

    evaluations = [r for r in inputs["model_evaluations"] if r.get("createdAt")]
    latest_eval = max(evaluations, key=lambda r: timeutil.parse_ts(r["createdAt"])) if evaluations else None
    provenance = (inputs["provenance"] or {}).get("data") or {}
    native_run_id = (latest_eval or {}).get("runId") or provenance.get("workflowRunId") or provenance.get("commitSha") or f"slate-{date}"
    run_id = build.run(sport=SPORT, repo=SOURCE_REPO, completed_at=now_iso, scope=f"slate {date}",
                       native_run_id=native_run_id)["run_id"]

    model_prices = build_model_prices(inputs, run_id, lookups, market_index, now, warnings)
    recommendations = build_recommendations(inputs, run_id, lookups, market_index, now, warnings)
    theses = build_theses(inputs, run_id, lookups, model_prices, now_iso)
    wagers, settlements = build_wagers_and_settlements(inputs, lookups, market_index, warnings)
    wagers = [linkage.apply_links(w, model_prices, recommendations, markets) for w in wagers]
    wagers.sort(key=lambda w: (w["placed_at"], w["wager_id"]))
    settlements.sort(key=lambda s: (s["settled_at"], s["settlement_id"]))

    last_market_capture = _latest([o.get("capturedAt") for o in inputs["observations"]])
    last_model_generated = _latest([p["generated_at"] for p in model_prices])
    settlement_as_of = _latest([s.get("settledAt") for s in inputs["settlements"] if s.get("settlementStatus") == "SETTLED"])
    router_as_of = _latest([b.get("recordedAt") for b in inputs["real_bets"] if b.get("importBatchId") == "kalshi-router-v1"])
    schedule_as_of = (inputs["meta"] or {}).get("fetchedAt") or provenance.get("capturedAt")
    model_version = None
    for p in model_prices:
        if p.get("model_version") and p["extensions"].get("artifact_source") == "prospective_snapshot":
            model_version = p["model_version"]
            break
    if model_version is None and model_prices:
        model_version = next((p["model_version"] for p in model_prices if p.get("model_version")), None)

    if not inputs["markets"]:
        warnings.append(f"markets partition for {date} is empty")
    if inputs["slate"] is None and inputs["markets"]:
        kalshi_games = {(r.get("awayTeam"), r.get("homeTeam")) for r in inputs["games"] if r.get("awayTeam")}
        warnings.append(f"no published slate for {date} (data/slates/{date}/authoritative.json missing): "
                        f"{len(kalshi_games)} Kalshi-discovered game(s) await MLB schedule reconciliation, so "
                        f"events and model prices cannot be built until Fetch Slate Data publishes the slate")
    if not model_prices:
        warnings.append(f"no model price could be exported for {date}")
    if date != et_date_for_instant(now):
        warnings.append(f"no slate for today ({et_date_for_instant(now)}); exporting newest slate date {date}")

    run_doc = build.run(
        sport=SPORT, repo=SOURCE_REPO, completed_at=now_iso, scope=f"slate {date}", status="SUCCESS",
        native_run_id=native_run_id, commit_sha=commit_sha, workflow_run_id=workflow_run_id, model_version=model_version,
        started_at=now_iso, events_requested=len(events), events_processed=len(events),
        markets_discovered=len(markets), markets_priced=len({p["market_id"] for p in model_prices}),
        recommendations_created=len(recommendations),
        data_sources=["data/slates/<date>/authoritative.json", "data/edgelab/games", "data/edgelab/markets",
                      "data/edgelab/observations", "data/kalshi/discovery", "data/edgelab/model_evaluations",
                      "data/edgelab/recommendations", "data/pipeline/<date>/execution.json",
                      "data/handicapping_card/latest.json", "data/edgelab/bets/bets.jsonl", "data/edgelab/settlements"],
        input_freshness={"kalshi": last_market_capture, "model": last_model_generated, "schedule": schedule_as_of,
                         "settlement": settlement_as_of, "router": router_as_of},
        warnings=warnings, source_ids={"slate_date": date, "pipeline_workflow_run_id": provenance.get("workflowRunId"),
                                       "pipeline_commit_sha": provenance.get("commitSha")})
    assert run_doc["run_id"] == run_id

    health_doc = health.build_health(
        sport=SPORT, run_id=run_id, bet_authority=BET_AUTHORITY, last_market_capture=last_market_capture,
        last_model_generated=last_model_generated, last_successful_run=now_iso, payload_run_id=run_id,
        payload_available=True, export_failed=False, commit_sha=commit_sha,
        next_scheduled_run=_next_half_hour(timeutil.parse_ts(now)), router_as_of=router_as_of,
        settlement_as_of=settlement_as_of, model_required=MODEL_REQUIRED, thresholds=THRESHOLDS,
        warnings=warnings, errors=[], extra_components=_extra_components(inputs, now), now=now)

    board_doc = board.build_board(sport=SPORT, run_id=run_id, generated_at=now_iso, events=events, markets=markets,
                                  model_prices=model_prices, recommendations=recommendations, wagers=wagers,
                                  health=health_doc, thresholds=THRESHOLDS, now=now)
    clv_values = {}
    for w in wagers:
        clv = w["extensions"].get("clv_pct_points")
        if clv is not None and w["extensions"].get("clv_convention") == "POSITIVE_IS_GOOD_V1":
            clv_values[w["wager_id"]] = round(float(clv) / 100.0, 6)
    perf = performance.build_performance(
        sport=SPORT, run_id=run_id, generated_at=now_iso, wagers=wagers, settlements=settlements, markets=markets,
        recommendations=recommendations, bankroll_history=None, bankroll_basis=None, clv_values=clv_values,
        notes=["Only trackingType REAL / REAL_PROBE rows of data/edgelab/bets/bets.jsonl are exported.",
               "Fees come from the ledger row (executionEconomics.totalFees or totalFees); null when the row has neither.",
               "gross_payout is null: the ledger's returnAmount is a net figure (equals netProfitLoss).",
               "CLV is the ledger's own clv (percentage points, POSITIVE_IS_GOOD_V1) divided by 100."])
    freshness_of = {}
    for name, stamp, component in (("kalshi", last_market_capture, "market_data"), ("model", last_model_generated, "model"),
                                   ("schedule", schedule_as_of, "schedule"), ("settlement", settlement_as_of, "settlement"),
                                   ("router", router_as_of, "router")):
        freshness_of[name] = {"as_of": _ts(stamp),
                              "status": freshness.status_for(stamp, component=component, now=now, thresholds=THRESHOLDS.get(component))}

    documents = {
        "events": build.collection("events", SPORT, run_id, now_iso, events),
        "markets": build.collection("markets", SPORT, run_id, now_iso, markets),
        "model_prices": build.collection("model_prices", SPORT, run_id, now_iso, model_prices),
        "recommendations": build.collection("recommendations", SPORT, run_id, now_iso, recommendations),
        "theses": build.collection("theses", SPORT, run_id, now_iso, theses),
        "wagers": build.collection("wagers", SPORT, run_id, now_iso, wagers),
        "settlements": build.collection("settlements", SPORT, run_id, now_iso, settlements),
        "runs": build.collection("runs", SPORT, run_id, now_iso, [run_doc]),
        "board": board_doc,
        "performance": perf,
    }
    board_rows = {row["event_id"]: row for row in board_doc["items"]}
    for ev in events:
        documents[f"event_detail/{ev['event_id']}"] = board.build_event_detail(
            sport=SPORT, run_id=run_id, generated_at=now_iso, event=ev, markets=markets, model_prices=model_prices,
            recommendations=recommendations, theses=theses, wagers=wagers, settlements=settlements,
            context={"slate_date": date, "lineup_status": ev["extensions"].get("lineup_status"),
                     "lineup_confirmed": ev["extensions"].get("lineup_confirmed")},
            data_freshness=board_rows[ev["event_id"]]["data_freshness"])
    return {"run_id": run_id, "generated_at": now_iso, "documents": documents, "health": health_doc,
            "freshness": freshness_of, "warnings": warnings, "model_version": model_version, "date": date}


def export(*, out_root, data_root, now, commit_sha=None, workflow_run_id=None, date=None):
    today = et_date_for_instant(now)
    date = date or resolve_export_date(data_root, today)
    inputs = load_inputs(data_root, date)
    bundle = build_bundle(inputs, now=now, commit_sha=commit_sha, workflow_run_id=workflow_run_id)
    manifest = publish.publish(
        root=out_root, sport=SPORT, run_id=bundle["run_id"], generated_at=bundle["generated_at"],
        documents=bundle["documents"], source_repo=SOURCE_REPO, source_branch=SOURCE_BRANCH, commit_sha=commit_sha,
        model_version=bundle["model_version"], status="SUCCESS", freshness=bundle["freshness"],
        warnings=bundle["warnings"], health=bundle["health"])
    return manifest, bundle


def write_failure_health(out_root, exc, *, now, commit_sha=None):
    previous = publish.read_manifest(out_root)
    prev_health = _read_json(os.path.join(out_root, publish.HEALTH_NAME)) or {}
    payload_run_id = (previous or {}).get("run_id")
    doc = health.build_health(
        sport=SPORT, run_id=payload_run_id or build.run(sport=SPORT, repo=SOURCE_REPO, completed_at=now, scope="export-failed")["run_id"],
        bet_authority=BET_AUTHORITY, last_market_capture=prev_health.get("last_market_capture"),
        last_model_generated=prev_health.get("last_model_generated"), last_successful_run=prev_health.get("last_successful_run"),
        payload_run_id=payload_run_id, payload_available=previous is not None, export_failed=True, commit_sha=commit_sha,
        model_required=MODEL_REQUIRED, thresholds=THRESHOLDS, warnings=[],
        errors=[f"{type(exc).__name__}: {exc}"], now=now)
    return publish.write_health_only(out_root, doc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="app root to publish into (e.g. app/latest)")
    ap.add_argument("--data-root", default=os.path.join(REPO_ROOT, "data"))
    ap.add_argument("--now", default=None, help="aware ISO timestamp; default: wall clock UTC")
    ap.add_argument("--commit-sha", default=None)
    ap.add_argument("--workflow-run-id", default=None)
    ap.add_argument("--date", default=None, help="force the slate date (default: newest slate at or before today ET)")
    args = ap.parse_args(argv)
    now = timeutil.parse_ts(args.now) if args.now else timeutil.now_utc()
    out_root = os.path.abspath(args.out)
    try:
        manifest, bundle = export(out_root=out_root, data_root=os.path.abspath(args.data_root), now=now,
                                  commit_sha=args.commit_sha, workflow_run_id=args.workflow_run_id, date=args.date)
    except Exception as exc:  # noqa: BLE001 -- the failure path must catch everything
        traceback.print_exc()
        try:
            path = write_failure_health(out_root, exc, now=now, commit_sha=args.commit_sha)
            print(f"app export FAILED; wrote {path} only (payload untouched)", file=sys.stderr)
        except Exception as inner:  # noqa: BLE001
            print(f"app export FAILED and health could not be written: {inner}", file=sys.stderr)
        return 1
    print(json.dumps({"run_id": manifest["run_id"], "date": bundle["date"], "counts": manifest["counts"],
                      "overall_status": bundle["health"]["overall_status"], "warnings": bundle["warnings"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
