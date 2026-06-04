/**
 * api/savant_batting.js
 * 
 * Fetches Savant batter leaderboard and aggregates by team.
 * Returns: teamWOBA, teamFBPct per team abbreviation.
 * Lightweight — one Savant HTTP call, fast enough for 60s timeout.
 * Called by fetch-slate workflow via: fetch_endpoint savant_batting "...api/savant_batting"
 */
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { year = '2026' } = req.query;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

  function parseCSV(text) {
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
    const rows = lines.slice(1)
      .filter(l => l.trim())
      .map(line => {
        const vals = splitLine(line);
        const obj = {};
        headers.forEach((h, i) => { obj[h] = vals[i] || ''; });
        return obj;
      });
    return { headers, rows };
  }

  // Savant team_name -> standard abbr
  const SAVANT_TO_ABBR = {
    'ARI':'ARI','ATL':'ATL','BAL':'BAL','BOS':'BOS','CHC':'CHC',
    'CWS':'CWS','CIN':'CIN','CLE':'CLE','COL':'COL','DET':'DET',
    'HOU':'HOU','KC':'KC','LAA':'LAA','LAD':'LAD','MIA':'MIA',
    'MIL':'MIL','MIN':'MIN','NYM':'NYM','NYY':'NYY','OAK':'ATH',
    'ATH':'ATH','PHI':'PHI','PIT':'PIT','STL':'STL','SD':'SD',
    'SF':'SF','SEA':'SEA','TB':'TB','TEX':'TEX','TOR':'TOR',
    'WSH':'WSH','AZ':'ARI',
  };

  function getTeam(row, headers) {
    for (const col of ['player_team', 'team_name', 'team', 'team_abbrev']) {
      const val = (row[col] || '').trim().toUpperCase();
      if (val) return SAVANT_TO_ABBR[val] || val;
    }
    return null;
  }

  try {
    const url = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=batter` +
      `&filter=&min=10&selections=xwoba,bb_percent,k_percent,hard_hit_percent,barrel_batted_rate,fb_percent` +
      `&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm&csv=true`;

    const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (!r.ok) throw new Error(`Savant batter leaderboard: ${r.status}`);

    const { headers, rows } = parseCSV(await r.text());

    // Aggregate by team
    const buckets = {};
    const batters = {};  // player_id -> xwoba for lineup adj

    for (const row of rows) {
      const pid   = row['player_id']?.trim();
      const team  = getTeam(row, headers);
      const xwoba = pf(row['xwoba']);
      const fbPct = pf(row['fb_percent']);
      const bbPct = pf(row['bb_percent']);
      const kPct  = pf(row['k_percent']);
      const hh    = pf(row['hard_hit_percent']);
      const brl   = pf(row['barrel_batted_rate']);

      if (pid && xwoba !== null) batters[pid] = xwoba;

      if (!team) continue;
      if (!buckets[team]) buckets[team] = { xwoba:[], fbPct:[], bbPct:[], kPct:[], hh:[], brl:[] };
      const b = buckets[team];
      if (xwoba !== null) b.xwoba.push(xwoba);
      if (fbPct !== null) b.fbPct.push(fbPct);
      if (bbPct !== null) b.bbPct.push(bbPct);
      if (kPct  !== null) b.kPct.push(kPct);
      if (hh    !== null) b.hh.push(hh);
      if (brl   !== null) b.brl.push(brl);
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
      fetchedAt:   new Date().toISOString(),
      teamCount:   Object.keys(teams).length,
      batterCount: Object.keys(batters).length,
      csvHeaders:  headers.slice(0, 10),  // debug: show first 10 headers
      teams,
      batters,
    });

  } catch(err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
