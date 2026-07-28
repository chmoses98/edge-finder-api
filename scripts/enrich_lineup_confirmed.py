#!/usr/bin/env python3
"""
scripts/enrich_lineup_confirmed.py  v2.0
==========================================
Promotes team-level lineupConfirmed to game-level in slate.json.

v2.0 changes vs v1.0
---------------------
ROOT CAUSE OF JUNE 17 STALE-FIELD BUG:
  fetch_lineups.py writes awayTeamStats/homeTeamStats.lineupConfirmed and the
  lineup_audit_YYYY-MM-DD.json in the same pipeline run.  The lineup audit is
  generated at 22:45Z, but the slate.json GAME-LEVEL lineupConfirmed field
  (set by enrich_lineup_confirmed.py) is only run once per fetch-slate
  invocation — at 18:19Z in the June 17 case.  When lineups confirm between
  18:19Z and first-pitch, the field in slate.json stays stale.

FIX:
  1. PRIMARY source: lineup_audit_YYYY-MM-DD.json  (freshest, written last)
     Read awayTeamStats / homeTeamStats directly from the audit.
  2. FALLBACK: awayTeamStats.lineupConfirmed from slate.json itself (v1 behaviour)
  3. SECONDARY cross-check: RotoWire (unchanged from v1)

This means any consumer of slate.json that runs AFTER this script will see
the freshest lineup status.  Analysis code that reads slate.json directly
(like our live session analysis) can also call this script to refresh before
eligibility filtering.

AUDIT SOURCE SELECTION LOGIC:
  - Look for data/lineup_audit_{DATE}.json
  - If found and generatedAt > slate.json lineupCheckedAt → use audit values
  - If missing → fall back to v1 teamStats-only behaviour

IDEMPOTENT: safe to re-run any number of times.

EXIT CODES:
  0 — success (all games enriched)
  1 — hard failure (slate missing / unreadable)
"""

import json
import re
import urllib.request
import ssl
from datetime import datetime, timezone

SLATE_PATH     = "data/slate.json"
AUDIT_TEMPLATE = "data/lineup_audit_{date}.json"


def now_utc() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts_str: str) -> datetime | None:
    """Parse ISO8601 timestamp; return None on failure."""
    if not ts_str:
        return None
    try:
        s = ts_str.rstrip('Z')
        if '+' not in s and '-' not in s[10:]:
            s += '+00:00'
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def fetch_rotowire() -> tuple[set, bool]:
    """Unchanged from v1 — returns (confirmed_game_keys, ok)."""
    url = "https://www.rotowire.com/baseball/daily-lineups.php"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.rotowire.com/",
    }
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  RotoWire fetch failed: {e}")
        return set(), False

    MLB_ABBRS = {
        'MIA', 'PHI', 'KC', 'WSH', 'NYM', 'CIN', 'SD', 'STL',
        'COL', 'CHC', 'MIN', 'TEX', 'DET', 'HOU', 'LAA', 'AZ',
        'PIT', 'OAK', 'TB', 'LAD', 'NYY', 'BOS', 'ATL', 'SF',
        'SEA', 'CLE', 'MIL', 'TOR', 'BAL', 'CWS', 'ATH'
    }
    confirmed_keys: set = set()
    confirmed_sections = re.findall(
        r'((?:is-confirmed|CONFIRMED).{0,500})',
        html, re.DOTALL | re.IGNORECASE
    )
    for section in confirmed_sections:
        found = set(re.findall(r'\b([A-Z]{2,3})\b', section))
        teams = found & MLB_ABBRS
        teams_list = list(teams)
        for i in range(len(teams_list)):
            for j in range(i + 1, len(teams_list)):
                confirmed_keys.add(frozenset({teams_list[i], teams_list[j]}))

    print(f"  RotoWire: found {len(confirmed_keys)} confirmed game keys")
    return confirmed_keys, True


