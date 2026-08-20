#!/usr/bin/env python3
"""
lib/kalshi_registry_market_builders.py
=========================================
Market-Universe Parity mission: pure, independently-testable builder
functions for the market families scripts/build_kalshi_registry.py's
SERIES_CATALOGUE previously omitted (F3, F7, and the 7 pitcher/hitter
player-prop series) — see that script's SERIES_CATALOGUE docstring for
the root-cause history.

Factored into their own importable module (rather than left as
functions inside build_kalshi_registry.py itself) specifically so they
can be unit-tested directly: build_kalshi_registry.py has no
`if __name__` guard and makes real, unconditional live Kalshi HTTP
calls at import time (an existing, intentional repository convention —
see tests/test_kalshi_discovery_entry_points.py's docstring), so
anything that stays defined inside it can only be tested by reading its
source text, not by exercising the real logic.

Every function here is pure: no network calls, no file I/O, no clock
reads. `price_block()`/`norm()`/`american()` are small, deliberately
self-contained copies of the equivalent helpers already defined inline
in scripts/build_kalshi_registry.py (not imported from there, to avoid
coupling this testable module's behavior to that script's own
unconditional-network-call import-time execution) — kept intentionally
tiny and byte-comparable so a divergence would be obvious in review.
"""


def norm(v):
    if v is None:
        return None
    f = float(v)
    return round(f if f <= 1.0 else f / 100.0, 4)


def american(mid):
    if not mid or mid <= 0 or mid >= 1:
        return None
    return round(-(mid / (1 - mid)) * 100) if mid >= 0.5 else round(((1 - mid) / mid) * 100)


def price_block(m):
    bid = norm(m.get('yes_bid_dollars') or m.get('yes_bid'))
    ask = norm(m.get('yes_ask_dollars') or m.get('yes_ask'))
    last = norm(m.get('last_price_dollars') or m.get('last_price'))
    mid = round(((bid or 0) + (ask or 0)) / 2, 4) if (bid or ask) else None
    return {
        'yes_bid': bid,
        'yes_ask': ask,
        'mid': mid,
        'implied_pct': round(mid * 100, 2) if mid else None,
        'american': american(mid),
        'last_price': last,
        'status': m.get('status', ''),
    }


# Player-prop series -> market_taxonomy family name, consumed by
# lib.research.player_prop_parser.parse_player_prop_market's `family`
# arg (for its stat-text cross-check against the market's own title).
PLAYER_PROP_FAMILY = {
    'KXMLBKS': 'pitcher_strikeouts',
    'KXMLBOUTS': 'pitcher_outs',
    'KXMLBHIT': 'hitter_hits',
    'KXMLBTB': 'hitter_total_bases',
    'KXMLBHRR': 'hitter_hits_runs_rbis',
    'KXMLBRBI': 'hitter_rbis',
    'KXMLBSB': 'hitter_stolen_bases',
}


def build_three_way_period_market(series, mkts_for_suffix, away, home):
    """
    F3/F7 winner markets (Away/Tie/Home) — same contract shape as
    KXMLBF5's existing (untouched) inline block in
    scripts/build_kalshi_registry.py. Returns None if `mkts_for_suffix`
    is empty (no markets for this game+series), otherwise a dict with
    away/home/tie tickers and prices, `researchOnly: True`.
    """
    if not mkts_for_suffix:
        return None
    away_m = next((m for m in mkts_for_suffix if m['ticker'].endswith(f'-{away}')), None)
    home_m = next((m for m in mkts_for_suffix if m['ticker'].endswith(f'-{home}')), None)
    tie_m = next((m for m in mkts_for_suffix if m['ticker'].endswith('-TIE')), None)
    return {
        'series': series,
        'away_ticker': away_m['ticker'] if away_m else None,
        'home_ticker': home_m['ticker'] if home_m else None,
        'tie_ticker': tie_m['ticker'] if tie_m else None,
        'prices': {
            'away': price_block(away_m) if away_m else None,
            'home': price_block(home_m) if home_m else None,
            'tie': price_block(tie_m) if tie_m else None,
        },
        'note': 'Three-way market. YES=away wins, YES=home wins, YES=tied at this period. Bet away or home YES side.',
        'researchOnly': True,
    }


def build_player_prop_ladders(series, mkts_for_suffix, away, home, parse_contract, parse_player_prop_market):
    """
    Groups a period's raw player-prop markets (KXMLBKS/OUTS/HIT/TB/HRR/
    RBI/SB) into one threshold ladder per player. `parse_contract` and
    `parse_player_prop_market` are injected (rather than imported at
    module scope) purely so tests can supply lightweight fakes without
    needing real Kalshi ticker/title strings for every case — production
    always passes lib.kalshi_mlb_contract_parser.parse_contract and
    lib.research.player_prop_parser.parse_player_prop_market, the SAME
    canonical parsers ingestion/settlement already depend on (never a
    third, independent ticker-parsing implementation).

    Returns None if there is nothing to report (no markets at all).
    RESEARCH-ONLY: never read by scripts/merge_odds.py or
    scripts/build_market_ledger.py.
    """
    family = PLAYER_PROP_FAMILY.get(series)
    players = {}
    unparseable = 0
    for m in mkts_for_suffix:
        contract = parse_contract(m)
        parsed = parse_player_prop_market(
            contract.get('ticker'), event_ticker=contract.get('eventTicker'),
            title=contract.get('marketTitle'), subtitle=contract.get('marketSubtitle'),
            away_team=away, home_team=home, family=family,
        )
        if parsed['parseStatus'] != 'PARSED':
            unparseable += 1
            continue
        player_key = (
            f"{parsed['teamAbbr']}:{parsed['displayNameRaw']}" if parsed['displayNameRaw']
            else f"{parsed['teamAbbr']}:{parsed['rawPlayerToken']}"
        )
        entry = players.setdefault(player_key, {
            'displayName': parsed['displayNameRaw'],
            'team': parsed['teamAbbr'],
            'teamResolutionStatus': parsed['teamResolutionStatus'],
            'thresholds': [],
        })
        entry['thresholds'].append({
            'ticker': parsed['marketTicker'],
            'threshold': parsed['threshold'],
            **price_block(m),
        })
    for entry in players.values():
        entry['thresholds'].sort(key=lambda x: x.get('threshold') if x.get('threshold') is not None else -1)
    if not players and unparseable == 0:
        return None
    return {
        'series': series,
        'family': family,
        'players': players,
        'unparseableCount': unparseable,
        'researchOnly': True,
        'note': 'Per-player N+ threshold ladder. RESEARCH-ONLY — not read by merge_odds.py/build_market_ledger.py.',
    }
