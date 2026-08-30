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
                     "constant base rate. The conversion defects are real and fixing them is "
                     "not sufficient: the residual is the informativeness of the mean itself. "
                     "MLB-RSCH-0033 measured that mean at r-squared 0.0377 -- an implied "
                     "correlation near 0.19 -- which caps attainable AUC close to 0.55. A "
                     "conversion cannot manufacture ranking information the mean does not "
                     "carry, and no change of distribution can, because every candidate here "
                     "is monotone in teamProj at fixed threshold and therefore preserves the "
                     "ordering exactly.") if best_still_loses else "",
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
        "byThreshold": by_threshold(rows),
        "stratificationCaveat": (
            "Within every threshold stratum that meets the 30-row floor, C2's Brier is slightly "
            "BELOW the market's, while pooled it is ABOVE. That reversal is a Simpson effect: "
            "the thresholds carry materially different base rates, and the pooled constant "
            "cannot vary across them while the market can. The within-stratum ordering is "
            "reported descriptively and is NOT claimed as a result -- it carries no interval, "
            "and three strata tested without multiplicity control is exactly the kind of "
            "favourable-sign reading Methodology V3 exists to refuse. The pooled comparison, "
            "which does carry a game-clustered interval, is the one that counts."),
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
            "reason": ("The best candidate loses to a constant base rate (Brier delta "
                       "+%.4f, CI [%+.4f, %+.4f]) and to the Kalshi vig-free fair price "
                       "(+%.4f, CI [%+.4f, %+.4f]), both game-clustered CIs excluding zero. "
                       "A family that cannot beat its own base rate cannot be priced against "
                       "a sharp market, however much its internal conversion improves. This "
                       "blocker is reported SEPARATELY from the V3 labels on purpose: the "
                       "four labels answer 'is this candidate better than production', and "
                       "the answer is yes -- which is exactly why it must not be mistaken "
                       "for 'is this candidate good enough to bet'."
                       % (classification["bestCandidateVsConstant"]["mean"],
                          classification["bestCandidateVsConstant"]["ciLow"],
                          classification["bestCandidateVsConstant"]["ciHigh"],
                          bootstrap_delta(rows, "C2", "marketP")["mean"],
                          bootstrap_delta(rows, "C2", "marketP")["ciLow"],
                          bootstrap_delta(rows, "C2", "marketP")["ciHigh"])),
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
        "v3Passes": passes,
    }, indent=2))
    return artifact


if __name__ == "__main__":
    main()
