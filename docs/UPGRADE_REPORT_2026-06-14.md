# Reliability Upgrade Report — 2026-06-14

Generated: 2026-06-14
Session type: Automated reliability upgrade

---

## HEAD Commit at Start of Session

```
5543192 log(2026-06-14): 1W 1L -$0.80 real | 39 entries (2 REAL, 5 MODEL_ONLY, 32 PAPER) | schema: add trackingType/actuallyPlaced/placementConfirmedAt
```

---

## Commits Created

| Hash | Message |
|------|---------|
| 51fa2f2 | audit: June 14 block rule audit and opportunity table |
| 9d26770 | fix: data integrity — CLV infrastructure, authoritative slate, sentinel rejection, trackingType enforcement, postponed guard, F5 settlement |
| 65fa5ec | test: 20 regression tests for reliability upgrade |
| c8a88d4 | chore: add .gitignore for Python cache files |
| (this commit) | docs: BETTING_FRAMEWORK.md, UPGRADE_REPORT_2026-06-14.md |

---

## Files Changed

### New files created

```
docs/audit_june14.md                     Phase 1 audit
docs/BETTING_FRAMEWORK.md                Framework documentation
docs/UPGRADE_REPORT_2026-06-14.md        This report

lib/clv_validator.py                     CLV validation logic
lib/f5_settlement.py                     F5 settlement (linescore primary)
lib/postponed_guard.py                   Postponed/cancelled game guard
lib/promotion_engine.py                  CLV-based promotion/demotion framework
lib/sentinel_validator.py                Sentinel price rejection
lib/slate_manager.py                     Authoritative slate protection
lib/tracking_type.py                     trackingType enforcement
lib/yrfi_nrfi_validator.py               YRFI/NRFI input validator

scripts/capture_clv_pregame.py           Pregame CLV snapshot capture
scripts/generate_performance_report.py   Rolling performance report generator

tests/test_reliability_upgrade.py        20 regression tests
```

---

## Tests Added — All 20, Pass/Fail Status

| # | Test | Status |
|---|------|--------|
| 1 | CLV snapshot before first pitch → VALID | PASS |
| 2 | CLV snapshot after first pitch → INVALID_POST_START | PASS |
| 3 | Missing snapshot → MISSING | PASS |
| 4 | Missing ticker → TICKER_NOT_FOUND | PASS |
| 5 | Sentinel prices are rejected (19900, -19900, 100000, -100000) | PASS |
| 6 | Authoritative slate cannot be overwritten by rerun | PASS |
| 7 | Valid lineup recheck before first pitch can update a game | PASS |
| 8 | Rerun after first pitch cannot update that game | PASS |
| 9 | One contaminated game does not overwrite the full slate | PASS |
| 10 | Widespread contaminated rerun is quarantined as REJECTED_CONTAMINATED | PASS |
| 11 | PAPER F5 $1.50 bet is NOT counted as REAL | PASS |
| 12 | Official bankroll excludes MODEL_ONLY and PAPER | PASS |
| 13 | REAL_PROBE is separate from REAL in bankroll reporting | PASS |
| 14 | Postponed game generates no active bets | PASS |
| 15 | F5 tie grades as LOSS | PASS |
| 16 | F5 settlement uses linescore before RBI reconstruction | PASS |
| 17 | YRFI/NRFI explanations cannot cite bullpen/full-game-only factors | PASS |
| 18 | Game totals are tracked even when blocked for win-rate reasons | PASS |
| 19 | Post-slate review cannot use postgame snapshots for CLV | PASS |
| 20 | betSize > 1 never controls real-money classification | PASS |

**Result: 20/20 PASS**

---

## Example CLV Output

```json
{
  "clvStatus": "VALID",
  "clvPct": 2.15,
  "entryPrice": -141,
  "entryTimestamp": "2026-06-14T11:36:00-04:00",
  "closePrice": 55.0,
  "closeTimestamp": "2026-06-14T13:35:00Z",
  "clvNotes": "CLV calculated from pregame snapshot pregame_748550.json"
}
```

