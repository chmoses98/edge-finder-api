# KALSHI_MARKET_TAXONOMY.md

Model Performance Phase 1 (Market Audit) — Parts 2 and 3.

## Discovery sources used

1. **Primary (freshest, most complete):**
   `data/kalshi_registry_snapshots/kalshi_search_2026-07-29_0803.json`
   — a real, current, local snapshot (720 markets, 8 series). This is
   the file `scripts/research/build_kalshi_market_inventory.py` reads
   to produce `data/research/kalshi_mlb_market_inventory.json`.
2. **Cross-validation (stability check across time):**
   `archive/data/kalshi_full_enumeration.json` (2026-06-04) — an
   earlier, independent hand-run discovery probe. Used to confirm the
   two-way/three-way structural findings below are not an artifact of
   one specific day.
3. **Supplementary:** `archive/data/kalshi_remaining_discovery.json`
   (2026-06-04) — surfaced `KXMLBSPREAD`'s per-team margin-threshold
   ticker shape with real example titles ("Giants wins by over 10.5
   runs?").
4. No live Kalshi API call was made at any point during this phase.

## Discovered MLB market families and counts (2026-07-29 snapshot)

| Series ticker | Count | Family | Scope | Three-way? |
|---|---|---|---|---|
| `KXMLBGAME` | 30 | `game_result` | `full_game` | **No** (confirmed 2-way, no `-TIE` ticker in either snapshot) |
| `KXMLBF5` | 45 | `inning_result` | `F5` | **Yes** (confirmed 3-way, explicit `-TIE` ticker every event) |
| `KXMLBSPREAD` | 90 | `winning_margin` | `full_game` | No |
| `KXMLBTOTAL` | 165 | `game_total` | `full_game` | No |
| `KXMLBTEAMTOTAL` | 210 | `team_total` | `full_game` | No |
| `KXMLBF5SPREAD` | 60 | `winning_margin` | `F5` | No |
| `KXMLBF5TOTAL` | 105 | `inning_total` | `F5` | No |
| `KXMLBRFI` | 15 | `first_inning_run` | `F1` | No |
| **Total** | **720** | 8 families/series | — | 45 three-way markets (all `KXMLBF5`) |

**Number of discovered market families: 8** (as series; 7 distinct
normalized `family` values, since `KXMLBSPREAD` and `KXMLBF5SPREAD`
share `winning_margin` at different scopes).

**No F3 or F7 series was found** in any of the ~250 snapshot files or
3 archive discovery files examined. This is recorded explicitly in
`data/research/kalshi_mlb_market_inventory.json`'s
`confirmedAbsentSeries` field rather than silently omitted.

**No standalone pitcher-prop or hitter-prop series was found.** This
repository's own fetch scripts (`api/kalshisearch.js` /
`scripts/build_kalshi_registry.py`) only ever target the 8 series
above — this is recorded as an **inventory gap** (this repo's fetchers
may simply never have targeted whatever pitcher/hitter series Kalshi
offers), not a confirmed statement that Kalshi has no such markets.

## Settlement conventions — read and documented per series, not assumed

The mission explicitly warns not to assume every "winner" market uses
the same convention, and not to infer that a two-way moneyline maps to
a Kalshi three-way contract. Per-series findings:

- **`KXMLBGAME` (full-game "Winner?")** — confirmed two-way via direct
  ticker-count inspection (exactly 2 market tickers per event, one per
  team, never a `-TIE` leg, across two independent snapshots ~2 months
  apart). Settlement basis: final score **including extra innings**
  (a regulation tie is not a terminal outcome for this contract — the
  game continues until decided).
- **`KXMLBF5` (first-5-innings "Winner?")** — confirmed three-way via
  the same direct inspection (exactly 3 market tickers per event: two
  team legs + one `-TIE` leg, confirmed with a real title, *"Seattle
  vs Los Angeles D first 5 innings tie?"*). Settlement basis: score
  **after exactly 5 complete innings**, independent of what happens in
  the rest of the game.
- **`KXMLBSPREAD` / `KXMLBF5SPREAD` (winning margin)** — one market per
  team per margin threshold (e.g. `-SF11` = "Giants win by over 10.5
  runs"), confirmed via real archive titles. Not itself three-way; each
  threshold is its own binary market.
- **`KXMLBTOTAL` / `KXMLBF5TOTAL` (totals)**, **`KXMLBTEAMTOTAL` (team
  totals)** — standard Over/Under binary markets at a strike; this
  phase did NOT independently locate Kalshi's own settlement-rules text
  for these (this repository's fetch parser does not capture that
  field at all — see the "Documented gaps" section below), so the
  half-run-line convention (odd `.5` strikes → no push possible) is
  ASSUMED from the observed strike values, not confirmed from Kalshi's
  own rules text.
- **`KXMLBRFI` (NRFI/YRFI)** — one market family, `F1`-scoped,
  binary (runs scored in the first inning: yes/no).

**This module deliberately did NOT infer that any two-way sportsbook
moneyline corresponds directly to a Kalshi three-way contract** — the
full-game vs. F5 finding above is exactly the kind of case the mission
warned about, and it was resolved by direct ticker-count inspection of
real data, not assumption.

## Documented gaps in the current inventory (real, not hidden)

- **No NO-side pricing captured.** This repository's own snapshot
  format (produced by `api/kalshisearch.js`) records `yes_bid`/
  `yes_ask`/`mid`/`last_price` but has no `no_bid`/`no_ask` fields at
  all. `data/research/kalshi_mlb_market_inventory.json` records
  `noBid`/`noAsk` as explicit `null`, never a derived/fabricated value.
- **No Kalshi settlement-rules text captured.** Kalshi's real API
  exposes `rules_primary`/`rules_secondary` fields; this repository's
  fetch scripts do not currently request or store them. Every
  `settlementBasis` value in the inventory is `settlementRulesSource:
  "inferred_from_ticker_structure_not_kalshi_rules_field"` — inferred
  from observed ticker shape (e.g. presence/absence of a `-TIE` leg),
  not read from Kalshi's own rules text. **Recommend Wave 1** add
  `rules_primary`/`rules_secondary` capture to the fetch layer before
  any market activation decision is finalized.
- **Open interest and volume are present** for every market in the
  primary snapshot — no gap there.

## Normalized market taxonomy (implemented, not just designed)

`lib/research/market_taxonomy.py`'s `classify_market()` implements
exactly the Part 3 contract shape, tested in
`tests/research/test_market_taxonomy.py` against REAL ticker strings
copied from the primary snapshot (not invented examples). Every raw
identifier (`marketTicker`, `eventTicker`, `seriesTicker`, `rawTitle`,
`rawSubtitle`) is preserved verbatim; `family`/`scope`/`outcome`/
`team`/`operator`/`settlementBasis` are separated into distinct fields,
never collapsed into one ambiguous "market" string.

`is_three_way_family(family, scope)` returns `True` **only** for
`("inning_result", "F3"|"F5"|"F7")` — full-game is deliberately
excluded, matching the confirmed two-way finding above. This function
is the single place a future phase should consult before assuming any
market needs three-outcome handling, rather than re-deriving the
distinction ad hoc.
