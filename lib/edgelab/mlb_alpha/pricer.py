"""
lib/edgelab/mlb_alpha/pricer.py
===============================
Turn one SimResult into a YES probability for any parsed game-level contract.

Every probability comes from the SAME simulated games, so contradictions
between families (a team total above the game total, an F5 spread more likely
than the full-game spread it should be dominated by) cannot arise from model
disagreement. A contract the simulator cannot price returns status != PRICED
and p = None; nothing is approximated silently.

STATUS VALUES
  PRICED                      p is the simulated probability
  PRICED_RULE_UNVERIFIED      priced under the stated reading of the rules; the
                              exchange's wording for a corner case was not seen
  UNSUPPORTED                 needs information this simulator does not have
                              (player props), or a half-inning that may be unplayed
"""

from lib.edgelab.mlb_alpha import taxonomy as tx

PRICED = "PRICED"
PRICED_RULE_UNVERIFIED = "PRICED_RULE_UNVERIFIED"
UNSUPPORTED = "UNSUPPORTED"

_PERIOD_K = {"KXMLBF3": 3, "KXMLBF5": 5, "KXMLBF7": 7}


def _result(p, n, status, why=None):
    se = None
    if p is not None and n:
        se = (max(p * (1.0 - p), 0.0) / n) ** 0.5
    return {"p": None if p is None else float(p), "se": se, "status": status, "why": why}


def yes_vector(sim_result, parsed):
    """
    Boolean array (one entry per simulated game) of YES outcomes for `parsed`, plus status.
    Returns (vector | None, status, why). `probability` is its mean; the engine uses the vector
    itself to measure payoff correlation between contracts of the same game.
    """
    if not parsed or not str(parsed.get("parseStatus", "")).startswith("PARSED"):
        return None, UNSUPPORTED, "ticker did not parse: %s" % (parsed or {}).get("parseStatus")
    s = parsed["seriesTicker"]
    r = sim_result
    sel = parsed.get("selection")
    thr = parsed.get("threshold")

    def team_run_arrays(k=13):
        return r.away[:, :k].sum(axis=1), r.home[:, :k].sum(axis=1)

    if s == "KXMLBGAME":
        a, h = team_run_arrays()
        return (h > a) if sel == "HOME" else (a > h), PRICED, None
    if s in _PERIOD_K:
        a, h = team_run_arrays(_PERIOD_K[s])
        return {"HOME": h > a, "AWAY": a > h, "TIE": a == h}[sel], PRICED, None
    if s in ("KXMLBSPREAD", "KXMLBF5SPREAD"):
        a, h = team_run_arrays(5 if s == "KXMLBF5SPREAD" else 13)
        return ((h - a) if sel == "HOME" else (a - h)) >= thr, PRICED, None
    if s in ("KXMLBTOTAL", "KXMLBF5TOTAL"):
        a, h = team_run_arrays(5 if s == "KXMLBF5TOTAL" else 13)
        return (a + h) >= thr, PRICED, None
    if s == "KXMLBTEAMTOTAL":
        a, h = team_run_arrays()
        return (h if sel == "HOME" else a) >= thr, PRICED, None
    if s == "KXMLBRFI":
        return (r.away[:, 0] + r.home[:, 0]) >= 1, PRICED, None
    if s == "KXMLBEXTRAS":
        return r.extras, PRICED, None
    if s in ("KXMLBINNINGWIN", "KXMLBINNINGTOTAL"):
        i = parsed.get("inning")
        if not i or i < 1:
            return None, UNSUPPORTED, "no inning"
        if i >= 9:
            return None, UNSUPPORTED, "inning >= 9: unplayed/extra half-inning rules not observed"
        a, h = r.away[:, i - 1], r.home[:, i - 1]
        if s == "KXMLBINNINGTOTAL":
            return (a + h) >= thr, PRICED_RULE_UNVERIFIED, "assumes N means total runs in that inning >= N"
        return {"HOME": h > a, "AWAY": a > h, "TIE": a == h}[sel], PRICED_RULE_UNVERIFIED, "assumes a scoreless inning resolves TIE"
    cls = parsed.get("classification") or {}
    return None, UNSUPPORTED, "no simulator path for %s (%s)" % (s, cls.get("modelStatus"))


def probability(sim_result, parsed):
    """YES probability of `parsed` (output of taxonomy.parse_market_ticker) under `sim_result`."""
    vec, status, why = yes_vector(sim_result, parsed)
    if vec is None:
        return _result(None, 0, status, why)
    return _result(vec.mean(), sim_result.n, status, why)


def price_many(sim_result, tickers):
    """{ticker: probability dict} for an iterable of market tickers."""
    return {t: probability(sim_result, tx.parse_market_ticker(t)) for t in tickers}
