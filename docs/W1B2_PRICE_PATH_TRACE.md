# W1-B2 — the live executable-price path, traced before any edit

Traced on `main` at `97eccefe2209e78bb59fa70b18001c8423ab4616`, before a single
production line was changed. Every claim below is a file and line number on that
commit, not a recollection from B1.

The short version: **production never sees a genuine executable ask for any
family except NRFI/YRFI.** The real book exists, survives two stages, and is
then dropped one stage before the decision layer — which quietly substitutes a
midpoint and stores it in a field named `executablePriceUsed`.

---

## The pipeline, stage by stage

### Stage 1 — live Kalshi response → `scripts/fetch_kalshi_markets.py`

Writes `data/kalshi_registry_snapshots/kalshi_search_<date>_<HHMM>.json`.

| line | what happens |
|---|---|
| 178-180 | reads `yes_bid` **or** `yes_bid_dollars`, same for ask and last price |
| 183-186 | `norm(v): return f if f <= 1.0 else f / 100.0` — **magnitude heuristic H1** |
| 190 | `mid = ((yes_bid_d or 0) + (yes_ask_d or 0)) / 2` — **midpoint M1**, **zero substitution Z1** |
| 202-206 | persists `yes_bid`, `yes_ask`, `mid`, `implied_pct`, `last_price` |
| 243-263 | fallback branch repeats the same `mid` and zero substitution |

**Discarded here and never recovered:** `price_level_structure`, `price_ranges`,
`yes_bid_size_fp`, `yes_ask_size_fp`, `no_bid_dollars`, `no_ask_dollars`. The
grid metadata is present in the raw payload — B1 measured it on 3,255 markets —
and this is the stage that throws it away.

### Stage 2 — `lib/kalshi_mlb_contract_parser.py::parse_contract`

| line | what happens |
|---|---|
| 190-198 | `_price_to_pct(v): return round(v,2) if v > 1.0 else round(v*100,2)` — **magnitude heuristic H2** |
| 257-260 | prefers `*_dollars`, else the bare field; produces `yesBid/yesAsk/noBid/noAsk` at 0–100 pct |

This feeds `lib/edgelab/market_universe.py` → `data/edgelab/observations/*.jsonl.gz`,
which is the archive B1's canonical join reads. So the observation archive is
**downstream of H2** — a fact that matters for §2.

### Stage 3 — registry: `scripts/build_kalshi_registry.py::price_block` (L198) and its byte-copy `lib/kalshi_registry_market_builders.py::price_block` (L44)

| line | what happens |
|---|---|
| 189-192 / 30-34 | `norm()` again — **magnitude heuristics H3 and H4** (two independent copies) |
| 202 / 47 | `mid = ((bid or 0) + (ask or 0)) / 2` — **midpoints M2, M3**; **zero substitutions Z2, Z3** |
| 208 / 53 | `'american': american(mid)` — American odds derived **from the midpoint** |
| 204-205 / 49-50 | the block **does** retain `yes_bid` and `yes_ask` |

`bid or 0` is the CR-5 mechanism: an **absent** bid becomes numeric zero, so an
ask-only book yields `ask/2` and calls it a price.

### Stage 4 — `scripts/merge_odds.py` — **where the book dies**

| line | family | what survives into `odds.kalshi.*` |
|---|---|---|
| 242-247 | `ml` | `away`/`home` **american only**, tickers, source |
| 259-268 | `rl` | `implied_pct`, `american`, tickers |
| 274-281 | `total` | `implied_pct`, `american`, ticker |
| 289-299 | `team_totals` | `implied_pct`, `american`, ticker |
| 313-325 | `f5ml` | `american` ×3, tickers |
| 334-340 | `f5_spread` | `implied_pct`, `american` |
| ~345 | `f5_total` | same shape |

`away_p` / `home_p` are the Stage-3 price blocks and **do** carry `yes_bid` /
`yes_ask`. This stage keeps only `american`. That is the single line where
genuine book evidence stops existing for the decision layer.

**One exception.** RFI is plumbed correctly today: lines 85-106 carry
`yrfi_bid` / `yrfi_ask` through, and derive the NRFI side as `1 - yes_bid`.

