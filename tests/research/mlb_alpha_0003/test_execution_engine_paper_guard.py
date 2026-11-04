"""MLB-ALPHA-0003: execution economics, opportunity engine, paper ledger, live-execution guard."""
import ast
import gzip
import json
import os

import pytest

np = pytest.importorskip("numpy")

from lib.edgelab import kalshi_fees as kf  # noqa: E402
from lib.edgelab.mlb_alpha import anchor, engine, execution as ex, live_guard as lg, paper  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SURFACE = os.path.join(REPO, "data/edgelab/research_artifacts/mlb_alpha_0003/anchor_surface_v1.json.gz")
EV = "26OCT152000MILLAD"
BOOK = {"yes_dollars": [["0.40", "100"], ["0.38", "300"]], "no_dollars": [["0.55", "80"], ["0.50", "500"]]}


# ----------------------------------------------------------------- execution
def test_prices_come_from_each_sides_own_resting_orders():
    q = ex.best_quotes(BOOK)
    assert q["yesBid"] == 0.40 and q["yesAsk"] == 0.45          # 1 - best NO bid
    assert q["noBid"] == 0.55 and q["noAsk"] == 0.60            # 1 - best YES bid


def test_fill_walks_depth_and_charges_fee_on_filled_quantity():
    f = ex.fill_buy(BOOK, "YES", 100)
    assert f["filled"] == 100 and f["partial"] is False and f["levels"] == 2
    assert f["topPrice"] == 0.45 and f["avgPrice"] == pytest.approx((80 * 0.45 + 20 * 0.50) / 100, abs=1e-4)
    assert f["fee"] == kf.taker_fee(100, round(f["avgPrice"], 4))
    assert f["cash"] == pytest.approx(f["cost"] + f["fee"], abs=1e-6)


def test_partial_fill_is_reported_not_scaled():
    f = ex.fill_buy({"no_dollars": [["0.55", "10"]], "yes_dollars": []}, "YES", 50)
    assert f["filled"] == 10 and f["partial"] is True


def test_budget_sizing_respects_cash_including_fee():
    n = ex.contracts_for_budget(BOOK, "YES", 10.0)
    f = ex.fill_buy(BOOK, "YES", n)
    assert f["cash"] <= 10.0 + 1e-9 and ex.fill_buy(BOOK, "YES", n + 1)["cash"] > 10.0


def test_break_even_is_above_the_price():
    assert ex.break_even_probability(0.50) == pytest.approx(0.50 + 0.07 * 0.25)
    assert ex.net_edge_per_contract(0.50, 0.50) < 0


def test_maker_ev_falls_with_adverse_selection():
    assert ex.maker_bid_ev(0.5, 0.45, 0.2, 0.0) > ex.maker_bid_ev(0.5, 0.45, 0.2, 0.05)


def test_kelly_is_zero_without_a_fee_adjusted_edge():
    assert ex.kelly_fraction(0.50, 0.50) == 0.0
    assert 0.0 < ex.kelly_fraction(0.60, 0.50) <= 0.05


# ----------------------------------------------------------------- engine
@pytest.fixture(scope="module")
def surface():
    return anchor.Surface.from_json(json.load(gzip.open(SURFACE, "rt")))


def _book_at(yes_bid, yes_ask, size=500):
    return {"yes_dollars": [[("%.2f" % yes_bid), str(size)]], "no_dollars": [[("%.2f" % (1 - yes_ask)), str(size)]]}


def _scan(surface, books, validation=None, anchor_row=None):
    cfg = engine.EngineConfig(n_sim=40_000, n_script_sim=15_000)
    mk = [{"ticker": t, "book": b, "minToStart": 60.0} for t, b in books.items()]
    return engine.scan_game("g", mk, anchor_row or {"pHome": 0.55, "line": 8.5, "pOver": 0.5}, surface, validation, cfg)


