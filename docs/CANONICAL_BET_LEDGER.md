# Canonical Placed-Bet Ledger

Status: Phase 1 extension (Canonical Placed-Bet Ledger milestone). Builds
on `docs/EDGELAB_PHASE1.md` and `data/edgelab/schema_v1/README.md` —
read those first for the schema/ID/storage conventions this document
assumes.

This milestone does **not** place wagers, size stakes, infer that a
recommendation was actually placed, alter production recommendations, or
change staking logic. It is a recording and reporting layer only.

## 1. Canonical source of truth

**`data/edgelab/bets/bets.jsonl`** is the one authoritative,
repository-backed ledger of every wager actually placed, regardless of
which chat/session/workflow produced the recommendation. It is the
source of truth for daily postmortems, bankroll tracking, CLV,
settlement, ROI, market-family performance, model-vs-manual attribution,
EdgeLab calibration, replay linkage, and cross-chat historical lookup.

Everything else is either a **compatibility mirror** (a legacy ledger
EdgeLab ingests from, never the other way around) or a **generated
report** (derived output, safe to regenerate, never hand-edited):

| Path | Role |
|---|---|
| `data/edgelab/bets/bets.jsonl` | **Canonical.** Every write goes through `lib.edgelab.bets.write_placed_bet`. |
| `bets.json` (repo root) | Legacy, still live — written by `scripts/write_pending_bets.py` (production model bets) and `scripts/log_manual_bet.py`. EdgeLab reads it, never writes it. |
| `data/bets.json` | Legacy, still live — written by `scripts/log_session_bets.py`. EdgeLab reads it, never writes it. |
| `BET_LOG.md` | Generated Markdown mirror of `bets.json`, rebuilt by `clv_update.py`. Not touched by EdgeLab. |
| `data/edgelab/reports/<date>.md` / `.json` | Generated daily research report. |
| `data/edgelab/reports/<date>_postmortem.md` / `.json` | Generated daily postmortem (§8). |
| `data/edgelab/bets/backups/*.bak` | Local, gitignored safety copies made before a bulk reconciliation write (§10). |

Why not replace `bets.json`/`data/bets.json` outright: they are still
the live write path for the production model (`write_pending_bets.py` →
settled by `clv_update.py`) and pre-EdgeLab manual scripts. Cutting them
over is out of scope for this milestone (no production recommendation
changes) — `scripts/edgelab/ingest_existing_bets.py` reconciles them into
the canonical ledger on every postgame run instead (`entryMethod:
"LEGACY_BACKFILL"`), so nothing is lost and nothing upstream has to
change. See `docs/EDGELAB_PHASE1.md`'s "Why a new schema instead of
extending what's there" for the original rationale, unchanged by this
milestone.

## 2. Entry methods

Every canonical bet row's `entryMethod` field records which surface
wrote it:

| Value | Meaning |
|---|---|
| `MANUAL_GITHUB_FORM` | The "Record Placed Bet" GitHub Actions form (§4). |
| `MANUAL_CHAT_CONFIRMED` | `scripts/edgelab/log_bet.py`, invoked directly (its default). |
| `PRODUCTION_RECOMMENDATION_CONFIRMED` | The user explicitly confirmed placing a bet the production model recommended (a `recommendationId` is attached). |
| `LEGACY_BACKFILL` | Normalized from `bets.json`/`data/bets.json` by `ingest_existing_bets.py`. |
| `IMPORTED_RECEIPT` | Backfilled from an external receipt/confirmation rather than entered live. |

**A recommendation is never a placed bet.** `entrySource` (`MANUAL` /
`MODEL` / `OTHER`) records who identified the pick; `entryMethod` records
how it got written down. Neither is ever set unless the user has
explicitly told the system the bet was placed.

## 3. Safe write API

**One function** — `lib.edgelab.bets.write_placed_bet(record, *, path=None,
on_conflict="reject", near_duplicate_window_seconds=180)` — is the only
sanctioned way to add or correct a row in the canonical ledger. Every
entry surface (the GitHub form, `log_bet.py`, any future chat-driven
writer) calls it; no script implements its own append logic.

