# KALSHI_PRICE_CHECKER.md

Standalone Kalshi MLB price-check tool. **This tool reports market
prices only. It does not determine whether a wager has positive
expected value.**

## What this tool does

Retrieves, normalizes, filters, displays, and (optionally) archives
current Kalshi MLB market prices — full-game moneyline, F3/F5/F7
inning-result Away/Tie/Home, full-game totals, inning totals, team
totals, run lines, winning margins, NRFI/YRFI, pitcher/hitter props —
**without** running the slate, projection, recommendation, risk,
execution, or settlement pipeline.

Only markets in a strict, evidence-based single-game MLB registry are
ever included in the daily output — see
[`KALSHI_PRICE_CHECKER_STRICT_REGISTRY.md`](KALSHI_PRICE_CHECKER_STRICT_REGISTRY.md)
for the full list of approved series, the exclusion-reason taxonomy, and
why season leaders/awards/futures/other-competition markets (which a
broader, separate audit tool also flags as "MLB-associated") never reach
this tool's output.

## What this tool does NOT do

- Does not run projections or generate model probabilities.
- Does not calculate model edge (every edge-shaped field is either
  absent or explicitly marked unavailable).
- Does not generate recommendations, confidence tiers, or bet sizes.
- Does not create pending bets or touch `bets.json`, `data/slate.json`,
  `data/pending_bets.json`, or `data/execution_slip.json`.
- Does not affect bankroll calculations.
- Does not activate any market for real-money eligibility.
- Does not dispatch any production workflow.
- Does not place or simulate any bet.
- Never imports `scripts/build_market_ledger.py`, `scripts/risk_gate.py`,
  `scripts/write_pending_bets.py`, `scripts/protect_slate.py`, or
  `scripts/validate_slate_final.py` — see
  `tests/test_check_kalshi_prices_safety_isolation.py` for the
  automated proof.

## Local usage

```bash
python3 scripts/check_kalshi_prices.py \
  --date 2026-07-29 \
  --team Yankees \
  --scope F5 \
  --family inning_result \
  --include-unknown \
  --format table
```

All filters are optional and combine freely. Matching is
case-insensitive. An exact `--ticker` lookup always takes priority
over every other filter. A valid search with zero matches prints a
clear message and exits `0`; a genuine fetch/parse failure exits
non-zero.

### Example commands

```bash
# 1. Every open Kalshi MLB market today
python3 scripts/check_kalshi_prices.py --date 2026-07-29

# 2. All markets for a specific matchup
python3 scripts/check_kalshi_prices.py --game "Yankees vs Red Sox"

# 3. F3/F5/F7 examples
python3 scripts/check_kalshi_prices.py --scope F3 --family inning_result --game "Cubs vs Cardinals"
python3 scripts/check_kalshi_prices.py --scope F5 --outcome Tie
python3 scripts/check_kalshi_prices.py --scope F7 --family inning_result --include-unknown

# 9. Pitcher props (real markets not yet confirmed to exist -- shown
#    only if the discovery layer ever surfaces one; text-matches the
#    title/subtitle, independent of family classification)
python3 scripts/check_kalshi_prices.py --pitcher "Paul Skenes"

# 6. Exact ticker lookup
python3 scripts/check_kalshi_prices.py --ticker KXMLBF5-26JUL292210SEALAD-TIE

# 7. Unknown/unsupported markets only
python3 scripts/check_kalshi_prices.py --family unknown --include-unknown

# 8. Verify current executable prices without running the slate
python3 scripts/check_kalshi_prices.py --source live --format table

# 10. Selected games only (exact matchups -- never a substring match)
python3 scripts/check_kalshi_prices.py --games "PIT@CIN,NYY@BOS"

# 11. Only games that have not started yet
python3 scripts/check_kalshi_prices.py --exclude-started
```

## GitHub Actions usage

