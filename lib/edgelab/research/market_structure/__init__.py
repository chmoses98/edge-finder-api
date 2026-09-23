"""
MRV market-structure research package (RESEARCH ONLY).

Shared primitives for the Kalshi MLB market-structure / relative-value /
information / execution research program described in
docs/EDGELAB_MRV_MARKET_STRUCTURE_PROGRAM.md.

Nothing in this package reads production probabilities, places orders, or
writes to any production, ledger, or raw-archive path. Every module is
standard-library only so the PR CI suite (which installs no numpy) can test
it.
"""