When CLV unavailable:
```json
{
  "clvStatus": "MISSING",
  "clvPct": null,
  "entryPrice": -141,
  "entryTimestamp": "2026-06-14T11:36:00-04:00",
  "closePrice": null,
  "closeTimestamp": null,
  "clvNotes": "No pregame snapshot found for ticker KXMLBF5-26JUN141340ATLNYM-ATL on 2026-06-14"
}
```

Note: clvPct is NEVER zero when unavailable — always null with descriptive status.

---

## Example Slate File Structure

### authoritative.json
```json
{
  "date": "2026-06-14",
  "_runType": "OFFICIAL_PREGAME",
  "_authoritative": true,
  "_officialRunAt": "2026-06-14T15:29:00Z",
  "games": [...]
}
```

### recheck_20260614T180000Z.json
```json
{
  "date": "2026-06-14",
  "_runType": "LINEUP_RECHECK",
  "games": [...]
}
```

### rejected_contaminated_20260614T190000Z.json
```json
{
  "date": "2026-06-14",
  "_runType": "REJECTED_CONTAMINATED",
  "_quarantined": true,
  "_reason": "Widespread contamination: 4/5 games rejected",
  "games": [...]
}
```

---

## Proof: Contaminated Reruns Cannot Overwrite Authoritative Slate

**Code:** `lib/slate_manager.py` — `merge_rerun_into_authoritative()` function

Logic:
1. Each game in rerun is validated by `validate_game_for_rerun()` before updating
2. If game contains sentinel prices, it is added to `rejected` list, not merged
3. If rejection_rate > 0.5 (>50% games bad), entire rerun is quarantined:
   ```python
   if rejection_rate > 0.5 and len(rejected) >= 2:
       run_report = {"runType": RUN_TYPE_REJECTED_CONTAMINATED, ...}
       return auth_data, run_report  # Returns unchanged authoritative
   ```
4. save_slate() for REJECTED_CONTAMINATED writes to `rejected_contaminated_*.json` — NEVER touches authoritative.json

**Test:** Test 9 (one contaminated game) and Test 10 (widespread contamination) both verify this.

---

## Proof: Postgame Snapshots Cannot Be Used for CLV

**Code:** `lib/clv_validator.py` — `validate_clv()` function

Logic:
1. Snapshot is loaded from `data/clv_snapshots/YYYY-MM-DD/pregame_*.json`
2. If snapshot's `clvStatus != "VALID"`, that status is returned directly (INVALID_POST_START, etc.)
3. Even if snapshot has a valid price, close timestamp is checked against game start:
   ```python
   if close_ts_dt and game_start_dt and close_ts_dt >= game_start_dt:
       return _unavailable("INVALID_POST_START", ...)
   ```
4. Yes_price of 98+ (settlement prices near 100) are rejected by `is_valid_yes_price()`

**Test:** Test 2 (post-start snapshot) and Test 19 (postgame snapshot) both verify this.

---

## Proof: betSize > 1 No Longer Controls Real-Money Classification

**Code:** `lib/tracking_type.py` — `TrackingSchema.counts_for_bankroll` property

```python
@property
def counts_for_bankroll(self):
    return (
        self.trackingType in (TRACKING_REAL, TRACKING_REAL_PROBE)
        and self.actuallyPlaced is True
        and self.placementConfirmedAt is not None
    )
```

betSize is not part of the bankroll calculation. Only trackingType + actuallyPlaced + placementConfirmedAt determine inclusion.

For REAL_PROBE specifically:
```python
if self.trackingType == TRACKING_REAL_PROBE:
    if self.betSize > REAL_PROBE_ABSOLUTE_MAX_STAKE:  # $1.50
        raise TrackingTypeError(...)
```

REAL_PROBE bets with betSize > $1.50 raise an error, preventing misclassification.

**Test:** Tests 11, 12, 13, and 20 all verify this.

---

## REAL_PROBE Rules as Implemented

See `lib/tracking_type.py`:
- Max stake: $1.00 (`REAL_PROBE_MAX_STAKE = 1.00`)
- Absolute max stake: $1.50 (`REAL_PROBE_ABSOLUTE_MAX_STAKE = 1.50`)
- Must pass ALL DATA_HARD and MARKET_MECHANICS_HARD blocks
- Can fail at most ONE RISK_SOFT or CALIBRATION block
- Exact ticker required
- Valid pregame price required
- Default actuallyPlaced=false until confirmed in final slip
- `validate_real_probe_eligibility()` enforces all constraints

