"""
scripts/fetch_lineups.py — v2.1
Fetches confirmed starting lineups from MLB Stats API boxscore endpoint
and computes lineupWOBADelta + lineupAdj for each team in slate.json.

Changes from v2.0:
- Phase 5: split into a network adapter (fetch_boxscore), a pure parser
  (parse_lineup_response) with no I/O of its own, and a pure per-slate
  transform (apply_lineups_immutable) that builds a NEW slate object
  instead of mutating the loaded one in place. main() is now purely an
  orchestration adapter: it fetches raw data for every game first, parses
  each response, then applies the whole batch in one pure pass. CLI
  invocation, file paths, and output content are unchanged — see
  docs/IMMUTABLE_PIPELINE.md's fetch_lineups.py section for the full
  before/after contract and the golden-equivalence tests that prove it.
- Factored the four structurally-identical "missing/error lineup" field
  blocks (no gameId, batting order not yet posted, API returned nothing,
  exception while processing) into one missing_lineup_fields(reason,
  status) helper — same fields, same values, just no longer four
  hand-copied dict literals that could silently drift apart.

Changes from v1.0:
- Delta now computed vs team's own season xwOBA (not league average)
  Reason: league-average delta misstates the adjustment for above/below-average
  offenses. A .340 xwOBA team sitting their best hitters looks neutral vs league
  avg but is a meaningful downgrade vs their own baseline.
- lineupConfirmed flag added (True/False) — downstream logic gates TT bets on this
- lineupAdj field added: R/G adjustment ready to apply directly to offense_baseline
  Formula: lineupAdj = lineupWOBADelta * 4.5
  (wOBA delta * 4.5 converts wOBA gap to expected R/G change, per MODEL_CORE Section 1 Step 2)
- lineupBattersResolved added: count of batters with real xwOBA data (out of 9)
- Requires savant_team.json (fetched by fetch_savant_team.py) and teamstats.json
"""

import json
import os
import tempfile
import time
import urllib.request

SEASON = '2026'
MIN_BATTERS_FOR_CONFIRMED = 6  # need at least 6/9 xwOBA values to apply adjustment
WOBA_TO_RPG_SCALAR = 4.5       # MODEL_CORE Section 1 Step 2: wOBA delta * 4.5 = R/G adj
LINEUP_ADJ_CAP = 0.25          # cap at ±0.25 R/G per MODEL_CORE

def fetch_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f'  fetch error: {e} | {url[:80]}')
        return None

def load_batter_woba():
    """Load individual batter xwOBA from savant_team.json (keyed by player_id string)."""
    try:
        with open('data/savant_team.json') as f:
            savant = json.load(f)
        batters = savant.get('batters', {})
        print(f'Loaded {len(batters)} batter xwOBA values from savant_team.json')
        return batters
    except Exception as e:
        print(f'WARNING: Could not load savant_team.json: {e}')
        return {}

def load_team_woba():
    """Load team season xwOBA from savant_team.json (keyed by abbr)."""
    try:
        with open('data/savant_team.json') as f:
            savant = json.load(f)
        teams = savant.get('teams', {})
        # Build abbr -> xwoba map
        team_woba = {}
        for abbr, data in teams.items():
            xw = data.get('xwoba')
            if xw is not None:
                team_woba[abbr] = float(xw)
        print(f'Loaded season xwOBA for {len(team_woba)} teams')
        return team_woba
    except Exception as e:
        print(f'WARNING: Could not load team xwOBA from savant_team.json: {e}')
        return {}

POSITIONAL_WOBA = {
    'C': 0.305, '1B': 0.335, '2B': 0.315, '3B': 0.325,
    'SS': 0.310, 'LF': 0.330, 'RF': 0.330, 'CF': 0.315,
    'DH': 0.340, 'P': 0.145,
}
LEAGUE_AVG_WOBA = 0.318

def get_positional_fallback(player_data):
    """Return positional average wOBA when individual xwOBA unavailable."""
    pos = player_data.get('position', {}).get('abbreviation', '')
    return POSITIONAL_WOBA.get(pos, LEAGUE_AVG_WOBA)


