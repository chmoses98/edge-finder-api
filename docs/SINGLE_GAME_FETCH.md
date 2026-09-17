# Single-Game Fetch

Fresh data for exactly **one** MLB matchup, in one self-contained file, without
running or disturbing the full slate pipeline.

---

## How to run it

Workflow: **Fetch Single Game** (`.github/workflows/fetch-single-game.yml`),
`workflow_dispatch` only.

### 1. A normal one-game matchup

```
POST /repos/chmoses98/edge-finder-api/actions/workflows/fetch-single-game.yml/dispatches
Body: {"ref":"main","inputs":{"date":"2026-09-17","game":"Yankees vs Red Sox"}}
```

`date` may be left blank — it defaults to **today, America/New_York**.
`game` accepts `Yankees vs Red Sox`, `Yankees at Red Sox`, `NYY@BOS`, `NYY/BOS`,
and either order (`Red Sox vs Yankees` selects the same game).

### 2. A team abbreviation

```
Body: {"ref":"main","inputs":{"game":"MIL"}}
```

One team is enough whenever that team plays exactly once that day. Full names,
cities, nicknames and other sources' abbreviations all resolve onto this repo's
convention: `Brewers`, `Milwaukee`, `MIL`; `ARI`→`AZ`; `OAK`→`ATH`; `CHW`→`CWS`;
`WAS`→`WSH`.

Deliberately ambiguous tokens are **refused**, not guessed: `Chicago`
(CHC/CWS), `Sox` (BOS/CWS), `New York` (NYY/NYM), `LA` (LAD/LAA).

### 3. A doubleheader — use the exact gamePk

`game` alone cannot identify a doubleheader leg, so the run **fails closed**
(exit code 3) and prints every candidate:

```
REFUSED: AMBIGUOUS_GAME_SELECTOR: ['BOS', 'NYY'] matches 2 games on this date
         (doubleheader) -- re-run with an exact gamePk

Candidate games on 2026-09-17:
  gamePk=776201 NYY@BOS start=2026-09-17T17:10:00Z status=Scheduled venue=Fenway Park
  gamePk=776202 NYY@BOS (doubleheader game 2) start=2026-09-17T23:10:00Z status=Scheduled venue=Fenway Park

Re-run with --game-pk <gamePk> to pick exactly one.
```

Then:

```
Body: {"ref":"main","inputs":{"date":"2026-09-17","game_pk":"776202"}}
```

`game_pk` wins over `game`, and is still validated against that date's schedule —
a gamePk that is not playing that day is refused rather than trusted.

### Locally

```bash
python3 scripts/fetch_single_game.py --date 2026-09-17 --game "Yankees vs Red Sox"
python3 scripts/fetch_single_game.py --game MIL
python3 scripts/fetch_single_game.py --date 2026-09-17 --game-pk 776202
python3 scripts/fetch_single_game.py --date 2026-09-17 --game MIL --source snapshot   # offline, reuse archive
```

---

## Where the artifact lands

```
data/single_game/<YYYY-MM-DD>/<gamePk>.json    the bundle
data/single_game/<YYYY-MM-DD>/index.json       that date's artifacts
data/single_game/latest.json                   newest-first pointer  ← start here
```

`latest.json` exists so a fresh ChatGPT/Claude session can find the newest
single-game artifact without knowing any gamePk.

### What is in the bundle

| Key | Contents |
|-----|----------|
| `gamePk` / `matchup` / `scheduledStart` / `venue` / `doubleheaderGameNumber` | resolved identity, from the MLB schedule |
| `rawArchive` | where the COMPLETE unfiltered capture was written, its market count, and the two invariant flags |
| `markets` | **every** Kalshi market for this game — moneyline, F3/F5/F7, run line / winning margin, game totals, team totals, NRFI/YRFI, pitcher and hitter props |
| `marketSummary` | census by family and by scope, so "did we get everything?" is answerable at a glance |
| `registryExcludedForThisGame` | contracts naming this matchup that the strict single-game registry gate rejected, with reasons — reported, never silently dropped |
| `marketFilterStageReport` | per-stage counts, so a zero result always has a named cause |
| `slateContext` | this game's canonical slate block **verbatim**, `marketLedger` included — or an explicit absence status |
| `fullMarketCoverage` | this game's rows from the already-built full-market-coverage artifact, when it exists |
| `warnings` / `guarantees` | what was missing, and what the run structurally promises |

