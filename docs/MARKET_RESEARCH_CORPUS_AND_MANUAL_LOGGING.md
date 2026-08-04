# MLB Market Research Corpus & Frictionless Manual Logging

Status: builds directly on `docs/EDGELAB_PHASE1.md`, `docs/EDGELAB_PHASE2_DESIGN.md`,
`docs/CANONICAL_BET_LEDGER.md`, and `data/edgelab/schema_v1/README.md` —
read those first. This document covers what this milestone adds on top
of that existing infrastructure: it does **not** re-describe collection/
linkage machinery that already existed (full-universe market observation
capture, settlement-attempted-for-every-observed-market, the canonical
placed-bet writer) except where this milestone changes or extends it.

**Important scoping note**: after this milestone, every recognized MLB
Kalshi market is **observable and queryable** — it is captured, archived,
and reachable through the research query surface (§10) regardless of
whether anyone ever bet it. That is *not* the same claim as "every
market's outcome is settled." Settlement is *attempted* for every
observed market, but pitcher/hitter player-prop families (strikeouts,
outs, hits, total bases, etc.) have no automatic outcome-settlement
implementation at all yet and are recorded `SETTLEMENT_UNRESOLVED` —
this is a pre-existing gap this milestone does not close (§5, §14). A
follow-up issue tracks closing it.

This milestone does **not**: place wagers, auto-size stakes, change
production probabilities/thresholds/risk-gate behavior/stake sizing,
treat manual analysis as production-model output, feed settlement data
into pregame model calculations, or modify the replay engine
(`lib/edgelab/replay.py`, `scripts/run_replay.py`) in any way.

---

## 1. What already existed vs. what this milestone adds

Before this milestone, EdgeLab (Phase 1/2) already had:

- Automatic, slate-independent raw Kalshi capture (`capture-snapshots-scheduled.yml`,
  every 30 min, `data/kalshi_registry_snapshots/`).
- Normalization of that capture into `MarketObservation`/`Market`/`Game`
  records (`edgelab-capture.yml` → `scripts/edgelab/ingest_market_observations.py`
  → `lib/edgelab/market_universe.py`), for every confirmed single-game MLB
  Kalshi market family.
- Settlement *attempted* for **every observed market**, not only placed/
  recommended ones (`scripts/edgelab/settle_markets.py` →
  `lib/edgelab/settlement.py`) — though pitcher/hitter player-prop
  families have no settlement implementation at all and are always
  recorded `SETTLEMENT_UNRESOLVED` (pre-existing gap, see §5).
- The canonical placed-bet ledger and its one writer
  (`lib/edgelab/bets.py:write_placed_bet`), with duplicate/conflict/
  tranche detection, and a cross-chat read-only query layer
  (`lib/edgelab/query.py`).

This milestone adds:

1. **Corpus completeness and growth control** (`lib/edgelab/market_universe.py`,
   `scripts/edgelab/ingest_market_observations.py`): checkpoint
   classification and pregame-validity flags are now actually wired up
   (previously hardcoded `null`); a brand-new, not-yet-allowlisted
   KXMLB*-prefixed series is now archived into the corpus
   (`registryClassificationStatus="UNCLASSIFIED_MLB"`) instead of being
   silently dropped; a retention filter keeps the corpus from growing
   without bound while never discarding a real price/status change (§3).
2. **The standalone price-check tool also archives** an unfiltered
   capture on every successful run (§4).
3. **Settlement now records `wasRecommended`/`wasPlaced`** so "observed
   but never bet" research is a single-field filter, not a cross-ledger
   join (§5).
4. **Timestamp-optional manual bet imports** (§6): `entryTimestamp` is
   now optional everywhere; identity no longer depends solely on
   `marketTicker + entryTimestamp`.
5. **A bulk import surface** (`scripts/edgelab/import_bet_batch.py`) that
   accepts a JSON payload of multiple wagers, resolves an unspecified
   ticker against the archived corpus, and writes every row through the
   same canonical writer (§7).
6. **Automatic bet-to-observation linkage** (`lib/edgelab/observation_linkage.py`)
   (§8).
7. **Structured postmortem ingestion** (`lib/edgelab/postmortems.py`,
   `scripts/edgelab/import_postmortem.py`) (§9).
