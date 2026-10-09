#!/usr/bin/env python3
"""
tests/test_app_contract_v1.py
=============================
Edge Finder app export (scripts/app_export.py) against the vendored contract
(contract/edge_finder_contract/). Every test writes only under tmp_path; the
real-data smoke test READS the committed corpus and publishes into tmp_path.
"""
import copy
import hashlib
import importlib.util
import json
import os
import sys

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "contract")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from edge_finder_contract import publish, sync, timeutil  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("app_export", os.path.join(ROOT, "scripts", "app_export.py"))
app_export = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(app_export)

NOW = "2026-10-02T15:00:00Z"
DATE = "2026-10-01"
SECRET_SHAPES = ("PRIVATE KEY", "ghp_", "github_pat_", "Bearer ", "AIRTABLE")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "app-export.yml")

# ---------------------------------------------------------------------------
# synthetic data root: real committed record shapes, trimmed
# ---------------------------------------------------------------------------

_PROV = {"capturedAt": "2026-10-02T00:41:44.334Z", "ingestedAt": "2026-10-02T00:51:07Z",
         "sourceFile": "data/kalshi_registry_snapshots/kalshi_search_2026-10-01_0041.json",
         "sourceKey": None, "sourceSystem": "kalshi_registry_snapshots"}

SLATE = {
    "date": DATE, "kalshiDate": "26OCT01", "scheduleSource": "statsapi", "_officialRunAt": "2026-10-01T22:59:38Z",
    "games": [{
        "gameId": 849844, "status": "Pre-Game", "startTime": "2026-10-02T00:00:00Z", "venue": "Truist Park",
        "park": {"name": "Truist Park", "parkFactor": 101}, "scheduleSource": "statsapi",
        "away": {"team": "Philadelphia Phillies", "abbr": "PHI", "pitcher": {"name": "Aaron Nola", "id": "605400"}},
        "home": {"team": "Atlanta Braves", "abbr": "ATL", "pitcher": {"name": "Ray Kerr", "id": "678061"}},
        "lineupConfirmed": True, "lineupStatus": "confirmed", "lineupCheckedAt": "2026-10-01T22:58:57Z",
        "kalshiKey": "PHIATL", "kalshiEventTickerSuffix": "26OCT012000PHIATL", "oddsApiEventId": "abc123",
        "marketLedger": [
            {"market": "NRFI", "status": "Rejected", "modelProb": 40.55, "marketProbVF": 53.5,
             "executablePriceMarketTicker": "KXMLBRFI-26OCT011400PHIATL", "executablePriceSide": "NO",
             "netExecutableEdge": -3.872, "betUpToPriceNet": 35.04, "confidenceTier": None,
             "priceSnapshotTimestamp": "2026-10-01T22:59:02Z", "awayProjRuns": 3.69, "homeProjRuns": 4.433,
             "totalProj": 8.123, "f5AwayProj": 1.2, "f5HomeProj": 2.682},
            {"market": "ML_Away", "status": "Rejected", "modelProb": 39.42, "marketProbVF": 49.5,
             "executablePriceMarketTicker": "KXMLBGAME-26OCT011400PHIATL-PHI", "executablePriceSide": "YES",
             "netExecutableEdge": -9.1, "betUpToPriceNet": 30.0, "confidenceTier": None,
             "priceSnapshotTimestamp": "2026-10-01T22:59:02Z"},
        ],
    }],
}

GAMES = [{"actualStartTime": None, "awayTeam": "PHI", "createdAt": "2026-10-01T10:27:24Z", "doubleheaderGameNumber": None,
          "gameDate": DATE, "gameId": "2026-10-01_PHI_ATL_1400", "homeTeam": "ATL", "kalshiKey": "PHIATL",
          "mlbGamePk": "849844", "platform": "KALSHI", "scheduledStartTime": None, "schemaVersion": "1",
          "source": "kalshi_registry_snapshots", "sport": "MLB", "status": "Pre-Game",
          "supersededBy": {"canonicalGameId": "849844"}, "updatedAt": "2026-10-01T21:01:45Z", "venue": "Truist Park"}]


def _market(ticker, family, title, team=None, horizon="FULL_GAME", operator="OVER", threshold=None, series=None):
    return {"comparisonOperator": operator, "createdAt": "2026-10-02T00:51:07Z", "eventTicker": ticker.rsplit("-", 1)[0] if ticker.count("-") >= 2 else ticker,
            "gameId": "849844", "marketFamily": family, "marketHorizon": horizon, "marketTicker": ticker, "outcomeLabel": None,
            "parserStatus": "parsed", "platform": "KALSHI", "player": None, "provenance": dict(_PROV, sourceKey=ticker),
            "schemaVersion": "1", "seriesTicker": series or ticker.split("-", 1)[0], "source": "kalshi_registry_snapshots",
            "sport": "MLB", "subtitle": "", "team": team, "threshold": threshold, "title": title, "updatedAt": None,
            "validationStatus": "valid"}


MARKETS = [
    _market("KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", "team_total", "Will Atlanta score over 3.5 runs?", team="ATL", threshold=3.5),
    _market("KXMLBRFI-26OCT011400PHIATL", "first_inning_run", "1st inning: Over 0.5 runs", horizon=None),
    _market("KXMLBF3-26OCT011400PHIATL-ATL", "inning_result", "Atlanta first 3 innings winner", team="ATL", horizon="F3"),
    _market("KXMLBGAME-26OCT011400PHIATL-PHI", "game_result", "Philadelphia wins", team="PHI", operator=None),
]


def _obs(ticker, captured, yes_bid, yes_ask, last, checkpoint="POST_START", game_id="849844"):
    m = next(x for x in MARKETS if x["marketTicker"] == ticker)
    return {"awayTeam": "PHI", "capturedAt": captured, "checkpoint": checkpoint, "comparisonOperator": m["comparisonOperator"],
            "createdAt": "2026-10-02T00:43:49Z", "eventTicker": m["eventTicker"], "gameId": game_id, "homeTeam": "ATL",
            "lastPrice": last, "marketFamily": m["marketFamily"], "marketHorizon": m["marketHorizon"],
            "marketObservationId": hashlib.sha1(f"{ticker}{captured}".encode()).hexdigest(), "marketStatus": "active",
            "marketTicker": ticker, "noAsk": round(1 - yes_bid, 2), "noBid": round(1 - yes_ask, 2), "openInterest": 100.0,
            "platform": "KALSHI", "provenance": dict(_PROV, capturedAt=captured, sourceKey=ticker), "runId": "MARKET_OBSERVATION_INGEST_test",
            "schemaVersion": "1", "seriesTicker": m["seriesTicker"], "source": "kalshi_registry_snapshots", "sport": "MLB",
            "spreadCents": round(yes_ask - yes_bid, 2), "team": m["team"], "threshold": m["threshold"], "title": m["title"],
            "validationStatus": "valid", "volume": 1000.0, "yesAsk": yes_ask, "yesBid": yes_bid}


