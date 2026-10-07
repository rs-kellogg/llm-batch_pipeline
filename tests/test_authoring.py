from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

import kellogg_llm_batch.core as core
from kellogg_llm_batch.authoring import (
    ProjectDraftConflict,
    field_rows_to_schema,
    load_input_table,
    load_project_draft,
    new_project_draft,
    parse_provider_options_json,
    render_project_preview,
    render_project_yaml,
    save_project_draft,
    schema_supports_guided_editor,
    schema_to_field_rows,
)
from kellogg_llm_batch.core import prepare_run
from kellogg_llm_batch.scaffold import scaffold_project
from conftest import FakeAdapter


def _configured_draft(example_config: Path, tmp_path: Path):
    example = example_config.parent
    draft = new_project_draft(tmp_path / "gui-project")
    draft.config["project"] = {"name": "gui-grant-coding", "description": "GUI test"}
    draft.config["input"].update(
        {
            "path": str((example / "data" / "input-data.csv").resolve()),
            "id_column": "grant_id",
            "fields_sent": {"project_title": "project_title", "abstract": "abstract"},
            "columns_preserved": ["year", "investigator", "source_file"],
            "required_fields": ["abstract"],
        }
    )
    draft.schema = json.loads((example / "schema.json").read_text(encoding="utf-8"))
    draft.system_prompt = (example / "prompts" / "system.txt").read_text(encoding="utf-8")
    draft.user_prompt = (example / "prompts" / "user.txt").read_text(encoding="utf-8")
    draft.codebook_source = (example / "context" / "codebook.csv").resolve()
    draft.config["prompt"]["context"] = {
        "codebook": {"path": str(draft.codebook_source), "format": "auto"}
    }
    return draft


def test_supported_input_formats(tmp_path):
    frame = pd.DataFrame([{"record_id": "A", "text": "alpha"}, {"record_id": "B", "text": "beta"}])
    paths = {
        "csv": tmp_path / "input.csv",
        "parquet": tmp_path / "input.parquet",
        "jsonl": tmp_path / "input.jsonl",
    }
    frame.to_csv(paths["csv"], index=False)
    frame.to_parquet(paths["parquet"], index=False)
    frame.to_json(paths["jsonl"], orient="records", lines=True)

    for expected_format, path in paths.items():
        loaded, actual_format = load_input_table(path)
        assert actual_format == expected_format
        assert loaded["record_id"].astype(str).tolist() == ["A", "B"]


def test_new_gui_draft_keeps_generic_schema(tmp_path):
    draft = new_project_draft(tmp_path / "gui-project")
    assert list(draft.schema["properties"]) == ["label", "confidence", "justification"]
    assert "enum" not in draft.schema["properties"]["label"]
    assert draft.config["evaluation"]["gold_columns"] == {}
    assert draft.config["providers"]["openai"]["options"] == {
        "reasoning": {"effort": "low"}
    }
    assert draft.config["providers"]["anthropic"]["options"] == {
        "temperature": 0
    }


def test_provider_options_json_editor_parser():
    assert parse_provider_options_json('{"reasoning": {"effort": "low"}}') == {
        "reasoning": {"effort": "low"}
    }
    with pytest.raises(ValueError, match="valid JSON"):
        parse_provider_options_json('{"temperature":')
    with pytest.raises(ValueError, match="JSON object"):
        parse_provider_options_json("[]")


def test_gui_save_keeps_initialized_gold_mapping(tmp_path):
    project = scaffold_project(tmp_path / "project")
    draft = load_project_draft(project)
    assert draft.config["evaluation"]["gold_columns"] == {
        "primary_label": "reference_primary_label"
    }

    result = save_project_draft(draft)
    assert result.validation_report["valid"] is True
    saved = yaml.safe_load(result.project_file.read_text(encoding="utf-8"))
    assert saved["evaluation"]["gold_columns"] == {
        "primary_label": "reference_primary_label"
    }


