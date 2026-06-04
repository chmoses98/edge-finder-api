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
      // Savant leaderboard for individual batter xwOBA and fbPct
      // (Savant CSV has no team column — individual player data only)
      const fbUrl = `https://baseballsavant.mlb.com/leaderboard/custom?year=${year}&type=batter` +
        `&filter=&min=10&selections=xwoba,fb_percent,bb_percent,k_percent,hard_hit_percent,barrel_batted_rate` +
        `&chart=false&x=xwoba&y=xwoba&r=no&chartType=beeswarm&csv=true`;

      // MLB Stats API for team-level batting stats (reliable, has team abbr)
      const mlbUrl = `https://statsapi.mlb.com/api/v1/teams/stats?season=${year}&sportId=1` +
        `&group=hitting&gameType=R&stats=season&order=asc`;

      const [fbRes, mlbRes] = await Promise.all([
        fetch(fbUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } }),
        fetch(mlbUrl),
      ]);

      const { rows: frows } = parseCSV(fbRes.ok ? await fbRes.text() : '');

      // Build individual batter xwOBA and fbPct maps
      const batters  = {};  // player_id -> xwoba
      let   totalFB  = 0, fbCount = 0;
      for (const row of frows) {
        const pid = row['player_id']?.trim();
        const xw  = pf(row['xwoba']);
        const fb  = pf(row['fb_percent']);
        if (pid && xw !== null) batters[pid] = xw;
        if (fb !== null) { totalFB += fb; fbCount++; }
      }
      const lgFBPct = fbCount > 0 ? totalFB / fbCount : 0.355; // league avg fb%

      // Compute team wOBA from MLB Stats API using FIP-adjacent formula:
      // wOBA ≈ (0.69*BB + 0.89*1B + 1.27*2B + 1.62*3B + 2.10*HR) / (AB + BB + SF)
      const mlbData = mlbRes.ok ? await mlbRes.json() : null;
      const teams = {};
      if (mlbData) {
        for (const rec of (mlbData?.stats?.[0]?.splits || [])) {
          const abbr = rec.team?.abbreviation;
          if (!abbr) continue;
          const s  = rec.stat || {};
          const bb = parseInt(s.baseOnBalls || 0);
          const h  = parseInt(s.hits || 0);
          const d  = parseInt(s.doubles || 0);
          const t  = parseInt(s.triples || 0);
          const hr = parseInt(s.homeRuns || 0);
          const ab = parseInt(s.atBats || 0);
          const sf = parseInt(s.sacFlies || 0);
          const singles = Math.max(0, h - d - t - hr);
          const denom   = ab + bb + sf;
          const woba    = denom > 0
            ? Math.round((0.69*bb + 0.89*singles + 1.27*d + 1.62*t + 2.10*hr) / denom * 1000) / 1000
            : null;
          // fbPct: not in MLB Stats API — use league average as placeholder
          // (individual pitch-level data requires Savant which lacks team column)
          teams[abbr] = { xwoba: woba, fbPct: Math.round(lgFBPct*1000)/1000, bbPct: null, kPct: null, hardHit: null, barrel: null };
        }
      }

      // Debug: capture MLB Stats structure
      let mlbDebug = {};
      if (mlbData) {
        const statsArr = mlbData?.stats || [];
        const firstSplit = statsArr[0]?.splits?.[0];
        mlbDebug = {
          statsCount: statsArr.length,
          firstStatType: statsArr[0]?.type?.displayName,
          splitsCount: statsArr[0]?.splits?.length || 0,
          firstSplitKeys: firstSplit ? Object.keys(firstSplit) : [],
          teamKeys: firstSplit?.team ? Object.keys(firstSplit.team) : [],
          teamData: firstSplit?.team || null,
          statKeys: firstSplit?.stat ? Object.keys(firstSplit.stat).slice(0,8) : [],
        };
      }

      return res.status(200).json({
        ok: true, year, type: 'batting',
        fetchedAt: new Date().toISOString(),
        teamCount: Object.keys(teams).length,
        batterCount: Object.keys(batters).length,
        fbRows: frows.length,
        wobaSource: 'mlb_stats_formula',
        fbPctNote: 'league_average_placeholder_savant_team_column_unavailable',
        mlbDebug,
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
              `&season=${season}&playerPool=relief&sportId=1&limit=500`),
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
