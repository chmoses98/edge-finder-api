#!/usr/bin/env python3
"""
lib/bet_backlog_classifier.py
==================================
Production Reliability and Settlement Recovery milestone: pure,
deterministic classification of bets.json's non-terminal (pending/open)
records into one of eight categories, using ONLY evidence already
present in this repository -- no live network access, no guessed
settlement outcomes. See scripts/remediate_bet_backlog.py for the CLI
that wraps this module, and
docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md for the investigation
these categories and thresholds are drawn from.

Evidence used, and why each threshold was chosen (every constant below
is cited, not guessed):
  - CLV_WORKFLOW_CREATED (2026-06-10): the actual creation date of
    .github/workflows/clv-update.yml, confirmed via the GitHub Actions
    API's list_workflows response during this milestone's incident
    investigation. Any bet dated before this could never have been
    settled automatically -- the settlement mechanism did not exist yet.
  - LEGITIMATE_PENDING_WINDOW_DAYS (2): clv-update.yml's daily schedule
    processes "yesterday ET" by default (clv_update.py's own `main()`:
    `date = (et_now - timedelta(days=1))`), so a bet from today or
    yesterday has not necessarily had a settlement pass run against it
    yet at all -- that is not evidence of a problem.
  - KNOWN_FAILED_CLV_UPDATE_DATES: the specific dates independently
    confirmed, via this milestone's GitHub Actions run-log
    investigation, to have had a FAILED clv-update.yml run whose
    "yesterday ET" target was that date. This is deliberately a
    hardcoded, cited list rather than a live API call -- classification
    must be reproducible offline and deterministic.
"""
import collections
import json
import re
from datetime import date as _date, datetime, timedelta

CLV_WORKFLOW_CREATED = "2026-06-10"
LEGITIMATE_PENDING_WINDOW_DAYS = 2

# Dates whose "yesterday ET" clv-update.yml settlement run is independently
# confirmed (GitHub Actions run history, this milestone's incident
# investigation) to have FAILED outright -- see
# docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md. A bet dated one of these
# values was never actually processed by any successful automated run
# unless a later manual `workflow_dispatch --date=<date>` rerun happened
# (which this offline classifier has no way to observe, hence
# "pipeline_failure" rather than a stronger claim).
KNOWN_FAILED_CLV_UPDATE_DATES = frozenset({
    "2026-06-09",  # 2026-06-10T07:01Z run (push, first-ever run, failed)
    "2026-06-10",  # 2026-06-11T17:59Z run (schedule, failed)
    "2026-06-15",  # 2026-06-16T19:52Z run (schedule, failed -- git commit race, see incident doc)
    "2026-07-30",  # 2026-07-31T09:10Z run (schedule, failed -- same git commit race)
    "2026-07-31",  # 2026-08-01T08:29Z run (schedule, failed -- same git commit race)
})

# clv_update.py's own determine_result() permanently returns (None, None,
# None) for these two families and routes them to a hardcoded
# `nrfi_yrfi_manual` list -- there is no automated settlement path for
# NRFI/YRFI anywhere in production, confirmed at clv_update.py's main().
UNSUPPORTED_MARKET_FAMILIES = frozenset({"NRFI", "YRFI"})

TERMINAL_RESULTS = frozenset({"WIN", "LOSS", "PUSH", "VOID", "NO_ACTION"})

CATEGORY_LEGITIMATELY_PENDING = "legitimately_pending"
CATEGORY_SETTLEABLE_FROM_EVIDENCE = "settleable_from_evidence"
CATEGORY_MISSING_SOURCE_DATA = "missing_source_data"
CATEGORY_MALFORMED_RECORD = "malformed_record"
CATEGORY_DUPLICATE = "duplicate"
CATEGORY_UNSUPPORTED_MARKET_FAMILY = "unsupported_market_family"
CATEGORY_PIPELINE_FAILURE = "pipeline_failure"
CATEGORY_REQUIRES_MANUAL_REVIEW = "requires_manual_review"

