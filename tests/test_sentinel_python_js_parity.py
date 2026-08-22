#!/usr/bin/env python3
"""
tests/test_sentinel_python_js_parity.py
===========================================
Sentinel Single-Source mission (docs/DUPLICATE_LOGIC_INVENTORY.md #2):
proves the sentinel-price definition lives in exactly one place --
lib/sentinel_constants.json -- and that every consumer (the canonical
lib/sentinel_validator.py, its downstream Python callers, and api/slate.js's
isSentinelPrice()) reads the same values rather than an independently
maintained copy.

Requires `node` on PATH, same as tests/test_f5_python_js_parity.py.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.sentinel_validator import (  # noqa: E402
    SENTINEL_AMERICAN_PRICES,
    SENTINEL_ABS_THRESHOLD,
    is_sentinel_american,
)
from lib.clv_validator import is_sentinel as clv_is_sentinel  # noqa: E402
from scripts.capture_clv_pregame import is_sentinel_price as capture_is_sentinel_price  # noqa: E402

NODE_AVAILABLE = shutil.which("node") is not None
pytestmark = pytest.mark.skipif(not NODE_AVAILABLE, reason="node not available on PATH")

CONSTANTS_PATH = os.path.join(ROOT, "lib", "sentinel_constants.json")

VALUE_FIXTURES = [19900, -19900, 100000, -100000, 199, -199, 50, -141, 19000, 18999, -19000, 0]


def _run_js(snippet):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", snippet],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def _js_is_sentinel_price(value):
    snippet = f"""
import {{ isSentinelPrice }} from './api/slate.js';
console.log(JSON.stringify(isSentinelPrice({value})));
"""
    return _run_js(snippet)


class TestCanonicalJsonIsTheOnlySource:

    def test_json_file_exists_and_has_expected_shape(self):
        with open(CONSTANTS_PATH) as f:
            data = json.load(f)
        assert set(data.keys()) == {"SENTINEL_AMERICAN_PRICES", "SENTINEL_ABS_THRESHOLD"}
        assert isinstance(data["SENTINEL_AMERICAN_PRICES"], list)
        assert isinstance(data["SENTINEL_ABS_THRESHOLD"], int)

    def test_python_canonical_module_loads_from_json(self):
        with open(CONSTANTS_PATH) as f:
            data = json.load(f)
        assert SENTINEL_AMERICAN_PRICES == set(data["SENTINEL_AMERICAN_PRICES"])
        assert SENTINEL_ABS_THRESHOLD == data["SENTINEL_ABS_THRESHOLD"]

    def test_js_module_values_match_the_canonical_json(self):
        """
        api/slate.js's isSentinelPrice() deliberately does NOT read
        lib/sentinel_constants.json at runtime (see
        docs/PRODUCTION_INCIDENT_SLATE_FS_IMPORT.md -- a prior revision did,
        and that top-level `import { readFileSync } from 'fs'` broke the
        deployed Vercel endpoint). It keeps a hardcoded literal instead,
        proven here to match the JSON's values for every fixture, run
        through the real exported function (not a source-text regex) --
        and tests/test_slate_no_filesystem_io.py separately guards that the
        runtime read is never reintroduced.
        """
        with open(CONSTANTS_PATH) as f:
            data = json.load(f)
        js_prices = set(data["SENTINEL_AMERICAN_PRICES"])
        js_threshold = data["SENTINEL_ABS_THRESHOLD"]
        for v in VALUE_FIXTURES:
            expected = v in js_prices or abs(v) >= js_threshold
            assert _js_is_sentinel_price(v) == expected, f"JS isSentinelPrice({v}) diverged from JSON-derived expectation"

    def test_python_fallback_literal_matches_json(self):
        """
        lib/sentinel_validator.py also keeps a hardcoded fallback pair for
        the rare case the JSON read fails (e.g. a dependency whitelist that
        predates this file, like the one
        tests/test_end_to_end_pipeline_sandbox.py builds). That fallback
        must never silently drift from the canonical JSON either.
        """
        with open(CONSTANTS_PATH) as f:
            data = json.load(f)
        with open(os.path.join(ROOT, "lib", "sentinel_validator.py")) as f:
            src = f.read()
        start = src.index("SENTINEL_AMERICAN_PRICES = {19900")
        end = src.index("}", start)
        literal = src[start:end]
        for price in data["SENTINEL_AMERICAN_PRICES"]:
            assert str(price) in literal, f"Python fallback literal missing {price} present in JSON"
        assert f"SENTINEL_ABS_THRESHOLD = {data['SENTINEL_ABS_THRESHOLD']}" in src

    def test_js_literal_matches_json(self):
        """
        api/slate.js's hardcoded literal must never silently drift from the
        canonical JSON -- if someone changes one without the other, this
        test catches it.
        """
        with open(CONSTANTS_PATH) as f:
            data = json.load(f)
        with open(os.path.join(ROOT, "api", "slate.js")) as f:
            src = f.read()
        start = src.index("const SENTINEL_AMERICAN_PRICES = new Set([")
        end = src.index("]);", start)
        literal = src[start:end]
        for price in data["SENTINEL_AMERICAN_PRICES"]:
            assert str(price) in literal, f"JS literal missing {price} present in JSON"
        assert f"const SENTINEL_ABS_THRESHOLD = {data['SENTINEL_ABS_THRESHOLD']};" in src


class TestPythonJsValueParity:

    @pytest.mark.parametrize("value", VALUE_FIXTURES)
    def test_matches_python_canonical(self, value):
        assert _js_is_sentinel_price(value) == is_sentinel_american(value)


class TestPythonConsumersStillUseCanonicalCore:
    """
    capture_clv_pregame.py and clv_validator.py both add a script-local
    {199, -199} extension on top of the shared canonical set (real American
    odds of +/-199 are ordinary values elsewhere in the system, so that
    pair must never be promoted into the shared JSON -- see each module's
    own comment for the full rationale). This proves both still delegate
    the broad set/threshold to the one canonical function rather than
    keeping their own independent copies of it.
    """

    @pytest.mark.parametrize("value", [19900, -19900, 100000, -100000, 19000, 18999, 50])
    def test_capture_clv_pregame_matches_canonical_outside_local_extension(self, value):
        assert capture_is_sentinel_price(value) == is_sentinel_american(value)

    @pytest.mark.parametrize("value", [19900, -19900, 100000, -100000, 19000, 18999, 50])
    def test_clv_validator_matches_canonical_outside_local_extension(self, value):
        assert clv_is_sentinel(value) == is_sentinel_american(value)

    def test_both_local_extensions_still_flag_199(self):
        assert capture_is_sentinel_price(199) is True
        assert capture_is_sentinel_price(-199) is True
        assert clv_is_sentinel(199) is True
        assert clv_is_sentinel(-199) is True
        # ...but the shared canonical function must NOT (real odds value).
        assert is_sentinel_american(199) is False
        assert is_sentinel_american(-199) is False
