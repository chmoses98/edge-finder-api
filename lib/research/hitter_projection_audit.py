#!/usr/bin/env python3
"""
lib/research/hitter_projection_audit.py
===========================================
Retrospective grading + calibration audit for the archived PROSPECTIVE
hitter projection engine (docs/HITTER_SIMULATION_ENGINE.md,
scripts/build_hitter_projection_board.py). RESEARCH-ONLY: reads existing
archived artifacts and computes derived research metrics; changes no
production formula, threshold, weight, prior, recommendation, staking,
or settlement-semantics logic anywhere in this repository.

WHAT THIS AUDITS
-----------------
The hitter projection engine (docs/HITTER_SIMULATION_ENGINE.md) is NOT
wired into the daily production pipeline (docs/PROJECTION_BOARD.md:
"Hitter props remain untouched/out of scope"; the ModelEvaluation schema
itself documents evaluationStatus="NO_MODEL_SUPPORT ... e.g. a player
prop"). Its only prospective output is the standalone, manually-triggered
research board `data/pipeline/<date>/hitter_projection_board.json`
(scripts/run_standalone_hitter_research.py, invoked via
.github/workflows/kalshi-price-check.yml, workflow_dispatch only). This
module discovers every one of those archived boards, keeps only rows the
engine actually priced pregame (projectionStatus == "PROJECTED" -- every
other status means no fair probability was ever computed for that row),
and joins each to the real settled outcome already computed by the
existing automatic player-prop settlement pipeline (GitHub issue #43,
docs/PLAYER_PROP_SETTLEMENT.md, data/edgelab/settlements/<date>.jsonl).

THIS MODULE NEVER COMPUTES ITS OWN SETTLEMENT.
------------------------------------------------
It reads settlementEvidence/outcome fields the existing production
settlement pipeline already wrote; it never re-derives a box score,
never guesses a settlement, and never mutates a settlement/bet record.
A ticker with no matching settlement row is reported UNRESOLVED, never
inferred.

PROSPECTIVE INTEGRITY
----------------------
A row's projectionStatus=="PROJECTED" already implies the board builder
itself refused to price it once its game had started (see
lib.research.hitter_board_builder / docs/HITTER_SIMULATION_ENGINE.md
Sec.16, GAME_STARTED reuses lib.edgelab.checkpoints.classify_checkpoint's
POST_START determination) -- so a PROJECTED row is pregame by
construction. This module adds one further, independent cross-check
(`classify_provenance_confidence`): a PROJECTED row's own
`projectionGeneratedAt` must agree (within a generous tolerance) with
the capture timestamp embedded in its own `sourceCapturePath` filename
(an independently-named, separately-archived Kalshi snapshot file this
run consumed) -- two independently-produced artifacts landing at the
same wall-clock time is much stronger evidence of genuine prospective
generation than trusting a single self-reported field. Rows that fail
this cross-check (missing/unparseable capture timestamp, or a gap wider
than the tolerance) are flagged PROVENANCE_UNCERTAIN and excluded from
the PRIMARY performance metrics (kept, separately reported) -- never
silently blended in.
"""
import glob
import json
import math
import os
import re
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from lib.edgelab.calibration import calibration_status
from lib.edgelab.kalshi_fees import (
    FEE_TYPE_TAKER,
    fee_adjusted_break_even_probability,
    net_settlement_pl_fee_only,
)

SCHEMA_VERSION = "1.0"

PIPELINE_ROOT_DEFAULT = "data/pipeline"
SETTLEMENTS_ROOT_DEFAULT = "data/edgelab/settlements"

HITTER_FAMILIES = (
    "hitter_hits", "hitter_total_bases", "hitter_hits_runs_rbis",
    "hitter_rbis", "hitter_stolen_bases",
)

# Every non-"PROJECTED" status the board builder emits, and why it can
# never carry a modelProbability -- see lib/research/hitter_board_builder.py
# and docs/HITTER_SIMULATION_ENGINE.md Sec.16 for the authoritative list.
NON_PROJECTED_REASON_LABELS = {
    "LINEUP_UNCONFIRMED": "confirmed starting lineup not yet available",
    "GAME_STARTED": "game had already started at generation time (pregame safety exclusion)",
    "PLAYER_NOT_IN_STARTING_LINEUP": "ticker's player not in the confirmed starting lineup",
    "PLAYER_ID_UNRESOLVED": "player identity could not be resolved",
    "MARKET_SEMANTICS_UNSUPPORTED": "contract semantics not supported by this engine",
    "AMBIGUOUS_TICKER_MATCH": "ticker matched more than one candidate",
    "MISSING_REQUIRED_CONTEXT": "required pregame context unavailable",
    "MODEL_ERROR": "an exception was raised while simulating this hitter",
}

PROBABILITY_BUCKETS = [
    (0.0, 0.35, "<35%"),
    (0.35, 0.40, "35-39.9%"),
    (0.40, 0.45, "40-44.9%"),
    (0.45, 0.50, "45-49.9%"),
    (0.50, 0.55, "50-54.9%"),
    (0.55, 0.60, "55-59.9%"),
    (0.60, 0.65, "60-64.9%"),
    (0.65, 0.70, "65-69.9%"),
    (0.70, 0.75, "70-74.9%"),
    (0.75, 1.0001, "75%+"),
]

EDGE_BUCKETS = [
    (0.0, 0.02, "0-2pp"),
    (0.02, 0.05, "2-5pp"),
    (0.05, 0.10, "5-10pp"),
    (0.10, 0.20, "10-20pp"),
    (0.20, 1.0001, "20pp+"),
]