It:
1. Validates the record against `data/edgelab/schema_v1/placed_bet.schema.json` (missing required fields, unknown fields, bad enum values). Invalid input is never written.
2. Computes `betId = sha1(gameId, marketTicker, entryTimestamp)` (deterministic — see `lib/edgelab/ids.py`).
3. Acquires an exclusive `fcntl.flock` on `bets.jsonl.lock` (`lib.edgelab.storage.locked`) for the whole read-modify-write cycle, so two same-host writers (a background CLV job racing an interactive form submission, or two chats submitting at once) never lose an update.
4. Compares against any existing row with the same `betId`:
   - **No existing row** → insert. `duplicateStatus: "NEW"`.
   - **Existing row, identical content** (ignoring volatile `createdAt`/`updatedAt`/`provenance.ingestedAt`) → **deterministic no-op**. Nothing is written; the receipt echoes the already-stored row. Retrying an identical submission (a resent chat message, two chats independently reporting the same confirmed bet) is always safe.
   - **Existing row, different content** → **CONFLICT**, refused by default. Nothing is written. The receipt's `conflictingFields` lists exactly what differs. Resolve explicitly with `on_conflict="overwrite"` (a deliberate correction — the row's `recordStatus` becomes `"CORRECTED"`, never silent).
5. Writes atomically (temp file + `fsync` + `os.replace`) — a corrupt or partial ledger file is never observable.

A genuine **second tranche** (same ticker, a real second fill) is never
confused with a duplicate: its `entryTimestamp` differs, so it derives a
different `betId` and is always inserted as `"NEW"`. If it lands within
180 seconds (configurable) of another bet on the same ticker+side, the
receipt carries an informational `nearDuplicateWarnings` list — **this
never blocks the write**, it only flags it for a human to glance at.

## 4. GitHub Actions entry form

`.github/workflows/record-placed-bet.yml` ("Record Placed Bet"),
`workflow_dispatch` only. `workflow_dispatch` caps at 10 top-level
inputs, so the form has exactly 10:

`game_date`, `market_ticker`, `selection`, `side`, `stake`,
`entry_price`, `placed_at`, `recommendation_id`, `notes`, `advanced_json`.

Less-common fields (event/series ticker, market family/horizon,
threshold, `gameId`, `matchup`, `entryOdds`, `snapshotId`,
`productionRunId`, model fields, correlation groups, thesis tags,
`trackingType`, an explicit `onConflict` override) go in `advanced_json`,
a JSON object string — see
`scripts/edgelab/record_bet_from_workflow.py`'s docstring for the exact
key list.

The workflow:
1. Runs `scripts/edgelab/record_bet_from_workflow.py`, which builds the record and calls `write_placed_bet` — the identical function `log_bet.py` uses.
2. Regenerates that date's daily report (`generate_daily_report.py`).
3. Uploads the JSON receipt as a workflow artifact (`placed-bet-receipt-<run_id>`, 90-day retention).
4. Writes the receipt into the job summary (`$GITHUB_STEP_SUMMARY`).
5. Commits and pushes `data/edgelab/bets/bets.jsonl` + that date's reports directly to `main`, guarded by `git diff --cached --quiet` (matches every other EdgeLab workflow's convention) and a rebase-retry loop on push conflict.
6. Fails the job (non-zero exit) if the bet was not saved, so a CONFLICT/INVALID never looks like success in the Actions UI.

It has **no Kalshi (or any) order-placement capability at all** — no HTTP
client, no API credentials, nothing. It only records a wager the user
says they already placed elsewhere. See
`tests/edgelab/test_no_automatic_wagering.py`, a grep-verified guardrail
that fails loudly if that ever changes.

## 5. Chat-ready entry contract

### Human format (what a chat produces)

```
RECORD BET
Game: Yankees at Red Sox
Market: Yankees F5 winner
Ticker: KXMLBF5-...
Side: YES
Stake: $25
Entry: 54¢
Placed at: 2026-08-03T13:04:00-05:00
Source: Manual recommendation
Notes: Confirmed lineup
```

### Machine-readable JSON (what actually gets written)

```json
{
  "marketTicker": "KXMLBF5-26AUG031804NYYBOS-NYY",
  "selection": "NYY F5 winner",
  "side": "YES",
  "stake": 25,
  "entryPrice": 0.54,
  "entryTimestamp": "2026-08-03T18:04:00Z",
  "gameDate": "2026-08-03",
  "matchup": "NYY @ BOS",
  "source": "MANUAL",
  "entryMethod": "MANUAL_CHAT_CONFIRMED",
  "recommendationId": null,
  "rationale": "Confirmed lineup"
}
```

