"""
lib/handicap_runtime.py
=======================
The COMPACT SERVING LAYER for MLB handicapping.

WHAT PROBLEM THIS SOLVES
------------------------
`data/handicapping_card/<date>.json` is the archival, real-money
handicapping card. On 2026-09-18 it is 8.6 MB, because it carries every
one of the day's 4,247 attributable Kalshi contracts as a 49-field JSON
object that repeats the same matchup, date, scheduled start, eligibility
flag and 150-character `modelProbabilityNote` on every single row.

That file is CORRECT and stays exactly as it is. It is the archive.

What it is not is a thing a chat handicapper can read. A fresh session
had to ingest the whole card plus six governance documents
(HANDICAPPING_PLAYBOOK, PLAYBOOK_LESSONS, RULES, MODEL_CORE,
SLATE_WORKFLOW, DATA_SOURCES) before it could look at a single price.

THE FIX IS REPRESENTATION, NEVER SELECTION
------------------------------------------
This module does NOT filter. Read that again, because every plausible
"optimisation" here is a bug:

  * it does not rank, shortlist or preselect markets;
  * it does not drop markets without a production adapter;
  * it does not drop markets without automatic settlement support;
  * it does not drop pitcher or hitter props;
  * it does not drop closed, unclassified or registry-excluded
    contracts;
  * it does not compute, and therefore cannot filter on, model edge.

Every market on the card for a game is in that game's bundle. The
`verify` function below proves it by ticker set equality, and the build
refuses to write anything it cannot prove.

The size comes from three lossless moves:

  1. NORMALISE. Matchup, date, teams, scheduled start, eligibility and
     the model-probability disclaimer are game-level facts. They appear
     once in the bundle's header instead of once per market row.
  2. COLUMNARISE. Markets become a `columns`/`rows` table instead of
     4,247 objects each repeating 49 key names.
  3. INTERN. Low-cardinality strings (event ticker, series, family,
     scope, capture timestamp, the five axis verdicts) become integer
     indices into a per-bundle legend.

None of the three can lose a market or change a price, and
`expand_market_table` is the exact inverse of `build_market_table`, so
the claim is testable rather than asserted.

A SECOND MARKET DISCOVERY IMPLEMENTATION WOULD BE THE REAL DANGER
-----------------------------------------------------------------
So there isn't one. This module takes an ALREADY-BUILT card as input and
projects it. It never reads a Kalshi snapshot, never normalises a raw
contract, never applies the registry and never decides eligibility. Those
live in `lib/kalshi_price_check.py`, `lib/kalshi_mlb_single_game_registry.py`,
`lib/betting_eligibility.py` and `lib/contract_accounting.py`, and they
stay the only implementations. If the card's semantics change, this layer
changes with them for free, because it is downstream of them.
"""
from __future__ import annotations

import gzip
import json
import os

SCHEMA_VERSION = "1"
MANIFEST_ARTIFACT_TYPE = "MLB_HANDICAP_RUNTIME_MANIFEST"
BUNDLE_ARTIFACT_TYPE = "MLB_HANDICAP_GAME_BUNDLE"

RUNTIME_ROOT = os.path.join("data", "handicap_runtime")

#: The card fields carried into every compact market row, in row order.
#:
#: WHAT IS *NOT* HERE, AND WHY. A field is omitted only when it is
#: (a) a game-level constant now in the bundle header -- `matchup`,
#: `date`, `awayTeam`, `homeTeam`,
#: `gameEligibleForRealMoney`, `realMoneyEligible`, `marketAvailable`,
#: `modelProbability` (always null), `modelProbabilityNote`,
#: `sourceMode`, `sourceUsed`; or (b) arithmetic on a field that IS here
#: -- `yesBidCents`/`yesAskCents`/`noBidCents`/`noAskCents` (x100),
#: `yesAskProbability` (== yesAsk), `midpoint` ((yesBid+yesAsk)/2),
#: `bidAskSpread` (yesAsk-yesBid). Nothing is dropped because it looked
#: uninteresting, and `DERIVED_MARKET_FIELDS` below states every
#: derivation so a consumer can reconstruct the card row exactly.
MARKET_COLUMNS = (
    "ticker",
    "eventTicker",
    "seriesTicker",
    "family",
    "scope",
    "title",
    "subtitle",
    # The MARKET's own scheduled-start field, which is NOT the game's.
    # It looked like a game-level constant and is not: on 2026-09-18 a
    # single game's rows carry up to 17 distinct values, some of them
    # plainly stale relative to the slate. Lifting it into the header
    # would have quietly rewritten every row to one arbitrary value --
    # which is exactly what `verify` refused to let happen. The game's
    # real start time is `eligibility.scheduledStart` in the header.
    "scheduledStart",
    "outcome",
    "participant",
    "line",
    "marketStructure",
    "status",
    "yesBid",
    "yesAsk",
    "noBid",
    "noAsk",
    "lastPrice",
    "volume",
    "openInterest",
    "capturedAt",
    "closeTime",
    "expirationTime",
    "settlementStatus",
    "automaticSettlementSupport",
    "productionModelSupport",
    "manualHandicappingEligibility",
    "classificationStatus",
    "validationStatus",
    "recordStatus",
)

