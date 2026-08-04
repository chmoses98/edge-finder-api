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

### 3.1 Parser-integrity gate (correction round)

A market is left `SETTLEMENT_UNRESOLVED` -- never settled on a
partially-trusted parse -- BEFORE player resolution is ever attempted,
when any of the following hold (each with its own specific reason,
checked in `lib.edgelab.player_prop_settlement.settle_player_prop_market`):

| Condition | Reason |
|---|---|
| Ticker and title thresholds disagree | `player_prop_threshold_mismatch` |
| Ticker's team token matches neither known side, or no team context at all | `player_prop_team_unresolved` |
| Player token doesn't match the expected shape | `player_prop_token_malformed` |
| Title doesn't match "Name: N+ stat?", or none available | `player_prop_market_not_parseable` |
| Title's stat wording doesn't match the family's expected wording | `player_prop_stat_text_family_mismatch` |
| Title's parenthetical team tag conflicts with the ticker-derived team | `player_prop_parenthetical_team_conflict` |

The stat-wording check (`lib.research.player_prop_parser.FAMILY_STAT_TEXT`)
validates the EXACT text Kalshi uses per family (verified against all
46,784 real rows): `strikeouts` / `outs recorded` / `hits` /
`total bases` / `hits + runs + rbis` / `rbis` / `stolen bases`.
Team resolution is strict: `settle_player_prop_market` always supplies
`away_abbr`/`home_abbr`, so a ticker whose team token matches neither
side is a hard block, never downgraded into
`lib.edgelab.player_resolution.resolve_player_in_game`'s "search both
rosters" fallback (that fallback exists only for the parser's own
lenient ingestion-time use, where team context may genuinely be
unavailable) -- a market is never settled merely because its title
contains a name that happens to uniquely match a player somewhere in
the game.

### 3.2 Participation verification (correction round)

A player NAME-matched within the correct team's boxscore listing is
**not proof of participation** -- every player on the active roster for
a game is listed in the boxscore's `players` dict whether used or not,
so an entirely zero-filled `stats.batting`/`stats.pitching` sub-object
is indistinguishable from a bench player who never entered.
`lib.edgelab.player_participation.verify_participation` requires
POSITIVE authoritative evidence before a zero-valued stat is trusted as
a genuine `NO`:

- Pitcher props: `gamesPitched` (or `gamesPlayed` within the pitching
  stat group) `>= 1`, or a non-zero `inningsPitched`.
- Hitter/pinch-runner props: `gamesPlayed >= 1` (this is the field that
  correctly credits a PINCH RUNNER who scored or stole a base without
  ever batting -- MLB's own `gamesPlayed` convention counts any
  in-game appearance, so `hitter_hits_runs_rbis`/`hitter_stolen_bases`
  verify correctly even at zero plate appearances), or a positive
  `plateAppearances`/`atBats` as a secondary signal.

Missing or inconclusive evidence is `SETTLEMENT_UNRESOLVED`/
`player_participation_unverified` -- never inferred either way from a
zero-filled stat object alone, and never a path to an automatic `NO` or
`VOID` (see §6).

### 3.3 Strict numeric validation (correction round)

