export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  try {
    const response = await fetch(
      'https://raw.githubusercontent.com/chmoses98/edge-finder-api/main/slate-ui.json',
      { headers: { 'Cache-Control': 'no-cache' } }
    );
    if (!response.ok) {
      return res.status(response.status).json({ error: 'Failed to fetch slate-ui.json' });
    }
    const data = await response.json();
    res.setHeader('Cache-Control', 's-maxage=60, stale-while-revalidate');
    return res.status(200).json(data);
  } catch (error) {
    return res.status(500).json({ error: error.message });
  }
}
