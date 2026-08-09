#!/usr/bin/env python3
"""
tests/edgelab/test_wager_context_capture.py
================================================
Prospective Canonical Wager-Context Capture milestone: when a real wager
is canonically imported/placed, the decision-time context needed for
later calibration and CLV reporting (recommendation/confidence tier,
model fair probability, manual fair probability, the archived linked
market observation, the decision layer's own executable price and
bet-up-to price, post-friction edge, market family/threshold/side,
correlation group(s), and source recommendation/evaluation IDs) is
snapshotted onto the bet ONCE, at entry time, and never mutated
afterward except through an explicit correction (write_placed_bet's
on_conflict="overwrite").

Backfilling historical bets from chat memory is explicitly NOT covered by
this milestone -- see docs/PROSPECTIVE_WAGER_CONTEXT_CAPTURE.md if it
exists, or the PR description otherwise. Every test here only ever
exercises the FORWARD (prospective) capture path.
"""
import importlib.util
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.edgelab import storage
from lib.edgelab.bets import build_manual_bet_record, resolve_recommendation_context, write_placed_bet
from lib.edgelab.clv import compute_clv_for_bet, finalize_closing_quotes
from lib.edgelab.reports import build_rolling_window_report


def _load_script(name):
    path = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


import_script = _load_script("import_bet_batch.py")
workflow_script = _load_script("record_bet_from_workflow.py")

BETS_PATH = os.path.join("data", "edgelab", "bets", "bets.jsonl")
GAME_DATE = "2026-08-03"
TICKER = "SF-F5-ML"


def _seed_corpus(game_date=GAME_DATE, ticker=TICKER):
    games = [{"gameId": "9001", "gameDate": game_date, "awayTeam": "SF", "homeTeam": "LAD", "scheduledStartTime": "2026-08-03T23:00:00Z"}]
    markets = [{"marketTicker": ticker, "gameId": "9001", "marketFamily": "game_result", "marketHorizon": "F5", "team": "SF", "threshold": None}]
    storage.append_records(storage.partition_path("games", game_date), games, "gameId")
    storage.append_records(storage.partition_path("markets", game_date), markets, "marketTicker")


def _seed_recommendation(
    recommendation_id="rec-001", game_date=GAME_DATE, ticker=TICKER,
    model_fair_probability=58.0, estimated_edge=3.5, confidence="HIGH",
    market_implied_probability=54.5, price_ceiling=56.2, model_evaluation_id="eval-001",
):
    rec = {
        "schemaVersion": "1", "recommendationId": recommendation_id, "runId": "run-001",
        "gameId": "9001", "sport": "MLB", "platform": "KALSHI", "marketTicker": ticker,
        "marketName": None, "marketFamily": "game_result", "status": "RECOMMENDED",
        "modelEvaluationId": model_evaluation_id,
        "modelFairProbability": model_fair_probability, "marketImpliedProbability": market_implied_probability,
        "estimatedEdge": estimated_edge, "evPerDollar": None, "rankWithinGame": None,
        "priceCeiling": price_ceiling, "confidence": confidence, "passReason": None,
        "comparisonMarkets": [], "betPlaced": False, "betId": None, "tickerResolutionStatus": "RESOLVED",
        "thresholdDisplay": None, "createdAt": "2026-08-03T20:00:00Z", "updatedAt": None,
        "source": "pipeline_recommendations", "validationStatus": "valid",
        "provenance": {"sourceSystem": "test", "sourceFile": None, "sourceKey": None,
                        "capturedAt": "2026-08-03T20:00:00Z", "ingestedAt": "2026-08-03T20:00:00Z"},
    }
    storage.append_records(storage.partition_path("recommendations", game_date), [rec], "recommendationId")
    return rec


# ══════════════════════════════════════════════════════════════════════════════
# resolve_recommendation_context -- unit coverage
# ══════════════════════════════════════════════════════════════════════════════

