// api/savantsprintspeed.js
// ===========================
// Hitter Projection Engine -- Phase 3 sprint-speed/baserunning ingestion.
//
// Baseball Savant's Sprint Speed leaderboard
// (https://baseballsavant.mlb.com/leaderboard/sprint_speed) -- same
// CSV-export mechanism every other Savant fetcher in this repo already
// uses. Column names could not be verified against a live response in
// this development environment (Savant traffic is blocked here) -- see
// docs/HITTER_ENVIRONMENT_FOUNDATION.md. Multi-candidate findCol()
// resilience, same pattern as api/savantbattracking.js /
// api/savantdefense.js; an unresolved field stays null, never fabricated.
//
// NOT stolen-base modeling -- this is raw speed data only, foundation
// for a future infield-hit/BABIP/extra-base-hit signal.
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { year = '2026', minOpp = '10' } = req.query;

  function parseCSV(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) return [];
    function splitCSVLine(line) {
      const result = []; let current = ''; let inQuotes = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"') { inQuotes = !inQuotes; }
        else if (ch === ',' && !inQuotes) { result.push(current); current = ''; }
        else { current += ch; }
      }
      result.push(current);
      return result;
    }
    const headers = splitCSVLine(lines[0]).map(h => h.trim());
    return lines.slice(1).map(line => {
      const values = splitCSVLine(line);
      const obj = {};
      headers.forEach((h, i) => { obj[h] = (values[i] || '').trim(); });
      return obj;
    });
  }

  function pf(val) { if (val === undefined || val === '') return null; const n = parseFloat(val); return isNaN(n) ? null : n; }
  function findCol(row, ...candidates) {
    for (const c of candidates) { if (row[c] !== undefined && row[c] !== '') return row[c]; }
    return null;
  }

  try {
    const url = `https://baseballsavant.mlb.com/leaderboard/sprint_speed?year=${encodeURIComponent(year)}&position=&team=&min=${encodeURIComponent(minOpp)}&csv=true`;
    const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (!r.ok) throw new Error(`Savant sprint-speed fetch failed: ${r.status}`);
    const rows = parseCSV(await r.text());

    const batters = {};
    for (const row of rows) {
      const id = findCol(row, 'player_id', 'id', 'runner_id');
      if (!id) continue;
      batters[id] = {
        playerId: id,
        name: findCol(row, 'name', 'player_name', 'last_name, first_name'),
        sprintSpeedFtPerSec: pf(findCol(row, 'sprint_speed', 'r_sprint_speed_top50percent')),
        homeToFirstSec: pf(findCol(row, 'hp_to_1b', 'seconds_since_hit_090', 'home_to_first')),
        boltPct: pf(findCol(row, 'bolts', 'bolt_pct', 'competitive_runs')),
      };
    }

    const resolvedFieldCount = Object.values(batters).reduce(
      (acc, b) => acc + Object.values(b).filter(v => v !== null && typeof v === 'number').length, 0
    );

    return res.status(200).json({
      ok: true, year, fetchedAt: new Date().toISOString(),
      batterCount: Object.keys(batters).length,
      resolvedFieldCount,
      csvHeaders: rows.length ? Object.keys(rows[0]) : [],
      batters,
    });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message,
      fallback: 'Baseball Savant may be blocking automated requests, or the sprint-speed leaderboard endpoint/columns have changed.' });
  }
}
