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

# ── Milestone 0A hardening pass: PIT REQUIREMENT ROLES ──────────────────────
# A given data family is not equally "PIT-safe" for every USE a report
# might put it to -- settlement_outcome, for instance, is perfectly fine
# as the thing an experiment scores predictions AGAINST, but can never
# legitimately be a predictive FEATURE. Every pitRequirements entry an
# experiment registers now declares which of these three roles it plays,
# not merely that the input exists in this manifest at all.
ROLE_PREDICTIVE_INPUT = "PREDICTIVE_INPUT"        # feeds the model/candidate's own prediction
ROLE_EVALUATION_TARGET = "EVALUATION_TARGET"      # what the prediction is scored against, post-hoc
ROLE_AUXILIARY_METADATA = "AUXILIARY_METADATA"    # descriptive/bookkeeping only -- never scored, never predictive
PIT_ROLES = frozenset({ROLE_PREDICTIVE_INPUT, ROLE_EVALUATION_TARGET, ROLE_AUXILIARY_METADATA})

# Evidence levels that make a "this specific historical input was
# genuinely knowable pre-decision" claim -- E0/E1 make no such claim (E0
# is purely descriptive, E1 already admits reconstruction uncertainty),
# and E4/E5 are prospective-forward claims governed by live capture, not
# by whether an input existed in some HISTORICAL archive. Only E2/E3 are
# actually asserting historical PIT safety, so only these two are gated
# by pitStatus below.
HISTORICAL_PIT_CLAIM_EVIDENCE_LEVELS = frozenset({"E2_PIT_HISTORICAL", "E3_WALK_FORWARD_HOLDOUT"})

# pitStatus values that must never be silently treated as PIT-safe for a
# HISTORICAL_PIT_CLAIM_EVIDENCE_LEVELS experiment when used as a
# PREDICTIVE_INPUT -- covers all three of the spec's E2/E3-specific
# rules (UNKNOWN_REQUIRES_AUDIT, RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS,
# PROSPECTIVE_ONLY) in one shared check, since the failure mode and the
# fix (audit it, or register at a lower/different evidence level) are
# identical for all three.
_NOT_HISTORICALLY_PIT_SAFE_AS_PREDICTIVE = frozenset({
    UNKNOWN_REQUIRES_AUDIT, RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS, PROSPECTIVE_ONLY,
})