def test_guided_schema_round_trip_and_nullable_enum():
    schema = field_rows_to_schema(
        [
            {
                "name": "label",
                "type": "string",
                "nullable": True,
                "enum_json": '["financial", "other"]',
                "description": "Primary code",
            },
            {"name": "confidence", "type": "number", "nullable": False, "enum_json": "", "description": ""},
        ]
    )
    assert schema["properties"]["label"]["type"] == ["string", "null"]
    assert schema["properties"]["label"]["enum"] == ["financial", "other", None]
    assert schema_supports_guided_editor(schema)
    assert field_rows_to_schema(schema_to_field_rows(schema)) == schema


def test_preview_save_validate_and_reopen_preserves_settings(example_config, tmp_path):
    draft = _configured_draft(example_config, tmp_path)
    original_schema = json.loads((example_config.parent / "schema.json").read_text(encoding="utf-8"))
    draft.config["providers"]["openai"]["options"] = {"temperature": 0}
    preview = render_project_preview(draft, source_row=4, row_count=3)

    assert preview.record_id == "GRANT-005"
    assert preview.record_ids == ["GRANT-005", "GRANT-006", "GRANT-007"]
    assert preview.source_rows == [4, 5, 6]
    assert '"record_id":"GRANT-005"' in preview.user_prompt
    assert '"record_id":"GRANT-006"' in preview.user_prompt
    assert '"record_id":"GRANT-007"' in preview.user_prompt
    assert '"abstract"' in preview.user_prompt
    assert "GRANT-001" not in preview.user_prompt

    yaml_preview = render_project_yaml(draft)
    assert yaml_preview.startswith("version: 1\n\nproject:\n")
    assert "\n\ninput:\n" in yaml_preview
    assert "\n\noutput:\n" in yaml_preview
    assert "\n  columns_preserved:\n    - year\n    - investigator\n    - source_file\n" in yaml_preview
    assert "\n  required_fields:\n    - abstract\n" in yaml_preview
    assert '\n  version: "1.0"\n' in yaml_preview
    saved = save_project_draft(draft)
    assert saved.project_file.read_text(encoding="utf-8") == yaml_preview
    saved_schema_text = (draft.project_dir / "schema.json").read_text(encoding="utf-8")
    assert saved_schema_text == json.dumps(original_schema, indent=2, ensure_ascii=False) + "\n"
    assert json.loads(saved_schema_text)["properties"]["secondary_label"]["type"] == ["string", "null"]
    assert saved.validation_report["valid"] is True
    assert saved.validation_report["source_rows"] == 10
    assert (draft.project_dir / "context" / "codebook.csv").is_file()
    assert len(pd.read_csv(example_config.parent / "data" / "input-data.csv")) == 10

    reopened = load_project_draft(draft.project_dir)
    assert reopened.config["providers"]["openai"]["options"] == {"temperature": 0}
    assert reopened.config["input"]["fields_sent"] == {
        "project_title": "project_title",
        "abstract": "abstract",
    }
    assert reopened.codebook_source == draft.project_dir / "context" / "codebook.csv"
    assert yaml.safe_load(yaml_preview) == reopened.config
    assert render_project_yaml(reopened) == yaml_preview
    assert save_project_draft(reopened).project_file.read_text(encoding="utf-8") == yaml_preview


def test_uploaded_input_is_copied_into_project_data(example_config, tmp_path):
    draft = _configured_draft(example_config, tmp_path)
    uploaded = tmp_path / "selected-grants.csv"
    uploaded.write_bytes((example_config.parent / "data" / "input-data.csv").read_bytes())
    draft.input_upload_source = uploaded
    draft.config["input"]["path"] = str(uploaded)

    result = save_project_draft(draft)
    saved_config = yaml.safe_load(result.project_file.read_text(encoding="utf-8"))

    assert saved_config["input"]["path"] == "data/selected-grants.csv"
    assert (draft.project_dir / "data" / "selected-grants.csv").read_bytes() == uploaded.read_bytes()
    assert draft.input_upload_source is None


