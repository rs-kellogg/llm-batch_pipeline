# plan.md — Kellogg Reliable LLM Batch Pipeline

## Summary

Build `kellogg-llm-batch`, an internal Python package with the `kllm-batch` CLI for reproducible labeling, classification, and structured extraction over large tabular datasets.

The first release will:

- Support OpenAI Batch and Anthropic Message Batches through a common provider interface.
- Accept one CSV, Parquet, or JSONL table per run.
- Use configurable source columns and rows per API request.
- Detect duplicate IDs and duplicate research records before generating API request artifacts.
- Provide prompt/model pilots, cost gates, resumable processing, manual retries, provenance, validation, and cross-provider agreement reports.
- Use inspectable JSON state rather than SQLite.
- Include a complete runnable example with input data, prompts, schema, configuration, and a workflow README.

## Input Contract and Project Setup

Each source row represents one research unit, such as a paper, grant, Wikipedia page, post, or comment. Source column names are configured rather than hard-coded.

A generated project contains:

```text
project-name/
├── project.yaml
├── schema.json
├── prompts/
│   ├── system.txt
│   └── user.txt
├── data/
├── context/
└── runs/
```

Recommended configuration:

```yaml
version: 1

project:
  name: grant-topic-coding
  description: Classify grant abstracts using the project codebook

input:
  path: data/grants.parquet
  format: auto
  id_column: grant_id
  csv_encoding: utf-8

  fields_sent:
    title: project_title
    text: abstract

  columns_preserved:
    - year
    - investigator
    - source_file

  required_fields:
    - text
  missing_required: error

  field_limits:
    text:
      overflow: error
      # Explicit opt-in:
      # max_characters: 30000
      # overflow: truncate

task:
  rows_per_request: 20
  output_schema: schema.json
  max_output_tokens: 4000

prompt:
  version: "1.0"
  system_file: prompts/system.txt
  user_file: prompts/user.txt
  context:
    codebook:
      path: context/codebook.csv
      format: csv

providers:
  openai:
    model: MODEL_NAME
  anthropic:
    model: MODEL_NAME

budget:
  max_estimated_usd: 100.00

output:
  write_parquet: true
  write_csv: true
```

Input behavior:

- `id_column` identifies the researcher’s stable source key. Values are normalized to nonempty strings and exposed to the model as `record_id`.
- If `id_column` is omitted, generate deterministic IDs from the input-file checksum and original source-row number.
- If an ID column is configured, missing values are errors; IDs are not selectively generated for individual rows.
- `fields_sent` maps model-facing names to source columns. Only these fields and `record_id` are sent to providers.
- `columns_preserved` are joined back into results but never included in prompts.
- Missing or empty `required_fields` block preparation.
- Empty optional values become JSON `null`.
- Source files are never edited or overwritten.
- One project configuration accepts one table per run. Combining multiple tables remains an upstream operation in v1.

## Duplicate and Data-Quality Gate

Run validation both through `kllm-batch validate` and at the beginning of `prepare`, before creating a run directory, request JSONL, or provider payload.

### Duplicate identifiers

- Reject duplicate configured IDs after string normalization and whitespace trimming.
- Reject missing or empty configured IDs.
- Verify generated IDs are unique.
- Report duplicate values, counts, and original source-row numbers.
- Treat duplicate IDs as blocking errors requiring correction upstream.

### Duplicate records independent of ID

- Build a canonical comparison key from all normalized `fields_sent` values.
- Ignore source IDs, generated IDs, source-row numbers, and preserved metadata.
- Detect exact duplicates after null, line-ending, and outer-whitespace normalization.
- Do not lowercase text or perform fuzzy matching.
- Report every duplicate group with IDs, source rows, and shortened field previews.
- Treat duplicate content as blocking by default, even when IDs differ.
- Require researchers to deduplicate upstream or add a genuinely distinguishing field to `fields_sent`.
- Never silently retain one duplicate, discard another, or submit repeated records.

