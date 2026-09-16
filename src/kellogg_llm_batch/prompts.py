from __future__ import annotations

import json
from pathlib import Path
from string import Template
from typing import Any

import pandas as pd
import yaml

from .config import ProjectConfig


def _context_format(path: Path, configured: str) -> str:
    if configured != "auto":
        return configured
    mapping = {".csv": "csv", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".txt": "text", ".md": "text"}
    if path.suffix.lower() not in mapping:
        raise ValueError(f"Cannot infer context format for {path}")
    return mapping[path.suffix.lower()]


def load_context_file(name: str, path: Path, configured: str = "auto") -> dict[str, str]:
    """Load one prompt-context file using the package's deterministic representation."""
    if not path.exists():
        raise FileNotFoundError(f"Prompt context not found: {path}")
    fmt = _context_format(path, configured)
    if fmt == "csv":
        frame = pd.read_csv(path, dtype=object)
        data: Any = frame.where(pd.notna(frame), None).to_dict("records")
    elif fmt == "json":
        data = json.loads(path.read_text(encoding="utf-8"))
    elif fmt == "yaml":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif fmt == "text":
        return {f"{name}_text": path.read_text(encoding="utf-8")}
    else:
        raise ValueError(f"Unsupported context format: {fmt}")
    return {f"{name}_json": json.dumps(data, ensure_ascii=False, sort_keys=True)}


def load_context(config: ProjectConfig) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, item in config.prompt.context.items():
        path = config.resolve(item.path)
        values.update(load_context_file(name, path, item.format))
    return values


def load_prompt_files(config: ProjectConfig) -> tuple[str, str]:
    system_path = config.resolve(config.prompt.system_file)
    user_path = config.resolve(config.prompt.user_file)
    if not system_path.exists():
        raise FileNotFoundError(f"System prompt not found: {system_path}")
    if not user_path.exists():
        raise FileNotFoundError(f"User prompt not found: {user_path}")
    return system_path.read_text(encoding="utf-8"), user_path.read_text(encoding="utf-8")


def validate_template(template_text: str, context: dict[str, str]) -> None:
    allowed = set(context) | {"records_json"}
    identifiers: set[str] = set()
    for match in Template.pattern.finditer(template_text):
        if match.group("invalid") is not None:
            raise ValueError("Invalid '$' placeholder in user prompt")
        identifier = match.group("named") or match.group("braced")
        if identifier:
            identifiers.add(identifier)
    unknown = sorted(identifiers - allowed)
    if unknown:
        raise ValueError(f"Unknown prompt placeholders: {unknown}; allowed: {sorted(allowed)}")
    if "records_json" not in identifiers:
        raise ValueError("User prompt must contain ${records_json}")


def render_user_prompt(template_text: str, records: list[dict[str, Any]], context: dict[str, str]) -> str:
    substitutions = dict(context)
    substitutions["records_json"] = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    return Template(template_text).substitute(substitutions)
