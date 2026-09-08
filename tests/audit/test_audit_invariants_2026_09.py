#!/usr/bin/env python3
"""
tests/audit/test_audit_invariants_2026_09.py
============================================
AUDIT / RESEARCH ONLY -- NOT IMPORTED BY PRODUCTION.

Executable evidence for docs/MLB_INSTITUTIONAL_SYSTEM_AUDIT_2026_09.md.

Nothing in `scripts/`, `lib/`, `api/` or any workflow imports this module.
It reads production modules and archived data; it never writes, never
mutates, and never changes production behavior. Adding or deleting this
file cannot alter a single recommendation.

WHY SOME TESTS ARE xfail
------------------------
Each `xfail` below states an invariant that *should* hold and that this
audit proved does NOT hold on the audited commit. They are marked
`strict=False` deliberately:

  - The suite stays green, so this audit-only PR does not turn CI red for
    defects it is documenting rather than fixing (the audit brief forbids
    remediation here).
  - The invariant is nevertheless executable and permanently recorded. The
    moment a remediation PR fixes the defect, the test reports XPASS --
    visible, and never a false failure.

Each xfail reason names the finding ID in the audit report. The PASSING
tests in this file are the complement: properties this audit verified are
currently CORRECT and that a future remediation must not regress.

Run just this module:

    python3 -m pytest tests/audit/test_audit_invariants_2026_09.py -v -rxX
"""

import glob
import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(_HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SLATE = os.path.join(ROOT, "data", "slate.json")
SLATES_GLOB = os.path.join(ROOT, "data", "slates", "*", "authoritative.json")
SNAPSHOTS_GLOB = os.path.join(
    ROOT, "data", "kalshi_registry_snapshots", "kalshi_search_*.json")

# Families the production marketLedger can actually qualify for real money.
PRODUCTION_MARKET_TYPES = frozenset(
    {"moneyline", "team_total", "f5_moneyline", "nrfi_yrfi", "total", "spread"})

# ── CR-6 evaluation population ───────────────────────────────────────────────
# The CR-6 invariant is evaluated over the most recent CR6_WINDOW_DATES
# ARCHIVED slates rather than data/slate.json. See the long note above
# test_js_and_python_engines_agree_on_the_same_market for why.
CR6_WINDOW_DATES = 20
CR6_MIN_OBSERVATIONS = 150


# ── helpers ──────────────────────────────────────────────────────────────────

def _load(path):
    with open(path) as f:
        return json.load(f)


def _ledger_rows(slate):
    for game in slate.get("games", []):
        for row in (game.get("marketLedger") or []):
            yield game, row


def _archived_slates(since=None):
    for path in sorted(glob.glob(SLATES_GLOB)):
        date = os.path.basename(os.path.dirname(path))
        if since and date < since:
            continue
        try:
            yield date, _load(path)
        except (ValueError, OSError):
            continue


def _ml_away_engine_disagreements(slate):
    """
    Per-game |JS engine - Python engine| on ML_Away, in percentage points.

    `game.modelProb.away` is api/slate.js's own projection engine; the sibling
    `marketLedger` row's `modelProb` is scripts/build_market_ledger.py's. They
    describe the SAME market, so any nonzero difference is two production
    engines disagreeing about one number.
    """
    out = []
    for game, row in _ledger_rows(slate):
        if row.get("market") != "ML_Away":
            continue
        js = (game.get("modelProb") or {}).get("away")
        py = row.get("modelProb")
        if js is None or py is None:
            continue
        out.append(abs(float(js) - float(py)))
    return out


def _cr6_population(slates):
    """
    Pure. Reduces an iterable of (date, slate) into the CR-6 evaluation
    population: the most recent CR6_WINDOW_DATES dates that actually carry
    comparable ML_Away observations, plus summary statistics.

    Taking a fixed COUNT of dates rather than a fixed calendar anchor is what
    makes this invariant both un-flippable by one benign day and still able to
    report a genuine fix (see the note on the test itself).
    """
    per_date = [(date, _ml_away_engine_disagreements(slate))
                for date, slate in slates]
    per_date = [(date, diffs) for date, diffs in per_date if diffs]
    window = per_date[-CR6_WINDOW_DATES:]
    diffs = [d for _date, ds in window for d in ds]
    return {
        "dates": [date for date, _ds in window],
        "perDate": window,
        "observations": diffs,
        "n": len(diffs),
        "worst": max(diffs) if diffs else None,
        "mean": (sum(diffs) / len(diffs)) if diffs else None,
        "overFloor": sum(1 for d in diffs if d > 1.0),
        "datesOverFloor": sum(1 for _date, ds in window if max(ds) > 1.0),
    }


@pytest.fixture(scope="module")
def live_slate():
    if not os.path.exists(SLATE):
        pytest.skip("data/slate.json not present in this checkout")
    return _load(SLATE)


# ─────────────────────────────────────────────────────────────────────────────
# CR-1 / issue #53 -- executable price must not be the midpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="AUDIT CR-1 / GitHub issue #53: executablePriceUsed is the mid-derived "
           "price on 100% of production ledger rows. The real yes_ask is captured "
           "in odds.kalshi.<family>.all_lines and discarded.",
    strict=False,
)
def test_executable_price_is_not_the_midpoint(live_slate):
    """
    `executablePriceUsed` claims (build_market_ledger.py:786) to be the YES ask.
    An ask and a mid coincide only on a zero-spread book, which is rare. If
    almost every row has executablePriceUsed == marketProbVF, the field is a
    midpoint wearing an executable label.
    """
    same = total = 0
    for _game, row in _ledger_rows(live_slate):
        vf, ex = row.get("marketProbVF"), row.get("executablePriceUsed")
        if vf is None or ex is None:
            continue
        total += 1
        if abs(float(vf) - float(ex)) < 0.06:
            same += 1
    if total == 0:
        pytest.skip("no ledger rows carrying both marketProbVF and executablePriceUsed")
    ratio = same / total
    assert ratio < 0.10, (
        "%d/%d (%.0f%%) of ledger rows price against the midpoint rather than the "
        "executable YES ask" % (same, total, ratio * 100))


