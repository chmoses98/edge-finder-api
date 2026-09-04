#!/usr/bin/env python3
"""
scripts/edgelab/run_night_before_timing_research.py
===================================================
RESEARCH ONLY. Builds every reproducible artifact behind
docs/EDGELAB_MLB_NIGHT_BEFORE_TIMING_RESEARCH_2026_09.md.

Reads only the immutable, already-archived EdgeLab corpus. Writes only
under data/edgelab/research_artifacts/night_before_timing/. Changes NO
production behaviour: no probability, recommendation, edge, confidence,
Bet Up To, stake, bankroll, market-eligibility, lineup-gate, slate,
settlement or cron behaviour is touched, and nothing this script emits is
consumed by any production code path.

Stages (all run by default, `--stage` to run one):

  coverage   How much night-before evidence actually exists, and of what
             provenance. Must be read before any profitability number.
  dataset    Links each contract's own executable quotes across research
             horizons, through settlement.
  analysis   Price movement, CLV, realized ROI, per-family and per-window
             results, liquidity, lineup risk, walk-forward, policies A-E.

Determinism: every stage is a pure function of the committed corpus plus
the flags below. No wall-clock branching, no network, no randomness other
than a seeded bootstrap whose seed is recorded in the artifact.
"""
import argparse
import collections
import csv
import glob
import gzip
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from lib.edgelab.research import night_before_timing as nbt  # noqa: E402

OBSERVATIONS_DIR = os.path.join(ROOT_DIR, "data", "edgelab", "observations")
SETTLEMENTS_DIR = os.path.join(ROOT_DIR, "data", "edgelab", "settlements")
EVALUATIONS_DIR = os.path.join(ROOT_DIR, "data", "edgelab", "model_evaluations")
RAW_SNAPSHOT_DIR = os.path.join(ROOT_DIR, "data", "kalshi_registry_snapshots")
LINEUP_AUDIT_GLOB = os.path.join(ROOT_DIR, "data", "lineup_audit_*.json")
ARTIFACT_DIR = os.path.join(
    ROOT_DIR, "data", "edgelab", "research_artifacts", "night_before_timing"
)

BOOTSTRAP_SEED = 20260904
BOOTSTRAP_ITERATIONS = 2000

# The seven player-prop families. These are NOT excluded from realized ROI.
#
# CORRECTION (this revision): an earlier pass of this study held props out of
# every ROI conclusion on the belief that their settlement was still pending
# GitHub issue #43. That was stale. Issue #43 is closed; PR #44 (squash
# c709b0a890e1a876c883451d36e71d9d767bf823) added automatic settlement for all
# seven captured prop families via lib/edgelab/player_prop_settlement.py, and
# lib/edgelab/settlement.py's settle_market_full() routes them there. Audited
# directly against the committed settlement corpus: 97,423 prop contracts carry
# settlementStatus=SETTLED, and every one of them carries settlementEvidence.
#
# The label is retained only so prop and non-prop populations can be REPORTED
# separately (they behave differently and must not be pooled into one headline),
# never to drop them. Unresolved props are excluded individually, by their own
# recorded unavailableReason, exactly like an unresolved game market.
PROP_FAMILIES = frozenset({
    "hitter_hits", "hitter_total_bases", "hitter_rbis",
    "hitter_stolen_bases", "hitter_hits_runs_rbis",
    "pitcher_strikeouts", "pitcher_outs",
})


# Spec requirement 6 asks for full-game moneyline, run line, game totals, F3,
# F5 moneyline, F5 spread, F5 totals, team totals and NRFI/YRFI to be tested
# SEPARATELY. The corpus's own `marketFamily` field cannot express that: via
# lib/edgelab/market_family_mapping.py it collapses KXMLBF3 + KXMLBF5 +
# KXMLBF7 all into `inning_result`, and KXMLBSPREAD (full-game run line) +
# KXMLBF5SPREAD into `winning_margin`. Pooling a 3-inning market with a
# 5-inning one, or a full-game run line with an F5 spread, is exactly the
# pooling the spec forbids.
#
# The Kalshi series ticker is the honest discriminator that IS present on
# 100% of rows, so this research-only vocabulary splits on it. It does NOT
# modify market_family_mapping.py -- production's canonical 17-family
# vocabulary keeps its meaning, and every prior report that cites it stays
# valid. This is an additive research label, like the timing horizons.
RESEARCH_SUB_FAMILY_BY_SERIES = {
    "KXMLBGAME": "full_game_moneyline",
    "KXMLBSPREAD": "full_game_run_line",
    "KXMLBTOTAL": "full_game_total",
    "KXMLBTEAMTOTAL": "team_total",
    "KXMLBRFI": "first_inning_run_nrfi_yrfi",
    "KXMLBF3": "f3_result",
    "KXMLBF5": "f5_moneyline",
    "KXMLBF7": "f7_result",
    "KXMLBF5SPREAD": "f5_spread",
    "KXMLBF5TOTAL": "f5_total",
    "KXMLBKS": "pitcher_strikeouts",
    "KXMLBOUTS": "pitcher_outs",
    "KXMLBHIT": "hitter_hits",
    "KXMLBTB": "hitter_total_bases",
    "KXMLBHRR": "hitter_hits_runs_rbis",
    "KXMLBRBI": "hitter_rbis",
    "KXMLBSB": "hitter_stolen_bases",
}


def research_sub_family(series_ticker, market_family):
    """
    Research-only sub-family. Falls back to the corpus's own family name
    prefixed `UNSPLIT_` for any series ticker not yet enumerated -- never a
    silent guess, and visible in the emitted tables so a new Kalshi series
    shows up as unmapped rather than being folded into a neighbour.
    """
    mapped = RESEARCH_SUB_FAMILY_BY_SERIES.get(series_ticker)
    if mapped:
        return mapped
    return f"UNSPLIT_{market_family}"


# ===========================================================================
# IO helpers
# ===========================================================================

def _open_maybe_gzip(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path, "rt")


def _read_jsonl_dir(directory):
    for path in sorted(glob.glob(os.path.join(directory, "*.jsonl*"))):
        with _open_maybe_gzip(path) as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield json.loads(line)


def _write_json(name, payload):
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    path = os.path.join(ARTIFACT_DIR, name)
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str)
        handle.write("\n")
    return path


def _write_csv(name, rows, fieldnames):
    """Writes gzipped when `name` ends in .gz, so one large table does not
    bloat the repository while every small summary stays plain-text
    reviewable in a diff."""
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    path = os.path.join(ARTIFACT_DIR, name)
    opener = (lambda p: gzip.open(p, "wt", newline="")) if name.endswith(".gz") \
        else (lambda p: open(p, "w", newline=""))
    with opener(path) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def _git_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, text=True
        ).strip()
    except Exception:
        return None


def _provenance(stage):
    return {
        "stage": stage,
        "generatedByScript": "scripts/edgelab/run_night_before_timing_research.py",
        "sourceGitCommitSha": _git_sha(),
        "productionBehaviorChanged": False,
        "researchOnly": True,
    }


# ===========================================================================
# Statistics (small, self-contained, seeded)
# ===========================================================================

def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def clustered_bootstrap_mean_ci(values_by_cluster, iterations=BOOTSTRAP_ITERATIONS,
                                seed=BOOTSTRAP_SEED, alpha=0.05):
    """
    95% CI for the mean, resampling CLUSTERS (games/dates) rather than rows.

    Contracts on the same game share a single outcome and a single price
    shock, so row-level resampling would badly understate uncertainty --
    the same reason lib/edgelab/research_stats.py clusters. This is a
    deliberately small local implementation because this study's cluster
    key (game, or slate date for the stability analyses) varies per table.
    """
    clusters = [v for v in values_by_cluster.values() if v]
    if len(clusters) < 2:
        return None, None, len(clusters)
    rng = random.Random(seed)
    flat = [x for cluster in clusters for x in cluster]
    if not flat:
        return None, None, len(clusters)
    means = []
    n = len(clusters)
    for _ in range(iterations):
        pool = []
        for _ in range(n):
            pool.extend(clusters[rng.randrange(n)])
        if pool:
            means.append(sum(pool) / len(pool))
    if not means:
        return None, None, len(clusters)
    means.sort()
    lo = means[int(alpha / 2 * len(means))]
    hi = means[min(len(means) - 1, int((1 - alpha / 2) * len(means)))]
    return lo, hi, n


def benjamini_hochberg(pvalues, alpha=0.05):
    """Returns the set of indices rejected at FDR `alpha`."""
    indexed = sorted((p, i) for i, p in enumerate(pvalues) if p is not None)
    rejected = set()
    m = len(indexed)
    for rank, (p, idx) in enumerate(indexed, start=1):
        if p <= alpha * rank / m:
            rejected = {i for _, i in indexed[:rank]}
    return rejected


def sign_test_p_value(wins, losses):
    """
    Two-sided binomial p-value against p=0.5 for the paired sign of a
    price move ("did the price move for or against the early bettor more
    often than a coin flip").

    Exact for small n; a continuity-corrected normal approximation above
    `_EXACT_SIGN_TEST_MAX_N`, where the exact tail overflows a float and
    the approximation is in any case accurate to far more digits than the
    Benjamini-Hochberg step downstream needs.

    NOTE these rows are NOT independent -- several contracts share one
    game. This p-value is reported only as a rough ranking statistic for
    the multiple-testing step; every effect-size claim in the report is
    carried by the game-clustered bootstrap CI instead.
    """
    n = wins + losses
    if n == 0:
        return None
    observed = min(wins, losses)
    if n <= _EXACT_SIGN_TEST_MAX_N:
        from math import comb
        tail = sum(comb(n, k) for k in range(0, observed + 1))
        return min(1.0, 2.0 * tail / (2.0 ** n))
    from math import erfc, sqrt
    z = (abs(observed + 0.5 - n / 2.0)) / (0.5 * sqrt(n))
    return min(1.0, erfc(z / sqrt(2.0)))


