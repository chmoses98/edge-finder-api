#!/usr/bin/env python3
"""
scripts/edgelab/repair_wager_backlog.py
=======================================
WAVE 0.08, Mission A. Historical accounting repair for the root wager ledger.

Writes OUTCOME and LIFECYCLE truth into `bets.json` rows -- and nothing else --
where, and only where, genuine evidence already proves the result. Dry run by
default; `--execute` is required to write anything.

WHAT THIS IS AUTHORIZED TO DO
-----------------------------
Exactly two repairs, each from a different evidence source:

  LIFECYCLE_PROPAGATION
      The canonical EdgeLab wager ledger (data/edgelab/bets/bets.jsonl) already
      holds a TERMINAL result for this same wager while the root ledger still
      says pending. The outcome is not computed here at all -- it is copied from
      canonical truth that already exists. Requires a UNIQUE terminal
      counterpart matched on the reconciler's own (date, teams, market family)
      key, and requires the two ledgers to agree on `stake` first, so a copied
      profit/loss figure can never be attached to a different-sized wager.

  MLB_EVIDENCE_BACKFILL
      No canonical counterpart knows the outcome, so the outcome is derived
      from official MLB Stats API evidence (final status + linescore) using the
      repository's EXISTING settlement helpers -- never a second interpretation
      of market semantics:
          F5_ML_*        -> lib.f5_settlement.settle_f5_from_linescore_api
                            (canonical, including the standard Kalshi rule that
                            a tie after five innings is a LOSS)
          everything else-> clv_update.determine_result, the production root
                            ledger settler for ML / Run Line / Total / Team
                            Total
      Where that helper itself declines to grade a family, this tool declines
      too. It does not know better than production.

WHAT THIS IS NOT AUTHORIZED TO DO
---------------------------------
It never creates a wager, never infers that a recommendation was placed, and
never touches `stake`, executed/entry price, `closingPrice`, `clv`, CLV
convention or unit, `side`, `marketTicker`, `importBatchId`, `sourceBetKey`,
any `confirmedReceipt*` field, or placement provenance. Those are asserted
unchanged per row and recorded in the receipt as `fieldsProvenUnchanged`.

Historical OUTCOME truth may be backfilled. Historical PRICE truth may not be
synthesized. A wager may legitimately become WIN/LOSS/PUSH while keeping
`closingPrice: null` and `clv: null` -- that is a correct end state, not a gap.

REFUSAL IS A FIRST-CLASS OUTCOME
--------------------------------
Any row whose evidence is missing, ambiguous or contradictory is REFUSED with
an explicit reason and left exactly as it was. Refusals are expected and are
never worked around: an ambiguous doubleheader leg, a game that did not reach
the innings the market needs, a market family production cannot grade, a
missing line, an unresolvable side, a non-unique canonical match.

EVIDENCE IS FETCHED SEPARATELY FROM THE REPAIR
----------------------------------------------
`--fetch-evidence PATH` performs the only network step (MLB Stats API) and
writes a durable evidence file. Repair itself reads that file and is fully
offline and deterministic, so the same evidence always produces the same
proposal set and the dry run is a faithful preview of `--execute`.

IDEMPOTENCE
-----------
A second `--execute` over an already-repaired ledger proposes nothing: every
candidate row is selected only while it is still non-terminal.

USAGE
-----
    # 1. in GitHub Actions (network):
    python3 scripts/edgelab/repair_wager_backlog.py --fetch-evidence data/edgelab/operational_health/wager_repair_evidence.json

    # 2. anywhere (offline, no writes):
    python3 scripts/edgelab/repair_wager_backlog.py --evidence <path>

    # 3. write the approved repairs:
    python3 scripts/edgelab/repair_wager_backlog.py --evidence <path> --execute
"""

import argparse
import collections
import copy
import hashlib
import importlib.util
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(_HERE))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import clv_update  # noqa: E402  -- the production root-ledger settler
from lib import f5_settlement  # noqa: E402  -- the canonical F5 grader
from lib.edgelab import mlb_schedule  # noqa: E402
from lib.atomic_json import write_json_atomic  # noqa: E402

