# SOURCE_OF_TRUTH_MAP.md

Phase 2, Part 2 — every authoritative object in the repository.

**No migrations are performed in this document or this phase.** This is a
map of what exists today, who owns it, who writes it, who reads it, what
duplicates exist, and a recommendation on whether it should remain
authoritative — recommendations only, not actions.

---

## 0. Final reconciliation (Phase 10) — `authoritative.json` vs `data/slate.json` vs `execution.json`/`validation.json`/`protection.json`

**This section is the final answer this document deferred across
Phases 4, 7, 8, and 9.** No file was merged, deleted, or renamed to
produce it, no script was changed to implement it, and no compatibility
adapter was needed — every fact below was already true in the code
graph; what was missing was a single, explicit statement of it in one
place. Production behavior is unchanged by writing this section.

**The apparent conflict dissolves once "source of truth" is scoped per
concept, not treated as one single crown to award:**

| Concept | Single source of truth | Why the others are NOT competing for this role |
|---|---|---|
| **The current, in-progress pipeline run's working state** (whatever the slate looks like right now, mid-run) | `data/slate.json` | This is not a claim that `data/slate.json` is more "correct" than `authoritative.json` — it is the only file every downstream script (18 of them) actually reads during a live run. Nothing else could serve this role without every one of those 18 scripts being rewritten first, which is explicitly out of scope (§2). |
| **The immutable pregame decision record** (what the model recommended before first pitch, preserved even after later steps mutate `data/slate.json` further) | `data/slates/<date>/authoritative.json` | `protect_slate.py` writes this once per date and only merges later reruns under strict freeze/reject rules (`lib/slate_manager.py`'s `merge_rerun_into_authoritative()`) — it is deliberately insulated from everything that runs AFTER it in the workflow (`risk_gate.py`, `write_pending_bets.py`). `data/slate.json` is NOT a substitute for this role: after `risk_gate.py` runs, `data/slate.json` is overlaid with post-protection execution decisions `authoritative.json` never receives — the two files are snapshots of two different, sequential pipeline moments (pre-risk-gate vs. post-risk-gate), not two competing copies of the same moment. This is why hardening the "soft sync" (§1's Phase 4-era recommendation) was correctly never acted on: reconciling them would require choosing one moment to be canonical and discarding the other's information. |
| **The execution/risk-gate decision for each candidate bet** (real-money vs. PAPER, rejection reason, approved stake/price) | `data/slate.json` (post-`risk_gate.py`, full detail) for pipeline consumption; `data/pipeline/<date>/execution.json` for an immutable, narrow, point-in-time record of the same decision | Neither `authoritative.json` (written before `risk_gate.py` runs) nor `bets.json` (only real-money Accepted bets that clear `write_pending_bets.py`'s own separate live-game gate) captures this — `execution.json` is the only artifact that does, by construction (`build_execution_artifact_payload()` reshapes the exact decision `apply_tt_safety()`/`apply_portfolio_rules()` already made). |
| **The validation gate result** (pass/fail, errors, warnings) | `final_validation_status` (the GitHub Actions step output `validate_slate_final.py` sets, which downstream workflow steps actually gate on) for control flow; `data/pipeline/<date>/validation.json` for an immutable, narrow record of the same result | Deliberately NOT `data/slate.json` — validation errors/warnings were never written back into the slate object itself. |
| **The protection/quarantine outcome** (run type, sentinel count, whether this run was quarantined) | `data/pipeline/<date>/protection.json` for an immutable, narrow record; the mere EXISTENCE of `data/slates/<date>/authoritative.json` for whether a clean pregame run has ever completed for that date | `authoritative.json`'s CONTENT is the pregame decision record (above); its mere presence/absence is a separate, useful signal that happens to be readable without opening the file. |
| **The actual placed/pending bet** | `bets.json` (repo root) | Never ambiguous — already established Phase 0/2, reconfirmed every phase since, untouched by this reconciliation. |

**What this resolves, concretely:**
- `authoritative.json`'s own docstring claim ("post-slate review MUST
  use `authoritative.json` as source of truth") is **not wrong** — it
  correctly describes `authoritative.json`'s role for the ONE concept
  it actually owns (the immutable pregame record). It was previously
  read as a claim about the pipeline's *general* source of truth,
  which is where the perceived conflict with `data/slate.json` came
  from. No docstring change was needed once the concept was scoped
  correctly — see `docs/IMMUTABLE_PIPELINE.md` §13/§14 for where this
  was first noticed.
- `execution.json`/`validation.json`/`protection.json` remaining
  "non-authoritative" (§2a/§2b/§2c) is not a gap to fix — it is the
  correct, permanent state for point-in-time immutable records that
  intentionally never override the live `data/slate.json` they were
  derived from.
- **No compatibility adapter was required.** Every field in every one
  of these files already has exactly one owner once scoped by concept
  (table above) — there was no field genuinely claimed by two
  producers that needed reconciling logic, only a documentation gap
  describing which file to consult for which question.
- **Duplication check:** the three `data/pipeline/<date>/*.json`
  artifacts are deliberately narrow subsets, not full duplicates of
  `data/slate.json`/`authoritative.json` (see each artifact's own
  "Duplicate copies" row below) — this reconciliation did not surface
  any additional unnecessary duplication beyond what Phase 7 already
  found and documented (`data/kalshi_market_index.json` +
  `data/kalshi_odds_history.json`, §7; `data/bets.json`, §3).

This closes the ambiguity `docs/IMMUTABLE_PIPELINE.md` §15 item 2 refers
to as resolved "at the field-ownership level" while the four files
themselves remain permanently parallel by design.

---

## 1. The authoritative slate

| | |
|---|---|
| **Object** | The daily model output: schedule, projections, edges, `marketLedger[]` per game |
| **Canonical location** | `data/slates/<date>/authoritative.json` |
| **Owner** | `lib/slate_manager.py` (`save_slate`, `detect_run_type`) |
| **Writers** | `scripts/protect_slate.py` (calls into `slate_manager`) |
| **Readers** | Intended reader: post-slate review / analyst tooling, per `protect_slate.py`'s own docstring ("Post-slate review MUST use data/slates/DATE/authoritative.json as source of truth"). **(Phase 9)** Confirmed by direct repository-wide search: `authoritative.json` has exactly **one producer and one content-level consumer across the entire repository** — `scripts/protect_slate.py` itself (via `lib/slate_manager.py`'s `load_authoritative()`/`save_slate()` merge path on subsequent runs). No other script reads its content; every other script reads `data/slate.json` instead. The docstring's claim that "post-slate review MUST use `authoritative.json`" describes an intended external process, not anything enforced or exercised by code in this repository. See `docs/IMMUTABLE_PIPELINE.md` §13. |
| **Duplicate copies** | `data/slate.json` (root) — kept in sync by convention: `protect_slate.py` copies `authoritative.json` → `slate.json` after every non-contaminated run. This is a **soft convention, not an enforced contract** — nothing prevents a future script from writing `data/slate.json` directly without going through `protect_slate.py` and silently diverging from the "real" authoritative copy. |
| **Lifecycle** | Written once per slate date on `OFFICIAL_PREGAME` (first successful run), then either left untouched (`LINEUP_RECHECK`/`IN_PLAY_RECHECK` write to `recheck_<ts>.json` instead) or never created at all (`REJECTED_CONTAMINATED` runs write only to `rejected_contaminated_<ts>.json` — confirmed real for at least 3 historical dates: 2026-06-15, 2026-06-18, 2026-07-22, which have no `authoritative.json`). |
| **Should remain authoritative?** | **Yes, for the immutable pregame decision record — final answer, §0.** The Phase 4-era recommendation to harden the soft-sync convention (making every downstream script read `authoritative.json` directly) was reconsidered and NOT pursued: §0 establishes that `data/slate.json` and `authoritative.json` deliberately capture two different sequential pipeline moments (pre- vs. post-`risk_gate.py`), so "hardening the sync" would mean discarding one moment's information, not fixing a bug. No code change made. |

