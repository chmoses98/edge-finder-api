"""
lib/edgelab/temporal_alignment.py
=====================================
EdgeLab Research Trustworthiness milestone: the ONE no-look-ahead join
primitive for matching a ModelEvaluation to a market observation/
checkpoint by TIME, not merely by marketTicker.

Why this module exists (verified against real code and data, not
assumed -- see docs/EDGELAB_PHASE2_DESIGN.md and this milestone's own
audit): lib.edgelab.query.build_research_rows currently picks
`evals_for_ticker[-1]` -- the last element of an UNORDERED list -- as
"the" evaluation for every observation of that ticker, regardless of
when that evaluation was produced relative to when the price was
captured. That is a look-ahead bug waiting to happen (a later
evaluation, or simply the wrong list-order element, could get compared
against an earlier price) and, separately, cannot be trusted to pick a
consistent element at all since Python list order here is whatever
order the caller happened to load rows in.

Two ModelEvaluation timestamp candidates exist, and only one is causal:
  - ModelEvaluation.createdAt is EdgeLab's own INGESTION timestamp (when
    this repo's ingest step read the upstream artifact), not the
    model's decision time. Confirmed against real data: multiple
    ModelEvaluation rows for the same date share the exact same
    createdAt (stamped once per ingestion batch, second-resolution),
    so it cannot even disambiguate same-day rows, let alone serve as a
    trustworthy "when did the model decide" signal.
  - ModelEvaluation.pipelineRunId is copied verbatim from the upstream
    recommendations.json artifact's own `meta.createdAt` -- the
    timestamp the PRODUCTION PIPELINE stamped onto its output the
    moment it actually ran and froze that day's fair probabilities
    (lib.edgelab.model_evaluation.py's own docstring: "keyed by the
     source artifact's own meta.createdAt"). Confirmed against every
    real committed data/edgelab/model_evaluations/*.jsonl file: each
    date has exactly one non-null pipelineRunId value shared by every
    real (non-full-universe) row that date -- i.e. the pipeline runs
    once per day, and pipelineRunId is that run's own immutable,
    already-written-to-disk-before-EdgeLab-ever-ingested-it moment.
    This is the closest thing this repository has to a provably causal
    "when did the model decide" timestamp, so this module uses it, and
    ONLY it, for temporal eligibility -- never createdAt.

The rule (spec section 5): for a market observation/checkpoint captured
at time T, a ModelEvaluation is eligible only if its pipelineRunId is
<= T. An evaluation with no pipelineRunId at all (e.g. a NOT_EVALUATED/
full-universe-extension row, which is never pipeline-produced -- see
model_evaluation.py's own `"pipelineRunId": None` for that path) can
never be proven causal and is therefore never eligible here -- this
module does not fall back to createdAt or invent a timestamp.
"""

from datetime import datetime, timezone


def _parse_iso(value):
    """Returns a tz-aware UTC datetime, or None for a falsy/unparseable value -- never raises."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# Reasons select_temporally_valid_evaluation() reports for why NO
# evaluation was selected -- always one of these, never a bare None with
# no explanation, so a caller/report can distinguish "no model opinion
# existed at all" from "a model opinion existed but isn't provably
# causal for this checkpoint" from "genuinely evaluated in the future
# relative to this checkpoint".
NO_EVALUATIONS_FOR_TICKER = "NO_EVALUATIONS_FOR_TICKER"
NO_CAUSAL_TIMESTAMP = "NO_CAUSAL_TIMESTAMP_ON_ANY_CANDIDATE"
ALL_EVALUATIONS_AFTER_CHECKPOINT = "ALL_EVALUATIONS_AFTER_CHECKPOINT"
OBSERVATION_TIMESTAMP_UNPARSEABLE = "OBSERVATION_TIMESTAMP_UNPARSEABLE"


def _selection_key(evaluation):
    """(selection, side, threshold) -- identifies WHICH bettable expression of a ticker this evaluation is for, not merely which ticker."""
    return (evaluation.get("selection"), evaluation.get("side"), evaluation.get("threshold"))


def select_temporally_valid_evaluation(evaluations_for_ticker, observation_captured_at):
    """
    The single no-look-ahead selector every research-dataset/backtest
    consumer must use instead of an unordered `evals_for_ticker[-1]`
    pick. `evaluations_for_ticker`: every ModelEvaluation row already
    filtered to one marketTicker (any order -- this function does not
    trust caller ordering). `observation_captured_at`: the checkpoint's
    own capturedAt (str or datetime).

    Returns (selected_evaluation_or_None, candidates, reason):
      - selected_evaluation_or_None: the single ModelEvaluation to use,
        chosen as the LATEST-pipelineRunId evaluation that is still
        <= observation_captured_at among evaluations sharing the WINNING
        (selection, side, threshold) key (see below) -- never a future
        evaluation, per spec section 5's explicit rule. None when no
        evaluation qualifies.
      - candidates: every temporally-eligible evaluation (pipelineRunId
        <= T), across ALL (selection, side, threshold) keys -- so a
        caller can see every side the model actually evaluated for this
        ticker as of T, not just the chosen primary one. Empty when
        `selected_evaluation_or_None` is None.
      - reason: None when a selection succeeded; otherwise one of this
        module's NO_*/ALL_* constants explaining why not -- a caller
        must record this, never silently treat a None selection as "no
        opinion" without the reason attached.

    Disambiguation when multiple DISTINCT (selection, side, threshold)
    keys are temporally eligible for the same ticker at T (confirmed
    real-data case: a spread ticker's away-side and home-side are two
    separate ModelEvaluation rows sharing one ticker) -- the PRIMARY
    selection is the side whose own side/selection resolves to 'YES'
    when known, else the lexicographically first (selection, side,
    threshold) tuple, for a fully deterministic (never random-order-
    dependent) pick; every other key's latest-eligible row is still
    returned in `candidates` so nothing is silently dropped.
    """
    if not evaluations_for_ticker:
        return None, [], NO_EVALUATIONS_FOR_TICKER

    checkpoint_dt = _parse_iso(observation_captured_at)
    if checkpoint_dt is None:
        return None, [], OBSERVATION_TIMESTAMP_UNPARSEABLE

    any_has_timestamp = False
    eligible = []  # (pipeline_dt, evaluation)
    for ev in evaluations_for_ticker:
        pipeline_dt = _parse_iso(ev.get("pipelineRunId"))
        if pipeline_dt is None:
            continue
        any_has_timestamp = True
        if pipeline_dt <= checkpoint_dt:
            eligible.append((pipeline_dt, ev))

    if not any_has_timestamp:
        return None, [], NO_CAUSAL_TIMESTAMP
    if not eligible:
        return None, [], ALL_EVALUATIONS_AFTER_CHECKPOINT

    # Latest-eligible evaluation per distinct (selection, side, threshold) key.
    latest_by_key = {}
    for pipeline_dt, ev in eligible:
        key = _selection_key(ev)
        current = latest_by_key.get(key)
        if current is None or pipeline_dt > current[0]:
            latest_by_key[key] = (pipeline_dt, ev)

    candidates = [ev for _, ev in latest_by_key.values()]

    def _key_sort(key):
        selection, side, threshold = key
        return (side != "YES", selection or "", threshold if threshold is not None else float("-inf"))

    primary_key = sorted(latest_by_key.keys(), key=_key_sort)[0]
    selected = latest_by_key[primary_key][1]
    return selected, candidates, None