#: Columns stored as an integer index into `legends[column]`. Chosen by
#: cardinality, not by meaning: a 341-row game has 17 distinct event
#: tickers and 14 distinct families, so interning them removes most of
#: the table's bytes without removing any of its information.
INTERNED_COLUMNS = frozenset({
    "scheduledStart",
    "eventTicker",
    "seriesTicker",
    "family",
    "scope",
    "outcome",
    "participant",
    "marketStructure",
    "status",
    "capturedAt",
    "closeTime",
    "expirationTime",
    "settlementStatus",
    "automaticSettlementSupport",
    "productionModelSupport",
    "manualHandicappingEligibility",
    "classificationStatus",
    "validationStatus",
    "recordStatus",
})

#: The card fields this layer stops storing because they are exactly
#: recomputable from the fields it does store. Published in the bundle so
#: a consumer reconstructing a card row is not guessing.
DERIVED_MARKET_FIELDS = {
    "yesBidCents": "round(yesBid * 100)",
    "yesAskCents": "round(yesAsk * 100)",
    "noBidCents": "round(noBid * 100)",
    "noAskCents": "round(noAsk * 100)",
    "yesAskProbability": "yesAsk",
    "midpoint": "(yesBid + yesAsk) / 2",
    "bidAskSpread": "yesAsk - yesBid",
}

#: Card fields that are game-level constants, lifted into the bundle
#: header. Named here so the round-trip test can prove that the union of
#: {compact row} + {these} + {derived} is the whole card row and nothing
#: silently fell out of all three.
GAME_LEVEL_MARKET_FIELDS = (
    "date",
    "matchup",
    "awayTeam",
    "homeTeam",
    "gameEligibleForRealMoney",
    "realMoneyEligible",
    "marketAvailable",
    "modelProbability",
    "modelProbabilityNote",
    "sourceMode",
    "sourceUsed",
    "retrievedAt",
    "rulesText",
)

#: Source key on a card market row -> compact column name, where they
#: differ. `_recordStatus` and `snapshotTimestamp` are the only two.
_COLUMN_SOURCE_KEY = {
    "capturedAt": "snapshotTimestamp",
    "recordStatus": "_recordStatus",
}


def _source_key(column):
    return _COLUMN_SOURCE_KEY.get(column, column)


# ─────────────────────────────────────────────────────────────────────
# The compact market table, and its exact inverse
# ─────────────────────────────────────────────────────────────────────

def build_market_table(markets):
    """
    Pure. Every market, in the order given, as a columnar table.

    Returns ``{"columns": [...], "legends": {col: [values]}, "rows":
    [[...]], "rowCount": n}``. A legend value of ``None`` is a real
    legend entry, so decoding is uniform and a null never becomes a
    missing key.
    """
    markets = list(markets)
    legends = {column: [] for column in INTERNED_COLUMNS if column in MARKET_COLUMNS}
    indices = {column: {} for column in legends}

    def intern(column, value):
        key = (type(value).__name__, value) if not isinstance(value, (str, type(None))) else value
        table = indices[column]
        if key not in table:
            table[key] = len(legends[column])
            legends[column].append(value)
        return table[key]

    rows = []
    for market in markets:
        row = []
        for column in MARKET_COLUMNS:
            value = market.get(_source_key(column))
            row.append(intern(column, value) if column in legends else value)
        rows.append(row)

    return {
        "columns": list(MARKET_COLUMNS),
        "internedColumns": sorted(legends),
        "legends": legends,
        "rowCount": len(rows),
        "rows": rows,
    }


def expand_market_table(table):
    """
    Pure. The exact inverse of :func:`build_market_table`: the compact
    table back into one dict per market, keyed by the card's own field
    names (`snapshotTimestamp`, `_recordStatus` included).

    This exists so "the compact form changed nothing" is a property a
    test can execute rather than a sentence in a docstring.
    """
    columns = table["columns"]
    legends = table.get("legends") or {}
    out = []
    for row in table["rows"]:
        record = {}
        for column, value in zip(columns, row):
            if column in legends:
                value = legends[column][value]
            record[_source_key(column)] = value
        out.append(record)
    return out


