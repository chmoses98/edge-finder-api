"""
tests/test_handicap_runtime.py
==============================
The compact handicapper serving layer's promises, each one executed.

The serving layer exists to make a slate READABLE, and the entire risk
of that is that "smaller" quietly becomes "fewer". So every test here is
about a market NOT disappearing, or a price NOT changing, or an
ineligible game NOT becoming executable -- and the size claim gets
exactly one test, at the end, where it belongs.

Fixture strategy matches tests/test_handicapping_card.py: synthetic raw
Kalshi contracts pushed through the REAL normalizer, registry, contract
accounting and card builder, so nothing here is testing a mock of the
thing it is meant to protect. The last section runs the real committed
2026-09-18 card -- 4,247 contracts across 15 games -- through the whole
projection.
"""
import gzip
import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lib import contract_accounting  # noqa: E402
from lib import handicap_runtime as runtime  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "scripts", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load("build_handicapping_card")
runtime_builder = _load("build_handicap_runtime")

DATE = "2026-09-17"
NOW = "2026-09-17T18:40:00Z"
GAME_KEY = "26SEP171510SDCOL"
GAME_ID = 824305


# ── fixtures ───────────────────────────────────────────────────────────

def _raw(ticker, event_ticker, **extra):
    row = {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "series_ticker": event_ticker.split("-", 1)[0],
        "title": f"Contract {ticker}",
        "yes_ask": 50, "yes_bid": 48, "no_ask": 52, "no_bid": 50,
        "status": "active",
        "close_time": "2026-09-18T02:00:00Z",
    }
    row.update(extra)
    return row


def _slate(*, lineups_confirmed=True, status="Scheduled", start="2026-09-17T20:10:00Z"):
    side = {
        "lineupConfirmedOfficial": lineups_confirmed,
        "lineupConfirmed": lineups_confirmed,
        "offenseFormLine": "L7: 5.1 avg | 3.0 med | 3,4,2,20,5,1,1",
        "offenseFormLabel": "OUTLIER_INFLATED",
        "confirmedLineup": [{"order": 1, "name": "A Hitter", "position": "CF"}],
    }
    return {
        "date": DATE,
        "games": [{
            "gameId": GAME_ID,
            "away": {"abbr": "SD", "team": "San Diego Padres",
                     "pitcher": {"name": "A Starter"},
                     "bullpen": {"era": 3.9, "xFIP": 4.1}},
            "home": {"abbr": "COL", "team": "Colorado Rockies",
                     "pitcher": {"name": "B Starter"},
                     "bullpen": {"era": 5.2, "xFIP": 5.0}},
            "startTime": start,
            "status": status,
            "venue": "Coors Field",
            "park": {"dome": False, "name": "Coors Field", "parkFactor": 112},
            "awayTeamStats": dict(side, abbr="SD"),
            "homeTeamStats": dict(side, abbr="COL"),
            "modelProb": {"away": 55.0, "home": 45.0},
            "marketLedger": [{"market": "NRFI", "status": "Rejected",
                              "rejectionReason": "edge below floor", "edge": -3.2,
                              "executablePriceUsed": 54.0, "confidence": None}],
        }],
    }


def _card(raw_markets, *, slate=None, bankroll=None, now=NOW):
    records, _counts, _malformed = builder.normalize_batch(
        raw_markets, source_mode="snapshot", source_used="snapshot")
    validated, excluded = builder.apply_strict_game_registry(records, requested_date=DATE)
    unclassified = contract_accounting.unclassified_raw_contracts(
        raw_markets, records, normalize_market=builder.normalize_market,
        source_mode="snapshot", source_used="snapshot")
    card = builder.build_card(
        date=DATE, slate=slate or _slate(), slate_source="test", records=validated,
        bankroll=bankroll if bankroll is not None else {}, now_utc=now,
        raw_markets=raw_markets, excluded=excluded, unclassified=unclassified,
        all_records=records,
    )
    card["rawUniverseSnapshot"] = "test-snapshot"
    return card


