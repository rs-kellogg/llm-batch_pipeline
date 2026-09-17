# Kellogg LLM Batch

`kellogg-llm-batch` provides the `kllm-batch` command for reliable, reproducible classification and structured extraction with the OpenAI and Anthropic batch APIs.

The package separates local preparation from paid API operations. `validate`
and `prepare` are local. `submit` executes the exact prepared artifacts and may
incur model charges. Batch-mode `status`, `sync`, and `cancel` also contact the
provider.

## Install

Create and activate your own dedicated mamba environment, then install the
package from the repository root:

```bash
mamba create -n kllm-batch python=3.12 -y
mamba activate kllm-batch
python -m pip install -e .
kllm-batch --help
```

To use the optional local project-builder GUI, install the GUI extra instead:

```bash
python -m pip install -e '.[gui]'
kllm-batch init my-project
kllm-batch gui my-project
```

The GUI binds only to `127.0.0.1`, makes no provider calls, and requires no API
credentials. It writes the same YAML, JSON schema, and prompt files used by the
CLI. Add `--no-browser` for a headless launch or `--port PORT` to select a
different local port. The launcher configures Streamlit to use its light base
theme, 18-pixel base type, clean sans-serif typography, and restrained Kellogg
purple accents, independent of the operating system's light/dark preference.

API credentials are read from the environment:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
```

Never commit keys or place them in `project.yaml`.

## Start here

The complete synthetic [grant-coding example](examples/grant_coding/README.md) includes input data, a codebook, prompts, an output schema, provider settings, and every command in the workflow.

For a new project, `init` creates a minimal editable directory containing
starter data, prompts, a schema, configuration, and an empty runs directory. It
refuses to overwrite a non-empty directory:

```bash
kllm-batch init my-project
kllm-batch validate -c my-project/project.yaml
```

After running `init`, researchers who prefer a form can open that project with
`kllm-batch gui my-project`. The GUI only edits initialized projects. It opens
file-selection windows for input data and a codebook, maps columns, drafts the
schema and prompts, previews the first complete request using
`rows_per_request`, saves explicitly, and runs the same local validation. A
sidebar links directly to each authoring section, and rendered prompts wrap
long lines instead of requiring horizontal scrolling. Side-by-side panes show
the guided schema beside `schema.json`, prompt editors beside their persistent
rendered preview, and ordered settings beside the exact `project.yaml` that
Save will write. Selected input data is copied into `data/` and the codebook
into `context/` only when the project is saved. Preparation and all API
operations remain CLI-only.

Prepare a deterministic pilot for inspection before making an API call:

```bash
kllm-batch prepare -c my-project/project.yaml --provider openai \
  --sample-size 20 --seed 42
# Start with RUN_ID/REVIEW.md, then inspect the exact API payloads and prompts
kllm-batch submit RUN_ID
```

Supplying `--sample-size` or `--ids-file` makes the run a pilot and defaults to
synchronous execution. Omitting both selects every row and defaults to the
provider's batch API. `--execution sync|batch` overrides either default. Both
paths use the same run layout, validation, audit, retry, and provenance code.
A synchronous `submit` waits for responses, writes normalized results, and
runs the audit automatically. A batch `submit` only starts the remote jobs;
`sync` downloads completed responses and then performs the same processing and
automatic audit. The separate `audit` command is available to rerun the check.
For synchronous resumes, completed request IDs already checkpointed in
`raw_responses/` are skipped. An interruption in the short interval after a
provider finishes but before the response is saved locally can rerun that one
request. Recorded API errors require `retry`, and a partial JSONL checkpoint
stops resume with a safety warning rather than continuing blindly.
During synchronous execution, the CLI reports each completed request. On
resume, its confirmation count and cost estimate cover only requests that are
not already checkpointed.

One input row represents one research unit. `fields_sent` controls what providers receive; `columns_preserved` is carried to results locally. Duplicate IDs and exact duplicate model inputs are blocking errors.

`schema.json` describes one result row. For portability across both providers, every object sets `additionalProperties: false`, every property is required, and optional values use a nullable type such as `["string", "null"]`. The package adds `record_id` and the outer `results` array.
The Anthropic adapter converts nullable type arrays to equivalent `anyOf`
branches in the provider payload. It also expresses unsupported numeric and
length bounds as schema descriptions. The original project schema remains
unchanged and is enforced during local post-response validation.

## Safety and reproducibility

- Source files are never overwritten.
- Preparation records source, prompt, schema, model, package, Python, and Git provenance.
- Submission requires confirmation and is blocked above the configured estimated-cost ceiling.
- Remote IDs are saved after each submission so repeating a command does not duplicate work.
- Raw outputs and retry attempts are immutable.
- JSON state is atomically replaced and backed up as `state.previous.json`.

See `kllm-batch COMMAND --help` for command-specific recovery guidance.

## Input setup and duplicate correction

Inputs may be CSV, Parquet, or JSONL. Configure one stable `id_column` whenever
the same research units will be compared across data versions. If no ID column
is supplied, IDs are deterministically derived from the source-file checksum
and zero-based source row. `fields_sent` maps model-facing names to source
columns; `columns_preserved` stays local and is joined back after processing.

Validation blocks both duplicate normalized IDs and duplicate content across
all `fields_sent` values. It never chooses a row to keep. Correct the source
upstream, or add a genuinely distinguishing source field to `fields_sent`, and
then validate again. Use `--report PATH` when a machine-readable diagnostic is
useful; failed validation creates no run or provider payload.

## Recovery and troubleshooting

- If a command is interrupted, rerun `status` and then `sync`; saved remote IDs
  prevent duplicate submission of already-recorded segments.
- Treat `raw_responses/` as immutable. If a completed synchronous run is
  missing an expected raw response, `submit` reports an integrity warning and
  makes no API calls. Restore the response from backup, or put the affected
  source `record_id` values in a UTF-8 text file with one ID per line and no
  header, then prepare and review a new run with `--ids-file rerun_ids.txt`.
- If `state.json` is damaged, the reader falls back to
  `state.previous.json`. Preserve both files when asking for support.
- If a lock remains after a crashed process, first verify that no other local
  command is operating on the run, then remove only that run's `.run.lock`.
- Unknown model pricing blocks preparation. Add explicit input/output price
  overrides under that provider and review the dated pricing assumption.
- Context-window or byte-limit failures should be corrected by reducing
  `rows_per_request` or explicitly configuring a field limit; truncation is
  never implicit.

## Research-data boundary

Only `record_id` and `fields_sent` enter provider prompts. Preserved columns are
local, but raw inputs, snapshots, requests, and outputs may still contain
sensitive research material and should remain in approved storage. Credentials
belong in environment variables and are never recorded by this package.

PDF/document ingestion, OCR, de-identification, and combining source tables are
upstream responsibilities in v1. OpenRouter, fuzzy duplicate detection, and
automatic adjudication are also outside v1.

See [migration from the original scripts](docs/MIGRATION.md) and
[additional task recipes](docs/RECIPES.md).

The fast test suite runs with `python -m pytest`. The opt-in 300,000-row
preparation stress test can be run on a machine with sufficient memory using
`KLLM_RUN_SCALE_TEST=1 python -m pytest tests/test_scale.py`.
