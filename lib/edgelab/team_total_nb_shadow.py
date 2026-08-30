"""
lib/edgelab/team_total_nb_shadow.py
===================================
MLB-RSCH-0035 -- TEAM_TOTAL_NB_V1 prospective shadow.

Pairs, for every archived-eligible team-total contract on a slate:

  CONTROL   production's own v1.2 Poisson conversion,
            p_over_total(teamProj, N - 1) capped at 0.95
  CANDIDATE the SAME teamProj and the SAME AT_LEAST_N semantics with the
            frozen MLB-RSCH-0010 negative-binomial body
  MARKET    Kalshi's vig-free fair probability
  EXECUTION the actual executable price, recorded for SECONDARY
            economics only

The candidate is frozen. It has no fitted coefficient, no calibration
map, no market anchoring, no threshold-specific term, and no dependence
on any outcome. Its entire definition is: production's mean, the
contract's own threshold, and dispersion 0.281513.

Deliberately mirrors lib/edgelab/shadow_distribution.py rather than
introducing a second shadow mechanism: same per-game isolation, same
record shape conventions, same "pure, never writes" contract. Structured
so scripts/edgelab/run_prospective_snapshots.py can call it exactly the
way it already calls the MLB-RSCH-0011 shadow step.
"""
import re

from lib.edgelab import ids
from lib.edgelab.backtest.run_distributions import negative_binomial_pmf
from lib.edgelab.shadow_distribution import FROZEN_DISPERSION

CANDIDATE_VERSION = "TEAM_TOTAL_NB_V1"
CONTROL_VERSION = "PRODUCTION_TEAM_TOTAL_POISSON_V1_2"

# The v1.2 production cap, reproduced so the control is production's real
# output and not an idealised version of it.
PRODUCTION_PROBABILITY_CAP = 0.95
MAX_RUNS = 40

# KXMLBTEAMTOTAL-<YY><MON><DD><HHMM><AWAY><HOME>-<TEAM><N>
TICKER_RE = re.compile(
    r"^KXMLBTEAMTOTAL-(\d{2})([A-Z]{3})(\d{2})\d{4}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})(\d+)$")

FAILURE_NO_TICKER = "NO_CANONICAL_TEAM_TOTAL_TICKER"
FAILURE_UNPARSEABLE = "TICKER_NOT_PARSEABLE"
FAILURE_NO_PROJECTION = "NO_TEAM_PROJECTION"
FAILURE_TEAM_NOT_IN_EVENT = "SUFFIX_TEAM_NOT_IN_EVENT_TICKER"


def parse_team_total_ticker(ticker):
    """(team, threshold, side) or None. Exact match only -- never fuzzy."""
    m = TICKER_RE.match(ticker or "")
    if not m:
        return None
    _yy, _mon, _dd, away, home, team, n = m.groups()
    if team == home:
        side = "HOME"
    elif team == away:
        side = "AWAY"
    else:
        return None
    return {"team": team, "threshold": int(n), "side": side, "away": away, "home": home}


def candidate_probability(team_proj, threshold, *, dispersion=FROZEN_DISPERSION):
    """P(team_runs >= threshold) under the frozen negative binomial.

    Same event as the contract settles, same mean as production, only the
    distributional body differs. Nothing here is fitted.
    """
    if team_proj is None or team_proj <= 0 or threshold is None:
        return None
    below = sum(negative_binomial_pmf(k, team_proj, dispersion) for k in range(0, int(threshold)))
    return max(0.0, min(1.0, 1.0 - below))


def control_probability(team_proj, threshold, p_over_total_fn):
    """Production's OWN v1.2 conversion, called exactly as production calls it.

    `p_over_total_fn` is injected rather than imported at module scope so
    this module never pulls the whole ledger in, and so a test can prove
    the control really is production's function.
    """
    if team_proj is None or threshold is None:
        return None
    raw = p_over_total_fn(team_proj, int(threshold) - 1)
    return min(raw, PRODUCTION_PROBABILITY_CAP)


