# 2026-08-31 Manual Postmortem

**Record:** 5-9 · **Exact cost:** $374.99 · **Exact paid out:** $314.47 · **Exact P/L:** -$60.52 · **Exact ROI:** -16.14%
**Canonical whole-dollar risk:** $375 · **Canonical P/L:** -$60.53

14 user-confirmed positions, imported via `scripts/edgelab/import_bet_batch.py` under
`importBatchId=manual-postmortem-20260831-v1`, 12 straight + 2 MANUALCOMBO parlays.
All 12 straight-market bets were objectively settled from the pre-existing EdgeLab
settlement archive (`data/edgelab/settlements/2026-08-31.jsonl`, generated before this
session and confirmed to already match every one of these outcomes exactly) via
`scripts/edgelab/reconcile_settled_bets_from_archive.py` -- no live network fetch was
needed or attempted. Every one of the 14 bets additionally carries a confirmed manual
receipt (`lib.edgelab.bets.confirm_realized_return`, source=MANUAL_POSTMORTEM_RECEIPT)
with the exact paid-out/net-P&L evidence; the two combos remain `status=pending` in the
objective ledger (no real Kalshi market backs a MANUALCOMBO ticker) with their true
LOSS results carried entirely by the confirmed receipt.

## Family breakdown

| Family | Record | Risk | P/L | ROI |
|---|---|---|---|---|
| F5 sides | 4-2 | $195 | +$45.22 | +23.19% |
| ML | 1-2 | $105 (canonical) | -$30.75 | -29.29% |
| Team totals | 0-3 | $55 | -$55.00 | -100% |
| Parlays | 0-2 | $20 | -$20.00 | -100% |

Core F5 + ML: **+$14.47** (canonical). Team totals + parlays: **-$75.00**.

## Process postmortem

1. **Straight side handicapping was NOT the main source of the loss.** F5 sides went
   4-2 for +$45.22 (+23.19% ROI) -- the day's clearly strongest family.
2. **Team totals and parlays produced most of the damage** (-$75.00 combined against
   an otherwise-profitable core).
3. **TB F5:** a small-sample/uncertain opposing starter situation should have reduced
   confidence before laying a 57%-displayed favorite. LOSS.
4. **AZ F5:** faded an established starter largely because recent/underlying metrics
   looked attackable -- insufficient justification on its own. LOSS.
5. **BAL:** BAL ML WON while BAL 6+ runs LOST. Direct evidence that "I like this
   team's side" is NOT equivalent to "I like this team to clear a high scoring
   threshold." Team-total legs need their own independent scoring thesis.
6. **NYY:** ML + team-total exposure concentrated one bad thesis on a single
   game (DUPLICATE_THESIS) -- both legs lost.
7. **AZ:** F5 + team-total similarly concentrated one thesis (DUPLICATE_THESIS) --
   both legs lost.
8. **MIL ML** around a coin-flip displayed price (52%) did not carry enough
   independent justification to deserve meaningful exposure.

## Combo/parlay detail

- **5-leg combo** (TB F5 / BOS F5 / MIN F5 / TEX F5 / BAL ML), $10, max payout $234.19:
  4/5 legs won (only TB F5 lost) -- still a full LOSS on an all-or-nothing parlay.
- **2-leg combo** (AZ F5 / NYY ML), $10, max payout $40.89: 0/2 legs won -- LOSS.

Both combos are imported as a single `MANUALCOMBO-*` position each, per the
repository's existing convention (never split into per-leg placed bets).

## Reconciliation note

Every dollar figure above reconciles to this repository's own canonical ledger truth
(see `reportedTotals`/`canonicalTotals` on the stored Postmortem record) to within a
single cent on the ML family, which is an intentional artifact of the canonical
whole-dollar-stake rule (NYY ML: $39.99 share-card cost -> $40 canonical stake per
`docs/KALSHI_FEE_AWARE_EXECUTION_ECONOMICS.md`), not a data error.