OBSERVATIONS = [
    _obs("KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", "2026-10-01T10:24:41.885Z", 0.5, 0.52, 0.5, "FIRST_DAILY", "2026-10-01_PHI_ATL_1400"),
    _obs("KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", "2026-10-02T00:41:44.334Z", 0.88, 0.9, 0.88),
    _obs("KXMLBRFI-26OCT011400PHIATL", "2026-10-02T00:41:44.334Z", 0.99, 1.0, 0.99),
    _obs("KXMLBF3-26OCT011400PHIATL-ATL", "2026-10-02T00:41:44.334Z", 0.85, 0.92, 0.93),
    _obs("KXMLBGAME-26OCT011400PHIATL-PHI", "2026-10-02T00:41:44.334Z", 0.49, 0.5, 0.49),
]


def _eval(ticker, selection, fair, implied, *, created, run_id, artifact=None, tier="RESEARCH_ONLY", quality=None,
          eval_id, checkpoint=None, family="team_total", game_id="849844", tags=()):
    return {"artifactSource": artifact, "checkpoint": checkpoint, "confidence": None, "correlationGroups": [],
            "createdAt": created, "dataQuality": quality, "dataQualityReasons": [], "estimatedEdge": None, "evPerDollar": None,
            "evaluationStatus": "EVALUATED", "eventTicker": ticker.rsplit("-", 1)[0], "gameId": game_id,
            "lineupConfirmationState": "CONFIRMED", "marketFamily": family, "marketImpliedProbability": implied,
            "marketTicker": ticker, "modelCommitSha": "774d4b735696231de21fac8e71bb01b43edbbd2b", "modelConfigVersion": "1.0",
            "modelEvaluationId": eval_id, "modelFairOdds": None, "modelFairProbability": fair,
            "modelSource": "scripts/build_market_ledger.py", "modelVersion": None, "platform": "KALSHI",
            "probabilityAdapter": "kalshiVF", "provenance": {"capturedAt": created, "ingestedAt": created, "sourceFile": None,
                                                              "sourceKey": "PHI@ATL|" + selection, "sourceSystem": "prospective_snapshot"},
            "qualityTier": tier, "recommendationId": None, "runId": run_id, "schemaVersion": "1", "selection": selection,
            "seriesTicker": ticker.split("-", 1)[0], "side": None, "source": "prospective_snapshot", "sport": "MLB",
            "thesisTags": list(tags), "threshold": None, "validationStatus": "valid"}


MODEL_EVALUATIONS = [
    _eval("KXMLBRFI-26OCT011400PHIATL", "NRFI", 40.55, 53.5, created="2026-10-01T22:56:32Z",
          run_id="PROSPECTIVE_SNAPSHOT_20261001T225632Z_79bb9870", artifact="prospective_snapshot", tier="TRUSTED_PRODUCTION",
          quality="full", eval_id="4f5ea8922a137981f3aceb5f10db63ed1a933f42", checkpoint="LINEUP_CONFIRMATION",
          family="KXMLBRFI", game_id=849844, tags=("LINEUP_EDGE",)),
    _eval("KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", "Will Atlanta score over 3.5 runs?", 64.627, 0.93,
          created="2026-10-02T12:19:26Z", run_id="RECOMMENDATION_SYNC_20261002T121926Z_gh37005883883_74c4bc6d",
          eval_id="4bcce78e47c42e050ea3916deb232f928cd00a6f", game_id="2026-10-01_PHI_ATL_1400"),
    _eval("KXMLBF3-26OCT011400PHIATL-ATL", "Atlanta first 3 innings winner", 48.458, 1.0,
          created="2026-10-02T12:19:26Z", run_id="RECOMMENDATION_SYNC_20261002T121926Z_gh37005883883_74c4bc6d",
          eval_id="231954bda57229ea790db4d422ca29400e129486", family="inning_result", game_id="2026-10-01_PHI_ATL_1400"),
    # a row the exporter must skip: no fair probability at all
    dict(_eval("KXMLBGAME-26OCT011400PHIATL-PHI", "Philadelphia wins", None, 49.5, created="2026-10-02T12:19:26Z",
               run_id="RECOMMENDATION_SYNC_20261002T121926Z_gh37005883883_74c4bc6d", eval_id="deadbeef00", family="game_result"),
         evaluationStatus="NO_MODEL_SUPPORT", qualityTier="UNSUPPORTED"),
]


def _rec(ticker, status, *, rec_id, bet_id=None, market_name=None, fair=None, implied=None, edge=None, ceiling=None,
         pass_reason=None, family="team_total", game_id="2026-10-01_PHI_ATL_1400"):
    return {"betId": bet_id, "betPlaced": bet_id is not None, "comparisonMarkets": [], "confidence": None,
            "createdAt": "2026-10-02T12:20:10Z", "estimatedEdge": edge, "evPerDollar": None, "gameId": game_id,
            "marketFamily": family, "marketImpliedProbability": implied, "marketName": market_name, "marketTicker": ticker,
            "modelEvaluationId": None, "modelFairProbability": fair, "passReason": pass_reason, "platform": "KALSHI",
            "priceCeiling": ceiling, "provenance": {"capturedAt": "2026-10-01T10:24:41.885Z", "ingestedAt": "2026-10-02T12:20:10Z",
                                                    "sourceFile": None, "sourceKey": "PHI@ATL|" + str(market_name), "sourceSystem": "pipeline_recommendations"},
            "rankWithinGame": None, "recommendationId": rec_id, "runId": "RECOMMENDATION_SYNC_20261002T121926Z_gh37005883883_74c4bc6d",
            "schemaVersion": "1", "source": "market_universe_extension", "sport": "MLB", "status": status, "thresholdDisplay": None,
            "tickerResolutionStatus": "RESOLVED", "updatedAt": "2026-10-02T12:20:10Z", "validationStatus": "valid"}


RECOMMENDATIONS = [
    _rec("KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", "BET_PLACED", rec_id="1af48120a72246b302442cb402a7e15b839bc271",
         bet_id="9639c569d18de3c69b77132290af5f320317ffb7"),
    _rec("KXMLBGAME-26OCT011400PHIATL-PHI", "PASS_NO_EDGE", rec_id="9c947d005cebd12fd29b60ec6fd41cdbadadb7f6", market_name="ML_Away",
         fair=39.42, implied=49.5, edge=-10.08, ceiling=30.0, pass_reason="Rule71: no edge", family="game_result", game_id=849844),
    _rec("KXMLBF3-26OCT011400PHIATL-ATL", "INSUFFICIENT_MODEL_SUPPORT", rec_id="000000000000000000000000000000000000ffff",
         family="inning_result"),
]


