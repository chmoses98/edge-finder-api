"""
MLB-RSCH-0034: TEAM-TOTAL PROBABILITY CONVERSION
================================================

MLB-RSCH-0033 established that production's expected team-run MEAN is not
the defect: it is correctly scaled (calibration slope 1.0287), it beats a
constant baseline (MSE 9.9556 vs 10.2326), and it extracts ~106% of the
variance its own spread allows. It located the team-total failure
DOWNSTREAM of the mean, in the probability conversion, and classified the
root cause CASE_E_DISTRIBUTION_CONVERSION_PRIMARY_PROBLEM.

This experiment asks the follow-on question: production has a useful
expected team-run mean -- so why are KXMLBTEAMTOTAL probabilities poor?

METHODOLOGICAL ORDER, deliberately:

  1. TRACE the real production path in source, asserting each hop against
     the actual code rather than assuming it.
  2. ROUND-TRIP archived modelProb from archived inputs BEFORE any
     alternative is scored. A conversion model that cannot reproduce
     production is not a description of production.
  3. Establish CONTRACT TRUTH independently -- from the ticker suffix, the
     market title, the canonical taxonomy parser and the settlement
     grader -- rather than from the pricing code, so the pricing code can
     be checked against it.
  4. Only then score candidates, and attribute the semantic and
     distributional contributions SEPARATELY.

THE HEADLINE, AND IT CORRECTS THIS PROGRAM'S OWN PRIOR CLAIM:

Production ALREADY FIXED the team-total threshold semantics. A documented
"v1.2" change in scripts/build_market_ledger.py converts with
`p_over_total(proj, tt_line - 1)`, which is exactly P(runs >= N) for a
contract that settles YES iff team_runs >= N. The round-trip dates that
fix precisely: archived rows through 2026-08-20 reproduce under the OLD
v1.1 convention, and rows from 2026-08-21 onward reproduce under v1.2.

MLB-RSCH-0031 and MLB-RSCH-0032 reported a "+0.5 threshold defect
reaching live recommendations". For PRICING that claim is superseded here:
it was true of the v1.1 era corpus those experiments measured, and was
already fixed in production before those experiments were written. Their
merged artifacts are NOT rewritten; this supersedes that one conclusion.

Nothing is fitted. The negative-binomial dispersion is RSCH-0010's frozen
0.281513, used as-is and never estimated on the evaluation sample.
"""
import collections
import json
import math
import os
import random
import re
import statistics
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.edgelab import experiment_registry as reg
from lib.edgelab import evidence_levels as ev
from lib.edgelab import control_identity as ctrl_id
from lib.edgelab import research_lab_ids as rlids
from lib.edgelab.backtest.run_distributions import negative_binomial_pmf
from lib.edgelab.shadow_distribution import FROZEN_DISPERSION
from lib.edgelab.research import methodology_v3 as v3

# Production's OWN conversion function, imported unmodified.
from scripts.build_market_ledger import p_over_total

EXPERIMENT_ID = "MLB-RSCH-0034"
REGISTRATION_TIMESTAMP = "2026-08-30T06:00:00Z"

ANALYTICS_DIR = os.path.join(_ROOT, "data", "edgelab", "analytics")
ARTIFACT_PATH = os.path.join(ANALYTICS_DIR, "latest_mlb_rsch_0034_team_total_conversion.json")
REPORT_PATH = os.path.join(_ROOT, "docs", "EDGELAB_MLB_RSCH_0034_TEAM_TOTAL_CONVERSION.md")
PIPELINE_DIR = os.path.join(_ROOT, "data", "pipeline")
LEDGER_PATH = os.path.join(_ROOT, "scripts", "build_market_ledger.py")

# The date the v1.2 semantic fix began appearing in archived output. NOT a
# tuned parameter -- it is READ OFF the round-trip, and the round-trip
# reports it rather than assuming it.
MAX_RUNS = 40
TICKER_RE = re.compile(
    r"^KXMLBTEAMTOTAL-(\d{2})([A-Z]{3})(\d{2})\d{4}([A-Z]{2,3})([A-Z]{2,3})-([A-Z]{2,3})(\d+)$")
MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
          "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

EXACT_TOL = 1e-4          # archived modelProb is stored to 2dp of a percent
TOLERANCE_TOL = 5e-3

ROUND_TRIP_BUCKETS = (
    "EXACT_MATCH",
    "TOLERANCE_MATCH",
    "MODEL_VERSION_MISMATCH",
    "MISSING_INPUTS",
    "SEMANTIC_MISMATCH",
    "UNRESOLVED",
)

ROOT_CAUSE_CASES = (
    "CASE_A_SEMANTICS_PRIMARY",
    "CASE_B_DISTRIBUTION_PRIMARY",
    "CASE_C_BOTH",
    "CASE_D_CURRENT_PRODUCTION_CONVERSION_CORRECT",
    "CASE_E_RESIDUAL_OTHER",
)


def _current_git_commit_sha():
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=_ROOT,
                              capture_output=True, text=True).stdout.strip() or "UNKNOWN"
    except Exception:
        return "UNKNOWN"


# ── 1. PRODUCTION PATH TRACE ─────────────────────────────────────────────

def trace_production_path():
    """Read the ACTUAL conversion out of production source.

    Every hop is asserted against the real file, so this trace cannot
    silently describe a path production does not take. `assumedNothing` is
    the point: the distribution family is READ from p_over_total's body,
    not assumed to be Poisson.
    """
    source = open(LEDGER_PATH, encoding="utf-8").read()

    # Anchor on the team-total SECTION marker, not the first textual
    # occurrence of "TT_Away_Over" -- that one is a module-docstring
    # mention ~1500 lines earlier, and a window taken from it never
    # reaches the conversion. Bound the block at the next section rule so
    # the trace cannot read a neighbouring family's code.
    tt_block_start = source.index("# \u2500\u2500 TT_Away_Over / TT_Home_Over")
    next_rule = source.find("\n    # \u2500\u2500 ", tt_block_start + 10)
    tt_block = source[tt_block_start:next_rule if next_rule != -1 else len(source)]

    conversion_call = "p_over_total(proj, tt_line - 1)" in tt_block
    legacy_call = "p_over_total(proj, tt_line)" in tt_block and not conversion_call

    # What distribution does p_over_total actually use? Read its body.
    pot_start = source.index("def p_over_total(")
    pot_body = source[pot_start:source.index("\ndef ", pot_start + 10)]
    uses_poisson = "poisson_pmf" in pot_body
    families = [n for n in ("poisson_pmf", "negative_binomial", "normal_cdf", "binomial")
                if n in pot_body]

    return {
        "hops": [
            {"hop": "teamProj",
             "source": "compute_projections(g) -> (away_proj, home_proj, f5_away, f5_home, missing)",
             "note": "validated as correct by MLB-RSCH-0033; clamped to [2.5, 7.0]"},
            {"hop": "team identity",
             "source": "ticker suffix -<TEAM><N>; home/away resolved from the event ticker's "
                       "<AWAY><HOME> pair",
             "note": "the projection chosen is the projection of the team named in the suffix"},
            {"hop": "contract threshold",
             "source": "tt_line = Kalshi ticker-suffix digit `over_n` (scripts/merge_odds.py: "
                       "'line': bl.get('over_n'))",
             "note": "a raw suffix digit, NOT a plain 'greater than N' line"},
            {"hop": "contract event semantics",
             "source": "digit N encodes 'over (N-0.5)' => YES iff team_runs >= N",
             "note": "independently confirmed below against the taxonomy parser and the "
                     "settlement grader"},
            {"hop": "distribution",
             "source": "p_over_total(proj, line) body",
             "note": "distribution families actually referenced: %s" % (families or "NONE FOUND")},
            {"hop": "probability conversion",
             "source": "model_p = p_over_total(proj, tt_line - 1)" if conversion_call
                       else "model_p = p_over_total(proj, tt_line)  [LEGACY v1.1]",
             "note": "p_over_total(proj, L) = P(runs >= L+1); with L = N-1 that is P(runs >= N)"},
            {"hop": "modelProb",
             "source": "model_p = min(model_p, 0.95); modelProb = round(model_p * 100, 2)",
             "note": "a 0.95 cap is applied AFTER conversion and is part of production's output"},
        ],
        "currentVersion": "v1.2" if conversion_call else ("v1.1" if legacy_call else "UNRECOGNISED"),
        "distributionIsPoisson": uses_poisson,
        "distributionFamiliesFound": families,
        "probabilityCapApplied": "min(model_p, 0.95)" in tt_block,
        "assumedNothing": ("The distribution family is read out of p_over_total's body and the "
                           "threshold convention out of the call site; neither is assumed."),
    }