8. **A broader read-only research query surface**
   (`scripts/edgelab/query_research.py`) (§10).

---

## 2. Daily operational workflow

1. GitHub automatically archives the complete Kalshi MLB market universe
   throughout the day (`capture-snapshots-scheduled.yml` → `edgelab-capture.yml`,
   unchanged cadence/trigger from before this milestone).
2. The user may still run **Kalshi Price Check (Standalone)** any time
   for manual analysis — its display/filtering behavior is unchanged,
   and it now *also* archives an unfiltered capture into the same corpus
   (§4).
3. The user discusses the markets in ChatGPT and places wagers.
4. The user later supplies only a normal bet list (e.g. `SF F5, $12 at
   +128`) to Claude. **No exact placement timestamp is required.** Claude
   translates that into the small JSON payload
   `scripts/edgelab/import_bet_batch.py` / the "Import Manual Bet Batch"
   GitHub Action expects (§7) and runs it.
5. Each manual wager is automatically linked to the most relevant
   archived pregame market observation (§8) — never claimed as the
   actual placement time.
6. After games finish, `scripts/edgelab/settle_markets.py` attempts
   settlement for every archived market observation, not only placed/
   recommended ones (pre-existing behavior, now also recording
   `wasRecommended`/`wasPlaced`, §5) — every market is observable and
   queryable either way, but player-prop families remain
   `SETTLEMENT_UNRESOLVED` until the follow-up settlement work lands
   (§14).
7. After the betting day, ChatGPT supplies a Claude handoff prompt
   containing the finished postmortem (Markdown) and structured
   findings. Claude runs `scripts/edgelab/import_postmortem.py` / the
   "Import Daily Postmortem" GitHub Action once (§9) to save it, linked
   to the real canonical `betId`s.

---

## 3. Market corpus: completeness, checkpoints, and growth control

### 3.1 Storage (unchanged location, extended content)

Still `data/edgelab/observations/<date>.jsonl.gz` (+ `games/<date>.jsonl`,
`markets/<date>.jsonl`, `research_runs/<date>.jsonl` as the per-run
manifest) — this milestone did **not** introduce a parallel
`market_corpus/` directory tree, since the existing partition-by-date
JSONL(.gz) structure already satisfies every requirement (append-only,
idempotent, deterministic IDs, concurrency-safe via `lib/edgelab/storage.py`'s
`locked()`, queryable by date/game/ticker/family/capture-time). Each
`research_runs/<date>.jsonl` row already **is** the capture manifest:
`runId`, `inputFiles` (the raw snapshot paths consumed), `outputFiles`,
and `counts` (see §3.3).

### 3.2 New fields on `MarketObservation` (additive, backward-compatible)

| Field | Meaning |
|---|---|
| `gameStartedAtCapture` | `true`/`false`/`null`. Derived from `checkpoint == POST_START`; `null` only when `scheduledStart` isn't known yet. |
| `isValidPregameObservation` | `true` only for a tradable, pre-first-pitch quote — the population eligible for closing-candidate/bet-linkage selection. |
| `registryClassificationStatus` | `CLASSIFIED` (confirmed single-game-MLB allowlist) or `UNCLASSIFIED_MLB` (a KXMLB*-prefixed series Kalshi returned that isn't allowlisted yet, but also isn't a recognized non-game pattern — archived for research, but **never** fed into production selection; `classify_series_for_price_check` is completely unchanged). |
| `githubRunId` / `commitSha` | Audit trail for which workflow run / commit captured this row. |
| `checkpoint` / `isClosingCandidate` | Already existed in the schema, but were hardcoded `null` before this milestone — now actually computed by `lib/edgelab/checkpoints.py`. |

A record written before this milestone simply has these fields `null` —
per `data/edgelab/schema_v1/README.md`'s versioning policy, that is
never an error.

### 3.3 Growth control (`lib.edgelab.market_universe.select_observations_for_retention`)

Every 30-minute capture tick produces one candidate observation per
observed market (~2,700+/day). Committing every tick unconditionally
would make git-committed volume grow without bound. The retention filter
always keeps:

