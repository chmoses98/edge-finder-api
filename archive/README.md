# archive/

Files moved here on 2026-06-07 during post-implementation audit.
These were identified as exploratory, debug-only, or superseded scripts with no reference
in any active workflow, GitHub Action, or documentation.

They are preserved here for reference. Do not load these files at session start.

## Contents

| File | Original path | Reason archived |
|------|--------------|-----------------|
| `debug_kalshi.py` | root | One-off debug; duplicates scripts/ functionality |
| `probe_kalshi.py` | root | One-off probe; root-level clutter |
| `scripts/debug_savant_batting.py` | scripts/ | Debug script; no workflow reference |
| `scripts/discover_remaining.py` | scripts/ | Exploratory; no production use |
| `scripts/discover_series.py` | scripts/ | Exploratory; no production use |
| `scripts/enumerate_all_markets.py` | scripts/ | Superseded by build_kalshi_registry.py |
| `scripts/final_sweep.py` | scripts/ | No reference anywhere in docs or Actions |
| `scripts/quick_check.py` | scripts/ | No workflow reference |
| `scripts/show_kalshi_series.py` | scripts/ | Exploratory |
| `workflows/debug-kalshi.yml` | .github/workflows/ | Debug workflow; not in production sequence |

## Restoration

To restore any file, copy from `archive/<path>` back to the original path and commit.
