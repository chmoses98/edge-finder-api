#!/usr/bin/env python3
"""
tests/test_kalshi_capture_completeness_contract.py
==================================================
PHASES C-G: the capture layer must be able to PROVE what it retrieved.

    DISCOVERED == ARCHIVED + EXPLICITLY EXCLUDED + EXPLICITLY FAILED

Before this, none of the three right-hand terms was knowable from a
snapshot. A 429 broke the page loop and the live cursor was discarded
with no record; maxPages=10 ceilinged a series at 2,000 markets with no
flag; the broad pass stopped at 500 and reported 500 either way; the date
filter counted only survivors; and a handler error produced a body with
no `markets` key, which the workflow's `markets_count > 0` gate then
declined to archive.

Measured cost over 21 days: 9 of 106 captures partial, every one an HTTP
429, >=7,356 markets lost, and all 227 ingest runs reporting success.
Because the 17 series are fetched sequentially and the loop broke on the
first rate limit, the loss landed on whatever came last, every time --
KXMLBHRR x6, KXMLBRBI x6, KXMLBSB x5, KXMLBTB x2. Biased research, not
random noise.

These drive the ACTUAL SHIPPED functions through node, with fetch/sleep/
clock injected. If these assertions and api/kalshisearch.js disagree, the
file is what deploys, so the file is what is tested.
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINT = os.path.join(ROOT, "api", "kalshisearch.js")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is not available in this environment",
)


def _run(driver_body):
    """Execute a node module that imports the real endpoint and prints JSON."""
    driver = "import * as K from %s;\n%s" % (json.dumps(ENDPOINT), driver_body)
    proc = subprocess.run(["node", "--input-type=module", "-e", driver],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, "node failed: %s" % proc.stderr[-3000:]
    return json.loads(proc.stdout)


# A scripted fetch: each entry is one HTTP response, consumed in order.
_HARNESS = """
function makeFetch(script) {
  let i = 0;
  return async (url) => {
    const step = script[Math.min(i, script.length - 1)];
    i += 1;
    if (step.throw) throw new Error(step.throw);
    return {
      ok: step.status >= 200 && step.status < 300,
      status: step.status,
      headers: { get: (h) => (step.headers || {})[h.toLowerCase()] ?? null },
      json: async () => step.body || {},
    };
  };
}
let slept = [];
const sleepImpl = async (ms) => { slept.push(ms); };
"""


def _paginate(script, **opts):
    body = _HARNESS + """
const script = %s;
const opts = Object.assign(
  { fetchImpl: makeFetch(script), sleepImpl, nowImpl: () => 0 }, %s);
const out = await K.fetchPaginated('https://x/markets?limit=200', 'markets', opts);
process.stdout.write(JSON.stringify({ ...out, slept }));
""" % (json.dumps(script), json.dumps(opts))
    return _run(body)


def _page(*tickers, cursor="", status=200, headers=None, throw=None):
    step = {"status": status}
    if throw:
        step["throw"] = throw
    if headers:
        step["headers"] = headers
    step["body"] = {"markets": [{"ticker": t} for t in tickers], "cursor": cursor}
    return step


# ── a live cursor can never be discarded silently ───────────────────────────

def test_exhausting_the_cursor_is_the_only_way_to_be_complete():
    out = _paginate([_page("A", cursor="c1"), _page("B", cursor="")])
    assert out["pagination"]["complete"] is True
    assert out["pagination"]["truncationReason"] is None
    assert out["pagination"]["finalCursor"] is None
    assert [r["ticker"] for r in out["records"]] == ["A", "B"]


def test_a_live_cursor_at_the_page_cap_is_a_truncation_not_a_stop():
    """THE defect maxPages hid: page 10 returned a cursor and it was dropped."""
    out = _paginate([_page("A", cursor="still-live")], maxPages=1)
    assert out["pagination"]["complete"] is False
    assert out["pagination"]["truncationReason"] == "PAGE_CAP_REACHED_WITH_LIVE_CURSOR"
    assert out["pagination"]["finalCursor"] == "still-live"


def test_the_cursor_before_and_after_every_page_is_recorded():
    out = _paginate([_page("A", cursor="c1"), _page("B", cursor="c2"),
                     _page("C", cursor="")])
    pages = out["pagination"]["pages"]
    assert [p["cursorBefore"] for p in pages] == [None, "c1", "c2"]
    assert [p["cursorAfter"] for p in pages] == ["c1", "c2", None]


def test_a_cursor_that_returns_no_items_cannot_advance_forever():
    out = _paginate([_page("A", cursor="c1"), _page(cursor="c1")])
    assert out["pagination"]["pagesFetched"] == 2
    assert len(out["records"]) == 1


# ── a 429 is retried, and when it wins it is recorded, never silent ─────────

def test_a_429_is_retried_with_backoff_and_then_succeeds():
    out = _paginate([{"status": 429}, _page("A", cursor="")])
    assert out["pagination"]["complete"] is True
    assert out["pagination"]["retriesAttempted"] == 1
    assert out["slept"] == [400]
    assert [r["ticker"] for r in out["records"]] == ["A"]


def test_retry_after_is_honoured_over_the_default_backoff():
    out = _paginate([{"status": 429, "headers": {"retry-after": "2"}},
                     _page("A", cursor="")])
    assert out["slept"] == [2000]


def test_backoff_is_deterministic_and_bounded():
    """No jitter: one sequential invocation has no herd to spread, and an
    auditable capture is worth more than a randomised one."""
    out = _run(_HARNESS + """
