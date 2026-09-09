# plan.md — Kellogg Reliable LLM Batch Pipeline

## Summary

Build `kellogg-llm-batch`, an internal Python package with the `kllm-batch` CLI for reproducible labeling, classification, and structured extraction over large tabular datasets.

The first release will:

- Support OpenAI Batch and Anthropic Message Batches through a common provider interface.
- Accept one CSV, Parquet, or JSONL table per run.
- Use configurable source columns and configurable rows per API request.
- Detect duplicate IDs and duplicate research records before generating any API request artifacts.
- Provide prompt/model pilots, cost gates, resumable processing, manual retries, provenance, validation, and cross-provider agreement reports.
- Use inspectable JSON state rather than SQLite.

## Input Contract and Project Setup

Each source row represents one research unit, such as a paper, grant, Wikipedia page, post, or comment. Source column names are not hard-coded.

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
      # Explicit opt-in example:
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

- `id_column` identifies the researcher’s stable source key. Its values are normalized to nonempty strings and exposed to the model as `record_id`.
- If `id_column` is omitted, generate deterministic `record_id` values from the input-file checksum and original source-row number.
- If an ID column is configured, missing values are errors; IDs are not generated selectively for only those rows.
- `fields_sent` maps model-facing names to source columns. Only these values and `record_id` are sent to providers.
- `columns_preserved` are retained in derived results but never included in prompts.
- `required_fields` names logical entries from `fields_sent`. Missing or empty required values block preparation.
- Empty tabular values become JSON `null`; string fields receive consistent line-ending and outer-whitespace normalization in the derived canonical copy.
- Source files are never edited or overwritten.
- One project configuration accepts one table per run. Combining multiple source tables remains an explicit upstream operation in v1.

## Duplicate and Data-Quality Gate

Run duplicate checks during `validate` and again at the beginning of `prepare`, before creating a run directory, request JSONL, or provider payload.

### Duplicate identifiers

- If `id_column` is configured, reject duplicate IDs after string normalization and whitespace trimming.
- Also reject missing or empty IDs.
- If IDs are generated, verify the generated `record_id` values are unique before continuing.
- Display the duplicate value, duplicate count, and original source-row numbers.
- Treat duplicate IDs as a blocking validation error. Researchers must correct the input before preparation.

### Duplicate records independent of ID

Detect records that become duplicates when identifiers are ignored:

- Build a canonical comparison key from all normalized `fields_sent` values.
- Exclude `id_column`, generated `record_id`, source-row number, and `columns_preserved` from this comparison.
- Do not lowercase text or perform fuzzy matching; v1 detects exact duplicates after the same null, line-ending, and outer-whitespace normalization used for prompts.
- Report every duplicate group with its record IDs, source-row numbers, and a shortened preview of the duplicated fields.
- Treat duplicate content as a blocking validation finding by default because distinct IDs can otherwise hide repeated research units.
- Researchers must deduplicate upstream or correct `fields_sent` if an omitted distinguishing field caused the apparent duplication.
- Do not silently retain the first row, discard later rows, or submit duplicate records.

Validation exits nonzero when either duplicate condition exists. It prints a concise console summary and can write a diagnostic report only when `--report PATH` is requested; it does not generate batch artifacts.

Other pre-generation checks cover:

- Input file readability and configured column existence.
- Required-field missingness.
- Schema and prompt-placeholder validity.
- Unsupported data types.
- Oversized records and provider context limits.
- Estimated request counts, batch counts, bytes, tokens, and cost.
- Source and canonical row counts.

## Prompt and Output Schema

`prompts/system.txt` contains durable research instructions. `prompts/user.txt` uses a restricted placeholder format:

```text
Codebook:

${codebook_json}

Records:

${records_json}
```

- CSV, JSON, and YAML context files are converted to deterministic JSON.
- Plain-text context files use `${name_text}`.
- Arbitrary Python and template expressions are not executed.
- The exact rendered prompt, prompt version, and hash are preserved for every run.

Researchers define only one row’s predicted fields in `schema.json`:

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

The package injects `record_id` and creates the provider-facing wrapper:

```json
{
  "results": [
    {
      "record_id": "GRANT-001",
      "primary_label": "organizational",
      "secondary_label": null,
      "confidence": 0.91,
      "justification": "The abstract focuses on organizational decisions."
    }
  ]
}
```

This bookkeeping contract is owned by the package and cannot be overridden by project schemas.

## Architecture and State

- Create a standard `src/` package with `pyproject.toml`, Python 3.10–3.12 support, and the `kllm-batch` console entry point.
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

Write state through a same-directory temporary file followed by atomic replacement. Use a run lock to prevent simultaneous local mutation. Keep per-row state in JSONL/Parquet artifacts rather than the compact JSON state.

## CLI Workflow

