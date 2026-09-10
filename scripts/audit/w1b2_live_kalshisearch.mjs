/**
 * scripts/audit/w1b2_live_kalshisearch.mjs
 * ========================================
 * W1-B2, CEO review of PR #206, BLOCKER 3.
 *
 * Drives api/kalshisearch.js's REAL default export against the LIVE Kalshi API
 * and writes the response to a file. This is the actual production ingestion
 * function, not a reimplementation of it: the same fetch, the same series
 * loop, the same parseMarketRecord, the same declared-unit conversion.
 *
 * It exists because the endpoint normally runs as a Vercel serverless handler
 * and there is otherwise no way to exercise it end-to-end. The req/res pair
 * below is the minimum the handler touches.
 *
 * READ-ONLY. It writes exactly one file, to the path given on the command
 * line, which the rehearsal workflow places under $RUNNER_TEMP. It never
 * writes into the repository and never places a wager.
 *
 *   node scripts/audit/w1b2_live_kalshisearch.mjs <out.json> [YYYY-MM-DD]
 */
import { writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, '..', '..');
const ENDPOINT = pathToFileURL(resolve(ROOT, 'api', 'kalshisearch.js')).href;

const outPath = process.argv[2];
const date = process.argv[3] || undefined;
if (!outPath) {
  console.error('usage: w1b2_live_kalshisearch.mjs <out.json> [YYYY-MM-DD]');
  process.exit(2);
}

const { default: handler } = await import(ENDPOINT);

// The smallest req/res the handler actually uses. `json`/`send` capture the
// payload instead of writing to a socket.
let captured = null;
let statusCode = null;
const res = {
  setHeader() {},
  status(code) { statusCode = code; return res; },
  json(payload) { captured = payload; return res; },
  send(payload) { captured = payload; return res; },
  end() { return res; },
};

const started = new Date().toISOString();
await handler({ method: 'GET', query: date ? { date } : {} }, res);
const finished = new Date().toISOString();

if (captured == null) {
  console.error('the endpoint produced no payload');
  process.exit(1);
}
if (typeof captured === 'string') {
  console.error('unexpected JSONP response; this driver sends no callback');
  process.exit(1);
}

writeFileSync(outPath, JSON.stringify(captured, null, 2));

const markets = captured.markets || [];
const withBid = markets.filter(m => m.yes_bid != null).length;
const withAsk = markets.filter(m => m.yes_ask != null).length;
const states = {};
for (const m of markets) states[m.book_state] = (states[m.book_state] || 0) + 1;

const failures = captured.fetchFailures || [];
const priceFailures = failures.filter(f => f.scope === 'series');
const discoveryFailures = failures.filter(f => f.scope !== 'series');

console.log(JSON.stringify({
  status: statusCode,
  error: captured.error || null,
  fetchFailureCount: failures.length,
  priceFetchFailureCount: priceFailures.length,
  discoveryFetchFailureCount: discoveryFailures.length,
  fetchFailures: failures.slice(0, 5),
  requestStartedAt: started,
  requestFinishedAt: finished,
  fetchedAt: captured.fetched_at || null,
  kalshiDate: captured.kalshi_date || null,
  totalMarkets: markets.length,
  marketsWithYesBid: withBid,
  marketsWithYesAsk: withAsk,
  bookStates: states,
  seriesCounts: captured.series_counts || {},
  outPath,
}, null, 2));

// A live rehearsal that could not reach the PRICE universe has proven nothing,
// and must not be mistaken for a rehearsal that found an empty one. Exit
// non-zero so the workflow fails loudly and the constraint is stated rather
// than silently substituted for evidence.
//
// The broad discovery sweep is held to a different standard on purpose. It
// pages over the ENTIRE exchange for research visibility, is never read by the
// registry backfill or by merge_odds, and is therefore the one call that gets
// rate-limited (observed: HTTP 429 on page 9, while all 17 MLB price series
// returned complete books). Failing a price rehearsal on it would train
// everyone to ignore a red rehearsal -- which is worse than the noise it was
// meant to catch. It is reported loudly and does not abort.
if (discoveryFailures.length > 0) {
  console.error(
    `\nNOTE: ${discoveryFailures.length} research-only discovery request(s) ` +
    `did not succeed (${discoveryFailures.map(f => f.status || f.error).join(', ')}). ` +
    `That sweep feeds no price path, so it does not invalidate this rehearsal.`);
}
if (priceFailures.length > 0) {
  console.error(
    `\nLIVE PRICE FETCH FAILED: ${priceFailures.length} MLB series request(s) ` +
    `did not succeed. This is NOT evidence of an empty market universe -- it ` +
    `is evidence that the price universe was unreachable from this runner.`);
  process.exit(3);
}