## 2. `data/slate.json` (the practically-consumed slate)

| | |
|---|---|
| **Object** | Working copy of the slate that ~18 scripts actually read/write during the pipeline run |
| **Owner** | No single owner — mutated in place by ten scripts (see `docs/MODEL_V2_ARCHITECTURE.md` §7) |
| **Writers** | `fetch_savant_pitchers.py`, `fetch_lineups.py`, `enrich_lineup_confirmed.py`, `post_fetch_gate.py`, `merge_odds.py`, `enrich_data.py`, `build_market_ledger.py`, `validate_slate_final.py`, `protect_slate.py` (overwrites wholesale from authoritative), `risk_gate.py` — still ten scripts, still no single owner; ownership has not changed across Phases 3-6, only *how* five of them (`enrich_lineup_confirmed.py`, `merge_odds.py`, `fetch_savant_pitchers.py`, `fetch_lineups.py`, `post_fetch_gate.py`) compute what they write, and (Phase 6) how `build_market_ledger.py` sources the projection values it writes into `marketLedger` (see `docs/IMMUTABLE_PIPELINE.md` §4) |
| **Readers** | All of the above plus `write_pending_bets.py`, `validate_bet_logging.py`, `write_tracked_tickers.py`, `regression_test.py`, `validate_current_slate_date.py`, `validate_slate_pre.py` |
| **Duplicate copies** | Is itself the "working" duplicate of `authoritative.json` above |
| **Lifecycle** | Rewritten on every pipeline stage within a single run; not versioned; not date-partitioned (unlike `authoritative.json`, which is). **(Phase 5)** `fetch_lineups.py`'s and `fetch_savant_pitchers.py`'s writes are now atomic (temp file + `fsync` + `os.replace()`, matching `lib/pipeline_artifacts.py`'s mechanism applied inline) — a serialization failure can no longer leave a truncated file at this path for those two writers specifically. **(Phase 6)** `post_fetch_gate.py`'s quarantine-marker write is now atomic too, and all three writers' atomic-write code was consolidated onto one new shared helper, `lib/atomic_json.write_json_atomic()` (replacing what were two independently-inlined, byte-for-byte-identical copies from Phase 5). **(Phase 7)** `risk_gate.py`'s `data/slate.json` write is now also atomic via the same shared helper (`indent=2` preserved). **(Phase 8)** `validate_slate_final.py`'s `data/slate.json` execution-slip patch is now also atomic via the same shared helper (`indent=2` preserved); its companion `data/execution_slip_<date>.json` write (a separate file, not `data/slate.json` itself) was migrated the same way. **Five** of the ten writers still use a plain `open(path, 'w')` + `json.dump()`, sharing the same theoretical (unfixed) truncation-on-failure gap `lib/slate_manager.py`'s non-atomic write already had (§9 of `docs/IMMUTABLE_PIPELINE.md`); `lib/slate_manager.py` itself was deliberately not migrated onto the new shared helper — it writes `data/authoritative.json`, explicitly out of scope through Phase 8. |
| **Should remain authoritative?** | **Yes, for the current in-progress pipeline run's working state — final answer, §0.** This is the file that is *practically* authoritative today (everything reads it); `protect_slate.py`'s own docs describing `authoritative.json` as the source of truth are correct for a DIFFERENT concept (the immutable pregame record), not a competing claim about this file's role. Resolved, not merely "practically true" — see §0 for the full reconciliation. **(Phase 7)** Confirmed `risk_gate.py` never reads or writes `authoritative.json` at all — see `docs/IMMUTABLE_PIPELINE.md` §11/§13 and `tests/test_risk_gate_authoritative_boundary.py` for the exact workflow-step-ordering evidence that the two files capture different (never-reconciled, and correctly so) pipeline moments. `validate_slate_final.py` was already confirmed (Phase 8) to never reference `authoritative.json` either — see `docs/IMMUTABLE_PIPELINE.md` §12. |

## 2a. `data/pipeline/<date>/execution.json` (new, Phase 7)

| | |
|---|---|
| **Object** | Narrow, canonical per-candidate execution decision: game/market identity, final decision (real-money vs PAPER), rejection reason, approved stake/price, evaluation order, source recommendation ticker — plus the top-level GO/PAPER_ONLY decision and reason |
| **Owner / Writer** | `scripts/risk_gate.py`'s `build_execution_artifact_payload()` + `lib/pipeline_artifacts.write_stage_artifact()`, called after `apply_tt_safety()`/`apply_portfolio_rules()`/the PAPER_ONLY third pass have all already run — never a second computation of the decision |
| **Readers** | None yet — purely additive, best-effort (a publication failure only logs a warning and never affects `data/slate.json`/`data/meta.json` or the exit code) |
| **Duplicate copies** | None — it is the first canonical (non-`data/slate.json`-shaped) record of `risk_gate.py`'s decisions |
| **Lifecycle** | Fully overwritten (not merged/appended) on every run, same as `data/meta.json`'s `risk_gate` key |
| **Should remain authoritative?** | No — explicitly non-authoritative by design; it is the immutable record of the execution decision, not a live-state file. Final relationship to `authoritative.json`/`data/slate.json` resolved in §0 (Phase 10): no reconciliation needed, each already owns a distinct concept. |

## 2b. `data/pipeline/<date>/validation.json` (new, Phase 8)

| | |
|---|---|
| **Object** | Narrow, canonical validation result: overall pass/fail status, game count, ordered error list, ordered warning list — no per-game-market decision detail, no settlement/PnL field, no full-slate payload |
| **Owner / Writer** | `scripts/validate_slate_final.py`'s `build_validation_artifact_payload()` + `lib/pipeline_artifacts.write_stage_artifact()`, called with the exact `(errors, warnings)` `validate_final()` already returned to `main()` — never a second validation computation |
| **Readers** | None — purely additive, best-effort (a publication failure only logs a warning and never affects `data/slate.json`, `final_validation_status`, or the exit code) |
| **Duplicate copies** | None — narrower than `execution.json` in that it doesn't even carry per-candidate detail, since this script doesn't own per-game-market decisions (`recommendations.json`/`execution.json` do) |
| **Lifecycle** | Fully overwritten (not merged/appended) on every run; written on BOTH the pass and fail paths (unlike the legacy `execution_slip_<date>.*` files, which are only written on the pass path) |
| **Should remain authoritative?** | No — explicitly non-authoritative by design; `final_validation_status` (the GitHub Actions output) remains the sole thing downstream workflow steps gate on. See §0 (Phase 10) for the final scoped-by-concept resolution. |

## 2c. `data/pipeline/<date>/protection.json` (new, Phase 9)

| | |
|---|---|
| **Object** | Narrow, canonical protection/sentinel-gate result: run type, quarantine status, sentinel count, saved paths, authoritative-write/-update flags, a narrowed `runReportSummary` (accepted/rejected/frozen counts + quarantined flag only — no per-game breakdown), legacy-sync flag, authoritative-exists flag |
| **Owner / Writer** | `scripts/protect_slate.py`'s `build_protection_artifact_payload()` + `lib/pipeline_artifacts.write_stage_artifact()`, called with values `main()` already computed (sentinel scan result, `save_slate()`'s return value, the legacy-sync decision) — never a second protection computation |
| **Readers** | None — purely additive, best-effort (a publication failure only logs a warning and never affects `data/slate.json`, `authoritative.json`, or the return value) |
| **Duplicate copies** | None — deliberately narrower than `lib.slate_manager.save_slate()`'s own `runReport`, which carries full per-game accepted/rejected/frozen detail; that detail remains owned by `lib/slate_manager.py` and is not duplicated here, matching the precedent set by `execution.json` (2a) and `validation.json` (2b) of not re-deriving detail owned by another stage |
| **Lifecycle** | Fully overwritten (not merged/appended) on every run, written on both the clean and sentinel-quarantined paths |
| **Should remain authoritative?** | No — explicitly non-authoritative by design, same rationale as `execution.json` and `validation.json`. The `authoritative.json` vs. `data/slate.json` reconciliation this row deferred past Phase 9 is now resolved in §0 (Phase 10) — scoped by concept, not merged. |

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
| Authoritative slate | `data/slates/<date>/authoritative.json` | Authoritative for the immutable pregame decision record (§0, final) |
| Working slate | `data/slate.json` | Authoritative for the current in-progress pipeline run's working state (§0, final) — not ambiguous vs. above once scoped by concept |
| Execution decision | `data/pipeline/<date>/execution.json` | Non-authoritative by design; immutable narrow record of `risk_gate.py`'s decision (§0) |
| Validation result | `data/pipeline/<date>/validation.json` | Non-authoritative by design; `final_validation_status` is what workflow steps gate on (§0) |
| Protection/quarantine result | `data/pipeline/<date>/protection.json` | Non-authoritative by design; immutable narrow record of `protect_slate.py`'s decision (§0) |
| Bet ledger | `bets.json` (root) | Authoritative |
| Stale duplicate ledger | `data/bets.json` | Confirmed stale, reconciliation deferred to Phase 3 |
| Pipeline stage status | `data/pipeline_status.json` | Authoritative, no consumer yet |
| Fetch gate result | `data/fetch_status.json` | Authoritative |
| Fetch/publish stamp | `data/meta.json` | Authoritative |
| Market registry | `data/kalshi_market_registry.json` | Authoritative |
| Vestigial parallel registry | `data/kalshi_market_index.json` + `data/kalshi_odds_history.json` | Incomplete, dead consumer, deprecation candidate |
| CLV tracked tickers | `data/clv_snapshots/<date>/tracked_tickers.json` | Authoritative |
| Market-wide snapshot archive | `data/kalshi_registry_snapshots/*` | Authoritative, actively growing (now with a 21-day retention rule for timestamped snapshots, dated snapshots kept forever -- see `lib/snapshot_retention.py`) |
| Session bets | `data/session_bets/<date>.json` | Likely abandoned, confirm before touching |

