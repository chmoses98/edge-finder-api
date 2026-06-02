export default async function handler(req, res) {
  try {
    // Test the broad fetch
    const r1 = await fetch('https://external-api.kalshi.com/trade-api/v2/markets?series_ticker=KXMLBGAME&status=open&limit=200');
    const d1 = r1.ok ? await r1.json() : { error: r1.status, text: await r1.text() };
    const count = (d1.markets || []).length;
    const sample = (d1.markets || []).slice(0, 3).map(m => ({ ticker: m.ticker, title: m.title, bid: m.yes_bid_dollars, ask: m.yes_ask_dollars }));
    
    // Also test the event tickers
    const tickers = (d1.markets || []).map(m => m.event_ticker).filter(Boolean);
    const uniqueSeries = [...new Set(tickers.map(t => t.split('-')[0]))];
    
    return res.status(200).json({ 
      status: r1.status, 
      count, 
      sample, 
      uniqueSeries,
      allTickers: tickers.slice(0, 10)
    });
  } catch(e) {
    return res.status(500).json({ error: e.message });
  }
}