- `kllm-batch init DIRECTORY` — scaffold an annotated project with example input, configuration, prompts, and schema.
- `kllm-batch validate -c project.yaml [--report PATH]` — run all local configuration, duplicate, input, schema, size, and cost checks.
- `kllm-batch pilot -c project.yaml` — run a deterministic sample through candidate models and report validity, configured metrics, and projected cost.
- `kllm-batch prepare -c project.yaml --provider openai|anthropic` — rerun validation and create an immutable prepared run.
- `kllm-batch submit RUN_ID` — submit prepared segments after cost confirmation; `--yes` supports automation.
- `kllm-batch status RUN_ID` — show cached and current remote status.
- `kllm-batch sync RUN_ID [--watch]` — poll, download, normalize, and process idempotently.
- `kllm-batch cancel RUN_ID` — cancel active jobs without deleting artifacts.
- `kllm-batch audit RUN_ID` — report completeness, missing/duplicate/unexpected result IDs, schema violations, and provider errors.
- `kllm-batch retry RUN_ID` — prepare a linked child attempt containing selected failed rows; do not submit automatically.
- `kllm-batch merge RUN_ID` — combine valid attempts deterministically.
- `kllm-batch compare RUN_A RUN_B` — report field-level agreement, Cohen’s kappa for configured categorical fields, and rows requiring human review.

Every command and subcommand includes examples, defaults, accepted states, credential requirements, and recovery instructions in `--help`.

## Reliability, Cost, and Provenance

- Stream or batch-read large inputs and split work by `rows_per_request` plus provider request and byte limits.
- Never truncate long records silently. Explicit truncation applies only to the provider-facing copy and records the affected field and original/sent lengths.
- Validate JSON syntax, schema, expected IDs, completeness, duplicate output IDs, unexpected IDs, provider status, stop reason, and configured value constraints.
- Preserve valid rows from partially successful requests and retry only missing or invalid source records.
- Classify failures as transient, expired/cancelled, malformed, incomplete, permanent configuration/request error, or unknown.
- Never patch or delete earlier attempts. Retries create linked child attempts.
- Make submission idempotent so repeated commands cannot silently create duplicate remote jobs.
- Record per-row provenance: run and attempt IDs, source ID and row, source checksum, provider, requested and returned model, prompt version/hash, schema/config hashes, request and batch IDs, timestamps, parameters, token usage, estimated/actual cost, truncation status, validation status, and error category.
- Produce `provenance.json`, `run_summary.json`, `run_summary.md`, `results.parquet`, optional `results.csv`, and separate failure and disagreement tables.
- Maintain a dated pricing registry with project-level overrides. Unknown pricing blocks submission unless explicitly acknowledged.
- Enforce `max_estimated_cost` before submission and require a new estimate and confirmation for retries.

Provider limits remain centralized and configurable. Current defaults reflect up to 50,000 requests and 200 MB for an OpenAI batch and up to 100,000 requests or 256 MB for an Anthropic batch. Provider results are always joined through `custom_id`, never file order. ([OpenAI Batch guide](https://developers.openai.com/api/docs/guides/batch), [Anthropic Batch guide](https://platform.claude.com/docs/en/build-with-claude/batch-processing))

## Documentation and Migration

- Provide a Kellogg-focused README covering GitHub installation, input setup, column mapping, duplicate correction, credentials, the recommended workflow, troubleshooting, state recovery, and research-data handling.
- Include annotated examples for classification, codebook coding, extraction, configurable multi-row requests, OpenAI/Claude comparison, gold-label pilots, and retries.
- Document PDF and document ingestion as an upstream responsibility outside v1.
- Preserve `pipeline_openai/` and `pipeline_post/` as reference material during migration.
- Recreate their useful behavior through package modules and regression fixtures rather than importing project-specific globals.
- Add a migration guide mapping the existing generation, manager, processing, malformed-chunk repair, and missing-row retry commands to `kllm-batch`.
- Treat the repository as Kellogg-internal initially; public publication and licensing are outside v1.

## Test and Acceptance Plan

- Test CSV, Parquet, and JSONL inputs with configured IDs and generated IDs.
- Test duplicate-ID detection for strings, numbers, whitespace differences, empty values, and duplicate generated IDs.
- Test duplicate-record detection where:
  - IDs differ but every sent field matches.
  - Preserved metadata differs but sent fields match.
  - Whitespace or line endings differ.
  - One genuinely distinguishing sent field differs.
  - Null and empty required fields occur.
- Assert that duplicate or missing-ID failures create no run directory or request payload.
- Unit-test prompt rendering, schema wrapping, chunk boundaries, request splitting, state transitions, JSON recovery, cost calculations, provenance, and deterministic merging.
- Use golden fixtures for OpenAI and Anthropic payloads and successful, errored, expired, malformed, incomplete, duplicate-ID, and out-of-order responses.
- Mock both provider SDKs for submission, polling, cancellation, downloading, partial completion, retries, and idempotence.
- Test pilot and comparison metrics with gold labels, nullable values, missing results, and categorical disagreements.
- Run a synthetic 300,000-row test to verify bounded-memory processing, deterministic segmentation, compact state, and duplicate detection.
- Verify clean installation and the complete CLI help tree in `/Users/peilinliao/pliao_envs/openaillm`.

## Assumptions

- Exact duplicate research records are treated as input errors, even when they have different IDs.
- Researchers resolve duplicates upstream; the package does not choose which duplicate to retain.
- Duplicate detection is exact after canonical normalization, not fuzzy or semantic.
-4
- Generated IDs are appropriate for a fixed source snapshot; stable cross-version comparisons should use a researcher-supplied `id_column`.
- JSON state assumes one writer per run. Multi-host concurrent orchestration would justify reconsidering a transactional database later.
- OpenRouter, direct document ingestion, automatic model adjudication, fuzzy duplicate detection, and automatic retry submission are outside v1.