A real run (MIL@PIT, 2026-09-17) produced **141 markets** for the one game out of
a 1,151-market universe, across 13 families.

---

## Exhaustiveness: nothing attributable may vanish

Every raw Kalshi contract attributable to the selected game must be represented
in the bundle — in `markets`, or in `registryExcludedForThisGame` with its raw
reason. The failure this prevents: Kalshi introduces a new single-game family,
the strict registry does not recognise its series, and its contracts quietly
disappear while the artifact still looks complete.

`contractAccounting` makes it checkable:

| Field | Meaning |
|---|---|
| `attributionMethod` | `KALSHI_EVENT_TICKER` — Kalshi's own grouping key, not a guess |
| `rawGameAttributableContracts` | raw contracts sharing this game's event tickers |
| `normalizedMarkets` / `excludedOrUnresolved` | where they ended up |
| `accountedContracts` | how many are represented somewhere |
| **`silentRemainderCount`** | attributable but represented nowhere — **must be 0** |
| `unattributableRawContracts` | contracts with no event ticker at all: ambiguity **retained explicitly**, never guessed into or out of this game |

`silentRemainderCount == 0` is required for a successful artifact. A violation
**aborts the run** (exit 1) rather than writing a plausible-looking but
incomplete bundle.

## Lineup confirmation is exposed, never bypassed

The artifact is produced regardless of lineup status, because a single-game
fetch is also a research tool. But it states the answer explicitly in
`lineupConfirmation`:

```json
{"bothOfficialLineupsConfirmed": true, "realMoneyEligible": true,
 "status": "BETTING_ELIGIBLE", "sides": {"away": {...}, "home": {...}}}
```

A fresh single-game fetch is **not** a way around the confirmed-lineup betting
gate. When `realMoneyEligible` is false the run emits a warning saying the
artifact is research/early-value context only.

## The two invariants

### 1. The raw Kalshi archive stays the COMPLETE, UNFILTERED universe

The order is fixed *and checked*, not assumed:

1. fetch the full `/api/kalshisearch` universe (one call);
2. write it **verbatim and unfiltered** to
   `data/kalshi_registry_snapshots/kalshi_search_<date>_<HHMM>.json` — the same
   production archive path and convention `capture-snapshots-scheduled.yml` uses;
3. read the archived file back and assert its market count equals what was
   fetched (`verify_archive_is_complete`);
4. **only then** filter down to the requested game.

A failure at 2 or 3 aborts before any filtering happens, and no artifact is
written. The archive write is deliberately the **timestamped file only** — never
the primary `kalshi_search_<date>.json`, which belongs to the scheduled capture
workflow — so this capture is strictly additive. Both the artifact and the raw
capture are committed.

### 2. It never writes a one-game pseudo-slate

Nothing here writes `data/slate.json`, `bets.json`, `marketLedger`, `risk_gate`
output, or any pipeline artifact. `data/slate.json` is the authoritative betting
slate for the whole day and is guarded by `scripts/protect_slate.py`; a one-game
file written there would look like a real slate to every downstream consumer
(model-snapshot-scheduler, risk_gate, validate_slate_final, CLV tracking) while
containing a fifteenth of the day.

---

## Reuse, not a mini-model

Nothing about markets is re-derived. The production modules do the work,
unchanged:

* `lib/edgelab/mlb_schedule.py` — the one canonical MLB schedule adapter
  (`fetch_schedule_range` was added here for the offensive-form capture; the
  single-game path uses the existing `fetch_schedule`/`parse_schedule_games`)
* `lib/single_game_selector.py` — friendly selector → exactly one gamePk, or a
  refusal with candidates (pure; the only genuinely new logic)
* `lib/kalshi_price_check.py` — `normalize_batch` → `apply_strict_game_registry`
  (allowlist-only; a market that is not a real single-game MLB contract can never
  be included) → `apply_filters(games=[...])`
* the canonical slate artifact for handicapping context, copied verbatim

**No probability, edge, price adjustment or recommendation is computed
anywhere in this path.** Context that does not exist is reported as absent with
its own reason — never fabricated and never silently taken from another date.

---

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | artifact written |
| `1` | a real failure (schedule fetch failed, upstream fetch failed, archive verification failed) — nothing written |
| `3` | **ambiguous selector** — candidates printed, nothing fetched or written. Re-run with `game_pk`. |

The workflow surfaces code 3 explicitly in its job summary and error annotation,
so the operator is told to pick a gamePk rather than being handed a generic
failure.
