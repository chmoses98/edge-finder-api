# W1-A — Canonical Settlement Semantics + Symmetric Bet-Side Resolution

**Status:** implemented, evidence attached, **NOT merged**. Money-touching
accounting logic; returns to CEO review.

**Baseline:** W1-C completion `614996505d2a54100eed4e164c158c04ba3093bd`.
Everything on `main` after that SHA is generated data/evidence only (verified:
all 4 commits touch `data/` exclusively), so this work proceeds from current
`main` with no substantive code, test, workflow, config or dependency change to
incorporate.

---

## 0. The governing invariant

> A WAGER MAY BE CALLED WON OR LOST ONLY WHEN THE EXACT CONTRACT, THE EXACT
> SIDE OWNED, AND THE TERMINAL TRUTH ARE ALL PROVEN.

Missing evidence is not permission to infer. Every resolver below returns a
refusal as a first-class value.

---

## 1. The settlement call / data-flow map, as discovered

```
ROOT LEDGER (bets.json, 565 rows)
  scripts/log_manual_bet.py            writes a wager  (betSide: free-form string)
  scripts/write_pending_bets.py        writes a wager  (betSide: AWAY/HOME/market name)
        |
        v
  clv_update.py  main()
        |-- normalize_market()             19 raw market spellings -> 8 canonical
        |-- get_betside()            [1]   -> AWAY / HOME / None
        |-- determine_result()       [2]   -> WIN / LOSS / PUSH / None        -> b['result'], b['pl']
        |-- settle_f5_bet_from_linescore() -> lib/f5_settlement.py            -> b['result'], b['pl']
        |-- fetch_kalshi_closing_price()   -> b['clv']
        |-- extract_closing()        [3]   DEAD -- defined, never called
        |
        +-> scripts/edgelab/repair_wager_backlog.py   (imports get_betside + determine_result)
        +-> scripts/audit_settlement_backlog.py       (classification only)
        +-> lib/bet_backlog_classifier.py             (classification only)

EDGELAB LEDGER (data/edgelab/bets/bets.jsonl, 457 rows)
  lib/edgelab/bets.py
        |-- _derive_side()           [4]   -> YES / NO
        |-- from_legacy_root_bets_record()
        |-- from_legacy_session_bets_record()
        |
        v
  scripts/edgelab/settle_markets.py
        |-- lib/edgelab/settlement.py::settle_market_full()
        |       |-- settle_market()                 -> (status, YES/NO, reason)
        |       |     |-- lib/research/inning_result_settlement.py   (F3/F5/F7, three-way)
        |       |     +-- lib/research/market_taxonomy.py            (families)
        |       +-- lib/edgelab/player_prop_settlement.py            (issue #43)
        |-- settle_bets_for_ticker()  [5]  -> bet.result / netProfitLoss
        |       +-- derive_bet_result()
        |       +-- lib/edgelab/execution_economics.py::realized_pl_for_bet()
        |-- build_settlement_record() / merge_settlement_record()   -> settlements/*.jsonl
        |
        +-> scripts/edgelab/reconcile_settled_bets_from_archive.py  (same two functions)
        +-> lib/edgelab/research_reports.py          [6]  (reads side for calibration)
        +-> lib/edgelab/exchange_settlement.py            (canonical vs Kalshi cross-check)

PRICING (not settlement — listed so the boundary is explicit)
  lib/edgelab/decision_side.py::resolve_side()   W1-B1. Which quote to price a
                                                 model number against. Already
                                                 refuses rather than defaults.
                                                 UNCHANGED by W1-A.
```

### Every bet-side resolver found, and its classification

