// api/savantdefense.js
// =======================
// Hitter Projection Engine -- Phase 3 defensive (OAA) ingestion.
//
// Baseball Savant's Outs Above Average leaderboard
// (https://baseballsavant.mlb.com/leaderboard/outs_above_average) is the
// one authoritative, programmatic (CSV-export) source for MLB defensive
// range data -- same CSV-export mechanism every other Savant fetcher in
// this repo already uses, not a scrape of unstable presentation HTML.
//
// COLUMN-NAME VERIFICATION CAVEAT (see docs/HITTER_ENVIRONMENT_FOUNDATION.md):
// exact column names could not be verified against a live response from
// this development environment (Savant traffic is blocked here). Parsing
// below uses the same multi-candidate findCol() resilience
// api/savant.js's fetchPlatoonSplits() and api/savantbattracking.js
// already rely on for the same reason -- an unresolved field stays null,
// never a fabricated number.
//
// This endpoint returns PLAYER-level OAA rows; team-level aggregation
// (sum of each fielder's OAA on that team) happens server-side here so
// scripts/fetch_savant_defense.py can persist one snapshot per team,
// matching how this repo's hitter-matchup context reasons about "the
// opposing defense" as a team unit (see lib.research.defense_store's
// own docstring for why).
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { year = '2026' } = req.query;

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
    const url = `https://baseballsavant.mlb.com/leaderboard/outs_above_average?type=Fielder&startYear=${encodeURIComponent(year)}&endYear=${encodeURIComponent(year)}&split=no&team=&range=year&min=1&pos=&roles=&viz=hide&csv=true`;
    const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (!r.ok) throw new Error(`Savant OAA fetch failed: ${r.status}`);
    const rows = parseCSV(await r.text());

    const teams = {};
    let resolvedFieldCount = 0;
    for (const row of rows) {
      const team = findCol(row, 'team_abbrev', 'team', 'entity_abbrev');
      const oaa = pf(findCol(row, 'outs_above_average', 'oaa', 'run_value'));
      const pos = findCol(row, 'primary_pos_formatted', 'position', 'pos');
      if (!team) continue;
      if (!teams[team]) teams[team] = { teamOAA: 0, infieldOAA: 0, outfieldOAA: 0, armStrengthOAA: 0, playerCount: 0 };
      if (oaa !== null) {
        teams[team].teamOAA += oaa;
        resolvedFieldCount++;
        const infieldPositions = ['1B', '2B', '3B', 'SS'];
        const outfieldPositions = ['LF', 'CF', 'RF'];
        if (pos && infieldPositions.includes(pos)) teams[team].infieldOAA += oaa;
        if (pos && outfieldPositions.includes(pos)) teams[team].outfieldOAA += oaa;
      }
      teams[team].playerCount += 1;
    }
    for (const t of Object.keys(teams)) {
      teams[t].teamOAA = Math.round(teams[t].teamOAA * 10) / 10;
      teams[t].infieldOAA = Math.round(teams[t].infieldOAA * 10) / 10;
      teams[t].outfieldOAA = Math.round(teams[t].outfieldOAA * 10) / 10;
    }

    return res.status(200).json({
      ok: true, year, fetchedAt: new Date().toISOString(),
      teamCount: Object.keys(teams).length,
      resolvedFieldCount,
      csvHeaders: rows.length ? Object.keys(rows[0]) : [],
      teams,
    });
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message,
      fallback: 'Baseball Savant may be blocking automated requests, or the OAA leaderboard endpoint/columns have changed.' });
  }
}
