/**
 * api/enrich.js
 *
 * Consolidated enrichment endpoint. Replaces savant_batting, savant_tto, savant_bullpen_hl.
 * Route via ?type= parameter:
 *   ?type=batting               → team wOBA/FB% + individual batter wOBA
 *   ?type=tto&playerIds=..      → TTO splits for given pitcher IDs
 *   ?type=bullpen               → HL bullpen xFIP (saves+holds weighted)
 *   ?type=batterplatoon&playerIds=.. → per-batter wOBA/K%/BB%/ISO vs LHP and
 *     vs RHP (MLB Stats API sitCodes=vl/vr hitting splits -- same source/
 *     wOBA formula as ?type=batting, split by opposing pitcher hand instead
 *     of aggregated). Consumed by scripts/fetch_batter_platoon_splits.py to
 *     populate each confirmed lineup batter's platoonSplits field for
 *     lib.research.platoon_context. No new vendor -- statsapi.mlb.com is
 *     already used throughout this file.
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

  // MLB Stats API returns team.id but NOT team.abbreviation in teams/stats splits.
  // Use this map to convert team ID -> standard abbreviation.
  const MLB_ID_TO_ABBR = {
    108:'LAA',109:'ARI',110:'BAL',111:'BOS',112:'CHC',113:'CIN',114:'CLE',
    115:'COL',116:'DET',117:'HOU',118:'KC',119:'LAD',120:'WSH',121:'NYM',
    133:'ATH',134:'PIT',135:'SD',136:'SEA',137:'SF',138:'STL',139:'TB',
    140:'TEX',141:'TOR',142:'MIN',143:'PHI',144:'ATL',145:'CWS',146:'MIA',
    147:'NYY',158:'MIL',
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
          // MLB Stats teams/stats endpoint returns team.id but not team.abbreviation
          const abbr = MLB_ID_TO_ABBR[rec.team?.id] || rec.team?.abbreviation;
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
      // Use teams/stats endpoint (same as bullpen.js — reliable)
      const reliefRes = await fetch(
        `https://statsapi.mlb.com/api/v1/teams/stats?season=${season}&sportId=1` +
        `&group=pitching&gameType=R&stats=season&playerPool=relief`
      );
      if (!reliefRes.ok) throw new Error(`Relief teams/stats: ${reliefRes.status}`);
      const reliefData = await reliefRes.json();

      const teamRelievers = {};
      for (const split of (reliefData?.stats?.[0]?.splits||[])) {
        // teams/stats splits have team.abbreviation directly
        const abbr = split.team?.abbreviation || MLB_ID_TO_ABBR[split.team?.id];
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


  // ── PITCHER FB% ──────────────────────────────────────────────────────────
  // Computes pitcher fly ball % from MLB Stats API groundOuts/airOuts.
  // airOuts / (airOuts + groundOuts) ≈ FB% (includes pop-ups, correlates well).
  // Called with: ?type=pitcherfbpct&playerIds=id1,id2,...&year=2026
  if (type === 'pitcherfbpct') {
    if (!playerIds) return res.status(400).json({ ok: false, error: 'playerIds required' });

    async function fetchPitcherBattedBall(pitcherId) {
      try {
        const r = await fetch(
          `https://statsapi.mlb.com/api/v1/people/${pitcherId}/stats` +
          `?stats=season&group=pitching&season=${year}&gameType=R`
        );
        if (!r.ok) return null;
        const d = await r.json();
        const s = d?.stats?.[0]?.splits?.[0]?.stat;
        if (!s) return null;
        const groundOuts = parseInt(s.groundOuts || 0);
        const airOuts    = parseInt(s.airOuts    || 0);
        const total      = groundOuts + airOuts;
        if (total < 30) return null;  // min sample
        const fbPct = Math.round(airOuts / total * 1000) / 10;  // as percentage
        // Also grab groundOutsToAirouts ratio as a sanity check
        const goAoRatio = pf(s.groundOutsToAirouts);
        return { fbPct, groundOuts, airOuts, goAoRatio };
      } catch(e) { return null; }
    }

    try {
      const ids = playerIds.split(',').map(s => s.trim()).filter(Boolean);
      const results = {};
      await Promise.all(ids.map(async id => {
        const data = await fetchPitcherBattedBall(id);
        results[id] = data ? data.fbPct : null;
      }));
      const resolved = Object.values(results).filter(v => v !== null).length;
      return res.status(200).json({
        ok: true, year, type: 'pitcherfbpct',
        fetchedAt: new Date().toISOString(),
        pitcherCount: ids.length,
        resolved,
        pitchers: results,
      });
    } catch(e) {
      return res.status(500).json({ ok: false, type: 'pitcherfbpct', error: e.message });
    }
  }

  // ── VELOCITY TREND ───────────────────────────────────────────────────────────
  // ?type=velocity&playerIds=...: Returns avg FB velocity last 3 starts vs season.
  // Uses MLB Stats API game log with pitchMix hydration.
  if (type === 'velocity') {
    const ids = (playerIds || '').split(',').filter(Boolean);
    if (!ids.length) return res.status(400).json({ error: 'playerIds required' });

    const results = {};

    await Promise.all(ids.map(async (pid) => {
      try {
        const url = `https://statsapi.mlb.com/api/v1/people/${pid}/stats` +
          `?stats=gameLog&group=pitching&season=${year}&gameType=R&hydrate=pitchData&limit=10`;
        const r = await fetch(url);
        if (!r.ok) { results[pid] = { velocityRecent: null, velocitySeason: null }; return; }
        const data = await r.json();

        const splits = (data?.stats?.[0]?.splits || [])
          .filter(s => s?.stat?.gamesStarted > 0);

        // Extract avg fastball speed per start
        const startVelos = [];
        for (const split of splits) {
          // pitchData.avgSpeed if hydrated, else null
          const speed = split?.pitchData?.avgSpeed
            ?? split?.stat?.pitchData?.avgSpeed
            ?? null;
          if (speed !== null && speed !== undefined) {
            const v = parseFloat(speed);
            if (!isNaN(v) && v > 70) startVelos.push(v); // sanity: >70 mph
          }
        }

        if (startVelos.length < 3) {
          results[pid] = { velocityRecent: null, velocitySeason: null, startsN: startVelos.length };
          return;
        }

        const seasonAvg = startVelos.reduce((a, b) => a + b, 0) / startVelos.length;
        const recentAvg = startVelos.slice(-3).reduce((a, b) => a + b, 0) / 3;

        results[pid] = {
          velocityRecent:   Math.round(recentAvg * 10) / 10,
          velocitySeason:   Math.round(seasonAvg * 10) / 10,
          velocityStartsN:  startVelos.length,
          velocityDrop:     Math.round((seasonAvg - recentAvg) * 10) / 10,
        };
      } catch(e) {
        results[pid] = { velocityRecent: null, velocitySeason: null };
      }
    }));

    const resolved = Object.values(results).filter(r => r.velocityRecent !== null).length;
    return res.json({ ok: true, resolved, total: ids.length, pitchers: results });
  }

  // ── BATTER PLATOON SPLITS (vs LHP / vs RHP) ───────────────────────────────
  // ?type=batterplatoon&playerIds=...: per-batter wOBA/K%/BB%/ISO split by
  // opposing pitcher handedness, via MLB Stats API's own sitCodes hitting
  // split (`sitCodes=vl` = vs LHP, `sitCodes=vr` = vs RHP -- the same
  // statsapi.mlb.com host already used by every other split in this file;
  // no new vendor). Reuses the exact wOBA weights `type=batting` above
  // already uses, applied per split instead of aggregated over the full
  // season. A split with fewer plate appearances than
  // lib.research.platoon_context.MIN_PA_HITTER_SPLIT is still returned
  // (never silently dropped here) -- the PA-floor decision belongs to the
  // consumer (platoon_context.hitter_platoon_value), which shrinks to
  // seasonWOBA below that floor; this endpoint's job is only to report
  // what MLB Stats API actually has, honestly, including a thin sample.
  if (type === 'batterplatoon') {
    if (!playerIds) return res.status(400).json({ ok: false, error: 'playerIds required for type=batterplatoon' });

    async function fetchHittingSplit(playerId, sitCode) {
      try {
        const r = await fetch(
          `https://statsapi.mlb.com/api/v1/people/${playerId}/stats` +
          `?stats=season&group=hitting&season=${year}&gameType=R&sitCodes=${sitCode}`
        );
        if (!r.ok) return null;
        const d = await r.json();
        const s = d?.stats?.[0]?.splits?.[0]?.stat;
        if (!s) return null;

        const ab = parseInt(s.atBats ?? 0);
        const bb = parseInt(s.baseOnBalls ?? 0);
        const h  = parseInt(s.hits ?? 0);
        const d2 = parseInt(s.doubles ?? 0);
        const t  = parseInt(s.triples ?? 0);
        const hr = parseInt(s.homeRuns ?? 0);
        const so = parseInt(s.strikeOuts ?? 0);
        const sf = parseInt(s.sacFlies ?? 0);
        const pa = parseInt(s.plateAppearances ?? (ab + bb + sf));
        const denom = ab + bb + sf;
        if (denom < 1 || pa < 1) return null;

        const singles = Math.max(0, h - d2 - t - hr);
        // Same wOBA weights as ?type=batting above -- not re-derived here.
        const woba = Math.round((0.69*bb + 0.89*singles + 1.27*d2 + 1.62*t + 2.10*hr) / denom * 1000) / 1000;
        const slg  = ab > 0 ? Math.round(((singles + 2*d2 + 3*t + 4*hr) / ab) * 1000) / 1000 : null;
        const avg  = ab > 0 ? Math.round((h / ab) * 1000) / 1000 : null;
        const iso  = (slg !== null && avg !== null) ? Math.round((slg - avg) * 1000) / 1000 : null;

        return {
          woba, iso, slg, pa,
          kPct: Math.round((so / pa) * 1000) / 10,
          bbPct: Math.round((bb / pa) * 1000) / 10,
        };
      } catch(e) { return null; }
    }

    try {
      const ids = playerIds.split(',').map(s => s.trim()).filter(Boolean);
      const results = {};
      await Promise.all(ids.map(async (id) => {
        const [vsLHP, vsRHP] = await Promise.all([
          fetchHittingSplit(id, 'vl'),
          fetchHittingSplit(id, 'vr'),
        ]);
        results[id] = { vsLHP, vsRHP };
      }));
      const resolved = Object.values(results).filter(r => r.vsLHP || r.vsRHP).length;
      return res.status(200).json({
        ok: true, year, type: 'batterplatoon',
        fetchedAt: new Date().toISOString(),
        batterCount: ids.length,
        resolved,
        batters: results,
      });
    } catch(e) {
      return res.status(500).json({ ok: false, type: 'batterplatoon', error: e.message });
    }
  }

  return res.status(400).json({ ok: false, error: 'type must be batting, tto, bullpen, pitcherfbpct, velocity, or batterplatoon' });
}