def rehydrate_card_rows(bundle):
    """
    Pure. The bundle's markets as full card-shaped rows: compact columns,
    plus the game-level constants in the header, plus every derived
    field. Used by the regression tests to prove byte-for-byte semantic
    equality against `data/handicapping_card/<date>.json`, and available
    to any consumer that wants the original shape back.
    """
    constants = bundle["markets"]["gameLevelConstants"]
    out = []
    for record in expand_market_table(bundle["markets"]):
        row = dict(record)
        row.update(constants)
        yes_bid, yes_ask = row.get("yesBid"), row.get("yesAsk")
        no_bid, no_ask = row.get("noBid"), row.get("noAsk")
        row["yesBidCents"] = None if yes_bid is None else round(yes_bid * 100)
        row["yesAskCents"] = None if yes_ask is None else round(yes_ask * 100)
        row["noBidCents"] = None if no_bid is None else round(no_bid * 100)
        row["noAskCents"] = None if no_ask is None else round(no_ask * 100)
        row["yesAskProbability"] = yes_ask
        row["midpoint"] = None if (yes_bid is None or yes_ask is None) else (yes_bid + yes_ask) / 2
        row["bidAskSpread"] = None if (yes_bid is None or yes_ask is None) else yes_ask - yes_bid
        out.append(row)
    return out


# ─────────────────────────────────────────────────────────────────────
# Game context (the handicapper's evidence, normalised once)
# ─────────────────────────────────────────────────────────────────────

#: Per-team offensive-form and quality fields lifted from the slate's
#: `awayTeamStats`/`homeTeamStats`. `offenseFormLine` is the one
#: HANDICAPPING_PLAYBOOK.md §6 requires VERBATIM -- it is carried as the
#: slate wrote it and is never recomputed here.
_OFFENSE_FIELDS = (
    "offenseFormLine", "offenseFormLabel", "offenseFormReason", "offenseFormAsOf",
    "runsPerGame", "last7RpG", "last15RpG", "seasonRpG", "runs", "gamesPlayed",
    "wrcPlus", "rpgIndex", "teamWOBA", "teamSeasonWOBA", "teamKPct", "teamBBPct",
    "teamHardHit", "teamBarrel", "teamFBPct", "oppXFIPavg", "oppQualityAdj",
    "oppQualityConf", "oppQualityGames", "oppQualityNote",
    "offenseBaselineRaw", "offenseBaselineBayes", "offenseBaselineOppAdj",
    "offenseBaselineAdj",
)

_LINEUP_FIELDS = (
    "lineupConfirmed", "lineupConfirmedOfficial", "lineupPosted", "lineupStatus",
    "lineupStatusReason", "lineupSource", "lineupDataQuality",
    "lineupBattersExpected", "lineupBattersFound", "lineupBattersResolved",
    "lineupBattersFallback", "lineupAvgWOBA", "lineupWOBADelta",
    "lineupAdj", "lineupAdjAvailable", "lineupAdjApplied", "lineupHandedness",
)

#: Production-model artefacts carried as REFERENCE ONLY. They are the
#: legacy 11-row engine's own output; HANDICAPPING_PLAYBOOK.md §7 is
#: explicit that this is evidence, not truth, and the bundle labels it
#: so rather than presenting it beside the manual handicap as an equal.
_PROJECTION_BLOCKS = (
    "modelProb", "f5", "teamTotals", "nrfi", "totalEval", "mlEdge",
    "runLineEval", "allEdges", "kalshiVF", "kalshiF5VF",
    "pinnacleVF", "pinnacleF5VF",
)

#: The market-ledger fields a handicapper actually reads. The full row is
#: ~80 fields of execution-economics provenance and is 77 KB per game;
#: the slate keeps all of it and this is the reading view.
_LEDGER_FIELDS = (
    "market", "status", "kalshiPrice", "kalshiVF", "pinnacleVF", "modelProb",
    "marketProbVF", "executablePriceUsed", "executableMarketProb",
    "executablePriceBasis", "executablePriceMarketTicker", "executablePriceSide",
    "executablePriceCapturedAt", "quoteStale", "quoteAgeSeconds", "bookState",
    "priceRefusalReason", "calibrationFactor", "calibratedEdgeVsExecutable",
    "netExecutableEdge", "netExpectedValuePerDollar",
    "feeAdjustedBreakEvenProbability", "edge", "edgeUsedForQualification",
    "confidence", "confidenceTier", "betSize", "maxBetPrice", "betUpToPriceNet",
    "rejectionReason", "missingFields", "evaluationError",
)

PROJECTION_LABEL = (
    "REFERENCE_ONLY -- the legacy 11-row production model's own output. "
    "HANDICAPPING_PLAYBOOK.md section 7: the model is evidence, not the source of "
    "truth, and it prices only these 11 rows. It is NOT the manual handicap, and "
    "it says nothing about the other markets in this bundle."
)


def _pick(source, fields):
    source = source or {}
    return {field: source.get(field) for field in fields if field in source}


