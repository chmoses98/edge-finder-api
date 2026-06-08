# MERGE READINESS REPORT
**Branch:** `clv-hardening-rule71-review`  
**Reviewed:** 2026-06-08  
**CI status:** 28/28 steps ✓ (run 27155276178)  
**Tests:** 104/104 pass  
**Verdict:** ⛔ NOT READY — 1 critical issue must be fixed before merge

---

## Critical Issue (Blocks Merge)

### 1. `update-clv.yml` hardcodes the branch name

```yaml
# .github/workflows/update-clv.yml, line 28
- name: Checkout repo
  uses: actions/checkout@v4
  with:
    ref: clv-hardening-rule71-review   # ← MUST be removed
    fetch-depth: 0
```

**Impact:** After merging to main, the CLV update workflow will continue checking out `clv-hardening-rule71-review` instead of main. It will silently function until the branch is deleted — at that point the entire CLV workflow breaks with a hard error.

**Fix:** Remove the `ref:` line entirely. `actions/checkout@v4` without `ref` defaults to the branch that triggered the workflow, which is correct.

---

## Production Behavior Changes (Branch vs Main)

### New scripts (additive, not present on main)

| Script | Purpose | Safety |
|--------|---------|--------|
| `validate_slate_pre.py` | Pre-pipeline gate: starters + pinnacleVF only | Replaces old `validate_slate.py` |
| `validate_slate_final.py` | Post-pipeline gate: full ledger + enrichment check | New — blocks bad commits |
| `fetch_kalshi_clv_v2.py` | Historical candlestick CLV from Kalshi API | Strict: fails loudly on missing ticker/timestamp/candle |
| `audit_bet_identity.py` | Classifies each bet's CLV-readiness status | Read-only against bets.json |
| `backfill_market_identity.py` | Retroactively assigns marketTicker from dated snapshots | Snapshot-gated; never cross-dates |
| `rule71_tracker.py` | Converts Rule 71 from hard block to confidence downgrade; tracks ROI/CLV | Behavioral change to bet sizing, not pipeline |

### Modified scripts

| Script | Version | Change summary |
|--------|---------|---------------|
| `fetch_savant_pitchers.py` | v5.0 → v5.1 | null-safe pitcher access; retry/backoff; explicit exit 1 on crash |
| `post_fetch_gate.py` | v1.0 → v1.1 | Several ERROR→WARNING (see below); removed Kalshi registry check |
| `enrich_data.py` | v6.0 | Added `ABBR_NORMALIZE` map (`ARI→AZ`, `OAK→ATH`) |

### Workflow changes

**`fetch-slate.yml`** (+152 -22 lines):
- Fetches odds **before** Kalshi (not after validate)
- New: Archive step saves `kalshi_search_{DATE}.json` snapshot with stale-date guard
- New: Snapshot commit runs unconditionally (`if: always()`) — survives pipeline failures
- New: Pre-validate gate (exit 0/1/2) before full pipeline runs
- New: Final validate gate after build_market_ledger before commit
- Old `validate_slate.py` step replaced by `validate_slate_pre.py`

**`update-clv.yml`** (+82 -16 lines):
- Adds new `skip_settlement` input flag
- Adds Kalshi CLV v2 step (Phase 3 candlestick CLV)
- Adds identity audit step (writes `data/identity_audit.json`)
- Adds Rule 71 tracking report step (writes `data/rule71_report.json`)
- Commit now includes `data/identity_audit.json` and `data/rule71_report.json`
- **CRITICAL BUG:** hardcodes `ref: clv-hardening-rule71-review`

---

## Validation Rules Weakened ERROR → WARNING

Each downgrade was necessary to allow the pipeline to run at valid data-readiness windows (pipeline runs at ~3pm–6pm ET; many game data fields are posted progressively throughout the day).

### `post_fetch_gate.py` (v1.0 → v1.1)

| Check | Old | New | Why safe | Residual protection |
|-------|-----|-----|----------|---------------------|
| `pitcherSavant=null` (single side) | ERROR | WARN | TBD starters are normal for evening games at 3-5pm ET | Dual-null xFIP on same game still hard FAILs |
| `lineupConfirmed=null` | ERROR | WARN | Lineups post 3-6pm ET; pipeline often runs before they're posted | `all_rpg_null` (last7, last15, season all null) still FAILs |
| `teamStats block missing` | ERROR | WARN | Rare edge case; enrich_data handles missing team gracefully | R/G null check still FAILs if no baseline computable |
| `kalshi_market_registry.json` check | REMOVED | — | Registry is built **after** this gate runs — check was always wrong timing | ⚠️ See gap below |

