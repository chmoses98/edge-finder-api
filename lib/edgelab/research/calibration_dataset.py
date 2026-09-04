"""
lib/edgelab/research/calibration_dataset.py
============================================
Point-in-time (PIT) calibration research dataset builder. RESEARCH ONLY.
Reads only already-committed archives; writes only under
data/edgelab/research_artifacts/calibration_research/. Changes no
production behaviour.

WHY THIS EXISTS (see docs/EDGELAB_MLB_CALIBRATION_RESEARCH_2026_09.md):
the prior production-calibration audits (MLB-RSCH-0022, the 2026-09-03
10-day review) scored `data/edgelab/model_evaluations` rows whose
`source == "kalshi_discovery_extension"`.  Those rows were written from
the day's LAST discovery run, which usually happened after first pitch:
~85% of them were created after the game started (median +7h), their
`marketImpliedProbability` is the yes-ask observed at that time (in-game
or already-settled), and their model probability was computed from a
slate whose projections had already absorbed post-start information
(actual starters, lineups).  Neither side of that comparison is a
pregame forecast.

This builder instead replays the archived, prospectively-frozen
PRE_GAME_DECISION captures (data/edgelab/snapshots/<date>/pre_game_decision/
<ts>/frozen/) -- each is a byte-frozen copy of the slate (projection
inputs) and of the Kalshi market universe (bid/ask/mid) taken at the same
instant -- through production's OWN discovery/adapter code
(scripts.discover_kalshi_mlb_markets.discover, lib.kalshi_probability_adapters),
keeping only games that had not started at capture time.  Replaying a
capture with the current code reproduces the archived discovery output
exactly (565/565 contracts on 2026-09-02) except where the code has since
been fixed (total-ladder rung semantics, fixed 2026-09-01), which is the
intended behaviour: the dataset describes what the CURRENT engine says on
genuinely pregame inputs.

Two probability engines are captured because production uses both:
  ENGINE_A  scripts/build_market_ledger.py (11 production markets; the
            frozen recommendation_output marketLedger rows, as written by
            the then-current production code).
  ENGINE_B  lib/kalshi_probability_adapters.py via discover() (the full
            alternate-line universe; recomputed from frozen inputs with
            current code).

Outcomes come from data/edgelab/settlements with two documented
corrections applied: the total-ladder ">= N" correction map
(data/edgelab/research_artifacts/mlb_alpha_0001/corrected_total_settlements.json)
and an F5-spread regrade from independently verified F5 linescores
(f5_settlement_verification.json).  Unverifiable F5 spreads get no
outcome (they were graded on the full-game margin in the archive; see
docs/EDGELAB_F5_SPREAD_SETTLEMENT_HORIZON.md).

Nothing here fits anything.
"""
import datetime as _dt
import glob
import gzip
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab.storage import read_records  # noqa: E402

SNAPSHOT_ROOT = os.path.join(_ROOT, "data", "edgelab", "snapshots")
SETTLEMENTS_DIR = os.path.join(_ROOT, "data", "edgelab", "settlements")
OBSERVATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "observations")
MODEL_EVALUATIONS_DIR = os.path.join(_ROOT, "data", "edgelab", "model_evaluations")
DISCOVERY_DIR = os.path.join(_ROOT, "data", "kalshi", "discovery")
CORRECTED_TOTALS_PATH = os.path.join(_ROOT, "data", "edgelab", "research_artifacts", "mlb_alpha_0001", "corrected_total_settlements.json")
F5_VERIFICATION_PATH = os.path.join(_ROOT, "data", "edgelab", "research_artifacts", "mlb_alpha_0001", "f5_settlement_verification.json")
OUTPUT_DIR = os.path.join(_ROOT, "data", "edgelab", "research_artifacts", "calibration_research")

PREGAME_STATUSES = {"Scheduled", "Pre-Game", "Warmup"}
ENGINE_A_MARKETS = ("NRFI", "YRFI", "F5_ML_Away", "F5_ML_Home", "TT_Away_Over", "TT_Home_Over",
                    "ML_Away", "ML_Home", "Game_Total", "RL_Away", "RL_Home")

