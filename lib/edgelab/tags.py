"""
lib/edgelab/tags.py
=====================
Controlled thesis-tag vocabulary, loaded from the single source of truth
data/edgelab/schema_v1/tags.json (not duplicated here as a Python literal,
so the JSON file stays authoritative for both Python and any future
non-Python reader).
"""

import json
import os

_TAGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "edgelab", "schema_v1", "tags.json",
)


def load_thesis_tags():
    with open(_TAGS_PATH) as f:
        return frozenset(json.load(f)["tags"])


THESIS_TAGS = load_thesis_tags()


def validate_tags(tags):
    """Raises ValueError listing any tag not in the controlled vocabulary."""
    unknown = sorted(set(tags) - THESIS_TAGS)
    if unknown:
        raise ValueError(
            f"Unknown thesis tag(s): {unknown}. Add to data/edgelab/schema_v1/tags.json "
            f"if this is a genuinely new research thesis, rather than repurposing an existing tag."
        )