def _side_context(side, team_side, team_stats):
    team_side = team_side or {}
    team_stats = team_stats or {}
    return {
        "side": side,
        "abbr": team_side.get("abbr") or team_stats.get("abbr"),
        "team": team_side.get("team"),
        "record": team_side.get("record"),
        "teamId": team_stats.get("teamId"),
        "startingPitcher": {
            **(team_side.get("pitcher") or {}),
            "savant": team_side.get("pitcherSavant"),
        },
        "bullpen": team_side.get("bullpen"),
        "offense": _pick(team_stats, _OFFENSE_FIELDS),
        "lineup": {
            **_pick(team_stats, _LINEUP_FIELDS),
            # The official lineup itself, as confirmed -- order, player,
            # position and platoon splits. This is what "both official
            # lineups confirmed" actually refers to.
            "officialLineup": team_stats.get("confirmedLineup"),
        },
    }


def build_game_context(slate_game):
    """
    Pure. One game's handicapping evidence, from that game's slate row.

    Everything HANDICAPPING_PLAYBOOK.md §2 asks for that this repository
    actually captures: starters and their Savant profiles, bullpen state
    and recent usage, confirmed lineups with platoon splits, offensive
    form (the verbatim `offenseFormLine`), opponent quality, park. What
    it does NOT capture is stated as such rather than invented: the slate
    carries no weather record, so `weather` is null with a reason and no
    number is fabricated for it.

    `slate_game` may be None (a card game with no slate row); the
    context then says so instead of half-existing.
    """
    if not slate_game:
        return {
            "available": False,
            "unavailableReason": "no slate row for this gameId",
        }
    return {
        "available": True,
        "gameId": slate_game.get("gameId"),
        "startTime": slate_game.get("startTime"),
        "kalshiGameTime": slate_game.get("kalshiGameTime"),
        "status": slate_game.get("status"),
        "venue": slate_game.get("venue"),
        "park": slate_game.get("park"),
        # Not captured anywhere in the current slate. Stated, not guessed.
        "weather": None,
        "weatherNote": (
            "The current slate pipeline captures no weather record. This is an "
            "acknowledged gap, not a null reading -- do not substitute a remembered "
            "or web-searched forecast as though it came from this artifact."
        ),
        "lineupCheckedAt": slate_game.get("lineupCheckedAt"),
        "lineupAuditUsed": slate_game.get("lineupAuditUsed"),
        "away": _side_context("away", slate_game.get("away"), slate_game.get("awayTeamStats")),
        "home": _side_context("home", slate_game.get("home"), slate_game.get("homeTeamStats")),
        "productionProjections": {
            "_label": PROJECTION_LABEL,
            **_pick(slate_game, _PROJECTION_BLOCKS),
            "marketLedger": [
                _pick(row, _LEDGER_FIELDS) for row in (slate_game.get("marketLedger") or [])
            ],
        },
    }


# ─────────────────────────────────────────────────────────────────────
# Bundles and manifest
# ─────────────────────────────────────────────────────────────────────

def _eligibility_row(game_block):
    lineups = game_block.get("lineups") or {}
    status = game_block.get("gameStatus") or {}
    return {
        "gameId": game_block.get("gameId"),
        "matchup": game_block.get("matchup"),
        "scheduledStart": game_block.get("scheduledStart"),
        "eligibilityStatus": game_block.get("eligibilityStatus"),
        "eligibilityReason": game_block.get("eligibilityReason"),
        "bettingEligible": bool(game_block.get("realMoneyEligible")),
        "realMoneyEligible": bool(game_block.get("realMoneyEligible")),
        "started": not status.get("isPregame", False) if status else None,
        "gameStatus": status.get("gameStatus"),
        "isPostponed": status.get("isPostponed"),
        "lineups": {
            "bothConfirmed": lineups.get("bothConfirmed"),
            "unconfirmedSides": lineups.get("unconfirmedSides"),
            "away": _lineup_side(lineups.get("away")),
            "home": _lineup_side(lineups.get("home")),
        },
    }


def _lineup_side(side):
    side = side or {}
    return {
        "team": side.get("team"),
        "lineupConfirmedOfficial": side.get("lineupConfirmedOfficial"),
        "battersResolved": side.get("battersResolved"),
        "lineupCheckedAt": side.get("lineupCheckedAt"),
        "reason": side.get("reason"),
    }