# Model-version boundaries read off git history / prior artifacts (dates are slate dates).
ERA_BOUNDARIES = {
    "f5_three_way": "2026-08-02",        # F5 legs priced three-way (512268d)
    "pitcher_props_modeled": "2026-08-08",  # KXMLBKS/KXMLBOUTS first modeled (d5677fd)
    "first_inning_context": "2026-08-11",   # NRFI/YRFI lambda from first_inning_context (9b369d9)
    "fee_aware_edge": "2026-08-14",         # qualification on netExecutableEdge (4d00f5e)
    "team_total_v12": "2026-08-21",         # TT off-by-one fix (2d5d8d5); v1.1 rows before
    "rfi_suspended": "2026-08-29",          # KXMLBRFI paper-only (cbb664f)
    "total_rung_ge": "2026-09-01",          # game/inning total rung >= N (b607886 / 2f1962f)
}


def _parse_iso(ts):
    if not ts:
        return None
    ts = ts.replace("Z", "+00:00")
    try:
        d = _dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d


def _capture_ts(dirname):
    # 2026-08-25T163337Z -> aware datetime
    m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})Z$", dirname)
    if not m:
        return None
    return _dt.datetime(int(m.group(1)[:4]), int(m.group(1)[5:7]), int(m.group(1)[8:10]),
                        int(m.group(2)), int(m.group(3)), int(m.group(4)), tzinfo=_dt.timezone.utc)


def _load_json_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def list_captures(snapshot_root=SNAPSHOT_ROOT):
    """Every keyed PRE_GAME_DECISION capture with the three frozen inputs present."""
    out = []
    for date_dir in sorted(glob.glob(os.path.join(snapshot_root, "????-??-??"))):
        date = os.path.basename(date_dir)
        pgd = os.path.join(date_dir, "pre_game_decision")
        if not os.path.isdir(pgd):
            continue
        for cap in sorted(os.listdir(pgd)):
            ts = _capture_ts(cap)
            if ts is None:
                continue
            frozen = os.path.join(pgd, cap, "frozen")
            need = ["market_universe.json.gz", "normalized_slate.json.gz"]
            if not all(os.path.exists(os.path.join(frozen, n)) for n in need):
                continue
            out.append({"date": date, "captureId": cap, "capturedAt": ts, "frozenDir": frozen})
    return out


# ---------------------------------------------------------------- outcomes

def load_outcomes(settlements_dir=SETTLEMENTS_DIR, corrected_totals_path=CORRECTED_TOTALS_PATH,
                  f5_verification_path=F5_VERIFICATION_PATH):
    """
    {ticker: {"outcome": 0/1, "settleDate": ..., "source": ...}} with the two documented
    corrections applied.  Also returns f5 linescores {gamePk: (awayF5, homeF5)} for regrading.
    """
    out = {}
    for fn in sorted(os.listdir(settlements_dir)):
        if not (fn.endswith(".jsonl") or fn.endswith(".jsonl.gz")):
            continue
        settle_date = fn.split(".jsonl")[0]
        for d in read_records(os.path.join(settlements_dir, fn)):
            t = d.get("marketTicker")
            if not t or d.get("outcome") not in ("YES", "NO"):
                continue
            out[t] = {"outcome": 1 if d["outcome"] == "YES" else 0, "settleDate": settle_date,
                      "source": "settlement_store", "gameId": str(d.get("gameId") or "")}
    corrected = 0
    if os.path.exists(corrected_totals_path):
        with open(corrected_totals_path) as f:
            cmap = json.load(f).get("tickers", {})
        for t, v in cmap.items():
            if v.get("corrected") in ("YES", "NO") and t in out:
                new = 1 if v["corrected"] == "YES" else 0
                if new != out[t]["outcome"]:
                    corrected += 1
                out[t]["outcome"] = new
                out[t]["source"] = "settlement_store+total_rung_correction"
            elif v.get("corrected") not in ("YES", "NO") and t in out:
                # unresolved under corrected semantics -> drop rather than trust the wrong grade
                out.pop(t, None)
    f5 = {}
    if os.path.exists(f5_verification_path):
        with open(f5_verification_path) as f:
            for g in json.load(f).get("games", []):
                if g.get("status") == "VERIFIED" and g.get("awayRunsF5") is not None:
                    f5[str(g["gamePk"])] = (int(g["awayRunsF5"]), int(g["homeRunsF5"]))
    return out, f5, corrected