_EXACT_SIGN_TEST_MAX_N = 900


# ===========================================================================
# STAGE 1 -- COVERAGE
# ===========================================================================

# Capture filenames in data/kalshi_registry_snapshots/ come in four real
# shapes, and the audit must cover all of them:
#
#   kalshi_search_<date>.json                      -- the "latest for this date" copy
#                                                     the scheduled workflow overwrites
#                                                     on every run
#   kalshi_search_<date>_<HHMM>.json               -- a timestamped scheduled capture
#   kalshi_search_<date>_<HHMMSS>_standalone.json  -- an UNFILTERED research capture
#                                                     written by the standalone price
#                                                     checker
#   kalshi_search_<date>_recheck_<HHMM>.json       -- a lineup-recheck capture
#
# CORRECTION (this revision): an earlier pass matched only the first two shapes
# and therefore silently dropped 39 files carrying 101,223 market rows -- and
# those were precisely the UNFILTERED standalone captures, the ones most likely
# to contain an out-of-slate market if any capture ever did. Dropping them
# weakened the study's central "no next-day market was ever captured" claim by
# never looking at the most promising evidence. They are included now, and the
# report breaks the population down by capture kind instead of quoting one
# blended total.
_RAW_FILENAME = re.compile(
    r"kalshi_search_(\d{4}-\d{2}-\d{2})"
    r"(?:_(?P<hhmm>\d{4})"
    r"|_(?P<hhmmss>\d{6})_standalone"
    r"|_recheck_(?P<recheck>\d{4}))?"
    r"\.json$"
)


def _capture_kind(filename_match):
    """Which capture pathway wrote this file, from its filename shape alone."""
    if filename_match.group("hhmmss"):
        return "STANDALONE_RESEARCH_CAPTURE"
    if filename_match.group("recheck"):
        return "LINEUP_RECHECK_CAPTURE"
    if filename_match.group("hhmm"):
        return "SCHEDULED_TIMESTAMPED_CAPTURE"
    return "SCHEDULED_LATEST_FOR_DATE_COPY"


def scan_raw_archive():
    """
    Audits the unfiltered capture archive that feeds everything else --
    including the question the study cannot answer any other way: were
    next-calendar-day markets ever fetched at all?
    """
    files = sorted(glob.glob(os.path.join(RAW_SNAPSHOT_DIR, "kalshi_search_*.json")))
    per_slate = collections.defaultdict(lambda: {
        "files": 0, "timestampedFiles": 0, "marketRows": 0,
        "distinctEvents": set(), "maxLeadHours": None,
        "earliestFetchEt": None, "latestFetchEt": None,
    })
    cross_date_rows = 0
    cross_date_examples = []
    fetch_day_offsets = collections.Counter()
    fetch_hour_et = collections.Counter()
    lead_buckets = collections.Counter()
    unparsed_tickers = collections.Counter()
    total_rows = 0
    files_by_kind = collections.Counter()
    rows_by_kind = collections.Counter()
    cross_date_rows_by_kind = collections.Counter()
    unmatched_filenames = []
    # The scheduled workflow writes each capture TWICE -- once timestamped and
    # once as the "latest for this date" copy -- so the two files share an
    # identical (date, fetched_at). Counting both would inflate every raw-row
    # total by roughly one capture per slate date. Deduplicated here, and the
    # number removed is reported rather than hidden.
    seen_capture_identities = set()
    duplicate_files_skipped = 0
    duplicate_rows_skipped = 0

    for path in files:
        name = os.path.basename(path)
        match = _RAW_FILENAME.match(name)
        if not match:
            unmatched_filenames.append(name)
            continue
        try:
            with open(path) as handle:
                payload = json.load(handle)
        except Exception:
            continue
        slate_date = payload.get("date")
        fetched_at = nbt.parse_timestamp(payload.get("fetched_at"))
        if not slate_date or fetched_at is None:
            continue
        kind = _capture_kind(match)
        markets = payload.get("markets") or []
        identity = (slate_date, payload.get("fetched_at"))
        if identity in seen_capture_identities:
            duplicate_files_skipped += 1
            duplicate_rows_skipped += len(markets)
            continue
        seen_capture_identities.add(identity)
        files_by_kind[kind] += 1
        fetched_et = fetched_at.astimezone(nbt.EASTERN)
        bucket = per_slate[slate_date]
        bucket["files"] += 1
        if kind != "SCHEDULED_LATEST_FOR_DATE_COPY":
            bucket["timestampedFiles"] += 1
        fetch_hour_et[fetched_et.hour] += 1
        offset = (fetched_et.date() - datetime.strptime(slate_date, "%Y-%m-%d").date()).days
        fetch_day_offsets[offset] += 1
        iso = fetched_et.isoformat()
        if bucket["earliestFetchEt"] is None or iso < bucket["earliestFetchEt"]:
            bucket["earliestFetchEt"] = iso
        if bucket["latestFetchEt"] is None or iso > bucket["latestFetchEt"]:
            bucket["latestFetchEt"] = iso

        for market in markets:
            event_ticker = market.get("event_ticker") or ""
            start = nbt.scheduled_start_from_event_ticker(event_ticker)
            if start is None:
                unparsed_tickers[event_ticker.split("-")[0]] += 1
                continue
            total_rows += 1
            rows_by_kind[kind] += 1
            bucket["marketRows"] += 1
            bucket["distinctEvents"].add(event_ticker)
            game_date = start.astimezone(nbt.EASTERN).date().isoformat()
            if game_date != slate_date:
                cross_date_rows += 1
                cross_date_rows_by_kind[kind] += 1
                if len(cross_date_examples) < 20:
                    cross_date_examples.append({
                        "file": name, "captureKind": kind, "slateDate": slate_date,
                        "gameDate": game_date, "eventTicker": event_ticker,
                    })
            lead = (start - fetched_at).total_seconds() / 3600.0
            lead_buckets[nbt.classify_lead_time_horizon(lead)] += 1
            if bucket["maxLeadHours"] is None or lead > bucket["maxLeadHours"]:
                bucket["maxLeadHours"] = lead

    per_slate_rows = []
    for slate_date in sorted(per_slate):
        bucket = per_slate[slate_date]
        per_slate_rows.append({
            "slateDate": slate_date,
            "captureFiles": bucket["files"],
            "timestampedCaptureFiles": bucket["timestampedFiles"],
            "marketRows": bucket["marketRows"],
            "distinctEvents": len(bucket["distinctEvents"]),
            "maxLeadHours": round(bucket["maxLeadHours"], 3) if bucket["maxLeadHours"] is not None else None,
            "earliestFetchEt": bucket["earliestFetchEt"],
            "latestFetchEt": bucket["latestFetchEt"],
        })

    return {
        "archiveFilesOnDisk": len(files),
        "archiveFilesAudited": sum(files_by_kind.values()),
        "filesByCaptureKind": dict(files_by_kind),
        "marketRowsByCaptureKind": dict(rows_by_kind),
        "filenamesNotMatchingAnyKnownCaptureShape": unmatched_filenames,
        "duplicateCaptureFilesSkipped": duplicate_files_skipped,
        "duplicateMarketRowsSkipped": duplicate_rows_skipped,
        "populationDefinitions": {
            "archiveFilesOnDisk": "every kalshi_search_*.json in the snapshot directory",
            "archiveFilesAudited": (
                "files on disk, minus any whose filename matches no known capture "
                "shape, minus exact duplicates sharing one (slateDate, fetched_at)"
            ),
            "parsedMarketRows": (
                "market rows in the AUDITED files whose event ticker carries a "
                "resolvable date/time stamp"
            ),
        },
        "parsedMarketRows": total_rows,
        "slateDatesCovered": len(per_slate_rows),
        "crossCalendarDateMarketRows": cross_date_rows,
        "crossCalendarDateMarketRowsByCaptureKind": dict(cross_date_rows_by_kind),
        "crossCalendarDateExamples": cross_date_examples,
        "captureFetchDayOffsetCounts": {str(k): v for k, v in sorted(fetch_day_offsets.items())},
        "captureFetchHourEtCounts": {f"{h:02d}": fetch_hour_et.get(h, 0) for h in range(24)},
        "leadTimeHorizonCounts": {k: lead_buckets.get(k, 0) for k in nbt.LEAD_TIME_HORIZON_ORDER},
        "unparsedTickerPrefixes": dict(unparsed_tickers.most_common(10)),
        "perSlateDate": per_slate_rows,
    }