def test_resolve_recommendation_context_converts_scales_correctly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_recommendation()
    context = resolve_recommendation_context("rec-001", GAME_DATE)
    assert context["modelFairProbability"] == 0.58  # 58.0 / 100
    assert context["estimatedEdgeAtEntry"] == 3.5  # copied verbatim -- already percentage points
    assert context["confidence"] == "HIGH"
    assert context["executablePriceAtEntry"] == 0.545  # 54.5 / 100
    assert context["betUpToPriceAtEntry"] == 0.562  # 56.2 / 100
    assert context["modelEvaluationId"] == "eval-001"


def test_resolve_recommendation_context_none_when_no_id_or_date(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_recommendation()
    assert resolve_recommendation_context(None, GAME_DATE) is None
    assert resolve_recommendation_context("rec-001", None) is None
    assert resolve_recommendation_context("", "") is None


def test_resolve_recommendation_context_none_when_not_found_never_fabricated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_recommendation()
    assert resolve_recommendation_context("does-not-exist", GAME_DATE) is None
    # A date with no recommendation ledger at all -- same treatment, never an error.
    assert resolve_recommendation_context("rec-001", "2099-01-01") is None


# ══════════════════════════════════════════════════════════════════════════════
# 1. Manual bet with model-backed recommendation context
# ══════════════════════════════════════════════════════════════════════════════

def test_manual_bet_with_model_backed_recommendation_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    _seed_recommendation()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
            "recommendationId": "rec-001",
        }],
    }
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    exit_code = import_script.main()
    assert exit_code == 0

    row = list(storage.read_records(BETS_PATH))[0]
    assert row["recommendationId"] == "rec-001"
    assert row["modelEvaluationId"] == "eval-001"
    assert row["modelSupported"] is True
    assert row["modelFairProbability"] == 0.58
    assert row["estimatedEdgeAtEntry"] == 3.5
    assert row["confidence"] == "HIGH"
    assert row["executablePriceAtEntry"] == 0.545
    assert row["betUpToPriceAtEntry"] == 0.562
    assert row["manualFairProbability"] is None  # never inferred from model context
    assert row["marketFamily"] == "game_result"
    assert row["marketHorizon"] == "F5"
    assert row["side"] == "YES"


def test_explicit_row_value_wins_over_resolved_recommendation_context(tmp_path, monkeypatch):
    """A row-supplied field always overrides what the recommendation would have resolved to."""
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    _seed_recommendation()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
            "recommendationId": "rec-001", "confidence": "MEDIUM",
        }],
    }
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    import_script.main()
    row = list(storage.read_records(BETS_PATH))[0]
    assert row["confidence"] == "MEDIUM"  # explicit row value, not the recommendation's "HIGH"
    assert row["modelFairProbability"] == 0.58  # everything else still resolved


def test_record_bet_from_workflow_resolves_recommendation_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    _seed_recommendation()
    monkeypatch.setenv("GAME_DATE", GAME_DATE)
    monkeypatch.setenv("MARKET_TICKER", TICKER)
    monkeypatch.setenv("SELECTION", "SF F5 moneyline")
    monkeypatch.setenv("SIDE", "YES")
    monkeypatch.setenv("STAKE", "5")
    monkeypatch.setenv("ENTRY_PRICE", "0.55")
    monkeypatch.setenv("PLACED_AT", "2026-08-03T18:00:00Z")
    monkeypatch.setenv("RECOMMENDATION_ID", "rec-001")
    monkeypatch.setenv("RECEIPT_PATH", str(tmp_path / "receipt.json"))
    monkeypatch.delenv("ADVANCED_JSON", raising=False)
    monkeypatch.delenv("NOTES", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    exit_code = workflow_script.main()
    assert exit_code == 0
    row = list(storage.read_records(BETS_PATH))[0]
    assert row["modelEvaluationId"] == "eval-001"
    assert row["modelSupported"] is True
    assert row["modelFairProbability"] == 0.58
    assert row["confidence"] == "HIGH"
    assert row["betUpToPriceAtEntry"] == 0.562
    assert row["executablePriceAtEntry"] == 0.545
    # This script now also computes the archived-observation linkage,
    # regardless of whether a recommendation was cited.
    assert row["marketObservationLinkage"]["linkageStatus"] == "UNLINKED"  # no observations seeded


# ══════════════════════════════════════════════════════════════════════════════
# 2. Manual-only fair probability
# ══════════════════════════════════════════════════════════════════════════════

def test_manual_only_fair_probability_never_blended_with_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
            "manualFairProbability": 0.60,
        }],
    }
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    import_script.main()
    row = list(storage.read_records(BETS_PATH))[0]
    assert row["manualFairProbability"] == 0.60
    assert row["modelFairProbability"] is None
    assert row["recommendationId"] is None
    assert row["modelEvaluationId"] is None
    assert row["modelSupported"] is None


