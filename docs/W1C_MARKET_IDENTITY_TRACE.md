# W1-C — the market identity chain, traced before any edit

Written before touching production code, in the same spirit as
`docs/W1B2_PRICE_PATH_TRACE.md`. B2 proved *what a contract costs*. This traces
*which contract it is*, and finds the place where that question is currently
answered by a team-pair string.

The chain production must be able to prove:

```
PHYSICAL MLB GAME → KALSHI EVENT → EXACT CONTRACT
  → FAMILY/HORIZON → SELECTION/TEAM/DIRECTION → THRESHOLD/STRIKE → SIDE
```

---

## 1. Every current identity representation

| representation | where created | what it actually identifies | unique per physical game? |
|---|---|---|---|
| `gameId` (slate) | MLB Stats API ingestion | **the MLB gamePk** (6-digit, e.g. `824912`) | **YES — authoritative** |
| `gamePk` (EdgeLab) | `lib/edgelab/mlb_schedule.py`, observation rows | same integer as `gameId` | **YES** |
| `kalshiKey` | `build_kalshi_registry.py:512`, `merge_odds.py:562` | `f"{away}{home}"` — **team pair only** | **NO** |
| `event_ticker_suffix` | `build_kalshi_registry.py` `parse_suffix()` | `26JUL111605MILPIT` — date+time+teams | yes *if* the time differs per leg |
| `eventTicker` | Kalshi API / `kalshisearch.js` | `KXMLBRFI-26SEP092210CINLAD` | yes (exchange event) |
| `market_ticker` | Kalshi API | `KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4` | **yes — the exact contract** |
| `seriesTicker` | ticker prefix | family only (`KXMLBGAME`, `KXMLBTEAMTOTAL`…) | no — family, not identity |
| `time_str` / `game_time_et` | `parse_suffix()` | the Kalshi event's HHMM | **the leg discriminator — parsed, then discarded** |
| `kalshiGameTime` | `merge_odds.py` | display copy of the above | no |
| `scheduledStartTime` | slate | MLB scheduled first pitch | leg discriminator on the MLB side |
| `doubleheaderGameNumber` | `kalshi_mlb_contract_parser.parse_contract` | leg number when the ticker encodes one | when present |
| synthetic `"<gamePk>:<family>"` | `observation_join.py` | a decision row, not a contract | n/a |

### Which are which

* **Physical-game identity:** `gameId`/`gamePk`, `scheduledStartTime`.
* **Exchange-event identity:** `eventTicker`, `event_ticker_suffix`, `time_str`.
* **Contract identity:** `market_ticker` (+ family, selection, direction, strike, side).
* **Lookup/display conveniences only:** `kalshiKey`, `kalshiGameTime`, `seriesTicker`.

---

## 2. Where date+teams is still treated as unique — the CR-3 root cause

### 2a. The registry is keyed by the team pair, and the second leg overwrites the first

`scripts/build_kalshi_registry.py`:

```python
for suffix in sorted(event_suffixes):
    time_str, away, home = parse_suffix(suffix)   # time_str IS the leg discriminator
    kalshi_key = f"{away}{home}"                  # ... and is then thrown away
    ...
    registry[kalshi_key] = entry                  # L512 — SILENT OVERWRITE
```

Two legs of a doubleheader produce two distinct `event_ticker_suffix` values with
two distinct `time_str` values — and **the same `kalshi_key`**. The second
iteration overwrites the first. No warning, no collision check. The registry
physically cannot represent both legs.

`parse_suffix` extracts `time_str` one line earlier. The information needed to
tell the legs apart is in hand at the moment it is discarded.

### 2b. The slate→registry join is first-match over an unordered set

`scripts/merge_odds.py`:

```python
def find_registry_entry(away_full, home_full, away_abbr, home_abbr, registry):
    candidates = set()                       # <- a SET: iteration order is arbitrary
    for a in [away_abbr, to_abbr(away_full)]:
        for h in [home_abbr, to_abbr(home_full)]:
            candidates.add(f"{a}{h}")
    for key in candidates:
        if key in registry:
            return registry[key]             # <- FIRST MATCH WINS
    return None
```

No date. No start time. No gamePk. Both legs of a doubleheader are handed the
**same** registry entry. This is on the money path: `merge_odds` → `slate.json`
→ `build_market_ledger` → `bets.json`.