@pytest.mark.xfail(
    reason="AUDIT CR-1: the selected rung's real yes_ask is present in all_lines "
           "but build_market_ledger.py reads yes_ask off the summary dict, which "
           "has no such key, and silently falls back to the mid.",
    strict=False,
)
def test_selected_team_total_rung_is_priced_at_its_own_ask(live_slate):
    """The ask for the exact chosen ticker is one JSON level away. Prove it is used."""
    checked = mispriced = 0
    for game, row in _ledger_rows(live_slate):
        side = {"TT_Away_Over": "away", "TT_Home_Over": "home"}.get(row.get("market"))
        if side is None or row.get("executablePriceUsed") is None:
            continue
        node = ((game.get("odds") or {}).get("kalshi") or {}).get("team_totals") or {}
        node = node.get(side) or {}
        best = node.get("best_ticker")
        line = next((ln for ln in (node.get("all_lines") or [])
                     if ln.get("ticker") == best), None)
        if not line or not line.get("yes_ask"):
            continue
        checked += 1
        if abs(float(row["executablePriceUsed"]) - float(line["yes_ask"]) * 100) > 0.51:
            mispriced += 1
    if checked == 0:
        pytest.skip("no team-total rows with a recoverable ask on this slate")
    assert mispriced == 0, (
        "%d/%d team-total rows are not priced at their own chosen rung's yes_ask"
        % (mispriced, checked))


# ─────────────────────────────────────────────────────────────────────────────
# CR-2 -- production entry points must be importable the way production runs them
# ─────────────────────────────────────────────────────────────────────────────