def _project(card, slate=None, root=None):
    """Card -> runtime, in memory, with the same verification the CLI runs."""
    slate = slate if slate is not None else _slate()
    slate_games = runtime_builder.slate_games_by_id(slate)
    bundles = {}
    for block in card["bettingEligibleGames"] + card["researchOnlyGames"]:
        bundles[block["gameId"]] = runtime.build_bundle(
            card, block, slate_games.get(block["gameId"]), manifest_path="m.json")
    rows = [
        runtime.manifest_game_row(block, f"games/{block['gameId']}.json")
        for block in card["bettingEligibleGames"] + card["researchOnlyGames"]
    ]
    manifest = runtime.build_manifest(card, rows, rules=_rules(), date_root=root or ".")
    return manifest, bundles


def _rules():
    with open(os.path.join(ROOT, "config", "rules.json"), encoding="utf-8") as handle:
        return json.load(handle)


TWO_FAMILIES = [
    _raw("KXMLBGAME-26SEP171510SDCOL-COL", "KXMLBGAME-26SEP171510SDCOL"),
    _raw("KXMLBGAME-26SEP171510SDCOL-SD", "KXMLBGAME-26SEP171510SDCOL"),
    _raw("KXMLBTOTAL-26SEP171510SDCOL-10", "KXMLBTOTAL-26SEP171510SDCOL"),
    _raw("KXMLBF5-26SEP171510SDCOL-SD", "KXMLBF5-26SEP171510SDCOL"),
    # A hitter prop -- explicitly in scope, explicitly never filtered out.
    _raw("KXMLBHIT-26SEP171510SDCOL-SOMEHITTER-0.5",
         "KXMLBHIT-26SEP171510SDCOL"),
    # A pitcher prop, likewise.
    _raw("KXMLBKS-26SEP171510SDCOL-SOMEPITCHER-5.5",
         "KXMLBKS-26SEP171510SDCOL"),
]


# ── 1 & 5. every normalized market for a game reaches its bundle ───────

def test_the_eligible_game_bundle_contains_every_normalized_market():
    card = _card(TWO_FAMILIES)
    game = card["bettingEligibleGames"][0]
    _manifest, bundles = _project(card)
    bundle = bundles[GAME_ID]

    card_tickers = sorted(m["ticker"] for m in game["markets"])
    bundle_tickers = sorted(
        row["ticker"] for row in runtime.expand_market_table(bundle["markets"]))

    assert card_tickers == bundle_tickers
    assert bundle["marketCount"] == game["marketCount"] == len(card_tickers)
    assert bundle["markets"]["rowCount"] == len(card_tickers)


def test_pitcher_and_hitter_props_are_never_filtered_out_of_a_bundle():
    """They are archived and researchable (issue #43 owns settlement),
    and this layer must not be the thing that quietly removes them."""
    card = _card(TWO_FAMILIES)
    _manifest, bundles = _project(card)
    rows = runtime.expand_market_table(bundles[GAME_ID]["markets"])
    families = {row["family"] for row in rows}

    assert any(f.startswith("hitter_") for f in families), families
    assert any(f.startswith("pitcher_") for f in families), families


def test_no_market_is_dropped_for_lacking_production_or_settlement_support():
    card = _card(TWO_FAMILIES)
    game = card["bettingEligibleGames"][0]
    unsupported = [
        m["ticker"] for m in game["markets"]
        if m["productionModelSupport"] != "MODELLED"
        or m["automaticSettlementSupport"] != "SUPPORTED"
    ]
    assert unsupported, "fixture must contain at least one unsupported market"

    _manifest, bundles = _project(card)
    present = {row["ticker"] for row in
               runtime.expand_market_table(bundles[GAME_ID]["markets"])}
    assert set(unsupported) <= present


# ── 2. zero silent remainder ───────────────────────────────────────────

def test_the_manifest_reports_zero_silent_remainder_and_reconciles():
    card = _card(TWO_FAMILIES)
    manifest, _bundles = _project(card)

    assert manifest["counts"]["silentRemainderTotal"] == 0
    assert manifest["completeness"]["holds"] is True
    assert manifest["completeness"]["bundleRowsEqualCardMarkets"] is True
    for row in manifest["games"]:
        assert row["silentRemainderCount"] == 0
        assert row["invariantHolds"] is True
        # raw attributable == normalized + excluded/unresolved, per game.
        assert row["rawAttributableContracts"] == (
            row["normalizedMarkets"] + row["excludedOrUnresolved"])