# Pregame-only checkpoint labels the existing CLV/settlement pipeline
# already produces (lib/edgelab/checkpoints.py) -- ordered closest-to-
# first-pitch first. None of these can be a post-first-pitch quote (the
# settlement pipeline that wrote hypotheticalReturnsByCheckpoint never
# records one past first pitch); "closest available" is therefore always
# a safe pregame closing-line proxy, never a risk of leaking a live quote.
CLOSING_CHECKPOINT_PRIORITY = (
    "T_MINUS_5", "T_MINUS_15", "T_MINUS_30", "T_MINUS_60", "T_MINUS_90",
)
# FIRST_DAILY and LINEUP_CONFIRMATION are deliberately EXCLUDED from this
# list, even though both are pregame-safe. FIRST_DAILY is BY DEFINITION
# the earliest capture of the day for a ticker (lib/edgelab/checkpoints.py)
# -- for a hitter-board row whose own entry only exists once the lineup
# is confirmed (i.e. already fairly close to game time), FIRST_DAILY is
# very likely to be an OPENING quote relative to this row's own entry,
# not a closing one; treating it as "closing" risks silently reversing
# the direction of the CLV calculation. LINEUP_CONFIRMATION's timing
# relative to this row's own entry is not reliably orderable either (this
# audit's settlement rows carry no per-checkpoint capturedAt to verify
# against). Only the explicit time-distance-to-first-pitch checkpoints
# (T_MINUS_X, strictly ordered by construction -- T_MINUS_5 is always
# closer to first pitch than T_MINUS_90) are used. A ticker with only a
# FIRST_DAILY/LINEUP_CONFIRMATION checkpoint on record gets
# closingCheckpointUsedForCLV=None / clvCents=None -- CLV honestly
# marked unavailable, never a blind fallback to whatever quote exists
# (matching this mission's explicit CLV instruction).

PROVENANCE_TOLERANCE_MINUTES = 20


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_projection_boards(pipeline_root=PIPELINE_ROOT_DEFAULT):
    """Every data/pipeline/<date>/hitter_projection_board.json on disk, sorted by date."""
    paths = sorted(glob.glob(os.path.join(pipeline_root, "*", "hitter_projection_board.json")))
    out = []
    for path in paths:
        date = os.path.basename(os.path.dirname(path))
        if re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            out.append((date, path))
    return out


def _safe_load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def load_board(date, path):
    """Returns (all_rows, board_summary) with every row tagged sourceDate/sourceBoardPath. Never raises."""
    doc = _safe_load_json(path)
    if doc is None:
        return [], None
    data = doc.get("data") or {}
    rows = data.get("rows") or []
    for r in rows:
        r["sourceDate"] = date
        r["sourceBoardPath"] = path
    return rows, data.get("summary")


def load_settlement_index(date, settlements_root=SETTLEMENTS_ROOT_DEFAULT):
    """{marketTicker: settlement_row} for every hitter-family settlement row on `date`. {} if the file doesn't exist (settlement not yet run for that date -- an honest gap, not an error)."""
    path = os.path.join(settlements_root, f"{date}.jsonl")
    index = {}
    if not os.path.exists(path):
        return index
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("marketFamily") in HITTER_FAMILIES and row.get("marketTicker"):
                index[row["marketTicker"]] = row
    return index


def load_feature_index(date, pipeline_root=PIPELINE_ROOT_DEFAULT):
    """{playerId_str: feature_hitter_record} extracted from data/pipeline/<date>/hitter_features.json, for segmentation context (lineup slot, home/away, handedness where populated). Keyed by playerId alone (not also gameId) because the hitter_projection_board row schema does not carry gameId -- a player appears in at most one game per date, so this is unambiguous within one date's index. {} if unavailable."""
    path = os.path.join(pipeline_root, date, "hitter_features.json")
    doc = _safe_load_json(path)
    index = {}
    if not doc:
        return index
    games = ((doc.get("data") or {}).get("games")) or []
    for g in games:
        for side in ("away", "home"):
            side_block = g.get(side) or {}
            for hitter in side_block.get("hitters") or []:
                pid = (hitter.get("playerIdentity") or {}).get("playerId")
                if pid is None:
                    continue
                index[str(pid)] = hitter
    return index


# ---------------------------------------------------------------------------
# Prospective-integrity classification
# ---------------------------------------------------------------------------

_SNAPSHOT_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})_(\d{6})")


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_snapshot_capture_timestamp(source_capture_path):
    """Best-effort parse of the capture timestamp embedded in a kalshi_search_<date>_<HHMMSS>*.json filename. None if the filename doesn't match the known convention -- never guessed."""
    if not source_capture_path:
        return None
    m = _SNAPSHOT_TS_RE.search(os.path.basename(source_capture_path))
    if not m:
        return None
    date_part, time_part = m.groups()
    try:
        return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


PROVENANCE_MIN_TOLERANCE_SECONDS = PROVENANCE_TOLERANCE_MINUTES * 60
PROVENANCE_LAG_BUFFER_SECONDS = 300
# Board generation is a serial per-hitter simulation loop against ONE
# snapshot fetched at run start (docs/HITTER_SIMULATION_ENGINE.md Sec.15) --
# a hitter simulated late in a large run can legitimately have
# projectionGeneratedAt many minutes after that snapshot's own capture
# time (2026-08-16's real board: 1213s elapsed for 72 hitters). A FIXED
# tolerance would misclassify those late-in-run, perfectly legitimate
# rows as provenance-uncertain. Instead the caller passes this run's own
# board summary elapsedSeconds (the true, measured upper bound on
# in-run drift for THIS run) plus a fixed buffer for clock skew/startup
# overhead -- never a guessed constant.
#
# NOTE on why this checks marketObservedAt, not the snapshot filename's
# embedded date: a real discrepancy was found in the archived
# 2026-08-15 board -- every row's sourceCapturePath filename embeds
# "2026-08-15", but marketObservedAt/projectionGeneratedAt on those same
# rows are real 2026-08-16T00:39-00:40Z timestamps (the run was
# triggered with --date 2026-08-15 shortly after UTC midnight; the
# filename reflects the requested SLATE date, not the wall-clock capture
# date). This is a genuine data-hygiene finding (see
# `filename_date_mismatch_count` in provenance_audit()), but it means
# the filename can NEVER be trusted as the primary contemporaneity
# signal -- marketObservedAt (the row's own record of when the priced
# market snapshot it was actually computed against was captured,
# written by the SAME ingestion code that stamps projectionGeneratedAt)
# is the correct, reliable comparison. The snapshot file's mere
# EXISTENCE on disk is still checked as an independent corroboration
# that the referenced immutable artifact is real, not fabricated.