ALL_CATEGORIES = (
    CATEGORY_LEGITIMATELY_PENDING,
    CATEGORY_SETTLEABLE_FROM_EVIDENCE,
    CATEGORY_MISSING_SOURCE_DATA,
    CATEGORY_MALFORMED_RECORD,
    CATEGORY_DUPLICATE,
    CATEGORY_UNSUPPORTED_MARKET_FAMILY,
    CATEGORY_PIPELINE_FAILURE,
    CATEGORY_REQUIRES_MANUAL_REVIEW,
)

_NRFI_YRFI_ALIASES = {"NRFI", "YRFI"}


def get_result(bet):
    """Mirrors clv_update.py's own get_result(): result wins over status."""
    return bet.get("result") or bet.get("status")


def is_non_terminal(bet):
    """True when this bet has not yet reached a real settlement outcome (mirrors clv_update.py's own skip check)."""
    result = get_result(bet)
    return (result or "").upper() not in TERMINAL_RESULTS


def canonical_market_family(market):
    """
    Coarse family classifier for the *settlement-support* question only
    (NOT a full market taxonomy -- lib.research.market_taxonomy already
    owns that, for a different purpose). Recognizes NRFI/YRFI under any
    of the naming conventions seen across this repo's several bet-schema
    eras (e.g. "NRFI", "NRFI_Away", "nrfi") without over-matching an
    unrelated market that merely contains those letters.
    """
    if not market:
        return None
    m = str(market).strip().upper()
    for alias in _NRFI_YRFI_ALIASES:
        if m == alias or m.startswith(alias + "_") or m.startswith(alias + " "):
            return alias
    return m


