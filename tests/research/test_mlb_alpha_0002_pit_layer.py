"""MLB-ALPHA-0002 PIT feature layer: as-of correctness, leakage guards,
identity, CLV convention reuse, credit/idempotence discipline, and the
real-money firewall. Stdlib + pytest only (CI has no numpy); every
analysis module that needs numpy is exercised only through
numpy-free helpers or skipped when numpy is absent."""

import ast
import gzip
import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(REPO, "scripts", "research", "mlb_alpha_0002")
sys.path.insert(0, REPO)


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = load("build_candle_panel")
sp = load("build_sharp_panel")
rk = load("recover_kalshi_history")
pp = load("pull_pinnacle_history")


# ------------------------------------------------------------ candle panel
def test_to_cents_and_two_sided():
    assert cp.to_cents("0.4800") == 48.0
    assert cp.to_cents(None) is None
    assert cp.two_sided((37.0, 48.0, 0.0, 0.0))
    assert not cp.two_sided((None, 48.0, 0.0, 0.0))
    assert not cp.two_sided((0.0, 100.0, 0.0, 0.0))       # one-sided book is not executable
    assert not cp.two_sided((50.0, 49.0, 0.0, 0.0))       # crossed


def test_state_at_never_looks_forward():
    cd = {100: (40.0, 42.0, 1.0, 1.0), 105: (45.0, 47.0, 2.0, 2.0)}
    assert cp.state_at(cd, 104)[4] == 100          # latest candle AT OR BEFORE t
    assert cp.state_at(cd, 105)[4] == 105
    assert cp.state_at(cd, 99) is None


def test_window_sum_only_uses_minutes_at_or_before_t():
    agg = {10: {"n": 1, "yesBuyQty": 5.0, "noBuyQty": 0.0, "block": 0},
           11: {"n": 1, "yesBuyQty": 0.0, "noBuyQty": 7.0, "block": 1}}
    ys, ns, n, b = cp.window_sum(agg, 10, 5)
    assert (ys, ns, n, b) == (5.0, 0.0, 1, 0)        # minute 11 is the future: excluded