- the first observation of a ticker each day (`FIRST_DAILY`),
- a named pregame-distance checkpoint (`T_MINUS_90/60/30/15/5`, `LINEUP_CONFIRMATION`),
- the specific tick where a market's `gameStartedAtCapture` first flips to `true`,
- any tick whose `yesBid`/`yesAsk`/`noBid`/`noAsk`/`lastPrice`/`marketStatus`
  differ from the last **retained** observation for that same ticker.

It only drops a plain repeat tick (no target checkpoint, no change on
any of those fields) — a real price or status change is never silently
discarded. `research_runs/<date>.jsonl`'s `counts.observationsBuilt` vs.
`counts.observationsRetained` makes the effect of this filter visible on
every run.

---

## 4. Standalone price-check tool: unchanged display, new archival

`.github/workflows/kalshi-price-check.yml`'s existing display/filtering
behavior, inputs, and non-commit of its own `kalshi_price_check.json/.csv`
output are **completely unchanged**. Two new steps run at the end of a
successful invocation, regardless of the user's display filters:

1. An independent live fetch of the same `/api/kalshisearch` endpoint
   (identical to `capture-snapshots-scheduled.yml`'s own step — this does
   **not** reuse anything from `lib/kalshi_price_check.py`, so that
   tool's pure/network-isolated core is unaffected).
2. `scripts/edgelab/ingest_market_observations.py --source-system standalone_price_check`
   folds that capture into the same corpus, tagged with
   `source: standalone_price_check` so `lib/edgelab/observation_linkage.py`
   can prefer it when linking a manual bet (§8).

Only `data/kalshi_registry_snapshots/` and `data/edgelab/` are ever
committed by this workflow — never `bets.json`, `data/slate.json`, or any
other production file. `permissions.contents` had to widen from `read`
to `write` for this (see `tests/test_kalshi_price_check_workflow.py`'s
`test_commit_steps_only_touch_the_research_corpus` for the enforced
scope).

---

## 5. Settlement: `wasRecommended` / `wasPlaced`, and the player-prop gap

`Settlement` (settlement *attempted* for every observed market, not only
placed/recommended ones — unchanged from before this milestone) now also
carries:

- `wasRecommended`: true if any `Recommendation` row for that ticker ever
  reached `WATCH`/`RECOMMENDED`/`RECOMMENDED_NOT_BET`/`BET_PLACED`
  (`lib.edgelab.settlement.was_market_ever_recommended`).
- `wasPlaced`: true if a real (non-`CANCELLED`) bet exists on that ticker.

This makes "markets observed but never recommended," "recommended but
not placed," and "performance by family across *every* observed market
that has an actual settled outcome" (§10) single-pass queries instead of
a cross-ledger join every time.

**What this does NOT mean**: pitcher/hitter player-prop families
(`pitcher_strikeouts`, `pitcher_outs`, `hitter_hits`, `hitter_total_bases`,
and every other currently-captured pitcher/hitter family) have **no
automatic outcome-settlement implementation at all** — this was true
before this milestone and remains true after it
(`lib.edgelab.settlement._PLAYER_PROP_FAMILIES`, always
`SETTLEMENT_UNRESOLVED` with
`unavailableReason: "player_prop_settlement_not_implemented"`). This
milestone captures, archives, and makes those markets fully observable
and queryable (§3, §10) — it does **not** make them outcome-settled. A
scoped follow-up issue tracks closing this gap; see §14.

---

## 6. Timestamp-optional manual bet imports

### 6.1 Schema (additive; nothing existing breaks)

`PlacedBet.entryTimestamp` is now `["string", "null"]` (was required).
New fields, all optional/nullable so every historical row remains valid:

| Field | Meaning |
|---|---|
| `recordedAt` | Automatically generated UTC logging timestamp — set on every write going forward. **Never** represented as the placement time. |
| `timestampStatus` | `PROVIDED` \| `NOT_PROVIDED` \| `APPROXIMATE`. `null` on a pre-milestone row means `PROVIDED` (it necessarily had a real timestamp back when the field was required). |
| `importBatchId` / `sourceBetKey` | Identity components for a timestamp-free bet (see §6.2). Both are **explicit, caller-assigned** values. |
| `sourceRow` | The row's 0-based position within its payload — display/audit only, **never** part of identity. |
| `marketObservationLinkage` | See §8. |

### 6.2 Identity, without depending solely on `marketTicker + entryTimestamp`

`lib/edgelab/ids.py:build_bet_id`:

- `entryTimestamp` known → **unchanged**: `sha1(gameId, marketTicker, entryTimestamp)`.
- `entryTimestamp` unknown → `sha1('bet_import', importBatchId, sourceBetKey, marketTicker, side)`.
- Neither → the pre-existing ULID-style fallback token (unchanged, rare).

**Both `importBatchId` and `sourceBetKey` must be explicit, caller-assigned
values** — generated by the calling client (Claude, during the ChatGPT
handoff) as part of building the JSON payload; the end user never sees
or tracks either one manually. An earlier design instead defaulted
`importBatchId` to a hash of the payload's own `gameDate`(s) and used
each row's list index as `sourceRow`/its discriminator — both were found
(maintainer review) to be unsafe:

- A `gameDate`-derived batch id **collides across two genuinely separate
  same-day sessions** (e.g. a morning and an evening round of bets) —
  both hash to the identical batch id, so same-positioned rows in each
  session would silently collapse into the same bet.
- A list-index row key is **not stable under reordering or insertion** —
  the same logical row gets a different identity purely because
  something else moved in the list, or a new row was inserted earlier.

`sourceBetKey` fixes this by traveling *with* its row rather than its
position (e.g. `"bet-01"`), so:
- Reordering the payload, or inserting a new row anywhere in it, never
  changes an existing row's identity.
- Two intentional same-ticker/same-side tranches are distinguished by
  giving them distinct `sourceBetKey` values.
- Two genuinely separate same-day sessions are distinguished by giving
  them distinct `importBatchId` values.
- Re-running the identical batch (same `importBatchId` + `sourceBetKey`
  per row) is still a pure no-op.

`sourceRow` (the row's list position) is still accepted and stored, but
purely for display/audit — it is excluded from both `betId`'s identity
computation and from the content-fingerprint comparison
`write_placed_bet` uses to detect a genuine change, specifically so a
row's position shifting (from reordering or insertion) is never mistaken
for a content conflict.

`lib/edgelab/bets.py:build_manual_bet_record` refuses (raises
`ValueError`) a caller that passes `entryTimestamp=None` without both
`import_batch_id` and `source_bet_key` — a timestamp-free bet must always
have a stable, deterministic, explicit identity; it is never allowed to
fall back to a shared/derived field or to the non-reproducible ULID
token.

### 6.3 The canonical writer is still the only writer

`write_placed_bet` (`lib/edgelab/bets.py`) is completely unchanged in its
duplicate/conflict/tranche semantics — a timestamp-free bet goes through
the exact same function, same receipt shape, same locking. No script
appends to `bets.jsonl` directly.

---

## 7. Bulk import surface

`scripts/edgelab/import_bet_batch.py` (also reachable as the "Import
Manual Bet Batch" GitHub Action) accepts a JSON file or inline JSON
payload: either a bare array of rows, or `{"importBatchId": "...", "rows": [...]}`.
A normal row only needs `sourceBetKey` (required whenever the row omits
`entryTimestamp` — see §6.2), `gameDate`, a way to identify the game
(`away`/`home` or `matchup`), enough market description to resolve or an
exact `marketTicker`, `stake`, and `entryPrice` or `entryOdds`. See the
script's module docstring for the full row shape. `importBatchId` and
every row's `sourceBetKey` are meant to be generated by the calling
client (Claude) during the handoff — a timestamp-free row missing either
is refused with a clear unresolved receipt, never silently defaulted
from `gameDate` or the row's position (see §6.2's identity design).

**Ticker resolution** (`lib/edgelab/ticker_resolution.py`), only when
`marketTicker` isn't already supplied: matches the archived
`Game`/`Market` dimension tables on game + family + horizon + team/
player + threshold. **Refuses ambiguous matches** — that row is returned
as an explicit `UNRESOLVED` receipt (never written, never guessed), and
the receipt lists every candidate ticker found so the caller can
disambiguate (usually by supplying `threshold` or `marketTicker`
directly).

Every row, resolved or not, gets a real receipt:
`success`, `duplicateStatus` (`NEW`/`DUPLICATE_NOOP`/`CORRECTED`/`CONFLICT`/`INVALID`/`UNRESOLVED`),
`betId`, `marketTicker`, `stake`, `entryPrice`, `timestampStatus`,
`linkageStatus`, `errors`/`ambiguityCandidates`. A batch with some
resolvable and some ambiguous rows writes the resolvable ones and
reports the rest — never all-or-nothing silently.

---

## 8. Bet-to-observation linkage

`lib/edgelab/observation_linkage.py`. For each manually imported wager,
once its exact `marketTicker` is known (resolved or supplied), this finds
the best archived **pregame** `MarketObservation` for that exact ticker:

1. Only observations for the **exact** ticker are ever candidates.
2. Only **valid pregame** observations qualify (`isValidPregameObservation == true`)
   — a post-start observation is never selected when any valid pregame
   observation exists; if none exists at all, the bet is saved
   **unlinked**, never guessed.
3. Among valid pregame candidates, the **latest** one wins (closest to
   the actual decision moment); a manually-triggered standalone
   price-check capture (`source == standalone_price_check`, §4) is
   preferred over an automated one at the same `capturedAt`, since that
   is the capture most likely to be the exact quote the user looked at.

Stored on `PlacedBet.marketObservationLinkage`: `observationId`,
`marketCorpusRunId`, `observedAt`, `observedPrice`, `linkageMethod`,
`linkageStatus` (`LINKED`/`UNLINKED`), `linkageConfidence`,
`unavailableReason`. **`observedAt` is never represented as the
placement time** — `entryTimestamp`/`recordedAt` remain the only fields
with that meaning, and the user's own reported `entryPrice` is always
preserved separately from `observedPrice`.

---

## 9. Structured postmortem ingestion

`lib/edgelab/postmortems.py` + `scripts/edgelab/import_postmortem.py`
(also the "Import Daily Postmortem" GitHub Action). Distinct from the
pre-existing `scripts/edgelab/generate_postmortem.py` (pure arithmetic
computed **from** the ledger): this is the qualitative narrative
(analytical wins/misses, process errors, proposed investigations)
supplied **by** the user/ChatGPT, stored under
`data/edgelab/postmortems/<gameDate>/`:

| File | Contents |
|---|---|
| `postmortem.json` | Current (`ACTIVE`) revision — schema `postmortem.schema.json`. |
| `postmortem.md` | Current revision's Markdown narrative. |
| `revisions.jsonl` | Every **superseded** revision, append-only. |
| `bet_linkage.json` | Resolved/unresolved bet references, snapshotted. |
| `import_receipts.json` | One receipt per import attempt, append-only. |

Key guarantees:

- **Never substitutes a recommendation for a missing placed bet.**
  `linkedBetIds` only ever contains betIds that genuinely exist in
  `bets.jsonl` at import time; anything else is kept in
  `unresolvedBetReferences`, never silently dropped or guessed.
- **`canonicalTotals` is always independently recomputed** from the
  actual linked bets (`lib.edgelab.postmortems.compute_canonical_totals`,
  the same arithmetic `lib.edgelab.reports.build_postmortem` uses) —
  never taken from the caller's `reportedTotals` claim. `totalsMatch`
  surfaces any discrepancy rather than silently reconciling it either
  direction.
- **Idempotent**: an identical resubmission is a no-op
  (`duplicateStatus: DUPLICATE_NOOP`). A genuinely different
  resubmission becomes a new **revision** of the same `postmortemId`
  (`duplicateStatus: CORRECTED`) — the prior revision is preserved in
  `revisions.jsonl`, never silently overwritten.
- Regenerates `generate_daily_report.py` / `generate_postmortem.py`'s
  outputs for the date after a successful import (best-effort;
  regeneration failure doesn't undo the import).

---

## 10. Research query surface

`scripts/edgelab/query_research.py` — **read-only by construction** (it
only ever calls `lib.edgelab.storage.read_records`; never a write
function; enforced by a static grep test). Subcommands answer every
question in the milestone's Part 6 list: `observed-markets`,
`alternate-thresholds`, `pitcher-strikeout-closings`,
`checkpoint-comparison`, `observed-never-recommended`,
`recommended-not-placed`, `manual-bets-without-slate`,
`performance-by-family`, `capture-for-bet`, `postmortem-for-date`,
`postmortem-for-bet` — each backed by a pure function in
`lib/edgelab/query.py` so any script/chat/test can call it directly
without shelling out.

---

## 11. Distinguishing states (unchanged, restated for this milestone)

This milestone preserves the repository's existing distinction between:
**market observation** (a price point, no judgment) → **model
evaluation**/**recommendation** (the production decision layer, entirely
unmodified by this milestone) → **manual analysis** (a human/ChatGPT
judgment, never conflated with model output — `modelSupported=True`
still requires a real `modelEvaluationId`, enforced since before this
milestone) → **user-confirmed placed bet** (the one canonical ledger) →
**settlement** (an objective outcome fact) → **postmortem** (a narrative
reflection, linked to but never substituted for the ledger).

**Full deterministic replay** (`lib/edgelab/replay.py`) applies only to
production model decisions and is unmodified by this milestone. Manual
chat reasoning has no replay equivalent — it is evaluated through market
observations (what price was actually available), placed bets (what was
actually risked), and postmortems (what the analysis concluded), never
through a deterministic re-simulation of the chat itself.

---

## 12. Recovery and rerun procedures

- **Missed a scheduled capture tick**: no action needed — the next tick
  self-corrects; `edgelab-capture.yml` also supports `workflow_dispatch`
  with `--all-snapshots` to backfill every snapshot file for a date.
- **A bulk bet import partially failed**: fix the ambiguous/invalid rows
  (usually by adding `marketTicker` or `threshold`) and resubmit the
  *same* file — already-written rows resolve as `DUPLICATE_NOOP`, only
  the previously-failed rows write.
- **A postmortem needs correction**: resubmit with the corrected
  `findings`/Markdown and the same `--date` — this creates a new
  revision automatically; nothing needs to be deleted first.
- **A standalone price-check archive step fails** (e.g. transient fetch
  failure): the price-check display/artifacts the user actually asked
  for are unaffected (`if: always()` guards mean the archive steps never
  fail the job); the next scheduled capture or standalone run picks up
  the corpus again.

---

## 13. Repository growth considerations

- The retention filter (§3.3) is the primary growth control for
  `observations/<date>.jsonl.gz` — without it, every 30-minute tick would
  add ~2,700 rows/day even with zero price movement; with it, only
  named checkpoints and genuine changes add rows.
- `MarketObservation` remains the one entity stored gzip-compressed
  (~20x smaller) per `docs/EDGELAB_PHASE1.md`'s existing storage-growth
  analysis — unchanged by this milestone.
- Postmortems are one small JSON+Markdown pair per betting day, plus a
  `revisions.jsonl` that only grows on an actual correction (rare, by
  construction) — negligible compared to the observation corpus.
- Bulk-imported bets add exactly one row per real wager to the same
  `bets.jsonl` as every other entry surface — no additional growth
  vector beyond normal bet volume.

---

## 14. Known limitations

- The bulk import tool accepts **structured JSON**, not free-form
  natural-language bet-list text (`"SF F5, $12 at +128"`) — translating
  that text into the JSON payload is Claude's job during the handoff,
  not something this tool parses itself. A future milestone could add a
  small NL parser for the common `"TEAM MARKET, $STAKE at ODDS"` shape,
  but it isn't part of this one.
- `registryClassificationStatus="UNCLASSIFIED_MLB"` markets are archived
  with whatever `parse_contract`/`classify_market` can best-effort infer
  from an unfamiliar ticker shape — fields may be partial/null; this is
  expected and reflected in `validationStatus: "warning"`, never
  fabricated.
- Bet-to-observation linkage only ever considers the **exact** ticker —
  it does not attempt to find a "close enough" alternate line/threshold
  when the exact ticker was never captured pregame.
- **Player-prop settlement families (pitcher/hitter props) remain
  `SETTLEMENT_UNRESOLVED`** — this was already true before this milestone
  and is explicitly out of scope here (see `docs/EDGELAB_PHASE1.md`'s
  Phase 2 recommendations). These markets ARE fully observable and
  queryable after this milestone (captured, archived, checkpoint-
  classified, reachable through §10's query surface, and
  `wasRecommended`/`wasPlaced` are populated for them) — they are simply
  not outcome-settled yet. Tracked as a scoped follow-up:
  **[#43 — Follow-up: automatic settlement for pitcher/hitter player-prop markets](https://github.com/chmoses98/edge-finder-api/issues/43)**.