def _import_with_script_run_syspath(module_name):
    """
    Import `scripts/<module_name>.py` in a clean subprocess using the SAME
    sys.path a `python3 scripts/<entry_point>.py` invocation produces, and
    return the subprocess result. Read-only: imports the module, never executes
    an entry point.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # the GitHub runner has no PYTHONPATH set
    return subprocess.run(
        [sys.executable, "-P", "-c",
         "import sys; sys.path.insert(0, 'scripts'); import %s" % module_name],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_sibling_clv_modules_import_under_the_production_syspath():
    """
    PASSES today, and scopes CR-2: the sibling module `run_kalshi_clv_step.py`
    imports alongside the broken one is fine, so the outage is caused by exactly
    one module's top-level `from lib.edgelab import ...`, not by a general
    packaging problem.
    """
    if not os.path.exists(os.path.join(ROOT, "scripts", "fetch_kalshi_clv_v2.py")):
        pytest.skip("scripts/fetch_kalshi_clv_v2.py not present")
    proc = _import_with_script_run_syspath("fetch_kalshi_clv_v2")
    assert "ModuleNotFoundError" not in proc.stderr, proc.stderr.strip()[-500:]


# REMEDIATION WAVE 0 converted this invariant from xfail to a REQUIRED GUARD.
#
# It was xfail because CR-2 was live: scripts/clv_from_snapshot.py imported
# lib.edgelab at module scope while scripts/run_kalshi_clv_step.py put only
# scripts/ on sys.path, and clv-update.yml failed on every scheduled run from
# 2026-09-02 to 2026-09-07 with ModuleNotFoundError: No module named 'lib'.
#
# Wave 0 fixed the root cause (the repository root is now placed on sys.path
# before the import, matching the pattern ~20 sibling scripts already use), so
# the xfail marker is REMOVED rather than left to report a permanent XPASS.
# The assertion itself is unchanged -- it was never weakened to make it pass.
#
# The broader, repo-wide form of this guard lives in
# tests/audit/test_workflow_entry_points.py, which statically proves NO script
# can reintroduce the defect.
@pytest.mark.parametrize("module_name", ["clv_from_snapshot"])
def test_workflow_entry_points_import_the_way_the_workflow_runs_them(module_name):
    """
    The existing tests import these modules after inserting the repository ROOT
    on sys.path. The workflow does not: run_kalshi_clv_step.py:26-30 inserts
    ONLY `scripts/`, then imports these modules, and clv-update.yml runs it as
    `python3 scripts/run_kalshi_clv_step.py <date>` from the repo root.

    This reproduces exactly that sys.path and exactly that import, in a clean
    subprocess -- deliberately importing the module rather than executing the
    entry point, so the check stays strictly read-only even after the defect is
    fixed and the script would otherwise run for real.

    `-P` is load-bearing and not a detail: when Python runs a SCRIPT it puts the
    SCRIPT'S OWN DIRECTORY at sys.path[0], not the working directory -- which is
    precisely why `python3 scripts/run_kalshi_clv_step.py` cannot see `lib/`
    while `python3 -c` from the repo root can. Without `-P` the cwd is prepended
    and this probe passes vacuously against a broken entry point.

    This is the gap that let a five-day production outage pass 9,623 green tests.
    """
    if not os.path.exists(os.path.join(ROOT, "scripts", module_name + ".py")):
        pytest.skip("scripts/%s.py not present" % module_name)
    proc = _import_with_script_run_syspath(module_name)
    assert "ModuleNotFoundError" not in proc.stderr, (
        "scripts/%s.py cannot be imported with the sys.path its production entry "
        "point (scripts/run_kalshi_clv_step.py) actually sets:\n%s"
        % (module_name, proc.stderr.strip()[-800:]))


# ─────────────────────────────────────────────────────────────────────────────
# CR-3 -- doubleheader legs must never share a market identity
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="AUDIT CR-3: kalshiKey is date+teams with no leg discriminator. "
           "2026-06-17 SFATL and 2026-07-11 MILPIT each assigned BOTH legs the "
           "same Kalshi tickers; the 06-17 case produced real-money-tier rows "
           "and reached bets.json (rows 2026-06-17-111 / -112).",
    strict=False,
)
def test_doubleheader_legs_never_share_a_kalshi_ticker():
    offenders = []
    for date, slate in _archived_slates():
        by_key = {}
        for game in slate.get("games", []):
            by_key.setdefault(game.get("kalshiKey"), []).append(game)
        for key, legs in by_key.items():
            if key is None or len(legs) < 2:
                continue
            tickers = []
            for game in legs:
                kal = (game.get("odds") or {}).get("kalshi") or {}
                tickers.append((
                    (kal.get("ml") or {}).get("away_ticker"),
                    ((kal.get("team_totals") or {}).get("away") or {}).get("best_ticker"),
                    (kal.get("f5ml") or {}).get("away_ticker"),
                ))
            for i in range(len(tickers)):
                for j in range(i + 1, len(tickers)):
                    shared = [a for a, b in zip(tickers[i], tickers[j])
                              if a is not None and a == b]
                    if shared:
                        offenders.append((date, key, shared))
    assert offenders == [], (
        "doubleheader legs sharing Kalshi tickers: %r" % (offenders[:5],))


def test_doubleheader_legs_never_share_a_gameid():
    """
    PASSES today. MLB gamePk is a correct physical-game identity and is
    distinct per leg -- the raw material for a proper fix already exists in
    the slate; only kalshiKey discards it.
    """
    for date, slate in _archived_slates():
        ids = [g.get("gameId") for g in slate.get("games", []) if g.get("gameId")]
        assert len(ids) == len(set(ids)), "duplicate gameId on %s" % date


# ─────────────────────────────────────────────────────────────────────────────
# CR-5 -- a one-sided book must never produce a halved midpoint
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="AUDIT CR-5: build_kalshi_registry.py:202 computes "
           "mid = ((bid or 0)+(ask or 0))/2, so an ask-only book yields ask/2 -- "
           "half the true price. 29.4% of 132,619 archived rows are one-sided.",
    strict=False,
)
def test_one_sided_book_never_produces_a_halved_midpoint():
    files = sorted(glob.glob(SNAPSHOTS_GLOB))[-40:]
    if not files:
        pytest.skip("no archived registry snapshots")
    halved = total = 0
    example = None
    for path in files:
        try:
            snap = _load(path)
        except (ValueError, OSError):
            continue
        for market in snap.get("markets", []):
            bid, ask, mid = market.get("yes_bid"), market.get("yes_ask"), market.get("mid")
            if mid is None:
                continue
            total += 1
            bid_empty, ask_empty = bid in (None, 0), ask in (None, 0)
            if bid_empty == ask_empty:
                continue
            live = ask if bid_empty else bid
            if live and abs(mid - live / 2.0) < 1e-9:
                halved += 1
                if example is None:
                    example = (os.path.basename(path), market.get("market_ticker"),
                               bid, ask, mid)
    if total == 0:
        pytest.skip("no priced markets in the sampled snapshots")
    assert halved == 0, (
        "%d/%d archived market rows carry a midpoint that is exactly half a "
        "one-sided book (e.g. %r)" % (halved, total, example))


def test_one_sided_books_have_not_yet_reached_a_production_chosen_rung():
    """
    PASSES today, and is the reason CR-5 is 'latent' rather than 'realised'.
    Across every archived authoritative slate, no rung selected by best_line()
    had a one-sided book. If this ever fails, CR-5 has fired on real money.
    """
    hits = []
    for date, slate in _archived_slates():
        for game in slate.get("games", []):
            kal = (game.get("odds") or {}).get("kalshi") or {}
            nodes = [(kal.get("team_totals") or {}).get("away"),
                     (kal.get("team_totals") or {}).get("home"),
                     kal.get("total"), kal.get("rl")]
            for node in nodes:
                if not node:
                    continue
                best = node.get("best_ticker")
                line = next((ln for ln in (node.get("all_lines") or [])
                             if ln.get("ticker") == best), None)
                if not line:
                    continue
                bid_empty = line.get("yes_bid") in (None, 0)
                ask_empty = line.get("yes_ask") in (None, 0)
                if bid_empty != ask_empty:
                    hits.append((date, best, line.get("yes_bid"), line.get("yes_ask")))
    assert hits == [], (
        "a production-selected rung had a one-sided book (CR-5 has fired): %r"
        % (hits[:5],))


# ─────────────────────────────────────────────────────────────────────────────
# CR-6 -- there must be exactly one production probability for a market
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="AUDIT CR-6: api/slate.js carries a complete second projection engine. "
           "Its ML_Away probability differs from build_market_ledger.py's by a "
           "mean of 1.74pp (max 8.86pp) across the archived evaluation window; "
           "66% of games diverge by more than the entire 1.0pp PAPER "
           "qualification floor.",
    strict=False,
)
def test_js_and_python_engines_agree_on_the_same_market():
    """
    EVIDENCE SOURCE: the archived slate corpus, NOT data/slate.json.

    This invariant originally read data/slate.json -- one current day's slate,
    rewritten by scheduled data commits many times a day (129 commits touched
    it in the 30 days before this correction). On 2026-09-08 that file happened
    to contain only 3 comparable games which happened to agree to within
    0.42pp, and the invariant reported XPASS while CR-6 was entirely untouched:
    both engines still present, neither engine changed by a single byte. A
    one-day sample is not a population, and an audit invariant that a routine
    data commit can silently clear is worse than no invariant at all.

    The corrected source is `data/slates/<date>/authoritative.json` -- the
    archived, committed, write-once corpus the other invariants in this module
    already read through `_archived_slates()`.

    WHY A ROLLING COUNT OF DATES RATHER THAN A FIXED CALENDAR ANCHOR
    ----------------------------------------------------------------
    A fixed anchor (e.g. `since="2026-08-15"`) would pin an immutable stretch
    of history, so it could never XPASS: archived August slates keep their
    divergent values forever no matter what Wave 1 does to api/slate.js. That
    is a permanently-stuck invariant, which is its own kind of dead signal.

    Taking the most recent CR6_WINDOW_DATES (20) dates that carry comparable
    observations gives both required properties:

      - A single benign day CANNOT flip it. The verdict is the maximum over
        ~20 independent dates and ~260 games; one agreeing day contributes
        nothing to that maximum while 19 other dates still exceed the floor.
        Today 20 of the 20 dates in the window individually exceed 1.0pp.
      - A genuine fix CAN eventually clear it. Once CR-6 is actually repaired,
        20 subsequent archived dates roll into the window and it reports
        XPASS -- meaning the engines agreed across ~260 real games on 20
        separate days, which is a fix, not a coincidence.

    The threshold is UNCHANGED at 1.0pp (the PAPER qualification floor). The
    predicate is also strictly STRONGER than before: previously max over one
    day, now max over twenty. Nothing here is loosened to force an XFAIL.

    As of this correction the window is 2026-08-16..2026-09-07 and reproduces
    the audit report's published CR-6 figures exactly: n=261, mean 1.74pp,
    max 8.86pp, 66.3% over the 1.0pp floor.
    """
    pop = _cr6_population(_archived_slates())

    # Insufficient corpus is a hard failure, never a skip and never a pass: a
    # thinned archive must not be able to produce an all-clear either. The
    # required (non-xfail) guard below is what actually turns CI red for this,
    # so the signal is not buried inside an xfail.
    assert len(pop["dates"]) >= CR6_WINDOW_DATES and pop["n"] >= CR6_MIN_OBSERVATIONS, (
        "CR-6 cannot be honestly evaluated: the archived corpus yielded only %d "
        "date(s) / %d observation(s), below the required %d / %d"
        % (len(pop["dates"]), pop["n"], CR6_WINDOW_DATES, CR6_MIN_OBSERVATIONS))

    assert pop["worst"] <= 1.0, (
        "the JS (api/slate.js) and Python (build_market_ledger.py) engines "
        "disagree by up to %.2fpp on ML_Away across %d games on %d archived "
        "dates (%s..%s); mean %.2fpp; %d/%d games (%.1f%%) exceed the 1.0pp "
        "PAPER qualification floor; %d/%d individual dates exceed it"
        % (pop["worst"], pop["n"], len(pop["dates"]),
           pop["dates"][0], pop["dates"][-1], pop["mean"],
           pop["overFloor"], pop["n"], 100.0 * pop["overFloor"] / pop["n"],
           pop["datesOverFloor"], len(pop["dates"])))


# ── guards on the CR-6 invariant itself (REQUIRED -- deliberately not xfail) ──
#
# The CR-6 invariant produced a false XPASS on 2026-09-08 because its evidence
# source was mutable. These three tests are the minimum needed to prove that
# cannot recur, and they are required guards rather than xfails: if the CR-6
# invariant ever becomes flippable-by-one-day again, CI must go red for THAT,
# independently of whether CR-6 is fixed.

def _synthetic_slate(n_games, disagreement_pp):
    """An archived-slate-shaped document with a known engine disagreement."""
    return {"games": [
        {"modelProb": {"away": 50.0 + disagreement_pp},
         "marketLedger": [{"market": "ML_Away", "modelProb": 50.0}]}
        for _ in range(n_games)
    ]}


def test_the_cr6_invariant_does_not_read_the_mutable_daily_slate():
    """
    Source-level proof. data/slate.json is rewritten by scheduled data commits
    many times a day; an audit invariant may not depend on it.
    """
    import inspect
    fn = test_js_and_python_engines_agree_on_the_same_market

    assert "live_slate" not in inspect.signature(fn).parameters, (
        "the CR-6 invariant must not consume the live_slate fixture -- that "
        "fixture reads the mutable data/slate.json")

    body = inspect.getsource(fn)
    _, _, body = body.partition('"""')          # skip the decorator
    _, _, body = body.partition('"""')          # skip the docstring's own prose
    for forbidden in ("live_slate", "SLATE", "slate.json"):
        assert forbidden not in body, (
            "the CR-6 invariant's executable body references %r; its evidence "
            "source must be the archived corpus via _archived_slates()"
            % forbidden)
    assert "_archived_slates()" in body, (
        "the CR-6 invariant must draw its population from the canonical "
        "archived-slate loader")