def test_candle_panel_features_use_only_past_and_targets_use_only_future(monkeypatch, tmp_path):
    """Synthetic contract: the future is a big jump. No feature may see it."""
    start = cp.EPOCH.replace(year=2026, month=8, day=20, hour=23, minute=5)   # 19:05 ET
    m0 = int((start - cp.EPOCH).total_seconds() // 60)
    cd = {}
    for m in range(m0 - 300, m0):
        cd[m] = (40.0, 42.0, 10.0, 100.0) if m < m0 - 4 else (60.0, 62.0, 10.0, 100.0)   # jump at T-4
    ticker = "KXMLBGAME-26AUG201905BOSNYY-NYY"
    series = {ticker: {"family": "game_result", "gameDate": "2026-08-20", "candles": cd, "trades": {}}}
    monkeypatch.setattr(cp, "load_ticker_series", lambda dates: series)
    monkeypatch.setattr(cp, "load_settlements", lambda: {ticker: "YES"})
    rows = cp.build(["2026-08-20"])
    assert rows, "grid rows expected"
    for r in rows:
        assert r["mid"] == 41.0                       # never the post-jump price
        assert r["dMid30"] == 0.0 and r["minutesUnchanged"] >= 30
        assert r["fairMidMoveToClose"] == 20.0        # the jump is only in the target
        assert r["clvYesCents"] == 20.0 and r["clvNoCents"] == -20.0
        assert r["clvConvention"] == "POSITIVE_IS_GOOD_V1"
        assert r["decisionMinute"] < r["closeMinute"]
    assert rows[0]["minutesToStart"] == cp.MAX_BEFORE and rows[-1]["minutesToStart"] == cp.MIN_BEFORE


def test_candle_panel_skips_contracts_without_a_two_sided_close(monkeypatch):
    start = cp.EPOCH.replace(year=2026, month=8, day=20, hour=23, minute=5)
    m0 = int((start - cp.EPOCH).total_seconds() // 60)
    cd = {m: (0.0, 100.0, 0.0, 0.0) for m in range(m0 - 300, m0)}         # never two-sided
    ticker = "KXMLBGAME-26AUG201905BOSNYY-NYY"
    monkeypatch.setattr(cp, "load_ticker_series", lambda dates: {ticker: {"family": "game_result", "gameDate": "2026-08-20", "candles": cd, "trades": {}}})
    monkeypatch.setattr(cp, "load_settlements", lambda: {ticker: "YES"})
    assert cp.build(["2026-08-20"]) == []


def test_order_flow_imbalance_sign_follows_taker_side(monkeypatch):
    start = cp.EPOCH.replace(year=2026, month=8, day=20, hour=23, minute=5)
    m0 = int((start - cp.EPOCH).total_seconds() // 60)
    cd = {m: (40.0, 42.0, 10.0, 100.0) for m in range(m0 - 300, m0)}
    trades = {m0 - 100: {"n": 3, "yesBuyQty": 30.0, "noBuyQty": 10.0, "block": 0, "lastYesPriceCents": 42.0, "lastTs": "x"}}
    ticker = "KXMLBGAME-26AUG201905BOSNYY-NYY"
    monkeypatch.setattr(cp, "load_ticker_series", lambda dates: {ticker: {"family": "game_result", "gameDate": "2026-08-20", "candles": cd, "trades": trades}})
    monkeypatch.setattr(cp, "load_settlements", lambda: {ticker: "NO"})
    rows = {r["minutesToStart"]: r for r in cp.build(["2026-08-20"])}
    assert rows[100]["ofi10"] == 0.5 and rows[100]["lastTradeMinusMid"] == 1.0
    assert rows[120]["ofi10"] is None                 # trade is in the future for T-120
    assert rows[95]["ofi10"] == 0.5 and rows[85]["ofi10"] is None and rows[85]["ofi30"] == 0.5


# ------------------------------------------------------------ sharp panel
def test_devig_and_american_conversion():
    assert sp.american_to_implied(-110) == pytest.approx(0.5238, abs=1e-3)
    assert sp.american_to_implied(150) == pytest.approx(0.4)
    a, b = sp.devig(0.55, 0.50)
    assert a + b == pytest.approx(1.0) and a > b
    assert sp.devig(None, 0.5) == (None, None)


def test_sharp_panel_as_of_is_capture_time_not_book_update(tmp_path, monkeypatch):
    """A book's `updated` earlier than our capture must NOT make the price
    'known' before we fetched it."""
    d = tmp_path / "data" / "slates" / "2026-08-20"; d.mkdir(parents=True)
    slate = {"games": [{"away": "STL", "home": "CIN", "startTime": "2026-08-20T16:40:00Z",
                        "odds": {"pinnacle": {"ml": {"away": -109, "home": 101, "updated": "2026-08-20T10:00:00Z"}},
                                 "kalshi": {"ml": {"away_ticker": "KXMLBGAME-26AUG201240STLCIN-STL"}}}}]}
    (d / "scheduled_refresh_20260820T163338Z.json").write_text(json.dumps(slate))
    monkeypatch.setattr(sp, "REPO", str(tmp_path))
    monkeypatch.setattr(sp, "ART", str(tmp_path / "art"))
    monkeypatch.setattr(sp, "OUT", str(tmp_path / "art" / "p.jsonl.gz"))
    sp.main()
    rows = [json.loads(l) for l in gzip.open(tmp_path / "art" / "p.jsonl.gz", "rt")]
    assert len(rows) == 1
    assert rows[0]["capturedAt"] == "2026-08-20T16:33:38Z" and rows[0]["captureBasis"] == "filename"
    assert rows[0]["mlUpdated"] == "2026-08-20T10:00:00Z"
    assert rows[0]["gameKey"] == "2026-08-20:26AUG201240STLCIN"
    assert rows[0]["mlHomeVigFree"] == pytest.approx(0.4882, abs=2e-3)


# ------------------------------------------------- recovery / acquisition
def test_recovery_skips_done_tickers_and_records_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(rk, "OUT", str(tmp_path / "hist"))
    monkeypatch.setattr(rk, "MANIFEST", str(tmp_path / "hist" / "m.json"))
    monkeypatch.setattr(rk, "SLEEP", 0.0)
    calls = []
    def fake_http(url, retries=3, timeout=30):
        calls.append(url)
        if "candlesticks" in url:
            return {"candlesticks": [{"end_period_ts": 1, "yes_bid": {}, "yes_ask": {}}]}, None
        return {"trades": [{"trade_id": "t"}], "cursor": ""}, None
    monkeypatch.setattr(rk, "http_json", fake_http)
    t = "KXMLBGAME-26AUG201905BOSNYY-NYY"
    from datetime import datetime
    monkeypatch.setattr(rk, "iter_settled_tickers", lambda fams, dates: [(t, "game_result", "2026-08-20", datetime(2026, 8, 20, 23, 5))])
    monkeypatch.setattr(sys, "argv", ["x"])
    assert rk.main() == 0
    man = json.load(open(tmp_path / "hist" / "m.json"))
    assert man["tickers"][t]["status"] == "done" and man["tickers"][t]["candles"] == 1
    n = len(calls)
    assert rk.main() == 0
    assert len(calls) == n, "second run must make zero API calls for a done ticker"
    for sub in ("candles", "trades"):
        rows = [json.loads(l) for l in gzip.open(tmp_path / "hist" / sub / "2026-08-20.jsonl.gz", "rt")]
        assert len(rows) == 1 and rows[0]["ticker"] == t


def test_recovery_marks_error_and_retries_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(rk, "OUT", str(tmp_path / "hist"))
    monkeypatch.setattr(rk, "MANIFEST", str(tmp_path / "hist" / "m.json"))
    monkeypatch.setattr(rk, "SLEEP", 0.0)
    monkeypatch.setattr(rk, "http_json", lambda url, retries=3, timeout=30: (None, "HTTP 500"))
    from datetime import datetime
    t = "KXMLBGAME-26AUG201905BOSNYY-NYY"
    monkeypatch.setattr(rk, "iter_settled_tickers", lambda fams, dates: [(t, "game_result", "2026-08-20", datetime(2026, 8, 20, 23, 5))])
    monkeypatch.setattr(sys, "argv", ["x"])
    rk.main()
    man = json.load(open(tmp_path / "hist" / "m.json"))
    assert man["tickers"][t]["status"] == "error"
    assert not os.path.exists(tmp_path / "hist" / "candles")


def test_pinnacle_pull_hard_stops_at_credit_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "OUT", str(tmp_path / "pinn"))
    monkeypatch.setattr(pp, "MANIFEST", str(tmp_path / "pinn" / "manifest.json"))
    monkeypatch.setattr(pp, "KEY", "k")
    from datetime import datetime
    monkeypatch.setattr(pp, "scheduled_starts_for_date", lambda d: [datetime(2026, 8, 20, 23, 5)])
    remaining = [1000]
    def fake_get(url):
        remaining[0] -= 20
        return {"timestamp": "2026-08-20T20:00:00Z", "data": []}, str(remaining[0]), None, None
    monkeypatch.setattr(pp, "api_get", fake_get)
    monkeypatch.setattr(pp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", ["x", "--dates", "2026-08-20", "--max-credits", "40", "--step-minutes", "15"])
    assert pp.main() == 0
    man = json.load(open(tmp_path / "pinn" / "manifest.json"))
    assert len(man["snapshots"]) == 3          # first call sets the baseline; stop once 40 credits are spent
    assert man["creditsRemainingLast"] == 940


def test_pinnacle_pull_refuses_without_key(monkeypatch):
    monkeypatch.setattr(pp, "KEY", "")
    monkeypatch.setattr(sys, "argv", ["x", "--dates", "2026-08-20"])
    assert pp.main() == 2


def test_pinnacle_snapshot_grid_is_pregame_only():
    from datetime import datetime
    g = pp.snapshot_grid([datetime(2026, 8, 20, 23, 5)], 1.0, 15)
    assert g[0] == datetime(2026, 8, 20, 22, 5) and g[-1] <= datetime(2026, 8, 20, 23, 5)


# ------------------------------------------------------ firewall / reuse
FORBIDDEN_IMPORTS = ("write_pending_bets", "risk_gate", "build_market_ledger", "log_manual_bet",
                     "bet_eligibility", "promotion_engine", "lib.edgelab.bets", "recommendations")


@pytest.mark.parametrize("name", sorted(f[:-3] for f in os.listdir(SCRIPTS) if f.endswith(".py")))
def test_program_scripts_never_import_betting_paths_or_write_ledgers(name):
    raw = open(os.path.join(SCRIPTS, name + ".py")).read()
    tree = ast.parse(raw)
    # judge code, not docstrings/comments that describe the firewall
    src = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) and isinstance(node.value.value, str):
            src = src.replace(node.value.value, "")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for n in names:
                assert not any(f in (n or "") for f in FORBIDDEN_IMPORTS), "%s imports %s" % (name, n)
    for bad in ("bets.json", "config/rules.json", "data/edgelab/bets", "recommendations.json"):
        assert bad not in src, "%s references %s" % (name, bad)
    assert "POST" not in src or "orders" not in src.lower(), "no order placement"


def test_clv_comes_from_the_canonical_helper_only():
    for name in ("build_candle_panel", "build_kalshi_panel", "family_d_leadlag"):
        src = open(os.path.join(SCRIPTS, name + ".py")).read()
        assert "clv_convention" in src
        assert "clv_for_yes" in src and "clv_for_no" in src


def test_recovery_workflow_refuses_protected_branches_and_commits_only_research_paths():
    raw = open(os.path.join(REPO, ".github", "workflows", "research-kalshi-history-recovery.yml")).read()
    # judge the executable YAML, not the header comment that lists what it never touches
    y = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    assert "Refuse to run against a protected branch" in y
    assert "research_artifacts/mlb_alpha_0002/kalshi_history/" in y
    assert "schedule:" not in y and "pull_request" not in y
    for bad in ("bets.json", "config/rules.json", "write_pending_bets", "risk_gate"):
        assert bad not in y