def build_bundle(card, game_block, slate_game, *, manifest_path):
    """
    Pure. One game's complete compact bundle.

    EVERY market in `game_block["markets"]` is in the table -- there is
    no predicate anywhere in this function. The registry exclusions and
    unclassified contracts travel with it, and so does the card's own
    `contractAccounting`, verbatim, so the completeness invariant can be
    checked from the bundle alone.
    """
    markets = list(game_block.get("markets") or [])
    eligibility = _eligibility_row(game_block)

    # Axis 1 is a property of the GAME. If the card ever disagreed with
    # itself row-by-row, lifting it to the header would silently pick a
    # winner -- so refuse instead.
    disagreeing = {
        bool(m.get("realMoneyEligible")) for m in markets
    } - {eligibility["realMoneyEligible"]}
    if disagreeing:
        raise ValueError(
            f"{game_block.get('matchup')}: card rows disagree with the game's "
            f"realMoneyEligible ({eligibility['realMoneyEligible']}); refusing to "
            "normalise a contradiction into the bundle header"
        )

    # LIFTING A FIELD OUT OF EVERY ROW IS ONLY LOSSLESS IF IT REALLY IS
    # CONSTANT. `scheduledStart` looked game-level and is not -- one
    # game's rows carry up to 17 distinct values -- so this is checked
    # per build rather than believed. A field that varies is a bug in
    # GAME_LEVEL_MARKET_FIELDS, and the build stops instead of
    # rewriting rows to one arbitrary value.
    constants = {}
    for field in GAME_LEVEL_MARKET_FIELDS:
        distinct = {json.dumps(m.get(field), sort_keys=True) for m in markets}
        if len(distinct) > 1:
            raise ValueError(
                f"{game_block.get('matchup')}: {field!r} is not constant across this "
                f"game's {len(markets)} market rows ({len(distinct)} distinct values); "
                "it cannot be normalised into the bundle header without losing data"
            )
        constants[field] = markets[0].get(field) if markets else None

    table = build_market_table(markets)
    table["gameLevelConstants"] = constants
    table["derivedFields"] = dict(DERIVED_MARKET_FIELDS)
    table["coverage"] = (
        "EVERY Kalshi market the canonical card attributes to this game is a row "
        "here. No ranking, shortlisting, edge filter or family filter is applied "
        "anywhere in this artifact."
    )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactType": BUNDLE_ARTIFACT_TYPE,
        "date": card["date"],
        "generatedAt": card["generatedAt"],
        "playbookVersion": card.get("playbookVersion"),
        "manifest": manifest_path,
        "gameId": game_block.get("gameId"),
        "matchup": game_block.get("matchup"),
        "eligibility": eligibility,
        "context": build_game_context(slate_game),
        "markets": table,
        "marketCount": len(markets),
        "marketsByFamily": game_block.get("marketsByFamily"),
        "familiesWithoutAutomaticSettlement": game_block.get(
            "familiesWithoutAutomaticSettlement"),
        # States 2 and 3 of the four terminal states (see
        # lib/contract_accounting.py). Kept verbatim: an unrecognised
        # Kalshi family must stay visible HERE too, not only on the card.
        "registryExcluded": game_block.get("registryExcludedForThisGame") or [],
        "unclassified": game_block.get("unclassifiedForThisGame") or [],
        "contractAccounting": game_block.get("contractAccounting"),
        "marketFilterStageReport": game_block.get("marketFilterStageReport"),
    }


#: Execution-critical constants the consumer needs and would otherwise
#: have to read RULES.md/MODEL_CORE.md/config/rules.json to find. READ
#: FROM THE CANONICAL SOURCE AT BUILD TIME, never restated here -- see
#: `execution_constants`. Duplicating a threshold as a literal in this
#: file is exactly the drift the manifest exists to prevent.
EXECUTION_CONSTANT_KEYS = ("calibration", "edge_thresholds", "base_sizes", "multipliers")


def execution_constants(rules):
    """
    Pure. The small, execution-critical subset of `config/rules.json` a
    handicapper needs in hand: calibration factors, edge thresholds, base
    sizes and market multipliers.

    It is a PROJECTION of the canonical config, taken at build time --
    so the manifest cannot drift from `config/rules.json`, because it has
    no independent copy to drift from. `tests/test_handicap_runtime.py`
    pins that equality.
    """
    rules = rules or {}
    return {
        "_source": "config/rules.json",
        "_sourceVersion": rules.get("_version"),
        "_sourceUpdated": rules.get("_updated"),
        "_note": (
            "Projected verbatim from config/rules.json at build time. RULES.md and "
            "MODEL_CORE.md remain authoritative for everything else; this is the "
            "subset needed to size and threshold a wager without loading them."
        ),
        **{key: rules.get(key) for key in EXECUTION_CONSTANT_KEYS},
    }


#: The consumer contract, published as data so RUN_THE_SLATE.md and the
#: runtime cannot describe two different flows. The doc quotes this list;
#: a test asserts the doc still matches it.
CONSUMER_FLOW = [
    "Read HANDICAPPING_PLAYBOOK.md and PLAYBOOK_LESSONS.md (methodology; ~19 KB total).",
    "Read this manifest.",
    "Select games where bettingEligible is true (not started AND both official "
    "lineups confirmed).",
    "Load ONLY those games' bundles, by the `bundle` path on each manifest row. "
    "The full-day handicapping card is NOT required and must not be loaded to "
    "handicap a slate.",
    "For EACH eligible game, independently: build the baseball thesis first, then "
    "inspect EVERY market row in that bundle, compare all viable expressions, and "
    "keep only wagers clearing the threshold in manifest.executionConstants.",
    "Run one final portfolio/correlation pass across all games (playbook section 5).",
    "Return the betting card.",
]


