# DUPLICATE_LOGIC_INVENTORY.md

Phase 2, Part 3 — every place business logic is duplicated across the
repository, with a canonical recommendation for each. Per the phase
brief: **only the safest duplicates are consolidated this phase**
(one, documented in §8); everything else is documented for a future
phase.

---

## 1. Game status / live-game / postponement detection

**Canonical implementation:** `lib/postponed_guard.py` (`check_game_status`,
`is_postponed`, `is_in_play`, `is_final`, `check_first_pitch_passed`) — the
two-signal design (explicit status + first-pitch-timestamp fallback) is
well-tested (`tests/test_live_game_gate.py`, 30+ cases) and is already the
implementation `write_pending_bets.py`, `validate_bet_logging.py`,
`risk_gate.py` (Phase 1), and `validate_slate_final.py` all call.

**Duplicate found this phase:** `scripts/log_session_bets.py`'s
`validate_bet()` does its own naive check —
`datetime.fromisoformat(start_str) < datetime.now(utc)` — instead of
calling `check_first_pitch_passed()`. Lower severity than it looks: this
script is not wired into any workflow (manual/CLI-only, last used
2026-06-18), so it carries no live production risk today, but it's a
landmine if the manual-session path is ever revived.

**Recommendation:** point `log_session_bets.py`'s naive check at
`lib/postponed_guard.check_first_pitch_passed()`. Deferred — this script's
current use status is unclear (see `docs/SOURCE_OF_TRUTH_MAP.md` §12);
changing it without confirming it's still wanted risks solving a problem
nobody has.

---

## 2. Sentinel-price detection

**This is the most-duplicated concept in the repository — five
independent implementations, with drifted constant sets:**

| # | Implementation | Constant set | Status |
|---|---|---|---|
| 1 | `lib/sentinel_validator.py` (`scan_for_sentinels`, field-aware) | `{19900, -19900, 100000, -100000}` (+ distinguishes model-probability fields from settlement fields) | **Canonical** — most correct (avoids false positives on legitimate 100%-probability model fields), most-used (`protect_slate.py`, `slate_manager.py` primary path) |
| 2 | `lib/slate_manager.py`'s own `is_sentinel_price()`/`find_sentinel_in_object()` | Same core set, but generic (not field-aware) | Explicitly a documented fallback: `validate_game_for_rerun()` tries to import #1 first, only falls back to this on `ImportError`. Intentional redundancy, not a silent drift. |
| 3 | `lib/clv_validator.py`'s own `is_sentinel()` | `{19900, -19900, 100000, -100000, 199, -199}` — **different set**, includes `199`/`-199` | Test-only — no production script calls `validate_clv()` |
| 4 | `scripts/capture_clv_pregame.py`'s own `is_sentinel_price()` | Its own set (see module docstring's `SENTINEL_PRICES`) | **Wired into production** (`clv_capture.yml`) — a genuine production duplicate, not test-only |
| 5 | `api/slate.js`'s inline IIFE `annotateSentinels`/`SENTINEL_SET` | JS, own copy | **Wired into production** (`api/slate.js`, the single most-called endpoint) — explicitly bypasses `lib/slate_protection.js` "to avoid a `require()` dependency in the Vercel serverless environment" (comment in the code itself) |

**Recommendation:** `lib/sentinel_validator.py` should be the sole
canonical Python implementation; `scripts/capture_clv_pregame.py`'s copy
should import it instead of maintaining its own constant set (real
production risk: if the canonical set is ever updated — e.g. a new
sentinel value is discovered — this copy would silently miss it). The JS
duplicate in `api/slate.js` is a separate ecosystem (Node vs. Python) and
would need a shared JSON constants file or similar to unify safely — larger
migration, Phase 4+.

**Not consolidated this phase:** `capture_clv_pregame.py`'s sentinel
constants touch live CLV-integrity logic (real-money adjacent); changing
it without a dedicated regression pass risks exactly the kind of subtle
CLV-correctness regression this audit is trying to prevent. Documented,
not touched.

---

## 3. Stale-date validation

**Two parallel, differently-scoped implementations exist:**

- **`scripts/validate_current_slate_date.py`** — the one actually wired
  into `fetch-slate.yml`. Cross-checks `fetch_status.json`/`meta.json`/
  `slate.json`/`pitchers.json`/`kalshi_search.json`/
  `kalshi_market_registry.json` dates plus Kalshi-ticker date-prefixes.
- **`scripts/stale_date_guard.py`** — a "shared helper"
  (`check_date_or_abort()`) with the *same* abort-message format
  (`"STALE SLATE ABORT: requested=... actual=... source=..."`) and a
  near-identical file set, but **not called from any workflow** — only
  reachable via `tests/test_stale_date_guard.py`.

