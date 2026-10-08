# Multi-segment retry smoke test

This provider-selectable example exercises a complete batch failure, retry, and
merge workflow. Its 30 synthetic grants become ten requests and five segments:

- `rows_per_request: 3` creates ten requests.
- `max_requests_per_batch: 2` creates five segments of two requests each.
- `MRETRY-002`, `MRETRY-014`, and `MRETRY-026` carry controlled retry markers
  in segments 0, 2, and 4.

The prompt deliberately tells the model to omit each marked record when its
named trigger is in the same request. If the provider follows that instruction,
the parent produces 27 valid rows and three `missing_output` failures. The retry
child contains only the three omitted records. Their trigger records are absent
from the child request, so the same prompt tells the model to return them.

These omissions are controlled test behavior. They do not simulate a provider
API error, and provider behavior can vary. A model may ignore the omission rule
or introduce an additional real failure.

All scripts resolve paths from their own location. Provider-facing commands
retain the CLI confirmation prompt and require an explicit run directory. They
do not print credentials or modify prepared request artifacts.

## 1. Prepare the parent

From any directory, choose one provider:

```bash
examples/grant_coding_retry/prepare.sh openai
```

or:

```bash
examples/grant_coding_retry/prepare.sh anthropic
```

Preparation is local and free. Copy the complete run directory printed by the
command. Before submission, inspect `REVIEW.md`, `manifest.json`, and all five
files under `api_requests/`.

## 2. Submit and synchronize the parent

```bash
examples/grant_coding_retry/submit.sh PARENT_RUN_DIR
```

```bash
examples/grant_coding_retry/sync.sh PARENT_RUN_DIR
```

`submit.sh` is the first paid operation. `sync.sh` waits for the five remote
batches, downloads available responses, normalizes them, and runs the audit.

## 3. Inspect the controlled failures

```bash
examples/grant_coding_retry/inspect-failures.sh PARENT_RUN_DIR
```

The expected outcome is:

- 27 valid records;
- three failures, all categorized as `missing_output`;
- failed IDs `MRETRY-002`, `MRETRY-014`, and `MRETRY-026`;
- an incomplete parent audit.

The script prints the failure category counts and audit, reports additional
failures separately, and exits unsuccessfully when the expected controlled IDs
were not omitted as intended. Review `outputs/failures.jsonl` and
`run_reports/audit.json` directly before creating the retry.

## 4. Prepare and run the retry child

```bash
examples/grant_coding_retry/retry.sh PARENT_RUN_DIR
```

`retry.sh` is local and free. It prepares a child containing only retryable
failed records and prints its directory. With the expected parent result, the
child has one request containing the three controlled IDs. Inspect its
`REVIEW.md`, `manifest.json`, `input_snapshot/request_map.jsonl`, and request
JSONL before submission.

```bash
examples/grant_coding_retry/submit.sh CHILD_RUN_DIR
```

```bash
examples/grant_coding_retry/sync.sh CHILD_RUN_DIR
```

The retry submit is another paid operation. The expected child audit is
complete with three valid records.

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

## 6. Repeat and handle provider variation

Prepare a fresh parent with the other provider and repeat the workflow. Do not
reuse one provider's prepared payloads for another provider.

If a child has another retryable failure, use that child as the parent of the
next retry:

```bash
examples/grant_coding_retry/retry.sh NEWEST_CHILD_RUN_DIR
```

After submitting and synchronizing the new child, merge from the newest
descendant so the command can walk the complete attempt chain:

```bash
examples/grant_coding_retry/merge.sh NEWEST_DESCENDANT_RUN_DIR
```

If the provider ignores one or more controlled omissions, fewer than three
records will be eligible for retry. Treat the actual failure artifacts and
audit as authoritative; do not manufacture or edit failures to force the
demonstration.
