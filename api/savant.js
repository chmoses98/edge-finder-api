export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { playerIds, year = '2026', splits = 'false' } = req.query;

  function parseCSV(text) {
    const lines = text.trim().split('\n');
    if (lines.length < 2) return [];
    function splitCSVLine(line) {
      const result = []; let current = ''; let inQuotes = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"') { inQuotes = !inQuotes; }
        else if (ch === ',' && !inQuotes) { result.push(current.trim()); current = ''; }
        else { current += ch; }
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

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  try {
    const pitcherUrl = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=pitcher&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,hard_hit_percent,xera,exit_velocity_avg,barrel_batted_rate&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`;
    const batterUrl  = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=batter&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,xwoba,hard_hit_percent,barrel_batted_rate,exit_velocity_avg&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`;

    const [pitcherRes, batterRes] = await Promise.all([
      fetch(pitcherUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(batterUrl,  { headers: { 'User-Agent': 'Mozilla/5.0' } })
    ]);

    if (!pitcherRes.ok) throw new Error(`Savant pitcher fetch failed: ${pitcherRes.status}`);
    if (!batterRes.ok)  throw new Error(`Savant batter fetch failed: ${batterRes.status}`);

    const rawPitchers = parseCSV(await pitcherRes.text());
    const rawBatters  = parseCSV(await batterRes.text());

    const pitchers = {};
    for (const p of rawPitchers) {
      const id = p['player_id'];
      if (!id) continue;
      const bbPct = pf(p['bb_percent']);
      const xERA  = pf(p['xera']);
      pitchers[id] = {
        playerId: id, name: p['last_name, first_name'] || '', year: p['year'],
        kPct: pf(p['k_percent']), bbPct, whiffPct: pf(p['whiff_percent']),
        xERA, hardHitPct: pf(p['hard_hit_percent']),
        exitVeloAvg: pf(p['exit_velocity_avg']), barrelPct: pf(p['barrel_batted_rate']),
        highWalkRisk: bbPct !== null && bbPct > 9.2,
        eliteStarter: xERA !== null && xERA < 2.50,
      };
    }

    const batters = {};
    for (const b of rawBatters) {
      const id = b['player_id'];
      if (!id) continue;
      batters[id] = {
        playerId: id, name: b['last_name, first_name'] || '', year: b['year'],
        kPct: pf(b['k_percent']), bbPct: pf(b['bb_percent']),
        whiffPct: pf(b['whiff_percent']), xwOBA: pf(b['xwoba']),
        hardHitPct: pf(b['hard_hit_percent']), barrelPct: pf(b['barrel_batted_rate']),
        exitVeloAvg: pf(b['exit_velocity_avg']),
      };
    }

    // ── First-inning splits for opener-flagged pitchers ──────────────────────
    // When splits=true AND playerIds provided, fetch inning-1 ERA from Savant
    // game-log splits. Returns firstInningERA, firstInningApps, openerQualified.
    let firstInningSplits = {};
    if (splits === 'true' && playerIds) {
      const ids = playerIds.split(',').map(id => id.trim()).filter(Boolean);
      await Promise.all(ids.map(async (id) => {
        try {
          // Savant pitching split by inning: inning_number=1
          const splitUrl = `https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfPT=&hfAB=&hfGT=R%7C&hfPR=&hfZ=&hfStadium=&hfBBL=&hfNewZones=&hfPull=&hfC=&hfSea=${year}%7C&hfSit=&player_type=pitcher&hfOuts=&hfOpponent=&pitcher_throws=&batter_stands=&hfSA=&game_date_gt=&game_date_lt=&hfMo=&hfTeam=&home_road=&hfRO=&position=&hfInfield=&hfOutfield=&hfInn=1%7C&hfBBT=&hfFlag=&metric_1=&group_by=name&min_pitches=0&min_results=0&min_pas=0&sort_col=pitches&player_event_sort=api_p_release_speed&sort_order=desc&chk_stats_pa=on&chk_stats_abs=on&chk_stats_bip=on&chk_stats_hits=on&chk_stats_singles=on&chk_stats_dbls=on&chk_stats_trpls=on&chk_stats_hrs=on&chk_stats_so=on&chk_stats_bb=on&chk_stats_era=on&chk_stats_xera=on&pitchers_lookup%5B%5D=${id}&type=details&player_lookup%5B%5D=${id}`;
          const r = await fetch(splitUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
          if (!r.ok) { firstInningSplits[id] = { available: false, reason: `HTTP ${r.status}` }; return; }
          const rows = parseCSV(await r.text());
          if (!rows.length) { firstInningSplits[id] = { available: false, reason: 'no_data' }; return; }
          // Aggregate across rows: count appearances, sum ER and IP
          let totalER = 0, totalOuts = 0, appearances = rows.length;
          let xERAsum = 0, xERAcount = 0;
          for (const row of rows) {
            const er = pf(row['earned_runs'] ?? row['er'] ?? row['runs']);
            const outs = pf(row['outs_recorded'] ?? row['outs']);
            const xe = pf(row['estimated_era_using_speedangle'] ?? row['xera']);
            if (er !== null)  totalER   += er;
            if (outs !== null) totalOuts += outs;
            if (xe !== null) { xERAsum += xe; xERAcount++; }
          }
          const ipFull = Math.floor(totalOuts / 3) + (totalOuts % 3) / 10;
          const era    = totalOuts > 0 ? Math.round((totalER / (totalOuts / 3)) * 9 * 100) / 100 : null;
          const xERAavg = xERAcount > 0 ? Math.round(xERAsum / xERAcount * 100) / 100 : null;
          firstInningSplits[id] = {
            available:       appearances >= 5,
            appearances,
            firstInningERA:  era,
            firstInningXERA: xERAavg,
            ip:              Math.round(ipFull * 10) / 10,
            // Qualified = 5+ appearances with data
            openerQualified: appearances >= 5 && (era !== null || xERAavg !== null),
            strongOpener:    xERAavg !== null && xERAavg < 3.00,
          };
        } catch(e) {
          firstInningSplits[id] = { available: false, reason: e.message };
        }
      }));
    }

    let filteredPitchers = pitchers;
    let filteredBatters  = batters;
    if (playerIds) {
      const ids = playerIds.split(',').map(id => id.trim());
      filteredPitchers = {}; filteredBatters = {};
      for (const id of ids) {
        if (pitchers[id]) filteredPitchers[id] = pitchers[id];
        if (batters[id])  filteredBatters[id]  = batters[id];
      }
    }

    return res.status(200).json({
      ok: true, year, fetchedAt: new Date().toISOString(),
      pitcherCount: Object.keys(filteredPitchers).length,
      batterCount:  Object.keys(filteredBatters).length,
      pitchers: filteredPitchers,
      batters:  filteredBatters,
      firstInningSplits: Object.keys(firstInningSplits).length ? firstInningSplits : undefined,
    });

  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message,
      fallback: 'Baseball Savant may be blocking automated requests.' });
  }
}
