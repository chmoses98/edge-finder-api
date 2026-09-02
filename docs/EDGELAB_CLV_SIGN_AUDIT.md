# CLV sign convention: impact audit (READ-ONLY)

**Status: audit + findings. No ledger row mutated. No production behaviour
changed by this document.** The accompanying remediation PR is separate
and unmerged.

## The contradiction

The intuitive, and the convention MLB-ALPHA-0001's universe CLV uses:

```
good_clv = closing side-relevant executable price - entry side-relevant price
positive = you entered CHEAPER than the close  (good)
```

Several production writers instead store `entry - closing`, while their
own documentation describes a positive value as good. `lib/edgelab/clv.py`
states positive means "entered at a better (cheaper) price than the close"
— but for a buyer, cheaper-than-close means `closing > entry`, which that
formula scores **negative**.

## 1. Writers and their actual sign

| Writer | Formula | Convention |
|---|---|---|
| `clv_update.py:1050` | `(close_imp - our_imp) * 100` | **positive-is-good** ✅ |
| `clv_update.py:1709` | `(our_closing_impl - bet_impl) * 100` | **positive-is-good** ✅ |
| `scripts/fetch_kalshi_clv_v2.py:315` | `(closing_implied - entry_implied) * 100` | **positive-is-good** ✅ |
| `scripts/clv_from_snapshot.py:558` (`clvMidPct`) | `closing_pct - entry_pct` | **positive-is-good** ✅ |
| `scripts/clv_from_snapshot.py:560` (`clvAskPct`) | `closing_ask_pct - entry_pct` | **positive-is-good** ✅ |
| `scripts/clv_from_snapshot.py:140` (`clv_pp`) | `(entry_implied - closing_implied) * 100` | **INVERTED** ❌ |
| `lib/edgelab/clv.py::compute_clv_for_bet` | `(entry_implied - closing_implied) * 100` | **INVERTED** ❌ |

The two conventions coexist *inside a single file*: `clv_from_snapshot.py`
writes `clv_pp` inverted while its own `clvMidPct`/`clvAskPct` are
positive-is-good.

## 2. What the canonical ledger actually stores

`scripts/research/clv_sign_audit/audit_stored_clv_sign.py` recomputes
side-aware good-CLV from each bet's own `entryPrice`, `closingPrice` and
`side` and compares it with the stored `clv`
(`data/edgelab/research_artifacts/clv_sign_audit/stored_clv_sign_audit.json`):

| Classification | Bets |
|---|---:|
| matches **INVERTED** (`entry − closing`) | **184** |
| matches positive-is-good | **0** |
| sign-ambiguous (`entry == closing`, so CLV 0 — evidence for neither) | 97 |
| stored `clv` null | 104 |
| other discrepancy | **0** |
| **total** | 385 |

**There is ONE clean convention in the canonical ledger, and it is the
inverted one.** No date boundary, no source split: `MANUAL`, `MODEL` and
`OTHER` rows, and both 2026-06 and 2026-08, all agree. (An earlier pass of
this audit reported a 97/184 split; that was a classifier ordering flaw —
a zero-CLV row matches *both* formulas and is evidence for neither. It is
now checked for ambiguity first.)

Row-level proof:

| side | entry | closing | stored `clv` | true good CLV | reality |
|---|---:|---:|---:|---:|---|
| YES | 0.3300 | 0.3400 | **−1.00** | +1.00 | bought 1¢ **below** the close — good |
| YES | 0.6255 | 0.6100 | **+1.55** | −1.55 | paid 1.55¢ **above** the close — bad |

## 3. Consumer classification

| Consumer | Class | Reads CLV sign? |
|---|---|---|
| `scripts/risk_gate.py` | LIVE_DECISIONING | **No** |
| `scripts/build_market_ledger.py` (qualification) | LIVE_DECISIONING | **No** — carries null CLV scaffold fields only |
| `scripts/write_pending_bets.py` | LIVE_DECISIONING | **No** |
| `scripts/bet_eligibility.py` | LIVE_DECISIONING | **No** — explicitly: "Missing CLV data NEVER blocks a live actionable bet" |
| `lib/promotion_engine.py` | ADVISORY_GOVERNANCE | **Yes** — `avgCLV >= 0` promote, repeated negative demote |
| `scripts/generate_performance_report.py` | REPORTING_ONLY | Yes (via promotion_engine) |
| `scripts/rule71_tracker.py` / `run_rule71_report.py` | REPORTING_ONLY | Yes — writes `data/rule71_report.json` |
| `lib/edgelab/calibration.py` | RESEARCH | Yes |
| `lib/edgelab/market_intelligence.py`, `market_comparison.py`, `reports.py` | REPORTING_ONLY | Yes |
| `lib/edgelab/scored_replay.py`, `analytics.py`, `query.py` | RESEARCH | Yes |
| `scripts/edgelab/run_edge_persistence_experiment.py` | RESEARCH | Yes |
| `lib/clv_validator.py` | REPORTING_ONLY (integrity) | Magnitude only |