def test_the_cr6_evaluation_population_is_large_enough_to_be_a_population():
    """
    A thinning archive must turn CI red here rather than quietly shrinking the
    CR-6 invariant back down toward a one-day sample.
    """
    pop = _cr6_population(_archived_slates())
    assert len(pop["dates"]) >= CR6_WINDOW_DATES, (
        "CR-6 is evaluated over %d archived date(s); at least %d are required"
        % (len(pop["dates"]), CR6_WINDOW_DATES))
    assert pop["n"] >= CR6_MIN_OBSERVATIONS, (
        "CR-6 is evaluated over %d observation(s); at least %d are required"
        % (pop["n"], CR6_MIN_OBSERVATIONS))


def test_a_single_benign_day_cannot_clear_the_cr6_invariant():
    """
    THE regression this correction exists to prevent, tested directly.

    Splice a perfectly-agreeing day onto the real archived corpus -- exactly
    the shape of the 2026-09-08 slate that caused the false XPASS -- and the
    verdict must be unchanged. The final assertion is the guard-the-guard: the
    same benign day WOULD have cleared the old one-day predicate, so this test
    is demonstrably capable of failing.
    """
    real = list(_archived_slates())
    benign = ("2999-01-01", _synthetic_slate(n_games=3, disagreement_pp=0.42))

    before = _cr6_population(real)
    after = _cr6_population(real + [benign])

    assert before["worst"] > 1.0, (
        "precondition: the archived corpus must currently show CR-6")
    assert after["worst"] > 1.0, (
        "one benign day cleared the CR-6 invariant (worst fell to %.2fpp) -- "
        "the invariant is flippable by a single day's data commit again"
        % after["worst"])
    assert after["n"] >= CR6_MIN_OBSERVATIONS

    # Guard the guard: the benign day alone WOULD clear a one-day predicate,
    # and a wholly-agreeing population DOES clear the corrected one. So the
    # assertion is falsifiable and the corrected source is what does the work.
    assert max(_ml_away_engine_disagreements(benign[1])) <= 1.0
    all_benign = [("2999-01-%02d" % (i + 1), _synthetic_slate(15, 0.42))
                  for i in range(CR6_WINDOW_DATES)]
    assert _cr6_population(all_benign)["worst"] <= 1.0, (
        "the corrected invariant must still be able to pass when the engines "
        "genuinely agree across the whole window")