def test_fairly_priced_game_yields_honest_passes(surface):
    from lib.edgelab.mlb_alpha import pricer, sim, taxonomy as tx
    la, lh, _ = anchor.solve(surface, 0.55, 8.5, 0.5)
    r = sim.run(la, lh, sim.Draws(n=40_000, seed=20261027))
    books = {}
    for t in ("KXMLBGAME-%s-LAD" % EV, "KXMLBF5TOTAL-%s-5" % EV, "KXMLBTEAMTOTAL-%s-LAD5" % EV):
        p = pricer.probability(r, tx.parse_market_ticker(t))["p"]
        books[t] = _book_at(max(0.02, p - 0.01), min(0.98, p + 0.01))
    books["KXMLBHIT-%s-MILWCONTRERAS24-2" % EV] = _book_at(0.30, 0.33)
    out = _scan(surface, books)
    verdicts = {o["ticker"]: o["verdict"] for o in out["opportunities"]}
    assert verdicts["KXMLBHIT-%s-MILWCONTRERAS24-2" % EV] == "PASS_UNPRICEABLE"
    assert all(v in ("PASS_NO_EDGE", "PASS_UNPRICEABLE") for v in verdicts.values())
    assert out["selected"] == []


def test_a_large_mispricing_is_paper_only_until_the_family_is_qualified(surface):
    t = "KXMLBF5TOTAL-%s-5" % EV
    out = _scan(surface, {t: _book_at(0.20, 0.22)})            # the sim says this is far more likely than 22 cents
    row = out["opportunities"][0]
    assert row["verdict"] == "PAPER_SHADOW" and row["side"] == "YES" and row["validationStatus"] == "UNVALIDATED"
    assert row["netEdgeConservative"] >= 0.03 and row["scriptsPositive"] >= 5
    assert not any(o["verdict"] == "TRADE_ELIGIBLE" for o in out["opportunities"])


def test_trade_eligible_requires_a_qualified_family_and_failed_families_stay_passed(surface):
    t = "KXMLBF5TOTAL-%s-5" % EV
    q = engine.ValidationRegistry({"KXMLBF5TOTAL|YES": {"status": "QUALIFIED", "evidence": "synthetic test entry"}})
    f = engine.ValidationRegistry({"KXMLBF5TOTAL|YES": {"status": "FAILED", "evidence": "synthetic test entry"}})
    assert _scan(surface, {t: _book_at(0.20, 0.22)}, q)["opportunities"][0]["verdict"] == "TRADE_ELIGIBLE"
    assert _scan(surface, {t: _book_at(0.20, 0.22)}, f)["opportunities"][0]["verdict"] == "PASS_VALIDATED_NEGATIVE"


def test_thin_one_sided_or_late_books_are_illiquid_not_traded(surface):
    t = "KXMLBF5TOTAL-%s-5" % EV
    thin = _scan(surface, {t: _book_at(0.20, 0.22, size=5)})["opportunities"][0]
    assert thin["verdict"] == "PASS_ILLIQUID"
    one_sided = _scan(surface, {t: {"yes_dollars": [["0.20", "500"]], "no_dollars": []}})["opportunities"][0]
    assert one_sided["verdict"] == "PASS_ILLIQUID"


def test_no_anchor_means_visible_unpriceable_rows(surface):
    out = _scan(surface, {"KXMLBGAME-%s-LAD" % EV: _book_at(0.4, 0.42)}, anchor_row={"pHome": 0.99, "line": 8.5, "pOver": 0.5})
    assert out["anchorStatus"] == "NO_ANCHOR_OR_OUTSIDE_SURFACE"
    assert out["opportunities"][0]["verdict"] == "PASS_UNPRICEABLE"


