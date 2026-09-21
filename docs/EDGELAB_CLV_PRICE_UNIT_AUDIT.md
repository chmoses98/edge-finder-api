# EdgeLab CLV 100x Price-Unit Audit (2026-09-21)

Companion to `docs/EDGELAB_CLV_SIGN_AUDIT.md`. That audit fixed CLV's
**sign**; this one fixes CLV's **scale**. They are independent defects on
the same field.

## Symptom

Canonical bet rows from mid-September 2026 carry closing prices two orders
of magnitude too small, and a CLV computed against them:

| betId (prefix) | side | entryPrice | closingPrice | stored clv |
|---|---|---|---|---|
| `997fd52d6c` | NO | 0.72 | 0.0066 | -71.34 |
| `bb73efa113` | YES | 0.48 | 0.0048 | -47.52 |
| `003d2c486e` | YES | 0.98 | 0.0066 | -97.34 |

Daily and rolling reports consequently showed roughly -45 to -53 cents
average CLV with virtually every wager negative — a number that would
imply the book moved almost the entire contract against every position
taken.

## Root cause

`lib/edgelab/clv.py::_executable_closing_implied()` divided every ClvQuote
price by `100.0`, i.e. it assumed the quote was denominated in **integer
cents**. Nothing in the ClvQuote record stated the unit, so the assumption
was never checked.

That assumption held only while it happened to be true. The Kalshi
registry snapshots this repository captures carry a `price_source_fields`
map naming the exact API field each price came from. Measured across the
archive:

| snapshot date | price fields declared | observation scale |
|---|---|---|
| 2026-09-01 … 2026-09-09 | undeclared (legacy integer-cent fields) | CENTS (max 100) |
| 2026-09-10 | mixed (3 of 5 snapshots declared) | CENTS |
| 2026-09-11 … 2026-09-20 | **declared `*_dollars`** | **PROBABILITY (max 1.0)** |

From 2026-09-11 the snapshots emit Kalshi's `*_dollars` fixed-point
fields, so `lib/edgelab/market_universe.py` writes MarketObservation — and
`project_observations_to_clv_quotes()` therefore writes ClvQuote — prices
as 0-1 probabilities. The consumer was never told. A 0.66 NO ask became a
0.0066 closing probability.

This is precisely the failure mode `lib/edgelab/price_units.py` (W1-B2)
was written to end — *the unit is a property of the FIELD, not of the
value* — reintroduced at a boundary that module never covered.

## Which side of the comparison is wrong

The **closing** price, not the entry price. `entryPrice` is independently
corroborated by cash arithmetic on every receipt-imported row:

```
entryPrice == contractCost / contracts     101 / 101 rows exact, 0 mismatches
```