# ─────────────────────────────────────────────────────────────────────────────
# M-5 -- Poisson(0) is a point mass at zero
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="AUDIT M-5: build_market_ledger.poisson_pmf returns 0.0 for lam<=0 at "
           "every k including k=0, where P(X=0) must be 1.0. MLB-RSCH-0010 found "
           "and fixed this inside research only; production is untouched. A lambda "
           "of 0 would make p_nrfi=0 and therefore p_yrfi=1.0.",
    strict=False,
)
def test_poisson_pmf_zero_lambda_is_a_point_mass_at_zero():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from build_market_ledger import poisson_pmf  # noqa: E402
    assert poisson_pmf(0, 0) == 1.0
    assert poisson_pmf(1, 0) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# L-2 / issue #54 -- manual-import idempotency after confirmed receipt
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.xfail(
    reason="AUDIT L-2 / GitHub issue #54: _ALWAYS_PRESERVE_FIELDS omits every "
           "confirmedReceipt* field and _content_fingerprint does not exclude "
           "them, so resubmitting an identical payload for a confirmed bet "
           "yields CONFLICT instead of DUPLICATE_NOOP.",
    strict=False,
)
def test_resubmitting_a_confirmed_bet_is_a_noop_not_a_conflict():
    from lib.edgelab import bets as B  # noqa: E402
    record = B.build_manual_bet_record(
        market_ticker="KXMLBGAME-26AUG071910AAABBB-AAA", selection="YES",
        stake=10.0, entry_price=0.55, entry_timestamp=None,
        game_date="2026-08-07", matchup="AAA@BBB", market_family="game_result",
        import_batch_id="audit-invariant-repro-v1", source_bet_key="k1")
    existing = dict(record)
    existing.update({
        "status": "settled", "result": "WIN",
        "returnAmount": 18.18, "netProfitLoss": 8.18,
        "confirmedReceiptReturn": 18.18, "confirmedReceiptNetProfitLoss": 8.18,
        "confirmedReceiptSource": "kalshi_receipt", "confirmedReceiptNote": "ok",
        "confirmedReceiptAt": "2026-08-08T00:00:00Z",
    })
    merged = B._inherit_lifecycle_fields(dict(record), existing)
    assert B._content_fingerprint(merged) == B._content_fingerprint(existing), (
        "identical resubmission of a confirmed bet fingerprints as a CONFLICT; "
        "differing fields: %r"
        % sorted(k for k in set(merged) | set(existing)
                 if merged.get(k) != existing.get(k)))


