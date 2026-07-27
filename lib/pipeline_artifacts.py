#!/usr/bin/env python3
"""
lib/pipeline_artifacts.py
===========================
Shared helper for writing immutable, versioned pipeline-stage artifacts
alongside the legacy data/slate.json working file.

This is the first building block of the Phase 3 immutable-pipeline
architecture (see docs/IMMUTABLE_PIPELINE.md). A stage that adopts it:

  1. Computes its output exactly as it did before — no change to WHAT is
     computed, only to where the result additionally gets written.
  2. Continues writing data/slate.json (or whatever legacy path
     downstream scripts still expect) exactly as before, so production
     behavior is completely unchanged.
  3. ALSO writes that same output to data/pipeline/<date>/<stage>.json —
     a new, distinctly-named artifact this stage owns, that no other
     script writes.

This additive pattern is deliberate: it does not require every consumer
of data/slate.json to be migrated before any stage can start producing an
immutable artifact. Consumers can switch to reading a stage's immutable
artifact directly at their own pace in a future phase, instead of all at
once. Per the same reasoning, callers should wrap write_stage_artifact()
in a try/except and treat any failure as non-fatal — this is a new,
additive code path and must never be able to break the pipeline stage
that adopts it.
"""

import json
import os

PIPELINE_ROOT = os.path.join("data", "pipeline")


def artifact_path(stage: str, date: str) -> str:
    """Return the path a stage's immutable artifact is written to."""
    if not stage:
        raise ValueError("stage name is required to build an artifact path")
    if not date:
        raise ValueError("date is required to build an artifact path")
    return os.path.join(PIPELINE_ROOT, date, f"{stage}.json")


def write_stage_artifact(stage: str, date: str, data: dict) -> str:
    """
    Write `data` to this stage's immutable artifact path and return the
    path written. Does not touch any other file — in particular, never
    touches data/slate.json or any other stage's artifact.

    Artifacts are write-once per (stage, date) in intent — a rerun that
    calls this again for the same stage/date overwrites the artifact with
    the new run's output, mirroring how data/slate.json itself is
    refreshed on a rerun. This does not currently implement the
    official/recheck versioning lib/slate_manager.py uses for the
    authoritative slate — that is a candidate for a future phase, not
    introduced here to keep this change additive and low-risk.
    """
    path = artifact_path(stage, date)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def read_stage_artifact(stage: str, date: str) -> dict:
    """Read a previously-written stage artifact. Raises FileNotFoundError if absent."""
    path = artifact_path(stage, date)
    with open(path) as f:
        return json.load(f)


def stage_artifact_exists(stage: str, date: str) -> bool:
    return os.path.exists(artifact_path(stage, date))
