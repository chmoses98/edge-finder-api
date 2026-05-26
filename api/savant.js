export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { playerIds, year = '2026' } = req.query;

  // Helper: parse Savant CSV — handles the "last_name, first_name" column
  // which contains a comma inside quotes
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

  // Helper: safely parse float
  function pf(val) {
    const n = parseFloat(val);
    return isNaN(n) ? null : n;
  }

  // BB% to approximate BB/9 conversion
  // Average MLB game has ~38 batters faced per 9 innings
  // BB/9 ≈ (BB% / 100) * 38 * (9 / innings_per_game)
  // Simpler: BB% > 10% ≈ BB/9 > 3.8, BB% > 9% ≈ BB/9 > 3.4
  // Model rule threshold is BB/9 > 3.5 → use BB% > 9.2% as equivalent
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

    const rawPitchers = parseCSV(pitcherCSV);
    const rawBatters  = parseCSV(batterCSV);

    // ── PARSE PITCHERS ─────────────────────────────────────────────────────────
    const pitchers = {};
    for (const p of rawPitchers) {
      const id = p['player_id'];
      if (!id) continue;
      const bbPct = pf(p['bb_percent']);
      const xERA  = pf(p['xera']);
      pitchers[id] = {
        playerId:     id,
        name:         p['last_name, first_name'] || '',
        year:         p['year'],
        kPct:         pf(p['k_percent']),
        bbPct:        bbPct,
        whiffPct:     pf(p['whiff_percent']),
        xERA:         xERA,
        hardHitPct:   pf(p['hard_hit_percent']),
        exitVeloAvg:  pf(p['exit_velocity_avg']),
        barrelPct:    pf(p['barrel_batted_rate']),
        // Model rule flags
        highWalkRisk: isHighWalkRisk(bbPct),   // BB% > 9.2% ≈ BB/9 > 3.5
        eliteStarter: xERA !== null && xERA < 2.50,
      };
    }

    // ── PARSE BATTERS ──────────────────────────────────────────────────────────
    const batters = {};
    for (const b of rawBatters) {
      const id = b['player_id'];
      if (!id) continue;
      batters[id] = {
        playerId:    id,
        name:        b['last_name, first_name'] || '',
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

    // ── FILTER BY PLAYER IDs IF PROVIDED ──────────────────────────────────────
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