| # | Implementation | Answers | Classification | W1-A action |
|---|---|---|---|---|
| 1 | `clv_update.get_betside()` | AWAY / HOME / None | **DUPLICATE AUTHORITY** — production, money path | Rewritten as a thin **ADAPTER** over the canonical module; name kept (4 importers) |
| 2 | `clv_update.determine_result()` | WIN / LOSS / PUSH | **CANONICAL (root ledger)** for score-based settlement | Four silent defaults removed; reads the canonical expression |
| 3 | `clv_update.extract_closing()` | AWAY/HOME + OVER/UNDER | **DEAD / UNREACHABLE** (one occurrence in the file: its own `def`) | Annotated as dead, **not** deleted; a test asserts it stays uncalled |
| 4 | `lib.edgelab.bets._derive_side()` | YES / NO | **DUPLICATE AUTHORITY** — wrote the side onto every legacy-ingested PlacedBet | Delegates to the canonical module; returns `None` + a reason instead of defaulting YES |
| 5 | `lib.edgelab.settlement.settle_bets_for_ticker()` | grade from side | **CANONICAL (EdgeLab)** | `bet.get("side") or "YES"` removed; refuses and records why |
| 6 | `lib.edgelab.research_reports._model_eligible_rows()` | reads side | **RESEARCH ONLY** | `r.get("side") or "YES"` removed; an unproven row is ineligible |
| 7 | `lib.edgelab.decision_side.resolve_side()` | YES / NO | **CANONICAL (B1 pricing)** — a different question | **Unchanged** |
| 8 | `scripts/backfill_market_identity.parse_bet_side()` | AWAY/HOME | **ADAPTER** (one-shot backfill, not in the settlement path) | Unchanged |
| 9 | `scripts/capture_closing_lines.normalize_side_abbr()` | team abbr | **ADAPTER** (closing-line capture) | Unchanged — W1-E territory |
| 10 | `scripts/fetch_kalshi_clv_v2.py:378` `betSide or side` | free string | **LEGACY COMPATIBILITY** — conflates orientation with selection | Quantified, not rewritten (W1-E) |
| 11 | `scripts/build_wager_research_db.py:156` `_first(bet,"betSide","side")` | free string | **RESEARCH ONLY** — same conflation | Quantified, not rewritten |
| 12 | `scripts/edgelab/repair_wager_backlog.py:236` | cross-checks `get_betside` | **ADAPTER** | Inherits the fix via `get_betside` |
| 13 | `scripts/data_quality_gate.py:162` | free string | **RESEARCH ONLY** (pre-bet gate) | Unchanged |
| 14 | `api/slate.js:1856` `betSideAway` | AWAY/HOME | **NOT A SETTLEMENT PATH** — picks which side to *recommend* | Unchanged |

**Now canonical:** `lib/wager_settlement_semantics.py`. It is the one answer to
“which end of which exact contract did this wager own, and what does its
settlement mean”. It does not re-parse tickers — W1-C’s
`lib/kalshi_mlb_contract_parser.py` and `lib/edgelab/market_identity.py` own
contract identity and the side/direction/horizon vocabulary, and both are
imported rather than duplicated (asserted by a test).

---

## 2. Canonical vocabularies

### Contract side (re-exported from W1-C, so there is one spelling of YES)
`YES` · `NO` · `None` (unproven — a real answer, never a default)

### Semantic expression (kept separate from the side, deliberately)
- **selection** — a club, from an enumerated abbreviation/name table, exact match only
- **direction** — `WIN` · `TIE` · `OVER` · `UNDER` · `EVENT_OCCURS` · `EVENT_DOES_NOT_OCCUR`
- **threshold** — the strike, converted to the contract’s own integer rung under the family’s convention
- **horizon** — `FULL_GAME` · `F3` · `F5` · `F7` · `FIRST_INNING` · `PLAYER_PROP`

### Orientation (a role in a matchup — **not** a contract side)
`AWAY` · `HOME`. Resolved to a club before it is allowed near a contract.
Meaningless for RFI and game totals, and never accepted as a side for them.

### Canonical wager outcome
| Outcome | Meaning | Persisted `result` | Persisted `status` |
|---|---|---|---|
| `WON` | terminal, the owned side paid | `WIN` | `settled` |
| `LOST` | terminal, it did not | `LOSS` | `settled` |
| `PUSH` | terminal, stake returned | `PUSH` | `settled` |
| `VOID` | venue cancelled the contract | `VOID` | `void` |
| `NOT_TERMINAL` | truth does not exist yet | `null` | `pending` |
| `UNRESOLVED` | **refused** — evidence insufficient or contradictory | `null` | `pending` + `settlementRefusalReason` |

