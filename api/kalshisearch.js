/**
 * kalshisearch.js — v3.1
 *
 * Fetches ALL Kalshi MLB markets for today across ALL series:
 *   KXMLBGAME, KXMLBSPREAD, KXMLBTOTAL, KXMLBTEAMTOTAL,
 *   KXMLBF5, KXMLBF5SPREAD, KXMLBF5TOTAL, KXMLBRFI
 *
 * Each series is queried independently via /markets?series_ticker=
 * because nested markets on KXMLBGAME events only returns ML markets.
 */

// ── Pure helpers, hoisted to module scope (W1-B2, CEO review of PR #206) ──
// Exported by name so tests/test_w1b2_kalshisearch_price_transport.py can
// drive them directly through node, with no network and no serverless
// runtime. The platform routes only the default export; these named
// exports are inert to it, and keeping them in THIS file means the
// deployed artifact is byte-for-byte the thing under test.

export function classifyMarket(ticker, title, subtitle) {
  const t = (title || '').toLowerCase();
  const s = (subtitle || '').toLowerCase();
  const k = (ticker || '').toLowerCase();
  const combined = `${t} ${s} ${k}`;

  if (k.includes('kxmlbrfi') || combined.includes('nrfi') || combined.includes('no run first inning')) return 'nrfi_yrfi';
  if (combined.includes('yrfi') || combined.includes('first inning run') ||
      combined.includes('score in the first') || combined.includes('runs in the 1st')) return 'nrfi_yrfi';

  if (k.includes('kxmlbf5total') || k.includes('f5total')) return 'f5_total';
  if (k.includes('kxmlbf5spread') || (k.includes('f5') && (combined.includes('wins by') || combined.includes('1.5')))) return 'f5_spread';
  if (k.includes('kxmlbf5') || (combined.includes('first 5') && (combined.includes('wins') || combined.includes('winner')))) return 'f5_moneyline';

  // Kalshi price-checker correction mission: KXMLBF3/KXMLBF7 are now
  // CONFIRMED real series tickers (live series-catalogue dispatch,
  // data/kalshi/discovery/2026-07-30_series_catalogue.json), so both the
  // ticker prefix AND the title-text fallback (still needed for anything
  // this list hasn't confirmed yet) are checked.
  if (k.includes('kxmlbf3') || ((combined.includes('first 3') || combined.includes('3 innings')) &&
      (combined.includes('wins') || combined.includes('winner') || combined.includes('tie')))) return 'f3_moneyline';
  if (k.includes('kxmlbf7') || ((combined.includes('first 7') || combined.includes('7 innings')) &&
      (combined.includes('wins') || combined.includes('winner') || combined.includes('tie')))) return 'f7_moneyline';

  // Confirmed pitcher/hitter single-game player-prop series (same
  // dispatch as above) -- classified by ticker prefix only, since these
  // are new enough that no reliable title-text convention has been
  // observed yet.
  if (k.includes('kxmlbks')) return 'pitcher_strikeouts';
  if (k.includes('kxmlbouts')) return 'pitcher_outs';
  if (k.includes('kxmlbhrr')) return 'hitter_hits_runs_rbis';
  if (k.includes('kxmlbhit')) return 'hitter_hits';
  if (k.includes('kxmlbtb')) return 'hitter_total_bases';
  if (k.includes('kxmlbrbi')) return 'hitter_rbis';
  if (k.includes('kxmlbsb')) return 'hitter_stolen_bases';

  if (k.includes('kxmlbteamtotal') || combined.includes('team total') || combined.includes('scores over') || combined.includes('score over')) return 'team_total';
  if (k.includes('kxmlbtotal') || (combined.includes('total') && (combined.includes('over') || combined.includes('under')) && !combined.includes('inning'))) return 'total';
  if (k.includes('kxmlbspread') || combined.includes('wins by') || combined.includes('run line')) return 'spread';
  if (k.includes('kxmlbgame') || combined.includes('wins') || combined.includes('winner') || combined.includes('moneyline')) return 'moneyline';

  return 'unknown';
}


