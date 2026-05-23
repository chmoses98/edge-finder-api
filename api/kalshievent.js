export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  try {
    // Use the series_ticker that we KNOW works
    const url = `https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200`;
    const response = await fetch(url);
    const data = await response.json();
    const markets = data.markets || [];

    // Show ALL unique market titles to find every market type Kalshi offers
    const uniqueTitles = [...new Set(markets.map(m => m.title))].sort();

    // Group by event to see all markets per game
    const byEvent = {};
    for (const m of markets) {
      const et = m.event_ticker;
      if (!byEvent[et]) byEvent[et] = [];
      byEvent[et].push({
        ticker: m.ticker,
        title: m.title,
        yesBid: m.yes_bid_dollars,
        yesAsk: m.yes_ask_dollars,
        volume: m.volume_fp
      });
    }

    // Pick one event as a sample to see all its markets
    const sampleEvent = Object.keys(byEvent)[0];

    const result = {
      totalMarkets: markets.length,
      uniqueEventCount: Object.keys(byEvent).length,
      uniqueMarketTitles: uniqueTitles,
      sampleEvent: sampleEvent,
      sampleEventMarkets: byEvent[sampleEvent] || []
    };

    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(200).json(result);

  } catch (error) {
    const result = { error: error.message };
    if (callback) {
      res.setHeader('Content-Type', 'application/javascript');
      return res.status(200).send(`${callback}(${JSON.stringify(result)})`);
    }
    return res.status(500).json(result);
  }
}