No new persisted vocabulary was invented. Both ledgers already agree on
`WIN/LOSS/PUSH/VOID` (`lib.edgelab.bets._RESULT_ENUM`,
`placed_bet.schema.json`), and schema churn on a money ledger is its own risk.
The refusal state is carried **additively** in three new optional fields —
`sideResolutionBasis`, `settlementRefusalReason`, `settlementRefusalClass` —
precisely so an UNRESOLVED row is not indistinguishable from a pending one.

Kalshi MLB contracts are binary thresholds on integer quantities with
half-point or minimum-inclusive strikes, so **no Kalshi MLB family can push**
(`PUSH_CAPABLE_CONTRACT_CONDITIONS` is empty). `PUSH` survives in the
vocabulary because the root ledger carries 9 historical pushes from
sportsbook-style whole-number lines, and erasing a recorded push would be
rewriting history rather than normalizing it.

### Refusal vocabulary — three classes, never merged
- **`MISSING_EVIDENCE`** — we never recorded what we needed
- **`CONTRADICTION`** — two proven facts disagree
- **`DEFERRED`** — out of scope for automatic settlement (player props, combos)

“We never recorded the side” and “the row says AWAY while its ticker names the
home club” are different problems with different remedies. A single `UNKNOWN`
would hide the second inside the first. Every reason constant is mapped to a
class, and a test fails if any is left unclassified.

---

## 3. How YES and NO are resolved — and why they are symmetric

One statement, evaluated over `(contract condition, expression direction)`:

> the claim either **asserts** the contract’s own YES condition, or **negates**
> it.

- asserts → `YES`
- negates → `NO`
- neither provable → refuse

There is no branch that reaches `YES` with less evidence than `NO`, and no
field whose absence produces a side. A declared side on the row wins only if it
does not contradict the row’s own expression; where the expression cannot be
converted into a side at all (see the three-way rule), the declared side stands
and the resolution records `corroboratedByExpression: false` — inability to
corroborate is not a contradiction.

### The binary complement rule
A sibling contract’s claim may be read as this contract’s `NO` **only** where
the event space is positively stated to have exactly two outcomes:

| Condition | Horizon | Binary? |
|---|---|---|
| `TEAM_WINS` | `FULL_GAME` | **yes** — an MLB game plays to a decision |
| `TEAM_WINS` | `F3` / `F5` / `F7` | **no** — the segment can tie; Kalshi lists a separate `-TIE` contract |
| anything unlisted | — | **no** — absence of a recorded tie is not evidence there cannot be one |

Two separate things are kept apart here:

- **Same-ticker NO** — always well defined. “Under 8.5” is the `KXMLBTOTAL-9`
  contract’s own NO; `NRFI` is `KXMLBRFI`’s own NO. No cross-contract reasoning,
  so no tie can hide in it.
- **Cross-ticker complement** — only under the table above.

A *sibling club’s threshold* contract is never a complement in any family:
“DET scores 4+” is not the negation of “COL scores 4+” (both can be true), and
neither is “DET wins by 2+” of “COL wins by 2+”. Those refuse as
`CONTRADICTION`.

### NRFI / YRFI
`KXMLBRFI` is **one** contract per game. `YRFI` is its `YES` (a run scores),
`NRFI` is its `NO`. Tests assert they resolve to opposite sides of the *same
ticker object* and grade inversely from one settlement, in both directions.

### F3 / F5 / F7
`NO` on Team A’s period contract is **not** `YES` on Team B’s. Asserted for all
three horizons, and asserted as a *difference*: two rows identical except for
horizon must not get the same answer (`FULL_GAME` → `NO`, `F5` → refuse). The
explicit `-TIE` contract settles as itself, and a moneyline claim landing on it
is a `CONTRADICTION`.

