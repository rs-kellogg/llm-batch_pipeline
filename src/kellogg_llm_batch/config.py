from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectSection(StrictModel):
    name: str
    description: str = ""


class FieldLimit(StrictModel):
    max_characters: int | None = Field(default=None, gt=0)
    overflow: Literal["error", "truncate"] = "error"

    @model_validator(mode="after")
    def require_limit_for_truncation(self):
        if self.overflow == "truncate" and self.max_characters is None:
            raise ValueError("max_characters is required when overflow is 'truncate'")
        return self


class InputSection(StrictModel):
    path: Path
    format: Literal["auto", "csv", "parquet", "jsonl"] = "auto"
    id_column: str | None = None
    csv_encoding: str = "utf-8"
    fields_sent: dict[str, str]
    columns_preserved: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    missing_required: Literal["error"] = "error"
    field_limits: dict[str, FieldLimit] = Field(default_factory=dict)

    @field_validator("fields_sent")
    @classmethod
    def fields_must_exist(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("fields_sent must contain at least one mapping")
        if "record_id" in value:
            raise ValueError("record_id is reserved and cannot appear in fields_sent")
        return value

    @model_validator(mode="after")
    def validate_logical_fields(self):
        unknown_required = set(self.required_fields) - set(self.fields_sent)
        unknown_limits = set(self.field_limits) - set(self.fields_sent)
        if unknown_required:
            raise ValueError(f"required_fields not present in fields_sent: {sorted(unknown_required)}")
        if unknown_limits:
            raise ValueError(f"field_limits not present in fields_sent: {sorted(unknown_limits)}")
        reserved = {"record_id", "source_row", "provider", "model_requested", "model_returned", "run_id", "custom_id", "_kllm_truncated_fields"}
        collisions = (set(self.fields_sent) | set(self.columns_preserved)) & reserved
        alias_collisions = set(self.fields_sent) & set(self.columns_preserved)
        if collisions:
            raise ValueError(f"input mappings use package-reserved output names: {sorted(collisions)}")
        if alias_collisions:
            raise ValueError(f"logical fields and preserved columns overlap: {sorted(alias_collisions)}")
        return self


class TaskSection(StrictModel):
    rows_per_request: int = Field(default=1, gt=0)
    output_schema: Path
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int = Field(default=1000, gt=0)


class ContextFile(StrictModel):
    path: Path
    format: Literal["auto", "csv", "json", "yaml", "text"] = "auto"


class PromptSection(StrictModel):
    version: str
    system_file: Path
    user_file: Path
    context: dict[str, ContextFile] = Field(default_factory=dict)

    @field_validator("context")
    @classmethod
    def context_names_are_placeholders(cls, value: dict[str, ContextFile]):
        invalid = [name for name in value if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)]
        if invalid:
            raise ValueError(f"context names must be valid placeholder identifiers: {invalid}")
        return value


class ProviderSection(StrictModel):
    model: str
    max_requests_per_batch: int | None = Field(default=None, gt=0)
    max_batch_bytes: int | None = Field(default=None, gt=0)
    options: dict[str, Any] = Field(default_factory=dict)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)


class BudgetSection(StrictModel):
    max_estimated_usd: float = Field(gt=0)


class OutputSection(StrictModel):
    write_parquet: Literal[True] = True
    write_csv: bool = True
    runs_directory: Path = Path("runs")


class PilotSection(StrictModel):
    sample_size: int = Field(default=10, gt=0)
    random_seed: int = 42
    gold_columns: dict[str, str] = Field(default_factory=dict)


class ProjectConfig(StrictModel):
    version: Literal[1]
    project: ProjectSection
    input: InputSection
    task: TaskSection
    prompt: PromptSection
    providers: dict[str, ProviderSection]
    budget: BudgetSection
    output: OutputSection = Field(default_factory=OutputSection)
    pilot: PilotSection = Field(default_factory=PilotSection)
    config_path: Path = Field(exclude=True)
    base_dir: Path = Field(exclude=True)

    @field_validator("providers")
    @classmethod
    def supported_providers(cls, value: dict[str, ProviderSection]):
        unsupported = set(value) - {"openai", "anthropic"}
        if unsupported:
            raise ValueError(f"unsupported providers: {sorted(unsupported)}")
        if not value:
            raise ValueError("at least one provider is required")
        return value

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self.base_dir / path


def load_config(path: str | Path) -> ProjectConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration not found: {config_path}")
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("project configuration must be a YAML mapping")
    return ProjectConfig.model_validate(
        {**data, "config_path": config_path, "base_dir": config_path.parent}
    )