def f5_spread_outcome(contract, f5_scores):
    """Regrade a KXMLBF5SPREAD contract from verified F5 linescores. None when unverifiable."""
    sc = f5_scores.get(str(contract.get("gameId")))
    if sc is None or contract.get("line") is None:
        return None
    away_f5, home_f5 = sc
    team = contract.get("subjectId")
    if team == contract.get("awayTeam"):
        margin = away_f5 - home_f5
    elif team == contract.get("homeTeam"):
        margin = home_f5 - away_f5
    else:
        return None
    return 1 if margin > float(contract["line"]) else 0


# ------------------------------------------------------------ observations

_OBS_CHECKPOINTS = ("FIRST_DAILY", "T_MINUS_90", "T_MINUS_60", "T_MINUS_30", "T_MINUS_15", "T_MINUS_5")


def load_market_quotes(observations_dir=OBSERVATIONS_DIR):
    """
    Per ticker: the pregame CLOSING quote (last observation with
    isValidPregameObservation == True), plus named-checkpoint pregame quotes.
    Prices in cents.  Also returns {ticker: scheduledStart}.
    """
    closing, checkpoints, starts = {}, {}, {}
    for fn in sorted(os.listdir(observations_dir)):
        if not fn.endswith(".jsonl.gz"):
            continue
        for r in read_records(os.path.join(observations_dir, fn)):
            t = r.get("marketTicker")
            if not t:
                continue
            if r.get("scheduledStart"):
                starts[t] = r["scheduledStart"]
            if r.get("isValidPregameObservation") is not True:
                continue
            q = {"capturedAt": r.get("capturedAt"), "yesBid": r.get("yesBid"), "yesAsk": r.get("yesAsk"),
                 "lastPrice": r.get("lastPrice"), "volume": r.get("volume"), "openInterest": r.get("openInterest"),
                 "checkpoint": r.get("checkpoint")}
            prev = closing.get(t)
            if prev is None or (q["capturedAt"] or "") > (prev["capturedAt"] or ""):
                closing[t] = q
            cp = r.get("checkpoint")
            if cp in _OBS_CHECKPOINTS:
                checkpoints.setdefault(t, {})[cp] = q
    return closing, checkpoints, starts


def _mid(bid, ask):
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


# ---------------------------------------------------------------- captures

def _game_index(slate_games):
    idx = {}
    for g in slate_games:
        gid = g.get("gameId")
        if gid is None:
            continue
        idx[int(gid)] = g
    return idx


def _lineup_fields(g):
    a = g.get("awayTeamStats") or {}
    h = g.get("homeTeamStats") or {}
    return {
        "lineupConfirmed": bool(g.get("lineupConfirmed")),
        "lineupStatus": g.get("lineupStatus"),
        "awayLineupConfirmedOfficial": a.get("lineupConfirmedOfficial"),
        "homeLineupConfirmedOfficial": h.get("lineupConfirmedOfficial"),
        "awayLineupDataQuality": a.get("lineupDataQuality"),
        "homeLineupDataQuality": h.get("lineupDataQuality"),
        "awayPitcherName": ((g.get("away") or {}).get("pitcher") or {}).get("name"),
        "homePitcherName": ((g.get("home") or {}).get("pitcher") or {}).get("name"),
        "awayPitcherXFIP": ((g.get("away") or {}).get("pitcherSavant") or {}).get("xFIP"),
        "homePitcherXFIP": ((g.get("home") or {}).get("pitcherSavant") or {}).get("xFIP"),
        "awayAvgIPperStart": ((g.get("away") or {}).get("pitcherSavant") or {}).get("avgIPperStart"),
        "homeAvgIPperStart": ((g.get("home") or {}).get("pitcherSavant") or {}).get("avgIPperStart"),
    }


