# Bankroll Context

Where the number a handicap sizes against comes from, how old it is, and when
it may not be used at all.

---

## 1. One sizing authority, and it is not this repository

Exactly one thing may set a real-money stake size: **the authenticated Kalshi
account balance**, read by `chmoses98/kalshi-bet-router` and delivered here.

That is a change of posture from the previous version of this document, and a
real defect forced it. The old implementation let this repository's own
**derived ledger** become the sizing authority whenever its most recent
evidence looked fresh. Its cash transaction history is known incomplete — one
`STARTING_BALANCE` of $350 dated 2026-08-03 and no deposits since — so
`availableBankroll` was **negative**, and a single wager updated thirty
seconds ago was enough to stamp that negative figure `FRESH` and permit
staking against it.

A recent timestamp on an incomplete ledger is not freshness. It is a recent
row.

| Source | Can it size? |
|---|---|
| **Authenticated Kalshi balance** (`kalshi_authenticated_balance`) | **yes — the only one** |
| This repo's derived ledger (`compute_bankroll_summary`) | **never**, at any age |
| A human-typed `userReportedBalance` | **never** |
| Anything else | **never** |

`SIZING_ELIGIBLE_SOURCES` and `SIZING_ELIGIBLE_VALUE_TYPES` are frozensets
checked inside `build_context()`. `sizingAllowed` is **derived there**, never
passed in, so no caller can grant it to a value that did not earn it.

**A handicap may always proceed.** Only *staking* stops. And `sizingAllowed`
is only half the question — the other half is whether the reader actually
holds the number. See **Two questions, not one** below.

## 2. Which field, and what it actually means

`valueType: KALSHI_AVAILABLE_CASH_BALANCE` — Kalshi's `balance` field, the
account's **available cash**, delivered in dollars after the router converts
it from the integer cents the API returns.

Deliberately **not** `portfolio_value`, which is the mark-to-market value of
open positions. Sizing against that would double-count exposure already at
risk. The producing side refuses to substitute it
(`parse_balance_response` raises rather than falling back); this side refuses
to accept any other `valueType` as a sizing basis, and says so in
`unavailableReason`.