def test_a_silent_remainder_fails_the_runtime_build_rather_than_publishing():
    """The invariant is only worth having if violating it stops a write."""
    card = _card(TWO_FAMILIES)
    manifest, bundles = _project(card)
    # Inject the failure the invariant exists to catch.
    bundles[GAME_ID]["contractAccounting"]["silentRemainderCount"] = 1
    bundles[GAME_ID]["contractAccounting"]["silentRemainderTickers"] = ["GHOST-1"]

    problems = runtime.verify(card, manifest, bundles)
    assert any("silentRemainderCount" in p for p in problems), problems


# ── 3. an unsupported / brand-new Kalshi family stays visible ──────────

def test_a_brand_new_kalshi_family_is_explicitly_visible_in_the_bundle():
    """An unrecognised series has no parser, no registry entry and no
    model. It must still be SHOWN -- as a registry exclusion with its
    reason -- not dropped because nothing here understands it."""
    raw = TWO_FAMILIES + [
        _raw("KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES",
             "KXMLBQUANTUMFLUX-26SEP171510SDCOL"),
    ]
    card = _card(raw)
    _manifest, bundles = _project(card)
    bundle = bundles[GAME_ID]

    shown = {row.get("ticker") for row in bundle["registryExcluded"]}
    assert "KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES" in shown
    assert bundle["contractAccounting"]["silentRemainderCount"] == 0
    # And the exclusion carries a reason, so it is visible AND explained.
    excluded = next(r for r in bundle["registryExcluded"]
                    if r.get("ticker") == "KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES")
    assert any(str(v) for k, v in excluded.items() if "eason" in k), excluded


def test_dropping_a_registry_exclusion_from_a_bundle_is_caught():
    raw = TWO_FAMILIES + [
        _raw("KXMLBQUANTUMFLUX-26SEP171510SDCOL-YES",
             "KXMLBQUANTUMFLUX-26SEP171510SDCOL"),
    ]
    card = _card(raw)
    manifest, bundles = _project(card)
    bundles[GAME_ID]["registryExcluded"] = []

    assert any("registry exclusions were dropped" in p
               for p in runtime.verify(card, manifest, bundles))


# ── 4. a malformed contract stays explicitly accounted for ─────────────

#: A contract with a ticker the classifier cannot recognise. It parses
#: far enough to keep its identity, so the strict registry rejects it
#: and it ends in state 2 -- REGISTRY_EXCLUDED, with its reason.
UNRECOGNISED_TITLE = {"ticker": "JUNK-1",
                      "event_ticker": "KXMLBGAME-26SEP171510SDCOL"}

#: A contract with NO usable ticker. The normalizer cannot key it at
#: all ("missing market_ticker"), so it ends in state 3 --
#: UNCLASSIFIED, identity recovered from the raw payload.
NO_TICKER_AT_ALL = {"event_ticker": "KXMLBGAME-26SEP171510SDCOL",
                    "title": "a contract with no ticker", "yes_ask": 50}


def test_a_malformed_contract_reaches_the_bundle_as_a_registry_exclusion():
    """State 2. It has an identity, so it is shown WITH its reason."""
    card = _card(TWO_FAMILIES + [UNRECOGNISED_TITLE])
    _manifest, bundles = _project(card)
    bundle = bundles[GAME_ID]

    shown = {row.get("ticker") for row in bundle["registryExcluded"]}
    assert "JUNK-1" in shown
    assert bundle["contractAccounting"]["excludedOrUnresolvedCount"] >= 1
    assert bundle["contractAccounting"]["silentRemainderCount"] == 0
    # Attributable == visible. Nothing became a remainder.
    acct = bundle["contractAccounting"]
    assert acct["rawAttributableCount"] == acct["accountedCount"]


def test_an_unkeyable_contract_reaches_the_bundle_as_an_unclassified_row():
    """State 3. No ticker to key it by, so it is carried with the
    normalizer's own reason rather than counted and lost."""
    card = _card(TWO_FAMILIES + [NO_TICKER_AT_ALL])
    manifest, bundles = _project(card)
    bundle = bundles[GAME_ID]

    assert card["counts"]["unclassifiedRawContracts"] == 1
    reasons = [row.get("reason") for row in bundle["unclassified"]]
    assert reasons == ["missing market_ticker"]
    assert all(row.get("state") == "UNCLASSIFIED" for row in bundle["unclassified"])
    assert manifest["counts"]["unclassifiedRawContracts"] == 1
    assert manifest["counts"]["silentRemainderTotal"] == 0


