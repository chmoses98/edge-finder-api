// api/savantpitches.js
// =======================
// Hitter Projection Engine -- Phase 2 raw per-pitch ingestion.
//
// Every other Savant endpoint in this repo (api/savant.js, api/enrich.js)
// requests `group_by=name` (or the /leaderboard/custom CSV, which is
// pre-aggregated server-side by Savant) -- this is the one endpoint in
// this repo that omits group_by entirely, which is what makes Savant's
// statcast_search/csv return one row PER PITCH instead of one row per
// player-season. Same host, same CSV-export mechanism, same
// User-Agent-spoofing pattern every other fetcher here already uses --
// not a new/unstable source, just a different query on the one already
// in use.
//
// Scoped to a single gamePk per request (a full game is ~250-300 pitch
// rows) so this stays well inside a normal Vercel function's response
// size/time budget -- scripts/fetch_statcast_pitch_log.py calls this
// once per not-yet-archived gamePk, never for a whole date range at once.
export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.status(200).end();

  const { gamePk } = req.query;
  if (!gamePk) return res.status(400).json({ ok: false, error: 'gamePk query param required' });

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
  function pi(val) { if (val === undefined || val === '') return null; const n = parseInt(val, 10); return isNaN(n) ? null : n; }
  function ps(val) { return (val === undefined || val === '') ? null : val; }
  function pb01(val) { return val === '1' || val === 'true' || val === 'True'; }

  // Statcast's `description` -> this repo's canonical pitchCallType.
  // Anything not recognized falls to 'other' rather than a guess.
  function classifyCall(description) {
    const d = (description || '').toLowerCase();
    if (d === 'ball' || d === 'blocked_ball' || d === 'pitchout') return 'ball';
    if (d === 'called_strike') return 'called_strike';
    if (d === 'swinging_strike' || d === 'swinging_strike_blocked' || d === 'missed_bunt') return 'swinging_strike';
    if (d === 'foul' || d === 'foul_tip' || d === 'foul_bunt') return 'foul';
    if (d === 'hit_into_play') return 'in_play';
    if (d === 'hit_by_pitch') return 'hit_by_pitch';
    return d ? 'other' : null;
  }

  try {
    const url = `https://baseballsavant.mlb.com/statcast_search/csv?all=true&hfGT=R%7C&game_pk=${encodeURIComponent(gamePk)}&type=details`;
    const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
    if (!r.ok) throw new Error(`Savant pitch-log fetch failed: ${r.status}`);
    const rows = parseCSV(await r.text());

    const pitches = rows.map(row => ({
      gamePk: pi(row['game_pk']) || Number(gamePk),
      gameDate: ps(row['game_date']),
      batterId: ps(row['batter']),
      pitcherId: ps(row['pitcher']),
      batterHand: ps(row['stand']),
      pitcherHand: ps(row['p_throws']),
      inning: pi(row['inning']),
      atBatIndex: pi(row['at_bat_number']),
      pitchNumber: pi(row['pitch_number']),
      balls: pi(row['balls']),
      strikes: pi(row['strikes']),
      outsWhenUp: pi(row['outs_when_up']),
      onFirst: row['on_1b'] !== undefined ? !!ps(row['on_1b']) : null,
      onSecond: row['on_2b'] !== undefined ? !!ps(row['on_2b']) : null,
      onThird: row['on_3b'] !== undefined ? !!ps(row['on_3b']) : null,
      pitchType: ps(row['pitch_type']),
      pitchName: ps(row['pitch_name']),
      releaseSpeed: pf(row['release_speed']),
      spinRate: pf(row['release_spin_rate']),
      inducedVertBreak: pf(row['pfx_z']) !== null ? Math.round(pf(row['pfx_z']) * 12 * 100) / 100 : null,
      horizontalBreak: pf(row['pfx_x']) !== null ? Math.round(pf(row['pfx_x']) * 12 * 100) / 100 : null,
      releaseHeight: pf(row['release_pos_z']),
      releaseSide: pf(row['release_pos_x']),
      extension: pf(row['release_extension']),
      armAngle: pf(row['arm_angle']),
      plateX: pf(row['plate_x']),
      plateZ: pf(row['plate_z']),
      szTop: pf(row['sz_top']),
      szBot: pf(row['sz_bot']),
      pitchCallType: classifyCall(row['description']),
      description: ps(row['description']),
      events: ps(row['events']),
      launchSpeed: pf(row['launch_speed']),
      launchAngle: pf(row['launch_angle']),
      hitCoordX: pf(row['hc_x']),
      hitCoordY: pf(row['hc_y']),
      battedBallType: ps(row['bb_type']),
      estimatedBA: pf(row['estimated_ba_using_speedangle']),
      estimatedWOBA: pf(row['estimated_woba_using_speedangle']),
      wobaValue: pf(row['woba_value']),
    })).filter(p => p.gamePk && p.pitchNumber !== null);

    return res.status(200).json({
      ok: true, gamePk, fetchedAt: new Date().toISOString(),
      pitchCount: pitches.length, pitches,
    });
  } catch (err) {
    return res.status(500).json({ ok: false, gamePk, error: err.message,
      fallback: 'Baseball Savant may be blocking automated requests.' });
  }
}
