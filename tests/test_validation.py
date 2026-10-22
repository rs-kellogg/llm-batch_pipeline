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


def test_multiple_sampling_controls_warn(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["providers"]["openai"]["options"] = {"temperature": 0.2, "top_p": 0.9}
    config = tmp_path / "multiple-sampling-controls.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    report = validate_project(config)

    assert report["valid"] is True
    assert "multiple_sampling_controls" in {
        finding["code"] for finding in report["findings"]
    }


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_generation_seed_is_rejected_with_selection_guidance(
    example_config, tmp_path, provider
):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["providers"][provider]["options"] = {"seed": 42}
    config = tmp_path / f"invalid-{provider}-seed.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ProjectValidationError) as exc:
        validate_project(config)

    assert "select input rows only" in str(exc.value)


def test_openai_compatible_requires_base_url(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["providers"]["openai_compatible"] = {"model": "llama3.1:8b"}
    config = tmp_path / "missing-base-url.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ProjectValidationError) as exc:
        validate_project(config)

    assert "base_url is required" in str(exc.value)


def test_openai_compatible_validates_with_base_url_and_sync_pricing(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["providers"]["openai_compatible"] = {
        "model": "llama3.1:8b",
        "base_url": "http://localhost:11434/v1",
        "sync_input_price_per_million": 0,
        "sync_output_price_per_million": 0,
    }
    config = tmp_path / "openai-compatible.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    report = validate_project(config)

    assert report["valid"] is True
    assert report["cost_estimates"]["openai_compatible"]["estimated_usd"] is None


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
    findings_by_code = {finding["code"]: finding for finding in exc.value.report["findings"]}
    assert code in findings_by_code
    assert findings_by_code[code]["source_rows"] == [0, 1]
    assert "source rows [0, 1]" in findings_by_code[code]["message"]
    assert not (tmp_path / "runs").exists()


def test_generated_ids_are_deterministic(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["input"]["id_column"] = None
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    first = validate_project(config)
    second = validate_project(config)
    assert first["valid"] and second["valid"]


@pytest.mark.parametrize("fmt", ["parquet", "jsonl"])
def test_supported_non_csv_formats(example_config, tmp_path, fmt):
    frame = pd.read_csv(example_config.parent / "data" / "input-data.csv")
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
    frame = pd.read_csv(example_config.parent / "data" / "input-data.csv")
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
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
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
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    logical_name = next(iter(raw["input"]["fields_sent"]))
    raw["input"]["fields_sent"][logical_name] = "column_that_does_not_exist"
    config = tmp_path / "project.yaml"
    report = tmp_path / "validation.json"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ProjectValidationError):
        validate_project(config, report)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["valid"] is False
    assert payload["findings"][0]["code"] == "validation_error"


def test_fields_sent_accepts_arbitrary_names_and_multiple_mappings(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["input"]["fields_sent"] = {
        "research_heading": "project_title",
        "document_body": "abstract",
        "award_year_seen_by_model": "year",
    }
    raw["input"]["required_fields"] = ["document_body"]
    raw["input"]["field_limits"] = {}
    config = tmp_path / "arbitrary-fields.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    report = validate_project(config)
    assert report["valid"] is True
    assert report["canonical_rows"] == 10


@pytest.mark.parametrize(
    ("gold_columns", "code"),
    [
        ({"primary_label": "missing_human_label"}, "missing_gold_column"),
        ({"not_in_schema": "year"}, "unknown_gold_output_field"),
    ],
)
def test_evaluation_gold_mapping_is_validated(example_config, tmp_path, gold_columns, code):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["evaluation"]["gold_columns"] = gold_columns
    config = tmp_path / "invalid-gold.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ProjectValidationError) as exc:
        validate_project(config)
    assert code in {finding["code"] for finding in exc.value.report["findings"]}


def test_field_placeholders_render_one_record_with_null_as_empty():
    from kellogg_llm_batch.prompts import render_user_prompt, validate_template

    template = "<t>${field_title}</t> by ${field_author} (${field_record_id})"
    validate_template(template, {}, ["title", "author"], rows_per_request=1)
    records = [{"record_id": "7", "title": "Solar $cells", "author": None}]
    assert render_user_prompt(template, records, {}) == "<t>Solar $cells</t> by  (7)"


def test_field_placeholders_validation_errors():
    from kellogg_llm_batch.prompts import validate_template

    with pytest.raises(ValueError, match="rows_per_request must be 1"):
        validate_template("${field_title}", {}, ["title"], rows_per_request=3)
    with pytest.raises(ValueError, match="Unknown prompt placeholders"):
        validate_template("${field_missing}", {}, ["title"], rows_per_request=1)
    with pytest.raises(ValueError, match="must contain"):
        validate_template("no placeholders", {}, ["title"], rows_per_request=1)
    # existing behavior unchanged: records_json alone is valid at any batch size
    validate_template("${records_json}", {}, ["title"], rows_per_request=3)


def test_field_placeholders_end_to_end_validate_project(example_config, tmp_path):
    raw = yaml.safe_load(example_config.read_text())
    _absolute_support_paths(raw, example_config)
    raw["input"]["path"] = str(example_config.parent / "data" / "input-data.csv")
    raw["task"]["rows_per_request"] = 1
    user_prompt = tmp_path / "user.txt"
    user_prompt.write_text("Codebook: ${codebook_json}\n<abstract>${field_abstract}</abstract>\n", encoding="utf-8")
    raw["prompt"]["user_file"] = str(user_prompt)
    config = tmp_path / "field-placeholders.yaml"
    config.write_text(yaml.safe_dump(raw), encoding="utf-8")

    report = validate_project(config)

    assert report["valid"] is True
    assert report["request_count"] == report["source_rows"]