# ══════════════════════════════════════════════════════════════════════════════
# 3. Missing context stays null/unavailable -- never fabricated
# ══════════════════════════════════════════════════════════════════════════════

def test_missing_context_stays_null_no_recommendation_at_all(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
        }],
    }
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    import_script.main()
    row = list(storage.read_records(BETS_PATH))[0]
    for field in (
        "modelFairProbability", "manualFairProbability", "estimatedEdgeAtEntry",
        "executablePriceAtEntry", "betUpToPriceAtEntry", "confidence",
        "recommendationId", "modelEvaluationId",
    ):
        assert row[field] is None, f"{field} should be null, got {row[field]!r}"
    assert row["modelSupported"] is None


def test_missing_context_stays_null_recommendation_id_does_not_resolve(tmp_path, monkeypatch):
    """A recommendationId that was cited but doesn't match any real ledger
    row must never fabricate context from it -- it stays exactly as
    unresolved/null as if no recommendationId had been given at all,
    except recommendationId itself (which is always recorded verbatim,
    for audit, whether or not it resolves)."""
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
            "recommendationId": "does-not-exist",
        }],
    }
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    import_script.main()
    row = list(storage.read_records(BETS_PATH))[0]
    assert row["recommendationId"] == "does-not-exist"
    for field in ("modelFairProbability", "estimatedEdgeAtEntry", "executablePriceAtEntry", "betUpToPriceAtEntry", "confidence", "modelEvaluationId"):
        assert row[field] is None
    assert row["modelSupported"] is None


# ══════════════════════════════════════════════════════════════════════════════
# 4. Later CLV attachment does not mutate the entry snapshot
# ══════════════════════════════════════════════════════════════════════════════

def test_clv_attachment_does_not_mutate_entry_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    _seed_recommendation()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
            "recommendationId": "rec-001",
        }],
    }
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    import_script.main()
    bet_before = list(storage.read_records(BETS_PATH))[0]
    assert bet_before["clv"] is None
    assert bet_before["closingPrice"] is None

    # Mirrors scripts/edgelab/collect_clv.py's own update pattern exactly:
    # dict(bet) copy, then only clv/closingPrice/clvQuoteId/updatedAt set.
    closing_quote = {"clvQuoteId": "q1", "isClosingQuote": True, "yesBid": 48, "yesAsk": 53, "noBid": None, "noAsk": None}
    result = compute_clv_for_bet(bet_before, [closing_quote])
    assert result["clvStatus"] == "VALID"

    bet_after = dict(bet_before)
    bet_after["clv"] = result["clvCents"]
    bet_after["closingPrice"] = result["closingImpliedProbability"]
    bet_after["clvQuoteId"] = result["clvQuoteId"]
    bet_after["updatedAt"] = "2026-08-03T23:05:00Z"

    entry_context_fields = [
        "recommendationId", "modelEvaluationId", "modelSupported", "modelFairProbability",
        "manualFairProbability", "estimatedEdgeAtEntry", "confidence", "executablePriceAtEntry",
        "betUpToPriceAtEntry", "entryPrice", "stake", "marketTicker", "side", "marketFamily",
        "marketHorizon", "threshold", "entryTimestamp", "recordedAt", "createdAt",
        "marketObservationLinkage",
    ]
    for field in entry_context_fields:
        assert bet_after[field] == bet_before[field], f"{field} was mutated by CLV attachment"

    assert bet_after["clv"] is not None
    assert bet_after["closingPrice"] is not None
    assert bet_after["clvQuoteId"] == "q1"
    assert bet_after["updatedAt"] != bet_before["updatedAt"]

    # And the actual write path (storage.upsert_records, same as collect_clv.py) confirms it too.
    storage.upsert_records(BETS_PATH, [bet_after], "betId")
    persisted = list(storage.read_records(BETS_PATH))[0]
    for field in entry_context_fields:
        assert persisted[field] == bet_before[field]
    assert persisted["clv"] == result["clvCents"]


