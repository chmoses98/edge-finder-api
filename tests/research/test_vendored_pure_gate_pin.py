"""The vendored tools/pure_gate copy must match the sha256 pinned in VENDORED_FROM
(sift-sports-intelligence 558c592). Do not edit the vendored files."""
import hashlib
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_vendored_pure_gate_matches_pin():
    pin = open(os.path.join(ROOT, "tools", "pure_gate", "VENDORED_FROM"), encoding="utf-8").read()
    pairs = re.findall(r"([0-9a-f]{64})\s+(\S+)", pin)
    assert len(pairs) == 2
    for sha, rel in pairs:
        with open(os.path.join(ROOT, "tools", "pure_gate", rel), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == sha, rel