def build_manifest(card, game_rows, *, rules=None, date_root=None):
    """
    Pure. The tiny index a consumer reads FIRST.

    It answers "how many eligible games exist, which ones are they, and
    how many markets must I inspect" with no bundle loaded and the
    archival card never opened.
    """
    bankroll = card.get("bankroll") or {}
    counts = card.get("counts") or {}
    bundle_market_total = sum(row["normalizedMarkets"] for row in game_rows)

    return {
        "schemaVersion": SCHEMA_VERSION,
        "artifactType": MANIFEST_ARTIFACT_TYPE,
        "date": card["date"],
        "generatedAt": card["generatedAt"],
        "eligibilityEvaluatedAt": card.get("eligibilityEvaluatedAt"),
        "playbookVersion": card.get("playbookVersion"),
        "root": date_root,
        "provenance": {
            "sourceArtifact": "data/handicapping_card/%s.json" % card["date"],
            "sourceArtifactType": card.get("artifactType"),
            "rawUniverseSnapshot": card.get("rawUniverseSnapshot"),
            "slateSource": card.get("slateSource"),
            "derivation": (
                "A lossless projection of the canonical handicapping card. This layer "
                "runs no market discovery, no normalisation, no registry and no "
                "eligibility logic of its own."
            ),
        },
        "eligibilityPolicy": card.get("eligibilityPolicy"),
        "axes": card.get("axes"),
        "bankroll": {
            # The consumer-facing subset. The amount is not here and is
            # not anywhere in this layer -- see lib/bankroll_context.py.
            "status": bankroll.get("status"),
            "source": bankroll.get("source"),
            "valueType": bankroll.get("valueType"),
            "observedAt": bankroll.get("observedAt"),
            "resolvedAt": bankroll.get("resolvedAt"),
            "ageMinutes": bankroll.get("ageMinutes"),
            "maxAgeMinutes": bankroll.get("maxAgeMinutes"),
            "currency": bankroll.get("currency"),
            "sizingAllowed": bankroll.get("sizingAllowed"),
            "numericBankrollAvailable": bankroll.get("numericBankrollAvailable"),
            "consumerSizingVerdict": bankroll.get("consumerSizingVerdict"),
            "unavailableReason": bankroll.get("unavailableReason"),
        },
        "executionConstants": execution_constants(rules),
        "counts": {
            "gamesTotal": counts.get("gamesTotal"),
            "bettingEligibleGames": counts.get("bettingEligibleGames"),
            "researchOnlyGames": counts.get("researchOnlyGames"),
            "researchOnlyByReason": counts.get("researchOnlyByReason"),
            "eligibleMarketsTotal": counts.get("eligibleMarketsTotal"),
            "researchOnlyMarketsTotal": counts.get("researchOnlyMarketsTotal"),
            "rawUniverseContracts": counts.get("rawUniverseContracts"),
            "normalizedRegistryValidatedMarkets": counts.get(
                "normalizedRegistryValidatedMarkets"),
            "registryExcludedMarkets": counts.get("registryExcludedMarkets"),
            "unclassifiedRawContracts": counts.get("unclassifiedRawContracts"),
            "silentRemainderTotal": counts.get("silentRemainderTotal"),
            "runtimeBundleMarketRows": bundle_market_total,
        },
        "completeness": {
            "invariant": "silentRemainderCount == 0 on every game",
            "silentRemainderTotal": counts.get("silentRemainderTotal"),
            "holds": all(row["silentRemainderCount"] == 0 for row in game_rows),
            "bundleRowsEqualCardMarkets":
                bundle_market_total == (counts.get("eligibleMarketsTotal") or 0)
                + (counts.get("researchOnlyMarketsTotal") or 0),
            "note": (
                "Every raw Kalshi contract attributable to a game is in exactly one "
                "visible state: a market row in that game's bundle, a registry "
                "exclusion with its reason, or an unclassified contract with the "
                "normalizer's reason. A build that cannot prove this refuses to write."
            ),
        },
        "guarantees": card.get("guarantees"),
        "consumerFlow": list(CONSUMER_FLOW),
        "games": game_rows,
    }


