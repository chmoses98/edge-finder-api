#!/usr/bin/env python3
"""
tests/test_odds_api_credential_hygiene.py
=========================================
REMEDIATION WAVE 0, section B. Regression coverage proving that accidental
whitespace around an API credential cannot recreate the 2026-09 settlement
outage.

THE ORIGINAL FAILURE
--------------------
The stored ODDS_API_KEY secret carried trailing whitespace. GitHub Actions
interpolates a secret verbatim (masking it in logs, not trimming it), so
clv_update.py received "<key> " and interpolated it straight into a URL.
http.client validates the request target BEFORE opening a socket and raised:

    InvalidURL: URL can't contain control characters.
    '/v4/sports/baseball_mlb/scores?apiKey=*** &daysFrom=2' (found at least ' ')

api_get()'s broad `except Exception` turned that into `return None, None`, so
clv_update.py printed "Auto-settled this run: 0" and exited 0 -- a settlement
run that silently did nothing while reporting success.

Two independent defects, so two independent guards below:
  1. the credential is normalized at its single canonical boundary; and
  2. a malformed request target is FAIL-LOUD rather than silently swallowed.

Fixing only (1) would leave the swallow in place for the next malformed URL;
fixing only (2) would turn a recoverable config typo into a hard outage.
"""

import importlib
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SENTINEL_KEY = "audit-wave0-sentinel-key"

# Every way a credential picks up whitespace in practice: a trailing space from
# a copy-paste, a trailing newline from `echo`/a file, a leading space, a tab,
# and CRLF from a Windows clipboard.
WHITESPACE_VARIANTS = [
    ("trailing space", SENTINEL_KEY + " "),
    ("leading space", " " + SENTINEL_KEY),
    ("trailing newline", SENTINEL_KEY + "\n"),
    ("trailing tab", SENTINEL_KEY + "\t"),
    ("crlf", SENTINEL_KEY + "\r\n"),
    ("both ends", "  " + SENTINEL_KEY + "  "),
]


def _reload_clv_update(monkeypatch, key_value):
    monkeypatch.setenv("ODDS_API_KEY", key_value)
    import clv_update
    return importlib.reload(clv_update)


# ── 1. Normalization at the canonical boundary ───────────────────────────────

@pytest.mark.parametrize("label,raw", WHITESPACE_VARIANTS)
def test_odds_api_key_is_normalized_at_the_boundary(monkeypatch, label, raw):
    mod = _reload_clv_update(monkeypatch, raw)
    assert mod.ODDS_API_KEY == SENTINEL_KEY, (
        "ODDS_API_KEY with %s survived into the module unnormalized (%r)"
        % (label, mod.ODDS_API_KEY))


@pytest.mark.parametrize("label,raw", WHITESPACE_VARIANTS)
def test_normalized_key_produces_a_transmittable_url(monkeypatch, label, raw):
    """
    The end-to-end property that actually matters: a URL built from the
    normalized key must be acceptable to http.client. Asserted against the real
    validator rather than against a regex of our own, so it tracks CPython's
    actual rule rather than our belief about it.
    """
    from http.client import HTTPConnection, InvalidURL
    mod = _reload_clv_update(monkeypatch, raw)
    url = "%s/sports/%s/scores?apiKey=%s&daysFrom=2" % (
        mod.BASE_URL, mod.SPORT, mod.ODDS_API_KEY)
    selector = url.split("the-odds-api.com", 1)[1]
    conn = HTTPConnection("example.invalid")
    try:
        conn.putrequest("GET", selector)
    except InvalidURL as exc:  # pragma: no cover - the regression itself
        pytest.fail("normalized key still yields an untransmittable target (%s): %s"
                    % (label, exc))
    finally:
        conn.close()


def test_whitespace_only_key_still_fails_loudly(monkeypatch):
    """
    Normalization must not become a way to smuggle an EMPTY credential past the
    guard. A whitespace-only secret strips to '' and must stay falsy so
    clv_update.main()'s `if not ODDS_API_KEY: sys.exit(1)` still fires -- this
    is strictly louder than the old behavior, which built a broken URL for
    every request and reported success.
    """
    mod = _reload_clv_update(monkeypatch, "   \n\t ")
    assert mod.ODDS_API_KEY == ""
    assert not mod.ODDS_API_KEY


def test_a_legitimate_key_is_left_exactly_alone(monkeypatch):
    """Normalization must repair accidents, never mutate a valid credential."""
    mod = _reload_clv_update(monkeypatch, SENTINEL_KEY)
    assert mod.ODDS_API_KEY == SENTINEL_KEY


# ── 2. Fail-loud on a malformed request target ───────────────────────────────

