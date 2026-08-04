# Player-Prop Settlement (GitHub issue #43)

Closes the player-prop settlement gap left open by
`docs/MARKET_RESEARCH_CORPUS_AND_MANUAL_LOGGING.md`: pitcher/hitter
player-prop markets were fully observable/queryable but never
outcome-settled (`SETTLEMENT_UNRESOLVED` /
`player_prop_settlement_not_implemented`, unconditionally). This
milestone adds automatic settlement for all seven currently-captured
families, reusing the existing EdgeLab settlement pipeline, the
canonical `Settlement` schema, and the canonical placed-bet ledger — no
parallel settlement system, no second scheduled workflow.

**Explicitly out of scope** (unchanged by this work): production
probability calculations, recommendation thresholds, risk gates, stake
sizing, order execution, replay logic, slate output behavior.

## 1. Real-data audit

Before writing any code, every currently-captured player-prop market in
this repository's own archive
(`data/kalshi_registry_snapshots/kalshi_search_*.json`, all files, not
just the one named in the issue — **46,784 player-prop market rows
total**) was inspected directly. Findings, all independently verified
against every one of those 46,784 rows (0 exceptions):

- **Ticker structure**: `{SERIES}-{eventSuffix}-{playerToken}-{threshold}`,
  e.g. `KXMLBKS-26AUG021920BOSLAD-LADESHEEHAN80-9`. The event suffix
  (`26AUG021920BOSLAD`) encodes date+time+teams identically to
  `KXMLBGAME` (`lib.kalshi_mlb_contract_parser.parse_event_suffix`).
  Splitting the ticker's suffix (everything after the event ticker) on
  the **last** `-` always yields exactly `(playerToken, threshold)`;
  `threshold` is always a plain non-negative integer string.
- **Player token structure**: always exactly
  `{teamAbbr}{firstInitial}{lastNameCompact}{trailingDigits}`, e.g.
  `LADESHEEHAN80` = team `LAD` + `E` + `SHEEHAN` + `80`. `teamAbbr` is
  always an exact prefix match against the game's own away/home
  abbreviation when known (never ambiguous, never needing a
  2-vs-3-letter guess). `trailingDigits` looks like a jersey number —
  **it is not an MLBAM player id** (see §3).
- **Title structure**: always `"{Display Name}: {N}+ {stat text}?"`,
  optionally with a parenthetical team tag (`"Max Muncy (LAD): 1+
  hits?"`). The `{N}` in the title always equals the ticker's own
  threshold (checked across all 46,784 rows — 0 mismatches; code still
  defensively records a `thresholdMismatch` flag rather than assuming
  this always holds).
- **Player-token inconsistency, real example found in the archive**:
  the same real last name **"Hernández"** appears as **both**
  `HERNANDEZ` (fully transliterated) and `HERNNDEZ` (accented character
  simply dropped) in different tickers for two different players. This
  is proof the ticker token must never be trusted as an authoritative
  identity source — see §3.
- Unicode/accented names (`Carlos Narváez`, `Teoscar Hernández`,
  `José Ramírez`, …), apostrophes (`Ryan O'Hearn`, `Ke'Bryan Hayes`,
  `Travis d'Arnaud`), periods (`A.J. Ewing`, `J.T. Realmuto`), and
  suffixes (`Bobby Witt Jr.`, `Fernando Tatis Jr.`, `Vladimir Guerrero
  Jr.`) all occur in real archived titles — all handled by
  `lib/research/player_prop_parser.py`'s normalization (§3).

This audit is fully reproducible and is also encoded as the module
docstring of `lib/research/player_prop_parser.py`.

## 2. Supported family matrix

| Family | Series | Stat category | Stat source |
|---|---|---|---|
| `pitcher_strikeouts` | `KXMLBKS` | pitching | `strikeOuts` |
| `pitcher_outs` | `KXMLBOUTS` | pitching | `outs` field, else derived from `inningsPitched` |
| `hitter_hits` | `KXMLBHIT` | batting | `hits` |
| `hitter_total_bases` | `KXMLBTB` | batting | `totalBases` field, else derived from hits/doubles/triples/homeRuns |
| `hitter_hits_runs_rbis` | `KXMLBHRR` | batting | `hits + runs + rbi` |
| `hitter_rbis` | `KXMLBRBI` | batting | `rbi` |
| `hitter_stolen_bases` | `KXMLBSB` | batting | `stolenBases` |

No speculative/not-yet-captured family was added. The single source of truth
for this table is `lib/edgelab/player_stats.py`'s
`STAT_CATEGORY_BY_FAMILY`.

