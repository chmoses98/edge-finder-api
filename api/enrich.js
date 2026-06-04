/**
 * api/enrich.js
 *
 * Consolidated enrichment endpoint. Replaces savant_batting, savant_tto, savant_bullpen_hl.
 * Route via ?type= parameter:
 *   ?type=batting          → team wOBA/FB% + individual batter wOBA
 *   ?type=tto&playerIds=.. → TTO splits for given pitcher IDs
 *   ?type=bullpen          → HL bullpen xFIP (saves+holds weighted)
 */
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { type, playerIds, year = '2026', season = '2026' } = req.query;

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }
  const FIP_CONST = 3.10;

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

  const SAVANT_TO_ABBR = {
    'ARI':'ARI','ATL':'ATL','BAL':'BAL','BOS':'BOS','CHC':'CHC','CWS':'CWS',
    'CIN':'CIN','CLE':'CLE','COL':'COL','DET':'DET','HOU':'HOU','KC':'KC',
    'LAA':'LAA','LAD':'LAD','MIA':'MIA','MIL':'MIL','MIN':'MIN','NYM':'NYM',
    'NYY':'NYY','OAK':'ATH','ATH':'ATH','PHI':'PHI','PIT':'PIT','STL':'STL',
    'SD':'SD','SF':'SF','SEA':'SEA','TB':'TB','TEX':'TEX','TOR':'TOR',
    'WSH':'WSH','AZ':'ARI',
  };

  function normTeam(raw) {
    const up = (raw||'').trim().toUpperCase();
    return SAVANT_TO_ABBR[up] || up || null;
  }

  function getTeam(row) {
    for (const col of ['team_name','player_team','team','team_abbrev','Team']) {
      const v = normTeam(row[col]);
      if (v) return v;
    }
    return null;
  }

  function bullpenGrade(x) {
    if (x === null) return null;
    if (x < 3.50) return 'ELITE';
    if (x < 4.00) return 'ABOVE_AVERAGE';
    if (x < 4.50) return 'AVERAGE';
    if (x < 5.00) return 'BELOW_AVERAGE';
    return 'VULNERABLE';
  }

  // ── BATTING ──────────────────────────────────────────────────────────────
  if (type === 'batting') {
    try {
      // Primary: expected_statistics (has team column + est_woba)
      const xUrl = `https://baseballsavant.mlb.com/expected_statistics?type=batter` +
        `&year=${year}&position=&team=&filterType=batter&min=10&csv=true`;
      // Secondary: custom leaderboard (has fb_percent)
      const fbUrl = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=batter` +
        `&filter=&min=10&selections=xwoba,fb_percent,bb_percent,k_percent,hard_hit_percent,barrel_batted_rate` +
        `&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm&csv=true`;

      const [xRes, fbRes] = await Promise.all([
        fetch(xUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
        fetch(fbUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
      ]);

      const { headers: xh, rows: xrows } = parseCSV(xRes.ok ? await xRes.text() : '');
      const { headers: fh, rows: frows } = parseCSV(fbRes.ok ? await fbRes.text() : '');

      // player_id -> fbPct from leaderboard
      const playerFB = {};
      const batters  = {};
      for (const row of frows) {
        const pid = row['player_id']?.trim();
        const fb  = pf(row['fb_percent']);
        const xw  = pf(row['xwoba']);
        if (pid && fb  !== null) playerFB[pid] = fb;
        if (pid && xw  !== null) batters[pid]  = xw;
      }

      // Aggregate by team
      const buckets = {};
      const processRow = (row, xwoba, team) => {
        if (!team) return;
        if (!buckets[team]) buckets[team] = { xwoba:[], fbPct:[], bbPct:[], kPct:[], hh:[], brl:[] };
        const b = buckets[team];
        if (xwoba !== null) b.xwoba.push(xwoba);
        const fb = playerFB[row['player_id']?.trim()];
        if (fb != null) b.fbPct.push(fb);
        const bb = pf(row['bb_percent']); if (bb !== null) b.bbPct.push(bb);
        const k  = pf(row['k_percent']);  if (k  !== null) b.kPct.push(k);
        const hh = pf(row['hard_hit_percent']); if (hh !== null) b.hh.push(hh);
        const brl= pf(row['barrel_batted_rate']); if (brl !== null) b.brl.push(brl);
      };

      // Use expected_statistics rows (primary — has team + est_woba)
      for (const row of xrows) {
        const pid   = row['player_id']?.trim();
        const xwoba = pf(row['est_woba'] ?? row['xwoba']);
        const team  = getTeam(row);
        if (pid && xwoba !== null) batters[pid] = xwoba;
        processRow(row, xwoba, team);
      }

      // If no teams from xstats, fall back to leaderboard rows
      if (Object.keys(buckets).length === 0) {
        for (const row of frows) {
          const pid   = row['player_id']?.trim();
          const xwoba = pf(row['xwoba']);
          const team  = getTeam(row);
          if (pid && xwoba !== null) batters[pid] = xwoba;
          processRow(row, xwoba, team);
        }
      }

      const avg = arr => arr.length ? Math.round(arr.reduce((a,b)=>a+b,0)/arr.length*1000)/1000 : null;
      const teams = {};
      for (const [abbr, b] of Object.entries(buckets)) {
        teams[abbr] = {
          xwoba: avg(b.xwoba), fbPct: avg(b.fbPct), bbPct: avg(b.bbPct),
          kPct: avg(b.kPct), hardHit: avg(b.hh), barrel: avg(b.brl),
        };
      }

      return res.status(200).json({
        ok: true, year, type: 'batting',
        fetchedAt: new Date().toISOString(),
        teamCount: Object.keys(teams).length,
        batterCount: Object.keys(batters).length,
        xstatsRows: xrows.length, fbRows: frows.length,
        xstatsHeaders: xh.slice(0,12), fbHeaders: fh.slice(0,12),
        teams, batters,
      });
    } catch(e) {
      return res.status(500).json({ ok: false, type: 'batting', error: e.message });
    }
  }

  // ── TTO ──────────────────────────────────────────────────────────────────
  if (type === 'tto') {
    if (!playerIds) return res.status(400).json({ ok: false, error: 'playerIds required for type=tto' });

    async function fetchGameLogs(pitcherId) {
      try {
        const r = await fetch(
          `https://statsapi.mlb.com/api/v1/people/${pitcherId}/stats` +
          `?stats=gameLog&group=pitching&season=${year}&gameType=R&limit=15`
        );
        if (!r.ok) return [];
        const d = await r.json();
        return (d?.stats?.[0]?.splits || []).filter(s => (s.stat?.gamesStarted || 0) > 0);
      } catch(e) { return []; }
    }

    function computeFIP(games) {
      let ip=0, hr=0, bb=0, k=0;
      for (const l of games) {
        const s = l.stat || {};
        const raw = parseFloat(s.inningsPitched || '0');
        ip += Math.floor(raw) + (raw % 1) / 0.3 * 0.333;
        hr += parseInt(s.homeRuns    || 0);
        bb += parseInt(s.baseOnBalls || 0);
        k  += parseInt(s.strikeOuts  || 0);
      }
      return ip >= 5 ? Math.round(((13*hr+3*bb-2*k)/ip+FIP_CONST)*100)/100 : null;
    }

    async function computeTTO(pitcherId) {
      const logs = await fetchGameLogs(pitcherId);
      if (!logs.length) return { available: false, reason: 'no_game_logs' };
      const getIP = l => { const r=parseFloat(l.stat?.inningsPitched||'0'); return Math.floor(r)+(r%1)/0.3*0.333; };
      const deepStarts  = logs.filter(l => getIP(l) >= 6);
      const earlyStarts = logs.filter(l => getIP(l) <= 4);
      if (deepStarts.length < 3) return { available: false, reason: 'insufficient_deep_starts' };
      const tto1FIP = computeFIP(earlyStarts.length >= 3 ? earlyStarts : logs);
      const tto3FIP = computeFIP(deepStarts);
      if (!tto1FIP || !tto3FIP) return { available: false, reason: 'insufficient_ip' };
      const split = Math.round((tto3FIP - tto1FIP)*100)/100;
      return {
        available: true, ttoSplit: split, ttoRisk: split > 0.50,
        tto1: { fip: tto1FIP, gamesUsed: earlyStarts.length||logs.length },
        tto3: { fip: tto3FIP, gamesUsed: deepStarts.length },
        note: 'game_log_proxy',
      };
    }

    try {
      const ids = playerIds.split(',').map(s=>s.trim()).filter(Boolean);
      const results = {};
      await Promise.all(ids.map(async id => { results[id] = await computeTTO(id); }));
      return res.status(200).json({ ok: true, year, type: 'tto', fetchedAt: new Date().toISOString(), pitchers: results });
    } catch(e) {
      return res.status(500).json({ ok: false, type: 'tto', error: e.message });
    }
  }

  // ── BULLPEN HL ────────────────────────────────────────────────────────────
  if (type === 'bullpen') {
    try {
      const [reliefRes, teamRes] = await Promise.all([
        fetch(`https://statsapi.mlb.com/api/v1/stats?stats=season&group=pitching&gameType=R` +
              `&season=${season}&playerPool=relief&sportId=1&limit=500` +
              `&fields=stats,splits,stat,saves,holds,inningsPitched,homeRuns,baseOnBalls,strikeOuts,era,player,team`),
        fetch(`https://statsapi.mlb.com/api/v1/teams?sportId=1&season=${season}`)
      ]);
      if (!reliefRes.ok) throw new Error(`Relief: ${reliefRes.status}`);
      const [reliefData, teamData] = await Promise.all([reliefRes.json(), teamRes.json()]);

      const teamMap = {};
      for (const t of (teamData.teams||[])) teamMap[t.id] = t.abbreviation;

      const teamRelievers = {};
      for (const split of (reliefData?.stats?.[0]?.splits||[])) {
        const abbr = split.team?.abbreviation || teamMap[split.team?.id];
        if (!abbr) continue;
        const s = split.stat || {};
        const saves = parseInt(s.saves||0), holds = parseInt(s.holds||0);
        const ipRaw = parseFloat(s.inningsPitched||'0');
        const ip = Math.floor(ipRaw) + (ipRaw%1)/0.3*0.333;
        const hr=parseInt(s.homeRuns||0), bb=parseInt(s.baseOnBalls||0), k=parseInt(s.strikeOuts||0);
        if (ip < 2) continue;
        const fip = ip > 0 ? Math.round(((13*hr+3*bb-2*k)/ip+FIP_CONST)*100)/100 : null;
        if (!teamRelievers[abbr]) teamRelievers[abbr] = [];
        teamRelievers[abbr].push({ saves, holds, ip, fip, leverageScore: saves+holds });
      }

      const hlResults = {};
      for (const [abbr, relievers] of Object.entries(teamRelievers)) {
        const sorted  = relievers.sort((a,b)=>b.leverageScore-a.leverageScore).slice(0,5).filter(r=>r.fip!==null);
        if (!sorted.length) continue;
        const totalIP = sorted.reduce((s,r)=>s+r.ip,0);
        const wtdFIP  = totalIP > 0 ? Math.round(sorted.reduce((s,r)=>s+r.fip*r.ip,0)/totalIP*100)/100 : null;
        hlResults[abbr] = {
          hlXFIP: wtdFIP, hlGrade: bullpenGrade(wtdFIP),
          hlAvailable: wtdFIP!==null, hlSamplePA: Math.round(totalIP*4.3),
          hlMethod: 'top_saves_holds_weighted', hlArmsUsed: sorted.length,
        };
      }

      return res.status(200).json({
        ok: true, season, type: 'bullpen',
        fetchedAt: new Date().toISOString(),
        teamCount: Object.keys(hlResults).length,
        teams: hlResults,
      });
    } catch(e) {
      return res.status(500).json({ ok: false, type: 'bullpen', error: e.message });
    }
  }

  return res.status(400).json({ ok: false, error: 'type must be batting, tto, or bullpen' });
}