def build_team_total_shadow_records(team_total_rows, *, run_id, experiment_id, evidence_level,
                                    p_over_total_fn, now=None):
    """One record per eligible team-total contract.

    `team_total_rows` is an iterable of dicts carrying at least the
    production row's ticker, threshold, team projection and market/price
    fields -- see extract_team_total_rows below.

    Every row is isolated: a bad or missing input produces a single
    FAILED_ISOLATED record with an explicit failureReason and NO
    probability fields, never a fabricated fallback and never an abort.

    Pure. Writes nothing. Never calls any production recommendation,
    edge, staking or fee function.
    """
    now = now or ids.utc_now_iso()
    records, failures = [], []

    for row in team_total_rows:
        ticker = row.get("marketTicker") or row.get("ticker")
        game_id = row.get("gameId")
        checkpoint = row.get("checkpoint")
        base = {
            "shadowEvaluationId": ids.build_model_evaluation_id(
                f"{run_id}:{experiment_id}", f"{game_id}:{checkpoint}:{ticker}"),
            "experimentId": experiment_id,
            "evidenceLevel": evidence_level,
            "runId": run_id,
            "gameId": game_id,
            "checkpoint": checkpoint,
            "marketTicker": ticker,
            "marketFamily": "KXMLBTEAMTOTAL",
            "candidateVersion": CANDIDATE_VERSION,
            "controlVersion": CONTROL_VERSION,
            "frozenDispersion": FROZEN_DISPERSION,
            "capturedAt": now,
        }
        try:
            if not ticker:
                raise ValueError(FAILURE_NO_TICKER)
            parsed = parse_team_total_ticker(ticker)
            if parsed is None:
                raise ValueError(
                    FAILURE_TEAM_NOT_IN_EVENT if ticker.startswith("KXMLBTEAMTOTAL-")
                    else FAILURE_UNPARSEABLE)
            team_proj = row.get("teamProj")
            if team_proj is None:
                raise ValueError(FAILURE_NO_PROJECTION)

            control = control_probability(team_proj, parsed["threshold"], p_over_total_fn)
            candidate = candidate_probability(team_proj, parsed["threshold"])
            records.append(dict(base, **{
                "computationStatus": "COMPUTED",
                "team": parsed["team"],
                "side": parsed["side"],
                "threshold": parsed["threshold"],
                "contractEvent": "AT_LEAST_%d" % parsed["threshold"],
                "period": "FULL_GAME",
                "teamProj": team_proj,
                "controlProbability": control,
                "candidateProbability": candidate,
                "candidateMinusControl": (None if (control is None or candidate is None)
                                          else round(candidate - control, 6)),
                # Market and execution are RECORDED, never used as inputs.
                "marketVigFreeProbability": row.get("marketVigFreeProbability"),
                "executablePrice": row.get("executablePrice"),
                "productionStatus": row.get("productionStatus"),
                "productionConfidence": row.get("productionConfidence"),
            }))
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            records.append(dict(base, computationStatus="FAILED_ISOLATED", failureReason=reason))
            failures.append({"gameId": game_id, "checkpoint": checkpoint,
                             "marketTicker": ticker, "reason": reason})
    return records, failures


def extract_team_total_rows(evaluated_snapshots, *, evaluate_game_fn):
    """Pull the team-total contracts out of a prospective-snapshot cycle.

    Uses production's OWN evaluate_game against the SAME game objects the
    core cycle just evaluated, so the control probability recorded here
    is production's actual output rather than a reconstruction.

    Isolated per game: one game that raises contributes nothing and never
    aborts the rest.
    """
    rows = []
    for entry in evaluated_snapshots or []:
        game_id, checkpoint, game = entry.get("gameId"), entry.get("checkpoint"), entry.get("game")
        try:
            produced = evaluate_game_fn(game)
        except Exception:
            continue
        if not isinstance(produced, list):
            produced = [produced]
        for r in produced:
            if not isinstance(r, dict):
                continue
            if r.get("market") not in ("TT_Away_Over", "TT_Home_Over"):
                continue
            side = "AWAY" if r["market"] == "TT_Away_Over" else "HOME"
            rows.append({
                "gameId": game_id,
                "checkpoint": checkpoint,
                "marketTicker": r.get("ticker") or r.get("marketTicker"),
                "teamProj": r.get("awayProjRuns") if side == "AWAY" else r.get("homeProjRuns"),
                "marketVigFreeProbability": r.get("kalshiVF"),
                "executablePrice": r.get("executablePriceUsed"),
                "productionStatus": r.get("status"),
                "productionConfidence": r.get("confidence"),
            })
    return rows