> **Verified against the live account on 2026-09-18.** The first authenticated
> run of `kalshi-bet-router`'s `publish-bankroll.yml`
> ([run 35298910389](https://github.com/chmoses98/kalshi-bet-router/actions/runs/35298910389))
> called `GET /portfolio/balance` and `parse_balance_response` accepted the
> real response unchanged — so the live payload does carry `balance` as a
> non-negative integer number of cents, exactly as the API reference
> documents. Both sides remain strict rather than tolerant: every shape they
> have not been shown is refused, so a future contract change surfaces as
> "sizing unavailable" rather than as a plausible wrong number.

## 3. Freshness: 30 minutes

| Status | When | `sizingAllowed` |
|---|---|---|
| `FRESH` | observed within `maxAgeMinutes` (**30**) | `true` (if every other gate passes) |
| `STALE` | older than that window | `false` |
| `UNAVAILABLE` | no value, no usable timestamp, a materially future-dated observation, a non-authoritative source, or a zero/negative/non-finite amount | `false` |

A balance is a live quantity: a fill, a settlement or a deposit moves it, and
any of those can happen between two slates. **A 24-hour-old reading is not the
current bankroll**, and calling it current for real-money staking is exactly
the failure this window exists to prevent.

Five minutes of future-dated tolerance is allowed for clock skew between two
GitHub runners. Beyond that the producer is broken and the reading is refused.

## 4. A bankroll that cannot be staked is not a small bankroll

`_usable_amount()` refuses, and each refusal is a real-money error rather than
a rounding one:

| Value | Why refused |
|---|---|
| `None`, a string, a `bool` | there is no number here |
| `NaN`, `±inf` | arithmetic silently poisons every stake it touches |
| `0` | nothing can be deployed |
| **negative** | a percentage of a negative number is a negative stake |

`test_a_negative_bankroll_is_refused_even_when_perfectly_fresh` pins the
combination that would otherwise slip through: a real, authenticated,
seconds-old reading of a negative number.

## 5. How the number gets here — and why it is not committed

**Both repositories are PUBLIC:**

```
chmoses98/edge-finder-api     public
chmoses98/kalshi-bet-router   public
```

On a public repository, workflow logs, job summaries and uploaded artifacts
are readable by anyone with no login. So the balance cannot be committed here,
cannot be printed into a log here, and cannot arrive as an artifact.

It arrives as an **encrypted GitHub Actions secret**: sealed by the router
with libsodium against this repository's Actions public key, unreadable
through the API, decrypted only inside a workflow run of this repository.

```
kalshi-bet-router                          edge-finder-api
publish-bankroll.yml (*/15 * * * *)
  GET /portfolio/balance  (read only)
      └─ 5 fields, sealed ─────────────►  secret KALSHI_BANKROLL_CONTEXT
                                                  │
                                          fetch-slate.yml
                                            └─ build_handicapping_card.py
                                                 sizes against it
                                                 COMMITS A REDACTED CARD
```

### Two questions, not one

**Sizing authority and numeric visibility are different facts, and neither
implies the other.** Dollar stake sizing requires BOTH:

| Field | Question it answers |
|---|---|
| `sizingAllowed` | did a FRESH, sizing-authoritative bankroll exist for whoever produced this? |
| `numericBankrollAvailable` | is the actual amount in **this reader's** hands? |

`consumerSizingVerdict.verdict` states the conclusion:

| Verdict | May present dollar stakes |
|---|---|
| `DOLLAR_SIZING_PERMITTED` | yes |
| `NO_DOLLAR_SIZING_FOR_THIS_CONSUMER` | **no** — the bankroll is real and fresh, its value is redacted from this copy |
| `NO_DOLLAR_SIZING` | **no** — stale, unavailable, or not sizing-authoritative |

The committed card is exactly the case where the first holds and the second
does not: `status: FRESH`, `sizingAllowed: true`, `bankroll: null`. Both
statements are true — of *different readers*. A consumer that checks only
`sizingAllowed` will invent dollar figures it has no basis for, which is
precisely what this field pair prevents.

`redacted()` sets `numericBankrollAvailable: false` and **recomputes** the
verdict from the redacted object rather than inheriting it, so removing the
number necessarily revokes permission to spend it. Both gates are checked with
`is True`, not truthiness: a `"yes"` from some future producer must not buy a
real-money stake.

When sizing is refused for either reason, **the handicap is unaffected**: edge,
confidence and bet-up-to *fractions* of bankroll all work without the amount.
Only dollar figures stop.

### What the committed card carries

```json
"bankroll": {
  "bankroll": null,
  "bankrollRedacted": true,
  "numericBankrollAvailable": false,
  "consumerSizingVerdict": {
    "verdict": "NO_DOLLAR_SIZING_FOR_THIS_CONSUMER",
    "mayPresentDollarStakes": false,
    "reason": "... the bankroll exists and is fresh, its value is redacted from this copy ..."
  },
  "status": "FRESH",
  "sizingAllowed": true,
  "observedAt": "2026-09-18T17:57:00Z",
  "ageMinutes": 3.0,
  "maxAgeMinutes": 30,
  "source": "kalshi_authenticated_balance",
  "valueType": "KALSHI_AVAILABLE_CASH_BALANCE"
}
```

`data/handicapping_card/latest.json` — the pointer a fresh chat reads first —
carries `numericBankrollAvailable` and `dollarSizingVerdict` alongside
`bankrollStatus`, so the distinction is visible before the full card is opened.

Everything needed to **trust or distrust the sizing** survives; the amount
does not. `redacted()` is an **allowlist**, not a deletion pass, so a field
added to the context later cannot leak by being forgotten. `diagnostic` is
dropped wholesale — re-publishing dollar figures beside a redacted bankroll
field is how a redaction stops meaning anything.

### How a human or an assistant gets the actual number

| Who | How |
|---|---|
| The card build (where sizing actually happens) | automatically, from the secret, every slate run |
| A local operator | `KALSHI_BANKROLL_CONTEXT='<json>' python3 scripts/build_handicapping_card.py --reveal-bankroll --out-root /tmp/card` |
| A fresh ChatGPT/Claude session | **it does not, from this repository.** A public repo has no channel that delivers a private number to an external reader. The card says so explicitly (`NO_DOLLAR_SIZING_FOR_THIS_CONSUMER`) rather than letting the session guess. Paste the balance into the chat yourself if you want dollar stakes there; edge, confidence and bet-up-to fractions work without it. |

That last row is a genuine architectural limit, not an oversight. If the
numeric bankroll should be readable from the repository itself, the honest fix
is to **make `edge-finder-api` private** — at which point `--reveal-bankroll`
can be switched on in the workflow and nothing else changes.

`KALSHI_BANKROLL_CONTEXT_PATH` is also accepted, and the path **must resolve
outside this repository's working tree**. A bankroll file inside the tree is
one `git add -A` away from publishing the balance permanently, so that is
refused structurally rather than remembered.

## 6. Hard rules

- **No hard-coded number.** No dollar literal exists in
  `lib/bankroll_context.py`; a regex test enforces it.
- **No second bankroll authority.** The derived ledger is diagnostic context
  and is structurally barred from sizing.
- **No network, no credential.** This module reads a published value. It has
  no HTTP client and no Kalshi key, and a test asserts both.
- **A malformed payload is never echoed.** The reason string reaches a public
  Actions log; a malformed secret is still a balance.
- **Stale is explicit.** Silence is never allowed to look like freshness.

## 7. Usage

```python
from lib import bankroll_context

ctx = bankroll_context.load_bankroll_context()
verdict = ctx["consumerSizingVerdict"]
if verdict["mayPresentDollarStakes"]:          # NOT just ctx["sizingAllowed"]
    stake = size_against(ctx["bankroll"])
else:
    report(f"no dollar sizing ({verdict['verdict']}): {verdict['reason']}")

print(bankroll_context.describe_for_output(ctx))                # redacted
print(bankroll_context.describe_for_output(ctx, reveal=True))   # local only

commit(bankroll_context.redacted(ctx))                          # public-safe
```

The producing half is documented in
`kalshi-bet-router/docs/BANKROLL_DELIVERY.md`.