Run the **Kalshi Price Check (Standalone)** workflow manually
(`workflow_dispatch`) from the Actions tab. It exposes exactly **10**
top-level inputs -- **not 15**, and this is deliberate: GitHub Actions
hard-caps `workflow_dispatch` at 10 inputs (exceeding it produces
"you may only define up to 10 inputs for a workflow_dispatch event").
An earlier version of this workflow defined 15 individual inputs,
which is why the "Run workflow" form behaved erratically -- editing
one field (e.g. `max_results`) would revert other fields to their
defaults. That was the direct, predictable symptom of exceeding this
platform limit, not a random UI bug.

The 8 common filters each have their own input: `source`, `team`,
`game`, `scope`, `family`, `include_unknown`, `include_closed`,
`max_results`. `include_unknown`, `include_closed`, and
`archive_snapshot` are real `type: boolean` inputs (native checkboxes
in the UI). The rarely-used filters -- `date`, `outcome`,
`participant`, `ticker`, `event_ticker`, `series_ticker`, plus
`games` (selected games only) and `exclude_started` (not-yet-started
games only) -- are consolidated into a single `advanced_filters_json`
input, a JSON object string, e.g.:

```json
{"date": "2026-07-30", "ticker": "KXMLBF5-26JUL292210SEALAD-TIE"}
```
```json
{"games": "PIT@CIN,NYY@BOS", "exclude_started": true}
```

Leave `advanced_filters_json` blank for none. It is parsed by
`scripts/parse_advanced_filters.py`, which fails the run loudly (not
silently) on invalid JSON or an unrecognized key -- see
`tests/test_parse_advanced_filters.py`. `games` takes a comma-separated
list of exact `AWAY@HOME` matchups (never a substring match -- see
`--games` above); `exclude_started` is a boolean flag (present it as
`true` to enable, or omit the key). Both apply ONLY to this run's own
display output (`kalshi_price_check.json`/`.csv`/table/summary) -- the
research-corpus archive below always captures the complete, unfiltered
market universe regardless of any display filter.

Safe defaults: `source=auto`, `include_closed=false`,
`include_unknown=true`, `archive_snapshot=false`, `max_results=250`,
`advanced_filters_json=""`. The workflow only ever invokes
`scripts/check_kalshi_prices.py`, writes a job summary, uploads
JSON/CSV/metadata artifacts (and an archive-ready bundle if
`archive_snapshot=true`), and never commits anything to the
repository.

### Reading results on GitHub Mobile

Every returned market is rendered as a single Markdown table directly in
the job summary AND printed to the workflow log -- no artifact download
needed, which matters because the GitHub mobile app surfaces the job
summary and log tab far more readily than run artifacts. Both are the
exact same rendering (`lib.kalshi_price_check.
format_mobile_markdown_table()`, invoked once via `scripts/
print_price_check_table.py` and piped through `tee` to both
destinations at once), so they can never drift out of sync.

The table is sorted Game -> Market Family (Winner, Run Line, Total, Team
Total, NRFI/YRFI, Pitcher Props, Player Props, Other) -> Scope (F3, F5,
F7, Full Game) -> Market, so every market for one game is grouped
together -- easy to scan one game at a time on a phone. It is capped at
250 rows for readability regardless of `max_results`; any remainder is
called out explicitly, and the JSON/CSV artifacts always remain the
complete, uncapped result. A zero-market run shows "No markets matched
the requested filters." instead of a blank table. This is purely a
display layer -- it never re-fetches, re-prices, re-filters, or
re-classifies anything; see
`tests/test_kalshi_price_check_mobile_table.py`.

## Filters

