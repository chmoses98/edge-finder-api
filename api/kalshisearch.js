/**
 * kalshisearch.js — v3.0
 *
 * Fetches ALL Kalshi MLB markets for today across ALL series:
 *   KXMLBGAME, KXMLBSPREAD, KXMLBTOTAL, KXMLBTEAMTOTAL,
 *   KXMLBF5, KXMLBF5SPREAD, KXMLBF5TOTAL, KXMLBRFI
 *
 * Each series is queried independently via /markets?series_ticker=
 * because nested markets on KXMLBGAME events only returns ML markets.
 */

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
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

  // All 8 MLB series
  const ALL_SERIES = [
    'KXMLBGAME',
    'KXMLBSPREAD',
    'KXMLBTOTAL',
    'KXMLBTEAMTOTAL',
    'KXMLBF5',
    'KXMLBF5SPREAD',
    'KXMLBF5TOTAL',
    'KXMLBRFI',
  ];

  function classifyMarket(ticker, title, subtitle) {
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

    if (k.includes('kxmlbteamtotal') || combined.includes('team total') || combined.includes('scores over') || combined.includes('score over')) return 'team_total';
    if (k.includes('kxmlbtotal') || (combined.includes('total') && (combined.includes('over') || combined.includes('under')) && !combined.includes('inning'))) return 'total';
    if (k.includes('kxmlbspread') || combined.includes('wins by') || combined.includes('run line')) return 'spread';
    if (k.includes('kxmlbgame') || combined.includes('wins') || combined.includes('winner') || combined.includes('moneyline')) return 'moneyline';

    return 'unknown';
  }

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

  function parseMarket(mkt, eventTicker) {
    const ticker = mkt.ticker || '';
    const title = mkt.title || '';
    const subtitle = mkt.subtitle || '';
    const marketType = classifyMarket(ticker, title, subtitle);

    const yesBid = normPrice(mkt.yes_bid ?? mkt.yes_bid_dollars);
    const yesAsk = normPrice(mkt.yes_ask ?? mkt.yes_ask_dollars);
    const last = normPrice(mkt.last_price ?? mkt.last_price_dollars);
    const mid = (yesBid != null && yesAsk != null) ? (yesBid + yesAsk) / 2
              : (yesBid ?? yesAsk);
    const impliedPct = mid != null ? Math.round(mid * 1000) / 10 : null;

    return {
      event_ticker:  eventTicker || mkt.event_ticker || '',
      market_ticker: ticker,
      title,
      subtitle,
      open_time:     mkt.open_time || '',
      close_time:    mkt.close_time || '',
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
    const seriesResults = {};

    // Fetch each series independently
    for (const series of ALL_SERIES) {
      const mktsUrl = `${KALSHI_BASE}/markets?series_ticker=${series}&status=open&limit=200`;
      const mkts = await fetchAllPages(mktsUrl, 'markets');
      const todayMkts = mkts.filter(m => (m.event_ticker || '').includes(kalshiDate));
      seriesResults[series] = todayMkts.length;
      for (const mkt of todayMkts) {
        allMarkets.push(parseMarket(mkt, mkt.event_ticker));
      }
    }

    console.log(`[kalshisearch v3] ${kalshiDate} | series: ${JSON.stringify(seriesResults)}`);

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
