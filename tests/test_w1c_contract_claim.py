#!/usr/bin/env python3
"""
tests/test_w1c_contract_claim.py
================================
W1-C, final correction. The last gap in the chain:

    exact MLB gamePk -> exact Kalshi event -> exact Kalshi contract
      -> family/horizon/SELECTION/DIRECTION/THRESHOLD/SIDE
      -> executable price for THAT SAME contract

Everything before this increment proved that the CALLER FILLED IN THE REQUIRED
FIELDS. That is not the same statement as "the exact exchange contract says
these fields", and the difference is tradable. A caller could pass

    ticker    = KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4
    selection = PIT
    threshold = 7
    direction = OVER
    side      = YES

and satisfy every completeness rule. Nothing is missing there. The fields are
simply not that contract's: the ticker says MIL, and it says 4.

WHY THE NUMERALS ARE NEVER COMPARED DIRECTLY
--------------------------------------------
Kalshi encodes the integer in a suffix as the MINIMUM INCLUSIVE outcome and
words it as "over N-0.5". The ledger's own families disagree about which of
those two numbers they carry: GAME_TOTAL and TEAM_TOTAL carry N, RUN_LINE
carries N-0.5. So on the real 2026-09-10 slate

    KXMLBSPREAD-26SEP101905COLNYY-NYY4   ledger threshold 3.5   -> min 4  AGREE
    KXMLBTEAMTOTAL-26SEP101905COLNYY-NYY7 ledger threshold 7    -> min 7  AGREE

and a comparison of the raw numbers would have refused the first and, worse,
accepted a team-total claim of 3.5 against a -NYY4 contract. Both sides are
normalized to `minimumInclusive` before anything is compared.

EVERY TICKER CONVENTION HERE IS PROVEN FROM ARCHIVED REAL MARKETS
-----------------------------------------------------------------
`test_the_parsed_condition_matches_the_archived_kalshi_wording` reads
`data/kalshi/discovery/*.json` -- real markets with Kalshi's own titles -- and
checks the parser against the exchange's wording rather than against this
file's assumptions. Nothing below is a fixture semantics invention.
"""
import glob
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib.edgelab import market_identity as mi              # noqa: E402
from lib.edgelab import canonical_price as cp              # noqa: E402
from lib.edgelab import production_price as pp             # noqa: E402
from lib import kalshi_mlb_contract_parser as kmcp         # noqa: E402


# Real archived contracts (data/kalshi/discovery/2026-09-10.json) and the real
# 2026-07-11 MIL@PIT doubleheader contract the CR-3 finding names.
ML_PIT = "KXMLBGAME-26SEP101940PITCWS-PIT"
ML_CWS = "KXMLBGAME-26SEP101940PITCWS-CWS"
F5_PIT = "KXMLBF5-26SEP101940PITCWS-PIT"
F5_TIE = "KXMLBF5-26SEP101940PITCWS-TIE"
F3_PIT = "KXMLBF3-26SEP101940PITCWS-PIT"
F7_PIT = "KXMLBF7-26SEP101940PITCWS-PIT"
RL_PIT2 = "KXMLBSPREAD-26SEP101940PITCWS-PIT2"      # "wins by over 1.5 runs?"
RL_NYY5 = "KXMLBSPREAD-26SEP101905COLNYY-NYY5"      # "wins by over 4.5 runs?"
GT_9 = "KXMLBTOTAL-26SEP101940PITCWS-9"             # "Over 8.5 runs scored"
F5_TOTAL_7 = "KXMLBF5TOTAL-26SEP101940PITCWS-7"     # "First 5 innings: Over 6.5"
TT_PIT4 = "KXMLBTEAMTOTAL-26SEP101940PITCWS-PIT4"   # "score over 3.5 runs?"
TT_MIL4 = "KXMLBTEAMTOTAL-26JUL111605MILPIT-MIL4"   # the CR-3 contract itself
RFI = "KXMLBRFI-26SEP101940PITCWS"                  # "1st inning: Over 0.5 runs"


def outcome(ticker, **claim):
    return mi.resolve_contract(ticker, **claim)[1]


def proven(ticker, **claim):
    return outcome(ticker, **claim) == mi.IDENTITY_PROVEN


