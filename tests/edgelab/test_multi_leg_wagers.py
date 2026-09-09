"""Canonical multi-leg (combo/parlay) wager support.

A user-placed Kalshi combo is ONE economic position -- one stake, one
payout, one result -- spanning several contracts. Before this milestone
the canonical ledger could only express one contract per row, so every
such wager had to be left BLOCKED_UNSUPPORTED_COMBO rather than either
(a) invented as a synthetic single ticker or (b) split into N straight
bets, which would have multiplied one stake into N and corrupted risk,
ROI and bankroll.

THE REPRESENTATION: legs are embedded on the parent row, never written as
their own PlacedBet rows. That is what makes "count the position exactly
once" structural rather than a rule every aggregator has to remember --
a leg is not a ledger row, so it cannot contribute stake, P/L, ROI or
bankroll on its own, and no reporting/bankroll code needed to change.
"""
import copy
import importlib.util
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab import bankroll, reports, schema, storage  # noqa: E402
from lib.edgelab.bets import (  # noqa: E402
    build_manual_bet_record, reconcile_with_existing, write_placed_bet,
)
from lib.edgelab.clv import compute_clv_for_bet  # noqa: E402


def _load_script(name):
    path = os.path.join(_ROOT, "scripts", "edgelab", name)
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEGS = [
    {"legKey": "leg-01", "selection": "Toronto wins first 5 innings",
     "marketTicker": "KXMLBF5-26SEP072205TORATH-TOR", "side": "YES", "legResult": "LOSS"},
    {"legKey": "leg-02", "selection": "Toronto over 4.5 team runs",
     "marketTicker": "KXMLBTEAMTOTAL-26SEP072205TORATH-TOR5", "side": "YES", "legResult": "WIN"},
]


def _combo(**over):
    kwargs = dict(
        game_date="2026-09-07", import_batch_id="batch-v1", source_bet_key="combo-key-1",
        market_family="multi_market_combo", entry_method="IMPORTED_RECEIPT", side=None,
        legs=copy.deepcopy(LEGS),
    )
    kwargs.update(over)
    return build_manual_bet_record(None, "2-market Kalshi combo", 24.99, None, None, **kwargs)


def _single(**over):
    kwargs = dict(game_date="2026-09-07", import_batch_id="batch-v1", source_bet_key="single-key-1",
                  entry_method="IMPORTED_RECEIPT")
    kwargs.update(over)
    return build_manual_bet_record("KXMLBF5-26SEP071305ATLPHI-PHI", "PHI F5", 50.0, 0.56, None, **kwargs)


# ------------------------------------------------------------------ (1)
class TestSingleMarketRowsAreUnaffected:
    def test_a_single_row_still_builds_and_validates_identically(self):
        row = _single()
        assert schema.validate_record("placed_bet", row) == []
        assert row["wagerStructure"] == "SINGLE"
        assert row["legs"] == []
        assert row["marketTicker"] == "KXMLBF5-26SEP071305ATLPHI-PHI"

    def test_a_pre_existing_row_without_the_new_fields_still_validates(self):
        """Every row written before this milestone lacks wagerStructure/legs
        entirely. Absent must keep meaning SINGLE -- no migration."""
        row = _single()
        del row["wagerStructure"]
        del row["legs"]
        assert schema.validate_record("placed_bet", row) == []

    def test_real_committed_ledger_rows_all_still_validate(self):
        rows = list(storage.read_records(
            os.path.join(_ROOT, "data", "edgelab", "bets", "bets.jsonl")))
        assert rows, "ledger fixture is empty"
        bad = [(r["betId"], schema.validate_record("placed_bet", r))
               for r in rows if schema.validate_record("placed_bet", r)]
        assert bad == [], f"pre-existing ledger rows stopped validating: {bad[:3]}"

    def test_a_single_wager_still_may_not_omit_ticker_or_price(self):
        with pytest.raises(ValueError, match="marketTicker"):
            build_manual_bet_record(None, "x", 10.0, 0.5, None,
                                    import_batch_id="b", source_bet_key="k")
        with pytest.raises(ValueError, match="entryPrice"):
            build_manual_bet_record("KXMLB-T", "x", 10.0, None, None,
                                    import_batch_id="b", source_bet_key="k")


