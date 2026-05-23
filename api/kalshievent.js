export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { callback } = req.query;

  try {
    // Pull ALL open KXMLBGAME markets — not filtered by series
    // This gets every contract under every game event
    const url = `https://external-api.kalshi.com/trade-api/v2/markets?status=open&limit=1000`;
    const response = await fetch(url);
    const data = await response.json();
    const markets = data.markets || [];

    // Filter to KXMLBGAME series only
    const gameMarkets = markets.filter(m =>
      (m.event_ticker || '').startsWith('KXMLBGAME')
    );

    // Group all markets by event ticker
    const byEvent = {};
    for (const m of gameMarkets) {
      const et = m.event_ticker;
      if (!byEvent[et]) byEvent[et] = [];
      byEvent[et].push({
        ticker: m.ticker,
        title: m.title,
        subtitle: m.subtitle || '',
        yesBid: m.yes_bid_dollars,
        yesAsk: m.yes_ask_dollars,
        lastPrice: m.last_price_dollars,
        volume: m.volume_fp,
        closeTime: m.close_time
      });
    }

    // Show all unique titles found across all games
    const uniqueTitles = [...new Set(gameMarkets.map(m => m.title))];

    const result = {
      totalKXMLBGAMEMarkets: gameMarkets.length,
      uniqueEventCount: Object.keys(byEvent).length,
      uniqueMarketTitles: uniqueTitles,
      byEvent: byEvent
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