def test_second_leg_is_added_only_when_it_adds_value(surface):
    books = {"KXMLBF5TOTAL-%s-5" % EV: _book_at(0.20, 0.22), "KXMLBF5TOTAL-%s-4" % EV: _book_at(0.30, 0.32),
             "KXMLBTOTAL-%s-6" % EV: _book_at(0.30, 0.32)}
    out = _scan(surface, books)
    assert 1 <= len(out["selected"]) <= 2
    if len(out["selected"]) == 2:
        # a second leg must be reported with its correlation to the first
        assert all(s["correlationWithOtherSelections"] for s in out["selected"])


# ----------------------------------------------------------------- paper ledger
def test_paper_ledger_is_idempotent_limited_and_settles(tmp_path):
    L = paper.PaperLedger(str(tmp_path / "ledger.jsonl"), per_trade_usd=10, per_game_usd=15, max_open_usd=30)
    kw = dict(run_id="R1", rule_id="ENGINE_V1", ticker="T-1", game="G1", side="YES", contracts=20, book=BOOK, model_prob=0.6)
    a = L.submit_taker(**kw)
    assert a["type"] == "FILL" and a["contracts"] == 20
    assert L.submit_taker(**kw)["reason"] == "DUPLICATE_INTENT"
    big = L.submit_taker(**dict(kw, run_id="R2", contracts=200))
    assert big["type"] == "REJECT" and big["reason"] in ("PER_TRADE_LIMIT",)
    over_game = L.submit_taker(**dict(kw, run_id="R3", ticker="T-2", contracts=15))
    assert over_game["reason"] == "PER_GAME_LIMIT"
    s = L.settle("T-1", True)
    assert s[0]["net"] == pytest.approx(20 - a["cash"], abs=1e-6)
    assert L.settle("T-1", True) == []                      # settling twice does nothing
    rep = L.report()
    assert rep["fills"] == 1 and rep["settled"] == 1 and rep["rejections"]["DUPLICATE_INTENT"] == 1
    # a fresh instance rebuilt from the file reports the same thing
    assert paper.PaperLedger(str(tmp_path / "ledger.jsonl")).report() == rep


def test_next_book_mode_uses_the_worse_book():
    worse = {"yes_dollars": [["0.40", "100"]], "no_dollars": [["0.50", "500"]]}      # ask 0.50
    L = paper.PaperLedger.__new__(paper.PaperLedger)
    L.path, L.bankroll, L.per_trade_usd, L.per_game_usd, L.max_open_usd, L.events = "/dev/null", 100, 100, 100, 100, []
    L._append = lambda ev: L.events.append(ev)
    a = L.submit_taker(run_id="R", rule_id="X", ticker="T", game="G", side="YES", contracts=10, book=BOOK, next_book=worse, mode=paper.TAKER_NEXT_BOOK)
    assert a["avgPrice"] == pytest.approx(0.50)


def test_resting_order_never_fills_on_a_touch_and_respects_queue():
    tape = [{"ts": 10, "yes_price": 0.45, "count": 30, "taker_side": "no"},      # exactly at our YES bid of 0.45
            {"ts": 11, "yes_price": 0.46, "count": 500, "taker_side": "no"},     # above our bid: does not reach us
            {"ts": 12, "yes_price": 0.44, "count": 40, "taker_side": "no"}]      # through our bid: fills us first
    r = paper.simulate_resting_fill(tape, "YES", 0.45, 25, placed_ts=0, queue_ahead=20)
    assert r["filled"] == 25 and r["firstFillTs"] == 10                           # 30 - 20 queue = 10, then 15 more from ts 12
    # a huge queue swallows every print AT the level, but a print THROUGH it (0.44) means the whole level filled first
    deep = paper.simulate_resting_fill(tape, "YES", 0.45, 25, placed_ts=0, queue_ahead=10_000)
    assert deep["filled"] == 25 and deep["firstFillTs"] == 12
    only_touch = paper.simulate_resting_fill(tape[:1], "YES", 0.45, 25, placed_ts=0, queue_ahead=30)
    assert only_touch["filled"] == 0


