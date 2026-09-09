from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from kellogg_llm_batch.validation import ProjectValidationError, validate_project


def _absolute_support_paths(raw, example_config):
    raw["task"]["output_schema"] = str(example_config.parent / "schema.json")
    raw["prompt"]["system_file"] = str(example_config.parent / "prompts" / "system.txt")
    raw["prompt"]["user_file"] = str(example_config.parent / "prompts" / "user.txt")
    raw["prompt"]["context"]["codebook"]["path"] = str(example_config.parent / "context" / "codebook.csv")


def test_example_validates(example_config):
    report = validate_project(example_config)
    assert report["valid"] is True
    assert report["source_rows"] == 10
    assert report["request_count"] == 4


@pytest.mark.parametrize(
    ("filename", "code"),
    [("invalid_duplicate_ids.csv", "duplicate_record_id"), ("invalid_duplicate_content.csv", "duplicate_content")],
)
def test_duplicate_inputs_block_before_run(example_config, tmp_path, filename, code):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / filename)
    raw["output"]["runs_directory"] = str(tmp_path / "runs")
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(config)
    assert code in {finding["code"] for finding in exc.value.report["findings"]}
    assert not (tmp_path / "runs").exists()


def test_generated_ids_are_deterministic(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "grants.csv")
    raw["input"]["id_column"] = None
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    first = validate_project(config)
    second = validate_project(config)
    assert first["valid"] and second["valid"]


@pytest.mark.parametrize("fmt", ["parquet", "jsonl"])
def test_supported_non_csv_formats(example_config, tmp_path, fmt):
    frame = pd.read_csv(example_config.parent / "data" / "grants.csv")
    source = tmp_path / ("grants.parquet" if fmt == "parquet" else "grants.jsonl")
    if fmt == "parquet":
        frame.to_parquet(source, index=False)
    else:
        frame.to_json(source, orient="records", lines=True)
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(source)
    raw["input"]["format"] = "auto"
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert validate_project(config)["source_rows"] == 10


def test_missing_configured_id_is_blocking(example_config, tmp_path):
    frame = pd.read_csv(example_config.parent / "data" / "grants.csv")
    frame.loc[0, "grant_id"] = None
    source = tmp_path / "missing_id.csv"
    frame.to_csv(source, index=False)
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(source)
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ProjectValidationError) as exc:
        validate_project(config)
    assert "missing_record_id" in {finding["code"] for finding in exc.value.report["findings"]}


def test_schema_requires_nullable_instead_of_optional(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "grants.csv")
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object","properties":{"label":{"type":"string"}},"required":[],"additionalProperties":false}', encoding="utf-8")
    raw["task"]["output_schema"] = str(schema)
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="make optional values nullable"):
        validate_project(config)


def test_report_is_written_for_structural_validation_error(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "grants.csv")
    raw["input"]["fields_sent"]["text"] = "column_that_does_not_exist"
    config = tmp_path / "project.yaml"
    report = tmp_path / "validation.json"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ProjectValidationError):
        validate_project(config, report)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["valid"] is False
    assert payload["findings"][0]["code"] == "validation_error"
