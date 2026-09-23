"""
Per-cycle reconciliation and completeness classification for the MRV
collector.  The arithmetic must close:

  marketsReceived = marketsArchived (full rows) + marketsReferenced (unchanged,
                    named in the cross-section) + marketsExcluded (with reason)
  unaccountedRows = marketsReceived - archived - referenced - excluded   (must be 0)

Classes (mirroring lib.edgelab.capture_completeness's principle: COMPLETE is
provable, not assumed):
  COMPLETE  every included series paginated to exhaustion, no fetch failure,
            unaccountedRows == 0, every eligible per-game book requested was
            received (booksFailed == 0)
  PARTIAL   any truncated series, fetch failure, failed book, or arithmetic gap
  FAILED    no market row received at all
Research-complete additionally requires zero family starvation across
eligible games (every core family present for every eligible pregame game
that Kalshi lists at all).
"""


def classify(recon):
    if recon["marketsReceived"] == 0:
        return "FAILED"
    ok = (recon["seriesIncomplete"] == 0 and recon["seriesFetchFailed"] == 0 and recon["unaccountedRows"] == 0
          and recon["booksFailed"] == 0 and recon["seriesListFetchOk"])
    return "COMPLETE" if ok else "PARTIAL"


def build(recon):
    """Fill derived fields and class; returns the same dict."""
    recon["unaccountedRows"] = (recon["marketsReceived"] - recon["marketsArchived"] - recon["marketsReferenced"]
                                - recon["marketsExcluded"])
    recon["captureClass"] = classify(recon)
    recon["researchComplete"] = recon["captureClass"] == "COMPLETE" and recon["familyStarvationCount"] == 0
    return recon


def family_starvation(eligible_games, markets_by_game, core_families):
    """
    eligible_games: iterable of game keys (the collector passes gamePk); markets_by_game: {game: {series: count}}.
    A game is 'starved' of a core family when it has markets in at least one
    core family but zero in another.  (A game Kalshi has not listed at all is
    reported separately as 'unlisted', not as starvation.)
    -> {"starved": [{game, missing:[series]}], "unlisted": [game...], "count": n}
    """
    starved, unlisted = [], []
    for g in eligible_games:
        fam = markets_by_game.get(g) or {}
        present = [s for s in core_families if fam.get(s)]
        if not present:
            unlisted.append(g)
            continue
        missing = [s for s in core_families if not fam.get(s)]
        if missing:
            starved.append({"game": g, "missing": missing})
    return {"starved": starved, "unlisted": unlisted, "count": len(starved)}
