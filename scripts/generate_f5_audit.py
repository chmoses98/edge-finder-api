#!/usr/bin/env python3
"""
scripts/generate_f5_audit.py
==============================
Phase 1A: Generate daily F5 audit files:
  data/f5_audit_YYYY-MM-DD.json
  data/f5_audit_YYYY-MM-DD.csv

Reads from data/kalshi_market_registry.json and data/slate.json.
Called after build_kalshi_registry.py and merge_odds.py.

Columns:
  date, game, away, home, startTime
  eventTicker, marketTicker (away/home/tie), marketTitle
  mappedOutcome (away/home/tie/unknown)
  yesBid, yesAsk, noBid, noAsk
  midPrice, lastPrice, kalshiVF, executablePriceUsed
  modelProb, rawEdgeVsVF, rawEdgeVsExecutable
  calibratedEdgeVsExecutable
  maxBetPrice, priceSnapshotTimestamp
  mappingConfidence, eligibilityStatus, reasonCodes
"""

import json, csv, os, sys
from datetime import datetime, timezone, timedelta

DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now(tz=timezone(timedelta(hours=-4))).strftime('%Y-%m-%d')

REGISTRY_PATH = 'data/kalshi_market_registry.json'
SLATE_PATH    = 'data/slate.json'
OUT_JSON = f'data/f5_audit_{DATE}.json'
OUT_CSV  = f'data/f5_audit_{DATE}.csv'

def norm_cents(v):
    """Normalize price to cents (0-100 scale)."""
    if v is None: return None
    f = float(v)
    return round(f * 100 if f <= 1.0 else f, 4)

def cents_to_prob(c):
    if c is None: return None
    return round(c / 100.0, 6)

def raw_edge(model_prob, market_prob):
    if model_prob is None or market_prob is None: return None
    return round((model_prob - market_prob) * 100, 3)

CAL_MEDIUM = 0.255

def cal_edge(raw_e, cal_factor=CAL_MEDIUM):
    if raw_e is None: return None
    return round(raw_e * cal_factor, 3)