// ── Price transport: declared units, never inferred ────────────────────────
//
// W1-B2 (CEO review of PR #206). This endpoint feeds data/kalshi_search.json,
// which scripts/build_kalshi_registry.py uses to BACKFILL missing and
// null-priced ML, F5 and team-total markets into the registry -- so the
// numbers below become production executable prices. It previously read:
//
//     return isNaN(f) ? null : (f > 1.0 ? f / 100 : f);
//
// a dollars-vs-cents decision made from the SIZE of the number, and it was
// wrong in both directions at exactly the prices that matter most:
//
//   * yes_bid = 1      -> 1 is not > 1.0, so it stayed 1 and meant $1.00.
//                         A ONE-CENT contract read as a DOLLAR contract:
//                         100x, on the longshots where the most tempting
//                         apparent edges live.
//   * yes_bid = 0.5     -> a genuine HALF-CENT quote on a deci-cent grid,
//                         read as $0.50. Also 100x, the other way.
//   * yes_bid = 50      -> 0.50. Right, but only by luck of magnitude.
//
// Kalshi denominates by FIELD NAME: `*_dollars` fields are fixed-point
// dollar strings (4dp, so 0.01c resolution), and the bare `yes_bid` /
// `yes_ask` / `no_bid` / `no_ask` / `last_price` fields are integer-ish
// CENTS. That is knowable, so it is declared here rather than guessed.
export function toDollars(value, unit) {
  if (value == null) return null;
  const f = parseFloat(value);
  if (isNaN(f)) return null;
  // Cents -> dollars. Sub-cent survives (0.5c -> 0.005) and zero survives
  // as zero, because the conversion is arithmetic, not a magnitude test.
  return unit === 'cents' ? f / 100 : f;
}

/**
 * Read the first PRESENT field, in declared preference order, and return
 * both the dollar value and which field it came from.
 *
 * Presence is `!= null`, never truthiness: a genuine resting quote of ZERO
 * is a real, meaningful observation ("no bid at any price"), and `||` would
 * discard it and fall through to a field denominated in a different unit --
 * producing a number that is both the wrong value and the wrong scale.
 *
 * The fixed-point `*_dollars` fields are preferred where present because
 * they carry the exchange's own 4dp precision; the bare cents field is the
 * fallback for markets or API versions that do not supply them.
 */
export function readPrice(mkt, dollarField, centsField) {
  if (mkt[dollarField] != null) {
    const v = toDollars(mkt[dollarField], 'dollars');
    if (v != null) return { value: v, field: dollarField };
  }
  if (mkt[centsField] != null) {
    const v = toDollars(mkt[centsField], 'cents');
    if (v != null) return { value: v, field: centsField };
  }
  return { value: null, field: null };
}


export function computeAmericanOdds(mid) {
  if (!mid || mid <= 0 || mid >= 1) return null;
  return mid >= 0.5
    ? Math.round(-(mid / (1 - mid)) * 100)
    : Math.round(((1 - mid) / mid) * 100);
}


export function parseMarketRecord(mkt, eventTicker, snapshotTs) {
  const ticker = mkt.ticker || '';
  const title = mkt.title || '';
  const subtitle = mkt.subtitle || '';
  const marketType = classifyMarket(ticker, title, subtitle);

  const yesBidRead = readPrice(mkt, 'yes_bid_dollars', 'yes_bid');
  const yesAskRead = readPrice(mkt, 'yes_ask_dollars', 'yes_ask');
  const noBidRead = readPrice(mkt, 'no_bid_dollars', 'no_bid');
  const noAskRead = readPrice(mkt, 'no_ask_dollars', 'no_ask');
  const lastRead = readPrice(mkt, 'last_price_dollars', 'last_price');

  const yesBid = yesBidRead.value;
  const yesAsk = yesAskRead.value;

  // A MIDPOINT NEEDS TWO GENUINE SIDES.
  //
  // This used to be `: (yesBid ?? yesAsk)` -- so an ask-only book reported
  // its ASK as the market's midpoint, and a bid-only book reported its BID,
  // and `implied_pct` and `american_odds` were then derived from that
  // invented number as if it were a real two-sided mid. Downstream,
  // build_kalshi_registry.py's backfill reads `m.get('mid')` first, so the
  // fabrication propagated straight into the registry.
  //
  // A resting quote of ZERO is not a side: "no bid at any price" means
  // there is nothing on that end of the book, which is why the test is
  // `> 0` and not merely `!= null`. One-sided books stay visibly one-sided:
  // mid, implied_pct and american_odds are all null, and book_state says
  // which end is missing.
  const hasBid = yesBid != null && yesBid > 0;
  const hasAsk = yesAsk != null && yesAsk > 0;
  const mid = (hasBid && hasAsk) ? (yesBid + yesAsk) / 2 : null;
  const impliedPct = mid != null ? Math.round(mid * 1000) / 10 : null;
  const bookState = (hasBid && hasAsk) ? 'TWO_SIDED'
                  : hasAsk ? 'ASK_ONLY'
                  : hasBid ? 'BID_ONLY'
                  : 'EMPTY';

  return {
    event_ticker:  eventTicker || mkt.event_ticker || '',
    market_ticker: ticker,
    title,
    subtitle,
    open_time:     mkt.open_time || '',
    close_time:    mkt.close_time || '',
    market_type:   marketType,
    status:        mkt.status || 'open',
    // When this quote was observed. This handler fetches live from Kalshi,
    // so its own fetch time IS this quote's capture time -- and it must
    // travel with the price, because a registry rebuilt hours later must
    // not be able to present this observation as a fresh one.
    snapshot_ts:   snapshotTs,
    // The unit these price fields are denominated in, stated rather than
    // left to be inferred downstream.
    unit:          'dollars',
    yes_bid:       yesBid,
    yes_ask:       yesAsk,
    no_bid:        noBidRead.value,
    no_ask:        noAskRead.value,
    book_state:    bookState,
    // Which raw Kalshi field each number actually came from, so a
    // disagreement downstream can be traced to a source field rather than
    // guessed at.
    price_source_fields: {
      yes_bid: yesBidRead.field, yes_ask: yesAskRead.field,
      no_bid: noBidRead.field, no_ask: noAskRead.field,
      last_price: lastRead.field,
    },
    mid:           mid != null ? Math.round(mid * 10000) / 10000 : null,
    implied_pct:   impliedPct,
    american_odds: mid != null ? computeAmericanOdds(mid) : null,
    last_price:    lastRead.value,
    volume:        parseFloat(mkt.volume ?? mkt.volume_fp ?? 0) || 0,
    open_interest: parseFloat(mkt.open_interest ?? mkt.open_interest_fp ?? 0) || 0,
  };
}