This maps 1:1 onto `lib.edgelab.bets.build_manual_bet_record`'s keyword
arguments (snake_case) and is exactly the shape
`scripts/edgelab/log_bet.py --ticker ... --selection ...` or
`record_bet_from_workflow.py`'s `ADVANCED_JSON` accept.

### Validation requirements

Required: `marketTicker`, `selection`, `stake` (dollars, > 0),
`entryPrice` (0–1 implied probability — **not** cents; 54¢ → `0.54`),
`entryTimestamp` (ISO 8601 UTC). Everything else is optional and is
**never fabricated** — a manual bet with no model evaluation gets
`modelSupported: null`, `modelFairProbability: null`, not a guessed
value.

### Receipt format (returned after saving)

```json
{
  "success": true,
  "betId": "…sha1…",
  "market": {"marketTicker": "…", "selection": "…", "side": "YES"},
  "stake": 25.0,
  "entryPrice": 0.54,
  "potentialGrossReturn": 46.3,
  "timestamp": "2026-08-03T18:04:00Z",
  "linkageStatus": "UNLINKED",
  "linkedEntities": [],
  "duplicateStatus": "NEW",
  "settlementStatus": "pending",
  "clvStatus": "UNAVAILABLE",
  "errors": [],
  "conflictingFields": [],
  "nearDuplicateWarnings": [],
  "generatedAt": "2026-08-03T18:04:05Z"
}
```

`duplicateStatus` is one of `NEW`, `DUPLICATE_NOOP`, `CORRECTED`,
`CONFLICT`, `INVALID`. Only the first three mean something was actually
written (or already was). **A chat must never claim a bet is saved
without this receipt existing and `success: true`** — ordinary
conversation text is not a repository write. The long-term goal is for a
GitHub-capable chat/tool to invoke `write_placed_bet` (or
`log_bet.py`/the GitHub form) directly and relay the real receipt back,
never to fabricate one.

## 6. Cross-chat read path

`lib.edgelab.query` (pure functions over an already-loaded bet list) and
`scripts/edgelab/query_bets.py` (CLI wrapper, `--format json|human`):

```
python3 scripts/edgelab/query_bets.py --filter today [--date YYYY-MM-DD]
python3 scripts/edgelab/query_bets.py --filter unsettled
python3 scripts/edgelab/query_bets.py --filter settled
python3 scripts/edgelab/query_bets.py --filter date --date YYYY-MM-DD
python3 scripts/edgelab/query_bets.py --filter date-range --start ... --end ...
python3 scripts/edgelab/query_bets.py --filter market-family --market-family FAMILY_INNING_RESULT
python3 scripts/edgelab/query_bets.py --filter game --game-id ...
python3 scripts/edgelab/query_bets.py --filter snapshot [--snapshot-id ...]
python3 scripts/edgelab/query_bets.py --filter recommendation [--recommendation-id ...]
python3 scripts/edgelab/query_bets.py --filter manual-no-model
python3 scripts/edgelab/query_bets.py --filter bankroll-history
```

Read-only by construction — nothing in `lib.edgelab.query` ever calls a
write function.

## 7. Cross-chat operating protocol

Before answering a question about actual wagers, a project chat/tool
must **read the canonical ledger** (`query_bets.py` or
`lib.edgelab.query` directly) — never rely on conversation memory or the
recommendation list.

When the user confirms a new wager, a chat should:
1. Produce the structured JSON entry (§5).
2. Execute or instruct the real write (`log_bet.py`, the GitHub form, or `write_placed_bet` directly).
3. Return the real receipt (§5) — **never claim "saved" until the repository confirms it.**

When doing a postmortem, use `scripts/edgelab/generate_postmortem.py`
(§8) — built exclusively from the canonical ledger, never from chat
memory or the recommendation list.

Always distinguish, in both directions of the conversation:

| State | Meaning |
|---|---|
| Discussed bet | Mentioned in conversation. No ledger row. |
| Recommended bet | The model or a manual analysis suggested it. Still no ledger row — see `Recommendation.status`. |
| User-confirmed bet | The user said "place/log this." A write was attempted. |
| Repository-saved bet | `write_placed_bet` returned `success: true`. Now in `bets.jsonl`. |
| Settled bet | `status: "settled"` with a real `result` — evidence-based, via `scripts/edgelab/settle_markets.py`. |

