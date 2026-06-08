# data/kalshi_registry_snapshots/

Dated snapshots of `data/kalshi_search.json`, archived by the `fetch-slate.yml`
workflow after every Kalshi market fetch.

**File pattern:** `kalshi_search_YYYY-MM-DD.json`

Each snapshot is a frozen copy of the full Kalshi market registry for that slate date.
`backfill_market_identity.py` uses these to match bet records to `marketTicker` values
without guessing across dates.

Once a snapshot exists for a given date, all bets from that date become backfill-eligible.