process.stdout.write(JSON.stringify({
  ladder: [0,1,2,3,4].map(a => K.backoffDelayMs(a, null)),
  cap: K.MAX_BACKOFF_MS,
}));""")
    assert out["ladder"] == [400, 800, 1600, 3200, 4000]
    assert max(out["ladder"]) <= out["cap"]


def test_exhausted_retries_leave_the_series_explicitly_incomplete():
    """The production case: this used to `break` and return a short list that
    looked exactly like a complete one."""
    out = _paginate([{"status": 429}])
    assert out["pagination"]["complete"] is False
    assert out["pagination"]["truncationReason"] == "RETRIES_EXHAUSTED"
    assert out["pagination"]["retriesAttempted"] == 3
    assert out["records"] == []


def test_a_429_partway_through_keeps_the_pages_it_did_get_and_says_it_is_short():
    out = _paginate([_page("A", cursor="c1"), {"status": 429}])
    assert [r["ticker"] for r in out["records"]] == ["A"]
    assert out["pagination"]["complete"] is False
    assert out["pagination"]["finalCursor"] == "c1"


def test_a_non_retryable_status_is_not_retried():
    out = _paginate([{"status": 404}])
    assert out["pagination"]["retriesAttempted"] == 0
    assert out["pagination"]["truncationReason"] == "TRANSPORT_ERROR"
    assert out["slept"] == []


def test_a_transport_error_is_retried_like_a_5xx():
    """The request never reached a server that said no."""
    out = _paginate([{"status": 0, "throw": "ECONNRESET"}, _page("A", cursor="")])
    assert out["pagination"]["complete"] is True
    assert out["pagination"]["retriesAttempted"] == 1


def test_every_page_attempt_records_its_http_status():
    out = _paginate([{"status": 429}, _page("A", cursor="")])
    assert out["pagination"]["pages"][0]["httpStatus"] in (429, 200)


# ── the deadline protects the artifact, never hides a loss ──────────────────

def test_the_deadline_stops_the_loop_rather_than_risking_a_timeout():
    """A truthful PARTIAL artifact beats being killed mid-flight with none."""
    out = _paginate([_page("A", cursor="c1"), _page("B", cursor="c2")],
                    deadlineAt=-1)
    assert out["pagination"]["complete"] is False
    assert out["pagination"]["truncationReason"] == "DEADLINE_EXCEEDED"


def test_a_backoff_that_would_overrun_the_deadline_is_not_slept():
    out = _run(_HARNESS + """
const out = await K.fetchPaginated('https://x/markets?limit=200', 'markets', {
  fetchImpl: makeFetch([{ status: 429 }]), sleepImpl,
  nowImpl: () => 0, deadlineAt: 100,
});
process.stdout.write(JSON.stringify({ ...out, slept }));""")
    assert out["slept"] == []
    assert out["pagination"]["truncationReason"] == "DEADLINE_EXCEEDED"
    assert out["pagination"]["complete"] is False


# ── one family's rate limit cannot erase the families behind it ─────────────

def test_fetch_order_is_rotated_deterministically():
    out = _run("""