def _replay_projection_context(g):
    """Production's own projection context recomputed from the frozen slate game (current code)."""
    from scripts.build_market_ledger import compute_game_projection_context
    from lib.kalshi_period_projections import compute_period_projection_context
    out = {}
    try:
        ctx = compute_game_projection_context(g)
        out = {"ctxAwayProjRuns": ctx.get("awayProjRuns"), "ctxHomeProjRuns": ctx.get("homeProjRuns"),
               "ctxTotalProj": ctx.get("totalProj"), "ctxF5AwayProj": ctx.get("f5AwayProj"), "ctxF5HomeProj": ctx.get("f5HomeProj")}
    except Exception as exc:  # noqa: BLE001 - research replay must never abort on one bad game
        out = {"ctxError": str(exc)[:200]}
    for per in ("F3", "F7"):
        try:
            pc = compute_period_projection_context(g, per)
            out["ctx%sAwayProj" % per] = pc.get("awayProj")
            out["ctx%sHomeProj" % per] = pc.get("homeProj")
        except Exception:  # noqa: BLE001
            out["ctx%sAwayProj" % per] = None
            out["ctx%sHomeProj" % per] = None
    return out


def _game_record(g, capture, universe_by_ticker):
    odds = g.get("odds") or {}
    pin = odds.get("pinnacle") or {}
    pvf = g.get("pinnacleVF") or {}
    pf5 = g.get("pinnacleF5VF") or {}
    kf5 = g.get("kalshiF5VF") or {}
    mp = g.get("modelProb") or {}
    ml_markets = (g.get("kalshi") or {}).get("markets") or []
    rec = {
        "date": capture["date"], "captureId": capture["captureId"], "capturedAt": capture["capturedAt"].isoformat(),
        "gameId": int(g["gameId"]), "away": (g.get("away") or {}).get("abbr"), "home": (g.get("home") or {}).get("abbr"),
        "startTime": g.get("startTime"), "status": g.get("status"), "kalshiKey": g.get("kalshiKey"),
        "awayProjRuns": mp.get("awayProjRuns"), "homeProjRuns": mp.get("homeProjRuns"), "totalProj": mp.get("totalProj"),
        "modelMLAway": mp.get("away"), "modelMLHome": mp.get("home"),
        "kalshiVFAway": (g.get("kalshiVF") or {}).get("away"), "kalshiVFHome": (g.get("kalshiVF") or {}).get("home"),
        "kalshiF5VFAway": kf5.get("away"), "kalshiF5VFHome": kf5.get("home"),
        "pinnacleVFAway": pvf.get("away"), "pinnacleVFHome": pvf.get("home"), "pinnacleVFAvailable": pvf.get("available"),
        "pinnacleF5VFAway": pf5.get("away"), "pinnacleF5VFHome": pf5.get("home"), "pinnacleF5VFSource": pf5.get("source"),
        "pinMLAway": (pin.get("ml") or {}).get("away"), "pinMLHome": (pin.get("ml") or {}).get("home"), "pinMLUpdated": (pin.get("ml") or {}).get("updated"),
        "pinTotalLine": (pin.get("total") or {}).get("line"), "pinTotalOver": (pin.get("total") or {}).get("over"), "pinTotalUnder": (pin.get("total") or {}).get("under"),
        "pinTTAwayLine": ((pin.get("teamTotals") or {}).get("away") or {}).get("line"), "pinTTAwayOver": ((pin.get("teamTotals") or {}).get("away") or {}).get("over"), "pinTTAwayUnder": ((pin.get("teamTotals") or {}).get("away") or {}).get("under"),
        "pinTTHomeLine": ((pin.get("teamTotals") or {}).get("home") or {}).get("line"), "pinTTHomeOver": ((pin.get("teamTotals") or {}).get("home") or {}).get("over"), "pinTTHomeUnder": ((pin.get("teamTotals") or {}).get("home") or {}).get("under"),
        "pinRLAway": (pin.get("rl") or {}).get("away"), "pinRLAwayPoint": (pin.get("rl") or {}).get("awayPoint"), "pinRLHome": (pin.get("rl") or {}).get("home"), "pinRLHomePoint": (pin.get("rl") or {}).get("homePoint"),
        "pinF5MLAway": (pin.get("f5ml") or {}).get("away") if isinstance(pin.get("f5ml"), dict) else None,
        "pinF5MLHome": (pin.get("f5ml") or {}).get("home") if isinstance(pin.get("f5ml"), dict) else None,
        "bookMLs": {b: {"away": (odds.get(b) or {}).get("ml", {}).get("away"), "home": (odds.get(b) or {}).get("ml", {}).get("home")}
                    for b in ("fanduel", "draftkings", "betmgm") if isinstance(odds.get(b), dict)},
        "bookTotals": {b: dict((odds.get(b) or {}).get("total") or {}) for b in ("fanduel", "draftkings", "betmgm") if isinstance(odds.get(b), dict)},
        "kalshiMLTickers": {m.get("ticker"): {"yesBid": m.get("yesBid"), "yesAsk": m.get("yesAsk")} for m in ml_markets if m.get("ticker")},
    }
    rec.update(_lineup_fields(g))
    rec.update(_replay_projection_context(g))
    st = _parse_iso(g.get("startTime"))
    rec["minutesToStart"] = round((st - capture["capturedAt"]).total_seconds() / 60.0, 1) if st else None
    rec["pregameAtCapture"] = bool(st and st > capture["capturedAt"] and (g.get("status") in PREGAME_STATUSES))
    return rec