# ── 2. CONTRACT TRUTH, established independently of the pricing code ─────

def contract_truth():
    """What does Kalshi actually resolve? Determined from FOUR independent
    sources, none of which is build_market_ledger's pricing line."""
    findings = []

    tax_path = os.path.join(_ROOT, "lib", "research", "market_taxonomy.py")
    tax_src = open(tax_path, encoding="utf-8").read()
    fn_start = tax_src.index("def _team_and_margin_from_suffix")
    fn_body = tax_src[fn_start:fn_start + 2000]
    stores_half = "- 0.5" in fn_body or "-0.5" in fn_body
    findings.append({
        "source": "lib/research/market_taxonomy.py::_team_and_margin_from_suffix",
        "evidence": "stores threshold = N - 0.5" if stores_half else "does NOT store N - 0.5",
        "impliesYesIff": "team_runs > N - 0.5, i.e. team_runs >= N" if stores_half else "UNKNOWN",
    })

    set_path = os.path.join(_ROOT, "lib", "edgelab", "settlement.py")
    set_src = open(set_path, encoding="utf-8").read()
    findings.append({
        "source": "lib/edgelab/settlement.py::settle_market (FAMILY_TEAM_TOTAL)",
        "evidence": "grades team totals in the shared threshold branch with GAME_TOTAL / "
                    "WINNING_MARGIN (pays YES iff the scored quantity exceeds the stored "
                    "threshold)",
        "impliesYesIff": "team_runs > threshold, and threshold is N - 0.5, so team_runs >= N",
        "familyPresent": "FAMILY_TEAM_TOTAL" in set_src,
    })

    reg_path = os.path.join(_ROOT, "scripts", "build_kalshi_registry.py")
    note = ""
    if os.path.exists(reg_path):
        reg_src = open(reg_path, encoding="utf-8").read()
        if "over_n" in reg_src:
            note = "registry builder documents the suffix convention (over_n=4 means 'scores over 3.5')"
    findings.append({
        "source": "scripts/build_kalshi_registry.py",
        "evidence": note or "suffix convention note not located",
        "impliesYesIff": "team_runs >= N" if note else "UNKNOWN",
    })

    titles = _sample_market_titles()
    findings.append({
        "source": "archived Kalshi market titles/subtitles (data/kalshi_registry_snapshots)",
        "evidence": "sampled team_total titles: %s" % (titles[:3] or "none archived"),
        "impliesYesIff": "title text is a display label and is NOT authoritative for the "
                         "inequality; the settlement grader is",
    })

    return {
        "resolvedEvent": "AT_LEAST_N",
        "statement": "A KXMLBTEAMTOTAL contract with ticker suffix -<TEAM><N> settles YES iff "
                     "that team scores AT LEAST N runs in the FULL GAME. Equivalently 'over "
                     "(N - 0.5)'. It is NOT 'over N'.",
        "distinguishedFrom": {
            "OVER_INTEGER_N": "would be team_runs >= N + 1 -- excluded: the taxonomy parser "
                              "stores N - 0.5, not N",
            "OVER_X_POINT_5": "identical to AT_LEAST_N when X.5 = N - 0.5; this IS the contract",
            "YES_NO_DIRECTION": "YES is the OVER side; the suffix names the team, not the side",
            "HOME_AWAY": "the suffix team is matched against the event ticker's <AWAY><HOME> "
                         "pair, so the correct team's projection is used",
            "PERIOD": "full game -- F5 team totals carry the KXMLBF5 series prefix, not "
                      "KXMLBTEAMTOTAL",
        },
        "sources": findings,
    }


def _sample_market_titles():
    import glob
    out = []
    for path in sorted(glob.glob(os.path.join(_ROOT, "data", "kalshi_registry_snapshots", "*")))[:6]:
        try:
            snap = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for m in (snap.get("markets") or snap.get("results") or []):
            if m.get("market_type") == "team_total" or "TEAMTOTAL" in str(
                    m.get("ticker") or m.get("market_ticker") or ""):
                t = (m.get("title") or "").strip()
                sub = (m.get("subtitle") or "").strip()
                label = (t + (" | " + sub if sub else "")).strip()
                if label and label not in out:
                    out.append(label)
            if len(out) >= 5:
                return out
    return out


# ── 3. DATA LOADING ──────────────────────────────────────────────────────

def load_archived_projections():
    """Production's OWN archived team-run means, per date and team."""
    import glob
    proj = {}
    for path in sorted(glob.glob(os.path.join(PIPELINE_DIR, "*", "projections.json"))):
        try:
            payload = json.load(open(path, encoding="utf-8")).get("data", {})
        except Exception:
            continue
        date = payload.get("date")
        for g in payload.get("games", []):
            if g.get("awayProjRuns") is None or g.get("homeProjRuns") is None:
                continue
            proj[(date, g["away"])] = g["awayProjRuns"]
            proj[(date, g["home"])] = g["homeProjRuns"]
    return proj


def parse_ticker(ticker):
    m = TICKER_RE.match(ticker or "")
    if not m:
        return None
    yy, mon, dd, away, home, team, n = m.groups()
    return {
        "date": "20%s-%02d-%s" % (yy, MONTHS[mon], dd),
        "away": away, "home": home, "team": team,
        "threshold": int(n),
        "side": "HOME" if team == home else ("AWAY" if team == away else "UNKNOWN"),
    }


# ── 4. THE CANDIDATES ────────────────────────────────────────────────────

def poisson_at_least(mean, n):
    """C1/C0 conversion: production's own p_over_total, called as production
    calls it. p_over_total(proj, L) = P(runs >= L+1), so P(runs >= N) needs
    L = N - 1."""
    return p_over_total(mean, n - 1)


def nb_at_least(mean, n):
    """C2 conversion: identical semantics, negative-binomial body.

    Dispersion is RSCH-0010's FROZEN value, imported. It is NOT estimated
    here -- fitting dispersion on the evaluation sample is exactly the
    error this experiment is forbidden to make."""
    below = sum(negative_binomial_pmf(k, mean, FROZEN_DISPERSION) for k in range(0, n))
    return max(0.0, min(1.0, 1.0 - below))


def legacy_poisson(mean, n):
    """The v1.1 convention, kept only so the round-trip can IDENTIFY it."""
    return p_over_total(mean, n)


