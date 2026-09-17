from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from jsonschema import Draft202012Validator

from .config import ProjectConfig
from .data import canonicalize, infer_format, read_table
from .prompts import load_context_file, render_user_prompt, validate_template
from .scaffold import PROJECT_YAML, SCHEMA
from .utils import sha256_file
from .validation import ProjectValidationError, validate_project


DEFAULT_SYSTEM_PROMPT = (
    "You are a rigorous research-coding assistant. Use only the supplied evidence, "
    "apply the codebook consistently, and return one result for every record_id.\n"
)
DEFAULT_USER_PROMPT = "Records:\n\n${records_json}\n"
SCALAR_TYPES = {"string", "integer", "number", "boolean"}
PROJECT_CONFIG_ORDER = (
    "version",
    "project",
    "input",
    "task",
    "prompt",
    "providers",
    "budget",
    "evaluation",
    "output",
)


class ProjectDraftConflict(RuntimeError):
    """Raised when a managed project file changed after it was loaded."""


@dataclass
class ProjectDraft:
    project_dir: Path
    config: dict[str, Any]
    schema: dict[str, Any]
    system_prompt: str
    user_prompt: str
    codebook_source: Path | None = None
    input_upload_source: Path | None = None
    original_hashes: dict[str, str | None] = field(default_factory=dict)
    existing_project: bool = False


@dataclass
class PreviewResult:
    source_rows: list[int]
    record_ids: list[str]
    system_prompt: str
    user_prompt: str
    records: list[dict[str, Any]]

    @property
    def source_row(self) -> int:
        """First previewed source row, retained for API compatibility."""
        return self.source_rows[0]

    @property
    def record_id(self) -> str:
        """First previewed record ID, retained for API compatibility."""
        return self.record_ids[0]

    @property
    def record(self) -> dict[str, Any]:
        """First previewed record, retained for API compatibility."""
        return self.records[0]


@dataclass
class SaveResult:
    project_file: Path
    validation_report: dict[str, Any]


def _managed_relative_paths(config: dict[str, Any]) -> list[str]:
    prompt = config.get("prompt", {})
    task = config.get("task", {})
    paths = [
        "project.yaml",
        str(task.get("output_schema", "schema.json")),
        str(prompt.get("system_file", "prompts/system.txt")),
        str(prompt.get("user_file", "prompts/user.txt")),
    ]
    codebook = (prompt.get("context") or {}).get("codebook")
    if codebook and codebook.get("path"):
        paths.append(str(codebook["path"]))
    return list(dict.fromkeys(paths))


def _snapshot_hashes(project_dir: Path, config: dict[str, Any]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for relative in _managed_relative_paths(config):
        path = project_dir / relative
        hashes[relative] = sha256_file(path) if path.is_file() else None
    return hashes


def new_project_draft(project_dir: str | Path) -> ProjectDraft:
    root = Path(project_dir).expanduser().resolve()
    if (root / "project.yaml").exists():
        raise FileExistsError(f"A project already exists at {root}; open it instead of creating a new draft")
    config = yaml.safe_load(PROJECT_YAML)
    config["project"]["name"] = root.name or "my-coding-project"
    config["input"]["path"] = ""
    config["input"]["id_column"] = None
    config["input"]["fields_sent"] = {}
    config["input"]["required_fields"] = []
    config["prompt"]["context"] = {}
    return ProjectDraft(
        project_dir=root,
        config=config,
        schema=json.loads(SCHEMA),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        user_prompt=DEFAULT_USER_PROMPT,
        original_hashes=_snapshot_hashes(root, config),
    )


def load_project_draft(path: str | Path) -> ProjectDraft:
    candidate = Path(path).expanduser().resolve()
    project_file = candidate if candidate.name == "project.yaml" else candidate / "project.yaml"
    if not project_file.is_file():
        raise FileNotFoundError(f"Existing project configuration not found: {project_file}")
    project_dir = project_file.parent
    config = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("project.yaml must contain a YAML mapping")
    task = config.get("task", {})
    prompt = config.get("prompt", {})

    def resolve(value: str) -> Path:
        item = Path(value).expanduser()
        return item if item.is_absolute() else project_dir / item

    schema_path = resolve(str(task.get("output_schema", "schema.json")))
    system_path = resolve(str(prompt.get("system_file", "prompts/system.txt")))
    user_path = resolve(str(prompt.get("user_file", "prompts/user.txt")))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    codebook_source = None
    codebook = (prompt.get("context") or {}).get("codebook")
    if codebook and codebook.get("path"):
        codebook_source = resolve(str(codebook["path"])).resolve()
    return ProjectDraft(
        project_dir=project_dir,
        config=config,
        schema=schema,
        system_prompt=system_path.read_text(encoding="utf-8"),
        user_prompt=user_path.read_text(encoding="utf-8"),
        codebook_source=codebook_source,
        original_hashes=_snapshot_hashes(project_dir, config),
        existing_project=True,
    )


def load_input_table(path: str | Path, configured_format: str = "auto", csv_encoding: str = "utf-8") -> tuple[pd.DataFrame, str]:
    source = Path(path).expanduser().resolve()
    fmt = infer_format(source, configured_format)
    return read_table(source, fmt, csv_encoding), fmt


def _project_config_from_draft(draft: ProjectDraft) -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            **copy.deepcopy(draft.config),
            "config_path": draft.project_dir / "project.yaml",
            "base_dir": draft.project_dir,
        }
    )


