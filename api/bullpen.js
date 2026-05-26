export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { season = '2026' } = req.query;

  function pf(val) {
    const n = parseFloat(val);
    return isNaN(n) ? null : n;
  }

  // Flag bullpen quality tiers
  function bullpenGrade(xfip) {
    if (xfip === null) return 'UNKNOWN';
    if (xfip < 3.50) return 'ELITE';
    if (xfip < 4.00) return 'ABOVE_AVERAGE';
    if (xfip < 4.50) return 'AVERAGE';
    if (xfip < 5.00) return 'BELOW_AVERAGE';
    return 'VULNERABLE';
  }

  try {
    // Pull team pitching stats filtered to relievers only
    const [relieverRes, teamRes] = await Promise.all([
      fetch(`https://statsapi.mlb.com/api/v1/teams/stats?season=${season}&sportId=1&group=pitching&gameType=R&stats=season&playerPool=relief`),
      fetch(`https://statsapi.mlb.com/api/v1/teams?sportId=1&season=${season}`)
    ]);

    if (!relieverRes.ok) throw new Error(`Reliever stats fetch failed: ${relieverRes.status}`);
    if (!teamRes.ok) throw new Error(`Team fetch failed: ${teamRes.status}`);

    const relieverData = await relieverRes.json();
    const teamData     = await teamRes.json();

    // Build team ID -> abbreviation map
    const teamMap = {};
    for (const t of (teamData.teams || [])) {
      teamMap[t.id] = {
        abbr: t.abbreviation,
        name: t.name
      };
    }

    // Parse reliever stats by team
    const bullpens = {};
    for (const rec of (relieverData?.stats?.[0]?.splits || [])) {
      const teamId = rec.team?.id;
      const abbr   = rec.team?.abbreviation || teamMap[teamId]?.abbr;
      if (!abbr) continue;

      const s = rec.stat || {};
      const era    = pf(s.era);
      const whip   = pf(s.whip);
      const kPer9  = pf(s.strikeoutsPer9Inn);
      const bbPer9 = pf(s.walksPer9Inn);
      const hr9    = pf(s.homeRunsPer9);
      const ip     = pf(s.inningsPitched);

      // xFIP approximation:
      // xFIP = ((13 * league_avg_HR_per_FB * FB%) + (3 * BB) - (2 * K)) / IP + FIP_constant
      // We don't have FB% from this endpoint so we use a simplified proxy:
      // Simplified xFIP ≈ ERA adjusted for HR luck using league avg HR/9 (1.20 in 2026)
      const leagueHR9 = 1.20;
      let xFIP = null;
      if (era !== null && hr9 !== null && kPer9 !== null && bbPer9 !== null) {
        // Adjust ERA by replacing actual HR rate with league average HR rate
        const hrDiff = (hr9 - leagueHR9) * 1.35; // 1.35 runs per HR above average
        xFIP = Math.round((era - hrDiff) * 100) / 100;
      }

      bullpens[abbr] = {
        abbr,
        name:         teamMap[teamId]?.name || '',
        season,
        era,
        xFIP,
        whip,
        kPer9,
        bbPer9,
        hr9,
        inningsPitched: ip,
        grade:        bullpenGrade(xFIP),
        // Flags for model
        elite:        xFIP !== null && xFIP < 3.50,
        vulnerable:   xFIP !== null && xFIP > 4.50,
      };
    }

    return res.status(200).json({
      ok: true,
      season,
      fetchedAt: new Date().toISOString(),
      teamCount: Object.keys(bullpens).length,
      bullpens
    });

  } catch (err) {
    return res.status(500).json({
      ok: false,
      error: err.message
    });
  }
}
