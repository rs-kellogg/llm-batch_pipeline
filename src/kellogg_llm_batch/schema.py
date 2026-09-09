from __future__ import annotations

import copy
import json

from jsonschema import Draft202012Validator

from .config import ProjectConfig


def load_row_schema(config: ProjectConfig) -> dict:
    path = config.resolve(config.task.output_schema)
    if not path.exists():
        raise FileNotFoundError(f"Output schema not found: {path}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object":
        raise ValueError("Per-row output schema must have type 'object'")
    if "record_id" in schema.get("properties", {}):
        raise ValueError("record_id is package-managed and must not appear in schema.json")
    _validate_common_structured_output_subset(schema, path="$")
    return schema


def _validate_common_structured_output_subset(schema: dict, path: str) -> None:
    """Enforce the conservative JSON Schema subset shared by both adapters."""
    unsupported = {"$ref", "$defs", "definitions", "oneOf", "allOf", "not", "patternProperties"} & set(schema)
    if unsupported:
        raise ValueError(f"Unsupported structured-output keywords at {path}: {sorted(unsupported)}")
    schema_type = schema.get("type")
    types = set(schema_type) if isinstance(schema_type, list) else {schema_type}
    if "object" in types:
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"Object schema at {path} must define properties")
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"Object schema at {path} must set additionalProperties to false")
        required = set(schema.get("required", []))
        missing_required = set(properties) - required
        if missing_required:
            raise ValueError(
                f"All structured-output properties must be required; make optional values nullable at {path}: "
                f"{sorted(missing_required)}"
            )
        for name, child in properties.items():
            _validate_common_structured_output_subset(child, f"{path}.{name}")
    if "array" in types:
        items = schema.get("items")
        if not isinstance(items, dict):
            raise ValueError(f"Array schema at {path} must define an object-valued items schema")
        _validate_common_structured_output_subset(items, f"{path}[]")


def wrapped_schema(row_schema: dict) -> dict:
    item = copy.deepcopy(row_schema)
    item["properties"] = {"record_id": {"type": "string"}, **item.get("properties", {})}
    item["required"] = list(dict.fromkeys(["record_id", *item.get("required", [])]))
    item["additionalProperties"] = False
    return {
        "type": "object",
        "properties": {"results": {"type": "array", "items": item}},
        "required": ["results"],
        "additionalProperties": False,
    }