---

## 4. What the old code actually did

Measured on the committed corpus, not described from memory — the audit script
imports the pre-W1-A `clv_update.py` out of git and runs its real functions.

| Site | The default | Effect |
|---|---|---|
| `determine_result` ML | `bet_side == winner` | `None` never equals `'AWAY'`, so an unproven side graded **LOSS** |
| `determine_result` Run Line | `if bet_side == 'HOME': … else:` | unproven side silently became **AWAY** |
| `determine_result` Total | `is_over = not is_under` | unproven direction silently became **OVER** |
| `determine_result` Team Total | `is_away_side = … or away_abbr in bet_str` | falsy silently became **HOME** |
| `determine_result` Team Total | `is_over = 'OVER' in bet_str or '+' in bet_str` | falsy silently became **UNDER** |
| `determine_result` Team Total | `re.search(r'(\d+\.?\d*)\s*$', bet)` | strike scraped off the end of prose — `'Sale K Over 8'` yields a total of 8 |
| F5 settlement | `settle_f5_bet_from_linescore(b, game_pk, bet_side or 'away')` | **an unproven side was graded as if it bought AWAY**, against real linescore truth, written to `pl` |
| `get_betside` | `if ta: return 'HOME'` | any string `to_abbr` didn’t recognise became HOME (and `to_abbr` never fails — “last resort: first 3 chars uppercased”) |
| `get_betside` | `away_abbr in bet_str` | substring of free prose — **42 archived YRFI wagers labelled `'AWAY'`** because `"LAA/TB YRFI"` contains `"LAA"` |
| `settle_bets_for_ticker` | `bet.get("side") or "YES"` | a null side graded at the wrong end of the book |
| `_derive_side` | `"NO" if "NRFI" in name else "YES"` | every Under / NO-side total / NO-side team total called YES (40 such rows exist) |
| `research_reports` | `r.get("side") or "YES"` ×2 | model calibration scored against an invented side |

`get_betside` returned `None` on **203 of 565 rows**, and **175 of those 203**
are already graded WIN or LOSS — i.e. graded by one of the defaults above.

---

## 5. Reading free text without guessing

`read_bet_string()` parses the root ledger’s semi-structured `bet` column
(416 rows, 290 distinct spellings) **whole or not at all**:

- every token must be in the controlled vocabulary; **one** unreadable token
  makes the whole string unreadable;
- two distinct clubs means the string identifies the **game**, not a side, so no
  selection is established (`'LAA/TB YRFI'`, `'MIN/PIT Under 8'`);
- a number is read as a strike, never dropped — `'PIT Over 4'` without the 4 is
  a different bet;
- a **negative** number is a signed handicap whose sign convention this
  repository has never written down (W1-B1 refuses run lines for the same
  reason), so it is recorded and never converted into a strike;
- player-prop strings return nothing: `'Sale K Over 8'` must not yield a total
  of 8.

The club table is an **enumerated lookup**, not a matcher. What is absent is
what made `clv_update.to_abbr()` dangerous: its fuzzy pass, which returned a
confident abbreviation for any string at all (`'Over'` → `'OVE'`, a surname → a
club). Ambiguous names are deliberately absent — `'SOX'` is two clubs and
resolves to nothing.

---

## 6. Blast radius (`scripts/audit/w1a_settlement_semantics_audit.py`)

Both semantics run over the same rows, against each row’s **own recorded final
score**, so every difference is a difference in semantics and never in the
evidence.

### Bet-side orientation, old → new (565 rows)

| old → new | rows |
|---|---|
| `AWAY → AWAY` | 245 |
| `HOME → HOME` | 65 |
| `None → None` | 83 |
| `None → HOME` | 120 *(newly proven)* |
| `AWAY → None` | 52 *(newly refused — the old code was inferring)* |
| **hard flips (both confident, disagree)** | **0** |

