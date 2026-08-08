"""
lib/edgelab/model_evaluation.py
====================================
EdgeLab Phase 2 Milestone 3 (docs/EDGELAB_MODEL_EVALUATION.md): the
first-class, durable ModelEvaluation ledger -- closing the write-path gap
Milestone 2 surfaced (no settled bet carries modelFairProbability because
nothing durably records what the model actually evaluated, independent of
whether a bet was ever placed on it).

Reads the exact same source artifact lib.edgelab.recommendations already
reads (data/pipeline/<date>/recommendations.json's data.games[].marketLedger
rows) -- this module does NOT recompute any model math. It only persists,
for every row the model's pipeline already produced, whatever fair
probability/edge/confidence that row already carries, plus an explicit
evaluationStatus classifying why a probability is or isn't trustworthy.
"Do not fabricate unavailable values" (Milestone 3 scope item 3): every
field below is either copied verbatim from the source row, looked up from
an already-captured MarketObservation, or left null.

Shares Recommendation's exact ID-and-versioning scheme (Phase 1 section G):
pipeline-derived rows are keyed by the source artifact's own
meta.createdAt (not this script's run timestamp), so re-ingesting an
already-finalized recommendations.json is a pure no-op; full-universe rows
are keyed by (date, marketTicker) and upserted, since there's no decision
content to version for a market the model never touches at all.
"""

import json
import os
import subprocess

from lib.edgelab import ids
from lib.edgelab import DEFAULT_PLATFORM, DEFAULT_SPORT, SCHEMA_VERSION
from lib.edgelab.tags import validate_tags
from lib.pipeline_artifacts import read_stage_artifact, stage_artifact_exists
from lib.rules_config import load_rules_config, RULES_PATH as RULES_CONFIG_PATH
from scripts.clv_from_snapshot import implied_to_american

# Evaluation-status values this module can assign -- see
# data/edgelab/schema_v1/model_evaluation.schema.json for the meaning of
# each. Ordered here roughly by "how much of a real evaluation exists",
# most complete first, purely for readability.
EVALUATED = "EVALUATED"
PARTIAL_EVALUATION = "PARTIAL_EVALUATION"
NO_MODEL_SUPPORT = "NO_MODEL_SUPPORT"
NOT_EVALUATED = "NOT_EVALUATED"
INVALID_PROBABILITY = "INVALID_PROBABILITY"
MISSING_MARKET_PRICE = "MISSING_MARKET_PRICE"
DATA_QUALITY_BLOCK = "DATA_QUALITY_BLOCK"
PARSER_UNRESOLVED = "PARSER_UNRESOLVED"

# Which upstream script produced the marketLedger rows this module reads
# -- taken verbatim from the artifact's own meta.producedBy (never
# invented), used as ModelEvaluation.modelSource for pipeline-derived rows.
_FALLBACK_MODEL_SOURCE = "scripts/build_market_ledger.py"

RULES_CONFIG_PATH = os.path.join("config", "rules.json")

# In priority order -- the first of these that's non-null on a row is both
# the value used for marketImpliedProbability AND named verbatim as
# ModelEvaluation.probabilityAdapter (Milestone 4 scope item 4).
_MARKET_IMPLIED_PROBABILITY_FIELDS = ("kalshiVF", "marketProbVF", "executableMarketProb")


def _market_implied_probability_with_adapter(row):
    for field in _MARKET_IMPLIED_PROBABILITY_FIELDS:
        value = row.get(field)
        if value is not None:
            return value, field
    return None, None


def _market_implied_probability(row):
    return _market_implied_probability_with_adapter(row)[0]


def _confidence_with_source(row):
    """confidenceTier is the pipeline's primary tier field; a bare 'confidence' key is checked as a fallback, same priority build_recommendations_from_pipeline already uses."""
    if row.get("confidenceTier") is not None:
        return row["confidenceTier"], "confidenceTier"
    if row.get("confidence") is not None:
        return row["confidence"], "confidence"
    return None, None


def _data_quality_reasons(row):
    """Every reason copied verbatim from the row's own missingFields/lineupStatusReason -- never fabricated, never summarized/reworded."""
    reasons = list(row.get("missingFields") or [])
    status_reason = row.get("lineupStatusReason")
    if status_reason:
        reasons.append(status_reason)
    return reasons


def _git_commit_sha():
    """
    The git commit SHA of THIS repo checkout at the moment this function
    runs -- GITHUB_SHA (set automatically in every GitHub Actions job) when
    running in Actions, else a local `git rev-parse HEAD` for manual/dev
    runs. This is the ingestion-time commit (the code that wrote this
    ModelEvaluation record), not necessarily the commit that produced the
    upstream recommendations.json artifact -- fetch-slate.yml (which
    builds it) and edgelab-postgame.yml (which ingests it) are separate
    workflow runs that can straddle a same-day push. Documented as a
    precision limitation, never claimed as more than it is. Returns None
    (never a fabricated placeholder) if neither source is available.
    """
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _model_config_version(rules_path=RULES_CONFIG_PATH):
    """
    config/rules.json's own "_version" field -- a real, existing,
    manually-bumped config version. None if the file or field is
    unavailable. Deliberately non-strict (strict=False): this is a
    metadata tag for provenance, not a gate on production behavior, so
    a config that's mid-edit or otherwise structurally incomplete still
    yields whatever "_version" it has rather than blocking ingestion --
    the hard structural gate lives in
    lib.edgelab.recommendations.load_model_covered_series, the one
    reader whose output actually changes what the pipeline does.
    """
    try:
        return load_rules_config(rules_path, strict=False).get("_version")
    except (OSError, json.JSONDecodeError):
        return None