def mismatch(ticker, **claim):
    """REFUSED for a positive contradiction -- not for an absence.

    The distinction is the point of the separate outcome: NO_CONTRACT,
    UNKNOWN_FAMILY and INCOMPLETE all say "something is missing", and here
    nothing is. Every field is populated. They are populated wrong.
    """
    return outcome(ticker, **claim) == mi.IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH


# ── 1. FULL-GAME MONEYLINE ───────────────────────────────────────────────────

def test_moneyline_proves_the_ticker_selected_team_and_full_game_horizon():
    ident, out = mi.resolve_contract(ML_PIT, selection="PIT",
                                     direction=mi.DIRECTION_WIN,
                                     side=mi.SIDE_YES)
    assert out == mi.IDENTITY_PROVEN
    assert ident["horizon"] == mi.HORIZON_FULL_GAME
    assert ident["contractCondition"] == kmcp.CONDITION_TEAM_WINS
    assert ident["parsedSelection"] == "PIT"
    assert ident["contractClaimVerified"] is True


def test_moneyline_wrong_team_refuses():
    """`KXMLBGAME-...-PIT` settles on Pittsburgh. Claiming Chicago names the
    other side of the same game, at the other side's price."""
    assert mismatch(ML_PIT, selection="CWS", direction=mi.DIRECTION_WIN,
                    side=mi.SIDE_YES)
    assert mismatch(ML_CWS, selection="PIT", direction=mi.DIRECTION_WIN,
                    side=mi.SIDE_YES)
    ident, _ = mi.resolve_contract(ML_PIT, selection="CWS",
                                   direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)
    assert ident["contractClaimMismatch"] == [
        {"field": "selection", "claimed": "CWS", "parsed": "PIT"}]


# ── 2. F3 / F5 / F7 MONEYLINE ────────────────────────────────────────────────

def test_period_moneylines_prove_team_and_horizon_together():
    for ticker, horizon in ((F3_PIT, mi.HORIZON_F3), (F5_PIT, mi.HORIZON_F5),
                            (F7_PIT, mi.HORIZON_F7)):
        ident, out = mi.resolve_contract(ticker, selection="PIT",
                                         direction=mi.DIRECTION_WIN,
                                         side=mi.SIDE_YES)
        assert out == mi.IDENTITY_PROVEN, ticker
        assert ident["horizon"] == horizon


def test_a_period_moneyline_with_the_wrong_team_refuses():
    for ticker in (F3_PIT, F5_PIT, F7_PIT):
        assert mismatch(ticker, selection="CWS", direction=mi.DIRECTION_WIN,
                        side=mi.SIDE_YES), ticker


def test_a_period_moneyline_claimed_as_full_game_refuses():
    """A BOS F5 ticker is not a BOS full-game ticker. The team agrees, the
    horizon does not, and they settle on different things."""
    assert outcome(F5_PIT, selection="PIT", direction=mi.DIRECTION_WIN,
                   side=mi.SIDE_YES,
                   expected_series="KXMLBGAME") == mi.IDENTITY_REFUSED_SERIES_MISMATCH
    assert outcome(ML_PIT, selection="PIT", direction=mi.DIRECTION_WIN,
                   side=mi.SIDE_YES,
                   expected_series="KXMLBF5") == mi.IDENTITY_REFUSED_SERIES_MISMATCH


def test_away_home_and_tie_contracts_are_distinct():
    away, _ = mi.resolve_contract(F5_PIT, selection="PIT",
                                  direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)
    home, _ = mi.resolve_contract("KXMLBF5-26SEP101940PITCWS-CWS",
                                  selection="CWS", direction=mi.DIRECTION_WIN,
                                  side=mi.SIDE_YES)
    assert away["parsedSelection"] != home["parsedSelection"]
    # The tie contract names NO team, so a team claim on it is a claim about a
    # different contract -- and the condition itself disagrees as well.
    tie, tie_out = mi.resolve_contract(F5_TIE, selection="PIT",
                                       direction=mi.DIRECTION_WIN,
                                       side=mi.SIDE_YES)
    assert tie_out == mi.IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH
    fields = {d["field"] for d in tie["contractClaimMismatch"]}
    assert fields == {"condition", "selection"}
    assert tie["contractCondition"] == kmcp.CONDITION_PERIOD_TIE