The 52 newly-refused break down as **42 YRFI**, 7 `Total`, 2 `NRFI`, 1
`Team Total` — every one a market with no away/home side to own, which the old
code nonetheless called `'AWAY'` from a substring of the free-text bet string.

The 120 newly-proven are `ML` 41, `F5 ML` 35, `Team Total` 26, `Run Line` 12,
unrecognised-market 6 — rows whose club was recorded only in the `bet` column
and is now read by full tokenization instead of a substring scan.

### Settlement grade, old vs new (92 rows with a recorded final score)

| | count |
|---|---|
| identical | 80 |
| **changed grade** | **4** |
| newly resolved | 7 |
| newly refused | 1 |

### Agreement with the committed ledger

| | matches the ledger | grades it **differently** |
|---|---|---|
| OLD semantics | 59 | **3** |
| NEW semantics | 67 | **1** |

All 7 newly-resolved rows match the committed ledger exactly, **including P/L
to the cent** — the increase came from parsing recorded evidence, not from
weaker inference.

The single newly-refused row is `2026-06-03-062`, `'Sox TT Under 3.5'` in the
game `'Sox @ Twins'`. **`Sox` is two clubs.** The old code picked one; refusing
is the correct answer to an ambiguous question, and whether the ledger's
recorded `LOSS` is right cannot be determined from the row.

### Canonical CONTRACT side across the root ledger (565 rows)

| | rows |
|---|---|
| proven `YES` | 124 |
| proven `NO` | 0 |
| refused | 441 |

