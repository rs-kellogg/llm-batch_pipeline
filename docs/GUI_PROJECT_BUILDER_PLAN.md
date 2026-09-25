# Local GUI Project Builder

## Summary

Add an optional local browser GUI that opens projects previously created by
`kllm-batch init`, maps input columns, loads a codebook, drafts schemas and
prompts, previews a rendered request, saves standard project files, and runs
local validation.

## GUI workflow

- Launch with `kllm-batch gui PROJECT_DIRECTORY`, with optional `--port` and
  `--no-browser`.
- Require an existing `project.yaml` created by `kllm-batch init`; show the
  two-command init/open workflow at the top of the page.
- Select CSV, Parquet, or JSONL data through a browser file picker; display
  dimensions and a preview.
- Configure the ID, model-facing, required, and preserved columns.
- Keep selected files temporary until Save, then copy input data into `data/`
  and the selected CSV, JSON, YAML, or text codebook into `context/`.
- Draft scalar schemas through a guided editor, with advanced JSON editing for
  complex schemas. Show the guided table and an explicitly updated, read-only
  `schema.json` preview side by side. Synchronize compatible edits when users
  switch modes, and preserve advanced-only schemas without lossy conversion.
- Edit system and user prompts using starter templates beside a persistent
  preview that is refreshed explicitly and marked when stale.
- Preview the exact rendered prompts for the first `rows_per_request` source
  rows with `${codebook_json}` and `${records_json}`, wrapping long lines.
- Provide sidebar links to every authoring section and show a valid JSON enum
  array example above the guided schema table.
- Emphasize the stable-ID selector and explain the initialized `data/grants.csv`
  and `context/` locations directly under the corresponding section headings.
- Arrange settings in `project.yaml` key order beside a read-only live YAML
  preview produced by the same configuration builder used during Save.
- Save `project.yaml`, `schema.json`, both prompt files, and the codebook
  atomically, then run local validation.
- Make no provider calls and request no credentials.

## Architecture and interfaces

- Include Streamlit in the standard installation; retain the `gui` extra as a
  compatibility alias.
- Bind the application to `127.0.0.1`, use a clean light theme with an
  18-pixel base font and Kellogg purple accents, and disable usage telemetry.
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
  rendering, schema modes, initialized-project reopen/save round trips, atomic
  saving, validation errors, and external-change conflicts.
- Confirm request preview starts at row one, respects `rows_per_request`, and
  never filters the saved dataset.
- Exercise the interface through Streamlit's testing API.
- Test missing dependencies, custom ports, and `--no-browser`.
- Validate and prepare a GUI-generated project through the existing CLI.
- Run the complete suite without provider API calls.

## Assumptions

- Streamlit is included in the standard installation.
- Browser-selected input data and codebooks are copied into the project only on
  explicit save.
- The GUI runs only on the researcher's computer.
- Saving is explicit, with no autosave.
- Existing complex schemas open in advanced JSON mode.
- Generated files remain the single source of truth for GUI and CLI workflows.