def classify_provenance_confidence(row, repo_root=".", max_generation_lag_seconds=None):
    """
    Returns (confidence, reason). confidence is one of:
      PROSPECTIVE_VERIFIED   -- projectionGeneratedAt falls at-or-after
                                 this row's own marketObservedAt (the
                                 priced snapshot instant), within
                                 max_generation_lag_seconds (the run's
                                 own measured duration + buffer -- see
                                 module-level comment), AND the
                                 referenced Kalshi snapshot file
                                 (sourceCapturePath) is actually present
                                 on disk.
      PROVENANCE_UNCERTAIN   -- a required timestamp is missing/
                                 unparseable, generation predates its own
                                 market observation (impossible for a
                                 genuine prospective run), the gap
                                 exceeds the run's own bound, or the
                                 referenced snapshot file cannot be found.
    Never raises; never guesses a timestamp or file that isn't present.
    """
    gen_at = _parse_iso(row.get("projectionGeneratedAt"))
    if gen_at is None:
        return "PROVENANCE_UNCERTAIN", "MISSING_PROJECTION_GENERATED_AT"

    obs_at = _parse_iso(row.get("marketObservedAt"))
    if obs_at is None:
        return "PROVENANCE_UNCERTAIN", "MISSING_MARKET_OBSERVED_AT"

    source_capture_path = row.get("sourceCapturePath")
    if source_capture_path and not os.path.exists(os.path.join(repo_root, source_capture_path)):
        return "PROVENANCE_UNCERTAIN", "SNAPSHOT_FILE_NOT_FOUND_ON_DISK"

    delta_seconds = (gen_at - obs_at).total_seconds()
    tolerance = max_generation_lag_seconds if max_generation_lag_seconds is not None else PROVENANCE_MIN_TOLERANCE_SECONDS
    tolerance = max(tolerance, PROVENANCE_MIN_TOLERANCE_SECONDS) + PROVENANCE_LAG_BUFFER_SECONDS

    if delta_seconds < -60:
        # Projection claiming to have been generated BEFORE the market
        # snapshot it was priced against was even captured -- not
        # possible for a genuine run; a real, concrete red flag.
        return "PROVENANCE_UNCERTAIN", f"GENERATED_AT_PRECEDES_MARKET_OBSERVATION_BY_{-delta_seconds:.0f}S"
    if delta_seconds > tolerance:
        return "PROVENANCE_UNCERTAIN", f"GENERATED_AT_MARKET_OBSERVATION_GAP_{delta_seconds:.0f}S_EXCEEDS_TOLERANCE_{tolerance:.0f}S"
    return "PROSPECTIVE_VERIFIED", None


# ---------------------------------------------------------------------------
# Settlement join / grading
# ---------------------------------------------------------------------------

def grade_row(row, settlement):
    """
    Returns a dict with propositionOutcome ('YES'/'NO'/'PUSH'/'UNRESOLVED'/
    'VOID_DID_NOT_PLAY') and settlement evidence fields, never inventing a
    settlement. `settlement` is the joined data/edgelab/settlements row
    (or None if no row was found for this ticker on this date).
    """
    if settlement is None:
        return {
            "propositionOutcome": "UNRESOLVED",
            "unresolvedReason": "NO_SETTLEMENT_RECORD_FOUND",
            "settlementStatus": None,
            "actualValue": None,
        }

    settlement_status = settlement.get("settlementStatus")
    evidence = settlement.get("settlementEvidence") or {}
    resolution_status = evidence.get("resolutionStatus")
    outcome = settlement.get("outcome")

    if settlement_status != "SETTLED" or outcome not in ("YES", "NO"):
        reason = evidence.get("resolutionReason") or resolution_status or settlement.get("unavailableReason") or "SETTLEMENT_NOT_RESOLVED"
        # A resolved-but-non-participating player is a genuine VOID/DNP
        # candidate distinct from a parser/data failure -- but per
        # docs/PLAYER_PROP_SETTLEMENT.md Sec.6, this repo's settlement
        # pipeline itself never emits an automatic VOID for non-
        # participation (no Kalshi rules-text evidence exists to support
        # one) -- it leaves such cases SETTLEMENT_UNRESOLVED. This audit
        # mirrors that: it never invents a VOID the settlement layer
        # itself didn't produce.
        return {
            "propositionOutcome": "UNRESOLVED",
            "unresolvedReason": str(reason),
            "settlementStatus": settlement_status,
            "actualValue": evidence.get("actualValue"),
        }

    return {
        "propositionOutcome": outcome,
        "unresolvedReason": None,
        "settlementStatus": settlement_status,
        "actualValue": evidence.get("actualValue"),
        "settledAt": settlement.get("settledAt"),
        "gameStatus": evidence.get("gameStatus"),
        "hypotheticalReturnsByCheckpoint": settlement.get("hypotheticalReturnsByCheckpoint") or [],
    }


def _closing_checkpoint_yes_price(hypothetical_checkpoints):
    """Nearest-to-first-pitch pregame checkpoint yesPrice available for this ticker -- never a post-first-pitch quote (see module docstring). Returns (yesPrice, checkpointLabel) or (None, None)."""
    by_checkpoint = {c.get("checkpoint"): c.get("yesPrice") for c in hypothetical_checkpoints if c.get("yesPrice") is not None}
    for label in CLOSING_CHECKPOINT_PRIORITY:
        if label in by_checkpoint:
            return by_checkpoint[label], label
    return None, None