# ── 3. RUN LINE ──────────────────────────────────────────────────────────────

def test_run_line_proves_team_margin_horizon_and_side():
    """-PIT2 is Kalshi's "Pittsburgh wins by over 1.5 runs?", i.e. margin >= 2.

    The ledger carries that strike as `wins_by_over` = 1.5. Both are normalized
    to a minimum of 2 before they are compared -- which is the entire reason
    this passes, and the reason a raw 1.5-vs-2 comparison would not."""
    ident, out = mi.resolve_contract(RL_PIT2, selection="PIT",
                                     direction=mi.DIRECTION_OVER,
                                     threshold=1.5, side=mi.SIDE_YES)
    assert out == mi.IDENTITY_PROVEN
    assert ident["contractCondition"] == kmcp.CONDITION_TEAM_WIN_MARGIN_AT_LEAST
    assert ident["parsedMinimumInclusive"] == 2
    assert ident["claimedMinimumInclusive"] == 2
    assert ident["thresholdConvention"] == mi.HALF_POINT_BELOW
    assert ident["horizon"] == mi.HORIZON_FULL_GAME


def test_the_f5_spread_family_is_verified_on_the_same_terms():
    """Rule 7 again: `KXMLBF5SPREAD` is in SERIES_SEMANTICS, so it is reachable
    the moment a caller prices it. "Pittsburgh wins first 5 innings by over 2.5
    runs?" -> margin >= 3, on the F5 horizon."""
    ticker = "KXMLBF5SPREAD-26SEP101940PITCWS-PIT3"
    ident, out = mi.resolve_contract(ticker, selection="PIT",
                                     direction=mi.DIRECTION_OVER,
                                     threshold=2.5, side=mi.SIDE_YES)
    assert out == mi.IDENTITY_PROVEN
    assert ident["horizon"] == mi.HORIZON_F5
    assert ident["parsedMinimumInclusive"] == 3
    assert mismatch(ticker, selection="CWS", direction=mi.DIRECTION_OVER,
                    threshold=2.5, side=mi.SIDE_YES)
    assert mismatch(ticker, selection="PIT", direction=mi.DIRECTION_OVER,
                    threshold=1.5, side=mi.SIDE_YES)
    # ... and it is not interchangeable with the full-game run line.
    assert outcome(ticker, selection="PIT", direction=mi.DIRECTION_OVER,
                   threshold=2.5, side=mi.SIDE_YES,
                   expected_series="KXMLBSPREAD") == mi.IDENTITY_REFUSED_SERIES_MISMATCH


def test_run_line_opposite_team_refuses():
    assert mismatch(RL_PIT2, selection="CWS", direction=mi.DIRECTION_OVER,
                    threshold=1.5, side=mi.SIDE_YES)


def test_run_line_wrong_margin_refuses():
    # 2.5 normalizes to a minimum of 3; the contract's is 2.
    assert mismatch(RL_PIT2, selection="PIT", direction=mi.DIRECTION_OVER,
                    threshold=2.5, side=mi.SIDE_YES)
    # ... and -NYY5 is "over 4.5", so 3.5 is a different rung of the ladder.
    assert mismatch(RL_NYY5, selection="NYY", direction=mi.DIRECTION_OVER,
                    threshold=3.5, side=mi.SIDE_YES)
    assert proven(RL_NYY5, selection="NYY", direction=mi.DIRECTION_OVER,
                  threshold=4.5, side=mi.SIDE_YES)


def test_a_whole_number_run_line_is_not_a_kalshi_run_line():
    """"Wins by over 2" is not a rung Kalshi lists: the ladder is half-points,
    and 2 would normalize to 2.5, which is not a whole number of runs. Refusing
    beats rounding to the nearest rung, which would invent a contract."""
    assert mismatch(RL_PIT2, selection="PIT", direction=mi.DIRECTION_OVER,
                    threshold=2, side=mi.SIDE_YES)


def test_run_line_side_no_needs_the_negating_direction():
    assert mismatch(RL_PIT2, selection="PIT", direction=mi.DIRECTION_OVER,
                    threshold=1.5, side=mi.SIDE_NO)
    assert proven(RL_PIT2, selection="PIT", direction=mi.DIRECTION_UNDER,
                  threshold=1.5, side=mi.SIDE_NO)


