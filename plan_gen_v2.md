
# plan.md — Kellogg Reliable LLM Batch Pipeline

## Summary

Build `kellogg-llm-batch`, an internal Python package with the `kllm-batch` CLI for reproducible labeling, classification, and structured extraction over large tabular datasets.

SQLite is not necessary for the initial workflow. Use compact JSON state with atomic writes, while storing large request, mapping, and result records separately as JSONL or Parquet. This keeps the package transparent and easy for researchers to inspect or modify.

The first release will:

- Support OpenAI Batch and Anthropic Message Batches through a shared provider interface.
- Read CSV, Parquet, and JSONL inputs without modifying source files.
- Allow configurable rows per model request instead of assuming 20 or 25.
- Provide prompt/model pilots, cost estimation and budget gates, resumable submission, validation, manual retries, provenance, and cross-provider agreement reports.
- Replace overlapping project-specific scripts with reusable modules while retaining them as reference implementations.

## Architecture and Public Interfaces

- Create a standard `src/` package with `pyproject.toml`, Python 3.10–3.12 support, console entry point `kllm-batch`, and dependencies including Pydantic, Typer, Rich, pandas/PyArrow, PyYAML, OpenAI, Anthropic, and JSON Schema validation.
- Expose a small Python API:
  - `ProjectConfig` and `load_config()`
  - `prepare_run()`, `sync_run()`, `audit_run()`, and `prepare_retry()`
  - `ProviderAdapter` with OpenAI and Anthropic implementations
  - `CanonicalRequest`, `BatchHandle`, `NormalizedResult`, `AuditFinding`, and `CostEstimate`
  - `compare_runs()` for provider/model agreement
- Use one provider-neutral state machine: `prepared → submitted → running → downloaded → processed → audited`, with terminal `completed`, `completed_with_failures`, `failed`, and `cancelled` states.
- Keep provider-specific payload construction, status translation, downloading, parsing, usage accounting, and error classification inside adapters. OpenRouter remains a documented future adapter.

Each run directory will contain:

- `manifest.json`: immutable configuration, prompt, schema, source, and environment snapshot.
- `state.json`: compact mutable segment/job status.
- `state.previous.json`: last valid state for recovery.
- `requests/`: canonical and provider-native JSONL requests.
- `mappings/`: request-to-source-row JSONL maps.
- `raw/`: immutable provider responses and errors.
- `results/`: normalized Parquet/CSV outputs.
- `reports/`: cost, audit, comparison, and provenance summaries.

State writes will use a temporary file followed by atomic replacement. A run lock will prevent two local processes from mutating the same run simultaneously. Per-row state will not be stored in `state.json`; it will be derived from mappings and normalized results, keeping the JSON small even for 300,000-row projects.

## Configuration and CLI

A project scaffold contains:

- `project.yaml`: input mapping, provider/model settings, prompt version, chunking, retry policy, cost limit, output options, and evaluation fields.
- `schema.json`: required structured-output schema.
- `prompts/system.txt` and `prompts/user.txt`.
- Optional codebook or context files referenced from YAML.
- A project-local `.gitignore` covering credentials and generated artifacts.

The structured-output contract requires a top-level `results` array and one `row_id` per supplied record. Templates support explicit placeholders such as `${records_json}` and named JSON context files.

Primary commands:

- `kllm-batch init DIRECTORY` — create an annotated example project.
- `kllm-batch validate -c project.yaml` — validate configuration, input quality, schema compatibility, prompt placeholders, request sizes, and provider constraints locally.
- `kllm-batch pilot -c project.yaml` — run a deterministic sample through candidate models, validate outputs, calculate configured metrics, and project full-run cost.
- `kllm-batch prepare -c project.yaml --provider openai|anthropic` — create an immutable run directory, provider payloads, manifest, and cost estimate.
- `kllm-batch submit RUN_ID` — submit prepared segments after cost confirmation; `--yes` supports automation.
- `kllm-batch status RUN_ID` — show cached and current remote status.
- `kllm-batch sync RUN_ID [--watch]` — poll, download, normalize, and process idempotently.
- `kllm-batch cancel RUN_ID` — cancel active jobs without deleting local artifacts.
- `kllm-batch audit RUN_ID` — report completeness, missing/duplicate/unexpected IDs, invalid responses, and provider errors.
- `kllm-batch retry RUN_ID` — prepare a linked child attempt containing selected failed rows; submission remains separate.
- `kllm-batch merge RUN_ID` — combine successful attempts deterministically.
- `kllm-batch compare RUN_A RUN_B` — produce agreement statistics and disagreement files for human review.

Every command and subcommand will include examples, defaults, accepted states, credential requirements, and recovery guidance in `--help`.

## Reliability, Cost, and Provenance

