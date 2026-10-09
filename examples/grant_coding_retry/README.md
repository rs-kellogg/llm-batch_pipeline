# Multi-segment retry smoke test

This example starts from a recorded failed parent run and exercises retry and
merge without asking a model to manufacture a failure. Choose OpenAI or
Anthropic. Each provider has a recorded parent with:

- 30 synthetic grants grouped into ten requests;
- five processed batch segments of two requests each;
- 27 valid results;
- three `missing_output` failures: `MRETRY-002`, `MRETRY-014`, and
  `MRETRY-026`.

The fixtures retain the original provider-native requests, raw responses,
normalized outputs, audits, state, and provider batch identifiers. The data are
synthetic and the identifiers are provenance, not credentials. See
[`fixtures/README.md`](fixtures/README.md) for their origin and layout.

Fixture setup, inspection, retry preparation, merge, and verification are
local and free. Only submitting the retry child contacts a provider and may
incur usage charges. Scripts resolve paths from their own location, never use
`--yes`, and require explicit run directories for operations after setup.

## 1. Install a failed parent fixture

Choose a provider:

```bash
examples/grant_coding_retry/setup-parent.sh openai
```

or:

```bash
examples/grant_coding_retry/setup-parent.sh anthropic
```

The script verifies the fixture's SHA-256 inventory, copies it into the
ignored `runs/` directory, resolves portable path placeholders for the current
checkout, and prints `PARENT_RUN_DIR`. It refuses to overwrite an existing run.
If a run with the recorded ID is already present, inspect and move that local
copy aside before installing the fixture.

The installed parent is a terminal historical artifact. Do not run `submit`,
`status`, or `sync` on it.

## 2. Inspect the parent failures

```bash
examples/grant_coding_retry/inspect-failures.sh PARENT_RUN_DIR
```

The expected outcome is 27 valid records and three `missing_output` failures.
The script prints failure categories and the incomplete audit. You can also
inspect these files directly:

- `outputs/failures.jsonl`
- `outputs/results.csv` and `outputs/results.parquet`
- `run_reports/audit.json`
- `input_snapshot/request_map.jsonl`
- `api_requests/segment_*.jsonl`
- `raw_responses/segment_*_output.jsonl`

## 3. Prepare the retry child

```bash
examples/grant_coding_retry/retry.sh PARENT_RUN_DIR
```

`retry.sh` is local and free. It prepares a child containing one request with
the three failed IDs and prints `CHILD_RUN_DIR`. Inspect its `REVIEW.md`,
`manifest.json`, `input_snapshot/request_map.jsonl`, and request JSONL before
submission.

The live project sends only `project_title` and `abstract`. The historical
`retry_case` and `retry_trigger_id` columns remain in the source CSV so its
recorded SHA-256 stays unchanged, but they are absent from new model requests.
The current prompts simply require one classification for every `record_id`.

## 4. Submit and synchronize the retry

```bash
examples/grant_coding_retry/submit.sh CHILD_RUN_DIR
```

```bash
examples/grant_coding_retry/sync.sh CHILD_RUN_DIR
```

`submit.sh` is the paid operation. `sync.sh` waits for the retry batch,
downloads its response, normalizes it, and runs the child audit. The expected
result is three valid records and a complete child audit. A real provider or
model can still produce an organic failure; if that happens, inspect the new
failure file and retry the newest child again.

## 5. Merge and verify the chain

```bash
examples/grant_coding_retry/merge.sh CHILD_RUN_DIR
```

```bash
examples/grant_coding_retry/verify-merged.sh CHILD_RUN_DIR
```

Both commands are local. `merge` walks the parent chain and writes
`outputs/merged.csv` and `outputs/merged.parquet` under the child run.
`verify-merged.sh` requires both files to contain exactly 30 unique IDs,
`MRETRY-001` through `MRETRY-030`.

If a child has another retryable failure, create and run the next descendant:

```bash
examples/grant_coding_retry/retry.sh NEWEST_CHILD_RUN_DIR
```

After it finishes, merge from the newest descendant so the full attempt chain
is included.

## Why the parent is recorded

The first version of this example instructed the model to omit marked records
when a trigger record appeared in the same request. OpenAI omitted the three
parent records and returned all three when retried without their triggers.
Anthropic omitted the same parent records, but its first retry returned
`{"results":[]}` and a later retry recovered only `MRETRY-014`.

That behavior correctly became new `missing_output` failures, demonstrating
that retry preparation cannot guarantee model compliance. It also made the
manual smoke test provider-dependent. Recorded failed parents preserve the
five-segment failure scenario, while the live retry now uses an ordinary
classification prompt for both providers.