def _draft_context(draft: ProjectDraft) -> dict[str, str]:
    values: dict[str, str] = {}
    context = (draft.config.get("prompt", {}).get("context") or {})
    for name, item in context.items():
        configured_path = Path(str(item["path"])).expanduser()
        path = configured_path if configured_path.is_absolute() else draft.project_dir / configured_path
        if name == "codebook" and draft.codebook_source is not None:
            path = draft.codebook_source
        values.update(load_context_file(name, path, str(item.get("format", "auto"))))
    if draft.codebook_source is not None and "codebook" not in context:
        values.update(load_context_file("codebook", draft.codebook_source, "auto"))
    return values


def render_project_preview(draft: ProjectDraft, source_row: int = 0, row_count: int = 1) -> PreviewResult:
    config = _project_config_from_draft(draft)
    source = config.resolve(config.input.path)
    frame, _ = load_input_table(source, config.input.format, config.input.csv_encoding)
    records = canonicalize(config, frame, source)
    if not records:
        raise ValueError("Input contains no records")
    if source_row < 0 or source_row >= len(records):
        raise IndexError(f"Preview row {source_row} is outside 0..{len(records) - 1}")
    if row_count < 1:
        raise ValueError("Preview row_count must be at least 1")
    selected = records[source_row : source_row + row_count]
    prompt_records = [{"record_id": record.record_id, **record.sent} for record in selected]
    context = _draft_context(draft)
    validate_template(draft.user_prompt, context)
    return PreviewResult(
        source_rows=list(range(source_row, source_row + len(selected))),
        record_ids=[record.record_id for record in selected],
        system_prompt=draft.system_prompt,
        user_prompt=render_user_prompt(draft.user_prompt, prompt_records, context),
        records=prompt_records,
    )


def schema_supports_guided_editor(schema: dict[str, Any]) -> bool:
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return False
    if schema.get("additionalProperties") is not False:
        return False
    if set(schema.get("required", [])) != set(schema["properties"]):
        return False
    allowed = {"type", "enum", "description", "minimum", "maximum"}
    for item in schema["properties"].values():
        if not isinstance(item, dict) or set(item) - allowed:
            return False
        schema_type = item.get("type")
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        non_null = [value for value in types if value != "null"]
        if len(non_null) != 1 or non_null[0] not in SCALAR_TYPES or len(types) > 2:
            return False
    return True


def schema_to_field_rows(schema: dict[str, Any]) -> list[dict[str, Any]]:
    if not schema_supports_guided_editor(schema):
        raise ValueError("Schema contains features that require advanced JSON mode")
    rows = []
    for name, item in schema["properties"].items():
        schema_type = item.get("type")
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        rows.append(
            {
                "name": name,
                "type": next(value for value in types if value != "null"),
                "nullable": "null" in types,
                "enum_json": json.dumps(item.get("enum", []), ensure_ascii=False) if "enum" in item else "",
                "description": item.get("description", ""),
                "minimum": item.get("minimum"),
                "maximum": item.get("maximum"),
            }
        )
    return rows