def build_graded_row(row, settlement, feature_index=None, repo_root=".", max_generation_lag_seconds=None):
    """The full graded_projections.jsonl record for one PROJECTED board row."""
    confidence, confidence_reason = classify_provenance_confidence(
        row, repo_root=repo_root, max_generation_lag_seconds=max_generation_lag_seconds,
    )
    grade = grade_row(row, settlement)

    model_prob = row.get("modelProbability")
    entry_price = row.get("executableKalshiPrice")
    edge = None
    if model_prob is not None and entry_price is not None:
        edge = round(model_prob - entry_price, 6)

    side = None
    if edge is not None and abs(edge) > 1e-9:
        side = "YES" if edge > 0 else "NO"

    side_entry_price = None
    if side == "YES" and entry_price is not None:
        side_entry_price = entry_price
    elif side == "NO" and entry_price is not None:
        side_entry_price = round(1.0 - entry_price, 6)

    simulated_won = None
    if side is not None and grade["propositionOutcome"] in ("YES", "NO"):
        simulated_won = (grade["propositionOutcome"] == side)

    simulated_pl = None
    if simulated_won is not None and side_entry_price is not None and 0 < side_entry_price < 1:
        simulated_pl = net_settlement_pl_fee_only(1.0, side_entry_price, simulated_won, fee_type=FEE_TYPE_TAKER)

    clv_cents = None
    closing_checkpoint_used = None
    if grade["propositionOutcome"] in ("YES", "NO") and entry_price is not None:
        closing_yes_price, closing_checkpoint_used = _closing_checkpoint_yes_price(grade.get("hypotheticalReturnsByCheckpoint") or [])
        if closing_yes_price is not None:
            entry_implied = entry_price if side != "NO" else side_entry_price
            closing_implied = closing_yes_price if side != "NO" else round(1.0 - closing_yes_price, 6)
            if entry_implied is not None:
                clv_cents = round((entry_implied - closing_implied) * 100, 4)

    feature_ctx = None
    if feature_index:
        player_id = row.get("playerId")
        if player_id is not None:
            feature_ctx = feature_index.get(str(player_id))

    segment = extract_segment_fields(feature_ctx)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "sourceDate": row.get("sourceDate"),
        "provenanceSource": row.get("provenanceSource"),
        "checkpoint": row.get("checkpoint"),
        "gameId": row.get("gameId"),
        "marketTicker": row.get("marketTicker"),
        "marketFamily": row.get("marketFamily"),
        "threshold": row.get("threshold"),
        "player": row.get("player"),
        "playerId": row.get("playerId"),
        "matchup": row.get("matchup"),
        "naturalLanguageMarket": row.get("naturalLanguageMarket"),
        "modelProbability": model_prob,
        "executableKalshiPrice": entry_price,
        "rawProbabilityEdge": row.get("rawProbabilityEdge"),
        "computedEdge": edge,
        "monteCarloStderr": row.get("monteCarloStderr"),
        "distributionUsed": row.get("distributionUsed"),
        "researchRunId": row.get("researchRunId"),
        "sourceCapturePath": row.get("sourceCapturePath"),
        "marketObservedAt": row.get("marketObservedAt"),
        "projectionGeneratedAt": row.get("projectionGeneratedAt"),
        "provenanceConfidence": confidence,
        "provenanceReason": confidence_reason,
        "propositionOutcome": grade["propositionOutcome"],
        "unresolvedReason": grade.get("unresolvedReason"),
        "actualValue": grade.get("actualValue"),
        "simulatedBetSide": side,
        "simulatedBetEntryPrice": side_entry_price,
        "simulatedBetWon": simulated_won,
        "simulatedBetNetPL": simulated_pl,
        "feeAdjustedBreakEvenProbability": fee_adjusted_break_even_probability(entry_price) if entry_price and 0 < entry_price < 1 else None,
        "closingCheckpointUsedForCLV": closing_checkpoint_used,
        "clvCents": clv_cents,
        "segment": segment,
    }


def extract_segment_fields(feature_ctx):
    """Pulls out the segmentation dimensions this repo's hitter feature board actually carries, honestly reporting UNAVAILABLE rather than guessing. See docs/EDGELAB_EVALUATION_METADATA.md-style honesty convention."""
    if not feature_ctx:
        return {
            "lineupSlot": None, "offenseSide": None, "batSide": None,
            "opposingStarterHand": None, "dataAvailable": False,
        }
    lineup = feature_ctx.get("lineupContext") or {}
    platoon = feature_ctx.get("platoonContext") or {}
    identity = feature_ctx.get("playerIdentity") or {}
    order = lineup.get("order")
    lineup_slot_bucket = None
    if isinstance(order, int):
        if order <= 3:
            lineup_slot_bucket = "TOP_1_3"
        elif order <= 6:
            lineup_slot_bucket = "MIDDLE_4_6"
        else:
            lineup_slot_bucket = "BOTTOM_7_9"
    return {
        "lineupOrder": order,
        "lineupSlot": lineup_slot_bucket,
        "offenseSide": lineup.get("offenseSide"),
        "batSide": identity.get("batSide"),
        "opposingStarterHand": platoon.get("opposingStarterHand"),
        "dataAvailable": True,
    }


# ---------------------------------------------------------------------------
# Full corpus assembly
# ---------------------------------------------------------------------------

CHECKPOINT_SNAPSHOTS_ROOT_DEFAULT = os.path.join("data", "edgelab", "hitter_projection_snapshots")

PROVENANCE_SOURCE_LEGACY_BOARD = "LEGACY_SINGLE_FILE_BOARD"
PROVENANCE_SOURCE_CHECKPOINT_SCHEDULER = "CHECKPOINT_SCHEDULER"


def discover_checkpoint_snapshot_dates(root=CHECKPOINT_SNAPSHOTS_ROOT_DEFAULT):
    """Every date with an archived data/edgelab/hitter_projection_snapshots/<date>.jsonl file -- the append-only, checkpoint-tagged corpus lib.research.hitter_prospective_snapshot produces. Sorted, [] if the directory doesn't exist yet (this system may not have run yet)."""
    if not os.path.isdir(root):
        return []
    dates = []
    for name in os.listdir(root):
        if name.endswith(".jsonl"):
            date = name[: -len(".jsonl")]
            if re.match(r"^\d{4}-\d{2}-\d{2}$", date):
                dates.append(date)
    return sorted(dates)