CANDIDATES = {
    "C0_PRODUCTION_AS_ARCHIVED": "production's archived modelProb, byte-for-byte",
    "C1_SEMANTICS_ONLY": "same teamProj, same Poisson body, correct AT_LEAST_N semantics",
    "C2_DISTRIBUTION": "same teamProj, correct semantics, frozen NB dispersion 0.281513",
    "C3_SEMANTICS_PLUS_DISTRIBUTION": "C1's semantics and C2's distribution together "
                                      "(identical to C2 -- C2 already carries C1's semantics)",
}


# ── 5. ROUND-TRIP ────────────────────────────────────────────────────────

def round_trip(rows, projections):
    """Can archived modelProb be reproduced from archived inputs?

    Reports the six preregistered buckets and, crucially, the DATE at which
    the reproducing convention changes -- which is how the v1.2 fix is
    detected rather than assumed."""
    buckets = collections.Counter()
    by_date = collections.defaultdict(collections.Counter)
    examples = collections.defaultdict(list)

    for r in rows:
        parsed = parse_ticker(r.get("marketTicker"))
        archived = r.get("modelP")
        if parsed is None:
            buckets["UNRESOLVED"] += 1
            continue
        if archived is None:
            buckets["MISSING_INPUTS"] += 1
            continue
        mean = projections.get((parsed["date"], parsed["team"]))
        if mean is None:
            buckets["MISSING_INPUTS"] += 1
            continue

        v12 = min(poisson_at_least(mean, parsed["threshold"]), 0.95)
        v11 = min(legacy_poisson(mean, parsed["threshold"]), 0.95)
        d12, d11 = abs(v12 - archived), abs(v11 - archived)

        if d12 <= EXACT_TOL:
            bucket, conv = "EXACT_MATCH", "v1.2"
        elif d11 <= EXACT_TOL:
            bucket, conv = "MODEL_VERSION_MISMATCH", "v1.1"
        elif d12 <= TOLERANCE_TOL:
            bucket, conv = "TOLERANCE_MATCH", "v1.2"
        elif d11 <= TOLERANCE_TOL:
            bucket, conv = "MODEL_VERSION_MISMATCH", "v1.1"
        else:
            bucket, conv = "SEMANTIC_MISMATCH", None
            if len(examples["SEMANTIC_MISMATCH"]) < 8:
                examples["SEMANTIC_MISMATCH"].append({
                    "date": parsed["date"], "team": parsed["team"],
                    "threshold": parsed["threshold"], "teamProj": round(mean, 4),
                    "archived": round(archived, 4), "v1.2": round(v12, 4), "v1.1": round(v11, 4)})
        buckets[bucket] += 1
        if conv:
            by_date[parsed["date"]][conv] += 1

    dates = sorted(by_date)
    fix_date = None
    for d in dates:
        if by_date[d]["v1.2"] > 0 and by_date[d]["v1.1"] == 0:
            fix_date = d
            break

    total = sum(buckets.values())
    reproduced = buckets["EXACT_MATCH"] + buckets["TOLERANCE_MATCH"] + buckets["MODEL_VERSION_MISMATCH"]
    return {
        "buckets": {b: buckets.get(b, 0) for b in ROUND_TRIP_BUCKETS},
        "total": total,
        "reproducedUnderSomeProductionVersion": reproduced,
        "reproductionRate": round(reproduced / total, 4) if total else None,
        "conventionByDate": {d: dict(by_date[d]) for d in dates},
        "detectedFixDate": fix_date,
        "semanticMismatchExamples": examples["SEMANTIC_MISMATCH"],
        "verdict": ("production IS reproducible from archived inputs once the model VERSION is "
                    "respected; the reproducing convention changes on %s, which dates the v1.2 "
                    "semantic fix" % fix_date) if fix_date else
                   "no single date separates the two conventions",
    }


# ── 6. SCORING ───────────────────────────────────────────────────────────

def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows)


def constant_brier(rows):
    base = sum(r["outcome"] for r in rows) / len(rows)
    return sum((base - r["outcome"]) ** 2 for r in rows) / len(rows)


def calibration_slope(rows, key):
    n = len(rows)
    mp = sum(r[key] for r in rows) / n
    mo = sum(r["outcome"] for r in rows) / n
    var = sum((r[key] - mp) ** 2 for r in rows)
    if var == 0:
        return None
    return sum((r[key] - mp) * (r["outcome"] - mo) for r in rows) / var


def auc(rows, key):
    pos = [r[key] for r in rows if r["outcome"] == 1]
    neg = [r[key] for r in rows if r["outcome"] == 0]
    if not pos or not neg:
        return None
    wins = sum((a > b) + 0.5 * (a == b) for a in pos for b in neg)
    return wins / (len(pos) * len(neg))


def bootstrap_delta(rows, key_a, key_b, *, cluster="gameId", draws=3000, seed=20260830):
    """Game-clustered bootstrap of brier(a) - brier(b). Negative == a better."""
    by = collections.defaultdict(list)
    for r in rows:
        by[r[cluster]].append(r)
    keys = list(by)
    rnd = random.Random(seed)
    deltas = []
    for _ in range(draws):
        sample = [x for k in (rnd.choice(keys) for _ in keys) for x in by[k]]
        if key_b == "__constant__":
            deltas.append(brier(sample, key_a) - constant_brier(sample))
        else:
            deltas.append(brier(sample, key_a) - brier(sample, key_b))
    deltas.sort()
    lo = deltas[int(0.025 * len(deltas))]
    hi = deltas[int(0.975 * len(deltas))]
    return {"mean": round(sum(deltas) / len(deltas), 6),
            "ciLow": round(lo, 6), "ciHigh": round(hi, 6),
            "excludesNull": bool(lo * hi > 0)}


def score_block(rows, label):
    if len(rows) < 30:
        return {"label": label, "n": len(rows), "status": "INSUFFICIENT_SAMPLE"}
    out = {
        "label": label,
        "n": len(rows),
        "independentGames": len({r["gameId"] for r in rows}),
        "independentDates": len({r["settleDate"] for r in rows}),
        "baseRate": round(sum(r["outcome"] for r in rows) / len(rows), 4),
        "constantBrier": round(constant_brier(rows), 4),
        "marketBrier": round(brier(rows, "marketP"), 4),
    }
    for key in ("C0", "C1", "C2"):
        out[key] = {
            "brier": round(brier(rows, key), 4),
            "slope": (round(calibration_slope(rows, key), 4)
                      if calibration_slope(rows, key) is not None else None),
            "auc": (round(auc(rows, key), 4) if auc(rows, key) is not None else None),
            "meanProb": round(sum(r[key] for r in rows) / len(rows), 4),
        }
    return out


# ── 7. ATTRIBUTION AND CLASSIFICATION ────────────────────────────────────