Line 133-136 `vig_free(a_am, h_am)` → `kalshiVF`, the vig-free **midpoint**
probability, which becomes the fallback the next stage leans on.

### Stages 5-7 — `scripts/build_market_ledger.py`

| line | family | executable price actually used |
|---|---|---|
| 1401-1402 | ML | `_to_cents(ml.get('away_yes_ask') or ml.get('yes_ask'))` — **H5**, and away and home read the **same** fallback key |
| 1404-1410 | ML | when None (always, today) → implied prob **from `american(mid)`** |
| 1629-1634 | Team total | `_to_cents(yes_ask)`, else `tt_implied` (from mid), else from `tt_am` — **two** midpoint fallbacks |
| 1820 | F5 ML | `american_to_ask_cents(f5_prices, am_val)`; `f5_prices` is empty in practice → midpoint fallback; **H6** at L349-351 |
| 1754 | F5 tie | same helper, same fallback |
| 2114-2115 | NRFI / YRFI | `_tc2` — **H7**; NRFI `= 100 - yrfi_bid`, YRFI `= yrfi_ask`. **Genuine book.** |
| 452 | all | `exec_prob = executable_prob_from_price(yes_ask_cents) if yes_ask_cents is not None else kalshi_vf` — **the vig-free fallback for authority** |
| 514 | all | `'executablePriceUsed': yes_ask_cents` |
| 548-549 | all | `modelSnapshotPrice`, `executablePriceAtOutput` = the same value |

Then fees (`lib/edgelab/kalshi_fees.py`, L466-487), then
`confidence_from_edge(netExecutableEdge)`, the two tier caps, and
`enforce_bet_up_to`.

---

## The three defects this makes concrete

**D1 — `executablePriceUsed` is a midpoint.** For ML, TT, F5 and totals the
value stored in that field is derived from `american(mid)` or from
`implied_pct`, both of which are the midpoint. Production's own live numbers say
so: on the 2026-09-09 slate the field is populated on 42 of 165 ledger rows and
**equals `marketProbVF` exactly on 34 of them**, differing by at most 0.9pp on
the rest, and takes values like **99.9¢** that are not even on Kalshi's
whole-cent grid.

**D2 — away and home share a fallback key.** Lines 1401-1402 both read
`ml.get('yes_ask')`. Today `merge_odds` emits no such key so both resolve to
`None` and the bug is dormant. **It wakes up the moment the book is plumbed
through** — which is exactly what B2 does. Fixing the plumbing without fixing
this line would hand both contracts one book.

**D3 — seven magnitude heuristics on a money path.** H1…H7 all decide
dollars-vs-cents from the numeric magnitude. Each maps a genuine **1-cent quote
to 100 cents**, and symmetrically reads a `1.0` meaning `$1.00` as 1 cent. They
sit at every boundary between the exchange and the decision.

| id | location |
|---|---|
| H1 | `scripts/fetch_kalshi_markets.py::norm` |
| H2 | `lib/kalshi_mlb_contract_parser.py::_price_to_pct` |
| H3 | `scripts/build_kalshi_registry.py::norm` |
| H4 | `lib/kalshi_registry_market_builders.py::norm` |
| H5 | `scripts/build_market_ledger.py::_to_cents` (defined **twice**, L1265 and L1397) |
| H6 | `scripts/build_market_ledger.py::american_to_ask_cents` |
| H7 | `scripts/build_market_ledger.py::_tc2` (L2104) |

---

## What B2 must therefore change

1. Replace H1…H7 with declared-unit, Decimal conversion, or quarantine the path
   from authoritative pricing.
2. Stop Stage 4 discarding the book: carry ticker, side book, `capturedAt` and
   provenance per contract.
3. Replace all five family derivations with **one** seam that consumes
   `lib.edgelab.canonical_price`.
4. Delete the `else kalshi_vf` fallback at L452 for authority.
5. Fix D2 explicitly and prove it with an away/home swap test.

Everything else — identity redesign, settlement, JS authority, model quality —
is out of scope and stays out.