def field_rows_to_schema(rows: list[dict[str, Any]]) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        name = str(row.get("name", "")).strip()
        if not name:
            raise ValueError(f"Schema field row {index} has no name")
        if name == "record_id":
            raise ValueError("record_id is package-managed and cannot be a schema field")
        if name in properties:
            raise ValueError(f"Duplicate schema field: {name}")
        field_type = str(row.get("type", "string"))
        if field_type not in SCALAR_TYPES:
            raise ValueError(f"Unsupported guided field type for {name}: {field_type}")
        nullable = bool(row.get("nullable", False))
        item: dict[str, Any] = {"type": [field_type, "null"] if nullable else field_type}
        enum_text = str(row.get("enum_json", "")).strip()
        if enum_text:
            enum_values = json.loads(enum_text)
            if not isinstance(enum_values, list) or not enum_values:
                raise ValueError(f"Enum for {name} must be a nonempty JSON array")
            if nullable and None not in enum_values:
                enum_values.append(None)
            item["enum"] = enum_values
        description = str(row.get("description", "")).strip()
        if description:
            item["description"] = description
        for bound in ("minimum", "maximum"):
            value = row.get(bound)
            if value is not None and not pd.isna(value):
                if field_type not in {"integer", "number"}:
                    raise ValueError(f"{bound} is only valid for numeric field {name}")
                item[bound] = int(value) if field_type == "integer" and float(value).is_integer() else float(value)
        properties[name] = item
    if not properties:
        raise ValueError("Schema must define at least one output field")
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _portable_path(path: Path, project_dir: Path) -> str:
    path = path.expanduser().resolve()
    try:
        return str(path.relative_to(project_dir))
    except ValueError:
        return str(path)


def _context_placeholders(config: dict[str, Any]) -> dict[str, str]:
    placeholders: dict[str, str] = {}
    for name, item in (config.get("prompt", {}).get("context") or {}).items():
        path = Path(str(item["path"]))
        fmt = str(item.get("format", "auto"))
        if fmt == "auto":
            fmt = "text" if path.suffix.lower() in {".txt", ".md"} else "json"
        placeholders[f"{name}_text" if fmt == "text" else f"{name}_json"] = ""
    return placeholders


def _check_conflicts(draft: ProjectDraft) -> None:
    for relative, expected in draft.original_hashes.items():
        path = draft.project_dir / relative
        current = sha256_file(path) if path.is_file() else None
        if current != expected:
            raise ProjectDraftConflict(
                f"Managed file changed after the GUI loaded it: {path}. Reload the project before saving."
            )


def _build_save_config(draft: ProjectDraft) -> tuple[dict[str, Any], Path | None, Path | None]:
    """Build the exact portable configuration and copy destinations used by Save."""
    root = draft.project_dir.expanduser().resolve()
    remaining = copy.deepcopy(draft.config)
    config = {key: remaining.pop(key) for key in PROJECT_CONFIG_ORDER if key in remaining}
    config.update(remaining)
    input_destination: Path | None = None
    if draft.input_upload_source is not None:
        input_source = draft.input_upload_source.expanduser().resolve()
        if not input_source.is_file():
            raise FileNotFoundError(f"Uploaded input is no longer available: {input_source}")
        input_destination = Path("data") / input_source.name
        existing_destination = root / input_destination
        source_hash = sha256_file(input_source)
        if existing_destination.is_file() and sha256_file(existing_destination) != source_hash:
            input_destination = Path("data") / (
                f"{input_source.stem}-{source_hash[:8]}{input_source.suffix.lower()}"
            )
            existing_destination = root / input_destination
            if existing_destination.is_file() and sha256_file(existing_destination) != source_hash:
                raise FileExistsError(f"Refusing to overwrite an existing input file: {existing_destination}")
        config["input"]["path"] = str(input_destination)
    else:
        input_path = Path(str(config["input"]["path"])).expanduser()
        if not input_path.is_absolute():
            input_path = (root / input_path).resolve()
        config["input"]["path"] = _portable_path(input_path, root)
    config["task"]["output_schema"] = "schema.json"
    config["prompt"]["system_file"] = "prompts/system.txt"
    config["prompt"]["user_file"] = "prompts/user.txt"
    context = copy.deepcopy(config["prompt"].get("context") or {})
    codebook_destination: Path | None = None
    if draft.codebook_source is not None:
        source = draft.codebook_source.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Codebook not found: {source}")
        codebook_destination = Path("context") / source.name
        existing_destination = root / codebook_destination
        source_hash = sha256_file(source)
        if (
            existing_destination.is_file()
            and str(codebook_destination) not in draft.original_hashes
            and existing_destination.resolve() != source
            and sha256_file(existing_destination) != source_hash
        ):
            codebook_destination = Path("context") / (
                f"{source.stem}-{source_hash[:8]}{source.suffix.lower()}"
            )
            existing_destination = root / codebook_destination
            if existing_destination.is_file() and sha256_file(existing_destination) != source_hash:
                raise FileExistsError(f"Refusing to overwrite an existing context file: {existing_destination}")
        context["codebook"] = {"path": str(codebook_destination), "format": "auto"}
    else:
        context.pop("codebook", None)
    config["prompt"]["context"] = context
    return config, input_destination, codebook_destination


