#!/usr/bin/env python3
"""
Build the MLB-ALPHA-0003 market registry from archived evidence (read-only).

    python3 scripts/research/mlb_alpha_0003/build_registry.py \
        [--repo-root .] [--prospective-root <.../mlb_alpha_0002/prospective>] \
        [--out data/edgelab/research_artifacts/mlb_alpha_0003]

Inputs, all optional except the repo root:
  * newest data/kalshi/discovery/*_series_catalogue.json   (series list, titles)
  * data/edgelab/observations                              (priced markets, Aug..)
  * data/edgelab/settlements                               (settlement truth)
  * data/kalshi_registry_snapshots                         (titles / fresh tickers)
  * prospective quotes (tickers only: that store carries no prices, see audit)

Live mode (--live) is implemented in discover_live.py and needs network egress to
Kalshi; this script never touches the network.
"""

import argparse
import ast
import glob
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, REPO)

from lib.edgelab.mlb_alpha import corpus, registry  # noqa: E402


def _production_series(repo_root):
    src = open(os.path.join(repo_root, "scripts", "build_kalshi_registry.py")).read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "SERIES_CATALOGUE":
            return set(ast.literal_eval(node.value))
    return set()


def _alpha0002_policy(repo_root):
    p = os.path.join(repo_root, "data", "edgelab", "research_artifacts", "mlb_alpha_0002", "series_universe_policy.json")
    pol = json.load(open(p))["tiers"]

    class _Tiers(dict):
        def get(self, series, default=None):
            if series in pol["FULL_MICROSTRUCTURE"]["series"]:
                return "FULL_MICROSTRUCTURE"
            if series in pol["LIGHT_CAPTURE"]["series"]:
                return "LIGHT_CAPTURE"
            for pre in pol["DAILY_ONLY"]["seriesPrefixes"]:
                if series.startswith(pre):
                    return "DAILY_ONLY"
            return default
    return _Tiers()


def _mrv_policy(repo_root):
    p = os.path.join(repo_root, "data", "edgelab", "research_artifacts", "mrv_prospective", "mrv_series_policy.json")
    pol = json.load(open(p))

    class _Tiers(dict):
        def get(self, series, default=None):
            if series in pol["perGameBookSeries"]:
                return "PER_GAME_BOOK"
            if series in pol["propQuoteSeries"]:
                return "PROP_QUOTE"
            for pre in pol["excludedPrefixes"]:
                if series.startswith(pre):
                    return default            # excluded counts as not captured
            return default
    return _Tiers()


def _series_rows(repo_root):
    files = sorted(glob.glob(os.path.join(repo_root, "data", "kalshi", "discovery", "*_series_catalogue.json")))
    if not files:
        return [], None
    d = json.load(open(files[-1]))
    return [{"seriesTicker": s["seriesTicker"], "title": s.get("title")} for s in d.get("mlbAssociatedSeries", [])], d.get("date")


def _catalogue_history(repo_root):
    out = []
    for f in sorted(glob.glob(os.path.join(repo_root, "data", "kalshi", "discovery", "*_series_catalogue.json"))):
        d = json.load(open(f))
        if d.get("seriesCatalogueQueryError"):
            continue
        out.append((d["date"], {s["seriesTicker"] for s in d.get("mlbAssociatedSeries", [])}))
    return out


def _market_rows(repo_root, prospective_root):
    for r in corpus.iter_observations(repo_root):
        yield {"ticker": r.get("marketTicker"), "ts": r.get("capturedAt"),
               "yesBid": corpus.obs_price_dollars(r, "yesBid"), "yesAsk": corpus.obs_price_dollars(r, "yesAsk"),
               "volume": r.get("volume")}
    for f in sorted(glob.glob(os.path.join(repo_root, "data", "kalshi_registry_snapshots", "kalshi_search_2026-10-*.json"))):
        d = json.load(open(f))
        for m in d.get("markets", []):
            yield {"ticker": m.get("market_ticker"), "ts": m.get("snapshot_ts"),
                   "yesBid": m.get("yes_bid"), "yesAsk": m.get("yes_ask"), "volume": m.get("volume")}
    if prospective_root:
        for r in corpus.iter_prospective(prospective_root, "quotes"):
            yield {"ticker": r.get("marketTicker"), "ts": r.get("capturedAt"), "yesBid": None, "yesAsk": None}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=REPO)
    ap.add_argument("--prospective-root", default=None)
    ap.add_argument("--out", default=os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0003"))
    a = ap.parse_args(argv)

    series_rows, cat_date = _series_rows(a.repo_root)
    policies = {"production": _production_series(a.repo_root),
                "alpha0002": _alpha0002_policy(a.repo_root),
                "mrv": _mrv_policy(a.repo_root)}
    reg = registry.build_registry(
        series_rows, _market_rows(a.repo_root, a.prospective_root),
        corpus.iter_settlements(a.repo_root), policies, as_of="catalogue %s + archive through newest observation" % cat_date,
        catalogue_history=_catalogue_history(a.repo_root))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "market_registry.json"), "w") as fh:
        json.dump(reg, fh, indent=1, sort_keys=True, default=str)
    with open(os.path.join(a.out, "MARKET_REGISTRY.md"), "w") as fh:
        fh.write(registry.render_markdown(reg))
    print(json.dumps(reg["counts"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