Of the 441 refusals, **429 are `SIDE_UNPROVEN_NO_MARKET_TICKER_ON_THE_ROW`** —
only 128 of 565 root rows carry a ticker at all, so 76% of the root ledger
cannot be bound to an exact Kalshi contract and therefore has no provable
*contract* side. Those rows still settle through the score-based path
(`determine_result`), which needs an orientation and a direction rather than a
contract. The remaining refusals are 8 player props (#43) and 4 tickers whose
series has no described contract grammar.

There are **0 proven `NO` rows in the root ledger** because no ticketed root
wager expresses a negation — the 10 rows that do say `(Kalshi NO)` in prose
carry no ticker and refuse at the contract gate. The EdgeLab ledger carries 40
`NO` rows, and the NO path is covered there and by the adversarial matrix.

### Doubleheader exposure
128 root rows carry a ticker; **3 (date, matchup) pairs resolve to more than one
Kalshi event suffix**. A row with no ticker cannot be bound to a leg by date and
clubs alone, and is refused rather than bound.

The single row the new semantics grade differently from the ledger is the one
where the **ledger carries the old code’s error** (§7).

---

## 7. Historical discrepancy classification

| Class | Rows | Detail |
|---|---|---|
| **SEMANTICALLY REPAIRABLE FROM EXISTING EVIDENCE** | 8 | 1 proven mis-grade + 7 rows the old settler declined and the new one grades, all 7 matching the ledger exactly |
| **AMBIGUOUS / CONTRADICTORY** | 4 | 3 root-ledger-vs-exchange contradictions, + 1 row naming a club (`Sox`) that is two clubs |
| **NEWLY RESOLVABLE (exchange settled, ledger still pending)** | 3 | terminal on the exchange, still `pending` on the ledger |
| **SOURCE DATA ABSENT** | 429 | no `marketTicker` on the row → no contract → no provable contract side |
| **OUT OF CORPUS WINDOW** | — | EdgeLab settlements begin 2026-08-02; wagers begin 2026-05-26, so the earliest wagers have no exchange row to reconcile against. **No earlier provenance was invented for them.** |
| **PLAYER PROP / #43** | 53 | 45 EdgeLab + 8 root; deferred, never graded |
| **OTHER — multi-market combo** | 14 | no single contract side exists for a parlay |
| **OTHER — undescribed series** | 4 | ticker whose series has no contract grammar |

Across both ledgers the read-only rehearsal refuses 770 of 1022 wagers:
**437 `MISSING_EVIDENCE`, 59 `DEFERRED`**, and the balance refused at the
settlement stage rather than the side stage. No refusal was converted into a
grade, and no `gamePk`, ticker, side, price or outcome was reconstructed from a
guess anywhere in this subwave.

### The one proven mis-grade

`2026-06-02-COL-LAA-TT-HOME-OVER` — `betSide: "HOME OVER"`, `line: 4.0`,
final 9–8, so the home club (LAA) scored 8.

- The row’s own `betSide` says **OVER**. 8 > 4 ⇒ **WIN**.
- The old code read the direction from `bet`, which is `null` on this row, so
  `is_over` was `False` and it graded **UNDER** ⇒ LOSS.
- Ledger records `LOSS`, `pl: -7.00`. Correct is `WIN`, `+6.25`. **A $13.25
  error**, provable from the row’s own recorded fields.

### Root ledger vs exchange settlement (read-only rehearsal)

| Wager | Ticker | Root ledger | Exchange settlement | Canonical chain |
|---|---|---|---|---|
| `2026-09-07-174` | `…CHCMIL-CHC4` | `WIN` | `NO`, realized **−3.44** | `LOST` |
| `2026-09-07-175` | `…CHCMIL-MIL4` | `PUSH` | `YES`, realized **+2.50** | `WON` |
| `2026-09-10-180` | `KXMLBF5-…PITCWS-CWS` | `pending` | `NO` | `LOST` |

The first two are the same game, and the ledger has them **swapped**. The third
is terminal on the exchange and still open on the ledger. A Kalshi team total at
an integer rung **cannot push**, so `2026-09-07-175`’s recorded `PUSH` is
independently suspect.

**Nothing above is rewritten by this PR.** Every one is reported with its
provenance and returned for CEO adjudication. W1-A writes no historical
correction.

---

## 8. P/L and CLV

- P/L derives from the canonical wager plus canonical terminal settlement
  semantics. `calc_pl` / `realized_pl_for_bet` are untouched — only the
  *result* feeding them is now proven rather than defaulted.
- **No committed P/L figure is changed by this PR.** The audit’s
  `hypotheticalPlDelta` (**+$30.31** across the rows whose grade differs) is
  what the two semantics *would* produce if each differing row were re-graded
  from its own recorded final score. It is a measurement, not a restatement.
- CLV side interpretation: the live Kalshi CLV path is
  `fetch_kalshi_closing_price()`, which is keyed on the **ticker**, not on a
  side, so it carries no side ambiguity. The Odds-API-era `extract_closing()`,
  which did (four defaults), is dead and is now annotated and test-guarded as
  dead.
- Two consumers still read `betSide or side` as if the two columns were
  interchangeable (`scripts/fetch_kalshi_clv_v2.py:378`,
  `scripts/build_wager_research_db.py:156`). They are **different concepts** —
  an orientation and a selection — and they differ on **111 of the 116 rows**
  that carry both. Quantified here; **not** rewritten, because repairing the
  historical CLV corpus is W1-E.

---

## 9. Idempotence

Proven by test:

- resolving the same row twice returns a byte-identical verdict;
- settling the same bet twice does not flip the result or double-count P/L, and
  `bet_needs_settlement_update` reports `False` on the second pass;
- **re-refusing an unproven bet is also a no-op** — a refusal must be as
  idempotent as a grade, or every nightly run rewrites the same row with a
  fresh `updatedAt` forever;
- resolution never mutates the row it reads.

---

## 10. Scope

**Not started, not touched:** W1-D (duplicate JS/Python decision authority),
W1-E (historical closing-line/CLV reconstruction), Wave 2 (predictive edge).

**Unchanged:** model probabilities, calibration, edge thresholds, confidence
tiers, Bet Up To, staking, bankroll, portfolio rules, fee calculations, market
selection, pricing, B1/B2 executable-price truth, W1-C game/contract identity.

**Model-driven real-money authority remains OFF.** Nothing in this subwave
touches `risk_gate.py`, `write_pending_bets.py` or `validate_slate_final.py`,
and no wager is created anywhere in it.