# ── 4. GAME TOTAL ────────────────────────────────────────────────────────────

def test_game_total_proves_the_exact_total_condition():
    ident, out = mi.resolve_contract(GT_9, direction=mi.DIRECTION_OVER,
                                     threshold=9, side=mi.SIDE_YES)
    assert out == mi.IDENTITY_PROVEN
    assert ident["contractCondition"] == kmcp.CONDITION_COMBINED_RUNS_AT_LEAST
    assert ident["parsedMinimumInclusive"] == 9
    assert ident["thresholdConvention"] == mi.MINIMUM_INCLUSIVE


def test_game_total_wrong_threshold_refuses():
    for wrong in (8, 10, 8.5):
        assert mismatch(GT_9, direction=mi.DIRECTION_OVER, threshold=wrong,
                        side=mi.SIDE_YES), wrong


def test_game_total_yes_no_meaning_is_enforced():
    assert mismatch(GT_9, direction=mi.DIRECTION_OVER, threshold=9,
                    side=mi.SIDE_NO)
    assert proven(GT_9, direction=mi.DIRECTION_UNDER, threshold=9,
                  side=mi.SIDE_NO)


def test_the_f5_total_family_is_verified_on_the_same_terms():
    """Rule 7: any production family reachable by the ledger gets equivalent
    verification. F5TOTAL shares GAME_TOTAL's ledger semantics and horizon F5."""
    ident, out = mi.resolve_contract(F5_TOTAL_7, direction=mi.DIRECTION_OVER,
                                     threshold=7, side=mi.SIDE_YES)
    assert out == mi.IDENTITY_PROVEN
    assert ident["horizon"] == mi.HORIZON_F5
    assert ident["parsedMinimumInclusive"] == 7
    assert mismatch(F5_TOTAL_7, direction=mi.DIRECTION_OVER, threshold=6,
                    side=mi.SIDE_YES)


# ── 5. TEAM TOTAL ────────────────────────────────────────────────────────────

def test_team_total_proves_the_exact_team_and_scoring_condition():
    """The CR-3 contract itself. "Will Milwaukee score over 3.5 runs?" -> >= 4."""
    ident, out = mi.resolve_contract(TT_MIL4, selection="MIL",
                                     direction=mi.DIRECTION_OVER, threshold=4,
                                     side=mi.SIDE_YES)
    assert out == mi.IDENTITY_PROVEN
    assert ident["contractCondition"] == kmcp.CONDITION_TEAM_RUNS_AT_LEAST
    assert ident["parsedSelection"] == "MIL"
    assert ident["parsedMinimumInclusive"] == 4


def test_team_total_same_ticker_other_team_refuses():
    """PIT is in this game, and PIT's team total is a real market -- just not
    this one. That is what makes it dangerous rather than obviously wrong."""
    assert mismatch(TT_MIL4, selection="PIT", direction=mi.DIRECTION_OVER,
                    threshold=4, side=mi.SIDE_YES)


def test_team_total_wrong_threshold_refuses():
    for wrong in (3, 5, 7, 3.5):
        assert mismatch(TT_MIL4, selection="MIL", direction=mi.DIRECTION_OVER,
                        threshold=wrong, side=mi.SIDE_YES), wrong


def test_the_ceo_forged_row_is_refused_and_says_what_disagreed():
    """The exact row named in review: the right ticker, every field populated,
    and two of them belonging to a different contract."""
    ident, out = mi.resolve_contract(TT_MIL4, selection="PIT",
                                     direction=mi.DIRECTION_OVER, threshold=7,
                                     side=mi.SIDE_YES)
    assert out == mi.IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH
    assert ident["contractClaimVerified"] is False
    assert ident["contractClaimMismatch"] == [
        {"field": "selection", "claimed": "PIT", "parsed": "MIL"},
        {"field": "threshold", "claimed": 7, "parsed": 4},
    ]
    assert not ident["missing"], (
        "nothing was missing -- that is exactly why completeness could not "
        "catch this, and why the refusal must not be an INCOMPLETE")