def _bet(bet_id, ticker, *, side, stake, price, contracts, status, result, pnl, source_key, recorded, fees=None,
         entry_ts=None, batch="kalshi-router-v1", tracking="REAL", record_status="ACTIVE", clv=None, family="team_total",
         game_id=None):
    return {"averageFillPrice": price, "betId": bet_id, "clv": clv, "clvConvention": "POSITIVE_IS_GOOD_V1" if clv is not None else None,
            "clvUnit": "PERCENTAGE_POINTS" if clv is not None else None, "closingPrice": None, "confidence": None,
            "contractCost": None if fees is None else round(stake - fees, 4), "contracts": contracts, "createdAt": recorded,
            "economicsSource": "EXACT_API_EXECUTION", "entryMethod": "IMPORTED_RECEIPT" if source_key else "LEGACY_BACKFILL",
            "entryPrice": price, "entryTimestamp": entry_ts, "executionStatus": "HELD_TO_SETTLEMENT", "gameDate": DATE,
            "gameId": game_id, "importBatchId": batch, "marketFamily": family, "marketHorizon": "FULL_GAME",
            "marketTicker": ticker, "modelEvaluationId": None, "netProfitLoss": pnl, "platform": "KALSHI",
            "provenance": {"capturedAt": entry_ts, "ingestedAt": recorded, "sourceFile": None, "sourceKey": None, "sourceSystem": "manual_entry"},
            "recommendationId": None, "recordStatus": record_status, "recordedAt": recorded, "result": result,
            "returnAmount": pnl, "schemaVersion": "1", "selection": family + " FULL_GAME", "side": side, "source": "MANUAL",
            "sourceBetKey": source_key, "sport": "MLB", "stake": stake, "status": status, "thesisTags": [],
            "timestampStatus": "PROVIDED" if entry_ts else "NOT_PROVIDED", "totalFees": fees, "trackingType": tracking,
            "updatedAt": "2026-10-02T12:20:10Z", "validationStatus": "valid", "wagerStructure": "SINGLE"}


BETS = [
    _bet("9639c569d18de3c69b77132290af5f320317ffb7", "KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", side="YES", stake=149.9956,
         price=0.51, contracts=289.15, status="settled", result="WIN", pnl=139.1544,
         source_key="kalshi:v1:d4b52fcd054a0b3a5f94ae26b1c069bb89bbb7419221e65e07d51bcf65881899",
         recorded="2026-10-02T00:35:16Z", fees=2.5291, clv=-3.0),
    _bet("c5509d1c6d8fc25cd3f860ba6ab44d468dae9a55", "KXMLBF3-26OCT011400PHIATL-ATL", side="YES", stake=49.9979, price=0.36,
         contracts=135.84, status="settled", result="WIN", pnl=85.8421,
         source_key="kalshi:v1:8377f605d8fef2fe8a2e6e2f9d4f2cb19de5b36aae843448c891b269397115a6",
         recorded="2026-10-02T00:35:07Z", fees=1.0955, family="inning_result"),
    # legacy manual row: aware offset timestamp, no contracts, ticker from an earlier date (-> market stub, no event)
    _bet("d291660ebfe59487a8a05accfb9854224e04a882", "KXMLBTEAMTOTAL-26JUN121840MIAPIT-MIA4", side="YES", stake=10.0,
         price=0.5745, contracts=None, status="settled", result="LOSS", pnl=-10.0, source_key=None, batch=None,
         recorded="2026-09-13T11:35:24Z", entry_ts="2026-06-12T12:51:07-04:00", game_id="2026-06-12_MIA_PIT"),
    # pending wager
    _bet("041b78d6aaaa000000000000000000000000beef", "KXMLBGAME-26OCT011400PHIATL-PHI", side="NO", stake=5.0, price=0.5,
         contracts=10.0, status="pending", result=None, pnl=None,
         source_key="kalshi:v1:0000000000000000000000000000000000000000000000000000000000000001",
         recorded="2026-10-01T23:00:00Z", fees=0.1, family="game_result"),
    # paper row: never exported
    _bet("ffff000000000000000000000000000000000000", "KXMLBGAME-26OCT011400PHIATL-PHI", side="YES", stake=1.0, price=0.5,
         contracts=2.0, status="settled", result="LOSS", pnl=-1.0, source_key=None, batch="paper", tracking="PAPER",
         recorded="2026-10-01T23:00:00Z", family="game_result"),
]


def _settlement(bet_id, ticker, result, settlement_id, *, evidence=True):
    return {"betId": bet_id, "createdAt": "2026-10-02T01:20:49Z", "gameId": "849844", "marketFamily": "team_total",
            "marketTicker": ticker, "outcome": result, "platform": "KALSHI", "realizedReturn": None, "result": result,
            "schemaVersion": "1", "settledAt": "2026-10-02T12:20:11Z",
            "settlementEvidence": {"gameStatus": "Final", "sourceSystem": "MLB_STATS_API", "kalshiOfficialResult": None} if evidence else None,
            "settlementId": settlement_id, "settlementSource": "edgelab_settle_markets", "settlementStatus": "SETTLED",
            "source": "edgelab_settlement", "sport": "MLB", "unavailableReason": None, "validationStatus": "valid",
            "wasPlaced": True, "wasRecommended": True}


SETTLEMENTS = [
    _settlement("9639c569d18de3c69b77132290af5f320317ffb7", "KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", "YES", "13ad597c2b"),
    _settlement("c5509d1c6d8fc25cd3f860ba6ab44d468dae9a55", "KXMLBF3-26OCT011400PHIATL-ATL", "YES", "bd657ea746", evidence=False),
    _settlement(None, "KXMLBRFI-26OCT011400PHIATL", "YES", "aaaa0000"),
]

DISCOVERY = {"date": DATE, "generatedAt": "2026-10-02T01:14:22Z", "contracts": [
    {"ticker": "KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", "gameId": 849844, "marketFamily": "team_total", "period": "full_game",
     "subjectType": "TEAM", "subjectId": "ATL", "side": "Over", "line": 3.5, "marketStatus": "active",
     "closeTime": "2026-10-04T18:00:00Z", "modelSupportStatus": "SUPPORTED", "realMoneyEligibilityStatus": "BLOCKED"},
    {"ticker": "KXMLBF3-26OCT011400PHIATL-ATL", "gameId": 849844, "marketFamily": "inning_result", "period": "F3",
     "subjectType": "TEAM", "subjectId": "ATL", "side": "ATL", "line": None, "marketStatus": "active",
     "closeTime": "2026-10-04T18:00:00Z", "modelSupportStatus": "SUPPORTED", "realMoneyEligibilityStatus": "NOT_GOVERNED_BY_THIS_ARTIFACT"},
]}

EXECUTION = {"meta": {"createdAt": "2026-10-01T22:59:38Z", "stage": "execution"},
             "data": {"candidates": [{"approvedPrice": 54.0, "approvedStake": None, "game": "PHI@ATL", "market": "NRFI",
                                      "realMoneyEligible": False, "rejectionReason": None,
                                      "sourceRecommendationTicker": "KXMLBRFI-26OCT011400PHIATL", "status": "Rejected", "tier": None}],
                      "date": DATE, "decision": "GO"}}