def load_checkpoint_snapshot_rows(date, root=CHECKPOINT_SNAPSHOTS_ROOT_DEFAULT):
    """Every HitterProjectionSnapshot row for `date`, tagged sourceDate/provenanceSource -- mirrors load_board's own row-tagging convention so downstream grading code treats both sources uniformly."""
    path = os.path.join(root, f"{date}.jsonl")
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            row["sourceDate"] = date
            row["provenanceSource"] = PROVENANCE_SOURCE_CHECKPOINT_SCHEDULER
            rows.append(row)
    return rows


def build_full_corpus(pipeline_root=PIPELINE_ROOT_DEFAULT, settlements_root=SETTLEMENTS_ROOT_DEFAULT,
                       checkpoint_snapshots_root=CHECKPOINT_SNAPSHOTS_ROOT_DEFAULT):
    """
    Discovers every archived board (the legacy single-file-per-date
    hitter_projection_board.json artifact) AND every archived checkpoint-
    tagged snapshot (data/edgelab/hitter_projection_snapshots/<date>.jsonl,
    produced by lib.research.hitter_prospective_snapshot's scheduled
    checkpoint system -- may be empty/absent until that system has run),
    loads every row (every projectionStatus, for the provenance/data-
    quality report), grades every PROJECTED row against settlement, and
    returns:
      {
        "allRows": [...],            # every row, every status, every date, both sources
        "projectedRows": [...],      # projectionStatus == PROJECTED only, both sources
        "graded": [...],             # build_graded_row() output for each projected row
        "boardSummaries": {date: summary},   # legacy-board summaries only
      }
    Every row (legacy or checkpoint-sourced) carries provenanceSource so
    downstream reports can always split by it -- this function never
    blends the two silently into an indistinguishable pool.
    """
    all_rows = []
    projected_rows = []
    board_summaries = {}
    graded = []

    for date, path in discover_projection_boards(pipeline_root):
        rows, summary = load_board(date, path)
        for row in rows:
            row["provenanceSource"] = PROVENANCE_SOURCE_LEGACY_BOARD
        board_summaries[date] = summary
        all_rows.extend(rows)
        settlement_index = load_settlement_index(date, settlements_root)
        feature_index = load_feature_index(date, pipeline_root)
        # The true measured upper bound on how long a genuine in-run
        # simulation could have taken for THIS board (see the module-level
        # comment above classify_provenance_confidence) -- never a guessed
        # constant when the board's own summary reports it.
        elapsed = (summary or {}).get("elapsedSeconds")
        max_lag = float(elapsed) if isinstance(elapsed, (int, float)) else None
        for row in rows:
            if row.get("projectionStatus") != "PROJECTED":
                continue
            projected_rows.append(row)
            settlement = settlement_index.get(row.get("marketTicker"))
            graded.append(build_graded_row(row, settlement, feature_index, max_generation_lag_seconds=max_lag))

    for date in discover_checkpoint_snapshot_dates(checkpoint_snapshots_root):
        rows = load_checkpoint_snapshot_rows(date, checkpoint_snapshots_root)
        all_rows.extend(rows)
        settlement_index = load_settlement_index(date, settlements_root)
        feature_index = load_feature_index(date, pipeline_root)
        for row in rows:
            if row.get("projectionStatus") != "PROJECTED":
                continue
            projected_rows.append(row)
            settlement = settlement_index.get(row.get("marketTicker"))
            graded.append(build_graded_row(row, settlement, feature_index))

    return {
        "allRows": all_rows,
        "projectedRows": projected_rows,
        "graded": graded,
        "boardSummaries": board_summaries,
    }


def primary_metric_rows(graded):
    """PROSPECTIVE_VERIFIED + resolved (YES/NO) rows only -- the set every calibration/ROI/CLV metric in this module is computed over, per this mission's explicit instruction not to mix uncertain-provenance rows into primary metrics."""
    return [g for g in graded if g["provenanceConfidence"] == "PROSPECTIVE_VERIFIED" and g["propositionOutcome"] in ("YES", "NO")]


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def _brier_and_logloss(rows, eps=1e-6):
    if not rows:
        return None, None
    briers, losses = [], []
    for r in rows:
        p = max(eps, min(1 - eps, r["modelProbability"]))
        y = 1.0 if r["propositionOutcome"] == "YES" else 0.0
        briers.append((p - y) ** 2)
        losses.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    return round(statistics.mean(briers), 4), round(statistics.mean(losses), 4)


def _wilson_ci(wins, n, z=1.96):
    if n == 0:
        return None, None
    p = wins / n
    denom = 1 + z ** 2 / n
    center = (p + z ** 2 / (2 * n)) / denom
    half = (z * math.sqrt((p * (1 - p) / n) + (z ** 2 / (4 * n ** 2)))) / denom
    return round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)


def independent_evidence_counts(rows):
    """
    Raw row N overstates statistical confidence for this corpus by
    construction: many rows share the same underlying game outcome
    (multiple thresholds per hitter, multiple hitters per game, per
    market family) -- see docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md
    Sec.9's identical caution ("the independent-evidence denominator
    stays ~18.5 games/day, not [row count]") and this audit's own
    summary.md Sec.12. Returns the distinct-date/game/player counts a
    reader should actually weigh sample-size claims against, alongside
    (never instead of) raw row N.
    """
    dates = {r.get("sourceDate") for r in rows if r.get("sourceDate")}
    games = {(r.get("sourceDate"), r.get("gameId") or r.get("matchup")) for r in rows if r.get("sourceDate")}
    players = {(r.get("sourceDate"), r.get("playerId") or r.get("player")) for r in rows if r.get("sourceDate")}
    return {
        "rawRowN": len(rows),
        "distinctDates": len(dates),
        "distinctGameDates": len(games),
        "distinctPlayerDates": len(players),
        "avgRowsPerGameDate": round(len(rows) / len(games), 2) if games else None,
    }