def _resolve_engine_a_ticker(row, game, universe_by_event):
    """Map a production marketLedger row to its Kalshi ticker + YES/NO side."""
    market = row.get("market")
    t = row.get("ticker")
    if market in ("YRFI", "NRFI"):
        base = t or (game.get("_rfiTicker"))
        return (base, "YES" if market == "YRFI" else "NO") if base else (None, None)
    if t:
        return t, "YES"
    ml = (game.get("kalshi") or {}).get("markets") or []
    away = (game.get("away") or {}).get("abbr")
    home = (game.get("home") or {}).get("abbr")
    if market in ("ML_Away", "ML_Home"):
        want = away if market == "ML_Away" else home
        for m in ml:
            if m.get("ticker") and m["ticker"].endswith("-" + str(want)):
                return m["ticker"], "YES"
        return None, None
    if market in ("F5_ML_Away", "F5_ML_Home"):
        # derive the F5 event from the moneyline event ticker: KXMLBGAME-<suffix> -> KXMLBF5-<suffix>
        want = away if market == "F5_ML_Away" else home
        for m in ml:
            et = m.get("eventTicker") or ""
            if et.startswith("KXMLBGAME-"):
                cand = "KXMLBF5-" + et[len("KXMLBGAME-"):] + "-" + str(want)
                if cand in universe_by_event:
                    return cand, "YES"
        return None, None
    return None, None