def load_observations():
    """
    Every archived market observation, de-duplicated on
    (marketTicker, capturedAt) and enriched with this study's three
    research axes. Returns (timelines_by_ticker, audit).
    """
    seen = set()
    timelines = collections.defaultdict(list)
    audit = {
        "rawRows": 0,
        "duplicateRowsDropped": 0,
        "rowsWithoutParseableStart": 0,
        "rowsWithAuthoritativeScheduledStart": 0,
        "scheduledStartAgreementChecked": 0,
        "scheduledStartAgreementExactMatches": 0,
        "scheduledStartDisagreementExamples": [],
        "leadTimeHorizonCounts": collections.Counter(),
        "calendarContextCounts": collections.Counter(),
        "bookUsabilityCounts": collections.Counter(),
        "noSideDirectlyCapturedRows": 0,
        "fieldNonNullCounts": collections.Counter(),
    }

    for row in _read_jsonl_dir(OBSERVATIONS_DIR):
        audit["rawRows"] += 1
        for key, value in row.items():
            if value is not None and value != "":
                audit["fieldNonNullCounts"][key] += 1
        if row.get("noBid") is not None or row.get("noAsk") is not None:
            audit["noSideDirectlyCapturedRows"] += 1

        ticker = row.get("marketTicker")
        captured_at = row.get("capturedAt")
        if not ticker or not captured_at:
            continue
        dedup_key = (ticker, captured_at)
        if dedup_key in seen:
            audit["duplicateRowsDropped"] += 1
            continue
        seen.add(dedup_key)

        start = nbt.scheduled_start_from_event_ticker(row.get("eventTicker"))
        if start is None:
            audit["rowsWithoutParseableStart"] += 1
            continue

        authoritative = row.get("scheduledStart")
        if authoritative:
            audit["rowsWithAuthoritativeScheduledStart"] += 1
            parsed = nbt.parse_timestamp(authoritative)
            if parsed is not None:
                audit["scheduledStartAgreementChecked"] += 1
                delta = abs((parsed - start).total_seconds())
                if delta < 60:
                    audit["scheduledStartAgreementExactMatches"] += 1
                elif len(audit["scheduledStartDisagreementExamples"]) < 20:
                    audit["scheduledStartDisagreementExamples"].append({
                        "marketTicker": ticker,
                        "reconstructed": start.isoformat(),
                        "authoritative": authoritative,
                        "deltaSeconds": delta,
                    })

        lead = nbt.hours_to_first_pitch(captured_at, start)
        horizon = nbt.classify_lead_time_horizon(lead)
        calendar = nbt.classify_calendar_context(captured_at, start)
        usability, spread = nbt.book_usability(row)
        audit["leadTimeHorizonCounts"][horizon] += 1
        audit["calendarContextCounts"][calendar] += 1
        audit["bookUsabilityCounts"][usability] += 1

        timelines[ticker].append({
            "marketTicker": ticker,
            "eventTicker": row.get("eventTicker"),
            "physicalGameKey": nbt.physical_game_key(row.get("eventTicker")),
            "mlbGamePk": (str(row["mlbGameId"]) if row.get("mlbGameId") else None),
            "gameId": row.get("gameId"),
            "marketFamily": row.get("marketFamily"),
            "seriesTicker": row.get("seriesTicker"),
            "capturedAt": captured_at,
            "scheduledStart": start.isoformat(),
            "slateDate": start.astimezone(nbt.EASTERN).date().isoformat(),
            "hoursBeforeStart": lead,
            "leadTimeHorizon": horizon,
            "calendarContext": calendar,
            "yesBid": row.get("yesBid"),
            "yesAsk": row.get("yesAsk"),
            "spreadCents": spread,
            "bookUsability": usability,
            "volume": row.get("volume"),
            "openInterest": row.get("openInterest"),
            "lastPrice": row.get("lastPrice"),
            "productionCheckpoint": row.get("checkpoint"),
        })

    for ticker in timelines:
        timelines[ticker].sort(key=lambda r: r["capturedAt"])
        for row, flag in zip(timelines[ticker], nbt.stale_flags(timelines[ticker])):
            row["stalenessFlag"] = flag

    audit["leadTimeHorizonCounts"] = dict(audit["leadTimeHorizonCounts"])
    audit["calendarContextCounts"] = dict(audit["calendarContextCounts"])
    audit["bookUsabilityCounts"] = dict(audit["bookUsabilityCounts"])
    audit["fieldNonNullCounts"] = dict(audit["fieldNonNullCounts"])
    audit["distinctMarketTickers"] = len(timelines)
    return timelines, audit


def load_settlements():
    """
    marketTicker -> {'result': 'YES'|'NO', ...}. A ticker whose repeated
    settlement rows disagree is dropped entirely and counted, never
    resolved by picking one -- a contract we cannot say settled cleanly is
    not evidence about anything.
    """
    by_ticker = {}
    conflicts = set()
    unresolved = 0
    unresolved_by_family = collections.defaultdict(collections.Counter)
    settled_by_family = collections.Counter()
    prop_evidence = collections.Counter()
    for row in _read_jsonl_dir(SETTLEMENTS_DIR):
        ticker = row.get("marketTicker")
        if not ticker:
            continue
        family = row.get("marketFamily")
        if row.get("settlementStatus") != "SETTLED" or row.get("result") not in ("YES", "NO"):
            unresolved += 1
            unresolved_by_family[family][
                row.get("unavailableReason") or row.get("settlementStatus") or "UNKNOWN"
            ] += 1
            continue
        settled_by_family[family] += 1
        if family in PROP_FAMILIES:
            prop_evidence["withEvidence" if row.get("settlementEvidence")
                          else "withoutEvidence"] += 1
        result = row["result"]
        if ticker in by_ticker and by_ticker[ticker]["result"] != result:
            conflicts.add(ticker)
        by_ticker[ticker] = {"result": result, "marketFamily": row.get("marketFamily")}
    for ticker in conflicts:
        by_ticker.pop(ticker, None)
    return by_ticker, {
        "settledTickers": len(by_ticker),
        "unresolvedSettlementRows": unresolved,
        "conflictingTickersDropped": len(conflicts),
        "settledRowsByFamily": dict(settled_by_family),
        "unresolvedReasonsByFamily": {
            family: dict(reasons) for family, reasons in sorted(unresolved_by_family.items())
        },
        "propSettlementEvidence": dict(prop_evidence),
        "playerPropSettlementNote": (
            "GitHub issue #43 is CLOSED. PR #44 (squash "
            "c709b0a890e1a876c883451d36e71d9d767bf823) added automatic settlement "
            "for all seven captured prop families; lib/edgelab/settlement.py's "
            "settle_market_full() routes them to "
            "lib/edgelab/player_prop_settlement.py. Settled props are therefore "
            "INCLUDED in realized ROI. Unresolved props are excluded individually "
            "by their own recorded unavailableReason, never as a class."
        ),
    }


def load_lineup_confirmation_times():
    """
    physicalGameKey -> earliest observed LINEUP_CONFIRMATION capture time.

    Sourced from ModelEvaluation rows, the only store in this repository
    that timestamps the moment production saw a confirmed lineup. The
    market-observation archive carries `lineupConfirmationState` on 0 of
    473,130 rows, so the price archive alone cannot locate this moment.

    KEYED ON THE PHYSICAL GAME, NOT THE EVENT TICKER. A lineup is confirmed
    for a BASEBALL GAME, not for a Kalshi series -- the moment the Yankees
    post their card, it is known to the moneyline, the total, the run line
    and every player prop simultaneously. The ModelEvaluation rows that
    carry this checkpoint happen to come from a handful of series
    (KXMLBRFI, KXMLBGAME, ...), so keying on their raw eventTicker made the
    confirmation time reachable ONLY by markets in those same series: every
    player prop silently lost its Policy D entry point, and
    `balanced_player_props` came out as literally 0 contracts. Keying on the
    physical game propagates the timestamp to every market on that game,
    which is what the underlying fact actually means.
    """
    earliest = {}
    for row in _read_jsonl_dir(EVALUATIONS_DIR):
        if row.get("checkpoint") != "LINEUP_CONFIRMATION":
            continue
        game_key = nbt.physical_game_key(row.get("eventTicker"))
        stamp = (row.get("provenance") or {}).get("capturedAt") or row.get("createdAt")
        if not game_key or not stamp:
            continue
        if game_key not in earliest or stamp < earliest[game_key]:
            earliest[game_key] = stamp
    return earliest


def audit_game_identity(timelines):
    """
    Establishes and VALIDATES the canonical physical-game key, and reports the
    four population counts the study must never conflate: physical MLB games,
    Kalshi event tickers, market-series events, and contracts.

    Re-validates on every run that the event-ticker suffix is 1:1 with both
    other identifiers in the corpus. If a future corpus breaks that property
    the conclusion flips to REVIEW_REQUIRED rather than silently
    mis-clustering.
    """
    suffix_to_pk = collections.defaultdict(set)
    pk_to_suffix = collections.defaultdict(set)
    suffix_to_dated = collections.defaultdict(set)
    suffix_to_series = collections.defaultdict(set)
    event_tickers = set()
    series_events = set()
    raw_game_ids = set()
    unresolved_key_rows = 0

    for ticker, rows in timelines.items():
        for row in rows:
            key = row["physicalGameKey"]
            if key is None:
                unresolved_key_rows += 1
                continue
            event_tickers.add(row["eventTicker"])
            series_events.add((row["seriesTicker"], key))
            suffix_to_series[key].add(row["seriesTicker"])
            if row.get("mlbGamePk"):
                suffix_to_pk[key].add(row["mlbGamePk"])
                pk_to_suffix[row["mlbGamePk"]].add(key)
            game_id = row.get("gameId")
            if game_id:
                raw_game_ids.add(game_id)
                if str(game_id).startswith("20"):
                    suffix_to_dated[key].add(game_id)

    pk_conflicts = {k: sorted(v) for k, v in suffix_to_pk.items() if len(v) > 1}
    suffix_conflicts = {k: sorted(v) for k, v in pk_to_suffix.items() if len(v) > 1}
    dated_conflicts = {k: sorted(v) for k, v in suffix_to_dated.items() if len(v) > 1}
    clean = not (pk_conflicts or suffix_conflicts or dated_conflicts)

    return {
        "distinctPhysicalMlbGames": len(suffix_to_series),
        "distinctKalshiEventTickers": len(event_tickers),
        "distinctMarketSeriesEvents": len(series_events),
        "distinctContracts": len(timelines),
        "distinctRawGameIdValues": len(raw_game_ids),
        "distinctMlbGamePks": len(pk_to_suffix),
        "physicalGamesCarryingAnMlbGamePk": len(suffix_to_pk),
        "physicalGamesCarryingADatedGameId": len(suffix_to_dated),
        "rowsWithUnresolvablePhysicalGameKey": unresolved_key_rows,
        "meanEventTickersPerPhysicalGame": (
            round(len(event_tickers) / len(suffix_to_series), 3) if suffix_to_series else None),
        "physicalGamesSpanningMoreThanOneSeries": sum(
            1 for v in suffix_to_series.values() if len(v) > 1),
        "validation": {
            "suffixesMappingToMoreThanOneGamePk": len(pk_conflicts),
            "gamePksMappingToMoreThanOneSuffix": len(suffix_conflicts),
            "suffixesMappingToMoreThanOneDatedGameId": len(dated_conflicts),
            "examples": dict(list(pk_conflicts.items())[:5]
                             + list(suffix_conflicts.items())[:5]
                             + list(dated_conflicts.items())[:5]),
            "conclusion": "VALIDATED_ONE_TO_ONE" if clean else "REVIEW_REQUIRED",
        },
        "whyNotEventTicker": (
            "One physical MLB game is priced by ~17 Kalshi series, each with its own "
            "event ticker, so clustering on eventTicker treats one game as many "
            "independent observations and understates uncertainty."
        ),
        "whyNotRawGameId": (
            "The corpus's gameId field carries two incompatible formats -- a dated "
            "string on game markets and a bare MLB gamePk on player props -- so the "
            "same physical game appears under two different values."
        ),
    }