Validation exits nonzero for either duplicate condition. It prints a concise summary and writes a diagnostic report only when `--report PATH` is requested. No batch artifacts are generated after a failed check.

Additional checks cover:

- Input readability and configured columns.
- Required-field missingness.
- Prompt placeholders and schema compatibility.
- Unsupported data types.
- Oversized records and model context limits.
- Request, batch, byte, token, and cost estimates.
- Source and canonical row counts.

## Prompt and Output Schema

Prompt templates use a restricted placeholder format:

```text
Codebook:

${codebook_json}

Records:

${records_json}
```

- CSV, JSON, and YAML context files are converted into deterministic JSON.
- Plain-text context files use `${name_text}`.
- Arbitrary Python and template expressions are not executed.
- Exact prompt contents, version, and hashes are copied into every run manifest.

Researchers define one row’s predicted fields in `schema.json`:

```json
{
  "type": "object",
  "properties": {
    "primary_label": {
      "type": "string",
      "enum": ["financial", "organizational", "technical", "other"]
    },
    "secondary_label": {
      "type": ["string", "null"]
    },
    "confidence": {
      "type": "number",
      "minimum": 0,
      "maximum": 1
    },
    "justification": {
      "type": "string"
    }
  },
  "required": [
    "primary_label",
    "secondary_label",
    "confidence",
    "justification"
  ],
  "additionalProperties": false
}
```

The package injects `record_id` and constructs the provider-facing `results` wrapper. Project schemas cannot override this bookkeeping contract.

## Required Runnable Example

Add `examples/grant_coding/` as a complete, self-contained teaching example:

```text
examples/grant_coding/
├── README.md
├── project.yaml
├── schema.json
├── data/
│   └── grants.csv
├── context/
│   └── codebook.csv
├── prompts/
│   ├── system.txt
│   └── user.txt
└── expected/
    ├── validation_summary.txt
    └── result_columns.md
```

The example must include:

- A small synthetic `grants.csv` with 8–12 records and no real or sensitive research data.
- A stable `grant_id`, two fields sent to the model, and preserved metadata fields.
- A small classification codebook.
- A complete system prompt explaining the coding task, evidence limits, and consistency requirements.
- A complete user prompt containing `${codebook_json}` and `${records_json}`.
- A per-row JSON Schema with a categorical label, optional secondary label, confidence, and justification.
- A valid `project.yaml` demonstrating column mapping, required fields, chunk size, both providers, budget cap, and output options.
- Expected validation messages and documentation of the resulting output columns.
- At least one separate invalid-input fixture used by tests to demonstrate duplicate-ID and duplicate-content failures.

The example README must explain:

1. How to install the package in the provided mamba environment.
/node.
2. How to set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` without committing credentials.
3. How to inspect the source data, prompts, codebook, schema, and YAML mappings.
4. How to run local validation.
5. How to run a small pilot.
6. How to prepare a batch and inspect the cost estimate before submission.
7. How to submit, check status, synchronize results, and run the audit.
8. How to retry failed rows manually.
9. How to run the same example with the other provider.
10. How to compare the OpenAI and Anthropic runs.
11. Where raw, normalized, provenance, failure, and comparison outputs appear.
12. Which commands use an API and may incur costs.

Document this exact workflow, using actual CLI syntax:

```bash
mamba create -n kllm-batch python=3.12 -y
mamba activate kllm-batch

python -m pip install -e .

kllm-batch validate -c examples/grant_coding/project.yaml

kllm-batch pilot generate \
  -c examples/grant_coding/project.yaml \
  --provider openai

# Inspect the generated records, rendered prompts, and exact provider payload.
kllm-batch pilot run PILOT_DIR

kllm-batch prepare \
  -c examples/grant_coding/project.yaml \
  --provider openai