GATE = {"checkedAt": "2026-10-01T18:55:44Z", "summary": {"overall": "HEALTHY", "criticalFailureCount": 0}, "assertions": []}
DAILY_HEALTH = {"checkedAt": "2026-10-02T02:37:17Z", "date": DATE, "healthStatus": "HEALTHY"}
META = {"fetchedAt": "2026-10-02T01:11:39Z", "date": DATE, "status": "ok"}
CARD = {"bankrollStatus": "FRESH", "bettingEligibleGames": [], "date": DATE, "generatedAt": "2026-10-02T01:13:55Z",
        "numericBankrollAvailable": False}
PROVENANCE = {"data": {"capturedAt": "2026-10-02T01:11:38Z", "commitSha": "b4957764660e5bbd649f16f0e907a7af980ae71c",
                       "workflowRunId": "36949347815"}}


def _write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def make_data_root(root, *, observations=None, bets=None, model_evaluations=None):
    root = str(root)
    _write_json(os.path.join(root, "slates", DATE, "authoritative.json"), SLATE)
    _write_json(os.path.join(root, "slate.json"), SLATE)
    _write_jsonl(os.path.join(root, "edgelab", "games", f"{DATE}.jsonl"), GAMES)
    _write_jsonl(os.path.join(root, "edgelab", "markets", f"{DATE}.jsonl"), MARKETS)
    _write_jsonl(os.path.join(root, "edgelab", "observations", f"{DATE}.jsonl"), observations if observations is not None else OBSERVATIONS)
    _write_jsonl(os.path.join(root, "edgelab", "model_evaluations", f"{DATE}.jsonl"),
                 model_evaluations if model_evaluations is not None else MODEL_EVALUATIONS)
    _write_jsonl(os.path.join(root, "edgelab", "recommendations", f"{DATE}.jsonl"), RECOMMENDATIONS)
    _write_jsonl(os.path.join(root, "edgelab", "bets", "bets.jsonl"), bets if bets is not None else BETS)
    _write_jsonl(os.path.join(root, "edgelab", "settlements", f"{DATE}.jsonl"), SETTLEMENTS)
    # an empty "today" partition, exactly like the 2026-10-02 FAILED registry snapshot
    _write_jsonl(os.path.join(root, "edgelab", "markets", "2026-10-02.jsonl"), [])
    _write_json(os.path.join(root, "kalshi", "discovery", f"{DATE}.json"), DISCOVERY)
    _write_json(os.path.join(root, "pipeline", DATE, "execution.json"), EXECUTION)
    _write_json(os.path.join(root, "pipeline", DATE, "recommendations.json"), {"meta": {"createdAt": "2026-10-02T01:11:38Z"}, "data": {}})
    _write_json(os.path.join(root, "pipeline", DATE, "provenance.json"), PROVENANCE)
    _write_json(os.path.join(root, "handicapping_card", "latest.json"), CARD)
    _write_json(os.path.join(root, "edgelab", "operational_health", "production_health_gate.json"), GATE)
    _write_json(os.path.join(root, "edgelab", "health", f"{DATE}.json"), DAILY_HEALTH)
    _write_json(os.path.join(root, "meta.json"), META)
    return root


def run_export(data_root, out_root, now=NOW, **kw):
    return app_export.main(["--out", str(out_root), "--data-root", str(data_root), "--now", now,
                            "--commit-sha", "0123456789abcdef", "--workflow-run-id", "42", *kw.get("extra", [])])


def _load(out_root, name):
    with open(os.path.join(str(out_root), name), encoding="utf-8") as fh:
        return json.load(fh)


