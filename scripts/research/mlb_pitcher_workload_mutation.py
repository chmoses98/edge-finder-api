#!/usr/bin/env python3
"""
scripts/research/mlb_pitcher_workload_mutation.py
=================================================
W1-D runtime market-mutation test (RESEARCH ONLY). It actually RE-RUNS the upstream
models; it never compares pre-made files.

    python3 scripts/research/mlb_pitcher_workload_mutation.py [--workdir DIR] [--out report.json]

For each arm (normal, normal_repeat, market_removed, market_randomized) it builds a data
root under --workdir that is a symlink farm of the real `data/` tree:

  * normal / normal_repeat: market files linked unchanged;
  * market_removed: every market artifact absent -- data/kalshi*, data/odds.json,
    data/clv*, data/kalshi_*, data/edgelab/{settlements,observations,markets,clv_quotes},
    data/research_cache/{pinnacle_historical,sharp_market_probe};
  * market_randomized: data/edgelab/settlements and data/edgelab/observations partitions
    dated >= 2026-09-28 rewritten with seeded random values (every numeric field except
    identifiers: actualValue, statFields, prices, ...; YES/NO results reshuffled).
  In both mutated arms the odds/Kalshi subtrees embedded in the postseason schedule
  snapshots data/pipeline/<date>/normalized_slate.json are dropped / randomized too.

Then it runs `scripts/research/mlb_pitcher_workload_joint.py rerun` for
  * the PURE challenger (`--arm challenger`) -> must be BIT-IDENTICAL in every arm, and
  * the NEGATIVE CONTROL (`--arm champion_postseason`, the champion's production
    postseason path that reads settled Kalshi KS/OUTS rows as observations) -> must be
    deterministic on normal/normal_repeat and CHANGE (or fail) under each mutation.
Exit 0 only for PASS_RUNTIME_NONLEAKAGE.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "research", "mlb_pitcher_workload_joint.py")
MARKET_TOP = ("kalshi", "odds.json", "clv", "kalshi_")
MARKET_EDGELAB = ("settlements", "observations", "markets", "clv_quotes")
MARKET_CACHE = ("pinnacle_historical", "sharp_market_probe")
RANDOMIZE_FROM = "2026-09-28"
ID_KEYS = ("id", "Id", "pk", "Pk", "jerseyNumber", "schemaVersion")


def _is_id(key):
    return any(key.endswith(s) or key == s for s in ID_KEYS)


def _perturb(obj, rng, key=""):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("result", "outcome") and v in ("YES", "NO"):
                out[k] = rng.choice(("YES", "NO"))
            else:
                out[k] = _perturb(v, rng, k)
        return out
    if isinstance(obj, list):
        return [_perturb(v, rng, key) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str) or _is_id(key):
        return obj
    if isinstance(obj, int):
        return obj + rng.choice((-3, -2, -1, 1, 2, 3))
    if isinstance(obj, float):
        return round(obj * rng.uniform(0.5, 1.5) + rng.uniform(-0.05, 0.05), 6)
    return obj


def randomize_file(src, dst, seed):
    """Format-preserving randomization of one JSONL(.gz) market file."""
    rng = random.Random(f"{seed}:{os.path.basename(src)}")
    op = gzip.open if src.endswith(".gz") else open
    with op(src, "rt", encoding="utf-8") as fh:
        rows = [json.loads(x) for x in fh if x.strip()]
    with op(dst, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(_perturb(r, rng), sort_keys=True) + "\n")


MARKET_KEY_TOKENS = ("odds", "kalshi", "pinnacle", "VF", "Edge", "Prob", "Eval", "teamTotals", "nrfi", "f5",
                     "executionSlip", "requestsRemaining")


def _is_market_key(k):
    return any(t in k for t in MARKET_KEY_TOKENS)


def mutate_slate(src, dst, arm, seed):
    """Pipeline schedule snapshot: drop (removed) or randomize (randomized) every embedded
    market/odds subtree; schedule/probable-pitcher fields untouched."""
    rng = random.Random(f"{seed}:{src}")

    def walk(o):
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                if _is_market_key(k):
                    if arm == "market_removed":
                        continue
                    out[k] = _perturb(v, rng, "")
                else:
                    out[k] = walk(v)
            return out
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o

    with open(src, encoding="utf-8") as fh:
        doc = json.load(fh)
    with open(dst, "w", encoding="utf-8") as fh:
        json.dump(walk(doc), fh)


def _link(src, dst):
    os.symlink(os.path.abspath(src), dst)


def build_root(data_root, dest, arm, seed):
    os.makedirs(dest)
    for name in sorted(os.listdir(data_root)):
        src = os.path.join(data_root, name)
        market_top = any(name.startswith(m) for m in MARKET_TOP)
        if arm == "market_removed" and market_top:
            continue
        if name == "edgelab":
            ed = os.path.join(dest, "edgelab")
            os.makedirs(ed)
            for child in sorted(os.listdir(src)):
                csrc = os.path.join(src, child)
                if child in MARKET_EDGELAB and arm == "market_removed":
                    continue
                if child in ("settlements", "observations") and arm == "market_randomized":
                    cd = os.path.join(ed, child)
                    os.makedirs(cd)
                    for f in sorted(os.listdir(csrc)):
                        fs = os.path.join(csrc, f)
                        if f[:10] >= RANDOMIZE_FROM:
                            randomize_file(fs, os.path.join(cd, f), seed)
                        else:
                            _link(fs, os.path.join(cd, f))
                    continue
                _link(csrc, os.path.join(ed, child))
            continue
        if name == "pipeline" and arm in ("market_removed", "market_randomized"):
            pd = os.path.join(dest, "pipeline")
            os.makedirs(pd)
            for d in sorted(os.listdir(src)):
                dsrc = os.path.join(src, d)
                if d < RANDOMIZE_FROM or not os.path.isdir(dsrc):
                    _link(dsrc, os.path.join(pd, d))
                    continue
                os.makedirs(os.path.join(pd, d))
                for f in sorted(os.listdir(dsrc)):
                    if f == "normalized_slate.json":
                        mutate_slate(os.path.join(dsrc, f), os.path.join(pd, d, f), arm, seed)
                    else:
                        _link(os.path.join(dsrc, f), os.path.join(pd, d, f))
            continue
        if name == "research_cache" and arm == "market_removed":
            rc = os.path.join(dest, "research_cache")
            os.makedirs(rc)
            for child in sorted(os.listdir(src)):
                if child not in MARKET_CACHE:
                    _link(os.path.join(src, child), os.path.join(rc, child))
            continue
        _link(src, os.path.join(dest, name))


def sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def run(arm_name, model_arm, root, workdir):
    out = os.path.join(workdir, f"{model_arm}.{arm_name}.jsonl")
    env = {**os.environ, "PYTHONHASHSEED": "0", "TZ": "UTC", "OMP_NUM_THREADS": "1"}
    p = subprocess.run([sys.executable, SCRIPT, "rerun", "--arm", model_arm, "--data-root", root, "--out", out],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    if p.returncode != 0 or not os.path.exists(out):
        return {"exit": p.returncode, "sha256": None, "stderr_tail": p.stderr[-400:]}
    with open(out, encoding="utf-8") as fh:
        n = sum(1 for _ in fh)
    return {"exit": 0, "sha256": sha(out), "rows": n}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default=os.path.join(ROOT, "data"))
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    workdir = a.workdir or tempfile.mkdtemp(prefix="mlb_mutation_")
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir)
    arms = ("normal", "normal_repeat", "market_removed", "market_randomized")
    roots = {}
    for arm in arms:
        roots[arm] = os.path.join(workdir, "root_" + arm)
        build_root(a.data_root, roots[arm], "normal" if arm == "normal_repeat" else arm, a.seed)
    res = {m: {arm: run(arm, m, roots[arm], workdir) for arm in arms} for m in ("challenger", "champion_postseason")}
    ch, ct = res["challenger"], res["champion_postseason"]
    model_ok = all(r["exit"] == 0 for r in ch.values()) and len({r["sha256"] for r in ch.values()}) == 1
    ctrl_det = ct["normal"]["exit"] == 0 and ct["normal"]["sha256"] == ct["normal_repeat"]["sha256"]
    ctrl_resp = all(ct[m]["exit"] != 0 or ct[m]["sha256"] != ct["normal"]["sha256"] for m in ("market_removed", "market_randomized"))
    verdict = ("PASS_RUNTIME_NONLEAKAGE" if model_ok and ctrl_det and ctrl_resp else
               "FAIL_MARKET_LEAK" if not model_ok else
               "FAIL_NONDETERMINISTIC" if not ctrl_det else "FAIL_NO_POWER")
    report = {"verdict": verdict, "seed": a.seed, "randomize_from": RANDOMIZE_FROM, "results": res,
              "challenger_bit_identical_all_arms": model_ok, "control_deterministic": ctrl_det,
              "control_responds_to_every_mutation": ctrl_resp, "workdir": workdir}
    txt = json.dumps(report, indent=1, sort_keys=True)
    print(txt)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(txt + "\n")
    return 0 if verdict == "PASS_RUNTIME_NONLEAKAGE" else 1


if __name__ == "__main__":
    sys.exit(main())
