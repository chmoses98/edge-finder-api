"""
HTTP layer for the MRV collector.  Every request produces a FetchResult
carrying its OWN timestamps: requestedAt (just before the socket call),
respondedAt (after the body is read) and sourceDate (the HTTP Date header,
second precision, the only exchange-side clock the public API exposes).
Callers store these per market/book/odds row; a cycle-level timestamp is
never a substitute.

The fetcher is injectable: tests pass a fake `transport(url) -> (status,
headers, body_bytes)`; production uses urllib.  Adaptive throttling backs
off on HTTP 429 and decays on success (measured in ALPHA-0002: a burst of
499 calls drew 90 429s; ~7 req/s sustained drew none).
"""
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FetchResult(object):
    __slots__ = ("url", "requestedAt", "respondedAt", "sourceDate", "status", "json", "error",
                 "elapsedMs", "attempts", "headers", "bytes")

    def __init__(self, url):
        self.url = url
        self.requestedAt = None
        self.respondedAt = None
        self.sourceDate = None
        self.status = None
        self.json = None
        self.error = None
        self.elapsedMs = None
        self.attempts = 0
        self.headers = {}
        self.bytes = 0

    @property
    def ok(self):
        return self.error is None and self.json is not None

    def stamp(self):
        """The timestamp triple every stored row carries."""
        return {"requestedAt": self.requestedAt, "respondedAt": self.respondedAt, "sourceDate": self.sourceDate}


def _urllib_transport(url, timeout=30):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "edgelab-mrv-collector"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, body
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:
            pass
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, body


class Fetcher(object):
    def __init__(self, transport=None, *, base_sleep=0.13, min_sleep=0.10, max_sleep=2.5, retries=3, clock=None, sleeper=None):
        self.transport = transport or _urllib_transport
        self.sleep_s = base_sleep
        self.min_sleep = min_sleep
        self.max_sleep = max_sleep
        self.retries = retries
        self.clock = clock or utc_now_iso
        self.sleeper = sleeper or time.sleep
        self.stats = {"requests": 0, "http429": 0, "errors": 0, "bytes": 0, "retries": 0}

    def get(self, url, *, retryable=(429, 500, 502, 503, 504), max_attempts=None):
        """max_attempts overrides the instance retry count (the metered Odds API leg uses 1 so a retry can never spend credits the budget guard did not approve)."""
        r = FetchResult(url)
        attempts_allowed = max_attempts or self.retries
        for attempt in range(attempts_allowed):
            r.attempts = attempt + 1
            if attempt:
                self.stats["retries"] += 1
            self.sleeper(self.sleep_s)
            self.stats["requests"] += 1
            r.requestedAt = self.clock()
            t0 = time.time()
            try:
                status, headers, body = self.transport(url)
            except Exception as exc:  # network-level failure
                r.respondedAt = self.clock()
                r.error = "transport:%s" % (exc.__class__.__name__)
                r.elapsedMs = int((time.time() - t0) * 1000)
                if attempt == attempts_allowed - 1:
                    self.stats["errors"] += 1
                    return r
                self.sleeper(0.5 * (attempt + 1))
                continue
            r.respondedAt = self.clock()
            r.elapsedMs = int((time.time() - t0) * 1000)
            r.status = status
            r.headers = headers or {}
            r.sourceDate = r.headers.get("date")
            r.bytes = len(body or b"")
            self.stats["bytes"] += r.bytes
            if status == 429:
                self.stats["http429"] += 1
                self.sleep_s = min(self.max_sleep, self.sleep_s * 2.0 + 0.05)
                r.error = "HTTP 429"
                self.sleeper(1.0 + attempt)
                continue
            if status in retryable and status != 429:
                r.error = "HTTP %d" % status
                continue
            if status != 200:
                r.error = "HTTP %d" % status
                self.stats["errors"] += 1
                return r
            try:
                r.json = json.loads(body.decode("utf-8") if isinstance(body, bytes) else body)
            except Exception:
                r.error = "malformed_json"
                self.stats["errors"] += 1
                return r
            r.error = None
            self.sleep_s = max(self.min_sleep, self.sleep_s * 0.97)
            return r
        self.stats["errors"] += 1
        if r.error is None:
            r.error = "retries_exhausted"
        return r
