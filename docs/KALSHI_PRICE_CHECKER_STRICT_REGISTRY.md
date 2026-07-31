# Kalshi Price-Checker Strict Registry (correction mission)

## Problem

The standalone daily MLB price checker (`scripts/check_kalshi_prices.py` /
`lib/kalshi_price_check.py`) is a pricing/discovery tool only -- it must
show *just* today's real MLB game markets. A separate, deliberately broad
research tool (`scripts/discover_kalshi_series_catalogue.py`) treats any
Kalshi series with a `KXMLB`-prefixed ticker, or a title containing
"MLB"/"baseball", as "MLB-associated" -- useful for a catalogue/audit pass,
but far too broad for a daily game-price check. A live dispatch of that
discovery script captured
`data/kalshi/discovery/2026-07-30_series_catalogue.json`: **179**
"MLB-associated" series, of which **171** are outside the pre-existing
8-series allowlist. Only **17** of the 179 are genuine single-game (or
single-game player-prop) MLB markets -- the other 162 are season leaders,
awards, division/pennant/World Series futures, season win totals, draft
picks, trades, Home Run Derby, streaks, and markets for entirely different
competitions (World Baseball Classic, Mexican Baseball League, college
baseball, the Congressional Baseball Game) that merely share a `KXMLB`
ticker prefix or mention "baseball" in their title.

Querying all 179 series for market data (as the broad discovery script
did) is also what caused a flood of HTTP 429 (rate limit) errors.

## Root causes fixed

1. **`scripts/discover_kalshi_series_catalogue.py`** called the expensive
   `discover_markets_for_series()` (2 HTTP calls: open + closed) for
   *every* MLB-associated series it found -- up to 358 calls in one run.
   Fixed: only series in the strict single-game registry (see below) ever
   get the per-series markets query; every other flagged series still gets
   its ticker/title/evidence recorded in the catalogue (broad discovery
   stays broad for research), just without the expensive query.

2. **The actual price checker never queried the 162 unrelated series at
   all** -- it only reads `/api/kalshisearch`'s `markets` field, which is
   built from a small, fixed, per-series allowlist (`ALL_SERIES` in
   `api/kalshisearch.js`). The real latent gap there was narrower but real:
   `ALL_SERIES` never included the newly-confirmed F3/F7 winner markets or
   any of the 7 pitcher/hitter player-prop series, so they were never
   fetched at all; and `--include-unknown` defaulted to `True`, meaning any
   future broadening of the source (or an older/broader snapshot file)
   could flow unclassified markets straight into the daily output with no
   hard gate in front of them.

## The fix: a strict, evidence-based single-game registry

`lib/research/market_taxonomy.py`'s `SERIES_FAMILY_MAP` is the single,
evidence-only-based source of truth mapping a series ticker to a market
family -- every entry was added only after direct observation of a real
Kalshi series (never guessed). `SINGLE_GAME_SERIES_TICKERS =
frozenset(SERIES_FAMILY_MAP.keys())` is the resulting strict registry: the
exact 17 series confirmed to be single-game / single-game-player-prop MLB
markets --

| Ticker | Market family |
|---|---|
| `KXMLBGAME` | Full-game moneyline |
| `KXMLBSPREAD` | Full-game spread / run line |
| `KXMLBTOTAL` | Full-game total |
| `KXMLBTEAMTOTAL` | Team totals |
| `KXMLBRFI` | NRFI/YRFI |
| `KXMLBF3` | First 3 innings winner (three-way) |
| `KXMLBF5` | First 5 innings winner (three-way) |
| `KXMLBF7` | First 7 innings winner (three-way) |
| `KXMLBF5SPREAD` | First 5 innings spread |
| `KXMLBF5TOTAL` | First 5 innings total |
| `KXMLBKS` | Pitcher strikeouts |
| `KXMLBOUTS` | Pitcher outs recorded |
| `KXMLBHIT` | Player hits |
| `KXMLBTB` | Player total bases |
| `KXMLBHRR` | Hits + runs + RBIs |
| `KXMLBRBI` | RBIs |
| `KXMLBSB` | Stolen bases |

`lib/kalshi_mlb_single_game_registry.py` builds on this (never duplicates
it): `classify_series_for_price_check(series_ticker, title)` returns
`(True, None)` for anything in the registry, or `(False, reason_code)`
otherwise. **The allow decision is allowlist-only** -- title/prefix pattern
tables in that module are used *only* to assign a more specific exclusion
reason for audit telemetry (e.g. distinguishing "World Baseball Classic"
from "a season leader board"); an unmatched non-allowlisted series still
safely falls back to the generic `SERIES_NOT_ALLOWLISTED` reason. This is
the opposite of a title blacklist: nothing is included because its title
looks safe.

