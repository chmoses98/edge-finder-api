// api/savantcatcherframing.js
// ==============================
// Hitter Projection Engine -- Phase 3 catcher framing ingestion.
//
// Baseball Savant's Catcher Framing leaderboard
// (https://baseballsavant.mlb.com/leaderboard/catcher-framing) -- same
// CSV-export mechanism every other Savant fetcher in this repo already
// uses. Column names could not be verified against a live response in
// this development environment (Savant traffic is blocked here) -- see
// docs/HITTER_ENVIRONMENT_FOUNDATION.md. Multi-candidate findCol()
// resilience, same pattern as api/savantbattracking.js; an unresolved
// field stays null, never fabricated.
//
// Blocking/pop-time are deliberately NOT fetched here -- this mission's
// spec explicitly deprioritizes them for a hitter-prop foundation.
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { year = '2026', minP = '500' } = req.query;

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
    const url = `https://baseballsavant.mlb.com/leaderboard/catcher-framing?year=${encodeURIComponent(year)}&team=&min=${encodeURIComponent(minP)}&csv=true`;
    const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (!r.ok) throw new Error(`Savant catcher-framing fetch failed: ${r.status}`);
    const rows = parseCSV(await r.text());

    const catchers = {};
    for (const row of rows) {
      const id = findCol(row, 'player_id', 'id', 'catcher_id');
      if (!id) continue;
      catchers[id] = {
        playerId: id,
        name: findCol(row, 'name', 'player_name', 'last_name, first_name'),
        framingRunsExtra: pf(findCol(row, 'runs_extra_strikes', 'framing_runs', 'strike_runs')),
        strikeRatePlusMinus: pf(findCol(row, 'strike_rate', 'rate_plus_minus', 'diff_pct')),
      };
    }

    const resolvedFieldCount = Object.values(catchers).reduce(
      (acc, c) => acc + Object.values(c).filter(v => v !== null && typeof v === 'number').length, 0
    );

    return res.status(200).json({
      ok: true, year, fetchedAt: new Date().toISOString(),
      catcherCount: Object.keys(catchers).length,
      resolvedFieldCount,
      csvHeaders: rows.length ? Object.keys(rows[0]) : [],
      catchers,
    });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message,
      fallback: 'Baseball Savant may be blocking automated requests, or the catcher-framing leaderboard endpoint/columns have changed.' });
  }
}