def _tree_digests(out_root):
    out = {}
    for dirpath, _dirs, files in os.walk(str(out_root)):
        for name in files:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, str(out_root))] = hashlib.sha256(fh.read()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# 1. vendored contract intact
# ---------------------------------------------------------------------------

def test_vendored_contract_matches_its_manifest():
    assert sync.check() == []


# ---------------------------------------------------------------------------
# 2. end to end on the synthetic root
# ---------------------------------------------------------------------------

@pytest.fixture
def published(tmp_path):
    data_root = make_data_root(tmp_path / "data")
    out = tmp_path / "app" / "latest"
    assert run_export(data_root, out) == 0
    return data_root, out


def test_export_end_to_end_and_tree_verifies(published):
    _data_root, out = published
    assert publish.verify_published(out) == []
    manifest = _load(out, "manifest.json")
    assert manifest["sport"] == "MLB" and manifest["source_branch"] == "main"
    counts = manifest["counts"]
    assert counts["events"] == 1
    # 4 board markets + 1 stub for the June wager
    assert counts["markets"] == 5
    # NRFI (prospective), ATL4 + F3 (adapter), ML_Away (slate ledger only); the unsupported row is skipped
    assert counts["model_prices"] == 4
    assert counts["recommendations"] == 2       # BET_PLACED + PASS; INSUFFICIENT_MODEL_SUPPORT skipped
    assert counts["wagers"] == 4                # REAL rows only; the PAPER row is never exported
    assert counts["settlements"] == 3
    assert counts["theses"] == 1
    detail_files = os.listdir(out / "event_detail")
    assert len(detail_files) == 1
    for name in ("events", "markets", "model_prices", "recommendations", "theses", "wagers", "settlements", "runs",
                 "board", "performance", "health", "manifest"):
        assert os.path.exists(out / f"{name}.json"), name


def test_event_identity_and_market_mapping(published):
    _data_root, out = published
    events = _load(out, "events.json")["items"]
    ev = events[0]
    assert ev["source_ids"]["mlb_game_pk"] == "849844"
    assert ev["source_ids"]["edgelab_game_id"] == "2026-10-01_PHI_ATL_1400"
    assert ev["start_time_utc"] == "2026-10-02T00:00:00Z"
    assert ev["start_time_confidence"] == "SCHEDULED"
    assert ev["status"] == "FINAL"   # settlement evidence says the game is Final
    assert {p["short_name"] for p in ev["participants"]} == {"PHI", "ATL"}
    markets = {m["kalshi_ticker"]: m for m in _load(out, "markets.json")["items"]}
    m = markets["KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4"]
    assert m["event_id"] == ev["event_id"]
    assert m["captured_at"] == "2026-10-02T00:41:44Z"      # the LATEST observation
    assert m["yes_bid"] == 0.88 and m["yes_ask"] == 0.9 and m["market_probability"] == 0.89
    assert m["period"] == "FULL_GAME" and m["side"] == "OVER" and m["line"] == 3.5
    assert m["market_status"] == "SETTLED"
    assert markets["KXMLBGAME-26OCT011400PHIATL-PHI"]["market_status"] == "CLOSED"   # final game, no settlement row
    stub = markets["KXMLBTEAMTOTAL-26JUN121840MIAPIT-MIA4"]
    assert stub["event_id"] is None and stub["yes_bid"] is None and stub["source"] == "bets_ledger"
    assert "raw" not in json.dumps(m["extensions"]).lower() or len(json.dumps(m["extensions"])) < 2000


def test_model_prices_are_yes_oriented_and_production_first(published):
    _data_root, out = published
    prices = {p["market_id"]: p for p in _load(out, "model_prices.json")["items"]}
    nrfi = prices["mkt_kalshi_KXMLBRFI-26OCT011400PHIATL"]
    # NRFI is the NO side of the RFI contract: P(YES) = 1 - 0.4055
    assert nrfi["fair_probability"] == 0.5945 and nrfi["market_probability"] == 0.465
    assert nrfi["support_status"] == "TRUSTED_PRODUCTION" and nrfi["data_quality_status"] == "OK"
    assert nrfi["extensions"]["selection_side"] == "NO"
    assert nrfi["extensions"]["market_ledger"]["model_prob_pct"] == 40.55
    assert nrfi["model_version"] == "1.0+774d4b735696"
    att = prices["mkt_kalshi_KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4"]
    assert att["fair_probability"] == 0.64627 and att["support_status"] == "RESEARCH_ONLY"
    assert att["edge"] == round(0.64627 - 0.0093, 6)
    ledger_only = prices["mkt_kalshi_KXMLBGAME-26OCT011400PHIATL-PHI"]
    assert ledger_only["support_status"] == "MARKET_LEDGER" and ledger_only["fair_probability"] == 0.3942
    assert ledger_only["generated_at"] == "2026-10-02T01:11:38Z" and ledger_only["data_quality_status"] == "UNKNOWN"
    runs = _load(out, "runs.json")["items"]
    assert runs[0]["source_ids"]["native_run_id"] == "RECOMMENDATION_SYNC_20261002T121926Z_gh37005883883_74c4bc6d"
    assert all(p["run_id"] == runs[0]["run_id"] for p in prices.values())


def test_recommendation_status_mapping(published):
    _data_root, out = published
    recs = {r["market_id"]: r for r in _load(out, "recommendations.json")["items"]}
    placed = recs["mkt_kalshi_KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4"]
    # NOW (10-02 15:00Z) is after the 10-02 00:00Z first pitch: the pregame recommendation is
    # history, never an actionable candidate (test_started_game_* covers the pregame side).
    assert placed["status"] == "EXPIRED" and placed["authority"] == "MANUAL" and placed["research_only"] is False
    assert placed["reason_not_playable"].startswith("GAME_STARTED")
    assert placed["extensions"]["native_status"] == "BET_PLACED"
    assert placed["selection"] == "YES" and placed["extensions"]["side_basis"] == "PLACED_BET_SIDE"
    assert placed["stake_dollars"] is None and placed["bankroll_basis"] is None
    passed = recs["mkt_kalshi_KXMLBGAME-26OCT011400PHIATL-PHI"]
    assert passed["status"] == "PASS" and passed["research_only"] is True
    assert passed["fair_probability"] == 0.3942 and passed["current_price"] == 0.495 and passed["bet_up_to_price"] == 0.3
    assert passed["reason_not_playable"] == "Rule71: no edge"
    assert passed["source_ids"]["recommendation_id"] == "9c947d005cebd12fd29b60ec6fd41cdbadadb7f6"


def test_wagers_settlements_and_ledger_pnl(published):
    _data_root, out = published
    wagers = {w["kalshi_ticker"]: w for w in _load(out, "wagers.json")["items"]}
    settlements = {s["settlement_id"]: s for s in _load(out, "settlements.json")["items"]}
    assert set(wagers) == {"KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4", "KXMLBF3-26OCT011400PHIATL-ATL",
                           "KXMLBTEAMTOTAL-26JUN121840MIAPIT-MIA4", "KXMLBGAME-26OCT011400PHIATL-PHI"}
    router = wagers["KXMLBTEAMTOTAL-26OCT011400PHIATL-ATL4"]
    assert router["source"] == "KALSHI_ROUTER" and router["source_bet_key"].startswith("kalshi:v1:")
    assert router["fees"] == 2.5291 and router["average_price"] == 0.51 and router["contracts"] == 289.15
    assert router["placed_at"] == "2026-10-02T00:35:16Z" and router["extensions"]["placed_at_basis"] == "RECORDED_AT"
    assert router["settlement_status"] == "SETTLED" and router["settlement_id"] in settlements
    assert router["profit_loss"] == 139.1544 and router["payout"] is None
    assert router["event_id"] is not None
    # temporal linkage: the ATL4 adapter price (12:19 next day) and the market's (latest) capture
    # (00:41 next day) are both AFTER the wager, so neither links -- never the nearest one
    assert router["model_price_id"] is None and router["model_run_id"] is None
    assert router["linkage"]["market_captured_at"] is None
    assert router["linkage"]["wager_placed_at"] == router["placed_at"]
    assert router["recommendation_id"] is None     # BET_PLACED row was synced after placement
    legacy = wagers["KXMLBTEAMTOTAL-26JUN121840MIAPIT-MIA4"]
    assert legacy["source"] == "LEGACY_IMPORT" and legacy["placed_at"] == "2026-06-12T16:51:07Z"
    assert legacy["contracts"] == round(10.0 / 0.5745, 4) and legacy["extensions"]["contracts_basis"] == "DERIVED_STAKE_OVER_ENTRY_PRICE"
    assert legacy["fees"] is None and legacy["event_id"] is None
    legacy_settlement = settlements[legacy["settlement_id"]]
    assert legacy_settlement["result"] == "LOST" and legacy_settlement["verification_status"] == "UNVERIFIED"
    assert legacy_settlement["winning_side"] is None
    s = settlements[router["settlement_id"]]
    assert s["result"] == "WON" and s["winning_side"] == "YES" and s["verification_status"] == "MODEL_DERIVED"
    assert s["net_pnl"] == 139.1544 and s["gross_payout"] is None and s["market_id"] == router["market_id"]
    pending = wagers["KXMLBGAME-26OCT011400PHIATL-PHI"]
    assert pending["settlement_status"] == "PENDING" and pending["settlement_id"] is None and pending["selection"] == "NO"
    for w in wagers.values():
        if w["settlement_status"] == "SETTLED":
            assert w["settlement_id"] in settlements and settlements[w["settlement_id"]]["wager_id"] == w["wager_id"]
    perf = _load(out, "performance.json")
    ledger_pnl = round(sum(b["netProfitLoss"] for b in BETS if b["trackingType"] == "REAL" and b["status"] == "settled"), 4)
    assert perf["totals"]["net_pnl"] == ledger_pnl
    assert perf["totals"]["wagers"] == 4 and perf["totals"]["settled"] == 3 and perf["totals"]["pending"] == 1
    assert perf["clv"]["wagers_with_clv"] == 1 and perf["clv"]["mean_clv"] == -0.03
    assert perf["bankroll"]["available"] is False


def test_health_board_and_freshness_on_the_synthetic_root(published):
    _data_root, out = published
    health = _load(out, "health.json")
    assert health["bet_authority"] == "MANUAL"
    assert health["thresholds"]["market_data"] == {"fresh_after_seconds": 1200, "stale_after_seconds": 7200}
    assert health["thresholds"]["model"] == {"fresh_after_seconds": 1800, "stale_after_seconds": 21600}
    assert health["last_market_capture"] == "2026-10-02T00:41:44Z"
    assert health["last_model_generated"] == "2026-10-02T12:19:26Z"
    # 15:00 vs a 00:41 capture: market data is past the 2h stale threshold
    assert health["market_data_status"] == "STALE" and health["overall_status"] == "STALE"
    assert health["components"]["production_gate"]["status"] == "OK"
    assert health["components"]["export"]["status"] == "OK" and health["errors"] == []
    assert any("no slate for today" in w for w in health["warnings"])
    board = _load(out, "board.json")
    assert board["count"] == 1 and board["items"][0]["markets_available"] == 4
    assert board["items"][0]["markets_priced"] == 4 and board["items"][0]["wagers_count"] == 3
    assert board["overall_status"] == health["overall_status"]
    detail = _load(out, board["items"][0]["detail_path"])
    assert len(detail["markets"]) == 4 and len(detail["wagers"]) == 3 and len(detail["settlements"]) == 2
    assert detail["theses"][0]["summary"] is None
    assert detail["theses"][0]["evidence"]["projected_runs"]["total"] == 8.123


# ---------------------------------------------------------------------------
# 3. determinism
# ---------------------------------------------------------------------------

def test_two_runs_with_the_same_now_are_byte_identical(tmp_path):
    data_root = make_data_root(tmp_path / "data")
    a, b = tmp_path / "a" / "latest", tmp_path / "b" / "latest"
    assert run_export(data_root, a) == 0
    assert run_export(data_root, b) == 0
    assert _tree_digests(a) == _tree_digests(b)
    ma, mb = _load(a, "manifest.json"), _load(b, "manifest.json")
    assert {k: v["sha256"] for k, v in ma["files"].items()} == {k: v["sha256"] for k, v in mb["files"].items()}


# ---------------------------------------------------------------------------
# 4. failure safety
# ---------------------------------------------------------------------------

def test_a_broken_input_leaves_the_previous_payload_untouched(tmp_path, capsys):
    data_root = make_data_root(tmp_path / "data")
    out = tmp_path / "app" / "latest"
    assert run_export(data_root, out) == 0
    before = _tree_digests(out)
    with open(os.path.join(data_root, "edgelab", "bets", "bets.jsonl"), "a", encoding="utf-8") as fh:
        fh.write("{this is not json\n")
    assert run_export(data_root, out, now="2026-10-02T15:30:00Z") == 1
    after = _tree_digests(out)
    assert {k: v for k, v in after.items() if k != "health.json"} == {k: v for k, v in before.items() if k != "health.json"}
    health = _load(out, "health.json")
    assert health["overall_status"] in ("DEGRADED", "STALE", "UNAVAILABLE")
    assert health["components"]["export"]["status"] == "DEGRADED"
    assert health["errors"] and "JSONDecodeError" in health["errors"][0]
    assert health["payload_run_id"] == _load(out, "manifest.json")["run_id"]
    assert health["last_export_attempt"] == "2026-10-02T15:30:00Z"
    assert publish.verify_published(out) == []


def test_failure_with_no_previous_payload_is_unavailable(tmp_path):
    out = tmp_path / "app" / "latest"
    assert run_export(tmp_path / "nowhere", out) == 1
    health = _load(out, "health.json")
    assert health["overall_status"] == "UNAVAILABLE" and health["payload_run_id"] is None
    assert os.listdir(out) == ["health.json"]


# ---------------------------------------------------------------------------
# 5. stale data
# ---------------------------------------------------------------------------

def test_far_future_now_reports_stale(tmp_path):
    data_root = make_data_root(tmp_path / "data")
    out = tmp_path / "app" / "latest"
    assert run_export(data_root, out, now="2026-12-25T12:00:00Z") == 0
    health = _load(out, "health.json")
    assert health["overall_status"] == "STALE"
    assert health["model_status"] == "STALE" and health["market_data_status"] == "STALE"
    assert health["freshness_status"] == "STALE"
    board = _load(out, "board.json")
    assert "STALE_DATA" in board["items"][0]["health_flags"]


# ---------------------------------------------------------------------------
# 6. naive timestamps never leave the adapter
# ---------------------------------------------------------------------------

def test_naive_timestamp_in_an_input_is_refused(tmp_path):
    naive = copy.deepcopy(OBSERVATIONS)
    naive[1]["capturedAt"] = "2026-10-02T00:41:44"          # no zone
    data_root = make_data_root(tmp_path / "data", observations=naive)
    out = tmp_path / "app" / "latest"
    assert run_export(data_root, out) == 1
    assert os.listdir(out) == ["health.json"]
    health = _load(out, "health.json")
    assert "NaiveTimestampError" in health["errors"][0] or "no zone" in health["errors"][0]


def test_every_emitted_timestamp_is_canonical_utc(published):
    _data_root, out = published
    ts_keys = ("_at", "_utc", "as_of", "placed_at", "settled_at", "generated_at", "created_at")

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and any(path.endswith(k) for k in ts_keys) and "T" in node and node[:4].isdigit():
            assert timeutil.is_canonical(node), (path, node)

    for dirpath, _d, files in os.walk(str(out)):
        for name in files:
            with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                walk(json.load(fh), name)


# ---------------------------------------------------------------------------
# 7. the real production proof
# ---------------------------------------------------------------------------

REAL_DATA = os.path.join(ROOT, "data")


def _real_data_present():
    markets = os.path.join(REAL_DATA, "edgelab", "markets")
    bets = os.path.join(REAL_DATA, "edgelab", "bets", "bets.jsonl")
    return os.path.isdir(markets) and any(n.endswith((".jsonl", ".jsonl.gz")) for n in os.listdir(markets)) and os.path.exists(bets)


@pytest.mark.skipif(not _real_data_present(), reason="committed data/edgelab corpus not present in this checkout")
def test_real_committed_data_exports_and_verifies(tmp_path):
    out = tmp_path / "app" / "latest"
    code = app_export.main(["--out", str(out), "--data-root", REAL_DATA, "--now", NOW])
    assert code == 0, "export of the committed corpus failed; see health.json"
    assert publish.verify_published(out) == []
    manifest = _load(out, "manifest.json")
    counts = manifest["counts"]
    assert counts["events"] >= 1 and counts["markets"] >= 1 and counts["runs"] == 1
    assert counts["model_prices"] >= 1
    health = _load(out, "health.json")
    assert health["overall_status"] in ("HEALTHY", "DEGRADED", "STALE", "RESEARCH_ONLY")
    assert health["bet_authority"] == "MANUAL"
    # wagers <-> settlements cross reference, and P&L equals the ledger's own sum
    wagers = _load(out, "wagers.json")["items"]
    settlements = {s["settlement_id"]: s for s in _load(out, "settlements.json")["items"]}
    assert all(w["settlement_id"] in settlements for w in wagers if w["settlement_status"] == "SETTLED")
    assert all(s["wager_id"] in {w["wager_id"] for w in wagers} for s in settlements.values())
    from lib.edgelab import storage
    ledger = [b for b in storage.read_records(os.path.join(REAL_DATA, "edgelab", "bets", "bets.jsonl"))
              if b.get("trackingType") in ("REAL", "REAL_PROBE") and b.get("recordStatus", "ACTIVE") == "ACTIVE"]
    assert len(wagers) == len(ledger)
    ledger_pnl = round(sum(b["netProfitLoss"] for b in ledger if b.get("status") == "settled" and b.get("netProfitLoss") is not None), 4)
    perf = _load(out, "performance.json")
    assert perf["totals"]["net_pnl"] == ledger_pnl
    assert perf["totals"]["stake"] == round(sum(float(b["stake"]) for b in ledger), 4)
    events = _load(out, "events.json")["items"]
    assert all(e["source_ids"].get("mlb_game_pk") for e in events)
    assert all(e["start_time_confidence"] in ("SCHEDULED", "VERIFIED", "PLACEHOLDER") for e in events)


# ---------------------------------------------------------------------------
# 8. no secret-shaped strings
# ---------------------------------------------------------------------------

def test_no_secret_shaped_strings_in_any_output(published):
    _data_root, out = published
    for dirpath, _d, files in os.walk(str(out)):
        for name in files:
            with open(os.path.join(dirpath, name), encoding="utf-8") as fh:
                text = fh.read()
            for shape in SECRET_SHAPES:
                assert shape not in text, (name, shape)


# ---------------------------------------------------------------------------
# 9. workflow
# ---------------------------------------------------------------------------

def test_app_export_workflow_is_wired_like_the_other_data_workflows():
    with open(WORKFLOW, encoding="utf-8") as fh:
        source = fh.read()
    doc = yaml.safe_load(source)
    triggers = doc.get("on") or doc.get(True)
    assert "workflow_dispatch" in triggers and "schedule" in triggers
    assert triggers["schedule"][0]["cron"].startswith("*/30 ")
    watched = set(triggers["workflow_run"]["workflows"])
    assert watched == {"Fetch Slate Data", "Build Handicapping Card", "Prospective Model Snapshots (Scheduled)",
                       "EdgeLab Market Capture", "EdgeLab Postgame Settlement", "EdgeLab Settlement Reconcile",
                       "Refresh MLB Game State"}
    assert triggers["workflow_run"]["types"] == ["completed"]
    # actions: write is for scripts/ci/pipeline_watchdog.py only (tests/test_pipeline_watchdog.py)
    assert doc["permissions"] == {"contents": "write", "actions": "write"}
    assert doc["concurrency"]["cancel-in-progress"] is False
    assert "scripts/app_export.py" in source and "--out app/latest" in source
    assert "scripts/ci/git_data_commit.py" in source and '"app/latest/"' in source
    assert "requirements-ci.txt" in source and "python-version: '3.11'" in source
    # the exporter's own exit code must decide the job: the commit step runs always, then the gate fails
    assert "if: always()" in source and "steps.export.outcome" in source
    # the workflow_run names exist exactly as spelled in those files
    for name in watched:
        found = False
        for wf in os.listdir(os.path.join(ROOT, ".github", "workflows")):
            with open(os.path.join(ROOT, ".github", "workflows", wf), encoding="utf-8") as fh:
                if yaml.safe_load(fh).get("name") == name:
                    found = True
                    break
        assert found, name


def test_card_pointer_resolves_to_the_dated_card(tmp_path):
    """handicapping_card/latest.json is a pointer whose bettingEligibleGames is a COUNT; the exporter must
    read the dated card it names (the per-game market lists live there) and never iterate the count."""
    card_dir = tmp_path / "handicapping_card"
    card_dir.mkdir()
    full = {"date": "2026-10-03", "bettingEligibleGames": [
        {"markets": [{"ticker": "KXMLBGAME-26OCT03NYYBOS-NYY", "realMoneyEligible": True},
                     {"ticker": "KXMLBGAME-26OCT03NYYBOS-BOS", "realMoneyEligible": False}]}]}
    (card_dir / "2026-10-03.json").write_text(json.dumps(full))
    (card_dir / "latest.json").write_text(json.dumps(
        {"date": "2026-10-03", "bettingEligibleGames": 1, "path": "data/handicapping_card/2026-10-03.json"}))
    card = app_export._load_card(str(tmp_path))
    assert card == full
    assert app_export._real_money_tickers({"card": card, "execution": None}) == {"KXMLBGAME-26OCT03NYYBOS-NYY"}
    # a pointer whose card is missing yields no card, and a bare count is never iterated
    (card_dir / "2026-10-03.json").unlink()
    assert app_export._load_card(str(tmp_path)) is None
    assert app_export._real_money_tickers({"card": {"bettingEligibleGames": 2}, "execution": None}) == set()


# ---------------------------------------------------------------------------
# 10. event status: settlement > game-state feed > slate snapshot; an old first-pitch LIVE says so
# ---------------------------------------------------------------------------

def _game_state(abstract, detailed, as_of="2026-10-02T03:30:00Z"):
    return {"schema_version": "mlb_game_state/1.0.0", "date": DATE, "as_of": as_of, "source": "statsapi.mlb.com/api/v1/schedule",
            "games": {"849844": {"abstract_game_state": abstract, "detailed_state": detailed, "start_time_utc": "2026-10-02T00:00:00Z"}}}


def _event(tmp_path, *, game_state=None, now=NOW, settlements=None):
    root = make_data_root(tmp_path / "data")
    if game_state is not None:
        _write_json(os.path.join(root, "slates", DATE, "game_state.json"), game_state)
    if settlements is not None:
        _write_jsonl(os.path.join(root, "edgelab", "settlements", f"{DATE}.jsonl"), settlements)
    out = tmp_path / "out"
    assert run_export(root, out, now=now) == 0
    ev = _load(out, "events.json")["items"][0]
    return ev["status"], ev["extensions"].get("status_basis"), ev["extensions"].get("status_as_of")


def test_a_finished_game_is_final_from_the_game_state_feed_not_live_until_settlement(tmp_path):
    # The slate still says Pre-Game at 15:00Z (first pitch 00:00Z); without the feed the first-pitch gate holds LIVE.
    status, basis, as_of = _event(tmp_path, game_state=_game_state("Final", "Final"), settlements=[])
    assert (status, basis, as_of) == ("FINAL", "STATSAPI_GAME_STATE", "2026-10-02T03:30:00Z")


def test_game_state_live_postponed_and_preview_map_to_the_contract(tmp_path):
    assert _event(tmp_path, game_state=_game_state("Live", "In Progress"), settlements=[])[:2] == ("LIVE", "STATSAPI_GAME_STATE")
    assert _event(tmp_path, game_state=_game_state("Final", "Postponed"), settlements=[])[:2] == ("POSTPONED", "STATSAPI_GAME_STATE")
    # Preview before first pitch stays a pregame SCHEDULED; after first pitch the gate still turns it LIVE.
    assert _event(tmp_path, game_state=_game_state("Preview", "Pre-Game"), settlements=[], now="2026-10-01T23:00:00Z")[:2] == ("SCHEDULED", "STATSAPI_GAME_STATE")
    assert _event(tmp_path, game_state=_game_state("Preview", "Pre-Game"), settlements=[], now="2026-10-02T01:00:00Z")[:2] == ("LIVE", "FIRST_PITCH_PASSED")


def test_settlement_final_outranks_a_game_state_that_still_says_live(tmp_path):
    status, basis, _ = _event(tmp_path, game_state=_game_state("Live", "In Progress"))
    assert (status, basis) == ("FINAL", "SETTLEMENT_FINAL")


def test_an_old_first_pitch_live_with_no_word_is_marked_unconfirmed_and_warned(tmp_path):
    root = make_data_root(tmp_path / "data")
    _write_jsonl(os.path.join(root, "edgelab", "settlements", f"{DATE}.jsonl"), [])
    out = tmp_path / "out"
    assert run_export(root, out, now="2026-10-02T07:30:00Z") == 0   # 7.5h after first pitch, nothing says it ended
    ev = _load(out, "events.json")["items"][0]
    assert (ev["status"], ev["extensions"]["status_basis"]) == ("LIVE", "FIRST_PITCH_PASSED_UNCONFIRMED")
    health = _load(out, "health.json")
    assert any("no schedule or settlement word" in w for w in health["warnings"])
    # Two hours after first pitch the same silence is ordinary.
    out2 = tmp_path / "out2"
    assert run_export(root, out2, now="2026-10-02T02:00:00Z") == 0
    assert _load(out2, "events.json")["items"][0]["extensions"]["status_basis"] == "FIRST_PITCH_PASSED"


# ---------------------------------------------------------------------------
# 11. an MLB off day: market capture idle is NOT_APPLICABLE, never STALE -- on current evidence only
# ---------------------------------------------------------------------------

_SERIES = ("KXMLBGAME", "KXMLBTOTAL", "KXMLBRFI")


def _capture(fetched_at, *, markets=0, truncated=(), failures=0, pagination=True):
    """A raw registry capture for TODAY (2026-10-02 ET) shaped like data/kalshi_registry_snapshots/
    kalshi_search_2026-10-09_1112.json: every series answered HTTP 200, records only for other
    dates, the broad discovery pass truncated -- and therefore labelled captureStatus FAILED."""
    pages = [{"scope": "series", "series": s, "complete": s not in truncated, "recordsReceived": 7,
              "truncationReason": "MAX_PAGES" if s in truncated else None} for s in _SERIES]
    pages.append({"scope": "discovery", "series": None, "complete": False, "recordsReceived": 40000,
                  "truncationReason": "ENTRY_CAP"})
    rows = [{"market_ticker": f"KXMLBGAME-26OCT021900PHIATL-{i}", "event_ticker": "KXMLBGAME-26OCT021900PHIATL"}
            for i in range(markets)]
    return {"date": "2026-10-02", "kalshi_date": "26OCT02", "fetched_at": fetched_at, "total_markets": markets,
            "markets": rows, "series_counts": {s: 0 for s in _SERIES}, "captureStatus": "FAILED" if not markets else "COMPLETE",
            "captureContractVersion": "kalshi_capture_v4", "fetchFailures": [{"scope": "series"}] * failures,
            "fetchFailureCount": failures, "pagination": pages if pagination else [],
            "exclusions": {"event_ticker_not_for_this_slate_date": 21}}


def _off_day_health(tmp_path, capture, *, settlements=None, now=NOW):
    root = make_data_root(tmp_path / "data")
    if capture is not None:
        _write_json(os.path.join(root, "kalshi_registry_snapshots", "kalshi_search_2026-10-02_1450.json"), capture)
    if settlements is not None:
        _write_jsonl(os.path.join(root, "edgelab", "settlements", f"{DATE}.jsonl"), settlements)
    out = tmp_path / "out"
    assert run_export(root, out, now=now) == 0
    assert publish.verify_published(out) == []
    return _load(out, "health.json"), _load(out, "events.json")


def test_an_off_day_with_a_current_empty_capture_is_idle_not_stale(tmp_path):
    """2026-10-09 regression: no MLB game listed for today, yesterday's only game FINAL, and health
    still said market_data STALE (57,000 s) for yesterday's last pre-close quote."""
    h, events = _off_day_health(tmp_path, _capture("2026-10-02T14:50:00.000Z"))
    assert [e["status"] for e in events["items"]] == ["FINAL"]
    assert h["market_data_status"] == "NOT_APPLICABLE" and h["model_status"] == "NOT_APPLICABLE"
    assert h["overall_status"] == "HEALTHY" and h["freshness_status"] == "FRESH"
    md = h["components"]["market_data"]
    assert md["as_of"] == "2026-10-02T14:50:00Z" and md["age_seconds"] == 600.0
    assert "kalshi_search_2026-10-02_1450.json found 0 markets" in md["detail"]
    # the real last observation is still reported as such
    assert h["last_market_capture"] == "2026-10-02T00:41:44Z"
    assert any(w.startswith("market capture idle: no MLB market listed for 2026-10-02") for w in h["warnings"])
    assert any("no slate for today" in w for w in h["warnings"])


@pytest.mark.parametrize("capture, settlements, why", [
    (None, None, "no capture for today at all (a capture outage)"),
    (_capture("2026-10-02T12:30:00.000Z"), None, "the empty capture is older than the 2h stale threshold"),
    (_capture("2026-10-02T14:50:00.000Z", failures=1), None, "a recorded fetch failure"),
    (_capture("2026-10-02T14:50:00.000Z", pagination=False), None, "no per-series evidence (workflow FAILED stub)"),
    (_capture("2026-10-02T14:50:00.000Z", truncated=("KXMLBTOTAL",)), None, "a series did not paginate to exhaustion"),
    (_capture("2026-10-02T14:50:00.000Z", markets=2), None, "today has markets"),
    (_capture("2026-10-02T14:50:00.000Z"), [], "the exported slate's game has no final word (still LIVE)"),
])
def test_anything_short_of_current_complete_evidence_stays_stale(tmp_path, capture, settlements, why):
    h, _events = _off_day_health(tmp_path, capture, settlements=settlements)
    assert h["market_data_status"] == "STALE" and h["overall_status"] == "STALE", why
    assert not any(w.startswith("market capture idle") for w in h["warnings"]), why
