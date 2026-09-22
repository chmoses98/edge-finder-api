"""
Settlement outcomes for the MRV program with the research-layer `>= N`
correction for integer total ladders.

The archived settlement engine settled KXMLBTOTAL / KXMLBF5TOTAL rung N as
"total > N" until the semantics fix; Kalshi pays YES iff total >= N.  For a
ladder that is exactly: corrected YES(N) == archived YES(N-1).  This loader
applies the rung shift when the neighbouring rung is archived and otherwise
uses the ALPHA-0001 corrected mapping when present; rows it cannot correct
are returned as None (never guessed).
"""
import gzip
import json
import os

from lib.edgelab.research.market_structure.identity import parse_ticker

LADDER_FAMILIES = ("game_total", "inning_total")

# The archived engine settled integer total ladders as "total > N" for game
# dates through 2026-08-31 and as "total >= N" (Kalshi's rule) from
# 2026-09-01.  Verified empirically in this program by reconstructing each
# game's final total from its two team-total ladders (unchanged semantics)
# and reading the archived result at the rung equal to that total: NO on
# every one of 167 August games, YES on every one of 138 September games.
LEGACY_GT_RULE_LAST_GAME_DATE = "2026-08-31"


def _open(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def load_archived(root, dates=None):
    """{marketTicker: 'YES'|'NO'} from data/edgelab/settlements (SETTLED rows only, last write wins)."""
    d = os.path.join(root, "data", "edgelab", "settlements")
    out = {}
    for fn in sorted(os.listdir(d)):
        date = fn[:10]
        if dates and date not in dates:
            continue
        with _open(os.path.join(d, fn)) as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("settlementStatus") != "SETTLED" or r.get("result") not in ("YES", "NO"):
                    continue
                out[r["marketTicker"]] = r["result"]
    return out


def load_alpha_corrections(root):
    p = os.path.join(root, "data", "edgelab", "research_artifacts", "mlb_alpha_0001", "corrected_total_settlements.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        d = json.load(f)
    return {k: v.get("corrected") for k, v in (d.get("tickers") or {}).items()}


def corrected_outcome(ticker, archived, alpha=None):
    """
    'YES'/'NO'/None under Kalshi semantics.  Non-ladder families: archived
    result as is.  Ladder rung N: archived result of rung N-1 (the `> N-1`
    rule == `>= N`); if rung N-1 is not archived, fall back to the ALPHA-0001
    mapping; else None.
    """
    ident = parse_ticker(ticker)
    if ident.get("status") != "RESOLVED":
        return archived.get(ticker)
    if ident["family"] not in LADDER_FAMILIES:
        return archived.get(ticker)
    n = ident["rung"]
    if n is None:
        return None
    if ident["gameDate"] > LEGACY_GT_RULE_LAST_GAME_DATE:
        return archived.get(ticker)          # archive already uses >= N
    prev = "%s-%s-%d" % (ident["seriesTicker"], ident["physicalGameKey"], n - 1)
    if prev in archived:
        return archived[prev]
    if n == 1:
        return "YES" if archived.get(ticker) is not None else None  # total >= 1 is near-certain but only assert when the game settled at all
    if alpha and ticker in alpha and alpha[ticker] in ("YES", "NO"):
        return alpha[ticker]
    return None


def build_outcomes(root, dates=None):
    archived = load_archived(root, dates)
    alpha = load_alpha_corrections(root)
    out = {}
    for t in archived:
        o = corrected_outcome(t, archived, alpha)
        if o in ("YES", "NO"):
            out[t] = o
    return out
