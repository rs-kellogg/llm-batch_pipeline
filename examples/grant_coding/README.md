# Grant coding example

This example classifies ten entirely synthetic grants. It is safe to inspect and modify; it contains no real research data.

Basic shell wrappers for this walkthrough are available in
[`walkthrough/`](walkthrough/README.md). They preserve the same review and cost
confirmation steps while resolving project paths from the script location.

Two larger manual smoke tests are also available. The
[`multisegment/`](multisegment/README.md) example demonstrates staged range
submission and cancellation. The
[`multisegment-retry/`](multisegment-retry/README.md) example creates three
controlled missing outputs across five segments, retries only those records,
and merges the recovered rows with the successful parent results.

## 1. Install

Create and activate your own dedicated mamba environment. Then install the
package from the repository root:

```bash
mamba create -n kllm-batch python=3.12 -y
mamba activate kllm-batch
python -m pip install -e .
```

The standard installation includes the local project builder. To open this
example in a browser, run:

```bash
kllm-batch gui examples/grant_coding
```

The GUI can reopen this project, select input data and a codebook through file
picker windows, edit mappings, schema, and prompts, preview a rendered
request containing the first `rows_per_request` records, and run local
validation. Its sidebar links to every section, and the guided schema editor
shows the required JSON-array syntax for enums. Long rendered prompts wrap in
the persistent side-by-side preview. The schema editor displays the generated
`schema.json`, while the settings section displays the exact ordered
`project.yaml` that will be saved. Selected files are copied into the project
only when it is saved. The GUI never submits API requests and opens with
Streamlit's light base theme and larger, sans-serif interface type. Because
this checked-in example is already initialized, it can be opened directly. For
your own work, first run `kllm-batch init my-project`, then
`kllm-batch gui my-project`. The builder shows the configured
`data/input-data.csv` and identifies `context/` as the default place for a
codebook. Guided/Advanced schema mode changes carry compatible fields in both
directions.

### Starting your own project with `init`

You do not need `init` to run this checked-in grant example. Use it when you
are ready to create a separate project of your own:

```bash
kllm-batch init my-project
```

The command is local, requires no API key, and creates this starter structure:

```text
my-project/
├── project.yaml
├── schema.json
├── data/
│   └── input-data.csv
├── context/
│   └── codebook.csv
├── prompts/
│   ├── system.txt
│   └── user.txt
└── runs/
```

It will not overwrite a directory that already contains files. The starter
contains the same ten synthetic grants, illustrative reference labels,
four-topic codebook, prompts, and primary/secondary-label schema as this
checked-in example. It omits only this example's teaching fixtures and
explanatory files. Replace the sample data
and codebook for your task, configure the column mappings and providers in
`project.yaml`, revise both prompts and `schema.json`, and then validate the
new project:

```bash
kllm-batch validate -c my-project/project.yaml
```

The remainder of this README uses `examples/grant_coding/project.yaml`, but the
same `validate` → `prepare` → inspect → `submit` workflow applies to the project
created by `init`.

## 2. Configure credentials

Set only the provider you plan to use:

```bash
export OPENAI_API_KEY="your-key"
export ANTHROPIC_API_KEY="your-key"
```

Do not put credentials in YAML, prompt files, shell history shared with others, or Git.

## 3. Inspect the project

- `data/input-data.csv` has one grant per row, a stable `grant_id`, and an
  illustrative `reference_primary_label` for local evaluation.
- `project.yaml` maps `project_title` and `abstract` to model-facing fields while preserving three local metadata columns.
- `context/codebook.csv` defines the four labels.
- `prompts/system.txt` contains the coding rules.
- `prompts/user.txt` inserts `${codebook_json}` and `${records_json}`.
- `schema.json` defines one row's required output fields; the package adds `record_id` and the `results` wrapper.

### How `input` becomes `${records_json}`