LEDGER = os.path.join(ROOT_DIR, "bets.json")
CANONICAL = os.path.join(ROOT_DIR, "data", "edgelab", "bets", "bets.jsonl")
OUT_DIR = os.path.join(ROOT_DIR, "data", "edgelab", "operational_health")

MLB_STATS_API = "https://statsapi.mlb.com/api/v1"

TERMINAL = frozenset({"WIN", "LOSS", "PUSH", "VOID", "CANCELLED", "CANCELED", "NO_ACTION"})

# The only fields this tool may ever write.
#
# `pnl` was in this list and was deliberately removed. The canonical ledger
# carries `netProfitLoss` and it was tempting to copy it across, but two facts
# ruled that out: there is not a single already-settled wager present in BOTH
# ledgers with a numeric figure on each side, so the claim "root.pnl means the
# same thing as canonical.netProfitLoss" has no supporting evidence at all; and
# 398 of the root ledger's 399 settled rows leave `pnl` unset (the one exception
# is a VOID at stake 0). Writing it would therefore invent a money-derived
# figure AND depart from the ledger's own settled-row convention. Outcome truth
# is `result` and `status`; everything downstream of them is money.
WRITABLE_FIELDS = ("result", "status")

# Named money/identity/provenance fields, kept for documentation and for the
# receipt. NOTE: immutability is NOT enforced from this list -- see apply_plan,
# which asserts that EVERY field except WRITABLE_FIELDS is byte-identical.
#
# An enumerated whitelist was the original design and it was too weak. The root
# ledger carries at least two schemas: newer rows use stake/pnl/actualEntryPrice,
# while 79 of the rows this tool proposes to repair are older rows using
# size/pl/price/closingLine, and 14 of those carry a whole fee and Bet-Up-To
# block (betUpToPriceGross, referenceAllocation*, feeMultiplier, ...). None of
# those were covered by the enumerated list, so a bug touching them would not
# have been caught. Enumerating what may change is finite and safe; enumerating
# what may not is neither.
DOCUMENTED_SENSITIVE_FIELDS = (
    "stake", "betSize", "actualEntryPrice", "kalshiPrice", "odds",
    "entryPrice", "executedPrice", "closingPrice", "clv", "clvConvention",
    "clvUnit", "side", "betSide", "betTeam", "ticker", "marketIdentity",
    "market", "line", "date", "game", "source", "createdBy", "entryTimestamp",
    "importBatchId", "sourceBetKey", "modelProb", "edgePct", "confidenceTier",
    "realMoneyBlocked", "scheduledStartTime", "marketImpliedProb", "pnl",
    # older schema
    "size", "pl", "price", "closingLine", "closingLineSource",
    "closingLineTimestamp", "betTimeLine", "betBook", "modelPct", "kalshiPct",
    "trueProbPct", "bet", "confidence", "notes",
    # fee / bet-up-to / bankroll block
    "betUpToPriceGross", "betUpToPriceNet", "feeType", "feeMultiplier",
    "feeSource", "feeScheduleVersion", "grossEdgePct", "expectedFeeDrag",
    "netExecutableEdge", "netExpectedValuePerDollar",
    "feeAdjustedBreakEvenProbability", "referenceAllocationDollars",
    "referenceAllocationContracts", "betType", "bankrollNote",
)


def settled_status_for(bet):
    """
    The settled marker in THIS row's own status vocabulary.

    The ledger uses two: newer rows carry lowercase lifecycle values
    ('pending' / 'open' / 'settled'), older rows carry uppercase
    ('PENDING' / 'SETTLED'). Writing one convention over the other would
    corrupt the field's meaning for part of the ledger, so the row's existing
    case decides.
    """
    current = str(bet.get("status") or "")
    if current and current.isupper():
        return "SETTLED"
    return "settled"

LIFECYCLE_PROPAGATION = "LIFECYCLE_PROPAGATION"
MLB_EVIDENCE_BACKFILL = "MLB_EVIDENCE_BACKFILL"

