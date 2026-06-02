// /api/kalshi_markets.js
// Fetches all open Kalshi baseball markets grouped by series ticker.
// Used to discover what series exist for F5, TT, NRFI.

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { date } = req.query;

  // Format today's date for Kalshi ticker matching: 26JUN02
  const d = date ? new Date(date + 'T12:00:00Z') : new Date();
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const kDate = String(d.getUTCFullYear()).slice(2) + months[d.getUTCMonth()] + String(d.getUTCDate()).padStart(2,'0');

  try {
    // Pull all open markets (large limit to catch everything)
    const r = await fetch(
      'https://external-api.kalshi.com/trade-api/v2/markets?status=open&limit=1000',
      { headers: { 'Content-Type': 'application/json' } }
    );
    const data = await r.json();
    const all = data.markets || [];

    // Filter to today's baseball markets
    const today = all.filter(m => {
      const et = m.event_ticker || m.ticker || '';
      return et.includes(kDate);
    });

    // Group by series ticker
    const bySeries = {};
    for (const m of today) {
      const series = (m.event_ticker || '').split('-')[0] || 'UNKNOWN';
      if (!bySeries[series]) bySeries[series] = [];
      bySeries[series].push({
        ticker: m.ticker,
        eventTicker: m.event_ticker,
        title: m.title,
        bid: m.yes_bid_dollars,
        ask: m.yes_ask_dollars,
        volume: m.volume_fp,
        closeTime: m.close_time,
      });
    }

    return res.status(200).json({
      kDate,
      totalOpen: all.length,
      todayCount: today.length,
      seriesFound: Object.keys(bySeries),
      bySeries,
    });

  } catch(e) {
    return res.status(500).json({ error: e.message });
  }
}
