"""
lib/edgelab/canonical_era.py
================================
Defines the boundary between legacy (pre-canonical-ledger) wager history
and the official canonical betting era. This is deliberately a single
constant plus a couple of pure filter helpers -- not a new schema field,
not a new ledger, not a migration of any existing row. No existing bet
row is added, modified, cancelled, reinterpreted, or moved by this
module; it only changes which already-stored rows are counted by
DEFAULT when producing an "official" (canonical-era) report, as opposed
to a full-history/legacy query, which remains available by passing
include_legacy=True at the call site.

The canonical betting era begins on CANONICAL_ERA_START_DATE. Every bet
whose date (gameDate, falling back to entryTimestamp's date component --
same convention as lib.edgelab.query._entry_date) is on or after that
date counts toward the official record; everything earlier is legacy/
archive-only and is excluded from official bankroll, win/loss record,
total risked/returned, ROI, CLV summaries, market-family performance,
cumulative postmortems, bankroll charts, and canonical-era analytics by
default. Legacy rows are never deleted, edited, or migrated -- they stay
exactly as they are in bets.jsonl and remain queryable via
lib.edgelab.query (by_date, by_date_range, etc.) or legacy_bets() below.
"""

CANONICAL_ERA_START_DATE = "2026-08-03"


def _entry_date(bet):
    """Prefer the explicit gameDate; fall back to entryTimestamp's date component. Mirrors lib.edgelab.query._entry_date exactly, duplicated here rather than imported to keep this module dependency-free and independently testable."""
    return bet.get("gameDate") or (bet.get("entryTimestamp") or "")[:10] or None


def is_canonical_era_date(date_str):
    """True if date_str (YYYY-MM-DD) is on or after CANONICAL_ERA_START_DATE. Plain ISO-date string comparison is valid lexicographic ordering for this format."""
    return bool(date_str) and date_str >= CANONICAL_ERA_START_DATE


def is_canonical_era(bet):
    """
    True if `bet` falls on or after CANONICAL_ERA_START_DATE. A bet with
    no resolvable date is treated as NOT canonical-era -- conservative by
    design, since a row with an unknown date should never be silently
    counted in an official total just because it wasn't excluded.
    """
    return is_canonical_era_date(_entry_date(bet))


def canonical_era_bets(bets):
    """Official-record bets only -- on or after the canonical era boundary. Never mutates or reorders the input list."""
    return [b for b in bets if is_canonical_era(b)]


def legacy_bets(bets):
    """Pre-canonical-era bets -- legacy/archive history only. Still fully queryable (see lib.edgelab.query), just never counted in an official report by default."""
    return [b for b in bets if not is_canonical_era(b)]
