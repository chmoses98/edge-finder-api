#!/usr/bin/env python3
"""
tests/test_vercel_config.py
=================================
Coverage for vercel.json -- specifically the git.deploymentEnabled change
that disables automatic Vercel Preview deployments for every branch
except `main` (PR #93 final targeted fix, item 6). This is repo-level,
syntactic-only coverage -- it cannot verify actual Vercel deploy
behavior (that requires Vercel's own Git integration, which this repo
does not control), only that the committed configuration is valid and
says what this fix intends.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERCEL_JSON_PATH = os.path.join(ROOT, "vercel.json")


def _load():
    with open(VERCEL_JSON_PATH) as f:
        return json.load(f)


class TestVercelConfigSyntacticValidity:
    def test_file_exists_and_is_valid_json(self):
        assert os.path.exists(VERCEL_JSON_PATH)
        doc = _load()  # raises if malformed
        assert isinstance(doc, dict)

    # The five functions that were configured before any of this, pinned
    # exactly. A change to one of THESE is what this guard exists to catch.
    PREEXISTING_FUNCTIONS = {
        "api/slate.js": {"maxDuration": 60},
        "api/savant.js": {"maxDuration": 60},
        "api/enrich.js": {"maxDuration": 60},
        "api/bullpen.js": {"maxDuration": 30},
        "api/teamstats.js": {"maxDuration": 30},
    }

    def test_existing_function_duration_config_untouched(self):
        """The existing production function configuration must never change.

        This previously asserted equality of the WHOLE functions map, which
        also forbade configuring a function that had none -- a different
        thing from modifying one that did. api/kalshisearch.js had no
        maxDuration at all and so ran on the platform default, which is not
        enough budget for the bounded retries the capture completeness
        contract needs (PHASE D). Each pre-existing entry is still pinned
        exactly; only ADDING a new one is permitted.
        """
        doc = _load()
        for name, config in self.PREEXISTING_FUNCTIONS.items():
            assert doc["functions"][name] == config, (
                "pre-existing production function config must not change")

    def test_a_newly_configured_function_must_actually_exist(self):
        """An entry for a file that isn't there is dead config at best."""
        import os
        doc = _load()
        for name in doc["functions"]:
            assert os.path.exists(os.path.join(ROOT, name)), (
                "vercel.json configures %s, which does not exist" % name)

    def test_the_capture_endpoint_has_a_duration_budget(self):
        """The capture path retries on rate limits; retries are only safe if
        they cannot get the function killed mid-flight, which is exactly what
        running on the unconfigured platform default risked."""
        doc = _load()
        assert doc["functions"]["api/kalshisearch.js"]["maxDuration"] >= 60


class TestVercelAutomaticPreviewDeploymentsDisabled:
    def test_git_deployment_enabled_key_present(self):
        doc = _load()
        assert "git" in doc
        assert "deploymentEnabled" in doc["git"]

    def test_main_branch_deployment_still_enabled(self):
        """The production deployment path (auto-deploy on push to `main`) must
        remain intact -- this fix targets PR/branch previews only, never
        production, per the task's own 'do not disable a production
        deployment path unless genuinely necessary' constraint."""
        doc = _load()
        assert doc["git"]["deploymentEnabled"]["main"] is True

    def test_every_other_branch_disabled_via_catchall_glob(self):
        """'**' (minimatch, matches across '/' -- unlike a bare '*') catches
        every branch name INCLUDING slash-namespaced ones (e.g.
        'claude/hitter-scheduler-runtime-hardening', this repo's own actual
        feature-branch naming convention) -- a bare '*' would NOT match
        those, since minimatch's '*' does not cross path separators by
        default. This is what actually stops automatic previews for every
        real PR branch this repo uses, not just single-segment names."""
        doc = _load()
        assert doc["git"]["deploymentEnabled"]["**"] is False

    def test_catchall_pattern_is_true_path_separator_aware(self):
        """Explicit regression guard against reverting '**' to a bare '*',
        which would silently stop protecting slash-namespaced branches."""
        doc = _load()
        pattern = "**"
        assert pattern in doc["git"]["deploymentEnabled"]
        assert "*" not in doc["git"]["deploymentEnabled"] or doc["git"]["deploymentEnabled"].get("*") is False
