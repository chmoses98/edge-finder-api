#!/usr/bin/env python3
"""
tests/test_log_manual_bet.py
===============================
Coverage for scripts/log_manual_bet.py: required-field validation and
safe append-to-bets.json behavior.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def m():
    if "log_manual_bet" in sys.modules:
        del sys.modules["log_manual_bet"]
    import log_manual_bet as mod
    return mod


def good_bet(**overrides):
    bet = {
        "date": "2026-07-30",
        "gameId": "778899",
        "game": "BOS @ ATH",
        "market": "ML",
        "side": "BOS",
        "line": None,
        "ticker": "KXMLBGAME-26JUL302140BOSATH-BOS",
        "entryBid": 0.53,
        "entryAsk": 0.54,
        "entryMid": 0.535,
        "purchasedPrice": -115,
        "entryTimestamp": "2026-07-30T21:30:00Z",
        "probability": 58,
        "stake": 10,
        "source": "MANUAL",
    }
    bet.update(overrides)
    return bet


class TestValidation:

    def test_valid_bet_passes(self, m):
        m.validate_bet(good_bet())  # should not raise

    def test_missing_required_fields_all_reported(self, m):
        with pytest.raises(m.ManualBetValidationError) as exc_info:
            m.validate_bet({"date": "2026-07-30", "game": "BOS @ ATH"})
        errs = exc_info.value.errors
        for field in ("market", "side", "line", "ticker", "entryBid", "entryAsk",
                      "entryMid", "purchasedPrice", "entryTimestamp",
                      "probability", "stake", "source"):
            assert any(field in e for e in errs), f"expected error mentioning {field}"

    def test_line_key_required_but_may_be_null(self, m):
        bet = good_bet()
        bet["line"] = None
        m.validate_bet(bet)  # OK — key present, value null (ML has no line)

        del bet["line"]
        with pytest.raises(m.ManualBetValidationError):
            m.validate_bet(bet)  # key entirely missing -> invalid

    def test_game_id_fully_optional(self, m):
        bet = good_bet()
        del bet["gameId"]
        m.validate_bet(bet)  # should not raise

    def test_invalid_source_rejected(self, m):
        with pytest.raises(m.ManualBetValidationError) as exc_info:
            m.validate_bet(good_bet(source="ROBOT"))
        assert any("source" in e for e in exc_info.value.errors)

    def test_valid_sources_accepted(self, m):
        m.validate_bet(good_bet(source="MANUAL"))
        m.validate_bet(good_bet(source="MODEL"))

    def test_bad_date_format_rejected(self, m):
        with pytest.raises(m.ManualBetValidationError) as exc_info:
            m.validate_bet(good_bet(date="07/30/2026"))
        assert any("date" in e for e in exc_info.value.errors)

    def test_bad_entry_timestamp_rejected(self, m):
        with pytest.raises(m.ManualBetValidationError) as exc_info:
            m.validate_bet(good_bet(entryTimestamp="not-a-timestamp"))
        assert any("entryTimestamp" in e for e in exc_info.value.errors)

    def test_non_positive_stake_rejected(self, m):
        with pytest.raises(m.ManualBetValidationError) as exc_info:
            m.validate_bet(good_bet(stake=0))
        assert any("stake" in e for e in exc_info.value.errors)

        with pytest.raises(m.ManualBetValidationError):
            m.validate_bet(good_bet(stake=-5))

    def test_non_numeric_price_fields_rejected(self, m):
        with pytest.raises(m.ManualBetValidationError) as exc_info:
            m.validate_bet(good_bet(entryBid="not-a-number"))
        assert any("entryBid" in e for e in exc_info.value.errors)


class TestAppendBehavior:

    def test_appends_to_empty_ledger(self, m, tmp_path):
        bets_path = str(tmp_path / "bets.json")
        with open(bets_path, "w") as f:
            json.dump([], f)
        entry = m.log_bet(good_bet(), bets_path=bets_path)
        with open(bets_path) as f:
            stored = json.load(f)
        assert len(stored) == 1
        assert stored[0]["id"] == entry["id"]
        assert stored[0]["source"] == "MANUAL"
        assert stored[0]["betSide"] == "BOS"
        assert stored[0]["marketTicker"] == "KXMLBGAME-26JUL302140BOSATH-BOS"

    def test_appends_without_clobbering_existing_bets(self, m, tmp_path):
        bets_path = str(tmp_path / "bets.json")
        existing = [{"id": "2026-07-29-001", "date": "2026-07-29", "game": "X @ Y"}]
        with open(bets_path, "w") as f:
            json.dump(existing, f)
        m.log_bet(good_bet(), bets_path=bets_path)
        with open(bets_path) as f:
            stored = json.load(f)
        assert len(stored) == 2
        assert stored[0] == existing[0]

    def test_id_sequence_increments_within_same_date(self, m, tmp_path):
        bets_path = str(tmp_path / "bets.json")
        with open(bets_path, "w") as f:
            json.dump([], f)
        e1 = m.log_bet(good_bet(ticker="T-1", entryTimestamp="2026-07-30T21:30:00Z"), bets_path=bets_path)
        e2 = m.log_bet(good_bet(ticker="T-2", entryTimestamp="2026-07-30T21:31:00Z"), bets_path=bets_path)
        assert e1["id"] == "2026-07-30-001"
        assert e2["id"] == "2026-07-30-002"

    def test_duplicate_guard_rejects_same_ticker_timestamp(self, m, tmp_path):
        bets_path = str(tmp_path / "bets.json")
        with open(bets_path, "w") as f:
            json.dump([], f)
        m.log_bet(good_bet(), bets_path=bets_path)
        with pytest.raises(ValueError, match="Duplicate"):
            m.log_bet(good_bet(), bets_path=bets_path)

    def test_duplicate_guard_can_be_overridden(self, m, tmp_path):
        bets_path = str(tmp_path / "bets.json")
        with open(bets_path, "w") as f:
            json.dump([], f)
        m.log_bet(good_bet(), bets_path=bets_path)
        m.log_bet(good_bet(), bets_path=bets_path, allow_duplicate=True)
        with open(bets_path) as f:
            stored = json.load(f)
        assert len(stored) == 2

    def test_probability_and_prices_normalized_to_pct(self, m, tmp_path):
        bets_path = str(tmp_path / "bets.json")
        with open(bets_path, "w") as f:
            json.dump([], f)
        # Fractional 0-1 inputs must normalize the same as 0-100 inputs.
        entry = m.log_bet(good_bet(entryBid=0.53, entryAsk=0.54, entryMid=0.535, probability=0.58),
                           bets_path=bets_path)
        assert entry["entryBidPct"] == 53.0
        assert entry["entryAskPct"] == 54.0
        assert entry["entryMidPct"] == 53.5
        assert entry["probabilityPct"] == 58.0

    def test_invalid_bet_never_written(self, m, tmp_path):
        bets_path = str(tmp_path / "bets.json")
        with open(bets_path, "w") as f:
            json.dump([], f)
        with pytest.raises(m.ManualBetValidationError):
            m.log_bet({"date": "2026-07-30"}, bets_path=bets_path)
        with open(bets_path) as f:
            stored = json.load(f)
        assert stored == []

    def test_creates_bets_file_if_missing(self, m, tmp_path):
        bets_path = str(tmp_path / "bets.json")  # does not exist yet
        m.log_bet(good_bet(), bets_path=bets_path)
        assert os.path.exists(bets_path)
        with open(bets_path) as f:
            stored = json.load(f)
        assert len(stored) == 1
