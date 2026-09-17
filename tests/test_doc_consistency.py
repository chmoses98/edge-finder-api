#!/usr/bin/env python3
"""
tests/test_doc_consistency.py
=================================
One methodology, stated the same way everywhere.

The authoritative documents (`HANDICAPPING_PLAYBOOK.md`,
`RUN_THE_SLATE.md`, `README.md`, and the copy/paste startup prompt) are
read by a fresh AI session that has no other context. If two of them
disagree, the session picks one at random -- and the two disagreements
that matter most are exactly the ones this file searches for:

  * a statement that restricts the manual handicapper to only the legacy
    11 production-model markets AFTER a game is eligible;
  * a statement that lets an unconfirmed-lineup game onto the real-money
    card.

Both are searched for programmatically, over normalized text, so a
future edit that reintroduces either phrasing fails here rather than in
a live slate.
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

AUTHORITATIVE_DOCS = (
    "HANDICAPPING_PLAYBOOK.md",
    "PLAYBOOK_LESSONS.md",
    "RUN_THE_SLATE.md",
    "README.md",
)


def _normalized(name):
    """Whitespace-collapsed document text: the source is hard-wrapped, so
    a contradictory phrase can straddle a newline and evade a naive grep."""
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return " ".join(f.read().split())


def _all_docs():
    return {name: _normalized(name) for name in AUTHORITATIVE_DOCS}


# ── contradiction 1: the handicapper restricted to the 11 legacy markets ──

# Phrases that would (re)assert the 11-row ledger as the ONLY market
# universe, with no production-model scope qualifier attached.
RESTRICTIVE_PATTERNS = (
    r"marketLedger[^.]{0,80}remains the ONLY source of truth",
    r"ONLY source of truth for recommendation-eligible markets",
    r"research/audit visibility, not a betting input",
    r"only the 11 (?:required|production)?\s*markets may be (?:bet|compared)",
)


@pytest.mark.parametrize("pattern", RESTRICTIVE_PATTERNS)
def test_no_doc_restricts_an_eligible_game_to_the_legacy_eleven_markets(pattern):
    offenders = [
        name for name, text in _all_docs().items()
        if re.search(pattern, text, re.IGNORECASE)
    ]
    assert offenders == [], (
        f"{offenders} still restrict the manual handicapper to the legacy 11-row production "
        f"universe (pattern {pattern!r}). Once a game is BETTING-ELIGIBLE, every Kalshi market "
        f"attributable to it is comparable."
    )


def test_the_eleven_row_universe_is_always_scoped_to_the_production_model():
    """`marketLedger` may still be authoritative -- for the production
    model and risk gate. Every mention of it as a source of truth must
    say so."""
    text = _normalized("RUN_THE_SLATE.md")
    for match in re.finditer(r"[^.]*marketLedger[^.]*source of truth[^.]*\.", text):
        sentence = match.group(0)
        assert re.search(r"production|risk.gate|legacy|model", sentence, re.IGNORECASE), (
            f"unscoped marketLedger authority claim: {sentence!r}"
        )


def test_the_playbook_and_run_the_slate_agree_that_every_market_is_comparable():
    playbook = _normalized("HANDICAPPING_PLAYBOOK.md")
    runbook = _normalized("RUN_THE_SLATE.md")
    assert "inspect the **complete** market universe" in playbook
    assert "every Kalshi market attributable" in runbook
    assert "NOT** the set of markets a manual handicapper may compare" in runbook


# ── contradiction 2: an unconfirmed-lineup game on the real-money card ──

def test_every_authoritative_doc_states_the_confirmed_lineup_gate():
    docs = _all_docs()
    for name in ("HANDICAPPING_PLAYBOOK.md", "RUN_THE_SLATE.md", "README.md"):
        assert re.search(r"BOTH official lineups", docs[name], re.IGNORECASE), (
            f"{name} does not state the confirmed-lineup gate"
        )


def test_no_doc_permits_an_unconfirmed_lineup_game_on_the_real_money_card():
    permissive = (
        r"probable lineups? (?:are|is) (?:good enough|sufficient|acceptable)",
        r"projected lineups? (?:may|can) be treated as confirmed",
        r"may bet .{0,40}without confirmed lineups",
        r"lineup confirmation is optional",
    )
    offenders = []
    for name, text in _all_docs().items():
        for pattern in permissive:
            if re.search(pattern, text, re.IGNORECASE):
                offenders.append((name, pattern))
    assert offenders == [], f"permissive lineup language found: {offenders}"


def test_the_docs_say_probable_lineups_are_never_confirmed():
    playbook = _normalized("HANDICAPPING_PLAYBOOK.md")
    runbook = _normalized("RUN_THE_SLATE.md")
    assert "never** confirmed" in playbook or "NEVER treated as confirmed" in playbook
    assert "NEVER treated as confirmed" in runbook


def test_the_early_value_surface_is_explicitly_separate():
    runbook = _normalized("RUN_THE_SLATE.md")
    assert "Early Value" in runbook
    assert "realMoneyEligible: false" in runbook
    assert "never leak into the executable card" in runbook


# ── the startup prompt must encode the whole workflow ───────────────────

def _startup_prompt():
    with open(os.path.join(ROOT, "RUN_THE_SLATE.md"), encoding="utf-8") as f:
        text = f.read()
    return text.split("## START-OF-SLATE PROMPT", 1)[1].split("```")[1]


def test_startup_prompt_is_compact():
    lines = [l for l in _startup_prompt().strip().splitlines() if l.strip()]
    assert 10 <= len(lines) <= 14, f"startup prompt is {len(lines)} lines"


@pytest.mark.parametrize("requirement", [
    "handicapping_playbook",       # 1. read the playbook + lessons
    "playbook_lessons",
    "newest valid",                # 2. newest valid evidence
    "bankroll",                    # 3. canonical bankroll
    "already started",             # 4. exclude started games
    "both official lineups",       # 5. exclude unconfirmed lineups
    "every available kalshi market",  # 6. full market universe
    "before choosing a market",    # 7. thesis first
    "compare expressions",         # 8. compare alternatives
    "correlation",                 # 9. price/uncertainty/correlation/bankroll
    "against each proposed bet",   # 10. evidence against
    "clearing the betting threshold",  # 11. only qualifying wagers
    "never assume a recommendation was placed",  # 12. never assume placed
])
def test_startup_prompt_encodes_the_required_workflow(requirement):
    assert requirement in _startup_prompt().lower(), f"startup prompt omits {requirement!r}"


def test_startup_prompt_points_at_the_repository_rather_than_restating_it():
    """Its job is to say where the durable memory lives, not to repeat it."""
    prompt = _startup_prompt()
    assert "edge-finder-api" in prompt
    assert len(prompt.split()) < 180, "the prompt is restating the methodology instead of pointing at it"


# ── version coherence ───────────────────────────────────────────────────

def test_the_playbook_version_is_stated_once_and_matches_the_lessons_file():
    from lib.playbook import load_lessons, read_playbook_version

    version = read_playbook_version(os.path.join(ROOT, "HANDICAPPING_PLAYBOOK.md"))
    assert version
    assert load_lessons(os.path.join(ROOT, "config", "playbook_lessons.json"))["playbookVersion"] == version
    assert f"PLAYBOOK_VERSION: {version}" in _normalized("PLAYBOOK_LESSONS.md")