# ══════════════════════════════════════════════════════════════════════════════
# 5. Post-start observation rejected for CLV -- never used as a close
# ══════════════════════════════════════════════════════════════════════════════

def test_post_start_observation_rejected_for_clv():
    scheduled_start = "2026-08-03T23:00:00Z"
    # The only candidate quote was captured AFTER the game's scheduled
    # start -- select_closing_quote (via finalize_closing_quotes) must
    # never treat it as a valid close.
    quotes = [{
        "clvQuoteId": "post-start-1", "capturedAt": "2026-08-03T23:05:00Z",
        "marketStatus": "active", "isClosingQuote": False,
    }]
    finalized = finalize_closing_quotes(quotes, scheduled_start=scheduled_start)
    assert all(not q["isClosingQuote"] for q in finalized), "a post-start quote must never be selected as the closing quote"

    bet = {"entryPrice": 0.55, "side": "YES"}
    result = compute_clv_for_bet(bet, finalized)
    assert result["clvStatus"] == "UNAVAILABLE"
    assert result["unavailableReason"] == "NO_VALID_PRE_CLOSE_QUOTE"


def test_pregame_observation_still_accepted_for_clv_when_present():
    """Sanity counterpart to the post-start rejection above -- a genuine
    pregame quote for the same market IS selected and DOES produce a
    valid CLV, so the rejection above is a real filter, not a blanket
    failure."""
    scheduled_start = "2026-08-03T23:00:00Z"
    quotes = [
        {"clvQuoteId": "pregame-1", "capturedAt": "2026-08-03T22:45:00Z", "marketStatus": "active", "isClosingQuote": False, "yesBid": 48, "yesAsk": 53},
        {"clvQuoteId": "post-start-1", "capturedAt": "2026-08-03T23:05:00Z", "marketStatus": "active", "isClosingQuote": False},
    ]
    finalized = finalize_closing_quotes(quotes, scheduled_start=scheduled_start)
    closing = [q for q in finalized if q["isClosingQuote"]]
    assert len(closing) == 1
    assert closing[0]["clvQuoteId"] == "pregame-1"

    bet = {"entryPrice": 0.55, "side": "YES"}
    result = compute_clv_for_bet(bet, finalized)
    assert result["clvStatus"] == "VALID"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Duplicate/idempotent import preserves original context
# ══════════════════════════════════════════════════════════════════════════════

def test_duplicate_idempotent_import_preserves_original_context(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    _seed_recommendation()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
            "recommendationId": "rec-001",
        }],
    }
    raw = json.dumps(payload)

    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", raw])
    import_script.main()
    rows_first = list(storage.read_records(BETS_PATH))
    capsys.readouterr()

    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", raw])
    exit_code = import_script.main()
    stdout = capsys.readouterr().out
    receipts = json.loads(stdout)

    rows_second = list(storage.read_records(BETS_PATH))
    assert exit_code == 0
    assert len(rows_second) == len(rows_first) == 1
    assert receipts[0]["duplicateStatus"] == "DUPLICATE_NOOP"
    assert rows_second[0] == rows_first[0]
    assert rows_second[0]["modelFairProbability"] == 0.58
    assert rows_second[0]["betUpToPriceAtEntry"] == 0.562