# ─────────────────────────────────────────────────────────────────────────────
# Properties this audit verified are CORRECT -- these must never regress
# ─────────────────────────────────────────────────────────────────────────────

def test_no_order_placement_code_exists_anywhere():
    """
    The single most valuable safety property in the system: there is no Kalshi
    trading client, so no bug in any layer can place an unauthorised order.
    """
    proc = subprocess.run(
        ["grep", "-rniE", r"create_order|place_order|portfolio/orders",
         os.path.join(ROOT, "scripts"), os.path.join(ROOT, "lib"),
         os.path.join(ROOT, "api")],
        capture_output=True, text=True)
    hits = [ln for ln in proc.stdout.splitlines() if "/tests/" not in ln]
    assert hits == [], "order-placement code appeared in the repository: %r" % hits[:5]


def test_total_and_team_total_settlement_thresholds_stay_as_certified():
    """
    Section 4 certification, pinned. Integer game/inning totals settle on
    `>= N`; half-point team totals and winning margins settle on `> N - 0.5`.
    Both were verified against MLB ground truth, not inferred.
    """
    from lib.edgelab.settlement import settle_market  # noqa: E402
    outcome = {"awayRuns": 5, "homeRuns": 4, "awayAbbr": "AAA", "homeAbbr": "BBB",
               "gameStatus": "Final"}
    assert settle_market({"marketFamily": "game_total", "threshold": 9}, outcome)[1] == "YES"
    assert settle_market({"marketFamily": "game_total", "threshold": 10}, outcome)[1] == "NO"
    tt = {"marketFamily": "team_total", "threshold": 4.5, "team": "AAA"}
    assert settle_market(tt, outcome)[1] == "YES"          # 5 > 4.5
    tt5 = {"marketFamily": "team_total", "threshold": 5.5, "team": "AAA"}
    assert settle_market(tt5, outcome)[1] == "NO"          # 5 !> 5.5


