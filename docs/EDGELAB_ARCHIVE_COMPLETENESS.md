# Archive completeness: discovered vs archived

**Phase 8.** Every downstream number — coverage, calibration, CLV — is computed
over the EdgeLab archive and silently assumes the archive holds everything that
was discoverable. Nothing had checked that assumption.

## The reconciliation

Two identities must hold for every capture. Both are built entirely from numbers
the pipeline already computes; nobody had ever subtracted them.

```
snapshot total_markets == observationsBuilt + marketsExcluded + rows with no ticker

observationsBuilt      == observationsWritten
                        + observationsDroppedNoChange
                        + observationsSkippedDuplicate
```

Measured over 21 days and 227 ingest runs, the second identity holds exactly:
**`unaccountedObservations: 0`**. The pipeline's internal arithmetic is sound.
The loss is entirely upstream, at fetch.

## What is NOT loss

`observationsDroppedNoChange` is **not** a missing market. It is a tick whose
`yesBid` / `yesAsk` / `noBid` / `noAsk` / `lastPrice` / `marketStatus` were
identical to the last retained row for that ticker
(`lib/edgelab/market_universe.py::select_observations_for_retention`). The
archive is a change log, and the price at that moment is known from the row it
duplicates. Dropping it is correct, and it is counted honestly.

Volume is deliberately not compared, so a market whose volume moved while its
quote held still is dropped — also correct. An earlier draft of this audit used
"volume advanced while the row is absent" as evidence of loss; that is exactly
backwards, because volume is the one field retention ignores. It was measuring
the signature of correct deduplication and calling it a defect. The
[Phase 6](../data/edgelab/reports/capture_health.md) and Phase 7 coverage
figures were re-derived against snapshot ground truth to confirm this costs
**zero** `TRUE_CLOSE` coverage (1,719 markets by either measure, across
2026-09-18…20).

## The actual defect

`api/kalshisearch.js` has always recorded its own partial fetches into every
snapshot it writes — `fetchFailures` (carrying the URL and HTTP status of each
aborted page loop), `fetchFailureCount`, `priceFetchFailureCount`.

**No Python file in this repository read any of them.** So a capture that
fetched three of seventeen series was ingested, written, and reported
`status: success` with `marketsExcluded: 0`, indistinguishable from a complete
one.

Measured over 21 days of retained snapshots:

| | |
|---|---|
| captures | 106 |
| partial fetches | **9 (8.5%)** |
| cause | HTTP **429** in every case |
| markets lost (lower bound) | **7,356** |
| ingest runs | 227 |
| runs reporting `success` | **227** |

## The loss is systematic, not random

`fetchAllPages` breaks out of its page loop on a non-ok response, and the 17
series are fetched **sequentially**. A rate limit that bites partway through the
list therefore truncates whichever series come *last* — the same high-volume
hitter prop families every time:

| series | family | times truncated in 21 days |
|---|---|---|
| `KXMLBHRR` | `hitter_hits_runs_rbis` | 6 |
| `KXMLBRBI` | `hitter_rbis` | 6 |
| `KXMLBSB` | `hitter_stolen_bases` | 5 |
| `KXMLBTB` | `hitter_total_bases` | 2 |

Worst single tick: `kalshi_search_2026-09-11_1906.json` lost `KXMLBHRR` (1,234),
`KXMLBRBI` (525) and `KXMLBSB` (227) — **1,986 markets, 56% of that capture**.

### Consequence for research already published

This directly qualifies a result in
[#233](https://github.com/chmoses98/edge-finder-api/pull/233). That PR reported
`hitter_stolen_bases` with a calibration gap of −0.0217, outside its Wilson
interval. `KXMLBSB` is one of the systematically truncated series, so that
family's sample is biased against exactly the busy game-time moments when rate
limits bite. **The result should not be read as a measured property of the
family** until it is recomputed over captures known to be complete. It was
already labelled exploratory for correlation reasons; this is a second,
independent reason.

## The fix

`lib/edgelab/market_universe.py::snapshot_fetch_completeness` reads what the
capture layer already records, and `ingest_market_observations.py`:

- emits an `INCOMPLETE_SOURCE_CAPTURE` warning naming the failed series;
- records `snapshotsWithIncompleteFetch`, `sourceFetchFailures` and
  `seriesTruncatedAtSource` in the run record, so a downstream consumer can say
  *which* families are biased, not merely that something failed;
- sets `status: partial` rather than `success`.

The run-record schema's own description already required this: *"Never write
'success' merely because the process reached the end."*

Fail-closed applies to the **status, not the data**. A truncated capture still
archives every market it did receive — discarding those would turn a partial
loss into a total one.

## Bounds

Every figure here is a lower bound.

- Truncation loss is measured only where a series vanished **entirely** versus a
  complete peer on the same date. A series truncated partway through its pages
  is invisible.
- A date with no complete peer capture cannot be measured at all.
- Timestamped snapshots are pruned after 21 days
  (`lib/snapshot_retention.py`), so earlier dates cannot be audited even in
  principle. The Phase 7 full-archive figures reach back further than this audit
  can verify.

## Known, still open

These were found in the same audit and are **not** fixed here:

1. **`fetchAllPages` page cap.** `maxPages = 10` at `api/kalshisearch.js:294`.
   With `limit=200` that ceilings a series at **2,000 markets per capture**, and
   when page 10 returns a live cursor it is discarded with no truncation flag.
   Observed peak is 1,268 (`KXMLBHRR`), so the cap is not biting yet — it is at
   63% of the ceiling, and it is latent rather than theoretical.
2. **Broad-discovery cap.** `if (discoveredUnknownSeriesMarkets.length >= 500)
   break;` truncates silently; the count reports 500 either way.
3. **`if not ticker: continue`** at `market_universe.py` drops a market with no
   counter at all — the only exclusion in the path that is neither recorded nor
   counted.
4. **Per-market exclusion rows are discarded.** The registry gate builds
   `{marketTicker, seriesTicker, title, exclusionReason}` for every excluded
   market and then reports only the integer `marketsExcluded`. The audit trail
   exists in memory and is thrown away.
5. **A failed capture leaves no artifact.** On a handler error the snapshot has
   no `markets` key, the workflow's `markets_count > 0` guard skips archiving,
   and nothing anywhere records that a tick was missed.
