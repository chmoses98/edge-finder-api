#!/usr/bin/env python3
"""
scripts/clv_from_snapshot.py
=============================
CLV from archived Kalshi registry snapshots.

This is the PRIMARY CLV source for the post-slate review.  It reads the
archived kalshi_search_YYYY-MM-DD.json snapshots from
data/kalshi_registry_snapshots/ — files that were written BEFORE games
started by the fetch-slate workflow — and extracts pre-game prices for
every bet by exact market_ticker match.

Fallback hierarchy (per MODEL_CORE Section 4 / CLV pipeline spec):
  A. Best source:   exact ticker price from snapshot closest to scheduled
                    first pitch, taken BEFORE first pitch.
  B. If unavailable: exact ticker from any valid pre-start snapshot for
                    the same date.
  C. If unavailable: exact ticker from kalshi_market_registry.json closing_
                    snapshots (populated by capture_closing_lines.py).
  D. If unavailable: FAIL_NO_CANDLE — mark unavailable with reason.
     (never call Kalshi live API from this module)

Guarantees:
  - Only pre-start snapshot prices are used.  Any snapshot taken after the
    game's scheduledStartTime is rejected for that specific bet.
  - Prices are read from exact market_ticker matches only — no fuzzy
    side inference.
  - No API calls.  Pure file I/O.
  - Idempotent: running twice does not change CLV results.

Writes to bets.json (root) only; data/bets.json is a separate ledger and
must be synced manually after review.

CLV formula (consistent with fetch_kalshi_clv_v2.py):
  entry_implied = american_to_implied(entry_american_price)
  closing_implied = mid from snapshot  (YES-side for YES-side bets)
  clv_pp = (entry_implied - closing_implied) * 100   [percentage points]
  Positive CLV = we bought cheaper than the market closed.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPTS_DIR   = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR      = os.path.dirname(SCRIPTS_DIR)
SNAPSHOT_DIR  = os.path.join(ROOT_DIR, "data", "kalshi_registry_snapshots")
REGISTRY_PATH = os.path.join(ROOT_DIR, "data", "kalshi_market_registry.json")
BETS_PATH     = os.path.join(ROOT_DIR, "bets.json")

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_ts(ts_str):
    """Parse ISO timestamp → Unix epoch (int).  Returns None on failure."""
    if not ts_str:
        return None
    try:
        s = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def american_to_implied(odds):
    """Convert American odds → implied probability [0,1].  Returns None on error."""
    try:
        o = float(odds)
        if o >= 0:
            return 100.0 / (o + 100.0)
        else:
            return abs(o) / (abs(o) + 100.0)
    except Exception:
        return None


def implied_to_american(prob):
    """Convert implied probability [0,1] → American odds (int).  Returns None on error."""
    try:
        p = float(prob)
        if p <= 0 or p >= 1:
            return None
        if p >= 0.5:
            return int(round(-(p / (1 - p)) * 100))
        else:
            return int(round(((1 - p) / p) * 100))
    except Exception:
        return None


def is_yes_side(ticker, market_type, side_hint=None):
    """
    Determine whether this bet is on the YES side of the Kalshi market.

    For moneyline/F5: YES = the team indicated by the ticker suffix wins.
    For NRFI: YES = YRFI (a run scores); the bet is on YES only if market==YRFI.
    For NRFI bet: bet is on NO side (NRFI = no run = NO on the RFI market).
    For TT/total/spread: YES = over/favourite depending on ticker suffix; we use
    the entry price sign to disambiguate if needed, but for this pipeline we always
    store YES-side closing prob and flip it in calculate_clv.

    Returns True if the bet is on YES side, False if NO side.
    """
    mt = (market_type or "").upper()
    if mt == "NRFI":
        return False   # NRFI = NO on Kalshi RFI market (YES = YRFI)
    if mt == "YRFI":
        return True
    # For ML, F5 ML, RL, TT, Total — YES = the side named in the ticker suffix.
    # Since the ticker IS the market for that side, we are always on the YES side
    # of our specific market ticker.
    return True


def calculate_clv(entry_american, closing_implied_yes, bet_is_yes):
    """
    Calculate CLV in percentage points.

    CLV = entry_implied − closing_implied
    Positive → we got a better price than market close (positive edge vs close).
    Negative → market moved against us (closed at worse value than we bought).
    """
    entry_implied = american_to_implied(entry_american)
    if entry_implied is None or closing_implied_yes is None:
        return None

    if bet_is_yes:
        closing_implied = closing_implied_yes
    else:
        closing_implied = 1.0 - closing_implied_yes

    clv_pp = round((entry_implied - closing_implied) * 100, 2)
    return clv_pp


# ── Snapshot loading ──────────────────────────────────────────────────────────

def load_snapshot(date_str, snapshot_dir=None):
    """
    Load the kalshi_search snapshot for a given date.

    Args:
        date_str:     YYYY-MM-DD
        snapshot_dir: override directory for snapshot files (default: SNAPSHOT_DIR).
                      Useful in tests that write temp snapshots.

    Returns: (list_of_market_dicts, snapshot_ts_str, snapshot_path)
    Raises: FileNotFoundError if no snapshot found.
    """
    snap_dir = snapshot_dir or SNAPSHOT_DIR
    path = os.path.join(snap_dir, f"kalshi_search_{date_str}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No snapshot for {date_str}: {path}")
    with open(path) as f:
        snap = json.load(f)

    snapshot_ts = snap.get("fetched_at") or snap.get("snapshot_ts") or ""
    markets_raw = snap.get("markets", [])

    # Normalise: the snapshot stores markets as a list of dicts with 'market_ticker'
    if isinstance(markets_raw, list):
        markets = markets_raw
    elif isinstance(markets_raw, dict):
        # Older format: dict keyed by ticker
        markets = list(markets_raw.values())
    else:
        markets = []

    return markets, snapshot_ts, path


def build_ticker_index(markets):
    """Build {market_ticker: market_dict} from a markets list."""
    idx = {}
    for m in markets:
        t = m.get("market_ticker") or m.get("ticker")
        if t:
            idx[t] = m
    return idx


def get_mid_from_entry(entry):
    """Extract mid-point probability from a market entry dict."""
    mid = entry.get("mid")
    if mid is not None:
        try:
            m = float(mid)
            if 0 < m < 1:
                return m
        except Exception:
            pass

    # Fall back to bid/ask average
    bid_raw = entry.get("yes_bid")
    ask_raw = entry.get("yes_ask")
    try:
        bid = float(bid_raw) if bid_raw is not None else None
        ask = float(ask_raw) if ask_raw is not None else None
    except Exception:
        bid = ask = None

    if bid is not None and ask is not None:
        return round((bid + ask) / 2.0, 4)
    if bid is not None:
        return bid
    if ask is not None:
        return ask

    # Last resort: last_price
    lp = entry.get("last_price")
    if lp is not None:
        try:
            p = float(lp)
            if 0 < p < 1:
                return p
        except Exception:
            pass

    return None


def get_ask_from_entry(entry):
    """
    Extract the executable YES-side ask probability from a market entry —
    the price we'd actually have to pay to buy this exact contract, as
    distinct from the midpoint (used only for research/calibration, never
    as "the price we could have transacted at").
    """
    ask_raw = entry.get("yes_ask")
    try:
        ask = float(ask_raw) if ask_raw is not None else None
    except Exception:
        ask = None
    if ask is not None and 0 < ask < 1:
        return ask
    return None


# ── Registry official_closing_snapshot (highest-priority source) ────────────

def load_registry_official_snapshot(ticker):
    """
    Path A.0 (highest priority): every game's official_closing_snapshot,
    set by scripts/capture_pregame_closing_lines.py. By construction this
    is always PRE_START and the closest available snapshot to scheduled
    first pitch — never a late/post-start snapshot — so if it contains
    this exact ticker, it is used before anything else.

    Returns: (mid_prob, ask_prob, snapshot_ts_str, source_label)
             or (None, None, None, None)
    """
    if not ticker or not os.path.exists(REGISTRY_PATH):
        return None, None, None, None
    try:
        with open(REGISTRY_PATH) as f:
            reg_doc = json.load(f)
    except Exception:
        return None, None, None, None

    registry = reg_doc.get("registry", {})
    for kalshi_key, entry in registry.items():
        official = entry.get("official_closing_snapshot")
        if not official:
            continue
        prices = official.get("prices", {}) or {}
        pb = (prices.get("by_ticker") or {}).get(ticker)
        if pb and pb.get("mid") is not None:
            snap_ts_str = official.get("snapshot_ts", "")
            ask = pb.get("yes_ask")
            ask_prob = float(ask) if isinstance(ask, (int, float)) and 0 < ask < 1 else None
            return (float(pb["mid"]), ask_prob, snap_ts_str,
                    f"official_closing_snapshot:{kalshi_key}@{snap_ts_str}")
    return None, None, None, None


def _find_ticker_in_snapshot_prices(prices, ticker):
    """Search a single snapshot's prices dict for an exact ticker match.
    Supports both the flat 'by_ticker' index (new format, written by
    capture_pregame_closing_lines.py) and the older nested
    market-type/side-key shape (capture_closing_lines.py snapshot mode)."""
    by_ticker = prices.get("by_ticker")
    if by_ticker and ticker in by_ticker:
        return by_ticker[ticker]
    for mkt_type, mkt_data in prices.items():
        if mkt_type == "by_ticker" or not isinstance(mkt_data, dict):
            continue
        for side_key in ("away", "home", "tie", "yes", "yrfi", "nrfi"):
            cand = mkt_data.get(side_key)
            if isinstance(cand, dict):
                t = cand.get("ticker") or cand.get("market_ticker")
                if t == ticker:
                    return cand
        for line in (mkt_data.get("lines") or []):
            if line.get("ticker") == ticker:
                return line
    return None


# ── Registry closing_snapshots fallback ───────────────────────────────────────

def load_registry_closing_snapshots(ticker, scheduled_start_ts):
    """
    Fallback (C): search kalshi_market_registry.json closing_snapshots for
    a pre-game price for the given ticker — the CLOSEST pre-start snapshot
    to scheduled first pitch, never a post-start ("late") one.

    Returns: (mid_prob, ask_prob, snapshot_ts_str, source_label)
             or (None, None, None, None)
    """
    if not os.path.exists(REGISTRY_PATH):
        return None, None, None, None
    try:
        with open(REGISTRY_PATH) as f:
            reg_doc = json.load(f)
    except Exception:
        return None, None, None, None

    registry = reg_doc.get("registry", {})
    best = None  # (snap_ts, mid, ask, snap_ts_str)
    for kalshi_key, entry in registry.items():
        for snap in entry.get("closing_snapshots", []):
            snap_ts_str = snap.get("snapshot_ts", "")
            snap_ts = parse_ts(snap_ts_str)
            if snap_ts and scheduled_start_ts and snap_ts >= scheduled_start_ts:
                continue  # post-start ("late") snapshot — never a valid closing line
            mkt_entry = _find_ticker_in_snapshot_prices(snap.get("prices", {}) or {}, ticker)
            mid = mkt_entry.get("mid") if mkt_entry else None
            if mid is None:
                continue
            ask = mkt_entry.get("yes_ask") if mkt_entry else None
            ask = float(ask) if isinstance(ask, (int, float)) and 0 < ask < 1 else None
            if best is None or (snap_ts and snap_ts > best[0]):
                best = (snap_ts or parse_ts("1970-01-01T00:00:00Z"), float(mid), ask, snap_ts_str)

    if best:
        return best[1], best[2], best[3], f"registry_closing_snapshot:{best[3]}"
    return None, None, None, None


def load_registry_late_snapshot(ticker, scheduled_start_ts):
    """
    Detect a post-start ("late") snapshot for this ticker, purely so callers
    can distinguish "no data ever captured" (FAIL_NO_SNAPSHOT_PRICE) from
    "only a late snapshot exists" (LATE_ONLY) — a late price is NEVER
    returned as usable CLV data by this function; it is detection-only.

    Returns True if a late snapshot with a price for this ticker exists.
    """
    if not ticker or not os.path.exists(REGISTRY_PATH):
        return False
    try:
        with open(REGISTRY_PATH) as f:
            reg_doc = json.load(f)
    except Exception:
        return False

    registry = reg_doc.get("registry", {})
    for kalshi_key, entry in registry.items():
        for snap in entry.get("closing_snapshots", []):
            snap_ts = parse_ts(snap.get("snapshot_ts", ""))
            is_late = snap.get("capture_timing") == "LATE" or (
                snap_ts and scheduled_start_ts and snap_ts >= scheduled_start_ts
            )
            if not is_late:
                continue
            mkt_entry = _find_ticker_in_snapshot_prices(snap.get("prices", {}) or {}, ticker)
            if mkt_entry and mkt_entry.get("mid") is not None:
                return True
    return False


# ── Core per-bet CLV resolver ─────────────────────────────────────────────────

def resolve_clv_for_bet(bet, ticker_index, snapshot_ts_str, snapshot_path,
                        scheduled_start_ts, snapshot_dir=None):
    """
    Resolve CLV for a single bet using the pre-loaded snapshot index.

    Args:
        bet:                 bet dict (must have marketTicker / ticker, scheduledStartTime,
                             betTimeLine / price, market)
        ticker_index:        {market_ticker: market_dict} from the date's snapshot
        snapshot_ts_str:     ISO timestamp of the snapshot
        snapshot_path:       path used (for audit trail)
        scheduled_start_ts:  Unix epoch of first pitch

    Returns: dict with CLV fields merged onto bet.
    """
    updated = dict(bet)

    ticker = bet.get("marketTicker") or bet.get("ticker")
    entry_price = bet.get("betTimeLine") or bet.get("price")
    market_type = bet.get("market", "")
    bet_is_yes = is_yes_side(ticker, market_type)

    if not ticker:
        updated["clvStatus"] = "FAIL_NO_TICKER"
        updated["clvError"] = "marketTicker missing"
        updated["clv"] = None
        updated["closingPrice"] = None
        updated["closingTimestamp"] = None
        updated["clvSource"] = None
        return updated

    if not scheduled_start_ts:
        updated["clvStatus"] = "FAIL_NO_TIMESTAMP"
        updated["clvError"] = "scheduledStartTime missing or unparseable"
        updated["clv"] = None
        updated["closingPrice"] = None
        updated["closingTimestamp"] = None
        updated["clvSource"] = None
        return updated

    ask_prob = None

    # ── Path A.0: registry official_closing_snapshot (highest priority) ──────
    # Set by scripts/capture_pregame_closing_lines.py — always PRE_START and
    # the closest available snapshot to first pitch by construction. Checked
    # before the kalshi_search archive (Path A) precisely because it is a
    # more precise, purpose-built source than an incidental archive file.
    mid_prob, ask_prob, official_snap_ts, official_source = load_registry_official_snapshot(ticker)
    if mid_prob is not None:
        source_label = official_source
        snapshot_ts_str = official_snap_ts or snapshot_ts_str

    # ── Path A: snapshot closest to first pitch ───────────────────────────────
    if mid_prob is None:
        snap_ts = parse_ts(snapshot_ts_str)
        if snap_ts and snap_ts > scheduled_start_ts:
            # This snapshot was taken AFTER first pitch — invalid for this bet
            # Try path B/C below
            mid_prob = None
            source_label = None
        else:
            entry = ticker_index.get(ticker)
            if entry:
                mid_prob = get_mid_from_entry(entry)
                ask_prob = get_ask_from_entry(entry)
                source_label = (
                    f"kalshi_registry_snapshot:{os.path.basename(snapshot_path)}"
                    f"@{snapshot_ts_str}"
                )
            else:
                mid_prob = None
                source_label = None

    # ── Path B: any other pre-start snapshot for same date ────────────────────
    if mid_prob is None:
        date_str = (bet.get("date") or "")[:10]
        snap_dir = snapshot_dir or SNAPSHOT_DIR
        if date_str:
            all_snaps = sorted(Path(snap_dir).glob(f"kalshi_search_{date_str}*.json"))
            for snap_file in all_snaps:
                try:
                    with open(snap_file) as f:
                        s = json.load(f)
                    s_ts_str = s.get("fetched_at") or s.get("snapshot_ts") or ""
                    s_ts = parse_ts(s_ts_str)
                    if s_ts and s_ts > scheduled_start_ts:
                        continue  # post-game
                    alt_markets = s.get("markets", [])
                    if isinstance(alt_markets, dict):
                        alt_markets = list(alt_markets.values())
                    alt_index = build_ticker_index(alt_markets)
                    entry = alt_index.get(ticker)
                    if entry:
                        mid_prob = get_mid_from_entry(entry)
                        if mid_prob is not None:
                            ask_prob = get_ask_from_entry(entry)
                            source_label = (
                                f"kalshi_registry_snapshot:{snap_file.name}"
                                f"@{s_ts_str}"
                            )
                            snapshot_ts_str = s_ts_str
                            break
                except Exception:
                    continue

    # ── Path C: registry closing_snapshots ───────────────────────────────────
    if mid_prob is None:
        mid_prob, ask_prob, reg_snap_ts, reg_source = load_registry_closing_snapshots(
            ticker, scheduled_start_ts
        )
        if mid_prob is not None:
            source_label = reg_source
            snapshot_ts_str = reg_snap_ts or ""

    # ── Path D: unavailable — distinguish LATE_ONLY from no-data-at-all ──────
    if mid_prob is None:
        if load_registry_late_snapshot(ticker, scheduled_start_ts):
            updated["clvStatus"] = "LATE_ONLY"
            updated["clvCaptureStatus"] = "LATE_ONLY"
            updated["clvError"] = (
                f"Only a post-first-pitch (LATE) snapshot exists for ticker={ticker}. "
                "A late snapshot is never used as a valid closing line — no pre-start "
                "price was captured, so CLV cannot be computed for this bet."
            )
            updated["closingLineUnavailableReason"] = updated["clvError"]
        else:
            updated["clvStatus"] = "FAIL_NO_SNAPSHOT_PRICE"
            updated["clvError"] = (
                f"No pre-start price found in any snapshot for ticker={ticker}. "
                "Kalshi live API not consulted (by design). "
                "Run fetch-slate to generate future snapshots."
            )
        updated["clv"] = None
        updated["closingLine"] = None
        updated["closingPrice"] = None
        updated["closingPriceAmerican"] = None
        updated["closingImpliedPct"] = None
        updated["closingTimestamp"] = None
        updated["clvSource"] = None
        return updated

    # ── Compute CLV ───────────────────────────────────────────────────────────
    clv_pp = calculate_clv(entry_price, mid_prob, bet_is_yes)

    if clv_pp is None:
        updated["clvStatus"] = "FAIL_CALC_ERROR"
        updated["clvError"] = (
            f"CLV calculation failed: entry_price={entry_price!r}, "
            f"closing_mid={mid_prob!r}, bet_is_yes={bet_is_yes}"
        )
        updated["clv"] = None
        updated["closingPrice"] = mid_prob
        updated["closingTimestamp"] = snapshot_ts_str
        updated["clvSource"] = source_label
        return updated

    closing_for_side = mid_prob if bet_is_yes else (1.0 - mid_prob)
    closing_american = implied_to_american(closing_for_side)
    closing_pct = round(closing_for_side * 100, 2)

    entry_pct = round(american_to_implied(entry_price) * 100, 2) if american_to_implied(entry_price) is not None else None
    closing_ask_pct = None
    if ask_prob is not None:
        closing_ask_for_side = ask_prob if bet_is_yes else (1.0 - ask_prob)
        closing_ask_pct = round(closing_ask_for_side * 100, 2)

    updated["closingPrice"] = mid_prob            # always YES-side probability
    updated["closingPriceAmerican"] = closing_american
    updated["closingImpliedPct"] = closing_pct
    updated["closingMidPct"] = closing_pct         # midpoint, our side — research/calibration only
    updated["closingAskPct"] = closing_ask_pct     # executable ask, our side — the contract we bought
    updated["closingLine"] = closing_ask_pct if closing_ask_pct is not None else closing_pct
    updated["closingTimestamp"] = snapshot_ts_str
    updated["clv"] = clv_pp
    # Positive = the contract became MORE expensive after entry.
    if entry_pct is not None:
        updated["clvMidPct"] = round(closing_pct - entry_pct, 2)
        if closing_ask_pct is not None:
            updated["clvAskPct"] = round(closing_ask_pct - entry_pct, 2)
    updated["clvStatus"] = "OK"
    updated["clvCaptureStatus"] = "OK"
    updated["closingLineUnavailableReason"] = None
    updated["clvSource"] = source_label
    updated["clvError"] = None
    return updated


# ── Main entry point ──────────────────────────────────────────────────────────

def run_snapshot_clv(date_str, bets_path=None, write=False, dry_run=False,
                     snapshot_dir=None):
    """
    Run CLV resolution from snapshots for all real-money bets on date_str.

    Args:
        date_str:      YYYY-MM-DD
        bets_path:     path to bets.json (defaults to ROOT_DIR/bets.json)
        write:         if True, write results back to bets_path
        dry_run:       if True, print results but do not write
        snapshot_dir:  override snapshot directory (default: SNAPSHOT_DIR).
                       Pass a temp dir in tests that create their own snapshots.

    Returns: (results_dict, summary_dict)
    """
    path = bets_path or BETS_PATH

    # Load snapshot
    try:
        markets, snapshot_ts_str, snapshot_path = load_snapshot(date_str, snapshot_dir=snapshot_dir)
    except FileNotFoundError as e:
        print(f"WARNING: {e}")
        print("No snapshot available — CLV will be marked FAIL_NO_SNAPSHOT_PRICE.")
        markets, snapshot_ts_str, snapshot_path = [], "", ""

    ticker_index = build_ticker_index(markets)
    print(f"[snapshot_clv] date={date_str}  snapshot={os.path.basename(snapshot_path) or 'NONE'}"
          f"  fetched_at={snapshot_ts_str}  tickers={len(ticker_index)}")

    # Load bets
    with open(path) as f:
        bets = json.load(f)

    # Target: real-money bets on date_str without valid CLV
    SETTLED_STATUSES = {"settled", "SETTLED", "WIN", "LOSS", "win", "loss", "PUSH", "push"}
    SUPPORTED_MARKETS = {"ML", "F5 ML", "F5", "Run Line", "RL", "Total",
                         "Team Total", "TT", "NRFI", "YRFI"}

    # ── Paper detection (consistent with clv_update.py rebuild_log) ────────
    def _is_paper(b):
        if b.get("betType") == "PAPER" or b.get("type") == "paper":
            return True
        conf = str(b.get("confidence", "") or "").strip().title()
        if conf == "Low":
            conf = "Paper"
        if conf == "Paper":
            return True
        if str(b.get("conf", "") or "").upper() == "PAPER":
            return True
        if str(b.get("status", "") or "").upper() == "PAPER":
            return True
        return False

    def _is_real(b):
        return b.get("betType") == "REAL" or b.get("type") == "real"

    # Real-money CLV targets (unchanged behaviour, must have REAL signal)
    targets = [
        b for b in bets
        if (b.get("date") or "")[:10] == date_str
        and _is_real(b)
        and b.get("status") in SETTLED_STATUSES
        and (b.get("market", "") in SUPPORTED_MARKETS)
        and (b.get("clv") is None or b.get("clvStatus") in
             ("FAIL_NO_CANDLE", "FAIL_NO_SNAPSHOT_PRICE", "unavailable",
              "not_yet_captured", "FAIL_NO_TICKER", "FAIL_NO_TIMESTAMP"))
    ]

    # Paper CLV targets — same rules but paper bets are processed separately
    # so their CLV never contaminates real-money stats.
    # Paper bets require the same exact ticker matching; no post-start prices.
    paper_targets = [
        b for b in bets
        if (b.get("date") or "")[:10] == date_str
        and _is_paper(b)
        and b.get("status") in SETTLED_STATUSES
        and (b.get("market", "") in SUPPORTED_MARKETS)
        and (b.get("clv") is None or b.get("clvStatus") in
             ("FAIL_NO_CANDLE", "FAIL_NO_SNAPSHOT_PRICE", "unavailable",
              "not_yet_captured", "FAIL_NO_TICKER", "FAIL_NO_TIMESTAMP",
              "PAPER_PENDING"))
    ]

    print(f"[snapshot_clv] Real CLV targets:  {len(targets)} bets")
    print(f"[snapshot_clv] Paper CLV targets: {len(paper_targets)} bets")

    results = {}
    ok = fail_no_snap = fail_no_ticker = fail_other = 0

    for b in targets:
        bid = b.get("id") or b.get("marketTicker") or "?"
        fp_raw = b.get("scheduledStartTime")
        fp_ts  = parse_ts(fp_raw)

        updated = resolve_clv_for_bet(
            b, ticker_index, snapshot_ts_str, snapshot_path, fp_ts,
            snapshot_dir=snapshot_dir
        )
        results[bid] = updated

        status = updated.get("clvStatus", "?")
        if status == "OK":
            ok += 1
            clv_val = updated.get("clv")
            print(f"  ✅ {bid}  CLV={clv_val:+.2f}pp  closing={updated.get('closingPriceAmerican')}  src={updated.get('clvSource','')[:50]}")
        elif status == "FAIL_NO_TICKER":
            fail_no_ticker += 1
            print(f"  ❌ {bid}  {status}: {updated.get('clvError','')[:80]}")
        elif "NO_SNAPSHOT" in status or "NO_CANDLE" in status:
            fail_no_snap += 1
            print(f"  ⚠️  {bid}  {status}: {updated.get('clvError','')[:80]}")
        else:
            fail_other += 1
            print(f"  ❓ {bid}  {status}: {updated.get('clvError','')[:60]}")

    # ── Paper CLV processing (separate — does NOT affect real stats) ───────
    paper_results = {}
    paper_ok = paper_fail_ticker = paper_fail_snap = paper_fail_other = 0

    if paper_targets:
        print(f"\n[snapshot_clv] --- Paper CLV (excluded from real stats) ---")
        for b in paper_targets:
            bid = b.get("id") or b.get("marketTicker") or "?"
            fp_raw = b.get("scheduledStartTime")
            fp_ts  = parse_ts(fp_raw)

            updated = resolve_clv_for_bet(
                b, ticker_index, snapshot_ts_str, snapshot_path, fp_ts,
                snapshot_dir=snapshot_dir
            )
            # Tag paper CLV clearly so it cannot be confused with real CLV
            updated["paperClv"] = updated.get("clv")
            updated["paperClvStatus"] = updated.get("clvStatus")
            updated["paperClvSource"] = updated.get("clvSource")
            paper_results[bid] = updated

            status = updated.get("clvStatus", "?")
            if status == "OK":
                paper_ok += 1
                clv_val = updated.get("clv")
                print(f"  📄 {bid}  [PAPER] CLV={clv_val:+.2f}pp  closing={updated.get('closingPriceAmerican')}  src={updated.get('clvSource','')[:40]}")
            elif status == "FAIL_NO_TICKER":
                paper_fail_ticker += 1
                print(f"  📄❌ {bid}  [PAPER] {status}: {updated.get('clvError','')[:70]}")
            elif "NO_SNAPSHOT" in status or "NO_CANDLE" in status:
                paper_fail_snap += 1
                print(f"  📄⚠️  {bid}  [PAPER] {status}")
            else:
                paper_fail_other += 1
                print(f"  📄❓ {bid}  [PAPER] {status}")

    summary = {
        "date": date_str,
        "snapshot": os.path.basename(snapshot_path) or "NONE",
        "snapshot_ts": snapshot_ts_str,
        # Real-money stats
        "targets": len(targets),
        "clv_ok": ok,
        "fail_no_ticker": fail_no_ticker,
        "fail_no_snapshot": fail_no_snap,
        "fail_other": fail_other,
        "coverage_pct": round(ok / len(targets) * 100, 1) if targets else 0.0,
        # Paper stats (separate — never mixed with real)
        "paper_targets": len(paper_targets),
        "paper_clv_ok": paper_ok,
        "paper_fail_no_ticker": paper_fail_ticker,
        "paper_fail_no_snapshot": paper_fail_snap,
        "paper_fail_other": paper_fail_other,
        "paper_coverage_pct": round(paper_ok / len(paper_targets) * 100, 1) if paper_targets else 0.0,
    }

    print(f"\n[snapshot_clv] REAL SUMMARY:  {ok}/{len(targets)} resolved  "
          f"({summary['coverage_pct']}% coverage)")
    if paper_targets:
        print(f"[snapshot_clv] PAPER SUMMARY: {paper_ok}/{len(paper_targets)} resolved  "
              f"({summary['paper_coverage_pct']}% coverage) — excluded from real stats")

    # Merge all results (real + paper) back into bets
    all_results = {**results, **paper_results}

    if not dry_run and write and all_results:
        # Merge results back
        bet_map = {(b.get("id") or b.get("marketTicker")): b for b in bets}
        for bid, updated in all_results.items():
            if bid in bet_map:
                bet_map[bid] = updated
        updated_bets = [bet_map.get(b.get("id") or b.get("marketTicker"), b) for b in bets]
        with open(path, "w") as f:
            json.dump(updated_bets, f, indent=2)
        print(f"[snapshot_clv] Wrote {len(updated_bets)} bets → {path}")

    return results, summary


# ── CLI entry ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    date = sys.argv[1] if len(sys.argv) > 1 else ""
    if not date:
        print("Usage: python3 clv_from_snapshot.py YYYY-MM-DD [--write] [--dry-run]")
        sys.exit(1)
    write  = "--write" in sys.argv
    dry    = "--dry-run" in sys.argv
    results, summary = run_snapshot_clv(date, write=write, dry_run=dry)
    print("\nFinal summary:", json.dumps(summary, indent=2))
    sys.exit(0 if summary["fail_no_ticker"] == 0 else 1)
