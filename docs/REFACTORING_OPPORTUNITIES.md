# REFACTORING_OPPORTUNITIES.md

Phase 2, Part 5 — highest-value architectural improvements, ranked by
impact, implementation risk, and expected future benefit. **Documented
only, per the phase brief — none of these are implemented in this
phase**, with one narrow exception already covered in
`docs/DUPLICATE_LOGIC_INVENTORY.md` §8 (removal of a fully dead file).

Ranking scale: Impact / Risk / Benefit each rated Low / Medium / High.

---

## 1. Materialize `data/slate.json`'s implicit schema

**Impact: High. Risk: Medium. Benefit: High.**

`data/slate.json` is mutated in place by ten scripts across a
single pipeline run (`docs/MODEL_V2_ARCHITECTURE.md` §7) with no
schema validation at any handoff point beyond ad hoc field-presence
checks scattered across each script. This is the root architectural
cause of the class of bug fixed in Phase 1 (a script downstream silently
assuming an upstream script's output shape without verifying it).

**Recommended approach:** introduce the `Game`/`Projection`/
`MarketProbability`/`Recommendation` schemas designed in
`docs/CANONICAL_SCHEMAS.md` as explicit, validated contracts between
pipeline stages — not necessarily separate files, but at minimum a
schema-validation step after each mutation (or, more ambitiously, refactor
each mutating script into "read `Game` → pure transform → validated
write" rather than "read raw dict → mutate in place → hope the shape is
still right"). This is the single highest-value structural change
available, and also the riskiest, since it touches the most
frequently-executed code path in the repository. Requires Phase 4+ and a
dedicated regression-test buildout before any script is touched.

---

## 2. Resolve the `data/slate.json` vs. `authoritative.json` ambiguity

**Impact: Medium. Risk: Low. Benefit: Medium.**

Two objects both claim to be "the" authoritative slate
(`docs/SOURCE_OF_TRUTH_MAP.md` §§1-2): `data/slates/<date>/authoritative.json`
(per `protect_slate.py`'s own docstring) and `data/slate.json` (what every
script actually reads). They're kept in sync by convention, not by
enforcement.

**Recommended approach:** either (a) make every downstream reader consume
`authoritative.json` directly, retiring `data/slate.json` as a
compatibility shim only, or (b) formally declare `data/slate.json` the
real source of truth and demote `authoritative.json` to an audit
snapshot. Low risk because it's a read-path change, not a logic change —
but requires touching ~10 scripts' file paths, so it's a "small migration,"
not a one-line fix.

---

## 3. Consolidate sentinel-price detection

**Impact: Medium. Risk: Medium. Benefit: High.**

Five independent implementations exist (`docs/DUPLICATE_LOGIC_INVENTORY.md`
§2), two of which are live in production with independently-maintained
constant sets (`capture_clv_pregame.py`, `api/slate.js`). A future
sentinel value that only gets added to the canonical
`lib/sentinel_validator.py` would silently fail to be caught by the other
two.

**Recommended approach:** point `capture_clv_pregame.py` at the canonical
Python module (low risk, same language, straightforward import). The JS
side (`api/slate.js`) requires either a shared constants file readable
from both ecosystems or accepting the duplication as a cross-language
reality and instead adding a test that asserts the two constant sets stay
in sync. Medium risk because this touches CLV-integrity code paths
directly handling real-money-adjacent logic — needs its own dedicated
regression pass, not a rider on an unrelated change.

---

## 4. Retire or fully compare the shadow implementations

**Impact: Low individually, Medium in aggregate. Risk: Low. Benefit: Medium.**

Three "second implementations that are dead in production but have live
test coverage" exist: `scripts/stale_date_guard.py` (vs.
`validate_current_slate_date.py`), `scripts/data_quality_gate.py` (vs.
`scripts/bet_eligibility.py`), and `lib/clv_validator.py` (vs.
`clv_from_snapshot.py`/`fetch_kalshi_clv_v2.py`).

**Recommended approach:** for each pair, do a side-by-side feature diff —
does the dead implementation handle any case the live one doesn't? If
not, delete the dead one and port its tests to exercise the live
implementation instead. If it does handle something extra, decide
explicitly whether that case matters and port the *logic*, not just the
tests. Low risk per-file (each is individually unreachable from
production), but three separate careful passes, not one refactor —
sequence them across Phase 3 rather than batching.

---

## 5. Deprecate the vestigial Kalshi market-index pipeline

**Impact: Low. Risk: Low. Benefit: Medium (mostly clarity/maintenance cost).**

`scripts/fetch_kalshi_markets.py` → `data/kalshi_market_index.json` +
`data/kalshi_odds_history.json` → `scripts/build_final_index.py` (removed
as dead code in the Production Reliability and Settlement Recovery
milestone -- confirmed zero production/test references before deletion)
was a parallel, incomplete (ML-only) attempt at what
`scripts/build_kalshi_registry.py` → `data/kalshi_market_registry.json`
already does correctly and completely. It runs every day
(`continue-on-error: true` in `fetch-slate.yml`), silently produces
incomplete data under a name that suggests completeness, and its only
designed consumer is unreachable dead code.

**Recommended approach:** confirm nothing external (e.g. a manual
analyst workflow, a dashboard) depends on `kalshi_market_index.json`
before removing the `fetch-slate.yml` step and the two source scripts.
Low risk technically (the workflow step is already `continue-on-error`
and non-blocking), but "confirm nothing external depends on it" is a
process step, not a code step — flagged for the repo owner to confirm
before Phase 3 touches it.

---

## 6. Add `modelVersion` / `calibrationVersion` / `pipelineRunId` to every recommendation and bet record

**Impact: High (for future evaluation work). Risk: Low. Benefit: High.**

None of `Recommendation`, `ExecutedBet`, or `Settlement` currently carry
any version/run identifier (`docs/CANONICAL_SCHEMAS.md`, "Cross-cutting
gaps" §1). This blocks essentially all of the Model V2 objective's
evaluation goals (chronological out-of-sample comparison, "performance by
model version," etc.) — without a version tag, you cannot know which
model produced a historical recommendation.

**Recommended approach:** additive-only change — add the fields with a
default/backfill value for historical records (e.g. `"unknown"` or the
commit SHA at time of backfill) and start populating them going forward
from `build_market_ledger.py`/`write_pending_bets.py`. Low risk because
it's purely additive (no existing field changes meaning), but needs a
deliberate versioning scheme decided first (semantic version? commit SHA?
date-based?) — a design decision for Phase 4, not implemented here.

---

## 7. Reconcile `data/bets.json` (stale duplicate ledger)

**Impact: Low (already known, already dormant). Risk: Medium (any ledger
touch is inherently higher-stakes). Benefit: Low (mostly hygiene).**

Confirmed again this phase (`rule71_report.json`'s `total_bets: 509`
matches root `bets.json` exactly, not the 92-entry `data/bets.json`).
Already recommended for Phase 3 in the Phase 0 audit; re-confirmed, not
re-litigated here.

**Recommended approach:** unchanged from Phase 0's recommendation — a
non-destructive reconciliation report first, no silent discard, before
any decision on archiving `data/bets.json`.

---

## 8. Give `data/pipeline_status.json` a consumer

**Impact: Medium. Risk: Low. Benefit: Medium.**

The Phase 1 hardening pass introduced this artifact but nothing reads it
yet — there's no alert or dashboard that surfaces a `"partial"` or
`"failed"` run to a human. Low risk (purely additive, read-only consumer),
clear benefit (this was the whole point of building the artifact).

**Recommended approach:** a small script or workflow step that checks the
latest `pipeline_status.json` and raises a visible signal (GitHub issue,
notification, or simply a workflow-summary annotation) on anything other
than `"success"`. Straightforward Phase 3 candidate.

---

## Ranked summary

| # | Refactor | Impact | Risk | Benefit | Suggested phase |
|---|---|---|---|---|---|
| 1 | Materialize `slate.json`'s schema | High | Medium | High | Phase 4 |
| 6 | Add version/run-ID fields | High | Low | High | Phase 4 |
| 3 | Consolidate sentinel-price detection | Medium | Medium | High | Phase 3 |
| 2 | Resolve slate.json vs. authoritative.json | Medium | Low | Medium | Phase 3 |
| 8 | Consumer for pipeline_status.json | Medium | Low | Medium | Phase 3 |
| 4 | Retire shadow implementations (3x) | Low–Medium | Low | Medium | Phase 3 |
| 5 | Deprecate vestigial Kalshi index pipeline | Low | Low | Medium | Phase 3 (pending owner confirmation) |
| 7 | Reconcile `data/bets.json` | Low | Medium | Low | Phase 3 |

---

## Refactors completed this phase

Exactly one, per `docs/DUPLICATE_LOGIC_INVENTORY.md` §8: removal of
`lib/slate_protection.js`, a fully dead file with zero call sites
anywhere in the repository, already superseded in production by an inline
copy in `api/slate.js`. Verified zero behavior change via full test suite
re-run.

## Refactors intentionally deferred

Every item in the ranked table above, plus every duplicate-logic finding
in `docs/DUPLICATE_LOGIC_INVENTORY.md` other than §8.

## Remaining technical debt (not itself a refactor, just enumerated)

- 3 of the 5 originally-identified orphaned/dead scripts
  (`build_final_index.py`, `pull_confirmed.py`, `validate_slate.py`) were
  removed in the Production Reliability and Settlement Recovery
  milestone -- each confirmed to have zero production call sites AND zero
  test references before deletion. The remaining two (the shadow
  implementations, §4 above -- `stale_date_guard.py` and
  `data_quality_gate.py`) still have live test coverage and were
  deliberately left in place per §4's "one careful pass per pair, not a
  batch deletion" recommendation.
- `data/execution_slip_*`, `data/f5_audit_*`, `data/lineup_audit_*` are
  write-only audit trails with no reader — not wrong, just worth noting
  they exist purely for human inspection, not pipeline logic.
- `data/session_bets/` appears abandoned (2 dated files from the
  project's first week, never used since) — status unconfirmed.
- No PR-triggered CI exists in this repository (confirmed during the
  Phase 1 PR hardening pass) — orthogonal to this phase's scope but worth
  restating here since it affects how confidently any future refactor
  in this list can be validated before merge.

## Recommended Phase 3 plan

1. Ledger reconciliation (`data/bets.json` vs. root `bets.json`) —
   non-destructive report first.
2. Retire the three shadow implementations (§4) one at a time, each with
   its own before/after test-coverage comparison.
3. Resolve `slate.json` vs. `authoritative.json` (§2).
4. Build a `pipeline_status.json` consumer (§8).
5. Confirm with the repo owner whether the vestigial Kalshi index
   pipeline (§5) and `data/session_bets/` manual path are still needed;
   act on the answer.
6. Only after 1–5: begin Phase 4's schema materialization (§1) and
   version-tagging (§6) work, since those are the highest-impact but also
   the most invasive changes, and benefit from the smaller cleanups above
   already being done first.