def build_engine_a_rows(capture, slate_games, universe_by_ticker, recommendation_games):
    rows = []
    gidx = _game_index(slate_games)
    for rg in recommendation_games:
        gid = rg.get("gameId")
        if gid is None:
            continue
        g = gidx.get(int(gid), rg)
        # RFI ticker for NRFI rows (ledger only stamps it on the YRFI row)
        rfi = None
        for r in rg.get("marketLedger") or []:
            if r.get("market") in ("YRFI", "NRFI") and r.get("ticker"):
                rfi = r["ticker"]
        g = dict(g)
        g["_rfiTicker"] = rfi
        st = _parse_iso(g.get("startTime") or rg.get("startTime"))
        mts = round((st - capture["capturedAt"]).total_seconds() / 60.0, 1) if st else None
        pregame = bool(st and st > capture["capturedAt"] and ((g.get("status") or rg.get("status")) in PREGAME_STATUSES))
        for r in rg.get("marketLedger") or []:
            if r.get("modelProb") is None:
                continue
            ticker, side = _resolve_engine_a_ticker(r, g, universe_by_ticker)
            u = universe_by_ticker.get(ticker) if ticker else None
            rows.append({
                "engine": "A", "date": capture["date"], "captureId": capture["captureId"],
                "capturedAt": capture["capturedAt"].isoformat(), "gameId": int(gid),
                "market": r.get("market"), "family": _engine_a_family(r.get("market")),
                "ticker": ticker, "side": side, "line": r.get("line"),
                "modelP": float(r["modelProb"]) / 100.0,
                "kalshiVF": (float(r["kalshiVF"]) / 100.0) if r.get("kalshiVF") is not None else None,
                "executablePriceUsed": (float(r["executablePriceUsed"]) / 100.0) if r.get("executablePriceUsed") is not None else None,
                "pinnacleVF": (float(r["pinnacleVF"]) / 100.0) if r.get("pinnacleVF") is not None else None,
                "yesBid": (u.get("yes_bid") * 100.0) if u and u.get("yes_bid") is not None else None,
                "yesAsk": (u.get("yes_ask") * 100.0) if u and u.get("yes_ask") is not None else None,
                "quoteTs": u.get("snapshot_ts") if u else None,
                "volume": u.get("volume") if u else None,
                "confidence": r.get("confidenceTier") or r.get("confidence"),
                "status": r.get("status"), "rejectionReason": r.get("rejectionReason"),
                "netExecutableEdge": r.get("netExecutableEdge"), "rawEdgeVsVF": r.get("rawEdgeVsVF"),
                "lineupDataQuality": r.get("lineupDataQuality"), "lineupConfirmedOfficial": r.get("lineupConfirmedOfficial"),
                "f5PricingVersion": r.get("f5PricingVersion"),
                "minutesToStart": mts, "pregameAtCapture": pregame, "gameStatus": g.get("status") or rg.get("status"),
                "startTime": g.get("startTime") or rg.get("startTime"),
            })
    return rows


def _engine_a_family(market):
    return {
        "NRFI": "first_inning_run", "YRFI": "first_inning_run",
        "F5_ML_Away": "inning_result", "F5_ML_Home": "inning_result",
        "TT_Away_Over": "team_total", "TT_Home_Over": "team_total",
        "ML_Away": "game_result", "ML_Home": "game_result",
        "Game_Total": "game_total", "RL_Away": "winning_margin", "RL_Home": "winning_margin",
    }.get(market)


