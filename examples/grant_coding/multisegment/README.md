# Multi-segment batch smoke tests

This local example uses 24 synthetic grants, one request per row, and at most
five requests per provider batch. A complete preparation therefore creates five
segments containing 5, 5, 5, 5, and 4 requests. Run each scenario from a fresh
prepared run. Only `submit`, `cancel`, and `sync` contact the provider.

Scripts resolve their own paths, so they can be called from any directory. They
never use `--yes`, print credentials, or infer a run to modify.

## Prepare

```bash
examples/grant_coding/multisegment/prepare.sh openai
# or
examples/grant_coding/multisegment/prepare.sh anthropic
```

Copy the complete `RUN_DIR` printed by `prepare`, then inspect its `REVIEW.md`,
`manifest.json`, `state.json`, and five request JSONL files.

## Scenario A: submit every segment

```bash
examples/grant_coding/multisegment/submit-all.sh RUN_DIR
examples/grant_coding/multisegment/sync.sh RUN_DIR
kllm-batch audit RUN_DIR
```

Run `submit-all.sh` again after the first submission to test idempotency. The
recorded remote batch IDs must remain unchanged and no completed segment should
be submitted again.

## Scenario B: staged ranges

```bash
examples/grant_coding/multisegment/submit-range.sh RUN_DIR 0 2
examples/grant_coding/multisegment/sync.sh RUN_DIR
examples/grant_coding/multisegment/submit-range.sh RUN_DIR 2 -1
examples/grant_coding/multisegment/sync.sh RUN_DIR
```

After the first sync, outputs cover only segments 0 and 1 and are provisional;
the final audit and run summary wait for the remaining segments. After the
second sync, outputs are rebuilt cumulatively and the final audit should be
complete.

## Scenario C: targeted cancellation

```bash
examples/grant_coding/multisegment/submit-range.sh RUN_DIR 0 3
examples/grant_coding/multisegment/cancel-range.sh RUN_DIR 1 2
kllm-batch status RUN_DIR
examples/grant_coding/multisegment/sync.sh RUN_DIR
```

Cancellation is asynchronous and race-sensitive. The first table may show a
local status of `cancelling`; continue polling or run `sync --watch` until the
provider reaches a terminal state. Anthropic reports request-level cancellation
counts, so the `Cancellation` column can show results such as `3/5 requests
cancelled` or `0/5 cancelled; completed first`. OpenAI reports the batch-level
outcome without a cancelled-request count, so a cancelled batch is shown as
`batch cancelled; request count unavailable`. If it finishes first, the column
shows `not cancelled; batch completed first`. Synchronization still downloads
and processes any partial OpenAI output. Existing artifacts retain the
cancellation record. Segments 3 and 4 remain prepared and can be submitted
later.