# ── 6. NRFI / YRFI ───────────────────────────────────────────────────────────

def test_one_rfi_contract_two_sides_both_proven():
    assert proven(RFI, direction=mi.DIRECTION_EVENT_OCCURS, side=mi.SIDE_YES)
    assert proven(RFI, direction=mi.DIRECTION_EVENT_DOES_NOT_OCCUR,
                  side=mi.SIDE_NO)
    ident, _ = mi.resolve_contract(RFI, direction=mi.DIRECTION_EVENT_OCCURS,
                                   side=mi.SIDE_YES)
    assert ident["contractCondition"] == kmcp.CONDITION_FIRST_INNING_RUNS_AT_LEAST
    assert ident["parsedMinimumInclusive"] == 1, "'1st inning: Over 0.5 runs'"


def test_reversed_rfi_side_semantics_refuse():
    """Buying NO while claiming a run scores is buying the opposite trade."""
    assert mismatch(RFI, direction=mi.DIRECTION_EVENT_OCCURS, side=mi.SIDE_NO)
    assert mismatch(RFI, direction=mi.DIRECTION_EVENT_DOES_NOT_OCCUR,
                    side=mi.SIDE_YES)


def test_there_is_no_fabricated_second_rfi_contract():
    yrfi, _ = mi.resolve_contract(RFI, direction=mi.DIRECTION_EVENT_OCCURS,
                                  side=mi.SIDE_YES)
    nrfi, _ = mi.resolve_contract(RFI,
                                  direction=mi.DIRECTION_EVENT_DOES_NOT_OCCUR,
                                  side=mi.SIDE_NO)
    assert yrfi["marketTicker"] == nrfi["marketTicker"] == RFI
    assert yrfi["contractCondition"] == nrfi["contractCondition"]
    assert yrfi["side"] == mi.SIDE_YES and nrfi["side"] == mi.SIDE_NO


# ── 7. UNPARSEABLE IS A REFUSAL, NOT A GUESS ─────────────────────────────────

def test_a_team_the_event_does_not_contain_does_not_parse():
    """-XYZ4 on a MILPIT event names no team in that game. Parsing it to a
    team called XYZ would be inventing one."""
    out = outcome("KXMLBTEAMTOTAL-26JUL111605MILPIT-XYZ4", selection="XYZ",
                  direction=mi.DIRECTION_OVER, threshold=4, side=mi.SIDE_YES)
    assert out == mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNPARSEABLE


def test_an_unreadable_suffix_refuses_rather_than_defaulting():
    for ticker in ("KXMLBTOTAL-26SEP101940PITCWS-NINE",
                   "KXMLBTEAMTOTAL-26SEP101940PITCWS-4",
                   "KXMLBRFI-26SEP101940PITCWS-EXTRA",
                   "KXMLBGAME-26SEP101940PITCWS-PIT2"):
        assert outcome(ticker, selection="PIT", direction=mi.DIRECTION_OVER,
                       threshold=4, side=mi.SIDE_YES) in (
            mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNPARSEABLE,
            mi.IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH), ticker


def test_an_unparseable_contract_is_not_reported_as_incomplete():
    """"We cannot read the exchange's contract" and "the caller left a field
    blank" are different problems with different fixes."""
    ident, out = mi.resolve_contract("KXMLBTOTAL-26SEP101940PITCWS-NINE",
                                     direction=mi.DIRECTION_OVER, threshold=9,
                                     side=mi.SIDE_YES)
    assert out == mi.IDENTITY_REFUSED_CONTRACT_SEMANTICS_UNPARSEABLE
    assert not ident["missing"]
    assert ident["contractClaimVerified"] is False


# ── 8. EVENT COMPOSITION: the whole chain, one statement ─────────────────────

_FRESH = "2026-09-10T23:32:30Z"
_DECIDED = "2026-09-10T23:33:00Z"
_EVENT = "26SEP101940PITCWS"
_OTHER_EVENT = "26SEP101905COLNYY"


