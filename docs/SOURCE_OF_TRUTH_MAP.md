# SOURCE_OF_TRUTH_MAP.md

Phase 2, Part 2 — every authoritative object in the repository.

**No migrations are performed in this document or this phase.** This is a
map of what exists today, who owns it, who writes it, who reads it, what
duplicates exist, and a recommendation on whether it should remain
authoritative — recommendations only, not actions.

---

## 1. The authoritative slate

| | |
|---|---|
| **Object** | The daily model output: schedule, projections, edges, `marketLedger[]` per game |
| **Canonical location** | `data/slates/<date>/authoritative.json` |
| **Owner** | `lib/slate_manager.py` (`save_slate`, `detect_run_type`) |
| **Writers** | `scripts/protect_slate.py` (calls into `slate_manager`) |
| **Readers** | Intended reader: post-slate review / analyst tooling, per `protect_slate.py`'s own docstring ("Post-slate review MUST use data/slates/DATE/authoritative.json as source of truth") |
| **Duplicate copies** | `data/slate.json` (root) — kept in sync by convention: `protect_slate.py` copies `authoritative.json` → `slate.json` after every non-contaminated run. This is a **soft convention, not an enforced contract** — nothing prevents a future script from writing `data/slate.json` directly without going through `protect_slate.py` and silently diverging from the "real" authoritative copy. |
| **Lifecycle** | Written once per slate date on `OFFICIAL_PREGAME` (first successful run), then either left untouched (`LINEUP_RECHECK`/`IN_PLAY_RECHECK` write to `recheck_<ts>.json` instead) or never created at all (`REJECTED_CONTAMINATED` runs write only to `rejected_contaminated_<ts>.json` — confirmed real for at least 3 historical dates: 2026-06-15, 2026-06-18, 2026-07-22, which have no `authoritative.json`). |
| **Should remain authoritative?** | **Yes, but the soft-sync convention with `data/slate.json` should be hardened in a future phase** (Phase 4 schema work) — e.g. by making every downstream script read `authoritative.json` directly instead of `data/slate.json`, or by making the copy step schema-validated rather than a blind file copy. Not changed this phase. |

## 2. `data/slate.json` (the practically-consumed slate)

