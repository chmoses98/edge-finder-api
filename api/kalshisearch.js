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

  async function fetchAllPages(baseUrl, key, maxPages = 10) {
    const results = [];
    let cursor = '';
    for (let page = 0; page < maxPages; page++) {
      const url = cursor ? `${baseUrl}&cursor=${cursor}` : baseUrl;
      const r = await fetch(url, { headers: { 'Content-Type': 'application/json' } });
      if (!r.ok) break;
      const data = await r.json();
      const items = data[key] || [];
      results.push(...items);
      cursor = data.cursor || '';
      if (!cursor || !items.length) break;
    }
    return results;
  }


  try {
    const allMarkets = [];
    const seriesResults = {};

    // Fetch each series independently
    for (const series of ALL_SERIES) {
      const mktsUrl = `${KALSHI_BASE}/markets?series_ticker=${series}&status=open&limit=200`;
      const mkts = await fetchAllPages(mktsUrl, 'markets');
      const todayMkts = mkts.filter(m => (m.event_ticker || '').includes(kalshiDate));
      seriesResults[series] = todayMkts.length;
      for (const mkt of todayMkts) {
        allMarkets.push(parseMarketRecord(mkt, mkt.event_ticker, snapshotTs));
      }
    }

    console.log(`[kalshisearch v3] ${kalshiDate} | series: ${JSON.stringify(seriesResults)}`);

    // Model Performance Phase 2A correction: ALL_SERIES above is a fixed,
    // pre-known allowlist -- a real Kalshi series this repository doesn't
    // yet know the name of (e.g. the real F3/F7 series tickers, per
    // user-confirmed real-money wagers placed on both) would never be
    // queried by the per-series loop above, no matter how long ALL_SERIES
    // grows. This broad, unfiltered pass SUPPLEMENTS (never replaces) that
    // loop: it fetches open markets with no series_ticker filter, then
    // retains any market whose series ISN'T already in ALL_SERIES under a
    // separate, additive field so nothing is silently dropped. Existing
    // consumers (scripts/build_kalshi_registry.py's backfill,
    // scripts/merge_odds.py) read only markets/results/series_counts and
    // are therefore completely unaffected by this addition -- it is pure
    // research-visibility scaffolding, capped defensively at 500 entries
    // to avoid unbounded response growth from an unrelated exchange-wide
    // category briefly sharing this date's ticker substring.
    const discoveredUnknownSeriesMarkets = [];
    let broadDiscoveryError = null;
    try {
      const broadUrl = `${KALSHI_BASE}/markets?status=open&limit=1000`;
      const broadMkts = await fetchAllPages(broadUrl, 'markets');
      for (const mkt of broadMkts) {
        const et = mkt.event_ticker || '';
        if (!et.includes(kalshiDate)) continue;
        const series = et.split('-')[0] || '';
        if (ALL_SERIES.includes(series)) continue; // already covered above
        if (discoveredUnknownSeriesMarkets.length >= 500) break;
        discoveredUnknownSeriesMarkets.push(parseMarketRecord(mkt, et, snapshotTs));
      }
    } catch (e) {
      broadDiscoveryError = e.message;
      console.log(`[kalshisearch v3] broad discovery pass failed: ${e.message}`);
    }

    const byType = {};
    const byEvent = {};
    for (const m of allMarkets) {
      byType[m.market_type] = (byType[m.market_type] || 0) + 1;
      byEvent[m.event_ticker] = (byEvent[m.event_ticker] || 0) + 1;
    }

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
      // Model Performance Phase 2A correction: additive, research-only.
      // Never read by scripts/build_kalshi_registry.py or
      // scripts/merge_odds.py -- see the broad-discovery block above.
      discoveredUnknownSeriesMarkets,
      discoveredUnknownSeriesCount: discoveredUnknownSeriesMarkets.length,
      broadDiscoveryError,
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch (error) {
    const result = { error: error.message, date: todayET };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