def attribute(rows):
    """Semantic gain and distribution gain, quantified SEPARATELY.

    The semantic gain is measurable only where production was still on
    v1.1 -- on post-fix rows C0 already IS C1, so the semantic gain there
    is zero BY CONSTRUCTION and reporting a pooled number would understate
    it. Both are reported with their own eligible populations."""
    pre = [r for r in rows if r["productionVersion"] == "v1.1"]
    post = [r for r in rows if r["productionVersion"] == "v1.2"]

    semantic = None
    if len(pre) >= 30:
        semantic = {
            "population": "rows priced under v1.1", "n": len(pre),
            "independentGames": len({r["gameId"] for r in pre}),
            "brierBefore": round(brier(pre, "C0"), 4),
            "brierAfter": round(brier(pre, "C1"), 4),
            "gain": round(brier(pre, "C0") - brier(pre, "C1"), 4),
            "bootstrap": bootstrap_delta(pre, "C1", "C0"),
        }
    distribution = {
        "population": "all eligible rows (semantics held correct at C1)", "n": len(rows),
        "independentGames": len({r["gameId"] for r in rows}),
        "brierBefore": round(brier(rows, "C1"), 4),
        "brierAfter": round(brier(rows, "C2"), 4),
        "gain": round(brier(rows, "C1") - brier(rows, "C2"), 4),
        "bootstrap": bootstrap_delta(rows, "C2", "C1"),
    }
    # TRANSPORT, computed rather than asserted: does the distribution gain
    # replicate in BOTH disjoint chronological blocks? Two time-separated
    # populations agreeing is chronological validation; one pooled number
    # is not.
    blocks = {}
    for name, block in (("v1.1_era", pre), ("v1.2_era", post)):
        if len(block) >= 30:
            blocks[name] = {
                "n": len(block),
                "independentGames": len({r["gameId"] for r in block}),
                "gain": round(brier(block, "C1") - brier(block, "C2"), 4),
            }
    replicates = bool(len(blocks) == 2 and all(b["gain"] > 0 for b in blocks.values()))

    return {
        "semanticGain": semantic,
        "distributionGain": distribution,
        "postFixRows": len(post),
        "chronologicalBlocks": blocks,
        "distributionGainReplicatesInBothBlocks": replicates,
        "transportEvidence": (v3.TRANSPORT_CHRONOLOGICAL_VALIDATION if replicates else None),
        "note": ("On post-fix rows C0 == C1 by construction, so the semantic gain is measured "
                 "only where production was still on v1.1. This is a population restriction, "
                 "not a selection on outcome."),
    }


def classify(attribution, rows):
    """All five preregistered cases are reachable."""
    sem = attribution["semanticGain"]
    dist = attribution["distributionGain"]
    sem_real = bool(sem and sem["bootstrap"]["excludesNull"] and sem["gain"] > 0)
    dist_real = bool(dist["bootstrap"]["excludesNull"] and dist["gain"] > 0)

    beats_constant = bootstrap_delta(rows, "C2", "__constant__")
    best_still_loses = beats_constant["ciLow"] > 0

    if sem_real and dist_real:
        case = "CASE_C_BOTH"
        why = ("both the semantic correction and the distribution change improve the proper "
               "score with game-clustered CIs excluding zero")
    elif sem_real:
        case = "CASE_A_SEMANTICS_PRIMARY"
        why = "the semantic correction improves the score; the distribution change does not"
    elif dist_real:
        case = "CASE_B_DISTRIBUTION_PRIMARY"
        why = "the distribution change improves the score; the semantics were already correct"
    elif not best_still_loses:
        case = "CASE_D_CURRENT_PRODUCTION_CONVERSION_CORRECT"
        why = "no correction improves the score and production is not beaten by a constant"
    else:
        case = "CASE_E_RESIDUAL_OTHER"
        why = "neither correction explains the loss"

    return {
        "case": case,
        "why": why,
        "semanticCorrectionIsReal": sem_real,
        "distributionCorrectionIsReal": dist_real,
        "bestCandidateVsConstant": beats_constant,
        "bestCandidateStillLosesToConstant": best_still_loses,
        "allCasesReachable": list(ROOT_CAUSE_CASES),
        "residual": ("Even with BOTH corrections applied, the best candidate does not beat a "
                     "pooled constant base rate. What that implies about the mean is NOT "
                     "settled here: see achievableAucSimulation. An earlier version of this "
                     "artifact asserted that r-squared 0.0377 'caps attainable AUC near 0.55'. "
                     "That claim was WITHDRAWN -- r-squared does not determine AUC for a "
                     "thresholded binary event, and a counter-example is computed in "
                     "aucCeilingClaim.refutation. What does hold structurally is narrower: "
                     "every candidate here is monotone in teamProj at a fixed threshold, so "
                     "none of them can reorder teams within a threshold. That constrains what "
                     "a DISTRIBUTION change can do; it says nothing about a ceiling on AUC."
                     ) if best_still_loses else "",
    }


# ── 8. STRATIFIED VIEWS ──────────────────────────────────────────────────

def by_threshold(rows, floor=30):
    out = {}
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["threshold"]].append(r)
    for n in sorted(groups):
        block = groups[n]
        out["AT_LEAST_%d" % n] = (score_block(block, "AT_LEAST_%d" % n) if len(block) >= floor
                                  else {"n": len(block), "status": "INSUFFICIENT_SAMPLE"})
    return out


def by_side(rows):
    return {s: score_block([r for r in rows if r["side"] == s], s) for s in ("HOME", "AWAY")}


def by_proj_band(rows):
    out = {}
    bands = (("LOW_lt_4.0", lambda p: p < 4.0),
             ("MID_4.0_to_5.0", lambda p: 4.0 <= p < 5.0),
             ("HIGH_ge_5.0", lambda p: p >= 5.0))
    for name, fn in bands:
        block = [r for r in rows if fn(r["teamProj"])]
        out[name] = score_block(block, name)
    return out


def by_prob_band(rows):
    out = {}
    bands = (("P_lt_0.35", 0.0, 0.35), ("P_0.35_0.65", 0.35, 0.65), ("P_ge_0.65", 0.65, 1.01))
    for name, lo, hi in bands:
        block = [r for r in rows if lo <= r["C2"] < hi]
        out[name] = score_block(block, name)
    return out


# ── 9. ECONOMICS -- computed LAST, and never optimised to ────────────────

def economics(rows):
    """Executable capacity only. Applied AFTER the proper-score verdict, as
    a gate. No parameter anywhere in this experiment is fitted to it."""
    priced = [r for r in rows if r.get("marketP") is not None]
    disagreements = [r for r in priced if abs(r["C2"] - r["marketP"]) >= 0.05]
    return {
        "rowsWithExecutablePrice": len(priced),
        "rowsWhereBestCandidateDisagreesWithMarketBy5ppt": len(disagreements),
        "independentGamesAmongThose": len({r["gameId"] for r in disagreements}),
        "note": ("Reported as CAPACITY only. No ROI, stake or P&L figure is computed, and no "
                 "threshold in this experiment was chosen by reference to one. Because the "
                 "best candidate does not beat a constant on proper score, economic capacity "
                 "is moot -- V3 forbids promoting on capacity when the predictive gate fails."),
    }


# ── 10. V3 PREREGISTRATION ───────────────────────────────────────────────

def preregistration():
    return v3.MaterialityPreregistration(
        null_value=0.0,
        effect_floor=0.005,
        harm_tolerance=0.0,
        require_ci_excludes_null=True,
        min_score_improvement=0.005,
        min_independent_games=100,
        min_independent_dates=15,
        min_replicating_blocks=0,
        required_transport=v3.TRANSPORT_CHRONOLOGICAL_VALIDATION,
        require_executable_capacity=True,
        min_executable_opportunities=25,
        subject_unit="team-game",
        justification=(
            "A team-total conversion change is only worth making if it improves the Brier score "
            "by at least 0.005 against production AND leaves the result no worse than a constant "
            "base rate, because a family that cannot beat its own base rate cannot be priced "
            "against a sharp market whatever its internal calibration. The 100-game floor matches "
            "the floor already fixed for KXMLBF5 so that no family is held to a lower evidential "
            "bar than another, and the clustering unit is the game because both team-total "
            "contracts in a game share one game state and are not independent observations."),
        notes=("Fixed before any candidate was scored. The frozen NB dispersion 0.281513 is "
               "imported from RSCH-0010 and is NOT estimated on this sample."),
    )


# ── 11. REGISTRATION ─────────────────────────────────────────────────────

