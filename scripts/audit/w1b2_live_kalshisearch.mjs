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

// The series that actually feed the production price path. These are the ones
// scripts/build_kalshi_registry.py reads into the registry and merge_odds.py
// turns into books; a 429 on any of them means the rehearsal could not reach
// the price universe and has proven nothing.
//
// The player-prop series below are RESEARCH-ONLY -- lib.research and
// build_kalshi_registry's RESEARCH_ONLY_SERIES -- fetched for visibility and
// never read by real-money qualification. Observed live: Kalshi 429s the tail
// of the series sweep (KXMLBTB, KXMLBHRR, KXMLBRBI) while every price series
// returned complete books, which aborted a rehearsal whose price ingestion had
// fully succeeded. That is the same over-blunt classification already fixed
// for the discovery sweep, one level down.
//
// This DOWNGRADES nothing that can reach a wager. A 429 on any price series
// still fails the rehearsal loudly.
const RESEARCH_ONLY_SERIES = new Set([
  'KXMLBF3', 'KXMLBF7',
  'KXMLBKS', 'KXMLBOUTS', 'KXMLBHIT', 'KXMLBTB', 'KXMLBHRR', 'KXMLBRBI', 'KXMLBSB',
]);

function seriesOf(url) {
  const match = /series_ticker=([A-Z0-9]+)/.exec(url || '');
  return match ? match[1] : null;
}

const failures = captured.fetchFailures || [];
const priceFailures = failures.filter(
  f => f.scope === 'series' && !RESEARCH_ONLY_SERIES.has(seriesOf(f.url)));
const researchFailures = failures.filter(
  f => f.scope === 'series' && RESEARCH_ONLY_SERIES.has(seriesOf(f.url)));
const discoveryFailures = failures.filter(f => f.scope !== 'series');

console.log(JSON.stringify({
  status: statusCode,
  error: captured.error || null,
  fetchFailureCount: failures.length,
  priceFetchFailureCount: priceFailures.length,
  researchOnlyFetchFailureCount: researchFailures.length,
  researchOnlySeriesThatFailed: researchFailures.map(f => seriesOf(f.url)),
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
if (researchFailures.length > 0) {
  console.error(
    `\nNOTE: ${researchFailures.length} research-only SERIES request(s) did not ` +
    `succeed (${researchFailures.map(f => `${seriesOf(f.url)} ${f.status || f.error}`).join(', ')}). ` +
    `Those series are never read by real-money qualification, so they do not ` +
    `invalidate this rehearsal. Every price series is reported separately below.`);
}
if (priceFailures.length > 0) {
  console.error(
    `\nLIVE PRICE FETCH FAILED: ${priceFailures.length} MLB series request(s) ` +
    `did not succeed. This is NOT evidence of an empty market universe -- it ` +
    `is evidence that the price universe was unreachable from this runner.`);
  process.exit(3);
}