def _estimated_edge(row):
    edge = row.get("calibratedEdgeVsExecutable")
    if edge is None:
        edge = row.get("edge")
    return edge


def _model_version_for_row(row):
    """
    F5 Three-Way Pricing Correction milestone (item 9 -- versioning and
    provenance): scripts/build_market_ledger.py stamps every F5_ML_Away/
    F5_ML_Home row it evaluates with f5PricingVersion (currently always
    F5_PRICING_VERSION_CURRENT = "f5_three_way_v1" -- there is no
    production code path left that produces the legacy value). Copied
    here verbatim, never invented -- other market families have no
    versioning concept yet and modelVersion stays None for them, exactly
    as before this milestone. A historical ModelEvaluation record with
    modelVersion=None therefore unambiguously predates this fix (see
    docs/F5_THREE_WAY_PRICING.md's "Historical data handling" section):
    every new one for an F5 row carries an explicit, non-null version.
    """
    return row.get("f5PricingVersion")


def _ev_per_dollar(row):
    """
    F5 Three-Way Pricing Correction milestone: contract_pricing()
    (scripts/build_market_ledger.py) computes expectedValuePerDollar for
    F5_ML_Away/F5_ML_Home, nested under f5ContractPricing (this row's
    own side -- never the sibling side or the Tie leg, which has no
    Recommendation/ModelEvaluation row of its own). Copied here verbatim
    when present -- previously this field was hardcoded None for every
    row unconditionally, even though the upstream data already existed
    for roughly all F5 rows (a real, silently-dropped field, not a
    "legitimately not computed" gap). No other market family has a
    per-dollar EV concept computed anywhere in the pipeline, so
    evPerDollar correctly stays None for every non-F5 row, never
    fabricated.
    """
    return (row.get("f5ContractPricing") or {}).get("expectedValuePerDollar")


def classify_evaluation_status(row):
    """
    Pure function of one marketLedger row -> one of the 7 evaluationStatus
    values. Deliberately independent of the row's own `status` field
    (Missing Data/Rejected/Accepted) wherever the model's own probability
    already answers the question directly: a "Rejected" row (the model
    DID produce a fair probability; a later edge-threshold/portfolio rule
    declined to bet it) is just as fully EVALUATED as an "Accepted" one --
    rejection is a Recommendation-level decision, not evidence the model
    itself failed to evaluate the market. `row.get("status")` is only
    consulted as a last resort, when the row carries no modelProb at all,
    to distinguish why.

    A present modelProb is never downgraded just because this row's own
    ticker/marketTicker field is empty: confirmed against real committed
    data/pipeline/*/recommendations.json artifacts, a Rejected ML_Away/
    ML_Home/F5_ML_Away/F5_ML_Home/NRFI/YRFI row's ticker is genuinely
    missing (scripts/build_market_ledger.py's rejected_row() calls for
    those specific markets don't thread `ticker=`/`**identity(...)`
    through, unlike RL_Away/RL_Home/Game_Total/TT_Away_Over/TT_Home_Over,
    which do) even though kalshiPrice/kalshiVF/edge are all populated --
    proof the market WAS genuinely priced and evaluated, not that its
    identity couldn't be resolved. This was previously misclassified
    PARSER_UNRESOLVED (~53% of real committed records with a modelProb --
    see lib.edgelab.market_comparison's now-historical note on this exact
    finding), which wrongly implies the pipeline couldn't even parse the
    contract; PARSER_UNRESOLVED is reserved for an actual recorded parse
    failure (see the "Evaluation Failed" branch below). ModelEvaluation.
    marketTicker (required, non-null) still falls back to the synthetic
    (runKey:marketKey) identifier for these rows -- see
    build_model_evaluations_from_pipeline -- so nothing downstream ever
    sees a missing marketTicker.
    """
    model_prob = row.get("modelProb")
    if model_prob is not None:
        if not (0 < model_prob < 100):
            return INVALID_PROBABILITY
        if _market_implied_probability(row) is None:
            return MISSING_MARKET_PRICE
        if _estimated_edge(row) is None:
            return PARTIAL_EVALUATION
        return EVALUATED

    row_status = row.get("status")
    if row_status == "Evaluation Failed":
        err = (row.get("evaluationError") or "").lower()
        if "pars" in err or "ticker" in err:
            return PARSER_UNRESOLVED
        return DATA_QUALITY_BLOCK
    if row_status == "Missing Data":
        missing = " ".join(row.get("missingFields") or []).lower()
        if "kalshi" in missing or "odds" in missing or "price" in missing:
            return MISSING_MARKET_PRICE
        return DATA_QUALITY_BLOCK
    return NO_MODEL_SUPPORT