def test_gui_generated_project_prepares_with_existing_pipeline(example_config, tmp_path, monkeypatch):
    draft = _configured_draft(example_config, tmp_path)
    project_file = save_project_draft(draft).project_file
    fake = FakeAdapter()
    monkeypatch.setattr(core, "get_provider", lambda name: fake)

    run = prepare_run(project_file, "openai", sample_size=2, seed=42)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["selected_rows"] == 2
    assert manifest["execution"] == "sync"
    assert (run / "api_requests" / "segment_0000.jsonl").is_file()


def test_save_blocks_external_file_change(example_config, tmp_path):
    draft = _configured_draft(example_config, tmp_path)
    save_project_draft(draft)
    reopened = load_project_draft(draft.project_dir)
    system_path = draft.project_dir / "prompts" / "system.txt"
    system_path.write_text(system_path.read_text(encoding="utf-8") + "External edit.\n", encoding="utf-8")

    with pytest.raises(ProjectDraftConflict, match="changed after the GUI loaded"):
        save_project_draft(reopened)


def test_new_project_without_codebook_uses_records_only_prompt(tmp_path):
    source = tmp_path / "source.csv"
    pd.DataFrame([{"id": "A", "text": "Alpha"}, {"id": "B", "text": "Beta"}]).to_csv(source, index=False)
    draft = new_project_draft(tmp_path / "records-only")
    draft.config["providers"] = {"openai": {"model": "gpt-5-mini"}}
    draft.config["input"].update(
        {
            "path": str(source),
            "id_column": "id",
            "fields_sent": {"text": "text"},
            "required_fields": ["text"],
        }
    )

    preview = render_project_preview(draft, source_row=0)
    result = save_project_draft(draft)

    assert preview.record_id == "A"
    assert "codebook" not in preview.user_prompt.lower()
    assert result.validation_report["valid"] is True
    with pytest.raises(FileExistsError, match="open it instead"):
        new_project_draft(draft.project_dir)


@pytest.mark.parametrize("same_content", [True, False])
def test_codebook_selection_preserves_unmanaged_context_file(tmp_path, same_content):
    source = tmp_path / "source.csv"
    pd.DataFrame([{"id": "A", "text": "Alpha"}]).to_csv(source, index=False)
    draft = new_project_draft(tmp_path / "project")
    draft.config["providers"] = {"openai": {"model": "gpt-5-mini"}}
    draft.config["input"].update(
        {
            "path": str(source),
            "id_column": "id",
            "fields_sent": {"text": "text"},
            "required_fields": ["text"],
        }
    )
    save_project_draft(draft)
    reopened = load_project_draft(draft.project_dir)
    existing = reopened.project_dir / "context" / "codebook.csv"
    existing.parent.mkdir(parents=True)
    existing.write_text("label,definition\nold,Existing\n", encoding="utf-8")
    selected = tmp_path / "selection" / "codebook.csv"
    selected.parent.mkdir()
    selected.write_text(
        existing.read_text(encoding="utf-8") if same_content else "label,definition\nnew,Selected\n",
        encoding="utf-8",
    )
    reopened.codebook_source = selected
    reopened.config["prompt"]["context"] = {
        "codebook": {"path": str(selected), "format": "auto"}
    }

    result = save_project_draft(reopened)
    saved = yaml.safe_load(result.project_file.read_text(encoding="utf-8"))
    saved_path = saved["prompt"]["context"]["codebook"]["path"]

    assert existing.read_text(encoding="utf-8") == "label,definition\nold,Existing\n"
    if same_content:
        assert saved_path == "context/codebook.csv"
    else:
        assert saved_path.startswith("context/codebook-")
        assert (reopened.project_dir / saved_path).read_text(encoding="utf-8") == selected.read_text(encoding="utf-8")