def build_engine_b_rows(capture, contracts, slate_games, universe_by_ticker):
    rows = []
    gidx = _game_index(slate_games)
    for c in contracts:
        if c.get("modelSupportStatus") != "SUPPORTED" or c.get("fairProbabilityPct") is None:
            continue
        gid = c.get("gameId")
        g = gidx.get(int(gid)) if gid is not None else None
        st = _parse_iso((g or {}).get("startTime"))
        mts = round((st - capture["capturedAt"]).total_seconds() / 60.0, 1) if st else None
        pregame = bool(st and st > capture["capturedAt"] and (c.get("gameStatus") in PREGAME_STATUSES))
        u = universe_by_ticker.get(c["ticker"]) or {}
        rows.append({
            "engine": "B", "date": capture["date"], "captureId": capture["captureId"],
            "capturedAt": capture["capturedAt"].isoformat(), "gameId": int(gid) if gid is not None else None,
            "ticker": c["ticker"], "eventTicker": c.get("eventTicker"), "seriesTicker": c.get("seriesTicker"),
            "family": c.get("marketFamily"), "period": c.get("period"), "side": "YES",
            "contractSide": c.get("side"), "line": c.get("line"), "subjectType": c.get("subjectType"),
            "subjectId": c.get("subjectId"), "subjectName": c.get("subjectName"),
            "awayTeam": c.get("awayTeam"), "homeTeam": c.get("homeTeam"),
            "modelP": float(c["fairProbabilityPct"]) / 100.0,
            "yesBid": c.get("yesBid"), "yesAsk": c.get("yesAsk"),
            "lastPrice": (u.get("last_price") * 100.0) if u.get("last_price") is not None else None,
            "volume": c.get("volume"), "openInterest": u.get("open_interest"),
            "marketStatus": c.get("marketStatus"), "quoteTs": c.get("currentMarketObservedAt"),
            "isProtectedExpression": c.get("isProtectedExpression"),
            "minutesToStart": mts, "pregameAtCapture": pregame, "gameStatus": c.get("gameStatus"),
            "startTime": (g or {}).get("startTime"),
            "lineupConfirmed": bool((g or {}).get("lineupConfirmed")) if g else None,
            "lineupStatus": (g or {}).get("lineupStatus") if g else None,
        })
    return rows


# ----------------------------------------------------------------- driver

def _era_flags(date):
    return {("era_" + k): (date >= v) for k, v in ERA_BOUNDARIES.items()}


def attach_outcomes_and_quotes(rows, outcomes, f5_scores, closing, checkpoints, archived_discovery):
    for r in rows:
        t = r.get("ticker")
        o = outcomes.get(t) if t else None
        r["outcome"] = None
        r["outcomeSource"] = None
        r["settleDate"] = None
        if r.get("engine") == "B" and r.get("family") == "winning_margin" and r.get("period") == "F5":
            y = f5_spread_outcome(r, f5_scores)
            if y is not None:
                r["outcome"], r["outcomeSource"] = y, "f5_linescore_regrade"
                r["settleDate"] = o["settleDate"] if o else None
        elif o is not None:
            y = o["outcome"]
            if r.get("side") == "NO":
                y = 1 - y
            r["outcome"], r["outcomeSource"], r["settleDate"] = y, o["source"], o["settleDate"]
        cq = closing.get(t) if t else None
        r["closeBid"] = cq["yesBid"] if cq else None
        r["closeAsk"] = cq["yesAsk"] if cq else None
        r["closeMid"] = _mid(cq["yesBid"], cq["yesAsk"]) if cq else None
        r["closeCapturedAt"] = cq["capturedAt"] if cq else None
        r["closeCheckpoint"] = cq["checkpoint"] if cq else None
        cps = checkpoints.get(t, {}) if t else {}
        for cp in _OBS_CHECKPOINTS:
            q = cps.get(cp)
            r["mid_" + cp] = _mid(q["yesBid"], q["yesAsk"]) if q else None
        r["mid"] = _mid(r.get("yesBid"), r.get("yesAsk"))
        if r.get("side") == "NO":
            # express the NO leg's quotes as YES-of-NO so modelP/market/outcome are on one side
            for k in ("mid", "closeMid"):
                if r.get(k) is not None:
                    r[k] = 100.0 - r[k]
            for cp in _OBS_CHECKPOINTS:
                if r.get("mid_" + cp) is not None:
                    r["mid_" + cp] = 100.0 - r["mid_" + cp]
            r["yesBid"], r["yesAsk"] = (100.0 - r["yesAsk"]) if r.get("yesAsk") is not None else None, (100.0 - r["yesBid"]) if r.get("yesBid") is not None else None
        ad = archived_discovery.get((r.get("date"), t)) if t else None
        r["archivedDiscoveryP"] = (ad / 100.0) if ad is not None else None
        r.update(_era_flags(r["date"]))
    return rows