def stage_coverage():
    raw = scan_raw_archive()
    timelines, obs_audit = load_observations()
    settlements, settle_audit = load_settlements()
    lineup_times = load_lineup_confirmation_times()

    identity = audit_game_identity(timelines)
    slate_dates = collections.Counter()
    family_horizon = collections.defaultdict(collections.Counter)
    per_date = collections.defaultdict(lambda: {
        "events": set(), "tickers": set(), "observations": 0,
        "tickersWithEarly12h": set(), "tickersWithEarly18h": set(),
        "tickersWithPrevEvening": set(), "tickersWithOvernight": set(),
    })
    liquidity_by_horizon = collections.defaultdict(lambda: {
        "spreads": [], "volumes": [], "openInterest": [], "n": 0, "stale": 0,
    })

    for ticker, rows in timelines.items():
        family = rows[0]["marketFamily"]
        slate = rows[0]["slateDate"]
        slate_dates[slate] += 1
        bucket = per_date[slate]
        bucket["tickers"].add(ticker)
        bucket["events"].add(rows[0]["physicalGameKey"])
        bucket["observations"] += len(rows)
        for row in rows:
            family_horizon[family][row["leadTimeHorizon"]] += 1
            lead = row["hoursBeforeStart"]
            if lead is not None and lead >= 12:
                bucket["tickersWithEarly12h"].add(ticker)
            if lead is not None and lead >= 18:
                bucket["tickersWithEarly18h"].add(ticker)
            if row["calendarContext"] == nbt.CALENDAR_PREVIOUS_EVENING:
                bucket["tickersWithPrevEvening"].add(ticker)
            if row["calendarContext"] == nbt.CALENDAR_OVERNIGHT:
                bucket["tickersWithOvernight"].add(ticker)
            if row["bookUsability"] == nbt.USABLE:
                stats = liquidity_by_horizon[row["leadTimeHorizon"]]
                stats["n"] += 1
                stats["spreads"].append(row["spreadCents"])
                if row["volume"] is not None:
                    stats["volumes"].append(row["volume"])
                if row["openInterest"] is not None:
                    stats["openInterest"].append(row["openInterest"])
                if row.get("stalenessFlag") == nbt.STALE_REPEATED_BOOK:
                    stats["stale"] += 1

    coverage_rows = []
    for slate in sorted(per_date):
        bucket = per_date[slate]
        coverage_rows.append({
            "slateDate": slate,
            "distinctPhysicalGames": len(bucket["events"]),
            "distinctContracts": len(bucket["tickers"]),
            "observations": bucket["observations"],
            "contractsWithQuoteAt12hPlus": len(bucket["tickersWithEarly12h"]),
            "contractsWithQuoteAt18hPlus": len(bucket["tickersWithEarly18h"]),
            "contractsWithPreviousEveningQuote": len(bucket["tickersWithPrevEvening"]),
            "contractsWithOvernightQuote": len(bucket["tickersWithOvernight"]),
        })

    family_rows = []
    for family in sorted(family_horizon):
        row = {"marketFamily": family}
        row.update({h: family_horizon[family].get(h, 0) for h in nbt.LEAD_TIME_HORIZON_ORDER})
        row["total"] = sum(family_horizon[family].values())
        family_rows.append(row)

    liquidity_rows = []
    for horizon in nbt.LEAD_TIME_HORIZON_ORDER:
        stats = liquidity_by_horizon.get(horizon)
        if not stats or not stats["n"]:
            continue
        liquidity_rows.append({
            "leadTimeHorizon": horizon,
            "usableObservations": stats["n"],
            "medianSpreadCents": _median(stats["spreads"]),
            "meanSpreadCents": round(_mean(stats["spreads"]), 4),
            "medianVolume": _median(stats["volumes"]),
            "medianOpenInterest": _median(stats["openInterest"]),
            "repeatedBookFlaggedPct": round(100.0 * stats["stale"] / stats["n"], 2),
        })

    agreement = obs_audit["scheduledStartAgreementChecked"]
    matches = obs_audit["scheduledStartAgreementExactMatches"]
    payload = {
        "provenance": _provenance("coverage"),
        "evidenceClassification": {
            "ORIGINAL_EVIDENCE": [
                "Archived Kalshi top-of-book quotes (yesBid/yesAsk/lastPrice/volume/openInterest)",
                "Capture timestamps (capturedAt / fetched_at)",
                "Settlement outcomes for auto-settled families",
                "LINEUP_CONFIRMATION capture timestamps from ModelEvaluation rows",
            ],
            "DETERMINISTIC_RECONSTRUCTION": [
                "Scheduled first pitch, decoded from the Kalshi event ticker's embedded "
                "ET date/time (validated below against the corpus's own scheduledStart field)",
                "Executable NO ask, derived as 100 - yesBid via the Kalshi binary identity",
            ],
            "UNAVAILABLE_HISTORICALLY": [
                "Directly captured NO-side quotes (0 rows in the entire archive)",
                "Order-book depth beyond top of book (never captured)",
                "Suspended/inactive market states (every archived row is status=active)",
                "Any market for a NEXT calendar day (never fetched -- see crossCalendarDateMarketRows)",
                "Per-quote exchange timestamps (only our own capture time exists)",
            ],
        },
        "rawArchive": raw,
        "observationStore": {
            k: v for k, v in obs_audit.items()
            if k != "scheduledStartDisagreementExamples"
        },
        "scheduledStartReconstructionValidation": {
            "checkedObservations": agreement,
            "exactMatchesWithinOneMinute": matches,
            "agreementPct": round(100.0 * matches / agreement, 4) if agreement else None,
            "disagreementExamples": obs_audit["scheduledStartDisagreementExamples"],
            "conclusion": (
                "VALIDATED" if agreement and matches == agreement else "REVIEW_REQUIRED"
            ),
        },
        "settlements": settle_audit,
        "lineupConfirmationTimestamps": {
            "distinctPhysicalGamesWithConfirmationTime": len(lineup_times),
            "source": "data/edgelab/model_evaluations (checkpoint=LINEUP_CONFIRMATION)",
            "note": (
                "The market-observation archive itself carries lineupConfirmationState on "
                "0 of its rows, so the confirmation moment is located from an independent store."
            ),
        },
        "gameIdentity": identity,
        "totals": {
            "distinctContracts": len(timelines),
            "distinctPhysicalMlbGames": identity["distinctPhysicalMlbGames"],
            "distinctKalshiEventTickers": identity["distinctKalshiEventTickers"],
            "distinctMarketSeriesEvents": identity["distinctMarketSeriesEvents"],
            "distinctSlateDates": len(slate_dates),
            "settledContracts": len(settlements),
        },
    }
    _write_json("coverage_report.json", payload)
    _write_csv("coverage_by_slate_date.csv", coverage_rows, list(coverage_rows[0].keys()) if coverage_rows else ["slateDate"])
    _write_csv("coverage_by_market_family.csv", family_rows, list(family_rows[0].keys()) if family_rows else ["marketFamily"])
    _write_csv("liquidity_by_horizon.csv", liquidity_rows,
               list(liquidity_rows[0].keys()) if liquidity_rows else ["leadTimeHorizon"])
    _write_csv("raw_archive_by_slate_date.csv", raw["perSlateDate"],
               list(raw["perSlateDate"][0].keys()) if raw["perSlateDate"] else ["slateDate"])
    return payload



# ===========================================================================
# STAGE 2 -- DATASET (per-contract linked timelines)
# ===========================================================================

# (research point label, resolver kwargs). Order is chronological, earliest
# first, so a movement table reads left-to-right in time.
RESEARCH_POINTS = (
    nbt.POINT_EARLY_18H,
    nbt.POINT_EARLY_12H,
    nbt.POINT_EARLY_8H,
    nbt.POINT_FIRST_GAME_DAY,
    nbt.POINT_T_MINUS_90,
    nbt.POINT_LINEUP_CONFIRMATION,
    nbt.POINT_T_MINUS_30,
    nbt.POINT_CLOSING,
)