def _lineup_confirmation_state(row):
    """
    Maps the marketLedger row's own lineup fields (all set by
    scripts/fetch_lineups.py) to the 5-value controlled vocabulary
    (Milestone 4 scope item 5): CONFIRMED, PROJECTED, PARTIAL,
    UNCONFIRMED, UNKNOWN.

    - CONFIRMED: lineupConfirmedOfficial=True and lineupDataQuality=='full'
      (the official MLB Stats API batting order posted, fully resolved).
    - PARTIAL: either an official-but-incompletely-resolved lineup
      (lineupConfirmedOfficial=True but quality not 'full'), or a real,
      posted-but-non-official lineup with partial/insufficient quality.
    - PROJECTED: a real, posted lineup exists but isn't official yet
      (e.g. a projected/probable lineup from a secondary source), or
      lineupStatus carries any non-"missing" value without the official
      flag being set.
    - UNCONFIRMED: lineup information was actively sought and the check
      came back negative (lineupStatus=="missing", or lineupPosted is
      explicitly False with no other status set).
    - UNKNOWN: no lineup evidence fields are present at all (checked
      too early before any lineup-fetch attempt for this game).

    Verified against the only three field combinations observed in real
    committed data (tests/edgelab/test_model_evaluation.py) -- the
    partial/insufficient branches are reachable per fetch_lineups.py's
    own documented logic even though not yet observed in the two real
    committed dates.
    """
    quality = row.get("lineupDataQuality")
    confirmed_official = row.get("lineupConfirmedOfficial")
    posted = row.get("lineupPosted")
    status = (row.get("lineupStatus") or "").strip().lower()

    if confirmed_official is True:
        return "CONFIRMED" if quality == "full" else "PARTIAL"
    if posted is True:
        return "PARTIAL" if quality in ("partial", "insufficient") else "PROJECTED"
    if status == "missing":
        # The one real "actively checked, not available" status value
        # observed in production (scripts/fetch_lineups.py).
        return "UNCONFIRMED"
    if status:
        return "PROJECTED"
    if posted is False:
        return "UNCONFIRMED"
    return "UNKNOWN"


# ── Thesis tags (Milestone 4 scope item 6) ───────────────────────────────
#
# Every tag below is gated on an explicit, already-computed pipeline
# field crossing an explicit, documented threshold -- never inferred from
# an outcome, never a new handicapping computation. Per the Milestone 4
# audit (docs/EDGELAB_EVALUATION_METADATA.md), roughly a third of the
# controlled vocabulary (PLATOON_EDGE, WEATHER_OVER/UNDER, UMPIRE_FACTOR,
# WORKLOAD_OVER/UNDER, STRIKEOUT_MATCHUP, CONTACT_MATCHUP,
# CORRELATED_POSITION) has NO real producer anywhere in the production
# pipeline today -- those tags are never assigned, by design, not by
# oversight.

# market name -> (own-side-object-key, opponent-side-object-key) for the
# two-sided markets that have a clear "which team does this row back" --
# used by both the bullpen tags below and correlation_groups_for_row().
_SIDE_MARKETS = {
    "ML_Away": ("away", "home"), "ML_Home": ("home", "away"),
    "F5_ML_Away": ("away", "home"), "F5_ML_Home": ("home", "away"),
}


