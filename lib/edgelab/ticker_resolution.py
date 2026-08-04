"""
lib/edgelab/ticker_resolution.py
====================================
Timestamp-Optional Manual Imports milestone: resolves a marketTicker for
a bulk-imported wager row that didn't already supply one, by matching
the archived market corpus (data/edgelab/games/<date>.jsonl +
data/edgelab/markets/<date>.jsonl) on game + market family + horizon +
threshold + participant/team + side.

Deliberately conservative: an ambiguous match (more than one candidate
ticker survives every filter the caller actually supplied) is REFUSED,
never silently resolved to "the first one" -- the bulk importer must
then either return an unresolved receipt or ask the caller to supply the
exact ticker directly. A match with zero candidates is equally explicit
(NOT_FOUND), never a guess.
"""

RESOLVED = "RESOLVED"
AMBIGUOUS = "AMBIGUOUS"
NOT_FOUND = "NOT_FOUND"


def _matches_game(market, games_by_id, away=None, home=None):
    game = games_by_id.get(market.get("gameId"))
    if game is None:
        return False
    if away and game.get("awayTeam") != away:
        return False
    if home and game.get("homeTeam") != home:
        return False
    return True


def resolve_ticker(
    markets, games,
    *, away=None, home=None, market_family=None, market_horizon=None,
    team=None, player=None, threshold=None, outcome_label=None,
):
    """
    Pure. `markets`/`games` are already-loaded lists of that date's Market/
    Game dimension records (data/edgelab/markets/<date>.jsonl and
    data/edgelab/games/<date>.jsonl -- see lib.edgelab.storage). Filters
    are applied only when the caller actually supplies them (None means
    "don't filter on this"); at least one of away/home/market_family must
    be given, or every market for the date is a "candidate" and this
    function refuses as AMBIGUOUS rather than pretend that's a real
    match attempt.

    Returns (marketTicker_or_None, status, candidate_tickers).
    status is one of RESOLVED/AMBIGUOUS/NOT_FOUND. candidate_tickers is
    always the full surviving candidate list (length 1 for RESOLVED,
    0 for NOT_FOUND, 2+ for AMBIGUOUS) so a caller can show the user what
    it found instead of just failing silently.
    """
    if not any([away, home, market_family]):
        return None, AMBIGUOUS, []

    games_by_id = {g["gameId"]: g for g in games}
    candidates = []
    for m in markets:
        if not _matches_game(m, games_by_id, away=away, home=home):
            continue
        if market_family and m.get("marketFamily") != market_family:
            continue
        if market_horizon and m.get("marketHorizon") != market_horizon:
            continue
        if team and m.get("team") != team:
            continue
        if player and m.get("player") != player:
            continue
        if threshold is not None and m.get("threshold") != threshold:
            continue
        if outcome_label and m.get("outcomeLabel") != outcome_label:
            continue
        candidates.append(m["marketTicker"])

    candidates = sorted(set(candidates))
    if not candidates:
        return None, NOT_FOUND, []
    if len(candidates) > 1:
        return None, AMBIGUOUS, candidates
    return candidates[0], RESOLVED, candidates
