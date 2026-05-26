export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { playerIds, year = '2026' } = req.query;

  function parseCSV(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) return [];

    function splitCSVLine(line) {
      const result = [];
      let current = '';
      let inQuotes = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"') {
          inQuotes = !inQuotes;
        } else if (ch === ',' && !inQuotes) {
          result.push(current.trim());
          current = '';
        } else {
          current += ch;
        }
      }
      result.push(current.trim());
      return result;
    }

    const headers = splitCSVLine(lines[0]);
    return lines.slice(1).map(line => {
      const values = splitCSVLine(line);
      const obj = {};
      headers.forEach((h, i) => { obj[h] = values[i] || ''; });
      return obj;
    });
  }

  function pf(val) {
    const n = parseFloat(val);
    return isNaN(n) ? null : n;
  }

  function isHighWalkRisk(bbPct) {
    if (bbPct === null) return false;
    return bbPct > 9.2;
  }

  try {
    const pitcherUrl = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=pitcher&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,hard_hit_percent,xera,exit_velocity_avg,barrel_batted_rate&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`;

    const batterUrl = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=batter&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,xwoba,hard_hit_percent,barrel_batted_rate,exit_velocity_avg&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`;

    const [pitcherRes, batterRes] = await Promise.all([
      fetch(pitcherUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(batterUrl,  { headers: { 'User-Agent': 'Mozilla/5.0' } })
    ]);

    if (!pitcherRes.ok) throw new Error(`Savant pitcher fetch failed: ${pitcherRes.status}`);
    if (!batterRes.ok)  throw new Error(`Savant batter fetch failed: ${batterRes.status}`);

    const pitcherCSV = await pitcherRes.text();
    const batterCSV  = await batterRes.text();

    // Debug: expose raw first line so we can see exact column names
    const pitcherHeaders = pitcherCSV.split('\n')[0];
    const batterHeaders  = batterCSV.split('\n')[0];

    const rawPitchers = parseCSV(pitcherCSV);
    const rawBatters  = parseCSV(batterCSV);

    // Debug: expose first raw row so we can see exact keys
    const samplePitcher = rawPitchers[0] || {};
    const sampleBatter  = rawBatters[0]  || {};

    const pitchers = {};
    for (const p of rawPitchers) {
      const id = p['player_id'];
      if (!id) continue;
      const bbPct = pf(p['bb_percent']);
      const xERA  = pf(p['xera']);

      // Try every possible name key Savant might use
      const name = p['last_name, first_name']
        || p['last_name,first_name']
        || p['name']
        || p['player_name']
        || '';

      pitchers[id] = {
        playerId:     id,
        name,
        year:         p['year'],
        kPct:         pf(p['k_percent']),
        bbPct:        bbPct,
        whiffPct:     pf(p['whiff_percent']),
        xERA:         xERA,
        hardHitPct:   pf(p['hard_hit_percent']),
        exitVeloAvg:  pf(p['exit_velocity_avg']),
        barrelPct:    pf(p['barrel_batted_rate']),
        highWalkRisk: isHighWalkRisk(bbPct),
        eliteStarter: xERA !== null && xERA < 2.50,
      };
    }

    const batters = {};
    for (const b of rawBatters) {
      const id = b['player_id'];
      if (!id) continue;

      const name = b['last_name, first_name']
        || b['last_name,first_name']
        || b['name']
        || b['player_name']
        || '';

      batters[id] = {
        playerId:    id,
        name,
        year:        b['year'],
        kPct:        pf(b['k_percent']),
        bbPct:       pf(b['bb_percent']),
        whiffPct:    pf(b['whiff_percent']),
        xwOBA:       pf(b['xwoba']),
        hardHitPct:  pf(b['hard_hit_percent']),
        barrelPct:   pf(b['barrel_batted_rate']),
        exitVeloAvg: pf(b['exit_velocity_avg']),
      };
    }

    let filteredPitchers = pitchers;
    let filteredBatters  = batters;

    if (playerIds) {
      const ids = playerIds.split(',').map(id => id.trim());
      filteredPitchers = {};
      filteredBatters  = {};
      for (const id of ids) {
        if (pitchers[id]) filteredPitchers[id] = pitchers[id];
        if (batters[id])  filteredBatters[id]  = batters[id];
      }
    }

    return res.status(200).json({
      ok: true,
      year,
      fetchedAt: new Date().toISOString(),
      debug: {
        pitcherHeaders,
        batterHeaders,
        samplePitcherKeys: Object.keys(samplePitcher),
        sampleBatterKeys:  Object.keys(sampleBatter),
      },
      pitcherCount: Object.keys(filteredPitchers).length,
      batterCount:  Object.keys(filteredBatters).length,
      pitchers: filteredPitchers,
      batters:  filteredBatters,
    });

  } catch (err) {
    return res.status(500).json({
      ok: false,
      error: err.message,
      fallback: 'Baseball Savant may be blocking automated requests.'
    });
  }
}
