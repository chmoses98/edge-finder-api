#!/usr/bin/env python3
"""MLB-ALPHA-0002: deterministically re-materialise the raw Kalshi
exchange history that is deliberately NOT stored in Git.

Downloads the immutable Release assets named in raw_data_manifest.json,
extracts them into
data/edgelab/research_artifacts/mlb_alpha_0002/kalshi_history/, and
verifies EVERY per-file SHA256 against the manifest. Any mismatch or
missing file is a hard failure -- a partially hydrated dataset must never
be mistaken for the real one.

  python3 scripts/research/mlb_alpha_0002/hydrate_raw_dataset.py
  python3 ... --verify-only          # check an existing hydration
  python3 ... --from-dir <dir>       # extract from local archives instead

Exit 0 = every file present and hash-verified. RESEARCH ONLY; downloads
and verifies, never mutates the manifest.
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(REPO, "data", "edgelab", "research_artifacts", "mlb_alpha_0002")
HIST = os.path.join(ART, "kalshi_history")
MANIFEST = os.path.join(ART, "raw_data_manifest.json")
RELEASE_URL = "https://github.com/chmoses98/edge-finder-api/releases/download/%s/%s"


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest():
    with open(MANIFEST) as fh:
        return json.load(fh)


def verify(man, quiet=False):
    """-> (ok_count, problems[])"""
    ok, problems = 0, []
    for f in man["files"]:
        p = os.path.join(HIST, os.path.relpath(f["path"], "kalshi_history"))
        if not os.path.exists(p):
            problems.append("MISSING %s" % f["path"])
            continue
        if os.path.getsize(p) != f["bytes"]:
            problems.append("SIZE %s (%d != %d)" % (f["path"], os.path.getsize(p), f["bytes"]))
            continue
        got = sha256_file(p)
        if got != f["sha256"]:
            problems.append("SHA256 %s (%s != %s)" % (f["path"], got[:16], f["sha256"][:16]))
            continue
        ok += 1
        if not quiet and ok % 20 == 0:
            print("  verified %d/%d" % (ok, len(man["files"])))
    return ok, problems


def download(tag, asset, dest):
    url = RELEASE_URL % (tag, asset)
    print("  downloading %s" % url)
    with urllib.request.urlopen(url, timeout=600) as resp, open(dest, "wb") as out:
        shutil.copyfileobj(resp, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--from-dir", default="")
    ap.add_argument("--work-dir", default=os.path.join(REPO, ".hydrate_tmp"))
    args = ap.parse_args()
    man = load_manifest()
    tag = man["release"]["tag"]

    if not args.verify_only:
        expected = {a["asset"]: a for a in man.get("archives", [])}
        os.makedirs(args.work_dir, exist_ok=True)
        os.makedirs(HIST, exist_ok=True)
        for asset in man["release"]["assets"]:
            if not asset.endswith(".tar"):
                continue
            local = os.path.join(args.from_dir or args.work_dir, asset)
            if not args.from_dir:
                if not os.path.exists(local):
                    download(tag, asset, local)
            if not os.path.exists(local):
                print("MISSING ARCHIVE: %s" % local)
                return 2
            if asset in expected:
                got = sha256_file(local)
                if got != expected[asset]["sha256"]:
                    print("ARCHIVE SHA256 MISMATCH for %s: %s != %s" % (asset, got, expected[asset]["sha256"]))
                    return 2
                print("  archive %s sha256 OK" % asset)
            with tarfile.open(local) as tf:
                for m in tf.getmembers():           # refuse path traversal
                    if m.name.startswith("/") or ".." in m.name.split("/"):
                        print("REFUSING unsafe member %s" % m.name)
                        return 2
                tf.extractall(HIST)
            print("  extracted %s" % asset)

    ok, problems = verify(man)
    print("verified %d/%d files" % (ok, len(man["files"])))
    if problems:
        print("PROBLEMS (%d):" % len(problems))
        for p in problems[:20]:
            print("  " + p)
        return 1
    print("HYDRATION VERIFIED: %d files, %d bytes, dataset %s"
          % (ok, man["totals"]["rawBytes"], tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
