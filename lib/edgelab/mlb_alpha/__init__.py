"""
lib/edgelab/mlb_alpha
=====================
MLB-ALPHA-0003: full-market Kalshi MLB research and opportunity engine.

RESEARCH / PAPER ONLY. Nothing in this package places an order, reads a
trading credential, changes a production probability, recommendation,
family eligibility, stake or fee, or touches the wager ledger.

Modules
-------
taxonomy   -- series classification (MLB vs not-MLB, family, scope, model and
              research status) and an exact market-ticker parser
registry   -- dynamic market registry built from Kalshi series / market rows
sim        -- one opponent-adjusted run-environment simulator that prices
              every game-level contract family from the same simulated games
execution  -- executable-price, depth-walk and fee economics (reuses
              lib.edgelab.kalshi_fees)
engine     -- opportunity scan / rank / PASS
paper      -- paper-trading ledger (simulated fills, settlement, reporting)
live_guard -- live execution safety layer; HARD-DISABLED, no network, no keys

Heavy third-party packages (numpy) are imported inside the functions that
need them so that the stdlib-only layers stay importable in CI.
"""

PROGRAM_ID = "MLB-ALPHA-0003"
SCHEMA_VERSION = "mlb_alpha_0003_v1"