def manifest_game_row(game_block, bundle_path, *, bundle_bytes=None, gzip_bytes=None,
                      gzip_path=None):
    """Pure. One line of the manifest's game index."""
    accounting = game_block.get("contractAccounting") or {}
    row = _eligibility_row(game_block)
    row.update({
        "rawAttributableContracts": accounting.get("rawAttributableCount"),
        "normalizedMarkets": accounting.get("normalizedCount"),
        "excludedOrUnresolved": accounting.get("excludedOrUnresolvedCount"),
        "registryExcluded": accounting.get("registryExcludedCount"),
        "unclassified": accounting.get("unclassifiedCount"),
        "accounted": accounting.get("accountedCount"),
        "silentRemainderCount": accounting.get("silentRemainderCount"),
        "invariantHolds": accounting.get("invariantHolds"),
        "marketsToInspect": game_block.get("marketCount"),
        "marketsByFamily": game_block.get("marketsByFamily"),
        "bundle": bundle_path,
        "bundleGzip": gzip_path,
        "bundleBytes": bundle_bytes,
        "bundleGzipBytes": gzip_bytes,
    })
    return row


# ─────────────────────────────────────────────────────────────────────
# Verification -- the build refuses to write what it cannot prove
# ─────────────────────────────────────────────────────────────────────

def verify(card, manifest, bundles):
    """
    Pure. Every way this layer could lose or alter a market, checked.

    Returns a list of human-readable problems; empty means the runtime is
    a faithful, complete projection of the card. The CLI treats a
    non-empty list as a hard build failure -- a plausible-looking runtime
    that quietly dropped a contract is strictly worse than no runtime.
    """
    problems = []
    card_games = {
        g["gameId"]: g for g in card["bettingEligibleGames"] + card["researchOnlyGames"]
    }

    if set(card_games) != set(bundles):
        problems.append(
            f"game set mismatch: card={sorted(card_games)} runtime={sorted(bundles)}")

    manifest_ids = [row["gameId"] for row in manifest["games"]]
    if sorted(manifest_ids) != sorted(card_games):
        problems.append(
            f"manifest game set mismatch: manifest={sorted(manifest_ids)} "
            f"card={sorted(card_games)}")
    if len(manifest_ids) != len(set(manifest_ids)):
        problems.append("manifest lists a gameId more than once")

    for game_id, game in card_games.items():
        bundle = bundles.get(game_id)
        if bundle is None:
            continue
        label = f"{game.get('matchup')} ({game_id})"

        card_tickers = [m.get("ticker") for m in game.get("markets") or []]
        bundle_rows = expand_market_table(bundle["markets"])
        bundle_tickers = [r.get("ticker") for r in bundle_rows]

        if len(card_tickers) != len(bundle_tickers):
            problems.append(
                f"{label}: market COUNT changed -- card={len(card_tickers)} "
                f"bundle={len(bundle_tickers)}")
        missing = sorted(set(card_tickers) - set(bundle_tickers))
        if missing:
            problems.append(
                f"{label}: {len(missing)} market(s) on the card are absent from the "
                f"bundle: {missing[:10]}")
        extra = sorted(set(bundle_tickers) - set(card_tickers))
        if extra:
            problems.append(
                f"{label}: {len(extra)} market(s) in the bundle are not on the card: "
                f"{extra[:10]}")

        # Prices and semantics, row by row, not just counts.
        rehydrated = {row["ticker"]: row for row in rehydrate_card_rows(bundle)}
        for market in game.get("markets") or []:
            got = rehydrated.get(market.get("ticker"))
            if got is None:
                continue
            for field, want in market.items():
                if field not in got:
                    problems.append(f"{label}/{market.get('ticker')}: lost field {field}")
                elif got[field] != want and not (
                        isinstance(want, float) and isinstance(got[field], float)
                        and abs(got[field] - want) < 1e-9):
                    problems.append(
                        f"{label}/{market.get('ticker')}: field {field} changed "
                        f"{want!r} -> {got[field]!r}")

        accounting = bundle.get("contractAccounting") or {}
        if accounting.get("silentRemainderCount"):
            problems.append(
                f"{label}: silentRemainderCount="
                f"{accounting['silentRemainderCount']} -- contracts attributable to "
                "this game are visible nowhere")
        if accounting != (game.get("contractAccounting") or {}):
            problems.append(f"{label}: contractAccounting was altered by the projection")

        if len(bundle.get("registryExcluded") or []) != len(
                game.get("registryExcludedForThisGame") or []):
            problems.append(f"{label}: registry exclusions were dropped")
        if len(bundle.get("unclassified") or []) != len(
                game.get("unclassifiedForThisGame") or []):
            problems.append(f"{label}: unclassified contracts were dropped")

        if bool(game.get("realMoneyEligible")) != bool(
                bundle["eligibility"]["bettingEligible"]):
            problems.append(f"{label}: eligibility flipped in the bundle")

    if not manifest["completeness"]["holds"]:
        problems.append("manifest.completeness.holds is false")
    if not manifest["completeness"]["bundleRowsEqualCardMarkets"]:
        problems.append(
            "manifest row totals do not reconcile with the card's own market counts")

    return problems