The keys on the left side of `fields_sent` are user-chosen names the model
sees; there can be any number of them. The values on the right are columns in
`data/input-data.csv`:

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

The `evaluation.gold_columns.primary_label` mapping points to
`reference_primary_label`. This column is not in `fields_sent` or
`columns_preserved`, so it stays out of provider prompts and normalized
results. Its labels are teaching examples, not externally validated research
ground truth. When substituting your own data, supply reviewed reference
labels or remove the mapping.

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

- `REVIEW.md`: the starting point, with the selection, provider, model,
  execution mode, request count, estimated cost, prompt-inspection command, and
  next command.
- `api_requests/segment_*.jsonl`: the exact provider-native payloads that will run,
  including every fully rendered prompt and the records substituted into
  `${records_json}`. For OpenAI, inspect `body.instructions` and `body.input`;
  for Anthropic, inspect `params.system` and `params.messages[].content`.
- `project_snapshot/schema.json` and `manifest.json`: the enforced response contract,
  hashes, deterministic seed, selected IDs, execution mode, and cost estimate.

For Anthropic, the exact request payload may represent nullable type arrays as
equivalent `anyOf` branches and move unsupported numeric bounds into field
descriptions. The unchanged `project_snapshot/schema.json` is still used for
strict local validation, including the confidence range.

`REVIEW.md` includes an appropriate `jq` command for printing all system and
user prompts from the segment files. The package also retains
`input_snapshot/canonical_input.parquet` for preserved-column
joins, retries, and audits, plus `input_snapshot/request_map.jsonl` for matching API
requests back to record IDs. With gold evaluation enabled,
`input_snapshot/gold_labels.parquet` holds only the selected reference labels;
its relative path appears in `manifest.json` as `gold_labels_file`.
Researchers normally do not need to inspect these snapshots. The redundant
canonical CSV, model-record JSONL, and separate
rendered-prompt JSONL are not created.

After reviewing those artifacts, run those exact saved requests:

```bash
kllm-batch submit RUN_ID
```

Only `submit` contacts the provider and may incur cost. A synchronous pilot is
processed immediately into the normal `outputs/` and `run_reports/` directories;
this processing includes the completeness audit, so it does not need `status`,
`sync`, or a separate `audit` command. Submission verifies the request-artifact
hashes and is idempotent. If review reveals a needed change, edit the source
YAML, prompt, context, or data and prepare a new run; do not patch generated
payloads in place.

Synchronous progress is checkpointed after every completed API request. On
resume, saved request IDs are skipped. There is a small ambiguous window where
the provider may finish but the local response has not yet been saved; that
request may run again and incur duplicate cost. Recorded API errors are not
automatically rerun—inspect `outputs/failures.jsonl` and use `retry`. If the
last checkpoint line is partial or invalid, resume stops with a warning for
manual review.
The CLI prints a running completion count after every synchronous request. If
you resume, the confirmation prompt shows only the remaining request count and
its proportional estimated maximum cost.

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

Preparation is local. It validates again and prints a run directory such as `examples/grant_coding/runs/20260909T120000Z_openai_ab12cd34`. In the commands below, replace `RUN_ID` with that complete path. Inspect `REVIEW.md`, `manifest.json`, and `api_requests/` before submitting.

## 7. Submit, monitor, download, and audit

For a normal batch run, the required workflow is:

```bash
kllm-batch submit RUN_ID
kllm-batch sync RUN_ID --watch
```

`submit` displays the recorded estimate and asks for confirmation. `sync`
with `--watch` polls the provider until the batch is finished, downloads
immutable raw JSONL, validates row-level outputs, writes normalized results,
and runs the completeness audit automatically. It prints the current run and
provider status after every poll, along with the time until the next check.

These diagnostic commands are optional for a batch run:

```bash
# Show a one-time provider-status snapshot before or during sync
kllm-batch status RUN_ID

# Recompute and print the local completeness audit after sync
kllm-batch audit RUN_ID
```

