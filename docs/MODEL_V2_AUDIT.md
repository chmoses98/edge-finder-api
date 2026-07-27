# MODEL_V2_AUDIT.md

Repository audit and Model V2 foundation plan.
Branch: `model-v2-foundation`. Prepared as Phase 0/1 of the Model V2 rebuild.

This document does not propose deleting or rewriting the production system.
It describes what exists, what actually broke, what is fixed on this branch,
and what should happen next — in phases, on this branch, without touching
`main`.

---

## 1. Current architecture (as it exists today)

The system is a single-pipeline, file-based MLB betting research and
execution tool, not yet layered. One GitHub Actions workflow
(`fetch-slate.yml`) performs data collection, model evaluation, portfolio
gating, execution logging, and publication in one linear job. A second
workflow (`clv_capture.yml` / `clv-update.yml` / `fetch-kalshi-clv.yml`)
settles CLV and bets on a separate schedule. A third
(`capture-snapshots-scheduled.yml`) archives Kalshi market snapshots
independently, every ~10–70 minutes, regardless of the main pipeline's
state — this is why the git history shows a steady stream of `kalshi
snapshot ...` commits even on days the main slate pipeline failed.

Conceptually, the actual current flow (before this branch's changes) is:

```
Vercel APIs (api/*.js)
   → fetch-slate.yml: fetch teamstats/pitchers/weather/bullpen/slate/odds/kalshi
   → post_fetch_gate.py (stale-date guard, quarantine)
   → fetch_savant_*.py, fetch_lineups.py, enrich_lineup_confirmed.py
   → build_kalshi_registry.py, fetch_opp_quality.py, merge_odds.py, enrich_data.py
   → build_market_ledger.py  (writes g['marketLedger'], the coverage source of truth)
   → regression_test.py, validate_slate_final.py (execution slip)
   → protect_slate.py        (sentinel check, writes data/slates/DATE/authoritative.json)
   → risk_gate.py            (TT safety + portfolio concentration)
   → write_pending_bets.py   (writes bets.json)
   → validate_bet_logging.py (hard gate: bets.json must match marketLedger)
   → write_tracked_tickers.py, capture_closing_lines.py
   → "Write meta and commit"  (writes data/meta.json, commits data/ + bets.json)
```

Critically, **every one of these steps was on the same hard-failure chain**.
A failure anywhere from `risk_gate.py` onward aborted the job before the
final commit step ran — see Section 4.

### Model logic

A Poisson run-projection engine (`RUN_THE_SLATE.md` Section S4, mirrored in
`scripts/build_market_ledger.py`) converts projected runs per team into
win/total probabilities, compares them to Kalshi no-vig prices
(`kalshiVF`), and applies a calibration factor from `config/rules.json` to
convert raw model-vs-market gap into a "calibrated edge." Pinnacle is used
as a sanity check, not a betting target. On top of this sit ~83 rules
(`RULES.md`), tiered T1 (hard gate)/T2 (soft gate, tier downgrade)/T3
(sizing scalar only), most of which are implemented as inline conditionals
across `build_market_ledger.py`, `risk_gate.py`, and
`validate_slate_final.py` rather than as a single rules engine.

### 11-market ledger

Every game is required to produce exactly 11 `marketLedger` rows (NRFI,
YRFI, F5 ML ×2, Team Total Over ×2, ML ×2, Game Total, RL ×2). This is
enforced by `scripts/regression_test.py` and is a genuinely good invariant:
it makes market coverage auditable and catches silent evaluation gaps.

---

## 2. Source-of-truth files (confirmed, not assumed)

| Concern | File | Status |
|---|---|---|
| Bet ledger | **`bets.json`** (root) | Confirmed authoritative — 509 entries, actively written by `write_pending_bets.py` / `log_session_bets.py`, most recent entry 2026-07-24 |
| Stale/orphaned ledger | `data/bets.json` | **92 entries, most recent 2026-06-18, not written by any current script.** This is a duplicate/abandoned ledger from before the June refactor. Left untouched — flagged for Phase 3 reconciliation, not deleted. |
| Market coverage | `g['marketLedger']` inside `data/slate.json` | Confirmed authoritative per `RUN_THE_SLATE.md`; `allEdges` is explicitly documented as *not* the source of truth |
| Authoritative pregame slate | `data/slates/<date>/authoritative.json` | Written by `protect_slate.py`; `data/slate.json` is a backwards-compatible copy |
| Fetch metadata | `data/meta.json` | Written only in the final commit step — **this is the file that went stale** (Section 4) |
| Fetch gate result | `data/fetch_status.json` | Written unconditionally (`if: always()`) by `post_fetch_gate.py`, much earlier in the pipeline than `meta.json` |
| Numeric thresholds | `config/rules.json` | Confirmed machine-readable source of truth for calibration factors, multipliers, edge thresholds, gates |
| Rule definitions | `RULES.md` (83 rules, T1/T2/T3) | Reference-only per `RUN_THE_SLATE.md`; not machine-read directly — logic is duplicated into scripts |
| Execution order | `RUN_THE_SLATE.md` | Declared single authoritative execution sequence |

**Ledger duplication finding:** `bets.json` and `data/bets.json` are two
different files with divergent, non-overlapping content. Nothing currently
reads `data/bets.json`. This is exactly the kind of silent duplicate the
task's ledger-reconciliation phase (Phase 3, not attempted in this session)
needs to formally resolve — it should not be deleted or merged without a
reconciliation report.

---

## 3. Known coupling / duplicated logic

The single biggest structural risk found in this audit: **the same rule
("never produce a real-money bet for a live/final game") is independently
re-implemented in at least four places**, with different levels of
correctness:

1. `lib/postponed_guard.check_game_status()` — the correct, tested, canonical implementation (two-signal: explicit status + first-pitch timestamp fallback).
2. `scripts/bet_eligibility.py` — a separate, well-designed three-status classifier (`bet_eligibility_status` / `clv_capture_status` / `review_integrity_status`) that calls into (1) but is applied once, early, inside `build_market_ledger.py`, and is **never read by any downstream consumer** (risk gate, bet writer, or validator). It is a fully-built, tested, unused abstraction.
3. `scripts/write_pending_bets.py` — re-calls `check_game_status()` itself, fresh, at write time. This one is correct and necessary (it catches games that started *between* ledger build and bet-write time), but it duplicates (1) rather than consuming (2).
4. `scripts/risk_gate.py` and `scripts/validate_bet_logging.py` — **did not check game status at all** before this branch's fix. They treated every `status=='Accepted'` + `HIGH`/`MEDIUM` marketLedger row as real-money-eligible, with no awareness that `write_pending_bets.py` would legitimately refuse to log some of them.

This is the direct root cause of Section 4. It is also a textbook instance
of the coupling problem this rebuild is supposed to fix: four
implementations of one rule, three of which happened to agree by luck, one
combination of which did not.

**Fix applied on this branch:** `risk_gate.py` and `validate_bet_logging.py`
now import and call the same `check_game_status()` used by
`write_pending_bets.py`, computed fresh at their own run time. This does
not add a new rule or abstraction — it removes two silent gaps in an
existing one. `scripts/bet_eligibility.py`'s classifier remains unused by
these three scripts; wiring it in as the single source of truth (retiring
the three ad hoc re-implementations in favor of one call) is recommended
Phase 2 work, not done here to keep this change minimal and reviewable.

Other coupling worth flagging for later phases (not touched this session):
- `risk_gate.py` writes directly into `data/meta.json` (merging in a
  `risk_gate` key). Harmless today because it merges rather than
  overwrites, but it means an "optional" stage has write access to the
  "authoritative" file — worth separating in Phase 2 schema work.
- Rule logic (`RULES.md`'s 83 rules) is partially duplicated as string
  literals (`"Rule 71: ..."`) across `build_market_ledger.py`, `risk_gate.py`,
  and `validate_slate_final.py`. No single place enumerates which of the 83
  rules are actually wired into code versus aspirational documentation.
  Auditing rule-by-rule (which are load-bearing, which are dead, which
  have measurable evidence per `config/rules.json`) is recommended as
  Phase 2/3 work — RULES.md is too large (83 rules) to audit rule-by-rule
  in this session without shortcutting the rest of the assignment.

---

## 4. Root cause of the known high-priority problem (fixed this session)

**Symptom reported:** `data/fetch_status.json` showed a successful fetch for
2026-07-26 (`status: "OK"`, `requestedDate == actualDate == "2026-07-26"`),
but `data/meta.json` still reflected 2026-07-24.

**Confirmed via GitHub Actions run logs** (not guessed): runs `30175591673`
and `30175861494` (2026-07-25) and `30211684499` (2026-07-26) all
**failed**, at the same step, for the same reason:

```
[risk_gate] Portfolio composition: Total real-money stake: 28.0u across 7 bets
[write_pending_bets] PREGAME GATE BLOCKED: CLE@TB status='In Progress'
                      reason=LIVE_GAME_BLOCKED — no real-money bets logged
[write_pending_bets] Written 6 new pending bets to bets.json
[validate_bet_logging] Expected real-money bets in ledger:  7
[validate_bet_logging] Logged in bets.json for 2026-07-26:  6
[validate_bet_logging] GATE FAIL — 1 bets NOT in bets.json:
    game=CLE@TB market=TT_Away_Over ... 
##[error]Process completed with exit code 1.
```

`write_pending_bets.py` correctly refused to log a bet for CLE@TB (a game
that was live) — this is the required, correct behavior per "never
recommend a live or completed game." But `validate_bet_logging.py` computed
its "expected" count from `marketLedger` alone, with no game-status
awareness, saw 7 expected vs. 6 logged, and treated the *correct* exclusion
as a hard failure. `risk_gate.py` had the identical gap in its own
portfolio-composition tally.

Because `validate_bet_logging.py` runs *before* the "Write meta and commit"
step, and that step had no `continue-on-error`/`if: always()`, the job
died before ever reaching it. `data/fetch_status.json` had already been
committed unconditionally minutes earlier by a separate, `if: always()`
step — so the run looked healthy in `fetch_status.json` while
`data/meta.json`, `data/slate.json`, and the day's `bets.json` entries were
never published. This happened on three of the last five run attempts
(2026-07-25 twice, 2026-07-26 once); the last successful full publish was
2026-07-24 17:55 ET (commit `c0e88bd`).

**Fixed this session (two independent, compounding fixes):**

1. **Logic fix** — `scripts/risk_gate.py` and `scripts/validate_bet_logging.py`
   now exclude live/final/postponed games from their real-money tallies,
   using the same `lib/postponed_guard.check_game_status()` call
   `write_pending_bets.py` already used. A legitimately-excluded live game
   no longer causes a false `GATE FAIL`.
2. **Architecture fix** — `.github/workflows/fetch-slate.yml` now commits
   `data/meta.json` and the authoritative slate **immediately after**
   `protect_slate.py`, *before* the risk gate / bet-logging / CLV chain runs.
   Those downstream steps are marked `continue-on-error: true`, and a new
   final step writes `data/pipeline_status.json` (stage-status artifact)
   and commits whatever the execution/logging chain produced, unconditionally.

   With both fixes, the July 25/26 scenario (a live game producing an
   "expected but unlogged" bet) would not have failed the job at all
   (logic fix). Even if some *other*, unrelated bug fails
   `risk_gate.py`/`write_pending_bets.py`/`validate_bet_logging.py` in the
   future, `data/meta.json` and the authoritative slate now publish anyway
   (architecture fix) — exactly the "authoritative slate must publish even
   when downstream execution/logging fails" requirement.

**Regression tests added** (`tests/test_pipeline_publication_reliability.py`,
`tests/test_fetch_slate_workflow_structure.py`) reproduce the exact July
25/26 scenario against the fixed code, confirm a genuinely-missing pregame
bet still fails the gate (no over-correction), and structurally assert the
workflow YAML can't silently regress the publish-before-execution ordering.

**Note on this fix's effect on production:** these are code changes on
`model-v2-foundation`, not `main`. `fetch-slate.yml` is dispatched against
`ref: main`, so **none of this affects the live production pipeline until a
human merges this branch.** Production continues running the current
(unfixed) workflow until then — see "Remaining risks" in the final report.

---

## 5. Stale-data / staleness risks beyond this incident

- `data/meta.json` has no independent staleness check today — nothing
  compares `meta.json`'s date against "today" and flags it. The new
  `data/pipeline_status.json` (`slateDate`, `completedAt`, per-stage status)
  gives a machine-readable signal for this, but an explicit stale-slate
  detector (e.g., "meta.json date is >18h old and it's a game day") is
  Phase 1 follow-up work, not built this session.
- `scripts/protect_slate.py` already has strong protection against reruns
  overwriting `authoritative.json` and against sentinel-price contamination
  — this part of the pipeline is well designed and was not touched.

---

## 6. Test coverage

661 of 662 collected tests pass on this branch (baseline before this
session's changes: 649/650; +12 new tests added, no regressions). The one
persistent failure (`tests/test_clv_date_and_auth.py::TestValidPregameSnapshot::test_valid_snapshot_writes_file`)
pre-dates this branch and is unrelated to the pipeline-publication fix: it
hardcodes a 2026-06-19 game date and computes "now" from the real system
clock, so as real time advances past that fixture's game date the test's
own timestamp-fallback logic (correctly) marks the fixture snapshot as
`INVALID_POST_START`. This is a test-hygiene issue (time-dependent fixture
using live wall-clock time instead of an injected "now") — flagged for
Phase 2 cleanup, not fixed here to keep this session's diff scoped to the
publication-reliability problem.

Also observed: running the full suite leaves a stray `data/clv_report.json`
in the working tree (some test path in the CLV test suite writes to the
real `data/` directory instead of a temp directory). Not committed;
flagged as a test-isolation issue for Phase 2.

Coverage is otherwise strong for the specific failure modes this project
cares most about: live-game blocking, postponement voiding, CLV capture,
bet-identity backfill, and stale-date guards all have dedicated test files.
There was, before this session, no test at all for `risk_gate.py`'s
portfolio math or for `validate_bet_logging.py`'s expected/logged
comparison — the exact gap that shipped the July 25/26 incident. That gap
is now closed.

---

## 7. Recommended deprecations (flagged, not acted on)

- `data/bets.json` — stale duplicate ledger, superseded by root `bets.json`.
  Recommend Phase 3 reconciliation report before any action.
- `archive/` — already self-documenting (`archive/README.md`); no changes needed.
- Nothing else is recommended for deprecation without more evidence. In
  particular, **Rule 71, Rule 81, and the confidence-tier system were not
  touched or evaluated in depth this session** — per the working
  philosophy, they should only be modified after their historical
  performance is measured against `config/rules.json`'s own tracked `n`/`wr`/`avg_clv`
  fields (which already exist for exactly this purpose), not removed as a
  matter of course.

---

## 8. Proposed V2 architecture (target state, phased)

```
Raw Data Ingestion (Workflow A)
        ↓
Validation and Normalization
        ↓
Feature Engineering
        ↓
Run Environment / Team Strength Projections
        ↓
Market-Specific Probability Models   (currently one shared Poisson engine;
        ↓                            market-specific separation is Phase 4)
Probability Calibration               (currently a single flat factor per tier;
        ↓                            config/rules.json already tracks n/wr per
        ↓                            market — a real calibration interface is Phase 4)
Market Comparison and Edge Calculation
        ↓
Portfolio Construction                (risk_gate.py today; Phase 4 interface work)
        ↓
Authoritative Recommendations         (data/slates/DATE/authoritative.json — exists,
        ↓                            fixed to publish reliably this session)
Execution / Logging / CLV Tracking    (now decoupled from publication — this session)
        ↓
Settlement and Reporting              (clv_update.py, generate_performance_report.py —
                                       exist; Phase 5 is building the report-card layer)
```

This session's changes land squarely in the middle of this diagram: they
make the boundary between "Authoritative Recommendations" and "Execution /
Logging / CLV Tracking" real and enforced, instead of implicit and coupled
by accident.

---

## 9. Phased implementation plan (status)

| Phase | Scope | Status |
|---|---|---|
| 0 | Repository orientation + this audit | **Done this session** |
| 1 | Pipeline reliability (this incident) | **Done this session** — logic fix, workflow decoupling, stage-status artifact, regression tests |
| 2 | Schema + source-of-truth cleanup (versioned schemas, retire duplicate game-status checks in favor of `bet_eligibility.py`, fix test-isolation issues found in Section 6) | Not started |
| 3 | Historical ledger reconciliation (`bets.json` vs `data/bets.json`, duplicate-detection keys, reconciliation report) | Not started |
| 4 | Model V2 interfaces (market-specific models, calibration interface, portfolio decision object) | Not started |
| 5 | Reporting foundation (daily/cumulative report card) | Not started |

Per the guiding instructions for this work, phases 2–5 are intentionally
**not** attempted in this session — the assignment is explicit that pipeline
reliability (the reported incident) is the priority, and that the rebuild
should proceed in small, reviewable increments rather than one large pass.
