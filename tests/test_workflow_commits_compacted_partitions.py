"""
Every workflow that writes a COMPACTABLE EdgeLab partition must commit both
the plain and the .gz form of that partition's path.

Why: scripts/edgelab/compact_edgelab_partitions.py gzips finalized
partitions for five entities, so any date older than the compaction window
lives on disk as `<date>.jsonl.gz`. lib.edgelab.storage.resolve_partition_path
correctly writes back to that .gz file -- but the workflows listed only the
uncompressed `<date>.jsonl` in their git_data_commit path list, so the write
was never staged and was silently discarded when the runner was torn down.

Demonstrated on 2026-08-31: a backfill ingest of 2026-08-28 reported
marketsUpserted=4964 and outputFiles including
`data/edgelab/markets/2026-08-28.jsonl.gz`, yet the resulting commit
(f2be7ee) contained games, observations.gz and research_runs and NO markets
file at all -- leaving the market dimension, and therefore the settlement
universe settle_markets.py builds from it, stuck at the stale 1,896 rows.

git_data_commit.py skips paths that don't exist, so listing both forms is
always safe: exactly one of them is present on any given run.
"""
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKFLOW_DIR = os.path.join(_ROOT, ".github", "workflows")

# scripts/edgelab/compact_edgelab_partitions.py::ENTITIES
COMPACTABLE_ENTITIES = ("settlements", "clv_quotes", "model_evaluations",
                        "markets", "recommendations")

_COMMITTED_PATH_RE = re.compile(r'"(data/edgelab/(\w+)/[^"]*?\.jsonl)"')


def _workflow_files():
    return [os.path.join(_WORKFLOW_DIR, n) for n in sorted(os.listdir(_WORKFLOW_DIR))
            if n.endswith((".yml", ".yaml"))]


def _compactable_plain_paths(text):
    return [m.group(1) for m in _COMMITTED_PATH_RE.finditer(text)
            if m.group(2) in COMPACTABLE_ENTITIES]


@pytest.mark.parametrize("workflow", _workflow_files(), ids=os.path.basename)
def test_a_compactable_partition_is_committed_in_both_forms(workflow):
    text = open(workflow).read()
    missing = [p for p in _compactable_plain_paths(text) if f'"{p}.gz"' not in text]
    assert not missing, (
        f"{os.path.basename(workflow)} commits these compactable partitions only in "
        f"uncompressed form, so a write to the already-compacted .gz partition of an "
        f"older date is silently dropped: {missing}"
    )


def test_the_compactable_entity_list_matches_the_compaction_script():
    """If the compaction script starts gzipping another entity, this guard must
    widen with it rather than silently stop covering the new one."""
    script = open(os.path.join(_ROOT, "scripts", "edgelab", "compact_edgelab_partitions.py")).read()
    declared = re.search(r"^ENTITIES\s*=\s*\(([^)]*)\)", script, re.M).group(1)
    assert set(re.findall(r'"(\w+)"', declared)) == set(COMPACTABLE_ENTITIES)


def test_the_regression_is_actually_detected():
    """The guard must fail on the exact shape that shipped the bug."""
    broken = 'python3 scripts/ci/git_data_commit.py "data/edgelab/markets/2026-08-28.jsonl"'
    assert _compactable_plain_paths(broken) == ["data/edgelab/markets/2026-08-28.jsonl"]
    assert '"data/edgelab/markets/2026-08-28.jsonl.gz"' not in broken