# NAME aliases only -- never new semantics.
#
# The root ledger carries two market naming conventions. The older, human style
# ("ML", "F5 ML", "Team Total", "Total", "Run Line") is the only one
# clv_update.MARKET_CANONICAL knows. A newer producer writes the EdgeLab style
# ("ML_Away", "F5_ML_Home", "TT_Over", ...), for which normalize_market returns
# None -- so determine_result receives canonical_mkt=None and declines every
# such row. That alias-table gap, not any missing formula, is why these wagers
# were never settled by production: 101 non-terminal rows carry an
# EdgeLab-style market name.
#
# This table maps those names onto the EXACT canonical family clv_update
# already grades. It adds no market, changes no formula, and changes no
# eligibility rule -- it only lets an existing formula recognise a name it
# already has semantics for. The underlying production defect is deliberately
# NOT fixed here: changing clv_update's own alias table would change what
# production settles going forward, which is outside this mission.
EDGELAB_MARKET_NAME_ALIASES = {
    "ML_AWAY": "ML", "ML_HOME": "ML",
    "F5_ML_AWAY": "F5 ML", "F5_ML_HOME": "F5 ML",
    "TT_OVER": "Team Total", "TT_AWAY_OVER": "Team Total",
    "TT_HOME_OVER": "Team Total", "TT OVER": "Team Total",
    "TOTAL OVER": "Total",
}

# The side a market name asserts, where the name itself carries one. Used only
# to CROSS-CHECK the row's own betSide; a contradiction is refused, never
# silently resolved in favour of either source.
MARKET_NAME_SIDE = {
    "ML_AWAY": "AWAY", "ML_HOME": "HOME",
    "F5_ML_AWAY": "AWAY", "F5_ML_HOME": "HOME",
    "TT_AWAY_OVER": "AWAY", "TT_HOME_OVER": "HOME",
}


def norm_abbr(abbr):
    """
    Bridge between two team-abbreviation vocabularies that disagree.

    clv_update.to_abbr normalises Arizona to "ARI"; lib.edgelab.mlb_schedule's
    TEAM_ID_TO_ABBR calls the same club "AZ". Comparing the two directly means
    no Arizona game can ever match its own official schedule entry -- ten
    backlog rows were silently unresolvable for exactly that reason until the
    rehearsal exposed it. Normalising BOTH sides through clv_update's own
    vocabulary is the fix; it is the vocabulary the ledger itself is written in.
    """
    return clv_update.to_abbr(abbr) if abbr else abbr


def canonical_market_name(raw):
    """clv_update's canonical family for a root market string, via alias."""
    direct = clv_update.normalize_market(raw)
    if direct:
        return direct
    return EDGELAB_MARKET_NAME_ALIASES.get(str(raw or "").strip().upper())


def resolve_side(bet, away_abbr):
    """
    Returns (side, refusal_reason). The row's own betSide is authoritative; the
    market name is only ever used to confirm it, or to supply it when absent.
    """
    declared = clv_update.get_betside(bet, away_abbr)
    implied = MARKET_NAME_SIDE.get(str(bet.get("market") or "").strip().upper())
    if declared and implied and declared != implied:
        return None, ("the row's betSide (%s) contradicts the side its market name "
                      "asserts (%s = %s); refusing rather than choosing one"
                      % (declared, bet.get("market"), implied))
    side = declared or implied

    if side not in ("AWAY", "HOME"):
        # Last resort: the row's own `bet` string names a team explicitly, e.g.
        # "MIN F5 ML" for the game "KC @ MIN". clv_update.get_betside reads that
        # string but has an AWAY branch with no HOME counterpart, so it returns
        # None whenever the HOME team was backed -- an asymmetry in production,
        # not real ambiguity. Resolved here only when the answer is unarguable:
        # exactly one of the two teams appears, so there is nothing to choose
        # between. If both or neither appear, this still refuses.
        home_abbr = None
        text = str(bet.get("bet") or "").upper()
        if text:
            away_h, home_h = clv_update.parse_game(bet.get("game") or "")
            home_abbr = home_h
            tokens = {clv_update.to_abbr(t) for t in text.split()}
            hits_away = away_abbr in tokens
            hits_home = home_abbr in tokens
            if hits_away and not hits_home:
                side = "AWAY"
            elif hits_home and not hits_away:
                side = "HOME"
            elif hits_away and hits_home:
                return None, ("the bet string %r names BOTH teams, so the side it "
                              "backs is genuinely ambiguous" % bet.get("bet"))

    if side not in ("AWAY", "HOME"):
        return None, "cannot determine which side of the market was backed"
    return side, None


