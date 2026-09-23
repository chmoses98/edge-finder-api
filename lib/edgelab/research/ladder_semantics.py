"""
lib/edgelab/research/ladder_semantics.py
========================================
Two archive facts every research consumer of Kalshi quotes and settlements
must honour, in one standard-library module so no consumer re-derives them:

1. INTEGER TOTAL-LADDER SETTLEMENT RULE IS DATE-DEPENDENT IN THE ARCHIVE.
   Kalshi pays KXMLBTOTAL / KXMLBF5TOTAL rung N iff total >= N.  The
   archived settlement engine used "total > N" for game dates through
   2026-08-31 and ">= N" from 2026-09-01 (docs/EDGELAB_KALSHI_TOTAL_LADDER_SEMANTICS.md
   fixed the engine; historical rows were deliberately not rewritten).
   Verified empirically in the MRV program by reconstructing each game's
   final total from its two team-total ladders (whose semantics never
   changed) and reading the archived game-total result at the rung equal to
   that total: NO on 167/167 August games, YES on 138/138 September games.
   For a legacy-rule row the correction is exact: corrected(N) = archived(N-1).

2. OBSERVATION-ARCHIVE QUOTE UNITS CHANGED ON 2026-09-10.  yesBid/yesAsk/
   noBid/noAsk/lastPrice in data/edgelab/observations are integer-ish CENTS
   (47.0) on rows captured before 2026-09-10 and DOLLARS (0.47) from that
   date, under the same field names.  Magnitude decides: a value above 1 is
   cents, below 1 is dollars, exactly 1.0 is ambiguous (1c or 100c, neither
   executable) and is refused.

Nothing here touches production settlement (lib.edgelab.settlement) or any
ledger; these are read-side research corrections.
"""
import re

LEGACY_GT_RULE_LAST_GAME_DATE = "2026-08-31"
OBSERVATION_DOLLARS_FROM_DATE = "2026-09-10"
LADDER_SERIES = ("KXMLBTOTAL", "KXMLBF5TOTAL")

_LADDER_RE = re.compile(r"^(?P<series>KXMLBTOTAL|KXMLBF5TOTAL)-(?P<event>(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})\d{4}[A-Z]+?(?:G\d)?)-(?P<rung>\d+)$")
_MON = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))}


def parse_ladder_ticker(ticker):
    """-> (series, event_block, rung, game_date 'YYYY-MM-DD') or None for a non-ladder ticker."""
    if not isinstance(ticker, str):
        return None
    m = _LADDER_RE.match(ticker)
    if not m:
        return None
    mon = _MON.get(m.group("mon"))
    if mon is None:
        return None
    game_date = "%04d-%02d-%02d" % (2000 + int(m.group("yy")), mon, int(m.group("dd")))
    return m.group("series"), m.group("event"), int(m.group("rung")), game_date


def ladder_rule_for_game_date(game_date):
    """'GT' (archived as total > N) or 'GE' (archived as total >= N, Kalshi's rule)."""
    return "GT" if game_date <= LEGACY_GT_RULE_LAST_GAME_DATE else "GE"


def corrected_ladder_outcome(ticker, archived_lookup):
    """
    Kalshi-rule outcome ('YES'/'NO'/None) for any settled ticker.

    archived_lookup: mapping or callable ticker -> 'YES'|'NO'|None from the
    archived settlement engine.  Non-ladder tickers pass through unchanged.
    Ladder rung N on a legacy-rule game date returns archived(N-1) and None
    when that rung is not archived (never guessed).  Ladder rows on GE-rule
    dates pass through unchanged.
    """
    get = archived_lookup.get if hasattr(archived_lookup, "get") else archived_lookup
    parsed = parse_ladder_ticker(ticker)
    if parsed is None:
        return get(ticker)
    series, event, rung, game_date = parsed
    if ladder_rule_for_game_date(game_date) == "GE":
        return get(ticker)
    if rung <= 0:
        return None
    return get("%s-%s-%d" % (series, event, rung - 1))


def observation_quote_cents(value):
    """Observation-archive quote -> integer cents (1..99) or None (absent, ambiguous 1.0, or off-range)."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v > 1.0:
        c = int(round(v))
    elif v < 1.0:
        c = int(round(v * 100.0))
    else:
        return None
    return c if 0 < c < 100 else None
