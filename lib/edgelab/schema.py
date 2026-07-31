"""
lib/edgelab/schema.py
=======================
Lightweight validation against data/edgelab/schema_v1/*.schema.json,
without adding a jsonschema dependency (not currently installed in this
repo). Covers exactly what EdgeLab needs to catch: missing required
fields, values outside a declared enum, and unknown properties -- not a
full JSON Schema implementation (no $ref chasing beyond the one level
this repo's schemas actually use, no format validation).

Migration contract (data/edgelab/schema_v1/README.md's "Versioning
policy"): a record missing an OPTIONAL field, or carrying a field this
version of the schema doesn't recognize under an OLDER schemaVersion, is
not an error here -- only required-field/enum/additionalProperties
violations against the record's OWN declared schemaVersion are.
"""

import json
import os

SCHEMA_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(SCHEMA_DIR)), "data", "edgelab", "schema_v1")

_ENTITY_FILES = {
    "game": "game.schema.json",
    "market": "market.schema.json",
    "market_observation": "market_observation.schema.json",
    "model_evaluation": "model_evaluation.schema.json",
    "recommendation": "recommendation.schema.json",
    "placed_bet": "placed_bet.schema.json",
    "clv_quote": "clv_quote.schema.json",
    "settlement": "settlement.schema.json",
    "research_run": "research_run.schema.json",
}

_schema_cache = {}


def load_schema(entity: str) -> dict:
    if entity not in _ENTITY_FILES:
        raise ValueError(f"Unknown EdgeLab entity {entity!r}. Known: {sorted(_ENTITY_FILES)}")
    if entity not in _schema_cache:
        path = os.path.join(SCHEMA_DIR, _ENTITY_FILES[entity])
        with open(path) as f:
            _schema_cache[entity] = json.load(f)
    return _schema_cache[entity]


def validate_record(entity: str, record: dict):
    """
    Returns a list of human-readable error strings; empty list means valid.
    Never raises on a malformed record -- callers decide what to do with
    the errors (log, quarantine, block a commit, etc).
    """
    schema = load_schema(entity)
    errors = []

    required = schema.get("required", [])
    properties = schema.get("properties", {})

    for field in required:
        if field not in record or record[field] is None:
            errors.append(f"{entity}: missing required field '{field}'")

    if schema.get("additionalProperties") is False:
        unknown = set(record) - set(properties)
        for field in sorted(unknown):
            errors.append(f"{entity}: unknown field '{field}' not in schema")

    for field, spec in properties.items():
        if field not in record or record[field] is None:
            continue
        enum = spec.get("enum")
        if enum is not None and record[field] not in enum:
            errors.append(f"{entity}: field '{field}' value {record[field]!r} not in allowed enum {enum}")

    return errors