# ── identity ────────────────────────────────────────────────────────────────

def row_key(bet, index):
    """
    A stable identifier for a root row. 30 rows carry no `id`, so an id alone
    cannot address this ledger; the content hash makes those rows nameable
    without inventing an id for them or mutating their identity.
    """
    payload = json.dumps(
        {k: bet.get(k) for k in ("date", "game", "market", "line", "betSide",
                                 "betTeam", "stake", "actualEntryPrice", "ticker")},
        sort_keys=True, default=str)
    return {
        "index": index,
        "betId": bet.get("id"),
        "contentKey": hashlib.sha256(payload.encode()).hexdigest()[:16],
    }


def row_hash(bet):
    return hashlib.sha256(
        json.dumps(bet, sort_keys=True, default=str).encode()).hexdigest()


def _reconciler():
    spec = importlib.util.spec_from_file_location(
        "_reconcile_bet_ledgers",
        os.path.join(ROOT_DIR, "scripts", "reconcile_bet_ledgers.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_non_terminal(bet):
    return str(bet.get("result") or "").upper() not in TERMINAL


# ── evidence fetch (the only network step) ──────────────────────────────────

def _http_json(url, timeout=20):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "edge-finder-edgelab/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode())
    except Exception:
        return None


def fetch_evidence(bets, out_path):
    """
    Resolves official MLB evidence for every distinct (date, away, home) among
    the non-terminal rows: gamePk, final status, final score and linescore.

    A matchup the schedule lists more than once on a date is a doubleheader.
    Both entries are recorded and the repair step refuses those rows: the root
    ledger carries no leg discriminator, so picking one would be a guess.
    """
    needed = collections.OrderedDict()
    for bet in bets:
        if not is_non_terminal(bet):
            continue
        date = str(bet.get("date") or "")[:10]
        away, home = clv_update.parse_game(bet.get("game") or "")
        if not date or not away or not home:
            continue
        needed[(date, away, home)] = True

    schedules = {}
    games = {}
    for date, away, home in needed:
        if date not in schedules:
            raw = _http_json("%s/schedule?sportId=1&date=%s&gameType=R" % (MLB_STATS_API, date))
            schedules[date] = mlb_schedule.parse_schedule_games(raw) if raw else []
        matches = []
        for g in schedules[date]:
            a = norm_abbr(mlb_schedule.TEAM_ID_TO_ABBR.get(g.get("awayTeamId")))
            h = norm_abbr(mlb_schedule.TEAM_ID_TO_ABBR.get(g.get("homeTeamId")))
            if a == away and h == home:
                matches.append(g)
        entry = {"date": date, "away": away, "home": home,
                 "scheduleMatches": len(matches), "games": []}
        for g in matches:
            line = _http_json("%s/game/%s/linescore" % (MLB_STATS_API, g["gamePk"]))
            entry["games"].append({
                "gamePk": g["gamePk"],
                "gameNumber": g.get("gameNumber"),
                "status": g.get("status"),
                "linescore": line,
            })
        games["%s|%s|%s" % (date, away, home)] = entry

    payload = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "scripts/edgelab/repair_wager_backlog.py --fetch-evidence",
        "evidenceSource": "MLB Stats API (statsapi.mlb.com) schedule + linescore",
        "matchups": games,
    }
    write_json_atomic(payload, out_path)
    resolved = sum(1 for e in games.values() if e["scheduleMatches"] == 1)
    print("[repair] evidence: %d matchups, %d uniquely resolved, %d ambiguous/missing"
          % (len(games), resolved, len(games) - resolved))
    print("[repair] wrote %s" % out_path)
    return 0


# ── planning ────────────────────────────────────────────────────────────────

def _refuse(bet, index, source, reason):
    return {"decision": "REFUSED", "source": source, "reason": reason,
            "rowKey": row_key(bet, index), "date": str(bet.get("date") or "")[:10],
            "game": bet.get("game"), "market": bet.get("market")}


def _propose(bet, index, source, changes, evidence):
    after = copy.deepcopy(bet)
    after.update(changes)
    return {
        "decision": "PROPOSED", "source": source,
        "rowKey": row_key(bet, index),
        "date": str(bet.get("date") or "")[:10],
        "game": bet.get("game"), "market": bet.get("market"),
        "changes": {k: {"before": bet.get(k), "after": v} for k, v in changes.items()},
        "fieldsProvenUnchanged": sorted(f for f in bet if f not in WRITABLE_FIELDS),
        "preStateHash": row_hash(bet),
        "postStateHash": row_hash(after),
        "evidence": evidence,
    }


def plan_lifecycle(bets, canonical_rows):
    """Copy already-existing canonical terminal truth onto stale root rows."""
    rec = _reconciler()
    by_key = collections.defaultdict(list)
    for row in canonical_rows:
        key = rec._canonical_key(row)
        if key:
            by_key[key].append(row)

    out = []
    for index, bet in enumerate(bets):
        if not is_non_terminal(bet):
            continue
        key = rec._root_key(bet)
        if key is None:
            continue
        terminal = [c for c in by_key.get(key, [])
                    if str(c.get("result") or "").upper() in TERMINAL]
        if not terminal:
            continue
        if len(terminal) > 1:
            results = {str(c.get("result")).upper() for c in terminal}
            if len(results) > 1:
                out.append(_refuse(bet, index, LIFECYCLE_PROPAGATION,
                                   "%d canonical counterparts disagree on the result (%s); "
                                   "refusing rather than choosing one"
                                   % (len(terminal), ", ".join(sorted(results)))))
                continue
            out.append(_refuse(bet, index, LIFECYCLE_PROPAGATION,
                               "%d canonical counterparts match this row's (date, teams, "
                               "market family) key; the match is not unique so the wager "
                               "cannot be identified" % len(terminal)))
            continue

        counterpart = terminal[0]
        root_stake, canon_stake = bet.get("stake"), counterpart.get("stake")
        if root_stake is None or canon_stake is None:
            out.append(_refuse(bet, index, LIFECYCLE_PROPAGATION,
                               "cannot confirm the two ledgers describe the same wager: "
                               "stake is missing on one side (root=%r canonical=%r)"
                               % (root_stake, canon_stake)))
            continue
        try:
            same_stake = abs(float(root_stake) - float(canon_stake)) < 1e-9
        except (TypeError, ValueError):
            same_stake = False
        if not same_stake:
            out.append(_refuse(bet, index, LIFECYCLE_PROPAGATION,
                               "stake disagrees between ledgers (root=%r canonical=%r); a "
                               "profit/loss figure must never be copied onto a "
                               "different-sized wager" % (root_stake, canon_stake)))
            continue

        changes = {"result": str(counterpart.get("result")).upper(),
                   "status": settled_status_for(bet)}
        out.append(_propose(bet, index, LIFECYCLE_PROPAGATION, changes, {
            "kind": "CANONICAL_LEDGER",
            "canonicalBetId": counterpart.get("betId"),
            "canonicalResult": counterpart.get("result"),
            "canonicalStatus": counterpart.get("status"),
            "canonicalNetProfitLoss": counterpart.get("netProfitLoss"),
            "matchedOn": "date + normalized teams + normalized market family",
            "source": "data/edgelab/bets/bets.jsonl",
            "stakeAgreement": True,
        }))
    return out


def plan_mlb(bets, evidence, already):
    """Derive outcomes from official MLB evidence via existing settlers."""
    matchups = (evidence or {}).get("matchups") or {}
    handled = {json.dumps(p["rowKey"], sort_keys=True) for p in already
               if p["decision"] == "PROPOSED"}
    out = []
    for index, bet in enumerate(bets):
        if not is_non_terminal(bet):
            continue
        key = json.dumps(row_key(bet, index), sort_keys=True)
        if key in handled:
            continue

        date = str(bet.get("date") or "")[:10]
        away, home = clv_update.parse_game(bet.get("game") or "")
        if not date or not away or not home:
            out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                               "`game` does not resolve to two team abbreviations, so no "
                               "physical MLB game identity can be established"))
            continue

        entry = matchups.get("%s|%s|%s" % (date, away, home))
        if not entry:
            out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                               "no MLB evidence was fetched for %s %s@%s" % (date, away, home)))
            continue
        if entry.get("scheduleMatches", 0) == 0:
            out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                               "the official MLB schedule lists no regular-season %s@%s on %s"
                               % (away, home, date)))
            continue
        if entry.get("scheduleMatches", 0) > 1:
            out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                               "%s@%s on %s is a doubleheader (%d scheduled games) and this "
                               "ledger row carries no leg discriminator; choosing a leg "
                               "would be a guess (audit CR-3)"
                               % (away, home, date, entry["scheduleMatches"])))
            continue

        game = entry["games"][0]
        status = str(game.get("status") or "")
        if status not in ("Final", "Completed Early", "Game Over"):
            out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                               "official game status is %r, not a completed final" % status))
            continue
        linescore = game.get("linescore")
        if not linescore:
            out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                               "no linescore is available for gamePk %s" % game.get("gamePk")))
            continue

        market = str(bet.get("market") or "")
        base_evidence = {
            "kind": "MLB_STATS_API",
            "gamePk": game.get("gamePk"),
            "gameStatus": status,
            "source": "statsapi.mlb.com schedule + linescore",
        }

        if canonical_market_name(market) == "F5 ML":
            side, why = resolve_side(bet, away)
            if side is None:
                out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL, why))
                continue
            try:
                settled = f5_settlement.settle_f5_from_linescore_api(linescore, side)
            except Exception as exc:
                out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                                   "canonical F5 settler declined: %s" % exc))
                continue
            result = settled.get("result")
            if result not in TERMINAL:
                out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                                   "canonical F5 settler returned %r" % result))
                continue
            ev = dict(base_evidence, settler="lib.f5_settlement.settle_f5_from_linescore_api",
                      betSide=side, awayF5=settled.get("awayF5"),
                      homeF5=settled.get("homeF5"), isTie=settled.get("isTie"),
                      settlerNotes=settled.get("notes"))
            out.append(_propose(bet, index, MLB_EVIDENCE_BACKFILL,
                                {"result": result,
                                 "status": settled_status_for(bet)}, ev))
            continue

        teams = (linescore.get("teams") or {})
        away_runs = (teams.get("away") or {}).get("runs")
        home_runs = (teams.get("home") or {}).get("runs")
        if away_runs is None or home_runs is None:
            out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                               "linescore carries no final run totals"))
            continue

        canonical_mkt = canonical_market_name(bet.get("market", ""))
        scores = {(away, home): {"away_score": away_runs, "home_score": home_runs}}
        result, a_sc, h_sc = clv_update.determine_result(bet, scores, away, home, canonical_mkt)
        if result not in TERMINAL:
            out.append(_refuse(bet, index, MLB_EVIDENCE_BACKFILL,
                               "the production settler (clv_update.determine_result) declines "
                               "to grade market %r (canonical family %r) from a final score; "
                               "this tool does not substitute its own semantics"
                               % (bet.get("market"), canonical_mkt)))
            continue
        ev = dict(base_evidence, settler="clv_update.determine_result",
                  canonicalMarket=canonical_mkt, awayScore=a_sc, homeScore=h_sc)
        out.append(_propose(bet, index, MLB_EVIDENCE_BACKFILL,
                            {"result": result,
                             "status": settled_status_for(bet)}, ev))
    return out