def main():
    try:
        with open(REGISTRY_PATH) as f:
            reg_doc = json.load(f)
    except FileNotFoundError:
        print(f'ERROR: {REGISTRY_PATH} not found — run build_kalshi_registry.py first')
        sys.exit(1)

    registry = reg_doc.get('registry', {})
    snapshot_ts = reg_doc.get('generated_at', '')

    # Load slate for modelProb if available
    model_probs = {}
    try:
        with open(SLATE_PATH) as f:
            slate = json.load(f)
        for game in slate.get('games', []):
            for row in game.get('marketLedger', []):
                mkt = row.get('market', '')
                if mkt in ('F5_ML_Away', 'F5_ML_Home') and row.get('modelProb') is not None:
                    ticker = row.get('marketTicker', '')
                    if ticker:
                        model_probs[ticker] = row.get('modelProb')
    except (FileNotFoundError, Exception) as e:
        print(f'  NOTE: Could not load slate for modelProb: {e}')

    audit_rows = []
    
    for key, entry in sorted(registry.items()):
        away = entry.get('away', '')
        home  = entry.get('home', '')
        game_label = f'{away}@{home}'
        start_time = entry.get('game_time_et', '')
        
        f5 = entry.get('markets', {}).get('f5_moneyline', {})
        if not f5:
            # Game has no F5 market at all — still log a row for audit visibility
            audit_rows.append({
                'date': DATE, 'game': game_label, 'away': away, 'home': home,
                'startTime': start_time,
                'eventTicker': None, 'marketTicker': None, 'marketTitle': None,
                'mappedOutcome': 'unknown',
                'yesBid': None, 'yesAsk': None, 'noBid': None, 'noAsk': None,
                'midPrice': None, 'lastPrice': None, 'kalshiVF': None,
                'executablePriceUsed': None,
                'modelProb': None, 'rawEdgeVsVF': None, 'rawEdgeVsExecutable': None,
                'calibratedEdgeVsExecutable': None,
                'maxBetPrice': None, 'priceSnapshotTimestamp': snapshot_ts,
                'mappingConfidence': 'none',
                'eligibilityStatus': 'F5_MARKET_ABSENT',
                'reasonCodes': 'F5_TIE_MARKET_UNMAPPED',
            })
            continue
        
        prices  = f5.get('prices', {}) or {}
        
        # Check if all three markets are present
        tie_present = f5.get('tie_ticker') is not None and (prices.get('tie') or {}).get('yes_bid') is not None

        for outcome, ticker_key, price_key in [
            ('away', 'away_ticker', 'away'),
            ('home', 'home_ticker', 'home'),
            ('tie',  'tie_ticker',  'tie'),
        ]:
            tkr = f5.get(ticker_key)
            p   = (prices.get(price_key) or {})
            
            yes_bid_c = norm_cents(p.get('yes_bid'))
            yes_ask_c = norm_cents(p.get('yes_ask'))
            no_bid_c  = round(100 - yes_ask_c, 4) if yes_ask_c is not None else None
            no_ask_c  = round(100 - yes_bid_c, 4) if yes_bid_c is not None else None
            mid_c     = norm_cents(p.get('mid'))
            last_c    = norm_cents(p.get('last_price'))
            
            # Kalshi VF = mid-based implied prob
            kalshi_vf = cents_to_prob(mid_c)
            
            # For YES bets (away/home outcome): executable = yes_ask
            # For TIE: also a YES bet on the tie market
            exec_price = yes_ask_c
            exec_prob  = cents_to_prob(exec_price)
            
            # Model prob from ledger if available
            model_prob = model_probs.get(tkr)
            
            raw_vs_vf   = raw_edge(model_prob / 100.0 if model_prob else None, kalshi_vf)
            raw_vs_exec = raw_edge(model_prob / 100.0 if model_prob else None, exec_prob)
            cal_vs_exec = cal_edge(raw_vs_exec)
            
            # Mapping confidence
            if outcome in ('away', 'home') and tkr:
                map_confidence = 'high'
                elg_status = 'ELIGIBLE' if (yes_ask_c is not None) else 'MISSING_PRICE'
                rc = 'F5_MARKET_MAPPED_CONFIDENTLY'
            elif outcome == 'tie' and tkr:
                map_confidence = 'high'
                elg_status = 'TIE_PRESENT'
                rc = 'F5_MARKET_MAPPED_CONFIDENTLY'
            else:
                map_confidence = 'none'
                elg_status = 'F5_MAPPING_AMBIGUOUS'
                rc = 'F5_MAPPING_AMBIGUOUS'
            
            if not tie_present and outcome == 'tie':
                elg_status = 'F5_TIE_MARKET_ABSENT'
                rc = 'F5_TIE_MARKET_UNMAPPED'
            
            audit_rows.append({
                'date':              DATE,
                'game':              game_label,
                'away':              away,
                'home':              home,
                'startTime':         start_time,
                'eventTicker':       f5.get('eventTicker', f5.get('series', 'KXMLBF5') + '-' + entry.get('event_ticker_suffix', '')),
                'marketTicker':      tkr,
                'marketTitle':       f'F5 {outcome.upper()} wins — {game_label}',
                'mappedOutcome':     outcome,
                'yesBid':            yes_bid_c,
                'yesAsk':            yes_ask_c,
                'noBid':             no_bid_c,
                'noAsk':             no_ask_c,
                'midPrice':          mid_c,
                'lastPrice':         last_c,
                'kalshiVF':          round(kalshi_vf * 100, 3) if kalshi_vf else None,
                'executablePriceUsed': exec_price,
                'modelProb':         model_prob,
                'rawEdgeVsVF':       raw_vs_vf,
                'rawEdgeVsExecutable': raw_vs_exec,
                'calibratedEdgeVsExecutable': cal_vs_exec,
                'maxBetPrice':       exec_price,  # set max = current price at audit time
                'priceSnapshotTimestamp': snapshot_ts,
                'mappingConfidence': map_confidence,
                'eligibilityStatus': elg_status,
                'reasonCodes':       rc,
            })
    
    os.makedirs('data', exist_ok=True)
    
    with open(OUT_JSON, 'w') as f:
        json.dump({
            'date': DATE,
            'generated_at': datetime.now(tz=timezone.utc).isoformat(),
            'total_rows': len(audit_rows),
            'rows': audit_rows,
        }, f, indent=2)
    print(f'[F5 AUDIT] Written: {OUT_JSON} ({len(audit_rows)} rows)')
    
    if audit_rows:
        fieldnames = list(audit_rows[0].keys())
        with open(OUT_CSV, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(audit_rows)
        print(f'[F5 AUDIT] Written: {OUT_CSV}')
    
    # Summary
    by_eligibility = {}
    for r in audit_rows:
        s = r.get('eligibilityStatus', 'unknown')
        by_eligibility[s] = by_eligibility.get(s, 0) + 1
    for s, c in sorted(by_eligibility.items()):
        print(f'  {s}: {c} rows')

if __name__ == '__main__':
    main()
