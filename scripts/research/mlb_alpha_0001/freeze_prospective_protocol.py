#!/usr/bin/env python3
"""MLB-ALPHA-0001 Sections D/E/L: freeze the C01-PIT PROSPECTIVE SHADOW
protocol -- the official trigger stream and the checkpoint thresholds --
BEFORE any prospective outcome exists.

Refuses to overwrite an existing freeze. RESEARCH ONLY.
"""

import hashlib
import json
import os
import sys

ART = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
OUT = os.path.join(ART, "frozen_prospective_protocol.json")

C01_PIT_RULE_SHA256 = (
    "882f16d8330af1af12aec928a561302bfe81de6a5e5716a3a7fa352bc048376b")

PROTOCOL = {
    "program": "MLB-ALPHA-0001",
    "shadowId": "MLB-ALPHA-0001-C01-PIT-SHADOW-V1",
    "candidateId": "MLB-ALPHA-0001-C01-PIT",
    "candidateRuleSha256": C01_PIT_RULE_SHA256,
    "evidenceLevel": "E4_PROSPECTIVE_SHADOW",
    "realMoney": False,

    "marketRule": {
        "universe": "KXMLBF5TOTAL contracts (marketFamily=inning_total)",
        "side": "BUY_YES",
        "executablePrice": "archived yesAsk (top of book)",
        "priceBandCentsInclusive": [90, 99],
        "entryWindowMinutesBeforeStart": {"openAt": 60, "closeAt": 0},
        "entrySelection": "FIRST_QUALIFYING_QUOTE_IN_WINDOW",
        "settlement": "Kalshi AT_LEAST_N (rung N pays YES iff F5 runs >= N)",
        "order": "USD 10 taker, whole contracts, Tier C realistic execution",
        "unchangedFromHoldout": True,
    },

    # ---- Section D: the trigger stream is part of the strategy identity ----
    "triggerStream": {
        "streamId": "c01pit_trigger_v1",
        "pollIntervalMinutes": 10,
        "activeWindowMinutesBeforeStart": {"openAt": 60, "closeAt": 0},
        "onlyThisStreamMayTrigger": True,
        "provenanceFlag": "canTriggerC01Pit",
        "rationale": (
            "'FIRST qualifying observation' is meaningless without naming the "
            "sampling process. The cadence audit "
            "(data/edgelab/research_artifacts/mlb_alpha_0001/cadence_audit.json) "
            "measured the HISTORICAL streams that produced C01-PIT entries: "
            "kalshi_registry_snapshots and standalone_price_check, with a median "
            "inter-capture gap of 76-119 minutes -- WIDER than the 60-minute "
            "entry window itself. 51.9% of eligible contracts had NO in-window "
            "observation at all (discovery 46.5%, validation 41.3%, spent "
            "holdout 77.9%), and two holdout dates had none whatsoever."),
        "honestDifferenceFromHistory": (
            "A 10-minute in-window poll is DENSER than the historical cadence. "
            "Prospective entries will therefore tend to fire earlier within the "
            "window and to occur more often than the historical opportunity "
            "rate. The MARKET rule is byte-identical, but the SAMPLING PROCESS "
            "is deliberately re-specified and frozen here. This is disclosed, "
            "not glossed: prospective results are comparable to the historical "
            "record at the level of the market rule, NOT at the level of the "
            "trigger process, and the shadow must never be described as a "
            "like-for-like continuation of the historical opportunity stream."),
    },

    # ---- Section E: research-only observational quotes may never trigger ----
    "observationalStreams": {
        "purpose": ("higher-frequency captures for CLV, spread dynamics, depth, "
                    "price movement and closing-quote accuracy"),
        "canTriggerC01Pit": False,
        "mayAlterOfficialEntry": False,
        "examples": ["kalshi_registry_snapshots", "standalone_price_check",
                     "c01pit_observational_v1"],
        "rule": ("Every persisted observation carries an explicit "
                 "canTriggerC01Pit boolean. Only c01pit_trigger_v1 observations "
                 "may create an official shadow entry; every other capture is "
                 "research context and can never change an entry, its price, or "
                 "its timing."),
    },

    # ---- Section L: checkpoints frozen before the first outcome ----
    "checkpoints": {
        "firstMaterial": {"independentGames": 100, "independentDates": 10},
        "stronger": {"independentGames": 200, "independentDates": 20},
        "mayNotBeLowered": True,
        "reportAtCheckpoint": [
            "opportunities", "independent games", "independent dates",
            "wins/losses", "entry-price distribution", "post-fee net ROI",
            "net P/L", "max drawdown", "game-clustered uncertainty",
            "date concentration", "executable CLV", "fair-mid CLV",
            "depth/fillability coverage", "settlement agreement rate",
        ],
        "reachingACheckpointAuthorizes": "a REVIEW only -- never a wager",
    },

    "settlementTruth": {
        "requireExchangeCrossCheck": True,
        "onMismatch": "quarantine the research row and alert; never overwrite either source",
        "quarantinedRowsCountTowardCheckpoints": False,
    },

    "productionFirewall": {
        "productionRecommendationIntegration": False,
        "stakingIntegration": False,
        "riskGateIntegration": False,
        "placesOrders": False,
        "note": "Research-only. A real-money decision requires explicit CEO review.",
    },
}


def main():
    if os.path.exists(OUT):
        print("REFUSING: %s already exists (protocol is frozen)" % OUT)
        return 1
    body = json.dumps(PROTOCOL, indent=2, sort_keys=True)
    digest = hashlib.sha256(body.encode()).hexdigest()
    doc = dict(PROTOCOL)
    doc["protocolSha256"] = digest
    trigger_digest = hashlib.sha256(
        json.dumps(PROTOCOL["triggerStream"], sort_keys=True).encode()).hexdigest()
    doc["triggerStreamSha256"] = trigger_digest
    with open(OUT, "w") as fh:
        json.dump(doc, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print("frozen:", OUT)
    print("protocolSha256     :", digest)
    print("triggerStreamSha256:", trigger_digest)
    print("candidateRuleSha256:", C01_PIT_RULE_SHA256)
    return 0


if __name__ == "__main__":
    sys.exit(main())
