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
policy"): a record missing an OPTIONAL field is never an error. Only one
schema version ("1") exists today, so additionalProperties strictness is
validated against that single version's field set; a future
schema_v2/ directory is expected to get its own schema files and its own
validate_record() dispatch (by the record's own schemaVersion), not a
retrofit onto this module -- not implemented yet since there is nothing
to migrate from/to until a v2 actually exists.
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
_common_cache = None


def load_schema(entity: str) -> dict:
    if entity not in _ENTITY_FILES:
        raise ValueError(f"Unknown EdgeLab entity {entity!r}. Known: {sorted(_ENTITY_FILES)}")
    if entity not in _schema_cache:
        path = os.path.join(SCHEMA_DIR, _ENTITY_FILES[entity])
        with open(path) as f:
            _schema_cache[entity] = json.load(f)
    return _schema_cache[entity]


def _load_common() -> dict:
    global _common_cache
    if _common_cache is None:
        with open(os.path.join(SCHEMA_DIR, "_common.schema.json")) as f:
            _common_cache = json.load(f)
    return _common_cache


def _resolve(spec: dict) -> dict:
    """
    Resolve a single-level '$ref': '_common.schema.json#/definitions/X'
    to the referenced definition. This repo's schemas never nest $ref
    more than one level deep, so this is deliberately not a general
    JSON Pointer resolver.
    """
    ref = spec.get("$ref")
    if not ref:
        return spec
    _, pointer = ref.split("#", 1)
    node = _load_common()
    for part in pointer.strip("/").split("/"):
        node = node[part]
    return node


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
        resolved = _resolve(spec)
        enum = resolved.get("enum")
        if enum is not None and record[field] not in enum:
            errors.append(f"{entity}: field '{field}' value {record[field]!r} not in allowed enum {enum}")
        const = resolved.get("const")
        if const is not None and record[field] != const:
            errors.append(f"{entity}: field '{field}' value {record[field]!r} must equal {const!r}")

    return errors