### Exclusion reason codes

| Code | Meaning |
|---|---|
| `SERIES_NOT_ALLOWLISTED` | Series is not one of the 17 confirmed single-game families, and matched no more specific pattern below. |
| `NON_MLB_COMPETITION` | World Baseball Classic, Mexican Baseball League, college baseball, or the Congressional Baseball Game. |
| `FUTURES_OR_AWARD` | Season leader boards, awards, division/pennant/World Series futures, season win totals, draft, trade, Home Run Derby, streaks, etc. |
| `DATE_MISMATCH` | Record's own parsed date does not match the `--date` requested by the caller. |
| `MALFORMED_EVENT` | Event ticker did not parse to a well-formed (date, away, home) triple at all. |
| `NOT_SINGLE_GAME_MARKET` | Reserved for a future case where an allowed series contains a non-single-game contract shape. Not yet triggered. |
| `TEAM_MAPPING_FAILED` | Reserved for a future team-identity mismatch signal. Not yet triggered. |
| `PLAYER_GAME_MAPPING_FAILED` | Reserved for player-level identity validation once real per-market player-prop ticker-suffix payloads are observed. Not yet triggered. |
| `CLOSED_OR_INACTIVE` | Reserved; closed-market exclusion is currently handled by the pre-existing, unchanged `apply_filters()` `include_closed` flag. |

### Why no `data/slate.json` dependency

`data/slate.json` is a hard safety-isolation boundary for this tool (see
`tests/test_check_kalshi_prices_safety_isolation.py`'s `FORBIDDEN_PATHS`,
and the price checker's own docstring: it must never touch the production
slate/projection/recommendation/risk/execution pipeline). Game/date
validation therefore uses only the record's own event-ticker-derived
`(date, awayTeam, homeTeam)` fields (already parsed and tested via
`parse_event_teams()`/`parse_kalshi_event_date()`), matched against the
CLI's `--date` when one is supplied. A same-batch "anchor series" cross-
check (e.g. requiring a `KXMLBGAME` moneyline market to co-occur with every
other market for that game) was considered and rejected: Kalshi's
`status=open` per-series queries mean a game's moneyline market can already
be closed while its other markets are still open, so requiring a companion
market in the exact same fetch is a real, observed source of false
exclusions, not a genuine game-identity signal.

## Where the gate runs

`lib.kalshi_price_check.apply_strict_game_registry(records,
requested_date=None)` is the mandatory gate: it runs on every normalized
record, before the user's optional `apply_filters()` pipeline, and cannot
be disabled by any CLI flag (unlike `--include-unknown`). It returns
`(kept, excluded)` -- `excluded` entries carry `{**record,
"exclusionReason": <code>}` and are never dumped into the main output, but
are written to a separate audit artifact
(`kalshi_price_check_artifacts/kalshi_price_check_excluded.json` when
`--archive` is used).

## Output

The standalone checker's table output is now grouped by game
(`lib.kalshi_price_check.group_by_game()` /
`format_by_game()`) -- one section per real MLB game, listing every
approved market family for it. `run()`'s metadata additionally reports:

- `gamesFoundCount` / `gamesFound` -- distinct games represented in the
  approved output.
- `approvedSeriesQueried` -- which of the 17 registry series actually
  returned data this run.
- `marketsExcludedByRegistry` / `exclusionReasonCounts` -- how many
  markets were excluded and why.
- `unresolvedMappingsCount` -- excluded markets whose game identity could
  not be resolved at all (`DATE_MISMATCH` + `MALFORMED_EVENT`).
- `queryErrors` -- any live-fetch fallback error.

## Backward compatibility

- The broad series-catalogue audit (`scripts/discover_kalshi_series_
  catalogue.py`) still records every MLB-associated series it finds --
  only the expensive per-series *market* query is now gated.
- F3/F5/F7 three-way support is unchanged and unregressed -- all three are
  in the strict registry.
- `lib.research.market_taxonomy`'s classification, settlement logic, and
  probability adapters are unchanged; only 3 new hitter-prop family
  constants and 7 new `SERIES_FAMILY_MAP` entries were added (all backed by
  the same live catalogue evidence).
- Existing `apply_filters()` behavior (date/team/family/scope/status/
  include-closed/include-unknown/max-results) is completely unchanged --
  the strict registry gate runs *before* it, as an additional, separate
  stage.
