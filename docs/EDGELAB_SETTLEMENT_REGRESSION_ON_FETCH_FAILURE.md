# Canonical settlement regresses SETTLED facts when the authoritative fetch fails

**Found:** 2026-09-21, while running the canonical settlement path for
2026-09-17 … 2026-09-20.
**Status:** unfixed — reported, not remediated. Changing settlement
semantics needs a run against live authoritative data to validate, and
that data was not reachable from the session that found this.

## What happens

`scripts/edgelab/settle_markets.py` sources every outcome from
`statsapi.mlb.com`. When that fetch fails for a game, each of its markets
is (correctly) computed as `SETTLEMENT_UNRESOLVED`. The computed record is
then handed to `lib/edgelab/settlement.py::merge_settlement_record()`,
which compares it to the stored one:

```python
if _comparable_settlement_view(existing_record) == _comparable_settlement_view(new_record):
    return existing_record
return dict(new_record, createdAt=existing_record.get("createdAt", new_record["createdAt"]))
```

A stored `SETTLED` record and a freshly computed `SETTLEMENT_UNRESOLVED`
one are not equal, so the **unresolved record wins** and the settled fact
— its `result`, `outcome`, `settledAt` and `settlementEvidence` — is
overwritten with nulls.

There is no monotonicity guard: nothing in the merge knows that
`SETTLED -> SETTLEMENT_UNRESOLVED` is a *regression* rather than a
*correction*.

## Measured blast radius

One run of the documented command, with the MLB Stats API unreachable:

```
$ python3 scripts/edgelab/settle_markets.py --date 2026-09-20
[settle_markets] date=2026-09-20 markets=5163 settled_or_void=0 bets_settled=0 warnings=46
```

The summary line reports zero settlements and zero bets touched, which
reads like a clean no-op. The settlements ledger says otherwise:

| partition | SETTLED -> SETTLEMENT_UNRESOLVED |
|---|---|
| `data/edgelab/settlements/2026-09-19.jsonl` | 4,967 |
| `data/edgelab/settlements/2026-09-20.jsonl` | 3,297 |

**8,264 settled market facts destroyed by a run that reported doing
nothing.** The changes were reverted with `git checkout`; they are not in
any commit.

The canonical **bet** ledger (`data/edgelab/bets/bets.jsonl`) was *not*
damaged — `bets_settled=0` and no bet row changed. The damage is confined
to the settlements ledger, which is where `settlementEvidence` lives.

## Why this matters beyond a bad network day

The settlements partition is the archived evidence for *how* each market
resolved. Losing it is not self-healing: re-running settlement once the
API is reachable can recompute `result`, but the original `settledAt` and
the evidence captured at that time are gone. A transient outage, a rate
limit, or a policy-blocked egress during any scheduled run silently
rewrites history.

It also makes the settlement path unsafe to run as a diagnostic. The
present mission could not re-run settlement to inspect its behavior
without first proving it would not eat the ledger.

## Proposed remediation

Add a monotonicity guard in `merge_settlement_record()`: a stored
`SETTLED` (or `VOID`) record must never be replaced by a computed
`SETTLEMENT_UNRESOLVED` one. Keep the stored record and surface the
attempt as a run warning instead.

A genuine correction — `SETTLED` with a *different* result, from a fetch
that actually succeeded — must still be allowed through, so the guard has
to key on the settlement **status transition**, not on equality of the
whole record. Suggested rule:

| stored | computed | outcome |
|---|---|---|
| `SETTLEMENT_UNRESOLVED` | anything | take computed |
| `SETTLED` / `VOID` | `SETTLED` / `VOID` | take computed (real correction) |
| `SETTLED` / `VOID` | `SETTLEMENT_UNRESOLVED` | **keep stored**, warn |

Tests should cover all three rows, plus the existing byte-identical no-op
case. The run summary should additionally report a
`settlementsRegressionSuppressed` count so a bad run is visible rather
than silent.