### 2c. The two existing doubleheader resolvers do not protect this path

`scripts/discover_kalshi_mlb_markets.resolve_game_match` and
`scripts/build_hitter_projection_board._resolve_doubleheader_market` both use
`lib/kalshi_ticker_time.closest_by_hhmm`, which **already returns an
`is_unique` flag** and already refuses to hide a tie. That is good design — but:

* neither resolver is used by `merge_odds.py` or `build_market_ledger.py`;
* they operate on the *discovery* and *hitter board* paths;
* `closest_by_hhmm` needs per-leg candidate times, and §2a has already
  collapsed the registry to one entry per team pair, so on the money path there
  is nothing left to disambiguate **with**.

W1-C must therefore fix identity at the registry and the join, and reuse
`closest_by_hhmm`'s existing uniqueness semantics rather than inventing a third
resolver.

---

## 3. Reproduced contamination (archived evidence, unmodified)

Read out of `data/slates/`, which W1-C does not rewrite.

### 2026-06-17 SF @ ATL — `data/slates/2026-06-17/official_20260617T181908Z.json`

| gamePk | kalshiKey | kalshiGameTime | ledger rows | distinct tickers |
|---|---|---|---|---|
| **824912** | `SFATL` | `7:15 PM ET` | 11 | 7 |
| **824913** | `SFATL` | `7:15 PM ET` | 11 | **0** |

Two physically distinct games, one shared team-pair key, and **the same event
time asserted for both**. Leg 2 lost the overwrite race and ended with no
contracts at all. This is the case that reached real-money-tier rows.

### 2026-07-11 MIL @ PIT — `data/slates/2026-07-11/official_20260711T211016Z.json`

| gamePk | kalshiKey | kalshiGameTime | ledger rows | distinct tickers |
|---|---|---|---|---|
| **823357** | `MILPIT` | `4:05 PM ET` | 11 | 2 |
| **823356** | `MILPIT` | `4:05 PM ET` | 11 | 3 |

**Two market tickers are attached to BOTH physical gamePks:**

```
KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4
KXMLBTEAMTOTAL-26JUL111605MILPIT-PIT3
```

One exact Kalshi contract, claimed by two different physical baseball games.
At most one of those claims can be true.

Note both legs also carry the *same* `kalshiGameTime`. The time discriminator
was not merely unused — it was overwritten, so nothing downstream could have
recovered the distinction.

---

## 4. Where strike / direction / horizon are lost

B2 deliberately left these fail-closed. Measured on the committed slate: every
family's ledger rows carry `marketTicker: null`, `threshold: null`,
`selection: null`, `direction: null`, `team: null`.

`build_market_ledger.py` has an `identity()` helper returning
`marketTicker`/`ticker`/`seriesTicker`/`eventTicker`/`scheduledStartTime`, and
`make_row` whitelists `marketTicker` and `line` — but:

* `marketTicker=` is passed at **one** call site;
* `line=` is passed at **two** (team totals only);
* run line carries no team and no win-margin threshold;
* game total carries no OVER/UNDER direction and no integer threshold;
* F3/F5/F7 carry no explicit horizon field;
* NRFI/YRFI rely on family name alone to imply the side.

So the plumbing exists and is almost entirely unused. W1-C should fill it, not
replace it.

---

## 5. What W1-C must therefore change

1. **Registry key** — stop keying authoritative state by `away+home`. Collision
   must be impossible by construction or detected and refused loudly. Never a
   silent overwrite.
2. **Slate→registry join** — resolve by gamePk where available; otherwise by
   provable schedule evidence (teams + date + start time + leg number), with
   ambiguity failing closed. Remove first-match-over-a-set.
3. **Contract identity** — populate ticker, family, horizon, selection/team,
   direction and threshold on every actionable row; refuse when absent.
4. **`kalshiKey`** — demoted to a display/lookup hint; never authoritative
   physical-game identity on the money path.
5. **Compose with B2** — identity unproven must fail closed *even when a
   perfectly good book exists*. A price may not rescue an unknown contract.

Out of scope and deliberately untouched: settlement normalisation (W1-A),
duplicate decision authority and the external gates (W1-D), historical CLV
contamination and `capture_closing_lines.py` (W1-E), model quality (Wave 2).

---

## 6. Addendum — what "contract identity" had to mean in the end

