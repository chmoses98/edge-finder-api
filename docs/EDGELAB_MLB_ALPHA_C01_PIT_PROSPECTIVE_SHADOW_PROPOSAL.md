# C01-PIT prospective shadow — PROPOSAL (research only, not implemented)

**Status: PROPOSAL following an INCONCLUSIVE blind holdout.** Nothing here
is implemented in this session, no production behaviour changes, and no
real-money activation is proposed.

## Why forward collection is the only remaining move

The blind holdout returned **INCONCLUSIVE** on its pre-registered floor:
17 independent games against the 30 required. The binding constraint was
**capture cadence, not the strategy** — two of six dates produced zero
qualifying quotes inside `[T-60, T-0)`.

The historical archive cannot fix this. Widening the window, lowering the
floor, or relaxing the price band to manufacture sample would all be
tuning a frozen rule after seeing its result, and are refused. The six
holdout dates are **SPENT** and can never be reused.

## The rule — unchanged, byte for byte

Rule sha256 `882f16d8330af1af12aec928a561302bfe81de6a5e5716a3a7fa352bc048376b`:

- universe `KXMLBF5TOTAL` (`marketFamily = inning_total`)
- side **BUY YES** at the archived executable `yesAsk`
- band **90–99 cents inclusive**
- entry: **FIRST** qualifying ACTIVE quote inside **[T-60 min, T-0)**
- settlement: Kalshi **AT_LEAST_N** (rung N pays YES iff F5 runs ≥ N)
- order: **$10 taker, whole contracts**

No modification to market family, side, price band, entry window, or the
FIRST-quote rule. Any change makes it a different candidate needing its
own discovery.

## Per-opportunity capture schema

`exactTicker`, `eventTicker`, `gameId`, `threshold`, `capturedAt`,
`minutesToStart`, `yesBid`, `yesAsk`, **`askSize`/`bidSize`/book depth if
and only if Kalshi exposes them**, `volume`, `openInterest`, `spreadCents`,
`entryExecutableCents`, official **exchange** settlement, **canonical**
settlement, executable CLV, fair-mid CLV, fees, and hypothetical $10
whole-contract P/L — all under the canonical positive-is-good CLV
convention (`closing − entry`).

Depth is the one field the historical archive never had, which is why a
$10 fill has only ever been `TOP_OF_BOOK_PRICE_OBSERVED`, never proven.
Capturing it prospectively is the only way that claim can ever be upgraded.

## Two capture fixes the holdout exposed (forward-only)

1. **Near-start capture cadence.** The rule needs an observation inside
   `[T-60, T-0)`; on 2 of 6 holdout dates none existed. A dedicated
   in-window poll is required or the shadow will accrue sample as slowly
   as the holdout did.
2. **Doubleheader identity.** Kalshi appends `G1`/`G2` to the event
   suffix; the frozen parser refuses those (correctly — it never guesses),
   which silently dropped 4 events. The prospective collector should
   resolve `G1`/`G2` explicitly.

Both are **capture** changes affecting only future rows. Neither may be
retro-applied to the spent holdout.

## Pre-registered checkpoints — fixed BEFORE any result arrives

| Checkpoint | Requirement |
|---|---|
| **First material review** | **100 independent games AND 10 independent dates** |
| **Stronger review** | **200 independent games AND 20 independent dates** |

No smaller threshold may be introduced once results begin accumulating,
and no interim peek constitutes a decision. Reaching a checkpoint
authorizes a **review**, never a wager.

## Exchange settlement cross-check is a prerequisite

The shadow must run alongside the immutable settled-Kalshi capture
designed in `docs/EDGELAB_SETTLED_KALSHI_CAPTURE_DESIGN.md`, so every
prospective row carries **both** exchange truth and our canonical grade.
On mismatch: **quarantine the affected research row and alert — never
silently overwrite.** This program has already found two grading defects
that were invisible precisely because exchange truth was never stored.

## Explicitly out of scope

No production recommendation, staking, eligibility or risk-gate
integration; no real-money activation; no modification of the frozen rule;
no reuse of the spent holdout dates.