def load_archived_discovery(discovery_dir=DISCOVERY_DIR):
    out = {}
    for fn in sorted(os.listdir(discovery_dir)):
        if not re.match(r"^\d{4}-\d{2}-\d{2}\.json$", fn):
            continue
        with open(os.path.join(discovery_dir, fn)) as f:
            doc = json.load(f)
        for c in doc.get("contracts", []):
            if c.get("modelSupportStatus") == "SUPPORTED" and c.get("fairProbabilityPct") is not None:
                out[(doc.get("date"), c.get("ticker"))] = c["fairProbabilityPct"]
    return out


def build(snapshot_root=SNAPSHOT_ROOT, out_dir=OUTPUT_DIR, log=print):
    from scripts.discover_kalshi_mlb_markets import discover  # production's own discovery engine

    captures = list_captures(snapshot_root)
    log(f"[calibration_dataset] {len(captures)} keyed PRE_GAME_DECISION captures")
    outcomes, f5_scores, n_corrected = load_outcomes()
    log(f"[calibration_dataset] settled tickers={len(outcomes)} total-rung corrections applied={n_corrected} f5 linescores={len(f5_scores)}")
    closing, checkpoints, _starts = load_market_quotes()
    log(f"[calibration_dataset] tickers with a pregame closing quote={len(closing)}")
    archived = load_archived_discovery()

    all_rows, all_games = [], []
    for cap in captures:
        mu = _load_json_gz(os.path.join(cap["frozenDir"], "market_universe.json.gz"))
        ns = _load_json_gz(os.path.join(cap["frozenDir"], "normalized_slate.json.gz"))
        slate = ns.get("data", ns)
        games = slate.get("games") or []
        universe_by_ticker = {m["market_ticker"]: m for m in mu.get("markets", []) if m.get("market_ticker")}
        contracts, _summary = discover(cap["date"], mu, slate)
        rows_b = build_engine_b_rows(cap, contracts, games, universe_by_ticker)
        rows_a = []
        ro_path = os.path.join(cap["frozenDir"], "recommendation_output.json.gz")
        if os.path.exists(ro_path):
            ro = _load_json_gz(ro_path)
            rec_games = (ro.get("data") or {}).get("games") or []
            rows_a = build_engine_a_rows(cap, games, universe_by_ticker, rec_games)
        all_rows.extend(rows_a)
        all_rows.extend(rows_b)
        for g in games:
            if g.get("gameId") is not None:
                all_games.append(_game_record(g, cap, universe_by_ticker))
        log(f"  {cap['date']} {cap['captureId']} games={len(games)} engineA={len(rows_a)} engineB={len(rows_b)}")

    attach_outcomes_and_quotes(all_rows, outcomes, f5_scores, closing, checkpoints, archived)
    os.makedirs(out_dir, exist_ok=True)
    rows_path = os.path.join(out_dir, "pit_rows.jsonl.gz")
    games_path = os.path.join(out_dir, "pit_games.jsonl.gz")
    with gzip.open(rows_path, "wt", encoding="utf-8") as f:
        for r in all_rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    with gzip.open(games_path, "wt", encoding="utf-8") as f:
        for g in all_games:
            f.write(json.dumps(g, sort_keys=True) + "\n")
    manifest = {
        "builtAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "captures": len(captures),
        "rows": len(all_rows),
        "rowsEngineA": sum(1 for r in all_rows if r["engine"] == "A"),
        "rowsEngineB": sum(1 for r in all_rows if r["engine"] == "B"),
        "rowsPregameWithOutcome": sum(1 for r in all_rows if r["pregameAtCapture"] and r["outcome"] is not None),
        "settledTickers": len(outcomes),
        "totalRungCorrectionsApplied": n_corrected,
        "f5LinescoreGames": len(f5_scores),
        "eraBoundaries": ERA_BOUNDARIES,
        "outputs": {"rows": os.path.relpath(rows_path, _ROOT), "games": os.path.relpath(games_path, _ROOT)},
        "researchOnly": True,
        "productionBehaviorChanged": False,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    log(f"[calibration_dataset] wrote {len(all_rows)} rows -> {rows_path}")
    return manifest