Point 3 above says "populate ticker, family, horizon, selection/team, direction
and threshold on every actionable row; refuse when absent." That was carried
out, and it turned out not to be enough. **Populated is not verified.** A row
can name the right ticker and carry a complete set of fields belonging to a
different contract:

```
marketTicker = KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4
selection    = PIT            <- the other team in the same game
threshold    = 7              <- a real rung, on a different ladder
direction    = OVER
side         = YES
```

Nothing is missing there, so no completeness rule can see it. The exchange's
own contract has to be read and compared.

### The convention, and where it is proven from

`lib/kalshi_mlb_contract_parser.parse_contract_condition` turns the market
suffix into a structured condition. The rule it encodes is uniform and is taken
from archived real markets with Kalshi's own titles
(`data/kalshi/discovery/2026-09-10.json`), never from a repository belief:

| ticker | Kalshi's own wording | canonical condition |
|---|---|---|
| `KXMLBGAME-…-PIT` | "Pittsburgh wins" | `TEAM_WINS` PIT |
| `KXMLBF5-…-PIT` | "Pittsburgh first 5 innings winner" | `TEAM_WINS` PIT, horizon F5 |
| `KXMLBF5-…-TIE` | "first 5 innings tie" | `PERIOD_TIE` |
| `KXMLBSPREAD-…-PIT2` | "Pittsburgh wins by over 1.5 runs?" | `TEAM_WIN_MARGIN_AT_LEAST` PIT **2** |
| `KXMLBTOTAL-…-9` | "Over 8.5 runs scored" | `COMBINED_RUNS_AT_LEAST` **9** |
| `KXMLBF5TOTAL-…-7` | "First 5 innings: Over 6.5 runs" | `COMBINED_RUNS_AT_LEAST` **7**, horizon F5 |
| `KXMLBTEAMTOTAL-…-PIT4` | "Will Pittsburgh score over 3.5 runs?" | `TEAM_RUNS_AT_LEAST` PIT **4** |
| `KXMLBRFI-…` | "1st inning: Over 0.5 runs" | `FIRST_INNING_RUNS_AT_LEAST` **1** |

**The integer in a suffix is always the minimum inclusive outcome**, worded as
"over N−0.5". `tests/test_w1c_contract_claim.py` re-derives that minimum from
the archived *titles* and asserts the parser agrees, across every archived
market that states a threshold — so a Kalshi wording change surfaces loudly
instead of silently repricing a ladder.

### Why the numerals are never compared

The ledger's own families disagree about which of the two numbers they carry:

| family | ledger field | carries | convention |
|---|---|---|---|
| `GAME_TOTAL` | `kalshi.total.line` | `N` | `MINIMUM_INCLUSIVE` |
| `TEAM_TOTAL` | `kalshi.team_totals.*.line` (`over_n`) | `N` | `MINIMUM_INCLUSIVE` |
| `RUN_LINE` | `kalshi.rl.wins_by_over` | `N − 0.5` | `HALF_POINT_BELOW` |

On the real 2026-09-10 board, `KXMLBSPREAD-26SEP101905COLNYY-NYY4` is claimed
with threshold `3.5`. A raw comparison would refuse a correct contract; worse,
treating every number as the integer would let a team-total claim of `3.5`
match a `-NYY4` contract. Both sides are normalized to `minimumInclusive`
first (`market_identity.normalize_ledger_claim`), and only then compared
(`compare_contract_claim`).

### Vocabulary

* `IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH` — a positive contradiction. The
  fields are present and they are the wrong contract's.
* `IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNPARSEABLE` — the exchange side could
  not be read, so there is nothing to check against.

Neither is folded into `NO_CONTRACT`, `UNKNOWN_FAMILY` or the generic
`INCOMPLETE`: those all say *something is missing*, and here nothing is.

### The one exemption, and why it cannot leak

Player-prop series (`KXMLBKS/OUTS/HIT/TB/HRR/RBI/SB`) have no described
grammar — their suffix carries a player token this subwave does not undertake
to resolve. `resolve_contract` records that on the identity
(`contractClaimVerified: false`, reason `SERIES_NOT_DESCRIBED`) rather than
passing silently, and a test asserts that every series literal appearing in a
`build_market_ledger.identity(...)` call has a described grammar and is not a
research-only one. The exemption is therefore unreachable from any row that can
become actionable.
