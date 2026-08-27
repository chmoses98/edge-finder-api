"""
lib/edgelab/pit_provenance.py
=================================
Research Lab Milestone 0A: the point-in-time (PIT) feature/data
provenance manifest -- answers "could this feature/data source
legitimately have been known at checkpoint T?" for every canonical
EdgeLab research input.

This module does NOT audit every possible baseball feature (spec:
"does NOT require auditing every possible baseball feature in depth").
It provides the manifest STRUCTURE (PitProvenanceEntry) and populates it
for the canonical inputs the spec explicitly names, using only evidence
already established elsewhere in this repository (docs/EDGELAB_*.md,
the modules those docs describe) -- never a fresh guess. Anything not
already backed by a specific, citable finding is marked UNKNOWN /
REQUIRES_AUDIT, never assumed PIT-safe (spec: "For anything uncertain,
mark it UNKNOWN / REQUIRES_AUDIT rather than assuming PIT-safe").

PIT STATUS VOCABULARY (spec section 3):
  OBSERVED_AT_DECISION_TIME             -- genuinely captured/observed at
                                            (or provably before) decision time.
  RECONSTRUCTABLE_FROM_DATED_RAW        -- not itself captured at decision
                                            time, but legitimately
                                            re-derivable from other raw
                                            data that IS dated/immutable
                                            (e.g. re-running a pure
                                            function against a frozen
                                            historical input).
  RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS -- reconstructed after
                                            the fact from evidence that
                                            does not fully prove
                                            point-in-time availability.
  PROSPECTIVE_ONLY                      -- only trustworthy from the
                                            moment a live prospective
                                            capture pathway started
                                            recording it; no historical
                                            depth.
  UNAVAILABLE_HISTORICALLY              -- confirmed, not merely assumed,
                                            that no historical record of
                                            this input exists at all.
  UNKNOWN_REQUIRES_AUDIT                -- not yet audited; must never be
                                            treated as PIT-safe until it is.
"""

from collections import namedtuple

OBSERVED_AT_DECISION_TIME = "OBSERVED_AT_DECISION_TIME"
RECONSTRUCTABLE_FROM_DATED_RAW = "RECONSTRUCTABLE_FROM_DATED_RAW"
RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS = "RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS"
PROSPECTIVE_ONLY = "PROSPECTIVE_ONLY"
UNAVAILABLE_HISTORICALLY = "UNAVAILABLE_HISTORICALLY"
UNKNOWN_REQUIRES_AUDIT = "UNKNOWN_REQUIRES_AUDIT"

PIT_STATUSES = frozenset({
    OBSERVED_AT_DECISION_TIME, RECONSTRUCTABLE_FROM_DATED_RAW,
    RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS, PROSPECTIVE_ONLY,
    UNAVAILABLE_HISTORICALLY, UNKNOWN_REQUIRES_AUDIT,
})

# A status is PIT-safe enough to support E2_PIT_HISTORICAL evidence only
# for these two -- never for RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS
# or UNKNOWN_REQUIRES_AUDIT (those cap an experiment at E1 at best -- see
# lib.edgelab.evidence_levels).
PIT_SAFE_FOR_E2_HISTORICAL = frozenset({OBSERVED_AT_DECISION_TIME, RECONSTRUCTABLE_FROM_DATED_RAW})

PitProvenanceEntry = namedtuple("PitProvenanceEntry", [
    "sourceIdentifier", "dataFamily", "timestampSemantics", "availabilitySemantics",
    "reconstructionMethod", "earliestTrustworthyEvidenceLevel", "knownGaps", "auditNotes", "pitStatus",
])


def _entry(**kwargs):
    for field in PitProvenanceEntry._fields:
        kwargs.setdefault(field, None)
    return PitProvenanceEntry(**kwargs)


# ── The manifest ──────────────────────────────────────────────────────────
# Every entry below cites the specific module/doc finding it is based on.
# Import evidence_levels lazily-by-name (as strings) to avoid a circular
# import -- evidence_levels.py does not import this module, but keeping
# the reference as the bare string constant (matching evidence_levels'
# own constant names) is deliberate/reviewable without importing here.