| Flag | Meaning |
|---|---|
| `--date` | Date filter |
| `--game` | Matchup substring |
| `--games` | Comma-separated list of exact `AWAY@HOME` matchups (case-insensitive, never a substring match, unlike `--game`) -- e.g. `--games "PIT@CIN,NYY@BOS"` |
| `--exclude-started` | Exclude games whose scheduled start has already passed, via `lib.kalshi_price_check.game_has_started()` (reuses `lib.edgelab.checkpoints.classify_checkpoint`'s `POST_START` classification -- the same authoritative "has first pitch happened" logic `lib.edgelab.market_universe` already uses; never a live game-status fetch) |
| `--team` / `--away-team` / `--home-team` | Team filters |
| `--family` | Market family (`inning_result`, `game_result`, `team_total`, `unknown`, ...) |
| `--scope` | Horizon (`F3`, `F5`, `F7`, `full_game`) |
| `--outcome` | `Away` / `Tie` / `Home` |
| `--participant` / `--pitcher` / `--hitter` | Text match against participant/title/subtitle |
| `--ticker` | Exact market ticker (takes priority over everything else) |
| `--event-ticker` / `--series-ticker` | Exact ticker matches |
| `--status` | Market status |
| `--include-closed` / `--exclude-unknown` | Toggle inclusion (unknown markets included by default) |
| `--max-results` | Cap result count |
| `--format table\|json\|csv` | Output format |
| `--output` | Write to a file instead of stdout |
| `--metadata-output` | Write the full diagnostic metadata (fetch endpoint/HTTP status, raw/normalized/classified/unknown counts, per-filter-stage removal counts, and an always-populated `diagnosis` string) to this path as JSON. **Always pass this in automation** -- see "No silent zero results" below. |
| `--archive` | Also write `kalshi_price_check_artifacts/{json,csv,metadata}` |
| `--source live\|snapshot\|auto` | Data source mode |
| `--snapshot-path` | Use a specific snapshot file |
| `--cache-ttl-seconds` | Local rate-limit-protection cache (default 45s) |
| `--verbose` | Print metadata to stderr |

## Live vs. snapshot modes

- **`live`**: requires a live, read-only fetch from the deployed
  `/api/kalshisearch` endpoint. Fails clearly (non-zero exit) if
  unavailable — never silently falls back.
- **`snapshot`**: uses only the supplied (`--snapshot-path`) or newest
  local snapshot file under `data/kalshi_registry_snapshots/`. Never
  attempts network access.
- **`auto`** (default): attempts live retrieval first, falls back to
  the newest valid local snapshot on failure, and clearly labels which
  source was actually used plus the fallback reason.

Every output includes `sourceMode`, `sourceUsed`, `snapshotTimestamp`,
`retrievedAt`, and a `pricesMayBeStale` flag. Snapshot output is always
labeled `SNAPSHOT PRICE — captured <timestamp>` in table mode — it is
never presented as a current live price.

## Executable price definitions

- **Buying YES** executes near the **YES ask**.
- **Selling YES** executes near the **YES bid**.
- **Buying NO** executes near the **NO ask** (derived as `1 - YES bid`
  — this repository's snapshot format does not independently capture
  NO-side pricing).
- **Selling NO** executes near the **NO bid** (`1 - YES ask`).
- **Midpoint** is descriptive only, never labeled executable.
- **Last trade** may be stale and is never labeled executable.

This tool never calculates a recommended wager or expected value.

## Three-way (F3/F5/F7) display

Discovered Away/Tie/Home legs for the same event+scope are grouped
together, showing each leg's YES/NO bid/ask, the sum of YES asks, the
sum of midpoints, and an explicit missing-leg warning. **A three-way
structure is only assumed when independently verified** (F5 today) —
F3/F7 legs are shown with every raw discovered contract and a
`Structure: UNRESOLVED` label, never a synthesized missing outcome.

## Unknown-market behavior

Markets outside the model's current classification (unrecognized
series, unverified props, anything not yet supported) are **included
by default** (`--include-unknown` is the default; pass
`--exclude-unknown` to hide them). The production activation allowlist
is never consulted by this tool — discovery here is intentionally
broader than what production evaluates.

## Stale-data warnings

Any output sourced from a snapshot (rather than a fresh live fetch) is
labeled with its capture timestamp in every format, and
`pricesMayBeStale: true` is set in the JSON/CSV metadata.

## Output artifacts

- `kalshi_price_check.json`
- `kalshi_price_check.csv`
- `kalshi_price_check_metadata.json` (filters used, source
  requested/used, retrieval timestamp, market counts by terminal
  status, fallback reason, raw vs. normalized record counts)

Guarantee: `raw records fetched == normalized records + explicitly
rejected malformed records`, and every fetched market resolves to
exactly one terminal status (`Included`, `Filtered Out`,
`Classification Unknown`, `Missing Price`, `Malformed Record`,
`Duplicate Record`, `Unsupported Market`) — see
`tests/test_kalshi_price_check_lib.py`'s no-silent-drop tests.

