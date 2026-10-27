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


def probability(sim_result, parsed):
    """YES probability of `parsed` (output of taxonomy.parse_market_ticker) under `sim_result`."""
    if not parsed or not str(parsed.get("parseStatus", "")).startswith("PARSED"):
        return _result(None, 0, UNSUPPORTED, "ticker did not parse: %s" % (parsed or {}).get("parseStatus"))
    s = parsed["seriesTicker"]
    r = sim_result
    n = r.n
    sel = parsed.get("selection")
    thr = parsed.get("threshold")

    def team_run_arrays(k=13):
        a, h = r.away[:, :k].sum(axis=1), r.home[:, :k].sum(axis=1)
        return a, h

    if s == "KXMLBGAME":
        a, h = team_run_arrays()
        hw = h > a
        return _result((hw if sel == "HOME" else ~hw).mean(), n, PRICED)
    if s in _PERIOD_K:
        a, h = team_run_arrays(_PERIOD_K[s])
        m = {"HOME": h > a, "AWAY": a > h, "TIE": a == h}[sel]
        return _result(m.mean(), n, PRICED)
    if s in ("KXMLBSPREAD", "KXMLBF5SPREAD"):
        k = 5 if s == "KXMLBF5SPREAD" else 13
        a, h = team_run_arrays(k)
        margin = (h - a) if sel == "HOME" else (a - h)
        return _result((margin >= thr).mean(), n, PRICED)
    if s in ("KXMLBTOTAL", "KXMLBF5TOTAL"):
        k = 5 if s == "KXMLBF5TOTAL" else 13
        a, h = team_run_arrays(k)
        return _result(((a + h) >= thr).mean(), n, PRICED)
    if s == "KXMLBTEAMTOTAL":
        a, h = team_run_arrays()
        return _result(((h if sel == "HOME" else a) >= thr).mean(), n, PRICED)
    if s == "KXMLBRFI":
        return _result(((r.away[:, 0] + r.home[:, 0]) >= 1).mean(), n, PRICED)
    if s == "KXMLBEXTRAS":
        return _result(r.extras.mean(), n, PRICED)
    if s in ("KXMLBINNINGWIN", "KXMLBINNINGTOTAL"):
        i = parsed.get("inning")
        if not i or i < 1:
            return _result(None, 0, UNSUPPORTED, "no inning")
        if i >= 9:
            # the home half of the 9th is unplayed in ~40% of games and extras are conditional: wording unseen
            return _result(None, 0, UNSUPPORTED, "inning >= 9: unplayed/extra half-inning rules not observed")
        a, h = r.away[:, i - 1], r.home[:, i - 1]
        if s == "KXMLBINNINGTOTAL":
            return _result(((a + h) >= thr).mean(), n, PRICED_RULE_UNVERIFIED,
                           "assumes N means total runs in that inning >= N")
        m = {"HOME": h > a, "AWAY": a > h, "TIE": a == h}[sel]
        return _result(m.mean(), n, PRICED_RULE_UNVERIFIED, "assumes a scoreless inning resolves TIE")
    cls = parsed.get("classification") or {}
    return _result(None, 0, UNSUPPORTED, "no simulator path for %s (%s)" % (s, cls.get("modelStatus")))


def price_many(sim_result, tickers):
    """{ticker: probability dict} for an iterable of market tickers."""
    return {t: probability(sim_result, tx.parse_market_ticker(t)) for t in tickers}