PIT_MANIFEST = {
    "archived_kalshi_market_observation": _entry(
        sourceIdentifier="data/edgelab/observations/<date>.jsonl(.gz)",
        dataFamily="market_price",
        timestampSemantics="MarketObservation.capturedAt is the actual capture instant of the scheduled scraping workflow (edgelab-capture.yml), not an ingestion timestamp.",
        availabilitySemantics="A price observed at capturedAt was genuinely tradable/visible on Kalshi at that instant -- this is the repo's own raw evidence, not a derived value.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel="E2_PIT_HISTORICAL",
        knownGaps="Capture cadence is coarse (10-30 min polling, see docs/EDGELAB_PHASE1.md) -- a price between polls is genuinely unobserved, never interpolated.",
        auditNotes="Confirmed real, immutable, append-only, git-committed archive (lib.edgelab.storage). This is the single strongest PIT source in the corpus.",
        pitStatus=OBSERVED_AT_DECISION_TIME,
    ),
    "kalshi_bid_ask_executable_price": _entry(
        sourceIdentifier="MarketObservation.yesBid/yesAsk/noBid/noAsk",
        dataFamily="market_price",
        timestampSemantics="Same as archived_kalshi_market_observation -- same capturedAt.",
        availabilitySemantics="An executable YES/NO price at the observation instant, per lib.edgelab.research_dataset._executable_yes_price/_executable_no_price's own documented ask-preferred/bid-fallback convention.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel="E2_PIT_HISTORICAL",
        knownGaps="No archived fill/order-book depth -- only top-of-book bid/ask; a large hypothetical order's true realizable price at size is not knowable (lib.edgelab.kalshi_fees module docstring's fractional/whole-contract caveat).",
        auditNotes=None,
        pitStatus=OBSERVED_AT_DECISION_TIME,
    ),
    "kalshi_closing_market_quote": _entry(
        sourceIdentifier="lib.edgelab.checkpoints.select_closing_quote() output",
        dataFamily="market_price",
        timestampSemantics="A DERIVED selection (the last valid pre-start observation), not a separately-captured instant -- its own capturedAt is a real observation's capturedAt.",
        availabilitySemantics="Only known once the FULL observation series for that market is available -- i.e. only after the fact, even though the underlying observation it selects was itself pregame. A checkpoint at T cannot know which later observation will end up being 'the closing quote' -- using isClosingQuote as a FEATURE for a T-checkpoint prediction would be a leakage bug (see lib.edgelab.research_dataset docstring's isClosingQuote-vs-checkpoint distinction).",
        reconstructionMethod="Deterministic function over the full archived observation series for a ticker.",
        earliestTrustworthyEvidenceLevel="E2_PIT_HISTORICAL for OUTCOME/EVALUATION use (e.g. CLV, closing-line comparison); E0_DESCRIPTIVE only if ever used as a predictive FEATURE (which it structurally can never legitimately be for a checkpoint before it).",
        knownGaps=None,
        auditNotes="Never use as a predictive input for any checkpoint at or before its own capturedAt -- only as a downstream comparison/evaluation target.",
        pitStatus=OBSERVED_AT_DECISION_TIME,
    ),
    "model_evaluation_probability_pipeline_derived": _entry(
        sourceIdentifier="ModelEvaluation rows with artifactSource='recommendations' (once-daily pipeline)",
        dataFamily="model_probability",
        timestampSemantics="pipelineRunId (the production pipeline's own meta.createdAt) is the ONLY causal timestamp -- createdAt is EdgeLab's ingestion time, never causal (lib.edgelab.temporal_alignment module docstring).",
        availabilitySemantics="A ModelEvaluation is a valid PIT input for a checkpoint at T only if pipelineRunId <= T (lib.edgelab.temporal_alignment.select_temporally_valid_evaluation) -- most FIRST_DAILY checkpoints in the current corpus fail this test (docs/EDGELAB_RESEARCH_TRUSTWORTHINESS.md section 7: only 264/75,280 historical rows are causally linked).",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel="E2_PIT_HISTORICAL, and only for the specific rows the temporal-alignment join actually selects -- never for a row merely sharing a ticker.",
        knownGaps="Sparse causal coverage historically (once-daily pipeline run time vs. mostly-earlier checkpoints).",
        auditNotes=None,
        pitStatus=OBSERVED_AT_DECISION_TIME,
    ),
    "model_evaluation_probability_prospective_snapshot": _entry(
        sourceIdentifier="ModelEvaluation rows with artifactSource='prospective_snapshot'",
        dataFamily="model_probability",
        timestampSemantics="pipelineRunId is the actual live intraday evaluation instant (not a pipeline run) -- docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md.",
        availabilitySemantics="Genuinely prospective for the fields the checkpoint refreshes live (lineup, at LINEUP_CONFIRMATION only); every OTHER field reuses inputs persisted from the day's single earlier pipeline fetch (inputFreshnessNote=ALL_INPUTS_PERSISTED_FROM_SLATE_AT_LAST_PIPELINE_FETCH) -- still legitimately pre-event/pre-decision, but NOT independently re-verified fresh at the checkpoint instant. A NEW research feature riding on this pathway is PIT-safe only to the extent ITS OWN inputs were part of what got refreshed -- audit per-feature, never assume by pathway alone (see lib.edgelab.evidence_levels module docstring, item 2).",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel="E4_PROSPECTIVE_SHADOW for the lineup-refreshed fields specifically; E2_PIT_HISTORICAL (not E4) for the persisted-from-slate fields, since those were fixed once per day, not re-verified per checkpoint.",
        knownGaps="Only exists from this system's deployment forward -- zero historical depth before it (see docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md section 0, 'we cannot retroactively manufacture historical predictions').",
        auditNotes=None,
        pitStatus=PROSPECTIVE_ONLY,
    ),
    "settlement_outcome": _entry(
        sourceIdentifier="Settlement.result / Settlement.settlementStatus",
        dataFamily="outcome",
        timestampSemantics="Known only after the market resolves -- inherently POST-decision by construction.",
        availabilitySemantics="Never a valid predictive input for any pregame checkpoint under any circumstance -- this entry exists so a future feature-provenance audit has an explicit, citable NEVER-PIT-SAFE entry to check a candidate feature against, not because anyone should ever be tempted to use it.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel="Not applicable as a predictive feature at any evidence level; valid only as an EVALUATION target.",
        knownGaps=None,
        auditNotes="A leakage guard, not a feature source -- see docs/EDGELAB_RESEARCH_LAB.md's data-leakage examples.",
        pitStatus=UNAVAILABLE_HISTORICALLY,
    ),
    "lineup_status_official": _entry(
        sourceIdentifier="MarketObservation.lineupConfirmationState / ModelEvaluation.lineupConfirmationState",
        dataFamily="lineup",
        timestampSemantics="For the prospective-snapshot LINEUP_CONFIRMATION checkpoint: the actual live poll instant (docs/EDGELAB_PROSPECTIVE_MODEL_SNAPSHOTS.md section 3a). For any other checkpoint/pipeline-derived row: whatever data/slate.json held at the ONE daily pipeline fetch, which may be stale relative to the checkpoint's own instant.",
        availabilitySemantics="CONFIRMED at the LINEUP_CONFIRMATION checkpoint is genuinely PIT-safe (a live poll, in-memory only, never leaking backward into an earlier checkpoint -- proven by test_t_minus_30_snapshot_keeps_its_own_earlier_lineup_state_even_when_lineup_confirms_same_cycle). Lineup state attached to a T_MINUS_* checkpoint is the STALE, once-daily-fetched value, not a live check at that instant.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel="E4_PROSPECTIVE_SHADOW at the LINEUP_CONFIRMATION checkpoint specifically; E1_RECONSTRUCTED_RETROSPECTIVE elsewhere (the value exists but wasn't re-verified at that checkpoint's own instant).",
        knownGaps="No historical archive of lineup state AT EACH checkpoint before this system's deployment -- only whatever the day's single slate fetch captured.",
        auditNotes=None,
        pitStatus=PROSPECTIVE_ONLY,
    ),
    "sharp_sportsbook_observation": _entry(
        sourceIdentifier="the-odds-api.com pull (live, not archived per-date)",
        dataFamily="external_market_signal",
        timestampSemantics="Unknown/not tracked -- no committed, dated archive of odds-API responses exists in this repository as of this audit.",
        availabilitySemantics="Unaudited.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="No archived, per-date, immutable capture of sportsbook odds was found in data/edgelab/ or data/ during this milestone's audit -- odds are fetched live and consumed in-memory (lib.edgelab.prospective_snapshot module docstring section 2: 'a metered/rate-limited service', re-fetched sparingly, not archived per checkpoint).",
        auditNotes="UNKNOWN_REQUIRES_AUDIT is the honest status -- this milestone did not exhaustively search every script for a possible archive path; a future audit should re-check before relying on this being empty.",
        pitStatus=UNKNOWN_REQUIRES_AUDIT,
    ),
    "weather_input": _entry(
        sourceIdentifier="data/weather.json (live fetch, api/weather.js)",
        dataFamily="weather",
        timestampSemantics="Unknown -- data/weather.json is overwritten in place, not date-partitioned/archived.",
        availabilitySemantics="Confirmed orphaned from the production pipeline itself (docs/EDGELAB_EVALUATION_METADATA.md section 1: 'never merged into slate.json/recommendations.json by any script... risk_gate.py hardcodes weatherAdjustment: None').",
        reconstructionMethod="Historical weather COULD in principle be reconstructed from a third-party dated weather-history API keyed by park+game time, but no such reconstruction exists in this repository today -- not claimed here.",
        earliestTrustworthyEvidenceLevel="E1_RECONSTRUCTED_RETROSPECTIVE at best, and only once a documented reconstruction path is actually built -- currently there is no historical weather signal usable for research at all.",
        knownGaps="No per-date archive; current live fetch is not wired into any research-persisted record.",
        auditNotes=None,
        pitStatus=UNAVAILABLE_HISTORICALLY,
    ),
    "season_to_date_stats": _entry(
        sourceIdentifier="pitcher/hitter Savant + MLB Stats API season aggregates consumed by scripts/enrich_data.py",
        dataFamily="player_stats",
        timestampSemantics="Unaudited by this milestone -- whether the specific aggregate window used by any given historical evaluation was truly 'as of that game date' (rather than a later, more-complete season snapshot) was not traced field-by-field.",
        availabilitySemantics="Unaudited.",
        reconstructionMethod="Plausibly reconstructable from MLB Stats API's own dated game logs (a real, dated data source exists), but no EdgeLab module currently performs this reconstruction with a documented, tested method.",
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="A rolling/season-to-date statistic recomputed today using 'current' Savant data for a PAST game date risks exactly the look-ahead pattern the spec's data-leakage section calls out ('calculating a rolling statistic with data after the game') if not carefully date-bounded -- this risk has NOT been ruled out by this milestone's audit.",
        auditNotes="Deliberately left UNKNOWN_REQUIRES_AUDIT rather than assumed safe -- a future experiment proposing to use season-to-date stats as a PIT feature must audit scripts/enrich_data.py's actual date-bounding behavior first.",
        pitStatus=UNKNOWN_REQUIRES_AUDIT,
    ),
    "hitter_snapshot": _entry(
        sourceIdentifier="lib.edgelab.hitter_board_bridge / hitter_projection_snapshot entity",
        dataFamily="player_projection",
        timestampSemantics="Unaudited by this milestone.",
        availabilitySemantics="Unaudited.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="Not traced field-by-field this milestone -- research-only hitter-market support exists (per the milestone brief) but its own PIT depth was not independently re-verified here.",
        auditNotes=None,
        pitStatus=UNKNOWN_REQUIRES_AUDIT,
    ),
    "pitcher_snapshot": _entry(
        sourceIdentifier="pitcherSavant projections consumed by scripts/build_market_ledger.py",
        dataFamily="player_projection",
        timestampSemantics="Unaudited by this milestone.",
        availabilitySemantics="Unaudited.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="Same caveat as season_to_date_stats -- a 'current' Savant pull used to recompute a past date's pitcher projection is a plausible leakage vector, not yet ruled out.",
        auditNotes=None,
        pitStatus=UNKNOWN_REQUIRES_AUDIT,
    ),
}