def test_a_whitespace_bearing_target_is_rejected_by_cpython_itself(monkeypatch):
    """
    The upstream half of the mechanism, asserted hermetically against CPython's
    real validator: a request target containing whitespace is rejected locally,
    before any socket is opened. This is why the original failure consumed no
    API credits and produced no HTTP status -- and why it can never be
    transient.
    """
    from http.client import HTTPConnection, InvalidURL
    mod = _reload_clv_update(monkeypatch, SENTINEL_KEY)
    selector = "/v4/sports/%s/scores?apiKey=%s &daysFrom=2" % (mod.SPORT, SENTINEL_KEY)
    conn = HTTPConnection("example.invalid")
    try:
        with pytest.raises(InvalidURL):
            conn.putrequest("GET", selector)
    finally:
        conn.close()


def _patch_urlopen_to_raise_invalid_url(monkeypatch, mod):
    """
    Inject the exact exception CPython raises, rather than relying on the real
    urlopen to produce it.

    Deliberate: several other modules in this suite globally replace urlopen
    with a fake response object, and because clv_update.py does
    `from urllib.request import urlopen`, an importlib.reload here can pick up
    that fake and make these tests silently exercise nothing. Injecting the
    exception directly makes the assertions about api_get's OWN handler
    hermetic and order-independent.
    """
    from http.client import InvalidURL

    def _raise(*args, **kwargs):
        raise InvalidURL("URL can't contain control characters. "
                         "'/v4/sports/baseball_mlb/scores?apiKey=%s &daysFrom=2' "
                         "(found at least ' ')" % SENTINEL_KEY)

    monkeypatch.setattr(mod, "urlopen", _raise)


def test_api_get_raises_on_a_malformed_target_instead_of_returning_none(monkeypatch):
    """
    The swallow that hid the outage: api_get's broad `except Exception` turned
    a permanent configuration defect into `return None, None`, so clv_update.py
    reported "Auto-settled this run: 0" and exited 0.
    """
    mod = _reload_clv_update(monkeypatch, SENTINEL_KEY)
    _patch_urlopen_to_raise_invalid_url(monkeypatch, mod)
    with pytest.raises(RuntimeError) as excinfo:
        mod.api_get("%s/sports/%s/scores?apiKey=%s" % (mod.BASE_URL, mod.SPORT, SENTINEL_KEY))
    assert "malformed" in str(excinfo.value).lower()


def test_api_get_failure_message_never_leaks_the_credential(monkeypatch):
    """
    The underlying exception message embeds the credential (it quotes the whole
    request target), so the handler must report the endpoint and the cause
    without ever echoing the value. GitHub's log masking is a backstop, not a
    licence to print secrets -- and it does not mask an exception surfaced
    through a non-Actions channel at all.
    """
    mod = _reload_clv_update(monkeypatch, SENTINEL_KEY)
    _patch_urlopen_to_raise_invalid_url(monkeypatch, mod)
    with pytest.raises(RuntimeError) as excinfo:
        mod.api_get("%s/sports/%s/scores?apiKey=%s" % (mod.BASE_URL, mod.SPORT, SENTINEL_KEY))
    message = str(excinfo.value)
    assert SENTINEL_KEY not in message, "credential leaked into the error message"
    assert "apiKey=" not in message
    assert "/sports/baseball_mlb/scores" in message, (
        "message should still identify the endpoint so the failure is actionable")


def test_network_errors_remain_soft(monkeypatch):
    """
    The fail-loud change must be surgical. A genuine network failure is
    transient and must still return (None, None) so one unreachable endpoint
    cannot abort a settlement run that can still make progress -- only the
    never-transient InvalidURL is escalated.
    """
    mod = _reload_clv_update(monkeypatch, SENTINEL_KEY)

    def _boom(*args, **kwargs):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(mod, "urlopen", _boom)
    assert mod.api_get("%s/sports/%s/scores?apiKey=%s" % (
        mod.BASE_URL, mod.SPORT, SENTINEL_KEY)) == (None, None)


# ── 3. The sibling credential in the same workflow chain ─────────────────────

@pytest.mark.parametrize("label,raw", WHITESPACE_VARIANTS)
def test_kalshi_api_key_is_normalized(monkeypatch, label, raw):
    """
    KALSHI_API_KEY reaches an HTTP HEADER rather than a URL, where the same
    accidental whitespace fails differently but just as silently: a trailing
    newline makes http.client raise `ValueError: Invalid header value`, and a
    trailing space is transmitted and rejected upstream as a 401 that reads
    like an expired key.
    """
    monkeypatch.setenv("KALSHI_API_KEY", raw)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import fetch_kalshi_clv_v2
    mod = importlib.reload(fetch_kalshi_clv_v2)
    assert mod._KALSHI_API_KEY == SENTINEL_KEY
    headers = mod._auth_headers()
    assert headers["Authorization"] == "Bearer " + SENTINEL_KEY


def test_kalshi_missing_key_still_yields_no_auth_header(monkeypatch):
    """Optional-credential semantics must be preserved exactly."""
    monkeypatch.setenv("KALSHI_API_KEY", "   ")
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import fetch_kalshi_clv_v2
    mod = importlib.reload(fetch_kalshi_clv_v2)
    assert mod._KALSHI_API_KEY == ""
    assert mod._auth_headers() == {}