@pytest.mark.parametrize("extra,field", [
    (UNRECOGNISED_TITLE, "registryExcluded"),
    (NO_TICKER_AT_ALL, "unclassified"),
])
def test_dropping_a_non_normalized_contract_from_a_bundle_is_caught(extra, field):
    card = _card(TWO_FAMILIES + [extra])
    manifest, bundles = _project(card)
    assert bundles[GAME_ID][field], "fixture must produce one of these"
    bundles[GAME_ID][field] = []

    assert any("were dropped" in p for p in runtime.verify(card, manifest, bundles))


# ── 6. research-only can never leak into real-money eligibility ────────

def test_an_unconfirmed_lineup_game_is_research_only_everywhere_in_the_runtime():
    card = _card(TWO_FAMILIES, slate=_slate(lineups_confirmed=False))
    assert not card["bettingEligibleGames"], "fixture must be research-only"

    manifest, bundles = _project(card, slate=_slate(lineups_confirmed=False))
    row = manifest["games"][0]
    bundle = bundles[GAME_ID]

    assert row["bettingEligible"] is False
    assert row["realMoneyEligible"] is False
    assert row["lineups"]["bothConfirmed"] is False
    assert bundle["eligibility"]["bettingEligible"] is False
    assert bundle["markets"]["gameLevelConstants"]["realMoneyEligible"] is False
    assert bundle["markets"]["gameLevelConstants"]["gameEligibleForRealMoney"] is False
    assert manifest["counts"]["bettingEligibleGames"] == 0
    # Its markets are still all there -- research surface, not deletion.
    assert bundle["marketCount"] == len(TWO_FAMILIES)


def test_flipping_a_bundles_eligibility_is_caught_by_verification():
    card = _card(TWO_FAMILIES, slate=_slate(lineups_confirmed=False))
    manifest, bundles = _project(card, slate=_slate(lineups_confirmed=False))
    bundles[GAME_ID]["eligibility"]["bettingEligible"] = True

    assert any("eligibility flipped" in p
               for p in runtime.verify(card, manifest, bundles))


def test_a_bundle_refuses_to_normalise_contradictory_row_level_eligibility():
    """Lifting axis 1 into the header is only safe because every row
    agrees. If they ever disagreed, silently picking one would be the
    leak -- so the build stops."""
    card = _card(TWO_FAMILIES)
    game = card["bettingEligibleGames"][0]
    game["markets"][0]["realMoneyEligible"] = False

    with pytest.raises(ValueError, match="realMoneyEligible"):
        runtime.build_bundle(card, game, None, manifest_path="m.json")


# ── 7. a started game can never become executable ──────────────────────

def test_a_started_game_cannot_become_executable_in_the_runtime():
    card = _card(TWO_FAMILIES, slate=_slate(status="In Progress"),
                 now="2026-09-17T22:30:00Z")
    assert not card["bettingEligibleGames"]

    manifest, bundles = _project(card, slate=_slate(status="In Progress"))
    row = manifest["games"][0]

    assert row["bettingEligible"] is False
    assert row["eligibilityStatus"] != "BETTING_ELIGIBLE"
    assert bundles[GAME_ID]["eligibility"]["bettingEligible"] is False
    assert manifest["counts"]["bettingEligibleGames"] == 0


def test_a_game_whose_scheduled_start_has_passed_is_not_eligible():
    card = _card(TWO_FAMILIES, slate=_slate(start="2026-09-17T17:00:00Z"),
                 now="2026-09-17T18:40:00Z")
    manifest, _bundles = _project(card, slate=_slate(start="2026-09-17T17:00:00Z"))
    assert manifest["counts"]["bettingEligibleGames"] == 0
    assert manifest["games"][0]["bettingEligible"] is False


# ── 8. the manifest answers the routing question on its own ────────────