---

## Addendum (Production Reliability and Settlement Recovery milestone) — settlement and ledger mutation, current source of truth

Everything above is the Phase 2 Part 2 snapshot and is left as written. This
addendum documents what changed for §3 (the bet ledger) and §10
(settlement data) specifically:

- **Who may mutate `bets.json`, `BET_LOG.md`, `data/slate.json`, and
  `data/slates/<date>/authoritative.json`:** exactly three workflows --
  `fetch-slate.yml`, `clv-update.yml`, `lineup-recheck.yml` -- and nothing
  else. All three now share the `edge-finder-ledger-writer` concurrency
  group (`cancel-in-progress: false`), so GitHub Actions serializes them
  instead of allowing the concurrent-write race §3's "Owner" row above
  already flagged as unresolved.
- **How `bets.json` is written:** every writer (`clv_update.py`,
  `scripts/write_pending_bets.py`, `scripts/clv_from_snapshot.py`,
  `scripts/fetch_kalshi_clv_v2.py`, `scripts/backfill_market_identity.py`,
  `scripts/log_manual_bet.py`, `scripts/log_session_bets.py`,
  `scripts/capture_closing_lines.py` settle mode) now goes through
  `lib/atomic_json.write_json_atomic()` (temp file in the same directory +
  `os.replace`), not a plain `open()`+`json.dump()`. A crash or killed job
  mid-write can therefore never leave a truncated or partially-written
  `bets.json` on disk.
- **A real, previously-undocumented incident** (see
  `docs/POSTMORTEM_PRODUCTION_RELIABILITY_2026.md`): `clv-update.yml`'s
  "Commit all updates" step never `git add`ed `data/clv_report.json`, so
  once that file existed as a tracked file, `git pull --rebase` failed
  outright on any run where it had also changed, silently discarding that
  day's settlement/CLV work (confirmed on 2026-07-31 and 2026-08-01, and
  intermittently since at least 2026-06-16). Fixed this milestone.
- **§3's "Duplicate copies" row** (`data/bets.json`, stale, 92 entries,
  last written 2026-06-18) is unchanged -- still not reconciled, still not
  deleted, per the original "do not delete historical data" guidance.
