"""
lib/edgelab/mlb_alpha/corpus.py
===============================
Read-only loaders for the archived Kalshi / sportsbook / MLB-state evidence.

Two roots exist because the evidence lives on two branches:
  repo_root         main: observations, settlements, model_evaluations, registry snapshots
  prospective_root  research/mlb-alpha-0002-prospective:
                    data/edgelab/research_artifacts/mlb_alpha_0002/prospective
                    (books, trades, odds, mlb_state, runs, quotes)

Units are never inferred from magnitude inside a loader without a declared
rule: observation quotes follow lib.edgelab.research.ladder_semantics
(cents before 2026-09-10 19:30Z, dollars after), order books and trade tape are
declared dollars by their field names.

Stdlib only.
"""

import gzip
import json
import os

from lib.edgelab.research.ladder_semantics import observation_quote_cents

# Last observation snapshot captured in cents (kalshi_search_2026-09-10_1904);
# the next (2148) is dollars. Boundary measured, see docs/EDGELAB_MLB_ALPHA_0003_PROGRAM.md.
OBS_CENTS_THROUGH_CAPTURED_AT = "2026-09-10T19:30:00Z"


def _open(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def iter_jsonl(path):
    if not os.path.exists(path):
        return
    with _open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def list_days(directory):
    if not os.path.isdir(directory):
        return []
    return sorted(fn[:10] for fn in os.listdir(directory) if fn[:4] == "2026" or fn[:4] == "2027")


def _day_path(directory, day):
    for ext in (".jsonl.gz", ".jsonl"):
        p = os.path.join(directory, day + ext)
        if os.path.exists(p):
            return p
    return None


def obs_price_dollars(row, field):
    """Observation-archive quote -> dollars in (0, 1), or None. Declared-unit rule by capturedAt."""
    v = row.get(field)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    captured = row.get("capturedAt") or ""
    if captured <= OBS_CENTS_THROUGH_CAPTURED_AT:
        c = observation_quote_cents(f)          # cents era
        return None if c is None else c / 100.0
    return f if 0.0 < f < 1.0 else None         # dollars era; 0 and 1 are not executable prices


def iter_observations(repo_root, days=None):
    d = os.path.join(repo_root, "data", "edgelab", "observations")
    for day in (days or list_days(d)):
        p = _day_path(d, day)
        if p:
            for r in iter_jsonl(p):
                yield r


def iter_settlements(repo_root, days=None):
    d = os.path.join(repo_root, "data", "edgelab", "settlements")
    for day in (days or list_days(d)):
        p = _day_path(d, day)
        if p:
            for r in iter_jsonl(p):
                yield r


def prospective_dir(prospective_root, kind):
    return os.path.join(prospective_root, kind)


def iter_prospective(prospective_root, kind, days=None):
    d = prospective_dir(prospective_root, kind)
    for day in (days or list_days(d)):
        p = _day_path(d, day)
        if p:
            for r in iter_jsonl(p):
                yield r
