
# plan.md — Kellogg Reliable LLM Batch Pipeline

## Summary

Build `kellogg-llm-batch`, an internal Python package and `llm-batch` CLI for reproducible labeling, classification, and structured extraction over large tabular datasets.

The first release will:

- Support OpenAI Batch and Anthropic Message Batches through a shared provider interface.
- Read CSV, Parquet, and JSONL inputs without modifying source files.
- Allow configurable rows per model request instead of assuming 20 or 25.
- Provide prompt/model pilots, cost estimation and budget gates, resumable submission, validation, manual retries, provenance, and cross-provider agreement reports.
- Replace the overlapping project-specific scripts with reusable modules while retaining them as reference implementations.

## Architecture and Public Interfaces

- Create a standard `src/` package with `pyproject.toml`, Python 3.10–3.12 support, console entry point `llm-batch`, and runtime dependencies including Pydantic, Typer, Rich, pandas/PyArrow, PyYAML, OpenAI, Anthropic, and JSON Schema validation.
- Expose a small Python API:
  - `ProjectConfig` and `load_config()`
  - `prepare_run()`, `sync_run()`, `audit_run()`, and `prepare_retry()`
  - `ProviderAdapter` with OpenAI and Anthropic implementations
  - `CanonicalRequest`, `BatchHandle`, `NormalizedResult`, `AuditFinding`, and `CostEstimate`
  - `compare_runs()` for provider/model agreement
- Use one provider-neutral run state machine: `prepared → submitted → running → downloaded → processed → audited`, with terminal `completed`, `completed_with_failures`, `failed`, and `cancelled` states.
- Store operational state in a run-local SQLite database using atomic transactions. Export human-readable JSON/CSV summaries so researchers do not need SQLite knowledge.
- Keep provider-specific payload construction, status translation, downloading, output parsing, token usage, and error classification inside adapters. OpenRouter remains an explicitly documented future adapter.

## Configuration and CLI

A project scaffold contains:

- `project.yaml`: input mapping, provider/model settings, prompt version, chunking, retry policy, cost limit, output options, and evaluation fields.
- `schema.json`: required structured-output schema.
- `prompts/system.txt` and `prompts/user.txt`.
- Optional codebook or context files referenced from YAML.
- A project-local `.gitignore` covering credentials, generated requests, raw responses, state databases, and derived outputs.

The structured-output contract requires a top-level `results` array and one `row_id` per supplied record. Templates support explicit placeholders such as `${records_json}` and named JSON context files.

Primary commands:

- `llm-batch init DIRECTORY` — create an annotated example project.
- `llm-batch validate -c project.yaml` — validate configuration, schema compatibility, input columns, IDs, missingness, duplicates, prompt placeholders, request sizes, and provider constraints without submitting.
- `llm-batch pilot -c project.yaml` — select a deterministic sample, run candidate models synchronously, validate outputs, calculate configured accuracy/agreement metrics, and project full-run cost.
- `llm-batch prepare -c project.yaml --provider openai|anthropic` — create an immutable run directory, canonical input snapshot, provider payloads, manifest, and cost estimate.
- `llm-batch submit RUN_ID` — submit prepared segments after an interactive cost confirmation; `--yes` supports automation.
- `llm-batch status RUN_ID` and `llm-batch sync RUN_ID [--watch]` — inspect, poll, download, normalize, and process idempotently.
- `llm-batch cancel RUN_ID` — cancel active provider jobs without deleting local artifacts.
- `llm-batch audit RUN_ID` — report completeness, invalid responses, missing/duplicate/unexpected IDs, schema violations, and provider errors.
- `llm-batch retry RUN_ID` — prepare a linked child attempt containing selected failed rows; submission remains a separate confirmed action.
- `llm-batch merge RUN_ID` — combine successful attempts deterministically, preferring the latest valid result per row.
- `llm-batch compare RUN_A RUN_B` — produce field-level agreement, Cohen’s kappa for configured categorical fields, and disagreement files for human review.

Every command and subcommand will include examples, defaults, accepted states, credential requirements, and recovery guidance in `--help`.

## Reliability, Cost, and Provenance

- Stream or batch-read large inputs and split work by both configurable `rows_per_request` and provider request/byte limits. Preserve deterministic ordering and a request-to-row map for every segment.
- Require unique source IDs when configured. Otherwise generate run-stable IDs from the source checksum and original row number, retaining both values in outputs.
- Use structured outputs for both providers, translating the shared JSON Schema into OpenAI Responses and Anthropic `output_config.format` payloads.
- Validate every response against:
  - JSON syntax and schema
  - Exact expected row-ID membership
  - One result per row
  - Duplicate and unexpected IDs
  - Provider status and stop reason
  - Configured categorical values or numeric constraints