def overall_calibration(rows):
    rows = [r for r in rows if r.get("modelProbability") is not None]
    n = len(rows)
    if n == 0:
        return {"n": 0, "status": "INSUFFICIENT_SAMPLE", "avgPredictedProbability": None,
                "actualWinRate": None, "calibrationError": None, "brierScore": None, "logLoss": None,
                "independentEvidence": independent_evidence_counts(rows)}
    avg_pred = round(statistics.mean(r["modelProbability"] for r in rows), 4)
    wins = sum(1 for r in rows if r["propositionOutcome"] == "YES")
    actual_rate = round(wins / n, 4)
    brier, logloss = _brier_and_logloss(rows)
    lo, hi = _wilson_ci(wins, n)
    return {
        "n": n,
        "status": calibration_status(n),
        "avgPredictedProbability": avg_pred,
        "actualWinRate": actual_rate,
        "calibrationError": round(actual_rate - avg_pred, 4),
        "brierScore": brier,
        "logLoss": logloss,
        "actualWinRate95CI": [lo, hi],
        "independentEvidence": independent_evidence_counts(rows),
    }


def bucket_calibration(rows, buckets=PROBABILITY_BUCKETS):
    rows = [r for r in rows if r.get("modelProbability") is not None]
    out = []
    for lo, hi, label in buckets:
        bucket_rows = [r for r in rows if lo <= r["modelProbability"] < hi]
        n = len(bucket_rows)
        if n == 0:
            out.append({"bucket": label, "n": 0, "status": "INSUFFICIENT_SAMPLE",
                        "avgPredictedProbability": None, "actualWinRate": None,
                        "calibrationError": None, "actualWinRate95CI": [None, None]})
            continue
        wins = sum(1 for r in bucket_rows if r["propositionOutcome"] == "YES")
        avg_pred = round(statistics.mean(r["modelProbability"] for r in bucket_rows), 4)
        actual_rate = round(wins / n, 4)
        ci_lo, ci_hi = _wilson_ci(wins, n)
        out.append({
            "bucket": label, "n": n, "status": calibration_status(n),
            "avgPredictedProbability": avg_pred, "actualWinRate": actual_rate,
            "calibrationError": round(actual_rate - avg_pred, 4),
            "actualWinRate95CI": [ci_lo, ci_hi],
        })
    return out


def market_family_calibration(rows):
    by_family = defaultdict(list)
    for r in rows:
        by_family[r["marketFamily"]].append(r)
    return {family: overall_calibration(fam_rows) for family, fam_rows in sorted(by_family.items())}


def threshold_calibration(rows):
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r["marketFamily"], r["threshold"])].append(r)
    out = []
    for (family, threshold), key_rows in sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        stats = overall_calibration(key_rows)
        stats["marketFamily"] = family
        stats["threshold"] = threshold
        out.append(stats)
    return out


def edge_bucket_analysis(rows, buckets=EDGE_BUCKETS):
    """Calibration + simulated ROI by |edge| magnitude bucket -- answers 'do bigger edges perform better'."""
    out = []
    for lo, hi, label in buckets:
        bucket_rows = [r for r in rows if r.get("computedEdge") is not None and lo <= abs(r["computedEdge"]) < hi]
        n = len(bucket_rows)
        calib = overall_calibration(bucket_rows)
        roi = roi_simulation(bucket_rows)
        out.append({
            "edgeBucket": label, "n": n, "status": calibration_status(n),
            "avgPredictedProbability": calib["avgPredictedProbability"],
            "actualWinRate": calib["actualWinRate"],
            "calibrationError": calib["calibrationError"],
            "brierScore": calib["brierScore"],
            "roi": roi["roi"], "netPL": roi["netPL"], "qualifyingBets": roi["qualifyingBets"],
        })
    return out


# ---------------------------------------------------------------------------
# ROI / simulated betting performance
# ---------------------------------------------------------------------------

def roi_simulation(rows):
    """One-unit flat-stake simulated performance following the model's own edge-implied side, fee-adjusted via lib.edgelab.kalshi_fees.net_settlement_pl_fee_only (Tier B, fee-only-adjusted -- no double counting, no integer-contract rounding noise)."""
    qualifying = [r for r in rows if r.get("simulatedBetSide") is not None and r.get("simulatedBetWon") is not None and r.get("simulatedBetNetPL") is not None]
    n = len(qualifying)
    if n == 0:
        return {"qualifyingBets": 0, "status": "INSUFFICIENT_SAMPLE", "grossWinRate": None,
                "avgEntryPrice": None, "medianEntryPrice": None, "feeAdjustedBreakEven": None,
                "netPL": None, "roi": None, "avgEdge": None, "medianEdge": None, "maxDrawdown": None,
                "independentEvidence": independent_evidence_counts(qualifying)}

    wins = sum(1 for r in qualifying if r["simulatedBetWon"])
    entry_prices = [r["simulatedBetEntryPrice"] for r in qualifying]
    edges = [abs(r["computedEdge"]) for r in qualifying if r.get("computedEdge") is not None]
    total_pl = round(sum(r["simulatedBetNetPL"] for r in qualifying), 4)
    total_staked = float(n)  # one unit ($1) per qualifying bet

    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in sorted(qualifying, key=lambda x: (x.get("sourceDate") or "", x.get("marketObservedAt") or "")):
        running += r["simulatedBetNetPL"]
        peak = max(peak, running)
        max_dd = min(max_dd, running - peak)

    return {
        "qualifyingBets": n,
        "status": calibration_status(n),
        "grossWinRate": round(wins / n, 4),
        "avgEntryPrice": round(statistics.mean(entry_prices), 4),
        "medianEntryPrice": round(statistics.median(entry_prices), 4),
        "feeAdjustedBreakEven": round(statistics.mean(fee_adjusted_break_even_probability(p) for p in entry_prices), 4),
        "netPL": total_pl,
        "roi": round(total_pl / total_staked, 4) if total_staked else None,
        "avgEdge": round(statistics.mean(edges), 4) if edges else None,
        "medianEdge": round(statistics.median(edges), 4) if edges else None,
        "maxDrawdown": round(max_dd, 4),
        "independentEvidence": independent_evidence_counts(qualifying),
    }


