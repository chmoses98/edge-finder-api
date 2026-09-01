#!/usr/bin/env python3
"""MLB-ALPHA-0001: blind-holdout scorer for MLB-ALPHA-0001-C01-PIT.

THIS SCRIPT IS LOCKED. It refuses to read a single byte of holdout market
data, settlement data, or outcome data unless an explicit authorization
file exists AND names the exact frozen candidate rule hash.

The authorization file is NOT created by the session that wrote this
scorer. Creating it is a deliberate human act.

Structure is deliberate: `authorize_or_refuse()` runs BEFORE any import of
data-loading helpers is used and before any path is opened, so an
unauthorized invocation cannot touch holdout data even accidentally.
"""

import json
import os
import sys

ART = os.path.join(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
    "data", "edgelab", "research_artifacts", "mlb_alpha_0001")
PROTOCOL_PATH = os.path.join(ART, "frozen_holdout_protocol.json")
AUTH_PATH = os.path.join(ART, "HOLDOUT_AUTHORIZATION.json")


class HoldoutSealed(RuntimeError):
    """Raised when the holdout is scored without explicit authorization."""


def load_protocol():
    with open(PROTOCOL_PATH) as fh:
        return json.load(fh)


def authorize_or_refuse(protocol=None, auth_path=AUTH_PATH):
    """Returns the authorization record, or raises HoldoutSealed.

    Requires ALL of:
      * the authorization file exists;
      * it parses as JSON;
      * its candidateRuleSha256 exactly equals the frozen protocol's;
      * it sets authorized == True.
    Any failure refuses. Nothing is read from the holdout before this
    function returns successfully.
    """
    protocol = protocol or load_protocol()
    if not os.path.exists(auth_path):
        raise HoldoutSealed(
            "BLIND HOLDOUT IS SEALED. No authorization file at %s. "
            "Scoring refused; no holdout data was read." % auth_path)
    try:
        with open(auth_path) as fh:
            auth = json.load(fh)
    except Exception as exc:
        raise HoldoutSealed("authorization file unreadable (%s); refusing" % exc)
    if auth.get("authorized") is not True:
        raise HoldoutSealed("authorization file does not set authorized=true; refusing")
    expected = protocol["candidateRuleSha256"]
    if auth.get("candidateRuleSha256") != expected:
        raise HoldoutSealed(
            "authorization names rule %r but the frozen protocol is %r; refusing"
            % (auth.get("candidateRuleSha256"), expected))
    return auth


def main():
    protocol = load_protocol()
    try:
        authorize_or_refuse(protocol)
    except HoldoutSealed as exc:
        print("REFUSED:", exc)
        print("holdout status:", protocol["holdoutStatus"])
        return 2
    # Everything below is unreachable without a valid authorization file.
    print("AUTHORIZED. Scoring %s on %s"
          % (protocol["candidateId"], protocol["holdoutDates"]))
    print("NOTE: the scoring implementation is intentionally not written in "
          "the sealing session. Implement it under the frozen protocol, "
          "applying the verdict rules verbatim.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