# ─────────────────────────────────────────────────────────────────────
# Writing
# ─────────────────────────────────────────────────────────────────────

def _dump(payload, *, pretty):
    """
    The manifest is PRETTY because a human and a chat both read it
    directly and it is small. A bundle is COMPACT because its market
    table is thousands of short arrays, and one-element-per-line
    indentation costs more bytes than the data: on 2026-09-18 it is the
    difference between 153 KB and 96 KB per game, for identical content.
    """
    if pretty:
        return json.dumps(payload, indent=1, sort_keys=True).encode("utf-8")
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def write_runtime(card, bundles, manifest_builder, *, root=RUNTIME_ROOT, gzip_bundles=True):
    """
    Write the runtime tree and return ``(manifest, manifest_path, rows)``.

    Bundles are written UNCOMPRESSED and, when `gzip_bundles`, ALSO as
    `.json.gz`. Both on purpose: the actual consumer is a chat session
    reading `raw.githubusercontent.com`, which cannot gunzip, so an
    exclusively-compressed layer would be unreadable by the only client
    that exists; and the compressed copy is what a programmatic consumer
    (and the size evidence) should use. Together they are still a small
    fraction of the archival card.

    `manifest_builder(rows)` is called with the finished per-game rows so
    the manifest can carry each bundle's real on-disk size.
    """
    date_root = os.path.join(root, card["date"])
    games_root = os.path.join(date_root, "games")
    os.makedirs(games_root, exist_ok=True)

    rows = []
    for game_id, (game_block, bundle) in bundles.items():
        name = f"{game_id}.json"
        path = os.path.join(games_root, name)
        blob = _dump(bundle, pretty=False)
        with open(path, "wb") as handle:
            handle.write(blob)
        gzip_path = None
        gzip_bytes = None
        if gzip_bundles:
            gzip_path = path + ".gz"
            # mtime=0: the compressed bytes are then a pure function of
            # the content, so an unchanged slate does not produce a
            # spurious diff every time the builder runs.
            with gzip.GzipFile(gzip_path, "wb", compresslevel=9, mtime=0) as handle:
                handle.write(blob)
            gzip_bytes = os.path.getsize(gzip_path)
        rows.append(manifest_game_row(
            game_block, path.replace(os.sep, "/"),
            bundle_bytes=len(blob), gzip_bytes=gzip_bytes,
            gzip_path=gzip_path.replace(os.sep, "/") if gzip_path else None,
        ))

    rows.sort(key=lambda row: (not row["bettingEligible"], str(row["scheduledStart"]),
                              str(row["matchup"])))
    manifest = manifest_builder(rows)
    manifest_path = os.path.join(date_root, "manifest.json")
    with open(manifest_path, "wb") as handle:
        handle.write(_dump(manifest, pretty=True))

    # THE POINTER A FRESH CHAT READS FIRST.
    #
    # "RUN MLB" arrives with no date in it. Without this, the consumer has
    # to guess today's date in the right timezone and hope a manifest
    # exists at that path -- and a guess that lands on yesterday is a
    # slate that has already been played. A few hundred bytes removes the
    # guess. It carries the sizing verdict too, because `bankrollStatus:
    # FRESH` alone invites a session to compute dollar stakes it has no
    # number for.
    latest = os.path.join(root, "latest.json")
    # AND IT NEVER MOVES BACKWARDS. Rebuilding an archived date -- a replay,
    # a backfill, a bug hunt -- must not repoint a fresh chat at a slate that
    # has already been played. The per-date manifest is always rewritten;
    # only the pointer is held.
    existing = None
    if os.path.exists(latest):
        try:
            with open(latest, encoding="utf-8") as handle:
                existing = json.load(handle).get("date")
        except (OSError, ValueError):
            existing = None
    if existing and existing > manifest["date"]:
        return manifest, manifest_path, rows

    with open(latest, "wb") as handle:
        handle.write(_dump({
            "date": manifest["date"],
            "manifest": manifest_path.replace(os.sep, "/"),
            "generatedAt": manifest["generatedAt"],
            "playbookVersion": manifest.get("playbookVersion"),
            "bettingEligibleGames": manifest["counts"]["bettingEligibleGames"],
            "gamesTotal": manifest["counts"]["gamesTotal"],
            "eligibleMarketsTotal": manifest["counts"]["eligibleMarketsTotal"],
            "silentRemainderTotal": manifest["counts"]["silentRemainderTotal"],
            "bankrollStatus": manifest["bankroll"]["status"],
            "numericBankrollAvailable": manifest["bankroll"]["numericBankrollAvailable"],
            "dollarSizingVerdict": (
                manifest["bankroll"].get("consumerSizingVerdict") or {}).get("verdict"),
            "readThisFirst": list(CONSUMER_FLOW[:2]),
        }, pretty=True))
    return manifest, manifest_path, rows