def thesis_tags_and_evidence_for_row(row, game):
    """
    Returns (tags: list[str], evidence: dict[tag -> str]) for one
    marketLedger row + its parent game object. Every assigned tag has a
    one-line evidence string citing the exact source field(s)/value(s)
    that justified it (Milestone 4's per-tag provenance requirement).
    Every tag returned is validated against the Phase 1 controlled
    vocabulary (lib.edgelab.tags.validate_tags) before returning -- a
    typo here must fail loudly, never silently write an unrecognized tag.
    """
    market = row.get("market") or ""
    away = game.get("away") or {}
    home = game.get("home") or {}
    f5 = game.get("f5") or {}
    team_totals = game.get("teamTotals") or {}
    park = game.get("park") or {}
    reason_codes = set(row.get("reasonCodes") or [])

    tags = []
    evidence = {}

    def add(tag, reason):
        tags.append(tag)
        evidence[tag] = reason

    # STARTER_EDGE / STARTER_FADE -- game.f5.xERAGap/f5Amplified/favoredSide
    # (scripts/build_market_ledger.py's xera_gap = abs(away_xfip - home_xfip),
    # f5Amplified = xera_gap >= 1.5), scoped to the exact F5 ML markets this
    # signal is about.
    if market in ("F5_ML_Away", "F5_ML_Home") and f5.get("f5Amplified") is True:
        side = "AWAY" if market == "F5_ML_Away" else "HOME"
        favored = f5.get("favoredSide")
        gap = f5.get("xERAGap")
        if favored == side:
            add("STARTER_EDGE", f"f5.xERAGap={gap} (>=1.5, f5Amplified); f5.favoredSide={favored} matches this side")
        elif favored is not None:
            add("STARTER_FADE", f"f5.xERAGap={gap} (>=1.5, f5Amplified); f5.favoredSide={favored} opposes this side")

    # BULLPEN_EDGE / BULLPEN_DISADVANTAGE -- away/home.bullpen.vulnerable
    # (api/bullpen.js: vulnerable = xFIP > 4.50), scoped to markets with a
    # clear side.
    if market in _SIDE_MARKETS:
        own_key, opp_key = _SIDE_MARKETS[market]
        own_side = {"away": away, "home": home}[own_key]
        opp_side = {"away": away, "home": home}[opp_key]
        own_bp = own_side.get("bullpen") or {}
        opp_bp = opp_side.get("bullpen") or {}
        if opp_bp.get("vulnerable") is True:
            add("BULLPEN_EDGE", f"opponent bullpen.vulnerable=True (xFIP={opp_bp.get('xFIP')})")
        if own_bp.get("vulnerable") is True:
            add("BULLPEN_DISADVANTAGE", f"own bullpen.vulnerable=True (xFIP={own_bp.get('xFIP')})")

    # LINEUP_EDGE / LINEUP_DOWNGRADE -- scripts/fetch_lineups.py's own
    # lineupConfirmedOfficial/lineupAdjApplied/lineupDataQuality fields.
    if row.get("lineupConfirmedOfficial") is True and row.get("lineupAdjApplied") is True and row.get("lineupDataQuality") == "full":
        add("LINEUP_EDGE", "lineupConfirmedOfficial=True, lineupAdjApplied=True, lineupDataQuality=full")
    elif row.get("lineupDataQuality") in ("partial", "insufficient") and (row.get("lineupPosted") or row.get("lineupSource")):
        add("LINEUP_DOWNGRADE", f"lineupDataQuality={row.get('lineupDataQuality')!r} with real (incomplete) lineup evidence present")

    # PRICE_DISLOCATION -- scripts/reason_codes.py's own controlled
    # RAW_EDGE_STRONG code, already computed and populated on real rows.
    if "RAW_EDGE_STRONG" in reason_codes:
        add("PRICE_DISLOCATION", "reasonCodes contains RAW_EDGE_STRONG")

    # MARKET_EXPRESSION -- game.teamTotals.{away,home}TTReason, a real,
    # already-computed rationale string (api/slate.js), non-null only
    # when the pipeline itself decided one was needed.
    tt_reason_field = {"TT_Away_Over": "awayTTReason", "TT_Home_Over": "homeTTReason"}.get(market)
    if tt_reason_field:
        reason = team_totals.get(tt_reason_field)
        if reason:
            add("MARKET_EXPRESSION", f"teamTotals.{tt_reason_field}={reason!r}")

    # F5_OVER_FULL_GAME -- this F5 expression is the amplified/preferred
    # one over the full-game equivalent, per the same f5Amplified flag.
    if market.startswith("F5_") and f5.get("f5Amplified") is True:
        add("F5_OVER_FULL_GAME", f"f5.f5Amplified=True (xERAGap={f5.get('xERAGap')})")

    # PARK_FACTOR -- game.park.parkFactor, a real (if static) per-park
    # number; only meaningful for total-type markets, and only tagged
    # when it actually deviates from the 100=league-average baseline.
    if market in ("Game_Total", "TT_Away_Over", "TT_Home_Over"):
        park_factor = park.get("parkFactor")
        if park_factor is not None and park_factor != 100:
            add("PARK_FACTOR", f"park.parkFactor={park_factor} (park={park.get('name')})")

    validate_tags(tags)
    return tags, evidence


# ── Correlation groups (Milestone 4 scope item 8) ────────────────────────
#
# Purely deterministic name-based grouping -- NO numerical correlation
# estimate, and never used to filter recommendations or size stakes (see
# docs/EDGELAB_EVALUATION_METADATA.md). Two-sided single-ticker markets
# (e.g. a run-line spread's RL_Away/RL_Home) each map to THEIR OWN team's
# group, not a shared one -- this is deliberate: Rule 76's actual concern
# ("ML + RL + F5 + TT on the same team are the same bet") is exactly
# captured by RL_Away and ML_Away both landing in GAME_SIDE_<away>.
# Alternate-line markets on the same underlying team/direction (e.g. a
# team total at a different threshold) intentionally collapse into the
# same group regardless of `threshold` -- they're the same directional
# thesis at a different price, not an independent one.