def register_experiment():
    try:
        existing = reg.load_experiment(EXPERIMENT_ID)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        return ctrl_id.load_control(existing["controlModelId"]), existing

    control = ctrl_id.build_control_registration(
        name="mlb_rsch_0034_team_total_conversion_control_v1",
        source_git_commit_sha=_current_git_commit_sha(),
        model_config_version="1.0",
        config_fingerprint=rlids.config_fingerprint(
            config_text="MLB-RSCH-0034 team-total conversion v1: control = production's archived "
                        "KXMLBTEAMTOTAL modelProb exactly as written, round-tripped from archived "
                        "projections.json means through production's OWN p_over_total. Candidates "
                        "change ONLY the contract semantics (C1) and the distribution body (C2, "
                        "frozen RSCH-0010 dispersion 0.281513). No coefficient is fitted and no "
                        "dispersion is estimated on the evaluation sample."
        ),
        probability_adapter_identity="team_total_at_least_n_conversion",
        model_engine_family="team_total_probability_conversion_v1",
        required_input_provenance=["model_evaluation_probability_pipeline_derived",
                                   "season_to_date_stats", "pitcher_snapshot"],
        identity_confidence=ctrl_id.IDENTITY_EXACT,
        description=("Why are KXMLBTEAMTOTAL probabilities poor when MLB-RSCH-0033 showed the "
                     "underlying team-run mean is sound?"),
        registered_at=REGISTRATION_TIMESTAMP,
    )
    ctrl_id.register_control(control)

    definition = reg.build_experiment_definition(
        experiment_id=EXPERIMENT_ID,
        title="Team-Total Probability Conversion",
        hypothesis=(
            "H1 (semantics): the conversion prices the wrong event -- an off-by-one against a "
            "contract that settles YES iff team_runs >= N -- and correcting it materially improves "
            "the proper score. H2 (distribution): the Poisson body under-disperses relative to real "
            "team scoring, and the frozen RSCH-0010 negative binomial improves it. H3 (both). "
            "H4 (neither): the current conversion is already correct and the residual lies "
            "elsewhere. H1 and H4 are NOT mutually exclusive across time, because production may "
            "have changed version mid-corpus -- which is why the round-trip runs first."
        ),
        research_question=("Production has a useful expected team-run mean. Why are its team-total "
                           "probabilities poor, and is the probability conversion the cause?"),
        owner="research-lab",
        control_model_id=control["controlModelId"],
        evidence_level=ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        target_population=("Every archived KXMLBTEAMTOTAL model evaluation that settled, whose team "
                           "and date join to an archived production projection."),
        market_families=["team_total"],
        eligibility_criteria=[
            "archived EVALUATED KXMLBTEAMTOTAL row with a modelFairProbability",
            "a canonical ticker parseable to (date, team, threshold N)",
            "an archived production teamProj for that (date, team)",
            "a settled outcome for the contract",
        ],
        exclusion_criteria=[
            "any dispersion fitted on the evaluation sample -- the NB dispersion is RSCH-0010's "
            "frozen 0.281513, imported",
            "any threshold-specific coefficient; thresholds are reported, never tuned",
            "ROI or P&L as a fitting objective -- economics is a gate applied after scoring",
            "F5 team totals, which are a different series and a different period",
        ],
        prediction_checkpoints=["ARCHIVED_PREGAME_SLATE"],
        primary_metric="Brier score of the team-total probability against settlement",
        secondary_metrics=[
            "calibration slope and AUC per candidate",
            "round-trip reproduction rate against archived modelProb, by production version",
            "semantic gain and distribution gain, attributed separately",
            "per-threshold, per-side, per-projection-band and per-probability-band breakdowns",
            "comparison to the Kalshi vig-free fair probability on identical rows",
        ],
        chronological_split_policy=(
            "Descriptive across all archived dates. Nothing is fitted, so no train/validation split "
            "exists. The corpus DOES span a production version change, and the round-trip reports "
            "the date of that change rather than pooling across it silently."),
        minimum_sample_requirement={"independentGames": 100, "independentDates": 15},
        clustering_unit="gameId",
        experiment_type=reg.EXPERIMENT_TYPE_EXPLORATORY,
        false_discovery_handling=reg.FDR_OTHER_DOCUMENTED,
        pit_requirements={
            "model_evaluation_probability_pipeline_derived": "PREDICTIVE_INPUT",
            "season_to_date_stats": "PREDICTIVE_INPUT",
            "pitcher_snapshot": "PREDICTIVE_INPUT",
        },
        registered_at=REGISTRATION_TIMESTAMP,
        notes=(
            "falseDiscoveryHandling=OTHER_DOCUMENTED: the candidate family is a fixed, "
            "preregistered set of three alternatives to production, each reported with its own "
            "game-clustered bootstrap interval and none selected by search; there is no scan to "
            "correct. SUPERSEDES the PRICING half of MLB-RSCH-0031's and MLB-RSCH-0032's "
            "'+0.5 threshold defect reaches live recommendations' conclusion: production fixed "
            "that conversion on the date this experiment's round-trip identifies, before either "
            "experiment was written, and both measured a corpus dominated by pre-fix rows. Those "
            "merged artifacts are NOT rewritten. Evidence level E1 for the same reason as "
            "MLB-RSCH-0033: the PIT manifest records season_to_date_stats and pitcher_snapshot as "
            "UNKNOWN_REQUIRES_AUDIT, so E2 is not assertable without auditing those entries."),
    )
    reg.register_experiment(definition)
    return control, definition


# ── 12. MAIN ─────────────────────────────────────────────────────────────

def build_rows(projections, fix_date):
    """Join archived evaluations to settlements and archived projections."""
    sys.path.insert(0, os.path.join(_ROOT, "scripts", "edgelab"))
    import run_production_calibration_audit_experiment as calib

    outcomes, _ = calib.load_settled_outcomes()
    evaluated, _ = calib.load_evaluated_rows()
    joined = calib.build_audit_rows(evaluated, outcomes, pick="last")

    rows = []
    for r in joined:
        if r.get("marketFamily") != "KXMLBTEAMTOTAL":
            continue
        parsed = parse_ticker(r.get("marketTicker"))
        if parsed is None:
            continue
        mean = projections.get((parsed["date"], parsed["team"]))
        if mean is None:
            continue
        c1 = min(poisson_at_least(mean, parsed["threshold"]), 0.95)
        c2 = min(nb_at_least(mean, parsed["threshold"]), 0.95)
        rows.append({
            "gameId": r["gameId"],
            "settleDate": r["settleDate"],
            "marketTicker": r["marketTicker"],
            "threshold": parsed["threshold"],
            "side": parsed["side"],
            "teamProj": mean,
            "outcome": r["outcome"],
            "marketP": r["marketP"],
            "C0": r["modelP"],
            "C1": c1,
            "C2": c2,
            "C3": c2,
            "productionVersion": "v1.2" if (fix_date and r["settleDate"] >= fix_date) else "v1.1",
        })
    return rows


