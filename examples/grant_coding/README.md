# Grant coding example

This example classifies ten entirely synthetic grants. It is safe to inspect and modify; it contains no real research data.

## 1. Install

From the repository root:

```bash
mamba activate /Users/peilinliao/pliao_envs/openaillm
python -m pip install -e .
```

## 2. Configure credentials

Set only the provider you plan to use:

```bash
export OPENAI_API_KEY="your-key"
export ANTHROPIC_API_KEY="your-key"
```

Do not put credentials in YAML, prompt files, shell history shared with others, or Git.

## 3. Inspect the project

- `data/grants.csv` has one grant per row and a stable `grant_id`.
- `project.yaml` maps `project_title` and `abstract` to model-facing fields while preserving three local metadata columns.
- `context/codebook.csv` defines the four labels.
- `prompts/system.txt` contains the coding rules.
- `prompts/user.txt` inserts `${codebook_json}` and `${records_json}`.
- `schema.json` defines one row's required output fields; the package adds `record_id` and the `results` wrapper.

## 4. Validate locally

```bash
kllm-batch validate -c examples/grant_coding/project.yaml
```

This is local and free. It checks duplicate IDs, duplicate model-facing records, missing values, prompts, schema, and estimated costs. The two `invalid_duplicate_*.csv` files are teaching and test fixtures; point a copied YAML file at either one to see validation fail before request generation.

## 5. Run a pilot

```bash
kllm-batch pilot \
  -c examples/grant_coding/project.yaml \
  --provider openai
```

`pilot` calls the provider synchronously and may incur cost. It samples four records deterministically and writes a report under `examples/grant_coding/pilots/`.

## 6. Prepare and inspect cost

```bash
kllm-batch prepare \
  -c examples/grant_coding/project.yaml \
  --provider openai
```

Preparation is local. It validates again and prints a run directory such as `examples/grant_coding/runs/20260909T120000Z_openai_ab12cd34`. In the commands below, replace `RUN_ID` with that complete path. Inspect `manifest.json`, `state.json`, and `requests/` before submitting.

## 7. Submit, monitor, download, and audit

```bash
kllm-batch submit RUN_ID
kllm-batch status RUN_ID
kllm-batch sync RUN_ID --watch
kllm-batch audit RUN_ID
```

These commands contact the provider; completed requests incur provider charges. `submit` displays the recorded estimate and asks for confirmation. `sync` downloads immutable raw JSONL, validates row-level outputs, and writes normalized results.

## 8. Retry failures manually

```bash
kllm-batch retry RUN_ID
```

This prepares a child run for retryable failed rows without submitting it. Review the new cost and request files, then use `kllm-batch submit CHILD_RUN_ID`. After processing, combine attempts with `kllm-batch merge CHILD_RUN_ID`.

## 9. Run with Anthropic

```bash
kllm-batch pilot -c examples/grant_coding/project.yaml --provider anthropic
kllm-batch prepare -c examples/grant_coding/project.yaml --provider anthropic
kllm-batch submit ANTHROPIC_RUN_ID
kllm-batch status ANTHROPIC_RUN_ID
kllm-batch sync ANTHROPIC_RUN_ID --watch
kllm-batch audit ANTHROPIC_RUN_ID
```

## 10. Compare providers

```bash
kllm-batch compare OPENAI_RUN_ID ANTHROPIC_RUN_ID
```

The comparison reports agreement on categorical and string fields and exports disagreements for human review. It does not use a model to adjudicate differences.

## 11. Outputs

Each run contains `snapshot/`, `requests/`, `mappings/`, `raw/`, `results/`, and `reports/`. Raw provider responses and earlier attempts are never patched or deleted. See `expected/result_columns.md` for normalized columns.

## 12. Cost boundary

`validate` and `prepare` are local. `pilot`, `submit`, `status`, `sync`, and `cancel` use provider APIs. Estimates are conservative projections, not invoices; actual token usage is captured after retrieval.