def correlation_groups_for_row(row, game):
    market = row.get("market") or ""
    away = game.get("away") or {}
    home = game.get("home") or {}
    away_abbr = away.get("abbr")
    home_abbr = home.get("abbr")
    away_pitcher = (away.get("pitcher") or {}).get("name")
    home_pitcher = (home.get("pitcher") or {}).get("name")

    groups = []

    def side_groups(prefix, own_abbr, own_pitcher, opp_pitcher):
        if own_abbr:
            groups.append(f"{prefix}_{own_abbr}")
        if own_pitcher:
            groups.append(f"STARTER_SUCCESS_{own_pitcher}")
        if opp_pitcher:
            groups.append(f"STARTER_FAILURE_{opp_pitcher}")

    if market == "ML_Away":
        side_groups("GAME_SIDE", away_abbr, away_pitcher, home_pitcher)
    elif market == "ML_Home":
        side_groups("GAME_SIDE", home_abbr, home_pitcher, away_pitcher)
    elif market == "F5_ML_Away":
        side_groups("F5_SIDE", away_abbr, away_pitcher, home_pitcher)
    elif market == "F5_ML_Home":
        side_groups("F5_SIDE", home_abbr, home_pitcher, away_pitcher)
    elif market == "RL_Away" and away_abbr:
        groups.append(f"GAME_SIDE_{away_abbr}")
    elif market == "RL_Home" and home_abbr:
        groups.append(f"GAME_SIDE_{home_abbr}")
    elif market == "TT_Away_Over" and away_abbr:
        groups.append(f"TEAM_RUNS_OVER_{away_abbr}")
    elif market == "TT_Home_Over" and home_abbr:
        groups.append(f"TEAM_RUNS_OVER_{home_abbr}")
    elif market == "Game_Total":
        # config/rules.json's market_list has exactly one Game_Total
        # entry (no paired "Game_Total_Under") -- by this config's own
        # naming convention, the unqualified name represents the over
        # expression, same as TT_*_Over's explicit suffix.
        groups.append("GAME_OVER")
    elif market == "YRFI":
        groups.append("YRFI")
    elif market == "NRFI":
        groups.append("NRFI")

    return groups


def _model_fair_odds(model_fair_probability):
    if model_fair_probability is None:
        return None
    return implied_to_american(model_fair_probability / 100.0)


def _ticker_lookup_from_observations(observations):
    """{marketTicker: (eventTicker, seriesTicker)} built from already-captured
    MarketObservation rows for this date -- reused, never re-parsed."""
    lookup = {}
    for obs in observations:
        ticker = obs.get("marketTicker")
        if ticker and ticker not in lookup:
            lookup[ticker] = (obs.get("eventTicker"), obs.get("seriesTicker"))
    return lookup


def build_model_evaluations_from_pipeline(date, run_id, observations):
    """
    One ModelEvaluation per data/pipeline/<date>/recommendations.json
    marketLedger row -- every row, not just Accepted/Rejected ones, so a
    market the model looked at but couldn't evaluate (Missing Data,
    Evaluation Failed) is still durably recorded with its reason. Returns
    (records, warnings); empty records + a warning if the artifact
    doesn't exist yet, mirroring
    lib.edgelab.recommendations.build_recommendations_from_pipeline
    exactly (same source file, same non-fabrication contract).

    Returns records keyed by the same (source_run_key, market_key)
    scheme lib.edgelab.recommendations uses for recommendationId, so
    lib.edgelab.recommendations can look up "the ModelEvaluation for
    this exact row" by recomputing the identical key -- see
    build_recommendation_and_evaluation_ids().
    """
    if not stage_artifact_exists("recommendations", date):
        return [], [f"no data/pipeline/{date}/recommendations.json artifact"]

    rec_env = read_stage_artifact("recommendations", date)
    source_run_key = rec_env["meta"]["createdAt"]
    model_source = rec_env["meta"].get("producedBy") or _FALLBACK_MODEL_SOURCE
    artifact_source = rec_env["meta"].get("stage")
    games = (rec_env.get("data") or {}).get("games") or []
    ticker_lookup = _ticker_lookup_from_observations(observations)

    # Ingestion-time facts, computed once per run (not per row) -- see
    # docs/EDGELAB_EVALUATION_METADATA.md for what modelCommitSha/
    # modelConfigVersion do and don't mean.
    commit_sha = _git_commit_sha()
    config_version = _model_config_version()

    now = ids.utc_now_iso()
    source_file = os.path.join("data", "pipeline", date, "recommendations.json")
    records = []

    for g in games:
        game_id = g.get("gameId")
        away = (g.get("away") or {}).get("abbr")
        home = (g.get("home") or {}).get("abbr")

        for row in g.get("marketLedger") or []:
            market_name = row.get("market")
            ticker = row.get("ticker") or row.get("marketTicker")
            # market_name is always part of the key -- see the identical
            # comment in lib.edgelab.recommendations.build_recommendations_from_pipeline;
            # both modules must derive the same market_key for the same
            # row so their IDs cross-link correctly.
            market_key = f"{ticker}:{market_name}" if ticker else f"{game_id}:{market_name}"
            evaluation_status = classify_evaluation_status(row)
            model_fair_probability = row.get("modelProb") if evaluation_status in (EVALUATED, PARTIAL_EVALUATION) else None
            observed_event_ticker, observed_series_ticker = ticker_lookup.get(ticker, (None, None))
            market_implied_probability, probability_adapter = (
                _market_implied_probability_with_adapter(row) if evaluation_status in (EVALUATED, PARTIAL_EVALUATION) else (None, None)
            )
            confidence, confidence_source = _confidence_with_source(row)
            tags, evidence = thesis_tags_and_evidence_for_row(row, g)
            correlation_groups = correlation_groups_for_row(row, g)

            records.append({
                "schemaVersion": SCHEMA_VERSION,
                "modelEvaluationId": ids.build_model_evaluation_id(source_run_key, market_key),
                "runId": run_id,
                "gameId": game_id,
                "sport": DEFAULT_SPORT,
                "platform": DEFAULT_PLATFORM,
                "marketTicker": ticker or market_key,
                "eventTicker": observed_event_ticker,
                "seriesTicker": observed_series_ticker or row.get("seriesTicker"),
                # Raw value as evaluated, canonicalized at query time via
                # lib.edgelab.market_family_mapping, never rewritten here
                # (see that module's docstring). MARKET_FAMILY_ALIASES
                # already recognizes every REQUIRED_MARKETS market name
                # (e.g. "NRFI", "F5_ML_Away") as a raw spelling, so falling
                # back to it here -- instead of leaving marketFamily null
                # whenever a Rejected row's ticker wasn't threaded through
                # scripts/build_market_ledger.py -- fills a real gap with a
                # value the canonicalization layer already understands.
                "marketFamily": ticker.split("-", 1)[0] if ticker else market_name,
                "selection": market_name,
                "side": None,
                "threshold": row.get("line"),
                "evaluationStatus": evaluation_status,
                "modelFairProbability": model_fair_probability,
                "modelFairOdds": _model_fair_odds(model_fair_probability),
                "modelVersion": _model_version_for_row(row),
                "modelCommitSha": commit_sha,
                "modelConfigVersion": config_version,
                "probabilityAdapter": probability_adapter,
                "modelSource": model_source,
                "calibrationVersion": None,
                "pipelineRunId": source_run_key,
                "artifactSource": artifact_source,
                "marketImpliedProbability": market_implied_probability,
                "estimatedEdge": _estimated_edge(row) if evaluation_status == EVALUATED else None,
                "evPerDollar": _ev_per_dollar(row),
                "confidence": confidence,
                "confidenceSource": confidence_source,
                "lineupConfirmationState": _lineup_confirmation_state(row),
                "dataQuality": row.get("lineupDataQuality"),
                "dataQualityReasons": _data_quality_reasons(row),
                "thesisTags": tags,
                "tagEvidence": evidence,
                "correlationGroups": correlation_groups,
                "recommendationId": ids.build_recommendation_id(source_run_key, market_key),
                "createdAt": now,
                "source": "pipeline_recommendations",
                "validationStatus": "valid",
                "provenance": {
                    "sourceSystem": "pipeline_recommendations",
                    "sourceFile": source_file,
                    "sourceKey": f"{away}@{home}|{market_name}",
                    "capturedAt": source_run_key,
                    "ingestedAt": now,
                },
            })

    return records, []


