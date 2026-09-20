#!/usr/bin/env python3
"""
tests/test_bankroll_refresh_coupling.py
=======================================
JUST-IN-TIME BANKROLL FRESHNESS, end to end.

The 2026-09-19 failure was not a broken workflow. `publish-bankroll.yml`
succeeded every time it ran. It was an ARCHITECTURAL gap: the publisher
and the card build were two independent `*/15`-ish crons, GitHub's
scheduler fired the publisher at 17:48, 19:31, 20:08, 22:19 and 00:15,
and nothing made either one happen near the other. The card therefore
read a reading older than the 30-minute sizing window and published
`bankrollStatus: STALE` / `dollarSizingVerdict: NO_DOLLAR_SIZING` --
correct, fail-closed, and operationally useless.

`scripts/ci/refresh_bankroll_context.py` closes it by PULLING a reading
whose age the build knows, instead of hoping an unrelated cron ran
recently. These tests pin the eleven properties that make that safe.
"""
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lib import bankroll_context as ctx  # noqa: E402
from lib import handicap_runtime as runtime  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "refresh_bankroll_context",
    os.path.join(ROOT, "scripts", "ci", "refresh_bankroll_context.py"),
)
refresh_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(refresh_mod)

WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "fetch-slate.yml")
SCRIPT_PATH = os.path.join(ROOT, "scripts", "ci", "refresh_bankroll_context.py")

NOW = datetime(2026, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
AMOUNT = 1234.56  # the number that must never escape


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def sealed(minutes_ago=1, **overrides):
    payload = {
        "schemaVersion": 3,
        "bankroll": AMOUNT,
        "currency": "USD",
        "observedAt": iso(NOW - timedelta(minutes=minutes_ago)),
        "source": "kalshi_authenticated_balance",
        "valueType": "KALSHI_AVAILABLE_CASH_BALANCE",
    }
    payload.update(overrides)
    return payload


def workflow():
    with open(WORKFLOW_PATH) as handle:
        return yaml.safe_load(handle)


# ── a GitHub the tests own ────────────────────────────────────────────

class FakeGitHub:
    """Records every call, so "exactly one dispatch" is provable."""

    def __init__(self, runs=None, dispatch_status=204, appear_after=0, finish_after=1,
                 conclusion="success"):
        self.calls = []
        self.runs = list(runs or [])
        self.dispatch_status = dispatch_status
        self.appear_after = appear_after
        self.finish_after = finish_after
        self.conclusion = conclusion
        self._list_calls = 0
        self._run_polls = 0

    @property
    def dispatches(self):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith("/dispatches")]

    def __call__(self, method, path, token, body=None, timeout=30):
        self.calls.append((method, path, body))
        if method == "POST" and path.endswith("/dispatches"):
            if self.dispatch_status == 204:
                self.runs.insert(0, {
                    "id": 999, "event": "workflow_dispatch", "status": "queued",
                    "created_at": iso(NOW), "conclusion": None,
                })
            return self.dispatch_status, ({"message": "refused"}
                                          if self.dispatch_status != 204 else None)
        if "/runs?" in path:
            self._list_calls += 1
            visible = self.runs if self._list_calls > self.appear_after else [
                r for r in self.runs if r["id"] != 999]
            return 200, {"workflow_runs": visible}
        if re.search(r"/actions/runs/\d+$", path):
            self._run_polls += 1
            run = dict(self.runs[0])
            if self._run_polls >= self.finish_after:
                run["status"] = "completed"
                run["conclusion"] = self.conclusion
            return 200, run
        return 404, None


@pytest.fixture
def clock():
    state = {"now": NOW}

    def now():
        return state["now"]

    def sleep(seconds):
        state["now"] = state["now"] + timedelta(seconds=seconds)

    return state, now, sleep


def run_refresh(github, clock, token="pat", max_wait_seconds=420, out=None):
    _state, now, sleep = clock
    original = refresh_mod.api
    refresh_mod.api = github
    try:
        return refresh_mod.refresh(
            token, max_wait_seconds=max_wait_seconds, poll_seconds=10,
            sleep=sleep, now=now, out=out or open(os.devnull, "w"))
    finally:
        refresh_mod.api = original


# ── 1. fresh publisher + card build ───────────────────────────────────