# Policy label -> the research point that policy transacts at.
POLICIES = {
    "A_NIGHT_BEFORE": nbt.POINT_EARLY_12H,
    "B_FIRST_GAME_DAY": nbt.POINT_FIRST_GAME_DAY,
    "C_T_MINUS_90": nbt.POINT_T_MINUS_90,
    "D_LINEUP_CONFIRMED": nbt.POINT_LINEUP_CONFIRMATION,
    "E_T_MINUS_30": nbt.POINT_T_MINUS_30,
}

T_MINUS_90_TARGET_HOURS = 1.5
T_MINUS_90_TOLERANCE_HOURS = 0.5
T_MINUS_30_TARGET_HOURS = 0.5
T_MINUS_30_TOLERANCE_HOURS = 0.25


def _usable_rows(timeline):
    """Only rows that could actually have been transacted at."""
    return [r for r in timeline if r["bookUsability"] == nbt.USABLE]


def _point_snapshot(row):
    if row is None:
        return None
    return {
        "capturedAt": row["capturedAt"],
        "hoursBeforeStart": round(row["hoursBeforeStart"], 4),
        "calendarContext": row["calendarContext"],
        "leadTimeHorizon": row["leadTimeHorizon"],
        "yesBid": row["yesBid"],
        "yesAsk": row["yesAsk"],
        "yesEntryCents": nbt.yes_ask_cents(row),
        "noEntryCents": nbt.no_ask_cents(row),
        "spreadCents": row["spreadCents"],
        "midCents": (row["yesBid"] + row["yesAsk"]) / 2.0,
        "lastPrice": row["lastPrice"],
        "volume": row["volume"],
        "openInterest": row["openInterest"],
        "stalenessFlag": row.get("stalenessFlag"),
    }


def build_contract_records(timelines, settlements, lineup_times):
    """
    One record per contract: its own executable quote at each research
    point, plus its settled outcome. Every price on a record comes from
    the SAME contract's own timeline, so any movement measured between two
    points is that contract's own price change.
    """
    records = []
    skipped = collections.Counter()
    for ticker, timeline in timelines.items():
        usable = _usable_rows(timeline)
        if not usable:
            skipped["NO_USABLE_QUOTE_EVER"] += 1
            continue
        pregame = [r for r in usable if r["hoursBeforeStart"] is not None
                   and r["hoursBeforeStart"] >= 0]
        if not pregame:
            skipped["NO_USABLE_PREGAME_QUOTE"] += 1
            continue

        head = timeline[0]
        if head["physicalGameKey"] is None:
            skipped["NO_RESOLVABLE_PHYSICAL_GAME"] += 1
            continue
        event_ticker = head["eventTicker"]
        points = {
            nbt.POINT_EARLY_18H: nbt.select_earliest_at_least(pregame, 18.0),
            nbt.POINT_EARLY_12H: nbt.select_earliest_at_least(pregame, 12.0),
            nbt.POINT_EARLY_8H: nbt.select_earliest_at_least(pregame, 8.0),
            nbt.POINT_FIRST_GAME_DAY: nbt.select_first_game_day(pregame),
            nbt.POINT_T_MINUS_90: nbt.select_nearest_to_target(
                pregame, T_MINUS_90_TARGET_HOURS, T_MINUS_90_TOLERANCE_HOURS),
            nbt.POINT_T_MINUS_30: nbt.select_nearest_to_target(
                pregame, T_MINUS_30_TARGET_HOURS, T_MINUS_30_TOLERANCE_HOURS),
            nbt.POINT_CLOSING: nbt.select_closing(pregame),
        }
        confirmation_time = lineup_times.get(head["physicalGameKey"])
        points[nbt.POINT_LINEUP_CONFIRMATION] = (
            nbt.select_at_or_before(pregame, confirmation_time)
            if confirmation_time else None
        )

        settlement = settlements.get(ticker)
        family = head["marketFamily"]
        records.append({
            "marketTicker": ticker,
            "eventTicker": event_ticker,
            "physicalGameKey": head["physicalGameKey"],
            "mlbGamePk": head["mlbGamePk"],
            "gameId": head["gameId"],
            "slateDate": head["slateDate"],
            "marketFamily": family,
            "seriesTicker": head["seriesTicker"],
            "researchSubFamily": research_sub_family(head["seriesTicker"], family),
            "isPropFamily": family in PROP_FAMILIES,
            "scheduledStart": head["scheduledStart"],
            "observationCount": len(timeline),
            "usablePregameObservationCount": len(pregame),
            "lineupConfirmationTime": confirmation_time,
            "settledResult": settlement["result"] if settlement else None,
            "points": {k: _point_snapshot(v) for k, v in points.items()},
        })
    return records, dict(skipped)


# ===========================================================================
# STAGE 3 -- ANALYSIS
# ===========================================================================

def _cluster(values_by_game):
    return clustered_bootstrap_mean_ci(values_by_game)


def _summarise_paired_movement(records, early_point, late_point, side):
    """
    Execution value only: how much cheaper (in cents) was `early_point`
    than `late_point` for a buyer of `side`, on contracts that have a
    usable quote at BOTH points.

    Positive `meanEarlyMinusLateCents` means the early price was HIGHER --
    i.e. waiting was cheaper. The sign convention is stated on every row of
    every emitted table so it can never be read backwards.
    """
    deltas_by_game = collections.defaultdict(list)
    deltas = []
    early_cheaper = late_cheaper = tied = 0
    key = "yesEntryCents" if side == nbt.SIDE_YES else "noEntryCents"
    for record in records:
        early = record["points"].get(early_point)
        late = record["points"].get(late_point)
        if not early or not late:
            continue
        if early[key] is None or late[key] is None:
            continue
        delta = early[key] - late[key]
        deltas.append(delta)
        deltas_by_game[record["physicalGameKey"]].append(delta)
        if delta < 0:
            early_cheaper += 1
        elif delta > 0:
            late_cheaper += 1
        else:
            tied += 1
    if not deltas:
        return None
    lo, hi, clusters = _cluster(deltas_by_game)
    return {
        "earlyPoint": early_point,
        "latePoint": late_point,
        "side": side,
        "contracts": len(deltas),
        "independentGames": clusters,
        "meanEarlyMinusLateCents": round(_mean(deltas), 4),
        "medianEarlyMinusLateCents": _median(deltas),
        "ci95LowCents": round(lo, 4) if lo is not None else None,
        "ci95HighCents": round(hi, 4) if hi is not None else None,
        "earlyCheaperCount": early_cheaper,
        "lateCheaperCount": late_cheaper,
        "tiedCount": tied,
        "earlyCheaperPct": round(100.0 * early_cheaper / len(deltas), 2),
        "signTestP": sign_test_p_value(early_cheaper, late_cheaper),
        "signConvention": "positive = early price HIGHER = waiting was cheaper",
    }


def _summarise_clv(records, entry_point, side):
    """
    Conventional closing-line value: entry price versus the closing MID,
    reported separately from executable-price movement so the two are
    never conflated. Positive = entry beat the closing mid.
    """
    values_by_game = collections.defaultdict(list)
    values = []
    beat = 0
    for record in records:
        entry = record["points"].get(entry_point)
        close = record["points"].get(nbt.POINT_CLOSING)
        if not entry or not close:
            continue
        entry_price = entry["yesEntryCents"] if side == nbt.SIDE_YES else entry["noEntryCents"]
        if entry_price is None or close["midCents"] is None:
            continue
        close_mid = close["midCents"] if side == nbt.SIDE_YES else 100.0 - close["midCents"]
        clv = close_mid - entry_price
        values.append(clv)
        values_by_game[record["physicalGameKey"]].append(clv)
        if clv > 0:
            beat += 1
    if not values:
        return None
    lo, hi, clusters = _cluster(values_by_game)
    return {
        "entryPoint": entry_point,
        "side": side,
        "contracts": len(values),
        "independentGames": clusters,
        "meanClvCents": round(_mean(values), 4),
        "medianClvCents": _median(values),
        "ci95LowCents": round(lo, 4) if lo is not None else None,
        "ci95HighCents": round(hi, 4) if hi is not None else None,
        "positiveClvPct": round(100.0 * beat / len(values), 2),
        "definition": "closing MID (side-adjusted) minus executable entry ask",
    }


def _summarise_roi(records, entry_point, side, require_settled=True):
    """
    Hypothetical realized ROI from actually buying `side` at
    `entry_point`'s executable ask on every qualifying contract. No fee
    adjustment (see night_before_timing.realized_return_per_contract).
    """
    returns_by_game = collections.defaultdict(list)
    returns = []
    wins = 0
    staked = 0.0
    payout = 0.0
    for record in records:
        if require_settled and record["settledResult"] not in ("YES", "NO"):
            continue
        point = record["points"].get(entry_point)
        if not point:
            continue
        price = point["yesEntryCents"] if side == nbt.SIDE_YES else point["noEntryCents"]
        ret = nbt.realized_return_per_contract(price, record["settledResult"], side)
        if ret is None:
            continue
        returns.append(ret)
        returns_by_game[record["physicalGameKey"]].append(ret)
        staked += price
        payout += 100.0 if record["settledResult"] == side else 0.0
        if record["settledResult"] == side:
            wins += 1
    if not returns:
        return None
    lo, hi, clusters = _cluster(returns_by_game)
    return {
        "entryPoint": entry_point,
        "side": side,
        "contracts": len(returns),
        "independentGames": clusters,
        "winRatePct": round(100.0 * wins / len(returns), 2),
        "meanRoi": round(_mean(returns), 6),
        "ci95LowRoi": round(lo, 6) if lo is not None else None,
        "ci95HighRoi": round(hi, 6) if hi is not None else None,
        "capitalWeightedRoi": round((payout - staked) / staked, 6) if staked else None,
        "meanEntryCents": round(staked / len(returns), 4),
        "feeAdjusted": False,
    }



