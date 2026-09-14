# Kellogg Reliable LLM Batch Pipeline implementation plan

The accepted, detailed specification is preserved in
[`plan_gen_v4.md`](plan_gen_v4.md). It is the normative plan for this
repository; this shorter file is its implementation index.

## Package and workflow

- Distribute `kellogg-llm-batch` with the `kllm-batch` CLI.
- Support OpenAI Batch and Anthropic Message Batches through provider adapters.
- Keep source data immutable and state inspectable in atomic JSON plus JSONL or
  Parquet row artifacts; do not introduce SQLite in v1.
- Provide validate, prepare, submit, status, sync, cancel, audit, retry, merge,
  and cross-provider compare commands. Pilot testing uses `prepare` record
  selection and the same `submit` lifecycle as a full run.
- Record source, prompt, schema, model, environment, cost, and row provenance.

## Input contract and blocking checks

- Accept one CSV, Parquet, or JSONL table per run.
- Configure a stable `id_column`, model-facing `fields_sent`, local
  `columns_preserved`, required fields, and flexible `rows_per_request`.
- Generate deterministic IDs only when no ID column is configured.
- Before creating a run or provider payload, block missing/duplicate normalized
  IDs and exact duplicate records across canonicalized `fields_sent` values.
- Report every duplicate group with IDs, source rows, counts, and previews;
  researchers correct duplicates upstream.
- Validate prompts, portable JSON Schema, context/byte limits, and estimated
  cost before submission.

## Required teaching example

Maintain `examples/grant_coding/` with synthetic input, a stable ID, two sent
fields, preserved metadata, a codebook, complete system and user prompts,
schema, two-provider YAML configuration, invalid duplicate fixtures, expected
outputs, and a workflow README. The README covers creating a dedicated mamba
environment, credentials, validation, pilot selection and prompt inspection,
submission, synchronization, auditing, retry/merge, and provider
comparison, and clearly marks commands that contact an API.

## Acceptance

- Exercise configuration, formats, ID generation, duplicates, schemas,
  provider payloads/lifecycles, state recovery, partial results, retries,
  provenance, comparison, CLI documentation, and scaffolding with automated
  tests and mocked providers.
- Keep a separate opt-in 300,000-row stress test for deterministic large-run
  preparation and compact state.
- Verify editable installation, the complete help tree, example validation,
  dependency consistency, and wheel construction in a researcher-created
  dedicated mamba environment.