def test_a_fresh_reading_sizes_privately_and_the_public_runtime_redacts_it(clock):
    """The whole point: the refresh succeeds, the context that lands is
    FRESH and sizing-authoritative in private, and the artifact a public
    repository commits still carries no amount."""
    github = FakeGitHub()
    outcome, run_id = run_refresh(github, clock)
    assert outcome == refresh_mod.OUTCOME_REFRESHED
    assert run_id == 999

    private = ctx.context_from_secret(sealed(minutes_ago=1), now=NOW)
    assert private["status"] == ctx.STATUS_FRESH
    assert private["sizingAllowed"] is True
    assert private["bankroll"] == AMOUNT
    assert ctx.dollar_sizing_verdict(private)["verdict"] == ctx.SIZING_PERMITTED

    public = ctx.redacted(private)
    assert public["bankroll"] is None
    assert public["numericBankrollAvailable"] is False
    assert public["status"] == ctx.STATUS_FRESH, "redaction must not hide the STATUS"
    assert str(AMOUNT) not in json.dumps(public)


# ── 2. publisher fails → the card fails closed ────────────────────────

@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_a_publisher_that_does_not_succeed_leaves_the_card_failing_closed(clock, conclusion):
    github = FakeGitHub(conclusion=conclusion)
    outcome, _run = run_refresh(github, clock)
    assert outcome == refresh_mod.OUTCOME_PUBLISHER_FAILED
    assert outcome not in refresh_mod.SUCCESSFUL_OUTCOMES

    # Whatever the secret still holds is old, so the card refuses to size.
    stale = ctx.context_from_secret(sealed(minutes_ago=180), now=NOW)
    assert stale["status"] == ctx.STATUS_STALE
    assert stale["sizingAllowed"] is False
    assert ctx.dollar_sizing_verdict(stale)["verdict"] == ctx.SIZING_NOT_AUTHORISED


def test_no_credential_is_not_a_failure_but_is_not_a_refresh_either(clock):
    """Forks and the window before the PAT exists. The slate must still
    fetch; dollar sizing must still refuse."""
    github = FakeGitHub()
    outcome, run_id = run_refresh(github, clock, token="")
    assert outcome == refresh_mod.OUTCOME_NO_CREDENTIAL
    assert run_id is None
    assert github.calls == [], "it contacted the router without a credential"
    assert outcome not in refresh_mod.SUCCESSFUL_OUTCOMES


def test_the_refresh_script_never_fails_the_workflow(monkeypatch, tmp_path):
    """A slate fetch must not be lost because a balance could not be
    read. Exit 0 on every outcome, including no credential at all."""
    monkeypatch.setenv(refresh_mod.TOKEN_ENV, "")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    assert refresh_mod.main([]) == 0
    written = (tmp_path / "out.txt").read_text()
    assert "outcome=NO_CREDENTIAL" in written
    assert "refreshed=false" in written


# ── 3/4/5. a reading that is not usable is refused ────────────────────

def test_a_reading_older_than_the_window_is_rejected_as_stale():
    context = ctx.context_from_secret(sealed(minutes_ago=31), now=NOW)
    assert context["status"] == ctx.STATUS_STALE
    assert context["sizingAllowed"] is False
    assert context["maxAgeMinutes"] == ctx.DEFAULT_MAX_AGE_MINUTES == 30


def test_the_wrong_value_type_is_rejected():
    context = ctx.context_from_secret(
        sealed(valueType="KALSHI_PORTFOLIO_VALUE"), now=NOW)
    assert context["sizingAllowed"] is False
    assert context["status"] != ctx.STATUS_FRESH


def test_the_wrong_source_is_rejected():
    context = ctx.context_from_secret(
        sealed(source="user_reported_balance"), now=NOW)
    assert context["sizingAllowed"] is False
    assert context["status"] != ctx.STATUS_FRESH


# ── 6. the amount reaches nothing public ──────────────────────────────

