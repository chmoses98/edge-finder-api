/**
 * api/savant_batting.js — v2
 *
 * Fetches team wOBA and FB% by aggregating Savant expected_statistics CSV.
 * This endpoint includes team abbreviation per player, enabling proper team aggregation.
 *
 * Primary: Savant expected_statistics CSV (has est_woba, team_name columns)
 * Fallback for FB%: Savant custom leaderboard (fb_percent column)
 */
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { year = '2026' } = req.query;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  function parseCSV(text) {
    if (!text || !text.trim()) return { headers: [], rows: [] };
    const lines = text.trim().split('\n');
    if (lines.length < 2) return { headers: [], rows: [] };
    const splitLine = (line) => {
      const result = []; let current = ''; let inQuotes = false;
      for (const ch of line) {
        if (ch === '"') { inQuotes = !inQuotes; }
        else if (ch === ',' && !inQuotes) { result.push(current.trim()); current = ''; }
        else { current += ch; }
      }
      result.push(current.trim());
      return result;
    };
    const headers = splitLine(lines[0]);
    const rows = lines.slice(1).filter(l => l.trim()).map(line => {
      const vals = splitLine(line);
      const obj = {};
      headers.forEach((h, i) => { obj[h] = vals[i] || ''; });
      return obj;
    });
    return { headers, rows };
  }

  // Savant team abbreviation normalization
  const SAVANT_TO_ABBR = {
    'ARI':'ARI','ATL':'ATL','BAL':'BAL','BOS':'BOS','CHC':'CHC',
    'CWS':'CWS','CIN':'CIN','CLE':'CLE','COL':'COL','DET':'DET',
    'HOU':'HOU','KC':'KC','LAA':'LAA','LAD':'LAD','MIA':'MIA',
    'MIL':'MIL','MIN':'MIN','NYM':'NYM','NYY':'NYY','OAK':'ATH',
    'ATH':'ATH','PHI':'PHI','PIT':'PIT','STL':'STL','SD':'SD',
    'SF':'SF','SEA':'SEA','TB':'TB','TEX':'TEX','TOR':'TOR',
    'WSH':'WSH','AZ':'ARI',
  };

  function normalizeTeam(raw) {
    const up = (raw || '').trim().toUpperCase();
    return SAVANT_TO_ABBR[up] || up || null;
  }

  function getTeamFromRow(row, headers) {
    // Try multiple possible team column names
    for (const col of ['team_name', 'player_team', 'team', 'team_abbrev', 'Team']) {
      const val = normalizeTeam(row[col]);
      if (val) return val;
    }
    return null;
  }

  try {
    // Primary: Savant expected_statistics CSV — has est_woba and team column
    const xstatsUrl = `https://baseballsavant.mlb.com/expected_statistics?type=batter` +
      `&year=${year}&position=&team=&filterType=batter&min=10&csv=true`;

    // Secondary: Savant custom leaderboard — has fb_percent but may lack team column
    const fbUrl = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=batter` +
      `&filter=&min=10&selections=xwoba,fb_percent,bb_percent,k_percent,hard_hit_percent,barrel_batted_rate` +
      `&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm&csv=true`;

    const [xstatsRes, fbRes] = await Promise.all([
      fetch(xstatsUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      fetch(fbUrl,     { headers: { 'User-Agent': 'Mozilla/5.0' } }),
    ]);

    const xstatsText = xstatsRes.ok ? await xstatsRes.text() : '';
    const fbText     = fbRes.ok     ? await fbRes.text()     : '';

    const { headers: xh, rows: xrows } = parseCSV(xstatsText);
    const { headers: fh, rows: frows } = parseCSV(fbText);

    // Build player_id -> fbPct map from leaderboard
    const playerFB = {};
    const playerFBLeaderboard = {};  // also capture wOBA from leaderboard as fallback
    for (const row of frows) {
      const pid = row['player_id']?.trim();
      if (!pid) continue;
      const fb  = pf(row['fb_percent']);
      const xw  = pf(row['xwoba']);
      if (fb  !== null) playerFB[pid]             = fb;
      if (xw  !== null) playerFBLeaderboard[pid]  = xw;
    }

    // Aggregate by team from expected_statistics rows (primary source for xwOBA + team)
    const buckets  = {};  // abbr -> { xwoba:[], fbPct:[], bbPct:[], kPct:[], hh:[], brl:[] }
    const batters  = {};  // player_id -> xwoba

    for (const row of xrows) {
      const pid   = row['player_id']?.trim();
      const team  = getTeamFromRow(row, xh);
      const xwoba = pf(row['est_woba'] ?? row['xwoba'] ?? row['xwOBA']);
      if (pid && xwoba !== null) batters[pid] = xwoba;
      if (!team) continue;
      if (!buckets[team]) buckets[team] = { xwoba:[], fbPct:[], bbPct:[], kPct:[], hh:[], brl:[] };
      const b = buckets[team];
      if (xwoba !== null) b.xwoba.push(xwoba);
      // Join fbPct from leaderboard by player_id
      const fb = pid ? playerFB[pid] : null;
      if (fb !== null && fb !== undefined) b.fbPct.push(fb);
    }

    // If xstats had no team column, fall back to leaderboard for both wOBA and team
    if (Object.keys(buckets).length === 0) {
      for (const row of frows) {
        const pid   = row['player_id']?.trim();
        const team  = getTeamFromRow(row, fh);
        const xwoba = pf(row['xwoba']);
        const fb    = pf(row['fb_percent']);
        const bbPct = pf(row['bb_percent']);
        const kPct  = pf(row['k_percent']);
        const hh    = pf(row['hard_hit_percent']);
        const brl   = pf(row['barrel_batted_rate']);
        if (pid && xwoba !== null) batters[pid] = xwoba;
        if (!team) continue;
        if (!buckets[team]) buckets[team] = { xwoba:[], fbPct:[], bbPct:[], kPct:[], hh:[], brl:[] };
        const b = buckets[team];
        if (xwoba !== null) b.xwoba.push(xwoba);
        if (fb    !== null) b.fbPct.push(fb);
        if (bbPct !== null) b.bbPct.push(bbPct);
        if (kPct  !== null) b.kPct.push(kPct);
        if (hh    !== null) b.hh.push(hh);
        if (brl   !== null) b.brl.push(brl);
      }
    }

    const avg = arr => arr.length ? Math.round(arr.reduce((a,b)=>a+b,0)/arr.length*1000)/1000 : null;
    const teams = {};
    for (const [abbr, b] of Object.entries(buckets)) {
      teams[abbr] = {
        xwoba:   avg(b.xwoba),
        fbPct:   avg(b.fbPct),
        bbPct:   avg(b.bbPct),
        kPct:    avg(b.kPct),
        hardHit: avg(b.hh),
        barrel:  avg(b.brl),
      };
    }

    return res.status(200).json({
      ok: true, year,
      fetchedAt:     new Date().toISOString(),
      teamCount:     Object.keys(teams).length,
      batterCount:   Object.keys(batters).length,
      xstatsHeaders: xh.slice(0, 12),
      fbHeaders:     fh.slice(0, 12),
      xstatsRows:    xrows.length,
      fbRows:        frows.length,
      teams,
      batters,
    });

  } catch(err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
