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

    def test_existing_function_duration_config_untouched(self):
        """The Vercel preview-deployment fix is infrastructure-only -- it must
        never touch the existing production function configuration."""
        doc = _load()
        assert doc["functions"] == {
            "api/slate.js": {"maxDuration": 60},
            "api/savant.js": {"maxDuration": 60},
            "api/enrich.js": {"maxDuration": 60},
            "api/bullpen.js": {"maxDuration": 30},
            "api/teamstats.js": {"maxDuration": 30},
        }


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