def extend_full_universe_evaluations(covered_tickers, observations, date, model_covered_series=None):
    """
    One ModelEvaluation per observed marketTicker NOT already covered by
    a pipeline-derived evaluation. Mirrors
    lib.edgelab.recommendations.extend_with_full_universe's own
    (date, marketTicker)-keyed upsert scheme (one current row per market
    per day, not versioned per run -- there's no decision content to
    version for a market the model never touches).

    model_covered_series (optional, defaults to no series considered
    covered -- preserves the exact prior behavior for any caller that
    doesn't pass it, e.g. an old test): when an extension row's own
    series IS one the 11-market model config supports in general
    (lib.edgelab.recommendations.load_model_covered_series), its
    evaluationStatus is NOT_EVALUATED, not NO_MODEL_SUPPORT -- the model
    demonstrably CAN evaluate this family (it evaluated a different
    line/ticker of the exact same family for this game already), it was
    simply never run against this specific archived alternate rung.
    NO_MODEL_SUPPORT is reserved for a family the model has no method
    for at all (e.g. a player prop). Without this distinction, every
    alternate line of an otherwise-covered family (e.g. a Game_Total at
    a different threshold than the one the pipeline evaluated) was
    silently indistinguishable from a market the model can never handle
    at all -- the root cause of "the analysis layer only evaluates part
    of the archived market universe" investigated here.
    """
    model_covered_series = model_covered_series or frozenset()
    now = ids.utc_now_iso()
    commit_sha = _git_commit_sha()
    config_version = _model_config_version()
    seen = set(covered_tickers)
    extra = []
    for obs in observations:
        ticker = obs["marketTicker"]
        if ticker in seen:
            continue
        seen.add(ticker)
        evaluation_status = NOT_EVALUATED if obs.get("seriesTicker") in model_covered_series else NO_MODEL_SUPPORT
        extra.append({
            "schemaVersion": SCHEMA_VERSION,
            "modelEvaluationId": ids.build_model_evaluation_id(date, ticker),
            "runId": obs["runId"],
            "gameId": obs.get("gameId"),
            "sport": DEFAULT_SPORT,
            "platform": DEFAULT_PLATFORM,
            "marketTicker": ticker,
            "eventTicker": obs.get("eventTicker"),
            "seriesTicker": obs.get("seriesTicker"),
            "marketFamily": obs.get("marketFamily"),
            "selection": None,
            "side": None,
            "threshold": obs.get("threshold"),
            "evaluationStatus": evaluation_status,
            "modelFairProbability": None,
            "modelFairOdds": None,
            "modelVersion": None,
            "modelCommitSha": commit_sha,
            "modelConfigVersion": config_version,
            "probabilityAdapter": None,
            "modelSource": None,
            "calibrationVersion": None,
            "pipelineRunId": None,
            "artifactSource": None,
            "marketImpliedProbability": None,
            "estimatedEdge": None,
            "evPerDollar": None,
            "confidence": None,
            "confidenceSource": None,
            "lineupConfirmationState": "UNKNOWN",
            "dataQuality": None,
            "dataQualityReasons": [],
            "thesisTags": [],
            "tagEvidence": {},
            "correlationGroups": [],
            "recommendationId": ids.build_recommendation_id(date, ticker),
            "createdAt": now,
            "source": "market_universe_extension",
            "validationStatus": "valid",
            "provenance": dict(obs["provenance"], ingestedAt=now),
        })
    return extra


