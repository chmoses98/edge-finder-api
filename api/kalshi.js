export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');

  if (req.method === 'OPTIONS') return res.status(200).end();

  const { date, callback } = req.query;

  // Default to today ET
  const today = date || new Date().toLocaleDateString('en-CA', {
    timeZone: 'America/New_York'
  });

  // Format date for Kalshi ticker e.g. 2026-05-23 -> 26MAY23
  const d = new Date(today + 'T12:00:00Z');
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const kalshiDate = String(d.getUTCFullYear()).slice(2) +
    months[d.getUTCMonth()] +
    String(d.getUTCDate()).padStart(2, '0');

  try {
    // Fetch all open MLB markets for today
    // Kalshi MLB moneyline series ticker is KXMLB
    // Event tickers look like KXMLB-26MAY23-NYYWIN (Yankees win today)
    const url = `https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLB&status=open&limit=200`;

    const response = await fetch(url, {
      headers: {
        'Content-Type': 'application/json'
      }
    });

    if (!response.ok) {
      const err = await response.text();
      const result = { error: `Kalshi API error: ${response.status} ${err}`, date: today };
      if (callback) {
        res.setHeader('Content-Type', 'application/javascript');
        return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
      }
      return res.status(200).json(result);
    }

    const data = await response.json();
    const allMarkets = data.markets || [];

    // Filter to today's markets only
    const todayMarkets = allMarkets.filter(m =>
      m.event_ticker && m.event_ticker.includes(kalshiDate)
    );

    // Parse into clean format
    // Kalshi prices are in cents (0-100), yes_bid is what you pay to bet YES
    // Implied probability = yes_ask / 100 (what market maker will sell YES for)
    // For our purposes: yes_bid = best you can buy YES at, yes_ask = what you pay
    const markets = todayMarkets.map(m => {
      const yesBid = parseFloat(m.yes_bid) || 0;   // cents
      const yesAsk = parseFloat(m.yes_ask) || 0;   // cents
      const lastPrice = parseFloat(m.last_price) || 0;
      const midpoint = (yesBid + yesAsk) / 2;

      // Convert cents to implied probability and American odds
      const impliedPct = midpoint / 100;
      const americanOdds = impliedPct >= 0.5
        ? Math.round(-(impliedPct / (1 - impliedPct)) * 100)
        : Math.round(((1 - impliedPct) / impliedPct) * 100);

      return {
        ticker: m.ticker,
        eventTicker: m.event_ticker,
        title: m.title,
        subtitle: m.subtitle || '',
        yesBid: yesBid,       // cents — best price to buy YES
        yesAsk: yesAsk,       // cents — what you pay for YES
        lastPrice: lastPrice,
        midpoint: midpoint,   // cents
        impliedPct: Math.round(impliedPct * 1000) / 10,  // percentage, 1dp
        americanOdds: americanOdds,
        volume: m.volume || 0,
        openInterest: m.open_interest || 0,
        closeTime: m.close_time || null,
        status: m.status
      };
    });

    // Group by event
    const events = {};
    for (const m of markets) {
      const et = m.eventTicker;
      if (!events[et]) events[et] = [];
      events[et].push(m);
    }

    const result = {
      date: today,
      kalshiDate,
      totalMarkets: markets.length,
      events: Object.entries(events).map(([ticker, mkts]) => ({
        eventTicker: ticker,
        markets: mkts
      })),
      markets // flat list too for easy scanning
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch (error) {
    const result = { error: error.message, date: today };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
