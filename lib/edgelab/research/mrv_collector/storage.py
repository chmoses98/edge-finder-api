"""
Append-only, versioned storage for the MRV collector.

Layout under <root> (= STORAGE_RELATIVE_ROOT on the research branch):
  attempts/<date>.jsonl            one row per cycle ATTEMPT, written BEFORE the cycle runs
  runs/<date>/<runId>.json         one immutable manifest per completed cycle (status COMPLETE/PARTIAL/FAILED)
  kalshi_quotes/<date>.jsonl.gz    full quote rows for tickers whose quote fingerprint changed
  kalshi_books/<date>.jsonl.gz     full order-book rows for tickers whose book fingerprint changed
  kalshi_crosssection/<date>.jsonl.gz  one row per cycle: every ticker seen -> {q fp, b fp, per-fetch respondedAt}
  kalshi_trades/<date>.jsonl.gz    trade tape rows (deduplicated by trade_id within the day)
  sportsbook_odds/<date>.jsonl.gz  one row per (event, book, market, outcome)
  sportsbook_joins/<date>.jsonl.gz one row per sportsbook event per cycle: MATCHED / AMBIGUOUS / UNMATCHED
  mlb_state/<date>.jsonl.gz        one row per game per cycle in which its state fingerprint changed (+ transitions)
  state/collector_state.json       fingerprint cache (rebuildable; anchors are verified against partitions)

Semantics:
  * append-only: gzip members are appended, never rewritten; manifests are
    write-once (a second write with the same runId is refused);
  * retry immutability: a failed or partial cycle keeps its attempt row and
    its manifest; a retry is a NEW runId;
  * deduplication: quote/book rows are change-suppressed by content
    fingerprint, but ONLY when the anchor row (same ticker, same fp) is
    proven present in a persisted partition of the last ANCHOR_SCAN_DAYS;
    otherwise the full row is written again (self-healing, no dangling refs);
  * the cross-section row makes every cycle reconstructible: for each ticker
    it names the fp of the quote/book in force and the fetch time.
"""
import gzip
import hashlib
import json
import os
from datetime import datetime, timedelta

from lib.edgelab.research.mrv_collector import COLLECTOR_ID, COLLECTOR_VERSION, SCHEMA_VERSION

ANCHOR_SCAN_DAYS = 3


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()[:20]


def version_stamp(run_id):
    return {"collectorId": COLLECTOR_ID, "collectorVersion": COLLECTOR_VERSION, "schemaVersion": SCHEMA_VERSION, "runId": run_id}


class Store(object):
    def __init__(self, root):
        self.root = root

    # ---- paths
    def part(self, kind, date):
        return os.path.join(self.root, kind, date + ".jsonl.gz")

    def manifest_path(self, date, run_id):
        return os.path.join(self.root, "runs", date, run_id + ".json")

    def attempts_path(self, date):
        return os.path.join(self.root, "attempts", date + ".jsonl")

    def state_path(self):
        return os.path.join(self.root, "state", "collector_state.json")

    # ---- writers
    def append_gz(self, kind, date, rows):
        if not rows:
            return 0
        path = self.part(kind, date)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with gzip.open(path, "at") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True, default=str) + "\n")
        return len(rows)

    def append_attempt(self, date, row):
        path = self.attempts_path(date)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")

    def write_manifest(self, date, run_id, manifest):
        path = self.manifest_path(date, run_id)
        if os.path.exists(path):
            raise FileExistsError("manifest already exists for %s (manifests are write-once)" % run_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(manifest, fh, indent=1, sort_keys=True, default=str)
            fh.write("\n")
        os.replace(tmp, path)
        return path

    # ---- readers
    def iter_gz(self, kind, date):
        path = self.part(kind, date)
        if not os.path.exists(path):
            return
        with gzip.open(path, "rt") as fh:
            for line in fh:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue

    def iter_manifests(self, date):
        d = os.path.join(self.root, "runs", date)
        if not os.path.isdir(d):
            return
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn)) as fh:
                    try:
                        yield json.load(fh)
                    except ValueError:
                        continue

    def iter_attempts(self, date):
        path = self.attempts_path(date)
        if not os.path.exists(path):
            return
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue

    def dates_with(self, kind):
        d = os.path.join(self.root, kind)
        if not os.path.isdir(d):
            return []
        return sorted(f.split(".")[0] for f in os.listdir(d) if f.endswith((".jsonl.gz", ".jsonl", ".json")) or os.path.isdir(os.path.join(d, f)))

    # ---- state
    def load_state(self):
        p = self.state_path()
        if os.path.exists(p):
            with open(p) as fh:
                try:
                    return json.load(fh)
                except ValueError:
                    pass
        return {"quoteFp": {}, "bookFp": {}, "stateFp": {}, "lastCycleStartTs": None, "recentTradeIds": []}

    def save_state(self, st):
        p = self.state_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(st, fh, sort_keys=True)
        os.replace(tmp, p)

    # ---- anchors (persisted partitions are the only proof a reference resolves)
    def persisted_anchors(self, date, kind, fp_key="fp", days=ANCHOR_SCAN_DAYS):
        """{(ticker, fp): date} for full rows present in the last `days` partitions of `kind`."""
        out = {}
        d0 = datetime.strptime(date, "%Y-%m-%d")
        for k in range(days):
            day = (d0 - timedelta(days=k)).strftime("%Y-%m-%d")
            for r in self.iter_gz(kind, day):
                t, fp = r.get("marketTicker"), r.get(fp_key)
                if t and fp:
                    out.setdefault((t, fp), day)
        return out

    def previous_states(self, date, days=ANCHOR_SCAN_DAYS):
        """Latest persisted mlb_state row per gamePk over the last `days` partitions."""
        out = {}
        d0 = datetime.strptime(date, "%Y-%m-%d")
        for k in range(days - 1, -1, -1):
            day = (d0 - timedelta(days=k)).strftime("%Y-%m-%d")
            for r in self.iter_gz("mlb_state", day):
                pk = r.get("gamePk")
                if pk is not None:
                    prev = out.get(pk)
                    if prev is None or (r.get("observedAt") or "") >= (prev.get("observedAt") or ""):
                        out[pk] = r
        return out