# ------------------------------------------------------------- (2) + (3)
class TestEconomicsAreCountedExactlyOnce:
    def _day(self, tmp_path):
        path = str(tmp_path / "bets.jsonl")
        for rec in (_single(), _combo()):
            rec["status"], rec["result"] = "settled", "LOSS"
            rec["netProfitLoss"], rec["returnAmount"] = -rec["stake"], 0.0
            write_placed_bet(rec, path=path)
        return list(storage.read_records(path))

    def test_the_combo_contributes_its_stake_exactly_once(self, tmp_path):
        rows = self._day(tmp_path)
        pm = reports.build_postmortem("2026-09-07", rows)
        assert pm["betsPlaced"] == 2, "one combo must be one position, not one-per-leg"
        assert pm["totalRisked"] == pytest.approx(50.0 + 24.99)

    def test_legs_are_not_ledger_rows_at_all(self, tmp_path):
        rows = self._day(tmp_path)
        assert len(rows) == 2
        tickers = [r["marketTicker"] for r in rows]
        for leg in LEGS:
            assert leg["marketTicker"] not in tickers, (
                "a leg became its own ledger row -- it would double-count the combo")

    def test_bankroll_counts_the_combo_stake_once(self, tmp_path):
        rows = self._day(tmp_path)
        summary = bankroll.compute_bankroll_summary([], rows)
        assert summary["settledBankroll"] == pytest.approx(-(50.0 + 24.99))

    def test_leg_results_never_leak_into_profit_and_loss(self, tmp_path):
        """leg-02 WON. The combo still lost the whole stake -- a per-leg
        result is context, never economics."""
        rows = self._day(tmp_path)
        combo = next(r for r in rows if r["wagerStructure"] == "MULTI_LEG")
        assert [l["legResult"] for l in combo["legs"]] == ["LOSS", "WIN"]
        assert combo["netProfitLoss"] == pytest.approx(-24.99)
        pm = reports.build_postmortem("2026-09-07", rows)
        assert pm["realizedEconomics"]["netProfitLoss"] == pytest.approx(-(50.0 + 24.99))


# ------------------------------------------------------------------ (4)
class TestReportsAndPostmortemsHandleTheParent:
    def test_the_postmortem_bet_row_renders_without_a_ticker(self, tmp_path):
        rec = _combo()
        rec["status"], rec["result"], rec["netProfitLoss"] = "settled", "LOSS", -24.99
        pm = reports.build_postmortem("2026-09-07", [rec])
        line = pm["bets"][0]
        assert line["marketTicker"] is None
        assert line["stake"] == pytest.approx(24.99)
        assert line["marketFamily"] == "multi_market_combo"

    def test_a_postmortem_can_link_the_combo_parent(self, tmp_path):
        from lib.edgelab.postmortems import build_postmortem_record
        rec = _combo()
        rec["status"], rec["result"], rec["netProfitLoss"] = "settled", "LOSS", -24.99
        record = build_postmortem_record("2026-09-07", [rec["betId"]], {rec["betId"]: rec})
        assert record["linkedBetIds"] == [rec["betId"]]
        assert record["unresolvedBetReferences"] == []
        assert record["canonicalTotals"]["totalRisked"] == pytest.approx(24.99)


# ------------------------------------------------------------------ (5)
class TestReIngestPreservesMultiLegFields:
    def test_a_legacy_reingest_cannot_flatten_a_combo(self):
        """A legacy ledger has no concept of legs. It must not erase them,
        nor turn the parent back into a single-market wager."""
        stored = _combo()
        stored.update({"status": "settled", "result": "LOSS", "netProfitLoss": -24.99})
        legacy = {
            "betId": stored["betId"], "marketTicker": "KXMLB-SOMETHING-ELSE", "side": "YES",
            "stake": 24.99, "entryPrice": 0.4, "createdAt": "2026-09-09T00:00:00Z",
            "status": "pending", "result": None, "netProfitLoss": None,
        }
        out = reconcile_with_existing(legacy, {stored["betId"]: stored})
        assert out["wagerStructure"] == "MULTI_LEG"
        assert len(out["legs"]) == 2
        assert [l["legKey"] for l in out["legs"]] == ["leg-01", "leg-02"]
        assert out["result"] == "LOSS" and out["status"] == "settled"


