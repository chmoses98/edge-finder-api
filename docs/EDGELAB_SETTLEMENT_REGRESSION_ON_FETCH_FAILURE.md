# Canonical settlement regresses SETTLED facts when the authoritative fetch fails

**Found:** 2026-09-21, while running the canonical settlement path for
2026-09-17 … 2026-09-20.
**Status: FIXED.** The transition rule now lives in one place,
`lib/edgelab/settlement.classify_settlement_transition()`, and is
verified against the real partitions that were damaged — see
*Verification* at the end.

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

## Remediation applied

`lib/edgelab/settlement.classify_settlement_transition()` is now the one
canonical rule; `merge_settlement_record()` and
`scripts/edgelab/settle_markets.py` both ask it rather than carrying
their own guards.

| stored | computed | outcome |
|---|---|---|
| (none) | anything | `FIRST_RECORD` — store it |
| non-terminal | anything | `NO_OP` / `ACCEPTED` |
| `SETTLED` / `VOID` | `SETTLED` / `VOID` | `NO_OP` / `ACCEPTED` (real correction) |
| `SETTLED` / `VOID` | anything non-terminal | **`REFUSED_TERMINAL_REGRESSION`** — keep stored |

Terminal = `{SETTLED, VOID}`.

A refused transition returns the stored record **by identity**, so not a
single field is rewritten — not even an audit stamp, since stamping the
row would mean writing to the very record the rule exists to protect.
The audit trail lives in the run instead:

- `counts.terminalSettlementRegressionPrevented` on the returned summary
  and on the persisted `research_runs` record;
- one aggregate warning (not one per market — the failure mode hits
  thousands at once and would bury the per-bet `REFUSED` warnings);
- the warning forces the run's `status` to `partial`, so a blinded run
  can never be recorded as clean;
- `terminal_regressions_prevented=N` on the CLI summary line.

The wording is deliberate: a non-zero count means the run was **blind**
for those markets, not that it re-confirmed them.

**Not immutability.** Keying on the status transition rather than on
record equality is what separates *the truth changed* from *we went
blind*. A corrected box score, or a game wrongly marked Suspended that
in fact completed, arrives via a fetch that SUCCEEDED — terminal →
terminal — and still flows through the normal correction path.

## Verification

Reproduced deterministically with no network access, by settling a
fixture successfully and then re-running it blind
(`tests/edgelab/test_settle_markets_script.py`), and against the two real
partitions the incident damaged:

```
$ python3 scripts/edgelab/settle_markets.py --date 2026-09-19
... settled_or_void=4967 bets_settled=0 terminal_regressions_prevented=4967
$ python3 scripts/edgelab/settle_markets.py --date 2026-09-20
... settled_or_void=3297 bets_settled=0 terminal_regressions_prevented=3297
```

4967 + 3297 = **8,264** — precisely the records destroyed by the original
run. Diffing both partitions against `HEAD` afterwards:

| partition | status transitions | terminal records modified in any way |
|---|---|---|
| `2026-09-19.jsonl` | none | **0** |
| `2026-09-20.jsonl` | 25 new `SETTLEMENT_UNRESOLVED` (first records) | **0** |

`data/edgelab/bets/bets.jsonl` was byte-identical before and after
(md5 `80502175d92de851226ad51ab739f082`).

Note what the first line now reports: `settled_or_void=4967` where the
pre-fix run reported `0`. The old zero was not a quiet run — it was the
sound of the facts already having been erased.

This proves the monotonicity rule only. It does **not** show that live
settlement works; the authoritative sources remain egress-blocked from
this environment.