## 4. Did the sign ever change real money? — the core question

**Automatically: NO.** Traced by call, not by name:

- No live gate reads a CLV field. `bet_eligibility.py` states the rule
  explicitly, and `build_market_ledger.py`'s CLV fields are a
  null-until-settlement scaffold, never an input to qualification.
- `promotion_engine` is imported by exactly one caller,
  `scripts/generate_performance_report.py`, which writes a JSON + Markdown
  report and nothing else.
- **Nothing in the repository writes `config/rules.json` programmatically.**
  Family eligibility changes are hand-edited.
- No live gate reads a performance report, promotion output, or
  `rule71_report.json`.

So: **no recommendation, confidence, stake, or family-eligibility value was
ever changed automatically by the CLV sign.**

**Via a human: plausibly, and this is the real exposure.** Rule 71 / Rule 81
suspensions are hardcoded strings in `build_market_ledger.py` that a person
wrote after reading a report, and they *do* block real money — they appear
as `rejectionReason` in real execution slips
(`data/execution_slip_2026-08-24.json`):

> `Rule 71 market suspension: Game Total WR 41%, CLV -1.43%`
> `Rule 81: RL suspended — WR 36%, CLV -4.09%`

**Classified UNKNOWN, deliberately.** No committed artifact contains the
values `-1.43` or `-4.09`, so which convention produced them cannot be
reconstructed, and I will not guess. Two mitigating facts, both
sign-independent: each rationale also cites a **win rate far below
break-even** (41% and 36%), which alone justifies suspension; and both
rules suspend *toward paper*, the conservative direction.

## 5. Historical conclusions — supersession status

| Surface | Status |
|---|---|
| Promotion/demotion verdicts in performance reports | **SIGN-REVERSED** — every family with n≥3 flips (see below) |
| Rule 71 / Rule 81 suspension rationales | **UNKNOWN** — CLV half unreconstructable; win-rate half unaffected |
| `docs/EDGELAB_CALIBRATION.md` CLV-sign study | **UNKNOWN** — needs re-reading under the corrected sign |
| Market-intelligence / market-comparison reports | **SIGN-REVERSED** in the CLV columns |
| MLB research sprint model-quality findings (Brier, log-loss, calibration) | **UNAFFECTED** — no CLV term |
| `docs/EDGELAB_KXMLBRFI_SUSPENSION.md` | **UNAFFECTED** in substance — its verdict rests on Brier vs a constant baseline; its "approximately flat / slightly negative" fee-aware note is settlement-based |
| **MLB-ALPHA-0001 C01 and C01-PIT** | **UNAFFECTED** — see §6 |

Advisory promotion verdicts, stored vs corrected (n≥3 families):

| Series | n | avg stored | avg corrected | stored verdict | corrected verdict |
|---|---:|---:|---:|---|---|
| KXMLBF5 | 107 | −0.516 | +0.516 | block/demote | promote paper→probe |
| KXMLBTEAMTOTAL | 59 | −0.763 | +0.763 | block/demote | promote paper→probe |
| KXMLBGAME | 52 | −0.446 | +0.446 | block/demote | promote paper→probe |
| KXMLBKS | 21 | −0.838 | +0.838 | block/demote | promote paper→probe |
| KXMLBTOTAL | 12 | +11.066 | −11.066 | promote probe→REAL | block/demote |
| KXMLBRFI | 10 | +0.823 | −0.823 | promote paper→probe | block/demote |
| KXMLBOUTS | 6 | −1.532 | +1.532 | block/demote | promote probe→REAL |
| KXMLBF3 | 5 | +0.806 | −0.806 | promote paper→probe | block/demote |
| KXMLBF7 | 5 | −8.800 | +8.800 | block/demote | promote probe→REAL |

All nine flip. Because these verdicts were advisory and never auto-applied,
this is a **reporting** correction, not a retroactive change to any wager.
Note the direction: most families' CLV was actually *better* than reported.

## 6. MLB-ALPHA-0001 is unaffected — verified, not assumed

- **C01 / C01-PIT economics never use CLV.** Their P/L is computed purely
  from executable entry price, the Kalshi fee model, and settlement
  outcome (`family_a_discovery.row_side_econ` → contracts, cash, net P/L).
  CLV appears nowhere in the candidate rules, the freeze, the discovery
  scan, the validation scoring, or the corrected inference.
- **The universe CLV artifact already uses the correct convention**, states
  it explicitly in its own module docstring, and computes it inside the
  research code rather than reading any stored `clv` field.