kllm-batch submit RUN_ID
kllm-batch status RUN_ID
kllm-batch sync RUN_ID --watch
kllm-batch audit RUN_ID
```

Also show the Anthropic equivalent and:

```bash
kllm-batch compare OPENAI_RUN_ID ANTHROPIC_RUN_ID
```

All example commands must be exercised in automated CLI tests using mocked providers. Local validation and preparation must also be executed against the real example files during testing.

## Architecture and State

- Create a standard `src/` package with `pyproject.toml`, Python 3.10–3.12 support, and the `kllm-batch` entry point.
- Use Pydantic, Typer, Rich, pandas/PyArrow, PyYAML, OpenAI, Anthropic, and JSON Schema validation.
- Expose:
  - `ProjectConfig` and `load_config()`
  - `validate_project()` and `find_duplicate_records()`
  - `prepare_run()`, `sync_run()`, `audit_run()`, and `prepare_retry()`
  - `ProviderAdapter` with OpenAI and Anthropic implementations
  - `CanonicalRecord`, `CanonicalRequest`, `BatchHandle`, `NormalizedResult`, `AuditFinding`, and `CostEstimate`
  - `compare_runs()`

Use the shared state sequence:

```text
prepared → submitted → running → downloaded → processed → audited
```

Terminal states are `completed`, `completed_with_failures`, `failed`, and `cancelled`.

Each run directory contains:

- `manifest.json`: immutable configuration, source, prompt, schema, package version, and environment snapshot.
- `state.json`: compact mutable segment/job state.
- `state.previous.json`: last valid state for recovery.
- `requests/`: canonical and provider-native JSONL requests.
- `mappings/`: request-to-source-row JSONL maps.
- `raw/`: immutable provider responses and errors.
- `results/`: normalized Parquet and optional CSV results.
- `reports/`: cost, audit, comparison, and provenance reports.

Write state through a same-directory temporary file followed by atomic replacement. Use a run lock to prevent simultaneous local mutation. Keep per-row state in JSONL or Parquet rather than the compact JSON state.

## CLI Workflow

- `kllm-batch init DIRECTORY` — scaffold an annotated project and runnable example structure.
- `kllm-batch validate -c project.yaml [--report PATH]` — run local configuration, duplicate, input, schema, size, and cost checks.
- `kllm-batch pilot generate -c project.yaml --provider PROVIDER` — locally build a deterministic sample, rendered prompts, schema, and exact provider payloads for inspection.
- `kllm-batch pilot run PILOT_DIR` — run the exact reviewed payloads and report validity, metrics, and projected cost.
- `kllm-batch prepare -c project.yaml --provider openai|anthropic` — rerun validation and create an immutable prepared run.
- `kllm-batch submit RUN_ID` — submit prepared segments after cost confirmation; `--yes` supports automation.
- `kllm-batch status RUN_ID` — show cached and current remote status.
- `kllm-batch sync RUN_ID [--watch]` — poll, download, normalize, and process idempotently.
- `kllm-batch cancel RUN_ID` — cancel active jobs without deleting artifacts.
- `kllm-batch audit RUN_ID` — report completeness, missing/duplicate/unexpected result IDs, schema violations, and provider errors.
- `kllm-batch retry RUN_ID` — prepare a linked child attempt containing selected failed rows without submitting automatically.
- `kllm-batch merge RUN_ID` — combine valid attempts deterministically.
- `kllm-batch compare RUN_A RUN_B` — report agreement statistics and rows requiring human review.

Every command includes examples, defaults, accepted states, credential requirements, cost implications, and recovery instructions in `--help`.

## Reliability, Cost, and Provenance

- Stream or batch-read large inputs and split work by `rows_per_request` plus provider request and byte limits.
- Never truncate silently. Explicit truncation affects only the provider-facing copy and records original and submitted lengths.
- Validate JSON syntax, schema, expected IDs, completeness, duplicate output IDs, unexpected IDs, provider status, stop reason, and configured constraints.
- Preserve valid rows from partially successful requests and retry only missing or invalid records.
- Classify failures as transient, expired/cancelled, malformed, incomplete, permanent request/configuration error, or unknown.
- Never patch or delete earlier attempts; retries create linked child attempts.
- Make submission idempotent so repeated commands cannot silently create duplicate remote jobs.
- Record per-row provenance: run and attempt IDs, source ID/row/checksum, provider, requested and returned model, prompt version/hash, schema/config hashes, request and batch IDs, timestamps, parameters, token usage, estimated/actual cost, truncation status, validation status, and error category.
- Produce `provenance.json`, `run_summary.json`, `run_summary.md`, `results.parquet`, optional `results.csv`, and separate failure/disagreement tables.
- Maintain a dated pricing registry with project-level overrides. Unknown pricing blocks submission unless explicitly acknowledged.
- Enforce `max_estimated_cost` and require a new estimate and confirmation for retries.

Provider limits remain centralized and configurable. Current defaults reflect up to 50,000 requests and 200 MB for an OpenAI batch and up to 100,000 requests or 256 MB for an Anthropic batch. Results are joined through `custom_id`, never file order. ([OpenAI Batch guide](https://developers.openai.com/api/docs/guides/batch), [Anthropic Batch guide](https://platform.claude.com/docs/en/build-with-claude/batch-processing))

## Documentation and Migration

- Provide a Kellogg-focused top-level README covering installation, input setup, column mapping, duplicate correction, credentials, workflow, troubleshooting, state recovery, and research-data handling.
- Link prominently to the runnable grant-coding example as the recommended starting point.
- Include additional concise examples for extraction, gold-label pilots, and configurable multi-row requests only where they add behavior not shown by the primary example.
- Document PDF and document ingestion as an upstream responsibility outside v1.
- Preserve `pipeline_openai/` and `pipeline_post/` as reference material during migration.
- Recreate their useful behavior through package modules and regression fixtures rather than importing project-specific globals.
- Add a migration guide mapping existing generation, manager, processing, malformed-chunk repair, and missing-row retry commands to `kllm-batch`.
- Treat the repository as Kellogg-internal initially; public publication and licensing are outside v1.

## Test and Acceptance Plan

- Test CSV, Parquet, and JSONL inputs with configured and generated IDs.
- Test duplicate IDs involving strings, numbers, whitespace, missing values, and generated IDs.
- Test duplicate records where IDs differ, preserved metadata differs, whitespace differs, a sent field differs, or required fields are missing.
- Assert that blocking validation failures create no run directory or provider payload.
- Unit-test prompt rendering, schema wrapping, chunk boundaries, request splitting, state transitions, JSON recovery, cost calculations, provenance, and deterministic merging.
- Use golden fixtures for OpenAI and Anthropic payloads and successful, errored, expired, malformed, incomplete, duplicate-ID, and out-of-order responses.
- Mock both provider SDKs for submission, polling, cancellation, downloading, partial completion, retries, and idempotence.
- Test every command in the example README so documented commands cannot drift from the implementation.
- Test pilot and comparison metrics with gold labels, nullable values, missing results, and categorical disagreements.
- Run a synthetic 300,000-row test for bounded-memory processing, deterministic segmentation, compact state, and duplicate detection.
- Verify clean installation and the complete CLI help tree in a dedicated researcher-created mamba environment.

## Assumptions

- Exact duplicate research records are input errors even when they have different IDs.
- Researchers resolve duplicates upstream; the package does not choose which row to retain.
- Duplicate detection is exact after canonical normalization, not fuzzy or semantic.
- Generated IDs are appropriate for a fixed source snapshot; cross-version comparisons should use a researcher-supplied ID.
- JSON state assumes one writer per run. Multi-host concurrent orchestration may justify a transactional database later.
- The primary example uses only synthetic, non-sensitive data.
- OpenRouter, direct document ingestion, automatic model adjudication, fuzzy duplicate detection, and automatic retry submission are outside v1.
