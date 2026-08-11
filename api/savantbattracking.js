// api/savantbattracking.js
// ===========================
// Hitter Projection Engine -- Phase 2 bat-tracking ingestion.
//
// Baseball Savant's bat-tracking leaderboard
// (https://baseballsavant.mlb.com/leaderboard/bat-tracking) is the one
// authoritative, programmatic (CSV-export) source for MLB bat-tracking
// data (bat speed, swing length, attack angle, squared-up rate, ...) --
// same CSV-export mechanism (`&csv=true`) every other Savant fetcher in
// this repo already uses, not a scrape of unstable presentation HTML.
//
// IMPORTANT CAVEAT (see docs/HITTER_STATCAST_FOUNDATION.md): this
// endpoint's exact column names could not be verified against a live
// response from this development environment (Savant traffic is
// blocked here, same as documented in api/savant.js's own fallback
// message). Column resolution below tries multiple plausible candidate
// names per field (the same `findCol`-style resilience api/savant.js's
// fetchPlatoonSplits() already uses for exactly this reason). Any field
// whose candidates don't match the live response resolves to null --
// never a fabricated number -- and the caller (scripts/
// fetch_savant_bat_tracking.py -> lib.research.hitter_feature_context)
// surfaces that as UNAVAILABLE_FROM_CURRENT_SOURCES rather than pretending
// success. If/when this is verified against a real response, only the
// candidate-name lists below need updating -- no schema change required
// downstream.
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { year = '2026', minSwings = '10' } = req.query;

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
    const url = `https://baseballsavant.mlb.com/leaderboard/bat-tracking?attackZone=all&type=batter&year=${encodeURIComponent(year)}&team=&min=${encodeURIComponent(minSwings)}&csv=true`;
    const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (!r.ok) throw new Error(`Savant bat-tracking fetch failed: ${r.status}`);
    const rows = parseCSV(await r.text());

    const batters = {};
    for (const row of rows) {
      const id = findCol(row, 'id', 'player_id', 'batter', 'batter_id');
      if (!id) continue;
      batters[id] = {
        playerId: id,
        name: findCol(row, 'name', 'player_name', 'last_name, first_name'),
        attempts: pf(findCol(row, 'attempts', 'swings', 'competitive_swings')),
        avgBatSpeed: pf(findCol(row, 'avg_bat_speed', 'avg_swing_speed', 'bat_speed')),
        maxBatSpeed: pf(findCol(row, 'max_bat_speed', 'hp_to_swing_max', 'fastest_swing')),
        fastSwingPct: pf(findCol(row, 'fast_swing_rate', 'hard_swing_rate', 'fast_swing_percent')),
        squaredUpRate: pf(findCol(row, 'squared_up_per_bat_contact', 'squared_up_rate', 'squared_up_percent')),
        squaredUpPerSwing: pf(findCol(row, 'squared_up_per_swing')),
        blastRate: pf(findCol(row, 'blast_percent', 'blast_per_bat_contact', 'blast_per_swing')),
        swingLength: pf(findCol(row, 'swing_length', 'avg_swing_length')),
        attackAngle: pf(findCol(row, 'attack_angle', 'avg_attack_angle')),
        idealAttackAngleRate: pf(findCol(row, 'ideal_attack_angle_rate', 'attack_angle_ideal_rate')),
        attackDirection: pf(findCol(row, 'attack_direction', 'avg_attack_direction')),
        swingTilt: pf(findCol(row, 'swing_tilt', 'swing_path_tilt')),
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
      fallback: 'Baseball Savant may be blocking automated requests, or the bat-tracking leaderboard endpoint/columns have changed.' });
  }
}
