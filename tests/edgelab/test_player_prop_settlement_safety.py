#!/usr/bin/env python3
"""
tests/edgelab/test_player_prop_settlement_safety.py
========================================================
GitHub issue #43 non-regression guardrails, mirroring
tests/edgelab/test_no_automatic_wagering.py's pattern for the new
player-prop settlement surface: no order placement, no auto-staking,
no Kalshi API client, and the new MLB boxscore fetcher only ever talks
to statsapi.mlb.com -- never Kalshi, never a production
recommendation/risk-gate/execution module.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NEW_SETTLEMENT_FILES = [
    os.path.join(ROOT, "lib", "edgelab", "mlb_boxscore.py"),
    os.path.join(ROOT, "lib", "edgelab", "player_resolution.py"),
    os.path.join(ROOT, "lib", "edgelab", "player_stats.py"),
    os.path.join(ROOT, "lib", "edgelab", "player_prop_settlement.py"),
    os.path.join(ROOT, "lib", "research", "player_prop_parser.py"),
    os.path.join(ROOT, "scripts", "edgelab", "settle_markets.py"),
    os.path.join(ROOT, "scripts", "edgelab", "backfill_player_prop_settlement.py"),
]

FORBIDDEN_PATTERNS = [
    r"place_order", r"createorder", r"submit_order", r"kalshi\.post",
    r"requests\.(post|put)\(", r"kelly", r"auto[_-]?stake", r"auto[_-]?bet",
]

PRODUCTION_MODULES_MUST_NOT_IMPORT = [
    "lib.risk_gate", "scripts.risk_gate", "lib.promotion_engine",
    "lib.replay", "scripts.protect_slate", "scripts.write_pending_bets",
    "scripts.validate_slate",
]


def test_new_settlement_files_exist():
    for path in NEW_SETTLEMENT_FILES:
        assert os.path.exists(path), path


def test_no_order_placement_or_auto_staking_language():
    for path in NEW_SETTLEMENT_FILES:
        with open(path) as f:
            source = f.read()
        for pattern in FORBIDDEN_PATTERNS:
            assert not re.search(pattern, source, re.IGNORECASE), f"{path}: unexpected match for {pattern!r}"


def test_never_talks_to_kalshi_or_uses_kalshi_credentials():
    """
    Reusing lib.kalshi_mlb_contract_parser (a pure ticker-string parser,
    no network/auth of any kind -- see its own module docstring) is
    fine and expected; what must never appear is an actual Kalshi API
    call or credential in the new settlement surface, which only ever
    talks to statsapi.mlb.com (see test_mlb_boxscore_only_talks_to_
    statsapi_mlb_com below).
    """
    for path in NEW_SETTLEMENT_FILES:
        with open(path) as f:
            source = f.read()
        assert "kalshi.com" not in source.lower()
        assert "KALSHI_API_KEY" not in source
        assert "trade-api" not in source.lower()


def test_never_imports_production_recommendation_risk_or_replay_modules():
    for path in NEW_SETTLEMENT_FILES:
        with open(path) as f:
            source = f.read()
        for forbidden in PRODUCTION_MODULES_MUST_NOT_IMPORT:
            assert forbidden not in source, f"{path}: unexpectedly references {forbidden!r}"


def test_mlb_boxscore_only_talks_to_statsapi_mlb_com():
    path = os.path.join(ROOT, "lib", "edgelab", "mlb_boxscore.py")
    with open(path) as f:
        source = f.read()
    urls = re.findall(r"https?://[^\s\"'{]+", source)
    assert urls, "expected at least one URL constant in mlb_boxscore.py"
    for url in urls:
        assert url.startswith("https://statsapi.mlb.com"), f"unexpected host: {url}"
    assert "kalshi.com" not in source.lower()


def test_settle_market_full_never_modifies_settle_market():
    """
    lib.edgelab.settlement.settle_market() (the pre-existing, tested
    game-level pure decision function) must remain byte-identical in
    behavior for every family it already handled -- settle_market_full()
    only ADDS a player-prop dispatch in front of it, never changes its
    body. Regression-guards
    tests/edgelab/test_settlement.py::test_player_props_are_explicitly_unimplemented_not_fabricated,
    which documents settle_market()'s own still-true, still-tested,
    contract in isolation.
    """
    from lib.edgelab.settlement import settle_market

    market = {"marketFamily": "pitcher_strikeouts"}
    status, result, reason = settle_market(market, {"gameStatus": "Final"})
    assert status == "SETTLEMENT_UNRESOLVED"
    assert result is None
    assert reason == "player_prop_settlement_not_implemented"