| | |
|---|---|
| **Object** | Working copy of the slate that ~18 scripts actually read/write during the pipeline run |
| **Owner** | No single owner — mutated in place by ten scripts (see `docs/MODEL_V2_ARCHITECTURE.md` §7) |
| **Writers** | `fetch_savant_pitchers.py`, `fetch_lineups.py`, `enrich_lineup_confirmed.py`, `post_fetch_gate.py`, `merge_odds.py`, `enrich_data.py`, `build_market_ledger.py`, `validate_slate_final.py`, `protect_slate.py` (overwrites wholesale from authoritative), `risk_gate.py` — still ten scripts, still no single owner; ownership has not changed across Phases 3-5, only *how* four of them (`enrich_lineup_confirmed.py`, `merge_odds.py`, `fetch_savant_pitchers.py`, `fetch_lineups.py`) compute what they write (see `docs/IMMUTABLE_PIPELINE.md` §4) |
| **Readers** | All of the above plus `write_pending_bets.py`, `validate_bet_logging.py`, `write_tracked_tickers.py`, `regression_test.py`, `validate_current_slate_date.py`, `validate_slate_pre.py` |
| **Duplicate copies** | Is itself the "working" duplicate of `authoritative.json` above |
| **Lifecycle** | Rewritten on every pipeline stage within a single run; not versioned; not date-partitioned (unlike `authoritative.json`, which is). **(Phase 5)** `fetch_lineups.py`'s and `fetch_savant_pitchers.py`'s writes are now atomic (temp file + `fsync` + `os.replace()`, matching `lib/pipeline_artifacts.py`'s mechanism applied inline) — a serialization failure can no longer leave a truncated file at this path for those two writers specifically. The other eight writers still use a plain `open(path, 'w')` + `json.dump()`, sharing the same theoretical (unfixed) truncation-on-failure gap `lib/slate_manager.py`'s non-atomic write already had (§9 of `docs/IMMUTABLE_PIPELINE.md`). |
| **Should remain authoritative?** | This is the file that is *practically* authoritative today (everything reads it), even though `protect_slate.py`'s own docs say `authoritative.json` should be. Still unresolved as of Phase 5 — recommend a future phase resolve this ambiguity explicitly rather than leaving two "authoritative slate" concepts alive side by side. |

## 3. The bet ledger

| | |
|---|---|
| **Object** | Every recommendation/bet ever logged: date, game, market, side, price, stake, result, P/L |
| **Canonical location** | **`bets.json` (repo root)** — confirmed via `docs/MODEL_V2_AUDIT.md` (Phase 0) and re-confirmed this phase: `data/rule71_report.json`'s `total_bets: 509` matches root `bets.json`'s 509 entries exactly |
| **Owner** | No single script owns writes — see writers below |
| **Writers** | `scripts/write_pending_bets.py` (new pending rows), `scripts/log_session_bets.py` (manual session bets — orphaned from CI), `clv_update.py` (settlement results), `scripts/run_kalshi_clv_step.py` → `clv_from_snapshot.py`/`fetch_kalshi_clv_v2.py` (closing-line fields), `scripts/capture_closing_lines.py` (settle mode — not currently invoked by any workflow in settle mode) |
| **Readers** | `scripts/validate_bet_logging.py`, `scripts/risk_gate.py` (indirectly via meta.json only), `scripts/audit_bet_identity.py`, `scripts/rule71_tracker.py`, `scripts/backfill_market_identity.py`, `scripts/generate_performance_report.py`, `scripts/snapshot_coverage_check.py`, `scripts/calibrate.py` |
| **Duplicate copies** | **`data/bets.json` — confirmed stale duplicate.** 92 entries, most recent 2026-06-18, no script writes to it anymore. Left untouched per "do not delete historical data." |
| **Lifecycle** | Append-only in principle (new pending rows), but settlement scripts mutate existing records in place (adding `result`, `pnl`, `closingLine`, etc.) — so it is not a pure event log, it's a mutable record store keyed by a composite identity (date+game+market+ticker). |
| **Should remain authoritative?** | Yes for root `bets.json`. **`data/bets.json` should be reconciled (not silently deleted) in Phase 3** per the original Phase 0 audit's recommendation — this phase re-confirms the finding but does not act on it. |

## 4. `data/pipeline_status.json`

| | |
|---|---|
| **Object** | Machine-readable per-run stage status (`validate`/`protect`/`publish`/`risk_gate`/`write_pending_bets`/`validate_bet_logging`/`write_tracked_tickers`/`capture_closing_lines`) and overall `success`/`partial`/`failed` rollup |
| **Owner / Writer** | `fetch-slate.yml`'s final step (inline `jq`, not a Python script) — introduced in the Phase 1 hardening pass |
| **Readers** | None yet — this is a new artifact; nothing currently consumes it programmatically (e.g. no stale-slate alerting reads it) |
| **Duplicate copies** | None. `data/fetch_status.json` is a distinct, narrower object (see below) — not a duplicate despite superficial naming similarity. |
| **Lifecycle** | Overwritten every `fetch-slate.yml` run; not date-partitioned (only reflects the most recent run) |
| **Should remain authoritative?** | Yes. **Recommend Phase 3 build a consumer** (a stale-run/partial-run alert) — the artifact exists but nothing acts on it yet. |

## 5. `data/fetch_status.json`

| | |
|---|---|
| **Object** | Post-fetch data-quality gate result: `{status, requestedDate, actualDate, fetchedAt/failedAt, quarantinedGames}` |
| **Owner / Writer** | `scripts/post_fetch_gate.py`, committed unconditionally (`if: always()`) early in `fetch-slate.yml` |
| **Readers** | `scripts/data_quality_gate.py` (dead code, no production reader), `scripts/stale_date_guard.py` (test-only), `scripts/validate_current_slate_date.py` (production) |
| **Duplicate copies** | None |
| **Lifecycle** | Overwritten every run |
| **Should remain authoritative?** | Yes — this is exactly the artifact whose "success" reading, decoupled from `meta.json`'s staleness, caused the Phase 1 incident. Its distinctness from `meta.json` should be preserved, not merged. |

## 6. `data/meta.json`

| | |
|---|---|
| **Object** | `{fetchedAt, date, status, oddsSource}` — the pipeline's basic "did today's fetch and publish succeed" stamp, plus (merged in later) a `risk_gate` diagnostics block |
| **Owner / Writer** | `fetch-slate.yml`'s `publish_slate` step (inline), then `scripts/risk_gate.py` merges in a `risk_gate` key without overwriting the rest |
| **Readers** | `scripts/risk_gate.py`, `scripts/stale_date_guard.py` (test-only), `scripts/validate_bet_logging.py` (no — confirmed it does not read meta.json, only slate.json/bets.json), `scripts/validate_current_slate_date.py` |
| **Duplicate copies** | None — distinct purpose from `fetch_status.json` |
| **Lifecycle** | Overwritten every run at publish time |
| **Should remain authoritative?** | Yes, unchanged. |

## 7. The market registry

| | |
|---|---|
| **Object** | Persistent game→Kalshi-ticker map used for price lookup and closing-line capture |
| **Canonical location** | **`data/kalshi_market_registry.json`** — its own docstring calls it "the persistent source of truth" |
| **Owner / Writer** | `scripts/build_kalshi_registry.py` (hits Kalshi API directly for the 8-series catalogue; uses `data/kalshi_search.json` only as a team-total-line backfill supplement) |
| **Readers** | `scripts/merge_odds.py`, `scripts/capture_closing_lines.py`, `scripts/generate_f5_audit.py`, `scripts/validate_current_slate_date.py` |
| **Duplicate copies** | **`data/kalshi_market_index.json` + `data/kalshi_odds_history.json`** — a parallel, incomplete (ML-only, confirmed by sampling: `"by_type": {"moneyline": 30}`, zero F5/spread/total/NRFI markets) attempt built by `scripts/fetch_kalshi_markets.py`, whose only designated consumer (`scripts/build_final_index.py`) is dead code (never called by any workflow). This is the clearest duplicate found this phase that wasn't caught in Phase 0/1. |
| **Lifecycle** | `kalshi_market_registry.json` rebuilt every `fetch-slate.yml` run; `kalshi_market_index.json` also rebuilt every run (`fetch_kalshi_markets.py`, `continue-on-error: true`) but its output is not used for anything live. |
| **Should remain authoritative?** | `data/kalshi_market_registry.json`: yes. `data/kalshi_market_index.json`/`data/kalshi_odds_history.json`/`build_final_index.py`: **recommend deprecating in a future phase** — they silently produce incomplete data under a name that suggests completeness, and their only consumer is unreachable. Not removed this phase (see `docs/REFACTORING_OPPORTUNITIES.md` for why this is deferred rather than done now). |

## 8. `data/kalshi_search.json` and `data/kalshi_raw.json`

| | |
|---|---|
| **Object** | Raw, unprocessed curl output of `api/kalshisearch.js` (all 8 series) and `api/kalshi.js` (ML-only) respectively |
| **Writers** | `fetch-slate.yml` curl steps |
| **Readers** | `kalshi_search.json` → `build_kalshi_registry.py` (backfill), `backfill_market_identity.py`, archived into `kalshi_registry_snapshots/`; `kalshi_raw.json` → `preview_kalshi.py` (display only), `clv_capture.yml`'s fallback path |
| **Duplicate copies** | Not duplicates of each other or of the registry — genuinely different scope (raw vs. structured; ML-only vs. all-series) — but worth noting both exist because two different endpoints (`api/kalshi.js`, `api/kalshisearch.js`) independently reimplement Kalshi API parsing (see `docs/DUPLICATE_LOGIC_INVENTORY.md`) |
| **Should remain authoritative?** | Yes for both, as raw intermediate inputs — not user-facing authoritative objects themselves. |

## 9. Projections / model output

| | |
|---|---|
| **Object** | Per-team run projections, Poisson win/total probabilities, calibrated edges |
| **Canonical location** | Embedded directly inside `data/slate.json`'s per-game objects (no separate file) |
| **Owner / Writer** | `api/slate.js` (initial Poisson computation) → `scripts/enrich_data.py` (offense-baseline adjustment) → `scripts/build_market_ledger.py` (final edge/calibration) |
| **Readers** | Everything downstream in the pipeline |
| **Duplicate copies** | None found — but also **no independent, inspectable "raw model probability" object exists separately from the final calibrated edge**, which is exactly the gap `docs/CANONICAL_SCHEMAS.md`'s `Projection`/`MarketProbability` schemas are designed to close in a future phase. |
| **Should remain authoritative?** | The computation is authoritative; the fact that it's not materialized as its own versioned object (only as fields buried inside `slate.json`) is a Phase 4 target, not changed here. |

## 10. Settlement data

| | |
|---|---|
| **Object** | WIN/LOSS/PUSH/VOID results, P/L, closing lines |
| **Canonical location** | Fields within `bets.json` records (`result`, `pnl`, `closingLine`, `closingLinePct`) |
| **Owner / Writer** | `clv_update.py` (root), `lib/f5_settlement.py` (F5-specific settlement logic, imported by `clv_update.py`) |
| **Readers** | `scripts/rule71_tracker.py`, `scripts/generate_performance_report.py`, `scripts/audit_bet_identity.py` |
| **Duplicate copies** | None found |
| **Should remain authoritative?** | Yes, unchanged. |

## 11. CLV tracking / snapshots

| | |
|---|---|
| **Object** | Which tickers to snapshot for closing-line value, and the snapshots themselves |
| **Canonical locations** | `data/clv_snapshots/<date>/tracked_tickers.json` (what to track), `data/clv_snapshots/<date>/pregame_*.json` (the snapshots), `data/kalshi_registry_snapshots/kalshi_search_<date[_HHMM]>.json` (321 files — the broader market-wide archive) |
| **Writers** | `scripts/write_tracked_tickers.py`, `scripts/capture_clv_pregame.py` (via `clv_capture.yml`), `fetch-slate.yml`'s snapshot-archive step, `capture-snapshots-scheduled.yml` |
| **Readers** | `scripts/clv_from_snapshot.py`, `scripts/backfill_market_identity.py`, `scripts/snapshot_coverage_check.py` |
| **Duplicate copies** | `lib/slate_manager.py` contains a dead `persist_tracked_tickers()` function that duplicates `write_tracked_tickers.py`'s purpose but is never called (confirmed by grep — not even by `protect_slate.py`, which only imports `detect_run_type`/`save_slate` from that module) |
| **Should remain authoritative?** | Yes for all the live paths. The dead `persist_tracked_tickers()` function should be removed in a future phase (documented in `docs/REFACTORING_OPPORTUNITIES.md`, not removed here since it requires confirming zero hidden call sites beyond grep — deferred out of caution). |

## 12. Session bets (manual input)

| | |
|---|---|
| **Object** | Analyst-authored bet records from live sessions, outside the automated pipeline |
| **Canonical location** | `data/session_bets/<date>.json` |
| **Writers** | Hand-authored; ingested by `scripts/log_session_bets.py` |
| **Readers** | `scripts/log_session_bets.py` only |
| **Lifecycle** | Only 2 dated instances exist (2026-06-17, 2026-06-18), both from the project's first week; never used again across 20+ subsequent slate dates |
| **Should remain authoritative?** | **Likely an abandoned early-project mechanism**, superseded by the fully automated `write_pending_bets.py` path. Recommend confirming with the repo owner whether this manual path is still needed before any future consolidation — not touched this phase. |

---

## Summary table

| Object | Canonical file | Status |
|---|---|---|
| Authoritative slate | `data/slates/<date>/authoritative.json` | Authoritative, soft-synced to `data/slate.json` |
| Working slate | `data/slate.json` | Practically authoritative (everything reads it); ambiguous vs. above |
| Bet ledger | `bets.json` (root) | Authoritative |
| Stale duplicate ledger | `data/bets.json` | Confirmed stale, reconciliation deferred to Phase 3 |
| Pipeline stage status | `data/pipeline_status.json` | Authoritative, no consumer yet |
| Fetch gate result | `data/fetch_status.json` | Authoritative |
| Fetch/publish stamp | `data/meta.json` | Authoritative |
| Market registry | `data/kalshi_market_registry.json` | Authoritative |
| Vestigial parallel registry | `data/kalshi_market_index.json` + `data/kalshi_odds_history.json` | Incomplete, dead consumer, deprecation candidate |
| CLV tracked tickers | `data/clv_snapshots/<date>/tracked_tickers.json` | Authoritative |
| Market-wide snapshot archive | `data/kalshi_registry_snapshots/*` | Authoritative, actively growing |
| Session bets | `data/session_bets/<date>.json` | Likely abandoned, confirm before touching |