def test_duplicate_import_after_a_second_recommendation_row_added_is_still_a_noop_for_the_first(tmp_path, monkeypatch):
    """Confirms context resolution is deterministic/idempotent even when
    the recommendation ledger itself has since grown (a second,
    independent recommendation for a different ticker was appended) --
    the original bet's own resolved context must not shift under it."""
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    _seed_recommendation()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
            "recommendationId": "rec-001",
        }],
    }
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    import_script.main()
    row_first = list(storage.read_records(BETS_PATH))[0]

    _seed_recommendation(recommendation_id="rec-002", ticker="OTHER-TICKER", model_fair_probability=99.0)

    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    exit_code = import_script.main()
    row_second = list(storage.read_records(BETS_PATH))[0]
    assert exit_code == 0
    assert row_second == row_first


# ══════════════════════════════════════════════════════════════════════════════
# 7. The rolling report (PR #64) automatically consumes captured context
# ══════════════════════════════════════════════════════════════════════════════

def test_rolling_report_automatically_consumes_captured_context_for_a_settled_bet(tmp_path, monkeypatch):
    """No report-side code change is needed for a future canonically-
    captured bet to show up correctly in the tier/calibration/CLV
    sections -- lib.edgelab.reports.build_rolling_window_report already
    reads confidence/modelFairProbability/marketFamily/clv straight off
    each bet row, so this proves the wiring end-to-end: a bet written
    through the real import path, with model-backed context resolved,
    settled and CLV-attached exactly like collect_clv.py would, lands in
    the right tier bucket and the right calibration row."""
    monkeypatch.chdir(tmp_path)
    _seed_corpus()
    _seed_recommendation()
    payload = {
        "importBatchId": "session-1",
        "rows": [{
            "sourceBetKey": "bet-01", "gameDate": GAME_DATE, "away": "SF", "home": "LAD",
            "marketTicker": TICKER, "stake": 12.0, "entryPrice": 0.55,
            "recommendationId": "rec-001",
        }],
    }
    monkeypatch.setattr(sys, "argv", ["import_bet_batch.py", "--json", json.dumps(payload)])
    import_script.main()
    bet = list(storage.read_records(BETS_PATH))[0]

    closing_quote = {"clvQuoteId": "q1", "isClosingQuote": True, "yesBid": 48, "yesAsk": 53, "noBid": None, "noAsk": None}
    clv_result = compute_clv_for_bet(bet, [closing_quote])

    settled_bet = dict(bet)
    settled_bet["status"] = "settled"
    settled_bet["result"] = "WIN"
    settled_bet["netProfitLoss"] = 9.82
    settled_bet["clv"] = clv_result["clvCents"]
    settled_bet["closingPrice"] = clv_result["closingImpliedProbability"]
    settled_bet["clvQuoteId"] = clv_result["clvQuoteId"]
    settled_bet["updatedAt"] = "2026-08-03T23:10:00Z"
    storage.upsert_records(BETS_PATH, [settled_bet], "betId")

    all_bets = list(storage.read_records(BETS_PATH))
    report = build_rolling_window_report(all_bets, window_size=30)

    assert report["windowActual"] == 1
    assert "HIGH" in report["tierBreakdown"]  # confidence resolved from the recommendation
    assert report["tierBreakdown"]["HIGH"]["count"] == 1
    assert "game_result" in report["marketFamilyBreakdown"]
    assert report["calibration"]["n"] == 1
    assert report["calibration"]["rows"][0]["predictedProbability"] == 0.58
    assert report["calibration"]["rows"][0]["probabilitySource"] == "MODEL"
    assert report["clvCoverage"]["withClv"] == 1