// ============================================================================
// THE CAPTURE COMPLETENESS CONTRACT
// ============================================================================
//
// DISCOVERED == ARCHIVED + EXPLICITLY EXCLUDED + EXPLICITLY FAILED
//
// with zero silent loss. Before this, none of the three terms on the right
// was knowable from a snapshot:
//
//   * a 429 broke the page loop and the live cursor was DISCARDED with no
//     record that more pages existed;
//   * maxPages = 10 ceilinged a series at 2,000 markets with no truncation
//     flag;
//   * the broad pass stopped at 500 entries, and the count read 500 either
//     way;
//   * markets filtered out by date were never counted, only the survivors;
//   * a handler error produced a body with no `markets` key at all, and the
//     workflow's `markets_count > 0` gate then skipped archiving, so a failed
//     capture was indistinguishable from a capture that never ran.
//
// Measured consequence over 21 days: 9 of 106 captures were partial, every
// one an HTTP 429, at least 7,356 markets lost -- and all 227 ingest runs
// reported success. Because the 17 series are fetched SEQUENTIALLY and the
// loop broke on the first rate limit, the loss landed on whichever families
// came last, every time: KXMLBHRR x6, KXMLBRBI x6, KXMLBSB x5, KXMLBTB x2.
// Systematically biased research, not random noise.
//
// Everything below is injectable (fetchImpl/sleepImpl/nowImpl) so the real
// shipped code is what the tests drive -- no network, no reimplementation.

export const TRUNCATION_NONE = null;
export const TRUNCATION_PAGE_CAP = 'PAGE_CAP_REACHED_WITH_LIVE_CURSOR';
export const TRUNCATION_DEADLINE = 'DEADLINE_EXCEEDED';
export const TRUNCATION_RETRIES = 'RETRIES_EXHAUSTED';
export const TRUNCATION_TRANSPORT = 'TRANSPORT_ERROR';
export const TRUNCATION_ENTRY_CAP = 'ENTRY_CAP_REACHED';

export const CAPTURE_COMPLETE = 'COMPLETE';
export const CAPTURE_PARTIAL = 'PARTIAL';
export const CAPTURE_FAILED = 'FAILED';

// A safety maximum, not a budget. It exists so a runaway cursor cannot loop
// forever; reaching it is a TRUNCATION, never a silent stop. The old value
// of 10 ceilinged a series at 2,000 markets/capture against an observed peak
// of 1,268 -- 63% of the way to silently losing data.
export const MAX_PAGES_SAFETY = 60;
export const BROAD_MAX_PAGES_SAFETY = 40;
// Likewise: hitting it marks the broad pass truncated rather than pretending
// the exchange held exactly this many unknown-series markets.
export const BROAD_DISCOVERY_ENTRY_CAP = 2000;

export const CAPTURE_CONTRACT_VERSION = 'kalshi_capture_v4';
// Well inside the function's configured maxDuration (see vercel.json), with
// room left to serialise a large response.
export const CAPTURE_DEADLINE_MS = 45000;
// Never start a second-pass series fetch without this much budget left.
export const SECOND_PASS_RESERVE_MS = 6000;