- Stream or batch-read large inputs and split work by configurable `rows_per_request` plus provider request and byte limits.
- Require unique source IDs when configured. Otherwise generate run-stable IDs from the source checksum and original row number.
- Translate the shared JSON Schema into OpenAI Responses and Anthropic structured-output payloads.
- Validate every response for:
  - JSON syntax and schema compliance
  - Exact expected row-ID membership
  - One result per row
  - Duplicate and unexpected IDs
  - Provider status and stop reason
  - Configured categorical or numeric constraints
- Preserve valid rows from partially successful requests and identify only missing or invalid rows for retry.
- Classify failures as transient, expired/cancelled, malformed, incomplete, permanent request/configuration error, or unknown.
- Never patch or delete an earlier attempt. Retries create linked child attempts with separate raw responses and provenance.
- Make submission idempotent: repeated submission commands reuse recorded remote IDs and cannot silently duplicate jobs.
- Recover interrupted state updates using `state.previous.json`; provide a validation command that reconstructs segment status from existing artifacts when necessary.
- Record per-row provenance: run and attempt IDs, source ID/row/checksum, provider, requested and returned model, prompt version/hash, schema/config hashes, request and batch IDs, timestamps, parameters, token usage, estimated/actual cost, validation status, and error category.
- Write `provenance.json`, `run_summary.json`, `run_summary.md`, canonical `results.parquet`, optional `results.csv`, and separate failure/disagreement tables.
- Maintain a versioned pricing registry with an effective date and YAML overrides. Unknown model pricing blocks submission unless explicitly acknowledged.
- Enforce `max_estimated_cost` before submission. Every retry receives a new estimate and confirmation.

Current adapter defaults should respect documented limits and keep them centrally configurable: OpenAI currently permits up to 50,000 requests and 200 MB per batch, while Anthropic permits up to 100,000 requests or 256 MB and returns results out of order, requiring `custom_id` matching. ([OpenAI Batch guide](https://developers.openai.com/api/docs/guides/batch), [Anthropic Batch guide](https://platform.claude.com/docs/en/build-with-claude/batch-processing))

## Documentation and Migration

- Provide a Kellogg-focused README covering GitHub installation, API-key environment variables, the recommended workflow, troubleshooting, state recovery, and research-data handling.
- Include annotated examples for classification, codebook-based coding, extraction, multi-row requests, OpenAI/Claude comparison, gold-label pilots, and retries.
- Document that PDF/document ingestion is upstream and out of scope for v1.
- Preserve `pipeline_openai/` and `pipeline_post/` as reference material during the initial migration.
- Recreate their useful behavior through package modules and regression fixtures rather than importing project-specific globals.
- Add a migration guide mapping the existing `generate`, manager `run`, `process`, `find_bad_chunks`, `fix_failed_chunks`, and retry workflows to the new CLI.
- Treat the repository as Kellogg-internal initially; public package publication and licensing are outside v1.

## Test and Acceptance Plan

- Unit-test configuration errors, readers, stable IDs, prompt rendering, chunk boundaries, request splitting, state transitions, atomic JSON recovery, cost calculations, schema validation, provenance, and deterministic merging.
- Use golden fixtures for OpenAI and Anthropic payloads and for successful, errored, expired, malformed, incomplete, duplicate-ID, and out-of-order responses.
- Mock both SDKs for submission, polling, cancellation, downloading, partial completion, retries, and idempotent re-execution.
- Test pilot and comparison metrics with missing rows, nullable values, gold labels, and categorical disagreements.
- Run a synthetic 300,000-row test to verify deterministic segmentation, bounded-memory processing, and compact state files.
- Test every CLI command and `--help`, clean installation in a dedicated researcher-created mamba environment, and CI on Python 3.10–3.12.
- Mark credentialed live-provider smoke tests as optional and never run them by default.

Acceptance requires:

- An interrupted run resumes without duplicate submission.
- A corrupted or incomplete `state.json` can recover from its previous copy or be reconstructed from artifacts.
- Every input row ends as valid, failed with a recorded reason, or explicitly excluded.
- A manual retry targets only selected failures and merges without losing earlier provenance.
- OpenAI and Claude runs produce the same canonical output columns.
- Researchers can create a new project using only the README and CLI help.
- Raw inputs, provider responses, and earlier attempts are never overwritten.

## Assumptions

- Distribution name: `kellogg-llm-batch`.
- Python import package: `kellogg_llm_batch`.
- CLI command: `kllm-batch`. This distinguishes it from the earlier `llm-batch` repository while remaining short and recognizable.
- Default sample seed is `42`; project templates set `rows_per_request: 10`, but it is configurable down to one.
- OpenAI uses the Responses Batch endpoint; Anthropic uses Message Batches. Both support batch structured outputs. ([OpenAI Batch reference](https://developers.openai.com/api/reference/resources/batches), [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs))
- Credentials come from `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` or a parsed, ignored `.env`.
- Cross-provider comparison is descriptive; v1 does not perform automatic model adjudication.
- Retries are prepared and submitted manually.
- JSON state assumes one local writer per run. Concurrent multi-host orchestration is outside v1 and would be the point at which a transactional database should be reconsidered.