- Preserve valid rows from partially successful requests. Audit only missing or invalid rows for retry rather than discarding an entire chunk.
- Classify failures as transient, expired/cancelled, malformed output, incomplete output, permanent request/configuration error, or unknown. `retry` defaults to retryable and output-validation failures; permanent errors require an explicit flag.
- Never patch or delete an earlier attempt. Raw provider responses, request files, error files, and manifests remain immutable; retries create linked attempts.
- Record per-row provenance: run and attempt IDs, source ID/row/checksum, provider, requested and returned model, prompt version/hash, schema/config hashes, request and batch IDs, timestamps, parameters, token usage, estimated/actual cost, validation status, and error category.
- Write `provenance.json`, `run_summary.json`, `run_summary.md`, canonical `results.parquet`, optional `results.csv`, and separate failure/disagreement tables.
- Maintain a versioned pricing registry with an effective date and YAML overrides. Unknown model pricing blocks submission unless explicitly acknowledged.
- Enforce `max_estimated_cost` before submission. Estimates display assumptions and uncertainty; every retry receives a new estimate and confirmation.
- Make submission idempotent: rerunning `submit` must reuse recorded remote IDs and cannot silently create duplicate jobs.

Current adapter defaults should respect documented limits and keep them centrally configurable: OpenAI currently permits up to 50,000 requests and 200 MB per batch, while Anthropic permits up to 100,000 requests or 256 MB and returns results out of order, requiring `custom_id` matching. ([OpenAI Batch guide](https://developers.openai.com/api/docs/guides/batch), [Anthropic Batch guide](https://platform.claude.com/docs/en/build-with-claude/batch-processing))

## Documentation and Migration

- Provide a Kellogg-focused README covering installation from GitHub, API-key environment variables, the recommended validate/pilot/prepare/submit/sync/audit workflow, troubleshooting, and safe handling of research data.
- Include annotated examples for classification, codebook-based qualitative coding, extraction, multi-row requests, OpenAI/Claude comparison, gold-label pilots, and retrying failures.
- Document that PDF/document ingestion is upstream and out of scope for v1; the package consumes tabular records and metadata.
- Preserve `pipeline_openai/` and `pipeline_post/` initially as reference material. Recreate their important behaviors through package examples and regression tests rather than importing their global constants or project-specific functions.
- Add a migration guide mapping `generate`, manager `run`, `process`, `find_bad_chunks`, `fix_failed_chunks`, and retry scripts to the new CLI.
- Treat the repository as Kellogg-internal initially: no public package registry or public-release license workflow in v1.

## Test and Acceptance Plan

- Unit-test configuration errors, readers, stable IDs, prompt rendering, chunk boundaries, byte/request splitting, state transitions, cost calculations, schema validation, provenance, and deterministic merging.
- Use golden fixtures for OpenAI and Anthropic request payloads and for successful, errored, expired, malformed, incomplete, duplicate-ID, and out-of-order responses.
- Mock both SDKs for submission, polling, cancellation, downloading, partial completion, retries, and idempotent re-execution. Mark credentialed live smoke tests as optional and never run them by default.
- Test pilot metrics with and without gold labels and comparison metrics with missing rows, nullable values, and categorical disagreements.
- Run a synthetic 300,000-row test to verify deterministic segmentation and bounded-memory preparation/processing.
- Test every CLI command and `--help`, clean installation in a dedicated researcher-created mamba environment, and CI on Python 3.10–3.12.
- Acceptance requires:
  - An interrupted run can resume without duplicate submission.
  - Every input row ends as valid, failed with a recorded reason, or explicitly excluded.
  - A manual retry can target only failed rows and merge without losing earlier provenance.
  - OpenAI and Claude runs produce the same canonical output columns.
  - Researchers can create and execute a new example project using only the README and CLI help.
  - Raw inputs and earlier attempts are never overwritten.

## Assumptions

- Working distribution name: `kellogg-llm-batch`; import package: `kellogg_llm_batch`; CLI: `llm-batch`.
- Default sample seed is `42`; project templates set `rows_per_request: 10`, but it is fully configurable down to one row.
- OpenAI uses the Responses Batch endpoint; Anthropic uses Message Batches. Both currently support batch structured outputs. ([OpenAI Batch reference](https://developers.openai.com/api/reference/resources/batches), [Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs))
- Credentials come only from `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` or a properly parsed, ignored `.env`; credentials and sensitive raw observations are never written to logs or provenance.
- Cross-provider comparison is descriptive and exports disagreements for human review; v1 does not use automatic model adjudication.
- Retries are prepared and triggered manually, with a configurable maximum attempt count and fresh budget approval.