def test_complex_schema_requires_advanced_mode():
    schema = {
        "type": "object",
        "properties": {
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
        "required": ["evidence"],
        "additionalProperties": False,
    }
    assert schema_supports_guided_editor(schema) is False
    with pytest.raises(ValueError, match="advanced JSON"):
        schema_to_field_rows(schema)


def test_streamlit_gui_starts_without_api_calls(tmp_path, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    project = scaffold_project(tmp_path / "new-project")
    monkeypatch.setenv("KLLM_GUI_PROJECT_DIR", str(project))
    app_path = Path(__file__).parents[1] / "src" / "kellogg_llm_batch" / "gui_app.py"

    app = streamlit_testing.AppTest.from_file(str(app_path), default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "Kellogg LLM Batch Project Builder"
    assert any('font-size: 1rem' in str(block.value) for block in app.markdown)
    assert any('stWidgetLabel' in str(block.value) for block in app.markdown)
    assert any('white-space: pre-wrap' in str(block.value) for block in app.markdown)
    sidebar_text = "\n".join(str(block.value) for block in app.sidebar.markdown)
    assert "#input-data" in sidebar_text
    assert "#project" not in sidebar_text
    assert "#prompts-preview" in sidebar_text
    assert "Project directory" not in [item.label for item in app.text_input]
    assert "Create new draft" not in [button.label for button in app.button]
    assert any("kllm-batch init PATH" in message.value for message in app.info)
    assert any("Enum example" in message.value for message in app.info)
    assert "Preview source row (1-based)" not in [item.label for item in app.number_input]
    assert "Update JSON" in [button.label for button in app.button]
    assert "Update prompt preview" in [button.label for button in app.button]
    assert "Save project and validate" in [button.label for button in app.button]
    assert any("Saving overwrites the existing managed" in warning.value for warning in app.warning)
    captions = "\n".join(caption.value for caption in app.caption)
    assert "data/input-data.csv" in captions
    assert "context/" in captions
    markdown_text = "\n".join(str(block.value) for block in app.markdown)
    assert "prompts/system.txt" in markdown_text
    assert "prompts/user.txt" in markdown_text
    assert "kllm-save-note" in markdown_text
    yaml_blocks = [str(block.value) for block in app.code if "project:" in str(block.value)]
    assert yaml_blocks
    yaml_text = yaml_blocks[0]
    ordered_keys = ["version:", "project:", "input:", "task:", "prompt:", "providers:", "budget:", "evaluation:", "output:"]
    assert [yaml_text.index(key) for key in ordered_keys] == sorted(yaml_text.index(key) for key in ordered_keys)
    next(item for item in app.text_input if item.label == "Project name").set_value("renamed-project").run()
    updated_yaml = next(str(block.value) for block in app.code if "project:" in str(block.value))
    assert "name: renamed-project" in updated_yaml


def test_streamlit_gui_file_pickers_load_input_and_codebook(tmp_path, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    project = scaffold_project(tmp_path / "new-project")
    monkeypatch.setenv("KLLM_GUI_PROJECT_DIR", str(project))
    app_path = Path(__file__).parents[1] / "src" / "kellogg_llm_batch" / "gui_app.py"
    app = streamlit_testing.AppTest.from_file(str(app_path), default_timeout=10).run()

    app.file_uploader[0].set_value(
        ("records.csv", b"record_id,text\nA,Alpha\nB,Beta\n", "text/csv")
    ).run()
    app.file_uploader[1].set_value(
        ("codebook.csv", b"label,definition\nalpha,First label\n", "text/csv")
    ).run()

    assert not app.exception
    assert len(app.dataframe) >= 1
    assert any("Prompt placeholder" in caption.value for caption in app.caption)
    assert any("Stable ID column" in str(block.value) for block in app.markdown)


def test_streamlit_schema_modes_round_trip_without_losing_guided_data(tmp_path, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    project = scaffold_project(tmp_path / "schema-project")
    monkeypatch.setenv("KLLM_GUI_PROJECT_DIR", str(project))
    app_path = Path(__file__).parents[1] / "src" / "kellogg_llm_batch" / "gui_app.py"
    app = streamlit_testing.AppTest.from_file(str(app_path), default_timeout=10).run()

    guided_rows = list(app.session_state["schema_rows"])
    guided_rows[0] = {**guided_rows[0], "name": "guided_label"}
    app.session_state["schema_rows"] = guided_rows
    app.radio[0].set_value("Advanced JSON").run()
    schema_editor = next(item for item in app.text_area if item.label == "schema.json")
    assert '"guided_label"' in schema_editor.value

    advanced_schema = {
        "type": "object",
        "properties": {"advanced_label": {"type": "string"}},
        "required": ["advanced_label"],
        "additionalProperties": False,
    }
    schema_editor.set_value(json.dumps(advanced_schema, indent=2)).run()
    app.radio[0].set_value("Guided").run()

    assert not app.exception
    assert [row["name"] for row in app.session_state["schema_rows"]] == ["advanced_label"]

    app.radio[0].set_value("Advanced JSON").run()
    complex_schema = {
        "type": "object",
        "properties": {"evidence": {"type": "array", "items": {"type": "string"}}},
        "required": ["evidence"],
        "additionalProperties": False,
    }
    next(item for item in app.text_area if item.label == "schema.json").set_value(
        json.dumps(complex_schema, indent=2)
    ).run()
    next(button for button in app.button if button.label == "Validate and use JSON").click().run()
    app.radio[0].set_value("Guided").run()
    assert any("advanced features" in error.value for error in app.error)
    app.radio[0].set_value("Advanced JSON").run()

    assert '"evidence"' in next(item for item in app.text_area if item.label == "schema.json").value


def test_streamlit_gui_loads_existing_data_codebook_and_preview(example_config, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    monkeypatch.setenv("KLLM_GUI_PROJECT_DIR", str(example_config.parent))
    app_path = Path(__file__).parents[1] / "src" / "kellogg_llm_batch" / "gui_app.py"

    app = streamlit_testing.AppTest.from_file(str(app_path), default_timeout=10).run()
    next(button for button in app.button if button.label == "Load current project input").click().run()
    assert not app.exception
    assert len(app.dataframe) >= 2
    next(button for button in app.button if button.label == "Load current project codebook").click().run()
    assert not app.exception
    next(button for button in app.button if button.label == "Update prompt preview").click().run()

    assert not app.exception
    rendered = "\n".join(str(block.value) for block in app.code)
    assert "GRANT-001" in rendered
    assert "GRANT-002" in rendered
    assert "GRANT-003" in rendered
    assert "GRANT-004" not in rendered
    assert "financial" in rendered
    assert any("Source rows 1–3" in caption.value for caption in app.caption)
    next(item for item in app.text_area if item.label == "System prompt").set_value("Changed system prompt").run()
    assert any("Update the preview" in warning.value for warning in app.warning)
    assert any("GRANT-001" in str(block.value) for block in app.code)


def test_streamlit_gui_reopens_saves_and_validates(example_config, tmp_path, monkeypatch):
    streamlit_testing = pytest.importorskip("streamlit.testing.v1")
    draft = _configured_draft(example_config, tmp_path)
    save_project_draft(draft)
    monkeypatch.setenv("KLLM_GUI_PROJECT_DIR", str(draft.project_dir))
    app_path = Path(__file__).parents[1] / "src" / "kellogg_llm_batch" / "gui_app.py"

    app = streamlit_testing.AppTest.from_file(str(app_path), default_timeout=10).run()
    next(button for button in app.button if button.label == "Load current project input").click().run()
    next(button for button in app.button if button.label == "Load current project codebook").click().run()
    next(button for button in app.button if button.label == "Save project and validate").click().run()

    assert not app.exception
    messages = [message.value for message in app.success]
    assert any("Saved project files" in message for message in messages)
    assert any("Validation passed" in message for message in messages)