def _write_slate_atomic(slate, path='data/slate.json'):
    """
    Write `slate` to `path` atomically: serialize to a temp file in the
    same directory, fsync it, then move it into place with os.replace().
    A plain `open(path, 'w')` + `json.dump()` writes incrementally, so a
    serialization failure partway through (verified empirically during
    the Phase 5 pre-refactor audit) leaves a truncated, invalid JSON file
    at `path` — this never happens with atomic replace: any exception
    before the final os.replace() leaves the previous valid file (or no
    file, on a first run) completely untouched. Output content and
    format (json.dump(slate, f), no indent/sort_keys — unlike
    lib/pipeline_artifacts.py's artifacts, this is the raw legacy slate
    object, not an envelope) are byte-for-byte unchanged from before;
    only the write mechanism is hardened. Applied inline rather than via
    lib/pipeline_artifacts.write_stage_artifact(), which wraps its
    payload in a meta/data envelope this file's format must not have —
    reusing it here would be a real output-format change, not a pure
    reliability fix.
    """
    dest_dir = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(prefix='.slate.', suffix='.json.tmp', dir=dest_dir)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(slate, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def missing_lineup_fields(reason, status='missing'):
    """
    The full 13-field "no usable lineup data" block, parameterized by
    `status` ('missing' when the batting order was never posted / never
    fetched at all, 'unknown' when an exception occurred while
    processing an otherwise-successful response) and `reason` (the
    human-readable explanation). Every one of these fields, and their
    values, are unchanged from the pre-Phase-5 implementation — this
    helper only replaces four byte-identical hand-copied dict literals
    with one parameterized call.
    """
    return {
        # Legacy
        'lineupConfirmed': False,
        'lineupWOBADelta': None,
        'lineupAdj': None,
        # Phase 1B: Separated fields
        'lineupPosted':              False,
        'lineupStatus':              status,
        'lineupConfirmedOfficial':   False,
        'lineupSource':              'mlb_stats_api',
        'lineupBattersExpected':     9,
        'lineupBattersFound':        0,
        'lineupBattersResolved':     0,
        'lineupAdjAvailable':        False,
        'lineupAdjApplied':          False,
        'lineupDataQuality':         'none',
        'lineupStatusReason':        reason,
    }


# ── Network adapter ────────────────────────────────────────────────────────────

def fetch_boxscore(game_pk, timeout=15):
    """
    Network adapter: fetch the raw MLB Stats API boxscore JSON for one
    game. Returns the parsed JSON dict, or None on any failure (network
    error, timeout, non-2xx, malformed JSON — see fetch_json(), which
    swallows every exception type uniformly). Makes no attempt to
    interpret the response shape; that is parse_lineup_response()'s job.
    """
    url = f'https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore'
    return fetch_json(url, timeout=timeout)


# ── Pure parser ────────────────────────────────────────────────────────────────

def parse_lineup_response(data, away_abbr, home_abbr, batter_woba_map, team_woba_map):
    """
    Pure function: given an already-fetched raw boxscore dict (or a
    falsy value if the fetch failed) plus the already-loaded wOBA maps,
    return {'away': {...}, 'home': {...}} in the exact shape
    fetch_lineup_for_game() has always returned, or None if `data` is
    falsy. Makes no network calls, no file I/O, reads no environment
    variables, and uses no wall-clock time — the same raw `data` and
    maps always produce the same result.

    lineupWOBADelta = confirmed_lineup_avg_xwOBA - team_season_xwOBA
    lineupAdj = lineupWOBADelta * WOBA_TO_RPG_SCALAR (capped at ±0.25 R/G)
    lineupConfirmed = True if battingOrder is present in boxscore
    """
    if not data:
        return None

    result = {}
    for side, abbr in [('away', away_abbr), ('home', home_abbr)]:
        try:
            team_data = data.get('teams', {}).get(side, {})
            batters_order = team_data.get('battingOrder', [])
            players = team_data.get('players', {})

            if not batters_order:
                # Lineup not yet posted
                result[side] = missing_lineup_fields(
                    'Batting order not yet posted by MLB Stats API'
                )
                continue

            # Collect xwOBA for each batter in the lineup
            lineup_wobas = []
            real_data_count = 0
            fallback_count = 0

            for player_id in batters_order[:9]:
                pid = str(player_id)
                player_data = players.get(f'ID{pid}', {})

                xwoba = batter_woba_map.get(pid)
                if xwoba is not None:
                    lineup_wobas.append(float(xwoba))
                    real_data_count += 1
                else:
                    # Use positional fallback rather than skipping
                    fallback = get_positional_fallback(player_data)
                    lineup_wobas.append(fallback)
                    fallback_count += 1

            if len(lineup_wobas) < 1:
                result[side] = {
                    'lineupConfirmed': False,
                    'lineupWOBADelta': None,
                    'lineupAdj': None,
                    'lineupBattersResolved': 0,
                }
                continue

            lineup_avg_woba = sum(lineup_wobas) / len(lineup_wobas)

            # Delta vs team's OWN season xwOBA (not league average)
            team_season_woba = team_woba_map.get(abbr)
            if team_season_woba is None:
                # Fall back to league average if team data missing
                team_season_woba = LEAGUE_AVG_WOBA
                print(f'  WARNING: No season xwOBA for {abbr} — using league avg {LEAGUE_AVG_WOBA}')

            raw_delta = lineup_avg_woba - team_season_woba
            # R/G adjustment: wOBA delta * 4.5 scalar, capped at ±0.25
            lineup_adj = max(-LINEUP_ADJ_CAP, min(LINEUP_ADJ_CAP, raw_delta * WOBA_TO_RPG_SCALAR))
            lineup_adj = round(lineup_adj, 3)
            raw_delta = round(raw_delta, 4)

            # Only mark confirmed if we have enough real data
            confirmed = real_data_count >= MIN_BATTERS_FOR_CONFIRMED

            # Phase 1B: Separated lineup fields
            # lineupPosted: battingOrder returned by API (independent of xwOBA resolution)
            lineup_posted = True  # we reached here, so battingOrder was present
            # lineupConfirmedOfficial: MLB Stats API returned battingOrder = official lineup
            # NOTE: MLB Stats API battingOrder presence IS official confirmation. This is
            # distinct from xwOBA data quality (whether we can compute lineup adjustments).
            lineup_confirmed_official = True  # battingOrder present = official

            adj_available = real_data_count >= MIN_BATTERS_FOR_CONFIRMED
            adj_applied   = adj_available  # we apply adj if available

            if adj_applied:
                data_quality = 'full' if real_data_count >= 8 else 'partial'
                status_reason = (
                    f'Official lineup confirmed, {real_data_count}/9 batters resolved for xwOBA adjustment'
                )
            else:
                data_quality = 'partial' if real_data_count > 0 else 'insufficient'
                status_reason = (
                    f'Official lineup confirmed but only {real_data_count}/9 batters resolved — '
                    f'lineup adjustment NOT applied (need {MIN_BATTERS_FOR_CONFIRMED}/9)'
                )

            result[side] = {
                # Legacy field (kept for backward compat with existing gates)
                'lineupConfirmed': confirmed,
                # Phase 1B: New separated fields
                'lineupPosted':              lineup_posted,
                'lineupStatus':              'confirmed',
                'lineupConfirmedOfficial':   lineup_confirmed_official,
                'lineupSource':              'mlb_stats_api',
                # NOTE: RotoWire/RotoGrinders sources not implemented — MLB Stats API
                # battingOrder is used as primary. Other sources would require paid API
                # access (RotoWire) or scraping (RotoGrinders), which is out of scope.
                # lineupSource='mlb_stats_api' when battingOrder present.
                'lineupBattersExpected':     9,
                'lineupBattersFound':        len(batters_order[:9]),
                'lineupBattersResolved':     real_data_count,
                'lineupAdjAvailable':        adj_available,
                'lineupAdjApplied':          adj_applied,
                'lineupDataQuality':         data_quality,
                'lineupStatusReason':        status_reason,
                # Legacy fields
                'lineupWOBADelta': raw_delta,
                'lineupAdj': lineup_adj if adj_applied else None,
                'lineupBattersFallback': fallback_count,
                'lineupAvgWOBA': round(lineup_avg_woba, 3),
                'teamSeasonWOBA': round(team_season_woba, 3),
            }

            if abs(raw_delta) > 0.005 or not confirmed:
                direction = '↑' if raw_delta > 0 else '↓'
                conf_str = 'CONFIRMED' if confirmed else f'PARTIAL ({real_data_count}/9 real)'
                print(f'  {abbr} lineup [{conf_str}]: avg_xwOBA={lineup_avg_woba:.3f} '
                      f'team_szn={team_season_woba:.3f} delta={raw_delta:+.4f} '
                      f'{direction} adj={lineup_adj:+.3f} R/G '
                      f'(real={real_data_count}, fallback={fallback_count})')

        except Exception as e:
            print(f'  Error processing {abbr} lineup: {e}')
            result[side] = missing_lineup_fields(f'Error fetching lineup: {e}', status='unknown')

    return result


def fetch_lineup_for_game(game_pk, away_abbr, home_abbr, batter_woba_map, team_woba_map):
    """
    Backward-compatible orchestration wrapper: fetch + parse in one call,
    preserving the exact public signature and behavior this function has
    always had. New code (main(), tests) should prefer calling
    fetch_boxscore() and parse_lineup_response() separately so the
    network call and the pure parse can be exercised/mocked independently.
    """
    data = fetch_boxscore(game_pk, timeout=15)
    return parse_lineup_response(data, away_abbr, home_abbr, batter_woba_map, team_woba_map)


# ── Pure per-slate transform ───────────────────────────────────────────────────

def compute_game_lineup_stats_fields(game, lineup_result):
    """
    Pure function: given one game dict and its already-fetched-and-parsed
    lineup_result (parse_lineup_response()'s return value for this game,
    or a pre-built missing_lineup_fields(...) dict when there was no
    gameId to fetch with at all), return NEW (awayTeamStats, homeTeamStats)
    dicts — shallow copies of whatever was already on `game`, updated
    with the lineup fields — without mutating `game` itself. Mirrors
    exactly what `game.setdefault(side_key, {}).update(d)` did before:
    additive to any pre-existing keys on that side's stats dict, not a
    wholesale replacement.
    """
    away_ts = dict(game.get('awayTeamStats') or {})
    home_ts = dict(game.get('homeTeamStats') or {})
    if lineup_result is None:
        away_ts.update(missing_lineup_fields('MLB Stats API returned no data for this game'))
        home_ts.update(missing_lineup_fields('MLB Stats API returned no data for this game'))
    else:
        away_ts.update(lineup_result.get('away', {}))
        home_ts.update(lineup_result.get('home', {}))
    return away_ts, home_ts


def apply_lineups_immutable(slate, lineup_results):
    """
    Pure transform: given the parsed slate and a list of per-game lineup
    results (parallel to slate['games'], each entry either the dict
    parse_lineup_response() returned for that game, a pre-built
    missing_lineup_fields(...) dict for a game with no gameId, or None
    if the fetch failed), return a NEW slate object with each game's
    awayTeamStats/homeTeamStats updated — without mutating `slate` or
    any game dict inside it, without changing any other top-level slate
    field, the number of games, or game order.
    """
    new_games = []
    for game, lineup_result in zip(slate.get('games', []), lineup_results):
        new_game = dict(game)
        away_ts, home_ts = compute_game_lineup_stats_fields(game, lineup_result)
        new_game['awayTeamStats'] = away_ts
        new_game['homeTeamStats'] = home_ts
        new_games.append(new_game)

    new_slate = dict(slate)
    new_slate['games'] = new_games
    return new_slate


def main():
    import time as t
    start = t.time()

    with open('data/slate.json') as f:
        slate = json.load(f)

    batter_woba = load_batter_woba()
    team_woba = load_team_woba()

    if not batter_woba:
        print('No batter wOBA data — lineup adjustments will be null for all games')

    games = slate.get('games', [])
    print(f'Fetching lineups for {len(games)} games...')

    confirmed_count = 0
    partial_count = 0
    missing_count = 0

    # ── Fetch + parse pass (network + pure parse; no slate mutation here) ──────
    lineup_results = []
    for game in games:
        game_pk   = game.get('gameId')
        away_abbr = game.get('away', {}).get('abbr', '')
        home_abbr = game.get('home', {}).get('abbr', '')

        if not game_pk:
            lineup_results.append({
                'away': missing_lineup_fields('No gameId available — cannot fetch lineup'),
                'home': missing_lineup_fields('No gameId available — cannot fetch lineup'),
            })
            missing_count += 2
            continue

        data = fetch_boxscore(game_pk)
        t.sleep(0.2)
        parsed = parse_lineup_response(data, away_abbr, home_abbr, batter_woba, team_woba)
        lineup_results.append(parsed)  # may be None -> apply_lineups_immutable's own missing-block path

        if parsed is None:
            missing_count += 2
            continue

        for side_name in ('away', 'home'):
            d = parsed.get(side_name, {})
            if d.get('lineupConfirmed'):
                confirmed_count += 1
            elif d.get('lineupBattersResolved', 0) > 0:
                partial_count += 1
            else:
                missing_count += 1

    # ── Pure transform pass (builds the new slate object) ──────────────────────
    slate = apply_lineups_immutable(slate, lineup_results)
    games = slate.get('games', [])

    _write_slate_atomic(slate)

    elapsed = round(t.time() - start, 1)
    print(f'\nDone in {elapsed}s')
    print(f'  Confirmed (≥{MIN_BATTERS_FOR_CONFIRMED}/9 real xwOBA): {confirmed_count}')
    print(f'  Partial (<{MIN_BATTERS_FOR_CONFIRMED}/9 real xwOBA, adj not applied): {partial_count}')
    print(f'  Missing (lineup not posted): {missing_count}')
    print(f'  lineupAdj applied only when lineupConfirmed=True')

    # Phase 1B: Generate lineup audit artifact
    _generate_lineup_audit(slate, games)

def _generate_lineup_audit(slate, games):
    """
    Phase 1B: Write lineup audit files:
      data/lineup_audit_YYYY-MM-DD.json
      data/lineup_audit_YYYY-MM-DD.csv
    """
    import os, csv
    from datetime import datetime, timezone

    today = slate.get('date', datetime.now(tz=timezone.utc).strftime('%Y-%m-%d'))
    if not today:
        today = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')

    audit_rows = []
    for game in games:
        away = game.get('away', {})
        home  = game.get('home', {})
        away_name = away.get('team', away.get('abbr', '?'))
        home_name  = home.get('team',  home.get('abbr', '?'))
        game_label = f"{away.get('abbr','?')}@{home.get('abbr','?')}"

        for side_key, team_name in [('awayTeamStats', away_name), ('homeTeamStats', home_name)]:
            ts = game.get(side_key, {}) or {}
            row = {
                'date':                    today,
                'game':                    game_label,
                'team':                    team_name,
                'lineupStatus':            ts.get('lineupStatus', 'unknown'),
                'lineupConfirmedOfficial': ts.get('lineupConfirmedOfficial', False),
                'lineupSource':            ts.get('lineupSource', 'mlb_stats_api'),
                'lineupBattersExpected':   ts.get('lineupBattersExpected', 9),
                'lineupBattersFound':      ts.get('lineupBattersFound', 0),
                'lineupBattersResolved':   ts.get('lineupBattersResolved', 0),
                'lineupAdjAvailable':      ts.get('lineupAdjAvailable', False),
                'lineupAdjApplied':        ts.get('lineupAdjApplied', False),
                'lineupDataQuality':       ts.get('lineupDataQuality', 'none'),
                'lineupStatusReason':      ts.get('lineupStatusReason', ''),
                'reasonCodes':             '',
            }
            # Build reason codes
            rc = []
            if ts.get('lineupConfirmedOfficial'):
                rc.append('LINEUP_CONFIRMED_OFFICIAL')
            elif ts.get('lineupStatus') == 'projected':
                rc.append('LINEUP_PROJECTED_ONLY')
            elif ts.get('lineupStatus') == 'missing':
                rc.append('LINEUP_MISSING')
            if ts.get('lineupAdjApplied'):
                rc.append('LINEUP_ADJ_APPLIED')
            elif ts.get('lineupConfirmedOfficial') and not ts.get('lineupAdjAvailable'):
                rc.append('LINEUP_ADJ_UNAVAILABLE_BUT_OFFICIAL_CONFIRMED')
            elif not ts.get('lineupAdjAvailable'):
                rc.append('LINEUP_ADJ_UNAVAILABLE')
            row['reasonCodes'] = '|'.join(rc)
            audit_rows.append(row)

    os.makedirs('data', exist_ok=True)
    json_path = f'data/lineup_audit_{today}.json'
    csv_path  = f'data/lineup_audit_{today}.csv'

    with open(json_path, 'w') as f:
        import json
        json.dump({'date': today, 'generated_at': datetime.now(tz=timezone.utc).isoformat(),
                   'rows': audit_rows}, f, indent=2)

    if audit_rows:
        fieldnames = list(audit_rows[0].keys())
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(audit_rows)

    print(f'  Lineup audit written: {json_path} ({len(audit_rows)} rows)')

if __name__ == '__main__':
    main()