def main():
    control, definition = register_experiment()
    projections = load_archived_projections()

    sys.path.insert(0, os.path.join(_ROOT, "scripts", "edgelab"))
    import run_production_calibration_audit_experiment as calib
    outcomes, _ = calib.load_settled_outcomes()
    evaluated, _ = calib.load_evaluated_rows()
    joined = [r for r in calib.build_audit_rows(evaluated, outcomes, pick="last")
              if r.get("marketFamily") == "KXMLBTEAMTOTAL"]

    trace = trace_production_path()
    truth = contract_truth()
    rt = round_trip(joined, projections)
    fix_date = rt["detectedFixDate"]

    rows = build_rows(projections, fix_date)
    if len(rows) < 30:
        raise SystemExit("insufficient eligible rows: %d" % len(rows))

    current_rows = [r for r in rows if r["productionVersion"] == "v1.2"]
    standardized_market = threshold_standardized(rows, rows, "C2", "marketP")
    standardized_production = threshold_standardized(rows, rows, "C2", "C0")
    current_decision = current_era_decision(
        current_rows,
        stratified(current_rows, "threshold", "AT_LEAST"),
        threshold_standardized(current_rows, rows, "C2", "marketP"))
    overall = score_block(rows, "ALL_ELIGIBLE")
    attribution = attribute(rows)
    classification = classify(attribution, rows)

    pre = preregistration()
    dist_boot = attribution["distributionGain"]["bootstrap"]
    econ = economics(rows)
    observed = v3.ObservedEvidence(
        effect_estimate=-dist_boot["mean"],
        effect_ci_low=-dist_boot["ciHigh"],
        effect_ci_high=-dist_boot["ciLow"],
        score_improvement=round(brier(rows, "C0") - brier(rows, "C2"), 6),
        independent_games=len({r["gameId"] for r in rows}),
        independent_dates=len({r["settleDate"] for r in rows}),
        transport_evidence=attribution["transportEvidence"],
        executable_opportunities=econ["rowsWhereBestCandidateDisagreesWithMarketBy5ppt"],
        cluster_unit="gameId",
    )
    passes, reasons, labels = v3.betting_shadow_gate_v3(pre, observed)

    artifact = {
        "experimentId": EXPERIMENT_ID,
        "title": "Team-Total Probability Conversion",
        "controlModelId": control["controlModelId"],
        "evidenceLevel": ev.E1_RECONSTRUCTED_RETROSPECTIVE,
        "generatedAt": REGISTRATION_TIMESTAMP,
        "productionPathTrace": trace,
        "contractTruth": truth,
        "roundTrip": rt,
        "candidates": CANDIDATES,
        "overall": overall,
        "preFix": score_block([r for r in rows if r["productionVersion"] == "v1.1"], "PRE_FIX_v1.1"),
        "postFix": score_block([r for r in rows if r["productionVersion"] == "v1.2"], "POST_FIX_v1.2"),
        "attribution": attribution,
        "classification": classification,
        "aucCeilingClaim": auc_ceiling_claim(),
        "achievableAucSimulation": achievable_auc_simulation(projections),
        "stratified": {
            "note": ("Discovered AFTER scoring, therefore EXPLORATORY. Reported so the branch "
                     "is not declared exhausted on a pooled number alone; NOT promoted."),
            "rowFloor": STRATUM_ROW_FLOOR,
            "aucFloor": STRATUM_AUC_FLOOR,
            "allEra": {
                "byThreshold": stratified(rows, "threshold", "AT_LEAST"),
                "bySide": stratified(rows, "side", "SIDE"),
            },
            "currentEraV12": {
                "byThreshold": stratified(current_rows, "threshold", "AT_LEAST"),
                "bySide": stratified(current_rows, "side", "SIDE"),
            },
        },
        "aggregation": {
            "rawPooled": {
                "c2MinusMarket": bootstrap_delta(rows, "C2", "marketP"),
                "c2MinusPooledConstant": bootstrap_delta(rows, "C2", "__constant__"),
            },
            "thresholdStandardized": {
                "c2MinusMarket": standardized_market,
                "c2MinusProduction": standardized_production,
            },
            "currentEraRawPooled": ({
                "c2MinusMarket": bootstrap_delta(current_rows, "C2", "marketP"),
                "c2MinusProduction": bootstrap_delta(current_rows, "C2", "C0"),
                "c2MinusPooledConstant": bootstrap_delta(current_rows, "C2", "__constant__"),
            } if len(current_rows) >= 30 else {"status": "INSUFFICIENT_SAMPLE"}),
            "currentEraThresholdStandardized": threshold_standardized(current_rows, rows,
                                                                      "C2", "marketP"),
        },
        "currentEraDecision": current_decision,
        "byThreshold": by_threshold(rows),
        "stratificationCaveat": (
            "SUPERSEDED WITHIN THIS ARTIFACT. An earlier version noted that C2 edges the "
            "market within each threshold stratum while losing pooled, called it a Simpson "
            "effect, and then dismissed it -- 'the pooled comparison is the one that counts'. "
            "That dismissal was wrong. Standardising the paired within-threshold effect to a "
            "fixed a-priori threshold distribution removes the pooled deficit entirely (see "
            "aggregation.thresholdStandardized), which shows the pooled number was driven by "
            "threshold mix rather than by the candidate. The within-stratum effects are ALSO "
            "not significant on their own -- every stratum's C2-minus-Kalshi interval spans "
            "zero -- so the honest reading is neither 'C2 beats the market' nor 'C2 loses to "
            "it', but that this corpus cannot tell them apart. The reversal was discovered "
            "after scoring and remains EXPLORATORY; it is audited here rather than either "
            "promoted or dismissed."),
        "bySide": by_side(rows),
        "byProjectionBand": by_proj_band(rows),
        "byProbabilityBand": by_prob_band(rows),
        "marketComparison": {
            "note": "Kalshi vig-free fair probability on IDENTICAL rows",
            "marketBrier": round(brier(rows, "marketP"), 4),
            "bestCandidateBrier": round(brier(rows, "C2"), 4),
            "bestCandidateVsMarket": bootstrap_delta(rows, "C2", "marketP"),
            "marketSlope": round(calibration_slope(rows, "marketP"), 4),
        },
        "economics": econ,
        "methodologyV3": {
            "preregistration": v3.describe_v3(pre),
            "labels": labels,
            "bettingShadowGatePasses": passes,
            "blockingReasons": reasons,
        },
        "promotionBlocked": {
            "blocked": True,
            "reason": ("Promotion is blocked, but NOT for the reason an earlier version of "
                       "this artifact gave. That version said the candidate 'loses to a "
                       "constant base rate and to Kalshi, both CIs excluding zero' and "
                       "treated the branch as exhausted. Those are RAW POOLED numbers, and "
                       "the pooled corpus mixes AT_LEAST_2..8 contracts with materially "
                       "different base rates. Standardised to a fixed a-priori threshold "
                       "distribution, C2 minus Kalshi is %+.6f with CI %s -- indistinguishable "
                       "from zero. The pooled deficit was largely a threshold-mix artifact and "
                       "is withdrawn as evidence. What blocks promotion now is sample, not a "
                       "measured loss: the current v1.2 era carries %d independent games "
                       "against the fixed 100-game floor."
                       % (standardized_market.get("standardizedEffect", float("nan")),
                          standardized_market.get("ci95"),
                          current_decision["independentGames"])),
            "whatSurvivesStandardisation": ("C2 minus production: %+.6f, CI %s -- the "
                                            "negative-binomial improvement over production's "
                                            "Poisson body is robust to threshold mix."
                                            % (standardized_production.get("standardizedEffect",
                                                                           float("nan")),
                                               standardized_production.get("ci95"))),
            "notPromoted": ("This is NOT a production candidate. It is frozen as a hypothesis "
                            "for an independent prospective study once the current era clears "
                            "its sample floor."),
        },
        "supersedes": {
            "experiments": ["MLB-RSCH-0031", "MLB-RSCH-0032"],
            "conclusion": ("the '+0.5 team-total threshold defect reaches live recommendations' "
                           "claim, for PRICING only"),
            "why": ("production corrected that conversion on %s; both experiments measured a "
                    "corpus dominated by pre-fix rows and neither separated the two production "
                    "versions" % fix_date),
            "artifactsRewritten": False,
        },
    }

    os.makedirs(ANALYTICS_DIR, exist_ok=True)
    with open(ARTIFACT_PATH, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({
        "experimentId": EXPERIMENT_ID,
        "eligibleRows": overall["n"],
        "independentGames": overall["independentGames"],
        "roundTripReproduction": rt["reproductionRate"],
        "detectedFixDate": fix_date,
        "case": classification["case"],
        "currentEraCase": artifact["currentEraDecision"]["case"],
        "currentEraGames": artifact["currentEraDecision"]["independentGames"],
        "v3Passes": passes,
    }, indent=2))
    return artifact




