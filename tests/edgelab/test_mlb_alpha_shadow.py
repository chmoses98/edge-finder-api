"""C01-PIT prospective shadow: trigger gating, entry schema, no leakage."""

import subprocess
import os
from datetime import datetime, timedelta

from lib.edgelab.mlb_alpha_shadow import (
    evaluate_observation, first_eligible_entry, can_trigger,
    TRIGGER_STREAM_ID, RULE_SHA256, ELIGIBLE, DECLINED,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
EVENT = "KXMLBF5TOTAL-26AUG161920SEAHOU"
START = datetime(2026, 8, 16, 23, 20)          # UTC first pitch


def obs(minutes_before=30, yes_ask=95.0, yes_bid=94.0, **kw):
    o = {"marketTicker": EVENT + "-1", "eventTicker": EVENT,
         "capturedAt": START - timedelta(minutes=minutes_before),
         "yesBid": yes_bid, "yesAsk": yes_ask, "threshold": 1,
         "volume": 500.0, "openInterest": 480.0, "marketStatus": "active",
         "captureId": "cap-1", "provenance": {"sourceSystem": TRIGGER_STREAM_ID}}
    o.update(kw)
    return o


# ------------------------------------------------------- trigger-stream gating
def test_only_the_frozen_trigger_stream_can_trigger():
    assert can_trigger(TRIGGER_STREAM_ID)
    for other in ("kalshi_registry_snapshots", "standalone_price_check",
                  "c01pit_observational_v1", "", None):
        assert not can_trigger(other)


def test_observational_streams_never_create_an_entry():
    """Section E: extra research captures must never alter the official entry."""
    for stream in ("kalshi_registry_snapshots", "standalone_price_check",
                   "c01pit_observational_v1"):
        rec = evaluate_observation(obs(), stream)
        assert rec["eligibility"] == DECLINED
        assert rec["exclusionReason"] == "not_the_frozen_trigger_stream"
        assert rec["canTriggerC01Pit"] is False


def test_trigger_stream_observation_is_eligible_and_flagged():
    rec = evaluate_observation(obs(), TRIGGER_STREAM_ID)
    assert rec["eligibility"] == ELIGIBLE
    assert rec["canTriggerC01Pit"] is True
    assert rec["ruleSha256"] == RULE_SHA256


# --------------------------------------------------------------- the frozen rule
def test_price_band_is_90_to_99_inclusive():
    assert evaluate_observation(obs(yes_ask=90.0), TRIGGER_STREAM_ID)["eligibility"] == ELIGIBLE
    assert evaluate_observation(obs(yes_ask=99.0), TRIGGER_STREAM_ID)["eligibility"] == ELIGIBLE
    for bad in (89.0, 99.5, 100.0):
        rec = evaluate_observation(obs(yes_ask=bad), TRIGGER_STREAM_ID)
        assert rec["eligibility"] == DECLINED
        assert rec["exclusionReason"] == "outside_price_band"


def test_window_is_t_minus_60_to_t_zero():
    assert evaluate_observation(obs(minutes_before=60), TRIGGER_STREAM_ID)["eligibility"] == ELIGIBLE
    assert evaluate_observation(obs(minutes_before=1), TRIGGER_STREAM_ID)["eligibility"] == ELIGIBLE
    early = evaluate_observation(obs(minutes_before=61), TRIGGER_STREAM_ID)
    assert early["exclusionReason"] == "before_trigger_window_opens"
    late = evaluate_observation(obs(minutes_before=-1), TRIGGER_STREAM_ID)
    assert late["exclusionReason"] == "post_start_or_at_start"


def test_first_qualifying_quote_wins_not_the_best_one():
    """The rule is FIRST, never cheapest/latest -- a later, better price must
    not displace the first qualifying observation."""
    entry = first_eligible_entry([obs(minutes_before=20, yes_ask=91.0),
                                  obs(minutes_before=50, yes_ask=97.0),
                                  obs(minutes_before=40, yes_ask=93.0)],
                                 TRIGGER_STREAM_ID)
    assert entry["minutesToStart"] == 50.0
    assert entry["entryExecutableCents"] == 97.0


def test_inactive_market_declines():
    rec = evaluate_observation(obs(marketStatus="closed"), TRIGGER_STREAM_ID)
    assert rec["exclusionReason"] == "market_not_active"


def test_non_universe_ticker_declines():
    rec = evaluate_observation(obs(marketTicker="KXMLBGAME-26AUG161920SEAHOU-SEA"),
                               TRIGGER_STREAM_ID)
    assert rec["exclusionReason"] == "outside_c01pit_universe"


def test_unresolved_identity_declines_with_reason():
    rec = evaluate_observation(obs(eventTicker="KXMLBF5TOTAL-26AUG161920ZZZQQQ"),
                               TRIGGER_STREAM_ID)
    assert rec["eligibility"] == DECLINED
    assert rec["exclusionReason"].startswith("identity_")


def test_doubleheader_entry_is_accepted_and_labelled():
    e = "KXMLBF5TOTAL-26AUG291915BOSNYYG2"
    rec = evaluate_observation(
        obs(marketTicker=e + "-1", eventTicker=e,
            capturedAt=datetime(2026, 8, 29, 22, 45)), TRIGGER_STREAM_ID)
    assert rec["eligibility"] == ELIGIBLE
    assert rec["doubleheaderGame"] == 2
    assert (rec["awayTeam"], rec["homeTeam"]) == ("BOS", "NYY")


# ----------------------------------------------------------------- entry schema
def test_entry_carries_no_outcome_information():
    """Entries are created before the result exists; no outcome field may
    appear, by construction."""
    rec = evaluate_observation(obs(), TRIGGER_STREAM_ID)
    banned = ("settlementResult", "result", "won", "netPL", "grossPL", "roi",
              "outcome", "finalScore", "f5Total", "correctedResult")
    for k in banned:
        assert k not in rec, "outcome field %r leaked into a prospective entry" % k


def test_entry_records_economics_without_claiming_a_fill():
    rec = evaluate_observation(obs(yes_ask=95.0), TRIGGER_STREAM_ID)
    assert rec["hypotheticalContracts"] > 0
    assert rec["modeledCashDeployedUsd"] > 0
    assert rec["estimatedFeeUsd"] >= 0
    assert "NOT proven" in rec["executionCaveat"]


def test_depth_absence_is_flagged_not_fabricated():
    rec = evaluate_observation(obs(), TRIGGER_STREAM_ID)
    assert rec["depthAvailable"] is False
    assert "DEPTH_UNAVAILABLE" in rec["dataQualityFlags"]
    assert rec["yesAskSize"] is None


def test_depth_is_recorded_when_the_exchange_supplies_it():
    rec = evaluate_observation(obs(yesAskSize=250, yesBidSize=100), TRIGGER_STREAM_ID)
    assert rec["depthAvailable"] is True
    assert "DEPTH_UNAVAILABLE" not in rec["dataQualityFlags"]
    assert rec["yesAskSize"] == 250


def test_no_side_prices_are_derived_from_the_yes_book():
    rec = evaluate_observation(obs(yes_bid=94.0, yes_ask=95.0), TRIGGER_STREAM_ID)
    assert rec["noAsk"] == 6.0      # 100 - yesBid
    assert rec["noBid"] == 5.0      # 100 - yesAsk


# ------------------------------------------------------------ production firewall
def test_no_production_module_imports_the_shadow():
    out = subprocess.run(
        ["grep", "-rn", "mlb_alpha_shadow",
         os.path.join(REPO, "scripts", "build_market_ledger.py"),
         os.path.join(REPO, "scripts", "risk_gate.py"),
         os.path.join(REPO, "api"),
         os.path.join(REPO, "lib", "promotion_engine.py")],
        capture_output=True, text=True).stdout.strip()
    assert out == "", "shadow leaked into a production path: %s" % out


# ------------------------------------------------------------- prospective CLV
from lib.edgelab.mlb_alpha_shadow import (  # noqa: E402
    select_closing_observation, executable_clv_cents, fair_mid_clv_cents, clv_block,
)


def _q(minutes_before, bid, ask, status="active"):
    return {"capturedAt": START - timedelta(minutes=minutes_before),
            "yesBid": bid, "yesAsk": ask, "marketStatus": status}


def test_closing_quote_must_be_strictly_later_than_the_entry():
    """The holdout defect: entry == close gives an identically zero CLV."""
    entry_at = START - timedelta(minutes=45)
    later = select_closing_observation(
        [_q(45, 94, 95), _q(20, 95, 96), _q(5, 96, 97)],
        START, entry_captured_at=entry_at)
    assert later["capturedAt"] == START - timedelta(minutes=5)


def test_no_later_quote_yields_no_close_rather_than_the_entry_itself():
    entry_at = START - timedelta(minutes=45)
    assert select_closing_observation([_q(45, 94, 95)], START,
                                      entry_captured_at=entry_at) is None


def test_post_start_and_inactive_quotes_cannot_be_the_close():
    entry_at = START - timedelta(minutes=45)
    assert select_closing_observation([_q(-5, 97, 98)], START, entry_at) is None
    assert select_closing_observation([_q(10, 97, 98, status="closed")],
                                      START, entry_at) is None


def test_executable_clv_is_positive_when_you_bought_below_the_close():
    assert executable_clv_cents(94.0, 96.0) == 2.0
    assert executable_clv_cents(96.0, 94.0) == -2.0
    assert executable_clv_cents(None, 96.0) is None


def test_fair_mid_clv_needs_both_sides_of_both_books():
    assert fair_mid_clv_cents(94, 96, 95, 97) == 1.0
    assert fair_mid_clv_cents(94, None, 95, 97) is None
    assert fair_mid_clv_cents(94, 96, None, 97) is None


def test_clv_block_reports_both_measures_and_spread_compression():
    entry = {"capturedAt": START - timedelta(minutes=45), "yesBid": 92.0, "yesAsk": 96.0}
    closing = {"capturedAt": START - timedelta(minutes=5), "yesBid": 95.0, "yesAsk": 97.0}
    b = clv_block(entry, closing)
    assert b["executableClvCents"] == 1.0          # 97 - 96
    assert b["fairMidClvCents"] == 2.0             # 96 - 94
    assert b["entrySpreadCents"] == 4.0 and b["closingSpreadCents"] == 2.0
    assert b["spreadCompressionCents"] == 2.0
    assert b["closingIsLaterThanEntry"] is True
    from lib.edgelab.clv_convention import CONVENTION_ID, UNIT_CENTS
    assert b["clvConvention"] == CONVENTION_ID
    assert b["clvUnit"] == UNIT_CENTS


def test_clv_block_is_honest_when_there_is_no_close():
    entry = {"capturedAt": START - timedelta(minutes=45), "yesBid": 92.0, "yesAsk": 96.0}
    b = clv_block(entry, None)
    assert b["executableClvCents"] is None and b["fairMidClvCents"] is None
    assert b["closingIsLaterThanEntry"] is False