def stage_dataset():
    timelines, obs_audit = load_observations()
    settlements, settle_audit = load_settlements()
    lineup_times = load_lineup_confirmation_times()
    records, skipped = build_contract_records(timelines, settlements, lineup_times)

    point_coverage = collections.Counter()
    for record in records:
        for point, snapshot in record["points"].items():
            if snapshot:
                point_coverage[point] += 1

    payload = {
        "provenance": _provenance("dataset"),
        "contracts": len(records),
        "contractsSkipped": skipped,
        "settledContracts": sum(1 for r in records if r["settledResult"]),
        "propContracts": sum(1 for r in records if r["isPropFamily"]),
        "researchPointCoverage": {p: point_coverage.get(p, 0) for p in RESEARCH_POINTS},
        "observationAudit": {
            "rawRows": obs_audit["rawRows"],
            "distinctMarketTickers": obs_audit["distinctMarketTickers"],
            "bookUsabilityCounts": obs_audit["bookUsabilityCounts"],
        },
        "settlementAudit": settle_audit,
    }
    _write_json("dataset_summary.json", payload)

    # Flat per-point CSV for the GAME-MARKET contracts. This is a file-size
    # convenience only, not an analytical exclusion: props ARE in every
    # analysis below, and contract_records.jsonl.gz (written immediately
    # after) carries every contract including all ~101k props. Emitting the
    # props here too produced a ~123 MB CSV that duplicated that file
    # verbatim. No evidence is lost.
    flat = []
    for record in records:
        if record["isPropFamily"]:
            continue
        base = {k: v for k, v in record.items() if k != "points"}
        for point in RESEARCH_POINTS:
            snapshot = record["points"].get(point)
            if not snapshot:
                continue
            row = dict(base)
            row["researchPoint"] = point
            row.update(snapshot)
            flat.append(row)
    if flat:
        _write_csv("contract_point_prices_nonprop.csv.gz", flat, list(flat[0].keys()))

    path = os.path.join(ARTIFACT_DIR, "contract_records.jsonl.gz")
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    with gzip.open(path, "wt") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return records, payload


def _analysis_universe(records, points_required, settled_only=True,
                       exclude_props=False, only_props=False):
    """
    Contracts usable for a paired comparison across `points_required`.

    Requiring a quote at every compared point is a real, stated
    restriction: it conditions on future DATA AVAILABILITY (was this
    contract still being captured later?), though never on future PRICES
    or OUTCOMES. Both the balanced set (this) and the unbalanced
    per-point sets are reported, so the effect of the restriction is
    visible rather than hidden.
    """
    out = []
    for record in records:
        if settled_only and record["settledResult"] not in ("YES", "NO"):
            continue
        if exclude_props and record["isPropFamily"]:
            continue
        if only_props and not record["isPropFamily"]:
            continue
        if any(record["points"].get(p) is None for p in points_required):
            continue
        out.append(record)
    return out


def _movement_tables(records, label):
    rows = []
    pairs = [
        (nbt.POINT_EARLY_18H, nbt.POINT_FIRST_GAME_DAY),
        (nbt.POINT_EARLY_12H, nbt.POINT_FIRST_GAME_DAY),
        (nbt.POINT_EARLY_12H, nbt.POINT_T_MINUS_90),
        (nbt.POINT_EARLY_12H, nbt.POINT_LINEUP_CONFIRMATION),
        (nbt.POINT_EARLY_12H, nbt.POINT_T_MINUS_30),
        (nbt.POINT_EARLY_12H, nbt.POINT_CLOSING),
        (nbt.POINT_EARLY_18H, nbt.POINT_CLOSING),
        (nbt.POINT_EARLY_8H, nbt.POINT_CLOSING),
        (nbt.POINT_FIRST_GAME_DAY, nbt.POINT_CLOSING),
        (nbt.POINT_LINEUP_CONFIRMATION, nbt.POINT_CLOSING),
    ]
    for early, late in pairs:
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            summary = _summarise_paired_movement(records, early, late, side)
            if summary:
                summary["population"] = label
                rows.append(summary)
    return rows


def _by_sub_family(records, fn, *args, **kwargs):
    """Same as _by_family, but on the research sub-family (§ spec requirement 6)."""
    rows = []
    groups = collections.defaultdict(list)
    for record in records:
        groups[record["researchSubFamily"]].append(record)
    for name in sorted(groups):
        summary = fn(groups[name], *args, **kwargs)
        if summary:
            summary["researchSubFamily"] = name
            summary["seriesTickers"] = ",".join(sorted(
                {r["seriesTicker"] for r in groups[name] if r["seriesTicker"]}))
            rows.append(summary)
    return rows


def _by_family(records, fn, *args, **kwargs):
    rows = []
    families = collections.defaultdict(list)
    for record in records:
        families[record["marketFamily"]].append(record)
    for family in sorted(families):
        summary = fn(families[family], *args, **kwargs)
        if summary:
            summary["marketFamily"] = family
            rows.append(summary)
    return rows


def _walk_forward(records, entry_point, comparison_point, side, min_train=5):
    """
    Rolling-origin evaluation of the simplest honest candidate rule the
    evidence could support: "enter at `entry_point` only in the (family,
    price band, spread band) cells whose historical early-vs-late
    execution advantage, measured ONLY on strictly earlier slate dates,
    was favourable."

    Every ingredient of the decision is knowable at the entry timestamp:
    the family, the displayed price, the displayed spread, and the
    performance of previous dates. No closing price, no settlement, and
    no same-date information enters cell selection. The test statistic is
    then measured on the held-out date only.
    """
    key = "yesEntryCents" if side == nbt.SIDE_YES else "noEntryCents"
    by_date = collections.defaultdict(list)
    for record in records:
        entry = record["points"].get(entry_point)
        if entry is None or entry[key] is None:
            continue
        by_date[record["slateDate"]].append(record)
    dates = sorted(by_date)

    def cell_of(record):
        entry = record["points"][entry_point]
        price = entry[key]
        band = "LOW" if price < 33 else ("MID" if price < 67 else "HIGH")
        spread = entry["spreadCents"]
        spread_band = "TIGHT" if spread is not None and spread <= 2 else "WIDE"
        return (record["marketFamily"], band, spread_band)

    history = collections.defaultdict(list)
    fold_rows = []
    all_oos_returns = []
    all_oos_by_game = collections.defaultdict(list)
    baseline_oos_returns = []
    for index, date in enumerate(dates):
        test = by_date[date]
        if index >= min_train:
            selected_cells = {c for c, vals in history.items()
                              if len(vals) >= 20 and _mean(vals) is not None and _mean(vals) < 0}
            picked = [r for r in test if cell_of(r) in selected_cells]
            returns, baseline = [], []
            for record in picked:
                entry = record["points"][entry_point]
                ret = nbt.realized_return_per_contract(entry[key], record["settledResult"], side)
                if ret is None:
                    continue
                returns.append(ret)
                all_oos_returns.append(ret)
                all_oos_by_game[record["physicalGameKey"]].append(ret)
                late = record["points"].get(comparison_point)
                if late and late[key] is not None:
                    base = nbt.realized_return_per_contract(
                        late[key], record["settledResult"], side)
                    if base is not None:
                        baseline.append(base)
                        baseline_oos_returns.append(base)
            fold_rows.append({
                "testDate": date,
                "trainDatesUsed": index,
                "selectedCells": len(selected_cells),
                "candidatesSelected": len(returns),
                "oosMeanRoi": round(_mean(returns), 6) if returns else None,
                "baselineMeanRoi": round(_mean(baseline), 6) if baseline else None,
            })
        for record in test:
            entry = record["points"][entry_point]
            late = record["points"].get(comparison_point)
            if not late or entry[key] is None or late[key] is None:
                continue
            history[cell_of(record)].append(entry[key] - late[key])

    lo, hi, clusters = _cluster(all_oos_by_game)
    return {
        "entryPoint": entry_point,
        "comparisonPoint": comparison_point,
        "side": side,
        "foldCount": len(fold_rows),
        "outOfSampleContracts": len(all_oos_returns),
        "independentGames": clusters,
        "outOfSampleMeanRoi": round(_mean(all_oos_returns), 6) if all_oos_returns else None,
        "ci95LowRoi": round(lo, 6) if lo is not None else None,
        "ci95HighRoi": round(hi, 6) if hi is not None else None,
        "baselineMeanRoiSameContractsLaterEntry": (
            round(_mean(baseline_oos_returns), 6) if baseline_oos_returns else None),
        "ruleDescription": (
            "Enter only in (family, price band, spread band) cells whose mean "
            "early-minus-late executable price on STRICTLY EARLIER slate dates was "
            "negative (early cheaper), with >=20 prior observations in the cell."
        ),
        "folds": fold_rows,
    }


def _leave_one_date_out(records, entry_point, late_point, side):
    """Stability: recompute the headline execution-value number with each slate date removed."""
    key = "yesEntryCents" if side == nbt.SIDE_YES else "noEntryCents"
    per_date = collections.defaultdict(list)
    for record in records:
        early, late = record["points"].get(entry_point), record["points"].get(late_point)
        if not early or not late or early[key] is None or late[key] is None:
            continue
        per_date[record["slateDate"]].append(early[key] - late[key])
    dates = sorted(per_date)
    rows = []
    for excluded in dates:
        pool = [v for d in dates if d != excluded for v in per_date[d]]
        rows.append({
            "excludedDate": excluded,
            "remainingContracts": len(pool),
            "meanEarlyMinusLateCents": round(_mean(pool), 4) if pool else None,
        })
    per_date_rows = [{
        "slateDate": d,
        "contracts": len(per_date[d]),
        "meanEarlyMinusLateCents": round(_mean(per_date[d]), 4),
    } for d in dates]
    return rows, per_date_rows