`997fd52d6c`'s own archived closing quote reads `yesBid 0.34 / yesAsk 0.37
/ noBid 0.63 / noAsk 0.66` — a coherent 3-cent book on the 0-1 scale. The
NO-side executable close is 0.66, so the true CLV is **-6.00 cents**
against a 0.72 entry, not -71.34.

## Scope

**94 wagers, 2026-09-11 through 2026-09-20.** Recomputing every stored CLV
from its own archived `clvQuoteId` with correct unit handling:

| | stored | corrected |
|---|---|---|
| mean CLV (cents) | -11.42 | +0.04 |
| negative CLV | 191 / 421 | 122 / 421 |
| rows materially changed | — | 94 |

Per date: 09-11: 8, 09-12: 15, 09-13: 1, 09-14: 5, 09-15: 16, 09-16: 10,
09-17: 7, 09-18: 8, 09-19: 9, 09-20: 15. Rows before 2026-09-11 are
unaffected — their `/100` was correct.

## Fix applied

The producer now DECLARES the unit and the consumer HONORS it:

- `project_observations_to_clv_quotes()` stamps `priceUnit: "PROBABILITY"`
  on every ClvQuote it emits.
- `clv_convention.executable_price(quote, side, unit)` replaces the
  hardcoded `100.0 - yesBid` NO-side derivation with the value of one
  contract *in the stated unit*. `executable_price_cents()` is retained as
  a `UNIT_CENTS` wrapper; its behavior is unchanged.
- `_executable_closing_implied()` reads the declared unit and converts via
  `clv_convention.convert()`.
- A quote with **no** declared unit is now `UNAVAILABLE /
  CLOSING_QUOTE_PRICE_UNIT_UNDECLARED` — it **fails closed** rather than
  guessing. Guessing is what produced the defect, and the undeclared case
  gets its own reason so it can never again masquerade as a merely absent
  quote.

One further behavior change came with routing YES through
`clv_convention.executable_price()`: the old code fell back to `yesBid`
when `yesAsk` was absent, i.e. it used the *bid* as a YES buyer's price.
That contradicts the same module's standing rule that a non-tradable
price is never substituted, and it is dead code in practice — across
**184,270 archived closing quotes, 0** have `yesAsk` absent with `yesBid`
present. Such a quote is now `CLOSING_QUOTE_MISSING_EXECUTABLE_PRICE`.

`scripts/edgelab/collect_clv.py` leaves a bet's existing `clv` untouched
when the result is `UNAVAILABLE`, so this change **rewrites no historical
value**. Archived rows keep their (defective) numbers until the migration
below runs.

Regression tests: `tests/edgelab/test_clv.py` (`test_clv_quotes_declare_their_price_unit`,
`test_probability_denominated_close_is_not_divided_by_100_again`,
`test_no_side_derived_from_yes_bid_respects_the_declared_unit`,
`test_same_book_in_either_unit_yields_identical_clv`,
`test_undeclared_price_unit_fails_closed_and_never_fabricates_a_number`,
`test_unrecognised_price_unit_is_rejected_not_coerced`).

## Can history be repaired deterministically, without hindsight leakage?

**Yes — and the evidence for it is already archived.** Two facts establish
this:

1. **Every affected row's closing quote still exists.** All 421 bets
   carrying a `clvQuoteId` resolve against the archived `clv_quotes`
   partitions (0 missing).
2. **The unit is recoverable from declared evidence, not inferred.** Each
   ClvQuote's `provenance.sourceFile` names the snapshot it came from, and
   every snapshot from 2026-09-11 onward carries `price_source_fields`
   naming `yes_bid_dollars` / `yes_ask_dollars` / etc. The unit is read
   from that declaration — the same rule `price_units.py` already applies.

**No hindsight leakage.** The repair changes only the unit conversion. It
does not re-select the closing quote, does not widen what counts as a
valid pre-close quote, and does not consult anything captured after the
quote's own `capturedAt`. `isClosingQuote` is left exactly as
`finalize_closing_quotes()` originally set it.

### Why the migration is NOT run in this change

A second, independent defect makes the repaired numbers not yet
interpretable as closing-line value: **57 of the 94 affected rows have
their "closing" quote at the `FIRST_DAILY` checkpoint** — the earliest
snapshot of the day, often many hours pre-game — rather than a genuine
pre-close quote:

```
FIRST_DAILY 57 | T_MINUS_90 11 | T_MINUS_15 12 | T_MINUS_30 5
T_MINUS_5 4 | T_MINUS_60 2 | INTERMEDIATE 3
```

That is why so many corrected values land on exactly 0.00: the wager was
priced against the same first-daily quote it is being scored against.
Rewriting the scale alone would replace an obviously-wrong number with a
plausible-looking one that still is not closing-line value, which is the
more dangerous of the two states.

**Recommended sequence:** (1) close the closing-quote coverage gap; (2)
run the unit migration below; (3) only then interpret CLV.

### Migration design (when run)

Follow `scripts/edgelab/migrate_clv_sign.py`'s receipt pattern:

1. For each ClvQuote partition, read `provenance.sourceFile`; take the
   unit from that snapshot's `price_source_fields` for the row's ticker.
   **Refuse** — never assume — any row whose snapshot is missing or whose
   fields are undeclared.
2. Backfill `priceUnit` onto the archived ClvQuote row.
3. Recompute CLV for every bet via the unmodified `compute_clv_for_bet()`,
   keyed on the bet's existing `clvQuoteId`.
4. Write a receipt recording every changed row's before/after.

Idempotent by construction: a second run finds `priceUnit` already
declared and recomputes identical values.
