"""
lib/edgelab/research_lab_ids.py
===================================
Research Lab Milestone 0A (Research Lab Control & Experiment Contract):
deterministic identifier builders for the new research-governance
entities this milestone introduces -- control-model registrations,
candidate-variant registrations, and experiment reports.

Deliberately a SEPARATE module from lib.edgelab.ids rather than an
addition to it: ids.py is imported by production-adjacent ingestion
paths (bets, recommendations, settlements, snapshots) that this
milestone must not touch at all (see docs/EDGELAB_RESEARCH_LAB.md's
production-safety section). Keeping every new ID builder in its own
file means this milestone's entire diff is additive/isolated -- zero
lines changed in ids.py -- and a future reviewer auditing "did this
milestone touch anything production-adjacent" can answer by file list
alone.

Same hashing convention as lib.edgelab.ids._sha1 (sha1 of
unit-separator-joined parts) -- duplicated here on purpose, not
imported, for the isolation reason above.
"""

import hashlib


def _sha1(*parts) -> str:
    h = hashlib.sha1()
    for p in parts:
        h.update((str(p) if p is not None else "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def build_control_model_id(name: str, source_git_commit_sha, config_fingerprint) -> str:
    """
    Deterministic over (name, sourceGitCommitSha, configFingerprint) --
    re-registering the identical control (same name, same commit, same
    config fingerprint) always re-derives the same id, so registration
    is idempotent. A genuinely different commit or config fingerprint
    for the "same" conceptual control produces a DIFFERENT id -- control
    identity is never allowed to silently drift under one stable name.
    """
    return "CTRL-" + _sha1("control_model", name, source_git_commit_sha or "", config_fingerprint or "")[:16]


def build_candidate_variant_id(name: str, base_control_model_id: str, change_description: str) -> str:
    """Deterministic over (name, baseControlModelId, changeDescription) -- same reasoning as build_control_model_id."""
    return "CAND-" + _sha1("candidate_variant", name, base_control_model_id or "", change_description or "")[:16]


def build_experiment_report_id(experiment_id: str, control_model_id: str, candidate_id, generated_at: str) -> str:
    """
    Deterministic over (experimentId, controlModelId, candidateId,
    generatedAt) -- unlike control/candidate registration, MULTIPLE
    reports for the same experiment are expected over time (a
    development-stage look, a later confirmatory holdout look), so
    identity intentionally includes generatedAt rather than collapsing
    repeated evaluations into one overwritten record.
    """
    return "RPT-" + _sha1("experiment_report", experiment_id, control_model_id or "", candidate_id or "", generated_at)[:16]


def config_fingerprint(*, config_dict=None, config_text=None) -> str:
    """
    A stable content fingerprint for a control/candidate's configuration
    -- sha1 over the sorted-keys JSON serialization of `config_dict`, or
    over `config_text` verbatim when the config isn't representable as a
    dict (e.g. a raw file's bytes). Exactly one of the two must be
    given. Never a hash of a file PATH (paths aren't content) or of a
    mutable object's id() (not stable across processes).
    """
    import json

    if (config_dict is None) == (config_text is None):
        raise ValueError("config_fingerprint requires exactly one of config_dict or config_text")
    if config_dict is not None:
        payload = json.dumps(config_dict, sort_keys=True, default=str)
    else:
        payload = config_text
    return _sha1("config_fingerprint", payload)