# ── apply ───────────────────────────────────────────────────────────────────

def apply_plan(bets, plan):
    """Applies only WRITABLE_FIELDS, and verifies nothing else moved."""
    applied = []
    for item in plan:
        if item["decision"] != "PROPOSED":
            continue
        index = item["rowKey"]["index"]
        bet = bets[index]
        before = copy.deepcopy(bet)
        for field, delta in item["changes"].items():
            if field not in WRITABLE_FIELDS:
                raise ValueError("refusing to write non-writable field %r" % field)
            bet[field] = delta["after"]
        if set(before) != set(bet):
            raise ValueError("field set changed on row %s (added=%s removed=%s)"
                             % (item["rowKey"], sorted(set(bet) - set(before)),
                                sorted(set(before) - set(bet))))
        for field in before:
            if field in WRITABLE_FIELDS:
                continue
            if before.get(field) != bet.get(field):
                raise ValueError("field %r changed on row %s but only %s may be "
                                 "written" % (field, item["rowKey"],
                                              list(WRITABLE_FIELDS)))
        item["appliedPostStateHash"] = row_hash(bet)
        applied.append(item)
    return applied


def build_batch_id(plan):
    payload = json.dumps(
        sorted(p["postStateHash"] for p in plan if p["decision"] == "PROPOSED"))
    return "WAGER_REPAIR_" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Repair the historical wager backlog")
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--canonical", default=CANONICAL)
    ap.add_argument("--out-dir", dest="out_dir", default=OUT_DIR)
    ap.add_argument("--evidence", default=None,
                    help="MLB evidence file produced by --fetch-evidence")
    ap.add_argument("--fetch-evidence", dest="fetch_evidence", default=None,
                    help="fetch official MLB evidence to this path and exit (network)")
    ap.add_argument("--execute", action="store_true",
                    help="write the approved repairs (default is a dry run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(args.ledger) as handle:
        bets = json.load(handle)

    if args.fetch_evidence:
        return fetch_evidence(bets, args.fetch_evidence)

    canonical_rows = []
    if os.path.exists(args.canonical):
        with open(args.canonical) as handle:
            canonical_rows = [json.loads(line) for line in handle if line.strip()]

    evidence = None
    if args.evidence and os.path.exists(args.evidence):
        with open(args.evidence) as handle:
            evidence = json.load(handle)

    plan = plan_lifecycle(bets, canonical_rows)
    if evidence is not None:
        plan += plan_mlb(bets, evidence, plan)

    proposed = [p for p in plan if p["decision"] == "PROPOSED"]
    refused = [p for p in plan if p["decision"] == "REFUSED"]
    batch_id = build_batch_id(plan)

    ledger_before = hashlib.sha256(open(args.ledger, "rb").read()).hexdigest()
    if args.execute and proposed:
        apply_plan(bets, plan)
        write_json_atomic(bets, args.ledger)
    ledger_after = hashlib.sha256(open(args.ledger, "rb").read()).hexdigest()

    receipt = {
        "repairBatchId": batch_id,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "scripts/edgelab/repair_wager_backlog.py",
        "mode": "EXECUTE" if args.execute else "DRY_RUN",
        "ledger": os.path.relpath(args.ledger, ROOT_DIR),
        "ledgerHashBefore": ledger_before,
        "ledgerHashAfter": ledger_after,
        "evidenceFile": (os.path.relpath(args.evidence, ROOT_DIR)
                         if args.evidence else None),
        "evidenceGeneratedAt": (evidence or {}).get("generatedAt"),
        "writableFields": list(WRITABLE_FIELDS),
        "immutabilityRule": ("every field except %s is asserted byte-identical "
                             "per row" % list(WRITABLE_FIELDS)),
        "documentedSensitiveFields": list(DOCUMENTED_SENSITIVE_FIELDS),
        "counts": {
            "proposed": len(proposed),
            "refused": len(refused),
            "proposedBySource": dict(collections.Counter(p["source"] for p in proposed)),
            "refusedBySource": dict(collections.Counter(p["source"] for p in refused)),
        },
        "rows": plan,
    }

    if args.execute:
        os.makedirs(args.out_dir, exist_ok=True)
        write_json_atomic(receipt, os.path.join(args.out_dir, "wager_repair_receipt.json"))

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True, default=str))
    else:
        print("WAGER BACKLOG REPAIR -- %s" % receipt["mode"])
        print("=" * 78)
        print("batch: %s" % batch_id)
        print("proposed: %d   refused: %d" % (len(proposed), len(refused)))
        for source, n in sorted(receipt["counts"]["proposedBySource"].items()):
            print("  PROPOSED  %-24s %d" % (source, n))
        for source, n in sorted(receipt["counts"]["refusedBySource"].items()):
            print("  REFUSED   %-24s %d" % (source, n))
        if refused:
            print("\nrefusal reasons:")
            for reason, n in collections.Counter(
                    p["reason"][:96] for p in refused).most_common():
                print("  %4d  %s" % (n, reason))
        if args.execute:
            print("\nledger %s -> %s" % (ledger_before[:16], ledger_after[:16]))
            print("receipt: %s" % os.path.join(
                os.path.relpath(args.out_dir, ROOT_DIR), "wager_repair_receipt.json"))
        else:
            print("\nDRY RUN -- nothing was written. Pass --execute to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
