/**
 * api/savant_tto.js
 *
 * Computes TTO (Times Through Order) splits for given pitcher IDs.
 * Uses MLB Stats API game logs to estimate performance by TTO window.
 *
 * Method: for each pitcher, fetch last 10 game logs with inning-by-inning data.
 * Innings 1-3 = 1st TTO, innings 4-6 = 2nd TTO, innings 7+ = 3rd TTO.
 * Compute FIP proxy for each window. ttoSplit = 3rd TTO FIP - 1st TTO FIP.
 *
 * Called with: ?playerIds=id1,id2,...&year=2026
 */
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { playerIds, year = '2026' } = req.query;
  if (!playerIds) return res.status(400).json({ ok: false, error: 'playerIds required' });

  const FIP_CONST = 3.10;
  const MIN_PA    = 20;  // minimum PA in window for reliable split

  function pf(val) { const n = parseFloat(val); return isNaN(n) ? null : n; }

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

  // Fetch inning-by-inning linescore for a game to determine TTO breakdown
  async function fetchInningStats(gamePk, pitcherId) {
    try {
      const r = await fetch(`https://statsapi.mlb.com/api/v1/game/${gamePk}/boxscore`);
      if (!r.ok) return null;
      const box = await r.json();
      // Find which side the pitcher is on
      for (const side of ['away', 'home']) {
        const pitchers = box.teams?.[side]?.pitchers || [];
        if (!pitchers.includes(parseInt(pitcherId))) continue;
        const players  = box.teams?.[side]?.players || {};
        const pData    = players[`ID${pitcherId}`];
        if (!pData) return null;
        const s = pData.stats?.pitching;
        if (!s) return null;
        // We get total game stats — use IP to estimate TTO windows
        const ipRaw = parseFloat(s.inningsPitched || '0');
        const ip    = Math.floor(ipRaw) + (ipRaw % 1) / 0.3 * 0.333;
        return {
          ip,
          er:  parseInt(s.earnedRuns || 0),
          hr:  parseInt(s.homeRuns   || 0),
          bb:  parseInt(s.baseOnBalls || 0),
          k:   parseInt(s.strikeOuts  || 0),
        };
      }
      return null;
    } catch(e) { return null; }
  }

  async function computeTTO(pitcherId) {
    const logs = await fetchGameLogs(pitcherId);
    if (!logs.length) return { available: false, reason: 'no_game_logs' };

    // Bucket games by how deep the starter went
    // TTO1 window: games where starter went ≥1 IP (all starts)
    // TTO3 window: games where starter went ≥6 IP (deep starts)
    const tto1Games = logs;  // all starts = faced lineup at least once
    const tto3Games = logs.filter(l => {
      const ipRaw = parseFloat(l.stat?.inningsPitched || '0');
      const ip    = Math.floor(ipRaw) + (ipRaw % 1) / 0.3 * 0.333;
      return ip >= 6.0;  // faced lineup 3rd time
    });

    if (tto3Games.length < 3) return { available: false, reason: 'insufficient_deep_starts' };

    const computeFIP = (games) => {
      let totalIP = 0, totalHR = 0, totalBB = 0, totalK = 0;
      for (const l of games) {
        const s = l.stat || {};
        const ipRaw = parseFloat(s.inningsPitched || '0');
        const ip    = Math.floor(ipRaw) + (ipRaw % 1) / 0.3 * 0.333;
        totalIP += ip;
        totalHR += parseInt(s.homeRuns    || 0);
        totalBB += parseInt(s.baseOnBalls || 0);
        totalK  += parseInt(s.strikeOuts  || 0);
      }
      if (totalIP < 5) return null;
      return Math.round(((13*totalHR + 3*totalBB - 2*totalK) / totalIP + FIP_CONST) * 100) / 100;
    };

    // Proxy for TTO1: take early-exit games (≤4 IP) as signal for early-inning performance
    // Proxy for TTO3: take deep-start games (≥6 IP) as signal for late-inning performance
    // Note: this is a rough approximation. The ideal is inning-by-inning stats.
    // For most starters, overall FIP ≈ TTO1 FIP; deep start FIP tells us late performance.
    const earlyGames = logs.filter(l => {
      const ipRaw = parseFloat(l.stat?.inningsPitched || '0');
      return (Math.floor(ipRaw) + (ipRaw % 1) / 0.3 * 0.333) <= 4.0;
    });

    // If not enough early exits, use all games as TTO1 proxy (overall FIP)
    const tto1FIP = earlyGames.length >= 3
      ? computeFIP(earlyGames)
      : computeFIP(tto1Games);
    const tto3FIP = computeFIP(tto3Games);

    if (tto1FIP === null || tto3FIP === null) {
      return { available: false, reason: 'insufficient_ip' };
    }

    const ttoSplit = Math.round((tto3FIP - tto1FIP) * 100) / 100;
    return {
      available:   true,
      ttoSplit,
      ttoRisk:     ttoSplit > 0.50,
      tto1:        { fip: tto1FIP, gamesUsed: earlyGames.length || tto1Games.length, method: 'game_log_proxy' },
      tto3:        { fip: tto3FIP, gamesUsed: tto3Games.length, method: 'deep_starts' },
      note:        'game_log_proxy (Savant statcast_search unavailable from server)',
    };
  }

  try {
    const ids = playerIds.split(',').map(s => s.trim()).filter(Boolean);
    const results = {};

    await Promise.all(ids.map(async (id) => {
      results[id] = await computeTTO(id);
    }));

    return res.status(200).json({ ok: true, year, fetchedAt: new Date().toISOString(), pitchers: results });
  } catch(err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
}
