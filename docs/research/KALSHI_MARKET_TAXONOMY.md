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

**CORRECTION — F3/F7 exist on Kalshi; this repository never discovered
them.** The original version of this document stated "No F3 or F7
series was found... Kalshi does not appear to offer" these markets.
**That statement was false and has been retracted.** A user with
direct Kalshi account access confirmed placing real wagers on both MLB
F3 (first-3-innings) and F7 (first-7-innings) markets, which were
visible and tradable in the Kalshi interface. See the dedicated
"F3/F7 correction" section below for the full corrected findings,
root cause, and status. `data/research/kalshi_mlb_market_inventory.json`
no longer contains the retracted `confirmedAbsentSeries` field — it
now carries `userConfirmedUndiscoveredHorizons`, which honestly
distinguishes "exists on Kalshi" from "discovered/archived/normalized/
projection-supported/production-supported by this repository."

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

`is_three_way_family(family, scope)` returns `True` for
`("inning_result", "F3"|"F5"|"F7")` — full-game is deliberately
excluded, matching the confirmed two-way finding above. This function
is the single place a future phase should consult before assuming any
market needs three-outcome handling, rather than re-deriving the
distinction ad hoc. **Important:** `True` here means "treat as
three-way for canonical-probability purposes" — it does NOT mean
"confirmed three-way for all three scopes equally." Only F5 is
confirmed three-way via direct ticker inspection; F3/F7 are an
unverified-but-conservative assumption (see below). Use
`HORIZON_MARKET_STATUS[scope]["outcomeStructureStatus"]` to see which
scopes are `"CONFIRMED_THREE_WAY"` vs. `"UNVERIFIED"`.

## F3/F7 correction (Model Performance Phase 1 amendment)

### What was wrong

This document, `docs/research/PROJECTION_AUDIT.md`,
`docs/research/PROJECTION_UPGRADE_ROADMAP.md`, PR #12's description,
and `lib/research/market_taxonomy.py`'s module docstring all previously
stated some form of "no F3 or F7 series exists on Kalshi." That
conclusion was drawn solely from the absence of any F3/F7 ticker in
`data/kalshi_registry_snapshots/*.json` and the archive discovery
files this phase examined — i.e., "this repository has never fetched
one" was incorrectly treated as equivalent to "Kalshi doesn't offer
one." **A user with direct Kalshi account access confirmed placing
real wagers on both MLB F3 (first-3-innings) and F7 (first-7-innings)
markets, which were visible and tradable in the Kalshi interface.**
The prior conclusion is retracted everywhere it appeared.

### Root cause of the discovery failure

See `docs/research/PROJECTION_AUDIT.md`'s "CORRECTION — F3/F7
discovery failure root cause" section for the full candidate-by-
candidate analysis. Summary: **a fixed, hardcoded series-ticker
allowlist** exists independently in `api/kalshisearch.js`'s
`ALL_SERIES` list, `scripts/build_kalshi_registry.py`'s
`SERIES_CATALOGUE` dict, and `scripts/fetch_kalshi_markets.py`'s
single hardcoded `SERIES_TICKER` — none includes an F3 or F7 ticker,
and no fetcher anywhere in this repository calls a Kalshi endpoint
capable of enumerating series without already knowing its name. The
snapshot archive this phase originally searched
(`data/kalshi_registry_snapshots/*.json`) is populated exclusively by
`api/kalshisearch.js`, so it could never have contained F3/F7 by
construction, independent of whether Kalshi offers them.

### Real read-only discovery attempt (this phase)