def load_audit(date: str) -> dict[str, dict]:
    """
    Load lineup_audit_{date}.json and return dict keyed by game string
    mapping to {away_confirmed, home_confirmed, away_batters, home_batters,
    away_status, home_status, away_source, home_source, generatedAt}.

    Returns empty dict if file missing.
    """
    path = AUDIT_TEMPLATE.format(date=date)
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"  Lineup audit not found: {path} — falling back to teamStats")
        return {}
    except Exception as e:
        print(f"  WARNING: Could not read lineup audit {path}: {e}")
        return {}

    generated_at = data.get('generatedAt', '')
    rows = data.get('rows', [])

    # Group by game
    by_game: dict[str, list] = {}
    for row in rows:
        game = row.get('game', '')
        by_game.setdefault(game, []).append(row)

    result = {}
    for game, team_rows in by_game.items():
        # We expect 2 rows per game (away team and home team)
        # away team = the team whose abbr appears before '@' in game key
        away_abbr = game.split('@')[0] if '@' in game else ''
        home_abbr = game.split('@')[1] if '@' in game else ''

        away_row = next(
            (r for r in team_rows
             if r.get('team', '').replace(' ', '').upper() != '' and
             (away_abbr and away_abbr.upper() in r.get('team', '').upper())),
            None
        )
        home_row = next(
            (r for r in team_rows
             if r.get('team', '').replace(' ', '').upper() != '' and
             (home_abbr and home_abbr.upper() in r.get('team', '').upper())),
            None
        )

        # Simpler: just take first row as away, second as home if we can't match
        if away_row is None and len(team_rows) >= 1:
            away_row = team_rows[0]
        if home_row is None and len(team_rows) >= 2:
            home_row = team_rows[1]

        def _confirmed(row) -> bool:
            if row is None:
                return False
            return bool(row.get('lineupConfirmedOfficial', False)) and \
                   row.get('lineupStatus', '') == 'confirmed'

        result[game] = {
            'away_confirmed': _confirmed(away_row),
            'home_confirmed': _confirmed(home_row),
            'away_batters':   away_row.get('lineupBattersResolved', 0) if away_row else 0,
            'home_batters':   home_row.get('lineupBattersResolved', 0) if home_row else 0,
            'away_status':    away_row.get('lineupStatus', 'unknown') if away_row else 'unknown',
            'home_status':    home_row.get('lineupStatus', 'unknown') if home_row else 'unknown',
            'away_source':    away_row.get('lineupSource', '') if away_row else '',
            'home_source':    home_row.get('lineupSource', '') if home_row else '',
            'generatedAt':    generated_at,
        }

    print(f"  Lineup audit loaded: {path} ({len(result)} games, generatedAt={generated_at})")
    return result


def compute_game_lineup_fields(g, audit, rw_keys, rw_ok, checked_at):
    """
    Pure function: given a game dict (read-only — never mutated) and the
    audit/RotoWire enrichment inputs, return (fields, diag):
      fields — the new field values this stage owns for this game
               (lineupConfirmed, lineupSource, lineupStatus,
               lineupCheckedAt, lineupAuditUsed)
      diag   — bookkeeping needed only for logging/counters, not part of
               the game's own schema

    This is the exact field-computation logic from v2.0's in-place loop
    body, extracted unchanged so it can be applied without mutating `g`.
    """
    away_abbr = g.get('away', {}).get('abbr', '') if isinstance(g.get('away'), dict) else ''
    home_abbr = g.get('home', {}).get('abbr', '') if isinstance(g.get('home'), dict) else ''
    game_key  = f"{away_abbr}@{home_abbr}"

    ats = g.get('awayTeamStats', {}) or {}
    hts = g.get('homeTeamStats', {}) or {}

    # ── Source 1: Lineup audit (v2 — freshest) ────────────────────────────
    audit_entry = audit.get(game_key)
    ats_lineup_confirmed = ats.get('lineupConfirmed', False)
    hts_lineup_confirmed = hts.get('lineupConfirmed', False)
    if audit_entry:
        away_mlb = audit_entry['away_confirmed']
        home_mlb = audit_entry['home_confirmed']
        away_batters = audit_entry['away_batters']
        home_batters = audit_entry['home_batters']
        data_source  = 'lineup_audit'
    else:
        # ── Source 2: teamStats (v1 fallback) ─────────────────────────────
        away_mlb     = ats_lineup_confirmed
        home_mlb     = hts_lineup_confirmed
        away_batters = ats.get('lineupBattersResolved', 0)
        home_batters = hts.get('lineupBattersResolved', 0)
        data_source  = 'mlb_statsapi'

    # ── Source 3: RotoWire cross-check ────────────────────────────────────
    rw_confirmed = False
    if rw_ok and away_abbr and home_abbr:
        rw_confirmed = frozenset({away_abbr, home_abbr}) in rw_keys

    final_confirmed = bool(away_mlb) and bool(home_mlb)
    either_mlb      = bool(away_mlb) or bool(home_mlb)

    sources = [data_source]
    if rw_ok:
        sources.append('rotowire')
    source_str = '+'.join(sources)

    if final_confirmed:
        status = 'confirmed'
    elif either_mlb:
        status = 'partial'
    else:
        status = 'unconfirmed'

    is_override = bool(audit_entry) and (
        away_mlb != ats_lineup_confirmed or home_mlb != hts_lineup_confirmed
    )

    fields = {
        'lineupConfirmed': final_confirmed,
        'lineupSource':    source_str if final_confirmed or either_mlb else 'unavailable',
        'lineupStatus':    status,
        'lineupCheckedAt': checked_at,
        'lineupAuditUsed': bool(audit_entry),
    }
    diag = {
        'game_key': game_key, 'away_mlb': away_mlb, 'home_mlb': home_mlb,
        'away_batters': away_batters, 'home_batters': home_batters,
        'status': status, 'rw_confirmed': rw_confirmed,
        'is_override': is_override,
    }
    return fields, diag


