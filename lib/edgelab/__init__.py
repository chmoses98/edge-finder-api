"""EdgeLab Phase 1: durable MLB/Kalshi research collection and linkage.

Collection and linkage only — no staking engine, no auto-betting. See
docs/EDGELAB_PHASE1.md and data/edgelab/schema_v1/README.md.
"""

SCHEMA_VERSION = "1"

# Phase 2 future-proofing fields (docs/EDGELAB_PHASE2_DESIGN.md §2.1).
# Every EdgeLab writer as of this phase is MLB/Kalshi-only, so these are
# hardcoded literals, not configuration -- the day a second sport or
# platform is added, each writer's call site is where that decision
# actually gets made, not here. Defined once so every writer sets the
# same literal value rather than five independent copies of "MLB"/"KALSHI".
DEFAULT_SPORT = "MLB"
DEFAULT_PLATFORM = "KALSHI"
