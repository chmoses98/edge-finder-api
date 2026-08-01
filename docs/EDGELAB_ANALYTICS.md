# EdgeLab Phase 2 Milestone 1: DuckDB query foundation

Status: query foundation only — descriptive statistics, no calibration,
no market-expression clustering, no automated strategy recommendations,
no dashboards, no portfolio optimization. Source of truth for the
architecture this implements: `docs/EDGELAB_PHASE2_DESIGN.md`.

## 0. What this is (and isn't)

`lib/edgelab/analytics.py` reads the existing git-committed
`data/edgelab/<entity>/*.jsonl[.gz]` partitions directly with DuckDB and
exposes cross-date, cross-file queries over them. It does not add a
server, a daemon, a paid service, or a second copy of the data. It
never claims statistical significance and never recommends a strategy
change — every grouped metric carries an explicit sample-size status
and must be read as descriptive only.

## 1. Running it locally

```bash
pip3 install duckdb   # if not already installed
python3 scripts/edgelab/run_analytics.py
```

This opens one disposable, in-memory DuckDB session over
`data/edgelab/`, runs the Milestone 1 query set, and writes:

- `data/edgelab/analytics/latest_summary.json` — machine-readable, one
  key per query, `sortkeys` stable so diffs are meaningful.
- `data/edgelab/reports/phase2_query_foundation.md` — the same data,
  human-readable.

Both files are regenerated (not appended to) on every run and are
committed, like every other EdgeLab report, so `git diff` shows exactly
how the numbers moved since the last ingest. Nothing else is written;
this script never touches `data/edgelab/<entity>/` itself.

To query ad hoc instead of running the CLI:

```python
from lib.edgelab.analytics import open_session

with open_session() as session:
    print(session.fetchall("SELECT * FROM v_placed_bets LIMIT 5"))
```

`open_session(root=...)` takes an optional root directory, which is how
the test suite (`tests/edgelab/test_analytics.py`) points it at a
`tmp_path` fixture instead of the real data.

## 2. What gets queried

One DuckDB `VIEW` per entity (`raw_<entity>`), built directly from
`read_json_auto(glob_pattern, union_by_name=true, filename=true)` — no
ETL step, no intermediate file. `union_by_name=true` is what lets one
glob mix files written before and after a schema change (e.g. before
and after the `sport`/`platform` fields existed): DuckDB fills in NULL
for whichever file is missing a given column. An entity with zero
matching files is reported as `unavailable`, not an error — it's a
normal, common state early in the project's life. An entity that does
have files but one of them is malformed JSON or a corrupt gzip stream
raises `AnalyticsDataError` as soon as `open_session()` registers it
(DuckDB samples every matched file to infer its columns at that point),
wrapping DuckDB's own message, which names the exact file and byte
offset.

On top of `raw_<entity>`, `register_canonical_views()` builds one
`v_<entity>` view per entity that has bet-relevant market-family data
(`v_placed_bets`, `v_market_observations`, `v_recommendations`,
`v_settlements`; `v_clv_quotes` has no market family to canonicalize).
Every column in these views — not just `sport`/`platform` — is
resolved through a column-existence check first (`_select_or_null` /
`_coalesce_or_default` in `analytics.py`), because a field can be
schema-optional and genuinely absent from every row currently on disk;
DuckDB doesn't infer a column at all in that case, so a naive
`alias.column` reference would fail to bind instead of just reading
back NULL.

Nothing here loads a full season into Python: every public function in
`analytics.py` returns the small, already-aggregated result of one SQL
query (DuckDB does the file scanning), never a per-row Python list
built by iterating the raw JSONL.

## 3. Canonical market-family vocabulary

`PlacedBet.marketFamily` (and the equivalent field on observations,
recommendations, and settlements) is free text, copied at ingestion
time from whichever of three different legacy conventions produced the
record: raw Kalshi series tickers (`KXMLBGAME`), the `config/rules.json`
model-naming convention (`F5_ML_Away`), and older sportsbook-style
abbreviations (`ML`, `YRFI`). Measured directly against the real
committed `data/edgelab/bets/bets.jsonl`, these three conventions
produce 11 different spellings for what is really only 5 distinct
families in `lib.research.market_taxonomy`'s 17-family taxonomy.

Every canonicalizing view exposes both:

- `rawMarketFamily` — the original value, verbatim, never discarded.
- `canonicalMarketFamily` — one of the 17 taxonomy families, or one of
  two sentinels:
  - `UNKNOWN` — the raw value is null, empty, or a known "no value"
    placeholder (`"N/A"`, `"none"`, `"null"`, `"unknown"`, ...).
  - `UNMAPPED` — the raw value is a real, non-empty string that isn't
    yet in the mapping table. Never guessed at; a new spelling always
    falls through to `UNMAPPED` until someone adds it.

