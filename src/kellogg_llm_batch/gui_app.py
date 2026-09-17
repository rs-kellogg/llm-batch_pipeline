from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from kellogg_llm_batch.authoring import (
    DEFAULT_USER_PROMPT,
    ProjectDraft,
    ProjectDraftConflict,
    field_rows_to_schema,
    load_input_table,
    load_project_draft,
    render_project_preview,
    save_project_draft,
    schema_supports_guided_editor,
    schema_to_field_rows,
)
from kellogg_llm_batch.data import normalize_id
from kellogg_llm_batch.prompts import load_context_file


def _apply_app_styles() -> None:
    """Keep secondary instructions readable at the larger app scale."""
    st.markdown(
        """
        <style>
        [data-testid="stCaptionContainer"] p {
            font-size: 1rem !important;
            line-height: 1.55 !important;
        }
        [data-testid="stWidgetLabel"] p,
        [data-testid="stWidgetLabel"] label,
        [data-testid="stFileUploaderDropzone"] span,
        [data-testid="stFileUploaderDropzone"] small,
        [data-testid="stExpander"] summary p,
        .stButton button p {
            font-size: 1rem !important;
            line-height: 1.45 !important;
        }
        [data-testid="stWidgetLabel"] p,
        [data-testid="stWidgetLabel"] label {
            font-weight: 500 !important;
        }
        .stTextInput input,
        .stTextArea textarea,
        .stNumberInput input,
        [data-baseweb="select"] {
            font-size: 1rem !important;
        }
        [data-testid="stCode"] code {
            font-size: 1rem !important;
            line-height: 1.5 !important;
        }
        [data-testid="stCode"] pre,
        [data-testid="stCode"] code {
            white-space: pre-wrap !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar_navigation() -> None:
    with st.sidebar:
        st.markdown("## Sections")
        st.markdown(
            """
            - [1. Input data and column mapping](#input-data)
            - [2. Codebook](#codebook)
            - [3. Output schema](#output-schema)
            - [4. Prompts and request preview](#prompts-preview)
            - [5. Settings, save, and validate](#settings-save)
            """
        )


def _resolved_config_path(draft: ProjectDraft, value: str) -> str:
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (draft.project_dir / path).resolve())


def _stage_upload(uploaded: Any, category: str) -> tuple[Path, str]:
    """Keep a browser upload outside the project until the user saves."""
    content = uploaded.getvalue()
    filename = Path(uploaded.name).name
    signature = f"{filename}:{sha256(content).hexdigest()}"
    directory = st.session_state.get("upload_staging_directory")
    if directory is None:
        directory = tempfile.TemporaryDirectory(prefix="kllm-gui-upload-")
        st.session_state.upload_staging_directory = directory
    target = Path(directory.name) / category / filename
    if st.session_state.get(f"{category}_upload_signature") != signature or not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        st.session_state[f"{category}_upload_signature"] = signature
    return target, signature


def _initialize_draft(draft: ProjectDraft) -> None:
    st.session_state.draft = draft
    st.session_state.project_directory = str(draft.project_dir)
    st.session_state.system_prompt = draft.system_prompt
    st.session_state.user_prompt = draft.user_prompt
    guided = schema_supports_guided_editor(draft.schema)
    st.session_state.schema_mode = "Guided" if guided else "Advanced JSON"
    st.session_state.schema_rows = schema_to_field_rows(draft.schema) if guided else []
    st.session_state.schema_json = json.dumps(draft.schema, indent=2, ensure_ascii=False)
    st.session_state.upload_widget_generation = st.session_state.get("upload_widget_generation", 0) + 1
    st.session_state.pop("input_frame", None)
    st.session_state.pop("column_rows", None)
    st.session_state.pop("codebook_preview", None)
    st.session_state.pop("input_upload_processed", None)
    st.session_state.pop("codebook_upload_processed", None)


def _load_initial_draft() -> None:
    if "draft" in st.session_state:
        return
    supplied = os.environ.get("KLLM_GUI_PROJECT_DIR")
    if not supplied:
        raise RuntimeError("No initialized project was supplied. Run kllm-batch init PATH, then kllm-batch gui PATH.")
    _initialize_draft(load_project_draft(Path(supplied).expanduser().resolve()))


def _column_editor_rows(draft: ProjectDraft, frame: pd.DataFrame) -> pd.DataFrame:
    sent_by_source = {source: logical for logical, source in draft.config["input"].get("fields_sent", {}).items()}
    required = set(draft.config["input"].get("required_fields", []))
    preserved = set(draft.config["input"].get("columns_preserved", []))
    rows = []
    for column in frame.columns:
        source = str(column)
        logical = sent_by_source.get(source, source)
        rows.append(
            {
                "source_column": source,
                "send_to_model": source in sent_by_source,
                "model_field_name": logical,
                "required": logical in required,
                "preserve_locally": source in preserved,
            }
        )
    return pd.DataFrame(rows)


def _apply_column_editor(draft: ProjectDraft, edited: pd.DataFrame, id_column: str) -> None:
    sent: dict[str, str] = {}
    required: list[str] = []
    preserved: list[str] = []
    for row in edited.to_dict("records"):
        source = str(row["source_column"])
        if bool(row["send_to_model"]):
            logical = str(row["model_field_name"]).strip()
            if not logical:
                raise ValueError(f"Model-facing name is empty for source column {source!r}")
            if logical in sent:
                raise ValueError(f"Duplicate model-facing field name: {logical}")
            sent[logical] = source
            if bool(row["required"]):
                required.append(logical)
        elif bool(row["preserve_locally"]):
            preserved.append(source)
    draft.config["input"]["id_column"] = None if id_column == "Generate deterministic IDs" else id_column
    draft.config["input"]["fields_sent"] = sent
    draft.config["input"]["required_fields"] = required
    draft.config["input"]["columns_preserved"] = preserved


def _show_validation(report: dict[str, Any]) -> None:
    if report["valid"]:
        st.success(f"Validation passed: {report['source_rows']:,} rows and {report['request_count']:,} requests.")
    else:
        st.error("Project saved, but validation found issues that must be fixed before preparation.")
    for finding in report.get("findings", []):
        renderer = st.error if finding["severity"] == "error" else st.warning
        renderer(f"{finding['code']}: {finding['message']}")
    if report.get("cost_estimates"):
        rows = []
        for provider, estimate in report["cost_estimates"].items():
            rows.append(
                {
                    "provider": provider,
                    "requests": estimate["request_count"],
                    "estimated_maximum_usd": estimate["estimated_usd"],
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _input_controls(draft: ProjectDraft) -> None:
    st.subheader("1. Input data and column mapping", anchor="input-data")
    configured_path = str(draft.config["input"].get("path", ""))
    current_path = _resolved_config_path(draft, configured_path) if configured_path else ""
    if current_path:
        st.info(f"Current input: {current_path}")
    uploaded = st.file_uploader(
        "Choose input data",
        type=["csv", "parquet", "jsonl"],
        key=f"input_upload_{st.session_state.upload_widget_generation}",
        help="The selected file is copied into data/ only when you save the project.",
    )
    if uploaded is not None:
        try:
            source, signature = _stage_upload(uploaded, "input")
            if st.session_state.get("input_upload_processed") != signature:
                frame, fmt = load_input_table(source)
                draft.input_upload_source = source
                draft.config["input"]["path"] = str(source)
                draft.config["input"]["format"] = "auto"
                st.session_state.input_frame = frame
                st.session_state.input_format = fmt
                st.session_state.column_rows = _column_editor_rows(draft, frame)
                st.session_state.input_upload_processed = signature
                st.success(f"Selected {uploaded.name}: {len(frame):,} rows and {len(frame.columns):,} columns.")
        except Exception as exc:
            st.error(str(exc))
    if st.button("Load current project input", disabled=not bool(current_path), width="stretch"):
        try:
            frame, fmt = load_input_table(current_path)
            st.session_state.input_frame = frame
            st.session_state.input_format = fmt
            st.session_state.column_rows = _column_editor_rows(draft, frame)
            st.success(f"Loaded {len(frame):,} rows and {len(frame.columns):,} columns.")
        except Exception as exc:
            st.error(str(exc))
    frame: pd.DataFrame | None = st.session_state.get("input_frame")
    if frame is None:
        return
    st.caption(f"Format: {st.session_state.input_format}; showing the first 20 rows.")
    st.dataframe(frame.head(20), width="stretch")
    id_options = ["Generate deterministic IDs", *map(str, frame.columns)]
    configured_id = draft.config["input"].get("id_column")
    id_index = id_options.index(configured_id) if configured_id in id_options else 0
    id_column = st.selectbox("Stable ID column", id_options, index=id_index)
    edited = st.data_editor(
        st.session_state.column_rows,
        key="column_mapping_editor",
        hide_index=True,
        width="stretch",
        disabled=["source_column"],
        column_config={
            "source_column": "Source column",
            "send_to_model": "Send to model",
            "model_field_name": "Model-facing name",
            "required": "Reject empty values",
            "preserve_locally": "Preserve locally",
        },
    )
    try:
        _apply_column_editor(draft, edited, id_column)
        st.session_state.column_rows = edited
        if id_column != "Generate deterministic IDs":
            normalized = frame[id_column].map(normalize_id)
            missing = int(normalized.isna().sum())
            duplicates = int(normalized.dropna().duplicated(keep=False).sum())
            if missing or duplicates:
                st.warning(f"ID check: {missing} missing and {duplicates} rows involved in duplicate IDs.")
    except Exception as exc:
        st.error(str(exc))


def _codebook_controls(draft: ProjectDraft) -> None:
    st.subheader("2. Codebook", anchor="codebook")
    current_path = str(draft.codebook_source or "")
    if current_path:
        st.info(f"Current codebook: {current_path}")
    uploaded = st.file_uploader(
        "Choose an optional codebook",
        type=["csv", "json", "jsonl", "yaml", "yml", "txt", "md"],
        key=f"codebook_upload_{st.session_state.upload_widget_generation}",
        help="The selected file is copied into context/ only when you save the project.",
    )
    if uploaded is not None:
        try:
            source, signature = _stage_upload(uploaded, "codebook")
            if st.session_state.get("codebook_upload_processed") != signature:
                _load_codebook(draft, source)
                st.session_state.codebook_upload_processed = signature
        except Exception as exc:
            st.error(str(exc))
    if st.button("Load current project codebook", disabled=not bool(current_path), width="stretch"):
        try:
            _load_codebook(draft, Path(current_path))
        except Exception as exc:
            st.error(str(exc))
    preview = st.session_state.get("codebook_preview")
    if preview:
        placeholder, value = next(iter(preview.items()))
        st.caption(f"Prompt placeholder: ${{{placeholder}}}. The file will be copied into context/ when saved.")
        try:
            st.json(json.loads(value))
        except json.JSONDecodeError:
            st.code(value)


def _load_codebook(draft: ProjectDraft, source: Path) -> None:
    preview = load_context_file("codebook", source, "auto")
    draft.codebook_source = source
    suffix = "_text" if "codebook_text" in preview else "_json"
    draft.config["prompt"].setdefault("context", {})["codebook"] = {
        "path": str(source),
        "format": "auto",
    }
    expected = f"${{codebook{suffix}}}"
    if st.session_state.user_prompt == DEFAULT_USER_PROMPT:
        st.session_state.user_prompt = f"Apply this codebook:\n\n{expected}\n\n{DEFAULT_USER_PROMPT}"
    st.session_state.codebook_preview = preview


def _schema_controls(draft: ProjectDraft) -> None:
    st.subheader("3. Output schema", anchor="output-schema")
    mode = st.radio("Editor mode", ["Guided", "Advanced JSON"], horizontal=True, key="schema_mode")
    if mode == "Guided":
        st.info(
            'Enum example: `["financial", "organizational", "technical", "other"]`. '
            "Use double quotes and square brackets; leave the cell blank when a field has no fixed choices."
        )
        rows = pd.DataFrame(st.session_state.schema_rows)
        edited = st.data_editor(
            rows,
            key="schema_field_editor",
            num_rows="dynamic",
            hide_index=True,
            width="stretch",
            column_config={
                "name": "Output field",
                "type": st.column_config.SelectboxColumn("Type", options=["string", "integer", "number", "boolean"]),
                "nullable": "Allow null",
                "enum_json": st.column_config.TextColumn(
                    "Optional enum as JSON array",
                    help='Example: ["financial", "organizational", "technical", "other"]',
                ),
                "description": "Description",
                "minimum": "Minimum (numeric only)",
                "maximum": "Maximum (numeric only)",
            },
        )
        st.session_state.schema_rows = edited.to_dict("records")
        try:
            draft.schema = field_rows_to_schema(st.session_state.schema_rows)
            st.session_state.schema_json = json.dumps(draft.schema, indent=2, ensure_ascii=False)
            st.json(draft.schema)
        except Exception as exc:
            st.error(str(exc))
    else:
        st.text_area("schema.json", key="schema_json", height=360)
        try:
            parsed = json.loads(st.session_state.schema_json)
            if not isinstance(parsed, dict):
                raise ValueError("Schema must be a JSON object")
            draft.schema = parsed
        except Exception as exc:
            st.error(f"Schema JSON: {exc}")


def _prompt_controls(draft: ProjectDraft) -> None:
    st.subheader("4. Prompts and request preview", anchor="prompts-preview")
    st.text_area("System prompt", key="system_prompt", height=160)
    st.text_area("User prompt template", key="user_prompt", height=220)
    draft.system_prompt = st.session_state.system_prompt
    draft.user_prompt = st.session_state.user_prompt
    task = draft.config["task"]
    rows_per_request = int(
        st.number_input(
            "Rows per API request",
            min_value=1,
            value=int(task.get("rows_per_request", 10)),
            help="The preview starts at the first source row and uses this many records, matching a real request.",
        )
    )
    task["rows_per_request"] = rows_per_request
    if st.button("Render prompt preview"):
        try:
            preview = render_project_preview(draft, source_row=0, row_count=rows_per_request)
            first_row = preview.source_rows[0] + 1
            last_row = preview.source_rows[-1] + 1
            st.caption(
                f"Previewing source rows {first_row}–{last_row} ({len(preview.records)} records), "
                f"matching rows_per_request={rows_per_request}. This does not filter the saved project."
            )
            st.markdown("**System prompt sent to the provider**")
            st.code(preview.system_prompt, language=None, wrap_lines=True)
            st.markdown("**Rendered user prompt sent to the provider**")
            st.code(preview.user_prompt, language="json", wrap_lines=True)
        except Exception as exc:
            st.error(str(exc))


def _settings_and_save(draft: ProjectDraft) -> None:
    st.subheader("5. Settings, save, and validate", anchor="settings-save")
    config = draft.config
    project = config["project"]
    task = config["task"]
    prompt = config["prompt"]
    budget = config["budget"]
    evaluation = config.setdefault("evaluation", {"random_seed": 42, "gold_columns": {}})
    project["name"] = st.text_input("Project name", value=project.get("name", ""))
    project["description"] = st.text_input("Description", value=project.get("description", ""))
    prompt["version"] = st.text_input("Prompt version", value=str(prompt.get("version", "1.0")))
    with st.expander("Provider, token, and budget settings"):
        existing = config.get("providers", {})
        selected = st.multiselect("Providers", ["openai", "anthropic"], default=list(existing) or ["openai"])
        providers = {}
        defaults = {"openai": "gpt-5-mini", "anthropic": "claude-haiku-4-5"}
        for name in selected:
            item = dict(existing.get(name, {}))
            item["model"] = st.text_input(f"{name} model", value=item.get("model", defaults[name]), key=f"model_{name}")
            providers[name] = item
        config["providers"] = providers
        task["max_input_tokens"] = int(st.number_input("Maximum input tokens", min_value=1, value=int(task.get("max_input_tokens") or 50000)))
        task["max_output_tokens"] = int(st.number_input("Maximum output tokens", min_value=1, value=int(task.get("max_output_tokens", 2000))))
        budget["max_estimated_usd"] = float(st.number_input("Maximum estimated USD", min_value=0.01, value=float(budget.get("max_estimated_usd", 10.0))))
        evaluation["random_seed"] = int(st.number_input("Random seed", value=int(evaluation.get("random_seed", 42))))

    if st.button("Save project and validate", type="primary", width="stretch"):
        try:
            draft.project_dir = Path(st.session_state.project_directory).expanduser().resolve()
            draft.system_prompt = st.session_state.system_prompt
            draft.user_prompt = st.session_state.user_prompt
            result = save_project_draft(draft)
            st.success(f"Saved project files to {result.project_file.parent}")
            _show_validation(result.validation_report)
        except ProjectDraftConflict as exc:
            st.error(f"Save blocked: {exc}")
        except Exception as exc:
            st.error(str(exc))


def run_app() -> None:
    st.set_page_config(page_title="Kellogg LLM Batch Project Builder", layout="wide")
    _apply_app_styles()
    _render_sidebar_navigation()
    st.title("Kellogg LLM Batch Project Builder")
    st.info(
        "This GUI edits an initialized project. To create another project, run "
        "`kllm-batch init PATH`, then open it with `kllm-batch gui PATH`."
    )
    st.caption("Build and validate project files locally. This interface never submits API requests.")
    _load_initial_draft()
    draft: ProjectDraft = st.session_state.draft
    st.caption(f"Editing initialized project: {draft.project_dir}")
    _input_controls(draft)
    _codebook_controls(draft)
    _schema_controls(draft)
    _prompt_controls(draft)
    _settings_and_save(draft)


run_app()