def _chain(*, event_suffix, ticker, claim, price_kwargs):
    """The production composition, evaluated exactly as the ledger evaluates it.

    Returns (actionable, identityStatus). `actionable` is the real conjunction:
    the price must be proven AND the contract claim proven AND the ticker's own
    event must be the event the physical game resolved to.
    """
    _, contract_outcome = mi.resolve_contract(ticker, **claim)
    belongs = mi.ticker_belongs_to_event(ticker, event_suffix)
    status = contract_outcome
    if status == mi.IDENTITY_PROVEN and belongs is not True:
        status = mi.IDENTITY_REFUSED_EVENT_GAME_MISMATCH
    price = pp.price_contract(market_ticker=ticker, side=cp.SIDE_YES,
                              **price_kwargs)
    return bool(price["actionable"] and mi.is_proven(status)), status


_GOOD_PRICE = dict(yes_bid="0.44", yes_ask="0.46", unit=cp.UNIT_DOLLARS,
                   captured_at=_FRESH, decided_at=_DECIDED)
_BAD_PRICE = dict(yes_bid="0.44", yes_ask="0.46", unit=cp.UNIT_DOLLARS,
                  captured_at=None, decided_at=_DECIDED)     # age unprovable
_GOOD_CLAIM = dict(selection="PIT", direction=mi.DIRECTION_OVER, threshold=4,
                   side=mi.SIDE_YES)
_BAD_CLAIM = dict(selection="CWS", direction=mi.DIRECTION_OVER, threshold=4,
                  side=mi.SIDE_YES)


def test_correct_event_wrong_contract_claim_refuses():
    ok, status = _chain(event_suffix=_EVENT, ticker=TT_PIT4, claim=_BAD_CLAIM,
                        price_kwargs=_GOOD_PRICE)
    assert status == mi.IDENTITY_REFUSED_CONTRACT_CLAIM_MISMATCH
    assert ok is False


def test_wrong_event_correct_contract_claim_refuses():
    """The wrong-leg case: the claim describes this contract perfectly, and the
    contract belongs to a different game. Ticker exclusivity cannot see it."""
    ok, status = _chain(event_suffix=_OTHER_EVENT, ticker=TT_PIT4,
                        claim=_GOOD_CLAIM, price_kwargs=_GOOD_PRICE)
    assert status == mi.IDENTITY_REFUSED_EVENT_GAME_MISMATCH
    assert ok is False


def test_correct_event_correct_claim_but_unproven_price_refuses():
    ok, status = _chain(event_suffix=_EVENT, ticker=TT_PIT4, claim=_GOOD_CLAIM,
                        price_kwargs=_BAD_PRICE)
    assert status == mi.IDENTITY_PROVEN, "identity really is proven here"
    assert ok is False, "B2 must still be load-bearing on its own"


def test_exact_event_exact_claim_exact_price_is_the_only_eligible_combination():
    ok, status = _chain(event_suffix=_EVENT, ticker=TT_PIT4, claim=_GOOD_CLAIM,
                        price_kwargs=_GOOD_PRICE)
    assert status == mi.IDENTITY_PROVEN
    assert ok is True
    # ... and every single-fault variant of it is not.
    for kwargs in (dict(event_suffix=_OTHER_EVENT, ticker=TT_PIT4,
                        claim=_GOOD_CLAIM, price_kwargs=_GOOD_PRICE),
                   dict(event_suffix=_EVENT, ticker=TT_PIT4, claim=_BAD_CLAIM,
                        price_kwargs=_GOOD_PRICE),
                   dict(event_suffix=_EVENT, ticker=TT_PIT4, claim=_GOOD_CLAIM,
                        price_kwargs=_BAD_PRICE)):
        assert _chain(**kwargs)[0] is False


# ── 9. THE CONVENTION, CHECKED AGAINST REAL ARCHIVED KALSHI WORDING ──────────

_TITLE_PATTERNS = (
    # "Over 8.5 runs scored" / "First 5 innings: Over 6.5 runs"
    (re.compile(r"over\s+(\d+)\.5\b", re.I), lambda m: int(m.group(1)) + 1),
    # "1st inning: Over 0.5 runs"
    (re.compile(r"over\s+0\.5\b", re.I), lambda m: 1),
)


