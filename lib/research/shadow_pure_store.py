#!/usr/bin/env python3
"""
lib/research/shadow_pure_store.py
=================================
Append-only, write-once file store with a sha256 hash-chain manifest for the PURE
shadow collection (W2 Phase 2). RESEARCH ONLY.

Rules enforced here (and by tests/research/test_shadow_pure_append_only.py against
origin/main):
  * every data file is written exactly once; writing to an existing path raises;
  * file names carry the capture time, so a new capture is always a new file;
  * MANIFEST.jsonl gets one line per file: {seq, path, sha256, bytes, written_at,
    prev_chain, chain}, chain = sha256(prev_chain + "|" + path + "|" + sha256);
    the manifest itself is only ever appended to;
  * verify() recomputes every hash and the chain and lists files missing from the
    manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

GENESIS = "0" * 64
MANIFEST = "MANIFEST.jsonl"


class WriteOnceError(RuntimeError):
    pass


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path):
    with open(path, "rb") as fh:
        return _sha256_bytes(fh.read())


def chain_hash(prev, rel, sha):
    return hashlib.sha256(f"{prev}|{rel}|{sha}".encode()).hexdigest()


class ShadowStore:
    def __init__(self, root):
        self.root = os.path.abspath(root)

    @property
    def manifest_path(self):
        return os.path.join(self.root, MANIFEST)

    def manifest(self):
        if not os.path.exists(self.manifest_path):
            return []
        with open(self.manifest_path, encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]

    def exists(self, rel):
        return os.path.exists(os.path.join(self.root, rel))

    def write_once(self, rel, payload, written_at=None):
        """Write `payload` (bytes, str, a JSON-able dict, or a list of dict rows -> JSONL) to
        root/rel exactly once and append it to the hash-chain manifest."""
        if rel == MANIFEST or rel.startswith(("/", "..")):
            raise WriteOnceError(f"refusing path {rel!r}")
        path = os.path.join(self.root, rel)
        if os.path.exists(path):
            raise WriteOnceError(f"write-once violation: {rel} already exists")
        if isinstance(payload, list):
            data = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in payload).encode()
        elif isinstance(payload, dict):
            data = (json.dumps(payload, indent=1, sort_keys=True) + "\n").encode()
        elif isinstance(payload, str):
            data = payload.encode()
        else:
            data = bytes(payload)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "xb") as fh:  # 'x' = fail if the file exists (race-safe)
            fh.write(data)
        man = self.manifest()
        prev = man[-1]["chain"] if man else GENESIS
        sha = _sha256_bytes(data)
        entry = {"seq": len(man), "path": rel, "sha256": sha, "bytes": len(data),
                 "written_at": written_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "prev_chain": prev, "chain": chain_hash(prev, rel, sha)}
        with open(self.manifest_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        return entry

    def files(self):
        out = []
        for d, _sub, names in os.walk(self.root):
            for n in names:
                rel = os.path.relpath(os.path.join(d, n), self.root)
                if rel != MANIFEST:
                    out.append(rel)
        return sorted(out)

    def verify(self):
        """{ok, n, problems[]}: hashes, chain continuity, unmanifested files."""
        problems, prev = [], GENESIS
        man = self.manifest()
        seen = set()
        for i, e in enumerate(man):
            if e.get("seq") != i:
                problems.append(f"seq gap at line {i}")
            if e.get("prev_chain") != prev:
                problems.append(f"chain break at {e.get('path')}")
            if e.get("chain") != chain_hash(e.get("prev_chain"), e.get("path"), e.get("sha256")):
                problems.append(f"chain hash mismatch at {e.get('path')}")
            p = os.path.join(self.root, e["path"])
            if not os.path.exists(p):
                problems.append(f"missing file {e['path']}")
            elif _sha256_file(p) != e["sha256"]:
                problems.append(f"content changed {e['path']}")
            if e["path"] in seen:
                problems.append(f"duplicate manifest entry {e['path']}")
            seen.add(e["path"])
            prev = e.get("chain")
        for f in self.files():
            if f not in seen:
                problems.append(f"unmanifested file {f}")
        return {"ok": not problems, "n": len(man), "problems": problems}