## 8. Daily postmortem & receipt semantics

`scripts/edgelab/generate_postmortem.py [--date YYYY-MM-DD]` builds
`data/edgelab/reports/<date>_postmortem.{json,md}` via
`lib.edgelab.reports.build_postmortem` — **exclusively from
`bets.jsonl`**, filtered to that date's real (`trackingType` REAL or
unset), non-`CANCELLED` bets. Never substitutes a recommendation for a
placed bet.

Includes: every bet placed that day (stake, entry price, result, gross
return, net P/L), daily win/loss/push/void/pending record, total risked
(all vs. settled-only), total returned, net P/L, ROI%, average CLV,
performance by market family, model-supported vs. manual, recommended
vs. non-recommended, snapshot/replay-linkage counts, and the list of
still-unresolved bet IDs. No win/loss is ever computed before settlement
evidence exists — a pending bet's `result`/`grossReturn` stay `null`.

A per-bet **receipt** (§5) is generated on every save, and a **daily
card** — every bet placed today plus total risked, broken out by market
family — is available via `query_bets.py --filter today`.

## 9. Bankroll semantics

`lib.edgelab.bankroll` (schema: `data/edgelab/schema_v1/bankroll_transaction.schema.json`,
ledger: `data/edgelab/bankroll/transactions.jsonl`). **Tracking only —
never feeds stake sizing.** `scripts/risk_gate.py` is deliberately
untouched; `tests/test_risk_gate_rule71_81_bankroll_absence.py` guards
that it stays bankroll-free.

Only five transaction types are ever written directly: `STARTING_BALANCE`,
`DEPOSIT`, `WITHDRAWAL`, `ADJUSTMENT` (requires a `reason`), and
`USER_REPORTED_BALANCE`. Stake reservation, stake return, and realized
P&L are **never stored as separate transactions** — they're computed
live from `bets.jsonl` by `compute_bankroll_summary`, so the two ledgers
can't drift apart.

Four distinct numbers:

| Field | Meaning |
|---|---|
| `settledBankroll` | Cash transactions + realized P/L from every *settled*, real-tracked bet. |
| `totalExposure` | Stake currently in *pending*, real-tracked bets. Not a loss — just unavailable until settlement. |
| `availableBankroll` | `settledBankroll - totalExposure`. |
| `userReportedBalance` | The latest manually-entered `USER_REPORTED_BALANCE`, verbatim, plus its delta against `settledBankroll` — informational reconciliation only, **never substituted into any calculation.** |

`PAPER`/`REAL_PROBE` bets never affect any of these totals. Record a
transaction with `scripts/edgelab/record_bankroll_transaction.py`.

## 10. CLV and settlement linkage

Unchanged mechanics from `docs/EDGELAB_PHASE1.md` (`lib/edgelab/clv.py`,
`lib/edgelab/settlement.py`) — this milestone verified and test-locked
them against its own requirements rather than reimplementing:

- Matching key is the exact `marketTicker`; closing quote = the final valid tradable quote strictly before actual/scheduled start (never a post-start or midpoint quote unless explicitly the executable ask, per `_executable_closing_implied`).
- Multiple tranches on one ticker each get their own CLV computed from their own `entryPrice`, all referencing the same `clvQuoteId` (`tests/edgelab/test_clv.py::test_multiple_tranches_on_one_ticker_each_get_own_clv_from_the_shared_closing_quote`).
- Unresolved linkage is explicit (`clvStatus: "UNAVAILABLE"` with a reason), never a fabricated number.
- Settlement evidence is family-specific (full game = final score, F3/F5/F7 = period score, first-inning-run = 1st-inning score; player props are explicitly `SETTLEMENT_UNRESOLVED` — not yet implemented, never guessed). YES/NO resolves per-ticker, mapped to the bet's own `side`.
- Every tranche on a settled ticker settles independently (`settle_bets_for_ticker`); a VOID/unresolved market leaves every bet on it untouched.

## 11. Duplicate/tranche behavior (summary)