# --------------------------------------------------------- (6) + (7) + (8)
class TestImporterBehaviour:
    def _payload(self, tmp_path, **row_over):
        row = {
            "sourceBetKey": "2026-09-07|COMBO|TOR_F5+TOR_TT|24.99", "gameDate": "2026-09-07",
            "selection": "2-market Kalshi combo", "stake": 24.99, "marketFamily": "multi_market_combo",
            "legs": [
                {"legKey": "leg-01", "selection": "Toronto wins first 5 innings", "away": "TOR",
                 "home": "ATH", "marketFamily": "inning_result", "marketHorizon": "F5",
                 "team": "TOR", "side": "YES", "legResult": "LOSS"},
                {"legKey": "leg-02", "selection": "Toronto over 4.5 team runs", "away": "TOR",
                 "home": "ATH", "marketFamily": "team_total", "marketHorizon": "FULL_GAME",
                 "team": "TOR", "threshold": 4.5, "side": "YES", "legResult": "WIN"},
            ],
        }
        row.update(row_over)
        return {"importBatchId": "test-combo-v1", "rows": [row]}

    def _seed_corpus(self, tmp_path):
        for part in ("games", "markets"):
            src = os.path.join(_ROOT, "data", "edgelab", part)
            dst = os.path.join(str(tmp_path), "data", "edgelab", part)
            os.makedirs(dst, exist_ok=True)
            for name in os.listdir(src):
                if name.startswith("2026-09-07"):
                    with open(os.path.join(src, name), "rb") as r, open(os.path.join(dst, name), "wb") as w:
                        w.write(r.read())

    def _run(self, tmp_path, payload):
        script = _load_script("import_bet_batch.py")
        import sys as _sys
        argv = _sys.argv
        _sys.argv = ["import_bet_batch.py", "--json", json.dumps(payload)]
        try:
            code = script.main()
        finally:
            _sys.argv = argv
        rows = list(storage.read_records(os.path.join("data", "edgelab", "bets", "bets.jsonl")))
        return code, rows

    def test_a_second_identical_import_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_corpus(tmp_path)
        payload = self._payload(tmp_path)
        code1, rows1 = self._run(tmp_path, payload)
        code2, rows2 = self._run(tmp_path, payload)
        assert code1 == 0 and code2 == 0
        assert len(rows1) == 1 and len(rows2) == 1, "re-import duplicated the combo"
        assert rows1[0]["betId"] == rows2[0]["betId"]

    def test_the_source_bet_key_gives_a_stable_deterministic_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_corpus(tmp_path)
        _, rows = self._run(tmp_path, self._payload(tmp_path))
        expected = build_manual_bet_record(
            None, "x", 24.99, None, None, import_batch_id="test-combo-v1",
            source_bet_key="2026-09-07|COMBO|TOR_F5+TOR_TT|24.99",
            legs=[{"legKey": "a", "selection": "a"}, {"legKey": "b", "selection": "b"}])["betId"]
        assert rows[0]["betId"] == expected, "combo identity must not depend on leg content"

    def test_an_ambiguous_leg_fails_closed_and_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_corpus(tmp_path)
        # team_total with no threshold matches the whole TOR ladder.
        payload = self._payload(tmp_path)
        del payload["rows"][0]["legs"][1]["threshold"]
        code, rows = self._run(tmp_path, payload)
        assert code == 1
        assert rows == [], "an ambiguous leg must never be written, nor guessed"

    def test_an_unresolvable_leg_keeps_a_null_ticker_never_a_guess(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._seed_corpus(tmp_path)
        payload = self._payload(tmp_path)
        payload["rows"][0]["legs"][0]["team"] = "ZZZ"  # no such team on this date
        code, rows = self._run(tmp_path, payload)
        assert code == 0 and len(rows) == 1
        assert rows[0]["legs"][0]["marketTicker"] is None
        assert rows[0]["legs"][1]["marketTicker"] == "KXMLBTEAMTOTAL-26SEP072205TORATH-TOR5"


# ------------------------------------------------------------------ (9)
class TestClvIsNeverFabricatedForACombo:
    def test_clv_is_explicitly_unavailable_with_a_reason(self):
        rec = _combo()
        result = compute_clv_for_bet(rec, [{"clvQuoteId": "q", "isClosingQuote": True, "yesAsk": 55}])
        assert result["clvStatus"] == "UNAVAILABLE"
        assert result["unavailableReason"] == "ENTRY_PRICE_MISSING"

    def test_clv_collection_skips_a_parent_with_no_ticker(self):
        """Both collect_clv passes key off marketTicker, so a combo parent
        is structurally never picked up -- its clv stays null rather than
        becoming a fabricated single-market number."""
        source = open(os.path.join(_ROOT, "scripts", "edgelab", "collect_clv.py")).read()
        assert 'if bet.get("marketTicker")' in source
        assert 'not bet.get("marketTicker")' in source

    def test_a_combo_parent_carries_no_clv_fields_on_creation(self):
        rec = _combo()
        assert rec["clv"] is None and rec["clvQuoteId"] is None and rec["closingPrice"] is None


# ----------------------------------------------------------------- (10)
class TestSettlementDoesNotPretendToSupportCombos:
    def test_a_combo_parent_has_no_ticker_for_settlement_to_match(self):
        rec = _combo()
        assert rec["marketTicker"] is None
        assert rec["status"] == "pending"

    def test_leg_tickers_are_not_promoted_to_the_parent(self):
        """If a leg ticker leaked onto the parent, automatic settlement
        would grade a 2-leg combo on one contract's outcome."""
        rec = _combo()
        assert rec["marketTicker"] is None
        assert {l["marketTicker"] for l in rec["legs"]} == {
            "KXMLBF5-26SEP072205TORATH-TOR", "KXMLBTEAMTOTAL-26SEP072205TORATH-TOR5"}


# ------------------------------------------------------- structural rules
class TestStructuralInvariants:
    def test_a_combo_needs_at_least_two_legs(self):
        with pytest.raises(ValueError, match="at least 2 legs"):
            _combo(legs=[{"legKey": "leg-01", "selection": "only one"}])

    def test_leg_keys_must_be_unique(self):
        with pytest.raises(ValueError, match="duplicate legKey"):
            _combo(legs=[{"legKey": "dup", "selection": "a"}, {"legKey": "dup", "selection": "b"}])

    def test_a_parent_may_not_carry_its_own_ticker(self):
        with pytest.raises(ValueError, match="must not carry a single marketTicker"):
            build_manual_bet_record("KXMLB-PARENT", "x", 10.0, None, None,
                                    import_batch_id="b", source_bet_key="k",
                                    legs=copy.deepcopy(LEGS))

    def test_legs_are_rejected_on_a_single_wager(self):
        with pytest.raises(ValueError, match="only valid on"):
            build_manual_bet_record("KXMLB-T", "x", 10.0, 0.5, None,
                                    import_batch_id="b", source_bet_key="k",
                                    wager_structure="SINGLE", legs=copy.deepcopy(LEGS))

    def test_leg_index_is_assigned_from_order(self):
        assert [l["legIndex"] for l in _combo()["legs"]] == [0, 1]

    def test_an_unknown_leg_field_is_refused(self):
        with pytest.raises(ValueError, match="unknown field"):
            _combo(legs=[{"legKey": "a", "selection": "a", "wager": 5},
                         {"legKey": "b", "selection": "b"}])

    def test_the_representation_supports_more_than_two_legs(self):
        rec = _combo(legs=[{"legKey": f"leg-{i:02d}", "selection": f"leg {i}"} for i in range(1, 6)])
        assert len(rec["legs"]) == 5
        assert schema.validate_record("placed_bet", rec) == []


# ------------------------------------------------- analytics vocabulary
class TestComboUsesTheCanonicalFamilyVocabulary:
    def test_the_combo_family_canonicalizes_rather_than_falling_through(self):
        """lib.edgelab.market_family_mapping already registered
        "multi_market_combo" for the 2026-08-26..30 manual wagers, so a
        combo gets its own analytics bucket instead of being force-fitted
        into one of the 17 single-market taxonomy families or silently
        landing in UNMAPPED. The importer must use that existing name, not
        invent a second spelling."""
        from lib.edgelab.market_family_mapping import UNMAPPED, canonicalize_market_family
        script = _load_script("import_bet_batch.py")
        assert script.MULTI_LEG_MARKET_FAMILY == "multi_market_combo"
        assert canonicalize_market_family(script.MULTI_LEG_MARKET_FAMILY) != UNMAPPED

    def test_the_committed_ledgers_combo_rows_are_all_canonically_mapped(self):
        from lib.edgelab.market_family_mapping import UNMAPPED, canonicalize_market_family
        rows = list(storage.read_records(
            os.path.join(_ROOT, "data", "edgelab", "bets", "bets.jsonl")))
        combos = [r for r in rows if r.get("wagerStructure") == "MULTI_LEG"]
        assert combos, "expected at least one committed multi-leg wager"
        for r in combos:
            assert canonicalize_market_family(r["marketFamily"]) != UNMAPPED


# --------------------------------------------- historical Aug 23 combo
class TestTheAug23HistoricalComboIsCanonicallyRepresented:
    """The 2026-08-23 3-leg combo (blocked artifact
    2026-08-23-combo-phi4-bal4-mia4-001) sat BLOCKED_SCHEMA_LIMITATION
    until the multi-leg representation existed. Its economics are the
    interesting part: the share card's raw Initial Cost was $9.99 while
    the canonical stake is the user's whole-dollar $10.00, and NO fee was
    ever evidenced -- three straight rows on the SAME date already show
    that exact pattern (24.99/25, 29.99/30, 24.99/25) with every
    fee/cost field left null.
    """

    BET_ID = "a8c98d1ac9377d5c291be2296dbecae06b6b187f"
    LEG_TICKERS = [
        "KXMLBTEAMTOTAL-26AUG231335STLPHI-PHI4",
        "KXMLBTEAMTOTAL-26AUG231335TBBAL-BAL4",
        "KXMLBTEAMTOTAL-26AUG231340WSHMIA-MIA4",
    ]

    def _ledger(self):
        return list(storage.read_records(
            os.path.join(_ROOT, "data", "edgelab", "bets", "bets.jsonl")))

    def _combo(self):
        return next(r for r in self._ledger() if r["betId"] == self.BET_ID)

    def test_it_is_one_multi_leg_parent_with_three_resolved_legs(self):
        c = self._combo()
        assert c["wagerStructure"] == "MULTI_LEG"
        assert c["marketFamily"] == "multi_market_combo"
        assert [l["legIndex"] for l in c["legs"]] == [0, 1, 2]
        assert [l["marketTicker"] for l in c["legs"]] == self.LEG_TICKERS
        assert [l["legResult"] for l in c["legs"]] == ["WIN", "LOSS", "WIN"]

    def test_stake_is_the_whole_dollar_commitment_not_the_share_card_cost(self):
        c = self._combo()
        assert c["stake"] == 10.00
        assert c["shareCardEvidence"]["shareCardInitialCost"] == 9.99
        assert c["shareCardEvidence"]["shareCardPaidOut"] == 0.0

    def test_no_fee_was_invented_to_explain_the_one_cent_gap(self):
        """The $0.01 is consistent with whole-contract rounding leaving
        budget undeployed. Asserting any fee here would be fabricating
        evidence that does not exist."""
        c = self._combo()
        for field in ("contractCost", "entryFees", "totalFees", "exitFees",
                      "actualCashConsumed", "unusedAllocatedCash", "feeStatus"):
            assert c[field] is None, f"{field} must stay null -- no fee is evidenced"

    def test_the_same_date_straight_rows_set_that_precedent(self):
        aug = [r for r in self._ledger() if r.get("gameDate") == "2026-08-23"]
        subdollar = [r for r in aug
                     if (r.get("shareCardEvidence") or {}).get("shareCardInitialCost") is not None
                     and abs(r["shareCardEvidence"]["shareCardInitialCost"] - r["stake"]) > 1e-9]
        assert len(subdollar) >= 3, "expected the same-date sub-dollar Initial Cost precedent rows"
        for r in subdollar:
            assert r["stake"] == round(r["stake"])           # whole-dollar stake
            assert r["contractCost"] is None and r["totalFees"] is None

    def test_no_executed_price_or_clv_was_synthesised(self):
        c = self._combo()
        assert c["entryPrice"] is None, "combo entryPrice must not be derived from max payout / cost"
        assert c["clv"] is None

    def test_the_legs_are_not_ledger_rows_and_the_day_totals_once(self):
        ledger = self._ledger()
        aug = [r for r in ledger if r.get("gameDate") == "2026-08-23"]
        assert len(aug) == 22, "21 straight + 1 combo parent"
        parents = [r for r in aug if r.get("wagerStructure") == "MULTI_LEG"]
        assert len(parents) == 1
        # The three leg tickers DO exist as separate straight wagers the user
        # also placed -- but none of them is the combo's leg row.
        for t in self.LEG_TICKERS:
            same = [r for r in aug if r.get("marketTicker") == t]
            # .get(): rows written before PR #196 have no wagerStructure key at
            # all, which is exactly the backward-compatible "absent means SINGLE".
            assert len(same) == 1 and same[0].get("wagerStructure") != "MULTI_LEG"
        assert round(sum(r["stake"] for r in aug), 2) == 441.00

    def test_the_postmortem_links_it_and_reconciles(self):
        pm = json.load(open(os.path.join(
            _ROOT, "data", "edgelab", "postmortems", "2026-08-23", "postmortem.json")))
        assert self.BET_ID in pm["linkedBetIds"]
        assert len(pm["linkedBetIds"]) == 22
        assert pm["unresolvedBetReferences"] == []
        assert pm["canonicalTotals"]["totalRisked"] == pytest.approx(441.00)
        assert pm["canonicalTotals"]["netProfitLoss"] == pytest.approx(78.41)
        assert pm["totalsMatch"] is True

    def test_no_blocked_schema_limitation_wager_remains_for_this_date(self):
        pm = json.load(open(os.path.join(
            _ROOT, "data", "edgelab", "postmortems", "2026-08-23", "postmortem.json")))
        assert pm["structuredFindings"].get("blockedWagers") == []


class TestTheAug20And21HistoricalCombosAreCanonicallyRepresented:
    """The 2026-08-20 7-leg and 2026-08-21 6-leg combos (blocked artifacts
    2026-08-20-combo-7leg-001 / 2026-08-21-combo-6leg-001) were the last
    two wagers whose ONLY blocker was BLOCKED_SCHEMA_LIMITATION.

    The economics distinction these pin is the one that is easiest to get
    wrong: Aug 21's share card recorded a raw $1.99 Initial Cost against a
    $2.00 whole-dollar stake (the same relationship PR #197 verified for
    Aug 23's $9.99/$10.00), while Aug 20's evidence records NO raw Initial
    Cost at all. $1.99 must therefore NOT be copied onto the Aug 20 row
    just because the two combos share a $2 rounded risk -- an absent fact
    stays absent.
    """

    AUG20 = "492da1d63ce32c607472ff13980fb8ffe5169f6b"
    AUG21 = "5dbad7ab6e08a7c3a25b6b5e11ee172abff2524d"

    AUG20_LEGS = [
        ("KXMLBGAME-26AUG201240STLCIN-STL", "WIN"),
        ("KXMLBTEAMTOTAL-26AUG201240STLCIN-STL5", "WIN"),
        ("KXMLBGAME-26AUG201310SFCLE-SF", "LOSS"),
        ("KXMLBGAME-26AUG201410ATHKC-ATH", "LOSS"),
        ("KXMLBTEAMTOTAL-26AUG201410ATHKC-ATH4", "LOSS"),
        ("KXMLBF5-26AUG201310TORTB-TB", "LOSS"),
        ("KXMLBF5-26AUG201410ATLCWS-CWS", "LOSS"),
    ]
    AUG21_LEGS = [
        ("KXMLBF5-26AUG211940NYMCWS-CWS", "LOSS"),
        ("KXMLBF5-26AUG212040CLECOL-CLE", "WIN"),
        ("KXMLBGAME-26AUG212010DETKC-DET", "LOSS"),
        ("KXMLBGAME-26AUG212210PITLAD-LAD", "WIN"),
        ("KXMLBTEAMTOTAL-26AUG211910SFBOS-BOS4", "WIN"),
        ("KXMLBTEAMTOTAL-26AUG212010ATHHOU-HOU5", "LOSS"),
    ]

    def _ledger(self):
        return list(storage.read_records(
            os.path.join(_ROOT, "data", "edgelab", "bets", "bets.jsonl")))

    def _row(self, bet_id):
        return next(r for r in self._ledger() if r["betId"] == bet_id)

    def test_each_is_one_multi_leg_parent_with_every_leg_resolved(self):
        for bet_id, legs in ((self.AUG20, self.AUG20_LEGS), (self.AUG21, self.AUG21_LEGS)):
            c = self._row(bet_id)
            assert c["wagerStructure"] == "MULTI_LEG"
            assert c["marketFamily"] == "multi_market_combo"
            assert c["marketTicker"] is None, "a combo parent has no single ticker"
            assert [l["legIndex"] for l in c["legs"]] == list(range(len(legs)))
            assert [l["marketTicker"] for l in c["legs"]] == [t for t, _ in legs]
            assert [l["legResult"] for l in c["legs"]] == [r for _, r in legs]
            # every leg ticker actually resolved -- never a null placeholder
            assert all(l["marketTicker"] for l in c["legs"])

    def test_stake_is_the_whole_dollar_commitment_for_both(self):
        for bet_id in (self.AUG20, self.AUG21):
            c = self._row(bet_id)
            assert c["stake"] == 2.00
            assert c["shareCardEvidence"]["shareCardPaidOut"] == 0.0
            assert c["confirmedReceiptReturn"] == 0.0
            assert c["confirmedReceiptNetProfitLoss"] == -2.00
            assert c["confirmedReceiptSource"] == "MANUAL_POSTMORTEM_RECEIPT"

    def test_aug21_preserves_its_raw_initial_cost_and_aug20_has_none(self):
        # Aug 21 DID record a raw Initial Cost -- preserved verbatim, and
        # still not equal to stake.
        aug21 = self._row(self.AUG21)
        assert aug21["shareCardEvidence"]["shareCardInitialCost"] == 1.99
        assert aug21["shareCardEvidence"]["shareCardInitialCost"] != aug21["stake"]
        # Aug 20 did NOT. The absent fact must stay absent -- specifically it
        # must not have acquired Aug 21's $1.99 by pattern-matching.
        aug20 = self._row(self.AUG20)
        assert aug20["shareCardEvidence"]["shareCardInitialCost"] is None

    def test_no_fee_or_entry_price_was_invented_for_either(self):
        for bet_id in (self.AUG20, self.AUG21):
            c = self._row(bet_id)
            for field in ("contractCost", "entryFees", "totalFees", "exitFees",
                          "actualCashConsumed", "unusedAllocatedCash", "feeStatus",
                          "entryPrice", "clv", "averageFillPrice"):
                assert c[field] is None, f"{field} was invented on {bet_id}"

    def test_neither_combo_leg_became_its_own_ledger_row(self):
        ledger = self._ledger()
        for bet_id, date, legs in ((self.AUG20, "2026-08-20", self.AUG20_LEGS),
                                   (self.AUG21, "2026-08-21", self.AUG21_LEGS)):
            day = [r for r in ledger if r["gameDate"] == date]
            assert len([r for r in day if r.get("wagerStructure") == "MULTI_LEG"]) == 1
            for ticker, _ in legs:
                # Some leg tickers coincide with a straight wager the user also
                # placed; none of those is ever the combo's own leg row.
                for r in [x for x in day if x.get("marketTicker") == ticker]:
                    assert r.get("wagerStructure") != "MULTI_LEG"

    def test_both_dates_now_reconcile_to_the_full_slate(self):
        expected = {
            "2026-08-20": (8, 116.00, 31.20, -84.80),
            "2026-08-21": (13, 243.00, 365.32, 122.32),
        }
        for date, (n_linked, risked, returned, npl) in expected.items():
            pm = json.load(open(os.path.join(
                _ROOT, "data", "edgelab", "postmortems", date, "postmortem.json")))
            assert len(pm["linkedBetIds"]) == n_linked
            assert pm["unresolvedBetReferences"] == []
            assert pm["canonicalTotals"]["totalRisked"] == pytest.approx(risked)
            assert pm["canonicalTotals"]["totalReturned"] == pytest.approx(returned)
            assert pm["canonicalTotals"]["netProfitLoss"] == pytest.approx(npl)
            assert pm["totalsMatch"] is True
            # the blocker is gone, and recorded as resolved rather than dropped
            assert pm["structuredFindings"].get("blockedWagers") == []
            assert len(pm["structuredFindings"]["formerlyBlockedNowImported"]) == 1

    def test_the_combos_are_linked_into_their_postmortems(self):
        for date, bet_id in (("2026-08-20", self.AUG20), ("2026-08-21", self.AUG21)):
            pm = json.load(open(os.path.join(
                _ROOT, "data", "edgelab", "postmortems", date, "postmortem.json")))
            assert bet_id in pm["linkedBetIds"]
            assert pm["revision"] >= 2
