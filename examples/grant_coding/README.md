# Grant coding example

This example classifies ten entirely synthetic grants. It is safe to inspect and modify; it contains no real research data.

## 1. Install

Create and activate your own dedicated mamba environment. Then install the
package from the repository root:

```bash
mamba create -n kllm-batch python=3.12 -y
mamba activate kllm-batch
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

### How `input` becomes `${records_json}`

The keys on the left side of `fields_sent` are user-chosen names the model
sees; there can be any number of them. The values on the right are columns in
`data/grants.csv`:

```yaml
id_column: grant_id
fields_sent:
  project_title: project_title
  abstract: abstract
```

Because `required_fields` also uses model-facing names, `- abstract` means an
empty source `abstract` is a blocking validation error. `project_title` is
still sent; it is simply allowed to be empty in this example.

For example, the first CSV row is converted to this model-facing record:

```json
{
  "record_id": "GRANT-001",
  "project_title": "Team learning after product failures",
  "abstract": "Studies how teams update routines and coordinate work after unsuccessful product launches."
}
```

The package groups these objects according to `task.rows_per_request`—three in
this example—serializes the group as deterministic JSON, and substitutes it for
`${records_json}` in `prompts/user.txt`. The `record_id` comes from `grant_id`.
The preserved columns `year`, `investigator`, and `source_file` are joined back
into results locally and are not placed in `${records_json}`. Similarly, the
CSV configured as prompt context under the name `codebook` becomes
`${codebook_json}`.

## 4. Validate locally

```bash
kllm-batch validate -c examples/grant_coding/project.yaml
```

This is local and free. It checks duplicate IDs, duplicate model-facing records, missing values, prompts, schema, and estimated costs. The two `invalid_duplicate_*.csv` files are teaching and test fixtures; point a copied YAML file at either one to see validation fail before request generation.

## 5. Prepare, inspect, and run a pilot

Prepare a deterministic sample and its complete prompts locally:

```bash
kllm-batch prepare \
  -c examples/grant_coding/project.yaml \
  --provider openai \
  --sample-size 4 \
  --seed 42
```

This command makes no API call. It prints a `RUN_ID` under
`examples/grant_coding/runs/`. Because records were selected, the manifest
records `purpose: pilot` and defaults to `execution: sync`. Inspect these files
before approving model usage:

- `requests/model_records.jsonl`: exactly the records substituted into
  `${records_json}`.
- `requests/canonical_input.csv` and `.parquet`: the selected source rows,
  including local preserved fields.
- `requests/rendered_prompts.jsonl`: readable system and fully rendered user
  prompts for every request.
- `requests/segment_*.jsonl`: the exact provider-native payloads that will run.
- `snapshot/schema.json` and `manifest.json`: the enforced response contract,
  hashes, deterministic seed, selected IDs, execution mode, and cost estimate.

After reviewing those artifacts, run those exact saved requests:

```bash
kllm-batch submit RUN_ID
```

Only `submit` contacts the provider and may incur cost. A synchronous pilot is
processed immediately into the normal `results/` and `reports/` directories;
it does not need `status` or `sync`. Submission verifies the request-artifact
hashes and is idempotent. If review reveals a needed change, edit the source
YAML, prompt, context, or data and prepare a new run; do not patch generated
payloads in place.

To select records deliberately instead of randomly, create a UTF-8 text file
with one `grant_id` per line and use `--ids-file pilot_ids.txt`. Selection
files with duplicate IDs, or IDs absent from the validated input, are rejected.
For either selection method, add `--execution batch` if a batch pilot is
preferred. Conversely, a full run can use `--execution sync` explicitly.

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
kllm-batch prepare -c examples/grant_coding/project.yaml --provider anthropic --sample-size 4 --seed 42
kllm-batch submit ANTHROPIC_PILOT_RUN_ID
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

`validate`, `prepare`, `audit`, `retry`, `merge`, and `compare` are local.
`submit` uses a provider API and may incur model charges. For batch runs,
`status`, `sync`, and `cancel` also use provider APIs. Estimates are
conservative projections, not invoices; actual token usage is captured after
processing.