**Contract semantics**: every one of these seven families is a literal
Kalshi "N+" contract — `YES` iff `actual >= N`. Equality is `YES`, never
a push. This is deliberately **not** the game-total family's
`actual > N.5` framing (`lib/edgelab/settlement.py`'s
`FAMILY_GAME_TOTAL`/`FAMILY_TEAM_TOTAL`/`FAMILY_WINNING_MARGIN`
branches) — the two must never share logic. A new `AT_LEAST` value was
added to the `comparisonOperator` enum in
`market_observation.schema.json`/`market.schema.json` (additive,
backward compatible — every existing `OVER`/`UNDER`/`YES`/`NO` row is
unaffected) specifically so player props represent their own semantics
honestly instead of borrowing `OVER`'s half-point framing.

## 3. Player identity and resolution design

**The ticker's numeric suffix is never treated as an MLB player id.**
It is jersey-number-style, and (see §1) Kalshi's own token rendering of
a last name is not even internally consistent — proof it cannot be an
authoritative identity source on its own.

One shared, pure parser (`lib.research.player_prop_parser.
parse_player_prop_market`) is used by **both** ingestion
(`lib.research.market_taxonomy.classify_market`, wired through
`lib.edgelab.market_universe.build_observations_from_snapshot`) and
settlement (`lib.edgelab.player_prop_settlement.
settle_player_prop_market`, which re-derives the parse fresh from the
Market record's own `marketTicker`/`eventTicker`/`title` rather than
trusting a possibly-stale persisted copy — see §6). It extracts:

- Market family (from the series ticker prefix, at the ingestion call
  site; the parser itself is family-agnostic)
- Displayed player name (from the title, parenthetical team tag
  stripped)
- Team abbreviation encoded by the ticker (prefers an exact prefix
  match against the game's own known away/home abbreviation; falls
  back to a 2-vs-3-letter heuristic only when team context isn't
  available)
- Raw Kalshi player token, verbatim (audit/corroboration only)
- Threshold (ticker-derived, cross-checked against the title's own
  "N+" text)
- `AT_LEAST` comparison semantics

**Resolution order** (`lib.edgelab.player_resolution.
resolve_player_in_game`), exactly per the issue's specification:

1. Exact MLB `gamePk` — enforced by the caller
   (`scripts/edgelab/settle_markets.py` only ever passes one game's
   boxscore at a time).
