#!/usr/bin/env python3
"""
tests/test_wave0_08_wager_backlog_repair.py
===========================================
WAVE 0.08, Mission A. Guards scripts/edgelab/repair_wager_backlog.py.

This tool writes outcome truth into real wager rows, so the tests that matter
most are the ones proving what it will NOT do: never touch money, never invent
a settlement, never resolve an ambiguous identity, never run without being
asked twice (dry run by default, `--execute` to write).

Every test builds its own ledger under tmp_path. None touch the repository's
real bets.json -- and the session guard in conftest.py independently proves
that for the suite as a whole.
"""

import copy
import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_repair_wager_backlog",
    os.path.join(ROOT, "scripts", "edgelab", "repair_wager_backlog.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

TOOL = os.path.join(ROOT, "scripts", "edgelab", "repair_wager_backlog.py")


def _root_bet(**kw):
    bet = {
        "id": "2026-08-05-001", "date": "2026-08-05", "game": "STL @ LAD",
        "market": "ML_Away", "betSide": "AWAY", "stake": 25.0,
        "actualEntryPrice": 0.44, "kalshiPrice": 44, "odds": -120,
        "result": None, "status": "pending", "pnl": None, "line": None,
        "ticker": "KXMLBGAME-26AUG05STLLAD-STL", "source": "MODEL",
    }
    bet.update(kw)
    return bet


def _canonical(**kw):
    row = {
        "betId": "c-1", "gameDate": "2026-08-05", "matchup": "STL @ LAD",
        "marketFamily": "ML_AWAY", "result": "WIN", "status": "settled",
        "stake": 25.0, "netProfitLoss": 31.8, "returnAmount": 56.8,
        "entryPrice": 0.44, "closingPrice": 0.51, "clv": 0.07,
    }
    row.update(kw)
    return row


def _linescore(away_by_inning, home_by_inning, away_total=None, home_total=None):
    innings = [{"num": i + 1, "away": {"runs": a}, "home": {"runs": h}}
               for i, (a, h) in enumerate(zip(away_by_inning, home_by_inning))]
    return {
        "innings": innings,
        "teams": {"away": {"runs": away_total if away_total is not None else sum(away_by_inning)},
                  "home": {"runs": home_total if home_total is not None else sum(home_by_inning)}},
    }


def _evidence(date="2026-08-05", away="STL", home="LAD", matches=1,
              status="Final", linescore=None, game_pk=771234):
    games = []
    for i in range(matches):
        games.append({"gamePk": game_pk + i, "gameNumber": i + 1,
                      "status": status, "linescore": linescore})
    return {"generatedAt": "2026-09-09T00:00:00Z", "matchups": {
        "%s|%s|%s" % (date, away, home): {
            "date": date, "away": away, "home": home,
            "scheduleMatches": matches, "games": games}}}


def _by_decision(plan):
    return ([p for p in plan if p["decision"] == "PROPOSED"],
            [p for p in plan if p["decision"] == "REFUSED"])


# ── lifecycle propagation ────────────────────────────────────────────────────

def test_lifecycle_propagates_a_unique_terminal_canonical_result():
    plan = R.plan_lifecycle([_root_bet()], [_canonical()])
    proposed, refused = _by_decision(plan)
    assert len(proposed) == 1 and not refused
    changes = proposed[0]["changes"]
    assert changes["result"]["after"] == "WIN"
    assert changes["status"]["after"] == "settled"
    assert changes["pnl"]["after"] == 31.8
    assert proposed[0]["evidence"]["kind"] == "CANONICAL_LEDGER"


def test_lifecycle_writes_only_outcome_fields():
    proposed, _ = _by_decision(R.plan_lifecycle([_root_bet()], [_canonical()]))
    assert set(proposed[0]["changes"]) <= set(R.WRITABLE_FIELDS)
    for money in ("stake", "actualEntryPrice", "kalshiPrice", "odds", "ticker",
                  "betSide", "market", "date", "game"):
        assert money not in proposed[0]["changes"]


def test_lifecycle_refuses_contradictory_counterparts():
    plan = R.plan_lifecycle([_root_bet()],
                            [_canonical(betId="c-1", result="WIN"),
                             _canonical(betId="c-2", result="LOSS")])
    _, refused = _by_decision(plan)
    assert len(refused) == 1
    assert "disagree" in refused[0]["reason"]


def test_lifecycle_refuses_a_non_unique_match_even_when_results_agree():
    plan = R.plan_lifecycle([_root_bet()],
                            [_canonical(betId="c-1"), _canonical(betId="c-2")])
    _, refused = _by_decision(plan)
    assert len(refused) == 1
    assert "not unique" in refused[0]["reason"]


def test_lifecycle_refuses_when_stake_disagrees():
    """A copied profit/loss must never land on a different-sized wager."""
    _, refused = _by_decision(
        R.plan_lifecycle([_root_bet(stake=25.0)], [_canonical(stake=100.0)]))
    assert len(refused) == 1 and "stake disagrees" in refused[0]["reason"]


def test_lifecycle_ignores_a_non_terminal_counterpart():
    """An unsettled canonical row proves nothing."""
    plan = R.plan_lifecycle([_root_bet()], [_canonical(result=None, status="pending")])
    assert plan == []


def test_lifecycle_never_touches_an_already_terminal_root_row():
    plan = R.plan_lifecycle([_root_bet(result="LOSS", status="settled")], [_canonical()])
    assert plan == []


# ── MLB evidence backfill ────────────────────────────────────────────────────

def test_full_game_ml_is_graded_by_the_production_settler():
    bet = _root_bet(market="ML_Away", betSide="AWAY")
    ev = _evidence(linescore=_linescore([1, 0, 2, 0, 1, 0, 0, 0, 1],
                                        [0, 0, 1, 0, 0, 0, 0, 1, 0]))
    proposed, _ = _by_decision(R.plan_mlb([bet], ev, []))
    assert len(proposed) == 1
    assert proposed[0]["changes"]["result"]["after"] == "WIN"   # away 5, home 2
    assert proposed[0]["evidence"]["settler"] == "clv_update.determine_result"
    assert proposed[0]["evidence"]["gamePk"] == 771234


def test_f5_is_graded_by_the_canonical_f5_settler():
    # Away leads 3-1 through five; away wins F5.
    bet = _root_bet(market="F5_ML_Away", betSide="AWAY")
    ev = _evidence(linescore=_linescore([1, 0, 2, 0, 0, 9], [0, 1, 0, 0, 0, 9]))
    proposed, _ = _by_decision(R.plan_mlb([bet], ev, []))
    assert len(proposed) == 1
    assert proposed[0]["changes"]["result"]["after"] == "WIN"
    assert proposed[0]["evidence"]["settler"] == \
        "lib.f5_settlement.settle_f5_from_linescore_api"


def test_f5_tie_follows_the_canonical_rule_and_is_not_reinvented():
    """
    A tie after five innings is a LOSS for F5 ML under the standard Kalshi rule
    that lib/f5_settlement.py already encodes. This test pins that the tool
    defers to it rather than inventing a PUSH.
    """
    bet = _root_bet(market="F5_ML_Away", betSide="AWAY")
    ev = _evidence(linescore=_linescore([1, 0, 1, 0, 0], [0, 1, 1, 0, 0]))
    proposed, _ = _by_decision(R.plan_mlb([bet], ev, []))
    assert len(proposed) == 1
    assert proposed[0]["changes"]["result"]["after"] == "LOSS"
    assert proposed[0]["evidence"]["isTie"] is True


def test_f5_refuses_a_game_that_did_not_reach_five_innings():
    bet = _root_bet(market="F5_ML_Away", betSide="AWAY")
    ev = _evidence(linescore=_linescore([1, 0, 1], [0, 1, 0]))
    _, refused = _by_decision(R.plan_mlb([bet], ev, []))
    assert len(refused) == 1


def test_a_doubleheader_is_always_refused():
    """CR-3: the root ledger has no leg discriminator, so a leg cannot be chosen."""
    bet = _root_bet()
    ev = _evidence(matches=2, linescore=_linescore([1] * 9, [0] * 9))
    _, refused = _by_decision(R.plan_mlb([bet], ev, []))
    assert len(refused) == 1
    assert "doubleheader" in refused[0]["reason"]


def test_a_game_that_is_not_final_is_refused():
    ev = _evidence(status="Postponed", linescore=_linescore([1] * 9, [0] * 9))
    _, refused = _by_decision(R.plan_mlb([_root_bet()], ev, []))
    assert len(refused) == 1 and "not a completed final" in refused[0]["reason"]


def test_a_matchup_with_no_evidence_is_refused():
    _, refused = _by_decision(R.plan_mlb([_root_bet()], {"matchups": {}}, []))
    assert len(refused) == 1 and "no MLB evidence" in refused[0]["reason"]


def test_a_market_production_cannot_grade_is_refused_not_guessed():
    """
    NRFI/YRFI have no automated settlement path in production. The tool must
    decline exactly where clv_update.determine_result declines.
    """
    bet = _root_bet(market="NRFI")
    ev = _evidence(linescore=_linescore([1] * 9, [0] * 9))
    _, refused = _by_decision(R.plan_mlb([bet], ev, []))
    assert len(refused) == 1
    assert "declines to grade" in refused[0]["reason"]


def test_an_unparseable_game_is_refused():
    _, refused = _by_decision(R.plan_mlb([_root_bet(game="not a matchup")],
                                         {"matchups": {}}, []))
    assert len(refused) == 1


def test_mlb_backfill_never_reprocesses_a_lifecycle_row():
    """The two sources must not both propose a change for the same row."""
    bet = _root_bet()
    life = R.plan_lifecycle([bet], [_canonical()])
    ev = _evidence(linescore=_linescore([9] * 9, [0] * 9))
    assert R.plan_mlb([bet], ev, life) == []


# ── writes, immutability, idempotence ────────────────────────────────────────

def test_apply_refuses_to_write_a_non_writable_field():
    bet = _root_bet()
    plan = R.plan_lifecycle([bet], [_canonical()])
    plan[0]["changes"]["stake"] = {"before": 25.0, "after": 999.0}
    with pytest.raises(ValueError, match="non-writable field"):
        R.apply_plan([bet], plan)


def test_apply_leaves_every_immutable_field_byte_identical():
    bets = [_root_bet()]
    before = copy.deepcopy(bets[0])
    R.apply_plan(bets, R.plan_lifecycle(bets, [_canonical()]))
    for field in R.IMMUTABLE_FIELDS:
        assert before.get(field) == bets[0].get(field), field
    assert bets[0]["result"] == "WIN"


def test_closing_price_and_clv_are_never_written():
    """Outcome truth may be backfilled; price truth may not be synthesized."""
    bets = [_root_bet(closingPrice=None, clv=None)]
    R.apply_plan(bets, R.plan_lifecycle(bets, [_canonical()]))
    assert bets[0]["closingPrice"] is None
    assert bets[0]["clv"] is None
    assert bets[0]["result"] == "WIN"
    assert "closingPrice" not in R.WRITABLE_FIELDS
    assert "clv" not in R.WRITABLE_FIELDS


def test_a_second_pass_proposes_nothing(tmp_path):
    """Idempotence, at the planning level."""
    bets = [_root_bet()]
    R.apply_plan(bets, R.plan_lifecycle(bets, [_canonical()]))
    assert R.plan_lifecycle(bets, [_canonical()]) == []


def test_row_key_addresses_rows_without_an_id():
    """30 root rows carry no id; they must still be nameable."""
    bet = _root_bet()
    del bet["id"]
    key = R.row_key(bet, 7)
    assert key["betId"] is None and key["index"] == 7 and key["contentKey"]
    other = _root_bet(stake=99.0)
    del other["id"]
    assert R.row_key(other, 7)["contentKey"] != key["contentKey"]


# ── CLI contract ─────────────────────────────────────────────────────────────

def _write_ledger(tmp_path, bets):
    path = tmp_path / "bets.json"
    path.write_text(json.dumps(bets, indent=2))
    return path


def _canonical_file(tmp_path, rows):
    path = tmp_path / "canonical.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_cli_dry_run_is_the_default_and_writes_nothing(tmp_path):
    ledger = _write_ledger(tmp_path, [_root_bet()])
    canon = _canonical_file(tmp_path, [_canonical()])
    before = ledger.read_bytes()
    proc = subprocess.run(
        [sys.executable, TOOL, "--ledger", str(ledger), "--canonical", str(canon),
         "--out-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "DRY RUN" in proc.stdout
    assert ledger.read_bytes() == before
    assert not (tmp_path / "wager_repair_receipt.json").exists()


def test_cli_execute_writes_and_produces_a_receipt(tmp_path):
    ledger = _write_ledger(tmp_path, [_root_bet()])
    canon = _canonical_file(tmp_path, [_canonical()])
    proc = subprocess.run(
        [sys.executable, TOOL, "--ledger", str(ledger), "--canonical", str(canon),
         "--out-dir", str(tmp_path), "--execute"],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(ledger.read_text())[0]["result"] == "WIN"

    receipt = json.loads((tmp_path / "wager_repair_receipt.json").read_text())
    for field in ("repairBatchId", "ledgerHashBefore", "ledgerHashAfter",
                  "writableFields", "immutableFields", "rows", "counts"):
        assert field in receipt, field
    row = receipt["rows"][0]
    for field in ("rowKey", "preStateHash", "postStateHash", "evidence",
                  "fieldsProvenUnchanged", "changes"):
        assert field in row, field
    assert receipt["ledgerHashBefore"] != receipt["ledgerHashAfter"]


def test_cli_execute_twice_is_a_byte_level_no_op(tmp_path):
    """The idempotence requirement, end to end through the CLI."""
    ledger = _write_ledger(tmp_path, [_root_bet()])
    canon = _canonical_file(tmp_path, [_canonical()])
    args = [sys.executable, TOOL, "--ledger", str(ledger), "--canonical", str(canon),
            "--out-dir", str(tmp_path), "--execute"]
    subprocess.run(args, capture_output=True, text=True, timeout=300, check=True)
    first = ledger.read_bytes()
    subprocess.run(args, capture_output=True, text=True, timeout=300, check=True)
    assert ledger.read_bytes() == first


def test_the_tool_never_creates_a_wager(tmp_path):
    ledger = _write_ledger(tmp_path, [_root_bet()])
    canon = _canonical_file(tmp_path, [_canonical(), _canonical(betId="c-9",
                                                               gameDate="2026-08-06",
                                                               matchup="NYY @ BOS")])
    subprocess.run(
        [sys.executable, TOOL, "--ledger", str(ledger), "--canonical", str(canon),
         "--out-dir", str(tmp_path), "--execute"],
        capture_output=True, text=True, timeout=300, check=True)
    assert len(json.loads(ledger.read_text())) == 1, (
        "a canonical row with no root counterpart must never become a new wager")


def test_the_tool_module_contains_no_price_or_clv_writes():
    with open(TOOL) as handle:
        source = handle.read()
    for forbidden in ('bet["closingPrice"] =', 'bet["clv"] =', 'bet["stake"] =',
                      '"closingPrice":', 'bet["entryPrice"] ='):
        assert forbidden not in source, forbidden
    assert '"result", "status", "pnl"' in source