def test_period_scoped_markets_never_settle_on_the_full_game_score():
    """The F5-spread horizon fix. Refusal, never substitution."""
    from lib.edgelab.settlement import settle_market  # noqa: E402
    status, result, reason = settle_market(
        {"marketFamily": "winning_margin", "marketHorizon": "F5",
         "threshold": 1.5, "team": "AAA"},
        {"awayRuns": 9, "homeRuns": 1, "awayAbbr": "AAA", "homeAbbr": "BBB",
         "gameStatus": "Final", "periodScores": {}})
    assert status == "SETTLEMENT_UNRESOLVED"
    assert result is None
    assert reason == "missing_period_score_F5"


def test_clv_convention_never_substitutes_a_midpoint():
    """clv_convention.py's central guarantee, pinned."""
    from lib.edgelab import clv_convention as cc  # noqa: E402
    assert cc.executable_price_cents({"yesBid": 40.0, "yesAsk": 60.0}, cc.SIDE_YES) == 60.0
    assert cc.executable_price_cents({"yesBid": 40.0, "yesAsk": 60.0}, cc.SIDE_NO) == 60.0
    # No ask on the YES side means no YES price -- never the mid.
    assert cc.executable_price_cents({"yesBid": 40.0}, cc.SIDE_YES) is None


