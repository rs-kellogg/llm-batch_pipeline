# Kellogg LLM Batch

`kellogg-llm-batch` provides the `kllm-batch` command for reliable, reproducible classification and structured extraction with the OpenAI and Anthropic batch APIs.

The package separates local preparation from paid API operations. `validate`,
`pilot generate`, and `prepare` are local. `pilot run`, `submit`, `status`,
`sync`, and `cancel` contact a provider; model execution may incur usage
charges.

## Install

Create and activate your own dedicated mamba environment, then install the
package from the repository root:

```bash
mamba create -n kllm-batch python=3.12 -y
mamba activate kllm-batch
python -m pip install -e .
kllm-batch --help
```

API credentials are read from the environment:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
```

Never commit keys or place them in `project.yaml`.

## Start here

The complete synthetic [grant-coding example](examples/grant_coding/README.md) includes input data, a codebook, prompts, an output schema, provider settings, and every command in the workflow.

For a new project:

```bash
kllm-batch init my-project
kllm-batch validate -c my-project/project.yaml
```

Generate a deterministic pilot for inspection before making an API call:

```bash
kllm-batch pilot generate -c my-project/project.yaml --provider openai
# Inspect sampled_records.jsonl, rendered_prompts.jsonl, and provider_requests.jsonl
kllm-batch pilot run PILOT_DIR
```

One input row represents one research unit. `fields_sent` controls what providers receive; `columns_preserved` is carried to results locally. Duplicate IDs and exact duplicate model inputs are blocking errors.

`schema.json` describes one result row. For portability across both providers, every object sets `additionalProperties: false`, every property is required, and optional values use a nullable type such as `["string", "null"]`. The package adds `record_id` and the outer `results` array.

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