- The program's C01 execution audit reports `clvCentsWhenDistinct` with the
  same explicit closing-minus-entry definition, and its n is 0 anyway.

## 7. Remediation stance

The canonical ledger has **one** convention, uniformly applied, so a
correction is well-defined rather than ambiguous. The remediation PR
therefore introduces a single canonical helper and one stated convention,
with YES/NO tests — but **writes no migration to historical rows in this
session**, and no ledger value is multiplied by −1 anywhere.

---

## Canonicalization completed (2026-09-02)

Re-audited against **current main**, 59 commits after this branch was
created. The earlier audit was **not exhaustive**.

### Five active writers, not three

| Writer | Before | Now |
|---|---|---|
| `lib/edgelab/clv.py::compute_clv_for_bet` | `entry − closing` ❌ | canonical ✅ |
| `scripts/clv_from_snapshot.py::calculate_clv` | `entry − closing` ❌ | canonical ✅ |
| **`lib/research/hitter_projection_audit.py`** | `entry − closing` ❌ | canonical ✅ |
| **`lib/clv_validator.py::clv_pct`** | `entry − closing` ❌ | canonical ✅ |
| `lib/edgelab/mlb_alpha_shadow.py` | already canonical | delegates ✅ |
| `clv_update.py` (×2), `fetch_kalshi_clv_v2.py`, `clvMidPct`/`clvAskPct` | already canonical | unchanged ✅ |

The two writers in **bold** were missed by the first pass.
`lib/clv_validator.py` escaped the grep because its target is named
`clv_pct` and the pattern required no underscore — it was caught only by
the new **ast-based** repository guard. Both carried the same
self-contradicting comment ("Positive = we bought cheaper … good CLV")
above the inverted formula.

### One canonical helper

`lib/edgelab/clv_convention.py` — `CONVENTION_ID = POSITIVE_IS_GOOD_V1`,
side-aware `clv_for_yes` / `clv_for_no` / `clv_for_side`, explicit units
(`CENTS` / `PROBABILITY` / `PERCENTAGE_POINTS`) that **raise** rather than
silently mix, `convention_marker()`, and the single sanctioned
`invert_legacy_entry_minus_closing` (proven by test to have no production
caller). Midpoint is never substituted for a missing executable price.

### Canonical ledger migration

`scripts/edgelab/migrate_clv_sign.py`, writing through
`lib.edgelab.storage.upsert_records`:

| | |
|---|---:|
| Rows examined | 385 |
| **Recomputed from source** | **197** |
| Zero rows (entry == closing) | 84 |
| Unresolved (missing source fields) | 104 |
| **Discrepancies** | **0** |
| Rows written (incl. convention markers) | 281 |
| `clv` values changed | 197 |

Every changed value was an exact negation of the stored legacy value —
but it was **recomputed from `side`/`entryPrice`/`closingPrice`**, never
obtained by multiplying by −1. Only `clv`, `clvConvention` and `clvUnit`
differ; all critical wager fields (stake, prices, result, settlement,
P/L, identity, batch keys) verified byte-identical. Idempotent: a second
`--apply` writes nothing, and a no-op run now **refuses to overwrite the
receipt**, which had briefly erased the real before/after hashes.

### The legacy root ledger is deliberately NOT migrated

`bets.json` (repo root) is legacy-but-live and is what the performance and
Rule 71 reports actually read. Its 241 CLV rows carry **no**
`side`/`entryPrice`/`closingPrice`, so they are **not recomputable** — and
negating unverifiable rows is precisely what the migration policy forbids.
Both report generators therefore now stamp
`clvConvention: LEGACY_ENTRY_MINUS_CLOSING` with a note that a **negative**
value there means the bet **beat** the close.

### Tests corrected, not deleted

Seven tests encoded the refuted belief and are corrected with the evidence
cited. Several preserved the original confusion verbatim — one read
`# entered cheaper (0.45) than the 0.50 closing ask -> wait: 0.45-0.50=-5`,
and another reasoned "positive CLV = we bought cheaper = entry_implied >
closing_implied", which conflates a longer-priced bet with a cheaper one.
In probability terms the implied value **is** the price paid.

A repository-wide **ast** guard now fails the suite if any assignment into
a CLV-named target reintroduces `entry − closing`, outside the named
legacy helper and the read-only sign audit. It deliberately does not flag
spread compression (`entrySpread − closingSpread`), a different quantity.

### No policy changed

`clv_sign_supersession_manifest.json` and
`clv_sign_human_review_required.json` record what a human should re-read.
No family was reactivated or suspended, no Rule 71/81 threshold moved, no
RFI status, staking, eligibility or confidence changed, no
`config/rules.json` edit, and C01/C01-PIT are untouched.