def test_kalshi_taker_fee_is_applied_exactly_once():
    """
    The fee shifts the break-even reference price once; it is never also
    subtracted from the edge. Pins the double-fee fix.
    """
    from lib.edgelab import kalshi_fees as kf  # noqa: E402
    price = 0.55
    break_even = kf.fee_adjusted_break_even_probability(price, fee_type=kf.FEE_TYPE_TAKER)
    expected = price + kf.FEE_MULTIPLIER_TAKER_STANDARD * price * (1 - price)
    assert break_even == pytest.approx(expected, abs=5e-4)
    assert kf.net_expected_value_per_dollar(break_even, price,
                                            fee_type=kf.FEE_TYPE_TAKER) == pytest.approx(0.0, abs=5e-3)


def test_kxmlbrfi_suspension_still_holds_on_every_archived_slate():
    """
    NRFI/YRFI is structurally +11.87pp biased on 100% of games (audit M-2).
    The MLB-RSCH-0032 suspension is the only thing keeping it off real money.
    """
    real_money = []
    for date, slate in _archived_slates(since="2026-08-15"):
        for _game, row in _ledger_rows(slate):
            if row.get("market") not in ("NRFI", "YRFI"):
                continue
            if row.get("status") == "Accepted" and row.get("confidence") in ("HIGH", "MEDIUM"):
                real_money.append((date, row.get("market"), row.get("confidence")))
    assert real_money == [], (
        "KXMLBRFI suspension has lapsed -- real-money-tier rows: %r" % real_money[:5])


def test_only_yes_side_purchases_reach_the_production_ledger():
    """
    Production never buys NO, so no NO-side execution economics are exercised.
    If this ever fails, the NO-side pricing path (untested in production) has
    become live and needs its own audit.
    """
    required = {"NRFI", "YRFI", "F5_ML_Away", "F5_ML_Home", "TT_Away_Over",
                "TT_Home_Over", "ML_Away", "ML_Home", "Game_Total",
                "RL_Away", "RL_Home"}
    seen = set()
    for _date, slate in _archived_slates(since="2026-08-15"):
        for _game, row in _ledger_rows(slate):
            if row.get("market"):
                seen.add(row["market"])
    if not seen:
        pytest.skip("no archived slates in the window")
    assert seen <= required, "unexpected market key in the production ledger: %r" % (
        sorted(seen - required),)