def test_the_manifest_answers_which_games_and_how_many_markets_alone():
    card = _card(TWO_FAMILIES)
    manifest, _bundles = _project(card)

    # Everything a consumer needs to decide what to load next.
    assert manifest["counts"]["bettingEligibleGames"] == 1
    row = manifest["games"][0]
    for field in ("gameId", "matchup", "scheduledStart", "started", "bettingEligible",
                  "lineups", "rawAttributableContracts", "normalizedMarkets",
                  "excludedOrUnresolved", "silentRemainderCount", "marketsToInspect",
                  "bundle"):
        assert field in row, field
    assert row["lineups"]["away"]["lineupConfirmedOfficial"] is True
    assert row["lineups"]["home"]["lineupConfirmedOfficial"] is True


def test_the_manifest_never_references_the_full_day_handicapping_card_as_a_load_step():
    card = _card(TWO_FAMILIES)
    manifest, _bundles = _project(card)

    # Provenance may (and must) name the card. The CONSUMER FLOW must
    # not ask anyone to open it.
    assert "handicapping_card" in manifest["provenance"]["sourceArtifact"]
    flow = " ".join(manifest["consumerFlow"])
    assert "handicapping card is NOT required" in flow
    assert not any(
        step.lower().startswith("load") and "handicapping_card" in step
        for step in manifest["consumerFlow"])


def test_the_manifest_carries_no_market_rows_at_all():
    """If the manifest ever grew the market table, the first load would
    be the giant load again."""
    card = _card(TWO_FAMILIES)
    manifest, _bundles = _project(card)
    blob = json.dumps(manifest)

    for market in card["bettingEligibleGames"][0]["markets"]:
        assert market["ticker"] not in blob, market["ticker"]


def test_the_manifest_carries_the_bankroll_verdict_but_never_an_amount():
    card = _card(TWO_FAMILIES, bankroll={
        "status": "FRESH", "sizingAllowed": True, "numericBankrollAvailable": False,
        "bankroll": None, "bankrollRedacted": True,
        "valueType": "KALSHI_AVAILABLE_CASH_BALANCE",
        "source": "kalshi_authenticated_balance",
        "consumerSizingVerdict": {"verdict": "NO_DOLLAR_SIZING_FOR_THIS_CONSUMER"},
    })
    manifest, _bundles = _project(card)

    assert manifest["bankroll"]["status"] == "FRESH"
    assert manifest["bankroll"]["sizingAllowed"] is True
    assert manifest["bankroll"]["numericBankrollAvailable"] is False
    assert manifest["bankroll"]["valueType"] == "KALSHI_AVAILABLE_CASH_BALANCE"
    assert (manifest["bankroll"]["consumerSizingVerdict"]["verdict"]
            == "NO_DOLLAR_SIZING_FOR_THIS_CONSUMER")
    assert "bankroll" not in manifest["bankroll"], "the amount must not be carried here"


# ── 9. prices and semantics are not altered ────────────────────────────

def test_the_compact_table_round_trips_exactly():
    card = _card(TWO_FAMILIES)
    markets = card["bettingEligibleGames"][0]["markets"]
    table = runtime.build_market_table(markets)

    assert runtime.expand_market_table(table) == [
        {runtime._source_key(c): m.get(runtime._source_key(c))
         for c in runtime.MARKET_COLUMNS}
        for m in markets
    ]


def test_rehydrating_a_bundle_reproduces_the_card_row_field_for_field():
    card = _card(TWO_FAMILIES)
    game = card["bettingEligibleGames"][0]
    _manifest, bundles = _project(card)

    rehydrated = {row["ticker"]: row for row in
                  runtime.rehydrate_card_rows(bundles[GAME_ID])}
    for market in game["markets"]:
        got = rehydrated[market["ticker"]]
        for field, want in market.items():
            assert field in got, f"{market['ticker']}: lost {field}"
            assert got[field] == want, f"{market['ticker']}: {field} changed"


def test_every_card_field_is_carried_derived_or_declared_game_level():
    """No card field may fall out of all three buckets unnoticed."""
    card = _card(TWO_FAMILIES)
    market = card["bettingEligibleGames"][0]["markets"][0]
    covered = (
        {runtime._source_key(c) for c in runtime.MARKET_COLUMNS}
        | set(runtime.DERIVED_MARKET_FIELDS)
        | set(runtime.GAME_LEVEL_MARKET_FIELDS)
    )
    assert set(market) <= covered, sorted(set(market) - covered)