export const MAX_RETRIES_PER_PAGE = 3;
export const BASE_BACKOFF_MS = 400;
export const MAX_BACKOFF_MS = 4000;

// Deterministic, no jitter: this is one sequential invocation, so there is no
// thundering herd to spread, and an auditable capture is worth more than a
// randomised one. Doubling from 400ms caps at 4s -- 400, 800, 1600.
export function backoffDelayMs(attempt, retryAfterHeader) {
  const retryAfter = parseRetryAfterMs(retryAfterHeader);
  if (retryAfter != null) return Math.min(retryAfter, MAX_BACKOFF_MS);
  return Math.min(BASE_BACKOFF_MS * Math.pow(2, attempt), MAX_BACKOFF_MS);
}

// Kalshi sends Retry-After in seconds; the HTTP-date form is accepted too so
// a spec-compliant server is never ignored.
export function parseRetryAfterMs(header, nowMs) {
  if (header == null || header === '') return null;
  const seconds = Number(header);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.round(seconds * 1000);
  const when = Date.parse(String(header));
  if (Number.isNaN(when)) return null;
  const delta = when - (nowMs == null ? Date.now() : nowMs);
  return delta > 0 ? delta : 0;
}

export function isRetryableStatus(status) {
  return status === 429 || status === 408 || (status >= 500 && status < 600);
}

/**
 * Deterministically rotate the fetch order so missingness cannot keep
 * landing on the same families.
 *
 * Rotation does not by itself prevent a rate limit from truncating whatever
 * is last -- retries do most of that work. What it prevents is the SAME
 * families absorbing the loss every single time, which is what turned a
 * transport problem into biased research. The seed is derived from the
 * capture's own date and hour, so the order is reproducible from the
 * snapshot alone and auditable after the fact.
 */
export function rotateSeries(series, seed) {
  const n = series.length;
  if (n === 0) return [];
  const offset = ((Math.trunc(seed) % n) + n) % n;
  return [...series.slice(offset), ...series.slice(0, offset)];
}

export function rotationSeed(kalshiDate, hourUtc) {
  let hash = 0;
  for (const ch of String(kalshiDate)) {
    hash = (hash * 31 + ch.charCodeAt(0)) % 1000003;
  }
  return hash + (Number(hourUtc) || 0);
}

/**
 * Page a cursor-driven Kalshi endpoint to exhaustion, or say exactly why not.
 *
 * Returns { records, pagination }. The pagination block is the evidence: one
 * entry per page attempt carrying the cursor before and after, the HTTP
 * status, retries spent and backoff waited -- plus `complete`, which is false
 * whenever a live cursor was left in hand for ANY reason.
 */