# ── Data-population report (Milestone 3 scope item 11) ──────────────────
#
# Read-only queries over lib.edgelab.analytics's v_model_evaluations
# (and, where a real link exists, v_placed_bets/v_settlements) -- never
# per-row Python materialization, same convention as
# lib.edgelab.calibration.

def _pct(session, total, where_sql):
    n = session.fetchall(f"SELECT COUNT(*) FROM v_model_evaluations WHERE {where_sql}")[0][0]
    return {"count": n, "pct": round(100.0 * n / total, 2) if total else None}


def population_report(session):
    """
    Overall coverage of every ModelEvaluation ever persisted: how many
    carry a real modelFairProbability/estimatedEdge/confidence/thesisTags,
    and how many are actually LINKED (by ID, not just ticker
    co-occurrence) to a Recommendation, PlacedBet, or Settlement. Returns
    None (not a fabricated all-zero report) when the entity has no files
    at all yet.
    """
    if not session.is_available("model_evaluations"):
        return None

    total = session.fetchall("SELECT COUNT(*) FROM v_model_evaluations")[0][0]
    result = {
        "total": total,
        "modelFairProbability": _pct(session, total, "modelFairProbability IS NOT NULL"),
        "estimatedEdge": _pct(session, total, "estimatedEdge IS NOT NULL"),
        "confidence": _pct(session, total, "confidence IS NOT NULL"),
        "thesisTags": _pct(session, total, "thesisTags IS NOT NULL AND len(thesisTags) > 0"),
        "linkedToRecommendation": _pct(session, total, "recommendationId IS NOT NULL"),
    }

    if session.is_available("bets"):
        result["linkedToPlacedBet"] = _pct(
            session, total,
            "modelEvaluationId IN (SELECT modelEvaluationId FROM v_placed_bets WHERE modelEvaluationId IS NOT NULL)",
        )
    else:
        result["linkedToPlacedBet"] = None

    if session.is_available("settlements"):
        # Settlement carries no modelEvaluationId field (deliberately --
        # see docs/EDGELAB_MODEL_EVALUATION.md's linkage-rules section on
        # why this link is query-time-by-ticker, not a new stored FK on
        # an entity Milestone 1 established should never be rewritten).
        result["linkedToSettlement"] = _pct(
            session, total,
            "marketTicker IN (SELECT marketTicker FROM v_settlements)",
        )
    else:
        result["linkedToSettlement"] = None

    return result


def population_by_canonical_family(session):
    """Same four coverage percentages as population_report(), broken out per canonical market family."""
    if not session.is_available("model_evaluations"):
        return []
    rows = session.fetchall("""
        SELECT
            canonicalMarketFamily,
            COUNT(*) AS n,
            SUM(CASE WHEN modelFairProbability IS NOT NULL THEN 1 ELSE 0 END) AS withProb,
            SUM(CASE WHEN estimatedEdge IS NOT NULL THEN 1 ELSE 0 END) AS withEdge,
            SUM(CASE WHEN confidence IS NOT NULL THEN 1 ELSE 0 END) AS withConfidence,
            SUM(CASE WHEN thesisTags IS NOT NULL AND len(thesisTags) > 0 THEN 1 ELSE 0 END) AS withTags
        FROM v_model_evaluations
        GROUP BY 1
        ORDER BY n DESC, canonicalMarketFamily
    """)
    return [
        {
            "canonicalMarketFamily": family, "n": n,
            "pctModelFairProbability": round(100.0 * with_prob / n, 2) if n else None,
            "pctEstimatedEdge": round(100.0 * with_edge / n, 2) if n else None,
            "pctConfidence": round(100.0 * with_conf / n, 2) if n else None,
            "pctThesisTags": round(100.0 * with_tags / n, 2) if n else None,
        }
        for family, n, with_prob, with_edge, with_conf, with_tags in rows
    ]


def population_by_model_version_and_source(session):
    """
    One row per (modelVersion, modelSource) pair actually observed --
    surfaces the honest current gap that modelVersion is null for every
    real pipeline-derived evaluation (docs/EDGELAB_MODEL_EVALUATION.md),
    rather than hiding it behind a single aggregate.
    """
    if not session.is_available("model_evaluations"):
        return []
    rows = session.fetchall("""
        SELECT COALESCE(modelVersion, 'UNKNOWN') AS modelVersion, COALESCE(modelSource, 'UNKNOWN') AS modelSource, COUNT(*) AS n
        FROM v_model_evaluations
        GROUP BY 1, 2
        ORDER BY n DESC, modelVersion, modelSource
    """)
    return [{"modelVersion": r[0], "modelSource": r[1], "n": r[2]} for r in rows]


# ── Milestone 4 additions: by date / by recommendation status / unresolved ──