| Scenario | `duplicateStatus` | Written? |
|---|---|---|
| New ticker+timestamp | `NEW` | Yes |
| Identical resend (same ticker+timestamp+content) | `DUPLICATE_NOOP` | No (already there) |
| Same ticker+timestamp, different content | `CONFLICT` | No, unless `on_conflict="overwrite"` (→ `CORRECTED`) |
| Same ticker, different timestamp (real tranche) | `NEW` | Yes, always — possibly with a `nearDuplicateWarnings` hint if very close in time |
| Schema-invalid input | `INVALID` | No |

## 12. Historical reconciliation

`scripts/edgelab/reconcile_bet_history.py` — **read-only report**, never
writes. Reports: total unique historical bets, exact duplicate rows
(same normalized `betId`), probable duplicates needing human review
(same ticker+side within 5 minutes, differing content), bets missing
ticker/entry price/stake, legitimate multi-tranche groups, and canonical-
ledger counts (model-linked, manual-only, settled/unsettled/void).

`scripts/edgelab/ingest_existing_bets.py` does the actual backfill write
(idempotent — safe to rerun). `--dry-run` computes and prints what would
change without touching the ledger. A real (non-dry-run) run that will
touch existing rows automatically backs up the current
`bets.jsonl` to `data/edgelab/bets/backups/<name>.<UTC timestamp>.bak`
first (gitignored, local-only) before writing.

**Limitations, by design, not fixed by this milestone:** 505 of 613 raw
legacy rows have no resolvable `marketTicker` and are never carried into
the canonical ledger (a known pre-EdgeLab gap — see
`docs/EDGELAB_PHASE1.md`); rows missing `entryPrice` are ingested with
`entryPrice: null` rather than a guessed value; `bets.json`/`data/bets.json`
are never deleted or rewritten by this milestone.

## 13. Concurrency & workflow safety

`lib.edgelab.storage.locked(path)` (`fcntl.flock` on a sidecar
`<path>.lock`) serializes the read-modify-write cycle for any two
same-host writers on the same file — closes the race a plain
read-then-`os.replace` would otherwise have. This is a same-host
guarantee; across separate GitHub Actions runners, safety still comes
from each workflow's own `concurrency:` group (every EdgeLab workflow,
including this one, sets `cancel-in-progress: false` so a second run
queues rather than racing) and from git itself as the eventual merge
point (rebase-retry-push loop on conflict, matching every other EdgeLab
workflow).

## 14. Recovery procedures

- **Restore from a backup**: `cp data/edgelab/bets/backups/bets.jsonl.<timestamp>.bak data/edgelab/bets/bets.jsonl` (backups are created automatically before any reconciliation write that would change existing rows — see §12).
- **A stale `.lock` file**: `fcntl.flock` locks are released automatically when the holding process exits (including a crash) — a leftover `bets.jsonl.lock` file itself is harmless and is gitignored; delete it only if you suspect a genuinely wedged process still holding it (`lsof <path>.lock`).
- **A bad correction**: `on_conflict="overwrite"` sets `recordStatus: "CORRECTED"` but never deletes the prior content outside of git history — `git log -p -- data/edgelab/bets/bets.jsonl` recovers the pre-correction row.
- **Re-deriving the whole ledger from scratch**: `scripts/edgelab/ingest_existing_bets.py` is fully idempotent against the legacy files; it is safe to delete `data/edgelab/bets/bets.jsonl` and rerun it to rebuild everything with `entryMethod: "LEGACY_BACKFILL"`, though any bet logged directly against the canonical schema (not present in a legacy file) would be lost — always prefer restoring from git history or a backup first.

## 15. Daily user workflow

1. **Place a bet** on Kalshi as usual (this system never does it for you).
2. **Record it**: run the "Record Placed Bet" GitHub Actions form, or tell a project chat in the compact format (§5) and have it invoke `log_bet.py`/`write_placed_bet` directly. Get back a receipt with `success: true`.
3. **Check today's card**: `query_bets.py --filter today` — every bet placed, total risked.
4. **After games finish**: the existing `edgelab-clv-collect.yml` / `edgelab-postgame.yml` workflows compute CLV and settle markets automatically (unchanged by this milestone).
5. **Read the postmortem**: `data/edgelab/reports/<date>_postmortem.md`, or run `generate_postmortem.py` manually.
6. **Check bankroll**: `record_bankroll_transaction.py` for deposits/withdrawals, `query_bets.py --filter bankroll-history` for the running summary.