### How to add a mapping safely

There is exactly one place to edit:
`lib/edgelab/market_family_mapping.py`'s `MARKET_FAMILY_ALIASES` dict.
Every canonicalizing SQL view joins against a `family_mapping` table
built straight from that dict (`register_family_mapping_table()`) — not
a scattered `CASE WHEN` per view — so adding support for a newly
observed spelling is a one-line addition there, not a hunt through
several SQL strings.

1. Run `python3 scripts/edgelab/run_analytics.py` and check the
   "Unmapped market-family values" section of the report, or query
   `unmapped_market_family_values()` / `SELECT * FROM v_placed_bets
   WHERE canonicalMarketFamily = 'UNMAPPED'` directly.
2. Add one entry to `MARKET_FAMILY_ALIASES`, mapping the exact raw
   string to one of the `FAMILY_*` constants from
   `lib.research.market_taxonomy`. Never repurpose an existing key's
   meaning — add a new key instead, so old runs' output stays
   explainable.
3. Run `python3 -m pytest tests/edgelab/test_analytics.py` (the
   canonicalization tests enumerate every spelling observed in
   production and assert none of them map to `UNMAPPED`) and re-run the
   CLI to confirm the new spelling no longer appears in the unmapped
   list.

Matching is exact and case-sensitive: every spelling actually observed
in the real data is consistently cased, so a differently-cased variant
that shows up in the future safely falls through to `UNMAPPED` rather
than silently guessing, and gets added here once confirmed.

## 4. Sample-size status

Every grouped metric (bets by family, ROI by family, CLV by family)
carries a `sampleStatus` computed by the same non-configurable CASE
expression (`lib.edgelab.analytics.sample_size_status_sql`,
`MIN_SAMPLE_SIZE = 20`):

- `n < 20` → `INSUFFICIENT_SAMPLE` — noise, not evidence.
- `n >= 20` → `DESCRIPTIVE_ONLY` — a real Milestone 1 number, but still
  not a calibrated statistical claim; that's a later milestone.

The underlying value (ROI, average CLV, ...) is always returned
alongside the status — never withheld — so the status is a required
reading instruction, not a filter. As of this report, no canonical
family in the real committed data reaches 20 settled bets or CLV
observations, so every current ROI/CLV row is `INSUFFICIENT_SAMPLE`;
this is expected this early in the project and is not itself a
finding.

## 5. Why no persisted `.duckdb` file and no committed Parquet

Milestone 1's explicit scope is the smallest production-safe query
foundation: `open_session()` opens an in-memory (`:memory:`) DuckDB
connection and closes it (or lets it fall out of scope) at the end of
every run, leaving nothing behind beyond the source JSONL. This keeps
the source-of-truth singular (the committed JSONL/JSONL.gz files
production already writes) — a persisted `.duckdb` file or committed
Parquet export would be a second copy of the data that could drift out
of sync, would need its own regeneration/staleness story, and isn't
needed yet: the current data volume reads back in well under a second
per full-history query, and every query here is already read-only,
disposable, and re-derivable from scratch on every run.

## 6. Current limitations

- **No calibration.** ROI/CLV numbers are raw descriptive statistics,
  not model-calibrated estimates. See `docs/EDGELAB_PHASE2_DESIGN.md`
  for where that lands in a later milestone.
- **No cross-entity joins in the CLI yet.** Each canonical view stands
  alone (e.g. bets aren't joined to their originating recommendation or
  settlement in the shipped query set); the views expose the join keys
  (`recommendationId`, `betId`, `gameId`) needed to do that in a future
  milestone.
- **Sample sizes are small across the board.** The real committed data
  is 77 placed bets total; almost every canonical-family bucket is
  below `MIN_SAMPLE_SIZE`. This is a fact about the data's current
  size, not a bug in the query layer.
- **`gameId`-based joins inherit the known doubleheader-collision gap**
  already documented in `docs/CANONICAL_SCHEMAS.md` and
  `docs/EDGELAB_PHASE1.md` — this milestone doesn't change that.
- **Completeness metrics reflect what was actually written, not the
  read-time COALESCE default** — e.g. `sport`/`platform` currently show
  `FIELD_NEVER_WRITTEN` against the real data (every committed record
  predates those fields), which is correct: querying through the
  canonical view's default would make a never-populated field look
  100% complete, which would be misleading.
