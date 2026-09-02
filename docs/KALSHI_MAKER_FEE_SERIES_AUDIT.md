# Official per-series Kalshi maker-fee audit

Date: 2026-09-02. Status: **UNRESOLVED — no official source retrievable.**
Scope: the 13 MLB series in the MLB-ALPHA-0002 `FULL_MICROSTRUCTURE`
capture tier. Code: `lib/edgelab/kalshi_fees.py`. Tests:
`tests/edgelab/test_maker_fee_series_audit.py`.

This document exists because the honest answer to "what is the maker fee
on each MLB series?" is **we do not know**, and the codebase previously
answered it implicitly with `0.0` — which is not the same thing, and is
wrong in the expensive direction.

## 1. What was asked

Determine, per series (`KXMLBGAME`, `KXMLBF5`, `KXMLBF5TOTAL`,
`KXMLBTOTAL`, `KXMLBSPREAD`, `KXMLBTEAMTOTAL`, `KXMLBRFI`, and the rest of
the FULL_MICROSTRUCTURE tier): the maker multiplier, the taker multiplier,
the official source, the effective date, and whether the series is
**explicitly listed** as designated or merely falls under a **default** —
without globally assigning `KXMLBGAME`'s treatment to every other MLB
series.

The rule under test: the quadratic maker fee is
`makerMultiplier × contracts × P × (1 − P)`, where the maker multiplier is
`0.0175` **only** for series specifically designated as maker-fee-charging,
and `0.0` by default otherwise.

## 2. Retrieval attempts, and their exact outcomes

Every path to an authoritative source failed.

| Channel | Target | Result |
|---|---|---|
| `curl` | `kalshi.com` | `CONNECT tunnel failed, 403` (proxy policy denial) |
| `curl` | `docs.kalshi.com` | `CONNECT tunnel failed, 403` |
| `curl` | `api.elections.kalshi.com` (`GET /series/KXMLBGAME`) | `CONNECT tunnel failed, 403` |
| WebFetch | `kalshi.com/docs/kalshi-fee-schedule.pdf` | `EGRESS_BLOCKED` |
| WebFetch | `docs.kalshi.com/changelog`, `help.kalshi.com`, and 4 third-party mirrors | `EGRESS_BLOCKED` (all 7 attempts) |
| WebSearch | current fee schedule, per-series maker designation, "designated maker fee series" list | Results returned, but **contradictory** (see below) |

The agent proxy's own status endpoint records the denials explicitly
(`connect_rejected … policy denial` for all three Kalshi hosts), so this is
a network policy in this environment, not a transient failure.

**The WebSearch contradiction, stated plainly.** One query returned "for
standard event contracts like KXMLBGAME, maker fees are always 25% of the
taker fee." A later, more MLB-targeted query returned the opposite:
"Kalshi does not yet impose market-maker fees for Major League Baseball…
MLB markets are not currently listed among these special event categories
that charge maker fees." Both are unattributed summaries. Neither is
quotable primary evidence. **This audit does not resolve that conflict and
does not pick a side.**

One further unverified signal worth recording: several summaries citing the
API changelog claim maker fees were switched on around 2026-08-19/20, with
a carve-out phrased as an *exception* (implying a broader default-on). That
is materially newer than the repo's `2026-07-07` effective-date lower bound
and would fall inside the window this program's own data was captured.
Unverified, and deliberately not acted on.

## 3. Findings, per series

| Series | Taker | Maker | Source | Effective date | Listed vs default | Evidence status |
|---|---|---|---|---|---|---|
| KXMLBGAME | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | not in any retrieved designated list → DEFAULT | UNKNOWN (contradictory) |
| KXMLBTOTAL | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBSPREAD | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBTEAMTOTAL | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBF5 | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBF5TOTAL | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBF5SPREAD | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBF3 | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBF7 | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBRFI | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBINNINGWIN | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBINNINGTOTAL | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |
| KXMLBEXTRAS | 0.07 | **UNKNOWN** | UNRETRIEVABLE | unknown | DEFAULT | UNKNOWN |

**Every row is individually UNKNOWN.** No series was established from an
official source, and nothing about `KXMLBGAME` has been propagated to the
others — the requirement not to generalize is satisfied by generalizing
*nothing*.