def render_project_yaml(draft: ProjectDraft) -> str:
    """Render the exact project.yaml content that Save would currently write."""
    config, _, _ = _build_save_config(draft)
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def validate_output_schema(schema: dict[str, Any]) -> None:
    """Validate JSON Schema syntax and the package-managed row contract."""
    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object":
        raise ValueError("Per-row schema must have type 'object'")
    if "record_id" in schema.get("properties", {}):
        raise ValueError("record_id is package-managed and cannot appear in schema.json")
    if schema.get("additionalProperties") is not False:
        raise ValueError("Per-row schema must set additionalProperties to false")
    if set(schema.get("properties", {})) - set(schema.get("required", [])):
        raise ValueError("Every schema property must be required; use nullable types for optional values")


def save_project_draft(draft: ProjectDraft) -> SaveResult:
    root = draft.project_dir.expanduser().resolve()
    if root.exists() and any(root.iterdir()) and not draft.existing_project and not (root / "project.yaml").exists():
        raise FileExistsError(f"Refusing to create a project in unrelated nonempty directory: {root}")
    _check_conflicts(draft)
    config, input_destination, codebook_destination = _build_save_config(draft)

    ProjectConfig.model_validate(
        {**copy.deepcopy(config), "config_path": root / "project.yaml", "base_dir": root}
    )
    validate_output_schema(draft.schema)
    validate_template(draft.user_prompt, _context_placeholders(config))

    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".kllm-gui-", dir=root))
    try:
        files: dict[Path, bytes] = {
            Path("project.yaml"): yaml.safe_dump(config, sort_keys=False, allow_unicode=True).encode("utf-8"),
            Path("schema.json"): (json.dumps(draft.schema, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
            Path("prompts/system.txt"): draft.system_prompt.encode("utf-8"),
            Path("prompts/user.txt"): draft.user_prompt.encode("utf-8"),
        }
        if input_destination is not None:
            files[input_destination] = draft.input_upload_source.read_bytes()
        if codebook_destination is not None:
            files[codebook_destination] = draft.codebook_source.read_bytes()
        if not (root / ".gitignore").exists():
            files[Path(".gitignore")] = b"runs/\n.env\n"
        for relative, content in files.items():
            staged = staging / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(content)
        originals = {
            relative: (root / relative).read_bytes() if (root / relative).is_file() else None
            for relative in files
        }
        replaced: list[Path] = []
        try:
            for relative in files:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / relative, target)
                replaced.append(relative)
        except Exception:
            for relative in reversed(replaced):
                target = root / relative
                original = originals[relative]
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    restore = target.with_name(target.name + ".restore-tmp")
                    restore.write_bytes(original)
                    os.replace(restore, target)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    draft.config = config
    draft.project_dir = root
    draft.existing_project = True
    draft.input_upload_source = None
    draft.codebook_source = root / codebook_destination if codebook_destination else None
    draft.original_hashes = _snapshot_hashes(root, config)
    project_file = root / "project.yaml"
    try:
        report = validate_project(project_file)
    except ProjectValidationError as exc:
        report = exc.report
    return SaveResult(project_file=project_file, validation_report=report)
