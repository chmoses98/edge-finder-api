"""CI guard: nothing already committed under data/shadow_pure/ may be modified or deleted
relative to origin/main (MANIFEST.jsonl may only be appended to), the manifest hash chain
must verify (the vendored gate pin is checked in test_vendored_pure_gate_pin.py)."""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHADOW = "data/shadow_pure"


def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


def _base():
    if _git("rev-parse", "--verify", "-q", "origin/main").returncode != 0:
        pytest.skip("origin/main not available in this checkout")
    mb = _git("merge-base", "origin/main", "HEAD")
    if mb.returncode != 0:
        pytest.skip("no merge base with origin/main")
    return mb.stdout.strip()


def test_no_committed_shadow_file_modified_or_deleted():
    base = _base()
    out = _git("diff", "--name-status", "--no-renames", base, "--", SHADOW).stdout.splitlines()
    out += _git("diff", "--name-status", "--no-renames", "HEAD", "--", SHADOW).stdout.splitlines()  # uncommitted edits
    bad = []
    for line in out:
        status, path = line.split("\t", 1)
        if status == "A":
            continue
        if status == "M" and path.endswith("MANIFEST.jsonl"):
            old = _git("show", f"{base}:{path}").stdout
            with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
                new = fh.read()
            if new.startswith(old):
                continue
        bad.append(line)
    assert bad == [], f"append-only violation under {SHADOW}: {bad}"


def test_manifest_chains_verify():
    from lib.research.shadow_pure_store import ShadowStore
    root = os.path.join(ROOT, SHADOW)
    if not os.path.isdir(root):
        pytest.skip("no shadow store committed yet")
    for d in sorted(os.listdir(root)):
        if os.path.exists(os.path.join(root, d, "MANIFEST.jsonl")):
            v = ShadowStore(os.path.join(root, d)).verify()
            assert v["ok"], v["problems"][:10]