def _minimum_from_title(title):
    """The minimum inclusive outcome Kalshi's own wording states, or None.

    Every counted MLB family words its rung as "over N-0.5", so the exchange's
    stated minimum is N+1 where the title says N.5. This is the independent
    reading the parser is checked against -- deliberately derived from the
    title text rather than from the ticker the parser also reads.
    """
    if not title:
        return None
    for pattern, to_minimum in _TITLE_PATTERNS:
        match = pattern.search(title)
        if match:
            return to_minimum(match)
    return None


def _archived_contracts():
    files = sorted(glob.glob(os.path.join(ROOT, "data", "kalshi", "discovery",
                                          "2026-*.json")))
    files = [f for f in files if re.search(r"\d{4}-\d{2}-\d{2}\.json$", f)]
    if not files:
        pytest.skip("no archived Kalshi discovery evidence in this tree")
    for path in files[-3:]:
        with open(path) as handle:
            try:
                doc = json.load(handle)
            except ValueError:
                continue
        for contract in doc.get("contracts") or []:
            yield contract


def test_the_parsed_condition_matches_the_archived_kalshi_wording():
    """
    THE EVIDENCE TEST. For every archived real market whose own title states a
    threshold, the parser's `minimumInclusive` must equal what the exchange's
    words say. This is what makes the integer convention a measured fact rather
    than a repository belief -- and it is where a Kalshi wording change would
    surface, loudly, instead of silently repricing a ladder.
    """
    checked, families = 0, set()
    for contract in _archived_contracts():
        ticker = contract.get("ticker")
        expected = _minimum_from_title(contract.get("marketTitle"))
        if expected is None or not ticker:
            continue
        parsed = kmcp.parse_contract_condition(ticker)
        if parsed["parseStatus"] != kmcp.PARSE_STATUS_PARSED:
            continue          # player props: no described grammar, by design
        assert parsed["minimumInclusive"] == expected, (
            "%s: Kalshi says %r (minimum %s), parser says %s"
            % (ticker, contract.get("marketTitle"), expected,
               parsed["minimumInclusive"]))
        families.add(parsed["condition"])
        checked += 1
    assert checked >= 30, (
        "only %d archived markets carried a stated threshold -- this test "
        "would pass vacuously" % checked)
    assert {kmcp.CONDITION_COMBINED_RUNS_AT_LEAST,
            kmcp.CONDITION_TEAM_RUNS_AT_LEAST,
            kmcp.CONDITION_TEAM_WIN_MARGIN_AT_LEAST,
            kmcp.CONDITION_FIRST_INNING_RUNS_AT_LEAST} <= families, (
        "the evidence must cover game total, team total, run line and RFI; "
        "covered: %s" % sorted(families))


def test_the_parsed_team_matches_the_archived_classifier_for_every_real_market():
    """
    The selection half of the same evidence check, against the classifier's own
    independently-derived `side`/`subjectType` on archived real markets.
    """
    checked = 0
    for contract in _archived_contracts():
        ticker = contract.get("ticker")
        if contract.get("subjectType") != "TEAM" or not ticker:
            continue
        parsed = kmcp.parse_contract_condition(ticker)
        if parsed["parseStatus"] != kmcp.PARSE_STATUS_PARSED:
            continue
        if parsed["condition"] != kmcp.CONDITION_TEAM_WIN_MARGIN_AT_LEAST:
            continue          # only winning_margin records the team in `side`
        assert parsed["selection"] == contract.get("side"), ticker
        checked += 1
    assert checked >= 10, "vacuous: only %d team-subject markets checked" % checked


# ── 10. THE EXEMPTION CANNOT LEAK ────────────────────────────────────────────

def _ledger_source():
    with open(os.path.join(ROOT, "scripts", "build_market_ledger.py")) as handle:
        return handle.read()