**Recommendation:** `validate_current_slate_date.py` is canonical (it's
what actually runs in production and is what the Phase 1 incident
analysis relied on). `stale_date_guard.py` should be deprecated —
either its logic should be deleted in favor of the production gate, or
(if its test coverage exercises scenarios the production gate doesn't)
that coverage should be ported onto the production gate and this file
retired.

**Not consolidated this phase:** `stale_date_guard.py` has live,
passing test coverage (`test_stale_date_guard.py`, 27 scenarios). Removing
or rewriting it risks silently dropping test coverage for edge cases the
production gate may not actually handle — this needs a side-by-side
behavioral diff before any merge, which is Phase 3 work, not a "safest
duplicate" this phase.

---

## 4. Kalshi ticker-date string conversion (`YYYY-MM-DD` → `26JUN13`)

Independently re-derived, byte-for-byte identical logic, in at least six
places: `validate_current_slate_date.py`, `data_quality_gate.py`,
`stale_date_guard.py`, `backfill_market_identity.py`,
`build_final_index.py`, `pull_confirmed.py`.

**Recommendation:** extract into a single `lib/kalshi_ticker_date.py`
helper (e.g. `to_kalshi_date_prefix(date_str) -> str`) in a future phase.
Zero behavior risk to consolidate (it's pure string formatting with no
external dependency), but touches enough files that it's a "small
migration," not a zero-risk one-liner — deferred, not attempted this
phase to keep this pass's diff minimal and auditable.

---

## 5. Bet-eligibility classification

**Canonical:** `scripts/bet_eligibility.py: apply_eligibility()` —
wired into `build_market_ledger.py`, sets `bet_eligibility_status` /
`clv_capture_status` / `review_integrity_status` on every ledger row.

**Duplicate:** `scripts/data_quality_gate.py: classify_bet()` — an
independently-designed second taxonomy (`OK_REAL_ELIGIBLE`,
`REJECT_TICKER_MISMATCH`, `REJECT_PITCHER_MISMATCH`, `PAPER_ONLY_DATA_WARNING`,
etc.) solving the same problem with different vocabulary. Confirmed dead
in production (not called by any workflow or any other script; only
`tests/test_stale_date_guard.py` imports it).

**Recommendation:** since `data_quality_gate.py` is unreachable in
production, it can eventually be either deleted (if its ideas are fully
subsumed by `bet_eligibility.py`) or its distinct ideas (e.g. explicit
`REJECT_PITCHER_MISMATCH`) folded into the canonical classifier if they
represent real gaps. Requires a side-by-side feature comparison — Phase 3.

**Not consolidated this phase:** same reasoning as §3 — has its own test
file, needs a deliberate compare-and-port pass, not a blind deletion.

---

## 6. Lineup confirmation

**Not actually duplicated** — `fetch_lineups.py` (computes
`lineupWOBADelta`/`lineupAdj`/`lineupConfirmed` from MLB boxscore data)
and `enrich_lineup_confirmed.py` (promotes team-level confirmation to
game-level) are two **complementary** stages of one pipeline, not two
competing implementations. Documented here only because the phase brief
asked me to specifically check this area — verdict: no consolidation
needed.

---

## 7. Executable-price / max-bet-price math

**Canonical:** `scripts/executable_price.py` (pure library:
`get_executable_prices`, `executable_prob_from_price`,
`check_max_bet_price`).

**Duplicate:** `scripts/build_market_ledger.py` contains an inline
`except ImportError:` fallback that reimplements the same three
functions, in case the import fails.

**Recommendation:** this is defense-in-depth by design (the fallback only
activates if the canonical module can't be imported at all, which would
be a packaging/deployment error, not a runtime code path) rather than an
accidental duplicate. Low priority — worth a comment in the code
clarifying intent, but not a functional risk. Not touched this phase.

---

## 8. Sentinel scan in the JS layer — resolved this phase (the one safe consolidation)

`lib/slate_protection.js` was designed to be `require()`d from
`api/slate.js` (per its own docstring) but **nothing in the repository
ever required it** — confirmed by grepping every `.js` file. `api/slate.js`
instead has its own inline, already-in-production `annotateSentinels`
IIFE that fully supersedes it (explicit comment in `api/slate.js`: *"We use
an inline sentinel check here to avoid a `require()` dependency in the
Vercel serverless environment"*).

**This is the one duplicate consolidated in this phase**, because it meets
every safety bar:
- Zero call sites anywhere in the repository (workflows, scripts, tests,
  or other `.js` files) — confirmed by exhaustive grep.
- The logic it would have provided is already running in production via
  the inline copy in `api/slate.js`, which is untouched.
- Removing it changes nothing about what any workflow, script, or
  deployed Vercel function executes.

**Action taken:** deleted `lib/slate_protection.js`. No other file
changed. Full Python test suite re-run afterward with no change in
pass/fail counts (it's a `.js` file with zero Python importers, so this
was expected — verified rather than assumed).

---

## 9. Duplicate-logic summary table

| Area | Canonical | Duplicate(s) | Production risk | Consolidated this phase? |
|---|---|---|---|---|
| Game status / live-game | `lib/postponed_guard.py` | `log_session_bets.py`'s inline check | Low (script not wired into CI) | No — deferred |
| Sentinel prices | `lib/sentinel_validator.py` | `slate_manager.py` (intentional fallback), `clv_validator.py` (test-only), `capture_clv_pregame.py` (**production**), `api/slate.js` (**production**, JS) | Medium — two live production copies with independently-maintained constants | No — deferred, needs dedicated CLV-safety regression pass |
| Stale-date validation | `validate_current_slate_date.py` | `stale_date_guard.py` (test-only) | Low (unused copy) | No — deferred, has its own test suite to preserve |
| Kalshi ticker-date formatting | none designated | 6 independent copies | Low (pure formatting) | No — deferred, touches too many files for a "safest" pass |
| Bet eligibility | `scripts/bet_eligibility.py` | `data_quality_gate.py` (test-only) | Low (unused copy) | No — deferred, needs feature comparison |
| Lineup confirmation | N/A — not actually duplicated | N/A | N/A | N/A |
| Executable-price math | `scripts/executable_price.py` | `build_market_ledger.py` import-fallback | None (intentional defense-in-depth) | No — not a real duplicate |
| JS sentinel wrapper | `api/slate.js` inline copy | `lib/slate_protection.js` (fully dead) | None (dead file) | **Yes — removed** |
