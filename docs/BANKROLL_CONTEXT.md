# Bankroll Context

Where the number a handicap sizes against comes from, how old it is, and when
it may not be used at all.

---

## 1. The audit: `kalshi-bet-router` does NOT maintain a bankroll

The pre-merge review asked for the canonical bankroll to be sourced from
`chmoses98/kalshi-bet-router`. That repository was read directly
(commit `a215e7e239fa6dee07d49300b0091acbc7d3c2ff`). **It does not maintain a
bankroll or account-balance artifact, and it is deliberately built not to:**

| Evidence | What it says |
|---|---|
| `src/kalshi_router/client.py` → `READ_ONLY_PATH_PREFIXES` | `/portfolio/balance` is **not** in the allowlist. The client refuses to *sign* a request to it before the request is built. |
| `tests/test_client.py` (3 separate tests) | `/portfolio/balance` is pinned as refused, and is listed under `TRADING_ROUTES` with the comment *"No Kalshi trading endpoint may be implemented — pinned, not assumed."* |
| `docs/OPERATIONS.md` → "Privacy" | *"no raw fill payload, fill id, subaccount identifier, **balance or account metadata** is persisted anywhere."* |
| `tests/test_privacy.py` | Asserts **no monetary value at all** appears in rendered router output. |
| `src/kalshi_router/destination.py` → `write_payloads` | What the router emits downstream is an importer payload of normalized **wager rows**, written to `RUNNER_TEMP` and pushed into `data/edgelab/bets/bets.jsonl` here — never account state. |

So there is no router bankroll to reuse today. Publishing one would be a
deliberate design decision by the owner of that repository — it would mean
allowlisting an endpoint that repo has explicitly pinned shut and relaxing a
privacy rule it enforces with tests. **That decision is not made here.**

## 2. What is used instead — and why it is not a second ledger

This repository already has a canonical bankroll ledger:
`lib/edgelab/bankroll.compute_bankroll_summary()` over
`data/edgelab/bankroll/transactions.jsonl` plus the canonical wager ledger.

`lib/bankroll_context.py` is an **adapter over that existing function**, not a
new authority. It computes no money of its own; every dollar figure comes from
`compute_bankroll_summary`. It is structured so that a router artifact wins the
moment one exists, with no code change here:

```
1. a router-published artifact          ← preferred, wins outright when present
     $KALSHI_ROUTER_BANKROLL_PATH, else data/router/bankroll.json
     {"bankroll"|"availableBalance"|"balance"|"availableCash": <number>,
      "observedAt": "<ISO-8601 UTC>",
      "valueType": "OBSERVED_ACCOUNT_BALANCE"}
2. this repository's existing canonical ledger   ← used today
```

## 3. Which field, and what it actually means

| Field | Meaning | Used for sizing? |
|---|---|---|
| `settledBankroll` | manual cash transactions + realized P&L on settled REAL bets | no |
| `totalExposure` | stake currently at risk in pending REAL bets | no |
| **`availableBankroll`** | `settledBankroll − totalExposure` — what could be staked now without going negative on paper | **yes** |
| `userReportedBalance` | the latest human-typed balance | **never** — informational only |

The value is labelled **`DERIVED_LEDGER_AVAILABLE_BANKROLL`**, not
`OBSERVED_ACCOUNT_BALANCE`. That distinction is load-bearing: it is a ledger
position derived from recorded transactions and graded wagers, not a reading of
the real Kalshi account. A router artifact, when it exists, would carry
`OBSERVED_ACCOUNT_BALANCE` and the label would change accordingly.

## 4. Freshness

`observedAt` is the most recent piece of real evidence behind the number: the
latest cash transaction, or the latest update to a REAL wager.

| Status | When | `sizingAllowed` |
|---|---|---|
| `FRESH` | observed within `maxAgeHours` (default **24h**) | `true` |
| `STALE` | older than that window | `false` |
| `UNAVAILABLE` | no value, no usable timestamp, a future-dated observation, or an unreadable ledger | `false` |

**A handicap may always proceed.** Only *staking* stops. When
`sizingAllowed` is false the analysis is produced normally, but stake sizes are
not presented and the reason is stated — never a remembered or hand-typed
substitute.

`describe_for_output()` renders the one line an output must carry:

```
BANKROLL: $1,234.56 (OBSERVED_ACCOUNT_BALANCE, 0.4h old) — sizing allowed. Source: …
BANKROLL: $-744.32 but STALE (25.2h old, window 24h) — do NOT size real-money stakes against it. Source: …
BANKROLL: UNAVAILABLE — stake sizing is NOT current. Reason: …
```

## 5. Hard rules

- **No hard-coded number.** There is no dollar literal in
  `lib/bankroll_context.py`, and a test enforces it.
- **No second bankroll authority.** The adapter delegates to the existing
  canonical function.
- **A human-typed balance is never canonical.** `userReportedBalance` is
  surfaced and explicitly marked informational.
- **Read-only.** Nothing here writes, fetches from, or mutates the router
  repository; it is a pure read of local committed evidence.
- **Stale is explicit.** Silence is never allowed to look like freshness.

## 6. Current state of this repository's ledger (worth knowing)

As of this writing the ledger holds **one** cash transaction — a
`STARTING_BALANCE` of $350 dated 2026-08-03 — and no deposits since, while
realized P&L is negative and pending exposure is ~$426. The derived
`availableBankroll` is therefore **negative**, and its most recent evidence is
older than the 24h window, so it resolves `STALE` / `sizingAllowed: false`.

That is the honest answer, and it is the *correct* behaviour: the derived
figure is not tracking the real Kalshi account. To make sizing usable, one of
these has to happen:

1. the router (or another sanctioned source) publishes an observed account
   balance at the documented path — the adapter picks it up with no code change; or
2. the cash side of the existing ledger is brought up to date with the real
   deposits/withdrawals via `scripts/edgelab/record_bankroll_transaction.py`.

Until then the system will keep saying "sizing unavailable" rather than
inventing a number, which is the intended failure mode.

## 7. Usage

```python
from lib import bankroll_context

ctx = bankroll_context.load_bankroll_context()
if ctx["sizingAllowed"]:
    stake = size_against(ctx["bankroll"])
else:
    report(f"sizing unavailable: {ctx['unavailableReason']}")
print(bankroll_context.describe_for_output(ctx))
```

The handicapping card embeds this object at `card["bankroll"]`, so the card
itself always records which bankroll it would have sized against.