def _liquidity_by_family_and_horizon(records):
    rows = []
    buckets = collections.defaultdict(lambda: {"spread": [], "vol": [], "oi": [], "n": 0, "stale": 0})
    for record in records:
        for point, snapshot in record["points"].items():
            if not snapshot:
                continue
            bucket = buckets[(record["marketFamily"], point)]
            bucket["n"] += 1
            bucket["spread"].append(snapshot["spreadCents"])
            if snapshot["volume"] is not None:
                bucket["vol"].append(snapshot["volume"])
            if snapshot["openInterest"] is not None:
                bucket["oi"].append(snapshot["openInterest"])
            if snapshot.get("stalenessFlag") == nbt.STALE_REPEATED_BOOK:
                bucket["stale"] += 1
    for (family, point) in sorted(buckets):
        bucket = buckets[(family, point)]
        rows.append({
            "marketFamily": family,
            "researchPoint": point,
            "contracts": bucket["n"],
            "medianSpreadCents": _median(bucket["spread"]),
            "meanSpreadCents": round(_mean(bucket["spread"]), 4),
            "medianVolume": _median(bucket["vol"]),
            "medianOpenInterest": _median(bucket["oi"]),
            "zeroVolumePct": round(100.0 * sum(1 for v in bucket["vol"] if v == 0) / len(bucket["vol"]), 2) if bucket["vol"] else None,
            "repeatedBookFlaggedPct": round(100.0 * bucket["stale"] / bucket["n"], 2),
        })
    return rows


def _lineup_risk(records):
    """
    Ex-post explanatory only (spec requirement 7). The magnitude of a
    game's own price revision between the overnight entry and the
    lineup-confirmation moment is the only lineup-driven uncertainty this
    archive can actually measure: the repository retains no point-in-time
    EXPECTED lineup from before game day (the only frozen slate is the
    ~1 PM ET PRE_GAME_DECISION snapshot, and data/pipeline/<date> is
    overwritten in place and ends the day holding the FINAL confirmed
    lineup). Expected-versus-actual hitter-level lineup change at the
    overnight horizon is therefore NOT reconstructible, and this study
    does not pretend otherwise.
    """
    lineup_quality = {}
    for path in sorted(glob.glob(LINEUP_AUDIT_GLOB)):
        try:
            payload = json.load(open(path))
        except Exception:
            continue
        for row in payload.get("rows") or []:
            game = row.get("game")
            if not game:
                continue
            key = (payload.get("date"), game)
            current = lineup_quality.setdefault(key, {"confirmed": 0, "missing": 0})
            if row.get("lineupStatus") == "confirmed":
                current["confirmed"] += 1
            else:
                current["missing"] += 1

    by_game = collections.defaultdict(list)
    for record in records:
        early = record["points"].get(nbt.POINT_EARLY_12H)
        late = record["points"].get(nbt.POINT_LINEUP_CONFIRMATION)
        if not early or not late:
            continue
        by_game[(record["slateDate"], record["physicalGameKey"], record["marketFamily"])].append(
            abs(early["midCents"] - late["midCents"]))

    rows = []
    for (date, event, family), moves in sorted(by_game.items()):
        rows.append({
            "slateDate": date,
            "physicalGameKey": event,
            "marketFamily": family,
            "contracts": len(moves),
            "meanAbsMidRevisionCents": round(_mean(moves), 4),
            "maxAbsMidRevisionCents": round(max(moves), 4),
        })

    family_rows = []
    grouped = collections.defaultdict(list)
    for row in rows:
        grouped[row["marketFamily"]].append(row["meanAbsMidRevisionCents"])
    for family in sorted(grouped):
        family_rows.append({
            "marketFamily": family,
            "gameFamilyCells": len(grouped[family]),
            "meanAbsMidRevisionCents": round(_mean(grouped[family]), 4),
            "medianAbsMidRevisionCents": _median(grouped[family]),
            "p90AbsMidRevisionCents": sorted(grouped[family])[int(0.9 * (len(grouped[family]) - 1))],
        })
    return rows, family_rows, {
        "lineupAuditGameCells": len(lineup_quality),
        "gamesWithAnyMissingLineup": sum(1 for v in lineup_quality.values() if v["missing"]),
        "limitation": (
            "No point-in-time EXPECTED lineup exists before game day anywhere in this "
            "repository, so ex-post expected-vs-actual hitter change, platoon change, "
            "star rest-day absence and probable-pitcher replacement CANNOT be measured "
            "at the overnight horizon. Market mid revision is used as the only "
            "available proxy for lineup-driven uncertainty and is explanatory only."
        ),
    }



