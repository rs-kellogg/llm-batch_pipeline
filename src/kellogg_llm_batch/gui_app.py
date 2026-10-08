from __future__ import annotations

import json
import os
import tempfile
from html import escape
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
    parse_provider_options_json,
    render_project_preview,
    render_project_yaml,
    save_project_draft,
    schema_supports_guided_editor,
    schema_to_field_rows,
    validate_output_schema,
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
        .kllm-field-heading {
            color: #4E2A84;
            font-size: 1.2rem;
            font-weight: 700;
            line-height: 1.35;
            margin: 1.1rem 0 0.2rem 0;
        }
        .kllm-prompt-label {
            align-items: baseline;
            display: flex;
            font-size: 1rem;
            font-weight: 600;
            gap: 1rem;
            justify-content: space-between;
            line-height: 1.4;
            margin: 0.35rem 0 0.25rem 0;
        }
        .kllm-prompt-file {
            color: #4E2A84;
            font-family: monospace;
            font-weight: 700;
            text-align: right;
        }
        .kllm-save-note {
            color: #4E2A84;
            font-size: 1rem;
            font-weight: 700;
            line-height: 1.45;
            margin: 0.25rem 0 0.75rem 0;
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


def _prompt_preview_signature(draft: ProjectDraft, rows_per_request: int) -> str:
    payload = {
        "system_prompt": st.session_state.system_prompt,
        "user_prompt": st.session_state.user_prompt,
        "rows_per_request": rows_per_request,
        "input": draft.config.get("input", {}),
        "context": draft.config.get("prompt", {}).get("context", {}),
        "codebook_source": str(draft.codebook_source or ""),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return sha256(serialized.encode("utf-8")).hexdigest()


def _initialize_draft(draft: ProjectDraft) -> None:
    st.session_state.draft = draft
    st.session_state.project_directory = str(draft.project_dir)
    st.session_state.system_prompt = draft.system_prompt
    st.session_state.user_prompt = draft.user_prompt
    guided = schema_supports_guided_editor(draft.schema)
    st.session_state.schema_mode = "Guided" if guided else "Advanced JSON"
    st.session_state.schema_last_mode = st.session_state.schema_mode
    st.session_state.schema_guided_blocked = False
    st.session_state.schema_rows = schema_to_field_rows(draft.schema) if guided else []
    st.session_state.schema_json_text = json.dumps(draft.schema, indent=2, ensure_ascii=False)
    st.session_state.pop("schema_json_editor", None)
    st.session_state.upload_widget_generation = st.session_state.get("upload_widget_generation", 0) + 1
    st.session_state.pop("input_frame", None)
    st.session_state.pop("column_rows", None)
    st.session_state.pop("codebook_preview", None)
    st.session_state.pop("prompt_preview", None)
    st.session_state.pop("prompt_preview_signature", None)
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
    st.caption(
        "The initialized default is `data/input-data.csv`. You may replace that file, copy a CSV, Parquet, or JSONL "
        "file into the project's `data/` folder, or choose a file below. A chosen file is copied into `data/` "
        "when the project is saved."
    )
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
    st.markdown('<p class="kllm-field-heading">Stable ID column</p>', unsafe_allow_html=True)
    st.caption(
        "Choose a unique, nonempty source identifier. Generated IDs are deterministic for this fixed input snapshot."
    )
    id_column = st.selectbox(
        "Stable ID column",
        id_options,
        index=id_index,
        label_visibility="collapsed",
    )
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
    st.caption(
        "`kllm-batch init` includes a sample `context/codebook.csv`. You can edit it or choose a replacement "
        "CSV, JSON, YAML, TXT, or Markdown codebook below. A chosen file is copied into `context/` "
        "when the project is saved. Keep output-schema labels aligned with the codebook."
    )
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
    previous_mode = st.session_state.get("schema_last_mode", mode)
    conversion_error: str | None = None
    if mode != previous_mode:
        if mode == "Advanced JSON":
            if st.session_state.get("schema_guided_blocked", False):
                st.session_state.schema_guided_blocked = False
            else:
                try:
                    converted = field_rows_to_schema(st.session_state.schema_rows)
                    validate_output_schema(converted)
                    draft.schema = converted
                    st.session_state.schema_json_text = json.dumps(converted, indent=2, ensure_ascii=False)
                except Exception as exc:
                    conversion_error = (
                        f"The guided table could not be converted, so Advanced JSON is showing the last valid schema: {exc}"
                    )
        else:
            try:
                advanced_text = st.session_state.get("schema_json_editor", st.session_state.schema_json_text)
                st.session_state.schema_json_text = advanced_text
                converted = json.loads(advanced_text)
                if not isinstance(converted, dict):
                    raise ValueError("Schema must be a JSON object")
                validate_output_schema(converted)
                draft.schema = converted
                if schema_supports_guided_editor(converted):
                    st.session_state.schema_rows = schema_to_field_rows(converted)
                    st.session_state.schema_guided_blocked = False
                else:
                    st.session_state.schema_guided_blocked = True
                    conversion_error = (
                        "This valid schema uses advanced features that the guided table cannot represent. "
                        "No JSON was changed; switch back to Advanced JSON to continue editing it."
                    )
            except Exception as exc:
                st.session_state.schema_guided_blocked = True
                conversion_error = (
                    f"The Advanced JSON could not be converted. Its text is preserved; switch back to fix it: {exc}"
                )
        st.session_state.schema_last_mode = mode
    editor_col, json_col = st.columns([1.15, 0.85], gap="large")
    if mode == "Guided":
        with editor_col:
            st.markdown("#### Guided field editor")
            if conversion_error:
                st.error(conversion_error)
            guided_available = conversion_error is None and schema_supports_guided_editor(draft.schema)
            st.info(
                'Enum example: `["financial", "organizational", "technical", "other"]`. '
                "Use double quotes and square brackets; leave the cell blank when a field has no fixed choices."
            )
            if guided_available:
                rows = pd.DataFrame(st.session_state.schema_rows)
                edited = st.data_editor(
                    rows,
                    key="schema_field_editor",
                    num_rows="dynamic",
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "name": "Output field",
                        "type": st.column_config.SelectboxColumn(
                            "Type", options=["string", "integer", "number", "boolean"]
                        ),
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
                proposed_rows = edited.to_dict("records")
                st.session_state.schema_rows = proposed_rows
                proposed_schema: dict[str, Any] | None = None
                try:
                    proposed_schema = field_rows_to_schema(proposed_rows)
                except Exception as exc:
                    st.error(str(exc))
                update_schema = st.button("Update JSON", disabled=proposed_schema is None, width="stretch")
                if update_schema and proposed_schema is not None:
                    draft.schema = proposed_schema
                    st.session_state.schema_json_text = json.dumps(draft.schema, indent=2, ensure_ascii=False)
                    st.success("schema.json preview updated.")
                elif proposed_schema is not None and proposed_schema != draft.schema:
                    st.warning("The table has changed. Click Update JSON to apply it to schema.json.")
        with json_col:
            st.markdown("#### schema.json preview")
            if conversion_error:
                st.caption(
                    "The Advanced JSON text is preserved. Save uses the last validated schema; switch back to "
                    "Advanced JSON to continue editing."
                )
            else:
                st.caption("Read-only in Guided mode. Save writes this exact JSON.")
            st.code(st.session_state.schema_json_text, language="json", wrap_lines=True, height=560)
    else:
        with editor_col:
            st.markdown("#### Advanced schema mode")
            if conversion_error:
                st.error(conversion_error)
            st.info(
                "Use Advanced JSON for nested objects, arrays, or other schemas that the guided table cannot represent. "
                "The JSON editor is the source of truth in this mode."
            )
        with json_col:
            st.markdown("#### Editable schema.json")
            if "schema_json_editor" not in st.session_state:
                st.session_state.schema_json_editor = st.session_state.schema_json_text
            st.text_area("schema.json", key="schema_json_editor", height=500, label_visibility="collapsed")
            st.session_state.schema_json_text = st.session_state.schema_json_editor
            candidate: dict[str, Any] | None = None
            try:
                parsed_candidate = json.loads(st.session_state.schema_json_text)
                if isinstance(parsed_candidate, dict):
                    candidate = parsed_candidate
            except json.JSONDecodeError:
                pass
            validate_json = st.button("Validate and use JSON", width="stretch")
            if validate_json:
                try:
                    parsed = json.loads(st.session_state.schema_json_text)
                    if not isinstance(parsed, dict):
                        raise ValueError("Schema must be a JSON object")
                    validate_output_schema(parsed)
                    draft.schema = parsed
                    if schema_supports_guided_editor(parsed):
                        st.session_state.schema_rows = schema_to_field_rows(parsed)
                    st.success("Advanced schema is valid and will be used when saving.")
                except Exception as exc:
                    st.error(f"Schema JSON: {exc}")
            elif candidate != draft.schema:
                st.warning("Advanced JSON has changed. Validate it before saving.")


def _prompt_controls(draft: ProjectDraft) -> None:
    st.subheader("4. Prompts and request preview", anchor="prompts-preview")
    prompt_config = draft.config["prompt"]
    system_file = str(prompt_config.get("system_file", "prompts/system.txt"))
    user_file = str(prompt_config.get("user_file", "prompts/user.txt"))
    st.markdown(
        '<p class="kllm-save-note">Save project and validate writes editor changes back to the configured files.</p>',
        unsafe_allow_html=True,
    )
    editor_col, preview_col = st.columns([1, 1], gap="large")
    with editor_col:
        st.markdown("#### Prompt editor")
        st.markdown(
            f'<div class="kllm-prompt-label"><span>System prompt</span>'
            f'<span class="kllm-prompt-file">{escape(system_file)}</span></div>',
            unsafe_allow_html=True,
        )
        st.text_area("System prompt", key="system_prompt", height=180, label_visibility="collapsed")
        st.markdown(
            f'<div class="kllm-prompt-label"><span>User prompt template</span>'
            f'<span class="kllm-prompt-file">{escape(user_file)}</span></div>',
            unsafe_allow_html=True,
        )
        st.text_area("User prompt template", key="user_prompt", height=260, label_visibility="collapsed")
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
        current_signature = _prompt_preview_signature(draft, rows_per_request)
        if st.button("Update prompt preview", width="stretch"):
            try:
                preview = render_project_preview(draft, source_row=0, row_count=rows_per_request)
                st.session_state.prompt_preview = {
                    "system_prompt": preview.system_prompt,
                    "user_prompt": preview.user_prompt,
                    "first_row": preview.source_rows[0] + 1,
                    "last_row": preview.source_rows[-1] + 1,
                    "record_count": len(preview.records),
                    "rows_per_request": rows_per_request,
                }
                st.session_state.prompt_preview_signature = current_signature
            except Exception as exc:
                st.error(str(exc))
    with preview_col:
        st.markdown("#### Provider prompt preview")
        saved_preview = st.session_state.get("prompt_preview")
        if saved_preview is None:
            st.info("Click Update prompt preview to render the first complete request.")
        else:
            if st.session_state.get("prompt_preview_signature") != current_signature:
                st.warning("The prompt, request size, or input mapping changed. Update the preview before relying on it.")
            st.caption(
                f"Source rows {saved_preview['first_row']}–{saved_preview['last_row']} "
                f"({saved_preview['record_count']} records), matching "
                f"rows_per_request={saved_preview['rows_per_request']}."
            )
            system_tab, user_tab = st.tabs(["System prompt", "Rendered user prompt"])
            with system_tab:
                st.code(saved_preview["system_prompt"], language=None, wrap_lines=True, height=430)
            with user_tab:
                st.code(saved_preview["user_prompt"], language="json", wrap_lines=True, height=430)


def _settings_and_save(draft: ProjectDraft) -> None:
    st.subheader("5. Settings, save, and validate", anchor="settings-save")
    config = draft.config
    settings_errors: list[str] = []
    settings_col, yaml_col = st.columns([1, 1], gap="large")
    with settings_col:
        st.markdown("#### Settings")
        st.markdown("**version**")
        st.caption(f"Configuration format version: {config.get('version', 1)}")

        st.markdown("**project**")
        project = config["project"]
        project["name"] = st.text_input("Project name", value=project.get("name", ""))
        project["description"] = st.text_input("Description", value=project.get("description", ""))

        st.markdown("**input**")
        input_config = config["input"]
        st.caption(f"path: {input_config.get('path', '')}")
        st.caption(f"id_column: {input_config.get('id_column') or 'generated IDs'}")
        st.caption(f"fields_sent: {', '.join(input_config.get('fields_sent', {})) or 'none'}")

        st.markdown("**task**")
        task = config["task"]
        st.caption(f"rows_per_request: {task.get('rows_per_request')} (edited with the prompt preview)")
        task["max_input_tokens"] = int(
            st.number_input("Maximum input tokens", min_value=1, value=int(task.get("max_input_tokens") or 50000))
        )
        task["max_output_tokens"] = int(
            st.number_input("Maximum output tokens", min_value=1, value=int(task.get("max_output_tokens", 2000)))
        )

        st.markdown("**prompt**")
        prompt = config["prompt"]
        prompt["version"] = st.text_input("Prompt version", value=str(prompt.get("version", "1.0")))
        st.caption(f"system_file: {prompt.get('system_file', 'prompts/system.txt')}")
        st.caption(f"user_file: {prompt.get('user_file', 'prompts/user.txt')}")

        st.markdown("**providers**")
        existing = config.get("providers", {})
        selected = st.multiselect("Providers", ["openai", "anthropic"], default=list(existing) or ["openai"])
        providers = {}
        defaults = {"openai": "gpt-5.4-mini", "anthropic": "claude-haiku-4-5"}
        for name in selected:
            item = dict(existing.get(name, {}))
            item["model"] = st.text_input(f"{name} model", value=item.get("model", defaults[name]), key=f"model_{name}")
            options_json = st.text_area(
                f"{name} options (JSON)",
                value=json.dumps(item.get("options", {}), indent=2, ensure_ascii=False),
                key=f"options_{name}",
                height=140,
                help=(
                    "Provider-native request options. The project manages model, prompts, "
                    "token limits, and structured output separately."
                ),
            )
            try:
                item["options"] = parse_provider_options_json(options_json)
            except ValueError as exc:
                message = f"{name}: {exc}"
                settings_errors.append(message)
                st.error(message)
            providers[name] = item
        config["providers"] = providers

        st.markdown("**budget**")
        budget = config["budget"]
        budget["max_estimated_usd"] = float(
            st.number_input(
                "Maximum estimated USD", min_value=0.01, value=float(budget.get("max_estimated_usd", 10.0))
            )
        )

        st.markdown("**evaluation**")
        evaluation = config.setdefault("evaluation", {"random_seed": 42, "gold_columns": {}})
        evaluation["random_seed"] = int(st.number_input("Random seed", value=int(evaluation.get("random_seed", 42))))

        st.markdown("**output**")
        output = config.setdefault("output", {})
        output["write_parquet"] = st.checkbox("Write Parquet results", value=bool(output.get("write_parquet", True)))
        output["write_csv"] = st.checkbox("Write CSV results", value=bool(output.get("write_csv", True)))

    with yaml_col:
        st.markdown("#### project.yaml preview")
        st.caption("Read-only and updated from the settings on the left. Save writes this exact YAML.")
        try:
            yaml_preview = render_project_yaml(draft)
            st.code(yaml_preview, language="yaml", wrap_lines=True, height=980)
        except Exception as exc:
            st.error(f"Cannot render project.yaml yet: {exc}")

    save_requested = st.button("Save project and validate", type="primary", width="stretch")
    st.warning(
        "Saving overwrites the existing managed project configuration, schema, and prompt files. "
        "Review the JSON and YAML previews first. If a managed file changed after this GUI loaded it, "
        "the save is blocked instead of overwriting that external change."
    )
    if save_requested:
        try:
            if settings_errors:
                raise ValueError("Correct the provider options JSON before saving")
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
