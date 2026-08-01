#!/usr/bin/env python3
"""
tests/edgelab/test_no_network_calls.py
==========================================
Static check backing the "makes zero Kalshi API calls" claim in
lib/edgelab/market_universe.py and lib/edgelab/clv.py's docstrings
(Phase 1 section K: "minimize Kalshi API requests"). Both modules read
already-captured snapshot files only.
"""
import os

_MODULES = [
    os.path.join("lib", "edgelab", "market_universe.py"),
    os.path.join("lib", "edgelab", "clv.py"),
    os.path.join("scripts", "edgelab", "ingest_market_observations.py"),
    os.path.join("scripts", "edgelab", "collect_clv.py"),
]

_NETWORK_MARKERS = ("requests.get", "requests.post", "urlopen", "httpx.", "http.client")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_market_capture_and_clv_modules_make_no_network_calls():
    for rel_path in _MODULES:
        path = os.path.join(_REPO_ROOT, rel_path)
        with open(path) as f:
            text = f.read()
        for marker in _NETWORK_MARKERS:
            assert marker not in text, f"{rel_path} unexpectedly references {marker!r} -- should read local files only"
