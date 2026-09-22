"""
MRV prospective collector v1 (RESEARCH ONLY, READ-ONLY).

A SEPARATE, PARALLEL capture path for the MRV market-structure program.
It shares nothing with the MLB-ALPHA-0002 prospective collector: its own
storage root, branch, run identity, state file, series policy and health
report.  It never reads or writes data/edgelab/research_artifacts/mlb_alpha_0002/
and therefore cannot change the data universe seen by the frozen ALPHA-0002
shadows (C01-F5REV, C03-BOOKIMB).

It imports no production module (no eligibility, recommendations, staking,
routing or settlement) and calls only public GET endpoints.
"""

COLLECTOR_ID = "MRV_PROSPECTIVE_COLLECTOR"
COLLECTOR_VERSION = "v1.0.0"
SCHEMA_VERSION = "mrv_prospective_v1"
STORAGE_RELATIVE_ROOT = "data/edgelab/research_artifacts/mrv_prospective/v1"
DEFAULT_RESEARCH_BRANCH = "research/mrv-prospective-v1"