def stage_analysis(records=None):
    if records is None:
        records, _ = stage_dataset()

    # Populations are kept APART rather than pooled. Player props and game
    # markets have different liquidity, different price levels and different
    # base rates, so a single blended headline over both would be
    # uninterpretable -- and pooling fundamentally different families is
    # exactly what the research brief forbids. Props are now fully settled
    # (see load_settlements' playerPropSettlementNote) and therefore carry
    # realized ROI of their own; they are simply reported in their own rows.

    # --- Population 1: settled NON-PROP contracts (game markets).
    unbalanced = _analysis_universe(records, points_required=(), settled_only=True,
                                    exclude_props=True)

    # --- Population 2: settled PROP contracts, reported separately.
    unbalanced_props = _analysis_universe(records, points_required=(), settled_only=True,
                                          only_props=True)

    # --- Population 3: balanced. Contracts quoted at EVERY policy point, so
    #     Policies A-E are compared on one identical candidate set. Split the
    #     same way.
    policy_points = tuple(POLICIES.values()) + (nbt.POINT_CLOSING,)
    balanced = _analysis_universe(records, points_required=policy_points,
                                  settled_only=True, exclude_props=True)
    balanced_props = _analysis_universe(records, points_required=policy_points,
                                        settled_only=True, only_props=True)

    movement_rows = (_movement_tables(unbalanced, "settled_game_markets")
                     + _movement_tables(unbalanced_props, "settled_player_props")
                     + _movement_tables(balanced, "balanced_game_markets")
                     + _movement_tables(balanced_props, "balanced_player_props"))

    movement_by_family = []
    for early, late in ((nbt.POINT_EARLY_12H, nbt.POINT_LINEUP_CONFIRMATION),
                        (nbt.POINT_EARLY_12H, nbt.POINT_CLOSING)):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            for row in _by_family(unbalanced, _summarise_paired_movement, early, late, side):
                row["population"] = "settled_game_markets"
                movement_by_family.append(row)
            for row in _by_family(unbalanced_props, _summarise_paired_movement,
                                  early, late, side):
                row["population"] = "settled_player_props"
                movement_by_family.append(row)

    movement_by_sub_family = []
    for early, late in ((nbt.POINT_EARLY_12H, nbt.POINT_LINEUP_CONFIRMATION),
                        (nbt.POINT_EARLY_12H, nbt.POINT_CLOSING)):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            for row in _by_sub_family(unbalanced + unbalanced_props,
                                      _summarise_paired_movement, early, late, side):
                row["population"] = "settled_all_families_split_by_sub_family"
                movement_by_sub_family.append(row)
    sub_pvalues = [row.get("signTestP") for row in movement_by_sub_family]
    sub_rejected = benjamini_hochberg(sub_pvalues, alpha=0.05)
    for index, row in enumerate(movement_by_sub_family):
        row["bhSignificantAtFdr05"] = index in sub_rejected

    roi_by_sub_family = []
    for policy, point in (("A_NIGHT_BEFORE", nbt.POINT_EARLY_12H),
                          ("D_LINEUP_CONFIRMED", nbt.POINT_LINEUP_CONFIRMATION)):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            for row in _by_sub_family(unbalanced + unbalanced_props,
                                      _summarise_roi, point, side):
                row["policy"] = policy
                roi_by_sub_family.append(row)

    # Realized ROI for the prop families, per family, on the settled prop
    # population. This is the sample expansion the earlier revision wrongly
    # forfeited: 97,423 settled prop contracts, every one carrying
    # settlementEvidence.
    # NOTE on reading this table: each policy row is computed over the contracts
    # that HAVE that policy's entry point, so the rows are not a paired
    # comparison and their ROIs are not directly subtractable. The rigorous
    # timing comparison is policyPairedDifferences, which holds the contract set
    # fixed. This table exists to show each prop family's own economics.
    roi_by_prop_family = []
    for policy, point in (("A_NIGHT_BEFORE", nbt.POINT_EARLY_12H),
                          ("D_LINEUP_CONFIRMED", nbt.POINT_LINEUP_CONFIRMATION),
                          ("CLOSING_BASELINE", nbt.POINT_CLOSING)):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            for row in _by_family(unbalanced_props, _summarise_roi, point, side):
                row["policy"] = policy
                roi_by_prop_family.append(row)

    clv_rows = []
    for point in (nbt.POINT_EARLY_18H, nbt.POINT_EARLY_12H, nbt.POINT_EARLY_8H,
                  nbt.POINT_FIRST_GAME_DAY, nbt.POINT_LINEUP_CONFIRMATION):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            for population_label, population in (
                    ("settled_game_markets", unbalanced),
                    ("settled_player_props", unbalanced_props)):
                summary = _summarise_clv(population, point, side)
                if summary:
                    summary["population"] = population_label
                    clv_rows.append(summary)
    clv_by_family = []
    for side in (nbt.SIDE_YES, nbt.SIDE_NO):
        for row in _by_family(unbalanced, _summarise_clv, nbt.POINT_EARLY_12H, side):
            clv_by_family.append(row)

    roi_rows = []
    for policy, point in sorted(POLICIES.items()):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            for population_label, population in (
                    ("balanced_game_markets", balanced),
                    ("balanced_player_props", balanced_props)):
                summary = _summarise_roi(population, point, side)
                if summary:
                    summary["policy"] = policy
                    summary["population"] = population_label
                    roi_rows.append(summary)
    roi_unbalanced = []
    for policy, point in sorted(POLICIES.items()):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            for population_label, population in (
                    ("settled_game_markets", unbalanced),
                    ("settled_player_props", unbalanced_props)):
                summary = _summarise_roi(population, point, side)
                if summary:
                    summary["policy"] = policy
                    summary["population"] = population_label
                    roi_unbalanced.append(summary)

    roi_by_family = []
    for policy, point in (("A_NIGHT_BEFORE", nbt.POINT_EARLY_12H),
                          ("D_LINEUP_CONFIRMED", nbt.POINT_LINEUP_CONFIRMATION)):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            for row in _by_family(balanced, _summarise_roi, point, side):
                row["policy"] = policy
                roi_by_family.append(row)

    # Paired policy differences on the identical balanced set: the single
    # number that answers "does waiting cost money, holding the bet fixed?"
    policy_pairs = []
    for population_label, population in (("balanced_game_markets", balanced),
                                         ("balanced_player_props", balanced_props)):
      for side in (nbt.SIDE_YES, nbt.SIDE_NO):
        key = "yesEntryCents" if side == nbt.SIDE_YES else "noEntryCents"
        for policy, point in sorted(POLICIES.items()):
            if policy == "A_NIGHT_BEFORE":
                continue
            diffs_by_game = collections.defaultdict(list)
            diffs = []
            for record in population:
                a = record["points"][nbt.POINT_EARLY_12H]
                b = record["points"][point]
                ra = nbt.realized_return_per_contract(a[key], record["settledResult"], side)
                rb = nbt.realized_return_per_contract(b[key], record["settledResult"], side)
                if ra is None or rb is None:
                    continue
                diffs.append(ra - rb)
                diffs_by_game[record["physicalGameKey"]].append(ra - rb)
            if not diffs:
                continue
            lo, hi, clusters = _cluster(diffs_by_game)
            policy_pairs.append({
                "population": population_label,
                "side": side,
                "policyA": "A_NIGHT_BEFORE",
                "policyB": policy,
                "contracts": len(diffs),
                "independentGames": clusters,
                "meanRoiDifferenceAMinusB": round(_mean(diffs), 6),
                "ci95Low": round(lo, 6) if lo is not None else None,
                "ci95High": round(hi, 6) if hi is not None else None,
                "interpretation": "positive = entering the night before beat waiting, on identical contracts",
            })

    # Multiple-testing correction across every family-level movement claim.
    pvalues = [row.get("signTestP") for row in movement_by_family]
    rejected = benjamini_hochberg(pvalues, alpha=0.05)
    for index, row in enumerate(movement_by_family):
        row["bhSignificantAtFdr05"] = index in rejected

    walk_forward = []
    for population_label, population in (("settled_game_markets", unbalanced),
                                         ("settled_player_props", unbalanced_props)):
        for side in (nbt.SIDE_YES, nbt.SIDE_NO):
            entry = _walk_forward(population, nbt.POINT_EARLY_12H,
                                  nbt.POINT_CLOSING, side)
            entry["population"] = population_label
            walk_forward.append(entry)

    lodo_rows, per_date_rows = _leave_one_date_out(
        unbalanced, nbt.POINT_EARLY_12H, nbt.POINT_CLOSING, nbt.SIDE_YES)

    lodo_prop_rows, per_date_prop_rows = _leave_one_date_out(
        unbalanced_props, nbt.POINT_EARLY_12H, nbt.POINT_CLOSING, nbt.SIDE_YES)
    for row in per_date_rows:
        row["population"] = "settled_game_markets"
    for row in per_date_prop_rows:
        row["population"] = "settled_player_props"
    per_date_rows = per_date_rows + per_date_prop_rows

    liquidity_rows = _liquidity_by_family_and_horizon(records)
    lineup_game_rows, lineup_family_rows, lineup_meta = _lineup_risk(unbalanced)

    fee_note = {
        "headlineIsFeeAdjusted": False,
        "rationale": (
            "The user's real cost basis for this workflow is the displayed executable "
            "contract price, so every headline number uses it unadjusted, per spec "
            "requirement 3. lib/edgelab/kalshi_fees.py exists and is production's one "
            "fee engine; it is deliberately NOT applied here."
        ),
        "sensitivityDirection": (
            "Kalshi's trading fee is charged per contract on both entry and settlement "
            "exposure, so applying it can only reduce every realized-ROI figure below. "
            "It cannot turn a negative headline ROI positive, so the verdict is "
            "insensitive to it in the direction that matters."
        ),
    }

    payload = {
        "provenance": _provenance("analysis"),
        "populations": {
            "settled_game_markets": len(unbalanced),
            "settled_player_props": len(unbalanced_props),
            "balanced_game_markets": len(balanced),
            "balanced_player_props": len(balanced_props),
            "balancedPointsRequired": list(policy_points),
            "note": (
                "Game markets and player props are reported as separate populations "
                "and never pooled into one headline. Both carry realized ROI."
            ),
        },
        "priceMovement": movement_rows,
        "priceMovementByFamily": movement_by_family,
        "priceMovementByResearchSubFamily": movement_by_sub_family,
        "realizedRoiByResearchSubFamily": roi_by_sub_family,
        "realizedRoiByPropFamily": roi_by_prop_family,
        "clv": clv_rows,
        "clvByFamily": clv_by_family,
        "realizedRoiBalanced": roi_rows,
        "realizedRoiUnbalanced": roi_unbalanced,
        "realizedRoiByFamily": roi_by_family,
        "policyPairedDifferences": policy_pairs,
        "walkForward": walk_forward,
        "stabilityLeaveOneDateOut": lodo_rows,
        "stabilityLeaveOneDateOutProps": lodo_prop_rows,
        "perSlateDateExecutionValue": per_date_rows,
        "lineupRisk": {"byFamily": lineup_family_rows, "meta": lineup_meta},
        "feeSensitivity": fee_note,
        "bootstrap": {"seed": BOOTSTRAP_SEED, "iterations": BOOTSTRAP_ITERATIONS,
                      "clusterUnit": "physicalGameKey (one physical MLB game, shared across every Kalshi series)"},
    }
    _write_json("analysis_report.json", payload)

    if movement_rows:
        _write_csv("price_movement.csv", movement_rows, list(movement_rows[0].keys()))
    if movement_by_family:
        _write_csv("price_movement_by_family.csv", movement_by_family,
                   list(movement_by_family[0].keys()))
    if movement_by_sub_family:
        _write_csv("price_movement_by_research_sub_family.csv", movement_by_sub_family,
                   list(movement_by_sub_family[0].keys()))
    if roi_by_sub_family:
        _write_csv("realized_roi_by_research_sub_family.csv", roi_by_sub_family,
                   list(roi_by_sub_family[0].keys()))
    if roi_by_prop_family:
        _write_csv("realized_roi_by_prop_family.csv", roi_by_prop_family,
                   list(roi_by_prop_family[0].keys()))
    if clv_rows:
        _write_csv("clv_summary.csv", clv_rows, list(clv_rows[0].keys()))
    if roi_rows:
        _write_csv("realized_roi_by_policy.csv", roi_rows, list(roi_rows[0].keys()))
    if roi_by_family:
        _write_csv("realized_roi_by_family.csv", roi_by_family, list(roi_by_family[0].keys()))
    if policy_pairs:
        _write_csv("policy_paired_differences.csv", policy_pairs, list(policy_pairs[0].keys()))
    if liquidity_rows:
        _write_csv("liquidity_by_family_and_point.csv", liquidity_rows,
                   list(liquidity_rows[0].keys()))
    if lineup_family_rows:
        _write_csv("lineup_risk_by_family.csv", lineup_family_rows,
                   list(lineup_family_rows[0].keys()))
    if lineup_game_rows:
        _write_csv("lineup_risk_by_game.csv", lineup_game_rows, list(lineup_game_rows[0].keys()))
    if per_date_rows:
        _write_csv("execution_value_by_slate_date.csv", per_date_rows,
                   list(per_date_rows[0].keys()))
    walk_rows = []
    for entry in walk_forward:
        for fold in entry["folds"]:
            row = dict(fold)
            row["side"] = entry["side"]
            walk_rows.append(row)
    if walk_rows:
        _write_csv("walk_forward_folds.csv", walk_rows, list(walk_rows[0].keys()))
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=["coverage", "dataset", "analysis", "all"],
                        default="all")
    args = parser.parse_args()
    records = None
    if args.stage in ("coverage", "all"):
        payload = stage_coverage()
        print("== COVERAGE ==")
        print(json.dumps(payload["totals"], indent=2))
        print("crossCalendarDateMarketRows:",
              payload["rawArchive"]["crossCalendarDateMarketRows"])
        print("raw archive lead buckets:",
              json.dumps(payload["rawArchive"]["leadTimeHorizonCounts"]))
        print("observation calendar contexts:",
              json.dumps(payload["observationStore"]["calendarContextCounts"]))
    if args.stage in ("dataset", "all"):
        records, payload = stage_dataset()
        print("== DATASET ==")
        print(json.dumps({k: v for k, v in payload.items()
                          if k not in ("provenance", "observationAudit")}, indent=2))
    if args.stage in ("analysis", "all"):
        payload = stage_analysis(records)
        print("== ANALYSIS ==")
        print(json.dumps(payload["populations"], indent=2))
        print("artifacts written to", ARTIFACT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