def test_altering_a_price_in_a_bundle_is_caught_by_verification():
    card = _card(TWO_FAMILIES)
    manifest, bundles = _project(card)
    table = bundles[GAME_ID]["markets"]
    column = table["columns"].index("yesAsk")
    table["rows"][0][column] = 0.99

    problems = runtime.verify(card, manifest, bundles)
    assert any("yesAsk changed" in p for p in problems), problems


def test_removing_a_row_from_a_bundle_is_caught_by_verification():
    card = _card(TWO_FAMILIES)
    manifest, bundles = _project(card)
    bundles[GAME_ID]["markets"]["rows"].pop()

    problems = runtime.verify(card, manifest, bundles)
    assert any("market COUNT changed" in p for p in problems), problems
    assert any("absent from the" in p for p in problems), problems


def test_the_bundle_labels_production_projections_as_reference_not_truth():
    card = _card(TWO_FAMILIES)
    _manifest, bundles = _project(card)
    projections = bundles[GAME_ID]["context"]["productionProjections"]

    assert "REFERENCE_ONLY" in projections["_label"]
    assert projections["modelProb"] == {"away": 55.0, "home": 45.0}
    assert projections["marketLedger"][0]["market"] == "NRFI"
    # And nothing in this layer invents a probability for a market.
    assert bundles[GAME_ID]["markets"]["gameLevelConstants"]["modelProbability"] is None


def test_the_bundle_carries_the_evidence_the_playbook_requires():
    card = _card(TWO_FAMILIES)
    _manifest, bundles = _project(card)
    context = bundles[GAME_ID]["context"]

    assert context["available"] is True
    assert context["park"]["parkFactor"] == 112
    assert context["away"]["startingPitcher"]["name"] == "A Starter"
    assert context["home"]["bullpen"]["xFIP"] == 5.0
    # HANDICAPPING_PLAYBOOK.md section 6 requires this line VERBATIM.
    assert context["away"]["offense"]["offenseFormLine"].startswith("L7: 5.1 avg")
    assert context["away"]["offense"]["offenseFormLabel"] == "OUTLIER_INFLATED"
    assert context["home"]["lineup"]["officialLineup"][0]["name"] == "A Hitter"
    # Weather is not captured by this pipeline; it is stated, not invented.
    assert context["weather"] is None
    assert "acknowledged gap" in context["weatherNote"]


# ── 18. the contract cannot drift from the code ────────────────────────

def test_the_manifests_execution_constants_are_config_rules_json_verbatim():
    """They are PROJECTED from the canonical config at build time, so
    there is no second copy to drift. This pins that."""
    rules = _rules()
    constants = runtime.execution_constants(rules)

    for key in runtime.EXECUTION_CONSTANT_KEYS:
        assert constants[key] == rules[key], key
    assert constants["_source"] == "config/rules.json"
    assert constants["_sourceVersion"] == rules["_version"]


def test_no_threshold_is_restated_as_a_literal_in_the_runtime_module():
    """A hand-copied 3.0 or 0.187 in this module is the drift the
    projection exists to prevent."""
    source = open(os.path.join(ROOT, "lib", "handicap_runtime.py"),
                  encoding="utf-8").read()
    rules = _rules()
    literals = set()
    for tier in rules["calibration"].values():
        literals.add(repr(tier["factor"]))
    for value in rules["edge_thresholds"].values():
        if isinstance(value, (int, float)):
            literals.add(repr(float(value)))
    # `repr` of each is a distinctive token (0.187, 0.255, ...). None of
    # them may appear as a literal anywhere in the module.
    for literal in literals:
        assert literal not in source, f"{literal} is hard-coded in lib/handicap_runtime.py"


def test_run_the_slate_documents_the_runtime_flow_the_code_publishes():
    """RUN_THE_SLATE.md quotes `CONSUMER_FLOW`. If the code's flow
    changes and the doc does not, this fails rather than letting a chat
    follow a stale contract."""
    doc = open(os.path.join(ROOT, "RUN_THE_SLATE.md"), encoding="utf-8").read()
    for step in runtime.CONSUMER_FLOW:
        assert step in doc, f"RUN_THE_SLATE.md is missing the published step: {step}"