PitProvenanceEntry = namedtuple("PitProvenanceEntry", [
    "sourceIdentifier", "dataFamily", "timestampSemantics", "availabilitySemantics",
    "reconstructionMethod", "earliestTrustworthyEvidenceLevel", "knownGaps", "auditNotes", "pitStatus",
    "allowedRoles",
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
        allowedRoles=frozenset({ROLE_EVALUATION_TARGET}),
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
        allowedRoles=frozenset({ROLE_EVALUATION_TARGET}),
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

    # ── Milestone 2: HISTORICAL BACKTEST CORPUS / PIT FEATURE AUDIT ────────
    # Entries below are NEW, narrowly-scoped keys added by that milestone's
    # audit -- each describes one SPECIFIC reconstruction pathway or
    # confirmed-absent source, evidenced by reading the actual fetch/
    # storage code (see docs/EDGELAB_MILESTONE2_PIT_FEATURE_AUDIT.md for
    # the full audit and coverage counts this is drawn from). These do NOT
    # replace or loosen season_to_date_stats/hitter_snapshot/
    # pitcher_snapshot above (still UNKNOWN_REQUIRES_AUDIT, unaudited by
    # this milestone) -- those keys name different, still-unaudited
    # production artifacts, not the pathways audited here.
    "hitter_statcast_raw_archive": _entry(
        sourceIdentifier="data/statcast_raw/games/<gamePk>.jsonl + index/batter_games.jsonl, via lib.research.statcast_pitch_store.load_pitches_for_batter(batter_id, as_of, since)",
        dataFamily="hitter_statcast",
        timestampSemantics="Per-pitch gameDate, from the archived raw pitch record itself -- not an ingestion timestamp.",
        availabilitySemantics="load_pitches_for_batter's as_of parameter is EXCLUSIVE (a pitch on gameDate == as_of is never included -- its own docstring: 'matching \"as_of=<slate date>\" meaning everything known strictly before today's games'), enforced twice: once via the per-batter index, once again per-pitch as defense in depth. Confirmed by reading the module in full (Milestone 2 audit) -- this is a genuinely PIT-safe query interface, already used in production by lib.research.hitter_pitch_derivation and lib.research.hitter_feature_context.",
        reconstructionMethod="Deterministic: read the raw archived pitch records for a batter with as_of=<target date>, then apply lib.research.hitter_pitch_derivation's pure derivation functions (derive_contact_quality, derive_spray_profile).",
        earliestTrustworthyEvidenceLevel="E2_PIT_HISTORICAL",
        knownGaps="Archive depth only as far back as ingestion has actually run -- 15 distinct gameDates (2026-08-11..2026-08-25) / 203 games as of this audit (2026-08-27), NOT a multi-season backfill. Growing daily going forward, not retroactively.",
        auditNotes="Milestone 2 finding: the strongest existing PIT-safe reconstruction pathway in the repo for any family. Depth is the binding constraint, not safety.",
        pitStatus=RECONSTRUCTABLE_FROM_DATED_RAW,
    ),
    "pitcher_statcast_raw_archive": _entry(
        sourceIdentifier="data/statcast_raw/games/<gamePk>.jsonl + index/pitcher_games.jsonl, via lib.research.statcast_pitch_store.load_pitches_for_pitcher(pitcher_id, as_of, since)",
        dataFamily="starter_pitch_characteristics",
        timestampSemantics="Same as hitter_statcast_raw_archive -- per-pitch gameDate.",
        availabilitySemantics="Symmetric counterpart to hitter_statcast_raw_archive -- same exclusive as_of contract, same index-first + per-pitch defense-in-depth filter (confirmed by reading the module in full).",
        reconstructionMethod="Deterministic: load_pitches_for_pitcher(as_of=<target date>) gives every archived pitch thrown by that pitcher before the target date -- velocity, pitch type/shape, location are all present per-pitch.",
        earliestTrustworthyEvidenceLevel="E2_PIT_HISTORICAL",
        knownGaps="Same depth constraint as hitter_statcast_raw_archive -- 15 gameDates as of this audit, growing forward only.",
        auditNotes=None,
        pitStatus=RECONSTRUCTABLE_FROM_DATED_RAW,
    ),
    "team_recent_game_log_reconstruction": _entry(
        sourceIdentifier="MLB Stats API /schedule + /game/{gamePk}/boxscore, via lib.edgelab.pit_reconstruction.as_of_completed_team_games / reconstruct_team_bullpen_usage_as_of (new this milestone, reusing lib.edgelab.bullpen_usage's existing network adapters and pure parsers)",
        dataFamily="team_recent_form",
        timestampSemantics="Each game's own schedule `date` (the day it was played) and boxscore contents -- both dated by MLB Stats API, not by this repo's fetch time.",
        availabilitySemantics="Games are included only if COMPLETED (per lib.edgelab.bullpen_usage.extract_completed_games_for_team's COMPLETED_STATUSES filter -- a live/postponed/scheduled game is excluded, never approximated) AND strictly before the requested as-of date, enforced twice: the schedule query window itself never asks the API about as_of_date or later, and every returned game is independently re-filtered to date < as_of_date. Proven by this milestone's leakage tests (tests/edgelab/test_pit_reconstruction.py), including a test where a deliberately misbehaving fetcher returns games on/after as_of_date and the module still excludes them from the result and NEVER requests their boxscores.",
        reconstructionMethod="lib.edgelab.pit_reconstruction.as_of_completed_team_games(team_id, as_of_date, lookback_days) for the raw game list; reconstruct_team_bullpen_usage_as_of(...) for a demonstrative feature value (recent bullpen usage), built entirely from lib.edgelab.bullpen_usage's existing pure functions.",
        earliestTrustworthyEvidenceLevel="E2_PIT_HISTORICAL",
        knownGaps="Only bullpen recent-usage is wired to a feature value this milestone; the same primitive equally supports starter recent-workload/rest reconstruction (same schedule+boxscore substrate scripts/fetch_opp_quality.py's fetch_actual_starter already uses for starter identification) and a coarse box-score-derived team-offense proxy (e.g. runs/game), but neither of those feature computations has been built or tested yet -- audited as reconstructable in principle via this same mechanism, not implemented. Unlike the Statcast-raw-archive entries above, depth here is bounded only by MLB Stats API's own historical availability (multiple past seasons), not by this repo's short EdgeLab corpus window -- but the corpus of games with a contemporaneous archived Kalshi market or a captured model probability to pair it against remains bounded to this repo's ~20-27 day EdgeLab corpus.",
        auditNotes="Milestone 2 build: formalizes an as-of guard on top of a mechanism (lib.edgelab.bullpen_usage) that previously only supported 'today'-relative live use, with no test proving no-lookahead for a historical date.",
        pitStatus=RECONSTRUCTABLE_FROM_DATED_RAW,
    ),
    "team_offense_savant_season_aggregate": _entry(
        sourceIdentifier="data/savant_team.json, fetched by scripts/fetch_savant_team.py",
        dataFamily="team_offense",
        timestampSemantics="No timestamp at all -- SEASON is a hardcoded '2026' constant in the fetch script, and the output file is overwritten in place on every run.",
        availabilitySemantics="Confirmed by reading scripts/fetch_savant_team.py in full: the fetch URL takes no date parameter, and no per-date archive of this file exists anywhere under data/ (confirmed by directory search). A query for 'this team's offense as of a past date' cannot be distinguished from today's current aggregate -- there is nothing to distinguish it FROM.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="No per-date archive exists at any point in this repository's history for this specific artifact.",
        auditNotes="Milestone 2 finding: this is the production feature's actual Statcast/wOBA-level team-offense input, and it is NOT historically reconstructable as currently fetched/stored. A coarser box-score-derived proxy (runs scored per game, via the same schedule+boxscore mechanism as team_recent_game_log_reconstruction) is plausible but not built -- see docs/EDGELAB_MILESTONE2_PIT_FEATURE_AUDIT.md.",
        pitStatus=UNAVAILABLE_HISTORICALLY,
    ),
    "starter_quality_savant_season_aggregate": _entry(
        sourceIdentifier="Savant pitcher leaderboard data consumed by scripts/fetch_savant_pitchers.py",
        dataFamily="starter_quality",
        timestampSemantics="No timestamp -- same hardcoded current-SEASON, overwrite-in-place pattern as team_offense_savant_season_aggregate.",
        availabilitySemantics="Same systemic issue, confirmed by reading the fetch script: no date parameter, no per-date archive.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="No per-date archive exists.",
        auditNotes=None,
        pitStatus=UNAVAILABLE_HISTORICALLY,
    ),
    "bullpen_talent_savant_season_aggregate": _entry(
        sourceIdentifier="data/bullpen.json's era/xFIP/whip/grade/hlXFIP fields, fetched by scripts/fetch_savant_bullpen_hl.py",
        dataFamily="bullpen_talent",
        timestampSemantics="No timestamp -- same hardcoded current-SEASON, overwrite-in-place pattern. Distinct from lib.edgelab.bullpen_usage's RECENT-usage fields in the same file, which are a different, PIT-reconstructable pathway (see team_recent_game_log_reconstruction).",
        availabilitySemantics="Same systemic issue as the other Savant season-aggregate sources -- no date parameter, no per-date archive.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="No per-date archive exists.",
        auditNotes=None,
        pitStatus=UNAVAILABLE_HISTORICALLY,
    ),
    "batter_platoon_split_savant_season_aggregate": _entry(
        sourceIdentifier="Savant platoon-split data consumed by scripts/fetch_batter_platoon_splits.py",
        dataFamily="platoon_performance",
        timestampSemantics="No timestamp -- same hardcoded current-SEASON, overwrite-in-place pattern.",
        availabilitySemantics="Same systemic issue, confirmed by reading the fetch script. Distinct from the STATIC handedness identity fields (which arm/side a player is), which are essentially time-invariant and carried in every boxscore record this repo already reads -- not itself a leakage risk, but not given its own manifest entry by this milestone since no research code currently reads handedness identity as a standalone PIT input.",
        reconstructionMethod="A platoon-relevant signal could in principle be derived from hitter_statcast_raw_archive/pitcher_statcast_raw_archive's per-pitch batterHand/pitcherHand fields within their archive depth, but no derivation helper exists for this specific split yet -- not built this milestone.",
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="No per-date archive of the season-aggregate split exists.",
        auditNotes=None,
        pitStatus=UNAVAILABLE_HISTORICALLY,
    ),
    "park_factor_static_table": _entry(
        sourceIdentifier="api/slate.js hardcoded per-team parkFactor lookup table",
        dataFamily="park",
        timestampSemantics="No timestamp -- a single static dict, not date-partitioned or versioned.",
        availabilitySemantics="Confirmed by reading api/slate.js: a plain per-team constant (e.g. NYY: 103). No team changed home ballparks during this corpus's window, so the value in effect today is PLAUSIBLY the same value that was in effect at any past date in the corpus -- but this repository has no dated/versioned archive that PROVES the exact value used historically, so it cannot be claimed fully PIT-safe.",
        reconstructionMethod="Use the current static table value, on the assumption park factors are stable within the corpus's short window -- an assumption, not a proof.",
        earliestTrustworthyEvidenceLevel="E1_RECONSTRUCTED_RETROSPECTIVE",
        knownGaps="No dated archive; historical correctness rests on an unverified stability assumption. Would break silently if a team's park (or its factor) ever changed.",
        auditNotes=None,
        pitStatus=RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS,
    ),
    "injury_restriction_data": _entry(
        sourceIdentifier="none found",
        dataFamily="injury",
        timestampSemantics=None,
        availabilitySemantics="Confirmed by a repository-wide search (Milestone 2 audit): no injury/IL/restriction data fetcher, file, or archive exists anywhere in this repository. lib.research.pitcher_workload_projection.py's WEAK_INFERENCE evidence-quality tier explicitly treats 'general injury history' as a narrative-only, non-data-feed signal -- confirming absence, not contradicting it.",
        reconstructionMethod=None,
        earliestTrustworthyEvidenceLevel=None,
        knownGaps="No data source of any kind exists for this family.",
        auditNotes="Genuinely UNAVAILABLE, not merely unaudited -- distinguished here from UNKNOWN_REQUIRES_AUDIT because the absence itself was confirmed, not assumed.",
        pitStatus=UNAVAILABLE_HISTORICALLY,
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
    lib.edgelab.control_identity for a control's requiredInputProvenance
    (mere existence -- a control has no role/evidence-level context of
    its own to check compatibility against; that check lives at the
    EXPERIMENT level, via validate_pit_requirements below).
    """
    for key in data_family_keys:
        get_entry(key)


def check_predictive_compatibility(data_family_key: str, evidence_level: str):
    """
    Whether `data_family_key` may legitimately serve as a
    ROLE_PREDICTIVE_INPUT for an experiment registered at
    `evidence_level`. Returns (True, None) when compatible, or
    (False, reason) when not -- never raises itself, so a caller can
    choose whether a failure should abort registration (hard error) or
    merely gate a disposition (see lib.edgelab.experiment_report).

    Rules (spec, Milestone 0A hardening pass item 1):
      - UNAVAILABLE_HISTORICALLY can never be a predictive input, at any
        evidence level -- there is no archive to draw a feature from.
      - UNKNOWN_REQUIRES_AUDIT / RETROSPECTIVELY_RECONSTRUCTED_WITH_LIMITATIONS /
        PROSPECTIVE_ONLY can never support a HISTORICAL_PIT_CLAIM
        evidence level (E2/E3) as a predictive input -- each of these
        three pitStatus values means "this was not proven knowable at
        the historical checkpoint," which is exactly the claim E2/E3
        makes. They remain usable at E0/E1 (no PIT claim made) and at
        E4/E5 (a live, forward-looking claim, not a historical one).
    """
    entry = get_entry(data_family_key)
    status = entry.pitStatus
    if status == UNAVAILABLE_HISTORICALLY:
        return False, (
            f"{data_family_key!r} has pitStatus=UNAVAILABLE_HISTORICALLY -- it can never be used as a "
            f"PREDICTIVE_INPUT, at any evidence level (no historical archive exists to draw a feature from)."
        )
    if status in _NOT_HISTORICALLY_PIT_SAFE_AS_PREDICTIVE and evidence_level in HISTORICAL_PIT_CLAIM_EVIDENCE_LEVELS:
        return False, (
            f"{data_family_key!r} has pitStatus={status} -- it cannot support {evidence_level} as a "
            f"PREDICTIVE_INPUT (that pitStatus does not prove historical point-in-time availability). "
            f"Either audit/upgrade its manifest entry, use it only as EVALUATION_TARGET/AUXILIARY_METADATA, "
            f"or register this experiment at a lower/different evidence level."
        )
    return True, None


def validate_pit_requirement(data_family_key: str, role: str, evidence_level: str) -> None:
    """
    Raises ValueError for a role/evidence-level combination this
    manifest entry cannot support. `role` must be one of PIT_ROLES.
    An entry with allowedRoles=None permits any role (the common case);
    an entry with an explicit allowedRoles set (settlement_outcome,
    kalshi_closing_market_quote) restricts to exactly that set --
    e.g. EVALUATION_TARGET only, never PREDICTIVE_INPUT, regardless of
    evidence level.
    """
    if role not in PIT_ROLES:
        raise ValueError(f"Unknown PIT requirement role {role!r} for {data_family_key!r}. Known roles: {sorted(PIT_ROLES)}")
    entry = get_entry(data_family_key)
    allowed_roles = entry.allowedRoles if entry.allowedRoles is not None else PIT_ROLES
    if role not in allowed_roles:
        raise ValueError(
            f"{data_family_key!r} may only be used in role(s) {sorted(allowed_roles)}, not {role!r}. "
            f"See lib.edgelab.pit_provenance.PIT_MANIFEST[{data_family_key!r}].auditNotes."
        )
    if role == ROLE_PREDICTIVE_INPUT:
        ok, reason = check_predictive_compatibility(data_family_key, evidence_level)
        if not ok:
            raise ValueError(reason)


def validate_pit_requirements(pit_requirements: dict, evidence_level: str) -> None:
    """
    `pit_requirements`: {dataFamilyKey: role}. Raises ValueError/KeyError
    on the first incompatible or unknown entry -- called by
    lib.edgelab.experiment_registry.validate_experiment_definition (at
    registration time) and re-checked by
    lib.edgelab.experiment_report.build_experiment_report (at report
    time, as a defense-in-depth gate for favorable dispositions) so a
    later relaxation elsewhere can never silently let an incompatible
    PIT requirement through.
    """
    for data_family_key, role in pit_requirements.items():
        validate_pit_requirement(data_family_key, role, evidence_level)
