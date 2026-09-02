"""
lib/edgelab/exchange_settlement.py
==================================
Kalshi's OWN settlement result, captured immutably and cross-checked
against this repository's canonical grade.

RESEARCH INFRASTRUCTURE. Pure functions only -- no network here, no
writes, and never an overwrite of either source.

WHY. This program found two grading defects that were invisible from
inside the system precisely because exchange truth was never stored: 1,512
KXMLBF5SPREAD contracts graded on the full-game margin, and every
integer-rung total contract mis-graded at its boundary. All 686,220
archived raw market records carry status="active", because the capture
path only ever requested OPEN markets.

CLASSIFICATION
    AGREE              canonical == exchange
    MISMATCH           both settled, results differ      <- the alarm case
    CANONICAL_MISSING  exchange settled, we have nothing
    EXCHANGE_MISSING   we settled, exchange row absent
    VOID_DISAGREEMENT  one side void/cancelled, the other graded

On MISMATCH or VOID_DISAGREEMENT the research row is QUARANTINED: it
alerts, it does not count toward any prospective checkpoint, and neither
source is silently rewritten.
"""

AGREE = "AGREE"
MISMATCH = "MISMATCH"
CANONICAL_MISSING = "CANONICAL_MISSING"
EXCHANGE_MISSING = "EXCHANGE_MISSING"
VOID_DISAGREEMENT = "VOID_DISAGREEMENT"

_VOIDISH = {"VOID", "CANCELLED", "CANCELED", "VOIDED"}
_GRADED = {"YES", "NO"}


def normalize_result(value):
    """Kalshi reports `yes`/`no`; the canonical store uses YES/NO. Anything
    else is returned upper-cased so a void/unknown state stays visible
    rather than being coerced into a grade."""
    if value is None:
        return None
    return str(value).strip().upper() or None


def classify(canonical_result, exchange_result):
    """Pure comparison of one ticker's two settlement opinions."""
    c, e = normalize_result(canonical_result), normalize_result(exchange_result)
    if c is None and e is None:
        return EXCHANGE_MISSING if c is None else CANONICAL_MISSING
    if c is None:
        return CANONICAL_MISSING
    if e is None:
        return EXCHANGE_MISSING
    if (c in _VOIDISH) != (e in _VOIDISH):
        return VOID_DISAGREEMENT
    if c in _VOIDISH and e in _VOIDISH:
        return AGREE
    if c in _GRADED and e in _GRADED:
        return AGREE if c == e else MISMATCH
    return MISMATCH if c != e else AGREE


def is_quarantined(classification):
    """A row whose two sources disagree cannot count toward research
    checkpoints until a human resolves it."""
    return classification in (MISMATCH, VOID_DISAGREEMENT)


def compare_settlements(canonical_by_ticker, exchange_by_ticker):
    """
    Join two {marketTicker: result} maps. Returns
    {ticker: {canonical, exchange, classification, quarantined}} over the
    UNION of tickers -- a ticker present on only one side is reported, never
    dropped.
    """
    out = {}
    for ticker in set(canonical_by_ticker) | set(exchange_by_ticker):
        c = canonical_by_ticker.get(ticker)
        e = exchange_by_ticker.get(ticker)
        cls = classify(c, e)
        out[ticker] = {
            "marketTicker": ticker,
            "canonicalResult": normalize_result(c),
            "exchangeResult": normalize_result(e),
            "classification": cls,
            "quarantined": is_quarantined(cls),
        }
    return out


def summarize(comparison):
    counts = {}
    for row in comparison.values():
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1
    total = len(comparison) or 1
    agree = counts.get(AGREE, 0)
    return {
        "tickersCompared": len(comparison),
        "counts": counts,
        "agreementRate": round(agree / total, 6),
        "quarantinedTickers": sorted(t for t, r in comparison.items() if r["quarantined"]),
        "quarantinedCount": sum(1 for r in comparison.values() if r["quarantined"]),
    }