---

## Recommended Market-Specific Changes

### Implemented

| Market | Change | Status |
|--------|--------|--------|
| ML | Rule 51 reclassified as RISK_SOFT (probe-eligible when pitcher/price/identity clean) | DOCUMENTED in audit |
| F5 ML | F5 multiplier confirmed not inflating paper bets (trackingType enforcement) | IMPLEMENTED |
| F5 ML | Correct tie settlement: tied = LOSS for F5 ML side | IMPLEMENTED |
| YRFI/NRFI | Disallowed bullpen/full-game inputs validation | IMPLEMENTED |
| YRFI/NRFI | Required first-inning-specific output fields | IMPLEMENTED |
| YRFI/NRFI | Probe eligibility check added | IMPLEMENTED |
| Team Totals | CALIBRATION class, tracking fields documented | DOCUMENTED |
| Game Totals | Must track all blocked bets with market identity + CLV fields | DOCUMENTED |
| All markets | CLV snapshot infrastructure | IMPLEMENTED |
| All markets | Authoritative slate protection | IMPLEMENTED |
| All markets | Sentinel price rejection | IMPLEMENTED |
| All markets | Postponed game guard | IMPLEMENTED |
| All markets | REAL_PROBE lane | IMPLEMENTED |

### Recommended Only (not yet implemented in slate.js)

These require changes to the Node.js slate generator (api/slate.js):
- Actual Rule 51 soft block logic injection into slate.js output
- Authoritative slate write on first pregame run (currently slate.js writes data/slate.json)
- Pregame tracked ticker persistence at generation time (needs slate.js integration hook)
- F5 linescore API call in settlement workflow
- YRFI output field injection into slate.js eval

These are library functions ready for integration — the logic is proven by tests.

---

## What Was Intentionally NOT Changed

Per the mandate, these were not modified:

1. **Core model probability calculations** — Poisson engine in MODEL_CORE.md unchanged
2. **Core edge formulas** — `edge = (modelProb - kalshiVF) × calibrationFactor` unchanged
3. **Standard bet sizing rules** — Base sizes (High=$4, Medium=$3, Paper=$1) unchanged
4. **Market multipliers** — F5 (1.5x), TT (1.25x), YRFI (1.25x) all unchanged
5. **Calibration factors** — HIGH=0.187, MEDIUM=0.255, PAPER=0.18 unchanged
6. **Edge thresholds** — High 3.0%, Medium 1.5%, Paper 1.0% unchanged
7. **Signal hierarchy** — xERAGap, starterXERA tier rankings unchanged
8. **Park factors** — Numeric table unchanged
9. **Rules text** — RULES.md not modified (audit only)
10. **config/rules.json** — Not modified

---

## Remaining Risks

1. **Integration gap**: lib/ modules are Python; api/slate.js is Node.js. The authoritative slate protection and sentinel rejection need to be integrated into slate.js or called via a Python pre-commit hook. Currently the logic exists but is not wired into the live slate generation flow.

2. **CLV snapshot timing**: capture_clv_pregame.py must be run as a scheduled step between slate generation and first pitch. If it is not triggered, all CLV will remain UNAVAILABLE (same as June 14).

3. **YRFI composite completeness**: The current slate.js evalNRFI() function uses a NRFI score that does include bullpen-adjacent signals. The yrfi_nrfi_validator.py library validates outputs but does not yet intercept slate.js's generation. Requires slate.js modification.

4. **No production test of F5 linescore API**: f5_settlement.py is implemented and tested with mock data. The actual MLB linescore API endpoint /api/v1/game/{gamePk}/linescore has not been called in production from this module.

5. **Rule 51 ML gate**: The audit correctly identifies this as RISK_SOFT, but Rule 51 is not formally defined in RULES.md (it appears only in bets.json notes). It should be formalized.

6. **REAL_PROBE promotion path**: The framework is built; the decision to classify any specific market as REAL_PROBE requires manual review by the bettor — no automatic promotion occurs.

7. **pycache in git**: The .gitignore was added but pycache files from the test commit (65fa5ec) are already in git history. They will not be tracked going forward.
