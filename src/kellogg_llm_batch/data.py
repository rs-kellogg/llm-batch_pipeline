from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import ProjectConfig
from .models import CanonicalRecord
from .utils import sha256_file


def infer_format(path: Path, configured: str) -> str:
    if configured != "auto":
        return configured
    formats = {".csv": "csv", ".parquet": "parquet", ".jsonl": "jsonl", ".ndjson": "jsonl"}
    suffix = path.suffix.lower()
    if suffix not in formats:
        raise ValueError(f"Cannot infer input format from '{suffix}'. Set input.format explicitly.")
    return formats[suffix]


def read_table(path: Path, fmt: str, csv_encoding: str = "utf-8") -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input not found: {path}")
    if fmt == "csv":
        return pd.read_csv(path, encoding=csv_encoding, low_memory=False, dtype=object)
    if fmt == "parquet":
        return pd.read_parquet(path)
    if fmt == "jsonl":
        return pd.read_json(path, lines=True)
    raise ValueError(f"Unsupported input format: {fmt}")


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        return normalized or None
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (int, float, bool, list, dict)):
        return value
    raise TypeError(f"unsupported value type: {type(value).__name__}")


def normalize_id(value: Any) -> str | None:
    normalized = normalize_value(value)
    return None if normalized is None else str(normalized).strip() or None


def load_source(config: ProjectConfig) -> tuple[pd.DataFrame, Path, str]:
    source = config.resolve(config.input.path)
    fmt = infer_format(source, config.input.format)
    return read_table(source, fmt, config.input.csv_encoding), source, fmt


def required_source_columns(config: ProjectConfig) -> set[str]:
    columns = set(config.input.fields_sent.values()) | set(config.input.columns_preserved)
    if config.input.id_column:
        columns.add(config.input.id_column)
    return columns


def canonicalize(config: ProjectConfig, df: pd.DataFrame, source: Path) -> list[CanonicalRecord]:
    missing_columns = sorted(required_source_columns(config) - set(df.columns))
    if missing_columns:
        raise ValueError(f"Input is missing configured columns: {missing_columns}")
    checksum = sha256_file(source)
    records: list[CanonicalRecord] = []
    for source_row, (_, row) in enumerate(df.iterrows()):
        if config.input.id_column:
            record_id = normalize_id(row[config.input.id_column]) or ""
        else:
            record_id = "gen_" + hashlib.sha256(f"{checksum}:{source_row}".encode()).hexdigest()[:24]
        sent: dict[str, Any] = {}
        truncated: dict[str, dict[str, int]] = {}
        for logical, source_column in config.input.fields_sent.items():
            value = normalize_value(row[source_column])
            limit = config.input.field_limits.get(logical)
            if limit and limit.max_characters and isinstance(value, str) and len(value) > limit.max_characters:
                if limit.overflow == "error":
                    raise ValueError(
                        f"row {source_row}, field '{logical}' has {len(value)} characters; "
                        f"limit is {limit.max_characters}"
                    )
                original_length = len(value)
                value = value[: limit.max_characters]
                truncated[logical] = {"original_length": original_length, "sent_length": len(value)}
            sent[logical] = value
        preserved = {name: normalize_value(row[name]) for name in config.input.columns_preserved}
        records.append(
            CanonicalRecord(
                record_id=record_id,
                source_row=source_row,
                sent=sent,
                preserved=preserved,
                truncated_fields=truncated,
            )
        )
    return records


def record_content_key(record: CanonicalRecord) -> str:
    return json.dumps(record.sent, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