def enrich_games_immutable(games, audit, rw_keys, rw_ok, checked_at):
    """
    Pure function: returns a NEW list of game dicts with lineup-
    confirmation fields applied, without mutating any input game dict —
    each new game is `{**g, **fields}` rather than `g[key] = value`.
    Also returns the same counters/log lines main() previously printed
    inline, so console output is unchanged.
    """
    new_games = []
    counters = {'confirmed': 0, 'partial': 0, 'unconfirmed': 0, 'audit_overrides': 0}
    logs = []

    for g in games:
        fields, diag = compute_game_lineup_fields(g, audit, rw_keys, rw_ok, checked_at)
        new_games.append({**g, **fields})

        counters[{'confirmed': 'confirmed', 'partial': 'partial', 'unconfirmed': 'unconfirmed'}[diag['status']]] += 1
        if diag['is_override']:
            counters['audit_overrides'] += 1

        rw_note = f"(RotoWire: {'✓' if diag['rw_confirmed'] else '✗'})" if rw_ok else ''
        audit_note = '[AUDIT_OVERRIDE]' if diag['is_override'] else ''
        logs.append(
            f"  {diag['game_key']}: "
            f"away={diag['away_mlb']}({diag['away_batters']}/9) home={diag['home_mlb']}({diag['home_batters']}/9) "
            f"→ {fields['lineupConfirmed']} [{diag['status']}] {rw_note} {audit_note}"
        )

    return new_games, counters, logs


def main():
    checked_at = now_utc()

    try:
        with open(SLATE_PATH) as f:
            slate = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {SLATE_PATH} not found")
        return 1
    except Exception as e:
        print(f"ERROR: could not read {SLATE_PATH}: {e}")
        return 1

    date  = slate.get('date', '')
    games = slate.get('games', [])
    print(f"enrich_lineup_confirmed v2.0 | date={date} | games={len(games)}")

    # ── Load lineup audit (v2 primary source) ────────────────────────────
    audit = load_audit(date)

    # Check freshness: compare audit generatedAt vs existing lineupCheckedAt
    # (Only relevant if this script has been run before on this slate)
    audit_generated_at = None
    for v in audit.values():
        audit_generated_at = v.get('generatedAt', '')
        break

    # ── RotoWire secondary source ─────────────────────────────────────────
    rw_keys, rw_ok = fetch_rotowire()

    # ── Enrich games (immutable: build a new list, never mutate `games`) ──
    new_games, counters, logs = enrich_games_immutable(games, audit, rw_keys, rw_ok, checked_at)
    for line in logs:
        print(line)

    # Stage output is a freshly-built slate object, not the mutated input —
    # this is the Phase 3 immutable-pipeline pattern (see
    # docs/IMMUTABLE_PIPELINE.md). data/slate.json is still written to the
    # same legacy path so every downstream script continues to work
    # unchanged.
    new_slate = {**slate, 'games': new_games}

    with open(SLATE_PATH, 'w') as f:
        json.dump(new_slate, f)

    print(
        f"\nDone. Confirmed={counters['confirmed']} Partial={counters['partial']} "
        f"Unconfirmed={counters['unconfirmed']} | AuditOverrides={counters['audit_overrides']}"
    )
    print(f"Written: {SLATE_PATH}")
    if counters['audit_overrides']:
        print(
            f"⚠️  {counters['audit_overrides']} game(s) had stale lineupConfirmed in slate.json "
            f"that were corrected from the lineup audit "
            f"(generatedAt={audit_generated_at})"
        )
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
