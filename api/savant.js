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

  // Fetch last N starts for a pitcher from MLB Stats API — returns avg IP and recent xFIP proxy
  async function fetchRecentStarts(pitcherId, numStarts = 5) {
    try {
      const r = await fetch(
        `https://statsapi.mlb.com/api/v1/people/${pitcherId}/stats?stats=gameLog&group=pitching&season=${year}&gameType=R&limit=10`
      );
      if (!r.ok) return null;
      const d = await r.json();
      const logs = (d?.stats?.[0]?.splits || []).filter(l => l.stat?.gamesStarted > 0).slice(0, numStarts);
      if (!logs.length) return null;

      let totalIP = 0, totalER = 0, totalBB = 0, totalK = 0, totalHR = 0, count = 0;
      for (const l of logs) {
        const s = l.stat || {};
        const ipRaw = parseFloat(s.inningsPitched || '0');
        const ipFull = Math.floor(ipRaw) + (ipRaw % 1) / 0.3 * 0.333;
        totalIP += ipFull;
        totalER += parseInt(s.earnedRuns || 0);
        totalBB += parseInt(s.baseOnBalls || 0);
        totalK  += parseInt(s.strikeOuts || 0);
        totalHR += parseInt(s.homeRuns || 0);
        count++;
      }
      if (count === 0 || totalIP === 0) return null;

      const avgIP = Math.round((totalIP / count) * 100) / 100;
      // Simplified xFIP proxy from game log: ((13*HR) + (3*BB) - (2*K)) / IP + 3.10 (FIP constant ~2026)
      const FIP_CONST = 3.10;
      const recentFIP = Math.round(((13 * totalHR + 3 * totalBB - 2 * totalK) / totalIP + FIP_CONST) * 100) / 100;
      // xFIP ≈ FIP with HR normalized — we use FIP as proxy since we lack FB% for xFIP
      // This is directionally correct and better than season xERA for recency
      return { avgIP, recentFIP, startsSampled: count };
    } catch(e) { return null; }
  }

  // Fetch platoon splits (vs LHH and RHH) for a pitcher from Savant
  async function fetchPlatoonSplits(pitcherId) {
    try {
      // Savant spray chart endpoint supports L/R batter filter
      const [vsL, vsR] = await Promise.all([
        fetch(`https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfSea=${year}%7C&player_type=pitcher&pitchers_lookup%5B%5D=${pitcherId}&batter_stands=L&hfGT=R%7C&min_pitches=0&min_results=0&min_pas=20&group_by=name&sort_col=pitches&sort_order=desc&chk_stats_pa=on&chk_stats_k_percent=on&chk_stats_bb_percent=on&chk_stats_xera=on&type=details`, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
        fetch(`https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfSea=${year}%7C&player_type=pitcher&pitchers_lookup%5B%5D=${pitcherId}&batter_stands=R&hfGT=R%7C&min_pitches=0&min_results=0&min_pas=20&group_by=name&sort_col=pitches&sort_order=desc&chk_stats_pa=on&chk_stats_k_percent=on&chk_stats_bb_percent=on&chk_stats_xera=on&type=details`, { headers: { 'User-Agent': 'Mozilla/5.0' } })
      ]);

      const parseAggregate = async (r) => {
        if (!r.ok) return null;
        const text = await r.text();
        const rows = parseCSV(text);
        if (!rows.length) return null;
        // group_by=name returns one aggregated row per pitcher
        // Column names in this endpoint: 'pa', 'k_percent', 'bb_percent', 'xera' (or similar)
        // Try multiple possible column name variants Savant uses
        const row = rows[0];
        const allKeys = Object.keys(row);

        const findCol = (...candidates) => {
          for (const c of candidates) {
            if (row[c] !== undefined && row[c] !== '') return row[c];
          }
          return null;
        };

        const pa   = pf(findCol('pa','total_pas','plate_appearances')) ?? 0;
        // K% in statcast_search grouped output: 'k_percent', 'strikeout_percent', 'so'
        // If k_percent is 0 but 'so' (strikeouts) and 'pa' are present, compute it
        let kPct = pf(findCol('k_percent','strikeout_percent'));
        const so = pf(findCol('so','strikeouts','strike_outs'));
        if ((kPct === null || kPct === 0) && so !== null && pa > 0) {
          kPct = Math.round(so / pa * 1000) / 10;
        }
        let bbPct = pf(findCol('bb_percent','walk_percent'));
        const bb = pf(findCol('bb','walks','base_on_balls'));
        if ((bbPct === null || bbPct === 0) && bb !== null && pa > 0) {
          bbPct = Math.round(bb / pa * 1000) / 10;
        }
        const xERA = pf(findCol('estimated_era_using_speedangle','xera','xERA'));

        if (pa < 20) return null; // insufficient sample

        return { pa, kPct, bbPct, xERA };
      };

      const [lhh, rhh] = await Promise.all([parseAggregate(vsL), parseAggregate(vsR)]);
      // Only return if we have meaningful sample (20+ PA each side)
      return {
        vsLHH: lhh && lhh.pa >= 20 ? lhh : null,
        vsRHH: rhh && rhh.pa >= 20 ? rhh : null,
      };
    } catch(e) { return null; }
  }

  try {
    // Primary leaderboard — add xfip to selections
    const pitcherUrl = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=pitcher&filter=&min=1&selections=k_percent,bb_percent,whiff_percent,hard_hit_percent,xera,xfip,exit_velocity_avg,barrel_batted_rate&chart=false&x=k_percent&y=k_percent&r=no&chartType=beeswarm&csv=true`;
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
      const xFIP  = pf(p['xfip'] ?? p['p_xfip'] ?? p['xFIP']);
      pitchers[id] = {
        playerId: id, name: p['last_name, first_name'] || '', year: p['year'],
        kPct: pf(p['k_percent']), bbPct,
        whiffPct: pf(p['whiff_percent']),
        xERA, xFIP,
        hardHitPct:  pf(p['hard_hit_percent']),
        exitVeloAvg: pf(p['exit_velocity_avg']),
        barrelPct:   pf(p['barrel_batted_rate']),
        highWalkRisk: bbPct !== null && bbPct > 9.2,
        eliteStarter: xFIP !== null ? xFIP < 2.50 : (xERA !== null && xERA < 2.50),
        // xFIP/xERA divergence signal: positive = outperforming (fade), negative = underperforming (buy)
        xFIPvsXERA: (xFIP !== null && xERA !== null) ? Math.round((xFIP - xERA) * 100) / 100 : null,
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

    // ── Enrich specific pitchers with recent starts + platoon splits ──────────
    // When playerIds provided: fetch recent starts (avg IP, recentFIP) and platoon splits
    let enrichedPitchers = {};
    if (playerIds) {
      const ids = playerIds.split(',').map(id => id.trim()).filter(Boolean);
      await Promise.all(ids.map(async (id) => {
        const base = pitchers[id] || { playerId: id };

        const [recentData, platoonData] = await Promise.all([
          fetchRecentStarts(id),
          fetchPlatoonSplits(id),
        ]);

        enrichedPitchers[id] = {
          ...base,
          avgIPperStart: recentData?.avgIP ?? null,
          recentFIP:     recentData?.recentFIP ?? null,
          startsSampled: recentData?.startsSampled ?? null,
          // openerRole: set by caller (slate.js) based on avgIPperStart < 3.0
          vsLHH: platoonData?.vsLHH ?? null,
          vsRHH: platoonData?.vsRHH ?? null,
        };
      }));
    }

    // ── First-inning splits for opener-flagged pitchers ──────────────────────
    let firstInningSplits = {};
    if (splits === 'true' && playerIds) {
      const ids = playerIds.split(',').map(id => id.trim()).filter(Boolean);
      await Promise.all(ids.map(async (id) => {
        try {
          const splitUrl = `https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfPT=&hfAB=&hfGT=R%7C&hfPR=&hfZ=&hfStadium=&hfBBL=&hfNewZones=&hfPull=&hfC=&hfSea=${year}%7C&hfSit=&player_type=pitcher&hfOuts=&hfOpponent=&pitcher_throws=&batter_stands=&hfSA=&game_date_gt=&game_date_lt=&hfMo=&hfTeam=&home_road=&hfRO=&position=&hfInfield=&hfOutfield=&hfInn=1%7C&hfBBT=&hfFlag=&metric_1=&group_by=name&min_pitches=0&min_results=0&min_pas=0&sort_col=pitches&player_event_sort=api_p_release_speed&sort_order=desc&chk_stats_pa=on&chk_stats_abs=on&chk_stats_bip=on&chk_stats_hits=on&chk_stats_singles=on&chk_stats_dbls=on&chk_stats_trpls=on&chk_stats_hrs=on&chk_stats_so=on&chk_stats_bb=on&chk_stats_era=on&chk_stats_xera=on&pitchers_lookup%5B%5D=${id}&type=details&player_lookup%5B%5D=${id}`;
          const r = await fetch(splitUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } });
          if (!r.ok) { firstInningSplits[id] = { available: false, reason: `HTTP ${r.status}` }; return; }
          const rows = parseCSV(await r.text());
          if (!rows.length) { firstInningSplits[id] = { available: false, reason: 'no_data' }; return; }
          let totalER = 0, totalOuts = 0, appearances = rows.length;
          let xERAsum = 0, xERAcount = 0;
          for (const row of rows) {
            const er   = pf(row['earned_runs'] ?? row['er'] ?? row['runs']);
            const outs = pf(row['outs_recorded'] ?? row['outs']);
            const xe   = pf(row['estimated_era_using_speedangle'] ?? row['xera']);
            if (er !== null)   totalER   += er;
            if (outs !== null) totalOuts += outs;
            if (xe !== null) { xERAsum += xe; xERAcount++; }
          }
          const ipFull = Math.floor(totalOuts / 3) + (totalOuts % 3) / 10;
          const era    = totalOuts > 0 ? Math.round((totalER / (totalOuts / 3)) * 9 * 100) / 100 : null;
          const xERAavg = xERAcount > 0 ? Math.round(xERAsum / xERAcount * 100) / 100 : null;
          firstInningSplits[id] = {
            available:        appearances >= 5,
            appearances,
            firstInningERA:   era,
            firstInningXERA:  xERAavg,
            ip:               Math.round(ipFull * 10) / 10,
            openerQualified:  appearances >= 5 && (era !== null || xERAavg !== null),
            strongOpener:     xERAavg !== null && xERAavg < 3.00,
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
      filteredPitchers = {};
      filteredBatters  = {};
      for (const id of ids) {
        // Use enriched version if available, else base
        if (enrichedPitchers[id]) filteredPitchers[id] = enrichedPitchers[id];
        else if (pitchers[id]) filteredPitchers[id] = pitchers[id];
        if (batters[id]) filteredBatters[id] = batters[id];
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