def test_the_amount_reaches_no_public_surface(clock, tmp_path, monkeypatch):
    """git diff, logs, job summaries and outputs. The refresh job never
    holds the number at all -- it holds a dispatch token -- so the check
    is that nothing it writes, and nothing the runtime commits, contains
    it."""
    log = tmp_path / "step.log"
    with open(log, "w") as handle:
        github = FakeGitHub()
        run_refresh(github, clock, out=handle)
    printed = log.read_text()
    assert str(AMOUNT) not in printed
    assert "1234" not in printed
    # Digits at all would be suspicious in this step's output, so pin what
    # IS allowed to be numeric rather than waving at it. The repository
    # name and the workflow budget are not readings.
    without_names = printed.replace(refresh_mod.ROUTER_REPO, "<router>")
    assert set(re.findall(r"\d[\d.,]*", without_names)) <= {"999", "420"}, printed

    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out.txt"))
    refresh_mod.write_outputs(refresh_mod.OUTCOME_REFRESHED, 999)
    assert str(AMOUNT) not in (tmp_path / "out.txt").read_text()

    # The committed runtime manifest, built from the redacted card block.
    private = ctx.context_from_secret(sealed(minutes_ago=1), now=NOW)
    manifest_block = {key: ctx.redacted(private).get(key) for key in (
        "status", "source", "valueType", "observedAt", "ageMinutes", "maxAgeMinutes",
        "currency", "sizingAllowed", "numericBankrollAvailable", "consumerSizingVerdict",
        "unavailableReason")}
    serialised = json.dumps(manifest_block)
    assert str(AMOUNT) not in serialised
    assert "bankroll" not in manifest_block, "the amount key itself must not be here"
    assert "1234" not in serialised

    # And the script's own source carries no balance-reading capability.
    source = open(SCRIPT_PATH).read()
    assert "portfolio/balance" not in source


def test_the_runtime_manifest_field_set_cannot_grow_to_include_an_amount():
    """`lib/handicap_runtime.py` copies an allowlist. If someone adds the
    amount to the card's bankroll block, the manifest must still not
    carry it."""
    source = open(os.path.join(ROOT, "lib", "handicap_runtime.py")).read()
    block = source[source.index("def build_manifest"):]
    bankroll_region = block[block.index("bankroll"):block.index("bankroll") + 2000]
    assert '"bankroll":' not in bankroll_region.replace('"bankroll": {', "")


# ── 7/8. both slate entry points get the protection ───────────────────

def test_the_fetch_job_cannot_run_before_the_refresh_job():
    spec = workflow()
    assert spec["jobs"]["fetch"]["needs"] == "refresh_bankroll"


@pytest.mark.parametrize("trigger", ["workflow_dispatch", "schedule", "push"])
def test_every_slate_entry_point_is_covered(trigger):
    """A manual run, a scheduled run and the `.fetch-trigger` push all
    enter through the same workflow, and the refresh job is not gated on
    the event -- so all three get a fresh reading. The user never has to
    run the publisher by hand."""
    spec = workflow()
    assert trigger in (spec.get(True) or spec.get("on"))
    refresh_job = spec["jobs"]["refresh_bankroll"]
    assert "if" not in refresh_job, (
        "the refresh job is event-gated, so some slate runs would build against a "
        "reading nobody refreshed")
    for step in refresh_job["steps"]:
        assert "if" not in step, f"step {step.get('name')!r} is conditional"


def test_a_refresh_failure_never_skips_the_slate_fetch():
    """`needs:` alone would SKIP `fetch` if the refresh job failed. The
    slate is the more important of the two and must survive."""
    spec = workflow()
    assert spec["jobs"]["fetch"]["if"].strip() == "${{ !cancelled() }}"


# ── 9. the dispatch cannot loop or storm ──────────────────────────────

def test_exactly_one_dispatch_per_invocation(clock):
    github = FakeGitHub(appear_after=2, finish_after=4)
    outcome, _run = run_refresh(github, clock)
    assert outcome == refresh_mod.OUTCOME_REFRESHED
    assert len(github.dispatches) == 1, github.dispatches


def test_a_run_already_in_flight_is_adopted_rather_than_duplicated(clock):
    """IDEMPOTENCY, and not merely politeness: the router's publisher
    declares `concurrency: publish-bankroll` with
    `cancel-in-progress: true`, so a second dispatch would CANCEL the run
    this one is waiting on -- and a cancelled publisher leaves the secret
    exactly as stale as it was."""
    github = FakeGitHub(runs=[{
        "id": 555, "event": "schedule", "status": "in_progress",
        "created_at": iso(NOW - timedelta(minutes=1)), "conclusion": None,
    }])
    outcome, run_id = run_refresh(github, clock)
    assert outcome == refresh_mod.OUTCOME_ADOPTED
    assert run_id == 555
    assert github.dispatches == [], "it queued a second reading over a live one"


def test_the_wait_is_bounded(clock):
    """A publisher that never finishes must not hold the slate forever."""
    github = FakeGitHub(finish_after=10 ** 6)
    outcome, run_id = run_refresh(github, clock, max_wait_seconds=60)
    assert outcome == refresh_mod.OUTCOME_TIMED_OUT
    assert run_id == 999
    state, _now, _sleep = clock
    assert state["now"] <= NOW + timedelta(seconds=60 + 30)