def test_research_only_series_are_exempt_but_unreachable_from_the_ledger():
    """
    Player-prop series have no described contract grammar, so their claims are
    not checked. That exemption is recorded on the identity rather than silent
    -- and it must never be reachable from a row that can become actionable.

    The guard is structural: every `identity(...)` call in the ledger names its
    series literally, and none of them is a research-only one.
    """
    ident, out = mi.resolve_contract("KXMLBKS-26SEP102140BOSNYY-GRAY6",
                                     selection="GRAY", threshold=6,
                                     side=mi.SIDE_YES)
    assert out == mi.IDENTITY_PROVEN
    assert ident["contractClaimVerified"] is False
    assert ident["contractClaimUnverifiedReason"] == "SERIES_NOT_DESCRIBED"

    source = _ledger_source()
    declared = set(re.findall(r"identity\([^)]*?'(KXMLB[A-Z0-9]+)'", source))
    assert declared, "no identity() call sites found -- the guard is vacuous"
    for series in declared:
        assert series not in mi.PLAYER_PROP_SERIES, (
            "%s is research-only and must never reach a ledger identity call"
            % series)
        assert kmcp.contract_grammar_for(series) is not None, (
            "%s reaches the ledger but has no described contract grammar, so "
            "its claims would go unchecked" % series)


def test_the_live_rehearsal_catches_a_forged_claim_rather_than_reporting_zero():
    """
    NEGATIVE CONTROL. `contractClaimMismatchViolations: 0` is only evidence if
    the check can be non-zero, and it is re-derived in the rehearsal from the
    row's own fields rather than read back off the ledger's verdict -- so a
    ledger that stamped a row verified while carrying the wrong selection must
    still be caught.
    """
    sys.path.insert(0, os.path.join(ROOT, "scripts", "audit"))
    import importlib
    ev = importlib.import_module("w1c_live_identity_evidence")

    def row(**kw):
        base = {"_gamePk": "1", "market": "TT_Away_Over", "confidence": "HIGH",
                "marketTicker": TT_PIT4, "marketFamily": "TEAM_TOTAL",
                "marketHorizon": mi.HORIZON_FULL_GAME, "physicalGameKey": "1",
                "selection": "PIT", "direction": mi.DIRECTION_OVER,
                "threshold": 4, "contractSide": mi.SIDE_YES,
                "identityStatus": mi.IDENTITY_PROVEN,
                "contractClaimVerified": True,
                "resolvedEventTickerSuffix": _EVENT,
                "contractEventTickerSuffix": _EVENT,
                "parsedMinimumInclusive": 4,
                "thresholdConvention": mi.MINIMUM_INCLUSIVE}
        base.update(kw)
        return base

    clean = ev.contract_identity_report([row()])
    assert clean["contractClaimMismatchViolations"] == []
    assert clean["actionableRowsWithSemanticsReChecked"] == 1
    assert clean["actionableRowsCarryingAStrike"] == 1, (
        "the strike counter is what says the normalization was exercised")

    # The ledger says verified; the payload says otherwise. The stamp loses.
    forged = ev.contract_identity_report([row(selection="CWS"), row(threshold=7)])
    assert len(forged["contractClaimMismatchViolations"]) == 2
    fields = {d["field"]
              for v in forged["contractClaimMismatchViolations"]
              for d in v["disagreements"]}
    assert fields == {"selection", "threshold"}


def test_every_production_series_the_ledger_can_price_has_a_grammar():
    for series, (_h, _f, _req) in mi.SERIES_SEMANTICS.items():
        assert kmcp.contract_grammar_for(series) is not None, series


def test_the_claim_check_is_reached_by_every_proven_production_contract():
    """A row may not be PROVEN with its claim unchecked, for any described
    family. `contractClaimVerified is not True` is deliberate: None means the
    check never ran, and "never ran" is not "passed"."""
    for ticker, claim in (
        (ML_PIT, dict(selection="PIT", direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)),
        (F5_PIT, dict(selection="PIT", direction=mi.DIRECTION_WIN, side=mi.SIDE_YES)),
        (RL_PIT2, dict(selection="PIT", direction=mi.DIRECTION_OVER,
                       threshold=1.5, side=mi.SIDE_YES)),
        (GT_9, dict(direction=mi.DIRECTION_OVER, threshold=9, side=mi.SIDE_YES)),
        (TT_PIT4, dict(selection="PIT", direction=mi.DIRECTION_OVER,
                       threshold=4, side=mi.SIDE_YES)),
        (RFI, dict(direction=mi.DIRECTION_EVENT_OCCURS, side=mi.SIDE_YES)),
    ):
        ident, out = mi.resolve_contract(ticker, **claim)
        assert out == mi.IDENTITY_PROVEN, ticker
        assert ident["contractClaimVerified"] is True, ticker