export async function fetchPaginated(baseUrl, key, opts = {}) {
  const {
    scope = 'series',
    series = null,
    maxPages = MAX_PAGES_SAFETY,
    maxRetries = MAX_RETRIES_PER_PAGE,
    deadlineAt = null,
    fetchImpl = fetch,
    sleepImpl = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    nowImpl = () => Date.now(),
  } = opts;

  const records = [];
  const pages = [];
  let cursor = '';
  let truncationReason = TRUNCATION_NONE;
  let retriesAttempted = 0;
  let totalBackoffMs = 0;

  for (let page = 0; page < maxPages; page++) {
    if (deadlineAt != null && nowImpl() >= deadlineAt) {
      truncationReason = TRUNCATION_DEADLINE;
      break;
    }

    const url = cursor ? `${baseUrl}&cursor=${cursor}` : baseUrl;
    const attemptLog = {
      page, scope, series,
      cursorBefore: cursor || null,
      cursorAfter: null,
      httpStatus: null,
      retries: 0,
      backoffMs: 0,
      recordsReceived: 0,
      error: null,
    };

    let data = null;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      let response;
      try {
        response = await fetchImpl(url, { headers: { 'Content-Type': 'application/json' } });
      } catch (e) {
        attemptLog.error = String((e && e.message) || e);
        attemptLog.httpStatus = null;
        // A transport error is as retryable as a 5xx: the request never
        // reached a server that said no.
        if (attempt < maxRetries) {
          const delay = backoffDelayMs(attempt, null);
          if (deadlineAt != null && nowImpl() + delay >= deadlineAt) {
            truncationReason = TRUNCATION_DEADLINE;
            break;
          }
          attemptLog.retries = attempt + 1;
          attemptLog.backoffMs += delay;
          retriesAttempted += 1;
          totalBackoffMs += delay;
          await sleepImpl(delay);
          continue;
        }
        truncationReason = TRUNCATION_TRANSPORT;
        break;
      }

      attemptLog.httpStatus = response.status;
      if (response.ok) {
        data = await response.json();
        attemptLog.error = null;
        break;
      }

      if (isRetryableStatus(response.status) && attempt < maxRetries) {
        const header = response.headers && typeof response.headers.get === 'function'
          ? response.headers.get('retry-after')
          : null;
        const delay = backoffDelayMs(attempt, header);
        // Never sleep past the deadline: returning a truthful partial
        // capture beats being killed mid-flight with no artifact at all.
        if (deadlineAt != null && nowImpl() + delay >= deadlineAt) {
          truncationReason = TRUNCATION_DEADLINE;
          break;
        }
        attemptLog.retries = attempt + 1;
        attemptLog.backoffMs += delay;
        retriesAttempted += 1;
        totalBackoffMs += delay;
        await sleepImpl(delay);
        continue;
      }

      // Either a non-retryable status, or retries are spent.
      truncationReason = isRetryableStatus(response.status)
        ? TRUNCATION_RETRIES
        : TRUNCATION_TRANSPORT;
      break;
    }

    if (data == null) {
      // The page never arrived. Whatever cursor we were holding is still
      // live, so this series is NOT complete -- and the log says why.
      if (truncationReason === TRUNCATION_NONE) truncationReason = TRUNCATION_TRANSPORT;
      attemptLog.cursorAfter = cursor || null;
      pages.push(attemptLog);
      break;
    }

    const items = data[key] || [];
    records.push(...items);
    cursor = data.cursor || '';
    attemptLog.cursorAfter = cursor || null;
    attemptLog.recordsReceived = items.length;
    pages.push(attemptLog);

    if (!cursor) break;                 // genuinely exhausted
    if (!items.length) break;           // a cursor with no items cannot advance

    if (page === maxPages - 1) {
      // THE defect this constant used to hide: a live cursor at the cap.
      truncationReason = TRUNCATION_PAGE_CAP;
    }
  }

  return {
    records,
    pagination: {
      scope,
      series,
      pages,
      pagesFetched: pages.length,
      recordsReceived: records.length,
      finalCursor: cursor || null,
      retriesAttempted,
      totalBackoffMs,
      truncationReason,
      // The single question every downstream consumer actually asks.
      complete: truncationReason === TRUNCATION_NONE && !cursor,
    },
  };
}

/**
 * Roll per-series pagination evidence up into one capture verdict.
 *
 * A capture is COMPLETE only if every series paginated to exhaustion AND the
 * broad discovery pass did too. Anything else is PARTIAL, and a capture that
 * retrieved nothing at all is FAILED. There is deliberately no fourth state
 * meaning "probably fine".
 */