Every counting statistic (strikeouts, direct pitcher outs, hits, direct
total bases, runs, RBIs, stolen bases, and every total-base / hits-runs-
RBIs derivation component) is parsed via
`lib.edgelab.player_stats.parse_nonnegative_int` -- never a bare
`int(value)` conversion, which silently TRUNCATES a fractional value
(`int(3.5) == 3`) instead of rejecting it. It accepts ONLY an exact
nonnegative integer (as `int`, as an exact-whole `float`, or as a
plain-digit `str`) and rejects everything else: booleans (`bool` is an
`int` subclass in Python and would otherwise silently parse as 1/0),
negative numbers, non-integral floats, decimal strings, `NaN`/`inf`,
malformed strings, and arbitrary objects. `inningsPitched` parsing
remains its own, independently strict path (only `.0`/`.1`/`.2`
fractional components are ever valid; a negative, non-numeric, boolean,
or `NaN`/`inf` value is rejected outright).

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
`resolutionStatus`/`resolutionReason`, every exact-name `candidates`
found (populated even when unresolved, for audit), and
`participationStatus`/`participationEvidence` (correction round -- the
exact `gamesPlayed`/`gamesPitched`/`inningsPitched`/`plateAppearances`/
`atBats` fields §3.2's participation check was derived from).

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
**no** automatic VOID/NO path for a non-participating player, in either
of the two ways "didn't participate" can present:
- **Absent from the boxscore entirely**: resolves via
  `player_not_resolved_zero_candidates` (player resolution finds no
  matching name at all).
- **Listed but never verifiably used** (correction round, §3.2): a
  bench player's name may match, but resolves via
  `player_participation_unverified` (positive participation evidence
  was required and not found).

Both leave the market `SETTLEMENT_UNRESOLVED` -- neither must gain an
automatic VOID/NO path without first capturing real Kalshi rule
evidence.

Similarly, `kalshiOfficialResult` is always `None` in every actual
settlement run today: no ingestion path in this repository captures
Kalshi's own settlement result for these markets, and
`scripts/edgelab/settle_markets.py` never populates
`game_outcome["kalshiOfficialResultsByTicker"]` (grep-verified — that
key is never set anywhere in this codebase). The conflict-detection
code path (`kalshi_mlb_result_conflict`: if a Kalshi result and the
MLB-derived result ever disagree, the settlement is left unresolved
with both preserved in evidence, never silently resolved one way) is
implemented and covered by unit tests that pass
`kalshi_official_result` directly — but this is prepared plumbing only.
It does **not** activate automatically merely because some future field
is added; capturing a real Kalshi official result and threading it
through `settle_markets.py`'s `game_outcome` would still require
deliberate future ingestion and orchestration work. No such data source
is invented or wired up in this PR.

## 7. Idempotency and corrections

Semantic (not just ID-level) idempotency: `storage.upsert_records`
prevents duplicate rows on its own, but the ORIGINAL implementation
still rewrote `createdAt`/`updatedAt`/`settledAt` (and a settled bet's
`updatedAt`) on every rerun, since a fresh wall-clock timestamp is
generated on every invocation regardless of whether anything actually
changed. The correction round fixes this so an identical rerun leaves
the canonical settlements/bets files **byte-for-byte unchanged**:

- `lib.edgelab.settlement.merge_settlement_record(existing, new)`
  compares every field EXCEPT `createdAt`/`updatedAt`/`settledAt` and
  `settlementEvidence`'s own `fetchedAt`/`sourcePayloadHash` (a fresh
  network fetch of byte-identical upstream data must never register as
  a difference). If nothing meaningful changed, it returns the
  `existing` record verbatim -- the exact same object, not just an
  equal one -- so `createdAt` is preserved, `settledAt` is preserved,
  and `updatedAt` is never touched
  (`test_exact_rerun_is_byte_for_byte_idempotent`). If something
  genuinely changed (a corrected authoritative statistic), it returns
  the freshly computed record with `createdAt` overridden back to the
  original -- the fact was first recorded when it was first recorded;
  only `updatedAt`/`settledAt` advance
  (`test_corrected_final_stat_updates_existing_record_and_bet_safely`).
- `lib.edgelab.settlement.bet_needs_settlement_update(original, computed)`
  does the analogous check for the bet ledger: a bet already carrying
  the exact status/result/netProfitLoss/returnAmount this settlement
  would produce is never rewritten, never gets a fresh `updatedAt`, and
  is never even passed to `storage.upsert_records`.
- `scripts/edgelab/settle_markets.py`'s `settle_date()` reports a
  `settlementsMeaningfullyChanged` count (0 on a true no-op rerun,
  distinct from `settlementsUpdated`, which still reflects the
  underlying `upsert_records` mechanics touching every row it's given)
  and a `betsSettled` count that is likewise 0 when nothing changed.
- A player-prop-focused backfill rerun of a date that also has
  unrelated game-level markets never churns those unchanged game-level
  settlements/bets either -- the merge logic is generic, not
  player-prop-specific
  (`test_player_prop_backfill_does_not_churn_unrelated_game_level_settlement`).

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