`status` is unnecessary when using `sync --watch`, because `sync` already
polls status. The explicit `audit` is also unnecessary for normal processing,
because `sync` runs it automatically. Use these commands when you want a
one-time progress check or need to display/recompute the audit later. `submit`,
`status`, and `sync` contact the provider; `audit` is local and free.

## 8. Retry failures manually

```bash
kllm-batch retry RUN_ID
```

This prepares a child run for retryable failed rows without submitting it. Review the new cost and request files, then use `kllm-batch submit CHILD_RUN_ID`. After processing, combine attempts with `kllm-batch merge CHILD_RUN_ID`.

### Intentionally rerun selected records

Completed runs and their raw responses are immutable. Do not delete a response
or overwrite an earlier result to force another API call. To code selected
records again, create a UTF-8 file such as `rerun_ids.txt` containing their
source `grant_id` values, one per line with no header:

```text
GRANT-002
GRANT-007
```

Then prepare a separate run:

```bash
kllm-batch prepare \
  -c examples/grant_coding/project.yaml \
  --provider openai \
  --ids-file rerun_ids.txt

kllm-batch submit NEW_RUN_ID
```

Blank lines are ignored. Duplicate IDs and IDs not found in the configured
input are rejected. The original and new runs remain separate so researchers
can compare them and explicitly choose which result to use downstream.

If a raw response was deleted accidentally, running `submit` again reports an
integrity warning and makes no API calls. Use
`input_snapshot/request_map.jsonl` to map the reported missing request ID to
the source record IDs, then restore the original response from backup or use
those record IDs in `rerun_ids.txt`.

## 9. Run with Anthropic

```bash
kllm-batch prepare -c examples/grant_coding/project.yaml --provider anthropic --sample-size 4 --seed 42
kllm-batch submit ANTHROPIC_PILOT_RUN_ID
kllm-batch prepare -c examples/grant_coding/project.yaml --provider anthropic
kllm-batch submit ANTHROPIC_RUN_ID
kllm-batch sync ANTHROPIC_RUN_ID --watch
```

As with OpenAI batch runs, `status ANTHROPIC_RUN_ID` and
`audit ANTHROPIC_RUN_ID` are optional diagnostics; `sync --watch` already
polls status, processes the responses, and runs the audit.

## 10. Compare providers

```bash
kllm-batch compare OPENAI_RUN_ID ANTHROPIC_RUN_ID
```

The comparison reports agreement on categorical and string fields and exports disagreements for human review. It does not use a model to adjudicate differences.

## 11. Outputs

Immediately after `prepare`, a run contains `REVIEW.md`, `manifest.json`,
`state.json`, `api_requests/`, `input_snapshot/`, and `project_snapshot/`. The
`raw_responses/` directory is created when provider responses are retrieved;
`outputs/` and `run_reports/` are created when processing begins. Raw responses
and earlier attempts are never patched or deleted. Successful processing writes
both `outputs/results.csv` for quick inspection and `outputs/results.parquet`
for type-stable analysis. An `outputs/failures.jsonl` file is created only when
failures occur. Essential
row provenance is stored directly in these result and failure rows; shared
prompt, schema, configuration, and environment provenance remains in
`manifest.json`. The `run_reports/` directory contains only JSON reports:
`audit.json`, `run_summary.json`, and `usage.json`. See
`expected/result_columns.md` for normalized columns. With the illustrative
reference labels configured, `run_summary.json` includes
`evaluation_metrics.primary_label` with the number of matched valid results
and their exact-match accuracy. Check `audit.json` for missing or invalid
results before interpreting that metric.

## 12. Cost boundary

`validate`, `prepare`, `audit`, `retry`, `merge`, and `compare` are local.
`submit` uses a provider API and may incur model charges. For batch runs,
`status`, `sync`, and `cancel` also use provider APIs. Estimates are
conservative projections, not invoices; actual token usage is captured after
processing.