const S = ['a','b','c','d','e'];
process.stdout.write(JSON.stringify({
  seed2: K.rotateSeries(S, 2),
  same: K.rotateSeries(S, 2),
  seed3: K.rotateSeries(S, 3),
  wraps: K.rotateSeries(S, 7),
  negative: K.rotateSeries(S, -1),
  empty: K.rotateSeries([], 3),
}));""")
    assert out["seed2"] == ["c", "d", "e", "a", "b"]
    assert out["same"] == out["seed2"], "rotation must be reproducible"
    assert out["seed3"] != out["seed2"]
    assert sorted(out["wraps"]) == ["a", "b", "c", "d", "e"], "no series may be lost"
    assert sorted(out["negative"]) == ["a", "b", "c", "d", "e"]
    assert out["empty"] == []


def test_rotation_moves_the_last_slot_across_the_day():
    """The whole point: the same families must not always absorb the loss."""
    out = _run("""
const S = ['KXMLBGAME','KXMLBTOTAL','KXMLBHIT','KXMLBTB','KXMLBHRR',
           'KXMLBRBI','KXMLBSB'];
const last = [];
for (let h = 0; h < 24; h++) last.push(K.rotateSeries(S, K.rotationSeed('26SEP20', h)).at(-1));
process.stdout.write(JSON.stringify({ distinct: [...new Set(last)].length, last }));""")
    assert out["distinct"] >= 7, "every family must take a turn in the exposed slot"


def test_the_rotation_seed_is_stable_for_a_given_date_and_hour():
    out = _run("""process.stdout.write(JSON.stringify({
  a: K.rotationSeed('26SEP20', 18), b: K.rotationSeed('26SEP20', 18),
  c: K.rotationSeed('26SEP21', 18),
}));""")
    assert out["a"] == out["b"]
    assert out["a"] != out["c"]


# ── the capture verdict ─────────────────────────────────────────────────────

def _summary(paginations, archived):
    return _run("""process.stdout.write(JSON.stringify(
  K.summarizeCapture(%s, { marketsArchived: %d })));"""
                % (json.dumps(paginations), archived))


def _pg(series, complete=True, scope="series", reason=None):
    return {"scope": scope, "series": series, "complete": complete,
            "truncationReason": reason, "retriesAttempted": 0, "totalBackoffMs": 0,
            "recordsReceived": 0}


def test_a_capture_is_complete_only_when_every_series_exhausted():
    out = _summary([_pg("A"), _pg("B")], 100)
    assert out["captureStatus"] == "COMPLETE"
    assert out["captureComplete"] is True
    assert out["seriesIncomplete"] == []


def test_one_incomplete_series_makes_the_whole_capture_partial():
    out = _summary([_pg("A"), _pg("KXMLBSB", complete=False, reason="RETRIES_EXHAUSTED")], 100)
    assert out["captureStatus"] == "PARTIAL"
    assert out["captureComplete"] is False
    assert out["seriesIncomplete"] == ["KXMLBSB"]
    assert out["truncationReasons"] == ["RETRIES_EXHAUSTED"]


def test_an_incomplete_broad_pass_also_blocks_complete():
    out = _summary([_pg("A"), _pg(None, complete=False, scope="discovery",
                                  reason="ENTRY_CAP_REACHED")], 100)
    assert out["captureStatus"] == "PARTIAL"
    assert "discovery" in out["incompleteScopes"]


def test_retrieving_nothing_at_all_is_failed_not_partial():
    out = _summary([_pg("A", complete=False, reason="TRANSPORT_ERROR")], 0)
    assert out["captureStatus"] == "FAILED"


def test_a_genuinely_empty_but_complete_capture_is_not_failed():
    """An off-day slate is not a defect."""
    out = _summary([_pg("A"), _pg("B")], 0)
    assert out["captureStatus"] == "COMPLETE"


# ── safety caps are truncations, never silent stops ─────────────────────────

def test_the_safety_caps_are_large_enough_to_be_safety_not_budget():
    out = _run("""process.stdout.write(JSON.stringify({
  pages: K.MAX_PAGES_SAFETY, broad: K.BROAD_MAX_PAGES_SAFETY,
  entries: K.BROAD_DISCOVERY_ENTRY_CAP, version: K.CAPTURE_CONTRACT_VERSION,
}));""")
    # Observed peak was 1,268 markets in one series against the old ceiling of
    # 200*10 = 2,000. 200*60 = 12,000 is headroom, not a new silent limit.
    assert out["pages"] * 200 >= 12000
    assert out["entries"] > 500
    assert out["version"] == "kalshi_capture_v4"


# ── PHASE G: an attempt that fails must still leave durable evidence ────────

class TestFailedCaptureLeavesEvidence:
    """Absence of an artifact made a failed capture indistinguishable from a
    capture that never ran, which is why the 21-day audit could only ever
    report a LOWER bound on what was lost."""

    WORKFLOW = os.path.join(ROOT, ".github", "workflows",
                            "capture-snapshots-scheduled.yml")

    def _steps(self):
        yaml = pytest.importorskip("yaml")
        with open(self.WORKFLOW) as fh:
            data = yaml.safe_load(fh)
        return list(data["jobs"].values())[0]["steps"]

    def test_archiving_is_no_longer_gated_on_having_found_markets(self):
        """`markets_count > 0` was half the defect: the capture worth
        recording most is the one that retrieved nothing."""
        archive = next(s for s in self._steps() if s.get("id") == "archive")
        assert "markets_count" not in str(archive.get("if") or "")

    def _fetch_code(self):
        """Executable lines only -- the comments explaining the old defect
        naturally quote it, and matching those would test nothing."""
        fetch = next(s for s in self._steps() if s.get("id") == "fetch_markets")
        return "\n".join(line for line in fetch["run"].splitlines()
                         if not line.lstrip().startswith("#"))

    def test_the_fetch_step_no_longer_aborts_the_job_on_a_failed_fetch(self):
        assert "exit 1" not in self._fetch_code(), (
            "aborting leaves no artifact; the attempt must be recorded")

    def test_a_failed_fetch_synthesises_a_well_formed_failed_snapshot(self):
        fetch = next(s for s in self._steps() if s.get("id") == "fetch_markets")
        assert '"captureStatus": "FAILED"' in fetch["run"]
        assert '"markets": []' in fetch["run"]

    def test_the_error_body_is_preserved_rather_than_discarded(self):
        assert "captureErrorBody" in self._fetch_code()
        assert "curl -sf" not in self._fetch_code(), (
            "-f discards the error body, which is the only evidence of why")

    def test_the_kept_forever_dated_snapshot_is_not_clobbered_by_a_failed_capture(self):
        """Archiving unconditionally must not destroy evidence in the name of
        recording it. lib/snapshot_retention.py keeps kalshi_search_<date>.json
        FOREVER as the per-slate-date reference other consumers reconcile
        against; letting a capture that retrieved nothing overwrite a good one
        would turn a transient fetch failure into permanent loss on the one
        file that is never pruned."""
        archive = next(s for s in self._steps() if s.get("id") == "archive")
        run = archive["run"]
        assert 'cp data/kalshi_search_live.json "$SNAP_TS"' in run, (
            "the timestamped attempt record must always be written")
        dated = run[run.index('cp data/kalshi_search_live.json "$SNAP_DATE"'):]
        guard = run[:run.index('cp data/kalshi_search_live.json "$SNAP_DATE"')]
        assert "markets_count" in guard.rsplit("\n", 6)[-1] or "-gt 0" in guard, (
            "advancing the kept-forever dated copy must be conditional on "
            "having actually retrieved markets")

    def test_the_commit_is_gated_on_the_attempt_record_not_the_dated_copy(self):
        """Gating on the dated copy would write a failed attempt to the
        runner's disk and then discard it when the runner is reclaimed -- no
        artifact, which is the exact defect PHASE G exists to close."""
        commit = next(s for s in self._steps()
                      if (s.get("name") or "").startswith("Commit snapshot"))
        condition = str(commit.get("if"))
        assert "snapshot_ts_path" in condition
        assert "snapshot_date_path" not in condition

    def test_the_handler_returns_a_snapshot_shape_on_error_not_a_bare_error(self):
        with open(ENDPOINT) as fh:
            src = fh.read()
        tail = src[src.rindex("} catch (error) {"):]
        assert "markets:       []" in tail, "an error body must still carry `markets`"
        assert "CAPTURE_FAILED" in tail
        assert "res.status(500)" not in tail, (
            "a 500 makes curl discard the body and the workflow archive nothing")


# ── the function has the budget its retries need ────────────────────────────

def test_the_capture_function_has_an_explicit_timeout_budget():
    """Retries are only safe if they cannot get the function killed."""
    with open(os.path.join(ROOT, "vercel.json")) as fh:
        config = json.load(fh)
    max_duration = config["functions"]["api/kalshisearch.js"]["maxDuration"]
    deadline = _run("process.stdout.write(JSON.stringify(K.CAPTURE_DEADLINE_MS));")
    assert deadline < max_duration * 1000, (
        "the internal deadline must fire before the platform kills the function")

