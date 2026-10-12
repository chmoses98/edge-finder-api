"""The vendored tools/pure_gate copies must match the sha256 pinned in VENDORED_FROM
(sift-sports-intelligence 558c592 for the gate and schema, e9cca49 for the scorecards). Do not edit them."""
import hashlib
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_vendored_pure_gate_matches_pin():
    pin = open(os.path.join(ROOT, "tools", "pure_gate", "VENDORED_FROM"), encoding="utf-8").read()
    pairs = re.findall(r"([0-9a-f]{64})\s+(\S+)", pin)
    assert {rel for _sha, rel in pairs} == {"pure_gate.py", "schema/pure_forecast.v1.schema.json", "accuracy_scorecard.py",
                                           "value_scorecard.py", "SCORECARDS.md"}
    for sha, rel in pairs:
        with open(os.path.join(ROOT, "tools", "pure_gate", rel), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest() == sha, rel