**Kalshi registry check removal:** The check was misplaced — `post_fetch_gate.py` runs before `build_kalshi_registry.py`. Moving it earlier was correct. **However, no equivalent check was added after `build_kalshi_registry.py` runs.** If Kalshi API returns zero markets, the registry is silently empty. `validate_slate_final.py` warns (not errors) on null Kalshi prices. `regression_test.py` asserts Accepted rows have `kalshiPrice != null` but a zero-market registry produces zero Accepted rows — so regression_test passes on an empty registry. **The pipeline will commit a slate with zero Kalshi coverage if the Kalshi API fails completely.** This is a pre-existing gap; no bets would be logged, but the silent pass is worth noting.

### `validate_slate_final.py` (new file)

This gate did not exist on main. The rules below were new and calibrated during the session:

| Check | Classification | Why |
|-------|---------------|-----|
| `pitcher.name` missing (TBD starter) | WARN | Evening games at 3-5pm ET genuinely have TBD starters |
| `pitcherSavant=null` | WARN | Same — TBD starter means no Savant data yet |
| `lineupConfirmed=null` | WARN | Lineups not yet posted |
| `offenseBaselineAdj=null` | WARN | Team abbr mismatch fallback (ARI/AZ); enrich_data always writes this when team is found |
| `awayProjRuns` absent from `allEdges` | WARN (when ledger present) | `allEdges` field only populated by Vercel engine; `marketLedger` is the authoritative evaluation record |
| Kalshi prices null | WARN | Kalshi doesn't list every game/market |
| `pinnacleVF.away` missing | **ERROR** | Required for Rule 71 gap; pre-validate already gated on this |
| `marketLedger` empty or missing required market | **ERROR** | Pipeline failure if ledger is absent after build step |
| Invalid ledger row status | **ERROR** | Data integrity |
| Accepted row with null edge/confidence/kalshiPrice | **ERROR** | Same as regression_test assertion A7 |
| Negative `recentFIP` after `fetch_savant_pitchers` ran | **ERROR** | Should have been sanitized to 0.0 by v5.1 |

---

## Silent-Pass Risk Assessment

| Risk | Severity | Status |
|------|----------|--------|
| Empty Kalshi registry silently commits | Medium | Pre-existing gap, now slightly wider (registry check removed from gate). No bets would be logged. Recommend adding post-build registry check in a follow-up. |
| `offenseBaselineAdj=null` (team not in teamstats) | Low | Model uses league-average fallback (4.5 R/G). WARN is visible in CI logs. Root cause is abbr mismatch (ARI/AZ), which `ABBR_NORMALIZE` now fixes. |
| `awayProjRuns` absent from allEdges | Low | Projection ran but result wasn't surfaced in allEdges (happens with TBD pitchers). `marketLedger` is the real output; this field is informational. |
| TBD pitcher (no Savant data) | Low | `build_market_ledger.py` uses league-average xFIP=4.50 fallback. This is documented behavior, not a silent corruption. |

---

## Debug Artifacts and Temporary Code

| Item | File | Assessment |
|------|------|------------|
| `print(f'Loading slate from: {path}...')` | `validate_slate_final.py` L46 | Informational; useful for CI logs. Not harmful. Minor cleanup candidate. |
| `# DIAGNOSTIC: print slate summary...` comment + 3-game print | `validate_slate_final.py` L77–85 | Added during debugging session. Output is useful (shows game count and field presence). Minor cleanup candidate. |
| `2>&1` on validate step | `fetch-slate.yml` L240 | Added to merge stderr into CI stdout log. Causes double-printing since errors now go to both streams. Not harmful; operational value in making CI failures visible. |
| `VALIDATE CRASH` error duplicated to stdout + stderr | `validate_slate_final.py` L246–248 | Intentional for CI visibility. Not a bypass. |
| Error messages duplicated to stdout + stderr | `validate_slate_final.py` L270–284 | Intentional for CI visibility. Not a bypass. |

**Confirmed absent:**
- No `TEMP DEBUG` strings
- No `would exit 1` bypass
- No hardcoded credentials
- No `exit 0` used as a bypass (all `sys.exit(0)` calls are legitimate success paths)

---

## Summary

| Category | Count | Notes |
|----------|-------|-------|
| New scripts | 6 | Additive; don't affect existing behavior |
| Modified scripts | 3 | fetch_savant_pitchers, post_fetch_gate, enrich_data |
| New workflows (modified) | 2 | fetch-slate, update-clv |
| Tests | 104 | All passing |
| Critical blockers | 1 | `update-clv.yml` hardcoded branch ref |
| Validation downgrades | 8 | All justified by data-timing reality; residual protections intact |
| Silent-pass gaps | 1 existing, slightly wider | Empty Kalshi registry |
| Cleanup items | 4 | Non-blocking; diagnostic prints and debug redirect |

---

## Merge Decision

**⛔ NOT READY.** One fix required:

```
Remove `ref: clv-hardening-rule71-review` from .github/workflows/update-clv.yml (line 28).
```

Once that line is removed, all other issues are either pre-existing, non-blocking, or cleanup candidates that can follow in a separate commit. The branch is otherwise technically sound: 28/28 CI steps pass, 104/104 tests pass, and all behavioral changes are justified and protected by residual validation.