def get_entry(data_family_key: str) -> PitProvenanceEntry:
    if data_family_key not in PIT_MANIFEST:
        raise KeyError(
            f"No PIT provenance manifest entry for {data_family_key!r}. "
            f"Known keys: {sorted(PIT_MANIFEST)}. An experiment depending on an unlisted "
            f"input must add a manifest entry (defaulting pitStatus to UNKNOWN_REQUIRES_AUDIT) "
            f"before it can be registered -- never silently assumed PIT-safe."
        )
    return PIT_MANIFEST[data_family_key]


def is_pit_safe_for_e2_historical(data_family_key: str) -> bool:
    """True only for OBSERVED_AT_DECISION_TIME / RECONSTRUCTABLE_FROM_DATED_RAW -- see PIT_SAFE_FOR_E2_HISTORICAL."""
    return get_entry(data_family_key).pitStatus in PIT_SAFE_FOR_E2_HISTORICAL


def assert_known_inputs(data_family_keys) -> None:
    """
    Raises KeyError (via get_entry) for any input an experiment declares
    that has no manifest entry at all -- called by
    lib.edgelab.experiment_registry.validate_experiment_definition so an
    experiment can never register against an unaudited, undocumented
    input silently.
    """
    for key in data_family_keys:
        get_entry(key)