2. Correct team within that game (when the ticker's team resolved).
3. Normalized full player name from the market title
   (`normalize_player_name`: Unicode NFKD decompose + drop combining
   marks, strip periods/apostrophes/commas, single-space, lowercase;
   `normalized_name_variants` additionally accepts a
   suffix-stripped alternate form for `Jr.`/`Sr.`/`II`/`III`/`IV`/`V`).
4. The raw ticker token / jersey number — used **only** as secondary
   corroboration to break a tie among 2+ exact-name candidates, never
   as a primary signal and never consulted when the name search alone
   found zero candidates.
5. A unique MLB player — anything else (zero or 2+ candidates, even
   after jersey-number corroboration) is left `SETTLEMENT_UNRESOLVED`,
   never a "closest name" guess.

Search is always restricted to the exact game's own two rosters
(never an unrestricted league-wide fuzzy match).

## 4. Final-stat source and caching design

Reuses the MLB Stats API convention already established by
`scripts/fetch_lineups.py`/`scripts/fetch_opp_quality.py`/`clv_update.py`
— no new external integration, just a new endpoint
(`lib/edgelab/mlb_boxscore.py`):

- `GET https://statsapi.mlb.com/api/v1.1/game/{gamePk}/feed/live` — one
  call returns **both** the game's authoritative status
  (`gameData.status.detailedState`) **and** every player's final
  batting/pitching stat line
  (`liveData.boxscore.teams.{away,home}.players`).
- Fetched **at most once per `gamePk`**, lazily, and only for a game
  that actually has at least one player-prop market
  (`scripts/edgelab/settle_markets.py`'s `player_prop_cache`, keyed by
  `gameId`) — a game with no player props never triggers this fetch,
  and N player-prop markets on the same game never trigger more than
  one fetch (see `test_one_boxscore_fetch_per_game_despite_many_prop_
  markets`).
- Gated on `detailedState` in `{"Final", "Game Over", "Completed
  Early"}` (matching `scripts/fetch_opp_quality.py`'s
  `COMPLETED_STATUSES` convention) — a live/suspended/delayed/postponed
  game is always left `SETTLEMENT_UNRESOLVED`/`game_not_final`.
- A fetch failure (network error, timeout, malformed response) for one
  game is recorded as a run warning and leaves only that game's
  player-prop markets `SETTLEMENT_UNRESOLVED`/`boxscore_fetch_failed`
  — it never blocks any other game's settlement in the same run
  (`test_one_failed_game_does_not_block_another`).

## 5. Settlement-evidence design

Additive `settlementEvidence` object on the existing `Settlement`
schema (nullable; null for every non-player-prop settlement) —
deliberately **not** a separate persisted evidence file, since every
fact needed for a full audit trail is small and fits inline:
`gamePk`, `gameStatus`, `sourceSystem`/`sourceEndpoint`, a
`sourcePayloadHash` (sha1 of the exact feed payload used — traces a
settlement back to the exact response without committing the full
feed), `fetchedAt`, the resolved `playerId`/`playerName`/
`teamAbbreviation`, the ticker's `rawPlayerToken` (audit only),
`statCategory`/`statFields`/`actualValue`/`threshold`/
`comparisonOperator`, `kalshiOfficialResult` (see §6),
`resolutionStatus`/`resolutionReason`, and every exact-name `candidates`
found (populated even when unresolved, for audit).

## 6. Participation / DNP / void-rule findings

Every raw player-prop market record in every snapshot this repository
holds has **exactly** these fields: `event_ticker`, `market_ticker`,
`title`, `subtitle`, `open_time`, `close_time`, `market_type`, `status`,
`snapshot_ts`, `yes_bid`, `yes_ask`, `mid`, `implied_pct`,
`american_odds`, `last_price`, `volume`, `open_interest`. **None of
this repository's ingestion captures any Kalshi rules-text or
void-condition field for these series.** No participation/DNP/void rule
can therefore be directly verified from evidence this repository
actually has.

Per the issue's own explicit instruction, this milestone implements
**no** automatic VOID/NO path for a non-participating player — a player
absent from the final boxscore always resolves via the same
`player_not_resolved_zero_candidates` path as any other missing player,
left `SETTLEMENT_UNRESOLVED`. It must not gain an automatic VOID/NO
path without first capturing real Kalshi rule evidence.

Similarly, `kalshiOfficialResult` is always `None` today — no ingestion
path in this repository captures Kalshi's own settlement result for
these markets. The conflict-detection code path (`kalshi_mlb_result_
conflict`: if a Kalshi result and the MLB-derived result ever disagree,
the settlement is left unresolved with both preserved in evidence, never
silently resolved one way) is implemented and tested against synthetic
fixtures so it activates automatically the moment such a field exists —
no code change required.

## 7. Idempotency and corrections

`scripts/edgelab/settle_markets.py`'s existing `storage.upsert_records`
(keyed by `settlementId`/`betId`) already gives this for free — nothing
new needed:

- **Exact rerun**: identical final MLB stats produce a settlement
  record with the identical `settlementId`/`result`/`settlementStatus`/
  evidence content; `upsert_records` overwrites the existing row rather
  than appending a duplicate (`test_exact_rerun_is_idempotent`).
- **Corrected final stat**: if MLB later corrects a final statistic,
  rerunning `settle_markets.py` (or the backfill CLI) fetches the fresh
  feed, recomputes, and safely updates the existing `Settlement` record
  and every matching bet's result/net P&L in place — never a duplicate
  `Settlement` or `PlacedBet` record
  (`test_corrected_final_stat_updates_existing_record_and_bet_safely`).

## 8. Historical backfill

`scripts/edgelab/backfill_player_prop_settlement.py` reuses
`scripts/edgelab/settle_markets.py`'s `settle_date()` directly — no
second settlement implementation. A player-prop market's settlement is
derived fresh from its own ticker/eventTicker/title every time (§3), so
a date ingested long before this issue shipped is settleable
immediately, with no separate re-ingestion step.

```
python3 scripts/edgelab/backfill_player_prop_settlement.py --date 2026-08-02 --dry-run
python3 scripts/edgelab/backfill_player_prop_settlement.py --start-date 2026-08-01 --end-date 2026-08-03
```

`--dry-run` computes and prints the exact same per-family counts
(observed/settled/void/unresolved/betsUpdated) and unresolved-reason
breakdown **without writing anything to disk**. Per the issue's
instruction, no large generated historical settlement data is committed
in this PR — running a live (non-dry-run) historical backfill is a
separate operational step after merge.

## 9. Existing nightly integration

No new scheduled workflow was added. `.github/workflows/
edgelab-postgame.yml`'s existing `python3 scripts/edgelab/
settle_markets.py --date "$DATE"` step now settles player-prop markets
in the exact same run as every other family — `settle_date()` (the
refactored, reusable core of that script) fetches the player-prop
boxscore lazily alongside the existing linescore fetch, in the same
per-market loop, writing to the same `settlements/<date>.jsonl` and
`bets/bets.jsonl` files the workflow already commits.