def roi_by_market_family(rows):
    by_family = defaultdict(list)
    for r in rows:
        by_family[r["marketFamily"]].append(r)
    return {family: roi_simulation(fam_rows) for family, fam_rows in sorted(by_family.items())}


def roi_by_probability_bucket(rows, buckets=PROBABILITY_BUCKETS):
    out = []
    for lo, hi, label in buckets:
        bucket_rows = [r for r in rows if r.get("modelProbability") is not None and lo <= r["modelProbability"] < hi]
        stats = roi_simulation(bucket_rows)
        stats["bucket"] = label
        out.append(stats)
    return out


def roi_by_threshold(rows):
    by_key = defaultdict(list)
    for r in rows:
        by_key[(r["marketFamily"], r["threshold"])].append(r)
    out = []
    for (family, threshold), key_rows in sorted(by_key.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        stats = roi_simulation(key_rows)
        stats["marketFamily"] = family
        stats["threshold"] = threshold
        out.append(stats)
    return out


# ---------------------------------------------------------------------------
# CLV
# ---------------------------------------------------------------------------

def clv_summary(rows):
    available = [r for r in rows if r.get("clvCents") is not None]
    n = len(available)
    if n == 0:
        return {"n": 0, "status": "CLV_UNAVAILABLE", "avgClvCents": None, "medianClvCents": None, "pctPositive": None}
    values = [r["clvCents"] for r in available]
    positive = sum(1 for v in values if v > 0)
    return {
        "n": n, "totalGradedRows": len(rows), "coveragePct": round(n / len(rows), 4) if rows else None,
        "status": calibration_status(n),
        "avgClvCents": round(statistics.mean(values), 4),
        "medianClvCents": round(statistics.median(values), 4),
        "pctPositive": round(positive / n, 4),
    }


def clv_by_dimension(rows, key_fn, label_fn=None):
    by_key = defaultdict(list)
    for r in rows:
        by_key[key_fn(r)].append(r)
    out = {}
    for key, key_rows in sorted(by_key.items(), key=lambda kv: str(kv[0])):
        label = label_fn(key) if label_fn else str(key)
        out[label] = clv_summary(key_rows)
    return out


# ---------------------------------------------------------------------------
# Monotonicity / ladder quality
# ---------------------------------------------------------------------------

def monotonicity_check(projected_rows):
    """
    Groups PROJECTED rows by (sourceDate, researchRunId's hitter, marketFamily)
    -- i.e. every threshold rung the SAME simulation run priced for the SAME
    hitter in the SAME market family -- sorts by threshold, and checks
    P(stat>=N+1) <= P(stat>=N). Returns (violations, flat_ladders, all_ladders).
    """
    by_hitter_family = defaultdict(list)
    for r in projected_rows:
        if r.get("modelProbability") is None or r.get("threshold") is None:
            continue
        # Ticker shape: {SERIES}-{eventSuffix}-{playerToken}-{threshold}
        # (docs/PLAYER_PROP_SETTLEMENT.md Sec.1). Stripping only the final
        # "-{threshold}" segment (rsplit with maxsplit=1) leaves the
        # series+event+player prefix shared by every rung of the SAME
        # hitter's SAME market family in the SAME game -- exactly the
        # ladder-grouping key needed.
        ticker = r.get("marketTicker")
        hitter_key = ticker.rsplit("-", 1)[0] if ticker else r.get("player")
        key = (r.get("sourceDate"), hitter_key, r.get("marketFamily"))
        by_hitter_family[key].append(r)

    violations = []
    flat_ladders = []
    all_ladders = []
    for (date, hitter_key, family), ladder_rows in by_hitter_family.items():
        if len(ladder_rows) < 2:
            continue
        ladder_rows = sorted(ladder_rows, key=lambda r: r["threshold"])
        probs = [r["modelProbability"] for r in ladder_rows]
        thresholds = [r["threshold"] for r in ladder_rows]
        ladder_record = {
            "sourceDate": date, "player": ladder_rows[0].get("player"), "matchup": ladder_rows[0].get("matchup"),
            "marketFamily": family, "thresholds": thresholds, "probabilities": probs,
        }
        all_ladders.append(ladder_record)

        for i in range(1, len(probs)):
            if probs[i] > probs[i - 1] + 1e-9:
                violations.append(dict(ladder_record, violationAt=(thresholds[i - 1], thresholds[i]),
                                        violationDelta=round(probs[i] - probs[i - 1], 4)))
        if len(set(round(p, 4) for p in probs)) == 1 and len(probs) >= 2:
            flat_ladders.append(ladder_record)

    return {
        "totalLaddersChecked": len(all_ladders),
        "violations": violations,
        "violationCount": len(violations),
        "flatLadders": flat_ladders,
        "flatLadderCount": len(flat_ladders),
    }


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------

def segmentation_report(rows):
    dims = {
        "lineupSlot": lambda r: (r.get("segment") or {}).get("lineupSlot"),
        "offenseSide": lambda r: (r.get("segment") or {}).get("offenseSide"),
        "snapshotSourceDate": lambda r: r.get("sourceDate"),
        "provenanceSource": lambda r: r.get("provenanceSource"),
    }
    out = {}
    for dim_name, key_fn in dims.items():
        by_key = defaultdict(list)
        for r in rows:
            by_key[key_fn(r)].append(r)
        out[dim_name] = {}
        for key, key_rows in sorted(by_key.items(), key=lambda kv: str(kv[0])):
            if key is None:
                label = "UNKNOWN_OR_UNAVAILABLE"
            else:
                label = str(key)
            calib = overall_calibration(key_rows)
            roi = roi_simulation(key_rows)
            out[dim_name][label] = {"n": calib["n"], "status": calib["status"],
                                     "calibrationError": calib["calibrationError"],
                                     "actualWinRate": calib["actualWinRate"], "roi": roi["roi"],
                                     "netPL": roi["netPL"]}
    return out


def snapshot_timing_report(rows):
    """
    Calibration + ROI broken out by checkpoint label (T_MINUS_90/60/30,
    LINEUP_CONFIRMATION, HITTER_CLOSING_WINDOW -- see
    lib.research.hitter_prospective_snapshot). Legacy single-file-board
    rows (provenanceSource == LEGACY_SINGLE_FILE_BOARD) never carry a
    checkpoint label -- the hitter engine's only historical mode of
    operation was one ad hoc snapshot per manual run, with no within-day
    checkpoint diversity to compare (see this audit's own summary.md
    Sec.11) -- those rows fall into the explicit
    'LEGACY_NO_CHECKPOINT_LABEL' bucket, never silently dropped or
    merged into a real checkpoint's numbers. This report only becomes
    genuinely informative once lib.research.hitter_prospective_snapshot's
    scheduled system has accumulated real checkpoint-tagged data across
    multiple dates.
    """
    by_checkpoint = defaultdict(list)
    for r in rows:
        by_checkpoint[r.get("checkpoint") or "LEGACY_NO_CHECKPOINT_LABEL"].append(r)
    out = {}
    for checkpoint, key_rows in sorted(by_checkpoint.items(), key=lambda kv: str(kv[0])):
        calib = overall_calibration(key_rows)
        roi = roi_simulation(key_rows)
        clv = clv_summary(key_rows)
        out[checkpoint] = {
            "n": calib["n"], "status": calib["status"],
            "calibrationError": calib["calibrationError"], "actualWinRate": calib["actualWinRate"],
            "brierScore": calib["brierScore"], "roi": roi["roi"], "netPL": roi["netPL"],
            "avgClvCents": clv.get("avgClvCents"), "pctPositiveClv": clv.get("pctPositive"),
            "independentEvidence": calib["independentEvidence"],
        }
    return out


# ---------------------------------------------------------------------------
# Provenance / data-quality audit
# ---------------------------------------------------------------------------

def provenance_audit(all_rows, graded, board_summaries):
    status_counts = defaultdict(int)
    family_counts = defaultdict(int)
    date_counts = defaultdict(lambda: defaultdict(int))
    for r in all_rows:
        status_counts[r.get("projectionStatus")] += 1
        family_counts[r.get("marketFamily")] += 1
        date_counts[r.get("sourceDate")][r.get("projectionStatus")] += 1

    unresolved_reasons = defaultdict(int)
    provenance_counts = defaultdict(int)
    provenance_reason_counts = defaultdict(int)
    for g in graded:
        provenance_counts[g["provenanceConfidence"]] += 1
        if g["provenanceConfidence"] != "PROSPECTIVE_VERIFIED":
            # Collapse the numeric suffix so e.g. every
            # GENERATED_AT_MARKET_OBSERVATION_GAP_...S row groups under
            # one reason bucket instead of one bucket per exact second.
            reason = g.get("provenanceReason") or "UNKNOWN"
            reason_bucket = reason.split("_EXCEEDS_")[0] if "_EXCEEDS_" in reason else reason
            reason_bucket = re.sub(r"_\d+S$", "", reason_bucket)
            provenance_reason_counts[reason_bucket] += 1
        if g["propositionOutcome"] == "UNRESOLVED":
            unresolved_reasons[g.get("unresolvedReason")] += 1

    filename_date_mismatch_count = 0
    filename_date_mismatch_dates = set()
    for r in graded:
        snap_at = _parse_snapshot_capture_timestamp(r.get("sourceCapturePath"))
        obs_at = _parse_iso(r.get("marketObservedAt"))
        if snap_at is not None and obs_at is not None and snap_at.date() != obs_at.date():
            filename_date_mismatch_count += 1
            filename_date_mismatch_dates.add(r.get("sourceDate"))

    dates_with_boards = sorted(board_summaries.keys())
    dates_with_zero_projected = [d for d, s in board_summaries.items() if s and s.get("rowsByProjectionStatus", {}).get("PROJECTED", 0) == 0]

    return {
        "totalArchivedBoardDates": len(board_summaries),
        "boardDates": dates_with_boards,
        "totalRowsAllStatuses": len(all_rows),
        "rowsByProjectionStatus": dict(status_counts),
        "rowsByMarketFamily": dict(family_counts),
        "rowsByDateAndStatus": {d: dict(v) for d, v in date_counts.items()},
        "datesWithZeroProjectedRows": dates_with_zero_projected,
        "totalProjectedRows": len(graded),
        "provenanceConfidenceCounts": dict(provenance_counts),
        "provenanceUncertainReasonCounts": dict(provenance_reason_counts),
        "unresolvedReasonCounts": dict(unresolved_reasons),
        "nonProjectedReasonLabels": NON_PROJECTED_REASON_LABELS,
        "snapshotFilenameDateMismatch": {
            "count": filename_date_mismatch_count,
            "affectedDates": sorted(filename_date_mismatch_dates),
            "explanation": (
                "sourceCapturePath's embedded date reflects the --date the "
                "standalone run was invoked with, not necessarily the "
                "wall-clock UTC calendar date the snapshot was actually "
                "captured on (observed for the 2026-08-15 board: filenames "
                "embed 2026-08-15, but marketObservedAt/projectionGeneratedAt "
                "are real 2026-08-16T00:39-00:40Z timestamps -- the run was "
                "triggered shortly after UTC midnight). Never used as the "
                "provenance gate for this reason -- see "
                "classify_provenance_confidence's module comment."
            ),
        },
    }