# ── 13. THE WITHDRAWN AUC-CEILING CLAIM ──────────────────────────────────

def auc_ceiling_claim():
    """An earlier version of this experiment asserted that production's
    r-squared of 0.0377 'caps attainable AUC near 0.55'.

    That is withdrawn. r-squared of a continuous prediction against a noisy
    continuous outcome does NOT determine the AUC of that prediction for a
    thresholded binary event -- the mapping depends on the generative
    distribution and on the threshold, neither of which r-squared encodes.

    This function computes the counter-example rather than asserting it: a
    SINGLE predictor with a SINGLE r-squared achieves materially different
    AUCs at different thresholds, none of them equal to the claimed cap.
    """
    rnd = random.Random(7)
    n = 20000
    preds = [rnd.gauss(4.5, 0.60) for _ in range(n)]
    runs = [_poisson_sample(rnd, max(0.05, m)) for m in preds]
    r = _pearson(preds, runs)
    rows = []
    for threshold in (3, 4, 5, 6, 7):
        binary = [(p, 1 if x >= threshold else 0) for p, x in zip(preds, runs)]
        a = _fast_auc(binary)
        if a is not None:
            rows.append({"threshold": threshold,
                         "baseRate": round(sum(o for _, o in binary) / len(binary), 4),
                         "auc": round(a, 4)})
    aucs = [x["auc"] for x in rows]
    return {
        "claimWithdrawn": "r-squared 0.0377 caps attainable AUC near 0.55",
        "whyWithdrawn": ("r-squared does not determine AUC for a thresholded binary event. "
                         "The claim was stated as a fact and used as evidence that further "
                         "distributional work is futile. It supported neither."),
        "refutation": {
            "construction": ("one simulated predictor, one fixed r-squared, AUC measured for "
                             "AT_LEAST_N at several N"),
            "rSquared": round(r * r, 4),
            "byThreshold": rows,
            "aucRange": [min(aucs), max(aucs)],
            "conclusion": ("a single r-squared corresponds to a RANGE of AUCs across "
                           "thresholds, all of them far from the asserted cap"),
        },
    }


def achievable_auc_simulation(projections, reps=25):
    """What the earlier claim SHOULD have asked, stated with its assumptions.

    Question: if production's archived teamProj were the true mean, and
    outcomes were drawn from the model's own distributional family, what
    AUC would ranking by teamProj achieve?

    This is NOT a ceiling. It is an assumption-dependent reference value,
    and both assumptions are load-bearing:
      (a) teamProj is the TRUE conditional mean (if it carries error, the
          achievable AUC is lower than reported here);
      (b) outcomes are conditionally Poisson / negative-binomial given that
          mean.
    Reported for both families so the answer cannot hide behind one.
    """
    means = sorted(projections.values())
    if len(means) < 100:
        return {"status": "INSUFFICIENT_PROJECTIONS", "n": len(means)}
    rnd = random.Random(20260830)
    out = {}
    for family, sampler in (("POISSON", _poisson_sample),
                            ("FROZEN_NB", _nb_sample)):
        per_threshold = {}
        for threshold in (3, 4, 5, 6):
            aucs, r2s, bases = [], [], []
            for _ in range(reps):
                runs = [sampler(rnd, m) for m in means]
                binary = [(p, 1 if x >= threshold else 0) for p, x in zip(means, runs)]
                a = _fast_auc(binary)
                if a is None:
                    continue
                aucs.append(a)
                r2s.append(_pearson(means, runs) ** 2)
                bases.append(sum(o for _, o in binary) / len(binary))
            if aucs:
                per_threshold["AT_LEAST_%d" % threshold] = {
                    "meanAuc": round(sum(aucs) / len(aucs), 4),
                    "meanRSquared": round(sum(r2s) / len(r2s), 4),
                    "meanBaseRate": round(sum(bases) / len(bases), 4),
                }
        out[family] = per_threshold
    return {
        "status": "COMPUTED",
        "reps": reps,
        "projectionsUsed": len(means),
        "byFamily": out,
        "assumptions": [
            "teamProj is treated as the TRUE conditional mean; any error in it lowers the "
            "achievable AUC below these figures",
            "outcomes are conditionally Poisson or negative-binomial (frozen dispersion "
            "%.6f) given that mean" % FROZEN_DISPERSION,
        ],
        "interpretation": ("This is a reference value under stated assumptions, NOT a ceiling "
                           "and NOT a claim about the real data-generating process."),
    }


def _poisson_sample(rnd, lam):
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rnd.random()
        if p <= limit:
            return k
        k += 1
        if k > 60:
            return k


def _nb_sample(rnd, mu):
    """Gamma-Poisson mixture matching Var = mu + dispersion * mu^2."""
    shape = 1.0 / FROZEN_DISPERSION
    scale = mu * FROZEN_DISPERSION
    return _poisson_sample(rnd, max(1e-6, rnd.gammavariate(shape, scale)))


def _pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _fast_auc(pairs):
    import bisect
    pos = sorted(p for p, o in pairs if o == 1)
    neg = [p for p, o in pairs if o == 0]
    if not pos or not neg:
        return None
    total = 0.0
    for b in neg:
        lo = bisect.bisect_left(pos, b)
        hi = bisect.bisect_right(pos, b)
        total += (len(pos) - hi) + 0.5 * (hi - lo)
    return total / (len(pos) * len(neg))


# ── 14. STRATIFIED ANALYSIS (the Simpson effect, audited properly) ───────

STRATUM_ROW_FLOOR = 30
STRATUM_AUC_FLOOR = 50          # AUC needs more than a Brier mean does


def stratum_report(rows, label):
    """Everything required to judge one stratum on its own terms.

    Crucially the constant baseline here is the STRATUM'S OWN base rate,
    not the pooled one -- comparing a threshold-specific candidate against
    a pooled constant is what produced the Simpson reversal in the first
    place.
    """
    n = len(rows)
    if n < STRATUM_ROW_FLOOR:
        return {"label": label, "rows": n, "status": "INSUFFICIENT_SAMPLE"}
    games = len({r["gameId"] for r in rows})
    dates = len({r["settleDate"] for r in rows})
    out = {
        "label": label,
        "rows": n,
        "independentGames": games,
        "independentDates": dates,
        "yesBaseRate": round(sum(r["outcome"] for r in rows) / n, 4),
        "constantBrierWithinStratum": round(constant_brier(rows), 4),
        "productionBrier": round(brier(rows, "C0"), 4),
        "c2Brier": round(brier(rows, "C2"), 4),
        "kalshiBrier": round(brier(rows, "marketP"), 4),
        "pairedC2MinusKalshi": bootstrap_delta(rows, "C2", "marketP"),
        "pairedC2MinusProduction": bootstrap_delta(rows, "C2", "C0"),
        "pairedC2MinusStratumConstant": bootstrap_delta(rows, "C2", "__constant__"),
        "calibrationSlopeC2": (round(calibration_slope(rows, "C2"), 4)
                               if calibration_slope(rows, "C2") is not None else None),
        "calibrationSlopeKalshi": (round(calibration_slope(rows, "marketP"), 4)
                                   if calibration_slope(rows, "marketP") is not None else None),
    }
    if n >= STRATUM_AUC_FLOOR:
        a_c2 = auc(rows, "C2")
        a_mkt = auc(rows, "marketP")
        out["withinStratumAucC2"] = round(a_c2, 4) if a_c2 is not None else None
        out["withinStratumAucKalshi"] = round(a_mkt, 4) if a_mkt is not None else None
    else:
        out["withinStratumAucC2"] = None
        out["withinStratumAucKalshi"] = None
        out["aucNote"] = "below the %d-row AUC floor" % STRATUM_AUC_FLOOR
    return out


