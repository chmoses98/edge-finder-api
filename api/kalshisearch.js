/**
 * kalshisearch.js — v2.0
 * 
 * Fetches ALL Kalshi MLB markets for today using the Events endpoint
 * with nested markets enabled. For each event (game), enumerates every
 * associated market, classifies it by type, and returns a structured response.
 *
 * This replaces the search-term approach with proper event enumeration.
 * 
 * Output structure per market:
 *   event_ticker, market_ticker, title, subtitle,
 *   open_time, close_time, market_type, odds snapshot
 */

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { date, callback } = req.query;

  // Resolve date — default to today ET
  const todayET = date || new Date().toLocaleDateString('en-CA', {
    timeZone: 'America/New_York'
  });

  // Build Kalshi date format: 2026-06-04 → 26JUN04
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const d = new Date(todayET + 'T12:00:00Z');
  const kalshiDate = String(d.getUTCFullYear()).slice(2) +
    months[d.getUTCMonth()] +
    String(d.getUTCDate()).padStart(2, '0');

  const KALSHI_BASE = 'https://api.elections.kalshi.com/trade-api/v2';
  const SERIES = 'KXMLBGAME';
  const snapshotTs = new Date().toISOString();

  // ── Market classifier ──────────────────────────────────────────────────────
  function classifyMarket(ticker, title, subtitle) {
    const t = (title || '').toLowerCase();
    const s = (subtitle || '').toLowerCase();
    const k = (ticker || '').toLowerCase();
    const combined = `${t} ${s} ${k}`;

    if (combined.includes('nrfi') || combined.includes('no run first inning')) return 'nrfi';
    if (combined.includes('yrfi') || combined.includes('first inning run') ||
        combined.includes('score in the first') || combined.includes('runs in the 1st')) return 'yrfi';

    const isF5 = combined.includes('first 5') || combined.includes('1st 5') || k.includes('f5');
    if (isF5) {
      if (combined.includes('wins by') || combined.includes('run line') ||
          combined.includes('1.5 runs') || combined.includes('2.5 runs')) return 'f5_spread';
      return 'f5_moneyline';
    }

    if (combined.includes('wins by') || combined.includes('run line') ||
        combined.includes('-1.5') || combined.includes('+1.5')) return 'spread';

    if (combined.includes('score over') || combined.includes('scores over') ||
        combined.includes('team total')) return 'team_total';

    if (combined.includes('total runs') || combined.includes('combined') ||
        (combined.includes('total') && (combined.includes('over') || combined.includes('under')) &&
         !combined.includes('inning'))) return 'total';

    if (combined.includes('wins') || combined.includes('winner') ||
        combined.includes('moneyline')) return 'moneyline';

    return 'unknown';
  }

  // ── Normalize Kalshi price (cents or dollars → decimal) ──────────────────
  function normPrice(v) {
    if (v == null) return null;
    const f = parseFloat(v);
    return isNaN(f) ? null : (f > 1.0 ? f / 100 : f);
  }

  function computeAmericanOdds(mid) {
    if (!mid || mid <= 0 || mid >= 1) return null;
    return mid >= 0.5
      ? Math.round(-(mid / (1 - mid)) * 100)
      : Math.round(((1 - mid) / mid) * 100);
  }

  // ── Paginate helper ────────────────────────────────────────────────────────
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

  // ── Parse market record ────────────────────────────────────────────────────
  function parseMarket(mkt, eventTicker, eventOpenTime, eventCloseTime) {
    const ticker    = mkt.ticker || '';
    const title     = mkt.title || '';
    const subtitle  = mkt.subtitle || '';
    const openTime  = mkt.open_time || eventOpenTime || '';
    const closeTime = mkt.close_time || eventCloseTime || '';
    const marketType = classifyMarket(ticker, title, subtitle);

    const yesBid = normPrice(mkt.yes_bid ?? mkt.yes_bid_dollars);
    const yesAsk = normPrice(mkt.yes_ask ?? mkt.yes_ask_dollars);
    const last   = normPrice(mkt.last_price ?? mkt.last_price_dollars);
    const mid    = (yesBid != null && yesAsk != null) ? (yesBid + yesAsk) / 2
                 : (yesBid ?? yesAsk);
    const impliedPct = mid != null ? Math.round(mid * 1000) / 10 : null;

    return {
      event_ticker:  eventTicker,
      market_ticker: ticker,
      title,
      subtitle,
      open_time:     openTime,
      close_time:    closeTime,
      market_type:   marketType,
      status:        mkt.status || 'open',
      snapshot_ts:   snapshotTs,
      yes_bid:       yesBid,
      yes_ask:       yesAsk,
      mid:           mid != null ? Math.round(mid * 10000) / 10000 : null,
      implied_pct:   impliedPct,
      american_odds: mid != null ? computeAmericanOdds(mid) : null,
      last_price:    last,
      volume:        parseFloat(mkt.volume ?? mkt.volume_fp ?? 0) || 0,
      open_interest: parseFloat(mkt.open_interest ?? mkt.open_interest_fp ?? 0) || 0,
    };
  }

  try {
    const allMarkets = [];

    // ── Strategy 1: Events endpoint with nested markets ────────────────────
    const eventsUrl = `${KALSHI_BASE}/events?series_ticker=${SERIES}&status=open&with_nested_markets=true&limit=100`;
    const events = await fetchAllPages(eventsUrl, 'events');

    const todayEvents = events.filter(e => (e.event_ticker || '').includes(kalshiDate));

    for (const event of todayEvents) {
      const et = event.event_ticker;
      let markets = event.markets || [];

      // If no nested markets came with the event, try fetching per-event
      if (!markets.length) {
        const evR = await fetch(`${KALSHI_BASE}/events/${et}?with_nested_markets=true`);
        if (evR.ok) {
          const evData = await evR.json();
          const ev = evData.event || evData;
          markets = ev.markets || [];
        }
      }

      for (const mkt of markets) {
        allMarkets.push(parseMarket(mkt, et, event.open_time, event.close_time));
      }
    }

    // ── Strategy 2: Fallback — /markets with series_ticker ────────────────
    if (!allMarkets.length) {
      const mktsUrl = `${KALSHI_BASE}/markets?series_ticker=${SERIES}&status=open&limit=200`;
      const mkts = await fetchAllPages(mktsUrl, 'markets');
      const todayMkts = mkts.filter(m => (m.event_ticker || '').includes(kalshiDate));
      for (const mkt of todayMkts) {
        allMarkets.push(parseMarket(mkt, mkt.event_ticker || '', '', mkt.close_time));
      }
    }

    // ── Summarize ──────────────────────────────────────────────────────────
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
      markets:       allMarkets,
      // Legacy compat: also expose 'results' array for preview_kalshi.py
      results:       allMarkets.map(m => ({
        ticker:    m.market_ticker,
        title:     m.title,
        subtitle:  m.subtitle,
        market_type: m.market_type,
        event_ticker: m.event_ticker,
      })),
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