def test_run_the_slate_points_the_consumer_at_the_manifest_not_the_card():
    doc = open(os.path.join(ROOT, "RUN_THE_SLATE.md"), encoding="utf-8").read()
    assert "data/handicap_runtime/<DATE>/manifest.json" in doc
    assert "scripts/build_handicap_runtime.py" in doc


def test_the_runtime_paths_in_the_doc_match_the_code():
    doc = open(os.path.join(ROOT, "RUN_THE_SLATE.md"), encoding="utf-8").read()
    assert runtime.RUNTIME_ROOT.replace(os.sep, "/") in doc


def test_the_doc_tells_a_dateless_consumer_where_to_start():
    """"RUN MLB" carries no date. A consumer that has to guess one can
    guess yesterday, and yesterday's slate has already been played."""
    doc = open(os.path.join(ROOT, "RUN_THE_SLATE.md"), encoding="utf-8").read()
    assert "data/handicap_runtime/latest.json" in doc


def test_the_latest_pointer_routes_to_the_manifest_and_carries_no_markets(tmp_path):
    card = _card(TWO_FAMILIES, bankroll={
        "status": "FRESH", "numericBankrollAvailable": False,
        "consumerSizingVerdict": {"verdict": "NO_DOLLAR_SIZING_FOR_THIS_CONSUMER"},
    })
    manifest, path, problems = runtime_builder.build(
        card, _slate(), rules=_rules(), root=str(tmp_path))
    assert problems == []

    with open(os.path.join(str(tmp_path), "latest.json"), encoding="utf-8") as handle:
        pointer = json.load(handle)

    assert pointer["date"] == card["date"]
    assert pointer["manifest"] == path.replace(os.sep, "/")
    assert pointer["bettingEligibleGames"] == 1
    assert pointer["silentRemainderTotal"] == 0
    # `bankrollStatus: FRESH` alone would invite a chat to invent dollar
    # stakes. The consumer verdict travels with it.
    assert pointer["dollarSizingVerdict"] == "NO_DOLLAR_SIZING_FOR_THIS_CONSUMER"
    assert pointer["numericBankrollAvailable"] is False
    assert "bankroll" not in pointer, "the amount must not be in the pointer"

    blob = json.dumps(pointer)
    for market in card["bettingEligibleGames"][0]["markets"]:
        assert market["ticker"] not in blob
    assert len(blob) < 2048, "the pointer must stay a pointer"


# ── the real 2026-09-18 slate, end to end ──────────────────────────────

REAL_CARD = os.path.join(ROOT, "data", "handicapping_card", "2026-09-18.json")


@pytest.fixture(scope="module")
def real_projection(tmp_path_factory):
    if not os.path.exists(REAL_CARD):
        pytest.skip("the 2026-09-18 card is not present in this checkout")
    with open(REAL_CARD, encoding="utf-8") as handle:
        card = json.load(handle)
    slate_path = os.path.join(ROOT, "data", "slates", "2026-09-18", "authoritative.json")
    slate = None
    if os.path.exists(slate_path):
        with open(slate_path, encoding="utf-8") as handle:
            slate = json.load(handle)
    out = tmp_path_factory.mktemp("runtime")
    manifest, path, problems = runtime_builder.build(
        card, slate, rules=_rules(), root=str(out))
    assert problems == [], problems[:10]
    return card, manifest, path, str(out)


def test_real_slate_projects_with_no_verification_problems(real_projection):
    _card, manifest, _path, _root = real_projection
    assert manifest["date"] == "2026-09-18"
    assert manifest["counts"]["silentRemainderTotal"] == 0
    assert manifest["completeness"]["holds"] is True
    assert manifest["completeness"]["bundleRowsEqualCardMarkets"] is True


def test_real_slate_loses_no_market_anywhere(real_projection):
    card, manifest, _path, root = real_projection
    card_games = {g["gameId"]: g
                  for g in card["bettingEligibleGames"] + card["researchOnlyGames"]}
    total = 0
    for row in manifest["games"]:
        with open(os.path.join(root, "2026-09-18", "games", f"{row['gameId']}.json"),
                  encoding="utf-8") as handle:
            bundle = json.load(handle)
        card_tickers = sorted(m["ticker"] for m in card_games[row["gameId"]]["markets"])
        bundle_tickers = sorted(
            r["ticker"] for r in runtime.expand_market_table(bundle["markets"]))
        assert card_tickers == bundle_tickers
        total += len(bundle_tickers)

    assert total == (card["counts"]["eligibleMarketsTotal"]
                     + card["counts"]["researchOnlyMarketsTotal"])
    assert total == card["counts"]["rawUniverseContracts"] == 4247