def test_the_call_graph_is_a_single_directed_edge():
    """edge-finder-api -> the router's publisher, and nothing else. One
    edge cannot cycle. The other half of this -- that the publisher
    dispatches nothing back -- is pinned in the router's own suite,
    because that file lives there."""
    source = open(SCRIPT_PATH).read()
    targets = set(re.findall(r"/repos/\{([A-Za-z0-9_]+)\}/actions", source))
    assert targets == {"ROUTER_REPO"}, targets
    assert refresh_mod.ROUTER_REPO == "chmoses98/kalshi-bet-router"
    assert refresh_mod.PUBLISHER_WORKFLOW == "publish-bankroll.yml"
    posts = re.findall(r'api\(\s*"(\w+)"', source)
    assert posts.count("POST") == 1, f"more than one write call: {posts}"


def test_a_refused_dispatch_is_reported_and_not_retried(clock):
    github = FakeGitHub(dispatch_status=403)
    outcome, run_id = run_refresh(github, clock)
    assert outcome == refresh_mod.OUTCOME_DISPATCH_REFUSED
    assert run_id is None
    assert len(github.dispatches) == 1


# ── 10. no trading capability is introduced ───────────────────────────

def test_the_coupling_introduces_no_kalshi_capability_in_this_repository():
    """The router stays the only component holding a Kalshi credential.
    This job holds a dispatch token, cannot read a secret and cannot
    push."""
    source = open(SCRIPT_PATH).read()
    for forbidden in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY", "trading.kalshi",
                      "/portfolio/orders", "create_order", "cancel_order"):
        assert forbidden not in source, forbidden

    job = workflow()["jobs"]["refresh_bankroll"]
    assert job["permissions"] == {"contents": "read"}, (
        "the refresh job needs read for checkout and nothing more")
    env_names = set()
    for step in job["steps"]:
        env_names |= set((step.get("env") or {}).keys())
    assert env_names == {refresh_mod.TOKEN_ENV}, env_names
    assert not any("KALSHI" in name and name != refresh_mod.TOKEN_ENV
                   for name in env_names)


def test_the_refresh_job_cannot_write_to_this_repository():
    """It reads the checkout and dispatches. It may not commit, and the
    checkout credential is not left on disk for a later step to pick up."""
    job = workflow()["jobs"]["refresh_bankroll"]
    assert job["permissions"] == {"contents": "read"}
    assert "write" not in json.dumps(job["permissions"])
    checkout = job["steps"][0]
    assert checkout["with"]["persist-credentials"] is False


# ── 11. a late cron can never look fresh ──────────────────────────────

def test_a_scheduler_delay_cannot_make_a_stale_reading_look_fresh():
    """THE 2026-09-19 SHAPE. The publisher SUCCEEDED at 22:19; the card
    built at 23:59. Nothing about the success makes the reading fresh --
    only its own observedAt does, and it is 100 minutes old."""
    published_at = datetime(2026, 9, 19, 22, 19, 0, tzinfo=timezone.utc)
    built_at = datetime(2026, 9, 19, 23, 59, 12, tzinfo=timezone.utc)
    payload = sealed(observedAt=iso(published_at))
    context = ctx.context_from_secret(payload, now=built_at)
    assert context["status"] == ctx.STATUS_STALE
    assert context["ageMinutes"] == pytest.approx(100.2, abs=0.5)
    assert context["sizingAllowed"] is False
    assert ctx.dollar_sizing_verdict(context)["verdict"] == ctx.SIZING_NOT_AUTHORISED


def test_a_successful_refresh_outcome_is_not_itself_evidence_of_freshness(clock):
    """The script reporting REFRESHED must never be wired up as a
    substitute for the age check. Proof: a REFRESHED outcome alongside an
    old reading still refuses to size."""
    github = FakeGitHub()
    outcome, _run = run_refresh(github, clock)
    assert outcome in refresh_mod.SUCCESSFUL_OUTCOMES

    context = ctx.context_from_secret(sealed(minutes_ago=45), now=NOW)
    assert context["sizingAllowed"] is False, (
        "freshness was taken from the refresh outcome instead of observedAt")


def test_the_publisher_cron_is_no_longer_the_only_correctness_mechanism():
    """The cron may stay as a backup. What must be true is that a slate
    build no longer DEPENDS on it having fired recently."""
    spec = workflow()
    assert "refresh_bankroll" in spec["jobs"]
    assert spec["jobs"]["fetch"]["needs"] == "refresh_bankroll"
