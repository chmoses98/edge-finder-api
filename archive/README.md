# archive/

Files moved here during post-implementation audit (June 2026).
These are exploratory, debug-only, or superseded files with no role in active workflows.
Do not load any of these files at session start.

## archive/scripts/ — orphaned scripts

| File | Original path | Reason |
|------|--------------|---------|
| `debug_savant_batting.py` | scripts/ | Debug script; no workflow reference |
| `discover_remaining.py` | scripts/ | Exploratory; no production use |
| `discover_series.py` | scripts/ | Exploratory; no production use |
| `enumerate_all_markets.py` | scripts/ | Superseded by build_kalshi_registry.py |
| `final_sweep.py` | scripts/ | No reference anywhere |
| `quick_check.py` | scripts/ | No workflow reference |
| `show_kalshi_series.py` | scripts/ | Exploratory |

## archive/workflows/ — orphaned workflows

| File | Original path | Reason |
|------|--------------|---------|
| `debug-kalshi.yml` | .github/workflows/ | Debug workflow; not in production sequence |

## archive/ root — orphaned root-level scripts

| File | Original path | Reason |
|------|--------------|---------|
| `debug_kalshi.py` | root | Duplicates scripts/ functionality |
| `probe_kalshi.py` | root | One-off probe |

## archive/data/ — debug artifacts from data/

These files were produced by exploratory/debug scripts, not by the fetch-slate Action.
The Action writes a fixed set of files to data/ on each run — these are not in that set.

| File | Reason |
|------|--------|
| `debug_endpoints.json` | Debug output |
| `kalshi_confirmed_series.json` | Discovery artifact |
| `kalshi_final_sweep.json` | Discovery artifact (large) |
| `kalshi_full_enumeration.json` | Discovery artifact |
| `kalshi_markets.json` | Superseded by kalshi_market_registry.json |
| `kalshi_odds_history.json` | Historical snapshot; not refreshed by Action |
| `kalshi_probe_output.txt` | Debug output |
| `kalshi_remaining_discovery.json` | Discovery artifact |
| `kalshi_series_discovery.json` | Discovery artifact |
| `quick_check_output.txt` | Debug output |

## Restoration

To restore any file, copy from `archive/<path>` back to the original path and commit.