export function summarizeCapture(paginations, { marketsArchived = 0 } = {}) {
  const incomplete = paginations.filter((p) => p && !p.complete);
  const seriesIncomplete = incomplete.filter((p) => p.scope === 'series');
  const discoveryIncomplete = incomplete.filter((p) => p.scope === 'discovery');

  // captureStatus is a claim about THE PRICE UNIVERSE, which is the 17 series.
  //
  // It used to require every scope, including the broad discovery pass. That
  // made COMPLETE definitionally unreachable: the broad pass has no series
  // filter, so it pages the ENTIRE Kalshi exchange and can never be exhausted
  // inside one invocation. The first live v4 capture proved it -- all 17
  // series complete, 40,000 records pulled by the broad pass, 39,938 of them
  // not even for this slate date, and the whole capture reported PARTIAL.
  //
  // A contract that can never be satisfied is worse than no contract: every
  // capture would be PARTIAL forever, no capture would ever qualify for
  // research, and the completeness classification would be dead on arrival.
  //
  // So COMPLETE means "every series paginated to exhaustion". The broad pass
  // is supplementary by design -- api/kalshisearch.js's own note calls it
  // "pure research-visibility scaffolding ... never read by
  // build_kalshi_registry.py's backfill or by merge_odds.py" -- and its
  // truncation is reported in its OWN fields, never hidden, just not allowed
  // to invalidate a price universe that was in fact captured whole.
  let status = CAPTURE_COMPLETE;
  if (seriesIncomplete.length) status = CAPTURE_PARTIAL;
  if (marketsArchived === 0 && incomplete.length) status = CAPTURE_FAILED;

  return {
    captureStatus: status,
    captureComplete: status === CAPTURE_COMPLETE,
    seriesAttempted: paginations.filter((p) => p && p.scope === 'series').length,
    seriesIncomplete: seriesIncomplete.map((p) => p.series).filter(Boolean).sort(),
    // Reported separately and never folded into captureStatus.
    discoveryComplete: discoveryIncomplete.length === 0,
    discoveryTruncationReasons: [...new Set(
      discoveryIncomplete.map((p) => p.truncationReason))].sort(),
    incompleteScopes: [...new Set(incomplete.map((p) => p.scope))].sort(),
    truncationReasons: [...new Set(incomplete.map((p) => p.truncationReason))].sort(),
    totalRetries: paginations.reduce((sum, p) => sum + ((p && p.retriesAttempted) || 0), 0),
    totalBackoffMs: paginations.reduce((sum, p) => sum + ((p && p.totalBackoffMs) || 0), 0),
  };
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  // No-cache headers — date-sensitive response must never be served stale
  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.setHeader('Pragma', 'no-cache');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { date, callback } = req.query;

  const todayET = date || new Date().toLocaleDateString('en-CA', {
    timeZone: 'America/New_York'
  });

  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const d = new Date(todayET + 'T12:00:00Z');
  const kalshiDate = String(d.getUTCFullYear()).slice(2) +
    months[d.getUTCMonth()] +
    String(d.getUTCDate()).padStart(2, '0');

  const KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2';
  const snapshotTs = new Date().toISOString();

  // The strict single-game MLB market registry (Kalshi price-checker
  // correction mission): the original 8 full-game/F5 series PLUS the 9
  // additional series independently confirmed real via a live Kalshi
  // series-catalogue dispatch (data/kalshi/discovery/2026-07-30_series_
  // catalogue.json) -- F3/F7 winner markets and 7 pitcher/hitter player-
  // prop markets. This list must stay in sync with the Python source of
  // truth, lib.research.market_taxonomy.SINGLE_GAME_SERIES_TICKERS (this
  // serverless function has no import mechanism from that module, so the
  // two are deliberately kept as parallel, independently-evidenced
  // lists, not literally shared code). Every entry here is a confirmed
  // single-game (or single-game player-prop) MLB market family -- this
  // is NOT the broad ~179-series MLB-association heuristic used by
  // scripts/discover_kalshi_series_catalogue.py for audit purposes,
  // which deliberately stays broad and is never queried per-series here.
  const ALL_SERIES = [
    'KXMLBGAME',
    'KXMLBSPREAD',
    'KXMLBTOTAL',
    'KXMLBTEAMTOTAL',
    'KXMLBF5',
    'KXMLBF5SPREAD',
    'KXMLBF5TOTAL',
    'KXMLBRFI',
    'KXMLBF3',
    'KXMLBF7',
    'KXMLBKS',
    'KXMLBOUTS',
    'KXMLBHIT',
    'KXMLBTB',
    'KXMLBHRR',
    'KXMLBRBI',
    'KXMLBSB',
  ];

  // Fetch failures are RECORDED, not swallowed.
  //
  // W1-B2 (CEO review of PR #206). `if (!r.ok) break;` returned an empty list
  // and the response then reported a perfectly successful fetch of zero
  // markets -- indistinguishable from "the exchange has no markets today".
  //
  // `fetchFailures` / `fetchFailureCount` / `priceFetchFailureCount` are kept
  // verbatim so every existing consumer and every archived snapshot keeps its
  // meaning. What is new is `pagination` and `captureStatus` alongside them:
  // the old fields say a request failed, the new ones say whether the capture
  // is COMPLETE -- which a failure count alone cannot, because a live cursor
  // discarded at the page cap is a loss with no failure attached to it.
  const fetchFailures = [];
  const paginations = [];

  // Budget for the whole capture. Retries are only safe if they cannot get
  // the function killed mid-flight: a truthful PARTIAL artifact is worth far
  // more than a timeout that leaves no artifact at all.
  const deadlineAt = Date.now() + CAPTURE_DEADLINE_MS;

  async function pagedFetch(baseUrl, key, { scope = 'series', series = null,
                                            maxPages = MAX_PAGES_SAFETY } = {}) {
    const { records, pagination } = await fetchPaginated(baseUrl, key, {
      scope, series, maxPages, deadlineAt,
    });
    paginations.push(pagination);
    for (const page of pagination.pages) {
      if (page.httpStatus != null && page.httpStatus >= 400) {
        fetchFailures.push({ scope, url: baseUrl, page: page.page, status: page.httpStatus });
      } else if (page.error) {
        fetchFailures.push({ scope, url: baseUrl, page: page.page, error: page.error });
      }
    }
    return { records, pagination };
  }

  try {
    const allMarkets = [];
    const seriesResults = {};
    const exclusions = {};

    function recordExclusion(reason, count) {
      if (count > 0) exclusions[reason] = (exclusions[reason] || 0) + count;
    }

    // PHASE F -- fetch order is ROTATED, deterministically.
    //
    // The 17 series are still fetched sequentially, so whatever is last is
    // still the most exposed to a rate limit. What must not happen is the
    // SAME families being last every time: that is what turned a transport
    // problem into systematically biased research (KXMLBHRR/RBI/SB/TB
    // truncated 6/6/5/2 times in 21 days while the early series never were).
    // The seed comes from the capture's own date and hour, so the order is
    // reproducible from the snapshot and auditable after the fact.
    const hourUtc = new Date(snapshotTs).getUTCHours();
    const seed = rotationSeed(kalshiDate, hourUtc);
    const fetchOrder = rotateSeries(ALL_SERIES, seed);

    async function fetchSeries(series) {
      const mktsUrl = `${KALSHI_BASE}/markets?series_ticker=${series}&status=open&limit=200`;
      const { records, pagination } = await pagedFetch(mktsUrl, 'markets', { series });
      const todayMkts = records.filter(m => (m.event_ticker || '').includes(kalshiDate));
      // PHASE H: the date filter's DROPS are counted, not just its survivors.
      recordExclusion('event_ticker_not_for_this_slate_date', records.length - todayMkts.length);
      return { todayMkts, pagination };
    }

    for (const series of fetchOrder) {
      const { todayMkts, pagination } = await fetchSeries(series);
      seriesResults[series] = todayMkts.length;
      for (const mkt of todayMkts) {
        allMarkets.push(parseMarketRecord(mkt, mkt.event_ticker, snapshotTs));
      }
      if (!pagination.complete) {
        console.log(`[kalshisearch v4] ${series} INCOMPLETE: ${pagination.truncationReason}`);
      }
    }

    // PHASE F -- one bounded second pass, after the earlier series have let
    // the rate-limit window recover. A series that failed while the budget
    // was tight often succeeds now, and this is the difference between
    // losing a family for the day and losing it for a few seconds. Strictly
    // bounded: one retry per series, and only while the deadline allows.
    const retryable = paginations.filter(
      p => p.scope === 'series' && !p.complete
        && (p.truncationReason === TRUNCATION_RETRIES
            || p.truncationReason === TRUNCATION_TRANSPORT));
    for (const stale of retryable) {
      if (Date.now() >= deadlineAt - SECOND_PASS_RESERVE_MS) break;
      const { todayMkts, pagination } = await fetchSeries(stale.series);
      if (pagination.complete) {
        // Supersede the failed attempt; both remain in `pagination` for audit.
        stale.supersededBy = pagination.pages.length ? 'SECOND_PASS' : null;
        stale.complete = true;
        stale.truncationReason = TRUNCATION_NONE;
        const already = new Set(allMarkets.map(m => m.market_ticker));
        seriesResults[stale.series] = todayMkts.length;
        for (const mkt of todayMkts) {
          const row = parseMarketRecord(mkt, mkt.event_ticker, snapshotTs);
          if (!already.has(row.market_ticker)) allMarkets.push(row);
        }
        console.log(`[kalshisearch v4] ${stale.series} RECOVERED on second pass`);
      }
    }

    console.log(`[kalshisearch v4] ${kalshiDate} | series: ${JSON.stringify(seriesResults)}`);

    // Broad, unfiltered supplementary pass -- see the note below. It never
    // replaces the per-series loop; it exists so a real Kalshi series this
    // repository does not yet know the name of is still visible.
    const discoveredUnknownSeriesMarkets = [];
    let broadDiscoveryError = null;
    let broadEntryCapHit = false;
    try {
      const broadUrl = `${KALSHI_BASE}/markets?status=open&limit=1000`;
      const { records: broadMkts } = await pagedFetch(broadUrl, 'markets', {
        scope: 'discovery', maxPages: BROAD_MAX_PAGES_SAFETY,
      });
      let offDate = 0;
      let alreadyCovered = 0;
      for (const mkt of broadMkts) {
        const et = mkt.event_ticker || '';
        if (!et.includes(kalshiDate)) { offDate += 1; continue; }
        const series = et.split('-')[0] || '';
        if (ALL_SERIES.includes(series)) {
          // Already archived by the per-series loop. NOT archiving it twice is
          // right; not COUNTING it was the same defect as the Python side's
          // `if not ticker: continue` -- a row the source returned that the
          // capture could neither show nor explain. The first live v4 capture
          // reported unaccounted: 4, and these were all four of them.
          alreadyCovered += 1;
          continue;
        }
        if (discoveredUnknownSeriesMarkets.length >= BROAD_DISCOVERY_ENTRY_CAP) {
          // PHASE E: hitting a safety cap is a TRUNCATION, not a stop. The
          // old 500-entry cap reported 500 whether the exchange held 500 or
          // 50,000.
          broadEntryCapHit = true;
          break;
        }
        discoveredUnknownSeriesMarkets.push(parseMarketRecord(mkt, et, snapshotTs));
      }
      recordExclusion('broad_discovery_not_for_this_slate_date', offDate);
      recordExclusion('broad_discovery_already_covered_by_series_pass', alreadyCovered);
      if (broadEntryCapHit) {
        const broadPagination = paginations[paginations.length - 1];
        broadPagination.complete = false;
        broadPagination.truncationReason = TRUNCATION_ENTRY_CAP;
      }
    } catch (e) {
      broadDiscoveryError = e.message;
      console.log(`[kalshisearch v4] broad discovery pass failed: ${e.message}`);
    }

    const byType = {};
    const byEvent = {};
    for (const m of allMarkets) {
      byType[m.market_type] = (byType[m.market_type] || 0) + 1;
      byEvent[m.event_ticker] = (byEvent[m.event_ticker] || 0) + 1;
    }

    const summary = summarizeCapture(paginations, { marketsArchived: allMarkets.length });
    const recordsReceived = paginations.reduce((n, p) => n + p.recordsReceived, 0);
    const excludedTotal = Object.values(exclusions).reduce((n, v) => n + v, 0);

    const result = {
      date:          todayET,
      kalshi_date:   kalshiDate,
      fetched_at:    snapshotTs,
      total_markets: allMarkets.length,
      by_type:       byType,
      by_event:      byEvent,
      series_counts: seriesResults,
      markets:       allMarkets,
      results:       allMarkets.map(m => ({
        ticker:       m.market_ticker,
        title:        m.title,
        subtitle:     m.subtitle,
        market_type:  m.market_type,
        event_ticker: m.event_ticker,
      })),
      discoveredUnknownSeriesMarkets,
      discoveredUnknownSeriesCount: discoveredUnknownSeriesMarkets.length,
      broadDiscoveryError,
      fetchFailures,
      fetchFailureCount: fetchFailures.length,
      priceFetchFailureCount: fetchFailures.filter(f => f.scope === 'series').length,

      // ---- the completeness contract -------------------------------------
      captureContractVersion: CAPTURE_CONTRACT_VERSION,
      fetchOrder,
      rotationSeed: seed,
      pagination: paginations,
      exclusions,
      // DISCOVERED == ARCHIVED + EXCLUDED + (unknown, which must be zero).
      reconciliation: {
        sourceRecordsReceived: recordsReceived,
        marketsArchived: allMarkets.length,
        discoveredUnknownSeriesArchived: discoveredUnknownSeriesMarkets.length,
        explicitlyExcluded: excludedTotal,
        unaccounted: recordsReceived - allMarkets.length
                     - discoveredUnknownSeriesMarkets.length - excludedTotal,
      },
      ...summary,
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch (error) {
    // PHASE G: a capture that failed must still leave DURABLE EVIDENCE.
    //
    // This used to return `{error, date}` -- a body with no `markets` key at
    // all. The capture workflow's `markets_count > 0` gate then skipped
    // archiving entirely, so a failed capture and a capture that never ran
    // were indistinguishable in the archive forever after. The shape below is
    // a well-formed snapshot that happens to contain nothing, and it is
    // returned with HTTP 200 precisely so the workflow archives it.
    const result = {
      date:          todayET,
      kalshi_date:   kalshiDate,
      fetched_at:    snapshotTs,
      total_markets: 0,
      by_type:       {},
      by_event:      {},
      series_counts: {},
      markets:       [],
      results:       [],
      discoveredUnknownSeriesMarkets: [],
      discoveredUnknownSeriesCount: 0,
      broadDiscoveryError: null,
      fetchFailures,
      fetchFailureCount: fetchFailures.length,
      priceFetchFailureCount: fetchFailures.filter(f => f.scope === 'series').length,
      captureContractVersion: CAPTURE_CONTRACT_VERSION,
      pagination: paginations,
      exclusions: {},
      reconciliation: {
        sourceRecordsReceived: 0, marketsArchived: 0,
        discoveredUnknownSeriesArchived: 0, explicitlyExcluded: 0, unaccounted: 0,
      },
      captureStatus: CAPTURE_FAILED,
      captureComplete: false,
      seriesAttempted: paginations.filter(p => p && p.scope === 'series').length,
      seriesIncomplete: ALL_SERIES.slice().sort(),
      truncationReasons: [TRUNCATION_TRANSPORT],
      captureError: error.message,
    };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);
  }
}