### Why the artifact downloads as a ZIP, and how to avoid it

GitHub Actions' `actions/upload-artifact` always packages the uploaded
file(s) as a ZIP on download — this is inherent platform behavior with
no opt-out, not a workflow bug, and not worth fighting (e.g. with a
third-party action or a raw REST API call) for a small internal tool.
Two lightweight, zero-download alternatives already exist instead:

- **Job summary table** — every returned market, rendered as one
  Markdown table (`scripts/print_price_check_table.py`), visible
  directly on the run's summary page and in the workflow log (surfaces
  well on GitHub Mobile — see below).
- **Job summary raw JSON** — the exact same `kalshi_price_check.json`
  the JSON artifact contains, embedded as a collapsed `<details>` block
  (`scripts/print_price_check_json_summary.py`) — expand it and
  copy-paste the complete JSON with no download at all. If the result
  is too large to embed safely, the block says so explicitly and points
  at the `kalshi-price-check-json` artifact instead of silently
  truncating into invalid JSON.

The `kalshi-price-check-json`/`-csv`/`-metadata` artifacts remain the
complete, uncapped result for programmatic consumption or archiving —
this only adds a second path, it does not remove the first.

## No silent zero results

A successful run that returns zero markets always explains why, via
`metadata["diagnosis"]` (also always printed to stderr, independent of
`--verbose`) and the full stage-by-stage breakdown in
`--metadata-output`'s JSON:

```
Source used: live
Endpoint: https://edge-finder-api.vercel.app/api/kalshisearch
HTTP status: 200
Raw records fetched: 642
Normalized: 642
Classified: 601
Unknown: 41
Filtered by date: 570
Filtered by status: 18
Filtered by family: 5
Returned: 8
```
or, for a genuinely empty source:
```
Raw records fetched: 0
Reason: Live endpoint (or snapshot) returned zero raw records.
```
or, when filters removed everything:
```
Returned: 0
Reason: All records removed by the 'date' filter stage.
```

The GitHub Actions workflow always requests `--metadata-output` and
renders every counter in the job summary (via
`scripts/print_price_check_summary.py`) — a run that returns zero
markets is never reported as just "0 market(s) matched" with nothing
else. If `rawRecordsFetched` is 0 in `live` mode with HTTP 200, that
means the deployed endpoint itself returned no markets (an external/
upstream condition — check whether Kalshi has any open MLB markets for
the requested date, e.g. an off-day or off-season) — it is not this
tool silently dropping data.

## Troubleshooting

- **"FETCH ERROR: live fetch failed"** in `--source live` mode: the
  deployed API endpoint was unreachable — this is expected behavior
  (a genuine failure, not silently swallowed); retry, or use
  `--source auto` / `--source snapshot`.
- **"snapshot file not found"**: no local snapshot exists yet, or
  `--snapshot-path` points to a missing file — run a snapshot-capture
  workflow first, or check the path.
- **Empty results with exit code 0**: this is correct behavior for a
  valid search with no matches — check your filters, not the tool.
- **"Run workflow" form reverts a field I just edited**: known GitHub
  Actions behavior when a `workflow_dispatch` workflow defines more
  than its documented maximum of 10 inputs — this workflow was fixed
  to use exactly 10 (see "GitHub Actions usage" above); if you see
  this again, count the top-level `inputs:` keys before assuming it's
  a fresh bug.
- **`advanced_filters_json` run fails immediately**: the JSON is
  either invalid or contains a key outside `date`, `outcome`,
  `participant`, `ticker`, `event_ticker`, `series_ticker` — the job
  log names the exact problem; this is a loud, intentional failure,
  not the workflow silently ignoring part of your input.

## Security boundaries

- No credentials are read, stored, or required — the tool only makes
  unauthenticated, read-only HTTP GET requests.
- The local rate-limit cache (`.kalshi_price_check_cache/`) never
  contains credentials and is never committed.
- No order, cancellation, or account-mutation function exists anywhere
  in this tool (`tests/test_check_kalshi_prices_safety_isolation.py`
  proves this via AST inspection, not just documentation).