def parse_game_teams(game_str):
    """
    'AWAY @ HOME' / 'AWAY@HOME' -> (away, home) strings, or (None, None)
    when unparseable. Deliberately does NOT resolve to canonical team
    abbreviations (clv_update.py's to_abbr() does that, for a live
    settlement match) -- this classifier only needs to know whether the
    field is structurally parseable at all.
    """
    if not game_str or not isinstance(game_str, str):
        return None, None
    sep = " @ " if " @ " in game_str else ("@" if "@" in game_str else None)
    if sep is None:
        return None, None
    parts = game_str.split(sep, 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        return None, None
    return parts[0].strip(), parts[1].strip()


def _record_content_key(bet):
    """Every field except `id` -- see find_duplicates()'s docstring for why `id` is excluded."""
    return json.dumps({k: v for k, v in bet.items() if k != "id"}, sort_keys=True, default=str)


def find_duplicates(bets):
    """
    Returns the set of `id`s that are byte-for-byte content duplicates of
    an earlier record in `bets` (every field except `id` identical).
    Deliberately NOT keyed on (date, game, market, bet) alone -- this
    repo legitimately carries multiple independent bet TRANCHES on the
    same market (e.g. an automated pipeline bet and a separate manual
    session bet on the same ticker, confirmed in real 2026-06-19 data:
    different stake, different entry price/timestamp, different source)
    that must never be flagged as duplicates of each other.
    """
    seen = {}
    duplicate_ids = set()
    for bet in bets:
        key = _record_content_key(bet)
        if key in seen:
            duplicate_ids.add(bet.get("id"))
        else:
            seen[key] = bet.get("id")
    return duplicate_ids


def classify_bet(bet, today, duplicate_ids=None):
    """
    Returns one of ALL_CATEGORIES for a single non-terminal bet.
    `today` is an ISO YYYY-MM-DD string (the classification run date).
    """
    bet_id = bet.get("id")
    if duplicate_ids and bet_id in duplicate_ids:
        return CATEGORY_DUPLICATE

    bet_date = bet.get("date")
    game = bet.get("game")
    away, home = parse_game_teams(game)
    if not bet_date or not away or not home:
        return CATEGORY_MALFORMED_RECORD

    try:
        today_d = datetime.strptime(today, "%Y-%m-%d").date()
        bet_d = datetime.strptime(bet_date, "%Y-%m-%d").date()
    except ValueError:
        return CATEGORY_MALFORMED_RECORD

    if bet_d > today_d:
        return CATEGORY_MALFORMED_RECORD  # a bet dated in the future is a data error, never "pending"

    if (today_d - bet_d).days <= LEGITIMATE_PENDING_WINDOW_DAYS:
        return CATEGORY_LEGITIMATELY_PENDING

    family = canonical_market_family(bet.get("market"))
    if family in UNSUPPORTED_MARKET_FAMILIES:
        return CATEGORY_UNSUPPORTED_MARKET_FAMILY

    if bet_date < CLV_WORKFLOW_CREATED:
        return CATEGORY_MISSING_SOURCE_DATA

    if bet_date in KNOWN_FAILED_CLV_UPDATE_DATES:
        return CATEGORY_PIPELINE_FAILURE

    # settleable_from_evidence would go here if a local, already-committed
    # post-game score/settlement artifact existed for this game -- see
    # find_local_settlement_evidence() below. Never populated by guessing.
    evidence = find_local_settlement_evidence(bet)
    if evidence is not None:
        return CATEGORY_SETTLEABLE_FROM_EVIDENCE

    return CATEGORY_REQUIRES_MANUAL_REVIEW


def find_local_settlement_evidence(bet, settlement_index=None):
    """
    Looks for a real, already-committed local settlement/score record for
    this bet's game -- e.g. a lib.edgelab Settlement artifact, or a
    post-game score file, if one is ever added to this repo. Returns that
    evidence dict, or None (never a guess). `settlement_index` lets a
    caller inject a prebuilt lookup (keyed however the caller likes) for
    testing; the default (None) means "no local settlement archive is
    wired up yet" -- this repo currently has none (data/slates/ holds only
    PRE-game snapshots, confirmed during this milestone's investigation),
    so this always returns None in production today. This function exists
    so a future milestone that DOES add a post-game score archive only
    needs to populate `settlement_index`, not touch classify_bet() at all.
    """
    if not settlement_index:
        return None
    return settlement_index.get(bet.get("id"))


def build_plan(bets, today, date_from=None, date_to=None, settlement_index=None):
    """
    Classifies every non-terminal bet (optionally scoped to
    [date_from, date_to] inclusive) and returns the machine-readable
    plan dict scripts/remediate_bet_backlog.py writes to disk and prints
    a summary of. `autoSafeChanges` is deliberately empty in this
    milestone -- see the module docstring and
    docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md for why no financial
    field is ever auto-remediated without live settlement evidence.
    """
    duplicate_ids = find_duplicates(bets)

    considered = []
    for bet in bets:
        if not is_non_terminal(bet):
            continue
        bet_date = bet.get("date")
        if date_from and (not bet_date or bet_date < date_from):
            continue
        if date_to and (not bet_date or bet_date > date_to):
            continue
        considered.append(bet)

    counts = collections.Counter()
    by_category = collections.defaultdict(list)
    for bet in considered:
        category = classify_bet(bet, today, duplicate_ids=duplicate_ids)
        counts[category] += 1
        by_category[category].append({
            "id": bet.get("id"),
            "date": bet.get("date"),
            "game": bet.get("game"),
            "market": bet.get("market"),
        })

    # Recommended (human-actionable, not auto-executed) dates to rerun
    # `python3 clv_update.py <date>` for -- every distinct date behind a
    # pipeline_failure or requires_manual_review classification.
    rerun_candidate_dates = sorted({
        item["date"]
        for category in (CATEGORY_PIPELINE_FAILURE, CATEGORY_REQUIRES_MANUAL_REVIEW)
        for item in by_category.get(category, [])
        if item["date"]
    })

    return {
        "schemaVersion": "1",
        "totalBetsInLedger": len(bets),
        "totalConsidered": len(considered),
        "classificationCounts": dict(counts),
        "classificationDetail": {k: v for k, v in by_category.items()},
        "autoSafeChanges": [],  # see module docstring -- never populated by guessing a financial outcome
        "recommendedManualRerunDates": rerun_candidate_dates,
        "dateFrom": date_from,
        "dateTo": date_to,
    }