def population_by_date(session):
    """
    Same coverage percentages as population_by_canonical_family(), broken
    out by the entryTimestamp-free date embedded in each date-partitioned
    model_evaluations/<date>.jsonl file's own __edgelab_filename -- the
    same regexp-on-filename convention lib.edgelab.analytics.row_counts_by_entity_and_date
    already uses for every other date-partitioned entity.
    """
    if not session.is_available("model_evaluations"):
        return []
    rows = session.fetchall("""
        SELECT
            regexp_extract(__edgelab_filename, '([0-9]{4}-[0-9]{2}-[0-9]{2})', 1) AS date,
            COUNT(*) AS n,
            SUM(CASE WHEN modelFairProbability IS NOT NULL THEN 1 ELSE 0 END) AS withProb,
            SUM(CASE WHEN estimatedEdge IS NOT NULL THEN 1 ELSE 0 END) AS withEdge,
            SUM(CASE WHEN confidence IS NOT NULL THEN 1 ELSE 0 END) AS withConfidence,
            SUM(CASE WHEN thesisTags IS NOT NULL AND len(thesisTags) > 0 THEN 1 ELSE 0 END) AS withTags,
            SUM(CASE WHEN correlationGroups IS NOT NULL AND len(correlationGroups) > 0 THEN 1 ELSE 0 END) AS withCorrelationGroups
        FROM v_model_evaluations
        GROUP BY 1
        ORDER BY 1
    """)
    return [
        {
            "date": date, "n": n,
            "pctModelFairProbability": round(100.0 * with_prob / n, 2) if n else None,
            "pctEstimatedEdge": round(100.0 * with_edge / n, 2) if n else None,
            "pctConfidence": round(100.0 * with_conf / n, 2) if n else None,
            "pctThesisTags": round(100.0 * with_tags / n, 2) if n else None,
            "pctCorrelationGroups": round(100.0 * with_corr / n, 2) if n else None,
        }
        for date, n, with_prob, with_edge, with_conf, with_tags, with_corr in rows
    ]


def population_by_recommendation_status(session):
    """
    Coverage broken out by the LINKED Recommendation's own status
    (RECOMMENDED/BET_PLACED/PASS_*/etc.) -- joins v_model_evaluations to
    v_recommendations by recommendationId (both share the exact same
    idempotent key, see lib/edgelab/model_evaluation.py's module
    docstring), so this reads "how complete is our metadata for markets
    we actually bet vs. recommended-not-bet vs. passed". Empty when
    either entity is unavailable, or when no evaluation's recommendationId
    resolves to a real Recommendation row.
    """
    if not session.is_available("model_evaluations") or not session.is_available("recommendations"):
        return []
    rows = session.fetchall("""
        SELECT
            r.status,
            COUNT(*) AS n,
            SUM(CASE WHEN e.modelFairProbability IS NOT NULL THEN 1 ELSE 0 END) AS withProb,
            SUM(CASE WHEN e.confidence IS NOT NULL THEN 1 ELSE 0 END) AS withConfidence,
            SUM(CASE WHEN e.thesisTags IS NOT NULL AND len(e.thesisTags) > 0 THEN 1 ELSE 0 END) AS withTags
        FROM v_model_evaluations e
        JOIN v_recommendations r ON r.recommendationId = e.recommendationId
        GROUP BY 1
        ORDER BY n DESC, r.status
    """)
    return [
        {
            "recommendationStatus": status, "n": n,
            "pctModelFairProbability": round(100.0 * with_prob / n, 2) if n else None,
            "pctConfidence": round(100.0 * with_conf / n, 2) if n else None,
            "pctThesisTags": round(100.0 * with_tags / n, 2) if n else None,
        }
        for status, n, with_prob, with_conf, with_tags in rows
    ]


def unresolved_metadata_report(session):
    """
    Concrete, groundable "unresolved/conflicting metadata" cases
    (Milestone 4 scope item 9/11) -- NOT a fabricated concept: flags
    evaluations that reached a real EVALUATED status (a trustworthy
    fair probability against a real market price) yet are missing
    confidence or lineup evidence entirely, since those are the two
    metadata dimensions this milestone can otherwise usually populate
    for an EVALUATED row. A non-zero count here is a genuine, actionable
    data gap -- not a query bug.
    """
    if not session.is_available("model_evaluations"):
        return None
    evaluated_missing_confidence = session.fetchall(
        "SELECT COUNT(*) FROM v_model_evaluations WHERE evaluationStatus = 'EVALUATED' AND confidence IS NULL"
    )[0][0]
    evaluated_missing_lineup = session.fetchall(
        "SELECT COUNT(*) FROM v_model_evaluations WHERE evaluationStatus = 'EVALUATED' AND lineupConfirmationState = 'UNKNOWN'"
    )[0][0]
    total_evaluated = session.fetchall("SELECT COUNT(*) FROM v_model_evaluations WHERE evaluationStatus = 'EVALUATED'")[0][0]
    return {
        "totalEvaluated": total_evaluated,
        "evaluatedMissingConfidence": evaluated_missing_confidence,
        "evaluatedMissingLineupEvidence": evaluated_missing_lineup,
    }
