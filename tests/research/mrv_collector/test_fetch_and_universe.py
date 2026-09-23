import pytest

from lib.edgelab.research.mrv_collector import fetch as F, universe as UV
from tests.research.mrv_collector.fake_world import standard_world, FakeWorld


def _fetcher(world):
    return F.Fetcher(transport=world.transport, clock=world.clock, sleeper=lambda s: None, base_sleep=0.0, min_sleep=0.0)


def test_every_fetch_carries_its_own_timestamps_and_source_date():
    w = standard_world()
    f = _fetcher(w)
    r = f.get(UV.KALSHI + "/series?limit=1000")
    assert r.ok and r.requestedAt and r.respondedAt and r.requestedAt < r.respondedAt
    assert r.sourceDate == "Tue, 22 Sep 2026 20:00:00 GMT"
    r2 = f.get(UV.KALSHI + "/series?limit=1000")
    assert r2.requestedAt > r.respondedAt          # not a cycle-level timestamp


def test_429_backs_off_then_recovers_and_is_counted():
    w = standard_world(rate_limit_first_n=2)
    f = _fetcher(w)
    r = f.get(UV.KALSHI + "/series?limit=1000")
    assert r.ok and r.attempts == 3 and f.stats["http429"] == 2 and f.sleep_s > 0.0


def test_pagination_to_exhaustion_records_every_page():
    w = standard_world(page_size=2)
    f = _fetcher(w)
    items, pg = UV.fetch_open_markets(f, "KXMLBTOTAL")
    assert len(items) == 5 and pg["complete"] and pg["pages"] == 3 and pg["truncationReason"] is None
    assert [a["cursorBefore"] for a in pg["attempts"]] == [None, "2", "4"]
    assert all(it["_page"]["respondedAt"] for it in items)


def test_failed_page_is_reported_not_hidden():
    w = standard_world(page_size=2, fail_urls=["cursor=2"])
    f = _fetcher(w)
    items, pg = UV.fetch_open_markets(f, "KXMLBTOTAL")
    assert len(items) == 2 and not pg["complete"] and pg["truncationReason"] == "PAGE_FETCH_FAILED"
    assert pg["finalCursor"] == "2"


def test_safety_cap_is_explicit_truncation_evidence():
    w = standard_world(page_size=1)
    f = _fetcher(w)
    items, pg = UV.page_to_exhaustion(f, UV.KALSHI + "/markets?series_ticker=KXMLBTOTAL&status=open&limit=1000", "markets", page_cap=2)
    assert len(items) == 2 and not pg["complete"] and pg["truncationReason"] == "SAFETY_PAGE_CAP"


def test_series_classification_never_silently_drops_an_mlb_series():
    p = UV.DEFAULT_POLICY
    assert UV.classify_series("KXMLBTOTAL", p) == "PER_GAME_BOOK"
    assert UV.classify_series("KXMLBHIT", p) == "PROP_QUOTE"
    assert UV.classify_series("KXMLBWS", p) == "EXCLUDED"
    assert UV.classify_series("KXMLBMYSTERY", p) == "UNCLASSIFIED_MLB"