def test_real_slate_eligible_market_count_is_identical_to_the_card(real_projection):
    _card, manifest, _path, _root = real_projection
    eligible = [row for row in manifest["games"] if row["bettingEligible"]]

    assert len(eligible) == manifest["counts"]["bettingEligibleGames"] == 8
    assert sum(row["marketsToInspect"] for row in eligible) == \
        manifest["counts"]["eligibleMarketsTotal"] == 1903


def test_real_slate_gzip_bundles_decompress_to_the_same_json(real_projection):
    _card, manifest, _path, root = real_projection
    for row in manifest["games"][:3]:
        plain = os.path.join(root, "2026-09-18", "games", f"{row['gameId']}.json")
        with open(plain, encoding="utf-8") as handle:
            want = json.load(handle)
        with gzip.open(plain + ".gz", "rt", encoding="utf-8") as handle:
            assert json.load(handle) == want


def test_the_committed_runtime_serves_the_real_run_mlb_consumer_path():
    """END TO END, against what is actually committed.

    `scripts/verify_handicap_runtime.py` walks the contract in
    RUN_THE_SLATE.md -- pointer, manifest, eligible bundles, and nothing
    else -- and fails if a betting-eligible game's complete market
    universe is not reachable that way, or if the path touches the
    archival card. This runs it.
    """
    if not os.path.exists(os.path.join(ROOT, "data", "handicap_runtime", "latest.json")):
        pytest.skip("no committed runtime in this checkout")
    verifier = _load("verify_handicap_runtime")
    assert verifier.main(["--audit-against-card"]) == 0


def test_rebuilding_an_older_date_does_not_move_the_pointer_backwards(tmp_path):
    """A replay of an archived date must not repoint a fresh chat at a
    slate that has already been played."""
    today = _card(TWO_FAMILIES)
    runtime_builder.build(today, _slate(), rules=_rules(), root=str(tmp_path))

    older = _card(TWO_FAMILIES)
    older["date"] = "2026-09-01"
    for block in older["bettingEligibleGames"] + older["researchOnlyGames"]:
        for market in block["markets"]:
            market["date"] = "2026-09-01"
    _manifest, path, problems = runtime_builder.build(
        older, _slate(), rules=_rules(), root=str(tmp_path))
    assert problems == []

    # The older date's OWN manifest is written...
    assert os.path.exists(path)
    assert "2026-09-01" in path
    # ...and the pointer still names the newer one.
    with open(os.path.join(str(tmp_path), "latest.json"), encoding="utf-8") as handle:
        assert json.load(handle)["date"] == DATE


def test_the_consumer_path_never_lists_a_governance_document():
    """The old contract's six-document startup read is what the runtime
    exists to remove; the verifier measures it, so it must still name it."""
    verifier = _load("verify_handicap_runtime")
    assert set(verifier.RUNTIME_STARTUP_DOCS) == {
        "HANDICAPPING_PLAYBOOK.md", "PLAYBOOK_LESSONS.md"}
    assert set(verifier.LEGACY_STARTUP_DOCS) > set(verifier.RUNTIME_STARTUP_DOCS)
    for name in verifier.LEGACY_STARTUP_DOCS:
        assert os.path.exists(os.path.join(ROOT, name)), name


def test_real_slate_is_dramatically_smaller_to_handicap(real_projection):
    """The only size test. Everything above is about not losing data;
    this is the reason for doing any of it."""
    _card, manifest, path, _root = real_projection
    card_bytes = os.path.getsize(REAL_CARD)
    manifest_bytes = os.path.getsize(path)
    eligible_bytes = sum(row["bundleBytes"] for row in manifest["games"]
                         if row["bettingEligible"])

    assert card_bytes > 8_000_000
    # A consumer that only wants to know what to do next reads ~32 KB.
    assert manifest_bytes < 64 * 1024
    # A consumer that handicaps the whole eligible slate reads well under
    # a tenth of the archival card.
    assert manifest_bytes + eligible_bytes < card_bytes / 10