The only externally corroborated numbers are the **taker** multiplier
(0.07, consistent across every query, and reproduced exactly by
`taker_fee(100, 0.5) == 1.75`) and the **magnitude of the designated maker
rate** if a series were designated (0.0175, arithmetic-checked against a
returned maker fee table at five price points). Neither establishes whether
any MLB series is designated.

## 4. Why the old default was unsafe

A maker fee is a strictly positive cost. Assuming `0.0` when the truth is
`0.0175` understates cost and therefore **overstates** every maker
strategy's P/L, ROI and edge — strategies look **better** than reality,
which is the direction that loses money.

At `P = 0.50` the unmodelled cost is `0.0175 × 0.5 × 0.5 = $0.004375` per
contract, i.e. ~87.5 bp of principal per side (~175 bp round trip if both
legs rest). Microstructure maker edges in this program are quoted in tens
of basis points, so an unmodelled 87.5 bp per side can flip a candidate's
sign outright. Fee rounding is ceil-to-cent, so at small contract counts
the realized fee is ≥ the continuous rate — the understatement is if
anything worse.

The reverse error — assuming a fee that does not exist — only makes a real
edge look smaller. A strategy that survives it is genuinely viable. So the
conservative assumption is unambiguously "the maker fee IS charged."

## 5. What changed in the code

Additive and opt-in. No existing constant, default, or function behavior
moved, so no existing caller silently shifts:

- `SERIES_FEE_METADATA` entries now carry `makerFeeMultiplier: None`
  (**UNKNOWN**, never "known to be zero") and
  `makerFeeRuleConfidence: "UNKNOWN_NO_OFFICIAL_SOURCE_RETRIEVABLE"`,
  alongside an explicit `takerFeeMultiplier`.
- The five FULL_MICROSTRUCTURE series that were missing from the registry
  entirely (`KXMLBF3`, `KXMLBF7`, `KXMLBINNINGWIN`, `KXMLBINNINGTOTAL`,
  `KXMLBEXTRAS`) are now registered. They previously fell through to the
  `UNKNOWN_SERIES` branch — correct, but silent.
- New `maker_fee_multiplier_for_series(ticker, *, assume_unknown_is_charged=True)`
  returns `(multiplier, confidence)`. An unknown series resolves to the
  **conservative** `0.0175` / `UNKNOWN_ASSUMED_CHARGED_CONSERVATIVE`. The
  optimistic `0.0` leg still exists but must be asked for explicitly, and
  the two must be reported side by side, never pooled.
- `FEE_MULTIPLIER_MAKER_DEFAULT` and `maker_fee()`'s own default are
  **unchanged** (0.0), deliberately, for backward compatibility. The
  conservative resolver is the recommended path for new research code.

`scripts/research/mlb_alpha_0002/maker_simulation.py` was already doing the
right thing independently — it defines its own conservative/optimistic
multipliers, headlines the conservative one
(`netPlPerFill_makerFee25pctOfTaker`), and reports `zeroMakerFee` only as a
sensitivity leg. This audit brings the shared library up to that standard.

## 6. What would actually close this gap

Any one of, in rough order of cost:

1. **Allowlist `api.elections.kalshi.com`** and call
   `GET /trade-api/v2/series/{ticker}` for each of the 13 tickers — it
   returns `fee_type` and `fee_multiplier`. This is public market data; the
   activation audit already made 499 such calls successfully (it got 429s,
   not 401s) and simply **discarded** the fee fields. That discard is a
   second, independently fixable gap.
2. **`GET /trade-api/v2/exchange/series/fee_changes`** — per-series
   scheduled fee changes with `scheduled_ts`, which would settle the
   2026-08-19/20 maker-activation question and populate `feeEffectiveAt`.
3. **Allowlist `kalshi.com`** and read the fee schedule PDF's designated
   series table verbatim.

Where the answer lands when it arrives: `SERIES_FEE_METADATA`'s
`makerFeeMultiplier` per series, with `makerFeeRuleConfidence` set to
`API_CONFIRMED` and `feeEffectiveAt` set to the override's own
`scheduled_ts`. `maker_fee_multiplier_for_series()` then returns real data
and the conservative fallback stops being load-bearing.

## 7. Bottom line

No fee value in this document is invented. Per-series maker designation is
**UNKNOWN for all 13 MLB series**, the repo now says so explicitly instead
of implying zero, and every unknown resolves conservatively so that no
maker strategy can be made to look profitable by an assumption nobody
verified.
