#!/usr/bin/env python3
"""
MRV-PORT-001: within-game outcome correlation (descriptive).  RESEARCH ONLY.
Phi correlation between settlement outcomes of canonical same-game contracts
and the effective number of independent bets for one contract per family.

Writes data/edgelab/research_artifacts/market_structure/portfolio_correlation.json
"""
import collections
import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import REPO, write_artifact, now_iso  # noqa: E402

from lib.edgelab.research.market_structure.identity import parse_ticker  # noqa: E402
from lib.edgelab.research.market_structure.settlements import build_outcomes  # noqa: E402

# canonical contract per family per game: home ML, home F5 ML, home spread 2 (wins by >=2), game total 8+, F5 total 5+, home team total 4+
CANON = {"ML_HOME": ("KXMLBGAME", "HOME", None), "F5ML_HOME": ("KXMLBF5", "HOME", None), "RL_HOME_2": ("KXMLBSPREAD", "HOME", 2),
         "TOTAL_8": ("KXMLBTOTAL", None, 8), "F5TOTAL_5": ("KXMLBF5TOTAL", None, 5), "TT_HOME_4": ("KXMLBTEAMTOTAL", "HOME", 4)}


def main():
    outcomes = build_outcomes(REPO)
    games = collections.defaultdict(dict)
    for t, oc in outcomes.items():
        ident = parse_ticker(t)
        if ident.get("status") != "RESOLVED":
            continue
        for name, (series, side, rung) in CANON.items():
            if ident["seriesTicker"] == series and (side is None or ident["side"] == side) and (rung is None or ident["rung"] == rung):
                games[ident["physicalGameKey"]][name] = 1 if oc == "YES" else 0
    names = list(CANON)
    phi = {}
    n_pairs = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            xs = [(g[a], g[b]) for g in games.values() if a in g and b in g]
            n = len(xs)
            if n < 30:
                continue
            ma = sum(x for x, _ in xs) / n; mb = sum(y for _, y in xs) / n
            cov = sum((x - ma) * (y - mb) for x, y in xs) / n
            sa = math.sqrt(sum((x - ma) ** 2 for x, _ in xs) / n); sb = math.sqrt(sum((y - mb) ** 2 for _, y in xs) / n)
            phi["%s~%s" % (a, b)] = round(cov / (sa * sb), 4) if sa > 0 and sb > 0 else None
            n_pairs["%s~%s" % (a, b)] = n
    # effective N for equal-weight one-contract-per-family portfolio on games having all six
    full = [g for g in games.values() if all(n in g for n in names)]
    eff = None
    if len(full) >= 30:
        k = len(names)
        tot_corr = 0.0
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                key = "%s~%s" % (a, b)
                if phi.get(key) is not None:
                    tot_corr += abs(phi[key])
        avg_abs = tot_corr / (k * (k - 1) / 2)
        eff = k / (1 + (k - 1) * avg_abs)
        eff_note = "k / (1 + (k-1) * mean |phi|), sign-agnostic upper-bound-style estimate"
    out = {"generatedAt": now_iso(), "tier": "TIER_B_EXPLORATORY", "games": len(games), "gamesWithAllSix": len(full),
           "phi": phi, "pairN": n_pairs, "effectiveIndependentBetsOfSix": eff, "effectiveNNote": eff_note if eff else None,
           "note": "descriptive; no inference; canonical contracts chosen a priori (home side, total 8+, F5 total 5+, team total 4+)"}
    p = write_artifact("portfolio_correlation.json", out)
    print("wrote", p, len(games), len(full), eff); print(phi)


if __name__ == "__main__":
    main()
