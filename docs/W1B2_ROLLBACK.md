# W1-B2 rollback — restoring pre-cutover pricing without touching history

B2 changes what price gates real money, so it needs a way back that does not
depend on anyone reasoning correctly under pressure.

## The shape of the rollback: revert the commit, nothing else

**There is no runtime pricing toggle, and that is deliberate.** A flag that
selects between "canonical executable price" and "midpoint" would leave two
competing price authorities alive in the codebase indefinitely, which is the
condition W1-D exists to remove for the decision engines and which would be
worse here, on the money path, than the defect B2 fixes. So:

```
git revert -m 1 <B2 merge commit>
```

That restores pre-B2 pricing behaviour completely, because every B2 change is
additive-or-substitutive within the code and **none of it rewrites data**:

| changed | rollback effect |
|---|---|
| `lib/edgelab/price_units.py` (new) | unused after revert |
| `lib/edgelab/production_price.py` (new) | unused after revert |
| `lib/edgelab/canonical_price.py` | loses `parse_instant`; nothing else reads it |
| `lib/edgelab/observation_join.py` | `parse_ts` returns to its own local definition |
| `lib/kalshi_registry_market_builders.py` | `norm`/`price_block` return to magnitude inference and the one-sided midpoint |
| `scripts/build_kalshi_registry.py` | re-declares its local copies |
| `scripts/fetch_kalshi_markets.py` | returns to the previous capture shape |
| `lib/kalshi_mlb_contract_parser.py` | `_price_to_pct` returns to magnitude inference |
| `scripts/merge_odds.py` | stops emitting `*_book` / `kalshiSnapshotTs`, and the primary registry RFI branch stops carrying `yrfi_bid`/`yrfi_ask` |
| `scripts/build_market_ledger.py` | restores the `else kalshi_vf` fallback and the per-family derivations |

## Why no historical evidence needs repairing

B2 writes nothing into `data/`. It changes how the NEXT run prices, not what
any past run recorded. Concretely:

* No wager, settlement, recommendation or CLV artifact is modified.
* `bets.json` and `BET_LOG.md` are untouched.
* Rows produced under B2 carry *additional* provenance fields
  (`executablePriceBasis`, `quoteAgeSeconds`, `bookState`, …). After a revert
  those fields simply stop being written; existing rows keep them, and every
  consumer reads them with `.get()`, so nothing breaks on their absence.
* `data/kalshi_market_registry.json` and `data/slate.json` are regenerated from
  scratch on every `fetch-slate.yml` run, so the first post-revert run restores
  the old shape without any repair step.

So rollback is a code revert plus one ordinary scheduled run. There is no
migration to undo and no backfill to write.

## What a revert costs, stated plainly

Reverting restores the defect. Production would go back to storing a
midpoint-derived number in `executablePriceUsed`, to `(bid or 0 + ask) / 2` on
one-sided books, and to the seven magnitude heuristics. On the frozen slate
measured for this PR that means roughly two more candidates called actionable
than the real book supports, and prices understated by up to 35.5c on the worst
team-total contract. Rollback is a safety valve, not a neutral choice.

## The one flag that exists, and why it is not a second authority

`W1_B2_DECISION_AT` overrides the instant quotes are aged against. It exists so
the rehearsal and the tests can evaluate a frozen historical slate without every
quote ageing out.

It is safe by construction:

* **Default-off.** Unset, production uses the real clock
  (`build_market_ledger._decision_instant`).
* **It is a clock, not a price path.** It changes what "now" is. It cannot
  change which book is read, which side is bought, or how a price is derived —
  there is exactly one code path for those, and it has no branch on this value.
* **Setting it can only make quotes look OLDER or NEWER, never make an absent
  price appear.** A candidate with no book still refuses.
* **Tested.** `tests/test_w1b2_executable_price_cutover.py` covers the fresh
  case, the stale case and the unknown-age case explicitly.
* **Removal plan.** It goes away with the rehearsal workflow once B2 is merged
  and the cutover has run in production for one full slate cycle; W1-C should
  delete both, and nothing in production reads the variable.

## If something is wrong but a full revert is too blunt

The failure mode B2 introduces is *over-refusal* — candidates going
non-actionable because the book, side or age could not be proven. That is the
intended direction, but if a family is refusing for a reason that turns out to
be a plumbing bug rather than a genuine gap, the narrow fix is to correct the
plumbing for that family, not to reinstate a fallback. Every refusal is
labelled with a reason in `priceRefusalReason` and counted per family in the
blast-radius report, so the diagnosis starts from data rather than from a guess.

Reinstating a midpoint fallback for one family "temporarily" is not a rollback
option. It is the original defect with a smaller blast radius.
