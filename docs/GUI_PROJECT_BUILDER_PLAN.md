# Local GUI Project Builder

## Summary

Add an optional local browser GUI that creates or reopens projects, maps input
columns, loads a codebook, drafts schemas and prompts, previews a rendered
prompt using one selected record, saves standard project files, and runs local
validation.

## GUI workflow

- Launch with `kllm-batch gui [PROJECT_DIRECTORY]`, with optional `--port` and
  `--no-browser`.
- Create a project or reopen an existing `project.yaml`.
- Load CSV, Parquet, or JSONL data by path; display dimensions and a preview.
- Configure the ID, model-facing, required, and preserved columns.
- Reference the original data file, using a relative path when it is inside the
  project.
- Load and preview a CSV, JSON, YAML, or text codebook, then copy it into
  `context/`.
- Draft scalar schemas through a guided editor, with advanced JSON editing for
  complex schemas.
- Edit system and user prompts using starter templates.
- Select one source row and preview the exact rendered prompts with
  `${codebook_json}` and `${records_json}`.
- Save `project.yaml`, `schema.json`, both prompt files, and the codebook
  atomically, then run local validation.
- Make no provider calls and request no credentials.

## Architecture and interfaces

- Add Streamlit under an optional `gui` dependency group.
- Bind the application to `127.0.0.1` and disable usage telemetry.
- Give a clear installation command when GUI dependencies are absent.
- Add UI-independent `load_project_draft()`, `render_project_preview()`, and
  `save_project_draft()` functions.
- Reuse existing configuration, table-reading, context, prompt-rendering,
  schema, and validation logic.
- Preserve settings not exposed by the basic interface when reopening projects.
- Detect externally modified files and block accidental overwrites.
- Keep execution commands outside the GUI.

## Test plan

- Test supported input formats, mappings, codebook copying, deterministic
  rendering, schema modes, create/reopen round trips, atomic saving, validation
  errors, and external-change conflicts.
- Confirm preview-row selection never filters the saved dataset.
- Exercise the interface through Streamlit's testing API.
- Test missing dependencies, custom ports, and `--no-browser`.
- Validate and prepare a GUI-generated project through the existing CLI.
- Run the complete suite without provider API calls.

## Assumptions

- Streamlit is optional and installed with
  `python -m pip install -e '.[gui]'`.
- Input data remains at its original path; the codebook is copied.
- The GUI runs only on the researcher's computer.
- Saving is explicit, with no autosave.
- Existing complex schemas open in advanced JSON mode.
- Generated files remain the single source of truth for GUI and CLI workflows.