This phase attempted a live, read-only Kalshi API call to
independently search for F3/F7 markets, per the corrected mission's
explicit instruction not to simply repeat the "does not exist" claim.
The attempt failed: this execution environment's network egress policy
explicitly denies outbound connections to `api.elections.kalshi.com`
(confirmed via the environment's own proxy-status diagnostic, which
reported `"connect_rejected"` / `"gateway answered 403 to CONNECT
(policy denial or upstream failure)"` for that host). **F3 and F7
existence is confirmed by user observation, but the repository's
current API discovery path (and this phase's execution environment)
cannot retrieve them.** No fabricated or guessed live data is recorded
anywhere in this correction.

### Corrected market-family count

The "8 discovered market families / 720 markets" table above is
**unchanged and still accurate** — it describes what this
repository's fetchers actually queried and received. It was never the
count of "all markets Kalshi offers for MLB"; the correction is to how
that distinction is now made explicit at every layer (inventory JSON,
projection comparison, taxonomy docstring) rather than left implicit
or, worse, mis-stated as completeness.

### Outcome structure — verified vs. unverified

| Horizon | Existence | Outcome structure | Confidence |
|---|---|---|---|
| Full game (`KXMLBGAME`) | Confirmed via repository snapshot | Two-way (Away/Home) | Confirmed via direct ticker-count inspection |
| F5 (`KXMLBF5`) | Confirmed via repository snapshot | Three-way (Away/Tie/Home) | Confirmed via direct ticker-count inspection (explicit `-TIE` leg) |
| F3 | **Exists on Kalshi (user-confirmed)** | **Unverified** | Not independently verified this phase — do NOT assume it matches F5's three-way shape merely by analogy. `is_three_way_family()` conservatively treats it as three-way (so a real tie is never renormalized away if one exists), but this is a safety default, not a confirmed fact. |
| F7 | **Exists on Kalshi (user-confirmed)** | **Unverified** | Same as F3. |

### Repository support status (per horizon, single source of truth)

`lib/research/market_taxonomy.py`'s `HORIZON_MARKET_STATUS` dict (also
reused verbatim in `data/research/kalshi_mlb_market_inventory.json`'s
`userConfirmedUndiscoveredHorizons` and
`data/research/projection_outcome_comparison.json`'s per-horizon
`marketSupportStatus`) is the one place this distinction is recorded,
so no artifact can drift back into the retracted claim:

| Status field | Full game | F5 | F3 | F7 |
|---|---|---|---|---|
| `existenceStatus` | `CONFIRMED_VIA_REPOSITORY_SNAPSHOT` | `CONFIRMED_VIA_REPOSITORY_SNAPSHOT` | `EXISTS_ON_KALSHI_USER_CONFIRMED` | `EXISTS_ON_KALSHI_USER_CONFIRMED` |
| `repositoryFetcherSupport` | True | True | **False** | **False** |
| `archiveCoverage` | True | True | **False** | **False** |
| `normalizationSupport` | True | True | True (title-fallback classifier, see below) | True |
| `projectionSupport` | True | True | True (`three_way_projection.py` is horizon-generic) | True |
| `productionEnabled` | True | True (team legs only; Tie captured-never-evaluated) | False | False |
| `outcomeStructureStatus` | `CONFIRMED_TWO_WAY` | `CONFIRMED_THREE_WAY` | `UNVERIFIED` | `UNVERIFIED` |
| `settlementStatus` | inferred from ticker structure | inferred from ticker structure | `UNVERIFIED` | `UNVERIFIED` |

### Classifier fix — no longer requires a pre-approved prefix

`lib/research/market_taxonomy.py`'s `classify_market()` now has a
title/subtitle-text fallback path (`_infer_unconfirmed_inning_scope_from_text`,
`_looks_like_result_market`) used whenever a series ticker prefix is
not recognized: it searches the title/subtitle text for F3/F7 horizon
language ("first 3 innings", "first three innings", "through 3
innings", "after 3 innings", and the F7 equivalents, plus a
word-boundary `f3`/`f7` match) combined with result-market language
("winner"/"wins"/"win"). A match classifies the market as
`inning_result`/`F3` or `F7` with
`classificationStatus = "classified_by_title_fallback_unverified_prefix"`
— honestly distinguishable from prefix-confirmed classifications, and
still detects a `-TIE` suffix as `outcome="Tie"` rather than a push.
This directly satisfies the requirement that discovery must not
require a series to be pre-approved by ticker prefix before it can
appear in the audit inventory — see
`tests/research/test_market_taxonomy.py`'s
`TestTitleFallbackClassification` for tests using literal F3/F7-styled
example titles with an unrecognized ticker prefix.

### What must change in a future phase

F3/F7 market **ingestion** (adding real series tickers — once
independently confirmed, not guessed — to the fetch layer, capturing
real snapshots, and building a real historical archive) remains
unbuilt. This is a real, still-open gap, distinct from the retracted
"doesn't exist" claim. See the revised Phase 2A scope in
`docs/research/PROJECTION_UPGRADE_ROADMAP.md`, which now covers
F3/F5/F7 uniformly rather than F5 alone.