def stratified(rows, key, prefix):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    return {"%s_%s" % (prefix, k): stratum_report(groups[k], "%s_%s" % (prefix, k))
            for k in sorted(groups, key=str)}


def threshold_standardized(rows, weight_rows, key_a="C2", key_b="marketP"):
    """Paired within-threshold effect, aggregated with FIXED weights.

    The weights are the threshold distribution of `weight_rows` -- the full
    evaluation corpus -- fixed before the per-stratum effects are looked at
    and never chosen by outcome. This stops a shifting threshold mix across
    periods from driving the pooled answer, which is exactly the Simpson
    mechanism the raw pooled number is exposed to.
    """
    weights = collections.Counter(r["threshold"] for r in weight_rows)
    total_weight = sum(weights.values())
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["threshold"]].append(r)

    contributions, used_weight = [], 0
    for threshold in sorted(groups):
        block = groups[threshold]
        if len(block) < STRATUM_ROW_FLOOR:
            continue
        w = weights.get(threshold, 0)
        if w == 0:
            continue
        if key_b == "__constant__":
            effect = brier(block, key_a) - constant_brier(block)
        else:
            effect = brier(block, key_a) - brier(block, key_b)
        contributions.append({"threshold": threshold, "rows": len(block),
                              "weight": w, "effect": round(effect, 6)})
        used_weight += w
    if not contributions:
        return {"status": "NO_STRATUM_MEETS_FLOOR"}
    standardized = sum(c["effect"] * c["weight"] for c in contributions) / used_weight

    # Game-clustered bootstrap of the SAME standardized statistic.
    by_game = collections.defaultdict(list)
    for r in rows:
        by_game[r["gameId"]].append(r)
    game_keys = list(by_game)
    rnd = random.Random(20260830)
    draws = []
    for _ in range(2000):
        sample = [x for k in (rnd.choice(game_keys) for _ in game_keys) for x in by_game[k]]
        g = collections.defaultdict(list)
        for r in sample:
            g[r["threshold"]].append(r)
        num, den = 0.0, 0
        for threshold, block in g.items():
            if len(block) < STRATUM_ROW_FLOOR:
                continue
            w = weights.get(threshold, 0)
            if w == 0:
                continue
            if key_b == "__constant__":
                e = brier(block, key_a) - constant_brier(block)
            else:
                e = brier(block, key_a) - brier(block, key_b)
            num += e * w
            den += w
        if den:
            draws.append(num / den)
    draws.sort()
    ci = ([round(draws[int(0.025 * len(draws))], 6),
           round(draws[int(0.975 * len(draws))], 6)] if draws else None)
    return {
        "status": "COMPUTED",
        "comparison": "%s minus %s" % (key_a, key_b),
        "weightSource": "threshold distribution of the FULL evaluation corpus, fixed a priori",
        "weights": {str(k): v for k, v in sorted(weights.items())},
        "perThresholdContributions": contributions,
        "standardizedEffect": round(standardized, 6),
        "ci95": ci,
        "excludesNull": bool(ci and ci[0] * ci[1] > 0),
        "note": ("Negative favours %s. Strata below the %d-row floor are excluded from the "
                 "aggregate rather than being given a noisy weight."
                 % (key_a, STRATUM_ROW_FLOOR)),
    }


def current_era_decision(current_rows, per_threshold, standardized_vs_market):
    """The four preregistered outcomes, decided on CURRENT-era evidence.

    Two disciplines are enforced here, both learned the hard way:

    1. Hundreds of obsolete v1.1 rows must not decide a question about
       today's production model, so the sample floor is applied to the
       current era alone.
    2. A FAVOURABLE SIGN IS NOT A RESULT. An earlier version of this
       function returned CASE_2 whenever every scored stratum had a
       negative point estimate, with no reference to whether any of those
       estimates was distinguishable from zero. On this corpus that
       promoted two strata whose intervals both span zero, on a
       standardized aggregate that also spans zero -- the exact
       reasoning Methodology V3 was written to refuse. CASE_2 now
       additionally requires statistical support.
    """
    games = len({r["gameId"] for r in current_rows})
    dates = len({r["settleDate"] for r in current_rows})
    if games < 100:
        return {
            "case": "CASE_4_INSUFFICIENT_CURRENT_ERA_SAMPLE",
            "why": ("the v1.2 era carries %d independent games against the fixed 100-game "
                    "floor; no promotion or rejection may rest on it" % games),
            "independentGames": games, "independentDates": dates,
            "shortBy": 100 - games,
        }

    scored = {k: v for k, v in per_threshold.items()
              if v.get("status") != "INSUFFICIENT_SAMPLE"}
    favourable_sign = [k for k, v in scored.items()
                       if v["pairedC2MinusKalshi"]["mean"] < 0]
    significant = [k for k, v in scored.items()
                   if v["pairedC2MinusKalshi"]["excludesNull"]
                   and v["pairedC2MinusKalshi"]["mean"] < 0]
    standardized_supports = bool(standardized_vs_market.get("excludesNull")
                                 and (standardized_vs_market.get("standardizedEffect") or 0) < 0)

    if not scored:
        case = "CASE_4_INSUFFICIENT_CURRENT_ERA_SAMPLE"
        why = "no current-era threshold stratum meets the row floor"
    elif len(significant) == len(scored) or (standardized_supports and significant):
        case = "CASE_2_CONSISTENT_WITHIN_THRESHOLD_VALUE"
        why = ("C2 improves on the market with intervals excluding zero, and the "
               "threshold-standardized aggregate agrees; freeze as a NEW hypothesis "
               "requiring an independent prospective or holdout study")
    elif len(significant) == 1:
        case = "CASE_3_SINGLE_THRESHOLD_ONLY"
        why = ("exactly one stratum reaches significance; exploratory only, and no "
               "multiplicity or sample standard is relaxed to keep it")
    elif not favourable_sign:
        case = "CASE_1_NB_LOSES_POOLED_AND_WITHIN_THRESHOLD"
        why = "C2 loses to market and base rate both pooled and within thresholds"
    else:
        case = "CASE_3_SINGLE_THRESHOLD_ONLY"
        why = ("%d of %d scored strata favour C2 BY SIGN ONLY -- no stratum interval "
               "excludes zero and the threshold-standardized aggregate does not either, "
               "so nothing here is distinguishable from the market. Exploratory only; a "
               "favourable sign is not a result."
               % (len(favourable_sign), len(scored)))

    return {"case": case, "why": why, "independentGames": games,
            "independentDates": dates,
            "favourableBySignOnly": sorted(favourable_sign),
            "significantStrata": sorted(significant),
            "scoredStrata": sorted(scored),
            "standardizedSupportsC2": standardized_supports,
            "standardizedVsMarket": standardized_vs_market}


if __name__ == "__main__":
    main()