# ----------------------------------------------------------------- live guard
def test_live_execution_is_disabled_by_construction():
    assert lg.LIVE_EXECUTION_CODE_ENABLED is False
    with pytest.raises(lg.LiveExecutionDisabled):
        lg.submit_order({"ticker": "T"})
    assert not os.path.exists(os.path.join(REPO, lg.AUTHORIZATION_FILE_REQUIRED))


def test_live_guard_imports_no_network_or_credential_machinery():
    src = open(lg.__file__).read()
    tree = ast.parse(src)
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add((n.module or "").split(".")[0])
    assert not imported & {"requests", "urllib", "http", "socket", "ssl", "aiohttp", "httpx", "websocket", "subprocess"}
    assert "environ" not in src.replace("environment variable", "") and "getenv" not in src


def test_policy_refuses_unqualified_strategies_and_enforces_limits(tmp_path):
    pol = lg.ExecutionPolicy(bankroll=lg.PaperBankroll(1000))
    stop = lg.EmergencyStop(str(tmp_path / "STOP"))
    intent = {"game": "G", "cashUsd": 10.0, "limitPrice": 0.40}
    kw = dict(open_by_game={}, open_total=0.0, realised_loss_today=0.0, stop=stop)
    with pytest.raises(lg.RiskLimitBreach, match="NOT_QUALIFIED"):
        pol.validate(intent, validation_status="UNVALIDATED", **kw)
    assert pol.validate(intent, validation_status="QUALIFIED", **kw) is intent
    with pytest.raises(lg.RiskLimitBreach, match="PRICE_OUTSIDE_BAND"):
        pol.validate(dict(intent, limitPrice=0.99), validation_status="QUALIFIED", **kw)
    with pytest.raises(lg.RiskLimitBreach, match="PER_GAME_LIMIT"):
        pol.validate(intent, validation_status="QUALIFIED", **dict(kw, open_by_game={"G": 20.0}))
    with pytest.raises(lg.RiskLimitBreach, match="BANKROLL_FRACTION_LIMIT"):
        lg.ExecutionPolicy(bankroll=lg.PaperBankroll(100)).validate(intent, validation_status="QUALIFIED", **kw)
    stop.trigger("test")
    with pytest.raises(lg.RiskLimitBreach, match="EMERGENCY_STOP_SET"):
        pol.validate(intent, validation_status="QUALIFIED", **kw)


def test_idempotency_key_and_reconciliation():
    a = lg.client_order_id("h", "R", "T", "YES")
    assert a == lg.client_order_id("h", "R", "T", "YES") and a != lg.client_order_id("h", "R", "T", "NO")
    assert lg.reconcile({a: {"contracts": 10, "limitPrice": 0.4}}, {a: {"contracts": 10, "avgPrice": 0.39}}) == {}
    bad = lg.reconcile({a: {"contracts": 10, "limitPrice": 0.4}}, {a: {"contracts": 7, "avgPrice": 0.43}, "zzz": {"contracts": 1, "avgPrice": 0.5}})
    assert set(bad) == {"quantityMismatch", "priceMismatch", "unexpected"}


def test_max_size_within_slippage_handles_edges():
    assert ex.max_size_within_slippage({"yes_dollars": [], "no_dollars": []}, "YES") == 0
    book = {"no_dollars": [["0.55", "100"], ["0.50", "1000"]], "yes_dollars": []}      # asks 0.45 x100 then 0.50 x1000
    n = ex.max_size_within_slippage(book, "YES", 0.01)
    f = ex.fill_buy(book, "YES", n)
    assert f["avgPrice"] - f["topPrice"] <= 0.01 + 1e-9
    assert (ex.fill_buy(book, "YES", n + 1)["avgPrice"] - 0.45) > 0.01 - 1e-9
    assert ex.max_size_within_slippage({"no_dollars": [["0.55", "40"]], "yes_dollars": []}, "YES", 0.0) == 40
